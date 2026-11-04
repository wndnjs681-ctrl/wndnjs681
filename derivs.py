#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
글로벌 파생상품 포지션 — 투자자들이 시장 방향에 어떻게 베팅하고 있나

1) CFTC 금융선물 투자자별 미결제(TFF, 매주 화요일 기준·금요일 발표)
   자산운용사(연기금·뮤추얼펀드 = 실수요 롱) / 레버리지 펀드(헤지펀드·CTA = 투기) / 딜러(은행 = 반대편)
   순포지션과 3년 백분위(0=3년 중 가장 숏, 100=가장 롱), 4주 변화.
2) 변동성 곡선(야후): VIX9D·VIX·VIX3M·VIX6M — 단기가 장기보다 높으면(역전) 당장의 공포, VVIX·SKEW(꼬리 위험 헤지 수요)
3) CBOE 풋/콜 비율(전체·주식·지수) — 받을 수 있을 때만

결과는 output/market_index.json 의 "deriv" 키에 덧붙인다(대시보드 동기화가 이미 이 파일을 옮김). 따로 output/derivs.json 도 남긴다.
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone

import requests

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36"

# (코드, 이름, 묶음) — CFTC 계약 코드
TFF = [
    ("13874A", "S&P500 E-mini", "주가지수"),
    ("209742", "나스닥100 E-mini", "주가지수"),
    ("239742", "러셀2000 E-mini", "주가지수"),
    ("124603", "다우 E-mini", "주가지수"),
    ("240743", "니케이225(엔화)", "주가지수"),
    ("244042", "MSCI 신흥국", "주가지수"),
    ("244041", "MSCI EAFE(선진국)", "주가지수"),
    ("1170E1", "VIX 선물", "변동성"),
    ("043602", "미 10년물", "금리"),
    ("020601", "미 30년물(본드)", "금리"),
    ("097741", "엔화", "통화"),
    ("099741", "유로", "통화"),
    ("133741", "비트코인", "기타"),
]


def tff():
    since = (datetime.now() - timedelta(days=3 * 365 + 30)).strftime("%Y-%m-%d")
    codes = ",".join(f"'{c}'" for c, _, _ in TFF)
    sel = ("cftc_contract_market_code,market_and_exchange_names,report_date_as_yyyy_mm_dd,open_interest_all,"
           "dealer_positions_long_all,dealer_positions_short_all,asset_mgr_positions_long,asset_mgr_positions_short,"
           "lev_money_positions_long,lev_money_positions_short")
    for k in range(3):
        try:
            r = requests.get("https://publicreporting.cftc.gov/resource/gpe5-46if.json",
                             params={"$where": f"cftc_contract_market_code in({codes}) AND report_date_as_yyyy_mm_dd>'{since}'",
                                     "$select": sel, "$limit": 50000, "$order": "report_date_as_yyyy_mm_dd"},
                             headers={"User-Agent": UA}, timeout=60)
            rows = r.json()
            if isinstance(rows, list) and rows:
                break
            print("  CFTC 응답:", str(rows)[:200])
        except Exception as e:
            print(f"  CFTC {type(e).__name__} {e}"[:200])
        time.sleep(3 + 3 * k)
    else:
        return None
    by = {}
    for x in rows:
        by.setdefault(x["cftc_contract_market_code"], []).append(x)
    out = []
    n = lambda x, a, b: int(float(x.get(a) or 0)) - int(float(x.get(b) or 0))
    for code, name, grp in TFF:
        xs = sorted(by.get(code, []), key=lambda x: x["report_date_as_yyyy_mm_dd"])
        if len(xs) < 20:
            print(f"  [{name}] 데이터 부족 {len(xs)}")
            continue
        am = [n(x, "asset_mgr_positions_long", "asset_mgr_positions_short") for x in xs]
        lf = [n(x, "lev_money_positions_long", "lev_money_positions_short") for x in xs]
        dl = [n(x, "dealer_positions_long_all", "dealer_positions_short_all") for x in xs]
        oi = int(float(xs[-1].get("open_interest_all") or 0))
        pct = lambda s: round(sum(1 for v in s if v <= s[-1]) / len(s) * 100)
        ch = lambda s, k: s[-1] - s[-1 - k] if len(s) > k else None
        rec = {"code": code, "name": name, "grp": grp, "date": xs[-1]["report_date_as_yyyy_mm_dd"][:10], "oi": oi,
               "am": am[-1], "am_p": pct(am), "am_1w": ch(am, 1), "am_4w": ch(am, 4),
               "lf": lf[-1], "lf_p": pct(lf), "lf_1w": ch(lf, 1), "lf_4w": ch(lf, 4),
               "dl": dl[-1], "dl_p": pct(dl),
               "hist": {"d": [x["report_date_as_yyyy_mm_dd"][:10] for x in xs[-104:]], "am": am[-104:], "lf": lf[-104:]}}
        out.append(rec)
        print(f"[{name}] {rec['date']} 자산운용 {am[-1]:+,}({rec['am_p']}%ile, 4주 {rec['am_4w']:+,}) · "
              f"레버리지 {lf[-1]:+,}({rec['lf_p']}%ile, 4주 {rec['lf_4w']:+,}) · 딜러 {dl[-1]:+,} · OI {oi:,}")
    return out


