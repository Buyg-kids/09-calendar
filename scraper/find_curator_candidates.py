"""[1회성 조사] @we09.lab 같은 공구 큐레이션(모음) 계정 후보를 찾아 검증한다.

1) 큐레이션 계정들이 쓰는 해시태그 페이지(로그인 세션)에서 게시물 shortcode를 모은다.
2) 각 게시물의 공개 임베드 페이지(로그인 불필요)에서 작성자 + 본문을 읽어
   '상품 | 핸들' / '상품 @핸들' 목록형 게시물인지 1차 판정한다.
3) 1차 통과 계정만 최근 게시물(scrape_insta)로 꾸준히 그런 형식인지 2차 검증한다.
   당첨자 발표/이벤트 게시물은 제외하고, 나열된 핸들이 기존 targets.json의
   인플루언서와 얼마나 겹치는지(=진짜 공구 인플루언서 목록인지)를 근거로 삼는다.

등록은 하지 않는다 - 결과(JSON)를 보고 사람이 판단해 targets.json에 is_curator로 넣는다.
야간 파이프라인 실행 중에는 돌리지 말 것 (같은 로그인 세션을 쓴다).

실행:
    python -m scraper.find_curator_candidates
"""
from __future__ import annotations

import json
import logging
import random
import re
import time

from playwright.sync_api import sync_playwright

from config import BASE_DIR, TARGETS_PATH
from scraper import auto_discover_scheduler as ad
from scraper.curator import PRODUCT_HANDLE_RE
from scraper.insta_scraper import scrape_insta

logger = logging.getLogger(__name__)

SEED_HASHTAGS = ["오늘의공구", "공구일정", "공동구매정보", "육아핫딜", "공구모음", "공구연구소", "공구아카이브", "육아공구모음"]
POSTS_PER_TAG = 12
OUT_PATH = BASE_DIR / "data" / "curator_candidates.json"

MENTION_LINE_RE = re.compile(r"^[•\-·▪️✔️🔸🔹\s]*(.+?)\s*[|｜]?\s*@([A-Za-z0-9_.]{3,30})\s*$", re.MULTILINE)
EVENT_RE = re.compile(r"당첨|발표|추첨|이벤트 결과|축하드")


def _known_handles() -> set[str]:
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    out = set()
    for t in targets:
        out.add((t.get("instagram_id") or "").lstrip("@").lower())
        out.add((t.get("influencer_name") or "").lower())
    return {h for h in out if h}


def listed_handles(text: str) -> list[str]:
    """목록형 줄에서 핸들만 뽑는다 (we09.lab 형식 '- 상품 | 핸들' + '상품 @핸들' 형식)."""
    hs = [m[1] for m in PRODUCT_HANDLE_RE.findall(text or "")]
    hs += [m[1] for m in MENTION_LINE_RE.findall(text or "")]
    return [h.lower().rstrip(".") for h in hs]


def _embed(browser, code: str) -> tuple[str | None, str]:
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(f"https://www.instagram.com/p/{code}/embed/captioned/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
    except Exception:  # noqa: BLE001
        return None, ""
    finally:
        ctx.close()
    author = next((ln.strip() for ln in body.splitlines() if ln.strip() and ad._looks_like_username(ln.strip())), None)
    return author, body


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    known = _known_handles()
    first_pass: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for tag in SEED_HASHTAGS:
                try:
                    codes = ad._get_hashtag_post_shortcodes(browser, tag, POSTS_PER_TAG)
                except Exception as e:  # noqa: BLE001
                    logger.error("#%s 스캔 실패: %s", tag, e)
                    continue
                logger.info("#%s -> 게시물 %d개", tag, len(codes))
                for code in codes:
                    time.sleep(random.uniform(2.0, 4.0))
                    author, body = _embed(browser, code)
                    if not author:
                        continue
                    hs = [h for h in listed_handles(body) if h != author.lower()]
                    is_event = bool(EVENT_RE.search(body[:600]))
                    rec = first_pass.setdefault(author, {"tags": set(), "best_list": 0, "event_only": True, "sample": code})
                    rec["tags"].add(tag)
                    if len(hs) > rec["best_list"]:
                        rec["best_list"], rec["sample"] = len(hs), code
                    if not is_event and len(hs) >= 5:
                        rec["event_only"] = False
                time.sleep(random.uniform(15, 30))
        finally:
            browser.close()

    shortlist = [a for a, r in first_pass.items() if r["best_list"] >= 5 and not r["event_only"] and a.lower() not in {"we09.lab"}]
    logger.info("1차: 작성자 %d명 중 목록형 게시물 계정 %d명: %s", len(first_pass), len(shortlist), shortlist)

    results = []
    for handle in shortlist:
        time.sleep(random.uniform(20, 40))
        try:
            r = scrape_insta(handle, max_posts=6)
        except Exception as e:  # noqa: BLE001
            logger.error("@%s 2차 검증 실패: %s", handle, e)
            continue
        posts = [c["text"] for c in r.captions if c.get("text")]
        list_posts, all_handles = 0, set()
        for t in posts:
            if EVENT_RE.search(t[:600]):
                continue
            hs = {h for h in listed_handles(t) if h != handle.lower()}
            if len(hs) >= 5:
                list_posts += 1
                all_handles |= hs
        overlap = len(all_handles & known)
        results.append({
            "handle": handle,
            "bio": (r.bio_text or "")[:200],
            "posts_checked": len(posts),
            "list_posts": list_posts,
            "unique_listed_handles": len(all_handles),
            "already_known": overlap,
            "new_handles": len(all_handles - known),
            "regex_compatible": sum(1 for t in posts if len(PRODUCT_HANDLE_RE.findall(t)) >= 5),
            "hashtags": sorted(first_pass[handle]["tags"]),
            "sample_post": first_pass[handle]["sample"],
        })
        logger.info("2차 @%s: %s", handle, results[-1])

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("결과 저장: %s (%d개 계정)", OUT_PATH, len(results))


if __name__ == "__main__":
    main()
