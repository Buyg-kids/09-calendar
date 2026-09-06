# Windows 작업 스케줄러가 매일 00:00에 이 스크립트를 새 프로세스로 실행한다.
# scraper/auto_discover_scheduler.py --tonight (지금부터 10:00까지만 탐색하고
# 스스로 정상 종료하는 단발 모드) 를 올바른 작업 디렉터리에서 실행하고,
# 실행일자별 로그 파일에 출력을 남긴다.

$ProjectDir = "C:\Users\user1\Desktop\클로드\인스타공구캘린더"
$Python = "C:\Users\user1\Desktop\클로드\.venv\Scripts\python.exe"

Set-Location $ProjectDir

$LogDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$LogFile = Join-Path $LogDir ("auto_discover_scheduled_{0}.log" -f (Get-Date -Format "yyyyMMdd"))

# PowerShell 5.1이 네이티브 프로세스의 stderr를 *>>/2>&1 로 직접 리다이렉트하면
# 파이썬 logging의 매 줄이 NativeCommandError로 감싸지고 로그가 깨진다.
# cmd.exe 리다이렉션으로 우회하고, PYTHONIOENCODING으로 UTF-8을 강제한다.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

cmd /c "`"$Python`" -m scraper.auto_discover_scheduler --tonight >> `"$LogFile`" 2>&1"
