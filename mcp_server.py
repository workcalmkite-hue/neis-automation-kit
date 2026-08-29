#!/usr/bin/env python3
"""나이스 출결 자동화 — Claude Code용 MCP 서버.

이걸 연결해 두면 클로드 코드에서 이렇게 말할 수 있습니다.

    "출결 캘린더 9개 만들어줘"
    "7번 박지우 오늘 2교시부터 질병조퇴 넣어줘"

구글 캘린더 공식 MCP(커넥터)에는 캘린더를 **만드는** 기능이 없습니다.
일정을 넣고 빼는 것까지만 됩니다. 그래서 이 서버가 그 자리를 채웁니다.

연결 방법은 docs/05_MCP-연결.md 를 보세요.

인증은 설정 마법사(setup_wizard.py)가 만들어 둔 토큰을 그대로 씁니다.
따로 로그인할 필요가 없습니다.
"""
import re
import sys
from datetime import date, datetime, timedelta

from mcp.server.fastmcp import FastMCP

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import teacher_config

mcp = FastMCP("neis-attendance")

# 분류 캘린더 9개 — 이름과 색(구글 캘린더 colorId 1~24)
CALENDARS = [
    ("질병결석", "11"), ("질병지각", "4"),  ("질병조퇴", "6"),
    ("미인정결석", "3"), ("미인정지각", "1"), ("미인정조퇴", "9"),
    ("출석인정결석", "10"), ("출석인정지각", "2"), ("출석인정조퇴", "7"),
]
CALENDAR_NAMES = [name for name, _ in CALENDARS]


# ============================================================
# 구글 연결
# ============================================================
def _service():
    token = teacher_config.TOKEN_FILE
    if not token.exists():
        raise RuntimeError(
            "구글 계정이 연동되지 않았습니다.\n"
            "먼저 설정 마법사를 실행하세요:  python setup_wizard.py"
        )
    creds = Credentials.from_authorized_user_file(str(token))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=creds)


def _all_calendars(svc) -> dict[str, str]:
    """{캘린더 이름: id}"""
    out, page = {}, None
    while True:
        res = svc.calendarList().list(pageToken=page).execute()
        for c in res.get("items", []):
            out[c.get("summary", "")] = c["id"]
        page = res.get("nextPageToken")
        if not page:
            break
    return out


# ============================================================
# 도구 — 캘린더 만들기
# ============================================================
@mcp.tool()
def make_attendance_calendars() -> dict:
    """출결 분류 캘린더 9개를 한 번에 만듭니다 (일정이 아니라 캘린더 자체).

    질병 / 미인정 / 출석인정  ×  결석 / 지각 / 조퇴 조합입니다.
    이미 같은 이름이 있으면 건너뛰므로 여러 번 불러도 캘린더가 중복되지 않습니다.

    Returns:
        made: 이번에 새로 만든 캘린더
        skipped: 이미 있어서 건너뛴 캘린더
    """
    svc = _service()
    have = _all_calendars(svc)
    made, skipped = [], []

    for name, color in CALENDARS:
        if name in have:
            skipped.append(name)
            continue
        cal = svc.calendars().insert(
            body={"summary": name, "timeZone": "Asia/Seoul"}
        ).execute()
        try:
            svc.calendarList().patch(
                calendarId=cal["id"], body={"colorId": color}
            ).execute()
        except Exception:
            pass   # 색이 안 걸려도 캘린더는 만들어졌으니 넘어간다
        made.append(name)

    return {
        "made": made,
        "skipped": skipped,
        "message": f"새로 만든 것 {len(made)}개 / 이미 있어서 건너뛴 것 {len(skipped)}개",
    }


@mcp.tool()
def check_attendance_calendars() -> dict:
    """출결 캘린더 9개가 다 있는지 확인만 합니다 (아무것도 만들지 않습니다)."""
    have = _all_calendars(_service())
    present = [n for n in CALENDAR_NAMES if n in have]
    missing = [n for n in CALENDAR_NAMES if n not in have]
    return {
        "present": present,
        "missing": missing,
        "ready": not missing,
        "message": ("9개 모두 준비됐습니다."
                    if not missing else
                    f"{len(missing)}개가 없습니다: {', '.join(missing)}"),
    }


