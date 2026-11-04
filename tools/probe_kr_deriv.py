#!/usr/bin/env python3
"""국내 파생 투자자별 매매 데이터 소스 확인용(액션에서 1회 실행) — 응답 앞부분만 찍는다."""
import requests, json
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36"}
def show(tag, r):
    t = r.text if hasattr(r, "text") else str(r)
    print(f"=== {tag} [{getattr(r,'status_code','?')}] len={len(t)}\n{t[:1500]}\n")
for so in ("01", "03", "04", "05", "06"):
    try:
        r = requests.get("https://finance.naver.com/sise/investorDealTrendDay.naver", params={"bizdate": "20261103", "sosok": so}, headers=UA, timeout=20)
        r.encoding = "euc-kr"; import re
        rows = re.findall(r"<tr>\s*<td class=\"date2\">(.*?)</tr>", r.text, re.S)
        print(f"=== naver sosok={so} [{r.status_code}] rows={len(rows)}")
        for x in rows[:3]: print("   ", re.sub(r"<[^>]+>|\s+", " ", x)[:200])
    except Exception as e: print("naver", so, e)
s = requests.Session(); s.headers.update(UA)
try:
    s.get("https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201050201", timeout=20)
except Exception as e: print("krx home", e)
hdr = {"Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201050201", "X-Requested-With": "XMLHttpRequest"}
for bld, extra in [("dbms/MDC/STAT/standard/MDCSTAT13101", {"prodId": "KRDRVFUK2I", "inqTpCd": "1"}),
                   ("dbms/MDC/STAT/standard/MDCSTAT13102", {"prodId": "KRDRVFUK2I", "inqTpCd": "2"}),
                   ("dbms/MDC/STAT/standard/MDCSTAT13101", {"prodId": "KRDRVOPWKM", "inqTpCd": "1"}),
                   ("dbms/MDC/STAT/standard/MDCSTAT13101", {"prodId": "KRDRVOPWKI", "inqTpCd": "1"}),
                   ("dbms/MDC/STAT/standard/MDCSTAT13101", {"prodId": "KRDRVOPK2I", "inqTpCd": "1"})]:
    try:
        r = s.post("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd", headers=hdr, timeout=20,
                   data={"bld": bld, "strtDd": "20261027", "endDd": "20261103", "juya": "ALL", "prtType": "QTY", "prtCheck": "SU", "rghtTpCd": "", "locale": "ko_KR", **extra})
        show(f"krx {bld} {extra}", r)
    except Exception as e: print("krx", bld, e)
for u in ["https://finance.daum.net/api/trend/futures_investors?perPage=5",
          "https://finance.daum.net/api/investor/futures?perPage=5",
          "https://m.stock.naver.com/api/index/FUT/trend",
          "https://api.stock.naver.com/futures/KOSPI200/investorTrend"]:
    try:
        r = requests.get(u, headers={**UA, "Referer": "https://finance.daum.net/"}, timeout=20); show(u, r)
    except Exception as e: print(u, e)
