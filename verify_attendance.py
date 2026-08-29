# -*- coding: utf-8 -*-
"""나이스 출결 검증 (읽기 전용).

캘린더 기준 기대값 vs 나이스 화면 실제값을 날짜별로 비교한다.
아무것도 입력·저장하지 않는다.

사용: python verify_attendance.py 2026-07-01 2026-07-31
"""
import asyncio, sys, json
from datetime import date, timedelta
from playwright.async_api import async_playwright
import main as M
import teacher_config

# 데이터 행의 children 인덱스 (헤더 12칸과 달리 데이터 행은 index 2에 빈 칸이 하나 더 있다 — 실측)
IDX = {"번호":0, "성명":1, "마감":3, "조회":4, "1교시":5, "2교시":6, "3교시":7,
       "4교시":8, "5교시":9, "6교시":10, "종례":11, "사유":12}
COLS = list(IDX)

JS_READ = """
() => {
  const out = [];
  document.querySelectorAll('.cl-grid-row').forEach(r => {
    const kids = Array.from(r.children).map(d => (d.innerText || '').trim());
    if (kids.length) out.push(kids);
  });
  return out;
}
"""

JS_HEADER = r"""
() => {
  const heads = [];
  document.querySelectorAll('[class*=grid-head], [class*=grid-column-head], .cl-grid-header').forEach(h => {
    const t = (h.innerText || '').trim();
    if (t) heads.push(t.split(String.fromCharCode(10)).map(x => x.trim()).filter(Boolean));
  });
  return heads;
}
"""

JS_DATEVAL = r"""
() => {
  const vals = [];
  document.querySelectorAll('input').forEach(i => {
    if (i.value && /^[0-9]{4}[.][0-9]{2}[.][0-9]{2}/.test(i.value)) vals.push(i.value);
  });
  return vals;
}
"""


async def read_grid(page):
    """그리드 전체를 번호 기준 dict로. 가상 스크롤 대비해 스크롤하며 여러 번 수집."""
    # 그리드가 그려질 때까지 대기 (조회 직후엔 비어 있음 → 0행으로 오판하던 원인)
    for _ in range(30):
        if await page.locator(".cl-grid-row").count() > 0:
            break
        await page.wait_for_timeout(500)
    await page.wait_for_timeout(600)
    rows = {}
    hdr_cache = {}

    async def collect():
        raw = await page.evaluate(JS_READ)
        # 그날 시간표 교시 수에 따라 열 개수가 달라진다(실측: 7/16·7/21은 1칸 적음).
        # → 헤더 행에서 실제 컬럼명을 읽어 매핑한다.
        header = None
        for kids in raw:
            if kids and kids[0].strip() == "번호":
                header = [k.split(chr(10))[0].strip() for k in kids]
                break
        if header:
            hdr_cache["names"] = header
        header = header or hdr_cache.get("names")

        for kids in raw:
            if not header or len(kids) != len(header) + 1:
                continue                      # 헤더/장식 행 (데이터 행은 index 2에 빈 칸이 하나 더 있음)
            cells = kids
            num = cells[0].strip()
            if not num.isdigit():
                continue
            named = {}
            for i, nm in enumerate(header):
                named[nm] = (cells[i] if i < 2 else cells[i + 1]).strip()
            if num in rows and len(rows[num]) >= len(named):
                continue
            rows[num] = named

    await collect()
    for _ in range(6):
        await page.mouse.wheel(0, 600)
        await page.wait_for_timeout(250)
        await collect()
    await page.keyboard.press("Home")
    await page.wait_for_timeout(200)
    return rows


def marks_of(named):
    """행에서 비어있지 않은 출결 표시만 뽑기 → {컬럼명: 값}"""
    return {k: v for k, v in named.items()
            if k not in ("번호", "성명") and v}


async def run(start, end):
    M.load_teacher_settings()
    recs = M.load_from_calendar(start, end)
    task_map = M.build_task_map(recs)
    holidays = M.load_holidays()

    dates = [d for d in task_map
             if start <= d <= end and d.weekday() < 5 and d not in holidays]
    print(f"검증 대상 {len(dates)}일: " + ", ".join(d.strftime('%m/%d') for d in dates))

    PROFILE_DIR = str(teacher_config.CONFIG_DIR / "chrome_profile")
    result = {}
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR, channel="chrome", headless=False, slow_mo=40,
            args=["--profile-directory=Default",
                  "--disable-blink-features=AutomationControlled",
                  "--disable-features=PrivateNetworkAccessForNavigations,PrivateNetworkAccessPermissionPrompt,PermissionChip",
                  "--disable-web-security", "--allow-running-insecure-content",
                  "--test-type", "--start-maximized", "--noerrdialogs"],
            ignore_default_args=["--enable-automation", "--no-sandbox"], viewport=None)
        await ctx.grant_permissions(["notifications"], origin=M._origin_of(M.NEIS_URL))
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        try:
            await page.goto(M.NEIS_URL)
            await M.auto_login(page)
            await M.wait_for_attendance_page(page)
            result["_header"] = await page.evaluate(JS_HEADER)

            for d in dates:
                await M.dismiss_alert_popup(page, timeout=800)
                await M.set_date_and_search(page, d)
                await page.wait_for_timeout(500)
                grid = await read_grid(page)
                dateval = await page.evaluate(JS_DATEVAL)
                actual = {num: marks_of(c) for num, c in grid.items() if marks_of(c)}
                result[d.isoformat()] = {
                    "rows_read": len(grid),
                    "date_on_screen": dateval,
                    "raw": grid,
                    "actual": actual,
                    "expected": [
                        {"number": t["number"], "name": t["name"],
                         "gubun": t["gubun"], "jongryu": t["jongryu"], "note": t["note"]}
                        for t in task_map[d]],
                }
                print(f"  {d.strftime('%m/%d')} 읽음: {len(grid)}행, 표시된 학생 {len(actual)}명, 화면날짜={dateval}")
        finally:
            await ctx.close()

    with open("verify_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("\nSAVED verify_result.json")


if __name__ == "__main__":
    s = date.fromisoformat(sys.argv[1]); e = date.fromisoformat(sys.argv[2])
    asyncio.run(run(s, e))
