# 30일 자동 운용 파이프라인 (Windows 작업 스케줄러가 매일 00:00에 새 프로세스로 실행).
#
#   Step 1 (00:00~10:00) : auto_discover_scheduler.py --tonight
#                          - 기존 안전장치 그대로 유지: 시간당 6~8개 속도 제한,
#                            4~9초 랜덤 지연, 프로필 키워드 필터, 네거티브 키워드 필터.
#                          - 큐레이션 계정(is_curator) 우선 스캔도 이 단계 안에서 자동 수행됨.
#   Step 2 (10:00~10:15) : 15분 쿨다운 (인스타그램 요청 흐름을 끊어 차단 위험을 낮춤)
#   Step 3 (10:15~)      : run_manual.py (수집 -> 파싱 -> gonggu.db 저장 -> 카드뉴스 -> view.html/index.html)
#   Step 4                : GitHub Pages 배포 (index.html을 git add/commit/push)
#
# 세 단계 모두 오류 없이 끝나면(exit code 0 이고 로그에 Traceback 없음) index.html을
# GitHub에 푸시해 웹사이트를 갱신하고 PC를 절전 모드로 전환한다. 하나라도 문제가
# 있으면 (배포도 절전도) 건너뛰고 원인 분석을 위해 로그만 남긴 채 그대로 둔다 -
# 파이프라인이 실패한 상태의 데이터를 공개 웹사이트에 배포하지 않기 위함이다.
#
# PowerShell 5.1이 네이티브 프로세스의 stderr를 직접 리다이렉트하면 로그가
# NativeCommandError로 깨지는 문제(run_tonight.ps1에서 실제로 겪음)를 피하기 위해
# PYTHONIOENCODING으로 UTF-8을 강제한다. 이 파일 자체도 경로에 한글이 섞여 있어
# UTF-8 BOM으로 저장해야 한다(BOM 없으면 PowerShell 5.1이 시스템 코드페이지로
# 잘못 읽어 스크립트가 파싱 단계에서 깨지는 사고를 겪었음).
#
# 2026-09-07, 09-08, 09-09 세 번 연속 Step 3(수집)가 몇 시간 진행되다가 아무
# 에러 로그도 없이 STATUS_CONTROL_C_EXIT(0xC000013A)로 조용히 죽는 사고가 있었다.
# cmd /c 자식 프로세스 격리(09-08 적용)로도 못 잡았다 - 09-09~09-10 밤에는
# Start-Sleep(자식 프로세스 자체가 없는 순수 대기)마저 죽어서, 원인이 "자식 프로세스가
# 부모 콘솔을 공유하는 문제"가 아니라 이 로그온 세션(콘솔) 전체를 겨냥한 외부
# 신호/이벤트라는 게 명확해졌다. Application/System/Defender/세션 이벤트 로그를
# 전부 훑어봤지만 결정적 증거(재부팅/전원/크래시/보안위협/RDP 세션전환)는 없었다 -
# 정확한 발생원은 끝내 특정하지 못했다.
#
# 그래서 두 겹으로 방어한다: (1) 작업 자체를 SYSTEM 계정으로 돌려서 대화형
# 로그온 세션(콘솔)에서 완전히 분리한다(이 파이프라인은 저장된 쿠키로 항상
# headless 브라우저만 쓰므로 대화형 세션이 원래 필요 없다). (2) 그래도 혹시
# 모를 외부 종료에 대비해, 각 단계가 0이 아닌 exit로 끝나면 스크립트 자체가
# 한 번 더 재시도한다(Invoke-WithRetry) - Task Scheduler의 RestartCount 설정에만
# 맡기지 않고 스크립트 레벨에서 직접 보장한다.
#
# 2026-09-11: SYSTEM 계정 전환 첫 실행에서 STATUS_CONTROL_C_EXIT는 재발하지
# 않았다 (원인 해결로 보인다). 대신 새 문제가 드러났다 - Playwright 브라우저가
# 인터랙티브 사용자 프로필(C:\Users\user1\AppData\Local\ms-playwright)에 설치돼
# 있어서, SYSTEM 프로필에서는 그 경로에 브라우저가 없어 전부 실패했다.
# PLAYWRIGHT_BROWSERS_PATH를 프로젝트 폴더 하위로 고정해 계정에 상관없이
# 항상 같은 위치를 보도록 고쳤다(이 폴더는 SYSTEM에게 이미 전체 제어 권한이
# 상속돼 있음을 icacls로 확인함).

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
# SYSTEM 계정에는 별도 사용자 프로필이 있어 Playwright 브라우저를 찾지 못한다 -
# 프로젝트 폴더 하위 고정 경로로 지정해 어떤 계정으로 실행되든 동일한 설치를 쓰게 한다.
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $ProjectDir ".playwright-browsers"

function Write-Summary {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $SummaryLog -Value $line -Encoding utf8
}

