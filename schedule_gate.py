#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
screener-full 예약 실행 문지기 — GitHub 예약(cron)이 몇 시간씩 늦거나 빠져도 장 마감 종가를 놓치지 않게 한다.

  python schedule_gate.py wait    미국 장 마감(16:10 ET) 전이면 그때까지 잔다(한 번에 최대 5시간 50분 —
                                  잡 제한 6시간이라 워크플로에서 wait 잡을 세 번 이어 붙인다)
  python schedule_gate.py check   지금 수집해야 할 시장을 GITHUB_OUTPUT 의 markets= 로 낸다(없으면 빈 값)
                                  - 미국: SPY 일봉의 마지막 '마감된' 거래일 > output/universe_us.json 의 date,
                                          또는 직전 실행에서 마지막 봉이 빠진 종목이 절반 넘음(late_ratio)
                                  - 국내: KOSPI(^KS11) 마지막 마감 거래일 > output/universe_kr.json 의 date
                                  휴장일은 지수 일봉에 그날이 없으니 저절로 건너뛴다. 지수 조회가 실패하면
                                  평일 기준 예상 거래일로 판단한다.
표준 라이브러리만 쓴다(의존성 설치 전에도 돈다).
"""
import json, os, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")
US_READY = (16, 10)          # 이 시각(ET) 이후면 당일 봉을 마감 종가로 본다
KR_READY = (15, 40)          # KST
MAX_SLEEP = 5 * 3600 + 50 * 60
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def _ready_today(now, hm):
    return now.weekday() < 5 and (now.hour, now.minute) >= hm


def _weekday_expected(now, hm):
    d = now.date() if _ready_today(now, hm) else now.date() - timedelta(days=1)
    if not _ready_today(now, hm):
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d


def last_session(symbol, tz, hm):
    """지수 일봉에서 마지막으로 '마감된' 거래일. 실패하면 평일 기준 예상일."""
    now = datetime.now(tz)
    try:
        req = urllib.request.Request(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10d&interval=1d",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36"})
        js = json.load(urllib.request.urlopen(req, timeout=15))
        ts = js["chart"]["result"][0]["timestamp"]
        days = sorted({datetime.fromtimestamp(t, timezone.utc).astimezone(tz).date() for t in ts})
        if days and days[-1] == now.date() and not _ready_today(now, hm):
            days = days[:-1]                         # 오늘 봉은 아직 장중
        if days:
            return days[-1], "지수"
    except Exception as e:
        print(f"  {symbol} 조회 실패: {type(e).__name__} — 평일 기준으로 판단", flush=True)
    return _weekday_expected(now, hm), "평일기준"


def _have(mk):
    try:
        d = json.load(open(os.path.join(OUT, f"universe_{mk}.json"), encoding="utf-8"))
        return d.get("date"), d.get("late_ratio")
    except Exception:
        return None, None


def check():
    mks = []
    for mk, sym, tz, hm in (("kr", "^KS11", KST, KR_READY), ("us", "SPY", ET, US_READY)):
        want, how = last_session(sym, tz, hm)
        have, late = _have(mk)
        need = not have or have < want.isoformat() or (mk == "us" and (late or 0) > 0.5)
        print(f"[{mk}] 마지막 마감 거래일 {want}({how}) · 보유 {have}"
              + (f" · 늦은 종목 {late:.0%}" if late is not None else "") + (" → 수집" if need else " → 최신"), flush=True)
        if need:
            mks.append(mk)
    val = ",".join(mks)
    gh = os.getenv("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write(f"markets={val}\n")
    print(f"markets={val}")


def wait():
    now = datetime.now(ET)
    if now.weekday() >= 5 or (now.hour, now.minute) >= US_READY:
        print(f"대기 없음 (지금 {now:%a %H:%M} ET)")
        return
    target = now.replace(hour=US_READY[0], minute=US_READY[1], second=0, microsecond=0)
    sec = (target - now).total_seconds()
    nap = min(sec, MAX_SLEEP)
    print(f"미국 장 마감 대기: 지금 {now:%H:%M} ET → {target:%H:%M} ET 까지 {sec / 60:.0f}분 중 {nap / 60:.0f}분 잔다", flush=True)
    time.sleep(max(0, nap))


if __name__ == "__main__":
    {"wait": wait, "check": check}[sys.argv[1] if len(sys.argv) > 1 else "check"]()
