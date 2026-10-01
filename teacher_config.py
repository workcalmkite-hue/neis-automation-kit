"""교사별 로컬 설정 저장 위치 및 로드/저장 유틸.

설정은 저장소 폴더가 아니라 %LOCALAPPDATA%\neis-automation 에 저장한다.
  - 저장소를 git pull 로 업데이트해도 내 설정이 지워지지 않는다
  - 개인정보(학년·반·인증서 이름·캘린더 ID)가 실수로 커밋될 일이 없다

인증서 비밀번호는 이 파일에 저장하지 않는다 — Windows 자격 증명 관리자(keyring)에만 들어간다.
"""
import json
import os
import sys
from pathlib import Path

# 윈도우 파워셸·클로드 앱 터미널은 기본 코드페이지가 cp949 라서, 한글·이모지를 찍으면
# UnicodeEncodeError 로 스크립트가 죽는다 (2026-09-16 샌드박스 리허설에서 --dry-run 이 이것으로 실패).
# 이 파일은 모든 실행 스크립트가 import 하므로 여기서 한 번만 출력 인코딩을 UTF-8 로 돌린다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass   # 파이프로 묶였거나 reconfigure 가 없는 경우 — 그냥 넘어간다

CONFIG_DIR   = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "neis-automation"
CONFIG_FILE  = CONFIG_DIR / "teacher_config.json"
TOKEN_FILE   = CONFIG_DIR / "token.json"
KEYRING_SERVICE = "neis-automation"

REQUIRED_KEYS = ("grade", "class", "cert_name", "calendars")

# 설정에 없으면 이 값을 쓴다 (모두 개인정보가 아닌 일반 기본값)
DEFAULTS = {
    # 시도교육청별 나이스 주소. 본인 지역 주소는 브라우저에서 나이스에 접속해
    # 주소창을 그대로 복사하면 된다 (설정 마법사가 물어본다).
    "neis_url": "https://sen.neis.go.kr/jsp/main.jsp",
    # 휴업일(재량휴업일 등)을 읽어올 구글 시트. 비워두면 이 기능만 건너뛴다.
    "holiday_sheet_id": "",
    "holiday_sheet_range": "출결!K2:K50",
    # 복무 신청 시 쓰는 값
    "contact": "",                 # 예: "010-1234-5678" — 근무상황신청 연락처
    "approval_line": "",           # 나이스에 미리 저장해 둔 개인결재선 프리셋 이름
    "last_period": 7,              # 하루 최대 교시 수
}


class ConfigMissingError(Exception):
    pass


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise ConfigMissingError(
            f"설정 파일이 없습니다: {CONFIG_FILE}\n"
            "먼저 설정 마법사를 실행하세요:  python setup_wizard.py"
        )
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ConfigMissingError(
            f"설정 파일에 누락된 항목: {missing}\n설정 마법사를 다시 실행하세요:  python setup_wizard.py"
        )
    return {**DEFAULTS, **data}


def save_config(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def secret_input(prompt: str) -> str:
    """비밀번호를 화면에 안 보이게 받는다.

    ★ getpass 를 그대로 쓰면 안 되는 자리가 있다 — 설정 마법사처럼 sys.stdin 을 CONIN$ 로
    갈아 끼운 새 창에서는 getpass 가 «Can not control echo on the terminal» 경고를 내고
    친 글자를 그대로 화면에 찍는다 (2026-10-01 샌드박스 실측). 윈도우에서는 키보드를 직접 읽는다.
    """
    if os.name != "nt":
        from getpass import getpass
        return getpass(prompt)
    import msvcrt
    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            break
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x08":
            if chars:
                chars.pop()
            continue
        if ch in ("\x00", "\xe0"):   # 화살표·F키 같은 특수키는 두 글자로 온다 — 버린다
            msvcrt.getwch()
            continue
        chars.append(ch)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(chars)


def prepare_console_window() -> None:
    """새로 띄운 검은 창을 «바로 칠 수 있는 상태»로 만든다 (윈도우만).

    ① 빠른 편집(QuickEdit) 끄기 — 켜져 있으면 창을 마우스로 한 번 누르는 순간 «선택 모드»가 되고,
       그다음 Enter 는 답이 아니라 «선택 복사»로 먹힌다. 선생님 눈에는 «Enter 가 안 먹는다»로 보인다
       (2026-10-01 샌드박스 실측: 창을 누른 뒤 첫 Enter 가 사라졌다).
    ② 창을 맨 앞으로 — 클로드 앱 뒤에 숨어 뜨면 선생님이 못 찾는다.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        import msvcrt
        k32 = ctypes.windll.kernel32
        h = msvcrt.get_osfhandle(sys.stdin.fileno())
        mode = ctypes.c_uint()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
            k32.SetConsoleMode(h, (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS)
        hwnd = k32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
