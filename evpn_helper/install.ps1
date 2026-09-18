# EVPN 도우미 설치 — 관리자 권한으로 한 번만 돈다 (python evpn.py install 이 UAC 한 번으로 띄운다).
#
# 하는 일
#   1) AXGATE VPN Client 서명 확인 (AXGATE Co., Ltd. · 유효)
#   2) 도우미를 C:\Program Files\NeisAutomationEVPN 에 컴파일 (일반 사용자가 못 고치는 폴더)
#   3) AXGATE Bin 폴더의 exe·dll 목록과 해시를 기준으로 기록 — 도우미는 이것과 같을 때만 AXGATE 를 띄운다
#   4) 로그 폴더 C:\ProgramData\NeisAutomationEVPN (관리자만 쓰기, 사용자는 읽기)
#   5) «가장 높은 권한으로 실행» 예약 작업 등록 — 이 선생님 계정 · 로그온 중일 때만 · 실행 파일 고정
# 결과는 C:\ProgramData\NeisAutomationEVPN\install_result.txt 에 남긴다 (키트가 읽는다).
param(
    [Parameter(Mandatory = $true)][string]$User,
    [Parameter(Mandatory = $true)][string]$Source
)
$ErrorActionPreference = 'Stop'
$TaskName = 'NeisAutomation EVPN 도우미'
$Dst = Join-Path $env:ProgramFiles 'NeisAutomationEVPN'
$LogDir = Join-Path $env:ProgramData 'NeisAutomationEVPN'
$Bin = 'C:\ProgramData\AXGATE\AXGATE VPN Client\Bin'
$Result = Join-Path $LogDir 'install_result.txt'

New-Item -ItemType Directory -Force $LogDir | Out-Null
# 로그 폴더: 상속 끊고 SYSTEM·Administrators 만 쓰기, Users 는 읽기
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
foreach ($r in @(
        @('NT AUTHORITY\SYSTEM', 'FullControl'),
        @('BUILTIN\Administrators', 'FullControl'),
        @('BUILTIN\Users', 'ReadAndExecute'))) {
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
                $r[0], $r[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
}
Set-Acl $LogDir $acl

function Done($ok, $msg) {
    Set-Content $Result "$ok|$msg" -Encoding UTF8
    if ($ok -ne 'OK') { exit 1 } else { exit 0 }
}

try {
    $exe = Join-Path $Bin 'AxgateVpnClient.exe'
    if (-not (Test-Path $exe)) { Done 'FAIL' 'AXGATE VPN Client 가 설치되어 있지 않습니다' }
    $sig = Get-AuthenticodeSignature $exe
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'AXGATE Co\., Ltd\.') {
        Done 'FAIL' "AXGATE 서명 확인 실패 ($($sig.Status))"
    }

    # 1) 도우미 컴파일 — 원본을 보호 폴더로 먼저 복사한 뒤 거기서 컴파일한다
    New-Item -ItemType Directory -Force $Dst | Out-Null
    Copy-Item (Join-Path $Source 'helper.cs') (Join-Path $Dst 'helper.cs') -Force
    $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
    $out = & $csc -nologo -codepage:65001 -target:winexe -r:System.Web.Extensions.dll `
        "-out:$(Join-Path $Dst 'evpn_helper.exe')" (Join-Path $Dst 'helper.cs') 2>&1
    if ($LASTEXITCODE -ne 0) { Done 'FAIL' "도우미 컴파일 실패: $out" }

    # 2) AXGATE 파일 기준 — 일반 사용자가 고칠 수 있는 파일이 있으면 멈춘다
    $lines = @()
    $bad = @()
    Get-ChildItem $Bin -Recurse -File | Where-Object { $_.Extension -in '.exe', '.dll' } | ForEach-Object {
        $rel = $_.FullName.Substring($Bin.Length + 1)
        $lines += "$rel|$((Get-FileHash $_.FullName -Algorithm SHA256).Hash)"
        $w = (Get-Acl $_.FullName).Access | Where-Object {
            $_.IdentityReference -match 'Users|Everyone|Authenticated Users' -and
            $_.AccessControlType -eq 'Allow' -and
            ($_.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]'WriteData,AppendData,Delete,ChangePermissions,TakeOwnership')
        }
        if ($w) { $bad += $rel }
    }
    if ($bad) { Done 'FAIL' "일반 사용자가 고칠 수 있는 AXGATE 파일이 있어 멈춥니다: $($bad -join ', ')" }
    Set-Content (Join-Path $Dst 'axgate_baseline.txt') $lines -Encoding UTF8

    # 3) 예약 작업
    $a = New-ScheduledTaskAction -Execute (Join-Path $Dst 'evpn_helper.exe')
    $p = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
    $s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
    Register-ScheduledTask -TaskName $TaskName -Action $a -Principal $p -Settings $s -Force | Out-Null

    Done 'OK' "설치 완료 · AXGATE 파일 $($lines.Count)개 기준 기록"
}
catch {
    Done 'FAIL' $_.Exception.Message
}
