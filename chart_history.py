#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
장기 차트 데이터 — 2000년부터의 일봉을 보관·갱신하고, 대시보드용 차트 팩을 만든다.

보관소(“서버”)
    GitHub Release  `chart-history`  의 자산
        hist_kr.parquet · hist_us.parquet   (2000-01-01 ~ 오늘, 수정주가 일봉 OHLCV 전 종목)
    매 실행마다 내려받아 → 새 봉만 덧붙이고 → 다시 올린다(덮어쓰기). 저장소 커밋 기록은 늘지 않는다.

차트 팩(대시보드가 읽는 파일)
    브랜치 `chart` 에 한 커밋만 유지(강제 푸시)
        index.json
        kr_00.json.gz … kr_15.json.gz,  us_00.json.gz … us_15.json.gz
    종목마다  d = 최근 DAILY_YEARS 년 일봉 · w = 2000년~ 주봉 · m = 2000년~ 월봉
    (년봉은 월봉에서, 최신 구간은 매일 갱신되는 series_*.json 에서 브라우저가 합친다)

사용
    python chart_history.py --hist hist --out packs --market kr     # 국내만 갱신 + 팩 전체 재생성
    python chart_history.py --self-test                               # 네트워크 없이 인코딩·집계 검증
"""
import argparse, gzip, json, math, os, sys, time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

KST = timezone(timedelta(hours=9))
START = os.getenv("HIST_START", "2000-01-01")
EPOCH = pd.Timestamp("2000-01-01")
SHARDS = int(os.getenv("CHART_SHARDS", "16"))
DAILY_YEARS = float(os.getenv("DAILY_YEARS", "3"))
PACK_BUDGET_MB = float(os.getenv("PACK_BUDGET_MB", "44"))     # 아티팩트 한 버전 64MB 안에 들어가도록 팩 총량 상한
TIME_BUDGET_MIN = float(os.getenv("TIME_BUDGET_MIN", "200"))   # 수집에 쓸 최대 시간(워크플로 타임아웃보다 짧게)
MAX_FULL = int(os.getenv("MAX_FULL", "3000"))                  # 한 번에 전체 이력을 새로 받을 최대 종목 수
SCALE = {"kr": 1, "us": 100}                                   # 가격 정수화: 원 / 센트
COLS = ["Open", "High", "Low", "Close", "Volume"]


# ───────────────────────── 공통 ─────────────────────────
def shard_of(t):
    """브라우저와 같은 규칙 — 티커 글자 코드 가중합 % SHARDS"""
    return sum((i + 1) * ord(ch) for i, ch in enumerate(str(t))) % SHARDS


def universe(market, root="."):
    p = os.path.join(root, "output", f"universe_{market}.json")
    try:
        with open(p, encoding="utf-8") as f:
            return [r["ticker"] for r in json.load(f).get("rows", []) if r.get("ticker")]
    except Exception as e:
        print(f"[{market}] 유니버스 파일 없음({e}) — 보관된 종목만 갱신", file=sys.stderr)
        return []


def clean(df, market):
    """FinanceDataReader 결과 → 수정주가 OHLCV. 미국은 Adj Close 비율로 시·고·저까지 보정."""
    if df is None or df.empty:
        return None
    df = df.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    if market == "us" and "Adj Close" in df.columns and "Close" in df.columns:
        f = (df["Adj Close"] / df["Close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
        for c in ("Open", "High", "Low"):
            if c in df.columns:
                df[c] = df[c] * f
        df["Close"] = df["Adj Close"]
    for c in COLS:
        if c not in df.columns:
            df[c] = np.nan if c != "Volume" else 0
    df = df[COLS].apply(pd.to_numeric, errors="coerce")
    df = df[df["Close"] > 0]
    for c in ("Open", "High", "Low"):                  # 시가가 0/결측인 옛 봉은 종가로 채운다
        df[c] = df[c].where(df[c] > 0, df["Close"])
    df["High"] = df[["High", "Open", "Close"]].max(axis=1)
    df["Low"] = df[["Low", "Open", "Close"]].min(axis=1)
    df["Volume"] = df["Volume"].fillna(0).clip(lower=0)
    return df[~df.index.duplicated(keep="last")].sort_index()


def fetch(market, ticker, start, tries=3):
    import FinanceDataReader as fdr
    for k in range(tries):
        try:
            return clean(fdr.DataReader(ticker, start), market)
        except Exception as e:
            if k == tries - 1:
                raise
            time.sleep(1.5 * (k + 1))


# ───────────────────────── 보관소 갱신 ─────────────────────────
def load_store(path):
    if not os.path.exists(path):
        return {}
    df = pd.read_parquet(path)
    out = {}
    for t, g in df.groupby("ticker", sort=False):
        out[t] = g.set_index("Date")[COLS].sort_index()
    return out


def save_store(store, path):
    frames = []
    for t, g in store.items():
        if g is None or g.empty:
            continue
        x = g.copy()
        x.index.name = "Date"
        x = x.reset_index()
        x.insert(0, "ticker", t)
        frames.append(x)
    if not frames:
        return 0
    df = pd.concat(frames, ignore_index=True)
    df["ticker"] = df["ticker"].astype("category")
    for c in ("Open", "High", "Low", "Close"):
        df[c] = df[c].astype("float64")
    df["Volume"] = df["Volume"].astype("float64")
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)
    return len(df)


def update_market(market, store, tickers, t_end):
    """보관된 종목은 최근 20일만 다시 받아 덧붙이고, 없는 종목은 2000년부터 받는다.
    겹치는 구간의 종가가 0.3% 넘게 다르면 수정주가가 바뀐 것(분할·배당락 등)이라 전체를 다시 받는다."""
    stats = dict(inc=0, full=0, refetch=0, fail=0, skipped=0, jumps=0)
    todo_inc = [t for t in tickers if t in store]
    todo_full = [t for t in tickers if t not in store]
    fails = []

    def over():
        return time.time() > t_end

    for t in todo_inc:
        if over():
            stats["skipped"] += 1
            continue
        old = store[t]
        since = (old.index[-1] - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
        try:
            new = fetch(market, t, since)
        except Exception as e:
            stats["fail"] += 1; fails.append(f"{t}:{type(e).__name__}")
            continue
        if new is None or new.empty:
            continue
        ov = old.index.intersection(new.index)
        if len(ov):
            diff = (new.loc[ov, "Close"] / old.loc[ov, "Close"] - 1).abs().max()
            if diff > 0.003:
                todo_full.append(t); stats["refetch"] += 1
                continue
        store[t] = pd.concat([old[old.index < new.index[0]], new])
        stats["inc"] += 1
        time.sleep(0.03)

    done_full = 0
    for t in todo_full:
        if over() or done_full >= MAX_FULL:
            stats["skipped"] += 1
            continue
        try:
            df = fetch(market, t, START)
        except Exception as e:
            stats["fail"] += 1; fails.append(f"{t}:{type(e).__name__}")
            continue
        if df is None or df.empty:
            stats["fail"] += 1; fails.append(f"{t}:빈결과")
            continue
        store[t] = df
        done_full += 1
        stats["full"] += 1
        time.sleep(0.05)

    # 수정주가가 아닐 가능성(하루 ±35% 넘는 봉) — 진단용
    for t in tickers:
        g = store.get(t)
        if g is None or len(g) < 2:
            continue
        r = g["Close"].pct_change().abs()
        if (r > 0.35).any():
            stats["jumps"] += 1
    stats["fails"] = fails[:15]
    return stats


# ───────────────────────── 차트 팩 ─────────────────────────
def aggregate(df, rule):
    """W: 토~금 주 단위(금요일 마감) / M: 월. 봉 날짜는 그 구간의 마지막 거래일."""
    key = df.index.to_period("W-FRI") if rule == "W" else df.index.to_period("M")
    g = df.assign(_d=df.index).groupby(key)
    out = pd.DataFrame({
        "Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
        "Close": g["Close"].last(), "Volume": g["Volume"].sum(), "_d": g["_d"].last(),
    })
    return out.set_index("_d").sort_index()


def _j(a):
    return ",".join(str(int(x)) for x in a)


def encode(df, sc):
    """날짜: 2000-01-01 부터의 일수를 차분 / 종가: 정수화 후 차분 / 시·고·저: 종가와의 차 / 거래량: 천 단위"""
    if df is None or df.empty:
        return None
    day = ((df.index - EPOCH).days).to_numpy()
    c = np.round(df["Close"].to_numpy() * sc).astype(np.int64)
    o = np.round(df["Open"].to_numpy() * sc).astype(np.int64) - c
    h = np.round(df["High"].to_numpy() * sc).astype(np.int64) - c
    l = np.round(df["Low"].to_numpy() * sc).astype(np.int64) - c
    v = np.round(df["Volume"].to_numpy() / 1000.0).astype(np.int64)
    return {"t": _j(np.diff(day, prepend=0)), "c": _j(np.diff(c, prepend=0)),
            "o": _j(o), "h": _j(h), "l": _j(l), "v": _j(v)}


def decode(e, sc):
    """self-test 용 — 브라우저 디코더와 같은 규칙"""
    t = np.cumsum([int(x) for x in e["t"].split(",")])
    c = np.cumsum([int(x) for x in e["c"].split(",")])
    o = c + np.array([int(x) for x in e["o"].split(",")])
    h = c + np.array([int(x) for x in e["h"].split(",")])
    l = c + np.array([int(x) for x in e["l"].split(",")])
    return t, o / sc, h / sc, l / sc, c / sc


def build_packs(market, store, tickers, out_dir, asof, daily_years=None):
    dy = DAILY_YEARS if daily_years is None else daily_years
    sc = SCALE[market]
    shards = [dict() for _ in range(SHARDS)]
    cut = pd.Timestamp(asof) - pd.Timedelta(days=int(365.25 * dy))
    n = 0
    first = None
    for t in tickers:
        g = store.get(t)
        if g is None or g.empty:
            continue
        rec = {"d": encode(g[g.index >= cut], sc), "w": encode(aggregate(g, "W"), sc),
               "m": encode(aggregate(g, "M"), sc)}
        shards[shard_of(t)][t] = rec
        n += 1
        first = g.index[0] if first is None else min(first, g.index[0])
    sizes = []
    for i, body in enumerate(shards):
        payload = {"mk": market, "asof": asof, "sc": sc, "shard": i, "t": body}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        p = os.path.join(out_dir, f"{market}_{i:02d}.json.gz")
        with open(p, "wb") as f:
            f.write(gzip.compress(raw, 9, mtime=0))
        sizes.append(os.path.getsize(p))
    return {"asof": asof, "n": n, "shards": SHARDS, "sc": sc, "daily_years": dy,
            "from": first.strftime("%Y-%m-%d") if first is not None else None,
            "bytes": int(sum(sizes)), "max_shard": int(max(sizes) if sizes else 0)}


# ───────────────────────── 실행 ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", default="hist")
    ap.add_argument("--out", default="packs")
    ap.add_argument("--market", default="all", help="갱신할 시장: kr, us, all, none(팩만 재생성)")
    ap.add_argument("--root", default=".", help="output/universe_*.json 이 있는 저장소 루트")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    os.makedirs(a.hist, exist_ok=True)
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    t_end = t0 + TIME_BUDGET_MIN * 60
    upd = ["kr", "us"] if a.market in ("all", "auto") else ([] if a.market == "none" else [a.market])
    idx_path = os.path.join(a.out, "index.json")
    index = {"generated_at": datetime.now(KST).isoformat(timespec="seconds"), "markets": {}, "diag": {}}

    stores = {}
    for mk in ("kr", "us"):
        path = os.path.join(a.hist, f"hist_{mk}.parquet")
        store = load_store(path)
        tickers = universe(mk, a.root) or list(store.keys())
        print(f"[{mk}] 보관 {len(store):,}종목 · 유니버스 {len(tickers):,}종목")
        if mk in upd:
            # 두 시장을 한 번에 돌리면 국내에 시간의 45% 만 쓰고 나머지는 미국에 남긴다
            share_end = t0 + (t_end - t0) * 0.45 if (mk == "kr" and len(upd) == 2) else t_end
            st = update_market(mk, store, tickers, share_end)
            rows = save_store(store, path)
            print(f"[{mk}] 갱신 {st} · 저장 {rows:,}행")
            index["diag"][mk] = st
        if store:
            stores[mk] = (store, tickers)

    # 팩 생성 — 총량이 예산을 넘으면 일봉 보관 연수를 1년씩 줄여 다시 만든다
    dy = DAILY_YEARS
    while True:
        index["markets"] = {}
        for mk, (store, tickers) in stores.items():
            asof = max(g.index[-1] for g in store.values() if g is not None and len(g)).strftime("%Y-%m-%d")
            index["markets"][mk] = build_packs(mk, store, tickers, a.out, asof, dy)
        total = sum(v["bytes"] for v in index["markets"].values()) / 1e6
        if total <= PACK_BUDGET_MB or dy <= 1:
            break
        print(f"팩 {total:.1f}MB > 예산 {PACK_BUDGET_MB}MB — 일봉 {dy}년 → {dy-1}년으로 줄여 다시 생성")
        dy -= 1
    for mk, info in index["markets"].items():
        print(f"[{mk}] 팩 {info['n']:,}종목 · {info['bytes']/1e6:.1f}MB (최대 샤드 {info['max_shard']/1e6:.2f}MB) · {info['from']}~{info['asof']} · 일봉 {info['daily_years']}년")

    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print(f"완료 {time.time()-t0:.0f}s")


def self_test():
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2000-01-03", "2026-09-29")
    c = 10000 * np.exp(np.cumsum(rng.normal(0, 0.02, len(idx))))
    df = pd.DataFrame({"Open": c * 0.99, "High": c * 1.02, "Low": c * 0.97, "Close": c,
                       "Volume": rng.integers(1e4, 1e6, len(idx)).astype(float)}, index=idx)
    df = clean(df, "kr")
    # 1) 인코딩 왕복
    for rule in ("D", "W", "M"):
        g = df if rule == "D" else aggregate(df, rule)
        e = encode(g, 1)
        t, o, h, l, cc = decode(e, 1)
        assert len(t) == len(g)
        assert (EPOCH + pd.to_timedelta(t, "D") == g.index).all(), rule
        assert np.allclose(cc, np.round(g["Close"]), atol=0.5), rule
        assert (h >= cc).all() and (l <= cc).all(), rule
    # 2) 주봉 = 금요일 마감, 봉 날짜는 마지막 거래일
    w = aggregate(df, "W")
    assert (w.index.dayofweek <= 4).all()
    wk = df.index.to_period("W-FRI")
    assert w["High"].iloc[5] == df["High"][wk == wk[0] + 5].max()
    assert w.index[5] == df.index[wk == wk[0] + 5][-1]
    m = aggregate(df, "M")
    assert len(m) == len(pd.period_range("2000-01", "2026-09", freq="M"))
    # 3) 샤드 규칙 (브라우저와 동일해야 함)
    assert shard_of("005930") == sum((i + 1) * ord(ch) for i, ch in enumerate("005930")) % SHARDS
    # 4) 팩 생성·크기
    import tempfile
    d = tempfile.mkdtemp()
    info = build_packs("kr", {"005930": df, "000660": df * 1.1}, ["005930", "000660", "없음"], d, "2026-09-29")
    assert info["n"] == 2 and info["from"] == "2000-01-03"
    with gzip.open(os.path.join(d, f"kr_{shard_of('005930'):02d}.json.gz")) as f:
        p = json.load(f)
    rec = p["t"]["005930"]
    per_bar = info["bytes"] / 2 / (len(rec["d"]["t"].split(",")) + len(rec["w"]["t"].split(",")) + len(rec["m"]["t"].split(",")))
    print(f"self-test OK · 종목당 {info['bytes']/2/1e3:.1f}KB · 봉당 {per_bar:.1f}B")
    # 5) 증분 병합 규칙
    old = df.iloc[:-30]
    new = df.iloc[-50:]
    merged = pd.concat([old[old.index < new.index[0]], new])
    assert merged.index.equals(df.index)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
