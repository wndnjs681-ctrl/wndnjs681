#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
기업개요 수집 — 스크리너 종목 창의 '기업개요' 칸에 들어갈 문장.

  국내: FnGuide 기업정보 메인의 'Business Summary'(한국어 요약 몇 줄) → 실패 시 네이버 금융 '기업개요'
  미국: Yahoo quoteSummary assetProfile (영문 사업 설명 · 업종 · 직원 수 · 본사 · 홈페이지)

산출물: output/profile_kr.json, output/profile_us.json
    {"asof": "...", "n": 123, "p": {"005930": {"s": "요약", "ind": "업종", "emp": 123, "hq": "...", "web": "...", "src": "FnGuide", "at": "2026-10-01"}}}

개요는 거의 안 바뀌므로 증분으로 돈다: 기존 파일을 읽어 없는 종목과 REFRESH_DAYS 지난 종목만 다시 받는다.
"""
import json, os, re, sys, time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime("%Y-%m-%d")
OUT_DIR = os.getenv("OUT_DIR", "output")
REFRESH_DAYS = int(os.getenv("REFRESH_DAYS", "90"))
MAX_FETCH = int(os.getenv("MAX_FETCH", "3000"))            # 한 번에 새로 받을 최대 종목 수
TIME_BUDGET = float(os.getenv("TIME_BUDGET_MIN", "50")) * 60
MAX_LEN = 900                                              # 개요 최대 글자 수
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
T0 = time.time()


def over():
    return time.time() - T0 > TIME_BUDGET


def clip(s):
    s = re.sub(r"\s+", " ", s or "").strip()
    if len(s) <= MAX_LEN:
        return s
    cut = s[:MAX_LEN]
    k = max(cut.rfind(". "), cut.rfind("다. "))
    return (cut[:k + 1] if k > MAX_LEN * 0.5 else cut) + " …"


def universe(mk):
    p = os.path.join(OUT_DIR, f"universe_{mk}.json")
    try:
        d = json.load(open(p, encoding="utf-8"))
        return [(str(r["ticker"]), r.get("name", "")) for r in d.get("rows", []) if r.get("ticker")]
    except Exception as e:
        print(f"[{mk}] 유니버스 없음: {e}")
        return []


def load_prev(mk):
    p = os.path.join(OUT_DIR, f"profile_{mk}.json")
    try:
        return json.load(open(p, encoding="utf-8")).get("p") or {}
    except Exception:
        return {}


def stale(rec):
    if not rec or not rec.get("s"):
        # 실패 기록은 7일 뒤 재시도
        return not rec or (rec.get("fail_at") or "0000") < (datetime.now(KST) - timedelta(days=7)).strftime("%Y-%m-%d")
    return (rec.get("at") or "0000") < (datetime.now(KST) - timedelta(days=REFRESH_DAYS)).strftime("%Y-%m-%d")


# ───────────────────────── 국내 ─────────────────────────
def kr_fnguide(sess, code):
    from bs4 import BeautifulSoup
    r = sess.get("https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp",
                 params={"pGB": "1", "gicode": "A" + code, "cID": "", "MenuYn": "Y", "ReportGB": "", "NewMenuID": "101", "stkGb": "701"},
                 timeout=20)
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "lxml")
    box = soup.select_one("#bizSummaryContent") or soup.select_one(".um_bssummary")
    lines = [li.get_text(" ", strip=True) for li in box.select("li")] if box else []
    head = soup.select_one("#bizSummaryHeader")
    ind = ""
    for sel in ("#compBody .stxt_group .stxt1", "p.stxt.stxt1", ".corp_group2 dd"):
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            ind = re.sub(r"^\s*(KSE|KOSDAQ|KOSPI)\s*", "", el.get_text(" ", strip=True)).strip(" |")
            break
    s = " ".join(x for x in lines if x)
    if not s:
        return None
    return {"s": clip(s), "ind": ind[:60], "title": head.get_text(" ", strip=True)[:80] if head else "", "src": "FnGuide"}


def kr_naver(sess, code):
    from bs4 import BeautifulSoup
    r = sess.get("https://finance.naver.com/item/main.naver", params={"code": code}, timeout=20)
    r.encoding = r.apparent_encoding or "euc-kr"
    soup = BeautifulSoup(r.text, "lxml")
    box = soup.select_one("#summary_info")
    ps = [p.get_text(" ", strip=True) for p in box.select("p")] if box else []
    s = " ".join(x for x in ps if x)
    if not s:
        return None
    return {"s": clip(s), "src": "네이버 금융(FnGuide 제공)"}


def run_kr(tickers, prev):
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    out, st = dict(prev), dict(new=0, fail=0, keep=0, skipped=0, fn=0, nv=0)
    todo = [t for t, _ in tickers if stale(prev.get(t))]
    st["keep"] = len(tickers) - len(todo)
    for i, t in enumerate(todo):
        if over() or st["new"] + st["fail"] >= MAX_FETCH:
            st["skipped"] += 1
            continue
        rec = None
        for f, k in ((kr_fnguide, "fn"), (kr_naver, "nv")):
            try:
                rec = f(sess, t)
            except Exception:
                rec = None
            if rec:
                st[k] += 1
                break
        if rec:
            rec["at"] = TODAY
            out[t] = rec
            st["new"] += 1
        else:
            out[t] = {**(prev.get(t) or {}), "fail_at": TODAY}
            st["fail"] += 1
        time.sleep(0.15)
        if i and i % 200 == 0:
            print(f"  [kr] {i}/{len(todo)} …")
    return out, st


# ───────────────────────── 미국 ─────────────────────────
def yahoo_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    s.get("https://fc.yahoo.com", timeout=15)
    crumb = (s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15).text or "").strip()
    if not crumb or len(crumb) > 40:
        raise RuntimeError(f"crumb 이상: {crumb[:30]!r}")
    return s, crumb


def us_one(sess, crumb, t):
    sym = t.replace(".", "-")
    r = sess.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}",
                 params={"modules": "assetProfile", "crumb": crumb}, timeout=20)
    res = ((r.json() or {}).get("quoteSummary") or {}).get("result") or []
    ap = (res[0] or {}).get("assetProfile") if res else None
    if not ap or not ap.get("longBusinessSummary"):
        return None
    hq = ", ".join(x for x in (ap.get("city"), ap.get("state"), ap.get("country")) if x)
    return {"s": clip(ap.get("longBusinessSummary")), "ind": (ap.get("industry") or "")[:60],
            "sec": (ap.get("sector") or "")[:40], "emp": ap.get("fullTimeEmployees"),
            "hq": hq[:80], "web": (ap.get("website") or "")[:100], "src": "Yahoo Finance"}


def run_us(tickers, prev):
    out, st = dict(prev), dict(new=0, fail=0, keep=0, skipped=0)
    todo = [t for t, _ in tickers if stale(prev.get(t))]
    st["keep"] = len(tickers) - len(todo)
    try:
        sess, crumb = yahoo_session()
    except Exception as e:
        st["err"] = f"{type(e).__name__}: {e}"[:120]
        return out, st
    for i, t in enumerate(todo):
        if over() or st["new"] + st["fail"] >= MAX_FETCH:
            st["skipped"] += 1
            continue
        rec = None
        for k in range(2):
            try:
                rec = us_one(sess, crumb, t)
                break
            except Exception:
                time.sleep(1.5)
        if rec:
            rec["at"] = TODAY
            out[t] = rec
            st["new"] += 1
        else:
            out[t] = {**(prev.get(t) or {}), "fail_at": TODAY}
            st["fail"] += 1
        time.sleep(0.12)
        if i and i % 200 == 0:
            print(f"  [us] {i}/{len(todo)} …")
    return out, st


def main():
    mks = [m.strip() for m in os.getenv("MARKETS", "kr,us").split(",") if m.strip()]
    os.makedirs(OUT_DIR, exist_ok=True)
    for mk in mks:
        tk = universe(mk)
        if not tk:
            continue
        prev = load_prev(mk)
        out, st = (run_kr if mk == "kr" else run_us)(tk, prev)
        keep = {t for t, _ in tk}
        out = {t: v for t, v in out.items() if t in keep}        # 유니버스에서 빠진 종목은 정리
        n_ok = sum(1 for v in out.values() if v.get("s"))
        payload = {"asof": TODAY, "n": n_ok, "diag": st, "p": out}
        p = os.path.join(OUT_DIR, f"profile_{mk}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        print(f"[{mk}] 개요 {n_ok}/{len(tk)}종목 · {st} · {os.path.getsize(p):,} bytes")


if __name__ == "__main__":
    sys.exit(main())
