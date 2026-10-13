"""[연령 뱃지 사전 계산] 지도 상세 시트의 연령 적합도 뱃지(0~1세 베이비 / 2~3세 토들러 / 4~5세 키즈 / 6~7세 프리스쿨)용으로
tour_events.json · seoul_reserve.json · free_spots_seed.json 의 각 항목에 `age_groups`(키 목록)를 미리 계산해 넣는다.
키·구간은 홈의 '연령별 인기공구'와 같은 generator/age_tags.py 하나만 쓴다(프론트에 같은 규칙을 다시 구현하지 않는다).

항목별 텍스트 규칙(추정이 어려운 항목은 비워 둔다 - 뱃지는 숨겨진다):
- event  : 제목
- reserve: 제목 + 이용대상. 단 대상 문구에서 '초등학생·중학생·고등학생·청소년·성인' 은 먼저 지운다('초등' 키워드가 6~7세로 오분류되는 것을 막는다).
- spot   : 제목 + 분류(키즈카페 등)

안전 규칙 (run_nightly_pipeline.ps1이 로그의 "Traceback"을 실패로 판정해 배포를 건너뜀):
run()은 절대 예외를 던지지 않고(logger.exception 금지) 파일을 하나씩 따로 처리한다. 읽을 수 없거나 형식이 다르면 그 파일은 건드리지 않고,
내용이 바뀐 파일만 임시 파일 -> 교체 방식으로 쓴다(멱등).

실행 (단독):  python -m generator.age_annotate [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import re

from config import BASE_DIR
from generator.age_tags import age_groups

logger = logging.getLogger(__name__)

# (파일, 목록 키, 종류). free_spots_seed.json 은 'spots' 키(없으면 최상위 리스트).
SOURCES = [("tour_events.json", "events", "event"), ("seoul_reserve.json", "items", "reserve"), ("free_spots_seed.json", "spots", "spot")]
_OLDER = re.compile(r"초등\S*|중학생|고등학생|청소년|성인")


def ages_for_item(kind: str, it: dict) -> list[str]:
    title = str(it.get("title") or "")
    if kind == "reserve":
        target = _OLDER.sub(" ", str(it.get("target") or ""))
        return age_groups(f"{title} {target}")
    if kind == "spot":
        return age_groups(f"{title} {it.get('category') or ''}")
    return age_groups(title)


def annotate_file(fname: str, key: str, kind: str, dry_run: bool = False) -> dict | None:
    """파일 하나를 처리. 반환: {'count','tagged','changed'} 또는 None(읽기 불가/형식 불일치 -> 파일은 그대로)."""
    path = BASE_DIR / fname
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    items = raw if isinstance(raw, list) else (raw.get(key) if isinstance(raw, dict) else None)
    if not isinstance(items, list):
        return None
    changed = 0
    tagged = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        ages = ages_for_item(kind, it)
        tagged += 1 if ages else 0
        if it.get("age_groups") != ages:
            it["age_groups"] = ages
            changed += 1
    if changed and not dry_run:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    return {"count": len(items), "tagged": tagged, "changed": changed}


def run(dry_run: bool = False) -> dict | None:
    try:
        out = {}
        for fname, key, kind in SOURCES:
            res = annotate_file(fname, key, kind, dry_run)
            if res is None:
                logger.error("연령 뱃지 계산: %s 를 읽을 수 없거나 형식이 달라 건너뜀", fname)
                continue
            out[fname] = res
        if out and not dry_run:
            logger.info("연령 뱃지 계산 완료: %s", ", ".join(f"{f} {r['tagged']}/{r['count']}" for f, r in out.items()))
        return out
    except Exception as e:
        logger.error("연령 뱃지 계산 단계 오류 - 무시하고 계속 진행 (%s)", type(e).__name__)
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run(dry_run=a.dry_run), ensure_ascii=False, indent=2))
