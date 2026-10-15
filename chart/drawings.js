// ── 그리기 도구 ─────────────────────────────────────────────
// 도형: { id, type, pts: [{t, p}], color, width, text?, locked? }
// 차트(ch)가 주는 변환: ch.xy(pt) → {x, y},  ch.pt(x, y) → {t, p},  ch.tToI / ch.iToT,  ch.plotW, ch.fmt(p)
"use strict";
(function () {
  const TOOLS = {
    cursor: { name: "십자선", n: 0, key: "Escape" },
    trend: { name: "추세선", n: 2, key: "T" },
    ray: { name: "연장선 (한쪽)", n: 2 },
    ext: { name: "연장선 (양쪽)", n: 2 },
    hline: { name: "수평선", n: 1, key: "H" },
    hray: { name: "수평 반직선", n: 1 },
    vline: { name: "수직선", n: 1, key: "V" },
    channel: { name: "평행 채널", n: 3 },
    rect: { name: "사각형", n: 2 },
    fib: { name: "피보나치 되돌림", n: 2, key: "F" },
    measure: { name: "가격·기간 측정", n: 2, key: "M" },
    text: { name: "텍스트 메모", n: 1 },
    long: { name: "롱 포지션", n: 1 },
    short: { name: "숏 포지션", n: 1 },
  };
  // 도구 아이콘 (고정 SVG)
  const ICON = {
    cursor: '<path d="M12 3v18M3 12h18"/>',
    trend: '<path d="M4 19 20 5"/><circle cx="4" cy="19" r="1.8"/><circle cx="20" cy="5" r="1.8"/>',
    ray: '<path d="M4 18 21 6"/><circle cx="4" cy="18" r="1.8"/><circle cx="12" cy="12.4" r="1.8"/>',
    ext: '<path d="M2 19 22 5"/><circle cx="8" cy="14.8" r="1.8"/><circle cx="16" cy="9.2" r="1.8"/>',
    hline: '<path d="M2 12h20"/><circle cx="12" cy="12" r="1.8"/>',
    hray: '<path d="M8 12h14"/><circle cx="8" cy="12" r="1.8"/>',
    vline: '<path d="M12 2v20"/><circle cx="12" cy="12" r="1.8"/>',
    channel: '<path d="M3 15 15 5M9 19 21 9"/>',
    rect: '<rect x="4" y="6" width="16" height="12" rx="1"/>',
    fib: '<path d="M3 5h18M3 9.5h18M3 13h18M3 19h18"/>',
    measure: '<path d="M5 19V5M5 19h14M9 15l8-8M14 7h3v3"/>',
    text: '<path d="M5 6h14M12 6v13"/>',
    long: '<rect x="4" y="4" width="16" height="9" rx="1"/><path d="M4 16h16"/><path d="M4 19h16" opacity=".5"/>',
    short: '<rect x="4" y="11" width="16" height="9" rx="1"/><path d="M4 8h16"/><path d="M4 5h16" opacity=".5"/>',
    magnet: '<path d="M6 4v8a6 6 0 0 0 12 0V4M6 8h4M14 8h4"/>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="1.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
  };
  SC.TOOLS = TOOLS;
  SC.icon = (k, size = 18) => `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[k] || ""}</svg>`;
  const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

  function dSeg(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1, L = dx * dx + dy * dy;
    let t = L ? ((px - x1) * dx + (py - y1) * dy) / L : 0; t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - x1 - t * dx, py - y1 - t * dy);
  }
  // 선분을 화면 끝까지 연장
  function extend(a, b, W, both) {
    const dx = b.x - a.x, dy = b.y - a.y;
    if (Math.abs(dx) < 1e-6) return [a, { x: b.x, y: dy > 0 ? 1e5 : -1e5 }];
    const k = dy / dx, toX = x => ({ x, y: a.y + k * (x - a.x) });
    const far = dx > 0 ? toX(W) : toX(0);
    return [both ? (dx > 0 ? toX(0) : toX(W)) : a, far];
  }

  // 손잡이: [{x, y, move(pt)}]
  function handles(d, ch) {
    const P = d.pts.map(p => ch.xy(p)), T = d.type;
    if (T === "hline" || T === "hray") return [{ x: T === "hline" ? ch.plotW / 2 : P[0].x, y: P[0].y, move: pt => { d.pts[0].p = pt.p; if (T === "hray") d.pts[0].t = pt.t; } }];
    if (T === "vline") return [{ x: P[0].x, y: 40, move: pt => (d.pts[0].t = pt.t) }];
    if (T === "long" || T === "short") {
      const x0 = P[0].x, x1 = P[1].x, ys = ch.xy({ t: d.pts[1].t, p: d.pts[1].p }).y, yt = ch.xy({ t: d.pts[2].t, p: d.pts[2].p }).y;
      return [
        { x: x0, y: P[0].y, move: pt => { d.pts[0].p = pt.p; d.pts[0].t = pt.t; } },
        { x: x0, y: ys, move: pt => (d.pts[1].p = pt.p) },
        { x: x0, y: yt, move: pt => (d.pts[2].p = pt.p) },
        { x: x1, y: P[0].y, move: pt => { if (ch.tToI(pt.t) > ch.tToI(d.pts[0].t) + 1) d.pts[1].t = d.pts[2].t = pt.t; } },
      ];
    }
    if (T === "rect" || T === "measure") {
      const h = P.map((q, i) => ({ x: q.x, y: q.y, move: pt => (d.pts[i] = pt) }));
      h.push({ x: P[0].x, y: P[1].y, move: pt => { d.pts[0].t = pt.t; d.pts[1].p = pt.p; } }, { x: P[1].x, y: P[0].y, move: pt => { d.pts[1].t = pt.t; d.pts[0].p = pt.p; } });
      return h;
    }
    return P.map((q, i) => ({ x: q.x, y: q.y, move: pt => (d.pts[i] = pt) }));
  }

  // 도형 그리기. 가격축에 표시할 라벨을 돌려줌
  function draw(ctx, d, ch, sel) {
    const col = SC.color(d.color), P = d.pts.map(p => ch.xy(p)), W = ch.plotW, axis = [];
    ctx.save();
    ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = d.width || 2; ctx.lineCap = "round";
    ctx.font = `12px ${SC.fontNum}`; ctx.textBaseline = "middle";
    const ln = (a, b) => { ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); };
    const label = (txt, x, y, align = "left", bg) => {
      ctx.textAlign = align; const w = ctx.measureText(txt).width;
      if (bg) { ctx.fillStyle = bg; const bx = align === "left" ? x - 4 : align === "right" ? x - w - 4 : x - w / 2 - 4; ctx.fillRect(bx, y - 9, w + 8, 18); }
      ctx.fillStyle = bg ? "#ffffff" : col; ctx.fillText(txt, x, y);
    };
    switch (d.type) {
      case "trend": ln(P[0], P[1]); break;
      case "ray": { const [a, b] = extend(P[0], P[1], W, false); ln(a, b); break; }
      case "ext": { const [a, b] = extend(P[0], P[1], W, true); ln(a, b); break; }
      case "hline": ln({ x: 0, y: P[0].y }, { x: W, y: P[0].y }); axis.push({ y: P[0].y, text: ch.fmt(d.pts[0].p), color: col }); break;
      case "hray": ln(P[0], { x: W, y: P[0].y }); axis.push({ y: P[0].y, text: ch.fmt(d.pts[0].p), color: col }); break;
      case "vline": ln({ x: P[0].x, y: 0 }, { x: P[0].x, y: ch.plotH }); break;
      case "channel": {
        ln(P[0], P[1]);
        if (P[2]) {
          const k = P[1].x !== P[0].x ? (P[1].y - P[0].y) / (P[1].x - P[0].x) : 0, off = P[2].y - (P[0].y + k * (P[2].x - P[0].x));
          const a2 = { x: P[0].x, y: P[0].y + off }, b2 = { x: P[1].x, y: P[1].y + off };
          ln(a2, b2);
          ctx.globalAlpha = 0.08; ctx.beginPath(); ctx.moveTo(P[0].x, P[0].y); ctx.lineTo(P[1].x, P[1].y); ctx.lineTo(b2.x, b2.y); ctx.lineTo(a2.x, a2.y); ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;
          ctx.setLineDash([4, 4]); ctx.lineWidth = 1; ln({ x: P[0].x, y: P[0].y + off / 2 }, { x: P[1].x, y: P[1].y + off / 2 }); ctx.setLineDash([]);
        }
        break;
      }
      case "rect": {
        const x = Math.min(P[0].x, P[1].x), y = Math.min(P[0].y, P[1].y), w = Math.abs(P[1].x - P[0].x), h = Math.abs(P[1].y - P[0].y);
        ctx.globalAlpha = 0.12; ctx.fillRect(x, y, w, h); ctx.globalAlpha = 1; ctx.strokeRect(x, y, w, h); break;
      }
      case "fib": {
        const x1 = Math.min(P[0].x, P[1].x), x2 = Math.max(P[0].x, P[1].x, x1 + 40);
        ctx.lineWidth = 1;
        FIB.forEach((L, i) => {
          const p = d.pts[1].p + (d.pts[0].p - d.pts[1].p) * L, y = ch.xy({ t: d.pts[0].t, p }).y;
          if (i < FIB.length - 1) {
            const pn = d.pts[1].p + (d.pts[0].p - d.pts[1].p) * FIB[i + 1], yn = ch.xy({ t: d.pts[0].t, p: pn }).y;
            ctx.globalAlpha = i % 2 ? 0.05 : 0.09; ctx.fillRect(x1, Math.min(y, yn), x2 - x1, Math.abs(yn - y)); ctx.globalAlpha = 1;
          }
          ln({ x: x1, y }, { x: x2, y });
          label(`${L} (${ch.fmt(p)})`, x1 + 4, y - 9);
        });
        ctx.setLineDash([3, 3]); ln(P[0], P[1]); ctx.setLineDash([]);
        break;
      }
      case "measure": {
        const up = d.pts[1].p >= d.pts[0].p, c = SC.color(up ? "up" : "down");
        const x = Math.min(P[0].x, P[1].x), y = Math.min(P[0].y, P[1].y), w = Math.abs(P[1].x - P[0].x), h = Math.abs(P[1].y - P[0].y);
        ctx.fillStyle = c; ctx.globalAlpha = 0.12; ctx.fillRect(x, y, w, h); ctx.globalAlpha = 1;
        ctx.strokeStyle = c; ctx.lineWidth = 1; ln({ x: (P[0].x + P[1].x) / 2, y: P[0].y }, { x: (P[0].x + P[1].x) / 2, y: P[1].y }); ln({ x: P[0].x, y: (P[0].y + P[1].y) / 2 }, { x: P[1].x, y: (P[0].y + P[1].y) / 2 });
        const dp = d.pts[1].p - d.pts[0].p, bars = Math.round(ch.tToI(d.pts[1].t) - ch.tToI(d.pts[0].t)), days = Math.round((d.pts[1].t - d.pts[0].t) / 864e5);
        const lines = [`${dp >= 0 ? "+" : ""}${ch.fmt(dp)} (${SC.fmtPct((dp / d.pts[0].p) * 100)})`, `${bars}봉 · ${days}일`];
        const ly = up ? y - 26 : y + h + 8, lx = (P[0].x + P[1].x) / 2;
        ctx.font = `12px ${SC.fontNum}`;
        const bw = Math.max(...lines.map(s => ctx.measureText(s).width)) + 16;
        ctx.fillStyle = c; ctx.fillRect(lx - bw / 2, ly - 2, bw, 36);
        ctx.fillStyle = "#ffffff"; ctx.textAlign = "center"; lines.forEach((s, i) => ctx.fillText(s, lx, ly + 8 + i * 16));
        break;
      }
      case "text": {
        ctx.font = `500 13px ${getComputedStyle(document.body).fontFamily}`; ctx.textAlign = "left";
        const lines = String(d.text || "메모").split("\n");
        lines.forEach((s, i) => ctx.fillText(s, P[0].x, P[0].y + i * 17));
        d._box = { x: P[0].x - 4, y: P[0].y - 10, w: Math.max(...lines.map(s => ctx.measureText(s).width)) + 8, h: lines.length * 17 + 4 };
        if (sel) { ctx.setLineDash([3, 3]); ctx.lineWidth = 1; ctx.strokeRect(d._box.x, d._box.y, d._box.w, d._box.h); ctx.setLineDash([]); }
        break;
      }
      case "long": case "short": {
        const x0 = P[0].x, x1 = P[1].x, ye = P[0].y, ys = ch.xy(d.pts[1]).y, yt = ch.xy(d.pts[2]).y;
        const fill = (y1, y2, c) => { ctx.fillStyle = SC.alpha(c, 0.16); ctx.fillRect(x0, Math.min(y1, y2), x1 - x0, Math.abs(y2 - y1)); };
        fill(ye, yt, "c3"); fill(ye, ys, "c8");
        ctx.lineWidth = 1; ctx.strokeStyle = SC.color("ink-2"); ln({ x: x0, y: ye }, { x: x1, y: ye });
        const e = d.pts[0].p, s = d.pts[1].p, t = d.pts[2].p, risk = Math.abs(e - s), reward = Math.abs(t - e);
        const rr = risk ? (reward / risk).toFixed(2) : "—";
        ctx.font = `12px ${SC.fontNum}`;
        label(`목표 ${ch.fmt(t)} (${SC.fmtPct(((t - e) / e) * 100)})`, (x0 + x1) / 2, yt + (yt < ye ? -11 : 11), "center", SC.color("c3"));
        label(`손절 ${ch.fmt(s)} (${SC.fmtPct(((s - e) / e) * 100)})`, (x0 + x1) / 2, ys + (ys < ye ? -11 : 11), "center", SC.color("c8"));
        label(`${d.type === "long" ? "롱" : "숏"} ${ch.fmt(e)} · 손익비 ${rr}`, (x0 + x1) / 2, ye, "center", SC.color("ink-2"));
        axis.push({ y: yt, text: ch.fmt(t), color: SC.color("c3") }, { y: ys, text: ch.fmt(s), color: SC.color("c8") });
        break;
      }
    }
    if (sel) {
      for (const h of handles(d, ch)) {
        ctx.beginPath(); ctx.arc(h.x, h.y, 4.5, 0, 7); ctx.fillStyle = SC.color("panel"); ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = col; ctx.stroke();
      }
    }
    ctx.restore();
    return axis;
  }

  // 맞으면 {kind:"handle", h} 또는 {kind:"body"}
  function hit(d, ch, x, y) {
    const tol = 6;
    for (const h of handles(d, ch)) if (Math.hypot(h.x - x, h.y - y) <= 7) return { kind: "handle", h };
    const P = d.pts.map(p => ch.xy(p)), W = ch.plotW;
    let on = false;
    switch (d.type) {
      case "trend": on = dSeg(x, y, P[0].x, P[0].y, P[1].x, P[1].y) < tol; break;
      case "ray": case "ext": { const [a, b] = extend(P[0], P[1], W, d.type === "ext"); on = dSeg(x, y, a.x, a.y, b.x, b.y) < tol; break; }
      case "hline": on = Math.abs(y - P[0].y) < tol; break;
      case "hray": on = Math.abs(y - P[0].y) < tol && x >= P[0].x - tol; break;
      case "vline": on = Math.abs(x - P[0].x) < tol; break;
      case "channel": {
        on = dSeg(x, y, P[0].x, P[0].y, P[1].x, P[1].y) < tol;
        if (!on && P[2]) { const k = P[1].x !== P[0].x ? (P[1].y - P[0].y) / (P[1].x - P[0].x) : 0, off = P[2].y - (P[0].y + k * (P[2].x - P[0].x)); on = dSeg(x, y, P[0].x, P[0].y + off, P[1].x, P[1].y + off) < tol; }
        break;
      }
      case "rect": case "measure": case "fib": {
        const x1 = Math.min(P[0].x, P[1].x) - tol, x2 = Math.max(P[0].x, P[1].x, d.type === "fib" ? P[0].x + 40 : 0) + tol, y1 = Math.min(P[0].y, P[1].y) - tol, y2 = Math.max(P[0].y, P[1].y) + tol;
        on = x >= x1 && x <= x2 && y >= y1 && y <= y2; break;
      }
      case "text": { const b = d._box; on = !!b && x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h; break; }
      case "long": case "short": {
        const ys = ch.xy(d.pts[1]).y, yt = ch.xy(d.pts[2]).y;
        on = x >= P[0].x && x <= P[1].x && y >= Math.min(ys, yt) && y <= Math.max(ys, yt); break;
      }
    }
    return on ? { kind: "body" } : null;
  }

  // 새 도형 만들기 (첫 클릭 지점에서)
  function create(type, pt, ch, color) {
    const d = { id: SC.uid(), type, pts: [{ ...pt }], color: color || "c1", width: type === "fib" || type === "measure" ? 1 : 2 };
    const n = TOOLS[type].n;
    for (let i = 1; i < n; i++) d.pts.push({ ...pt });
    if (type === "long" || type === "short") {
      const end = ch.iToT(ch.tToI(pt.t) + 20), sgn = type === "long" ? 1 : -1;
      d.pts = [{ ...pt }, { t: end, p: pt.p * (1 - sgn * 0.03) }, { t: end, p: pt.p * (1 + sgn * 0.06) }];
      d.width = 1;
    }
    if (type === "text") d.text = "";
    return d;
  }
  // 몸통 끌기: 시간은 봉 단위로, 가격은 그대로 이동
  function shift(d, base, di, dp, ch) {
    d.pts = base.map(p => ({ t: ch.iToT(ch.tToI(p.t) + di), p: p.p + dp }));
  }

  SC.Draw = { draw, hit, create, shift, handles };
})();
