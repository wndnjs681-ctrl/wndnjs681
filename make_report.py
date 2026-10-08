#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
추세·모멘텀 TOP5 모닝 리포트 (HTML) 만들기

입력 (같은 폴더 기준, OUT_DIR 로 바꿀 수 있음)
  top5_kr.json, top5_us.json         ← daily_top5.py
  series_kr.json, series_us.json     ← screener_full.py (스파크라인)
  market_index.json                  ← market_index.py (지수 등락)
  report_comments.json               ← 리포트 작성자가 쓰는 종목별 코멘트
    {"asof": "2026-10-08 07:30",
     "summary": {"kr": "한두 문장", "us": "한두 문장"},
     "kr": {"010950": {"take": "한 줄 요약", "news": [{"t": "내용", "src": [["매체", "https://..."]]}], "risk": "한 줄"}},
     "us": {...}}
사용: python3 make_report.py [출력 파일]   (기본 report.html)
"""
import html, json, os, sys
from datetime import datetime, timedelta, timezone

D = os.getenv("OUT_DIR", "output")
KST = timezone(timedelta(hours=9))
WD = "월화수목금토일"
e = lambda s: html.escape(str(s if s is not None else ""), quote=True)


def jl(name, default=None):
    try:
        return json.load(open(os.path.join(D, name), encoding="utf-8"))
    except Exception:
        return default


def fnum(v, mk, kind="px"):
    if v is None:
        return "–"
    if kind == "px":
        return f"${v:,.2f}" if mk == "us" else f"{v:,.0f}원"
    if kind == "cap":
        return (f"${v / 1000:,.1f}B" if v >= 1000 else f"${v:,.0f}M") if mk == "us" else (f"{v / 10000:,.1f}조" if v >= 10000 else f"{v:,.0f}억")
    return str(v)


def sgn(v, d=1, suf="%"):
    if v is None:
        return "–"
    return f"{'+' if v > 0 else ''}{v:.{d}f}{suf}"


def tone(v):
    return "" if v is None or v == 0 else ("up" if v > 0 else "dn")


def spark(series, t, n=130):
    raw = series and series.get("s", {}).get(t)
    if not raw:
        return ""
    c = [float(x) for x in raw["c"].split(",")]
    ma = [None] * len(c)
    for i in range(49, len(c)):
        ma[i] = sum(c[i - 49:i + 1]) / 50
    c, ma = c[-n:], ma[-n:]
    vals = c + [m for m in ma if m is not None]
    lo, hi = min(vals), max(vals)
    W, H = 300, 64
    x = lambda i: 2 + i / (len(c) - 1) * (W - 8)
    y = lambda v: 4 + (hi - v) / ((hi - lo) or 1) * (H - 8)
    pc = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(c))
    pm = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(ma) if v is not None)
    area = f"{x(0):.1f},{H} {pc} {x(len(c) - 1):.1f},{H}"
    return (f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label="최근 6개월 종가와 50일선">'
            f'<polygon points="{area}" fill="var(--area)"/>'
            f'<polyline points="{pm}" fill="none" stroke="var(--ma)" stroke-width="1.4" stroke-dasharray="3 3"/>'
            f'<polyline points="{pc}" fill="none" stroke="var(--ink)" stroke-width="1.6" stroke-linejoin="round"/>'
            f'<circle cx="{x(len(c) - 1):.1f}" cy="{y(c[-1]):.1f}" r="2.8" fill="var(--accent)"/></svg>')


def idx_line(mi, keys):
    out = []
    for k in keys:
        ix = (mi or {}).get("idx", {}).get(k)
        if not ix or len(ix["c"]) < 2:
            continue
        ch = (ix["c"][-1] / ix["c"][-2] - 1) * 100
        out.append(f'<span class="ix"><b>{e(ix["name"])}</b> <span class="mono">{ix["c"][-1]:,.2f}</span> '
                   f'<span class="mono {tone(ch)}">{sgn(ch, 2)}</span> <small>{e(ix["d"][-1][5:].replace("-", "/"))}</small></span>')
    return "".join(out)


def card(i, x, mk, series, cm):
    c = cm.get(x["ticker"], {})
    news = "".join(
        f'<li>{e(n["t"])}' + "".join(f' <a href="{e(u)}" target="_blank" rel="noopener">{e(s)}</a>' for s, u in n.get("src", [])) + "</li>"
        for n in c.get("news", []))
    stats = [("종가", fnum(x["price"], mk), ""), ("전일비", sgn(x["chg1d"], 2), tone(x["chg1d"])),
             ("52주 고점", sgn(x["hi_gap"]), tone(x["hi_gap"]) if x["hi_gap"] < 0 else ""),
             ("3개월", sgn(x["r63"], 0), tone(x["r63"])), ("RS", str(x["rs"]), ""),
             ("50일선 이격", sgn(x["ext50"], 0), ""), ("선행 PER", f'{x["fwd_per"]:.1f}배' if x.get("fwd_per") else "–", ""),
             ("시총", fnum(x["mcap"], mk, "cap"), "")]
    return f'''
<article class="pick" id="{mk}-{e(x["ticker"])}">
  <header class="ph">
    <span class="rk mono">{i}</span>
    <div class="nm"><h3>{e(x["name"])}</h3><span class="mono tk">{e(x["ticker"])}</span><span class="sec">{e(x["sector"] or "업종 미분류")}</span></div>
    <div class="sc"><span class="mono">{x["score"]:.0f}</span><small>/100</small></div>
  </header>
  {f'<p class="take">{e(c["take"])}</p>' if c.get("take") else ""}
  <div class="body">
    <div class="chart">{spark(series, x["ticker"])}<div class="lg"><i class="l1"></i>종가 <i class="l2"></i>50일선 · 최근 6개월</div></div>
    <dl class="st">{"".join(f'<div><dt>{k}</dt><dd class="mono {t}">{v}</dd></div>' for k, v, t in stats)}</dl>
  </div>
  <div class="chips">{"".join(f"<span>{e(w)}</span>" for w in x["why"])}</div>
  {f'<div class="news"><h4>뉴스 · 애널리스트</h4><ul>{news}</ul></div>' if news else '<div class="news"><h4>뉴스 · 애널리스트</h4><p class="muted">최근 확인된 리포트·뉴스 없음</p></div>'}
  {f'<p class="risk"><b>리스크</b> {e(c["risk"])}</p>' if c.get("risk") else ""}
</article>'''


def section(mk, top, series, cm, summary):
    lab = "국내" if mk == "kr" else "미국"
    st = top["stats"]
    funnel = (f'전체 {st["universe"]:,} → 유동성 통과 {st["liquid"]:,} → 트렌드 템플릿 {st["template"]:,} '
              f'→ 과열 제외 후 {st["passed"]:,}종목')
    bench = ", ".join(f'{e(b["name"])} <span class="mono">{b["score"]:.0f}</span>' for b in top.get("next", []))
    return f'''
<section class="mkt" id="{mk}">
  <div class="sh"><h2>{lab} TOP5</h2><span class="muted">{e(top["date"])} 종가 기준 · {funnel}</span></div>
  {f'<p class="sum">{e(summary)}</p>' if summary else ""}
  <div class="picks">{"".join(card(i + 1, x, mk, series, cm) for i, x in enumerate(top["top"]))}</div>
  {f'<p class="bench"><b>다음 후보</b> {bench}</p>' if bench else ""}
</section>'''


CSS = """
:root{
  /* 레이아웃: 한 줄 머리말 → 지수 띠 → 시장별 카드 2열(좁으면 1열) */
  --bg:#F3F5F4; --paper:#FFFFFF; --ink:#16201C; --ink2:#44514B; --muted:#6F7C76; --line:#D9E0DC;
  --accent:#0F7B6C; --soft:#E3F1EE; --area:rgba(15,123,108,.10); --ma:#B07A1F;
  --up:#C42B2B; --dn:#1E5BB8;
  --f-body:"IBM Plex Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;
  --f-mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0E1412; --paper:#151D1A; --ink:#E4ECE8; --ink2:#B6C3BD; --muted:#84928C; --line:#26312D;
  --accent:#3FB8A5; --soft:#173029; --area:rgba(63,184,165,.13); --ma:#D7A54A; --up:#F0605A; --dn:#6AA2FF; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0E1412; --paper:#151D1A; --ink:#E4ECE8; --ink2:#B6C3BD; --muted:#84928C; --line:#26312D;
  --accent:#3FB8A5; --soft:#173029; --area:rgba(63,184,165,.13); --ma:#D7A54A; --up:#F0605A; --dn:#6AA2FF; color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font-family:var(--f-body);font-size:14.5px;line-height:1.6;margin:0}
.wrap{max-width:1180px;margin:0 auto;padding-inline:16px;padding-block:22px 56px;display:grid;gap:22px}
.mono{font-family:var(--f-mono);font-variant-numeric:tabular-nums}
.muted{color:var(--muted)} .up{color:var(--up)} .dn{color:var(--dn)}
a{color:var(--accent)}
.top{display:grid;gap:6px}
.eyebrow{font-size:11.5px;letter-spacing:.12em;color:var(--accent);font-weight:600}
h1{margin:0;font-size:24px;line-height:1.25;letter-spacing:-.02em;text-wrap:balance}
.meta{color:var(--ink2);font-size:13px}
.strip{display:flex;flex-wrap:wrap;gap:8px 18px;padding:10px 14px;background:var(--paper);border:1px solid var(--line);border-radius:10px;font-size:13px}
.ix small{color:var(--muted);margin-left:2px}
.rule{font-size:12.5px;color:var(--ink2);margin:0;max-width:110ch}
.rule b{color:var(--ink)}
.mkt{display:grid;gap:12px}
.sh{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;border-bottom:2px solid var(--ink);padding-bottom:6px}
.sh h2{margin:0;font-size:19px;letter-spacing:-.01em}
.sh .muted{font-size:12.5px}
.sum{margin:0;font-size:14px;color:var(--ink2);max-width:95ch}
.picks{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,520px),1fr));gap:12px}
.pick{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:14px 16px;display:grid;gap:10px;min-width:0;align-content:start}
.ph{display:flex;align-items:flex-start;gap:12px}
.rk{font-size:13px;font-weight:600;color:var(--paper);background:var(--ink);border-radius:6px;min-width:24px;height:24px;display:grid;place-items:center;margin-top:2px}
.nm{flex:1;min-width:0;display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 8px}
.nm h3{margin:0;font-size:17px;letter-spacing:-.01em}
.tk{color:var(--muted);font-size:12.5px}
.sec{flex-basis:100%;font-size:12px;color:var(--muted)}
.sc{text-align:right;line-height:1}
.sc span{font-size:24px;font-weight:600;color:var(--accent)} .sc small{display:block;font-size:10.5px;color:var(--muted);margin-top:3px}
.take{margin:0;font-weight:600;font-size:14px}
.body{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:12px;align-items:start}
@media (max-width:560px){.body{grid-template-columns:minmax(0,1fr)}}
.chart svg{display:block;width:100%;height:72px}
.lg{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:5px;margin-top:2px}
.lg i{display:inline-block;width:14px;height:0;border-top:2px solid var(--ink)} .lg i.l2{border-top:2px dashed var(--ma);margin-left:6px}
.st{margin:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:3px 12px;font-size:12.5px}
.st div{display:flex;justify-content:space-between;gap:8px;border-bottom:1px dotted var(--line);padding:1px 0}
.st dt{color:var(--muted)} .st dd{margin:0}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chips span{font-size:11.5px;background:var(--soft);color:var(--ink2);border-radius:5px;padding:1px 7px;font-family:var(--f-mono)}
.news h4{margin:0 0 4px;font-size:12px;letter-spacing:.06em;color:var(--muted)}
.news ul{margin:0;padding-left:18px;display:grid;gap:4px;font-size:13.5px}
.news a{font-size:11.5px;white-space:nowrap}
.news p{margin:0;font-size:13px}
.risk{margin:0;font-size:13px;color:var(--ink2)} .risk b{color:var(--up);font-weight:600;margin-right:4px}
.bench{margin:0;font-size:12.5px;color:var(--ink2)} .bench b{color:var(--ink);margin-right:4px}
.foot{font-size:12px;color:var(--muted);line-height:1.7;border-top:1px solid var(--line);padding-top:12px}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
"""


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "report.html"
    cm = jl("report_comments.json", {}) or {}
    mi = jl("market_index.json", {})
    secs, dates = [], []
    for mk in ("kr", "us"):
        top = jl(f"top5_{mk}.json")
        if not top:
            continue
        dates.append(("국내" if mk == "kr" else "미국") + " " + top["date"][5:].replace("-", "/"))
        secs.append(section(mk, top, jl(f"series_{mk}.json"), cm.get(mk, {}), (cm.get("summary") or {}).get(mk)))
    now = datetime.now(KST)
    asof = cm.get("asof") or now.strftime("%Y-%m-%d %H:%M")
    try:
        d = datetime.strptime(asof[:10], "%Y-%m-%d")
        head = f"{d.month}월 {d.day}일 ({WD[d.weekday()]}) 아침"
    except Exception:
        head = asof
    page = f"""<title>추세·모멘텀 TOP5</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;600;700&family=IBM+Plex+Mono:wght@400;600&display=swap">
