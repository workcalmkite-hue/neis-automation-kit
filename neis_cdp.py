# -*- coding: utf-8 -*-
"""neis_session.py 로 열어 둔 나이스 크롬에 붙어 스니펫 파일을 실행한다.

  python neis_cdp.py 스니펫.py

스니펫 안에서는 `page`(나이스 탭) · `ctx` · `asyncio` 를 바로 쓴다.
최상위 `await` 를 쓸 수 있고, `return` 으로 중간에 끝낼 수 있다.
클릭 → 확인 → 클릭을 한 스니펫에 묶으면 왕복이 줄어든다.
"""
import asyncio
import os
import sys
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

PORT = int(os.environ.get("NEIS_CDP_PORT", "9222"))


def neis_host():
    """설정의 나이스 주소 호스트 (예: sen.neis.go.kr). 못 읽으면 빈 문자열."""
    try:
        import teacher_config
        return (urlsplit(teacher_config.load_config()["neis_url"]).hostname or "").lower()
    except Exception:
        return ""


def _host_of(page):
    try:
        return (urlsplit(page.url).hostname or "").lower()
    except ValueError:
        return ""


def pick_neis_page(pages, host):
    """설정 주소와 호스트가 같은 탭 → 없으면 호스트가 `.neis.go.kr` 로 «끝나는» 탭. 아무 탭이나 고르지 않는다.

    «들어 있다»로 보면 `x.neis.go.kr.evil.example` 같은 주소도 통과하므로 끝부분으로 본다.
    """
    same = [pg for pg in pages if host and _host_of(pg) == host]
    neis = [pg for pg in pages if _host_of(pg).endswith(".neis.go.kr")]
    return (same or neis or [None])[0]


async def run(path):
    src = open(path, encoding="utf-8").read()
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        except Exception as e:
            sys.exit(f"❌  나이스 크롬에 붙지 못했습니다 (포트 {PORT}).\n"
                     "   neis_session.py 가 실행 중이고 READY 를 찍었는지 확인하세요.\n"
                     f"   ({str(e).splitlines()[0][:120]})")
        ctx = browser.contexts[0]
        page = pick_neis_page(ctx.pages, neis_host())
        if page is None:
            sys.exit(f"❌  포트 {PORT} 의 크롬에 나이스 탭이 없습니다 — neis_session.py 를 다시 실행하세요.\n"
                     "   (다른 프로그램이 같은 포트를 쓰고 있을 수도 있습니다)")
        ns = {"page": page, "ctx": ctx, "asyncio": asyncio}
        body = "\n".join("    " + line for line in src.splitlines()) or "    pass"
        exec(compile("async def __snippet(page, ctx):\n" + body, path, "exec"), ns)
        await ns["__snippet"](page, ctx)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
    if len(sys.argv) != 2:
        sys.exit("사용: python neis_cdp.py 스니펫.py")
    asyncio.run(run(sys.argv[1]))
