#!/usr/bin/env python3
"""나이스 복무(근무상황) 자동 상신 스크립트.

조퇴/외출/지각을 개인근무상황관리 화면에서 자동으로 신청하고 승인요청(상신)까지 완료한다.
로그인은 main.py의 인증서 자동 로그인 로직을 그대로 재사용한다.

사용 예:
    python main_bokmu.py --type 조퇴 --date 2026-07-10 --start 15:00 --end 16:20 --reason "개인 사유"
    python main_bokmu.py --type 외출 --date 2026-07-10 --start 10:00 --end 11:00 --reason "은행 업무" --dry-run
"""
import argparse
import asyncio
import re
import sys
from datetime import date, datetime

from playwright.async_api import async_playwright, Page

import teacher_config
import main as neis  # 로그인/알림팝업 로직 재사용

WORK_SITTN_TYPES = {"지각", "조퇴", "외출"}
MINUTE_STEP = 5  # 시작/종료 '분' 콤보박스는 5분 단위로만 선택 가능


def round_to_step(hm: str, step: int = MINUTE_STEP) -> str:
    h, m = map(int, hm.split(":"))
    m = round(m / step) * step
    if m == 60:
        m = 0
        h = (h + 1) % 24
    return f"{h:02d}:{m:02d}"


async def select_combo_option(page: Page, combo, option_text: str, max_scroll: int = 8):
    """콤보박스(cl-text[role=combobox])를 열고 옵션(cl-combobox-item[role=option])을 선택한다.
    목록이 가상 스크롤이라 원하는 값이 안 보이면 popup 안에서 스크롤하며 찾는다."""
    await combo.scroll_into_view_if_needed()
    await page.wait_for_timeout(200)
    await combo.click(timeout=5000, force=True)

    popup = page.locator('.cl-combobox-list[role="listbox"]').last
    await popup.wait_for(state="visible", timeout=5000)

    option = popup.locator('.cl-combobox-item[role="option"]').get_by_text(option_text, exact=True)

    # 가상 스크롤이라 목록 맨 위로 올린 뒤 아래로 훑으며 찾는다 (값이 위/아래 어느 쪽에 있는지 몰라도 안전)
    await popup.hover()
    for _ in range(max_scroll):
        await page.mouse.wheel(0, -400)
        await page.wait_for_timeout(80)

    for _ in range(max_scroll * 2):
        if await option.count() > 0 and await option.first.is_visible():
            break
        await page.mouse.wheel(0, 120)
        await page.wait_for_timeout(150)
    else:
        raise RuntimeError(f"콤보박스 옵션 '{option_text}'을(를) 찾지 못함")

    await option.first.click(timeout=5000)
    await page.wait_for_timeout(200)


async def fill_text_input(page: Page, locator, value: str):
    await locator.click(timeout=5000)
    await locator.press("Control+a")
    await locator.type(value, delay=20)
    await page.wait_for_timeout(150)


async def open_gwn_apply_modal(page: Page):
    """복무 > 개인근무상황관리 진입 후 '신청' 버튼을 눌러 근무상황신청 모달을 연다."""
    await page.locator("a").filter(has_text="복무 0단계 메뉴항목").first.click(timeout=8000)
    await page.wait_for_timeout(600)
    await page.locator("a").filter(has_text="개인근무상황관리 1단계").first.click(timeout=8000)
    await page.wait_for_timeout(1000)

    apply_btns = page.get_by_text("신청", exact=True)
    n = await apply_btns.count()
    clicked = False
    for i in range(n):
        cand = apply_btns.nth(i)
        if await cand.is_visible():
            await cand.click(timeout=5000)
            clicked = True
            break
    if not clicked:
        raise RuntimeError("보이는 '신청' 버튼을 찾지 못함")

    dialog = page.locator('[role="dialog"][aria-label="근무상황신청"]')
    await dialog.wait_for(state="visible", timeout=10_000)
    await page.wait_for_timeout(1500)  # 상단 안내 배너 레이아웃 안정화 대기
    return dialog


