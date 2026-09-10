#!/usr/bin/env python3
"""최초 1회 실행 — 학교/학년·반, 인증서, 구글 계정 연동을 설정한다.

재실행해도 안전하다(idempotent): 이미 있는 캘린더는 재사용하고,
기존 설정값은 기본값으로 보여주므로 Enter만 눌러 그대로 둘 수 있다.

설정은 저장소가 아니라 %LOCALAPPDATA%\neis-automation 에 저장된다.
"""
import json
import sys
from getpass import getpass
from pathlib import Path

import keyring
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

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
    print("   서울이면 기본값 그대로 Enter.")
    neis_url = ask("나이스 주소", old.get("neis_url", teacher_config.DEFAULTS["neis_url"]))

    print("\n── 2. 담임 학급 (담임이 아니면 그냥 Enter) ──")
    print("   학생 출결 자동화에만 쓰는 값입니다.")
    print("   부장·교과 전담 등 담임이 아니시면 두 항목 모두 비워 두세요.")
    grade  = ask("학년 (예: 1)", old.get("grade", ""), required=False)
    class_ = ask("반 (예: 7)", old.get("class", ""), required=False)

    print("\n── 3. 공동인증서 ──")
    print("   나이스 '인증서 로그인' 창에 뜨는 이름을 그대로 적으세요.")
    print("   (확인하는 법은 docs/01_처음-준비.md 의 '인증서 이름 확인' 참고)")
    cert_name = ask("인증서 이름", old.get("cert_name", ""))
    cert_password = getpass("인증서 비밀번호 (화면에 표시되지 않습니다, 그대로 두려면 Enter): ")
    if not cert_password and not keyring.get_password(teacher_config.KEYRING_SERVICE, cert_name):
        print("  ⚠️  저장된 비밀번호가 없습니다. 다시 입력해주세요.")
        cert_password = getpass("인증서 비밀번호: ")

    print("\n── 4. 복무 신청용 값 (안 쓰면 그냥 Enter) ──")
    contact = ask("근무상황신청에 넣을 연락처 (예: 010-1234-5678)",
                  old.get("contact", ""), required=False)
    approval_line = ask("나이스에 저장해 둔 개인결재선 프리셋 이름",
                        old.get("approval_line", ""), required=False)

    print("\n── 5. 휴업일 시트 (선택, 안 쓰면 그냥 Enter) ──")
    print("   재량휴업일에 출결을 입력하지 않으려면 날짜가 적힌 구글 시트 ID를 넣으세요.")
    holiday_sheet = ask("휴업일 시트 ID", old.get("holiday_sheet_id", ""), required=False)
    holiday_range = old.get("holiday_sheet_range", teacher_config.DEFAULTS["holiday_sheet_range"])
    if holiday_sheet:
        holiday_range = ask("휴업일이 적힌 범위 (예: 출결!K2:K50)", holiday_range)

    print("\n── 6. 학생 출결 자동화 (구글 캘린더) ──")
    print("   복무(조퇴·외출·출장)만 쓰실 거면 이 단계는 건너뛰셔도 됩니다.")
    print("   출결까지 쓰시려면 본인 구글 클라우드 프로젝트에서 발급한")
    print("   oauth_client.json 이 이 폴더에 있어야 합니다.")
    use_calendar = ask("출결 자동화도 쓰시겠습니까? (y/n)",
                       "y" if old.get("calendars") else "n", required=False)

    calendars = old.get("calendars", {})
    if use_calendar.strip().lower().startswith("y"):
        creds = run_oauth()
        print("\n📅  출결 분류 캘린더 확인/생성 중...")
        calendars = ensure_calendars(creds)
    else:
        print("   건너뜁니다. 나중에 출결도 쓰시려면 이 설정을 다시 돌리면 됩니다.")

    if cert_password:
        keyring.set_password(teacher_config.KEYRING_SERVICE, cert_name, cert_password)

    teacher_config.save_config({
        "neis_url": neis_url,
        "grade": grade,
        "class": class_,
        "cert_name": cert_name,
        "contact": contact,
        "approval_line": approval_line,
        "holiday_sheet_id": holiday_sheet,
        "holiday_sheet_range": holiday_range,
        "last_period": old.get("last_period", 7),
        "calendars": calendars,
    })

    print("\n🎉  설정 완료!")
    print(f"   설정 파일: {teacher_config.CONFIG_FILE}")
    print("   비밀번호는 이 파일이 아니라 Windows 자격 증명 관리자에 따로 저장됩니다.")
    print("\n   이제 구글 캘린더에 출결 이벤트를 넣고  python main.py  를 실행하면 됩니다.")


if __name__ == "__main__":
    main()
