// ── 스크리너 차트: 공용 도구 (데이터 로드, 봉 변환, 숫자 표시, DOM 헬퍼) ──
"use strict";
const SC = (window.SC = {});
SC.DATA = "output/";
SC.MK = { kr: "국내", us: "미국", ix: "지수" };
SC.IV = { D: "일봉", W: "주봉", M: "월봉" };

// ── 브라우저 저장 (막혀 있어도 동작) ─────────────────────────
SC.store = {
  get(k, d) { try { const v = localStorage.getItem("sc:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("sc:" + k, JSON.stringify(v)); } catch { /* 무시 */ } },
};

// ── 데이터 ───────────────────────────────────────────────────
SC.db = { kr: null, us: null, ix: null, names: [], byId: {}, loading: {}, cache: {}, listeners: [] };
SC.onData = fn => SC.db.listeners.push(fn);

async function getJSON(path) {
  const r = await fetch(SC.DATA + path, { cache: "no-cache" });
  if (!r.ok) throw new Error(path + " (" + r.status + ")");
  return r.json();
}
SC.loadMarket = function (mk) {
  const db = SC.db;
  if (db.loading[mk]) return db.loading[mk];
  db.loading[mk] = (async () => {
    if (mk === "ix") {
      const j = await getJSON("market_index.json");
      db.ix = { asof: j.asof, s: j.idx };
      for (const [t, v] of Object.entries(j.idx)) addName({ id: "ix:" + t, mk, t, name: v.name, sector: "지수", mcap: 1e12 });
    } else {
      const [series, uni] = await Promise.all([getJSON(`series_${mk}.json`), getJSON(`universe_${mk}.json`)]);
      const rows = {};
      for (const r of uni.rows) rows[r.ticker] = r;
      db[mk] = { dates: series.dates, s: series.s, rows, asof: uni.date, schema: uni.schema };
      for (const t of Object.keys(series.s)) {
        const r = rows[t] || {};
        addName({ id: mk + ":" + t, mk, t, name: r.name || t, sector: r.sector || "", mcap: r.mcap ? (mk === "us" ? r.mcap * 14 : r.mcap) : 0 });
      }
    }
    db.names.sort((a, b) => (b.mcap || 0) - (a.mcap || 0));
    db.listeners.forEach(fn => fn(mk));
  })();
  return db.loading[mk];
};
function addName(n) { if (!SC.db.byId[n.id]) { SC.db.byId[n.id] = n; SC.db.names.push(n); } }
SC.nameOf = id => (SC.db.byId[id] ? SC.db.byId[id].name : id.split(":")[1]);
SC.mkOf = id => id.split(":")[0];
SC.ready = id => { const mk = SC.mkOf(id); return !!SC.db[mk]; };

// yymmdd 또는 YYYY-MM-DD → UTC ms
SC.ymd = s => (s.length === 6 ? Date.UTC(2000 + +s.slice(0, 2), +s.slice(2, 4) - 1, +s.slice(4, 6)) : Date.parse(s + "T00:00:00Z"));

// 종목 → 일봉 [{t,o,h,l,c,v,ohlc}]
SC.dailyBars = function (id) {
  if (SC.db.cache[id]) return SC.db.cache[id];
  const [mk, t] = id.split(":");
  let out = null;
  if (mk === "ix") {
    const v = SC.db.ix && SC.db.ix.s[t];
    if (v) out = v.d.map((d, i) => ({ t: SC.ymd(d), o: v.c[i], h: v.c[i], l: v.c[i], c: v.c[i], v: v.v ? v.v[i] : 0, ohlc: false }));
  } else {
    const m = SC.db[mk], rec = m && m.s[t];
    if (rec) {
      const c = rec.c.split(",").map(Number), v = rec.v.split(",").map(Number);
      const k = rec.k || 0, n = c.length, dates = m.dates.slice(-n);
      const sp = s => (s ? s.split(",").map(Number) : []);
      const o = sp(rec.o), h = sp(rec.h), l = sp(rec.l);
      out = [];
      for (let i = 0; i < n; i++) {
        const j = i - (n - k); // OHLC 는 마지막 k 개만, 종가와의 차이로 저장
        if (j >= 0) {
          const oo = c[i] + o[j];
          out.push({ t: SC.ymd(dates[i]), o: oo, h: Math.max(c[i] + h[j], oo, c[i]), l: Math.min(c[i] + l[j], oo, c[i]), c: c[i], v: v[i] * 1000, ohlc: true });
        } else out.push({ t: SC.ymd(dates[i]), o: c[i], h: c[i], l: c[i], c: c[i], v: v[i] * 1000, ohlc: false });
      }
    }
  }
  if (out) SC.db.cache[id] = out;
  return out;
};

SC.periodKey = (t, iv) => {
  if (iv === "D") return t;
  const d = new Date(t);
  if (iv === "M") return d.getUTCFullYear() * 12 + d.getUTCMonth();
  return t - ((d.getUTCDay() + 6) % 7) * 864e5;
};
SC.resample = function (bars, iv) {
  if (iv === "D") return bars;
  const out = [];
  let cur = null, ck = null;
  for (const b of bars) {
    const k = SC.periodKey(b.t, iv);
    if (k !== ck) { if (cur) out.push(cur); cur = { ...b }; ck = k; }
    else { cur.h = Math.max(cur.h, b.h); cur.l = Math.min(cur.l, b.l); cur.c = b.c; cur.v += b.v; cur.ohlc = cur.ohlc && b.ohlc; }
  }
  if (cur) out.push(cur);
  return out;
};
SC.heikin = function (bars) {
  const out = [];
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i], p = out[i - 1];
    const c = (b.o + b.h + b.l + b.c) / 4, o = p ? (p.o + p.c) / 2 : (b.o + b.c) / 2;
    out.push({ ...b, o, c, h: Math.max(b.h, o, c), l: Math.min(b.l, o, c) });
  }
  return out;
};
// 시각 t 이하의 마지막 봉 위치 (없으면 -1)
SC.asof = function (bars, t) {
  let lo = 0, hi = bars.length - 1, r = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (bars[m].t <= t) { r = m; lo = m + 1; } else hi = m - 1; }
  return r;
};

