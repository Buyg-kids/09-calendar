"""discover_sellers.py 가 만든 data/candidates_discovered.json 후보를 검토하고, 승인한 핸들만 targets.json 에 병합.

기본은 드라이런(파일을 바꾸지 않음). 실제 병합은 --apply 가 있을 때만, 병합 전 targets.json 을 백업한다.

  python tools/merge_candidates.py --list
  python tools/merge_candidates.py --accept handle1,handle2            # 드라이런
  python tools/merge_candidates.py --accept handle1,handle2 --apply
  python tools/merge_candidates.py --accept-all --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seller_utils as su  # noqa: E402


def build_entry(cand: dict) -> dict:
    h = cand["handle"].lower()
    return {
        "influencer_name": h,
        "instagram_id": "@" + h,
        "multilink_url": cand.get("landing_url", ""),
        "multilink_type": cand.get("multilink_type", ""),
        "primary_focus": "육아용품",
        "source": "discovered:sellers",
        "is_verified_seller": True,
    }


def select(cands: list[dict], accept: set[str], accept_all: bool, existing: set[str]) -> tuple[list[dict], list[str], list[str]]:
    """(병합할 항목, 이미 있어서 건너뜀, 후보에 없는 핸들)"""
    by_handle = {c["handle"].lower(): c for c in cands}
    chosen = list(by_handle) if accept_all else [h for h in accept if h in by_handle]
    missing = [] if accept_all else [h for h in accept if h not in by_handle]
    skipped = [h for h in chosen if h in existing]
    return [build_entry(by_handle[h]) for h in chosen if h not in existing], skipped, missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="후보 목록 출력")
    ap.add_argument("--accept", default="", help="병합할 핸들(쉼표 구분)")
    ap.add_argument("--accept-all", action="store_true", help="후보 전체 병합")
    ap.add_argument("--apply", action="store_true", help="실제로 targets.json 에 반영(기본은 드라이런)")
    args = ap.parse_args()

    cands = su.load_json(su.CANDIDATES_PATH, [])
    targets = su.load_json(su.TARGETS_PATH, [])
    existing = {h for h in (su.handle_of_target(e) for e in targets) if h}

    if args.list or not (args.accept or args.accept_all):
        print(f"후보 {len(cands)}명 ({su.CANDIDATES_PATH})")
        for c in cands:
            flag = "  (이미 타겟)" if c["handle"].lower() in existing else ""
            print(f"  @{c['handle']:<30} {c.get('multilink_type', ''):<7} {c.get('landing_url', '')}{flag}")
        return 0

    accept = {h.strip().lstrip("@").lower() for h in args.accept.split(",") if h.strip()}
    entries, skipped, missing = select(cands, accept, args.accept_all, existing)
    print(f"병합 대상 {len(entries)}명 / 이미 타겟이라 건너뜀 {len(skipped)}명 / 후보에 없음 {len(missing)}명")
    for e in entries:
        print(f"  + {e['instagram_id']}  {e['multilink_url']}")
    for h in missing:
        print(f"  ? @{h} 는 후보 목록에 없음")
    if not args.apply:
        print("[드라이런] 변경 없음. 반영하려면 --apply 를 붙이세요.")
        return 0
    if not entries:
        print("병합할 항목이 없어 종료합니다.")
        return 0
    bak = su.backup_targets("mergecand")
    su.save_json(su.TARGETS_PATH, targets + entries)
    print(f"백업: {bak.name}\n병합 완료: targets.json {len(targets)} -> {len(targets) + len(entries)}명")
    return 0


if __name__ == "__main__":
    sys.exit(main())
