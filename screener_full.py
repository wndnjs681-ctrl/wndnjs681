#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크리닝 대시보드용 전 종목 × 다지표 덤프 (GitHub Actions 전용)

국내(KOSPI+KOSDAQ)와 미국(S&P500+NASDAQ100) 전 종목의 일봉을 받아
지표를 계산한 뒤 output/universe_kr.json / universe_us.json 으로 남긴다.

출력 스키마
  { "market","date","generated_at","count",
    "schema": [ {key,label,type,unit,group,better,decimals}, ... ],
    "rows":   [ {ticker,name,...지표...}, ... ] }

지표를 추가하려면 → (1) compute_metrics() 에 한 줄 추가
                     (2) SCHEMA 에 같은 key 로 한 줄 추가
대시보드는 schema 를 읽어 필터 UI 를 스스로 그리므로 화면 수정은 필요 없다.

투자 권유가 아니며 종목 발굴 보조 자료입니다.
"""
import json, os, sys, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import FinanceDataReader as fdr

KST = timezone(timedelta(hours=9))

# ── 실행 파라미터 (워크플로 env 로 덮어쓸 수 있음) ─────────────
MIN_CAP_KR = float(os.getenv("MIN_CAP_EOK", "500"))      # 국내 최소 시총(억)
MIN_VAL_KR = float(os.getenv("MIN_VALUE_EOK", "3"))      # 국내 최소 거래대금(억)
MIN_CAP_US = float(os.getenv("MIN_CAP_MUSD", "1000"))    # 미국 최소 시총(백만달러)
MAX_KR     = int(os.getenv("MAX_KR", "2600"))
MAX_US     = int(os.getenv("MAX_US", "1200"))
WORKERS    = int(os.getenv("WORKERS", "10"))
LOOKBACK_D = int(os.getenv("LOOKBACK_DAYS", "560"))      # 달력일 (약 2년 반)
BB_LEN     = int(os.getenv("BB_LEN", "150"))             # 볼린저 기간
BB_K       = float(os.getenv("BB_K", "1.5"))             # 볼린저 표준편차 배수


# ══════════════════════════════════════════════════════════════
# 지표 계산
# ══════════════════════════════════════════════════════════════
def _r(x, n=2):
    """NaN/inf 를 None 으로 바꾸며 반올림 (JSON 안전)"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, n) if np.isfinite(v) else None


def obv_series(close, volume):
    return (np.sign(close.diff().fillna(0.0)) * volume).cumsum()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    # 하락분이 전혀 없으면 100, 상승분이 전혀 없으면 0 (연속 상한가/하한가 구간)
    out = out.mask((dn == 0) & (up > 0), 100.0)
    out = out.mask((up == 0) & (dn > 0), 0.0)
    return out


def atr_pct(df, n=20):
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean() / c * 100


def bull_streak(close, open_):
    """마지막 봉부터 거슬러 올라가며 양봉 연속 일수"""
    s = 0
    for i in range(len(close) - 1, -1, -1):
        if close.iloc[i] > open_.iloc[i]:
            s += 1
        else:
            break
    return s


