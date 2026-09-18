# -*- coding: utf-8 -*-
"""K-에듀파인 지출품의 — 목록 열기 · 지난 품의 읽기 · 화면 채우기 · (말할 때만) 저장.

  .\\.venv\\Scripts\\python.exe -X utf8 edufine_punui.py 열기                 로그인해서 품의목록까지 (3단계)
  .\\.venv\\Scripts\\python.exe -X utf8 edufine_punui.py 읽기 --건수 5        지난 품의 5건 읽기 (4단계, 읽기만)
  .\\.venv\\Scripts\\python.exe -X utf8 edufine_punui.py 채우기 품의.json     [신규] 화면 채우고 멈춤 (6단계, 저장 안 함)
  .\\.venv\\Scripts\\python.exe -X utf8 edufine_punui.py 채우기 품의.json --저장   채우고 저장까지 (7단계)

★★ [결재요청] 을 누르는 코드는 이 파일에 «없다». 한 번 올리면 기안자가 되돌릴 수 없다(2026-09-17 실측).
   결재요청은 선생님이 화면을 보고 직접 누른다. 이 파일에 그 기능을 넣지 마라.
★  --저장 은 선생님이 제목·개요·예산·품목·합계를 보고 «저장해» 라고 한 뒤에만 붙인다.
★  읽기 는 [신규]·[저장]·[삭제] 를 하나도 누르지 않는다.

품의.json 모양 (이 폴더에 만든다):
  {
    "제목": "[연습] 2026학년도 기술 수업 활동 물품 구입",
    "개요": "2026학년도 기술 수업 활동 물품을 다음과 같이 구입하고자 합니다.\\n 1. ...  끝.",
    "예산": "기술실험실습재료비",
    "품목": [
      {"내용": "우드락 5T", "규격": "610x910", "수량": 10, "단가": 3050},
      {"내용": "배송비", "수량": 1, "단가": 3000}
    ]
  }
  - "예산" 은 예산선택 창의 줄 글자 중 «그 줄에만 있는» 한 부분 (세부항목 이름 등).
    여러 줄에 걸리면 채우지 않고 후보를 보여 주며 멈춘다
  - 결과 캡처는 이 폴더의 캡처/ 에 남는다 (gitignore). 마지막 줄에 RESULT_JSON= 을 찍는다
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import edufine_common as E
from playwright.sync_api import sync_playwright

MENU = "품의목록"
FORBIDDEN = ("결재요청", "삭제", "결재취소요청")          # 이 파일은 절대 안 누른다

# ── 화면 읽기용 JS ──────────────────────────────────────────────
JS_GRIDS = r"""
() => {
  const grids = new Set();
  document.querySelectorAll('div[id*="gridrow_"]').forEach(e => {
    const m = e.id.match(/^(.*)\.body\.gridrow_\d+$/); if (m) grids.add(m[1]);
  });
  return [...grids];
}
"""

JS_ROWS = r"""
(gid) => [...document.querySelectorAll('div[id^="' + gid + '.body.gridrow_"]')]
  .filter(e => /gridrow_\d+$/.test(e.id))
  .map(e => ({idx: parseInt(e.id.match(/gridrow_(\d+)$/)[1], 10),
              text: e.innerText.replace(/\s*\n\s*/g, ' | ').trim()}))
  .filter(r => r.text)
