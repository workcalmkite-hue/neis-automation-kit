# -*- coding: utf-8 -*-
"""나이스에 인증서로 로그인해 두고, 그 크롬을 원격 디버깅 포트로 열어 둔다.

출장·41조 연수처럼 전용 스크립트가 없는 화면을 Claude Code 가 조작할 때 쓴다.
Claude Code 의 브라우저 도구(Playwright MCP)로 나이스를 열면 로그인 창이
«보안프로그램이 로딩중»에서 멈춘다 — 크롬이 https 페이지에서 127.0.0.1(보안모듈)
접근을 막기 때문이다 (2026-09-11 실측). 그걸 푸는 설정이 CHROME_ARGS 에 있다.

  python neis_session.py --check   저장된 설정·암호만 점검한다 (크롬을 띄우지 않는다)
  python neis_session.py           로그인 → `READY port=9222` 출력 → 창을 열어 둔다
  python neis_cdp.py 스니펫.py      열어 둔 창에 붙어 조작한다

인증서 암호는 이 스크립트가 keyring 에서 직접 읽어 암호칸에만 넣는다. 화면·오류 메시지에 찍지 않는다.
- 인증서를 고르다 막히면(이름 불일치·여러 개·만료) 암호를 넣기 «전에» 멈춘다
- 암호를 넣은 뒤 막히면(오류 안내·로딩 지연) 다시 시도하지 않는다
어느 쪽이든 고치는 법을 찍고, 창을 닫지 않은 채 사람이 직접 로그인하기를
MANUAL_WAIT_MIN 분 동안 기다렸다가 이어서 진행한다.
"""
import argparse
import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import keyring
from playwright.async_api import async_playwright

import teacher_config

PORT = int(os.environ.get("NEIS_CDP_PORT", "9222"))
KEEP_OPEN_MIN = 50
MANUAL_WAIT_MIN = 5
# main.py · main_bokmu.py · verify_attendance.py 와 같은 프로필이다 (로그인 상태를 같이 쓴다)
PROFILE_DIR = teacher_config.CONFIG_DIR / "chrome_profile"
GPKI_DIR = Path(r"C:\GPKI\Certificate\class2")   # 교육행정 인증서가 보통 들어 있는 곳
FIX = r"python fix_cert.py  (가상환경이면 .\.venv\Scripts\python.exe fix_cert.py)"

CHROME_ARGS = [
    "--profile-directory=Default",
    "--disable-blink-features=AutomationControlled",
    # 이 셋이 없으면 https 페이지가 127.0.0.1 보안모듈에 못 붙어 «보안프로그램 로딩중»에서 멈춘다
    "--disable-features=PrivateNetworkAccessForNavigations,PrivateNetworkAccessPermissionPrompt,PermissionChip",
    "--disable-web-security",
    "--allow-running-insecure-content",
    "--test-type",
    "--start-maximized",
    "--noerrdialogs",
    f"--remote-debugging-port={PORT}",
]

# 로그인 여부 판정. 2026-09-11 실측: 로그인 전 첫 화면 0개 / 로그인 뒤 1개(보임).
# 「학급담임」 메뉴는 담임이 아니면 없을 수 있어서 쓰지 않는다.
LOGGED_IN = "text=로그아웃"
# 암호를 넣은 뒤 로그인 처리 시간이 들쭉날쭉하다 (2026-09-11 실측: 1초 · 10~30초 · 60초 넘게 «로딩 중입니다»).
# 로딩 표시가 보이는 동안은 실패로 치지 않고 LOGIN_MAX_S 까지, 로딩 표시도 없으면 NO_LOADING_GIVEUP_S 뒤에 멈춘다.
LOADING = "text=로딩 중입니다 >> visible=true"
LOGIN_MAX_S = 120
NO_LOADING_GIVEUP_S = 40
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 인증서 창의 글자를 « | » 로 이어 붙여 가져온다 (암호칸에서 위로 올라가며 목록이 든 상자를 찾는다)
JS_CERT_DIALOG = """() => {
    let el = document.querySelector('input[aria-label="인증서 암호 입력필드"], input[title="인증서 암호 입력필드"]');
    for (let i = 0; el && i < 15; i++, el = el.parentElement) {
        const t = el.innerText || '';
        if (t.includes('발급자') && t.includes('인증서 보기')) return t.replace(/\\s*\\n\\s*/g, ' | ');
    }
    return '';
}"""