def compute_metrics(df):
    """df: 날짜 오름차순, 컬럼 Open/High/Low/Close/Volume → dict (실패 시 None)"""
    if df is None or len(df) < 130:
        return None
    df = df.dropna(subset=["Close"])
    if len(df) < 130:
        return None

    close, open_ = df["Close"].astype(float), df["Open"].astype(float)
    high, low = df["High"].astype(float), df["Low"].astype(float)
    vol = df["Volume"].astype(float)
    px = float(close.iloc[-1])
    if px <= 0 or vol.iloc[-20:].sum() <= 0:
        return None

    n = len(df)
    m = {}

    # ── 가격 ──────────────────────────────────────────────
    m["price"] = _r(px, 4 if px < 1000 else 1)
    m["chg1d"] = _r(close.iloc[-1] / close.iloc[-2] * 100 - 100)
    m["bars"] = n

    # ── 기간 신고가 / 신저가 ─────────────────────────────
    for w in (20, 60, 120, 252):
        ww = min(w, n)
        hh, ll = float(high.iloc[-ww:].max()), float(low.iloc[-ww:].min())
        gap = (px / hh - 1) * 100 if hh > 0 else np.nan
        m[f"hi_gap{w}"] = _r(gap)     # 현재가가 기간 고점에서 얼마나 눌렸나 (당일 포함)
        # 신고가 갱신 = 당일 고가가 '직전' 기간의 최고 고가 이상 (HTS 신고가와 같은 정의).
        # 종가를 당일 포함 고가와 비교하면 당일 고가를 넘을 수 없어 사실상 항상 False 가 된다.
        prior = high.iloc[-ww:-1]
        m[f"is_hi{w}"] = bool(len(prior) and float(high.iloc[-1]) >= float(prior.max()) - 1e-9)
        if w == 252:
            m["lo_gap252"] = _r((px / ll - 1) * 100 if ll > 0 else np.nan)
            m["hi252"] = _r(hh, 1)
            m["lo252"] = _r(ll, 1)
            rng = hh - ll
            m["pos52"] = _r((px - ll) / rng * 100 if rng > 0 else np.nan, 1)

    # ── 이동평균 ─────────────────────────────────────────
    ma = {}
    for w in (5, 20, 60, 120):
        ma[w] = float(close.iloc[-w:].mean()) if n >= w else np.nan
        m[f"ma{w}"] = _r(ma[w], 1)
        m[f"above_ma{w}"] = bool(np.isfinite(ma[w]) and px > ma[w])
        m[f"disp{w}"] = _r(px / ma[w] * 100 if np.isfinite(ma[w]) and ma[w] > 0 else np.nan, 1)

    got = [ma[w] for w in (5, 20, 60, 120)]
    ok = all(np.isfinite(v) for v in got)
    m["ma_align"] = bool(ok and got[0] > got[1] > got[2] > got[3])      # 정배열
    m["ma_align_rev"] = bool(ok and got[0] < got[1] < got[2] < got[3])  # 역배열
    m["ma_above_cnt"] = int(sum(bool(np.isfinite(v) and px > v) for v in got))
    # 20일선 기울기 (최근 5거래일간 변화율 %)
    if n >= 25:
        prev20 = float(close.iloc[-25:-5].mean())
        m["ma20_slope"] = _r(ma[20] / prev20 * 100 - 100 if prev20 > 0 else np.nan)
    else:
        m["ma20_slope"] = None
    # 골든크로스: 5일선이 20일선을 최근 5거래일 내 상향 돌파
    if n >= 30:
        s5 = close.rolling(5).mean()
        s20 = close.rolling(20).mean()
        d = (s5 - s20).iloc[-6:]
        m["golden_cross"] = bool(d.iloc[0] <= 0 < d.iloc[-1])
    else:
        m["golden_cross"] = False

    # ── 거래량 ───────────────────────────────────────────
    v20 = float(vol.iloc[-21:-1].mean())
    v60 = float(vol.iloc[-61:-1].mean()) if n >= 61 else np.nan
    m["vol_x20"] = _r(vol.iloc[-1] / v20 if v20 > 0 else np.nan)
    m["vol_x60"] = _r(vol.iloc[-1] / v60 if np.isfinite(v60) and v60 > 0 else np.nan)
    # 최근 5일 평균 거래량 / 직전 60일 평균 — 에너지가 붙는 중인지
    m["vol_burst"] = _r(vol.iloc[-5:].mean() / v60 if np.isfinite(v60) and v60 > 0 else np.nan)

    obv = obv_series(close, vol)
    ow = min(252, n)
    rec = obv.iloc[-ow:]
    rngo = float(rec.max() - rec.min())
    m["obv_pos"] = _r((obv.iloc[-1] - rec.min()) / rngo * 100 if rngo > 0 else np.nan, 1)
    m["obv_high"] = bool(obv.iloc[-1] >= rec.max() - 1e-9)
    # 다이버전스: OBV 는 신고가인데 가격은 아직 아님
    m["obv_diverge"] = bool(m["obv_high"] and not m["is_hi252"])

    # ── 모멘텀 ───────────────────────────────────────────
    for w in (5, 20, 60, 120):
        m[f"ret{w}"] = _r(px / close.iloc[-w - 1] * 100 - 100 if n > w else np.nan)
    m["rsi14"] = _r(rsi(close).iloc[-1], 1)
    m["streak_up"] = bull_streak(close, open_)

    # ── 변동성 · 볼린저밴드(BB_LEN, BB_K, 종가) ──────────
    m["atr20"] = _r(atr_pct(df).iloc[-1], 2)
    bl = min(BB_LEN, n)
    mid = float(close.iloc[-bl:].mean())
    sd = float(close.iloc[-bl:].std(ddof=0))
    up, dn = mid + BB_K * sd, mid - BB_K * sd
    m["bb_upper"] = _r(up, 1)
    m["bb_gap"] = _r(px / up * 100 - 100 if up > 0 else np.nan)   # 상한선 대비 % (음수=아래)
    m["bb_width"] = _r((up - dn) / mid * 100 if mid > 0 else np.nan)  # 밴드폭 = 수축 여부
    # %B: 0=하단, 100=상단
    m["bb_pctb"] = _r((px - dn) / (up - dn) * 100 if up > dn else np.nan, 1)

    return m


