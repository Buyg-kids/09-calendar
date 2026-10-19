"""tools/collect_theme_pool.py · tools/import_public_csv.py 단위 테스트(네트워크 없음).  python -m unittest tests.test_collect_theme_pool -v"""
import csv
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import collect_theme_pool as ctp  # noqa: E402
import import_public_csv as ipc  # noqa: E402


def region(area, addr, ld=""):   # tour_collector.region_of 의 단순판(테스트용)
    first = (addr or "").split(" ")[0]
    code = {"11": "서울", "41": "경기", "26": "부산", "50": "제주"}.get(str(ld)[:2], "")
    byaddr = {"서울특별시": "서울", "서울": "서울", "경기도": "경기", "경기": "경기", "부산광역시": "부산", "부산": "부산", "제주특별자치도": "제주", "제주": "제주"}.get(first, "")
    if code and byaddr and code != byaddr:
        return ""
    return code or byaddr


def raw(**kw):
    d = {"contentid": "100", "contenttypeid": "12", "title": "공룡박물관", "addr1": "경기도 수원시", "mapx": "127.0", "mapy": "37.3", "lDongRegnCd": "41", "firstimage": "", "cpyrhtDivCd": ""}
    d.update(kw)
    return d


class FakeBudget:
    def __init__(self, data):
        self.data, self.used = data, 0

    def call(self, key, endpoint, params):
        self.used += 1
        return self.data.get((params["keyword"], params["pageNo"]), [])


class TestCandidate(unittest.TestCase):
    def test_keyword_must_be_in_title(self):
        self.assertIsNotNone(ctp.candidate(raw(), "dinosaur", "공룡", "2026-10-19", region))
        self.assertIsNone(ctp.candidate(raw(title="자연사박물관"), "dinosaur", "공룡", "2026-10-19", region))     # 키워드가 제목에 없음

    def test_types_adult_geo_region_filters(self):
        def c(**k):
            return ctp.candidate(raw(**k), "dinosaur", "공룡", "2026-10-19", region)
        self.assertIsNone(c(contenttypeid="39"))                  # 음식점 제외
        self.assertIsNone(c(title="공룡 나이트클럽"))              # 성인 업종
        self.assertIsNone(c(mapx="", mapy=""))                    # 좌표 없음
        self.assertIsNone(c(mapx="10", mapy="37"))                # 한국 밖
        self.assertIsNone(c(addr1="서울특별시 종로구", lDongRegnCd="41"))   # 코드와 주소가 어긋남

    def test_negative_rule_princess(self):
        def c(t, a="경기도"):
            return ctp.candidate(raw(title=t, addr1=a), "princess", "캐릭터", "2026-10-19", region)
        self.assertIsNotNone(c("뽀로로 캐릭터파크"))
        self.assertIsNone(c("캐릭터 국립공주박물관"))
        self.assertIsNone(c("캐릭터 체험", "충남 공주시 반포면"))

    def test_photo_only_type1_visitkorea(self):
        ok = ctp.candidate(raw(firstimage="http://tong.visitkorea.or.kr/a.jpg", cpyrhtDivCd="Type1"), "dinosaur", "공룡", "2026-10-19", region)
        self.assertEqual(ok["photo"]["license"], "공공누리 제1유형(출처표시)")
        self.assertTrue(ok["photo"]["url"].startswith("https://"))
        self.assertIsNone(ctp.candidate(raw(firstimage="https://tong.visitkorea.or.kr/a.jpg", cpyrhtDivCd="Type3"), "dinosaur", "공룡", "2026-10-19", region)["photo"])
        self.assertIsNone(ctp.candidate(raw(firstimage="https://evil.example/a.jpg", cpyrhtDivCd="Type1"), "dinosaur", "공룡", "2026-10-19", region)["photo"])


