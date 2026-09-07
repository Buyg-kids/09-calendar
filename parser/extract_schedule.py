"""[Agent 2: 하이브리드 AI/룰베이스 Parser & Filter]

data/raw_collected.json (Agent 1의 출력) 을 읽어 영유아 공동구매 일정을 엄격하게 파싱한다:
  - [육아용품, 영유아식품, 키즈가구] 중 하나에 해당하지 않으면 discard
  - 해당하면 influencer_name/category/product_name/brand/start_date/end_date/
    purchase_link/key_benefit 구조로 정제
  - 유효한 결과를 SQLite data/gonggu.db 에 (influencer_name, product_name, start_date)
    기준으로 중복 없이 upsert

하이브리드 동작 (.env의 ANTHROPIC_API_KEY 유무로 자동 전환):
  - API 키 없음 -> 정규식 + 육아 키워드 사전 기반 룰베이스 파싱만 수행.
    ('9/5~9/7', '9.5-9.7' 같은 날짜 범위, '마감'/'오픈' 같은 신호어, config.py의
    카테고리 키워드를 기준으로 추출. 날짜를 못 찾으면 절대 추측하지 않고 스킵.)
  - API 키 있음 -> 룰베이스로 먼저 뽑아둔 뒤, Claude(`extract_group_buys` 도구
    강제 호출)로 같은 텍스트를 더 정교하게 재추출해 결과를 교체(2차 정제).
    Claude 호출이 실패하거나 빈 결과면 룰베이스 결과로 폴백한다.
  - 즉 API 키가 없어도 시스템은 절대 멈추지 않는다.

실행:
    python -m parser.extract_schedule
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

import gonggu_db
from config import ANTHROPIC_API_KEY, CATEGORIES, CATEGORY_NAMES, CLAUDE_MODEL, EXCLUDE_HINT, RAW_COLLECTED_PATH
from parser.category_filter import GROUP_BUY_SIGNAL_WORDS, quick_prefilter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# =============================================================================
# 1) 룰베이스 파서 (API 키 없이도 항상 동작)
# =============================================================================
DATE_RANGE_RE = re.compile(r"(\d{1,2})\s*[./]\s*(\d{1,2})\s*(?:일)?\s*[~\-–]\s*(\d{1,2})\s*[./]\s*(\d{1,2})\s*(?:일)?")
DATE_SINGLE_RE = re.compile(r"(\d{1,2})\s*[./]\s*(\d{1,2})\s*(?:일)?")
BENEFIT_RE = re.compile(r"(\d{1,3}\s*%|1\+1|2\+1|무료배송|사은품\s*증정?|선착순\s*\d*명?)")
URL_RE = re.compile(r"https?://\S+")
DEADLINE_WORDS = ["마감", "까지"]
START_WORDS = ["오픈", "시작", "부터"]


def _resolve_month_day(month: str, day: str, ref: date) -> date | None:
    try:
        d = date(ref.year, int(month), int(day))
    except ValueError:
        return None
    # 기준일보다 살짝 과거면(며칠 이내 오차 허용) 그대로 두고, 많이 과거면 내년으로 보정
    if d < ref - timedelta(days=3):
        try:
            d = date(ref.year + 1, int(month), int(day))
        except ValueError:
            return None
    return d


def _line_category(line: str) -> str | None:
    haystack = line.replace(" ", "")
    for name, cat in CATEGORIES.items():
        if any(kw.replace(" ", "") in haystack for kw in cat["keywords"]):
            return name
    return None


def _rule_based_extract(text: str, influencer_name: str, reference_date: str) -> list[dict]:
    try:
        ref = datetime.strptime(reference_date, "%Y-%m-%d").date()
    except ValueError:
        ref = date.today()

    items: list[dict] = []
    lines = [ln.strip() for ln in re.split(r"[\n\r]+", text) if ln.strip()]

    for line in lines:
        category = _line_category(line)
        if not category:
            continue

        has_signal = any(w in line for w in GROUP_BUY_SIGNAL_WORDS)
        range_m = DATE_RANGE_RE.search(line)
        single_m = None if range_m else DATE_SINGLE_RE.search(line)
        if not has_signal and not range_m and not single_m:
            continue  # 카테고리 키워드만 있고 공구/날짜 신호가 전혀 없으면 스킵 (엄격 기준)

        start_date: date | None = None
        end_date: date | None = None

        if range_m:
            sm, sd, em, ed = range_m.groups()
            s = _resolve_month_day(sm, sd, ref)
            e = _resolve_month_day(em, ed, ref)
            if s and e:
                if e < s:
                    e = date(e.year + 1, e.month, e.day)
                start_date, end_date = s, e
        elif single_m:
            m, d = single_m.groups()
            dt = _resolve_month_day(m, d, ref)
            if dt:
                if any(w in line for w in DEADLINE_WORDS):
                    start_date, end_date = ref, dt
                elif any(w in line for w in START_WORDS):
                    start_date = dt
                else:
                    start_date, end_date = dt, dt

        if not start_date:
            continue  # 날짜를 특정 못하면 추측하지 않고 스킵

        benefit_m = BENEFIT_RE.search(line)
        url_m = URL_RE.search(line)
        product_name = URL_RE.sub("", line).strip()[:60]
        if not product_name:
            continue

        items.append(
            {
                "influencer_name": influencer_name,
                "category": category,
                "product_name": product_name,
                "brand": "",
                "price": "",  # 룰베이스는 가격 표기를 신뢰성 있게 못 뽑아 항상 빈값 (뷰어가 '가격공개예정'으로 표시)
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat() if end_date else "",
                "purchase_link": url_m.group(0) if url_m else "",
                "key_benefit": benefit_m.group(0) if benefit_m else "",
            }
        )

    return items


# =============================================================================
# 2) Claude 파서 (API 키가 있을 때 룰베이스 결과를 대체하는 2차 정제)
# =============================================================================
_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


EXTRACT_TOOL = {
    "name": "extract_group_buys",
    "description": "텍스트에서 영유아 관련 공동구매(공구) 일정을 엄격하게 추출한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_group_buy": {
                "type": "boolean",
                "description": "육아용품/영유아식품/키즈가구 카테고리에 해당하는 공동구매 정보가 하나라도 있으면 true. 조금이라도 애매하면 false.",
            },
            "items": {
                "type": "array",
                "description": "is_group_buy가 true일 때 상품별 공구 정보 (없으면 빈 배열)",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": CATEGORY_NAMES,
                            "description": "육아용품 / 영유아식품 / 키즈가구 중 하나",
                        },
                        "product_name": {
                            "type": "string",
                            "description": (
                                "상품명. 한 브랜드/타이틀 아래 슬래시(/), 쉼표, 줄바꿈 등으로 나열된 "
                                "세부 품목(예: '기저귀바구니 / 기저귀패드', '거즈햇&블루머')이 함께 "
                                "있으면 절대 버리지 말고 '브랜드명 (세부품목1, 세부품목2, ...)' 형태로 "
                                "전부 괄호에 나열하라. 예: 타이틀 '[우아맘x이몽]' 아래 '기저귀바구니 / "
                                "기저귀패드', '거즈햇&블루머'가 있으면 '이몽 (기저귀바구니, 기저귀패드, "
                                "거즈햇&블루머)'로 응답. 세부 품목이 6개를 넘으면 앞 3~4개만 쓰고 "
                                "'외 N종'을 붙여라. 세부 품목 없이 '~모음전'처럼 뭉뚱그려 요약하는 "
                                "응답은 금지한다 (세부 품목이 텍스트에 실제로 없을 때만 예외)."
                            ),
                        },
                        "brand": {"type": "string", "description": "브랜드명이 텍스트에 명시돼 있으면 기입, 불명확하면 빈 문자열"},
                        "price": {
                            "type": "string",
                            "description": (
                                "가격/혜택 표기. 정확한 금액이 없어도 할인·특가를 암시하는 문구가 "
                                "있으면 절대 놓치지 말고 그대로 담아라 - 반드시 숫자 가격일 필요는 "
                                "없다. 예: '19,900원', '1만원대', '1+1 특가', '최대 50% 할인', "
                                "'정가 5만원→2만원대', '체험 특가', '반값 할인'. 이런 힌트가 텍스트에 "
                                "정말 하나도 없을 때만(가격/혜택 관련 언급 자체가 전무) "
                                "'가격공개예정'이라고 적어라 (구매 링크로 들어가야만 알 수 있는 "
                                "경우가 많으므로 빈 문자열보다 이 표현이 사용자에게 더 명확하다)."
                            ),
                        },
                        "start_date": {"type": "string", "description": "공구 시작일 YYYY-MM-DD. 알 수 없으면 빈 문자열."},
                        "end_date": {"type": "string", "description": "공구 마감일 YYYY-MM-DD. 알 수 없으면 빈 문자열."},
                        "purchase_link": {"type": "string", "description": "구매/신청 링크 언급이 있으면 원문 그대로, 없으면 빈 문자열."},
                        "key_benefit": {"type": "string", "description": "할인율 또는 사은품 등 혜택을 1줄로 요약. 없으면 빈 문자열."},
                    },
                    "required": ["category", "product_name"],
                },
            },
        },
        "required": ["is_group_buy", "items"],
    },
}


def _category_rules_text() -> str:
    return "\n".join(
        f"{cat['priority']}순위 {name}: {', '.join(cat['keywords'])} 등" for name, cat in CATEGORIES.items()
    )


def _build_prompt(raw_text: str, influencer_name: str, reference_date: str) -> str:
    return f"""다음은 육아 인플루언서 '{influencer_name}'의 게시물/캡션/멀티링크 원문이다.
