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
import time
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


# '상신' 뒤에 떠도 되는 팝업. 이 문구가 없는 팝업은 누르지 않는다.
# ── 상신 뒤 뜨는 확인창 (2026-09-10 윈도우 · sen.neis.go.kr 에서 실측) ──────────
# [상신] 을 누르면 메시지 상자가 정확히 2개, 순서대로 뜬다. 둘 다 380x216 이고
# class 에 modal-msg 가 붙는다. 구분은 aria-label 로 한다 (role·class 는 둘이 같다).
#
#   ① aria-label="알림"   "최종 결재자 이외의 결재자는 / 검토로 변경합니다"   버튼: 확인
#   ② aria-label="확인"   "상신하겠습니까?"                                버튼: 확인, 취소
#      └ 이 ②의 [확인] 을 눌러야 진짜 상신이다. 그 전까지는 취소하면 안 올라간다.
#
# 같은 순간 화면에는 근무상황신청(910px)·기안문서상신(1210px) 다이얼로그도 함께 떠 있다.
# 하지만 둘 다 modal-msg 가 아니고 '확인' 버튼도 없어서 아래 선택자에 걸리지 않는다.
#
# ⚠️  innerText 에는 제목과 버튼 라벨이 같이 딸려온다.
#     ②의 실제 값은 "확인\n상신하겠습니까?\n확인\n취소" 다. 그래서 공백을 한 칸으로
#     눌러 «버튼 글자까지 포함한 전체 본문» 이 똑같은지 본다. 부분일치가 아니다 —
#     이유는 바로 아래 SUBMIT_POPUP_STEPS 설명.
MSG_BOX = '[role="dialog"].modal-msg'

# 정규화한 «전체 본문»으로 맞춘다. 부분일치로 두면 안 된다 —
# "중복 신청입니다. 그래도 상신하겠습니까?" 같은 경고가 그대로 통과한다.
# (2026-09-10 코덱스 검토 지적. 부분일치였던 초안은 이 경우를 못 막았다)
SUBMIT_POPUP_STEPS = (
    ("알림", "알림 최종 결재자 이외의 결재자는 검토로 변경합니다 확인"),
    ("확인", "확인 상신하겠습니까? 확인 취소"),
)

EXPECTED_DESC = " → ".join(f"[{a}] {x}" for a, x in SUBMIT_POPUP_STEPS)


def norm_text(s: str) -> str:
    """줄바꿈·연속공백·﻿NBSP 를 한 칸으로 눌러 비교용 문자열을 만든다."""
    return re.sub(r"\s+", " ", (s or "").replace("\u00a0", " ")).strip()


def match_popup_step(aria: str, text: str):
    """이 상자가 몇 번째 단계인가. 어느 것도 아니면 None."""
    want = norm_text(text)
    for i, (a, x) in enumerate(SUBMIT_POPUP_STEPS):
        if aria == a and want == x:
            return i
    return None


