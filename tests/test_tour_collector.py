"""TourAPI 수집기 단위 테스트 (네트워크 없음). 실행: python -m unittest tests.test_tour_collector -v

※ SAMPLE 응답은 공식 문서 기준으로 가정한 형태다. 키 발급 후 첫 드라이런에서 실제 응답과 대조할 것.
"""
import os
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector import tour_collector as tc  # noqa: E402

TODAY = date(2026, 10, 10)

SAMPLE = {"response": {"header": {"resultCode": "0000"}, "body": {"items": {"item": [
    {"contentid": "1001", "title": "어린이 가족 동화 축제", "addr1": "서울특별시 종로구 세종대로 1", "areacode": "1",
     "mapx": "126.9769", "mapy": "37.5759", "eventstartdate": "20261012", "eventenddate": "20261014", "modifiedtime": "20261001"},
    {"contentid": "1002", "title": "전국 막걸리 페스타", "addr1": "경기도 수원시 팔달구", "areacode": "31",
     "mapx": "127.0", "mapy": "37.28", "eventstartdate": "20261012", "eventenddate": "20261013"},
], }, "totalCount": 2}}}


class TestRegion(unittest.TestCase):
    def test_code(self):
        self.assertEqual(tc.region_of("1", "서울특별시 종로구"), "서울")
        self.assertEqual(tc.region_of("39", ""), "제주")
        self.assertEqual(tc.region_of("32", "강원특별자치도 춘천시"), "강원")
        self.assertEqual(tc.region_of("35", "경상북도 경주시"), "경북")

    def test_ldong_code(self):
        # 실제 응답: areacode 는 비어 있고 lDongRegnCd 만 채워진다
        self.assertEqual(tc.region_of("", "서울특별시 송파구", "11"), "서울")
        self.assertEqual(tc.region_of("", "전남광주통합특별시 여수시", "12"), "광주")
        self.assertEqual(tc.region_of("", "세종특별자치시 한솔동", "36110"), "세종")
        self.assertEqual(tc.region_of("", "강원특별자치도 춘천시", "51"), "강원")
        self.assertEqual(tc.region_of("", "전북특별자치도 전주시", "52"), "전북")
        self.assertEqual(tc.region_of("", "경기도 수원시", "11"), "")      # 코드(서울)와 주소(경기) 불일치

    def test_address_only_and_missing(self):
        self.assertEqual(tc.region_of("", "부산광역시 해운대구"), "부산")
        self.assertEqual(tc.region_of("", ""), "")
        self.assertEqual(tc.region_of("99", ""), "")

    def test_mismatch_dropped(self):
        self.assertEqual(tc.region_of("1", "경기도 수원시"), "")   # 코드=서울, 주소=경기 -> 제외

    def test_all_codes_map_to_known_regions(self):
        self.assertEqual(len(set(tc.AREA_CODE_TO_REGION.values())), 17)


class TestFee(unittest.TestCase):
    def test_free(self):
        for t in ["무료", "전 프로그램 무료 입장", "입장료 없음", "FREE", "참가비 없음"]:
            self.assertEqual(tc.classify_fee(t), "free", t)

    def test_partial(self):
        self.assertEqual(tc.classify_fee("어린이 무료, 성인 5,000원"), "partial")
        self.assertEqual(tc.classify_fee("무료 (유료 체험 별도 3,000원)"), "partial")

    def test_entry_free_with_paid_extras(self):
        self.assertEqual(tc.classify_fee("입장료 무료 (일부 체험, 홍보판매 푸드트럭 등 유료)"), "free")
        self.assertEqual(tc.classify_fee("관람 무료, 일부 프로그램 유료"), "free")

    def test_paid(self):
        for t in ["10,000원", "유료", "성인 3000원 / 청소년 2000원", "무료 아님"]:
            self.assertEqual(tc.classify_fee(t), "paid", t)

    def test_unknown(self):
        for t in ["", None, "자세한 내용은 홈페이지 참조"]:
            self.assertEqual(tc.classify_fee(t), "unknown", t)


class TestKid(unittest.TestCase):
    def test_scores(self):
        self.assertGreaterEqual(tc.kid_score("어린이 가족 동화 축제"), 3)
        self.assertEqual(tc.kid_score("가을 국화 전시"), 0)
        self.assertIsNone(tc.kid_score("전국 막걸리 페스타"))
        self.assertIsNone(tc.kid_score("가족 와인 페스티벌"))     # 긍정 키워드가 있어도 성인 키워드가 우선


