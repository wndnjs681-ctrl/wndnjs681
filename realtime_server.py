#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
내 PC 실시간 차트 서버 — 한투 실시간 체결가를 받아 chart.html 에 흘려준다.

  python realtime_server.py          → http://localhost:8000/chart.html 이 열린다

처음 실행하면 한투 앱키·앱시크릿을 물어보고 같은 폴더의 .env 에 저장한다 (GitHub 에 올라가지 않음).
이 프로그램은 내 PC 안에서만 열린다(127.0.0.1). 브라우저 ↔ 서버는 SSE, 서버 ↔ 한투는 WebSocket.

  GET  /api/status              실시간 켜짐 여부
  GET  /api/stream              틱 스트림 (Server-Sent Events)
  POST /api/subscribe {ids:[]}  구독할 종목 (kr:005930, us:AAPL … 최대 40개)
  GET  /api/minutes?id=kr:…     국내 당일 1분봉 (한투 REST)
"""
import asyncio, getpass, json, mimetypes, os, queue, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import requests  # noqa: F401
    import websockets
except ImportError:
    print("필요한 패키지가 없습니다. 아래를 한 번 실행한 뒤 다시 시작하세요:\n\n  python -m pip install requests websockets\n")
    sys.exit(1)

import kis
from kis import KIS, KISError, TR_KR, TR_US, parse_frame, sub_message, tick_kr, tick_us

PORT = int(os.getenv("PORT", "8000"))
MAX_SUBS = 40
CACHE_PATH = os.path.join(HERE, ".kis_cache.json")


# ── 처음 실행: 키 입력 받아 .env 저장 ─────────────────────────
def first_run_setup():
    kis.load_env()
    if os.getenv("KIS_APP_KEY") and os.getenv("KIS_APP_SECRET"):
        return
    print("한국투자증권 Open API 키를 처음 한 번만 입력합니다. (입력한 글자는 화면에 보이지 않습니다)\n")
    key = getpass.getpass("앱키(App Key)를 붙여넣고 Enter: ").strip()
    secret = getpass.getpass("앱시크릿(App Secret)을 붙여넣고 Enter: ").strip()
    env = input("실전투자면 1, 모의투자면 2 를 입력하고 Enter [1]: ").strip()
    env = "paper" if env == "2" else "real"
    if not key or not secret:
        print("키가 비어 있어 멈춥니다. 다시 실행해 주세요.")
        sys.exit(1)
    kis.save_env(key, secret, env)
    os.environ.update({"KIS_APP_KEY": key, "KIS_APP_SECRET": secret, "KIS_ENV": env})
    print(f"\n저장했습니다: {kis.ENV_PATH}\n다음부터는 묻지 않습니다. 키를 바꾸려면 이 파일을 지우고 다시 실행하세요.\n")


# ── 브라우저로 보내기 (SSE) ──────────────────────────────────
class Hub:
    def __init__(self):
        self.clients, self.lock = set(), threading.Lock()
        self.pending, self.plock = {}, threading.Lock()
        self.status = {"type": "status", "state": "starting", "msg": "한투 실시간에 연결하는 중"}

    def add(self):
        q = queue.Queue(maxsize=2000)
        with self.lock:
            self.clients.add(q)
        q.put(self.status)
        return q

    def remove(self, q):
        with self.lock:
            self.clients.discard(q)

    def send(self, msg):
        with self.lock:
            for q in list(self.clients):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass

    def set_status(self, state, msg):
        self.status = {"type": "status", "state": state, "msg": msg}
        self.send(self.status)

    # 틱은 종목별로 0.25초마다 묶어서 보낸다 (체결량은 더해 둔다)
    def tick(self, t):
        with self.plock:
            p = self.pending.get(t["id"])
            if p:
                t["vol"] = (t.get("vol") or 0) + (p.get("vol") or 0)
            self.pending[t["id"]] = t

    def flush_loop(self):
        while True:
            time.sleep(0.25)
            with self.plock:
                items, self.pending = list(self.pending.values()), {}
            for t in items:
                self.send({"type": "tick", **t})


HUB = Hub()


# ── 한투 실시간 연결 ─────────────────────────────────────────
class Feed:
    def __init__(self, client):
        self.k = client
        self.want = set()          # 브라우저가 원하는 종목 id
        self.active = {}           # (tr, key) → id
        self.us_ex = self._load_cache()
        self.loop = asyncio.new_event_loop()
        self.ws = None
        self.wake = None

    def _load_cache(self):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f).get("us_exchange", {})
        except (OSError, ValueError):
            return {}

    def _save_cache(self):
        try:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"us_exchange": self.us_ex}, f)
        except OSError:
            pass

    def set_want(self, ids):
        self.want = set(ids[:MAX_SUBS])
        if self.wake:
            self.loop.call_soon_threadsafe(self.wake.set)

    def target(self, id_):
        mk, code = id_.split(":", 1)
        if mk == "kr":
            return (TR_KR, code)
        if mk == "us":
            ex = self.us_ex.get(code)
            if ex is None:
                try:
                    ex = self.k.find_us_exchange(code) or ""
                except KISError:
                    ex = ""
                self.us_ex[code] = ex
                self._save_cache()
            return (TR_US, "D" + ex + code) if ex else None
        return None

    async def sync_subs(self):
        want = {}
        for id_ in list(self.want):
            t = await self.loop.run_in_executor(None, self.target, id_)
            if t:
                want[t] = id_
        for t in [t for t in self.active if t not in want]:
            await self.ws.send(sub_message(self.ak, t[0], t[1], subscribe=False))
            self.active.pop(t, None)
        for t, id_ in want.items():
            if t not in self.active:
                await self.ws.send(sub_message(self.ak, t[0], t[1]))
                self.active[t] = id_

    async def session(self):
        self.ak = await self.loop.run_in_executor(None, self.k.approval_key)
        async with websockets.connect(self.k.ws_url, ping_interval=None, max_size=2 ** 20) as ws:
            self.ws, self.active = ws, {}
            HUB.set_status("on", "한투 실시간 연결됨")
            await self.sync_subs()
            recv = asyncio.ensure_future(ws.recv())
            while True:
                waiter = asyncio.ensure_future(self.wake.wait())
                done, _ = await asyncio.wait({recv, waiter}, return_when=asyncio.FIRST_COMPLETED)
                if waiter in done:
                    self.wake.clear()
                    await self.sync_subs()
                else:
                    waiter.cancel()
                if recv in done:
                    self.handle(recv.result())
                    recv = asyncio.ensure_future(ws.recv())
                    if getattr(self, "_pong", None):
                        await ws.send(self._pong)
                        self._pong = None

    def handle(self, msg):
        frames = parse_frame(msg)
        if frames is None:
            try:
                j = json.loads(msg)
            except ValueError:
                return
            h, b = j.get("header", {}), j.get("body", {})
            if h.get("tr_id") == "PINGPONG":
                self._pong = msg
            elif b.get("rt_cd") not in (None, "0"):
                print(f"  구독 응답: {h.get('tr_key')} {b.get('msg1')}")
            return
        for tr, f in frames:
            try:
                t = tick_kr(f) if tr == TR_KR else tick_us(f) if tr == TR_US else None
            except (IndexError, ValueError):
                continue
            if t and t["price"]:
                HUB.tick(t)

    async def run(self):
        self.wake = asyncio.Event()
        delay = 2
        while True:
            try:
                await self.session()
            except KISError as e:
                HUB.set_status("error", str(e)); print("✘", e)
                delay = 60
            except Exception as e:  # 연결 끊김 등 — 잠시 뒤 다시
                HUB.set_status("retry", f"다시 연결하는 중 ({type(e).__name__})")
            self.ws = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    def start(self):
        def go():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.run())
        threading.Thread(target=go, daemon=True).start()


FEED = None


# ── HTTP ─────────────────────────────────────────────────────
ALLOWED_PREFIX = ("chart/", "output/")


class Handler(BaseHTTPRequestHandler):
    server_version = "ScreenerChart/1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self.send_response(302); self.send_header("location", "/chart.html"); self.end_headers(); return
        if u.path == "/api/status":
            return self._json({"live": True, "env": FEED.k.env, "state": HUB.status["state"], "msg": HUB.status["msg"]})
        if u.path == "/api/minutes":
            id_ = (parse_qs(u.query).get("id") or [""])[0]
            rows = []
            if id_.startswith("kr:"):
                try:
                    rows = FEED.k.minutes_kr(id_[3:])
                except KISError as e:
                    return self._json({"rows": [], "error": str(e)})
            return self._json({"rows": rows})
        if u.path == "/api/stream":
            return self.stream()
        return self.static(u.path)

    def do_POST(self):
        if urlparse(self.path).path != "/api/subscribe":
            return self._json({"error": "not found"}, 404)
        n = int(self.headers.get("content-length") or 0)
        try:
            ids = [str(x) for x in json.loads(self.rfile.read(n) or b"{}").get("ids", []) if str(x)[:3] in ("kr:", "us:")]
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        FEED.set_want(ids)
        return self._json({"ok": True, "count": min(len(ids), MAX_SUBS)})

    def stream(self):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("connection", "keep-alive")
        self.end_headers()
        q = HUB.add()
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(b"data: " + json.dumps(msg, ensure_ascii=False).encode() + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            HUB.remove(q)

    # chart.html, chart/*, output/* 만 내준다 (.env 같은 파일은 절대 안 나감)
    def static(self, path):
        rel = path.lstrip("/")
        ok = rel == "chart.html" or (rel.startswith(ALLOWED_PREFIX) and "/." not in "/" + rel and ".." not in rel)
        full = os.path.realpath(os.path.join(HERE, rel))
        if not ok or not full.startswith(HERE + os.sep) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main():
    global FEED
    first_run_setup()
    try:
        client = KIS()
        client.token()
    except KISError as e:
        print(f"✘ 한투 연결 실패: {e}")
        print("  앱키·앱시크릿과 실전/모의 구분을 확인하세요. 다시 입력하려면 .env 파일을 지우고 다시 실행합니다.")
        sys.exit(1)
    print(f"✔ 한투 연결 확인 ({'실전' if client.env == 'real' else '모의'}투자)")
    FEED = Feed(client)
    FEED.start()
    threading.Thread(target=HUB.flush_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    url = f"http://localhost:{PORT}/chart.html"
    print(f"✔ 차트 주소: {url}\n  이 창을 닫으면 실시간이 멈춥니다. 끝내려면 Ctrl+C")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")


if __name__ == "__main__":
    main()
