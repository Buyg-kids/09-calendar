"""나들이 시드 장소 일일 자동 보강 배치.  python tools/enrich_spots.py [--spots 100] [--max-calls 400]   (기본: 프로젝트 루트의 free_spots_seed.json -> spot_enriched.json)

장소마다 (1) searchKeyword2 로 contentid·좌표·주소·대표사진(공공누리 제1유형만) 매핑, (2) 좌표가 있으면 locationBasedList2 로 반경 2km 음식점·카페를 매핑한다.
큐 방식: 우선순위(좌표 없음 > 근처 식당 없음 > 오래된 순) 로 하루 --spots 곳만 처리하고, 요청 사이 0.5초 쉰다. 쿼터 초과 신호가 오면 즉시 멈춘다.
상태: data/spot_queue_state.json (gitignore, 다음 실행이 이어받음).  산출: <out>/spot_enriched.json (장소별 한 덩어리, 공개).
로그 규칙: 예외는 타입명만 남긴다(logger.exception 금지 - 야간 로그에 'Traceback' 이 찍히면 배포 게이트가 막힌다). 인증키는 .env, 출력에 쓰지 않는다."""
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT = Path(os.environ.get("BUYG_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(PROJECT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT / ".env")
from collector import tour_collector as tc  # noqa: E402

log = logging.getLogger("enrich_spots")
DELAY = 0.5
RADIUS_M = 2000
CAFE_CAT3 = "A05020900"
CAT3_LABEL = {"A05020100": "한식", "A05020200": "양식", "A05020300": "일식", "A05020400": "중식", "A05020700": "이색 음식점", "A05020900": "카페"}   # TourAPI 음식점 중분류 코드
KID_MENU_RE = ["돈가스", "돈까스", "우동", "국수", "칼국수", "순두부", "볶음밥", "짜장", "만두", "카레", "오므라이스", "파스타", "스테이크", "떡볶이", "불고기", "비빔밥", "수제비"]   # 메뉴 이름 기준(아이가 먹기 쉬운 편)
NEARBY_KEEP = 5                    # 식당·카페 각각 가까운 순 N곳
RETRY_NO_MATCH_DAYS = 7            # 못 찾은 곳은 키워드 변형을 바꿔 가며 일주일 뒤 재시도
REFRESH_DAYS = 30                  # 매핑된 곳은 한 달마다 사진·좌표·주변 식당을 다시 확인
SRC = "https://api.visitkorea.or.kr/"
# 포함 일치(점수 1) 중 운영자가 같은 장소로 승인한 시드 이름. 그 밖의 포함 일치는 status "review" 로 큐에 남기고 데이터에 싣지 않는다.
ACCEPT_CONTAINS = {"강릉 오죽헌", "제천 의림지", "전주 한옥마을", "함덕해수욕장"}
QUOTA_CODES = {"22", "20", "30", "31"}   # LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR 등(인증·쿼터 문제는 더 시도하지 않는다)


class QuotaStop(Exception):
    pass


class Budget:
    def __init__(self, max_calls):
        self.left, self.used = max_calls, 0

    def call(self, key, endpoint, params):
        if self.left <= 0:
            raise QuotaStop("호출 한도(--max-calls) 도달")
        self.left -= 1
        self.used += 1
        j = tc._get(endpoint, key, params)
        time.sleep(DELAY)
        try:
            code = str(j["response"]["header"]["resultCode"])
        except (TypeError, KeyError):
            return None
        if code in QUOTA_CODES:
            raise QuotaStop(f"TourAPI resultCode {code}")
        if code != "0000":
            return None
        try:
            items = j["response"]["body"]["items"]["item"]
        except (TypeError, KeyError):
            return []
        return items if isinstance(items, list) else ([items] if items else [])


def norm(s):
    return re.sub(r"\s+", "", re.sub(r"\(.*?\)", "", s or ""))


def variants(title):
    """검색어 변형(시도마다 다음 변형을 쓴다): 원문 -> 지역 접두어 제거 -> 괄호·구분자 제거 -> 앞 두 어절 -> 흔한 접미어 제거."""
    out = [title]
    toks = title.split()
    if len(toks) >= 2 and re.fullmatch(r"(서울|경기|인천|부산|대구|대전|광주|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주|[가-힣]{2}(시|군))", toks[0]):
        out.append(" ".join(toks[1:]))
    out.append(re.sub(r"[()·.\-]", " ", title).strip())
    if len(toks) >= 2:
        out.append(" ".join(toks[:2]))
    out.append(re.sub(r"(해수욕장|해변공원|해변|호수공원|공원|생태공원|국가정원|어린이박물관|어린이미술관|박물관|마을|숲길)$", "", title.replace(" ", "")).strip())
    seen, res = set(), []
    for v in out:
        v = re.sub(r"\s+", " ", v).strip()
        if len(v) >= 2 and v not in seen:
            seen.add(v)
            res.append(v)
    return res


def title_ok(seed_title, cand):
    a, b = norm(seed_title), norm(cand)
    if a == b:
        return 3                                                   # 이름 완전 일치
    t = seed_title.split()
    if len(t) >= 2 and norm(" ".join(t[1:])) == b:
        return 2                                                   # 지역 접두어만 뗀 일치(예: '제주 용두암' ↔ '용두암')
    if (len(a) >= 4 and a in b) or (len(b) >= 4 and b in a):
        return 1                                                   # 포함 일치: 하위 시설·다른 시설일 수 있어 사람이 승인해야 쓴다
    return 0


def addr_ok(seed_addr, cand_addr):
    toks = (seed_addr or "").split()
    return len(toks) >= 2 and toks[1] in (cand_addr or "")


def dist_label(m):
    return f"장소에서 직선 약 {m}m" if m < 1000 else f"장소에서 직선 약 {m / 1000:.1f}km"


def empty_curation():
    """육아 꿀팁(블로그/유튜브 요약) 적재용 빈 틀. 값은 출처·검수가 붙은 항목만 화면에 쓴다(스키마 문서 참조)."""
    return {"status": "empty", "parent_tips": [], "parking_tip": None, "stroller_difficulty": None, "recommended_ages": None}


def map_place(budget, key, s, attempt, today):
    """searchKeyword2 로 장소 매핑. 시도 횟수에 따라 키워드 변형을 바꿔 가며 쓴다. 반환: (record 또는 None, 사용한 키워드)."""
    vs = variants(s["title"])
    order = vs[attempt % len(vs):] + vs[:attempt % len(vs)]
    for kw in order[:2]:                                    # 한 번에 최대 2개 변형만(호출 절약)
        best = None
        for it in budget.call(key, "searchKeyword2", {"keyword": kw, "numOfRows": 20, "pageNo": 1, "arrange": "A"}) or []:
            sc = title_ok(s["title"], it.get("title", ""))
            if sc and addr_ok(s.get("address", ""), it.get("addr1", "")) and (best is None or sc > best[0]):
                best = (sc, it)
        if best:
            return best[1], kw, best[0]
    return None, order[0], 0


def menu_of(budget, key, cid, today):
    """detailIntro2(음식점)의 대표메뉴·취급메뉴(공식). 없으면 None."""
    items = budget.call(key, "detailIntro2", {"contentId": cid, "contentTypeId": 39}) or []
    if not items:
        return None
    it = items[0]
    first = re.sub(r"\s+", " ", (it.get("firstmenu") or "")).strip()
    treat = re.sub(r"\s+", " ", (it.get("treatmenu") or "")).strip()
    text = (first or (treat.split("/")[0].strip() if treat else ""))[:40]
    if not text.strip():
        return None
    kid = [w for w in KID_MENU_RE if w in (first + " " + treat)][:3]
    return {"v": text, "src": SRC, "checked": today, "kid_menu_hint": kid}


def nearby_for(budget, key, lat, lon, today):
    items = budget.call(key, "locationBasedList2", {"mapX": lon, "mapY": lat, "radius": RADIUS_M, "contentTypeId": 39, "arrange": "E", "numOfRows": 100, "pageNo": 1}) or []
    eats, cafes = [], []
    for it in items:
        try:
            m = int(float(it.get("dist", "")))
        except ValueError:
            continue
        cid = str(it.get("contentid") or "")
        if not re.fullmatch(r"\d{3,9}", cid) or not (it.get("title") or "").strip() or not (it.get("addr1") or "").strip():
            continue
        is_cafe = (it.get("cat3") or "") == CAFE_CAT3
        e = {"id": f"eat_t{cid}", "kind": "카페" if is_cafe else "식당", "name": it["title"].strip(), "address": it["addr1"].strip(), "level": "listed",
             "distance": {"v": dist_label(m), "mode": "straight", "meters": m},
             "identity": {"src": SRC, "checked": today, "note": f"한국관광공사 TourAPI 음식점 정보(contentid {cid})에서 확인", "tourapi_contentid": cid},
             "place_check": {"status": "unchecked", "note": "공식 출처로 이름·주소만 확인했고 영업 여부는 매장 확인이 필요해요"}}
        e["category"] = CAT3_LABEL.get(it.get("cat3") or "", "음식점")
        img, th = tc._safe_https(it.get("firstimage")), tc._safe_https(it.get("firstimage2"))
        if img and it.get("cpyrhtDivCd") == "Type1":
            e["photo"] = {"url": img, "thumb": th or img, "credit": "한국관광공사 TourAPI", "license": "공공누리 제1유형(출처표시)", "contentid": cid, "checked": today}
        (cafes if is_cafe else eats).append(e)
    eats, cafes = eats[:NEARBY_KEEP], cafes[:NEARBY_KEEP]
    for e in eats + cafes:      # 가까운 순 상위만 상세(대표메뉴) 조회: 호출 절약
        mn = menu_of(budget, key, e["identity"]["tourapi_contentid"], today)
        if mn:
            hint = mn.pop("kid_menu_hint")
            e["menu"] = mn
            if hint and e["kind"] == "식당":
                e["kid_menu_hint"] = hint
    return eats, cafes


def due(st, today):
    """다음에 처리할 차례인가: 처음이거나, 실패는 7일, 성공은 30일 뒤."""
    if not st or not st.get("last_attempt"):
        return True
    gap = (today - date.fromisoformat(st["last_attempt"])).days
    return gap >= (REFRESH_DAYS if st.get("status") == "ok" else RETRY_NO_MATCH_DAYS)


def priority(sid, rec, st):
    if not st or not st.get("last_attempt"):
        return (0, "")
    if not rec or not (rec.get("geo") or {}).get("lat"):
        return (1, st["last_attempt"])
    if not (rec.get("nearby") or {}).get("eats") and not (rec.get("nearby") or {}).get("cafes"):
        return (2, st["last_attempt"])
    return (3, st["last_attempt"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=str(PROJECT / "free_spots_seed.json"))
    ap.add_argument("--out", default=str(PROJECT))
    ap.add_argument("--state", default=str(PROJECT / "data" / "spot_queue_state.json"))
    ap.add_argument("--spots", type=int, default=100)
    ap.add_argument("--max-calls", type=int, default=400)
    ap.add_argument("--only", default="", help="쉼표로 구분한 시드 id 만 강제 처리(샌드박스 시험용)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    key = os.getenv("TOURAPI_SERVICE_KEY", "").strip()
    if not key:
        log.error("TOURAPI_SERVICE_KEY 없음 - 자동 보강 건너뜀")
        return 0
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    today_d = date.today()
    today = today_d.isoformat()
    seed = json.loads(Path(a.seed).read_text(encoding="utf-8"))["spots"]
    data_p, st_p = out / "spot_enriched.json", Path(a.state)
    st_p.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(data_p.read_text(encoding="utf-8")) if data_p.exists() else {"version": 1, "spots": {}}
    state = json.loads(st_p.read_text(encoding="utf-8")) if st_p.exists() else {}
    queue = [s for s in seed if s["id"] in a.only.split(",")] if a.only else sorted([s for s in seed if due(state.get(s["id"]), today_d)], key=lambda s: priority(s["id"], data["spots"].get(s["id"]), state.get(s["id"])))[: a.spots]
    log.info("대기 %d곳 중 오늘 처리 %d곳 (호출 한도 %d)", sum(1 for s in seed if due(state.get(s["id"]), today_d)), len(queue), a.max_calls)
    budget = Budget(a.max_calls)
    stats = {"mapped": 0, "review": 0, "no_match": 0, "photo": 0, "nearby": 0, "error": 0}
    stopped = ""
    for s in queue:
        sid = s["id"]
        st = state.get(sid) or {"attempts": 0}
        try:
            it, kw, kind = map_place(budget, key, s, st.get("attempts", 0), today)
            st.update(attempts=st.get("attempts", 0) + 1, last_attempt=today)
            if it and kind == 1 and s["title"] not in ACCEPT_CONTAINS:
                st.update(status="review", last_keyword=kw, candidate={"title": it.get("title"), "contentid": str(it.get("contentid")), "address": (it.get("addr1") or "").strip()})
                stats["review"] += 1
                state[sid] = st
                continue
            if not it:
                st.update(status="no_match", last_keyword=kw)
                stats["no_match"] += 1
                state[sid] = st
                continue
            rec = data["spots"].get(sid) or {}
            lat, lon = float(it["mapy"]), float(it["mapx"])
            rec.update(contentid=str(it["contentid"]), matched_title=it.get("title"), address=(it.get("addr1") or "").strip(), updated=today,
                       geo={"lat": round(lat, 6), "lon": round(lon, 6), "source": SRC, "checked": today})
            url, thumb = tc._safe_https(it.get("firstimage")), tc._safe_https(it.get("firstimage2"))
            if url and it.get("cpyrhtDivCd") == "Type1":
                rec["photo"] = {"url": url, "thumb": thumb or url, "credit": "한국관광공사 TourAPI", "license": "공공누리 제1유형(출처표시)", "contentid": str(it["contentid"]), "checked": today}
                rec["photo_status"] = "ok"
                stats["photo"] += 1
            else:
                rec.setdefault("photo", None)
                rec["photo_status"] = "license_not_type1" if url else "no_photo"
            eats, cafes = nearby_for(budget, key, lat, lon, today)
            rec["nearby"] = {"radius_m": RADIUS_M, "source": SRC, "checked": today, "eats": eats, "cafes": cafes}
            rec.setdefault("curation", empty_curation())
            data["spots"][sid] = rec
            st.update(status="ok", last_keyword=kw)
            stats["mapped"] += 1
            stats["nearby"] += 1 if (eats or cafes) else 0
            state[sid] = st
        except QuotaStop as e:
            stopped = str(e)
            log.error("수집 중단: %s", stopped)
            break
        except Exception as e:      # noqa: BLE001 - 한 곳의 실패가 전체를 막지 않게(타입명만 기록)
            stats["error"] += 1
            log.error("%s 처리 실패(%s)", sid, type(e).__name__)
            st.update(last_attempt=today, status="error")
            state[sid] = st
    data["generated"] = today
    for p, obj in ((data_p, data), (st_p, state)):
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    mapped_total = sum(1 for v in data["spots"].values() if (v.get("geo") or {}).get("lat"))
    log.info("완료: 오늘 매핑 %d · 검수 대기 %d · 못 찾음 %d · 사진 %d · 주변 식당 %d · 오류 %d · 호출 %d회 · 누적 좌표 %d/%d곳%s",
             stats["mapped"], stats["review"], stats["no_match"], stats["photo"], stats["nearby"], stats["error"], budget.used, mapped_total, len(seed), (" · 중단: " + stopped) if stopped else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
