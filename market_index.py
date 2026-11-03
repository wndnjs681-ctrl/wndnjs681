#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
시장 지수·거시 지표 일봉 수집 — 스크리너의 시장 판단 화면(오닐 M·와인스타인 단계·미너비니 추세·리버모어 추세 구조)용.
  미국: 나스닥(^IXIC)·S&P500(^GSPC)·다우·러셀2000·필라델피아 반도체   국내: 코스피·코스닥
  일본: 니케이225·TOPIX   중화권: 항셍·상하이종합   대만: 가권   유럽: DAX·유로스톡스50
  거시: VIX·미 10년물 금리·달러인덱스·원/달러·엔/달러
산출물: output/market_index.json
  {"asof": "...", "idx": {"IXIC": {"name":"나스닥","mk":"us","rg":"미국","core":true,"kind":"eq","d":[...],"c":[...],"v":[...]}, ...}}
  core=true 인 지수(나스닥·S&P500·코스피·코스닥)만 종목 추천의 오닐 시장 방향(M)에 쓴다 — 예전 판정과 같게.
판정은 화면 쪽에서 계산한다 — 규칙을 바꿀 때 워크플로를 다시 돌릴 필요가 없게.
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

OUT_DIR = os.getenv("OUT_DIR", "output")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
KST = timezone(timedelta(hours=9))
NY = "America/New_York"
# (키, 야후 심볼 후보, 이름, 시장, 지역, 거래소 시간대, 마감 확정 시각, core, 종류)
IDX = [
    ("IXIC", ["^IXIC"], "나스닥", "us", "미국", NY, (16, 5), True, "eq"),
    ("GSPC", ["^GSPC"], "S&P500", "us", "미국", NY, (16, 5), True, "eq"),
    ("DJI", ["^DJI"], "다우존스", "us", "미국", NY, (16, 5), False, "eq"),
    ("RUT", ["^RUT"], "러셀2000", "us", "미국", NY, (16, 5), False, "eq"),
    ("SOX", ["^SOX"], "필라델피아 반도체", "us", "미국", NY, (16, 5), False, "eq"),
    ("KS11", ["^KS11"], "코스피", "kr", "한국", "Asia/Seoul", (15, 40), True, "eq"),
    ("KQ11", ["^KQ11"], "코스닥", "kr", "한국", "Asia/Seoul", (15, 40), True, "eq"),
    ("N225", ["^N225"], "니케이225", "jp", "일본", "Asia/Tokyo", (15, 35), False, "eq"),
    ("TOPIX", ["^TOPX", "998405.T", "1306.T"], "TOPIX", "jp", "일본", "Asia/Tokyo", (15, 35), False, "eq"),
    ("HSI", ["^HSI"], "항셍", "cn", "중화권", "Asia/Hong_Kong", (16, 15), False, "eq"),
    ("SSEC", ["000001.SS"], "상하이종합", "cn", "중화권", "Asia/Shanghai", (15, 5), False, "eq"),
    ("TWII", ["^TWII"], "대만 가권", "tw", "대만", "Asia/Taipei", (13, 35), False, "eq"),
    ("GDAXI", ["^GDAXI"], "DAX", "eu", "유럽", "Europe/Berlin", (17, 40), False, "eq"),
    ("SX5E", ["^STOXX50E"], "유로스톡스50", "eu", "유럽", "Europe/Berlin", (17, 40), False, "eq"),
    ("VIX", ["^VIX"], "VIX(공포지수)", "macro", "거시", NY, (16, 20), False, "vix"),
    ("TNX", ["^TNX"], "미 10년물 금리", "macro", "거시", NY, (15, 5), False, "rate"),
    ("DXY", ["DX-Y.NYB"], "달러인덱스", "macro", "거시", NY, (17, 5), False, "fx"),
    ("USDKRW", ["KRW=X"], "원/달러", "macro", "거시", NY, (17, 5), False, "fx"),
    ("USDJPY", ["JPY=X"], "엔/달러", "macro", "거시", NY, (17, 5), False, "fx"),
]


KEEP = int(os.getenv("IDX_DAYS", "1300"))              # 약 5년 — 대시보드 지수 차트(주·월봉)용


def _r(x):
    return None if x is None else round(x, 4 if abs(x) < 50 else 2)


def yahoo(sym, tz):
    """{날짜: (시, 고, 저, 종, 거래량)}. 날짜는 야후가 알려주는 거래소 시간대 기준(외환처럼 24시간 시장도 맞게)."""
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}",
                             params={"range": "5y", "interval": "1d"}, headers={"User-Agent": UA}, timeout=30)
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            meta = res.get("meta") or {}
            ztz = ZoneInfo(meta.get("exchangeTimezoneName") or tz)
            n = len(q["close"]); nul = [None] * n
            m = {}
            for t, o, h, l, cl, vo in zip(res["timestamp"], q.get("open") or nul, q.get("high") or nul, q.get("low") or nul,
                                          q["close"], q.get("volume") or nul):
                if cl is None:
                    continue                                 # 같은 날짜가 두 번이면 뒤의 값(마감)을 쓴다
                m[datetime.fromtimestamp(t, ztz).strftime("%Y-%m-%d")] = (_r(o), _r(h), _r(l), _r(cl), int(vo or 0))
            px, pt = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
            if px and pt:                                    # 일봉이 하루 이틀 늦게 붙을 때가 있다 — 마지막 체결가로 메운다
                md = datetime.fromtimestamp(pt, ztz).strftime("%Y-%m-%d")
                if m and md > max(m):
                    m[md] = (None, _r(meta.get("regularMarketDayHigh")), _r(meta.get("regularMarketDayLow")), _r(px),
                             int(meta.get("regularMarketVolume") or 0))
            if len(m) > 100:
                return m
        except Exception as e:
            print(f"  {sym} {host}: {type(e).__name__} {e}"[:150])
        time.sleep(1)
    return None


