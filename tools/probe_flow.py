#!/usr/bin/env python3
"""프로그램 매매·기관 세부(사모·연기금) 소스 확인(액션 1회용)."""
import requests
D = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36", "Referer": "https://finance.daum.net/"}
B = "https://finance.daum.net/api/"
U = [B+"investor/KOSPI/days?perPage=2&details=true", B+"investor/KOSPI/days?perPage=2&detail=true", B+"investor/KOSPI/days?perPage=2&isDetail=true",
     B+"investor/KOSPI/days?perPage=2&pagination=true&details=Y", B+"investor/KOSPI/details/days?perPage=2", B+"investor/KOSPI/days/details?perPage=2",
     B+"investor/KOSPI/times?perPage=2", B+"investor/KOSPI/institutions/days?perPage=2", B+"investor/institution/KOSPI/days?perPage=2",
     B+"program/KOSPI/days?perPage=2", B+"program/days?market=KOSPI&perPage=2", B+"programs/KOSPI/days?perPage=2", B+"program_trading/KOSPI/days?perPage=2",
     B+"program/KOSPI/times?perPage=2", B+"market_index/days?code=KOSPI&perPage=2", B+"investor/KOSPI?perPage=2",
     B+"investor/KOSPI/days?perPage=2&investorType=pension"]
for u in U:
    try:
        r = requests.get(u, headers=D, timeout=20); print(f"=== {u} [{r.status_code}]\n{r.text[:1100]}\n")
    except Exception as e:
        print("ERR", u, e)
H = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", "Referer": "https://m.stock.naver.com/"}
for u in ["https://m.stock.naver.com/api/index/KOSPI/integration", "https://m.stock.naver.com/api/index/KOSPI/program?pageSize=5",
          "https://m.stock.naver.com/api/index/KOSPI/trend?category=detail", "https://m.stock.naver.com/api/index/KOSPI/investor",
          "https://m.stock.naver.com/api/index/KOSPI/dealTrend", "https://m.stock.naver.com/api/index/KOSPI/programTrend?pageSize=5",
          "https://m.stock.naver.com/api/stock/005930/program", "https://m.stock.naver.com/api/stock/005930/trend/institution"]:
    try:
        r = requests.get(u, headers=H, timeout=20); t = r.text
        if "integration" in u:
            import json; j = r.json(); t = json.dumps({k: j.get(k) for k in ("dealTrendInfo", "programTrendInfo", "upDownStockInfo")}, ensure_ascii=False)
        print(f"=== {u} [{r.status_code}]\n{t[:700] if r.status_code==200 else ''}\n")
    except Exception as e:
        print("ERR", u, e)
