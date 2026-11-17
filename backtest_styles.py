#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
포트폴리오 설계 화면의 스타일 백테스트 — 국내·미국 따로

대시보드 '포트폴리오 설계'와 같은 규칙(가격·변동성 부분)으로 과거 매달(21거래일) 종목을 다시 고르고, 다음 달 수익률을 쌓는다.
  스타일: 추세추종 공격형 · 저베타 방어형 · 균형 코어(추세 60 + 방어 40 — 가치 다리는 과거 재무 자료가 없어 방어로 대체)
  종목 수 5·10, 비중 변동성 역수·동일, 업종당 최대 2종목, 리밸런싱 비용 반영(국내 0.25%·미국 0.05% × 회전율)
  비교: 지수(코스피·S&P500), 유니버스 동일가중
한계: 지금 거래대금 상위 종목만 써서 생존편향이 있고, 밸류·ROE·수급 조건은 과거 시점 자료가 없어 빠짐(가격 규칙만 검증).

입력: output/universe_{kr,us}.json   출력: output/pb_backtest.json (market_index.py 가 market_index.json 의 "pbbt" 로 실어 대시보드에 보냄)
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
N_TICK = int(os.getenv("PBBT_TICKERS", "350"))
YEARS = float(os.getenv("PBBT_YEARS", "5.2"))
STEP = 21
COST = {"kr": 0.0025, "us": 0.0005}
IDX = {"kr": "KS11", "us": "US500"}


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


def stats(r, step=STEP):
    r = np.array(r, float)
    if not len(r):
        return {}
    eq = np.cumprod(1 + r)
    yrs = len(r) * step / 252
    dd = eq / np.maximum.accumulate(eq) - 1
    sd = r.std(ddof=1) if len(r) > 1 else 0
    return {"cagr": round((eq[-1] ** (1 / yrs) - 1) * 100, 1) if yrs > 0 else None, "total": round((eq[-1] - 1) * 100, 1),
            "vol": round(sd * np.sqrt(252 / step) * 100, 1), "mdd": round(dd.min() * 100, 1),
            "sharpe": round(r.mean() / sd * np.sqrt(252 / step), 2) if sd > 0 else None, "win": round((r > 0).mean() * 100)}


