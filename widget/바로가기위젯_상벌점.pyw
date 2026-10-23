# -*- coding: utf-8 -*-
"""바로가기 위젯 «상벌점판» — 기본 위젯(바로가기위젯.pyw)에 [상벌점] 단추만 더한 학교 전용판.

  .\\.venv\\Scripts\\pythonw.exe widget\\바로가기위젯_상벌점.pyw

기본 위젯 파일은 그대로 두고 여기서 불러와 덧붙인다 (기본판을 쓰는 다른 학교 선생님께는 아무 영향이 없다).
바로가기위젯_상벌점_설치.cmd 로 깔면 %LOCALAPPDATA%\\neis-automation\\ealimi\\enabled 표시 파일이 생기고,
설정 창·바탕화면 바로가기가 이 파일을 띄운다.

[상벌점]: 학번(여러 명은 띄어 쓰기) + 상점/벌점/상쇄 + 점수 → [등록]. e알리미에 창 없이 1초 안에 들어간다 (상벌점_엔진.py give).
처음 한 번: 오른쪽 클릭 «상벌점(e알리미) 연결» → 아이디·암호 → 크롬 창에서 폰 승인 → 명렬·벌점표 받기(1~2분).
"""
import ctypes
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("바로가기위젯", str(HERE / "바로가기위젯.pyw"),
                                               loader=importlib.machinery.SourceFileLoader("바로가기위젯", str(HERE / "바로가기위젯.pyw")))
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)

POINTS = HERE / "상벌점_엔진.py"
DATA = W.BASE / "ealimi"
TEAL = "#0e7490"
W.BTNS.append(("상벌점", "points", "form", TEAL))
TYPES = [("상점", "상점"), ("벌점", "벌점"), ("상쇄", "벌점차감")]
DEFAULTS = {"상점": "수업 태도가 바람직하여 다른 학생의 모범이 될 때", "벌점": "과제 이행 상태 불량", "벌점차감": "벌점상쇄"}


def load(name, default):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except Exception:
        return default


def has_login():
    try:
        import keyring
        c = keyring.get_credential("ealimi-kit", None)
        return bool(c and c.username)
    except Exception:
        return False


