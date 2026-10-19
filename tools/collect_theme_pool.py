"""테마별 나들이 장소 풀 수집기 (TourAPI 키워드 스캔).  python tools/collect_theme_pool.py [--max-calls 400] [--dry-run] [--out theme_spots_pool.json] [--themes dinosaur,art]

테마별 핵심 키워드로 TourAPI searchKeyword2 를 훑어 관광지(12)·문화시설(14)·레포츠(28) 중 제목에 키워드가 들어 있고 좌표가 있는 곳만 풀에 담는다.
 - 지어내지 않는다: 키워드가 제목에 있는 것만 담고(주소·개요만 일치하는 것은 제외), 지역은 lDongRegnCd+주소 교차검증이 안 되면 제외, 사진은 공공누리 제1유형만.
 - 시드·앵커·이미 들어 있는 항목과 중복(이름 정규화 + 좌표 100m)이면 건너뛴다. 테마별 제외 규칙(예: 공주시)을 둔다.
 - --dry-run: 파일을 쓰지 않고 테마별 건수·표본만 출력(호출은 한다). 호출 한도 --max-calls, 요청 사이 0.5초, 쿼터 신호가 오면 즉시 멈춘다.
로그 규칙: 예외는 타입명만 남긴다(logger.exception 금지 - 야간 로그에 'Traceback' 이 찍히면 배포 게이트가 막힌다). 인증키는 .env, 출력에 쓰지 않는다.
산출: <out> (공개 파일, schema_version 1). 항목마다 provenance·fetched_at 가 있고 verified:false(사람 검수 전)."""
import argparse
import json
import logging
import math
import os
import re
import sys
from datetime import date
from pathlib import Path

