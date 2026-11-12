# -*- coding: utf-8 -*-
"""K-에듀파인 «기안기» 창 (WXSClient.exe) — 품의 [결재요청] 뒤에 뜨는 창을 채우고 [결재올림] 까지.

품의 화면에서 [결재요청] → «결재요청 하시겠습니까?» [확인] 을 누르면 브라우저가 아니라
컴퓨터에 깔린 에듀파인 기안기 창(«문서관리카드기안»)이 뜬다. 여기서 필수 세 칸을 채워야 실제로 올라간다.
  * 과제카드   — 비어서 온다
  * 대국민공개여부 — 아무것도 안 골라져서 온다
  * 결재경로   — 기안자 한 줄뿐이다 → «나의결재선» 에 저장해 둔 이름으로 고른다

순서 (2026-09-21 서울 중학교 실측, 연습 품의로 끝까지 올려 봄):
  [결재올림] → «수신자를 지정하지 않으면 내부결재로 처리됩니다» [확인]
            → 「결재올림」 창(IE 대화상자) [확인]      ← 여기부터 되돌릴 수 없다. [결재올림] 을 다시 누르지 않는다
            → «원문공개 문서는 바로 국민에게 공개됩니다» [확인] (공개일 때)
            → «정상적으로 처리되었습니다» [확인] → 기안기가 스스로 닫힌다
모르는 창·문구가 뜨면 누르지 않고 멈춘다 (창도 닫지 않는다 — 선생님이 이어서 손으로 할 수 있게).
"""
import ctypes
import ctypes.wintypes as W
import re
import sys
import time

sys.coinit_flags = 2   # STA — 기안기 안의 IE 문서(COM)는 STA 에서만 잡힌다. pywinauto 가 먼저 MTA 로 잡으면 «해당 인터페이스를 지원하지 않습니다» (2026-11-12)

import edufine_common as E

try:
    import win32api
    import win32clipboard
    import win32con
    import win32gui
    import win32process
    from pywinauto import Desktop, keyboard, mouse
except ImportError:                                  # 예전에 설치한 폴더에는 없다
    raise SystemExit(
        "기안기 창을 다루는 부품(pywinauto)이 아직 안 깔려 있습니다. 한 번만 깔아 주세요:\n"
        "  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt")

import edufine_quiet as Q                             # 2026-11-12 «조용한 입력» — 진짜 마우스·키보드 0, 창도 앞으로 안 꺼냄

IE_DLG = "Internet Explorer_TridentDlgFrame"
MSG_TITLE = "웹 페이지 메시지"
DISCLOSURE = {"공개": "othbcLmtt1", "비공개": "othbcLmtt3"}    # 부분공개는 제한근거를 골라야 해서 자동으로 안 한다


class Stop(Exception):
    """누르지 않고 멈춘다 — 기안기 창은 그대로 둔다."""


# ── 창 찾기 ─────────────────────────────────────────────────────
def wxs_pids():
    out = set()
    def f(h, _):
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
            hp = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            buf = ctypes.create_unicode_buffer(260)
            n = W.DWORD(260)
            if hp and ctypes.windll.kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(n)):
                if buf.value.lower().endswith("wxsclient.exe"):
                    out.add(pid)
            if hp:
                ctypes.windll.kernel32.CloseHandle(hp)
        except Exception:
            pass
    win32gui.EnumWindows(f, None)
    return out


def windows(cls=None, title=None, pids=None):
    out = []
    def f(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        if cls and win32gui.GetClassName(h) != cls:
            return
        if title and title not in win32gui.GetWindowText(h):
            return
        if pids is not None and win32process.GetWindowThreadProcessId(h)[1] not in pids:
            return
        out.append(h)
    win32gui.EnumWindows(f, None)
    return out


def gian_hwnd():
    hs = windows("#32770", "기안기", wxs_pids())
    return hs[-1] if hs else None


def wait_gian(timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        h = gian_hwnd()
        if h:
            return h
        time.sleep(0.5)
    return None


def gian_window(h):
    return Desktop(backend="uia").window(handle=h)


# ── 누르기·붙여넣기 ──────────────────────────────────────────────
def focus(h):
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)       # Alt 를 한 번 눌러야 SetForegroundWindow 가 먹는다
    try:
        win32gui.SetForegroundWindow(h)
    except Exception:
        pass
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)


def center(ctrl):
    r = ctrl.rectangle()
    return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)


def rel_click(h, x, y, wait=0.4):
    left, top, _, _ = win32gui.GetWindowRect(h)
    mouse.click(coords=(left + x, top + y))
    time.sleep(wait)


def paste(text):
    """한글은 type_keys 로 못 친다 — 클립보드로 붙인다."""
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()
    keyboard.send_keys("^a^v")
    time.sleep(0.3)


