#!/usr/bin/env python3
"""
나이스 출결 자동 입력기 v4.0
구글 캘린더 출결 캘린더 → 나이스 일일출결관리(담임용) 자동 입력
"""

import asyncio
import re
import sys
from datetime import datetime, timedelta, date
from pathlib import Path
from playwright.async_api import async_playwright, Page

import keyring
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import teacher_config

# ============================================================
# 설정 — 학교 공통(하드코딩) 값
# ============================================================
YEAR       = date.today().year
TOKEN_FILE = teacher_config.TOKEN_FILE

# 아래 값들은 load_teacher_settings()가 설정 파일에서 채운다.
NEIS_URL      = None   # 시도교육청별 나이스 주소
SHEET_ID      = ""     # 휴업일 시트 (비어 있으면 휴업일 확인을 건너뛴다)
HOLIDAY_RANGE = ""
LAST_PERIOD   = 7      # 하루 최대 교시 수

# ============================================================
# 설정 — 교사별 값 (load_teacher_settings()에서 채움)
# ============================================================
GRADE = CLASS = CERT_NAME = CERT_PASSWORD = None
ATTENDANCE_CALENDARS: dict[str, str] = {}


def _origin_of(url: str) -> str:
    """https://xxx.neis.go.kr/jsp/main.jsp  ->  https://xxx.neis.go.kr"""
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def load_teacher_settings() -> None:
    """%LOCALAPPDATA%의 teacher_config.json + keyring에서 교사별 설정을 읽어
    모듈 전역(GRADE/CLASS/CERT_NAME/CERT_PASSWORD/ATTENDANCE_CALENDARS)에 채운다."""
    global GRADE, CLASS, CERT_NAME, CERT_PASSWORD, ATTENDANCE_CALENDARS
    global NEIS_URL, SHEET_ID, HOLIDAY_RANGE, LAST_PERIOD
    cfg = teacher_config.load_config()
    NEIS_URL      = cfg["neis_url"]
    SHEET_ID      = cfg.get("holiday_sheet_id", "")
    HOLIDAY_RANGE = cfg.get("holiday_sheet_range", "")
    LAST_PERIOD   = int(cfg.get("last_period", 7))
    GRADE = cfg["grade"]
    CLASS = cfg["class"]
    CERT_NAME = cfg["cert_name"]
    ATTENDANCE_CALENDARS = cfg["calendars"]
    CERT_PASSWORD = keyring.get_password(teacher_config.KEYRING_SERVICE, CERT_NAME)
    if not CERT_PASSWORD:
        raise teacher_config.ConfigMissingError(
            f"'{CERT_NAME}' 인증서 비밀번호를 찾을 수 없습니다. 설정 마법사를 다시 실행하세요."
        )

KOREAN_WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

# ATTENDANCE_CALENDARS는 load_teacher_settings()가 teacher_config.json에서 채운다
# (교사별 8개 캘린더 ID — 설정 마법사가 자동 생성)

ATTENDANCE_MAP = {
    "질병결석":     ("질병",    "결석"),
    "미인정결석":   ("미인정",  "결석"),
    "기타결석":     ("기타",    "결석"),
    "출석인정결석": ("출석인정","결석"),
    "질병지각":     ("질병",    "지각"),
    "미인정지각":   ("미인정",  "지각"),
    "기타지각":     ("기타",    "지각"),
    "출석인정지각": ("출석인정","지각"),
    "질병조퇴":     ("질병",    "조퇴"),
    "미인정조퇴":   ("미인정",  "조퇴"),
    "기타조퇴":     ("기타",    "조퇴"),
    "출석인정조퇴": ("출석인정","조퇴"),
}

# ============================================================
# 구글 시트 휴업일 읽기
# ============================================================
def load_holidays() -> set:
    if not SHEET_ID:
        return set()   # 휴업일 시트를 설정하지 않았으면 이 기능만 건너뛴다
    try:
        svc = build("sheets", "v4", credentials=Credentials.from_authorized_user_file(str(TOKEN_FILE)))
        res = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=HOLIDAY_RANGE).execute()
        holidays = set()
        for row in res.get("values", []):
            if not row or not row[0].strip():
                continue
            try:
                m, d = row[0].strip().split("/")
                holidays.add(date(YEAR, int(m), int(d)))
            except Exception:
                pass
        return holidays
    except Exception as e:
        print(f"  ⚠️  휴업일 로드 실패: {e}")
        return set()


