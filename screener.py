#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
국내 주식 3-조건 스크리너 — 깃허브 액션용
  A. 신고가 임박 (52주 최고가 -NEAR% 이내)
  B. OBV 신고가 (최근 OBV_WIN 거래일 최고치)
  C. N일 연속 양봉 (종가 > 시가)
  ★ A∩B∩C  /  참고: OBV 다이버전스(가격 미신고가 + OBV 신고가)

output/latest.md, output/latest.json, output/YYYYMMDD.md 를 남긴다.
데이터: FinanceDataReader (종목 리스트=깃허브 캐시, 시세=네이버 / KRX 로그인 불필요)
투자 권유가 아니며 종목 발굴 보조 자료입니다.
"""
import json, os, sys, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import FinanceDataReader as fdr

KST = timezone(timedelta(hours=9))

NEAR      = float(os.getenv("NEAR_PCT", "15"))
OBV_WIN   = int(os.getenv("OBV_WIN", "252"))
HIGH_WIN  = int(os.getenv("HIGH_WIN", "252"))
STREAK    = int(os.getenv("STREAK", "3"))
MIN_CAP   = float(os.getenv("MIN_CAP_EOK", "1000"))
MIN_VAL   = float(os.getenv("MIN_VALUE_EOK", "10"))
MAX_N     = int(os.getenv("MAX_TICKERS", "900"))
WORKERS   = int(os.getenv("WORKERS", "8"))
TOP_N     = int(os.getenv("TOP_N", "20"))


# ── 지표 ──────────────────────────────────────────────────────
def obv_series(close, volume):
    return (np.sign(close.diff().fillna(0.0)) * volume).cumsum()


def evaluate_one(df, near_pct=NEAR, obv_win=OBV_WIN, high_win=HIGH_WIN, streak=STREAK):
    """df: 날짜 오름차순, 컬럼 Open/High/Low/Close/Volume"""
    if df is None or len(df) < max(60, streak + 5):
        return None
    close, open_ = df["Close"].astype(float), df["Open"].astype(float)
    high, vol = df["High"].astype(float), df["Volume"].astype(float)
    if close.iloc[-1] <= 0 or vol.iloc[-20:].sum() == 0:
        return None

    hw, ow = min(high_win, len(df)), min(obv_win, len(df))
    win_high = high.iloc[-hw:].max()
    if not np.isfinite(win_high) or win_high <= 0:
        return None
    gap = (close.iloc[-1] / win_high - 1.0) * 100.0

    obv = obv_series(close, vol)
    rec = obv.iloc[-ow:]
    cond_b = bool(obv.iloc[-1] >= rec.max() - 1e-9)
    rng = rec.max() - rec.min()
    obv_pos = float((obv.iloc[-1] - rec.min()) / rng * 100.0) if rng > 0 else float("nan")

    bull = (close > open_).iloc[-streak:]
    r5  = (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) > 6 else float("nan")
    r20 = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) > 21 else float("nan")
    vb = vol.iloc[-21:-1].mean()

    return {
        "종가": float(close.iloc[-1]),
        "52주최고": float(win_high),
        "고점대비%": round(float(gap), 2),
        "신고가갱신": bool(gap >= -0.0001),
        "OBV위치%": round(obv_pos, 1) if np.isfinite(obv_pos) else None,
        "5일수익%": round(float(r5), 2) if np.isfinite(r5) else None,
        "20일수익%": round(float(r20), 2) if np.isfinite(r20) else None,
        "거래량배수": round(float(vol.iloc[-1] / vb), 2) if vb > 0 else None,
        "20일선위": bool(close.iloc[-1] > close.iloc[-20:].mean()),
        "60일선위": bool(close.iloc[-1] > close.iloc[-60:].mean()),
        "데이터일수": int(len(df)),
        "A_신고가임박": bool(gap >= -near_pct),
        "B_OBV신고가": cond_b,
        "C_연속양봉": bool(bull.all()) and len(bull) == streak,
    }


def self_test():
    def F(c, o=None, v=None):
        c = np.array(c, float)
        o = np.array(o, float) if o is not None else c * 0.99
        v = np.array(v, float) if v is not None else np.full(len(c), 1e6)
        return pd.DataFrame({"Open": o, "High": np.maximum(c, o) * 1.005,
                             "Low": np.minimum(c, o) * 0.995, "Close": c, "Volume": v})
    c = np.linspace(100, 200, 300)
    r = evaluate_one(F(c, c * 0.995), 5, 252, 252, 3)
    assert r["A_신고가임박"] and r["B_OBV신고가"] and r["C_연속양봉"]
    o = c * 0.995; o[-1] = c[-1] * 1.01
    assert not evaluate_one(F(c, o), 5, 252, 252, 3)["C_연속양봉"]
    g = np.random.default_rng(0)
    c4 = np.concatenate([np.linspace(100, 190, 200), 190 + g.normal(0, 2, 100).cumsum() * 0.1])
    c4[-1] = c4[:200].max() * 0.99
    v4 = np.where(np.sign(np.diff(c4, prepend=c4[0])) > 0, 5e5, 3e6)
    assert not evaluate_one(F(c4, c4 * 0.999, v4), 5, 252, 252, 3)["B_OBV신고가"]
    assert list(obv_series(pd.Series([10, 11, 10, 12, 12.]),
                           pd.Series([100, 200, 300, 400, 500.]))) == [0, 200, -100, 300, 300]
    print("self-test OK (4 cases)")


# ── 데이터 ────────────────────────────────────────────────────
END = datetime.now(KST).strftime("%Y-%m-%d")
START = (datetime.now(KST) - timedelta(days=int(HIGH_WIN * 1.75) + 40)).strftime("%Y-%m-%d")


def fetch_ohlcv(code, tries=3):
    for a in range(tries):
        try:
            d = fdr.DataReader(code, START, END)
            if d is None or d.empty:
                return None
            d = d.rename(columns={c: str(c).capitalize() for c in d.columns})
            need = ["Open", "High", "Low", "Close", "Volume"]
            if not all(c in d.columns for c in need):
                return None
            return d[need].sort_index()
        except Exception:
            if a == tries - 1:
                return None
            time.sleep(0.8 * (a + 1))
    return None


def normalize_listing(df):
    ren = {}
    for c in df.columns:
        s = str(c).strip()
        if s in ("Code", "Symbol", "종목코드"):        ren[c] = "Code"
        elif s in ("Name", "종목명"):                  ren[c] = "Name"
        elif s in ("Market", "시장구분"):              ren[c] = "Market"
        elif s in ("Marcap", "MarketCap", "시가총액"): ren[c] = "Marcap"
        elif s in ("Amount", "거래대금"):              ren[c] = "Amount"
        elif s in ("Volume", "거래량"):                ren[c] = "Volume"
        elif s in ("Close", "종가"):                   ren[c] = "Close"
    return df.rename(columns=ren)


def build_universe():
    listing = None
    for src in ("KRX", "KOSPI"):
        try:
            listing = fdr.StockListing(src)
            if listing is not None and len(listing):
                print(f"listing source: {src} ({len(listing):,} rows) cols={list(listing.columns)}")
                break
        except Exception as e:
            print(f"StockListing('{src}') failed: {e}", file=sys.stderr)
    if listing is None or not len(listing):
        raise RuntimeError("종목 리스트를 받지 못했습니다")

    df = normalize_listing(listing)
    if "Code" not in df.columns:
        raise RuntimeError(f"종목코드 컬럼 없음: {list(listing.columns)}")
    df["Code"] = df["Code"].astype(str).str.zfill(6)
    if "Name" not in df.columns:
        df["Name"] = df["Code"]

    df["시총(억)"] = (pd.to_numeric(df["Marcap"], errors="coerce") / 1e8
                      if "Marcap" in df.columns else np.nan)
    if "Amount" in df.columns:
        df["거래대금(억)"] = pd.to_numeric(df["Amount"], errors="coerce") / 1e8
    elif {"Close", "Volume"} <= set(df.columns):
        df["거래대금(억)"] = (pd.to_numeric(df["Close"], errors="coerce")
                              * pd.to_numeric(df["Volume"], errors="coerce")) / 1e8
    else:
        df["거래대금(억)"] = np.nan

    u = df
    if u["시총(억)"].notna().any():
        u = u[u["시총(억)"].fillna(0) >= MIN_CAP]
    if u["거래대금(억)"].notna().any():
        u = u[u["거래대금(억)"].fillna(0) >= MIN_VAL]
    u = u[~u["Name"].astype(str).str.contains("스팩|리츠", na=False)]
    u = u[~u["Name"].astype(str).str.contains(r"우[A-Z]?$|\d우", regex=True, na=False)]
    u = u[u["Code"].str.endswith("0")]

    # 관리종목 제외 (실패해도 진행)
    try:
        adm = normalize_listing(fdr.StockListing("KRX-ADMINISTRATIVE"))
        if "Code" in adm.columns:
            bad = set(adm["Code"].astype(str).str.zfill(6))
            before = len(u); u = u[~u["Code"].isin(bad)]
            print(f"관리종목 제외: {before - len(u)}종목")
    except Exception as e:
        print(f"관리종목 목록 조회 생략: {e}", file=sys.stderr)

    u = u.drop_duplicates(subset="Code", keep="first")
    u = u.sort_values("거래대금(억)", ascending=False).head(MAX_N)
    return u[["Code", "Name", "시총(억)", "거래대금(억)"]].reset_index(drop=True)


# ── 리포트 ────────────────────────────────────────────────────
def lines(df, title, n):
    if not len(df):
        return f"### {title}\n\n_해당 없음_\n"
    out = [f"### {title} — {len(df)}종목 (상위 {min(n, len(df))})", ""]
    out.append("| 종목 | 티커 | 종가 | 고점대비 | OBV | 5일 | 거래량 |")
    out.append("|---|---|---:|---:|---:|---:|---:|")
    for _, r in df.head(n).iterrows():
        f = lambda v, s: (s.format(v) if v is not None and pd.notna(v) else "—")
        out.append(f"| {r['종목명']} | {r['티커']} | {r['종가']:,.0f} | "
                   f"{f(r['고점대비%'], '{:+.1f}%')} | {f(r['OBV위치%'], '{:.0f}')} | "
                   f"{f(r['5일수익%'], '{:+.1f}%')} | {f(r['거래량배수'], '{:.1f}x')} |")
    out.append("")
    return "\n".join(out)


def main():
    self_test()
    stamp = datetime.now(KST).strftime("%Y-%m-%d")
    u = build_universe()
    print(f"universe: {len(u):,} tickers")

    ohlcv, failed = {}, []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fut = {ex.submit(fetch_ohlcv, c): c for c in u["Code"]}
        for i, f in enumerate(as_completed(fut), 1):
            c = fut[f]
            try:
                d = f.result()
            except Exception:
                d = None
            if d is None:
                failed.append(c)
            else:
                ohlcv[c] = d
            if i % 150 == 0:
                print(f"  fetched {i}/{len(fut)}")
    print(f"fetched ok={len(ohlcv):,} failed={len(failed):,}")
    if not ohlcv:
        raise RuntimeError("시세를 하나도 받지 못했습니다")

    rows = []
    for c, d in ohlcv.items():
        r = evaluate_one(d)
        if r:
            r["Code"] = c
            rows.append(r)
    res = (pd.DataFrame(rows).merge(u, on="Code", how="left")
           .rename(columns={"Code": "티커", "Name": "종목명"}))

    A = res[res["A_신고가임박"]].sort_values("고점대비%", ascending=False)
    B = res[res["B_OBV신고가"]].sort_values("OBV위치%", ascending=False)
    C = res[res["C_연속양봉"]].sort_values("5일수익%", ascending=False)
    ABC = res[res["A_신고가임박"] & res["B_OBV신고가"] & res["C_연속양봉"]] \
            .sort_values(["고점대비%", "거래량배수"], ascending=[False, False])
    DIV = res[res["B_OBV신고가"] & ~res["신고가갱신"] &
              (res["고점대비%"] >= -25)].sort_values("고점대비%", ascending=False)

    md = [
        f"# 국내 3-조건 스크리닝 — {stamp}", "",
        f"조건: 신고가 −{NEAR:g}% 이내 / OBV {OBV_WIN}일 신고가 / {STREAK}일 연속 양봉",
        f"유니버스: 시총 {MIN_CAP:g}억+ · 거래대금 {MIN_VAL:g}억+ · 관리종목·우선주·스팩·리츠 제외",
        f"분석 {len(res):,}종목 (수집 실패 {len(failed):,})", "",
        f"**A 신고가임박 {len(A)} · B OBV신고가 {len(B)} · C 연속양봉 {len(C)} · "
        f"★교집합 {len(ABC)} · 다이버전스 {len(DIV)}**", "",
        lines(ABC, "★ 세 조건 교집합", TOP_N),
        lines(DIV, "OBV 다이버전스 (가격 미신고가 + OBV 신고가)", TOP_N),
        lines(A, "신고가 임박", TOP_N),
        lines(C, f"{STREAK}일 연속 양봉", TOP_N),
        "---", "", "_종목 발굴 보조 자료이며 투자 권유가 아닙니다._",
        f"_생성: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST_", "",
    ]
    text = "\n".join(md)

    os.makedirs("output", exist_ok=True)
    open("output/latest.md", "w", encoding="utf-8").write(text)
    open(f"output/{stamp.replace('-','')}.md", "w", encoding="utf-8").write(text)
    keep = ["티커", "종목명", "종가", "고점대비%", "신고가갱신", "OBV위치%",
            "5일수익%", "20일수익%", "거래량배수", "시총(억)", "거래대금(억)"]
    json.dump({
        "date": stamp, "params": {"near": NEAR, "obv_win": OBV_WIN, "streak": STREAK,
                                  "min_cap_eok": MIN_CAP, "min_value_eok": MIN_VAL},
        "counts": {"analyzed": len(res), "failed": len(failed), "A": len(A),
                   "B": len(B), "C": len(C), "ABC": len(ABC), "DIV": len(DIV)},
        "abc": ABC[keep].head(TOP_N).to_dict("records"),
        "div": DIV[keep].head(TOP_N).to_dict("records"),
        "a":   A[keep].head(TOP_N).to_dict("records"),
        "c":   C[keep].head(TOP_N).to_dict("records"),
    }, open("output/latest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    res.to_csv("output/all_metrics.csv", index=False, encoding="utf-8-sig")
    print(text[:1200])
    print("\nwrote output/latest.md, latest.json, all_metrics.csv")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        os.makedirs("output", exist_ok=True)
        open("output/latest.md", "w", encoding="utf-8").write(
            f"# 스크리닝 실패 — {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST\n\n"
            "```\n" + traceback.format_exc()[-1500:] + "\n```\n")
        sys.exit(1)
