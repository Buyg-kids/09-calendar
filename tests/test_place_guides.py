"""장소 가이드 데이터 검증기 단위 테스트.  python -m unittest tests.test_place_guides -v"""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generator import place_guides_validator as pv  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
D = "2026-10-14"
S = "https://example.org/x"


def fact(v=None):
    return {"v": v, "src": S if v is not None else None, "checked": D if v is not None else None}


def good_place():
    return {
        "id": "p1", "name": "테스트", "region": "경기", "theme": "museum", "copy": "한 줄", "photos": [],
        "facts": {k: fact("값") for k in pv.FACT_KEYS},
        "age_fit": {"baby": 1, "toddler": 2, "kid": 3, "preschool": 3, "basis": "editor", "reviewed": False, "note": "n"},
        "tips": [{"text": "팁", "src": S, "checked": D}], "highlights": [{"text": "포인트", "src": S, "checked": D}],
        "infra": {k: fact() for k in pv.INFRA_KEYS},
        "faq": [{"q": "질문", "a": "답", "src": S, "checked": D}], "nearby": [{"name": "옆", "src": S, "checked": D}],
    }


class TestValidator(unittest.TestCase):
    def errs(self, p):
        return pv.validate({"places": {"p1": p}})

    def test_good_place_passes(self):
        self.assertEqual(self.errs(good_place()), [])

    def test_null_facts_are_allowed(self):
        p = good_place()
        for k in pv.FACT_KEYS:
            p["facts"][k] = fact(None)
        self.assertEqual(self.errs(p), [])

    def test_fact_without_source_or_date_fails(self):
        p = good_place(); p["facts"]["fee"] = {"v": "무료", "src": None, "checked": D}
        self.assertTrue(any("출처" in e for e in self.errs(p)))
        p = good_place(); p["facts"]["fee"] = {"v": "무료", "src": S, "checked": None}
        self.assertTrue(any("확인일" in e for e in self.errs(p)))
        p = good_place(); p["infra"]["parking"] = {"v": "요금", "src": "http://insecure", "checked": D}
        self.assertTrue(any("https" in e for e in self.errs(p)))

    def test_tip_faq_nearby_need_sources(self):
        for key, bad in (("tips", {"text": "팁", "checked": D}), ("highlights", {"text": "p", "src": S}),
                         ("faq", {"q": "q", "a": "a", "checked": D}), ("nearby", {"name": "n", "src": S})):
            p = good_place(); p[key] = [bad]
            self.assertTrue(self.errs(p), key)

    def test_age_fit_is_editorial_not_statistics(self):
        p = good_place(); p["age_fit"]["basis"] = "analytics"
        self.assertTrue(any("editor" in e for e in self.errs(p)))
        p = good_place(); p["age_fit"]["kid"] = 45          # 퍼센트 같은 값은 허용하지 않는다
        self.assertTrue(any("0~3" in e for e in self.errs(p)))
        p = good_place(); del p["age_fit"]["reviewed"]
        self.assertTrue(any("검수" in e for e in self.errs(p)))

    def test_photos_need_license_info(self):
        p = good_place(); p["photos"] = [{"url": "https://example.org/a.jpg"}]
        self.assertTrue(any("license" in e for e in self.errs(p)))
        p = good_place(); p["photos"] = [{"url": "https://example.org/a.jpg", "credit": "기관", "license": "공공누리 1유형"}]
        self.assertEqual(self.errs(p), [])

    def test_structure_errors(self):
        self.assertTrue(pv.validate([]))
        p = good_place(); del p["faq"]
        self.assertTrue(any("필수 키" in e for e in self.errs(p)))
        p = good_place(); p["theme"] = "zzz"
        self.assertTrue(any("theme" in e for e in self.errs(p)))
        self.assertTrue(pv.validate({"places": {"p2": good_place()}}))      # id 불일치

    def test_real_data_file_is_valid(self):
        path = ROOT / "place_guides.json"
        if not path.exists():
            self.skipTest("place_guides.json 없음")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(pv.validate(data), [])
        self.assertGreaterEqual(len(data["places"]), 3)


if __name__ == "__main__":
    unittest.main()
