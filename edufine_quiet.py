# -*- coding: utf-8 -*-
"""에듀파인 기안기 창(품의 결재요청·공문 기안)을 «조용히» 다루는 도구 — 진짜 마우스·키보드를 쓰지 않고, 창도 앞으로 꺼내지 않는다 (2026-11-12 실측).

원리
- 기안 창은 IE(Trident) 기반이다. 창 안의 «Internet Explorer_Server» 에 WM_HTML_GETOBJECT 를 보내면
  그 페이지 문서(DOM)를 COM 으로 받을 수 있다 → 페이지 안에서 JS 를 돌려 값 넣기·버튼 함수 부르기
- 본문은 한글 컨트롤(HwpCtrl ActiveX)이다 → PutFieldText('본문', …) 로 누름틀에 바로 넣는다 (붙여넣기 불필요)
- 과제카드 팝업은 nexacro 앱 → nexacro API(dsSrchParam·fnSearch·fnSelectUnitTkcrd·확인 핸들러)로
- «웹 페이지 메시지»(alert)·파일 열기 창은 WM_SETTEXT / WM_COMMAND 를 창에 «보내기»만 한다
- 팝업을 여는 함수는 showModalDialog 라 JS 를 그 자리에서 부르면 이쪽이 멈춘다 → setTimeout 으로 띄운다
"""
import ctypes, json, os, time
import pythoncom, win32com.client, win32con, win32gui

FORM = "일반기안문 서식(결재4인,협조4인)"
DLG = "Internet Explorer_TridentDlgFrame"
WM_HTML = win32gui.RegisterWindowMessage("WM_HTML_GETOBJECT")


def _visible(cls=None, title=None, exact=False):
    out = []
    def f(h, _):
        if not win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h):
            return
        if cls and win32gui.GetClassName(h) != cls:
            return
        t = win32gui.GetWindowText(h)
        if title and ((t != title) if exact else (title not in t)):
            return
        out.append(h)
    win32gui.EnumWindows(f, None)
    return out


def doc_of(top):
    ss = []
    win32gui.EnumChildWindows(top, lambda c, _: ss.append(c) if win32gui.GetClassName(c) == "Internet Explorer_Server" else True, None)
    if not ss:
        raise SystemExit("IE 문서를 못 찾음: %s" % win32gui.GetWindowText(top))
    res = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(ss[0], WM_HTML, 0, 0, 2, 5000, ctypes.byref(res))
    return win32com.client.Dispatch(pythoncom.ObjectFromLresult(res.value, pythoncom.IID_IDispatch, 0))


def js(doc, code):
    """페이지 안에서 JS 를 돌리고 return 값을 문자열로 받는다. 오류는 'ERR:…'"""
    # new Function 으로 감싸 «문법 오류»도 try 가 잡게 한다 — 그냥 넣으면 IE «스크립트 오류» 창이 떠서
    # 페이지가 통째로 멈춘다 (2026-11-12 실측)
    doc.parentWindow.execScript(
        "try{document.body.setAttribute('data-cc',String((new Function(%s))()))}"
        "catch(e){document.body.setAttribute('data-cc','ERR:'+e.message)}" % json.dumps(code), "JavaScript")
    r = doc.body.getAttribute("data-cc")
    if r and r.startswith("ERR:"):
        raise SystemExit("기안 창 JS 오류: " + r)
    return r


def form():
    ws = _visible(title=FORM)
    if len(ws) != 1:
        raise SystemExit("기안 창이 %d개다 — 하나만 남기고 다시" % len(ws))
    return ws[0]


def ready(h, timeout=60):
    """기안 창이 뜬 직후엔 문서가 아직 없다 → 제목칸과 한글 컨트롤 누름틀이 잡힐 때까지 기다렸다가 문서를 돌려준다"""
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        try:
            d = doc_of(h)
            # 품의 «문서관리카드기안» 창에 본문 편집기가 없을 수도 있다 (실측 전) → 편집기가 «있을 때만» 준비를 기다린다
            r = js(d, "if(document.readyState!='complete'||!document.getElementById('streReportCours'))return 'wait';"
                      "var f=document.getElementById('editor1'); if(!f)return 'ok';"
                      "var h=f.contentWindow.document.getElementById('editor1'); if(!h)return 'wait';"
                      "return String(h.GetFieldList(0,0)).length>0?'ok':'wait';")
            if r == "ok":
                # «자동저장문서를 읽어오시겠습니까?» 는 문서가 다 뜬 «뒤»에 늦게 뜬다 (2026-11-12 실측, 3~5초).
                # 이 창이 떠 있는 동안은 페이지 타이머가 멈춰 팝업이 안 열린다 → 잠깐 지켜보다 뜨면 [취소](새로 씀)
                for _ in range(12):
                    time.sleep(0.5)
                    clear_autosave()
                return d
        except (Exception, SystemExit) as ex:
            last = str(ex)[:80]
        time.sleep(1)
    raise SystemExit("기안 창 문서가 %d초 안에 준비되지 않았다: %s" % (timeout, last))


