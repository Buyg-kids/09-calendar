"""spot_photos.json(시드 장소 대표 사진 목록) 검증기.  python -m generator.spot_photos_validator

규칙: 시드 id 존재 / 사진은 visitkorea.or.kr https + 공공누리 제1유형 + 출처 표기 + 확인일 / 도로명주소·contentid·일치 장소명 필수.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _host_ok(u) -> bool:
    try:
        p = urlparse(u)
    except Exception:
        return False
    return p.scheme == "https" and (p.hostname == "visitkorea.or.kr" or (p.hostname or "").endswith(".visitkorea.or.kr"))


def validate(data: dict, seed_ids: set) -> list:
    errs = []
    if not isinstance(data, dict) or not isinstance(data.get("spots"), dict):
        return ["spots 객체가 없음"]
    for sid, v in data["spots"].items():
        if sid not in seed_ids:
            errs.append(f"{sid}: 시드에 없는 id")
        if not isinstance(v, dict):
            errs.append(f"{sid}: 객체가 아님")
            continue
        if not re.fullmatch(r"\d{3,9}", str(v.get("contentid", ""))):
            errs.append(f"{sid}.contentid 형식")
        if not str(v.get("matched_title") or "").strip():
            errs.append(f"{sid}.matched_title 없음")
        if not str(v.get("address") or "").strip():
            errs.append(f"{sid}.address 없음")
        ph = v.get("photo")
        if not isinstance(ph, dict):
            errs.append(f"{sid}.photo 없음")
            continue
        for k in ("url", "thumb"):
            if not _host_ok(ph.get(k)):
                errs.append(f"{sid}.photo.{k}: visitkorea.or.kr https 만 허용")
        if "제1유형" not in str(ph.get("license") or ""):
            errs.append(f"{sid}.photo.license: 공공누리 제1유형만 허용")
        if not str(ph.get("credit") or "").strip():
            errs.append(f"{sid}.photo.credit 없음")
        if not DATE_RE.match(str(ph.get("checked") or "")):
            errs.append(f"{sid}.photo.checked 날짜 형식")
        if str(ph.get("contentid")) != str(v.get("contentid")):
            errs.append(f"{sid}.photo.contentid 불일치")
    return errs


def main() -> int:
    data = json.loads((ROOT / "spot_photos.json").read_text(encoding="utf-8"))
    seed = json.loads((ROOT / "free_spots_seed.json").read_text(encoding="utf-8"))["spots"]
    errs = validate(data, {s["id"] for s in seed})
    print(f"{len(data.get('spots', {}))}곳 검사, 오류 {len(errs)}건")
    for e in errs:
        print("  ✗", e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
