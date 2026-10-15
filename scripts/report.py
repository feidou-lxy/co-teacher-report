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


def course_of(group_name, product_name=None):
    """课程 → (展示名, 课程名原串)。

    v4.7：**优先用后台「响应时效」列表直接给出的课程名 `productName`**
    （如「2026Q-SX-高二数学985层进化班六D谢胡」），不再从群名 groupName 切段解析；
    groupName 仅作回退，兼容 v4.6 及更早归档的 groups.json（无 productName 字段）。
    """
    raw = common.course_name(product_name, group_name)
    if raw == "高二数学清北层E邵":          # 旧群名写法缺「日」，回退路径专用修正
        raw = "高二数学清北层日E邵"
    return common.clean_course_name(raw), raw


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
    """配合度结论（v4.5：家长侧归因修正）。

    ⚠️ v4.5 修正的伪结论：**学生本人在群里活跃时，家长往往就隐身了**——
    这不是「家长配合一般」，而是「家长没必要出面」。旧口径把「家长发言少」
    直接映射为负面定级（家长参与较少 / 配合一般 / 偏弱），属**归因错位**。
    现改为条件判据，且家长侧证据换成**硬事实**——除主发言人外其他发言者的条数
    （`n_other`，即家长的实际出面量），不再用会被学生本人话语污染的启发式分数：
      · 学生自驱达标（sc ≥ 3）→ 家长侧**不下负面结论**，只做中性事实描述；
      · 只有学生自驱不足（sc ≤ 2）时，「家长也少出面」才构成真实的配合度问题；
      · 家长零发言痕迹 → 改判「无法判定」，建议先联系家长确认，不硬扣「配合偏弱」。
    """
    n_stu, q = s["n_stu"], s["q"]
    n_sub = s.get("n_stu_sub", n_stu)
    n_ack = s.get("n_ack", 0)
    sc = s.get("student_coop", 0)
    # 家长出面量（硬事实）。兼容 v4.4 及更早的分析产物（无 n_other 字段）→ 退化为旧启发式
    n_other = s.get("n_other")
    if n_other is None:
        _pc = s.get("parent_coop", 0)
        n_other = 8 if _pc >= 4 else (2 if _pc > 1 else 0)
    if n_sub == 0:
        if n_stu == 0:
            return "窗口内学生/家长均无发言，暂无法判断配合度，需先主动确认学生状态与学习意愿。"
        return (f"窗口内学生/家长仅 {n_ack} 条礼节性回应（好的/收到/致谢，均无需回复），"
                "无实质互动，配合度暂无法判断，建议主动联系确认学习状态与意愿。")
    stu_lv = {0: "学生本人几乎不露面", 1: "学生自驱偏低", 2: "学生自驱偏低",
              3: "学生自驱中等", 4: "学生自驱较强", 5: "学生自驱标杆级"}[sc]
    par_lv = ("家长高度配合" if n_other >= 20 else
              "家长配合度较高" if n_other >= 8 else
              "家长偶有露面" if n_other >= 3 else
              "家长出面很少" if n_other >= 1 else
              "群内无家长发言痕迹")
    ev = f"（窗口内本人主动提问 {q} 处）" if q >= 5 else ""
    # 家长身份是否可用：① 有家长措辞证据，或 ② 不存在「疑似同一人第二账号」的迹象
    sig = s.get("parent_sig", 0)
    dup = s.get("same_person", 0)
    known_parent = (sig > 0) or (not dup and n_other >= 1)
    # —— 学生自驱达标：家长少出面是常态，不判负面 ——
    if sc >= 3:
        if known_parent and n_other >= 8:
            par = f"家长在群内配合（另有 {n_other} 条发言）"
            typ = "学生自驱与家长配合双高——放手培养型，重点给足深度练习与挑战题。"
        elif known_parent:
            par = f"家长较少在群内出面（另有 {n_other} 条发言 · 学生自主推进中，家长无需代劳）"
            typ = ""
        elif n_other >= 1:
            par = f"群内另有其他成员发言 {n_other} 条（疑似学生本人另一账号，未按家长计）"
            typ = ""
        else:
            par = "家长较少在群内出面（群内未见家长发言 · 学生自主推进中，家长无需代劳）"
            typ = ""
        return f"{stu_lv}{ev}；{par}。{typ}"
    # —— 学生自驱不足：此时家长是否出面才真正说明问题 ——
    if known_parent and n_other >= 8:
        typ = "典型“家长推着走”型——家长高度配合但学生本人疏离，建议把学习主动权交还学生（要求学生本人提问、本人交作业）。"
    elif known_parent and n_other >= 1:
        typ = "学生在群内参与不足，家长虽有露面但跟进有限——需重点激活，先确认学习意愿与到课状态。"
    else:
        typ = ("学生在群内参与不足，且未见家长出面痕迹——建议主动联系家长，"
               "确认是「家长不在群/潜水」还是「确实无人跟进」。")
    # 家长身份不可用时，不硬套「高度配合 / 配合偏弱」定级，只陈述事实
    if known_parent:
        par_txt = par_lv
    elif n_other >= 1:
        par_txt = f"家长身份未确证（群内另有 {n_other} 条发言）"
    else:
        par_txt = "群内未见家长发言痕迹"
    return f"{stu_lv}{ev}；{par_txt}。{typ}"


def speakers_line(s):
    """群内发言人分布（可核查事实）：谁在说话、各说了多少条。"""
    sp = s.get("speakers") or []
    if not sp:
        return ""

    def _h(x):
        return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = " ｜ ".join(f"{_h(x['name'])} {x['n']} 条" for x in sp)
    return (f'<div class="spk" title="窗口内学生侧（学生本人 + 家长）发言人及条数，可回群逐条核对">'
            f'<b>群内发言人</b>{body}</div>')


