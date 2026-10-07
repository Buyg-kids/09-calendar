"""targets.json 의 is_verified_seller 플래그를 일괄 표시하는 수동 실행 도구 (인스타그램에는 접속하지 않음).

규칙: multilink_url 이 인포크(link.inpock.co.kr / inpk.link) · 리틀리(litt.ly) · 릿링크(lit.link) 계열이면 true.
multilink_url 이 비어 있는 계정은 '프로필 외부 링크 점검 필요' 목록으로 따로 출력하고, 이미 수집된
data/raw_collected.json 의 프로필 소개/링크 텍스트에서 해당 문자열이 보이면 근거 URL 을 함께 보여준다
(자동으로 true 로 바꾸지는 않는다 - 사람이 확인 후 처리).
기본은 드라이런, --apply 일 때만 백업(targets.json.bak_..._pre_verifiedflag) 후 반영한다.
기존 키는 모두 보존하고, 플래그가 없는 항목에는 false 를 채운다.

  python tools/flag_verified_sellers.py
  python tools/flag_verified_sellers.py --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seller_utils as su  # noqa: E402

EVIDENCE_RE = re.compile(
    r"(?:https?://)?(?:link\.inpock\.co\.kr|inpk\.link|litt\.ly|lit\.link)/[A-Za-z0-9_.\-]+", re.IGNORECASE
)


def classify(targets: list[dict]) -> tuple[list[dict], list[dict]]:
    """(새 targets, 링크 비어 있는 항목 목록). 입력은 바꾸지 않는다."""
    out, empty = [], []
    for e in targets:
        n = dict(e)
        url = (n.get("multilink_url") or "").strip()
        if su.multilink_type_of(url):
            n["is_verified_seller"] = True
        else:
            n["is_verified_seller"] = bool(n.get("is_verified_seller", False))
        if not url:
            empty.append(n)
        out.append(n)
    return out, empty


def evidence_for(handle: str, raw: list[dict]) -> str:
    """raw_collected.json 에서 해당 핸들의 소개/멀티링크 텍스트 속 인포크·리틀리 URL (없으면 '')."""
    for r in raw:
        ig = r.get("instagram") or {}
        if (ig.get("handle") or "").lower() != handle:
            continue
        blob = " ".join(str(x) for x in (ig.get("bio_text"), ig.get("links"), r.get("multilink_url"), r.get("multilink")) if x)
        m = EVIDENCE_RE.search(blob)
        return m.group(0) if m else ""
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="실제로 targets.json 에 반영(기본은 드라이런)")
    args = ap.parse_args()

    targets = su.load_json(su.TARGETS_PATH, [])
    if not targets:
        print("targets.json 이 비어 있거나 없습니다.")
        return 1
    before = sum(1 for e in targets if e.get("is_verified_seller") is True)
    new_targets, empty = classify(targets)
    after = sum(1 for e in new_targets if e["is_verified_seller"])
    print(f"전체 {len(targets)}명 / is_verified_seller=true: {before} -> {after} (+{after - before})")

    raw = su.load_json(su.RAW_COLLECTED_PATH, [])
    print(f"\n[프로필 외부 링크 점검 필요] multilink_url 이 빈 계정 {len(empty)}명")
    shown = 0
    with_evidence = 0
    for e in empty:
        h = su.handle_of_target(e)
        ev = evidence_for(h, raw) if h else ""
        with_evidence += bool(ev)
        if ev or shown < 15 or h in ("kelley_mom_dad", "__simplyhome"):
            print(f"  @{h or '?':<28} 근거 링크: {ev or '-'}")
            shown += 1
    print(f"  ... (수집 데이터에서 인포크/리틀리 링크 근거가 보이는 계정 {with_evidence}명)")

    if not args.apply:
        print("\n[드라이런] 변경 없음. 반영하려면 --apply 를 붙이세요.")
        return 0
    bak = su.backup_targets("verifiedflag")
    su.save_json(su.TARGETS_PATH, new_targets)
    print(f"\n백업: {bak.name}\n반영 완료: is_verified_seller=true {after}명")
    return 0


if __name__ == "__main__":
    sys.exit(main())
