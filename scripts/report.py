"""步骤9：生成学情分析 HTML（4 块结构 + Tab 切换）。

支持三种查看视角：
  - 联席视角（--co）：仅按「课程」维度分组切换（默认行为）。
  - 教务视角（--edu）：交互式「双维度」——
       ① 按课程维度：课程 Tab 切换，每个课程下可再「筛选联席」；
       ② 按联席维度：联席 Tab 切换下钻，学生卡标注所属联席。
       两种维度均可在报告顶部一键切换。

读取 analysis_merged.json，输出 <workdir>/<姓名>_学情分析.html。

报告结构（每张学生卡）：
  ① 速览卡（定档 + 配合度结论式 + 最该盯 1 件事 + 一句话总评）
  ② 出勤（后台权威）：逐节 ✓/回放/✗ + 是否需关注 + 请假备注
  ③ 知识掌握与薄弱点
  ④ 联席跟进 Action / 教务 To-Do（按角色；优先级动作 + 追踪节奏 + 改善信号）
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, sys.path[0])
import common
try:  # 聊天高频知识点的权威词表，用于小黑板「关联知识点」口径对齐
    from extract import TOPICS as CHAT_TOPIC_KEYS
except Exception:
    CHAT_TOPIC_KEYS = {}

WINDOW_DEFAULT = "2026-09-01"
# 响应时效提示阈值（与 extract.py 的 RESP_WARN_H 保持一致）：≥12h 在卡片提示「最长响应等待」，不计定档
RESP_WARN_H = 12.0


def course_of(group_name):
    gn = group_name or ""
    parts = gn.split("-")
    raw = parts[1] if len(parts) > 1 else gn
    if raw == "高二数学清北层E邵":
        raw = "高二数学清北层日E邵"
    m = re.match(r"高二数学(清北层|985层|211层|[\u4e00-\u9fa5]+层)(.+?)(邵|谢胡|胡[\u4e00-\u9fa5]*)$", raw)
    if m:
        return f"高二数学·{m.group(1)}·{m.group(2)}班（{m.group(3)}老师）", raw
    return raw, raw


ERR_MAP = {
    "立体几何/空间向量": "建系/法向量/坐标运算",
    "直线和圆": "位置关系与计算",
    "函数与导数": "求导与单调性讨论",
    "数列": "通项与求和",
    "圆锥曲线": "方程与几何性质",
    "三角函数": "恒等变换",
    "概率统计": "模型识别",
    "解三角形": "正余弦定理解算",
    "不等式/参数": "分类讨论",
    "计算/粗心": "计算符号与漏解",
}
IMPROVE = {
    "立体几何/空间向量": "同类建系/法向量题计算正确率提升",
    "直线和圆": "位置关系与计算类题正确率提升",
    "计算/粗心": "同类题计算错误次数下降",
    "函数与导数": "求导与单调性判断准确率提升",
    "数列": "通项/求和正确率提升",
}


def badge(text, cls):
    return f'<span class="badge {cls}">{text}</span>'


def signal_strip(s):
    """速览卡信号条：只保留值得一眼看到的量化信号，压成一行 chips。

    原先这里的 5 句长总评与别处重复、且淹没有效信息，现拆分去重：
      · 「近窗覆盖 / 条数统计」→ 卡片头 cmeta 已有；
      · 「高频问题集中在…」→ 第③块「知识掌握与薄弱点」已有 chips；
      · 仅保留 上面两处没有的 风险/状态信号（情绪、出勤、作业、礼貌回应）。
    无任何信号时给一句兜底，不再堆砌文字。
    """
    items = []
    if s.get("neg"):
        items.append((f"情绪/压力 {s['neg']} 处", "bad"))
    if s.get("att_n"):
        items.append((f"出勤/请假 {s['att_n']} 次", "warn"))
    if s.get("hw"):
        items.append((f"作业提及 {s['hw']} 次", ""))
    if s.get("pos"):
        items.append((f"礼貌回应 {s['pos']} 次", "ok"))
    # 响应时效（聊天记录自算）：迟缓置顶警示，偏慢次之
    if s.get("resp_slow"):
        items.insert(0, (f"响应迟缓 {s.get('resp_max_h', 0):.0f} 小时", "bad"))
    elif s.get("resp_max_h", 0) >= RESP_WARN_H:
        _t = f"最长响应等待 {s.get('resp_max_h', 0):.0f} 小时"
        if "已说明" in (s.get("resp_evi") or ""):
            _t += "（已说明安排）"
        items.insert(0, (_t, "warn"))
    if not items:
        return '<span class="si none">窗口内无明显风险信号</span>'
    return "".join(f'<span class="si {c}">{t}</span>' for t, c in items)


def four_dims(s):
    """联席关注四维（v3.7）：互动状态 / 催交作业是否交 / 批改后是否订正 / 提问意愿。

    这四维是「联席为学习效果负责」的直接抓手，只在联席 / 课程视角出现。
    出勤/请假不在此列——它归教务关切（见 att_block 的教务版）。"""
    cells = []
    lv_cls = {"ok": "ok", "warn": "warn", "bad": "bad"}
    # ① 互动状态
    cells.append(("互动状态", s.get("interact") or "—", lv_cls.get(s.get("interact_lv"), ""),
                  s.get("interact_evi") or ""))
    # ② 催交作业 → 是否交
    un, uo = s.get("urge_n", 0), s.get("urge_ok", 0)
    if un == 0:
        cells.append(("催交作业", "未催交", "", "窗口内无催交记录"))
    else:
        cls = "ok" if uo == un else ("bad" if uo == 0 else "warn")
        cells.append(("催交作业", f"{uo}/{un} 已提交", cls,
                      "均已在提醒后提交" if uo == un else f"提醒后仍未交 {un - uo} 次"))
    # ③ 批改 → 是否改正
    fn, fo = s.get("fix_n", 0), s.get("fix_ok", 0)
    if fn == 0:
        cells.append(("批改订正", "无订正要求", "", "窗口内无批改记录"))
    else:
        cls = "ok" if fo == fn else ("bad" if fo == 0 else "warn")
        cells.append(("批改订正", f"{fo}/{fn} 已改正", cls,
                      "批改后均已改正" if fo == fn else f"批改后未改 {fn - fo} 次"))
    # ④ 提问意愿
    ask = s.get("ask_lv", "无")
    cells.append(("提问意愿", ask, {"高": "ok", "中": "", "低": "warn", "无": "bad"}.get(ask, ""),
                  f"窗口内主动提问 {s.get('q', 0)} 处"))
    html = "".join(
        f'<div class="f4"><span class="f4k">{k}</span><span class="f4v {c}">{v}</span>'
        f'<span class="f4e">{e}</span></div>' for k, v, c, e in cells)
    return ('<div class="blk-h fm-h">🎯 联席关注四维（学习效果抓手）</div>'
            f'<div class="four">{html}</div>')


def coop_conclusion(s):
    n_stu, q = s["n_stu"], s["q"]
    n_sub = s.get("n_stu_sub", n_stu)
    n_ack = s.get("n_ack", 0)
    sc, pc = s.get("student_coop", 0), s.get("parent_coop", 0)
    if n_sub == 0:
        if n_stu == 0:
            return "窗口内学生/家长均无发言，暂无法判断配合度，需先主动确认学生状态与学习意愿。"
        return (f"窗口内学生/家长仅 {n_ack} 条礼节性回应（好的/收到/致谢，均无需回复），"
                "无实质互动，配合度暂无法判断，建议主动联系确认学习状态与意愿。")
    stu_lv = {0: "学生本人几乎不露面", 1: "学生自驱偏低", 2: "学生自驱偏低",
              3: "学生自驱中等", 4: "学生自驱较强", 5: "学生自驱标杆级"}[sc]
    par_lv = {0: "无家长互动", 1: "家长参与较少", 2: "家长配合偏弱",
              3: "家长配合一般", 4: "家长配合度较高", 5: "家长高度配合"}[pc]
    if sc <= 2 and pc >= 4:
        typ = "典型“家长推着走”型——家长高度配合但学生本人疏离，建议把学习主动权交还学生（要求学生本人提问、本人交作业）。"
    elif sc >= 4 and pc >= 4:
        typ = "学生自驱与家长配合双高——放手培养型，无需家长强力推动，重点给足深度练习与挑战题。"
    elif sc >= 4 and pc <= 2:
        typ = "学生自驱强、家长参与较少——良性自驱型，可适度引导家长了解学习进度。"
    elif sc <= 2 and pc <= 2:
        typ = "学生与家长配合均偏弱——需重点激活，先确认学习意愿与到课状态。"
    else:
        typ = ""   # 常规情形：两级结论已说明，不再叠加「配合度中等」类套话
    ev = f"（窗口内本人主动提问 {q} 处）" if q >= 5 else ""
    return f"{stu_lv}{ev}；{par_lv}。{typ}"


def grade(r, edu=False):
    """定档（红/黄/绿）。
    v3.7：出勤异常只计入「教务」定档 —— 教务为续报负责，出勤是核心关切；
    联席/课程为学习效果负责，定档只看学习与互动信号（响应迟缓 / 未解疑问 / 情绪 / 作业），
    避免「缺课」把联席的注意力从学习情况上带偏。"""
    s = r["s"]
    # 红：「响应迟缓」（聊天记录自算的等待时长 ≥ 24h）或「需回复」（会话尾部仍有未获回应提问）
    if s.get("resp_slow") or r["unreply"]:
        return "红", "重点跟进"
    hard = s["unresolved"] or s["neg"] > 0 or s.get("hw_neg", 0) > 0
    if edu:
        hard = hard or s.get("att_leave", 0) > 0
    if hard:
        return "黄", "需关注"
    return "绿", "正常推进"


def att_block(r, edu=False):
    """② 出勤块：按角色调整权重。
    - 教务（edu=True）：对续报负责，出勤是核心关切 → 逐节明细完整展开 + 醒目的「是否需关注」红黄绿。
    - 联席 / 课程（edu=False）：对学习效果负责，出勤只是背景信息 → 压成一行中性结论，
      逐节明细折叠，且不给红黄绿定档色（避免出勤抢走学习情况的注意力）。"""
    a = r["att"]
    lv2 = r["s"].get("leave")
    lvnote = (f'<div class="lvnote">聊天记录提及：{ " / ".join(lv2) }</div>') if lv2 else ""
    if not a:
        head = "② 出勤（教务主责）" if not edu else "② 出勤（后台权威）"
        return (f'<div class="blk"><div class="blk-h">{head}</div>'
                '<div class="noatt">后台无该生出勤记录（可能已退课 / 未排入该课程）</div>'
                f'{lvnote}</div>')
    chips = []
    for c in a["classes"]:
        seq, dt = c["classSeq"], c["timeStart"]
        if c["isAttend"] == 1:
            cls, txt = "ok", "✓"
        elif c["isPlayback"] == 1:
            cls, txt = "pb", "回放"
        else:
            cls, txt = "no", "✗"
        chips.append(f'<span class="lchip {cls}">第{seq}节 {txt}<i>{dt[5:]}</i></span>')
    n, ab, pbc = a["lessonCount"], a["absentCount"], a["playbackCount"]
    absent_seqs = [c["classSeq"] for c in a["classes"] if c["isAttend"] != 1]
    no_pb = [c["classSeq"] for c in a["classes"] if c["isAttend"] != 1 and c["isPlayback"] != 1]
    if ab == 0:
        lv, txt = "green", "全勤，无需特殊关注"
    elif ab == n:
        lv, txt = "red", f'全部 {n} 节未出勤' + ("（均已看回放）" if pbc == n else "（部分未看回放）") + " —— 建议电话确认是否继续跟课"
    elif ab >= 2 or len(no_pb) >= 1:
        lv, txt = "red", f'缺课 {ab} 节' + (f'，其中第{"、".join(map(str,no_pb))}节未看回放' if no_pb else "，均已看回放") + " —— 需关注"
    else:
        lv, txt = ("yellow", "缺 1 节且未看回放 —— 轻关注") if no_pb else ("green", "缺 1 节但已看回放 —— 常规")
    conc = f'共 {n} 节：出勤 {n-ab} · 缺 {ab} · 有回放 {pbc}。' + (f'缺席第 {"、".join(map(str,absent_seqs))} 节。' if absent_seqs else '')
    chips_html = "".join(chips)
    if edu:
        # 教务：出勤＝续报关注重点，逐节明细全展开，红黄绿醒目
        return f'''<div class="blk">
      <div class="blk-h">② 出勤（后台权威 · 续报关注重点）</div>
      <div class="attbar">{chips_html}</div>
      <div class="attconc">{conc}</div>
      <div class="attflag {lv}">是否需关注：{txt}</div>
      {lvnote}
    </div>'''
    # 联席 / 课程：出勤降为背景信息 —— 一行中性结论 + 折叠明细，不给定档色
    return f'''<div class="blk dim">
      <div class="blk-h">② 出勤（教务主责 · 联席仅作背景参考）</div>
      <div class="attconc">{conc}<span class="attmini">（{txt}）</span></div>
      <details class="attdet"><summary>逐节明细（共 {n} 节，展开查看）</summary>
      <div class="attbar">{chips_html}</div></details>
      {lvnote}
    </div>'''


def norm_topic_key(t):
    """知识点名归一：去掉 / 与 和 等连接符与空白，便于跨来源对齐。"""
    return re.sub(r"[\s/与和及、,，·\-]", "", str(t or ""))


def align_topic(t, std_keys):
    """把小黑板输出的『关联知识点』对齐到聊天高频的标准词表；无法对齐则原样返回。"""
    if not t:
        return ""
    if t in std_keys:
        return t
    nk = norm_topic_key(t)
    for k in std_keys:
        if norm_topic_key(k) == nk:
            return k
    return t


def board_focus(board):
    """小黑板实证 → 联席可执行动作（v3.7）。

    联席为学习效果负责，课堂作答是学习效果最直接的证据：聊天里风平浪静、但小黑板
    多轮「未收尾 / 空白」的学生，才是联席真正该盯的人。返回 (优先级, 动作, 依据) 或 None。
    """
    if not board:
        return None
    details = board.get("details", [])
    if not details:
        return None
    rounds = board.get("rounds", len(details))
    sc = board.get("status_counts", {})
    pb = sc.get("部分作答", 0)
    blank = sc.get("空白未提交", 0) + sc.get("空白", 0)
    errs = board.get("error_types", []) or []
    err_top = "、".join(errs[:2])
    tail = f"（典型：{err_top}）" if err_top else ""
    if blank >= 1 and blank >= pb:
        return ("中", f"有 {blank} 轮小黑板未提交：先确认是漏做还是漏交，补上并单独催一次",
                f"小黑板读图：{blank} 轮空白未提交（共 {rounds} 轮）")
    if pb >= 2 and pb >= max(2, rounds * 0.4):
        return ("中", f"课堂作答 {pb}/{rounds} 轮未收尾{tail}：课上盯他写完最后一步再交",
                f"小黑板读图：{pb} 轮部分作答，典型错误 {err_top or '—'}")
    return None


def board_block(sm):
    """学生卡第③块内的佐证子块：小黑板逐轮读图结论。
    默认只给「作答概况 + 涉及知识点 + 实证薄弱 + 典型错误」四行结论；
    逐轮明细折叠（≤2 轮自动展开），避免多轮学生（如 29 轮）把卡片撑得又长又啰嗦。"""
    if not sm:
        return ('<div class="sub-blk"><div class="sb-h">课堂小黑板表现（逐轮读图）</div>'
                '<div class="noatt">该生暂无小黑板图片（未被收录 / 课程未提供）</div></div>')
    from collections import Counter
    ds = sm.get("details", []) or []
    n = sm.get("rounds", 0) or len(ds)
    sc = sm.get("status_counts", {})
    dist = " · ".join(f"{k} {v}" for k, v in sc.items()) or "—"
    kb = sm.get("kb_topics", []) or []
    kb_chips = "".join(f'<span class="chip">{t}</span>' for t in kb) or '<span class="chip">—</span>'
    et = sm.get("error_topics", []) or []
    et_chips = ("".join(f'<span class="chip kb">🖍 {t}</span>' for t in et)
                or '<span class="chip">无（作答无明显错误）</span>')
    ec = Counter(e for d in ds for e in (d.get("errors") or []))
    errs = "、".join(f"{k}×{v}" for k, v in ec.most_common(3)) or "—"
    trend = sm.get("trend", "—")
    rows = ""
    for d in ds:
        cls = {"完整作答": "ok", "部分作答": "pb", "空白": "no"}.get(d.get("status"), "")
        tk = d.get("topic_knowledge", "") or "—"
        err = "；".join(d.get("errors") or []) or "—"
        stuck = d.get("stuck_at", "") or "—"
        rows += (f'<tr><td>{d.get("round","")}</td><td>{d.get("problem","")}</td>'
                 f'<td class="bs {cls}">{d.get("status","")}</td><td>{d.get("correct","")}</td>'
                 f'<td>{tk}</td><td>{err}</td><td>{stuck}</td></tr>')
    op = " open" if n <= 2 else ""
    return f'''<div class="sub-blk">
      <div class="sb-h">课堂小黑板表现（佐证依据）</div>
      <div class="kv"><span class="k">作答概况</span><span class="v">共 {n} 轮 ｜ {dist} ｜ 趋势：{trend}</span></div>
      <div class="kv"><span class="k">涉及知识点</span><span class="chips">{kb_chips}</span></div>
      <div class="kv"><span class="k">实证薄弱</span><span class="chips">{et_chips}</span></div>
      <div class="kv"><span class="k">典型错误</span><span class="v">{errs}</span></div>
      <details class="bdet"{op}><summary>逐轮明细（{n} 轮，点击展开）</summary>
        <table><tr><th>轮次</th><th>题目</th><th>作答</th><th>正确性</th><th>关联知识点</th><th>错误类型</th><th>卡点</th></tr>{rows}</table>
      </details>
    </div>'''


def card_html(r, edu=False, show_co_tag=False, board=None):
    s, g = r["s"], r["g"]
    co = g.get("coTeacherName") or "未分配联席"
    co_tag = (f'<span class="ctag">联席：{co}</span>') if (edu or show_co_tag) else ""
    bt = (s.get("buy_type") or "").strip()
    bt_cls = {"直播": "live", "回放": "replay"}.get(bt, "other")
    bt_tag = f'<span class="btag {bt_cls}">{bt}</span>' if bt else ""
    topics = "".join(f'<span class="chip">{t}</span>' for t in s["top_topics"]) or '<span class="chip">—</span>'
    errs = [ERR_MAP[t] for t in s["top_topics"] if t in ERR_MAP]
    err_html = "、".join(errs) if errs else "—"
    if s["neg"] == 0 and (s["pos"] > 0 or s["q"] >= 5):
        adv = "学习意愿强、沟通配合好，无明显情绪风险" if s["q"] >= 5 else "沟通配合好，关系良好"
    else:
        adv = "—"
    # 小黑板是「学习情况」的直接依据：其错误类型并入错误类型、正确作答佐证优势，
    # 关联知识点与聊天高频交叉验证薄弱点（口径先对齐到聊天高频的标准词表）
    board_errs, board_ok = [], False
    std_keys = list(CHAT_TOPIC_KEYS.keys())
    board_err_topics = []
    if board:
        for d in board.get("details", []):
            for e in d.get("errors", []):
                if e and e not in board_errs:
                    board_errs.append(e)
            if d.get("status") == "完整作答" and d.get("correct") in ("正确", "基本正确"):
                board_ok = True
        for t in (board.get("error_topics") or []):
            t2 = align_topic(t, std_keys)
            if t2 and t2 not in board_err_topics:
                board_err_topics.append(t2)
    if board_errs:
        err_html = (err_html + "；" if err_html not in ("—", "") else "") + "小黑板：" + "、".join(board_errs)
    if board_ok:
        adv = (adv + "；" if adv not in ("—", "") else "") + "课堂小黑板有完整作答且正确，解题落地能力较好"
    # 薄弱知识点：聊天高频话题 × 小黑板实证 交叉验证（双证据更精准）
    chat_topics = s["top_topics"]
    merged = list(chat_topics)
    for t in board_err_topics:
        if t not in merged:
            merged.append(t)
    weak_chips = ""
    for t in merged:
        dual = (t in chat_topics) and (t in board_err_topics)
        kb_only = (t in board_err_topics) and (t not in chat_topics)
        if dual:
            weak_chips += f'<span class="chip dual" title="聊天高频 ＋ 小黑板作答实证">🖍 {t}</span>'
        elif kb_only:
            weak_chips += f'<span class="chip kb" title="小黑板作答实证薄弱（聊天未提及）">🖍 {t}</span>'
        else:
            weak_chips += f'<span class="chip">{t}</span>'
    weak_chips = weak_chips or '<span class="chip">—</span>'
    gd, gd_label = grade(r, edu=edu)
    gdbadge = badge(gd_label, "red" if gd == "红" else ("orange" if gd == "黄" else "green"))
    status = ""
    if s.get("resp_slow"):
        status += badge(f'响应迟缓 {s.get("resp_max_h", 0):.0f}小时', "red")
    if r["unreply"]:
        status += badge("需回复", "red")
    if not s.get("resp_slow") and not r["unreply"]:
        status += badge("正常", "green")
    if s["unresolved"]:
        status += badge("有未解疑问", "orange")
    coop = coop_conclusion(s)
    # 联席/课程视角：把小黑板的「学习实证问题」并入 To-Do —— 聊天侧无「高」优先项时置顶，
    # 使「最该盯」优先落到学习问题上（而非「维持常规跟进」这类空结论）。
    todos_list = list(s["todos"])
    if not edu:
        _bf = board_focus(board)
        if _bf:
            # 聊天侧已有具体问题（高/中）→ 小黑板结论排其后；聊天侧只剩「低·维持常规」→ 置顶
            _idx = 0 if (not todos_list or todos_list[0][0] == "低") else 1
            todos_list.insert(_idx, _bf)
    top_todo = todos_list[0][1] if todos_list else ("常规家长沟通" if edu else "维持常规跟进")
    top_basis = todos_list[0][2] if todos_list else ""
    todos_html = "".join(
        f'<li><span class="pri pri-{p}">{p}</span> {a} <span class="basis">«{b}»</span></li>'
        for p, a, b in todos_list
    )
    cadence_default = "常规家长沟通" if edu else "常规跟进"
    cadence = {"高": "每 2–3 天回访一次", "中": "每周一次",
               "低": ("常规家长沟通即可" if edu else "常规跟进即可")}.get(
        s["todos"][0][0], cadence_default) if s["todos"] else cadence_default
    blk4_title = "④ 教务 To-Do（面向学生的跟进）" if edu else "④ 联席跟进 Action"
    cadence_label = "沟通节奏" if edu else "追踪节奏"
    imp = IMPROVE.get(s["top_topics"][0], "该生相关薄弱点正确率提升、疑问闭环") if s["top_topics"] else "学生整体状态稳定"
    return f"""
    <div class="card" data-co="{co}" data-name="{r['stu']}">
      <div class="chead">
        <div class="cname">{r['stu']}{bt_tag}</div>
        <div class="cgroup">{r['course_raw']}{co_tag}</div>
        <div class="cstatus">{gdbadge}{status}</div>
      </div>
      <div class="cmeta">近窗 {(s['oldest'] or '—')[:10]} ~ {(s['newest'] or '—')[:10]} ｜ 可分析 {s['n_text']} 条（生/家长 {s['n_stu']}，其中礼节 {s.get('n_ack', 0)} · 师 {s['n_tea']}）｜ 语音 {s['voice']} 条</div>
      <div class="blk"><div class="blk-h">① 速览卡</div>
        <div class="coop"><b>配合度</b>{coop}</div>
        <div class="watch"><div class="watch-h">最该盯<span class="watch-b">依据：{top_basis}</span></div><div class="watch-act">{top_todo}</div></div>
        {four_dims(s) if not edu else ""}
        <div class="sig">{signal_strip(s)}</div>
      </div>
      {att_block(r, edu=edu)}
      <div class="blk"><div class="blk-h">③ 学习掌握与薄弱点</div>
        <div class="kv"><span class="k">薄弱知识点</span><span class="chips">{weak_chips}</span></div>
        <div class="kv"><span class="k">错误类型</span><span class="v">{err_html}</span></div>
        <div class="kv"><span class="k">优势</span><span class="v">{adv}</span></div>
        {board_block(board)}
      </div>
      <div class="blk"><div class="blk-h">{blk4_title}</div>
        <ul class="todos">{todos_html}</ul>
        <div class="kv"><span class="k">{cadence_label}</span><span class="v">{cadence}</span></div>
        <div class="kv"><span class="k">改善信号</span><span class="v">{imp}</span></div>
      </div>
    </div>"""


CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f6f8;color:#1f2329;margin:0;padding:24px}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:24px;margin:0 0 4px}
.sub{color:#8a9099;font-size:13px;margin-bottom:14px}
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
.kpi{background:#fff;border:1px solid #e6e8eb;border-radius:10px;padding:14px 18px;flex:1;min-width:130px}
.kpi .n{font-size:26px;font-weight:700}
.kpi .l{font-size:12px;color:#8a9099;margin-top:2px}
.kpi.red .n{color:#e54545} .kpi.green .n{color:#2ba471} .kpi.orange .n{color:#ff8f1f}
.section{background:#fff;border:1px solid #e6e8eb;border-radius:12px;padding:18px;margin-bottom:20px}
.section h2{font-size:17px;margin:0 0 12px}
.section h3{font-size:15px;margin:0 0 10px;color:#3a5bdb}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #f0f1f3}
th{color:#8a9099;font-weight:600}
.badge{display:inline-block;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:4px}
.badge.red{background:#fdecec;color:#e54545} .badge.green{background:#e8f7f0;color:#2ba471} .badge.orange{background:#fff3e0;color:#ff8f1f}
.card{background:#fff;border:1px solid #e6e8eb;border-radius:12px;padding:16px;margin-bottom:14px}
.chead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.cname{font-size:17px;font-weight:700}
.cgroup{font-size:12px;color:#8a9099}
.ctag{display:inline-block;font-size:11px;color:#3a5bdb;background:#eef2ff;padding:1px 7px;border-radius:8px;margin-left:6px}
.btag{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:8px;margin-left:6px;vertical-align:middle}
.btag.live{background:#e8f7f0;color:#2ba471}
.btag.replay{background:#fff3e0;color:#ff8f1f}
.btag.other{background:#eef2f5;color:#6b7280}
.cstatus{margin-left:auto}
.cmeta{font-size:12px;color:#8a9099;margin:6px 0}
.chips{margin:6px 0}
.chip{display:inline-block;background:#eef2ff;color:#3a5bdb;font-size:11px;padding:2px 8px;border-radius:10px;margin:2px 4px 2px 0}
.sig{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0}
.si{font-size:11.5px;line-height:1.6;padding:2px 9px;border-radius:10px;background:#eef2f5;color:#6b7280}
.si.bad{background:#fdecec;color:#e54545}
.si.warn{background:#fff3e0;color:#ff8f1f}
.si.ok{background:#e8f7f0;color:#2ba471}
.si.none{background:#f2f4f7;color:#8a9099}
.todos ul{margin:0;padding-left:18px}
.todos li{font-size:13px;line-height:1.7}
.pri{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;border-radius:8px;margin-right:6px}
.pri-高{background:#fdecec;color:#e54545} .pri-中{background:#fff3e0;color:#ff8f1f} .pri-低{background:#eef2f5;color:#6b7280}
.basis{color:#8a9099;font-size:12px}
.blk{border-top:1px dashed #eef0f3;padding-top:10px;margin-top:10px}
.blk .blk{border-top:1px solid #f0f1f3;margin-top:8px;padding-top:8px}
.blk-h{font-size:13px;font-weight:700;color:#3a5bdb;margin-bottom:6px}
.chip.dual{background:#ffeded;color:#cf1322;font-weight:600;box-shadow:inset 0 0 0 1px #ffccc7}
.chip.kb{background:#fff7e6;color:#d46b08;box-shadow:inset 0 0 0 1px #ffd591}
.sub-blk{margin-top:8px;padding:8px 10px;background:#fafbfc;border:1px solid #eef0f3;border-radius:8px}
.sb-h{font-size:12px;font-weight:700;color:#5a6472;margin-bottom:6px}
details.bdet{margin-top:6px;border-top:1px dashed #eef0f3;padding-top:6px}
details.bdet>summary{cursor:pointer;font-size:12px;font-weight:600;color:#3a5bdb;list-style:none;outline:none}
details.bdet>summary::-webkit-details-marker{display:none}
details.bdet>summary:before{content:"▸ "}
details.bdet[open]>summary:before{content:"▾ "}
details.bdet table{margin-top:6px}
details.attdet{margin:4px 0}
details.attdet>summary{cursor:pointer;font-size:12px;font-weight:600;color:#3a5bdb;list-style:none;outline:none}
details.attdet>summary::-webkit-details-marker{display:none}
details.attdet>summary:before{content:"▸ "}
details.attdet[open]>summary:before{content:"▾ "}
.coop{font-size:13px;color:#1f2329;margin:2px 0 10px;line-height:1.7}
.coop b{color:#3a5bdb;margin-right:6px}
.watch{font-size:13px;background:#fff7ec;border-left:3px solid #ff8f1f;padding:7px 10px;border-radius:4px;margin:0 0 10px}
.watch-h{font-weight:700;color:#b45309;margin-bottom:3px}
.watch-h .watch-b{font-weight:400;color:#8a9099;font-size:12px;margin-left:8px}
.watch-act{color:#1f2329;line-height:1.65}
.kv{display:flex;gap:10px;font-size:13px;line-height:1.7;margin:4px 0;align-items:flex-start}
.kv .k{flex:0 0 64px;color:#8a9099;font-weight:600}
.kv .v{flex:1}
.note{font-size:12px;color:#8a9099;line-height:1.6}
.attbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.lchip{display:inline-flex;flex-direction:column;align-items:center;line-height:1.25;font-size:12px;font-weight:700;padding:5px 10px;border-radius:8px;min-width:64px}
.lchip i{font-style:normal;font-size:10px;font-weight:400;opacity:.75}
.lchip.ok{background:#e8f7f0;color:#2ba471}
.lchip.pb{background:#eef2ff;color:#3a5bdb}
.lchip.no{background:#fdecec;color:#e54545}
.attconc{font-size:13px;color:#4b5563;margin-bottom:6px}
.attflag{font-size:13px;border-radius:6px;padding:6px 10px;font-weight:600}
.attflag.red{background:#fdecec;color:#e54545}
.attflag.yellow{background:#fff3e0;color:#ff8f1f}
.attflag.green{background:#e8f7f0;color:#2ba471}
.lvnote{font-size:12px;color:#8a9099;margin-top:6px;line-height:1.6}
.fm-h{margin-top:12px}
.four{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.f4{background:#fafbfc;border:1px solid #eef0f3;border-radius:8px;padding:7px 9px}
.f4k{display:block;font-size:11px;color:#8a9099}
.f4v{display:block;font-size:15px;font-weight:700;color:#1f2329;margin:2px 0;line-height:1.3}
.f4v.ok{color:#0f8a4d}.f4v.warn{color:#c47b00}.f4v.bad{color:#d93a3a}
.f4e{display:block;font-size:11px;color:#a0a6ae;line-height:1.45}
.blk.dim{opacity:.9}
.blk.dim .blk-h{color:#8a9099}
.attmini{color:#8a9099;font-size:12px;margin-left:6px}
.noatt{font-size:13px;color:#8a9099}
.bs{font-weight:600}
.bs.ok{color:#2ba471} .bs.pb{color:#ff8f1f} .bs.no{color:#e54545}
.tabs{position:sticky;top:52px;z-index:20;background:#f5f6f8;padding:10px 0;display:flex;gap:8px;flex-wrap:wrap;border-bottom:2px solid #e6e8eb;margin-bottom:18px;max-height:212px;overflow-y:auto}
.tab{border:1px solid #d8dce3;background:#fff;color:#3a5bdb;font-size:13px;font-weight:600;padding:7px 12px;border-radius:10px;cursor:pointer;line-height:1.3;text-align:left;transition:.15s;max-width:232px}
.tab .tname{display:block;max-width:206px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tab .tcount{display:block;font-size:11px;font-weight:400;color:#8a9099;margin-top:2px}
.tab:hover{border-color:#3a5bdb}
.tab.active{background:#3a5bdb;color:#fff;border-color:#3a5bdb}
.tab.active .tcount{color:#dce3ff}
.ckpis{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.dimswitch{position:sticky;top:0;z-index:30;background:#f5f6f8;padding:10px 0 4px;display:flex;gap:8px;align-items:center}
.dimlabel{font-size:13px;color:#8a9099;font-weight:600;margin-right:4px}
.dimb{border:1px solid #d8dce3;background:#fff;color:#3a5bdb;font-size:13px;font-weight:600;padding:8px 16px;border-radius:10px;cursor:pointer}
.dimb.active{background:#1f2329;color:#fff;border-color:#1f2329}
.cofilterwrap{margin:4px 0 16px;font-size:13px;color:#3a5bdb;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.cofilter{font-size:13px;padding:6px 10px;border:1px solid #d8dce3;border-radius:8px;color:#1f2329;background:#fff;min-width:200px}
.cofilterwrap .cnt{font-size:12px;color:#8a9099;font-weight:400}
.searchbar{display:flex;gap:10px;align-items:center;margin:0 0 16px;background:#fff;border:1px solid #e6e8eb;border-radius:12px;padding:12px 16px}
.searchinp{font-size:14px;padding:9px 14px;border:1px solid #d8dce3;border-radius:10px;min-width:300px;outline:none;color:#1f2329}
.searchinp:focus{border-color:#3a5bdb;box-shadow:0 0 0 3px #eef2ff}
.searchcnt{font-size:12px;color:#8a9099;font-weight:600}
.searchclear{font-size:12px;color:#3a5bdb;cursor:pointer;border:none;background:none;padding:4px 8px}
"""