def grade(r, edu=False):
    """定档（红/黄/绿）。

    v4.0 起：卡片右上角只展示「学习分层」（见 common.learning_level），不再渲染本函数的红黄绿徽章
    —— 用户反馈右上角同时出现两个分层标签时语义混淆。本函数保留备用（KPI / 排序若需可复用）。
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


def att_line(r, edu=False):
    """出勤一句话（v3.8）：不再单独占一个模块。
    - 联席 / 课程：只给一句话（出勤 X 次（含回放 Y 次），缺课 Z 次）。
    - 教务：一句话 + 折叠的逐节明细 + 是否需关注（教务对续报负责，出勤是核心关切）。
    """
    a = r["att"]
    lv2 = r["s"].get("leave")
    lvnote = (f'<span class="attmini">聊天提及：{ " / ".join(lv2) }</span>') if lv2 else ""
    if not a:
        return '<div class="attline none">出勤：后台无该生出勤记录（可能已退课 / 未排入该课程）</div>'
    n, eff, att, pb_only, pb, absent, no_pb = common.att_stats(a)
    if absent == 0 and pb_only == 0:
        lv, txt = "ok", f"全勤（{n}/{n} 节）"
    elif absent == 0:
        lv, txt = "ok", f"出勤 {eff}/{n} 次（其中 {pb_only} 次为看回放补齐），无缺课"
    else:
        lv = "bad" if absent >= 2 else "warn"
        tail = f"，第{'、'.join(map(str, no_pb))}节既没到课也没看回放" if no_pb else ""
        txt = (f"出勤 {eff}/{n} 次" + (f"（含回放 {pb_only} 次）" if pb_only else "")
               + f"，缺课 {absent} 次{tail}")
    line = (f'<div class="attline {lv}"><span class="attk">出勤</span>{txt}'
            f'<span class="attmini">（到课 {att} · 看回放 {pb}）</span>{lvnote}</div>')
    if not edu:
        return line
    # 教务：补逐节明细（折叠）与「是否需关注」
    chips = []
    for c in a.get("classes", []):
        seq, dt = c.get("classSeq"), c.get("timeStart") or ""
        if c.get("isAttend") == 1:
            cl, tx = "ok", "✓"
        elif c.get("isPlayback") == 1:
            cl, tx = "pb", "回放"
        else:
            cl, tx = "no", "✗"
        chips.append(f'<span class="lchip {cl}">第{seq}节 {tx}<i>{dt[5:]}</i></span>')
    if absent == 0:
        flag = "🟢 常规"
    elif absent >= 2 or eff == 0:
        flag = "🔴 需关注"
    else:
        flag = "🟡 轻关注"
    return (line
            + f'<details class="attdet"><summary>逐节明细（共 {n} 节，展开查看）</summary>'
              f'<div class="attbar">{"".join(chips)}</div></details>'
            + f'<div class="attmini">是否需关注：{flag}</div>')


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


def render_fine(fine, fallback_chips, err_tip=""):
    """细粒度薄弱点渲染（v4.2）：**知识点 → 具体题型/方法**，每条附原文出处，可回群核对。

    这是「薄弱知识点」的替代呈现（不再用课程大类 chips，那对联席没有可操作性）：
    如「直线和圆」下面到底是「含参直线过定点」还是「圆的一般式与圆心坐标」写错。

    有细粒度证据时**只呈现细粒度**（聊天侧「错误类型」与其同源、更粗，不再单列，避免用户
    反馈的「三部分内容重复」）；确实下钻不到题型时，才退回粗知识点 chips ＋ 一句聊天高频错因兜底。
    """
    if not fine:
        _tip = (f'<div class="note">聊天高频错因：{err_tip}</div>'
                if err_tip and err_tip not in ("—", "") else "")
        return (f'<div class="kv"><span class="k">薄弱知识点</span><span class="chips">{fallback_chips}</span></div>'
                + _tip +
                '<div class="note">窗口内未捕捉到可下钻到题型的明确证据，暂只列知识点范围（多来自课堂提问，老师未在群里具体点出问题）。</div>')
    kind_cls = {"老师指出": "tea", "学生提问": "stu", "小黑板": "bd"}
    blocks = []
    for topic, rows in fine.items():
        lis = ""
        for r in rows:
            k = r.get("kind") or ""
            cnt = r.get("count", 1)
            cnt_html = f'<span class="fcnt">×{cnt}</span>' if cnt > 1 else ""
            lis += (f'<li><span class="fitm">{r.get("item", "")}</span>'
                    f'<span class="fsrc {kind_cls.get(k, "")}">{k}</span>{cnt_html}'
                    f'<span class="fevi">{r.get("at", "")} 「{r.get("evi", "")}」</span></li>')
        blocks.append(f'<div class="fine-t">{topic}</div><ul class="fine-l">{lis}</ul>')
    return ('<div class="fine"><div class="fine-h">🎯 具体薄弱点（下钻到题型 / 方法，每条附原文出处，可回群核对）</div>'
            + "".join(blocks) + '</div>')


def board_block(sm):
    """学生卡「学习情况」内的佐证子块：课堂小黑板逐轮读图结论（v3.8 按**课次**组织）。

    目录层级：第 1 层=课次（上课日期）→ 第 2 层=该课次下每一轮（时间戳）→ 第 3 层=图片。
    v4.0：正文只留「一句话作答概况 + 典型错误」，「逐次课情况」表与「逐轮明细」表**均可折叠**
    （默认收起），需要时点击展开 —— 避免 8 次课 × 29 轮铺满卡片、淹没结论。"""
    if not sm:
        return ('<div class="sub-blk"><div class="sb-h">课堂小黑板（学习情况佐证）</div>'
                '<div class="noatt">该生暂无小黑板图片（未被收录 / 课程未提供）</div></div>')
    from collections import Counter
    ds = sm.get("details", []) or []
    n = sm.get("rounds", 0) or len(ds)
    lessons = sm.get("lessons") or []
    lc = sm.get("lesson_count") or len(lessons)
    sc = sm.get("status_counts", {})
    dist = " · ".join(f"{k} {v}" for k, v in sc.items()) or "—"
    # v4.0：删除「涉及知识点」「实证薄弱」两行 —— 与 ① 的「薄弱知识点」chips 同源重复
    # （实证薄弱点已由 ① 以 🖍 标出）；全量知识点在下方折叠的逐轮明细里逐条列出。
    ec = Counter(e for d in ds for e in (d.get("errors") or []))
    errs = "、".join(f"{k}×{v}" for k, v in ec.most_common(3)) or "—"
    trend = sm.get("trend", "—")
    lrows = ""
    for g in lessons:
        gd = " · ".join(f"{k}{v}" for k, v in (g.get("status_counts") or {}).items()) or "—"
        lrows += (f'<tr><td class="ld">{g.get("lesson_date") or "—"}</td>'
                  f'<td>{g.get("lesson") or "课次"}</td>'
                  f'<td>{g.get("rounds", 0)} 轮</td><td>{gd}</td></tr>')
    ltable = ('<table class="lstab"><tr><th>上课日期</th><th>课次</th><th>轮数</th><th>作答分布</th></tr>'
              f'{lrows}</table>') if lrows else ""
    rows = ""
    for d in ds:
        cls = {"完整作答": "ok", "部分作答": "pb", "空白": "no"}.get(d.get("status"), "")
        tk = d.get("topic_knowledge") or d.get("topic") or "—"
        err = "；".join(d.get("errors") or []) or "—"
        stuck = d.get("stuck_at", "") or "—"
        ld = d.get("lesson_date") or "—"
        rows += (f'<tr><td>{ld}</td><td>{d.get("problem","")}</td>'
                 f'<td class="bs {cls}">{d.get("status","")}</td><td>{d.get("correct","")}</td>'
                 f'<td>{tk}</td><td>{err}</td><td>{stuck}</td></tr>')
    # v4.0：正文只留「一句话作答概况 + 典型错误」，**每次课的情况与逐轮明细一律折叠**
    #（用户反馈：课次表铺在卡里太占版面，需要时再点开看）。
    return f'''<div class="sub-blk">
      <div class="sb-h">课堂小黑板（{lc} 次课 · {n} 轮 · 学习情况佐证）</div>
      <div class="kv"><span class="k">作答概况</span><span class="v">共 {n} 轮 ｜ {dist} ｜ 趋势：{trend}</span></div>
      <div class="kv"><span class="k">典型错误</span><span class="v">{errs}</span></div>
      <details class="bdet"><summary>逐次课情况（{lc} 次课，点击展开）</summary>{ltable}</details>
      <details class="bdet"><summary>逐轮明细（{n} 轮，点击展开）</summary>
        <table><tr><th>上课日期</th><th>题目</th><th>作答</th><th>正确性</th><th>关联知识点</th><th>错误类型</th><th>卡点</th></tr>{rows}</table>
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
    # 小黑板是「学习情况」的直接依据：其正确作答佐证优势；关联知识点与聊天高频交叉验证薄弱点
    # （口径先对齐到聊天高频的标准词表）。小黑板的具体错误逐条列在下方「课堂小黑板」块的
    # 「典型错误」行 —— 不再并入顶部错因串，避免与 ① 具体薄弱点重复（v4.2）。
    board_ok = False
    std_keys = list(CHAT_TOPIC_KEYS.keys())
    board_err_topics = []
    if board:
        for d in board.get("details", []):
            if d.get("status") == "完整作答" and d.get("correct") in ("正确", "基本正确"):
                board_ok = True
        for t in (board.get("error_topics") or []):
            t2 = align_topic(t, std_keys)
            if t2 and t2 not in board_err_topics:
                board_err_topics.append(t2)
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
    # 卡片右上角（v4.0）：**只展示「学习分层」这一条分层结论**。
    # 旧版这里还叠了「重点跟进 / 需关注 / 正常推进」（红黄绿定档）与「响应迟缓 / 需回复」裸徽章——
    # 前者与学习分层语义混淆（用户反馈「两个分层不知道什么意思」），后者在 ② 信号条、③ To-Do 里已有，
    # 属重复。故头部只留学习分层；近 3 天待回应是硬提醒，作为唯一附加红徽标保留。
    lv_key, lv_cn, lv_ev = common.learning_level(s, board)
    # v4.4：依据块逐条给出「加减分」，并汇总总分 —— 让口径可当场核对（用户要求「规则要能看懂」）
    _lv_sum = sum(d for _, _, d in lv_ev)
    _lv_tail = ""
    for _s, _t, _d in lv_ev:
        if "托底" in _t:
            _lv_tail = " · 成绩托底"
        elif "封顶" in _t:
            _lv_tail = " · 成绩封顶"
    lv_ev_html = "".join(
        (f'<span class="lvev"><b>{src}</b><i class="lvd {"neg" if d < 0 else ("pos" if d > 0 else "zero")}">'
         f'{d:+d}</i>{t}</span>') if d else f'<span class="lvev obs"><b>{src}</b>{t}</span>'
        for src, t, d in lv_ev) or '<span class="lvev">—</span>'
    head_right = (f'<span class="lvbadge sm {lv_key}" title="学习分层：综合小黑板作答 + 作业闭环 + 老师反馈 + 学生成绩 四源判定">{lv_cn}</span>')
    rt_items = (s.get("recent3") or {}).get("items") or []
    if rt_items:
        head_right += badge(f'近3天待回应 {len(rt_items)}', "red")
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
    # ③ 只此一处给动作清单：第 1 条即「最该盯」（v4.0 取消②里与之重复的「最该盯」独立块），
    # 用 .t1 高亮 + 「最该盯」标签，让行动入口唯一、不被重复内容稀释。
    _lis = []
    for _i, (p, a, b) in enumerate(todos_list):
        _cls = ' class="t1"' if _i == 0 else ""
        _tag = '<span class="t1tag">最该盯</span>' if _i == 0 else ""
        _lis.append(f'<li{_cls}><span class="pri pri-{p}">{p}</span>{_tag} {a} '
                    f'<span class="basis">«{b}»</span></li>')
    todos_html = "".join(_lis)
    cadence_default = "常规家长沟通" if edu else "常规跟进"
    cadence = {"高": "每 2–3 天回访一次", "中": "每周一次",
               "低": ("常规家长沟通即可" if edu else "常规跟进即可")}.get(
        s["todos"][0][0], cadence_default) if s["todos"] else cadence_default
    blk3_title = "③ 教务 To-Do（面向学生的跟进）" if edu else "③ 联席跟进 Action"
    cadence_label = "沟通节奏" if edu else "追踪节奏"
    imp = IMPROVE.get(s["top_topics"][0], "该生相关薄弱点正确率提升、疑问闭环") if s["top_topics"] else "学生整体状态稳定"
    # 细粒度薄弱点（v4.2）：聊天侧（extract 已算）+ 小黑板侧（读图卡点）合并，替代原来的粗知识点 chips
    fine = common.merge_fine(s.get("fine") or {}, common.fine_from_board(board))
    fine_html = render_fine(fine, weak_chips, err_html)
    # 成绩（v4.4 第四类证据）：有成绩时在 ① 顶部前置展示，无则整行不出现（不占版面）
    exam_row = ""
    _ex = s.get("exam") or {}
    _exl = _ex.get("latest")
    if _exl:
        _exlv_cls = {"高": "ok", "中": "warn", "偏低": "bad"}.get(_ex.get("level"), "")
        _exlv_cn = {"高": "较好", "中": "中等", "偏低": "偏低"}.get(_ex.get("level"), "未知")
        exam_row = (f'<div class="kv"><span class="k">成绩</span><span class="v">'
                    f'{_ex.get("why") or "—"} <span class="exlv {_exlv_cls}">{_exlv_cn}</span>'
                    f'<span class="basis">«{_exl.get("evi") or ""}»</span></span></div>')
    # v4.3：把「哪一类学生」预先算成 data-* 标记，供顶部 KPI 卡片点击筛选（前端零计算）
    _att_absent = common.att_stats(r["att"])[5] if r.get("att") else 0
    kflag = {
        "slow": 1 if r["slow"] else 0,
        "unres": 1 if s.get("unresolved") else 0,
        "inter": 1 if s.get("interact_lv") in ("bad", "warn") else 0,
        "urge": 1 if s.get("urge_n", 0) > s.get("urge_ok", 0) else 0,
        "fix": 1 if s.get("fix_n", 0) > s.get("fix_ok", 0) else 0,
        "absent": 1 if _att_absent else 0,
        "recent3": 1 if rt_items else 0,
    }
    kattr = " ".join(f'data-{k}="{v}"' for k, v in kflag.items())
    return f"""
    <div class="card" data-co="{co}" data-name="{r['stu']}" {kattr}>
      <div class="chead">
        <div class="cname">{r['stu']}{bt_tag}</div>
        <div class="cgroup">{r['course_raw']}{co_tag}</div>
        <div class="cstatus">{head_right}</div>
      </div>
      <div class="cmeta">近窗 {(s['oldest'] or '—')[:10]} ~ {(s['newest'] or '—')[:10]} ｜ 可分析 {s['n_text']} 条（生/家长 {s['n_stu']}，其中礼节 {s.get('n_ack', 0)} · 师 {s['n_tea']}）｜ 语音 {s['voice']} 条</div>
      <div class="blk"><div class="blk-h">① 学习情况<span class="blk-sub">分层结论见卡片右上角 · 依据如下</span></div>
        {exam_row}
        {fine_html}
        <div class="kv"><span class="k">优势</span><span class="v">{adv}</span></div>
        {board_block(board)}
        <details class="lvdet"><summary>学习分层判定依据（{lv_cn} · 累计 {_lv_sum:+d}{_lv_tail}）· 点击展开</summary>
          <div class="lvevs">{lv_ev_html}</div>
          <div class="note">判档口径（v4.4）：综合「① 课堂小黑板作答 ＋ ② 作业闭环 ＋ ③ 老师反馈 ＋ ④ 学生成绩（月考/期中）」四类证据加减分求和 —— 需重点关注（≤-3）／需关注（≤-1）／正常／良好（≥+2）。其中 <b>老师反馈按密度归一化</b>（每 10 轮小黑板，避免「老师常纠错」被误判）；<b>小黑板把「仅未完成收尾」与「实质错误」分开</b>（前者是课堂节奏，不记在能力账上）；<b>成绩为最强证据</b>：成绩好则档位托底「良好」，成绩偏低则封顶「需关注」—— 成绩好但作业不做，仍属学习效果较好；成绩差但作业都交，仍属需要关注。</div>
        </details>
      </div>
      <div class="blk"><div class="blk-h">② 配合度<span class="blk-sub">行为事实（怎么配合的）</span></div>
        {recent3_block(s)}
        <div class="coop">{coop}</div>
        {speakers_line(s)}
        {four_dims(s) if not edu else ""}
        {att_line(r, edu=edu)}
        <div class="sig">{signal_strip(s)}</div>
      </div>
      <div class="blk"><div class="blk-h">{blk3_title}<span class="blk-sub">按优先级排序 · 第 1 条即最该盯</span></div>
        <ol class="todos">{todos_html}</ol>
        <div class="kv"><span class="k">{cadence_label}</span><span class="v">{cadence}</span></div>
        <div class="kv"><span class="k">改善信号</span><span class="v">{imp}</span></div>
      </div>
    </div>"""


# ══════════════════════════════════════════════════════════════════════════
# v4.12：预警卡 —— 把「无学生卡」的在册学生（失联 / 出勤但无沟通）摊开写清
# ══════════════════════════════════════════════════════════════════════════
# 依据 v4.8 结构方案 §4.4：预警卡**不是空卡**，而是把「哪一轴缺、哪一轴正常」
# 摊开写清楚，并给出具体动作；每条证据都可回后台 / 回文件夹逐字核对。
#
# 🔒 防伪铁律（用户强要求，也是过去最容易出的伪结论）：
#    学习轴一旦有证据（出勤 / 小黑板作答）→ **只写「企微沟通通道失效」**，
#    绝不写「学生流失」。实测本课 6 名「出勤但无沟通」学生全部正常上课
#    （其中一名出勤 5/5、小黑板作答 21/29 张），若写「流失」就是完全错误的结论。
WARN_TIER_CLS = {"失联": "t1", "出勤但无沟通": "t2"}


def _warn_comm_axis(v, chat_days):
    """沟通轴一句话：群在不在、有没有人说话（可回后台逐条核对）。"""
    group, msg = v.get("group"), v.get("msgRecent") or 0
    if group == "missing":
        return "企微服务群<b>未采集到</b>（接口未返回该生 roomId，群列表里也没有对应群）"
    if msg == 0:
        return f"企微服务群已建，但近 {chat_days} 天<b>群内 0 条消息</b>（联席 · 教务 · 学生家长均未发言）"
    return (f"近 {chat_days} 天 {msg} 条消息（老师 {v.get('msgTeacher') or 0} 条 · "
            f"学生·家长实质发言 {v.get('subRecent') or 0} 条）")


def _warn_learn_axis(v):
    """学习轴一句话：出勤 ＋ 小黑板。返回 (出勤文案, 小黑板文案, 是否有学习证据)。"""
    n_att, n_les = v.get("nAtt") or 0, v.get("nLes") or 0
    bn, ball, br = v.get("boardN") or 0, v.get("boardAll") or 0, v.get("boardRounds") or 0
    att_txt = f"出勤 <b>{n_att}/{n_les}</b>" if n_les else "出勤接口无记录"
    if bn > 0:
        return att_txt, f"课堂小黑板作答 <b>{bn}/{ball} 张</b>（{br} 轮）", True
    if ball > 0:                      # 有图但全是「空白未提交」→ 人到了没写，不作学习证据
        return att_txt, f"课堂小黑板 {ball} 张<b>均为空白未提交</b>", n_att > 0
    return att_txt, "小黑板无记录", n_att > 0


def warn_card_html(v, chat_days=14, show_co_tag=False):
    """单张预警卡（左红边）—— 只渲染「未进入学生学情分析」的在册学生。"""
    tier = v.get("tier") or "失联"
    stu = v.get("stuName") or "—"
    course = v.get("_course") or common.course_name(v.get("productName"), "")
    bn = v.get("boardN") or 0
    has_ev = _warn_learn_axis(v)[2]
    comm_txt = _warn_comm_axis(v, chat_days)
    att_txt, board_txt, _ = _warn_learn_axis(v)

    # ---- 结论（防伪核心）----
    if tier == "出勤但无沟通":
        concl = ("企业微信沟通通道失效：学生<b>在正常上课</b>（有出勤记录），"
                 "但企微侧完全没有触达 —— 缺的是通道，不是学生。")
        if bn > 0:
            concl += f" 课堂小黑板有 <b>{bn} 张有效作答</b>，据此可确认<b>并非流失</b>。"
        act = ("① 教务<b>当日电话触达家长</b>：先说明孩子的课堂表现（有出勤 / 有作答），"
               "再确认服务群是漏建还是拉错；② 建群 / 纠正群后，联席在群内做一次"
               "<b>点名课堂反馈</b>，把通道真正用起来；③ 下次课前确认家长已进群、能收到消息。")
    elif has_ev:
        concl = ("企业微信沟通通道失效：学习侧有实证（学生在正常上课），"
                 "但群内零沟通、家长也未触达 —— <b>并非学生流失</b>。")
        act = ("① 教务<b>当日电话触达家长</b>，同步孩子课堂表现（说明课堂参与正常）；"
               "② 联席在群内做一次点名反馈，把群用起来；③ 下次课前确认家长已进群。")
    else:
        concl = ("无沟通<b>且</b>无学习痕迹：既无企微群消息，也无出勤 / 小黑板记录 —— "
                 "属<b>最需人工介入</b>的一档，先核实就读状态再定性。")
        act = ("① 教务<b>先核实就读状态</b>（是否已退课 / 跨班 / 从未建群）；"
               "② 若仍在读 → 立即建群并电话触达；③ 若已退课 → 从在册名单与预警中移除。")

    side_badge = ('<span class="lvbadge sm green">学习侧正常</span>' if has_ev
                  else '<span class="lvbadge sm red">学习侧无痕迹</span>')
    co_tag = (f'<span class="ctag">联席：{v.get("coTeacherName")}</span>'
              if (show_co_tag and v.get("coTeacherName")) else "")
    src = ("在册花名册 ✓ ｜ 群列表 " + ("✓" if v.get("group") == "found" else "✗ 未采集到")
           + " ｜ 聊天 " + ("✓" if v.get("chat") == "has" else "✗ 无记录")
           + " ｜ 小黑板 " + (f"✓ {bn} 张" if bn
                              else ("△ 有图但均空白未提交" if (v.get("boardAll") or 0) else "✗ 无记录")))
    return f'''
    <div class="card warncard" data-name="{stu}" data-warn="1" data-slow="0" data-unres="0"
         data-inter="0" data-urge="0" data-fix="0" data-absent="0" data-recent3="0"
         data-tier="{tier}">
      <div class="chead">
        <div class="cname">{stu}<span class="wtag {WARN_TIER_CLS.get(tier, 't1')}">{tier}</span></div>
        <div class="cgroup">{course}{co_tag}</div>
        <div class="cstatus">{side_badge}</div>
      </div>
      <div class="warnrow"><span class="warnk">沟通轴</span><span class="warnv">{comm_txt}</span></div>
      <div class="warnrow"><span class="warnk">学习轴</span><span class="warnv">{att_txt} ｜ {board_txt}
        {f'→ <b>学生在正常上课</b>' if has_ev else '→ 无学习侧证据'}</span></div>
      <div class="warnconcl">🚩 <b>结论</b>：{concl}</div>
      <div class="warnact">✅ <b>建议动作</b>：{act}</div>
      <div class="warnsrc">📎 数据来源：{src}</div>
      <div class="warnnote">本卡对象＝<b>在册但未进入学生学情分析</b>的学生（无任何聊天记录，故没有常规学生卡）。
        判定口径与分档说明见「🚨 失联预警」Tab。</div>
    </div>'''


CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f6f8;color:#1f2329;margin:0;padding:24px}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:24px;margin:0 0 4px}
.sub{color:#8a9099;font-size:13px;margin-bottom:14px}
.datanote{background:#fff7e6;border:1px solid #ffd591;border-left:4px solid #ff8f1f;border-radius:8px;padding:9px 13px;font-size:12.5px;color:#8a5300;margin:-6px 0 14px;line-height:1.6}
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
.todos{margin:0;padding-left:20px}
.todos li{font-size:13px;line-height:1.7;margin-bottom:3px}
.todos li.t1{background:#fff7ec;border-left:3px solid #ff8f1f;border-radius:4px;padding:5px 8px;margin-left:-4px;list-style-position:inside}
.t1tag{display:inline-block;font-size:10.5px;font-weight:700;color:#fff;background:#ff8f1f;padding:0 6px;border-radius:7px;margin:0 5px 0 4px;vertical-align:1px}
.pri{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;border-radius:8px;margin-right:6px}
.pri-高{background:#fdecec;color:#e54545} .pri-中{background:#fff3e0;color:#ff8f1f} .pri-低{background:#eef2f5;color:#6b7280}
.basis{color:#8a9099;font-size:12px}
.blk{border-top:1px dashed #eef0f3;padding-top:10px;margin-top:10px}
.blk .blk{border-top:1px solid #f0f1f3;margin-top:8px;padding-top:8px}
.blk-h{font-size:13px;font-weight:700;color:#3a5bdb;margin-bottom:6px}
.blk-sub{font-size:11.5px;font-weight:400;color:#a0a6ae;margin-left:8px}
.chip.dual{background:#ffeded;color:#cf1322;font-weight:600;box-shadow:inset 0 0 0 1px #ffccc7}
.chip.kb{background:#fff7e6;color:#d46b08;box-shadow:inset 0 0 0 1px #ffd591}
.fine{margin:6px 0 8px}
.fine-h{font-size:11.5px;font-weight:700;color:#8a9099;margin-bottom:4px}
.fine-t{font-size:12.5px;font-weight:700;color:#3a5bdb;margin:8px 0 3px}
.fine-l{margin:0;padding:0;list-style:none}
.fine-l li{font-size:12.5px;line-height:1.65;margin-bottom:3px;padding-left:11px;position:relative}
.fine-l li:before{content:"·";position:absolute;left:2px;color:#c3c8d1;font-weight:700}
.fitm{font-weight:600;color:#1f2329}
.fsrc{display:inline-block;font-size:10.5px;font-weight:700;padding:1px 6px;border-radius:7px;margin:0 4px}
.fsrc.tea{background:#fdecec;color:#e54545}
.fsrc.stu{background:#fff3e0;color:#c47b00}
.fsrc.bd{background:#eef2ff;color:#3a5bdb}
.fcnt{font-size:11px;color:#a0a6ae;margin-right:5px}
.fevi{color:#8a9099;font-size:11.5px}
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
.spk{font-size:11.5px;color:#6b7480;background:#f6f8fa;border-radius:6px;padding:4px 9px;margin:0 0 10px;line-height:1.6;word-break:break-all}
.spk b{color:#3a5bdb;margin-right:8px;font-weight:600}
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
.attline{font-size:13px;color:#4b5563;margin:8px 0;padding:7px 10px;background:#fafbfc;border:1px solid #eef0f3;border-radius:8px}
.attline .attk{font-weight:700;color:#8a9099;margin-right:8px}
.attline.ok{background:#e8f7f0;border-color:#cdeadd}
.attline.warn{background:#fff8ec;border-color:#f5e3c0}
.attline.bad{background:#fdecec;border-color:#f6cfcf}
.attline.none{background:#f3f5f8}
.lvbar{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px}
.lvlbl{font-size:12px;color:#8a9099}
.lvbadge{font-size:14px;font-weight:700;padding:3px 12px;border-radius:14px;color:#fff}
.lvbadge.sm{font-size:12px;padding:2px 10px;border-radius:11px;font-weight:700}
.lvbadge.red{background:#e54545}.lvbadge.orange{background:#ff8f1f}
.lvbadge.green{background:#2ba471}.lvbadge.blue{background:#3a5bdb}
details.lvdet{margin-top:8px;border-top:1px dashed #eef0f3;padding-top:7px}
details.lvdet>summary{cursor:pointer;font-size:12px;font-weight:600;color:#3a5bdb;list-style:none;outline:none}
details.lvdet>summary::-webkit-details-marker{display:none}
details.lvdet>summary:before{content:"▸ "}
details.lvdet[open]>summary:before{content:"▾ "}
details.lvdet .lvevs{margin-top:6px}
.lvevs{display:flex;flex-wrap:wrap;gap:6px;flex:1}
.lvev{font-size:11.5px;color:#5a6472;background:#f3f5f8;border-radius:6px;padding:2px 8px}
.lvev b{color:#3a5bdb;margin-right:4px;font-weight:600}
.lvev.obs{opacity:.72}
.lvd{font-style:normal;font-weight:600;margin-right:5px;font-variant-numeric:tabular-nums}
.lvd.neg{color:#e54545} .lvd.pos{color:#2ba471} .lvd.zero{color:#98a2b3}
.exlv{font-size:11px;border-radius:5px;padding:1px 6px;margin-left:6px}
.exlv.ok{background:#e8f7f0;color:#2ba471} .exlv.warn{background:#fff3e0;color:#ff8f1f}
.exlv.bad{background:#fdecec;color:#e54545}
.lstab{margin:6px 0 8px}
.lstab .ld{font-weight:600;color:#3a5bdb}
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
/* v4.3：KPI 小卡 = 可点击筛选入口 */
.kpi.kbl,.kpi.clickable{cursor:pointer;transition:transform .15s,box-shadow .15s}
.kpi.clickable:hover{transform:translateY(-2px);box-shadow:0 6px 16px rgba(58,91,219,.16)}
.kpi.clickable:active{transform:translateY(0)}
/* v4.10 三大 Tab */
.maintabs{display:flex;gap:6px;border-bottom:2px solid #e6e8eb;margin-bottom:18px;flex-wrap:wrap}
.mtab{border:none;background:transparent;font-size:14.5px;font-weight:700;color:#8a9099;padding:11px 18px;cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px;border-radius:8px 8px 0 0;display:flex;align-items:center;gap:7px}
.mtab:hover{color:#3a5bdb;background:#fafbfe}
.mtab.active{color:#1f2329;border-bottom-color:#3a5bdb;background:#f7f9ff}
.mcnt{font-size:11.5px;font-weight:700;padding:1px 7px;border-radius:9px;background:#eef1f6;color:#5b6472}
.mcnt.warn{background:#ffeded;color:#cf1322}
.mcnt.ok{background:#eaf7f0;color:#2ba471}
.mainpanel{animation:fadein .18s ease-out}
@keyframes fadein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.kpi.kbl:hover{transform:translateY(-2px);box-shadow:0 6px 16px rgba(58,91,219,.16)}
.kpi.kbl:active{transform:translateY(0)}
.kpi.kbl.on{outline:2px solid #3a5bdb;outline-offset:1px;box-shadow:0 6px 16px rgba(58,91,219,.24)}
.kpihint{font-size:11.5px;color:#8a9099;margin:-6px 0 16px}
.kpicnt{color:#3a5bdb;font-weight:700;margin-left:6px}
details.meth{margin:20px 0 8px;border-top:1px solid #eef0f3;padding-top:12px}
details.meth>summary{cursor:pointer;font-size:15px;font-weight:700;color:#3a5bdb;list-style:none;outline:none}
details.meth>summary::-webkit-details-marker{display:none}
details.meth>summary:before{content:"▸ "}
details.meth[open]>summary:before{content:"▾ "}
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
.rtbanner{background:#fff;border:1px solid #f6cfcf;border-left:4px solid #e54545;border-radius:10px;margin-bottom:16px;overflow:hidden}
.rtbanner.ok{border-color:#cdeadd;border-left-color:#2ba471;background:#f4fbf7;padding:12px 16px}
.rtbanner>summary{cursor:pointer;list-style:none;outline:none;padding:11px 16px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;background:#fff9f9}
.rtbanner>summary::-webkit-details-marker{display:none}
.rtbanner[open]>summary{border-bottom:1px solid #f6e3e3}
.rt-t{font-size:14px;font-weight:700;color:#cf1322}
.rt-s{font-size:12px;color:#8a9099}
.rt-more{margin-left:auto;font-size:12px;font-weight:600;color:#cf1322}
.rt-more:after{content:" ▾"}
.rtbanner[open] .rt-more:after{content:" ▴"}
.rtbody{padding:12px 16px 14px}
.rtbanner h2{font-size:15px;margin:0 0 6px;color:#cf1322}
.rtbanner.ok h2{color:#0f8a4d}
.rtbanner .rtsub{font-size:12px;color:#8a9099;margin-bottom:8px}
.rtbanner table{margin-top:2px}
.rtk{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;border-radius:8px;margin-right:6px;white-space:nowrap}
.rtk.miss{background:#e54545;color:#fff}
.rtk.open{background:#ff8f1f;color:#fff}
.rtbox{background:#fdecec;border:1px solid #f6cfcf;border-left:3px solid #e54545;border-radius:8px;padding:8px 10px;margin:0 0 10px}
.rt-h{font-size:13px;font-weight:700;color:#cf1322;margin-bottom:4px}
.rtlist{margin:0;padding-left:16px}
.rtlist li{font-size:12.5px;line-height:1.6;color:#4b5563;margin-bottom:4px}
.rtlist .note{font-size:11.5px}
/* v4.8 在册全覆盖与失联预警 */
.covsec{background:#fff;border:1px solid #e6e8eb;border-radius:12px;padding:16px 18px;margin-bottom:16px}
.covsec h2{margin:0 0 10px;font-size:16px}
.covbar{font-size:13px;color:#4b5563;background:#f7f9fc;border:1px solid #e6eaf2;border-radius:8px;padding:10px 13px;margin-bottom:12px;line-height:1.8}
.covbar b{color:#1f2329}
.covbar .hl{color:#cf1322;font-weight:700}
/* v4.10.2：覆盖概览与判定口径默认折叠（只是换个默认可见性，内容不动） */
.covhead{margin:0 0 12px}
.covhead>summary{background:#f7f9fc;border:1px solid #e6eaf2}
.covhead .covbar{border:none;background:transparent;padding:0;margin:0}
.covtab{width:100%;border-collapse:collapse;font-size:13px}
.covtab th,.covtab td{border-bottom:1px solid #eef0f3;padding:7px 9px;text-align:left;vertical-align:top}
.covtab th{background:#fafbfc;color:#6b7280;font-weight:600;font-size:12px}
.covtab tr:last-child td{border-bottom:none}
/* v4.10.3：档位表「人数」可点击 —— 就在本行下方展开该档名单 */
.covn{cursor:pointer;color:#3a5bdb;font-weight:700;padding:1px 4px;border-radius:5px}
.covn:hover{background:#eef3ff}
.covn .covar{font-size:9px;color:#9db1e8}
.covn .covar:before{content:"▾ "}
.covn.on .covar:before{content:"▴ "}
.covtab .covdet>td{background:#fbfcfe;padding:0;border-bottom:1px solid #eef0f3}
.covdetin{padding:4px 10px 10px}
.covsub{width:100%;border-collapse:collapse;font-size:12.5px}
.covsub th,.covsub td{border-bottom:1px solid #f1f3f6;padding:6px 8px;text-align:left;vertical-align:top}
.covsub th{color:#8a9099;font-weight:600;font-size:11.5px}
.covsub tr:last-child td{border-bottom:none}
.tierk{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:8px;white-space:nowrap;color:#fff}
.tierk.t1{background:#e54545}
.tierk.t2{background:#ff8f1f}
.tierk.t3{background:#e0a80f}
.tierk.t4{background:#3a5bdb}
.tierk.t5{background:#2ba471}
.tierk.t0{background:#c3c8d0}
.covlist{margin-top:12px;border:1px solid #eceff3;border-radius:8px;overflow:hidden}
.covlist>summary{cursor:pointer;list-style:none;padding:9px 13px;font-size:13px;font-weight:600;color:#3a5bdb;background:#fafbfc;outline:none}
.covlist>summary::-webkit-details-marker{display:none}
.covlist[open]>summary{border-bottom:1px solid #eceff3}
.covlist .in{padding:10px 13px;max-height:430px;overflow:auto}
.covlist table{width:100%;border-collapse:collapse;font-size:12.5px}
.covlist th,.covlist td{border-bottom:1px solid #f1f3f6;padding:6px 8px;text-align:left;vertical-align:top}
.covlist th{color:#8a9099;font-weight:600;font-size:11.5px;position:sticky;top:0;background:#fff}
.covevi{color:#6b7280}
.covcause{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;background:#f2f4f7;color:#6b7280;white-space:nowrap}
.covdeg{margin-top:10px;font-size:11.5px;color:#8a9099;line-height:1.7}
/* v4.9 主讲备课建议（下节课重点讲什么） */
.tadv{margin-top:14px;border:1px solid #ffe0b2;border-radius:10px;overflow:hidden;background:#fffdf8}
.tadv-h{padding:9px 13px;font-size:13.5px;font-weight:700;color:#ad6800;background:#fff7e6;border-bottom:1px solid #ffe0b2}
.tadv-sum{padding:11px 13px;font-size:13px;line-height:1.9;color:#3d3d3d;border-bottom:1px dashed #ffe0b2;background:#fffefa}
.tadv-sum b{color:#ad6800}
.tadv-sum .hit{color:#cf1322;font-weight:700}
.tadv-tab{width:100%;border-collapse:collapse;font-size:12.5px}
.tadv-tab th,.tadv-tab td{border-bottom:1px solid #f6f1e7;padding:7px 10px;text-align:left;vertical-align:top}
.tadv-tab th{color:#8a9099;font-weight:600;font-size:11.5px;background:#fffcf5;white-space:nowrap}
.tadv-tab td.rk{font-weight:700;color:#ad6800;width:32px}
.tadv-tab td.nm{font-weight:700;color:#1f2329}
.tadv-tab td.n{white-space:nowrap;color:#4b5563}
.tadv-tab td.n b{color:#cf1322}
.tadv .mute{color:#b3aca0;font-weight:400}
.tadv .hard{color:#cf1322;font-weight:700;margin-left:5px;font-size:11px}
.tadv-tab .itm{color:#7c5c1e;line-height:1.7}
.tadv-cap{padding:6px 13px;font-size:11px;color:#a09a8c;background:#fffcf5;border-bottom:1px dashed #ffe0b2;line-height:1.7}
.tadv-ev>summary{cursor:pointer;list-style:none;padding:8px 13px;font-size:12px;color:#ad6800;background:#fffcf5;outline:none;font-weight:600}
.tadv-ev>summary::-webkit-details-marker{display:none}
.tadv-ev[open]>summary{border-bottom:1px dashed #ffe0b2}
.tadv-ev .in{padding:8px 13px;max-height:420px;overflow:auto}
.tadv-ev .fevi{color:#a09a8c;font-size:11.5px}
.tadv-note{padding:7px 13px;font-size:11px;color:#a09a8c;background:#fffcf5;line-height:1.75;border-top:1px dashed #ffe0b2}
/* v4.13：Tab② 「下节课可重点关注的学生」子模块 */
.tadv-focus{border-top:1px dashed #ffe0b2;padding:10px 13px 11px;background:#fffdf8}
.fadv-h{font-size:13px;font-weight:700;color:#ad6800;margin:0 0 4px}
.fadv-s{font-size:12px;color:#6b7280;line-height:1.75;margin-bottom:8px}
.fadv-s b{color:#cf1322}
.foc-tab{width:100%;border-collapse:collapse;font-size:12.5px}
.foc-tab th,.foc-tab td{border-bottom:1px solid #f6f1e7;padding:6px 9px;text-align:left;vertical-align:top}
.foc-tab th{color:#8a9099;font-weight:600;font-size:11.5px;background:#fffcf5;white-space:nowrap}
.foc-tab td.fst{font-weight:700;color:#1f2329;white-space:nowrap}
.foc-tab td.fw{min-width:132px}
.foc-tab td.fact{color:#7c5c1e;line-height:1.7}
.foc-tab td.fev{color:#a09a8c;font-size:11.5px;line-height:1.65;max-width:310px}
.foc-tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:9px;margin:1px 4px 1px 0;white-space:nowrap}
.foc-tag.bad{color:#cf1322;background:#fff1f0;border:1px solid #ffccc7}
.foc-tag.warn{color:#ad6800;background:#fff7e6;border:1px solid #ffe0b2}
.foc-tag.bd{color:#0958d9;background:#f0f5ff;border:1px solid #d6e4ff}
.foc-more{margin-top:6px}
.foc-more>summary{cursor:pointer;list-style:none;padding:4px 0;font-size:11.5px;color:#ad6800;font-weight:600;outline:none}
.foc-more>summary::-webkit-details-marker{display:none}
.foc-more[open]>summary{border-bottom:1px dashed #ffe0b2;margin-bottom:4px}
.fadv-note{font-size:11px;color:#a0a6ae;margin-top:7px;line-height:1.7}
/* v4.12：预警卡（v4.8 §4.4 落地）—— 无学生卡的在册学生，「哪一轴缺、哪一轴正常」摊开写 */
.warnzone{margin:12px 0 0;border:1px solid #f6cfcf;border-radius:10px;padding:12px 14px;background:#fffafa}
.warnzone-h{font-size:13.5px;font-weight:700;color:#cf1322;margin:0 0 10px;line-height:1.6}
.warnzone-h .warnzone-s{font-size:11.5px;font-weight:400;color:#8a9099;margin-left:6px}
.card.warncard{border-left:4px solid #e54545;background:#fffafa;margin-bottom:12px}
.wtag{display:inline-block;font-size:11px;font-weight:700;padding:1px 8px;border-radius:8px;margin-left:6px;color:#fff;vertical-align:1px}
.wtag.t1{background:#e54545}
.wtag.t2{background:#ff8f1f}
.warnrow{display:flex;gap:10px;font-size:13px;line-height:1.7;margin:5px 0;align-items:flex-start}
.warnk{flex:0 0 62px;color:#8a9099;font-weight:600}
.warnv{flex:1}
.warnconcl{font-size:13px;line-height:1.75;background:#fdecec;border-radius:6px;padding:7px 11px;margin:8px 0}
.warnact{font-size:13px;line-height:1.75;background:#fff7e6;border-radius:6px;padding:7px 11px;margin:0 0 8px}
.warnsrc{font-size:11.5px;color:#8a9099;line-height:1.6}
.warnnote{font-size:11px;color:#a0a6ae;margin-top:6px;line-height:1.6}
.warnbar{margin:12px 0 0;padding:9px 13px;border:1px dashed #f0b4b4;border-radius:8px;background:#fffafa;color:#b02525;font-size:12.5px;line-height:1.6;cursor:pointer;user-select:none}
.warnbar:hover{background:#fff3f3;border-color:#e54545}
.warnbar b{color:#cf1322}
.warnbar .warnbar-c{float:right;color:#cf1322;font-weight:600}
"""


