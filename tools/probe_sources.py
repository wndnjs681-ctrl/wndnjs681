# 일정 출처 점검(일회성): 야후 경제 캘린더 원문 구조
import re, requests
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"}
for p in [{"from": "2026-11-30", "to": "2026-12-06", "day": "2026-12-04"}, {"day": "2026-12-04"}, {"from": "2026-12-04", "to": "2026-12-04", "day": "2026-12-04", "offset": 0, "size": 100},
          {"from": "2026-12-07", "to": "2026-12-13", "day": "2026-12-10"}]:
    r = requests.get("https://finance.yahoo.com/calendar/economic", headers=H, params=p, timeout=15)
    t = r.text
    print("==", p, r.status_code, len(t))
    for kw in ("Non-Farm Payrolls", "PPI", "CPI"):
        for m in list(re.finditer(re.escape(kw), t))[:2]:
            print(kw, "@", m.start(), repr(t[max(0, m.start() - 160): m.start() + 260]))
    for m in re.finditer(r'"econ_release"|data-testid-cell="econ_release"', t):
        print("cell", repr(t[m.start(): m.start() + 300])); break
    print("rows tr:", len(re.findall(r"<tr", t)))
