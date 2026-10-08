#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CAN SLIM 재무·수급 데이터 수집 — 스크리너 '오닐' 체크리스트용.

  미국 (Yahoo)
    C  분기 EPS·매출 (fundamentals-timeseries + earningsHistory)
    A  연간 EPS·매출·순이익·자본(ROE 계산)·부채
    S  발행주식 수 추이(자사주 매입), 유통주식 수, 부채
    I  기관 보유 비율·기관 수, 상위 기관 보유 증감(institutionOwnership pctChange)
  국내 (WiseReport = 네이버 증권 기업분석 원본 → 실패 시 FnGuide Financial Highlight)
    C  분기 매출·영업이익·지배순이익·EPS
    A  연간 같은 항목 + ROE·부채비율
    S  발행주식 수 추이
    I  기관·외국인 60거래일 순매수 금액 (pykrx = KRX, 시장 전체 한 번에)

산출물: output/canslim_{kr,us}.json, output/consensus_kr.json(국내 12M 선행·후행 EPS, BPS → screener_full.py)
  {"asof":..., "n":..., "diag":{...}, "p":{ticker:{
      "q":[["2025-06", eps, 매출, 순이익], ...]  (오래된→최근, 실적만, 추정치 제외)
      "y":[["2024", eps, 매출, 순이익, 자본, 부채비율], ...]
      "sh":[["2024-12", 주식수], ...], "fl": 유통주식수, "so": 발행주식수,
      "fe":[["2026-12", 추정EPS], ...]  (국내: 연간 컨센서스 추정 EPS, 없으면 빈 목록)
      "roe": ROE%, "ih": 기관보유%, "ic": 기관수, "inet": [증가 기관수, 감소 기관수], "ipc": 상위기관 평균 증감%,
      "inst60": 기관 60일 순매수(억원), "frgn60": 외국인 60일 순매수(억원),
      "src": "...", "at": "YYYY-MM-DD"}}}

