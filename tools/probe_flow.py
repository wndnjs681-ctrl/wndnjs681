#!/usr/bin/env python3
"""수급 데이터 소스 확인(액션 1회용) — 응답 앞부분만 찍는다."""
import requests
H = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148", "Referer": "https://m.stock.naver.com/"}
M = "https://m.stock.naver.com/api/"
U = [M+"stock/005930/trend?pageSize=10", M+"stock/005930/trend", M+"stock/005930/investorTrend", M+"stock/005930/integration",
     M+"index/KOSDAQ/trend", M+"index/KOSPI/integration", M+"index/KOSDAQ/integration", M+"index/KOSPI/program", M+"index/KOSPI/programTrend",
     M+"stocks/foreignBuy/KOSPI?page=1&pageSize=5", M+"stocks/marketValue/KOSPI?page=1&pageSize=3", M+"stocks/up/KOSPI?page=1&pageSize=3",
     M+"stocks/investor/foreign/KOSPI?page=1&pageSize=5", M+"stocks/dealRank/KOSPI?investor=foreign",
     "https://m.stock.naver.com/front-api/stock/005930/trend", "https://m.stock.naver.com/front-api/investor/trend?market=KOSPI",
     "https://finance.naver.com/sise/sise_deal_rank.naver", "https://finance.naver.com/item/frgn.naver?code=005930",
     "https://finance.naver.com/sise/programDealTrendDay.naver", "https://finance.naver.com/sise/investorDealTrendDay.naver?sosok=01"]
for u in U:
    try:
        r = requests.get(u, headers=H, timeout=20); t = r.text
        if "finance.naver.com" in u:
            r.encoding = "euc-kr"; t = r.text
            import re; i = t.find("외국인"); t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t[max(0, i-200): i+1200])) if i >= 0 else t[:200]
        print(f"=== {u} [{r.status_code}] len={len(r.text)}\n{t[:900]}\n")
    except Exception as e:
        print("ERR", u, e)
D = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.daum.net/"}
for u in ["https://finance.daum.net/api/investor/days?page=1&perPage=5&market=KOSPI", "https://finance.daum.net/api/trend/market_investors?market=KOSPI",
          "https://finance.daum.net/api/investor/KOSPI/days?perPage=5", "https://finance.daum.net/api/quote/A005930/investors?perPage=5",
          "https://finance.daum.net/api/investor/A005930/days?perPage=5&page=1", "https://finance.daum.net/api/trend/foreign_investors?market=KOSPI&perPage=5"]:
    try:
        r = requests.get(u, headers=D, timeout=20); print(f"=== {u} [{r.status_code}]\n{r.text[:700]}\n")
    except Exception as e:
        print("ERR", u, e)