이 텍스트에서 "영유아 대상 공동구매(공구)" 일정만 엄격하게 추출하라.

[카테고리 규칙 - 우선순위 순]
{_category_rules_text()}

[제외 규칙]
{EXCLUDE_HINT}
공동구매 자체가 아니거나 위 3개 카테고리에 명확히 속하지 않으면 is_group_buy=false, items=[] 로 응답하라.
애매하면 포함시키지 말고 제외하라 (엄격 기준).

[상품명 표기 규칙]
멀티링크(인포크/리틀리 등) 버튼 하나에 브랜드/타이틀과 세부 품목이 함께 적혀
있는 경우가 많다 (예: 타이틀 '[우아맘x이몽]' 밑에 '기저귀바구니 / 기저귀패드',
'거즈햇&블루머'가 슬래시나 줄바꿈으로 나열됨). 이런 세부 품목은 유저가 검색할
때 찾는 실제 키워드이므로 절대 생략하지 말고, product_name에
"브랜드명 (세부품목1, 세부품목2, ...)" 형태로 전부 담아라. "OO 모음전"처럼
세부 품목을 뭉개고 요약하지 마라 (텍스트에 정말 세부 품목이 없을 때만 예외).

[날짜 해석 기준]
이 텍스트가 수집된 기준일은 {reference_date} 이다. "내일", "이번주 금요일", "9/5" 같은
상대/축약 표현은 이 기준일을 기준으로 절대 날짜(YYYY-MM-DD)로 환산하라.
연도가 없으면 기준일과 같은 연도로 간주하되, 기준일보다 과거가 되어버리면 다음 연도로 보정하라.
날짜를 특정할 수 없으면 해당 필드를 빈 문자열로 두라. 절대 추측으로 지어내지 마라.

