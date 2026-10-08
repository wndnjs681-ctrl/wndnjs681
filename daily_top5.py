#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
추세추종·모멘텀 TOP5 — 국내·미국 각각 (매일 아침 리포트용)

입력: output/universe_{kr,us}.json (지표) + output/series_{kr,us}.json (일봉 250개)
출력: output/top5_{kr,us}.json

1) 유동성: 국내 시총 2,000억↑·20일 평균 거래대금 30억↑ / 미국 시총 $2B↑·20일 평균 거래대금 $20M↑
2) 미너비니 트렌드 템플릿(전부 충족):
   종가 > 50일 > 150일 > 200일, 200일선 1개월 상승, 52주 고점 25% 이내, 52주 저점 +30%↑, RS 70↑
3) 추격 금지: 50일선 대비 +30% 초과, RSI 80 이상, 5일 +15%(미국 +12%) 초과 급등은 제외
4) 점수(100): RS 35 · 52주 고점 근접 20 · 3개월 수익률 15 · 매집(상승일/하락일 거래량) 15 · 수축(20일 고저폭) 15
5) 같은 섹터는 최대 2종목
RS 는 IBD 식 (최근 3개월 2배 + 6·9·12개월) 수익률의 시장 내 백분위(1~99).
"""
import json, os, sys
from datetime import datetime, timedelta, timezone

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
LIQ = {"kr": dict(mcap=2000, val=30, val_div=1e8), "us": dict(mcap=2000, val=20, val_div=1e6)}   # 억/억원 · M$/M$


def load(mk):
    u = json.load(open(os.path.join(OUT, f"universe_{mk}.json"), encoding="utf-8"))
    s = json.load(open(os.path.join(OUT, f"series_{mk}.json"), encoding="utf-8"))
    return u, s


def sma(c, n, end=None):
    end = len(c) if end is None else end
    if end < n:
        return None
    return sum(c[end - n:end]) / n


def feats(raw):
    c = [float(x) for x in raw["c"].split(",")]
    v = [float(x) * 1000 for x in raw["v"].split(",")]           # 천주 → 주
    n = len(c)
    if n < 210 or c[-1] <= 0:
        return None
    ret = lambda k: c[-1] / c[-1 - k] - 1 if n > k and c[-1 - k] > 0 else None
    w = c[-250:]
    f = dict(px=c[-1], n=n, m50=sma(c, 50), m150=sma(c, 150), m200=sma(c, 200), m200p=sma(c, 200, n - 21),
             hi=max(w), lo=min(w), r21=ret(21), r63=ret(63), r126=ret(126), r189=ret(189), r249=ret(min(249, n - 1)),
             rng20=max(c[-20:]) / min(c[-20:]) - 1)
    up = dn = 0.0
    for i in range(n - 50, n):
        if c[i] > c[i - 1]:
            up += v[i]
        elif c[i] < c[i - 1]:
            dn += v[i]
    f["udr"] = up / dn if dn > 0 else None
    f["val20"] = sum(c[i] * v[i] for i in range(n - 20, n)) / 20
    f["rsv"] = 2 * f["r63"] + f["r126"] + f["r189"] + f["r249"] if None not in (f["r63"], f["r126"], f["r189"], f["r249"]) else None
    return f


def pct_rank(vals):
    xs = sorted(v for v in vals if v is not None)
    def f(x):
        if x is None or not xs:
            return None
        lo, hi = 0, len(xs)
        while lo < hi:
            m = (lo + hi) // 2
            if xs[m] < x:
                lo = m + 1
            else:
                hi = m
        return lo / max(1, len(xs) - 1) * 100
    return f


def pick(mk, k=5):
    u, s = load(mk)
    L = LIQ[mk]
    F = {t: feats(raw) for t, raw in s["s"].items() if raw and raw.get("c")}
    F = {t: f for t, f in F.items() if f}
    rsP = pct_rank([f["rsv"] for f in F.values()])
    for f in F.values():
        f["rs"] = max(1, min(99, round(rsP(f["rsv"])))) if f["rsv"] is not None else None
    r63P = pct_rank([f["r63"] for f in F.values()])
    cands, stats = [], dict(universe=len(u["rows"]), liquid=0, template=0, extended=0)
    for r in u["rows"]:
        f = F.get(r["ticker"])
        if not f or not r.get("mcap"):
            continue
        if r["mcap"] < L["mcap"] or f["val20"] / L["val_div"] < L["val"]:
            continue
        stats["liquid"] += 1
        tt = (f["m50"] and f["m150"] and f["m200"] and f["m200p"]
              and f["px"] > f["m50"] > f["m150"] > f["m200"] and f["m200"] > f["m200p"]
              and f["px"] >= f["hi"] * 0.75 and f["px"] >= f["lo"] * 1.30 and (f["rs"] or 0) >= 70)
        if not tt:
            continue
        stats["template"] += 1
        ext = f["px"] / f["m50"] - 1
        hot = ext > 0.30 or (r.get("rsi14") or 0) >= 80 or (r.get("ret5") or 0) > (12 if mk == "us" else 15)
        if hot:
            stats["extended"] += 1
            continue
        near = 1 - (f["hi"] - f["px"]) / f["hi"] / 0.25                    # 고점이면 1, 25% 아래면 0
        udr = min(1.0, max(0.0, ((f["udr"] or 1) - 0.8) / 0.8))             # 0.8 → 0, 1.6↑ → 1
        tight = min(1.0, max(0.0, (0.30 - f["rng20"]) / 0.25))              # 20일 폭 5% → 1, 30% → 0
        score = 35 * f["rs"] / 99 + 20 * near + 15 * (r63P(f["r63"]) or 0) / 100 + 15 * udr + 15 * tight
        why = [f"RS {f['rs']}", f"52주 고점 {(f['px'] / f['hi'] - 1) * 100:+.1f}%", f"3개월 {f['r63'] * 100:+.0f}%",
               f"50일선 +{ext * 100:.0f}%", f"매집비 {f['udr']:.2f}" if f["udr"] else None, f"20일 폭 {f['rng20'] * 100:.0f}%"]
        cands.append(dict(ticker=r["ticker"], name=r["name"], sector=r.get("sector") or "", score=round(score, 1),
                          price=r.get("price"), chg1d=r.get("chg1d"), mcap=r.get("mcap"), rs=f["rs"],
                          hi_gap=round((f["px"] / f["hi"] - 1) * 100, 1), r21=round(f["r21"] * 100, 1),
                          r63=round(f["r63"] * 100, 1), r126=round(f["r126"] * 100, 1), ext50=round(ext * 100, 1),
                          udr=round(f["udr"], 2) if f["udr"] else None, rng20=round(f["rng20"] * 100, 1),
                          rsi14=r.get("rsi14"), vol_x20=r.get("vol_x20"), bb_gap=r.get("bb_gap"),
                          per=r.get("per"), fwd_per=r.get("fwd_per"), why=[w for w in why if w]))
    cands.sort(key=lambda x: -x["score"])
    top, per = [], {}
    for c in cands:
        sec = c["sector"] or "미분류"
        if sec not in ("미분류", "기타") and per.get(sec, 0) >= 2:
            continue
        per[sec] = per.get(sec, 0) + 1
        top.append(c)
        if len(top) >= k:
            break
    return dict(market=mk, date=u["date"], generated_at=datetime.now(KST).isoformat(timespec="seconds"),
                rule="미너비니 트렌드 템플릿 + RS·고점 근접·3개월 모멘텀·매집·수축 점수, 과열 제외, 섹터당 2종목",
                stats=dict(stats, passed=len(cands)), top=top, next=cands[k:k + 5])


def main():
    mks = [m.strip() for m in (sys.argv[1] if len(sys.argv) > 1 else os.getenv("MARKETS", "kr,us")).split(",") if m.strip()]
    for mk in mks:
        try:
            res = pick(mk)
        except FileNotFoundError as e:
            print(f"[{mk}] 입력 없음: {e}")
            continue
        p = os.path.join(OUT, f"top5_{mk}.json")
        json.dump(res, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[{mk}] {res['date']} · " + json.dumps(res["stats"], ensure_ascii=False) + " → "
              + ", ".join(f"{x['name']}({x['score']})" for x in res["top"]))


if __name__ == "__main__":
    main()
