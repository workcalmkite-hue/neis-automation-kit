# -*- coding: utf-8 -*-
"""EVPN 연결 → 작업 한 번 → 끊기를 한 번에 한다. 학교 밖에서는 늘 이걸로 돌린다.

  python run_vpn.py -- python main.py 2026-09-14
  python run_vpn.py --minutes 8 -- python edufine_inspect.py

왜 한 번에 해야 하나:
  부산·전남·강원·경기 등은 VPN 이 붙어 있는 동안 일반 인터넷이 막힌다 → 그동안 클로드도 응답을 못 한다.
  그래서 «연결하고, 클로드와 대화하며 작업하고, 나중에 끊는» 방식은 안 된다. 연결·작업·끊기를
  스크립트 하나가 끝까지 해야 클로드가 다시 말할 수 있다. (서울은 VPN 중에도 인터넷이 된다 — 09-18 실측)

안전장치:
  - 작업이 시간(기본 6분 30초)을 넘기면 멈추고 끊는다
  - 이 스크립트가 강제로 죽어도(클로드 도구 시간 초과 등) 따로 떠 있는 감시자가 VPN 을 끊는다
  - 시작 전에 이미 연결돼 있었으면(선생님이 직접 연결) 끝나도 끊지 않는다
  - 마지막 줄에 RESULT_JSON {...} 을 항상 찍는다 — 클로드는 이 줄로 결과를 판단한다
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import teacher_config
import evpn

STATE = teacher_config.CONFIG_DIR / "run_vpn_state.json"


def _alive(pid: int) -> bool:
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                       creationflags=evpn._NO_WINDOW)
    return str(pid) in (r.stdout or "")


def watchdog(parent: int, deadline: float) -> None:
    """부모가 죽거나 시간이 지났는데 «우리가 연결한 VPN» 이 남아 있으면 끊는다."""
    while True:
        time.sleep(3)
        try:
            st = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            return
        if st.get("finished"):
            return
        if not _alive(parent) or time.time() > deadline:
            if st.get("we_connected"):
                evpn.disconnect()
            st["finished"] = True
            st["by_watchdog"] = True
            STATE.write_text(json.dumps(st), encoding="utf-8")
            return


def internet_ok() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("https://www.google.com/generate_204", timeout=6)
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=6.5, help="작업에 줄 시간 (기본 6.5분)")
    ap.add_argument("--watchdog", nargs=2, help=argparse.SUPPRESS)
    ap.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args()

    if a.watchdog:
        watchdog(int(a.watchdog[0]), float(a.watchdog[1]))
        return 0

    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    result = {"connected": False, "work_exit": None, "disconnected": None, "internet_after": None}
    if not cmd:
        print("사용법: python run_vpn.py -- python main.py 2026-09-14")
        return 2
    if cmd[0] in ("python", "py"):
        cmd[0] = sys.executable                   # 가상환경 파이썬을 그대로 쓴다

    already = evpn.tap_status() == "Up"
    if evpn.tap_status() == "unknown":
        print("⚠️  VPN 상태를 읽지 못했습니다 — 그대로 진행합니다")
    deadline = time.time() + a.minutes * 60 + 150      # 연결 70초 + 끊기 여유
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"we_connected": False, "finished": False}), encoding="utf-8")
    subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                      "--watchdog", str(os.getpid()), str(deadline)],
                     creationflags=evpn._NO_WINDOW | 0x00000008 | 0x00000200,   # DETACHED · 새 그룹
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)

    try:
        if already:
            print("ℹ️  VPN 이 이미 연결되어 있어 그대로 씁니다 (끝나도 끊지 않습니다)")
            result["connected"] = True
        else:
            # 연결을 «시도하기 전에» 기록 — 도중에 죽어도 감시자가 끊는다
            STATE.write_text(json.dumps({"we_connected": True, "finished": False}), encoding="utf-8")
            result["connected"] = evpn.connect()
        if result["connected"]:
            print(f"▶️  작업 시작 (최대 {a.minutes:g}분): {' '.join(cmd[1:]) if cmd[0] == sys.executable else ' '.join(cmd)}",
                  flush=True)
            try:
                r = subprocess.run(cmd, timeout=a.minutes * 60, stdin=subprocess.DEVNULL)
                result["work_exit"] = r.returncode
            except subprocess.TimeoutExpired:
                result["work_exit"] = "timeout"
                print(f"⏱  {a.minutes:g}분을 넘겨 작업을 멈췄습니다")
    finally:
        if not already:
            result["disconnected"] = evpn.disconnect()
        STATE.write_text(json.dumps({"we_connected": False, "finished": True}), encoding="utf-8")
        result["internet_after"] = internet_ok()
        print("RESULT_JSON " + json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if (result["connected"] and result["work_exit"] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