def recent3_block(s):
    """学生卡内的「近 N 天待回应」提醒（v3.9）：联席忘记回复 / 学生问题未解决，重点标记。"""
    rt = s.get("recent3") or {}
    items = rt.get("items") or []
    if not items:
        return ""
    lis = "".join(
        f'<li><span class="rtk {"miss" if it["kind"] == "忘记回复" else "open"}">{it["kind"]}</span>'
        f'<b>{it["at"]}</b> 提问「{it["q"]}…」<br><span class="note">{it["evi"]}</span></li>'
        for it in items)
    return (f'<div class="rtbox"><div class="rt-h">🚨 近 {rt.get("days", 3)} 天待回应 · {len(items)} 项'
            f'（忘记回复 {rt.get("missed_n", 0)} ｜ 问题未解决 {rt.get("open_n", 0)}）</div>'
            f'<ul class="rtlist">{lis}</ul></div>')


def recent3_banner(rows, edu=False):
    """报告顶部「近 N 天待回应」**汇总入口**（v3.9 新增 · v4.0 改为折叠入口）。

    这是给管理者 / 教务看的统一视图：一行入口条 + 点击展开的全量明细表。
    默认收起，避免一进门就被大表格压住；无此类情况时显示绿色「检查通过」。
    """
    items = []
    for r in rows:
        for it in (r["s"].get("recent3") or {}).get("items") or []:
            items.append((0 if it["kind"] == "忘记回复" else 1, it.get("at") or "", r["stu"], it))
    items.sort(key=lambda x: (x[0], x[1]))
    if not items:
        return ('<div class="rtbanner ok"><h2>✅ 近 3 天待回应检查通过</h2>'
                '<div class="rtsub">未发现「联席忘记回复」或「学生问题未解决」的情况。</div></div>')
    n_miss = sum(1 for x in items if x[0] == 0)
    trs = "".join(
        f'<tr><td><b>{stu}</b></td><td>{it["at"]}</td>'
        f'<td><span class="rtk {"miss" if it["kind"] == "忘记回复" else "open"}">{it["kind"]}</span></td>'
        f'<td>{it["q"]}…</td><td class="note">{it["evi"]}</td>'
        f'<td>{"今日内必须回应，先接住情绪再解题" if it["kind"] == "忘记回复" else "跟进到学生回复「懂了」为止，必要时换种讲法"}</td></tr>'
        for _, _, stu, it in items)
    who = "教务视角：以下需提醒联席当日闭环，并向家长同步进展" if edu else "联席视角：以下需当日处理"
    return (f'<details class="rtbanner">'
            f'<summary><span class="rt-t">🚨 近 3 天待回应 {len(items)} 项</span>'
            f'<span class="rt-s">忘记回复 {n_miss} ｜ 问题未解决 {len(items) - n_miss}</span>'
            f'<span class="rt-more">点击查看明细</span></summary>'
            f'<div class="rtbody"><div class="rtsub">{who}；口径：以窗口内最新一条消息为基准往前 3 天，'
            f'学生/家长实质提问后无老师动作 = 忘记回复；老师应允（稍后/晚点…）后无下文或 24 小时内学生再次追问 = 问题未解决。</div>'
            f'<table><tr><th>学生</th><th>日期</th><th>类型</th><th>提问内容</th><th>依据</th><th>建议动作</th></tr>{trs}</table></div></details>')


