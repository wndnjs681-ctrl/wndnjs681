#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Post-IPO 탭 자료 자동 갱신 — 최근 13개월 신규 상장(국내·미국)

  미국: 나스닥 IPO 캘린더(월별 priced) → 종목·상장일·공모가·조달액, 업종은 FDR 상장 목록, 시세는 FDR 일봉
  국내: 한국투자증권 Open API 공모주 청약 일정(예탁원) → 종목·상장일·공모가·주관사, 시세는 FDR 일봉
        (기관경쟁률·확약·유통물량은 공개 API 가 없어 기존 스냅샷 값이 있으면 유지)
  기존 output/ipo.json 을 바탕으로 새 상장을 더하고, 모든 종목 시세를 다시 붙인다.

결과: output/ipo.json, 그리고 output/market_index.json 의 "ipo"(대시보드 동기화가 이 파일을 옮김)
"""
import json, os, re, time
from datetime import date, datetime, timedelta, timezone

import requests

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
START = TODAY - timedelta(days=int(os.getenv("IPO_DAYS", "400")))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
KIS = os.getenv("KIS_BASE", "https://openapi.koreainvestment.com:9443")
KEY, SECRET = os.getenv("KIS_APP_KEY", "").strip(), os.getenv("KIS_APP_SECRET", "").strip()


def num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except Exception:
        return None


def ymd(s):
    s = re.sub(r"[^0-9]", "", str(s or ""))
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else None


# ── 업종 → 대시보드 분류 ──
SEC_RULES = [(r"biotech|pharma|drug|therapeut|biolog|bio", "바이오"), (r"health|medical|diagnos|hospital|care", "헬스케어"),
             (r"bank|insur|capital|financ|credit|asset|blank check|shell|acquisition|reit|invest", "금융"),
             (r"real estate|property|reit", "부동산"), (r"oil|gas|energy|solar|uranium|nuclear|mining|metal|chemical|material|steel|utilit|power", "에너지·소재"),
             (r"aerospace|defense|industrial|machinery|construct|engineer|transport|logistic|airline|electrical equip|robot", "산업재·방산"),
             (r"software|semicon|technolog|internet|comput|data|cloud|electronic|it serv|artificial|ai\b|anthropic|openai|xai\b", "테크"),
             (r"media|telecom|communic|entertain|advertis|game", "미디어·통신"),
             (r"retail|consumer|food|beverage|apparel|restaurant|leisure|auto|household|cosmetic|education", "소비재")]


def sec_of(txt):
    t = (txt or "").lower()
    for rx, s in SEC_RULES:
        if re.search(rx, t):
            return s
    return "미분류"


# ── 시세(FDR) ──
def prices(code, start):
    import FinanceDataReader as fdr
    for k in range(2):
        try:
            df = fdr.DataReader(code, start)
            if df is not None and len(df):
                df = df[df["Close"] > 0]
                return df
        except Exception:
            time.sleep(1 + k)
    return None


def attach(r):
    """상장일 이후 일봉 → o1·c1·sd·sc(yymmdd, 종가)"""
    df = prices(r["c"], r["d"])
    if df is None or not len(df):
        return False
    idx = [d.strftime("%y%m%d") for d in df.index]
    first = df.iloc[0]
    if df.index[0].strftime("%Y-%m-%d") <= (datetime.strptime(r["d"], "%Y-%m-%d") + timedelta(days=4)).strftime("%Y-%m-%d"):
        if not r.get("o1") and num(first.get("Open")):
            r["o1"] = round(float(first["Open"]), 2)
        if not r.get("c1"):
            r["c1"] = round(float(first["Close"]), 2)
    r["sd"] = ",".join(idx)
    r["sc"] = ",".join(f"{v:.2f}".rstrip("0").rstrip(".") for v in df["Close"])
    r["sx"] = "fdr"
    r["snap"] = round(float(df["Close"].iloc[-1]), 2)
    return True


# ── 미국: 나스닥 IPO 캘린더 ──
def nasdaq(path, params):
    for k in range(3):
        try:
            return requests.get("https://api.nasdaq.com/api/" + path, params=params, timeout=20,
                                headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}).json()
        except Exception:
            time.sleep(1.5 + k)
    return {}


WATCH = {}


def us_new():
    out, m = {}, START.replace(day=1)
    while m <= TODAY:
        j = nasdaq("ipo/calendar", {"date": m.strftime("%Y-%m")})
        data = j.get("data") or {}
        pr = data.get("priced") or {}
        rows = pr.get("rows") if isinstance(pr, dict) else None
        for r in rows or []:
            t = (r.get("proposedTickerSymbol") or "").strip().upper()
            try:
                d = datetime.strptime(r.get("pricedDate") or "", "%m/%d/%Y").date()
            except ValueError:
                continue
            if not t or d > TODAY or d < START:
                continue
            nm = (r.get("companyName") or t).strip()
            if re.search(r"acquisition|spac|capital corp\.? ?(i|ii|iii|iv|v)?$|merger", nm, re.I) and not re.search(r"bancorp|bank", nm, re.I):
                continue                                        # 스팩(SPAC)은 뺌
            usd = num(r.get("dollarValueOfSharesOffered"))
            out[t] = {"c": t, "n": nm, "m": "US", "d": d.isoformat(), "op": num(r.get("proposedSharePrice")), "o1": None, "c1": None,
                      "raise_": round(usd / 1e6) if usd else None, "comp": None, "lock": None, "fl": None, "uw": None, "src": "nasdaq",
                      "ex": r.get("proposedExchange")}
            if "ANTHROPIC" in nm.upper():
                print("앤트로픽 행:", r)
        up = data.get("upcoming") or {}
        urows = ((up.get("upcomingTable") or {}).get("rows") if isinstance(up, dict) else None) or []
        for r in urows:                                         # 상장 예정 → 감시 목록(상장 뒤 나스닥 표에서 빠지는 경우가 있어 기억해 둔다)
            t = (r.get("proposedTickerSymbol") or "").strip().upper()
            try:
                d = datetime.strptime(r.get("expectedPriceDate") or "", "%m/%d/%Y").date()
            except ValueError:
                continue
            nm = (r.get("companyName") or t).strip()
            if t and not re.search(r"acquisition|spac|merger", nm, re.I):
                px = [num(x) for x in re.findall(r"[\d.]+", str(r.get("proposedSharePrice") or ""))]
                px = [x for x in px if x]
                WATCH[t] = {"t": t, "n": nm, "d": d.isoformat(), "op": round(sum(px) / len(px), 2) if px else None,
                            "usd": num(r.get("dollarValueOfSharesOffered")), "ex": r.get("proposedExchange")}
        time.sleep(0.4)
        m = (m + timedelta(days=32)).replace(day=1)
    print(f"[US] 나스닥 priced {len(out)}건(스팩 제외)")
    return out


def us_industry():
    try:
        import FinanceDataReader as fdr
        ind = {}
        for ex in ("NASDAQ", "NYSE", "AMEX"):
            try:
                df = fdr.StockListing(ex)
                col = "Industry" if "Industry" in df else ("Sector" if "Sector" in df else None)
                if col:
                    ind.update({str(s).upper(): str(v) for s, v in zip(df["Symbol"], df[col]) if v == v})
            except Exception as e:
                print("StockListing", ex, type(e).__name__)
        return ind
    except Exception:
        return {}


# ── 국내: KIS 공모주 청약 일정 ──
def kis_token():
    if not KEY or not SECRET:
        print("KIS 키 없음 — 국내 신규 상장 건너뜀")
        return None
    j = requests.post(KIS + "/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": KEY, "appsecret": SECRET}, timeout=20).json()
    return j.get("access_token")


def kr_new(tok):
    out, s = {}, START
    h = {"authorization": "Bearer " + tok, "appkey": KEY, "appsecret": SECRET, "tr_id": "HHKDB669108C0", "custtype": "P", "content-type": "application/json; charset=utf-8"}
    up = []
    end_all = TODAY + timedelta(days=45)
    while s <= end_all:
        e = min(end_all, s + timedelta(days=14))
        try:
            j = requests.get(KIS + "/uapi/domestic-stock/v1/ksdinfo/pub-offer", headers=h, timeout=20,
                             params={"SHT_CD": "", "CTS": "", "F_DT": s.strftime("%Y%m%d"), "T_DT": e.strftime("%Y%m%d")}).json()
        except Exception as ex:
            print("KIS 공모:", type(ex).__name__)
            j = {}
        for r in j.get("output1") or j.get("output") or []:
            nm, code = (r.get("isin_name") or "").strip(), (r.get("sht_cd") or "").strip()
            ld = ymd(r.get("list_dt"))
            if not nm or not code or not ld or re.search(r"스팩|기업인수목적|리츠|REIT", nm, re.I):
                continue
            op = num(r.get("fix_subscr_pri"))
            row = {"c": code, "n": re.sub(r"\(주\)|㈜", "", nm).strip(), "m": "KQ", "d": ld, "op": op, "o1": None, "c1": None,
                   "raise_": None, "comp": None, "lock": None, "fl": None, "uw": (r.get("lead_mgr") or "").strip() or None, "src": "kis"}
            if ld <= TODAY.isoformat():
                out[code] = row
            else:
                up.append({"n": row["n"], "d": ld, "op": op, "comp": None, "lock": None, "note": "청약 " + str(r.get("subscr_dt") or "").replace(" ", "")})
        s = e + timedelta(days=1)
        time.sleep(0.3)
    print(f"[KR] KIS 공모 상장 {len(out)}건 · 상장 예정 {len(up)}건")
    return out, up


def kr_market():
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        col = "Market" if "Market" in df else None
        return {str(c): ("KS" if "KOSPI" in str(m) else "KQ") for c, m in zip(df["Code"], df[col])} if col else {}
    except Exception as e:
        print("KRX 목록:", type(e).__name__)
        return {}


def main():
    p = os.path.join(OUT, "ipo.json")
    base = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"v": 1, "kr": [], "us": [], "upcoming": []}
    keep = lambda L: {r["c"]: r for r in L if r.get("d", "") >= START.isoformat()}
    us, kr = keep(base.get("us", [])), keep(base.get("kr", []))

    # 미국
    for w in base.get("watch", []):
        WATCH.setdefault(w["t"], w)
    new_us = us_new()
    for t, w in list(WATCH.items()):
        if w["d"] < START.isoformat() or w["d"] < (TODAY - timedelta(days=30)).isoformat():
            WATCH.pop(t)
            continue
        if t in new_us or t in us or w["d"] > TODAY.isoformat():
            continue
        r = {"c": t, "n": w["n"], "m": "US", "d": w["d"], "op": w.get("op"), "o1": None, "c1": None,
             "raise_": round(w["usd"] / 1e6) if w.get("usd") else None, "comp": None, "lock": None, "fl": None, "uw": None, "src": "nasdaq-upcoming", "ex": w.get("ex")}
        if attach(r) and r["sd"].split(",")[0] >= w["d"][2:].replace("-", ""):   # 상장일 이후 시세가 실제로 있으면 상장 확인
            new_us[t] = r
            print("감시 목록에서 상장 확인:", t, w["n"], w["d"], "첫날", r.get("o1"), r.get("c1"))
            r.pop("sx", None)
        else:
            print("감시 목록: 아직 시세 없음", t, w["d"])
    ind = us_industry() if new_us else {}
    uu = {}
    try:
        uu = {r["ticker"]: r for r in json.load(open(os.path.join(OUT, "universe_us.json"), encoding="utf-8"))["rows"]}
    except Exception:
        pass
    for t, r in new_us.items():
        if t in us:                                             # 기존 행은 메타데이터 유지, 빈칸만 채움
            for k, v in r.items():
                if us[t].get(k) in (None, "") and v not in (None, ""):
                    us[t][k] = v
            continue
        i = ind.get(t) or (uu.get(t) or {}).get("sector") or ""
        r["ind"] = i or None
        r["sec"] = sec_of(i + " " + r["n"])
        us[t] = r
    # 국내
    tok = kis_token()
    up = base.get("upcoming", [])
    if tok:
        new_kr, up_new = kr_new(tok)
        mkt = kr_market()
        uk = {}
        try:
            uk = {r["ticker"]: r for r in json.load(open(os.path.join(OUT, "universe_kr.json"), encoding="utf-8"))["rows"]}
        except Exception:
            pass
        for c, r in new_kr.items():
            if c in kr:
                for k, v in r.items():
                    if kr[c].get(k) in (None, "") and v not in (None, ""):
                        kr[c][k] = v
                continue
            r["m"] = mkt.get(c, "KQ")
            r["ind"] = (uk.get(c) or {}).get("sector")
            r["sec"] = sec_of(r["ind"] or "") if r["ind"] else "미분류"
            kr[c] = r
        if up_new or not any(u.get("d", "") > TODAY.isoformat() for u in up):
            up = sorted(up_new, key=lambda u: u["d"])

    # 시세 붙이기
    ok = 0
    for L in (us, kr):
        for r in L.values():
            try:
                ok += attach(r)
            except Exception as e:
                print("시세", r["c"], type(e).__name__)
            time.sleep(0.05)
    print(f"시세 {ok}/{len(us) + len(kr)}종목")
    out = {"v": 1, "asof": TODAY.isoformat(), "built": datetime.now(KST).strftime("%Y-%m-%dT%H:%M+09:00"),
           "note": "자동 갱신: 미국 나스닥 IPO 캘린더·국내 한국투자증권 공모 일정(예탁원), 시세 FinanceDataReader. 기관경쟁률·확약·유통물량은 9월 스냅샷 종목만",
           "kr": sorted(kr.values(), key=lambda r: r["d"], reverse=True), "us": sorted(us.values(), key=lambda r: r["d"], reverse=True), "upcoming": up,
           "watch": sorted(WATCH.values(), key=lambda w: w["d"])}
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    mp = os.path.join(OUT, "market_index.json")
    mi = json.load(open(mp, encoding="utf-8"))
    mi["ipo"] = out
    cal = mi.get("cal") or {}
    if cal.get("ev") is not None:                               # 일정 탭: 최근 상장이 빠져 있으면 넣는다
        rng = cal.get("range") or ["0000", "9999"]
        have = {(e.get("tk"), e.get("mk")) for e in cal["ev"] if e.get("cat") == "ipo"}
        add = 0
        for mk, L in (("us", out["us"]), ("kr", out["kr"])):
            for r in L:
                if rng[0] <= r["d"] <= rng[1] and (r["c"], mk) not in have:
                    pxs = f"공모가 ${r['op']:.2f}" if mk == "us" and r.get("op") else f"공모가 {r['op']:,.0f}원" if r.get("op") else ""
                    first = f"첫날 종가 {'$' if mk == 'us' else ''}{r['c1']:,}" if r.get("c1") else ""
                    cal["ev"].append({"d": r["d"], "mk": mk, "cat": "ipo", "n": f"{r['n']} 상장", "imp": 3 if (r.get("raise_") or 0) >= 5000 else 2 if (r.get("raise_") or 0) >= 300 or mk == "kr" else 1,
                                      "tk": r["c"], "x": " · ".join(x for x in [r.get("ex") or "", pxs, first, f"규모 ${r['raise_']:,}M" if mk == "us" and r.get("raise_") else ""] if x)})
                    add += 1
        cal["ev"].sort(key=lambda e: (e["d"], -e.get("imp", 1), e["n"]))
        print(f"일정 탭에 상장 {add}건 보강")
    json.dump(mi, open(mp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"→ {p} · 국내 {len(kr)} · 미국 {len(us)} · 예정 {len(up)} · 최근 미국 {[(r['n'], r['d']) for r in out['us'][:5]]}")


if __name__ == "__main__":
    main()
