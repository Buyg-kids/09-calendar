"""풀 보강 수집기·검증기 단위 테스트 (네트워크 없음).  python -m unittest tests.test_collect_pool_enriched -v"""
import copy
import os
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("BUYG_ROOT", str(ROOT))      # 저장소 루트(enrich_spots·collector 를 찾기 위함)

from generator import pool_enriched_validator as v  # noqa: E402  (수집기보다 먼저: 수집기가 sys.path 앞에 저장소 루트를 끼워 넣는다)
import collect_pool_enriched as c  # noqa: E402

TODAY = "2026-10-23"


class FakeBudget:
    def __init__(self, items):
        self.items, self.calls = items, []

    def call(self, key, endpoint, params):
        self.calls.append((endpoint, params))
        return self.items


class TestCleaning(unittest.TestCase):
    def test_noise_values_are_dropped(self):
        for raw in (None, "", "  ", "※ 홈페이지 참조", "홈페이지 참조", "없음", "해당없음", "-", "문의", "전화 문의"):
            self.assertIsNone(c.clean_text(raw), raw)

    def test_tags_newlines_and_separators(self):
        self.assertEqual(c.clean_text("- 평일 09:00~18:00- 주말 9:00~18:30※ 평일 출발"), "평일 09:00~18:00 · 주말 9:00~18:30 ※ 평일 출발")
        self.assertEqual(c.clean_text("어른 4,000원<br />청소년 3,000원\n유아 &amp; 어린이 2,000원"), "어른 4,000원 청소년 3,000원 유아 & 어린이 2,000원")
        self.assertEqual(c.clean_text("09:00-18:00"), "09:00-18:00")           # 시간 범위의 하이픈은 건드리지 않는다

    def test_cap(self):
        t = c.clean_text("가" * 500, 50)
        self.assertEqual(len(t), 50)
        self.assertTrue(t.endswith("…"))

    def test_flags(self):
        self.assertIs(c.parse_flag("가능"), True)
        self.assertIs(c.parse_flag("있음"), True)
        self.assertIs(c.parse_flag("불가"), False)
        self.assertIs(c.parse_flag("유모차 불가능"), False)     # 부정이 우선
        self.assertIs(c.parse_flag("없음"), False)
        self.assertIsNone(c.parse_flag(""))
        self.assertIsNone(c.parse_flag("문의"))


class TestFree(unittest.TestCase):
    def test_free_only_from_admission_text(self):
        self.assertIs(c.derive_free("무료"), True)
        self.assertIs(c.derive_free("무료입장"), True)
        self.assertIs(c.derive_free("어른 4,000원 ※ 무료 : 3세 미만"), False)     # 금액이 있으면 무료가 아니다
        self.assertIsNone(c.derive_free("무료 (전시 별도 유료)"))
        self.assertIsNone(c.derive_free("홈페이지 참조 요금 안내"))
        self.assertIsNone(c.derive_free(None))

    def test_parking_free_does_not_make_place_free(self):
        item = {"parkingfeeleports": "무료", "infocenterleports": "031-582-7788", "restdateleports": "연중무휴"}
        d = c.details_from(item, 28)
        self.assertEqual(d["parking_fee"], "무료")
        self.assertNotIn("free", d)             # 입장료 정보가 없으니 판정하지 않는다
        self.assertNotIn("fee", d)


class TestDetails(unittest.TestCase):
    def test_type_specific_fields(self):
        culture = {"usefee": "- 어른 4,000원- 청소년 3,000원 ※ 무료 : 3세 미만", "usetimeculture": "09:00~18:00", "restdateculture": "매주 월요일",
                   "infocenterculture": "055-831-3344", "spendtime": "2시간", "chkbabycarriageculture": "가능", "chkpetculture": "불가", "parkingculture": ""}
        d = c.details_from(culture, 14)
        self.assertEqual(d["fee"], "어른 4,000원 · 청소년 3,000원 ※ 무료 : 3세 미만")
        self.assertIs(d["free"], False)
        self.assertEqual(d["duration"], "2시간")
        self.assertIs(d["stroller"], True)
        self.assertIs(d["pet"], False)
        self.assertNotIn("parking", d)           # 빈 값은 담지 않는다
        tourist = {"infocenter": "코레일 1544-7788", "restdate": "※ 홈페이지 참조", "usetime": "※ 홈페이지 참조", "expguide": ""}
        self.assertEqual(c.details_from(tourist, 12), {"tel": "코레일 1544-7788"})
        self.assertEqual(c.details_from({}, 12), {})

    def test_wrong_type_fields_ignored(self):
        self.assertEqual(c.details_from({"usefeeleports": "무료"}, 14), {})       # 유형이 다르면 그 필드는 보지 않는다