# ============================================================================
# v4.8 在册全覆盖与失联预警
# 口径（用户 2026-10-15 定稿）：
#   失联         = 无聊天记录 且 无出勤记录
#   出勤但无沟通  = 无聊天记录 且 有出勤记录
#   单向沟通      = 有聊天记录，但学生·家长实质发言 = 0
#   低互动        = 有聊天记录，学生·家长实质发言 1~2
# ⚠️ 群「采到与否」只作 cause 根因标签，**不作分档依据** ——
#    群列表接口有损（实测教务 243 在册 / 群仅 144），拿群判失联会一次性误报 93 人。
#    故一律用「出勤」当「人还在不在」的判据。
# ============================================================================
TIER_CLS = {"失联": "t1", "出勤但无沟通": "t2", "单向沟通": "t3", "低互动": "t4", "正常": "t5"}
TIER_DESC = {
    "失联": "无聊天记录、也无出勤记录 → <b>立即逐一核实</b>是否退课 / 从未建群",
    "出勤但无沟通": "学生在正常上课，但企微沟通通道未打通 → <b>补建 / 核对群，并主动触达家长</b>",
    "单向沟通": "仅有老师单向输出，学生·家长无实质回应 → 换沟通方式，制造回应点",
    "低互动": "学生·家长实质发言 1~2 条 → 关注并适度引导",
    "正常": "沟通与出勤均正常",
}
CAUSE_CN = {
    "group_missing": "企微群未采集到",
    "group_empty": "群已建但零消息",
    "single_way": "仅老师单向输出",
    "ok": "",
}
COV_TIER_ORDER = ["失联", "出勤但无沟通", "单向沟通", "低互动", "正常"]   # 档位表渲染顺序


def coverage_block(cov, roster_n, analyzed_n, chat_days=14, degraded=None):
    """报告顶部的「在册全覆盖与失联预警」（v4.8）。

    这一块回答的是：**在册名单里有多少人根本没进这份报告**，以及他们去哪了。
    报告主体只覆盖「有聊天记录」的学生；本块把余下的人按「出勤 × 聊天」两轴收口，
    避免「没群没聊天」的学生被静默漏掉 —— 而他们恰恰最需要预警。
    """
    if not cov:
        return ""
    tiers = Counter(v.get("tier") for v in cov.values())
    causes = Counter(v.get("cause") for v in cov.values() if v.get("cause") != "ok")
    lost_n = tiers.get("失联", 0)
    anc_n = tiers.get("出勤但无沟通", 0)
    no_chat = lost_n + anc_n
    analyzed_n = analyzed_n or 0
    courses = {v.get("productName") for v in cov.values()}
    multi = len(courses) > 1

    # ---- 头图一句话 ----
    # ⚠️ 两个分母口径不同，必须写清，否则会「两边各算一次」导致数字打架：
    #   预警口径 = 近 chat_days 天（沟通轴）
    #   学生卡口径 = 报告窗口全窗口（analysis_merged）
    recent_n = sum(tiers.get(t, 0) for t in ("单向沟通", "低互动", "正常"))
    head = (f'总人数 <b>{roster_n}</b> 人 ｜ 近 {chat_days} 天有沟通 <b>{recent_n}</b> 人'
            f' ｜ 无沟通 <b>{no_chat}</b> 人')
    if no_chat:
        head += f'，其中 <span class="hl">{anc_n} 人在正常上课</span>、仅企业微信沟通通道未打通'
    if analyzed_n and analyzed_n > recent_n:
        head += (f'<br>学生卡另详析 <b>{analyzed_n}</b> 人（按报告窗口统计）；'
                 f'其中 <b>{analyzed_n - recent_n}</b> 人窗口内有互动、但近 {chat_days} 天已沉默，'
                 f'同样计入上面的「无沟通」，属<b>需重新触达</b>对象。')
    # v4.10.2：本段（在册 / 沟通概览 ＋ 判定口径）默认**折叠收起**（用户要求「这块说明隐藏」）。
    # 档位人数与名单由下方档位表、分档名单承载，数字并未丢失；口径说明保留可展开，
    # 以符合「结论可回查」的既有要求 —— 不删内容，只改默认可见性。
    bar_inner = (f'{head}<br>'
                 f'口径：沟通轴取近 {chat_days} 天，「无聊天记录」= 群未采集到 或 群内近 {chat_days} 天零消息；'
                 f'学习轴取报告窗口全窗口出勤。判定「失联」以<b>出勤</b>为准，<b>不以群是否存在为准</b>'
                 f'（群列表接口有损，实测会漏掉约四成学生）。')
    bar = ('<details class="covlist covhead"><summary>📖 覆盖概览与判定口径 —— 点击展开核对</summary>'
           f'<div class="in"><div class="covbar">{bar_inner}</div></div></details>')

    # ---- 档位总表（v4.10.3：点「人数」直接在本行下方展开该档名单）----
    # 原先名单另设三组折叠块挂在表下方，用户反馈「点击人数，直接展开明细」→ 改为行内展开，
    # 少一次「先找到人数、再滚到下面找同名折叠块」的跳转。
    by_tier = {}
    for v in cov.values():
        by_tier.setdefault(v.get("tier") or "未知", []).append(v)

    def _cov_rows(hits):
        out = []
        for v in hits:
            cz = CAUSE_CN.get(v.get("cause") or "", "")
            chip = f'<span class="covcause">{cz}</span> ' if cz else ""
            out.append(f'<tr><td><b>{v.get("stuName")}</b></td>'
                       f'<td class="covevi">{v.get("productName") or "—"}</td>'
                       f'<td class="covevi">{chip}{v.get("evidence") or ""}</td></tr>')
        return "".join(out)

    trows = []
    for t in COV_TIER_ORDER:
        n = tiers.get(t, 0)
        pct = f"{n / roster_n * 100:.1f}%" if roster_n else "—"
        hits = sorted(by_tier.get(t, []),
                      key=lambda v: (v.get("productName") or "", v.get("stuName") or ""))
        if hits:
            # 人数 = 开关；「失联」默认展开（最紧急），其余默认收起
            dflt = (t == "失联")
            ncell = (f'<span class="covn{" on" if dflt else ""}" data-covn="{t}" '
                     f"onclick=\"covToggle('{t}')\" "
                     f'title="点击{"收起" if dflt else "展开"}本档 {len(hits)} 人名单">{n}'
                     f'<span class="covar"></span></span>')
        else:
            ncell = f'{n}'
        trows.append(
            f'<tr class="covrow"><td><span class="tierk {TIER_CLS.get(t, "t0")}">{t}</span></td>'
            f'<td>{ncell}</td><td>{pct}</td><td>{TIER_DESC.get(t, "")}</td></tr>')
        if hits:
            trows.append(
                f'<tr class="covdet" id="covdet-{t}" '
                f'style="display:{"table-row" if dflt else "none"}">'
                f'<td colspan="4"><div class="covdetin">'
                '<table class="covsub"><tr><th>学生</th><th>课程</th><th>判定依据</th></tr>'
                + _cov_rows(hits) + '</table></div></td></tr>')
    tab = ('<table class="covtab"><tr><th>档位</th><th>人数</th><th>占比</th><th>说明与建议动作</th></tr>'
           + "".join(trows) + '</table>')

    # ---- 多课程时给课程 × 档位 ----
    ctab = ""
    if multi:
        byc = {}
        for v in cov.values():
            byc.setdefault(v.get("productName") or "未知", Counter())[v.get("tier")] += 1
        cr = []
        for c, cc in sorted(byc.items(), key=lambda kv: -sum(kv[1].values())):
            tot = sum(cc.values())
            cr.append(f'<tr><td>{c}</td><td>{tot}</td>'
                      f'<td><b style="color:#cf1322">{cc.get("失联", 0)}</b></td>'
                      f'<td>{cc.get("出勤但无沟通", 0)}</td>'
                      f'<td>{cc.get("单向沟通", 0)}</td><td>{cc.get("低互动", 0)}</td></tr>')
        ctab = ('<details class="covlist" open><summary>按课程拆分（每个课程的总人数与各档人数）</summary>'
                '<div class="in"><table><tr><th>课程</th><th>总人数</th><th>失联</th>'
                '<th>出勤但无沟通</th><th>单向沟通</th><th>低互动</th></tr>'
                + "".join(cr) + '</table></div></details>')

    # v4.10.3：原「分档名单」三组折叠块（失联 / 出勤但无沟通 / 单向+低互动）已删除 ——
    # 名单改为在档位表内**点人数直接展开**（见上 trows 的 covrow + covdet）。
    # 本处只保留根因分布与降级声明。
    lists = ""

    # ---- 根因 & 降级声明 ----
    if causes:
        lists += ('<div class="covdeg">根因分布：'
                  + " ｜ ".join(f"{CAUSE_CN.get(k, k)} {n}" for k, n in causes.most_common())
                  + '</div>')
    if degraded:
        items = "；".join(f'{d.get("field")}（{d.get("reason")}）' for d in degraded)
        lists += f'<div class="covdeg">⚠️ 数据降级声明（缺失即明写，不做静默兜底）：{items}</div>'

    return (f'<div class="covsec" id="covsec"><h2>🚨 总人数覆盖与失联预警</h2>'
            f'{bar}{tab}{ctab}{lists}</div>')


# ══════════════════════════════════════════════════════════════════════════
# v4.9：主讲备课建议 —— 回答「主讲下节课应该重点讲什么 / 什么题型」
# ══════════════════════════════════════════════════════════════════════════
# 只用两类**已经发生的事实**，不生成任何新内容：
#   ① 群聊侧：学生主动提问 / 老师指出问题 命中的知识点（extract.top_topics）
#   ② 小黑板侧：读图实证「有错误轮次所关联的知识点」（boards_summary.error_topics）
#      ＋ 逐题卡点 / 原题（common.fine_from_board 下钻到题型）
# 排序按「覆盖学生数」降序（普遍性优先）；同覆盖时「有小黑板实证错误」优先 ——
# 小黑板是学生真实作答，比「群里提过」硬。
# 🔒 反编造铁律：题型名只能来自 FINE_TYPES 词表命中，或字段原文（形如「课堂作答卡点：…」）；
#    每条必附原文出处；无证据时不硬凑，如实说明。
TOPIC_ALIAS = {"解析几何": "直线和圆", "平面解析几何": "直线和圆"}


def _std_topic(t):
    """读图 / 词表给出的 topic → 统一大类名。

    口径表优先于模型标签（v4.6 原则）：读图模型常把「直线和圆」类题目标成「解析几何」，
    教材章节口径应归「直线和圆」，故先用 TOPIC_ALIAS 硬映射，再走 align_topic 归一。
    「其他」（问卷 / 调查表）直接丢弃 —— 它属出勤参与度，不是知识点薄弱。
    """
    t = (t or "").strip()
    if not t or t == "其他":
        return ""
    return align_topic(TOPIC_ALIAS.get(t, t), list(CHAT_TOPIC_KEYS.keys()))


def _collect_topics(crows, boards_map):
    """聚合「知识点 → 分源覆盖学生集合」。分子分母都限定在本组 crows 内，口径一致。

    返回 {topic: {"chat": set(学生), "board": set(学生), "err": set(学生)}}，
    分别是「群聊提及」「小黑板涉及」「小黑板实证有错」。
    """
    agg = {}
    for r in crows:
        stu = r["stu"]
        for t in (r["s"].get("top_topics") or []):
            k = _std_topic(t)
            if k:
                agg.setdefault(k, {"chat": set(), "board": set(), "err": set()})["chat"].add(stu)
    for r in crows:
        stu = r["stu"]
        b = (boards_map or {}).get(common.norm_name(stu))
        if not b:
            continue
        for t in (b.get("kb_topics") or []):
            k = _std_topic(t)
            if k:
                agg.setdefault(k, {"chat": set(), "board": set(), "err": set()})["board"].add(stu)
        for t in (b.get("error_topics") or []):
            k = _std_topic(t)
            if k:
                agg.setdefault(k, {"chat": set(), "board": set(), "err": set()})["err"].add(stu)
    return agg


def _collect_items(crows, boards_map, max_evi=2):
    """聚合「知识点 → 题型/卡点 → 证据」。聊天侧 fine ＋ 小黑板侧 fine_from_board 合并。

    复用学生卡同一套合并逻辑（与卡片「具体薄弱点」同源），保证两处口径一致、不会互相打架。
    """
    out = {}
    for r in crows:
        stu = r["stu"]
        b = (boards_map or {}).get(common.norm_name(stu))
        merged = common.merge_fine(r["s"].get("fine") or {}, common.fine_from_board(b))
        for topic, rows in (merged or {}).items():
            k = _std_topic(topic)
            if not k:
                continue
            slot = out.setdefault(k, {})
            for it in rows:
                item = (it.get("item") or "").strip()
                if not item:
                    continue
                e = slot.setdefault(item, {"stu": set(), "hard_stu": set(),
                                           "kinds": set(), "evi": [], "cnt": 0})
                e["stu"].add(stu)
                if it.get("kind"):
                    e["kinds"].add(it["kind"])
                # hard：读图判「有错」或存在非「没写完」的实质错误 → 说明是「真的不会」，
                # 而不是「课堂没写完」。v4.4 口径：未完成收尾属节奏问题，不算知识点薄弱。
                if it.get("hard"):
                    e["hard_stu"].add(stu)
                e["cnt"] += int(it.get("count") or 1)
                txt = f'{it.get("at") or ""} 「{(it.get("evi") or "").strip()}」'.strip()
                if it.get("evi") and txt not in e["evi"] and len(e["evi"]) < max_evi:
                    e["evi"].append(txt)
    return out


