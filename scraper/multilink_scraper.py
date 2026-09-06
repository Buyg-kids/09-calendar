"""인포크링크(link.inpock.co.kr) / 릿링크(lit.link) / 리틀리(litt.ly) /
링크트리(linktr.ee) 프로필 페이지 수집기.

전부 클라이언트 렌더링(SPA) 페이지이므로 Playwright로 렌더링 후 본문 텍스트를
통째로 뽑아온다. 정확한 DOM 구조는 서비스 업데이트에 따라 바뀔 수 있어,
구조화 추출(링크 카드 title/설명)을 우선 시도하고 실패하면 body innerText
전체를 raw_text 로 저장해 parser 단계(Claude)가 의미를 해석하도록 한다 —
즉 스크래퍼는 "최대한 텍스트를 확보"하는 역할만 하고, 공구 여부/카테고리
판단은 parser/ 가 전담한다.

사용 전: `playwright install chromium` 1회 실행 필요.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from config import INPOCK_STORAGE_STATE_PATH, LITLINK_STORAGE_STATE_PATH

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

INSTAGRAM_HREF_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)/?")
_NOT_A_HANDLE = {"accounts", "explore", "p", "reel", "reels", "direct", "stories", "share"}


_PROFILE_VIEWPORT = {"width": 390, "height": 844}
# 프로필 헤더(아바타/소개/소셜 아이콘)는 항상 첫 화면(스크롤 없이 보이는 영역)
# 안에 있다 - 헤더 바깥(=본문 상품 목록 어딘가)에서 우연히 걸리는 instagram.com
# 링크(협찬 브랜드 계정 등 페이지 주인과 무관한 링크)를 걸러내기 위한 기준선.
_HEADER_ZONE_PX = _PROFILE_VIEWPORT["height"]


def find_instagram_handle(url: str) -> str | None:
    """멀티링크 페이지를 방문해 '프로필 헤더 영역'의 인스타그램 아이콘이 실제로
    가리키는 진짜 인스타그램 핸들을 추출한다.

    과거 버그 1: 멀티링크 URL의 슬러그(예: litt.ly/our.moomoo 의 'our.moomoo')를
    인스타그램 ID로 그대로 추정해 저장했다가, 실제로는 존재하지도 않는 계정을
    DB에 잘못 기록한 사고가 있었다(실제 계정은 페이지 안 인스타 아이콘이 가리키는
    전혀 다른 '@iam.juniverse'였음). 슬러그 추정을 완전히 배제하고, 페이지에
    삽입된 href만을 근거로 삼는다.

    과거 버그 2: 위 수정 직후 실측(inpk.link/wooamom_goeun)에서, 페이지 헤더에는
    인스타 아이콘이 아예 없고 본문 상품 목록 한참 아래(스크롤 3000px+)에 있는
    '협찬 브랜드'의 instagram.com 링크 하나가 엉뚱하게 잡혀 전혀 다른 사람으로
    오판정하는 걸 확인했다. 그래서 페이지 첫 화면(헤더 영역, 세로 위치 <= 뷰포트
    높이) 안에 있는 링크만 신뢰하고, 그 범위 밖에서만 발견되면 검증 불가로 처리한다
    (서비스마다 헤더에 인스타 아이콘이 없을 수도 있음 - 없으면 확인 불가이므로
    호출부가 이 타겟을 버리도록 None을 반환한다)."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=USER_AGENT, viewport=_PROFILE_VIEWPORT)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except PWTimeoutError:
                    pass
                candidates = page.eval_on_selector_all(
                    "a[href*='instagram.com']",
                    "els => els.map(e => ({href: e.href, top: e.getBoundingClientRect().top}))",
                )
            finally:
                browser.close()
    except Exception:
        logger.warning("멀티링크 페이지 방문 실패 (인스타그램 링크 확인 불가): %s", url)
        return None

    for c in candidates:
        if c["top"] > _HEADER_ZONE_PX:
            continue  # 헤더 밖(본문 어딘가)에서 발견된 링크는 신뢰하지 않는다
        m = INSTAGRAM_HREF_RE.search(c["href"])
        if m and m.group(1).lower() not in _NOT_A_HANDLE:
            return m.group(1)
    return None


@dataclass
class MultilinkResult:
    source: str  # 'inpock' | 'litlink' | 'littly' | 'linktree'
    url: str
    raw_text: str
    link_items: list[str]  # 개별 링크 카드 제목/설명 (구조화 추출 성공 시)