def sink(h):
    """창을 «다른 창들 맨 뒤»로 보낸다 (펼친 채, 활성화 X).
    ⚠️ 기안 창은 한 번도 펼쳐진 적 없이 최소화되면 한글 컨트롤(본문)이 안 만들어져 «해당 인터페이스를 지원하지 않습니다»가 난다
    (2026-11-12 실측) → 문서가 준비될 때까지는 뒤로만 보내고, ready() 다음에 tuck()"""
    F = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
    if win32gui.IsIconic(h):
        win32gui.ShowWindow(h, 4)               # SW_SHOWNOACTIVATE
    win32gui.SetWindowPos(h, win32con.HWND_BOTTOM, 0, 0, 0, 0, F)


def clear_autosave():
    """«자동저장문서를 읽어오시겠습니까?» → [취소] (새로 쓴다). 누른 게 있으면 True"""
    hit = False
    for h, t in msg_boxes():
        if "자동저장문서" in t:
            press(h, "취소")
            hit = True
    return hit


def give_back(prev):
    """기안 창이 뜨면서 앞창(키보드 포커스)을 가져갔으면, 원래 쓰던 창에 돌려준다 — 진짜 키 입력 없이
    (AttachThreadInput). 기안 창이 앞창인 채로 두면 과제카드 팝업이 같이 앞으로 튀어나온다 (2026-11-12 실측 7초)"""
    if not prev or not win32gui.IsWindow(prev):
        return False
    import win32process, win32api
    fg = win32gui.GetForegroundWindow()
    if fg == prev:
        return True
    me = win32api.GetCurrentThreadId()
    t_fg = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
    t_prev = win32process.GetWindowThreadProcessId(prev)[0]
    u = ctypes.windll.user32
    try:
        for t in {t_fg, t_prev} - {0, me}:
            u.AttachThreadInput(me, t, True)
        u.BringWindowToTop(prev)
        u.SetForegroundWindow(prev)
    finally:
        for t in {t_fg, t_prev} - {0, me}:
            u.AttachThreadInput(me, t, False)
    time.sleep(0.2)
    return win32gui.GetForegroundWindow() == prev


def tuck(h):
    """창을 최소화하되 앞으로 꺼내지 않는다 (SW_SHOWMINNOACTIVE)"""
    win32gui.ShowWindow(h, 7)


