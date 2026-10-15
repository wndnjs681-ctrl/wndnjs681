#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한투 API 키 점검 — 저장소 Secrets(또는 .env)의 키로 토큰·실시간 접속키를 받고 시세를 한 번 조회한다.
키 값 자체는 출력하지 않는다. 실패하면 이유를 한국어로 보여주고 종료 코드 1.
"""
import os, sys, traceback

from kis import KIS, KISError


def main():
    key = os.getenv("KIS_APP_KEY", "")
    print(f"환경: {os.getenv('KIS_ENV') or 'real'} · 앱키 길이 {len(key)}자 · 앱시크릿 길이 {len(os.getenv('KIS_APP_SECRET', ''))}자")
    try:
        k = KIS(token_cache=False)
        k.token()
        print("✔ 접근 토큰 발급 성공")
        k.approval_key()
        print("✔ 실시간 접속키 발급 성공")
        p = k.price_kr("005930")
        print(f"✔ 국내 현재가: 삼성전자 {p['price']:,.0f}원 ({p['chg_pct']:+.2f}%)")
        ex = k.find_us_exchange("AAPL")
        if ex:
            u = k.price_us("AAPL", ex)
            print(f"✔ 미국 현재가: AAPL ({ex}) ${u['price']:,.2f} ({u['chg_pct']:+.2f}%)")
        else:
            print("△ 미국 현재가를 받지 못했습니다 — 해외주식 시세 권한이 없는 계정일 수 있습니다.")
        print("모든 점검을 통과했습니다.")
    except KISError as e:
        print(f"✘ {e}")
        print("앱키·앱시크릿이 정확한지, KIS_ENV(실전 real / 모의 paper)가 발급받은 쪽과 같은지 확인하세요.")
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__" and "--live" not in sys.argv:
    main()


# ── 실시간·분봉 점검 (python kis_check.py --live) ──────────────
def live_check(seconds=20):
    import asyncio
    from kis import TR_KR, TR_US, stream
    k = KIS(token_cache=False)
    rows = k.minutes_kr("005930")
    print(f"✔ 국내 당일 1분봉: {len(rows)}개" + (f" ({rows[0]['hm']} ~ {rows[-1]['hm']}, 마지막 종가 {rows[-1]['c']:,.0f})" if rows else ""))
    ak = k.approval_key()
    got = {"n": 0}

    def on_tick(t, f):
        got["n"] += 1
        if got["n"] <= 3:
            print(f"  틱 필드 {len(f)}개 · {t}")

    def on_status(s):
        print(f"  구독 응답: {s}")

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(stream(k.ws_url, ak, [(TR_KR, "005930"), (TR_KR, "000660"), (TR_US, "DNASAAPL")], on_tick, on_status, stop))
        await asyncio.sleep(seconds)
        stop.set(); task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(run())
    print(f"{'✔' if got['n'] else '△'} 실시간 체결 {got['n']}건 수신 ({seconds}초)")


if __name__ == "__main__" and "--live" in sys.argv:
    live_check()
