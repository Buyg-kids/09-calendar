"""지역이 비어 있는 체험(experience) 행을 보강된 지명 사전으로 다시 채운다 (2026-10-06, 재실행 안전).

실행:  python tools/fix_experience_regions.py [--dry-run]   (실행 전 data/gonggu.db 백업)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gonggu_db  # noqa: E402
from parser.extract_schedule import _verify_region  # noqa: E402


def main(dry_run: bool = False) -> None:
    gonggu_db.init_db()
    changed, total = [], 0
    with gonggu_db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, product_name, place_name, caption_text FROM gonggu "
            "WHERE item_type = 'experience' AND (region IS NULL OR region = '')"
        ).fetchall()
        total = len(rows)
        for r in rows:
            # 장소명/상품명만 근거로 쓴다(원문 전체에는 다른 지역 이야기가 섞여 있어 오매핑 위험)
            region = _verify_region(None, [r["place_name"], r["product_name"]])
            if region:
                changed.append((region, r["id"], r["product_name"]))
        if not dry_run:
            conn.executemany("UPDATE gonggu SET region = ? WHERE id = ?", [(c[0], c[1]) for c in changed])
    print(f"지역 비어 있던 체험 행 {total}건 중 {len(changed)}건 채움" + (" (dry-run)" if dry_run else ""))
    for region, _id, name in changed[:15]:
        print("  ", region, "|", (name or "")[:40])


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
