#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
옵션 데스크 자료 — CBOE 지연 시세(무료 공개) 옵션 체인으로 종목별 옵션 흐름·감마·변동성 계산

  종목별: 콜·풋 거래량과 거래대금(거래량 × 체결가 × 100), 콜 비중, ATM IV·30일 IV, 가장 가까운 만기 예상 변동폭(ATM 스트래들),
          행사가별 미결제약정·감마 노출(콜 +, 풋 −, 1% 움직일 때 달러), 콜 벽·풋 벽·감마 플립·맥스페인,
          만기별 요약(거래량·미결제 콜/풋, ATM IV, 예상 변동, 맥스페인), IV 기간 구조, 25델타 스큐, IV 스마일,
          포지션 지도(행사가 × 만기: 미결제·거래량·감마), 가까운 만기 옵션 체인, 특이사항(미결제 대비 거래 급증·대형 거래대금)
  기록: output/options_hist.json 에 날마다 쌓아 '평소 대비'(20일 평균 콜 비중), IV 순위·백분위(쌓인 기간 기준)를 계산
  시세 경로: FinanceDataReader 일봉(실제 변동성·차트)

결과: output/market_index.json 의 "opt" (대시보드 동기화가 이 파일을 옮김)
"""
import json, math, os, re, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
ET = timezone(timedelta(hours=-5))
TICK = [t.strip() for t in os.getenv("OPT_TICKERS", "SPX,SPY,QQQ,IWM,NVDA,TSLA,AAPL,MSFT,AMZN,META,GOOGL,AMD,AVGO,MU,PLTR,NFLX,COIN,MSTR,INTC,ORCL,SMCI,IBIT,TLT,BA,QCOM,UNH").split(",") if t.strip()]
IDXSYM = {"SPX": "_SPX", "NDX": "_NDX", "RUT": "_RUT", "VIX": "_VIX"}
FDRSYM = {"SPX": "US500", "NDX": "IXIC", "RUT": "RUT"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
OSYM = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def fetch_chain(t):
    sym = IDXSYM.get(t, t)
    for k in range(3):
        try:
            r = requests.get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json", headers={"User-Agent": UA, "Accept": "application/json"}, timeout=30)
            if r.status_code == 200:
                return t, r.json()
            print(f"[{t}] CBOE {r.status_code}")
        except Exception as e:
            print(f"[{t}] {type(e).__name__}")
        time.sleep(1.5 + k)
    return t, None


def fetch_px(t):
    import FinanceDataReader as fdr
    try:
        df = fdr.DataReader(FDRSYM.get(t, t), (datetime.now() - timedelta(days=420)).strftime("%Y-%m-%d"))
        if df is not None and len(df) > 30:
            return t, df
    except Exception:
        pass
    return t, None


def f(v):
    try:
        v = float(v)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def r2(v, n=2):
    return None if v is None else round(v, n)


def analyze(t, J, px_df):
    D = J.get("data") or {}
    S = f(D.get("current_price")) or f(D.get("close"))
    if not S:
        return None
    today = datetime.now(ET).date()
    rows = []
    for o in D.get("options") or []:
        m = OSYM.match(o.get("option") or "")
        if not m:
            continue
        root, ymd, cp, k = m.groups()
        exp = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
        if exp < today:
            continue
        bid, ask, last = f(o.get("bid")) or 0, f(o.get("ask")) or 0, f(o.get("last_trade_price")) or 0
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last
        px = last if last > 0 else mid
        rows.append({"exp": exp, "cp": cp, "k": int(k) / 1000, "vol": int(f(o.get("volume")) or 0), "oi": int(f(o.get("open_interest")) or 0),
                     "iv": f(o.get("iv")), "delta": f(o.get("delta")), "gamma": f(o.get("gamma")) or 0, "px": px, "mid": mid, "bid": bid, "ask": ask,
                     "chg": f(o.get("percent_change")), "root": root})
    if not rows:
        return None
    exps = sorted({r["exp"] for r in rows})
    dte = lambda e: max((e - today).days, 0)
    # ── 거래량·거래대금 ──
    cv = sum(r["vol"] for r in rows if r["cp"] == "C"); pv = sum(r["vol"] for r in rows if r["cp"] == "P")
    cprem = sum(r["vol"] * r["px"] * 100 for r in rows if r["cp"] == "C"); pprem = sum(r["vol"] * r["px"] * 100 for r in rows if r["cp"] == "P")
    coi = sum(r["oi"] for r in rows if r["cp"] == "C"); poi = sum(r["oi"] for r in rows if r["cp"] == "P")

    def atm_iv(e):
        L = [r for r in rows if r["exp"] == e and r["iv"] and r["iv"] > 0]
        if not L:
            return None
        k0 = min({r["k"] for r in L}, key=lambda k: abs(k - S))
        v = [r["iv"] for r in L if r["k"] == k0]
        return sum(v) / len(v) if v else None

    def straddle(e):
        L = [r for r in rows if r["exp"] == e]
        ks = sorted({r["k"] for r in L})
        if not ks:
            return None
        k0 = min(ks, key=lambda k: abs(k - S))
        c = next((r for r in L if r["k"] == k0 and r["cp"] == "C"), None); p = next((r for r in L if r["k"] == k0 and r["cp"] == "P"), None)
        if not c or not p:
            return None
        v = (c["mid"] or c["px"]) + (p["mid"] or p["px"])
        return v if v > 0 else None

    def max_pain(e):
        L = [r for r in rows if r["exp"] == e and r["oi"] > 0]
        ks = sorted({r["k"] for r in L})
        if not ks:
            return None
        best, bk = None, None
        for K in ks:
            pain = sum(r["oi"] * max(0, K - r["k"]) for r in L if r["cp"] == "C") + sum(r["oi"] * max(0, r["k"] - K) for r in L if r["cp"] == "P")
            if best is None or pain < best:
                best, bk = pain, K
        return bk

    # 만기별 요약(최대 10개)
    exp_rows = []
    for e in exps[:10]:
        L = [r for r in rows if r["exp"] == e]
        cvv = sum(r["vol"] for r in L if r["cp"] == "C"); pvv = sum(r["vol"] for r in L if r["cp"] == "P")
        coo = sum(r["oi"] for r in L if r["cp"] == "C"); poo = sum(r["oi"] for r in L if r["cp"] == "P")
        st, iv = straddle(e), atm_iv(e)
        exp_rows.append({"e": e.isoformat(), "dte": dte(e), "cv": cvv, "pv": pvv, "coi": coo, "poi": poo, "iv": r2(iv * 100 if iv else None, 1),
                         "em": r2(st), "emp": r2(st / S * 100 if st else None, 1), "mp": max_pain(e), "mon": e.weekday() == 4 and 15 <= e.day <= 21})
    near = next((x for x in exp_rows if x["dte"] >= 0 and (x["cv"] + x["pv"]) > 0), exp_rows[0] if exp_rows else None)
    # 30일 IV: CBOE iv30 이 있으면 그것, 없으면 30일 근처 두 만기 ATM IV 보간
    iv30 = f(D.get("iv30"))
    if not iv30:
        pts = [(x["dte"], x["iv"]) for x in exp_rows if x["iv"]]
        lo = max([p for p in pts if p[0] <= 30], default=None, key=lambda p: p[0]); hi = min([p for p in pts if p[0] > 30], default=None, key=lambda p: p[0])
        iv30 = lo[1] if lo and not hi else hi[1] if hi and not lo else (lo[1] + (hi[1] - lo[1]) * (30 - lo[0]) / (hi[0] - lo[0]) if lo and hi and hi[0] != lo[0] else None)
    # ── 감마 노출(1% 움직임당 $) — 콜 +, 풋 − ──
    rng = 0.15 if t not in ("SPX", "SPY", "QQQ", "IWM") else 0.08
    gex = {}
    for r in rows:
        if abs(r["k"] / S - 1) > rng or not r["gamma"]:
            continue
        g = r["gamma"] * r["oi"] * 100 * S * S * 0.01 * (1 if r["cp"] == "C" else -1)
        gex[r["k"]] = gex.get(r["k"], 0) + g
    ks = sorted(gex)
    net_g = sum(gex.values())
    flip = None
    cum = 0
    for k in ks:                                            # 아래에서부터 누적 — 부호가 바뀌는 행사가
        prev = cum
        cum += gex[k]
        if prev < 0 <= cum or prev > 0 >= cum:
            flip = k
    # 콜 벽·풋 벽(미결제 + 감마 기준, 지금 가격 위/아래)
    oik = {}
    for r in rows:
        if abs(r["k"] / S - 1) <= rng:
            a = oik.setdefault(r["k"], [0, 0, 0, 0])
            if r["cp"] == "C":
                a[0] += r["oi"]; a[2] += r["vol"]
            else:
                a[1] += r["oi"]; a[3] += r["vol"]
    cw = sorted([k for k in oik if k >= S], key=lambda k: -oik[k][0])[:2]
    pw = sorted([k for k in oik if k <= S], key=lambda k: -oik[k][1])[:2]
    # 행사가 묶음(너무 촘촘하면 간격을 넓힘)
    step = None
    kk = sorted(oik)
    if kk:
        diffs = sorted(b - a for a, b in zip(kk, kk[1:]) if b > a)
        step = diffs[len(diffs) // 2] if diffs else None
    want = 40
    if step and kk and (kk[-1] - kk[0]) / step > want:
        mult = math.ceil((kk[-1] - kk[0]) / step / want)
        step *= mult
    bucket = (lambda k: round(round(k / step) * step, 4)) if step else (lambda k: k)
    strikes = {}
    for k, (co, po, cvv, pvv) in oik.items():
        b = bucket(k)
        a = strikes.setdefault(b, [0, 0, 0, 0, 0.0])
        a[0] += co; a[1] += po; a[2] += cvv; a[3] += pvv; a[4] += gex.get(k, 0)
    strike_list = [[k, *v[:4], round(v[4])] for k, v in sorted(strikes.items())]
    # 포지션 지도: 행사가(묶음) × 가까운 만기 6개
    hm_exp = [e for e in exps if dte(e) >= 0][:6]
    hm = {}
    for r in rows:
        if r["exp"] not in hm_exp or abs(r["k"] / S - 1) > rng:
            continue
        b = bucket(r["k"])
        a = hm.setdefault((b, r["exp"]), [0, 0, 0, 0, 0.0])
        if r["cp"] == "C":
            a[0] += r["oi"]; a[2] += r["vol"]
        else:
            a[1] += r["oi"]; a[3] += r["vol"]
        a[4] += r["gamma"] * r["oi"] * 100 * S * S * 0.01 * (1 if r["cp"] == "C" else -1)
    hm_k = sorted({k for k, _ in hm})
    heat = {"k": hm_k, "e": [e.isoformat() for e in hm_exp],
            "v": [[[*(hm.get((k, e)) or [0, 0, 0, 0])[:4], round((hm.get((k, e)) or [0, 0, 0, 0, 0])[4])] for e in hm_exp] for k in hm_k]}
    # IV 스마일: 가까운 만기 3개
    smile = []
    for e in [e for e in exps if dte(e) >= 1][:3] or exps[:3]:
        pts = {}
        for r in rows:
            if r["exp"] != e or not r["iv"] or r["iv"] <= 0 or abs(r["k"] / S - 1) > rng:
                continue
            if (r["cp"] == "P" and r["k"] <= S) or (r["cp"] == "C" and r["k"] >= S):   # OTM 쪽만
                pts[r["k"]] = r["iv"] * 100
        smile.append({"e": e.isoformat(), "pts": [[k, round(v, 1)] for k, v in sorted(pts.items())]})
    # 25델타 스큐: 25~45일 만기(없으면 가장 가까운 월물)
    sk_e = next((e for e in exps if 20 <= dte(e) <= 50), exps[min(len(exps) - 1, 3)])
    def iv_at_delta(cp, target):
        L = [r for r in rows if r["exp"] == sk_e and r["cp"] == cp and r["delta"] is not None and r["iv"]]
        if not L:
            return None
        b = min(L, key=lambda r: abs(r["delta"] - target))
        return b["iv"] * 100
    p25, c25 = iv_at_delta("P", -0.25), iv_at_delta("C", 0.25)
    skew = r2(p25 - c25, 1) if p25 is not None and c25 is not None else None
    # 옵션 체인: 가까운 만기 4개 × 지금 가격 근처 행사가 ±14
    chain = []
    for e in [e for e in exps if dte(e) >= 0][:5]:
        L = [r for r in rows if r["exp"] == e]
        kset = sorted({r["k"] for r in L})
        if not kset:
            continue
        i0 = min(range(len(kset)), key=lambda i: abs(kset[i] - S))
        ksel = kset[max(0, i0 - 14): i0 + 15]
        line = []
        for K in ksel:
            c = next((r for r in L if r["k"] == K and r["cp"] == "C"), None); p = next((r for r in L if r["k"] == K and r["cp"] == "P"), None)
            pack = lambda x: [r2(x["px"]), r2(x["chg"], 1), r2(x["bid"]), r2(x["ask"]), x["vol"], x["oi"], r2(x["iv"] * 100 if x["iv"] else None, 1), r2(x["delta"], 2)] if x else None
            line.append([K, pack(c), pack(p)])
        chain.append({"e": e.isoformat(), "rows": line})
    # 특이사항: 미결제 대비 거래 급증·대형 거래대금
    unusual = []
    for r in rows:
        prem = r["vol"] * r["px"] * 100
        if prem < 1e6 or r["vol"] < 500:
            continue
        ratio = r["vol"] / r["oi"] if r["oi"] > 0 else 99
        lvl = 3 if prem >= 2e7 or (ratio >= 10 and prem >= 5e6) else 2 if prem >= 5e6 or ratio >= 5 else 1 if ratio >= 2 or prem >= 3e6 else 0
        if not lvl:
            continue
        why = []
        if ratio >= 2:
            why.append(f"새 포지션 급증 · 미결제 {'없음' if r['oi'] == 0 else f'대비 {ratio:.1f}배'}")
        if prem >= 2e7:
            why.append("초대형 거래대금")
        unusual.append({"t": t, "e": r["exp"].isoformat(), "k": r["k"], "cp": r["cp"], "prem": round(prem), "vol": r["vol"], "oi": r["oi"], "ratio": round(ratio, 1),
                        "iv": r2(r["iv"] * 100 if r["iv"] else None, 1), "px": r2(r["px"]), "otm": round((r["k"] / S - 1) * 100, 1), "lvl": lvl, "why": " · ".join(why) or "대형 거래대금"})
    unusual.sort(key=lambda u: (-u["lvl"], -u["prem"]))
    # 실제 변동성·시세(최근 120일)
    rv20 = None
    ohlc = None
    if px_df is not None and len(px_df) > 25:
        c = [float(x) for x in px_df["Close"].tolist()]
        lr = [math.log(c[i] / c[i - 1]) for i in range(1, len(c)) if c[i] > 0 and c[i - 1] > 0]
        if len(lr) >= 20:
            m = sum(lr[-20:]) / 20
            rv20 = math.sqrt(sum((x - m) ** 2 for x in lr[-20:]) / 19) * math.sqrt(252) * 100
        tail = px_df.tail(260)
        ohlc = {"d": [d.strftime("%y%m%d") for d in tail.index], **{k[0].lower(): [r2(float(v)) for v in tail[k]] for k in ("Open", "High", "Low", "Close") if k in tail},
                "v": [int(v) for v in tail["Volume"]] if "Volume" in tail else []}
    em_d = near["em"] if near else None
    out = {"t": t, "px": r2(S), "chg": r2(f(D.get("price_change_percent")), 2), "asof": J.get("timestamp"),
           "cv": cv, "pv": pv, "cprem": round(cprem), "pprem": round(pprem), "coi": coi, "poi": poi,
           "cshare": r2(cprem / (cprem + pprem) * 100 if cprem + pprem else None, 1),
           "atm": near["iv"] if near else None, "iv30": r2(iv30 * (100 if iv30 and iv30 < 5 else 1) if iv30 else None, 1), "rv20": r2(rv20, 1),
           "near": near, "em": r2(em_d), "emp": near["emp"] if near else None,
           "net_g": round(net_g), "flip": flip, "cw": cw, "pw": pw, "mp": near["mp"] if near else None,
           "skew": skew, "skew_e": sk_e.isoformat(), "exps": exp_rows, "strikes": strike_list, "heat": heat, "smile": smile, "chain": chain,
           "unusual": unusual[:30], "ohlc": ohlc}
    return out


def main():
    p = os.path.join(OUT, "market_index.json")
    mi = json.load(open(p, encoding="utf-8"))
    hp = os.path.join(OUT, "options_hist.json")
    try:
        hist = json.load(open(hp, encoding="utf-8"))
    except Exception:
        hist = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        chains = dict(ex.map(fetch_chain, TICK))
    with ThreadPoolExecutor(max_workers=6) as ex:
        pxs = dict(ex.map(fetch_px, TICK))
    res, first = {}, True
    for t in TICK:
        J = chains.get(t)
        if not J:
            continue
        if first:
            D = J.get("data") or {}
            print("CBOE 응답 예시:", t, {k: v for k, v in D.items() if k != "options"}, (D.get("options") or [None])[0])
            first = False
        try:
            a = analyze(t, J, pxs.get(t))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[{t}] 분석 실패 {type(e).__name__}: {e}")
            a = None
        if a:
            res[t] = a
            print(f"[{t}] {a['px']} · 콜 {a['cprem'] / 1e6:,.0f}M 풋 {a['pprem'] / 1e6:,.0f}M · 콜비중 {a['cshare']}% · ATM IV {a['atm']} · 30일 {a['iv30']} · RV {a['rv20']} · "
                  f"예상 ±{a['emp']}% · 순감마 {a['net_g'] / 1e9:.2f}B · 플립 {a['flip']} · 콜벽 {a['cw']} 풋벽 {a['pw']} · 맥스페인 {a['mp']} · 스큐 {a['skew']} · 특이 {len(a['unusual'])}", flush=True)
    if not res:
        print("옵션 자료를 하나도 못 받음 — 기존 값 유지")
        return
    # 기록·평소 대비·IV 순위
    day = datetime.now(ET).strftime("%Y-%m-%d") if datetime.now(ET).hour >= 16 else (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    for t, a in res.items():
        H = hist.setdefault(t, [])
        rec = {"d": day, "cs": a["cshare"], "iv": a["iv30"], "atm": a["atm"], "px": a["px"], "prem": a["cprem"] + a["pprem"], "pc": round(a["pv"] / a["cv"], 2) if a["cv"] else None, "g": a["net_g"]}
        H[:] = [h for h in H if h["d"] != day] + [rec]
        H.sort(key=lambda h: h["d"])
        del H[:-400]
        prev = [h for h in H if h["d"] < day][-20:]
        cs_avg = sum(h["cs"] for h in prev if h.get("cs") is not None) / max(1, len([h for h in prev if h.get("cs") is not None])) if prev else None
        a["cs_avg"] = r2(cs_avg, 1) if prev else None
        a["cs_vs"] = r2(a["cshare"] - cs_avg, 1) if prev and a["cshare"] is not None else None
        a["prem_avg"] = round(sum(h["prem"] for h in prev) / len(prev)) if prev else None
        ivs = [h["iv"] for h in H if h.get("iv") is not None]
        if len(ivs) >= 10 and a["iv30"] is not None:
            lo, hi = min(ivs), max(ivs)
            a["ivr"] = round((a["iv30"] - lo) / (hi - lo) * 100) if hi > lo else 50
            a["ivp"] = round(sum(1 for v in ivs if v <= a["iv30"]) / len(ivs) * 100)
        a["hist_n"] = len(H)
        a["hist"] = [[h["d"][2:].replace("-", ""), h.get("cs"), h.get("iv"), h.get("pc")] for h in H[-60:]]
    json.dump(hist, open(hp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    allU = sorted([u for a in res.values() for u in a["unusual"]], key=lambda u: (-u["lvl"], -u["prem"]))
    tc = sum(a["cprem"] for a in res.values()); tp = sum(a["pprem"] for a in res.values())
    mi["opt"] = {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "day": day, "src": "CBOE 지연 시세(15분) · 장 마감 뒤 스냅샷",
                 "sum": {"cprem": tc, "pprem": tp, "cv": sum(a["cv"] for a in res.values()), "pv": sum(a["pv"] for a in res.values()),
                         "cshare": round(tc / (tc + tp) * 100, 1) if tc + tp else None, "n": len(res), "unusual": len(allU)},
                 "top": allU[:40], "t": res}
    json.dump(mi, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"→ {p} opt · {len(res)}종목 · 특이 {len(allU)}건 · 크기 {len(json.dumps(mi['opt'])) // 1024}KB")


if __name__ == "__main__":
    main()