def run(mk):
    u = json.load(open(os.path.join(OUT, f"universe_{mk}.json"), encoding="utf-8"))
    rows = sorted((r for r in u["rows"] if not r.get("is_etf") and r.get("ticker")), key=lambda r: -(r.get("value") or 0))[:N_TICK]
    sec = {r["ticker"]: (r.get("sector") or "미분류") for r in rows}
    names = {r["ticker"]: r.get("name") or r["ticker"] for r in rows}
    print(f"[{mk}] {len(rows)}종목 {YEARS}년치 받는 중…", flush=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        got = dict(x for x in ex.map(fetch, [r["ticker"] for r in rows] + [IDX[mk]]) if x[1] is not None)
    ix = got.pop(IDX[mk], None)
    if ix is None or len(got) < 60:
        raise RuntimeError(f"자료 부족 (지수 {'있음' if ix is not None else '없음'}, 종목 {len(got)})")
    C = pd.DataFrame(got).sort_index()
    C = C[C.notna().sum(axis=1) >= len(got) * 0.5]
    I = ix.reindex(C.index).ffill()
    lr = np.log(C).diff()
    m50, m150, m200 = (C.rolling(n, min_periods=n).mean() for n in (50, 150, 200))
    m200p = m200.shift(20)
    hi = C.rolling(252, min_periods=240).max()
    vol = lr.rolling(250, min_periods=200).std() * np.sqrt(252)
    rsv = 2 * C / C.shift(63) + C / C.shift(126) + C / C.shift(189) + C / C.shift(252)
    rs = rsv.rank(axis=1, pct=True) * 99
    peak = C.rolling(250, min_periods=200).max()
    # 1년 최대 낙폭(근사: 250일 창 안에서 고점 대비 최저)
    ddw = (C / peak - 1).rolling(250, min_periods=200).min()
    # 주간 베타(52주)
    wk = np.log(C).diff(5); wi = np.log(I).diff(5)
    cov = wk.rolling(255, min_periods=200).cov(wi); var = wi.rolling(255, min_periods=200).var()
    beta = cov.div(var, axis=0)
    F = C.shift(-STEP) / C - 1
    idxs = [i for i in range(len(C)) if rs.iloc[i].notna().sum() >= 50 and vol.iloc[i].notna().sum() >= 50]
    if not idxs:
        raise RuntimeError("점수를 매길 수 있는 날이 없음")
    evals = list(range(idxs[0], len(C) - STEP, STEP))
    clamp = lambda v, a=0, b=100: v.clip(a, b)

    def pick(i, sty, n, wm):
        px, a50, a150, a200, a200p = C.iloc[i], m50.iloc[i], m150.iloc[i], m200.iloc[i], m200p.iloc[i]
        st2 = (px > a50) & (a50 > a150) & (a150 > a200) & (a200 > a200p)
        hig, ext, g200 = px / hi.iloc[i] - 1, px / a50 - 1, px / a200 - 1
        v, b, R, dd = vol.iloc[i], beta.iloc[i], rs.iloc[i], ddw.iloc[i]
        ok = px.notna() & v.notna() & R.notna() & a200.notna() & F.iloc[i].notna()
        lowv, lowb = clamp((0.6 - v) / 0.45 * 100), clamp((1.2 - b.fillna(1)) / 1.2 * 100)
        trend = st2.astype(float) * 40 + clamp((hig + 0.3) / 0.3 * 30, 0, 30) + (g200 > 0).astype(float) * 30
        el_t = ok & st2 & (hig >= -0.15) & (R >= 75) & (ext <= 0.25)
        el_d = ok & (v <= 0.35) & (g200 > -0.05) & (dd > -0.32) & (b.fillna(1) <= 0.9)
        sc_t = R * 0.45 + trend * 0.3 + clamp(b.fillna(1) * 60) * 0.1
        sc_d = (lowv * 0.35 + lowb * 0.2 + trend * 0.15 + clamp((dd + 0.4) / 0.4 * 100) * 0.15) / 0.85
        legs = {"trend": [(el_t, sc_t, n)], "defense": [(el_d, sc_d, n)],
                "core": [(el_t & (v <= 0.5), sc_t, round(n * 0.6)), (el_d, sc_d, n - round(n * 0.6))]}[sty]
        chosen, cnt = [], {}
        for el, sc, k in legs:
            got_ = 0
            for t in sc[el].sort_values(ascending=False).index:
                if got_ >= k:
                    break
                if t in chosen or cnt.get(sec.get(t), 0) >= 2:
                    continue
                cnt[sec.get(t)] = cnt.get(sec.get(t), 0) + 1; chosen.append(t); got_ += 1
        if not chosen:
            return {}
        w = pd.Series(1.0, index=chosen) if wm == "equal" else 1 / v[chosen].clip(lower=0.1)
        w = w / w.sum()
        for _ in range(20):                                   # 종목당 최대 25%(5종목) · 15%(10종목)
            cap = 0.25 if n <= 5 else 0.15
            over = (w - cap).clip(lower=0).sum()
            if over < 1e-9: break
            w = w.clip(upper=cap); free = w[w < cap]; w[w < cap] += over * free / free.sum()
        return w.to_dict()

    res = {"market": mk, "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
           "period": [C.index[evals[0]].strftime("%Y-%m-%d"), C.index[min(len(C) - 1, evals[-1] + STEP)].strftime("%Y-%m-%d")],
           "tickers": len(got), "rebalances": len(evals), "cost": COST[mk], "configs": {}}
    dates = [C.index[i + STEP].strftime("%Y-%m-%d") for i in evals]
    # 비교 대상
    bx = [float(I.iloc[i + STEP] / I.iloc[i] - 1) for i in evals]
    bu = [float(F.iloc[i][F.iloc[i].notna()].mean()) for i in evals]
    res["bench"] = {"index": {"stats": stats(bx), "r": [round(x, 5) for x in bx]}, "ew": {"stats": stats(bu), "r": [round(x, 5) for x in bu]}}
    res["dates"] = dates
    for sty in ("trend", "defense", "core"):
        for n in (5, 10):
            for wm in ("invvol", "equal"):
                prev, rets, turns, last = {}, [], [], {}
                for i in evals:
                    w = pick(i, sty, n, wm)
                    f = F.iloc[i]
                    g = sum(wt * float(f[t]) for t, wt in w.items()) if w else 0.0
                    turn = sum(abs(w.get(t, 0) - prev.get(t, 0)) for t in set(w) | set(prev)) / 2 if w else 0
                    rets.append(g - turn * 2 * COST[mk]); turns.append(turn); prev = w; last = w
                # 지금 시점 종목(참고)
                now = pick(len(C) - 1 - 0, sty, n, wm) if F.iloc[-1].notna().any() else {}
                key = f"{sty}_{n}_{wm}"
                res["configs"][key] = {"stats": stats(rets), "r": [round(x, 5) for x in rets], "turnover": round(float(np.mean(turns)) * 100, 1),
                                       "beat_index": round(float(np.mean([a > b for a, b in zip(rets, bx)])) * 100),
                                       "last": [{"t": t, "n": names.get(t, t), "w": round(wt, 3)} for t, wt in sorted(last.items(), key=lambda z: -z[1])]}
                s = res["configs"][key]["stats"]
                print(f"[{mk}] {key:16} CAGR {s.get('cagr')}% · MDD {s.get('mdd')}% · 샤프 {s.get('sharpe')} · 회전율 {res['configs'][key]['turnover']}%", flush=True)
    print(f"[{mk}] 지수 {res['bench']['index']['stats']} · 동일가중 {res['bench']['ew']['stats']}")
    return res


def main():
    out = {"generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "note": "가격·변동성 규칙만 검증(밸류·ROE·수급 제외) · 현재 거래대금 상위 종목만(생존편향) · 월 1회(21거래일) 교체 · 비용 반영"}
    for mk in [m.strip() for m in os.getenv("MARKETS", "kr,us").split(",") if m.strip()]:
        try:
            out[mk] = run(mk)
        except Exception as e:
            print(f"[{mk}] 실패: {type(e).__name__}: {e}", flush=True)
    p = os.path.join(OUT, "pb_backtest.json")
    try:                                                       # 이번에 못 한 시장은 지난 결과 유지
        old = json.load(open(p, encoding="utf-8"))
        for mk in ("kr", "us"):
            if mk not in out and mk in old:
                out[mk] = old[mk]
    except Exception:
        pass
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("→", p)


if __name__ == "__main__":
    main()