# ============================================================
# v4.13：Tab② 新增「👀 下节课可重点关注的学生」模块
#
# 主讲课上能做的动作只有两件 —— ① 点名把沉默的人拉进互动 ② 盯课堂练习落实。
# 因此本模块**只收三类已被数据证实的情况**，不新增任何判定逻辑：
#   ① 互动状态（extract 四维之一 interact_lv）—— 与 Tab③「联席跟进 Action」里
#      「课上多给一次点名提问 / 小黑板任务」**同源同口径**，两处不会互相打架；
#   ② 小黑板课堂作答：空白未提交 / 多轮未收尾（读图 status_counts，与 board_focus 同一判定）；
#   ③ 小黑板实证错误（fine_from_board 的 hard —— 「真的做错」，非「没写完」）。
# 每行都附原文依据，可回群 / 回图逐条核对。
# ============================================================
_FOCUS_SEV = {"bad": 0, "warn": 1}
_FOCUS_CLS = {"bad": "bad", "warn": "warn", "blank": "bd", "unfin": "bd", "hard": "bd"}
_FOCUS_ACT = {
    "bad": "主动点名 —— 先抛一个他大概率答得上的问题，把人带进课堂节奏",
    "warn": "多给一次点名提问 / 小黑板任务，把人调动起来",
}
# 入榜门槛（v4.13）：**只收「课上互动不足」的学生** —— 主讲一节课能额外点的就几个人，
# 门槛必须聚焦，否则人人上榜等于没筛（实测本课 48 人里 26 人群内互动不足，宽门槛会命中大半）。
#   · bad  = 沉默不回复 / 近期沉默（窗口内本人实质发言 0 条，或已沉默 ≥14 天）
#   · warn = 不积极（本人实质发言 ≤3 条）；「一般」（4~11 条）属正常参与，已排除。
# 小黑板的「未提交 / 未收尾 / 实证错误」**不作为入榜条件**（那属于「该知识点讲什么」，
# 由上方优先级表承载），只作为**附加理由与排序权重** —— 互动差且课上也没落实的排最前。
_FOCUS_WARN_OK = {"不积极"}


def focus_students(crows, boards_map, limit=6):
    """筛出「下节课课上值得重点关注」的学生（v4.13）。

    返回 (shown, total, summary_text)：shown = 前 limit 人，total = 互动不足总人数，
    summary = 一句话分布小结。无符合者返回 ([], 0, "")。

    排序（互动差 + 课堂也没落实 = 最该盯）：
      ① bad 优先于 warn；
      ② 有课堂作答问题（未提交／未收尾／实证错误）的优先；
      ③ 本人最新发言越早（沉默越久）越靠前，再比实质发言条数。
    """
    out = []
    for r in crows:
        s = r["s"]
        bd = (boards_map or {}).get(common.norm_name(r["stu"]))
        # ① 入榜门槛：互动状态（extract 四维）
        ilv = s.get("interact_lv")
        iact = (s.get("interact") or "").strip() or "不足"
        if not (ilv == "bad" or (ilv == "warn" and iact in _FOCUS_WARN_OK)):
            continue
        kinds, why, evi = [ilv], [], []
        why.append(f"互动「{iact}」")
        if s.get("interact_evi"):
            evi.append(s["interact_evi"])
        # ② 小黑板课堂作答：附加理由（不作门槛），用于排前与说明
        if bd:
            sc = bd.get("status_counts") or {}
            rounds = bd.get("rounds") or len(bd.get("details") or [])
            pb = sc.get("部分作答", 0)
            blank = sc.get("空白未提交", 0) + sc.get("空白", 0)
            if blank >= 1 and blank >= pb:
                kinds.append("blank")
                why.append(f"小黑板 {blank} 轮未提交")
                evi.append(f"小黑板读图：{blank} 轮空白未提交（共 {rounds} 轮）")
            elif pb >= 2 and pb >= max(2, rounds * 0.4):
                kinds.append("unfin")
                why.append(f"课堂作答 {pb}/{rounds} 轮未收尾")
                evi.append(f"小黑板读图：{pb} 轮部分作答（共 {rounds} 轮）")
            hard = [(t, it) for t, its in (common.fine_from_board(bd) or {}).items()
                    for it in its if it.get("hard")]
            if hard:
                kinds.append("hard")
                why.append("、".join(sorted({t for t, _ in hard})[:2]) + " 实证错误")
                evi.append("小黑板实证错误：" + "、".join(
                    f'{it.get("item")}（{it.get("at") or "—"}）' for _, it in hard[:2]))
        out.append(dict(
            stu=r["stu"], co=r.get("co") or "未分配联席",
            sev=_FOCUS_SEV[ilv], lead=ilv, kinds=kinds, why=why, evi=evi,
            has_board=bool({"blank", "unfin", "hard"} & set(kinds)),
            board_lead=next((k for k in ("blank", "unfin", "hard") if k in kinds), "hard"),
            # 「沉默不回复」（窗口内 0 条实质发言）比「近期沉默」（只是最近没说话）更值得先点
            sub=(0 if iact == "沉默不回复" else 1),
            newest=(s.get("newest") or ""), strength=(s.get("n_stu_sub") or 0),
        ))
    if not out:
        return [], 0, ""
    out.sort(key=lambda x: (x["sev"], 0 if x["has_board"] else 1, x["sub"],
                            x["newest"], x["strength"], x["stu"]))
    n_bad = sum(1 for x in out if x["sev"] == 0)
    n_warn = len(out) - n_bad
    n_board = sum(1 for x in out if x["has_board"])
    summary = (f'全组 <b>{len(crows)}</b> 人中 <b>{len(out)}</b> 人群内互动不足'
               f'（沉默不回复／近期沉默 {n_bad} · 实质发言 ≤3 条的 {n_warn}），'
               f'其中 <b>{n_board}</b> 人课堂作答也有问题（未提交／未收尾／实证错误）——'
               f'这些人既没在群里出现、课上也没落实，最该被主动点到。')
    return out[:limit], len(out), summary


def _focus_html(crows, boards_map, limit=6):
    """渲染「👀 下节课可重点关注的学生」块（Tab② 内嵌）。无数据返回空串。"""
    shown, total, summary = focus_students(crows, boards_map, limit=limit)
    if not shown:
        return ""
    more = total - len(shown)
    _bact = {"blank": "当场确认小黑板是漏做还是漏交，留一步让他补",
             "unfin": "讲这道题时盯他写完最后一步",
             "hard": "讲该题型时点他确认是否真的会"}

    def _row(x):
        tags = "".join(
            f'<span class="foc-tag {_FOCUS_CLS[k]}">{w}</span>'
            for k, w in zip(x["kinds"], x["why"]))
        act = _FOCUS_ACT[x["lead"]]
        if x["has_board"]:
            act += "；" + _bact[x["board_lead"]]
        ev = " ｜ ".join(e for e in x["evi"] if e)
        return (f'<tr><td class="fst">{x["stu"]}</td>'
                f'<td class="fw">{tags}</td>'
                f'<td class="fact">{act}</td>'
                f'<td class="fev">{ev or "—"}</td></tr>')

    body = "".join(_row(x) for x in shown)
    more_html = ""
    if more > 0:
        rest, _, _ = focus_students(crows, boards_map, limit=10 ** 6)
        more_html = (f'<details class="foc-more"><summary>展开其余 {more} 人'
                     f'（同一口径排序，共 {total} 人）</summary>'
                     f'<table class="foc-tab"><tr><th>学生</th><th>关注理由</th>'
                     f'<th>课上可以做的事</th><th>依据（可核对）</th></tr>'
                     + "".join(_row(x) for x in rest[len(shown):]) + '</table></details>')
    return (
        '<div class="tadv-focus">'
        '<div class="fadv-h">👀 下节课可重点关注的学生</div>'
        f'<div class="fadv-s">{summary}'
        f'下表列最该先动的 {len(shown)} 人'
        + (f'，其余 {more} 人见折叠。' if more > 0 else '。') + '</div>'
        '<table class="foc-tab"><tr><th>学生</th><th>关注理由</th>'
        '<th>课上可以做的事</th><th>依据（可核对）</th></tr>'
        + body + '</table>'
        + more_html
        + '<div class="fadv-note">名单只复述已有证据（群聊互动状态 ／ 小黑板逐轮读图），'
          '不评价学习态度、不预测成绩。「互动不足」＝窗口内本人实质发言 ≤3 条或已沉默，'
          '不等于学生不用功（「一般」＝实质发言 4~11 条，属正常参与，已排除在名单外）。'
          '排序＝先互动、后练习：互动沉默且课堂作答也有问题的排最前。'
          '互动状态与 Tab③「联席跟进 Action」同源，两处口径一致。</div>'
        '</div>')


def teaching_advice(crows, boards_map, n_any=None):
    """渲染「🎓 主讲备课建议」区块。无任何证据时返回空串（不渲染空壳）。"""
    n_any = n_any if n_any is not None else len(crows)
    if not n_any:
        return ""
    agg = _collect_topics(crows, boards_map)
    items = _collect_items(crows, boards_map)
    if not agg:
        return ""
    n_bd = sum(1 for r in crows if (boards_map or {}).get(common.norm_name(r["stu"])))
    rank = []
    for t, d in agg.items():
        stu = d["chat"] | d["board"]
        un = len(stu)
        # 只留「有普遍性」的：≥2 人，或虽只 1 人但占比已达 1/3（小组课也该被看到）
        if un < 2 and un < n_any * 0.30:
            continue
        rank.append({
            "topic": t, "un": un, "chat": len(d["chat"]),
            "board": len(d["board"]), "err": len(d["err"]),
            # 题型排序：先「有实证错误的人数」降序（真的不会 > 只是没写完），再覆盖人数降序
            "items": sorted(items.get(t, {}).items(),
                            key=lambda kv: (-len(kv[1]["hard_stu"]), -len(kv[1]["stu"]),
                                            -kv[1]["cnt"], kv[0])),
        })
    if not rank:
        return ""
    rank.sort(key=lambda x: (-x["un"], -x["err"], -x["chat"], x["topic"]))
    top = rank[0]
    pct = round(100 * top["un"] / n_any)

    # ---- 一句话结论 ----
    ev = []
    if top["err"]:
        ev.append(f'其中 <span class="hit">{top["err"]} 人</span>小黑板作答有实证错误')
    elif top["board"]:
        ev.append(f'其中 {top["board"]} 人小黑板涉及该知识点的作答')
    _hard_items = [(i, v) for i, v in top["items"] if v["hard_stu"]][:2]
    if _hard_items:
        ev.append('最集中的实证错误题型是 '
                  + "、".join(f'「{i}」（{len(v["hard_stu"])} 人）' for i, v in _hard_items))
    elif top["items"]:
        ev.append('主要集中在 ' + "、".join(f'「{i}」' for i, _ in top["items"][:2]))
    tail = ("，" + "；".join(ev)) if ev else ""
    if pct >= 60:
        line = (f'下节课建议 <b>优先处理「{top["topic"]}」</b> —— '
                f'本组 {top["un"]}/{n_any} 人（<span class="hit">{pct}%</span>）涉及{tail}。')
    elif pct >= 35:
        line = (f'建议重点讲 <b>「{top["topic"]}」</b> —— {top["un"]} 人（{pct}%）涉及{tail}；'
                f'其余按优先级表依次安排。')
    else:
        line = (f'本组暂无压倒性共性问题，按优先级表顺序处理即可，首条为 '
                f'<b>「{top["topic"]}」</b>（{top["un"]} 人，{pct}%）{tail}；'
                f'建议结合课堂实际进度取舍。')
    if len(rank) > 1 and rank[1]["un"] >= max(3, top["un"] * 0.7):
        _s = rank[1]
        line += (f' 可同步安排 <b>「{_s["topic"]}」</b>'
                 f'（{_s["un"]} 人，{round(100 * _s["un"] / n_any)}%）。')

    # ---- 优先级表（v4.10.1：显示全部知识点，长尾不再截断 —— 上方的「高频知识点」明细表
    # 已删除，这里成为唯一承载知识点清单的地方，故必须完整）----
    rows = ""
    for i, x in enumerate(rank, 1):
        its = "、".join(
            f'{k} <span class="mute">{len(v["stu"])}人</span>'
            + (f'<b class="hard">实证错误 {len(v["hard_stu"])}</b>' if v["hard_stu"] else '')
            for k, v in x["items"][:2]) or "—"
        bd = (f'{x["err"]}' if x["err"] else "—") + (f' <span class="mute">/涉及 {x["board"]}</span>' if x["board"] else "")
        rows += (f'<tr><td class="rk">{i}</td><td class="nm">{x["topic"]}</td>'
                 f'<td class="n"><b>{x["un"]}</b>/{n_any} · {round(100 * x["un"] / n_any)}%</td>'
                 f'<td class="n">{x["chat"] or "—"}</td>'
                 f'<td class="n">{bd}</td>'
                 f'<td class="itm">{its}</td></tr>')

    # ---- 可核对的证据（折叠）----
    ev_html = ""
    kind_cn = {"小黑板": "bd", "老师指出": "tea", "学生提问": "stu"}
    for x in rank:
        lis = ""
        for item, e in x["items"][:6]:
            ks = "／".join(sorted(e["kinds"]))
            cls = kind_cn.get(ks, "stu")
            evi = " ".join(e["evi"])
            hard = (f'<b class="hard">实证错误 {len(e["hard_stu"])} 人</b>' if e["hard_stu"]
                    else '<span class="mute">仅未完成／卡点</span>')
            lis += (f'<li><span class="fitm">{item}</span>'
                    f'<span class="fsrc {cls}">{ks}</span>{hard}'
                    f'<span class="fcnt">×{e["cnt"]} / {len(e["stu"])}人</span>'
                    + (f'<span class="fevi">{evi}</span>' if evi else '') + '</li>')
        ev_html += f'<div class="fine-t">{x["topic"]}</div><ul class="fine-l">{lis}</ul>'

    return (
        '<div class="tadv">'
        '<div class="tadv-h">🎓 主讲备课建议 · 下节课重点讲什么</div>'
        f'<div class="tadv-sum">{line}</div>'
        '<table class="tadv-tab"><tr><th>优先</th><th>建议重点讲</th><th>覆盖学生</th>'
        '<th>群聊提及</th><th>小黑板实证</th><th>典型题型 / 卡点</th></tr>'
        + rows + '</table>'
        + f'<div class="tadv-cap">「覆盖学生」= 群聊提及 ∪ 小黑板涉及，分母为本组 {n_any} 人；'
          f'「小黑板实证」= 该知识点上读图判定有错误／卡点的学生数（本组小黑板读图覆盖 {n_bd} 人，'
          f'未被素材覆盖的学生不在分母内）。'
        + ('<b>本视角未接入小黑板读图数据，「实证错误」维度为空，请以群聊高频为准。</b>'
           if n_bd == 0 else '')
        + '</div>'
        + _focus_html(crows, boards_map)
        + (f'<details class="tadv-ev"><summary>📎 展开题型与原文出处'
           f'（{len(rank)} 个知识点 · 可回群 / 回图逐条核对）</summary>'
           f'<div class="in">{ev_html}</div></details>' if ev_html else '')
        + '<div class="tadv-note">排序：按「覆盖人数」降序；同覆盖时「有小黑板实证错误」的优先。'
          '题型旁标 <b class="hard">实证错误 N</b> 的，是读图判定<b>确实做错</b>的学生数（真的不会）；'
          '标「仅未完成／卡点」的，是<b>只是没写完</b>（课堂节奏 / 书写速度，非能力问题）——'
          '两者已按 v4.4 口径区分，不要混为一谈。文案只复述已发生的证据'
          '（群聊提问 / 老师指出 / 小黑板作答），不预测考情、不生成内容。</div>'
        '</div>')


# 注（v4.10.1）：原 `_topic_table_html()` 已删除 —— 它渲染的「高频知识点 · 数据明细」表
# 与「主讲备课建议」表的列（覆盖学生 / 群聊提及 / 小黑板实证）完全重合，属同源重复。
# 高频知识点信息由备课建议表直接承载（且已改为显示全部知识点，长尾不丢）。