[원문]
\"\"\"{raw_text[:4000]}\"\"\"

extract_group_buys 도구를 사용해 결과를 반환하라."""


def _claude_extract(raw_text: str, influencer_name: str, reference_date: str) -> list[dict]:
    client = _get_client()
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_group_buys"},
        messages=[{"role": "user", "content": _build_prompt(raw_text, influencer_name, reference_date)}],
    )

    for block in resp.content:
        if block.type == "tool_use" and block.name == "extract_group_buys":
            data = block.input
            if not data.get("is_group_buy"):
                return []
            items = []
            for it in data.get("items", []):
                category = it.get("category", "")
                product_name = (it.get("product_name") or "").strip()
                if category not in CATEGORIES or not product_name:
                    continue
                items.append(
                    {
                        "influencer_name": influencer_name,
                        "category": category,
                        "product_name": product_name,
                        "brand": (it.get("brand") or "").strip(),
                        "price": (it.get("price") or "").strip(),
                        "start_date": (it.get("start_date") or "").strip(),
                        "end_date": (it.get("end_date") or "").strip(),
                        "purchase_link": (it.get("purchase_link") or "").strip(),
                        "key_benefit": (it.get("key_benefit") or "").strip(),
                    }
                )
            return items

    logger.warning("Claude 응답에서 tool_use 블록을 찾지 못함")
    return []


# =============================================================================
# 3) 하이브리드 디스패처
# =============================================================================
def extract_from_text(raw_text: str, influencer_name: str, reference_date: str) -> tuple[list[dict], str]:
    """returns (items, method) - method는 'rule' | 'claude' 로 통계용."""
    rule_items = _rule_based_extract(raw_text, influencer_name, reference_date)

    if not ANTHROPIC_API_KEY:
        return rule_items, "rule"

    try:
        claude_items = _claude_extract(raw_text, influencer_name, reference_date)
    except Exception:
        logger.exception("[%s] Claude 2차 정제 실패 - 룰베이스 결과로 폴백", influencer_name)
        return rule_items, "rule"

    if claude_items:
        return claude_items, "claude"
    return rule_items, "rule"


# =============================================================================
# 오케스트레이션
# =============================================================================
def _collect_text_blobs(entry: dict) -> list[tuple[str, str]]:
    """한 타겟 raw_collected 엔트리에서 파싱 대상 (텍스트, 대표 이미지 URL) 조각들을
    모은다. 이미지는 인스타 캡션 블롭에만 있고(멀티링크/bio는 항상 "") - 이 이미지를
    그 캡션에서 나온 상품에 그대로 붙여서 gonggu.db에 저장한다."""
    blobs: list[tuple[str, str]] = []

    multilink = entry.get("multilink")
    if multilink:
        if multilink.get("raw_text"):
            blobs.append((multilink["raw_text"], ""))
        blobs.extend((item, "") for item in multilink.get("link_items", []))

    instagram = entry.get("instagram")
    if instagram:
        if instagram.get("bio_text"):
            blobs.append((instagram["bio_text"], ""))
        for cap in instagram.get("captions", []):
            if cap.get("text"):
                blobs.append((cap["text"], cap.get("image_url") or ""))

    return blobs


def run() -> dict:
    if not RAW_COLLECTED_PATH.exists():
        raise FileNotFoundError(
            f"{RAW_COLLECTED_PATH} 가 없습니다. 먼저 `python -m scraper.collector` 를 실행하세요."
        )

    gonggu_db.init_db()
    entries = json.loads(RAW_COLLECTED_PATH.read_text(encoding="utf-8"))
    logger.info(
        "타겟 %d명의 원본 데이터 파싱 시작 (모드: %s)",
        len(entries), "하이브리드(룰베이스+Claude)" if ANTHROPIC_API_KEY else "룰베이스 전용(API 키 없음)",
    )

    stats = {
        "blobs_checked": 0, "blobs_sent_to_claude": 0,
        "saved": 0, "saved_by_rule": 0, "saved_by_claude": 0,
        "skipped_no_date": 0,
    }

    for entry in entries:
        influencer_name = entry.get("influencer_name", "")
        reference_date = (entry.get("collected_at") or datetime.now(timezone.utc).isoformat())[:10]

        for text, image_url in _collect_text_blobs(entry):
            stats["blobs_checked"] += 1
            if not quick_prefilter(text):
                continue

            if ANTHROPIC_API_KEY:
                stats["blobs_sent_to_claude"] += 1

            try:
                items, method = extract_from_text(text, influencer_name, reference_date)
            except Exception:
                logger.exception("[%s] 파싱 실패", influencer_name)
                continue

            for item in items:
                if not DATE_RE.match(item["start_date"]):
                    logger.info("[%s] '%s' 시작일 불명으로 스킵", influencer_name, item["product_name"])
                    stats["skipped_no_date"] += 1
                    continue
                if item["end_date"] and not DATE_RE.match(item["end_date"]):
                    item["end_date"] = ""
                item["image_url"] = image_url
                gonggu_db.upsert_gonggu(item)
                stats["saved"] += 1
                stats["saved_by_claude" if method == "claude" else "saved_by_rule"] += 1
                logger.info(
                    "[%s] 저장(%s): [%s] %s (%s~%s)",
                    influencer_name, method, item["category"], item["product_name"],
                    item["start_date"], item["end_date"] or "?",
                )

    logger.info(
        "파싱 완료. 검사 %d건 / Claude 호출 %d건 / 저장 %d건(룰베이스 %d, Claude %d) / 날짜불명 스킵 %d건",
        stats["blobs_checked"], stats["blobs_sent_to_claude"], stats["saved"],
        stats["saved_by_rule"], stats["saved_by_claude"], stats["skipped_no_date"],
    )
    return stats


if __name__ == "__main__":
    run()