def yahoo(sym, rng="1y"):
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}",
                             params={"range": rng, "interval": "1d"}, headers={"User-Agent": UA}, timeout=30)
            res = r.json()["chart"]["result"][0]
            ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
            pts = [(datetime.fromtimestamp(t, timezone(timedelta(hours=-5))).strftime("%Y-%m-%d"), c) for t, c in zip(ts, cl) if c]
            if pts:
                return pts
        except Exception as e:
            print(f"  {sym} {host}: {type(e).__name__}"[:120])
        time.sleep(1)
    return None


def vol():
    syms = {"VIX9D": "^VIX9D", "VIX": "^VIX", "VIX3M": "^VIX3M", "VIX6M": "^VIX6M", "VVIX": "^VVIX", "SKEW": "^SKEW",
            "VXN": "^VXN", "MOVE": "^MOVE"}
    got = {k: yahoo(s) for k, s in syms.items()}
    out = {}
    for k, pts in got.items():
        if not pts:
            continue
        vs = [c for _, c in pts]
        out[k] = {"date": pts[-1][0], "v": round(vs[-1], 2), "p1y": round(sum(1 for v in vs if v <= vs[-1]) / len(vs) * 100),
                  "chg5": round(vs[-1] - vs[-6], 2) if len(vs) > 5 else None, "s": [round(v, 2) for v in vs[-120:]]}
    if "VIX" in out and "VIX3M" in out:                     # 9D/3M 비율 이력(1 넘으면 역전)
        a, b = dict(got["VIX"]), dict(got["VIX3M"])
        rs = [a[d] / b[d] for d in a if d in b and b[d]]
        out["ratio"] = {"vix_3m": round(out["VIX"]["v"] / out["VIX3M"]["v"], 3),
                        "p1y": round(sum(1 for v in rs if v <= rs[-1]) / len(rs) * 100) if rs else None}
        if "VIX9D" in out:
            out["ratio"]["vix9d_vix"] = round(out["VIX9D"]["v"] / out["VIX"]["v"], 3)
    print("변동성:", {k: (v.get("v"), v.get("p1y")) if isinstance(v, dict) and "v" in v else v for k, v in out.items()})
    return out


def putcall():
    """CBOE 일일 옵션 통계(JSON). 최근 거래일부터 거꾸로 7일까지 찾아 최대 20거래일 모은다."""
    rows = []
    d = datetime.now(timezone(timedelta(hours=-5))).date()
    for _ in range(40):
        if d.weekday() < 5:
            try:
                r = requests.get(f"https://cdn.cboe.com/data/us/options/market_statistics/daily/{d:%Y-%m-%d}_daily_options",
                                 headers={"User-Agent": UA}, timeout=20)
                if r.ok:
                    j = r.json()
                    ratios = {x["name"]: x["value"] for x in j.get("ratios", [])}
                    rows.append({"date": f"{d:%Y-%m-%d}", **{k: float(v) for k, v in ratios.items() if v not in (None, "")}})
            except Exception as e:
                if not rows:
                    print(f"  CBOE {d}: {type(e).__name__} {e}"[:150])
        if len(rows) >= 20:
            break
        d -= timedelta(days=1)
    if not rows:
        print("  CBOE 풋/콜: 받지 못함")
        return None
    rows.sort(key=lambda x: x["date"])
    print("풋/콜:", rows[-1])
    return rows


def kr_flow(old):
    """네이버 — 코스피200 선물·코스피 현물 투자자별 순매수(억원). 하루치만 주므로 날마다 쌓는다(장 마감 15:45 이후 값만 확정으로)."""
    hist = {x["d"]: x for x in (old or [])}
    now = datetime.now(KST)
    num = lambda v: int(str(v).replace(",", "").replace("+", "") or 0)
    row = {}
    for code, key in (("FUT", "fut"), ("KOSPI", "spot")):
        try:
            j = requests.get(f"https://m.stock.naver.com/api/index/{code}/trend",
                             headers={"User-Agent": UA, "Referer": "https://m.stock.naver.com/"}, timeout=20).json()
            d = j["bizdate"]
            row["d"] = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            row[key] = {"frn": num(j["foreignValue"]), "ind": num(j["personalValue"]), "ins": num(j["institutionalValue"])}
        except Exception as e:
            print(f"  네이버 {code}: {type(e).__name__} {e}"[:150])
    if row.get("d") and (row["d"] < now.strftime("%Y-%m-%d") or (now.hour, now.minute) >= (15, 45)):
        hist[row["d"]] = row
        print("국내 수급:", row)
    elif row:
        print("국내 수급: 장중 값이라 저장 안 함", row)
    return [hist[k] for k in sorted(hist)][-250:] or None


def main():
    os.makedirs(OUT, exist_ok=True)
    try:
        old_kr = (json.load(open(os.path.join(OUT, "market_index.json"), encoding="utf-8")).get("deriv") or {}).get("kr")
    except Exception:
        old_kr = None
    res = {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "cot": tff(), "vol": vol(), "pc": putcall(), "kr": kr_flow(old_kr)}
    json.dump(res, open(os.path.join(OUT, "derivs.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    p = os.path.join(OUT, "market_index.json")
    try:
        mi = json.load(open(p, encoding="utf-8"))
        old = mi.get("deriv") or {}
        for k in ("cot", "vol", "pc", "kr"):                     # 이번에 못 받은 항목은 지난 값 유지
            if not res.get(k) and old.get(k):
                res[k] = old[k]
        mi["deriv"] = res
        json.dump(mi, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        print(f"→ {p} 에 deriv 추가")
    except Exception as e:
        print(f"market_index.json 갱신 실패: {e}")


if __name__ == "__main__":
    main()
