"""[Agent 1: Scraper] data/targets.json 의 타겟 인플루언서를 순회하며

  - 멀티링크(인포크/릿링크) 프로필의 공구 일정/상품명/링크  [메인 수집 소스]
  - 인스타그램 공개 피드/릴스 최근 캡션                    [보조, instagram_id가 있을 때만]

을 수집해 원본 그대로 data/raw_collected.json 에 저장한다.
scraper/discover.py 로 자동 발굴된 타겟은 instagram_id가 비어 있으므로
인스타그램 로그인/스크래핑을 아예 시도하지 않고 멀티링크 공개 페이지만
수집한다 — 인스타그램 계정 차단 위험을 피하기 위한 의도적인 설계다.
카테고리 판단/정제는 하지 않는다 (그 역할은 다음 단계인 parser/ 가 맡는다).

실행:
    python -m scraper.collector
"""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from config import RAW_COLLECTED_PATH, TARGETS_PATH
from scraper.multilink_scraper import scrape_multilink
from scraper.insta_scraper import scrape_insta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 인스타그램 로그인 세션으로 프로필을 방문하는 대상 사이에만 두는 대기 시간
# (멀티링크 전용 타겟은 인스타그램 서버를 아예 안 건드리므로 대상에서 제외).
# auto_discover_scheduler.py의 "시간당 6~8개" 페이스보다는 빠르지만, targets.json
# 전체(수십~백여 개)를 한 세션에서 쉬지 않고 연달아 훑는 버스트 패턴을 피하기 위함.
COLLECT_WAIT_MIN_SEC = 15.0
COLLECT_WAIT_MAX_SEC = 35.0


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_multilink_type(url: str) -> str | None:
    if not url:
        return None
    if "inpock" in url or "inpk.link" in url:
        return "inpock"
    if "litt.ly" in url:
        return "littly"
    if "linktr.ee" in url:
        return "linktree"
    if "lit.link" in url:
        return "litlink"
    logger.warning("멀티링크 URL에서 서비스 종류를 판별하지 못함: %s", url)
    return None


def _strip_handle(instagram_id: str) -> str:
    return (instagram_id or "").lstrip("@").strip()


def load_targets() -> list[dict]:
    if not TARGETS_PATH.exists():
        raise FileNotFoundError(
            f"{TARGETS_PATH} 가 없습니다. influencer_name/instagram_id/multilink_url/"
            "primary_focus 형식으로 타겟 목록을 먼저 만들어주세요."
        )
    return json.loads(TARGETS_PATH.read_text(encoding="utf-8"))


def collect_target(target: dict) -> dict:
    influencer_name = target.get("influencer_name", "")
    instagram_id = target.get("instagram_id", "")
    multilink_url = target.get("multilink_url", "")
    handle = _strip_handle(instagram_id)

    entry: dict = {
        "influencer_name": influencer_name,
        "instagram_id": instagram_id,
        "multilink_url": multilink_url,
        "primary_focus": target.get("primary_focus", ""),
        "collected_at": _utcnow(),
        "multilink": None,
        "instagram": None,
        "errors": [],
    }

    multilink_type = _detect_multilink_type(multilink_url)
    if multilink_type:
        try:
            result = scrape_multilink(multilink_type, multilink_url)
            entry["multilink"] = asdict(result)
            logger.info(
                "[%s] %s 멀티링크 수집 완료 (텍스트 %d자, 링크항목 %d개)",
                influencer_name, multilink_type, len(result.raw_text), len(result.link_items),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] 멀티링크 수집 실패", influencer_name)
            entry["errors"].append(f"multilink: {exc}")

    if handle:
        try:
            ig = scrape_insta(handle)
            entry["instagram"] = asdict(ig)
            logger.info("[%s] 인스타그램 수집 완료 (게시물 %d개)", influencer_name, len(ig.captions))
        except FileNotFoundError as exc:
            logger.warning("[%s] 인스타그램 수집 건너뜀: %s", influencer_name, exc)
            entry["errors"].append(f"instagram: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] 인스타그램 수집 실패", influencer_name)
            entry["errors"].append(f"instagram: {exc}")

    return entry


def run() -> list[dict]:
    targets = load_targets()
    # [VIP 소스 전략 1] 큐레이션 계정(is_curator=true)을 최우선 순위로 먼저
    # 수집한다 - 이 계정들의 캡션이 곧 다른 다수 계정 발굴의 원천(scraper/curator.py)
    # 이므로 가장 먼저 최신 상태로 확보해둘 가치가 있다. sorted()는 안정 정렬이라
    # curator 여부 외의 상대 순서는 그대로 유지된다.
    targets = sorted(targets, key=lambda t: not t.get("is_curator"))
    curator_count = sum(1 for t in targets if t.get("is_curator"))
    logger.info("타겟 %d명 수집 시작 (큐레이션 계정 %d명 최우선 처리)", len(targets), curator_count)

    results: list[dict] = []
    for target in targets:
        results.append(collect_target(target))
        if _strip_handle(target.get("instagram_id", "")):
            wait_sec = random.uniform(COLLECT_WAIT_MIN_SEC, COLLECT_WAIT_MAX_SEC)
            logger.info("인스타그램 프로필 방문 후 %.1f초 대기 (계정 차단 방지)", wait_sec)
            time.sleep(wait_sec)

    RAW_COLLECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_COLLECTED_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("수집 완료. 저장 위치: %s", RAW_COLLECTED_PATH)
    return results


if __name__ == "__main__":
    run()
