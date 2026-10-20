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
    "princess":  {"label": "공주", "keywords": ["공주", "드레스", "동화", "백설공주"], "neg": r"공주시|국립공주박물관|공주대|공주 ?(?:공산성|무령왕릉|한옥)|동화사|가을동화|수변공원|감귤밭|팔공산|동화 ?(?:골프|CC)"},
    "character": {"label": "만화·캐릭터", "keywords": ["뽀로로", "티니핑", "타요", "핑크퐁", "애니메이션", "캐릭터", "키즈월드"], "neg": ""},
    "dinosaur":  {"label": "공룡 탐험", "keywords": ["공룡", "화석", "자연사", "쥐라기"], "neg": r"자연사랑|화석정|워케이션"},
    "transport": {"label": "기차·자동차", "keywords": ["철도", "기차", "자동차", "교통박물관", "항공", "열차", "증기기관차", "모노레일", "레일바이크", "교통안전", "안전체험관", "비행기"], "neg": r"터미널|정류장|철도공사|철도역|항공사|항공권|야영장|자동차극장|극장|대학|공장|교육원|카페|패러글라이딩|해골바위|경비행장|항공산업|문화회관|두바퀴|케이원|양평 미래항공"},
    "art":       {"label": "미술·드로잉", "keywords": ["어린이미술관", "드로잉", "미술체험", "공예", "도예", "도자기체험", "목공체험", "판화", "공방", "어린이미술", "키즈아트"], "neg": r"\(주\)|협동조합"},
    "cooking":   {"label": "쿠킹·베이킹", "keywords": ["농장체험", "딸기체험", "치즈체험", "쿠킹", "베이킹", "체험농장", "치즈마을", "피자체험"], "neg": r"곤충|동물|허브"},
    "animal":    {"label": "동물", "keywords": ["동물원", "목장", "알파카", "승마", "사파리"], "neg": r"목장군|목장터|목장길|목장성|목장 입구|바래길|말목장"},
    "fish":      {"label": "물고기", "keywords": ["아쿠아리움", "수족관", "민물고기", "해양생물"], "neg": ""},
    "nature":    {"label": "숲·모래놀이", "keywords": ["유아숲", "생태공원", "모래놀이", "수목원"], "neg": ""},
    "picnic":    {"label": "꽃·피크닉", "keywords": ["국가정원", "식물원", "꽃축제", "피크닉"], "neg": r"^피크닉$|피크닉댕댕|행복&피크닉"},
    "museum":    {"label": "박물관", "keywords": ["어린이박물관", "역사박물관", "민속촌", "고궁"], "neg": ""},
    "space":     {"label": "별·우주", "keywords": ["천문대", "우주", "항공우주", "별자리", "플라네타륨"], "neg": ""},
    "science":   {"label": "과학·탐구", "keywords": ["과학관", "과학체험", "탐구", "발명"], "neg": ""},
}
# 10/20 테마 세분화: 이전 9종 -> 13종. 예전 풀(matched_keywords)을 새 테마로 옮기는 규칙(keyword -> theme)
LEGACY_KW = {"동화": "princess", "공주": "princess", "드레스": "princess", "백설공주": "princess",
             "뽀로로": "character", "티니핑": "character", "타요": "character", "핑크퐁": "character", "애니메이션": "character", "캐릭터": "character", "키즈월드": "character", "인형극": "character",
             "동물원": "animal", "목장": "animal", "알파카": "animal", "승마체험": "animal", "승마": "animal", "사파리": "animal",
             "아쿠아리움": "fish", "수족관": "fish", "민물고기": "fish", "해양생물": "fish",
             "천문대": "space", "우주": "space", "항공우주": "space", "별자리": "space", "플라네타륨": "space",
             "어린이박물관": "museum", "역사박물관": "museum", "민속촌": "museum", "고궁": "museum",
             "과학관": "science", "과학체험": "science", "탐구": "science", "발명": "science"}
UNCHANGED = ("dinosaur", "transport", "art", "cooking", "nature", "picnic")


