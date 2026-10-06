"""체험/여행/숙소 공구 1회성 백필 (2026-10-06).

배경: 체험 분류 기능(10/2) 이전에 한 번 판정된 글, 그리고 공구 신호어가 없어 사전필터에서 탈락했던 글은
processed_blobs 캐시/필터 때문에 체험으로 재판정되지 않았다. 이 스크립트는
  1) data/raw_collected.json 의 캡션 중 체험 힌트 키워드가 든 글만 processed_blobs 에서 해시를 지워 재검사 대상으로 만들고
  2) parser.extract_schedule.run() 으로 파싱한다(나머지 글은 기존 캐시로 건너뜀).
재실행해도 안전하다(재판정된 글은 다시 캐시에 기록되므로 두 번째 실행에선 지울 해시가 거의 없다).

실행:  python tools/backfill_experience.py [--dry-run]
      (실행 전에 data/gonggu.db 를 백업할 것. Claude API 호출 비용이 든다.)
야간 파이프라인이 도는 중에는 실행하지 말 것.
"""
import hashlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gonggu_db  # noqa: E402
from config import EXPERIENCE_HINT_KEYWORDS, RAW_COLLECTED_PATH  # noqa: E402
from parser.category_filter import quick_prefilter  # noqa: E402
from parser.extract_schedule import clean_blob_text  # noqa: E402

logger = logging.getLogger("backfill_experience")


def candidate_hashes() -> list[str]:
    entries = json.loads(RAW_COLLECTED_PATH.read_text(encoding="utf-8"))
    hashes = []
    for e in entries:
        for cap in (e.get("instagram") or {}).get("captions") or []:
            text = cap.get("text") or ""
            hay = text.replace(" ", "")
            if not any(k in hay for k in EXPERIENCE_HINT_KEYWORDS):
                continue
            if not quick_prefilter(text):
                continue            # 새 사전필터에서도 탈락하면 어차피 Claude에 가지 않는다
            clean = clean_blob_text(text)
            if clean:
                hashes.append(hashlib.sha256(clean.encode("utf-8")).hexdigest())
    return list(dict.fromkeys(hashes))


def main(dry_run: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    gonggu_db.init_db()
    hashes = candidate_hashes()
    with gonggu_db.get_conn() as conn:
        cached = [h for h in hashes if conn.execute("SELECT 1 FROM processed_blobs WHERE text_hash = ?", (h,)).fetchone()]
    logger.info("체험 후보 글 %d개 중 캐시에 기록된(재검사 필요) 글 %d개", len(hashes), len(cached))
    if dry_run:
        return
    with gonggu_db.get_conn() as conn:
        conn.executemany("DELETE FROM processed_blobs WHERE text_hash = ?", [(h,) for h in cached])
    logger.info("캐시 해제 %d건 - 파싱 실행", len(cached))
    from parser.extract_schedule import run
    stats = run()
    logger.info("백필 파싱 결과: %s", {k: v for k, v in stats.items() if k != "usage"})


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
