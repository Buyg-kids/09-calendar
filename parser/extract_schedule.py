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

비용 최적화 (타겟 계정이 340개까지 늘면서 밤마다 Anthropic 크레딧이 바닥나는
사고가 반복돼 2026-09 추가):
  - 모델을 Sonnet 5 -> Haiku 4.5로 낮춤(config.CLAUDE_MODEL, 토큰당 절반 가격) -
    스키마가 고정된 추출 작업이라 Haiku로도 품질 유지 확인.
  - 사전필터(parser/category_filter.quick_prefilter) 강화: 공구 신호어가 전혀
    없는 캡션은 카테고리 키워드만으로 더 이상 통과시키지 않음 - "오늘 기저귀
    샀어요" 같은 일상 글이 Claude까지 가던 걸 차단.
  - 시스템 프롬프트(카테고리/제외/상품명/가격 규칙)를 매 호출 공통 system
    파라미터로 분리해 prompt caching 적용 - 같은 규칙을 매번 통째로 다시
    보내지 않고 캐시로 읽어 입력 토큰 비용을 크게 줄임.
  - 같은 게시물이 "최신 5개" 안에 며칠씩 남아있어 매일 밤 똑같은 캡션을 다시
    Claude에 보내는 중복 호출을 gonggu_db.processed_blobs 캐시로 스킵.
  - 해시태그/이모지 도배(_strip_noise)를 자르고 나서 본문을 보내 토큰을 아낌.
  - 임베드 UI 문구와 매일 바뀌는 좋아요/팔로워 수 줄을 걷어낸 순수 본문
    (clean_blob_text)으로 중복 해시를 계산하고 Claude에 보냄 (2026-10-01).

실행:
    python -m parser.extract_schedule
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

import gonggu_db
from config import (
    ANTHROPIC_API_KEY,
    CATEGORIES,
    CATEGORY_NAMES,
    CLAUDE_MODEL,
    CLAUDE_PRICE_PER_MTOK_USD,
    EXCLUDE_HINT,
    RAW_COLLECTED_PATH,
    REGIONS,
    USAGE_LOG_PATH,
)
from parser.category_filter import GROUP_BUY_SIGNAL_WORDS, is_experience_candidate, quick_prefilter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# =============================================================================
# 1) 룰베이스 파서 (API 키 없이도 항상 동작)
# =============================================================================
DATE_RANGE_RE = re.compile(r"(\d{1,2})\s*[./]\s*(\d{1,2})\s*(?:일)?\s*[~\-–]\s*(\d{1,2})\s*[./]\s*(\d{1,2})\s*(?:일)?")
DATE_SINGLE_RE = re.compile(r"(\d{1,2})\s*(?:[./]|월\s*)\s*(\d{1,2})\s*(?:일)?")
BENEFIT_RE = re.compile(r"(\d{1,3}\s*%|1\+1|2\+1|무료배송|사은품\s*증정?|선착순\s*\d*명?)")
URL_RE = re.compile(r"https?://\S+")
DEADLINE_WORDS = ["마감", "까지", "종료", "연장"]
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