class TestScan(unittest.TestCase):
    def test_scan_merges_dedupes_and_pages(self):
        d = {("공룡", 1): [raw(contentid="1", title="공룡박물관"), raw(contentid="2", title="공룡 화석 체험관", addr1="경기도 안양시", mapx="127.1"), raw(contentid="1", title="공룡박물관")],
             ("화석", 1): [raw(contentid="2", title="공룡 화석 체험관", addr1="경기도 안양시", mapx="127.1"), raw(contentid="3", title="화석전시관", mapx="128.0")]}
        pool, known = {}, [{"title": "화석전시관", "geo": {"lat": 37.3, "lon": 128.0}, "contentid": ""}]
        st = ctp.scan(FakeBudget(d), "k", ["dinosaur"], pool, known, "2026-10-19", region)
        items = pool["dinosaur"]["items"]
        self.assertEqual(sorted(i["title"] for i in items), ["공룡 화석 체험관", "공룡박물관"])   # 3은 시드와 같은 이름·위치라 제외, 1·2는 한 번씩만
        self.assertEqual(sorted(items[0]["matched_keywords"] + items[1]["matched_keywords"]), ["공룡", "공룡", "화석"])
        self.assertGreaterEqual(st["dup"], 1)

    def test_known_name_far_away_is_not_duplicate(self):
        item = ctp.candidate(raw(contentid="9", title="공룡박물관", mapx="129.0"), "dinosaur", "공룡", "2026-10-19", region)
        self.assertFalse(ctp.is_dup(item, [], [{"title": "공룡박물관", "geo": {"lat": 37.3, "lon": 127.0}, "contentid": ""}]))   # 이름이 같아도 멀면 다른 곳

    def test_write_and_load_roundtrip(self):
        pool = {"art": {"label": "x", "keywords": [], "items": [{"id": "pool_1", "contentid": "1", "title": "t", "geo": {"lat": 37, "lon": 127}, "matched_keywords": [], "photo": None}]}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pool.json"
            ctp.write_pool(p, pool, "2026-10-19")
            self.assertEqual(ctp.load_pool(p)["art"]["items"][0]["id"], "pool_1")
            self.assertEqual(ctp.load_pool(Path(d) / "none.json"), {})


class TestCsv(unittest.TestCase):
    def _csv(self, header, rows, enc="utf-8-sig"):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        f = tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False)
        f.write(buf.getvalue().encode(enc))
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_forest_cp949_and_filters(self):
        p = self._csv(["유아숲체험원명", "소재지도로명주소", "위도", "경도", "관리기관명"],
                      [["가나유아숲", "경기도 용인시 처인구", "37.2", "127.2", "용인시"], ["좌표없음숲", "경기도 용인시", "", "", "용인시"], ["부산숲", "부산광역시 해운대구", "35.2", "129.1", "부산시"]], enc="cp949")
        rows = ipc.read_rows(p)
        pool = {}
        st = ipc.ingest(rows, "forest", "유아숲 표준데이터", "2026-01-01", pool, [], "2026-10-19", region)
        self.assertEqual((st["kept"], st["invalid"]), (2, 1))
        it = pool["nature"]["items"][0]
        self.assertEqual((it["provenance"], it["source"], it["photo"]), ("data.go.kr", "유아숲 표준데이터", None))
        self.assertIn("확인 필요", it["license_note"])

    def test_museum_theme_by_name_and_park_filter(self):
        self.assertEqual(ipc.theme_of("museum", "철도박물관", 0, 5000), "transport")
        self.assertEqual(ipc.theme_of("museum", "공룡화석전시관", 0, 5000), "dinosaur")
        self.assertEqual(ipc.theme_of("museum", "OO향토박물관", 0, 5000), "science")
        self.assertIsNone(ipc.theme_of("park", "행복어린이공원", 1200, 5000))               # 동네 놀이터는 풀에 넣지 않는다
        self.assertEqual(ipc.theme_of("park", "OO생태공원", 800, 5000), "nature")
        self.assertEqual(ipc.theme_of("park", "큰어린이공원", 9000, 5000), "nature")

    def test_dedupe_against_known_and_second_import(self):
        p = self._csv(["시설명", "소재지도로명주소", "위도", "경도"], [["철도박물관", "경기도 의왕시", "37.3", "126.9"]])
        rows = ipc.read_rows(p)
        pool = {}
        self.assertEqual(ipc.ingest(rows, "museum", "박물관", "", pool, [], "2026-10-19", region)["kept"], 1)
        self.assertEqual(ipc.ingest(rows, "museum", "박물관", "", pool, [], "2026-10-19", region)["dup"], 1)        # 두 번 넣어도 한 번만
        known = [{"title": "철도박물관", "geo": {"lat": 37.3, "lon": 126.9}, "contentid": ""}]
        self.assertEqual(ipc.ingest(rows, "museum", "박물관", "", {}, known, "2026-10-19", region)["dup"], 1)


