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
def _safe_str(v):
    """NaN/None/실수 등 무엇이 오든 안전한 문자열로 만든다."""
    if v is None:
        return ""
    if isinstance(v, float):
        if not np.isfinite(v):
            return ""
        return str(v)
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none", "-", "<na>") else t


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
    # 밸류에이션 — 적자 종목은 PER 이 비어 있다(음수 PER 은 해석이 불가능해 제외)
    dict(key="per",        label="PER",                type="num", unit="배", group="밸류에이션", better="low",  decimals=1),
    dict(key="fwd_per",    label="선행 PER(12M)",      type="num", unit="배", group="밸류에이션", better="low",  decimals=1),
    dict(key="pbr",        label="PBR",                type="num", unit="배", group="밸류에이션", better="low",  decimals=2),
    dict(key="roe",        label="ROE",                type="num", unit="%",  group="밸류에이션", better="high", decimals=1),
    dict(key="dvd_yld",    label="배당수익률",          type="num", unit="%",  group="밸류에이션", better="high", decimals=2),
    dict(key="eps_growth", label="EPS 증가율(선행/후행)", type="num", unit="%", group="밸류에이션", better="high", decimals=1),
    dict(key="per_vs_sec", label="업종 대비 PER",       type="num", unit="%",  group="밸류에이션", better="low",  decimals=0),
    dict(key="pbr_vs_sec", label="업종 대비 PBR",       type="num", unit="%",  group="밸류에이션", better="low",  decimals=0),
    dict(key="per_pos",    label="52주 PER 밴드 위치",  type="num", unit="%",  group="밸류에이션", better="low",  decimals=1),
    dict(key="eps",        label="EPS",                type="num", unit="",   group="밸류에이션", better="high", decimals=0),
    dict(key="bps",        label="BPS",                type="num", unit="",   group="밸류에이션", better="high", decimals=0),
    dict(key="fwd_eps",    label="선행 EPS(12M)",      type="num", unit="",   group="밸류에이션", better="high", decimals=0),
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