# 체험 공구 지역 검증용: 원문에 자주 나오는 시·군·관광지명 -> 광역 지역.
# 2026-10-02 첫 실데이터에서 Claude가 '가평 켄싱턴'을 강원, '델피노(울산바위뷰)'를 울산으로
# 추측했다 - 지역은 원문 근거(광역명 또는 아래 지명)가 있을 때만 인정하고, 지명이 보이면 이 표가 우선한다.
_PLACE_REGION = {
    "서울": ["국립중앙박물관", "잠실", "코엑스", "어린이대공원", "용산", "강남", "성수", "여의도", "마포", "송파",
             "서울상상나라", "국립어린이과학관",
             # 2026-10-06 보강: 구 지명 + 대표 시설
             "종로", "서초", "관악", "동작", "영등포", "노원", "은평", "서대문", "구로", "금천", "양천", "동대문",
             "성북", "강북", "도봉", "중랑", "광진", "성동", "강동", "상암", "마곡", "건대", "홍대", "신촌", "이태원",
             "롯데월드", "롯데월드 아쿠아리움", "코엑스 아쿠아리움", "서울숲", "서울식물원", "남산", "경복궁", "북촌"],
    "부산": ["해운대", "기장", "광안리", "서면", "송도해수욕장", "롯데월드 어드벤처 부산", "씨라이프 부산",
             "센텀", "남포동", "다대포", "이기대", "벡스코", "씨라이프", "부산아쿠아리움", "국립해양박물관", "영도", "송정"],
    "대전": ["오월드", "엑스포과학공원", "유성", "둔산", "대덕", "국립중앙과학관", "한밭수목원", "유성온천"],
    "대구": ["이월드", "스파밸리", "앞산", "수성", "동성로", "달성", "팔공산", "83타워"],
    # 2026-10-06 신설: REGIONS enum에 있지만 사전에 키가 없어 매핑이 안 되던 권역. '광주'는 경기 광주시와 모호하므로
    # 단독 '광주'는 넣지 않고(아래 _verify_region에서 경기 광주 처리) 광역시 확정 지명만 둔다.
    "광주": ["광주광역시", "상무지구", "첨단", "충장로", "무등산", "국립아시아문화전당", "양림동", "광주호"],
    "세종": ["세종시", "세종특별자치시", "세종호수공원", "나성동", "조치원", "국립세종수목원", "베어트리파크"],
    "울산": ["울산광역시", "태화강", "간절곶", "대왕암", "장생포", "울산대공원", "울주", "언양", "신불산"],
    "인천": ["송도", "영종", "강화", "월미도", "선재해림", "선재도", "영흥도", "을왕리",
             "청라", "계양", "부평", "소래", "인천대공원", "검단", "영종도", "차이나타운"],
    "경기": ["가평", "양평", "포천", "파주", "용인", "수원", "광교", "고양", "일산", "성남", "판교", "화성",
             "평택", "김포", "남양주", "의정부", "안산", "시흥", "이천", "여주", "안성", "하남", "광명",
             "부천", "안양", "군포", "의왕", "오산", "구리", "동두천", "연천", "과천", "에버랜드",
             "캐리비안베이", "서울랜드", "고고다이노", "원마운트", "한국민속촌", "서울대공원",
             "곤지암", "남한산성", "경기 광주", "아쿠아플라넷 광교", "아쿠아플라넷 일산", "킨텍스", "아침고요수목원", "허브아일랜드"],
    "강원": ["춘천", "원주", "강릉", "속초", "양양", "평창", "홍천", "횡성", "정선", "삼척", "동해",
             "태백", "영월", "인제", "철원", "화천", "양구", "설악", "델피노", "알펜시아", "비발디파크",
             "소노펠리체", "하이원", "휘닉스파크", "오션월드", "레고랜드", "웰리힐리",
             "설악산", "대관령", "주문진", "경포", "안목", "용평", "엘리시안강촌", "남이섬", "소양강", "오크밸리"],
    "충북": ["청주", "충주", "제천", "단양", "보은", "옥천", "영동", "진천", "괴산", "음성", "증평",
             "대청호", "청남대", "수안보", "속리산", "월악산", "만천하", "충주호"],
    "충남": ["천안", "아산", "공주", "보령", "서산", "논산", "당진", "태안", "홍성", "예산", "부여", "서천", "금산",
             "대천", "안면도", "꽃지", "만리포", "독립기념관", "외암민속마을", "현충사", "스파비스", "도고"],
    "전북": ["전주", "군산", "익산", "정읍", "남원", "김제", "완주", "무주", "부안", "고창",
             "한옥마을", "변산", "채석강", "덕유산"],
    "전남": ["여수", "순천", "목포", "광양", "나주", "담양", "보성", "해남", "완도", "구례", "곡성",
             "화순", "영암", "장성", "신안", "진도", "함평", "영광", "고흥", "강진", "장흥",
             "아쿠아플라넷 여수", "여수 아쿠아리움", "순천만"],
    "경북": ["포항", "경주", "안동", "구미", "영주", "김천", "상주", "문경", "울진", "영덕", "청도",
             "블루원", "보문단지", "보문", "불국사", "황리단길"],
    "경남": ["창원", "김해", "진주", "통영", "거제", "양산", "사천", "밀양", "남해", "하동", "거창",
             "로봇랜드", "통도환타지아", "공룡엑스포", "진해", "마산", "창녕", "함양", "산청", "합천", "롯데워터파크"],
    "제주": ["서귀포", "애월", "성산", "중문", "신화월드", "신화테마파크", "신화파크", "디스커버스",
             "뽀로로앤타요", "아쿠아플라넷 제주", "제주시", "한림", "협재", "함덕", "표선", "우도", "섭지코지", "월정리", "곽지"],
}
_PLACE_LOOKUP = sorted(((n, r) for r, names in _PLACE_REGION.items() for n in names), key=lambda x: -len(x[0]))
_FALSE_REGION_HITS = ("울산바위",)  # 지역명이 들어갔지만 다른 곳의 지명
# 여러 지역에 지점이 있는 체인 이용권은 지도 특정 권역이 아니라 '전국'으로 둔다.
# ('아쿠아플라넷 제주'처럼 지점명이 붙은 경우는 위 단일 지역 표가 먼저 잡는다)
_NATIONWIDE_CHAINS = ("공룡월드", "히어로플레이파크", "히어로파크", "뽀로로아쿠아빌리지", "뽀로로아쿠아",
                      "뽀로로파크", "아쿠아플라넷", "키자니아", "블루타이거", "플레이인더박스", "챔피언더블랙벨트",
                      "놀숲", "짐보리")
_MULTI_BRANCH_RE = re.compile(r"\d+\s*개\s*지점|전\s*지점|전국\s*(?:지점|매장|어디서나)")


def _verify_region(region: str | None, evidence: list[str]) -> str | None:
    """evidence는 우선순위 순(장소명 -> 상품명 -> 원문). 지명이 먼저 잡히는 쪽을 따른다
    (원문 아래쪽에 다른 지역 이야기가 섞여 있어도 장소명이 우선)."""
    texts = []
    for t in evidence:
        t = t or ""
        for false_hit in _FALSE_REGION_HITS:
            t = t.replace(false_hit, "")
        texts.append(t)
    if region == "전국" and any("전국" in t or "전 지점" in t for t in texts):
        return "전국"  # 여러 지점 공용 이용권 - 지점 지명(일산·여수 등)이 나열돼도 전국이 맞다
    head = " ".join(texts[:2])  # 장소명·상품명만 (원문 전체에 체인명이 스쳐 지나가는 경우 제외)
    # 순서: ① '아쿠아플라넷 제주'처럼 지점이 박힌 체인 항목 -> ② 체인/다지점 표현은 전국
    # (상품명에 '세종점, 아산점…' 지점 목록이 나열돼도 전국) -> ③ 일반 지명
    for name, mapped in _PLACE_LOOKUP:
        if name in head and any(ch in name for ch in _NATIONWIDE_CHAINS):
            return mapped
    if any(ch in head for ch in _NATIONWIDE_CHAINS) or any(_MULTI_BRANCH_RE.search(t) for t in texts[:2]):
        return "전국"
    for name, mapped in _PLACE_LOOKUP:
        if name in head:
            return mapped
    for t in texts:
        for name, mapped in _PLACE_LOOKUP:
            if name in t:
                return mapped
    if region in REGIONS and region != "전국" and any(region in t for t in texts):
        # '광주'는 경기 광주시(곤지암·남한산성·퇴촌)와 모호하다 - 광역시를 뜻하는 단서가 없고 경기 단서가 있으면 경기.
        if region == "광주" and not any("광주광역시" in t or "상무" in t or "첨단" in t for t in texts) \
                and any(k in t for t in texts for k in ("경기 광주", "광주시", "곤지암", "남한산성", "퇴촌")):
            return "경기"
        return region
    return None


