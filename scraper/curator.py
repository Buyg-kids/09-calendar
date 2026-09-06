"""[VIP 소스] '공구연구소'류 큐레이션 계정 전용 처리.

@we09.lab처럼 여러 인플루언서의 공구 일정을 매일 정리해 올리는 계정은 한 번
수집으로 수십 명의 실제 활동 계정을 한꺼번에 확보할 수 있어 데이터 효율이
압도적으로 높다 (일반 계정 1개 수집 = 공구 1~2건, 큐레이션 계정 1개 수집 =
연결된 실제 계정 수십 개 + 그날의 공구 목록).

이 모듈이 하는 일:
  1) targets.json에 is_curator=true로 표시된 계정의 최근 캡션에서
     '상품명 | 핸들' 형식의 목록을 파싱한다 (큐레이션 계정 특유의 정리 포맷).
  2) 새로 나온 핸들을 즉시 targets.json에 신규 타겟으로 등록한다 - 이미
     활동성과 공구 진행이 검증된 'A급 인플루언서'이므로 별도 프로필 필터링
     없이 바로 편입시킨다.

auto_discover_scheduler.py가 매일 밤 활성 시간대에 진입하자마자(가장 먼저)
run_curator_scan()을 호출해서, 다음날부터 새로 등록된 핸들들이 일반 수집
대상(collector.py)에 합류하도록 한다.

실행:
    python -m scraper.curator
"""
from __future__ import annotations

import json
import logging
import random
import re
import time

from config import TARGETS_PATH
from scraper.insta_scraper import scrape_insta

logger = logging.getLogger(__name__)

# 큐레이션 계정 특유의 정리 포맷: '- 상품명 | 핸들' 또는 '• 상품명 | 핸들'.
# (@we09.lab 실측 캡션으로 확인된 형식)
PRODUCT_HANDLE_RE = re.compile(r"^[•\-]\s*(.+?)\s*\|\s*([A-Za-z0-9_.]+)\s*$", re.MULTILINE)

# 일반 계정(insta_scraper.MAX_POSTS_PER_RUN=5)보다 훨씬 많이 본다 - 큐레이션
# 계정 하나가 곧 수십 명 분의 소스이므로 더 깊이 파고들 가치가 있다.
CURATOR_MAX_POSTS = 15

# 큐레이션 계정을 여러 개 스캔할 때 계정 사이에 두는 대기 (계정 차단 방지).
BETWEEN_CURATORS_MIN_SEC = 8.0
BETWEEN_CURATORS_MAX_SEC = 18.0


def _load_targets() -> list[dict]:
    if not TARGETS_PATH.exists():
        return []
    try:
        return json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_targets(targets: list[dict]) -> None:
    TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGETS_PATH.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")


def get_curator_handles() -> list[str]:
    """targets.json에서 is_curator=true인 계정의 핸들 목록."""
    return [
        (t.get("influencer_name") or "").strip()
        for t in _load_targets()
        if t.get("is_curator") and (t.get("influencer_name") or "").strip()
    ]


def extract_handles_from_text(text: str) -> list[tuple[str, str]]:
    """캡션 텍스트에서 (상품명, 핸들) 쌍 목록 추출."""
    return [(m.group(1).strip(), m.group(2).strip()) for m in PRODUCT_HANDLE_RE.finditer(text or "")]


def register_new_targets(handles: list[str], source: str) -> int:
    """새 핸들들을 targets.json에 신규 타겟으로 등록한다 (이미 있으면 건너뜀).
    반환값: 실제로 새로 추가된 수."""
    targets = _load_targets()
    known_ids = {(t.get("instagram_id") or "").lstrip("@").lower() for t in targets if t.get("instagram_id")}
    known_names = {(t.get("influencer_name") or "").lower() for t in targets}

    added = 0
    for handle in handles:
        key = handle.lower()
        if key in known_ids or key in known_names:
            continue
        targets.append(
            {
                "influencer_name": handle,
                "instagram_id": f"@{handle}",
                "multilink_url": "",
                "multilink_type": "",
                "primary_focus": "육아용품",
                "source": source,
            }
        )
        known_ids.add(key)
        known_names.add(key)
        added += 1

    if added:
        _save_targets(targets)
    return added


def scan_curator(handle: str) -> dict:
    """큐레이션 계정 하나를 스캔해 캡션에서 (상품, 핸들) 목록을 뽑고, 새로
    나온 핸들을 targets.json에 등록한다."""
    result = scrape_insta(handle, max_posts=CURATOR_MAX_POSTS)
    pairs: list[tuple[str, str]] = []
    for c in result.captions:
        pairs.extend(extract_handles_from_text(c["text"]))

    unique_handles = sorted({h for _, h in pairs})
    added = register_new_targets(unique_handles, source=f"curator:{handle}")

    logger.info(
        "[curator] @%s 스캔 완료 - 상품 %d건 / 연결 계정 %d명 발견 / 신규 타겟 %d명 등록",
        handle, len(pairs), len(unique_handles), added,
    )
    return {"handle": handle, "pairs": pairs, "unique_handles": unique_handles, "added": added}


def run_curator_scan() -> list[dict]:
    """등록된 모든 curator 계정을 스캔한다 - 나이트 스케줄러가 매일 밤 활성
    시간대 진입 직후 가장 먼저 호출한다."""
    handles = get_curator_handles()
    if not handles:
        logger.info("[curator] 등록된 큐레이션 계정이 없음 - 건너뜀")
        return []

    results = []
    for i, h in enumerate(handles):
        try:
            results.append(scan_curator(h))
        except Exception:
            logger.exception("[curator] @%s 스캔 실패", h)
        if i < len(handles) - 1:
            time.sleep(random.uniform(BETWEEN_CURATORS_MIN_SEC, BETWEEN_CURATORS_MAX_SEC))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_curator_scan()