class LoginBlocked(Exception):
    """사람이 고쳐야 넘어갈 수 있는 곳. 메시지에 고치는 법을 담는다."""


_SECRETS = []   # 화면에 찍기 전에 가릴 값 (인증서 암호). login() 이 채운다


def scrub(text):
    """글자에서 암호를 **** 로 가린다. 자르기 «전에» 불러야 한다 (잘린 암호 조각이 남지 않게)."""
    for s in _SECRETS:
        if s:
            text = text.replace(s, "****")
    return text


# ---------- 크롬을 띄우기 전 점검 ----------

def gpki_users(folder=GPKI_DIR):
    """교육행정 인증서 파일명(`851홍길동005_sig.cer`)에서 사용자 이름을 뽑는다. 폴더가 없으면 None."""
    if not folder.is_dir():
        return None
    return sorted({p.name[: -len("_sig.cer")] for p in folder.glob("*_sig.cer")})


def gpki_notes(name, users):
    """설정의 인증서 이름이 이 PC 인증서 파일과 맞는지 알려 주는 줄들. 여기서는 멈추지 않는다."""
    manual = "그때는 열린 창에서 직접 로그인하시면 이어서 진행합니다."
    if users is None:
        return [f"ℹ️  {GPKI_DIR} 폴더가 없습니다. 인증서가 USB 등 다른 곳에 있으면 자동 선택이 안 될 수 있습니다 — {manual}"]
    hits = [u for u in users if name in u]
    if len(hits) == 1:
        return [f"✅  이 PC의 교육행정 인증서 '{hits[0]}' 와 이름이 맞습니다."]
    if not hits:
        return [f"⚠️  설정 이름 '{name}' 이 이 PC 인증서 파일({', '.join(users) or '없음'})과 맞지 않습니다.",
                f"   파일 이름 그대로 다시 저장하세요 → {FIX}  (USB 인증서면 {manual})"]
    return [f"⚠️  '{name}' 이 들어간 인증서가 {len(hits)}개입니다({', '.join(hits)}). 로그인 창에서 하나로 못 고르면 멈춥니다.",
            f"   쓰실 인증서 이름을 통째로 저장해 두세요 → {FIX}"]


def preflight():
    """설정·인증서 이름·저장된 암호를 본다. (설정, 암호)를 돌려준다. 암호는 찍지 않는다."""
    try:
        cfg = teacher_config.load_config()
    except teacher_config.ConfigMissingError as e:
        raise LoginBlocked(str(e)) from None
    name = (cfg.get("cert_name") or "").strip()
    if not name:
        raise LoginBlocked(f"설정에 인증서 이름(cert_name)이 비어 있습니다.\n   → {FIX}")
    try:
        pw = keyring.get_password(teacher_config.KEYRING_SERVICE, name)
    except Exception as e:
        raise LoginBlocked(f"Windows 자격 증명 관리자를 읽지 못했습니다: {type(e).__name__}\n   → {FIX}") from None
    if not pw:
        raise LoginBlocked(
            f"'{name}' 인증서 암호가 이 PC(Windows 자격 증명 관리자)에 저장돼 있지 않습니다.\n"
            f"   → {FIX}\n"
            "   암호를 칠 때 화면에 아무것도 안 보이는 게 정상입니다.")
    print(f"✅  설정 파일 · 인증서 이름 '{name}' · 저장된 암호 확인", flush=True)
    print(f"✅  나이스 주소 {cfg['neis_url']}", flush=True)
    for line in gpki_notes(name, gpki_users()):
        print(line, flush=True)
    return {**cfg, "cert_name": name}, pw


