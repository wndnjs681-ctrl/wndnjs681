#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
시장 지수 일봉 수집 — 스크리너의 오닐식 시장 방향(M) 판정용.
  미국: 나스닥 종합(^IXIC), S&P500(^GSPC)   국내: 코스피(^KS11), 코스닥(^KQ11)
산출물: output/market_index.json
  {"asof": "...", "idx": {"IXIC": {"name":"나스닥","mk":"us","d":[...],"c":[...],"v":[...]}, ...}}
판정(분산일·팔로스루·상태)은 화면 쪽에서 계산한다 — 규칙을 바꿀 때 워크플로를 다시 돌릴 필요가 없게.
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone
import requests

OUT_DIR = os.getenv("OUT_DIR", "output")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
IDX = [("IXIC", "^IXIC", "나스닥", "us"), ("GSPC", "^GSPC", "S&P500", "us"),
       ("KS11", "^KS11", "코스피", "kr"), ("KQ11", "^KQ11", "코스닥", "kr")]
KST = timezone(timedelta(hours=9))


def yahoo(sym):
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}",
                             params={"range": "2y", "interval": "1d"}, headers={"User-Agent": UA}, timeout=30)
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            d, c, v = [], [], []
            for t, cl, vo in zip(res["timestamp"], q["close"], q["volume"]):
                if cl is None:
                    continue
                d.append(datetime.fromtimestamp(t, KST if sym.startswith("^K") else timezone(timedelta(hours=-5))).strftime("%Y-%m-%d"))
                c.append(round(cl, 2)); v.append(int(vo or 0))
            if len(c) > 100:
                return d, c, v
        except Exception as e:
            print(f"  {sym} {host}: {type(e).__name__} {e}"[:150])
        time.sleep(1)
    return None


def fdr(sym):
    """야후가 막히면 FinanceDataReader 로 대신 받는다."""
    try:
        import FinanceDataReader as F
        m = {"^IXIC": "IXIC", "^GSPC": "US500", "^KS11": "KS11", "^KQ11": "KQ11"}[sym]
        df = F.DataReader(m, (datetime.now() - timedelta(days=740)).strftime("%Y-%m-%d")).dropna(subset=["Close"])
        return ([i.strftime("%Y-%m-%d") for i in df.index], [round(float(x), 2) for x in df["Close"]],
                [int(x or 0) for x in df.get("Volume", [0] * len(df))])
    except Exception as e:
        print(f"  {sym} FDR: {type(e).__name__} {e}"[:150])
        return None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    out = {}
    for key, sym, name, mk in IDX:
        got = yahoo(sym) or fdr(sym)
        if not got:
            print(f"[{key}] 실패"); continue
        d, c, v = got
        out[key] = {"name": name, "mk": mk, "d": d[-400:], "c": c[-400:], "v": v[-400:]}
        print(f"[{key}] {name} {len(d)}일 · 마지막 {d[-1]} {c[-1]:,} · 거래량 0인 날 {sum(1 for x in v[-60:] if not x)}/60")
    if not out:
        sys.exit("지수를 하나도 받지 못했습니다")
    p = os.path.join(OUT_DIR, "market_index.json")
    json.dump({"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "idx": out},
              open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"→ {p} {os.path.getsize(p):,} bytes")


if __name__ == "__main__":
    main()
