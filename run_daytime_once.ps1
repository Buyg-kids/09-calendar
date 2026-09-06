# 1회성 낮 시간대 보충 실행용 launcher.
# 어젯밤(9/4) 야간 스케줄러가 스크립트 버그로 실패해 수집이 하루 쉬었으므로,
# 오늘 밤(9/5) 00:00 정식 스케줄 전까지 5시간만 보충으로 돌린다.
# scraper/auto_discover_scheduler.py --duration 5h (활성 시간대 제한 무시,
# 속도제한/지연/키워드 필터 안전장치는 그대로 유지, 5시간 후 스스로 정상 종료)

$ProjectDir = "C:\Users\user1\Desktop\클로드\인스타공구캘린더"
$Python = "C:\Users\user1\Desktop\클로드\.venv\Scripts\python.exe"

Set-Location $ProjectDir

$LogDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$LogFile = Join-Path $LogDir ("auto_discover_daytime_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmm"))

# run_tonight.ps1에서 겪은 것과 같은 이유(PowerShell 5.1의 네이티브 stderr
# 리다이렉트가 로그를 깨뜨림)로 cmd.exe 리다이렉션을 쓰고, UTF-8을 강제한다.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

cmd /c "`"$Python`" -m scraper.auto_discover_scheduler --duration 5h >> `"$LogFile`" 2>&1"