# ============================================================
# 구글 캘린더 읽기
# ============================================================
def get_calendar_service():
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def load_from_calendar(start_date: date, end_date: date) -> list[dict]:
    service = get_calendar_service()
    time_min = datetime.combine(start_date, datetime.min.time()).isoformat() + "Z"
    time_max = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).isoformat() + "Z"

    records = []
    for att_type, cal_id in ATTENDANCE_CALENDARS.items():
        result = service.events().list(
            calendarId=cal_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        for event in result.get("items", []):
            title = event.get("summary", "")
            parts = title.split()
            if len(parts) < 2:
                continue
            try:
                number = int(parts[0])
            except ValueError:
                continue

            name = parts[1]
            # 교시 정보는 보통 제목 끝 괄호 안에 있음 (예: "31 이영희 (4교시~)")
            paren = re.search(r"\(([^)]*)\)\s*$", title)
            note = paren.group(1) if paren else event.get("description", "")
            ev_start = event["start"]
            ev_end   = event["end"]

            if "date" in ev_start:
                s = date.fromisoformat(ev_start["date"])
                e = date.fromisoformat(ev_end["date"]) - timedelta(days=1)
            else:
                s = datetime.fromisoformat(ev_start["dateTime"]).date()
                e = s

            records.append({
                "number":     str(number),
                "name":       name,
                "att_type":   att_type,
                "start_date": s,
                "end_date":   e,
                "note":       note,
            })

    return records


def _missed_periods(task: dict) -> float:
    """하루 중 빠진 교시 수 추정 — 같은 날 여러 건일 때 많이 빠진 건을 마지막에 입력하기 위한 정렬 키."""
    jongryu = task["jongryu"]
    if jongryu == "결석":
        return 99  # 하루 전체
    g = parse_gyosi_num(task["note"], jongryu)
    if g is None:
        return 50  # 교시 미상이면 중간 순위
    if jongryu == "지각":
        return g + 1  # 조회(0)~g교시까지 빠짐
    if jongryu == "조퇴":
        return LAST_PERIOD - g + 1  # g교시부터 하루 끝까지 빠짐
    return 50


def build_task_map(records: list[dict]) -> dict[date, list[dict]]:
    task_map: dict[date, list[dict]] = {}
    for rec in records:
        att = ATTENDANCE_MAP.get(rec["att_type"])
        if not att:
            print(f"  ⚠️  알 수 없는 분류: '{rec['att_type']}' — 건너뜀")
            continue
        cur = rec["start_date"]
        while cur <= rec["end_date"]:
            if cur.weekday() >= 5:  # 토(5)·일(6)요일은 등교일이 아니므로 건너뜀
                cur += timedelta(days=1)
                continue
            tasks = task_map.setdefault(cur, [])
            # 같은 학생이라도 분류가 다르면 둘 다 입력 (예: 같은 날 지각+조퇴)
            # — 완전히 동일한 (학생, 분류) 건만 중복으로 건너뜀
            if not any(t["number"] == rec["number"] and t["label"] == rec["att_type"] for t in tasks):
                tasks.append({
                    "number":  rec["number"],
                    "name":    rec["name"],
                    "gubun":   att[0],
                    "jongryu": att[1],
                    "note":    rec["note"],
                    "label":   rec["att_type"],
                })
            else:
                print(f"  🔁 중복 건너뜀: {cur} {rec['number']}번 {rec['name']} [{rec['att_type']}]")
            cur += timedelta(days=1)
    # 같은 학생이 하루에 여러 건이면 빠진 시간이 짧은 것부터 입력하고
    # 제일 많이 빠진(제일 안 좋은) 건을 맨 마지막에 입력한다.
    # 예: 지각(조회만 늦음) + 조퇴(2교시~) → 지각 먼저, 조퇴 마지막
    #     지각(~5교시) + 조퇴(7교시~)     → 조퇴 먼저, 지각 마지막
    for tasks in task_map.values():
        tasks.sort(key=_missed_periods)
    return dict(sorted(task_map.items()))


# ============================================================
# NEIS 자동화
# ============================================================
async def auto_login(page: Page, wait_ms: int = 8_000):
    """인증서 자동 로그인. 회선이 느리면 wait_ms를 늘려 부른다."""
    print("🔐  로그인 페이지 확인 중...")

    try:
        await page.wait_for_selector("text=학급담임", timeout=3000)
        print("✅  이미 로그인 상태")
        return
    except:
        pass

    try:
        # 인증서 로그인 버튼 클릭
        btn = page.get_by_role("button", name="인증서 로그인")
        await btn.wait_for(timeout=wait_ms)
        await btn.click()
        print("🔐  인증서 로그인 버튼 클릭")

        # 인증서 선택 (다이얼로그가 뜨는 경우)
        try:
            cert = page.locator(f"xpath=//div[contains(@title,'{CERT_NAME}')]").first
            await cert.wait_for(timeout=5000)
            await cert.click()
            print(f"🔐  인증서 '{CERT_NAME}' 선택")
        except:
            pass  # 자동 선택된 경우 무시

        # 비밀번호 입력 후 Enter (버튼 클릭 대신)
        pwd = page.get_by_role("textbox", name="인증서 암호 입력필드")
        await pwd.wait_for(timeout=8000)
        await pwd.fill(CERT_PASSWORD)
        await pwd.press("Enter")
        print("🔐  비밀번호 입력 완료")
        print("✅  인증서 입력 완료. 로그인 대기 중...")

    except Exception as e:
        print(f"⚠️  자동 로그인 실패: {e}")
        try:    # 무슨 화면에 서 있는지 남긴다 (오류 페이지 구분용)
            print(f"   현재 화면: {page.url}")
            print(f"   제목: {await page.title()}")
        except Exception:
            pass
        print("   브라우저에서 수동으로 로그인해주세요.")


async def wait_for_attendance_page(page: Page):
    print("📌  로그인 대기 중...")
    await page.wait_for_selector("text=학급담임", timeout=120_000)
    print("✅  로그인 완료!")

    # 이미 출결관리 페이지에 있으면 바로 진행 (다른 탭에 숨어있는 버튼은 무시하고 실제로 보이는 것만 확인)
    if await page.locator(".cl-dateinput-button:visible").count() > 0:
        print("✅  출결관리 페이지 이미 열려있음!\n")
        return

    print("📌  출결관리로 이동 중...")

    # 학급담임 → 출결관리(2단계 카테고리) → 출결관리(3단계=일일출결관리)
    # 로그인 직후 공지 팝업이 메뉴를 가리거나, 메뉴가 이미 열려 있어 토글로 닫히는
    # 경우가 있어 팝업 닫기 + 재시도로 감싼다.
    last_error = None
    for attempt in range(1, 4):
        await dismiss_alert_popup(page, timeout=1500)
        try:
            sub2 = page.get_by_role("link", name="출결관리 2단계").first
            if not await sub2.is_visible():
                await page.get_by_role("link", name="학급담임 0단계 메뉴항목").click()
                await page.wait_for_timeout(800)

            await sub2.click(timeout=8000)
            await page.wait_for_timeout(500)

            await page.locator("a").filter(has_text="출결관리 3단계").click(timeout=8000)

            # 날짜 달력 버튼이 나타날 때까지 대기 (숨은 탭의 버튼은 무시)
            await page.locator(".cl-dateinput-button:visible").first.wait_for(timeout=15_000)
            print("✅  출결관리 페이지 진입!\n")
            return
        except Exception as e:
            last_error = e
            print(f"  ⚠️  메뉴 이동 실패, 재시도 {attempt}/3")
            await page.wait_for_timeout(1000)
    raise last_error


async def click_confirm(page: Page, timeout: int = 3000) -> bool:
    """현재 열려있는 팝업의 (실제로 보이는) '확인' 버튼/링크를 클릭.
    다른 숨은 탭에 있는 동명 요소를 잘못 클릭하거나, 그런 요소 때문에
    기본 30초 액션 타임아웃까지 기다리는 것을 막기 위해 :visible로 한정하고
    짧은 timeout을 명시한다."""
    btn = page.locator("a:visible, button:visible", has_text=re.compile(r"^확인$"))
    try:
        await btn.last.click(force=True, timeout=timeout)
        return True
    except Exception:
        return False


async def dismiss_alert_popup(page: Page, timeout=3000) -> bool:
    """알림 팝업이 뜨면 확인 클릭."""
    try:
        await page.wait_for_selector("text=알림", timeout=timeout)
    except Exception:
        return False
    ok = await click_confirm(page, timeout=3000)
    if ok:
        print("  ℹ️  알림 팝업 닫기")
        await page.wait_for_timeout(400)
    return ok


async def set_date_and_search(page: Page, d: date) -> bool:
    date_str = d.strftime("%Y.%m.%d.")
    target_name = f"{d.year}년 {d.month}월 {d.day}일 {KOREAN_WEEKDAYS[d.weekday()]}"

    # overlay 닫기 (사이드바가 열려있으면 Escape로 닫기)
    overlay = page.locator(".cl-overlay")
    if await overlay.count() > 0:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
    if await overlay.count() > 0:
        await overlay.first.click(force=True)
        await page.wait_for_timeout(500)

    # 달력 버튼 클릭 (날짜 선택 후 aria-label이 "선택됨:..."으로 바뀌므로 클래스로 찾기)
    # 페이지에 여러 개 있을 수 있으므로 is_visible()로 실제 보이는 버튼 선택
    all_cal = page.locator(".cl-dateinput-button")
    n = await all_cal.count()
    cal_btn = None
    for i in range(n):
        btn = all_cal.nth(i)
        if await btn.is_visible():
            cal_btn = btn
            break
    if cal_btn is None:
        cal_btn = all_cal.last
    try:
        await cal_btn.click(timeout=5000)
    except:
        await cal_btn.click(force=True)
    await page.wait_for_timeout(400)

    # 날짜 버튼 클릭 (달 이동 필요시 최대 6번 시도)
    date_btn = page.get_by_role("button", name=target_name)
    found = False
    for _ in range(6):
        if await date_btn.count() > 0:
            await date_btn.click()
            found = True
            break
        prev = page.locator("button").filter(has_text=re.compile(r"이전|prev", re.IGNORECASE))
        if await prev.count() > 0:
            await prev.first.click()
            await page.wait_for_timeout(300)
        else:
            break

    if not found:
        print(f"  ⚠️  달력 날짜 버튼 없음, 타이핑 시도: {date_str}")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
        inputs = page.locator("input")
        cnt = await inputs.count()
        for i in range(cnt):
            val = await inputs.nth(i).input_value()
            if val and re.match(r"\d{4}\.\d{2}\.\d{2}", val):
                await inputs.nth(i).click(force=True)
                await inputs.nth(i).press("Control+a")
                await inputs.nth(i).type(date_str, delay=30)
                await inputs.nth(i).press("Tab")
                break

    print(f"  📅  날짜 설정: {date_str}")

    # 날짜를 바꾸면 나이스가 "학적변동 시간표 확인하세요" 같은 알림 팝업을 바로 띄우는
    # 경우가 있음 — 안 닫으면 팝업 오버레이가 조회 버튼 클릭을 막아 타임아웃 남
    await dismiss_alert_popup(page, timeout=1500)

    # 조회 클릭 (tabpanel 안의 <a> 태그)
    # 알림 팝업이 위 dismiss_alert_popup 체크 이후 뒤늦게 뜨는 경우가 있어
    # 오버레이가 클릭을 막으면(30초 타임아웃 대신) 팝업을 다시 닫고 재시도한다.
    조회_btn = page.get_by_role("tabpanel").locator("a").filter(has_text=re.compile(r"^조회$"))
    for attempt in range(3):
        try:
            await 조회_btn.click(timeout=5000)
            break
        except Exception:
            print("  ⚠️  조회 클릭 막힘 (알림 팝업 추정) — 확인 클릭 후 재시도")
            await dismiss_alert_popup(page, timeout=3000)
            await page.wait_for_timeout(300)
    else:
        await 조회_btn.click(force=True)
    print("  ✅  조회 클릭")

    # 알림 팝업 처리
    await dismiss_alert_popup(page, timeout=2000)

    # 그리드 로드 대기
    try:
        await page.wait_for_selector("text=Total", timeout=5000)
    except:
        await page.wait_for_timeout(2000)

    return True


def parse_gyosi_num(note: str, jongryu: str = "") -> int | None:
    """note에서 교시 번호 추출.
    조퇴: 첫 번째 빠진 교시 (e.g. '4교시~'→4, '5,6교시'→5)
    지각: 마지막 빠진 교시 (e.g. '조회'→0, '1교시'→1, '1,2교시'→2)
    """
    if not note:
        return None
    if re.search(r"조회", note):
        return 0
    nums = re.findall(r"\d+", note)
    if not nums:
        return None
    return int(nums[-1]) if jongryu == "지각" else int(nums[0])



GRID_HEADER_JS = r"""
() => {
  const NL = String.fromCharCode(10);
  // 헤더 셀에는 정렬 표시 등이 덧붙는다. 첫 줄의 첫 낱말만 취한다
  // (컬럼 이름에는 공백이 없다: 번호·성명·마감·조회·N교시·종례·사유)
  const clean = t => ((t || '').trim().split(NL)[0].trim().split(/\s+/)[0] || '');
  for (const r of document.querySelectorAll('.cl-grid-row')) {
    const kids = Array.from(r.children).map(d => d.innerText);
    if (kids.length && clean(kids[0]) === '번호') return kids.map(clean);
  }
  return [];
}
"""


async def read_day_columns(page: Page) -> list:
    """그날 그리드의 실제 컬럼 이름 목록.

    ['번호','성명','마감','조회','1교시',...,'종례','사유'] 형태.
    교시 수가 날마다 다르므로(단축수업·학교급별 차이) 위치를 고정하면 안 된다.
    읽지 못하면 빈 리스트 — 그때는 예전처럼 고정 위치로 계산한다.
    """
    try:
        cols = await page.evaluate(GRID_HEADER_JS)
    except Exception:
        return []
    if not isinstance(cols, list):
        return []
    # 제대로 읽었는지 확인한다. 헤더 모양이 바뀌어 엉뚱하게 읽혔는데 그대로 믿으면
    # 멀쩡한 학생을 "없는 교시"라며 통째로 건너뛰게 된다 — 그게 더 나쁘다.
    if "조회" not in cols or not any(c.endswith("교시") for c in cols):
        print("  ⚠️  그리드 헤더를 읽지 못했습니다 — 교시 위치를 예전 방식으로 계산합니다.")
        return []
    return cols


def day_last_period(day_cols) -> int:
    """그날 마지막 교시 번호. 못 읽으면 0."""
    nums = [int(c[:-2]) for c in (day_cols or []) if c.endswith("교시") and c[:-2].isdigit()]
    return max(nums) if nums else 0


def gyosi_col_index(gyosi_num: int, day_cols=None):
    """교시 번호 → td 0-based 인덱스. 그날 없는 교시면 None.

    day_cols(그날 실제 헤더)가 있으면 **이름으로** 찾는다 — 이게 정확한 방법이다.
    헤더 목록의 인덱스가 곧 이 함수가 돌려주는 값이다
    (번호=0, 성명=1, 마감=2, 조회=3, 1교시=4 ...).

    day_cols가 없으면 예전처럼 6~7교시 시간표를 가정하고 계산한다.
    """
    want = "조회" if gyosi_num == 0 else f"{gyosi_num}교시"
    if day_cols:
        return day_cols.index(want) if want in day_cols else None
    if gyosi_num == 0:
        return 3  # 조회
    return 3 + gyosi_num  # 1교시→4, 4교시→7


async def click_magam_cell(page: Page, name: str, number: str, col: int = 2):
    """셀 클릭. col은 td 0-based 인덱스.
    CSS nth-child 오프셋 = col + 2 (그리드 구조상 앞에 숨은 열이 1개 있음)
    마감=2→nth-child(4), 조회=3→nth-child(5), 1교시=4→nth-child(6), 4교시=7→nth-child(9) ...
    """
    row = page.locator(".cl-grid-row").filter(has_text=name).first
    await row.scroll_into_view_if_needed()
    await page.wait_for_timeout(150)

    nth = col + 2
    cell = row.locator(f"> div:nth-child({nth})").locator(".cl-text")
    if await cell.count() == 0:
        cell = row.locator(f"> div:nth-child({nth})")
    await cell.click(timeout=5000)


async def handle_magam_popup(page: Page, gubun: str, jongryu: str):
    """팝업 열리면 구분/종류 선택 후 적용."""
    await page.wait_for_selector("text=출결마감구분", timeout=10_000)
    await page.wait_for_timeout(200)

    # 구분 선택
    await page.get_by_text(gubun, exact=True).first.click()
    await page.wait_for_timeout(200)

    # 종류 선택 (radio)
    await page.get_by_role("radio", name=jongryu).click()
    await page.wait_for_timeout(200)

    # 사유란은 개인정보 우려로 입력하지 않음 (비워둠)

    # 적용
    await page.locator("a").filter(has_text=re.compile(r"^적용$")).click()
    await page.wait_for_timeout(400)


# 학생 한 명의 결과 갈래
FAILED  = "failed"   # ❌ 아예 안 들어감 — 나이스에서 직접 넣어야 한다
PARTIAL = "partial"  # ⚠️ 들어갔지만 교시 칸이 빔 — 교시만 채우면 된다


async def enter_student(page: Page, task: dict, day_cols=None) -> tuple[str, str] | None:
    """한 학생의 출결을 입력한다.

    돌려주는 값: 다 들어갔으면 None, 아니면 (갈래, 이유).
      (FAILED,  이유) — 한 칸도 안 들어갔다
      (PARTIAL, 이유) — 구분/종류는 들어갔는데 교시 칸이 비었다
    예전에는 print 만 하고 삼켜서, 한 명이 빠져도 루프는 그대로 돌고
    마지막에 '모든 출결 입력 완료'가 찍혔다 — 누가 빠졌는지 알 길이 없었다.
    """
    name    = task["name"]
    number  = task["number"]
    gubun   = task["gubun"]
    jongryu = task["jongryu"]
    note    = task["note"]

    # 없는 교시를 요구하면 아예 손대지 않는다.
    # 그날 3교시까지인데 '5교시~'를 넣으라고 하면, 예전 코드는 위치를 고정 계산해
    # '사유' 칸이나 '종례' 칸을 눌러 엉뚱한 값을 만들었다 (오류도 안 났다).
    if jongryu in ("조퇴", "지각") and day_cols:
        _g = parse_gyosi_num(note, jongryu)
        if _g is not None and gyosi_col_index(_g, day_cols) is None:
            last = day_last_period(day_cols)
            print(f"  ⛔  {number}번 {name}: 이 날은 {last}교시까지입니다 "
                  f"— note '{note}' 의 {_g}교시는 없는 교시라 입력하지 않았습니다.")
            print(f"      캘린더 제목을 그날 시간표에 맞게 고친 뒤 다시 돌려주세요.")
            return (FAILED, f"이 날은 {last}교시까지인데 note '{note}' 는 {_g}교시를 가리킴")

    partial_reason = None

    try:
        # 1단계: 항상 마감(col=2) 셀 클릭 → 구분/종류 팝업 → 적용
        await click_magam_cell(page, name, number, col=2)
        await handle_magam_popup(page, gubun, jongryu)

        if jongryu in ("조퇴", "지각"):
            # 2단계: 교시 셀 클릭 (마감 팝업 적용 후 수정된 행에서 해당 교시 셀 클릭)
            gyosi_num = parse_gyosi_num(note, jongryu)
            if gyosi_num is not None:
                col = gyosi_col_index(gyosi_num, day_cols)
                row = page.locator(".cl-grid-row").filter(has_text=name).first
                nth = col + 2
                cell = row.locator(f"> div:nth-child({nth})").locator(".cl-text")
                if await cell.count() == 0:
                    cell = row.locator(f"> div:nth-child({nth})")
                await cell.click(timeout=5000)
                await page.wait_for_timeout(300)
                # 교시 셀 클릭 후 팝업이 다시 열리면 그대로 적용
                apply_btn = page.locator("a").filter(has_text=re.compile(r"^적용$"))
                if await apply_btn.count() > 0:
                    await apply_btn.click()
                    await page.wait_for_timeout(300)
                gyosi_label = " 조회" if gyosi_num == 0 else f" {gyosi_num}교시"
            else:
                # 구분/종류는 이미 들어갔지만 교시 칸은 빈 채로 남는다.
                # 예전에는 ✅ 로만 찍혀 '성공'으로 세었다 — 선생님이 알 수가 없었다.
                gyosi_label = f" (교시 미지정, note='{note}')"
                print(f"  ⚠️  {number}번 {name}: 교시 정보를 note에서 찾을 수 없음 "
                      f"— 교시 칸이 빈 채로 들어갑니다")
                partial_reason = f"note='{note}' 에서 교시를 못 읽어 교시 칸이 빔"
            print(f"  ✅  {number}번 {name}: {gubun}/{jongryu}{gyosi_label}")
        else:
            print(f"  ✅  {number}번 {name}: {gubun}/{jongryu}")
    except Exception as e:
        print(f"  ❌  {number}번 {name} 실패: {e}")
        return (FAILED, f"{type(e).__name__}: {e}")

    if partial_reason:
        return (PARTIAL, partial_reason)
    return None


def report_failures(problems: list[dict]) -> int:
    """못 들어간 학생을 갈래별로 찍고 종료코드를 돌려준다 (하나라도 있으면 1)."""
    if not problems:
        print("\n🎉  모든 출결 입력 완료!")
        return 0

    failed  = [p for p in problems if p["kind"] == FAILED]
    partial = [p for p in problems if p["kind"] == PARTIAL]

    def block(items, mark, title, tail):
        print("\n" + "!" * 60)
        print(f"{mark}  {len(items)}명 — {title}")
        print("!" * 60)
        for p in items:
            print(f"  {mark}  {p['date'].strftime('%m/%d')}  {p['number']}번 {p['name']}")
            print(f"        이유: {p['reason']}")
        print("!" * 60)
        print(f"  ※  {tail}")
        print("!" * 60)

    if failed:
        block(failed, "❌", "안 들어감 (나이스에서 직접 넣어야 함)",
              "이 학생들은 저장된 내용에 없습니다. 나이스에서 직접 입력해 주세요.")
    if partial:
        block(partial, "⚠️", "들어갔지만 교시 없음 (교시만 채우면 됨)",
              "구분·종류는 저장됐습니다. 나이스에서 교시 칸만 채워 주세요.")

    print(f"\n   정리: ❌ 안 들어감 {len(failed)}명 / ⚠️ 교시 없음 {len(partial)}명")
    return 1


async def save_page(page: Page) -> tuple[bool, str]:
    """그날 입력분을 저장한다. 돌려주는 값: (저장됐나, 이유).

    ⚠️ 예전에는 확인 팝업 두 개를 각각 `except: pass` 로 삼키고 아무것도 돌려주지
       않았다. 그래서 «저장이 안 됐는데 마지막에 🎉 가 뜨고 종료코드 0» 이 될 수
       있었다 — 그날 학생 전원이 통째로 빠지는 건데도 알 길이 없었다.
    """
    # 직전 단계에서 알림 팝업·사이드바 오버레이가 남아 있으면 저장 클릭을 막으므로 먼저 정리
    await dismiss_alert_popup(page, timeout=1000)
    overlay = page.locator(".cl-overlay")
    if await overlay.count() > 0:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)

    # 저장 버튼 클릭 (<button> 역할, <a>가 아님 — codegen에서 확인)
    await page.get_by_role("button", name="저장").click()
    await page.wait_for_timeout(600)

    # 1차 팝업: "저장하시겠습니까?" → 확인
    asked = False
    try:
        await page.wait_for_selector("text=저장하시겠습니까", timeout=5000)
        asked = await click_confirm(page, timeout=3000)
        if asked:
            print("  ✅  저장 확인")
        await page.wait_for_timeout(600)
    except Exception:
        pass

    # 2차 팝업: "저장했습니다" 또는 "변경된 내용이 없습니다" → 확인
    told = False
    no_change = False
    try:
        await page.wait_for_selector("text=알림", timeout=6000)
        no_change = await page.locator("text=변경된 내용이 없습니다").count() > 0
        told = await click_confirm(page, timeout=3000)
        if no_change:
            print("  ℹ️  변경 내용 없음 (이미 저장됨)")
        else:
            print("  💾  저장 완료!")
    except Exception:
        pass

    await page.wait_for_timeout(600)

    if no_change:
        # 방금 학생을 넣었는데 «변경 없음» 이면 입력이 반영되지 않은 것이다.
        return (False, "나이스가 「변경된 내용이 없습니다」라고 답했습니다 — 입력이 반영되지 않았습니다")
    if not (asked or told):
        return (False, "저장 확인창이 뜨지 않았습니다 — 저장됐는지 확인할 수 없습니다")
    return (True, "")


