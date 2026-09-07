"""로그인 쿠키(data/insta_cookies.json)를 사용해 인스타그램 프로필 bio와
최근 피드/릴스 캡션을 수집한다.

login_helper.py 로 미리 쿠키를 저장해둬야 한다 (data/insta_cookies.json).
프로필 방문(bio + 최근 게시물 목록 확인)만 로그인 세션을 쓰고, 캡션 텍스트
자체는 로그인이 필요 없는 공개 임베드 페이지에서 가져와 인증 세션에 대한
요청 빈도를 최소화한다 - 계정 정지 리스크를 낮추기 위한 의도적인 설계다.

실행:
    python -m scraper.insta_scraper <handle>
"""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from config import INSTA_COOKIES_PATH

logger = logging.getLogger(__name__)

MAX_POSTS_PER_RUN = 5
MIN_DELAY_SEC = 2.0
MAX_DELAY_SEC = 5.0


@dataclass
class InstaResult:
    handle: str
    bio_text: str = ""
    captions: list[dict] = field(default_factory=list)  # [{shortcode, type, text}]


def _polite_sleep() -> None:
    time.sleep(random.uniform(MIN_DELAY_SEC, MAX_DELAY_SEC))


def _load_cookies() -> list[dict]:
    if not INSTA_COOKIES_PATH.exists():
        raise FileNotFoundError(
            f"{INSTA_COOKIES_PATH} 가 없습니다. 먼저 `python login_helper.py` 를 실행해 "
            "로그인 쿠키를 저장하세요."
        )
    return json.loads(INSTA_COOKIES_PATH.read_text(encoding="utf-8"))


def _get_bio_and_shortcodes(handle: str, limit: int) -> tuple[str, list[tuple[str, str]]]:
    """로그인 쿠키로 프로필 페이지를 방문해 bio 텍스트와 최근 게시물
    (shortcode, 'p'|'reel') 목록을 수집한다."""
    cookies = _load_cookies()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        try:
            # 게시물 그리드는 클라이언트에서 늦게(지연) 렌더링되므로 domcontentloaded로는
            # 부족하다 - networkidle까지 기다리고 살짝 스크롤까지 해줘야 /p/,/reel/ 링크가
            # DOM에 나타난다 (실제 계정으로 직접 확인함).
            page.goto(f"https://www.instagram.com/{handle}/", wait_until="networkidle", timeout=25000)
            try:
                page.wait_for_selector("header", timeout=8000)
            except PWTimeoutError:
                pass
            page.wait_for_timeout(1500)
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(1500)

            bio_text = ""
            try:
                # 'header section'이 아니라 header 전체 innerText를 써야 bio/통계/
                # 공구 안내 문구까지 전부 잡힌다 (실제 페이지로 직접 확인함).
                bio_text = page.eval_on_selector("header", "el => el.innerText") or ""
            except Exception:
                pass

            hrefs = page.eval_on_selector_all(
                "a[href*='/p/'], a[href*='/reel/']",
                "els => els.map(e => e.getAttribute('href'))",
            )
            shortcodes: list[tuple[str, str]] = []
            seen = set()
            for href in hrefs:
                if not href:
                    continue
                parts = [seg for seg in href.split("/") if seg]
                if len(parts) < 2:
                    continue
                kind = "reel" if "reel" in parts else "p"
                code = parts[-1]
                if code in seen:
                    continue
                seen.add(code)
                shortcodes.append((code, kind))
                if len(shortcodes) >= limit:
                    break

            return bio_text, shortcodes
        finally:
            browser.close()


def _get_caption(shortcode: str, kind: str) -> tuple[str, str]:
    """로그인 불필요한 공개 임베드 페이지에서 캡션 텍스트 + 대표 이미지 URL 추출
    (인증 세션 보호). 반환: (caption_text, image_url).

    img.EmbeddedMediaImage 는 사진/캐러셀 게시물과 릴스(비디오 포스터 프레임)
    양쪽 모두에서 실제 콘텐츠 이미지를 가리키는 걸 브라우저로 직접 확인한
    셀렉터다 (프로필 아바타 100x100, 사이드바 추천 썸네일 150x150과 구분됨).

    주의: 이 URL은 인스타그램 CDN의 서명된(만료 시간이 박힌) 링크라 시간이
    지나면 깨진다 - 그래서 view.html 쪽에서 로드 실패 시 카테고리별
    플레이스홀더로 자동 대체하는 처리가 반드시 같이 있어야 한다."""
    url = f"https://www.instagram.com/{kind}/{shortcode}/embed/captioned/"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            try:
                page.wait_for_selector("[class*='Caption']", timeout=5000)
            except PWTimeoutError:
                pass
            text = page.inner_text("body")

            image_url = ""
            try:
                image_url = page.eval_on_selector("img.EmbeddedMediaImage", "el => el.src") or ""
            except Exception:
                pass
            if not image_url:
                try:
                    image_url = page.eval_on_selector("video", "el => el.poster") or ""
                except Exception:
                    pass

            return text, image_url
        finally:
            browser.close()


def scrape_insta(handle: str, max_posts: int = MAX_POSTS_PER_RUN) -> InstaResult:
    bio_text, shortcodes = _get_bio_and_shortcodes(handle, max_posts)
    result = InstaResult(handle=handle, bio_text=bio_text)

    for code, kind in shortcodes:
        _polite_sleep()
        try:
            text, image_url = _get_caption(code, kind)
        except Exception as exc:  # noqa: BLE001
            logger.warning("캡션 수집 실패 %s/%s: %s", handle, code, exc)
            continue
        result.captions.append({"shortcode": code, "type": kind, "text": text, "image_url": image_url})

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("사용법: python -m scraper.insta_scraper <handle>")
        raise SystemExit(1)
    r = scrape_insta(sys.argv[1])
    print("--- bio ---")
    print(r.bio_text)
    for c in r.captions:
        print(f"--- {c['type']} {c['shortcode']} ---")
        print(c["text"][:500])