def _extract_link_items(page, selectors: list[str]) -> list[str]:
    items: list[str] = []
    for sel in selectors:
        try:
            texts = page.eval_on_selector_all(sel, "els => els.map(e => e.innerText.trim())")
            items.extend(t for t in texts if t)
        except Exception:
            continue
    # 중복 제거, 순서 유지
    seen = set()
    unique = []
    for t in items:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def _scrape_url(url: str, source: str, link_selectors: list[str], storage_state_path=None) -> MultilinkResult:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context_kwargs = {"user_agent": USER_AGENT, "viewport": {"width": 390, "height": 844}}
        if storage_state_path is not None and storage_state_path.exists():
            context_kwargs["storage_state"] = str(storage_state_path)
            logger.info("%s: 저장된 로그인 세션 사용", source)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeoutError:
                pass  # 일부 SPA는 지속적으로 네트워크 요청을 하므로 타임아웃 무시하고 진행

            body_text = page.inner_text("body")
            link_items = _extract_link_items(page, link_selectors)
        finally:
            browser.close()

    return MultilinkResult(source=source, url=url, raw_text=body_text, link_items=link_items)


def scrape_inpock(profile_url: str) -> MultilinkResult:
    """link.inpock.co.kr/{id} 프로필 페이지 수집.

    후보 셀렉터는 실제 마크업 확인 없이 통용되는 카드/버튼 패턴을 넣어둔 것이라
    안 맞으면 무시되고 body 전체 텍스트로 자동 폴백된다. 정확도를 높이려면
    브라우저 개발자도구로 실제 링크 카드의 class 명을 확인해 여기에 추가하면 된다.
    """
    selectors = [
        "[class*='link-card']",
        "[class*='LinkCard']",
        "[class*='linkItem']",
        "a[class*='link']",
        "[class*='schedule']",  # 인포크의 '예정 일정 공유' 기능 대응
    ]
    return _scrape_url(profile_url, "inpock", selectors, INPOCK_STORAGE_STATE_PATH)


def scrape_litlink(profile_url: str) -> MultilinkResult:
    selectors = [
        "[class*='linkButton']",
        "[class*='LinkButton']",
        "[class*='card']",
        "a[class*='link']",
    ]
    return _scrape_url(profile_url, "litlink", selectors, LITLINK_STORAGE_STATE_PATH)


def scrape_littly(profile_url: str) -> MultilinkResult:
    """litt.ly/{id} 프로필 페이지 수집.

    `a.shadow-view-block` 은 실제 litt.ly 프로필 페이지(예: litt.ly/gonggu_alimi)의
    링크 카드에서 브라우저로 직접 확인한 셀렉터다.
    """
    selectors = [
        "a[class*='shadow-view-block']",
        "[class*='LinkBlock']",
    ]
    return _scrape_url(profile_url, "littly", selectors)


def scrape_linktree(profile_url: str) -> MultilinkResult:
    """linktr.ee/{id} 프로필 페이지 수집.

    `[data-testid='LinkClickTriggerLink']` 은 실제 linktr.ee 프로필 페이지
    (예: linktr.ee/spotify)에서 브라우저로 직접 확인한 셀렉터다.
    """
    selectors = [
        "[data-testid='LinkClickTriggerLink']",
        "[data-testid='Link']",
    ]
    return _scrape_url(profile_url, "linktree", selectors)


def scrape_multilink(multilink_type: str, url: str) -> MultilinkResult:
    if multilink_type == "inpock":
        return scrape_inpock(url)
    if multilink_type == "litlink":
        return scrape_litlink(url)
    if multilink_type == "littly":
        return scrape_littly(url)
    if multilink_type == "linktree":
        return scrape_linktree(url)
    raise ValueError(f"알 수 없는 multilink_type: {multilink_type}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("사용법: python -m scraper.multilink_scraper <inpock|litlink> <url>")
        raise SystemExit(1)
    result = scrape_multilink(sys.argv[1], sys.argv[2])
    print(f"--- {result.source} raw_text ({len(result.raw_text)} chars) ---")
    print(result.raw_text[:2000])
    print(f"--- link_items ({len(result.link_items)}) ---")
    for item in result.link_items[:20]:
        print("-", item)
