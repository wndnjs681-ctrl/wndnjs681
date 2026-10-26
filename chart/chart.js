// ── 차트 한 칸: 캔버스 두 장(base: 봉·지표·그림 / over: 십자선) ──
"use strict";
(function () {
  const AXIS_W = 72, TIME_H = 26;
  const STYLES = { candle: "캔들", hollow: "속 빈 캔들", bars: "바 (OHLC)", heikin: "하이킨아시", line: "라인", area: "영역", baseline: "베이스라인" };
  SC.STYLES = STYLES;
  SC.defaultInds = () => [
    { uid: SC.uid(), type: "ma_ribbon", params: SC.indDefaults("ma_ribbon"), colors: [], hidden: false },
    { uid: SC.uid(), type: "vol", params: SC.indDefaults("vol"), colors: [], hidden: false },
    { uid: SC.uid(), type: "rsi", params: SC.indDefaults("rsi"), colors: [], hidden: false },
  ];

  class Chart {
    constructor(app, host, cfg) {
      this.app = app;
      this.cfg = Object.assign({ id: "kr:005930", interval: "D", style: "candle", scale: "normal", inds: SC.defaultInds(), compare: [] }, cfg || {});
      this.bars = []; this.raw = []; this.daily = null; this.calc = [];
      this.end = 0; this.count = 120; this.manual = null; this.hover = null; this.syncT = null;
      this.sel = null; this.pending = null; this.drag = null; this.replay = null; this.replayPick = false;
      this.W = 0; this.H = 0; this.DPR = 1;
      this.buildDom(host);
    }

    // ── DOM ──
    buildDom(host) {
      const E = SC.el;
      this.base = E("canvas", { "aria-hidden": "true" });
      this.over = E("canvas", { class: "over", role: "img", "aria-label": "가격 차트", tabindex: "0" });
      this.plot = E("div", { class: "plot" }, this.base, this.over);
      this.legend = E("div", { class: "legend" });
      this.lowerBox = E("div", { class: "lower-labels" });
      this.selBar = E("div", { class: "selbar", hidden: true });
      this.replayBar = E("div", { class: "replaybar", hidden: true });
      this.hint = E("div", { class: "hint", hidden: true });
      this.status = E("div", { class: "status" }, "데이터를 불러오는 중…");
      this.autoBtn = E("button", { type: "button", class: "auto-btn", title: "가격축 자동 맞춤", onclick: () => { this.manual = null; this.draw(); } }, "A");
      this.el = E("div", { class: "cell" }, this.plot, this.legend, this.lowerBox, this.selBar, this.replayBar, this.hint, this.autoBtn, this.status);
      host.append(this.el);
      this.bctx = this.base.getContext("2d"); this.octx = this.over.getContext("2d");
      this.el.addEventListener("pointerdown", () => this.app.setActive(this), true);
      new ResizeObserver(() => this.resize()).observe(this.plot);
      this.bindEvents();
    }
    destroy() { this.stopReplay(); this.el.remove(); }
    resize() {
      const r = this.plot.getBoundingClientRect();
      this.DPR = window.devicePixelRatio || 1; this.W = r.width; this.H = r.height;
      for (const c of [this.base, this.over]) { c.width = Math.max(1, Math.round(this.W * this.DPR)); c.height = Math.max(1, Math.round(this.H * this.DPR)); }
      this.draw();
    }
    get plotW() { return this.W - AXIS_W; }
    get plotH() { return this.H - TIME_H; }

    // ── 종목·데이터 ──
    async setSymbol(id, keepView) {
      this.cfg.id = id; this.status.hidden = false; this.status.textContent = "데이터를 불러오는 중…";
      try { await SC.loadMarket(SC.mkOf(id)); } catch (e) { this.showError(e); return; }
      const d = SC.dailyBars(id);
      if (!d) { this.status.textContent = `${id.split(":")[1]} 데이터가 없습니다. 다른 종목을 검색해 보세요.`; return; }
      this.daily = d; this.status.hidden = true; this.sel = null; this.manual = null;
      for (const c of this.cfg.compare) SC.loadMarket(SC.mkOf(c.id)).then(() => this.rebuild()).catch(() => {});
      this.rebuild(!keepView);
      this.app.onChartChange(this);
    }
    showError(e) {
      this.status.hidden = false;
      this.status.replaceChildren(SC.el("div", null,
        SC.el("p", { class: "st-title" }, "데이터 파일을 읽지 못했습니다"),
        SC.el("p", null, "레포 맨 위 폴더에서 ", SC.el("code", null, "python3 realtime_server.py"), " 또는 ", SC.el("code", null, "python3 -m http.server"), " 를 실행한 뒤"),
        SC.el("p", null, SC.el("code", null, "http://localhost:8000/chart.html"), " 로 열어주세요."),
        SC.el("p", { class: "st-sub" }, String((e && e.message) || e))));
    }
    // 실시간 틱이 들어오면 app 이 daily 를 고친 뒤 부른다
    liveUpdate() {
      if (!this.daily) return;
      const atEnd = this.end >= this.raw.length - 1;
      this.rebuild(false);
      if (atEnd) { this.end = this.raw.length - 1 + Math.max(3, this.count * 0.05); this.draw(); }
    }
    rebuild(resetView) {
      if (!this.daily) return;
      const iv = this.cfg.interval;
      let d = this.app.barsFor(this.cfg.id, iv);
      if (this.replay) d = d.slice(0, this.replay.idx + 1);
      this.raw = SC.resample(d, SC.isMinute(iv) ? "D" : iv);
      this.bars = this.cfg.style === "heikin" ? SC.heikin(this.raw) : this.raw;
      this.calc = this.cfg.inds.map(ind => {
        const def = SC.IND[ind.type]; let vals = [];
        try { vals = def.calc(this.raw, ind.params); } catch (e) { console.warn(ind.type, e); }
        return { ind, def, vals };
      });
      this.cmp = this.cfg.compare.map(c => {
        let cb = this.app.barsFor(c.id, iv); if (!cb) return { c, vals: [] };
        cb = SC.resample(cb, SC.isMinute(iv) ? "D" : iv);
        const R = this.raw, vals = R.map((b, i) => { const lim = i + 1 < R.length ? R[i + 1].t - 1 : (this.replay ? b.t + 864e5 * 6 : Infinity); const j = SC.asof(cb, lim); return j >= 0 ? cb[j].c : null; });
        return { c, vals };
      });
      if (resetView) this.resetView(); else this.clampView();
      this.draw();
    }

    // ── 시간 ↔ 위치 ──
    step() { const B = this.raw, n = B.length; if (n < 2) return 864e5; const k = Math.min(20, n - 1); return (B[n - 1].t - B[n - 1 - k].t) / k; }
    tToI(t) {
      const B = this.raw, n = B.length; if (!n) return 0;
      if (t <= B[0].t) return (t - B[0].t) / this.step();
      if (t >= B[n - 1].t) return n - 1 + (t - B[n - 1].t) / this.step();
      const j = SC.asof(B, t); const a = B[j], b = B[j + 1];
      return j + (t - a.t) / (b.t - a.t);
    }
    iToT(i) {
      const B = this.raw, n = B.length; if (!n) return 0;
      if (i <= 0) return B[0].t + i * this.step();
      if (i >= n - 1) return B[n - 1].t + (i - (n - 1)) * this.step();
      const j = Math.floor(i), f = i - j; return B[j].t + f * (B[j + 1].t - B[j].t);
    }
    bw() { return this.plotW / this.count; }
    x(i) { return this.plotW - (this.end - i + 0.5) * this.bw(); }
    iAt(x) { return this.end - (this.plotW - x) / this.bw() + 0.5; }
    visible() { const n = this.bars.length; return [Math.max(0, Math.floor(this.end - this.count + 1)), Math.min(n - 1, Math.ceil(this.end))]; }
    clampView() {
      const n = this.raw.length;
      this.count = SC.clamp(this.count, 8, Math.max(n + 40, 40));
      this.end = SC.clamp(this.end, Math.min(n - 1, this.count * 0.2), n - 1 + this.count * 0.6);
    }
    resetView() { const n = this.raw.length; this.count = Math.min(n + 6, SC.isMinute(this.cfg.interval) ? 200 : this.cfg.interval === "D" ? 140 : 120); this.end = n - 1 + Math.max(3, this.count * 0.05); this.manual = null; this.clampView(); }

    // ── 가격 ↔ 화면 (일반 / 로그 / 퍼센트) ──
    scaleMode() { return this.cfg.compare.length ? "percent" : this.cfg.scale; }
    fwd(p) { const m = this.scaleMode(); if (m === "log") return Math.log(Math.max(p, 1e-9)); if (m === "percent") return (p / this.pbase - 1) * 100; return p; }
    inv(v) { const m = this.scaleMode(); if (m === "log") return Math.exp(v); if (m === "percent") return this.pbase * (1 + v / 100); return v; }
    xy(pt) { const m = this.panes && this.panes.main; return { x: this.x(this.tToI(pt.t)), y: m ? m.y(pt.p) : 0 }; }
    pt(x, y) { const m = this.panes.main; return { t: this.iToT(this.iAt(x) - 0.5), p: m.inv(y) }; }
    fmt(p) { return SC.fmtPrice(p, this.cfg.id); }

    layout() {
      const lows = this.calc.filter(c => c.def.pane === "lower"), plotH = this.plotH;
      const h = lows.length ? SC.clamp(plotH * (lows.length > 2 ? 0.16 : 0.2), 56, 150) : 0;
      const mainH = Math.max(plotH * 0.35, plotH - h * lows.length);
      const each = lows.length ? (plotH - mainH) / lows.length : 0;
      const panes = [{ id: "main", y: 0, h: mainH }];
      let y = mainH; for (const c of lows) { panes.push({ id: c.ind.uid, y, h: each, c }); y += each; }
      return panes;
    }

    // ── 그리기 ──
    draw() {
      if (!this.W || !this.bars.length) { this.drawOver(); return; }
      if (!SC.colors.ink) SC.readColors();
      const ctx = this.bctx, C = SC.colors, W = this.W, plotW = this.plotW, plotH = this.plotH;
      ctx.setTransform(this.DPR, 0, 0, this.DPR, 0, 0); ctx.clearRect(0, 0, W, this.H);
      ctx.font = `11px ${SC.fontNum}`; ctx.textBaseline = "middle";
      const B = this.bars, [i0, i1] = this.visible(), style = this.cfg.style;
      this.layoutPanes = this.layout(); this.panes = {};
      const mp = this.layoutPanes[0];
      const hasVol = this.calc.some(c => c.def.pane === "volume" && !c.ind.hidden);
      this.pbase = B[Math.max(0, i0)] ? B[Math.max(0, i0)].c : 1;
      const mode = this.scaleMode();

      // 범위
      let lo = Infinity, hi = -Infinity;
      const acc = v => { if (v == null || !isFinite(v) || (mode === "log" && v <= 0)) return; const f = this.fwd(v); if (f < lo) lo = f; if (f > hi) hi = f; };
      const lineLike = style === "line" || style === "area" || style === "baseline";
      for (let i = i0; i <= i1; i++) { const b = B[i]; if (!b) continue; if (lineLike || !b.ohlc) acc(b.c); else { acc(b.h); acc(b.l); } }
      for (const c of this.calc) if (c.def.pane === "main" && !c.ind.hidden) c.def.plots.forEach((pl, k) => { const off = SC.plotOffset(pl, c.ind.params), v = c.vals[k]; if (!v) return; for (let i = i0; i <= i1; i++) acc(v[i - off]); });
      if (mode === "percent") for (const s of this.cmp) { const base = this.cmpBase(s, i0); for (let i = i0; i <= i1; i++) if (s.vals[i] != null && base) { const f = (s.vals[i] / base - 1) * 100; lo = Math.min(lo, f); hi = Math.max(hi, f); } }
      if (!isFinite(lo)) { lo = 0; hi = 1; }
      if (hi === lo) { hi += Math.abs(hi) * 0.01 || 1; lo -= Math.abs(lo) * 0.01 || 1; }
      const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
      if (this.manual) ({ lo, hi } = this.manual);
      this.range = { lo, hi };
      const top = mp.y + 14, bot = mp.y + mp.h - (hasVol ? mp.h * 0.2 : 10);
      const Y = p => bot - (this.fwd(p) - lo) / (hi - lo) * (bot - top);
      const Yf = f => bot - (f - lo) / (hi - lo) * (bot - top);
      this.panes.main = { y: Y, yf: Yf, inv: y => this.inv(lo + (bot - y) / (bot - top) * (hi - lo)), invf: y => lo + (bot - y) / (bot - top) * (hi - lo), top, bot, pane: mp };

      // 격자 + 가격 눈금
      ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
      const ticks = this.priceTicks(lo, hi, top, bot);
      ctx.fillStyle = C["ink-3"]; ctx.textAlign = "left";
      ctx.save(); ctx.beginPath(); ctx.rect(plotW, mp.y, AXIS_W, mp.h); ctx.clip();
      for (const t of ticks) { const y = Math.round(Yf(t.f)) + 0.5; line(ctx, 0, y, plotW, y); ctx.fillText(t.label, plotW + 8, y); }
      ctx.restore();
      for (const t of ticks) { const y = Math.round(Yf(t.f)) + 0.5; if (y > mp.y && y < mp.y + mp.h) line(ctx, 0, y, plotW, y); }
      const tt = this.timeTicks(i0, i1);
      for (const k of tt) { const x = Math.round(this.x(k.i)) + 0.5; line(ctx, x, 0, x, plotH); }

      // 가격 창 내용은 창 밖으로 안 나가게
      ctx.save(); ctx.beginPath(); ctx.rect(0, mp.y, plotW, mp.h); ctx.clip();
      // 지표 채움 (봉 아래)
      for (const c of this.calc) if (c.def.pane === "main" && !c.ind.hidden) this.drawFills(ctx, c, Y, i0, i1);
      // 거래량
      for (const c of this.calc) if (c.def.pane === "volume" && !c.ind.hidden) this.drawVolume(ctx, c, mp, i0, i1);
      // 봉
      this.drawSeries(ctx, Y, i0, i1, top, bot);
      // 비교 종목
      this.cmp.forEach(s => { const base = this.cmpBase(s, i0); if (!base) return; this.polyline(ctx, i0, i1, i => (s.vals[i] == null ? null : Yf((s.vals[i] / base - 1) * 100)), SC.color(s.c.color), 1.75); });
      // 지표 선
      for (const c of this.calc) if (c.def.pane === "main" && !c.ind.hidden) this.drawPlots(ctx, c, Y, i0, i1);
      // 알림선
      const axisTags = [];
      for (const a of this.app.alertsFor(this.cfg.id)) {
        const y = Y(a.price); ctx.setLineDash([6, 4]); ctx.strokeStyle = a.hit ? C["ink-3"] : C.c4; ctx.lineWidth = 1; line(ctx, 0, Math.round(y) + 0.5, plotW, Math.round(y) + 0.5); ctx.setLineDash([]);
        ctx.fillStyle = a.hit ? C["ink-3"] : C.c4; ctx.textAlign = "left"; ctx.fillText(a.hit ? `알림 도달 ${SC.fmtDate(a.hit, "D")}` : "알림", 8, y - 8);
        axisTags.push({ y, text: this.fmt(a.price), color: a.hit ? C["ink-3"] : C.c4 });
      }
      // 그림
      const drawings = this.app.drawingsFor(this.cfg.id);
      if (!this.app.hideDrawings) for (const d of drawings.concat(this.pending ? [this.pending] : [])) {
        if (d.type !== "vline" && d.pts.some(p => !isFinite(this.tToI(p.t)))) continue;
        axisTags.push(...SC.Draw.draw(ctx, d, this, this.sel === d.id || this.pending === d));
      }
      ctx.restore();

      // 마지막 가격
      const last = B[B.length - 1], prev = B[B.length - 2];
      if (last) {
        const up = prev ? last.c >= prev.c : true, col = up ? C.up : C.down, y = Y(last.c);
        if (y > mp.y && y < mp.y + mp.h) {
          ctx.setLineDash([2, 3]); ctx.strokeStyle = col; ctx.lineWidth = 1; line(ctx, 0, Math.round(y) + 0.5, plotW, Math.round(y) + 0.5); ctx.setLineDash([]);
          axisTags.push({ y, text: mode === "percent" ? SC.fmtPct(this.fwd(last.c)) : this.fmt(last.c), color: col, strong: true });
        }
      }
      for (const s of this.cmp) { const base = this.cmpBase(s, i0), v = s.vals[s.vals.length - 1]; if (base && v != null) axisTags.push({ y: Yf((v / base - 1) * 100), text: SC.fmtPct((v / base - 1) * 100), color: SC.color(s.c.color) }); }
      for (const t of axisTags) if (t.y > mp.y && t.y < mp.y + mp.h) tag(ctx, plotW, t.y, t.text, t.color, "#ffffff");

      // 아래 창
      for (const p of this.layoutPanes.slice(1)) this.drawLower(ctx, p, i0, i1);

      // 테두리 + 날짜
      ctx.strokeStyle = C.line; ctx.lineWidth = 1;
      line(ctx, Math.round(plotW) + 0.5, 0, Math.round(plotW) + 0.5, this.H);
      line(ctx, 0, Math.round(plotH) + 0.5, W, Math.round(plotH) + 0.5);
      ctx.fillStyle = C["ink-3"]; ctx.textAlign = "center";
      for (const k of tt) { const x = this.x(k.i); if (x < 24 || x > plotW - 24) continue; ctx.fillText(k.label, x, plotH + TIME_H / 2); }

      this.autoBtn.classList.toggle("on", !this.manual);
      this.renderLegend(); this.renderLowerLabels(); this.renderSelBar(); this.drawOver();
    }
    cmpBase(s, i0) { for (let i = Math.max(0, i0); i < s.vals.length; i++) if (s.vals[i] != null) return s.vals[i]; return null; }

    priceTicks(lo, hi, top, bot) {
      const mode = this.scaleMode(), target = Math.max(3, Math.round((bot - top) / 56));
      if (mode === "percent") return SC.niceTicks(lo, hi, target).map(f => ({ f, label: SC.fmtPct(f).replace(".00", "") }));
      if (mode === "log") {
        const out = [], ps = SC.niceTicks(Math.exp(lo), Math.exp(hi), target * 2); let lastY = Infinity;
        for (const p of ps) { if (p <= 0) continue; const f = Math.log(p), y = bot - (f - lo) / (hi - lo) * (bot - top); if (lastY - y >= 24) { out.push({ f, label: this.fmt(p) }); lastY = y; } }
        return out;
      }
      return SC.niceTicks(lo, hi, target).map(f => ({ f, label: this.fmt(f) }));
    }
    timeTicks(i0, i1) {
      const out = [], B = this.bars, iv = this.cfg.interval; let lastX = -1e9, prev = null;
      const minute = SC.isMinute(iv), hrStep = minute ? (+iv >= 30 ? 6 : +iv >= 5 ? 2 : 1) : 0;
      let prevDay = null;
      for (let i = i0; i <= i1; i++) {
        const d = new Date(B[i].t), y = d.getUTCFullYear(), m = d.getUTCMonth(), day = Math.floor(B[i].t / 864e5);
        const key = minute ? day * 24 + Math.floor(d.getUTCHours() / hrStep) : iv === "M" ? y : iv === "W" ? y * 4 + Math.floor(m / 3) : y * 12 + m;
        if (prev != null && key !== prev) {
          const x = this.x(i);
          if (x - lastX >= 64) {
            const label = minute ? (day !== prevDay ? `${m + 1}/${d.getUTCDate()}` : `${String(d.getUTCHours()).padStart(2, "0")}:00`) : iv === "M" || m === 0 ? String(y) : `${m + 1}월`;
            out.push({ i, label }); lastX = x;
          }
        }
        prev = key; prevDay = day;
      }
      return out;
    }
    polyline(ctx, i0, i1, yOf, color, width, dash) {
      ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = "round"; if (dash) ctx.setLineDash(dash);
      ctx.beginPath(); let s = false;
      for (let i = i0; i <= i1; i++) { const y = yOf(i); if (y == null || !isFinite(y)) { s = false; continue; } const x = this.x(i); s ? ctx.lineTo(x, y) : ctx.moveTo(x, y); s = true; }
      ctx.stroke(); if (dash) ctx.setLineDash([]);
    }
    drawSeries(ctx, Y, i0, i1, top, bot) {
      const C = SC.colors, B = this.bars, style = this.cfg.style, bw = this.bw();
      const w = Math.max(1, Math.min(bw * 0.72, bw - 1.5));
      if (style === "line" || style === "area" || style === "baseline") {
        if (style === "area") {
          const g = ctx.createLinearGradient(0, top, 0, bot); g.addColorStop(0, SC.alpha("c1", 0.28)); g.addColorStop(1, SC.alpha("c1", 0.02));
          ctx.beginPath(); ctx.moveTo(this.x(i0), bot); for (let i = i0; i <= i1; i++) ctx.lineTo(this.x(i), Y(B[i].c)); ctx.lineTo(this.x(i1), bot); ctx.closePath(); ctx.fillStyle = g; ctx.fill();
          this.polyline(ctx, i0, i1, i => Y(B[i].c), C.c1, 2);
        } else if (style === "baseline") {
          const by = Y(this.pbase);
          for (const [c, clipTop] of [[C.up, true], [C.down, false]]) {
            ctx.save(); ctx.beginPath(); clipTop ? ctx.rect(0, 0, this.plotW, by) : ctx.rect(0, by, this.plotW, this.H); ctx.clip();
            ctx.beginPath(); ctx.moveTo(this.x(i0), by); for (let i = i0; i <= i1; i++) ctx.lineTo(this.x(i), Y(B[i].c)); ctx.lineTo(this.x(i1), by); ctx.closePath(); ctx.fillStyle = SC.alpha(c, 0.14); ctx.fill();
            this.polyline(ctx, i0, i1, i => Y(B[i].c), c, 2); ctx.restore();
          }
          ctx.setLineDash([4, 4]); ctx.strokeStyle = C["ink-3"]; ctx.lineWidth = 1; line(ctx, 0, by, this.plotW, by); ctx.setLineDash([]);
        } else this.polyline(ctx, i0, i1, i => Y(B[i].c), C.ink, 2);
        return;
      }
      // OHLC 없는 앞 구간은 종가 선
      let firstOhlc = B.findIndex(b => b.ohlc); if (firstOhlc < 0) firstOhlc = B.length;
      if (i0 < firstOhlc) this.polyline(ctx, i0, Math.min(i1, firstOhlc), i => Y(B[i].c), C["ink-2"], 1.5);
      for (let i = Math.max(i0, firstOhlc); i <= i1; i++) {
        const b = B[i]; if (!b) continue;
        const ref = style === "hollow" && B[i - 1] ? B[i - 1].c : b.o;
        const col = b.c > ref ? C.up : b.c < ref ? C.down : C.flat, x = Math.round(this.x(i)) + 0.5;
        ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1;
        if (style === "bars") {
          line(ctx, x, Y(b.h), x, Y(b.l)); const t = Math.max(2, w / 2);
          line(ctx, x - t, Math.round(Y(b.o)) + 0.5, x, Math.round(Y(b.o)) + 0.5); line(ctx, x, Math.round(Y(b.c)) + 0.5, x + t, Math.round(Y(b.c)) + 0.5); continue;
        }
        const y1 = Y(Math.max(b.o, b.c)), y2 = Y(Math.min(b.o, b.c)), bx = Math.round(x - w / 2), bwid = Math.max(1, Math.round(w)), bh = Math.max(1, y2 - y1);
        if (style === "hollow" && b.c >= b.o) {
          line(ctx, x, Y(b.h), x, y1); line(ctx, x, y2, x, Y(b.l));
          ctx.strokeRect(bx + 0.5, y1 + 0.5, Math.max(1, bwid - 1), Math.max(1, bh - 1));
        } else { line(ctx, x, Y(b.h), x, Y(b.l)); ctx.fillRect(bx, y1, bwid, bh); }
      }
    }
    plotColor(c, k) { return SC.color(c.ind.colors[k] || c.def.plots[k].color); }
    drawPlots(ctx, c, Y, i0, i1) {
      c.def.plots.forEach((pl, k) => {
        const v = c.vals[k]; if (!v || pl.type === "hist") return;
        const off = SC.plotOffset(pl, c.ind.params), col = this.plotColor(c, k);
        const a = Math.max(0, i0 - Math.abs(off) - 1), b = Math.min(v.length - 1 + off, i1 + Math.abs(off) + 1);
        if (pl.type === "dots") { ctx.fillStyle = col; for (let i = Math.max(i0, a); i <= Math.min(i1, b); i++) { const val = v[i - off]; if (val == null) continue; ctx.beginPath(); ctx.arc(this.x(i), Y(val), 1.8, 0, 7); ctx.fill(); } return; }
        this.polyline(ctx, a, b, i => (v[i - off] == null ? null : Y(v[i - off])), col, pl.width || 1.5);
      });
    }
    drawFills(ctx, c, Y, i0, i1) {
      for (const f of c.def.fills || []) {
        const A = c.vals[f.a], Bv = c.vals[f.b]; if (!A || !Bv) continue;
        const pa = c.def.plots[f.a], off = SC.plotOffset(pa, c.ind.params);
        const s = Math.max(0, i0 - Math.abs(off) - 1), e = Math.min(A.length - 1 + off, i1 + Math.abs(off) + 1);
        // 같은 부호 구간마다 다각형
        let seg = [];
        const flush = () => {
          if (seg.length > 1) {
            const up = seg[0].a >= seg[0].b;
            ctx.fillStyle = f.cloud ? SC.alpha(up ? "c3" : "c8", f.alpha) : SC.alpha(SC.toHex(c.ind.colors[f.a] || f.color || c.def.plots[f.a].color), f.alpha || 0.08);
            ctx.beginPath(); seg.forEach((p, j) => (j ? ctx.lineTo(p.x, Y(p.a)) : ctx.moveTo(p.x, Y(p.a))));
            for (let j = seg.length - 1; j >= 0; j--) ctx.lineTo(seg[j].x, Y(seg[j].b));
            ctx.closePath(); ctx.fill();
          }
          seg = [];
        };
        for (let i = s; i <= e; i++) {
          const a = A[i - off], b = Bv[i - off];
          if (a == null || b == null) { flush(); continue; }
          if (f.cloud && seg.length && (seg[seg.length - 1].a >= seg[seg.length - 1].b) !== (a >= b)) { const last = seg[seg.length - 1]; flush(); seg.push(last); }
          seg.push({ x: this.x(i), a, b });
        }
        flush();
      }
    }
    drawVolume(ctx, c, mp, i0, i1) {
      const B = this.raw, v = c.vals[0], ma = c.vals[1]; let max = 0;
      for (let i = i0; i <= i1; i++) if (v[i] > max) max = v[i];
      const vb = mp.y + mp.h - 2, vh = mp.h * 0.18, Y = x => vb - (x / (max || 1)) * vh, w = Math.max(1, this.bw() * 0.72), C = SC.colors;
      for (let i = i0; i <= i1; i++) {
        const b = B[i], p = B[i - 1], dir = b.ohlc ? b.c - b.o : p ? b.c - p.c : 0;
        ctx.fillStyle = SC.alpha(dir >= 0 ? C.up : C.down, 0.3); const y = Y(v[i]); ctx.fillRect(this.x(i) - w / 2, y, w, vb - y);
      }
      this.polyline(ctx, i0, i1, i => (ma[i] == null ? null : Y(ma[i])), this.plotColor(c, 1), 1.25);
      this.volY = { Y, max, vb, vh };
    }
    drawLower(ctx, p, i0, i1) {
      const C = SC.colors, c = p.c, def = c.def, t = p.y + 20, b = p.y + p.h - 6, plotW = this.plotW;
      ctx.strokeStyle = C.line; ctx.lineWidth = 1; line(ctx, 0, Math.round(p.y) + 0.5, this.W, Math.round(p.y) + 0.5);
      let lo = Infinity, hi = -Infinity;
      c.vals.forEach(v => { if (!v) return; for (let i = i0; i <= i1; i++) { const x = v[i]; if (x == null || !isFinite(x)) continue; if (x < lo) lo = x; if (x > hi) hi = x; } });
      for (const L of def.levels || []) { lo = Math.min(lo, L); hi = Math.max(hi, L); }
      if (def.range) { if (def.range[0] != null) lo = def.range[0]; if (def.range[1] != null) hi = def.range[1]; }
      if (!isFinite(lo)) { lo = 0; hi = 1; }
      if (hi === lo) { hi += 1; lo -= 1; }
      if (!def.range || def.range[1] == null) { const pd = (hi - lo) * 0.08; hi += pd; if (!def.range) lo -= pd; }
      const Y = v => b - (v - lo) / (hi - lo) * (b - t);
      this.panes[c.ind.uid] = { y: Y, inv: y => lo + (b - y) / (b - t) * (hi - lo), top: t, bot: b, pane: p };
      ctx.save(); ctx.beginPath(); ctx.rect(0, p.y, plotW, p.h); ctx.clip();
      if (def.band) { ctx.fillStyle = SC.alpha("ink-3", 0.08); ctx.fillRect(0, Y(def.band[1]), plotW, Y(def.band[0]) - Y(def.band[1])); }
      ctx.setLineDash([3, 3]); ctx.strokeStyle = C["ink-3"];
      for (const L of def.levels || []) { const y = Math.round(Y(L)) + 0.5; line(ctx, 0, y, plotW, y); }
      ctx.setLineDash([]);
      if (!c.ind.hidden) def.plots.forEach((pl, k) => {
        const v = c.vals[k]; if (!v) return;
        if (pl.type === "hist") {
          const z = Y(Math.max(lo, Math.min(hi, 0))), w = Math.max(1, this.bw() * 0.6);
          for (let i = i0; i <= i1; i++) { if (v[i] == null) continue; const y = Y(v[i]); ctx.fillStyle = pl.color === "updown" ? SC.alpha(v[i] >= 0 ? C.up : C.down, 0.55) : SC.alpha(this.plotColor(c, k), 0.5); ctx.fillRect(this.x(i) - w / 2, Math.min(y, z), w, Math.abs(z - y)); }
        } else if (pl.type === "area") {
          const z = Y(0);
          for (const [col, above] of [[C.up, true], [C.down, false]]) {
            ctx.save(); ctx.beginPath(); above ? ctx.rect(0, p.y, plotW, z - p.y) : ctx.rect(0, z, plotW, p.y + p.h - z); ctx.clip();
            ctx.beginPath(); let s = false;
            for (let i = i0; i <= i1; i++) { if (v[i] == null) continue; const x = this.x(i); if (!s) { ctx.moveTo(x, z); s = true; } ctx.lineTo(x, Y(v[i])); }
            ctx.lineTo(this.x(i1), z); ctx.closePath(); ctx.fillStyle = SC.alpha(col, 0.25); ctx.fill();
            this.polyline(ctx, i0, i1, i => (v[i] == null ? null : Y(v[i])), col, 1.25); ctx.restore();
          }
        } else this.polyline(ctx, i0, i1, i => (v[i] == null ? null : Y(v[i])), this.plotColor(c, k), pl.width || 1.5);
      });
      ctx.restore();
      ctx.fillStyle = C["ink-3"]; ctx.textAlign = "left";
      const ticks = def.levels && def.levels.length ? def.levels : SC.niceTicks(lo, hi, 3);
      for (const L of ticks) { const y = Y(L); if (y > p.y + 8 && y < p.y + p.h - 4) ctx.fillText(fmtShort(L), plotW + 8, y); }
      let lk = def.plots.findIndex(pl => pl.type !== "hist" && pl.type !== "area"); if (lk < 0) lk = 0;
      const lv = c.vals[lk], lastV = lv && lv[Math.min(i1, lv.length - 1)];
      if (lastV != null && !c.ind.hidden) tag(ctx, plotW, SC.clamp(Y(lastV), p.y + 10, p.y + p.h - 10), fmtShort(lastV), def.plots[lk].color === "updown" ? SC.colors[lastV >= 0 ? "up" : "down"] : this.plotColor(c, lk), "#ffffff");
    }

    // ── 위 캔버스: 십자선·동기 커서·리플레이 선택 ──
    drawOver() {
      const ctx = this.octx; ctx.setTransform(this.DPR, 0, 0, this.DPR, 0, 0); ctx.clearRect(0, 0, this.W, this.H);
      if (!this.bars.length || !this.panes) return;
      const C = SC.colors; ctx.font = `11px ${SC.fontNum}`; ctx.textBaseline = "middle";
      if (this.syncT != null && !this.hover) {
        const x = Math.round(this.x(this.tToI(this.syncT))) + 0.5; ctx.strokeStyle = C.cross; ctx.setLineDash([2, 4]); line(ctx, x, 0, x, this.plotH); ctx.setLineDash([]);
      }
      const h = this.hover; if (!h) return;
      const i = SC.clamp(Math.round(this.iAt(h.x) - 0.5), 0, this.bars.length - 1), x = Math.round(this.x(i)) + 0.5;
      ctx.strokeStyle = this.replayPick ? C.c1 : C.cross; ctx.lineWidth = this.replayPick ? 2 : 1;
      if (!this.replayPick) ctx.setLineDash([4, 4]);
      line(ctx, x, 0, x, this.plotH);
      if (h.y < this.plotH && !this.replayPick) line(ctx, 0, Math.round(h.y) + 0.5, this.plotW, Math.round(h.y) + 0.5);
      ctx.setLineDash([]); ctx.lineWidth = 1;
      if (h.y < this.plotH) {
        const pane = this.layoutPanes.find(p => h.y >= p.y && h.y < p.y + p.h), ps = pane && this.panes[pane.id];
        if (ps) {
          let txt;
          if (pane.id === "main") txt = this.scaleMode() === "percent" ? SC.fmtPct(ps.invf(h.y)) : this.fmt(ps.inv(h.y));
          else txt = fmtShort(ps.inv(h.y));
          tag(ctx, this.plotW, h.y, txt, C.tag, C["tag-ink"]);
        }
      }
      const label = SC.fmtDate(this.bars[i].t, this.cfg.interval), tw = ctx.measureText(label).width + 14, lx = SC.clamp(x - tw / 2, 0, this.plotW - tw);
      ctx.fillStyle = C.tag; roundRect(ctx, lx, this.plotH + 4, tw, 18, 3); ctx.fill();
      ctx.fillStyle = C["tag-ink"]; ctx.textAlign = "center"; ctx.fillText(label, lx + tw / 2, this.plotH + 13.5);
    }

    // ── 범례 ──
    renderLegend() {
      const E = SC.el, B = this.bars; if (!B.length) return;
      const i = this.hover ? SC.clamp(Math.round(this.iAt(this.hover.x) - 0.5), 0, B.length - 1) : B.length - 1;
      const b = B[i], p = B[i - 1], chg = p ? (b.c / p.c - 1) * 100 : null, [mk, t] = this.cfg.id.split(":");
      const rows = [];
      const live = this.app.liveState(this.cfg.id);
      rows.push(E("div", { class: "lg-head" },
        E("button", { type: "button", class: "lg-sym", title: "종목 바꾸기", onclick: () => this.app.openSearch(this) }, SC.nameOf(this.cfg.id)),
        E("span", { class: "lg-meta" }, `${t} · ${SC.MK[mk]} · ${SC.ivName(this.cfg.interval)}`),
        live ? E("span", { class: "live-dot " + live.cls, title: live.title }, live.label) : null,
        this.replay ? E("span", { class: "pill" }, "리플레이") : null));
      const o = E("div", { class: "lg-ohlc num" });
      if (b.ohlc) for (const [k, v] of [["시", b.o], ["고", b.h], ["저", b.l]]) o.append(E("span", null, k + " ", E("b", { class: SC.cls(b.c - b.o) }, this.fmt(v))));
      o.append(E("span", null, "종 ", E("b", { class: SC.cls(chg) }, this.fmt(b.c))), E("span", { class: SC.cls(chg) }, (chg != null && p ? (b.c - p.c >= 0 ? "+" : "") + this.fmt(b.c - p.c) + " " : "") + SC.fmtPct(chg)));
      o.append(E("span", { class: "lg-date" }, SC.fmtDate(b.t, this.cfg.interval)));
      rows.push(o);
      for (const s of this.cmp) {
        const v = s.vals[i], base = this.cmpBase(s, this.visible()[0]);
        rows.push(this.legendRow(E("span", { class: "sw", style: `background:${SC.color(s.c.color)}` }), SC.nameOf(s.c.id), base && v != null ? SC.fmtPct((v / base - 1) * 100) : "—", null, () => { this.cfg.compare = this.cfg.compare.filter(x => x !== s.c); this.rebuild(); this.app.save(); }, null));
      }
      for (const c of this.calc) {
        if (c.def.pane === "lower") continue;
        const vals = c.def.plots.map((pl, k) => { if (!c.vals[k] || pl.type === "hist" && c.def.pane !== "volume") return null; const off = SC.plotOffset(pl, c.ind.params), v = c.vals[k][i - off]; return E("b", { style: `color:${this.plotColor(c, k)}` }, c.def.pane === "volume" ? SC.fmtVol(v) : this.fmt(v)); }).filter(Boolean);
        rows.push(this.legendRow(null, SC.indLabel(c.ind.type, c.ind.params), vals, c));
      }
      this.legend.replaceChildren(...rows);
    }
    legendRow(sw, name, vals, c, onRemove) {
      const E = SC.el;
      const btn = (icon, label, fn) => E("button", { type: "button", class: "lg-btn", title: label, "aria-label": label, onclick: e => { e.stopPropagation(); fn(); }, html: icon });
      const actions = E("span", { class: "lg-acts" },
        c ? btn(c.ind.hidden ? EYE_OFF : EYE, c.ind.hidden ? "보이기" : "숨기기", () => { c.ind.hidden = !c.ind.hidden; this.draw(); this.app.save(); }) : null,
        c ? btn(GEAR, "설정", () => this.app.openIndSettings(this, c.ind)) : null,
        btn(XICON, "삭제", onRemove || (() => this.removeInd(c.ind))));
      return E("div", { class: "lg-row" + (c && c.ind.hidden ? " off" : "") }, sw, E("span", { class: "lg-name" }, name), E("span", { class: "lg-vals num" }, vals), actions);
    }
    renderLowerLabels() {
      const E = SC.el, B = this.bars; const rows = [];
      const i = this.hover ? SC.clamp(Math.round(this.iAt(this.hover.x) - 0.5), 0, B.length - 1) : B.length - 1;
      for (const p of this.layoutPanes.slice(1)) {
        const c = p.c;
        const vals = c.def.plots.map((pl, k) => (c.vals[k] ? E("b", { style: `color:${pl.color === "updown" ? SC.colors[(c.vals[k][i] || 0) >= 0 ? "up" : "down"] : this.plotColor(c, k)}` }, fmtShort(c.vals[k][i])) : null)).filter(Boolean);
        const row = this.legendRow(null, SC.indLabel(c.ind.type, c.ind.params), vals, c);
        row.style.top = p.y + 3 + "px"; rows.push(row);
      }
      this.lowerBox.replaceChildren(...rows);
    }
    removeInd(ind) { this.cfg.inds = this.cfg.inds.filter(x => x !== ind); this.rebuild(); this.app.save(); }

    // ── 선택한 그림의 떠 있는 도구줄 ──
    renderSelBar() {
      const d = this.sel && this.app.drawingsFor(this.cfg.id).find(x => x.id === this.sel);
      if (!d) { this.selBar.hidden = true; return; }
      const E = SC.el, app = this.app, id = this.cfg.id;
      const swatches = ["c1", "c2", "c3", "c4", "c5", "c7", "c8", "ink"].map(k => E("button", { type: "button", class: "swatch" + (d.color === k ? " on" : ""), style: `background:var(--${k})`, "aria-label": "색 " + k, onclick: () => { app.pushUndo(id); d.color = k; app.saveDrawings(id); this.draw(); } }));
      const widths = [1, 2, 3].map(w => E("button", { type: "button", class: "wbtn" + ((d.width || 2) === w ? " on" : ""), "aria-label": `굵기 ${w}`, onclick: () => { app.pushUndo(id); d.width = w; app.saveDrawings(id); this.draw(); } }, E("i", { style: `height:${w}px` })));
      this.selBar.replaceChildren(...[
        E("span", { class: "sb-name" }, SC.TOOLS[d.type].name), ...swatches, E("span", { class: "sb-sep" }), ...widths, E("span", { class: "sb-sep" }),
        d.type === "text" ? E("button", { type: "button", class: "sb-btn", onclick: () => this.editText(d) }, "글자 고치기") : null,
        d.type === "hline" || d.type === "hray" ? E("button", { type: "button", class: "sb-btn", onclick: () => { app.addAlert(id, d.pts[0].p); } }, "이 가격에 알림") : null,
        E("button", { type: "button", class: "sb-btn" + (d.locked ? " on" : ""), onclick: () => { d.locked = !d.locked; app.saveDrawings(id); this.renderSelBar(); } }, d.locked ? "잠김" : "잠그기"),
        E("button", { type: "button", class: "sb-btn danger", onclick: () => this.deleteSelected() }, "삭제")].filter(Boolean));
      this.selBar.hidden = false;
    }
    deleteSelected() {
      if (!this.sel) return;
      const id = this.cfg.id; this.app.pushUndo(id);
      this.app.setDrawings(id, this.app.drawingsFor(id).filter(d => d.id !== this.sel));
      this.sel = null; this.app.redrawSymbol(id);
    }
    editText(d, isNew) {
      const host = this.el, p = this.xy(d.pts[0]);
      const ta = SC.el("textarea", { class: "text-edit", rows: "2", "aria-label": "메모 내용", style: `left:${p.x}px;top:${p.y - 10}px` });
      ta.value = d.text || "";
      const id = this.cfg.id;
      const done = keep => {
        ta.remove();
        const v = ta.value.trim();
        if (!keep || !v) { if (isNew || !v) this.app.setDrawings(id, this.app.drawingsFor(id).filter(x => x !== d)); }
        else { if (!isNew) this.app.pushUndo(id); d.text = v; }
        this.app.saveDrawings(id); this.app.redrawSymbol(id);
      };
      ta.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); done(true); } else if (e.key === "Escape") done(!isNew); });
      ta.addEventListener("blur", () => ta.isConnected && done(true));
      host.append(ta); ta.focus();
    }

    // ── 리플레이 ──
    startReplayPick() { this.replayPick = true; this.showHint("리플레이를 시작할 봉을 클릭하세요 · Esc 취소"); this.drawOver(); }
    startReplay(t) {
      const daily = this.app.barsFor(this.cfg.id, "D");
      const idx = SC.clamp(SC.asof(daily, t), 10, daily.length - 1);
      this.replay = { idx, playing: false, speed: 1, timer: null };
      this.replayPick = false; this.hideHint(); this.renderReplayBar(); this.rebuild(false);
      this.end = this.raw.length - 1 + this.count * 0.3; this.clampView(); this.draw();
    }
    stepReplay() {
      const daily = this.app.barsFor(this.cfg.id, "D");
      if (!this.replay) return;
      if (this.replay.idx >= daily.length - 1) { this.pauseReplay(); SC.toast("마지막 봉까지 재생했습니다."); return; }
      this.replay.idx++;
      const dEl = this.replayBar.querySelector(".rb-date"); if (dEl) dEl.textContent = SC.fmtDate(daily[this.replay.idx].t, "D");
      const atEnd = this.end >= this.raw.length - 1;
      this.rebuild(false); if (atEnd) { this.end = this.raw.length - 1 + this.count * 0.3; this.draw(); }
    }
    playReplay() { if (!this.replay) return; this.pauseReplay(); this.replay.playing = true; this.replay.timer = setInterval(() => this.stepReplay(), 900 / this.replay.speed); this.renderReplayBar(); }
    pauseReplay() { if (!this.replay) return; clearInterval(this.replay.timer); this.replay.playing = false; this.renderReplayBar(); }
    stopReplay() { if (!this.replay) return; clearInterval(this.replay.timer); this.replay = null; this.replayBar.hidden = true; this.rebuild(false); this.resetView(); this.draw(); }
    renderReplayBar() {
      const r = this.replay, E = SC.el; if (!r) { this.replayBar.hidden = true; return; }
      const daily = this.app.barsFor(this.cfg.id, "D");
      const speed = E("select", { "aria-label": "재생 속도", onchange: e => { r.speed = +e.target.value; if (r.playing) this.playReplay(); } }, [1, 2, 5, 10].map(s => E("option", { value: s, selected: s === r.speed }, s + "배")));
      this.replayBar.replaceChildren(
        E("button", { type: "button", class: "rb-btn", onclick: () => (r.playing ? this.pauseReplay() : this.playReplay()) }, r.playing ? "일시정지" : "재생"),
        E("button", { type: "button", class: "rb-btn", onclick: () => this.stepReplay() }, "한 봉 앞으로"),
        speed,
        E("span", { class: "rb-date num" }, SC.fmtDate(daily[r.idx].t, "D")),
        E("button", { type: "button", class: "rb-btn", onclick: () => this.startReplayPick() }, "시작점 다시"),
        E("button", { type: "button", class: "rb-btn danger", onclick: () => this.stopReplay() }, "끝내기"));
      this.replayBar.hidden = false;
    }
    showHint(t) { this.hint.textContent = t; this.hint.hidden = false; }
    hideHint() { this.hint.hidden = true; }

    // ── 마우스·터치 ──
    bindEvents() {
      const ov = this.over, pointers = new Map();
      let pinch = null;
      const pos = e => { const r = ov.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
      const region = p => (p.x > this.plotW ? "price" : p.y > this.plotH ? "time" : "plot");
      const snap = (pt, p) => {
        if (!this.app.magnet || !this.panes) return pt;
        const i = SC.clamp(Math.round(this.iAt(p.x) - 0.5), 0, this.bars.length - 1), b = this.bars[i], Y = this.panes.main.y;
        let best = null, bd = 18;
        for (const v of b.ohlc ? [b.o, b.h, b.l, b.c] : [b.c]) { const d = Math.abs(Y(v) - p.y); if (d < bd) { bd = d; best = v; } }
        return best == null ? pt : { t: b.t, p: best };
      };
      const mainAt = p => p.y < this.layoutPanes[0].h;

      ov.addEventListener("pointerdown", e => {
        if (!this.bars.length) return;
        ov.setPointerCapture(e.pointerId);
        const p = pos(e); pointers.set(e.pointerId, p);
        if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = { d: Math.abs(a.x - b.x) || 1, count: this.count }; this.drag = null; return; }
        const reg = region(p);
        if (this.replayPick && reg === "plot") { this.startReplay(this.iToT(Math.round(this.iAt(p.x) - 0.5))); return; }
        if (reg === "price") { this.drag = { kind: "yscale", y0: p.y, lo: this.range.lo, hi: this.range.hi }; return; }
        if (reg === "time") { this.drag = { kind: "xscale", x0: p.x, count: this.count, end: this.end }; return; }
        const tool = this.app.tool, id = this.cfg.id;
        if (tool !== "cursor" && mainAt(p)) {
          const pt = snap(this.pt(p.x, p.y), p);
          if (this.pending) { this.pendingClick(pt); return; }
          this.app.pushUndo(id);
          const d = SC.Draw.create(tool, pt, this, this.app.drawColor);
          if (SC.TOOLS[tool].n <= 1) {
            this.app.setDrawings(id, this.app.drawingsFor(id).concat(d)); this.sel = d.id;
            if (tool === "text") this.editText(d, true); else this.app.saveDrawings(id);
            this.app.afterDraw(); this.app.redrawSymbol(id); return;
          }
          this.pending = d; this.pendingStep = 1; this.drag = { kind: "create", x0: p.x, y0: p.y };
          this.draw(); return;
        }
        // 그림 고르기
        if (!this.app.hideDrawings && mainAt(p)) {
          const list = this.app.drawingsFor(id), order = list.slice().sort((a, b) => (a.id === this.sel ? 1 : 0) - (b.id === this.sel ? 1 : 0)).reverse();
          for (const d of order) {
            const h = SC.Draw.hit(d, this, p.x, p.y);
            if (!h) continue;
            this.sel = d.id; this.draw();
            if (d.locked || this.app.lockAll) return;
            const before = JSON.stringify(list);
            if (h.kind === "handle") this.drag = { kind: "handle", h: h.h, d, before };
            else this.drag = { kind: "body", d, base: d.pts.map(q => ({ ...q })), i0: this.iAt(p.x), p0: this.panes.main.inv(p.y), before };
            return;
          }
        }
        if (this.sel) { this.sel = null; this.draw(); }
        this.drag = { kind: "pan", x0: p.x, y0: p.y, end: this.end, range: this.manual ? { ...this.manual } : null };
        ov.classList.add("grabbing");
      });

      ov.addEventListener("pointermove", e => {
        const p = pos(e);
        if (pointers.has(e.pointerId)) pointers.set(e.pointerId, p);
        if (pinch && pointers.size === 2) { const [a, b] = [...pointers.values()]; this.count = pinch.count * pinch.d / (Math.abs(a.x - b.x) || 1); this.clampView(); this.draw(); return; }
        const d = this.drag;
        if (d) {
          if (d.kind === "pan") {
            this.end = d.end - (p.x - d.x0) / this.bw(); this.clampView();
            if (d.range) { const k = (d.range.hi - d.range.lo) / (this.panes.main.bot - this.panes.main.top), dy = (p.y - d.y0) * k; this.manual = { lo: d.range.lo + dy, hi: d.range.hi + dy }; }
            this.draw(); this.app.syncCursor(this, null);
          } else if (d.kind === "yscale") {
            const mid = (d.lo + d.hi) / 2, f = Math.exp((p.y - d.y0) * 0.006), half = ((d.hi - d.lo) / 2) * f;
            this.manual = { lo: mid - half, hi: mid + half }; this.draw();
          } else if (d.kind === "xscale") {
            this.count = d.count * Math.exp((d.x0 - p.x) * -0.004); this.end = d.end; this.clampView(); this.draw();
          } else if (d.kind === "create") {
            this.pendingMove(snap(this.pt(p.x, p.y), p));
          } else if (d.kind === "handle") {
            d.h.move(snap(this.pt(p.x, p.y), p)); d.moved = true; this.draw();
          } else if (d.kind === "body") {
            const di = Math.round(this.iAt(p.x) - d.i0), dp = this.panes.main.inv(p.y) - d.p0;
            SC.Draw.shift(d.d, d.base, di, d.d.type === "vline" ? 0 : dp, this); d.moved = true; this.draw();
          }
        } else if (this.pending) this.pendingMove(snap(this.pt(p.x, p.y), p));
        this.hover = region(p) === "plot" || region(p) === "price" ? p : null;
        if (region(p) === "plot") this.app.syncCursor(this, this.iToT(Math.round(this.iAt(p.x) - 0.5)));
        // 커서 모양
        let cur = this.app.tool !== "cursor" || this.replayPick ? "crosshair" : region(p) === "price" ? "ns-resize" : region(p) === "time" ? "ew-resize" : "crosshair";
        if (!d && this.app.tool === "cursor" && region(p) === "plot" && !this.app.hideDrawings && this.panes && mainAt(p)) for (const dr of this.app.drawingsFor(this.cfg.id)) { const h = SC.Draw.hit(dr, this, p.x, p.y); if (h) { cur = h.kind === "handle" ? "move" : "pointer"; break; } }
        if (d && d.kind === "pan") cur = "grabbing";
        ov.style.cursor = cur;
        this.renderLegend(); this.renderLowerLabels(); this.drawOver();
      });

      const up = e => {
        const p = pos(e); pointers.delete(e.pointerId); if (pointers.size < 2) pinch = null;
        const d = this.drag; this.drag = null; ov.classList.remove("grabbing");
        if (!d) return;
        if (d.kind === "create") {
          // 끌어서 그렸으면 두 번째 점 확정
          if (Math.hypot(p.x - d.x0, p.y - d.y0) > 6) this.pendingClick(snap(this.pt(p.x, p.y), p));
        } else if ((d.kind === "handle" || d.kind === "body") && d.moved) {
          this.app.pushUndoRaw(this.cfg.id, d.before); this.app.saveDrawings(this.cfg.id); this.app.redrawSymbol(this.cfg.id);
        }
      };
      ov.addEventListener("pointerup", up); ov.addEventListener("pointercancel", up);
      ov.addEventListener("pointerleave", e => { if (e.pointerType === "mouse" && !this.drag) { this.hover = null; this.app.syncCursor(this, null); this.renderLegend(); this.renderLowerLabels(); this.drawOver(); } });
      ov.addEventListener("wheel", e => {
        e.preventDefault(); if (!this.bars.length) return;
        const p = pos(e);
        if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) this.end += (e.deltaX || e.deltaY) / this.bw() * 0.6;
        else { const anchor = this.iAt(p.x), k = Math.exp(e.deltaY * 0.0015), nc = this.count * k; this.end = anchor + (this.end - anchor) * nc / this.count; this.count = nc; }
        this.clampView(); this.hover = p; this.draw();
      }, { passive: false });
      ov.addEventListener("dblclick", e => {
        const p = pos(e), reg = region(p);
        if (reg === "price") { this.manual = null; this.draw(); return; }
        if (reg === "plot") {
          const d = this.app.drawingsFor(this.cfg.id).find(x => x.type === "text" && SC.Draw.hit(x, this, p.x, p.y));
          if (d) { this.editText(d); return; }
        }
        this.resetView(); this.draw();
      });
      ov.addEventListener("contextmenu", e => { if (this.pending) { e.preventDefault(); this.cancelPending(); } });
    }
    pendingMove(pt) {
      const d = this.pending; if (!d) return;
      for (let k = this.pendingStep; k < d.pts.length; k++) d.pts[k] = { ...pt };
      this.draw();
    }
    pendingClick(pt) {
      const d = this.pending; if (!d) return;
      d.pts[this.pendingStep] = { ...pt }; this.pendingStep++;
      if (this.pendingStep >= SC.TOOLS[d.type].n) {
        const id = this.cfg.id; this.pending = null;
        this.app.setDrawings(id, this.app.drawingsFor(id).concat(d)); this.sel = d.id; this.app.saveDrawings(id);
        this.app.afterDraw(); this.app.redrawSymbol(id);
      } else this.draw();
    }
    cancelPending() { if (this.pending) { this.pending = null; this.app.popUndo(this.cfg.id); this.draw(); } }

    // ── 스냅샷 ──
    snapshot() {
      const c = document.createElement("canvas"), D = this.DPR; c.width = this.base.width; c.height = this.base.height + 30 * D;
      const x = c.getContext("2d"); x.fillStyle = SC.colors.panel; x.fillRect(0, 0, c.width, c.height);
      x.scale(D, D); x.fillStyle = SC.colors.ink; x.font = `600 14px ${getComputedStyle(document.body).fontFamily}`; x.textBaseline = "middle";
      const b = this.bars[this.bars.length - 1];
      x.fillText(`${SC.nameOf(this.cfg.id)}  ${this.cfg.id.split(":")[1]} · ${SC.ivName(this.cfg.interval)} · ${SC.fmtDate(b.t, this.cfg.interval)} 종가 ${this.fmt(b.c)}`, 12, 15);
      x.setTransform(1, 0, 0, 1, 0, 30 * D); x.drawImage(this.base, 0, 0);
      return c;
    }
  }

  // ── 그리기 도우미 ──
  function line(ctx, x1, y1, x2, y2) { ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); }
  function roundRect(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
  function tag(ctx, x, y, text, bg, fg) {
    ctx.save(); ctx.font = `11px ${SC.fontNum}`; ctx.textBaseline = "middle";
    ctx.fillStyle = bg; roundRect(ctx, x + 2, y - 9, AXIS_W - 4, 18, 3); ctx.fill();
    ctx.fillStyle = fg; ctx.textAlign = "left"; ctx.fillText(text, x + 7, y + 0.5); ctx.restore();
  }
  function fmtShort(v) {
    if (v == null || !isFinite(v)) return "—";
    const a = Math.abs(v);
    if (a >= 1e8) return (v / 1e8).toFixed(2) + "억";
    if (a >= 1e4) return (v / 1e4).toFixed(1) + "만";
    if (a >= 100) return v.toFixed(0);
    return v.toFixed(2);
  }
  const EYE = SC.icon("eye", 14);
  const EYE_OFF = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M3 3l18 18M10.6 6.1A10.5 10.5 0 0 1 12 6c6.5 0 10 6 10 6a17 17 0 0 1-3.2 3.9M6.6 6.6C3.8 8.4 2 12 2 12s3.5 6 10 6a9.7 9.7 0 0 0 5.4-1.6"/></svg>`;
  const GEAR = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>`;
  const XICON = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>`;

  SC.isMinute = iv => /^\d+$/.test(iv);
  SC.ivName = iv => (SC.isMinute(iv) ? iv + "분" : SC.IV[iv]);
  SC.Chart = Chart;
})();
