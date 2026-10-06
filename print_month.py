# -*- coding: utf-8 -*-
"""근태신고서철 출력 3종 인쇄 — 월별 출결현황(엑셀) · 근태 색인 목록(그 달 쪽만) · 학급별 출결현황(PDF).

  python print_month.py 2026-10            무엇을 어느 프린터로 뽑을지 보기만
  python print_month.py 2026-10 --print    실제로 인쇄
  python print_month.py 2026-10 --print --only 엑셀,근태,pdf   골라서

프린터: 설정의 «printer» 가 있으면 그것, 없으면 윈도우 기본 프린터.
  프린터를 바꾸려면:  python print_month.py --printer "프린터 이름"   (설정에 기억)
  이름 목록:          python print_month.py --printers

파일은 month_close.py --download 와 geuntae.py 가 만든 것을 쓴다.
  출력/YYYY-MM_월별출결현황.xlsx · 출력/YYYY-MM_학급별출결현황.pdf · 설정의 근태 색인 목록 .hwp

★ 엑셀은 표를 A4 폭에 꽉 차게 넓혀서 뽑는다 (그냥 «한 쪽에 맞추기»만 하면 양옆이 크게 빈다).
★ 근태 색인 목록은 그 달 줄이 들어 있는 쪽만 뽑는다 (한 쪽에 25줄).
마지막 줄에 항상 RESULT_JSON {...} 을 찍는다.
"""
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "출력"
RESULT = {"ok": False, "month": None, "printer": None, "printed": [], "missing": [], "errors": []}


def log(*a):
    print(*a, flush=True)


def default_printer():
    import win32print
    try:
        return win32print.GetDefaultPrinter()
    except Exception:
        return None


def all_printers():
    import win32print
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags)]


def print_pdf(path: Path, printer: str, title: str = "") -> int:
    """PDF 를 «이름을 댄 프린터»로 직접 보낸다 (쪽마다 그림으로 그려서).

    기본 프린터를 바꿔서 뽑는 방식은 믿을 수 없다 — 이미 켜져 있던 아크로뱃·엑셀은
    기본 프린터를 바꿔도 원래 프린터로 보냈다 (2026-10-06 실측). 그래서 프린터를 직접 연다.
    PDF 를 열 프로그램이 없어도 된다. 보낸 쪽 수를 돌려준다."""
    import pymupdf
    import win32con
    import win32ui
    from PIL import Image, ImageWin
    doc = pymupdf.open(str(path))
    dc = win32ui.CreateDC()
    dc.CreatePrinterDC(printer)
    try:
        dpi = dc.GetDeviceCaps(win32con.LOGPIXELSX)
        pw, ph = dc.GetDeviceCaps(win32con.PHYSICALWIDTH), dc.GetDeviceCaps(win32con.PHYSICALHEIGHT)
        ox, oy = dc.GetDeviceCaps(win32con.PHYSICALOFFSETX), dc.GetDeviceCaps(win32con.PHYSICALOFFSETY)
        dc.StartDoc(title or path.name)
        n = 0
        for page in doc:
            zoom = min(dpi, 300) / 72
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if img.width > img.height and pw < ph:          # 가로 쪽은 세로 종이에 돌려서
                img = img.rotate(90, expand=True)
            scale = min(pw / img.width, ph / img.height)
            w, h = int(img.width * scale), int(img.height * scale)
            x0, y0 = (pw - w) // 2 - ox, (ph - h) // 2 - oy
            dc.StartPage()
            ImageWin.Dib(img).draw(dc.GetHandleOutput(), (x0, y0, x0 + w, y0 + h))
            dc.EndPage()
            n += 1
        dc.EndDoc()
        return n
    finally:
        dc.DeleteDC()
        doc.close()


