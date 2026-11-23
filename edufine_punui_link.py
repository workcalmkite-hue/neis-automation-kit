# -*- coding: utf-8 -*-
"""구글 시트 [📮 품의 올리기]·[🕘 복무 상신] 링크 → 이 컴퓨터에서 에듀파인 품의 / 나이스 복무를 올린다.

  .\\.venv\\Scripts\\python.exe -X utf8 edufine_punui_link.py 설치     edufine-punui:// 링크를 이 컴퓨터에 등록 (한 번만)
  .\\.venv\\Scripts\\python.exe -X utf8 edufine_punui_link.py 확인     등록됐는지 · 올린 기록 보기
  .\\.venv\\Scripts\\python.exe -X utf8 edufine_punui_link.py 지우기   등록 해제
  .\\.venv\\Scripts\\python.exe -X utf8 edufine_punui_link.py 시험 "<링크>"   링크만 풀어 보고 에듀파인은 안 연다

어떻게 이어지나:
  시트(앱스스크립트)는 구글 서버에서 돌아서 학교 컴퓨터의 에듀파인을 못 만진다.
  그래서 시트는 «주문서»만 만들어 링크 안에 담는다 → edufine-punui://run/<주문서>
  크롬에서 그 링크를 누르면 윈도우가 이 파일을 부르고, 이 파일이 edufine_punui.py 로 실제 입력을 한다.
  → 컴퓨터에 구글 로그인·토큰·웹앱이 필요 없다. 시트 주인이 누구든 같은 방식으로 된다.
  (조우열 연구위원님 «과학 물품 구매» 시트의 edufine-punui:// 방식을 따름. 그분은 숨은 탭에 주문서를 두고
   토큰으로 읽었고, 여기서는 링크에 담아 토큰을 없앴다)

주문서 종류 (kind):
  - 없음/"품의"  edufine_punui.py 채우기 <json> [--저장] [--결재요청]
  - "복무"       줄마다 main_bokmu.py --type 조퇴 --date … --start … --end … --reason … [--dry-run]
                 연락처·개인결재선은 시트에 안 싣는다 — 이 컴퓨터 설정(contact·approval_line)에서 읽는다

안전장치 (두 번 올라가는 사고 막기):
  - 주문서마다 번호가 있고, 한 번 시작한 번호는 다시 안 돌린다 (%LOCALAPPDATA%\\neis-automation\\punui_link_done.json)
  - 미리보기 뒤 15분이 지난 링크는 안 돌린다 → 시트에서 메뉴를 다시 눌러 새 링크를 만든다
  - 업무포털이 바로 안 열리면(학교 밖) run_vpn.py 로 EVPN 을 붙였다가 끝나면 끊는다
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import teacher_config

HERE = Path(__file__).resolve().parent
SCHEME = "edufine-punui"
KEY = r"Software\Classes\\" + SCHEME
DONE = teacher_config.CONFIG_DIR / "punui_link_done.json"
PLANS = teacher_config.CONFIG_DIR / "punui_link"
MAX_AGE_MIN = 15
SCOPE_ARGS = {"화면 채우기만": [], "저장까지": ["--저장"], "결재올림까지": ["--저장", "--결재요청"]}
BOKMU_SCOPE = {"입력만": ["--dry-run"], "상신까지": []}
BOKMU_TYPES = ("지각", "조퇴", "외출", "병지각", "병조퇴", "병외출", "육아시간")
BOKMU_MAX = 5


def venv_python() -> str:
    p = HERE / ".venv" / "Scripts" / "python.exe"
    return str(p) if p.exists() else sys.executable


# ── 링크 등록 ───────────────────────────────────────────────
def install() -> int:
    import winreg
    cmd = '"%s" -X utf8 "%s" "%%1"' % (venv_python(), Path(__file__).resolve())
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:에듀파인 품의 올리기")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY + r"\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    print("✅ 이 컴퓨터에 %s:// 링크를 등록했습니다 (지금 사용자만, 관리자 권한 필요 없음)" % SCHEME)
    print("   실행: " + cmd)
    print("   시트에서 [📮 품의 올리기] 를 누르면 크롬이 «열기» 를 묻습니다 → [열기]")
    return 0


def uninstall() -> int:
    import winreg
    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, KEY + sub)
        except FileNotFoundError:
            pass
    print("✅ %s:// 링크 등록을 지웠습니다" % SCHEME)
    return 0


def status() -> int:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY + r"\shell\open\command") as k:
            print("✅ 등록됨: " + winreg.QueryValueEx(k, "")[0])
    except Exception:
        print("❌ 등록 안 됨 — 설치 를 먼저 하세요")
    done = load_done()
    print("올린 기록 %d건" % len(done))
    for i, v in list(done.items())[-5:]:
        print("  %s  %s  %s  %s" % (i, v.get("시각", ""), v.get("상태", ""), v.get("품의번호", "")))
    print("RESULT_JSON=" + json.dumps({"등록": "확인", "기록": len(done)}, ensure_ascii=False))
    return 0


# ── 주문서 풀기 ─────────────────────────────────────────────
def decode(link: str) -> dict:
    m = re.match(r"^%s:/*run/([A-Za-z0-9_-]+)/?$" % SCHEME, link.strip())
    if not m:
        raise SystemExit("링크 모양이 다릅니다: %s…" % link[:60])
    raw = m.group(1)
    data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
    kind = data.setdefault("kind", "품의")
    if data.get("v") != 1 or not data.get("id") or kind not in ("품의", "복무"):
        raise SystemExit("시트 버전이 이 프로그램과 맞지 않습니다 — 키트를 git pull 하거나 시트 사본을 새로 받으세요.")
    if kind == "품의":
        if not isinstance(data.get("plan"), dict):
            raise SystemExit("품의 주문서에 plan 이 없습니다.")
        if data.get("범위") not in SCOPE_ARGS:
            raise SystemExit("올리기 범위가 이상합니다: %s" % data.get("범위"))
        return data
    if data.get("범위") not in BOKMU_SCOPE:
        raise SystemExit("복무 범위가 이상합니다: %s" % data.get("범위"))
    rows = data.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= BOKMU_MAX:
        raise SystemExit("복무 줄은 1~%d개여야 합니다." % BOKMU_MAX)
    for r in rows:
        ok = (r.get("종류") in BOKMU_TYPES and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(r.get("날짜", "")))
              and re.fullmatch(r"\d{2}:\d{2}", str(r.get("시작", ""))) and re.fullmatch(r"\d{2}:\d{2}", str(r.get("끝", "")))
              and str(r.get("사유", "")).strip() and r["시작"] < r["끝"])
        if not ok:
            raise SystemExit("복무 줄 값이 이상합니다: %s" % r)
    return data


def load_done() -> dict:
    try:
        return json.loads(DONE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mark(job_id: str, **kw) -> None:
    d = load_done()
    d.setdefault(job_id, {}).update(kw)
    DONE.parent.mkdir(parents=True, exist_ok=True)
    DONE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def show(data: dict) -> None:
    if data["kind"] == "복무":
        print("━" * 60)
        print("복무 주문서 %s  (범위: %s)" % (data["id"], data["범위"]))
        for r in data["rows"]:
            print("  · %s  %s %s~%s  %s%s" % (r["종류"], r["날짜"], r["시작"], r["끝"], r["사유"],
                                             "  [결재선 %s]" % r["결재선"] if r.get("결재선") else ""))
        print("━" * 60)
        return
    p = data["plan"]
    print("━" * 60)
    print("품의 주문서 %s  (올리기 범위: %s)" % (data["id"], data["범위"]))
    print("제목  " + p.get("제목", ""))
    print("예산  %s · 과제카드 %s · %s · 결재선 %s" % (p.get("예산"), p.get("과제카드") or "-",
                                                p.get("공개여부"), p.get("결재선") or "-"))
    tot = 0
    for it in p.get("품목", []):
        tot += it["수량"] * it["단가"]
        print("  · %s %s  %d개 × %s원" % (it["내용"], it.get("규격", ""), it["수량"], "{:,}".format(it["단가"])))
    print("합계  %s원" % "{:,}".format(tot))
    print("━" * 60)


def portal_direct() -> bool:
    """업무포털이 VPN 없이 열리면 학교 안이다."""
    import urllib.request
    import evpn
    try:
        return urllib.request.urlopen(evpn.portal_url(), timeout=8).status == 200
    except Exception:
        return False


def run(link: str, dry: bool = False) -> int:
    data = decode(link)
    show(data)
    job = data["id"]
    if dry:
        print("🧪 시험 — 링크를 풀어 보기만 했습니다. 에듀파인·나이스는 열지 않았습니다.")
        n = len(data["plan"].get("품목", [])) if data["kind"] == "품의" else len(data["rows"])
        print("RESULT_JSON=" + json.dumps({"시험": "풀림", "주문서": job, "종류": data["kind"], "줄": n},
                                          ensure_ascii=False))
        return 0

    age = (time.time() * 1000 - float(data.get("ts", 0))) / 60000
    if age > MAX_AGE_MIN:
        print("⛔ 미리보기를 띄운 지 %d분이 지났습니다. 시트에서 메뉴를 다시 눌러 새로 올려 주세요." % age)
        return 1
    prev = load_done().get(job)
    if prev:
        print("⛔ 이 주문서(%s)는 이미 %s 에 시작했습니다 (상태: %s). 두 번 올라가지 않게 멈춥니다."
              % (job, prev.get("시각"), prev.get("상태")))
        print("   다시 올려야 하면 에듀파인 품의목록을 먼저 보고, 시트에서 메뉴를 다시 눌러 새 링크로 올리세요.")
        return 1
    # 시트 «화면: 작게» → 크롬을 화면 밖에 띄운다 (edufine_common·main_bokmu 가 NEIS_HIDE 를 읽는다)
    if data.get("화면") == "작게":
        os.environ["NEIS_HIDE"] = "1"
        print("🫥 화면: 작게 — 크롬은 화면 밖에서 돕니다. 진행은 이 검은 창에 찍힙니다"
              + (" (결재올림의 기안기 창은 잠깐 뜹니다)" if data.get("범위") == "결재올림까지" else ""))
    title = data["plan"].get("제목") if data["kind"] == "품의" else "복무 %d건" % len(data["rows"])
    mark(job, 시각=datetime.now().strftime("%m-%d %H:%M"), 상태="진행중", 제목=title)
    if data["kind"] == "복무":
        return run_bokmu(data)

    if any(it.get("링크") for it in data["plan"].get("품목", [])):
        ok = fill_links(data["plan"])
        if not ok:
            mark(job, 상태="❌", 결과={"이유": "링크 읽기 실패 또는 선생님이 멈춤"})
            print("RESULT_JSON=" + json.dumps({"주문서": job, "상태": "❌", "이유": "링크"}, ensure_ascii=False))
            return 1

    PLANS.mkdir(parents=True, exist_ok=True)
    plan_file = PLANS / ("품의_%s.json" % job)
    plan_file.write_text(json.dumps(data["plan"], ensure_ascii=False, indent=1), encoding="utf-8")

    work = [venv_python(), "-X", "utf8", str(HERE / "edufine_punui.py"), "채우기", str(plan_file)] + SCOPE_ARGS[data["범위"]]
    if portal_direct():
        print("🏫 업무포털이 바로 열립니다 — VPN 없이 갑니다")
        cmd = work
    else:
        print("🌐 학교 밖입니다 — EVPN 을 붙였다가 끝나면 끊습니다 (2차 인증이 뜨면 직접 해 주세요)")
        cmd = [venv_python(), "-X", "utf8", str(HERE / "run_vpn.py"), "--minutes", "10", "--"] + work
    print("\n지금부터 몇 분 동안 창이 뜨고 마우스가 저절로 움직입니다.")
    print("그동안 마우스·키보드를 건드리지 말고 기다려 주세요.\n", flush=True)

    result = {}
    # 검은 창은 닫으면 기록이 사라진다 — 같은 내용을 파일에도 남긴다 (나중에 확인·원격 점검용)
    log = (PLANS / ("품의_%s.log" % job)).open("w", encoding="utf-8")
    p = subprocess.Popen(cmd, cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    for line in p.stdout:
        print(line, end="", flush=True)
        log.write(line)
        log.flush()
        if line.startswith("RESULT_JSON="):
            try:
                result = json.loads(line.split("=", 1)[1])
            except Exception:
                pass
    p.wait()

    num = result.get("품의번호", "")
    if result.get("결재요청") == "완료" or (data["범위"] == "저장까지" and result.get("저장") == "완료"):
        state = "✅"
    elif result.get("저장") in ("완료", "모름") or result.get("결재요청") in ("모름", "멈춤"):
        state = "⚠️"           # 저장은 됐을 수 있다 — 다시 돌리면 두 번 올라간다
    elif data["범위"] == "화면 채우기만" and result.get("채움"):
        state = "✅"
    else:
        state = "❌"
    mark(job, 상태=state, 품의번호=num, 결과=result)
    print("\n" + "━" * 60)
    if state == "✅":
        print("✅ 끝났습니다%s. 시트 «결과» 칸에 ✅ %s 를 적어 두세요." % (" — 품의번호 " + num if num else "", num))
    elif state == "⚠️":
        print("⚠️ 결과가 확실하지 않습니다%s. 다시 올리지 말고 에듀파인 품의목록부터 보세요." % (" (품의번호 " + num + ")" if num else ""))
    else:
        print("❌ 저장 전에 멈췄습니다: %s" % (result.get("이유") or result.get("오류") or "위 기록을 보세요"))
        print("   시트에서 고친 뒤 메뉴를 다시 누르면 새 링크로 다시 올릴 수 있습니다.")
    final = "RESULT_JSON=" + json.dumps({"주문서": job, "상태": state, "품의번호": num}, ensure_ascii=False)
    print(final)
    log.write(final + "\n")
    log.close()
    print("기록: %s" % log.name)
    return 0 if state == "✅" else 1


# ── 쇼핑몰 링크 → 품명·단가 ─────────────────────────────────
def korean_money(n: int) -> str:
    """164650 → 금일십육만사천육백오십원 (시트 한글금액() 과 같은 규칙)"""
    digit = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
    small, big = ["", "십", "백", "천"], ["", "만", "억", "조"]
    out, g = "", 0
    while n > 0:
        part, t, i = n % 10000, "", 0
        while part > 0:
            d = part % 10
            if d:
                t = digit[d] + small[i] + t
            part //= 10
            i += 1
        if t:
            out = t + big[g] + out
        n //= 10000
        g += 1
    return "금" + out + "원"


def rebuild_overview(plan: dict) -> None:
    """링크로 품명·단가가 바뀌었으면 개요·결재선을 시트와 같은 규칙으로 다시 만든다."""
    src = plan.get("개요재료") or {}
    items = plan["품목"]
    total = sum(it["수량"] * it["단가"] for it in items)
    goods = [it for it in items if it["내용"] != "배송비"] or items
    lines = []
    if src.get("관련"):
        lines.append("관련: " + src["관련"])
    lines.append("품명: " + goods[0]["내용"] + (" 외 %d개" % (len(goods) - 1) if len(goods) > 1 else ""))
    lines.append("금액: 금%s원(%s).  끝." % ("{:,}".format(total), korean_money(total)))
    head = src.get("머리말") or plan["개요"].split("\n")[0]
    plan["개요"] = "\n".join([head] + ["%d. %s" % (i + 1, t) for i, t in enumerate(lines)])
    if not plan.get("결재선직접") and plan.get("결재선표"):
        line = ""
        rules = [r for r in plan["결재선표"] if str(r[1]).strip()]
        for lim, name in sorted([r for r in rules if r[0] not in ("", None)], key=lambda r: float(r[0])):
            if total < float(lim):
                line = name
                break
        else:
            rest = [r for r in rules if r[0] in ("", None)]
            line = rest[0][1] if rest else ""
        plan["결재선"] = line


def fill_links(plan: dict) -> bool:
    """품목에 «링크» 가 있으면 shop_read.py 로 읽어 비어 있는 품명·단가를 채운다. 선생님 확인을 받는다."""
    need = [it for it in plan["품목"] if it.get("링크") and (not it.get("내용") or not it.get("단가"))]
    if not need:
        return True
    print("🔗 쇼핑몰 링크 %d개를 읽습니다 (화면 밖 크롬, 한 개에 5~8초)…" % len(need), flush=True)
    r = subprocess.run([venv_python(), "-X", "utf8", str(HERE / "shop_read.py")] + [it["링크"] for it in need],
                       cwd=str(HERE), capture_output=True, text=True, encoding="utf-8", errors="replace")
    got = []
    for line in (r.stdout or "").splitlines():
        if line.startswith("RESULT_JSON="):
            try:
                got = json.loads(line.split("=", 1)[1])
            except Exception:
                pass
    by = {g.get("링크"): g for g in got}
    bad = []
    for it in need:
        g = by.get(it["링크"]) or {}
        price = g.get("1개당가격") or g.get("할인가")      # 쿠팡은 1개당 가격을 단가로
        if not g.get("품명") or not price or g.get("응답코드") not in (200, None) or g.get("오류"):
            bad.append("%s — %s" % (it["링크"][:60], g.get("오류") or "응답 %s" % g.get("응답코드")))
            continue
        if not it.get("내용"):
            it["내용"] = re.sub(r"\s+", " ", g["품명"]).strip()[:50]
        if not it.get("단가"):
            it["단가"] = int(price)
            it["_링크가격"] = True
    if bad:
        print("❌ 링크를 못 읽었습니다 — 시트에 물품·단가를 직접 적고 다시 올려 주세요:")
        for b in bad:
            print("   · " + b)
        return False
    for it in plan["품목"]:
        it.pop("링크", None)
    rebuild_overview(plan)
    print("━" * 60)
    print("링크에서 읽은 값으로 바꾼 품의:")
    for it in plan["품목"]:
        print("  · %s  %d개 × %s원%s" % (it["내용"], it["수량"], "{:,}".format(it["단가"]),
                                       "  ← 링크" if it.pop("_링크가격", False) else ""))
    print(plan["개요"])
    print("결재선  %s" % (plan.get("결재선") or "-"))
    print("━" * 60)
    if os.environ.get("PUNUI_NO_PAUSE") == "1":
        return True
    ans = input("이대로 올릴까요? [엔터 = 올리기 / n = 멈춤] ").strip().lower()
    return ans not in ("n", "ㅜ", "아니", "no")


# ── 복무 ────────────────────────────────────────────────────
def bokmu_rows(data: dict) -> list:
    """줄마다 main_bokmu.py 를 한 번씩. 앞 줄이 확실하지 않으면(⚠️) 뒤 줄은 돌리지 않는다."""
    out = []
    stop = False
    for r in data["rows"]:
        if stop:
            out.append({**r, "상태": "건너뜀"})
            continue
        cmd = [venv_python(), "-X", "utf8", str(HERE / "main_bokmu.py"), "--type", r["종류"], "--date", r["날짜"],
               "--start", r["시작"], "--end", r["끝"], "--reason", r["사유"]] + BOKMU_SCOPE[data["범위"]]
        if str(r.get("자녀", "")).strip():           # 육아시간 대상 자녀 (한 명이면 비워도 된다)
            cmd += ["--child", str(r["자녀"]).strip()]
        if str(r.get("결재선", "")).strip():         # 그 줄만 다른 개인결재선 (비우면 설정의 approval_line)
            cmd += ["--approval-line", str(r["결재선"]).strip()]
        print("\n▶️  %s %s %s~%s" % (r["종류"], r["날짜"], r["시작"], r["끝"]), flush=True)
        code = subprocess.run(cmd, cwd=str(HERE), stdin=subprocess.DEVNULL).returncode
        if code == 0:
            st = "✅ 입력만" if data["범위"] == "입력만" else "✅ 상신"
        else:
            # main_bokmu 의 1 은 «안 올라갔다» 가 아니라 «확인 필요» — 다시 돌리면 두 번 상신될 수 있다
            st = "⚠️ 확인 필요"
            stop = True
        out.append({**r, "상태": st})
    return out


def run_bokmu(data: dict) -> int:
    PLANS.mkdir(parents=True, exist_ok=True)
    f = PLANS / ("복무_%s.json" % data["id"])
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n지금부터 몇 분 동안 창이 뜨고 마우스가 저절로 움직입니다.")
    print("그동안 마우스·키보드를 건드리지 말고 기다려 주세요.\n", flush=True)
    if portal_direct():
        print("🏫 업무포털이 바로 열립니다 — VPN 없이 갑니다")
        results = bokmu_rows(data)
    else:
        # VPN 은 한 번만 붙였다가 끊는다 — 줄 전부를 VPN 안에서 돌리고 결과는 파일로 받는다
        print("🌐 학교 밖입니다 — EVPN 을 붙였다가 끝나면 끊습니다 (2차 인증이 뜨면 직접 해 주세요)")
        res_file = PLANS / ("복무_%s_결과.json" % data["id"])
        cmd = [venv_python(), "-X", "utf8", str(HERE / "run_vpn.py"), "--minutes", str(4 * len(data["rows"]) + 2), "--",
               venv_python(), "-X", "utf8", str(Path(__file__).resolve()), "_복무실행", str(f), str(res_file)]
        subprocess.run(cmd, cwd=str(HERE), stdin=subprocess.DEVNULL)
        try:
            results = json.loads(res_file.read_text(encoding="utf-8"))
        except Exception:
            results = [{**r, "상태": "⚠️ 확인 필요"} for r in data["rows"]]
    state = "✅" if all(r["상태"].startswith("✅") for r in results) else "⚠️"
    mark(data["id"], 상태=state, 결과=results)
    print("\n" + "━" * 60)
    for r in results:
        print("%s  %s %s %s~%s" % (r["상태"], r["종류"], r["날짜"], r["시작"], r["끝"]))
    if state != "✅":
        print("⚠️ 확인이 필요한 줄이 있습니다. 다시 올리지 말고 나이스 «개인근무상황관리» 목록부터 보세요.")
    print("시트 메뉴 «✍️ 복무 결과 적기» 로 결과를 적어 두세요.")
    print("RESULT_JSON=" + json.dumps({"주문서": data["id"], "상태": state,
                                      "줄": [r["상태"] for r in results]}, ensure_ascii=False))
    return 0 if state == "✅" else 1


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    a = args[0]
    if a.startswith(SCHEME + ":"):
        try:
            code = run(a)
        except SystemExit as e:
            print("⛔ %s" % e)
            code = 1
        except Exception as e:
            print("❌ %s: %s" % (type(e).__name__, e))
            code = 1
        if os.environ.get("PUNUI_NO_PAUSE") != "1":
            input("\n엔터를 누르면 이 창이 닫힙니다…")
        return code
    if a == "설치":
        return install()
    if a == "지우기":
        return uninstall()
    if a == "확인":
        return status()
    if a == "_복무실행" and len(args) > 2:        # run_vpn 안에서 부르는 내부 단계
        data = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        res = bokmu_rows(data)
        Path(args[2]).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        return 0 if all(r["상태"].startswith("✅") for r in res) else 1
    if a == "시험" and len(args) > 1:
        return run(args[1], dry=True)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
