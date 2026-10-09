# BUYG 프로젝트 기준 문서 (PROJECT_CONTEXT)

> 새 대화/새 작업자가 이 문서 하나로 현재 구조와 작업 규칙을 파악하도록 만든 기준 문서다.
> 코드가 바뀌면 이 문서도 같이 고친다. **마지막 갱신: 2026-10-09 밤 (코드 기준 최신 커밋 `91fe69d`)**
> ⚠️ 이 저장소는 공개(GitHub Pages)다. 키·토큰·고객 정보(이름/키워드)는 이 문서와 커밋에 절대 넣지 않는다.

## 1. 서비스 한 줄 요약
인스타그램 육아 공구 일정을 매일 자정에 자동 수집해 캘린더로 보여주고(`index.html`), 주말 아이와 갈 곳·행사·공연·예약 정보를 지도(`map.html`)로 보여주는 정적 사이트. 주소 **https://buyg.kr** (GitHub Pages, `CNAME`), 저장소 `Buyg-kids/09-calendar`, 브랜드명 BUYG(바이그).

## 2. 큰 그림
```
[Windows 작업 스케줄러 InstaAutoPipeline_Daily, 매일 00:00, SYSTEM 계정]
        │ run_nightly_pipeline.ps1
        ├─ Step 1  scraper/auto_discover_scheduler.py --tonight   신규 셀러 계정 발굴 (약 10시간, 오전 10시경 종료)
        ├─ Step 2  15분 쿨다운
        ├─ Step 3  run_manual.py                                    수집(scraper) → 파싱(parser, Claude) → 이미지 보관(Cloudinary)
        │                                                            → 산출물 빌드(generator/card_news.py: index.html·JSON들) (약 9~10시간, 저녁 ~20시 종료)
        └─ Step 4  git add -A / commit / push  → GitHub Pages 자동 배포 (Step 3·1 로그에 "Traceback" 없을 때만)
```
- 로그: `logs/nightly_summary_YYYYMMDD.log`(단계 요약), `nightly_discover_*.log`, `nightly_pipeline_*.log`(실행 중에는 `.err.tmp`). `logs/`, `data/`, `output/`, `.env` 는 gitignore.
- **정상 종료 문구:** `모든 단계 정상 완료 - 절전 없이 정상 종료합니다` + `Step 4 완료: GitHub Pages 배포 성공`. 오류 시 `오류 감지 - 배포를 건너뛰고 유지합니다`.
- **배포 게이트(중요):** 판정은 종료코드 + 로그의 문자열 `Traceback` 유무뿐이다. 그래서 **로그에 Traceback 이 남을 수 있는 코드(`logger.exception` 등)를 쓰지 않는다.** 크레딧 소진 같은 API 오류는 `extract_schedule.extract_from_text` 의 의도적 `logger.exception` 으로 배포를 멈추게 해 둔 것이다(건드리지 말 것).
- 야간 실행은 하루 약 20시간 걸린다(24시간 한계에 근접). 계속 늦어지거나 타겟이 800~1,000명에 닿으면 유료 스크래핑 API 검토를 먼저 알릴 것.

## 3. 저장소 구조
| 경로 | 역할 |
|---|---|
| `index.html` | 홈. **직접 만든 파일이 아니라 `generator/templates/view_page.html` 에서 매일 재생성된다** (아래 4장 이중 수정 규칙) |
| `map.html`, `creator_sheet.js`, `sw.js`, `manifest.json`, `robots.txt` | 정적 파일(수동 편집). sitemap.xml 은 야간에 lastmod 만 갱신 |
| `generator/` | `card_news.py`(야간 산출물 빌드 + 부가 수집 훅), `templates/view_page.html`(홈 템플릿), `alert_matcher.py`(키워드 알림), `kakao_notifier.py`·`send_to_me.py`(카카오), 카드뉴스/릴스 렌더러 |
| `scraper/` | 인스타·인포크·릿링크 수집(Playwright), 셀러 발굴(`auto_discover_scheduler.py`) |
| `parser/` | `extract_schedule.py`(Claude Haiku 일정 추출), `category_filter.py`(사전필터), `image_store.py`(Cloudinary) |
| `collector/` | 외부 공공 API 수집기: `kopis_collector`, `tour_collector`, `seoul_reserve_collector`, 공용 `fee_utils`(요금 파서) |
| `tools/` | 수동 실행 도구: `rebuild_site_outputs.py`(DB로 홈/JSON 재생성), `discover_sellers.py`·`merge_candidates.py`·`flag_verified_sellers.py`, `spot_homepages.py`(시드 홈페이지 검증) 등 |
| `tests/` | `python -m unittest tests.test_xxx` (현재 122개). `tests` 에 `__init__` 이 없어 `discover` 대신 모듈명을 나열해 실행 |
| `gonggu_db.py`, `data/gonggu.db` | SQLite(gonggu, processed_blobs). 파싱 결과의 단일 원본 |
| `config.py` | 경로·카테고리·키워드 상수(`CATEGORIES`, `EXPERIENCE_*`, `COMMENT_CTA_PATTERNS` 등), `.env` 로드 |