# ══════════════════════════════════════════════════════════════
# 대시보드가 읽는 지표 사전 — 여기에 추가하면 필터가 자동 생성된다
# type: num | bool   better: high | low | none
# ══════════════════════════════════════════════════════════════
SCHEMA = [
    # 기본
    dict(key="price",  label="종가",         type="num", unit="",   group="기본", better="none", decimals=0),
    dict(key="chg1d",  label="전일대비",     type="num", unit="%",  group="기본", better="high"),
    dict(key="mcap",   label="시가총액",     type="num", unit="억",  group="기본", better="none", decimals=0),
    dict(key="value",  label="거래대금",     type="num", unit="억",  group="기본", better="high", decimals=0),
    # 신고가
    dict(key="hi_gap20",  label="20일 고점대비",  type="num",  unit="%", group="신고가", better="high"),
    dict(key="hi_gap60",  label="60일 고점대비",  type="num",  unit="%", group="신고가", better="high"),
    dict(key="hi_gap120", label="120일 고점대비", type="num",  unit="%", group="신고가", better="high"),
    dict(key="hi_gap252", label="52주 고점대비",  type="num",  unit="%", group="신고가", better="high"),
    dict(key="is_hi20",   label="20일 신고가",    type="bool", unit="",  group="신고가", better="high"),
    dict(key="is_hi60",   label="60일 신고가",    type="bool", unit="",  group="신고가", better="high"),
    dict(key="is_hi120",  label="120일 신고가",   type="bool", unit="",  group="신고가", better="high"),
    dict(key="is_hi252",  label="52주 신고가",    type="bool", unit="",  group="신고가", better="high"),
    dict(key="lo_gap252", label="52주 저점대비",  type="num",  unit="%", group="신고가", better="high"),
    dict(key="pos52",     label="52주 위치",      type="num",  unit="%", group="신고가", better="high", decimals=1),
    # 이동평균
    dict(key="ma_align",     label="정배열(5>20>60>120)", type="bool", unit="",  group="이동평균", better="high"),
    dict(key="ma_align_rev", label="역배열",              type="bool", unit="",  group="이동평균", better="low"),
    dict(key="ma_above_cnt", label="이평선 위 개수",      type="num",  unit="개", group="이동평균", better="high", decimals=0),
    dict(key="above_ma5",    label="5일선 위",            type="bool", unit="",  group="이동평균", better="high"),
    dict(key="above_ma20",   label="20일선 위",           type="bool", unit="",  group="이동평균", better="high"),
    dict(key="above_ma60",   label="60일선 위",           type="bool", unit="",  group="이동평균", better="high"),
    dict(key="above_ma120",  label="120일선 위",          type="bool", unit="",  group="이동평균", better="high"),
    dict(key="disp20",       label="20일 이격도",         type="num",  unit="%", group="이동평균", better="none", decimals=1),
    dict(key="disp60",       label="60일 이격도",         type="num",  unit="%", group="이동평균", better="none", decimals=1),
    dict(key="ma20_slope",   label="20일선 기울기(5일)",  type="num",  unit="%", group="이동평균", better="high"),
    dict(key="golden_cross", label="골든크로스(5/20)",    type="bool", unit="",  group="이동평균", better="high"),
    # 거래량
    dict(key="vol_x20",     label="거래량배수(20일)",  type="num",  unit="x", group="거래량", better="high"),
    dict(key="vol_x60",     label="거래량배수(60일)",  type="num",  unit="x", group="거래량", better="high"),
    dict(key="vol_burst",   label="5일평균/60일평균",  type="num",  unit="x", group="거래량", better="high"),
    dict(key="obv_pos",     label="OBV 위치",          type="num",  unit="%", group="거래량", better="high", decimals=1),
    dict(key="obv_high",    label="OBV 신고가",        type="bool", unit="",  group="거래량", better="high"),
    dict(key="obv_diverge", label="OBV 다이버전스",    type="bool", unit="",  group="거래량", better="high"),
    # 모멘텀
    dict(key="ret5",      label="5일 수익률",   type="num", unit="%", group="모멘텀", better="high"),
    dict(key="ret20",     label="20일 수익률",  type="num", unit="%", group="모멘텀", better="high"),
    dict(key="ret60",     label="60일 수익률",  type="num", unit="%", group="모멘텀", better="high"),
    dict(key="ret120",    label="120일 수익률", type="num", unit="%", group="모멘텀", better="high"),
    dict(key="rsi14",     label="RSI(14)",      type="num", unit="",  group="모멘텀", better="none", decimals=1),
    dict(key="streak_up", label="연속 양봉",    type="num", unit="일", group="모멘텀", better="high", decimals=0),
    # 변동성 · 밴드
    dict(key="bb_gap",   label=f"BB상한({BB_LEN},{BB_K}) 대비", type="num", unit="%", group="변동성", better="high"),
    dict(key="bb_pctb",  label="%B (밴드 내 위치)",             type="num", unit="%", group="변동성", better="none", decimals=1),
    dict(key="bb_width", label="밴드폭(수축도)",                type="num", unit="%", group="변동성", better="low"),
    dict(key="atr20",    label="ATR(20)",                       type="num", unit="%", group="변동성", better="none"),
]