def xlsx_to_pdf(path: Path) -> Path:
    """열 너비를 인쇄 폭에 비례해 넓히고(머리글 줄은 쪽마다 반복) PDF 로 내보낸다.
    엑셀에서 바로 인쇄하지 않는다 — 엑셀은 프린터 이름을 줘도 조용히 무시하고 다른 프린터로 보낸 적이 있다."""
    import win32com.client
    out = path.with_suffix(".pdf")
    xl = win32com.client.DispatchEx("Excel.Application")
    xl.Visible = False
    xl.DisplayAlerts = False
    try:
        wb = xl.Workbooks.Open(str(path))
        ws = wb.Worksheets(1)
        cm = 28.3465
        last_col = ws.UsedRange.Columns.Count
        ps = ws.PageSetup
        ps.PaperSize = 9            # A4
        ps.Orientation = 1          # 세로
        ps.LeftMargin = 0.6 * cm
        ps.RightMargin = 0.6 * cm
        ps.TopMargin = 1.2 * cm
        ps.BottomMargin = 1.2 * cm
        ps.CenterHorizontally = True
        ps.PrintTitleRows = "$1:$1"
        ws.Columns.AutoFit()
        printable = 595.28 - ps.LeftMargin - ps.RightMargin
        total = sum(ws.Columns(i + 1).Width for i in range(last_col))
        if total > 0:
            scale = printable / total
            for i in range(last_col):
                ws.Columns(i + 1).ColumnWidth = ws.Columns(i + 1).ColumnWidth * scale
        ws.UsedRange.WrapText = True
        ws.Rows.AutoFit()
        ps.Zoom = False
        ps.FitToPagesWide = 1
        ps.FitToPagesTall = False
        wb.Save()
        ws.ExportAsFixedFormat(0, str(out))      # 0 = PDF
        wb.Close(SaveChanges=False)
    finally:
        try:
            xl.Quit()
        except Exception:
            pass
    return out