async def fill_work_sittn_form(
    page: Page,
    dialog,
    sittn_type: str,
    d: date,
    start_hm: str,
    end_hm: str,
    reason: str,
    contact: str | None,
):
    date_str = d.strftime("%Y-%m-%d")
    start_hm = round_to_step(start_hm)
    end_hm = round_to_step(end_hm)
    sh, sm = start_hm.split(":")
    eh, em = end_hm.split(":")

    # 1) 근무상황소분류 (근무상황대분류는 기본값 '연가'로 이미 세팅되어 있음)
    sub_combo = dialog.locator('[role="combobox"][aria-label^="근무상황소분류"]').first
    await select_combo_option(page, sub_combo, sittn_type)

    # 2) 기간 시작/종료일자
    start_date_input = dialog.locator('input[aria-label="신청기간 시작일자"]').first
    await fill_text_input(page, start_date_input, date_str)
    end_date_input = dialog.locator('input[aria-label="신청기간 종료일자"]').first
    await fill_text_input(page, end_date_input, date_str)

    # 3) 시작/종료 시·분
    start_h_combo = dialog.locator('[role="combobox"][aria-label^="시작 시"]').first
    await select_combo_option(page, start_h_combo, sh)
    start_m_combo = dialog.locator('[role="combobox"][aria-label^="시작 분"]').first
    await select_combo_option(page, start_m_combo, sm)
    end_h_combo = dialog.locator('[role="combobox"][aria-label^="종료 시"]').first
    await select_combo_option(page, end_h_combo, eh)
    end_m_combo = dialog.locator('[role="combobox"][aria-label^="종료 분"]').first
    await select_combo_option(page, end_m_combo, em)

    # 4) 연락처 (선택 입력)
    if contact:
        contact_input = dialog.locator('input[aria-label="연락처"]').first
        await fill_text_input(page, contact_input, contact)

    # 5) 사유 또는 용무 (연가사유 드롭다운은 기본값 '선택' 그대로 둠 — 경조사 등 특수 법정사유 전용)
    reason_input = dialog.locator('input[aria-label="사유 또는 용무"]').first
    await fill_text_input(page, reason_input, reason)


async def submit(page: Page, dialog, approval_line: str):
    """승인요청 → 기안문서상신 화면 → 개인결재선 지정 → 상신까지 완료한다.

    NEIS 근무상황신청은 '승인요청'을 눌러도 결재선을 지정하지 않으면 그냥 저장만 되고
    실제 결재라인으로 올라가지 않는다. 반드시 개인결재선(사전 등록된 프리셋) → 지정 →
    상신 순서를 거쳐야 진짜 상신이 완료된다.
    """
    submit_btn = dialog.get_by_text("승인요청", exact=True)
    await submit_btn.first.click(timeout=5000)
    await page.wait_for_timeout(800)
    await neis.dismiss_alert_popup(page, timeout=2000)
    await page.wait_for_timeout(400)

    doc_dialog = page.locator('[role="dialog"][aria-label="기안문서상신"]')
    try:
        await doc_dialog.wait_for(state="visible", timeout=15_000)
    except Exception:
        await page.screenshot(path="bokmu_submit_debug.png")
        raise

    personal_line_btn = doc_dialog.get_by_text("개인결재선", exact=True)
    await personal_line_btn.first.click(timeout=5000)
    await page.wait_for_timeout(800)

    line_dialog = page.locator('[role="dialog"][aria-label="개인 결재선 지정"]')
    await line_dialog.wait_for(state="visible", timeout=10_000)

    preset_row = line_dialog.get_by_text(approval_line, exact=True)
    await preset_row.first.click(timeout=5000)
    await page.wait_for_timeout(800)  # 오른쪽 결재선 패널 자동 로드 대기

    assign_btn = line_dialog.locator('[aria-label="지정"][role="button"]').first
    await assign_btn.click(timeout=5000)
    await page.wait_for_timeout(800)

    real_submit_btn = doc_dialog.locator('[aria-label="상신"][role="button"]').first
    await real_submit_btn.click(timeout=5000)
    await page.wait_for_timeout(800)

    # '상신' 클릭 후 확인 팝업이 최소 2개 연달아 뜬다:
    #   1) 알림 "최종 결재자 이외의 결재자는 검토로 변경합니다" → 확인
    #   2) 확인 "상신하겠습니까?" → 확인 (여기까지 눌러야 진짜 상신 완료)
    for _ in range(4):
        await page.wait_for_timeout(500)
        btn = page.locator('[role="button"][aria-label="확인"]:visible')
        if await btn.count() > 0:
            await btn.first.click(timeout=3000)
            continue
        a_confirm = page.locator("a").filter(has_text=re.compile(r"^확인$"))
        if await a_confirm.count() > 0 and await a_confirm.last.is_visible():
            await a_confirm.last.click(force=True, timeout=3000)
            continue
        break


