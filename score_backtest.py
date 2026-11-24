#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
점수 백테스트 — "종목 진단 점수가 높았을 때 그 뒤 주가가 실제로 좋았나?"

대시보드 종목 진단의 타이밍 점수(추세 30 · 위치 25 · 시장 15 · 모멘텀 10, 수급 20은 과거 자료가 없어 뺌)를
가격·거래량만으로 과거 매주 다시 계산하고, 그 뒤 1주(5거래일)·1개월(20)·3개월(60) 수익률과 비교한다.
  - 점수 구간별(80↑, 70대 …) 평균·중앙값·승률·같은 날 유니버스 대비 초과수익
  - 판정별(매수 타이밍·돌파 대기·관심·추격 금지·관망·매수 부적합)
  - 추세추종 대가 6인(리버모어·오닐·미너비니·다르바스·와인스타인·터틀)별 '부합'일 때 vs 아닐 때
  - 전략: 4주마다 점수 Top 10 / 80점 이상 전부를 사서 4주 보유 vs 유니버스 동일가중
  - 종목별 주간 점수·종가(최근 약 2.5년) — 대시보드에서 점수 흐름과 주가를 겹쳐 본다
한계(화면에도 표시): 수급·실적·거래대금 같은 비가격 항목과 오닐 CAN SLIM 의 실적 항목은 빠짐, 시장 우호도는 지수 추세로 근사,
지금 거래대금 상위 종목만 써서 생존편향, 거래비용 미반영.

