#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
메모리 현물 가격 과거 자료 채우기(한 번 또는 가끔 수동 실행).

DRAMeXchange 는 과거 시세를 공개하지 않는다. 대신 인터넷 아카이브(Wayback Machine)가 첫 화면을 거의 매일 저장해 두므로
그 사본들에서 같은 표를 읽어 날짜별 평균가를 output/mem_hist.json 에 채운다(이미 있는 날짜는 건드리지 않음).
날짜는 화면의 'Last Update' 표기를 쓰고, 없으면 사본 저장일을 쓴다.
"""
import json, os, sys, time
from datetime import datetime, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from macro_extra import MEM_PICK, OUT, UA, parse_dram, upd_day   # noqa: E402

import re

DAYS = int(os.getenv("MEM_BACKFILL_DAYS", "400"))
BUDGET = int(os.getenv("MEM_BACKFILL_SEC", "1500"))


def snapshots():
    st = (datetime.utcnow() - timedelta(days=DAYS)).strftime("%Y%m%d")
    seen, out = set(), []
    for url in ("www.dramexchange.com/", "dramexchange.com/"):
        for k in range(3):
            try:
                r = requests.get("https://web.archive.org/cdx/search/cdx", params={"url": url, "from": st, "output": "json", "filter": "statuscode:200",
                                 "collapse": "timestamp:8", "fl": "timestamp,original"}, headers={"User-Agent": UA}, timeout=60)
                rows = r.json()[1:]
                break
            except Exception as e:
                print("CDX", url, type(e).__name__, str(e)[:120], flush=True)
                rows = []
                time.sleep(5 * (k + 1))
        for ts, orig in rows:
            if ts[:8] not in seen:
                seen.add(ts[:8])
                out.append((ts, orig))
        print(f"CDX {url}: {len(rows)}개", flush=True)
    return sorted(out)


def main():
    hp = os.path.join(OUT, "mem_hist.json")
    try:
        H = json.load(open(hp, encoding="utf-8"))
    except Exception:
        H = {}
    snaps = snapshots()
    print(f"사본 {len(snaps)}개 · {snaps[0][0][:8] if snaps else '-'} ~ {snaps[-1][0][:8] if snaps else '-'}", flush=True)
    t0, added, ok, fail = time.time(), 0, 0, 0
    for ts, orig in snaps:
        if time.time() - t0 > BUDGET:
            print("시간 예산 끝 — 다음 실행 때 이어서", flush=True)
            break
        day0 = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        if all(day0 in H.get(nm, {}) for _, nm, *_ in MEM_PICK):
            continue
        txt = None
        for k in range(3):
            try:
                r = requests.get(f"https://web.archive.org/web/{ts}id_/{orig}", headers={"User-Agent": UA}, timeout=40)
                if r.status_code == 200 and len(r.text) > 5000:
                    txt = r.text
                    break
                if r.status_code == 429:
                    time.sleep(20)
            except Exception:
                time.sleep(3 * (k + 1))
        if not txt:
            fail += 1
            continue
        try:
            items, upd = parse_dram(txt)
        except Exception as e:
            print(" ", ts, "읽기 실패", type(e).__name__)
            fail += 1
            continue
        day = upd_day(upd, day0)
        n = 0
        for rx, nm, *_ in MEM_PICK:
            x = next((i for i in items if re.search(rx, i["item"], re.I)), None)
            if x and day not in H.setdefault(nm, {}):
                H[nm][day] = x["avg"]
                n += 1
        added += n
        ok += 1
        if ok % 20 == 0:
            print(f"  {ok}개 읽음 · {day} · 추가 {added}", flush=True)
            json.dump(H, open(hp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        time.sleep(1.2)                                     # 아카이브 서버 부담 줄이기
    json.dump(H, open(hp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"→ 읽음 {ok} · 실패 {fail} · 추가 {added} · 품목별 기록 수 {{{', '.join(f'{k}: {len(v)}' for k, v in H.items())}}}", flush=True)
    # 대시보드 자료(mem.items[].hist)도 바로 갱신
    p = os.path.join(OUT, "market_index.json")
    mi = json.load(open(p, encoding="utf-8"))
    for it in (mi.get("mem") or {}).get("items", []):
        h = H.get(it["k"], {})
        it["hist"] = [[d, h[d]] for d in sorted(h)[-400:]]
    json.dump(mi, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
