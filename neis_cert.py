# -*- coding: utf-8 -*-
"""나이스·업무포털 «인증서 로그인 프로그램»(KCaseAgent)이 이 PC에 있는지 보고, 없으면 깐다.

  python neis_cert.py              상태만 본다 (프로그램 · 인증서 파일)
  python neis_cert.py install      내 지역 나이스에서 설치 파일을 받아 실행한다 ([예] 한 번)
  학교 밖이면:  python run_vpn.py --minutes 5 -- python neis_cert.py install

왜 필요한가:
  나이스·업무포털의 [인증서 로그인] 은 이 PC 안에서 도는 KCaseAgent(127.0.0.1:39721)와 이야기해서
  인증서 창을 띄운다. 프로그램이 없으면 버튼을 눌러도 «아무 일도 일어나지 않는다» — 오류도 안 뜬다.
  집 PC·새 노트북에는 대개 없다. (2026-10-02 샌드박스 실측: 이걸 몰라서 연습 한 번에 39분이 걸렸다)

  설치 파일 주소는 나이스 로그인 화면의 [인증서프로그램다운로드] 버튼과 같은 곳이다
  (login.clx.js 의 ../ins/cm/fcm/KCaseAgent_Installer.exe → https://<지역>.neis.go.kr/ins/cm/fcm/…).
  받은 파일은 KSign 서명이 «유효» 할 때만 실행한다.
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import teacher_config

_NO_WINDOW = 0x08000000
AGENT_PORT = 39721
AGENT_EXES = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Ksign" / "KCase" / "KCaseAgent.exe",
              Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Ksign" / "KCase" / "KCaseAgent.exe"]
INSTALLER_PATH = "/ins/cm/fcm/KCaseAgent_Installer.exe"
# 인증서 파일이 보통 있는 곳. 교육행정(GPKI) 인증서는 C:\GPKI 가 기본이다
CERT_DIRS = [Path(r"C:\GPKI\Certificate\class2"), Path(r"C:\GPKI\Certificate\class1"),
             Path.home() / "AppData" / "LocalLow" / "GPKI",
             Path.home() / "AppData" / "LocalLow" / "NPKI"]


def _ps(cmd: str, timeout: int = 20) -> str:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + cmd],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=_NO_WINDOW)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def program_installed() -> bool:
    return any(p.exists() for p in AGENT_EXES)


def agent_listening() -> bool:
    out = _ps(f"(Get-NetTCPConnection -State Listen -LocalPort {AGENT_PORT} "
              "-ErrorAction SilentlyContinue | Measure-Object).Count")
    return out.isdigit() and int(out) > 0


def cert_files() -> list[str]:
    """인증서 파일이 있는 폴더 목록 (이름만 보여 주고 파일 내용은 읽지 않는다)."""
    found = []
    for d in CERT_DIRS:
        if d.is_dir() and (any(d.rglob("*.cer")) or any(d.rglob("signCert.der"))):
            found.append(str(d))
    return found


def installer_url() -> str:
    cfg = teacher_config.load_config() if teacher_config.CONFIG_FILE.exists() else {}
    neis = cfg.get("neis_url") or teacher_config.DEFAULTS["neis_url"]
    u = urlparse(neis)
    return f"{u.scheme}://{u.netloc}{INSTALLER_PATH}"


def hint() -> str:
    """로그인 스크립트가 «인증서 창이 안 뜸» 으로 실패했을 때 찍을 한 줄."""
    if not program_installed():
        return ("❗ 이 PC에 나이스 인증서 프로그램(KCaseAgent)이 없습니다 — 그래서 인증서 창이 안 뜹니다.\n"
                "   설치: python neis_cert.py install   (학교 밖이면 run_vpn.py --minutes 5 -- python neis_cert.py install)")
    if not agent_listening():
        return ("❗ 나이스 인증서 프로그램은 깔려 있는데 돌고 있지 않습니다.\n"
                "   컴퓨터를 다시 켜거나 python neis_cert.py install 로 다시 까세요.")
    return ""


def status() -> int:
    print(f"나이스 인증서 프로그램  {'설치됨' if program_installed() else '❌ 없음 → python neis_cert.py install'}"
          f"{' (실행 중)' if agent_listening() else ''}")
    dirs = cert_files()
    print(f"인증서 파일 위치        {', '.join(dirs) if dirs else '❌ 못 찾음 (C:\\GPKI · LocalLow\\GPKI·NPKI 를 봤다)'}")
    return 0 if program_installed() and dirs else 1


def _signature_ok(path: Path) -> bool:
    out = _ps(f"$s=Get-AuthenticodeSignature '{path}'; \"$($s.Status)|$($s.SignerCertificate.Subject)\"")
    st, _, subj = out.partition("|")
    return st == "Valid" and "KSign" in subj


def install() -> int:
    if program_installed():
        print("✅  이미 설치돼 있습니다.")
        return 0
    url = installer_url()
    dest = Path.home() / "Downloads" / "KCaseAgent_Installer.exe"
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"⬇️   설치 파일 받는 중 ({url})", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            f.write(r.read())
    except Exception as e:
        print(f"❌  받지 못했습니다 ({type(e).__name__}). 학교 밖이면 EVPN 안에서 다시 하세요:")
        print("    python run_vpn.py --minutes 5 -- python neis_cert.py install")
        print("    그래도 안 되면 나이스 로그인 화면의 [인증서프로그램다운로드] 를 눌러 받아 실행하세요.")
        return 1
    if not _signature_ok(dest):
        print("❌  받은 파일의 서명이 KSign 정품이 아니라서 실행하지 않았습니다.")
        return 1
    print("🔐  설치합니다 (창 없이 10~30초). «이 앱이 디바이스를 변경하도록 허용하시겠어요?» 가 나오면 [예] 를 눌러 주세요.", flush=True)
    # NSIS 설치 파일이라 /S 로 창 없이 깔린다 (2026-10-03 샌드박스 실측 12초, 종료코드 0).
    # Program Files 에 까는 거라 관리자 권한이 필요하다 — 그냥 실행하면 «권한 상승 필요» 로 실패하므로
    # RunAs 로 불러 UAC [예] 를 받는다.
    _ps(f"Start-Process -FilePath '{dest}' -ArgumentList '/S' -Verb RunAs -Wait", timeout=300)
    for _ in range(15):          # 설치 직후 프로그램이 뜰 때까지 잠깐
        if program_installed() and agent_listening():
            break
        time.sleep(2)
    if not program_installed():
        print("❌  설치가 끝나지 않았습니다. [예] 를 누르지 않으셨다면 다시 해 주세요.")
        return 1
    print("✅  나이스 인증서 프로그램을 깔았습니다.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    sys.exit(install() if cmd == "install" else status())