# 체험/숙박 공구라면 상품명이나 장소명에 이런 단어가 하나는 있다. 하나도 없고 장소명도 없으면
# 장난감·교구 같은 물품이 키즈체험으로 잘못 분류된 것으로 보고 일반 육아용품으로 돌린다.
_EXPERIENCE_EVIDENCE = ("권", "입장", "이용", "숙박", "체험", "티켓", "패키지", "호텔", "리조트", "펜션",
                        "풀빌라", "키즈카페", "테마파크", "파크", "월드", "아쿠아", "워터", "클래스", "투어", "캠핑",
                        # 2026-10-06 확장 - 단, 장소명이 있을 때만 의미가 있는 단어(looks_like_experience 참고).
                        # '여행'·'스파츌라' 같은 모호어는 일반 육아용품과 섞이므로 넣지 않는다('스파'는 온천/스파밸리 형태로만).
                        "수영장", "농장", "체험농", "목장", "온천", "스파밸리", "스파비스", "눈썰매", "스키", "동물원",
                        "키자니아", "호캉스", "글램핑", "박물관", "전시관", "놀이공원", "승마", "트램폴린")


def looks_like_experience(product_name: str, place_name: str | None) -> bool:
    text = (product_name or "") + " " + (place_name or "")
    return bool(place_name) and any(w in text for w in _EXPERIENCE_EVIDENCE) or any(
        w in (product_name or "") for w in ("입장권", "이용권", "숙박권", "체험권", "티켓", "자유이용"))


def _experience_meta(it: dict, category: str, source_text: str = "") -> dict:
    """item_type/sub_category/region/place_name을 정규화한다. 키즈체험 카테고리와
    item_type=experience를 항상 일치시키고, 허용값 밖이면 None으로 비운다.
    지역은 장소명·원문에 근거가 있을 때만 남긴다(_verify_region)."""
    is_exp = category == "키즈체험" or it.get("item_type") == "experience"
    if not is_exp:
        return {"item_type": "product", "sub_category": None, "region": None, "place_name": None}
    sub = it.get("sub_category")
    place = (it.get("place_name") or "").strip()
    evidence = [place, it.get("product_name") or "", source_text]
    return {
        "item_type": "experience",
        "sub_category": sub if sub in ("stay", "play") else None,
        "region": _verify_region(it.get("region"), evidence),
        "place_name": place or None,
    }


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
                tilde_before = re.search(r"[~～]\s*$", line[: single_m.start()]) is not None
                if tilde_before or any(w in line for w in DEADLINE_WORDS):
                    if dt > ref + timedelta(days=200):
                        continue  # 이미 지난 날짜를 내년으로 넘긴 것이거나 먼 미래 표기 - 마감일로 신뢰하지 않음
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
                **_experience_meta({}, category, line),
            }
        )

    return items


# =============================================================================
# 1-b) 날짜 보정: '월 전체(1일~말일)' 임의 지정 방지
# =============================================================================
# 마감일만 적힌 게시물("~9.20", "9/20 마감")에서 LLM이 시작/종료일을 못 잡고
# 해당 월 1일~말일로 뭉뚱그리는 경우가 있어, 이런 결과는 원문 근거가 있을 때만 인정한다.
# 근거가 없으면 '수집일 기준 +FALLBACK_DAYS일'로 줄여 혼란을 막는다.
FALLBACK_DAYS = 4
_MD = r"(\d{1,2})\s*(?:[./]|월\s*)\s*(\d{1,2})\s*일?"
_WEEKDAY = r"(?:\s*[/(]\s*[월화수목금토일]\s*\)?)?"
_DEADLINE_PATTERNS = [
    re.compile(r"[~～]\s*" + _MD),
    re.compile(_MD + _WEEKDAY + r"\s*(?:까지|마감|종료)"),
    re.compile(_MD + _WEEKDAY + r"\s*[~\-–]\s*" + _MD),  # 범위: 마지막 날짜가 종료일
]
_MONTH_LONG_RE = re.compile(r"한\s*달|상시|이번\s*달|이달|월\s*내내|월말|월\s*한정|1\s*개월")


def _is_full_month(s: date, e: date) -> bool:
    last = calendar.monthrange(s.year, s.month)[1]
    return s.day == 1 and e == date(s.year, s.month, last)


def _deadline_candidates(text: str, ref: date) -> list[tuple[int, date]]:
    """원문에서 '마감일로 읽히는' 날짜와 그 등장 줄 번호를 모은다."""
    out: list[tuple[int, date]] = []
    for ln_no, line in enumerate(re.split(r"[\n\r]+", text)):
        for pat in _DEADLINE_PATTERNS:
            for m in pat.finditer(line):
                mm, dd = m.groups()[-2:]
                d = _resolve_month_day(mm, dd, ref)
                if d and d <= ref + timedelta(days=180):
                    out.append((ln_no, d))
    return out