def new_dialog(before, title_part, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        for h in _visible(DLG):
            if h not in before and title_part in win32gui.GetWindowText(h):
                time.sleep(1.5)
                return h
        time.sleep(0.4)
    return None


def msg_boxes():
    out = []
    for h in _visible("#32770", "웹 페이지 메시지", exact=True):
        parts = []
        win32gui.EnumChildWindows(h, lambda c, _: parts.append(win32gui.GetWindowText(c)) if win32gui.GetClassName(c) == "Static" else True, None)
        out.append((h, " ".join(p for p in parts if p).replace("\n", " ")))
    return out


def press(h, button="확인"):
    """대화상자 버튼을 WM_COMMAND 로 누른다 — 진짜 클릭 아님"""
    b = win32gui.FindWindowEx(h, 0, "Button", button)
    if not b:
        raise SystemExit("버튼 '%s' 없음: %s" % (button, win32gui.GetWindowText(h)))
    win32gui.PostMessage(h, win32con.WM_COMMAND, win32gui.GetDlgCtrlID(b), b)
    time.sleep(0.8)


def wait_msg(timeout):
    end = time.time() + timeout
    while time.time() < end:
        m = msg_boxes()
        if m:
            return m[-1]
        time.sleep(0.3)
    return None


EV = "var ev=document.createEvent('HTMLEvents'); ev.initEvent('change',true,true); %s.dispatchEvent(ev);"
HWP = "document.getElementById('editor1').contentWindow.document.getElementById('editor1')"


def set_title(d, title):
    return js(d, "var e=document.getElementById('Sj'); e.value=%s; %s return e.value;" % (json.dumps(title), EV % "e"))


def set_public(d):
    return js(d, "var r=document.getElementById('othbcLmtt1'); r.click(); return r.checked;")


def set_preset(d, name):
    r = js(d, """var s=document.getElementById('streReportCours'); var k=-1;
        for(var i=0;i<s.options.length;i++){if(s.options[i].text==%s)k=i;}
        if(k<0)return 'NONE'; s.selectedIndex=k; %s return s.options[s.selectedIndex].text;""" % (json.dumps(name), EV % "s"))
    if r == "NONE":
        raise SystemExit("결재선 프리셋이 목록에 없다: %s" % name)
    return r


def set_body(d, text):
    t = text.replace("\r\n", "\n").replace("\n", "\r\n")
    return js(d, "var h=%s; h.PutFieldText('본문', %s); return h.GetFieldText('본문');" % (HWP, json.dumps(t)))


def field(d, name):
    """한글 컨트롤 누름틀 글자. 편집기가 없는 창이면 빈 글자"""
    return js(d, "var f=document.getElementById('editor1'); if(!f)return ''; return %s.GetFieldText(%s);" % (HWP, json.dumps(name)))


def choose_card(d, card):
    clear_autosave()
    before = _visible(DLG)
    js(d, "setTimeout(function(){fncChooseTkcrd()},10); return 1")
    h = new_dialog(before, "웹 페이지 대화 상자")
    if not h:
        raise SystemExit("과제카드 팝업이 안 떴다")
    time.sleep(2)
    p = doc_of(h)
    F = "var f=nexacro.getApplication().mainframe.QuickViewFrame.form; var t=f.Tab00.Tabpage1.form;"
    js(p, F + "if(f.dsSrchParam.rowcount<1)f.dsSrchParam.addRow(); f.dsSrchParam.setColumn(0,'tkcrdNm',%s);"
              "t.divSearch.form.edtTaskNm.set_value(%s); f.fnSearch(); return 1" % (json.dumps(card), json.dumps(card)))
    rows = ""
    for _ in range(20):
        time.sleep(0.5)
        rows = js(p, F + "var o=[]; for(var i=0;i<f.dsAList.rowcount;i++)o.push(f.dsAList.getColumn(i,'tkcrdNm')); return o.join('\\n');")
        if rows:
            break
    names = rows.split("\n") if rows else []
    hit = [i for i, n in enumerate(names) if n.strip() == card or n.endswith("] " + card) or n.split("] ")[-1] == card]
    if len(hit) != 1:
        raise SystemExit("과제카드 '%s' 가 하나로 안 잡힘 (%d개): %s" % (card, len(hit), names[:5]))
    js(p, F + "f.fnSelectUnitTkcrd(%d); return 1" % hit[0])
    time.sleep(2.5)
    js(p, F + "setTimeout(function(){f.Tab00_Tabpage1_divAuth_btnAuthC01_onclick(t.divAuth.form.btnAuthC01,{})},10); return 1")
    for _ in range(20):
        time.sleep(0.5)
        if not win32gui.IsWindow(h) or not win32gui.IsWindowVisible(h):
            break
    else:
        raise SystemExit("과제카드 창이 안 닫혔다")
    time.sleep(1)
    got = js(d, "var o=[]; var c=document.getElementsByTagName('td'); for(var i=0;i<c.length;i++){var x=c[i].innerText||''; if(x.indexOf(%s)>=0 && x.length<80)o.push(x);} return o.join('|');" % json.dumps(card))
    return names[hit[0]] if got else ""


def set_recipient(d, name):
    before = _visible(DLG)
    js(d, "setTimeout(function(){fncRcverAppn()},10); return 1")
    h = new_dialog(before, "수신자지정")
    if not h:
        raise SystemExit("수신자지정 팝업이 안 떴다")
    p = doc_of(h)
    js(p, "document.getElementById('orgnztNm').value=%s; setTimeout(function(){fncOrgnztSearch2()},10); return 1" % json.dumps(name))
    time.sleep(3)
    where = "조직도"
    m = msg_boxes()
    if m:
        if "조회된 내용이 없습니다" not in m[-1][1]:
            raise SystemExit("수신자 조회 중 모르는 메시지 — 멈춤: " + m[-1][1])
        press(m[-1][0])
        where = "행안부유통"
        js(p, "onTabRecpChange('RecpOrgLDAP','tabarea02'); return 1")
        time.sleep(2)
        js(p, """document.getElementById('orgnztLdapNm').value=%s; var a=document.getElementsByTagName('a'); var b=null;
            for(var i=0;i<a.length;i++){if(a[i].offsetWidth>0 && (a[i].innerText||'').replace(/\\s/g,'')=='조회')b=a[i];}
            setTimeout(function(){b.click()},10); return 1""" % json.dumps(name))
        time.sleep(5)
        box = "ldapChk"
    else:
        box = "orgnztChk"
    n = js(p, """var c=document.getElementsByName('%s'); var hit=[];
        for(var i=0;i<c.length;i++){var v=(c[i].title||'')+'|'+c[i].value; if(v.indexOf(%s)>=0)hit.push(c[i]);}
        if(hit.length==1 && !hit[0].checked)hit[0].click(); return hit.length;""" % (box, json.dumps(name)))
    if n != "1":
        raise SystemExit("수신자 '%s' 가 하나로 안 잡힘 (%s개, %s) — 멈춤" % (name, n, where))
    js(p, "setTimeout(function(){fncAdd()},10); return 1")
    time.sleep(2.5)
    js(p, "setTimeout(function(){fncVerifyChoose()},10); return 1")
    for _ in range(20):
        time.sleep(0.5)
        if not win32gui.IsWindow(h) or not win32gui.IsWindowVisible(h):
            break
    else:
        raise SystemExit("수신자지정 창이 안 닫혔다")
    return where, field(d, "수신")


def attach(d, path):
    before = set(_visible("#32770", "열기"))
    js(d, "setTimeout(function(){fncFileAdit()},10); return 1")
    h = None
    for _ in range(30):
        time.sleep(0.4)
        new = [x for x in _visible("#32770", "열기") if x not in before]
        if new:
            h = new[0]
            break
    if not h:
        raise SystemExit("열기 창이 안 떴다: %s" % path)
    time.sleep(0.6)
    cb = win32gui.FindWindowEx(h, 0, "ComboBoxEx32", None)
    ed = win32gui.FindWindowEx(win32gui.FindWindowEx(cb, 0, "ComboBox", None), 0, "Edit", None) if cb else 0
    if not ed:
        raise SystemExit("열기 창 파일 이름 칸을 못 찾음")
    win32gui.SendMessage(ed, win32con.WM_SETTEXT, 0, os.path.abspath(path))
    press(h, "열기(&O)")
    for _ in range(20):
        time.sleep(0.4)
        if not win32gui.IsWindow(h) or not win32gui.IsWindowVisible(h):
            break
    else:
        raise SystemExit("열기 창이 안 닫혔다 (경로 오류?) — 멈춤: %s" % path)
    time.sleep(1.5)
    base = os.path.basename(path)
    ok = js(d, "var b=document.getElementById('HSFileBox')||document.documentElement; return (b.innerText||b.innerHTML).indexOf(%s)>=0;" % json.dumps(base))
    return ok == "true"


def auto_send(d):
    if js(d, "return document.getElementById('autoSendGian').checked;") == "true":
        return "이미 켜짐"
    before = _visible(DLG)
    js(d, "setTimeout(function(){document.getElementById('autoSendGian').click()},10); return 1")
    m = wait_msg(4)
    if m:
        if "자동발송" not in m[1]:
            raise SystemExit("자동발송 체크 중 모르는 메시지 — 멈춤: " + m[1])
        press(m[0])
    # 안내를 닫으면 «자동발송(관인선택)» 창이 따라 뜬다 (2026-11-12 실측) — 기본값(관인서명·학교 관인) 그대로 확인.
    # 예전 판은 이 창을 결재올림 때 «관인» 창으로 만나 같은 기본값으로 확인했다
    seal = "관인창 없음"
    h = new_dialog(before, "관인", timeout=8)
    if h:
        js(doc_of(h), "setTimeout(function(){fncVerify()},10); return 1")
        for _ in range(15):
            time.sleep(0.4)
            if not win32gui.IsWindow(h) or not win32gui.IsWindowVisible(h):
                seal = "관인 기본값으로 확인"
                break
        else:
            raise SystemExit("자동발송(관인선택) 창이 안 닫혔다 — 멈춤")
    if js(d, "return document.getElementById('autoSendGian').checked;") != "true":
        raise SystemExit("자동발송이 켜지지 않았다 — 멈춤")
    return "켬 (%s)" % seal


def click_text_in(doc, text):
    """팝업 안에서 글자가 정확히 text 인 보이는 버튼·링크를 DOM 으로 누른다"""
    return js(doc, """var a=document.querySelectorAll('a,button,span,input[type=button],img'); var b=null;
        for(var i=0;i<a.length;i++){var x=a[i]; var t=(x.innerText||x.value||x.alt||'').replace(/\\s/g,'');
          if(t==%s && x.offsetWidth>0)b=x;}
        if(!b)return 'NONE'; setTimeout(function(){b.click()},10); return 'OK';""" % json.dumps(text))
