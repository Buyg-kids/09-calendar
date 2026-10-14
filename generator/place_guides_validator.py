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


PROV = {"operator_verified"}
AGE_PRICE_KEYS = ("adult", "teen", "child", "toddler")
EAT_KINDS = {"식당", "카페"}
TIER_NUM = ("free_minutes", "base_minutes", "base", "step_minutes", "step", "daily_max")


def _prov_errors(path: str, o) -> list[str]:
    """출처 URL(https)+확인일(checked) 이거나, 운영자 확인(provenance=operator_verified)+확인일(verified_at) 중 하나가 있어야 한다."""
    if not isinstance(o, dict):
        return [f"{path}: 객체가 아님"]
    if _src_ok(o.get("src")) and isinstance(o.get("checked"), str) and DATE_RE.match(o["checked"]):
        return []
    if o.get("provenance") in PROV and isinstance(o.get("verified_at"), str) and DATE_RE.match(o["verified_at"]):
        return []
    return [f"{path}: 출처 URL+확인일 또는 운영자 확인(provenance·verified_at) 필요"]


def _prov_value_errors(path: str, o) -> list[str]:
    if not isinstance(o, dict) or not str(o.get("v") or "").strip():
        return [f"{path}: v 없음"]
    return _prov_errors(path, o)


def validate_costs(pid: str, c) -> list[str]:
    """요금 구조(합산용). 숫자는 공식 확인값이어야 하며(src+checked), 모르면 model=unknown + note."""
    errs: list[str] = []
    if not isinstance(c, dict):
        return [f"{pid}.costs: 객체가 아님"]
    a, pk = c.get("admission"), c.get("parking")
    if not isinstance(a, dict) or a.get("model") not in {"free", "per_person", "unknown"}:
        errs.append(f"{pid}.costs.admission.model: free/per_person/unknown 중 하나")
    else:
        if a["model"] == "unknown":
            if not str(a.get("note") or "").strip():
                errs.append(f"{pid}.costs.admission: unknown 이면 note 필요")
        else:
            errs += _prov_errors(f"{pid}.costs.admission", a)
        if a["model"] == "per_person":
            prices = a.get("prices") or {}
            for k in AGE_PRICE_KEYS:
                if not (isinstance(prices.get(k), int) and prices[k] >= 0):
                    errs.append(f"{pid}.costs.admission.prices.{k}: 0 이상 정수 필요")
            for ok, opt in (a.get("options") or {}).items():
                for k in AGE_PRICE_KEYS:
                    if not (isinstance((opt.get("prices") or {}).get(k), int) and opt["prices"][k] >= 0):
                        errs.append(f"{pid}.costs.admission.options.{ok}.prices.{k}: 0 이상 정수 필요")
    if not isinstance(pk, dict) or pk.get("model") not in {"tiered", "unknown"}:
        errs.append(f"{pid}.costs.parking.model: tiered/unknown 중 하나")
    elif pk["model"] == "unknown":
        if not str(pk.get("note") or "").strip():
            errs.append(f"{pid}.costs.parking: unknown 이면 note 필요")
    else:
        errs += _prov_errors(f"{pid}.costs.parking", pk)
        for k in TIER_NUM:
            if not (isinstance(pk.get(k), int) and pk[k] >= 0):
                errs.append(f"{pid}.costs.parking.{k}: 0 이상 정수 필요")
        if isinstance(pk.get("daily_max"), int) and isinstance(pk.get("base"), int) and pk["daily_max"] < pk["base"]:
            errs.append(f"{pid}.costs.parking: daily_max 가 base 보다 작음")
        if not pk.get("step_minutes"):
            errs.append(f"{pid}.costs.parking.step_minutes: 0 보다 커야 함")
    return errs


def validate_ref_menu(p: str, rm) -> list[str]:
    """대표메뉴 1종 기준가(플래너 식비·카페비 자동 계산용). 운영자 확인 또는 출처 URL 필수, 가격은 1~200,000원 정수."""
    if not isinstance(rm, dict):
        return [f"{p}.ref_menu: 객체가 아님"]
    errs = []
    if not str(rm.get("name") or "").strip():
        errs.append(f"{p}.ref_menu.name 없음")
    if not (isinstance(rm.get("price"), int) and not isinstance(rm.get("price"), bool) and 1 <= rm["price"] <= 200000):
        errs.append(f"{p}.ref_menu.price: 1~200,000 정수")
    errs += _prov_errors(f"{p}.ref_menu", rm)
    return errs



LISTED_MODES = {"in_park", "straight"}