def _cofilter_html(sec_id, crows, show_filter, co_opts):
    """「筛选联席」下拉（教务·按课程维度 / 课程维度用）。"""
    if not (show_filter and co_opts):
        return ""
    opts = "".join(f'<option value="{co}">{co}（{cnt}）</option>' for co, cnt in co_opts.most_common())
    return (f'<div class="cofilterwrap">筛选联席：'
            f'<select class="cofilter" onchange="coFilter(this)" data-target="{sec_id}">'
            f'<option value="__all__">全部联席（{len(crows)}）</option>{opts}</select>'
            f'<span class="cnt">该课程下共 {len(co_opts)} 名联席</span></div>')


def build_topic_block(sec_id, crows, title, is_all, boards_map=None):
    """Tab2 的一个分组：**主讲备课建议**（下节课重点讲什么）。

    v4.10.1：原先建议下方还挂了一张「高频知识点 · 数据明细」表，但它的列
    （知识点 / 群聊提及 / 小黑板实证 / 合计涉及 / 占比）与建议表的
    「建议重点讲 / 覆盖学生 / 群聊提及 / 小黑板实证」**完全重合**（用户指出「这两部分重复」），
    故整表删除，只保留备课建议；建议表本身已改为**显示全部知识点**，长尾（数列、解三角形等）不丢。
    """
    if not crows:
        return ""
    disp = 'style="display:block"' if is_all else 'style="display:none"'
    adv = teaching_advice(crows, boards_map)
    if not adv:
        adv = ('<div class="tadv"><div class="tadv-h">🎓 主讲备课建议 · 下节课重点讲什么</div>'
               f'<div class="tadv-sum">本组（{title}）暂无可用于备课建议的证据 —— '
               '聊天与小黑板均未捕捉到明确知识点（多为暂无群聊记录或未提供小黑板读图素材）。</div></div>')
    return f'''
<div class="grp" id="t-{sec_id}" {disp}>
  {adv}
</div>'''


def build_card_block(sec_id, crows, title, show_filter, co_opts, is_all,
                     edu=False, show_co_tag=False, boards_map=None,
                     warn_items=None, chat_days=14):
    """Tab3 的一个分组：KPI 筛选卡 ＋ 预警卡 ＋ 逐学生卡片。

    show_filter=True 时渲染「筛选联席」下拉（教务·按课程维度 / 课程维度用）。
    edu=True（教务视角）：学生卡第④块为「教务 To-Do」；否则为「联席跟进 Action」。
    show_co_tag=True（课程维度）：学生卡标注所属联席，便于一门课跨多个联席时区分归属。
    warn_items（v4.12）：该分组下「在册但无任何聊天记录」的学生（失联 / 出勤但无沟通），
        渲染为左红边**预警卡** —— 他们进不了常规学生卡（没有聊天数据），却是最该被看见的一批人。
    """
    warn_items = warn_items or []
    if not crows and not warn_items:
        return ""
    _warn_n = len(warn_items)
    c_slow = sum(1 for r in crows if r["s"].get("resp_slow"))
    c_unres = sum(1 for r in crows if r["s"]["unresolved"])
    c_absent = sum(1 for r in crows if r["att"] and r["att"]["absentCount"] > 0)
    cofilter = _cofilter_html(sec_id, crows, show_filter, co_opts)
    cards = "".join(card_html(r, edu=edu, show_co_tag=show_co_tag,
                              board=(boards_map or {}).get(common.norm_name(r["stu"]))) for r in crows)
    todo_head = "教务 To-Do" if edu else "联席 To-Do"

    # v4.12 新增 / v4.14 移位：预警卡区（无常规学生卡的在册学生）
    #   —— 常规学生卡才是本组的「主要信息」，预警卡默认**收起**并放到常规卡**下方**，
    #      避免一进 Tab③ 就被红卡占满首屏；展开入口 = 底部入口条 或 上方 KPI「未触达预警」。
    warns = "".join(warn_card_html(v, chat_days=chat_days, show_co_tag=show_co_tag)
                    for v in warn_items)
    warnzone = ""
    warnbar = ""
    if warns:
        warnzone = (f'<div class="warnzone" id="warnzone-{sec_id}" style="display:none">'
                    f'<div class="warnzone-h">⚠️ 未进入学情分析的在册学生 · 预警卡（{_warn_n} 人）'
                    f'<span class="warnzone-s">这些学生没有任何聊天记录，故无常规学生卡；'
                    f'本区把「哪一轴缺、哪一轴正常」摊开写清，并给出可直接执行的动作</span></div>'
                    f'{warns}</div>')
        warnbar = (f'<div class="warnbar" id="warnbar-{sec_id}" onclick="warnToggle(\'{sec_id}\')" '
                   f'title="展开查看预警卡（在册、但无任何聊天记录的学生）">'
                   f'⚠️ 另有 <b>{_warn_n}</b> 名在册学生未进入学情分析（无聊天记录）'
                   f'<span class="warnbar-c">点击展开预警卡 ▾</span></div>')

    # v4.3：KPI 小卡 = 可点击筛选入口（点一下只留这一类学生，再点恢复全部）。
    # 「可分析消息」已按用户要求下线（它对跟进动作没有指导意义，且与卡片内的「可分析 N 条」重复）。
    def ktile(n, label, key, cls="", hint=""):
        return (f'<div class="kpi kbl {cls}" data-k="{key}" title="{hint}" '
                f'onclick="kpiFilter(this,\'{sec_id}\',\'{key}\')">'
                f'<div class="n">{n}</div><div class="l">{label}</div></div>')

    if edu:
        # 教务：出勤为核心关切 → 末位放「有缺勤」
        kpi_extra = ktile(c_absent, "有缺勤(后台)", "absent", "orange", "后台课节考勤有缺勤记录")
    else:
        # 联席 / 课程：为学习效果负责 → 末位放「互动不足」「催交未交」「批改未改」
        c_weak_inter = sum(1 for r in crows if r["s"].get("interact_lv") in ("bad", "warn"))
        c_urge_fail = sum(1 for r in crows if r["s"].get("urge_n", 0) > r["s"].get("urge_ok", 0))
        c_fix_fail = sum(1 for r in crows if r["s"].get("fix_n", 0) > r["s"].get("fix_ok", 0))
        kpi_extra = (ktile(c_weak_inter, "互动不足", "inter", "orange", "互动状态判定为「不积极 / 沉默」")
                     + ktile(c_urge_fail, "催交未交", "urge", "orange", "老师在群里催交过、其后未见学生「已交」回应")
                     + ktile(c_fix_fail, "批改未改", "fix", "orange", "老师要求订正过、其后未见学生「已改」回应"))
    # v4.12：预警入口 —— 只筛出预警卡（这类学生没有常规学生卡，用独立 KPI 才能一键定位）
    warn_tile = (ktile(_warn_n, "未触达预警", "warn", "red",
                       "无任何聊天记录的在册学生（失联 / 出勤但无沟通），点击只看这部分预警卡")
                 if warn_items else "")
    disp = 'style="display:block"' if is_all else 'style="display:none"'
    # 常规学生卡区：本组没有常规学生卡时（整组都是在册但无聊天记录的学生）不渲染空标题
    cards_sec = (f'<div class="section"><h3>逐学生表现 &amp; {todo_head}'
                 f'（需关注置顶）</h3>{cards}</div>') if crows else ''
    return f'''
<div class="grp" id="{sec_id}" {disp}>
  {cofilter}
  <div class="ckpis">
    {ktile(len(crows), "学生数", "__all__", "", "点击恢复显示本组全部常规学生卡")}
    {warn_tile}
    {ktile(c_slow, "响应迟缓群", "slow", "red", "学生提问后 ≥24 小时才见老师回复")}
    {ktile(c_unres, "未解疑问群", "unres", "orange", "会话尾部仍有未获回应的提问")}
    {kpi_extra}
  </div>
  <div class="kpihint">💡 点击上方任一统计卡，可只看这一类学生；再次点击（或点「学生数」）恢复全部。<span class="kpicnt" id="kpiCnt-{sec_id}"></span></div>
  {cards_sec}
  {warnbar}
  {warnzone}
</div>'''


def _tabs_html(panel_id, prefix, n_all, groups):
    """分组切换 Tab。groups = [(标题, 人数, 迟缓数)]；点击调用 showTabIn(面板id, 前缀, 序号)。
    只有一个分组（无下钻）时返回空串，由调用方省略整条 Tab 栏。"""
    if not groups:
        return ""
    out = [f'<button class="tab active" onclick="showTabIn(\'{panel_id}\',\'{prefix}\',-1)">'
           f'📊 全部（{n_all}）</button>']
    for i, (title, n, t) in enumerate(groups):
        cnt = f'{n}人' + (f' · {t}迟缓' if t else '')
        out.append(f'<button class="tab" onclick="showTabIn(\'{panel_id}\',\'{prefix}\',{i})" '
                   f'title="{title}"><span class="tname">{title}</span>'
                   f'<span class="tcount">{cnt}</span></button>')
    return "".join(out)


def _dim_bar(where):
    """教务视角的双维度切换条（Tab2 / Tab3 各自一套，互不干扰）。"""
    return f'''<div class="dimswitch">
  <span class="dimlabel">查看维度：</span>
  <button class="dimb active" data-w="{where}" data-d="course" onclick="showDim('{where}','course')">📚 按课程维度</button>
  <button class="dimb" data-w="{where}" data-d="co" onclick="showDim('{where}','co')">👥 按联席维度</button>
</div>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", default=None, help="联席姓名（联席视角 / 课程内子筛选）")
    ap.add_argument("--edu", default=None, help="教务姓名（教务视角）")
    ap.add_argument("--course", default=None, help="课程名（课程维度），如 高二数学清北层日E邵")
    ap.add_argument("--window", default=WINDOW_DEFAULT)
    # 可选：在报告顶部打一条醒目横幅说明数据来源（如用合成/演示数据试跑时，
    # 避免看报告的人拿演示话术去真实群里核对而找不到）。
    ap.add_argument("--note", default=None, help="顶部数据来源/口径提示横幅（可选）")
    ap.add_argument("--chat-days", type=int, default=14, help="失联预警沟通轴窗口天数（默认近两周）")
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

    # v4.8 在册全覆盖（coverage.json 由 adapt_legacy.py + coverage.py 产出；缺失则不渲染该块，向后兼容）
    cov_path = os.path.join(wd, "coverage.json")
    cov = json.load(open(cov_path, encoding="utf-8")) if os.path.exists(cov_path) else {}
    cov_meta = {}
    _mp = os.path.join(wd, "snapshot", "meta.json")
    if os.path.exists(_mp):
        try:
            cov_meta = json.load(open(_mp, encoding="utf-8"))
        except Exception:
            cov_meta = {}

    rows = []
    for stu, s in A.items():
        g = s["group"]
        # 「是否需回复」「响应时效」均不再依赖后台列（isReplied / isTimeout 实测与聊天事实不符），
        # 统一改用聊天记录分析结论：unresolved=会话尾部仍有未获回应提问；resp_slow=提问后 ≥24h 才见回复
        unreply = bool(s.get("unresolved"))
        slow = bool(s.get("resp_slow"))
        # v4.7：课程名优先取后台 productName（响应时效列表新增字段），groupName 仅回退
        course, course_raw = course_of(g.get("groupName", ""), g.get("productName"))
        co = g.get("coTeacherName") or "未分配联席"
        score = (1 if slow else 0, 1 if s["unresolved"] else 0, s["n_text"])
        rows.append(dict(score=score, stu=stu, s=s, g=g, slow=slow,
                         unreply=unreply, course=course, course_raw=course_raw,
                         co=co, att=s.get("att")))
    rows.sort(key=lambda x: (-x["score"][0], -x["score"][1], -x["score"][2]))

    # ---------- v4.12：预警卡对象 ----------
    # 常规学生卡来自 analysis_merged（**只有有聊天记录的学生**才在里面），
    # 因此「在册但一条聊天记录都没有」的学生（失联 / 出勤但无沟通）在 Tab③ 里是**完全缺席**的 ——
    # 而他们恰恰是最该被看见的一批人。这里把他们从 coverage 里捞出来，渲染为左红边预警卡。
    _body_norms = {common.norm_name(k) for k in A}
    warn_all = []
    for _v in (cov or {}).values():
        _nm = _v.get("stuNameNorm") or common.norm_name(_v.get("stuName") or "")
        if _nm and _nm not in _body_norms:
            _c, _craw = course_of("", _v.get("productName"))
            warn_all.append(dict(_v, _course=_c, _course_raw=_craw))
    warn_all.sort(key=lambda v: ({"失联": 0, "出勤但无沟通": 1}.get(v.get("tier"), 9),
                                 v.get("_course") or "", v.get("stuName") or ""))
    warn_by_course, warn_by_co = {}, {}
    for _v in warn_all:
        warn_by_course.setdefault(_v.get("_course"), []).append(_v)
        warn_by_co.setdefault(_v.get("coTeacherName") or "未分配联席", []).append(_v)

    # 动态学科：从纳入的群中提取（角色只带某科时即显示该科，避免写死「高二数学」）
    _subj = []
    for r in rows:
        _g = r["g"]
        sj = (_g.get("subjectName")
              or common.subject_of(common.course_name(_g.get("productName"), _g.get("groupName")))
              or "").strip()
        if sj and sj not in _subj:
            _subj.append(sj)
    subject_line = " / ".join(_subj) or "—"

    # v4.3：「可分析消息（近窗）」KPI 已按用户要求下线 —— 对跟进动作无指导意义，
    # 且每张卡片 meta 行里已有「可分析 N 条」，属同源重复。
    slow_n = sum(1 for r in rows if r["slow"])
    unres_n = sum(1 for r in rows if r["s"]["unresolved"])
    # 近 3 天待回应（v3.9）：忘记回复 / 问题未解决的全局提醒（顶部横幅 + KPI）
    rt_stu = [r for r in rows if (r["s"].get("recent3") or {}).get("items")]
    rt_n_stu = len(rt_stu)
    rt_n_items = sum(len(r["s"]["recent3"]["items"]) for r in rt_stu)
    rt_banner = recent3_banner(rows, edu=is_edu)
    # v4.10：原「三、整体画像：高频知识点（全量）」章节已取消 —— 它与 Tab2 里「全部」分组的
    # 备课建议 + 高频知识点表完全重合（同一批人、同一套口径），重构为三 Tab 后不再重复渲染。

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

        # v4.10：Tab2（数据明细）与 Tab3（学生卡）**各生成一套分组** —— 两个 Tab 各有独立的
        # 分组 Tab 与外层维度切换，互不干扰（id 前缀：Tab2 = t-c / t-co，Tab3 = c / co）。
        t2_c = [build_topic_block("c-all", rows, "全部课程", True, boards_map=boards_map)]
        t3_c = [build_card_block("c-all", rows, "全部课程", True, co_all, True, True,
                                 boards_map=boards_map, warn_items=warn_all,
                                 chat_days=args.chat_days)]
        for ci, ck in enumerate(course_order):
            cr = by_course[ck]
            t2_c.append(build_topic_block(f"c-{ci}", cr, ck, False, boards_map=boards_map))
            t3_c.append(build_card_block(f"c-{ci}", cr, ck, True,
                                         Counter(r["co"] for r in cr), False, True,
                                         boards_map=boards_map,
                                         warn_items=warn_by_course.get(ck, []),
                                         chat_days=args.chat_days))
        t2_co = [build_topic_block("co-all", rows, "全部联席", True, boards_map=boards_map)]
        t3_co = [build_card_block("co-all", rows, "全部联席", False, None, True, True,
                                  boards_map=boards_map, warn_items=warn_all,
                                  chat_days=args.chat_days)]
        for ci, ck in enumerate(co_order):
            cr = by_co[ck]
            t2_co.append(build_topic_block(f"co-{ci}", cr, ck, False, boards_map=boards_map))
            t3_co.append(build_card_block(f"co-{ci}", cr, ck, False, None, False, True,
                                          boards_map=boards_map,
                                          warn_items=warn_by_co.get(ck, []),
                                          chat_days=args.chat_days))

        _cg = [(ck, len(by_course[ck]), sum(1 for r in by_course[ck] if r["slow"])) for ck in course_order]
        _og = [(ck, len(by_co[ck]), sum(1 for r in by_co[ck] if r["slow"])) for ck in co_order]

        topic_panels = f'''{_dim_bar('t2')}
<div class="dim-panel" id="t2-course">
  <div class="tabs">{_tabs_html('t2-course', 't-c', len(rows), _cg)}</div>
  {''.join(t2_c)}