# ============================================================
# 메인
# ============================================================
async def main():
    args = sys.argv[1:]

    print("=" * 52)
    print("🏫  나이스 출결 자동 입력기 v4.0")
    print("=" * 52)

    try:
        load_teacher_settings()
    except teacher_config.ConfigMissingError as e:
        print(f"\n❌  {e}")
        sys.exit(1)
    print(f"👤  {GRADE}학년 {CLASS}반 담임 ({CERT_NAME})")

    # --future: 오늘 이후 날짜도 입력 (예정된 결석을 미리 넣을 때)
    allow_future = "--future" in args
    # --dry-run: 무엇을 넣을지 목록만 보여주고 나이스는 열지 않는다
    dry_run = "--dry-run" in args
    date_arg = next((a for a in args if re.match(r"\d{4}-\d{2}-\d{2}", a)), None)
    if date_arg:
        try:
            start_date = date.fromisoformat(date_arg)
        except ValueError:
            print("❌  날짜 형식 오류. YYYY-MM-DD로 입력하세요.")
            sys.exit(1)
    else:
        today = date.today()
        start_date = date(today.year, today.month, 1)

    end_date = max(date.today(), start_date)
    print(f"\n📅  {start_date} ~ {end_date} 캘린더 읽는 중...")

    records  = load_from_calendar(start_date, end_date)
    task_map = build_task_map(records)
    total    = sum(len(v) for v in task_map.values())

    if total == 0:
        print("✅  해당 기간에 입력할 출결이 없습니다.")
        sys.exit(0)

    print(f"\n📊  {len(task_map)}개 날짜, 총 {total}건\n")
    for d, tasks in task_map.items():
        print(f"  {d.strftime('%m/%d')} — "
              + " / ".join(f"{t['number']}번 {t['name']}({t['label']})" for t in tasks))
    print()

    if dry_run:
        print("🔍  --dry-run 이라 여기서 멈춥니다. 나이스는 열지 않았습니다.")
        print("    목록이 맞으면 --dry-run 을 빼고 다시 실행하세요.")
        return

    PROFILE_DIR = str(teacher_config.CONFIG_DIR / "chrome_profile")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            channel="chrome",
            headless=False,
            slow_mo=50,
            args=[
                "--profile-directory=Default",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=PrivateNetworkAccessForNavigations,PrivateNetworkAccessPermissionPrompt,PermissionChip",
                "--disable-web-security",
                "--allow-running-insecure-content",
                "--test-type",
                "--start-maximized",
                "--noerrdialogs",
            ],
            ignore_default_args=["--enable-automation", "--no-sandbox"],
            viewport=None,
        )
        await context.grant_permissions(["notifications"], origin=_origin_of(NEIS_URL))
        page = context.pages[0] if context.pages else await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")

        failures: list[dict] = []   # 못 들어간 학생 (날짜·갈래·번호·이름·이유)
        exit_code = 0

        try:
            await page.goto(NEIS_URL)
            await auto_login(page)
            await wait_for_attendance_page(page)

            holidays = load_holidays()
            if holidays:
                print(f"  📅  휴업일 {len(holidays)}개 로드: {sorted(d.strftime('%m/%d') for d in holidays)}")

            for d, tasks in task_map.items():
                if d < start_date:
                    print(f"  ⏭  {d.strftime('%m/%d')} 건너뜀 (시작일 이전)")
                    continue
                if d > date.today() and not allow_future:
                    print(f"  ⏭  {d.strftime('%m/%d')} 건너뜀 (미래 날짜 — --future 옵션으로 미리 입력 가능)")
                    continue
                if d in holidays:
                    print(f"  ⏭  {d.strftime('%m/%d')} 건너뜀 (휴업일)")
                    continue
                if d.weekday() >= 5:
                    print(f"  ⏭  {d.strftime('%m/%d')} 건너뜀 (주말)")
                    continue
                print(f"\n📅  {d.strftime('%Y년 %m월 %d일')} ({len(tasks)}명)...")
                await dismiss_alert_popup(page, timeout=1000)
                await set_date_and_search(page, d)
                day_cols = await read_day_columns(page)
                last_p = day_last_period(day_cols)
                if last_p:
                    print(f"    이 날 시간표: {last_p}교시까지")
                for task in tasks:
                    result = await enter_student(page, task, day_cols)
                    if result:
                        kind, reason = result
                        failures.append({
                            "date":   d,
                            "kind":   kind,
                            "number": task["number"],
                            "name":   task["name"],
                            "reason": reason,
                        })
                # 실패자가 있어도 저장은 그대로 한다 —
                # 여기서 막으면 이미 들어간 학생들까지 같이 날아간다.
                saved, save_reason = await save_page(page)
                if not saved:
                    # 저장이 안 됐으면 그날 «전원» 이 안 들어간 것이다.
                    # 이미 개별 사유로 잡힌 학생은 빼고 나머지를 통째로 올린다.
                    already = {f["name"] for f in failures if f["date"] == d}
                    print(f"  ❌  {d.strftime('%m/%d')} 저장 실패 — {save_reason}")
                    for task in tasks:
                        if task["name"] in already:
                            continue
                        failures.append({
                            "date":   d,
                            "kind":   FAILED,
                            "number": task["number"],
                            "name":   task["name"],
                            "reason": f"저장 실패 — {save_reason}",
                        })

            exit_code = report_failures(failures)
        except Exception as e:
            import traceback
            print(f"\n❌  오류 발생: {e}")
            traceback.print_exc()
            if failures:
                report_failures(failures)
            exit_code = 1
            print("\n⚠️  브라우저는 열려 있습니다. 확인 후 Enter를 누르세요.")
            try:
                input("브라우저 닫으려면 Enter...")
            except EOFError:
                pass
        finally:
            await context.close()

        return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
