#!/usr/bin/env python3
"""국내 파생 투자자별 매매 데이터 소스 확인용(액션에서 1회 실행) — 응답 앞부분만 찍는다."""
import requests
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148", "Referer": "https://m.stock.naver.com/"}
B = "https://m.stock.naver.com/api/index/"
urls = [B + "FUT/trend?pageSize=20&page=1", B + "FUT/trend/history?pageSize=20", B + "FUT/investorTrend?pageSize=20", B + "FUT/basic", B + "FUT/integration",
        B + "KPI200/trend", B + "CALL/trend", B + "PUT/trend", B + "OPT/trend", B + "KOSPI/trend", B + "KOSPI/investorTrend?pageSize=5",
        "https://m.stock.naver.com/api/futures/FUT/trend", "https://m.stock.naver.com/front-api/index/FUT/trend",
        "https://m.stock.naver.com/api/index/FUT/trend/day?pageSize=20", "https://m.stock.naver.com/api/index/FUT/dealTrend?pageSize=20",
        "https://finance.naver.com/sise/sise_index.naver?code=FUT", "https://finance.naver.com/sise/sise_index_day.naver?code=FUT",
        "https://finance.naver.com/sise/investorDealTrendTime.naver?bizdate=20261103&sosok=03",
        "https://www.kofia.or.kr/", "https://open.krx.co.kr/", "https://openapi.krx.co.kr/"]
for u in urls:
    try:
        r = requests.get(u, headers=UA, timeout=20)
        t = r.text
        if "naver.com/sise" in u:
            r.encoding = "euc-kr"; t = r.text
            import re; i = t.find("외국인"); t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t[max(0, i - 300): i + 1500])) if i >= 0 else t[:300]
        print(f"=== {u} [{r.status_code}] len={len(r.text)}\n{t[:1200]}\n")
    except Exception as e:
        print("ERR", u, e)
