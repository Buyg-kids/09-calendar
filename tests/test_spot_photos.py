import copy
import json
import unittest
from pathlib import Path

from generator import spot_photos_validator as v

ROOT = Path(__file__).resolve().parent.parent


def _sample():
    return {"spots": {"spot_01": {"contentid": "1177980", "matched_title": "국립중앙박물관 어린이박물관", "address": "서울특별시 용산구 서빙고로 137",
                                  "photo": {"url": "https://tong.visitkorea.or.kr/a_image2_1.jpg", "thumb": "https://tong.visitkorea.or.kr/a_image3_1.jpg", "credit": "한국관광공사 TourAPI", "license": "공공누리 제1유형(출처표시)", "contentid": "1177980", "checked": "2026-10-14"}}}}


class SpotPhotosValidator(unittest.TestCase):
    def test_sample_ok(self):
        self.assertEqual(v.validate(_sample(), {"spot_01"}), [])

    def test_rejects_bad_cases(self):
        for mut, key in [(lambda d: d["spots"]["spot_01"]["photo"].update(license="공공누리 제3유형"), "license"),
                         (lambda d: d["spots"]["spot_01"]["photo"].update(url="http://tong.visitkorea.or.kr/a.jpg"), "url"),
                         (lambda d: d["spots"]["spot_01"]["photo"].update(thumb="https://evil.example/a.jpg"), "thumb"),
                         (lambda d: d["spots"]["spot_01"]["photo"].update(checked="어제"), "checked"),
                         (lambda d: d["spots"]["spot_01"].update(contentid="abc"), "contentid"),
                         (lambda d: d["spots"]["spot_01"]["photo"].update(contentid="999"), "contentid")]:
            d = copy.deepcopy(_sample()); mut(d)
            self.assertTrue(any(key in e for e in v.validate(d, {"spot_01"})), key)
        self.assertTrue(v.validate(_sample(), {"other"}))

    def test_repo_file_if_present(self):
        p = ROOT / "spot_photos.json"
        if not p.exists():
            self.skipTest("spot_photos.json 없음")
        seed = json.loads((ROOT / "free_spots_seed.json").read_text(encoding="utf-8"))["spots"]
        self.assertEqual(v.validate(json.loads(p.read_text(encoding="utf-8")), {s["id"] for s in seed}), [])


if __name__ == "__main__":
    unittest.main()
