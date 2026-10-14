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


OP = {"provenance": "operator_verified", "verified_at": D}


def opv(v):
    return dict({"v": v}, **OP)


def good_eat():
    return {
        "id": "eat1", "kind": "식당", "name": "테스트식당", "address": "경기 용인시 기흥구 상갈로 29",
        "distance": dict({"v": "도보 3분", "mode": "walk", "min_minutes": 3, "max_minutes": 3}, **OP),
        "recommend_for": opv("유아 동반"), "infra": {"high_chair": opv("구비")}, "menu": opv("순두부"),
        "pros": [dict({"text": "좋아요"}, **OP)], "cons": [dict({"text": "웨이팅"}, **OP)],
        "peak": {"start": "12:00", "end": "13:00", "label": "점심 피크"}, "place_check": {"status": "unchecked"}, **OP,
    }


class TestEatsAndCosts(unittest.TestCase):
    def errs(self, **extra):
        p = good_place(); p.update(extra)
        return pv.validate({"places": {"p1": p}})

    def test_good_eat_passes(self):
        self.assertEqual(self.errs(eats=[good_eat()]), [])

    def test_operator_verified_or_source_required(self):
        e = good_eat(); del e["verified_at"]
        self.assertTrue(any("운영자 확인" in x for x in self.errs(eats=[e])))
        e = good_eat(); e["recommend_for"] = {"v": "유아 동반"}
        self.assertTrue(any("recommend_for" in x for x in self.errs(eats=[e])))
        e = good_eat(); e["pros"] = [{"text": "좋아요"}]
        self.assertTrue(any("pros[0]" in x for x in self.errs(eats=[e])))
        e = good_eat(); e["infra"] = {"high_chair": {"v": "구비", "src": S, "checked": D}}     # 출처 URL 방식도 허용
        e["pros"] = [{"text": "좋아요", "src": S, "checked": D}]
        self.assertEqual(self.errs(eats=[e]), [])

    def test_eat_structure_rules(self):
        for mut, key in ((lambda e: e.update(kind="술집"), "kind"), (lambda e: e.update(address="용인시 어딘가"), "address"),
                         (lambda e: e["distance"].update(mode="bus"), "mode"), (lambda e: e["distance"].update(min_minutes=9, max_minutes=3), "minutes"),
                         (lambda e: e.update(pros=[], cons=[]), "하나는"), (lambda e: e.update(peak={"start": "12", "end": "13:00", "label": "x"}), "peak"),
                         (lambda e: e.update(place_check={"status": "ok"}), "status"), (lambda e: e.update(name=""), "name")):
            e = good_eat(); mut(e)
            self.assertTrue(any(key in x for x in self.errs(eats=[e])), key)
        a, b = good_eat(), good_eat()
        self.assertTrue(any("중복" in x for x in self.errs(eats=[a, b])))

    def test_costs_rules(self):
        free = {"model": "free", "note": "무료", "src": S, "checked": D}
        tier = {"model": "tiered", "free_minutes": 30, "base_minutes": 60, "base": 1000, "step_minutes": 10, "step": 200, "daily_max": 8000, "src": S, "checked": D}
        self.assertEqual(self.errs(costs={"admission": free, "parking": tier}), [])
        self.assertEqual(self.errs(costs={"admission": {"model": "unknown", "note": "확인 못 함"}, "parking": {"model": "unknown", "note": "예약 페이지 확인"}}), [])
        self.assertTrue(self.errs(costs={"admission": {"model": "unknown"}, "parking": tier}))                        # unknown 은 note 필요
        self.assertTrue(self.errs(costs={"admission": {"model": "free", "note": "무료"}, "parking": tier}))           # 숫자·무료는 출처 필수
        bad = dict(tier, daily_max=500)
        self.assertTrue(any("daily_max" in x for x in self.errs(costs={"admission": free, "parking": bad})))
        bad = dict(tier, step_minutes=0)
        self.assertTrue(self.errs(costs={"admission": free, "parking": bad}))
        pp = {"model": "per_person", "prices": {"adult": 5000, "teen": 3000, "child": 2000}, "src": S, "checked": D}
        self.assertTrue(any("toddler" in x for x in self.errs(costs={"admission": pp, "parking": tier})))

    def test_confirmed_place_check_needs_who_when_note(self):
        e = good_eat(); e["place_check"] = {"status": "confirmed", "confirmed_by": "운영자", "confirmed_at": D, "note": "직접 확인"}
        self.assertEqual(self.errs(eats=[e]), [])
        for bad in ({"status": "confirmed"}, {"status": "confirmed", "confirmed_by": "운영자", "confirmed_at": D},
                    {"status": "confirmed", "confirmed_by": "", "confirmed_at": D, "note": "x"}, {"status": "confirmed", "confirmed_by": "운영자", "confirmed_at": "어제", "note": "x"}):
            e = good_eat(); e["place_check"] = bad
            self.assertTrue(any("confirmed" in x for x in self.errs(eats=[e])), bad)

    def test_real_anchors_have_official_photos_and_confirmed_eats(self):
        path = ROOT / "place_guides.json"
        if not path.exists():
            self.skipTest("place_guides.json 없음")
        places = json.loads(path.read_text(encoding="utf-8"))["places"]
        for pid, p in places.items():
            self.assertTrue(p["photos"], pid)
            for ph in p["photos"]:
                self.assertTrue(ph["url"].startswith("https://tong.visitkorea.or.kr/"), ph["url"])
                self.assertIn("공공누리 제1유형", ph["license"])
        for e in places["spot_06"]["eats"]:
            self.assertEqual(e["place_check"]["status"], "confirmed")
            self.assertEqual(e["provenance"], "operator_verified")


if __name__ == "__main__":
    unittest.main()
