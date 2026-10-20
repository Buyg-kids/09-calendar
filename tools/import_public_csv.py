"""공공데이터포털 표준데이터(CSV) -> 테마 풀 흡수기.  python tools/import_public_csv.py --kind forest|museum|park --csv <파일> [--label "데이터셋 이름"] [--data-date YYYY-MM-DD] [--out theme_spots_pool.json] [--dry-run]

 - kind forest: 유아숲체험원 표준데이터 -> 테마 nature(숲·모래놀이)
 - kind museum: 박물관·미술관 표준데이터 -> 이름 키워드로 dinosaur/transport/space/science/art (단서가 없으면 museum)
 - kind park  : 어린이공원 표준데이터 -> 동네 놀이터가 풀을 덮지 않도록 '큰 공원/체험·생태·물놀이' 이름이거나 면적이 일정 이상(--min-area, 기본 5000㎡)일 때만 nature
인코딩(utf-8-sig/cp949)을 자동 판별하고, 열 이름은 별칭 표로 찾는다(데이터셋마다 열 이름이 조금씩 달라서). 좌표(위도·경도)가 없는 행은 건너뛴다(주소로 좌표를 만드는 단계는 따로).
출처 표기: provenance 에 데이터셋 이름·기준일을 적고, 이용 조건은 '데이터셋 페이지에서 확인 필요'로 둔다(단정하지 않음). 사진은 없다(카드는 이모지/손그림 폴백).
로그 규칙: 예외는 타입명만 남긴다(Traceback 금지). 인증키 불필요."""
import argparse
import csv
import io
import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

