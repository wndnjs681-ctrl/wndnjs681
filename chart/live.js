// ── 실시간: 내 PC의 realtime_server.py 가 있을 때만 켜진다 ──
// 서버 API: GET /api/status, GET /api/stream (SSE), POST /api/subscribe {ids}, GET /api/minutes?id=
"use strict";
(function () {
  const Live = {
    on: false, app: null, quotes: {}, minutes: {}, subscribed: new Set(), state: "off", msg: "",
    async init(app) {
      this.app = app;
      try {
        const r = await fetch("/api/status", { cache: "no-store" });
        if (!r.ok) throw new Error();
        const j = await r.json();
        if (!j.live) throw new Error();
        this.on = true; this.env = j.env;
      } catch { this.render(); return; }
      this.connect(); app.syncToolbar(); this.resubscribe(); this.render();
    },
    connect() {
      if (this.es) this.es.close();
      this.es = new EventSource("/api/stream");
      this.es.onopen = () => { this.state = "on"; this.render(); this.resubscribe(true); };
      this.es.onerror = () => { this.state = "retry"; this.render(); };
      this.es.onmessage = e => { let m; try { m = JSON.parse(e.data); } catch { return; } this.handle(m); };
    },
    handle(m) {
      if (m.type === "tick") this.tick(m);
      else if (m.type === "status") { this.state = m.state || this.state; this.msg = m.msg || ""; this.render(); }
    },
    render() {
      const el = document.getElementById("liveStatus"); if (!el) return;
      if (!this.on) { el.textContent = "일봉 데이터 (실시간 꺼짐)"; el.className = "tb-live off"; el.title = "내 PC에서 realtime_server.py 로 열면 한투 실시간 시세가 붙습니다"; return; }
      const label = { on: "실시간 연결됨", retry: "다시 연결 중…", error: "실시간 오류" }[this.state] || "실시간 준비 중";
      el.textContent = label + (this.env === "paper" ? " · 모의" : ""); el.className = "tb-live " + this.state; el.title = this.msg || label;
    },
    // 차트·비교·관심종목에 있는 국내·미국 종목을 구독 (최대 40개)
    resubscribe(force) {
      if (!this.on) return;
      clearTimeout(this._t);
      this._t = setTimeout(() => {
        const a = this.app, ids = new Set();
        for (const c of a.charts) { ids.add(c.cfg.id); c.cfg.compare.forEach(x => ids.add(x.id)); }
        for (const id of a.allWatchIds()) ids.add(id);
        const list = [...ids].filter(id => /^(kr|us):/.test(id)).slice(0, 40);
        const key = list.join(",");
        if (!force && key === this._last) return;
        this._last = key; this.subscribed = new Set(list);
        fetch("/api/subscribe", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ids: list }) }).catch(() => {});
      }, 300);
    },
    async loadMinutes(id) {
      if (!this.on || this.minutes[id]) return;
      try {
        const r = await fetch("/api/minutes?id=" + encodeURIComponent(id)); const j = await r.json();
        this.minutes[id] = (j.rows || []).map(x => ({ t: dt(x.d, x.hm), o: x.o, h: x.h, l: x.l, c: x.c, v: x.v, ohlc: true }));
      } catch { this.minutes[id] = []; }
    },
    minuteBars(id, n) {
      const src = this.minutes[id] || []; if (n === 1) return src;
      const out = []; let cur = null, ck = null;
      for (const b of src) {
        const d = new Date(b.t), k = Math.floor(b.t / 864e5) * 10000 + Math.floor((d.getUTCHours() * 60 + d.getUTCMinutes()) / n);
        if (k !== ck) { if (cur) out.push(cur); cur = { ...b }; ck = k; } else { cur.h = Math.max(cur.h, b.h); cur.l = Math.min(cur.l, b.l); cur.c = b.c; cur.v += b.v; }
      }
      if (cur) out.push(cur);
      return out;
    },
    tick(m) {
      const id = m.id, price = m.price; if (!price) return;
      this.quotes[id] = { price, chg: m.chg_pct, at: Date.now(), time: m.time };
      // 일봉: 오늘 봉을 고치거나 새로 붙인다
      const daily = SC.db.cache[id] || (SC.ready(id) && SC.dailyBars(id));
      if (daily && daily.length) {
        const t = dt(m.date, "0000"), last = daily[daily.length - 1];
        if (last.t === t) {
          if (m.open) last.o = m.open; last.h = Math.max(m.high || price, last.ohlc ? last.h : price); last.l = Math.min(m.low || price, last.ohlc ? last.l : price);
          last.c = price; if (m.acc_vol) last.v = m.acc_vol; last.ohlc = true;
        } else if (t > last.t) daily.push({ t, o: m.open || price, h: m.high || price, l: m.low || price, c: price, v: m.acc_vol || 0, ohlc: true });
      }
      // 1분봉
      const mins = this.minutes[id];
      if (mins && m.time) {
        const t = dt(m.date, m.time.slice(0, 4)), last = mins[mins.length - 1];
        if (last && last.t === t) { last.h = Math.max(last.h, price); last.l = Math.min(last.l, price); last.c = price; last.v += m.vol || 0; }
        else if (!last || t > last.t) mins.push({ t, o: price, h: price, l: price, c: price, v: m.vol || 0, ohlc: true });
      }
      this.dirty = this.dirty || new Set(); this.dirty.add(id);
      if (!this._raf) this._raf = setTimeout(() => this.flush(), 250);
    },
    flush() {
      this._raf = null; const ids = this.dirty; this.dirty = null; if (!ids) return;
      const a = this.app;
      for (const c of a.charts) if (ids.has(c.cfg.id) || c.cfg.compare.some(x => ids.has(x.id))) c.liveUpdate();
      a.renderWatchlist();
      for (const id of ids) a.evalAlerts({ id });
    },
    badge(id) {
      if (!this.on || !/^(kr|us):/.test(id)) return null;
      const q = this.quotes[id];
      if (q && Date.now() - q.at < 90e3) return { cls: "on", label: "실시간", title: `한투 실시간 · ${fmtTime(q.time)}` };
      if (this.subscribed.has(id)) return { cls: "idle", label: "대기", title: "구독 중 · 장이 열리면 체결가가 들어옵니다" };
      return null;
    },
  };
  // YYYYMMDD + HHMM → 그 시장 현지 시각을 UTC 숫자로 (화면 표시는 UTC 기준으로 읽는다)
  function dt(d, hm) { return Date.UTC(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6, 8), +(hm || "0000").slice(0, 2), +(hm || "0000").slice(2, 4)); }
  function fmtTime(t) { return t ? `${t.slice(0, 2)}:${t.slice(2, 4)}:${t.slice(4, 6)}` : ""; }
  SC.Live = Live;
})();
