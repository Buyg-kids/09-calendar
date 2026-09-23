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
import urllib.parse

import requests

import gonggu_db
from config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET

logger = logging.getLogger(__name__)

NAVER_IMAGE_API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/image"
_TAG_RE = re.compile(r"</?b>")  # 네이버 검색 결과의 강조 태그
_PAREN_RE = re.compile(r"\(.*?\)")
_REQUEST_INTERVAL_SEC = 0.2  # 네이버 API 호출 속도 여유

# 2026-09-23 사고: @_borahae '슈로스 놀이매트' 카드에 완전히 무관한 로봇 애니메이션
# 이미지가 떴다 - 원인은 네이버 이미지 검색이 "슈로스 놀이매트"라는 좁은 쿼리에
# sort=sim으로 상위 1건만 신뢰했는데, 그 1건이 디시인사이드 갤러리 게시물의
# 첨부 이미지였다(품목과 무관, 단순 텍스트 유사도 매칭). 커뮤니티/게시판 사이트는
# 이미지가 게시물 본문과 무관한 경우가 매우 흔해 제품 썸네일로 신뢰할 수 없으므로,
# 이런 출처는 후보에서 제외하고 다음 순위 결과를 본다 (전부 제외되면 빈 문자열을
# 반환해 프런트가 카테고리 플레이스홀더를 쓰게 한다 - 무관한 사진보다 안전하다).
_BAD_IMAGE_DOMAINS = {
    # 커뮤니티/게시판 - 게시물 첨부 이미지가 본문 텍스트와 무관한 경우가 매우 흔함
    "dcinside.com", "gall.dcinside.com", "ilbe.com", "todayhumor.co.kr", "humoruniv.com",
    "fmkorea.com", "ruliweb.com", "clien.net", "ppomppu.co.kr", "dogdrip.net",
    "mlbpark.donga.com", "instiz.net", "theqoo.net", "bobaedream.co.kr",
    "pann.nate.com", "natepann.com", "82cook.com",
    # 동영상 플랫폼 - 정적 상품 사진이 아니라 영상 스틸컷이라 애초에 콘텐츠 종류가
    # 안 맞고(자세/텍스트 오버레이 등), '슈로스 놀이매트' 사고에서도 상위 결과가
    # 전혀 무관한 애니메이션 유튜브 썸네일이었다.
    "youtube.com", "ytimg.com", "vimeo.com", "vimeocdn.com", "tiktokcdn.com",
}


def _source_domain(url: str) -> str:
    """네이버 썸네일 URL(search.pstatic.net/...?src=<원본 URL>)이든 원본 link든,
    실제 출처 도메인을 뽑아낸다 (썸네일은 항상 pstatic.net 도메인으로 프록시되므로
    바깥 호스트만 봐서는 출처를 알 수 없다)."""
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        src = (qs.get("src") or [None])[0]
        host = urllib.parse.urlparse(src).netloc if src else parsed.netloc
        return host.lower().split("@")[-1]  # 혹시 모를 userinfo@host 형태 방어
    except Exception:
        return ""


