"""지도 상세 시트 연령 뱃지 사전 계산 단위 테스트.  python -m unittest tests.test_age_annotate -v"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generator import age_annotate as aa  # noqa: E402


class TestAgesForItem(unittest.TestCase):
    def test_event_uses_title(self):
        self.assertEqual(aa.ages_for_item("event", {"title": "3~5세 가족 체험 축제"}), ["toddler", "kid"])
        self.assertEqual(aa.ages_for_item("event", {"title": "가을 축제"}), [])

    def test_reserve_ignores_older_target_words(self):
        it = {"title": "목공체험", "target": "성인, 어린이(영유아의 경우 보호자 동반), 청소년(중고등학생), 초등학생"}
        got = aa.ages_for_item("reserve", it)
        self.assertEqual(aa.ages_for_item("reserve", {"title": "목공체험", "target": "초등학생"}), [])
        self.assertEqual(aa.ages_for_item("reserve", {"title": "목공체험", "target": "성인, 청소년"}), [])

    def test_reserve_explicit_age_and_infant_words(self):
        self.assertEqual(aa.ages_for_item("reserve", {"title": "유아숲 4~5세 반", "target": "유아"}), ["kid"])
        self.assertEqual(aa.ages_for_item("reserve", {"title": "숲 놀이", "target": "영유아"}), ["toddler", "kid"])

    def test_spot_uses_title_and_category(self):
        self.assertEqual(aa.ages_for_item("spot", {"title": "OO 키즈카페", "category": "실내놀이"}), ["kid"])     # '키즈카페'는 강한 키워드(4~5세)
        self.assertEqual(aa.ages_for_item("spot", {"title": "OO 체험관", "category": "어린이 과학관"}), ["kid", "preschool"])   # '어린이'는 약한 키워드
        self.assertEqual(aa.ages_for_item("spot", {"title": "국립수목원", "category": "수목원/숲길"}), [])

    def test_missing_fields(self):
        for k in ("event", "reserve", "spot"):
            self.assertEqual(aa.ages_for_item(k, {}), [])


class TestAnnotateFiles(unittest.TestCase):
    def _env(self, d: Path):
        (d / "tour_events.json").write_text(json.dumps({"events": [{"id": "a", "title": "3세 체험"}]}), encoding="utf-8")
        (d / "seoul_reserve.json").write_text(json.dumps({"items": [{"id": "b", "title": "숲", "target": "영유아"}]}), encoding="utf-8")
        (d / "free_spots_seed.json").write_text(json.dumps({"spots": [{"id": "c", "title": "수목원", "category": "숲"}]}), encoding="utf-8")

    def test_writes_age_groups_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(aa, "BASE_DIR", Path(t)):
            d = Path(t); self._env(d)
            r1 = aa.run()
            self.assertEqual({k: v["changed"] for k, v in r1.items()}, {"tour_events.json": 1, "seoul_reserve.json": 1, "free_spots_seed.json": 1})
            self.assertEqual(json.loads((d / "tour_events.json").read_text(encoding="utf-8"))["events"][0]["age_groups"], ["toddler"])
            before = (d / "seoul_reserve.json").read_bytes()
            r2 = aa.run()
            self.assertEqual({v["changed"] for v in r2.values()}, {0})
            self.assertEqual((d / "seoul_reserve.json").read_bytes(), before)       # 변경 없으면 다시 쓰지 않는다
            self.assertFalse(list(d.glob("*.tmp")))

    def test_bad_or_missing_files_are_untouched_and_others_continue(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(aa, "BASE_DIR", Path(t)):
            d = Path(t); self._env(d)
            (d / "seoul_reserve.json").write_text("{not json", encoding="utf-8")
            (d / "free_spots_seed.json").unlink()
            r = aa.run()
            self.assertEqual(list(r), ["tour_events.json"])
            self.assertEqual((d / "seoul_reserve.json").read_text(encoding="utf-8"), "{not json")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(aa, "BASE_DIR", Path(t)):
            d = Path(t); self._env(d)
            before = (d / "tour_events.json").read_bytes()
            r = aa.run(dry_run=True)
            self.assertEqual(r["tour_events.json"]["changed"], 1)
            self.assertEqual((d / "tour_events.json").read_bytes(), before)

    def test_never_raises(self):
        with mock.patch.object(aa, "annotate_file", side_effect=RuntimeError("boom")):
            self.assertIsNone(aa.run())


if __name__ == "__main__":
    unittest.main()
