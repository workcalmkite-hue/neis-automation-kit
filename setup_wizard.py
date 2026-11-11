#!/usr/bin/env python3
"""최초 1회 실행 — 학교/학년·반, 인증서, 구글 계정 연동을 설정한다.

재실행해도 안전하다(idempotent): 이미 있는 캘린더는 재사용하고,
기존 설정값은 기본값으로 보여주므로 Enter만 눌러 그대로 둘 수 있다.

설정은 저장소가 아니라 %LOCALAPPDATA%\neis-automation 에 저장된다.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import keyring
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import school_calendar
import teacher_config

# 구글 OAuth 클라이언트 정보는 저장소에 커밋하지 않는다.
# 각자 본인 구글 클라우드 프로젝트에서 발급해 oauth_client.json 으로 저장한다.
# (만드는 방법: docs/02_구글-연동.md)
_OAUTH_CLIENT_FILE = Path(__file__).parent / "oauth_client.json"

SCOPES = [
    # calendar(읽기+쓰기): 분류 캘린더 8개를 자동으로 만들기 위해 필요
    "https://www.googleapis.com/auth/calendar",
    # 휴업일 시트 읽기용 (시트를 안 쓰면 실제로는 호출되지 않는다)
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

ATTENDANCE_CALENDAR_NAMES = [
    "질병결석", "질병지각", "질병조퇴",
    "미인정결석", "미인정지각", "미인정조퇴",
    "출석인정결석", "출석인정지각", "출석인정조퇴",
]


def load_oauth_client() -> dict:
    if not _OAUTH_CLIENT_FILE.exists():
        print(f"\n❌  {_OAUTH_CLIENT_FILE.name} 파일이 없습니다.")
        print("   구글 캘린더를 읽으려면 본인 구글 OAuth 클라이언트가 필요합니다.")
        print("   docs/02_구글-연동.md 를 따라 5분이면 만들 수 있습니다.")
        print("   (oauth_client.example.json 을 복사해 값만 바꿔 넣으세요)")
        sys.exit(1)
    try:
        data = json.loads(_OAUTH_CLIENT_FILE.read_text(encoding="utf-8"))
        # 구글에서 받은 파일 그대로(client_secret_….json / credentials.json — {"installed": {...}})도 받는다
        for k in ("installed", "web"):
            if isinstance(data.get(k), dict):
                data = data[k]
                break
        return {
            "installed": {
                "client_id": data["client_id"],
                "client_secret": data["client_secret"],
                "redirect_uris": ["http://localhost"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
    except (KeyError, json.JSONDecodeError) as e:
        print(f"\n❌  {_OAUTH_CLIENT_FILE.name} 형식이 잘못됐습니다: {e}")
        print("   oauth_client.example.json 과 같은 모양이어야 합니다.")
        sys.exit(1)


def run_oauth():
    print("\n🔐  브라우저가 열립니다. 출결 캘린더를 쓸 구글 계정으로 로그인/동의해주세요.")
    print("   (「이 앱은 Google에서 확인하지 않았습니다」 경고가 뜨면")
    print("    → 고급 → '(프로젝트 이름)(으)로 이동' 을 누르면 됩니다. 본인이 만든 앱이라 정상입니다.)")
    flow = InstalledAppFlow.from_client_config(load_oauth_client(), SCOPES)
    creds = flow.run_local_server(port=0)
    teacher_config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    teacher_config.TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"✅  구글 계정 연동 완료 ({teacher_config.TOKEN_FILE})")
    return creds


def ensure_calendars(creds) -> dict:
    """분류 캘린더가 이미 있으면 재사용, 없으면 새로 만든다."""
    service = build("calendar", "v3", credentials=creds)
    existing, page_token = {}, None
    while True:
        result = service.calendarList().list(pageToken=page_token).execute()
        for entry in result.get("items", []):
            existing[entry["summary"]] = entry["id"]
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    calendars = {}
    for name in ATTENDANCE_CALENDAR_NAMES:
        if name in existing:
            calendars[name] = existing[name]
            print(f"  ↺  '{name}' 캘린더 기존 것 재사용")
            continue
        created = service.calendars().insert(body={"summary": name}).execute()
        calendars[name] = created["id"]
        print(f"  ✅  '{name}' 캘린더 생성")
    return calendars


def connect_calendars() -> dict:
    """구글 로그인 → 출결 캘린더 9개. 실패해도 예외를 밖으로 내지 않는다 (앞에서 넣은 인증서·복무 값은 살린다)."""
    try:
        creds = run_oauth()
        print("\n📅  출결 분류 캘린더 확인/생성 중...")
        return ensure_calendars(creds)
    except (SystemExit, Exception) as e:
        print(f"  ⚠️  구글 캘린더 연결은 못 했습니다 ({e or '열쇠 파일 문제'}).")
        print("     나머지 설정은 그대로 둡니다. 열쇠를 확인한 뒤 «출결 연결해줘» 로 다시 하시면 됩니다.")
        return {}


def calendar_only() -> int:
    """`setup_wizard.py --calendar` — 다른 질문 없이 출결(구글 캘린더) 연결만 한다.
    키보드 입력이 필요 없어서(구글 로그인은 브라우저 창) 클로드가 직접 돌려도 된다.
    연수 날엔 열쇠가 없어 건너뛰고, 나중에 열쇠를 받은 뒤 이것만 돌리는 길이다 (2026-11-06)."""
    try:
        cfg = teacher_config.load_config()
    except teacher_config.ConfigMissingError:
        print("❌  아직 최초 설정을 안 하셨습니다. setup_wizard.py 를 먼저 돌려 주세요.")
        return 1
    if not _OAUTH_CLIENT_FILE.exists():
        print(f"❌  구글 열쇠 파일이 없습니다: {_OAUTH_CLIENT_FILE}")
        print("   docs/02_구글-연동.md 대로 구글 클라우드에서 열쇠를 만든 뒤 다시 돌려 주세요.")
        return 1
    calendars = connect_calendars()
    if not calendars:
        return 1
    teacher_config.save_config({**cfg, "calendars": calendars})
    print(f"\n🎉  출결 캘린더 {len(calendars)}개 연결 완료. 클로드 앱을 껐다 켜면 출결 도구가 붙습니다.")
    if not (cfg.get("grade") and cfg.get("class")):
        print("   ⚠️  학년·반이 비어 있습니다. 담임 학급을 넣으려면 setup_wizard.py 를 한 번 더 돌려 주세요 (나머지는 Enter).")
    return 0


# 지역 이름으로 답해도 된다 — 나이스 주소 앞글자 = 교육청 코드 (17개 모두 DNS 확인, 2026-09-18)
REGION_CODES = {
    "서울": "sen", "부산": "pen", "대구": "dge", "인천": "ice", "광주": "gen",
    "대전": "dje", "울산": "use", "세종": "sje", "경기": "goe", "강원": "gwe",
    "충북": "cbe", "충남": "cne", "전북": "jbe", "전남": "jne", "경북": "gbe",
    "경남": "gne", "제주": "jje",
}


def to_neis_url(answer: str) -> str:
    """「경기」처럼 지역 이름으로 답하면 그 지역 나이스 주소로 바꾼다. 주소면 그대로."""
    a = answer.strip()
    for name, code in REGION_CODES.items():
        if a in (name, name + "도", name + "시", name + "특별시", name + "광역시"):
            return f"https://{code}.neis.go.kr/jsp/main.jsp"
    return a


def gpki_names() -> list:
    r"""C:\GPKI\Certificate\class2 의 파일 이름에서 교육행정 인증서 이름을 뽑는다.
    `123홍길동456_sig.cer` → 홍길동. 폴더가 없으면 빈 목록 (그 PC 에 인증서가 없는 것)."""
    import re
    folder = Path(r"C:\GPKI\Certificate\class2")
    names = []
    if folder.is_dir():
        for f in sorted(folder.glob("*_sig.cer")):
            m = re.search(r"[가-힣]+", f.name)
            if m and m.group(0) not in names:
                names.append(m.group(0))
    return names


def has_keyboard() -> bool:
    """사람이 칠 수 있는 진짜 콘솔인가. ★ isatty() 는 못 믿는다 — 윈도우에서는 입력이
    NUL 장치로 막혀 있어도 True 가 나온다 (2026-09-18 실측). 콘솔 모드를 직접 물어본다."""
    if os.name != "nt":
        return sys.stdin is not None and sys.stdin.isatty()
    try:
        import ctypes
        import msvcrt
        h = msvcrt.get_osfhandle(sys.stdin.fileno())
        mode = ctypes.c_uint()
        return bool(ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode)))
    except Exception:
        return False


def relaunch_in_new_window() -> int:
    """클로드가 돌리면 키보드 입력을 받을 수 없다 — 선생님이 칠 수 있게 «새 창»으로 띄운다.

    선생님께 «검은 창을 여세요»라고 하지 않으려고 만든 길이다. 새 창이 뜨고, 거기서 답하고,
    끝나면 창이 닫힌다. 이 쪽(클로드가 보는 쪽)은 창이 닫힐 때까지 기다렸다가 결과만 알려준다.
    비밀번호는 새 창에서만 받으므로 클로드 대화에 남지 않는다.
    """
    env = dict(os.environ, NEIS_WIZARD_WINDOW="1")
    cmd = [sys.executable, "-X", "utf8", str(Path(__file__).resolve())]
    print("🪟  설정 창을 새로 띄웁니다. 작업표시줄에 새로 뜬 검은 창에서 질문에 답해 주세요.")
    print("   (인증서 비밀번호도 그 창에만 칩니다. 이 대화에는 남지 않습니다)", flush=True)
    # ★ 입출력을 부모(클로드 쪽)에서 물려받지 않게 끊는다 — 물려받으면 새 창이 떠도
    #   질문이 클로드 쪽으로 가고 입력은 빈 채로 끝난다 (2026-09-18 실측). 새 창이 자기 콘솔을 연다.
    proc = subprocess.Popen(cmd, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True)
    start = time.time()
    while proc.poll() is None:
        time.sleep(2)
        if time.time() - start > 15 * 60:
            print("⏱  15분이 지나도 창이 안 닫혔습니다. 창을 확인해 주세요.")
            return 1
    try:
        cfg = teacher_config.load_config()
    except Exception:
        print("❌  설정이 저장되지 않았습니다 (창을 중간에 닫으셨거나 오류가 났습니다).")
        return 1
    name = cfg.get("cert_name", "")
    saved = bool(name and keyring.get_password(teacher_config.KEYRING_SERVICE, name))
    print("\n결과 (비밀번호·연락처는 가려서 보여줍니다)")
    print(f"   나이스 주소       {cfg.get('neis_url', '')}")
    print(f"   학년·반           {cfg.get('grade') or '-'} / {cfg.get('class') or '-'}")
    print(f"   인증서 이름       {name[:1] + '*' * max(len(name) - 1, 0) if name else '(없음)'}")
    print(f"   인증서 비밀번호   {'저장됨' if saved else '❌ 없음'}")
    print(f"   복무 연락처·결재선 {'있음' if cfg.get('contact') else '없음'} / {'있음' if cfg.get('approval_line') else '없음'}")
    print(f"   우리 학교         {cfg.get('school_name') or '-'}")
    print(f"   출결 캘린더       {len(cfg.get('calendars') or {})}개")
    return 0 if (name and saved and proc.returncode == 0) else 1


def ask(label: str, default: str = "", required: bool = True) -> str:
    """기본값이 있으면 보여주고, 그냥 Enter 치면 기본값을 쓴다."""
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip() or default
        if value or not required:
            return value
        print("  ⚠️  꼭 입력해야 하는 항목입니다.")


def main():
    print("=" * 56)
    print("🏫  나이스 자동화 — 최초 설정")
    print("=" * 56)

    try:
        old = teacher_config.load_config()
        print("\n(기존 설정을 찾았습니다. 그대로 두려면 각 항목에서 그냥 Enter를 누르세요.)")
    except teacher_config.ConfigMissingError:
        old = dict(teacher_config.DEFAULTS)

    print("\n── 1. 나이스 접속 주소 ──")
    print("   브라우저로 본인 지역 나이스에 접속한 뒤 주소창을 그대로 붙여넣으세요.")
    print("   서울이면 기본값 그대로 Enter. 지역 이름(예: 경기)만 쳐도 됩니다.")
    # ★ 지역 이름·나이스 주소가 아니면 다시 묻는다 — 새 창이 앞으로 뜨는 사이 다른 데 치던 글자가 섞여
    #   «RUDD서울» 같은 값이 그대로 저장된 일이 있었다 (2026-11-06 윈도우 실기기 시험)
    while True:
        neis_url = to_neis_url(ask("나이스 주소", old.get("neis_url", teacher_config.DEFAULTS["neis_url"])))
        if neis_url.startswith("http") and ".neis.go.kr" in neis_url:
            break
        print("  ⚠️  지역 이름(예: 서울 · 경기)이나 나이스 주소(https://○○.neis.go.kr/…)로 다시 쳐 주세요.")

    print("\n── 2. 담임 학급 (담임이 아니면 그냥 Enter) ──")
    print("   학생 출결 자동화에만 쓰는 값입니다.")
    print("   부장·교과 전담 등 담임이 아니시면 두 항목 모두 비워 두세요.")
    grade  = ask("학년 (예: 1)", old.get("grade", ""), required=False)
    class_ = ask("반 (예: 7)", old.get("class", ""), required=False)

    print("\n── 3. 교육행정 인증서 ──")
    found = gpki_names()
    if found:
        print(f"   이 컴퓨터에서 찾은 인증서 이름: {', '.join(found)}")
        print("   맞으면 그냥 Enter. 여러 개면 쓰실 이름을 그대로 치세요.")
    else:
        print(r"   ⚠️  C:\GPKI\Certificate\class2 에 교육행정 인증서가 없습니다.")
        print("   평소 업무포털이 되는 컴퓨터인지, USB 인증서면 꽂혀 있는지 확인해 주세요.")
        print("   나이스 '인증서 로그인' 창에 뜨는 이름을 알면 그대로 적어도 됩니다.")
    cert_name = ask("인증서 이름", old.get("cert_name", "") or (found[0] if found else ""))
    cert_password = teacher_config.secret_input("인증서 비밀번호 (화면에 표시되지 않습니다, 그대로 두려면 Enter): ")
    if not cert_password and not keyring.get_password(teacher_config.KEYRING_SERVICE, cert_name):
        print("  ⚠️  저장된 비밀번호가 없습니다. 다시 입력해주세요.")
        cert_password = teacher_config.secret_input("인증서 비밀번호: ")

    print("\n── 4. 복무 신청용 값 (안 쓰면 그냥 Enter) ──")
    contact = ask("근무상황신청에 넣을 연락처 (예: 010-1234-5678)",
                  old.get("contact", ""), required=False)
    approval_line = ask("나이스에 저장해 둔 개인결재선 프리셋 이름",
                        old.get("approval_line", ""), required=False)

    print("\n── 5. 우리 학교 (휴업일 자동 확인) ──")
    print("   학교 이름을 치면 나이스 학사일정에서 재량휴업일·방학을 알아서 찾습니다.")
    print("   출결을 안 쓰시면 그냥 Enter.")
    school = {k: old.get(k, "") for k in ("school_office", "school_code", "school_name")}
    while True:
        q = ask("학교 이름 (예: 원묵중)", school["school_name"], required=False)
        if not q or q == school["school_name"]:
            break
        # ★ y/n 같은 짧은 답은 학교 이름이 아니다 — 옛 판은 6번 질문이 안 보여서 여기에 y/n 을 쳤고,
        #   «n» 이 든 학교 하나가 묻지도 않고 저장됐다 (2026-11-06 윈도우 실기기 재현)
        if q.strip().lower() in ("y", "n", "yes", "no", "예", "네", "아니요", "아니오", "ㅛ", "ㅜ") or len(q.strip()) < 2:
            print("  ⏭  학교 이름이 아니라서 이 칸은 건너뜁니다. (학교 이름은 두 글자 이상, 예: 원묵중)")
            break
        try:
            found_s = school_calendar.find_school(q, school_calendar.office_from_neis_url(neis_url)) \
                or school_calendar.find_school(q)
        except Exception as e:
            # 0건과 구분한다 — 이 칸은 «학교가 없다»가 아니라 «검색 서버에 못 갔다» (2026-11-11 한밭고)
            print(f"  ⚠️  나이스 학교 검색 서버에 연결하지 못했습니다 — {school_calendar.why(e)}")
            print(f"      ({type(e).__name__}: {e})")
            print("      학교 이름이 틀린 게 아닙니다. 그냥 Enter 로 넘어가고, 나중에 설정 마법사를 다시 돌려 학교만 넣어도 됩니다.")
            continue
        if not found_s:
            print("  ⚠️  그 이름의 학교가 없습니다. «중학교»를 빼고 짧게 쳐 보세요.")
            continue
        for i, s in enumerate(found_s, 1):
            print(f"   {i}) {s['name']} · {s['office_name']} · {s['addr']}")
        if len(found_s) == 1:
            pick = 1
        else:
            pick = ask("몇 번입니까? (안 보이면 그냥 Enter 하고 더 길게 다시)", "", required=False)
            if not pick.isdigit() or not 1 <= int(pick) <= len(found_s):
                continue
        s = found_s[int(pick) - 1]
        school = {"school_office": s["office"], "school_code": s["code"], "school_name": s["name"]}
        print(f"  ✅  {s['name']} — 휴업일은 나이스 학사일정에서 자동으로 확인합니다")
        break

    print("\n── 6. 학생 출결 자동화 (구글 캘린더) ──")
    print("   담임이시면 y, 담임이 아니면 n (복무·에듀파인만 쓰시는 분).")
    calendars = old.get("calendars", {})
    # ★ 구글 열쇠(oauth_client.json)가 없어도 묻는다 — 화면에 «담임이면 y»라고 적혀 있으니 담임은 y 를 누른다.
    #   그때 오류로 끝내지 말고 «출결 연결은 나중에»로 넘긴다. 열쇠는 연수 뒤 따로 받는 분이 많다
    #   (2026-10-21 매그넷고: y → «파일이 없습니다»로 통째로 끝남 / 2026-11-06: 열쇠 없는데 질문이 안 보여 헷갈림)
    if not _OAUTH_CLIENT_FILE.exists():
        print("   구글 열쇠가 아직 없어도 y 를 누르셔도 됩니다 — 오류 없이 넘어가고 나머지는 다 저장됩니다.")
    use_calendar = ask("출결 자동화도 쓰시겠습니까? (y/n)",
                       "y" if old.get("calendars") else "n", required=False)

    if use_calendar.strip().lower().startswith("y"):
        if not _OAUTH_CLIENT_FILE.exists():
            print("  ⏭  구글 열쇠(oauth_client.json)가 아직 없어서 출결 연결은 다음으로 미룹니다.")
            print("     복무는 지금 바로 쓰실 수 있습니다.")
            print("     열쇠를 만든 뒤 클로드에게 «출결 연결해줘» 라고 하시면 이 단계만 다시 합니다.")
        else:
            calendars = connect_calendars() or calendars
    else:
        print("   건너뜁니다. 나중에 출결도 쓰시려면 클로드에게 «출결 연결해줘» 라고 하시면 됩니다.")

    if cert_password:
        keyring.set_password(teacher_config.KEYRING_SERVICE, cert_name, cert_password)

    teacher_config.save_config({
        **old,                              # 에듀파인 쪽이 넣은 값(region 등)을 지우지 않는다
        "neis_url": neis_url,
        "grade": grade,
        "class": class_,
        "cert_name": cert_name,
        "contact": contact,
        "approval_line": approval_line,
        **school,
        "last_period": old.get("last_period", 7),
        "calendars": calendars,
    })

    print("\n🎉  설정 완료!")
    try:
        import neis_cert
        if not neis_cert.program_installed():
            print("   ⚠️  이 PC에 나이스 인증서 프로그램이 없습니다 — 클로드가 다음 단계에서 깔아 드립니다.")
    except Exception:
        pass
    print(f"   설정 파일: {teacher_config.CONFIG_FILE}")
    print("   비밀번호는 이 파일이 아니라 Windows 자격 증명 관리자에 따로 저장됩니다.")
    if calendars:
        print("\n   이제 구글 캘린더에 출결 이벤트를 넣고  python main.py  를 실행하면 됩니다.")


if __name__ == "__main__":
    if "--calendar" in sys.argv[1:]:
        sys.exit(calendar_only())
    # 클로드가 돌리면 입력창이 없다 → 새 창으로 띄운다 (선생님이 직접 검은 창을 열 필요가 없다)
    if not os.environ.get("NEIS_WIZARD_WINDOW") and os.name == "nt" and not has_keyboard():
        sys.exit(relaunch_in_new_window())
    if os.environ.get("NEIS_WIZARD_WINDOW"):
        # 새 창 쪽 — 이 창의 키보드·화면에 직접 붙는다
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stderr = sys.stdout
        teacher_config.prepare_console_window()
    try:
        main()
        code = 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception as e:
        print(f"\n❌  오류: {e}")
        code = 1
    if os.environ.get("NEIS_WIZARD_WINDOW"):
        if code:
            try:
                input("\n창을 닫으려면 Enter 를 누르세요...")
            except EOFError:
                pass
        else:
            # 성공이면 저절로 닫는다 — Enter 를 안 누르면 창이 하루 넘게 남아 있었다 (2026-10-02 실측)
            print("\n✅ 끝났습니다. 이 창은 20초 뒤 저절로 닫힙니다 (Enter 를 누르면 바로).")
            print("   창이 닫힌 뒤에도 클로드가 가만히 있으면, 클로드에게 이렇게 치세요:")
            print("   «설정 창 끝났어. 확인하고 이어서 해줘»")
            try:
                import msvcrt
                end = time.time() + 20
                while time.time() < end and not msvcrt.kbhit():
                    time.sleep(0.2)
            except Exception:
                time.sleep(20)
    sys.exit(code)