async def run(args):
    neis.load_teacher_settings()
    PROFILE_DIR = str(teacher_config.CONFIG_DIR / "chrome_profile")

    cfg = teacher_config.load_config()
    contact = args.contact or cfg.get("contact")
    if not contact:
        print("❌  연락처가 없습니다. --contact 로 주거나 설정 파일의 contact 값을 채우세요.")
        sys.exit(1)
    if not args.approval_line:
        args.approval_line = cfg.get("approval_line")
    if not args.approval_line:
        print("❌  개인결재선 프리셋 이름이 없습니다. --approval-line 으로 주거나")
        print("    설정 파일의 approval_line 값을 채우세요 (나이스에 미리 저장해 둔 결재선 이름).")
        sys.exit(1)

    d = date.fromisoformat(args.date)
    if args.type not in WORK_SITTN_TYPES:
        print(f"❌  지원하지 않는 근무상황: {args.type} (지원: {', '.join(WORK_SITTN_TYPES)})")
        sys.exit(1)

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
        await context.grant_permissions(["notifications"], origin=neis._origin_of(neis.NEIS_URL))
        page = context.pages[0] if context.pages else await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")

        try:
            await page.goto(neis.NEIS_URL)
            await neis.auto_login(page)
            await page.wait_for_selector("text=학급담임", timeout=120_000)
            await page.wait_for_timeout(1000)
            print("✅  로그인 완료")

            dialog = await open_gwn_apply_modal(page)
            print("✅  근무상황신청 모달 진입")

            await fill_work_sittn_form(
                page, dialog,
                sittn_type=args.type,
                d=d,
                start_hm=args.start,
                end_hm=args.end,
                reason=args.reason,
                contact=contact,
            )
            print(f"✅  폼 입력 완료: {args.type} {d} {args.start}~{args.end} 사유='{args.reason}'")

            await page.screenshot(path="bokmu_form_filled.png")
            print("📸  스크린샷 저장: bokmu_form_filled.png")

            if args.dry_run:
                print("🧪  --dry-run 모드: 승인요청을 누르지 않고 종료합니다.")
            else:
                await submit(page, dialog, approval_line=args.approval_line)
                print("🎉  결재선 지정 + 상신 완료!")

            print("\n브라우저는 15초 후 자동으로 닫힙니다...")
            await page.wait_for_timeout(15_000)

        except Exception as e:
            import traceback
            print(f"\n❌  오류 발생: {e}")
            traceback.print_exc()
            print("\n⚠️  브라우저는 열려 있습니다. 확인 후 Enter를 누르세요.")
            try:
                input("브라우저 닫으려면 Enter...")
            except EOFError:
                pass
        finally:
            await context.close()


def main():
    parser = argparse.ArgumentParser(description="나이스 복무(근무상황) 자동 상신")
    parser.add_argument("--type", required=True, choices=sorted(WORK_SITTN_TYPES), help="지각/조퇴/외출")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--start", required=True, help="시작 시각 HH:MM")
    parser.add_argument("--end", required=True, help="종료 시각 HH:MM")
    parser.add_argument("--reason", required=True, help="사유 또는 용무")
    parser.add_argument("--contact", default=None, help="연락처 (선택, 예: 010-1234-5678)")
    parser.add_argument(
        "--approval-line", default=None,
        help="개인결재선 프리셋 이름 (미지정 시 설정 파일의 approval_line 값을 쓴다)",
    )
    parser.add_argument("--dry-run", action="store_true", help="폼만 채우고 승인요청은 누르지 않음")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