# ══════════════════════════════════════════════════════════════
# 데이터 수집
# ══════════════════════════════════════════════════════════════
END = datetime.now(KST).strftime("%Y-%m-%d")
START = (datetime.now(KST) - timedelta(days=LOOKBACK_D)).strftime("%Y-%m-%d")
NEED = ["Open", "High", "Low", "Close", "Volume"]


def fetch_ohlcv(code, tries=3):
    for a in range(tries):
        try:
            d = fdr.DataReader(code, START, END)
            if d is None or d.empty:
                return None
            d = d.rename(columns={c: str(c).capitalize() for c in d.columns})
            if not all(c in d.columns for c in NEED):
                return None
            return d[NEED].sort_index()
        except Exception:
            if a == tries - 1:
                return None
            time.sleep(0.8 * (a + 1))
    return None


def _col(df, *names):
    for nm in names:
        if nm in df.columns:
            return df[nm]
    return None


DIAG = {}


def kr_sector_map(tickers):
    """국내 업종(섹터) 매핑. 여러 경로를 순서대로 시도하고 진단 로그를 함께 돌려준다."""
    diag, best, best_hit = [], {}, 0

    # (1) FinanceDataReader 상세 리스팅들
    for src in ("KRX-DESC", "KRX", "KOSPI", "KOSDAQ"):
        try:
            df = fdr.StockListing(src)
        except Exception as e:
            diag.append(f"{src}=예외:{type(e).__name__}")
            continue
        if df is None or not len(df):
            diag.append(f"{src}=빈결과")
            continue
        c = _col(df, "Code", "Symbol", "종목코드")
        sec = _col(df, "Sector", "Industry", "업종", "업종명", "SectorName", "IndustryName")
        if c is None or sec is None:
            diag.append(f"{src}=업종컬럼없음({','.join(map(str, list(df.columns)[:12]))})")
            continue
        m = {}
        for k, v in zip(c.astype(str).str.zfill(6), sec.astype(str)):
            v = v.strip()
            if v and v.lower() not in ("nan", "none", "-"):
                m[k] = v
        hit = sum(1 for t in tickers if t in m)
        diag.append(f"{src}={hit}/{len(tickers)}")
        if hit > best_hit:
            best, best_hit = m, hit
        if best_hit >= len(tickers) * 0.8:
            return best, diag

    # (2) KRX 업종분류 현황 (깃허브 러너는 KRX 에 직접 접근할 수 있다)
    try:
        import requests
        url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
        day = datetime.now(KST).strftime("%Y%m%d")
        m = {}
        for mkt in ("STK", "KSQ"):
            r = requests.post(url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
            }, data={
                "bld": "dbms/MDC/STAT/standard/MDCSTAT03901",
                "mktId": mkt, "trdDd": day, "money": "1", "csvxls_isNo": "false",
            })
            for row in (r.json().get("block1") or r.json().get("OutBlock_1") or []):
                code = str(row.get("ISU_SRT_CD", "")).zfill(6)
                nm = str(row.get("IDX_IND_NM", "")).strip()
                if code and nm:
                    m[code] = nm
        hit = sum(1 for t in tickers if t in m)
        diag.append(f"KRX-API={hit}/{len(tickers)}")
        if hit > best_hit:
            best, best_hit = m, hit
    except Exception as e:
        diag.append(f"KRX-API=예외:{type(e).__name__}")

    return best, diag


