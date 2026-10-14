"""planner.js(주말 일정 플래너 순수 로직) node 테스트를 unittest 에서 실행.  python -m unittest tests.test_planner_js -v"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestPlannerJs(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node 가 없으면 건너뜀")
    def test_node_planner_suite(self):
        r = subprocess.run(["node", str(ROOT / "tests" / "planner_test.js")], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("planner tests passed", r.stdout)


if __name__ == "__main__":
    unittest.main()
