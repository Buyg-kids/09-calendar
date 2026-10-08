"""[TourAPI 행사·축제 수집기 - 1단계 골격]

한국관광공사 TourAPI(KorService2)의 searchFestival2 로 기간 한정 지자체 행사·축제를 모아 tour_events.json 으로 저장한다.
(1단계: 상시 장소/이미지는 제외, 텍스트·링크만. 지도 레이어/출처 표기/파이프라인 연결은 이후 단계.)

- 인증키는 .env 의 TOURAPI_SERVICE_KEY (코드/커밋/로그에 넣지 않는다. 예외는 타입명만 기록한다).
- 응답 필드 이름(eventstartdate, usetimefestival 등)은 공식 문서 기준 가정이다. 키 발급 후 첫 `--dry-run` 으로 실제 응답과
  eventStartDate/eventEndDate 필터 의미(겹침 vs 시작일 기준)를 확인하고 필요하면 여기만 고친다. 날짜 범위는 클라이언트에서도 다시 거른다.
- 지도 권역은 areaCode → 광역명(KOPIS 수집기와 같은 17개 키)으로 매핑하고, 주소 문자열로 교차검증해 어긋나면 버린다.
- 무료 판별(fee_type)·아이 적합도(kid_score)·KOPIS 중복 제거는 모두 순수 함수라 네트워크 없이 테스트된다.

안전 규칙 (run_nightly_pipeline.ps1이 로그의 "Traceback"을 실패로 판정해 배포를 건너뜀):
run()은 절대 예외를 던지지 않고, 실패 시 한 줄 로그만 남기고 None을 반환한다. 실패해도 기존 tour_events.json 은 덮어쓰지 않는다.
※ 아직 야간 파이프라인(generator/card_news.py)에는 연결하지 않았다.

실행 (단독):
    python -m collector.tour_collector --dry-run     # 파일을 쓰지 않고 건수/분포만 출력
    python -m collector.tour_collector               # tour_events.json 생성
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import quote

import requests

from collector.kopis_collector import map_area
from config import BASE_DIR

logger = logging.getLogger(__name__)

OUTPUT_PATH = BASE_DIR / "tour_events.json"
KOPIS_PATH = BASE_DIR / "kids_performances.json"
DETAIL_CACHE_PATH = BASE_DIR / "data" / "tour_detail_cache.json"   # data/ 는 gitignore
HOME_CACHE_PATH = BASE_DIR / "data" / "tour_home_cache.json"       # 공식 홈페이지 URL 캐시 (modifiedtime 기준)
BASE_URL = "https://apis.data.go.kr/B551011/KorService2"
SOURCE_NAME = "한국관광공사"
RANGE_DAYS = 75          # KOPIS 수집기와 같은 기간
ROWS = 100
MAX_PAGES = 5
MAX_DETAIL_CALLS = 150   # 상세(요금) 호출 상한 - modifiedtime 이 바뀐 것만 호출한다
MAX_HOME_CALLS = 250     # 공식 홈페이지(detailCommon2) 호출 상한 - 역시 modifiedtime 이 바뀐 것만
TIMEOUT = 20

# TourAPI 지역코드(areaCode) -> 지도 데이터 광역명. 코드표는 areaCode2 로 확인 가능(첫 드라이런에서 대조).
AREA_CODE_TO_REGION = {
    "1": "서울", "2": "인천", "3": "대전", "4": "대구", "5": "광주", "6": "부산", "7": "울산", "8": "세종",
    "31": "경기", "32": "강원", "33": "충북", "34": "충남", "35": "경북", "36": "경남", "37": "전북", "38": "전남", "39": "제주",
}

# 법정동 시도코드(lDongRegnCd 앞 2자리) -> 광역명. 2026-10 드라이런에서 areacode 는 비어 있고 이 필드만 채워짐을 확인했다.
# 구 코드(29/42/45/46)와 신 코드(12 전남광주통합, 51 강원특별자치도, 52 전북특별자치도)를 모두 받는다.
# 전남광주통합특별시(12)는 KOPIS 수집기와 같이 '광주'로 두고, 지도는 두 권역을 '전남/광주' 한 권역으로 묶는다.
LDONG_TO_REGION = {
    "11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주", "30": "대전", "31": "울산", "36": "세종",
    "41": "경기", "42": "강원", "51": "강원", "43": "충북", "44": "충남", "45": "전북", "52": "전북",
    "46": "전남", "12": "광주", "47": "경북", "48": "경남", "50": "제주",
}
MAX_DURATION_DAYS = 45   # '섬 방문의 해'처럼 몇 달~몇 년짜리 캠페인/상설 항목은 기간 한정 행사가 아니라서 제외

# 한국 본토+제주 대략 범위(경도 mapx, 위도 mapy). 벗어나면 잘못된 좌표로 보고 버린다.
KOREA_LON = (124.0, 132.0)
KOREA_LAT = (33.0, 39.0)


# ---------------------------------------------------------------------------
# 순수 함수 (테스트 대상)
# ---------------------------------------------------------------------------
def region_of(area_code: str, address: str, ldong_code: str = "") -> str:
    """법정동 시도코드(lDongRegnCd)/areaCode 로 권역을 정하고 주소로 교차검증. 코드가 없으면 주소만으로, 어긋나면 ''(제외)."""
    ld = str(ldong_code or "").strip()[:2]
    by_code = LDONG_TO_REGION.get(ld, "") or AREA_CODE_TO_REGION.get(str(area_code or "").strip(), "")
    # 주소 전체가 아니라 첫 토큰(시·도)만 본다 - "부산 해운대구" 의 '대구' 처럼 구 이름이 다른 광역으로 오인되는 것 방지
    by_addr = map_area((address or "").split(" ")[0] if (address or "").strip() else "")
    if by_code and by_addr and by_code != by_addr:
        return ""
    return by_code or by_addr


_FREE_RE = re.compile(r"무\s*료|free|입장료\s*없|비용\s*없|참가비\s*없|관람료\s*없", re.IGNORECASE)
_PAID_RE = re.compile(r"유\s*료|\d[\d,]*\s*원|\d+\s*만\s*원")
_NOT_FREE_RE = re.compile(r"무\s*료\s*(?:가\s*)?(?:아님|아니|불가|제외)")
# '입장/관람/참가(료) 무료' 처럼 들어가는 비용이 무료라고 명시되면, 일부 체험·푸드트럭 등 부가 유료 언급이 있어도 무료로 본다.
_ENTRY_FREE_RE = re.compile(r"(?:입장|관람|참가|참여)\s*(?:료|비)?\s*(?:은|는)?\s*(?:전\s*면\s*)?무\s*료")


def classify_fee(text: str) -> str:
    """요금 텍스트 -> 'free'(전면 무료) / 'partial'(일부 무료·일부 유료) / 'paid' / 'unknown'(정보 없음)."""
    t = (text or "").strip()
    if not t:
        return "unknown"
    if _NOT_FREE_RE.search(t):
        return "paid"
    if _ENTRY_FREE_RE.search(t):
        return "free"
    free, paid = bool(_FREE_RE.search(t)), bool(_PAID_RE.search(t))
    if free and paid:
        return "partial"
    if free:
        return "free"
    if paid:
        return "paid"
    return "unknown"


_KID_POS = ["어린이", "아이", "가족", "키즈", "유아", "영유아", "체험", "동물", "놀이", "동화", "인형극", "캐릭터",
            "곤충", "공룡", "과학", "독서", "퍼레이드", "숲"]   # 2026-10-07 보강: 곤충/공룡/과학/독서/퍼레이드/숲 추가
_KID_NEG = ["맥주", "와인", "막걸리", "소주", "주류", "클럽", "성인", "19세", "술축제", "나이트", "와인페스타"]


def kid_score(title: str, extra: str = "") -> int | None:
    """아이 동반 적합도 점수. 성인 대상 키워드가 있으면 None(제외), 아니면 긍정 키워드 개수(0 이상).
    TourAPI 에는 연령 필드가 없어 키워드 기반이다 - 임계값은 드라이런 분포를 보고 정한다."""
    hay = ((title or "") + " " + (extra or "")).replace(" ", "")
    if any(w in hay for w in _KID_NEG):
        return None
    return len({w for w in _KID_POS if w in hay})


_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def clean_homepage(text: str) -> str:
    """detailCommon2 의 homepage(HTML 앵커/맨 주소/스킴 없는 주소가 섞여 있음) -> 안전한 http(s) URL, 없으면 ''."""
    t = (text or "").strip()
    if not t:
        return ""
    m = _HREF_RE.search(t)
    url = m.group(1).strip() if m else re.sub(r"<[^>]+>", " ", t).split()[0] if re.sub(r"<[^>]+>", " ", t).split() else ""
    if not url or url.lower().startswith(("javascript:", "data:", "mailto:", "tel:")):
        return ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url.lstrip("/")
    host = re.sub(r"^https?://", "", url, flags=re.IGNORECASE).split("/")[0]
    return url if "." in host and " " not in url else ""


def naver_map_url(address: str, title: str = "") -> str:
    """네이버 지도 검색 URL. 행사명이 아니라 주소(괄호 속 동 이름 제거)로 검색해야 '업체 없음'이 나지 않는다. 주소가 없으면 장소/제목."""
    q = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", address or "")).strip() or (title or "").strip()
    return "https://map.naver.com/p/search/" + quote(q)


# 공공누리 유형 중 상업적 이용이 가능한 1·3유형(출처표시, 3유형은 변경금지)만 이미지를 쓴다. 2·4유형(상업적 이용 금지)이나 미표시는 제외.
IMAGE_OK_TYPES = {"Type1", "Type3"}


def _safe_https(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    host = u.split("/")[2] if u.startswith("https://") and len(u.split("/")) > 2 else ""
    return u if host == "visitkorea.or.kr" or host.endswith(".visitkorea.or.kr") else ""


def pick_images(raw: dict) -> tuple[str, str]:
    """(대표 이미지 firstimage, 썸네일 firstimage2). 저작권 유형이 허용 범위가 아니거나 공식 호스트가 아니면 ('', '')."""
    if (raw.get("cpyrhtDivCd") or "") not in IMAGE_OK_TYPES:
        return "", ""
    return _safe_https(raw.get("firstimage")), _safe_https(raw.get("firstimage2"))


def sort_events(events: list[dict]) -> list[dict]:
    """아이 적합도 점수가 높은 행사를 위로, 같으면 시작일 빠른 순, 그다음 제목순. (점수로 제외하지는 않는다 - 성인 대상만 kid_score()가 걸러냄)"""
    return sorted(events, key=lambda e: (-(e.get("kid_score") or 0), e.get("start_date", ""), e.get("title", "")))


def _norm_title(s: str) -> str:
    s = re.sub(r"[\[\(【<].*?[\]\)】>]", "", s or "")        # [서울] (무료) 같은 꼬리표 제거
    return re.sub(r"[\W_]+", "", s).lower()


def _overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    if not (a_start and b_start):
        return False
    a_end, b_end = a_end or a_start, b_end or b_start
    return a_start <= b_end and b_start <= a_end


def dedupe_against_kopis(events: list[dict], performances: list[dict]) -> tuple[list[dict], list[dict]]:
    """KOPIS 공연과 같은 행사를 제거한다(KOPIS 우선). 같은 권역 + 기간 겹침 + 제목 일치(정규화 후 같거나 한쪽이 4자 이상으로 포함)."""
    perf = [(_norm_title(p.get("title", "")), p) for p in performances if p.get("title")]
    kept, removed = [], []
    for ev in events:
        et = _norm_title(ev.get("title", ""))
        dup = False
        if et:
            for pt, p in perf:
                if not pt or p.get("region") != ev.get("region"):
                    continue
                same = et == pt or (min(len(et), len(pt)) >= 4 and (et in pt or pt in et))
                if same and _overlap(ev.get("start_date", ""), ev.get("end_date", ""), p.get("start_date", ""), p.get("end_date", "")):
                    dup = True
                    break
        (removed if dup else kept).append(ev)
    return kept, removed


def _iso(d: str) -> str:
    d = re.sub(r"\D", "", str(d or ""))
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else ""


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_items(payload: dict) -> list[dict]:
    """TourAPI JSON 응답 -> item 리스트. 결과 없음(items가 '' 또는 item 이 단일 dict)도 처리한다."""
    try:
        items = payload["response"]["body"]["items"]
    except (KeyError, TypeError):
        return []
    if not items or not isinstance(items, dict):
        return []
    item = items.get("item", [])
    return [item] if isinstance(item, dict) else list(item or [])


def normalize_item(raw: dict, today: date, range_days: int = RANGE_DAYS, fee_text: str = "", homepage: str = "") -> dict | None:
    """원본 item 1건 -> 지도용 이벤트 dict. 지역/날짜/좌표가 불분명하거나 성인 대상이면 None."""
    cid = str(raw.get("contentid") or "").strip()
    title = (raw.get("title") or "").strip()
    start, end = _iso(raw.get("eventstartdate")), _iso(raw.get("eventenddate"))
    if not (cid and title and start):
        return None
    end = end or start
    horizon = (today + timedelta(days=range_days)).isoformat()
    if end < today.isoformat() or start > horizon:      # 이미 끝났거나 너무 먼 미래
        return None
    if (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 > MAX_DURATION_DAYS:   # 장기 캠페인/상설
        return None
    address = " ".join(x for x in (raw.get("addr1"), raw.get("addr2")) if x).strip()
    region = region_of(raw.get("areacode"), address, raw.get("lDongRegnCd"))
    if not region:
        return None
    lon, lat = _float(raw.get("mapx")), _float(raw.get("mapy"))
    if lon is not None and lat is not None and not (KOREA_LON[0] <= lon <= KOREA_LON[1] and KOREA_LAT[0] <= lat <= KOREA_LAT[1]):
        return None                                      # 좌표가 한국 밖이면 잘못된 데이터
    score = kid_score(title, fee_text)
    if score is None:
        return None
    ftype = classify_fee(fee_text)
    return {
        "id": "tour_" + cid, "title": title, "start_date": start, "end_date": end,
        "place": (raw.get("addr1") or "").strip(), "address": address, "region": region,
        "mapx": lon, "mapy": lat, "fee_text": fee_text, "fee_type": ftype, "is_free": ftype == "free",
        "kid_score": score, "tel": (raw.get("tel") or "").strip(), "modifiedtime": str(raw.get("modifiedtime") or ""),
        "source": SOURCE_NAME,
        "link": naver_map_url((raw.get("addr1") or ""), title),   # 이미지 없이 링크만 - 주소(addr1) 기반 네이버 지도
        "image": pick_images(raw)[0], "image_thumb": pick_images(raw)[1],   # 상세 시트 상단용(원본 비율 그대로, 가공 금지)
        "info_url": clean_homepage(homepage),                      # 공식/상세 안내(TourAPI detailCommon2 homepage), 없으면 ''
    }


# ---------------------------------------------------------------------------
# 네트워크 (키가 URL에 실리므로 예외 메시지 전체는 남기지 않는다)
# ---------------------------------------------------------------------------
def _get(endpoint: str, key: str, params: dict) -> dict | None:
    q = {"serviceKey": key, "MobileOS": "ETC", "MobileApp": "BUYG", "_type": "json", **params}
    last = ""
    for attempt in range(3):
        try:
            r = requests.get(f"{BASE_URL}/{endpoint}", params=q, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = type(e).__name__
        time.sleep(1.0 * (attempt + 1))
    logger.error("TourAPI %s 호출 실패 (%s)", endpoint, last)
    return None


def fetch_festivals(key: str, today: date, range_days: int = RANGE_DAYS) -> tuple[list[dict], bool]:
    """(행사 원본 목록, 완전 수집 여부). 어느 페이지든 호출에 실패했거나 상한(MAX_PAGES)까지 꽉 차면 complete=False -
    부분 결과로 기존 tour_events.json 을 덮어쓰지 않기 위한 신호다."""
    out, seen = [], set()
    complete = False
    for page in range(1, MAX_PAGES + 1):
        payload = _get("searchFestival2", key, {
            "numOfRows": ROWS, "pageNo": page,
            "eventStartDate": today.strftime("%Y%m%d"),
            "eventEndDate": (today + timedelta(days=range_days)).strftime("%Y%m%d"),
        })
        if payload is None:
            return out, False
        items = parse_items(payload)
        for it in items:
            cid = str(it.get("contentid") or "")
            if cid and cid not in seen:
                seen.add(cid)
                out.append(it)
        if len(items) < ROWS:
            complete = True      # 마지막 페이지까지 정상적으로 읽음
            break
        time.sleep(0.2)
    return out, complete


def fetch_homepage(key: str, raw: dict, cache: dict, budget: list[int]) -> str:
    """공식 홈페이지 URL(detailCommon2). modifiedtime 이 같으면 캐시를 쓰고, 호출은 budget 만큼만."""
    cid, mod = str(raw.get("contentid")), str(raw.get("modifiedtime") or "")
    hit = cache.get(cid)
    if hit is not None and hit.get("modifiedtime") == mod:
        return hit.get("homepage", "")
    if budget[0] <= 0:
        return hit.get("homepage", "") if hit else ""
    budget[0] -= 1
    payload = _get("detailCommon2", key, {"contentId": cid})
    items = parse_items(payload) if payload else []
    hp = clean_homepage((items[0].get("homepage") if items else "") or "")
    cache[cid] = {"modifiedtime": mod, "homepage": hp}
    time.sleep(0.2)
    return hp


def _load_cache() -> dict:
    try:
        return json.loads(DETAIL_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fetch_fee(key: str, raw: dict, cache: dict, budget: list[int]) -> str:
    """상세 소개(detailIntro2)의 이용요금 텍스트. modifiedtime 이 같으면 캐시를 쓰고, 호출은 budget 만큼만."""
    cid, mod = str(raw.get("contentid")), str(raw.get("modifiedtime") or "")
    hit = cache.get(cid)
    if hit and hit.get("modifiedtime") == mod:
        return hit.get("fee_text", "")
    if budget[0] <= 0:
        return hit.get("fee_text", "") if hit else ""
    budget[0] -= 1
    payload = _get("detailIntro2", key, {"contentId": cid, "contentTypeId": raw.get("contenttypeid") or "15"})
    items = parse_items(payload) if payload else []
    fee = re.sub(r"<[^>]+>", " ", (items[0].get("usetimefestival") if items else "") or "").strip()
    cache[cid] = {"modifiedtime": mod, "fee_text": fee}
    time.sleep(0.2)
    return fee


# ---------------------------------------------------------------------------
def build_events(key: str, today: date) -> tuple[list[dict], dict]:
    raws, complete = fetch_festivals(key, today)
    cache, budget = _load_cache(), [MAX_DETAIL_CALLS]
    try:
        hcache = json.loads(HOME_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hcache = {}
    hbudget = [MAX_HOME_CALLS]
    events, dropped = [], Counter()
    for raw in raws:
        fee = fetch_fee(key, raw, cache, budget)
        ev = normalize_item(raw, today, fee_text=fee)
        if ev:   # 홈페이지는 지도에 올라갈 행사에 대해서만 조회한다
            ev["info_url"] = fetch_homepage(key, raw, hcache, hbudget)
        if ev:
            events.append(ev)
        else:
            dropped["제외(지역/날짜/좌표/성인대상)"] += 1
    try:
        DETAIL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        DETAIL_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        HOME_CACHE_PATH.write_text(json.dumps(hcache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    try:
        perfs = json.loads(KOPIS_PATH.read_text(encoding="utf-8")).get("performances", [])
    except (OSError, ValueError):
        perfs = []
    events, dup = dedupe_against_kopis(events, perfs)
    stats = {"complete": complete, "raw": len(raws), "kept": len(events), "dropped": dict(dropped), "kopis_dup": len(dup),
             "fee_type": dict(Counter(e["fee_type"] for e in events)),
             "region": dict(Counter(e["region"] for e in events)),
             "kid_score": dict(Counter(e["kid_score"] for e in events))}
    return events, stats


MIN_PREV_FOR_DROP_GUARD = 30   # 기존 파일에 이 건수 이상 있을 때만 급감 검사
MIN_KEEP_RATIO = 0.4           # 새 결과가 기존의 40% 미만이면 API 이상으로 간주


def _existing_count() -> int:
    try:
        return int(json.loads(OUTPUT_PATH.read_text(encoding="utf-8")).get("count", 0))
    except (OSError, ValueError, TypeError):
        return 0


def run(dry_run: bool = False) -> "os.PathLike | dict | None":
    try:
        key = os.getenv("TOURAPI_SERVICE_KEY", "").strip()
        if not key:
            logger.error("TOURAPI_SERVICE_KEY 없음 - TourAPI 행사 수집 건너뜀")
            return None
        events, stats = build_events(key, date.today())
        if dry_run:
            return stats
        if not stats["complete"]:
            logger.error("TourAPI 응답이 불완전(호출 실패 또는 페이지 상한) - 기존 파일 유지")
            return None
        if not events:
            logger.error("TourAPI 행사 0건 - 기존 파일 유지")
            return None
        prev = _existing_count()
        if prev >= MIN_PREV_FOR_DROP_GUARD and len(events) < prev * MIN_KEEP_RATIO:
            logger.error("TourAPI 행사 %d건 -> %d건으로 급감 - 이상 응답으로 보고 기존 파일 유지", prev, len(events))
            return None
        events = sort_events(events)
        payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": SOURCE_NAME,
                   "count": len(events), "events": events}
        tmp = OUTPUT_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(OUTPUT_PATH)
        logger.info("TourAPI 행사 JSON 생성 (%d건): %s", len(events), OUTPUT_PATH)
        return OUTPUT_PATH
    except Exception as e:
        logger.error("TourAPI 수집 오류 - 무시하고 계속 진행 (%s)", type(e).__name__)
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 건수/분포만 출력")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = run(dry_run=a.dry_run)
    if isinstance(res, dict):
        print(json.dumps(res, ensure_ascii=False, indent=2))
