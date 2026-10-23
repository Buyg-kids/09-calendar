"""[풀 보강 데이터 검증기] pool_enriched.json(테마 풀 장소의 세부정보·주변 식당/카페)이 출처 없는 값이나 이상한 형식을 담지 않도록 검사한다.

원칙: 값은 한국관광공사 TourAPI 응답을 정리한 것뿐이다(지어내지 않음). 최상위 source 는 https, 장소 id 는 theme_spots_pool.json 의 id 와 같은 형식(pool_<숫자>),
세부정보·주변 구역마다 확인일(checked)이 있어야 한다. 식당·카페는 가까운 순 각 5곳 이하, 거리 0~2000m, 사진은 https 썸네일만.

사용:  python -m generator.pool_enriched_validator [pool_enriched.json] [theme_spots_pool.json]     (오류가 있으면 종료코드 1)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^pool_\d+$")
EAT_ID_RE = re.compile(r"^eat_t\d{3,9}$")
TEXT_KEYS = {"fee": 240, "hours": 240, "closed": 160, "parking": 120, "parking_fee": 120, "tel": 80, "experience": 160, "duration": 40, "discount": 160, "reservation": 160, "period": 80}
BOOL_KEYS = ("stroller", "pet", "free")
DETAIL_KEYS = set(TEXT_KEYS) | set(BOOL_KEYS) | {"checked"}
PHOTO_RE = re.compile(r"^https://([a-z0-9-]+\.)*visitkorea\.or\.kr/")
MAX_PER_KIND = 5
RADIUS = 2000


def _details_errors(p: str, d) -> list[str]:
    if not isinstance(d, dict):
        return [f"{p}.details: 객체가 아님"]
    errs = []
    if not (isinstance(d.get("checked"), str) and DATE_RE.match(d["checked"])):
        errs.append(f"{p}.details: 확인일(checked) 없음")
    for k, v in d.items():
        if k not in DETAIL_KEYS:
            errs.append(f"{p}.details.{k}: 알 수 없는 키")
        elif k in TEXT_KEYS:
            if not isinstance(v, str) or not v.strip():
                errs.append(f"{p}.details.{k}: 문자열이 비어 있음")
            elif len(v) > TEXT_KEYS[k]:
                errs.append(f"{p}.details.{k}: {TEXT_KEYS[k]}자 초과")
            elif "<" in v or ">" in v or "\n" in v:
                errs.append(f"{p}.details.{k}: 태그·개행이 남아 있음")
        elif k in BOOL_KEYS and not isinstance(v, bool):
            errs.append(f"{p}.details.{k}: true/false 여야 함")
    if "free" in d and "fee" not in d:
        errs.append(f"{p}.details.free: 입장료(fee) 없이 무료 여부를 판정할 수 없음")
    return errs


def _eat_errors(p: str, e, kind: str) -> list[str]:
    if not isinstance(e, dict):
        return [f"{p}: 객체가 아님"]
    errs = []
    if not EAT_ID_RE.match(str(e.get("id") or "")):
        errs.append(f"{p}.id: eat_t<contentid> 형식이 아님")
    if e.get("kind") != kind:
        errs.append(f"{p}.kind: {kind} 여야 함")
    if not str(e.get("name") or "").strip():
        errs.append(f"{p}.name: 비어 있음")
    if e.get("level") != "listed":
        errs.append(f"{p}.level: 'listed' 여야 함(영업 여부를 확인하지 않은 목록)")
    d = e.get("distance")
    if not (isinstance(d, dict) and isinstance(d.get("meters"), int) and 0 <= d["meters"] <= RADIUS and d.get("mode") == "straight"):
        errs.append(f"{p}.distance: meters(0~{RADIUS}) + mode 'straight' 필요")
    ph = e.get("photo")
    if ph is not None and not (isinstance(ph, dict) and PHOTO_RE.match(str(ph.get("thumb") or ""))):
        errs.append(f"{p}.photo: 한국관광공사(visitkorea.or.kr) https 썸네일(thumb)이어야 함")
    return errs


def _nearby_errors(p: str, n) -> list[str]:
    if not isinstance(n, dict):
        return [f"{p}.nearby: 객체가 아님"]
    errs = []
    if not (isinstance(n.get("checked"), str) and DATE_RE.match(n["checked"])):
        errs.append(f"{p}.nearby: 확인일(checked) 없음")
    if n.get("radius_m") != RADIUS:
        errs.append(f"{p}.nearby.radius_m: {RADIUS} 여야 함")
    for key, kind in (("eats", "식당"), ("cafes", "카페")):
        lst = n.get(key)
        if not isinstance(lst, list):
            errs.append(f"{p}.nearby.{key}: 목록이 아님")
            continue
        if len(lst) > MAX_PER_KIND:
            errs.append(f"{p}.nearby.{key}: {MAX_PER_KIND}곳 초과")
        ms = []
        for i, e in enumerate(lst):
            errs += _eat_errors(f"{p}.nearby.{key}[{i}]", e, kind)
            if isinstance(e, dict) and isinstance(e.get("distance"), dict) and isinstance(e["distance"].get("meters"), int):
                ms.append(e["distance"]["meters"])
        if ms != sorted(ms):
            errs.append(f"{p}.nearby.{key}: 가까운 순이 아님")
    return errs


def validate(data, pool=None) -> list[str]:
    if not isinstance(data, dict):
        return ["최상위가 객체가 아님"]
    errs = []
    if data.get("schema_version") != 1:
        errs.append("schema_version 은 1 이어야 함")
    if not str(data.get("source") or "").startswith("https://"):
        errs.append("source(https 출처) 없음")
    spots = data.get("spots")
    if not isinstance(spots, dict):
        return errs + ["spots 가 객체가 아님"]
    pool_ids = None
    if pool is not None:
        pool_ids = {it.get("id") for th in (pool.get("themes") or {}).values() for it in th.get("items") or []}
    for pid, rec in spots.items():
        if not ID_RE.match(pid):
            errs.append(f"{pid}: 풀 id 형식(pool_<숫자>)이 아님")
        elif pool_ids is not None and pid not in pool_ids:
            errs.append(f"{pid}: theme_spots_pool.json 에 없는 id")
        if not isinstance(rec, dict) or not ({"details", "nearby"} & set(rec)):
            errs.append(f"{pid}: details/nearby 중 하나는 있어야 함")
            continue
        extra = set(rec) - {"details", "nearby"}
        if extra:
            errs.append(f"{pid}: 알 수 없는 키 {sorted(extra)}")
        if "details" in rec:
            errs += _details_errors(pid, rec["details"])
        if "nearby" in rec:
            errs += _nearby_errors(pid, rec["nearby"])
    return errs


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parent.parent
    path = Path(argv[0]) if argv else root / "pool_enriched.json"
    pool_path = Path(argv[1]) if len(argv) > 1 else root / "theme_spots_pool.json"
    if not path.exists():
        print(f"{path.name} 없음 - 검사 건너뜀")
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    pool = json.loads(pool_path.read_text(encoding="utf-8")) if pool_path.exists() else None
    errs = validate(data, pool)
    for e in errs:
        print("-", e)
    print(f"{len(data.get('spots') or {})}곳 검사, 오류 {len(errs)}건")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