</div>
<div class="dim-panel" id="t2-co" style="display:none">
  <div class="tabs">{_tabs_html('t2-co', 't-co', len(rows), _og)}</div>
  {''.join(t2_co)}
</div>'''
        card_panels = f'''{_dim_bar('t3')}
<div class="dim-panel" id="t3-course">
  <div class="tabs">{_tabs_html('t3-course', 'c', len(rows), _cg)}</div>
  {''.join(t3_c)}
</div>
<div class="dim-panel" id="t3-co" style="display:none">
  <div class="tabs">{_tabs_html('t3-co', 'co', len(rows), _og)}</div>
  {''.join(t3_co)}
</div>'''
        # 注：原 edu_note 顶部提示文案已由三 Tab 结构 + 维度切换按钮自解释，不再单独渲染。
    elif is_course:
        # ===== 课程维度：单一课程，按「所属联席」下钻 =====
        co_all = Counter(r["co"] for r in rows)
        topic_panels = f'''<div class="dim-panel" id="t2-course">
  {build_topic_block("c-all", rows, name, True, boards_map=boards_map)}
</div>'''
        card_panels = f'''<div class="dim-panel" id="t3-course">
  {build_card_block("c-all", rows, name, True, co_all, True, False, show_co_tag=True,
                    boards_map=boards_map, warn_items=warn_all, chat_days=args.chat_days)}
</div>'''
        # 注：课程维度说明同上，不再单独渲染。
    else:
        # ===== 联席视角（仅按课程） =====
        course_order = [k for k, _ in Counter(r["course"] for r in rows).most_common()]
        t2 = [build_topic_block("c-all", rows, "全部课程", True, boards_map=boards_map)]
        t3 = [build_card_block("c-all", rows, "全部课程", False, None, True, False,
                               boards_map=boards_map, warn_items=warn_all,
                               chat_days=args.chat_days)]
        _cg = []
        for ci, ck in enumerate(course_order):
            cr = [r for r in rows if r["course"] == ck]
            t2.append(build_topic_block(f"c-{ci}", cr, ck, False, boards_map=boards_map))
            t3.append(build_card_block(f"c-{ci}", cr, ck, False, None, False, False,
                                       boards_map=boards_map,
                                       warn_items=warn_by_course.get(ck, []),
                                       chat_days=args.chat_days))
            _cg.append((ck, len(cr), sum(1 for r in cr if r["slow"])))
        topic_panels = f'''<div class="dim-panel" id="t2-course">
  <div class="tabs">{_tabs_html('t2-course', 't-c', len(rows), _cg)}</div>
  {''.join(t2)}
</div>'''
        card_panels = f'''<div class="dim-panel" id="t3-course">
  <div class="tabs">{_tabs_html('t3-course', 'c', len(rows), _cg)}</div>
  {''.join(t3)}
