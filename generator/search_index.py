"""[메인 통합 검색 인덱스] 지도 탭 데이터(TourAPI 행사·축제 / 서울 공공서비스예약 / KOPIS 어린이 공연)를 한 개의 작은 JSON(search_index.json)으로 모은다.
index.html 검색창이 처음 포커스될 때 한 번만 가져와(지연 로딩) '주말나들이·체험' 탭 결과로 쓴다. index.html 에는 내장하지 않는다.

- 항목은 짧은 배열: [id, kind, title, sub, start, end, fee, hay]
    kind: ev(행사·축제 -> map.html?tab=festival) / rs(예약 체험 -> ?tab=free) / sh(공연 -> ?tab=show). id 는 map.html ?place= 딥링크에 그대로 쓴다.
    hay : 제목을 뺀 검색용 소문자 문자열(장소·지역·분류·유형 별칭 '축제 행사', '체험 예약', '공연 뮤지컬 어린이' 등).
- 종료된 항목(행사/공연 종료일, 예약 접수 마감일이 오늘 이전)은 넣지 않는다. 클라이언트도 한 번 더 거른다.
- 원본 JSON 이 하나라도 없거나 깨졌으면(불완전) 기존 search_index.json 을 덮어쓰지 않는다. 0건이거나 이전 대비 40% 미만이어도 유지한다.

안전 규칙 (run_nightly_pipeline.ps1이 로그의 "Traceback"을 실패로 판정해 배포를 건너뜀):
run()은 절대 예외를 던지지 않고(logger.exception 금지) 실패 시 한 줄 로그만 남기고 None 을 반환한다.

실행 (단독):  python -m generator.search_index [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

from config import BASE_DIR

logger = logging.getLogger(__name__)

OUTPUT_PATH = BASE_DIR / "search_index.json"
SOURCES = {
    "ev": ("tour_events.json", "events"),
    "rs": ("seoul_reserve.json", "items"),
    "sh": ("kids_performances.json", "performances"),
}
MIN_PREV_FOR_DROP_GUARD = 30
MIN_KEEP_RATIO = 0.4
MAX_BYTES = 600_000          # 크기 상한(초과하면 이상 응답으로 보고 기존 파일 유지)
_ADDR_KEEP = 40              # 주소는 앞 40자만(시/군/구/동까지면 충분)


def _clean(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _fee(it: dict) -> str:
    if it.get("is_free") is True or it.get("fee_type") == "free":
        return "무료"
    mn = it.get("fee_min")
    if isinstance(mn, (int, float)) and mn > 0:
        return f"{int(mn):,}원"
    return ""


def _hay(*parts: str) -> str:
    return _clean(" ".join(p for p in parts if p)).lower()


def _live(end: str, today_iso: str) -> bool:
    return not end or end >= today_iso


def make_item(kind: str, it: dict, today_iso: str) -> list | None:
    """원본 한 건 -> 인덱스 배열. 제목/id 가 없거나 이미 종료됐으면 None."""
    iid, title = _clean(it.get("id")), _clean(it.get("title"))
    if not iid or not title:
        return None
    start, end = _clean(it.get("start_date")), _clean(it.get("end_date"))
    if kind == "ev":
        if not _live(end or start, today_iso):
            return None
        place = _clean(it.get("place"))
        sub = " · ".join(x for x in (_clean(it.get("region")), place) if x)
        hay = _hay(_clean(it.get("address"))[:_ADDR_KEEP], it.get("region"), "축제 행사 이벤트")
    elif kind == "rs":
        if not str(it.get("reserve_url") or "").startswith("https://"):
            return None
        rcpt = _clean(it.get("rcpt_end"))
        if not _live(rcpt or end or start, today_iso):
            return None
        sub = " · ".join(x for x in (_clean(it.get("region")), _clean(it.get("place"))) if x)
        hay = _hay(_clean(it.get("address"))[:_ADDR_KEEP], it.get("region"), it.get("category"), "체험 예약 프로그램 신청")
        end = rcpt or end        # 예약 항목의 종료일은 접수 마감일 기준으로 보여 준다
    elif kind == "sh":
        if not _live(end or start, today_iso):
            return None
        sub = " · ".join(x for x in (_clean(it.get("region")), _clean(it.get("venue"))) if x)
        hay = _hay(it.get("region"), it.get("genre"), "공연 어린이 아이와", _clean(it.get("age")))
    else:
        return None
    return [iid, kind, title, sub, start, end, _fee(it), hay]


def build_items(today: date | None = None, base: Path | None = None) -> tuple[list[list], bool]:
    """(항목들, complete). complete=False 면 원본 중 하나라도 없거나 읽을 수 없다는 뜻."""
    today_iso = (today or date.today()).isoformat()
    root = base or BASE_DIR
    items: list[list] = []
    complete = True
    seen: set[str] = set()
    for kind, (fname, key) in SOURCES.items():
        try:
            rows = json.loads((root / fname).read_text(encoding="utf-8")).get(key)
        except (OSError, ValueError, AttributeError):
            rows = None
        if not isinstance(rows, list):
            complete = False
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            row = make_item(kind, r, today_iso)
            if row and row[0] not in seen:
                seen.add(row[0]); items.append(row)
    return items, complete


def _existing_count() -> int:
    try:
        return int(json.loads(OUTPUT_PATH.read_text(encoding="utf-8")).get("count", 0))
    except (OSError, ValueError, TypeError):
        return 0


def run(dry_run: bool = False) -> "Path | dict | None":
    try:
        items, complete = build_items()
        payload = {"v": 1, "generated_at": datetime.now().isoformat(timespec="seconds"), "count": len(items), "items": items}
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if dry_run:
            return {"complete": complete, "count": len(items), "bytes": len(text.encode("utf-8")), "by_kind": {k: sum(1 for i in items if i[1] == k) for k in SOURCES}}
        if not complete:
            logger.error("통합 검색 인덱스: 원본 JSON 이 불완전 - 기존 파일 유지")
            return None
        if not items:
            logger.error("통합 검색 인덱스 0건 - 기존 파일 유지")
            return None
        if len(text.encode("utf-8")) > MAX_BYTES:
            logger.error("통합 검색 인덱스 크기 상한 초과 - 기존 파일 유지")
            return None
        prev = _existing_count()
        if prev >= MIN_PREV_FOR_DROP_GUARD and len(items) < prev * MIN_KEEP_RATIO:
            logger.error("통합 검색 인덱스 %d건 -> %d건으로 급감 - 기존 파일 유지", prev, len(items))
            return None
        tmp = OUTPUT_PATH.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(OUTPUT_PATH)
        logger.info("통합 검색 인덱스 JSON 생성 (%d건, %d바이트): %s", len(items), len(text.encode("utf-8")), OUTPUT_PATH)
        return OUTPUT_PATH
    except Exception as e:
        logger.error("통합 검색 인덱스 단계 오류 - 무시하고 계속 진행 (%s)", type(e).__name__)
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = run(dry_run=a.dry_run)
    if isinstance(res, dict):
        print(json.dumps(res, ensure_ascii=False, indent=2))