<style>{CSS}</style>
<main class="wrap">
  <header class="top">
    <span class="eyebrow">MORNING NOTE · 추세추종 · 모멘텀</span>
    <h1>{e(head)} — 국내·미국 TOP5</h1>
    <span class="meta">{e(" · ".join(dates))} 종가 기준 · 작성 {e(asof)} KST</span>
  </header>
  <div class="strip">{idx_line(mi, ["KS11", "KQ11", "GSPC", "IXIC"])}</div>
  <p class="rule"><b>선정 규칙</b> 유동성(국내 시총 2,000억↑·20일 평균 거래대금 30억↑ / 미국 시총 $2B↑·$20M↑) →
  미너비니 트렌드 템플릿(종가 &gt; 50 &gt; 150 &gt; 200일선, 200일선 상승, 52주 고점 25% 이내, 저점 +30%↑, RS 70↑) →
  추격 금지(50일선 +30% 초과·RSI 80↑·5일 급등 제외) → 점수 = RS 35 · 고점 근접 20 · 3개월 수익률 15 · 매집 15 · 수축 15, 섹터당 최대 2종목.</p>
  {"".join(secs)}
  <footer class="foot">점수와 순위는 종가 데이터로 계산한 규칙 기반 분류이고, 뉴스·목표주가는 공개 기사와 증권사 발표를 모은 것입니다. 매수·매도 권유가 아닙니다.
  목표주가는 발표일이 서로 다르니 링크의 원문 날짜를 함께 보세요. 업종 이름은 데이터 제공처 분류를 그대로 쓴 것이라 실제 사업과 다를 수 있습니다.</footer>
</main>"""
    open(out, "w", encoding="utf-8").write(page)
    print(f"wrote {out} ({len(page):,} bytes)")


if __name__ == "__main__":
    main()