function Invoke-Isolated {
    # Start-Process로 자식 프로세스를 별도 콘솔/신호 그룹에서 띄운다 - 부모(이 스크립트)나
    # 같은 로그온 세션의 다른 콘솔이 받는 Ctrl+C성 신호로부터 격리하기 위함.
    # stdout/stderr를 각각 다른 파일로만 리다이렉트할 수 있어 따로 받은 뒤 하나로 합친다.
    param([string]$Exe, [string[]]$ArgList, [string]$OutLog)
    $errLog = "$OutLog.err.tmp"
    $proc = Start-Process -FilePath $Exe -ArgumentList $ArgList `
        -RedirectStandardOutput $OutLog -RedirectStandardError $errLog -PassThru -Wait
    if (Test-Path $errLog) {
        Get-Content $errLog -Encoding utf8 | Add-Content -Path $OutLog -Encoding utf8
        Remove-Item $errLog -Force
    }
    return $proc.ExitCode
}

function Invoke-WithRetry {
    # 외부 신호로 죽는 사고에 대한 스크립트 레벨 방어망. 0이 아닌 exit로 끝나면
    # 60초 쉬고 한 번 더 시도한다. 이전 시도 로그는 지우지 않고 .attemptN 으로
    # 남겨서(디버깅용) 최종 로그($OutLog)는 항상 마지막 시도 내용만 담는다.
    param([string]$Exe, [string[]]$ArgList, [string]$OutLog, [string]$StepName, [int]$MaxAttempts = 2)
    $exitCode = 1
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        if ($attempt -gt 1) {
            Write-Summary ("{0}: {1}번째 시도가 exit={2}로 비정상 종료 - 60초 후 재시도" -f $StepName, ($attempt - 1), $exitCode)
            if (Test-Path $OutLog) { Move-Item -Path $OutLog -Destination ("{0}.attempt{1}" -f $OutLog, ($attempt - 1)) -Force }
            Start-Sleep -Seconds 60
        }
        $exitCode = Invoke-Isolated -Exe $Exe -ArgList $ArgList -OutLog $OutLog
        if ($exitCode -eq 0) { return $exitCode }
    }
    Write-Summary ("{0}: 최대 재시도({1}회) 후에도 실패 (마지막 exit={2})" -f $StepName, $MaxAttempts, $exitCode)
    return $exitCode
}

Write-Summary "=== 야간 파이프라인 시작 ==="

# --- Step 1: 해시태그/큐레이션 계정 자동 탐색 (00:00~10:00, 자체적으로 시간을 지켜 종료) ---
Write-Summary "Step 1 시작: auto_discover_scheduler --tonight"
$discoverExit = Invoke-WithRetry -Exe $Python -ArgList @("-m", "scraper.auto_discover_scheduler", "--tonight") -OutLog $DiscoverLog -StepName "Step 1"
Write-Summary ("Step 1 종료 (exit={0})" -f $discoverExit)

# --- Step 2: 15분 쿨다운 ---
Write-Summary "Step 2 시작: 15분 쿨다운 대기"
Start-Sleep -Seconds 900
Write-Summary "Step 2 종료: 쿨다운 완료"

# --- Step 3: 전체 파이프라인 (수집 -> 파싱 -> DB -> 카드뉴스 -> view.html) ---
Write-Summary "Step 3 시작: run_manual.py"
$pipelineExit = Invoke-WithRetry -Exe $Python -ArgList @("run_manual.py") -OutLog $PipelineLog -StepName "Step 3"
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
    Write-Summary "Step 4 시작: GitHub Pages 배포 (git add/commit/push)"
    # .gitignore가 .env/data//logs//output/ 등을 이미 제외하므로 add -A 로 안전하게 전부 스테이징한다.
    git add -A 2>&1 | Add-Content -Path $SummaryLog -Encoding utf8
    $commitMsg = "Auto update 캘린더 ({0})" -f (Get-Date -Format "yyyy-MM-dd HH:mm")
    git commit -m $commitMsg 2>&1 | Add-Content -Path $SummaryLog -Encoding utf8
    if ($LASTEXITCODE -eq 0) {
        git push origin main 2>&1 | Add-Content -Path $SummaryLog -Encoding utf8
        if ($LASTEXITCODE -eq 0) {
            Write-Summary "Step 4 완료: GitHub Pages 배포 성공"
        } else {
            Write-Summary "Step 4 실패: git push 오류 (네트워크/인증 문제일 수 있음 - 위 로그 확인)"
        }
    } else {
        Write-Summary "Step 4: 어제와 변경 사항 없음 - 커밋/배포 생략"
    }

    Write-Summary "모든 단계 정상 완료 - 절전 모드로 전환합니다."
    rundll32.exe powrprof.dll,SetSuspendState 0,1,0
} else {
    Write-Summary ("오류 감지 - 절전 모드로 전환하지 않고 유지합니다. " + `
        "(discoverExit=$discoverExit, pipelineExit=$pipelineExit, " + `
        "discoverTraceback=$discoverHasError, pipelineTraceback=$pipelineHasError)")
}
