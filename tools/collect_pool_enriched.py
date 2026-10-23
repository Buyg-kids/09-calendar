"""테마 풀 장소 세부정보·주변 식당/카페 수집기.  python tools/collect_pool_enriched.py [--regions 서울,경기,인천] [--phase details|nearby|both] [--max-calls 400] [--limit 0]

theme_spots_pool.json 의 TourAPI 출처 장소마다
 (1) detailIntro2 로 요금·운영시간·휴무·주차·유모차·문의처(한국관광공사 일반 정보)를 가져오고,
 (2) 좌표가 있으면 locationBasedList2 로 반경 2km 음식점·카페(가까운 순 각 5곳, 이름·분류·거리·사진 썸네일만)를 담는다.
산출: pool_enriched.json (공개, schema_version 1). 홈 첫 화면에서는 받지 않고, 풀 장소의 상세 모달·플래너에서 필요할 때만 불러온다(spot_enriched.json 과 분리).
 - 지어내지 않는다: 값은 TourAPI 응답 그대로(태그·개행만 정리). 비어 있거나 '홈페이지 참조' 같은 내용 없는 값은 담지 않는다(화면에서 '확인 필요').
 - 무료 판정(free)은 '입장료' 필드만 본다. 주차요금 '무료'는 입장 무료가 아니다. 금액이 하나라도 있으면 free=false, '유료'가 섞이면 판정하지 않는다.
 - 호출 한도 --max-calls, 요청 사이 0.5초, 쿼터 신호가 오면 즉시 멈추고 지금까지 모은 것을 저장한다. 이어서 실행하면 이미 최근(30일 이내)에 모은 장소는 건너뛴다.
로그 규칙: 예외는 타입명만 남긴다(logger.exception 금지 - 야간 로그에 'Traceback' 이 찍히면 배포 게이트가 막힌다). 인증키는 .env, 출력에 쓰지 않는다."""
import argparse
import html
import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

