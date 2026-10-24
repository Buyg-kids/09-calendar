"""무료 풀 장소 파일 생성기 단위 테스트 (네트워크 없음).  python -m unittest tests.test_build_pool_free -v"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_pool_free as b  # noqa: E402


def item(pid, title="장소", region="서울"):
    return {"id": pid, "contentid": pid.split("_")[1], "title": title, "address": region + " 어딘가", "region": region, "geo": {"lat": 37.5, "lon": 127.0}, "category": "문화시설",
            "photo": {"thumb": "https://tong.visitkorea.or.kr/t.jpg"}, "provenance": "tourapi", "matched_keywords": ["x"], "source_url": "https://api.visitkorea.or.kr/", "fetched_at": "2026-10-23", "verified": False}


POOL = {"themes": {"museum": {"label": "박물관", "keywords": ["박물관"], "items": [item("pool_1", "무료박물관"), item("pool_2", "유료박물관"), item("pool_3", "정보없음")]},
                   "science": {"label": "과학·탐구", "keywords": ["과학관"], "items": [item("pool_1", "무료박물관"), item("pool_4", "무료과학관", "경남")]},
                   "animal": {"label": "동물", "keywords": ["동물원"], "items": [item("pool_2", "유료박물관")]}}}
ENR = {"generated_at": "2026-10-24", "spots": {
    "pool_1": {"details": {"fee": "무료", "free": True, "checked": "2026-10-24"}},
    "pool_2": {"details": {"fee": "어른 4,000원", "free": False, "checked": "2026-10-24"}},
    "pool_3": {"details": {"hours": "09:00~18:00", "checked": "2026-10-24"}},
    "pool_4": {"details": {"fee": "무료입장", "free": True, "checked": "2026-10-24"}}}}


class TestBuild(unittest.TestCase):
    def test_only_free_confirmed_places(self):
        d = b.build(POOL, ENR)
        self.assertEqual(b.unique_ids(d), {"pool_1", "pool_4"})                  # 유료·정보 없음은 제외
        self.assertEqual(sorted(d["themes"]), ["museum", "science"])              # 무료 장소가 없는 테마(animal)는 만들지 않는다
        self.assertEqual(d["generated_at"], "2026-10-24")

    def test_theme_membership_is_kept(self):
        d = b.build(POOL, ENR)
        self.assertEqual([i["id"] for i in d["themes"]["museum"]["items"]], ["pool_1"])
        self.assertEqual([i["id"] for i in d["themes"]["science"]["items"]], ["pool_1", "pool_4"])   # 같은 장소가 속한 모든 테마에 남는다
        self.assertEqual(d["themes"]["science"]["label"], "과학·탐구")

    def test_items_are_trimmed_and_marked_free(self):
        it = b.build(POOL, ENR)["themes"]["museum"]["items"][0]
        self.assertIs(it["free"], True)
        self.assertEqual(set(it), set(b.KEEP) | {"free"})
        self.assertNotIn("matched_keywords", it)
        self.assertNotIn("verified", it)

    def test_input_not_mutated(self):
        pool, enr = copy.deepcopy(POOL), copy.deepcopy(ENR)
        b.build(POOL, ENR)
        self.assertEqual(POOL, pool)
        self.assertEqual(ENR, enr)

    def test_no_free_places_gives_empty_themes(self):
        d = b.build(POOL, {"spots": {"pool_2": ENR["spots"]["pool_2"]}})
        self.assertEqual(d["themes"], {})


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.pool, self.enr, self.out = base / "pool.json", base / "enr.json", base / "free.json"
        self.pool.write_text(json.dumps(POOL, ensure_ascii=False), encoding="utf-8")
        self.enr.write_text(json.dumps(ENR, ensure_ascii=False), encoding="utf-8")
        self.args = ["--pool", str(self.pool), "--enriched", str(self.enr), "--out", str(self.out)]

    def tearDown(self):
        self.dir.cleanup()

    def test_write_then_check_passes(self):
        self.assertEqual(b.main(self.args), 0)
        self.assertEqual(b.main(self.args + ["--check"]), 0)

    def test_check_fails_when_missing_or_stale(self):
        self.assertEqual(b.main(self.args + ["--check"]), 1)                       # 파일 없음
        b.main(self.args)
        enr = copy.deepcopy(ENR)
        enr["spots"]["pool_3"]["details"].update(fee="무료", free=True)            # 새로 무료로 확인된 장소
        self.enr.write_text(json.dumps(enr, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(b.main(self.args + ["--check"]), 1)                       # 목록이 달라졌다

    def test_errors_detect_bad_entries(self):
        d = b.build(POOL, ENR)
        d["themes"]["museum"]["items"][0]["free"] = False
        self.assertTrue(any("free" in e for e in b.errors(d, ENR)))
        d = b.build(POOL, ENR)
        d["themes"]["museum"]["items"].append(dict(item("pool_2"), free=True))     # 유료로 확인된 장소가 섞이면 오류
        self.assertTrue(any("무료로 확인된 장소가 아님" in e for e in b.errors(d, ENR)))


if __name__ == "__main__":
    unittest.main()
