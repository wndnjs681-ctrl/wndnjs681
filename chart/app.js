// ── 앱: 도구줄·그리기 도구·분할 화면·관심종목·스크리너·알림·실시간 ──
"use strict";
(function () {
  const E = SC.el;
  const LAYOUTS = { "1": "1개", "2h": "2개 (좌우)", "2v": "2개 (위아래)", "4": "4개" };
  const CMP_COLORS = ["c2", "c3", "c5", "c7", "c4", "c8"];

  const App = {
    charts: [], active: null, tool: "cursor", magnet: false, lockAll: false, hideDrawings: false, drawColor: "c1",
    undo: {}, redo: {}, drawCache: {}, keepTool: false,

    // ── 시작 ──
    async init() {
      SC.readColors();
      const st = SC.store.get("state", {});
      this.layout = st.layout || "1";
      this.magnet = !!st.magnet;
      this.alerts = SC.store.get("alerts", []);
      this.wl = SC.store.get("wl", { cur: 0, lists: [{ name: "기본", ids: ["kr:005930", "kr:000660", "kr:373220", "us:NVDA", "us:AAPL", "ix:KS11", "ix:IXIC"] }] });
      const cfgs = st.charts && st.charts.length ? st.charts : [{}];
      this.grid = document.getElementById("grid");
      this.buildToolbar(); this.buildDrawbar(); this.buildSide();
      this.applyLayout(cfgs, st.active || 0);
      SC.onData(() => { this.renderWatchlist(); this.renderInfo(); this.evalAlerts(); });
      this.bindKeys();
      const mq = matchMedia("(prefers-color-scheme: dark)"), re = () => { SC.readColors(); this.charts.forEach(c => c.draw()); };
      mq.addEventListener ? mq.addEventListener("change", re) : mq.addListener(re);
      new MutationObserver(re).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      if (document.fonts) document.fonts.ready.then(re);
      for (const mk of ["kr", "us", "ix"]) SC.loadMarket(mk).catch(() => {});
      SC.Live.init(this);
    },
    save() {
      SC.store.set("state", { layout: this.layout, active: this.charts.indexOf(this.active), magnet: this.magnet, charts: this.charts.map(c => c.cfg) });
    },

    // ── 분할 화면 ──
    applyLayout(cfgs, activeIdx) {
      const n = this.layout === "4" ? 4 : this.layout === "1" ? 1 : 2;
      const prev = this.charts.map(c => c.cfg);
      this.charts.forEach(c => c.destroy()); this.charts = [];
      this.grid.className = "grid l" + this.layout;
      for (let i = 0; i < n; i++) {
        const cfg = (cfgs && cfgs[i]) || prev[i] || (i ? { id: ["kr:000660", "us:NVDA", "ix:KS11"][(i - 1) % 3] } : {});
        const ch = new SC.Chart(this, this.grid, JSON.parse(JSON.stringify(cfg)));
        this.charts.push(ch);
        ch.setSymbol(ch.cfg.id);
      }
      this.setActive(this.charts[Math.min(activeIdx || 0, n - 1)]);
      this.layoutSel.value = this.layout;
      this.save();
    },
    setActive(ch) {
      if (this.active === ch) return;
      this.active = ch;
      this.charts.forEach(c => c.el.classList.toggle("active", c === ch && this.charts.length > 1));
      this.syncToolbar(); this.renderWatchlist(); this.renderInfo(); this.renderAlerts();
    },
    onChartChange(ch) {
      if (ch === this.active) { this.syncToolbar(); this.renderWatchlist(); this.renderInfo(); this.renderAlerts(); }
      SC.Live.resubscribe();
      this.save();
    },

    // ── 데이터 ──
    barsFor(id, iv) {
      if (SC.isMinute(iv)) return SC.Live.minuteBars(id, +iv);
      return SC.dailyBars(id) || [];
    },
    liveState(id) { return SC.Live.badge(id); },

    // ── 그림 저장·되돌리기 ──
    drawingsFor(id) { if (!this.drawCache[id]) this.drawCache[id] = SC.store.get("draw:" + id, []); return this.drawCache[id]; },
    setDrawings(id, arr) { this.drawCache[id] = arr; },
    saveDrawings(id) { SC.store.set("draw:" + id, this.drawingsFor(id).map(({ _box, ...d }) => d)); },
    pushUndo(id) { this.pushUndoRaw(id, JSON.stringify(this.drawingsFor(id))); },
    pushUndoRaw(id, json) { (this.undo[id] = this.undo[id] || []).push(json); if (this.undo[id].length > 100) this.undo[id].shift(); this.redo[id] = []; },
    popUndo(id) { if (this.undo[id]) this.undo[id].pop(); },
    undoDraw(redo) {
      const id = this.active.cfg.id, from = redo ? this.redo : this.undo, to = redo ? this.undo : this.redo;
      const s = (from[id] || []).pop(); if (!s) return;
      (to[id] = to[id] || []).push(JSON.stringify(this.drawingsFor(id)));
      this.drawCache[id] = JSON.parse(s); this.saveDrawings(id); this.active.sel = null; this.redrawSymbol(id);
    },
    redrawSymbol(id) { this.charts.forEach(c => { if (c.cfg.id === id) c.draw(); }); },
    afterDraw() { if (!this.keepTool) this.setTool("cursor"); },
    setTool(t) {
      this.tool = t;
      for (const c of this.charts) { if (c.pending) c.cancelPending(); }
      this.drawbar.querySelectorAll("[data-tool]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.tool === t)));
    },

    // ── 커서 동기화 ──
    syncCursor(src, t) { for (const c of this.charts) if (c !== src) { c.syncT = t; c.drawOver(); } },

    // ── 도구줄 ──
    buildToolbar() {
      const bar = document.getElementById("toolbar");
      const symBtn = E("button", { type: "button", class: "tb-sym", onclick: () => this.openSearch() }, E("span", { html: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>' }), E("span", { class: "tb-sym-name" }, "종목"), E("kbd", null, "/"));
      this.symBtn = symBtn;
      this.ivBox = E("div", { class: "seg", role: "group", "aria-label": "주기" });
      this.styleSel = E("select", { class: "tb-select", "aria-label": "차트 형식", onchange: e => { this.active.cfg.style = e.target.value; this.active.rebuild(); this.save(); } }, Object.entries(SC.STYLES).map(([k, v]) => E("option", { value: k }, v)));
      this.scaleBox = E("div", { class: "seg", role: "group", "aria-label": "가격축" }, [["normal", "일반"], ["log", "로그"], ["percent", "%"]].map(([k, v]) => E("button", { type: "button", "data-v": k, onclick: () => { this.active.cfg.scale = k; this.active.manual = null; this.active.draw(); this.syncToolbar(); this.save(); } }, v)));
      this.layoutSel = E("select", { class: "tb-select", "aria-label": "화면 분할", onchange: e => { this.layout = e.target.value; this.applyLayout(null, this.charts.indexOf(this.active)); } }, Object.entries(LAYOUTS).map(([k, v]) => E("option", { value: k }, "화면 " + v)));
      const btn = (label, fn, title) => E("button", { type: "button", class: "tb-btn", title: title || label, onclick: fn }, label);
      bar.append(symBtn, this.ivBox, this.styleSel,
        btn("지표", () => this.openIndicators(), "지표 추가 (/ 다음 검색)"), btn("비교", () => this.openCompare(), "다른 종목 겹쳐 보기"),
        this.scaleBox, this.layoutSel,
        btn("리플레이", () => (this.active.replay ? this.active.stopReplay() : this.active.startReplayPick()), "과거 시점부터 봉을 하나씩 재생"),
        btn("알림", () => { this.tab("alerts"); document.getElementById("alertPrice").focus(); }, "가격 알림 만들기"),
        btn("스냅샷", () => this.snapshot(), "차트 이미지 복사"),
        btn("전체화면", () => this.fullscreen()),
        E("span", { class: "tb-live", id: "liveStatus" }));
    },
    syncToolbar() {
      const c = this.active; if (!c) return;
      this.symBtn.querySelector(".tb-sym-name").textContent = SC.nameOf(c.cfg.id);
      const ivs = [["D", "일"], ["W", "주"], ["M", "월"]];
      if (SC.Live.on) ivs.unshift(["1", "1분"], ["5", "5분"], ["15", "15분"], ["60", "60분"]);
      this.ivBox.replaceChildren(...ivs.map(([k, v]) => E("button", { type: "button", "aria-pressed": String(c.cfg.interval === k), onclick: () => this.setInterval(k) }, v)));
      this.styleSel.value = c.cfg.style;
      const mode = c.scaleMode();
      this.scaleBox.querySelectorAll("button").forEach(b => { b.setAttribute("aria-pressed", String(b.dataset.v === mode)); b.disabled = c.cfg.compare.length > 0 && b.dataset.v !== "percent"; });
    },
    setInterval(iv) {
      const c = this.active; c.cfg.interval = iv; if (c.replay) c.stopReplay();
      if (SC.isMinute(iv)) SC.Live.loadMinutes(c.cfg.id).then(() => c.rebuild(true));
      c.rebuild(true); this.syncToolbar(); this.save();
    },

    // ── 왼쪽 그리기 도구 ──
    buildDrawbar() {
      const bar = (this.drawbar = document.getElementById("drawbar"));
      const groups = [["cursor"], ["trend", "ray", "ext", "hline", "hray", "vline", "channel"], ["rect", "fib", "measure", "text"], ["long", "short"]];
      for (const g of groups) {
        for (const t of g) {
          const T = SC.TOOLS[t];
          bar.append(E("button", { type: "button", class: "db-btn", "data-tool": t, "aria-pressed": String(t === "cursor"), title: T.name + (T.key && t !== "cursor" ? ` (Alt+${T.key})` : ""), "aria-label": T.name, onclick: () => this.setTool(this.tool === t && t !== "cursor" ? "cursor" : t), html: SC.icon(t) }));
        }
        bar.append(E("span", { class: "db-sep" }));
      }
      const toggle = (key, icon, label) => E("button", { type: "button", class: "db-btn", "aria-pressed": String(this[key]), title: label, "aria-label": label, html: SC.icon(icon), onclick: e => { this[key] = !this[key]; e.currentTarget.setAttribute("aria-pressed", String(this[key])); this.charts.forEach(c => c.draw()); this.save(); } });
      bar.append(
        toggle("magnet", "magnet", "자석: 시가·고가·저가·종가에 붙이기"),
        toggle("keepTool", "trend", "그린 뒤에도 도구 유지"),
        toggle("lockAll", "lock", "모든 그림 잠그기"),
        toggle("hideDrawings", "eye", "모든 그림 숨기기"),
        E("span", { class: "db-sep" }),
        E("button", { type: "button", class: "db-btn", title: "되돌리기 (Ctrl+Z)", "aria-label": "되돌리기", html: SC.icon("undo"), onclick: () => this.undoDraw(false) }),
        E("button", { type: "button", class: "db-btn", title: "다시 하기 (Ctrl+Y)", "aria-label": "다시 하기", html: SC.icon("redo"), onclick: () => this.undoDraw(true) }),
        E("button", { type: "button", class: "db-btn", title: "이 종목 그림 모두 지우기", "aria-label": "그림 모두 지우기", html: SC.icon("trash"), onclick: () => this.clearDrawings() }));
      bar.querySelector('[title^="그린 뒤에도"]').innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M4 20 15 9M15 9l2-5 3 3-5 2z"/><path d="M4 4h6M4 8h3"/></svg>';
    },
    clearDrawings() {
      const id = this.active.cfg.id, n = this.drawingsFor(id).length; if (!n) return;
      const close = SC.modal("그림 모두 지우기", E("p", null, `${SC.nameOf(id)}에 그린 ${n}개를 지웁니다. 되돌리기(Ctrl+Z)로 복구할 수 있습니다.`),
        [{ label: "취소" }, { label: "지우기", primary: true, onclick: () => { this.pushUndo(id); this.drawCache[id] = []; this.saveDrawings(id); this.active.sel = null; this.redrawSymbol(id); } }]);
      return close;
    },

    // ── 종목 검색 ──
    searchList(q, filterMk) {
      const s = q.trim().toLowerCase(), out = [[], [], []];
      for (const n of SC.db.names) {
        if (filterMk && filterMk !== "all" && n.mk !== filterMk) continue;
        if (!s) { if (out[2].length < 60) out[2].push(n); continue; }
        const t = n.t.toLowerCase(), nm = n.name.toLowerCase();
        if (t === s || nm === s) out[0].push(n); else if (t.startsWith(s) || nm.startsWith(s)) out[1].push(n); else if (nm.includes(s) || t.includes(s) || (n.sector || "").toLowerCase().includes(s)) out[2].push(n);
      }
      return out.flat().slice(0, 80);
    },
    pickSymbolDialog(title, onPick, initial) {
      let sel = 0, hits = [], mk = "all";
      const input = E("input", { type: "search", class: "dlg-search", placeholder: "종목명·코드·업종 (삼성, 005930, NVDA)", "aria-label": "종목 검색", autocomplete: "off" });
      const tabs = E("div", { class: "seg small" }, [["all", "전체"], ["kr", "국내"], ["us", "미국"], ["ix", "지수"]].map(([k, v]) => E("button", { type: "button", "aria-pressed": String(k === "all"), onclick: e => { mk = k; tabs.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b === e.currentTarget))); render(); } }, v)));
      const list = E("div", { class: "sym-list", role: "listbox" });
      let close;
      const render = () => {
        hits = this.searchList(input.value, mk); sel = SC.clamp(sel, 0, Math.max(0, hits.length - 1));
        list.replaceChildren(...(hits.length ? hits.map((n, i) => E("button", { type: "button", role: "option", "aria-selected": String(i === sel), onclick: () => { close(); onPick(n.id); } },
          E("span", { class: "sl-name" }, n.name), E("span", { class: "sl-t num" }, n.t), E("span", { class: "sl-sec" }, n.sector || ""), E("span", { class: "sl-mk" }, SC.MK[n.mk]))) : [E("p", { class: "empty" }, SC.db.names.length ? "일치하는 종목이 없습니다. 미국 종목은 영문 이름이나 심볼로 찾아보세요." : "종목 목록을 불러오는 중…")]));
        const s = list.children[sel]; if (s && s.scrollIntoView) s.scrollIntoView({ block: "nearest" });
      };
      input.addEventListener("input", () => { sel = 0; render(); });
      input.addEventListener("keydown", e => {
        if (e.key === "ArrowDown") { sel++; render(); e.preventDefault(); } else if (e.key === "ArrowUp") { sel--; render(); e.preventDefault(); }
        else if (e.key === "Enter" && hits[sel]) { close(); onPick(hits[sel].id); }
      });
      input.value = initial || "";
      close = SC.modal(title, E("div", { class: "pick" }, input, tabs, list), [], { wide: true });
      render(); setTimeout(() => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }, 0);
      SC.onData(render);
    },
    openSearch(ch, initial) { const c = ch || this.active; this.setActive(c); this.pickSymbolDialog("종목 찾기", id => c.setSymbol(id), initial); },

    // ── 지표 ──
    openIndicators() {
      const c = this.active, q = E("input", { type: "search", class: "dlg-search", placeholder: "지표 이름 (RSI, 이동평균, 일목…)", "aria-label": "지표 검색" });
      const list = E("div", { class: "ind-list" });
      const PANE = { main: "가격 위", volume: "가격 위", lower: "아래 창" };
      let close;
      const render = () => {
        const s = q.value.trim().toLowerCase(), groups = {};
        for (const [k, d] of Object.entries(SC.IND)) { if (s && !(d.name + d.short + k).toLowerCase().includes(s)) continue; (groups[d.group] = groups[d.group] || []).push([k, d]); }
        list.replaceChildren(...Object.entries(groups).map(([g, arr]) => E("section", null, E("h4", null, g), arr.map(([k, d]) => E("button", { type: "button", class: "ind-item", onclick: () => { c.cfg.inds.push({ uid: SC.uid(), type: k, params: SC.indDefaults(k), colors: [], hidden: false }); c.rebuild(); this.save(); SC.toast(`${d.short} 추가했습니다`); } }, E("span", null, d.name), E("span", { class: "ind-pane" }, PANE[d.pane]))))));
        if (!list.children.length) list.append(E("p", { class: "empty" }, "일치하는 지표가 없습니다."));
      };
      q.addEventListener("input", render);
      const cur = E("div", { class: "ind-cur" }, E("h4", null, "지금 켜진 지표"), c.cfg.inds.length ? c.cfg.inds.map(ind => E("span", { class: "chip" }, SC.indLabel(ind.type, ind.params))) : E("span", { class: "muted" }, "없음"));
      close = SC.modal(`지표 추가 · ${SC.nameOf(c.cfg.id)}`, E("div", { class: "pick" }, q, cur, list), [{ label: "닫기", primary: true }], { wide: true });
      render();
    },
    openIndSettings(ch, ind) {
      const def = SC.IND[ind.type], inputs = {}, colors = {};
      const body = E("div", { class: "form" });
      if (def.params.length) body.append(E("h4", null, "값"));
      for (const p of def.params) {
        const inp = E("input", { type: "number", id: "p-" + p.k, min: p.min, max: p.max, step: p.step, value: ind.params[p.k] });
        inputs[p.k] = inp; body.append(E("label", { class: "row", for: "p-" + p.k }, E("span", null, p.label), inp));
      }
      const plots = def.plots.map((pl, k) => [pl, k]).filter(([pl]) => pl.color !== "updown");
      if (plots.length) body.append(E("h4", null, "색"));
      for (const [pl, k] of plots) {
        const inp = E("input", { type: "color", id: "c-" + k, value: SC.toHex(ind.colors[k] || pl.color) });
        colors[k] = inp; body.append(E("label", { class: "row", for: "c-" + k }, E("span", null, pl.name), inp));
      }
      SC.modal(`${def.name} 설정`, body, [
        { label: "기본값", onclick: () => { for (const p of def.params) inputs[p.k].value = p.def; for (const [pl, k] of plots) colors[k].value = SC.toHex(pl.color); return false; } },
        { label: "취소" },
        { label: "적용", primary: true, onclick: () => {
          for (const p of def.params) { const v = parseFloat(inputs[p.k].value); if (isFinite(v)) ind.params[p.k] = SC.clamp(v, p.min, p.max); }
          ind.colors = []; for (const [pl, k] of plots) if (colors[k].value.toLowerCase() !== SC.toHex(pl.color).toLowerCase()) ind.colors[k] = colors[k].value;
          ch.rebuild(); this.save();
        } }]);
    },

    // ── 비교 ──
    openCompare() {
      const c = this.active;
      this.pickSymbolDialog(`${SC.nameOf(c.cfg.id)}와 비교할 종목`, id => {
        if (id === c.cfg.id || c.cfg.compare.some(x => x.id === id)) return;
        if (c.cfg.compare.length >= 5) { SC.toast("비교 종목은 5개까지입니다."); return; }
        const used = c.cfg.compare.map(x => x.color), color = CMP_COLORS.find(k => !used.includes(k)) || "c2";
        c.cfg.compare.push({ id, color });
        SC.loadMarket(SC.mkOf(id)).then(() => { c.manual = null; c.rebuild(); this.syncToolbar(); this.save(); });
      });
    },

    // ── 사이드바 ──
    buildSide() {
      const side = document.getElementById("side");
      const tabs = [["wl", "관심종목"], ["scr", "스크리너"], ["alerts", "알림"], ["info", "정보"]];
      this.tabBar = E("div", { class: "tabs", role: "tablist" }, tabs.map(([k, v]) => E("button", { type: "button", role: "tab", "data-tab": k, "aria-selected": String(k === "wl"), onclick: () => this.tab(k) }, v)));
      this.panels = {};
      for (const [k] of tabs) this.panels[k] = E("div", { class: "panel", role: "tabpanel", hidden: k !== "wl" });
      side.append(this.tabBar, ...Object.values(this.panels));
      this.buildWatchPanel(); this.buildScreener(); this.buildAlertsPanel();
      this.tab(SC.store.get("tab", "wl"));
    },
    tab(k) {
      this.tabBar.querySelectorAll("button").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === k)));
      for (const [key, p] of Object.entries(this.panels)) p.hidden = key !== k;
      SC.store.set("tab", k);
      if (k === "scr") this.runScreener();
    },

    // 관심종목 (여러 목록)
    buildWatchPanel() {
      const p = this.panels.wl;
      this.wlSel = E("select", { class: "tb-select grow", "aria-label": "관심종목 목록", onchange: e => { this.wl.cur = +e.target.value; this.saveWl(); this.renderWatchlist(); } });
      const menu = E("div", { class: "wl-tools" }, this.wlSel,
        E("button", { type: "button", class: "mini", title: "새 목록", onclick: () => this.nameDialog("새 관심종목 목록", "", n => { this.wl.lists.push({ name: n, ids: [] }); this.wl.cur = this.wl.lists.length - 1; this.saveWl(); this.renderWatchlist(); }) }, "+ 목록"),
        E("button", { type: "button", class: "mini", title: "이름 바꾸기", onclick: () => this.nameDialog("목록 이름 바꾸기", this.curList().name, n => { this.curList().name = n; this.saveWl(); this.renderWatchlist(); }) }, "이름"),
        E("button", { type: "button", class: "mini", title: "목록 삭제", onclick: () => { if (this.wl.lists.length < 2) { SC.toast("목록이 하나뿐이라 지울 수 없습니다."); return; } SC.modal("목록 삭제", E("p", null, `‘${this.curList().name}’ 목록을 지웁니다.`), [{ label: "취소" }, { label: "삭제", primary: true, onclick: () => { this.wl.lists.splice(this.wl.cur, 1); this.wl.cur = 0; this.saveWl(); this.renderWatchlist(); } }]); } }, "삭제"));
      this.wlAdd = E("button", { type: "button", class: "wl-add", onclick: () => { const id = this.active.cfg.id, L = this.curList(); if (!L.ids.includes(id)) { L.ids.push(id); this.saveWl(); this.renderWatchlist(); SC.Live.resubscribe(); } } }, "+ 지금 종목 담기");
      this.wlHead = E("div", { class: "wl-head" }, E("span", null, "종목"), E("span", null, "현재가"), E("span", null, "등락"));
      this.wlList = E("ul", { class: "wl" });
      p.append(menu, this.wlAdd, this.wlHead, this.wlList);
    },
    curList() { return this.wl.lists[this.wl.cur] || this.wl.lists[0]; },
    saveWl() { SC.store.set("wl", this.wl); },
    allWatchIds() { return [...new Set(this.wl.lists.flatMap(l => l.ids))]; },
    nameDialog(title, value, fn) {
      const inp = E("input", { type: "text", id: "nameInput", value, maxlength: "30", "aria-label": "이름" });
      SC.modal(title, E("div", { class: "form" }, inp), [{ label: "취소" }, { label: "저장", primary: true, onclick: () => { const v = inp.value.trim(); if (!v) return false; fn(v); } }]);
      inp.addEventListener("keydown", e => { if (e.key === "Enter") e.target.closest(".modal").querySelector(".btn.primary").click(); });
    },
    quote(id) {
      const b = this.barsFor(id, "D"); if (!b || b.length < 2) return null;
      const l = b[b.length - 1].c, p = b[b.length - 2].c; return { l, chg: (l / p - 1) * 100 };
    },
    renderWatchlist() {
      if (!this.wlList || !this.active) return;
      this.wlSel.replaceChildren(...this.wl.lists.map((l, i) => E("option", { value: i, selected: i === this.wl.cur }, `${l.name} (${l.ids.length})`)));
      const L = this.curList(), cur = this.active.cfg.id;
      this.wlList.replaceChildren(...L.ids.map(id => {
        const [mk, t] = id.split(":"), q = SC.ready(id) ? this.quote(id) : null, live = SC.Live.badge(id);
        const pick = E("button", { type: "button", class: "wl-pick", onclick: () => this.active.setSymbol(id) },
          E("span", { class: "n" }, SC.ready(id) ? SC.nameOf(id) : t, live ? E("i", { class: "live-mini " + live.cls, title: live.title }) : null),
          E("span", { class: "p num" }, q ? SC.fmtPrice(q.l, id) : "…"),
          E("span", { class: "c num " + (q ? SC.cls(q.chg) : "flat") }, q ? SC.fmtPct(q.chg) : ""),
          E("span", { class: "t" }, `${t} · ${SC.MK[mk]}`));
        const del = E("button", { type: "button", class: "del", "aria-label": `${t} 빼기`, title: "빼기", onclick: () => { L.ids = L.ids.filter(x => x !== id); this.saveWl(); this.renderWatchlist(); } }, "×");
        return E("li", id === cur ? { class: "on" } : null, pick, del);
      }));
      if (!L.ids.length) this.wlList.append(E("li", { class: "empty" }, "‘+ 지금 종목 담기’로 차트에 띄운 종목을 담으세요."));
      const has = L.ids.includes(cur); this.wlAdd.disabled = has; this.wlAdd.textContent = has ? "이 목록에 담겨 있음" : `+ ${SC.nameOf(cur)} 담기`;
    },

    // 스크리너 (universe_*.json 의 지표로 거르기)
    buildScreener() {
      const p = this.panels.scr;
      this.scr = { mk: "kr", preset: "hi52", conds: [], sort: { k: "mcap", dir: -1 } };
      const PRESETS = {
        hi52: ["52주 신고가", [["is_hi252", "is", 1]]],
        align: ["이동평균 정배열", [["ma_align", "is", 1]]],
        golden: ["골든크로스", [["golden_cross", "is", 1]]],
        oversold: ["RSI 과매도 (30 이하)", [["rsi14", "<=", 30]]],
        overbought: ["RSI 과매수 (70 이상)", [["rsi14", ">=", 70]]],
        volburst: ["거래량 급증 (20일 평균 2배)", [["vol_x20", ">=", 2]]],
        value: ["저PER·고ROE", [["per", ">", 0], ["per", "<=", 12], ["roe", ">=", 12]]],
        obv: ["OBV 신고가", [["obv_high", "is", 1]]],
        custom: ["직접 조건", []],
      };
      this.PRESETS = PRESETS;
      const mkSel = E("select", { class: "tb-select", "aria-label": "시장", onchange: e => { this.scr.mk = e.target.value; this.renderCondEditor(); this.runScreener(); } }, E("option", { value: "kr" }, "국내"), E("option", { value: "us" }, "미국"));
      const preSel = E("select", { class: "tb-select grow", "aria-label": "조건 묶음", onchange: e => { this.scr.preset = e.target.value; this.scr.conds = PRESETS[e.target.value][1].map(c => [...c]); this.renderCondEditor(); this.runScreener(); } }, Object.entries(PRESETS).map(([k, [v]]) => E("option", { value: k }, v)));
      this.scr.conds = PRESETS.hi52[1].map(c => [...c]);
      this.condBox = E("div", { class: "conds" });
      this.scrCount = E("p", { class: "scr-count" });
      this.scrTable = E("div", { class: "scr-table" });
      p.append(E("div", { class: "wl-tools" }, mkSel, preSel), this.condBox, this.scrCount, this.scrTable);
    },
    schemaFields(mk) { const m = SC.db[mk]; return m ? m.schema.filter(s => s.type === "num" || s.type === "bool") : []; },
    renderCondEditor() {
      const fields = this.schemaFields(this.scr.mk);
      if (!fields.length) { this.condBox.replaceChildren(E("p", { class: "muted" }, "데이터를 불러오는 중…")); return; }
      const rows = this.scr.conds.map((c, i) => {
        const f = fields.find(x => x.key === c[0]) || fields[0];
        const fieldSel = E("select", { "aria-label": "항목", onchange: e => { const nf = fields.find(x => x.key === e.target.value); c[0] = nf.key; c[1] = nf.type === "bool" ? "is" : ">="; c[2] = nf.type === "bool" ? 1 : 0; this.renderCondEditor(); this.runScreener(); } }, fields.map(x => E("option", { value: x.key, selected: x.key === f.key }, `${x.label}${x.unit ? " (" + x.unit + ")" : ""}`)));
        const ctrl = f.type === "bool"
          ? E("select", { "aria-label": "값", onchange: e => { c[2] = +e.target.value; this.runScreener(); } }, E("option", { value: 1, selected: c[2] == 1 }, "예"), E("option", { value: 0, selected: c[2] == 0 }, "아니오"))
          : E("span", { class: "cmp" }, E("select", { "aria-label": "비교", onchange: e => { c[1] = e.target.value; this.runScreener(); } }, [">=", "<=", ">", "<"].map(o => E("option", { value: o, selected: c[1] === o }, o))), E("input", { type: "number", "aria-label": "기준값", value: c[2], step: "any", onchange: e => { c[2] = parseFloat(e.target.value) || 0; this.runScreener(); } }));
        return E("div", { class: "cond" }, fieldSel, ctrl, E("button", { type: "button", class: "del", "aria-label": "조건 빼기", onclick: () => { this.scr.conds.splice(i, 1); this.renderCondEditor(); this.runScreener(); } }, "×"));
      });
      this.condBox.replaceChildren(...rows, E("button", { type: "button", class: "mini", onclick: () => { const f = fields[0]; this.scr.conds.push([f.key, f.type === "bool" ? "is" : ">=", f.type === "bool" ? 1 : 0]); this.renderCondEditor(); this.runScreener(); } }, "+ 조건 추가"));
    },
    runScreener() {
      const m = SC.db[this.scr.mk];
      if (!m) { this.scrCount.textContent = "데이터를 불러오는 중…"; SC.loadMarket(this.scr.mk).then(() => { this.renderCondEditor(); this.runScreener(); }).catch(() => {}); return; }
      if (!this.condBox.children.length) this.renderCondEditor();
      const pass = r => this.scr.conds.every(([k, op, v]) => { const x = r[k]; if (op === "is") return !!x === !!+v; if (x == null || !isFinite(x)) return false; return op === ">=" ? x >= v : op === "<=" ? x <= v : op === ">" ? x > v : x < v; });
      const rows = Object.values(m.rows).filter(pass);
      const { k, dir } = this.scr.sort;
      rows.sort((a, b) => ((a[k] ?? -Infinity) > (b[k] ?? -Infinity) ? 1 : -1) * dir);
      this.scrCount.textContent = `${rows.length}종목 · ${m.asof} 기준`;
      const extra = this.scr.conds.map(c => c[0]).find(x => !["is_hi252", "ma_align", "golden_cross", "obv_high"].includes(x)) || "ret20";
      const lab = (this.schemaFields(this.scr.mk).find(x => x.key === extra) || { label: extra }).label;
      const cols = [["name", "종목"], ["price", "종가"], ["chg1d", "등락"], [extra, lab]];
      const head = E("div", { class: "scr-row head" }, cols.map(([key, l]) => E("button", { type: "button", class: "scr-h", "aria-sort": key === k ? (dir > 0 ? "ascending" : "descending") : "none", onclick: () => { this.scr.sort = { k: key, dir: k === key ? -dir : -1 }; this.runScreener(); } }, l + (key === k ? (dir > 0 ? " ▲" : " ▼") : ""))));
      const id0 = this.active && this.active.cfg.id;
      const body = rows.slice(0, 300).map(r => {
        const id = this.scr.mk + ":" + r.ticker;
        return E("button", { type: "button", class: "scr-row" + (id === id0 ? " on" : ""), onclick: () => this.active.setSymbol(id) },
          E("span", { class: "n" }, r.name || r.ticker, E("small", { class: "num" }, r.ticker)), E("span", { class: "num" }, SC.fmtPrice(r.price, id)), E("span", { class: "num " + SC.cls(r.chg1d) }, SC.fmtPct(r.chg1d)), E("span", { class: "num" }, typeof r[extra] === "number" ? SC.fmtNum(r[extra], Math.abs(r[extra]) >= 100 ? 0 : 1) : r[extra] ? "예" : "—"));
      });
      this.scrTable.replaceChildren(head, ...body);
      if (rows.length > 300) this.scrTable.append(E("p", { class: "muted" }, `상위 300개만 보여줍니다. 조건을 더 좁혀 보세요.`));
    },

    // 알림
    buildAlertsPanel() {
      const p = this.panels.alerts;
      this.alertPrice = E("input", { type: "number", id: "alertPrice", step: "any", "aria-label": "알림 가격" });
      this.alertTitle = E("p", { class: "muted" });
      const form = E("form", { class: "alert-form", onsubmit: e => { e.preventDefault(); const v = parseFloat(this.alertPrice.value); if (isFinite(v) && v > 0) this.addAlert(this.active.cfg.id, v); } },
        this.alertTitle, E("div", { class: "row" }, this.alertPrice, E("button", { type: "submit", class: "btn primary" }, "알림 만들기")));
      this.alertList = E("ul", { class: "alerts" });
      p.append(form, E("p", { class: "muted small" }, "가격이 이 선을 지나면 ‘도달’로 표시됩니다. 실시간 연결 중에는 바로, 아니면 다음 데이터 갱신 때 확인합니다. 수평선 그림의 ‘이 가격에 알림’으로도 만들 수 있습니다."), this.alertList);
    },
    alertsFor(id) { return this.alerts.filter(a => a.id === id); },
    addAlert(id, price) {
      const b = this.barsFor(id, "D"), last = b[b.length - 1];
      this.alerts.push({ uid: SC.uid(), id, price, dir: price >= last.c ? "up" : "down", from: last.t, created: Date.now(), hit: null });
      SC.store.set("alerts", this.alerts); this.renderAlerts(); this.redrawSymbol(id);
      SC.toast(`${SC.nameOf(id)} ${SC.fmtPrice(price, id)} ${price >= last.c ? "이상" : "이하"} 알림을 만들었습니다`);
    },
    evalAlerts(liveTick) {
      let changed = false;
      for (const a of this.alerts) {
        if (a.hit || !SC.ready(a.id)) continue;
        const b = this.barsFor(a.id, "D");
        for (const x of b) { if (x.t <= a.from && !(liveTick && liveTick.id === a.id && x === b[b.length - 1])) continue; const hi = x.ohlc ? x.h : x.c, lo = x.ohlc ? x.l : x.c; if ((a.dir === "up" && hi >= a.price) || (a.dir === "down" && lo <= a.price)) { a.hit = x.t; changed = true; SC.toast(`알림: ${SC.nameOf(a.id)}이(가) ${SC.fmtPrice(a.price, a.id)}에 도달했습니다`); break; } }
      }
      if (changed) { SC.store.set("alerts", this.alerts); this.renderAlerts(); this.charts.forEach(c => c.draw()); }
    },
    renderAlerts() {
      if (!this.alertList || !this.active) return;
      const id = this.active.cfg.id, q = SC.ready(id) ? this.quote(id) : null;
      this.alertTitle.textContent = `${SC.nameOf(id)} · 현재 ${q ? SC.fmtPrice(q.l, id) : "—"}`;
      if (q && document.activeElement !== this.alertPrice) this.alertPrice.value = q.l;
      this.alertList.replaceChildren(...this.alerts.slice().reverse().map(a => E("li", { class: a.hit ? "hit" : "" },
        E("button", { type: "button", class: "al-pick", onclick: () => this.active.setSymbol(a.id) },
          E("span", { class: "n" }, SC.nameOf(a.id)),
          E("span", { class: "num" }, `${SC.fmtPrice(a.price, a.id)} ${a.dir === "up" ? "이상" : "이하"}`),
          E("span", { class: "st" }, a.hit ? `도달 ${SC.fmtDate(a.hit, "D")}` : "대기 중")),
        E("button", { type: "button", class: "del", "aria-label": "알림 삭제", onclick: () => { this.alerts = this.alerts.filter(x => x !== a); SC.store.set("alerts", this.alerts); this.renderAlerts(); this.redrawSymbol(a.id); } }, "×"))));
      if (!this.alerts.length) this.alertList.append(E("li", { class: "empty" }, "아직 알림이 없습니다."));
    },

    // 종목 정보
    renderInfo() {
      const p = this.panels && this.panels.info; if (!p || !this.active) return;
      const id = this.active.cfg.id, [mk, t] = id.split(":"), kids = [E("h3", { class: "info-name" }, SC.nameOf(id), E("small", { class: "num" }, ` ${t} · ${SC.MK[mk]}`))];
      if (!SC.ready(id)) { p.replaceChildren(...kids, E("p", { class: "muted" }, "불러오는 중…")); return; }
      const dl = rows => E("dl", { class: "kv num" }, rows.map(([k, v, c]) => [E("dt", null, k), E("dd", c ? { class: c } : null, v)]));
      if (mk === "ix") {
        const b = this.barsFor(id, "D"), cs = b.map(x => x.c), last = cs[cs.length - 1];
        kids.push(dl([["현재", SC.fmtPrice(last, id)], ["전일 대비", SC.fmtPct((last / cs[cs.length - 2] - 1) * 100), SC.cls(last - cs[cs.length - 2])], ["1년 수익률", SC.fmtPct((last / cs[Math.max(0, cs.length - 252)] - 1) * 100)], ["기준", SC.db.ix.asof]]));
      } else {
        const r = SC.db[mk].rows[t] || {}, f1 = (v, u = "") => (v == null || !isFinite(v) ? "—" : v.toFixed(1) + u);
        kids.push(E("p", { class: "muted" }, r.sector || ""));
        kids.push(dl([["시가총액", SC.fmtMoney(r.mcap, mk)], ["거래대금", SC.fmtMoney(r.value, mk)], ["PER", f1(r.per, "배")], ["PBR", f1(r.pbr, "배")], ["ROE", f1(r.roe, "%")], ["배당수익률", f1(r.dvd_yld, "%")], ["RSI 14", f1(r.rsi14)],
          ["5일 수익률", SC.fmtPct(r.ret5), SC.cls(r.ret5)], ["20일 수익률", SC.fmtPct(r.ret20), SC.cls(r.ret20)], ["60일 수익률", SC.fmtPct(r.ret60), SC.cls(r.ret60)], ["120일 수익률", SC.fmtPct(r.ret120), SC.cls(r.ret120)], ["기준일", SC.db[mk].asof]]));
        if (r.hi252 && r.lo252) {
          const pct = SC.clamp((r.price - r.lo252) / (r.hi252 - r.lo252) * 100, 0, 100);
          kids.push(E("div", { class: "range" }, E("div", { class: "muted small" }, `52주 범위 · 현재 ${pct.toFixed(0)}% 위치`), E("div", { class: "bar" }, E("span", { class: "dot", style: `left:${pct}%` })), E("div", { class: "ends num" }, E("span", null, SC.fmtPrice(r.lo252, id)), E("span", null, SC.fmtPrice(r.hi252, id)))));
        }
        const flags = [["is_hi252", "52주 신고가"], ["ma_align", "정배열"], ["golden_cross", "골든크로스"], ["obv_high", "OBV 신고가"], ["obv_diverge", "OBV 다이버전스"]].filter(([k]) => r[k]);
        if (flags.length) kids.push(E("div", { class: "flags" }, flags.map(([, l]) => E("span", { class: "chip" }, l))));
      }
      p.replaceChildren(...kids);
    },

    // ── 기타 ──
    async snapshot() {
      const c = this.active.snapshot();
      try {
        const blob = await new Promise(r => c.toBlob(r, "image/png"));
        await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
        SC.toast("차트 이미지를 클립보드에 복사했습니다");
      } catch {
        if (window.top === window) { const a = E("a", { href: c.toDataURL("image/png"), download: `${this.active.cfg.id.replace(":", "_")}.png` }); a.click(); SC.toast("차트 이미지를 내려받았습니다"); }
        else SC.toast("이 화면에서는 이미지 복사가 막혀 있습니다. 내 PC에서 열면 저장할 수 있습니다.");
      }
    },
    fullscreen() {
      const el = document.documentElement;
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      else if (el.requestFullscreen) el.requestFullscreen().catch(() => SC.toast("이 화면에서는 전체화면을 쓸 수 없습니다."));
    },
    bindKeys() {
      document.addEventListener("keydown", e => {
        if (e.target.closest && e.target.closest("input,textarea,select,.modal")) return;
        const c = this.active; if (!c) return;
        const k = e.key, ctrl = e.ctrlKey || e.metaKey;
        if (ctrl && (k === "z" || k === "Z")) { e.preventDefault(); this.undoDraw(e.shiftKey); return; }
        if (ctrl && (k === "y" || k === "Y")) { e.preventDefault(); this.undoDraw(true); return; }
        if (e.altKey) { const t = Object.entries(SC.TOOLS).find(([, v]) => v.key && v.key.toLowerCase() === k.toLowerCase()); if (t) { e.preventDefault(); this.setTool(t[0]); } return; }
        if (k === "Escape") { if (c.replayPick) { c.replayPick = false; c.hideHint(); c.drawOver(); } c.cancelPending(); this.setTool("cursor"); if (c.sel) { c.sel = null; c.draw(); } return; }
        if ((k === "Delete" || k === "Backspace") && c.sel) { e.preventDefault(); c.deleteSelected(); return; }
        if (k === "/") { e.preventDefault(); this.openSearch(); return; }
        if (k === "ArrowLeft" || k === "ArrowRight") { c.end += (k === "ArrowLeft" ? -1 : 1) * Math.max(1, c.count * 0.1); c.clampView(); c.draw(); return; }
        if (k === "+" || k === "=") { c.count *= 0.8; c.clampView(); c.draw(); return; }
        if (k === "-") { c.count *= 1.25; c.clampView(); c.draw(); return; }
        if (!ctrl && k.length === 1 && /[0-9a-zA-Z가-힣]/.test(k)) { e.preventDefault(); this.openSearch(c, k); }
      });
    },
  };

  SC.App = App;
  document.addEventListener("DOMContentLoaded", () => App.init());
})();