def _sector_from_file():
    """리포에 커밋해 둔 업종 매핑(output/sector_map_kr.json)을 읽는다.

    KRX·네이버는 깃허브 액션 러너 IP 를 막기 때문에 액션 안에서는 업종을
    받을 수 없다. make_sector_map.py 를 내 PC 에서 한 번 돌려 만든 이 파일이
    있으면 외부 접속 없이 업종이 채워진다."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "output", "sector_map_kr.json")
    if not os.path.exists(path):
        return {}, "(파일 없음)"
    try:
        with open(path, encoding="utf-8") as f:
            js = json.load(f)
    except Exception as e:
        return {}, f"(읽기 실패 {type(e).__name__}:{str(e)[:40]})"
    raw = js.get("map") or {}
    m = {}
    for k, v in raw.items():
        code, nm = str(k).zfill(6), _safe_str(v)
        if code and nm:
            m[code] = nm
    return m, f"(생성 {js.get('generated_at', '?')[:10]}, 출처 {js.get('source', '?')})"


def kr_sector_map(tickers):
    """국내 업종(섹터) 매핑. 여러 경로를 순서대로 시도하고 진단 로그를 함께 돌려준다."""
    diag, best, best_hit = [], {}, 0

    # (0) 리포에 커밋된 업종 매핑 — 액션에서 외부 접속이 막혀도 이건 항상 읽힌다
    fm, fnote = _sector_from_file()
    if fm:
        hit = sum(1 for t in tickers if t in fm)
        diag.append(f"FILE={hit}/{len(tickers)}{fnote}")
        best, best_hit = fm, hit
        if best_hit >= len(tickers) * 0.6:
            return best, diag
    else:
        diag.append(f"FILE=0{fnote}")

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

    # (2) 네이버 금융 업종 분류 — 가장 안정적인 경로
    try:
        m = _sector_from_naver()
        hit = sum(1 for t in tickers if t in m)
        diag.append(f"NAVER={hit}/{len(tickers)}")
        if hit > best_hit:
            best, best_hit = m, hit
        if best_hit >= len(tickers) * 0.6:
            return best, diag
    except Exception as e:
        diag.append(f"NAVER=예외:{type(e).__name__}:{str(e)[:60]}")

    # (3) pykrx — KRX 업종지수의 구성종목으로 업종을 역산
    try:
        m = _sector_from_pykrx()
        hit = sum(1 for t in tickers if t in m)
        diag.append(f"PYKRX={hit}/{len(tickers)}")
        if hit > best_hit:
            best, best_hit = m, hit
        if best_hit >= len(tickers) * 0.6:
            return best, diag
    except Exception as e:
        diag.append(f"PYKRX=예외:{type(e).__name__}:{str(e)[:60]}")

    # (4) KRX 업종분류 현황 API
    try:
        m, note = _sector_from_krx()
        hit = sum(1 for t in tickers if t in m)
        diag.append(f"KRX-API={hit}/{len(tickers)}{note}")
        if hit > best_hit:
            best, best_hit = m, hit
    except Exception as e:
        diag.append(f"KRX-API=예외:{type(e).__name__}:{str(e)[:60]}")

    return best, diag


def _sector_from_naver():
    """네이버 금융 업종별 시세에서 종목코드 → 업종명 매핑을 만든다."""
    import re
    import requests
    H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    BASE = "https://finance.naver.com"
    def _get(u):
        """네이버가 EUC-KR 에서 UTF-8 로 바뀌어도 읽히도록 인코딩을 자동 판별한다."""
        b = requests.get(u, headers=H, timeout=30).content
        for enc in ("euc-kr", "cp949", "utf-8"):
            try:
                return b.decode(enc)
            except UnicodeDecodeError:
                continue
        return b.decode("utf-8", "ignore")

    html = _get(BASE + "/sise/sise_group.naver?type=upjong")
    links = re.findall(r'href="([^"]*sise_group_detail[^"]*)"[^>]*>(.*?)</a>', html, re.S)
    if not links:   # 페이지 개편 대비 — 링크 태그 모양이 달라져도 no= 와 라벨만 건진다
        links = [(f"no={no}", lab) for no, lab in
                 re.findall(r'type=upjong&(?:amp;)?no=(\d+)[^>]*>\s*([^<]{1,40})', html)]
    groups = []
    seen = set()
    for href, label in links:
        mo = re.search(r'no=(\d+)', href)
        nm = re.sub(r"<[^>]+>", "", label).replace("&amp;", "&").strip()
        if mo and nm and mo.group(1) not in seen:
            seen.add(mo.group(1))
            groups.append((mo.group(1), nm))
    if not groups:
        i = html.find("upjong")
        snip = html[max(0, i - 140):i + 200] if i >= 0 else html[:220]
        raise RuntimeError(f"업종 목록 파싱 실패(len={len(html)}) 조각={snip!r}"[:420])
    m = {}
    for no, nm in groups:
        dh = _get(f"{BASE}/sise/sise_group_detail.naver?type=upjong&no={no}")
        for code in re.findall(r'code=(\d{6})', dh):
            m.setdefault(code, nm)
        time.sleep(0.12)
    return m


def _sector_from_pykrx():
    """pykrx 로 KRX 업종지수별 구성종목을 받아 종목 → 업종 매핑을 만든다."""
    try:
        from pykrx import stock
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pykrx"],
                       check=True, timeout=300)
        from pykrx import stock

    day = None
    for back in range(0, 15):
        d = (datetime.now(KST) - timedelta(days=back)).strftime("%Y%m%d")
        try:
            if len(stock.get_index_ticker_list(date=d, market="KOSPI")):
                day = d
                break
        except Exception:
            continue
    if day is None:
        raise RuntimeError("영업일을 찾지 못함")

    # 업종이 아닌 규모별·전체 지수는 제외
    SKIP = ("코스피", "코스닥", "대형주", "중형주", "소형주", "지수", "우선주",
            "KRX", "K-뉴딜", "배당", "가치", "ESG", "섹터", "글로벌")
    m = {}
    for market in ("KOSPI", "KOSDAQ"):
        for idx in stock.get_index_ticker_list(date=day, market=market):
            try:
                nm = stock.get_index_ticker_name(idx).strip()
            except Exception:
                continue
            if any(k in nm for k in SKIP):
                continue
            try:
                codes = stock.get_index_portfolio_deposit_file(idx, date=day)
            except Exception:
                continue
            for c in codes or []:
                m.setdefault(str(c).zfill(6), nm)
            time.sleep(0.05)
    if not m:
        raise RuntimeError("업종지수 구성종목이 비어 있음")
    return m


# ── KRX 데이터포털 공통 세션 ────────────────────────────────
# data.krx.co.kr 은 쿠키 없이 getJsonData.cmd 로 POST 하면 본문에 'LOGOUT' 을
# 돌려준다(HTTP 400). 로더 페이지를 먼저 GET 해서 세션 쿠키를 받아두고,
# 그 세션으로만 POST 한다. 쿠키가 만료되면 한 번 재발급 후 재시도한다.
_KRX_SESS = None
_KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
_KRX_REF = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd"


def _krx_session(fresh=False):
    global _KRX_SESS
    if _KRX_SESS is not None and not fresh:
        return _KRX_SESS
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Referer": _KRX_REF,
        "Origin": "https://data.krx.co.kr",
        "X-Requested-With": "XMLHttpRequest",
    })
    for u in (_KRX_REF + "?menuId=MDC0201", "https://data.krx.co.kr/"):
        try:
            s.get(u, timeout=20)
        except Exception:
            pass
    _KRX_SESS = s
    return s


def _krx_post(bld, **data):
    """KRX getJsonData 호출. 쿠키 문제면 세션을 새로 받아 한 번 더 시도한다."""
    global _KRX_SESS
    payload = {"bld": bld, "locale": "ko_KR", "csvxls_isNo": "false", "money": "1"}
    payload.update(data)
    last = ""
    for attempt in (0, 1):
        s = _krx_session(fresh=(attempt == 1))
        try:
            r = s.post(_KRX_URL, data=payload, timeout=30)
        except Exception as e:
            last = f"{type(e).__name__}:{str(e)[:50]}"
            continue
        txt = (r.text or "").strip()
        if txt.startswith("{") or txt.startswith("["):
            try:
                return r.json()
            except Exception as e:
                last = f"JSON파싱:{str(e)[:40]}"
                continue
        last = f"비JSON {r.status_code}: {txt[:60]!r}"
        _KRX_SESS = None          # 쿠키 재발급 유도
    raise RuntimeError(last or "KRX 응답 없음")


def _krx_rows(js):
    return js.get("block1") or js.get("OutBlock_1") or js.get("output") or []


def _krx_num(v):
    """'1,234,567' → 1234567.0, 빈값/'-' → nan"""
    t = str(v or "").replace(",", "").strip()
    if not t or t in ("-", "N/A"):
        return float("nan")
    try:
        return float(t)
    except Exception:
        return float("nan")


def _krx_business_days(n=10):
    for back in range(n):
        yield (datetime.now(KST) - timedelta(days=back)).strftime("%Y%m%d")


def krx_market_data():
    """전종목 시세(MDCSTAT01501)로 시가총액·거래대금·시장구분을 받는다.
    FinanceDataReader 의 KRX 리스팅에서 Marcap/Amount 컬럼이 사라졌을 때의 대체 경로."""
    note = ""
    for day in _krx_business_days(10):
        out = {}
        for mkt in ("STK", "KSQ"):
            try:
                js = _krx_post("dbms/MDC/STAT/standard/MDCSTAT01501",
                               mktId=mkt, trdDd=day, share="1")
            except Exception as e:
                note = f"({mkt} {type(e).__name__}:{str(e)[:50]})"
                return {}, note
            for row in _krx_rows(js):
                code = str(row.get("ISU_SRT_CD") or "").zfill(6)
                if not code or code in out:   # 시장 간 중복 시 먼저 받은 쪽을 남긴다
                    continue
                out[code] = {
                    "mcap": _krx_num(row.get("MKTCAP")) / 1e8,        # 억원
                    "value": _krx_num(row.get("ACC_TRDVAL")) / 1e8,   # 억원
                    "seg": "KOSPI" if mkt == "STK" else "KOSDAQ",
                }
        if out:
            return out, f"(기준일 {day}, {len(out)}종목)"
    return {}, note or "(모든 날짜 빈결과)"


# PER/PBR/배당수익률 화면은 시세 화면과 menuId 가 달라, 세션을 그 화면으로
# 맞춰 주지 않으면 KRX 가 400 'LOGOUT' 을 돌려준다.
_KRX_FUND_VARIANTS = [
    ("MDC0201020506", {"searchType": "1", "share": "1"}),
    ("MDC0201020506", {"share": "1"}),
    ("MDC0201020506", {"searchType": "1", "share": "1", "strtDd": "", "endDd": "",
                       "isuCd": "", "isuCd2": ""}),
    ("MDC0201", {"searchType": "1", "share": "1"}),
]


def _krx_prime(menu_id):
    """해당 통계 화면을 한 번 열어 세션 쿠키를 그 화면에 맞춘다."""
    s = _krx_session()
    for u in (f"{_KRX_REF}?menuId={menu_id}", _KRX_REF):
        try:
            s.get(u, timeout=20)
        except Exception:
            pass
    return s


def krx_fundamental(day=None, _variant=None):
    """전종목 PER/EPS/PBR/BPS/DPS/배당수익률 (MDCSTAT03501)."""
    global _KRX_SESS
    days = [day] if day else list(_krx_business_days(10))
    variants = [_variant] if _variant else _KRX_FUND_VARIANTS
    notes = []
    for menu_id, extra in variants:
        _KRX_SESS = None                 # 화면별로 세션을 새로 잡는다
        _krx_prime(menu_id)
        for d in days:
            out, err = {}, ""
            for mkt in ("STK", "KSQ"):
                try:
                    js = _krx_post("dbms/MDC/STAT/standard/MDCSTAT03501",
                                   mktId=mkt, trdDd=d, **extra)
                except Exception as e:
                    err = f"{type(e).__name__}:{str(e)[:44]}"
                    break
                for row in _krx_rows(js):
                    code = str(row.get("ISU_SRT_CD") or "").zfill(6)
                    if not code or code in out:
                        continue
                    out[code] = {
                        "eps": _krx_num(row.get("EPS")),
                        "per": _krx_num(row.get("PER")),
                        "bps": _krx_num(row.get("BPS")),
                        "pbr": _krx_num(row.get("PBR")),
                        "dps": _krx_num(row.get("DPS")),
                        "dvd": _krx_num(row.get("DVD_YLD")),
                    }
            if out:
                return out, f"(기준일 {d}, menu {menu_id}, {len(out)}종목)"
            if err:
                notes.append(f"{menu_id}:{err}")
                break                     # 같은 변형으로 날짜만 바꿔도 소용없다
        else:
            notes.append(f"{menu_id}:모든날짜빈결과")
    return {}, "(" + " / ".join(notes[:4]) + ")"


def load_fundamental_file():
    """로컬에서 뽑아 커밋해 둔 실적(output/consensus_kr.json)의 EPS·BPS.
    EPS·BPS 는 분기에 한 번만 바뀌므로, 주가만 매일 나눠 주면 PER·PBR 이 최신이 된다."""
    m, note = load_consensus_kr()
    if not m:
        return {}, note
    out = {}
    for code, v in m.items():
        eps, bps = v.get("eps"), v.get("bps")
        if eps is None and bps is None:
            continue
        out[code] = {"eps": eps, "bps": bps, "dps": v.get("dps"),
                     "per": None, "pbr": None, "dvd": v.get("dvd")}
    return out, note


def krx_per_history(months=12):
    """월 1회 스냅샷으로 최근 1년치 PER 을 모아 밴드 위치를 계산할 재료를 만든다.
    EPS 가 분기마다 바뀌므로, 주가 밴드와 달리 PER 밴드는 독립적인 정보를 준다."""
    hist, taken = {}, []
    now = datetime.now(KST)
    for k in range(1, months + 1):
        target = now - timedelta(days=30 * k)
        got = None
        for back in range(0, 6):          # 휴장이면 직전 영업일로
            d = (target - timedelta(days=back)).strftime("%Y%m%d")
            try:
                snap, _ = krx_fundamental(d)
            except Exception:
                snap = {}
            if snap:
                got = (d, snap)
                break
        if not got:
            continue
        d, snap = got
        taken.append(d)
        for code, v in snap.items():
            per = v.get("per")
            if per and np.isfinite(per) and per > 0:
                hist.setdefault(code, []).append(per)
        time.sleep(0.2)
    return hist, f"({len(taken)}개월치: {','.join(taken[:3])}…)" if taken else "(수집 실패)"


def _sector_from_krx():
    """KRX 업종분류 현황(MDCSTAT03901). 휴장이면 직전 영업일까지 되짚는다."""
    note, m = "", {}
    for day in _krx_business_days(6):
        m = {}
        for mkt in ("STK", "KSQ"):
            try:
                js = _krx_post("dbms/MDC/STAT/standard/MDCSTAT03901",
                               mktId=mkt, trdDd=day)
            except Exception as e:
                return {}, f"({mkt} {type(e).__name__}:{str(e)[:60]})"
            for row in _krx_rows(js):
                code = str(row.get("ISU_SRT_CD") or row.get("ISU_CD") or "").zfill(6)
                nm = str(row.get("IDX_IND_NM") or row.get("SECT_TP_NM") or "").strip()
                if code and nm:
                    m[code] = nm
        if m:
            return m, f"(기준일 {day})"
    return m, note or "(모든 날짜 빈결과)"


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

    # FDR 의 KRX 리스팅에서 Marcap/Amount 컬럼이 사라지면 시총 필터가 통째로
    # 무력화돼 유니버스가 KONEX 까지 부풀고 mcap 이 스키마에서 빠진다.
    # 그때는 KRX 전종목 시세로 메운다.
    need_cap = not u["mcap"].notna().any()
    need_val = not u["value"].notna().any()
    if need_cap or need_val:
        try:
            md, mnote = krx_market_data()
        except Exception as e:
            md, mnote = {}, f"(예외 {type(e).__name__}:{str(e)[:50]})"
        DIAG["kr_market"] = [f"KRX-시세={len(md)}종목{mnote}"]
        print("시총/거래대금 보완: " + DIAG["kr_market"][0])
        if md:
            if need_cap:
                u["mcap"] = u["ticker"].map(lambda t: md.get(t, {}).get("mcap", np.nan))
            if need_val:
                u["value"] = u["ticker"].map(lambda t: md.get(t, {}).get("value", np.nan))
            blank_seg = u["market_seg"].isin(["", "nan", "None"])
            u.loc[blank_seg, "market_seg"] = u.loc[blank_seg, "ticker"].map(
                lambda t: md.get(t, {}).get("seg", ""))

    # KONEX 는 거래가 거의 없어 지표가 의미를 갖지 못한다 — 유니버스에서 제외
    u = u[~u["market_seg"].str.upper().str.contains("KONEX|코넥스", na=False)]

    smap, diag = kr_sector_map(set(u["ticker"]))
    DIAG["kr_sector"] = diag
    u["sector"] = u["ticker"].map(smap).map(_safe_str) if smap else ""
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
    frames, notes = [], []
    for src in ("S&P500", "NASDAQ", "NYSE"):
        try:
            f = fdr.StockListing(src)
            if f is not None and len(f):
                frames.append(f)
                notes.append(f"{src}={len(f)}행({','.join(map(str, list(f.columns)[:8]))})")
            else:
                notes.append(f"{src}=빈결과")
        except Exception as e:
            notes.append(f"{src}=예외:{type(e).__name__}:{str(e)[:50]}")
    DIAG["us_listing"] = notes
    print("미국 리스팅: " + " | ".join(notes))
    if not frames:
        raise RuntimeError("미국 종목 리스트를 받지 못했습니다 — " + " | ".join(notes))
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
    u["sector"] = ind.map(_safe_str) if ind is not None else ""
    DIAG["us_sector"] = DIAG.get("us_listing", []) + [
        ("업종컬럼=" + str(ind.name)) if ind is not None else "업종컬럼없음",
        "가용컬럼=" + ",".join(map(str, list(df.columns)[:14])),
    ]
    u["value"] = np.nan
    u = u[u["ticker"].str.fullmatch(r"[A-Z.\-]{1,6}", na=False)]
    if u["mcap"].notna().any():
        u = u[u["mcap"].fillna(0) >= MIN_CAP_US]
        u = u.sort_values("mcap", ascending=False)
    return u.drop_duplicates("ticker").head(MAX_US).reset_index(drop=True)


def yahoo_fundamentals(symbols):
    """Yahoo quote 배치 조회로 미국 종목의 PER·선행PER·PBR·배당수익률·BPS 를 받는다."""
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"})
    crumb = ""
    try:
        s.get("https://fc.yahoo.com", timeout=15)
        r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15)
        crumb = (r.text or "").strip()
    except Exception as e:
        return {}, [f"crumb 실패:{type(e).__name__}"]
    if not crumb or len(crumb) > 40:
        return {}, [f"crumb 이상:{crumb[:30]!r}"]

    FIELDS = ("trailingPE,forwardPE,priceToBook,epsTrailingTwelveMonths,epsForward,"
              "bookValue,trailingAnnualDividendYield,dividendYield,marketCap")
    out, notes, bad = {}, [], 0
    syms = list(symbols)
    for i in range(0, len(syms), 50):
        chunk = syms[i:i + 50]
        try:
            r = s.get("https://query1.finance.yahoo.com/v7/finance/quote", timeout=30,
                      params={"symbols": ",".join(chunk), "crumb": crumb, "fields": FIELDS})
            js = r.json()
        except Exception as e:
            bad += 1
            if bad <= 2:
                notes.append(f"배치{i//50}:{type(e).__name__}")
            continue
        for q in (js.get("quoteResponse", {}).get("result") or []):
            sym = q.get("symbol")
            if not sym:
                continue
            dy = q.get("dividendYield")
            if dy is not None and dy < 1:      # 비율로 오는 경우가 있어 % 로 맞춘다
                dy *= 100
            out[sym] = {
                "per": q.get("trailingPE"),
                "fwd_per": q.get("forwardPE"),
                "pbr": q.get("priceToBook"),
                "eps": q.get("epsTrailingTwelveMonths"),
                "fwd_eps": q.get("epsForward"),
                "bps": q.get("bookValue"),
                "dvd": dy if dy is not None else q.get("trailingAnnualDividendYield"),
                "mcap": (q.get("marketCap") / 1e6) if q.get("marketCap") else None,
            }
        time.sleep(0.25)
    notes.insert(0, f"{len(out)}/{len(syms)}종목")
    return out, notes


def load_consensus_kr():
    """로컬에서 뽑아 커밋해 둔 국내 컨센서스(output/consensus_kr.json)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "output", "consensus_kr.json")
    if not os.path.exists(path):
        return {}, "파일없음"
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        return {}, f"읽기실패:{type(e).__name__}"
    m = d.get("map") or {}
    return m, f"{len(m)}종목(생성 {str(d.get('generated_at'))[:10]})"


