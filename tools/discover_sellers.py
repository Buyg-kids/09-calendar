"""인포크링크/리틀리 랜딩 페이지에서 공구 셀러의 인스타그램 핸들을 역추적하는 1회성 수동 실행 도구.

- 야간 파이프라인/스케줄러에는 연결하지 않는다. 인스타그램 서버에는 접속하지 않는다.
- 검색 수단(플러그형):
    1) .env 에 NAVER_SEARCH_CLIENT_ID/SECRET (없으면 NAVER_CLIENT_ID/SECRET) 이 있으면 네이버 검색 오픈 API(webkr)
    2) 없거나 거절(401/403)되면 저속 HTML 검색(DuckDuckGo html): 요청 간격 5초 이상, 쿼리당 소수 페이지, 결과 캐시
  구글 직접 스크래핑/CAPTCHA 우회는 하지 않는다. 차단·캡차가 감지되면 즉시 중단하고 안내를 출력한다.
- 결과: data/candidates_discovered.json (기존 targets.json 과 중복되는 핸들은 제외)
  -> tools/merge_candidates.py 로 검토/승인 후 병합.

실행:  python tools/discover_sellers.py            (쿼리당 1페이지, 랜딩 최대 30개)
       python tools/discover_sellers.py --pages 2 --max-landing 60
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

import seller_utils as su  # noqa: E402

logger = logging.getLogger("discover_sellers")

QUERIES = [
    'site:link.inpock.co.kr "육아"',
    'site:link.inpock.co.kr "공구"',
    'site:litt.ly "육아"',
    'site:litt.ly "공구 일정"',
]

USER_AGENT = "BUYG-seller-discovery/1.0 (+https://buyg.kr; contact: ayhkk1@gmail.com)"
MIN_INTERVAL = 5.0  # 모든 외부 요청 사이 최소 간격(초)
CACHE_DIR = su.ROOT / "data" / "discover_sellers_cache"
DDG_URL = "https://html.duckduckgo.com/html/"
NAVER_URL = "https://openapi.naver.com/v1/search/webkr.json"

# 랜딩 URL: 지원 도메인의 '프로필 페이지'만 (루트/도움말/정책 페이지 제외)
LANDING_RE = re.compile(
    r"https?://(link\.inpock\.co\.kr|inpk\.link|litt\.ly)/([A-Za-z0-9_.\-]+)/?(?=[\s\"'<>?#]|$)", re.IGNORECASE
)
LANDING_SLUG_BLOCK = {"", "login", "signup", "help", "about", "terms", "privacy", "policy", "explore", "search", "discover"}
CAPTCHA_MARKERS = ("anomaly-modal", "bots use DuckDuckGo", "captcha", "unusual traffic")


class BlockedError(RuntimeError):
    """검색/페이지 요청이 차단·캡차로 거절된 경우 - 즉시 중단한다."""


_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _cache_path(kind: str, key: str, ext: str) -> Path:
    return CACHE_DIR / f"{kind}_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:20]}.{ext}"


def parse_landing_urls(text: str) -> list[tuple[str, str]]:
    """텍스트(검색 결과 링크·HTML)에서 (정규화된 랜딩 URL, multilink_type) 목록. 중복 제거."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in LANDING_RE.finditer(text or ""):
        domain, slug = m.group(1).lower(), m.group(2)
        if slug.lower() in LANDING_SLUG_BLOCK:
            continue
        url = f"https://{domain}/{slug}"
        if url.lower() in seen:
            continue
        seen.add(url.lower())
        out.append((url, su.multilink_type_of(url)))
    return out


# ---------------------------------------------------------------------------
# 검색 백엔드 (플러그형)
# ---------------------------------------------------------------------------
class NaverBackend:
    name = "naver-webkr"

    def __init__(self, cid: str, secret: str):
        self.headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret, "User-Agent": USER_AGENT}

    def search(self, query: str, page: int) -> list[str]:
        _throttle()
        r = requests.get(
            NAVER_URL, headers=self.headers, timeout=15,
            params={"query": query, "display": 10, "start": 1 + (page - 1) * 10},
        )
        if r.status_code in (401, 403):
            raise PermissionError(f"네이버 검색 API 거절({r.status_code}): 검색 API 권한 미설정 가능성")
        if r.status_code == 429:
            raise BlockedError("네이버 검색 API 호출 한도 초과(429)")
        r.raise_for_status()
        return [it.get("link", "") for it in r.json().get("items", [])]


