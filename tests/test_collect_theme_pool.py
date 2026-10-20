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

    def test_noise_rules_from_first_scan(self):
        """1차 스캔(10/19) 오탐: 테마파크 일반명, 동화사, 야영장, 자동차극장, 목장길, 화석정, 곤충체험농장."""
        def ok(theme, kw, title, addr="경기도"):
            return ctp.candidate(raw(title=title, addr1=addr), theme, kw, "2026-10-19", region) is not None
        self.assertNotIn("테마파크", ctp.THEMES["princess"]["keywords"])
        self.assertFalse(ok("princess", "동화", "동화사(대구)"))
        self.assertTrue(ok("princess", "동화", "송월동 동화마을"))
        self.assertFalse(ok("transport", "자동차", "달궁자동차야영장"))
        self.assertFalse(ok("transport", "자동차", "함평자동차극장"))
        self.assertTrue(ok("transport", "철도", "의왕철도박물관"))
        self.assertFalse(ok("animal", "목장", "구두산목장길"))
        self.assertTrue(ok("animal", "목장", "대관령양떼목장"))
        self.assertFalse(ok("dinosaur", "화석", "화석정"))
        self.assertFalse(ok("dinosaur", "자연사", "자연사랑미술관"))
        self.assertFalse(ok("cooking", "체험농장", "세산곤충체험농장"))
        self.assertTrue(ok("cooking", "딸기체험", "양수리딸기체험농장"))
        self.assertFalse(ok("nature", "생태공원", "문암생태공원캠핑장"))


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
        self.assertEqual(ipc.theme_of("museum", "OO향토박물관", 0, 5000), "museum")
        self.assertEqual(ipc.theme_of("museum", "OO천문과학관", 0, 5000), "space")
        self.assertEqual(ipc.theme_of("museum", "OO과학박물관", 0, 5000), "science")
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


class TestCache(unittest.TestCase):
    def test_cached_budget_reuses_responses_without_calls(self):
        class Inner:
            used = 0
            def call(self, key, endpoint, params):
                Inner.used += 1
                return [{"contentid": "1", "kw": params["keyword"]}]
        with tempfile.TemporaryDirectory() as d:
            b = ctp.CachedBudget(Inner(), d)
            first = b.call("k", "searchKeyword2", {"keyword": "공룡", "pageNo": 1})
            again = b.call("k", "searchKeyword2", {"keyword": "공룡", "pageNo": 1})
            other = b.call("k", "searchKeyword2", {"keyword": "화석", "pageNo": 1})
            self.assertEqual(first, again)
            self.assertEqual((Inner.used, b.hits), (2, 1))           # 같은 요청은 한 번만 호출, 다른 요청은 호출
            self.assertEqual(other[0]["kw"], "화석")

    def test_expanded_keywords_present(self):
        self.assertIn("핑크퐁", ctp.THEMES["character"]["keywords"])
        self.assertIn("증기기관차", ctp.THEMES["transport"]["keywords"])
        self.assertNotIn("안전체험관", ctp.THEMES["transport"]["keywords"])   # 119안전체험관은 교통이 아님
        self.assertIn("도예촌", ctp.THEMES["art"]["keywords"])
        self.assertNotIn("공방", ctp.THEMES["art"]["keywords"])      # 매듭·떡 공방 등 성인 공방이 섞여 뺌
        self.assertIsNone(ctp.candidate(raw(title="수원버스터미널 열차", addr1="경기도 수원시"), "transport", "열차", "2026-10-19", region))


