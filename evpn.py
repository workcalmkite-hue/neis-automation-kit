# -*- coding: utf-8 -*-
"""학교 밖에서 나이스·에듀파인을 쓰기 위한 EVPN(원격업무지원) 자동 연결.

지금은 **서울(evpn.sen.go.kr, AXGATE VPN Client)** 만 실제로 연결해 봤다. 다른 시도는 설정의
나이스 주소에서 EVPN 주소를 골라 쓴다(EVPN_BY_CODE, 설정에 evpn_url 을 적으면 그게 먼저).
경기는 포털 스크립트가 서울과 같음까지만 확인했고, 실제 연결은 아직 안 해 봤다.

  python evpn.py                 상태만 본다 (설치·도우미·연결)
  python evpn.py setup --user 아이디    EVPN 아이디 저장 (비밀번호가 인증서 암호와 다르면 새 창에서 받는다)
  python evpn.py install         도우미 설치 — 처음 한 번, «허용하시겠어요? [예]» 를 한 번 누른다
  python evpn.py connect         연결만
  python evpn.py disconnect      끊기만
  python evpn.py ping            (VPN 연결 중에) 내 지역 업무포털이 열리는지만 본다
  python run_vpn.py -- python main.py 2026-09-14    ← 평소엔 이걸 쓴다 (연결 → 작업 → 끊기 한 번에)

왜 도우미가 필요한가:
  EVPN 에 로그인하면 AXGATE 가 «2차 인증»(인증서 암호) 창을 띄운다. 이 창은 관리자 권한 프로그램의
  창이라 일반 권한인 이 스크립트는 글자를 넣을 수 없다(윈도우가 막는다). 그래서 관리자 권한 도우미가
  대신 넣는다. 매번 «[예]» 를 누르지 않도록 설치 때 한 번만 허락받아 예약 작업으로 등록한다
  (2026-09-18 실측: 기본 UAC PC 에서 예약 작업으로 부르면 확인창 없이 관리자 권한으로 돈다).

인증서 암호·EVPN 비밀번호는 keyring(윈도우 자격 증명 관리자)에서만 읽는다. 화면·로그에 찍지 않는다.
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import keyring

import teacher_config

KIT_DIR = Path(__file__).resolve().parent
SEOUL_EVPN_URL = "https://evpn.sen.go.kr/"
CLIENT_EXE = Path(r"C:\ProgramData\AXGATE\AXGATE VPN Client\Bin\AxgateVpnClient.exe")
AGENT_PORT = 46435                       # 포털이 에이전트를 찾는 로컬 포트
TAP_KEYWORDS = ("TAP-Win32", "AXGATE", "TAP-Windows")
TASK_NAME = "NeisAutomation EVPN 도우미"
HELPER_LOG = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "NeisAutomationEVPN" / "helper.log"
INSTALL_RESULT = HELPER_LOG.parent / "install_result.txt"
DONE_FLAG = teacher_config.CONFIG_DIR / "evpn_helper_done.flag"
PROFILE_DIR = teacher_config.CONFIG_DIR / "chrome_profile_evpn"
KEYRING_EVPN_ACCOUNT = "evpn"
REGISTER_WAIT_SEC = 70                   # 로그인 → 2차 인증 → 연결 Yes 까지 (실측 37~47초)

_NO_WINDOW = 0x08000000

CHROME_ARGS = [
    "--profile-directory=Default",
    "--disable-blink-features=AutomationControlled",
    # https 포털이 http://127.0.0.1:46435(에이전트)에 붙으려면 이 기능들을 꺼야 한다.
    # 안 끄면 «이 기기의 다른 앱 및 서비스에 액세스» 허용을 묻고 멈춘다 (2026-09-18 샌드박스에서 뜸)
    "--disable-features=PrivateNetworkAccessForNavigations,PrivateNetworkAccessPermissionPrompt,"
    "PermissionChip,LocalNetworkAccessChecks",
    "--disable-web-security",
    "--allow-running-insecure-content",
    "--test-type",
    "--start-maximized",
    "--noerrdialogs",
    "--hide-crash-restore-bubble",       # 지난번 강제 종료 뒤 «페이지를 복원하시겠습니까?» 안 띄우기
]

# 로그인 버튼을 덮는 안내 팝업·설치 확인 가림막
OVERLAY_IDS = ("layer_popup", "layer_popup2", "popupOverlay",
               "loading-fullbox", "loading-fullbox2", "loading-fullbox3")


# ============================================================ 설정·계정
def _cfg() -> dict:
    """설정 파일을 읽는다. 출결용 필수 항목(학년·반)이 없어도 EVPN 은 쓸 수 있어야 해서 직접 읽는다."""
    try:
        return json.loads(teacher_config.CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# 시도별 EVPN 주소 — 나이스 주소(https://{코드}.neis.go.kr)의 코드로 고른다.
# 교육청 공개 전국 EVPN 주소표 + 공식 포털 확인(2026-09-11). 경북만 도메인이 .gbe.kr 이다.
# 경기(goe)는 2026-09-21 포털의 axgate.js·axgate2.js 가 서울과 md5 까지 같고 로그인 칸 id 도 같음을 확인
EVPN_BY_CODE = {c: f"https://evpn.{c}.go.kr/" for c in (
    "sen", "pen", "dge", "ice", "gen", "dje", "use", "sje", "goe", "gwe",
    "cbe", "cne", "jbe", "jne", "gne", "jje")}
EVPN_BY_CODE["gbe"] = "https://evpn.gbe.kr/"


def portal_url() -> str:
    """이 선생님 지역의 업무포털 — 연결 시험(ping)에 쓴다."""
    m = re.match(r"https?://([a-z]{3})\.neis\.go\.kr", _cfg().get("neis_url") or "")
    return f"https://{m.group(1) if m else 'sen'}.eduptl.kr"


def ping() -> int:
    """VPN 이 붙은 상태에서 업무포털이 열리는지만 본다 (run_vpn.py -- python evpn.py ping)."""
    import urllib.request
    url = portal_url()
    try:
        code = urllib.request.urlopen(url, timeout=20).status
    except Exception as e:
        print(f"업무포털 응답 없음 ({url}) — {type(e).__name__}")
        return 1
    print(f"업무포털 응답 {code} ({url})")
    return 0 if code == 200 else 1


def evpn_url() -> str:
    cfg = _cfg()
    if cfg.get("evpn_url"):
        return cfg["evpn_url"]
    m = re.match(r"https?://([a-z]{3})\.neis\.go\.kr", cfg.get("neis_url") or "")
    return EVPN_BY_CODE.get(m.group(1), SEOUL_EVPN_URL) if m else SEOUL_EVPN_URL


def load_credentials() -> tuple[str | None, str | None]:
    """(아이디, 비밀번호). EVPN 비밀번호를 따로 저장하지 않았으면 인증서 암호를 쓴다."""
    cfg = _cfg()
    user = cfg.get("evpn_user") or None
    pw = keyring.get_password(teacher_config.KEYRING_SERVICE, KEYRING_EVPN_ACCOUNT)
    if not pw and cfg.get("cert_name"):
        pw = keyring.get_password(teacher_config.KEYRING_SERVICE, cfg["cert_name"])
    return user, pw or None


# ============================================================ PC 상태
def _ps(cmd: str, timeout: int = 20) -> tuple[int, str]:
    try:
        cmd = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + cmd
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=_NO_WINDOW)
        return r.returncode, (r.stdout or "").strip()
    except Exception as e:
        return 1, f"{type(e).__name__}"


def client_installed() -> bool:
    return CLIENT_EXE.exists()


def tap_status() -> str:
    """'Up' / 'Down' / 'unknown'. 읽기에 실패하면 unknown — 끊겼다고 넘겨짚지 않는다."""
    cond = " -or ".join(f"$_.InterfaceDescription -like '*{k}*'" for k in TAP_KEYWORDS)
    code, out = _ps(f"$a = Get-NetAdapter -ErrorAction Stop | Where-Object {{ {cond} }}; "
                    "if (-not $a) { 'NOADAPTER' } else { ($a | Select-Object -First 1).Status }")
    if code != 0 or not out:
        return "unknown"
    if out == "Up":
        return "Up"
    if out in ("Disconnected", "Down", "NOADAPTER", "Not Present", "Disabled"):
        return "Down"
    return "unknown"


def agent_listening() -> bool:
    code, out = _ps(f"(Get-NetTCPConnection -State Listen -LocalPort {AGENT_PORT} "
                    "-ErrorAction SilentlyContinue | Measure-Object).Count")
    return code == 0 and out.isdigit() and int(out) > 0


def helper_installed() -> bool:
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True,
                       creationflags=_NO_WINDOW)
    return r.returncode == 0


def helper_running() -> bool:
    code, out = _ps("(Get-Process evpn_helper -ErrorAction SilentlyContinue | Measure-Object).Count")
    return out.isdigit() and int(out) > 0


def helper_log_tail(n: int = 8) -> list[str]:
    try:
        return HELPER_LOG.read_text(encoding="utf-8-sig").splitlines()[-n:]
    except Exception:
        return []


def start_helper() -> bool:
    """예약 작업으로 도우미를 띄운다 (UAC 창 없음). 도우미가 AXGATE 도 필요하면 띄운다."""
    if helper_running():
        # 지난 실행의 도우미가 남아 있으면 끝 신호를 보내고 기다린다 — 다음 창을 대신 누르지 않게
        DONE_FLAG.parent.mkdir(parents=True, exist_ok=True)
        DONE_FLAG.write_text(str(time.time()))
        for _ in range(20):
            if not helper_running():
                break
            time.sleep(0.5)
    try:
        DONE_FLAG.unlink()
    except FileNotFoundError:
        pass
    r = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True,
                       creationflags=_NO_WINDOW)
    return r.returncode == 0


def stop_helper() -> None:
    DONE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    DONE_FLAG.write_text(str(time.time()))


# ============================================================ 설치 (처음 한 번)
def install() -> bool:
    if not client_installed():
        print("❌  AXGATE VPN Client 가 아직 없습니다.")
        print(f"    크롬으로 {evpn_url()} → 아이디·비밀번호로 [LOGIN] → [다운로드] → 받은 파일 실행 → [Finish]")
        print("    (포털 윗줄에 «설치 Yes» 가 보여도 처음이면 [다운로드]부터 하세요)")
        return False
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    src = KIT_DIR / "evpn_helper"
    print("🔐  도우미를 설치합니다. 화면에 «이 앱이 디바이스를 변경하도록 허용하시겠어요?» 가 뜨면 [예] 를 눌러 주세요.")
    print("    (처음 한 번만입니다. 그다음부터는 연결할 때 아무것도 안 누릅니다)", flush=True)
    try:
        INSTALL_RESULT.unlink()
    except (FileNotFoundError, PermissionError):
        pass
    ps = ("Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
          f"'-NoProfile','-ExecutionPolicy','Bypass','-File','\"{src / 'install.ps1'}\"',"
          f"'-User','\"{user}\"','-Source','\"{src}\"'")
    started = time.time()
    code, out = _ps(ps, timeout=600)
    fresh = INSTALL_RESULT.exists() and INSTALL_RESULT.stat().st_mtime >= started - 2
    if code != 0 or not fresh:
        print("❌  설치 창이 취소됐거나 실행되지 않았습니다. [예] 를 누르지 않으셨다면 다시 해 주세요.")
        return False
    try:
        status, msg = INSTALL_RESULT.read_text(encoding="utf-8-sig").strip().split("|", 1)
    except Exception:
        print("❌  설치 결과를 읽지 못했습니다.")
        return False
    print(("✅  " if status == "OK" else "❌  ") + msg)
    if status != "OK":
        return False
    if not helper_installed():
        print("❌  예약 작업이 보이지 않습니다.")
        return False
    make_desktop_disconnect()
    return True


def make_desktop_disconnect() -> None:
    """바탕화면 «VPN 끊기.bat» — 클로드도 인터넷도 없을 때 선생님이 더블클릭해서 끊는다."""
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    try:
        code, out = _ps("[Environment]::GetFolderPath('Desktop')")
        if code == 0 and out:
            desktop = Path(out)
    except Exception:
        pass
    py = KIT_DIR / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    bat = desktop / "VPN 끊기.bat"
    bat.write_text(
        "@echo off\r\nchcp 65001 >nul\r\necho EVPN 연결을 끊습니다...\r\n"
        f"\"{py}\" -X utf8 \"{KIT_DIR / 'evpn.py'}\" disconnect\r\npause\r\n",
        encoding="utf-8", newline="")
    print(f"🖱  바탕화면에 «{bat.name}» 를 만들었습니다 (클로드가 멈췄을 때 더블클릭하면 끊깁니다)")


# ============================================================ 포털 조작
def _kill_stale_chrome() -> None:
    """이 EVPN 전용 프로필로 떠 있는 크롬만 닫는다 — 남아 있으면 새 크롬이 «기존 세션에서 여는 중» 으로 바로 죽는다.
    선생님이 평소 쓰는 크롬은 건드리지 않는다 (명령줄에 이 프로필 폴더 이름이 있는 것만)."""
    _ps("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{PROFILE_DIR.name}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    time.sleep(1)


async def _launch(p):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _kill_stale_chrome()
    _allow_protocol()
    ctx = await p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR), channel="chrome", headless=False,
        args=CHROME_ARGS, ignore_default_args=["--enable-automation", "--no-sandbox"],
        viewport=None, ignore_https_errors=True)
    await _block_axvpn(ctx)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    page.on("dialog", lambda d: (print(f"💬  포털 알림: {d.message}"), asyncio.ensure_future(d.dismiss())))
    return ctx, page


def _allow_protocol() -> None:
    """예전 판이 프로필에 심어 둔 axvpn:// 자동 허용·차단 값을 지운다 (아래 _block_axvpn 으로 대신한다).

    크롬 «비밀번호를 저장하시겠습니까?» 풍선도 여기서 끈다 — 포털 로그인 뒤 화면을 가렸다
    (2026-09-18 샌드박스 Sonnet 실측). 첫 실행이라 파일이 없으면 이 두 값만 넣어 만든다.
    """
    prefs = PROFILE_DIR / "Default" / "Preferences"
    if prefs.exists():
        try:
            data = json.loads(prefs.read_text(encoding="utf-8"))
        except Exception:
            return
    else:
        prefs.parent.mkdir(parents=True, exist_ok=True)
        data = {}
    data["credentials_enable_service"] = False
    data.setdefault("profile", {})["password_manager_enabled"] = False
    ph = data.get("protocol_handler", {})
    ph.get("excluded_schemes", {}).pop("axvpn", None)
    for v in ph.get("allowed_origin_protocol_pairs", {}).values():
        v.pop("axvpn", None)
    prefs.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


async def _block_axvpn(ctx) -> None:
    """포털 스크립트가 axvpn:// 로 AXGATE 를 새로 띄우지 못하게 그 줄만 무력화한다.

    포털은 에이전트가 늦게 잡히면 `window.location = "axvpn://open…"` 으로 AXGATE 를 띄우려 한다
    (axgate2.js 226·244·376행). AXGATE 는 관리자 권한 프로그램이라 그때마다 «[예]» 창이 뜨거나
    크롬이 «AXGATE VPN Client 을(를) 여시겠습니까?» 를 묻는다 — 2026-09-18 기본 UAC 실측에서
    끊기 도중 [예] 창이 4번 떴다. AXGATE 는 도우미가 띄워 두고, 로그인·로그아웃은 포털이
    http://localhost:46435 로 직접 말하므로(agentCmd) axvpn:// 가 없어도 연결·끊기가 된다.
    이 자동화 크롬에만 적용된다 (선생님이 평소 쓰는 크롬은 그대로).
    """
    async def handler(route):
        resp = await route.fetch()
        body = (await resp.text()).replace('"axvpn://', '"javascript:void 0//')
        await route.fulfill(response=resp, body=body)
    await ctx.route("**/axgate*.js*", handler)


def _ensure_agent(wait: int = 30) -> bool:
    """AXGATE 에이전트가 떠 있게 한다 — 없으면 도우미(예약 작업)가 띄운다. UAC 창 없음."""
    if agent_listening():
        return True
    start_helper()
    for _ in range(wait):
        if agent_listening():
            return True
        time.sleep(1)
    return False


async def _text(page, el_id: str) -> str:
    try:
        return (await page.evaluate(
            f"() => ((document.getElementById('{el_id}')||{{}}).textContent||'').trim()")) or ""
    except Exception:
        return ""


async def _hide_overlays(page) -> None:
    try:
        await page.evaluate("""(ids) => ids.forEach(id => { const e = document.getElementById(id);
                               if (e) e.style.display = 'none'; })""", list(OVERLAY_IDS))
    except Exception:
        pass


async def _visible(page, sel: str) -> bool:
    try:
        loc = page.locator(sel)
        return bool(await loc.count()) and await loc.first.is_visible()
    except Exception:
        return False


async def _open_portal(page) -> None:
    await page.goto(evpn_url(), wait_until="domcontentloaded")
    # ⚠️ 세션이 있어도 처음엔 로그인 폼(#username)이 보이고, 몇 초 뒤 /custom/index.html 로 넘어가서야
    #    [LOGOUT] 이 생긴다. 로그인 폼만 보고 판단하면 «이미 로그아웃 상태» 로 오판한다 (2026-08-30·09-18 실측)
    form_since = None
    for i in range(50):
        if await _visible(page, "#logoutButton"):
            break
        if await _visible(page, "#username"):
            form_since = i if form_since is None else form_since
            if i - form_since >= 12:           # 로그인 폼이 6초 넘게 그대로면 정말 로그아웃 상태
                break
        await page.wait_for_timeout(500)
    await page.wait_for_timeout(1500)
    await _hide_overlays(page)


async def _logout(page) -> None:
    if await _visible(page, "#logoutButton"):
        await page.locator("#logoutButton").first.click()
        print("🚪  포털 로그아웃")
        for _ in range(40):
            if tap_status() == "Down" and await _visible(page, "#username"):
                break
            await page.wait_for_timeout(1000)


async def _login_once(page, user: str, pw: str) -> bool:
    """포털 로그인 1회 → 도우미가 2차 인증 → 연결 Yes 를 기다린다."""
    # 에이전트 인식(install_status=Yes)이 로그인보다 먼저여야 포털이 터널을 건다
    for _ in range(80):
        if await _text(page, "install_status") == "Yes":
            break
        await page.wait_for_timeout(500)
    if await _visible(page, "#logoutButton"):
        await _logout(page)            # 반쪽 세션이 남아 있으면 깨끗하게 끊고 다시
    loc_u, loc_p = page.locator("#username").first, page.locator("#password").first
    await loc_u.wait_for(state="visible", timeout=20_000)
    for loc, val in ((loc_u, user), (loc_p, pw)):
        await loc.click()
        await loc.fill("")
        await loc.press_sequentially(val, delay=50)   # 포털이 붙여넣기를 막고 키 입력을 본다
    await _hide_overlays(page)
    try:
        await page.click("#loginButton", timeout=8_000)
    except Exception:
        await page.locator("#loginButton").dispatch_event("click")
    print("🔐  EVPN 포털 로그인 — 2차 인증은 도우미가 넣습니다")
    for i in range(REGISTER_WAIT_SEC // 2):
        if await _text(page, "connect_status") == "Yes" and tap_status() == "Up":
            return True
        await page.wait_for_timeout(2000)
    return False


async def connect_async() -> bool:
    from playwright.async_api import async_playwright

    if not client_installed():
        return install()                          # 설치 안내를 찍고 False
    if not helper_installed():
        print("❌  EVPN 도우미가 아직 설치되지 않았습니다 → python evpn.py install (처음 한 번)")
        return False
    user, pw = load_credentials()
    if not (user and pw):
        print("❌  EVPN 아이디가 저장되지 않았습니다 → python evpn.py setup --user 아이디")
        return False
    if url_note := ("" if evpn_url() == SEOUL_EVPN_URL else " (서울 외 지역 — 아직 시험 안 한 주소)"):
        print(f"ℹ️  EVPN 주소: {evpn_url()}{url_note}")

    if not start_helper():
        print("❌  도우미 예약 작업을 실행하지 못했습니다 → python evpn.py install 을 다시 해 주세요")
        return False
    for _ in range(30):                           # 도우미가 AXGATE 를 띄우고 에이전트가 포트를 열 때까지
        if agent_listening():
            break
        time.sleep(1)
    else:
        print("⚠️  AXGATE 에이전트가 30초 안에 안 떴습니다. 도우미 기록:")
        for line in helper_log_tail():
            print("    " + line)

    async with async_playwright() as p:
        ctx, page = await _launch(p)
        try:
            await _open_portal(page)
            if await _text(page, "connect_status") == "Yes" and tap_status() == "Up":
                print("✅  EVPN 이미 연결되어 있습니다")
                return True
            for attempt in (1, 2):                 # 실패하면 한 번만 더 (09-18 실측 10번 중 1번 실패, 바로 다시 하면 붙음)
                if attempt == 2:
                    print("🔁  연결이 안 돼서 한 번 더 합니다")
                    start_helper()
                    await _open_portal(page)
                if await _login_once(page, user, pw):
                    print("✅  EVPN 연결됨")
                    return True
                print(f"   · {attempt}번째: 연결 안 됨 (포털 연결={await _text(page, 'connect_status') or '?'}, "
                      f"어댑터={tap_status()})")
            print("❌  EVPN 에 두 번 다 연결하지 못했습니다. 도우미 기록:")
            for line in helper_log_tail():
                print("    " + line)
            return False
        finally:
            stop_helper()
            await ctx.close()


async def disconnect_async() -> bool:
    """포털 로그아웃으로 끊는다 (AXGATE 프로세스를 죽여도 터널은 안 끊긴다 — 2026-08-29 실측)."""
    from playwright.async_api import async_playwright

    if tap_status() == "Down":
        print("✅  이미 끊겨 있습니다")
        return True
    if not _ensure_agent():
        print("⚠️  AXGATE 에이전트가 안 떠 있습니다 — 그래도 포털 로그아웃을 해 봅니다")
    async with async_playwright() as p:
        ctx, page = await _launch(p)
        try:
            await _open_portal(page)
            await _logout(page)
            for _ in range(20):
                st = tap_status()
                if st == "Down":
                    print("✅  EVPN 끊김")
                    return True
                await page.wait_for_timeout(1000)
            print(f"❌  아직 끊기지 않았습니다 (어댑터={tap_status()}) — 바탕화면 «VPN 끊기» 를 한 번 더 눌러 주세요")
            return False
        finally:
            stop_helper()
            await ctx.close()


def connect() -> bool:
    return asyncio.run(connect_async())


def disconnect() -> bool:
    return asyncio.run(disconnect_async())


# ============================================================ setup
def setup(user: str | None) -> int:
    cfg = _cfg()
    if not cfg:
        print("❌  설정 파일이 없습니다 — 먼저 python setup_wizard.py")
        return 1
    if user:
        cfg["evpn_user"] = user.strip()
        teacher_config.save_config(cfg)
        print("✅  EVPN 아이디 저장됨")
    print("ℹ️  EVPN 비밀번호가 인증서 암호와 같으면 따로 넣을 필요가 없습니다.")
    print("    다르면: python evpn.py password  (새 창이 뜨고, 그 창에만 비밀번호를 칩니다)")
    u, p = load_credentials()
    print(f"확인: 아이디 {'있음' if u else '없음'} · 비밀번호 {'있음' if p else '없음'}")
    return 0 if (u and p) else 1


def password_window() -> int:
    """EVPN 비밀번호를 새 창에서 받는다 — 클로드 대화에 남지 않게."""
    if os.environ.get("NEIS_EVPN_PW_WINDOW") != "1":
        env = dict(os.environ, NEIS_EVPN_PW_WINDOW="1")
        print("🪟  새 검은 창이 뜹니다. 거기에 EVPN 비밀번호를 치고 Enter 를 눌러 주세요.", flush=True)
        proc = subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "password"],
                                env=env, creationflags=subprocess.CREATE_NEW_CONSOLE,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait(timeout=900)
        ok = bool(keyring.get_password(teacher_config.KEYRING_SERVICE, KEYRING_EVPN_ACCOUNT))
        print("✅  EVPN 비밀번호 저장됨" if ok else "❌  저장되지 않았습니다")
        return 0 if ok else 1
    from getpass import getpass
    pw = getpass("EVPN 비밀번호 (화면에 안 보입니다): ")
    if pw:
        keyring.set_password(teacher_config.KEYRING_SERVICE, KEYRING_EVPN_ACCOUNT, pw)
        print("저장했습니다. 이 창은 3초 뒤 닫힙니다.")
        time.sleep(3)
    return 0


def status() -> int:
    u, p = load_credentials()
    print(f"EVPN 주소            {evpn_url()}")
    print(f"AXGATE 클라이언트     {'설치됨' if client_installed() else '❌ 없음'}")
    print(f"EVPN 도우미          {'설치됨' if helper_installed() else '❌ 없음 → python evpn.py install'}")
    print(f"EVPN 아이디·비밀번호  {'있음' if u else '❌ 아이디 없음'} / {'있음' if p else '❌ 없음'}")
    print(f"VPN 어댑터           {tap_status()}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EVPN 자동 연결")
    ap.add_argument("cmd", nargs="?", default="status",
                    choices=["status", "setup", "password", "install", "connect", "disconnect", "ping"])
    ap.add_argument("--user")
    a = ap.parse_args()
    if a.cmd == "status":
        sys.exit(status())
    if a.cmd == "setup":
        sys.exit(setup(a.user))
    if a.cmd == "password":
        sys.exit(password_window())
    if a.cmd == "install":
        sys.exit(0 if install() else 1)
    if a.cmd == "connect":
        sys.exit(0 if connect() else 1)
    if a.cmd == "disconnect":
        sys.exit(0 if disconnect() else 1)
    if a.cmd == "ping":
        sys.exit(ping())