class TestNearby(unittest.TestCase):
    def items(self):
        mk = lambda cid, name, cat3, dist, img="https://tong.visitkorea.or.kr/i.jpg", cpy="Type1": {"contentid": cid, "title": name, "cat3": cat3, "dist": str(dist), "firstimage2": img, "cpyrhtDivCd": cpy}   # noqa: E731
        lst = [mk("100001", "먼 식당", "A05020100", 1900), mk("100002", "가까운 식당", "A05020100", 120, img="http://evil.example/i.jpg"), mk("100003", "카페A", "A05020900", 300),
               mk("100004", "제3유형 사진 식당", "A05020200", 500, cpy="Type3"), {"contentid": "x", "title": "id 이상", "dist": "10"}, mk("100005", "", "A05020100", 10),
               mk("100006", "범위 밖", "A05020100", 2500)]
        lst += [mk(str(200000 + i), f"식당{i}", "A05020100", 600 + i) for i in range(8)]
        return lst

    def test_light_schema_sorted_and_capped(self):
        b = FakeBudget(self.items())
        n = c.nearby_light(b, "KEY", 37.5, 127.0, TODAY)
        self.assertEqual(len(b.calls), 1)                         # 호출 1회(메뉴 조회 없음)
        self.assertEqual(b.calls[0][0], "locationBasedList2")
        self.assertEqual(n["radius_m"], 2000)
        self.assertEqual(len(n["eats"]), 5)
        self.assertEqual([e["name"] for e in n["eats"]][:3], ["가까운 식당", "제3유형 사진 식당", "식당0"])
        self.assertEqual([e["distance"]["meters"] for e in n["eats"]], sorted(e["distance"]["meters"] for e in n["eats"]))
        self.assertEqual(n["cafes"][0]["name"], "카페A")
        near = n["eats"][0]
        self.assertEqual(set(near), {"id", "kind", "name", "category", "level", "distance"})   # 공식 호스트가 아닌 사진은 버린다
        self.assertEqual(near["id"], "eat_t100002")
        self.assertNotIn("photo", n["eats"][1])                   # 제3유형 사진은 쓰지 않는다
        self.assertEqual(n["eats"][2]["photo"], {"thumb": "https://tong.visitkorea.or.kr/i.jpg"})
        self.assertEqual(c.nearby_light(FakeBudget([{"contentid": "100009", "title": "http 공식 사진", "cat3": "A05020100", "dist": "50", "firstimage2": "http://tong.visitkorea.or.kr/h.jpg", "cpyrhtDivCd": "Type1"}]), "KEY", 37.5, 127.0, TODAY)["eats"][0]["photo"], {"thumb": "https://tong.visitkorea.or.kr/h.jpg"})   # http 공식 호스트는 https 로 올린다
        self.assertFalse(any(e["name"] in ("", "범위 밖", "id 이상") for e in n["eats"] + n["cafes"]))

    def test_api_error_returns_none(self):
        self.assertIsNone(c.nearby_light(FakeBudget(None), "KEY", 37.5, 127.0, TODAY))


class TestTargetsAndDue(unittest.TestCase):
    POOL = {"themes": {
        "a": {"items": [
            {"id": "pool_1", "contentid": "1", "title": "나", "region": "경기", "category": "관광지", "geo": {"lat": 37.1, "lon": 127.1}, "provenance": "tourapi"},
            {"id": "pool_2", "contentid": "2", "title": "가", "region": "서울", "category": "문화시설", "geo": {"lat": 37.5, "lon": 127.0}, "provenance": "tourapi"},
            {"id": "pool_3", "contentid": "3", "title": "지방", "region": "경남", "category": "관광지", "geo": {"lat": 35.0, "lon": 128.0}, "provenance": "tourapi"},
            {"id": "pool_4", "contentid": "4", "title": "공공데이터", "region": "경기", "category": "관광지", "geo": {"lat": 37.1, "lon": 127.1}, "provenance": "public_csv"},
            {"id": "pool_5", "contentid": "5", "title": "유형 밖", "region": "경기", "category": "숙박", "geo": {"lat": 37.1, "lon": 127.1}, "provenance": "tourapi"},
            {"id": "pool_6", "contentid": "6", "title": "좌표 없음", "region": "경기", "category": "관광지", "geo": {}, "provenance": "tourapi"}]},
        "b": {"items": [{"id": "pool_1", "contentid": "1", "title": "나", "region": "경기", "category": "관광지", "geo": {"lat": 37.1, "lon": 127.1}, "provenance": "tourapi"}]}}}

    def test_targets(self):
        t = c.targets(self.POOL, ["서울", "경기", "인천"])
        self.assertEqual([x["id"] for x in t], ["pool_2", "pool_1"])            # 지역 순서(서울 먼저), 중복·지방·비TourAPI·유형 밖·좌표 없음 제외
        self.assertEqual((t[0]["ctype"], t[1]["ctype"]), (14, 12))
        self.assertEqual([x["id"] for x in c.targets(self.POOL, ["경남"])], ["pool_3"])

    def test_due(self):
        today = date(2026, 10, 23)
        self.assertTrue(c.due(None, today))
        self.assertTrue(c.due({}, today))
        self.assertFalse(c.due({"checked": "2026-10-10"}, today))
        self.assertTrue(c.due({"checked": "2026-09-20"}, today))
        self.assertTrue(c.due({"checked": "깨진 값"}, today))


