"""교사별 로컬 설정 저장 위치 및 로드/저장 유틸.

설정은 저장소 폴더가 아니라 %LOCALAPPDATA%\neis-automation 에 저장한다.
  - 저장소를 git pull 로 업데이트해도 내 설정이 지워지지 않는다
  - 개인정보(학년·반·인증서 이름·캘린더 ID)가 실수로 커밋될 일이 없다

인증서 비밀번호는 이 파일에 저장하지 않는다 — Windows 자격 증명 관리자(keyring)에만 들어간다.
"""
import json
import os
from pathlib import Path

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
