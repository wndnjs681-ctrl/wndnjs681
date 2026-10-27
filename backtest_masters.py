#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
대가 점수 검증(백테스트) — "추세추종 대가 점수가 높았던 종목이 실제로 그 뒤에 더 올랐나?"

대시보드 추천 탭의 '추세추종 대가'(리버모어·오닐·미너비니·다르바스·와인스타인·터틀) 체크리스트 중
가격·상대강도로 판단하는 항목만으로 과거 매달 점수를 다시 매기고, 그 뒤 1개월(21거래일)·3개월(63거래일) 수익률을 본다.
  - 점수 5분위별 평균 수익률·승률(유니버스 평균 대비 초과)
  - 매달 점수 상위 20종목 동일가중 보유(월 1회 교체) vs 유니버스 동일가중 — CAGR·최대낙폭·샤프·월 승률
한계: 지금 유니버스(거래대금 상위) 종목만 써서 생존편향이 있고, 실적·거래량·시장 방향 항목은 빠졌다. 거래비용 미반영.

입력: output/universe_{kr,us}.json   출력: output/backtest_{kr,us}.json
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
N_TICK = int(os.getenv("BT_TICKERS", "400"))        # 시장별 거래대금 상위 N 종목
YEARS = float(os.getenv("BT_YEARS", "4.2"))
STEP, TOPN = 21, 20


def fetch(code):
    import FinanceDataReader as fdr
    start = (datetime.now() - timedelta(days=int(365 * YEARS))).strftime("%Y-%m-%d")
    for k in range(3):
        try:
            df = fdr.DataReader(code, start)
            if df is not None and len(df) > 300 and "Close" in df:
                s = df["Close"].astype(float)
                s = s[np.isfinite(s) & (s > 0)]
                s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                return code, s[~s.index.duplicated(keep="last")]
        except Exception:
            time.sleep(1 + k)
    return code, None


def score_panel(C, mk):
    """종목×날짜 종가 → 추세추종 대가 6명 점수(0~100), 상대강도 백분위."""
    m50, m150, m200 = (C.rolling(n, min_periods=n).mean() for n in (50, 150, 200))
    m200p, m150p = m200.shift(21), m150.shift(21)
    hi, lo = C.rolling(252, min_periods=240).max(), C.rolling(252, min_periods=240).min()
    h20 = C.rolling(20).max()
    h20p, h55p, l10p = C.shift(1).rolling(20).max(), C.shift(1).rolling(55).max(), C.shift(1).rolling(10).min()
    rng20 = C.rolling(20).max() / C.rolling(20).min() - 1
    rsv = 2 * C / C.shift(63) + C / C.shift(126) + C / C.shift(189) + C / C.shift(252)
    rs = rsv.rank(axis=1, pct=True) * 99
    box = 0.15 if mk == "us" else 0.20
    f = lambda *cs: sum(c.astype(float) for c in cs) / len(cs)
    mine = f((C > m150) & (C > m200), m150 > m200, m200 > m200p, (m50 > m150) & (m50 > m200), C > m50,
             C >= 1.3 * lo, C >= 0.75 * hi, rs >= 70)
    live = f(C >= 0.95 * hi, (C > m50) & (m50 > m200), C >= 0.97 * h20)
    darv = f(C >= 0.95 * hi, rng20 <= box, (C > h20p).astype(float).rolling(3).max() > 0)
    wein = f(C > m150, m150 > m150p, ~((C < m150) & (m150 < m150p)), rs >= 50)
    turt = f(C > h20p, C > h55p, C > l10p)
    onei = f(C >= 0.85 * hi, rs >= 80, C > m50, m50 > m200)
    sc = (mine + live + darv + wein + turt + onei) / 6 * 100
    ok = m200p.notna() & hi.notna() & rsv.notna()
    return sc.where(ok), rs


def stats(monthly):
    r = np.array(monthly, float)
    if not len(r):
        return {}
    eq = np.cumprod(1 + r)
    yrs = len(r) * STEP / 252
    dd = eq / np.maximum.accumulate(eq) - 1
    sd = r.std(ddof=1) if len(r) > 1 else 0
    return {"cagr": round((eq[-1] ** (1 / yrs) - 1) * 100, 1) if yrs > 0 else None,
            "total": round((eq[-1] - 1) * 100, 1), "mdd": round(dd.min() * 100, 1),
            "sharpe": round(r.mean() / sd * np.sqrt(252 / STEP), 2) if sd > 0 else None,
            "win": round((r > 0).mean() * 100, 0), "avg": round(r.mean() * 100, 2)}