def build_section(prefix, sec_id, crows, title, show_filter, co_opts, is_all, edu=False, show_co_tag=False, boards_map=None):
    """构造一个分组区块（grp）。show_filter=True 时渲染「筛选联席」下拉（教务·按课程维度 / 课程维度用）。
    edu=True（教务视角）：学生卡第④块为「教务 To-Do」；否则为「联席跟进 Action」。
    show_co_tag=True（课程维度）：学生卡标注所属联席，便于一门课跨多个联席时区分归属。"""
    c_slow = sum(1 for r in crows if r["s"].get("resp_slow"))
    c_unres = sum(1 for r in crows if r["s"]["unresolved"])
    c_msgs = sum(r["s"]["n_text"] for r in crows)
    c_absent = sum(1 for r in crows if r["att"] and r["att"]["absentCount"] > 0)
    c_topic = Counter()
    for r in crows:
        for t in r["s"]["top_topics"]:
            c_topic[t] += 1
    topic_rows = "".join(
        f"<tr><td>{t}</td><td>{c}</td><td>{round(100*c/len(crows))}%</td></tr>"
        for t, c in c_topic.most_common())
    if show_filter and co_opts:
        opts = "".join(f'<option value="{co}">{co}（{cnt}）</option>' for co, cnt in co_opts.most_common())
        cofilter = (f'<div class="cofilterwrap">筛选联席：'
                    f'<select class="cofilter" onchange="coFilter(this)" data-target="{sec_id}">'
                    f'<option value="__all__">全部联席（{len(crows)}）</option>{opts}</select>'
                    f'<span class="cnt">该课程下共 {len(co_opts)} 名联席</span></div>')
    else:
        cofilter = ""
    cards = "".join(card_html(r, edu=edu, show_co_tag=show_co_tag,
                              board=(boards_map or {}).get(common.norm_name(r["stu"]))) for r in crows)
    todo_head = "教务 To-Do" if edu else "联席 To-Do"
    if edu:
        # 教务：出勤为核心关切 → KPI 末位放「有缺勤」
        kpi_extra = f'<div class="kpi orange"><div class="n">{c_absent}</div><div class="l">有缺勤(后台)</div></div>'
    else:
        # 联席 / 课程：为学习效果负责 → KPI 末位放「互动不足」「催交未交」「批改未改」
        c_weak_inter = sum(1 for r in crows if r["s"].get("interact_lv") in ("bad", "warn"))
        c_urge_fail = sum(1 for r in crows if r["s"].get("urge_n", 0) > r["s"].get("urge_ok", 0))
        c_fix_fail = sum(1 for r in crows if r["s"].get("fix_n", 0) > r["s"].get("fix_ok", 0))
        kpi_extra = (f'<div class="kpi orange"><div class="n">{c_weak_inter}</div><div class="l">互动不足</div></div>'
                     f'<div class="kpi orange"><div class="n">{c_urge_fail}</div><div class="l">催交未交</div></div>'
                     f'<div class="kpi orange"><div class="n">{c_fix_fail}</div><div class="l">批改未改</div></div>')
    disp = 'style="display:block"' if is_all else 'style="display:none"'
    return f'''
<div class="grp" id="{sec_id}" {disp}>
  {cofilter}
  <div class="ckpis">
    <div class="kpi"><div class="n">{len(crows)}</div><div class="l">学生数</div></div>
    <div class="kpi"><div class="n">{c_msgs}</div><div class="l">可分析消息</div></div>
    <div class="kpi red"><div class="n">{c_slow}</div><div class="l">响应迟缓群</div></div>
    <div class="kpi orange"><div class="n">{c_unres}</div><div class="l">未解疑问群</div></div>
    {kpi_extra}
  </div>
  <div class="section"><h3>高频知识点（{title}）</h3>
    <table><tr><th>知识点</th><th>涉及学生数</th><th>占比</th></tr>{topic_rows}</table></div>
  <div class="section"><h3>逐学生表现 &amp; {todo_head}（需关注置顶）</h3>{cards}</div>
</div>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", default=None, help="联席姓名（联席视角 / 课程内子筛选）")
    ap.add_argument("--edu", default=None, help="教务姓名（教务视角）")
    ap.add_argument("--course", default=None, help="课程名（课程维度），如 高二数学清北层日E邵")
    ap.add_argument("--window", default=WINDOW_DEFAULT)
    args = ap.parse_args()
    role, name = common.resolve_scope(args)
    is_edu = role == "edu"
    is_course = role == "course"
    role_cn = "教务" if is_edu else ("课程" if is_course else "联席")
    view_cn = "教务双维度" if is_edu else ("课程维度" if is_course else "联席")
    blk4_cn = "教务" if is_edu else "联席"
    scope_label = "该课程" if is_course else ("下属" if is_edu else "负责")
    wd = common.workdir(args)
    A = json.load(open(f"{wd}/analysis_merged.json", encoding="utf-8"))

    # 小黑板逐轮读图结论（可选）：boards_summary.json 由 pull_boards + read_boards 产出，按「姓名」索引
    boards_path = os.path.join(wd, "boards_summary.json")
    boards_map = json.load(open(boards_path, encoding="utf-8")) if os.path.exists(boards_path) else {}

    rows = []
    for stu, s in A.items():
        g = s["group"]
        # 「是否需回复」「响应时效」均不再依赖后台列（isReplied / isTimeout 实测与聊天事实不符），
        # 统一改用聊天记录分析结论：unresolved=会话尾部仍有未获回应提问；resp_slow=提问后 ≥24h 才见回复
        unreply = bool(s.get("unresolved"))
        slow = bool(s.get("resp_slow"))
        course, course_raw = course_of(g.get("groupName", ""))
        co = g.get("coTeacherName") or "未分配联席"
        score = (1 if slow else 0, 1 if s["unresolved"] else 0, s["n_text"])
        rows.append(dict(score=score, stu=stu, s=s, g=g, slow=slow,
                         unreply=unreply, course=course, course_raw=course_raw,
                         co=co, att=s.get("att")))
    rows.sort(key=lambda x: (-x["score"][0], -x["score"][1], -x["score"][2]))

    # 动态学科：从纳入的群中提取（角色只带某科时即显示该科，避免写死「高二数学」）
    _subj = []
    for r in rows:
        sj = (r["g"].get("subjectName") or common.subject_of(r["g"].get("groupName", "")) or "").strip()
        if sj and sj not in _subj:
            _subj.append(sj)
    subject_line = " / ".join(_subj) or "—"

    total_msgs = sum(r["s"]["n_text"] for r in rows)
    slow_n = sum(1 for r in rows if r["slow"])
    unres_n = sum(1 for r in rows if r["s"]["unresolved"])
    glob_topic = Counter()
    for r in rows:
        for t in r["s"]["top_topics"]:
            glob_topic[t] += 1
    glob_topic_rows = "".join(
        f"<tr><td>{t}</td><td>{c}</td><td>{round(100*c/len(rows))}%</td></tr>"
        for t, c in glob_topic.most_common())

    if is_edu:
        # ===== 教务双维度 =====
        by_course = {}
        by_co = {}
        for r in rows:
            by_course.setdefault(r["course"], []).append(r)
            by_co.setdefault(r["co"], []).append(r)
        course_order = [k for k, _ in Counter(r["course"] for r in rows).most_common()]
        co_order = [k for k, _ in Counter(r["co"] for r in rows).most_common()]
        co_all = Counter(r["co"] for r in rows)

        course_sections = [build_section("c", "c-all", rows, "全部课程（按课程维度）", True, co_all, True, True, boards_map=boards_map)]
        for ci, ck in enumerate(course_order):
            cr = by_course[ck]
            course_sections.append(build_section("c", f"c-{ci}", cr, ck, True,
                                                 Counter(r["co"] for r in cr), False, True, boards_map=boards_map))
        co_sections = [build_section("co", "co-all", rows, "全部联席（按联席维度）", False, None, True, True, boards_map=boards_map)]
        for ci, ck in enumerate(co_order):
            cr = by_co[ck]
            co_sections.append(build_section("co", f"co-{ci}", cr, ck, False, None, False, True, boards_map=boards_map))

        course_tabs = ['<button class="tab active" onclick="showTab(\'course\',\'c\',-1)">📊 全部（{0}）</button>'.format(len(rows))]
        for ci, ck in enumerate(course_order):
            n = len(by_course[ck])
            t = sum(1 for r in by_course[ck] if r["slow"])
            course_tabs.append(f'<button class="tab" onclick="showTab(\'course\',\'c\',{ci})" title="{ck}"><span class="tname">{ck}</span><span class="tcount">{n}人{t and f" · {t}迟缓" or ""}</span></button>')
        co_tabs = ['<button class="tab active" onclick="showTab(\'co\',\'co\',-1)">📊 全部（{0}）</button>'.format(len(rows))]
        for ci, ck in enumerate(co_order):
            n = len(by_co[ck])
            t = sum(1 for r in by_co[ck] if r["slow"])
            co_tabs.append(f'<button class="tab" onclick="showTab(\'co\',\'co\',{ci})" title="{ck}"><span class="tname">{ck}</span><span class="tcount">{n}人{t and f" · {t}迟缓" or ""}</span></button>')

        dim_bar = '''<div class="dimswitch">
  <span class="dimlabel">查看维度：</span>
  <button class="dimb active" data-d="course" onclick="showDim('course')">📚 按课程维度</button>
  <button class="dimb" data-d="co" onclick="showDim('co')">👥 按联席维度</button>