PROJECT = Path(os.environ.get("BUYG_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))

log = logging.getLogger("collect_theme_pool")
SRC = "https://api.visitkorea.or.kr/"
OK_TYPES = {"12": "관광지", "14": "문화시설", "28": "레포츠"}
PAGE_ROWS = 100
MAX_PAGES = 3                                  # 키워드 하나당 최대 300건까지만 본다
DEDUPE_M = 100

# 테마 id 는 index.html 의 THEMES 와 같다. 키워드는 한 곳에서 관리한다(사용자 지정 7종 + 나머지 2종은 기존 정규식의 핵심어).
THEMES = {
    "princess":  {"label": "공주·캐릭터", "keywords": ["캐릭터", "애니메이션", "동화", "뽀로로", "티니핑", "테마파크", "키즈카페"], "neg": r"공주시|국립공주박물관|공주대|공주 ?(?:공산성|무령왕릉|한옥)"},
    "dinosaur":  {"label": "공룡 탐험", "keywords": ["공룡", "화석", "자연사", "쥐라기"], "neg": ""},
    "transport": {"label": "기차·자동차", "keywords": ["철도", "기차", "자동차", "교통박물관", "항공"], "neg": r"철도공사|철도역|항공사|항공권"},
    "art":       {"label": "미술·드로잉", "keywords": ["어린이미술관", "드로잉", "미술체험", "공예"], "neg": ""},
    "cooking":   {"label": "쿠킹·베이킹", "keywords": ["농장체험", "딸기체험", "치즈체험", "쿠킹"], "neg": ""},
    "animal":    {"label": "동물·아쿠아리움", "keywords": ["동물원", "아쿠아리움", "목장", "알파카", "승마체험"], "neg": ""},
    "nature":    {"label": "숲·모래놀이", "keywords": ["유아숲", "생태공원", "모래놀이", "수목원"], "neg": ""},
    "science":   {"label": "박물관·과학", "keywords": ["과학관", "천문대", "어린이박물관"], "neg": ""},
    "picnic":    {"label": "꽃·피크닉", "keywords": ["국가정원", "식물원", "꽃축제", "피크닉"], "neg": ""},
}
# 성인·유흥 업종 제목은 어떤 테마에서도 제외
ADULT_RE = re.compile(r"나이트|클럽|카지노|룸살롱|주점|모텔|성인")


def norm(s):
    return re.sub(r"\s+", "", re.sub(r"\(.*?\)", "", s or ""))


def km(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def to_float(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def candidate(raw, theme_id, keyword, today, region_of):
    """TourAPI 한 건 -> 풀 항목(조건 미달이면 None). 순수 함수(테스트 대상)."""
    t = THEMES[theme_id]
    title = (raw.get("title") or "").strip()
    ctype = str(raw.get("contenttypeid") or "")
    lat, lon = to_float(raw.get("mapy")), to_float(raw.get("mapx"))
    if not title or ctype not in OK_TYPES or keyword not in title or ADULT_RE.search(title):
        return None
    if t["neg"] and re.search(t["neg"], title + " " + (raw.get("addr1") or "")):
        return None
    if lat is None or lon is None or not (33.0 <= lat <= 39.0 and 124.0 <= lon <= 132.0):
        return None
    address = (raw.get("addr1") or "").strip()
    region = region_of(raw.get("areacode"), address, raw.get("lDongRegnCd"))
    if not region:
        return None                                   # 지역 교차검증 실패(코드와 주소가 어긋남)는 제외
    item = {"id": "pool_" + str(raw.get("contentid")), "contentid": str(raw.get("contentid")), "title": title, "address": address, "region": region,
            "geo": {"lat": round(lat, 6), "lon": round(lon, 6)}, "category": OK_TYPES[ctype], "photo": None, "matched_keywords": [keyword],
            "provenance": "tourapi", "source_url": SRC, "fetched_at": today, "verified": False}
    img = (raw.get("firstimage") or "").strip()
    if img.startswith("http://"):
        img = "https://" + img[len("http://"):]
    host = img.split("/")[2] if img.startswith("https://") and len(img.split("/")) > 2 else ""
    if img and raw.get("cpyrhtDivCd") == "Type1" and (host == "visitkorea.or.kr" or host.endswith(".visitkorea.or.kr")):
        th = (raw.get("firstimage2") or "").strip().replace("http://", "https://", 1) or img
        item["photo"] = {"url": img, "thumb": th, "credit": "한국관광공사 TourAPI", "license": "공공누리 제1유형(출처표시)", "contentid": str(raw.get("contentid")), "checked": today}
    return item


def is_dup(item, pool_items, known):
    """같은 contentid, 같은 정규화 이름, 또는 100m 안의 같은 이름이면 중복(시드·앵커 = known)."""
    n = norm(item["title"])
    for o in pool_items + known:
        if o.get("contentid") and o.get("contentid") == item["contentid"]:
            return True
        on = norm(o.get("title", ""))
        if on == n:
            g = o.get("geo")
            if not g or km(item["geo"]["lat"], item["geo"]["lon"], g["lat"], g["lon"]) * 1000 <= DEDUPE_M:
                return True
    return False


def merge_item(theme_items, item, known):
    """테마 안에 넣는다. 이미 있으면 키워드만 합치고 새로 넣었으면 True."""
    for o in theme_items:
        if o["contentid"] == item["contentid"]:
            o["matched_keywords"] = sorted(set(o["matched_keywords"]) | set(item["matched_keywords"]))
            if not o.get("photo") and item.get("photo"):
                o["photo"] = item["photo"]
            return False
    if is_dup(item, theme_items, known):
        return False
    theme_items.append(item)
    return True


def known_places(root):
    """시드·앵커·보강 데이터의 (이름, 좌표) 목록 - 풀과 겹치지 않게 한다."""
    out = []
    try:
        seed = json.loads((root / "free_spots_seed.json").read_text(encoding="utf-8")).get("spots", [])
        enr = json.loads((root / "spot_enriched.json").read_text(encoding="utf-8")).get("spots", {})
        for s in seed:
            e = enr.get(s["id"]) or {}
            out.append({"title": s.get("title", ""), "geo": e.get("geo"), "contentid": str(e.get("contentid") or "")})
    except (OSError, ValueError, KeyError):
        pass
    try:
        for g in json.loads((root / "place_guides.json").read_text(encoding="utf-8")).get("places", {}).values():
            out.append({"title": g.get("name", ""), "geo": g.get("geo"), "contentid": ""})
    except (OSError, ValueError):
        pass
    return out


def scan(budget, key, theme_ids, pool, known, today, region_of, max_pages=MAX_PAGES):
    """키워드 스캔. pool = {theme_id: {'keywords': [...], 'items': [...]}} 를 제자리에서 갱신하고 통계를 돌려준다."""
    stats = {"calls": 0, "seen": 0, "kept": 0, "dup": 0}
    for tid in theme_ids:
        slot = pool.setdefault(tid, {"label": THEMES[tid]["label"], "keywords": list(THEMES[tid]["keywords"]), "items": []})
        slot["label"], slot["keywords"] = THEMES[tid]["label"], list(THEMES[tid]["keywords"])
        for kw in THEMES[tid]["keywords"]:
            for page in range(1, max_pages + 1):
                items = budget.call(key, "searchKeyword2", {"keyword": kw, "numOfRows": PAGE_ROWS, "pageNo": page, "arrange": "A"}) or []
                stats["calls"] += 1
                stats["seen"] += len(items)
                for raw in items:
                    c = candidate(raw, tid, kw, today, region_of)
                    if c is None:
                        continue
                    if merge_item(slot["items"], c, known):
                        stats["kept"] += 1
                    else:
                        stats["dup"] += 1
                if len(items) < PAGE_ROWS:
                    break
        slot["items"].sort(key=lambda i: (0 if i.get("photo") else 1, i["title"]))
    return stats


def load_pool(path):
    try:
        j = json.loads(Path(path).read_text(encoding="utf-8"))
        return j.get("themes", {}) if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}


def write_pool(path, pool, today):
    doc = {"schema_version": 1, "generated_at": today, "themes": pool}
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def summary(pool):
    return {tid: len(v["items"]) for tid, v in pool.items()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=400)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=str(PROJECT / "theme_spots_pool.json"))
    ap.add_argument("--themes", default="")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from dotenv import load_dotenv
    load_dotenv(PROJECT / ".env")
    import enrich_spots as es
    from collector import tour_collector as tc
    key = os.environ.get("TOURAPI_SERVICE_KEY") or os.environ.get("TOUR_API_KEY") or ""
    if not key:
        log.error("TourAPI 키가 없습니다(.env)")
        return 0
    ids = [t for t in a.themes.split(",") if t] or list(THEMES)
    ids = [t for t in ids if t in THEMES]
    today = date.today().isoformat()
    pool = load_pool(a.out)
    budget = es.Budget(a.max_calls)
    try:
        stats = scan(budget, key, ids, pool, known_places(PROJECT), today, tc.region_of)
    except es.QuotaStop as e:
        log.warning("중단: %s", e)
        stats = {"calls": budget.used}
    except Exception as e:   # 어떤 오류도 야간 흐름을 막지 않는다(타입명만 기록)
        log.error("수집 오류: %s", type(e).__name__)
        return 0
    log.info("호출 %s회 · 테마별 %s · %s", budget.used, summary(pool), "dry-run(파일 미작성)" if a.dry_run else a.out)
    if not a.dry_run:
        write_pool(a.out, pool, today)
    else:
        for tid, v in pool.items():
            log.info("[%s] %s곳 표본: %s", tid, len(v["items"]), ", ".join(i["title"] for i in v["items"][:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
