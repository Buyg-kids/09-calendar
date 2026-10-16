import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import collect_curation as cc  # noqa: E402
from generator import spot_enriched_validator as v  # noqa: E402


class Guardrails(unittest.TestCase):
    def test_ad_filter(self):
        for s in ["본 영상은 협찬을 받아 제작", "원고료를 지급받음", "체험단으로 방문", "소정의 수수료", "식사권 제공", "#광고", "유료 광고 포함"]:
            self.assertTrue(cc.is_ad(s), s)
        self.assertFalse(cc.is_ad("주말에 아이랑 다녀왔어요. 주차장이 넓어요"))

    def test_place_and_topic_sentences(self):
        self.assertTrue(cc.mentions_place("서울숲", "오늘은 서울 숲 나들이"))
        self.assertFalse(cc.mentions_place("서울숲", "한강 공원 영상"))
        txt = "서울숲 주차장은 주말 오전 11시 전에 만차가 돼요. 아이랑 걷기 좋아요."
        self.assertEqual(len(cc.topic_sentences(txt, "parking", "서울숲")), 1)

    def test_tokens_ok(self):
        src = "주말 오전 11시 전에 주차장이 만차가 돼요"
        self.assertTrue(cc.tokens_ok("주말엔 11시 전에 주차장이 가득 차요", src))
        self.assertFalse(cc.tokens_ok("주말엔 9시 전에 주차장이 가득 차요", src))              # 출처에 없는 숫자
        self.assertFalse(cc.tokens_ok("수유실이 깨끗해요", src))                               # 출처에 없는 시설

    def test_filters(self):
        self.assertTrue(cc.passes_filters("주말 오전에는 주차가 수월해요"))
        for bad in ["문의 010-1234-5678", "메일 a@b.com", "씨발 최악", "무조건 안전해요", "가" * 121, ""]:
            self.assertFalse(cc.passes_filters(bad), bad)

    def test_cross_check_needs_two_channels(self):
        one = [{"topic": "parking", "channel": "A", "text": "x", "url": "u1"}, {"topic": "parking", "channel": "A", "text": "y", "url": "u2"}]
        self.assertEqual(cc.aggregate(one), {})
        two = one + [{"topic": "parking", "channel": "B", "text": "z", "url": "u3"}, {"topic": "tip", "channel": "A", "text": "w", "url": "u4"}]
        agg = cc.aggregate(two)
        self.assertEqual(sorted(agg), ["parking"]); self.assertEqual(len(agg["parking"]), 2)


class ItemShape(unittest.TestCase):
    def item(self):
        srcs = [{"channel": "A", "url": "https://www.youtube.com/watch?v=aaa"}, {"channel": "B", "url": "https://www.youtube.com/watch?v=bbb"}]
        return cc.make_item("parking", "주말엔 오전 일찍 가면 주차가 수월해요", srcs, "2026-10-16")

    def run_v(self, mut=None):
        it = self.item()
        if mut:
            mut(it)
        sp = {"contentid": "126712", "matched_title": "서울숲", "address": "서울특별시 성동구 뚝섬로 273", "geo": {"lat": 37.5, "lon": 127.0, "source": "https://api.visitkorea.or.kr/", "checked": "2026-10-16"},
              "photo": None, "photo_status": "no_photo", "nearby": {"radius_m": 2000, "eats": [], "cafes": []},
              "curation": {"status": "reviewed", "parent_tips": [it], "parking_tip": None, "stroller_difficulty": None, "recommended_ages": None}}
        return v.validate({"spots": {"spot_04": sp}}, {"spot_04"})

    def test_made_item_passes_validator(self):
        self.assertEqual(self.run_v(), [])
        self.assertTrue(self.item()["text"].endswith("(유튜브 실방문 후기)"))

    def test_validator_rejects_unsafe_auto_items(self):
        for mut in [lambda i: i.update(source_count=1), lambda i: i.update(sponsor_risk=True), lambda i: i.pop("review"), lambda i: i["review"].update(mode="robot"),
                    lambda i: i.update(text="주차가 수월해요"), lambda i: i.update(src_urls=["https://evil.example/x"]), lambda i: i.update(text="협찬 받았지만 좋아요 (유튜브 실방문 후기)"),
                    lambda i: i.update(text="전화 010-1234-5678 (유튜브 실방문 후기)"), lambda i: i.update(topic="기타")]:
            self.assertTrue(self.run_v(mut), mut)


class Budget(unittest.TestCase):
    def test_calls_limit_persists_and_resets_daily(self):
        import json, tempfile
        d = Path(tempfile.mkdtemp()); f = d / "u.json"
        b = cc.LLMBudget(f, "2026-10-16", max_calls=3, max_usd=10)
        for _ in range(3):
            self.assertTrue(b.allow()); b.charge(100, 50)
        self.assertFalse(b.allow())
        b2 = cc.LLMBudget(f, "2026-10-16", max_calls=3, max_usd=10)               # 같은 날 다시 실행해도 합산
        self.assertEqual(b2.calls, 3); self.assertFalse(b2.allow())
        b3 = cc.LLMBudget(f, "2026-10-17", max_calls=3, max_usd=10)               # 날짜가 바뀌면 초기화
        self.assertTrue(b3.allow()); self.assertEqual(b3.calls, 0)
        self.assertIn("calls", json.loads(f.read_text(encoding="utf-8")))

    def test_usd_limit_stops_before_call_limit(self):
        import tempfile
        b = cc.LLMBudget(Path(tempfile.mkdtemp()) / "u.json", "2026-10-16", max_calls=30, max_usd=0.50)
        b.charge(4_000_000, 1_000_000)                                            # 기본 단가(입력 $0.10·출력 $0.50): 0.4 + 0.5 = $0.9
        self.assertFalse(b.allow())

    def test_default_prices_match_haiku_5_5(self):
        self.assertEqual((cc.LLM_MODEL, cc.PRICE_IN_PER_MTOK, cc.PRICE_OUT_PER_MTOK), ("claude-haiku-5-5", 0.10, 0.50))

    def test_defaults_match_decision_and_budget_sentinel(self):
        self.assertEqual((cc.MAX_LLM_CALLS, cc.MAX_LLM_USD), (30, 0.50))
        import tempfile
        b = cc.LLMBudget(Path(tempfile.mkdtemp()) / "u.json", "2026-10-16", max_calls=0)
        self.assertEqual(cc.summarize(["주차장이 넓어요."], "서울숲", "parking", True, b), "BUDGET")   # 한도 0 이면 호출 없이 신호만

    def test_kill_switch(self):
        import os
        os.environ["CURATION_DISABLED"] = "1"
        try:
            self.assertTrue(cc.killed())
        finally:
            del os.environ["CURATION_DISABLED"]


if __name__ == "__main__":
    unittest.main()