class TestLinks(unittest.TestCase):
    def test_naver_map_url_uses_address_not_title(self):
        u = tc.naver_map_url("서울특별시 송파구 올림픽로 300 (신천동)", "아이가 행복입니다 S9")
        self.assertTrue(u.startswith("https://map.naver.com/p/search/"))
        self.assertIn("%EC%98%AC%EB%A6%BC%ED%94%BD%EB%A1%9C%20300", u)      # 올림픽로 300
        self.assertNotIn("%EC%8B%A0%EC%B2%9C%EB%8F%99", u)                  # 괄호 속 동 이름 제거
        self.assertNotIn("S9", u)                                           # 행사명은 쓰지 않는다

    def test_naver_map_url_fallback_title(self):
        self.assertIn("%EA%B0%80%EB%9D%BD", tc.naver_map_url("", "가락 시장"))

    def test_clean_homepage(self):
        self.assertEqual(tc.clean_homepage("https://www.dsart.or.kr/"), "https://www.dsart.or.kr/")
        self.assertEqual(tc.clean_homepage("www.gngsctf.or.kr"), "https://www.gngsctf.or.kr")
        self.assertEqual(tc.clean_homepage('<a href="http://a.kr/x" target="_blank">링크</a>'), "http://a.kr/x")
        self.assertEqual(tc.clean_homepage("자세한 내용은 홈페이지 참조"), "")
        self.assertEqual(tc.clean_homepage("javascript:alert(1)"), "")
        self.assertEqual(tc.clean_homepage('<a href="javascript:void(0)">x</a>'), "")
        self.assertEqual(tc.clean_homepage(""), "")

    def test_event_has_info_url_field(self):
        raw = {"contentid": "7", "title": "어린이 축제", "addr1": "서울특별시 종로구 세종대로 1 (세종로)", "lDongRegnCd": "11",
               "mapx": "127.0", "mapy": "37.5", "eventstartdate": "20261012", "eventenddate": "20261013"}
        ev = tc.normalize_item(raw, TODAY, homepage="www.x.or.kr")
        self.assertEqual(ev["info_url"], "https://www.x.or.kr")
        self.assertIn("map.naver.com/p/search/", ev["link"])
        self.assertNotIn("%EC%96%B4%EB%A6%B0%EC%9D%B4", ev["link"])      # 제목('어린이')이 아니라 주소


class TestKidKeywordsAndSort(unittest.TestCase):
    def test_new_keywords(self):
        for title in ["곤충 체험 축제", "공룡 나라 대축제", "과학 놀이 한마당", "유성독서대전", "가을 퍼레이드", "숲속 음악회"]:
            self.assertGreaterEqual(tc.kid_score(title), 1, title)

    def test_not_a_cutoff(self):
        self.assertEqual(tc.kid_score("가을 국화 전시"), 0)            # 0점도 제외되지 않고 점수만 낮다
        ev = tc.normalize_item({"contentid": "9", "title": "가을 국화 전시", "addr1": "서울특별시 종로구", "lDongRegnCd": "11",
                                "mapx": "127.0", "mapy": "37.5", "eventstartdate": "20261012", "eventenddate": "20261013"}, TODAY)
        self.assertIsNotNone(ev)

    def test_sort(self):
        evs = [{"title": "b", "kid_score": 0, "start_date": "2026-10-11"}, {"title": "a", "kid_score": 2, "start_date": "2026-10-20"},
               {"title": "c", "kid_score": 2, "start_date": "2026-10-12"}, {"title": "d", "kid_score": 1, "start_date": "2026-10-10"}]
        self.assertEqual([e["title"] for e in tc.sort_events(evs)], ["c", "a", "d", "b"])