# ============================================================
# 도구 — 출결 넣기
# ============================================================
def _parse_period(note: str, jongryu: str) -> str:
    """'2교시~' / '~2교시' 를 그대로 통과시키고, 방향이 빠졌으면 채워준다."""
    if not note:
        return ""
    note = note.strip()
    if "~" in note:
        return note
    m = re.search(r"(\d+)\s*교시", note)
    if not m:
        return note
    n = m.group(1)
    if jongryu == "조퇴":
        return f"{n}교시~"     # 그 교시부터 없음
    if jongryu == "지각":
        return f"~{n}교시"     # 그 교시까지 없었음
    return note


@mcp.tool()
def add_attendance(number: int, name: str, category: str,
                   start_date: str, end_date: str = "", note: str = "") -> dict:
    """학생 출결을 캘린더에 넣습니다. 제목 형식을 알아서 맞춥니다.

    Args:
        number: 출석번호 (예: 7)
        name: 학생 이름 (예: 박지우)
        category: 분류 9개 중 하나
            질병결석 / 질병지각 / 질병조퇴
            미인정결석 / 미인정지각 / 미인정조퇴
            출석인정결석 / 출석인정지각 / 출석인정조퇴
        start_date: 시작일 YYYY-MM-DD
        end_date: 마지막 결석일 YYYY-MM-DD (여러 날 결석일 때만.
            구글 캘린더의 +1일 처리는 이 함수가 알아서 합니다)
        note: 교시나 특이사항 (예: "2교시~", "~2교시", "생리결석")

    조퇴는 "5교시~"(그 교시부터 없음), 지각은 "~2교시"(그 교시까지 없었음)입니다.
    숫자만 주면(예: "2교시") 분류에 맞는 방향을 자동으로 붙입니다.
    """
    if category not in CALENDAR_NAMES:
        return {"error": f"모르는 분류입니다: {category}",
                "사용 가능": CALENDAR_NAMES}

    svc = _service()
    have = _all_calendars(svc)
    if category not in have:
        return {"error": f"'{category}' 캘린더가 없습니다.",
                "해결": "make_attendance_calendars 를 먼저 실행하세요."}

    jongryu = category[-2:]                      # 결석 / 지각 / 조퇴
    note = _parse_period(note, jongryu)

    try:
        s = date.fromisoformat(start_date)
    except ValueError:
        return {"error": f"날짜 형식이 잘못됐습니다: {start_date} (YYYY-MM-DD 로 주세요)"}

    if jongryu in ("지각", "조퇴"):
        e_exclusive = s + timedelta(days=1)       # 조퇴·지각은 항상 하루
    else:
        last = date.fromisoformat(end_date) if end_date else s
        e_exclusive = last + timedelta(days=1)    # 구글 캘린더 종료일은 exclusive

    title = f"{number} {name} {category}" + (f" ({note})" if note else "")
    ev = svc.events().insert(
        calendarId=have[category],
        body={
            "summary": title,
            "start": {"date": s.isoformat()},
            "end": {"date": e_exclusive.isoformat()},
        },
    ).execute()

    return {
        "title": title,
        "calendar": category,
        "start": s.isoformat(),
        "end_exclusive": e_exclusive.isoformat(),
        "event_id": ev["id"],
        "message": f"'{title}' 을(를) {category} 캘린더에 넣었습니다.",
    }


@mcp.tool()
def list_attendance(start_date: str, end_date: str) -> dict:
    """기간 안의 출결을 캘린더 9개에서 모아 보여줍니다 (읽기만 합니다).

    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD (이 날짜까지 포함)
    """
    svc = _service()
    have = _all_calendars(svc)
    t_min = f"{start_date}T00:00:00Z"
    t_max = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat() + "T00:00:00Z"

    rows = []
    for name in CALENDAR_NAMES:
        if name not in have:
            continue
        res = svc.events().list(
            calendarId=have[name], timeMin=t_min, timeMax=t_max,
            singleEvents=True, orderBy="startTime",
        ).execute()
        for e in res.get("items", []):
            st = e["start"].get("date") or e["start"].get("dateTime", "")[:10]
            rows.append({"date": st, "category": name,
                         "title": e.get("summary", ""), "event_id": e["id"]})

    rows.sort(key=lambda r: (r["date"], r["title"]))
    return {"count": len(rows), "items": rows}


if __name__ == "__main__":
    mcp.run()