def sanitize_month_span(item: dict, text: str, ref: date) -> dict:
    """LLM/룰이 낸 항목의 날짜를 검증한다.
    - 시작일이 없고 종료일만 있으면 시작일=수집 기준일.
    - 1일~말일(월 전체)로 잡혔는데 원문에 월 단위 근거(범위 표기/'한 달' 등)가 없으면:
        · 상품명 근처(±2줄) 마감일 단서가 있으면 그 날짜를 종료일로,
        · 그것도 없으면 수집일 기준 +FALLBACK_DAYS일 추정.
    상품별 일정이 섞인 캡션에서 엉뚱한 날짜를 끌어오지 않도록 월 전체 케이스에만 손댄다."""
    try:
        s = date.fromisoformat(item.get("start_date") or "")
    except ValueError:
        s = None
    try:
        e = date.fromisoformat(item.get("end_date") or "")
    except ValueError:
        e = None

    if s is None and e is not None and e >= ref - timedelta(days=3):
        item["start_date"] = ref.isoformat()
        return item
    if s is None or e is None or not _is_full_month(s, e):
        return item

    # 원문에 월 단위 근거가 명시돼 있으면 그대로 인정
    range_evidence = any(
        d == e for _, d in _deadline_candidates(text, ref)
    ) and re.search(_MD + r"\s*[~\-–]\s*" + _MD, text)
    if _MONTH_LONG_RE.search(text) or range_evidence:
        return item

    # 상품명 근처 줄에서 마감 단서 찾기
    lines = re.split(r"[\n\r]+", text)
    name_tokens = [t for t in re.split(r"[\s()/,·]+", item.get("product_name") or "") if len(t) >= 2][:3]
    near_lines = {i for i, ln in enumerate(lines) if any(t in ln for t in name_tokens)}
    near = [
        d for ln_no, d in _deadline_candidates(text, ref)
        if any(abs(ln_no - i) <= 2 for i in near_lines) and s <= d <= e
    ]
    if near:
        item["end_date"] = max(near).isoformat()
        item["start_date"] = max(s, ref).isoformat() if max(s, ref) <= max(near) else s.isoformat()
    else:
        item["start_date"] = ref.isoformat()
        item["end_date"] = (ref + timedelta(days=FALLBACK_DAYS)).isoformat()
    logger.info("[날짜보정] 월 전체 → %s~%s: %s", item["start_date"], item["end_date"], item.get("product_name", "")[:30])
    return item


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


# =============================================================================
# 1-c) Usage/비용 추적
# =============================================================================
# run() 시작 시 reset_usage_totals()로 초기화하고, 호출마다 _record_usage()가 누적한다.
# "input"은 캐시에 안 걸린 순수 입력 토큰만 - 캐시 생성/읽기 토큰은 따로 집계해야
# Haiku 4.5 요율(캐시 읽기가 입력가의 1/10)로 정확한 비용을 추정할 수 있다.
_usage_totals = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0}


def reset_usage_totals() -> None:
    for k in _usage_totals:
        _usage_totals[k] = 0