## 4. 프론트엔드 규칙
- **홈 수정은 반드시 두 파일에 같이:** `generator/templates/view_page.html` + `index.html`. (템플릿만 고치면 다음 야간 빌드 전까지 사이트가 안 바뀌고, index 만 고치면 야간 재생성에서 사라진다.) 같은 패치 스크립트를 두 파일에 적용하고, 끝나면 `tools/rebuild_site_outputs.py` 로 템플릿→index 재생성 결과가 일치하는지 확인한다.
- `map.html` 은 정적이라 한 파일. 지도는 좌표 핀이 아니라 **12개 권역(SVG)** 집계 + 시트 목록 구조다(`REGION_GROUP`).
- 지도 탭(`data-sub`): `all` 전체 · `free` **주말나들이**(상시 장소 시드 101곳, `free_spots_seed.json`) · `festival` 행사·축제(`tour_events.json`) · `stay` 숙소·여행 · `play` 키카·체험 · `show` 공연·전시(`kids_performances.json`). URL `?tab=free|festival|stay|play|show|culture(=show)|events(=festival)`, `?place=<id>` 로 상세 시트 바로 열기.
- 홈 상단: 4장 자동 롤링 캐러셀(`#heroCarousel`, 파스텔 4톤·3단 타이포, 3.5초, 스와이프), 최상단 띠배너(키워드 알림 신청 카카오 채널), 카테고리 칩·인기·마감임박·피드 탭, 하단 공연·전시 브릿지.
- 요금 뱃지 `feeBadge()`: [무료] / 소액 금액(3,000원, 범위면 3,000원~) / 일부 무료 / 유료 / **요금 확인(미기재를 무료로 단정하지 않음)**.
- localStorage 키: 공구 찜 `buyg_favs_v1`, 장소·행사 찜 `buyg_place_favs_v1`(홈 '찜한 공구' 탭이 함께 표시), 띠배너 닫기 `buyg_notice_closed_on`, 날씨 `buygWx:<권역>`.
- 분석: GA4(홈·지도), **네이버 애널리틱스**(`wcs_add["wa"]`, 홈·지도 `</body>` 앞), 주요 커스텀 이벤트 `hero_slide_click`, `keyword_alert_click`, `place_detail_open`, `place_share`, `place_fav_toggle`, `tour_event_click`.
- 카피 규칙: 공공데이터 제목은 **원문 그대로** 표시(공공누리 3유형 변경금지), 한국관광공사 이미지는 `object-fit: contain`(자르지 않음), 출처 표기 "출처: 한국관광공사".

## 5. 외부 데이터 수집기 (모두 같은 안전 패턴)
공통 원칙: `run()` 은 **예외를 던지지 않고**, 실패 시 `logger.error` 한 줄 + `None`. 응답 불완전·0건·40% 미만 급감이면 **기존 JSON 보존**. 임시 파일에 쓴 뒤 교체(원자적). 키는 `.env`에서만 읽고 로그/예외에 남기지 않는다. 훅은 `generator/card_news.py` 의 `run()` 안에서 `try/except` 로 한 번 더 감싼다.

