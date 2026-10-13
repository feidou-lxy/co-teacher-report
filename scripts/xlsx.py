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


# 响应时效档位阈值（与 extract.py 保持一致）
RESP_SLOW_H = 24.0
RESP_WARN_H = 12.0


def resp_tag(s):
    """响应时效档位（聊天记录自算）：及时 / 偏慢(≥12h) / 偏慢·已说明(≥24h 但老师已解释) / 迟缓(≥24h)。"""
    h = s.get("resp_max_h", 0.0)
    if s.get("resp_slow"):
        return "迟缓"
    if h >= RESP_SLOW_H:
        return "偏慢·已说明"
    if h >= RESP_WARN_H:
        return "偏慢"
    return "及时"


def resp_wait_txt(s):
    h = s.get("resp_max_h", 0.0)
    return f"{h:.1f} 小时" if h > 0 else "—"


def grade(s, slow, unreply, edu=False):
    """定档（与 report.py 同口径）。v3.7：出勤异常只计入「教务」定档 —— 教务为续报负责；
    联席/课程为学习效果负责，定档只看学习与互动信号。"""
    if slow or unreply:
        return "红"
    hard = s["unresolved"] or s["neg"] > 0 or s.get("hw_neg", 0) > 0
    if edu:
        hard = hard or s.get("att_leave", 0) > 0
    return "黄" if hard else "绿"


def four_cells(s):
    """联席四维（v3.7）：互动状态 / 催交作业是否交 / 批改后是否订正 / 提问意愿。"""
    un, uo = s.get("urge_n", 0), s.get("urge_ok", 0)
    fn, fo = s.get("fix_n", 0), s.get("fix_ok", 0)
    return [
        s.get("interact") or "—",
        (f"{uo}/{un} 已提交" if un else "窗口内未催交"),
        (f"{fo}/{fn} 已改正" if fn else "无订正要求"),
        s.get("ask_lv") or "—",
    ]


