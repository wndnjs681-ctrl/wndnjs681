#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
주식 주요 일정 캘린더 — 대시보드 '일정' 탭

  국내(한국투자증권 Open API 예탁원정보·휴장일, 시세 조회만): 배당 기준일·공모주 청약·신규 상장·보호예수(의무예치) 해제·주주총회·유무상증자·휴장일
  미국(나스닥 공개 API): 주요 경제지표(CPI·고용·PCE·GDP·ISM·소매판매 …)와 다른 나라 중앙은행 결정, IPO 일정
  연준 FOMC(federalreserve.gov, 실패하면 알려진 일정)
  규칙으로 계산: 국내 옵션 만기(둘째 목요일)·선물옵션 동시만기·코스피200 정기변경, 미국 월물 만기(셋째 금요일)·쿼드러플 위칭, MSCI 분기 리뷰 적용일(통상)
  실적 발표일은 대시보드가 스크리너 자료(earn_date)에서 바로 가져온다.

결과: output/market_index.json 의 "cal" (대시보드 동기화가 이 파일을 옮김)
"""
import json, os, re, time
from datetime import date, datetime, timedelta, timezone

import requests

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
BACK, AHEAD = int(os.getenv("CAL_BACK", "35")), int(os.getenv("CAL_AHEAD", "100"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
KIS = os.getenv("KIS_BASE", "https://openapi.koreainvestment.com:9443")
KEY, SECRET = os.getenv("KIS_APP_KEY", "").strip(), os.getenv("KIS_APP_SECRET", "").strip()

TODAY = datetime.now(KST).date()
D0, D1 = TODAY - timedelta(days=BACK), TODAY + timedelta(days=AHEAD)
EV, LOG = [], {}
T0, BUDGET = time.time(), float(os.getenv("CAL_BUDGET", "720"))      # 전체 시간 상한(초) — 넘으면 남은 외부 조회는 건너뜀
over = lambda: time.time() - T0 > BUDGET


def add(d, mk, cat, n, imp=1, **kw):
    if isinstance(d, (date, datetime)):
        d = d.strftime("%Y-%m-%d")
    if not d or not (D0.isoformat() <= d <= D1.isoformat()):
        return
    EV.append({"d": d, "mk": mk, "cat": cat, "n": n, "imp": imp, **{k: v for k, v in kw.items() if v not in (None, "", [])}})


def ymd(s):
    s = re.sub(r"[^0-9]", "", str(s or ""))
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else None


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


# ───────────────────────── 휴장일 ─────────────────────────
def easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    return date(y, (h + l - 7 * m + 114) // 31, ((h + l - 7 * m + 114) % 31) + 1)


def nth_wd(y, m, wd, n):                     # n번째 요일(wd: 월0~일6), n=-1 이면 마지막
    if n > 0:
        d = date(y, m, 1)
        d += timedelta(days=(wd - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - wd) % 7)


def us_holidays(y):
    def obs(d):
        return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d
    H = {obs(date(y, 1, 1)): "신정", nth_wd(y, 1, 0, 3): "마틴 루서 킹 데이", nth_wd(y, 2, 0, 3): "대통령의 날",
         easter(y) - timedelta(days=2): "성금요일", nth_wd(y, 5, 0, -1): "메모리얼 데이", obs(date(y, 6, 19)): "준틴스",
         obs(date(y, 7, 4)): "독립기념일", nth_wd(y, 9, 0, 1): "노동절", nth_wd(y, 11, 3, 4): "추수감사절", obs(date(y, 12, 25)): "크리스마스"}
    H = {d: n for d, n in H.items() if d.year == y}
    early = {nth_wd(y, 11, 3, 4) + timedelta(days=1): "추수감사절 다음 날 조기 폐장(13시)"}
    if date(y, 12, 24).weekday() < 5:
        early[date(y, 12, 24)] = "크리스마스 이브 조기 폐장(13시)"
    if date(y, 7, 3).weekday() < 5 and date(y, 7, 4).weekday() < 5:
        early[date(y, 7, 3)] = "독립기념일 전날 조기 폐장(13시)"
    return H, early


def kis_token():
    if not KEY or not SECRET:
        print("KIS 키 없음 — 국내 예탁원 일정·휴장일은 건너뜀")
        return None
    try:
        j = requests.post(KIS + "/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": KEY, "appsecret": SECRET}, timeout=20).json()
        if j.get("access_token"):
            return j["access_token"]
        print("KIS 토큰 실패:", {k: v for k, v in j.items() if k != "access_token"})
    except Exception as e:
        print("KIS 토큰 오류:", type(e).__name__, e)
    return None


def kis_get(tok, path, tr, params, tries=3):
    h = {"authorization": "Bearer " + tok, "appkey": KEY, "appsecret": SECRET, "tr_id": tr, "custtype": "P",
         "content-type": "application/json; charset=utf-8"}
    for k in range(tries):
        try:
            j = requests.get(KIS + path, headers=h, params=params, timeout=20).json()
            if j.get("rt_cd") == "0":
                return j
            msg = f"{j.get('msg_cd')} {j.get('msg1')}"
            if "초당" in msg or "EGW00201" in msg:
                time.sleep(1 + k)
                continue
            return {"_err": msg}
        except Exception as e:
            time.sleep(1 + k)
            last = f"{type(e).__name__} {e}"
    return {"_err": "재시도 실패"}


def kr_holidays(tok):
    """KIS 국내휴장일조회(CTCA0903R) — 기준일부터 약 한 달씩. 평일인데 개장일(opnd_yn) 아님 → 휴장."""
    out, d, n = {}, max(D0, TODAY - timedelta(days=5)), 0
    while d <= D1 and n < 8:
        j = kis_get(tok, "/uapi/domestic-stock/v1/quotations/chk-holiday", "CTCA0903R",
                    {"BASS_DT": d.strftime("%Y%m%d"), "CTX_AREA_NK": "", "CTX_AREA_FK": ""})
        rows = j.get("output") or []
        if n == 0:
            print("휴장일 응답 예시:", j.get("_err") or (rows[:2] if rows else "(빈 응답)"))
        if not rows:
            break
        for r in rows:
            dd = ymd(r.get("bass_dt"))
            if dd and r.get("opnd_yn") == "N" and datetime.strptime(dd, "%Y-%m-%d").weekday() < 5:
                out[dd] = True
        last = ymd(rows[-1].get("bass_dt"))
        if not last:
            break
        d = datetime.strptime(last, "%Y-%m-%d").date() + timedelta(days=1)
        n += 1
        time.sleep(0.5)
    LOG["kr_holiday"] = len(out)
    return out


# ───────────────────────── 국내 예탁원 일정(KIS) ─────────────────────────
KSD = {   # 이름: (경로, tr_id, 추가 파라미터)
    "dividend": ("/uapi/domestic-stock/v1/ksdinfo/dividend", "HHKDB669102C0", {"GB1": "0", "HIGH_GB": ""}),
    "pubOffer": ("/uapi/domestic-stock/v1/ksdinfo/pub-offer", "HHKDB669108C0", {}),
    "listInfo": ("/uapi/domestic-stock/v1/ksdinfo/list-info", "HHKDB669107C0", {}),
    "mandDeposit": ("/uapi/domestic-stock/v1/ksdinfo/mand-deposit", "HHKDB669110C0", {}),
    "meet": ("/uapi/domestic-stock/v1/ksdinfo/sharehld-meet", "HHKDB669111C0", {}),
    "paidin": ("/uapi/domestic-stock/v1/ksdinfo/paidin-capin", "HHKDB669100C0", {"GB1": "1"}),
    "bonus": ("/uapi/domestic-stock/v1/ksdinfo/bonus-issue", "HHKDB669101C0", {}),
}


def ksd(tok, name):
    path, tr, extra = KSD[name]
    rows, seen, first = [], set(), True
    s = D0
    while s <= D1:                                         # 한 번에 많이 주지 않아 보름씩 끊어 받는다
        e = min(D1, s + timedelta(days=14))
        p = {"CTS": "", "F_DT": s.strftime("%Y%m%d"), "T_DT": e.strftime("%Y%m%d"), "SHT_CD": "", **extra}
        j = kis_get(tok, path, tr, p)
        got = j.get("output1") or j.get("output") or []
        if isinstance(got, dict):
            got = [got]
        if first:
            print(f"[{name}] 응답 예시:", j.get("_err") or (got[0] if got else "(빈 응답)"))
            first = False
            if j.get("_err"):
                LOG[name] = "오류 " + j["_err"]
                return []
        for r in got:
            k = json.dumps(r, sort_keys=True, ensure_ascii=False)
            if k not in seen:
                seen.add(k)
                rows.append(r)
        s = e + timedelta(days=1)
        time.sleep(0.35)
    LOG[name] = len(rows)
    return rows


def g(r, *ks):
    for k in ks:
        v = r.get(k)
        if v not in (None, "", "0", 0, "00000000"):
            return v
    return None


def kr_ksd(tok, big):
    """big: 시가총액(억) 등 화면에서 쓰는 국내 종목 정보 {종목코드: (이름, 시총)}"""
    for r in ksd(tok, "dividend"):
        t, nm = g(r, "sht_cd"), g(r, "isin_name", "kor_isnm")
        amt, kind = num(g(r, "per_sto_divi_amt")), g(r, "divi_kind") or ""
        d = ymd(g(r, "record_date"))
        if not (t and d) or t not in big:                     # ETF 분배금·소형주는 뺌(스크리너 종목만)
            continue
        mc = (big.get(t) or ("", 0, 0))[1]
        add(d, "kr", "div", f"{nm} 배당 기준일", 2 if mc >= 10000 else 1, tk=t,
            x=" · ".join(x for x in [kind.strip(), f"주당 {amt:,.0f}원" if amt else "", f"지급 {ymd(g(r, 'divi_pay_dt'))[5:]}" if ymd(g(r, 'divi_pay_dt')) else ""] if x))
    for r in ksd(tok, "pubOffer"):
        t, nm = g(r, "sht_cd"), g(r, "isin_name", "kor_isnm")
        price = num(g(r, "fix_subscr_pri"))
        sub = str(g(r, "subscr_dt") or "")
        m = re.findall(r"\d{4}[.\-/]?\d{2}[.\-/]?\d{2}", sub)
        if m:
            d1, d2 = ymd(m[0]), ymd(m[-1])
            add(d1, "kr", "ipo", f"{nm} 공모주 청약", 2, tk=t, end=d2,
                x=" · ".join(x for x in [f"확정 공모가 {price:,.0f}원" if price else "공모가 미정", f"청약 {d1[5:]}~{d2[5:]}" if d2 != d1 else "", f"주관 {g(r, 'lead_mgr')}" if g(r, "lead_mgr") else ""] if x))
        ld = ymd(g(r, "list_dt"))
        if ld:
            add(ld, "kr", "ipo", f"{nm} 상장(공모)", 2, tk=t, x=f"공모가 {price:,.0f}원" if price else "")
    for r in ksd(tok, "listInfo"):
        t, nm, d = g(r, "sht_cd"), g(r, "isin_name", "kor_isnm"), ymd(g(r, "list_dt"))
        kind = str(g(r, "issue_type", "stk_kind") or "")
        if not (t and d):
            continue
        q = num(g(r, "issue_stk_qty"))
        if t not in big and not ("신규" in kind or "공모" in kind):      # 추가 상장은 스크리너 종목만
            continue
        tot = num(g(r, "tot_issue_stk_qty"))
        pct = q / tot * 100 if q and tot else None
        if "신규" in kind or "공모" in kind:
            add(d, "kr", "ipo", f"{nm} 신규 상장", 2, tk=t, x=kind)
        elif pct is not None and pct >= 1:                     # 추가 상장 — 발행주식의 1% 이상만(물량 부담)
            add(d, "kr", "cap", f"{nm} 추가 상장 {pct:.1f}%", 2 if pct >= 5 else 1, tk=t, x=f"{kind} · {q:,.0f}주")
    for r in ksd(tok, "mandDeposit"):
        t, nm = g(r, "sht_cd"), g(r, "isin_name", "kor_isnm")
        rng = re.findall(r"\d{4}[./-]?\d{2}[./-]?\d{2}", str(g(r, "depo_date") or ""))   # "예치일 ~ 반환일"
        d = ymd(rng[-1]) if rng else None
        q = num(g(r, "stk_qty", "depo_qty", "dpst_qty"))
        if not (t and d):
            continue
        _, mc, px = big.get(t) or ("", 0, 0)
        sh = mc * 1e8 / px if mc and px else None                   # 상장주식수 추정(시총 ÷ 주가)
        pct = q / sh * 100 if q and sh else None
        val = q * px / 1e8 if q and px else None                    # 억원
        why = str(g(r, "depo_reason", "dpst_rson", "reason") or "").strip(" -")
        add(d, "kr", "lock", f"{nm} 보호예수 해제", 2 if (pct or 0) >= 3 or (val or 0) >= 100 else 1, tk=t,
            x=" · ".join(x for x in [f"{q:,.0f}주" if q else "", f"상장주식의 약 {pct:.1f}%" if pct else "", f"약 {val:,.0f}억원" if val else "", why,
                                     f"예치 {ymd(rng[0])[5:]}~" if len(rng) > 1 else ""] if x))
    for r in ksd(tok, "meet"):
        t, nm, d = g(r, "sht_cd"), g(r, "isin_name", "kor_isnm"), ymd(g(r, "gen_meet_dt"))
        if t and d and (big.get(t) or ("", 0, 0))[1] >= 3000:
            add(d, "kr", "meet", f"{nm} {g(r, 'gen_meet_type') or '주주총회'}", 1, tk=t, x=str(g(r, "agenda") or "")[:80])
    for r in ksd(tok, "paidin"):
        t, nm = g(r, "sht_cd"), g(r, "isin_name", "kor_isnm")
        d = ymd(g(r, "list_date", "list_dt"))
        if t and d:
            add(d, "kr", "cap", f"{nm} 유상증자 신주 상장", 2, tk=t,
                x=" · ".join(x for x in [f"발행가 {num(g(r, 'fix_price')):,.0f}원" if num(g(r, "fix_price")) else "", f"배정률 {g(r, 'fix_rate')}" if g(r, "fix_rate") else ""] if x))
        rd = ymd(g(r, "record_date"))
        if t and rd:
            add(rd, "kr", "cap", f"{nm} 유상증자 기준일", 1, tk=t)
    for r in ksd(tok, "bonus"):
        t, nm = g(r, "sht_cd"), g(r, "isin_name", "kor_isnm")
        rd, ld = ymd(g(r, "record_date")), ymd(g(r, "list_date", "list_dt"))
        if t and rd:
            add(rd, "kr", "cap", f"{nm} 무상증자 기준일", 1, tk=t, x=f"배정률 {g(r, 'fix_rate')}" if g(r, "fix_rate") else "")
        if t and ld:
            add(ld, "kr", "cap", f"{nm} 무상증자 신주 상장", 1, tk=t)


# ───────────────────────── 미국·세계 경제지표(나스닥) ─────────────────────────
MACRO = [   # (나라, 정규식, 한국어 이름, 중요도)
    ("United States", r"^(Core )?CPI( m/m| y/y|)$|Consumer Price Index", "미국 소비자물가(CPI)", 3),
    ("United States", r"Nonfarm Payrolls|Non-Farm", "미국 고용보고서(비농업 고용)", 3),
    ("United States", r"Unemployment Rate", "미국 실업률", 3),
    ("United States", r"FOMC (Meeting )?Minutes", "FOMC 의사록", 2),
    ("United States", r"(Core )?PCE Price", "미국 PCE 물가", 3),
    ("United States", r"^GDP|Gross Domestic Product", "미국 GDP", 3),
    ("United States", r"^(Core )?PPI|Producer Price", "미국 생산자물가(PPI)", 2),
    ("United States", r"(Core )?Retail Sales", "미국 소매판매", 2),
    ("United States", r"ISM Manufacturing PMI", "ISM 제조업 PMI", 2),
    ("United States", r"ISM (Non-Manufacturing|Services) PMI", "ISM 서비스업 PMI", 2),
    ("United States", r"JOLTS", "JOLTS 구인", 2),
    ("United States", r"ADP Nonfarm|ADP Employment", "ADP 민간 고용", 1),
    ("United States", r"Initial Jobless Claims", "신규 실업수당 청구", 1),
    ("United States", r"Michigan Consumer Sentiment", "미시간대 소비자심리", 1),
    ("United States", r"Durable Goods Orders", "내구재 주문", 1),
    ("United States", r"(Fed Chair|Powell).*(Speaks|Testifies)", "연준 의장 발언", 2),
    ("South Korea", r"Interest Rate Decision|Base Rate", "한국은행 금통위 기준금리 결정", 3),
    ("South Korea", r"^(Exports|Imports|Trade Balance)", "한국 수출입", 2),
    ("South Korea", r"CPI|Consumer Price", "한국 소비자물가", 2),
    ("South Korea", r"GDP", "한국 GDP", 2),
    ("Japan", r"BoJ Interest Rate Decision|Interest Rate Decision", "일본은행 금리 결정", 2),
    ("Euro Zone", r"(ECB )?Interest Rate Decision|Deposit Facility Rate", "ECB 금리 결정", 2),
    ("China", r"^CPI|Consumer Price", "중국 소비자물가", 1),
    ("China", r"^GDP", "중국 GDP", 2),
    ("China", r"(Manufacturing|NBS) PMI", "중국 제조업 PMI", 1),
    ("China", r"Loan Prime Rate", "중국 대출우대금리(LPR)", 1),
    ("China", r"^(Exports|Trade Balance)", "중국 수출입", 1),
]
CTRY = {"United States": "us", "South Korea": "kr"}


def nasdaq(path, params):
    if over():
        return {}
    for k in range(2):
        try:
            r = requests.get("https://api.nasdaq.com/api/" + path, params=params, timeout=12,
                             headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"})
            return r.json()
        except Exception as e:
            time.sleep(1.5 + k)
    return {}


def to_kst(d, hhmm):
    """나스닥 경제지표 'gmt' 값은 실제로는 고정 UTC-5(서머타임 없는 미 동부 표준시). 한국 시각(+14시간)으로."""
    m = re.match(r"(\d{1,2}):(\d{2})", str(hhmm or ""))
    if not m:
        return None
    t = datetime.strptime(d, "%Y-%m-%d").replace(hour=int(m.group(1)), minute=int(m.group(2)), tzinfo=timezone(timedelta(hours=-5))).astimezone(KST)
    return t.strftime("%m/%d %H:%M")


def macro():
    n, hit, first, miss = 0, 0, True, 0
    d = D0
    seen = {}
    while d <= D1:
        if d.weekday() < 5:
            j = nasdaq("calendar/economicevents", {"date": d.isoformat()})
            rows = ((j.get("data") or {}).get("rows")) or []
            if first:
                print("나스닥 경제지표 응답 예시:", rows[:2] if rows else str(j)[:300])
                first = False
            n += 1
            if not rows:
                miss += 1
                if n >= 4 and hit == 0 and miss >= 4:
                    print("나스닥 경제지표가 계속 비어 있음 — 중단")
                    break
            else:
                miss = 0
            if n % 20 == 0:
                print(f"  경제지표 {d} 까지 {n}일 · {hit}건 · {time.time() - T0:.0f}초", flush=True)
            for r in rows:
                c, ev = (r.get("country") or "").strip(), re.sub(r"\s+", " ", (r.get("eventName") or "").strip())
                for cc, rx, ko, imp in MACRO:
                    if c == cc and re.search(rx, ev, re.I):
                        key = (d.isoformat(), ko)
                        if key in seen:                     # 같은 지표 여러 줄(m/m·y/y) → 하나로, 세부는 덧붙임
                            if seen[key] is not None:
                                seen[key]["x"] = (seen[key].get("x", "") + " · " + ev + _val(r))[:240]
                            break
                        kst = to_kst(d.isoformat(), r.get("gmt"))
                        mk_ = CTRY.get(c, "gl")
                        dd = d if mk_ == "us" or not kst else date(d.year + (kst[:2] == "01" and d.month == 12), int(kst[:2]), int(kst[3:5]))   # 미국 밖 일정은 한국 날짜에
                        add(dd, mk_, "cb" if "금리" in ko or "FOMC" in ko else "macro", ko, imp, kst=kst, x=ev + _val(r))
                        seen[key] = EV[-1] if EV and EV[-1]["n"] == ko else None
                        hit += 1
                        break
            time.sleep(0.25)
        d += timedelta(days=1)
    LOG["macro"] = f"{hit}건 / {n}일 조회"


def _val(r):
    a, c, p = ((r.get(k) or "").replace("&nbsp;", "").strip() for k in ("actual", "consensus", "previous"))
    s = [f"실제 {a}" if a and a not in ("-", "--") else "", f"예상 {c}" if c and c not in ("-", "--") else "", f"이전 {p}" if p and p not in ("-", "--") else ""]
    s = [x for x in s if x]
    return (" (" + ", ".join(s) + ")") if s else ""


def us_ipo():
    m, cnt = D0.replace(day=1), 0
    months = set()
    while m <= D1:
        months.add(m.strftime("%Y-%m"))
        m = (m + timedelta(days=32)).replace(day=1)
    for ym in sorted(months):
        j = nasdaq("ipo/calendar", {"date": ym})
        data = j.get("data") or {}
        for key, lab in (("priced", "상장"), ("upcoming", "상장 예정"), ("filed", None)):
            blk = data.get(key) or {}
            rows = (blk.get("rows") if isinstance(blk, dict) else None) or ((blk.get("upcomingTable") or {}).get("rows") if isinstance(blk, dict) else None) or []
            if not lab:
                continue
            for r in rows:
                d = r.get("pricedDate") or r.get("expectedPriceDate")
                try:
                    d = datetime.strptime(d, "%m/%d/%Y").date()
                except Exception:
                    continue
                val = str(r.get("dollarValueOfSharesOffered") or "").replace("$", "").replace(",", "")
                try:
                    usd = float(val)
                except ValueError:
                    usd = 0
                add(d, "us", "ipo", f"{r.get('companyName') or r.get('proposedTickerSymbol')} {lab}", 2 if usd >= 3e8 else 1,
                    tk=r.get("proposedTickerSymbol"), x=" · ".join(x for x in [r.get("proposedExchange") or "", f"공모가 ${r.get('proposedSharePrice')}" if r.get("proposedSharePrice") else "", f"규모 ${usd / 1e6:,.0f}M" if usd else ""] if x))
                cnt += 1
        time.sleep(0.3)
    LOG["us_ipo"] = cnt


# ───────────────────────── 연준 FOMC ─────────────────────────
FOMC_KNOWN = {2026: ["01-27/28", "03-17/18", "04-28/29", "06-16/17", "07-28/29", "09-15/16", "10-27/28", "12-08/09"]}
MON = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"])}


def fomc():
    got = {}
    try:
        html = requests.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", headers={"User-Agent": UA}, timeout=25).text
        for blk in re.split(r'<div class="panel panel-default">', html):
            y = re.search(r"(\d{4}) FOMC Meetings", blk)
            if not y:
                continue
            y = int(y.group(1))
            for mm, dd in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<', blk, re.S):
                mon = MON.get(mm.split("/")[-1]) if "/" in mm else MON.get(mm)
                ds = re.findall(r"\d+", dd)
                if not mon or not ds:
                    continue
                last = int(ds[-1])
                m2 = mon if "/" not in mm or int(ds[-1]) > int(ds[0]) else MON.get(mm.split("/")[-1])
                try:
                    got[date(y, m2, last)] = "*" in dd
                except ValueError:
                    pass
    except Exception as e:
        print("FOMC 페이지:", type(e).__name__, e)
    src = "federalreserve.gov"
    if not got:
        src = "알려진 일정"
        for y, L in FOMC_KNOWN.items():
            for s in L:
                m, dd = s.split("-")
                got[date(y, int(m), int(dd.split("/")[-1]))] = s in ("03-17/18", "06-16/17", "09-15/16", "12-08/09")
    for d, sep in got.items():
        kst = (datetime(d.year, d.month, d.day, 14, 0) + timedelta(hours=13 if nth_wd(d.year, 3, 6, 2) <= d < nth_wd(d.year, 11, 6, 1) else 14)).strftime("%m/%d %H:%M")
        add(d, "us", "cb", "FOMC 금리 결정" + (" · 경제전망(점도표)" if sep else ""), 3, kst=kst, x="성명서 14:00(미 동부) · 의장 기자회견 14:30")
        add(d - timedelta(days=1), "us", "cb", "FOMC 회의 시작(이틀째 결정)", 1)
        add(d + timedelta(days=21), "us", "cb", "FOMC 의사록 공개(통상 3주 뒤)", 1, est=True)
    LOG["fomc"] = f"{len(got)}회 ({src})"


# ───────────────────────── 규칙으로 정해지는 일정 ─────────────────────────
def rules(krh, usH):
    def kr_bd(d, back=True):
        while d.weekday() >= 5 or d.isoformat() in krh:
            d += timedelta(days=-1 if back else 1)
        return d

    def us_bd(d, back=True):
        while d.weekday() >= 5 or d in usH:
            d += timedelta(days=-1 if back else 1)
        return d

    m = D0.replace(day=1)
    while m <= D1:
        y, mo = m.year, m.month
        q = mo in (3, 6, 9, 12)
        k = kr_bd(nth_wd(y, mo, 3, 2))
        add(k, "kr", "expiry", "국내 선물·옵션 동시만기(네 마녀의 날)" if q else "국내 옵션 만기일", 3 if q else 2,
            x="코스피200 선물·옵션, 개별주식 선물·옵션 만기 — 장 마감 무렵 프로그램 매매 변동성" if q else "코스피200 월물 옵션 만기(둘째 목요일)")
        if mo in (6, 12):
            add(kr_bd(k + timedelta(days=1), back=False), "kr", "index", "코스피200·코스닥150 정기변경 적용", 2,
                x="동시만기 다음 거래일부터 새 구성 — 편입·편출 종목 수급(발표는 통상 한 달 전 금요일)")
        u = us_bd(nth_wd(y, mo, 4, 3))
        add(u, "us", "expiry", "미국 쿼드러플 위칭" if q else "미국 월물 옵션 만기", 3 if q else 2,
            x="주가지수 선물·옵션, 개별주식 옵션 동시만기 · S&P500 분기 리밸런싱 적용(장 마감)" if q else "셋째 금요일 · 거래량 증가")
        if mo in (2, 5, 8, 11):
            last = us_bd(date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1))
            add(last, "gl", "index", "MSCI 분기 리뷰 리밸런싱(통상)", 2, est=True,
                x="월 마지막 거래일 장 마감에 편입·편출 반영 — 발표는 보통 그 달 둘째 주. 정확한 날짜는 MSCI 공지로 확인")
        m = (m + timedelta(days=32)).replace(day=1)


def main():
    p = os.path.join(OUT, "market_index.json")
    mi = json.load(open(p, encoding="utf-8"))
    big = {}
    try:
        u = json.load(open(os.path.join(OUT, "universe_kr.json"), encoding="utf-8"))
        big = {r["ticker"]: (r.get("name") or "", (r.get("mcap") or 0), (r.get("price") or 0)) for r in u["rows"] if r.get("ticker") and not r.get("is_etf")}
    except Exception:
        pass

    old = mi.get("cal") or {}
    try:                                                    # 하루 여러 번 도는 워크플로 — 6시간 안에 받았으면 건너뜀
        age = (datetime.now(KST) - datetime.strptime(old.get("asof", ""), "%Y-%m-%d %H:%M").replace(tzinfo=KST)).total_seconds() / 3600
        if age < float(os.getenv("CAL_MIN_HOURS", "6")):
            print(f"일정은 {age:.1f}시간 전에 받음 — 건너뜀")
            return
    except ValueError:
        pass
    tok = kis_token()
    krh = kr_holidays(tok) if tok else {}
    if not krh:                                             # 못 받으면 지난 값
        krh = {e["d"]: True for e in old.get("ev", []) if e.get("cat") == "holiday" and e.get("mk") == "kr"}
    for d in sorted(krh):
        add(d, "kr", "holiday", "국내 증시 휴장", 2)
    usH = {}
    for y in {D0.year, D1.year}:
        H, early = us_holidays(y)
        usH.update(H)
        for d, n in H.items():
            add(d, "us", "holiday", f"미국 증시 휴장 · {n}", 2)
        for d, n in early.items():
            add(d, "us", "holiday", n, 1)

    for step, fn in (("국내 예탁원", lambda: kr_ksd(tok, big) if tok else None), ("FOMC", fomc), ("경제지표", macro), ("미국 IPO", us_ipo)):
        n0 = len(EV)
        try:
            fn()
        except Exception as e:
            print(f"{step} 실패: {type(e).__name__}: {e}")
            LOG[step] = f"실패 {type(e).__name__}"
        print(f"{step}: +{len(EV) - n0}건")
    rules(krh, usH)

    # 이번에 못 받은 출처는 지난 값 유지(카테고리·시장 단위)
    have = {(e["mk"], e["cat"]) for e in EV}
    keep = [e for e in old.get("ev", []) if (e.get("mk"), e.get("cat")) not in have and D0.isoformat() <= e.get("d", "") <= D1.isoformat()]
    if keep:
        print(f"지난 값 유지 {len(keep)}건:", sorted({(e['mk'], e['cat']) for e in keep}))
    ev = sorted(EV + keep, key=lambda e: (e["d"], -e["imp"], e["n"]))
    uniq, seen = [], set()
    for e in ev:
        k = (e["d"], e["mk"], e["n"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    mi["cal"] = {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "range": [D0.isoformat(), D1.isoformat()], "src": LOG, "ev": uniq}
    json.dump(mi, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    from collections import Counter
    print("출처:", LOG)
    print("분류:", Counter((e["mk"], e["cat"]) for e in uniq))
    print(f"→ {p} cal {len(uniq)}건")


if __name__ == "__main__":
    main()
