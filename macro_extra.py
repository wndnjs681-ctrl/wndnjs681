#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
매크로 탭 하위 페이지용 자료 → output/market_index.json 의 "mem"(메모리 가격) · "fw"(FOMC 금리 확률).

메모리 가격: DRAMeXchange 첫 화면의 현물 시세 표(DRAM 칩·모듈·그래픽 D램·낸드·웨이퍼).
  그날 평균가(Session Average)를 날짜별로 output/mem_hist.json 에 쌓는다(과거 값은 공개 자료가 없어 수집 시작일부터).
FOMC 금리 확률: CME FedWatch 와 같은 방식 — 30일 연방기금금리 선물(ZQ) 월물 가격으로 회의 달의 평균 금리를 풀어
  회의 직후 금리를 구하고, 지금 목표 범위에서 0.25%p 단위로 확률을 나눈다. 1일·1주·1개월 전 값은 그날 선물 종가로 다시 계산.
  선물: 야후(ZQ{월}{연}.CBT) → 실효 연방기금금리(EFFR): 뉴욕 연준 API → FOMC 일정: 연준 누리집(calendar_events.fomc_dates).
"""
import json, os, re, time
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

import requests

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
LOG = {}


# ───────────────────────── 메모리 가격 ─────────────────────────
class Tables(HTMLParser):
    """모든 <table> 을 [ [셀 텍스트…] 행… ] 으로. 표 바로 앞 제목 텍스트도 함께."""
    def __init__(self):
        super().__init__()
        self.tables, self.stack, self.cell, self.row, self.last_text = [], [], None, None, ""

    def handle_starttag(self, tag, a):
        if tag == "table":
            self.stack.append({"title": self.last_text.strip()[:80], "rows": []})
        elif tag == "tr" and self.stack:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = ""

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None and self.stack:
            if self.row:
                self.stack[-1]["rows"].append(self.row)
            self.row = None
        elif tag == "table" and self.stack:
            self.tables.append(self.stack.pop())

    def handle_data(self, d):
        if self.cell is not None:
            self.cell += d
        elif d.strip():
            self.last_text = d


def fnum(s):
    s = str(s).replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def dram():
    r = requests.get("https://www.dramexchange.com/", headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=30)
    print(f"DRAMeXchange: HTTP {r.status_code} · {len(r.text):,}자", flush=True)
    r.raise_for_status()
    return parse_dram(r.text, log=True)


def upd_day(upd, default):
    """'Last Update: Nov. 24 2026' 같은 표기 → YYYY-MM-DD"""
    if upd:
        u = re.sub(r"\s+", " ", upd.replace(".", " ")).strip()
        for fmt in ("%b %d %Y", "%B %d %Y", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(u, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return default


def parse_dram(text, log=False):
    p = Tables()
    p.feed(text)
    upd = re.search(r"Last Update\s*:?\s*([A-Za-z]{3}\.?\s*\d{1,2}\s*\d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})", text)
    items = []
    for t in p.tables:
        rows = t["rows"]
        if len(rows) < 2:
            continue
        hdr = [h.lower() for h in rows[0]]
        ia = next((i for i, h in enumerate(hdr) if "session average" in h), None)
        if ia is None:
            ia = next((i for i, h in enumerate(hdr) if "average" in h), None)
        ic = next((i for i, h in enumerate(hdr) if "change" in h), None)
        if ia is None or not hdr or "item" not in hdr[0]:
            continue
        for row in rows[1:]:
            if len(row) <= ia:
                continue
            v = fnum(row[ia])
            if v is None or v <= 0:
                continue
            items.append({"item": row[0], "avg": v, "chg": fnum(row[ic]) if ic is not None and ic < len(row) else None,
                          "hdr": rows[0][ia], "tbl": t["title"]})
    if log:
        print("  표:", [(t["title"], len(t["rows"]), t["rows"][0][:6] if t["rows"] else []) for t in p.tables][:14], flush=True)
        print("  항목:", [(x["item"], x["avg"], x["chg"]) for x in items][:40], flush=True)
    return items, (upd.group(1) if upd else None)


# 화면에 보여 줄 항목(이름 규칙 → 표시 이름 · 묶음 · 갱신 주기)
MEM_PICK = [
    (r"^DDR5 16Gb", "DDR5 16Gb", "dram", "일별"), (r"^DDR4 16Gb", "DDR4 16Gb", "dram", "일별"), (r"^DDR4 8Gb", "DDR4 8Gb", "dram", "일별"),
    (r"DDR5 UDIMM 16GB", "DDR5 UDIMM 16GB", "module", "주별"), (r"DDR5 RDIMM 32GB", "DDR5 RDIMM 32GB", "module", "주별"),
    (r"DDR4 UDIMM 16GB", "DDR4 UDIMM 16GB", "module", "주별"),
    (r"^GDDR5 8Gb", "GDDR5 8Gb", "gddr", "주별"), (r"^GDDR6 8Gb", "GDDR6 8Gb", "gddr", "주별"),
    (r"^MLC 64Gb", "MLC 64Gb", "nand", "일별"), (r"^MLC 32Gb", "MLC 32Gb", "nand", "일별"),
    (r"512Gb TLC", "512Gb TLC", "wafer", "주별"), (r"256Gb TLC", "256Gb TLC", "wafer", "주별"),
]


def mem():
    items, upd = dram()
    day = upd_day(upd, datetime.now(KST).strftime("%Y-%m-%d"))
    hp = os.path.join(OUT, "mem_hist.json")
    try:
        H = json.load(open(hp, encoding="utf-8"))
    except Exception:
        H = {}
    out = []
    for rx, nm, grp, cyc in MEM_PICK:
        x = next((i for i in items if re.search(rx, i["item"], re.I)), None)
        if not x:
            continue
        h = H.setdefault(nm, {})
        h[day] = x["avg"]
        ds = sorted(h)
        out.append({"k": nm, "full": x["item"], "g": grp, "cyc": cyc, "px": x["avg"], "chg": x["chg"], "d": day,
                    "hist": [[d, h[d]] for d in ds[-400:]]})
    # 고르지 못한 나머지도 참고용으로(이름이 바뀌면 화면에서 알 수 있게)
    others = [{"item": i["item"], "px": i["avg"], "chg": i["chg"]} for i in items if not any(re.search(rx, i["item"], re.I) for rx, *_ in MEM_PICK)][:40]
    json.dump(H, open(hp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    LOG["mem"] = f"{len(out)}/{len(MEM_PICK)}항목 · {day}"
    return {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "day": day, "src": "DRAMeXchange", "items": out, "others": others}


# ───────────────────────── FOMC 금리 확률 ─────────────────────────
MCODE = "FGHJKMNQUVXZ"


def yahoo_hist(sym):
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": "3mo", "interval": "1d"},
                             headers={"User-Agent": UA}, timeout=20)
            res = r.json()["chart"]["result"][0]
            cl = res["indicators"]["quote"][0]["close"]
            # 선물 일봉의 시각은 전날 저녁 장 시작(미 중부 17시)으로 찍히기도 한다 → UTC+12시간으로 옮겨 거래일 날짜로
            m = {datetime.fromtimestamp(t + 12 * 3600, timezone.utc).strftime("%Y-%m-%d"): c for t, c in zip(res["timestamp"], cl) if c}
            m = {d: c for d, c in m.items() if date.fromisoformat(d).weekday() < 5}
            meta = res.get("meta") or {}
            if meta.get("regularMarketPrice") and meta.get("regularMarketTime"):   # 일봉에 아직 없는 날만 마지막 체결가로(전날 종가를 덮지 않게)
                md = datetime.fromtimestamp(meta["regularMarketTime"], timezone(timedelta(hours=-5))).strftime("%Y-%m-%d")
                if m and md > max(m):
                    m[md] = meta["regularMarketPrice"]
            if m:
                return m
        except Exception as e:
            err = f"{type(e).__name__}"
        time.sleep(0.3)
    return {}


def effr():
    try:
        j = requests.get("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/40.json", headers={"User-Agent": UA}, timeout=20).json()
        rows = j.get("refRates") or []
        m = {x["effectiveDate"]: (x["percentRate"], x.get("targetRateFrom"), x.get("targetRateTo")) for x in rows}
        print("EFFR(뉴욕 연준):", sorted(m.items())[-2:], flush=True)
        return m
    except Exception as e:
        print("EFFR 실패:", type(e).__name__, str(e)[:120], flush=True)
        return {}


def fedwatch():
    from calendar_events import fomc_dates
    today = datetime.now(KST).date()
    got, src = fomc_dates()
    meets = sorted(d for d in got if d >= today - timedelta(days=40))
    print("FOMC 회의:", [str(d) for d in meets][:10], src, flush=True)
    # 이번 달부터 13개월 월물
    months = []
    y, m = today.year, today.month
    for _ in range(14):
        months.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    px = {}
    for (y, m) in months:
        sym = f"ZQ{MCODE[m - 1]}{y % 100:02d}.CBT"
        h = yahoo_hist(sym)
        if h:
            px[(y, m)] = h
        print(f"  {sym}: {len(h)}일 · 마지막 {sorted(h.items())[-1] if h else '-'}", flush=True)
    if len(px) < 4:
        LOG["fw"] = "선물 시세 부족"
        return None
    E = effr()
    last_e = sorted(E.items())[-1][1] if E else None
    lo_now = float(last_e[1]) if last_e and last_e[1] is not None else None
    hi_now = float(last_e[2]) if last_e and last_e[2] is not None else None
    if lo_now is None:                                          # 목표 범위를 못 받으면 매크로 탭의 기준금리(상단)로
        try:
            mi = json.load(open(os.path.join(OUT, "market_index.json"), encoding="utf-8"))
            up = ((mi.get("macro") or {}).get("s") or {}).get("DFEDTARU", {}).get("v", [None])[-1]
            hi_now, lo_now = float(up), float(up) - 0.25
        except Exception:
            pass
    if lo_now is None:
        LOG["fw"] = "목표 범위 모름"
        return None
    eff_now = float(last_e[0]) if last_e else lo_now + 0.08
    spread = eff_now - lo_now                                   # 실효금리 − 목표 하단(보통 0.08~0.10)

    def on_date(asof):
        """asof(미 동부 날짜 문자열) 종가로 회의별 확률. {회의일: {하단: 확률}}"""
        r = {}
        for ym, h in px.items():
            ks = [k for k in h if k <= asof]
            if ks:
                r[ym] = 100 - h[max(ks)]
        res, S = {}, None
        prev_end = None
        for d in [x for x in meets if x > date.fromisoformat(asof)]:
            ym = (d.year, d.month)
            if ym not in r:                                  # 이미 만기된 월물(과거 날짜로 다시 계산할 때) — 그 회의는 건너뛴다
                prev_end = None
                continue
            N = monthrange(*ym)[1]
            pm = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
            pm_has = any((x.year, x.month) == pm for x in meets)
            if prev_end is None:
                S = r.get(pm) if (pm in r and not pm_has) else eff_now
            else:
                S = r.get(pm) if (pm in r and not pm_has) else prev_end
            nm = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
            nm_has = any((x.year, x.month) == nm for x in meets)
            if N - d.day < 7 and nm in r and not nm_has:       # 월말 회의면 다음 달 월물로
                Ee = r[nm]
            else:
                Ee = (r[ym] * N - S * d.day) / (N - d.day)
            prev_end = Ee
            lo_after = Ee - spread                               # 회의 뒤 기대 목표 하단
            steps = (lo_after - lo_now) / 0.25
            k = int((steps // 1))
            f = steps - k
            dist = {round(lo_now + k * 0.25, 2): round((1 - f) * 100, 1), round(lo_now + (k + 1) * 0.25, 2): round(f * 100, 1)}
            res[d.isoformat()] = {"dist": {f"{a:.2f}": p for a, p in dist.items() if p >= 0.05}, "exp": round(lo_after + 0.125, 3)}
        return res

    us_days = sorted({k for h in px.values() for k in h})
    now = us_days[-1]
    pick = lambda back: max([d for d in us_days if d <= (date.fromisoformat(now) - timedelta(days=back)).isoformat()] or [us_days[0]])
    cols = [("현재", now), ("1일 전", pick(1)), ("1주 전", pick(7)), ("1개월 전", pick(30))]
    tab = {lab: {"d": d, "m": on_date(d)} for lab, d in cols}
    nxt = [d for d in meets if d > date.fromisoformat(now)]
    LOG["fw"] = f"월물 {len(px)} · 회의 {len(nxt)} · 현재 범위 {lo_now:.2f}–{hi_now or lo_now + 0.25:.2f} · EFFR {eff_now}"
    print("FedWatch:", json.dumps(tab["현재"]["m"], ensure_ascii=False)[:600], flush=True)
    return {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "lo": lo_now, "hi": hi_now or lo_now + 0.25, "effr": eff_now,
            "meets": [{"d": d.isoformat(), "sep": got.get(d, False)} for d in nxt], "cols": [[lab, d] for lab, d in cols],
            "tab": tab, "src": "30일 연방기금금리 선물(CBOT ZQ, 야후 지연 시세) · EFFR 뉴욕 연준 · 회의 일정 " + src}


def main():
    p = os.path.join(OUT, "market_index.json")
    mi = json.load(open(p, encoding="utf-8"))
    for key, fn in (("mem", mem), ("fw", fedwatch)):
        try:
            v = fn()
            if v:
                mi[key] = v
        except Exception as e:
            import traceback
            traceback.print_exc()
            LOG[key] = f"실패 {type(e).__name__}: {str(e)[:120]}"
    json.dump(mi, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("요약:", LOG)


if __name__ == "__main__":
    main()
