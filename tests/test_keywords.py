"""교구 키워드 보강 단위 테스트 (네트워크/DB 없음). 실제 폴더 적용 후 tests/test_keywords.py 로 복사해 실행:
    python -m unittest tests.test_keywords -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CATEGORIES  # noqa: E402
from parser.category_filter import quick_prefilter  # noqa: E402
from parser import extract_schedule as ex  # noqa: E402


class TestToyKeywords(unittest.TestCase):
    def test_config_keywords(self):
        kws = CATEGORIES["육아용품"]["keywords"]
        for w in ("똑쟁낱말카드", "낱말카드", "자석블럭"):
            self.assertIn(w, kws)

    def test_rule_based_category(self):
        self.assertEqual(ex._line_category("똑쟁낱말카드 10/12~10/15 공구"), "육아용품")
        self.assertEqual(ex._line_category("벨베이비 자석블럭 세트 특가"), "육아용품")

    def test_prefilter_passes_group_buy_post(self):
        self.assertTrue(quick_prefilter("📢 똑쟁낱말카드 공구 오픈! 10/12~10/15 프로필 링크에서 확인하세요"))

    def test_prefilter_still_rejects_chatter(self):
        self.assertFalse(quick_prefilter("오늘은 아이랑 낱말카드로 놀았어요. 너무 귀엽죠"))


if __name__ == "__main__":
    unittest.main()