| 수집기 | 소스 / 키 | 산출물(루트, 배포됨) | 핵심 규칙 |
|---|---|---|---|
| `kopis_collector` | KOPIS 아동 공연(`kidstate=Y`), `KOPIS_API_KEY` | `kids_performances.json` | 오늘~75일, 공연완료 제외, 요금 필드(`is_free`/`fee_min`…) 추가 |
| `tour_collector` | 한국관광공사 TourAPI KorService2 `searchFestival2`, `TOURAPI_SERVICE_KEY` | `tour_events.json` | 지역은 `lDongRegnCd`+주소 교차검증, 45일 초과 장기 제외, 성인/전문가 **제목** 제외(요금 문구는 제외 판정에 쓰지 않음), 아이 적합도는 정렬용, 공식 홈페이지·이미지(공공누리 1·3유형만) |
| `seoul_reserve_collector` | 서울 열린데이터광장 공공서비스예약 `ListPublicReservationCulture/Education/Detail`, `SEOUL_OPENAPI_KEY` | `seoul_reserve.json` (**첫 생성은 2026-10-10 00:00 실행**) | 아이/가족 대상만, 접수 가능(접수중·안내중이면서 접수종료일 미경과), 요금은 PAYATNM+본문 금액(없으면 금액 없는 '유료'), 상세 API로 주소(캐시) |
| `alert_matcher` | 고객 키워드 `data/alert_keywords.json`(비공개) | `output/matched_alerts_today.json/.txt`(비공개) | 공구 목록과 매칭해 카톡 복붙 메시지 생성, 중복 방지 `data/alert_state.json`, 본문 매칭은 게시물당 1건·확인 필요 표시, 자동 푸시는 골격만(`ALERT_PUSH_PROVIDER=none`) |

`fee_utils.py`: 원문(`fee_text`/`price`)은 보존하고 `fee_type`(free/partial/paid/unknown), `is_free`, `fee_min`, `fee_max` 만 덧붙인다. **'무료' 여부로 데이터를 거르지 않는다.**

### 환경변수 (`.env`, 이름만 — 값은 절대 문서/커밋 금지)
`ANTHROPIC_API_KEY`, `CLOUDINARY_CLOUD_NAME/API_KEY/API_SECRET`, `KAKAO_REST_API_KEY/CLIENT_SECRET/REDIRECT_URI/REFRESH_TOKEN`, `KOPIS_API_KEY`, `TOURAPI_SERVICE_KEY`, `SEOUL_OPENAPI_KEY`, (선택) `ALERT_PUSH_PROVIDER`, 인스타/인포크/릿링크 세션 경로. 템플릿은 `.env.example`.

## 6. 데이터 파일
- **공개(배포됨):** `index.html`, `experience_deals.json`(지도 체험 공구), `kids_performances.json`, `tour_events.json`, `seoul_reserve.json`(예정), `creators_stats.json`, `free_spots_seed.json`(수작업 시드, `homepage_url` 포함), `sitemap.xml`, `notices/kakao/*.txt`.
- **비공개(gitignore):** `data/targets.json`(수집 타겟 약 611명, `is_verified_seller` 플래그), `data/gonggu.db`, `data/raw_collected.json`, `data/alert_keywords.json`/`alert_state.json`, `data/*_cache.json`, `output/`, `logs/`. 타겟 파일을 바꾸기 전엔 `data/targets.json.bak_YYYYMMDD_HHMMSS_pre_<이유>` 로 백업한다.

## 7. 작업 규칙 (반드시 지킬 것)
1. **야간 파이프라인이 도는 동안(대략 00:00~20:30)은 저장소 코드·`data/targets.json` 을 수정하지 않는다.** Step 4 가 작업 폴더를 통째로 커밋하고, 실행 중 모듈이 바뀌면 오류·검증 안 된 배포가 생긴다. `Get-ScheduledTask InstaAutoPipeline_Daily` 가 `Ready` 일 때만 실제 폴더를 수정하고, 실행 중에는 **샌드박스 복사본(.env 제외)에서 만들고 검증한 뒤 종료 후 적용**한다.
2. 로그에 `Traceback` 금지(`logger.error` 사용). 새 훅은 격리 패턴(예외 삼킴 + 기존 파일 보존) 필수.
3. 푸시 전 로컬 서버(8766)에서 모바일(320~400px)·데스크톱 화면과 콘솔을 확인하고, `python -m unittest …` 전부 통과를 확인한다. 푸시 후 Actions 배포 상태를 확인한다.
4. 커밋 메시지는 파일로 전달(`git commit -F`)하고 끝에 Co-Authored-By 줄을 단다. (Windows PowerShell 5.1: 큰따옴표·한글 here-string 인자 문제, `Set-Content -Encoding UTF8` 은 BOM 을 붙이므로 `[IO.File]::WriteAllText(..., UTF8Encoding($false))` 사용.)
5. 인스타그램에는 접속하지 않는 도구(발굴/병합/플래그)와 접속하는 수집기를 구분한다. 계정 차단 위험이 있어 요청 간격을 줄이지 않는다.
6. 개인정보·키는 문서/채팅 로그에 되도록 남기지 않는다(채팅에 붙여 넘긴 키는 재발급 가능).
7. 사용자에게 보고할 때 한 일뿐 아니라 **한계와 가정(검증 못 한 것)** 을 같이 적는다. 예: 네이버 지도·애널리틱스 실제 동작은 이 환경에서 확인 불가.

