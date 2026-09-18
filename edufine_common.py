# -*- coding: utf-8 -*-
"""K-에듀파인 자동화 공통 부품 — 로그인·지역·크롬 띄우기·팝업 닫기·버튼 누르기.

품의(edufine_punui.py)와 검수(edufine_inspect.py)가 함께 쓴다. 혼자서는 아무 일도 하지 않는다.

설정은 나이스 키트와 «한 벌»이다 — setup_wizard.py 가 만든
  %LOCALAPPDATA%\\neis-automation\\teacher_config.json   (cert_name · neis_url · region)
  Windows 자격 증명 관리자 'neis-automation'            (인증서 암호)
을 그대로 읽는다. 에듀파인용 설정 파일을 따로 두지 않는다 — 두 벌이면 반드시 어긋난다.

환경변수로 덮어쓸 수 있다 (시험용):
  EDUFINE_REGION=경기      EDUFINE_PROFILE=<빈 폴더>   EDUFINE_CERT_NAME=홍길동
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

try:                                   # 한글이 ??? 로 깨지지 않게 — 환경변수에 기대지 않는다
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "neis-automation"
CONFIG_FILE = BASE / "teacher_config.json"
KEYRING_SERVICE = "neis-automation"
# 에듀파인 전용 크롬 프로필. 평소 쓰는 크롬과 섞이지 않는다.
# 빈 프로필로 «첫 실행»을 재 보려면 EDUFINE_PROFILE 로 갈아끼운다.
PROFILE_DIR = os.environ.get("EDUFINE_PROFILE", str(BASE / "chrome_profile_edufine"))
# 캡처·읽은 품의는 이 폴더의 캡처/ 에 둔다 — 클로드는 이 폴더 밖 파일을 못 연다
# (2026-09-18 실측). .gitignore 가 캡처/ 를 막으니 저장소에는 안 올라간다.
OUT_DIR = HERE / "캡처"


def log(msg=""):
    print(msg, flush=True)


# ── 지역 (2026-09-11 · 09-18 DNS 로 34+17개 주소 확인. 화면까지 본 곳은 서울뿐) ──
# ⚠️ 경북만 K-에듀파인이 `.go.kr` 이 아니라 `klef.gbe.kr` 이다.
REGIONS = {
    "서울": "sen", "부산": "pen", "대구": "dge", "인천": "ice", "광주": "gen",
    "대전": "dje", "울산": "use", "세종": "sje", "경기": "goe", "강원": "gwe",
    "충북": "cbe", "충남": "cne", "전북": "jbe", "전남": "jne", "경북": "gbe",
    "경남": "gne", "제주": "jje",
}
CODE_TO_REGION = {v: k for k, v in REGIONS.items()}


def load_cfg():
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def region():
    """지역을 정한다. 순서: 환경변수 → 설정의 region → 설정의 neis_url 앞글자 → 서울."""
    cfg = load_cfg()
    name = os.environ.get("EDUFINE_REGION") or cfg.get("region")
    if not name:
        host = urlsplit(cfg.get("neis_url") or "").netloc.split(":")[0]
        name = CODE_TO_REGION.get(host.split(".")[0].lower())
    name = (name or "서울").strip()
    if name not in REGIONS:
        raise SystemExit("설정의 지역 '%s' 을 모르겠습니다. 이 중 하나여야 합니다:\n  %s"
                         % (name, " · ".join(REGIONS)))
    return name


REGION = region()
CODE = REGIONS[REGION]
PORTAL = os.environ.get("EDUFINE_PORTAL", "https://%s.eduptl.kr" % CODE)
KLEF = os.environ.get("EDUFINE_KLEF",
                      "https://klef.gbe.kr/" if CODE == "gbe" else "https://klef.%s.go.kr/" % CODE)
IDP = "https://idp1-%s.neis.go.kr:9443" % CODE


def creds():
    """인증서 이름은 설정 파일에서, 암호는 자격 증명 관리자에서 꺼낸다. 암호는 어디에도 찍지 않는다."""
    name = os.environ.get("EDUFINE_CERT_NAME") or load_cfg().get("cert_name")
    if not name:
        raise SystemExit(
            "인증서 이름이 설정에 없습니다 (%s).\n"
            "  설정 마법사를 먼저 돌려 주세요:  .\\.venv\\Scripts\\python.exe -X utf8 setup_wizard.py"
            % CONFIG_FILE)
    import keyring
    # 예전 에듀파인 배포판은 'edufine' 이름으로 넣게 했다 — 거기 있으면 그것도 쓴다
    for svc in (KEYRING_SERVICE, "edufine"):
        pw = keyring.get_password(svc, name)
        if pw:
            return name, pw
    raise SystemExit(
        "'%s' 인증서 암호가 저장돼 있지 않습니다.\n"
        "  설정 마법사를 다시 돌려 암호를 넣어 주세요:  .\\.venv\\Scripts\\python.exe -X utf8 setup_wizard.py"
        % name)


# ── 크롬 띄우기 ───────────────────────────────────────────────
# 이 컴퓨터에 깔린 구글 크롬을 쓴다(channel="chrome"). playwright 전용 브라우저는
# 새 컴퓨터에서 «side-by-side configuration» 오류로 안 켜진 적이 있다 (2026-09-16 실측).
CHROME_ARGS = [
    "--profile-directory=Default",
    "--disable-blink-features=AutomationControlled",
    # 이 줄이 없으면 업무포털이 127.0.0.1 보안프로그램에 못 붙어 «보안프로그램 로딩중»에서 멈춘다
    "--disable-features=PrivateNetworkAccessForNavigations,"
    "PrivateNetworkAccessPermissionPrompt,PermissionChip",
    "--start-maximized", "--noerrdialogs", "--test-type",
]


def launch(p):
    """에듀파인 전용 크롬을 띄우고 (ctx, page) 를 돌려준다."""
    kw = dict(user_data_dir=PROFILE_DIR, headless=False, args=CHROME_ARGS,
              ignore_default_args=["--enable-automation"], viewport=None)
    try:
        ctx = p.chromium.launch_persistent_context(channel="chrome", **kw)
    except Exception as e:
        msg = str(e)
        if "ProcessSingleton" in msg or "already in use" in msg or "user data directory" in msg:
            raise SystemExit(
                "에듀파인 자동화용 크롬이 이미 켜져 있습니다. 그 창을 닫고 다시 실행해 주세요.\n"
                "  (평소 쓰시는 크롬은 닫지 않으셔도 됩니다)")
        log("  (깔린 크롬으로 못 띄워서 playwright 크롬으로 띄웁니다: %s)" % msg.splitlines()[0][:100])
        ctx = p.chromium.launch_persistent_context(**kw)
    grant_local_network(ctx)             # ★ 페이지를 열기 «전»에
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    page.on("dialog", on_dialog)
    ctx.on("page", lambda pg: pg.on("dialog", on_dialog))    # 팝업 창에서 뜨는 확인창도
    return ctx, page


def is_ip_takeover(msg):
    """«IP(…)에서 접속 중입니다. 기존 사용자를 종료 후 계속 진행하시겠습니까?» 창인가."""
    return "접속 중입니다" in msg and "기존 사용자를 종료" in msg


def on_dialog(d):
    """브라우저 확인창 처리 — «다른 IP 에서 접속 중» 창만 [확인], 나머지는 모두 «아니오».

    접속 IP 가 바뀌면(학교↔집·EVPN) 에듀파인이 이 창을 띄운다. «아니오»로 닫으면
    «중복 로그인 안내» 페이지로 빠져 메뉴를 못 연다 (2026-09-18 학교 밖 EVPN 실측).
    [확인]하면 다른 곳(학교 PC 등)에 켜 둔 에듀파인은 끊긴다 — 2026-09-11 사용자 결정.
    """
    msg = " ".join((d.message or "").split())
    try:
        if is_ip_takeover(msg):
            log("  💬 다른 곳의 에듀파인 접속을 끊고 들어갑니다: %s" % msg[:120])
            d.accept()
        else:
            log("  💬 확인창을 «아니오»로 닫았습니다: %s" % msg[:120])
            d.dismiss()
    except Exception:
        pass                         # 이미 닫힌 창


def grant_local_network(ctx):
    """크롬의 «이 기기의 다른 앱 및 서비스에 액세스» 말풍선을 미리 허용한다.

    이 말풍선은 크롬이 그린 것이라 자동으로 못 누른다. 안 눌리면 보안프로그램에 영영 못 닿고
    «보안프로그램이 로딩중입니다» 가림막이 안 걷힌다 (2026-09-17 빈 프로필 실측 — 첫 실행이 막히던 진짜 원인).
    """
    ok = 0
    for u in (PORTAL, KLEF, IDP):
        sp = urlsplit(u)
        try:
            ctx.grant_permissions(["local-network-access"], origin="%s://%s" % (sp.scheme, sp.netloc))
            ok += 1
        except Exception as e:
            log("  (로컬 네트워크 접근을 미리 허용하지 못했습니다: %s)" % str(e).splitlines()[0][:100])
            log("  → 크롬이 «이 기기의 다른 앱 및 서비스에 액세스» 를 물어보면 [허용] 을 눌러 주세요.")
            return False
    return ok > 0


# ── 업무포털 «불러오는 중» 가림막 ─────────────────────────────────
LOAD_OVERLAY = ".isloading-overlay, [class*='isloading'], [class*='loading-overlay'], .blockUI"

JS_OVERLAY_COUNT = """
(s) => [...document.querySelectorAll(s)].filter(e => {
  const st = getComputedStyle(e);
  if (st.display === 'none' || st.visibility === 'hidden') return false;
  if (parseFloat(st.opacity === '' ? '1' : st.opacity) === 0) return false;
  if (st.pointerEvents === 'none') return false;
  const r = e.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}).length
