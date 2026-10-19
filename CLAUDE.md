# CLAUDE.md

국내(KOSPI·KOSDAQ)·미국(S&P500·NASDAQ100) 주식 스크리너 데이터 파이프라인.
GitHub Actions 가 예약 실행으로 파이썬 스크립트를 돌리고, 결과를 `output/` 에 커밋한다.
대시보드(화면)는 이 저장소 밖에 있고 `output/*.json` 을 읽는다. 투자 권유가 아닌 종목 발굴 보조 자료.

## 스크립트 ↔ 워크플로

| 스크립트 | 워크플로 | 산출물 (`output/`) |
|---|---|---|
| `screener.py` | `screener.yml` | `latest.md`, `latest.json`, `YYYYMMDD.md` (3-조건 스크리너) |
| `screener_full.py` | `screener-full.yml` | `universe_{kr,us}.json`, `series_{kr,us}.json`, `all_metrics.csv` 등 |
| `schedule_gate.py` | `screener-full.yml` | 없음 — 장 마감 전이면 대기(`wait`), 수집할 시장 판정(`check`) |
| `daily_top5.py` | `screener-full.yml` | `top5_{kr,us}.json` (universe·series 를 입력으로 사용) |
| `market_index.py` | `screener-full.yml`, `market-index.yml` | `market_index.json` |
| `canslim_data.py` | `canslim.yml` | `canslim_{kr,us}.json`, `consensus_auto_kr.json` |
| `company_profile.py` | `company-profile.yml` | `profile_{kr,us}.json` |
| `event_arb.py` | `event-arb.yml` | `events_kr.json` (DART 공시 기반 이벤트 차익) |
| `chart_history.py` | `chart-history.yml` | Release `chart-history` 의 parquet + `chart` 브랜치의 차트 팩 |
| `make_report.py` | (워크플로 없음, 수동) | top5 모닝 리포트 HTML |
| `chart.html` + `chart/*.js` | (없음, 정적 페이지) | 개인용 차트 화면 — `output/series_*`·`universe_*`·`market_index.json` 을 읽는다. 모듈: `util`(데이터·표시) `indicators`(지표) `drawings`(그리기) `chart`(차트 한 칸) `live`(실시간) `app`(화면 전체) |
| `realtime_server.py` | (없음, 내 PC 전용) | 한투 실시간 체결가를 SSE 로 `chart.html` 에 전달. `.env` 키 사용, 127.0.0.1 에서만 열림. 윈도우는 `실시간차트.bat` |
| `kis.py` · `kis_check.py` | `kis-check.yml` | 한투 Open API 공용 모듈(토큰·현재가·당일 1분봉·실시간) / Secrets 키 점검 (로그만, 커밋 없음) |

의존 순서: `screener_full.py` → `daily_top5.py` → `make_report.py`.

## 개발 규칙

- 의존성은 `requirements.txt` (워크플로의 `pip install` 목록 합본). 패키지를 추가하면
  **해당 워크플로의 `pip install` 줄과 `requirements.txt` 둘 다** 고친다. CI 는 Python 3.11.
- 설정은 모두 환경 변수(`os.getenv`)로 받는다 — 하드코딩 대신 기본값이 있는 env 를 추가한다.
- 시간대: 국내 로직은 KST(`timezone(timedelta(hours=9))`), 미국은 `America/New_York`.
  cron 은 UTC 이므로 워크플로 주석에 KST 를 함께 적는 기존 관례를 따른다.
- 주석·문서·커밋 메시지는 한국어, 기존 스타일(섹션 구분선 `# ── ... ──`)을 맞춘다.
- `output/` 은 봇(github-actions)이 커밋한다. 직접 데이터 파일을 손으로 고치거나 커밋하지 않는다.
  출력 JSON 스키마를 바꾸면 대시보드가 깨질 수 있으니 필드 추가는 괜찮지만 삭제·이름 변경은 피한다.
- 여러 워크플로가 `concurrency: group: screener-full` 을 공유해 `output` 커밋 충돌을 막는다 — 새 워크플로도 같은 그룹을 쓴다.

## 검증

테스트 스위트는 없다. 변경 후 최소한:

```bash
python3 -m py_compile *.py
```

실제 실행은 외부 사이트(네이버·KRX·Yahoo·DART·FnGuide 등)를 대량 호출하고 수십 분 걸리므로
각 스크립트 상단의 env(예: `screener.py` 의 `MAX_TICKERS`)로 범위를 줄여서 돌린다. 비밀값: `DART_API_KEY`(event_arb), `SEC_UA`(canslim), `KIS_APP_KEY`·`KIS_APP_SECRET`(·`KIS_ENV`)(한투) —
로컬/세션에는 없으므로 해당 기능은 건너뛰거나 실패할 수 있다.

## 한투 Open API 주의

- 접근 토큰은 **1분에 1번**만 발급된다. 한 실행 안에서는 `KIS` 객체 하나를 재사용한다 (`kis.py` 가 `.kis_token.json` 에 캐시).
- 실시간 구독은 세션당 41건 제한 — `realtime_server.py` 는 40개까지만 구독한다.
- `.env`·`.kis_token.json`·`.kis_cache.json` 은 커밋 금지 (.gitignore). 키 값을 로그·출력에 남기지 않는다.