BLOCK_MARKER = CACHE_DIR / "ddg_blocked_until.txt"
BLOCK_COOLDOWN_SEC = 2 * 3600  # 차단 감지 후 이 시간 동안은 같은 검색 엔진에 다시 요청하지 않는다


class DdgBackend:
    name = "duckduckgo-html"

    def search(self, query: str, page: int) -> list[str]:
        try:
            if time.time() < float(BLOCK_MARKER.read_text().strip()):
                raise BlockedError("DuckDuckGo 차단 쿨다운 중(최근 차단 감지) - 요청하지 않음")
        except (OSError, ValueError):
            pass
        _throttle()
        data = {"q": query}
        if page > 1:
            data["s"] = str((page - 1) * 30)
        r = requests.post(DDG_URL, data=data, headers={"User-Agent": USER_AGENT}, timeout=15)
        if r.status_code in (202, 403, 429) or any(mk in r.text for mk in CAPTCHA_MARKERS):
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                BLOCK_MARKER.write_text(str(time.time() + BLOCK_COOLDOWN_SEC))
            except OSError:
                pass
            raise BlockedError(f"DuckDuckGo 가 요청을 차단/캡차 처리함 (HTTP {r.status_code})")
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        return [a.get("href") for a in soup.select("a.result__a") if a.get("href")]


def pick_backend() -> list:
    """사용 가능한 백엔드 우선순위 목록: 네이버(키 있을 때) -> DDG."""
    try:
        from dotenv import load_dotenv
        load_dotenv(su.ROOT / ".env")
    except ImportError:
        pass
    cid = os.getenv("NAVER_SEARCH_CLIENT_ID") or os.getenv("NAVER_CLIENT_ID")
    sec = os.getenv("NAVER_SEARCH_CLIENT_SECRET") or os.getenv("NAVER_CLIENT_SECRET")
    chain = []
    if cid and sec:
        chain.append(NaverBackend(cid, sec))
    chain.append(DdgBackend())
    return chain


def run_query(chain: list, query: str, pages: int) -> tuple[list[str], str]:
    """캐시 -> 백엔드 순으로 검색. (링크 목록, 사용한 백엔드 이름). 차단되면 BlockedError."""
    links: list[str] = []
    used = "cache"
    for page in range(1, pages + 1):
        cp = _cache_path("search", f"{query}|{page}", "txt")
        if cp.exists():
            links += cp.read_text(encoding="utf-8").splitlines()
            continue
        got: list[str] | None = None
        for backend in list(chain):
            try:
                got = backend.search(query, page)
                used = backend.name
                break
            except PermissionError as e:
                logger.warning("%s - 다음 검색 수단으로 폴백", e)
                chain.remove(backend)
            except requests.RequestException as e:
                logger.warning("%s 요청 실패(%s) - 다음 검색 수단으로 폴백", backend.name, e.__class__.__name__)
                chain.remove(backend)
        if got is None:
            raise BlockedError("사용 가능한 검색 수단이 없음(모두 실패/거절)")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cp.write_text("\n".join(got), encoding="utf-8")
        links += got
    return links, used


# ---------------------------------------------------------------------------
# 랜딩 페이지 -> 인스타 핸들
# ---------------------------------------------------------------------------
def fetch_landing(url: str) -> str | None:
    """랜딩 페이지 HTML (캐시 우선, 페이지당 1요청). 실패하면 None, 차단이면 BlockedError."""
    cp = _cache_path("landing", url, "html")
    if cp.exists():
        return cp.read_text(encoding="utf-8")
    _throttle()
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    except requests.RequestException as e:
        logger.warning("랜딩 요청 실패(%s): %s", e.__class__.__name__, url)
        return None
    if r.status_code in (403, 429):
        raise BlockedError(f"랜딩 페이지 접근 거절 (HTTP {r.status_code}): {url}")
    if r.status_code != 200:
        logger.warning("랜딩 응답 %s: %s", r.status_code, url)
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp.write_text(r.text, encoding="utf-8")
    return r.text


