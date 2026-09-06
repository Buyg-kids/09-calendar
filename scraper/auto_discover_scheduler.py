"""[야간 해시태그 자동 탐색 스케줄러]

매일 00:00~10:00에만 활성화되어 인스타그램 해시태그(#육아공구, #유아식공구 등)
게시물 작성자를 찾고, 프로필을 검증해 조건에 맞는 계정만 data/targets.json에
추가한다. data/insta_cookies.json의 로그인 세션을 사용한다 (login_helper.py로
미리 저장해둬야 함).

2026-09-02~09-06 5일간의 시범 운영을 거쳐, 2026-09-06부터는 Windows 작업
스케줄러(작업명 InstaAutoPipeline_Daily)가 매일 00:00에 이 스크립트(--tonight)
-> 15분 쿨다운 -> run_manual.py 순으로 이어 실행하는 30일 상시 운용 체제로
전환됐다 (run_nightly_pipeline.ps1 참고). --tonight 모드는 실행 시점의 날짜만
보고 활성 시간대를 판단하므로 별도 종료일 설정 없이 계속 재사용된다.

계정 차단 위험을 낮추기 위한 3대 안전장치:
  1. 속도 제한: 시간당 6~8개 계정만 검증 (검증 사이 7~10분 대기) -> 하루 약 60~80개
  2. 사람같은 지연: 페이지 이동/스크롤/클릭마다 4~9초 무작위 딜레이
  3. 프로필 1차 필터: 멀티링크(인포크/litt.ly/linktr.ee/lit.link) 보유 또는
     bio에 '공구'/'마켓'/'비즈니스'/'유아식'/'육아' 키워드가 있어야 저장
  4. 네거티브 키워드 필터: bio에 성인 뷰티/패션/비육아 상업 키워드가 있고
     육아 키워드 비중이 그보다 낮으면 즉시 제외 (로그: "[Negative Filtered: ...]")

[VIP 소스 전략] @we09.lab처럼 여러 인플루언서 공구를 매일 정리해 올리는
큐레이션/아카이브 계정은 데이터 효율이 압도적으로 높다 (계정 1개 = 실제
활동 계정 수십 개 소스). 매일 밤 활성 시간대 진입 직후 해시태그 탐색보다
먼저 scraper/curator.py로 이런 계정을 스캔해 캡션 속 '상품명 | 핸들' 목록을
targets.json에 즉시 신규 등록하고(scraper/collector.py는 이 계정들을 최우선
순위로 수집한다), bio에 "공구연구소"/"아카이브" 등의 문구가 있으면 자동으로
is_curator=true로 표시해 다음날 밤부터 같은 우선 처리를 받도록 한다.

주의: 이 스크립트는 실제 로그인된 인스타그램 계정으로 해시태그 페이지와
다수의 프로필을 자동 방문한다. 위 안전장치를 적용해도 인스타그램이 이 패턴을
자동화로 감지해 일시 조치(액션 블록)를 걸 가능성은 남아 있다 - 완전히 없앨
수는 없다. 며칠간 사람이 지켜보지 않는 무인 실행이므로, 처음에는
`--once`(한 사이클만 즉시 실행)로 정상 동작을 확인한 뒤 무인 실행 여부를
직접 판단하는 것을 권장한다.

실행:
    python -m scraper.auto_discover_scheduler          # 예정된 5일 스케줄대로 계속 실행
    python -m scraper.auto_discover_scheduler --once   # 지금 즉시 한 계정만 검증하고 종료 (테스트용)
    python -m scraper.auto_discover_scheduler --duration 5h   # 활성 시간대 무시하고
                                                                # 지금부터 정확히 5시간만 실행 후 종료
                                                                # (수집이 밀린 날 낮 시간대 보충용, 1회성)
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import date, datetime, timedelta

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from config import INSTA_COOKIES_PATH, TARGETS_PATH
from scraper.curator import run_curator_scan

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 스케줄 설정
# ---------------------------------------------------------------------------
RUN_START_DATE = date(2026, 9, 2)
RUN_END_DATE = date(2026, 9, 6)
ACTIVE_START_HOUR = 0
ACTIVE_END_HOUR = 10

HASHTAGS = ["육아공구", "유아식공구", "아기간식공구", "아기식판공구", "키즈가구공구", "유아교구공구"]

# [VIP 소스 전략 3] @we09.lab처럼 여러 인플루언서 공구를 매일 정리해 올리는
# '큐레이션/아카이브' 계정을 찾기 위한 전용 해시태그. 일반 해시태그와 같은
# 방식(게시물 작성자 -> 프로필 검증)으로 로테이션에 섞어 돈다 - 새로운 스캔
# 경로를 추가하는 게 아니라 기존 안전장치를 그대로 타는 추가 태그일 뿐이다.
CURATOR_HASHTAGS = ["공구연구소", "공구아카이브", "육아공구모음"]
HASHTAGS = HASHTAGS + CURATOR_HASHTAGS

# 시간당 6~8개 -> 계정 하나 처리 후 7~10분 대기
WAIT_MIN_SEC_BETWEEN_ACCOUNTS = 7 * 60
WAIT_MAX_SEC_BETWEEN_ACCOUNTS = 10 * 60

JITTER_MIN_SEC = 4.0
JITTER_MAX_SEC = 9.0

HASHTAG_POSTS_PER_SCAN = 12  # 해시태그 한 번 스캔할 때 후보로 가져올 게시물 수

BIO_KEYWORDS = ["공구", "마켓", "비즈니스", "유아식", "육아"]

# 네거티브 키워드 필터: 성인 뷰티/패션/비육아 상업 계정을 걸러낸다.
# 이 키워드가 있다고 무조건 제외하는 게 아니라, 육아 키워드 매치 수가 이걸
# 못 넘으면(비중이 낮으면) 제외한다 - 육아템도 팔지만 부업으로 잡화도 겸하는
# 계정까지 과도하게 걸러내지 않기 위함.
NEGATIVE_KEYWORDS = {
    "뷰티/화장품": ["다이어트", "효소", "붓기차", "콜라겐", "이너뷰티", "앰플", "쿠션", "리프팅", "스킨케어"],
    "성인 패션/잡화": ["여성복", "오피스룩", "명품", "가방마켓", "귀걸이", "쥬얼리"],
    "기타 비육아 상업": ["부업", "재테크", "수익인증", "쇼핑몰창업"],
}
PARENTING_KEYWORDS = ["이유식", "유아", "아동", "키즈", "베이비", "출산", "돌준맘", "육아", "아기"]

# [VIP 소스 전략 3] bio에 이런 문구가 있으면 '개인 인플루언서'가 아니라 여러
# 계정의 공구를 정리해 올리는 '큐레이션/아카이브' 계정으로 보고 is_curator로
# 표시한다 (@we09.lab 실측 bio: "공구연구소 | 공박사 | 육아필수템",
# "매일 업데이트되는 육아 공구 아카이브", "보기 쉽게, 찾기 쉽게, 놓치지 않게").
CURATOR_BIO_KEYWORDS = ["공구연구소", "공구아카이브", "육아공구모음", "매일업데이트", "한눈에모아", "모아드려요", "아카이브"]
MULTILINK_DOMAIN_MAP = {
    "inpock.co": "inpock",
    "litt.ly": "littly",
    "linktr.ee": "linktree",
    "lit.link": "litlink",
}
MULTILINK_URL_RE = re.compile(
    r"https?://[^\s]*(?:inpock\.co[^\s]*|litt\.ly/[A-Za-z0-9_.\-]+|linktr\.ee/[A-Za-z0-9_.\-]+|lit\.link/[A-Za-z0-9_.\-]+)"
)

# 인스타그램 실제 핸들 형식: 영문/숫자/마침표/밑줄, 1~30자 (공백·한글·기타 기호 불가).
# 임베드 페이지의 UI 문구("Instagram", "Log in" 등)가 핸들로 오인되는 걸 막기 위한
# 형식 검증 + 블랙리스트 (실제 야간 실행에서 @Instagram 오탐이 확인되어 추가함).
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
INVALID_AUTHOR_HANDLES = {"instagram", "log", "login", "signup", "loading", "reels", "explore"}


def _looks_like_username(candidate: str) -> bool:
    if not USERNAME_RE.match(candidate):
        return False
    if candidate.lower() in INVALID_AUTHOR_HANDLES:
        return False
    return True


def _negative_filter_reason(bio_text: str) -> str | None:
    """제외 키워드가 있고 육아 키워드 비중이 낮으면 '카테고리(키워드)' 사유를 반환,
    통과하면 None. bio_text 는 공백 제거 전 원문이어도 됨(내부에서 처리)."""
    compact = bio_text.replace(" ", "")

    neg_hits: list[tuple[str, str]] = []
    for category, keywords in NEGATIVE_KEYWORDS.items():
        for kw in keywords:
            if kw in compact:
                neg_hits.append((category, kw))

    if not neg_hits:
        return None

    pos_hits = [kw for kw in PARENTING_KEYWORDS if kw in compact]
    if len(pos_hits) > len(neg_hits):
        return None  # 육아 키워드 비중이 더 높으면 통과 (육아템도 겸하는 계정 과잉 필터링 방지)

    category, keyword = neg_hits[0]
    return f"{category}({keyword})"


def _jitter() -> None:
    time.sleep(random.uniform(JITTER_MIN_SEC, JITTER_MAX_SEC))


def _load_cookies() -> list[dict]:
    if not INSTA_COOKIES_PATH.exists():
        raise FileNotFoundError(
            f"{INSTA_COOKIES_PATH} 가 없습니다. 먼저 `python login_helper.py` 를 실행해 "
            "로그인 쿠키를 저장하세요."
        )
    return json.loads(INSTA_COOKIES_PATH.read_text(encoding="utf-8"))


def _new_authenticated_context(browser):
    """공유 browser 위에 로그인 쿠키가 실린 새 context를 하나 연다 (가볍다 -
    브라우저 프로세스 자체를 새로 띄우는 것과 비교가 안 될 정도로 빠르고 안전함)."""
    cookies = _load_cookies()
    context = browser.new_context(viewport={"width": 1280, "height": 2000})
    context.add_cookies(cookies)
    return context


# ---------------------------------------------------------------------------
# 1) 해시태그 -> 후보 게시물 -> 작성자 핸들
#
# 아래 두 함수와 _check_profile()은 전부 run_forever()가 한 번만 띄운 공유
# browser 인스턴스를 받아서 쓴다. 최초 버전은 호출마다 sync_playwright()로
# 브라우저 프로세스를 통째로 새로 띄웠는데, 10시간 무인 실행 중 후반부에
# 'PlaywrightContextManager has no attribute _playwright' 에러가 반복 발생하며
# 사실상 멈춘 것을 실제 야간 실행 로그로 확인했다 - 브라우저 프로세스를
# 수백 번 띄우고 내리는 걸 반복하다 드라이버 쪽 리소스가 고갈된 것으로 보인다.
# browser를 재사용(대신 context만 매번 새로 열고 닫음)하도록 고쳐서 해결한다.
# ---------------------------------------------------------------------------
def _get_hashtag_post_shortcodes(browser, hashtag: str, limit: int) -> list[str]:
    context = _new_authenticated_context(browser)
    page = context.new_page()
    try:
        page.goto(
            f"https://www.instagram.com/explore/tags/{hashtag}/",
            wait_until="domcontentloaded",
            timeout=25000,
        )
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PWTimeoutError:
            pass  # 해시태그 페이지는 지속적으로 백그라운드 요청을 하므로 타임아웃 무시하고 진행
        _jitter()
        page.mouse.wheel(0, 1500)
        _jitter()

        hrefs = page.eval_on_selector_all("a", "els => els.map(e => e.getAttribute('href'))")
        codes: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            if not href:
                continue
            m = re.search(r"/p/([A-Za-z0-9_\-]+)/?", href)
            if not m:
                continue
            code = m.group(1)
            if code in seen:
                continue
            seen.add(code)
            codes.append(code)
            if len(codes) >= limit:
                break
        return codes
    finally:
        context.close()


def _get_post_author(browser, shortcode: str) -> str | None:
    """공개 임베드 페이지(로그인 불필요)에서 작성자 핸들만 가볍게 추출."""
    url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        try:
            page.wait_for_timeout(1500)
        except PWTimeoutError:
            pass
        body_text = page.inner_text("body")
        for line in body_text.splitlines():
            line = line.strip()
            if line and _looks_like_username(line):
                # 임베드 페이지 첫 줄이 보통 작성자 핸들이지만, 가끔 "View more on
                # Instagram" 같은 UI 문구가 먼저 잡히는 경우가 있어(실제 야간 실행
                # 로그에서 @Instagram 오탐 확인됨) 유효한 핸들 형태인지 검증한다.
                return line
        return None
    except Exception:  # noqa: BLE001
        return None
    finally:
        context.close()


# ---------------------------------------------------------------------------
# 2) 프로필 1차 필터링
# ---------------------------------------------------------------------------
def _check_profile(browser, handle: str) -> dict | None:
    """멀티링크 보유 또는 bio 키워드 매치 시 targets.json에 추가할 dict 반환, 아니면 None."""
    context = _new_authenticated_context(browser)
    page = context.new_page()
    try:
        page.goto(f"https://www.instagram.com/{handle}/", wait_until="domcontentloaded", timeout=25000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PWTimeoutError:
            pass
        try:
            page.wait_for_selector("header", timeout=8000)
        except PWTimeoutError:
            pass
        _jitter()

        header_text = ""
        try:
            header_text = page.eval_on_selector("header", "el => el.innerText") or ""
        except Exception:
            pass

        neg_reason = _negative_filter_reason(header_text)
        if neg_reason:
            logger.info("[Negative Filtered: %s] @%s", neg_reason, handle)
            return None

        multilink_url = ""
        multilink_type = ""
        m = MULTILINK_URL_RE.search(header_text)
        if m:
            multilink_url = m.group(0)
            for domain, mtype in MULTILINK_DOMAIN_MAP.items():
                if domain in multilink_url:
                    multilink_type = mtype
                    break

        bio_compact = header_text.replace(" ", "")
        keyword_hit = any(kw in bio_compact for kw in BIO_KEYWORDS)

        if not multilink_url and not keyword_hit:
            return None

        is_curator = any(kw in bio_compact for kw in CURATOR_BIO_KEYWORDS)
        if is_curator:
            logger.info("[Curator 발견] @%s - VIP 소스로 편입", handle)

        entry = {
            "influencer_name": handle,
            "instagram_id": f"@{handle}",
            "multilink_url": multilink_url,
            "multilink_type": multilink_type,
            "primary_focus": "육아용품",
        }
        if is_curator:
            entry["is_curator"] = True
        return entry
    finally:
        context.close()


# ---------------------------------------------------------------------------
# 3) targets.json 중복 없이 append
# ---------------------------------------------------------------------------
def _append_target(entry: dict) -> bool:
    existing = []
    if TARGETS_PATH.exists():
        try:
            existing = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []

    known_ids = {e.get("instagram_id") for e in existing if e.get("instagram_id")}
    known_urls = {e.get("multilink_url") for e in existing if e.get("multilink_url")}

    if entry["instagram_id"] in known_ids:
        return False
    if entry["multilink_url"] and entry["multilink_url"] in known_urls:
        return False

    existing.append(entry)
    TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGETS_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _already_known_handles() -> set[str]:
    if not TARGETS_PATH.exists():
        return set()
    try:
        existing = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return {
        (e.get("instagram_id") or "").lstrip("@")
        for e in existing
        if e.get("instagram_id")
    }


# ---------------------------------------------------------------------------
# 스케줄 / 메인 루프
# ---------------------------------------------------------------------------
def _in_active_window(now: datetime, end_date: date) -> bool:
    if now.date() < RUN_START_DATE or now.date() > end_date:
        return False
    return ACTIVE_START_HOUR <= now.hour < ACTIVE_END_HOUR


def _seconds_until_next_check(now: datetime, end_date: date) -> float:
    """비활성 시간대일 때 다음 활성 시작 시각까지 남은 초 (최대 1시간 단위로 쪼개 반환)."""
    if now.date() > end_date:
        return -1  # 종료 신호

    if now.hour < ACTIVE_START_HOUR or now.date() < RUN_START_DATE:
        target_date = max(now.date(), RUN_START_DATE)
        next_start = datetime.combine(target_date, datetime.min.time()).replace(hour=ACTIVE_START_HOUR)
    else:
        next_start = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()).replace(
            hour=ACTIVE_START_HOUR
        )

    return min((next_start - now).total_seconds(), 3600)


def run_one_cycle(browser, seen_handles: set[str], hashtag_idx: list[int]) -> bool:
    """후보 하나를 찾아 프로필을 검증하고 필요하면 targets.json에 추가한다.
    반환값: 실제로 계정 1개를 검증했으면 True (호출부가 그때만 rate-limit 대기)."""
    hashtag = HASHTAGS[hashtag_idx[0] % len(HASHTAGS)]
    hashtag_idx[0] += 1

    try:
        shortcodes = _get_hashtag_post_shortcodes(browser, hashtag, HASHTAG_POSTS_PER_SCAN)
    except Exception:
        logger.exception("해시태그 스캔 실패: #%s", hashtag)
        return False

    logger.info("#%s 스캔 -> 후보 게시물 %d개", hashtag, len(shortcodes))

    for code in shortcodes:
        _jitter()
        handle = _get_post_author(browser, code)
        if not handle or handle in seen_handles or handle in _already_known_handles():
            continue

        seen_handles.add(handle)
        logger.info("프로필 검증 중: @%s", handle)
        try:
            entry = _check_profile(browser, handle)
        except Exception:
            logger.exception("프로필 검증 실패: @%s", handle)
            return True  # 시도는 했으니 rate-limit 대기는 적용

        if entry:
            added = _append_target(entry)
            if added:
                logger.info(
                    "발굴 성공: @%s (멀티링크=%s, bio키워드매치=%s) -> targets.json 추가",
                    handle, entry["multilink_url"] or "없음", not entry["multilink_url"],
                )
            else:
                logger.info("@%s 는 이미 targets.json에 있음", handle)
        else:
            logger.info("@%s 조건 불충족 (멀티링크/키워드 없음) - 건너뜀", handle)

        return True  # 계정 하나 처리 완료

    return False  # 이번 해시태그에서 새 후보를 못 찾음 (다음 사이클에 다른 해시태그 시도)


def run_forever(end_date: date | None = None) -> None:
    """end_date 를 주면 RUN_END_DATE 대신 그 날짜까지만 실행한다
    (예: 5일 전체 중 오늘 하루만 먼저 시험 실행하고 싶을 때)."""
    end_date = end_date or RUN_END_DATE
    seen_handles: set[str] = set()
    hashtag_idx = [0]

    logger.info(
        "야간 해시태그 자동 탐색 스케줄러 시작 (%s ~ %s, 매일 %02d:00~%02d:00)",
        RUN_START_DATE, end_date, ACTIVE_START_HOUR, ACTIVE_END_HOUR,
    )

    while True:
        now = datetime.now()

        if now.date() > end_date:
            logger.info("예정된 기간(%s)이 끝나 스케줄러를 종료합니다.", end_date)
            return

        if not _in_active_window(now, end_date):
            wait_sec = _seconds_until_next_check(now, end_date)
            if wait_sec < 0:
                return
            logger.info("비활성 시간대(%s) - %.0f분 후 다시 확인", now.strftime("%Y-%m-%d %H:%M"), wait_sec / 60)
            time.sleep(wait_sec)
            continue

        logger.info("활성 시간대 진입 - 브라우저를 새로 띄웁니다.")
        _run_active_session(end_date, seen_handles, hashtag_idx)
        logger.info("활성 시간대 종료(또는 %02d:00 도달) - 브라우저를 닫았습니다.", ACTIVE_END_HOUR)


def _run_active_session(end_date: date, seen_handles: set[str], hashtag_idx: list[int]) -> None:
    """활성 시간대(00:00~10:00) 동안 브라우저 하나를 재사용하며 탐색을 반복한다.
    활성 시간대가 끝나면 브라우저를 닫고 리턴한다 - 매일 브라우저를 새로 띄우고
    내리므로, 한 프로세스를 여러 날 계속 띄워놔도 브라우저 자체는 최대 10시간
    이상 연속으로 살아있지 않는다 (야간 실행에서 실제로 겪은 장시간 연속 사용
    리소스 고갈 문제의 재발을 막기 위함)."""
    # [VIP 소스 전략 1] 해시태그 탐색보다 먼저 큐레이션 계정(예: we09.lab)부터
    # 스캔한다 - 한 번 수집으로 수십 명의 검증된 계정을 확보할 수 있어 데이터
    # 효율이 가장 높으므로 매일 밤 최우선으로 처리한다.
    try:
        curator_results = run_curator_scan()
        if curator_results:
            total_added = sum(r["added"] for r in curator_results)
            logger.info("[curator] 오늘 밤 큐레이션 스캔 완료 - 계정 %d개, 신규 타겟 %d명 등록",
                        len(curator_results), total_added)
    except Exception:
        logger.exception("[curator] 큐레이션 스캔 실패 - 해시태그 탐색은 정상 진행")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            while _in_active_window(datetime.now(), end_date):
                try:
                    processed = run_one_cycle(browser, seen_handles, hashtag_idx)
                except Exception:
                    logger.exception("탐색 사이클 중 오류 - 다음 사이클로 계속 진행")
                    processed = True  # 오류 시에도 과도한 재시도를 막기 위해 대기는 적용

                if processed:
                    wait_sec = random.uniform(WAIT_MIN_SEC_BETWEEN_ACCOUNTS, WAIT_MAX_SEC_BETWEEN_ACCOUNTS)
                    logger.info("다음 계정까지 %.1f분 대기 (시간당 6~8개 속도 제한)", wait_sec / 60)
                    time.sleep(wait_sec)
                else:
                    time.sleep(30)  # 후보를 못 찾았으면 짧게만 쉬고 다른 해시태그로 재시도
        finally:
            browser.close()


def run_tonight() -> None:
    """Windows 작업 스케줄러 등이 매일 00:00에 '새 프로세스'로 기동하는 걸
    전제로 한 단발 실행 모드. run_forever()처럼 여러 날에 걸쳐 대기/반복하지
    않고, 지금부터 오늘 %02d:00까지만 탐색한 뒤 스스로 완전히 종료한다.

    run_forever()로 여러 날을 하나의 프로세스로 이어가다가 원인불명으로
    프로세스가 중간에 정리되는 문제를 두 번 겪었다 (OS/세션 쪽 요인으로
    추정, 스크립트 자체 오류는 아니었음). 대신 매일 밤 짧게(최대 10시간)
    떴다가 확실히 끝나는 이 모드 + 매일 새로 트리거하는 작업 스케줄러
    조합이 하루가 실패해도 다음날엔 영향이 없어 더 안전하다."""
    today = date.today()
    logger.info(
        "단발성 야간 탐색 시작 (%s, 지금부터 %02d:00까지만 실행 후 정상 종료)",
        today, ACTIVE_END_HOUR,
    )

    if not _in_active_window(datetime.now(), today):
        logger.warning(
            "현재 시각이 활성 시간대(%02d:00~%02d:00) 밖입니다 - 할 일 없이 바로 종료합니다.",
            ACTIVE_START_HOUR, ACTIVE_END_HOUR,
        )
        return

    _run_active_session(today, set(), [0])
    logger.info("오늘 밤 탐색 완료 (%02d:00 도달) - 프로세스를 정상 종료합니다.", ACTIVE_END_HOUR)


def _run_for_duration(deadline: float, seen_handles: set[str], hashtag_idx: list[int]) -> None:
    """time.monotonic() 기준 deadline까지 브라우저 하나를 재사용하며 탐색을 반복한다.
    _run_active_session()과 달리 활성 시간대(00:00~10:00)와 무관하게 순수 경과 시간만으로
    종료를 판단한다 - 낮 시간대 1회성 보충 실행용."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            while time.monotonic() < deadline:
                try:
                    processed = run_one_cycle(browser, seen_handles, hashtag_idx)
                except Exception:
                    logger.exception("탐색 사이클 중 오류 - 다음 사이클로 계속 진행")
                    processed = True  # 오류 시에도 과도한 재시도를 막기 위해 대기는 적용

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break

                if processed:
                    wait_sec = min(random.uniform(WAIT_MIN_SEC_BETWEEN_ACCOUNTS, WAIT_MAX_SEC_BETWEEN_ACCOUNTS), remaining)
                    logger.info("다음 계정까지 %.1f분 대기 (시간당 6~8개 속도 제한)", wait_sec / 60)
                    time.sleep(wait_sec)
                else:
                    time.sleep(min(30, remaining))  # 후보를 못 찾았으면 짧게만 쉬고 다른 해시태그로 재시도
        finally:
            browser.close()


