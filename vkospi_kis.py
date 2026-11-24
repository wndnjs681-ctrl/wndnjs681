#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VKOSPI(코스피200 변동성지수) 일봉 → output/market_index.json 의 idx["VKOSPI"].

야후·네이버에는 없어서 한국투자증권 Open API(업종 기간별 시세, 조회 전용)로 받는다.
지수 코드는 한국투자증권 공개 업종 코드표(idxcode.mst)에서 이름에 '변동성'이 든 코스피200 지수를 찾는다.
토큰은 같은 작업에서 kr_prog_kis.py 가 받아 둔 것을 다시 쓴다(발급 횟수 제한).
"""
import io, json, os, re, time, zipfile
from datetime import datetime, timedelta, timezone

import requests

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
BASE = os.getenv("KIS_BASE", "https://openapi.koreainvestment.com:9443")
KEY, SECRET = os.getenv("KIS_APP_KEY", "").strip(), os.getenv("KIS_APP_SECRET", "").strip()
TOK_FILE = os.getenv("KIS_TOKEN_FILE", "/tmp/kis_token.json")
KEEP = 1300


def token():
    try:
        t = json.load(open(TOK_FILE))
        if time.time() - t["at"] < 6 * 3600:
            return t["tok"]
    except Exception:
        pass
    j = requests.post(BASE + "/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": KEY, "appsecret": SECRET}, timeout=20).json()
    if j.get("access_token"):
        json.dump({"tok": j["access_token"], "at": time.time()}, open(TOK_FILE, "w"))
        return j["access_token"]
    print("토큰 발급 실패:", {k: v for k, v in j.items() if k != "access_token"})
    return None


def codes():
    """업종 코드표에서 '변동성' 지수 후보 코드. 실패하면 흔히 쓰이는 후보."""
    out = []
    try:
        r = requests.get("https://new.real.download.dws.co.kr/common/master/idxcode.mst.zip", timeout=30)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        txt = z.read(z.namelist()[0]).decode("cp949", "ignore")
        for ln in txt.splitlines():
            if "변동성지수" in ln and "200" in ln and "목표" not in ln:
                m = re.match(r"\s*\S?(\d{4})", ln)
                print("  코드표:", ln.strip()[:60])
                if m:
                    out.append(m.group(1))
    except Exception as e:
        print("  코드표 실패:", type(e).__name__, str(e)[:120])
    return out


def daily(tok, code):
    h = {"authorization": "Bearer " + tok, "appkey": KEY, "appsecret": SECRET, "tr_id": "FHKUP03500100", "custtype": "P"}
    end, m, nm, j = datetime.now(KST), {}, None, {}
    for _ in range(40):                                   # 한 번에 최대 약 50거래일 → 거꾸로 이어 받기
        st = end - timedelta(days=70)
        p = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": st.strftime("%Y%m%d"),
             "FID_INPUT_DATE_2": end.strftime("%Y%m%d"), "FID_PERIOD_DIV_CODE": "D"}
        try:
            j = requests.get(BASE + "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice", headers=h, params=p, timeout=20).json()
        except Exception as e:
            print("  ", code, type(e).__name__)
            break
        if j.get("rt_cd") != "0":
            print("  ", code, j.get("msg_cd"), j.get("msg1"))
            if "초당" in str(j.get("msg1")):
                time.sleep(1.2)
                continue
            break
        o1, rows = j.get("output1") or {}, j.get("output2") or []
        if not m:
            nm = o1.get("hts_kor_isnm")
            print(f"  {code}: {nm or ''} 현재 {o1.get('bstp_nmix_prpr')} · {len(rows)}행")
        got = 0
        for r in rows:
            d = r.get("stck_bsop_date")
            c = float(r.get("bstp_nmix_prpr") or 0)
            if d and c > 0:
                f = lambda k: round(float(r.get(k) or 0), 2) or None
                m[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = (f("bstp_nmix_oprc"), f("bstp_nmix_hgpr"), f("bstp_nmix_lwpr"), round(c, 2))
                got += 1
        if not got or len(m) >= KEEP:
            break
        end = datetime.strptime(min(m), "%Y-%m-%d") - timedelta(days=1)
        time.sleep(0.25)
    return m, nm


KRX = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KH = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201010101",
      "X-Requested-With": "XMLHttpRequest"}


def krx():
    """KRX 정보데이터시스템: 지수 검색으로 '변동성' 지수 코드를 찾고 일별 시세를 받는다."""
    cands, r = [], None
    for mkt in ("1", "2", "3", "4", "5", ""):
        try:
            r = requests.post(KRX, data={"bld": "dbms/comm/finder/finder_equidx", "mktsel": mkt, "searchText": "변동성"}, headers=KH, timeout=20)
            j = r.json()
            rows = j.get("block1") or []
            print(f"  KRX 검색 mktsel={mkt!r}: HTTP {r.status_code} · {len(rows)}건 · {str(rows[:3])[:200]}")
            for x in rows:
                nm = str(x.get("codeName") or x.get("full_code") or "")
                if "200" in nm and "변동성" in nm and "목표" not in nm and "저변동" not in nm:
                    cands.append(x)
        except Exception as e:
            print(f"  KRX 검색 mktsel={mkt!r}: {type(e).__name__} {str(e)[:120]} · {(getattr(r, 'text', '') or '')[:120]!r}")
        if cands:
            break
    end = datetime.now(KST)
    for x in cands:
        g1, g2 = str(x.get("marketCode") or x.get("indIdx") or ""), str(x.get("short_code") or x.get("indIdx2") or x.get("full_code") or "")
        print("  KRX 후보:", x)
        m = {}
        for yrs in range(5):                                 # 2년씩 끊어 받기(한 번 요청 기간 제한)
            st, en = end - timedelta(days=730 * (yrs + 1)), end - timedelta(days=730 * yrs)
            try:
                r = requests.post(KRX, data={"bld": "dbms/MDC/STAT/standard/MDCSTAT00301", "indIdx": g1, "indIdx2": g2,
                                             "strtDd": st.strftime("%Y%m%d"), "endDd": en.strftime("%Y%m%d"), "share": "1", "money": "1"}, headers=KH, timeout=30)
                rows = r.json().get("output") or []
            except Exception as e:
                print("   시세 실패", type(e).__name__, getattr(r, "text", "")[:120])
                break
            if not yrs:
                print(f"   시세 {len(rows)}행 · {str(rows[:1])[:200]}")
            for q in rows:
                d = str(q.get("TRD_DD") or "").replace("/", "-")
                f = lambda k: round(float(str(q.get(k) or "0").replace(",", "")), 2) or None
                if d and f("CLSPRC_IDX"):
                    m[d] = (f("OPNPRC_IDX"), f("HGPRC_IDX"), f("LWPRC_IDX"), f("CLSPRC_IDX"))
            if not rows:
                break
            time.sleep(0.5)
        if len(m) > 100:
            return "KRX:" + g1 + "/" + g2, m
    return None, {}


def others():
    """그 밖의 공개 경로: 인베스팅닷컴 API → 네이버 모바일 → 야후 ^VKOSPI. 되는 첫 경로를 쓴다."""
    UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
    try:                                                     # 인베스팅닷컴: 검색으로 id → 일별 시세
        r = requests.get("https://api.investing.com/api/search/v2/search", params={"q": "vkospi"}, headers={**UA, "domain-id": "www"}, timeout=20)
        print(f"  인베스팅 검색: HTTP {r.status_code} · {r.text[:200]!r}")
        q = (r.json().get("quotes") or []) if r.ok else []
        q = [x for x in q if "VKOSPI" in str(x.get("symbol", "")).upper() or "volatility" in str(x.get("description", "")).lower()]
        if q:
            iid = q[0]["id"]
            m = {}
            end = datetime.now(KST)
            for k in range(5):
                st, en = end - timedelta(days=365 * (k + 1)), end - timedelta(days=365 * k)
                r = requests.get(f"https://api.investing.com/api/financialdata/historical/{iid}", params={"start-date": st.strftime("%Y-%m-%d"),
                                 "end-date": en.strftime("%Y-%m-%d"), "time-frame": "Daily", "add-missing-rows": "false"}, headers={**UA, "domain-id": "www"}, timeout=20)
                rows = (r.json().get("data") or []) if r.ok else []
                if not k:
                    print(f"  인베스팅 시세 {iid}: HTTP {r.status_code} · {len(rows)}행 · {str(rows[:1])[:200]}")
                for x in rows:
                    d = str(x.get("rowDateTimestamp") or x.get("rowDateRaw") or "")[:10]
                    f = lambda kk: round(float(x.get(kk)), 2) if x.get(kk) not in (None, "") else None
                    if d and f("last_closeRaw"):
                        m[d] = (f("last_openRaw"), f("last_maxRaw"), f("last_minRaw"), f("last_closeRaw"))
                if not rows:
                    break
            if len(m) > 100:
                return f"investing:{iid}", m
    except Exception as e:
        print("  인베스팅:", type(e).__name__, str(e)[:150])
    for u in ("https://m.stock.naver.com/api/index/VKOSPI/price?pageSize=60&page=1", "https://polling.finance.naver.com/api/realtime/domestic/index/VKOSPI"):
        try:
            r = requests.get(u, headers=UA, timeout=15)
            print(f"  네이버 {u.split('/api/')[1][:40]}: HTTP {r.status_code} · {r.text[:160]!r}")
        except Exception as e:
            print("  네이버:", type(e).__name__)
    try:
        r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EVKOSPI", params={"range": "5y", "interval": "1d"}, headers=UA, timeout=20)
        print(f"  야후 ^VKOSPI: HTTP {r.status_code} · {r.text[:160]!r}")
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        m = {}
        for t, o, h, l, c in zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"]):
            if c:
                m[datetime.fromtimestamp(t, KST).strftime("%Y-%m-%d")] = tuple(round(x, 2) if x else None for x in (o, h, l, c))
        if len(m) > 100:
            return "^VKOSPI", m
    except Exception as e:
        print("  야후:", type(e).__name__, str(e)[:100])
    return None, {}


def save(sym, m):
    d = sorted(m)[-KEEP:]
    p = os.path.join(OUT, "market_index.json")
    mi = json.load(open(p, encoding="utf-8"))
    mi.setdefault("idx", {})["VKOSPI"] = {"name": "VKOSPI(코스피 공포지수)", "mk": "macro", "rg": "거시", "core": False, "kind": "vix", "sym": sym,
                                          "d": d, "o": [m[x][0] for x in d], "h": [m[x][1] for x in d], "l": [m[x][2] for x in d],
                                          "c": [m[x][3] for x in d], "v": [0] * len(d)}
    json.dump(mi, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"→ VKOSPI {len(d)}일 · {d[0]}~{d[-1]} · 마지막 {m[d[-1]][3]} ({sym})")


def main():
    sym, m = krx()
    if not m:
        sym, m = others()
    if m:
        save(sym, m)
        return
    if not KEY or not SECRET:
        print("KIS 키 없음 — VKOSPI 건너뜀")
        return
    tok = token()
    if not tok:
        return
    best = None
    for c in codes():
        m, nm = daily(tok, c)
        last = m[max(m)][3] if m else None
        print(f"[VKOSPI 후보 {c}] {nm} · {len(m)}일 · 마지막 {max(m) if m else '-'} {last}")
        if len(m) > 100 and last and 5 < last < 100 and (nm is None or "변동" in str(nm) or "VKOSPI" in str(nm).upper()):
            best = (c, m)
            break
    if not best:
        print("VKOSPI 못 받음")
        return
    save("KIS:U" + best[0], best[1])


if __name__ == "__main__":
    main()
