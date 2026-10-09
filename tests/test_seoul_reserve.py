"""서울시 공공서비스예약 수집기 단위 테스트 (네트워크 없음).  python -m unittest tests.test_seoul_reserve -v

※ 응답 필드 이름은 2026-10 실측 응답(ListPublicReservationCulture/Education/Detail) 기준이다.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collector import seoul_reserve_collector as sr  # noqa: E402

TODAY = date(2026, 10, 10)


def row(**kw):
    base = {"GUBUN": "자체", "SVCID": "S1", "MAXCLASSNM": "문화체험", "MINCLASSNM": "교육체험", "SVCSTATNM": "접수중", "SVCNM": "어린이 숲 체험",
            "PAYATNM": "무료", "PLACENM": "서울숲>생태놀이터", "USETGTINFO": " 유아", "SVCURL": "https://yeyak.seoul.go.kr/web/reservation/x",
            "X": "127.04", "Y": "37.54", "SVCOPNBGNDT": "2026-10-12 00:00:00.0", "SVCOPNENDDT": "2026-10-31 00:00:00.0",
            "RCPTBGNDT": "2026-10-01 10:00:00.0", "RCPTENDDT": "2026-10-30 17:30:00.0", "AREANM": "성동구", "TELNO": "02-000-0000", "DTLCONT": ""}
    base.update(kw)
    return base


class TestTargetFilter(unittest.TestCase):
    def test_family_kept(self):
        for target, title in ((" 유아", "숲 놀이"), (" 가족(어린이 동반 가족)", "만들기"), (" 어린이", "과학교실"), (" 제한없음", "어린이 동화 체험")):
            self.assertIsNotNone(sr.kid_target_score(row(USETGTINFO=target, SVCNM=title)), (target, title))

    def test_adult_only_dropped_but_family_adult_mix_kept(self):
        self.assertIsNone(sr.kid_target_score(row(USETGTINFO=" 성인", SVCNM="가드닝 클래스", MINCLASSNM="기타")))
        self.assertIsNone(sr.kid_target_score(row(USETGTINFO=" 청년(만 19~39세)", SVCNM="청년 모임", MINCLASSNM="기타")))
        self.assertIsNone(sr.kid_target_score(row(USETGTINFO=" 어르신", SVCNM="건강 체조", MINCLASSNM="기타")))
        self.assertIsNotNone(sr.kid_target_score(row(USETGTINFO=" 성인, 어린이", SVCNM="가족 도예")))     # 어린이가 같이 있으면 유지

    def test_professional_titles_dropped(self):
        for t in ("취업 특강", "학술 세미나", "창업 포럼"):
            self.assertIsNone(sr.kid_target_score(row(SVCNM=t, USETGTINFO=" 제한없음")), t)

    def test_family_minclass_kept_even_if_target_unrestricted(self):
        self.assertIsNotNone(sr.kid_target_score(row(USETGTINFO=" 제한없음", SVCNM="서울형 키즈카페 도봉점", MINCLASSNM="서울형키즈카페")))

    def test_unrelated_dropped(self):
        self.assertIsNone(sr.kid_target_score(row(USETGTINFO=" 제한없음", SVCNM="정보통신 입문", MINCLASSNM="정보통신")))


class TestFee(unittest.TestCase):
    def test_free(self):
        f = sr.fee_from_row("무료", "")
        self.assertEqual((f["fee_type"], f["is_free"], f["fee_text"]), ("free", True, "무료"))

    def test_paid_without_amount_is_not_free(self):
        for pay in ("유료", "유료(요금안내문의)"):
            f = sr.fee_from_row(pay, "<p>모든 서비스의 이용은 담당 기관의 규정에 따릅니다.</p>")
            self.assertEqual((f["fee_type"], f["is_free"], f["fee_min"]), ("paid", False, None), pay)

    def test_paid_amount_from_description(self):
        f = sr.fee_from_row("유료", "<p>이용요금&nbsp;: 일반 2,000 원 / 어린이 1,000원</p>")
        self.assertEqual((f["fee_type"], f["is_free"], f["fee_min"]), ("paid", False, 2000))     # 요금 키워드 뒤 첫 금액
        self.assertIn("유료", f["fee_text"])
        self.assertIn("2,000", f["fee_text"])                                                    # 원문 일부 보존

    def test_cancel_fee_text_is_not_mistaken_for_price(self):
        f = sr.fee_from_row("유료", "취소 시 환불 규정: 3일 전 100% 환불")
        self.assertIsNone(f["fee_min"])

    def test_empty_pay_is_unknown_not_free(self):
        self.assertFalse(sr.fee_from_row("", "")["is_free"])


class TestNormalize(unittest.TestCase):
    def test_ok_item(self):
        it = sr.normalize_row(row(), TODAY, detail={"ADRES": "서울특별시 성동구 뚝섬로 273", "DTLCONT": ""})
        self.assertEqual((it["id"], it["region"], it["district"], it["place"], it["start_date"], it["end_date"]),
                         ("seoul_S1", "서울", "성동구", "생태놀이터", "2026-10-12", "2026-10-31"))
        self.assertEqual(it["address"], "서울특별시 성동구 뚝섬로 273")
        self.assertTrue(it["reservable"])
        self.assertEqual(it["reserve_url"], "https://yeyak.seoul.go.kr/web/reservation/x")
        self.assertTrue(it["is_free"])
        self.assertEqual(it["rcpt_end"], "2026-10-30")

    def test_region_follows_address_when_outside_seoul(self):
        it = sr.normalize_row(row(AREANM="종로구"), TODAY, detail={"ADRES": "경기도 안성시 보개면 인처골길 1", "DTLCONT": ""})
        self.assertEqual(it["region"], "경기")
        it = sr.normalize_row(row(), TODAY, detail={"ADRES": "서울특별시 성동구 뚝섬로 273", "DTLCONT": ""})
        self.assertEqual(it["region"], "서울")
        self.assertEqual(sr.normalize_row(row(), TODAY)["region"], "서울")          # 상세 주소가 없으면 서울

    def test_address_fallback_without_detail(self):
        it = sr.normalize_row(row(), TODAY)
        self.assertEqual(it["address"], "서울 성동구 생태놀이터")

    def test_not_bookable_statuses_dropped(self):
        for st in ("접수종료", "예약마감", "예약일시중지"):
            self.assertIsNone(sr.normalize_row(row(SVCSTATNM=st), TODAY), st)
        self.assertIsNotNone(sr.normalize_row(row(SVCSTATNM="안내중"), TODAY))

    def test_reception_already_closed_dropped_even_if_status_says_guide(self):
        self.assertIsNone(sr.normalize_row(row(SVCSTATNM="안내중", RCPTENDDT="2026-10-05 17:00:00.0"), TODAY))

    def test_period_filters(self):
        self.assertIsNone(sr.normalize_row(row(SVCOPNENDDT="2026-10-09 00:00:00.0"), TODAY))                         # 끝남
        self.assertIsNone(sr.normalize_row(row(SVCOPNBGNDT="2027-03-01 00:00:00.0", SVCOPNENDDT="2027-03-31 00:00:00.0"), TODAY))   # 너무 먼 미래
        self.assertIsNotNone(sr.normalize_row(row(SVCOPNBGNDT="2026-10-01 00:00:00.0"), TODAY))                      # 진행 중

    def test_bad_coordinates_dropped_but_item_kept(self):
        it = sr.normalize_row(row(X="0", Y="0"), TODAY)
        self.assertIsNotNone(it)
        self.assertIsNone(it["mapx"])
        it = sr.normalize_row(row(X="", Y=""), TODAY)
        self.assertIsNone(it["mapx"])

    def test_naver_url_uses_address_without_parentheses(self):
        u = sr.naver_map_url("서울 성북구 거점 5호 우리동네키움센터 (3층 달달마루(요리공간))", "x")
        self.assertNotIn("%29", u)                      # ')' 가 남지 않는다
        self.assertNotIn("%EB%8B%AC%EB%8B%AC", u)       # 괄호 안 '달달마루' 제외
        self.assertIn("map.naver.com/p/search/", u)

    def test_place_of(self):
        self.assertEqual(sr.place_of("서울역사박물관>백인제가옥"), "백인제가옥")
        self.assertEqual(sr.place_of("서울식물원"), "서울식물원")
        self.assertEqual(sr.place_of(""), "")

    def test_sort_by_reception_deadline_then_kid_score(self):
        a = {"title": "a", "rcpt_end": "2026-10-20", "kid_score": 1}
        b = {"title": "b", "rcpt_end": "2026-10-12", "kid_score": 0}
        c = {"title": "c", "rcpt_end": "2026-10-12", "kid_score": 3}
        self.assertEqual([x["title"] for x in sr.sort_items([a, b, c])], ["c", "b", "a"])


class TestFetchAndRunSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "seoul_reserve.json"
        self.ps = [mock.patch.object(sr, "OUTPUT_PATH", self.out), mock.patch.object(sr, "DETAIL_CACHE_PATH", self.tmp / "c.json"),
                   mock.patch.dict(os.environ, {"SEOUL_OPENAPI_KEY": "test-key"})]
        for p in self.ps:
            p.start()

    def tearDown(self):
        for p in self.ps:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def payload(self, svc, n, code="INFO-000"):
        return {svc: {"list_total_count": n, "RESULT": {"CODE": code, "MESSAGE": ""}, "row": [row(SVCID=f"S{i}") for i in range(n)]}}

    def test_fetch_service_complete_and_incomplete(self):
        svc = "ListPublicReservationCulture"
        with mock.patch.object(sr, "_get", return_value=self.payload(svc, 40)), mock.patch.object(sr.time, "sleep"):
            rows, ok = sr.fetch_service(svc, "k")
            self.assertTrue(ok)
            self.assertEqual(len(rows), 40)
        with mock.patch.object(sr, "_get", return_value=None), mock.patch.object(sr.time, "sleep"):
            self.assertEqual(sr.fetch_service(svc, "k"), ([], False))
        with mock.patch.object(sr, "_get", return_value={"RESULT": {"CODE": "ERROR-300"}}), mock.patch.object(sr.time, "sleep"):
            self.assertFalse(sr.fetch_service(svc, "k")[1])                      # 서비스 키가 없는 응답 = 불완전
        with mock.patch.object(sr, "_get", return_value=self.payload(svc, sr.PAGE)), mock.patch.object(sr.time, "sleep"):
            self.assertFalse(sr.fetch_service(svc, "k")[1])                      # 상한까지 꽉 참

    def _run_with(self, rows_by_service, complete=True):
        def fake_fetch(svc, key):
            return rows_by_service.get(svc, []), complete
        with mock.patch.object(sr, "fetch_service", side_effect=fake_fetch), mock.patch.object(sr, "fetch_detail", return_value={}), \
             mock.patch.object(sr, "date") as d:
            d.today.return_value = TODAY
            d.side_effect = lambda *a, **k: date(*a, **k)
            return sr.run()

    def _write_old(self, n):
        self.out.write_text(json.dumps({"count": n, "items": []}), encoding="utf-8")
        return self.out.read_text(encoding="utf-8")

    def test_normal_write_is_atomic_and_has_schema(self):
        rows = [row(SVCID=f"S{i}") for i in range(5)]
        res = self._run_with({"ListPublicReservationCulture": rows})
        self.assertEqual(Path(res), self.out)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(data["count"], 5)
        self.assertEqual(data["source"], "서울시 공공서비스예약")
        self.assertTrue(all(i["reservable"] and i["reserve_url"].startswith("https://") for i in data["items"]))
        self.assertFalse((self.tmp / "seoul_reserve.json.tmp").exists())

    def test_incomplete_zero_and_drop_keep_old_file(self):
        before = self._write_old(60)
        self.assertIsNone(self._run_with({"ListPublicReservationCulture": [row()]}, complete=False))
        self.assertIsNone(self._run_with({}, complete=True))
        self.assertIsNone(self._run_with({"ListPublicReservationCulture": [row(SVCID=f"S{i}") for i in range(5)]}))      # 60 -> 5
        self.assertEqual(self.out.read_text(encoding="utf-8"), before)

    def test_duplicates_across_services_collapsed(self):
        same = [row(SVCID="DUP")]
        res = self._run_with({"ListPublicReservationCulture": same, "ListPublicReservationEducation": same})
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8"))["count"], 1)

    def test_no_key_and_api_failure_never_raise(self):
        with mock.patch.dict(os.environ, {"SEOUL_OPENAPI_KEY": ""}):
            self.assertIsNone(sr.run())
            self.assertIsNone(sr.run(dry_run=True))
        before = self._write_old(60)
        with mock.patch.object(sr.requests, "get", side_effect=RuntimeError("boom")), mock.patch.object(sr.time, "sleep"):
            self.assertIsNone(sr.run())
        self.assertEqual(self.out.read_text(encoding="utf-8"), before)

    def test_key_never_in_logs(self):
        with mock.patch.object(sr.requests, "get", side_effect=RuntimeError("http://x/test-key/json")), mock.patch.object(sr.time, "sleep"):
            with self.assertLogs(sr.logger, level="ERROR") as cm:
                sr.run()
        self.assertNotIn("test-key", "\n".join(cm.output))

    def test_card_news_hook_never_raises(self):
        try:
            from generator import card_news
        except Exception as e:  # noqa: BLE001
            self.skipTest("card_news import 불가: " + type(e).__name__)
        with mock.patch.object(sr, "run", side_effect=RuntimeError("boom")):
            card_news._collect_seoul_reserve()
        with mock.patch.object(sr, "run", return_value=None) as r:
            card_news._collect_seoul_reserve()
            r.assert_called_once()


if __name__ == "__main__":
    unittest.main()