def _record_usage(usage) -> None:
    if usage is None:
        return
    _usage_totals["calls"] += 1
    _usage_totals["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
    _usage_totals["output_tokens"] += getattr(usage, "output_tokens", 0) or 0
    _usage_totals["cache_creation_tokens"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
    _usage_totals["cache_read_tokens"] += getattr(usage, "cache_read_input_tokens", 0) or 0


def estimate_cost_usd(totals: dict) -> float:
    p = CLAUDE_PRICE_PER_MTOK_USD
    mtok = 1_000_000
    return (
        totals["input_tokens"] / mtok * p["input"]
        + totals["output_tokens"] / mtok * p["output"]
        + totals["cache_creation_tokens"] / mtok * p["cache_write"]
        + totals["cache_read_tokens"] / mtok * p["cache_read"]
    )


def _append_usage_log(totals: dict, cost_usd: float) -> None:
    """회차별 사용량을 logs/usage_log.csv에 한 줄 추가한다 (누적 월별 비용 확인용)."""
    try:
        USAGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        is_new = not USAGE_LOG_PATH.exists()
        with USAGE_LOG_PATH.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(["date", "calls", "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens", "cost_usd"])
            w.writerow([
                datetime.now().strftime("%Y-%m-%d"), totals["calls"], totals["input_tokens"], totals["output_tokens"],
                totals["cache_creation_tokens"], totals["cache_read_tokens"], f"{cost_usd:.4f}",
            ])
    except OSError as exc:  # noqa: BLE001
        logger.warning("usage_log.csv 기록 실패 (파이프라인엔 영향 없음): %s", exc)


EXTRACT_TOOL = {
    "name": "extract_group_buys",
    "description": "텍스트에서 영유아 관련 공동구매(공구) 일정을 엄격하게 추출한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_group_buy": {
                "type": "boolean",
                "description": "육아용품/영유아식품/키즈가구/키즈체험 카테고리에 해당하는 공동구매 정보가 하나라도 있으면 true. 조금이라도 애매하면 false.",
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
                            "description": "육아용품 / 영유아식품 / 키즈가구 / 키즈체험 중 하나",
                        },
                        "product_name": {
                            "type": "string",
                            "description": (
                                "상품명. 반드시 '브랜드명 대표품목명 (세부모델1, 세부모델2, ...)' "
                                "형식을 갖춰야 한다 (필수, 예외 없음). 대표품목명은 사용자가 "
                                "검색창에 실제로 입력할 일반명사(카시트, 유모차, 하이체어, 아기띠, "
                                "기저귀, 유아복, 실내복, 식기, 이유식, 놀이매트 등)이며, 브랜드명이나 "
                                "모델명만으로는 절대 대체될 수 없다 - 원문에 명시가 없어도 "
                                "브랜드/모델로 미루어 확실히 알 수 있으면(예: '순성'은 카시트 "
                                "브랜드, '스토케 트립트랩'은 하이체어) 반드시 채워 넣어라. 예: "
                                "'순성 카시트 (빌리 프로, 브릭 프로, 노바)', '스토케 하이체어 "
                                "(트립트랩)'. 한 브랜드/타이틀 아래 슬래시(/), 쉼표, 줄바꿈 등으로 "
                                "나열된 세부 품목(예: '기저귀바구니 / 기저귀패드', '거즈햇&블루머')이 "
                                "함께 있으면 절대 버리지 말고 괄호에 전부 나열하라 - 이 경우 세부 "
                                "품목 자체가 이미 검색 가능한 품목명이므로 별도 대표품목명 없이 "
                                "'브랜드명 (세부품목1, 세부품목2, ...)'로 응답해도 된다. 예: 타이틀 "
                                "'[우아맘x이몽]' 아래 '기저귀바구니 / 기저귀패드', '거즈햇&블루머'가 "
                                "있으면 '이몽 (기저귀바구니, 기저귀패드, 거즈햇&블루머)'로 응답. 세부 "
                                "품목이 6개를 넘으면 앞 3~4개만 쓰고 '외 N종'을 붙여라. 대표품목명도 "
                                "세부 품목도 없이 '~모음전'처럼 뭉뚱그려 요약하는 응답은 금지한다 "
                                "(품목을 정말 특정할 수 없을 때만 예외)."
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
                                "경우가 많으므로 빈 문자열보다 이 표현이 사용자에게 더 명확하다). "
                                "'모음전'처럼 세부 품목이 여러 개고 품목마다 가격이 각각 달려 있으면 "
                                "'금전출납기 46,000원 / 캐시캣 19,800원 / ...'처럼 전부 나열하지 "
                                "말고, 그중 가장 낮은 가격 하나만 골라 '최저 11,000원 (외 4종)' "
                                "형태로 요약하라 (4는 나열된 품목 수에서 1을 뺀 값)."
                            ),
                        },
                        "start_date": {"type": "string", "description": "공구 시작일 YYYY-MM-DD. 알 수 없으면 빈 문자열."},
                        "end_date": {"type": "string", "description": "공구 마감일 YYYY-MM-DD. 알 수 없으면 빈 문자열."},
                        "purchase_link": {"type": "string", "description": "구매/신청 링크 언급이 있으면 원문 그대로, 없으면 빈 문자열."},
                        "key_benefit": {"type": "string", "description": "할인율 또는 사은품 등 혜택을 1줄로 요약. 없으면 빈 문자열."},
                        "item_type": {
                            "type": "string",
                            "enum": ["product", "experience"],
                            "description": "product=배송받는 육아 물품, experience=입장권·이용권·체험·여행·숙박처럼 장소에 가서 쓰는 공구 (category가 키즈체험이면 반드시 experience)",
                        },
                        "sub_category": {
                            "type": ["string", "null"],
                            "enum": ["stay", "play", None],
                            "description": "experience일 때만: stay=여행·숙소(펜션·풀빌라·호텔·리조트 숙박), play=키즈카페·테마파크·아쿠아리움·체험 입장/이용권. product면 null",
                        },
                        "region": {
                            "type": ["string", "null"],
                            "enum": REGIONS + [None],
                            "description": "experience일 때 이용 장소의 광역 지역. 여러 지점/전 지점 이용권이면 '전국'. product(배송)이거나 지역을 알 수 없으면 null",
                        },
                        "place_name": {
                            "type": ["string", "null"],
                            "description": "experience일 때 방문 장소명(예: '가평 고고다이노 키즈 호텔', '에버랜드'). 없거나 product면 null",
                        },
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


# 매 호출 공통이라 절대 안 바뀌는 규칙 텍스트는 system 파라미터로 분리해
# cache_control로 캐싱한다 (아래 _claude_extract 참고) - 인플루언서명/기준일/
# 원문처럼 매번 달라지는 값은 여기 넣으면 캐시가 매번 깨지므로 절대 넣지 않는다.
_SYSTEM_PROMPT = f"""너는 육아 인플루언서의 인스타그램 게시물/캡션/멀티링크 원문에서
"영유아 대상 공동구매(공구)" 일정만 엄격하게 추출하는 파서다. 사용자 메시지로
인플루언서명, 기준일, 원문을 받아 extract_group_buys 도구로 결과를 반환한다.

[카테고리 규칙 - 우선순위 순]
{_category_rules_text()}

[제외 규칙]
{EXCLUDE_HINT}
공동구매 자체가 아니거나 위 카테고리에 명확히 속하지 않으면 is_group_buy=false, items=[] 로 응답하라.
애매하면 포함시키지 말고 제외하라 (엄격 기준).

[상품명 표기 규칙 - 필수, 예외 없음]
product_name은 반드시 "브랜드명 대표품목명 (세부모델1, 세부모델2, ...)" 형식을
갖춰야 한다. 대표품목명은 유저가 검색창에 실제로 입력할 일반명사(카시트, 유모차,
하이체어, 아기띠, 기저귀, 유아복, 실내복, 식기, 이유식, 놀이매트 등)로, 브랜드명/
모델명만 있고 대표품목명이 빠지면 유저가 그 품목으로 검색해도 이 공구가 검색
결과에서 완전히 누락된다.
원문에 품목명이 직접 언급되지 않았어도 브랜드나 모델로 미루어 확실히 알 수
있으면(예: '순성'은 카시트 브랜드, '스토케 트립트랩'은 하이체어) 반드시 추론해서
채워라. 예: "순성 (빌리 프로, 브릭 프로, 노바)"라고만 쓰지 말고
"순성 카시트 (빌리 프로, 브릭 프로, 노바)"로, "스토케 트립트랩"이라고만 쓰지
말고 "스토케 하이체어 (트립트랩)"으로 쓴다.

멀티링크(인포크/리틀리 등) 버튼 하나에 브랜드/타이틀과 세부 품목이 함께 적혀
있는 경우도 많다 (예: 타이틀 '[우아맘x이몽]' 밑에 '기저귀바구니 / 기저귀패드',
'거즈햇&블루머'가 슬래시나 줄바꿈으로 나열됨). 이런 세부 품목은 그 자체가 이미
검색 가능한 품목명이므로 절대 생략하지 말고 전부 괄호에 나열하라 (이 경우는
별도 대표품목명 없이 "브랜드명 (세부품목1, 세부품목2, ...)"로 충분하다). 예:
"이몽 (기저귀바구니, 기저귀패드, 거즈햇&블루머)". "OO 모음전"처럼 대표품목명도
세부 품목도 없이 뭉개어 요약하지 마라 (품목을 정말 특정할 수 없을 때만 예외).

[키즈체험(지역/체험 공구) 규칙]
- 아이와 함께 이용하는 키즈카페·테마파크·놀이동산·아쿠아리움·동물원·워터파크·체험학습의
  입장권/이용권, 키즈펜션·키즈풀빌라·키즈호텔·가족 리조트 숙박권을 할인/특가로 판매하는
  공동구매는 category=키즈체험, item_type=experience 로 추출한다.
- sub_category: 숙박이면 stay, 입장·이용·체험이면 play.
- region: 이용 장소가 속한 광역 지역 하나(서울/경기/인천/강원/충북/충남/대전/세종/전북/
  전남/광주/경북/경남/대구/울산/부산/제주). 여러 지역 지점 공용 이용권이면 '전국'.
  장소의 지역을 원문으로 알 수 없으면 null (추측 금지).
- place_name: 방문 장소명 그대로(예: '가평 고고다이노 키즈 호텔', '에버랜드'). 모르면 null.
- product_name은 "장소명 상품종류 (옵션)" 형식. 예: "에버랜드 자유이용권 (종일권, 오후권)",
  "고고다이노 키즈호텔 숙박권 (주중, 주말)".
- 그 밖의 일반 육아 물품은 item_type=product, sub_category/region/place_name은 null.
- 제품 체험단·서포터즈 모집, 무료 체험 이벤트, 단순 나들이 후기(판매 링크·할인 없음)는
  공동구매가 아니므로 제외한다.

[가격 표기 규칙]
'모음전'처럼 세부 품목이 여러 개고 품목마다 가격이 따로 있으면, 개별 가격을
"금전출납기 46,000원 / 캐시캣 19,800원 / 시간학습 16,500원 / ..." 처럼 전부
나열하지 마라 (카드에 다 안 들어가고 지저분해진다). 그중 가장 낮은 가격 하나만
골라 "최저 11,000원 (외 4종)" 형태로 요약하라. 품목이 1개뿐이면 그냥 그 가격을
그대로 적는다.

[날짜 해석 기준]
사용자 메시지에 주어지는 "수집 기준일"을 기준으로, "내일", "이번주 금요일",
"9/5" 같은 상대/축약 표현을 절대 날짜(YYYY-MM-DD)로 환산하라. 연도가 없으면
기준일과 같은 연도로 간주하되, 기준일보다 과거가 되어버리면 다음 연도로
보정하라. 날짜를 특정할 수 없으면 해당 필드를 빈 문자열로 두라. 절대 추측으로
지어내지 마라.

[마감일/기간 규칙 - 중요]
- "~9.20", "9/20 마감", "9월 20일(일)까지", "~9.20 연장"처럼 마감일만 적혀 있으면
  start_date는 수집 기준일, end_date는 그 마감일로 한다. "연장"이 붙으면 연장된
  날짜가 end_date다.
- "9.14 - 9.30"처럼 범위가 적혀 있으면 그대로 start/end로 쓴다.
- 원문에 "이번 달 내내/한 달/상시" 같은 명시적 표현이 없는 한, 시작일=해당 월 1일,
  종료일=해당 월 말일로 채우지 마라(월 전체 임의 지정 금지). 마감일을 알 수 없으면
  end_date는 빈 문자열로 둔다."""

# 해시태그 도배(#태그 #태그 #태그...)와 이모지 연속 나열은 실질 정보 없이
# 토큰만 잡아먹어 본문에서 잘라낸다.
_TRAILING_HASHTAGS_RE = re.compile(r"(?:#[^\s#]+\s*){3,}$")
_EMOJI_CHAR_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF\U00002B00-\U00002BFF]"
)
_EMOJI_RUN_RE = re.compile(f"(?:{_EMOJI_CHAR_RE.pattern}){{3,}}")


def _strip_noise(text: str) -> str:
    """해시태그 도배(문미에 #태그 3개 이상 연속)와 이모지 연속(3개 이상)을
    줄여 Claude에 보내는 토큰 수를 아낀다. 본문 중간의 정상적인 문장/가격/
    날짜 표기는 건드리지 않는다."""
    if not text:
        return text
    text = _TRAILING_HASHTAGS_RE.sub("", text)
    text = _EMOJI_RUN_RE.sub(lambda m: m.group(0)[0], text)
    return text.strip()


# 2026-10-01: 캡션은 인스타 공개 임베드 페이지의 body 텍스트를 통째로 가져온 것이라
# 본문 앞뒤에 UI 문구가 붙어 있다 - 앞: "핸들 / N followers / View profile / N posts ·
# N followers / View more on Instagram / (릴스면 재생·Watch on Instagram·Original audio) /
# Like / 댓글 / 공유하기 / 저장 / N likes / 핸들", 뒤: "View all N comments / Add a
# comment... / Instagram". 프로필 bio에도 "N posts / N followers / N following /
# Followed by ..." 줄이 있다. 이 숫자들이 매일 바뀌어 같은 게시물·bio가 매일 다른
# 해시가 됐고, processed_blobs 캐시를 피해 Claude에 다시 보내졌다(실측: 하루 1,228개
# 캡션 중 최소 351건이 이미 파싱한 게시물, bio 236건은 매일 재전송). 뒤쪽 UI 문구 때문에
# _strip_noise의 "문미 해시태그" 규칙도 한 번도 안 걸렸다(절감률 0.1%).
_EMBED_ACTION_LINES = ("Like", "댓글", "공유하기", "저장")
_EMBED_FOOTER_RE = re.compile(r"^(?:View all(?: [\d,]+)? comments|Add a comment\.*|Instagram)$")
_VOLATILE_LINE_RE = re.compile(
    r"^(?:[\d,.]+[KkM]?\s*(?:posts?|followers?|following|likes?)"
    r"|[\d,.]+[KkM]? posts? · [\d,.]+[KkM]? followers?"
    r"|Followed by .+|Follow|Following|Message|View profile|View more on Instagram)$"
)


def clean_blob_text(text: str) -> str:
    """임베드 UI 문구와 매일 바뀌는 카운터 줄을 걷어낸 '순수 본문'.
    중복 판단 해시와 Claude 입력 모두 이 결과를 쓴다(원문은 caption_text로 따로 보관)."""
    if not text:
        return text
    lines = text.split("\n")
    # 캡션 헤더: 앞쪽 20줄 안에서 Like/댓글/공유하기/저장 연속 블록을 찾아 그 뒤부터 본문.
    # 블록 뒤의 "N likes"와 작성자 핸들 한 줄까지 헤더로 본다.
    head = [ln.strip() for ln in lines[:20]]
    for i in range(len(head) - 3):
        if tuple(head[i:i + 4]) == _EMBED_ACTION_LINES:
            j = i + 4
            if j < len(lines) and re.match(r"^[\d,.]+[KkM]? likes?$", lines[j].strip()):
                j += 1
            if j < len(lines) and re.match(r"^[A-Za-z0-9._]{1,30}$", lines[j].strip()):
                j += 1
            lines = lines[j:]
            break
    while lines and (not lines[-1].strip() or _EMBED_FOOTER_RE.match(lines[-1].strip())):
        lines.pop()
    lines = [ln for ln in lines if not _VOLATILE_LINE_RE.match(ln.strip())]
    return _strip_noise("\n".join(lines))


def _build_user_message(raw_text: str, influencer_name: str, reference_date: str) -> str:
    cleaned = _strip_noise(raw_text)[:4000]
    # 체험/여행/숙소 단어가 보이는 글에만 짧은 힌트를 붙인다(시스템 프롬프트 캐시는 그대로 유지).
    hint = ("\n[참고] 체험/여행/숙소 관련 단어가 있는 글이다. 키즈체험 공구면 item_type/sub_category/"
            "region/place_name을 채우고, 체험단 모집·후기글이면 제외하라.\n"
            if is_experience_candidate(cleaned) else "")
    return f"""인플루언서: '{influencer_name}'
수집 기준일: {reference_date}{hint}

[원문]
\"\"\"{cleaned}\"\"\""""


def _claude_extract(raw_text: str, influencer_name: str, reference_date: str) -> list[dict]:
    client = _get_client()
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_group_buys"},
        messages=[{"role": "user", "content": _build_user_message(raw_text, influencer_name, reference_date)}],
    )
    _record_usage(getattr(resp, "usage", None))

    for block in resp.content:
        if block.type == "tool_use" and block.name == "extract_group_buys":
            data = block.input
            if not data.get("is_group_buy"):
                return []
            items = []
            for it in data.get("items", []):
                category = it.get("category", "")
                if it.get("item_type") == "experience" or category == "키즈체험":
                    if looks_like_experience(it.get("product_name") or "", (it.get("place_name") or "").strip() or None):
                        category = "키즈체험"  # 체험/숙박은 카테고리도 항상 키즈체험으로 맞춘다
                    else:
                        # 장소도 이용권 단어도 없는 '체험' = 물품 오분류 -> 일반 육아용품
                        category, it = "육아용품", {**it, "item_type": "product"}
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
                        **_experience_meta(it, category, raw_text),
                    }
                )
            return items

    logger.warning("Claude 응답에서 tool_use 블록을 찾지 못함")
    return []


