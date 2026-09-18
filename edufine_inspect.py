# -*- coding: utf-8 -*-
"""K-에듀파인 물품 검사검수 — 밀린 건 조회, 그리고 (말할 때만) 등록.

  .\\.venv\\Scripts\\python.exe -X utf8 edufine_inspect.py                       조회만 (아무것도 안 누름)
  .\\.venv\\Scripts\\python.exe -X utf8 edufine_inspect.py --go --only 25-0277   그 한 건만 등록
  .\\.venv\\Scripts\\python.exe -X utf8 edufine_inspect.py --go                  밀린 건 전부 등록
      --date 2026-09-18   검사검수일 (기본: 오늘)
      --from 2026-03-01   요청일자 조회 시작일 (기본: 올해 1월 1일)

경로: 학교회계 > 계약관리 > 계약이행관리 > 검사검수요청목록 (왼쪽 즐겨찾기 글자 그대로)
마지막 줄에 RESULT_JSON= 을 한 줄 찍는다. 요약하지 말고 그대로 보여 주면 된다.

★ --go 는 되돌리기 어렵다. 선생님이 목록을 보고 «등록해» 라고 한 뒤에만 붙인다.
"""
import argparse
import json
import sys
from datetime import date

import edufine_common as E
from playwright.sync_api import sync_playwright

MENU = "검사검수요청목록"
TARGET = "검사검수요청"
COLS = ["계약연도", "계약번호", "계약명", "목적물", "요청차수",
        "검사검수부서명", "검수자명", "요청일자", "완료요구일", "검수일자", "진행상태"]

JS_FIND_GRID = """
() => {
  const set = new Set();
  document.querySelectorAll('div[id*="gridrow_"]').forEach(e => {
    const m = e.id.match(/^(.*)\\.body\\.gridrow_\\d+$/);
    if (m && m[1].endsWith('divContent.form.grdList')) set.add(m[1]);
  });
  return [...set].find(x => x.includes('A00FAH0103007')) || [...set][0] || null;
}
"""

JS_READ_ROWS = """
(gid) => [...document.querySelectorAll('div[id^="' + gid + '.body.gridrow_"]')]
  .filter(e => /gridrow_\\d+$/.test(e.id))
  .map(e => ({idx: parseInt(e.id.match(/gridrow_(\\d+)$/)[1], 10),
              text: e.innerText.replace(/\\s*\\n\\s*/g, '\\t').trim()}))
"""

JS_DETAIL = """
() => {
  const v = s => { const e = document.querySelector('input[id$="' + s + '"]'); return e ? e.value : null; };
  const set = new Set();
  document.querySelectorAll('div[id*="gridrow_"]').forEach(e => {
    const m = e.id.match(/^(.*)\\.body\\.gridrow_\\d+$/);
    if (m && m[1].endsWith('divContent.form.grdList')) set.add(m[1]);
  });
  const gid = [...set][0];
  const items = gid ? [...document.querySelectorAll('div[id^="' + gid + '.body.gridrow_"]')]
      .filter(e => /gridrow_\\d+$/.test(e.id))
      .map(e => e.innerText.replace(/\\s*\\n\\s*/g, ' | ').trim()).filter(Boolean) : [];
  return {계약번호: v('div01.form.mskCntrNo:input'), 계약명: v('div01.form.cntrctNm:input'),
          계약상대자: v('div01.form.edtBcncNm:input'), 계약금액: v('div01.form.mskCntrctAmt:input'),
          품목: items};
}
"""


def read_rows(page, gid):
    out = []
    for r in page.evaluate(JS_READ_ROWS, gid):
        parts = [p.strip() for p in r["text"].split("\t") if p.strip()]
        if len(parts) < 10:
            continue
        if len(parts) == 10:                    # 검수일자가 비면 10칸
            parts = parts[:9] + [""] + [parts[9]]
        row = dict(zip(COLS, parts[:11]))
        row["idx"] = r["idx"]
        out.append(row)
    return out


def search(page, frm):
    # 기본 범위(당월)로는 당일 건이 0건으로 나온 적이 있다 — 항상 넓게 잡는다
    E.set_date(page, "calAcptncDteFrom.calendaredit:input", frm)
    E.click_btn(page, "조회")
    page.wait_for_timeout(2500)
    gid = page.evaluate(JS_FIND_GRID)
    if not gid:
        raise SystemExit("목록 표를 찾지 못했습니다. 화면을 캡처해서 보여 주세요.")
    return gid, read_rows(page, gid)