PROJECT = Path(os.environ.get("BUYG_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT / ".env")
import enrich_spots as es  # noqa: E402  (Budget·QuotaStop·분류 코드 재사용)
from collector import tour_collector as tc  # noqa: E402

log = logging.getLogger("collect_pool_enriched")
SRC = "https://api.visitkorea.or.kr/"
TYPE_BY_CATEGORY = {"관광지": 12, "문화시설": 14, "레포츠": 28}
REFRESH_DAYS = 30
KEEP = 5                                                    # 식당·카페 각각 가까운 순 N곳
CAPITAL = ("서울", "경기", "인천")
# 콘텐츠 유형별 detailIntro2 필드 -> 우리 키 (유형마다 필드 이름이 다르다)
FIELDS = {
    12: {"hours": "usetime", "closed": "restdate", "parking": "parking", "stroller": "chkbabycarriage", "pet": "chkpet", "tel": "infocenter", "experience": "expguide"},
    14: {"fee": "usefee", "hours": "usetimeculture", "closed": "restdateculture", "parking": "parkingculture", "parking_fee": "parkingfee", "stroller": "chkbabycarriageculture",
         "pet": "chkpetculture", "tel": "infocenterculture", "duration": "spendtime", "discount": "discountinfo"},
    28: {"fee": "usefeeleports", "hours": "usetimeleports", "closed": "restdateleports", "parking": "parkingleports", "parking_fee": "parkingfeeleports", "stroller": "chkbabycarriageleports",
         "pet": "chkpetleports", "tel": "infocenterleports", "reservation": "reservation", "period": "openperiod"},
}
CAPS = {"hours": 240, "fee": 240, "closed": 160, "parking": 120, "parking_fee": 120, "tel": 80, "experience": 160, "duration": 40, "discount": 160, "reservation": 160, "period": 80}
FLAG_KEYS = ("stroller", "pet")
NOISE_RE = re.compile(r"^[※\s·\-]*(홈페이지|누리집|사이트|전화|문의|상세)\s*(참조|참고|문의|확인)?\s*(요망|바랍니다)?[.\s]*$|^[\s\-.]*(없음|해당\s*없음|정보\s*없음)[\s.]*$")
PRICE_RE = re.compile(r"\d[\d,]*\s*원|\d+\s*(천|만)\s*원")


def _tidy(v):
    """TourAPI 자유 텍스트 정리(내용 없는 값 거르기 전): 태그·엔티티·개행 제거, 항목 구분(- , ※)을 가운뎃점으로. 비면 ''."""
    if v is None:
        return ""
    t = html.unescape(str(v))
    t = re.sub(r"<br\s*/?>", " ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[\r\n]+", " ", t)
    t = re.sub(r"(?<=\S)-\s+(?=\S)", " · ", t)              # '09:00~18:00- 주말' -> '09:00~18:00 · 주말'
    t = re.sub(r"\s*※\s*", " ※ ", t)
    return re.sub(r"\s+", " ", t).strip().lstrip("-·※ ").strip()


def clean_text(v, cap=160):
    """정리한 텍스트. '홈페이지 참조' 같은 내용 없는 값은 None, 길면 cap 자로 줄인다."""
    t = _tidy(v)
    if len(t) < 2 or NOISE_RE.match(t):
        return None
    return t if len(t) <= cap else t[: cap - 1].rstrip() + "…"


def parse_flag(v):
    """'가능'/'있음' -> True, '불가'/'없음' -> False, 그 밖(빈 값·설명문)은 None. 부정이 우선한다('유모차 불가능' 등).
    '없음' 은 내용 없는 값이 아니라 '없다'는 답이므로 clean_text 의 거르기를 거치지 않는다."""
    t = _tidy(v)[:60]
    if not t:
        return None
    if re.search(r"불가|없|금지|안\s*됨", t):
        return False
    if re.search(r"가능|있|허용|대여", t):
        return True
    return None


def derive_free(fee):
    """입장료 텍스트만으로 무료 여부: 금액이 있으면 False, 금액 없이 '무료'만 있으면 True, '유료'가 섞이거나 알 수 없으면 None."""
    if not fee:
        return None
    if PRICE_RE.search(fee):
        return False
    if "무료" in fee and "유료" not in fee:
        return True
    return None


def details_from(item, ctype):
    """detailIntro2 한 건 -> 담을 값만 모은 dict(비어 있으면 {}). free 는 입장료가 있을 때만 판정한다."""
    out = {}
    for ours, theirs in FIELDS[ctype].items():
        raw = (item or {}).get(theirs)
        if ours in FLAG_KEYS:
            f = parse_flag(raw)
            if f is not None:
                out[ours] = f
        else:
            t = clean_text(raw, CAPS.get(ours, 160))
            if t:
                out[ours] = t
    fr = derive_free(out.get("fee"))
    if fr is not None:
        out["free"] = fr
    return out


def nearby_light(budget, key, lat, lon, today):
    """반경 2km 음식점·카페(각 가까운 순 KEEP곳). 이름·분류·거리·Type1 사진 썸네일만 담는다(메뉴 조회 없음 -> 호출 1회)."""
    items = budget.call(key, "locationBasedList2", {"mapX": lon, "mapY": lat, "radius": es.RADIUS_M, "contentTypeId": 39, "arrange": "E", "numOfRows": 100, "pageNo": 1})
    if items is None:
        return None
    eats, cafes = [], []
    for it in items:
        try:
            m = int(float(it.get("dist", "")))
        except ValueError:
            continue
        cid = str(it.get("contentid") or "")
        name = (it.get("title") or "").strip()
        if not re.fullmatch(r"\d{3,9}", cid) or not name or m < 0 or m > es.RADIUS_M:
            continue
        is_cafe = (it.get("cat3") or "") == es.CAFE_CAT3
        e = {"id": f"eat_t{cid}", "kind": "카페" if is_cafe else "식당", "name": name, "category": es.CAT3_LABEL.get(it.get("cat3") or "", "음식점"),
             "level": "listed", "distance": {"meters": m, "mode": "straight"}}
        th = tc._safe_https(it.get("firstimage2")) or tc._safe_https(it.get("firstimage"))
        if th and it.get("cpyrhtDivCd") == "Type1":
            e["photo"] = {"thumb": th}
        (cafes if is_cafe else eats).append(e)
    key_m = lambda e: e["distance"]["meters"]   # noqa: E731
    return {"radius_m": es.RADIUS_M, "checked": today, "eats": sorted(eats, key=key_m)[:KEEP], "cafes": sorted(cafes, key=key_m)[:KEEP]}


def targets(pool, regions):
    """풀(고유 id) 중 TourAPI 출처·좌표·지원 유형(관광지/문화시설/레포츠)인 장소. 지역 순서(서울·경기·인천 …) 후 이름순."""
    seen, out = {}, []
    for th in (pool.get("themes") or {}).values():
        for it in th.get("items") or []:
            pid = it.get("id")
            if not pid or pid in seen or it.get("region") not in regions or it.get("provenance") != "tourapi":
                continue
            ct = TYPE_BY_CATEGORY.get(it.get("category"))
            g = it.get("geo") or {}
            if not ct or not str(it.get("contentid") or "").isdigit() or not isinstance(g.get("lat"), (int, float)) or not isinstance(g.get("lon"), (int, float)):
                continue
            seen[pid] = 1
            out.append({"id": pid, "cid": str(it["contentid"]), "ctype": ct, "title": it.get("title", ""), "region": it["region"], "lat": g["lat"], "lon": g["lon"]})
    order = {r: i for i, r in enumerate(regions)}
    return sorted(out, key=lambda t: (order.get(t["region"], 99), t["title"]))


def due(sec, today, refresh=REFRESH_DAYS):
    """이 구역(details/nearby)을 지금 수집해야 하는가: 없으면 True, 최근 refresh 일 안에 모았으면 False."""
    if not sec or not sec.get("checked"):
        return True
    try:
        return (today - date.fromisoformat(sec["checked"])).days >= refresh
    except ValueError:
        return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=str(PROJECT / "theme_spots_pool.json"))
    ap.add_argument("--out", default=str(PROJECT / "pool_enriched.json"))
    ap.add_argument("--regions", default=",".join(CAPITAL))
    ap.add_argument("--phase", choices=["details", "nearby", "both"], default="both")
    ap.add_argument("--max-calls", type=int, default=400)
    ap.add_argument("--limit", type=int, default=0, help="이번 실행에서 처리할 장소 수 상한(0=제한 없음, 호출 한도까지)")
    ap.add_argument("--only", default="", help="쉼표로 구분한 풀 id 만 처리(시험용)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    key = os.getenv("TOURAPI_SERVICE_KEY", "").strip()
    if not key:
        log.error("TOURAPI_SERVICE_KEY 없음 - 수집 건너뜀")
        return 2
    today = date.today()
    pool = json.loads(Path(a.pool).read_text(encoding="utf-8"))
    regions = [r.strip() for r in a.regions.split(",") if r.strip()]
    outp = Path(a.out)
    data = json.loads(outp.read_text(encoding="utf-8")) if outp.exists() else {"schema_version": 1, "source": SRC, "spots": {}}
    spots = data.setdefault("spots", {})
    want = [t for t in targets(pool, regions) if not a.only or t["id"] in a.only.split(",")]
    queue = [t for t in want if (a.phase in ("details", "both") and due((spots.get(t["id"]) or {}).get("details"), today))
             or (a.phase in ("nearby", "both") and due((spots.get(t["id"]) or {}).get("nearby"), today))]
    if a.limit:
        queue = queue[: a.limit]
    log.info("대상 %d곳 중 처리 대기 %d곳 (단계 %s, 호출 한도 %d)", len(want), len(queue), a.phase, a.max_calls)
    budget = es.Budget(a.max_calls)
    done = errs = 0

    def save():
        data["generated_at"] = today.isoformat()
        outp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    try:
        for t in queue:
            rec = spots.setdefault(t["id"], {})
            try:
                if a.phase in ("details", "both") and due(rec.get("details"), today):
                    items = budget.call(key, "detailIntro2", {"contentId": t["cid"], "contentTypeId": t["ctype"]})
                    if items is not None:
                        rec["details"] = dict(details_from(items[0] if items else {}, t["ctype"]), checked=today.isoformat())
                if a.phase in ("nearby", "both") and due(rec.get("nearby"), today):
                    nb = nearby_light(budget, key, t["lat"], t["lon"], today.isoformat())
                    if nb is not None:
                        rec["nearby"] = nb
                done += 1
            except es.QuotaStop:
                raise
            except Exception as e:     # noqa: BLE001 - 한 장소의 실패가 전체를 막지 않는다(타입명만 기록)
                errs += 1
                log.warning("[%s] 수집 실패(%s) - 건너뜀", t["id"], type(e).__name__)
            if not rec:
                spots.pop(t["id"], None)
            if done and done % 20 == 0:
                save()                                  # 긴 수집이 중간에 끊겨도 지금까지 모은 것은 남긴다
    except es.QuotaStop as e:
        log.warning("호출 한도/쿼터로 중단: %s", e)
    save()
    n_det = sum(1 for r in spots.values() if (r.get("details") or {}).get("checked"))
    n_near = sum(1 for r in spots.values() if (r.get("nearby") or {}).get("checked"))
    log.info("완료: 처리 %d곳(실패 %d) · 호출 %d회 · 저장된 장소 %d곳(세부정보 %d, 주변 %d) -> %s", done, errs, budget.used, len(spots), n_det, n_near, outp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