"""

JS_DETAIL = r"""
() => {
  const v = s => { const e = document.querySelector('input[id$="' + s + '"]'); return e ? e.value : ''; };
  const rows = suf => {
    const g = new Set();
    document.querySelectorAll('div[id*="gridrow_"]').forEach(e => {
      const m = e.id.match(/^(.*)\.body\.gridrow_\d+$/); if (m && m[1].endsWith(suf)) g.add(m[1]);
    });
    const gid = [...g][0];
    return gid ? [...document.querySelectorAll('div[id^="' + gid + '.body.gridrow_"]')]
      .filter(e => /gridrow_\d+$/.test(e.id))
      .map(e => e.innerText.replace(/\s*\n\s*/g, ' | ').trim()).filter(Boolean) : [];
  };
  const ta = [...document.querySelectorAll('textarea')].filter(e => e.offsetParent)
      .map(e => e.value).filter(x => x && x.trim());
  return {품의번호: v('form.edtCnsulNoRe:input') || v('form.edtCnsulNo:input'),
          제목: v('form.edtCnsulSj:input'), 상태: v('form.edtProgrsSts:input'),
          요구금액: v('form.mskDmndAmt:input'), 개요: ta[0] || '',
          예산: rows('form.grdBgtDtl'), 품목: rows('form.grdPrdlstDtl')};
}
"""

JS_PLACE = r"""
(a) => {
  const e = document.getElementById(a.cell), b = document.getElementById(a.gid + '.body');
  if (!e || !b) return null;
  const r = e.getBoundingClientRect(), br = b.getBoundingClientRect();
  return {x: r.x + r.width / 2, y: r.y + r.height / 2, bottom: br.y + br.height - 4,
          gx: br.x + br.width / 2, gy: br.y + br.height / 2,
          inside: r.y >= br.y && r.y + r.height <= br.y + br.height};
}
"""

JS_ROWHITS = r"""
(a) => [...document.querySelectorAll('div[id^="' + a.gid + '.body.gridrow_"]')]
  .filter(e => /gridrow_\d+$/.test(e.id) && (e.innerText || '').indexOf(a.needle) >= 0)
  .map(r => {
    const cb = [...document.querySelectorAll('div[id^="' + r.id + '."]')]
        .find(e => e.id.indexOf('cellcheckbox') >= 0 && !e.id.endsWith(':icontext'));
    const q = cb ? cb.getBoundingClientRect() : null;
    return {row: +r.id.match(/gridrow_(\d+)$/)[1], x: q ? q.x + q.width / 2 : null,
            y: q ? q.y + q.height / 2 : null,
            text: (r.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 90)};
  })
