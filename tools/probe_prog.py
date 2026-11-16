#!/usr/bin/env python3
"""종목별 프로그램 매매 소스 찾기(액션 1회용)."""
import re, requests
H = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148", "Referer": "https://m.stock.naver.com/"}
D = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36", "Referer": "https://finance.daum.net/"}
def show(u, h):
    try:
        r = requests.get(u, headers=h, timeout=20)
        t = r.text if r.status_code == 200 and not r.text.lstrip().startswith("<") else ""
        print(f"=== {u} [{r.status_code}] {t[:600]}")
    except Exception as e:
        print("ERR", u, e)
M = "https://m.stock.naver.com/api/"
for u in [M+"stock/005930/programTrend", M+"stock/005930/program/trend", M+"stock/005930/programTrade", M+"stock/005930/programs",
          M+"stocks/program/KOSPI?page=1&pageSize=5", M+"stocks/programBuy/KOSPI?page=1&pageSize=5", M+"stocks/programNetBuy/KOSPI?page=1&pageSize=5",
          M+"stock/005930/trend?category=program", "https://m.stock.naver.com/front-api/stock/005930/program"]:
    show(u, H)
B = "https://finance.daum.net/api/"
for u in [B+"quotes/A005930/programs?perPage=5", B+"quotes/A005930/program?perPage=5", B+"program/A005930/days?perPage=5", B+"programs/A005930/days?perPage=5",
          B+"quotes/A005930/days?perPage=2", B+"investor/A005930/days?perPage=2&details=true", B+"quote/A005930/investors/days?perPage=2",
          B+"trend/program_trading_ranks?market=KOSPI", B+"program/ranks?market=KOSPI&perPage=5"]:
    show(u, D)
# 페이지 자바스크립트에서 program 관련 API 경로 찾기
for page, h, base in [("https://m.stock.naver.com/domestic/stock/005930/total", H, "https://m.stock.naver.com"),
                      ("https://finance.daum.net/quotes/A005930", D, "https://finance.daum.net")]:
    try:
        html = requests.get(page, headers=h, timeout=20).text
        srcs = re.findall(r'<script[^>]+src="([^"]+\.js[^"]*)"', html)
        found = set()
        for s in srcs[:40]:
            u = s if s.startswith("http") else base + s
            try:
                js = requests.get(u, headers=h, timeout=20).text
                for m in re.findall(r'["\'`](/?(?:api|front-api)[^"\'`]{0,120}?[Pp]rogram[^"\'`]{0,80})["\'`]', js):
                    found.add(m)
                for m in re.findall(r'["\'`]([^"\'`]{0,80}[Pp]rogram[A-Za-z]*(?:Trend|Trade|Rank|Deal)[^"\'`]{0,60})["\'`]', js):
                    found.add(m)
            except Exception:
                pass
        print(f"### {page} scripts={len(srcs)} program paths:", sorted(found)[:60])
    except Exception as e:
        print("ERR page", page, e)