def handles_from_landing(html: str) -> list[str]:
    return su.extract_instagram_handles(html)


def build_candidates(landing: list[tuple[str, str, str]], existing_handles: set[str], html_by_url: dict[str, str]) -> list[dict]:
    """(url, type, query) 목록 + 랜딩 HTML -> 후보 dict 목록(기존 타겟 제외, 핸들 중복 제거)."""
    now = datetime.now().isoformat(timespec="seconds")
    out: list[dict] = []
    seen = set(existing_handles)
    for url, mtype, query in landing:
        for h in handles_from_landing(html_by_url.get(url, ""))[:3]:  # 한 페이지에 계정이 많으면 상위 3개만
            if h in seen:
                continue
            seen.add(h)
            out.append({"handle": h, "landing_url": url, "multilink_type": mtype, "found_via_query": query, "discovered_at": now})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", type=int, default=1, help="쿼리당 검색 페이지 수(기본 1)")
    ap.add_argument("--max-landing", type=int, default=30, help="가져올 랜딩 페이지 최대 수(기본 30)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    targets = su.load_json(su.TARGETS_PATH, [])
    existing = {h for h in (su.handle_of_target(e) for e in targets) if h}
    prev = su.load_json(su.CANDIDATES_PATH, [])
    prev_handles = {c.get("handle") for c in prev}

    chain = pick_backend()
    logger.info("검색 수단 우선순위: %s", " -> ".join(b.name for b in chain))
    landing: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()
    stopped = False
    html_by_url: dict[str, str] = {}
    landing: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()
    blocked_msg = ""
    try:
        for q in QUERIES:
            links, used = run_query(chain, q, args.pages)
            urls = parse_landing_urls("\n".join(links))
            logger.info("쿼리 %s -> 결과 링크 %d개 / 랜딩 후보 %d개 (%s)", q, len(links), len(urls), used)
            for url, mtype in urls:
                if url.lower() not in seen_urls:
                    seen_urls.add(url.lower())
                    landing.append((url, mtype, q))
    except BlockedError as e:
        stopped, blocked_msg = True, str(e)
        logger.error("검색 중단: %s (여기까지 모은 랜딩 후보 %d개는 계속 처리)", e, len(landing))
    landing = landing[: args.max_landing]
    try:
        for i, (url, _t, _q) in enumerate(landing, 1):
            logger.info("랜딩 %d/%d: %s", i, len(landing), url)
            html = fetch_landing(url)
            if html:
                html_by_url[url] = html
    except BlockedError as e:
        stopped, blocked_msg = True, str(e)
        logger.error("랜딩 수집 중단: %s", e)
    if stopped:
        print("\n[안내] 검색/페이지 요청이 차단되었습니다(%s). 우회하지 않고 해당 단계를 멈췄습니다.\n"
              "  - .env 에 NAVER_SEARCH_CLIENT_ID / NAVER_SEARCH_CLIENT_SECRET 을 넣으면 네이버 검색 오픈 API(정식 경로)로 실행됩니다.\n"
              "  - 또는 몇 시간 뒤 다시 실행하세요(캐시된 결과는 재사용되고, 차단된 엔진에는 2시간간 요청하지 않습니다)." % blocked_msg)

    new = build_candidates(landing, existing | prev_handles, html_by_url)
    merged = prev + new
    if new:
        su.save_json(su.CANDIDATES_PATH, merged)
    print(f"\n랜딩 페이지 {len(html_by_url)}/{len(landing)}개 수집 / 신규 후보 {len(new)}명 (누적 {len(merged)}명) / 중단={'예' if stopped else '아니오'}")
    for c in new[:20]:
        print(f"  @{c['handle']}  <- {c['landing_url']}")
    if new:
        print(f"저장: {su.CANDIDATES_PATH}  ->  python tools/merge_candidates.py --list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