def universe_kr():
    listing = fdr.StockListing("KRX")
    df = listing.copy()
    code = _col(df, "Code", "Symbol", "종목코드")
    name = _col(df, "Name", "종목명")
    if code is None:
        raise RuntimeError(f"종목코드 컬럼 없음: {list(df.columns)}")
    u = pd.DataFrame({
        "ticker": code.astype(str).str.zfill(6),
        "name": (name if name is not None else code).astype(str),
    })
    mc = _col(df, "Marcap", "MarketCap", "시가총액")
    u["mcap"] = pd.to_numeric(mc, errors="coerce") / 1e8 if mc is not None else np.nan
    amt = _col(df, "Amount", "거래대금")
    if amt is not None:
        u["value"] = pd.to_numeric(amt, errors="coerce") / 1e8
    else:
        c, v = _col(df, "Close", "종가"), _col(df, "Volume", "거래량")
        u["value"] = (pd.to_numeric(c, errors="coerce") * pd.to_numeric(v, errors="coerce") / 1e8
                      if c is not None and v is not None else np.nan)
    mk = _col(df, "Market", "시장구분")
    u["market_seg"] = mk.astype(str) if mk is not None else ""

    smap, diag = kr_sector_map(set(u["ticker"]))
    DIAG["kr_sector"] = diag
    u["sector"] = u["ticker"].map(smap).fillna("") if smap else ""
    blank = u["sector"].isin(["", "nan", "None"])
    u.loc[blank, "sector"] = u.loc[blank, "market_seg"]
    print("업종 매핑 진단: " + " | ".join(diag))

    u = u[~u["name"].str.contains("스팩", na=False)]
    u = u[~u["name"].str.contains(r"우[A-Z]?$|\d우", regex=True, na=False)]
    u = u[u["ticker"].str.endswith("0")]
    if u["mcap"].notna().any():
        u = u[u["mcap"].fillna(0) >= MIN_CAP_KR]
    if u["value"].notna().any():
        u = u[u["value"].fillna(0) >= MIN_VAL_KR]
    try:
        adm = fdr.StockListing("KRX-ADMINISTRATIVE")
        ac = _col(adm, "Code", "Symbol", "종목코드")
        if ac is not None:
            u = u[~u["ticker"].isin(set(ac.astype(str).str.zfill(6)))]
    except Exception as e:
        print(f"관리종목 조회 생략: {e}", file=sys.stderr)

    u = (u.drop_duplicates("ticker")
          .sort_values("value", ascending=False)
          .head(MAX_KR).reset_index(drop=True))
    return u


