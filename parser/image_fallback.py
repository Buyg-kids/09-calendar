"""[Agent 2.5: 이미지 폴백] 인스타그램에서 대표 이미지를 못 가져온 공구에 대해
네이버 이미지 검색 API로 대표 썸네일을 채워 넣는다.

.env에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 없으면 조용히 스킵한다 - 이미지가
없는 카드는 프런트(view_page.html)에서 카테고리별 플레이스홀더로 대체되므로 이
단계가 없어도 파이프라인은 끝까지 동작한다.

네이버 클라우드 플랫폼(NCP) NAVER API HUB 발급 방법:
  1. https://www.ncloud.com 콘솔 > All Services > Application Services > NAVER API HUB
     에서 애플리케이션 등록 후 "이미지" 검색 API 사용 설정
  2. 발급된 Client ID / Secret을 .env에 NAVER_CLIENT_ID=, NAVER_CLIENT_SECRET= 로 저장

일반 개발자센터(openapi.naver.com)의 레거시 검색 API가 아니라 NCP API HUB
기준이라 인증 헤더가 다르다 (X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY,
엔드포인트도 naverapihub.apigw.ntruss.com). 응답에 가격/쇼핑몰 정보는 없고
검색 결과 중 대표 이미지 1장의 썸네일 URL만 가져온다.

실행:
    python -m parser.image_fallback
"""
from __future__ import annotations

import logging
import re
import time

import requests

import gonggu_db
from config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET

logger = logging.getLogger(__name__)

NAVER_IMAGE_API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/image"
_TAG_RE = re.compile(r"</?b>")  # 네이버 검색 결과의 강조 태그
_PAREN_RE = re.compile(r"\(.*?\)")
_REQUEST_INTERVAL_SEC = 0.2  # 네이버 API 호출 속도 여유


def _build_query(row: dict) -> str:
    # 괄호 속 세부모델 나열(예: "(빌리 프로, 브릭 프로, 노바)")은 검색어로는
    # 잡음이 되기 쉬우므로 브랜드 + 대표품목명만 사용한다. product_name이 이미
    # "브랜드 대표품목명" 형식(파서 프롬프트 규칙)이라 브랜드가 중복되기 쉬우므로
    # name에 브랜드가 이미 포함돼 있으면 브랜드를 앞에 다시 붙이지 않는다.
    name = _PAREN_RE.sub("", row.get("product_name") or "").strip()
    brand = (row.get("brand") or "").strip()
    if brand and brand not in name:
        query = f"{brand} {name}".strip()
    else:
        query = name
    return (query or row.get("product_name") or "")[:100]


def _search_thumbnail(query: str) -> str:
    # NCP API HUB 인증 헤더 (레거시 개발자센터의 X-Naver-Client-Id와는 다름).
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
    }
    # 이미지 검색 API는 쇼핑 검색과 응답 스키마가 달라 "image" 필드가 없다.
    # thumbnail(네이버가 직접 서빙하는 축소판, 안정적으로 임베드 가능)을 우선
    # 쓰고, 없으면 link(원본 출처 이미지 URL)로 폴백한다.
    params = {"query": query, "display": 1, "sort": "sim", "filter": "all"}
    resp = requests.get(NAVER_IMAGE_API_URL, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return ""
    image_url = items[0].get("thumbnail") or items[0].get("link") or ""
    return _TAG_RE.sub("", image_url)


def run() -> dict:
    stats = {"checked": 0, "filled": 0, "failed": 0}

    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        logger.info("NAVER_CLIENT_ID/NAVER_CLIENT_SECRET 미설정 - 이미지 폴백 단계 스킵")
        return stats

    rows = gonggu_db.list_missing_images()
    stats["checked"] = len(rows)
    if not rows:
        logger.info("이미지 누락 행 없음 - 이미지 폴백 스킵")
        return stats

    logger.info("이미지 누락 %d건에 대해 네이버 이미지 검색 폴백 시도", len(rows))

    for row in rows:
        query = _build_query(row)
        if not query:
            continue
        try:
            image_url = _search_thumbnail(query)
        except Exception:
            logger.exception("[id=%s] 네이버 이미지 검색 실패: %s", row["id"], query)
            stats["failed"] += 1
            time.sleep(_REQUEST_INTERVAL_SEC)
            continue

        if image_url:
            gonggu_db.update_image_url(row["id"], image_url)
            stats["filled"] += 1
            logger.info("[id=%s] 이미지 폴백 채움: '%s' -> %s", row["id"], query, image_url[:80])

        time.sleep(_REQUEST_INTERVAL_SEC)

    logger.info(
        "이미지 폴백 완료. 검사 %d건 / 채움 %d건 / 실패 %d건",
        stats["checked"], stats["filled"], stats["failed"],
    )
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    gonggu_db.init_db()
    run()