class TestThemeSplit(unittest.TestCase):
    def test_thirteen_themes_and_user_keywords(self):
        self.assertEqual(list(ctp.THEMES), ["princess", "character", "dinosaur", "transport", "art", "cooking", "animal", "fish", "nature", "picnic", "museum", "space", "science"])
        self.assertEqual(ctp.THEMES["princess"]["keywords"], ["공주", "드레스", "동화", "백설공주"])
        self.assertEqual(ctp.THEMES["character"]["keywords"][:7], ["뽀로로", "티니핑", "타요", "핑크퐁", "애니메이션", "캐릭터", "키즈월드"])   # 사용자 지정 7개 + 테디베어·헬로키티·레고랜드·인형박물관
        self.assertEqual(ctp.THEMES["fish"]["keywords"], ["아쿠아리움", "수족관", "민물고기", "해양생물"])
        self.assertEqual(ctp.THEMES["animal"]["keywords"], ["동물원", "목장", "알파카", "승마체험", "사파리"])   # 승마(45곳 승마장)는 동물 테마를 덮어 승마체험으로 좁힘
        self.assertEqual(ctp.THEMES["space"]["keywords"], ["천문대", "우주", "항공우주", "별자리", "플라네타륨"])
        self.assertEqual(ctp.THEMES["museum"]["keywords"], ["어린이박물관", "역사박물관", "민속촌", "고궁"])

    def test_gongju_place_names_still_excluded(self):
        def ok(title, addr="경기도"):
            return ctp.candidate(raw(title=title, addr1=addr), "princess", "공주", "2026-10-20", region) is not None
        self.assertTrue(ok("백설공주 드레스 체험관"))
        self.assertFalse(ok("공주시 어린이 공원", "충남 공주시"))
        self.assertFalse(ok("국립공주박물관"))

    def test_remap_old_pool_to_new_themes(self):
        def it(cid, title, kws):
            return {"id": "pool_" + cid, "contentid": cid, "title": title, "photo": None, "matched_keywords": kws}
        old = {"princess": {"items": [it("1", "동화마을", ["동화"]), it("2", "뽀로로파크", ["뽀로로"]), it("3", "애니박물관", ["애니메이션", "동화"])]},
               "animal": {"items": [it("4", "아쿠아A", ["아쿠아리움"]), it("5", "동물원B", ["동물원"]), it("6", "목장C", ["목장", "승마체험"])]},
               "science": {"items": [it("7", "천문대D", ["천문대"]), it("8", "어린이박물관E", ["어린이박물관"]), it("9", "과학관F", ["과학관"]), it("10", "뜻모를", ["없는키워드"])]},
               "dinosaur": {"items": [it("11", "공룡G", ["공룡"])]}}
        new = ctp.remap_pool(old)
        titles = {k: sorted(i["title"] for i in v["items"]) for k, v in new.items()}
        self.assertEqual(titles["princess"], ["동화마을", "애니박물관"])
        self.assertEqual(titles["character"], ["뽀로로파크", "애니박물관"])      # 키워드가 둘이면 두 테마에 모두
        self.assertEqual(titles["fish"], ["아쿠아A"]); self.assertEqual(titles["animal"], ["동물원B", "목장C"])
        self.assertEqual((titles["space"], titles["museum"], titles["science"]), (["천문대D"], ["어린이박물관E"], ["과학관F"]))
        self.assertEqual(titles["dinosaur"], ["공룡G"])
        self.assertNotIn("뜻모를", sum(titles.values(), []))                       # 매핑 규칙이 없는 항목은 버린다
        self.assertEqual(list(new), [t for t in ctp.THEMES if t in new])          # 정해진 테마 순서

    def test_load_pool_migrates_legacy_file(self):
        legacy = {"schema_version": 1, "themes": {"princess": {"items": [{"id": "pool_1", "contentid": "1", "title": "뽀로로파크", "photo": None, "matched_keywords": ["뽀로로"]}]}, "science": {"items": []}, "animal": {"items": []}}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pool.json"
            p.write_text(__import__("json").dumps(legacy, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(list(ctp.load_pool(p)), ["character"])                # 옛 9종 파일은 읽을 때 13종으로 옮겨진다


class TestAnchorDiscovery(unittest.TestCase):
    def test_kid_score_orders_flagships_and_excludes_noise(self):
        import discover_anchor_area as dis
        self.assertEqual(dis.kid_score("OO체육공원"), -1)
        self.assertEqual(dis.kid_score("달빛캠핑장"), -1)
        self.assertGreater(dis.kid_score("에버랜드", True), dis.kid_score("동네공원", True))
        self.assertGreater(dis.kid_score("한국민속촌", True), dis.kid_score("OO어린이도서관", True))   # 도서관은 감점
        self.assertGreater(dis.kid_score("경기도어린이박물관", True, "14"), dis.kid_score("아무개박물관", True, "14"))

    def test_pick_top_per_city_and_geo(self):
        import discover_anchor_area as dis
        raw = {"수원시": {"1": {"contentid": "1", "title": "수원화성박물관", "addr1": "경기도 수원시", "mapx": "127.0", "mapy": "37.3", "firstimage": "x", "contenttypeid": "14"},
                          "2": {"contentid": "2", "title": "OO체육공원", "addr1": "경기도 수원시", "mapx": "127.0", "mapy": "37.3", "contenttypeid": "12"},
                          "3": {"contentid": "3", "title": "아이랑농장", "addr1": "경기도 수원시", "mapx": "bad", "mapy": "37.3", "contenttypeid": "12"}}}
        rows = dis.pick(raw, top=5)["수원시"]
        self.assertEqual([r["title"] for r in rows], ["수원화성박물관", "아이랑농장"])        # 체육공원은 제외
        self.assertIsNone(rows[1]["geo"])
        self.assertEqual(rows[0]["geo"], {"lat": 37.3, "lon": 127.0})


if __name__ == "__main__":
    unittest.main()