def _is_bad_source(*urls: str) -> bool:
    for url in urls:
        host = _source_domain(url or "")
        if host and any(host == d or host.endswith("." + d) for d in _BAD_IMAGE_DOMAINS):
            return True
    return False


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
    # 쓰고, 없으면 link(원본 출처 이미지 URL)로 폴백한다. 상위 1건만 보면 그
    # 1건이 커뮤니티 게시판의 무관한 이미지일 때 대체할 후보가 없으므로 5건을
    # 받아 _BAD_IMAGE_DOMAINS에 걸리는 건은 건너뛰고 다음 순위를 본다.
    params = {"query": query, "display": 5, "sort": "sim", "filter": "all"}
    resp = requests.get(NAVER_IMAGE_API_URL, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    for item in items:
        image_url = item.get("thumbnail") or item.get("link") or ""
        if not image_url or _is_bad_source(image_url, item.get("link") or ""):
            continue
        return _TAG_RE.sub("", image_url)
    return ""


_IMAGE_CHECK_TIMEOUT_SEC = 6
_CHECK_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _is_image_alive(url: str) -> bool:
    """이미지 URL이 실제로 아직 열리는지 가볍게 확인한다. 인스타그램 CDN URL은
    서명 토큰이 며칠 뒤 만료돼 403/404로 죽는데, image_url 필드 자체는 계속
    비어있지 않은 채로 남아있어 list_missing_images()로는 못 잡는다 - HEAD
    요청으로 브라우저가 겪을 실패를 미리 확인한다."""
    try:
        resp = requests.head(url, timeout=_IMAGE_CHECK_TIMEOUT_SEC, allow_redirects=True, headers=_CHECK_HEADERS)
        if resp.status_code == 405:  # 일부 서버는 HEAD를 막아둬서 GET으로 재시도
            resp = requests.get(url, timeout=_IMAGE_CHECK_TIMEOUT_SEC, stream=True, headers=_CHECK_HEADERS)
        return resp.status_code < 400
    except requests.RequestException:
        return False


def revalidate_rows(rows: list[dict]) -> dict:
    """view.html/index.html에 실제로 노출될 (병합 후) 행 목록을 받아, image_url이
    있어도 죽어있으면(만료된 인스타 CDN 서명 토큰 등) 네이버 이미지 검색으로
    교체한다. 2026-09-18 실사고: ariseoan '베베루트/니가드키즈4' 카드가 며칠 전
    캡처된 죽은 URL을 그대로 써서 브라우저에 기본 SVG 아이콘만 떴었다 - 가장
    최근 재수집분을 우선하도록 고쳤지만(card_news._merge_duplicate_group), 그
    최신 캡처본마저 마감일이 먼 상품은 결국 만료될 수 있어 노출 직전에 실제로
    살아있는지 한 번 더 확인하는 마지막 방어선이다. DB 전체가 아니라 실제로
    화면에 나갈 병합 후 행(수백 건)에 대해서만 돌아 검사량을 최소화한다."""
    stats = {"checked": 0, "dead": 0, "replaced": 0, "still_broken": 0}
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        logger.info("NAVER_CLIENT_ID/NAVER_CLIENT_SECRET 미설정 - 이미지 재검증 스킵")
        return stats

    for row in rows:
        url = row.get("image_url") or ""
        if not url:
            continue
        stats["checked"] += 1
        if _is_image_alive(url):
            time.sleep(_REQUEST_INTERVAL_SEC)
            continue

        stats["dead"] += 1
        query = _build_query(row)
        if not query:
            time.sleep(_REQUEST_INTERVAL_SEC)
            continue
        try:
            replacement = _search_thumbnail(query)
        except Exception:
            logger.exception("[%s] 만료 이미지 재검색 실패: %s", row.get("row_id") or row.get("product_name"), query)
            stats["still_broken"] += 1
            time.sleep(_REQUEST_INTERVAL_SEC)
            continue

        if replacement:
            row["image_url"] = replacement
            stats["replaced"] += 1
            logger.info(
                "[%s] 만료된 이미지 교체: '%s' -> %s",
                row.get("row_id") or row.get("product_name"), query, replacement[:80],
            )
        else:
            # 대체 이미지를 못 찾았으면(전부 무관 도메인으로 걸러졌거나 검색 결과가
            # 없음) 죽은 URL을 그대로 두지 않고 비워서 카테고리 플레이스홀더로
            # 넘긴다. 프런트 img.onerror가 결국 같은 결과로 대체하긴 하지만,
            # 실패할 게 뻔한 네트워크 요청을 브라우저에서 굳이 한 번 더 시도하게
            # 두지 않고 여기서 명시적으로 정리한다.
            row["image_url"] = ""
            stats["still_broken"] += 1
        time.sleep(_REQUEST_INTERVAL_SEC)

    logger.info(
        "이미지 재검증 완료. 검사 %d건 / 만료 발견 %d건 / 교체 %d건 / 교체 실패 %d건",
        stats["checked"], stats["dead"], stats["replaced"], stats["still_broken"],
    )
    return stats


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
