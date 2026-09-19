"""[Agent 6: 바이럴 쇼츠 레퍼런스 수집기]

육아/살림 카테고리에서 최근 7일 업로드된 인기 쇼츠(60초 이하)를 조회수순 상위 3개
골라 제목/조회수/URL/자막을 data/viral_shorts_reference.json 에 저장한다.
generator/reels_generator.py가 이 파일을 읽어 릴스 대본 프롬프트의 참고 레퍼런스로 쓴다.

- YouTube Data API v3는 requests로 직접 호출한다 (추가 의존성 없이, 키를 헤더로 전달해
  URL/로그에 키가 남지 않게 함). 자막은 youtube-transcript-api(1.x/0.x 모두 지원).
- 자막 추출 실패 시 제목 + 설명란으로 대체한다.
- 쿼터: 키워드당 search.list 100유닛 + videos.list 1유닛 -> 하루 약 400유닛(기본 한도 10,000).
  같은 날 재실행(야간 스크립트의 Step 3 재시도 등)은 이미 오늘 수집됐으면 건너뛴다.

안전 규칙 (run_nightly_pipeline.ps1이 로그의 "Traceback"을 실패로 판정해 배포를 건너뜀):
run()은 절대 예외를 던지지 않고, 실패 시 트레이스백 없이 한 줄 로그만 남기고 None을 반환한다.
실패해도 기존 참고 파일은 덮어쓰지 않는다.

실행 (단독 테스트):
    python -m collector.youtube_shorts_collector
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from config import BASE_DIR

logger = logging.getLogger(__name__)

REFERENCE_PATH = BASE_DIR / "data" / "viral_shorts_reference.json"
KEYWORDS = ["육아템", "살림꿀팁", "육아공구", "출산준비물"]
TOP_N = 3
MAX_SHORT_SEC = 60
LOOKBACK_DAYS = 7
HTTP_TIMEOUT_SEC = 15
TRANSCRIPT_TIMEOUT_SEC = 30
TRANSCRIPT_MAX_CHARS = 1500
TOTAL_BUDGET_SEC = 150

_API = "https://www.googleapis.com/youtube/v3"
_DURATION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


class _Skip(Exception):
    """정상적인 건너뛰기 사유(쿼터 초과/API 오류 등). 메시지가 그대로 한 줄 로그가 된다."""


def _api_get(path: str, api_key: str, params: dict) -> dict:
    try:
        resp = requests.get(f"{_API}/{path}", params=params, headers={"x-goog-api-key": api_key}, timeout=HTTP_TIMEOUT_SEC)
    except requests.RequestException as e:
        raise _Skip(f"YouTube API 연결 실패 ({type(e).__name__})") from None
    if resp.status_code != 200:
        try:
            reason = resp.json()["error"]["errors"][0]["reason"]
        except Exception:
            reason = ""
        if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            raise _Skip("YouTube API 쿼터 초과")
        raise _Skip(f"YouTube API 오류 HTTP {resp.status_code} {reason}".strip())
    return resp.json()


def _duration_sec(iso: str) -> int | None:
    m = _DURATION_RE.match(iso or "")
    if not m:
        return None
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def _search_candidates(api_key: str, deadline: float) -> dict[str, dict]:
    """키워드별 최근 7일 조회수순 검색 -> videos.list로 길이/조회수 확정 (60초 이하만)."""
    published_after = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids: list[str] = []
    for kw in KEYWORDS:
        if time.monotonic() > deadline:
            break
        data = _api_get("search", api_key, {
            "part": "id", "q": kw, "type": "video", "order": "viewCount",
            "videoDuration": "short", "publishedAfter": published_after,
            "regionCode": "KR", "relevanceLanguage": "ko", "maxResults": 25,
        })
        ids += [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]
    ids = list(dict.fromkeys(ids))
    videos: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        data = _api_get("videos", api_key, {"part": "snippet,contentDetails,statistics", "id": ",".join(ids[i:i + 50])})
        for v in data.get("items", []):
            sec = _duration_sec(v.get("contentDetails", {}).get("duration", ""))
            # 세로 여부는 API로 알 수 없어 '60초 이하'를 쇼츠 판정 기준으로 쓴다
            if sec is None or sec > MAX_SHORT_SEC:
                continue
            videos[v["id"]] = {
                "video_id": v["id"],
                "title": v["snippet"].get("title", "").strip(),
                "description": v["snippet"].get("description", "").strip(),
                "views": int(v.get("statistics", {}).get("viewCount", 0) or 0),
            }
    return videos


def _fetch_transcript(video_id: str) -> str:
    """한국어(자동생성 포함) 자막 텍스트. 실패/없음/타임아웃이면 빈 문자열."""
    result: list[str] = []

    def _work() -> None:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi

            langs = ["ko", "ko-KR"]
            if hasattr(YouTubeTranscriptApi, "fetch"):  # 1.x
                texts = [s.text for s in YouTubeTranscriptApi().fetch(video_id, languages=langs)]
            else:  # 0.x
                texts = [s["text"] for s in YouTubeTranscriptApi.get_transcript(video_id, languages=langs)]
            result.append(" ".join(t.strip() for t in texts if t.strip()))
        except Exception:
            pass

    t = threading.Thread(target=_work, daemon=True)  # 자막 서버가 멈춰도 파이프라인이 매달리지 않게
    t.start()
    t.join(TRANSCRIPT_TIMEOUT_SEC)
    return (result[0] if result else "")[:TRANSCRIPT_MAX_CHARS]


def _already_collected_today() -> bool:
    try:
        return json.loads(REFERENCE_PATH.read_text(encoding="utf-8")).get("updated_at") == date.today().isoformat()
    except Exception:
        return False


def run(force: bool = False) -> Path | None:
    """성공 시 저장 경로, 스킵/실패 시 None. 예외/트레이스백은 절대 밖으로 내보내지 않는다."""
    try:
        api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
        if not api_key:
            logger.info("쇼츠 레퍼런스 수집 스킵: YOUTUBE_API_KEY 없음")
            return None
        if not force and _already_collected_today():
            logger.info("쇼츠 레퍼런스 수집 스킵: 오늘 이미 수집됨")
            return None

        deadline = time.monotonic() + TOTAL_BUDGET_SEC
        videos = _search_candidates(api_key, deadline)
        top = sorted(videos.values(), key=lambda v: v["views"], reverse=True)[:TOP_N]
        if not top:
            logger.warning("쇼츠 레퍼런스 수집: 조건에 맞는 쇼츠가 없음 - 기존 파일 유지")
            return None

        references = []
        for v in top:
            transcript = _fetch_transcript(v["video_id"]) if time.monotonic() < deadline else ""
            source = "transcript"
            if not transcript:
                source = "title_description"
                transcript = f"{v['title']}\n{v['description']}".strip()[:TRANSCRIPT_MAX_CHARS]
            references.append({
                "title": v["title"],
                "transcript": transcript,
                "views": str(v["views"]),
                "url": f"https://www.youtube.com/shorts/{v['video_id']}",
                "source": source,
            })

        REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"updated_at": date.today().isoformat(), "references": references}
        tmp = REFERENCE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(REFERENCE_PATH)
        logger.info("쇼츠 레퍼런스 %d개 저장 (자막 %d개): %s", len(references), sum(r["source"] == "transcript" for r in references), REFERENCE_PATH)
        return REFERENCE_PATH
    except _Skip as e:
        logger.warning("쇼츠 레퍼런스 수집 스킵: %s", e)
        return None
    except Exception as e:
        # logger.exception 금지 (야간 스크립트가 "Traceback"을 실패로 판정). API 키가 들어갈 수 있는 URL/메시지도 싣지 않는다.
        logger.error("쇼츠 레퍼런스 수집 실패 - 무시하고 계속 진행 (%s)", type(e).__name__)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = run(force=True)
    print(out if out else "레퍼런스 파일 갱신 안 됨 (로그 확인)")