class PointsWidget(W.Widget):
    def __init__(self):
        super().__init__()
        self.locks["points"] = threading.Lock()
        self.m.insert_separator(3)
        self.m.insert_command(4, label="상벌점(e알리미) 연결", command=self.ealimi_setup)
        self.m.insert_command(5, label="상벌점 명렬 다시 받기", command=self.ealimi_sync)

    # ── 엔진 ──
    def points_run(self, args, on_step=None, timeout=900):
        t0 = time.time()
        seen, last = [], ""
        with self.locks["points"]:
            p = subprocess.Popen([str(W.PY), "-X", "utf8", str(POINTS), *args], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                 env=W.ENV, cwd=str(HERE), creationflags=W.NO_WINDOW)
            for line in p.stdout:
                line = line.strip()
                seen.append(line)
                if line.startswith("STEP ") and on_step:
                    on_step(line[5:].split("  (")[0])
                elif line.startswith(("READY", "FAIL")):
                    last = line
            p.wait(timeout=timeout)
        W.log("points " + args[0], seen, t0)
        return last

    def click(self, site, cmd, text):
        if site != "points":
            return super().click(site, cmd, text)
        if self.busy:
            return
        if not has_login() or not (DATA / "roster.json").exists():
            return self.ealimi_setup()
        self.points_form()

    # ── 연결 (처음 한 번) ──
    def ealimi_setup(self):
        tip, fr = self.popup("e알리미 연결 — 처음 한 번만 합니다.\n아이디·암호는 이 컴퓨터의 «Windows 자격 증명 관리자»에만 저장돼요.\n"
                             "[연결]을 누르면 크롬 창이 뜨고, 폰으로 «인증 요청»이 오면 승인해 주세요.")
        g = tk.Frame(fr, bg=W.BG)
        g.pack(padx=12, pady=(0, 6), anchor="w")
        f = ("Malgun Gothic", 9)
        try:
            import keyring
            c = keyring.get_credential("ealimi-kit", None)
            old_id = c.username if c else ""
        except Exception:
            old_id = ""
        v_id, v_pw = tk.StringVar(value=old_id), tk.StringVar()
        tk.Label(g, text="e알리미 아이디", bg=W.BG, font=f).grid(row=0, column=0, sticky="w", pady=2)
        e_id = tk.Entry(g, textvariable=v_id, font=f, width=22)
        e_id.grid(row=0, column=1, padx=(8, 0), pady=2)
        tk.Label(g, text="암호", bg=W.BG, font=f).grid(row=1, column=0, sticky="w", pady=2)
        e_pw = tk.Entry(g, textvariable=v_pw, font=f, width=22, show="●")
        e_pw.grid(row=1, column=1, padx=(8, 0), pady=2)
        (e_pw if old_id else e_id).focus_set()
        btns = tk.Frame(fr, bg=W.BG)
        btns.pack(pady=(0, 10))

        def go():
            uid, pw = v_id.get().strip(), v_pw.get()
            if not uid or not pw:
                return self.say("아이디와 암호를 모두 넣어 주세요", "#c0392b", 5000)
            try:
                import keyring
                c = keyring.get_credential("ealimi-kit", None)
                if c and c.username != uid:
                    keyring.delete_password("ealimi-kit", c.username)
                keyring.set_password("ealimi-kit", uid, pw)
            except Exception as e:
                return self.say("암호를 저장하지 못했어요: %s" % str(e)[:60], "#c0392b", 8000)
            tip.destroy()
            self.ealimi_connect()
        tk.Button(btns, text="연결", font=("Malgun Gothic", 9, "bold"), bg=TEAL, fg="white", relief="flat", padx=12,
                  command=go).pack(side="left", padx=4)
        tk.Button(btns, text="닫기", font=f, relief="flat", padx=10, command=tip.destroy).pack(side="left", padx=4)
        tip.bind("<Return>", lambda e: go())
        tip.after(50, tip.focus_force)

    def ealimi_connect(self):
        self.busy = "e알리미 로그인 — 폰 승인을 기다려요"
        DATA.mkdir(parents=True, exist_ok=True)

        def fn():
            try:
                last = self.points_run(["login"], on_step=lambda s: setattr(self, "busy", s))
                if not last.startswith("READY"):
                    self.root.after(0, self.info, "앗, e알리미 연결이 안 됐어요.\n\n" + last[5:] +
                                    "\n\n아이디·암호를 확인하고 오른쪽 클릭 → «상벌점(e알리미) 연결»을 다시 해 주세요.")
                    return
                self.busy = "명렬·벌점표 받는 중 (1~2분)"
                last = self.points_run(["sync"], on_step=lambda s: setattr(self, "busy", s))
            finally:
                self.busy = None
            if last.startswith("READY"):
                self.root.after(0, self.info, "e알리미 연결 끝!\n%s\n\n이제 [상벌점]을 누르고 학번만 넣으면 돼요." % last[6:])
            else:
                self.root.after(0, self.info, "로그인은 됐는데 명렬을 못 받았어요.\n\n" + last[5:])
        threading.Thread(target=fn, daemon=True).start()

    def ealimi_sync(self):
        if self.busy:
            return
        self.busy = "명렬·벌점표 받는 중 (1~2분)"

        def fn():
            try:
                last = self.points_run(["sync"], on_step=lambda s: setattr(self, "busy", s))
            finally:
                self.busy = None
            if last.startswith("READY"):
                self.root.after(0, self.say, "다시 받았어요 — " + last[6:][:60], "#2f7d4f", 8000)
            else:
                self.root.after(0, self.info, "앗, 명렬을 못 받았어요.\n\n" + last[5:])
        threading.Thread(target=fn, daemon=True).start()

    # ── 상벌점 입력 ──
    def points_form(self):
        ros = {s["sid"]: s for s in load("roster.json", {}).get("students", [])}
        types = load("catalog.json", {}).get("types", {})
        f = ("Malgun Gothic", 9)
        tip, fr = self.popup("상벌점 — 학번(여러 명은 띄어 쓰기) · 종류 · 점수를 고르고 [등록]")
        g = tk.Frame(fr, bg=W.BG)
        g.pack(padx=12, pady=(0, 6), anchor="w")
        v_sid, v_type, v_point, v_item = tk.StringVar(), tk.StringVar(value="벌점"), tk.StringVar(value="1"), tk.StringVar()

        tk.Label(g, text="학번", bg=W.BG, font=f).grid(row=0, column=0, sticky="w", pady=2)
        e_sid = tk.Entry(g, textvariable=v_sid, font=("Malgun Gothic", 11), width=24)
        e_sid.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=2)
        who = tk.Label(g, text="예) 10701  또는  10701 10702", bg=W.BG, fg="#8a7a68", font=f, justify="left", wraplength=300)
        who.grid(row=1, column=1, sticky="w", padx=(8, 0))

        tk.Label(g, text="종류", bg=W.BG, font=f).grid(row=2, column=0, sticky="w", pady=2)
        kinds = tk.Frame(g, bg=W.BG)
        kinds.grid(row=2, column=1, sticky="w", padx=(8, 0))
        for i, (label, code) in enumerate(TYPES):
            tk.Radiobutton(kinds, text=label, variable=v_type, value=code, bg=W.BG, font=f,
                           activebackground=W.BG).grid(row=0, column=i, sticky="w", padx=(0, 6))
        tk.Label(g, text="점수", bg=W.BG, font=f).grid(row=3, column=0, sticky="w", pady=2)
        pts = tk.Frame(g, bg=W.BG)
        pts.grid(row=3, column=1, sticky="w", padx=(8, 0))
        tk.Label(g, text="항목", bg=W.BG, font=f).grid(row=4, column=0, sticky="w", pady=2)
        cb = ttk.Combobox(g, textvariable=v_item, state="readonly", width=40, font=f)
        cb.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=2)

        def allowed():
            return sorted({int(p) for x in types.get(v_type.get(), []) for p in x["points"]})

        def draw_points(*_):
            for w in pts.winfo_children():
                w.destroy()
            ps = allowed() or [1]
            if int(v_point.get() or 1) not in ps:
                v_point.set(str(ps[0]))
            for p in ps[:10]:
                tk.Radiobutton(pts, text=str(p), variable=v_point, value=str(p), bg=W.BG, font=f,
                               activebackground=W.BG).pack(side="left")
            draw_items()

        def draw_items(*_):
            t, p = v_type.get(), v_point.get()
            names = [x["item"] for x in types.get(t, []) if p in x["points"]]
            cb["values"] = names
            want = DEFAULTS.get(t)
            v_item.set(want if want in names else (names[0] if names else ""))
            self.place_tip(tip)

        def lookup(*_):
            sids = [s for s in re.split(r"[,\s]+", v_sid.get().strip()) if s]
            if not sids:
                who.config(text="예) 10701  또는  10701 10702", fg="#8a7a68")
                return
            parts = []
            for s in sids:
                st = ros.get(s)
                parts.append("%s %s" % (s, st["name"]) if st else "%s (없음)" % s)
            who.config(text=" · ".join(parts), fg="#c0392b" if any(s not in ros for s in sids) else "#2f7d4f")
        v_sid.trace_add("write", lookup)
        v_type.trace_add("write", draw_points)
        v_point.trace_add("write", draw_items)
        draw_points()
        e_sid.focus_set()

        btns = tk.Frame(fr, bg=W.BG)
        btns.pack(pady=(0, 10))

        def go():
            sids = [s for s in re.split(r"[,\s]+", v_sid.get().strip()) if s]
            if not sids:
                return self.say("학번을 넣어 주세요", "#c0392b", 4000)
            miss = [s for s in sids if s not in ros]
            if miss:
                return self.say("명렬에 없는 학번: " + ", ".join(miss), "#c0392b", 6000)
            if not v_item.get():
                return self.say("그 점수가 되는 항목이 없어요", "#c0392b", 5000)
            tip.destroy()
            self.points_submit(sids, v_type.get(), v_point.get(), v_item.get(), ros)
        tk.Button(btns, text="등록", font=("Malgun Gothic", 9, "bold"), bg=TEAL, fg="white", relief="flat", padx=12,
                  command=go).pack(side="left", padx=4)
        tk.Button(btns, text="닫기", font=f, relief="flat", padx=10, command=tip.destroy).pack(side="left", padx=4)
        e_sid.bind("<Return>", lambda e: go())
        tip.bind("<Escape>", lambda e: tip.destroy())
        tip.after(50, lambda: (tip.focus_force(), e_sid.focus_set()))

    def points_submit(self, sids, typ, point, item, ros):
        label = {"벌점차감": "상쇄"}.get(typ, typ)
        self.busy = "상벌점 등록 중"

        def fn():
            try:
                last = self.points_run(["give", "--sid", ",".join(sids), "--type", typ, "--point", str(point),
                                        "--item", item], timeout=120)
            finally:
                self.busy = None
            names = ", ".join(ros[s]["name"] for s in sids)
            if last.startswith("READY"):
                self.root.after(0, self.say, "✔ %s %s %s점" % (names[:24], label, point), "#2f7d4f", 7000)
            else:
                self.root.after(0, self.info, "앗, 등록이 안 됐을 수 있어요.\n%s %s %s점\n\n%s" % (names, label, point, last[5:]))
        threading.Thread(target=fn, daemon=True).start()

    def mainloop(self):
        if (DATA / "enabled").exists() and not has_login():
            self.root.after(2500, self.ealimi_setup)        # 설치 직후 — 연결 창을 먼저 띄운다
        super().mainloop()


def breakaway():
    """기본 위젯과 같은 이유(클로드 잡 밖으로). 다시 띄울 때 «이 파일»을 띄워야 해서 따로 둔다."""
    if "--unjobbed" in sys.argv:
        return False
    try:
        k = ctypes.windll.kernel32
        injob = ctypes.c_int(0)
        if not k.IsProcessInJob(k.GetCurrentProcess(), None, ctypes.byref(injob)) or not injob.value:
            return False
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--unjobbed"], cwd=str(HERE),
                         creationflags=0x01000000 | 0x00000008 | 0x00000200, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if breakaway():
        sys.exit(0)
    if W.single_instance():    # 이미 떠 있다 → 그 위젯을 앞으로 꺼내고 나는 끝 (기본 위젯과 같은 뮤텍스·이벤트)
        sys.exit(0)
    PointsWidget().mainloop()
