#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
국내 이벤트드리븐 차익 데스크 — 데이터 파이프라인 (GitHub Actions 전용)

DART OpenAPI 에서 최근 공시를 훑어 아래 이벤트를 뽑고, 관련 종목 시세를 붙여
output/events_kr.json 으로 남긴다.

  TENDER      공개매수 (현금)           공개매수신고서 원문 파싱 → 가격·수량·기간·결제일
  MERGER      회사합병 결정             합병비율·매수청구가·일정 (구조화 API)
  EXCHANGE    주식교환·이전 결정         교환비율·매수청구가·일정 (구조화 API)
  SPLITMERGER 회사분할합병 결정          분할합병비율·매수청구가·일정 (구조화 API)
  SPAC        스팩                       상장 스팩 전 종목 + 합병 발표 여부

필요한 비밀값: DART_API_KEY (opendart.fss.or.kr 에서 무료 발급, 40자리)

출력 스키마 (요약)
  { market, date, generated_at, diag[],
    events: [ {id,type,status,title,rcept_no,rcept_dt,bddd,url,
               legs:[{role,name,code,last,prev,unaffected,adv20,shares,mcap}],
               ratio:{per_target,text}, tender:{...}, appraisal:[...],
               dates:[{label,date}], text:{...}, flags:{...}} ],
    px: { code: {d:[yymmdd], c:[close]} } }

