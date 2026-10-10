"""0~7세 연령 태깅 단위 테스트.  python -m unittest tests.test_age_tags -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generator import age_tags as at  # noqa: E402


class TestExplicitAges(unittest.TestCase):
    def check(self, text, want):
        self.assertEqual(at.age_groups(text), want, text)

    def test_single_ages(self):
        self.check("3세 블록 놀이", ["toddler"])
        self.check("만 4세 이상 보드게임", ["kid", "preschool"])        # '이상' -> 7세까지
        self.check("5세용 학습 동화", ["kid"])
        self.check("7살 책가방", ["preschool"])
        self.check("0세 신생아 속싸개", ["baby"])

    def test_ranges(self):
        self.check("3~5세 교구", ["toddler", "kid"])
        self.check("2-3세 퍼즐", ["toddler"])
        self.check("4~7세 과학 키트", ["kid", "preschool"])
        self.check("0~7세 성장 맞춤", ["baby", "toddler", "kid", "preschool"])

    def test_under_over(self):
        self.check("6세 이하 놀이매트", ["baby", "toddler", "kid", "preschool"])
        self.check("2세부터 사용", ["toddler", "kid", "preschool"])
        self.check("3세까지 사용 가능", ["baby", "toddler"])

    def test_months(self):
        self.check("6개월 이유식", ["baby"])
        self.check("6~12개월 치발기", ["baby"])
        self.check("24개월 걸음마 신발", ["toddler"])
        self.check("18~36개월 놀이", ["baby", "toddler"])
        self.check("48개월 이후", ["kid"])

    def test_false_positives_are_ignored(self):
        for t in ("선물세트 3세트 구성", "특가 세일 20% 할인", "3세제 세트", "10세트 한정", "세이펜 4세대 5세대", "자기주도 행거"):
            self.assertEqual(at.age_groups(t), [], t)

    def test_out_of_range_ages_ignored(self):
        self.check("10세 이상 보드게임", [])
        self.check("12~14세 초등 고학년 문제집", [])
        self.check("성인 30세 이상 영양제", [])


class TestKeywordFallback(unittest.TestCase):
    def test_keywords_when_no_explicit_age(self):
        self.assertEqual(at.age_groups("신생아 속싸개 세트"), ["baby"])
        self.assertEqual(at.age_groups("프리미엄 젖병 2종"), ["baby"])
        self.assertEqual(at.age_groups("토들러 배변훈련 팬티"), ["toddler"])
        self.assertEqual(at.age_groups("유치원 가방"), ["kid"])
        self.assertEqual(at.age_groups("예비초등 한글 학습지"), ["preschool"])

    def test_explicit_age_beats_keyword(self):
        self.assertEqual(at.age_groups("신생아 모빌 5세까지 사용"), ["baby", "toddler", "kid"])     # 명시 나이(5세까지)가 키워드(신생아)보다 우선
        self.assertEqual(at.age_groups("초등 입학 준비 4세 이상 한글"), ["kid", "preschool"])

    def test_untagged_returns_empty(self):
        for t in ("유산균 영양제", "아기 물티슈 20팩", "주방 세제", "", None):
            self.assertEqual(at.age_groups(t), [], t)

    def test_soft_keywords_only_when_nothing_else(self):
        self.assertEqual(at.age_groups("유아 간식"), ["toddler", "kid"])
        self.assertEqual(at.age_groups("어린이 영양음료"), ["kid", "preschool"])
        self.assertEqual(at.age_groups("마이키즈 영양간식"), ["kid", "preschool"])
        self.assertEqual(at.age_groups("마이키즈 7세"), ["preschool"])            # 명시 나이가 약한 키워드보다 우선
        self.assertEqual(at.age_groups("키즈 신생아 모빌"), ["baby"])               # 강한 키워드가 약한 키워드보다 우선

    def test_order_is_stable(self):
        self.assertEqual(at.age_groups("신생아 예비초등"), ["baby", "preschool"])


class TestRowHelper(unittest.TestCase):
    def test_uses_name_brand_benefit_only(self):
        row = {"product_name": "블록 세트", "brand": "레고", "key_benefit": "4~5세 추천", "caption_text": "신생아 모빌", "influencer_name": "3세맘"}
        self.assertEqual(at.age_groups_for_row(row), ["kid"])

    def test_missing_fields(self):
        self.assertEqual(at.age_groups_for_row({}), [])


if __name__ == "__main__":
    unittest.main()