FDR = {"^IXIC": "IXIC", "^GSPC": "US500", "^KS11": "KS11", "^KQ11": "KQ11", "^DJI": "DJI", "^N225": "N225",
       "^HSI": "HSI", "000001.SS": "SSEC", "^GDAXI": "DAX", "^STOXX50E": "STOXX50E", "^VIX": "VIX",
       "KRW=X": "USD/KRW", "JPY=X": "USD/JPY"}


def fdr(sym):
    """야후가 막히면 FinanceDataReader 로 대신 받는다."""
    if sym not in FDR:
        return None
    try:
        import FinanceDataReader as F
        df = F.DataReader(FDR[sym], (datetime.now() - timedelta(days=int(KEEP * 1.45))).strftime("%Y-%m-%d")).dropna(subset=["Close"])
        g = lambda row, k: _r(float(row[k])) if k in row and row[k] == row[k] else None
        return {i.strftime("%Y-%m-%d"): (g(row, "Open"), g(row, "High"), g(row, "Low"), g(row, "Close"), int(row.get("Volume", 0) or 0))
                for i, row in df.iterrows()}
    except Exception as e:
        print(f"  {sym} FDR: {type(e).__name__} {e}"[:150])
        return None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    out = {}
    p = os.path.join(OUT_DIR, "market_index.json")
    try:                                                     # 이전 결과 — 이번에 받은 게 더 오래됐으면 지난 마감을 지키려고
        prev_all = json.load(open(p, encoding="utf-8"))
        prev = prev_all.get("idx", {})
    except Exception:
        prev_all, prev = {}, {}
    for key, syms, name, mk, rg, tz, ready, core, kind in IDX:
        got = used = None
        for sym in syms:
            got = yahoo(sym, tz) or fdr(sym)
            if got:
                used = sym
                break
        if not got:
            print(f"[{key}] 실패 ({', '.join(syms)})")
            continue
        now = datetime.now(ZoneInfo(tz))                     # 마감 전 실행이면 당일 봉은 현재가라 뺀다
        lastok = (now.date() if (now.hour, now.minute) >= ready else now.date() - timedelta(days=1)).strftime("%Y-%m-%d")
        m = {x: y for x, y in got.items() if x <= lastok}
        pv = prev.get(key)
        if pv and pv.get("d") and (not m or pv["d"][-1] > max(m)):   # 야후가 늦은 데이터를 주면 이전 마감을 이어 붙인다(되돌아가지 않게)
            nn = len(pv["d"]); col = lambda f: pv.get(f) or [None] * nn
            pm = {x: (o, h, l, cc, vv or 0) for x, o, h, l, cc, vv in zip(pv["d"], col("o"), col("h"), col("l"), pv["c"], col("v")) if x <= lastok}
            print(f"  [{key}] 받은 데이터가 이전보다 늦음({max(m) if m else '-'} < {pv['d'][-1]}) — 이전 마감 유지")
            pm.update(m); m = pm
        if not m:
            continue
        d = sorted(m)[-KEEP:]
        o, h, l, c, v = ([m[x][i] for x in d] for i in range(5))
        out[key] = {"name": name, "mk": mk, "rg": rg, "core": core, "kind": kind, "sym": used,
                    "d": d, "o": o, "h": h, "l": l, "c": c, "v": v}
        print(f"[{key}] {name}({used}) {len(d)}일 · 마지막 {d[-1]} {c[-1]:,} · 시고저 있음 {sum(1 for x in o[-60:] if x is not None)}/60 · 거래량 0인 날 {sum(1 for x in v[-60:] if not x)}/60")
    for key, pv in prev.items():                             # 이번에 아예 못 받은 지수는 지난 값을 그대로 둔다
        if key not in out and any(key == x[0] for x in IDX):
            out[key] = pv
            print(f"[{key}] 이번엔 못 받음 — 지난 값 유지(마지막 {pv['d'][-1]})")
    if not out:
        sys.exit("지수를 하나도 받지 못했습니다")
    json.dump({"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "idx": out,
               **({"deriv": prev_all["deriv"]} if prev_all.get("deriv") else {})},   # 파생 포지션(derivs.py)은 그대로 둔다
              open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"→ {p} {os.path.getsize(p):,} bytes")


if __name__ == "__main__":
    main()