def inspect_one(page, gid, row, insp):
    i = row["idx"]
    E.log("\n🧾 %s %s — 등록 시작" % (row["계약번호"], row["계약명"]))
    page.locator('div[id^="%s.body.gridrow_%d.cell_%d_0"]' % (gid, i, i)).first.click()
    page.wait_for_timeout(400)
    E.click_btn(page, "검사검수처리")
    page.wait_for_selector('input[id$="calAcptncDte.calendaredit:input"]', timeout=25000)
    E.wait_modal(page, "", 3000)             # 「G2B번호는…」 알림이 늦게 뜬다
    E.close_modals(page)
    page.wait_for_timeout(500)
    if page.locator(E.OVERLAY).count():
        raise RuntimeError("닫지 못한 창이 있습니다: %s" % E.modal_text(page))

    status_el = page.locator('input[id$="cboCntrctProgrsStsCd.comboedit:input"]')
    detail = page.evaluate(JS_DETAIL)
    before = status_el.input_value()
    if detail["계약번호"] != row["계약번호"]:
        raise RuntimeError("목록에서 고른 건과 열린 건이 다릅니다: %s / %s" % (row["계약번호"], detail["계약번호"]))
    if "완료" in before or "지급" in before:
        raise RuntimeError("이미 처리된 건입니다 (상태 %s)" % before)
    for it in detail["품목"]:
        E.log("    · %s" % it)

    got = E.set_date(page, "calAcptncDte.calendaredit:input", insp)
    want = "%s-%s-%s" % (insp[:4], insp[4:6], insp[6:])
    if got != want:
        raise RuntimeError("검사검수일 입력 실패: %s 를 넣었는데 %s" % (want, got))

    E.click_btn(page, "검사검수등록")
    confirm = E.wait_modal(page, "결재요청 없이", 10000)
    if confirm is None:
        raise RuntimeError("예상한 확인창이 안 떴습니다 — 아무것도 누르지 않았습니다: %s" % E.modal_text(page))
    page.wait_for_timeout(300)
    E.log("    확인창: %s" % (E.modal_text(page) or "")[:120])
    confirm.locator('div[id$="form.btnOk:icontext"]').last.click()

    status, done = before, ""
    for _ in range(50):
        page.wait_for_timeout(300)
        status = status_el.input_value()
        if status == "검사검수완료":
            break
        if page.locator(E.OVERLAY).count() and not E.has_business_confirm(page):
            done = E.modal_text(page) or done
            E.close_modals(page)
    ok = status == "검사검수완료"
    E.close_modals(page)
    E.log("    %s 상태 = %s" % ("✅" if ok else "❌", status))
    detail.update({"검사검수일": got, "상태": status, "성공": ok})
    try:
        E.click_btn(page, "목록")
        page.wait_for_timeout(2000)
    except Exception as e:
        detail["목록복귀오류"] = str(e)[:200]
    return detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="등록까지 (없으면 조회만)")
    ap.add_argument("--only", help="이 계약번호 한 건만")
    ap.add_argument("--date", help="검사검수일 YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--from", dest="frm", help="조회 시작일 YYYY-MM-DD (기본: 올해 1월 1일)")
    a = ap.parse_args()
    today = date.today()
    insp = (a.date or today.isoformat()).replace("-", "")
    frm = (a.frm or "%d-01-01" % today.year).replace("-", "")
    name, pw = E.creds()

    result = {"실행": bool(a.go), "대기": [], "처리": []}
    with sync_playwright() as p:
        ctx, page = E.launch(p)
        try:
            E.login(page, name, pw)
            E.open_klef_menu(page, MENU, 'input[id$="calAcptncDteFrom.calendaredit:input"]')
            gid, rows = search(page, frm)
            pend = [r for r in rows if r["진행상태"] == TARGET]
            if a.only:
                pend = [r for r in pend if r["계약번호"] == a.only]
            E.log("\n📋 전체 %d건 / 검사검수요청 %d건" % (len(rows), len(pend)))
            for r in pend:
                E.log("   • %s  %s  (요청 %s, 완료요구 %s)"
                      % (r["계약번호"], r["계약명"], r["요청일자"], r["완료요구일"]))
            E.shot(page, "검수_목록.png")
            result["대기"] = [{k: r[k] for k in ("계약번호", "계약명", "요청일자", "완료요구일")} for r in pend]

            if not a.go:
                E.log("\n※ 조회만 했습니다. 아무것도 누르지 않았습니다.")
            elif not pend:
                E.log("\n등록할 건이 없습니다.")
            else:
                for n, r in enumerate(pend):
                    try:
                        if n:                    # 목록이 바뀌었으니 다시 조회해서 줄을 새로 잡는다
                            gid, rows2 = search(page, frm)
                            m = [x for x in rows2 if x["계약번호"] == r["계약번호"]
                                 and x["요청차수"] == r["요청차수"] and x["진행상태"] == TARGET]
                            if not m:
                                continue
                            r = m[0]
                        res = inspect_one(page, gid, r, insp)
                    except (Exception, SystemExit) as e:
                        res = {"계약번호": r["계약번호"], "성공": False, "오류": str(e)[:300]}
                        E.log("❌ %s 실패: %s" % (r["계약번호"], res["오류"]))
                    result["처리"].append(res)
                    if not res.get("성공") or res.get("목록복귀오류"):
                        if len(pend) - n - 1:
                            E.log("⛔ 남은 %d건은 하지 않고 멈춥니다." % (len(pend) - n - 1))
                        break
        finally:
            ctx.close()

    print("RESULT_JSON=" + json.dumps(result, ensure_ascii=False))
    sys.exit(0 if all(x.get("성공") and not x.get("목록복귀오류") for x in result["처리"]) else 1)


if __name__ == "__main__":
    main()
