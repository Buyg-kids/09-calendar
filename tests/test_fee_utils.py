"""요금 파서/필터 규칙 단위 테스트 (네트워크 없음).  python -m unittest tests.test_fee_utils -v"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector import fee_utils as fu  # noqa: E402
from collector import tour_collector as tc  # noqa: E402

TODAY = date(2026, 10, 10)


class TestFeeParsing(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(fu.parse_fee_min("성인 5,000원 / 어린이 3,000원"), 3000)
        self.assertEqual(fu.parse_fee_max("성인 5,000원 / 어린이 3,000원"), 5000)
        self.assertEqual(fu.parse_fee_min("1만원"), 10000)
        self.assertEqual(fu.parse_fee_min("1만 5천원"), 15000)
        self.assertEqual(fu.parse_fee_min("3천원"), 3000)
        self.assertEqual(fu.parse_fee_min("R석 80,000원 S석 60,000원"), 60000)

    def test_zero_and_none(self):
        self.assertEqual(fu.parse_fee_min("0원"), 0)
        self.assertIsNone(fu.parse_fee_min("무료"))
        self.assertIsNone(fu.parse_fee_min(""))
        self.assertIsNone(fu.parse_fee_min(None))
        self.assertIsNone(fu.parse_fee_max("0원"))

    def test_classification(self):
        cases = {"무료": "free", "0원": "free", "전석 무료": "free", "입장료 무료 (일부 체험 유료)": "free",
                 "성인 3,000원 / 어린이 0원": "partial", "어린이 무료, 성인 5,000원": "partial",
                 "3,000원": "paid", "유료": "paid", "": "unknown", "홈페이지 참조": "unknown"}
        for text, want in cases.items():
            self.assertEqual(fu.classify_fee(text), want, text)

    def test_describe_fee_bundle(self):
        d = fu.describe_fee("전석 30,000원")
        self.assertEqual((d["fee_type"], d["is_free"], d["fee_min"], d["fee_max"]), ("paid", False, 30000, 30000))
        d = fu.describe_fee("전석 무료")
        self.assertEqual((d["fee_type"], d["is_free"]), ("free", True))
        d = fu.describe_fee("")
        self.assertEqual((d["fee_type"], d["is_free"], d["fee_min"]), ("unknown", False, None))      # 요금 미기재를 무료로 단정하지 않는다

    def test_tour_collector_reexports_same_functions(self):
        self.assertIs(tc.classify_fee, fu.classify_fee)


class TestNoFreeHardFilter(unittest.TestCase):
    def raw(self, title="가을 어린이 체험 축제", cid="1"):
        return {"contentid": cid, "title": title, "addr1": "서울특별시 종로구 세종대로 1", "lDongRegnCd": "11", "mapx": "127.0", "mapy": "37.5",
                "eventstartdate": "20261012", "eventenddate": "20261013"}

    def test_paid_and_unknown_events_are_kept(self):
        for fee, want_type in (("무료", "free"), ("3,000원", "paid"), ("", "unknown"), ("성인 5,000원 / 어린이 3,000원", "paid")):
            ev = tc.normalize_item(self.raw(), TODAY, fee_text=fee)
            self.assertIsNotNone(ev, fee)
            self.assertEqual(ev["fee_type"], want_type, fee)
            self.assertEqual(ev["is_free"], want_type == "free")
            self.assertEqual(ev["fee_text"], fee)                       # 원문 보존

    def test_fee_fields_in_output(self):
        ev = tc.normalize_item(self.raw(), TODAY, fee_text="어린이 3,000원 / 성인 5,000원")
        self.assertEqual((ev["fee_min"], ev["fee_max"]), (3000, 5000))


class TestAudienceFilter(unittest.TestCase):
    def test_professional_adult_events_excluded(self):
        for title in ("2026 영유아 교육 학술 심포지엄", "청년 취업 박람회", "채용 설명회", "전문가 포럼", "국제 컨퍼런스", "창업 세미나", "와인 페스티벌"):
            self.assertIsNone(tc.kid_score(title), title)

    def test_family_events_kept_and_prioritized(self):
        self.assertGreaterEqual(tc.kid_score("어린이 가족 체험 박물관 나들이"), 4)
        self.assertEqual(tc.kid_score("가을 국화 전시"), 0)             # 대상 키워드가 없어도 제외하지 않는다(우선순위만 낮음)
        self.assertGreaterEqual(tc.kid_score("국립박물관 숲 놀이터"), 3)

    def test_sort_puts_kid_events_first(self):
        evs = [{"title": "국화 전시", "kid_score": 0, "start_date": "2026-10-10"},
               {"title": "어린이 체험", "kid_score": 2, "start_date": "2026-10-20"}]
        self.assertEqual([e["title"] for e in tc.sort_events(evs)], ["어린이 체험", "국화 전시"])


class TestKopisFeeFields(unittest.TestCase):
    def test_kopis_output_carries_fee_flags(self):
        from unittest import mock
        import tempfile
        import json
        import os
        from collector import kopis_collector as kc
        tmp = Path(tempfile.mkdtemp())
        item = {"id": "PF1", "title": "어린이 뮤지컬", "start_date": "2026-10-12", "end_date": "2026-10-14", "venue": "극장", "poster": "",
                "area": "서울특별시", "genre": "뮤지컬", "state": "공연예정"}
        with mock.patch.dict(os.environ, {"KOPIS_API_KEY": "k"}), mock.patch.object(kc, "OUTPUT_PATH", tmp / "k.json"), \
             mock.patch.object(kc, "_fetch_list", return_value=[dict(item, id="PF1"), dict(item, id="PF2"), dict(item, id="PF3")]), \
             mock.patch.object(kc.time, "sleep"):
            prices = {"PF1": "전석 무료", "PF2": "R석 80,000원 S석 60,000원", "PF3": ""}
            def fill(key, it): it["price"] = prices[it["id"]]
            with mock.patch.object(kc, "_fill_detail", side_effect=fill):
                kc.run()
        perfs = {p["id"]: p for p in json.loads((tmp / "k.json").read_text(encoding="utf-8"))["performances"]}
        self.assertTrue(perfs["PF1"]["is_free"])
        self.assertEqual((perfs["PF2"]["is_free"], perfs["PF2"]["fee_min"], perfs["PF2"]["fee_max"]), (False, 60000, 80000))
        self.assertEqual((perfs["PF3"]["fee_type"], perfs["PF3"]["is_free"]), ("unknown", False))
        self.assertEqual(perfs["PF2"]["price"], "R석 80,000원 S석 60,000원")          # 원문 보존


if __name__ == "__main__":
    unittest.main()
