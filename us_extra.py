#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
미국 실적 일정 · 매크로 지표 · 미국 전 종목 목록 · 소식 피드 — 대시보드 '실적'·'매크로'·'미국 전 종목'·'피드' 탭

  실적(earn): 나스닥 실적 캘린더(최근 4일 ~ 앞으로 10일) — 시간(장 전·장 후), 예상 EPS·추정 수, 작년 EPS, 실제 EPS·서프라이즈(발표 뒤), 시총
  매크로(macro): 미 10년·2년 금리, 10년−2년, 10년 실질금리, 기준금리 상단, WTI, 달러지수, 하이일드 스프레드 — FRED 공개 CSV, 막히면 야후
  미국 전 종목(ussc): 나스닥 스크리너 전체(가격·등락·시총·거래량·섹터·업종·상장연도)
  피드(feed): 미국 거래정지(나스닥 트레이더 RSS) · 주요 종목 뉴스(야후 RSS) · 국내 주요 공시(OpenDART: 주요사항·공정공시·거래소 공시)

결과: output/market_index.json 의 "earn" · "macro" · "ussc" · "feed"
"""
import json, os, re, time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
ETZ = timezone(timedelta(hours=-5))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
NH = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
DART_KEY = os.getenv("DART_API_KEY", "").strip()
LOG = {}


def num(v):
    try:
        s = str(v).replace("$", "").replace(",", "").replace("%", "").strip()
        if s in ("", "N/A", "--", "NA"):
            return None
        neg = s.startswith("(") and s.endswith(")")
        x = float(s.strip("()"))
        return -x if neg else x
    except Exception:
        return None


def nasdaq(path, params=None, tries=3):
    for k in range(tries):
        try:
            r = requests.get("https://api.nasdaq.com/api/" + path, params=params or {}, headers=NH, timeout=25)
            return r.json()
        except Exception:
            time.sleep(1.5 + k)
    return {}


# ───────── 실적 ─────────
def earnings():
    today = datetime.now(ETZ).date()
    days = {}
    for off in range(-4, 11):
        d = today + timedelta(days=off)
        if d.weekday() >= 5:
            continue
        j = nasdaq("calendar/earnings", {"date": d.isoformat()})
        rows = ((j.get("data") or {}).get("rows")) or []
        if off == -1 and rows:
            print("실적 응답 예시(전일):", rows[0])
        out = []
        for r in rows:
            t = (r.get("symbol") or "").strip()
            if not t:
                continue
            tm = (r.get("time") or "").lower()
            out.append({"t": t, "n": (r.get("name") or "").strip()[:40], "tm": "pre" if "pre" in tm else "after" if "after" in tm else "",
                        "mc": num(r.get("marketCap")), "fq": r.get("fiscalQuarterEnding"), "est": num(r.get("epsForecast")), "ne": num(r.get("noOfEsts")),
                        "ly": num(r.get("lastYearEPS")), "lyd": r.get("lastYearRptDt"), "eps": num(r.get("eps")), "sur": num(r.get("surprise"))})
        out.sort(key=lambda x: -(x["mc"] or 0))
        days[d.isoformat()] = out[:220]
        time.sleep(0.4)
    LOG["earn"] = {k: len(v) for k, v in days.items()}
    E = {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "days": days}
    try:
        earn_revenue(E)
    except Exception as e:
        print("매출 보강 실패", type(e).__name__, e)
    return E


def earn_revenue(E):
    """시총 $2B 이상 종목: 다음 분기 매출 예상(야후 calendar)·발표 뒤 실제 매출(분기 손익계산서). 예상은 발표 전 캐시에 보관."""
    try:
        import yfinance as yf
    except Exception:
        print("yfinance 없음 — 매출 건너뜀")
        return
    cp = os.path.join(OUT, "earn_cache.json")
    try:
        cache = json.load(open(cp, encoding="utf-8"))
    except Exception:
        cache = {}
    rows = [r for v in E["days"].values() for r in v if (r.get("mc") or 0) >= 2e9]
    done = 0
    for r in rows[:int(os.getenv("EARN_REV_N", "160"))]:
        key = f"{r['t']}|{r.get('fq')}"
        c = cache.setdefault(key, {})
        try:
            tk = yf.Ticker(r["t"])
            if r.get("eps") is None and "rev_est" not in c:
                cal = tk.calendar or {}
                if isinstance(cal, dict) and cal.get("Revenue Average"):
                    c["rev_est"] = float(cal["Revenue Average"]); c["rev_lo"] = float(cal.get("Revenue Low") or 0) or None; c["rev_hi"] = float(cal.get("Revenue High") or 0) or None
            if r.get("eps") is not None and "rev" not in c:
                q = tk.quarterly_income_stmt
                if q is not None and "Total Revenue" in q.index and len(q.columns):
                    c["rev"] = float(q.loc["Total Revenue"].iloc[0]); c["rev_q"] = str(q.columns[0])[:10]
            done += 1
        except Exception as e:
            c["err"] = type(e).__name__
        for k in ("rev_est", "rev_lo", "rev_hi", "rev"):
            v = c.get(k)
            if isinstance(v, (int, float)) and v == v and abs(v) != float("inf"):
                r[k] = round(v)
            else:
                c.pop(k, None)
        if r.get("rev") and r.get("rev_est"):
            r["rsur"] = round((r["rev"] / r["rev_est"] - 1) * 100, 2)
        time.sleep(0.25)
    if len(cache) > 3000:
        cache = dict(list(cache.items())[-3000:])
    json.dump(cache, open(cp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    LOG["earn_rev"] = f"{done}건 조회 · 예상 {sum(1 for r in rows if r.get('rev_est'))} · 실제 {sum(1 for r in rows if r.get('rev'))}"


# ───────── 매크로 ─────────
FRED = [("DGS10", "미 10년물 금리", "%"), ("DGS2", "미 2년물 금리", "%"), ("T10Y2Y", "10년−2년 스프레드", "%p"), ("DFII10", "10년 실질금리", "%"),
        ("DFEDTARU", "기준금리 상단", "%"), ("DCOILWTICO", "WTI 유가", "$"), ("DTWEXBGS", "달러지수(광의)", ""), ("BAMLH0A0HYM2", "하이일드 스프레드", "%p")]
YAH = {"DGS10": "^TNX", "DGS2": "2YY=F", "DCOILWTICO": "CL=F", "DTWEXBGS": "DX-Y.NYB", "DFEDTARU": "^IRX", "BAMLH0A0HYM2": "HYG"}
YNAME = {"DTWEXBGS": "달러지수(DXY)", "DFEDTARU": "미 3개월물 금리", "BAMLH0A0HYM2": "하이일드 채권 ETF(HYG)"}


FRED_OK = [True]


def fred(sid):
    for k in range(2 if FRED_OK[0] else 0):
        try:
            r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": sid, "cosd": (date.today() - timedelta(days=400)).isoformat()},
                             headers={"User-Agent": UA}, timeout=20)
            if r.status_code == 200 and "," in r.text:
                pts = []
                for line in r.text.strip().splitlines()[1:]:
                    d, v = line.split(",")[:2]
                    x = num(v)
                    if x is not None:
                        pts.append([d, x])
                if pts:
                    return pts, "FRED"
        except Exception as e:
            print("FRED", sid, type(e).__name__)
            FRED_OK[0] = False                                   # 한 번 막히면 나머지는 바로 야후로
            break
        time.sleep(1 + k)
    ys = YAH.get(sid)
    if ys:
        try:
            j = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ys}", params={"range": "1y", "interval": "1d"}, headers={"User-Agent": UA}, timeout=20).json()
            R = j["chart"]["result"][0]
            ts, cl = R["timestamp"], R["indicators"]["quote"][0]["close"]
            pts = [[datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), round(c / (10 if ys in ("^TNX", "^IRX") and c > 20 else 1), 3)] for t, c in zip(ts, cl) if c is not None]
            return pts, "야후"
        except Exception as e:
            print("야후", ys, type(e).__name__)
    return None, None


def treasury():
    """미 재무부 일별 국채 금리(명목·실질) 공개 CSV — 올해·작년"""
    res = {}
    y0 = date.today().year
    for typ, cols in (("daily_treasury_yield_curve", {"3 Mo": "DGS3MO", "2 Yr": "DGS2", "10 Yr": "DGS10"}), ("daily_treasury_real_yield_curve", {"10 YR": "DFII10"})):
        rows = []
        for y in (y0 - 1, y0):
            try:
                r = requests.get(f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{y}/all",
                                 params={"type": typ, "field_tdr_date_value": y, "page": "", "_format": "csv"}, headers={"User-Agent": UA}, timeout=25)
                lines = r.text.strip().splitlines()
                if r.status_code != 200 or len(lines) < 2 or "Date" not in lines[0]:
                    print("재무부", typ, y, r.status_code)
                    continue
                hd = [h.strip().strip('"') for h in lines[0].split(",")]
                for ln in lines[1:]:
                    v = [x.strip().strip('"') for x in ln.split(",")]
                    rows.append(dict(zip(hd, v)))
            except Exception as e:
                print("재무부", typ, y, type(e).__name__)
        for col, sid in cols.items():
            pts = []
            for r in rows:
                key = next((k for k in r if k.upper() == col.upper()), None)
                x = num(r.get(key)) if key else None
                try:
                    d = datetime.strptime(r.get("Date", ""), "%m/%d/%Y").date()
                except ValueError:
                    continue
                if x is not None:
                    pts.append([d.isoformat(), x])
            pts.sort()
            if pts:
                res[sid] = pts
    return res


def te_rate():
    """Trading Economics — 미국 기준금리(최근 결정들)"""
    try:
        r = requests.get("https://tradingeconomics.com/united-states/interest-rate", headers={"User-Agent": UA, "Accept-Language": "en-US"}, timeout=20)
        i = r.text.find('id="calendar"')
        seg = r.text[i: i + 8000] if i > 0 else ""
        pts = []
        for body in re.findall(r"<tr[^>]*data-category[^>]*>(.*?)</tr>", seg, re.S):
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)]
            if len(cells) >= 5 and num(cells[4]) is not None:
                pts.append([cells[0], num(cells[4])])
        m = re.search(r"benchmark interest rate in the United States (?:was|to) (?:last recorded at )?([\d.]+) percent", r.text, re.I)
        if not pts and m:
            pts = [[date.today().isoformat(), float(m.group(1))]]
        return sorted(pts)
    except Exception as e:
        print("TE 기준금리", type(e).__name__)
        return []


def macro():
    out = {}
    TR = treasury()
    LOG["treasury"] = {k: len(v) for k, v in TR.items()}
    for sid, nm in (("DGS10", "미 10년물 금리"), ("DGS2", "미 2년물 금리"), ("DGS3MO", "미 3개월물 금리"), ("DFII10", "10년 실질금리(TIPS)")):
        if TR.get(sid):
            P = TR[sid][-260:]
            out[sid] = {"n": nm, "u": "%", "src": "미 재무부", "d": [p[0][2:].replace("-", "") for p in P], "v": [p[1] for p in P]}
    fr = te_rate()
    if fr:
        out["DFEDTARU"] = {"n": "기준금리(상단)", "u": "%", "src": "Trading Economics", "d": [p[0][2:].replace("-", "") for p in fr], "v": [p[1] for p in fr]}
    for sid, nm, u in FRED:
        if sid in out:
            continue
        pts, src = fred(sid)
        if pts:
            yn = YNAME.get(sid) if src == "야후" else None
            out[sid] = {"n": yn or nm, "u": ("$" if sid == "BAMLH0A0HYM2" else u) if yn else u, "src": src, "d": [p[0][2:].replace("-", "") for p in pts[-260:]], "v": [p[1] for p in pts[-260:]]}
        time.sleep(0.3)
    if "T10Y2Y" not in out and "DGS10" in out and "DGS2" in out:            # 10년−2년은 직접 계산
        a, b = out["DGS10"], out["DGS2"]
        m2 = dict(zip(b["d"], b["v"]))
        dd = [d for d in a["d"] if d in m2]
        out["T10Y2Y"] = {"n": "10년−2년 스프레드", "u": "%p", "src": "계산", "d": dd, "v": [round(a["v"][a["d"].index(d)] - m2[d], 3) for d in dd]}
    LOG["macro"] = {k: v["src"] for k, v in out.items()}
    return {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "s": out}


# ───────── 미국 전 종목 ─────────
def us_all():
    j = nasdaq("screener/stocks", {"tableonly": "true", "limit": "10000", "download": "true"})
    rows = ((j.get("data") or {}).get("rows")) or []
    print("나스닥 스크리너:", len(rows), rows[0] if rows else str(j)[:200])
    out = []
    for r in rows:
        t = (r.get("symbol") or "").strip()
        if not t or "^" in t or "/" in t:
            continue
        nm = (r.get("name") or "").strip()
        if re.search(r"\b(Warrants?|Units?|Rights?|Preferred|Notes due|Debentures|Depositary Shares, each representing)\b", nm, re.I) and "ADS" not in nm:
            continue                                            # 워런트·유닛·우선주·채권 제외(보통주·ADR만)
        nm = re.sub(r"\s+(Common Stock|Class [A-Z] Common Stock|Ordinary Shares|American Depositary Shares).*$", "", nm, flags=re.I)
        spac = bool(re.search(r"acquisition corp|blank check|SPAC", nm, re.I))
        mc = num(r.get("marketCap"))
        out.append([t, nm[:36], num(r.get("lastsale")), round(num(r.get("pctchange")) or 0, 2), int(mc) if mc else None, int(num(r.get("volume")) or 0),
                    (r.get("sector") or "").strip(), (r.get("industry") or "").strip()[:28], None, "", 1 if spac else 0])
    # 나스닥 스크리너의 lastsale 은 갱신이 늦거나(주말 뒤 금요일 장중 값이 남는 등) 장중 값일 때가 있다 → 야후 일봉 종가로 덮어쓴다
    yc = yahoo_closes([r[0] for r in out])
    fixed, cd = 0, {}
    for r in out:
        y = yc.get(r[0])
        if y:
            c, pv, d, v = y
            if r[4] and r[2]:
                r[4] = int(r[4] * c / r[2])                       # 시가총액도 새 종가 비율로
            r[2], r[3] = round(c, 4), round((c / pv - 1) * 100, 2) if pv else r[3]
            if v:
                r[5] = int(v)
            fixed += 1
            cd[d] = cd.get(d, 0) + 1
        r.append(y[2] if y else None)                          # 11: 종가 기준일(없으면 나스닥 값 그대로)
    close_d = max(cd, key=cd.get) if cd else None
    print(f"야후 종가로 교정 {fixed}/{len(out)} · 기준일 {cd}", flush=True)
    LOG["ussc"] = len(out)
    LOG["ussc_fixed"] = fixed
    return {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "close_d": close_d, "fixed": fixed,
            "cols": ["t", "n", "px", "chg", "mc", "vol", "sec", "ind", "ipo", "ctry", "spac", "d"], "rows": out}


def yahoo_closes(tickers, budget=420):
    """야후 일봉으로 마지막 종가·전일 종가·날짜·거래량. {티커: (종가, 전일, 'YYYY-MM-DD', 거래량)}"""
    try:
        import yfinance as yf
        import pandas as pd
    except Exception as e:
        print("yfinance 없음:", e)
        return {}
    t0, res, CH = time.time(), {}, 400
    ymap = {t: t.replace(".", "-") for t in tickers}
    inv = {v: k for k, v in ymap.items()}
    ys = list(inv)
    for k in range(0, len(ys), CH):
        if time.time() - t0 > budget:
            print("야후 종가 시간 초과 — 나머지는 나스닥 값", flush=True)
            break
        part = ys[k:k + CH]
        try:
            df = yf.download(part, period="10d", interval="1d", auto_adjust=False, progress=False, threads=True, group_by="ticker")
        except Exception as e:
            print("야후 묶음 실패", k, type(e).__name__)
            continue
        for y in part:
            try:
                sub = df[y] if isinstance(df.columns, pd.MultiIndex) and y in df.columns.get_level_values(0) else df
                cl = sub["Close"].dropna()
                if len(cl) < 2:
                    continue
                vol = sub["Volume"].reindex(cl.index)
                res[inv[y]] = (float(cl.iloc[-1]), float(cl.iloc[-2]), cl.index[-1].strftime("%Y-%m-%d"),
                               float(vol.iloc[-1]) if vol.notna().iloc[-1] else None)
            except Exception:
                continue
    print(f"야후 종가 {len(res)}/{len(tickers)} · {time.time() - t0:.0f}초", flush=True)
    return res


# ───────── 피드 ─────────
def rss(url, n=40):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        root = ET.fromstring(r.content)
        items = []
        for it in root.iter("item"):
            g = lambda tag: (it.findtext(tag) or "").strip()
            items.append({"title": g("title"), "link": g("link"), "date": g("pubDate"), "desc": re.sub(r"<[^>]+>", " ", g("description"))[:300], "_el": it})
            if len(items) >= n:
                break
        return items
    except Exception as e:
        print("RSS", url[:60], type(e).__name__)
        return []


def pdate(s):
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%m/%d/%Y %H:%M:%S"):
        try:
            d = datetime.strptime(s, fmt)
            if not d.tzinfo:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(KST).strftime("%Y-%m-%d %H:%M")
        except Exception:
            continue
    return ""


def feed(us_tickers):
    items = []
    # 1) 미국 거래정지
    for it in rss("https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts", 60):
        el = it["_el"]
        vals = {c.tag.split("}")[-1]: (c.text or "").strip() for c in el}
        t = vals.get("IssueSymbol") or ""
        code = vals.get("ReasonCode") or ""
        d = (vals.get("HaltDate") or "") + " " + (vals.get("HaltTime") or "")
        rs = {"T1": "중요 뉴스 대기", "T2": "뉴스 발표", "T12": "추가 정보 요청", "H10": "SEC 거래정지", "LUDP": "변동성 거래정지(LULD)", "LUDS": "변동성 거래정지(LULD)", "M": "변동성 거래정지", "H4": "상장 요건 미달", "T3": "뉴스·재개 예정"}.get(code, code)
        try:
            kd = datetime.strptime(d.strip(), "%m/%d/%Y %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=-5))).astimezone(KST).strftime("%Y-%m-%d %H:%M")
        except Exception:
            kd = ""
        items.append({"k": "halt", "mk": "us", "t": t, "d": kd, "imp": 3 if code in ("T1", "H10") else 2, "title": f"{vals.get('IssueName') or t} · 거래정지 — {rs}",
                      "x": ("재개 " + vals.get("ResumptionTradeTime", "")) if vals.get("ResumptionTradeTime") else "", "url": "https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts"})
    LOG["halt"] = sum(1 for i in items if i["k"] == "halt")
    # 2) 주요 종목 뉴스(야후 RSS)
    def one(t):
        return t, rss(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US", 6)
    with ThreadPoolExecutor(max_workers=6) as ex:
        for t, L in ex.map(one, us_tickers):
            for it in L:
                items.append({"k": "news", "mk": "us", "t": t, "d": pdate(it["date"]), "imp": 1, "title": it["title"], "x": it["desc"][:200], "url": it["link"]})
    LOG["news"] = sum(1 for i in items if i["k"] == "news")
    # 3) 국내 주요 공시(OpenDART)
    if DART_KEY:
        today = datetime.now(KST).date()
        for ty, lab, imp in (("B", "주요사항보고", 2), ("I", "거래소 공시", 1)):
            try:
                j = requests.get("https://opendart.fss.or.kr/api/list.json", timeout=20,
                                 params={"crtfc_key": DART_KEY, "bgn_de": (today - timedelta(days=2)).strftime("%Y%m%d"), "end_de": today.strftime("%Y%m%d"),
                                         "pblntf_ty": ty, "page_count": 100, "corp_cls": "Y"}).json()
                j2 = requests.get("https://opendart.fss.or.kr/api/list.json", timeout=20,
                                  params={"crtfc_key": DART_KEY, "bgn_de": (today - timedelta(days=2)).strftime("%Y%m%d"), "end_de": today.strftime("%Y%m%d"),
                                          "pblntf_ty": ty, "page_count": 100, "corp_cls": "K"}).json()
                for x in (j.get("list") or []) + (j2.get("list") or []):
                    nm = x.get("report_nm", "")
                    if ty == "I" and not re.search(r"잠정|실적|공급계약|수주|유상증자|무상증자|자기주식|합병|분할|최대주주|소송|횡령|배당|기업설명회|투자판단|매매거래정지|상장폐지|불성실", nm):
                        continue
                    hi = 3 if re.search(r"횡령|배임|상장폐지|매매거래정지|감자|불성실", nm) else 2 if re.search(r"잠정|실적|공급계약|수주|유상증자|자기주식|합병|분할", nm) else imp
                    rd = x.get("rcept_dt", "")
                    items.append({"k": "dart", "mk": "kr", "t": (x.get("stock_code") or "").strip(), "d": f"{rd[:4]}-{rd[4:6]}-{rd[6:]}" if len(rd) == 8 else "",
                                  "imp": hi, "title": f"{x.get('corp_name', '')} · {nm.strip()}", "x": lab + (" · " + x.get("flr_nm", "") if x.get("flr_nm") else ""),
                                  "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + x.get("rcept_no", "")})
            except Exception as e:
                print("DART", ty, type(e).__name__)
        LOG["dart"] = sum(1 for i in items if i["k"] == "dart")
    for i in items:
        i.pop("_el", None)
    items.sort(key=lambda i: i.get("d") or "", reverse=True)
    return {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "items": items[:600]}


def telegram(items):
    """중요 소식을 텔레그램으로(저장소 Secrets 에 TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID 가 있을 때만). 보낸 것은 기록해 두 번 보내지 않음."""
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat:
        print("텔레그램 설정 없음 — 알림 건너뜀")
        return
    sp = os.path.join(OUT, "tg_sent.json")
    try:
        sent = set(json.load(open(sp, encoding="utf-8")))
    except Exception:
        sent = set()
    new = [i for i in items if i["imp"] >= int(os.getenv("TG_MIN_IMP", "3")) and (i["k"] + i["title"] + i.get("d", "")) not in sent][:15]
    n = 0
    for i in reversed(new):
        txt = f"{'🇺🇸' if i['mk'] == 'us' else '🇰🇷'} [{ {'halt': '거래정지', 'dart': '공시', 'news': '뉴스'}.get(i['k'], i['k']) }] {i['title']}\n{i.get('x', '')}\n{i.get('d', '')} KST\n{i.get('url', '')}"
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": txt[:3900], "disable_web_page_preview": True}, timeout=15)
            if r.status_code == 200:
                sent.add(i["k"] + i["title"] + i.get("d", "")); n += 1
            else:
                print("텔레그램", r.status_code, r.text[:120])
        except Exception as e:
            print("텔레그램", type(e).__name__)
        time.sleep(0.5)
    json.dump(sorted(sent)[-3000:], open(sp, "w", encoding="utf-8"), ensure_ascii=False)
    LOG["telegram"] = n


def main():
    p = os.path.join(OUT, "market_index.json")
    mi = json.load(open(p, encoding="utf-8"))
    us_t = []
    try:
        u = json.load(open(os.path.join(OUT, "universe_us.json"), encoding="utf-8"))
        us_t = [r["ticker"] for r in sorted((r for r in u["rows"] if not r.get("is_etf")), key=lambda r: -(r.get("mcap") or 0))[:40]]
    except Exception:
        pass
    for key, fn in (("earn", earnings), ("macro", macro), ("ussc", us_all), ("feed", lambda: feed(us_t))):
        try:
            v = fn()
            ok = bool(v and (v.get("days") and any(v["days"].values()) or v.get("s") or v.get("rows") or v.get("items")))
            if ok:
                mi[key] = v
            else:
                print(f"{key}: 받은 자료 없음 — 기존 값 유지")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"{key} 실패: {type(e).__name__}: {e}")
    try:
        telegram((mi.get("feed") or {}).get("items") or [])
    except Exception as e:
        print("텔레그램 실패", type(e).__name__)
    json.dump(mi, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("출처:", LOG)
    print("크기:", {k: len(json.dumps(mi.get(k) or {})) // 1024 for k in ("earn", "macro", "ussc", "feed")}, "KB")


if __name__ == "__main__":
    main()