def hwp_month_pages(hwp, y, m):
    import geuntae as G
    rows = G.read_rows(hwp)
    school_year = y if m >= 3 else y - 1
    pages = set()
    for r in rows:
        a, _ = G.parse_cell_date(r["cells"].get("date"), school_year)
        if a and (a.year, a.month) == (y, m):
            pages.add((r["n"] - 1) // G.ROWS_PER_TABLE + 1)
    return sorted(pages)


def print_hwp_pages(hwp, pages, printer):
    for pg in pages:
        hwp.goto_printpage(pg)
        act = hwp.CreateAction("Print")
        pset = act.CreateSet()
        act.GetDefault(pset)
        pset.SetItem("Range", hwp.PrintRange("CurrentPage"))
        pset.SetItem("PrinterName", printer)
        pset.SetItem("NumCopy", 1)
        act.Execute(pset)
        time.sleep(1.5)


def main():
    args = sys.argv[1:]
    import teacher_config
    try:
        cfg = teacher_config.load_config()
    except teacher_config.ConfigMissingError as e:
        log(f"❌ {e}")
        RESULT["error"] = "config"
        return 1

    if "--printers" in args:
        dp = default_printer()
        for p in all_printers():
            log(("  ★ " if p == dp else "    ") + p + ("   (기본)" if p == dp else ""))
        RESULT["ok"] = True
        return 0
    if "--printer" in args:
        name = args[args.index("--printer") + 1].strip('"')
        if name not in all_printers():
            log(f"❌ «{name}» 프린터가 이 컴퓨터에 없습니다. --printers 로 이름을 확인하세요")
            return 1
        cfg["printer"] = name
        teacher_config.save_config(cfg)
        log(f"🖨  앞으로 «{name}» 으로 뽑습니다")
        RESULT["ok"] = True
        RESULT["printer"] = name
        return 0

    ym = next((a for a in args if re.fullmatch(r"\d{4}-\d{1,2}", a)), None)
    if not ym:
        log(__doc__)
        return 1
    y, m = map(int, ym.split("-"))
    tag = f"{y}-{m:02d}"
    RESULT["month"] = tag
    printer = cfg.get("printer") or default_printer()
    RESULT["printer"] = printer
    do = "--print" in args
    only = None
    if "--only" in args:
        only = {x.strip() for x in args[args.index("--only") + 1].split(",")}

    def want(k):
        return only is None or k in only

    xlsx = OUT_DIR / f"{tag}_월별출결현황.xlsx"
    pdf = OUT_DIR / f"{tag}_학급별출결현황.pdf"
    hwp_path = Path(cfg.get("geuntae_hwp") or "")

    log("=" * 52)
    log(f"🖨  근태신고서철 출력 — {y}년 {m}월  ({'인쇄' if do else '보기만'})")
    log(f"    프린터: {printer or '❌ 기본 프린터가 없습니다'}")
    log("=" * 52)
    if not printer:
        RESULT["error"] = "no_printer"
        return 1

    jobs = []
    if want("엑셀"):
        jobs.append(("월별 출결현황 (엑셀)", xlsx))
    if want("근태"):
        jobs.append(("근태 색인 목록 (그 달 쪽만)", hwp_path if hwp_path.name else None))
    if want("pdf"):
        jobs.append(("학급별 출결현황 (PDF)", pdf))
    for label, p in jobs:
        if not p or not Path(p).exists():
            RESULT["missing"].append(label)
            log(f"  ❌ {label} — 파일이 없습니다" + (f" ({Path(p).name})" if p else " (geuntae.py 로 파일을 먼저 지정)"))

    hwp_pages = []
    if want("근태") and hwp_path.name and hwp_path.exists():
        import geuntae as G
        G.kill_orphan_hwp()
        if G.hwp_running():
            log("  ⛔ 한글 프로그램이 켜져 있습니다 — 한글 창을 모두 닫고 다시 해 주세요")
            RESULT["error"] = "hwp_running"
            return 1
        hwp = G.open_hwp(hwp_path)
        try:
            hwp_pages = hwp_month_pages(hwp, y, m)
            log(f"  근태 색인 목록: {m}월 줄이 있는 쪽 = {hwp_pages or '없음'}")
            if do and hwp_pages:
                print_hwp_pages(hwp, hwp_pages, printer)
                RESULT["printed"].append(f"근태 색인 목록 {hwp_pages}쪽")
                log(f"  ✅ 근태 색인 목록 {hwp_pages}쪽 → {printer}")
        except Exception as e:
            RESULT["errors"].append(f"근태: {str(e)[:120]}")
            log("  ❌ 근태 색인 목록 인쇄 오류:", str(e)[:120])
        finally:
            G.close_hwp(hwp)

    if not do:
        log("\n  (보기만 했습니다. 맞으면 --print 를 붙여 다시 돌리세요)")
        RESULT["ok"] = not RESULT["missing"]
        return 0

    if want("엑셀") and xlsx.exists():
        try:
            n = print_pdf(xlsx_to_pdf(xlsx), printer, f"{tag} 월별 출결현황")
            RESULT["printed"].append(f"월별 출결현황 {n}쪽")
            log(f"  ✅ 월별 출결현황 {n}쪽 (A4 폭에 맞춤) → {printer}")
        except Exception as e:
            RESULT["errors"].append(f"엑셀: {str(e)[:120]}")
            log("  ❌ 월별 출결현황 인쇄 오류:", str(e)[:120])
    if want("pdf") and pdf.exists():
        try:
            n = print_pdf(pdf, printer, f"{tag} 학급별 출결현황")
            RESULT["printed"].append(f"학급별 출결현황 {n}쪽")
            log(f"  ✅ 학급별 출결현황 {n}쪽 → {printer}")
        except Exception as e:
            RESULT["errors"].append(f"PDF: {str(e)[:120]}")
            log("  ❌ 학급별 출결현황 인쇄 오류:", str(e)[:120])
    RESULT["ok"] = not RESULT["errors"] and not RESULT["missing"]
    log("\n  인쇄가 안 나오면 프린터 대기열(설정 › 프린터)을 보세요. 이 도구는 «보냈다»까지만 확인합니다.")
    return 0 if RESULT["ok"] else 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        RESULT["errors"].append(str(e)[:200])
    print("RESULT_JSON " + json.dumps(RESULT, ensure_ascii=False))
    sys.exit(code)
