"""把三列表导出成 Excel（xlsx）—— 2026-10-28

复用 export_measure_table.py 的数据（同一份 build_groups），
所以改标签/描述只改那一个文件，这里不用动。

产物：~/Desktop/联席措施表_v1.xlsx
  Sheet1「标签措施表」：标签 / 该标签概括的问题 / 联席措施（空，浅黄底）
  Sheet2「To-Do对照」：卡片 To-Do 与上表哪一条对应（附录，不占正表）
"""
import os
import sys
import unicodedata

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_measure_table as emt                          # noqa: E402

OUT = os.path.expanduser(f"~/Desktop/联席措施表_{emt.VER}.xlsx")

# ── 配色（与 HTML 版一致）──────────────────────────────────────────────
C_HEAD_BG = "2F4F73"
C_HEAD_FG = "FFFFFF"
C_GRP_BG = "E8ECF3"
C_GRP_FG = "33405A"
C_ACT_BG = "FFFDF4"      # 措施列浅黄：空着也一眼看到该填
C_LINE = "D9DEE6"

FONT = "PingFang SC"


def dwidth(s):
    """显示宽度：中文/全角算 2，其余算 1。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def wrap_lines(s, col_units):
    """估算在给定列宽下要占几行。"""
    w = max(1.0, col_units - 2)
    total = 0
    for para in str(s).split("\n"):
        total += max(1, -(-dwidth(para) // int(w)))
    return total


def main():
    groups = emt.build_groups()
    wb = Workbook()
    ws = wb.active
    ws.title = "标签措施表"

    thin = Side(style="thin", color=C_LINE)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap_top = Alignment(horizontal="left", vertical="top", wrap_text=True)
    center_top = Alignment(horizontal="center", vertical="top", wrap_text=True)

    widths = {"A": 26, "B": 68, "C": 46}
    for k, v in widths.items():
        ws.column_dimensions[k].width = v

    # ── 表头 ──
    heads = ["标签", "该标签概括的问题", "联席措施（请填写）"]
    for i, h in enumerate(heads, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(name=FONT, size=12, bold=True, color=C_HEAD_FG)
        c.fill = PatternFill("solid", fgColor=C_HEAD_BG)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border
    ws.row_dimensions[1].height = 30

    r = 2
    for gname, rows in groups:
        # 分组标题行（跨三列）
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
        c = ws.cell(row=r, column=1, value=gname)
        c.font = Font(name=FONT, size=11, bold=True, color=C_GRP_FG)
        c.fill = PatternFill("solid", fgColor=C_GRP_BG)
        c.alignment = Alignment(horizontal="left", vertical="center")
        for col in range(1, 4):
            ws.cell(row=r, column=col).border = border
        ws.row_dimensions[r].height = 26
        r += 1

        for lab, desc in rows:
            a = ws.cell(row=r, column=1, value=lab)
            a.font = Font(name=FONT, size=11, bold=True)
            a.alignment = wrap_top
            a.border = border

            b = ws.cell(row=r, column=2, value=desc)
            b.font = Font(name=FONT, size=11)
            b.alignment = wrap_top
            b.border = border

            c3 = ws.cell(row=r, column=3, value=None)
            c3.fill = PatternFill("solid", fgColor=C_ACT_BG)
            c3.alignment = center_top
            c3.border = border

            lines = max(wrap_lines(lab, widths["A"]), wrap_lines(desc, widths["B"]))
            ws.row_dimensions[r].height = max(32, lines * 17 + 14)
            r += 1

    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False

    # 打印：横向、按宽缩到一页
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = "1:1"

    # ── Sheet2：To-Do 对照 ──
    w2 = wb.create_sheet("To-Do对照")
    w2.column_dimensions["A"].width = 30
    w2.column_dimensions["B"].width = 52

    def head2(text):
        nonlocal rr
        c = w2.cell(row=rr, column=1, value=text)
        c.font = Font(name=FONT, size=12, bold=True, color=C_HEAD_FG)
        c.fill = PatternFill("solid", fgColor=C_HEAD_BG)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.border = border
        c2 = w2.cell(row=rr, column=2, value="对应上表哪一条")
        c2.font = Font(name=FONT, size=12, bold=True, color=C_HEAD_FG)
        c2.fill = PatternFill("solid", fgColor=C_HEAD_BG)
        c2.alignment = Alignment(horizontal="left", vertical="center")
        c2.border = border
        w2.row_dimensions[rr].height = 28
        rr += 1

    def grp2(text):
        nonlocal rr
        w2.merge_cells(start_row=rr, start_column=1, end_row=rr, end_column=2)
        c = w2.cell(row=rr, column=1, value=text)
        c.font = Font(name=FONT, size=11, bold=True, color=C_GRP_FG)
        c.fill = PatternFill("solid", fgColor=C_GRP_BG)
        c.alignment = Alignment(horizontal="left", vertical="center")
        w2.row_dimensions[rr].height = 24
        rr += 1

    rr = 1
    head2("卡片 To-Do")
    for title, rows in [("教务视角（9 条）", emt.TODO_MAP_EDU),
                        ("联席视角（11 条）", emt.TODO_MAP_CO)]:
        grp2(title)
        for a_txt, b_txt in rows:
            ca = w2.cell(row=rr, column=1, value=a_txt)
            ca.font = Font(name=FONT, size=11)
            ca.alignment = wrap_top
            ca.border = border
            cb = w2.cell(row=rr, column=2, value=b_txt)
            cb.font = Font(name=FONT, size=11, color="6B7280")
            cb.alignment = wrap_top
            cb.border = border
            w2.row_dimensions[rr].height = 24
            rr += 1

    w2.freeze_panes = "A2"
    w2.sheet_view.showGridLines = False

    wb.save(OUT)
    n = sum(len(x) for _, x in groups)
    print(f"✅ {n} 条 → {OUT}")


if __name__ == "__main__":
    main()
