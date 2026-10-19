// ── 지표 라이브러리 ─────────────────────────────────────────
// 각 지표: { name, group, pane: "main"|"volume"|"lower", params, plots, fills?, levels?, range?, calc(bars, p) → [값 배열...] }
// plots[i] = { name, color(토큰 또는 #hex), type: "line"|"hist"|"dots"|"area"|"step", width, offset? }
"use strict";
(function () {
  const N = n => new Array(n).fill(null);
  const src = b => b.map(x => x.c);
  const hl2 = b => b.map(x => (x.h + x.l) / 2);
  const hlc3 = b => b.map(x => (x.h + x.l + x.c) / 3);

  function sma(x, n) { const o = N(x.length); let s = 0, cnt = 0; for (let i = 0; i < x.length; i++) { if (x[i] == null) { s = 0; cnt = 0; continue; } s += x[i]; cnt++; if (cnt > n) s -= x[i - n]; if (cnt >= n) o[i] = s / n; } return o; }
  function ema(x, n) { const o = N(x.length), a = 2 / (n + 1); let e = null, cnt = 0; for (let i = 0; i < x.length; i++) { if (x[i] == null) continue; e = e == null ? x[i] : x[i] * a + e * (1 - a); if (++cnt >= n) o[i] = e; } return o; }
  function rma(x, n) { const o = N(x.length); let e = null, cnt = 0, s = 0; for (let i = 0; i < x.length; i++) { if (x[i] == null) continue; cnt++; if (cnt <= n) { s += x[i]; if (cnt === n) { e = s / n; o[i] = e; } } else { e = (e * (n - 1) + x[i]) / n; o[i] = e; } } return o; }
  function wma(x, n) { const o = N(x.length), d = n * (n + 1) / 2; for (let i = n - 1; i < x.length; i++) { let s = 0, ok = true; for (let j = 0; j < n; j++) { const v = x[i - j]; if (v == null) { ok = false; break; } s += v * (n - j); } if (ok) o[i] = s / d; } return o; }
  function stdev(x, n, m) { const o = N(x.length); for (let i = n - 1; i < x.length; i++) { if (m[i] == null) continue; let s = 0; for (let j = i - n + 1; j <= i; j++) s += (x[j] - m[i]) ** 2; o[i] = Math.sqrt(s / n); } return o; }
  function highest(x, n) { const o = N(x.length); for (let i = n - 1; i < x.length; i++) { let m = -Infinity; for (let j = i - n + 1; j <= i; j++) if (x[j] != null && x[j] > m) m = x[j]; o[i] = m; } return o; }
  function lowest(x, n) { const o = N(x.length); for (let i = n - 1; i < x.length; i++) { let m = Infinity; for (let j = i - n + 1; j <= i; j++) if (x[j] != null && x[j] < m) m = x[j]; o[i] = m; } return o; }
  function tr(b) { return b.map((x, i) => (i ? Math.max(x.h - x.l, Math.abs(x.h - b[i - 1].c), Math.abs(x.l - b[i - 1].c)) : x.h - x.l)); }
  function rsiOf(c, n) {
    const o = N(c.length); let g = 0, l = 0;
    for (let i = 1; i < c.length; i++) {
      const d = c[i] - c[i - 1], up = Math.max(d, 0), dn = Math.max(-d, 0);
      if (i <= n) { g += up; l += dn; if (i === n) { g /= n; l /= n; o[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l); } }
      else { g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n; o[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l); }
    }
    return o;
  }
  const map2 = (a, b, f) => a.map((v, i) => (v == null || b[i] == null ? null : f(v, b[i], i)));

  const P = (k, label, def, min = 1, max = 500, step = 1) => ({ k, label, def, min, max, step });
  const len = (d = 20) => P("len", "기간", d);

  const IND = {
    // ── 가격 위 (main) ──
    sma: { name: "단순 이동평균 (SMA)", short: "SMA", group: "추세", pane: "main", params: [len(20)], plots: [{ name: "SMA", color: "c2", width: 1.5 }], calc: (b, p) => [sma(src(b), p.len)] },
    ema: { name: "지수 이동평균 (EMA)", short: "EMA", group: "추세", pane: "main", params: [len(20)], plots: [{ name: "EMA", color: "c7", width: 1.5 }], calc: (b, p) => [ema(src(b), p.len)] },
    wma: { name: "가중 이동평균 (WMA)", short: "WMA", group: "추세", pane: "main", params: [len(20)], plots: [{ name: "WMA", color: "c3", width: 1.5 }], calc: (b, p) => [wma(src(b), p.len)] },
    ma_ribbon: {
      name: "이동평균 묶음 (5·20·60·120)", short: "MA", group: "추세", pane: "main", params: [P("a", "단기", 5), P("b", "중기", 20), P("c", "장기", 60), P("d", "초장기", 120)],
      plots: [{ name: "MA1", color: "c2", width: 1.5 }, { name: "MA2", color: "c7", width: 1.5 }, { name: "MA3", color: "c3", width: 1.5 }, { name: "MA4", color: "c4", width: 1.5 }],
      calc: (b, p) => [sma(src(b), p.a), sma(src(b), p.b), sma(src(b), p.c), sma(src(b), p.d)],
    },
    bb: {
      name: "볼린저 밴드", short: "BB", group: "변동성", pane: "main", params: [len(20), P("mult", "표준편차 배수", 2, 0.5, 5, 0.1)],
      plots: [{ name: "중심", color: "c2", width: 1 }, { name: "상단", color: "c1", width: 1 }, { name: "하단", color: "c1", width: 1 }], fills: [{ a: 1, b: 2, color: "c1", alpha: 0.07 }],
      calc: (b, p) => { const c = src(b), m = sma(c, p.len), sd = stdev(c, p.len, m); return [m, map2(m, sd, (x, s) => x + p.mult * s), map2(m, sd, (x, s) => x - p.mult * s)]; },
    },
    env: {
      name: "엔벨로프", short: "ENV", group: "변동성", pane: "main", params: [len(20), P("pct", "폭 %", 10, 0.5, 50, 0.5)],
      plots: [{ name: "중심", color: "c2", width: 1 }, { name: "상단", color: "c5", width: 1 }, { name: "하단", color: "c5", width: 1 }], fills: [{ a: 1, b: 2, color: "c5", alpha: 0.06 }],
      calc: (b, p) => { const m = sma(src(b), p.len); return [m, m.map(v => (v == null ? null : v * (1 + p.pct / 100))), m.map(v => (v == null ? null : v * (1 - p.pct / 100)))]; },
    },
    donchian: {
      name: "돈치안 채널", short: "DC", group: "변동성", pane: "main", params: [len(20)],
      plots: [{ name: "상단", color: "c1", width: 1 }, { name: "중심", color: "c2", width: 1 }, { name: "하단", color: "c1", width: 1 }], fills: [{ a: 0, b: 2, color: "c1", alpha: 0.05 }],
      calc: (b, p) => { const h = highest(b.map(x => x.h), p.len), l = lowest(b.map(x => x.l), p.len); return [h, map2(h, l, (x, y) => (x + y) / 2), l]; },
    },
    keltner: {
      name: "켈트너 채널", short: "KC", group: "변동성", pane: "main", params: [len(20), P("mult", "ATR 배수", 2, 0.5, 5, 0.1), P("atr", "ATR 기간", 10)],
      plots: [{ name: "중심", color: "c2", width: 1 }, { name: "상단", color: "c3", width: 1 }, { name: "하단", color: "c3", width: 1 }], fills: [{ a: 1, b: 2, color: "c3", alpha: 0.06 }],
      calc: (b, p) => { const m = ema(src(b), p.len), a = rma(tr(b), p.atr); return [m, map2(m, a, (x, y) => x + p.mult * y), map2(m, a, (x, y) => x - p.mult * y)]; },
    },
    ichimoku: {
      name: "일목균형표", short: "일목", group: "추세", pane: "main", params: [P("conv", "전환선", 9), P("base", "기준선", 26), P("span", "선행스팬2", 52), P("disp", "이동", 26)],
      plots: [{ name: "전환선", color: "c1", width: 1 }, { name: "기준선", color: "c8", width: 1 }, { name: "후행스팬", color: "c3", width: 1, offset: "-disp" }, { name: "선행스팬1", color: "c3", width: 1, offset: "disp" }, { name: "선행스팬2", color: "c8", width: 1, offset: "disp" }],
      fills: [{ a: 3, b: 4, cloud: true, alpha: 0.12 }],
      calc: (b, p) => {
        const H = b.map(x => x.h), L = b.map(x => x.l), mid = n => map2(highest(H, n), lowest(L, n), (x, y) => (x + y) / 2);
        const t = mid(p.conv), k = mid(p.base);
        return [t, k, src(b), map2(t, k, (x, y) => (x + y) / 2), mid(p.span)];
      },
    },
    psar: {
      name: "파라볼릭 SAR", short: "SAR", group: "추세", pane: "main", params: [P("start", "시작", 0.02, 0.001, 1, 0.001), P("inc", "증가", 0.02, 0.001, 1, 0.001), P("max", "최대", 0.2, 0.01, 1, 0.01)],
      plots: [{ name: "SAR", color: "c5", type: "dots", width: 2 }],
      calc: (b, p) => {
        const o = N(b.length); if (b.length < 2) return [o];
        let up = b[1].c >= b[0].c, af = p.start, ep = up ? b[0].h : b[0].l, sar = up ? b[0].l : b[0].h;
        for (let i = 1; i < b.length; i++) {
          sar = sar + af * (ep - sar);
          if (up) {
            sar = Math.min(sar, b[i - 1].l, i > 1 ? b[i - 2].l : b[i - 1].l);
            if (b[i].l < sar) { up = false; sar = ep; ep = b[i].l; af = p.start; }
            else if (b[i].h > ep) { ep = b[i].h; af = Math.min(p.max, af + p.inc); }
          } else {
            sar = Math.max(sar, b[i - 1].h, i > 1 ? b[i - 2].h : b[i - 1].h);
            if (b[i].h > sar) { up = true; sar = ep; ep = b[i].h; af = p.start; }
            else if (b[i].l < ep) { ep = b[i].l; af = Math.min(p.max, af + p.inc); }
          }
          o[i] = sar;
        }
        return [o];
      },
    },
    supertrend: {
      name: "슈퍼트렌드", short: "ST", group: "추세", pane: "main", params: [P("atr", "ATR 기간", 10), P("mult", "배수", 3, 0.5, 10, 0.1)],
      plots: [{ name: "상승", color: "up", width: 2 }, { name: "하락", color: "down", width: 2 }],
      calc: (b, p) => {
        const a = rma(tr(b), p.atr), m = hl2(b), up = N(b.length), dn = N(b.length);
        let fu = null, fl = null, dir = 1;
        for (let i = 0; i < b.length; i++) {
          if (a[i] == null) continue;
          const bu = m[i] + p.mult * a[i], bl = m[i] - p.mult * a[i];
          fu = fu == null || bu < fu || b[i - 1].c > fu ? bu : fu;
          fl = fl == null || bl > fl || b[i - 1].c < fl ? bl : fl;
          if (dir === 1 && b[i].c < fl) dir = -1; else if (dir === -1 && b[i].c > fu) dir = 1;
          if (dir === 1) up[i] = fl; else dn[i] = fu;
        }
        return [up, dn];
      },
    },
    // ── 거래량 (가격 창 바닥) ──
    vol: { name: "거래량", short: "거래량", group: "거래량", pane: "volume", params: [P("ma", "평균 기간", 20)], plots: [{ name: "거래량", color: "ink-3", type: "hist" }, { name: "평균", color: "ink-3", width: 1.25 }], calc: (b, p) => { const v = b.map(x => x.v); return [v, sma(v, p.ma)]; } },
    // ── 아래 창 (lower) ──
    rsi: { name: "RSI (상대강도지수)", short: "RSI", group: "모멘텀", pane: "lower", params: [len(14)], plots: [{ name: "RSI", color: "c7", width: 1.5 }], levels: [70, 50, 30], band: [30, 70], range: [0, 100], calc: (b, p) => [rsiOf(src(b), p.len)] },
    macd: {
      name: "MACD", short: "MACD", group: "모멘텀", pane: "lower", params: [P("fast", "빠른선", 12), P("slow", "느린선", 26), P("sig", "시그널", 9)],
      plots: [{ name: "히스토그램", color: "updown", type: "hist" }, { name: "MACD", color: "c1", width: 1.5 }, { name: "시그널", color: "c2", width: 1.5 }], levels: [0],
      calc: (b, p) => {
        const c = src(b), line = map2(ema(c, p.fast), ema(c, p.slow), (x, y) => x - y);
        const first = line.findIndex(v => v != null), sig = N(c.length);
        if (first >= 0) ema(line.slice(first), p.sig).forEach((v, i) => (sig[first + i] = v));
        return [map2(line, sig, (x, y) => x - y), line, sig];
      },
    },
    stoch: {
      name: "스토캐스틱", short: "Stoch", group: "모멘텀", pane: "lower", params: [P("k", "%K 기간", 14), P("sk", "%K 평활", 3), P("d", "%D 기간", 3)],
      plots: [{ name: "%K", color: "c1", width: 1.5 }, { name: "%D", color: "c2", width: 1.5 }], levels: [80, 50, 20], band: [20, 80], range: [0, 100],
      calc: (b, p) => {
        const hh = highest(b.map(x => x.h), p.k), ll = lowest(b.map(x => x.l), p.k);
        const raw = b.map((x, i) => (hh[i] == null ? null : hh[i] === ll[i] ? 50 : (100 * (x.c - ll[i])) / (hh[i] - ll[i])));
        const k = sma(raw, p.sk); return [k, sma(k, p.d)];
      },
    },
    stochrsi: {
      name: "스토캐스틱 RSI", short: "StochRSI", group: "모멘텀", pane: "lower", params: [P("rsi", "RSI 기간", 14), P("st", "스토캐스틱 기간", 14), P("k", "%K", 3), P("d", "%D", 3)],
      plots: [{ name: "%K", color: "c1", width: 1.5 }, { name: "%D", color: "c2", width: 1.5 }], levels: [80, 50, 20], band: [20, 80], range: [0, 100],
      calc: (b, p) => {
        const r = rsiOf(src(b), p.rsi), hh = highest(r, p.st), ll = lowest(r, p.st);
        const raw = r.map((x, i) => (x == null || hh[i] == null || !isFinite(hh[i]) ? null : hh[i] === ll[i] ? 50 : (100 * (x - ll[i])) / (hh[i] - ll[i])));
        const k = sma(raw, p.k); return [k, sma(k, p.d)];
      },
    },
    cci: {
      name: "CCI", short: "CCI", group: "모멘텀", pane: "lower", params: [len(20)], plots: [{ name: "CCI", color: "c5", width: 1.5 }], levels: [100, 0, -100], band: [-100, 100],
      calc: (b, p) => { const tp = hlc3(b), m = sma(tp, p.len); return [tp.map((x, i) => { if (m[i] == null) return null; let d = 0; for (let j = i - p.len + 1; j <= i; j++) d += Math.abs(tp[j] - m[i]); d /= p.len; return d ? (x - m[i]) / (0.015 * d) : 0; })]; },
    },
    willr: {
      name: "윌리엄스 %R", short: "%R", group: "모멘텀", pane: "lower", params: [len(14)], plots: [{ name: "%R", color: "c7", width: 1.5 }], levels: [-20, -50, -80], band: [-80, -20], range: [-100, 0],
      calc: (b, p) => { const hh = highest(b.map(x => x.h), p.len), ll = lowest(b.map(x => x.l), p.len); return [b.map((x, i) => (hh[i] == null ? null : hh[i] === ll[i] ? -50 : (-100 * (hh[i] - x.c)) / (hh[i] - ll[i])))]; },
    },
    mom: { name: "모멘텀", short: "MOM", group: "모멘텀", pane: "lower", params: [len(10)], plots: [{ name: "MOM", color: "c1", width: 1.5 }], levels: [0], calc: (b, p) => [b.map((x, i) => (i >= p.len ? x.c - b[i - p.len].c : null))] },
    roc: { name: "변화율 (ROC)", short: "ROC", group: "모멘텀", pane: "lower", params: [len(12)], plots: [{ name: "ROC", color: "c3", width: 1.5 }], levels: [0], calc: (b, p) => [b.map((x, i) => (i >= p.len ? (x.c / b[i - p.len].c - 1) * 100 : null))] },
    adx: {
      name: "ADX / DMI", short: "ADX", group: "추세", pane: "lower", params: [len(14)],
      plots: [{ name: "ADX", color: "c7", width: 2 }, { name: "+DI", color: "up", width: 1 }, { name: "-DI", color: "down", width: 1 }], levels: [20], range: [0, null],
      calc: (b, p) => {
        const pdm = b.map((x, i) => { if (!i) return 0; const u = x.h - b[i - 1].h, d = b[i - 1].l - x.l; return u > d && u > 0 ? u : 0; });
        const mdm = b.map((x, i) => { if (!i) return 0; const u = x.h - b[i - 1].h, d = b[i - 1].l - x.l; return d > u && d > 0 ? d : 0; });
        const a = rma(tr(b), p.len), pdi = map2(rma(pdm, p.len), a, (x, y) => (y ? (100 * x) / y : 0)), mdi = map2(rma(mdm, p.len), a, (x, y) => (y ? (100 * x) / y : 0));
        const dx = map2(pdi, mdi, (x, y) => (x + y ? (100 * Math.abs(x - y)) / (x + y) : 0));
        return [rma(dx, p.len), pdi, mdi];
      },
    },
    atr: { name: "ATR (평균 실제 범위)", short: "ATR", group: "변동성", pane: "lower", params: [len(14)], plots: [{ name: "ATR", color: "c8", width: 1.5 }], range: [0, null], calc: (b, p) => [rma(tr(b), p.len)] },
    bbw: { name: "볼린저 밴드 폭", short: "BBW", group: "변동성", pane: "lower", params: [len(20), P("mult", "배수", 2, 0.5, 5, 0.1)], plots: [{ name: "BBW", color: "c1", width: 1.5 }], range: [0, null], calc: (b, p) => { const c = src(b), m = sma(c, p.len), sd = stdev(c, p.len, m); return [map2(m, sd, (x, s) => (x ? (2 * p.mult * s) / x * 100 : null))]; } },
    obv: { name: "OBV (거래량 균형)", short: "OBV", group: "거래량", pane: "lower", params: [], plots: [{ name: "OBV", color: "c1", width: 1.5 }], calc: b => { let s = 0; return [b.map((x, i) => (i ? (s += x.c > b[i - 1].c ? x.v : x.c < b[i - 1].c ? -x.v : 0) : s))]; } },
    mfi: {
      name: "MFI (자금 흐름 지수)", short: "MFI", group: "거래량", pane: "lower", params: [len(14)], plots: [{ name: "MFI", color: "c3", width: 1.5 }], levels: [80, 50, 20], band: [20, 80], range: [0, 100],
      calc: (b, p) => { const tp = hlc3(b), o = N(b.length); for (let i = p.len; i < b.length; i++) { let pos = 0, neg = 0; for (let j = i - p.len + 1; j <= i; j++) { const f = tp[j] * b[j].v; if (tp[j] > tp[j - 1]) pos += f; else if (tp[j] < tp[j - 1]) neg += f; } o[i] = neg === 0 ? 100 : 100 - 100 / (1 + pos / neg); } return [o]; },
    },
    cmf: { name: "차이킨 자금 흐름 (CMF)", short: "CMF", group: "거래량", pane: "lower", params: [len(20)], plots: [{ name: "CMF", color: "updown", type: "area" }], levels: [0], calc: (b, p) => { const mfv = b.map(x => (x.h === x.l ? 0 : ((x.c - x.l - (x.h - x.c)) / (x.h - x.l)) * x.v)); const o = N(b.length); for (let i = p.len - 1; i < b.length; i++) { let s = 0, v = 0; for (let j = i - p.len + 1; j <= i; j++) { s += mfv[j]; v += b[j].v; } o[i] = v ? s / v : 0; } return [o]; } },
    disp: { name: "이격도", short: "이격도", group: "추세", pane: "lower", params: [len(20)], plots: [{ name: "이격도", color: "c2", width: 1.5 }], levels: [100], calc: (b, p) => { const m = sma(src(b), p.len); return [b.map((x, i) => (m[i] ? (x.c / m[i]) * 100 : null))]; } },
  };

  SC.IND = IND;
  SC.indFns = { sma, ema, rma, highest, lowest };
  SC.indDefaults = type => Object.fromEntries(IND[type].params.map(p => [p.k, p.def]));
  SC.indLabel = (type, params) => {
    const d = IND[type], vals = d.params.map(p => params[p.k]);
    return d.short + (vals.length ? " " + vals.join(" ") : "");
  };
  // 플롯 이동량(일목 같은 선행/후행 이동)
  SC.plotOffset = (plot, params) => (!plot.offset ? 0 : plot.offset[0] === "-" ? -params[plot.offset.slice(1)] : +params[plot.offset]);
})();