"""

JS_KILL_OVERLAY = """
(s) => {
  let n = 0;
  document.querySelectorAll(s).forEach(e => {
    const st = getComputedStyle(e);
    if (st.display !== 'none' && st.visibility !== 'hidden' && st.pointerEvents !== 'none') {
      e.style.setProperty('pointer-events', 'none', 'important'); n++;
    }
  });
  return n;
}
"""


def wait_overlay_gone(page, timeout=90, quiet=False):
    end = time.time() + timeout
    warned = False
    while time.time() < end:
        try:
            if page.evaluate(JS_OVERLAY_COUNT, LOAD_OVERLAY) == 0:
                return True
        except Exception:
            pass
        if not warned and not quiet:
            log("  화면이 아직 다 안 열렸습니다. 조금 기다립니다...")
            warned = True
        page.wait_for_timeout(1000)
    return False


def robust_click(page, loc, label):
    """보통 클릭 → 안 되면 가림막에 클릭 통과(pointer-events:none)를 박고 진짜 클릭.
    force 클릭·JS 클릭은 가림막 앞에서 소용없었다 (2026-09-17 실측)."""
    try:
        loc.click(timeout=15000)
        return
    except Exception:
        pass
    n = page.evaluate(JS_KILL_OVERLAY, LOAD_OVERLAY)
    log("  (가림막 %d개를 걷고 '%s' 를 다시 누릅니다)" % (n, label))
    loc.click(timeout=15000)


MANUAL_WAIT_MIN = int(os.environ.get("EDUFINE_MANUAL_WAIT_MIN", "10"))


def manual_login(page):
    """자동 로그인이 안 되면 죽지 않고, 열린 크롬에서 사람이 한 번 로그인하기를 기다린다.
    한 번만 손으로 하면 프로필에 세션이 남아 다음부터는 자동이다."""
    try:
        page.evaluate(JS_KILL_OVERLAY, LOAD_OVERLAY)
    except Exception:
        pass
    log("")
    log("=" * 60)
    log("  자동 로그인이 안 됩니다. 지금 열려 있는 크롬 창에서 직접 로그인해 주세요.")
    log("  (처음 한 번만 하시면 됩니다)")
    log("    1. 크롬이 «이 기기의 다른 앱 및 서비스에 액세스» 를 물으면 [허용]")
    log("    2. [교육행정 전자서명 인증서 로그인] → 본인 인증서 → 암호")
    log("    3. 로그인되면 그대로 두세요. 알아서 이어서 진행합니다")
    log("  단추가 안 눌리면 F5 로 새로고침하고 10초 뒤 다시 눌러 보세요.")
    log("  %d분 안에 안 되면 그만둡니다." % MANUAL_WAIT_MIN)
    log("=" * 60)
    end = time.time() + MANUAL_WAIT_MIN * 60
    next_kill = 0.0
    while time.time() < end:
        try:
            if "bpm_man_mn00_001" in page.url:
                log("✅ 로그인 확인 — 이어서 진행합니다")
                return
        except Exception:
            raise SystemExit("크롬 창이 닫혔습니다. 다시 실행해 주세요.")
        if time.time() >= next_kill:           # 화면이 다시 그려지면 가림막이 또 붙는다
            next_kill = time.time() + 3
            try:
                page.evaluate(JS_KILL_OVERLAY, LOAD_OVERLAY)
            except Exception:
                pass
        page.wait_for_timeout(2000)
    raise SystemExit("%d분 동안 로그인이 안 됐습니다.\n"
                     "  인증서가 이 컴퓨터에 있는지 보세요:  dir C:\\GPKI\\Certificate\\class2"
                     % MANUAL_WAIT_MIN)


def login(page, cert_name, cert_pw):
    log("🔐 업무포털 로그인 (지역: %s / %s)" % (REGION, PORTAL))
    page.goto(PORTAL, wait_until="domcontentloaded")
    wait_overlay_gone(page, timeout=90)
    btn = page.get_by_role("button", name="교육행정 전자서명 인증서 로그인", exact=True)
    reloaded = False
    retried = 0
    for i in range(60):
        if "bpm_man_mn00_001" in page.url:
            log("✅ 이미 로그인돼 있습니다")
            return
        # 예전 접속이 «중복로그인»으로 끊긴 채 남아 있으면 첫 화면이 오류 쪽지로 간다
        # (idp1-…/error.jsp?errorCode=1800, 2026-09-18 실측). 다시 들어가면 로그인 버튼이 나온다.
        if "errorCode=" in page.url and retried < 3:
            retried += 1
            log("  지난 접속이 끊긴 채 남아 있어 로그인 화면으로 다시 들어갑니다 (%s)"
                % page.url.split("errorCode=")[-1][:6])
            if retried >= 2:                  # 그래도 같으면 이 전용 크롬의 쿠키만 지운다
                page.context.clear_cookies()
            page.goto(PORTAL, wait_until="domcontentloaded")
            wait_overlay_gone(page, timeout=60, quiet=True)
            continue
        try:
            if btn.count() and btn.last.is_visible():
                break
        except Exception:
            pass
        if i == 20 and not reloaded:
            reloaded = True
            page.reload(wait_until="domcontentloaded")
            wait_overlay_gone(page, timeout=60)
        page.wait_for_timeout(1500)
    else:
        return manual_login(page)

    wait_overlay_gone(page, timeout=60)
    robust_click(page, btn.last, "교육행정 전자서명 인증서 로그인")
    wait_overlay_gone(page, timeout=60, quiet=True)
    pwd = page.get_by_role("textbox", name="인증서 암호 입력필드")
    try:
        pwd.wait_for(timeout=90000)
    except Exception:
        return manual_login(page)

    # ★ 인증서 줄을 먼저 «눌러서 고른» 다음 암호. 순서를 바꾸면 Enter 가 안 먹는다.
    for _ in range(30):
        cells = page.get_by_role("gridcell").filter(has_text=cert_name)
        if cells.count():
            if cells.count() > 1:
                log("  ⚠️ '%s' 가 든 인증서가 %d개입니다. 첫 번째를 고릅니다." % (cert_name, cells.count()))
            robust_click(page, cells.first, "인증서")
            break
        page.wait_for_timeout(1000)
    else:
        raise SystemExit("인증서 목록에서 '%s' 을(를) 못 찾았습니다.\n"
                         "  이름을 확인하세요:  dir C:\\GPKI\\Certificate\\class2" % cert_name)
    page.wait_for_timeout(400)
    pwd.fill(cert_pw)
    pwd.press("Enter")
    for _ in range(60):
        page.wait_for_timeout(1500)
        if "bpm_man_mn00_001" in page.url:
            log("✅ 로그인 완료")
            return
        box = page.locator("#nexacontainer.nexamodaloverlay")
        if box.count():
            raise SystemExit("로그인 중 알림: " + box.first.inner_text()[:200])
        if "errorCode=1800" in page.url or "중복로그인" in (page.content()[:5000]):
            raise SystemExit("「중복로그인」으로 끊겼습니다. 평소 쓰는 크롬에서 업무포털을 로그아웃하고 다시 실행해 주세요.")
    return manual_login(page)


# ── K-에듀파인(Nexacro) 화면 부품 ────────────────────────────────────
OVERLAY = "#nexacontainer.nexamodaloverlay"
CLOSE_SELECTORS = (
    OVERLAY + ' div[id$="divPopupBottom.form.btnClose:icontext"]',   # 공지사항
    OVERLAY + ' div[id$="form.btnOk:icontext"]',                     # 알림/확인
    OVERLAY + ' div[id$="btnConfirm:icontext"]',
)
# 이 문구가 든 창은 «업무 확인창»이다 — 공지 닫듯이 [확인]을 누르면 일이 실행돼 버린다
NEVER_AUTO_CLOSE = ("결재요청 없이", "저장하시겠습니까", "삭제하시겠습니까", "결재요청 하시겠습니까")


def has_business_confirm(page):
    return any(page.locator(OVERLAY).filter(has_text=k).count() for k in NEVER_AUTO_CLOSE)


def close_modals(page, limit=8):
    """공지·알림 팝업을 닫는다. 업무 확인창은 건드리지 않는다. 닫은 개수를 돌려준다."""
    n = 0
    for _ in range(limit):
        if page.locator(OVERLAY).count() == 0 or has_business_confirm(page):
            break
        clicked = False
        for sel in CLOSE_SELECTORS:
            loc = page.locator(sel)
            if loc.count():
                try:
                    loc.last.click(timeout=2500)
                    clicked = True
                    break
                except Exception:
                    continue
        if not clicked:
            break
        n += 1
        page.wait_for_timeout(900)
    return n


def modal_text(page):
    return page.evaluate(
        "() => { const el = document.querySelector('#nexacontainer.nexamodaloverlay');"
        " return el ? el.innerText.replace(/\\s*\\n\\s*/g, ' | ').slice(0, 300) : null; }")


def wait_modal(page, text, timeout_ms):
    box = page.locator(OVERLAY)
    if text:
        box = box.filter(has_text=text)
    try:
        box.last.wait_for(state="attached", timeout=timeout_ms)
        return box.last
    except Exception:
        return None


def set_date(page, suffix, yyyymmdd):
    el = page.locator('input[id$="%s"]' % suffix)
    el.click()
    el.press("Control+a")
    el.type(yyyymmdd, delay=35)
    el.press("Tab")
    page.wait_for_timeout(400)
    return el.input_value()


def click_btn(page, name, timeout=10000):
    """Nexacro 툴바 버튼(`…:icontext` DIV) 누르기. 누르기 전에 공지 팝업을 치운다."""
    close_modals(page)
    exact = re.compile("^%s$" % re.escape(name))
    last = None
    for make in (lambda: page.locator('div[id$=":icontext"]').filter(has_text=exact),
                 lambda: page.get_by_role("button", name=name, exact=True)):
        try:
            loc = make()
            if loc.count() == 0:
                continue
            # 같은 글자의 버튼이 화면 밖에도 있다 — 보이는 것을 고른다
            for i in range(loc.count() - 1, -1, -1):
                if loc.nth(i).is_visible():
                    loc.nth(i).click(timeout=timeout)
                    return
            loc.last.click(timeout=timeout)
            return
        except Exception as e:
            last = e
    raise RuntimeError("버튼 '%s' 을(를) 누르지 못했습니다: %s" % (name, str(last)[:200]))


def open_klef_menu(page, menu, ready_selector=None):
    """K-에듀파인으로 가서 왼쪽 즐겨찾기/메뉴의 글자 그대로인 항목을 누른다."""
    log("📂 K-에듀파인 → %s" % menu)
    page.goto(KLEF, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    loc = page.locator("div").filter(has_text=re.compile("^%s$" % re.escape(menu))).first
    last = None
    for _ in range(10):
        close_modals(page)          # 공지가 1~3개, 시차를 두고 뜬다
        page.wait_for_timeout(1200)
        if page.locator(OVERLAY).count():
            continue
        try:
            loc.click(timeout=5000)
            if ready_selector:
                page.wait_for_selector(ready_selector, timeout=30000)
            else:
                page.wait_for_timeout(4000)
            page.wait_for_timeout(800)
            return True
        except Exception as e:
            last = e
    raise SystemExit("'%s' 메뉴를 열지 못했습니다. 왼쪽 즐겨찾기에 '%s' 가 있는지 화면을 봐 주세요.\n  (%s)"
                     % (menu, menu, str(last)[:150]))


def shot(page, name):
    """스크린샷을 이 폴더의 캡처/ 에 남긴다 (gitignore — 학교·사람 이름이 찍히므로 올리지 않는다)."""
    d = OUT_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    try:
        page.screenshot(path=str(p))
        log("  📸 캡처: %s" % p)
    except Exception:
        pass
    return p