def validate_listed_eat(p: str, e: dict) -> list[str]:
    """공식 출처로 이름·주소(·판매품목)만 확인한 '목록형' 식당/카페. 확인하지 못한 아기의자·주차·장단점 등은 값이 없어야 한다(화면에서 '매장 확인 필요')."""
    errs: list[str] = []
    ident = e.get("identity")
    if not (isinstance(ident, dict) and _src_ok(ident.get("src")) and isinstance(ident.get("checked"), str) and DATE_RE.match(ident["checked"]) and str(ident.get("note") or "").strip()):
        errs.append(f"{p}.identity: 출처 URL(https)·확인일·설명 필요")
    d = e.get("distance")
    if not (isinstance(d, dict) and str(d.get("v") or "").strip() and d.get("mode") in LISTED_MODES and isinstance(d.get("meters"), int) and d["meters"] >= 0):
        errs.append(f"{p}.distance: v·mode(in_park/straight)·meters(0 이상 정수) 필요")
    elif d["mode"] == "in_park" and d["meters"] != 0:
        errs.append(f"{p}.distance: in_park 는 meters 0")
    m = e.get("menu")
    if m is not None and not (isinstance(m, dict) and str(m.get("v") or "").strip() and _src_ok(m.get("src")) and isinstance(m.get("checked"), str) and DATE_RE.match(m["checked"])):
        errs.append(f"{p}.menu: 값·출처 URL·확인일 필요")
    for k in ("recommend_for", "infra", "pros", "cons", "peak"):
        if e.get(k):
            errs.append(f"{p}.{k}: 목록형은 확인하지 못한 정보를 담을 수 없음(운영자 확인 후 level 을 올리고 입력)")
    pc = e.get("place_check")
    if not (isinstance(pc, dict) and pc.get("status") in {"unchecked", "confirmed", "mismatch", "closed"}):
        errs.append(f"{p}.place_check.status")
    return errs



def validate_eats(pid: str, eats) -> list[str]:
    """식당·카페 후기 카드. 사실 항목은 운영자 확인 또는 출처 URL, 호평/아쉬움은 각각 출처 표기가 있는 짧은 문장."""
    if not isinstance(eats, list):
        return [f"{pid}.eats: 목록이 아님"]
    errs: list[str] = []
    seen = set()
    for i, e in enumerate(eats):
        p = f"{pid}.eats[{i}]"
        if not isinstance(e, dict):
            errs.append(f"{p}: 객체가 아님"); continue
        if not str(e.get("id") or "").strip() or e["id"] in seen:
            errs.append(f"{p}.id: 비어 있거나 중복")
        seen.add(e.get("id"))
        if e.get("kind") not in EAT_KINDS:
            errs.append(f"{p}.kind: 식당/카페 중 하나")
        if not str(e.get("name") or "").strip():
            errs.append(f"{p}.name 없음")
        if not re.match(r"^(서울특별시|서울|경기도|경기|인천광역시|인천)\s", str(e.get("address") or "")):
            errs.append(f"{p}.address: 시·도로 시작하는 주소 필요")
        if e.get("ref_menu") is not None:
            errs += validate_ref_menu(p, e["ref_menu"])
        if e.get("level") == "listed":
            errs += validate_listed_eat(p, e)
            continue
        d = e.get("distance")
        errs += _prov_value_errors(f"{p}.distance", d)
        if isinstance(d, dict):
            if d.get("mode") not in {"car", "walk"}:
                errs.append(f"{p}.distance.mode: car/walk")
            mn, mx = d.get("min_minutes"), d.get("max_minutes")
            if not (isinstance(mn, int) and isinstance(mx, int) and 0 <= mn <= mx <= 60):
                errs.append(f"{p}.distance: min/max_minutes 는 0~60 정수, min<=max")
        errs += _prov_value_errors(f"{p}.recommend_for", e.get("recommend_for"))
        for k, f in (e.get("infra") or {}).items():
            errs += _prov_value_errors(f"{p}.infra.{k}", f)
        if e.get("menu") is not None:
            errs += _prov_value_errors(f"{p}.menu", e["menu"])
        for key in ("pros", "cons"):
            lst = e.get(key)
            if not isinstance(lst, list):
                errs.append(f"{p}.{key}: 목록이 아님"); continue
            for j, it in enumerate(lst):
                if not str(it.get("text") or "").strip() if isinstance(it, dict) else True:
                    errs.append(f"{p}.{key}[{j}]: text 없음")
                else:
                    errs += _prov_errors(f"{p}.{key}[{j}]", it)
        if not (e.get("pros") or e.get("cons")):
            errs.append(f"{p}: 호평 또는 아쉬운 점이 하나는 있어야 함")
        pk = e.get("peak")
        if pk is not None and not (isinstance(pk, dict) and re.match(r"^\d{2}:\d{2}$", str(pk.get("start"))) and re.match(r"^\d{2}:\d{2}$", str(pk.get("end"))) and str(pk.get("label") or "").strip()):
            errs.append(f"{p}.peak: start/end(HH:MM)·label 필요")
        errs += _prov_errors(p, e)
        pc = e.get("place_check")
        if not (isinstance(pc, dict) and pc.get("status") in {"unchecked", "confirmed", "mismatch", "closed"}):
            errs.append(f"{p}.place_check.status: unchecked/confirmed/mismatch/closed")
        elif pc["status"] == "confirmed" and not (str(pc.get("confirmed_by") or "").strip() and isinstance(pc.get("confirmed_at"), str) and DATE_RE.match(pc["confirmed_at"]) and str(pc.get("note") or "").strip()):
            errs.append(f"{p}.place_check: confirmed 이면 confirmed_by·confirmed_at·note 필요")
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
    if "geo" in p:
        g = p["geo"]
        if not (isinstance(g, dict) and isinstance(g.get("lat"), (int, float)) and isinstance(g.get("lon"), (int, float)) and 33 <= g["lat"] <= 39 and 124 <= g["lon"] <= 132
                and str(g.get("source") or "").strip() and isinstance(g.get("checked"), str) and DATE_RE.match(g["checked"])):
            errs.append(f"{pid}.geo: lat(33~39)·lon(124~132)·source·checked 필요")
    if "costs" in p:
        errs += validate_costs(pid, p["costs"])
    if "eats" in p:
        errs += validate_eats(pid, p["eats"])
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
