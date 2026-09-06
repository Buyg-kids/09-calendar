"""[Discovery] 육아 공구 인플루언서를 웹 검색으로 자동 발굴해 data/targets.json에 등록.

사람이 타겟을 일일이 입력하지 않아도 되도록, API 키가 필요 없는 DuckDuckGo HTML
검색(html.duckduckgo.com)으로 카테고리 키워드 + 스케줄 신호어("공구 오픈",
"공구 마감", "공구 일정", "단독 최저가")를 조합한 정밀 쿼리를 인포크링크
(link.inpock.co.kr)/릿링크(lit.link) 도메인과 함께 검색하고, 결과에서 프로필
URL을 뽑아 data/targets.json에 반영한다.

스케줄 신호어를 섞는 이유: "OOO 공구"만 검색하면 상시 쿠팡파트너스 제휴
링크 모음(만물상형 계정)이 많이 걸려 실제 날짜가 있는 공구를 찾기 어렵다
(실측 확인됨). "공구 마감/오픈/일정" 같은 문구가 페이지에 있을 확률이 높은
쪽으로 검색어를 좁혀 진짜 활성 공구 계정을 더 잘 걸러낸다.

인스타그램 계정은 검색 결과만으로는 알 수 없으므로 instagram_id는 빈 값으로
남겨둔다 (수집 단계는 멀티링크 공개 페이지를 메인 소스로 쓰므로 문제 없음).

실행:
    python -m scraper.discover
"""
from __future__ import annotations

import json
import logging
import random
import re
import time

import requests
from bs4 import BeautifulSoup

from config import TARGETS_PATH
from scraper.multilink_scraper import find_instagram_handle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DDG_HTML_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 카테고리 대표어 (검색어, 매칭될 카테고리 힌트) - primary_focus 로 저장되고,
# 실제 카테고리 판단은 parser/extract_schedule.py(Agent 2)가 본문을 보고 다시 내린다.
CATEGORY_TERMS = [
    ("육아용품", "육아용품"),
    ("영유아식품", "영유아식품"),
    ("이유식", "영유아식품"),
    ("아기간식", "영유아식품"),
    ("아기식판", "육아용품"),
    ("기저귀", "육아용품"),
    ("키즈", "키즈가구"),
]

# 활성 공구(날짜가 있는 진행중 공구) 계정을 우선적으로 찾기 위한 신호어.
# "{카테고리} 공구" 기본형에, 아래 신호어를 하나씩 덧붙여 더 정밀한 쿼리를 만든다.
SCHEDULE_SIGNALS = ["공구 오픈", "공구 마감", "공구 일정", "공구 단독 최저가"]


def _build_search_keywords() -> list[tuple[str, str]]:
    keywords: list[tuple[str, str]] = []
    for term, hint in CATEGORY_TERMS:
        keywords.append((f"{term} 공구", hint))  # 기본형 (넓게)
        for signal in SCHEDULE_SIGNALS:
            keywords.append((f"{term} {signal}", hint))  # 스케줄 신호어 결합형 (정밀)
    return keywords


SEARCH_KEYWORDS = _build_search_keywords()

# 도메인 -> (multilink_type, 정규화된 base url). collector.py의 _detect_multilink_type()
# 과 반드시 같은 type 문자열을 써야 한다.
DOMAIN_SERVICE_MAP = {
    "link.inpock.co.kr": ("inpock", "https://link.inpock.co.kr"),
    "inpk.link": ("inpock", "https://inpk.link"),  # 인포크의 단축 도메인 (실제 프로필에서 확인됨)
    "lit.link": ("litlink", "https://lit.link"),
    "litt.ly": ("littly", "https://litt.ly"),
    "linktr.ee": ("linktree", "https://linktr.ee"),
}

SEARCH_DOMAINS = list(DOMAIN_SERVICE_MAP.keys())

PROFILE_URL_RE = re.compile(
    r"https?://(link\.inpock\.co\.kr|inpk\.link|lit\.link|litt\.ly|linktr\.ee)/([A-Za-z0-9_.\-]+)/?"
)

MAX_RESULTS_PER_QUERY = 10
REQUEST_DELAY_RANGE = (4.0, 8.0)  # DDG 차단을 피하기 위한 요청 간 지연
PAGE_VISIT_DELAY_RANGE = (2.0, 4.0)  # 후보 멀티링크 페이지를 실제로 열어볼 때마다 두는 지연
MAX_QUERIES_PER_RUN = 6  # 한 번에 너무 많이 검색하면 DDG 봇 차단에 걸리기 쉬워 매 실행마다 일부만 순환


