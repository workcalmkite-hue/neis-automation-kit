# -*- coding: utf-8 -*-
"""K-에듀파인 기안기 «나의결재선» 에 저장된 결재선을 전부 읽어 온다 (읽기만).

  .\\.venv\\Scripts\\python.exe -X utf8 edufine_결재선목록.py

하는 일: 문서관리 > 기안 > 공용서식으로 빈 기안 창을 하나 연다 → «나의결재선» 목록을 기안 창 문서에서 바로 읽고
         하나씩 골라 그때 바뀌는 결재경로(직위)를 읽는다 → 기안 창을 «저장하지 않고» 닫는다.
★ 2026-11-12부터 진짜 마우스·키보드를 쓰지 않는다 (edufine_quiet.py). 기안 창은 작업표시줄에 내려 둔 채로 읽는다.
★ 아무것도 저장·상신하지 않는다. 제목·본문도 비워 둔 채 닫는다.
★ 모르는 창·문구가 뜨면 누르지 않고 멈춘다.
결과: 이 폴더 «나의결재선.json» + 마지막 줄 RESULT_JSON=
품의·공문의 «결재선» 칸에는 여기 나온 이름을 글자 그대로 쓴다.
"""
import json
import sys
import time
from pathlib import Path

import edufine_common as E
import edufine_gian as G
from edufine_gian import Stop, win32con, win32gui
import edufine_quiet as Q

HERE = Path(__file__).resolve().parent
OUT = HERE / "나의결재선.json"
FORM = "일반기안문 서식(결재4인,협조4인)"     # 공용서식 중 아무거나 — 기안 창만 열면 된다


def form_hwnds():
    return G.windows("#32770", FORM, G.wxs_pids())