# =============================================================================
# 3) 하이브리드 디스패처
# =============================================================================
_credit_alert_sent = False


def _alert_if_credit_low(exc: Exception) -> None:
    """Anthropic 크레딧 소진(400 'credit balance is too low')이면 카톡 '나에게 보내기'로 충전 경고를
    프로세스당 1회만 보낸다. 알림 실패는 파이프라인에 영향을 주지 않으며, Traceback도 남기지 않는다."""
    global _credit_alert_sent
    if _credit_alert_sent or "credit balance" not in str(exc).lower():
        return
    _credit_alert_sent = True  # 실패해도 재시도하지 않음 (호출이 수백 건이라 반복 방지)
    try:
        from generator.send_to_me import send_alert

        send_alert(
            "⚠️ [Buyg] Anthropic 크레딧이 소진됐어요.\n"
            "console.anthropic.com > Plans & Billing 에서 충전하세요.\n"
            "충전 전까지 오늘 밤 파싱/배포가 중단될 수 있어요."
        )
        logger.warning("크레딧 소진 감지 - 카카오 경고 알림을 발송했습니다")
    except Exception as alert_exc:  # noqa: BLE001
        logger.warning("크레딧 소진 감지 - 카카오 경고 알림 발송 실패: %s", alert_exc)


def extract_from_text(raw_text: str, influencer_name: str, reference_date: str) -> tuple[list[dict], str]:
    """returns (items, method) - method는 'rule' | 'claude' | 'rule_fallback_error'.

    'rule_fallback_error'는 Claude 호출 자체가 실패(크레딧 소진 등 API 오류)해서
    룰베이스로 대체했다는 뜻이고, 'rule'은 Claude가 정상 응답했지만 결과가
    없었다(=공구 아님으로 확정)는 뜻이다 - 이 둘을 구분해야 run()이 processed_blobs
    캐시에 "확정적으로 검사 끝남"만 기록하고, API 오류로 못 본 건 다음 밤에
    다시 시도하게 만들 수 있다."""
    rule_items = _rule_based_extract(raw_text, influencer_name, reference_date)

    if not ANTHROPIC_API_KEY:
        return rule_items, "rule"

    try:
        claude_items = _claude_extract(raw_text, influencer_name, reference_date)
    except Exception as exc:
        # 의도적으로 logger.exception 유지: 크레딧 소진 등 API 오류는 Traceback으로 배포를 멈춰 이상을 드러낸다.
        logger.exception("[%s] Claude 2차 정제 실패 - 룰베이스 결과로 폴백", influencer_name)
        _alert_if_credit_low(exc)
        return rule_items, "rule_fallback_error"

    if claude_items:
        try:
            ref = datetime.strptime(reference_date, "%Y-%m-%d").date()
        except ValueError:
            ref = date.today()
        return [sanitize_month_span(it, raw_text, ref) for it in claude_items], "claude"
    return rule_items, "rule"