def previous_universe(market):
    """직전 산출물에서 종목 목록을 복원한다 (리스팅 조회가 깨졌을 때의 안전망)."""
    path = f"output/universe_{market}.json"
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    rows = d.get("rows") or []
    if not rows:
        return None
    return pd.DataFrame([{
        "ticker": r.get("ticker", ""),
        "name": r.get("name", ""),
        "mcap": r.get("mcap"),
        "value": r.get("value"),
        "sector": r.get("sector", ""),
    } for r in rows if r.get("ticker")])


def attach_valuation(market, rows):
    """실적 기반 밸류에이션을 붙인다. 실패해도 나머지 지표는 그대로 살린다."""
    notes = []
    fund, fwd, hist = {}, {}, {}

    if market == "kr":
        try:
            fund, note = krx_fundamental()
            notes.append(f"KRX실적={len(fund)}종목{note}")
        except Exception as e:
            notes.append(f"KRX실적=예외:{type(e).__name__}:{str(e)[:50]}")
        fwd, fnote = load_consensus_kr()
        notes.append(f"컨센서스={fnote}")
        if not fund:      # KRX 가 막히면 커밋해 둔 EPS·BPS 로 주가에서 직접 계산
            fund, bnote = load_fundamental_file()
            notes.append(f"파일실적={len(fund)}종목({bnote})")
        if fund and os.getenv("PER_BAND", "1") != "0":
            try:
                hist, hnote = krx_per_history()
                notes.append(f"PER이력={len(hist)}종목{hnote}")
            except Exception as e:
                notes.append(f"PER이력=예외:{type(e).__name__}:{str(e)[:50]}")
    else:
        try:
            fund, ynotes = yahoo_fundamentals([r["ticker"] for r in rows])
            notes.append("Yahoo=" + " ".join(ynotes))
        except Exception as e:
            notes.append(f"Yahoo=예외:{type(e).__name__}:{str(e)[:50]}")

    def pick(d, k):
        v = d.get(k)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if np.isfinite(v) and v != 0 else None

    for r in rows:
        f = fund.get(r["ticker"]) or {}
        per, pbr = pick(f, "per"), pick(f, "pbr")
        eps, bps = pick(f, "eps"), pick(f, "bps")
        px = r.get("price")
        if per is None and eps and eps > 0 and px:
            per = px / eps                      # 커밋된 EPS + 오늘 주가
        if pbr is None and bps and bps > 0 and px:
            pbr = px / bps
        if pick(f, "dvd") is None and pick(f, "dps") and px:
            f = dict(f, dvd=pick(f, "dps") / px * 100)
        r["per"] = _r(per) if per and per > 0 else None       # 적자(음수 PER)는 의미가 없어 비움
        r["pbr"] = _r(pbr) if pbr and pbr > 0 else None
        r["eps"] = _r(eps, 0)
        r["bps"] = _r(bps, 0)
        r["dvd_yld"] = _r(pick(f, "dvd"))
        # ROE = EPS / BPS — KRX·Yahoo 둘 다 같은 정의로 계산된다
        r["roe"] = _r(eps / bps * 100) if eps is not None and bps and bps > 0 else None
        if market == "us" and pick(f, "mcap") and not r.get("mcap"):
            r["mcap"] = _r(pick(f, "mcap"), 0)

        # 12개월 선행
        if market == "us":
            fp, fe = pick(f, "fwd_per"), pick(f, "fwd_eps")
        else:
            c = fwd.get(r["ticker"]) or {}
            fe = pick(c, "fwd_eps")
            fp = pick(c, "fwd_per")
            if fp is None and fe and fe > 0 and r.get("price"):
                fp = r["price"] / fe
        r["fwd_per"] = _r(fp) if fp and fp > 0 else None
        r["fwd_eps"] = _r(fe, 0)
        # 이익 개선폭: 선행 EPS 가 후행 EPS 대비 얼마나 늘어나는가
        r["eps_growth"] = (_r((fe / eps - 1) * 100)
                           if fe is not None and eps and eps > 0 else None)

        # 52주 PER 밴드 내 위치
        hs = hist.get(r["ticker"]) or []
        if r["per"] and len(hs) >= 4:
            lo, hi = min(hs + [r["per"]]), max(hs + [r["per"]])
            r["per_pos"] = _r((r["per"] - lo) / (hi - lo) * 100, 1) if hi > lo else None
        else:
            r["per_pos"] = None

    # 업종 대비 (같은 업종 중앙값 = 100)
    for key, out in (("per", "per_vs_sec"), ("pbr", "pbr_vs_sec")):
        by = {}
        for r in rows:
            if r.get(key):
                by.setdefault(r["sector"], []).append(r[key])
        med = {k: sorted(v)[len(v) // 2] for k, v in by.items() if len(v) >= 3}
        for r in rows:
            m = med.get(r["sector"])
            r[out] = _r(r[key] / m * 100) if r.get(key) and m else None

    filled = sum(1 for r in rows if r.get("per") is not None)
    notes.append(f"PER채움={filled}/{len(rows)}")
    print(f"[{market}] 밸류에이션: " + " | ".join(notes))
    return notes


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
        m["sector"] = _safe_str(r.get("sector"))
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
        k = _safe_str(r.get("sector"))
        r["sector"] = k
        cnt[k] = cnt.get(k, 0) + 1
    for r in rows:
        k = r["sector"]
        r["sector"] = k if k and cnt.get(k, 0) >= 3 else ("기타" if k else "미분류")
    print(f"[{market}] 섹터 {len(set(r['sector'] for r in rows))}개")

    try:
        DIAG[market + "_valuation"] = attach_valuation(market, rows)
    except Exception as e:
        traceback.print_exc()
        DIAG[market + "_valuation"] = [f"전체실패:{type(e).__name__}:{str(e)[:80]}"]

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
        "diag": DIAG.get(market + "_sector", []) + DIAG.get(market + "_valuation", []),
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
    VALUATION = {"per", "fwd_per", "pbr", "roe", "dvd_yld", "eps_growth",
                 "per_vs_sec", "pbr_vs_sec", "per_pos", "eps", "bps", "fwd_eps"}
    missing = keys - set(m.keys()) - {"mcap", "value"} - VALUATION
    assert not missing, f"SCHEMA 에 있으나 계산되지 않는 지표: {missing}"
    # 밸류에이션 결합 — 적자·결측·업종 대비가 의도대로 처리되는지
    rows = [
        {"ticker": "A", "sector": "반도체", "price": 1000.0},
        {"ticker": "B", "sector": "반도체", "price": 2000.0},
        {"ticker": "C", "sector": "반도체", "price": 3000.0},
        {"ticker": "D", "sector": "반도체", "price": 4000.0},
    ]
    fake = {"A": {"per": 10, "pbr": 1.0, "eps": 100, "bps": 1000, "dvd": 2.0},
            "B": {"per": 20, "pbr": 2.0, "eps": 100, "bps": 1000, "dvd": 0},
            "C": {"per": -5, "pbr": 3.0, "eps": -50, "bps": 1000, "dvd": 0},
            "D": {"per": 30, "pbr": 4.0, "eps": 133, "bps": 1000, "dvd": 1.0}}
    _orig = globals()["krx_fundamental"]
    globals()["krx_fundamental"] = lambda day=None: (fake, "(테스트)")
    os.environ["PER_BAND"] = "0"
    try:
        attach_valuation("kr", rows)
    finally:
        globals()["krx_fundamental"] = _orig
    assert rows[2]["per"] is None, "적자 종목의 PER 이 남아 있음"
    assert rows[0]["roe"] == 10.0, rows[0]["roe"]
    assert rows[1]["per_vs_sec"] == 100.0, rows[1]["per_vs_sec"]   # 중앙값 20배
    assert rows[3]["per_vs_sec"] == 150.0, rows[3]["per_vs_sec"]
    assert rows[2]["per_vs_sec"] is None
    assert rows[0]["dvd_yld"] == 2.0 and rows[1]["dvd_yld"] is None

    print("self-test OK (9 cases)")


def main():
    self_test()
    os.makedirs("output", exist_ok=True)
    targets = os.getenv("MARKETS", "kr,us").split(",")
    for mk in [t.strip() for t in targets if t.strip()]:
        try:
            try:
                uni = universe_kr() if mk == "kr" else universe_us()
            except Exception as e:
                uni = previous_universe(mk)
                note = f"유니버스조회실패:{type(e).__name__}:{str(e)[:80]}"
                if uni is None or not len(uni):
                    raise
                note += f" → 직전 목록 {len(uni):,}종목으로 진행"
                print(note, file=sys.stderr)
                DIAG[mk + "_sector"] = DIAG.get(mk + "_sector", []) + [note]
            payload = run_market(mk, uni)
            path = f"output/universe_{mk}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            print(f"wrote {path} — {payload['count']:,} rows, {os.path.getsize(path):,} bytes")
        except Exception as e:
            traceback.print_exc()
            print(f"[{mk}] 실패 — 기존 파일 유지", file=sys.stderr)
            try:   # 실패 사유를 기존 산출물에 덧붙여 다음에 읽을 수 있게 한다
                path = f"output/universe_{mk}.json"
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as f:
                        d = json.load(f)
                    d["last_error"] = (f"{datetime.now(KST).strftime('%Y-%m-%d %H:%M')} "
                                       f"{type(e).__name__}: {str(e)[:200]}")
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                pass


if __name__ == "__main__":
    main()