## 8. 매일 아침 점검 체크리스트
1. `logs/nightly_summary_<오늘>.log`: Step 별 시각, `모든 단계 정상 완료`, `Step 4 완료`, `오류 감지` 없음. 스케줄러 `LastTaskResult 0`, `NextRunTime`.
2. 로그의 `Traceback` 0건, `파싱 완료`(검사/호출/저장/날짜불명) 와 `Usage/비용`(권장 $2 이하).
3. 부가 훅 줄: `KOPIS … 생성`, `TourAPI 행사 JSON 생성 (N건)`, `서울 공공서비스예약 JSON 생성 (N건)`, `[키워드 알림]` 블록. 건수 급감 시 "기존 파일 유지" 로그가 있는지.
4. 신규 타겟 첫 수집 여부(예: `@bellebaby_babygoods`), 신규 발굴 수.
5. 사이트 라이브(`buyg.kr`)에 새 데이터 반영, 콘솔 오류 없음.

## 9. 알려진 한계 / 함정
- 일정 추출은 구체적 날짜가 없는 글("오늘 10시 OPEN")을 못 담는다(예: `@__simplyhome`). 인스타 임베드가 막힌 계정은 오류 화면 문구만 수집된다(예: `@kelley_mom_dad`).
- `tour_events.json` 의 아이 적합도는 제목 키워드 기반이라 변별력이 낮다(정렬용).
- 서울 공공서비스예약: 목록에 금액이 없어 유료의 약 80%는 '유료'(금액 없음)로 표시된다. 시설대관·체육시설은 일부러 제외했다.
- 이 PC 브라우저 환경에서는 외부 스크립트 로딩 오류가 콘솔에 항상 보인다(기존 현상, 무시). `map.naver.com` 은 열 수 없다.
- GitHub Pages 배포가 가끔 몇 분 `waiting/queued` 로 지연된다(최신 푸시가 이전 대기 배포를 취소하기도 함).

## 10. 대기 중인 과제 (우선순위 순)
1. **자정 파이프라인 첫 자동 실행 점검 (2026-10-10 출근 후)**: `nightly_summary` 의 `모든 단계 정상 완료`/`Step 4 완료`, Traceback 0건, `seoul_reserve.json` 생성(`서울 공공서비스예약 JSON 생성 (N건)`), `@bellebaby_babygoods` 첫 수집, 키워드 알림 훅(`[키워드 알림]` 블록) 동작, TourAPI·KOPIS 요금 필드 포함. (8장 체크리스트 참고)
2. **서울 공공서비스예약 지도 연결** (승인 완료, 1번 점검 통과 후): 주말나들이 탭에 별도 섹션("🎟️ 예약 가능한 체험·프로그램") + **[예약] 뱃지 + 접수 마감 D-day** + 상세 시트 **'예약하러 가기'**(원문 예약 페이지 `reserve_url`) 버튼, 푸터 출처 "서울시 공공서비스예약". 이미지는 쓰지 않음.
3. **홈 1번 슬라이드 개편 + 0~7세 연령별 공구 큐레이션** (기획부터): 현재 1번 슬라이드 '최저가 육아공구 달력'을 **'연령별 인기공구'** 로 교체하고, 연령대 섹션/필터 체계를 0~1세 베이비 · 2~3세 토들러 · 4~5세 키즈 · 6~7세 프리스쿨 로 나눠 반영.
   - ⚠️ 설계 시 고려(구현 전 확정 필요): 현재 DB/`ROWS` 에 **연령 필드가 없다**(카테고리·브랜드·상품명뿐). 연령 태깅 출처를 정해야 한다 — 상품명/캡션 키워드("신생아·N개월·N세·유아·초등")로 룰 분류할지, 파서(Claude 추출)에 연령 필드를 추가할지(비용·재파싱 영향). 연령 미표기 상품 처리(전체 연령 섹션) 기준, 캐러셀 4장 구조(배너 문구·링크·앵커)와 어떻게 연결할지(필터 칩 `?age=` 딥링크 등)도 같이 정한다.
