"""[Agent 2.5: 이미지 보관] 인스타그램 썸네일을 Cloudinary로 옮겨 영구 URL로 바꾸고,
보관 기간이 지난 공구의 이미지는 Cloudinary에서 삭제한다.

왜 필요한가:
- 인스타 CDN 이미지 URL은 서명 토큰이 며칠 뒤 만료(403)된다. 예전엔 노출 직전에 죽은
  URL을 네이버 이미지 검색으로 바꿔 끼웠는데, 상품명만으로 검색하다 보니 엉뚱한 쇼핑몰/
  성인 의류 사진이 붙는 사고가 반복됐다(2026-09-23 '슈로스 놀이매트', 09-29 '실내복').
  그래서 외부 검색 폴백은 완전히 제거하고, 원본 인스타 이미지를 수집 직후 Cloudinary에
  올려 두는 방식으로 바꿨다. 원본을 못 구하면 무조건 Buyg 플레이스홀더를 쓴다.
- Cloudinary 무료 플랜(월 25크레딧) 안에서 영구 운영하려고, 종료일(없으면 시작일)
  기준 IMAGE_RETENTION_DAYS가 지난 공구는 이미지만 destroy하고 마감 플레이스홀더로
  바꾼다. 상품명/가격/날짜/인플루언서 등 텍스트는 시세 검색 히스토리용으로 그대로 둔다.

중복 업로드 방지: public_id를 게시물 shortcode로 고정(buyg/posts/<shortcode>)한다. 한
게시물에서 상품이 여러 개 나와도 이미지는 한 번만 올리고, 이미 public_id가 있는 게시물은
다시 올리지 않는다.

주의: 로그에 "Traceback"이 찍히면 야간 스크립트가 배포를 건너뛰므로 logger.exception 금지.

실행:
    python -m parser.image_store
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import urlparse

import requests

import gonggu_db
from config import (
    CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET, CLOUDINARY_CLOUD_NAME, CLOUDINARY_FOLDER,
    IMAGE_ENDED_PLACEHOLDER_URL, IMAGE_PLACEHOLDER_URL, IMAGE_RETENTION_DAYS, RAW_COLLECTED_PATH,
)

logger = logging.getLogger(__name__)

_SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")
_DOWNLOAD_HEADERS = {"User-Agent": "Mozilla/5.0"}
_DOWNLOAD_TIMEOUT_SEC = 15
_REQUEST_INTERVAL_SEC = 0.1
_MAX_CONSECUTIVE_UPLOAD_ERRORS = 5  # 인증/쿼터 문제면 수천 건을 헛돌지 않고 이번 회차는 중단


def _configure() -> bool:
    if not (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET):
        return False
    import cloudinary
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME, api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET, secure=True,
    )
    return True


def _shortcode(post_url: str) -> str:
    m = _SHORTCODE_RE.search(post_url or "")
    return m.group(1) if m else ""


def _is_insta_cdn(url: str) -> bool:
    host = urlparse(url or "").netloc
    return "cdninstagram.com" in host or "fbcdn.net" in host


def _effective_end(row: dict) -> str:
    return row.get("end_date") or row.get("start_date") or ""


def _fresh_image_map() -> dict[str, str]:
    """오늘 밤 수집분(raw_collected.json)의 shortcode -> 방금 캡처한 인스타 이미지 URL.
    이미 처리한 캡션은 processed_blobs 캐시 때문에 다시 파싱/upsert되지 않아 DB의
    image_url이 며칠 전 (만료된) URL로 남아 있을 수 있다 - 그래서 업로드 원본은 DB보다
    이 최신 캡처본을 먼저 쓴다."""
    try:
        entries = json.loads(RAW_COLLECTED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    fresh: dict[str, str] = {}
    for entry in entries:
        for cap in (entry.get("instagram") or {}).get("captions") or []:
            if cap.get("shortcode") and cap.get("image_url"):
                fresh[cap["shortcode"]] = cap["image_url"]
    return fresh


def _download(url: str, fails: dict | None = None) -> bytes | None:
    """이미지를 받아 bytes로 반환. 실패하면 None - fails(dict)가 있으면 원인별로 센다
    (403/404/other_status/bad_type/exception). 로그엔 요약만 남긴다(URL은 서명 토큰이라 찍지 않음)."""
    def _count(reason: str) -> None:
        if fails is not None:
            fails[reason] = fails.get(reason, 0) + 1

    try:
        resp = requests.get(url, headers=_DOWNLOAD_HEADERS, timeout=_DOWNLOAD_TIMEOUT_SEC)
    except requests.RequestException:
        _count("exception")
        return None
    if resp.status_code != 200:
        _count(str(resp.status_code) if resp.status_code in (403, 404) else "other_status")
        return None
    if not resp.headers.get("Content-Type", "").startswith("image/"):
        _count("bad_type")
        return None
    return resp.content


def _upload(data: bytes, shortcode: str) -> tuple[str, str]:
    """Cloudinary에 올리고 (노출용 URL, public_id)를 반환한다.
    - 저장 시점 변환(incoming): 너비 400px 제한 -> 원본 1080px을 그대로 쌓지 않아 스토리지 절약
    - 노출 URL: f_auto(브라우저별 WebP/AVIF 자동) + q_auto(자동 압축)"""
    import cloudinary
    import cloudinary.uploader
    result = cloudinary.uploader.upload(
        io.BytesIO(data),
        public_id=f"{CLOUDINARY_FOLDER}/{shortcode}",
        overwrite=True,
        resource_type="image",
        transformation=[{"width": 400, "crop": "limit"}],
    )
    public_id = result["public_id"]
    url = cloudinary.CloudinaryImage(public_id).build_url(
        fetch_format="auto", quality="auto", version=result.get("version"), secure=True,
    )
    return url, public_id


def cleanup_expired(rows: list[dict], today: date) -> dict:
    """보관 기간이 지난 공구의 Cloudinary 이미지를 삭제하고 마감 플레이스홀더로 바꾼다.
    같은 게시물(public_id)을 아직 보관 기간 안의 다른 행이 쓰고 있으면 삭제하지 않는다."""
    import cloudinary.uploader

    stats = {"expired_rows": 0, "destroyed": 0, "destroy_failed": 0}
    cutoff = (today - timedelta(days=IMAGE_RETENTION_DAYS)).isoformat()
    expired = [r for r in rows if _effective_end(r) and _effective_end(r) < cutoff]
    live_public_ids = {
        r["cloudinary_public_id"] for r in rows
        if r.get("cloudinary_public_id") and not (_effective_end(r) and _effective_end(r) < cutoff)
    }

    by_public_id: dict[str, list[dict]] = defaultdict(list)
    plain: list[int] = []
    for r in expired:
        if r.get("cloudinary_public_id"):
            by_public_id[r["cloudinary_public_id"]].append(r)
        elif r.get("image_url") != IMAGE_ENDED_PLACEHOLDER_URL:
            plain.append(r["id"])

    for public_id, group in by_public_id.items():
        ids = [r["id"] for r in group]
        if public_id in live_public_ids:
            # 이미지는 아직 쓰이는 중 - 이 행들만 마감 표시로 바꾸고 파일은 남긴다
            gonggu_db.set_image(ids, IMAGE_ENDED_PLACEHOLDER_URL, "")
            stats["expired_rows"] += len(ids)
            continue
        try:
            res = cloudinary.uploader.destroy(public_id, invalidate=True)
        except Exception as e:
            # public_id를 남겨 두면 다음 밤에 다시 시도된다
            logger.error("[이미지정리] %s 삭제 실패 (%s: %s)", public_id, type(e).__name__, str(e)[:200])
            stats["destroy_failed"] += 1
            continue
        if res.get("result") not in ("ok", "not found"):
            logger.error("[이미지정리] %s 삭제 응답 이상: %s", public_id, res)
            stats["destroy_failed"] += 1
            continue
        gonggu_db.set_image(ids, IMAGE_ENDED_PLACEHOLDER_URL, "")
        stats["destroyed"] += 1
        stats["expired_rows"] += len(ids)
        time.sleep(_REQUEST_INTERVAL_SEC)

    if plain:
        gonggu_db.set_image(plain, IMAGE_ENDED_PLACEHOLDER_URL, "")
        stats["expired_rows"] += len(plain)
    return stats


def _brand_fallback(today: date, fresh_codes: set[str]) -> int:
    """끝까지 이미지를 못 구한(플레이스홀더) 행에, 같은 인플루언서 + 같은 브랜드로 이미 올라간 다른
    행의 이미지를 빌려 쓴다. 같은 상품이 아니라 "같은 브랜드" 이미지이므로 brand가 비어 있으면 건너뛴다.
    - 빌린 행은 같은 cloudinary_public_id를 가리킨다. cleanup_expired는 보관기간 안의 다른 행이 쓰는
      public_id는 destroy하지 않으므로(모든 행이 만료돼야 삭제) 공유해도 안전하다.
    - 오늘 밤 새로 캡처된 게시물(fresh_codes)은 다운로드 실패가 일시적일 수 있어 건너뛴다(다음 밤 재시도).
    반환: 구제한 행 수."""
    cutoff = (today - timedelta(days=IMAGE_RETENTION_DAYS)).isoformat()
    rows = [r for r in gonggu_db.list_image_rows() if not (_effective_end(r) and _effective_end(r) < cutoff)]
    donors: dict[tuple[str, str], dict] = {}
    for r in rows:
        brand = (r.get("brand") or "").strip()
        if brand and r.get("cloudinary_public_id") and r.get("image_url") not in (IMAGE_PLACEHOLDER_URL, IMAGE_ENDED_PLACEHOLDER_URL):
            key = (r["influencer_name"], brand)
            if key not in donors or (r.get("start_date") or "") > (donors[key].get("start_date") or ""):
                donors[key] = r
    reused = 0
    for r in rows:
        brand = (r.get("brand") or "").strip()
        if not brand or r.get("image_url") != IMAGE_PLACEHOLDER_URL or r.get("cloudinary_public_id"):
            continue
        if _shortcode(r.get("post_url") or "") in fresh_codes:
            continue
        donor = donors.get((r["influencer_name"], brand))
        if donor:
            gonggu_db.set_image([r["id"]], donor["image_url"], donor["cloudinary_public_id"])
            reused += 1
    return reused


def sync_images(rows: list[dict], today: date) -> dict:
    """보관 기간 안의 공구 중 아직 Cloudinary로 안 옮긴 게시물 이미지를 업로드한다."""
    stats = {"uploaded": 0, "reused": 0, "placeholder": 0, "upload_failed": 0, "aborted": False,
             "no_source": 0, "brand_reused": 0, "download_failed": 0, "no_candidate": 0, "dl_fails": {}}
    cutoff = (today - timedelta(days=IMAGE_RETENTION_DAYS)).isoformat()
    active = [r for r in rows if not (_effective_end(r) and _effective_end(r) < cutoff)]
    fresh = _fresh_image_map()

    by_code: dict[str, list[dict]] = defaultdict(list)
    no_source: list[int] = []
    for r in active:
        code = _shortcode(r.get("post_url") or "")
        if code:
            by_code[code].append(r)
        elif not r.get("cloudinary_public_id") and r.get("image_url") != IMAGE_PLACEHOLDER_URL:
            # 멀티링크/프로필 소개글에서 나온 공구 - 원본 게시물 이미지가 없다
            no_source.append(r["id"])

    if no_source:
        gonggu_db.set_image(no_source, IMAGE_PLACEHOLDER_URL, "")
        stats["placeholder"] += len(no_source)
    # 원본 게시물이 없어 이미지를 구할 수 없는 행 수(이미 플레이스홀더인 행 포함, 브랜드 폴백 전 기준)
    stats["no_source"] = sum(1 for r in active if not _shortcode(r.get("post_url") or "") and not r.get("cloudinary_public_id"))

    consecutive_errors = 0
    for code, group in by_code.items():
        done = next((r for r in group if r.get("cloudinary_public_id")), None)
        if done:
            pending = [r["id"] for r in group if not r.get("cloudinary_public_id")]
            if pending:  # 같은 게시물의 새 상품 행 - 이미 올린 이미지를 재사용
                gonggu_db.set_image(pending, done["image_url"], done["cloudinary_public_id"])
                stats["reused"] += len(pending)
            continue

        candidates = [fresh.get(code, "")] + [r.get("image_url") or "" for r in group]
        candidates = list(dict.fromkeys(u for u in candidates if _is_insta_cdn(u)))
        data = None
        for url in candidates:
            data = _download(url, stats["dl_fails"])
            if data:
                break

        ids = [r["id"] for r in group]
        if not data:
            if candidates:
                stats["download_failed"] += 1   # 시도했는데 못 받은 게시물
            else:
                stats["no_candidate"] += 1      # 시도할 인스타 URL 자체가 없음(만료돼 플레이스홀더로 바뀐 뒤 재수집 안 됨)
            stale = [r["id"] for r in group if r.get("image_url") != IMAGE_PLACEHOLDER_URL]
            if stale:
                gonggu_db.set_image(stale, IMAGE_PLACEHOLDER_URL, "")
                stats["placeholder"] += len(stale)
            continue

        try:
            url, public_id = _upload(data, code)
        except Exception as e:
            logger.error("[이미지보관] %s 업로드 실패 (%s: %s)", code, type(e).__name__, str(e)[:200])
            stats["upload_failed"] += 1
            consecutive_errors += 1
            if consecutive_errors >= _MAX_CONSECUTIVE_UPLOAD_ERRORS:
                logger.error("[이미지보관] 업로드 %d회 연속 실패 - 인증/쿼터 문제로 보고 이번 회차 중단",
                             consecutive_errors)
                stats["aborted"] = True
                break
            continue
        consecutive_errors = 0
        gonggu_db.set_image(ids, url, public_id)
        stats["uploaded"] += 1
        time.sleep(_REQUEST_INTERVAL_SEC)

    stats["brand_reused"] = _brand_fallback(today, set(fresh))
    return stats


def run(today: date | None = None) -> dict:
    if not _configure():
        logger.info("CLOUDINARY_* 환경변수 미설정 - 이미지 보관 단계 스킵")
        return {}
    gonggu_db.init_db()
    today = today or date.today()

    stats = cleanup_expired(gonggu_db.list_image_rows(), today)
    stats.update(sync_images(gonggu_db.list_image_rows(), today))
    logger.info(
        "[이미지보관] 완료. 업로드 %d건 / 기존 이미지 재사용 %d행 / 플레이스홀더 %d행 / 업로드 실패 %d건 / "
        "보관기간 만료 %d행(Cloudinary 삭제 %d건, 삭제 실패 %d건)%s",
        stats["uploaded"], stats["reused"], stats["placeholder"], stats["upload_failed"],
        stats["expired_rows"], stats["destroyed"], stats["destroy_failed"],
        " - 연속 실패로 중단됨" if stats["aborted"] else "",
    )
    f = stats.get("dl_fails") or {}
    logger.info(
        "[이미지보관] 다운로드 실패 %d건 (403: %d, 404: %d, 기타: %d, 예외: %d, 비이미지: %d) / 실패 게시물 %d개 / "
        "재시도할 URL 없음 %d개 / 원본 없음 %d건 / 브랜드 재사용 %d건",
        sum(f.values()), f.get("403", 0), f.get("404", 0), f.get("other_status", 0), f.get("exception", 0),
        f.get("bad_type", 0), stats.get("download_failed", 0), stats.get("no_candidate", 0),
        stats.get("no_source", 0), stats.get("brand_reused", 0),
    )
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