// ── 숫자·날짜 표시 ───────────────────────────────────────────
SC.decimals = id => (SC.mkOf(id) === "kr" ? 0 : 2);
SC.fmtPrice = (x, id, d) => {
  if (x == null || !isFinite(x)) return "—";
  d = d == null ? SC.decimals(id) : d;
  if (d === 0 && Math.abs(x) < 100) d = 2;
  return x.toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
};
SC.fmtNum = (x, d = 2) => (x == null || !isFinite(x) ? "—" : x.toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d }));
SC.fmtVol = x => {
  if (!x) return "—";
  const a = Math.abs(x);
  if (a >= 1e8) return (x / 1e8).toFixed(2) + "억";
  if (a >= 1e4) return Math.round(x / 1e4).toLocaleString("ko-KR") + "만";
  return Math.round(x).toLocaleString("ko-KR");
};
SC.fmtPct = x => (x == null || !isFinite(x) ? "—" : (x > 0 ? "+" : "") + x.toFixed(2) + "%");
SC.cls = x => (x > 0 ? "up" : x < 0 ? "down" : "flat");
SC.fmtDate = (t, iv) => {
  const d = new Date(t), y = d.getUTCFullYear(), m = String(d.getUTCMonth() + 1).padStart(2, "0"), dd = String(d.getUTCDate()).padStart(2, "0");
  return iv === "M" ? `${y}.${m}` : `${y}.${m}.${dd}`;
};
SC.fmtMoney = (v, mk) => {
  if (v == null || !isFinite(v)) return "—";
  if (mk === "kr") return v >= 1e4 ? (v / 1e4).toLocaleString("ko-KR", { maximumFractionDigits: 1 }) + "조" : Math.round(v).toLocaleString("ko-KR") + "억";
  return v >= 1e6 ? "$" + (v / 1e6).toFixed(2) + "T" : v >= 1e3 ? "$" + (v / 1e3).toFixed(1) + "B" : "$" + Math.round(v) + "M";
};

// ── DOM ──────────────────────────────────────────────────────
SC.el = function (tag, attrs, ...kids) {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "html") e.innerHTML = v; // 고정 아이콘 SVG 에만 사용
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
};
SC.toast = function (msg) {
  const box = document.getElementById("toasts");
  const t = SC.el("div", { class: "toast", role: "status" }, msg);
  box.append(t);
  setTimeout(() => t.remove(), 2600);
};
// 모달: content 노드, buttons [{label, primary, onclick}] → 닫기 함수 반환
SC.modal = function (title, content, buttons = [], opts = {}) {
  const back = SC.el("div", { class: "modal-back" });
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey, true); opts.onclose && opts.onclose(); };
  const onKey = e => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  const box = SC.el("div", { class: "modal" + (opts.wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
    SC.el("header", null, SC.el("h3", null, title), SC.el("button", { type: "button", class: "x", "aria-label": "닫기", onclick: close }, "×")),
    SC.el("div", { class: "body" }, content),
    buttons.length ? SC.el("footer", null, buttons.map(b => SC.el("button", { type: "button", class: b.primary ? "btn primary" : "btn", onclick: () => { if (b.onclick && b.onclick() === false) return; close(); } }, b.label))) : null);
  back.append(box);
  back.addEventListener("pointerdown", e => { if (e.target === back) close(); });
  document.addEventListener("keydown", onKey, true);
  document.body.append(back);
  const f = box.querySelector("input,select,button.btn.primary"); if (f) setTimeout(() => f.focus(), 0);
  return close;
};

// ── 색 ──────────────────────────────────────────────────────
SC.colors = {};
SC.readColors = function () {
  const cs = getComputedStyle(document.documentElement);
  for (const k of ["panel", "bg", "line", "grid", "ink", "ink-2", "ink-3", "up", "down", "flat", "cross", "tag", "tag-ink", "sel", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"])
    SC.colors[k] = cs.getPropertyValue("--" + k).trim();
  SC.fontNum = cs.getPropertyValue("--font-num").trim() || "monospace";
};
// "c3" 같은 토큰 이름이나 #hex 를 실제 색으로
SC.color = c => (c && c[0] === "#" ? c : SC.colors[c] || c || SC.colors.ink);
SC.alpha = function (c, a) {
  c = SC.color(c);
  const m = /^#([0-9a-f]{6})$/i.exec(c);
  if (!m) return c;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};
SC.toHex = c => { c = SC.color(c); return /^#[0-9a-f]{6}$/i.test(c) ? c : "#888888"; };

SC.niceTicks = function (lo, hi, target) {
  const span = hi - lo;
  if (!(span > 0) || !isFinite(span)) return [lo];
  const raw = span / Math.max(1, target), p = 10 ** Math.floor(Math.log10(raw)), f = raw / p;
  const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) out.push(+v.toPrecision(12));
  return out;
};
SC.uid = () => Math.random().toString(36).slice(2, 9);
SC.clamp = (x, a, b) => Math.max(a, Math.min(b, x));
