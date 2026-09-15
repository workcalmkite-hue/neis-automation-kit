# 나이스 자동화 — 한 번에 설치하기
# 사용법: PowerShell에서  .\setup.ps1
# (클로드 앱에서 «setup.ps1 실행해줘» 라고 하면 대신 돌려 줍니다)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  나이스 자동화 설치를 시작합니다" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# 1) 파이썬 확인 — 없으면 winget 으로 알아서 깐다 (선생님이 python.org 에서 고를 일이 없게)
Write-Host "[1/4] 파이썬 확인 중..."

function Test-PythonOk($exe) {
    try {
        $v = & $exe --version 2>&1
        if ($v -match "Python 3\.(\d+)") {
            if ([int]$Matches[1] -ge 10) { return $true }
            Write-Host "  ! $v — 3.10 이상이 필요합니다." -ForegroundColor Yellow
        }
    } catch { }
    return $false
}

function Find-Python {
    foreach ($cmd in @("python", "py")) {
        if (Test-PythonOk $cmd) { return $cmd }
    }
    # 방금 깔아서 PATH 가 아직 안 잡힌 경우 — 사용자 설치 위치를 직접 찾는다 (새 버전부터, 3.10 이상만)
    $cands = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Directory.Name -replace '^Python3', '' -replace '\D.*$', '') } -Descending
    foreach ($c in $cands) {
        if (Test-PythonOk $c.FullName) { return $c.FullName }
    }
    return $null
}

$py = Find-Python
if (-not $py -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "  파이썬이 없어서 지금 깝니다 (winget · 2~5분, 창을 닫지 마세요)..." -ForegroundColor Yellow
    $ErrorActionPreference = "Continue"
    winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
    $ErrorActionPreference = "Stop"
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $py = Find-Python
}
if (-not $py) {
    Write-Host ""
    Write-Host "  X 파이썬을 자동으로 깔지 못했습니다." -ForegroundColor Red
    Write-Host "    https://www.python.org/downloads/windows/ 에서 «Windows installer (64-bit)» 를 받아 설치하세요."
    Write-Host "    (python.org 첫 화면의 큰 노란 버튼 «install manager» 가 아닙니다)"
    Write-Host "    설치 화면 맨 아래 [Add python.exe to PATH] 를 꼭 체크하세요!" -ForegroundColor Yellow
    Write-Host "    설치 후 클로드 앱을 완전히 껐다 켜고 이 스크립트를 다시 실행하세요."
    exit 1
}
Write-Host "  OK ($(& $py --version))" -ForegroundColor Green

# 2) 가상환경
Write-Host "[2/4] 가상환경(.venv) 만드는 중..."
function Stop-Failed($what) {
    Write-Host ""
    Write-Host "  X $what 에 실패했습니다. 위 빨간 글씨를 그대로 복사해 클로드에게 보여 주세요." -ForegroundColor Red
    Write-Host "    (학교망이 막고 있으면 개인 인터넷·테더링에서 설치만 다시 해 보세요)"
    exit 1
}
if (-not (Test-Path ".venv")) { & $py -m venv .venv; if ($LASTEXITCODE -ne 0) { Stop-Failed "가상환경 만들기" } }
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Stop-Failed "가상환경 만들기" }
if (-not (Test-PythonOk $venvPy)) {
    Write-Host "  X 예전에 만든 .venv 폴더가 너무 낮은 파이썬으로 만들어져 있습니다." -ForegroundColor Red
    Write-Host "    .venv 폴더를 지운 뒤 이 스크립트를 다시 실행하세요. (클로드에게 «.venv 지우고 다시 설치해줘» 라고 하면 됩니다)"
    exit 1
}
Write-Host "  OK" -ForegroundColor Green

# 3) 패키지
Write-Host "[3/4] 필요한 패키지 설치 중... (1~3분 걸립니다)"
& $venvPy -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "  ! pip 업그레이드는 건너뜁니다 (설치는 계속합니다)" -ForegroundColor Yellow }
& $venvPy -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { Stop-Failed "패키지 설치" }
Write-Host "  OK" -ForegroundColor Green

# 4) Playwright
Write-Host "[4/4] 브라우저 제어 도구 설치 중..."
& $venvPy -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Stop-Failed "브라우저 제어 도구 설치" }
Write-Host "  OK" -ForegroundColor Green

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  설치 완료!" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""
if (-not (Test-Path "oauth_client.json")) {
    Write-Host "다음 순서로 진행하세요:" -ForegroundColor Yellow
    Write-Host "  1) docs/02_구글-연동.md 를 보고 oauth_client.json 만들기"
    Write-Host "  2) .\.venv\Scripts\python.exe setup_wizard.py   (최초 설정)"
} else {
    Write-Host "다음 명령으로 최초 설정을 진행하세요:" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\python.exe setup_wizard.py"
}
Write-Host ""
