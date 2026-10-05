"""[KOPIS 어린이 공연 수집기]

공연예술통합전산망(KOPIS) 오픈 API에서 아동 공연(kidstate=Y) 중 진행/예정 공연을 모아
사이트 루트의 kids_performances.json 으로 저장한다. map.html 의 [🎭 공연·전시] 탭이 읽는다.

- 인증키는 .env 의 KOPIS_API_KEY (코드/커밋에 넣지 않는다). 키는 로그에도 남기지 않는다.
- 목록 API(pblprfr)로 공연을 모은 뒤, 상세 API(pblprfr/{id})로 관람연령/티켓가격을 채운다.
- 공연장소의 시·도(area)를 지도 권역 키(REGION_GROUP과 같은 17개 광역명)로 매핑한다.

안전 규칙 (run_nightly_pipeline.ps1이 로그의 "Traceback"을 실패로 판정해 배포를 건너뜀):
run()은 절대 예외를 던지지 않고, 실패 시 한 줄 로그만 남기고 None을 반환한다.
실패해도 기존 kids_performances.json 은 덮어쓰지 않는다.

실행 (단독 테스트):
    python -m collector.kopis_collector
"""
from __future__ import annotations

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import requests

from config import BASE_DIR

logger = logging.getLogger(__name__)

OUTPUT_PATH = BASE_DIR / "kids_performances.json"
LIST_URL = "http://www.kopis.or.kr/openApi/restful/pblprfr"
DETAIL_URL = "http://www.kopis.or.kr/openApi/restful/pblprfr/{id}"
RANGE_DAYS = 75          # 오늘부터 이만큼 뒤까지 시작하는 공연
MAX_PAGES = 6            # 목록 100건 x 6페이지 상한
MAX_DETAIL = 600         # 상세 호출 상한 (관람연령/가격)
TIMEOUT = 20

# KOPIS 시·도명 -> 지도 데이터가 쓰는 광역명. 행정구역 개편 표기(전남광주통합특별시 등)도 키워드로 흡수한다.
_AREA_KEYWORDS = [
    ("서울", "서울"), ("경기", "경기"), ("인천", "인천"), ("강원", "강원"),
    ("충청북", "충북"), ("충북", "충북"), ("충청남", "충남"), ("충남", "충남"),
    ("대전", "대전"), ("세종", "세종"),
    ("전북", "전북"), ("전라북", "전북"),
    ("전남광주", "광주"), ("광주", "광주"), ("전남", "전남"), ("전라남", "전남"),
    ("경북", "경북"), ("경상북", "경북"), ("대구", "대구"),
    ("경남", "경남"), ("경상남", "경남"), ("부산", "부산"), ("울산", "울산"),
    ("제주", "제주"),
]


def map_area(area: str) -> str:
    for kw, region in _AREA_KEYWORDS:
        if kw in (area or ""):
            return region
    return ""


def _get(url: str, params: dict) -> ET.Element | None:
    last = ""
    for attempt in range(3):  # 호출이 몰리면 일시 거부될 수 있어 짧게 재시도
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                return ET.fromstring(r.content)
            last = f"HTTP {r.status_code}"
        except Exception as e:  # 키가 URL에 있어 예외 메시지 전체는 남기지 않는다
            last = type(e).__name__
        time.sleep(1.0 * (attempt + 1))
    logger.error("KOPIS 호출 실패 (%s)", last)
    return None


def _txt(node: ET.Element, tag: str) -> str:
    v = node.find(tag)
    return (v.text or "").strip() if v is not None and v.text else ""


def _iso(d: str) -> str:
    return d.replace(".", "-") if d else ""


def _fetch_list(key: str, start: date, end: date) -> list[dict]:
    out, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        root = _get(LIST_URL, {
            "service": key, "stdate": start.strftime("%Y%m%d"), "eddate": end.strftime("%Y%m%d"),
            "cpage": page, "rows": 100, "kidstate": "Y",
        })
        if root is None:
            break
        dbs = root.findall("db")
        for db in dbs:
            pid = _txt(db, "mt20id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            state = _txt(db, "prfstate")
            if state == "공연완료":
                continue
            out.append({
                "id": pid, "title": _txt(db, "prfnm"),
                "start_date": _iso(_txt(db, "prfpdfrom")), "end_date": _iso(_txt(db, "prfpdto")),
                "venue": _txt(db, "fcltynm"), "poster": _txt(db, "poster").replace("http://", "https://"),
                "area": _txt(db, "area"), "genre": _txt(db, "genrenm"), "state": state,
            })
        if len(dbs) < 100:
            break
        time.sleep(0.2)
    return out


def _fill_detail(key: str, item: dict) -> None:
    root = _get(DETAIL_URL.format(id=item["id"]), {"service": key})
    if root is None:
        return
    db = root.find("db")
    if db is None:
        return
    item["age"] = _txt(db, "prfage")
    item["price"] = _txt(db, "pcseguidance")
    item["runtime"] = _txt(db, "prfruntime")


def run() -> "os.PathLike | None":
    try:
        key = os.getenv("KOPIS_API_KEY", "").strip()
        if not key:
            logger.error("KOPIS_API_KEY 없음 - 어린이 공연 수집 건너뜀")
            return None
        today = date.today()
        items = _fetch_list(key, today, today + timedelta(days=RANGE_DAYS))
        if not items:
            logger.error("KOPIS 어린이 공연 0건 - 기존 파일 유지")
            return None
        # 진행 중 공연 우선, 그다음 시작일순. 상세(연령/가격)는 상한까지만 조회.
        items.sort(key=lambda x: (x["state"] != "공연중", x["start_date"]))
        for it in items[:MAX_DETAIL]:
            _fill_detail(key, it)
            time.sleep(0.3)
        perfs = []
        for it in items:
            region = map_area(it["area"])
            if not region:
                continue
            perfs.append({
                "id": it["id"], "title": it["title"], "start_date": it["start_date"], "end_date": it["end_date"],
                "venue": it["venue"], "region": region, "poster": it["poster"], "genre": it["genre"],
                "age": it.get("age", ""), "price": it.get("price", ""), "state": it["state"],
                "link": "https://www.kopis.or.kr/por/db/pblprfr/pblprfrView.do?menuId=MNU_00020&mt20Id=" + it["id"],
            })
        payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "count": len(perfs), "performances": perfs}
        OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        logger.info("KOPIS 어린이 공연 JSON 생성 (%d건): %s", len(perfs), OUTPUT_PATH)
        return OUTPUT_PATH
    except Exception as e:
        logger.error("KOPIS 수집 오류 - 무시하고 계속 진행 (%s)", type(e).__name__)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