</div>'''
        panels = f'''{dim_bar}
<div class="dim-panel" id="dim-course">
  <div class="tabs">{''.join(course_tabs)}</div>
  {''.join(course_sections)}
</div>
<div class="dim-panel" id="dim-co" style="display:none">
  <div class="tabs">{''.join(co_tabs)}</div>
  {''.join(co_sections)}
</div>'''
        edu_note = "教务视角支持「双维度」：顶部可切换「按课程维度」（课程 Tab 下再筛联席）或「按联席维度」（按下属联席下钻，学生卡标注所属联席）。"
    elif is_course:
        # ===== 课程维度：单一课程，按「所属联席」下钻 =====
        co_all = Counter(r["co"] for r in rows)
        course_sections = [build_section("c", "c-all", rows, name, True, co_all, True, False, show_co_tag=True, boards_map=boards_map)]
        course_tabs = ['<button class="tab active" onclick="showTab(\'course\',\'c\',-1)">📊 全部（{0}）</button>'.format(len(rows))]
        panels = f'''<div class="dim-panel" id="dim-course">
  <div class="tabs">{''.join(course_tabs)}</div>
  {''.join(course_sections)}
</div>'''
        edu_note = "课程维度：单一课程下跨多个联席，顶部「筛选联席」可下钻到具体联席，学生卡标注所属联席；第④块为联席跟进 Action。"
    else:
        # ===== 联席视角（仅按课程） =====
        course_order = [k for k, _ in Counter(r["course"] for r in rows).most_common()]
        course_sections = [build_section("c", "c-all", rows, "全部课程", False, None, True, False, boards_map=boards_map)]
        for ci, ck in enumerate(course_order):
            cr = [r for r in rows if r["course"] == ck]
            course_sections.append(build_section("c", f"c-{ci}", cr, ck, False, None, False, False, boards_map=boards_map))
        course_tabs = ['<button class="tab active" onclick="showTab(\'course\',\'c\',-1)">📊 全部（{0}）</button>'.format(len(rows))]
        for ci, ck in enumerate(course_order):
            n = sum(1 for r in rows if r["course"] == ck)
            t = sum(1 for r in rows if r["course"] == ck and r["slow"])
            course_tabs.append(f'<button class="tab" onclick="showTab(\'course\',\'c\',{ci})" title="{ck}"><span class="tname">{ck}</span><span class="tcount">{n}人{t and f" · {t}迟缓" or ""}</span></button>')
        panels = f'''<div class="dim-panel" id="dim-course">
  <div class="tabs">{''.join(course_tabs)}</div>
  {''.join(course_sections)}
