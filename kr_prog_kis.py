#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
종목별 프로그램 매매 — 한국투자증권 Open API (시세 조회만, 주문 권한 사용 안 함)

  1) 프로그램매매 종합현황(일별) — 코스피·코스닥 시장 전체 차익·비차익
  2) 종목별 프로그램매매추이(일별) — 시가총액 상위 종목의 프로그램 매수·매도·순매수(수량·금액)
결과는 output/market_index.json 의 flow.kprog 에 넣는다(대시보드 동기화가 이 파일을 옮김).

키: 환경변수 KIS_APP_KEY / KIS_APP_SECRET (워크플로가 저장소 Secrets 에서 넣어 줌). 없으면 아무것도 하지 않는다.
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone

import requests

OUT = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
BASE = os.getenv("KIS_BASE", "https://openapi.koreainvestment.com:9443")
N_STOCK = int(os.getenv("PROG_STOCKS", "300"))
KEY, SECRET = os.getenv("KIS_APP_KEY", "").strip(), os.getenv("KIS_APP_SECRET", "").strip()


def num(v):
    try:
        return float(str(v).replace(",", "") or 0)
    except ValueError:
        return 0.0


def token():
    r = requests.post(BASE + "/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": KEY, "appsecret": SECRET}, timeout=20)
    j = r.json()
    if not j.get("access_token"):
        print("토큰 발급 실패:", {k: v for k, v in j.items() if k != "access_token"})
        return None
    return j["access_token"]


def get(tok, path, tr, params, tries=3):
    h = {"authorization": "Bearer " + tok, "appkey": KEY, "appsecret": SECRET, "tr_id": tr, "custtype": "P",
         "content-type": "application/json; charset=utf-8"}
    for k in range(tries):
        try:
            r = requests.get(BASE + path, headers=h, params=params, timeout=20)
            j = r.json()
            if j.get("rt_cd") == "0":
                return j
            msg = f"{j.get('msg_cd')} {j.get('msg1')}"
            if "초당" in msg or "EGW00201" in msg:            # 호출 한도 — 잠깐 쉬고 다시
                time.sleep(1.0 + k)
                continue
            return {"_err": msg}
        except Exception as e:
            time.sleep(1 + k)
            err = f"{type(e).__name__} {e}"
    return {"_err": "재시도 실패"}


def pick(o, *names):
    for n in names:
        if n in o and o[n] not in (None, ""):
            return num(o[n])
    return None


def stock_daily(tok, code):
    j = get(tok, "/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily", "FHPPG04650200",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": ""})
    rows = j.get("output") or j.get("output1") or []
    if isinstance(rows, dict):
        rows = [rows]
    return rows, j.get("_err")


def main():
    if not KEY or not SECRET:
        print("KIS 키 없음 — 건너뜀 (저장소 Secrets 에 KIS_APP_KEY·KIS_APP_SECRET)")
        return
    tok = token()
    if not tok:
        return
    p = os.path.join(OUT, "market_index.json")
    mi = json.load(open(p, encoding="utf-8"))
    flow = mi.setdefault("flow", {})
    u = json.load(open(os.path.join(OUT, "universe_kr.json"), encoding="utf-8"))
    rows = sorted((r for r in u["rows"] if not r.get("is_etf") and r.get("ticker")), key=lambda r: -(r.get("mcap") or 0))[:N_STOCK]
    out, first, errs = [], True, 0
    for r in rows:
        data, err = stock_daily(tok, r["ticker"])
        if first:
            print("응답 예시:", r["ticker"], err or (data[0] if data else "(빈 응답)"))
            first = False
        if err or not data:
            errs += 1
            if errs >= 15 and not out:
                print("오류가 계속됨 — 중단:", err)
                break
            continue
        data = sorted(data, key=lambda x: x.get("stck_bsop_date", ""))[-20:]
        d = [x.get("stck_bsop_date", "") for x in data]
        px = [pick(x, "stck_clpr") for x in data]
        na = [round((pick(x, "whol_smtn_ntby_tr_pbmn") or 0) / 100, 1) for x in data]     # 백만원 → 억원
        nb = [round((pick(x, "whol_smtn_shnu_tr_pbmn") or 0) / 100, 1) for x in data]
        ns = [round((pick(x, "whol_smtn_seln_tr_pbmn") or 0) / 100, 1) for x in data]
        nq = [int(pick(x, "whol_smtn_ntby_qty") or 0) for x in data]
        out.append({"t": r["ticker"], "n": r.get("name") or r["ticker"], "sec": r.get("sector") or "", "d": d, "px": px,
                    "na": na, "buy": nb, "sell": ns, "nq": nq})
        time.sleep(0.07)                                        # 초당 호출 한도(실전 20건) 아래로
    print(f"종목별 프로그램 {len(out)}/{len(rows)}종목 · 오류 {errs}")
    if out:
        flow["kprog"] = {"asof": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "stocks": out}
        json.dump(mi, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        s = sorted(out, key=lambda x: -x["na"][-1])[:5]
        print("오늘 프로그램 순매수 상위:", [(x["n"], x["na"][-1]) for x in s])
        print(f"→ {p} flow.kprog")


if __name__ == "__main__":
    main()
