# 인스타 영유아 공구 캘린더 파이프라인

인스타그램 인플루언서의 영유아(육아용품/영유아식품/키즈가구) 공동구매 일정을
자동 수집 -> Claude로 필터링/정제 -> 캘린더 대시보드 + 인스타 카드뉴스로
만들어주는 파이프라인.

## 폴더 구조

```
scraper/     인포크링크·릿링크·인스타그램 캡션 수집 (Playwright)      [Agent 1]
parser/      Claude API로 영유아 카테고리 필터링 + 일정 추출            [Agent 2]
web/         FastAPI + FullCalendar 대시보드 (web/static/index.html)   [Agent 3]
generator/   인스타 업로드용 카드뉴스 4장 렌더링 (card_news.py)         [Agent 4]
scheduler/   cron_job.py = 48~72시간 주기 자동 실행 (APScheduler)
gonggu_db.py 공유 SQLite 데이터 계층 (Agent 2가 쓰고, Agent 3/4가 읽음)
run_manual.py 터미널에서 파이프라인 전체를 한 번에 수동 실행 (테스트용)
data/        targets.json, raw_collected.json, gonggu.db, 로그인 세션
output/      cardnews_YYYYMMDD/ 에 생성된 카드뉴스 PNG 4장씩 저장
```

## 데이터 흐름

```
data/targets.json                 (사람이 직접 관리하는 타겟 인플루언서 목록)
        │
        ▼  python -m scraper.collector          [Agent 1: Scraper]
data/raw_collected.json           (원본 텍스트 그대로, 판단 없음)
        │
        ▼  python -m parser.extract_schedule    [Agent 2: AI Parser & Filter]
data/gonggu.db  (gonggu 테이블)   (Claude가 카테고리 판별 + 구조화, upsert)
        │
        ├─▶ python -m generator.card_news  [Agent 4]  →  output/cardnews_YYYYMMDD/*.png (카드뉴스 4장)
        └─▶ python main.py serve           [Agent 3]  →  http://127.0.0.1:8000 (대시보드)
```

각 단계는 독립적으로 다시 실행할 수 있다. 예를 들어 파싱 결과가 부실하면
`parser/extract_schedule.py`의 프롬프트/카테고리 규칙(`config.py`의 `CATEGORIES`)만
고치고 그 단계만 다시 돌리면 되고, 카드 디자인이 마음에 안 들면
`generator/`만 손보면 된다 — 다른 단계에 영향 없음.

## 처음 설정

```bash
# 1. 의존 패키지 설치 (이미 완료됨)
pip install -r requirements.txt
playwright install chromium

# 2. .env 생성 후 ANTHROPIC_API_KEY 입력
copy .env.example .env

# 3. 수집 대상 인플루언서 등록
# data/targets.json 편집 (influencer_name, instagram_id, multilink_url, primary_focus)

# 4. 로그인 세션 준비 (3개 서비스 모두 브라우저가 뜨면 직접 로그인 - 회원가입도 그 화면에서)
python -m scraper.instagram_login     # 인스타그램
python -m scraper.inpock_login        # 인포크링크
python -m scraper.litlink_login       # 릿링크(lit.link)
```

인포크/릿링크는 원래 로그인 없이도 공개 프로필 페이지를 읽을 수 있지만,
세션 파일(`data/inpock_storage_state.json`, `data/litlink_storage_state.json`)이
있으면 `scraper/multilink_scraper.py`가 자동으로 그 세션을 사용해 로그인 후에만
보이는 정보(예: 인포크의 비공개 예정 일정)까지 시도한다. 즉 로그인은 선택이고,
안 해도 공개 정보 수집은 그대로 동작한다.

## 실행 및 테스트 방법

### 1) 수동 실행 (지금 바로 한 번 돌려보고 결과 확인)

```bash
python run_manual.py
```

[수집 → AI 파싱/DB 저장 → 카드뉴스 렌더링]을 순서대로 한 번 실행하고, 끝나면
아래처럼 요약을 보여준다. 이 요약만 봐도 각 단계가 제대로 됐는지 바로 알 수 있다.

```
============================================================
완료 (9.1초 소요)
  1) 수집     : 성공 - 타겟 1명
  2) AI 파싱  : Claude 호출 3건 -> gonggu.db 저장 5건 (날짜 불명 스킵 1건)
  3) 카드뉴스 : 4장 생성
       - output/cardnews_20260901/1_cover.png
       - output/cardnews_20260901/2_육아용품.png
       - output/cardnews_20260901/3_영유아식품.png
       - output/cardnews_20260901/4_키즈가구.png
============================================================
```

문제가 생기면 항목별로 원인이 바로 보인다: 1)이 "실패"면 `data/targets.json`이나
로그인 세션 문제, 2)의 "Claude 호출 0건"이면 텍스트가 사전필터를 못 넘겼거나
`.env`의 `ANTHROPIC_API_KEY`가 없는 것, 3)이 "생성된 이미지 없음"이면 이번 주
`gonggu.db`에 저장된 공구가 아직 없다는 뜻이다.

### 2) 대시보드 확인

```bash
python main.py serve
```

