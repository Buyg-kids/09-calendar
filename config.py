"""프로젝트 전역 설정 및 카테고리 규칙."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

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
}

CATEGORY_NAMES = list(CATEGORIES.keys())

EXCLUDE_HINT = (
    "성인 패션, 성인 뷰티/화장품, 일반 가전, 다이어트 보조제, 반려동물 용품 등 "
    "영유아(만 0~7세 아이)와 직접 관련 없는 공동구매는 반드시 제외한다."
)
