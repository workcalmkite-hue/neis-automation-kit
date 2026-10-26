# 나이스 자동화 — 한 번에 설치하기
# 사용법: PowerShell에서  .\setup.ps1
# (클로드 앱에서 «setup.ps1 실행해줘» 라고 하면 대신 돌려 줍니다)
# -Widget : 바로가기 위젯 설치(widget\install.ps1)가 부를 때 — 맨 끝 «다음 순서» 안내를 안 찍는다
param([switch]$Widget)

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
    Write-Host "  파이썬이 없어서 지금 깝니다 (winget · 보통 2~5분, 느린 컴퓨터는 15분 넘게도 걸립니다. 창을 닫지 마세요)..." -ForegroundColor Yellow
    $ErrorActionPreference = "Continue"
    # --source winget 필수 — 없으면 winget 이 «msstore» 목록까지 뒤지다가 그쪽이 실패하면(0x8a15003b)
    #   «--source 로 하나를 고르라»며 13초 만에 멈춘다 (2026-10-01 샌드박스 실측)
    # 두 번까지 — 학교 PC 백신이 막 받은 설치 파일을 검사하느라 잡고 있으면
    #   «0x80070020 : The process cannot access the file because it is being used by another process» 로 끝난다
    #   (2026-10-23 김수나 선생님 PC 실측: 28MB 다 받은 뒤 이 오류). 잠깐 기다렸다 다시 하면 검사가 끝나 있다
    foreach ($try in 1..2) {
        winget install -e --id Python.Python.3.13 --source winget --scope user --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        $py = Find-Python
        if ($py) { break }
        if ($try -eq 1) { Write-Host "  ! 백신이 설치 파일을 검사 중일 수 있어요. 30초 기다렸다 한 번 더 합니다..." -ForegroundColor Yellow; Start-Sleep 30 }
    }
    $ErrorActionPreference = "Stop"
}
# winget 이 없거나 고장인 PC — python.org 공식 설치 파일을 직접 받아 깐다
#   새로 만든 윈도우 계정은 winget 목록이 비어 «필요한 데이터가 없습니다» 로 두 번 다 실패했다 (2026-10-14 실측)
if (-not $py) {
    Write-Host "  winget 으로 못 깔아서 python.org 공식 설치 파일로 깝니다 (약 30MB · 1~3분)..." -ForegroundColor Yellow
    $ErrorActionPreference = "Continue"
    $inst = Join-Path $env:TEMP "python-3.13.16-amd64.exe"
    $url = "https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe"
    try {
        # 이 창(새 powershell)은 TLS 1.2 가 꺼져 있을 수 있다 — 꺼져 있으면
        #   «Received an unexpected EOF or 0 bytes from the transport stream» 으로 끊긴다 (2026-10-23 김수나 선생님 PC)
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = "SilentlyContinue"
        try { Invoke-WebRequest $url -OutFile $inst -UseBasicParsing }
        catch {
            # 그래도 안 되면 윈도우에 들어 있는 curl.exe (윈도우 자체 통신 모듈을 따로 쓴다)
            Write-Host "  ! 다른 방법(curl)으로 다시 받습니다..." -ForegroundColor Yellow
            Remove-Item $inst -ErrorAction SilentlyContinue
            & curl.exe -L --retry 3 -s -o $inst $url
        }
        if (-not (Test-Path $inst) -or (Get-Item $inst).Length -lt 20MB) { throw "내려받은 파일이 없거나 덜 받아졌습니다" }
        # 백신이 막 받은 파일을 검사하는 동안은 열 수 없다 — 놓아줄 때까지 최대 90초 기다린다
        foreach ($i in 1..30) {
            try { $fs = [IO.File]::Open($inst, 'Open', 'Read', 'None'); $fs.Close(); break } catch { Start-Sleep 3 }
        }
        $sig = Get-AuthenticodeSignature $inst
        if ($sig.Status -eq "Valid" -and $sig.SignerCertificate.Subject -match "Python Software Foundation") {
            Start-Process $inst -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0", "Include_launcher=0" -Wait
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
            $py = Find-Python
        } else {
            Write-Host "  ! 받은 파일의 서명이 Python Software Foundation 이 아니라서 설치하지 않았습니다." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  ! python.org 에서 받지 못했습니다: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    Remove-Item $inst -ErrorAction SilentlyContinue
    $ErrorActionPreference = "Stop"
}
if (-not $py) {
    Write-Host ""
    Write-Host "  X 파이썬을 자동으로 깔지 못했습니다." -ForegroundColor Red
    Write-Host "    https://www.python.org/downloads/windows/ 에서 «Windows installer (64-bit)» 를 받아 설치하세요."
    Write-Host "    (python.org 첫 화면의 큰 노란 버튼 «install manager» 가 아닙니다)"
    Write-Host "    설치 화면 맨 아래 [Add python.exe to PATH] 를 꼭 체크하세요!" -ForegroundColor Yellow
    Write-Host "    설치 후 이 설치를 다시 실행하세요. (클로드 앱으로 하고 있었다면 앱을 완전히 껐다 켠 뒤)"
    exit 1
}
Write-Host "  OK ($(& $py --version))" -ForegroundColor Green

# 2) 가상환경
Write-Host "[2/4] 가상환경(.venv) 만드는 중..."
function Stop-Failed($what) {
    Write-Host ""
    Write-Host "  X $what 에 실패했습니다. 위 빨간 글씨를 사진으로 찍어 두세요. (클로드를 쓰시면 클로드에게 그대로 보여 주세요)" -ForegroundColor Red
    Write-Host "    (학교망이 막고 있으면 개인 인터넷·테더링에서 설치만 다시 해 보세요)"
    exit 1
}
if (-not (Test-Path ".venv")) { & $py -m venv .venv; if ($LASTEXITCODE -ne 0) { Stop-Failed "가상환경 만들기" } }
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Stop-Failed "가상환경 만들기" }
if (-not (Test-PythonOk $venvPy)) {
    Write-Host "  X 예전에 만든 .venv 폴더가 너무 낮은 파이썬으로 만들어져 있습니다." -ForegroundColor Red
    Write-Host "    .venv 폴더를 지운 뒤 이 스크립트를 다시 실행하세요. (클로드를 쓰시면 «.venv 지우고 다시 설치해줘» 라고 하면 됩니다)"
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
# 경남 등 일부 학교망은 HTTPS 를 교육청 인증서(GNE_CERT)로 다시 감싼다. playwright 다운로더(Node)는
#   윈도우 인증서 목록을 안 봐서 SELF_SIGNED_CERT_IN_CHAIN 으로 실패한다 → 윈도우 목록도 같이 믿게 한다.
#   기본 목록에 «더하는» 것이라 다른 지역엔 영향 없다 (2026-10-26 실측: 121개 → 121+152개, 경남 학교망에서 설치 성공 제보)
$env:NODE_USE_SYSTEM_CA = "1"
# 약 300MB 라 받다가 시간 초과로 끊길 때가 있다 — 두 번까지 다시 받는다 (2026-10-14 실측: 두 번째에 통과)
foreach ($try in 1..3) {
    & $venvPy -m playwright install chromium
    if ($LASTEXITCODE -eq 0) { break }
    if ($try -lt 3) { Write-Host "  ! 받다가 끊겼습니다. 다시 받습니다 ($try/2)..." -ForegroundColor Yellow; Start-Sleep 5 }
}
if ($LASTEXITCODE -ne 0) { Stop-Failed "브라우저 제어 도구 설치" }
Write-Host "  OK" -ForegroundColor Green

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  설치 완료!" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""
if ($Widget) { exit 0 }
if (-not (Test-Path "oauth_client.json")) {
    Write-Host "다음 순서로 진행하세요:" -ForegroundColor Yellow
    Write-Host "  1) docs/02_구글-연동.md 를 보고 oauth_client.json 만들기"
    Write-Host "  2) .\.venv\Scripts\python.exe setup_wizard.py   (최초 설정)"
} else {
    Write-Host "다음 명령으로 최초 설정을 진행하세요:" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\python.exe setup_wizard.py"
}
Write-Host ""