def board_focus(sm):
    """与 report.py 同口径：小黑板实证问题 → (优先级, 动作, 依据)；无实证问题返回 None。"""
    if not sm:
        return None
    details = sm.get("details", [])
    if not details:
        return None
    rounds = sm.get("rounds", len(details))
    sc = sm.get("status_counts", {})
    pb = sc.get("部分作答", 0)
    blank = sc.get("空白未提交", 0) + sc.get("空白", 0)
    errs = sm.get("error_types", []) or []
    err_top = "、".join(errs[:2])
    tail = f"（典型：{err_top}）" if err_top else ""
    if blank >= 1 and blank >= pb:
        return ("中", f"有 {blank} 轮小黑板未提交：先确认是漏做还是漏交，补上并单独催一次",
                f"小黑板读图：{blank} 轮空白未提交（共 {rounds} 轮）")
    if pb >= 2 and pb >= max(2, rounds * 0.4):
        return ("中", f"课堂作答 {pb}/{rounds} 轮未收尾{tail}：课上盯他写完最后一步再交",
                f"小黑板读图：{pb} 轮部分作答，典型错误 {err_top or '—'}")
    return None


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
    ap.add_argument("--course", default=None)
    args = ap.parse_args()
    role, name = common.resolve_scope(args)
    is_edu = role == "edu"
    is_course = role == "course"
    role_cn = "教务" if is_edu else ("课程" if is_course else "联席")
    wd = common.workdir(args)
    A = json.load(open(f"{wd}/analysis_merged.json", encoding="utf-8"))

    rows = []
    for stu, s in A.items():
        g = s["group"]
        # 「是否需回复」「响应时效」均不再依赖后台列（isReplied / isTimeout 实测与聊天事实不符），
        # 改用聊天记录分析结论：unresolved=会话尾部仍有未获回应提问；resp_slow=提问后 ≥24h 才见回复
        unreply = bool(s["unresolved"])
        slow = bool(s.get("resp_slow"))
        rows.append(((1 if slow else 0, 1 if s["unresolved"] else 0, s["n_text"]), stu, s, g, slow, unreply))
    rows.sort(key=lambda x: (-x[0][0], -x[0][1], -x[0][2]))

    # 小黑板逐轮读图结论（可选）：boards_summary.json 由 pull_boards + read_boards 产出，按「姓名」索引
    boards_path = os.path.join(wd, "boards_summary.json")
    boards_map = json.load(open(boards_path, encoding="utf-8")) if os.path.exists(boards_path) else {}

    def merged_todos(stu, s):
        """To-Do 列表：联席/课程视角下并入小黑板实证问题（置顶条件与 report.py 一致）。"""
        tl = list(s["todos"])
        if not is_edu:
            bf = board_focus(boards_map.get(common.norm_name(stu)))
            if bf:
                i = 0 if (not tl or tl[0][0] == "低") else 1
                tl.insert(i, bf)
        return tl

    wb = Workbook()
    thin = Side(style="thin", color="D0D3D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_fill = PatternFill("solid", fgColor="2F54EB")
    hdr_font = Font(bold=True, color="FFFFFF")
    wrap = Alignment(wrap_text=True, vertical="top")

    ws = wb.active; ws.title = "学生群总览"
    cols = ["序号", "学生", "购课类型", "群名", "课程", "所属联席", "定档", "是否需回复", "响应时效", "最长等待", "近窗起", "近窗止",
            "可分析消息", "生/家长消息", "礼节回应", "师消息", "语音条", "配合度结论",
            "互动状态", "催交作业", "批改订正", "提问意愿",
            "出勤(后台)", "是否需关注", "请假备注(聊天)", "高频知识点", "近期未解疑问"]
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        cell = ws.cell(1, c); cell.fill = hdr_fill; cell.font = hdr_font; cell.border = border
    for i, (_, stu, s, g, slow, unreply) in enumerate(rows, 1):
        att_txt, att_flag = att_short(s.get("att"))
        ws.append([
            i, stu, s.get("buy_type", ""), g.get("groupName", ""), course_of(g.get("groupName", "")), g.get("coTeacherName", ""),
            grade(s, slow, unreply, is_edu),
            "需回复" if unreply else "无需回复",
            resp_tag(s), resp_wait_txt(s),
            (s["oldest"] or "")[:10], (s["newest"] or "")[:10], s["n_text"], s["n_stu"], s.get("n_ack", 0), s["n_tea"], s["voice"],
            coop_conclusion(s),
            *four_cells(s),
            att_txt, att_flag, " / ".join(s.get("leave", [])),
            "、".join(s["top_topics"]), "是" if s["unresolved"] else "否",
        ])
    for r in range(2, len(rows) + 2):
        for c in range(1, len(cols) + 1):
            ws.cell(r, c).border = border; ws.cell(r, c).alignment = wrap
    ws.freeze_panes = "A2"
    widths = {"A": 5, "B": 13, "C": 10, "D": 30, "E": 24, "F": 12, "G": 7, "H": 9, "I": 9, "J": 11,
              "K": 11, "L": 11, "M": 9, "N": 11, "O": 9, "P": 8, "Q": 8, "R": 34,
              "S": 12, "T": 12, "U": 12, "V": 10,
              "W": 16, "X": 16, "Y": 32, "Z": 20, "AA": 10}
    for k, v in widths.items():
        ws.column_dimensions[k].width = v

    def build_todo_sheet(sheet_title, group_fn, action_label="To-Do 动作"):
        """构造一个「待办清单」sheet，按 group_fn(g) 分组排序（课程/联席）。"""
        ws = wb.create_sheet(sheet_title)
        cols2 = ["序号", "分组", "学生", "定档", "出勤(后台)", "是否需关注", "优先级", action_label, "依据", "群名", "最长等待"]
        ws.append(cols2)
        for c in range(1, len(cols2) + 1):
            cell = ws.cell(1, c); cell.fill = hdr_fill; cell.font = hdr_font; cell.border = border
        pri_fill = {"高": PatternFill("solid", fgColor="FDECEC"), "中": PatternFill("solid", fgColor="FFF3E0"), "低": PatternFill("solid", fgColor="EEF2F5")}
        idx = 0
        todo_rows = []
        for _, stu, s, g, slow, unreply in rows:
            att_txt, att_flag = att_short(s.get("att"))
            for p, a, b in merged_todos(stu, s):
                todo_rows.append((group_fn(g), stu, p, a, b, g.get("groupName", ""),
                                  resp_wait_txt(s), grade(s, slow, unreply, is_edu), att_txt, att_flag))
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
    elif is_course:
        # 课程维度：单一课程，按「所属联席」分组（联席跟进 Action）
        idx = build_todo_sheet("课程待办·按联席", lambda g: g.get("coTeacherName") or "未分配联席", "联席 To-Do")
    else:
        # 联席：按课程分组的待办清单
        idx = build_todo_sheet("课程待办清单", lambda g: course_of(g.get("groupName", "")), "联席 To-Do")

    ws3 = wb.create_sheet("出勤明细(后台)")
    cols3 = ["学生", "课程", "总节数", "出勤", "缺勤", "看回放", "逐节(✓出勤/回放/✗缺)", "是否需关注"]
    ws3.append(cols3)
    for c in range(1, len(cols3) + 1):
        cell = ws3.cell(1, c); cell.fill = hdr_fill; cell.font = hdr_font; cell.border = border
    for _, stu, s, g, slow, unreply in rows:
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

    # 小黑板逐轮读图分析（可选，boards_summary.json 已在 main 前部加载为 boards_map）
    if boards_map:
        ws4 = wb.create_sheet("小黑板分析(读图)")
        cols4 = ["学生", "轮次总数", "已读图", "作答分布", "涉及知识点", "实证薄弱(关联知识点)", "典型错误", "趋势", "逐轮明细(含关联知识点)"]
        ws4.append(cols4)
        for c in range(1, len(cols4) + 1):
            cell = ws4.cell(1, c); cell.fill = hdr_fill; cell.font = hdr_font; cell.border = border
        for _, stu, s, g, slow, unreply in rows:
            sm = boards_map.get(common.norm_name(stu))
            if not sm:
                continue
            sc = sm.get("status_counts", {})
            dist = " / ".join(f"{k}{v}" for k, v in sc.items()) or "—"
            kb = "、".join(sm.get("kb_topics", []) or []) or "—"
            et = "、".join(sm.get("error_topics", []) or []) or "无"
            details = " | ".join(
                f"{d.get('round','')}:{d.get('problem','')}[{d.get('topic_knowledge','') or '—'}]"
                f"({d.get('status','')}/{'正确' if d.get('correct')=='正确' else '有错'})"
                for d in sm.get("details", []))
            ws4.append([stu, sm.get("rounds", 0), sm.get("analyzed", 0), dist, kb, et,
                        "、".join(sm.get("error_types", [])) or "—", sm.get("trend", "—"), details])
            for c in range(1, len(cols4) + 1):
                ws4.cell(ws4.max_row, c).border = border; ws4.cell(ws4.max_row, c).alignment = wrap
        ws4.freeze_panes = "A2"
        w4 = {"A": 13, "B": 9, "C": 8, "D": 20, "E": 22, "F": 24, "G": 22, "H": 12, "I": 70}
        for k, v in w4.items():
            ws4.column_dimensions[k].width = v

    out = f"{wd}/{name}_学生待办清单.xlsx"
    wb.save(out)
    board_note = f" ｜ 小黑板 {len(boards_map)} 生" if boards_map else ""
    if is_edu:
        print(f"✅ XLSX 生成完成（教务·双维度·教务To-Do）｜ 总览 {len(rows)} 行 ｜ 按课程 {n_course} 项 + 按联席 {n_co} 项 ｜ {out}")
    elif is_course:
        print(f"✅ XLSX 生成完成（课程维度·按联席）｜ 总览 {len(rows)} 行 ｜ 待办 {idx} 项{board_note} ｜ {out}")
    else:
        print(f"✅ XLSX 生成完成（{role_cn}视角）｜ 总览 {len(rows)} 行 ｜ 待办 {idx} 项{board_note} ｜ {out}")


if __name__ == "__main__":
    main()