def universe_us():
    frames = []
    for src in ("S&P500", "NASDAQ"):
        try:
            frames.append(fdr.StockListing(src))
        except Exception as e:
            print(f"StockListing('{src}') 실패: {e}", file=sys.stderr)
    if not frames:
        raise RuntimeError("미국 종목 리스트를 받지 못했습니다")
    df = pd.concat(frames, ignore_index=True)
    sym = _col(df, "Symbol", "Code")
    nm = _col(df, "Name")
    u = pd.DataFrame({
        "ticker": sym.astype(str).str.strip(),
        "name": (nm if nm is not None else sym).astype(str),
    })
    mc = _col(df, "MarketCap", "Marcap")
    u["mcap"] = pd.to_numeric(mc, errors="coerce") / 1e6 if mc is not None else np.nan
    ind = _col(df, "Sector", "Industry")   # 큰 분류를 우선
    u["sector"] = ind.astype(str).str.strip() if ind is not None else ""
    DIAG["us_sector"] = [("컬럼=" + str(ind.name)) if ind is not None else "업종컬럼없음",
                         "가용컬럼=" + ",".join(map(str, list(df.columns)[:12]))]
    u["value"] = np.nan
    u = u[u["ticker"].str.fullmatch(r"[A-Z.\-]{1,6}", na=False)]
    if u["mcap"].notna().any():
        u = u[u["mcap"].fillna(0) >= MIN_CAP_US]
        u = u.sort_values("mcap", ascending=False)
    return u.drop_duplicates("ticker").head(MAX_US).reset_index(drop=True)


def run_market(market, uni):
    print(f"[{market}] universe = {len(uni):,}")
    data, failed = {}, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fut = {ex.submit(fetch_ohlcv, t): t for t in uni["ticker"]}
        for i, f in enumerate(as_completed(fut), 1):
            t = fut[f]
            try:
                d = f.result()
            except Exception:
                d = None
            if d is None:
                failed += 1
            else:
                data[t] = d
            if i % 200 == 0:
                print(f"  [{market}] {i}/{len(fut)}")
    print(f"[{market}] fetched ok={len(data):,} failed={failed:,}")

    rows = []
    for _, r in uni.iterrows():
        d = data.get(r["ticker"])
        if d is None:
            continue
        m = compute_metrics(d)
        if not m:
            continue
        m["ticker"] = r["ticker"]
        m["name"] = r["name"]
        m["sector"] = r.get("sector", "") or ""
        mc, vl = r.get("mcap"), r.get("value")
        m["mcap"] = _r(mc, 0) if pd.notna(mc) else None
        # 거래대금이 리스팅에 없으면 마지막 봉으로 추정
        if pd.notna(vl):
            m["value"] = _r(vl, 0)
        else:
            last = d.iloc[-1]
            div = 1e8 if market == "kr" else 1e6
            m["value"] = _r(float(last["Close"]) * float(last["Volume"]) / div, 0)
        rows.append(m)

    rows.sort(key=lambda x: (x.get("value") or 0), reverse=True)

    # 종목이 3개 미만인 꼬리 업종은 '기타' 로 묶는다 (섹터 화면이 잘게 부서지는 것 방지)
    cnt = {}
    for r in rows:
        k = (r.get("sector") or "").strip()
        cnt[k] = cnt.get(k, 0) + 1
    for r in rows:
        k = (r.get("sector") or "").strip()
        r["sector"] = k if k and cnt.get(k, 0) >= 3 else ("기타" if k else "미분류")
    print(f"[{market}] 섹터 {len(set(r['sector'] for r in rows))}개")

    # 미국은 시총·거래대금 단위가 백만달러
    sch = [dict(s) for s in SCHEMA]
    if market == "us":
        for s in sch:
            if s["key"] in ("mcap", "value"):
                s["unit"] = "M$"

    # 절반도 못 채운 지표는 스키마에서 뺀다 — 필터를 걸면 전 종목이 사라지기 때문.
    # (미국 종목 리스트에는 시가총액 컬럼이 없는 경우가 있다)
    if rows:
        keep = []
        for s in sch:
            filled = sum(1 for r in rows if r.get(s["key"]) is not None)
            if s["type"] == "bool" or filled / len(rows) >= 0.5:
                keep.append(s)
            else:
                print(f"[{market}] 스키마에서 제외: {s['label']} (채움률 {filled/len(rows)*100:.0f}%)")
        sch = keep

    return {
        "market": market,
        "date": END,
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "count": len(rows),
        "failed": failed,
        "params": {"bb_len": BB_LEN, "bb_k": BB_K},
        "diag": DIAG.get(market + "_sector", []),
        "schema": sch,
        "rows": rows,
    }