def run_for_duration(hours: float) -> None:
    """활성 시간대(00:00~10:00) 제한을 무시하고, 실행 시점부터 정확히 `hours`시간
    동안만 탐색한 뒤 스스로 정상 종료하는 1회성 모드. 속도 제한(시간당 6~8개)/
    사람같은 지연(4~9초)/프로필 키워드 필터/네거티브 키워드 필터는 run_one_cycle()과
    _check_profile() 안에 그대로 살아있으므로 이 모드에서도 동일하게 적용된다 -
    바뀌는 건 오직 '언제 실행 가능한가'(시간대 제한)뿐이다."""
    start = datetime.now()
    deadline = time.monotonic() + hours * 3600
    end_clock = start + timedelta(hours=hours)
    logger.info(
        "1회성 보충 탐색 시작 (%s ~ 약 %s, %.1f시간) - 활성 시간대 제한 무시, 안전장치는 그대로 유지",
        start.strftime("%Y-%m-%d %H:%M"), end_clock.strftime("%H:%M"), hours,
    )
    _run_for_duration(deadline, set(), [0])
    logger.info("1회성 보충 탐색 완료 (%.1f시간 경과) - 프로세스를 정상 종료합니다.", hours)


if __name__ == "__main__":
    import sys

    if "--duration" in sys.argv:
        _idx = sys.argv.index("--duration")
        _raw = sys.argv[_idx + 1]
        _hours = float(_raw[:-1]) if _raw.lower().endswith("h") else float(_raw)
        run_for_duration(_hours)
    elif "--once" in sys.argv:
        logging.info("--once 모드: 계정 1개만 즉시 검증하고 종료합니다.")
        with sync_playwright() as p:
            _browser = p.chromium.launch(headless=True)
            try:
                run_one_cycle(_browser, set(), [0])
            finally:
                _browser.close()
    elif "--tonight" in sys.argv:
        run_tonight()
    elif "--until" in sys.argv:
        idx = sys.argv.index("--until")
        until_date = date.fromisoformat(sys.argv[idx + 1])
        run_forever(end_date=until_date)
    else:
        run_forever()
