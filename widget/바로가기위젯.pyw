# -*- coding: utf-8 -*-
"""나이스·에듀파인 바로가기 위젯 — 바탕화면에 떠 있는 작은 단추 넷.

  .\\.venv\\Scripts\\pythonw.exe widget\\바로가기위젯.pyw      (설정하면 바탕화면 바로가기가 생긴다)

  [나이스] [에듀파인] [문서등록대장] [공람] [검수] [복무]  — 앞의 넷은 누르면 인증서 자동 로그인까지 해서 그 화면을 맨 앞에 띄운다.
  [검수]: 밀린 검사검수 건을 조회해 목록을 보여 주고, [등록하기]를 눌러야 등록한다 (edufine_inspect.py --go).
  [복무]: 작은 창에서 종류(조퇴·외출·지각·병조퇴·병외출·병지각)·날짜·시각·사유를 고르고 [상신]을 누르면 나이스에 승인요청까지 올린다
          (main_bokmu.py). 종료 시각 기본값은 설정의 «퇴근 시각»(학교마다 다르다 — 처음 설정에서 묻는다).
  위젯이 뜰 때 두 크롬을 «최소화된 채로» 미리 로그인해 두고 5분마다 세션을 살려 두므로, 클릭은 보통 1~2초.
  오른쪽 클릭: 인증서 설정 · 미리 열어두기 켜고 끄기 · 크롬 창 닫기 · 위젯 끄기.  끌어서 옮길 수 있다.

안 될 때: %LOCALAPPDATA%\\neis-automation\\widget.log 에 무슨 명령이 얼마 걸려 어떻게 끝났는지 한 줄씩 남는다.
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE / "바로가기_엔진.py"
SETUP = HERE / "바로가기_설정.py"
PY = Path(sys.executable).with_name("python.exe")
BASE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "neis-automation"
STATE = BASE / "widget_state.json"
LOG = BASE / "widget.log"
CONFIG = BASE / "teacher_config.json"
NO_WINDOW = 0x08000000
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
WARM_EVERY = 5 * 60 * 1000
RELAUNCH_EVERY = 60 * 1000      # 크롬을 닫았으면 1분 안에 다시 최소화로 띄워 데운다 (안 데우려면 메뉴 «미리 열어두기» 끄기)
PORTS = {"neis": 9322, "edufine": 9324}

BG, INK, LINE = "#fffaf2", "#3b2f25", "#d9ccb8"
BTNS = [("나이스", "neis", "open", "#1b6fb5"), ("에듀파인", "edufine", "main", "#2f7d4f"),
        ("문서등록대장", "edufine", "open", "#8a5a2b"), ("공람", "edufine", "cnrs", "#6b4fb5"),
        ("검수", "inspect", "check", "#d63a30"), ("복무", "bokmu", "form", "#c2417a")]
INSPECT = HERE.parent / "edufine_inspect.py"      # 키트의 물품 검사검수 (조회만 / --go 등록)
BOKMU = HERE.parent / "main_bokmu.py"             # 키트의 복무(근무상황) 상신
BOKMU_TYPES = ["조퇴", "외출", "지각", "병조퇴", "병외출", "병지각"]


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(**kw):
    st = load_state()
    st.update(kw)
    BASE.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def log(what, lines, t0):
    try:
        steps = " / ".join(ln[5:].split("  (")[0] for ln in lines if ln.startswith("STEP "))[:300]
        last = next((ln for ln in reversed(lines) if ln.startswith(("READY", "FAIL", "CLOSED"))), "(없음)")
        row = "%s  %s  %.1fs  %s  [%s]" % (datetime.now().strftime("%m-%d %H:%M:%S"), what, time.time() - t0, last[:100], steps)
        old = LOG.read_text(encoding="utf-8").splitlines()[-300:] if LOG.exists() else []
        BASE.mkdir(parents=True, exist_ok=True)
        LOG.write_text("\n".join(old + [row]) + "\n", encoding="utf-8")
    except Exception:
        pass


def port_alive(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=1.5) as r:
            return bool(json.load(r).get("Browser"))
    except Exception:
        return False


def configured():
    try:
        return bool(json.loads(CONFIG.read_text(encoding="utf-8")).get("cert_name"))
    except Exception:
        return False


def cfg():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def round5(hm):
    h, m = map(int, hm.split(":"))
    m = round(m / 5) * 5
    if m == 60:
        h, m = (h + 1) % 24, 0
    return "%02d:%02d" % (h, m)


WEEK = "월화수목금토일"


def date_label(iso):
    """2026-10-16 → «2026-10-16 (금) 📅» (단추 글자)"""
    from datetime import date
    try:
        return "%s (%s)  📅" % (iso, WEEK[date.fromisoformat(iso).weekday()])
    except ValueError:
        return iso + "  📅"


def date_picker(parent, var, bg, on_pick=None):
    """작은 달력 — 날짜를 누르면 var 에 YYYY-MM-DD 가 들어간다 (10/16 사용자: 날짜는 치지 말고 달력에서 클릭)"""
    import calendar
    from datetime import date
    f, fb = ("Malgun Gothic", 8), ("Malgun Gothic", 9, "bold")
    try:
        d0 = date.fromisoformat(var.get())
    except ValueError:
        d0 = date.today()
    shown = [d0.year, d0.month]
    box = tk.Frame(parent, bg=bg)
    head = tk.Frame(box, bg=bg)
    head.pack(fill="x")
    title = tk.Label(head, bg=bg, font=fb)
    days = tk.Frame(box, bg=bg)
    days.pack()

    def move(step):
        y, m = shown[0], shown[1] + step
        if m < 1:
            y, m = y - 1, 12
        elif m > 12:
            y, m = y + 1, 1
        shown[:] = [y, m]
        draw()

    def pick(iso):
        var.set(iso)
        draw()
        if on_pick:
            on_pick(iso)

    def draw():
        for w in days.winfo_children():
            w.destroy()
        y, m = shown
        title.config(text="%d년 %d월" % (y, m))
        for i, d in enumerate("일월화수목금토"):           # 일요일부터 — 우리 달력 모양
            tk.Label(days, text=d, bg=bg, font=f, width=3,
                     fg="#c0392b" if i == 0 else "#1b6fb5" if i == 6 else "#8a7a68").grid(row=0, column=i)
        sel, today = var.get(), date.today().isoformat()
        for r, week in enumerate(calendar.Calendar(firstweekday=6).monthdayscalendar(y, m), start=1):
            for c, d in enumerate(week):
                if not d:
                    continue
                iso = "%04d-%02d-%02d" % (y, m, d)
                on = iso == sel
                tk.Button(days, text=str(d), width=3, relief="flat", bd=0, font=f, cursor="hand2",
                          bg="#c2417a" if on else "#fde7f0" if iso == today else "white",
                          fg="white" if on else "#c0392b" if c == 0 else "#1b6fb5" if c == 6 else "#3b2f25",
                          activebackground="#f4c2d7",
                          command=lambda iso=iso: pick(iso)).grid(row=r, column=c, padx=1, pady=1)
    tk.Button(head, text="◀", relief="flat", bd=0, bg=bg, font=f, cursor="hand2", command=lambda: move(-1)).pack(side="left", padx=4)
    tk.Button(head, text="▶", relief="flat", bd=0, bg=bg, font=f, cursor="hand2", command=lambda: move(1)).pack(side="right", padx=4)
    title.pack(side="left", expand=True)
    draw()
    return box


class Widget:
    def __init__(self):
        st = load_state()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        sw = self.root.winfo_screenwidth()
        self.root.geometry("+%d+%d" % (st.get("x", sw - 470), st.get("y", 12)))
        self.prewarm = st.get("prewarm", False)   # 기본 끔 — 누르기 전에 창이 뜨면 «안 누른 문서등록대장이 켜진다»로 보인다 (2026-10-23)
        self.locks = {"neis": threading.Lock(), "edufine": threading.Lock(), "inspect": threading.Lock()}
        self.busy = None
        self.note = ("", INK)

        fr = tk.Frame(self.root, bg=BG, highlightbackground=LINE, highlightthickness=1)
        fr.pack()
        grip = tk.Label(fr, text="⋮", bg=BG, fg="#b5a593", font=("Malgun Gothic", 12), cursor="fleur", padx=4)
        grip.pack(side="left", fill="y")
        self.buttons = {}
        for text, site, cmd, color in BTNS:
            b = tk.Label(fr, text=text, bg=BG, fg=color, font=("Malgun Gothic", 10, "bold"), padx=9, pady=6, cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, s=site, c=cmd, t=text: self.click(s, c, t))
            b.bind("<Enter>", lambda e, w=b: w.configure(bg="#f3e9da"))
            b.bind("<Leave>", lambda e, w=b: w.configure(bg=BG))
            self.buttons[text] = b
        self.status = tk.Label(fr, text="", bg=BG, fg="#8a7a68", font=("Malgun Gothic", 9), padx=6)
        self.spacer = tk.Label(fr, text=" ", bg=BG, font=("Malgun Gothic", 12), padx=4)   # 왼쪽 ⋮ 와 같은 폭 → 좌우 대칭
        self.spacer.pack(side="left", fill="y")
        for w in (fr, grip):
            w.bind("<ButtonPress-1>", self.press)
            w.bind("<B1-Motion>", self.drag)
            w.bind("<ButtonRelease-1>", self.release)
        for w in (fr, grip, self.status, *self.buttons.values()):
            w.bind("<Button-3>", self.menu)

        self.m = tk.Menu(self.root, tearoff=0, font=("Malgun Gothic", 9))
        self.m.add_command(label="인증서 설정 (처음 한 번)", command=self.open_setup)
        self.prewarm_var = tk.BooleanVar(value=self.prewarm)
        self.m.add_checkbutton(label="미리 열어두기 (빠른 클릭)", variable=self.prewarm_var, command=self.toggle_prewarm)
        self.m.add_command(label="지금 미리 열어두기", command=lambda: self.warm_all(True))
        self.m.add_separator()
        self.m.add_command(label="위젯용 크롬 창 닫기", command=self.close_chrome)
        self.m.add_command(label="위젯 끄기", command=self.root.destroy)

    # ── 끌기 ──
    def press(self, e):
        self.sx, self.sy = e.x_root, e.y_root
        self.wx, self.wy = self.root.winfo_x(), self.root.winfo_y()

    def drag(self, e):
        self.root.geometry("+%d+%d" % (self.wx + e.x_root - self.sx, self.wy + e.y_root - self.sy))

    def release(self, e):
        save_state(x=self.root.winfo_x(), y=self.root.winfo_y())

    def menu(self, e):
        self.m.tk_popup(e.x_root, e.y_root)

    # ── 말풍선 ──
    def show_status(self, text, color):
        """상태 글자는 있을 때만 막대에 붙인다 (없을 땐 빼서 단추만 남게)."""
        if text:
            self.status.configure(text=text, fg=color)
            if not self.status.winfo_ismapped():
                self.status.pack(side="left", before=self.spacer)
        else:
            self.status.configure(text="")
            self.status.pack_forget()

    def say(self, text, color=INK, ms=4000):
        self.show_status(text, color)
        self.root.after(ms, lambda: self.show_status("", color) if self.status.cget("text") == text else None)

    def tick(self):
        if self.busy:
            dots = "." * (int(time.time() * 2) % 3 + 1)
            self.show_status(self.busy + dots, "#8a7a68")
        self.root.after(400, self.tick)

    # ── 엔진 부르기 ──
    def run(self, site, cmd, extra=(), what=None, on_step=None):
        t0 = time.time()
        seen, last = [], ""
        with self.locks[site]:
            p = subprocess.Popen([str(PY), "-X", "utf8", str(ENGINE), site, cmd, *extra], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                 env=ENV, cwd=str(HERE), creationflags=NO_WINDOW)
            try:
                ctypes.windll.user32.AllowSetForegroundWindow(p.pid)   # 클릭을 받은 건 위젯 — 창 꺼낼 권한을 넘긴다
            except Exception:
                pass
            for line in p.stdout:
                line = line.strip()
                seen.append(line)
                if line.startswith("STEP ") and on_step:
                    on_step(line[5:].split("  (")[0])
                elif line.startswith(("READY", "FAIL", "CLOSED")):
                    last = line
            p.wait()
        log(what or "%s %s" % (site, cmd), seen, t0)
        return last

    def click(self, site, cmd, text):
        if self.busy:
            return
        if not configured():
            self.say("먼저 오른쪽 클릭 → 인증서 설정", "#c0392b", 6000)
            return
        if site == "inspect":
            self.inspect_check()
            return
        if site == "bokmu":
            self.bokmu_form()
            return
        self.busy = text + " 여는 중"

        def fn():
            try:
                last = self.run(site, cmd, what="click " + text, on_step=lambda s: setattr(self, "busy", s))
            finally:
                self.busy = None
            if last.startswith("READY"):
                self.root.after(0, self.say, "짠! 열렸어요", "#2f7d4f")
            else:
                self.root.after(0, self.say, "앗, 실패: " + (last[5:] or "알 수 없는 오류"), "#c0392b", 10000)
        threading.Thread(target=fn, daemon=True).start()

    # ── 검수 (물품 검사검수) ──
    def run_inspect(self, go):
        """edufine_inspect.py 를 돌려 RESULT_JSON 을 돌려준다. 자기 크롬(chrome_profile_edufine)을 띄우므로 창이 보인다."""
        t0 = time.time()
        args = [str(PY), "-X", "utf8", str(INSPECT)] + (["--go"] if go else [])
        with self.locks["inspect"]:
            p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=ENV, cwd=str(INSPECT.parent), creationflags=NO_WINDOW, timeout=900)
        lines = (p.stdout or "").splitlines() + (p.stderr or "").splitlines()
        res = next((ln[12:] for ln in reversed(lines) if ln.startswith("RESULT_JSON=")), "")
        log("inspect" + (" --go" if go else ""), ["READY " + res[:80] if res else "FAIL " + " ".join(lines[-3:])[:120]], t0)
        try:
            return json.loads(res)
        except Exception:
            return None

    def inspect_check(self):
        self.busy = "검수 대기 조회 중"

        def fn():
            try:
                r = self.run_inspect(False)
            finally:
                self.busy = None
            if r is None:
                self.root.after(0, self.say, "앗, 검수 목록을 못 읽었어요 (widget.log 참고)", "#c0392b", 10000)
                return
            pend = r.get("대기", [])
            if not pend:
                self.root.after(0, self.say, "검수 대기 0건 — 할 게 없어요", "#2f7d4f", 6000)
                return
            text = "검수 대기 %d건\n\n" % len(pend) + "\n".join(
                "• %s  %s  (요청 %s · 완료요구 %s)" % (x.get("계약번호", ""), x.get("계약명", ""), x.get("요청일자", ""), x.get("완료요구일", ""))
                for x in pend) + "\n\n[등록하기]를 누르면 위 건을 모두 검사검수 등록합니다. 되돌리기 어렵습니다."
            self.root.after(0, self.confirm, text, "등록하기", self.inspect_go)
        threading.Thread(target=fn, daemon=True).start()

    def inspect_go(self):
        self.busy = "검수 등록 중"

        def fn():
            try:
                r = self.run_inspect(True)
            finally:
                self.busy = None
            if r is None:
                self.root.after(0, self.say, "앗, 검수 등록 결과를 못 받았어요 (widget.log 참고)", "#c0392b", 10000)
                return
            done = r.get("처리", [])
            ok = sum(1 for x in done if x.get("성공"))
            bad = [x for x in done if not x.get("성공")]
            msg = "검수 등록 끝 — 성공 %d건" % ok + ("\n실패 %d건:\n" % len(bad) + "\n".join("• %s %s" % (x.get("계약번호", ""), x.get("오류", "")[:80]) for x in bad) if bad else "")
            self.root.after(0, self.info, msg)
        threading.Thread(target=fn, daemon=True).start()

    # ── 복무 (근무상황 상신) ──
    def bokmu_form(self):
        c = cfg()
        now = round5(datetime.now().strftime("%H:%M"))
        end_default = c.get("work_end") or "16:20"
        tip, fr = self.popup("복무 상신 — 고르고 [상신]을 누르면 나이스에 승인요청까지 올립니다")
        g = tk.Frame(fr, bg=BG)
        g.pack(padx=12, pady=(0, 6), anchor="w")
        kind = tk.StringVar(value="조퇴")
        v_date = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        v_start = tk.StringVar(value=now)
        v_end = tk.StringVar(value=end_default)
        v_reason = tk.StringVar(value="")
        v_contact = tk.StringVar(value=c.get("contact", ""))
        v_line = tk.StringVar(value=c.get("approval_line", ""))
        f = ("Malgun Gothic", 9)

        def row(r, label, var, width=12):
            tk.Label(g, text=label, bg=BG, fg=INK, font=f).grid(row=r, column=0, sticky="w", pady=2)
            e = tk.Entry(g, textvariable=var, font=f, width=width)
            e.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=2)
            return e
        tk.Label(g, text="종류", bg=BG, fg=INK, font=f).grid(row=0, column=0, sticky="w", pady=2)
        kinds = tk.Frame(g, bg=BG)
        kinds.grid(row=0, column=1, sticky="w", padx=(8, 0))
        for i, k in enumerate(BOKMU_TYPES):      # 두 줄 × 셋 — 한 줄로 늘어놓으면 창 밖으로 나간다
            tk.Radiobutton(kinds, text=k, variable=kind, value=k, bg=BG, font=f, activebackground=BG, width=7, anchor="w").grid(row=i // 3, column=i % 3, sticky="w")
        # 날짜 — 치지 않고 단추를 눌러 달력에서 고른다
        tk.Label(g, text="날짜", bg=BG, fg=INK, font=f).grid(row=1, column=0, sticky="nw", pady=2)
        df = tk.Frame(g, bg=BG)
        df.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=2)
        db = tk.Button(df, text=date_label(v_date.get()), font=f, relief="groove", bg="white", cursor="hand2", anchor="w")
        db.pack(anchor="w")
        cal = {"box": None}

        def picked(iso):
            db.config(text=date_label(iso))
            toggle_cal()

        def toggle_cal():
            if cal["box"]:
                cal["box"].destroy(); cal["box"] = None
            else:
                cal["box"] = date_picker(df, v_date, BG, picked)
                cal["box"].pack(anchor="w", pady=(4, 2))
            self.place_tip(tip)                    # 크기가 바뀌었으니 다시 가운데로
        db.config(command=toggle_cal)
        row(2, "시작", v_start, 7)
        row(3, "종료", v_end, 7)
        e_reason = row(4, "사유", v_reason, 30)
        row(5, "연락처", v_contact, 16)
        row(6, "결재선", v_line, 24)
        hint = tk.Label(g, text="조퇴 종료 = 설정의 퇴근 시각 %s · 지각 시작 = 출근 시각 %s · 5분 단위\n결재선 = 나이스 «개인결재선»에 이름 붙여 저장해 둔 그 이름 (없으면 나이스에서 먼저 만들어 두세요)" % (end_default, c.get("work_start") or "08:40"),
                        bg=BG, fg="#8a7a68", font=("Malgun Gothic", 8), justify="left", wraplength=420)
        hint.grid(row=7, column=0, columnspan=2, sticky="w", pady=(2, 0))

        def on_kind(*_):
            k = kind.get()
            if k in ("조퇴", "병조퇴"):
                v_start.set(round5(datetime.now().strftime("%H:%M"))); v_end.set(end_default)
            elif k in ("외출", "병외출"):
                t = datetime.now(); v_start.set(round5(t.strftime("%H:%M"))); v_end.set(round5((t + timedelta(hours=1)).strftime("%H:%M")))
            else:
                v_start.set(c.get("work_start") or "08:40"); v_end.set(round5(datetime.now().strftime("%H:%M")))
        kind.trace_add("write", on_kind)
        e_reason.focus_set()
        btns = tk.Frame(fr, bg=BG)
        btns.pack(pady=(0, 10))
        tk.Button(btns, text="상신", font=("Malgun Gothic", 9, "bold"), bg="#c2417a", fg="white", relief="flat", padx=12,
                  command=lambda: self.bokmu_submit(tip, kind.get(), v_date.get().strip(), v_start.get().strip(), v_end.get().strip(), v_reason.get().strip(), v_contact.get().strip(), v_line.get().strip())).pack(side="left", padx=4)
        tk.Button(btns, text="닫기", font=f, relief="flat", padx=10, command=tip.destroy).pack(side="left", padx=4)

    def bokmu_submit(self, tip, kind, d, start, end, reason, contact, line):
        for hm in (start, end):
            if not re.fullmatch(r"\d{1,2}:\d{2}", hm):
                self.say("시각은 15:30 처럼 적어 주세요", "#c0392b", 5000); return
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            self.say("날짜는 2026-10-16 처럼 적어 주세요", "#c0392b", 5000); return
        if not reason:
            self.say("사유를 적어 주세요", "#c0392b", 5000); return
        if not re.fullmatch(r"[\d-]{9,14}", contact):
            self.say("연락처를 010-1234-5678 처럼 적어 주세요 (나이스가 요구해요)", "#c0392b", 6000); return
        if not line:
            self.say("결재선 이름을 적어 주세요 (나이스 개인결재선에 저장한 이름)", "#c0392b", 7000); return
        tip.destroy()
        self.busy = "복무 상신 중"
        c = cfg()
        if c.get("contact") != contact or c.get("approval_line") != line:      # 다음부터 기본값으로
            try:
                c["contact"], c["approval_line"] = contact, line
                CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        args = [str(PY), "-X", "utf8", str(BOKMU), "--type", kind, "--date", d, "--start", round5(start), "--end", round5(end),
                "--reason", reason, "--contact", contact, "--approval-line", line]

        def fn():
            t0 = time.time()
            try:
                with self.locks["neis"]:        # 나이스 크롬 프로필을 쓰는 동안 위젯의 나이스 데우기와 겹치지 않게
                    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                       env=ENV, cwd=str(BOKMU.parent), creationflags=NO_WINDOW, timeout=900)
                lines = [ln for ln in ((p.stdout or "") + "\n" + (p.stderr or "")).splitlines() if ln.strip()]
                ok = p.returncode == 0 and any("상신 완료" in ln for ln in lines)
                log("bokmu %s %s %s~%s" % (kind, d, start, end), ["READY" if ok else "FAIL " + " / ".join(lines[-3:])[:200]], t0)
            except Exception as e:
                ok, lines = False, [str(e)]
            finally:
                self.busy = None
            if ok:
                self.root.after(0, self.info, "상신 완료 — %s %s %s~%s\n사유: %s\n\n나이스 개인근무상황관리에서 한 번 확인하세요." % (kind, d, start, end, reason))
            else:
                self.root.after(0, self.info, "앗, 상신이 안 됐어요.\n\n" + "\n".join(lines[-4:])[:500] + "\n\n(크롬 창이 남아 있으면 거기서 직접 마무리할 수 있어요)")
        threading.Thread(target=fn, daemon=True).start()

    # ── 작은 창 ──
    def popup(self, text):
        tip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        fr = tk.Frame(tip, bg=BG, highlightbackground=LINE, highlightthickness=2)
        fr.pack()
        tk.Label(fr, text=text, font=("Malgun Gothic", 9), bg=BG, fg=INK, wraplength=420, justify="left", padx=12, pady=10).pack(anchor="w")
        self.place_tip(tip)
        tip.after(30, lambda: tip.winfo_exists() and self.place_tip(tip))   # 단추·입력칸이 다 붙은 뒤 한 번 더
        return tip, fr

    def place_tip(self, tip):
        """작은 창은 위젯 가운데 바로 아래 (한쪽으로 치우치지 않게). 밑에 자리가 없으면 위젯 위로."""
        tip.update_idletasks()
        w, h = tip.winfo_reqwidth(), tip.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = min(max(0, self.root.winfo_x() + self.root.winfo_width() // 2 - w // 2), sw - w)
        y = self.root.winfo_y() + self.root.winfo_height() + 6
        if y + h > sh - 56:
            y = max(0, self.root.winfo_y() - h - 6)
        tip.geometry("+%d+%d" % (x, y))

    def confirm(self, text, ok_label, on_ok):
        tip, fr = self.popup(text)
        row = tk.Frame(fr, bg=BG)
        row.pack(pady=(0, 10))
        tk.Button(row, text=ok_label, font=("Malgun Gothic", 9, "bold"), bg="#e5484d", fg="white", relief="flat", padx=10,
                  command=lambda: (tip.destroy(), on_ok())).pack(side="left", padx=4)
        tk.Button(row, text="닫기", font=("Malgun Gothic", 9), relief="flat", padx=10, command=tip.destroy).pack(side="left", padx=4)

    def info(self, text):
        tip, fr = self.popup(text)
        tk.Button(fr, text="확인", font=("Malgun Gothic", 9), relief="flat", command=tip.destroy).pack(pady=(0, 10))

    # ── 미리 열어두기 ──
    def warm(self, site, launch):
        if not configured() or not self.locks[site].acquire(blocking=False):
            return
        self.locks[site].release()
        self.run(site, "warm", ["--launch"] if launch else [], what="warm %s%s" % (site, " --launch" if launch else ""))

    def warm_all(self, launch):
        for site in ("neis", "edufine"):
            threading.Thread(target=self.warm, args=(site, launch), daemon=True).start()

    def loop_warm(self):
        if self.prewarm and not self.busy:
            self.warm_all(False)
        self.root.after(WARM_EVERY, self.loop_warm)

    def loop_relaunch(self):
        """닫힌 크롬을 다시 데운다 — 1분마다 포트만 보고, 없을 때만 warm --launch."""
        if self.prewarm and not self.busy and configured():
            for site, port in PORTS.items():
                if not port_alive(port):
                    threading.Thread(target=self.warm, args=(site, True), daemon=True).start()
        self.root.after(RELAUNCH_EVERY, self.loop_relaunch)

    def toggle_prewarm(self):
        self.prewarm = bool(self.prewarm_var.get())
        save_state(prewarm=self.prewarm)
        self.say("미리 열어둘게요" if self.prewarm else "미리 안 열어요")
        if self.prewarm:
            self.warm_all(True)

    # ── 설정·닫기 ──
    def open_setup(self):
        # 마우스로 고르는 설정 창 (클로드 없이 쓰는 선생님도 같은 창). 검은 창판(바로가기_설정.py)은 클로드 지시서용으로 남겨 둔다
        subprocess.Popen([str(PY.with_name("pythonw.exe")), str(HERE / "바로가기_설정창.pyw")], cwd=str(HERE), env=ENV)
        self.say("설정 창에서 마쳐 주세요", INK, 8000)

    def close_chrome(self):
        def fn():
            for site in ("neis", "edufine"):
                self.run(site, "close", what="close " + site)
            self.root.after(0, self.say, "닫았어요")
        threading.Thread(target=fn, daemon=True).start()

    def mainloop(self):
        self.tick()
        self.root.after(WARM_EVERY, self.loop_warm)
        self.root.after(RELAUNCH_EVERY, self.loop_relaunch)
        if not configured():
            self.root.after(800, lambda: self.say("오른쪽 클릭 → 인증서 설정부터 해 주세요", "#c0392b", 15000))
        elif self.prewarm:
            self.root.after(1500, lambda: self.warm_all(True))
        self.root.mainloop()


def breakaway():
    """클로드(코드 화면)가 띄운 명령은 «잡(job)»으로 묶여서, 클로드 쪽 프로세스가 끝나거나 죽으면 같이 죽는다
    (10/16 엔드투엔드 실측: 클로드 프로세스를 끊자 위젯도 사라짐). 잡 안에서 시작됐으면 잡 밖으로 나를 다시 띄우고 끝낸다."""
    if "--unjobbed" in sys.argv:
        return False
    try:
        k = ctypes.windll.kernel32
        injob = ctypes.c_int(0)
        if not k.IsProcessInJob(k.GetCurrentProcess(), None, ctypes.byref(injob)) or not injob.value:
            return False
        flags = 0x01000000 | 0x00000008 | 0x00000200          # CREATE_BREAKAWAY_FROM_JOB | DETACHED | NEW_PROCESS_GROUP
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--unjobbed"], cwd=str(HERE),
                         creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False          # 잡이 breakaway 를 안 허용하면 그냥 이대로 돈다


if __name__ == "__main__":
    if breakaway():
        sys.exit(0)
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "neis-edufine-widget")
    if ctypes.windll.kernel32.GetLastError() == 183:      # 이미 떠 있다
        sys.exit(0)
    Widget().mainloop()