def split_cmdline(cmd):
    """윈도우 명령줄을 윈도우와 같은 규칙으로 쪼갠다 (따옴표·공백 든 경로를 정확히 비교하려고)."""
    if os.name != "nt" or not cmd.strip():
        return []
    import ctypes
    from ctypes import wintypes
    fn = ctypes.windll.shell32.CommandLineToArgvW
    fn.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    fn.restype = ctypes.POINTER(wintypes.LPWSTR)
    argc = ctypes.c_int(0)
    argv = fn(cmd, ctypes.byref(argc))
    if not argv:
        return []
    try:
        return [argv[i] for i in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


def list_chrome():
    """떠 있는 chrome.exe 의 (pid, 명령줄) 목록. 못 읽으면 빈 목록."""
    if os.name != "nt":
        return []
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "@(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Select-Object ProcessId,CommandLine)"
          " | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30).stdout
        data = json.loads(out.decode("utf-8-sig", "replace").strip() or "[]")
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    return [(d["ProcessId"], d.get("CommandLine") or "") for d in data]


def classify_profile_chrome(procs, profile):
    """이 프로필로 떠 있는 크롬 «본체»를 (전에 neis_session 이 연 것, 다른 자동화가 연 것) 으로 나눈다.

    - 렌더러 같은 자식 프로세스(--type=)는 본체가 닫히면 같이 닫히므로 세지 않는다
    - --user-data-dir 가 이 프로필과 «정확히» 같은 것만 본다 (chrome_profile_edufine·공백 붙은 다른 경로 제외)
    - --remote-debugging-port 가 있으면 neis_session.py 가 띄운 것이다. main.py·verify_attendance.py 는 그 옵션을 안 쓴다
    """
    want = os.path.normcase(os.path.normpath(str(profile)))
    mine, others = [], []
    for pid, cmd in procs:
        args = split_cmdline(cmd)
        if any(a.startswith("--type=") for a in args):
            continue
        dirs = [a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir=")]
        if not dirs or os.path.normcase(os.path.normpath(dirs[0])) != want:
            continue
        (mine if any(a.startswith("--remote-debugging-port=") for a in args) else others).append(pid)
    return mine, others


def free_profile(profile=PROFILE_DIR):
    """크롬을 띄우기 전에 같은 프로필을 쓰는 크롬을 정리한다.

    같은 프로필로 두 번 띄우면 새 크롬이 «기존 브라우저 세션에서 여는 중»을 찍고 바로 죽는다 (2026-09-11 실측).
    전에 이 스크립트로 열어 둔 창만 닫는다. 출결 입력 같은 다른 자동화가 쓰는 중이면 닫지 않고 멈춘다.
    """
    mine, others = classify_profile_chrome(list_chrome(), profile)
    if others:
        raise LoginBlocked(
            "다른 나이스 자동화(출결 입력·출결 검증·복무 신청)가 같은 크롬을 쓰는 중입니다.\n"
            "   진행 중인 작업을 끊지 않으려고 닫지 않았습니다. 그 작업이 끝난 뒤 다시 실행하세요.\n"
            "   작업 없이 창만 남아 있는 거라면 그 크롬 창을 직접 닫고 다시 실행하세요.")
    for pid in mine:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if mine:
        print(f"🧹  전에 열어 둔 나이스 자동화 크롬 {len(mine)}개를 닫았습니다.", flush=True)
        time.sleep(2)


def port_busy(port=PORT):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def port_answers(port=PORT):
    """띄운 크롬의 원격 조작 포트가 응답하는지 (프록시 설정을 타지 않게 직접 연결)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


# ---------- 로그인 ----------

def parse_cert_rows(dialog_text):
    """인증서 창 글자에서 (사용자, 만료일, 발급자) 행을 뽑는다.

    실측 글자(2026-09-11): … | 구분 | 사용자 | 만료일 | 발급자 | 일반인증서 | 851홍길동005 | 2028-07-24 | 교육부 | 인증서 보기 | …
    열이 늘거나 줄어도 되게 «날짜 칸»을 기준으로 바로 앞 칸을 사용자, 바로 뒤 칸을 발급자로 본다.
    """
    cells = [c.strip() for c in dialog_text.split("|")]
    return [{"user": cells[i - 1], "expires": c, "issuer": cells[i + 1] if i + 1 < len(cells) else ""}
            for i, c in enumerate(cells) if i and DATE_RE.match(c)]


def check_cert_choice(name, rows, title_hits, today=None):
    """암호를 넣기 전에 «고를 인증서가 정확히 하나이고 만료 전인지» 본다.

    통과하면 목록에서 찾은 행(dict)을 돌려준다. 하나라도 확인이 안 되면 LoginBlocked (확인 못 한 채로는 넘기지 않는다).
    """
    listing = "\n".join(f"      · {r['user']}  (만료 {r['expires']}, {r['issuer']})" for r in rows) \
        or "      (목록이 비어 있습니다)"
    if title_hits == 0:
        raise LoginBlocked(
            f"인증서 목록에 '{name}' 이 들어간 인증서가 없습니다. 암호는 넣지 않았습니다.\n"
            f"   지금 창에 보이는 인증서:\n{listing}\n"
            f"   ① 목록에 본인 것이 있으면 «사용자» 칸 글자 그대로 다시 저장 → {FIX}\n"
            "   ② 목록이 비어 있으면 이 PC(하드디스크)에 인증서가 없는 것입니다.\n"
            "      평소 업무포털이 되는 PC에서 하시거나, USB 인증서면 꽂고 창에서 직접 로그인하세요.")
    hits = [r for r in rows if name in r["user"]]
    if rows and not hits:
        raise LoginBlocked(
            f"화면에 '{name}' 이 들어간 칸은 있지만 인증서 목록의 사용자와 맞지 않습니다. 암호는 넣지 않았습니다.\n"
            f"   지금 창에 보이는 인증서:\n{listing}\n"
            f"   «사용자» 칸 글자 그대로 다시 저장하세요 → {FIX}")
    if title_hits > 1 or len(hits) > 1:
        raise LoginBlocked(
            f"'{name}' 이 들어간 인증서가 여러 개라 하나를 고를 수 없습니다. 암호는 넣지 않았습니다.\n{listing}\n"
            f"   쓰실 인증서의 «사용자» 칸 글자를 통째로 저장하세요 → {FIX}")
    if not hits:
        # 목록 글자를 못 읽으면 어느 인증서인지·만료됐는지 확인할 수 없다 → 암호를 넣지 않고 사람에게 넘긴다
        raise LoginBlocked(
            "인증서 목록 글자를 읽지 못해 어느 인증서인지 확인할 수 없습니다. 암호는 넣지 않았습니다.\n"
            "   나이스 인증서 창 모양이 바뀐 것일 수 있습니다. 창에서 직접 로그인하세요.")
    hit = hits[0]
    try:
        expired = date.fromisoformat(hit["expires"]) < (today or date.today())
    except ValueError:
        raise LoginBlocked(f"인증서 '{hit['user']}' 의 만료일({hit['expires']})을 읽지 못했습니다. 암호는 넣지 않았습니다.\n"
                           "   창에서 직접 로그인하세요.") from None
    if expired:
        raise LoginBlocked(f"인증서 '{hit['user']}' 가 {hit['expires']} 에 만료됐습니다. 암호는 넣지 않았습니다.\n"
                           "   인증서를 갱신한 뒤 다시 하세요.")
    return hit


async def is_logged_in(page):
    loc = page.locator(LOGGED_IN)
    return await loc.count() > 0 and await loc.first.is_visible()


async def auto_cert_login(page, name, pw, alerts):
    """인증서 로그인 버튼 → 인증서 고르기 → 암호. 막히면 LoginBlocked (메시지에 암호는 절대 없다)."""
    if pw not in _SECRETS:
        _SECRETS.append(pw)
    btn = page.get_by_role("button", name="인증서 로그인")
    for _ in range(60):
        if await is_logged_in(page):
            print("✅  이미 로그인 상태", flush=True)
            return
        if await btn.count() and await btn.first.is_visible():
            break
        await page.wait_for_timeout(500)
    else:
        raise LoginBlocked(
            "나이스 첫 화면에서 「인증서 로그인」 버튼이 30초 동안 안 보였습니다.\n"
            f"   지금 주소: {page.url}\n"
            "   ① 설정의 나이스 주소(neis_url)가 본인 지역 주소인지\n"
            "   ② 학교망인지 — 학교 밖이면 교육청 VPN 없이는 나이스가 안 열릴 수 있습니다\n"
            "   ③ 평소 크롬으로 나이스에 들어갔을 때 보안프로그램 설치 안내가 뜨는지 확인하세요")
    await btn.first.click()

    pwd = page.get_by_role("textbox", name="인증서 암호 입력필드")
    try:
        await pwd.wait_for(timeout=20_000)
    except Exception:
        raise LoginBlocked(
            "「인증서 로그인」을 눌렀는데 인증서 창이 20초 안에 안 떴습니다.\n"
            "   화면에 «보안프로그램 설치»나 «로딩중»이 보이면, 평소 크롬으로 나이스에 한 번 로그인해\n"
            "   보안프로그램을 설치한 뒤 다시 하세요.") from None

    # 인증서 목록은 창이 뜬 뒤에 채워진다 → 이름이 든 칸이 보일 때까지 기다린다
    partial = page.locator(f"div[title*={json.dumps(name, ensure_ascii=False)}]:visible")
    for _ in range(20):
        if await partial.count():
            break
        await page.wait_for_timeout(500)
    rows = parse_cert_rows(await page.evaluate(JS_CERT_DIALOG))
    hit = check_cert_choice(name, rows, await partial.count())
    # 목록에서 찾은 사용자 이름과 «똑같은» 칸만 누른다
    target = page.locator(f"div[title={json.dumps(hit['user'], ensure_ascii=False)}]:visible")
    if not await target.count():
        raise LoginBlocked(f"인증서 '{hit['user']}' 칸을 화면에서 찾지 못했습니다. 암호는 넣지 않았습니다.")

    try:
        # ★ 행을 먼저 클릭해야 한다 — 바로 암호를 넣으면 «인증서 선택 후 암호를 입력하세요» 상태로 남는다
        await target.first.click()
        print(f"🔐  인증서 선택: {hit['user']}", flush=True)
        await page.wait_for_timeout(400)
        await pwd.fill(pw)
        await pwd.press("Enter")
    except Exception:
        # ⚠️ playwright 의 fill 오류 메시지에는 넣으려던 값(=암호)이 그대로 들어간다 (2026-09-11 실측). 원문을 찍지 않는다
        raise LoginBlocked("인증서를 고르거나 암호를 넣는 중에 화면이 바뀌어 멈췄습니다.\n"
                           "   (오류 원문에는 암호가 섞일 수 있어 찍지 않습니다)") from None
    print("🔐  암호 입력 (화면에 찍지 않음) — 로그인 기다리는 중", flush=True)

    started = time.monotonic()
    first_alert = None
    told = False
    while True:
        await page.wait_for_timeout(500)
        if await is_logged_in(page):
            print("✅  로그인 완료", flush=True)
            return
        elapsed = time.monotonic() - started
        if alerts and first_alert is None:
            first_alert = time.monotonic()
        if first_alert and time.monotonic() - first_alert > 6:
            break
        loading = await page.locator(LOADING).count() > 0
        if loading and elapsed > 10 and not told:
            print(f"⏳  나이스가 로그인을 처리하는 중입니다 («로딩 중») — 최대 {LOGIN_MAX_S}초 기다립니다", flush=True)
            told = True
        if elapsed > LOGIN_MAX_S or (not loading and elapsed > NO_LOADING_GIVEUP_S):
            break

    # 암호를 가린 «뒤에» 자른다 — 먼저 자르면 경계에 걸린 암호 조각이 가려지지 않고 남는다
    if alerts:
        why = "화면 안내: " + scrub(" / ".join(alerts))[:300]
        hint = f"   암호가 틀렸으면 다시 저장하세요 → {FIX}\n"
    elif await page.locator(LOADING).count():
        why = f"나이스가 {LOGIN_MAX_S}초 넘게 «로딩 중»입니다 (암호 오류 안내는 뜨지 않았습니다)"
        hint = "   나이스 서버가 느린 것일 수 있습니다. 창에서 로딩이 끝나기를 기다리거나 잠시 뒤 다시 실행하세요.\n"
    else:
        why = "화면 안내: " + (scrub(await page.evaluate(JS_CERT_DIALOG))[-160:] or "(화면에 뜬 안내 없음)")
        hint = f"   암호가 틀렸으면 다시 저장하세요 → {FIX}\n"
    raise LoginBlocked(f"암호를 넣었지만 로그인이 안 됐습니다. {why}\n{hint}"
                       "   암호를 여러 번 틀리지 않도록 자동으로 다시 시도하지 않습니다.")


async def wait_manual_login(page, blocked):
    """자동 로그인이 막히면 창을 닫지 않고, 사람이 직접 로그인하기를 기다린다."""
    if MANUAL_WAIT_MIN <= 0:
        raise blocked
    print(f"\n⏸  {blocked}", flush=True)
    print(f"⏸  열린 크롬 창에서 직접 로그인하시거나 로딩이 끝나면 그대로 이어서 진행합니다 (최대 {MANUAL_WAIT_MIN}분 기다립니다).", flush=True)
    for _ in range(MANUAL_WAIT_MIN * 120):
        try:
            if await is_logged_in(page):
                print("✅  로그인 확인 — 이어서 진행합니다", flush=True)
                return
            await page.wait_for_timeout(500)
        except Exception:
            raise LoginBlocked(f"크롬 창이 닫혀서 끝냅니다.\n   {blocked}") from None
    raise LoginBlocked(f"{MANUAL_WAIT_MIN}분 안에 로그인이 안 돼 끝냅니다.\n   {blocked}")


async def login(page, cfg, pw):
    if pw not in _SECRETS:
        _SECRETS.append(pw)
    alerts = []
    state = {"manual": False}

    async def on_dialog(d):
        if state["manual"]:
            # 사람이 직접 로그인하는 중 — 알림·확인창은 사람이 크롬 창에서 읽고 누르게 그대로 둔다
            print(f"   💬 화면 알림 (크롬 창에서 직접 눌러 주세요): {scrub(d.message)[:160]}", flush=True)
            return
        alerts.append(d.message)  # 자동 로그인 중 — 글자를 받아 두고 닫는다 (암호 오류 판정에 쓴다)
        await d.dismiss()
    page.on("dialog", on_dialog)

    url = cfg["neis_url"]
    print(f"🌐  {url}", flush=True)
    try:
        await page.goto(url, timeout=30_000)
    except Exception as e:
        raise LoginBlocked(
            f"나이스 주소에 접속하지 못했습니다 ({str(e).splitlines()[0][:100]}).\n"
            "   ① 설정의 나이스 주소(neis_url)가 맞는지 ② 학교망인지 확인하세요"
            " — 학교 밖이면 교육청 VPN 없이는 안 열릴 수 있습니다") from None
    try:
        await auto_cert_login(page, cfg["cert_name"], pw, alerts)
        return
    except LoginBlocked as e:
        blocked = e
    except Exception as e:
        # 예상 못 한 화면 변화도 창을 닫지 말고 사람에게 넘긴다. 오류 원문은 암호를 가린 첫 줄만
        text = scrub(str(e))                      # 가린 뒤에 첫 줄을 뗀다 (줄바꿈 든 암호 조각 방지)
        first = text.splitlines()[0] if text else ""
        blocked = LoginBlocked(f"자동 로그인 중 예상하지 못한 오류로 멈췄습니다: {type(e).__name__}: {first[:120]}")
    state["manual"] = True
    await wait_manual_login(page, blocked)


async def close_notice(page):
    """로그인 직후 뜨는 공지 팝업을 닫는다 (메뉴 클릭을 가로챈다). 실패해도 로그인은 유지한다."""
    try:
        import main
        await main.dismiss_notice_popup(page, timeout=4000)
    except Exception as e:
        print(f"ℹ️  공지 팝업 정리는 건너뜁니다: {str(e)[:80]}", flush=True)


async def run():
    cfg, pw = preflight()
    free_profile()
    for _ in range(10):           # 방금 닫은 창이 포트를 놓을 때까지 잠깐 기다린다
        if not port_busy():
            break
        time.sleep(0.5)
    else:
        raise LoginBlocked(
            f"원격 조작 포트 {PORT} 을 다른 프로그램이 쓰고 있습니다.\n"
            "   다른 번호로 실행하세요 — PowerShell 에서 $env:NEIS_CDP_PORT='9333' 을 먼저 넣고,\n"
            "   neis_cdp.py 도 같은 창에서 실행합니다.")
    async with async_playwright() as p:
        try:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR), channel="chrome", headless=False, slow_mo=40,
                args=CHROME_ARGS, ignore_default_args=["--enable-automation", "--no-sandbox"], viewport=None,
            )
        except Exception as e:
            raise LoginBlocked("크롬을 띄우지 못했습니다: " + str(e).splitlines()[0][:150] + "\n"
                               "   구글 크롬이 설치돼 있는지 확인하세요.") from None
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(cfg["neis_url"]))
        await ctx.grant_permissions(["notifications"], origin=origin)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        await login(page, cfg, pw)
        if not port_answers():
            raise LoginBlocked(f"로그인은 됐지만 원격 조작 포트 {PORT} 이 열리지 않았습니다.\n"
                               "   다른 번호로 다시 실행해 보세요 — $env:NEIS_CDP_PORT='9333'")
        await close_notice(page)
        print(f"READY port={PORT}", flush=True)
        print(f"   이 창은 {KEEP_OPEN_MIN}분 동안 열려 있습니다. 끝나면 이 프로그램을 종료하세요.", flush=True)
        try:
            for _ in range(KEEP_OPEN_MIN * 12):
                await page.wait_for_timeout(5000)
        except Exception:
            print("ℹ️  크롬 창이 닫혀서 끝냅니다.", flush=True)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="나이스에 로그인해 둔 크롬을 열어 둔다 (출장·41조 연수 등)")
    ap.add_argument("--check", action="store_true", help="저장된 설정·암호만 점검한다 (크롬을 띄우지 않음)")
    args = ap.parse_args()
    try:
        if args.check:
            preflight()
            print("\n✅  저장된 설정·암호 점검 끝. 인증서 선택·암호가 맞는지는 옵션 없이 실행할 때 확인합니다.")
        else:
            asyncio.run(run())
        return 0
    except LoginBlocked as e:
        print(f"\n❌  {e}", flush=True)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"\n❌  예상하지 못한 오류로 멈췄습니다: {type(e).__name__}: {(scrub(str(e)).splitlines() or [''])[0][:160]}\n"
              "   이 줄을 그대로 Claude 에게 보여 주세요.", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