</div>'''
        # 联席视角单维度，无需维度说明。

    # v4.8：在册全覆盖块（分母用 coverage 的在册人数，不再用「有聊天记录的人数」冒充全量）
    roster_n = len(cov) or len(rows)
    analyzed_n = len(rows)
    _tiers = Counter(v.get("tier") for v in cov.values())
    lost_n, anc_n = _tiers.get("失联", 0), _tiers.get("出勤但无沟通", 0)
    cov_block = coverage_block(cov, roster_n, analyzed_n, args.chat_days, cov_meta.get("degraded"))
    kpi_cov = ""
    _cov_badge = ""
    if cov:
        # v4.10：顶部 KPI 卡全部可点击 —— 失联 / 出勤但无沟通 → 切到「失联预警」Tab 并展开对应档位
        kpi_cov = (f'<div class="kpi {"red" if lost_n else "green"} clickable" onclick="topKpi(\'lost\')" '
                   f'title="点击查看失联名单"><div class="n">{lost_n}</div>'
                   f'<div class="l">失联（无出勤·无沟通）</div></div>'
                   f'<div class="kpi orange clickable" onclick="topKpi(\'anc\')" '
                   f'title="点击查看「出勤但无沟通」名单"><div class="n">{anc_n}</div>'
                   f'<div class="l">出勤但无沟通</div></div>')
        _warn_n = lost_n + anc_n
        _cov_badge = f'<span class="mcnt {"warn" if _warn_n else "ok"}">{_warn_n}</span>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}（{role_cn}）学生学情分析</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<h1>{name}（{role_cn}）· 学生学情分析{ '（双维度：按课程 / 按联席）' if is_edu else ('（课程维度 · 按联席下钻）' if is_course else '（联席维度 · 按课程下钻）') }</h1>
<div class="sub">分析窗口：聊天内容 {args.window} 至今 ｜ 出勤：学生管理后台「课节考勤」权威数据 ｜ 学科：{subject_line} ｜ 角色：{role_cn} ｜ 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
{ f'<div class="datanote">⚠️ {args.note}</div>' if args.note else '' }
<div class="kpis">
  <div class="kpi clickable" onclick="topKpi('info')" title="点击查看全部学生"><div class="n">{roster_n}</div><div class="l">{scope_label}学生（{'总人数' if cov else '全量'}）</div></div>
  {kpi_cov}
  <div class="kpi red clickable" onclick="topKpi('slow')" title="点击筛选「响应迟缓」的学生"><div class="n">{slow_n}</div><div class="l">响应迟缓群（聊天分析）</div></div>
  <div class="kpi orange clickable" onclick="topKpi('unres')" title="点击筛选「待回复」的学生"><div class="n">{unres_n}</div><div class="l">待回复群（聊天分析）</div></div>
  <div class="kpi {'red' if rt_n_stu else 'green'} clickable" onclick="topKpi('recent3')" title="点击筛选「近 3 天待回应」的学生"><div class="n">{rt_n_stu}</div><div class="l">近3天待回应（{rt_n_items} 项）</div></div>
</div>
<div class="maintabs">
  <button class="mtab active" data-t="t1" onclick="showMain('t1')">🚨 失联预警{_cov_badge}</button>
  <button class="mtab" data-t="t2" onclick="showMain('t2')">🎓 主讲备课建议</button>
  <button class="mtab" data-t="t3" onclick="showMain('t3')">📋 学生学情 &amp; To-Do<span class="mcnt ok">{len(rows)}</span></button>
</div>
<div class="mainpanel" id="mp-t1">{cov_block or '<div class="covsec"><h2>🚨 失联预警</h2><div class="covbar">本报告未接入「总人数名单」数据（<code>coverage.json</code> 缺失），暂无法做总人数覆盖与失联预警。<br>请先运行 <code>adapt_legacy.py</code> 与 <code>coverage.py</code> 生成覆盖率快照后重新出报告。</div></div>'}</div>
<div class="mainpanel" id="mp-t2" style="display:none">{topic_panels}</div>
<div class="mainpanel" id="mp-t3" style="display:none">{rt_banner}
<div class="searchbar">
  <input id="stuSearch" class="searchinp" type="text" placeholder="🔍 输入学生姓名搜索（当前维度内实时筛选）" oninput="doSearch(this.value)">
  <span class="searchcnt" id="searchCnt"></span>
  <button class="searchclear" onclick="document.getElementById('stuSearch').value='';doSearch('')">清空</button>
</div>
{card_panels}</div>
<details class="meth"><summary>📖 方法论与口径说明（点击展开）</summary>
<p class="note">
• <b>报告结构（v4.10 起为三大 Tab）</b>：① <b>🚨 失联预警</b> —— 总人数覆盖统计与分档名单；② <b>🎓 主讲备课建议</b> —— 下节课重点讲什么（含覆盖人数、双源证据、典型题型与原文出处）；③ <b>📋 学生学情 &amp; To-Do</b> —— 逐生卡片与筛选。顶部 KPI 卡<b>全部可点击</b>：失联 / 出勤但无沟通 → 切到 Tab① 并展开对应档位名单；响应迟缓 / 待回复 / 近 3 天待回应 → 切到 Tab③ 并筛出对应学生。<br>
• 取数（群聊）：后台 <code>wxworkSessionStat/getList</code> 月维度与日维度并集得到该{role_cn}的全部学生群；<code>wxworkChat/getChatList</code> 分页取每群**全量**消息（倒序；单群 500 条上限已取消，按 <code>total</code> 逐页拉满），文本与语音转写均纳入。<br>
• 分析窗口：仅分析 <b>{args.window} 至今</b>的聊天内容。<br>
• 出勤（后台权威）：来自学生管理后台「我的学生 → 跟进学情 → 课节考勤」接口 <code>cClassAttend/getCClassAttendList</code>，逐节课记录 出勤✓ / 缺勤✗ / 是否看回放 + 回放有效时长 + 上课日期。<br>
• 出勤口径（v3.8 修正）：<b>看回放也算有效出勤</b> —— 有效出勤 = 到课 + 未到课但已看回放；只有「既没到课、也没看回放」才算<b>真缺课</b>（修正前把已看回放的节次也计成缺课）。呈现统一为<b>一句话</b>：如「出勤 4/5 次（含回放 2 次），缺课 1 次，第 5 节既没到课也没看回放」。判定：全勤 → 🟢；缺课 ≥2 节 → 🔴；缺课 1 节 → 🟡。<b>权重按角色区分：教务（为续报负责）在出勤一句话下另附折叠的逐节明细与「是否需关注」；联席（为学习效果负责）只有这一句话，不再单独成块，也不参与联席定档。</b><br>
• 请假备注：仅摘录学生在群内自述的请假/看回放缘由，后台无请假原因字段，具体以教务记录为准。<br>
• 报告结构（{view_cn}，v4.0 重排 · 去重）：<b>① 学习情况</b> —— 结论前置：<b>分层结论只出现在卡片右上角</b>（徽章：需重点关注 / 需关注 / 正常 / 良好），由四类证据<b>综合判档</b>：① 课堂小黑板作答（课次·轮次·空白/实质错误/仅未完成占比·典型错误·趋势）② 作业闭环（催交是否交、批改后是否订正）③ 老师反馈（群里指出问题 / 正面评价，<b>按密度归一化</b>）④ <b>学生成绩</b>（群聊里定期收集的月考 / 期中成绩，<b>最强证据</b>）；作业闭环做对、小黑板完整作答且无错都会加分，避免人人被判重点关注。判档依据与口径<b>折叠</b>在①末尾「学习分层判定依据」，<b>逐条给出加减分与累计总分</b>，点击展开即可核对。① 正文只保留<b>薄弱知识点</b>（<b>「聊天高频话题 × 小黑板实证」交叉验证</b>：两者都命中 → 🖍 双证据，仅小黑板命中 → 🖍 实证薄弱；聊天侧「错误类型」并入本行尾注，<b>不再单列</b> —— 它与薄弱点同源于高频话题，属同源重复）与<b>优势</b>；其下<b>课堂小黑板佐证</b>只留「一句话作答概况（轮数 / 分布 / 趋势）＋ 典型错误」，<b>逐次课情况表与逐轮明细表均默认折叠</b>，需要时点击展开。<b>② 配合度</b> —— 配合度结论 ＋{'<b>联席四维</b>（互动状态 / 催交作业是否交 / 批改后是否订正 / 提问意愿）＋' if not is_edu else ''}<b>出勤一句话</b>（{'教务另附折叠的逐节明细' if is_edu else '联席不再单独成块'}）＋ 风险信号条（不再另设「最该盯」独立块 —— 它与③第一条本是同一动作，v4.0 去重）。<b>③ {blk4_cn} To-Do</b>（优先级动作＋{'沟通节奏' if is_edu else '追踪节奏'}＋改善信号，<b>按优先级排序、第 1 条即「最该盯」并高亮；全报告动作清单只此一处</b>）——{'教务视角的 To-Do 面向「家长沟通 / 反馈学生问题 / 建立信任 / 推动续报」' if is_edu else '联席视角的 To-Do 严格按<b>联席四维</b>排序：互动状态 → 答疑闭环 → 催交作业 → 批改后订正 → 提问意愿；若小黑板暴露「多轮未收尾 / 多轮空白未提交」且聊天侧无更高优先级问题，该结论会置顶为「最该盯」。<b>出勤/请假不再进入联席 To-Do</b>（归教务关切）'}。{'教务视角支持双维度：① 按课程维度（课程 Tab 切换，每个课程下可「筛选联席」下钻到具体联席）；② 按联席维度（按下属联席下钻，学生卡标注所属联席）。' if is_edu else ('课程维度：单一课程下跨多个联席，顶部「筛选联席」下钻到具体联席，学生卡标注所属联席。' if is_course else '联席维度：同一联席名下的课程 Tab 切换，逐课程查看该联席负责的学生。')}<br>
• 小黑板目录层级（v3.8 明确）：第 1 层文件夹 = <b>一次课</b>（命名含上课日期，如 0906邵小黑板 / 0927邵清北打穿题）；第 2 层 = <b>该次课下的每一轮</b>（时间戳目录）；第 3 层 = 图片（带红笔批改痕迹的是联席批改后的图）。故报告按「课次 → 轮次」两层组织，轮次按第 2 层目录去重计数。<br>
• <b>配合度口径（v4.5 归因修正）</b>：结论由「学生自驱」＋「家长是否出面」两部分构成，且<b>家长侧只在学生自驱不足时才判负面</b>。修正的伪结论：<b>学生本人在群里很活跃时，家长往往就隐身了</b> —— 这不是「家长配合一般」，而是「家长没必要出面」，旧口径把「家长发言少」直接映射为负面定级，属<b>归因错位</b>。现口径：① 家长出面量改用<b>硬事实</b> —— 群内「除主导发言人外的其他发言人条数」（聊天数据带发言人昵称，可在卡片里逐条核对，<b>不再用会被学生本人话语污染的启发式分数</b>）；② 学生自驱达标（≥中等）时，家长少出面只做中性描述、<b>不下负面结论</b>；③ 家长零发言痕迹时改判「建议先联系家长确认」，不硬扣「配合偏弱」。<br>
• <b>学习分层口径（v4.4 重校）</b>：四档 <b>需重点关注（≤-3）／需关注（-2~-1）／正常（0~+1）／良好（≥+2）</b>，由四类证据加减分求和后落档，改动的三处 ——<br>
&nbsp;&nbsp;<b>(1) 老师反馈改为密度归一化</b>：旧版按「指出问题绝对次数」扣分（≥3 处即 -2），但真实数学群里老师高强度纠错是<b>教学常态</b>（实测三位学生分别在 2/8/8 次课里被指出 11/12/9 处），导致人人触发、人人扣分；现改为「每 10 轮小黑板（无小黑板数据时按每 100 条可分析消息）」的指出密度，≥10 → -2、≥5 → -1，未超基线不扣。<br>
&nbsp;&nbsp;<b>(2) 小黑板区分「仅未完成收尾」与「实质错误」</b>：「未完成收尾」在真实读图标注里占 25/33，大量轮次是「思路对了但没写完」——属课堂节奏 / 书写速度，<b>不记在能力账上</b>（仅在占比 ≥85% 时轻提示 -1）；只有剔除它之后的<b>实质错误</b>（计算错误 / 判断错误 / 思路偏差 / 推导跳步 / 漏解…）才按占比扣分（≥30% → -2、≥20% → -1）。空白未提交归参与度；「完整作答且无错」占比 ≥60% / ≥40% 给 +2 / +1。<br>
&nbsp;&nbsp;<b>(3) 新增学生成绩为最强证据</b>：从群聊里定期收集的月考 / 期中成绩中识别（<b>只匹配、不生成</b>，认不出即「未知」，绝不用裸数字硬凑）。口径按业务要求 —— <b>成绩好（得分率 ≥85%）→ 即使平时作业不做，也属学习效果较好，档位<b>托底「良好」</b>；成绩偏低（<65%）→ 即使作业都交、很配合，也属「学习效果不好」，档位<b>封顶「需关注」</b>；成绩中等（65%~85%）→ +1</b>。无满分标注时按学科推断（数学 150、其余 100）并在依据里注明「按 X 分制推断」。<br>
• 定档规则（v4.0：红黄绿定档<b>仅用于卡片排序与 KPI 统计</b>，卡片右上角只展示「学习分层」，避免两个分层标签让人混淆）：🔴 红=聊天分析判定「响应迟缓」（学生提问后 ≥24 小时才见老师回复）或「需回复」（会话尾部仍有未获回应提问）；🟡 黄=有未解疑问、作业负面信号、负面情绪{'、出勤异常（仅教务）' if is_edu else ''}；🟢 绿=其余正常推进。<br>
• 「是否需回复」结论：由聊天记录分析得出，不再依赖后台「是否回复」列（实测存在误差）——以「最后一条老师消息之后是否仍有学生/家长实质提问」为准；该结论同时驱动「待回复群」KPI 与定档红。<br>
• 响应时效：同样弃用后台「超时」标记（实测与聊天事实不符，如 6 分钟即回复的群被标「超时 19 小时」），改由聊天记录自行推算 —— 以「学生/家长的一条实质提问」到「其后第一条老师消息（含图片/讲义等任意动作）」的间隔为一次等待，同一轮多条连续提问只按首条起算；≥24 小时判「响应迟缓」并计入定档红，≥12 小时在卡片提示「最长响应等待」（不计定档），窗口末尾老师仍未回复的提问归入「需回复」。<br>
• <b>近 3 天待回应（v3.9 新增 · v4.0 改为顶部折叠入口）</b>：以窗口内最新一条消息为基准往前 3 天，逐条扫学生/家长的实质提问 —— ① <b>忘记回复</b>：提问之后（截至窗口末尾）群里再无任何老师消息；② <b>问题未解决</b>：老师确有回复，但回复命中延宕词（稍后 / 晚点 / 明天 / 回头…＝应允未答），或老师回复后 24 小时内学生就同类疑问再次追问（没讲透）。全量结果<b>汇总在报告顶部的「🚨 近 3 天待回应 N 项」入口条</b>（给管理者 / 教务的统一视图，<b>默认收起，点击展开</b>明细表：学生 / 日期 / 类型 / 提问原文 / 依据 / 建议动作），并在对应学生卡头部加红徽标、<b>② 配合度内置置顶清单</b>；同时生成最高优先级 To-Do（联席＝当天必须回应并跟进到学生确认「懂了」；教务＝提醒联席闭环并向家长同步进展）。全量无此类情况时入口条显示绿色「检查通过」。<br>
• <b>主讲备课建议（v4.9 新增 · v4.10.1 收敛为单表）</b>：回答「主讲下节课该重点讲什么／什么题型」，按<b>覆盖学生数降序</b>排列（普遍性优先），同覆盖时<b>有小黑板实证错误的优先</b>（小黑板是学生真实作答，比「群里提过」硬）；每条给出「典型题型／卡点」，点开可看原文出处，直接用于备课选题。<b>本表即为双源呈现</b> —— 此前「高频知识点」表只统计聊天命中（<code>extract.top_topics</code>），小黑板的实证错误完全没进；现并列展示「群聊提及」（聊天中命中该知识点的学生数）与「小黑板实证」（读图判定该知识点<b>有错误／卡点</b>的学生数），「覆盖学生」为两源并集。⚠️ <b>v4.10.1 删除了原先并列的「高频知识点 · 数据明细」表</b> —— 它的列（知识点／群聊提及／小黑板实证／合计涉及／占比）与建议表完全重合（用户指出「这两部分重复了，保留上面的即可」），故建议表改为<b>显示全部知识点</b>，长尾（数列、解三角形等）不再截断。③ ⚠️ <b>题型证据分硬／软</b>：标「实证错误 N」= 读图判「有错」或存在<b>非「没写完」</b>类实质错误（真的不会）；标「仅未完成／卡点」= 只是没写完（课堂节奏／书写速度，非能力）—— 与 v4.4 学习分层口径一致，不混为一谈。<b>读图模型对同一件事有「未完成收尾」与「收尾未完成」两种词序，后者才是真实语料主流写法（实测 176 条错误标注中占 94 条），故判定统一用包含式匹配（<code>common.is_finish_err</code>），精确匹配会静默漏掉最常见的写法。</b>④ 反编造：题型名只能来自 <code>FINE_TYPES</code> 词表命中或字段原文（形如「课堂作答卡点：…」），无证据时如实说明、留空，<b>绝不硬凑题型名</b>；不预测考情、不生成内容。<br>
• <b>总人数覆盖与失联预警（v4.8 新增 · 报告顶部独立区块）</b>：报告主体的学生卡<b>只覆盖「有聊天记录」的学生</b>，而总人数名单往往明显更大 —— 差额恰恰是最需要预警的一批人，本区块专门收口。判定用<b>两轴</b>：① 沟通轴（聊天，取近 {args.chat_days} 天）② 学习轴（出勤，取报告窗口全窗口）。分档：<b>失联</b>＝无聊天记录且无出勤记录；<b>出勤但无沟通</b>＝无聊天记录但有出勤记录（学生在正常上课，缺的是企业微信沟通通道）；<b>单向沟通</b>＝有聊天记录但学生·家长实质发言为 0（仅老师输出）；<b>低互动</b>＝学生·家长实质发言 1~2 条。⚠️ <b>判定「失联」一律以出勤为准，不以群是否存在为准</b> —— 群列表接口 <code>wxworkSessionStat/getList</code> 只收录有响应时效记录的群，实测有损（教务视角名单 243 人、群仅 144，缺口分散在全部 7 门课，其中 93 人仍在正常出勤），若拿群判失联会一次性误报约四成学生。群「采到与否」仅作根因标签展示。两轴窗口必须分开：出勤本稀疏，若也只看近两周，会把已结课学生全部误判为「无出勤」→ 误报失联。<br>
• <b>预警卡（v4.12 落地 · Tab③ 学生学情）</b>：常规学生卡来自「有聊天记录」的学生，<b>在册、却一条聊天记录都没有的学生在 Tab③ 原本完全缺席</b>。v4.12 把这批人（失联 / 出勤但无沟通）从覆盖率快照 <code>coverage.json</code> 里捞出来，以<b>左侧红边预警卡</b>呈现：把<b>沟通轴</b>（群在不在、有没有人说话）与<b>学习轴</b>（出勤 ＋ 小黑板作答）两句话摊开写清，给出<b>结论</b>与<b>可直接执行的动作</b>，并逐项标注<b>数据来源</b>（在册花名册 / 群列表 / 聊天 / 小黑板 各自 ✓ 或 ✗），便于回后台、回文件夹逐字核对。🔒 <b>防伪铁律</b>：学习轴一旦有证据（有出勤，或有小黑板作答）→ 结论只写「<b>企业微信沟通通道失效</b>」，<b>绝不写「学生流失」</b>（实测本课「出勤但无沟通」学生全部正常上课，如刘庭岩出勤 5/5、小黑板作答 21/29 张）；只有「既无沟通、也无出勤与小黑板」才写「无学习痕迹 · 需人工核实就读状态」。⚠️ <b>v4.14 起预警卡默认收起、并置于本组常规学生卡的下方</b> —— 常规学生卡才是主要信息，一进 Tab③ 不应被红卡占满首屏。展开入口有两处：本组底部的「⚠️ 另有 N 名在册学生未进入学情分析」入口条（点击就地展开 / 收起），以及上方 KPI 卡「<b>未触达预警</b>」（点击隐藏常规卡、只留预警卡）；点「学生数」或再点一次即恢复。搜索命中预警卡时该区自动展开。<br>
</p></details>
</div>
<script>
// ===== v4.10：三大 Tab（① 失联预警 ② 主讲备课建议 ③ 学生学情 & To-Do）=====
function showMain(t){{
  ['t1','t2','t3'].forEach(function(k){{
    var p=document.getElementById('mp-'+k);
    if(p) p.style.display=(k===t?'block':'none');
  }});
  document.querySelectorAll('.mtab').forEach(function(b){{
    b.classList.toggle('active', b.getAttribute('data-t')===t);
  }});
  window.scrollTo({{top:0,behavior:'smooth'}});
}}
// v4.10.3：档位表「人数」开关 —— 点击在本行下方展开 / 收起该档名单
function covToggle(t, forceOpen){{
  var row=document.getElementById('covdet-'+t); if(!row) return;
  var open = row.style.display !== 'none';
  var next = forceOpen ? true : !open;
  row.style.display = next ? 'table-row' : 'none';
  var n=document.querySelector('.covn[data-covn="'+t+'"]');
  if(n) n.classList.toggle('on', next);
}}
// 在指定面板内切换分组（Tab2 与 Tab3 各自一套，id 前缀不同，互不干扰）
function showTabIn(panelId, prefix, idx){{
  var pn=document.getElementById(panelId); if(!pn) return;
  pn.querySelectorAll('.grp').forEach(function(el){{el.style.display='none';}});
  pn.querySelectorAll('.tab').forEach(function(el){{el.classList.remove('active');}});
  var el=document.getElementById(prefix+'-'+(idx<0?'all':idx)); if(el) el.style.display='block';
  var tabs=pn.querySelectorAll('.tab');
  if(tabs.length){{ (idx<0?tabs[0]:tabs[idx+1]).classList.add('active'); }}
}}
// 维度切换（教务双维度）；where = 't2'（备课 Tab）| 't3'（学生 Tab）
function showDim(where, d){{
  ['course','co'].forEach(function(k){{
    var p=document.getElementById(where+'-'+k);
    if(p) p.style.display=(k===d?'block':'none');
  }});
  document.querySelectorAll('.dimb[data-w="'+where+'"]').forEach(function(b){{
    b.classList.toggle('active', b.getAttribute('data-d')===d);
  }});
  showTabIn(where+'-'+d, (where==='t2'?'t':'')+(d==='co'?'co':'c'), -1);
}}
// v4.10：顶部 KPI 全部可点击 ——
//   失联 / 出勤但无沟通 → 切到「失联预警」Tab 并展开对应档位名单；
//   响应迟缓 / 待回复 / 近3天待回应 → 切到「学生学情」Tab 并按该类筛选学生卡。
function topKpi(kind){{
  if(kind==='lost'||kind==='anc'){{
    showMain('t1');
    var t=(kind==='lost')?'失联':'出勤但无沟通';
    covToggle(t, true);   // 第二参 = 强制展开（不收起）
    var el=document.getElementById('covdet-'+t);
    if(el){{ setTimeout(function(){{el.scrollIntoView({{behavior:'smooth',block:'center'}});}},80); }}
    return;
  }}
  showMain('t3');
  var b=document.getElementById('t3-co');
  var useCo = b && b.style.display!=='none';
  var pf = useCo?'co':'c';
  var pnId = useCo?'t3-co':'t3-course';
  showTabIn(pnId, pf, -1);
  var allId = pf+'-all';
  var sec=document.getElementById(allId); if(!sec) return;
  if(kind==='info'){{
    sec.querySelectorAll('.kpi.kbl').forEach(function(x){{x.classList.remove('on');}});
    sec.querySelectorAll('.card').forEach(function(c){{c.style.display='block';}});
    warnSet(sec, false);
    var c0=document.getElementById('kpiCnt-'+allId); if(c0) c0.textContent='';
    return;
  }}
  var tile=sec.querySelector('.kpi.kbl[data-k="'+kind+'"]');
  if(tile) kpiFilter(tile, allId, kind);
}}
// v4.14：预警卡区默认收起（放在常规学生卡下方，不挤占主要信息）。
// 展开入口有两个：底部入口条 warnToggle()、上方 KPI 卡「未触达预警」。
function warnSet(sec, open){{
  var wz=sec.querySelector('.warnzone');
  var bar=sec.querySelector('.warnbar');
  if(wz){{
    wz.style.display = open ? 'block' : 'none';
    if(open) wz.querySelectorAll('.card').forEach(function(c){{c.style.display='block';}});
  }}
  if(bar) bar.style.display = open ? 'none' : '';
  var c=bar?bar.querySelector('.warnbar-c'):null;
  if(c && !open) c.textContent='点击展开预警卡 ▾';
}}
function warnToggle(secId){{
  var sec=document.getElementById(secId); if(!sec) return;
  var wz=sec.querySelector('.warnzone'); if(!wz) return;
  var open=(wz.style.display==='block');
  warnSet(sec, !open);
  if(!open) wz.scrollIntoView({{behavior:'smooth',block:'nearest'}});
}}
function coFilter(sel){{
  var sec=document.getElementById(sel.dataset.target); if(!sec) return;
  var v=sel.value;
  warnSet(sec, false);   // 换联席 → 预警区回到收起态，避免与筛选结果混杂
  sec.querySelectorAll('.card').forEach(function(c){{
    c.style.display = (v==='__all__' || c.getAttribute('data-co')===v) ? 'block' : 'none';
  }});
}}
// v4.3：KPI 小卡点击筛选 —— 点「响应迟缓群 / 未解疑问群 / 互动不足 / 催交未交 / 批改未改 / 有缺勤」
// 只保留对应学生；点「学生数」或再次点击当前项 → 恢复全部。v4.14：点「未触达预警」→ 只展开预警卡。
function kpiFilter(el, secId, key){{
  var sec=document.getElementById(secId); if(!sec) return;
  var wasOn=el.classList.contains('on');
  sec.querySelectorAll('.kpi.kbl').forEach(function(x){{x.classList.remove('on');}});
  var showAll = (key==='__all__') || wasOn;
  if(showAll){{
    sec.querySelectorAll('.card').forEach(function(c){{c.style.display='block';}});
    warnSet(sec, false);
    var cnt=document.getElementById('kpiCnt-'+secId);
    if(cnt) cnt.textContent='';
    return;
  }}
  el.classList.add('on');
  var cnt0=document.getElementById('kpiCnt-'+secId);
  if(key==='warn'){{
    sec.querySelectorAll('.card').forEach(function(c){{c.style.display='none';}});
    warnSet(sec, true);
    var wz=sec.querySelector('.warnzone');
    var nw=wz?wz.querySelectorAll('.card').length:0;
    if(cnt0) cnt0.textContent='已筛选：未触达预警 · '+nw+' 人';
    if(wz) setTimeout(function(){{wz.scrollIntoView({{behavior:'smooth',block:'center'}});}},80);
    return;
  }}
  warnSet(sec, false);
  var n=0;
  sec.querySelectorAll('.section .card').forEach(function(c){{
    var hit=(c.getAttribute('data-'+key)==='1');
    c.style.display = hit ? 'block' : 'none';
    if(hit) n++;
  }});
  var cnt=document.getElementById('kpiCnt-'+secId);
  if(cnt) cnt.textContent='已筛选：' + el.querySelector('.l').textContent + ' · ' + n + ' 人';
}}
function doSearch(val){{
  var q=(val||'').trim().toLowerCase();
  // 搜索只作用于「学生学情」Tab（Tab3）—— 只有这个 Tab 才是按学生卡片组织的。
  var activeDim=document.querySelector('#mp-t3 .dimb.active');
  var dim=activeDim?activeDim.getAttribute('data-d'):'course';
  var panel=document.getElementById('t3-'+dim); if(!panel) return;
  var cnt=document.getElementById('searchCnt');
  if(q===''){{
    panel.querySelectorAll('.grp').forEach(function(g){{ warnSet(g, false); }});
    showTabIn('t3-'+dim, (dim==='co'?'co':'c'), -1);
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
  // 隐藏没有匹配学生的空分组；命中预警卡时自动展开预警区（否则卡片藏在收起区里看不到）
  panel.querySelectorAll('.grp').forEach(function(g){{
    var wz=g.querySelector('.warnzone'), bar=g.querySelector('.warnbar'), hitW=0;
    if(wz){{
      wz.querySelectorAll('.card').forEach(function(c){{ if(c.style.display!=='none') hitW++; }});
      wz.style.display = hitW ? 'block' : 'none';
    }}
    if(bar) bar.style.display = hitW ? 'none' : '';
    var any=[].slice.call(g.querySelectorAll('.card')).some(function(c){{ return c.style.display!=='none'; }});
    if(!any) g.style.display='none';
  }});
  if(cnt) cnt.textContent='匹配到 '+n+' 名学生（当前维度）';
}}
</script>
</body></html>"""
    # 课程视角：--course 可传完整课程名（含 2026Q-SX- 前缀），文件名取简洁课程名，避免一串前缀不好认
    out = f"{wd}/{common.course_name(name) if is_course else name}_学情分析.html"
    open(out, "w", encoding="utf-8").write(html)
    if is_edu:
        print(f"✅ HTML 生成完成（教务双维度）｜ 课程数 {len(course_order)} · 联席数 {len(co_order)} ｜ {out}")
    elif is_course:
        print(f"✅ HTML 生成完成（课程维度）｜ 课程：{name} · 联席数 {len(co_all)} · 学生 {len(rows)} ｜ {out}")
    else:
        print(f"✅ HTML 生成完成（联席维度 · 按课程下钻）｜ 课程数 {len(course_order)} ｜ {out}")


if __name__ == "__main__":
    main()
