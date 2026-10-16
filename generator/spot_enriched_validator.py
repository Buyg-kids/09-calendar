"""spot_enriched.json(시드 장소 자동 보강 결과) 검증기.  python -m generator.spot_enriched_validator <spot_enriched.json> <free_spots_seed.json>

장소별 구조: contentid · matched_title · address · geo{lat,lon,source,checked} · photo(null 또는 제1유형 사진) · photo_status ·
nearby{radius_m,eats[],cafes[]}(목록형 식당 규격 = place_guides 의 level:"listed") · curation(육아 꿀팁 적재용 틀).
curation 규칙(블로그/유튜브 AI 요약이 붙을 자리): 항목마다 text(120자 이하 - 원문 문장 복사 금지)·src_urls(https 1개 이상)·
provenance(ai_summary|operator_verified|official)·checked·reviewed(bool). 화면은 reviewed:true 만 쓴다.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import place_guides_validator as pg  # noqa: E402

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PROV = {"ai_summary", "operator_verified", "official"}
LEVELS = {"easy", "moderate", "hard", "unknown"}
AGES = {"baby", "toddler", "kid", "preschool"}
STATUS = {"empty", "ai_draft", "reviewed"}
TIP_MAX = 120
TOPICS = {"parking", "stroller", "nursing", "tip"}
LABEL_RE = re.compile(r"\s*\((네이버 블로그|유튜브 실방문) 후기\)\s*$")          # 후기 요약 끝의 출처 표기
OK_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "blog.naver.com", "m.blog.naver.com"}
AD_RE = re.compile(r"협찬|원고료|체험단|유료\s*광고|제공\s*받|지원\s*받|소정의|식사권|#ad|#광고", re.I)
PII_RE = re.compile(r"01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}|\d{2,3}[-\s]\d{3,4}[-\s]\d{4}|[\w.+-]+@[\w-]+\.[\w.]+")


def _https(u):
    try:
        return urlparse(u).scheme == "https" and bool(urlparse(u).hostname)
    except Exception:
        return False


def _item(p, x, errs):
    if not isinstance(x, dict):
        errs.append(f"{p}: 객체가 아님")
        return
    text = str(x.get("text") or "").strip()
    body = LABEL_RE.sub("", text).strip()
    if not body or len(body) > TIP_MAX:
        errs.append(f"{p}.text: 1~{TIP_MAX}자(원문 복사 금지, 요약만)")
    urls = x.get("src_urls")
    if not (isinstance(urls, list) and urls and all(_https(u) for u in urls)):
        errs.append(f"{p}.src_urls: https URL 1개 이상")
    if x.get("provenance") not in PROV:
        errs.append(f"{p}.provenance: {sorted(PROV)} 중 하나")
    if not DATE_RE.match(str(x.get("checked") or "")):
        errs.append(f"{p}.checked 날짜 형식")
    if not isinstance(x.get("reviewed"), bool):
        errs.append(f"{p}.reviewed(bool) 필요")
    if "quote" in x or "raw" in x:
        errs.append(f"{p}: 원문 인용 필드 금지")
    rv = x.get("review")
    if x.get("reviewed") is True and not isinstance(rv, dict):
        errs.append(f"{p}.review: reviewed:true 항목은 review{{mode,...}} 표기가 필요")
    if isinstance(rv, dict):
        if rv.get("mode") not in {"auto", "manual"}:
            errs.append(f"{p}.review.mode: auto|manual")
        if rv.get("mode") == "auto":
            if not str(rv.get("rules_version") or "").strip() or not DATE_RE.match(str(rv.get("checked") or "")):
                errs.append(f"{p}.review: auto 는 rules_version·checked 필요")
            if not (isinstance(x.get("source_count"), int) and x["source_count"] >= 2):
                errs.append(f"{p}.source_count: 자동 승인은 서로 다른 출처 2건 이상")
            if x.get("sponsor_risk") is not False:
                errs.append(f"{p}.sponsor_risk: 자동 승인은 false 여야 함")
            if not LABEL_RE.search(text):
                errs.append(f"{p}.text: 후기 요약 끝에 (네이버 블로그 후기)/(유튜브 실방문 후기) 출처 표기 필요")
            if x.get("topic") not in TOPICS:
                errs.append(f"{p}.topic: {sorted(TOPICS)}")
            if AD_RE.search(text) or PII_RE.search(text):
                errs.append(f"{p}.text: 광고 표현·개인정보 포함")
            hosts = {(urlparse(u).hostname or "") for u in (x.get("src_urls") or [])}
            if not hosts or not hosts <= OK_HOSTS:
                errs.append(f"{p}.src_urls: 허용 출처(유튜브·네이버 블로그)만")


def validate_curation(sid, c):
    errs = []
    if not isinstance(c, dict) or c.get("status") not in STATUS:
        return [f"{sid}.curation.status: {sorted(STATUS)}"]
    tips = c.get("parent_tips")
    if not isinstance(tips, list):
        errs.append(f"{sid}.curation.parent_tips: 배열")
        tips = []
    for i, t in enumerate(tips):
        _item(f"{sid}.curation.parent_tips[{i}]", t, errs)
    for k in ("parking_tip", "stroller_difficulty", "recommended_ages"):
        x = c.get(k)
        if x is None:
            continue
        _item(f"{sid}.curation.{k}", x, errs)
        if k == "stroller_difficulty" and x.get("level") not in LEVELS:
            errs.append(f"{sid}.curation.{k}.level: {sorted(LEVELS)}")
        if k == "recommended_ages" and not (isinstance(x.get("ages"), list) and x["ages"] and set(x["ages"]) <= AGES):
            errs.append(f"{sid}.curation.{k}.ages: {sorted(AGES)} 의 부분집합")
    singles = [c[k] for k in ("parking_tip", "stroller_difficulty", "recommended_ages") if c.get(k)]
    if c["status"] == "empty" and (tips or singles):
        errs.append(f"{sid}.curation: status empty 인데 내용이 있음")
    if c["status"] == "reviewed":
        items = list(tips) + singles
        if not items or not all(isinstance(i, dict) and i.get("reviewed") is True for i in items):
            errs.append(f"{sid}.curation: status reviewed 는 모든 항목이 reviewed:true 여야 함")
    return errs


def validate(data, seed_ids):
    errs = []
    if not isinstance(data, dict) or not isinstance(data.get("spots"), dict):
        return ["spots 객체가 없음"]
    for sid, v in data["spots"].items():
        if sid not in seed_ids:
            errs.append(f"{sid}: 시드에 없는 id")
        g = v.get("geo")
        if not (isinstance(g, dict) and isinstance(g.get("lat"), (int, float)) and isinstance(g.get("lon"), (int, float)) and 33 <= g["lat"] <= 39 and 124 <= g["lon"] <= 132 and (_https(g.get("source")) or (v.get("source") == "operator_verified" and g.get("source") == "operator_verified")) and DATE_RE.match(str(g.get("checked") or ""))):
            errs.append(f"{sid}.geo: lat(33~39)·lon(124~132)·source·checked 필요")
        manual = v.get("source") == "operator_verified"
        if (not manual and not re.fullmatch(r"\d{3,9}", str(v.get("contentid", "")))) or not str(v.get("matched_title") or "").strip() or not str(v.get("address") or "").strip():
            errs.append(f"{sid}: contentid·matched_title·address 필요")
        ph = v.get("photo")
        if ph is not None:
            ok = isinstance(ph, dict) and all(_https(ph.get(k)) and (urlparse(ph[k]).hostname or "").endswith("visitkorea.or.kr") for k in ("url", "thumb")) and "제1유형" in str(ph.get("license") or "") and str(ph.get("credit") or "").strip() and DATE_RE.match(str(ph.get("checked") or ""))
            if not ok:
                errs.append(f"{sid}.photo: visitkorea.or.kr https·공공누리 제1유형·credit·checked 필요")
        elif v.get("photo_status") not in {"license_not_type1", "no_photo"}:
            errs.append(f"{sid}.photo_status: 사진이 없으면 사유 필요")
        nb = v.get("nearby") or {}
        if not (isinstance(nb, dict) and nb.get("radius_m") == 2000):
            errs.append(f"{sid}.nearby.radius_m")
        seen = set()
        for kind, key in (("식당", "eats"), ("카페", "cafes")):
            for e in nb.get(key, []):
                if e.get("kind") != kind:
                    errs.append(f"{sid}.nearby.{key}: kind 불일치")
                if e.get("id") in seen:
                    errs.append(f"{sid}.nearby: id 중복 {e.get('id')}")
                seen.add(e.get("id"))
                errs += pg.validate_listed_eat(f"{sid}.nearby.{key}.{e.get('id')}", e)
        errs += validate_curation(sid, v.get("curation"))
    return errs


def main(argv):
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    seed = json.loads(Path(argv[2]).read_text(encoding="utf-8"))["spots"]
    errs = validate(data, {s["id"] for s in seed})
    print(f"{len(data.get('spots', {}))}곳 검사, 오류 {len(errs)}건")
    for e in errs[:40]:
        print("  ✗", e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
