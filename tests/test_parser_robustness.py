"""Claude 응답 형식 이상(items 에 문자열 혼입)에 대한 파서 견고성 테스트 (네트워크 없음).
    python -m unittest tests.test_parser_robustness -v
"""
import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser import extract_schedule as ex  # noqa: E402

GOOD = {"category": "육아용품", "product_name": "테스트 장난감 세트", "brand": "", "price": "", "start_date": "2026-10-12",
        "end_date": "2026-10-15", "purchase_link": "", "key_benefit": ""}


def fake_client(payload):
    block = SimpleNamespace(type="tool_use", name="extract_group_buys", input=payload)
    resp = SimpleNamespace(content=[block], usage=None)
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **k: resp))


class TestClaudeResponseShapes(unittest.TestCase):
    def run_extract(self, payload):
        with mock.patch.object(ex, "_get_client", return_value=fake_client(payload)):
            return ex._claude_extract("테스트 공구 글 10/12~10/15", "tester", "2026-10-12")

    def test_string_items_are_skipped_not_crash(self):
        items = self.run_extract({"is_group_buy": True, "items": ["그냥 문자열", GOOD, 123, None]})
        self.assertEqual([i["product_name"] for i in items], ["테스트 장난감 세트"])

    def test_items_not_a_list(self):
        self.assertEqual(self.run_extract({"is_group_buy": True, "items": "문자열 하나"}), [])
        self.assertEqual(self.run_extract({"is_group_buy": True}), [])

    def test_normal_payload_unchanged(self):
        items = self.run_extract({"is_group_buy": True, "items": [GOOD]})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["category"], "육아용품")

    def test_dispatcher_logs_no_traceback_for_odd_shape(self):
        with mock.patch.object(ex, "ANTHROPIC_API_KEY", "x"), \
             mock.patch.object(ex, "_get_client", return_value=fake_client({"is_group_buy": True, "items": ["문자열", GOOD]})):
            with self.assertNoLogs(ex.logger, level=logging.ERROR):
                items, method = ex.extract_from_text("테스트 공구 글 10/12~10/15", "tester", "2026-10-12")
        self.assertIn(method, ("claude", "rule"))

    def test_real_api_error_still_surfaces_traceback(self):
        def boom(*a, **k):
            raise RuntimeError("credit balance is too low")
        client = SimpleNamespace(messages=SimpleNamespace(create=boom))
        with mock.patch.object(ex, "ANTHROPIC_API_KEY", "x"), mock.patch.object(ex, "_get_client", return_value=client), \
             mock.patch.object(ex, "_alert_if_credit_low"):
            with self.assertLogs(ex.logger, level=logging.ERROR) as cm:
                _items, method = ex.extract_from_text("테스트 공구 글 10/12~10/15", "tester", "2026-10-12")
        self.assertEqual(method, "rule_fallback_error")
        self.assertTrue(any(r.exc_info for r in cm.records))   # 의도적 logger.exception 은 유지


if __name__ == "__main__":
    unittest.main()