브라우저에서 http://127.0.0.1:8000 접속 → 탭으로 카테고리 필터링, 일정 클릭으로
팝업 확인, 오른쪽 패널에서 방금 만든 카드뉴스 미리보기.

### 3) 자동 스케줄러 (48~72시간 주기로 계속 반복)

```bash
python -m scheduler.cron_job
```

시작하자마자 1회 즉시 실행하고, 이후 `CRON_INTERVAL_HOURS`(`.env`, 기본 60시간)
주기로 반복한다. 터미널을 계속 켜둬야 하며 Ctrl+C로 종료한다. 즉시 1회만 실행하고
바로 끝내고 싶으면(=`run_manual.py`와 동일 동작이지만 스케줄러 코드 경로로 검증
하고 싶을 때):

```bash
python -m scheduler.cron_job --once
```

### 각 단계만 따로 실행하고 싶을 때

```bash
python main.py scrape      # 수집만 (Agent 1)
python main.py parse       # AI 파싱/DB 저장만 (Agent 2)
python main.py generate    # 카드뉴스 생성만 (Agent 4)
```

## 동작 방식

1. **scraper/collector.py (Agent 1)** — `data/targets.json`에 등록된 인플루언서별로
   멀티링크(인포크/릿링크) 프로필 텍스트와 인스타그램 bio + 최근 피드/릴스 캡션을
   수집해 원문 그대로 `data/raw_collected.json`에 저장. 카테고리 판단은 하지 않는다.
2. **parser/extract_schedule.py (Agent 2)** — 저비용 키워드 사전필터로 1차 거르고,
   통과한 텍스트만 Claude(`extract_group_buys` 도구 강제 호출)에 보내 영유아 3개
   카테고리 여부·상품명·브랜드·시작일/마감일·구매링크·혜택 요약을 구조화 추출.
   애매하거나 카테고리 무관이면 `is_group_buy=false`로 엄격 제외하고, 시작일을
   특정할 수 없으면 저장하지 않는다(날짜 추측 금지). 결과는 `data/gonggu.db`의
   `gonggu` 테이블에 `(influencer_name, product_name, start_date)` 기준 upsert.
3. **web/main.py + web/static/index.html (Agent 3)** — FastAPI가 `index.html`을
   정적으로 서빙하고 `/api/events`(카테고리 필터 지원)·`/api/categories`·
   `/api/cards`를 제공. 대시보드는 [전체/육아용품/영유아식품/키즈가구] 탭으로
   FullCalendar 일정을 필터링하고, 공구를 클릭하면 인플루언서·브랜드·기간·
   혜택·구매링크를 보여주는 팝업이 뜬다.
4. **generator/card_news.py (Agent 4)** — 이번 주(월~일) `gonggu` 데이터로
   인스타 업로드용 카드뉴스 4장(1080x1350)을 렌더링해
   `output/cardnews_YYYYMMDD/`(생성일 기준)에 `1_cover.png` ~
   `4_키즈가구.png` 번호순으로 저장: 1장은 카테고리별 건수/하이라이트 요약
   표지, 2~4장은 카테고리별 주간 캘린더.
5. **scheduler/cron_job.py** — 수집→파싱→카드생성 3단계(`scheduler/pipeline.py`
   의 `run_pipeline_once()`)를 `CRON_INTERVAL_HOURS`(기본 60시간, 48~72시간
   권장 범위)마다 반복. `run_manual.py`도 같은 `run_pipeline_once()`를 호출하는
   얇은 래퍼라 수동 실행과 자동 실행의 동작이 항상 동일하다.

## 알아둘 점 / 한계

- **인스타그램 스크래핑은 계정 정지 리스크가 있다.** 로그인 세션은 프로필 방문
  용도로만 최소한으로 쓰고, 캡션 텍스트는 로그인이 필요 없는 공개 임베드
  페이지에서 가져오도록 설계했지만, 그래도 요청 빈도를 늘리거나 대상
  인플루언서 수를 크게 키우면 계정이 제한될 수 있다. 스케줄 주기(2~3일)와
  `MAX_POSTS_PER_RUN`(`scraper/instagram_scraper.py`)을 보수적으로 유지할 것.
- **인포크/릿링크 CSS 셀렉터는 실제 계정으로 검증되지 않았다.** 두 서비스
  모두 SPA라 구조화 추출(`link_items`)이 실패해도 body 전체 텍스트를
  raw_text로 저장해 Claude가 의미를 해석하도록 폴백 처리되어 있지만,
  실제 인플루언서 계정으로 확인해보고 셀렉터를 다듬는 것을 권장.
- Claude 로그인/비밀번호 입력은 절대 자동화하지 않는다
  (`scraper/instagram_login.py`, `inpock_login.py`, `litlink_login.py` 모두
  브라우저만 띄우고 로그인은 사람이 직접 함. 아이디/비밀번호는 코드 어디에도
  저장되지 않고, 로그인 후 세션 쿠키만 `data/*_storage_state.json`에 저장됨).
- `parser/extract_schedule.py`는 `data/raw_collected.json`을 매번 전체 다시
  파싱한다(증분 처리 없음). `gonggu.db`가 upsert라 중복 저장은 안 되지만,
  타겟이 많아지면 Claude 호출 비용이 재수집 때마다 반복되는 점은 감안할 것.
