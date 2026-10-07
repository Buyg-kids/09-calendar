"""셀러 발굴 도구들(discover_sellers / merge_candidates / flag_verified_sellers)이 같이 쓰는 헬퍼.

야간 파이프라인에는 연결되지 않는다(수동 실행 도구 전용). 인스타그램 서버에는 접속하지 않는다.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TARGETS_PATH = ROOT / "data" / "targets.json"
CANDIDATES_PATH = ROOT / "data" / "candidates_discovered.json"
RAW_COLLECTED_PATH = ROOT / "data" / "raw_collected.json"

# 인스타그램 핸들: 영문/숫자/마침표/밑줄 1~30자
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

# instagram.com/ 바로 뒤 첫 경로가 이 값이면 계정이 아니라 기능 페이지
RESERVED_PATHS = {
    "p", "reel", "reels", "explore", "accounts", "stories", "tv", "direct", "about", "legal",
    "developer", "web", "share", "oauth", "challenge", "privacy", "terms", "api", "static",
    "emails", "graphql", "ajax", "directory", "lite", "business", "help", "download",
}
# 임베드/로그인 UI 문구가 핸들로 오인되는 것 방지 (auto_discover_scheduler 와 같은 취지)
INVALID_HANDLES = {"instagram", "log", "login", "signup", "loading", "meta", "facebook"}

# JSON 으로 이스케이프된 "instagram.com\/handle" 도 잡는다
INSTAGRAM_RE = re.compile(
    r"(?:https?:)?(?:\\?/\\?/)(?:www\.|m\.)?instagram\.com\\?/([A-Za-z0-9._]{1,30})(?=[\\/?#\"'\s<>)\]]|$)",
    re.IGNORECASE,
)

# 멀티링크 도메인 -> (multilink_type, 서비스 이름). type 문자열은 collector/discover 와 동일.
MULTILINK_DOMAINS = {
    "link.inpock.co.kr": "inpock",
    "inpk.link": "inpock",
    "inpock.co.kr": "inpock",
    "litt.ly": "littly",
    "lit.link": "litlink",
}


def extract_instagram_handles(text: str) -> list[str]:
    """HTML/텍스트에서 instagram.com/{username} 핸들을 등장 순서대로(중복 제거, 소문자 정규화) 뽑는다."""
    found: list[str] = []
    seen: set[str] = set()
    for m in INSTAGRAM_RE.finditer(text or ""):
        h = m.group(1).rstrip(".")
        low = h.lower()
        if not h or not USERNAME_RE.match(h):
            continue
        if low in RESERVED_PATHS or low in INVALID_HANDLES:
            continue
        if low not in seen:
            seen.add(low)
            found.append(low)
    return found


def multilink_type_of(url: str) -> str:
    """URL 이 인포크/리틀리/릿링크 계열이면 type 문자열, 아니면 ''."""
    host = (urlparse(url if "//" in (url or "") else "//" + (url or "")).hostname or "").lower()
    for domain, mtype in MULTILINK_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return mtype
    return ""


def handle_of_target(entry: dict) -> str:
    """targets.json 항목에서 인스타 핸들(소문자). instagram_id 우선, 없으면 influencer_name 첫 토큰.
    멀티링크 URL 슬러그는 쓰지 않는다(슬러그 != 인스타 핸들이던 과거 사고)."""
    iid = (entry.get("instagram_id") or "").strip().lstrip("@")
    if iid and USERNAME_RE.match(iid):
        return iid.lower()
    name = (entry.get("influencer_name") or "").strip().split(" ")[0].lstrip("@")
    if name and USERNAME_RE.match(name):
        return name.lower()
    return ""


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def backup_targets(reason: str) -> Path:
    """data/targets.json.bak_YYYYMMDD_HHMMSS_pre_<reason> 로 복사하고 경로를 돌려준다."""
    dst = TARGETS_PATH.with_name("targets.json.bak_%s_pre_%s" % (datetime.now().strftime("%Y%m%d_%H%M%S"), reason))
    shutil.copy2(TARGETS_PATH, dst)
    return dst
