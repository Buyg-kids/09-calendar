"""수정 전 자동 스냅샷/롤백 단위 테스트.  python -m unittest tests.test_backup_snapshot -v"""
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import backup_snapshot as bs  # noqa: E402
import rollback_snapshot as rb  # noqa: E402


def make_root(t: str) -> Path:
    root = Path(t)
    (root / "data").mkdir()
    con = sqlite3.connect(root / "data" / "gonggu.db")
    con.execute("create table gonggu (id integer primary key, name text)")
    con.execute("insert into gonggu (name) values ('before')")
    con.commit(); con.close()
    (root / "data" / "targets.json").write_text('["a"]', encoding="utf-8")
    (root / "index.html").write_text("v1", encoding="utf-8")
    return root


def db_names(root: Path) -> list:
    con = sqlite3.connect(root / "data" / "gonggu.db")
    try:
        return [r[0] for r in con.execute("select name from gonggu order by id")]
    finally:
        con.close()


class TestSnapshot(unittest.TestCase):
    def test_creates_files_manifest_and_skips_missing(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(t)
            snap = bs.create_snapshot(root, note="테스트", targets=["data/gonggu.db", "data/targets.json", "index.html", "map.html"])
            self.assertTrue(bs.SNAP_RE.match(snap.name))
            m = rb.read_manifest(snap)
            self.assertEqual((m["note"], sorted(f["path"] for f in m["files"]), m["missing"]), ("테스트", ["data/gonggu.db", "data/targets.json", "index.html"], ["map.html"]))
            self.assertEqual((snap / "index.html").read_text(encoding="utf-8"), "v1")

    def test_same_second_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(t)
            now = datetime(2026, 10, 13, 12, 0, 0)
            a = bs.create_snapshot(root, targets=["index.html"], now=now)
            b = bs.create_snapshot(root, targets=["index.html"], now=now)
            self.assertNotEqual(a.name, b.name)
            self.assertEqual(len(bs.snapshot_dirs(root)), 2)

    def test_keeps_only_latest_n(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(t)
            base = datetime(2026, 10, 1, 0, 0, 0)
            names = [bs.create_snapshot(root, targets=["index.html"], keep=3, now=base + timedelta(minutes=i)).name for i in range(6)]
            self.assertEqual([p.name for p in bs.snapshot_dirs(root)], sorted(names)[-3:])

    def test_foreign_folders_are_never_deleted(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(t)
            (root / "backups" / "notes").mkdir(parents=True)
            for i in range(3):
                bs.create_snapshot(root, targets=["index.html"], keep=1, now=datetime(2026, 10, 1, 0, i, 0))
            self.assertTrue((root / "backups" / "notes").is_dir())


class TestRollback(unittest.TestCase):
    def test_restore_roundtrip_db_and_files(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(t)
            snap = bs.create_snapshot(root, note="원본", targets=["data/gonggu.db", "index.html"])
            con = sqlite3.connect(root / "data" / "gonggu.db"); con.execute("insert into gonggu (name) values ('after')"); con.commit(); con.close()
            (root / "index.html").write_text("v2", encoding="utf-8")
            self.assertEqual(db_names(root), ["before", "after"])
            done = rb.restore(snap, root)
            self.assertEqual(sorted(done), ["data/gonggu.db", "index.html"])
            self.assertEqual(db_names(root), ["before"])
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), "v1")

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(t)
            snap = bs.create_snapshot(root, targets=["index.html"])
            (root / "index.html").write_text("v2", encoding="utf-8")
            self.assertEqual(rb.restore(snap, root, dry_run=True), ["index.html"])
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), "v2")

    def test_files_created_after_snapshot_are_kept(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(t)
            snap = bs.create_snapshot(root, targets=["index.html"])
            (root / "new.json").write_text("{}", encoding="utf-8")
            rb.restore(snap, root)
            self.assertTrue((root / "new.json").exists())

    def test_list_and_target_resolution(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(t)
            a = bs.create_snapshot(root, note="첫째", targets=["index.html"], now=datetime(2026, 10, 1, 0, 0, 0))
            b = bs.create_snapshot(root, note="둘째", targets=["index.html"], now=datetime(2026, 10, 2, 0, 0, 0))
            rows = rb.list_snapshots(root)
            self.assertEqual([r["note"] for r in rows], ["첫째", "둘째"])
            self.assertEqual(rb.resolve_target("latest", root), b)
            self.assertEqual(rb.resolve_target(a.name, root), a)

    def test_rejects_path_traversal_and_unknown(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(t)
            bs.create_snapshot(root, targets=["index.html"])
            for bad in ("../x", "..\\x", "snapshot_20990101_000000", "latest_not"):
                with self.assertRaises(SystemExit):
                    rb.resolve_target(bad, root)


if __name__ == "__main__":
    unittest.main()
