#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
국내 증시 수급 — 누가 사고 누가 파나

1) 시장 전체 투자자별 순매수(다음 금융): 코스피·코스닥 일별, 최근 약 1년 (원 → 억원)
2) 종목별 투자자별 순매수(네이버 모바일): 시가총액 상위 종목의 최근 20거래일
   외국인·기관·개인 순매수 수량 × 종가 → 억원, 외국인 보유율(소진율) 변화, 연속 순매수 일수
   기관 세부(연기금·사모·투신·금융투자·보험·은행·기타금융)와 기타법인도 함께
3) 프로그램 매매(차익·비차익, 네이버) — 그날 값만 주므로 날마다 쌓는다
4) 코스피200 선물 투자자별 순매수는 derivs.py 가 날마다 쌓는 기록(market_index.json deriv.kr)을 그대로 쓴다

결과는 output/market_index.json 의 "flow" 키에 넣는다(대시보드 동기화가 이 파일을 옮김).
"""
import json, os, re, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
N_STOCK = int(os.getenv("FLOW_STOCKS", "400"))
DAYS = int(os.getenv("FLOW_DAYS", "250"))
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"


def num(v):
    try:
        return float(str(v).replace(",", "").replace("+", "").replace("%", "") or 0)
    except ValueError:
        return 0.0


DET = {"PENSION_FUND": "pen", "PRIVATE_EQUITY_FUND": "pef", "MUTUAL_FUND": "mf", "FINANCIAL_INVESTOR": "fin",
       "INSURANCE_COMPANIES": "insc", "BANK": "bank", "ETC_FINANCIAL_INSTITUTION": "etcf", "ETC_CORPORATION": "corp", "ETC_FOREIGN": "efrn"}


def program(old):
    """네이버 — 프로그램 매매(차익·비차익·전체, 억원). 장 마감(15:45) 뒤 값만 확정으로 쌓는다."""
    now = datetime.now(KST)
    res = {}
    for mk in ("KOSPI", "KOSDAQ"):
        hist = {x["d"]: x for x in ((old or {}).get(mk.lower()) or [])}
        try:
            j = requests.get(f"https://m.stock.naver.com/api/index/{mk}/integration",
                             headers={"User-Agent": UA, "Referer": "https://m.stock.naver.com/"}, timeout=20).json()
            p, d = j.get("programTrendInfo") or {}, ((j.get("dealTrendInfo") or {}).get("bizdate") or "")
            row = {"d": f"{d[:4]}-{d[4:6]}-{d[6:]}", "arb": num(p.get("indexDifferenceReal")), "narb": num(p.get("indexBiDifferenceReal")),
                   "tot": num(p.get("indexTotalReal"))}
            final = row["d"] < now.strftime("%Y-%m-%d") or (row["d"] == now.strftime("%Y-%m-%d") and (now.hour, now.minute) >= (15, 45))
            if len(d) == 8 and final and any(row[k] for k in ("arb", "narb", "tot")):
                hist[row["d"]] = row
                print(f"[{mk}] 프로그램 {row}")
            else:
                print(f"[{mk}] 프로그램: 확정 전이거나 0 — 저장 안 함 {row}")
        except Exception as e:
            print(f"  프로그램 {mk}: {type(e).__name__} {e}"[:150])
        res[mk.lower()] = [hist[k] for k in sorted(hist)][-250:]
    return res


def market_days(mk):
    """다음 금융 — 시장 투자자별 순매수(원). 페이지를 넘겨 DAYS 일까지."""
    out, page = {}, 1
    while len(out) < DAYS and page <= 20:
        try:
            r = requests.get(f"https://finance.daum.net/api/investor/{mk}/days",
                             params={"perPage": 30, "page": page, "details": "true"},
                             headers={"User-Agent": UA, "Referer": "https://finance.daum.net/"}, timeout=20)
            data = (r.json() or {}).get("data") or []
        except Exception as e:
            print(f"  다음 {mk} p{page}: {type(e).__name__} {e}"[:150])
            break
        if not data:
            break
        new = 0
        for x in data:
            d = str(x.get("date", ""))[:10]
            if d and d not in out:
                out[d] = {"frn": round(num(x.get("foreignStraightPurchasePrice")) / 1e8),
                          "ins": round(num(x.get("institutionStraightPurchasePrice")) / 1e8),
                          "ind": round(num(x.get("individualStraightPurchasePrice")) / 1e8)}
                det = x.get("details")
                if isinstance(det, dict):                       # 기관 세부
                    for src, key in DET.items():
                        if src in det:
                            out[d][key] = round(num(det[src]) / 1e8)
                new += 1
        if not new:
            break
        page += 1
        time.sleep(0.3)
    rows = [{"d": d, **out[d]} for d in sorted(out)]
    print(f"[{mk}] 시장 수급 {len(rows)}일 · {rows[0]['d'] if rows else '-'} ~ {rows[-1]['d'] if rows else '-'}")
    return rows


def stock_trend(code):
    for k in range(3):
        try:
            r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/trend", params={"pageSize": 20},
                             headers={"User-Agent": UA, "Referer": "https://m.stock.naver.com/"}, timeout=15)
            js = r.json()
            if isinstance(js, list) and js:
                return code, js
        except Exception:
            time.sleep(1 + k)
    return code, None


def stocks():
    u = json.load(open(os.path.join(OUT, "universe_kr.json"), encoding="utf-8"))
    rows = [r for r in u["rows"] if not r.get("is_etf") and r.get("ticker")]
    rows.sort(key=lambda r: -(r.get("mcap") or r.get("market_cap") or r.get("value") or 0))
    rows = rows[:N_STOCK]
    meta = {r["ticker"]: r for r in rows}
    with ThreadPoolExecutor(max_workers=8) as ex:
        got = dict(x for x in ex.map(stock_trend, list(meta)) if x[1])
    print(f"종목 수급 {len(got)}/{len(meta)}종목")
    out = []
    for code, js in got.items():
        js = sorted(js, key=lambda x: x.get("bizdate", ""))          # 오래된 날 → 최근
        px = [num(x.get("closePrice")) for x in js]
        f = [round(num(x.get("foreignerPureBuyQuant")) * p / 1e8, 1) for x, p in zip(js, px)]
        o = [round(num(x.get("organPureBuyQuant")) * p / 1e8, 1) for x, p in zip(js, px)]
        i = [round(num(x.get("individualPureBuyQuant")) * p / 1e8, 1) for x, p in zip(js, px)]
        hr = [num(x.get("foreignerHoldRatio")) for x in js]
        if not px or not px[-1]:
            continue
        m = meta[code]
        streak = lambda s: next((k for k in range(len(s)) if s[-1 - k] <= 0), len(s)) if s and s[-1] > 0 else -next((k for k in range(len(s)) if s[-1 - k] >= 0), len(s))
        out.append({"t": code, "n": m.get("name") or code, "sec": m.get("sector") or "", "mc": m.get("mcap") or m.get("market_cap"),
                    "d": [x.get("bizdate") for x in js], "px": px, "f": f, "o": o, "i": i, "hr": hr,
                    "fs": streak(f), "os": streak(o)})
    return out


def main():
    p = os.path.join(OUT, "market_index.json")
    mi = json.load(open(p, encoding="utf-8"))
    old = mi.get("flow") or {}
    res = {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "kospi": market_days("KOSPI"), "kosdaq": market_days("KOSDAQ"), "stocks": stocks(),
           "prog": program(old.get("prog"))}
    for k in ("kospi", "kosdaq", "stocks"):                    # 이번에 못 받은 항목은 지난 값 유지
        if not res.get(k) and old.get(k):
            res[k] = old[k]
    if old.get("kprog"):                                       # 종목별 프로그램(kr_prog_kis.py)은 그대로 둔다
        res["kprog"] = old["kprog"]
    mi["flow"] = res
    json.dump(mi, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    k = res["kospi"][-1] if res["kospi"] else None
    print(f"→ {p} flow 추가 · 코스피 마지막 {k} · 종목 {len(res['stocks'])}")


if __name__ == "__main__":
    main()
