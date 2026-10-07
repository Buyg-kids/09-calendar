"""셀러 발굴 도구·사전필터 확장 단위 테스트 (네트워크/DB 접근 없음).

실행:  python -m unittest tests.test_seller_tools -v   (프로젝트 루트에서)
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import discover_sellers as ds  # noqa: E402
import flag_verified_sellers as fv  # noqa: E402
import merge_candidates as mc  # noqa: E402
import seller_utils as su  # noqa: E402
from parser.category_filter import legacy_prefilter, quick_prefilter  # noqa: E402

SAMPLE_HTML = """
<a href="https://www.instagram.com/Mom_Gonggu.Official/">ig</a>
<a href="https://instagram.com/p/ABC123/">post</a>
<a href="https://www.instagram.com/reel/XYZ/">reel</a>
<a href="https://www.instagram.com/explore/tags/gonggu/">explore</a>
<a href="https://www.instagram.com/accounts/login/">login</a>
<a href="https://www.instagram.com/stories/someone/1/">stories</a>
<a href="https://www.instagram.com/tv/AAA/">tv</a>
<a href="https://www.instagram.com/mom_gonggu.official?igsh=1">dup</a>
<script>{"url":"https:\\/\\/www.instagram.com\\/baby.shop_01\\/"}</script>
<a href="https://www.instagram.com/instagram/">brand</a>
<a href="https://www.instagram.com/with.한글/">bad</a>
"""


class TestHandleParsing(unittest.TestCase):
    def test_extract_and_exclusions(self):
        self.assertEqual(su.extract_instagram_handles(SAMPLE_HTML), ["mom_gonggu.official", "baby.shop_01"])

    def test_handle_validation(self):
        self.assertEqual(su.extract_instagram_handles("instagram.com/" + "a" * 31 + "/"), [])
        self.assertEqual(su.extract_instagram_handles("https://instagram.com/ok_name"), ["ok_name"])

    def test_handle_of_target(self):
        self.assertEqual(su.handle_of_target({"instagram_id": "@Kelley_Mom_Dad"}), "kelley_mom_dad")
        self.assertEqual(su.handle_of_target({"instagram_id": "", "influencer_name": "ye96ye (츄릅이네)"}), "ye96ye")
        self.assertEqual(su.handle_of_target({"instagram_id": "", "influencer_name": "한글계정"}), "")

    def test_multilink_type(self):
        self.assertEqual(su.multilink_type_of("https://link.inpock.co.kr/abc"), "inpock")
        self.assertEqual(su.multilink_type_of("https://inpk.link/abc"), "inpock")
        self.assertEqual(su.multilink_type_of("https://litt.ly/abc"), "littly")
        self.assertEqual(su.multilink_type_of("https://lit.link/abc"), "litlink")
        self.assertEqual(su.multilink_type_of("https://linktr.ee/abc"), "")
        self.assertEqual(su.multilink_type_of("https://evil-litt.ly.example.com/x"), "")
        self.assertEqual(su.multilink_type_of(""), "")


class TestDiscover(unittest.TestCase):
    def test_landing_urls(self):
        text = "https://link.inpock.co.kr/momA\nhttps://litt.ly/momB/\nhttps://litt.ly/\nhttps://litt.ly/help\nhttps://link.inpock.co.kr/momA"
        self.assertEqual(
            ds.parse_landing_urls(text),
            [("https://link.inpock.co.kr/momA", "inpock"), ("https://litt.ly/momB", "littly")],
        )

    def test_build_candidates_dedups_existing(self):
        html = {"https://litt.ly/momB": "https://instagram.com/new_one https://instagram.com/already_here"}
        out = ds.build_candidates([("https://litt.ly/momB", "littly", "q")], {"already_here"}, html)
        self.assertEqual([c["handle"] for c in out], ["new_one"])
        self.assertEqual(out[0]["multilink_type"], "littly")
        self.assertEqual(out[0]["found_via_query"], "q")

    def test_queries(self):
        self.assertEqual(len(ds.QUERIES), 4)
        self.assertIn('site:litt.ly "공구 일정"', ds.QUERIES)


class TestMerge(unittest.TestCase):
    def test_select_and_entry(self):
        cands = [{"handle": "a1", "landing_url": "https://litt.ly/a1", "multilink_type": "littly"},
                 {"handle": "b2", "landing_url": "https://litt.ly/b2", "multilink_type": "littly"}]
        entries, skipped, missing = mc.select(cands, {"a1", "zz"}, False, {"b2"})
        self.assertEqual([e["instagram_id"] for e in entries], ["@a1"])
        self.assertEqual(missing, ["zz"])
        e = entries[0]
        self.assertTrue(e["is_verified_seller"])
        self.assertEqual((e["source"], e["primary_focus"]), ("discovered:sellers", "육아용품"))
        entries, skipped, _ = mc.select(cands, set(), True, {"b2"})
        self.assertEqual(([e["influencer_name"] for e in entries], skipped), (["a1"], ["b2"]))


class TestFlag(unittest.TestCase):
    def test_classify(self):
        t = [{"influencer_name": "a", "multilink_url": "https://link.inpock.co.kr/a"},
             {"influencer_name": "b", "multilink_url": "https://linktr.ee/b"},
             {"influencer_name": "c", "multilink_url": "", "extra": 1},
             {"influencer_name": "d", "multilink_url": "", "is_verified_seller": True}]
        new, empty = fv.classify(t)
        self.assertEqual([x["is_verified_seller"] for x in new], [True, False, False, True])
        self.assertEqual(new[2]["extra"], 1)  # 기존 키 보존
        self.assertNotIn("is_verified_seller", t[0])  # 입력은 바뀌지 않음
        self.assertEqual(len(empty), 2)

    def test_evidence(self):
        raw = [{"instagram": {"handle": "kelley_mom_dad", "bio_text": "육아\nlitt.ly/kelley 문의"}}]
        self.assertEqual(fv.evidence_for("kelley_mom_dad", raw), "litt.ly/kelley")
        self.assertEqual(fv.evidence_for("nobody", raw), "")


class TestPrefilter(unittest.TestCase):
    MUST_PASS = [
        '댓글에 "링크" 남겨주세요 ~10/12 자정마감',
        "댓글에 저요 남겨주시면 DM 드려요! 단 3일간 OPEN",
        "10/8~10/12 오픈! 댓글 달아주시면 DM 드릴게요",
        "상단 프로필 확인하세요 OPEN 10.8 ~ 10.12",
    ]
    MUST_FAIL = [
        "오늘은 아이랑 공원에서 산책했어요. 날씨가 너무 좋아서 기분 좋은 하루였어요",
        "댓글 많이 남겨주세요 사랑해요 우리 아기 첫 걸음마",   # CTA 비슷하지만 일정 신호 없음
        "10/8~10/12 가족 여행 다녀왔어요 즐거웠다",            # 날짜 범위만 있고 CTA 없음
        "",
        "짧음",
    ]
    LEGACY_PASS = ["오늘 공구 오픈합니다", "서울랜드 입장권 판매", "프로필 링크 확인", "오늘만 단독가 특가 진행"]

    def test_new_pass(self):
        for t in self.MUST_PASS:
            self.assertTrue(quick_prefilter(t), t)

    def test_must_fail(self):
        for t in self.MUST_FAIL:
            self.assertFalse(quick_prefilter(t), t)

    def test_no_regression(self):
        for t in self.LEGACY_PASS:
            self.assertTrue(legacy_prefilter(t), t)
            self.assertTrue(quick_prefilter(t), t)

    def test_new_branch_is_additive_only(self):
        for t in self.MUST_PASS + self.MUST_FAIL + self.LEGACY_PASS:
            if legacy_prefilter(t):
                self.assertTrue(quick_prefilter(t))


if __name__ == "__main__":
    unittest.main()