# =============================================================================
# 오케스트레이션
# =============================================================================
def _collect_text_blobs(entry: dict) -> list[tuple[str, str, str]]:
    """한 타겟 raw_collected 엔트리에서 파싱 대상 (텍스트, 대표 이미지 URL, 원본
    게시물 URL) 조각들을 모은다. 이미지/게시물 URL은 인스타 캡션 블롭에만 있고
    (멀티링크/bio는 항상 "") - 그 캡션에서 나온 상품에 그대로 붙여서 gonggu.db에
    저장한다. post_url은 구매 링크가 아예 없는 "댓글 달면 자동DM" 유형의 공구를
    빈 링크로 방치하지 않고 사용자를 원본 게시물로 보내 댓글을 달 수 있게 하는
    최후의 폴백으로 쓰인다(generator/card_news.py 참고)."""
    blobs: list[tuple[str, str, str]] = []

    multilink = entry.get("multilink")
    if multilink:
        if multilink.get("raw_text"):
            blobs.append((multilink["raw_text"], "", ""))
        blobs.extend((item, "", "") for item in multilink.get("link_items", []))

    instagram = entry.get("instagram")
    if instagram:
        if instagram.get("bio_text"):
            blobs.append((instagram["bio_text"], "", ""))
        for cap in instagram.get("captions", []):
            if cap.get("text"):
                shortcode = cap.get("shortcode") or ""
                kind = cap.get("type") or "p"
                post_url = f"https://www.instagram.com/{kind}/{shortcode}/" if shortcode else ""
                blobs.append((cap["text"], cap.get("image_url") or "", post_url))

    return blobs


