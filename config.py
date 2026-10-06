"""프로젝트 전역 설정 및 카테고리 규칙."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# 파싱은 정해진 스키마로 강제 추출하는 단순 작업이라 Sonnet 5($2/$10 per 1M)까지는
# 필요 없다고 판단해 Haiku 4.5($1/$5 per 1M, 절반 가격)로 낮췄다 - 타겟 계정이
# 340개까지 늘면서 밤마다 크레딧이 바닥나는 문제(9/11, 9/12, 9/14 세 번 발생)의
# 가장 큰 지렛대. 필요시 .env의 CLAUDE_MODEL로 언제든 되돌릴 수 있다.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")

# Haiku 4.5 정가(2026-09 기준, Anthropic 공식 페이지). 캐시 쓰기는 기본가의 1.25배,
# 캐시 읽기는 0.1배가 Anthropic의 표준 공식 - 실제 요율이 바뀌면 여기만 고치면 된다.
CLAUDE_PRICE_PER_MTOK_USD = {
    "input": 1.0,
    "output": 5.0,
    "cache_write": 1.25,
    "cache_read": 0.10,
}

# Cloudinary (parser/image_store.py) - 인스타 CDN 썸네일은 서명 토큰이 며칠 뒤 만료되므로
# 수집 직후 Cloudinary로 옮겨 영구 URL을 쓴다. 셋 중 하나라도 없으면 업로드 단계는 스킵된다.
# 2026-09-29: 네이버 이미지 검색 폴백은 상품명만으로 엉뚱한 쇼핑몰/성인 의류 사진을 가져오는
# 사고가 반복돼 완전히 제거했다 - 원본 인스타 이미지가 없으면 무조건 아래 플레이스홀더를 쓴다.
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")
CLOUDINARY_FOLDER = "buyg/posts"
# 종료일(없으면 시작일) 기준 이 일수가 지난 공구는 Cloudinary 이미지를 삭제하고 마감 플레이스홀더로
# 바꾼다(공구 텍스트 정보는 시세 검색 히스토리용으로 그대로 보존).
IMAGE_RETENTION_DAYS = 30

# 2026-10-05 커스텀 도메인 전환(GitHub Pages CNAME=buyg.kr). 옛 주소(LEGACY_*)는 DB에 저장된 값을 새 주소로 바꿀 때만 쓴다.
SITE_BASE_URL = "https://buyg.kr"
LEGACY_SITE_BASE_URL = "https://buyg-kids.github.io/09-calendar"
IMAGE_PLACEHOLDER_URL = f"{SITE_BASE_URL}/icons/placeholder-buyg.png"      # 이미지 수집 실패/누락
IMAGE_ENDED_PLACEHOLDER_URL = f"{SITE_BASE_URL}/icons/placeholder-ended.png"  # 보관 기간 지나 이미지 삭제됨

INPOCK_STORAGE_STATE_PATH = BASE_DIR / os.getenv("INPOCK_STORAGE_STATE_PATH", "data/inpock_storage_state.json")
LITLINK_STORAGE_STATE_PATH = BASE_DIR / os.getenv("LITLINK_STORAGE_STATE_PATH", "data/litlink_storage_state.json")
INSTA_COOKIES_PATH = BASE_DIR / os.getenv("INSTA_COOKIES_PATH", "data/insta_cookies.json")
CRON_INTERVAL_HOURS = int(os.getenv("CRON_INTERVAL_HOURS", "60"))  # 48~72시간 권장 범위의 중간값

# ---------------------------------------------------------------------------
# 파이프라인 데이터 파일 경로 (전부 여기 한 곳에 모아둠 -> 저장 위치를 바꾸고
# 싶으면 이 블록만 수정하면 scraper/parser/generator/web 전체에 반영된다)
# ---------------------------------------------------------------------------
TARGETS_PATH = BASE_DIR / "data" / "targets.json"           # Agent 1 입력
RAW_COLLECTED_PATH = BASE_DIR / "data" / "raw_collected.json"  # Agent 1 출력 / Agent 2 입력
GONGGU_DB_PATH = BASE_DIR / "data" / "gonggu.db"             # Agent 2 출력 / generator·web 입력
OUTPUT_DIR = BASE_DIR / "output"                             # Agent 4 출력 (cardnews_YYYYMMDD/), web이 미리보기로 읽음
USAGE_LOG_PATH = BASE_DIR / "logs" / "usage_log.csv"          # 회차별 Claude 토큰/비용 누적 로그 (logs/는 gitignore, 로컬 전용)

# ---------------------------------------------------------------------------
# 카테고리 규칙 (우선순위 순서 = 표시 순서)
# 이 목록은 (a) 로컬 키워드 사전필터와 (b) parser/ 가 Claude에 보내는 프롬프트에
# 그대로 포함되어 최종 판단 기준으로 쓰인다.
# ---------------------------------------------------------------------------
CATEGORIES = {
    "육아용품": {
        "priority": 1,
        "color": "#FF6B6B",
        "keywords": [
            "기저귀", "물티슈", "유모차", "카시트", "아기 스킨케어", "베이비로션",
            "아기로션", "선크림", "교구", "장난감", "완구", "아기띠", "힙시트",
            "젖병", "유축기", "수유", "속싸개", "겉싸개", "아기옷", "내복",
            "기저귀가방", "아기욕조", "목욕용품",
        ],
    },
    "영유아식품": {
        "priority": 2,
        "color": "#4ECDC4",
        "keywords": [
            "이유식", "아기간식", "유아 유산균", "유산균", "영양제", "퓨레",
            "아기과자", "분유", "간식", "유아식", "저염", "아기김", "포도당",
            "비타민D", "철분", "오메가3",
        ],
    },
    "키즈가구": {
        "priority": 3,
        "color": "#FFD93D",
        "keywords": [
            "아기침대", "유아식탁의자", "안전문", "장난감수납장", "범퍼침대",
            "아기책상", "유아의자", "안전게이트", "매트", "놀이매트", "층간소음매트",
            "책장", "정리대", "쏘서", "바운서",
        ],
    },
    # 2026-10-02: 지역/체험 공구(키즈카페·테마파크·아쿠아리움 입장권, 키즈펜션·풀빌라 숙박 등).
    # 키워드는 룰베이스 폴백(_line_category)에서도 쓰이므로 '체험'처럼 넓은 단어는 넣지 않는다
    # - '제품 체험단 모집' 글이 Claude에서 걸러져도 룰베이스로 다시 들어오는 오탐을 막기 위함.
    "키즈체험": {
        "priority": 4,
        "color": "#7C83FD",
        "keywords": [
            "키즈카페", "키카", "테마파크", "놀이동산", "아쿠아리움", "동물원", "워터파크",
            "키즈펜션", "키즈풀빌라", "풀빌라", "키즈호텔", "자유이용권", "입장권",
        ],
    },
}

CATEGORY_NAMES = list(CATEGORIES.keys())

EXCLUDE_HINT = (
    "성인 패션, 성인 뷰티/화장품, 일반 가전, 다이어트 보조제, 반려동물 용품 등 "
    "영유아(만 0~7세 아이)와 직접 관련 없는 공동구매는 반드시 제외한다. "
    "아이 동반이 아닌 성인 대상 여행·숙박·레저, 그리고 제품 '체험단/서포터즈 모집'"
    "(무료 제공 후 리뷰를 받는 모집 글)은 공동구매가 아니므로 제외한다."
)

# 지역/체험 공구 메타데이터 (parser/extract_schedule.py 추출 스키마, 지도 탭용 experience_deals.json)
REGIONS = [
    "서울", "경기", "인천", "강원", "충북", "충남", "대전", "세종", "전북", "전남", "광주",
    "경북", "경남", "대구", "울산", "부산", "제주", "전국",
]
# 이 단어가 있는 글은 Claude에 '체험 후보' 힌트만 붙인다(사전필터 통과 기준 자체는 그대로 -
# 이 단어만으로 통과시키면 일상 나들이 글까지 호출돼 비용이 는다).
EXPERIENCE_HINT_KEYWORDS = [
    "티켓", "입장권", "예매", "키카", "키즈카페", "테마파크", "펜션", "풀빌라", "숙소",
    "아쿠아리움", "체험", "리조트",
    # 2026-10-06 확장(체험 후보 누락 점검 결과): 이용권류 / 숙박 / 시설 / 활동 / 대형 시설명
    "이용권", "자유이용", "체험권", "숙박권", "호캉스", "호텔", "글램핑", "캠핑장", "워터파크", "수영장",
    "농장", "체험농원", "목장", "동물원", "박물관", "전시", "눈썰매", "스키", "온천", "스파",
    "원데이", "클래스", "트램폴린", "방방", "놀이공원", "키자니아", "에버랜드", "롯데월드", "서울랜드",
    "오션월드", "캐리비안", "키즈풀", "승마", "공연", "뮤지컬",
]
# 체험 패스 키워드: 공구 신호어(공구/특가/할인/링크...)가 없어도 사전필터를 통과시켜 Claude가 판정하게 하는 단어.
# 입장권·숙박권처럼 "무엇을 판다"가 이미 드러나는 말 위주로 좁게 잡는다('캠핑'·'전시' 같은 흔한 말은 제외 -
# 육아용품 글까지 호출돼 비용이 늘기 때문). 판정은 여전히 Claude(엄격 제외 기준 포함)가 한다.
EXPERIENCE_PASS_KEYWORDS = [
    "입장권", "이용권", "자유이용", "티켓", "예매", "숙박권", "체험권", "키즈카페", "키카", "풀빌라", "펜션",
    "워터파크", "호캉스", "글램핑", "테마파크", "아쿠아리움", "체험농원", "원데이클래스", "키자니아", "눈썰매",
]
