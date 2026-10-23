"""카카오 토큰 발급일 기록·만료 임박 경고 단위 테스트 (네트워크 없음).  python -m unittest tests.test_kakao_token_meta -v"""
import json
import logging
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generator import send_to_me as st  # noqa: E402


class TestTokenMeta(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.meta = Path(self.dir.name) / "data" / "kakao_token_meta.json"

    def tearDown(self):
        self.dir.cleanup()

    def write(self, issued="2026-10-23", valid=60, **extra):
        self.meta.parent.mkdir(parents=True, exist_ok=True)
        self.meta.write_text(json.dumps(dict({"issued": issued, "valid_days": valid}, **extra)), encoding="utf-8")

    def test_days_left(self):
        self.write("2026-10-23", 60)
        self.assertEqual(st.token_days_left(date(2026, 10, 23), self.meta), 60)
        self.assertEqual(st.token_days_left(date(2026, 12, 21), self.meta), 1)
        self.assertEqual(st.token_days_left(date(2026, 12, 22), self.meta), 0)
        self.assertEqual(st.token_days_left(date(2026, 12, 24), self.meta), -2)

    def test_missing_or_broken_meta_is_none(self):
        self.assertIsNone(st.token_days_left(date(2026, 10, 23), self.meta))
        self.meta.parent.mkdir(parents=True)
        for bad in ("", "not json", "{}", json.dumps({"issued": "어제", "valid_days": 60}), json.dumps({"issued": "2026-10-23", "valid_days": "x"})):
            self.meta.write_text(bad, encoding="utf-8")
            self.assertIsNone(st.token_days_left(date(2026, 10, 23), self.meta), bad)

    def test_no_warning_when_far_or_unknown(self):
        sent = []
        self.assertIsNone(st.warn_if_token_expiring(date(2026, 10, 23), meta_path=self.meta, send=sent.append))     # 기록 없음
        self.write("2026-10-23", 60)
        self.assertIsNone(st.warn_if_token_expiring(date(2026, 11, 20), meta_path=self.meta, send=sent.append))    # 아직 여유(33일)
        self.assertEqual(sent, [])

    def test_warns_within_window_and_sends_once_per_day(self):
        self.write("2026-10-23", 60)
        sent = []
        with self.assertLogs("send_to_me", level=logging.WARNING) as cm:
            m1 = st.warn_if_token_expiring(date(2026, 12, 17), meta_path=self.meta, send=sent.append)               # 5일 남음
            m2 = st.warn_if_token_expiring(date(2026, 12, 17), meta_path=self.meta, send=sent.append)               # 같은 날 다시
        self.assertIn("5일 뒤 만료", m1)
        self.assertEqual(m1, m2)
        self.assertEqual(len(sent), 1)                                                                                # 카톡은 하루 한 번
        self.assertTrue(sent[0].startswith("⚠️ [Buyg]"))
        self.assertFalse(any("Traceback" in r.getMessage() for r in cm.records))
        st.warn_if_token_expiring(date(2026, 12, 18), meta_path=self.meta, send=sent.append)                        # 다음 날은 다시 보낸다
        self.assertEqual(len(sent), 2)

    def test_expired_warns_without_sending(self):
        self.write("2026-10-23", 60)
        sent = []
        with self.assertLogs("send_to_me", level=logging.WARNING):
            m = st.warn_if_token_expiring(date(2026, 12, 29), meta_path=self.meta, send=sent.append)
        self.assertIn("7일 전에 만료", m)
        self.assertEqual(sent, [])

    def test_send_failure_is_swallowed_and_logged_by_type(self):
        self.write("2026-10-23", 60)

        def boom(_):
            raise st.KakaoError("액세스 토큰 갱신 실패 - invalid_grant")

        with self.assertLogs("send_to_me", level=logging.WARNING) as cm:
            m = st.warn_if_token_expiring(date(2026, 12, 20), meta_path=self.meta, send=boom)
        self.assertIn("2일 뒤 만료", m)
        text = " ".join(r.getMessage() for r in cm.records)
        self.assertIn("KakaoError", text)
        self.assertNotIn("invalid_grant", text)
        self.assertNotIn("Traceback", text)
        self.assertTrue(all(r.exc_info is None for r in cm.records))

    def test_write_meta_stores_only_dates(self):
        with mock.patch.object(st, "TOKEN_META", self.meta), mock.patch.object(st, "kst_today", return_value=date(2026, 10, 23)):
            st._write_token_meta(5184000)                      # 60일
            st._write_token_meta(0)                            # 유효기간을 모르면 기록하지 않는다
        m = json.loads(self.meta.read_text(encoding="utf-8"))
        self.assertEqual(m, {"issued": "2026-10-23", "valid_days": 60})
        self.assertNotIn("token", self.meta.read_text(encoding="utf-8").lower())

    def test_check_token_cli(self):
        with mock.patch.object(st, "TOKEN_META", self.meta), mock.patch.object(st, "kst_today", return_value=date(2026, 12, 20)):
            self.assertEqual(st.cmd_check_token(), 0)          # 기록 없음
            self.write("2026-10-23", 60)
            self.assertEqual(st.cmd_check_token(), 0)          # 2일 남음
            self.write("2026-09-01", 60)
            self.assertEqual(st.cmd_check_token(), 1)          # 이미 만료


if __name__ == "__main__":
    unittest.main()