"""

JS_VISIBLE_POINT = r"""
(id) => {
  const e = document.getElementById(id);
  if (!e) return null;
  const r = e.getBoundingClientRect();
  if (!r.width || !r.height) return {covered: true};
  const y = r.y + r.height / 2;
  for (let x = r.x + 5; x < r.x + r.width - 3; x += 8) {
    if (x < 0 || y < 0 || x > innerWidth - 2 || y > innerHeight - 2) continue;
    const top = document.elementFromPoint(x, y);
    if (top && (top.id === id || (top.id || '').indexOf(id + '.') === 0)) return {x: x, y: y, covered: false};
  }
  return {covered: true, y: y};
}
"""

JS_CELLHAS = r"""
(a) => {
  const e = document.getElementById(a.cid);
  if (!e) return false;
  const ed = [...document.querySelectorAll('input')].find(x => (x.id || '').indexOf(a.cid + '.') === 0);
  return (((ed ? ed.value : '') + ' ' + (e.innerText || '')).replace(/,/g, '')).indexOf(a.want) >= 0;
}
"""

JS_CELLTEXT = r"""
(id) => { const e = document.getElementById(id); return e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : null; }
"""


# ── 누르기 ──────────────────────────────────────────────────────
def nexa_click(page, x, y):
    """Nexacro 는 down/up 이 붙어 있으면 못 알아듣는다 — 사이를 벌린다."""
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(120)
    page.mouse.up()


def click_safe(page, name):
    if name in FORBIDDEN:
        raise SystemExit("⛔ '%s' 는 이 스크립트가 누르지 않습니다." % name)
    E.click_btn(page, name)


def click_in_popup(page, name):
    """지금 떠 있는 팝업 «안쪽» 버튼만 누른다 (다른 확인창의 [확인]을 누르지 않게)."""
    loc = page.locator('%s div[id$=":icontext"]' % E.OVERLAY).filter(
        has_text=re.compile("^%s$" % re.escape(name)))
    if loc.count() == 0:
        raise SystemExit("⛔ 팝업 안에서 '%s' 버튼을 못 찾았습니다." % name)
    loc.last.click(timeout=8000)


def grid(page, suffix):
    return next((g for g in page.evaluate(JS_GRIDS) if g.endswith(suffix)), None)


# ── 목록 · 상세 ─────────────────────────────────────────────────
def open_list(page, frm=None):
    E.open_klef_menu(page, MENU)
    if frm:
        E.set_date(page, "calCnsulBeginDte.calendaredit:input", frm)
        E.set_date(page, "calCnsulEndDte.calendaredit:input", date.today().strftime("%Y%m%d"))
        click_safe(page, "조회")
        page.wait_for_timeout(3000)
    gid = grid(page, "grdMain")
    return gid


def on_detail(page):
    return page.locator('div[id$=":icontext"]').filter(has_text=re.compile("^목록$")).count() > 0


def open_title(page, gid, idx):
    """목록의 «제목»(파란 밑줄)을 눌러 상세 화면으로 간다.
    ★ 목록 아래 칸은 첫 줄 것으로 고정이라, 줄만 누르면 첫 품의를 N번 읽게 된다 (2026-09-16 실측)."""
    cell = "%s.body.gridrow_%d.cell_%d_3" % (gid, idx, idx)
    for _ in range(3):
        box = page.evaluate(JS_PLACE, {"cell": cell, "gid": gid})
        if not box:
            return False
        for _ in range(8):                      # 화면 밖 줄이면 굴려서 보이게
            if box["inside"]:
                break
            page.mouse.move(box["gx"], box["gy"])
            page.mouse.wheel(0, 120 if box["y"] > box["bottom"] else -120)
            page.wait_for_timeout(350)
            box = page.evaluate(JS_PLACE, {"cell": cell, "gid": gid})
            if not box:
                return False
        if not box["inside"]:
            return False
        nexa_click(page, box["x"], box["y"])
        for _ in range(12):
            page.wait_for_timeout(600)
            if on_detail(page):
                page.wait_for_timeout(1200)
                return True
    return False


def back_to_list(page, gid):
    for _ in range(3):
        try:
            click_safe(page, "목록")         # 바로 옆이 [신규] 다 — 글자 그대로 «목록»만 누른다
        except Exception:
            pass
        for _ in range(12):
            page.wait_for_timeout(600)
            if not on_detail(page) and page.locator('div[id^="%s.body.gridrow_0"]' % gid).count():
                page.wait_for_timeout(800)
                return True
    return False


def school_year_start():
    t = date.today()
    return "%d0301" % (t.year if t.month >= 3 else t.year - 1)


def cmd_read(page, n, frm):
    gid = open_list(page, frm)
    if not gid:
        raise SystemExit("품의 목록 표를 못 찾았습니다. 캡처를 보여 주세요.")
    rows = page.evaluate(JS_ROWS, gid)
    E.log("📋 %s ~ 오늘 품의 %d건 (이 중 %d건을 읽습니다)" % (frm, len(rows), min(n, len(rows))))
    out, prev = [], None
    for r in rows[:n]:
        E.close_modals(page)
        if not open_title(page, gid, r["idx"]):
            E.log("  ⛔ %d번째 줄 제목을 못 열었습니다" % (r["idx"] + 1))
            continue
        d = page.evaluate(JS_DETAIL)
        sig = (d["품의번호"], d["개요"][:40])
        if sig == prev:                         # 앞 건과 똑같으면 잘못 연 것
            E.log("  ⛔ 앞 건과 같은 내용이 열렸습니다 — 건너뜁니다")
        else:
            out.append(d)
            E.log("\n  · %s | %s | %s원 | %s" % (d["품의번호"], d["제목"], d["요구금액"], d["상태"]))
            for line in d["개요"].splitlines()[:8]:
                E.log("      %s" % line)
            for b in d["예산"]:
                E.log("      [예산] %s" % b)
            for it in d["품목"]:
                E.log("      [품목] %s" % it)
        prev = sig
        if not back_to_list(page, gid):
            E.log("  ⛔ 목록으로 못 돌아와 여기서 멈춥니다")
            break
    E.OUT_DIR.mkdir(parents=True, exist_ok=True)
    f = E.OUT_DIR / "품의_읽은것.json"          # 캡처/ 는 gitignore
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    E.log("\n저장: %s (%d건)" % (f.name, len(out)))
    return {"읽은건수": len(out), "파일": f.name}


# ── 채우기 ─────────────────────────────────────────────────────
def type_into(page, suffix, text):
    """값만 넣으면 저장할 때 «필수입력»으로 막힌다 — 실제로 타이핑한다."""
    el = page.locator('input[id$="%s"], textarea[id$="%s"]' % (suffix, suffix)).first
    el.click()
    page.wait_for_timeout(300)
    el.press("Control+a")
    el.type(text, delay=15)
    page.wait_for_timeout(400)
    return el.input_value()


def put_cell(page, gid, row, col, value):
    """품목 표 한 칸. 칸을 눌러도 편집기가 안 열린다 → 누르고 바로 타이핑 → Enter → Tab 으로 확정.
    오른쪽 Quick Link 가 칸을 덮으면 표를 가로로 굴린다 (2026-09-17 실측)."""
    cid = "%s.body.gridrow_%d.cell_%d_%d" % (gid, row, row, col)
    pt = page.evaluate(JS_VISIBLE_POINT, cid)
    if not pt:
        return False
    if pt.get("covered"):
        for _ in range(8):
            page.mouse.move(700, pt.get("y") or 300)
            page.mouse.wheel(300, 0)
            page.wait_for_timeout(350)
            pt = page.evaluate(JS_VISIBLE_POINT, cid)
            if pt and not pt.get("covered"):
                break
        if not pt or pt.get("covered"):
            E.log("      칸 %d 을(를) 누를 수 없습니다 (가려짐)" % col)
            return False
    if E.close_modals(page) or page.locator(E.OVERLAY).count():
        E.log("      창이 떠 있어 입력을 멈춥니다: %s" % E.modal_text(page))
        return False
    nexa_click(page, pt["x"], pt["y"])
    page.wait_for_timeout(350)
    page.keyboard.press("Control+a")
    page.keyboard.type(str(value), delay=25)
    page.wait_for_timeout(500)
    if page.locator(E.OVERLAY).count():
        return False
    want = str(value).replace(",", "")
    if not page.evaluate(JS_CELLHAS, {"cid": cid, "want": want}):
        E.log("      칸 %d: 값이 그 칸에 안 보여 Enter 를 보내지 않습니다" % col)
        return False
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    page.keyboard.press("Tab")
    page.wait_for_timeout(450)
    got = page.evaluate(JS_CELLTEXT, cid) or ""
    return want in got.replace(",", "")


def money(n):
    return "{:,}".format(n)


def load_plan(path):
    p = Path(path)
    if not p.is_absolute():
        p = E.HERE / p
    if not p.exists():
        raise SystemExit("'%s' 파일이 없습니다." % p)
    d = json.loads(p.read_text(encoding="utf-8-sig"))
    for k in ("제목", "개요", "예산", "품목"):
        if not d.get(k):
            raise SystemExit("품의.json 에 '%s' 가 비어 있습니다. 채우기 전에 선생님께 물어보세요." % k)
    for i, it in enumerate(d["품목"], 1):
        if not it.get("내용") or not isinstance(it.get("수량"), int) or not isinstance(it.get("단가"), int):
            raise SystemExit("품목 %d번에 내용·수량·단가(숫자)가 다 있어야 합니다: %s" % (i, it))
    d["합계"] = sum(it["수량"] * it["단가"] for it in d["품목"])
    return d


def cmd_fill(page, plan, save):
    E.log("\n채울 내용 — 제목 %r / 예산 %r / 품목 %d줄 / 합계 %s원"
          % (plan["제목"], plan["예산"], len(plan["품목"]), money(plan["합계"])))
    open_list(page)
    E.close_modals(page)
    click_safe(page, "신규")
    page.wait_for_timeout(4000)
    E.close_modals(page)

    type_into(page, "form.edtCnsulSj:input", plan["제목"])
    type_into(page, "form.txtareaCnsulSumrCn:textarea", plan["개요"])

    # ★ 예산을 먼저 골라야 [행추가] 가 품목 줄을 만든다 (먼저 누르면 조용히 아무 일도 없다)
    click_safe(page, "예산선택")
    page.wait_for_timeout(3500)
    bg = grid(page, "grdBgtChoise")
    if not bg:
        raise SystemExit("예산선택 창의 표를 못 찾았습니다. 캡처를 보여 주세요.")
    hits = page.evaluate(JS_ROWHITS, {"gid": bg, "needle": plan["예산"]})
    if len(hits) != 1:
        E.log("⛔ 예산 %r 에 맞는 줄이 %d개입니다. 선생님께 어느 줄인지 여쭤보세요." % (plan["예산"], len(hits)))
        for r in page.evaluate(JS_ROWS, bg):
            E.log("    예산 줄: %s" % r["text"])
        E.shot(page, "품의_예산선택.png")
        return {"채움": False, "이유": "예산 줄 %d개" % len(hits)}
    h = hits[0]
    if h["x"] is None:
        raise SystemExit("⛔ 그 예산 줄에 선택 칸이 없습니다: %s" % h["text"])
    E.log("예산 줄: %s" % h["text"])
    nexa_click(page, h["x"], h["y"])
    page.wait_for_timeout(1200)
    click_in_popup(page, "확인")
    page.wait_for_timeout(3500)
    E.close_modals(page)
    blc = page.locator('input[id$="form.mskBgtBlce:input"]').first.input_value()
    E.log("예산잔액: %s" % (blc or "(안 보임)"))
    try:
        if blc and int(blc.replace(",", "")) < plan["합계"]:
            E.log("⛔ 예산잔액(%s)이 합계(%s)보다 적습니다. 여기서 멈춥니다." % (blc, money(plan["합계"])))
            E.shot(page, "품의_채움.png")
            return {"채움": False, "이유": "예산잔액 부족", "예산잔액": blc}
    except ValueError:
        pass

    # ★ 칸 번호(2026-09-17 실측): 3 내용 · 4 S2B물품번호(숨김) · 5 규격 · 6 수량 · 7 단위 · 8 예상단가
    pid, bad = None, []
    for n, it in enumerate(plan["품목"]):
        click_safe(page, "행추가")
        page.wait_for_timeout(2200)
        pid = pid or grid(page, "grdPrdlstDtl")
        if not pid:
            raise SystemExit("품목 표를 못 찾았습니다.")
        cols = [(3, it["내용"])]
        if it.get("규격"):
            cols.append((5, it["규격"]))
        cols += [(6, it["수량"]), (8, it["단가"])]
        for col, val in cols:
            if not put_cell(page, pid, n, col, val):
                bad.append("%d번째 줄 %s" % (n + 1, {3: "내용", 5: "규격", 6: "수량", 8: "단가"}[col]))
        # 마지막 값은 다른 칸으로 옮겨야 확정된다 — 편집이 안 되는 «순번» 칸을 한 번 누른다
        # (내용 칸을 누르면 편집기가 다시 열려서 표 글자에 안 보인다 — 2026-09-18 실측)
        pt = page.evaluate(JS_VISIBLE_POINT, "%s.body.gridrow_%d.cell_%d_2" % (pid, n, n))
        if pt and not pt.get("covered"):
            nexa_click(page, pt["x"], pt["y"])
        page.wait_for_timeout(900)
    page.wait_for_timeout(1200)

    amt = page.locator('input[id$="form.mskDmndAmt:input"]').first.input_value()
    rows = [r["text"] for r in page.evaluate(JS_ROWS, pid)] if pid else []
    E.log("\n화면에 들어간 품목:")
    for r in rows:
        E.log("    %s" % r)
    same = amt.replace(",", "") == str(plan["합계"])
    E.log("요구금액: 화면 %s원 / 계산 %s원 → %s" % (amt, money(plan["합계"]), "✅ 같음" if same else "❌ 다름"))
    if bad:
        E.log("❌ 안 들어간 칸: %s" % ", ".join(bad))
    E.shot(page, "품의_채움.png")
    res = {"채움": True, "요구금액": amt, "계산합계": plan["합계"], "일치": same, "안들어간칸": bad}

    if not save:
        E.log("\n★ 저장·결재요청은 누르지 않았습니다. 여기서 멈춥니다.")
        return res
    if not same or bad:
        E.log("\n⛔ 금액이 다르거나 빈 칸이 있어 저장하지 않습니다.")
        return res

    E.log("\n⚠️  [저장] 을 누릅니다 (선생님 지시)")
    E.click_btn(page, "저장")
    page.wait_for_timeout(3000)
    mt = E.modal_text(page) or ""
    E.log("저장 물음: %s" % mt)
    # 버튼 이름(취소)이 아니라 «묻는 말»을 보고 판단한다
    if "저장하시겠습니까" not in mt or "결재요청" in mt or "삭제" in mt:
        E.log("⛔ 예상한 «저장하시겠습니까?» 창이 아닙니다 — 아무것도 누르지 않고 멈춥니다.")
        E.shot(page, "품의_저장물음.png")
        res["저장"] = "멈춤"
        return res
    click_in_popup(page, "확인")
    page.wait_for_timeout(3500)
    done = E.modal_text(page) or ""
    E.log("저장 결과: %s" % done)
    try:
        click_in_popup(page, "확인")
    except SystemExit:
        pass
    page.wait_for_timeout(2500)
    no = page.locator('input[id$="form.edtCnsulNoRe:input"]')
    num = no.first.input_value() if no.count() else ""
    E.log("품의번호: %s" % (num or "(못 읽음 — 품의목록에서 확인하세요)"))
    E.shot(page, "품의_저장뒤.png")
    res.update({"저장": "완료" if "완료" in done else "모름", "품의번호": num})
    E.log("\n★ 결재요청은 누르지 않았습니다. 선생님이 화면에서 확인하고 직접 누르세요.")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("할일", choices=["열기", "읽기", "채우기"])
    ap.add_argument("파일", nargs="?", help="채우기: 품의.json")
    ap.add_argument("--건수", type=int, default=5)
    ap.add_argument("--시작일", help="읽기 조회 시작일 YYYY-MM-DD (기본: 올해 회계연도 3월 1일)")
    ap.add_argument("--저장", action="store_true", help="채운 뒤 저장까지 (선생님이 말할 때만)")
    a = ap.parse_args()
    plan = load_plan(a.파일 or "품의.json") if a.할일 == "채우기" else None
    name, pw = E.creds()

    result = {"할일": a.할일}
    code = 0
    with sync_playwright() as p:
        ctx, page = E.launch(p)
        page.on("dialog", lambda d: d.dismiss())     # 브라우저 확인창은 모두 «아니오»
        try:
            E.login(page, name, pw)
            if a.할일 == "열기":
                open_list(page)
                E.shot(page, "품의목록.png")
                result["열림"] = True
                E.log("\n✅ 품의목록 화면까지 열었습니다. 아무것도 누르지 않았습니다.")
            elif a.할일 == "읽기":
                frm = (a.시작일 or "").replace("-", "") or school_year_start()
                result.update(cmd_read(page, a.건수, frm))
            else:
                result.update(cmd_fill(page, plan, a.저장))
                if not result.get("채움") or not result.get("일치", True):
                    code = 1
        except (Exception, SystemExit) as e:
            result["오류"] = str(e)[:300]
            E.log("❌ %s" % result["오류"])
            E.shot(page, "오류.png")
            code = 1
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    print("RESULT_JSON=" + json.dumps(result, ensure_ascii=False))
    sys.exit(code)


if __name__ == "__main__":
    main()
