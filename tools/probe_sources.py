# 일정 출처 점검(일회성): 고용보고서·PPI 일정을 어디서 받을 수 있는지
import re, requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}
U = [
 "https://finance.yahoo.com/calendar/economic?day=2026-12-04",
 "https://finance.yahoo.com/calendar/economic?from=2026-11-30&to=2026-12-06&day=2026-12-04",
 "https://tradingeconomics.com/united-states/non-farm-payrolls",
 "https://tradingeconomics.com/united-states/producer-prices-change",
 "https://tradingeconomics.com/united-states/calendar",
 "https://us.econoday.com/byweek.asp?cust=us",
 "https://www.econoday.com/",
 "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
 "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
 "https://www.investing.com/economic-calendar/nonfarm-payrolls-227",
 "https://www.dol.gov/newsroom/releases",
 "https://www.bls.gov/schedule/news_release/empsit.htm",
 "https://www.bls.gov/bls/archived_sched.htm",
 "https://api.bls.gov/publicAPI/v2/timeseries/data/CES0000000001",
 "https://www.marketwatch.com/economy-politics/calendar",
 "https://www.cnbc.com/economic-calendar/",
 "https://www.fxstreet.com/economic-calendar",
 "https://api.nasdaq.com/api/calendar/economicevents?date=2026-12-04",
 "https://www.federalreserve.gov/newsevents/calendar.htm",
 "https://www.whitehouse.gov/",
 "https://www.census.gov/economic-indicators/calendar-listview.html",
 "https://www.bea.gov/news/schedule",
]
for u in U:
    try:
        r = requests.get(u, headers=H, timeout=12)
        t = r.text
        hits = re.findall(r".{0,80}(?:Non-?\s?Farm Payrolls|Nonfarm Payrolls|Employment Situation|Producer Price|\bPPI\b).{0,80}", t, re.I)
        print(f"{r.status_code} {len(t):>8} hits={len(hits):>3} {u}")
        for h in hits[:4]:
            print("     ", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))[:170])
    except Exception as e:
        print("ERR", type(e).__name__, u)