def _search_ddg(query: str) -> list[str]:
    """DuckDuckGo HTML 검색 결과 링크 목록. 실패/차단/결과없음이면 빈 리스트."""
    try:
        resp = requests.post(
            DDG_HTML_URL,
            data={"q": query},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("검색 요청 실패: %s", query)
        return []

    if "anomaly-modal" in resp.text or "bots use DuckDuckGo" in resp.text:
        logger.warning(
            "DDG가 봇 트래픽으로 감지해 일시 차단함 (검색 요청이 너무 잦았을 수 있음): %s "
            "- 나중에 CRON_INTERVAL_HOURS 주기로 다시 시도하면 보통 풀린다.",
            query,
        )
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    return [a.get("href") for a in soup.select("a.result__a") if a.get("href")]


def _extract_profile_urls(links: list[str]) -> list[tuple[str, str]]:
    """검색 결과 링크에서 (정규화된 프로필 URL, 서비스타입) 만 추출, 중복 제거."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in links:
        m = PROFILE_URL_RE.search(link or "")
        if not m:
            continue
        domain, user_id = m.groups()
        service, base = DOMAIN_SERVICE_MAP[domain.lower()]
        url = f"{base}/{user_id}"
        if url in seen:
            continue
        seen.add(url)
        found.append((url, service))
    return found


def _entry_key(e: dict) -> str | None:
    """항목을 구분할 고유 키. multilink_url이 없는 항목(예: 해시태그 스캐너가
    instagram_id만으로 발굴한 계정)도 키를 못 찾아 누락되지 않도록 instagram_id ->
    multilink_url -> influencer_name 순으로 사용 가능한 것을 고른다.

    과거 버그: multilink_url 없으면 무조건 건너뛰었더니, discover()가 매번
    targets.json을 통째로 덮어쓰는 구조라서 인스타그램 해시태그로 발굴된(멀티링크
    없는) 계정이 discover() 실행 한 번에 전부 사라지는 사고가 실제로 발생했다."""
    instagram_id = (e.get("instagram_id") or "").strip()
    if instagram_id:
        return f"ig:{instagram_id.lstrip('@').lower()}"
    multilink_url = (e.get("multilink_url") or "").strip()
    if multilink_url and not multilink_url.rstrip("/").endswith("/sample1"):
        return f"url:{multilink_url}"
    influencer_name = (e.get("influencer_name") or "").strip()
    if influencer_name:
        return f"name:{influencer_name.lower()}"
    return None


def _load_existing_targets() -> dict[str, dict]:
    """기존 targets.json을 읽어 고유 키 기준 dict로 (전체 보존용). 최초 예시용
    플레이스홀더(sample1)는 정리."""
    if not TARGETS_PATH.exists():
        return {}
    try:
        entries = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    result: dict[str, dict] = {}
    for e in entries:
        key = _entry_key(e)
        if key:
            result[key] = e
    return result


def discover() -> list[dict]:
    # 기존에 발굴해둔 타겟(다른 발굴 경로인 auto_discover_scheduler.py의
    # 해시태그 발굴 결과 포함)에 이번 회차의 새 발굴 결과를 누적한다 - 매번
    # 새로 덮어쓰면 이번 실행에서 샘플링되지 않은 기존 결과가 사라진다.
    by_key = _load_existing_targets()

    all_combos = [(kw, hint, domain) for kw, hint in SEARCH_KEYWORDS for domain in SEARCH_DOMAINS]
    sampled = random.sample(all_combos, min(MAX_QUERIES_PER_RUN, len(all_combos)))

    new_found = 0
    for keyword, category_hint, domain in sampled:
        query = f"{keyword} site:{domain}"
        logger.info("검색: %s", query)

        links = _search_ddg(query)
        profile_urls = _extract_profile_urls(links)[:MAX_RESULTS_PER_QUERY]
        for url, service in profile_urls:
            key = f"url:{url}"
            if key in by_key:
                continue

            # URL 슬러그를 인스타그램 ID로 추정하지 않는다 (과거 사고: litt.ly/our.moomoo
            # 의 슬러그 'our.moomoo'를 그대로 저장했다가 실제로는 존재하지 않는 계정을
            # DB에 기록함 - 실제 계정은 페이지 내 인스타 아이콘이 가리키는 전혀 다른
            # '@iam.juniverse'였다). 페이지를 직접 방문해 인스타그램 아이콘의 실제
            # href에서 진짜 핸들을 확인하고, 없으면 유효하지 않은 타겟으로 보고 버린다.
            handle = find_instagram_handle(url)
            time.sleep(random.uniform(*PAGE_VISIT_DELAY_RANGE))
            if not handle:
                logger.info("인스타그램 연결 링크를 찾지 못해 제외: %s", url)
                continue

            by_key[key] = {
                "influencer_name": handle,
                "instagram_id": f"@{handle}",
                "multilink_url": url,
                "multilink_type": service,
                "primary_focus": category_hint,
            }
            new_found += 1
            logger.info("발굴: %s -> @%s (%s / %s)", url, handle, service, category_hint)

        time.sleep(random.uniform(*REQUEST_DELAY_RANGE))

    result = list(by_key.values())
    TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGETS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("이번 회차 신규 %d명 발굴, 누적 타겟 %d명 -> %s", new_found, len(result), TARGETS_PATH)
    return result


if __name__ == "__main__":
    discover()
