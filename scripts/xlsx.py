"""步骤10：生成待办清单 Excel（总览 + 待办 + 出勤明细）。

支持两种视角（--co 联席 / --edu 教务）：
  - 总览 sheet 增加「所属联席」列；
  - 教务视角双维度待办：①「教务To-Do·按课程」按课程分组；②「教务To-Do·按联席」按联席分组；
    （To-Do 内容面向教务职责：家长沟通 / 反馈学生问题 / 建立信任 / 推动续报）
  - 联席视角：①「课程待办清单」按课程分组（To-Do 面向跟课辅导 / 答疑 / 作业订正）。

读取 analysis_merged.json，输出 <workdir>/<姓名>_学生待办清单.xlsx。
依赖 openpyxl（在 WorkBuddy 受管 Python 的 venv 里安装：pip install openpyxl）。
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

common.ensure_openpyxl()

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side


def course_of(group_name):
    gn = group_name or ""
    parts = gn.split("-")
    raw = parts[1] if len(parts) > 1 else gn
    if raw == "高二数学清北层E邵":
        raw = "高二数学清北层日E邵"
    m = re.match(r"高二数学(清北层|985层|211层|[\u4e00-\u9fa5]+层)(.+?)(邵|谢胡|胡[\u4e00-\u9fa5]*)$", raw)
    if m:
        return f"高二数学·{m.group(1)}·{m.group(2)}班（{m.group(3)}老师）"
    return raw


def grade(s, timeout, unreply):
    if timeout or unreply:
        return "红"
    if s["unresolved"] or s["neg"] > 0 or s.get("hw_neg", 0) > 0 or s.get("att_leave", 0) > 0:
        return "黄"
    return "绿"


def coop_conclusion(s):
    n_stu, q = s["n_stu"], s["q"]
    n_sub = s.get("n_stu_sub", n_stu)
    n_ack = s.get("n_ack", 0)
    sc, pc = s.get("student_coop", 0), s.get("parent_coop", 0)
    if n_sub == 0:
        if n_stu == 0:
            return "窗口内学生/家长均无发言，暂无法判断，需先确认学生状态。"
        return f"窗口内学生/家长仅 {n_ack} 条礼节性回应（好的/收到/致谢，均无需回复），无实质互动，配合度暂无法判断。"
    stu_lv = {0: "学生本人几乎不露面", 1: "学生自驱偏低", 2: "学生自驱偏低",
              3: "学生自驱中等", 4: "学生自驱较强", 5: "学生自驱标杆级"}[sc]
    par_lv = {0: "无家长互动", 1: "家长参与较少", 2: "家长配合偏弱",
              3: "家长配合一般", 4: "家长配合度较高", 5: "家长高度配合"}[pc]
    if sc <= 2 and pc >= 4:
        typ = "“家长推着走”型，需把主动权交还学生"
    elif sc >= 4 and pc >= 4:
        typ = "双高·放手培养型"
    elif sc >= 4 and pc <= 2:
        typ = "良性自驱型，可适度引导家长"
    elif sc <= 2 and pc <= 2:
        typ = "双方配合均偏弱，需重点激活"
    else:
        typ = ""   # 常规情形：两级结论已说明，不再叠加套话
    return f"{stu_lv}；{par_lv}；{typ}。" if typ else f"{stu_lv}；{par_lv}。"


def att_short(a):
    if not a:
        return "无后台记录", "—"
    n, ab, pbc = a["lessonCount"], a["absentCount"], a["playbackCount"]
    no_pb = [c for c in a["classes"] if c["isAttend"] != 1 and c["isPlayback"] != 1]
    if ab == 0:
        flag = "🟢 全勤"
    elif ab == n:
        flag = "🔴 全勤未到，" + ("均看回放" if pbc == n else "部分未看回放")
    elif ab >= 2 or no_pb:
        flag = "🔴 需关注"
    else:
        flag = "🟡 轻关注" if no_pb else "🟢 常规"
    return f"出勤{n-ab}/{n}·缺{ab}·回放{pbc}", flag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", default=None)
    ap.add_argument("--edu", default=None)
    args = ap.parse_args()
    role, name = common.resolve_scope(args)
    is_edu = role == "edu"
    role_cn = "教务" if is_edu else "联席"
    wd = common.workdir(args)
    A = json.load(open(f"{wd}/analysis_merged.json", encoding="utf-8"))

    rows = []
    for stu, s in A.items():
        g = s["group"]
        timeout = str(g.get("isTimeout")) == "1" or g.get("isTimeout") is True
        # 「是否需回复」不再依赖后台 isReplied，改为聊天分析结论（会话尾部未获回应提问）
        unreply = bool(s["unresolved"])
        rows.append(((1 if timeout else 0, 1 if s["unresolved"] else 0, s["n_text"]), stu, s, g, timeout, unreply))
    rows.sort(key=lambda x: (-x[0][0], -x[0][1], -x[0][2]))

    wb = Workbook()
    thin = Side(style="thin", color="D0D3D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_fill = PatternFill("solid", fgColor="2F54EB")
    hdr_font = Font(bold=True, color="FFFFFF")
    wrap = Alignment(wrap_text=True, vertical="top")

    ws = wb.active; ws.title = "学生群总览"
    cols = ["序号", "学生", "购课类型", "群名", "课程", "所属联席", "定档", "是否需回复", "超时状态", "超时时长", "近窗起", "近窗止",
            "可分析消息", "生/家长消息", "礼节回应", "师消息", "语音条", "配合度结论",
            "出勤(后台)", "是否需关注", "请假备注(聊天)", "高频知识点", "近期未解疑问"]
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        cell = ws.cell(1, c); cell.fill = hdr_fill; cell.font = hdr_font; cell.border = border
    for i, (_, stu, s, g, timeout, unreply) in enumerate(rows, 1):
        att_txt, att_flag = att_short(s.get("att"))
        ws.append([
            i, stu, s.get("buy_type", ""), g.get("groupName", ""), course_of(g.get("groupName", "")), g.get("coTeacherName", ""),
            grade(s, timeout, unreply),
            "需回复" if unreply else "无需回复",
            "超时" if timeout else "正常", g.get("timeoutDuration", "") if timeout else "",
            (s["oldest"] or "")[:10], (s["newest"] or "")[:10], s["n_text"], s["n_stu"], s.get("n_ack", 0), s["n_tea"], s["voice"],
            coop_conclusion(s),
            att_txt, att_flag, " / ".join(s.get("leave", [])),
            "、".join(s["top_topics"]), "是" if s["unresolved"] else "否",
        ])
    for r in range(2, len(rows) + 2):
        for c in range(1, len(cols) + 1):
            ws.cell(r, c).border = border; ws.cell(r, c).alignment = wrap
    ws.freeze_panes = "A2"
    widths = {"A": 5, "B": 13, "C": 10, "D": 30, "E": 24, "F": 12, "G": 7, "H": 9, "I": 9, "J": 11,
              "K": 11, "L": 11, "M": 9, "N": 11, "O": 9, "P": 8, "Q": 8, "R": 34,
              "S": 16, "T": 16, "U": 32, "V": 20, "W": 10}
    for k, v in widths.items():
        ws.column_dimensions[k].width = v

    def build_todo_sheet(sheet_title, group_fn, action_label="To-Do 动作"):
        """构造一个「待办清单」sheet，按 group_fn(g) 分组排序（课程/联席）。"""
        ws = wb.create_sheet(sheet_title)
        cols2 = ["序号", "分组", "学生", "定档", "出勤(后台)", "是否需关注", "优先级", action_label, "依据", "群名", "超时时长"]
        ws.append(cols2)
        for c in range(1, len(cols2) + 1):
            cell = ws.cell(1, c); cell.fill = hdr_fill; cell.font = hdr_font; cell.border = border
        pri_fill = {"高": PatternFill("solid", fgColor="FDECEC"), "中": PatternFill("solid", fgColor="FFF3E0"), "低": PatternFill("solid", fgColor="EEF2F5")}
        idx = 0
        todo_rows = []
        for _, stu, s, g, timeout, unreply in rows:
            att_txt, att_flag = att_short(s.get("att"))
            for p, a, b in s["todos"]:
                todo_rows.append((group_fn(g), stu, p, a, b, g.get("groupName", ""),
                                 g.get("timeoutDuration", "") if timeout else "", grade(s, timeout, unreply), att_txt, att_flag))
        todo_rows.sort(key=lambda x: (x[0], {"高": 0, "中": 1, "低": 2}.get(x[2], 3), x[1]))
        for grp, stu, p, a, b, gn, td, gd, att_txt, att_flag in todo_rows:
            idx += 1
            ws.append([idx, grp, stu, gd, att_txt, att_flag, p, a, b, gn, td])
            r = ws.max_row
            for c in range(1, len(cols2) + 1):
                ws.cell(r, c).border = border; ws.cell(r, c).alignment = wrap
            ws.cell(r, 7).fill = pri_fill.get(p, pri_fill["低"])
        ws.freeze_panes = "A2"
        w2 = {"A": 5, "B": 24, "C": 13, "D": 7, "E": 16, "F": 16, "G": 7, "H": 40, "I": 24, "J": 28, "K": 11}
        for k, v in w2.items():
            ws.column_dimensions[k].width = v
        return idx

    if is_edu:
        # 教务：双维度「教务 To-Do」（按课程 + 按联席分组）
        n_course = build_todo_sheet("教务To-Do·按课程", lambda g: course_of(g.get("groupName", "")), "教务 To-Do")
        n_co = build_todo_sheet("教务To-Do·按联席", lambda g: g.get("coTeacherName") or "未分配联席", "教务 To-Do")
        idx = n_course + n_co
    else:
        # 联席：按课程分组的待办清单
        idx = build_todo_sheet("课程待办清单", lambda g: course_of(g.get("groupName", "")), "联席 To-Do")

    ws3 = wb.create_sheet("出勤明细(后台)")
    cols3 = ["学生", "课程", "总节数", "出勤", "缺勤", "看回放", "逐节(✓出勤/回放/✗缺)", "是否需关注"]
    ws3.append(cols3)
    for c in range(1, len(cols3) + 1):
        cell = ws3.cell(1, c); cell.fill = hdr_fill; cell.font = hdr_font; cell.border = border
    for _, stu, s, g, timeout, unreply in rows:
        a = s.get("att")
        if not a:
            ws3.append([stu, "—", "—", "—", "—", "—", "无后台记录", "—"])
        else:
            marks = " ".join(f"第{c['classSeq']}节" + ("✓" if c["isAttend"] == 1 else ("回" if c["isPlayback"] == 1 else "✗")) for c in a["classes"])
            _, flag = att_short(a)
            ws3.append([stu, a["course"], a["lessonCount"], a["attendCount"], a["absentCount"], a["playbackCount"], marks, flag])
        for c in range(1, len(cols3) + 1):
            ws3.cell(ws3.max_row, c).border = border; ws3.cell(ws3.max_row, c).alignment = wrap
    ws3.freeze_panes = "A2"
    w3 = {"A": 13, "B": 22, "C": 8, "D": 8, "E": 8, "F": 8, "G": 40, "H": 16}
    for k, v in w3.items():
        ws3.column_dimensions[k].width = v

    out = f"{wd}/{name}_学生待办清单.xlsx"
    wb.save(out)
    if is_edu:
        print(f"✅ XLSX 生成完成（教务·双维度·教务To-Do）｜ 总览 {len(rows)} 行 ｜ 按课程 {n_course} 项 + 按联席 {n_co} 项 ｜ {out}")
    else:
        print(f"✅ XLSX 生成完成（{role_cn}视角）｜ 总览 {len(rows)} 行 ｜ 待办 {idx} 项 ｜ {out}")


if __name__ == "__main__":
    main()
