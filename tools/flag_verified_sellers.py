"""targets.json 의 is_verified_seller 플래그를 일괄 표시하는 수동 실행 도구 (인스타그램에는 접속하지 않음).

규칙: multilink_url 이 인포크(link.inpock.co.kr / inpk.link) · 리틀리(litt.ly) · 릿링크(lit.link) 계열이면 true.
multilink_url 이 비어 있는 계정은 '프로필 외부 링크 점검 필요' 목록으로 따로 출력하고, 이미 수집된
data/raw_collected.json 의 프로필 소개/링크 텍스트에서 해당 문자열이 보이면 근거 URL 을 함께 보여준다
(자동으로 true 로 바꾸지는 않는다 - 사람이 확인 후 처리).
--use-evidence 를 주면 위 근거 링크가 확인된 계정도 true 로 표시한다(multilink_url 은 채우지 않는다 - 채우면 수집기가
그 페이지를 추가로 스크래핑하게 되어 야간 동작이 바뀐다). 기본은 드라이런, --apply 일 때만 백업(targets.json.bak_..._pre_verifiedflag) 후 반영한다.
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


def classify(targets: list[dict], raw: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """(새 targets, 링크 비어 있는 항목 목록). 입력은 바꾸지 않는다.
    raw 를 주면(--use-evidence) 링크가 빈 계정도 소개글에서 인포크/리틀리 링크가 확인되면 true."""
    index = _raw_index(raw) if raw else {}
    out, empty = [], []
    for e in targets:
        n = dict(e)
        url = (n.get("multilink_url") or "").strip()
        if su.multilink_type_of(url):
            n["is_verified_seller"] = True
        else:
            n["is_verified_seller"] = bool(n.get("is_verified_seller", False))
            if not url and index and _evidence(index, su.handle_of_target(n)):
                n["is_verified_seller"] = True
        if not url:
            empty.append(n)
        out.append(n)
    return out, empty


def _raw_index(raw: list[dict]) -> dict[str, dict]:
    return {((r.get("instagram") or {}).get("handle") or "").lower(): r for r in raw}


def _evidence(index: dict[str, dict], handle: str) -> str:
    r = index.get(handle)
    if not r:
        return ""
    ig = r.get("instagram") or {}
    blob = " ".join(str(x) for x in (ig.get("bio_text"), ig.get("links"), r.get("multilink_url"), r.get("multilink")) if x)
    m = EVIDENCE_RE.search(blob)
    return m.group(0) if m else ""


def evidence_for(handle: str, raw: list[dict]) -> str:
    """raw_collected.json 에서 해당 핸들의 소개/멀티링크 텍스트 속 인포크·리틀리 URL (없으면 '')."""
    return _evidence(_raw_index(raw), handle)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--use-evidence", action="store_true", help="multilink_url 이 빈 계정도 소개글 근거 링크가 있으면 true")
    ap.add_argument("--apply", action="store_true", help="실제로 targets.json 에 반영(기본은 드라이런)")
    args = ap.parse_args()

    targets = su.load_json(su.TARGETS_PATH, [])
    if not targets:
        print("targets.json 이 비어 있거나 없습니다.")
        return 1
    before = sum(1 for e in targets if e.get("is_verified_seller") is True)
    raw = su.load_json(su.RAW_COLLECTED_PATH, [])
    new_targets, empty = classify(targets, raw if args.use_evidence else None)
    after = sum(1 for e in new_targets if e["is_verified_seller"])
    print(f"전체 {len(targets)}명 / is_verified_seller=true: {before} -> {after} (+{after - before})")

    print(f"\n[프로필 외부 링크 점검 필요] multilink_url 이 빈 계정 {len(empty)}명")
    shown = 0
    with_evidence = 0
    for e in empty:
        h = su.handle_of_target(e)
        ev = evidence_for(h, raw) if h else ""
        with_evidence += bool(ev)
        if shown < 15 or h in ("kelley_mom_dad", "__simplyhome"):
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