</div>'''
        edu_note = ""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}（{role_cn}）学生学情分析</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<h1>{name}（{role_cn}）· 学生学情分析{ '（双维度：按课程 / 按联席）' if is_edu else ('（课程维度 · 按联席下钻）' if is_course else '（联席维度 · 按课程下钻）') }</h1>
<div class="sub">分析窗口：聊天内容 {args.window} 至今 ｜ 出勤：学生管理后台「课节考勤」权威数据 ｜ 学科：{subject_line} ｜ 角色：{role_cn} ｜ 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
<div class="kpis">
  <div class="kpi"><div class="n">{len(rows)}</div><div class="l">{scope_label}学生（全量）</div></div>
  <div class="kpi"><div class="n">{total_msgs}</div><div class="l">可分析消息（近窗）</div></div>
  <div class="kpi red"><div class="n">{slow_n}</div><div class="l">响应迟缓群（聊天分析）</div></div>
  <div class="kpi orange"><div class="n">{unres_n}</div><div class="l">待回复群（聊天分析）</div></div>
</div>
<div class="searchbar">
  <input id="stuSearch" class="searchinp" type="text" placeholder="🔍 输入学生姓名搜索（当前维度内实时筛选）" oninput="doSearch(this.value)">
  <span class="searchcnt" id="searchCnt"></span>
  <button class="searchclear" onclick="document.getElementById('stuSearch').value='';doSearch('')">清空</button>
</div>
{panels}
<div class="section"><h2>三、整体画像：高频知识点（全量）</h2>
  <table><tr><th>知识点</th><th>涉及学生数</th><th>占比</th></tr>{glob_topic_rows}</table></div>
<div class="section"><h2>四、方法论与口径说明</h2>
<p class="note">
• 取数（群聊）：后台 <code>wxworkSessionStat/getList</code> 月维度与日维度并集得到该{role_cn}的全部学生群；<code>wxworkChat/getChatList</code> 取每群最近消息（倒序，单群上限 500 条），文本与语音转写均纳入。<br>
• 分析窗口：仅分析 <b>{args.window} 至今</b>的聊天内容。<br>
• 出勤（后台权威）：来自学生管理后台「我的学生 → 跟进学情 → 课节考勤」接口 <code>cClassAttend/getCClassAttendList</code>，逐节课记录 出勤✓ / 缺勤✗ / 是否看回放 + 回放有效时长 + 上课日期。<br>
• 出勤判定：「是否需关注」规则 —— 全缺 → 🔴 高风险建议电话；缺 ≥2 节 或 “缺了且未看回放” → 🔴 需关注；缺 1 节未看回放 → 🟡 轻关注；缺 1 节但已看回放 / 全勤 → 🟢 常规。<b>出勤的权重按角色区分：教务（为续报负责）视出勤为核心关切，逐节明细全展开并给红黄绿定档；联席（为学习效果负责）仅作背景参考，压成一行中性结论、明细折叠，且不参与联席定档。</b><br>
• 请假备注：仅摘录学生在群内自述的请假/看回放缘由，后台无请假原因字段，具体以教务记录为准。<br>
• 报告结构（{view_cn}）：① 速览卡（定档＋配合度结论＋{'<b>联席关注四维</b>（互动状态 / 催交作业是否交 / 批改后是否订正 / 提问意愿）＋' if not is_edu else ''}最该盯 1 件事）；② 出勤（后台权威{'· 教务续报关注重点，逐节展开' if is_edu else '· 教务主责，联席仅作背景参考，明细折叠且不参与定档'}）；③ 学习掌握与薄弱点——<b>结论先行</b>：先给薄弱知识点、错误类型与优势，再附<b>课堂小黑板逐轮读图</b>作为佐证依据。薄弱知识点由<b>「聊天高频话题 × 课堂小黑板读图实证」交叉验证</b>得出：两者同时命中为 🖍 双证据高亮，仅小黑板命中为 🖍 实证薄弱（聊天未提及）；小黑板读图会标注每题的<b>关联知识点</b>（与聊天高频同口径），据此把作答错误精准落到知识点上；④ {blk4_cn} To-Do（优先级动作＋{'沟通节奏' if is_edu else '追踪节奏'}＋改善信号）——{'教务视角的 To-Do 面向「家长沟通 / 反馈学生问题 / 建立信任 / 推动续报」' if is_edu else '联席视角的 To-Do 严格按<b>联席四维</b>排序：互动状态 → 答疑闭环 → 催交作业 → 批改后订正 → 提问意愿；<b>出勤/请假不再进入联席 To-Do</b>（归教务关切），避免「确认出勤/补回放」抢走联席的「最该盯」'}。{'教务视角支持双维度：① 按课程维度（课程 Tab 切换，每个课程下可「筛选联席」下钻到具体联席）；② 按联席维度（按下属联席下钻，学生卡标注所属联席）。' if is_edu else ('课程维度：单一课程下跨多个联席，顶部「筛选联席」下钻到具体联席，学生卡标注所属联席。' if is_course else '联席维度：同一联席名下的课程 Tab 切换，逐课程查看该联席负责的学生。')}<br>
• 配合度（结论式）：依据 学生本人发言密度/主动提问 与 家长代交/代沟通措辞 综合判定类型（如“家长推着走”型 / 放手培养型），数据源未区分发言者 ID，为启发式估算，供跟进策略参考。<br>
• 定档规则：🔴 红=聊天分析判定「响应迟缓」（学生提问后 ≥24 小时才见老师回复）或「需回复」（会话尾部仍有未获回应提问）；🟡 黄=有未解疑问、作业负面信号、负面情绪{'、出勤异常（仅教务）' if is_edu else ''}；🟢 绿=其余正常推进。<br>
• 「是否需回复」结论：由聊天记录分析得出，不再依赖后台「是否回复」列（实测存在误差）——以「最后一条老师消息之后是否仍有学生/家长实质提问」为准；该结论同时驱动「待回复群」KPI 与定档红。<br>
• 响应时效：同样弃用后台「超时」标记（实测与聊天事实不符，如 6 分钟即回复的群被标「超时 19 小时」），改由聊天记录自行推算 —— 以「学生/家长的一条实质提问」到「其后第一条老师消息（含图片/讲义等任意动作）」的间隔为一次等待，同一轮多条连续提问只按首条起算；≥24 小时判「响应迟缓」并计入定档红，≥12 小时在卡片提示「最长响应等待」（不计定档），窗口末尾老师仍未回复的提问归入「需回复」。<br>
</p></div>
</div>
<script>
function showDim(d){{
  document.querySelectorAll('.dim-panel').forEach(function(el){{el.style.display='none';}});
  document.querySelectorAll('.dimb').forEach(function(el){{el.classList.remove('active');}});
  var p=document.getElementById('dim-'+d); if(p) p.style.display='block';
  var b=document.querySelector('.dimb[data-d="'+d+'"]'); if(b) b.classList.add('active');
  showTab(d, d==='co'?'co':'c', -1);
}}
var lastIdx={{course:-1, co:-1}};
function showTab(panel, prefix, idx){{
  lastIdx[panel]=idx;
  var pn=document.getElementById('dim-'+panel); if(!pn) return;
  pn.querySelectorAll('.grp').forEach(function(el){{el.style.display='none';}});
  pn.querySelectorAll('.tab').forEach(function(el){{el.classList.remove('active');}});
  var el=document.getElementById(prefix+'-'+(idx<0?'all':idx)); if(el) el.style.display='block';
  var tabs=pn.querySelectorAll('.tab');
  if(tabs.length){{ (idx<0?tabs[0]:tabs[idx+1]).classList.add('active'); }}
  window.scrollTo({{top:0,behavior:'smooth'}});
}}
function coFilter(sel){{
  var sec=document.getElementById(sel.dataset.target); if(!sec) return;
  var v=sel.value;
  sec.querySelectorAll('.card').forEach(function(c){{
    c.style.display = (v==='__all__' || c.getAttribute('data-co')===v) ? 'block' : 'none';
  }});
}}
function doSearch(val){{
  var q=(val||'').trim().toLowerCase();
  var activeDim=document.querySelector('.dimb.active');
  var dim=activeDim?activeDim.getAttribute('data-d'):'course';
  var panel=document.getElementById('dim-'+dim); if(!panel) return;
  var cnt=document.getElementById('searchCnt');
  if(q===''){{
    showTab(dim, dim==='co'?'co':'c', lastIdx[dim]);
    if(cnt) cnt.textContent='';
    return;
  }}
  // 搜索时展开该维度所有分组（跨 Tab），再按姓名过滤
  panel.querySelectorAll('.grp').forEach(function(g){{ g.style.display='block'; }});
  var n=0;
  panel.querySelectorAll('.card').forEach(function(c){{
    var nm=(c.getAttribute('data-name')||'').toLowerCase();
    if(nm.indexOf(q)>=0){{ c.style.display='block'; n++; }}
    else {{ c.style.display='none'; }}
  }});
  // 隐藏没有匹配学生的空分组
  panel.querySelectorAll('.grp').forEach(function(g){{
    var any=[].slice.call(g.querySelectorAll('.card')).some(function(c){{ return c.style.display!=='none'; }});
    if(!any) g.style.display='none';
  }});
  if(cnt) cnt.textContent='匹配到 '+n+' 名学生（当前维度）';
}}
</script>
</body></html>"""
    out = f"{wd}/{name}_学情分析.html"
    open(out, "w", encoding="utf-8").write(html)
    if is_edu:
        print(f"✅ HTML 生成完成（教务双维度）｜ 课程数 {len(course_order)} · 联席数 {len(co_order)} ｜ {out}")
    elif is_course:
        print(f"✅ HTML 生成完成（课程维度）｜ 课程：{name} · 联席数 {len(co_all)} · 学生 {len(rows)} ｜ {out}")
    else:
        print(f"✅ HTML 生成完成（联席·课程维度）｜ 课程数 {len(course_order)} ｜ {out}")


if __name__ == "__main__":
    main()