def run(mk):
    u = json.load(open(os.path.join(OUT, f"universe_{mk}.json"), encoding="utf-8"))
    rows = sorted((r for r in u["rows"] if not r.get("is_etf")), key=lambda r: -(r.get("value") or 0))[:N_TICK]
    codes = [r["ticker"] for r in rows]
    print(f"[{mk}] {len(codes)}종목 {YEARS}년치 받는 중…", flush=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        got = dict(x for x in ex.map(fetch, codes) if x[1] is not None)
    if len(got) < 50:
        raise RuntimeError(f"받은 종목이 {len(got)}개뿐")
    C = pd.DataFrame(got).sort_index()
    C = C[C.notna().sum(axis=1) >= len(got) * 0.5]                 # 대부분 종목이 거래한 날만(휴장 정렬)
    sc, rs = score_panel(C, mk)
    F21, F63 = C.shift(-21) / C - 1, C.shift(-63) / C - 1
    idx = [i for i in range(len(C)) if sc.iloc[i].notna().sum() >= 50]
    if not idx:
        raise RuntimeError("점수를 매길 수 있는 날이 없음")
    evals = list(range(idx[0], len(C) - 21, STEP))
    qrows, top_m, uni_m, curve, eq_t, eq_u = [], [], [], [], 1.0, 1.0
    for i in evals:
        s, f21, f63 = sc.iloc[i], F21.iloc[i], F63.iloc[i]
        ok = s.notna() & f21.notna()
        s, f21, f63 = s[ok], f21[ok], f63[ok]
        if len(s) < 50:
            continue
        q = pd.qcut(s.rank(method="first"), 5, labels=False)
        for k in range(5):
            m = q == k
            qrows.append({"q": k + 1, "lo": float(s[m].min()), "hi": float(s[m].max()), "f21": float(f21[m].mean()),
                          "f63": float(f63[m].mean()) if f63[m].notna().any() else np.nan, "hit": float((f21[m] > 0).mean()),
                          "u21": float(f21.mean()), "u63": float(f63.mean()) if f63.notna().any() else np.nan})
        top = s.sort_values(ascending=False, kind="mergesort").index[:TOPN]
        tr, ur = float(f21[top].mean()), float(f21.mean())
        top_m.append(tr); uni_m.append(ur)
        eq_t *= 1 + tr; eq_u *= 1 + ur
        curve.append([C.index[i + 21].strftime("%Y-%m-%d"), round(eq_t, 4), round(eq_u, 4)])
    Q = pd.DataFrame(qrows)
    quint = []
    for k, g in Q.groupby("q"):
        quint.append({"q": int(k), "score": [round(g["lo"].mean()), round(g["hi"].mean())],
                      "f21": round(g["f21"].mean() * 100, 2), "f63": round(g["f63"].mean() * 100, 2),
                      "x21": round((g["f21"] - g["u21"]).mean() * 100, 2), "x63": round((g["f63"] - g["u63"]).mean() * 100, 2),
                      "hit": round(g["hit"].mean() * 100, 0), "n": int(len(g))})
    now_top = sc.iloc[-1].dropna().sort_values(ascending=False).head(TOPN)
    res = {"market": mk, "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
           "period": [C.index[evals[0]].strftime("%Y-%m-%d"), C.index[-1].strftime("%Y-%m-%d")],
           "tickers": len(got), "rebalances": len(top_m), "top_n": TOPN, "quint": quint,
           "top": stats(top_m), "univ": stats(uni_m), "curve": curve,
           "spread21": round(quint[-1]["f21"] - quint[0]["f21"], 2) if len(quint) == 5 else None,
           "note": "가격·상대강도 항목만(실적·거래량·시장 방향 제외), 현재 거래대금 상위 종목만(생존편향), 비용 미반영, 월 1회(21거래일) 교체"}
    p = os.path.join(OUT, f"backtest_{mk}.json")
    json.dump(res, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"[{mk}] {res['period'][0]}~{res['period'][1]} · {len(got)}종목 · {len(top_m)}회 교체 · "
          f"상위{TOPN} {res['top']} · 유니버스 {res['univ']} · 5분위−1분위 1개월 {res['spread21']}%p → {p}", flush=True)
    for x in quint:
        print("   ", x)


def self_test():
    # 꾸준히 오르는 종목은 점수가 높고, 꾸준히 내리는 종목은 낮아야 한다
    n = 400
    t = np.arange(n)
    C = pd.DataFrame({"UP": 100 * 1.003 ** t, "DN": 100 * 0.997 ** t,
                      **{f"N{i}": 100 + np.sin(t / 7 + i) * 5 for i in range(6)}},
                     index=pd.bdate_range("2020-01-01", periods=n))
    sc, rs = score_panel(C, "us")
    last = sc.iloc[-1]
    assert last["UP"] > 80 and last["DN"] < 20, last.to_dict()
    print("backtest self-test OK")


if __name__ == "__main__":
    self_test()
    for mk in [m.strip() for m in (sys.argv[1] if len(sys.argv) > 1 else os.getenv("MARKETS", "us,kr")).split(",") if m.strip()]:
        try:
            run(mk)
        except Exception as e:
            print(f"[{mk}] 실패: {type(e).__name__}: {e}", flush=True)