def remap_pool(old):
    """이전 풀({theme:{items}}) -> 새 13개 테마. 유지 테마는 그대로, 나뉜 테마(공주·캐릭터, 동물, 과학 계열)는 matched_keywords 로 다시 배정한다. 매핑 규칙에 없는 키워드만 가진 항목은 버린다."""
    new = {}
    for tid, v in old.items():
        for it in v.get("items", []):
            if tid in UNCHANGED:
                targets = {tid: it.get("matched_keywords", [])}
            else:
                targets = {}
                for kw in it.get("matched_keywords", []):
                    nt = LEGACY_KW.get(kw)
                    if nt:
                        targets.setdefault(nt, []).append(kw)
            for nt, kws in targets.items():
                slot = new.setdefault(nt, {"label": THEMES[nt]["label"], "keywords": list(THEMES[nt]["keywords"]), "items": []})
                ex = next((o for o in slot["items"] if o["contentid"] == it.get("contentid")), None)
                if ex:
                    ex["matched_keywords"] = sorted(set(ex["matched_keywords"]) | set(kws))
                else:
                    slot["items"].append(dict(it, matched_keywords=sorted(set(kws))))
    for nt, slot in new.items():
        slot["items"].sort(key=lambda i: (0 if i.get("photo") else 1, i["title"]))
    return {nt: new[nt] for nt in THEMES if nt in new}


# 모든 테마 공통 제외: 야영장·캠핑장·펜션·골프장 등 숙박/레저 시설
GLOBAL_NEG = re.compile(r"캠핑|야영장|펜션|골프|워케이션")
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
    if GLOBAL_NEG.search(title) or (t["neg"] and re.search(t["neg"], title + " " + (raw.get("addr1") or ""))):
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


class CachedBudget:
    """TourAPI 원본 응답을 디렉터리에 저장·재사용한다(--cache-dir). 캐시에 있는 요청은 호출 한도를 쓰지 않는다 -> 키워드·제외 규칙 튜닝을 오프라인으로 반복할 수 있다."""
    def __init__(self, inner, cache_dir):
        self.inner, self.dir, self.hits = inner, Path(cache_dir), 0
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def used(self):
        return self.inner.used

    def call(self, key, endpoint, params):
        import hashlib
        fn = self.dir / (hashlib.md5((endpoint + json.dumps(params, sort_keys=True, ensure_ascii=False)).encode("utf-8")).hexdigest() + ".json")
        if fn.exists():
            self.hits += 1
            return json.loads(fn.read_text(encoding="utf-8"))
        items = self.inner.call(key, endpoint, params)
        if items is not None:
            fn.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        return items


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
        th = j.get("themes", {}) if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}
    if any(k in th for k in ("science",)) and not any(k in th for k in ("fish", "space", "museum", "character")) and any(k in th for k in ("princess", "animal")):
        return remap_pool(th)          # 10/19 이전 9종 구조 -> 13종으로 자동 이동
    return th


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
    ap.add_argument("--cache-dir", default="", help="원본 응답 캐시 폴더(있으면 같은 요청은 호출하지 않는다)")
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
    if a.cache_dir:
        budget = CachedBudget(budget, a.cache_dir)
    try:
        stats = scan(budget, key, ids, pool, known_places(PROJECT), today, tc.region_of)
    except es.QuotaStop as e:
        log.warning("중단: %s", e)
        stats = {"calls": budget.used}
    except Exception as e:   # 어떤 오류도 야간 흐름을 막지 않는다(타입명만 기록)
        log.error("수집 오류: %s", type(e).__name__)
        return 0
    log.info("호출 %s회%s · 테마별 %s · %s", budget.used, (" (캐시 %s건 재사용)" % budget.hits) if a.cache_dir else "", summary(pool), "dry-run(파일 미작성)" if a.dry_run else a.out)
    if not a.dry_run:
        write_pool(a.out, pool, today)
    else:
        for tid, v in pool.items():
            log.info("[%s] %s곳 표본: %s", tid, len(v["items"]), ", ".join(i["title"] for i in v["items"][:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
