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
    return {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "days": days}


# ───────── 매크로 ─────────
FRED = [("DGS10", "미 10년물 금리", "%"), ("DGS2", "미 2년물 금리", "%"), ("T10Y2Y", "10년−2년 스프레드", "%p"), ("DFII10", "10년 실질금리", "%"),
        ("DFEDTARU", "기준금리 상단", "%"), ("DCOILWTICO", "WTI 유가", "$"), ("DTWEXBGS", "달러지수(광의)", ""), ("BAMLH0A0HYM2", "하이일드 스프레드", "%p")]
YAH = {"DGS10": "^TNX", "DCOILWTICO": "CL=F", "DTWEXBGS": "DX-Y.NYB"}


def fred(sid):
    for k in range(2):
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
        time.sleep(1 + k)
    ys = YAH.get(sid)
    if ys:
        try:
            j = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ys}", params={"range": "1y", "interval": "1d"}, headers={"User-Agent": UA}, timeout=20).json()
            R = j["chart"]["result"][0]
            ts, cl = R["timestamp"], R["indicators"]["quote"][0]["close"]
            pts = [[datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), round(c / (10 if ys == "^TNX" and c > 20 else 1), 3)] for t, c in zip(ts, cl) if c is not None]
            return pts, "야후"
        except Exception as e:
            print("야후", ys, type(e).__name__)
    return None, None


def macro():
    out = {}
    for sid, nm, u in FRED:
        pts, src = fred(sid)
        if pts:
            out[sid] = {"n": nm, "u": u, "src": src, "d": [p[0][2:].replace("-", "") for p in pts[-260:]], "v": [p[1] for p in pts[-260:]]}
        time.sleep(0.3)
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
        spac = bool(re.search(r"acquisition corp|blank check|SPAC", nm, re.I))
        out.append([t, nm[:48], num(r.get("lastsale")), num(r.get("pctchange")), num(r.get("marketCap")), num(r.get("volume")),
                    (r.get("sector") or "").strip(), (r.get("industry") or "").strip()[:40], num(r.get("ipoyear")), (r.get("country") or "").strip()[:20], 1 if spac else 0])
    LOG["ussc"] = len(out)
    return {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "cols": ["t", "n", "px", "chg", "mc", "vol", "sec", "ind", "ipo", "ctry", "spac"], "rows": out}


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
    json.dump(mi, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("출처:", LOG)
    print("크기:", {k: len(json.dumps(mi.get(k) or {})) // 1024 for k in ("earn", "macro", "ussc", "feed")}, "KB")


if __name__ == "__main__":
    main()