class TestNormalize(unittest.TestCase):
    def items(self):
        return tc.parse_items(SAMPLE)

    def test_parse_items(self):
        self.assertEqual(len(self.items()), 2)
        self.assertEqual(tc.parse_items({"response": {"body": {"items": ""}}}), [])
        self.assertEqual(tc.parse_items({"response": {"body": {"items": {"item": {"contentid": "1"}}}}}), [{"contentid": "1"}])
        self.assertEqual(tc.parse_items({}), [])

    def test_ok_event(self):
        ev = tc.normalize_item(self.items()[0], TODAY, fee_text="무료")
        self.assertEqual((ev["id"], ev["region"], ev["start_date"], ev["end_date"]), ("tour_1001", "서울", "2026-10-12", "2026-10-14"))
        self.assertTrue(ev["is_free"])
        self.assertNotIn("image", ev)                     # 1차는 이미지 없음
        self.assertTrue(ev["link"].startswith("https://map.naver.com/"))

    def test_adult_event_dropped(self):
        self.assertIsNone(tc.normalize_item(self.items()[1], TODAY))

    def test_date_filters(self):
        base = dict(self.items()[0])
        self.assertIsNone(tc.normalize_item({**base, "eventenddate": "20261009"}, TODAY))             # 이미 끝남
        self.assertIsNone(tc.normalize_item({**base, "eventstartdate": "20270101", "eventenddate": "20270102"}, TODAY))  # 너무 먼 미래
        self.assertIsNotNone(tc.normalize_item({**base, "eventstartdate": "20261001", "eventenddate": "20261011"}, TODAY))  # 진행 중
        self.assertIsNone(tc.normalize_item({**base, "eventstartdate": ""}, TODAY))
        self.assertIsNone(tc.normalize_item({**base, "eventstartdate": "20260301", "eventenddate": "20261231"}, TODAY))   # 장기 캠페인
        self.assertIsNotNone(tc.normalize_item({**base, "eventstartdate": "20261001", "eventenddate": "20261030"}, TODAY))  # 30일

    def test_bad_coordinates_and_region(self):
        base = dict(self.items()[0])
        self.assertIsNone(tc.normalize_item({**base, "mapx": "0", "mapy": "0"}, TODAY))
        self.assertIsNone(tc.normalize_item({**base, "areacode": "31"}, TODAY))     # 주소(서울)와 코드(경기) 불일치
        self.assertIsNotNone(tc.normalize_item({**base, "mapx": "", "mapy": ""}, TODAY))  # 좌표 없음은 허용(지역은 확실)


class TestDedupe(unittest.TestCase):
    PERF = [{"title": "[서울] 햇님이 달님이", "region": "서울", "start_date": "2026-10-01", "end_date": "2026-10-20"},
            {"title": "뽀로로 콘서트", "region": "경기", "start_date": "2026-10-10", "end_date": "2026-10-12"}]

    def ev(self, title, region, s, e):
        return {"title": title, "region": region, "start_date": s, "end_date": e}

    def test_removes_same_region_overlap_title(self):
        kept, removed = tc.dedupe_against_kopis([self.ev("햇님이 달님이 (무료)", "서울", "2026-10-05", "2026-10-06")], self.PERF)
        self.assertEqual((len(kept), len(removed)), (0, 1))

    def test_keeps_other_region_or_dates_or_title(self):
        evs = [self.ev("햇님이 달님이", "부산", "2026-10-05", "2026-10-06"),
               self.ev("햇님이 달님이", "서울", "2026-12-01", "2026-12-02"),
               self.ev("전혀 다른 행사", "서울", "2026-10-05", "2026-10-06"),
               self.ev("콘서트", "경기", "2026-10-10", "2026-10-11")]      # 3글자 '콘서트' 는 부분 포함 규칙 대상 아님
        kept, removed = tc.dedupe_against_kopis(evs, self.PERF)
        self.assertEqual((len(kept), len(removed)), (4, 0))

    def test_empty(self):
        self.assertEqual(tc.dedupe_against_kopis([], self.PERF), ([], []))
        evs = [self.ev("a행사축제", "서울", "2026-10-05", "2026-10-06")]
        self.assertEqual(tc.dedupe_against_kopis(evs, [])[0], evs)


class TestRunSafety(unittest.TestCase):
    def test_no_key_returns_none_without_raising(self):
        old = os.environ.pop("TOURAPI_SERVICE_KEY", None)
        try:
            self.assertIsNone(tc.run())
            self.assertIsNone(tc.run(dry_run=True))
        finally:
            if old is not None:
                os.environ["TOURAPI_SERVICE_KEY"] = old


if __name__ == "__main__":
    unittest.main()
