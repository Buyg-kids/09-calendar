"""키워드 알림 매칭 단위 테스트 (네트워크/DB 없음). 실행:  python -m unittest tests.test_alert_matcher -v"""
import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generator import alert_matcher as am  # noqa: E402

TODAY = date(2026, 10, 10)


def row(product, brand="", benefit="", handle="mom_a", start="2026-10-10", end="2026-10-14", purchase="", post="https://www.instagram.com/p/AAA/"):
    return {"product_name": product, "brand": brand, "brand_product": f"{brand} {product}".strip() if brand else product,
            "key_benefit": benefit, "influencer_name": handle, "influencer_handle": handle, "influencer_label": f"@{handle}",
            "start_date": start, "end_date": end, "purchase_url": purchase, "post_url": post}


class TestPure(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(am.normalize("트립 트랩"), am.normalize("트립-트랩"))
        self.assertEqual(am.normalize("TripTrap!"), "triptrap")

    def test_message_template_exact(self):
        msg = am.build_message("테스트고객", "트립트랩", "스토케 트립트랩 하이체어", "https://example.com/x")
        self.assertEqual(msg, "[BUYG 공구 알림]\n안녕하세요, 테스트고객님!\n요청하신 '트립트랩' 공구 일정이 오픈되었습니다.\n"
                              "- 상품: 스토케 트립트랩 하이체어\n- 링크: https://example.com/x\n즐거운 하루 되세요! 😊")

    def test_matching_fields_and_order(self):
        kws = [{"id": 1, "keyword": "트립트랩", "norm": am.normalize("트립트랩"), "customer_name": "A"}]
        rows = [row("트립트랩 하이체어", brand="스토케", purchase="https://shop/x"),
                row("아기 물티슈", benefit="트립 트랩 증정"),
                row("장난감", brand="무관"),
                row("의자", handle="mom_b", post="https://www.instagram.com/p/BBB/")]   # 다른 게시물(같은 게시물이면 본문 매칭은 합쳐지거나 버려진다)
        caps = {("mom_b", "의자"): "오늘의 공구 트립트랩 오픈! 댓글 주세요"}
        ms = am.match_deals(rows, kws, caps, TODAY.isoformat())
        self.assertEqual([(m["deal_title"], m["matched_in"]) for m in ms],
                         [("스토케 트립트랩 하이체어", "상품명"), ("아기 물티슈", "혜택 요약"), ("의자", "본문")])
        self.assertEqual(ms[0]["deal_url"], "https://shop/x")
        self.assertTrue(ms[1]["deal_url"].startswith("https://www.instagram.com/p/"))     # 구매 링크 없으면 게시물
        self.assertIn("트립트랩", ms[0]["message"])

    def test_body_matches_collapsed_per_post(self):
        kws = [{"id": 1, "keyword": "트립트랩", "norm": "트립트랩", "customer_name": "A"}]
        post = "https://www.instagram.com/p/MULTI/"
        rows = [row("프레벨롱", handle="mini", post=post), row("낱말카드", handle="mini", post=post), row("스티커북", handle="mini", post=post)]
        caps = {("mini", r["product_name"]): "하이체어 커버(트립트랩) 소개 + 낱말카드 + 스티커북" for r in rows}
        ms = am.match_deals(rows, kws, caps, TODAY.isoformat())
        self.assertEqual(len(ms), 1)                                  # 한 게시물 -> 한 건
        self.assertTrue(ms[0]["needs_review"])
        self.assertIn("외 2종", ms[0]["deal_title"])
        self.assertIn("트립트랩", ms[0]["deal_title"])

    def test_body_match_dropped_when_post_has_product_level_hit(self):
        kws = [{"id": 1, "keyword": "트립트랩", "norm": "트립트랩", "customer_name": "A"}]
        post = "https://www.instagram.com/p/MULTI/"
        rows = [row("이지업 와이더커버 (트립트랩)", handle="mini", post=post), row("스티커북", handle="mini", post=post)]
        caps = {("mini", r["product_name"]): "이지업 와이더커버(트립트랩) + 스티커북" for r in rows}
        ms = am.match_deals(rows, kws, caps, TODAY.isoformat())
        self.assertEqual([(m["deal_title"], m["matched_in"], m["needs_review"]) for m in ms], [("이지업 와이더커버 (트립트랩)", "상품명", False)])

    def test_deal_url_fallback_to_site(self):
        kws = [{"id": 1, "keyword": "장난감", "norm": "장난감", "customer_name": "A"}]
        ms = am.match_deals([row("장난감", post="")], kws, {}, TODAY.isoformat())
        self.assertEqual(ms[0]["deal_url"], am.SITE_URL)

    def test_expired_deals_skipped(self):
        kws = [{"id": 1, "keyword": "장난감", "norm": "장난감", "customer_name": "A"}]
        self.assertEqual(am.match_deals([row("장난감", start="2026-10-01", end="2026-10-05")], kws, {}, TODAY.isoformat()), [])

    def test_one_match_per_keyword_deal_pair(self):
        kws = [{"id": 1, "keyword": "트립트랩", "norm": "트립트랩", "customer_name": "A"},
               {"id": 2, "keyword": "트립트랩", "norm": "트립트랩", "customer_name": "B"}]
        ms = am.match_deals([row("트립트랩", brand="트립트랩", benefit="트립트랩")], kws, {}, TODAY.isoformat())
        self.assertEqual(sorted(m["customer_name"] for m in ms), ["A", "B"])     # 고객별로 1건씩, 필드가 여러 개 걸려도 중복 없음


class TestRunAndFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.kw = self.tmp / "alert_keywords.json"
        self.state = self.tmp / "alert_state.json"
        self.patches = [mock.patch.object(am, "OUTPUT_DIR", self.tmp), mock.patch.object(am, "OUTPUT_JSON", self.tmp / "matched_alerts_today.json"),
                        mock.patch.object(am, "OUTPUT_TXT", self.tmp / "matched_alerts_today.txt")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_kw(self, data):
        self.kw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def run_am(self, rows, **kw):
        return am.run(rows, TODAY, keywords_path=self.kw, state_path=self.state, captions={}, **kw)

    def test_init_creates_sample_without_overwriting(self):
        self.assertTrue(am.init_keywords_file(self.kw))
        data = json.loads(self.kw.read_text(encoding="utf-8"))
        self.assertEqual(data[0], {"id": 1, "keyword": "트립트랩", "customer_name": "테스트고객", "created_at": "2026-10-09", "status": "active"})
        self.kw.write_text("[]", encoding="utf-8")
        self.assertFalse(am.init_keywords_file(self.kw))
        self.assertEqual(self.kw.read_text(encoding="utf-8"), "[]")

    def test_inactive_and_short_keywords_ignored(self):
        self.write_kw([{"id": 1, "keyword": "트립트랩", "customer_name": "A", "status": "paused"},
                       {"id": 2, "keyword": "아", "customer_name": "B", "status": "active"},
                       {"id": 3, "keyword": "  ", "customer_name": "C", "status": "active"},
                       {"id": 4, "keyword": "물티슈", "customer_name": "D", "status": "active"}, "깨진 항목", 5])
        self.assertEqual([k["keyword"] for k in am.load_keywords(self.kw)], ["물티슈"])

    def test_outputs_and_dedupe_across_days(self):
        self.write_kw([{"id": 1, "keyword": "트립트랩", "customer_name": "테스트고객", "created_at": "2026-10-09", "status": "active"}])
        rows = [row("트립트랩 하이체어", brand="스토케", purchase="https://shop/x"), row("무관 상품")]
        r1 = self.run_am(rows)
        self.assertEqual((r1["total_matches"], r1["new_count"], r1["repeat_count"]), (1, 1, 0))
        out = json.loads((self.tmp / "matched_alerts_today.json").read_text(encoding="utf-8"))
        self.assertEqual(out["matches"][0]["customer_name"], "테스트고객")
        self.assertTrue(out["matches"][0]["is_new"])
        txt = (self.tmp / "matched_alerts_today.txt").read_text(encoding="utf-8")
        self.assertIn("안녕하세요, 테스트고객님!", txt)
        self.assertIn("- 링크: https://shop/x", txt)
        r2 = self.run_am(rows)      # 다음 날 같은 공구 -> 이미 안내함
        self.assertEqual((r2["total_matches"], r2["new_count"], r2["repeat_count"]), (1, 0, 1))
        self.assertIn("새로 매칭된", (self.tmp / "matched_alerts_today.txt").read_text(encoding="utf-8"))
        r3 = self.run_am(rows + [row("트립트랩 매트", start="2026-10-12")])      # 새 공구는 새로 안내
        self.assertEqual(r3["new_count"], 1)

    def test_no_state_preview_does_not_record(self):
        self.write_kw([{"id": 1, "keyword": "트립트랩", "customer_name": "A", "status": "active"}])
        rows = [row("트립트랩 하이체어")]
        self.assertEqual(self.run_am(rows, update_state=False)["new_count"], 1)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.run_am(rows, update_state=False)["new_count"], 1)

    def test_missing_or_broken_files_never_raise(self):
        self.assertIsNone(self.run_am([row("x")]))                       # 키워드 파일 없음
        self.kw.write_text("{ 깨진 json", encoding="utf-8")
        self.assertIsNone(self.run_am([row("x")]))
        self.write_kw([{"id": 1, "keyword": "트립트랩", "customer_name": "A", "status": "active"}])
        self.state.write_text("깨진 상태", encoding="utf-8")                # 상태 파일이 깨져도 계속
        self.assertEqual(self.run_am([row("트립트랩")])["new_count"], 1)
        self.assertIsNone(am.run(None, TODAY, keywords_path=self.kw, state_path=self.state, captions={}))   # rows=None 같은 이상 입력도 예외 없음

    def test_log_has_no_traceback_and_survives_cp949_console(self):
        self.write_kw([{"id": 1, "keyword": "트립트랩", "customer_name": "A", "status": "active"}])
        with self.assertLogs(am.logger, level=logging.INFO) as cm:
            self.run_am([row("트립트랩")])
        joined = "\n".join(cm.output)
        self.assertIn("키워드 알림", joined)
        self.assertNotIn("Traceback", joined)
        fake = mock.Mock(encoding="cp949")
        with mock.patch.object(am.sys, "stderr", fake):
            safe = am._log_safe("즐거운 하루 되세요! 😊")
        safe.encode("cp949")      # 인코딩 가능해야 한다(UnicodeEncodeError 없음)

    def test_card_news_hook_never_raises(self):
        try:
            from generator import card_news
        except Exception as e:  # noqa: BLE001
            self.skipTest("card_news import 불가: " + type(e).__name__)
        with mock.patch.object(am, "run", side_effect=RuntimeError("boom")):
            card_news._match_alert_keywords([row("x")], TODAY)
        with mock.patch.object(am, "run", return_value=None) as r:
            card_news._match_alert_keywords([row("x")], TODAY)
            r.assert_called_once()


class TestNotifierSkeleton(unittest.TestCase):
    def test_default_is_null_and_sends_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ALERT_PUSH_PROVIDER", None)
            n = am.get_notifier()
        self.assertEqual(n.name, "none")
        self.assertFalse(n.send("x"))

    def test_provider_selection(self):
        self.assertEqual(am.get_notifier("kakao_memo").name, "kakao_memo")
        self.assertEqual(am.get_notifier("telegram").name, "telegram")
        self.assertEqual(am.get_notifier("모르는값").name, "none")

    def test_push_summary_only_for_new_and_hides_customer_names(self):
        sent = []
        class Spy:
            name = "spy"
            def send(self, text): sent.append(text); return True
        res = {"new_count": 2, "matches": [{"is_new": True, "keyword": "트립트랩", "customer_name": "홍길동"},
                                            {"is_new": True, "keyword": "물티슈", "customer_name": "김영희"}]}
        self.assertTrue(am.push_admin_summary(res, Spy()))
        self.assertIn("신규 2건", sent[0])
        self.assertNotIn("홍길동", sent[0])
        self.assertFalse(am.push_admin_summary({"new_count": 0, "matches": []}, Spy()))
        self.assertEqual(len(sent), 1)

    def test_push_summary_never_raises(self):
        class Boom:
            name = "boom"
            def send(self, text): raise RuntimeError("x")
        self.assertFalse(am.push_admin_summary({"new_count": 1, "matches": [{"is_new": True, "keyword": "a", "customer_name": "b"}]}, Boom()))

    def test_telegram_and_kakao_skeletons_do_not_raise(self):
        self.assertFalse(am.TelegramNotifier().send("x"))
        with mock.patch("generator.send_to_me.send_alert", side_effect=RuntimeError("no token")):
            self.assertFalse(am.KakaoMemoNotifier().send("x"))


if __name__ == "__main__":
    unittest.main()