입력: output/universe_{kr,us}.json   출력: output/score_bt.json 과 output/market_index.json 의 "sbt"
"""
import json, os, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
N_TICK = int(os.getenv("SBT_TICKERS", "600"))
YEARS = float(os.getenv("SBT_YEARS", "4.3"))
STEP = 5                                   # 주 1회
KEEP_W = int(os.getenv("SBT_KEEP_WEEKS", "130"))   # 종목별 시계열로 내보낼 주 수(약 2.5년)
IDX = {"kr": "KS11", "us": "US500"}
W = {"trend": 30, "pos": 25, "mkt": 15, "mom": 10}
MASTERS = [("livermore", "리버모어"), ("oneil", "오닐"), ("minervini", "미너비니"), ("darvas", "다르바스"), ("weinstein", "와인스타인"), ("turtle", "터틀")]


def fetch(code):
    import FinanceDataReader as fdr
    start = (datetime.now() - timedelta(days=int(365 * YEARS))).strftime("%Y-%m-%d")
    for k in range(3):
        try:
            df = fdr.DataReader(code, start)
            if df is not None and len(df) > 300 and "Close" in df:
                df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float) if "Volume" in df else df[["Close"]].astype(float)
                df = df[df["Close"] > 0]
                df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
                return code, df[~df.index.duplicated(keep="last")]
        except Exception:
            time.sleep(1 + k)
    return code, None


def ratio(*items):
    """대가 체크리스트: 판단 가능한(NaN 아닌) 항목 중 통과 비율. 판단 가능한 항목이 절반 미만이면 NaN."""
    ok = sum(i.fillna(0).astype(float) for i in items)
    ev = sum(i.notna().astype(float) for i in items)
    r = ok / ev.replace(0, np.nan)
    return r.where(ev >= max(2, int(np.ceil(len(items) * 0.6))))


def b(cond, valid):
    """조건(bool DataFrame)을 판단 불가(NaN) 처리와 함께 float 로"""
    return cond.astype(float).where(valid)


def cs_base(c, L):
    """대시보드 csBase 와 같은 규칙: 최근 250일 고점(피벗)·베이스 길이·깊이 → (ok, gap)"""
    if L < 60:
        return False, np.nan
    a = max(0, L - 250)
    w = c[a:L]
    if not len(w) or not np.isfinite(w).all():
        w = np.where(np.isfinite(w), w, -np.inf)
    j = a + int(len(w) - 1 - np.argmax(w[::-1]))         # 마지막 최고점
    H = c[j]
    k = j
    if L - j <= 5 and j - 5 >= a:
        w2 = c[a:j - 4]
        if len(w2):
            j2 = a + int(len(w2) - 1 - np.argmax(w2[::-1]))
            if j - j2 >= 20 and c[j2] >= H * 0.9:
                k, H = j2, c[j2]
    seg = c[k + 1:L + 1]
    lo = np.nanmin(seg) if len(seg) else np.nan
    depth = 1 - lo / H if H > 0 else np.nan
    pl = np.nanmin(c[max(0, k - 120):k]) if k > 0 else np.nan
    prior = H / pl - 1 if pl and pl > 0 else None
    ln = L - k
    flat = depth <= 0.15
    ok = ln >= (25 if flat else 35) and depth <= 0.35 and (prior is None or prior >= 0.3)
    return bool(ok), c[L] / H - 1


def stats(r, step):
    r = np.array([x for x in r if np.isfinite(x)], float)
    if not len(r):
        return {}
    eq = np.cumprod(1 + r)
    yrs = len(r) * step / 252
    dd = eq / np.maximum.accumulate(eq) - 1
    sd = r.std(ddof=1) if len(r) > 1 else 0
    return {"cagr": round((eq[-1] ** (1 / yrs) - 1) * 100, 1) if yrs > 0 else None, "total": round((eq[-1] - 1) * 100, 1),
            "mdd": round(dd.min() * 100, 1), "sharpe": round(r.mean() / sd * np.sqrt(252 / step), 2) if sd > 0 else None,
            "win": round((r > 0).mean() * 100), "n": int(len(r))}


def run(mk):
    us = mk == "us"
    u = json.load(open(os.path.join(OUT, f"universe_{mk}.json"), encoding="utf-8"))
    rows = sorted((r for r in u["rows"] if not r.get("is_etf") and r.get("ticker")), key=lambda r: -(r.get("value") or 0))[:N_TICK]
    names = {r["ticker"]: r.get("name") or r["ticker"] for r in rows}
    print(f"[{mk}] {len(rows)}종목 {YEARS}년치 받는 중…", flush=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        got = dict(x for x in ex.map(fetch, [r["ticker"] for r in rows] + [IDX[mk]]) if x[1] is not None)
    ixd = got.pop(IDX[mk], None)
    if ixd is None or len(got) < 60:
        raise RuntimeError(f"자료 부족 (지수 {'있음' if ixd is not None else '없음'}, 종목 {len(got)})")
    C = pd.DataFrame({t: d["Close"] for t, d in got.items()}).sort_index()
    keep_days = C.notna().sum(axis=1) >= len(got) * 0.5
    C = C[keep_days]
    V = pd.DataFrame({t: d["Volume"] for t, d in got.items() if "Volume" in d}).reindex(C.index)
    H_ = pd.DataFrame({t: d["High"] for t, d in got.items() if "High" in d}).reindex(C.index)
    L_ = pd.DataFrame({t: d["Low"] for t, d in got.items() if "Low" in d}).reindex(C.index)
    V, H_, L_ = V.reindex(columns=C.columns), H_.reindex(columns=C.columns), L_.reindex(columns=C.columns)
    I = ixd["Close"].reindex(C.index).ffill()
    print(f"[{mk}] 패널 {C.shape} · 지수 {I.notna().sum()}일", flush=True)

    # ── 지표(대시보드 lxPrep 과 같은 정의) ──
    ma = lambda n: C.rolling(n, min_periods=n).mean()
    m50, m150, m200 = ma(50), ma(150), ma(200)
    m50p, m150p, m200p = m50.shift(10), m150.shift(20), m200.shift(20)
    hi, lo = C.rolling(250, min_periods=200).max(), C.rolling(250, min_periods=200).min()
    h20 = C.rolling(20).max()
    h20p, h55p, l10p = C.shift(1).rolling(20).max(), C.shift(1).rolling(55).max(), C.shift(1).rolling(10).min()
    box = C.shift(4).rolling(20).max() / C.shift(4).rolling(20).min() - 1
    brk3 = (C > C.shift(1).rolling(20).max()).astype(float).rolling(3).max() > 0
    w2new = (C < m150).astype(float).shift(5).rolling(56).max() > 0
    ret = lambda k: C / C.shift(k) - 1
    rsv = 2 * ret(63) + ret(126) + ret(189) + ret(249)
    rs = (rsv.rank(axis=1, pct=True) * 99).clip(lower=1).round()
    vavg = V.shift(6).rolling(50).mean()
    upv = V.where(C > C.shift(1))
    bvx = upv.rolling(5, min_periods=1).max() / vavg
    volx20 = V / V.rolling(20).mean()
    volUp = (volx20 >= 1.4).astype(float).where(volx20.notna())
    tr = pd.concat([(H_ - L_), (H_ - C.shift(1)).abs(), (L_ - C.shift(1)).abs()]).groupby(level=0).max().reindex(C.index)
    atr20 = tr.rolling(20).mean() / C * 100
    dr = (C / C.shift(1) - 1).where(lambda x: x.abs() < 0.35)
    lvl = (1 + dr.mean(axis=1).fillna(0)).cumprod()
    rl = C.div(lvl, axis=0)
    rslVsAvg = rl / rl.shift(1).rolling(249, min_periods=60).mean() - 1
    # 시장: 지수 추세(대시보드 시장 우호도의 근사)
    im50, im200 = I.rolling(50).mean(), I.rolling(200).mean()
    mup = (I > im50) | (im50 > im50.shift(10))
    fav = pd.Series(np.select([(I > im50) & (im50 > im200) & (im50 > im50.shift(10)), I > im200, (I > im50)],
                              [72, 55, 45], 25), index=I.index).where(im200.notna())
    MUP = pd.DataFrame(np.repeat(mup.values[:, None], C.shape[1], axis=1), index=C.index, columns=C.columns)
    has200 = m200p.notna()
    v = C.notna()

    # ── 대가 6인 ──
    M = {}
    M["livermore"] = ratio(b(C / hi >= 0.95, hi.notna()), b((C > m50) & (m50 > m200), has200), b(C >= h20 * 0.97, v), volUp, b(MUP, v))
    M["oneil"] = ratio(b(C >= 0.85 * hi, hi.notna()), b(rs >= 80, rs.notna()), volUp, b(MUP, v), b(C > m50, m50.notna()))
    M["minervini"] = ratio(b((C > m150) & (C > m200), has200), b(m150 > m200, has200), b(m200 > m200p, has200),
                           b((m50 > m150) & (m50 > m200), has200), b(C > m50, m50.notna()), b(C / lo - 1 >= 0.30, lo.notna()),
                           b(C / hi - 1 >= -0.25, hi.notna()), b(rs >= 70, rs.notna()))
    M["darvas"] = ratio(b(C / hi >= 0.95, hi.notna()), b(box <= (0.15 if us else 0.20), box.notna()), b(brk3, box.notna()),
                        b(bvx >= 1.5, bvx.notna()) if bvx.notna().any().any() else volUp)
    M["weinstein"] = ratio(b(C > m150, m150.notna()), b(m150 > m150p, m150p.notna()), b(w2new & (C > m150), m150p.notna()),
                           b(bvx >= 2, bvx.notna()), b(rslVsAvg > 0, rslVsAvg.notna()), b(~((C < m150) & (m150 < m150p)), m150p.notna()))
    M["turtle"] = ratio(b(C > h20p, h20p.notna()), b(C > h55p, h55p.notna()), b(C > l10p, l10p.notna()), b(2 * atr20 <= 15, atr20.notna()))
    trend = sum(M[k].fillna(0) for k, _ in MASTERS) / len(MASTERS) * 100
    ext = C / m50 - 1
    g200 = C / m200 - 1
    R20, DH = ret(20), C / hi - 1
    F5, F20, F60 = C.shift(-5) / C - 1, C.shift(-20) / C - 1, C.shift(-60) / C - 1

    # 주간 표본(지표가 충분한 날부터)
    first = int(np.argmax((m200p.notna().sum(axis=1) >= 50).values))
    sidx = list(range(first, len(C), STEP))
    if sidx[-1] != len(C) - 1:
        sidx.append(len(C) - 1)                                  # 마지막 날(오늘) 포함
    carr = {t: C[t].values for t in C.columns}
    recs = []
    for i in sidx:
        d = C.index[i]
        fv = fav.iloc[i] if np.isfinite(fav.iloc[i]) else 50
        for t in C.columns:
            px = carr[t][i]
            if not np.isfinite(px) or not np.isfinite(m200p.iat[i, C.columns.get_loc(t)]):
                continue
            j = C.columns.get_loc(t)
            tr_ = float(trend.iat[i, j])
            ok, gap = cs_base(carr[t], i)
            e, g2 = ext.iat[i, j], g200.iat[i, j]
            if (ok and gap > 0.05) or (np.isfinite(e) and e > 0.25):
                zone = "hot"
            elif ok and 0 <= gap <= 0.05:
                zone = "buy"
            elif ok and -0.05 <= gap < 0:
                zone = "pivot"
            elif tr_ >= 60:
                zone = "mtrend"
            elif tr_ >= 45:
                zone = "watch"
            else:
                zone = "weak"
            pos = {"buy": 90, "pivot": 80, "hot": 25, "mtrend": 60, "weak": 15}.get(zone, 50)
            if np.isfinite(e) and e > 0.25:
                pos = min(pos, 25)
            if np.isfinite(g2) and g2 < 0:
                pos = min(pos, 25)
            r_ = rs.iat[i, j]
            mom = float(r_) if np.isfinite(r_) else 50
            score = round((tr_ * W["trend"] + pos * W["pos"] + fv * W["mkt"] + mom * W["mom"]) / sum(W.values()))
            if zone == "weak" or (np.isfinite(g2) and g2 < 0 and tr_ < 50):
                vc = "no"
            elif zone == "hot" or (np.isfinite(e) and e > 0.25):
                vc = "hot"
            elif score >= 70 and zone in ("buy", "pivot") and fv >= 40:
                vc = "go" if zone == "buy" else "pivot"
            elif score >= 55:
                vc = "wait"
            else:
                vc = "pass"
            recs.append((i, t, score, vc, *(float(M[k].iat[i, j]) if np.isfinite(M[k].iat[i, j]) else np.nan for k, _ in MASTERS),
                         F5.iat[i, j], F20.iat[i, j], F60.iat[i, j], px,
                         tr_, pos, fv, mom, float(R20.iat[i, j]), float(atr20.iat[i, j]), float(DH.iat[i, j])))
    cols = ["i", "t", "score", "vc"] + [k for k, _ in MASTERS] + ["f5", "f20", "f60", "px", "c_trend", "c_pos", "c_mkt", "c_mom", "r20", "atr", "dh"]
    D = pd.DataFrame(recs, columns=cols)
    print(f"[{mk}] 표본 {len(D):,}개 · {D['i'].nunique()}주", flush=True)
    for h in ("f5", "f20", "f60"):                               # 같은 날 유니버스 평균 대비 초과수익
        D["x" + h[1:]] = D[h] - D.groupby("i")[h].transform("mean")

    def agg(g):
        o = {"n": int(len(g))}
        for h in ("f5", "f20", "f60"):
            s = g[h].dropna()
            if len(s) >= 20:
                o[h] = round(s.mean() * 100, 2)
                o["m" + h[1:]] = round(s.median() * 100, 2)
                o["hit" + h[1:]] = round((s > 0).mean() * 100)
                xs = g["x" + h[1:]].dropna()
                o["x" + h[1:]] = round(xs.mean() * 100, 2)
                o["xm" + h[1:]] = round(xs.median() * 100, 2)        # 같은 날 평균 대비 초과수익의 중앙값
                o["xhit" + h[1:]] = round((xs > 0).mean() * 100)     # 같은 날 평균을 이긴 비율
        return o

    bins = [(80, 101, "80점 이상"), (70, 80, "70점대"), (60, 70, "60점대"), (50, 60, "50점대"), (40, 50, "40점대"), (0, 40, "40점 미만")]
    buckets = [dict(lab=lab, lo=a, **agg(D[(D.score >= a) & (D.score < b_)])) for a, b_, lab in bins]
    vlab = {"go": "매수 타이밍", "pivot": "돌파 대기", "wait": "관심 · 대기", "hot": "추격 금지", "pass": "관망", "no": "매수 부적합"}
    verdicts = [dict(k=k, lab=lab, **agg(D[D.vc == k])) for k, lab in vlab.items()]
    masters = []
    for k, nm in MASTERS:
        yes, no = D[D[k] >= 0.85], D[(D[k] < 0.85) & D[k].notna()]
        masters.append({"k": k, "n": nm, "yes": agg(yes), "no": agg(no)})
    allv = agg(D)
    # 점수 높을수록 좋았나: 점수와 1개월 초과수익의 순위상관(주별 평균)
    ic = D.dropna(subset=["f20"]).groupby("i").apply(lambda g: g["score"].rank().corr(g["f20"].rank()) if len(g) > 30 else np.nan).dropna()
    # 전략: 4주(20거래일)마다 Top10 / 80점 이상 동일가중 보유
    reb = sorted(D["i"].unique())[::4]
    curve, top_r, hi_r, uni_r, e1, e2, e3 = [], [], [], [], 1.0, 1.0, 1.0
    for i in reb:
        g = D[(D.i == i) & D.f20.notna()]
        if len(g) < 30:
            continue
        top = g.sort_values(["score"], ascending=False).head(10)
        h = g[g.score >= 80]
        tr_, ur = top.f20.mean(), g.f20.mean()
        hr = h.f20.mean() if len(h) else 0.0
        top_r.append(tr_); hi_r.append(hr); uni_r.append(ur)
        e1 *= 1 + tr_; e2 *= 1 + hr; e3 *= 1 + ur
        curve.append([C.index[min(len(C) - 1, i + 20)].strftime("%Y-%m-%d"), round(e1, 4), round(e2, 4), round(e3, 4), int(len(h))])
    # 종목별 주간 시계열(최근 KEEP_W 주)
    last_is = sorted(D["i"].unique())[-KEEP_W:]
    Dl = D[D.i.isin(last_is)]
    dates = [C.index[i].strftime("%y%m%d") for i in last_is]
    pos_of = {i: k for k, i in enumerate(last_is)}
    ser = {}
    for t, g in Dl.groupby("t"):
        s, p = [""] * len(last_is), [""] * len(last_is)
        for row in g.itertuples(index=False):
            k = pos_of[row.i]
            s[k] = str(int(row.score))
            p[k] = f"{row.px:.4g}" if row.px < 1e5 else str(int(round(row.px)))
        ser[t] = {"n": names.get(t, t), "s": ",".join(s), "p": ",".join(p)}
    now = D[D.i == D.i.max()].sort_values("score", ascending=False)
    ex = experiment(D)
    if ex:
        ex["cut"] = C.index[int(ex["cut"])].strftime("%Y-%m-%d")
    res = {"market": mk, "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
           "period": [C.index[sidx[0]].strftime("%Y-%m-%d"), C.index[-1].strftime("%Y-%m-%d")], "tickers": int(C.shape[1]),
           "weeks": int(D["i"].nunique()), "samples": int(len(D)), "all": allv, "buckets": buckets, "verdicts": verdicts, "masters": masters,
           "ic": {"mean": round(float(ic.mean()), 3) if len(ic) else None, "pos": round(float((ic > 0).mean() * 100)) if len(ic) else None, "n": int(len(ic))},
           "strat": {"top10": stats(top_r, 20), "hi80": stats(hi_r, 20), "univ": stats(uni_r, 20), "curve": curve},
           "now": [[r.t, int(r.score)] for r in now.head(20).itertuples()],
           "dates": dates, "s": ser, "w": W, "exp": ex}
    b80 = buckets[0]
    print(f"[{mk}] 80점↑ {b80} · 전체 {allv} · IC {res['ic']} · Top10 {res['strat']['top10']} · 유니버스 {res['strat']['univ']}", flush=True)
    return res


def _wk_corr(D, a, b):
    """주별(같은 날) 순위상관 — a, b 는 이미 주별 순위. 반환: 주별 상관 Series"""
    g = D["i"]
    ac = a - a.groupby(g).transform("mean")
    bc = b - b.groupby(g).transform("mean")
    num = (ac * bc).groupby(g).sum()
    den = np.sqrt((ac * ac).groupby(g).sum() * (bc * bc).groupby(g).sum())
    n = g.groupby(g).size()
    return (num / den.replace(0, np.nan))[n > 30].dropna()


def experiment(D):
    """가중치 실험 — 같은 날 종목끼리 순위를 얼마나 잘 맞히나(시장 효과 제거).
    시장 항목(c_mkt)은 같은 날 모든 종목에 같은 값이라 순위에는 영향이 없다 → 실험에서 뺀다.
    후보 항목: 추세·위치·모멘텀(지금 점수의 항목) + 단기 되돌림(최근 20일 덜 오른 쪽) · 낮은 변동성 · 52주 고점 근접.
    앞 절반 기간으로 고르고 뒤 절반 기간으로 확인(과최적화 점검)."""
    X = D.dropna(subset=["f60", "c_trend", "c_pos", "c_mom"]).copy()
    if len(X) < 5000:
        return None
    g = X["i"]
    pr = lambda col, asc=True: X[col].groupby(g).rank(pct=True, ascending=asc) * 100
    comp = {"trend": X["c_trend"], "pos": X["c_pos"], "mom": X["c_mom"],
            "rev": pr("r20", False).fillna(50), "lowvol": pr("atr", False).fillna(50), "high": pr("dh").fillna(50)}
    rf60, rf20 = X["f60"].groupby(g).rank(), X["f20"].groupby(g).rank()
    weeks = sorted(X["i"].unique())
    cut = weeks[len(weeks) // 2]
    fx = X["f60"] - X["f60"].groupby(g).transform("mean")
    fx20 = X["f20"] - X["f20"].groupby(g).transform("mean")
    reb = set(weeks[::4])

    def ev(w):
        sc = sum(comp[k] * v for k, v in w.items() if v) / max(1e-9, sum(w.values()))
        sc = sc + np.random.default_rng(0).random(len(sc)) * 1e-6           # 동점 깨기
        rk = sc.groupby(g).rank()
        ic60, ic20 = _wk_corr(X, rk, rf60), _wk_corr(X, rk, rf20)
        q = sc.groupby(g).rank(pct=True)
        top, bot = fx[q >= 0.9].mean() * 100, fx[q <= 0.1].mean() * 100
        t10 = X.assign(sc=sc, fx20=fx20)[X["i"].isin(reb)].sort_values("sc", ascending=False).groupby("i").head(10).groupby("i")["fx20"].mean()
        A, B_ = ic60[ic60.index < cut], ic60[ic60.index >= cut]
        return {"ic60": round(float(ic60.mean()), 3), "ic20": round(float(ic20.mean()), 3), "pos60": round(float((ic60 > 0).mean() * 100)),
                "ic60a": round(float(A.mean()), 3) if len(A) else None, "ic60b": round(float(B_.mean()), 3) if len(B_) else None,
                "top": round(float(top), 2), "bot": round(float(bot), 2), "spread": round(float(top - bot), 2),
                "t10x": round(float(t10.mean() * 100), 2) if len(t10) else None}

    named = [("현재 점수(추세30·위치25·모멘텀10)", {"trend": 30, "pos": 25, "mom": 10}),
             ("추세만", {"trend": 1}), ("위치만", {"pos": 1}), ("모멘텀(RS)만", {"mom": 1}),
             ("단기 되돌림만", {"rev": 1}), ("낮은 변동성만", {"lowvol": 1}), ("52주 고점 근접만", {"high": 1}),
             ("위치 제외(추세30·모멘텀10)", {"trend": 30, "mom": 10}),
             ("모멘텀 강화(추세30·위치10·모멘텀30)", {"trend": 30, "pos": 10, "mom": 30}),
             ("모멘텀 중심(추세20·위치5·모멘텀40)", {"trend": 20, "pos": 5, "mom": 40}),
             ("모멘텀 중심(추세10·모멘텀50)", {"trend": 10, "mom": 50}),
             ("모멘텀 중심(추세20·모멘텀40)", {"trend": 20, "mom": 40})]
    out = [dict(n=n, w=w, **ev(w)) for n, w in named]
    # 격자 탐색: 앞 절반 IC(3개월) 로만 고른다
    best, bw = None, None
    for a in range(4):
        for b_ in range(4):
            for c in range(4):
                for d in range(3):
                    for e in range(3):
                        for f in range(3):
                            w = {"trend": a, "pos": b_, "mom": c, "rev": d, "lowvol": e, "high": f}
                            if not sum(w.values()):
                                continue
                            sc = sum(comp[k] * v for k, v in w.items() if v)
                            rk = sc.groupby(g).rank()
                            ic = _wk_corr(X, rk, rf60)
                            tr = float(ic[ic.index < cut].mean())
                            if best is None or tr > best:
                                best, bw = tr, w
    tot = sum(bw.values())
    bwp = {k: round(v / tot * 100) for k, v in bw.items() if v}
    out.append(dict(n="최적(앞 절반으로 고름)", w=bwp, best=True, **ev(bw)))
    print("[exp]", [(o["n"], o["ic60"], o["ic60a"], o["ic60b"], o["spread"]) for o in out], flush=True)
    return {"schemes": out, "cut": str(cut), "weeks": len(weeks)}


def main():
    out = {"generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
           "note": "가격·거래량으로 다시 계산한 종목 진단 점수(추세30·위치25·시장15·모멘텀10, 수급·실적 제외) · 매주 표본 · 현재 거래대금 상위 종목만(생존편향) · 비용 미반영"}
    for mk in [m.strip() for m in os.getenv("MARKETS", "us,kr").split(",") if m.strip()]:
        try:
            out[mk] = run(mk)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[{mk}] 실패: {type(e).__name__}: {e}", flush=True)
    p = os.path.join(OUT, "score_bt.json")
    try:
        old = json.load(open(p, encoding="utf-8"))
        for mk in ("kr", "us"):
            if mk not in out and mk in old:
                out[mk] = old[mk]
    except Exception:
        pass
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    mp = os.path.join(OUT, "market_index.json")
    mi = json.load(open(mp, encoding="utf-8"))
    mi["sbt"] = out
    json.dump(mi, open(mp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("→", p, "· market_index.json sbt")


if __name__ == "__main__":
    main()