# ══════════════════════════════════════════════════════════════
def self_test():
    def F(c, o=None, v=None):
        c = np.asarray(c, float)
        o = np.asarray(o, float) if o is not None else c * 0.99
        v = np.asarray(v, float) if v is not None else np.full(len(c), 1e6)
        return pd.DataFrame({"Open": o, "High": np.maximum(c, o) * 1.005,
                             "Low": np.minimum(c, o) * 0.995, "Close": c, "Volume": v})

    up = np.linspace(100, 200, 300)
    m = compute_metrics(F(up))
    assert m["ma_align"] and not m["ma_align_rev"], "우상향인데 정배열이 아님"
    assert m["hi_gap252"] > -1.0, m["hi_gap252"]
    assert m["ma_above_cnt"] == 4 and m["obv_high"]
    assert m["streak_up"] >= 3 and m["rsi14"] > 60

    # 신고가: 당일 고가가 직전 기간 최고 고가를 넘으면 갱신
    assert m["is_hi252"] and m["is_hi20"], "우상향 종목인데 신고가로 안 잡힘"
    fall = F(up.copy())
    fall.loc[fall.index[-1], ["High", "Close", "Open"]] = float(up[-1]) * 0.9
    r = compute_metrics(fall)
    assert not r["is_hi252"] and not r["is_hi20"], "고가가 못 넘었는데 신고가로 잡힘"
    assert r["hi_gap252"] < -5

    dn = np.linspace(200, 100, 300)
    m2 = compute_metrics(F(dn, dn * 1.01))
    assert m2["ma_align_rev"] and not m2["ma_align"], "우하향인데 역배열이 아님"
    assert m2["hi_gap252"] < -40 and m2["streak_up"] == 0

    flat = np.full(300, 100.0) + np.sin(np.arange(300) / 5)
    m3 = compute_metrics(F(flat))
    assert not m3["ma_align"] and not m3["ma_align_rev"]
    assert 0 <= m3["bb_pctb"] <= 100 or m3["bb_pctb"] is None

    v = np.full(300, 1e6); v[-1] = 5e6
    m4 = compute_metrics(F(up, None, v))
    assert 4.5 < m4["vol_x20"] < 5.5, m4["vol_x20"]

    assert list(obv_series(pd.Series([10, 11, 10, 12, 12.]),
                           pd.Series([100, 200, 300, 400, 500.]))) == [0, 200, -100, 300, 300]
    keys = {s["key"] for s in SCHEMA}
    missing = keys - set(m.keys()) - {"mcap", "value"}
    assert not missing, f"SCHEMA 에 있으나 계산되지 않는 지표: {missing}"
    print("self-test OK (8 cases)")


def main():
    self_test()
    os.makedirs("output", exist_ok=True)
    targets = os.getenv("MARKETS", "kr,us").split(",")
    for mk in [t.strip() for t in targets if t.strip()]:
        try:
            uni = universe_kr() if mk == "kr" else universe_us()
            payload = run_market(mk, uni)
            path = f"output/universe_{mk}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            print(f"wrote {path} — {payload['count']:,} rows, {os.path.getsize(path):,} bytes")
        except Exception:
            traceback.print_exc()
            print(f"[{mk}] 실패 — 기존 파일 유지", file=sys.stderr)


if __name__ == "__main__":
    main()
