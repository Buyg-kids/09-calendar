# 30일 자동 운용 파이프라인 (Windows 작업 스케줄러가 매일 00:00에 새 프로세스로 실행).
#
#   Step 1 (00:00~10:00) : auto_discover_scheduler.py --tonight
#                          - 기존 안전장치 그대로 유지: 시간당 6~8개 속도 제한,
#                            4~9초 랜덤 지연, 프로필 키워드 필터, 네거티브 키워드 필터.
#                          - 큐레이션 계정(is_curator) 우선 스캔도 이 단계 안에서 자동 수행됨.
#   Step 2 (10:00~10:15) : 15분 쿨다운 (인스타그램 요청 흐름을 끊어 차단 위험을 낮춤)
#   Step 3 (10:15~)      : run_manual.py (수집 -> 파싱 -> gonggu.db 저장 -> 카드뉴스 -> view.html)
#
# 세 단계 모두 오류 없이 끝나면(exit code 0 이고 로그에 Traceback 없음) PC를 절전
# 모드로 전환한다. 하나라도 문제가 있으면 원인 분석을 위해 절전 모드로 넘어가지
# 않고 로그만 남긴 채 그대로 둔다.
#
# PowerShell 5.1이 네이티브 프로세스의 stderr를 직접 리다이렉트하면 로그가
# NativeCommandError로 깨지는 문제(run_tonight.ps1에서 실제로 겪음)를 피하기 위해
# cmd.exe 리다이렉션을 쓰고, PYTHONIOENCODING으로 UTF-8을 강제한다. 이 파일 자체도
# 경로에 한글이 섞여 있어 UTF-8 BOM으로 저장해야 한다(BOM 없으면 PowerShell 5.1이
# 시스템 코드페이지로 잘못 읽어 스크립트가 파싱 단계에서 깨지는 사고를 겪었음).

$ProjectDir = "C:\Users\user1\Desktop\클로드\인스타공구캘린더"
$Python = "C:\Users\user1\Desktop\클로드\.venv\Scripts\python.exe"

Set-Location $ProjectDir

$LogDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$DateTag = Get-Date -Format "yyyyMMdd"
$DiscoverLog = Join-Path $LogDir ("nightly_discover_{0}.log" -f $DateTag)
$PipelineLog = Join-Path $LogDir ("nightly_pipeline_{0}.log" -f $DateTag)
$SummaryLog  = Join-Path $LogDir ("nightly_summary_{0}.log" -f $DateTag)

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

function Write-Summary {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $SummaryLog -Value $line -Encoding utf8
}

Write-Summary "=== 야간 파이프라인 시작 ==="

# --- Step 1: 해시태그/큐레이션 계정 자동 탐색 (00:00~10:00, 자체적으로 시간을 지켜 종료) ---
Write-Summary "Step 1 시작: auto_discover_scheduler --tonight"
cmd /c "`"$Python`" -m scraper.auto_discover_scheduler --tonight >> `"$DiscoverLog`" 2>&1"
$discoverExit = $LASTEXITCODE
Write-Summary ("Step 1 종료 (exit={0})" -f $discoverExit)

# --- Step 2: 15분 쿨다운 ---
Write-Summary "Step 2 시작: 15분 쿨다운 대기"
Start-Sleep -Seconds 900
Write-Summary "Step 2 종료: 쿨다운 완료"

# --- Step 3: 전체 파이프라인 (수집 -> 파싱 -> DB -> 카드뉴스 -> view.html) ---
Write-Summary "Step 3 시작: run_manual.py"
cmd /c "`"$Python`" run_manual.py >> `"$PipelineLog`" 2>&1"
$pipelineExit = $LASTEXITCODE
Write-Summary ("Step 3 종료 (exit={0})" -f $pipelineExit)

# --- 성공/실패 판정 ---
# run_manual.py는 내부 각 단계(discover/collect/parse/generate)의 예외를 개별적으로
# 잡아 로그만 남기고 계속 진행하도록 설계되어 있어(scheduler/pipeline.py), 프로세스
# 자체는 내부 단계가 실패해도 exit code 0으로 끝난다. 그래서 exit code만으로는
# "치명적 에러 없음"을 보장할 수 없어, 로그에 Traceback이 있는지도 함께 확인한다.
$discoverHasError = (Test-Path $DiscoverLog) -and (Select-String -Path $DiscoverLog -Pattern "Traceback" -SimpleMatch -Quiet)
$pipelineHasError = (Test-Path $PipelineLog) -and (Select-String -Path $PipelineLog -Pattern "Traceback" -SimpleMatch -Quiet)

$allOk = ($discoverExit -eq 0) -and ($pipelineExit -eq 0) -and (-not $discoverHasError) -and (-not $pipelineHasError)

if ($allOk) {
    Write-Summary "모든 단계 정상 완료 - 절전 모드로 전환합니다."
    rundll32.exe powrprof.dll,SetSuspendState 0,1,0
} else {
    Write-Summary ("오류 감지 - 절전 모드로 전환하지 않고 유지합니다. " + `
        "(discoverExit=$discoverExit, pipelineExit=$pipelineExit, " + `
        "discoverTraceback=$discoverHasError, pipelineTraceback=$pipelineHasError)")
}
