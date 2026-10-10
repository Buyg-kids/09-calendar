"""경기 공공서비스예약 수집기(Draft) 단위 테스트.  python -m unittest tests.test_gg_reserve_collector -v"""
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector import gg_reserve_collector as gg  # noqa: E402

SPEC = {"name": "TestSvc", "label": "테스트 체험", "fields": {"id": "ID", "title": "NM", "start": "BGN", "end": "END", "address": "ADDR", "url": "URL", "fee": "FEE"}}
OK = {"TestSvc": [{"head": [{"list_total_count": 2}, {"RESULT": {"CODE": "INFO-000", "MESSAGE": "정상 처리되었습니다."}}]},
                  {"row": [{"ID": "1", "NM": "숲 체험", "BGN": "2026-10-20", "END": "2026-10-20", "ADDR": "경기도 용인시", "URL": "https://example.com/a", "FEE": "무료"},
                           {"ID": "2", "NM": "지난 행사", "BGN": "2026-09-01", "END": "2026-09-02", "URL": "https://example.com/b"}]}]}


class TestParse(unittest.TestCase):
    def test_ok(self):
        rows, total, code = gg.parse_response(OK, "TestSvc")
        self.assertEqual((len(rows), total, code), (2, 2, "INFO-000"))

    def test_no_data_and_errors(self):
        self.assertEqual(gg.parse_response({"RESULT": {"CODE": "INFO-200", "MESSAGE": "없음"}}, "TestSvc")[2], "INFO-200")
        self.assertEqual(gg.parse_response({"RESULT": {"CODE": "ERROR-300"}}, "TestSvc")[2], "ERROR-300")
        for bad in (None, [], "x", {"Other": []}):
            self.assertEqual(gg.parse_response(bad, "TestSvc")[2], "PARSE")


class TestNormalize(unittest.TestCase):
    TODAY = date(2026, 10, 10)

    def test_keeps_live_item_with_https(self):
        raw = OK["TestSvc"][1]["row"][0]
        it = gg.normalize_row(raw, SPEC, self.TODAY)
        self.assertEqual((it["id"], it["region"], it["reserve_url"], it["source"]), ("gg_TestSvc_1", "경기", "https://example.com/a", "경기데이터드림"))
        self.assertEqual(it["fee_type"], "unknown")   # 요금은 단정하지 않는다
        self.assertTrue(it["link"].startswith("https://map.naver.com/p/search/"))

    def test_drops_expired_and_unsafe(self):
        self.assertIsNone(gg.normalize_row(OK["TestSvc"][1]["row"][1], SPEC, self.TODAY))
        self.assertIsNone(gg.normalize_row({"ID": "3", "NM": "링크 없음", "BGN": "2026-10-20"}, SPEC, self.TODAY))
        self.assertIsNone(gg.normalize_row({"ID": "4", "NM": "http", "BGN": "2026-10-20", "URL": "http://x.com"}, SPEC, self.TODAY))
        self.assertIsNone(gg.normalize_row({"ID": "5", "NM": "", "URL": "https://x.com"}, SPEC, self.TODAY))


class TestRunSafety(unittest.TestCase):
    def test_no_services_is_noop(self):
        with mock.patch.object(gg, "SERVICES", []):
            self.assertIsNone(gg.run())

    def test_never_raises_and_keeps_file_on_failure(self):
        with mock.patch.object(gg, "SERVICES", [SPEC]), mock.patch.dict("os.environ", {"GG_DATA_KEY": "k"}), \
                mock.patch.object(gg.requests, "get", side_effect=RuntimeError("boom")), mock.patch.object(gg, "OUTPUT_PATH", Path("nonexistent_dir") / "gg.json"):
            self.assertIsNone(gg.run())

    def test_dry_run_returns_summary(self):
        resp = mock.Mock(); resp.json.return_value = OK
        with mock.patch.object(gg, "SERVICES", [SPEC]), mock.patch.dict("os.environ", {"GG_DATA_KEY": "k"}), mock.patch.object(gg.requests, "get", return_value=resp):
            out = gg.run(dry_run=True)
        self.assertTrue(out["complete"])
        self.assertGreaterEqual(out["kept"], 0)


if __name__ == "__main__":
    unittest.main()
