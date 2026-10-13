"""메인 통합 검색 인덱스 단위 테스트.  python -m unittest tests.test_search_index -v"""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generator import search_index as si  # noqa: E402

TODAY = date(2026, 10, 12)
TODAY_ISO = TODAY.isoformat()

EV = {"id": "tour_1", "title": "가족 축제", "start_date": "2026-10-20", "end_date": "2026-10-21", "place": "중앙공원", "address": "경기도 수원시 팔달구", "region": "경기", "is_free": True, "fee_type": "free"}
RS = {"id": "seoul_S1", "title": "유아숲 체험", "start_date": "2026-10-25", "end_date": "2026-10-25", "rcpt_end": "2026-10-20", "place": "북서울꿈의숲", "address": "서울 강북구", "region": "서울", "category": "교육체험", "fee_type": "paid", "fee_min": 3000, "reserve_url": "https://yeyak.seoul.go.kr/x"}
SH = {"id": "PF1", "title": "햇님이 달님이", "start_date": "2026-10-01", "end_date": "2026-11-01", "venue": "소극장", "region": "충북", "genre": "뮤지컬", "age": "36개월 이상", "fee_type": "paid", "fee_min": 50000}


class TestMakeItem(unittest.TestCase):
    def test_event_item(self):
        it = si.make_item("ev", EV, TODAY_ISO)
        self.assertEqual(it[:2], ["tour_1", "ev"])
        self.assertEqual(it[6], "무료")
        self.assertIn("축제", it[7]); self.assertIn("행사", it[7])
        self.assertEqual(len(it), 8)

    def test_reserve_item_uses_rcpt_end_and_needs_https(self):
        it = si.make_item("rs", RS, TODAY_ISO)
        self.assertEqual((it[5], it[6]), ("2026-10-20", "3,000원"))      # 종료일 칸 = 접수 마감일
        self.assertIn("체험", it[7])
        self.assertIsNone(si.make_item("rs", {**RS, "reserve_url": "http://x"}, TODAY_ISO))
        self.assertIsNone(si.make_item("rs", {**RS, "reserve_url": ""}, TODAY_ISO))

    def test_show_item(self):
        it = si.make_item("sh", SH, TODAY_ISO)
        self.assertEqual((it[0], it[1], it[6]), ("PF1", "sh", "50,000원"))
        for w in ("뮤지컬", "공연", "충북"):
            self.assertIn(w, it[7] + it[3])

    def test_expired_and_invalid_are_dropped(self):
        self.assertIsNone(si.make_item("ev", {**EV, "end_date": "2026-10-11"}, TODAY_ISO))
        self.assertIsNone(si.make_item("rs", {**RS, "rcpt_end": "2026-10-11"}, TODAY_ISO))
        self.assertIsNone(si.make_item("sh", {**SH, "end_date": "2026-09-30"}, TODAY_ISO))
        self.assertIsNone(si.make_item("ev", {**EV, "title": ""}, TODAY_ISO))
        self.assertIsNone(si.make_item("ev", {**EV, "id": ""}, TODAY_ISO))
        self.assertIsNone(si.make_item("zz", EV, TODAY_ISO))

    def test_hay_is_lowercase_and_title_not_duplicated(self):
        it = si.make_item("sh", {**SH, "genre": "Musical"}, TODAY_ISO)
        self.assertEqual(it[7], it[7].lower())
        self.assertNotIn("햇님이", it[7])


class TestBuildAndRun(unittest.TestCase):
    def _write(self, d: Path, ev=True, rs=True, sh=True):
        if ev: (d / "tour_events.json").write_text(json.dumps({"events": [EV, EV]}), encoding="utf-8")        # id 중복
        if rs: (d / "seoul_reserve.json").write_text(json.dumps({"items": [RS]}), encoding="utf-8")
        if sh: (d / "kids_performances.json").write_text(json.dumps({"performances": [SH]}), encoding="utf-8")

    def test_build_items_complete_and_dedup(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self._write(d)
            items, complete = si.build_items(TODAY, d)
        self.assertTrue(complete)
        self.assertEqual(sorted(i[1] for i in items), ["ev", "rs", "sh"])     # 같은 id 는 한 번만

    def test_missing_source_is_incomplete(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self._write(d, sh=False)
            items, complete = si.build_items(TODAY, d)
        self.assertFalse(complete)
        self.assertEqual(len(items), 2)

    def test_corrupt_source_is_incomplete(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); self._write(d); (d / "seoul_reserve.json").write_text("{not json", encoding="utf-8")
            _, complete = si.build_items(TODAY, d)
        self.assertFalse(complete)

    def test_run_keeps_old_file_when_incomplete_or_dropped(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); out = d / "search_index.json"
            old = {"v": 1, "count": 100, "items": [["x"] * 8] * 100}
            out.write_text(json.dumps(old), encoding="utf-8")
            with mock.patch.object(si, "OUTPUT_PATH", out), mock.patch.object(si, "BASE_DIR", d):
                self.assertIsNone(si.run())                    # 원본 없음 -> 불완전 -> 유지
                self._write(d)
                with mock.patch.object(si, "build_items", return_value=([si.make_item("ev", EV, TODAY_ISO)], True)):
                    self.assertIsNone(si.run())                # 100 -> 1 급감 -> 유지
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["count"], 100)

    def test_run_writes_when_healthy(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); out = d / "search_index.json"; self._write(d)
            with mock.patch.object(si, "OUTPUT_PATH", out), mock.patch.object(si, "BASE_DIR", d), \
                    mock.patch.object(si, "date", mock.Mock(today=lambda: TODAY)):
                res = si.run()
            self.assertEqual(res, out)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual((data["v"], data["count"]), (1, 3))
            self.assertFalse(out.with_suffix(".json.tmp").exists())

    def test_run_never_raises(self):
        with mock.patch.object(si, "build_items", side_effect=RuntimeError("boom")):
            self.assertIsNone(si.run())

    def test_size_guard(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); out = d / "search_index.json"
            big = [["i%d" % i, "ev", "t" * 50, "s", "", "", "", "h" * 50] for i in range(10)]
            with mock.patch.object(si, "OUTPUT_PATH", out), mock.patch.object(si, "MAX_BYTES", 100), mock.patch.object(si, "build_items", return_value=(big, True)):
                self.assertIsNone(si.run())
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