def run() -> dict:
    if not RAW_COLLECTED_PATH.exists():
        raise FileNotFoundError(
            f"{RAW_COLLECTED_PATH} 가 없습니다. 먼저 `python -m scraper.collector` 를 실행하세요."
        )

    gonggu_db.init_db()
    reset_usage_totals()
    entries = json.loads(RAW_COLLECTED_PATH.read_text(encoding="utf-8"))
    logger.info(
        "타겟 %d명의 원본 데이터 파싱 시작 (모드: %s)",
        len(entries), "하이브리드(룰베이스+Claude)" if ANTHROPIC_API_KEY else "룰베이스 전용(API 키 없음)",
    )

    stats = {
        "blobs_checked": 0, "blobs_sent_to_claude": 0, "skipped_cached": 0,
        "skipped_prefilter": 0, "saved": 0, "saved_by_rule": 0, "saved_by_claude": 0,
        "skipped_no_date": 0,
    }

    for entry in entries:
        influencer_name = entry.get("influencer_name", "")
        reference_date = (entry.get("collected_at") or datetime.now(timezone.utc).isoformat())[:10]

        for text, image_url, post_url in _collect_text_blobs(entry):
            stats["blobs_checked"] += 1
            if not quick_prefilter(text):
                # Step 1.5: 공구 신호어가 전혀 없는 일상 피드는 Claude 호출 자체를 건너뛴다
                # (parser/category_filter.GROUP_BUY_SIGNAL_WORDS 참고) - 비용 절감의 1차 방어선.
                stats["skipped_prefilter"] += 1
                continue

            # 같은 게시물이 "최신 5개" 안에 며칠씩 남아있어 캡션이 안 바뀐 채
            # 매일 밤 다시 검사 대상이 되는 경우가 많다 - 이미 확정적으로(API
            # 오류 아니게) 검사해본 텍스트면 Claude를 다시 부르지 않고 건너뛴다.
            # 해시는 UI 문구/카운터를 걷어낸 순수 본문 기준 - 좋아요·팔로워 수만 바뀐
            # 같은 게시물을 새 글로 오인하지 않게 한다(clean_blob_text 참고).
            clean_text = clean_blob_text(text)
            if not clean_text:
                stats["skipped_prefilter"] += 1
                continue
            text_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
            if gonggu_db.is_blob_processed(text_hash):
                stats["skipped_cached"] += 1
                continue

            if ANTHROPIC_API_KEY:
                stats["blobs_sent_to_claude"] += 1

            try:
                items, method = extract_from_text(clean_text, influencer_name, reference_date)
            except Exception:
                logger.exception("[%s] 파싱 실패", influencer_name)
                continue

            if method != "rule_fallback_error":
                gonggu_db.mark_blob_processed(text_hash)

            for item in items:
                if not DATE_RE.match(item["start_date"]):
                    logger.info("[%s] '%s' 시작일 불명으로 스킵", influencer_name, item["product_name"])
                    stats["skipped_no_date"] += 1
                    continue
                if item["end_date"] and not DATE_RE.match(item["end_date"]):
                    item["end_date"] = ""
                item["image_url"] = image_url
                item["post_url"] = post_url
                item["caption_text"] = text
                gonggu_db.upsert_gonggu(item)
                stats["saved"] += 1
                stats["saved_by_claude" if method == "claude" else "saved_by_rule"] += 1
                logger.info(
                    "[%s] 저장(%s): [%s] %s (%s~%s)",
                    influencer_name, method, item["category"], item["product_name"],
                    item["start_date"], item["end_date"] or "?",
                )

    logger.info(
        "파싱 완료. 검사 %d건 / [사전필터] 스킵 %d건 / 중복 캐시 스킵 %d건 / Claude 호출 %d건 / "
        "저장 %d건(룰베이스 %d, Claude %d) / 날짜불명 스킵 %d건",
        stats["blobs_checked"], stats["skipped_prefilter"], stats["skipped_cached"], stats["blobs_sent_to_claude"],
        stats["saved"], stats["saved_by_rule"], stats["saved_by_claude"], stats["skipped_no_date"],
    )

    cost_usd = estimate_cost_usd(_usage_totals)
    logger.info(
        "Usage/비용: 호출 %d건 / 입력 %d토큰 / 출력 %d토큰 / 캐시생성 %d토큰 / 캐시읽기 %d토큰 / "
        "추정비용 $%.4f (%s 기준, %s)",
        _usage_totals["calls"], _usage_totals["input_tokens"], _usage_totals["output_tokens"],
        _usage_totals["cache_creation_tokens"], _usage_totals["cache_read_tokens"], cost_usd,
        CLAUDE_MODEL, USAGE_LOG_PATH,
    )
    _append_usage_log(_usage_totals, cost_usd)
    stats["usage"] = dict(_usage_totals)
    stats["cost_usd"] = cost_usd
    return stats


if __name__ == "__main__":
    run()
