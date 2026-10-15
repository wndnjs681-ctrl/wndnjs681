#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
한국투자증권 Open API 공용 모듈 — GitHub Actions 와 내 PC 실시간 서버가 같이 쓴다.

키 읽는 순서
  1) 환경 변수 KIS_APP_KEY / KIS_APP_SECRET / KIS_ENV   (GitHub Actions: 저장소 Secrets)
  2) 같은 폴더의 .env 파일                              (내 PC: realtime_server.py 가 처음 실행 때 만들어 준다)

KIS_ENV: real = 실전투자, paper = 모의투자 (앱키를 발급받은 쪽과 같아야 한다)
접근 토큰은 하루 유효하고 자주 새로 받으면 제한이 걸리므로 .kis_token.json 에 보관해 재사용한다.
"""
import json, os, time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
TOKEN_PATH = os.path.join(HERE, ".kis_token.json")

REST = {"real": "https://openapi.koreainvestment.com:9443",
        "paper": "https://openapivts.koreainvestment.com:29443"}
WS = {"real": "ws://ops.koreainvestment.com:21000",
      "paper": "ws://ops.koreainvestment.com:31000"}


class KISError(RuntimeError):
    pass


# ── 설정 ──────────────────────────────────────────────────────
def load_env(path=ENV_PATH):
    """.env 의 KEY=VALUE 를 환경 변수로 (이미 있는 값은 덮지 않는다)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def save_env(app_key, app_secret, env, path=ENV_PATH):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 한국투자증권 Open API — 이 파일은 GitHub 에 올리지 않는다 (.gitignore)\n")
        f.write(f"KIS_APP_KEY={app_key}\nKIS_APP_SECRET={app_secret}\nKIS_ENV={env}\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def config():
    load_env()
    key, secret = os.getenv("KIS_APP_KEY", "").strip(), os.getenv("KIS_APP_SECRET", "").strip()
    env = (os.getenv("KIS_ENV", "real").strip().lower() or "real")
    if env not in REST:
        raise KISError(f"KIS_ENV 는 real 또는 paper 여야 합니다 (지금: {env!r})")
    return key, secret, env


# ── 클라이언트 ────────────────────────────────────────────────
class KIS:
    def __init__(self, app_key=None, app_secret=None, env=None, token_cache=True):
        k, s, e = config()
        self.key, self.secret, self.env = app_key or k, app_secret or s, env or e
        if not self.key or not self.secret:
            raise KISError("앱키(KIS_APP_KEY)·앱시크릿(KIS_APP_SECRET)이 없습니다.")
        self.base = REST[self.env]
        self.ws_url = WS[self.env]
        self.token_cache = token_cache
        self._token = None
        self._last_call = 0.0
        # 초당 호출 제한: 실전 20회, 모의 2회 — 여유 있게 잡는다
        self.min_gap = 0.06 if self.env == "real" else 0.55

    # 접근 토큰 (24시간 유효, 발급은 1분에 1번까지)
    def token(self):
        now = time.time()
        if self._token and self._token["exp"] - now > 600:
            return self._token["access_token"]
        if self.token_cache and os.path.exists(TOKEN_PATH):
            try:
                with open(TOKEN_PATH, encoding="utf-8") as f:
                    t = json.load(f)
                if t.get("key") == self.key[-6:] and t.get("env") == self.env and t["exp"] - now > 600:
                    self._token = t
                    return t["access_token"]
            except (OSError, ValueError, KeyError):
                pass
        r = requests.post(self.base + "/oauth2/tokenP", timeout=15,
                          json={"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret})
        j = _json(r)
        if "access_token" not in j:
            raise KISError(f"접근 토큰 발급 실패: {j.get('error_description') or j.get('msg1') or j}")
        t = {"access_token": j["access_token"], "exp": now + int(j.get("expires_in", 86400)),
             "key": self.key[-6:], "env": self.env}
        self._token = t
        if self.token_cache:
            try:
                with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                    json.dump(t, f)
                os.chmod(TOKEN_PATH, 0o600)
            except OSError:
                pass
        return t["access_token"]

    # 실시간(WebSocket) 접속키
    def approval_key(self):
        r = requests.post(self.base + "/oauth2/Approval", timeout=15,
                          json={"grant_type": "client_credentials", "appkey": self.key, "secretkey": self.secret})
        j = _json(r)
        if "approval_key" not in j:
            raise KISError(f"실시간 접속키 발급 실패: {j.get('error_description') or j.get('msg1') or j}")
        return j["approval_key"]

    def get(self, path, tr_id, params, tries=3):
        for attempt in range(tries):
            wait = self.min_gap - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            r = requests.get(self.base + path, params=params, timeout=15, headers={
                "authorization": "Bearer " + self.token(), "appkey": self.key, "appsecret": self.secret,
                "tr_id": tr_id, "custtype": "P", "content-type": "application/json; charset=utf-8"})
            j = _json(r)
            if j.get("rt_cd") == "0":
                return j
            if j.get("msg_cd") == "EGW00201" and attempt < tries - 1:   # 초당 호출 초과
                time.sleep(1.0)
                continue
            raise KISError(f"{tr_id} 실패: [{j.get('msg_cd')}] {j.get('msg1')}")
        raise KISError(f"{tr_id} 실패: 재시도 초과")

    # ── 시세 ──
    def price_kr(self, code):
        """국내 주식 현재가."""
        o = self.get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})["output"]
        return {"price": _f(o.get("stck_prpr")), "open": _f(o.get("stck_oprc")), "high": _f(o.get("stck_hgpr")),
                "low": _f(o.get("stck_lwpr")), "volume": _f(o.get("acml_vol")), "chg_pct": _f(o.get("prdy_ctrt")),
                "name": o.get("hts_kor_isnm") or ""}

    def price_us(self, symbol, excd="NAS"):
        """미국 주식 현재가. excd: NAS 나스닥, NYS 뉴욕, AMS 아멕스."""
        o = self.get("/uapi/overseas-price/v1/quotations/price", "HHDFS00000300",
                     {"AUTH": "", "EXCD": excd, "SYMB": symbol})["output"]
        return {"price": _f(o.get("last")), "volume": _f(o.get("tvol")), "chg_pct": _f(o.get("rate")), "excd": excd}

    def find_us_exchange(self, symbol):
        """미국 종목이 어느 거래소인지 현재가 조회로 찾는다 (가격이 0 이면 그 거래소가 아님)."""
        for ex in ("NAS", "NYS", "AMS"):
            try:
                p = self.price_us(symbol, ex)
            except KISError:
                continue
            if p["price"]:
                return ex
        return None

    def minutes_kr(self, code, max_pages=14):
        """국내 주식 당일 1분봉 (09:00 부터 지금까지). 한 번에 30개씩 거꾸로 받아 이어 붙인다."""
        hour = datetime.now(KST).strftime("%H%M%S")
        hour = min(hour, "153000")
        out = {}
        for _ in range(max_pages):
            j = self.get("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200",
                         {"FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                          "FID_INPUT_HOUR_1": hour, "FID_PW_DATA_INCU_YN": "N"})
            rows = [r for r in (j.get("output2") or []) if r.get("stck_cntg_hour")]
            if not rows:
                break
            for r in rows:
                key = r["stck_bsop_date"] + r["stck_cntg_hour"]
                out[key] = {"d": r["stck_bsop_date"], "hm": r["stck_cntg_hour"][:4],
                            "o": _f(r.get("stck_oprc")), "h": _f(r.get("stck_hgpr")),
                            "l": _f(r.get("stck_lwpr")), "c": _f(r.get("stck_prpr")), "v": _f(r.get("cntg_vol"))}
            earliest = min(r["stck_cntg_hour"] for r in rows)
            if earliest <= "090000":
                break
            t = datetime.strptime(earliest, "%H%M%S") - timedelta(minutes=1)
            hour = t.strftime("%H%M%S")
        return [out[k] for k in sorted(out)]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _json(r):
    try:
        return r.json()
    except ValueError:
        raise KISError(f"응답을 읽지 못했습니다 (HTTP {r.status_code}): {r.text[:200]}")
