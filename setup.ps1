# 나이스 자동화 — 한 번에 설치하기
# 사용법: PowerShell에서  .\setup.ps1
# (VS Code에서 이 파일을 열고 위쪽 터미널에 붙여넣어도 됩니다)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  나이스 자동화 설치를 시작합니다" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# 1) 파이썬 확인
Write-Host "[1/4] 파이썬 확인 중..."
$py = $null
foreach ($cmd in @("python", "py")) {
    try {
        $v = & $cmd --version 2>&1
        if ($v -match "Python 3\.(\d+)") {
            if ([int]$Matches[1] -ge 10) { $py = $cmd; break }
            Write-Host "  ! $v — 3.10 이상이 필요합니다." -ForegroundColor Yellow
        }
    } catch { }
}
if (-not $py) {
    Write-Host ""
    Write-Host "  X 파이썬이 없거나 너무 낮은 버전입니다." -ForegroundColor Red
    Write-Host "    https://www.python.org/downloads/ 에서 최신 버전을 받아 설치하세요."
    Write-Host "    설치 화면 맨 아래 [Add python.exe to PATH] 를 꼭 체크하세요!" -ForegroundColor Yellow
    Write-Host "    설치 후 VS Code를 껐다 켜고 이 스크립트를 다시 실행하세요."
    exit 1
}
Write-Host "  OK ($(& $py --version))" -ForegroundColor Green

# 2) 가상환경
Write-Host "[2/4] 가상환경(.venv) 만드는 중..."
if (-not (Test-Path ".venv")) { & $py -m venv .venv }
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Write-Host "  OK" -ForegroundColor Green

# 3) 패키지
Write-Host "[3/4] 필요한 패키지 설치 중... (1~3분 걸립니다)"
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r requirements.txt --quiet
Write-Host "  OK" -ForegroundColor Green

# 4) Playwright
Write-Host "[4/4] 브라우저 제어 도구 설치 중..."
& $venvPy -m playwright install chromium
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
