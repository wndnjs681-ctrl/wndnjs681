# 일정 출처 점검(일회성): Trading Economics 지표 페이지의 발표 일정 표
import re, requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"}
for slug in ("non-farm-payrolls", "producer-prices-change", "inflation-cpi", "core-pce-price-index-mom"):
    r = requests.get(f"https://tradingeconomics.com/united-states/{slug}", headers=H, timeout=15)
    t = r.text
    print("==", slug, r.status_code, len(t))
    i = t.find('id="calendar"')
    print("calendar@", i)
    if i > 0:
        seg = t[i:i + 6000]
        seg = re.sub(r"\s+", " ", re.sub(r"<(?!/?t[rd])[^>]+>", "", seg))
        print(seg[:2500])
    m = re.search(r"TimeZone[^;]{0,120}", t)
    print("tz:", m.group(0) if m else None)
r = requests.get("https://tradingeconomics.com/united-states/calendar", headers=H, timeout=15)
t = r.text
for m in list(re.finditer(r"Non Farm Payrolls|PPI MoM", t))[:3]:
    print("CAL", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t[max(0, m.start() - 1500): m.start() + 200]))[-700:])