4. 날짜 없는 셀러 글 추출 규칙(게시일 기준 기본 기간) 논의 — `@__simplyhome` 류.
5. 공식 홈페이지 미연결 장소 71곳(주소를 알려주면 `tools/spot_homepages.py` 후보에 추가·검증).
6. 네이버 서치어드바이저 수집 요청(사용자가 직접), 네이버 애널리틱스 집계 확인, 샘플 고객 "테스트고객" `status` → `paused`.
7. 알림 푸시 자동화(카카오 나에게 보내기 / 텔레그램) — 인터페이스만 있음.

## 10-1. 신규 데이터 확장 로드맵
| 순위 | 소스 | 상태 / 메모 |
|---|---|---|
| 1 | **서울시 공공서비스예약** | 수집 완료(`seoul_reserve_collector`, 드라이런 634건·서울형 키즈카페 146건 포함), **UI 연결 대기**(10장 2번) |
| 2 | 경기데이터드림(경기 공공서비스 예약 OpenAPI) | 데이터셋·명세 미확인 → 키 발급 후 드라이런부터(서울/TourAPI 때와 같은 순서) |
| 3 | 공공데이터포털 전국 표준 데이터셋(산림청 유아숲체험원, 어린이공원, 박물관) | 정제 후 상시 시드 `free_spots_seed.json` 대량 확충(현재 101곳). 중복·좌표/주소 정제, 홈페이지 URL 검증(`tools/spot_homepages.py`) 필요 |
| 4 | 육아종합지원센터 | 서울/경기 구별 데이터셋 전수조사 후 '육아지원' **별도 탭** 분리 검토(평일 프로그램이 많아 주말나들이와 분리). 서울 전체 API는 확인 못 함(조사 보류 상태에서 재개) |
| 5 | 국립 기관 예약 포털(숲나들e, 국립박물관 등) + 네이버 트렌드 기반 핫플 탐색 | 약관·접근 방식 확인 전. 스크래핑이 필요하면 법적/약관 검토를 먼저 |

공통 원칙: 새 소스는 (1) 라이선스·약관 확인 (2) 키 발급 → 짧은 호출로 명세 실측 (3) 수집기+격리 훅+단위 테스트 (4) 드라이런 리포트 (5) 승인 후 지도 연결 순서로 붙인다. 요금은 '무료'로 거르지 않고 `fee_utils` 로 파싱한다.

## 11. 최근 변경 이력 (요약)
| 날짜 | 내용 | 커밋 |
|---|---|---|
| 10-05~07 | PWA·GA4, 홈 레이아웃 개편, 셀러 발굴/검증 도구, TourAPI 수집기 골격→지도 레이어, 행사·축제 탭, 상세 시트·찜·공유·이미지 | `e7a4905`…`66a0003` |
| 10-08 | 4장 캐러셀, 시드 홈페이지 30곳, TourAPI 파이프라인 연결(견고화) | `bfcce7b` |
| 10-09 | 파스텔 3단 배너, SEO 메타, 교구 키워드, 신규 타겟; **Traceback 오탐 사고**(파서 수정 + 수동 배포), 키워드 알림 띠배너·네이버 애널리틱스 | `cbd027a` |
| 10-09 | 키워드 알림 매칭 모듈 | `5f3faa9` |
| 10-09 | '주말나들이' 워딩, 요금 뱃지, 수집 규칙 완화(+요금문구 오탐 버그 수정) | `6cc2408` |
| 10-09 | 서울 공공서비스예약 수집기 + 격리 훅 (OpenAPI 키 등록, 실측 드라이런 634건) | `91fe69d` |
| 10-09 | 기준 문서 `PROJECT_CONTEXT.md` + 일지 체계(`docs/journal/`) 생성 | `636a0fc` |

상세 일지는 `docs/journal/` 아래 날짜별 파일을 본다.
