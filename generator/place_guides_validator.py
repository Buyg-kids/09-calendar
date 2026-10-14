"""[장소 가이드 검증기] place_guides.json(앵커 장소의 꿀팁·FAQ·육아 인프라 등)이 '출처 없는 사실'을 담지 않도록 검사한다.

원칙: 지어낸 정보 금지. 값(v)이 있는 모든 사실 항목, 모든 팁·포인트·FAQ·연계 장소는 https 출처 URL(src)과 확인일(checked)이 있어야 한다.
확인하지 못한 항목은 v=null 로 두고 화면에서는 '확인 필요'로 보여 준다(없다고 단정하지 않는다).
연령 적합도(age_fit)는 통계가 아니라 에디터 판단이다 - basis='editor' 와 reviewed(검수 여부)를 반드시 적는다.

사용:  python -m generator.place_guides_validator [파일경로]     (오류가 있으면 종료코드 1)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FACT_KEYS = ("address", "tel", "hours", "closed", "fee", "reservation")
INFRA_KEYS = ("stroller", "nursing", "diaper", "parking", "transit")
AGE_KEYS = ("baby", "toddler", "kid", "preschool")
THEMES = {"museum", "forest", "zoo", "water", "indoor", "outdoor"}
REQUIRED = ("id", "name", "region", "theme", "copy", "photos", "facts", "age_fit", "tips", "highlights", "infra", "faq", "nearby")


def _src_ok(src) -> bool:
    return isinstance(src, str) and src.startswith("https://")


def _fact_errors(path: str, f) -> list[str]:
    if not isinstance(f, dict) or "v" not in f:
        return [f"{path}: {{v, src, checked}} 형식이 아님"]
    if f["v"] is None:
        return []
    errs = []
    if not isinstance(f["v"], str) or not f["v"].strip():
        errs.append(f"{path}: v 가 비어 있음")
    if not _src_ok(f.get("src")):
        errs.append(f"{path}: 출처 URL(https) 없음")
    if not (isinstance(f.get("checked"), str) and DATE_RE.match(f["checked"])):
        errs.append(f"{path}: 확인일(YYYY-MM-DD) 없음")
    return errs


def _sourced_list_errors(path: str, items, text_key: str) -> list[str]:
    if not isinstance(items, list):
        return [f"{path}: 목록이 아님"]
    errs = []
    for i, it in enumerate(items):
        p = f"{path}[{i}]"
        if not isinstance(it, dict) or not str(it.get(text_key) or "").strip():
            errs.append(f"{p}: {text_key} 없음")
            continue
        if not _src_ok(it.get("src")):
            errs.append(f"{p}: 출처 URL(https) 없음")
        if not (isinstance(it.get("checked"), str) and DATE_RE.match(it["checked"])):
            errs.append(f"{p}: 확인일 없음")
    return errs


def validate_place(pid: str, p: dict) -> list[str]:
    errs: list[str] = []
    for k in REQUIRED:
        if k not in p:
            errs.append(f"{pid}: 필수 키 없음: {k}")
    if errs:
        return errs
    if p["id"] != pid:
        errs.append(f"{pid}: id 불일치({p['id']})")
    if p["theme"] not in THEMES:
        errs.append(f"{pid}: 알 수 없는 theme {p['theme']}")
    for k in FACT_KEYS:
        errs += _fact_errors(f"{pid}.facts.{k}", p["facts"].get(k, {"v": None}))
    for k in INFRA_KEYS:
        errs += _fact_errors(f"{pid}.infra.{k}", p["infra"].get(k, {"v": None}))
    af = p["age_fit"]
    for k in AGE_KEYS:
        if not (isinstance(af.get(k), int) and 0 <= af[k] <= 3):
            errs.append(f"{pid}.age_fit.{k}: 0~3 정수여야 함")
    if af.get("basis") != "editor":
        errs.append(f"{pid}.age_fit.basis: 'editor' 여야 함(통계 아님을 명시)")
    if not isinstance(af.get("reviewed"), bool):
        errs.append(f"{pid}.age_fit.reviewed: 검수 여부(bool) 필요")
    errs += _sourced_list_errors(f"{pid}.tips", p["tips"], "text")
    errs += _sourced_list_errors(f"{pid}.highlights", p["highlights"], "text")
    errs += _sourced_list_errors(f"{pid}.faq", p["faq"], "a")
    errs += _sourced_list_errors(f"{pid}.nearby", p["nearby"], "name")
    for i, q in enumerate(p["faq"]):
        if not str(q.get("q") or "").strip():
            errs.append(f"{pid}.faq[{i}]: q 없음")
    if not isinstance(p["photos"], list):
        errs.append(f"{pid}.photos: 목록이 아님")
    for i, ph in enumerate(p["photos"] if isinstance(p["photos"], list) else []):
        if not (_src_ok(ph.get("url")) and str(ph.get("credit") or "").strip() and str(ph.get("license") or "").strip()):
            errs.append(f"{pid}.photos[{i}]: url(https)·credit·license 필요(라이선스 확인 전에는 비워 둔다)")
    return errs


def validate(data) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("places"), dict):
        return ["최상위 형식 오류: {'places': {...}} 필요"]
    errs: list[str] = []
    for pid, p in data["places"].items():
        errs += validate_place(pid, p) if isinstance(p, dict) else [f"{pid}: 객체가 아님"]
    return errs


def main(argv=None) -> int:
    path = Path((argv or sys.argv[1:] or ["place_guides.json"])[0])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"읽기 실패: {type(e).__name__}")
        return 1
    errs = validate(data)
    for e in errs:
        print("-", e)
    print(f"{len(data.get('places', {}))}곳 검사, 오류 {len(errs)}건")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
