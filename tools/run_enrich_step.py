"""야간 파이프라인 Step 3 이후·Step 4 이전에 도는 '나들이 데이터 자동 보강' 안전 실행기.

  python tools/run_enrich_step.py        (run_nightly_pipeline.ps1 이 Invoke-Isolated 로 호출, 로그는 logs/nightly_enrich_YYYYMMDD.log)

안전 규칙: ① 항상 exit 0 (실패해도 Step 4 배포를 막지 않는다) ② 실행 전 spot_enriched.json 백업 -> 오류·타임아웃·검증 실패면 복원
③ 자식 프로세스 출력의 'Traceback' 문구는 로그에 남기지 않는다(야간 로그 규칙) ④ 예외는 타입명만 기록(logger.exception 금지)
⑤ 호출 한도는 enrich_spots.py(--max-calls, 쿼터 신호 시 중단)가 지킨다."""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "spot_enriched.json"
BACKUP = ROOT / "data" / "spot_enriched.prev.json"       # data/ 는 gitignore
TIMEOUT_S = 25 * 60


def say(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def clean(text):
    return (text or "").replace("Traceback", "오류 추적(생략)")


def restore(reason):
    if BACKUP.exists():
        shutil.copyfile(BACKUP, TARGET)
        say(f"spot_enriched.json 을 이전 상태로 복원 ({reason})")


def main():
    if not TARGET.exists():
        say("spot_enriched.json 없음 - 보강 건너뜀")
        return 0
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TARGET, BACKUP)
    try:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "enrich_spots.py"), "--spots", "100", "--max-calls", "400"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
        for line in clean(r.stdout + r.stderr).splitlines()[-12:]:
            say("[보강] " + line[:200])
        if r.returncode != 0:
            restore(f"보강 스크립트 exit={r.returncode}")
            return 0
    except subprocess.TimeoutExpired:
        restore("25분 제한 시간 초과")
        return 0
    except Exception as e:      # noqa: BLE001 - 어떤 실패도 배포를 막지 않는다(타입명만 기록)
        restore(f"실행 오류 {type(e).__name__}")
        return 0
    try:
        v = subprocess.run([sys.executable, "-m", "generator.spot_enriched_validator", str(TARGET), str(ROOT / "free_spots_seed.json")], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        say("[검증] " + clean(v.stdout).strip().splitlines()[0] if v.stdout.strip() else "[검증] 출력 없음")
        if v.returncode != 0:
            restore("검증기 오류")
    except Exception as e:      # noqa: BLE001
        restore(f"검증 실행 오류 {type(e).__name__}")
    say("나들이 데이터 보강 단계 종료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