def dump_popup_html(tag: str, popups) -> list:
    """팝업의 outerHTML 을 파일로 남긴다. 나중에 구조가 바뀌면 이 파일로 진단한다."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = []
    for i, (_, aria, text, html) in enumerate(popups, 1):
        path = f"bokmu_popup_{tag}_{stamp}_{i}_{aria or '무제'}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write("<!-- 팝업에 보이던 글자 -->\n<!--\n")
            f.write(text.strip().replace("-->", "-- >"))
            f.write("\n-->\n")
            f.write(html)
        paths.append(path)
    return paths


async def pause_for_manual(message: str, detail: str = ""):
    """자동 처리를 멈추고 사람에게 넘긴다. 브라우저는 열린 채로 둔다.

    예외를 던지지 않는 이유: 추정이 틀렸을 때 원래 되던 상신까지 막아 버리는 것이
    잘못 눌리는 것 다음으로 나쁘다. 화면은 이미 떠 있으니 사람이 보고 누르면 된다.
    """
    print("\n" + "=" * 60)
    print(f"⛔  {message}")
    if detail:
        print(detail)
    print("    브라우저는 열어 둡니다. 화면에서 직접 처리하신 뒤 Enter를 누르세요.")
    print("=" * 60)
    try:
        await asyncio.to_thread(input, "  Enter... ")
    except EOFError:
        pass


async def visible_confirm_popups(page: Page, timeout: int = 5000):
    """지금 떠 있는 «메시지 상자»를 (확인버튼, aria-label, 글자, outerHTML) 로 모아 온다.

    2026-09-10 실측대로 role="dialog" + class 에 modal-msg 가 붙은 상자만 본다.
    예전에는 '확인' 버튼에서 조상을 거슬러 올라가 상자를 «추정»했는데,
    실제 DOM 을 받아 보니 상자 자체가 role="dialog" 라 그럴 필요가 없었다.
    """
    deadline = time.monotonic() + timeout / 1000
    while True:
        found = []
        boxes = page.locator(MSG_BOX)
        for i in range(await boxes.count()):
            box = boxes.nth(i)
            if not await box.is_visible():
                continue
            aria = (await box.get_attribute("aria-label")) or ""
            text = (await box.inner_text()) or ""
            html = await box.evaluate("el => el.outerHTML")
            # 실측 구조 (2026-09-10):
            #   <div role="button" class="btn-secondary cl-control cl-button">        ← 확인
            #   <div role="button" class="btn-outline-secondary cl-control cl-button"> ← 취소
            # 버튼에는 aria-label 이 없다. 글자 '확인' 은 안쪽 .cl-text 에 들어 있다.
            # 그래서 aria-label 로 찾으면 0개가 나온다 — 상자 안 role=button 을
            # 글자로 거른다. a/button 까지 넓히지는 않는다 (숨은 복제 버튼 방지).
            ok_btn = box.locator('[role="button"]').filter(
                has_text=re.compile(r"^\s*확인\s*$"))
            found.append((ok_btn, aria, text, html))
        if found or time.monotonic() >= deadline:
            return found
        await page.wait_for_timeout(300)


async def submit(page: Page, dialog, approval_line: str, dump_popup: bool = False) -> bool:
    """승인요청 → 기안문서상신 화면 → 개인결재선 지정 → 상신까지 완료한다.

    NEIS 근무상황신청은 '승인요청'을 눌러도 결재선을 지정하지 않으면 그냥 저장만 되고
    실제 결재라인으로 올라가지 않는다. 반드시 개인결재선(사전 등록된 프리셋) → 지정 →
    상신 순서를 거쳐야 진짜 상신이 완료된다.

    돌려주는 값: 자동으로 '상신하겠습니까?'까지 눌러 끝냈으면 True,
    사람에게 넘겼으면 False (그때도 예외는 던지지 않는다).
    """
    submit_btn = dialog.get_by_text("승인요청", exact=True)
    await submit_btn.first.click(timeout=5000)
    await page.wait_for_timeout(800)
    await neis.dismiss_alert_popup(page, timeout=2000)
    await neis.dismiss_notice_popup(page, timeout=1000)
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
    #
    # ⚠️  팝업 글자를 읽지 않고 보이는 '확인'을 누르면 중복 신청·기간 오류 같은
    #     예상 밖 경고창까지 그대로 승인된다. 위 두 문구가 든 팝업만 누르고,
    #     그 밖의 팝업은 화면을 남긴 뒤 에러로 멈춘다.
    # 순서를 강제한다: ① 알림 → ② 상신하겠습니까.
    # 뒤로 돌아가거나 같은 단계를 두 번 누르지 않는다.
    # ①은 결재선 구성에 따라 안 뜰 수도 있어 건너뛰기는 허용하되,
    # ②를 누르기 전에 «정확히 그 문구»인지 전체 본문으로 확인한다.
    submitted = False
    step_ptr = 0
    for _ in range(4):
        popups = await visible_confirm_popups(page, timeout=5000)
        if not popups:
            break

        if dump_popup:
            for path in dump_popup_html("ok", popups):
                print(f"  📄  팝업 DOM 저장: {path}")

        async def _handover(why: str):
            await page.screenshot(path="bokmu_unexpected_popup.png")
            paths = dump_popup_html("unexpected", popups)
            seen = "\n    ---\n".join(
                f"[{a or '무제'}] {norm_text(x)}" for _, a, x, _ in popups)
            await pause_for_manual(
                f"{why} 화면을 보고 직접 눌러 주세요.",
                f"    기대한 확인창: {EXPECTED_DESC}\n"
                f"    실제 확인창 내용:\n    {seen}\n"
                f"    화면: bokmu_unexpected_popup.png\n"
                f"    팝업 DOM: {', '.join(paths)}",
            )

        # 메시지 상자가 둘 이상 겹쳐 있으면 어느 것이 지금 것인지 단정할 수 없다.
        if len(popups) != 1:
            await _handover(f"확인창이 {len(popups)}개 겹쳐 떠 있습니다.")
            return False

        btn, aria, text, _html = popups[0]
        step = match_popup_step(aria, text)
        if step is None:
            await _handover("예상과 다른 확인창입니다.")
            return False
        if step < step_ptr:
            await _handover("이미 지난 단계의 확인창이 다시 떴습니다.")
            return False

        # 상자 안에 확인 버튼이 정확히 하나인지 본다 (숨은 복제 버튼 방지)
        if await btn.count() != 1:
            await _handover(f"확인 버튼이 {await btn.count()}개입니다.")
            return False

        await btn.click(timeout=3000)
        step_ptr = step + 1
        if step == len(SUBMIT_POPUP_STEPS) - 1:
            submitted = True
        await page.wait_for_timeout(500)

    if not submitted:
        await page.screenshot(path="bokmu_submit_unconfirmed.png")
        await pause_for_manual(
            "'상신하겠습니까?' 확인창을 찾지 못했습니다. 화면을 보고 직접 마무리해 주세요.",
            "    화면: bokmu_submit_unconfirmed.png",
        )
        return False

    return True


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

        # 사람에게 넘겼거나 오류로 끝나면 1. «상신이 됐다» 고 확신할 때만 0 이다.
        # ⚠️ 1 은 «안 올라갔다» 가 아니라 «확인이 필요하다» 는 뜻이다.
        #    ② 「상신하겠습니까?」를 누른 뒤에 멈춰도 1 이 나온다 — 이미 올라갔을 수 있다.
        #    그러니 1 을 받았다고 자동으로 다시 돌리면 «두 번 상신» 이 된다. 재시도 금지.
        # ⚠️ 0 도 «② 를 눌렀다» 까지다. 서버가 받았는지는 나이스 목록에서 눈으로 본다.
        #    (2026-09-11 코덱스 교차검토 조건)
        exit_code = 0

        try:
            await page.goto(neis.NEIS_URL)
            await neis.auto_login(page)
            await page.wait_for_selector("text=학급담임", timeout=120_000)
            await page.wait_for_timeout(1000)
            print("✅  로그인 완료")

            # 로그인 직후 공지 팝업이 뜨면 그 뒤 클릭을 통째로 가로챈다.
            # 출결 쪽에서 「메뉴 이동 실패」로 겪은 것과 같은 물건이다 (2026-09-11).
            await neis.dismiss_notice_popup(page, timeout=2000)

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
                ok = await submit(
                    page, dialog,
                    approval_line=args.approval_line,
                    dump_popup=args.dump_popup,
                )
                if ok:
                    print("🎉  결재선 지정 + 상신 완료!")
                else:
                    exit_code = 1
                    print("⚠️  자동 상신을 끝내지 못해 사람에게 넘겼습니다.")
                    print("    나이스에서 상신 상태를 꼭 직접 확인하세요.")

            print("\n브라우저는 15초 후 자동으로 닫힙니다...")
            await page.wait_for_timeout(15_000)

        except Exception as e:
            import traceback
            print(f"\n❌  오류 발생: {e}")
            traceback.print_exc()
            exit_code = 1
            print("\n⚠️  브라우저는 열려 있습니다. 확인 후 Enter를 누르세요.")
            try:
                input("브라우저 닫으려면 Enter...")
            except EOFError:
                pass
        finally:
            await context.close()

        return exit_code


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
    parser.add_argument(
        "--dump-popup", action="store_true",
        help="상신 뒤 뜨는 확인창의 outerHTML 을 bokmu_popup_*.html 로 남긴다 (진단용)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)) or 0)


if __name__ == "__main__":
    main()
