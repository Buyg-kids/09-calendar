"""[경기도 공공서비스예약 수집기 - DRAFT 뼈대] 경기데이터드림(openapi.gg.go.kr) OpenAPI 에서 체험/견학 예약 프로그램을 모아
gg_reserve.json 으로 저장하기 위한 골격. 아직 야간 파이프라인(generator/card_news.run)에 연결하지 않았다.

상태 (2026-10-10 실측, 상세는 docs/journal 의 경기 API 검증 리포트):
- 인증키(.env 의 GG_DATA_KEY)는 유효. 응답 형식은 {서비스명: [{"head": [{"list_total_count": N}, {"RESULT": {"CODE","MESSAGE"}}]}, {"row": [...]}]}.
  데이터 없음/오류는 최상위 {"RESULT": {"CODE": "INFO-200", ...}} 형태.
- 서울시 공공서비스예약(yeyak.seoul.go.kr)처럼 '도 전체 체험 예약'을 한 번에 주는 통합 API 는 찾지 못했다.
  (경기공유서비스는 시설 대관, 시/군 단위 체험 목록이 개별 서비스로 흩어져 있음)  따라서 GG_SERVICES 는 비어 있고,
  서비스명·필드 매핑이 확인될 때마다 SERVICES 에 한 줄씩 추가하는 구조다. 비어 있으면 run()은 아무것도 하지 않는다.

안전 규칙은 seoul_reserve_collector 와 같다: run()은 절대 예외를 던지지 않고(logger.exception 금지 - 로그에 "Traceback" 이 남으면
야간 배포가 중단된다), 응답이 불완전/0건/급감이면 기존 gg_reserve.json 을 덮어쓰지 않는다. 키는 URL 에 실리므로 예외는 타입명만 기록한다.

실행 (단독):
    python -m collector.gg_reserve_collector --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime
from urllib.parse import quote

import requests

from config import BASE_DIR

logger = logging.getLogger(__name__)

OUTPUT_PATH = BASE_DIR / "gg_reserve.json"
BASE_URL = "https://openapi.gg.go.kr"
PAGE = 1000
MAX_PAGES = 5
TIMEOUT = 30
SOURCE_NAME = "경기데이터드림"
MIN_PREV_FOR_DROP_GUARD = 30
MIN_KEEP_RATIO = 0.4

# 확인된 서비스를 한 줄씩 추가한다. fields: 논리 필드 -> 응답 컬럼명(없으면 생략).
#   id/title/start/end/rcpt_end/place/address/tel/fee/url  (url 은 https 예약·안내 페이지여야 지도에 노출된다)
# 예) {"name": "<OpenAPI 서비스명>", "label": "용인시 숲체험", "fields": {"id": "...", "title": "...", "start": "...", "end": "...", "address": "...", "url": "..."}}
SERVICES: list[dict] = []


def _iso(s: str) -> str:
    import re
    m = re.match(r"(\d{4})[-.]?(\d{2})[-.]?(\d{2})", str(s or "").strip())
    return "-".join(m.groups()) if m else ""


def parse_response(data: object, service: str) -> tuple[list[dict], int | None, str]:
    """(rows, list_total_count, result_code). 정상은 INFO-000, 데이터 없음 INFO-200, 그 외는 오류 코드/'PARSE'."""
    if not isinstance(data, dict):
        return [], None, "PARSE"
    top = data.get("RESULT")
    if isinstance(top, dict):
        return [], None, str(top.get("CODE") or "PARSE")
    blocks = data.get(service)
    if not isinstance(blocks, list):
        return [], None, "PARSE"
    rows: list[dict] = []
    total: int | None = None
    code = "PARSE"
    for b in blocks:
        if not isinstance(b, dict):
            continue
        for h in b.get("head") or []:
            if isinstance(h, dict) and "list_total_count" in h:
                try:
                    total = int(h["list_total_count"])
                except (TypeError, ValueError):
                    total = None
            if isinstance(h, dict) and isinstance(h.get("RESULT"), dict):
                code = str(h["RESULT"].get("CODE") or "PARSE")
        if isinstance(b.get("row"), list):
            rows.extend(r for r in b["row"] if isinstance(r, dict))
    return rows, total, code


def normalize_row(raw: dict, spec: dict, today: date) -> dict | None:
    """seoul_reserve.json 과 같은 스키마의 항목 하나. 필수값(제목/https 링크)이 없거나 이미 종료됐으면 None."""
    f = spec.get("fields") or {}
    get = lambda k: str(raw.get(f.get(k, ""), "") or "").strip()   # noqa: E731
    title, url = get("title"), get("url")
    if not title or not url.startswith("https://"):
        return None
    start, end = _iso(get("start")), _iso(get("end"))
    rcpt_end = _iso(get("rcpt_end"))
    last = rcpt_end or end or start
    if last and last < today.isoformat():
        return None
    address = get("address")
    sid = get("id") or title
    return {
        "id": f"gg_{spec['name']}_{sid}", "title": title, "category": spec.get("label", ""), "status": "접수중",
        "start_date": start, "end_date": end or start, "rcpt_start": "", "rcpt_end": rcpt_end,
        "place": get("place"), "address": address, "region": "경기", "target": "", "tel": get("tel"), "kid_score": 0,
        "fee_text": get("fee"), "fee_type": "unknown", "is_free": False, "fee_min": None, "fee_max": None,
        "reservable": True, "reserve_url": url, "source": SOURCE_NAME,
        "link": "https://map.naver.com/p/search/" + quote(address or title),
    }


def fetch_service(spec: dict, key: str) -> tuple[list[dict], bool]:
    """(rows, complete). complete=False 면 호출 실패/오류 코드가 있었다는 뜻 - 호출자가 기존 파일을 유지한다."""
    rows: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE_URL}/{spec['name']}?KEY={key}&Type=json&pIndex={page}&pSize={PAGE}"
        try:
            data = requests.get(url, timeout=TIMEOUT).json()
        except Exception as e:   # 키가 URL 에 있으므로 메시지는 남기지 않는다
            logger.error("경기 API 호출 실패 (%s, %s)", spec["name"], type(e).__name__)
            return rows, False
        part, total, code = parse_response(data, spec["name"])
        if code == "INFO-200":
            break
        if code != "INFO-000":
            logger.error("경기 API 오류 코드 (%s, %s)", spec["name"], code)
            return rows, False
        rows.extend(part)
        if total is None or len(rows) >= total:
            break
    return rows, True


def _existing_count() -> int:
    try:
        return int(json.loads(OUTPUT_PATH.read_text(encoding="utf-8")).get("count", 0))
    except (OSError, ValueError, TypeError):
        return 0


def run(dry_run: bool = False) -> "os.PathLike | dict | None":
    try:
        if not SERVICES:
            logger.info("경기 공공서비스예약: 등록된 서비스 없음 - 건너뜀 (스펙 확정 전 Draft)")
            return None
        key = os.getenv("GG_DATA_KEY", "").strip()
        if not key:
            logger.error("GG_DATA_KEY 없음 - 경기 공공서비스예약 수집 건너뜀")
            return None
        today = date.today()
        items: list[dict] = []
        complete = True
        seen: set[str] = set()
        for spec in SERVICES:
            rows, ok = fetch_service(spec, key)
            complete = complete and ok
            for r in rows:
                it = normalize_row(r, spec, today)
                if it and it["id"] not in seen:
                    seen.add(it["id"]); items.append(it)
        items.sort(key=lambda i: (i["rcpt_end"] or i["end_date"] or "9999", i["title"]))
        if dry_run:
            return {"complete": complete, "kept": len(items), "examples": items[:6]}
        if not complete or not items:
            logger.error("경기 공공서비스예약 응답 불완전 또는 0건 - 기존 파일 유지")
            return None
        prev = _existing_count()
        if prev >= MIN_PREV_FOR_DROP_GUARD and len(items) < prev * MIN_KEEP_RATIO:
            logger.error("경기 공공서비스예약 %d건 -> %d건으로 급감 - 기존 파일 유지", prev, len(items))
            return None
        payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "source": SOURCE_NAME, "count": len(items), "items": items}
        tmp = OUTPUT_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(OUTPUT_PATH)
        logger.info("경기 공공서비스예약 JSON 생성 (%d건): %s", len(items), OUTPUT_PATH)
        return OUTPUT_PATH
    except Exception as e:
        logger.error("경기 공공서비스예약 수집 오류 - 무시하고 계속 진행 (%s)", type(e).__name__)
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = run(dry_run=a.dry_run)
    if isinstance(res, dict):
        print(json.dumps(res, ensure_ascii=False, indent=2))