PROJECT = Path(os.environ.get("BUYG_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "tools"))
import collect_theme_pool as ctp  # noqa: E402

log = logging.getLogger("import_public_csv")
ALIAS = {
    "name": ["유아숲체험원명", "시설명", "체험원명", "박물관명", "박물관미술관명", "공원명", "명칭", "시설명(명칭)"],
    "road": ["소재지도로명주소", "도로명주소", "소재지도로명 주소", "주소"],
    "jibun": ["소재지지번주소", "지번주소", "소재지지번 주소"],
    "lat": ["위도", "latitude", "위도(WGS84)", "위도(y)"],
    "lon": ["경도", "longitude", "경도(WGS84)", "경도(x)"],
    "phone": ["전화번호", "관리기관전화번호", "운영기관전화번호"],
    "operator": ["관리기관명", "운영기관명", "운영기관", "관리기관"],
    "site": ["홈페이지주소", "홈페이지", "홈페이지URL"],
    "area": ["면적", "공원면적", "공원면적(㎡)", "시설면적"],
    "date": ["데이터기준일자", "기준일자", "데이터기준일"],
    "type": ["공원구분", "구분", "시설구분"],
}
MUSEUM_THEME = [("dinosaur", re.compile(r"공룡|화석|자연사|지질|쥐라기")), ("transport", re.compile(r"철도|기차|자동차|교통|소방")),
                ("space", re.compile(r"천문|우주|항공|비행|별자리|플라네타륨")), ("science", re.compile(r"과학|발명|탐구")),
                ("art", re.compile(r"미술|아트|공예|도자|판화|드로잉"))]
PARK_BIG = re.compile(r"테마|생태|체험|물놀이|수목|식물|숲|호수|해양|랜드|어린이대공원")


def read_rows(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("encoding")
    return list(csv.DictReader(io.StringIO(text)))


def col(row, key):
    """별칭 표로 열을 찾는다(공백 제거 후 비교)."""
    names = {re.sub(r"\s+", "", k or ""): k for k in row.keys()}
    for a in ALIAS[key]:
        k = names.get(re.sub(r"\s+", "", a))
        if k is not None and (row.get(k) or "").strip():
            return row[k].strip()
    return ""


def to_area(v):
    try:
        return float(re.sub(r"[^\d.]", "", v)) if v else 0.0
    except ValueError:
        return 0.0


def theme_of(kind, name, area, min_area, ptype=""):
    """(테마 id 또는 None). 순수 함수(테스트 대상)."""
    if kind == "forest":
        return "nature"
    if kind == "museum":
        for tid, rx in MUSEUM_THEME:
            if rx.search(name):
                return tid
        return "museum"
    if kind == "park":
        if PARK_BIG.search(name) or area >= min_area or (re.search(r"근린|체육|역사|문화|수변", ptype or "") and area >= min_area / 2):
            return "nature"
        return None
    return None


def to_item(row, kind, theme, label, data_date, today, region_of):
    name = col(row, "name")
    lat, lon = ctp.to_float(col(row, "lat")), ctp.to_float(col(row, "lon"))
    address = col(row, "road") or col(row, "jibun")
    if not name or lat is None or lon is None or not (33.0 <= lat <= 39.0 and 124.0 <= lon <= 132.0) or ctp.ADULT_RE.search(name):
        return None
    region = region_of("", address, "")
    if not region:
        return None
    it = {"id": "pub_" + re.sub(r"\W+", "", kind + norm_key(name + address))[:40], "contentid": "", "title": name, "address": address, "region": region,
          "geo": {"lat": round(lat, 6), "lon": round(lon, 6)}, "category": {"forest": "유아숲체험원", "museum": "박물관·미술관", "park": "공원"}[kind], "photo": None,
          "matched_keywords": [], "provenance": "data.go.kr", "source": label, "data_date": data_date or col(row, "date") or "",
          "license_note": "공공데이터포털 표준데이터 - 이용 조건은 데이터셋 페이지에서 확인 필요", "fetched_at": today, "verified": False}
    for k, f in (("phone", "phone"), ("operator", "operator"), ("homepage", "site")):
        v = col(row, f)
        if v:
            it[k] = v
    if kind == "forest":
        for k, f in (("operating_period", "운영기간"), ("how_to_join", "참여방법")):
            v = (row.get(f) or "").strip()
            if v:
                it[k] = v
    return it


class GeocodeStop(Exception):
    pass


def kakao_geocode(session, key, address, name, cache, region_of, delay=0.05):
    """주소(없으면 시설명 키워드) -> {'lat','lon','via'} 또는 None. 결과의 시·도가 CSV 주소와 다르면 버린다(오탐 방지). 인증·쿼터 오류는 GeocodeStop."""
    import time
    if address in cache:
        return cache[address]
    hdr = {"Authorization": "KakaoAK " + key}
    want = region_of("", address, "")
    out = None

    def docs_of(url, q):
        r = session.get(url, params={"query": q, "size": 5}, headers=hdr, timeout=10)
        time.sleep(delay)
        if r.status_code in (401, 403, 429):
            raise GeocodeStop("HTTP %s" % r.status_code)
        return r.json().get("documents", []) if r.status_code == 200 else []

    for url, q, field in (("https://dapi.kakao.com/v2/local/search/address.json", address, "address_name"), ("https://dapi.kakao.com/v2/local/search/keyword.json", ((address.split(" ")[:2] and " ".join(address.split(" ")[:2])) + " " + name).strip(), "address_name")):
        try:
            docs = docs_of(url, q)
        except GeocodeStop:
            raise
        except Exception as e:   # 네트워크 오류는 이 주소만 건너뜀(타입명만 기록)
            log.warning("지오코딩 오류: %s", type(e).__name__)
            docs = []
        for d in docs:
            got = region_of("", d.get(field) or "", "")
            lat, lon = ctp.to_float(d.get("y")), ctp.to_float(d.get("x"))
            if lat is not None and lon is not None and got and got == want:
                out = {"lat": lat, "lon": lon, "via": "address" if "address.json" in url else "keyword"}
                break
        if out:
            break
    cache[address] = out
    return out


def geocode_rows(rows, key, cache, region_of):
    """좌표 열이 없는 행에 위도·경도를 채운다. 반환: (성공 수, 실패 수)."""
    import requests
    ok = bad = 0
    s = requests.Session()
    for row in rows:
        if col(row, "lat") and col(row, "lon"):
            continue
        addr, name = col(row, "road") or col(row, "jibun"), col(row, "name")
        if not addr:
            bad += 1
            continue
        g = kakao_geocode(s, key, addr, name, cache, region_of)
        if g:
            row["위도"], row["경도"] = str(g["lat"]), str(g["lon"])
            ok += 1
        else:
            bad += 1
    return ok, bad


def norm_key(s):
    return re.sub(r"\s+", "", s)


def ingest(rows, kind, label, data_date, pool, known, today, region_of, min_area=5000):
    stats = {"rows": len(rows), "kept": 0, "no_theme": 0, "invalid": 0, "dup": 0}
    for row in rows:
        name = col(row, "name")
        theme = theme_of(kind, name, to_area(col(row, "area")), min_area, col(row, "type"))
        if theme is None:
            stats["no_theme"] += 1
            continue
        it = to_item(row, kind, theme, label, data_date, today, region_of)
        if it is None:
            stats["invalid"] += 1
            continue
        slot = pool.setdefault(theme, {"label": ctp.THEMES[theme]["label"], "keywords": list(ctp.THEMES[theme]["keywords"]), "items": []})
        if any(o["id"] == it["id"] for o in slot["items"]) or ctp.is_dup(it, slot["items"], known):
            stats["dup"] += 1
            continue
        slot["items"].append(it)
        stats["kept"] += 1
    for v in pool.values():
        v["items"].sort(key=lambda i: (0 if i.get("photo") else 1, i["title"]))
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=["forest", "museum", "park"])
    ap.add_argument("--csv", required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--data-date", default="")
    ap.add_argument("--min-area", type=float, default=5000)
    ap.add_argument("--out", default=str(PROJECT / "theme_spots_pool.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--geocode", action="store_true", help="좌표 열이 없으면 .env 의 KAKAO_REST_API_KEY 로 주소를 좌표로 바꾼다(실패한 주소는 건너뜀, 결과는 data/geocode_cache.json 에 캐시)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from collector import tour_collector as tc
    try:
        rows = read_rows(a.csv)
    except (OSError, ValueError) as e:
        log.error("CSV 읽기 실패: %s", type(e).__name__)
        return 1
    today = date.today().isoformat()
    if a.geocode:
        from dotenv import load_dotenv
        load_dotenv(PROJECT / ".env")
        key = os.environ.get("KAKAO_REST_API_KEY") or ""
        if not key:
            log.error("KAKAO_REST_API_KEY 가 .env 에 없습니다")
            return 1
        cache_path = PROJECT / "data" / "geocode_cache.json"
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        try:
            ok, bad = geocode_rows(rows, key, cache, tc.region_of)
            log.info("지오코딩 성공 %s · 실패(건너뜀) %s", ok, bad)
        except GeocodeStop as e:
            log.warning("지오코딩 중단(인증·쿼터 신호): %s - 좌표를 얻은 행만 사용", e)
        finally:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    pool = ctp.load_pool(a.out)
    label = a.label or {"forest": "전국 유아숲체험원 표준데이터", "museum": "전국 박물관·미술관 표준데이터", "park": "전국 어린이공원 표준데이터"}[a.kind]
    st = ingest(rows, a.kind, label, a.data_date, pool, ctp.known_places(PROJECT), today, tc.region_of, a.min_area)
    log.info("%s · 행 %s · 추가 %s · 테마 없음 %s · 좌표/지역 불량 %s · 중복 %s · 테마별 %s", a.kind, st["rows"], st["kept"], st["no_theme"], st["invalid"], st["dup"], ctp.summary(pool))
    if not a.dry_run:
        ctp.write_pool(a.out, pool, today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