재무는 분기마다 바뀌므로 REFRESH_DAYS(기본 7일) 지난 종목만 다시 받는다. 수급(inst60/frgn60)은 매번 갱신.
"""
import json, os, re, sys, time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.strftime("%Y-%m-%d")
OUT_DIR = os.getenv("OUT_DIR", "output")
REFRESH_DAYS = int(os.getenv("REFRESH_DAYS", "7"))
TIME_BUDGET = float(os.getenv("TIME_BUDGET_MIN", "50")) * 60
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
RAW = "https://raw.githubusercontent.com/wndnjs681-ctrl/wndnjs681/main/"
T0 = time.time()
ERR = {}


DEADLINE = T0 + TIME_BUDGET


def over():
    return time.time() > DEADLINE


def note(k, e):
    m = f"{type(e).__name__}:{str(e)[:70]}"
    ERR.setdefault(k, {})
    ERR[k][m] = ERR[k].get(m, 0) + 1


def fnum(x):
    try:
        if x is None:
            return None
        if isinstance(x, dict):
            x = x.get("raw")
        if isinstance(x, str):
            x = x.replace(",", "").strip()
            if x in ("", "-", "N/A", "nan", "완전잠식"):
                return None
        v = float(x)
        return v if v == v else None
    except Exception:
        return None


def universe(mk):
    p = os.path.join(OUT_DIR, f"universe_{mk}.json")
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        try:
            d = requests.get(RAW + f"output/universe_{mk}.json", timeout=60).json()
        except Exception as e:
            print(f"[{mk}] 유니버스 없음: {e}")
            return []
    return [str(r["ticker"]) for r in d.get("rows", []) if r.get("ticker")]


def load_prev(mk):
    try:
        return json.load(open(os.path.join(OUT_DIR, f"canslim_{mk}.json"), encoding="utf-8")).get("p") or {}
    except Exception:
        return {}


def stale(rec):
    if not rec or not (rec.get("q") or rec.get("y")):
        return True
    if len(rec.get("q") or []) < 5 or rec.get("src") == "Yahoo":   # 전년 동기 비교가 안 되는 기록은 다시 받는다
        return True
    if rec.get("y") and "fe" not in rec:                     # 추정 EPS 를 모으기 전 기록 → 한 번 다시 받는다
        return True
    return (rec.get("at") or "0000") < (NOW - timedelta(days=REFRESH_DAYS)).strftime("%Y-%m-%d")


# ═════════════════════════ 미국 ═════════════════════════
def yahoo_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    try:
        s.get("https://fc.yahoo.com", timeout=15)
    except Exception:
        pass
    crumb = (s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15).text or "").strip()
    if not crumb or len(crumb) > 40 or "<" in crumb:
        raise RuntimeError(f"crumb 이상: {crumb[:30]!r}")
    return s, crumb


TS_TYPES = ["quarterlyDilutedEPS", "quarterlyBasicEPS", "quarterlyTotalRevenue", "quarterlyNetIncomeCommonStockholders",
            "quarterlyOrdinarySharesNumber",
            "annualDilutedEPS", "annualBasicEPS", "annualTotalRevenue", "annualNetIncomeCommonStockholders",
            "annualStockholdersEquity", "annualTotalDebt", "annualOrdinarySharesNumber"]


def us_timeseries(sess, crumb, sym):
    p2 = int(time.time())
    p1 = p2 - 6 * 366 * 86400
    r = sess.get(f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}",
                 params={"type": ",".join(TS_TYPES), "period1": p1, "period2": p2, "crumb": crumb,
                         "merge": "false", "padTimeSeries": "true"}, timeout=25)
    out = {}
    for blk in ((r.json() or {}).get("timeseries") or {}).get("result") or []:
        typ = ((blk.get("meta") or {}).get("type") or [None])[0]
        if not typ or typ not in blk:
            continue
        ser = {}
        for o in blk.get(typ) or []:
            if not o:
                continue
            d = (o.get("asOfDate") or "")[:7]
            v = fnum((o.get("reportedValue") or {}).get("raw"))
            if d and v is not None:
                ser[d] = v
        out[typ] = ser
    return out


def us_summary(sess, crumb, sym):
    r = sess.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}",
                 params={"modules": "earningsHistory,defaultKeyStatistics,majorHoldersBreakdown,institutionOwnership,financialData",
                         "crumb": crumb}, timeout=25)
    res = ((r.json() or {}).get("quoteSummary") or {}).get("result") or []
    return res[0] if res else {}


def us_one(sess, crumb, t):
    sym = t.replace(".", "-")
    rec = {"src": "Yahoo"}
    ts = {}
    try:
        ts = us_timeseries(sess, crumb, sym)
    except Exception as e:
        note("us_ts", e)
    sm = {}
    try:
        sm = us_summary(sess, crumb, sym)
    except Exception as e:
        note("us_sum", e)
    if not ts and not sm:
        return None

    def pick(*keys):
        for k in keys:
            if ts.get(k):
                return ts[k]
        return {}

    qe, qr, qn = pick("quarterlyDilutedEPS", "quarterlyBasicEPS"), pick("quarterlyTotalRevenue"), pick("quarterlyNetIncomeCommonStockholders")
    # earningsHistory 로 분기 EPS 보충(타임시리즈는 보통 최근 4~5분기뿐)
    for h in ((sm.get("earningsHistory") or {}).get("history") or []):
        d = h.get("quarter") or {}
        if isinstance(d, dict) and d.get("fmt"):
            k = d["fmt"][:7]
            v = fnum(h.get("epsActual"))
            if v is not None and k not in qe:
                qe[k] = v
            est = fnum(h.get("epsEstimate"))
            if v is not None and est is not None:            # 어닝 서프라이즈(미너비니): [분기, 실제, 예상]
                rec.setdefault("sur", []).append([k, v, est])
    qk = sorted(set(qe) | set(qr))[-12:]
    rec["q"] = [[k, qe.get(k), qr.get(k), qn.get(k)] for k in qk]
    if rec.get("sur"):
        rec["sur"] = sorted(rec["sur"])[-4:]

    ye, yr, yn = pick("annualDilutedEPS", "annualBasicEPS"), pick("annualTotalRevenue"), pick("annualNetIncomeCommonStockholders")
    yq, yd = pick("annualStockholdersEquity"), pick("annualTotalDebt")
    yk = sorted(set(ye) | set(yr))[-6:]
    rec["y"] = []
    for k in yk:
        eq, debt = yq.get(k), yd.get(k)
        rec["y"].append([k[:4], ye.get(k), yr.get(k), yn.get(k), eq, round(debt / eq * 100, 1) if eq and debt is not None and eq > 0 else None])

    sh = dict(pick("annualOrdinarySharesNumber"))
    sh.update(pick("quarterlyOrdinarySharesNumber"))
    rec["sh"] = [[k, sh[k]] for k in sorted(sh)[-8:]]

    ks = sm.get("defaultKeyStatistics") or {}
    fd = sm.get("financialData") or {}
    mh = sm.get("majorHoldersBreakdown") or {}
    rec["fl"] = fnum(ks.get("floatShares"))
    rec["so"] = fnum(ks.get("sharesOutstanding"))
    roe = fnum(fd.get("returnOnEquity"))
    rec["roe"] = round(roe * 100, 1) if roe is not None else None
    ih = fnum(mh.get("institutionsPercentHeld"))
    rec["ih"] = round(ih * 100, 1) if ih is not None else None
    rec["ic"] = fnum(mh.get("institutionsCount"))
    own = (sm.get("institutionOwnership") or {}).get("ownershipList") or []
    chg = [fnum(o.get("pctChange")) for o in own]
    chg = [c for c in chg if c is not None]
    if chg:
        rec["inet"] = [sum(1 for c in chg if c > 0.001), sum(1 for c in chg if c < -0.001)]
        rec["ipc"] = round(sum(chg) / len(chg) * 100, 1)
    de = fnum(fd.get("debtToEquity"))
    if de is not None:
        rec["de"] = round(de, 1)
    try:
        sc = sec_one(t)
        if sc:
            if len([q for q in sc["q"] if q[1] is not None or q[3] is not None]) >= len(rec["q"]):
                rec["q"] = sc["q"]
            if len(sc["y"]) >= len(rec["y"]):
                rec["y"] = sc["y"]
            rec["src"] = "SEC+Yahoo"
            ERR.setdefault("sec_ok", {}); ERR["sec_ok"]["n"] = ERR["sec_ok"].get("n", 0) + 1
    except Exception as e:
        note("sec", e)
    time.sleep(0.1)
    if not rec["q"] and not rec["y"]:
        return None
    return rec


# ── SEC EDGAR (XBRL companyfacts): 분기·연간 EPS/매출/순이익 장기 이력. 야후 무료 데이터는 4분기·1~4년뿐이라 보강용 ──
SEC_UA = os.getenv("SEC_UA") or "personal-stock-screener research (github.com/wndnjs681-ctrl/wndnjs681)"
SEC = {"map": None}
CONCEPTS = {"eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic", "EarningsPerShareBasicAndDiluted"],
            "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                    "RevenueFromContractWithCustomerIncludingAssessedTax", "RevenuesNetOfInterestExpense"],
            "ni": ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"]}


def sec_map():
    if SEC["map"] is None:
        SEC["map"] = {}
        try:
            r = requests.get("https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": SEC_UA}, timeout=30)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} — SEC_UA(이름 이메일) 확인")
            for v in r.json().values():
                SEC["map"][str(v["ticker"]).upper().replace(".", "-")] = int(v["cik_str"])
        except Exception as e:
            ua = os.getenv("SEC_UA") or ""
            note("sec_map", RuntimeError(f"{e} | SEC_UA 등록됨={bool(ua)} · 이메일 형식={'@' in ua}"))
    return SEC["map"]


def _days(a, b):
    return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days


def _series(facts, names, unit_pred):
    """개념 목록 → {(start,end): val} 분기·연간. 같은 기간은 늦게 제출된 값, 여러 개념은 앞 순위 우선."""
    q, y = {}, {}
    for name in names:
        units = ((facts.get(name) or {}).get("units") or {})
        for u, arr in units.items():
            if not unit_pred(u):
                continue
            best = {}
            for o in arr:
                st, en, v = o.get("start"), o.get("end"), o.get("val")
                if not st or not en or v is None:
                    continue
                k = (st, en)
                if k not in best or (o.get("filed") or "") > (best[k][1] or ""):
                    best[k] = (v, o.get("filed"))
            for (st, en), (v, _) in best.items():
                d = _days(st, en)
                if 80 <= d <= 100:
                    q.setdefault((st, en), v)
                elif 350 <= d <= 380:
                    y.setdefault((st, en), v)
    # 4분기는 따로 공시되지 않는 경우가 많아 연간 − (1~3분기) 로 만든다
    for (ys, ye), yv in y.items():
        inside = sorted((k for k in q if k[0] >= ys and k[1] <= ye), key=lambda k: k[1])
        if any(k[1] == ye for k in inside) or len(inside) != 3:
            continue
        q4s = inside[-1][1]
        q[(q4s, ye)] = yv - sum(q[k] for k in inside)
    return q, y


def sec_one(t):
    cik = sec_map().get(t.upper().replace(".", "-"))
    if not cik:
        return None
    r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
                     headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}, timeout=40)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    facts = (r.json().get("facts") or {}).get("us-gaap") or {}
    if not facts:
        return None
    S = {k: _series(facts, v, (lambda u: "/shares" in u) if k == "eps" else (lambda u: u == "USD"))
         for k, v in CONCEPTS.items()}
    qd = {}
    for k, (q, _) in S.items():
        for (st, en), v in q.items():
            qd.setdefault(en[:7], {})[k] = v
    yd = {}
    for k, (_, y) in S.items():
        for (st, en), v in y.items():
            yd.setdefault(en[:7], {})[k] = v
    qk = sorted(qd)[-12:]
    yk = sorted(yd)[-6:]
    rd = lambda v, n: round(v, n) if isinstance(v, (int, float)) else None
    return {"q": [[k, rd(qd[k].get("eps"), 3), qd[k].get("rev"), qd[k].get("ni")] for k in qk],
            "y": [[k[:4], rd(yd[k].get("eps"), 3), yd[k].get("rev"), yd[k].get("ni"), None, None] for k in yk]}


def run_us(tickers, prev):
    out, st = dict(prev), dict(new=0, fail=0, keep=0, skipped=0)
    todo = [t for t in tickers if stale(prev.get(t))]
    st["keep"] = len(tickers) - len(todo)
    try:
        sess, crumb = yahoo_session()
    except Exception as e:
        st["err"] = f"{type(e).__name__}: {e}"[:150]
        return out, st
    for i, t in enumerate(todo):
        if over():
            st["skipped"] = len(todo) - i
            break
        rec = None
        for k in range(2):
            try:
                rec = us_one(sess, crumb, t)
                break
            except Exception as e:
                note("us", e)
                time.sleep(2)
        if rec:
            rec["at"] = TODAY
            out[t] = rec
            st["new"] += 1
        else:
            st["fail"] += 1
            if st["new"] == 0 and st["fail"] >= 40:
                st["abort"] = "처음 40종목 연속 실패"
                break
        time.sleep(0.15)
        if i and i % 200 == 0:
            print(f"  [us] {i}/{len(todo)} …", flush=True)
    return out, st


# ═════════════════════════ 국내 ═════════════════════════
def _period(h):
    """'2025/06', '2025/06(E)', '2025.06' → ('2025-06', 추정여부)"""
    m = re.search(r"(\d{4})[./](\d{2})", h or "")
    if not m:
        return None, False
    return f"{m.group(1)}-{m.group(2)}", ("(E)" in h or "(P)" in h or "추정" in h)


ROWS = {"eps": ("EPS",), "rev": ("매출액", "영업수익", "순영업수익", "보험료수익", "이자수익"),
        "op": ("영업이익",), "ni": ("지배주주순이익", "당기순이익(지배)", "지배주주지분순이익", "당기순이익"),
        "roe": ("ROE",), "debt": ("부채비율",), "sh": ("발행주식수",), "eq": ("지배주주지분", "자본총계")}


def _parse_table(tbl):
    """재무요약 표 → {항목: {기간: 값}}, 실적 기간 목록"""
    heads = []
    for tr in tbl.select("thead tr"):
        ths = tr.find_all("th")
        cand = [_period(th.get_text(" ", strip=True)) for th in ths]
        if sum(1 for p, _ in cand if p) >= 2:
            heads = cand
    if not heads:
        return None, []
    cols = [(p, e) for p, e in heads if p]
    data, pri = {}, {}
    for tr in tbl.select("tbody tr"):
        th = tr.find("th")
        if not th:
            continue
        lab = re.sub(r"\s+", "", th.get_text(" ", strip=True))
        tds = tr.find_all("td")
        if len(tds) < len(cols):
            continue
        tds = tds[-len(cols):]
        for key, names in ROWS.items():
            hit = next((i for i, n in enumerate(names) if lab.startswith(n)), None)
            if hit is None or (key == "ni" and names[hit] == "당기순이익" and "지배" not in lab and lab != "당기순이익"):
                continue
            if key in pri and pri[key] <= hit:
                continue
            pri[key] = hit
            data[key] = {p: fnum(td.get_text(strip=True)) for (p, e), td in zip(cols, tds) if not e}
            if key == "eps":                    # 추정치(E) 열의 EPS 는 따로 — 12개월 선행 EPS 재료
                data["eps_e"] = {p: fnum(td.get_text(strip=True)) for (p, e), td in zip(cols, tds) if e}
    return data, [p for p, e in cols if not e]


def kr_wise(sess, code):
    from bs4 import BeautifulSoup
    ref = "https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd=" + code
    page = sess.get(ref, timeout=20, headers={"Referer": "https://finance.naver.com/"}).text
    enc = re.search(r"encparam\s*:\s*['\"]([^'\"]+)", page)
    cid = re.search(r"\bid\s*:\s*['\"]([^'\"]+)", page)
    if not enc:
        raise ValueError("encparam 없음")
    res = {}
    for freq in ("Q", "Y"):
        params = {"cmp_cd": code, "fin_typ": "0", "freq_typ": freq, "encparam": enc.group(1)}
        if cid:
            params["id"] = cid.group(1)
        r = sess.get("https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx", params=params,
                     timeout=20, headers={"Referer": ref, "X-Requested-With": "XMLHttpRequest"})
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "lxml")
        best = None
        for tbl in soup.select("table"):
            d, ps = _parse_table(tbl)
            if d and ps and (best is None or len(d) > len(best[0])):
                best = (d, ps)
        if best:
            res[freq] = best
        time.sleep(0.1)
    return res


def kr_fnguide(sess, code):
    from bs4 import BeautifulSoup
    r = sess.get("https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp",
                 params={"pGB": "1", "gicode": "A" + code, "cID": "", "MenuYn": "Y", "ReportGB": "", "NewMenuID": "101", "stkGb": "701"},
                 timeout=20)
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "lxml")
    res = {}
    for freq, sel in (("Q", "#highlight_D_Q table"), ("Y", "#highlight_D_Y table")):
        tbl = soup.select_one(sel)
        if tbl:
            d, ps = _parse_table(tbl)
            if d and ps:
                res[freq] = (d, ps)
    return res


def kr_rec(res):
    rec = {}
    if "Q" in res:
        d, ps = res["Q"]
        rec["q"] = [[p, d.get("eps", {}).get(p), d.get("rev", {}).get(p), d.get("ni", {}).get(p), d.get("op", {}).get(p)] for p in ps]
    if "Y" in res:
        d, ps = res["Y"]
        rec["y"] = [[p[:4], d.get("eps", {}).get(p), d.get("rev", {}).get(p), d.get("ni", {}).get(p),
                     d.get("eq", {}).get(p), d.get("debt", {}).get(p), d.get("roe", {}).get(p)] for p in ps]
        rec["sh"] = [[p, d.get("sh", {}).get(p)] for p in ps if d.get("sh", {}).get(p)]
        rec["fe"] = [[p, v] for p, v in sorted((d.get("eps_e") or {}).items()) if v is not None]   # 연간 추정 EPS(결산월 기준)
        roes = [x for x in (d.get("roe") or {}).values() if x is not None]
        if roes:
            rec["roe"] = list(d["roe"].values())[-1] if list(d["roe"].values())[-1] is not None else roes[-1]
    # 분기 표에도 주식 수가 있으면 최신 값으로 보강
    if "Q" in res:
        d, ps = res["Q"]
        for p in ps:
            v = d.get("sh", {}).get(p)
            if v:
                rec.setdefault("sh", [])
                if p not in [x[0] for x in rec["sh"]]:
                    rec["sh"].append([p, v])
        if rec.get("sh"):
            rec["sh"].sort()
    return rec if (rec.get("q") or rec.get("y")) else None


def kr_flows():
    """기관·외국인 최근 60거래일 순매수 금액(억원) — pykrx(KRX) 로 시장 전체를 한 번에."""
    out, info = {}, {}
    try:
        from pykrx import stock
    except Exception as e:
        info["err"] = f"pykrx 없음: {e}"
        return out, info
    try:
        end = stock.get_nearest_business_day_in_a_week(NOW.strftime("%Y%m%d"))
        days = stock.get_previous_business_days(fromdate=(NOW - timedelta(days=120)).strftime("%Y%m%d"), todate=end)
        start = days[-60].strftime("%Y%m%d") if len(days) >= 60 else days[0].strftime("%Y%m%d")
        info["range"] = f"{start}~{end}"
        for who, key in (("기관합계", "inst60"), ("외국인", "frgn60")):
            for mkt in ("KOSPI", "KOSDAQ"):
                try:
                    df = stock.get_market_net_purchases_of_equities(start, end, mkt, who)
                    col = [c for c in df.columns if "순매수거래대금" in c]
                    if not col:
                        continue
                    for t, v in df[col[0]].items():
                        out.setdefault(str(t).zfill(6), {})[key] = round(float(v) / 1e8, 1)
                except Exception as e:
                    note("krx", e)
                time.sleep(0.5)
        info["n"] = len(out)
    except Exception as e:
        info["err"] = f"{type(e).__name__}: {e}"[:150]
    return out, info


def _int(x):
    v = fnum(x)
    return v if v is not None else 0.0


def kr_flow_one(sess, code):
    """네이버 증권 — 최근 60거래일 기관·외국인 순매수(수량×종가, 억원). 모바일 API → 데스크톱 표 순으로 시도."""
    try:
        r = sess.get(f"https://m.stock.naver.com/api/stock/{code}/trend", params={"pageSize": 60}, timeout=15,
                     headers={"Referer": "https://m.stock.naver.com/"})
        arr = r.json()
        if isinstance(arr, dict):
            arr = arr.get("result") or arr.get("trends") or []
        if arr:
            k0 = arr[0].keys()
            ko = next((k for k in k0 if "organ" in k.lower() and "pure" in k.lower()), None)
            kf = next((k for k in k0 if "foreign" in k.lower() and "pure" in k.lower()), None)
            kc = next((k for k in k0 if "close" in k.lower()), None)
            if ko and kf and kc:
                inst = sum(_int(a.get(ko)) * _int(a.get(kc)) for a in arr[:60]) / 1e8
                frgn = sum(_int(a.get(kf)) * _int(a.get(kc)) for a in arr[:60]) / 1e8
                return {"inst60": round(inst, 1), "frgn60": round(frgn, 1)}, "m"
            note("flow_m", ValueError("키 없음 " + ",".join(list(k0)[:8])))
    except Exception as e:
        note("flow_m", e)
    from bs4 import BeautifulSoup
    inst = frgn = 0.0
    rows = 0
    for page in (1, 2, 3):
        r = sess.get("https://finance.naver.com/item/frgn.naver", params={"code": code, "page": page}, timeout=15,
                     headers={"Referer": "https://finance.naver.com/"})
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "lxml")
        tbls = soup.select("table.type2")
        if len(tbls) < 2:
            break
        for tr in tbls[1].select("tr"):
            td = [x.get_text(strip=True) for x in tr.find_all("td")]
            if len(td) < 7 or not re.match(r"\d{4}\.\d{2}\.\d{2}", td[0]):
                continue
            px = _int(td[1])
            inst += _int(td[5]) * px
            frgn += _int(td[6]) * px
            rows += 1
            if rows >= 60:
                break
        if rows >= 60:
            break
        time.sleep(0.05)
    if not rows:
        raise ValueError("수급 표 없음")
    return {"inst60": round(inst / 1e8, 1), "frgn60": round(frgn / 1e8, 1)}, "d"


def run_kr(tickers, prev):
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    out, st = dict(prev), dict(new=0, fail=0, keep=0, skipped=0, wr=0, fn=0)
    todo = [t for t in tickers if stale(prev.get(t))]
    st["keep"] = len(tickers) - len(todo)
    flow_reserve = 9 * 60                                    # 수급 수집용 시간을 남겨 둔다
    for i, t in enumerate(todo):
        if time.time() > DEADLINE - flow_reserve:
            st["skipped"] = len(todo) - i
            break
        rec = None
        for f, k in ((kr_wise, "wr"), (kr_fnguide, "fn")):
            try:
                rec = kr_rec(f(sess, t))
                if not rec:
                    note(k, ValueError("표 없음"))
            except Exception as e:
                note(k, e)
                rec = None
            if rec:
                rec["src"] = "WiseReport" if k == "wr" else "FnGuide"
                st[k] += 1
                break
        if rec:
            rec["at"] = TODAY
            out[t] = rec
            st["new"] += 1
        else:
            st["fail"] += 1
            if st["new"] == 0 and st["fail"] >= 40:
                st["abort"] = "처음 40종목 연속 실패"
                break
        time.sleep(0.12)
        if i and i % 200 == 0:
            print(f"  [kr] {i}/{len(todo)} … 성공 {st['new']}", flush=True)
    flows, finfo = kr_flows()
    if len(flows) < len(tickers) // 2:                      # KRX(pykrx) 가 막히면 네이버에서 종목별로
        fs = dict(m=0, d=0, fail=0)
        for t in tickers:
            if over():
                fs["skipped"] = fs.get("skipped", 0) + 1
                continue
            try:
                f, how = kr_flow_one(sess, t)
                flows[t] = f
                fs[how] += 1
            except Exception as e:
                note("flow", e)
                fs["fail"] += 1
                if fs["m"] + fs["d"] == 0 and fs["fail"] >= 30:
                    fs["abort"] = "처음 30종목 연속 실패"
                    break
            time.sleep(0.08)
        finfo["naver"] = fs
    st["flows"] = finfo
    for t, f in flows.items():
        if t in out:
            out[t].update(f)
        elif t in tickers:
            out[t] = dict(f)
    return out, st


def _months(a, b):
    """'YYYY-MM' b 가 a 보다 몇 개월 뒤인가."""
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def fwd12_eps(fe, today=None):
    """연간 추정 EPS 들로 12개월 선행 EPS — FnGuide 방식(남은 개월 수로 FY1·FY2 가중).
    FY1 = 결산월이 이번 달 이후인 첫 추정 연도, FY2 = 그다음 해."""
    cur = (today or NOW).strftime("%Y-%m")
    fut = [(p, v) for p, v in sorted(fe or []) if v is not None and p >= cur]
    if not fut:
        return None, None
    (p1, e1) = fut[0]
    left = min(12, max(0, _months(cur, p1)))             # FY1 결산까지 남은 개월 수
    if len(fut) >= 2 and _months(p1, fut[1][0]) == 12:
        e2 = fut[1][1]
        return e1 * left / 12 + e2 * (12 - left) / 12, f"{p1[:4]}E·{fut[1][0][:4]}E"
    if left >= 9:                                         # FY2 추정이 없으면 FY1 이 12개월의 대부분일 때만 쓴다
        return e1, f"{p1[:4]}E"
    return None, None


def write_consensus_kr(out):
    """canslim 국내 기록 → output/consensus_kr.json. screener_full.py 가 이 파일을 읽어
    오늘 종가로 선행 PER(종가 ÷ 12M 선행 EPS)과 후행 PER·PBR 을 계산한다."""
    m, st = {}, dict(fwd=0, eps=0, bps=0)
    for t, r in out.items():
        rec = {}
        fe, basis = fwd12_eps(r.get("fe"))
        if fe is not None:
            rec["fwd_eps"] = round(fe, 1); rec["fwd_basis"] = basis; st["fwd"] += 1
        q = [x for x in (r.get("q") or []) if x[1] is not None]
        if len(q) >= 4 and _months(q[-4][0], q[-1][0]) == 9:   # 연속된 최근 4개 분기 EPS 합 = 후행 12개월 EPS
            rec["eps"] = round(sum(x[1] for x in q[-4:]), 1); st["eps"] += 1
        y = r.get("y") or []
        sh = [x[1] for x in (r.get("sh") or []) if x[1]]
        if y and (y[-1][4] or 0) > 0 and sh:                            # 지배주주지분(억원) ÷ 최근 발행주식수
            rec["bps"] = round(y[-1][4] * 1e8 / sh[-1], 1); st["bps"] += 1
        if rec:
            m[t] = rec
    p = os.path.join(OUT_DIR, "consensus_kr.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"generated_at": NOW.isoformat(timespec="seconds"),
                   "src": "WiseReport·FnGuide 재무요약(연간 추정치) — canslim_data.py",
                   "stats": st, "map": m}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[kr] 컨센서스 {len(m)}종목 · 선행EPS {st['fwd']} · 후행EPS {st['eps']} · BPS {st['bps']} → {p}", flush=True)


def main():
    mks = [m.strip() for m in os.getenv("MARKETS", "us,kr").split(",") if m.strip()]
    os.makedirs(OUT_DIR, exist_ok=True)
    share = TIME_BUDGET / max(1, len(mks))
    global DEADLINE
    for j, mk in enumerate(mks):
        DEADLINE = T0 + share * (j + 1) - (60 if mk == "kr" else 0)   # 시장별 시간 몫 (국내는 수급 수집용 1분 남김)
        tk = universe(mk)
        if not tk:
            continue
        prev = load_prev(mk)
        out, st = (run_kr if mk == "kr" else run_us)(tk, prev)
        keep = set(tk)
        out = {t: v for t, v in out.items() if t in keep}
        n_ok = sum(1 for v in out.values() if v.get("q") or v.get("y"))
        st["err"] = ERR.copy(); ERR.clear()
        p = os.path.join(OUT_DIR, f"canslim_{mk}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"asof": TODAY, "n": n_ok, "diag": st, "p": out}, f, ensure_ascii=False, separators=(",", ":"))
        print(f"[{mk}] 재무 {n_ok}/{len(tk)}종목 · {json.dumps(st, ensure_ascii=False)[:600]} · {os.path.getsize(p):,} bytes", flush=True)
        if mk == "kr":
            write_consensus_kr(out)


if __name__ == "__main__":
    sys.exit(main())