def msg_text(h):
    parts = []
    win32gui.EnumChildWindows(
        h, lambda c, _: parts.append(win32gui.GetWindowText(c)) if win32gui.GetClassName(c) == "Static" else None, None)
    return " ".join(p for p in parts if p).replace("\n", " ")


def press_msg(h, button="확인"):
    focus(h)
    b = win32gui.FindWindowEx(h, 0, "Button", button)
    if not b:
        raise Stop("알림창에 [%s] 단추가 없습니다: %s" % (button, msg_text(h)))
    left, top, right, bottom = win32gui.GetWindowRect(b)
    mouse.click(coords=((left + right) // 2, (top + bottom) // 2))
    time.sleep(0.6)


def shot(h, name):
    try:
        from PIL import ImageGrab
        E.OUT_DIR.mkdir(parents=True, exist_ok=True)
        p = E.OUT_DIR / name
        ImageGrab.grab(bbox=win32gui.GetWindowRect(h), all_screens=True).save(p)
        E.log("  📸 캡처: %s" % p)
    except Exception:
        pass


def norm(s):
    return re.sub(r"\s+", "", s or "")


# ── 칸 채우기 ────────────────────────────────────────────────────
def card_text(w):
    """과제카드 칸에 들어간 글자."""
    for c in w.descendants(control_type="Text"):
        t = c.window_text() or ""
        if t.startswith("[") and "]" in t and "(" in t:
            r = c.rectangle()
            if 140 < r.left < 700:
                return t
    return ""


def card_rows(pop):
    """과제카드 창 결과 줄 — [(글자, 화면 y)]. 못 읽으면 None, «조회 결과가 없습니다» 면 []."""
    try:
        d = Desktop(backend="uia").window(handle=pop)
        _, top, _, _ = win32gui.GetWindowRect(pop)
        out, empty = [], False
        for c in d.descendants(control_type="Text"):
            t = (c.window_text() or "").strip()
            r = c.rectangle()
            if not (270 <= r.top - top <= 515):
                continue
            if "조회 결과가 없습니다" in t:
                empty = True
            elif t.startswith("[") and "]" in t:
                out.append((t, (r.top + r.bottom) // 2))
        return out if (out or empty) else None
    except Exception:
        return None


def close_card_popup(pop):
    try:
        for b in Desktop(backend="uia").window(handle=pop).descendants(control_type="Button"):
            aid = b.element_info.automation_id or ""
            if aid.endswith("form.btnOk") or aid.endswith("btnClose00"):
                mouse.click(coords=center(b))
                time.sleep(0.8)
    except Exception:
        pass


# ── 조용한 입력 (2026-11-12) ──────────────────────────────────────
# 예전 판은 좌표 클릭·↓ 키·클립보드 붙여넣기로 채워서 그동안 선생님이 컴퓨터를 못 썼다.
# 지금은 기안기 안의 IE 문서를 COM 으로 잡아 페이지 함수를 부른다 (edufine_quiet.py 맨 위 설명).
#   과제카드 = nexacro API · 공개여부 = 라디오 click() · 결재선 = select 값 · 알림창 = WM_COMMAND
# 기안기 창은 «본문 컨트롤이 만들어질 때까지» 맨 뒤에 펼쳐 두고(sink) 그다음 최소화(tuck)한다.
def doc(h):
    return Q.ready(h)


def set_card(h, card):
    d = doc(h)
    m = re.match(r"\s*\[([^\]]+)\]\s*(.*)", card)
    query = m.group(1) if m else card
    try:
        got = Q.choose_card(d, query) if not m else _choose_exact(d, query, card)
    except SystemExit as ex:
        raise Stop(str(ex))
    if not got:
        raise Stop("과제카드가 '%s' 로 안 들어갔습니다. 이름을 에듀파인 목록 글자 그대로 적어 주세요" % card)
    return got


def _choose_exact(d, query, want):
    """«[단위과제] 카드명» 꼴 — 검색은 대괄호 안, 줄은 전체 글자가 맞는 것."""
    got = Q.choose_card(d, query)
    if norm(want) not in norm(got):
        raise Stop("과제카드가 '%s' 가 아니라 '%s' 로 잡혔습니다" % (want, got))
    return got


def set_partial(h, what, reason=""):
    """부분공개(6) · 비공개(5) 처럼 «호» 가 붙은 값. 정보공개법 제9조 제1항 — 5호 의사결정 과정 · 6호 개인정보 · 7호 영업비밀."""
    m = re.fullmatch(r"(부분공개|비공개)\((\d)\)", norm(what))
    if not m:
        raise Stop("공개여부 '%s' 를 모르겠습니다 — 공개 · 비공개 · 부분공개(6) · 비공개(5) 꼴로 적어 주세요" % what)
    kind, ho = m.groups()
    rid = {"부분공개": "othbcLmtt2", "비공개": "othbcLmtt3"}[kind]
    d = doc(h)
    r = Q.js(d, """var r=document.getElementById('%s'); r.click();
        var b=document.getElementById('othbcAt%s'); if(!b)return 'NOBOX'; if(!b.checked)b.click();
        var e=document.getElementById('othbcLmttResn'); if(e && %s){e.value=%s; var ev=document.createEvent('HTMLEvents'); ev.initEvent('change',true,true); e.dispatchEvent(ev);}
        return r.checked+'|'+b.checked;""" % (rid, ho, "true" if reason else "false", Q.json.dumps(reason)))
    if r == "NOBOX":
        raise Stop("공개제한근거 %s호 칸을 못 찾았습니다" % ho)
    if r != "true|true":
        raise Stop("공개여부 '%s' 가 안 골라졌습니다 (%s)" % (what, r))


def set_disclosure(h, what, reason=""):
    if "(" in what:
        return set_partial(h, what, reason)
    if what not in DISCLOSURE:
        raise Stop("공개여부 '%s' 는 자동으로 고르지 않습니다 (공개·비공개·부분공개(6) 꼴)" % what)
    r = Q.js(doc(h), "var r=document.getElementById('%s'); r.click(); return r.checked;" % DISCLOSURE[what])
    if r != "true":
        raise Stop("공개여부가 '%s' 로 안 골라졌습니다" % what)


def preset_value(w):
    """(예전 판 호환 — edufine_결재선만들기.py 가 쓴다) UIA 로 «나의결재선» 콤보와 지금 값."""
    cb = [c for c in w.descendants(control_type="ComboBox") if c.element_info.automation_id == "streReportCours"]
    if not cb:
        raise Stop("나의결재선 칸을 못 찾았습니다")
    return cb[0], (cb[0].legacy_properties().get("Value") or "")


def preset_names(h):
    return Q.js(doc(h), "var s=document.getElementById('streReportCours'); var o=[]; for(var i=0;i<s.options.length;i++)o.push(s.options[i].text); return o.join('|');").split("|")


def absent_notice():
    """결재선에 부재중인 결재자(출장 등)가 있으면 «…부재중입니다» 알림이 저절로 뜬다. 그 글자 (없으면 None).
    그때 함께 뜨는 «결재경로설정» 창은 건드리지 않는다 — 그 창의 [삭제] 는 저장된 결재선을 지운다."""
    for m in windows("#32770", MSG_TITLE, wxs_pids()):
        t = msg_text(m)
        if "부재" in t:
            return t
    return None


def set_preset(h, name):
    d = doc(h)
    names = preset_names(h)
    if not any(norm(n) == norm(name) for n in names):
        raise Stop("나의결재선에 '%s' 가 없습니다. 있는 것: %s" % (name, " / ".join(n for n in names if n)))
    exact = next(n for n in names if norm(n) == norm(name))
    try:
        Q.set_preset(d, exact)
    except SystemExit as ex:
        raise Stop(str(ex))
    for _ in range(10):
        time.sleep(0.5)
        t = absent_notice()
        if t:
            raise Stop("고른 결재선에 부재중인 결재자가 있습니다: %s — 기안 창에서 확인해 주세요" % t[:120])
        if any(Q.field(d, "직위.%d" % i).strip() for i in range(2, 5)):
            return


def route_rows(h):
    """결재란(문서 누름틀)의 직위 — [순번, 직위]. 창을 최소화해도 읽힌다 (예전 판은 화면 좌표로 표를 읽었다)."""
    d = doc(h)
    out = []
    for i in range(1, 5):
        p = Q.field(d, "직위.%d" % i).strip()
        if p:
            out.append([str(i), "결재", p, "", ""])
    if out:
        return out
    # 본문 편집기가 없는 창이면 결재경로 표(«순번»이 머리인 표)의 줄을 DOM 에서 읽는다
    txt = Q.js(d, """var t=document.getElementsByTagName('table'); var o=[];
        for(var i=0;i<t.length;i++){var h=t[i].innerText||''; if(h.indexOf('순번')<0||h.indexOf('처리방법')<0)continue;
          var r=t[i].rows; for(var j=0;j<r.length;j++){var c=[]; for(var k=0;k<r[j].cells.length;k++)c.push((r[j].cells[k].innerText||'').replace(/\\s+/g,' ').trim());
            if(c.length>=4 && /^\\d+$/.test(c[0]))o.push(c.join('\\t'));}
          if(o.length)break;}
        return o.join('|');""")
    return [row.split("\t") for row in txt.split("|") if row]


# ── 결재올림 ─────────────────────────────────────────────────────
def submit(h, timeout=90):
    """⚠️ 2026-11-12 조용한 입력으로 바꾼 뒤 실제 결재올림으로는 아직 안 돌려 봤다 — 첫 실행은 지켜볼 것."""
    d = doc(h)
    pids = wxs_pids()
    seen = set(windows(IE_DLG, None, pids))
    up = "setTimeout(function(){fncSanctn()},10); return 1"
    Q.js(d, up)
    done = set()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        time.sleep(0.3)
        for m in windows("#32770", MSG_TITLE, pids):
            txt = msg_text(m)
            E.log("  알림: %s" % txt[:90])
            if "수신자를 지정하지 않으면 내부결재로 처리" in txt and "내부결재" not in done:
                done.add("내부결재"); Q.press(m); break
            if "원문공개 문서는 바로 국민에게 공개" in txt and "원문공개" not in done:
                done.add("원문공개"); Q.press(m); break
            if "정상적으로 처리되었습니다" in txt:
                Q.press(m)
                for _ in range(30):                         # 기안기가 스스로 닫힌다
                    if not win32gui.IsWindow(h):
                        break
                    time.sleep(0.3)
                return True
            raise Stop("모르는 알림이라 누르지 않았습니다: %s" % txt[:150])
        for dlg in windows(IE_DLG, None, pids):
            if dlg in seen:
                continue
            t = win32gui.GetWindowText(dlg)
            if "관인" in t and "관인" not in done:
                time.sleep(1.5)
                if Q.click_text_in(Q.doc_of(dlg), "확인") != "OK":
                    raise Stop("관인선택 창에서 [확인] 을 못 찾았습니다 — 손으로 눌러 주세요")
                done.add("관인"); seen.add(dlg)
                time.sleep(2)
                Q.js(d, up)                                 # 관인을 고른 뒤 한 번 더 눌러야 다음 창
                continue
            if "문서처리" in t and "문서처리" not in done:
                time.sleep(1.5)
                if Q.click_text_in(Q.doc_of(dlg), "확인") != "OK":
                    raise Stop("「결재올림」 창에서 [확인] 을 못 찾았습니다 — 손으로 눌러 주세요")
                done.add("문서처리"); seen.add(dlg)        # 이 뒤로 결재올림 재클릭 금지
                E.log("  「결재올림」 창 [확인]")
                continue
            raise Stop("모르는 창이라 누르지 않았습니다: %s" % t)
    if "문서처리" in done:
        raise Stop("올리긴 했는데 %d초 안에 «정상적으로 처리» 를 못 봤습니다. "
                   "다시 누르지 말고 품의목록 상태를 봐 주세요 (두 번 올라갈 수 있습니다)" % timeout)
    raise Stop("%d초 동안 «결재올림» 창이 안 떴습니다 — 아직 안 올라갔습니다" % timeout)


def fill_and_submit(plan, wait_timeout=60):
    """기안기가 뜨기를 기다려 과제카드·공개여부·결재선을 채우고 [결재올림]. 결과 dict 를 돌려준다."""
    prev = win32gui.GetForegroundWindow()           # 선생님이 쓰던 창 — 기안기가 앞창을 가져가면 돌려준다
    h = wait_gian(wait_timeout)
    if not h:
        raise Stop("기안기 창이 %d초 안에 안 떴습니다 (기안기 프로그램 Kedufine 이 깔려 있는지 보세요)" % wait_timeout)
    E.log("기안기 창이 떴습니다 — 과제카드·공개여부·결재선을 채웁니다 (마우스·키보드는 그대로 쓰셔도 됩니다)")
    Q.sink(h)                                       # 맨 뒤로 — 본문 컨트롤이 준비될 때까지는 펼쳐 둔다
    try:
        Q.ready(h)
    except SystemExit as ex:
        raise Stop(str(ex))
    Q.tuck(h)                                       # 준비됐으면 작업표시줄로
    if prev != h:
        Q.give_back(prev)
    got = set_card(h, plan["과제카드"])
    E.log("  과제카드: %s" % got)
    set_disclosure(h, plan["공개여부"])
    E.log("  공개여부: %s" % plan["공개여부"])
    set_preset(h, plan["결재선"])
    rows = route_rows(h)
    for r in rows:
        E.log("  결재경로: %s" % " | ".join(r[:3]))
    if len(rows) < 2:
        raise Stop("결재경로에 기안자 말고는 아무도 없습니다 — '%s' 결재선을 확인해 주세요" % plan["결재선"])
    E.log("\n⚠️  [결재올림] 을 누릅니다")
    submit(h)
    return {"결재경로": [" ".join(r[:3]) for r in rows]}
