"""[서울시 공공서비스예약 수집기] 서울 열린데이터광장 OpenAPI(OA-2271 계열)에서 가족·육아 대상 체험/견학/교육 프로그램을 모아
루트의 seoul_reserve.json 으로 저장한다. map.html '주말나들이' 탭이 [예약] 뱃지와 함께 보여 준다(지도 연결은 드라이런 승인 후).

- 인증키는 .env 의 SEOUL_OPENAPI_KEY (코드/커밋/로그에 넣지 않는다. URL 경로에 키가 실리므로 예외는 타입명만 기록한다).
- 이용허락: 공공누리 1유형(출처표시, 상업적 이용·변경 가능) - 지도에 "출처: 서울시 공공서비스예약" 표기. 예약은 원문 예약 페이지(SVCURL)로 연결한다.
- 사용하는 서비스(실측 확인): ListPublicReservationCulture(문화체험: 교육체험·산림여가·서울형키즈카페·공원탐방·문화행사·전시/관람·농장체험)
  와 ListPublicReservationEducation(교육강좌 - 아이/가족 대상만). 시설대관(Institution)·체육시설(Sport)·진료(Medical)는 가족 프로그램이
  아니라 쓰지 않는다. 상세(ListPublicReservationDetail/1/1/<SVCID>)는 주소(ADRES)를 얻기 위해 최종 후보에만 호출하고 SVCID+기간으로 캐시한다.
- 요금: 목록에는 '무료/유료/유료(요금안내문의)'만 있고 금액이 없다. 상세 본문(DTLCONT)에 '이용요금 : 2,000원' 같은 문구가 있으면 그 금액을 쓰고,
  없으면 금액 없이 '유료'로 둔다(무료로 단정하지 않는다). 원문 PAYATNM 은 fee_text 로 보존한다.
- 필터: '무료' 여부로 거르지 않는다. 대상(USETGTINFO)·제목에 유아/어린이/가족/초등 등이 있거나 키즈카페/가족 체험이면 수집하고,
  성인/청년/어르신 전용·취업·학술 등은 제외한다.

안전 규칙 (run_nightly_pipeline.ps1이 로그의 "Traceback"을 실패로 판정해 배포를 건너뜀):
run()은 절대 예외를 던지지 않고, 실패 시 한 줄 로그만 남기고 None 을 반환한다. 응답이 불완전하거나 급감하면 기존 seoul_reserve.json 을 덮어쓰지 않는다.

실행 (단독):
    python -m collector.seoul_reserve_collector --dry-run     # 파일을 쓰지 않고 건수/분포/예시만 출력
    python -m collector.seoul_reserve_collector               # seoul_reserve.json 생성
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

from collector.fee_utils import describe_fee, parse_fee_max, parse_fee_min
from collector.kopis_collector import map_area
from config import BASE_DIR

logger = logging.getLogger(__name__)

OUTPUT_PATH = BASE_DIR / "seoul_reserve.json"
DETAIL_CACHE_PATH = BASE_DIR / "data" / "seoul_reserve_detail_cache.json"   # data/ 는 gitignore
BASE_URL = "http://openapi.seoul.go.kr:8088"
SERVICES = ("ListPublicReservationCulture", "ListPublicReservationEducation")
PAGE = 1000                    # 1회 최대 1000건(실측)
MAX_PAGES = 5                  # 서비스당 5000건 상한
TIMEOUT = 30
RANGE_DAYS = 75                # 이용 시작이 오늘부터 이 기간 안
MAX_DETAIL_CALLS = 800         # 상세(주소) 호출 상한 - 캐시에 없는 것만(첫 실행에 후보 대부분을 채운다)
MIN_PREV_FOR_DROP_GUARD = 30
MIN_KEEP_RATIO = 0.4
SOURCE_NAME = "서울시 공공서비스예약"
BOOKABLE_STATUS = {"접수중", "안내중"}     # 접수종료·예약마감·예약일시중지는 제외 (안내중 = 접수 오픈 예정)

_KID_TARGET = ["유아", "영유아", "영아", "어린이", "초등", "가족", "키즈", "아이", "미취학", "보호자", "부모", "청소년"]
_KID_TITLE = ["유아", "영유아", "어린이", "키즈", "가족", "아이", "초등", "놀이", "동화", "곤충", "공룡", "숲", "텃밭", "생태", "체험", "과학"]
_ADULT_TARGET = ["성인", "청년", "어르신", "노인", "중장년", "시니어", "직장인", "시민"]
_NEG_TITLE = ["취업", "채용", "학술", "심포지엄", "컨퍼런스", "세미나", "포럼", "전문가", "학회", "창업", "자격증", "청년"]
# 소분류 중 그 자체로 가족 프로그램인 것(대상이 '제한없음'이어도 수집)
_FAMILY_MINCLASS = {"서울형키즈카페", "농장체험", "교육체험", "산림여가", "공원탐방"}


# ---------------------------------------------------------------------------
# 순수 함수 (테스트 대상)
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _iso(s: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    return "-".join(m.groups()) if m else ""


def _strip_html(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", (h or "").replace("&nbsp;", " "))).strip()


def kid_target_score(row: dict) -> int | None:
    """가족·육아 적합도. 성인/청년/어르신 전용·취업/학술 제목은 None(제외), 아니면 가산점(0 이상).
    제외는 '대상'과 '제목'만 본다(본문의 일반 안내문에는 '성인' 같은 단어가 흔해 오탐이 난다)."""
    target = _norm(row.get("USETGTINFO"))
    title = _norm(row.get("SVCNM"))
    minc = row.get("MINCLASSNM") or ""
    if any(w in title for w in _NEG_TITLE):
        return None
    kid_in_target = any(w in target for w in _KID_TARGET)
    if any(w in target for w in _ADULT_TARGET) and not kid_in_target:
        return None                      # 성인/청년 전용 (가족·어린이가 같이 적혀 있으면 유지)
    score = sum(1 for w in _KID_TARGET if w in target) + sum(1 for w in _KID_TITLE if w in title)
    if minc in _FAMILY_MINCLASS or "키즈카페" in minc:
        score += 1
    # 수집 조건: 대상에 아이/가족이 있거나, 제목에 아이 키워드가 있거나, 가족형 소분류
    if kid_in_target or any(w in title for w in _KID_TITLE) or minc in _FAMILY_MINCLASS:
        return score
    return None


def fee_from_row(pay: str, dtlcont: str) -> dict:
    """PAYATNM('무료'/'유료'/'유료(요금안내문의)') + 상세 본문 금액 -> 요금 필드.
    본문에 '이용요금 : 2,000원'처럼 요금 키워드 뒤의 금액이 있으면 쓰고, 없으면 금액 없이 유료로 둔다(원문은 fee_text 에 보존)."""
    pay = (pay or "").strip()
    text = _strip_html(dtlcont)
    m = re.search(r"(?:이용\s*요금|참가비|참가\s*비용|입장료|수강료|체험비|요금|비용)[^\d]{0,25}\d[\d,]*\s*(?:만\s*원|천\s*원|원)", text)
    amount_text = m.group(0) if m else ""
    if pay == "무료":
        d = describe_fee("무료")
    elif pay.startswith("유료"):
        d = describe_fee(amount_text) if amount_text else {"fee_type": "paid", "is_free": False, "fee_min": None, "fee_max": None}
        if d["fee_type"] == "unknown":
            d = {"fee_type": "paid", "is_free": False, "fee_min": parse_fee_min(amount_text), "fee_max": parse_fee_max(amount_text)}
    else:
        d = describe_fee(amount_text)
    d = dict(d)
    d["fee_text"] = (pay + (f" ({amount_text[:60]})" if amount_text and pay.startswith("유료") else "")).strip()
    return d


def place_of(placenm: str) -> str:
    """'서울역사박물관>백인제가옥' -> '백인제가옥'(가장 구체적인 장소)."""
    parts = [p.strip() for p in (placenm or "").split(">") if p.strip()]
    return parts[-1] if parts else ""


def naver_map_url(address: str, place: str) -> str:
    # 괄호(중첩 포함) 이하는 층/호실 같은 부가 설명이라 네이버 지도 검색에서 뺀다
    q = re.sub(r"\s+", " ", (address or "").split("(")[0]).strip() or re.sub(r"\s+", " ", (place or "").split("(")[0]).strip()
    return "https://map.naver.com/p/search/" + quote(q)


def normalize_row(row: dict, today: date, range_days: int = RANGE_DAYS, detail: dict | None = None) -> dict | None:
    """원본 1건 -> 지도용 dict. 접수 불가·기간 밖·성인 전용·좌표/주소 모호면 None."""
    if (row.get("SVCSTATNM") or "") not in BOOKABLE_STATUS:
        return None
    sid = str(row.get("SVCID") or "").strip()
    title = (row.get("SVCNM") or "").strip()
    start, end = _iso(row.get("SVCOPNBGNDT")), _iso(row.get("SVCOPNENDDT"))
    if not (sid and title and start):
        return None
    end = end or start
    if end < today.isoformat() or start > (today + timedelta(days=range_days)).isoformat():
        return None
    rc_end = _iso(row.get("RCPTENDDT"))
    if rc_end and rc_end < today.isoformat():     # 상태가 '안내중'이어도 접수 기간이 이미 끝났으면 예약할 수 없다
        return None
    score = kid_target_score(row)
    if score is None:
        return None
    detail = detail or {}
    district = (row.get("AREANM") or "").strip()
    place = place_of(row.get("PLACENM") or detail.get("PLACENM") or "")
    address = (detail.get("ADRES") or "").strip() or " ".join(x for x in ("서울", district, place) if x)
    fee = fee_from_row(row.get("PAYATNM"), detail.get("DTLCONT") or row.get("DTLCONT") or "")
    try:
        lon, lat = float(row.get("X")), float(row.get("Y"))
    except (TypeError, ValueError):
        lon = lat = None
    if lon is not None and not (126.5 <= lon <= 127.5 and 37.3 <= lat <= 37.8):
        lon = lat = None                 # 서울 밖/잘못된 좌표는 좌표만 버린다(주소로 안내 가능)
    return {
        "id": "seoul_" + sid, "svcid": sid, "title": title, "category": row.get("MINCLASSNM") or "", "status": row.get("SVCSTATNM"),
        "start_date": start, "end_date": end,
        "rcpt_start": _iso(row.get("RCPTBGNDT")), "rcpt_end": rc_end,
        "place": place, "address": address, "district": district,
        # 서울시 프로그램이어도 장소가 서울 밖(예: 경기 안성 체험마을)이면 주소 기준 권역으로 둔다 - 첫 토큰(시·도)만 본다
        "region": map_area(address.split(" ")[0]) if detail.get("ADRES") and map_area(address.split(" ")[0]) else "서울",
        "target": (row.get("USETGTINFO") or "").strip(), "tel": (row.get("TELNO") or "").strip(),
        "mapx": lon, "mapy": lat, "kid_score": score,
        "fee_text": fee["fee_text"], "fee_type": fee["fee_type"], "is_free": fee["is_free"], "fee_min": fee["fee_min"], "fee_max": fee["fee_max"],
        "reservable": True, "reserve_url": (row.get("SVCURL") or "").strip(),
        "source": SOURCE_NAME, "link": naver_map_url(address, place),
    }


def sort_items(items: list[dict]) -> list[dict]:
    """접수 마감이 가까운 순(예약을 서두를 것 우선) -> 아이 적합도 높은 순 -> 제목."""
    return sorted(items, key=lambda x: (x.get("rcpt_end") or "9999-99-99", -(x.get("kid_score") or 0), x.get("title", "")))


# ---------------------------------------------------------------------------
# 네트워크 (키가 URL 경로에 실리므로 예외 메시지 전체는 남기지 않는다)
# ---------------------------------------------------------------------------
def _get(path: str, key: str) -> dict | None:
    last = ""
    for attempt in range(3):
        try:
            r = requests.get(f"{BASE_URL}/{key}/json/{path}", timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = type(e).__name__
        time.sleep(1.0 * (attempt + 1))
    logger.error("서울 공공서비스예약 %s 호출 실패 (%s)", path.split("/")[0], last)
    return None


def fetch_service(service: str, key: str) -> tuple[list[dict], bool]:
    """(행 목록, 완전 수집 여부). RESULT 코드가 INFO-000/INFO-200(데이터 없음)이 아니면 불완전."""
    rows: list[dict] = []
    for i in range(MAX_PAGES):
        s = i * PAGE + 1
        d = _get(f"{service}/{s}/{s + PAGE - 1}/", key)
        body = (d or {}).get(service) if isinstance(d, dict) else None
        if not isinstance(body, dict):
            return rows, False
        code = (body.get("RESULT") or {}).get("CODE", "")
        if code not in ("INFO-000", "INFO-200"):
            return rows, False
        got = body.get("row") or []
        rows += got
        if len(got) < PAGE:
            return rows, True
        time.sleep(0.2)
    return rows, False                   # 상한까지 꽉 참 -> 잘렸을 수 있음


def _load_cache() -> dict:
    try:
        return json.loads(DETAIL_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fetch_detail(key: str, row: dict, cache: dict, budget: list[int]) -> dict:
    """상세(주소/본문). 캐시 키는 SVCID + 이용 종료일(기간이 바뀌면 다시 받는다)."""
    ck = f"{row.get('SVCID')}|{row.get('SVCOPNENDDT')}"
    if ck in cache:
        return cache[ck]
    if budget[0] <= 0:
        return {}
    budget[0] -= 1
    d = _get(f"ListPublicReservationDetail/1/1/{row.get('SVCID')}", key)
    rows = ((d or {}).get("ListPublicReservationDetail") or {}).get("row") or []
    det = {k: (rows[0].get(k) or "") for k in ("ADRES", "PLACENM", "DTLCONT")} if rows else {}
    if det:
        det["DTLCONT"] = _strip_html(det.get("DTLCONT", ""))[:3000]     # 캐시 용량 절약(요금 문구 추출에는 충분)
        cache[ck] = det
    time.sleep(0.1)
    return det


def build_items(key: str, today: date) -> tuple[list[dict], dict]:
    all_rows, complete, per_service = [], True, {}
    for svc in SERVICES:
        rows, ok = fetch_service(svc, key)
        complete = complete and ok
        per_service[svc] = len(rows)
        all_rows += rows
    cache, budget = _load_cache(), [MAX_DETAIL_CALLS]
    items, dropped = [], Counter()
    for r in all_rows:
        # 주소·요금은 상세가 필요하지만, 후보가 아닌 행에 호출하지 않도록 상세 없이 먼저 판정한다
        if normalize_row(r, today) is None:
            dropped["제외(상태/기간/대상)"] += 1
            continue
        it = normalize_row(r, today, detail=fetch_detail(key, r, cache, budget))
        if it:
            items.append(it)
    try:
        DETAIL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        DETAIL_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    seen, uniq = set(), []
    for it in items:                    # 같은 SVCID 가 두 서비스에 겹쳐도 한 번만
        if it["id"] not in seen:
            seen.add(it["id"]); uniq.append(it)
    stats = {"complete": complete, "raw_by_service": per_service, "raw": len(all_rows), "kept": len(uniq), "dropped": dict(dropped),
             "category": dict(Counter(i["category"] for i in uniq)), "fee_type": dict(Counter(i["fee_type"] for i in uniq)),
             "paid_with_amount": sum(1 for i in uniq if (i.get("fee_min") or 0) > 0), "district": dict(Counter(i["district"] for i in uniq).most_common(8)),
             "status": dict(Counter(i["status"] for i in uniq)), "detail_calls_used": MAX_DETAIL_CALLS - budget[0]}
    return sort_items(uniq), stats


def _existing_count() -> int:
    try:
        return int(json.loads(OUTPUT_PATH.read_text(encoding="utf-8")).get("count", 0))
    except (OSError, ValueError, TypeError):
        return 0


def run(dry_run: bool = False) -> "os.PathLike | dict | None":
    try:
        key = os.getenv("SEOUL_OPENAPI_KEY", "").strip()
        if not key:
            logger.error("SEOUL_OPENAPI_KEY 없음 - 서울 공공서비스예약 수집 건너뜀")
            return None
        items, stats = build_items(key, date.today())
        if dry_run:
            return {**stats, "examples": items[:6]}
        if not stats["complete"]:
            logger.error("서울 공공서비스예약 응답이 불완전 - 기존 파일 유지")
            return None
        if not items:
            logger.error("서울 공공서비스예약 0건 - 기존 파일 유지")
            return None
        prev = _existing_count()
        if prev >= MIN_PREV_FOR_DROP_GUARD and len(items) < prev * MIN_KEEP_RATIO:
            logger.error("서울 공공서비스예약 %d건 -> %d건으로 급감 - 이상 응답으로 보고 기존 파일 유지", prev, len(items))
            return None
        payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": SOURCE_NAME, "count": len(items), "items": items}
        tmp = OUTPUT_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(OUTPUT_PATH)
        logger.info("서울 공공서비스예약 JSON 생성 (%d건): %s", len(items), OUTPUT_PATH)
        return OUTPUT_PATH
    except Exception as e:
        logger.error("서울 공공서비스예약 수집 오류 - 무시하고 계속 진행 (%s)", type(e).__name__)
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 건수/분포/예시만 출력")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = run(dry_run=a.dry_run)
    if isinstance(res, dict):
        print(json.dumps(res, ensure_ascii=False, indent=2))