모든 수치는 공시 자동 파싱 결과라 원문 확인이 필요하다. 대시보드에서 항목별로
값을 덮어쓸 수 있다. 투자 권유가 아니다.
"""
import io, json, os, re, sys, time, zipfile, traceback
from datetime import datetime, timedelta, timezone, date

import requests

KST = timezone(timedelta(hours=9))
DART = "https://opendart.fss.or.kr/api/"
KEY = os.getenv("DART_API_KEY", "").strip()
LOOKBACK = int(os.getenv("LOOKBACK_DAYS", "150"))       # 공시 조회 기간(달력일)
KEEP_DONE = int(os.getenv("KEEP_DONE_DAYS", "21"))      # 끝난 딜을 며칠 더 보여줄지
PX_DAYS = int(os.getenv("PX_DAYS", "130"))              # 차트용 종가 보관 일수
SPAC_RATE = float(os.getenv("SPAC_TRUST_RATE", "0.025"))  # 스팩 예치금 연이율 추정
OUT = os.getenv("OUT_DIR", "output")

DIAG = []
TODAY = datetime.now(KST).date()


def log(msg):
    print(msg)
    DIAG.append(msg)


# ══════════════════════════════════════════════════════════════
# 외부 호출 (테스트에서 monkeypatch 로 바꿔 끼운다)
# ══════════════════════════════════════════════════════════════
_S = requests.Session()
_CALLS = {"n": 0}


def dart_json(endpoint, params):
    """DART JSON API 호출. status 000(정상)·013(데이터 없음) 이외는 예외."""
    p = dict(params)
    p["crtfc_key"] = KEY
    for attempt in range(4):
        try:
            _CALLS["n"] += 1
            r = _S.get(DART + endpoint, params=p, timeout=30)
            js = r.json()
            st = js.get("status")
            if st in ("000", "013"):
                return js
            if st == "020":          # 요청 제한 초과
                raise RuntimeError("DART 일일 요청 한도 초과(020)")
            raise RuntimeError(f"DART {endpoint} status={st} {js.get('message')}")
        except RuntimeError:
            raise
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    return {}


def dart_doc_text(rcept_no):
    """공시 원문(zip 안의 XML들)을 태그를 걷어낸 평문으로 돌려준다."""
    r = _S.get(DART + "document.xml", params={"crtfc_key": KEY, "rcept_no": rcept_no}, timeout=60)
    b = r.content
    if not b[:2] == b"PK":
        m = re.search(rb"<message>(.*?)</message>", b, re.S)
        st = re.search(rb"<status>(.*?)</status>", b, re.S)
        msg = (st.group(1).decode() + " " + m.group(1).decode("utf-8", "replace")) if m and st else repr(b[:80])
        raise RuntimeError(f"원문 없음 {msg}")
    parts = []
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        for n in z.namelist():
            raw = z.read(n)
            for enc in ("utf-8", "euc-kr", "cp949"):
                try:
                    parts.append(raw.decode(enc))
                    break
                except Exception:
                    continue
    return html_to_text("\n".join(parts))


def load_listing():
    """국내 상장 종목 {code: {name, market, shares}} — FinanceDataReader."""
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX")
    cols = {c.lower(): c for c in df.columns}
    def col(*names):
        for n in names:
            if n.lower() in cols:
                return df[cols[n.lower()]]
        return None
    code, name = col("Code", "Symbol"), col("Name")
    mk, st = col("Market", "MarketId"), col("Stocks", "ListedShares")
    out = {}
    for i in range(len(df)):
        c = str(code.iloc[i]).zfill(6)
        out[c] = {
            "name": str(name.iloc[i]),
            "market": str(mk.iloc[i]) if mk is not None else "",
            "shares": _f(st.iloc[i]) if st is not None else None,
        }
    # 상장일 (스팩 만기 계산용) — 실패해도 무시
    try:
        d2 = fdr.StockListing("KRX-DESC")
        c2 = {c.lower(): c for c in d2.columns}
        if "code" in c2 and "listingdate" in c2:
            for cc, ld in zip(d2[c2["code"]].astype(str).str.zfill(6), d2[c2["listingdate"]]):
                if cc in out and str(ld) not in ("NaT", "nan", "None"):
                    out[cc]["listed"] = str(ld)[:10]
    except Exception as e:
        log(f"KRX-DESC(상장일) 생략: {type(e).__name__}")
    return out


def load_prices(code, start):
    """일봉 [(date, close, volume)] — FinanceDataReader."""
    import FinanceDataReader as fdr
    d = fdr.DataReader(code, start)
    if d is None or not len(d):
        return []
    out = []
    for idx, row in d.iterrows():
        c, v = _f(row.get("Close")), _f(row.get("Volume"))
        if c:
            out.append((idx.date() if hasattr(idx, "date") else idx, c, v or 0.0))
    return out


# ══════════════════════════════════════════════════════════════
# 파싱 도우미
# ══════════════════════════════════════════════════════════════
def _f(v):
    try:
        x = float(v)
        return None if x != x else x
    except Exception:
        return None


def html_to_text(s):
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</title>|</td>|</th>|</te>|</tu>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&#8228;", "·").replace("\xa0", " "))
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s


NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def num(s, min_val=None):
    """문자열 속 첫 숫자(쉼표 허용). min_val 보다 작은 숫자는 건너뛴다."""
    if s is None:
        return None
    for m in NUM_RE.finditer(str(s)):
        try:
            x = float(m.group().replace(",", ""))
        except Exception:
            continue
        if min_val is None or x >= min_val:
            return x
    return None


DATE_RE = re.compile(r"(20\d\d)\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})")


def pdate(s):
    """'2026년 10월 5일', '2026.10.05', '2026-10-05', '20261005' → '2026-10-05'."""
    if s is None:
        return None
    s = str(s)
    m = DATE_RE.search(s)
    if not m:
        m2 = re.search(r"(?<!\d)(20\d\d)(\d\d)(\d\d)(?!\d)", s)
        if not m2:
            return None
        y, mo, d = m2.groups()
    else:
        y, mo, d = m.groups()
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


def all_dates(s):
    out = []
    for m in DATE_RE.finditer(str(s or "")):
        try:
            out.append(date(*map(int, m.groups())).isoformat())
        except ValueError:
            pass
    return out


def clean_name(s):
    """공시 속 회사명: 첫 줄만, 영문 병기 괄호·'이하 ~' 주석 제거."""
    s = str(s or "").strip().splitlines()[0] if str(s or "").strip() else ""
    s = re.sub(r"\((?=[^)]*[A-Za-z])[^)]*\)?", "", s)          # (Dong-A Pharm Co., Ltd.)
    s = re.sub(r"\(이하[^)]*\)?", "", s)
    return re.sub(r"\s+", " ", s).strip(" ,")


def norm_name(s):
    s = clean_name(s)
    s = re.sub(r"주식회사|\(주\)|㈜|\(유\)|유한회사|Co\.,?\s*Ltd\.?|Inc\.?|Corp\.?", "", s, flags=re.I)
    return re.sub(r"[\s()\[\]·.,\-]", "", s).upper()


def name_positions(text, n1, n2):
    """비율 문구에서 두 회사명이 나오는 위치 (i1, i2). 한쪽 이름이 다른 쪽에 포함돼도(휴맥스/휴맥스홀딩스) 구분한다."""
    t, a, b = norm_name(text), norm_name(n1), norm_name(n2)
    if not a or not b or a == b:
        return None
    if a in b:
        i2 = t.find(b); i1 = t.replace(b, "#" * len(b)).find(a) if i2 >= 0 else -1
    elif b in a:
        i1 = t.find(a); i2 = t.replace(a, "#" * len(a)).find(b) if i1 >= 0 else -1
    else:
        i1, i2 = t.find(a), t.find(b)
    return (i1, i2) if i1 >= 0 and i2 >= 0 and i1 != i2 else None


def parse_ratio(text, left_name=None, right_name=None):
    """'1 : 0.4521317' 같은 비율 문구 → (a, b). 이름이 문구 안에 나오면 그 순서를 함께 돌려준다."""
    t = str(text or "")
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*[:：]\s*(\d[\d,]*(?:\.\d+)?)", t)
    if not m:
        return None
    a, b = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
    if a <= 0 or b <= 0:
        return None
    order = None
    if left_name and right_name:
        pos = name_positions(t, left_name, right_name)
        if pos:
            order = "as_given" if pos[0] < pos[1] else "swapped"
    return a, b, order


def absorber_from_method(text):
    """'A가 B를 흡수합병' → (A, B). 못 찾으면 None."""
    t = re.sub(r"\s+", " ", str(text or ""))
    m = re.search(r"([^\s,.;:()]{2,}?(?:\([^)]*\))?[^\s,.;:()]*)\s*(?:이|가|은|는)\s+"
                  r"([^\s,.;:()]{2,}?(?:\([^)]*\))?[^\s,.;:()]*)\s*(?:을|를)\s*(?:흡수\s*)?합병", t)
    if not m:
        return None
    return m.group(1), m.group(2)


# ══════════════════════════════════════════════════════════════
# 공시 목록
# ══════════════════════════════════════════════════════════════
def list_filings(ty, start, end, detail=None, max_pages=80):
    """start~end 공시 목록 전체(최대 max_pages×100건). corp_code 없이 검색하면
    DART 가 3개월 이내만 허용하므로 85일 단위로 잘라 부른다."""
    rows, s = [], start
    while s <= end:
        e = min(end, s + timedelta(days=84))
        page = 1
        while page <= max_pages:
            p = {"bgn_de": s.strftime("%Y%m%d"), "end_de": e.strftime("%Y%m%d"),
                 "pblntf_ty": ty, "page_no": page, "page_count": 100, "sort": "date", "sort_mth": "desc"}
            if detail:
                p["pblntf_detail_ty"] = detail
            js = dart_json("list.json", p)
            got = js.get("list") or []
            rows.extend(got)
            if page >= int(js.get("total_page") or 1) or not got:
                break
            page += 1
        s = e + timedelta(days=1)
    seen, uniq = set(), []
    for r in rows:
        if r.get("rcept_no") not in seen:
            seen.add(r.get("rcept_no"))
            uniq.append(r)
    return uniq


def classify(report_nm):
    r = re.sub(r"\s+", "", report_nm or "")
    if "공개매수" in r:
        if "결과보고" in r:
            return "TENDER_RESULT"
        if "철회" in r:
            return "TENDER_WITHDRAW"
        if "공개매수신고서" in r:
            return "TENDER"
        return None
    if "분할합병결정" in r:
        return "SPLITMERGER"
    if "회사합병결정" in r:
        return "MERGER"
    if ("주식교환" in r or "주식이전" in r) and "결정" in r:
        return "EXCHANGE"
    return None


# ══════════════════════════════════════════════════════════════
# 시세
# ══════════════════════════════════════════════════════════════
PX = {}          # code → [(date, close, vol)]


def prices(code):
    if not code:
        return []
    if code not in PX:
        try:
            PX[code] = load_prices(code, (TODAY - timedelta(days=max(PX_DAYS, LOOKBACK) + 100)).isoformat())
        except Exception as e:
            log(f"시세 실패 {code}: {type(e).__name__}:{str(e)[:60]}")
            PX[code] = []
    return PX[code]


def close_before(code, d_iso):
    """d_iso 이전(당일 제외) 마지막 종가 = 발표 전 가격(unaffected)."""
    if not d_iso:
        return None
    d = date.fromisoformat(d_iso)
    last = None
    for dt, c, _ in prices(code):
        if dt < d:
            last = c
        else:
            break
    return last


def vwap_window(code, end_incl, start_incl):
    num_, den = 0.0, 0.0
    for dt, c, v in prices(code):
        if start_incl <= dt <= end_incl and v:
            num_ += c * v
            den += v
    return num_ / den if den else None


def _months_back(d, m):
    y, mo = d.year, d.month - m
    while mo <= 0:
        mo += 12
        y -= 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30,
                      31, 31, 30, 31, 30, 31][mo - 1])
    return date(y, mo, day)


def statutory_appraisal(code, bddd_iso):
    """자본시장법 시행령 176조의7 상장법인 매수청구가 추정.
    이사회결의일 전일 기준 과거 2개월·1개월·1주일 거래량가중평균의 산술평균.
    (일별 종가×거래량 가중 근사 — 공식 산식은 일별 거래대금/거래량)"""
    if not code or not bddd_iso:
        return None
    base = date.fromisoformat(bddd_iso) - timedelta(days=1)
    w2 = vwap_window(code, base, _months_back(base, 2) + timedelta(days=1))
    w1 = vwap_window(code, base, _months_back(base, 1) + timedelta(days=1))
    ww = vwap_window(code, base, base - timedelta(days=6))
    xs = [x for x in (w2, w1, ww) if x]
    if len(xs) < 3:
        return None
    return {"est": round(sum(xs) / 3), "w2m": round(w2), "w1m": round(w1), "w1w": round(ww)}


def leg(role, name, code, listing, bddd=None):
    p = prices(code) if code else []
    last = p[-1][1] if p else None
    prev = p[-2][1] if len(p) > 1 else None
    adv = None
    if p:
        tail = p[-20:]
        adv = round(sum(c * v for _, c, v in tail) / len(tail) / 1e8, 2)   # 억원
    sh = (listing.get(code) or {}).get("shares") if code else None
    if code and code in listing:
        name = listing[code]["name"]
    else:
        name = clean_name(name)
    return {
        "role": role, "name": name, "code": code,
        "listed": bool(code and code in listing),
        "last": last, "prev": prev,
        "last_date": p[-1][0].isoformat() if p else None,
        "unaffected": close_before(code, bddd) if code else None,
        "adv20": adv, "shares": sh,
        "mcap": round(last * sh / 1e8) if last and sh else None,
    }


# ══════════════════════════════════════════════════════════════
# 이벤트 빌더
# ══════════════════════════════════════════════════════════════
STRUCT = {
    "MERGER": "cmpMgDecsn.json",
    "EXCHANGE": "stkExtrDecsn.json",
    "SPLITMERGER": "cmpDvmgDecsn.json",
}

DATE_LABELS = [
    # (키 끝부분, 라벨, 정렬순서)
    ("ctrd", "계약일", 1), ("shddstd", "주주확정기준일", 2),
    ("shclspd_bgd", "명부폐쇄 시작", 3), ("shclspd_edd", "명부폐쇄 종료", 4),
    ("extrop_rcpd_bgd", "반대의사통지 시작", 5), ("extrop_rcpd_edd", "반대의사통지 마감", 6),
    ("gmtsck_prd", "주주총회", 7),
    ("aprskh_expd_bgd", "매수청구 시작", 8), ("aprskh_expd_edd", "매수청구 마감", 9),
    ("trspprpd_bgd", "거래정지 시작", 10), ("trspprpd_edd", "거래정지 종료", 11),
    ("mgdt", "합병기일", 12), ("extrdt", "교환·이전일", 12), ("dvmgdt", "분할합병기일", 12),
    ("mgrgsprd", "등기예정일", 13), ("dvmgrgsprd", "등기예정일", 13),
    ("nstkdlprd", "신주교부 예정일", 14), ("nstklstprd", "신주 상장예정일", 15),
]


def collect_dates(rec):
    out, seen = [], set()
    for k, v in rec.items():
        for suf, label, order in DATE_LABELS:
            if k.endswith(suf):
                d = pdate(v)
                if d and (label, d) not in seen:
                    seen.add((label, d))
                    out.append({"label": label, "date": d, "o": order})
                break
    out.sort(key=lambda x: (x["date"], x["o"]))
    return [{"label": x["label"], "date": x["date"]} for x in out]


def find_code(name, listing, name_idx):
    if not name:
        return None
    n = norm_name(name)
    if n in name_idx:
        return name_idx[n]
    # 부분 일치(길이 3자 이상) — 가장 비슷한 길이를 고른다
    cands = [(abs(len(k) - len(n)), c) for k, c in name_idx.items()
             if len(n) >= 4 and len(k) >= 4 and (n in k or k in n)
             and min(len(k), len(n)) / max(len(k), len(n)) >= 0.6]
    return min(cands)[1] if cands else None


def build_struct_event(kind, rec, lrow, listing, name_idx):
    """합병·교환·분할합병 구조화 레코드 1건 → 이벤트."""
    me_name, me_code = lrow.get("corp_name"), (lrow.get("stock_code") or "").strip() or None
    bddd = pdate(rec.get("bddd")) or pdate(lrow.get("rcept_dt"))
    ratio_txt = rec.get("mg_rt") or rec.get("extr_rt") or rec.get("dvmg_rt") or ""
    other_name = clean_name(rec.get("mgptncmp_cmpnm") or rec.get("extr_tgcmp_cmpnm") or "")
    other_code = find_code(other_name, listing, name_idx)
    if other_code == me_code:
        other_code = None

    legs, ratio = [], None
    flags = {
        "bypass_listing": "해당" in str(rec.get("bdlst_atn") or "") and "미해당" not in str(rec.get("bdlst_atn") or ""),
        "put_option": str(rec.get("popt_ctr_atn") or "").strip().startswith(("예", "Y", "해당")),
        "spac": "스팩" in (me_name or "") or "기업인수목적" in (me_name or "") or "스팩" in other_name,
        "external_eval": str(rec.get("exevl_atn") or "").strip().startswith(("예", "Y", "해당", "여")),
    }

    if kind == "MERGER":
        surv, gone, how = me_name, other_name, "제출회사=존속 가정"
        is_spac = lambda n: bool(re.search(r"스팩|기업인수목적", n or ""))
        pos = name_positions(ratio_txt, me_name, other_name)
        ab = absorber_from_method(rec.get("mg_mth"))
        if is_spac(other_name) and not is_spac(me_name):
            surv, gone, how = other_name, me_name, "스팩=존속"
        elif is_spac(me_name):
            surv, gone, how = me_name, other_name, "스팩=존속"
        elif pos:
            surv, gone = (me_name, other_name) if pos[0] < pos[1] else (other_name, me_name)
            how = "비율 문구 순서(존속:소멸)"
        elif ab:
            a, b = ab
            if norm_name(other_name) and norm_name(other_name) in norm_name(a):
                surv, gone, how = other_name, me_name, "합병방법 문구"
            elif norm_name(me_name) in norm_name(b):
                surv, gone, how = other_name, me_name, "합병방법 문구"
        surv_code = me_code if surv == me_name else other_code
        gone_code = other_code if surv == me_name else me_code
        pr = parse_ratio(ratio_txt, surv, gone)
        if pr:
            a, b, order = pr
            if order == "swapped":
                a, b = b, a
            ratio = {"per_target": b / a, "text": ratio_txt.strip()[:300],
                     "basis": f"소멸 1주당 존속 {b / a:.6g}주 (문구 {a:g}:{b:g}, 방향 판정: {how})"}
        legs.append(leg("target", gone, gone_code, listing, bddd))
        legs.append(leg("acquirer", surv, surv_code, listing, bddd))
        title = f"{legs[0]['name']} → {legs[1]['name']} 흡수합병" if gone else f"{me_name} 합병"
    elif kind == "EXCHANGE":
        parent = (rec.get("atextr_cpcmpnm") or "").strip() or me_name
        if norm_name(parent) == norm_name(me_name):
            child, child_code, parent_code = other_name, other_code, me_code
        else:
            child, child_code, parent_code = me_name, me_code, find_code(parent, listing, name_idx)
        pr = parse_ratio(ratio_txt, parent, child)
        if pr:
            a, b, order = pr
            if order == "swapped":
                a, b = b, a
            ratio = {"per_target": b / a, "text": ratio_txt.strip()[:300],
                     "basis": f"자회사 1주당 모회사 {b / a:.6g}주 (문구 {a:g}:{b:g}, 모:자 순 가정)"}
        if parent_code and parent_code == child_code:
            if norm_name(child) == norm_name(me_name):
                parent_code = None
            else:
                child_code = None
        legs.append(leg("target", child, child_code, listing, bddd))
        legs.append(leg("acquirer", parent, parent_code, listing, bddd))
        title = f"{legs[0]['name']} ↔ {legs[1]['name']} 주식{'이전' if '이전' in str(rec.get('extr_sen')) else '교환'}"
    else:  # SPLITMERGER
        pr = parse_ratio(ratio_txt)
        if pr:
            a, b, _ = pr
            ratio = {"per_target": b / a, "text": ratio_txt.strip()[:300],
                     "basis": f"문구 {a:g}:{b:g} — 분할합병은 비율 해석을 원문으로 확인"}
        legs.append(leg("self", me_name, me_code, listing, bddd))
        if other_name:
            legs.append(leg("counter", other_name, other_code, listing, bddd))
        title = f"{me_name} 분할합병 ({other_name or '상대 미상'})"

    # 매수청구권 — 공시된 매수예정가격 + 법정 산식 추정치
    appraisal = []
    plan_txt = str(rec.get("aprskh_plnprc") or "")
    no_right_txt = " ".join(str(rec.get(k) or "") for k in ("mg_stn", "aprskh_plnprc", "aprskh_lmt", "aprskh_pym_plpd_mth", "extr_stn", "extr_sen"))
    no_right = bool(re.search(r"소규모\s*(합병|주식교환|분할합병)|간이\s*(합병|주식교환)|해당\s*사항\s*없|해당\s*없|미해당|적용\s*되지\s*않|부여되지\s*않", no_right_txt))
    flags["no_appraisal"] = no_right
    plan_nums = [float(x.replace(",", "")) for x in NUM_RE.findall(plan_txt)
                 if float(x.replace(",", "")) >= 100]
    for L in legs:
        if not L["code"]:
            continue
        mine = L["code"] == me_code
        stat = statutory_appraisal(L["code"], bddd)
        disclosed = None
        if mine and plan_nums:
            disclosed = plan_nums[0]
            # 문구 안에 상대회사 이름과 가격이 같이 있으면 순서대로 배정
        elif not mine and len(plan_nums) >= 2 and norm_name(L["name"]) in norm_name(plan_txt):
            disclosed = plan_nums[1]
        if no_right and not disclosed:
            continue
        if disclosed or stat:
            appraisal.append({
                "code": L["code"], "name": L["name"],
                "price": disclosed, "calc": stat,
                "source": "공시" if disclosed else "산식추정",
            })
    dates = collect_dates(rec)
    return {
        "type": kind, "title": title,
        "rcept_no": lrow.get("rcept_no"), "rcept_dt": pdate(lrow.get("rcept_dt")), "bddd": bddd,
        "reporter": me_name, "legs": legs, "ratio": ratio, "appraisal": appraisal, "dates": dates,
        "flags": flags,
        "text": {k: str(rec.get(k2) or "").strip()[:600] for k, k2 in (
            ("method", "mg_mth" if kind == "MERGER" else "dvmg_mth" if kind == "SPLITMERGER" else "extr_stn"),
            ("form", "mg_stn" if kind != "EXCHANGE" else "extr_sen"),
            ("purpose", "mg_pp" if kind == "MERGER" else "extr_pp"),
            ("ratio_basis", "mg_rt_bs" if kind == "MERGER" else "extr_rt_bs" if kind == "EXCHANGE" else "dvmg_rt_bs"),
            ("ext_opinion", "exevl_op"),
            ("appraisal_plan", "aprskh_plnprc"),
            ("appraisal_pay", "aprskh_pym_plpd_mth"),
            ("appraisal_effect", "aprskh_ctref"),
            ("appraisal_limit", "aprskh_lmt"),
        )} | {"report_nm": lrow.get("report_nm") or ""},
    }


# ── 공개매수 원문 파싱 ────────────────────────────────────────
def _grab(pat, t, flags=re.S):
    m = re.search(pat, t, flags)
    return m if m else None


def parse_tender(text):
    """공개매수신고서 평문 → 핵심 조건. 찾지 못한 항목은 None."""
    t = re.sub(r"[ \t]+", " ", text)
    out = {"price": None, "qty": None, "qty_min": None, "start": None, "end": None,
           "settle": None, "delist": False, "all_or_none": False, "conf": 0}

    # 가격: '공개매수가격' 근처의 'nn,nnn원'
    for pat in (r"공개\s*매수\s*가격[^\n]{0,80}?([\d,]{3,})\s*원",
                r"매수\s*가격[^\n]{0,60}?보통주[^\n]{0,40}?([\d,]{3,})\s*원",
                r"주당\s*([\d,]{3,})\s*원"):
        m = _grab(pat, t)
        if m:
            out["price"] = float(m.group(1).replace(",", ""))
            break
    # 수량
    for pat in (r"매수\s*예정\s*(?:주식\s*등의\s*)?수(?:량)?[^\n]{0,80}?([\d,]{2,})\s*주",
                r"공개\s*매수\s*(?:할\s*)?예정\s*수량[^\n]{0,80}?([\d,]{2,})\s*주"):
        m = _grab(pat, t)
        if m:
            out["qty"] = float(m.group(1).replace(",", ""))
            break
    m = _grab(r"최소\s*(?:응모|매수)\s*(?:예정\s*)?수량[^\n]{0,60}?([\d,]{2,})\s*주", t)
    if m:
        out["qty_min"] = float(m.group(1).replace(",", ""))
    # 기간
    m = _grab(r"공개\s*매수\s*기간[^\n]{0,20}\n?[^\n]{0,120}", t)
    if m:
        ds = all_dates(m.group(0))
        if len(ds) >= 2:
            out["start"], out["end"] = ds[0], ds[1]
        elif len(ds) == 1:
            out["start"] = ds[0]
    m = _grab(r"결제\s*일[^\n]{0,80}", t)
    if m:
        out["settle"] = pdate(m.group(0))
    # 목적·조건
    head = t[:20000]
    out["delist"] = bool(re.search(r"상장\s*폐지|자진\s*상장폐지|상장을\s*폐지", head))
    out["all_or_none"] = bool(re.search(r"(전부를|전부)\s*매수하지\s*아니|전량\s*매수하지\s*않", t))
    out["conf"] = sum(1 for k in ("price", "qty", "start", "end") if out[k])
    return out


def build_tenders(rows, listing):
    """공개매수 관련 목록 행들 → 대상회사별 이벤트."""
    by_target = {}
    for r in rows:
        k = classify(r.get("report_nm"))
        if not k or not k.startswith("TENDER"):
            continue
        key = (r.get("corp_code"), (r.get("flr_nm") or "").strip())
        by_target.setdefault(key, []).append((k, r))
    events = []
    bidders_per_target = {}
    for (cc, bidder), items in by_target.items():
        bidders_per_target.setdefault(cc, set()).add(bidder)
    for (cc, bidder), items in by_target.items():
        items.sort(key=lambda x: x[1].get("rcept_no"))
        filings = [r for k, r in items if k == "TENDER"]
        if not filings:
            continue
        first, latest = filings[0], filings[-1]
        after = [k for k, r in items if r.get("rcept_no") > first.get("rcept_no")]
        terms = {"price": None, "conf": 0}
        errs = []
        for f_ in ([latest, first] if latest is not first else [latest]):
            try:
                t0 = parse_tender(dart_doc_text(f_["rcept_no"]))
            except Exception as e:
                errs.append(f"{f_['rcept_no']}:{str(e)[:50]}")
                time.sleep(1.0)
                continue
            for k2, v in t0.items():
                if terms.get(k2) in (None, False, 0) and v:
                    terms[k2] = v
            terms["conf"] = sum(1 for k3 in ("price", "qty", "start", "end") if terms.get(k3))
            if terms["conf"] >= 3:
                break
        if errs and not terms.get("price"):
            log(f"공개매수 원문 실패 {latest.get('corp_name')}: " + " / ".join(errs))
        # 상태: 철회 > 결과보고(마감이 지났을 때만) > 마감 경과 > 진행
        today = TODAY.isoformat()
        end = terms.get("end")
        if "TENDER_WITHDRAW" in after:
            status = "철회"
        elif "TENDER_RESULT" in after and (not end or end < today):
            status = "종료"
        elif end and end < today:
            status = "결제대기"
        else:
            status = "진행"
        code = (latest.get("stock_code") or "").strip() or None
        bddd = pdate(first.get("rcept_dt"))
        dates = [d for d in (
            {"label": "신고서 제출", "date": bddd},
            {"label": "공개매수 시작", "date": terms.get("start")},
            {"label": "공개매수 마감", "date": terms.get("end")},
            {"label": "결제일", "date": terms.get("settle")},
        ) if d["date"]]
        tl = leg("target", latest.get("corp_name"), code, listing, bddd)
        events.append({
            "type": "TENDER",
            "title": f"{latest.get('corp_name')} 공개매수 — {bidder}",
            "rcept_no": latest.get("rcept_no"), "first_rcept_no": first.get("rcept_no"),
            "rcept_dt": pdate(latest.get("rcept_dt")), "bddd": bddd, "reporter": bidder,
            "status": status, "legs": [tl], "ratio": None, "appraisal": [],
            "tender": {**terms, "bidder": bidder,
                       "filings": [f"{pdate(r.get('rcept_dt'))} {r.get('report_nm')}" for _, r in items][-8:],
                       "competing": len(bidders_per_target.get(cc, ())) > 1,
                       "amendments": len(filings) - 1},
            "dates": sorted(dates, key=lambda d: d["date"]),
            "flags": {"delist": terms.get("delist"), "competing": len(bidders_per_target.get(cc, ())) > 1},
            "text": {"report_nm": latest.get("report_nm")},
        })
    return events


def spac_listed(code):
    """상장일을 못 받았으면 시세 첫 거래일로 대신한다 (스팩 존속기한 계산용)."""
    try:
        p = load_prices(code, "2018-01-01")
        return p[0][0].isoformat() if p else None
    except Exception:
        return None


def build_spacs(listing, merger_events):
    merging = set()
    for e in merger_events:
        for L in e["legs"]:
            if L["code"] and ("스팩" in (L["name"] or "") or "기업인수목적" in (L["name"] or "")):
                merging.add(L["code"])
    out = []
    for code, info in listing.items():
        nm = info.get("name") or ""
        if "스팩" not in nm and "기업인수목적" not in nm:
            continue
        L = leg("self", nm, code, listing)
        if not L["last"]:
            continue
        base = 10000.0 if L["last"] >= 6000 else 2000.0
        listed = info.get("listed") or spac_listed(code)
        maturity = None
        if listed:
            try:
                ld = date.fromisoformat(listed)
                maturity = date(ld.year + 3, ld.month, min(ld.day, 28)).isoformat()
            except Exception:
                maturity = None
        yrs_total = 3.0
        floor_mat = round(base * (1 + SPAC_RATE) ** yrs_total)
        out.append({
            "type": "SPAC", "title": f"{nm} (스팩)", "rcept_no": None, "rcept_dt": None, "bddd": None,
            "status": "합병발표" if code in merging else "대기",
            "legs": [L], "ratio": None, "appraisal": [],
            "spac": {"ipo_price_est": base, "floor_maturity_est": floor_mat, "listed": listed,
                     "maturity": maturity, "rate": SPAC_RATE, "merging": code in merging},
            "dates": [d for d in ({"label": "상장일", "date": listed},
                                  {"label": "존속기한(추정)", "date": maturity}) if d["date"]],
            "flags": {"spac": True}, "text": {},
        })
    return out


def status_struct(ev, withdrawn_codes):
    if any(L["code"] in withdrawn_codes for L in ev["legs"] if L["code"]):
        return "철회"
    today = TODAY.isoformat()
    ends = [d["date"] for d in ev["dates"] if d["label"] in ("신주 상장예정일", "합병기일", "교환·이전일", "분할합병기일")]
    if ends and max(ends) < today:
        return "완료"
    tr = [d["date"] for d in ev["dates"] if d["label"] == "거래정지 시작"]
    if tr and tr[0] <= today:
        return "거래정지"
    return "진행"


def keep(ev):
    if ev["status"] in ("진행", "결제대기", "거래정지", "대기", "합병발표"):
        return True
    last = max([d["date"] for d in ev["dates"]] or [ev.get("rcept_dt") or "1900-01-01"])
    return (TODAY - date.fromisoformat(last)).days <= KEEP_DONE


# ══════════════════════════════════════════════════════════════
def main():
    if not KEY:
        sys.exit("DART_API_KEY 가 없습니다 — 저장소 Settings → Secrets → Actions 에 등록하세요.")
    start = TODAY - timedelta(days=LOOKBACK)
    log(f"조회기간 {start} ~ {TODAY}")

    listing = {}
    try:
        listing = load_listing()
        log(f"상장종목 {len(listing):,}개")
    except Exception as e:
        log(f"상장 리스트 실패: {type(e).__name__}:{str(e)[:80]}")
    name_idx = {norm_name(v["name"]): c for c, v in listing.items()}

    # 1) 주요사항보고서(B) — 합병·교환·분할합병
    b_rows = list_filings("B", start, TODAY)
    log(f"주요사항보고서 {len(b_rows):,}건")
    struct_rows = [(classify(r.get("report_nm")), r) for r in b_rows]
    struct_rows = [(k, r) for k, r in struct_rows if k in STRUCT]
    withdrawn = {(r.get("stock_code") or "").strip() for r in b_rows
                 if "철회" in (r.get("report_nm") or "") and classify(r.get("report_nm")) in STRUCT}
    log(f"합병·교환·분할합병 공시 {len(struct_rows)}건 (철회 {len(withdrawn)}사)")

    # 회사별로 구조화 API 1번씩 — 결과는 rcept_no 로 목록 행과 매칭
    events, done = [], {}
    by_corp = {}
    for k, r in struct_rows:
        by_corp.setdefault((k, r["corp_code"]), []).append(r)
    for (k, cc), rs in by_corp.items():
        try:
            js = dart_json(STRUCT[k], {"corp_code": cc, "bgn_de": start.strftime("%Y%m%d"),
                                       "end_de": TODAY.strftime("%Y%m%d")})
        except Exception as e:
            log(f"{k} 구조화 실패 {rs[0].get('corp_name')}: {str(e)[:80]}")
            continue
        recs = js.get("list") or []
        rmap = {r["rcept_no"]: r for r in rs}
        # 같은 상대방에 대해선 가장 최근(정정 반영) 1건만
        latest = {}
        for rec in recs:
            cp = norm_name(rec.get("mgptncmp_cmpnm") or rec.get("extr_tgcmp_cmpnm") or rec.get("dvfcmp_cmpnm") or "")
            if rec.get("rcept_no", "") >= latest.get(cp, {}).get("rcept_no", ""):
                latest[cp] = rec
        for rec in latest.values():
            lrow = rmap.get(rec.get("rcept_no")) or rs[-1]
            try:
                ev = build_struct_event(k, rec, lrow, listing, name_idx)
            except Exception as e:
                log(f"{k} 해석 실패 {lrow.get('corp_name')}: {type(e).__name__}:{str(e)[:80]}")
                traceback.print_exc()
                continue
            # 양쪽 회사가 모두 공시한 딜은 하나로 합친다 (정렬된 종목쌍 기준)
            pair = (k, tuple(sorted(filter(None, (L["code"] or norm_name(L["name"]) for L in ev["legs"])))))
            if pair in done:
                # 상대 회사 공시에만 있는 매수예정가격·일정을 먼저 들어온 이벤트에 보탠다
                base = done[pair]
                for a in ev["appraisal"]:
                    if not a.get("price"):
                        continue
                    for b in base["appraisal"]:
                        if b["code"] == a["code"] and not b.get("price"):
                            b["price"], b["source"] = a["price"], "공시"
                    if not any(b["code"] == a["code"] for b in base["appraisal"]):
                        base["appraisal"].append(a)
                have = {(x["label"], x["date"]) for x in base["dates"]}
                base["dates"] = sorted(base["dates"] + [x for x in ev["dates"] if (x["label"], x["date"]) not in have],
                                       key=lambda x: x["date"])
                base.setdefault("also", []).append(ev["rcept_no"])
                continue
            done[pair] = ev
            ev["status"] = status_struct(ev, withdrawn)
            events.append(ev)

    # 2) 공개매수 — 지분공시(D). 세부유형 코드가 맞지 않으면 D 전체에서 거른다
    t_rows = []
    try:
        t_rows = [r for r in list_filings("D", start, TODAY, detail="D004") if classify(r.get("report_nm"))]
    except Exception as e:
        log(f"D004 조회 실패: {str(e)[:60]}")
    if not t_rows:
        t_rows = [r for r in list_filings("D", start, TODAY, max_pages=200)
                  if (classify(r.get("report_nm")) or "").startswith("TENDER")]
    log(f"공개매수 관련 공시 {len(t_rows)}건")
    events += build_tenders(t_rows, listing)

    # 3) 스팩
    spacs = build_spacs(listing, [e for e in events if e["type"] == "MERGER"])
    log(f"스팩 {len(spacs)}종목")
    events += spacs

    events = [e for e in events if keep(e) and any(L.get("code") and L.get("last") for L in e["legs"])]
    for i, e in enumerate(events):
        e["id"] = f"{e['type'][:2]}-{e.get('rcept_no') or (e['legs'][0]['code'] or i)}"
        e["url"] = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={e['rcept_no']}" if e.get("rcept_no") else None
        e.pop("_report_nm", None)

    # 차트용 종가
    cut = TODAY - timedelta(days=PX_DAYS)
    px = {}
    for e in events:
        for L in e["legs"]:
            c = L["code"]
            if c and c not in px and PX.get(c):
                pts = [(d, cl) for d, cl, _ in PX[c] if d >= cut]
                px[c] = {"d": [d.strftime("%y%m%d") for d, _ in pts], "c": [round(cl, 2) for _, cl in pts]}

    last_dates = [L["last_date"] for e in events for L in e["legs"] if L.get("last_date")]
    payload = {
        "market": "kr",
        "date": max(last_dates) if last_dates else TODAY.isoformat(),
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "lookback_days": LOOKBACK, "dart_calls": _CALLS["n"],
        "counts": {t: sum(1 for e in events if e["type"] == t) for t in ("TENDER", "MERGER", "EXCHANGE", "SPLITMERGER", "SPAC")},
        "diag": DIAG[-40:],
        "events": events, "px": px,
    }
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "events_kr.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"), default=str)
    print(f"WROTE {path} · 이벤트 {len(events)}건 {payload['counts']} · DART 호출 {_CALLS['n']}회 · "
          f"{os.path.getsize(path):,} bytes")


if __name__ == "__main__":
    main()