class FakeResp:
    def __init__(self, status, docs):
        self.status_code, self._d = status, docs

    def json(self):
        return {"documents": self._d}


class FakeSession:
    """주소/키워드 검색 응답을 흉내 낸다(네트워크 없음)."""
    def __init__(self, addr_docs=None, kw_docs=None, status=200):
        self.addr_docs, self.kw_docs, self.status, self.calls = addr_docs or [], kw_docs or [], status, 0

    def get(self, url, params=None, headers=None, timeout=0):
        self.calls += 1
        return FakeResp(self.status, self.addr_docs if "address.json" in url else self.kw_docs)


class TestGeocode(unittest.TestCase):
    def test_address_hit_and_cache(self):
        s, cache = FakeSession(addr_docs=[{"x": "127.05", "y": "37.65", "address_name": "서울 노원구 상계동 산1-2"}]), {}
        g = ipc.kakao_geocode(s, "KEY", "서울특별시 노원구 상계동 산1-2", "수락산 유아숲체험원", cache, region, delay=0)
        self.assertEqual((g["lat"], g["lon"], g["via"]), (37.65, 127.05, "address"))
        ipc.kakao_geocode(s, "KEY", "서울특별시 노원구 상계동 산1-2", "수락산 유아숲체험원", cache, region, delay=0)
        self.assertEqual(s.calls, 1)                                             # 캐시: 두 번째는 호출하지 않는다

    def test_keyword_fallback_and_region_mismatch_rejected(self):
        s = FakeSession(addr_docs=[], kw_docs=[{"x": "126.9", "y": "37.4", "address_name": "서울 관악구 어딘가"}])
        self.assertEqual(ipc.kakao_geocode(s, "KEY", "서울특별시 노원구 산1", "수락산", {}, region, delay=0)["via"], "keyword")
        bad = FakeSession(addr_docs=[{"x": "129.0", "y": "35.1", "address_name": "부산 해운대구 우동"}])
        self.assertIsNone(ipc.kakao_geocode(bad, "KEY", "서울특별시 노원구 산1", "수락산", {}, region, delay=0))   # 시·도가 다르면 버린다

    def test_auth_error_stops(self):
        with self.assertRaises(ipc.GeocodeStop):
            ipc.kakao_geocode(FakeSession(status=401), "KEY", "서울특별시 노원구 산1", "수락산", {}, region, delay=0)

    def test_forest_keeps_period_and_method(self):
        row = {"시설명": "가나유아숲체험원", "주소": "서울특별시 노원구 상계동 산1-2", "운영기간": "2025-03~2025-11", "전화번호": "02-000-0000", "참여방법": "전화/방문", "위도": "37.6", "경도": "127.1"}
        it = ipc.to_item(row, "forest", "nature", "라벨", "2025-11-30", "2026-10-19", region)
        self.assertEqual((it["operating_period"], it["how_to_join"], it["phone"]), ("2025-03~2025-11", "전화/방문", "02-000-0000"))


if __name__ == "__main__":
    unittest.main()
