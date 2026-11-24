#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VKOSPI(코스피200 변동성지수) 일봉 → output/market_index.json 의 idx["VKOSPI"].

야후·네이버·KRX(로그인 필요)·인베스팅(차단)에서는 못 받아서 한국투자증권 Open API(업종 기간별 시세, 조회 전용)로 받는다.
지수 코드는 한국투자증권 공개 업종 코드표(idxcode.mst)의 'VKOSPI'(00503). 코드가 바뀌면 코드표에서 다시 찾는다.
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
    """VKOSPI 업종 코드 후보: 알려진 0503 + 코드표에서 이름이 VKOSPI 인 코드."""
    out = ["0503"]                                       # 한국투자증권 업종 코드표의 VKOSPI(00503)
    try:
        r = requests.get("https://new.real.download.dws.co.kr/common/master/idxcode.mst.zip", timeout=30)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        txt = z.read(z.namelist()[0]).decode("cp949", "ignore")
        for ln in txt.splitlines():
            u = ln.upper()
            if "VKOSPI" in u or "V-KOSPI" in u:
                m = re.match(r"\s*\S?(\d{4})", ln)
                print("  코드표:", ln.strip()[:60])
                if m and m.group(1) not in out:
                    out.append(m.group(1))
    except Exception as e:
        print("  코드표 실패:", type(e).__name__, str(e)[:120])
    return out or ["0503"]


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


def save(sym, m):
    now = datetime.now(KST)
    if (now.hour, now.minute) < (15, 45):                  # 장중이면 오늘 봉은 아직 확정 전 — 뺀다
        m = {k: v for k, v in m.items() if k < now.strftime("%Y-%m-%d")}
    d = sorted(m)[-KEEP:]
    p = os.path.join(OUT, "market_index.json")
    mi = json.load(open(p, encoding="utf-8"))
    mi.setdefault("idx", {})["VKOSPI"] = {"name": "VKOSPI(코스피 공포지수)", "mk": "macro", "rg": "거시", "core": False, "kind": "vix", "sym": sym,
                                          "d": d, "o": [m[x][0] for x in d], "h": [m[x][1] for x in d], "l": [m[x][2] for x in d],
                                          "c": [m[x][3] for x in d], "v": [0] * len(d)}
    json.dump(mi, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"→ VKOSPI {len(d)}일 · {d[0]}~{d[-1]} · 마지막 {m[d[-1]][3]} ({sym})")


def main():
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