GOOD = {"schema_version": 1, "source": "https://api.visitkorea.or.kr/", "generated_at": TODAY, "spots": {
    "pool_100": {"details": {"fee": "무료", "free": True, "hours": "09:00~18:00", "stroller": True, "checked": TODAY},
                 "nearby": {"radius_m": 2000, "checked": TODAY,
                            "eats": [{"id": "eat_t100002", "kind": "식당", "name": "가까운 식당", "category": "한식", "level": "listed", "distance": {"meters": 120, "mode": "straight"}, "photo": {"thumb": "https://tong.visitkorea.or.kr/i.jpg"}}],
                            "cafes": []}}}}


class TestValidator(unittest.TestCase):
    def test_good_passes(self):
        self.assertEqual(v.validate(GOOD), [])

    def bad(self, mut):
        d = copy.deepcopy(GOOD)
        mut(d)
        return v.validate(d)

    def test_structure_errors(self):
        self.assertTrue(self.bad(lambda d: d.update(schema_version=2)))
        self.assertTrue(self.bad(lambda d: d.update(source="http://x")))
        self.assertTrue(self.bad(lambda d: d["spots"].update({"spot_01": {"details": {"checked": TODAY}}})))      # 풀 id 형식이 아님
        self.assertTrue(self.bad(lambda d: d["spots"]["pool_100"].update(extra=1)))
        self.assertTrue(self.bad(lambda d: d["spots"]["pool_100"]["details"].pop("checked")))
        self.assertTrue(self.bad(lambda d: d["spots"]["pool_100"]["details"].update(bogus="x")))

    def test_value_errors(self):
        self.assertTrue(self.bad(lambda d: d["spots"]["pool_100"]["details"].update(hours="<br>09:00")))
        self.assertTrue(self.bad(lambda d: d["spots"]["pool_100"]["details"].update(stroller="가능")))
        self.assertTrue(self.bad(lambda d: d["spots"]["pool_100"]["details"].update(fee="가" * 300)))
        self.assertTrue(self.bad(lambda d: d["spots"]["pool_100"]["details"].pop("fee")))                          # fee 없이 free 판정 금지

    def test_nearby_errors(self):
        eat = lambda d: d["spots"]["pool_100"]["nearby"]["eats"][0]   # noqa: E731
        self.assertTrue(self.bad(lambda d: eat(d)["distance"].update(meters=2500)))
        self.assertTrue(self.bad(lambda d: eat(d).update(level="verified")))
        self.assertTrue(self.bad(lambda d: eat(d).update(photo={"thumb": "http://x"})))
        self.assertTrue(self.bad(lambda d: eat(d).update(photo={"thumb": "https://evil.example/i.jpg"})))      # 공식 호스트만
        self.assertTrue(self.bad(lambda d: eat(d).update(id="eat_x")))
        self.assertTrue(self.bad(lambda d: d["spots"]["pool_100"]["nearby"].update(eats=[copy.deepcopy(eat(d)) for _ in range(6)])))
        def unsorted(d):
            a = copy.deepcopy(eat(d)); b = copy.deepcopy(eat(d)); a["distance"]["meters"] = 900
            d["spots"]["pool_100"]["nearby"]["eats"] = [a, b]
        self.assertTrue(self.bad(unsorted))

    def test_unknown_pool_id(self):
        pool = {"themes": {"a": {"items": [{"id": "pool_999"}]}}}
        self.assertTrue(v.validate(GOOD, pool))
        pool["themes"]["a"]["items"].append({"id": "pool_100"})
        self.assertEqual(v.validate(GOOD, pool), [])


if __name__ == "__main__":
    unittest.main()
