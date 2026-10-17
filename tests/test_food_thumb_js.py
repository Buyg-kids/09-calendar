"""food_thumb.js(식당·카페 썸네일 손그림 매칭) node 테스트를 unittest 에서 실행."""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestFoodThumbJs(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node 가 없으면 건너뜀")
    def test_node_food_thumb_suite(self):
        r = subprocess.run(["node", str(ROOT / "tests" / "food_thumb_test.js")], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("food_thumb tests passed", r.stdout)


if __name__ == "__main__":
    unittest.main()
