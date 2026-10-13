"""수정 전 자동 스냅샷: 주요 데이터와 UI/산출물을 backups/snapshot_YYYYMMDD_HHMMSS/ 에 저장한다.

  python tools/backup_snapshot.py                       # 스냅샷 생성 (설명 없음)
  python tools/backup_snapshot.py --note "통합 검색 적용 전"   # 설명 붙이기 (--list 에 표시)

- 대상: data/gonggu.db(SQLite 백업 API 로 일관성 있게 복사), data/targets.json, free_spots_seed.json,
  index.html, map.html, tour_events.json, seoul_reserve.json, kids_performances.json, search_index.json,
  generator/templates/view_page.html (UI 원본 템플릿).  없는 파일은 건너뛰고 manifest 에 기록한다.
- 최근 20개만 유지한다(초과분은 오래된 순으로 삭제). backups/ 는 .gitignore 대상이다(DB·데이터가 들어 있어 공개 저장소에 올리면 안 된다).
- 복원은 tools/rollback_snapshot.py. 야간 파이프라인(Step 4 git add -A)이 커밋하지 않도록 backups/ 는 gitignore 한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR_NAME = "backups"
KEEP = 20
SNAP_RE = re.compile(r"^snapshot_\d{8}_\d{6}(?:_\d+)?$")
TARGETS = [
    "data/gonggu.db", "data/targets.json", "free_spots_seed.json",
    "index.html", "map.html", "tour_events.json", "seoul_reserve.json", "kids_performances.json", "search_index.json",
    "generator/templates/view_page.html",
]


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _copy_sqlite(src: Path, dst: Path) -> None:
    """실행 중인 파이프라인이 쓰는 중이어도 일관된 복사본을 만든다(SQLite online backup)."""
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=60)
    try:
        d = sqlite3.connect(str(dst))
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def snapshot_dirs(root: Path = ROOT) -> list[Path]:
    base = root / BACKUP_DIR_NAME
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir() if p.is_dir() and SNAP_RE.match(p.name)), key=lambda p: p.name)


def create_snapshot(root: Path = ROOT, note: str = "", targets: list[str] | None = None, keep: int = KEEP, now: datetime | None = None) -> Path:
    base = root / BACKUP_DIR_NAME
    base.mkdir(exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    name, n = f"snapshot_{stamp}", 1
    while (base / name).exists():          # 같은 초에 두 번 만들어도 덮어쓰지 않는다
        n += 1
        name = f"snapshot_{stamp}_{n}"
    dest = base / name
    dest.mkdir()
    files, missing = [], []
    for rel in (targets if targets is not None else TARGETS):
        src = root / rel
        if not src.is_file():
            missing.append(rel)
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".db":
            _copy_sqlite(src, out)
        else:
            shutil.copy2(src, out)
        files.append({"path": rel, "size": out.stat().st_size, "sha256_16": _sha(out)})
    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "note": note, "files": files, "missing": missing}
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    if keep > 0:
        for old in snapshot_dirs(root)[:-keep]:
            shutil.rmtree(old, ignore_errors=True)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="수정 전 자동 스냅샷")
    ap.add_argument("--note", default="", help="스냅샷 설명 (예: 통합 검색 적용 전)")
    args = ap.parse_args()
    try:
        dest = create_snapshot(note=args.note)
    except Exception as e:      # 백업 실패는 작업 착수 전에 눈에 띄게 알린다
        print(f"스냅샷 실패: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    m = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    total = sum(f["size"] for f in m["files"])
    print(f"스냅샷 생성: {dest.relative_to(ROOT)}  (파일 {len(m['files'])}개, {total / 1e6:.1f}MB, 없음 {len(m['missing'])}개)  보관 {len(snapshot_dirs())}/{KEEP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
