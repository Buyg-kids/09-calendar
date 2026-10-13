"""backup_snapshot.py 로 만든 스냅샷으로 되돌린다.

  python tools/rollback_snapshot.py --list                                    # 백업 목록(폴더명·시각·설명·파일 수)
  python tools/rollback_snapshot.py --target snapshot_20261013_113000          # 계획을 보여주고 확인 입력을 받는다
  python tools/rollback_snapshot.py --target snapshot_20261013_113000 --yes    # 확인 없이 복원
  python tools/rollback_snapshot.py --target latest --dry-run                 # 무엇이 바뀌는지만 출력

- 복원 직전에 현재 상태를 안전 스냅샷으로 한 번 더 저장한다(되돌리기를 되돌릴 수 있게). --no-safety 로 끌 수 있다.
- 스냅샷에 들어 있던 파일만 덮어쓴다(스냅샷 뒤에 새로 생긴 파일은 지우지 않는다).
- 야간 파이프라인이 실행 중일 때는 복원하지 말 것(스케줄러 State=Running 이면 경고하고 --yes 가 없으면 중단한다).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_snapshot as bs  # noqa: E402

ROOT = bs.ROOT


def read_manifest(snap: Path) -> dict:
    try:
        return json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"created": "", "note": "(manifest 없음)", "files": [], "missing": []}


def list_snapshots(root: Path = ROOT) -> list[dict]:
    out = []
    for p in bs.snapshot_dirs(root):
        m = read_manifest(p)
        out.append({"name": p.name, "created": m.get("created", ""), "note": m.get("note", ""), "files": len(m.get("files", [])),
                    "mb": round(sum(f.get("size", 0) for f in m.get("files", [])) / 1e6, 1)})
    return out


def resolve_target(target: str, root: Path = ROOT) -> Path:
    snaps = bs.snapshot_dirs(root)
    if target == "latest":
        if not snaps:
            raise SystemExit("스냅샷이 없습니다.")
        return snaps[-1]
    if not bs.SNAP_RE.match(target):          # 경로 이탈(../) 방지: 정해진 이름 형식만 허용
        raise SystemExit(f"잘못된 스냅샷 이름: {target}  (--list 로 확인)")
    p = root / bs.BACKUP_DIR_NAME / target
    if not p.is_dir():
        raise SystemExit(f"스냅샷을 찾을 수 없습니다: {target}  (--list 로 확인)")
    return p


def _restore_db(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    try:
        d = sqlite3.connect(str(dst), timeout=60)
        try:
            s.backup(d)        # 대상 DB 파일을 직접 교체하지 않고 백업 API 로 내용을 되돌린다
        finally:
            d.close()
    finally:
        s.close()


def restore(snap: Path, root: Path = ROOT, dry_run: bool = False) -> list[str]:
    m = read_manifest(snap)
    done = []
    for f in m.get("files", []):
        rel = f["path"]
        src = snap / rel
        if not src.is_file():
            continue
        done.append(rel)
        if dry_run:
            continue
        dst = root / rel
        if src.suffix == ".db":
            _restore_db(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return done


def pipeline_running() -> bool:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-ScheduledTask -TaskName InstaAutoPipeline_Daily).State"],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip() == "Running"
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="스냅샷 롤백")
    ap.add_argument("--list", action="store_true", help="백업 목록 출력")
    ap.add_argument("--target", help="복원할 스냅샷 폴더명 (또는 latest)")
    ap.add_argument("--yes", action="store_true", help="확인 없이 복원")
    ap.add_argument("--dry-run", action="store_true", help="복원 대상만 출력")
    ap.add_argument("--no-safety", action="store_true", help="복원 직전 안전 스냅샷을 만들지 않음")
    a = ap.parse_args()
    if a.list or not a.target:
        rows = list_snapshots()
        if not rows:
            print("스냅샷이 없습니다.")
        for r in rows:
            print(f"{r['name']}  {r['created']}  파일 {r['files']}개 {r['mb']}MB  {r['note']}")
        return 0
    snap = resolve_target(a.target)
    m = read_manifest(snap)
    files = restore(snap, dry_run=True)
    print(f"복원 대상: {snap.name} ({m.get('created', '')}) {m.get('note', '')}")
    for rel in files:
        print("  -", rel)
    if a.dry_run:
        return 0
    if pipeline_running():
        print("경고: 야간 파이프라인이 실행 중입니다(State=Running). 복원은 종료 후에 하세요.", file=sys.stderr)
        if not a.yes:
            return 2
    if not a.yes:
        if input("위 파일을 스냅샷 시점으로 덮어씁니다. 계속할까요? [y/N] ").strip().lower() != "y":
            print("취소했습니다.")
            return 1
    if not a.no_safety:
        safety = bs.create_snapshot(note=f"rollback 직전 (되돌리려던 대상: {snap.name})")
        print("안전 스냅샷:", safety.name)
    done = restore(snap)
    print(f"복원 완료: {len(done)}개 파일")
    return 0


if __name__ == "__main__":
    sys.exit(main())
