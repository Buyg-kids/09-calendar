import copy
import unittest

from generator import spot_enriched_validator as v


def _spot():
    return {"contentid": "126712", "matched_title": "서울숲", "address": "서울특별시 성동구 뚝섬로 273",
            "geo": {"lat": 37.54, "lon": 127.04, "source": "https://api.visitkorea.or.kr/", "checked": "2026-10-14"},
            "photo": None, "photo_status": "license_not_type1",
            "nearby": {"radius_m": 2000, "source": "https://api.visitkorea.or.kr/", "checked": "2026-10-14", "eats": [
                {"id": "eat_t1234", "kind": "식당", "name": "가게", "address": "서울 성동구 1", "level": "listed", "distance": {"v": "장소에서 직선 약 300m", "mode": "straight", "meters": 300},
                 "identity": {"src": "https://api.visitkorea.or.kr/", "checked": "2026-10-14", "note": "TourAPI(contentid 1234)", "tourapi_contentid": "1234"}, "place_check": {"status": "unchecked", "note": "확인 필요"}}], "cafes": []},
            "curation": {"status": "empty", "parent_tips": [], "parking_tip": None, "stroller_difficulty": None, "recommended_ages": None}}


TIP = {"text": "주말 오전 일찍 가면 주차가 수월해요", "src_urls": ["https://blog.example.com/a"], "provenance": "ai_summary", "checked": "2026-10-14", "reviewed": False}


class SpotEnriched(unittest.TestCase):
    def run_v(self, mut=None):
        d = {"spots": {"spot_04": _spot()}}
        if mut:
            mut(d["spots"]["spot_04"])
        return v.validate(d, {"spot_04"})

    def cur(self, **kw):
        return lambda s: s["curation"].update(**kw)

    def test_ok(self):
        self.assertEqual(self.run_v(), [])

    def test_geo_and_photo_rules(self):
        self.assertTrue(self.run_v(lambda s: s["geo"].update(lat=10)))
        self.assertTrue(self.run_v(lambda s: s.update(photo={"url": "https://x.example/a.jpg", "thumb": "https://x.example/b.jpg", "license": "공공누리 제1유형", "credit": "a", "checked": "2026-10-14"})))
        self.assertTrue(self.run_v(lambda s: s.update(photo_status="뭔가")))

    def test_nearby_uses_listed_rules(self):
        self.assertTrue(self.run_v(lambda s: s["nearby"]["eats"][0].update(infra={"high_chair": {"v": "있음"}})))
        self.assertTrue(self.run_v(lambda s: s["nearby"]["eats"][0].update(kind="카페")))

    def test_curation_schema(self):
        self.assertEqual(self.run_v(self.cur(status="ai_draft", parent_tips=[copy.deepcopy(TIP)])), [])
        self.assertTrue(self.run_v(self.cur(status="empty", parent_tips=[copy.deepcopy(TIP)])))
        self.assertTrue(self.run_v(self.cur(status="reviewed", parent_tips=[copy.deepcopy(TIP)])))
        self.assertTrue(self.run_v(self.cur(status="ai_draft", parent_tips=[dict(TIP, text="가" * 121)])))
        self.assertTrue(self.run_v(self.cur(status="ai_draft", parent_tips=[dict(TIP, src_urls=[])])))
        self.assertTrue(self.run_v(self.cur(status="ai_draft", parent_tips=[dict(TIP, quote="원문")])))
        self.assertTrue(self.run_v(self.cur(status="ai_draft", stroller_difficulty=dict(TIP, level="아주쉬움"))))
        self.assertEqual(self.run_v(self.cur(status="ai_draft", recommended_ages=dict(TIP, ages=["baby", "toddler"]))), [])


if __name__ == "__main__":
    unittest.main()