def open_form():
    """문서관리 > 기안 > 공용서식에서 서식을 눌러 빈 기안 창을 연다 (브라우저는 여기까지만)."""
    from playwright.sync_api import sync_playwright
    if form_hwnds():
        raise Stop("기안 창이 이미 열려 있습니다. 그 창을 닫고 다시 돌려 주세요")
    name, pw = E.creds()
    with sync_playwright() as p:
        ctx, page = E.launch(p)
        try:
            E.login(page, name, pw)
            E.log("📂 K-에듀파인 → 문서관리 → 기안 → 공용서식")
            page.goto(E.KLEF, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            for _ in range(3):
                E.close_modals(page)
                page.wait_for_timeout(800)
            page.locator('div[id$="btnMenu_A00AAB01:icontext"]').first.click()        # 상단 «문서관리»
            page.wait_for_timeout(1500)
            b = page.locator('div[id$="grdMega_A00AAB0101.body.gridrow_0.cell_0_0"]').first.bounding_box()
            page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
            f = None
            for _ in range(30):
                f = next((x for x in page.frames if "retrieveCmmnFormatList" in x.url), None)
                if f:
                    break
                page.wait_for_timeout(500)
            if not f:
                raise Stop("공용서식 목록이 15초 안에 안 떴습니다")
            f.get_by_text(FORM, exact=True).first.click()                              # 한 번만 — 두 번이면 창이 두 개
            E.log("  서식을 눌렀습니다 — 기안 창(에듀파인 기안기)을 기다립니다")
            for _ in range(60):
                page.wait_for_timeout(500)
                if form_hwnds():
                    break
            else:
                raise Stop("기안 창이 30초 안에 안 떴습니다 (에듀파인 통합설치가 돼 있는지 보세요)")
        finally:
            ctx.close()                  # 브라우저를 닫아도 기안 창은 살아 있다


def clear_autosave(wait):
    """«자동저장문서가 존재합니다 … 읽어오시겠습니까?» → [취소]. 그 밖의 물음이면 멈춘다."""
    end = time.monotonic() + wait
    while time.monotonic() < end:
        for m in G.windows("#32770", G.MSG_TITLE, G.wxs_pids()):
            t = G.msg_text(m)
            if "열려있는 기안" in t:          # 지난번에 덜 닫힌 기안기가 남아 있으면 묻는다 → [확인] 으로 그걸 닫는다 (2026-10-14)
                Q.press(m, "확인")
                time.sleep(2.0)
                continue
            if "자동저장문서" not in t:
                raise Stop("기안 창이 모르는 것을 묻습니다: %s" % t[:150])
            Q.press(m, "취소")
            time.sleep(1.5)
            return
        time.sleep(0.5)


def handle_absent():
    """결재선에 부재중인 결재자가 있으면 에듀파인이 «결재경로설정» 창과 «…사용자가 현재 부재중입니다» 알림을
    저절로 띄운다 (2026-10-14 실측 — 교감 부재). 알림은 [확인], 결재경로설정 창은 저장 없이 닫는다.
    ★ 그 창의 [삭제] 는 저장된 나의결재선을 지우는 단추라 절대 누르지 않는다. 부재중 문구를 돌려준다.
    ⚠️ 이렇게 닫으면 기안 창 결재경로 표가 앞 결재선 그대로일 수 있다 → 부재자가 든 결재선의 «결재경로» 는 믿지 말 것
       (이름 목록은 정확). 정확한 경로는 edufine_결재선만들기.py "이름" --확인 으로 본다."""
    notes = []
    for _ in range(6):
        time.sleep(0.5)
        msgs = [m for m in G.windows("#32770", G.MSG_TITLE, G.wxs_pids()) if "부재" in G.msg_text(m)]
        for m in msgs:
            notes.append(G.msg_text(m)[:120])
            Q.press(m, "확인")
        for d in G.windows(G.IE_DLG, "결재경로설정", G.wxs_pids()):
            win32gui.PostMessage(d, win32con.WM_CLOSE, 0, 0)
            time.sleep(1.0)
        if notes and not G.windows(G.IE_DLG, "결재경로설정", G.wxs_pids()):
            break
    return notes


def read_presets(h):
    d = Q.ready(h)
    names = [n for n in G.preset_names(h) if n.strip() and n.strip() != "[현재 결재선]"]   # «[현재 결재선]» 은 빈 기본값
    found = []
    for name in names:
        Q.set_preset(d, name)
        time.sleep(1.5)                                             # 결재경로가 다시 채워질 시간
        absent = handle_absent()
        rows = G.route_rows(h)
        found.append({"이름": name.strip(), "결재경로": [" ".join(x[:4]) for x in rows], "부재중": absent})
        E.log("  · %s%s" % (name.strip(), "   ⚠️ 부재중인 결재자 있음" if absent else ""))
        for x in rows:
            E.log("      %s" % " | ".join(x[:4]))
    return found


def close_without_save(h):
    """기안 창 닫기 → 저장 여부를 물으면 [아니요]. 그 밖의 물음이면 멈춘다."""
    before = set(G.windows("#32770", G.MSG_TITLE, G.wxs_pids()))
    win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
    end = time.monotonic() + 15
    while time.monotonic() < end:
        if not win32gui.IsWindow(h) or not win32gui.IsWindowVisible(h):
            return True
        for m in G.windows("#32770", G.MSG_TITLE, G.wxs_pids()):
            if m in before:
                continue
            t = G.msg_text(m)
            if "저장" in t or "종료" in t or "닫" in t:
                E.log("  (닫을 때 묻습니다: %s)" % t[:80].replace("\n", " "))
                for b in ("아니요", "아니오", "예"):                  # 저장 안 함 우선 · «종료하시겠습니까» 면 [예]
                    if b == "예" and "저장" in t:
                        continue
                    try:
                        Q.press(m, b)
                        break
                    except (Exception, SystemExit):                   # 그 이름의 단추가 없으면 다음 이름
                        continue
                before.add(m)
            else:
                raise Stop("기안 창을 닫을 때 모르는 것을 묻습니다: %s" % t[:150])
        time.sleep(0.4)
    return not win32gui.IsWindow(h)


def main():
    prev = win32gui.GetForegroundWindow()           # 선생님이 쓰던 창 — 기안 창이 앞창을 가져가면 돌려준다
    open_form()
    clear_autosave(10)                   # 지난 기안기·자동저장본 물음부터 정리 — 그래야 창이 하나로 남는다
    hs = form_hwnds()
    if len(hs) != 1:
        raise Stop("기안 창이 %d개입니다 — 하나만 남기고 다시 돌려 주세요" % len(hs))
    h = hs[0]
    Q.sink(h)                            # 맨 뒤로 (활성화 X) — 문서가 준비되면 작업표시줄로
    Q.ready(h)
    Q.tuck(h)
    clear_autosave(3)
    Q.give_back(prev)
    E.log("📋 나의결재선 읽는 중 (저장·상신하지 않습니다 · 마우스·키보드는 그대로 쓰셔도 됩니다)")
    found = read_presets(h)
    handle_absent()                      # 남은 결재경로설정 창이 있으면 먼저 닫는다 — 기안 창만 닫고 이게 남았었다 (10/14)
    closed = close_without_save(h)
    OUT.write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding="utf-8")
    E.log("\n✅ 결재선 %d개를 읽었습니다 → %s" % (len(found), OUT.name))
    if not closed:
        E.log("⚠️ 기안 창이 아직 열려 있습니다. 저장하지 말고 닫아 주세요.")
    print("RESULT_JSON=" + json.dumps({"할일": "결재선목록", "개수": len(found), "파일": OUT.name,
                                      "기안창닫힘": closed}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Stop as e:
        E.log("⛔ %s" % e)
        print("RESULT_JSON=" + json.dumps({"할일": "결재선목록", "멈춤": str(e)[:200]}, ensure_ascii=False))
        sys.exit(1)
