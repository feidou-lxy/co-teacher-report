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
import html as _html
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
    # ⚠️ v4.31 起**已停用**（不再出现在报告里）。它把知识点无条件翻成一句固定错因
    #   （如「函数与导数」→「求导与单调性讨论」），没学导数时等于编造。留档备查，勿再调用。
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


def four_dims(s, fs_ok=False):
    """联席关注四维（v3.7）：互动状态 / 催交作业是否交 / 批改后是否订正 / 提问意愿。

    这四维是「联席为学习效果负责」的直接抓手，只在联席 / 课程视角出现。
    出勤/请假不在此列——它归教务关切（见 att_block 的教务版）。

    `fs_ok`（v4.28）：飞书表已确认提交（≥3 条记录且 ≥80%）。此时「催交后群里没回话」
    是**沟通问题**、不是没交作业 —— 文案要说清，否则同一张卡上「0/2 已提交」与
    「飞书 4/4（100%）」互相打架（实测 28/50 人）。"""
    cells = []
    lv_cls = {"ok": "ok", "warn": "warn", "bad": "bad"}
    # ① 互动状态
    cells.append(("互动状态", s.get("interact") or "—", lv_cls.get(s.get("interact_lv"), ""),
                  s.get("interact_evi") or ""))
    # ② 催交作业 → 是否交
    un, uo = s.get("urge_n", 0), s.get("urge_ok", 0)
    if un == 0:
        cells.append(("催交作业", "未催交", "", "窗口内无催交记录"))
    elif uo == un:
        cells.append(("催交作业", f"{uo}/{un} 已提交", "ok", "均已在提醒后提交"))
    elif fs_ok:
        # 群里没回话，但飞书表有提交记录 → 说清是「不回消息」，不写成「没交」
        cells.append(("催交作业", f"{uo}/{un} 群内回应", "warn",
                      f"提醒后群里未回 {un - uo} 次；飞书表有提交记录 → 属未回消息，非未交作业"))
    else:
        cells.append(("催交作业", f"{uo}/{un} 已提交", "bad" if uo == 0 else "warn",
                      f"提醒后仍未交 {un - uo} 次"))
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
    —— 用户反馈右上角同时出现两个分层标签时语义混淆。
    v4.29：**卡片排序也改为学习分层**（用户要求「需要关注的在上方、正常的在下方」），
    本函数**当前没有任何调用点**，仅作历史口径留档。新代码请勿再从 grade() 取档位，
    否则会出现「卡上写需重点关注、却排在正常堆里」的自相矛盾。
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
        # v4.24：无出勤记录**不等于退课**。群里有活跃沟通的，最可能是未排课 / 纯看回放——
        #   直接写「可能已退课」会让教务打错电话（实测本课 3 人：群内 26~83 条、最近消息当天）。
        _n = r["s"].get("n_text") or 0
        if _n:
            _new = (r["s"].get("newest") or "")[:10] or "—"
            return (f'<div class="attline none"><span class="attk">出勤</span>'
                    f'后台无该生出勤记录（最近消息 {_new}、群内 {_n} 条）→ '
                    f'更可能是<b>未排课 / 纯看回放</b>，不像退课；需向教务确认排课状态</div>')
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


def topic_unit_key(t):
    """取知识点的「子单元名」：`立体几何/空间向量` → `空间向量`。

    v4.35：同一知识点在不同来源里名字不同 —— `topic_signals` 用子单元（`空间向量`），
    `fine` / 小黑板错误用粗类前缀（`立体几何/空间向量`）。不归一的话同一知识点会在卡上
    出现两枚 chip（一枚「已掌握」、一枚「小黑板实证薄弱」），自相矛盾。
    """
    parts = [norm_topic_key(x) for x in re.split(r"[/·]", str(t or "")) if x.strip()]
    return parts[-1] if parts else norm_topic_key(t)


def same_topic(a, b):
    """两个知识点名是否指同一件事（全等 or 子单元相等）。`函数` ≠ `三角函数`，不能用包含判断。"""
    if not a or not b:
        return False
    if a == b:
        return True
    na, nb = norm_topic_key(a), norm_topic_key(b)
    if na == nb:
        return True
    return topic_unit_key(a) == topic_unit_key(b)


def pick_topic(m, t):
    """在 {知识点: 值} 里按 same_topic 取值（精确键优先）。"""
    if not m:
        return None
    if t in m:
        return m[t]
    for k, v in m.items():
        if same_topic(k, t):
            return v
    return None


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


def _mastered_line(md):
    """v4.32：把「已掌握」的知识点渲染成一行（用于 fine 分支 —— 那里不显示 chips 行）。

    业务要求：学生问过、老师讲过、后来确认懂了的知识点，要继续展示但要标出「已掌握」，
    否则读的人会以为现在还薄弱。每条都带原文出处，可回群核对。
    """
    if not md:
        return ""
    items = "".join(
        f'<span class="mstitem"><b>{_html.escape(t)}</b>'
        f'<span class="mstwhen">{_html.escape(str(v.get("at") or ""))} '
        f'{_html.escape(str(v.get("who") or ""))}说「'
        f'{_html.escape(str(v.get("text") or "")[:34])}」</span></span>'
        for t, v in md.items())
    return (f'<div class="mstline"><span class="msth">✅ 已掌握</span>{items}'
            f'<span class="mstnote">问过、答过、之后本人确认听懂（或老师确认做对）—— 不用再盯</span></div>')


def render_fine(fine, fallback_chips, err_tip="", mastered=None):
    """细粒度薄弱点渲染（v4.2）：**知识点 → 具体题型/方法**，每条附原文出处，可回群核对。

    这是「薄弱知识点」的替代呈现（不再用课程大类 chips，那对联席没有可操作性）：
    如「直线和圆」下面到底是「含参直线过定点」还是「圆的一般式与圆心坐标」写错。

    有细粒度证据时**只呈现细粒度**（聊天侧「错误类型」与其同源、更粗，不再单列，避免用户
    反馈的「三部分内容重复」）；确实下钻不到题型时，才退回粗知识点 chips ＋ 一句聊天高频错因兜底。
    """
    if not fine:
        # v4.31：**停用固定错因**（原 `ERR_MAP` 拼的「聊天高频错因」）——
        #   它不看实际命中词，硬把「函数」翻成「求导与单调性讨论」；高一上学期根本没学导数，
        #   于是卡片上出现一句凭空的错因，业务一眼就能看出「不知道哪来的」。
        #   现在：有具体题型才写错因；没有就只列知识点范围 ＋ 交代统计口径。
        _none = (not fallback_chips) or fallback_chips in ("—", '<span class="chip">—</span>')
        _note = (
            '窗口内未捕捉到明确薄弱证据 —— 只统计学生提问 / 说「不会」、老师明确指出错误的群消息；'
            '老师讲课内容、「还没学 / 后面才学」等进度说明、报名收费等事务消息<b>一律不计</b>。'
            if _none else
            '只统计群内「问题信号」：学生/家长提问、说「不会」、做错，或老师明确指出错误、要求强化。'
            '老师讲课与转写内容、「还没学 / 后面才学」等进度说明、报名收费等事务消息<b>一律不计</b>；'
            '<b>实心</b> = 同一知识点 ≥2 条（可当结论看），'
            '<b>虚线</b> = 仅 1 条（弱证据，只能说明提过），'
            '<b>已掌握</b> = 问题提出后学生明确说懂了 / 老师确认正确（悬停可看原文，可回群核对）。'
            '悬停每个知识点可看信号条数与首条原话，可回群逐字核对。')
        return (f'<div class="kv"><span class="k">薄弱知识点</span><span class="chips">{fallback_chips}</span></div>'
                f'<div class="note">{_note}</div>')
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
            + "".join(blocks) + _mastered_line(mastered) + '</div>')


def _fine_rows_for(name, fine):
    """把「知识点名」对应到细粒度题型清单。

    `topic_signals` 的键是 TOPIC_UNITS 子单元（`空间向量`），`fine` 的键是粗大类
    （`立体几何/空间向量`）—— 不能直接 get，要能互相包含地找（精确键优先）。
    """
    if not fine or not name:
        return []
    if fine.get(name):
        return list(fine[name])
    nk = norm_topic_key(name)
    if not nk:
        return []
    out = []
    for k, rows in fine.items():
        kk = norm_topic_key(k)
        if kk and (kk == nk or nk in kk or kk in nk):
            out.extend(rows)
    return out


def topic_row_html(names, chip_map, fine, sig, evi, mst, limit=4):
    """v4.35：卡片可见区「主要薄弱知识点」一行（用户要求）。

    原来知识点全塞在「补充信息」折叠区里 —— 要点两次才看得到，展开又和大段明细混在一起。
    现在：**可见区只列知识点名 ＋ 条数**（一眼知道卡在哪），**点某一枚才展开该知识点下的
    具体题型 / 方法 ＋ 原文出处**（可回群逐字核对）。默认全部收起，互不影响。
    已掌握的知识点也保留（带「已掌握」角标），展开能看到闭环依据，不会被误读成还没掌握。
    """
    items = []
    for t in names:
        v = pick_topic(sig, t) or {}
        e = pick_topic(evi, t) or v or {}
        rows = _fine_rows_for(t, fine)
        rows.sort(key=lambda r: -int(r.get("count") or 1))
        n = int(v.get("n") or e.get("n") or 0)
        # 「已掌握」与 chip 同源判定（chip 上带 msttag 才算）—— 小黑板有实证错误时
        #   chip 走 dual/kb 分支、不带 msttag，这里不能只看 _mst 就当成已掌握。
        mv = pick_topic(mst, t) if "msttag" in (chip_map.get(t) or "") else None
        items.append((t, n, rows, mv, e))
    # 实心未掌握 → 弱证据（仅 1 条）→ 已掌握（不用再盯）；同档按条数降序
    items.sort(key=lambda x: (bool(x[3]), 0 if x[1] >= _CARD_MIN_SIG else 1, -x[1]))
    kind_cls = {"老师指出": "tea", "学生提问": "stu", "小黑板": "bd"}
    chips, rest = [], []
    for i, (t, n, rows, mv, e) in enumerate(items):
        body = ""
        if rows:
            lis = ""
            for r in rows:
                k = r.get("kind") or ""
                c = int(r.get("count") or 1)
                lis += (f'<li><span class="fitm">{_html.escape(str(r.get("item") or ""))}</span>'
                        f'<span class="fsrc {kind_cls.get(k, "")}">{_html.escape(k)}</span>'
                        + (f'<span class="fcnt">×{c}</span>' if c > 1 else "")
                        + f'<span class="fevi">{_html.escape(str(r.get("at") or ""))} '
                          f'「{_html.escape(str(r.get("evi") or "")[:60])}」</span></li>')
            body += f'<ul class="fine-l">{lis}</ul>'
        elif e.get("text"):
            body += (f'<div class="tpda">群里 {n or "几"} 条问题信号 · 首条 '
                     f'{_html.escape(str(e.get("at") or ""))}'
                     f'「{_html.escape(str(e["text"])[:70])}」</div>')
        if mv:
            body += (f'<div class="tpdm">✅ 已掌握 · {_html.escape(str(mv.get("at") or ""))} '
                     f'{_html.escape(str(mv.get("who") or ""))}说「'
                     f'{_html.escape(str(mv.get("text") or "")[:48])}」—— 之后没再追问</div>')
        if not body:
            body = '<div class="tpda">窗口内只提到过这一处，暂无更细的题型证据</div>'
        chip = chip_map.get(t) or (f'<span class="chip">{_html.escape(t)}</span>')
        if n > 1 and "tcnt" not in chip:
            chip = chip.replace("</span>", f'<i class="tcnt">×{n}</i></span>', 1)
        cls = "tchip mstchip" if mv else "tchip"
        chunk = (f'<details class="{cls}"><summary>{chip}</summary>'
                 f'<div class="tchipd">{body}</div></details>')
        (chips if i < limit else rest).append(chunk)
    if not chips:
        return "", []
    html = ('<div class="trow"><span class="trowk">薄弱点</span>'
            f'<div class="tchips">{"".join(chips)}</div></div>')
    return html, rest


def issue_row(fst, limit=2):
    """v4.37：卡片可见区「学生问题」一行（用户要求）。

    来源 = 飞书表「学生学情 / 问题点」列（`common.fs_issues()` 已切句分类）。
    之前这段内容只躺在「补充信息 → 老师手记」里，要点两次才看得到 ——
    而它往往是**群里看不出来、只有老师/教务在表里写过**的那部分问题
    （不写小黑板、下课联系不上、家长不好联系、基础差全忘了…）。

    🔒 上屏的是表内**原句**（只截长度，hover / 展开给全文），点开还给出表名与行号。
    红 = 行为 / 态度 / 沟通；橙 = 基础 / 状态 / 外部压力。
    默认只露 2 条（够看清最要紧的），其余折进「还有 N 条」，可展开。
    """
    iss = (fst or {}).get("issues") or []
    if not iss:
        return ""
    src = ""
    if fst.get("sheet"):
        src = f'飞书表「{_html.escape(str(fst["sheet"]))}」'
        if fst.get("row"):
            src += f' 第 {int(fst["row"])} 行'

    def chip_html(sev, s, full):
        cls = "hard" if sev == 0 else "soft"
        txt = str(s)
        short = txt if len(txt) <= 30 else txt[:30] + "…"
        # 展开给**整句原文**（长句按逗号切过子句时，上下文不丢：「一直是不交作业」背后
        #   还有「不过暑期总结过框架笔记」这类补充，读者要能看到）
        whole = str(full or txt)
        body = (f'<div class="tchipd"><div class="tpda">表内原话：{_html.escape(whole)}</div>'
                + (f'<div class="isrc">{src}</div>' if src else "") + "</div>")
        return (f'<details class="tchip ichip {cls}">'
                f'<summary><span class="chip iss {cls}" title="{_html.escape(whole)}">'
                f'{_html.escape(short)}</span></summary>'
                f'{body}</details>')

    chips = [chip_html(sev, s, full) for sev, s, full in iss[:limit]]
    rest = iss[limit:]
    if rest:
        inner = "".join(f'<div class="tpda">{_html.escape(str(full or s))}</div>'
                        for _, s, full in rest)
        if src:
            inner += f'<div class="isrc">{src}</div>'
        chips.append(f'<details class="tchip ichip more"><summary>'
                     f'<span class="chip iss more">还有 {len(rest)} 条</span></summary>'
                     f'<div class="tchipd">{inner}</div></details>')
    return ('<div class="trow irow"><span class="trowk">学生问题</span>'
            f'<div class="tchips">{"".join(chips)}</div></div>')


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


# ---- v4.19.3 飞书表学习记录：由 adapt_feishu.py 产出 snapshot/feishuLearning.json，
#      在 main() 里按 **(姓名归一, 课程全名) 复合键** 索引后填充（模块级，避免改动所有卡片调用点）。
#      ⚠️ 复合键是必须的：实测同一学生会同时上多门课（本项目 12 人），只按姓名会串课。
FS_MAP = {}
# v4.27：加课 / 电话沟通（飞书表 meta.extraClass / meta.phoneComm）—— 预警卡也要用同一个入口取记录
FS_BY_NAME = {}


def fs_lookup(stu, product=None, course_raw=None):
    n = common.norm_name(stu or "")
    return (FS_MAP.get((n, product))
            or FS_MAP.get((n, course_raw or ""))
            or FS_BY_NAME.get(n))
# v4.24：本份报告是否真的有小黑板数据源。整份都没有时，卡片里那句
#   「该生暂无小黑板图片（未被收录 / 课程未提供）」×N 人纯属噪音 —— 直接不渲染该子块。
BOARDS_ON = True


def rating_block(ratings):
    """⭐ v4.21 评级趋势块：把逐节课的等级排成序列条 + 给出趋势结论。

    数据来自 adapt_feishu.py 的 `record["ratings"]`（由 profile.feishu.ratings 声明产生）。
    ⚠️ 没有等级列的团队 `ratings` 为空 → 直接返回 ("", "")，**对其他团队零影响**。
    返回 (折叠块内 HTML, 摘要徽章列表)。
    """
    if not ratings:
        return "", []
    parts, badges = [], []
    for item in sorted(ratings):
        g = ratings.get(item)
        if not isinstance(g, dict) or not g.get("seq"):
            continue
        seq = g.get("seq") or []
        absent = g.get("absent") or []
        levels = g.get("levels") or []
        labels = g.get("labels") or {}
        cnt = g.get("counts") or {}
        n_r, n_a = g.get("nRated") or 0, g.get("nAbsent") or 0
        trend = g.get("trend") or "na"

        # ---- 序列条：每节课一个方块（等级着色 / 「未提交」灰底 / 无记录虚线空框）
        cells = []
        for i, lv in enumerate(seq):
            ab = absent[i] if i < len(absent) else 0
            cls = f"rg-{lv}" if lv else ("rg-absent" if ab else "rg-none")
            txt = lv or ("未" if ab else "")
            tip = f"第{i + 1}次课：" + (f"{lv} {labels.get(lv, '')}".strip() if lv
                                       else ("未提交" if ab else "无记录"))
            cells.append(f'<i class="{cls}" title="{_html.escape(tip)}">{txt}</i>')
        cells.append(f'<span class="rgax">共 {len(seq)} 次课</span>')

        # ---- 结论
        cdesc = " ".join(f"{lv}×{cnt[lv]}" for lv in levels if cnt.get(lv)) or "—"
        min_r = g.get("minRated") or 3
        tail_absent = g.get("tailAbsent") or 0
        n_none = max(0, len(seq) - n_r - n_a)
        extra = []
        if n_a:
            extra.append(f"{n_a} 次未提交")
        if n_none:
            extra.append(f"{n_none} 次无记录")
        tail = ("；另有 " + "、".join(extra)) if extra else ""

        # ⚠️ 「末尾连续未提交」优先于趋势：人正在掉队比「降了一级」更紧急，
        #    且它不受样本量影响（有效次数不足时也成立）。
        if tail_absent >= 2:
            badge, bcls = f"近 {tail_absent} 次未交", "down"
        elif not n_r:
            badge, bcls = "无评级", "flat"
        elif n_r < min_r:
            badge, bcls = "样本不足", "flat"
        elif trend == "up":
            badge, bcls = "上升 ↑", "up"
        elif trend == "down":
            badge, bcls = "下滑 ↓", "down"
        else:
            badge, bcls = "稳定 →", "flat"

        warn = (f"⚠️ **最近连续 {tail_absent} 次未提交**。" if tail_absent >= 2 else "")
        if not n_r:
            bodytxt = f"**一次有效评级都没有**{tail}"
        elif n_r < min_r:
            bodytxt = f"仅 {n_r} 次有效评级（{cdesc}），不足 {min_r} 次**暂不判趋势**{tail}"
        elif trend == "up":
            bodytxt = (f"前期 {g.get('first')} → 近期 {g.get('last')}，**呈上升**"
                       f"（均分 {g.get('early')} → {g.get('late')}）{tail}")
        elif trend == "down":
            bodytxt = (f"前期 {g.get('first')} → 近期 {g.get('last')}，**呈下滑**"
                       f"（均分 {g.get('early')} → {g.get('late')}）{tail}")
        else:
            bodytxt = f"稳定在 {g.get('mean')} 分档（{cdesc}）{tail}"
        concl = warn + bodytxt

        # `**x**` → <b>，其余一律转义（等级只有单字母，但中文描述必须转义）
        chunks = concl.split("**")
        concl_html = "".join((f"<b>{_html.escape(c)}</b>" if i % 2 else _html.escape(c))
                             for i, c in enumerate(chunks))
        parts.append(
            f'<div class="fsrate"><div class="fshr"><b>📈 {_html.escape(item)}趋势</b>'
            f'<span class="rtb {bcls}">{badge}</span>'
            f'<span class="rtsum">{concl_html}</span></div>'
            f'<div class="rseq">{"".join(cells)}</div></div>')
        badges.append((item, badge, bcls, trend))
    return "".join(parts), badges


# 飞书表「学校」列的无意义取值（团队手填，噪音很多）
SCHOOL_NOISE = ("#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!",
                "保密", "不填", "无", "暂无", "未知", "不详", "没有", "待定", "空",
                "—", "-", "/", "\\", "无数据", "n/a", "N/A", "na", "0")


def school_of(st):
    """从飞书表元数据取「学校」并去噪（v4.25）。

    ⚠️ 团队手填的学校列噪音很重（实测 1117 条里 435 条有值，其中 `#N/A` 48 条、
       「保密」5 条）—— 直接展示会把公式错误值和「保密」当校名挂在学生头上。
       公式错误值（`#N/A` 等）= 没拿到数据，与空白同待遇，一律不展示。
    """
    if not st:
        return ""
    # 兼容两种入参：fs_stats 的结果（metaRaw）／adapt 产出的原始 record（meta）
    _m = st.get("metaRaw") if isinstance(st.get("metaRaw"), dict) else (st.get("meta") or {})
    v = _m.get("school")
    if v is None:
        return ""
    t = str(v).strip()
    if not t or t in SCHOOL_NOISE or len(t) < 2:
        return ""
    return t


def school_line(st):
    """姓名下方那一行小字（🏫 校名）。没有有效校名时返回空串，整行不渲染。"""
    t = school_of(st)
    return f'<span class="cschool" title="飞书表填写：{_html.escape(t)}">🏫 {_html.escape(t)}</span>' if t else ""


# ============ v4.36 近期成绩 → 学校名下方一行（用户要求：放到明显位置） ============
EXAM_LV_CN = {"高": "较好", "中": "中等", "偏低": "偏低"}
EXAM_LV_CLS = {"高": "ok", "中": "warn", "偏低": "bad"}


def exam_head(s, fst):
    """卡头「近期成绩」一行（学校名下面）。返回 (html, 是否展示)。

    用户原话（2026-10-20）：「近期的成绩也应该放在比较重要明显的位置，可以放在学校后面」——
    成绩是学习分层最强的证据之一，原来埋在「补充信息」折叠区，读者要点开才看得到，
    而折叠区的成绩恰恰是手机端最容易被忽略的一行。

    取数口径与 `common.learning_level()` **同源**（聊天侧 `s["exam"]` 优先；聊天侧没有
    才用飞书表 `fst["exam"]`），所以卡头显示的就是判档用的那一条，不会出现
    「卡头 70、判档按 90」的对不上。两个来源都没有 → 返回空串，整行不渲染（不写「—」）。

    ⚠️ 只展示**最近一次**（`latest`）；历史成绩不进卡头（否则一行装不下，且与分层无关）。
    """
    ex = (s or {}).get("exam") or {}
    exl = ex.get("latest") or None
    if exl:
        if exl.get("value") is not None:
            score = f'{exl["value"]:g}<span class="cf">/{exl["full"]:g}</span>'
        elif exl.get("rank"):
            score = f'第 {exl["rank"]} 名' + (f'<span class="cf">/{exl["total"]:g}</span>'
                                              if exl.get("total") else "")
        else:
            score = "—"
        pct = f'{exl["pct"]:.0%}' if exl.get("pct") is not None else ""
        lab = exl.get("label") or "考试"
        date = (exl.get("date") or "")[5:]          # 2026-10-08 → 10-08
        meta = " · ".join(x for x in (lab, date) if x)
        tip = (f'{lab} · {exl.get("date") or ""}｜群聊原话：{exl.get("evi") or ""}'
               + ("（原文无满分标注，满分按学科默认值推断）" if exl.get("assumed") else ""))
        if fst and fst.get("exam"):
            tip += f'｜飞书表另有：{fst["exam"].get("text") or ""}'
    elif fst and fst.get("exam"):
        f = fst["exam"]
        _raw = str(f.get("shown") or f.get("text") or "")
        # 「非常不理想」「不及格」这类**没有分数**的表内写法：不当大号分数渲染（否则像 0 分），
        #   改成一句批注样式，避免读者把文字当数字。
        score = (_html.escape(_raw) if re.search(r"\d", _raw)
                 else f'<span class="cword">{_html.escape(_raw)}</span>')
        pct = f'{f["pct"]:.0%}' if f.get("pct") is not None else ""
        meta = "飞书表"
        tip = (f'飞书表「{fst.get("sheet") or ""}」第 {fst.get("row")} 行：'
               f'{f.get("text") or ""}'
               + (f'（{f["assumed"]}）' if f.get("assumed") else ""))
    else:
        return "", False
    lv = ex.get("level") if exl else (fst.get("exam") or {}).get("level")
    lv_cn, lv_cls = EXAM_LV_CN.get(lv, ""), EXAM_LV_CLS.get(lv, "")
    lv_html = f'<span class="exlv {lv_cls}">{lv_cn}</span>' if lv_cn else ""
    # 偏低 = 红字（成绩低是要处理的问题，不是中性信息）；中等/较好用中性字色，避免满屏飘红
    cls = "low" if lv == "偏低" else ""
    html = (f'<div class="cexam {cls}" title="{_html.escape(tip)}">'
            f'<span class="ck">成绩</span><b>{score}</b>'
            + (f'<span class="cp">得分率 {pct}</span>' if pct else "")
            + f'{lv_html}<span class="csrc">{_html.escape(meta)}</span></div>')
    return html, True


# ===================== v4.27 加课 / 电话沟通（学生信息上方单独一条） =====================
XSUM_KEEP = 40          # 单条摘要保留字数（超出折叠，不硬截断丢信息）

def _xsum_split(text):
    """自由文本 → 条目列表。团队写法基本是**一行一条**；整段不分行的按一条处理。"""
    t = str(text or "").replace("\r", "\n")
    return [x.strip(" 　\t·-—") for x in t.split("\n") if x.strip(" 　\t·-—")]


def _xsum_date(rec):
    """抓条目开头的日期 → (日期, 去掉日期后的正文)。

    实测写法混杂：`10.13加课内容：…`、`9.6 一个小时，解三角形`、`10.2孩子会在群里互动`、
    `1016晚7点，内容是…` —— 前两种带分隔符，第三种是纯数字紧跟文字（月+日各两位）。
    ⚠️ 纯数字分支必须卡 1~12 / 1~31，否则「2026 年」这类会被当日期。
    """
    m = re.match(r"^\s*(\d{1,2})\s*[.\-/月]\s*(\d{1,2})\s*日?\s*", rec)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= 12 and 1 <= b <= 31:
            return f"{a}.{b}", rec[m.end():].strip(" ：:，,、")
    m = re.match(r"^\s*(\d{2})(\d{2})(?!\d)\s*", rec)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= 12 and 1 <= b <= 31:
            return f"{a}.{b}", rec[m.end():].strip(" ：:，,、")
    return "", rec


def _xsum_brief(txt, keep=XSUM_KEEP):
    t = re.sub(r"\s+", " ", str(txt or "")).strip()
    return t if len(t) <= keep else t[:keep].rstrip(" ，,、；;") + "…"


def extra_has_record(fs):
    """加课列里是否有**带日期的真实加课记录**（「需要 / 没学 / 不需要」这类意向不算）。

    用途：预警卡不能用「无学习痕迹」给一个表里明明写着「10.9 加课 直线与圆」的学生定性。
    """
    if not isinstance(fs, dict):
        return False
    recs = _xsum_split((fs.get("meta") or {}).get("extraClass"))
    return any(_xsum_date(r)[0] for r in recs)


def extra_line(fs, as_warn_row=False):
    """（v4.27）加课 / 电话沟通 → 卡片「学生情况」**上方**的一条汇总记录。

    用户要求：飞书表里这两类列（部分 sheet 有）不要散落在备注里，要在学生情况上方
    单独一条、把「加过几次课 / 电话沟通说了什么」一眼交代清楚。

    数据来源（均为团队手填的自由文本）：
      · 加课：`加课时间，内容` / `加课时间内容` / `数列加课` / 宽表「课程服务·加课」子列
      · 电话：`电话沟通情况` / `10月沟通结果`

    ⚠️ 口径：**有日期的条目才算「一次加课」**（「需要 / 没学 / 不需要」这类是加课意向或状态，
       不计数，原样展示）；电话取最近一条做摘要，其余折叠。
    🔒 只复述表里写了什么，不推断效果（不写「加课后明显进步」这类无证据结论）。
    返回空串 = 该生两类都没有，整条不渲染。
    """
    if not isinstance(fs, dict):
        return ""
    m = fs.get("meta") or {}
    xc = _xsum_split(m.get("extraClass"))
    pc = _xsum_split(m.get("phoneComm"))
    if not xc and not pc:
        return ""

    rows, details = [], []
    n_folded = [0]        # 折进「全部记录」的条数（用于 summary 文案）

    def build(label, recs, cls):
        dated, plain = [], []
        for r in recs:
            d, rest = _xsum_date(r)
            # 团队常把「加课内容：」当天花板写上（`10.13加课内容：立体几何`）→ 摘要里去掉这层冗余
            rest = re.sub(r"^(加课内容|加课|沟通内容)[：:]\s*", "", rest or "").strip()
            (dated if d else plain).append((d, rest, r))
        dated.sort(key=lambda x: tuple(int(p) for p in x[0].split(".")))
        if dated:
            d, rest, raw = dated[-1]
            many = len(dated) > 1
            cnt = f"共 {len(dated)} 次　" if many else ""
            head = f"{cnt}{'最近 ' if many else ''}{d}" + (f"：{_xsum_brief(rest)}" if rest else "")
            tip = raw
        else:
            many = len(plain) > 1
            cnt = f"共 {len(plain)} 条　" if many else ""
            head = cnt + _xsum_brief(plain[0][2], 14)    # 无日期：如实复述原文，不贴结论
            tip = plain[0][2]
        if len(recs) == 1 and recs[0] == "[图片]":
            head = "📷 表内是截图，需回表查看"
        row = (f'<span class="xrow"><b class="xk {cls}">{label}</b>'
               f'<span class="xt" title="{_html.escape(tip)}">{_html.escape(head)}</span></span>')
        # 折叠：有条目超过摘要长度 / 不止一条 / 有额外的意向类条目 → 给「全部记录」
        need = (len(recs) > 1 or any(len(x) > XSUM_KEEP for x in recs)
                or (dated and plain))
        if need:
            lis = []
            for d, rest, raw in dated:
                lis.append(f'<div><b>{_html.escape(d)}</b> {_html.escape(rest or raw)}</div>')
            for _d, _r, raw in plain:
                lis.append(f'<div>{_html.escape(raw)}</div>')
            details.append(f'<div class="xallgrp"><b class="xgl {cls}">{label} 全部 {len(recs)} 条</b>'
                           + "".join(lis) + "</div>")
            n_folded[0] += len(recs)
        return row

    if xc:
        rows.append(build("加课", xc, "xc"))
    if pc:
        rows.append(build("电话", pc, "pc"))
    body = "".join(rows)
    if details:
        # ⚠️ 这里**绝不能**加 open：用户明确要求「全部记录」默认收起，点开才看。
        body += (f'<details class="xdet"><summary>展开全部记录（共 {n_folded[0]} 条）</summary>'
                 f'<div class="xall">{"".join(details)}</div></details>')
    if as_warn_row:
        return (f'<div class="warnrow wir"><span class="warnk">加课/沟通</span>'
                f'<span class="warnv xw">{body}</span></div>')
    return f'<div class="kv xsum"><span class="k">加课 · 沟通</span><span class="v">{body}</span></div>'


def fs_parts(st, exam_shown=False):
    """飞书表记录 → **拆散后**可直接嵌进 ① 学习情况 / ② 配合度 的片段（v4.23）。

    背景（用户要求）：飞书表原来在卡片里是一个**独立折叠小模块**，与学情分析并列；
    但「作业交没交、评级在升还是在降、考了多少分」本身就是学习情况，应该参与结论，
    而不是另挂一块让人自己看。所以这里不再返回一个大块，而是返回 6 个可分别插入
    不同位置的片段（缺项为空串）：

      risk   需要关注的点（最多 3 条，来自 common.fs_stats().risks）→ ① 顶部
      items  各项提交情况（完成/总 ＋ 百分比）→ ① 薄弱知识点之后
      track  作业评级逐课轨迹（仅声明了 ratings 的团队才有）→ ① 优势之后
      note   老师手记（团队手填原文，已转义）→ ① 优势之后
      extra  续报 / 星星榜 / 新生 / 沟通优先级 → ② 配合度末尾（学校已移到卡片头部）
      src    来源溯源（表名 ＋ 行号 ＋ 口径）→ 判定依据 details 内

    ⚠️ 完成率口径：**完成 ÷（完成 ＋ 未完成）**；空白 = 该课次未记录（≠未完成），不计入分母。
    🔒 出勤不取自飞书（仍以系统为准），故这里不含出勤。
    """
    empty = {"risk": "", "items": "", "track": "", "note": "", "extra": "", "src": ""}
    if not st:
        return empty

    def esc(v):
        return _html.escape(str(v if v is not None else ""))

    out = dict(empty)

    # ---- 需要关注的点（这是「融合」的核心：直接说出哪里有问题） ----
    # ⚠️ 成绩那一条在 exam_shown=True 时已被 ① 顶部的「成绩」行占用，这里不再重复同一句
    _risks = [(s, t) for s, t in (st.get("risks") or [])
              if not (exam_shown and t.startswith("考试"))]
    if _risks:
        _sev = _risks[0][0]
        lis = "".join(f'<span class="fsrk">{esc(t)}</span>' for _, t in _risks)
        out["risk"] = (f'<div class="fsrisk sev{_sev}"><b>⚠️ 需要关注</b>{lis}</div>')

    # ---- 作业评级轨迹（有等级列的团队） ----
    # ⭐ v4.26：「评级趋势」与「提交情况」**放在一起**（用户要求）—— 两者说的是同一件事：
    #    作业交没交、交上来的质量怎么样。原来轨迹挂在「优势」之后，跟提交 chip 中间隔了整段
    #    「具体薄弱点」，看的人得自己把两处对起来。现在并进同一个 kv：
    #    标签「提交情况」→ 各项 chip → 评级序列条（同一缩进，天然成组）。
    _rb, _ = rating_block(st.get("ratingsRaw") or {})

    # ---- 各项提交情况 ----
    if st.get("items"):
        chips = []
        for i in st["items"]:
            pct = i["pct"]
            cls = "ok" if pct >= 90 else ("warn" if pct >= 70 else "bad")
            chips.append(f'<span class="fsitem {cls}" '
                         f'title="{esc(i["item"])}：完成 {i["done"]} 次 · 未完成 {i["undone"]} 次">'
                         f'{esc(i["item"])}<b>{i["done"]}/{i["total"]}</b><i>{pct:.0f}%</i></span>')
        _from = f'飞书表 · {st["nLessons"]} 次课' if st.get("nLessons") else "飞书表"
        out["items"] = (f'<div class="kv fsline"><span class="k">提交情况</span><span class="v">'
                        f'<span class="fsfrom">{esc(_from)}　合计 <b>{st["D"]}/{st["D"] + st["U"]}</b>'
                        f'（{st["pct"]:.0f}%）</span>'
                        f'<div class="fsitems">{"".join(chips)}</div>'
                        f'{_rb}</span></div>')
    elif _rb:
        # 兜底：完成项计数全为空、但评级列有记录（如全「未记录」却写了等级）
        out["items"] = (f'<div class="kv fsline"><span class="k">作业评级</span>'
                        f'<span class="v">{_rb}</span></div>')
    out["track"] = ""            # 已并入「提交情况」，不再单独占位（保留键以兼容调用方）

    # ---- 老师手记 ----
    # v4.37：手记里的**问题句已提到卡片可见区**（issue_row，红/橙 chip）——
    #   若整段手记基本就是那几句，折叠区再整段复述一遍纯属重复（用户反复提过这条）。
    #   只有手记里还剩「问题句之外的内容」（正面评价 / 背景 / 教务事务）时才渲染全文。
    if st.get("note"):
        _n = str(st["note"]).strip()
        _shown = sum(len(str(x[1])) for x in (st.get("issues") or [])[:2])
        if len(_n) > _shown + 12:
            out["note"] = (f'<div class="fsnote"><b>老师手记</b>'
                           f'{esc(_n).replace(chr(10), "<br>")}</div>')

    # ---- 背景信息（续报 / 星星榜 / 新生 / 沟通优先级） ----
    # ⭐ v4.25：**学校已提到卡片头部姓名下方**（school_line），此处不再重复列出 ——
    #    它属于「这个学生是谁」的身份信息，不是学习情况里的补充证据。
    m = st.get("metaRaw") or {}
    _bits = []
    for lab, key, tail in [("续报", "renew", ""), ("续报等级", "renewLevel", ""),
                           ("星星榜", "stars", ""),
                           ("新生", "isNew", ""), ("沟通优先级", "priority", "")]:
        _v = m.get(key)
        if not _v:
            continue
        # v4.34：「新生：否」是**无信息**的一行（不是新生不用写），只在真为新生时显示
        if key == "isNew" and str(_v).strip() in ("否", "False", "false", "0", "N", "n"):
            continue
        _bits.append(f'{lab}：{esc(_v)}')
    if _bits:
        out["extra"] = f'<div class="fsmeta">{"　｜　".join(_bits)}</div>'

    # ---- 溯源 ----
    out["src"] = (f'<div class="fsmeta">飞书表来源：「{esc(st.get("sheet") or "—")}」'
                  f'第 {esc(st.get("row") or "—")} 行　·　'
                  f'口径：完成 ÷（完成 ＋ 未完成），空白 = 该课次未记录、不计入　·　'
                  f'出勤仍以系统为准</div>')
    return out


def fs_exam_row(st):
    """飞书表里的考试成绩 → 「成绩」行（**v4.36 起已停用**，由 `exam_head()` 在卡头渲染）。

    ⚠️ 保留仅为留档：成绩已移到卡头（学校名下面），这里再渲染一次就会出现两个分数。
    """
    ex = (st or {}).get("exam")
    if not ex:
        return ""
    cls = {"高": "ok", "中": "warn", "偏低": "bad"}.get(ex.get("level"), "")
    cn = {"高": "较好", "中": "中等", "偏低": "偏低"}.get(ex.get("level"), "未知")
    p = f'（得分率 {ex["pct"]:.0%}{"·" + ex["assumed"] if ex.get("assumed") else ""}）' \
        if ex.get("pct") is not None else ""
    return (f'<div class="kv"><span class="k">成绩</span><span class="v">'
            f'考试 {_html.escape(str(ex["text"]))}{p} <span class="exlv {cls}">{cn}</span>'
            f'<span class="basis">«飞书表「{_html.escape(str(st.get("sheet") or ""))}」'
            f'第 {st.get("row")} 行»</span></span></div>')


def fs_focus(st):
    """飞书表 → 联席可执行动作。返回 (优先级, 动作, 依据) 或 None。

    与 `board_focus()` 同一定位：把「表格里的一个 ✘」翻译成「今天该做什么」。
    优先级口径：**最近连续未交**最急（人正在掉队）＞ 某项全未交 ＞ 完成率过低 ＞ 评级下滑 ＞ 考试偏低。
    """
    if not st:
        return None
    for g in st.get("ratings") or []:
        ta = int(g.get("tailAbsent") or 0)
        if ta >= 2:
            return ("高",
                    f'{g["item"]} 最近连续 {ta} 次未交：今天单独确认是漏做还是不会，并把最近一次补上',
                    f'飞书表：{g["item"]} 末 {ta} 次标记「未提交」')
    for i in st.get("zeros") or []:
        return ("中", f'{i["item"]} {i["total"]} 次全部未交：逐次点开看是没做还是没交，本周内补齐',
                f'飞书表：{i["item"]} 0/{i["total"]}')
    w = st.get("worst")
    if w and w["pct"] <= 50 and w["total"] >= 2:
        return ("中", f'{w["item"]}未完成 {w["undone"]}/{w["total"]}：把缺的 {w["undone"]} 次列成清单，逐次补',
                f'飞书表：{w["item"]} {w["done"]}/{w["total"]}（{w["pct"]:.0f}%）')
    for g in st.get("ratings") or []:
        if (int(g.get("tailAbsent") or 0) < 2 and g.get("trend") == "down"
                and int(g.get("nRated") or 0) >= int(g.get("minRated") or 3)):
            return ("中",
                    f'{g["item"]} 从 {g.get("first")} 降到 {g.get("last")}：聊一次卡在哪'
                    f'（时间不够 / 不会做 / 不想写）',
                    f'飞书表：{g["item"]} 评级 {g.get("first")} → {g.get("last")}'
                    f'（均分 {g.get("early")} → {g.get("late")}）')
    ex = st.get("exam")
    if ex and ex.get("level") == "偏低":
        return ("中", f'考试 {ex["text"]} 偏低：把错题按知识点归一次类，先补丢分最集中的一块',
                f'飞书表：考试成绩 {ex["text"]}')
    return None


# ══════════════════════════════════════════════════════════════════════════
# v4.34 卡片精简：「一眼 + 两折」
# ──────────────────────────────────────────────────────────────────────────
# 用户原话：「每个学生情况分析的内容太长了，信息太多，无法快速获取到有效信息」。
# 原来按「① 学习情况 / ② 配合度 / ③ To-Do」三块并列，读者要跨三块自己拼结论。
#
# 现在：
#   一眼 = 诊断句 + 四格指标 + 最该做/其次
#   两折 = 补充信息（指标格读不出来的）/ 其余跟进建议（第 3 条起）
#
# 三条口径（用户逐条拍板，2026-10-20）：
#   ① 诊断句**只做事实组合**，不做判断、不加形容词（「这孩子可能在掉队」这类不写）；
#   ② 「其次」固定可见 —— **不折叠**，只有第 3 条起折；
#   ③ 购「回放」课但确有节没看 → **要提示**（买回放 ≠ 可以不看）。
# ══════════════════════════════════════════════════════════════════════════
def att_view(r):
    """按**购课类型**判出勤 —— 返回 (指标格文字, 状态类, hover 说明, 问题句, 严重度)。

    ⚠️ 同一份出勤数据，买回放和买直播要读出完全不同的意思（用户指出的误判）：
      · 购「回放」：看回放**就是**正常完成，不许写「全靠回放补」（某生原来就被这么标）；
        但**确有节没看要说**（买了没看 ≠ 买回放所以只能看回放）。
      · 购「直播」：**看直播和看回放都算出勤**，指标格一律写有效出勤（含回放），
        不能写纯到课数（v4.38 修：某生 2 节到课 + 2 节回放曾被显示成「到课 2/5」）；
        只有「既没到课、也没看回放」才提。
    问题句为空 = 按购课类型属于正常，不上屏。
    """
    s = r["s"]
    a = r.get("att") or {}
    buy = (s.get("buy_type") or "").strip()
    if not a or not a.get("lessonCount"):
        return ("无记录", "mute", "后台无该生出勤记录", "", 0)
    n, eff, att, pb_only, pb, absent, no_pb = common.att_stats(a)

    if buy == "回放":
        if absent == 0:
            return (f"回放 {eff}/{n}", "ok",
                    f"购回放课 · 已看 {eff}/{n} 节（看回放即完成）", "", 0)
        return (f"回放 {eff}/{n}", "warn",
                f"购回放课 · {absent} 节回放未看", f"回放还有 {absent} 节没看", 1)

    # ── 购「直播」：v4.38 起**看直播、看回放都算出勤**（用户 2026-10-20 口径）──
    #   ⚠️ 这里必须用 eff（有效出勤 = 到课 + 仅回放补齐），不能用 att（纯到课）。
    #   实测某生：直播到 2 节 + 回放 2 节，原来显示「到课 2/5」→ 被读成「5 节只来了 2 节」，
    #   与出勤块「出勤 4/5 次（含回放 2 次）」自相矛盾。买直播的学生在家看回放 =
    #   这节课上了，只是没在线；**只有既没到课、也没看回放**才算真缺课（absent）。
    _mix = []
    if att:
        _mix.append(f"直播 {att} 节")
    if eff - att > 0:
        _mix.append(f"回放 {eff - att} 节")
    _t = f"购直播课 · {' ＋ '.join(_mix) or '0 节'}" + f"（看直播/看回放均算出勤）"
    if eff == 0:
        return (f"未到 0/{n}", "bad", f"购直播课 · {n} 节既没到课、也没看回放",
                f"{n} 节既没到课，也没看回放", 2)
    if absent == 0:
        # 出勤已齐 → 不论到没到过直播，都算完成。全看回放是事实，写在 hover 里，不上屏、不扣分。
        _t = (f"购直播课 · {n} 节全部看回放，未到过直播（看回放算出勤）"
              if att == 0 else _t)
        return (f"到课 {eff}/{n}", "ok", _t, "", 0)
    # 真缺课（既没到、也没看回放）——1 节轻提示，≥2 节按硬问题
    _t += f"，另有 {absent} 节既没到课、也没看回放"
    # 问题句：缺 1 节时**给节次**（「第 5 节没看」），比「5 节里 1 节没看」短、且能直接定位；
    #   节次来自后台 classSeq，没有就退回计数表述。
    if absent == 1 and no_pb:
        _iss_t = f"第 {no_pb[0]} 节没看"
    else:
        _iss_t = f"{n} 节里 {absent} 节没看（未到课、也没看回放）"
    return (f"到课 {eff}/{n}", "warn" if absent == 1 else "bad", _t,
            _iss_t, 2 if absent >= 2 else 1)


def diag_line(r, fst, lv_key):
    """一眼诊断句：2~3 个短句，**全部来自已有事实**，不推断、不加形容词。

    取材优先级：指标格里**读不出来**的（已掌握 / 主动提问 / 家长配合 / 直播却没到）优先，
    纯数字（作业 9/10、全勤 5/5）只做兜底 —— 否则一句话和四个格子说同一件事。
    负档「先说问题，再说反差」；正常档「先说状态，再点隐患」。
    """
    s = r["s"]
    n_stu, gap, q = s.get("n_stu_sub", 0), s.get("gap_days"), s.get("q", 0)
    mst = list((s.get("topic_mastered") or {}).keys())
    sig = s.get("topic_signals") or {}
    _av, _ac, _at, att_iss, att_sev = att_view(r)

    bad, good, fallback = [], [], []

    # ① 作业（措辞一律中性：只陈述「几项里交了几项」，不写「只交了」这种带评价的说法）
    if fst and (fst.get("D") + fst.get("U")):
        D, T = fst["D"], fst["D"] + fst["U"]
        if D == 0:
            bad.append(f"作业 {T} 项一次没交")
        elif D / T < 0.6:
            bad.append(f"作业 {T} 项里交了 {D} 项")
        elif D / T >= 0.85:
            fallback.append(f"作业 {D}/{T} 交齐")
        else:
            fallback.append(f"作业 {D}/{T}")

    # ② 出勤**重**问题（直播没到 / 全看回放）紧跟作业；轻问题（回放漏看）排到最后
    if att_iss and att_sev >= 2:
        bad.append(att_iss)

    # ③ 群内：「N 天没说话」与「只发过 N 条」是同一件事 → 合并成一句（原来会出两条重复）
    _g = round(gap) if gap is not None else None
    if n_stu <= 0:
        _t = "群里一次没发过言"
        if q == 0:
            _t += "，也没主动提问"
        bad.append(_t)
    elif _g is not None and _g >= 14:
        if n_stu <= 2:
            _t = f"群里 {_g} 天没说话，本人一共发过 {n_stu} 条"
            if q == 0:
                _t += "、一次没主动提问"
        else:
            _t = f"群里 {_g} 天没说话"
        bad.append(_t)
    elif n_stu <= 2:
        _t = f"本人在群里发过 {n_stu} 条"
        if q == 0:
            _t += "、一次没主动提问"
        bad.append(_t)

    if att_iss and att_sev < 2:
        bad.append(att_iss)

    # ④ 正面事实：主动提问的信息量高于「发言多」→ 排前
    if q >= 8:
        good.append(f"主动提问 {q} 处")
    if n_stu >= 20:
        good.append(f"群里很活跃（{n_stu} 条）")
    if (s.get("n_other") or 0) >= 8:
        good.append(f"家长也配合（{s['n_other']} 条）")
    # ⑤ 知识点闭环是四张卡里唯一带「结果」的信息 → 放最前
    for t in mst:
        good.insert(0, f"「{t}」已自己搞懂")
    # ⚠️「薄弱点集中在 X」**不进诊断句**：一是它是负面信息、混在正面位里会被读成结论；
    #    二是「只能说有这回事、不能定性」的那批知识点已经全部列在折叠区。诊断句只讲行为与状态。

    if lv_key in ("red", "orange"):
        parts = (bad[:2] + ["不过" + good[0]]) if (good and bad) else (bad[:3] or good[:2])
    else:
        base = good[:2] or fallback[:2]
        if bad and base:
            parts = ["、".join(base), "但" + bad[0]]
        else:
            parts = base or (["但" + bad[0]] if bad else [])
    return "；".join(parts) if parts else "窗口内证据不足，无法判断"


def metrics_grid(r, fst):
    """四格指标：作业提交 / 出勤 / 群内发言 / 最近活跃 —— 原来散在四个不同位置。"""
    s = r["s"]
    n_stu, gap = s.get("n_stu_sub", 0), s.get("gap_days")
    out = []
    if fst and (fst.get("D") + fst.get("U")):
        D, T = fst["D"], fst["D"] + fst["U"]
        p = D / T
        out.append(("作业提交", f"{D}/{T}",
                    "ok" if p >= 0.85 else ("warn" if p >= 0.6 else "bad"),
                    f"飞书表共 {T} 项，完成 ÷（完成＋未完成）；空白 = 未记录、不计入"))
    else:
        out.append(("作业提交", "无记录", "mute", "飞书表无该生记录"))
    _v, _c, _t, _iss, _sev = att_view(r)
    out.append(("出勤", _v, _c, _t))
    out.append(("群内发言", f"{n_stu} 条",
                "ok" if n_stu >= 20 else ("mute" if n_stu >= 5 else ("bad" if n_stu <= 1 else "warn")),
                "学生本人实质发言条数（不含礼节性回应）"))
    if gap is None or n_stu <= 0:
        out.append(("最近活跃", "无发言", "mute",
                    "窗口内本人/家长都没有实质发言" if n_stu <= 0 else ""))
    else:
        g = round(gap)
        out.append(("最近活跃", "今天" if g <= 0 else f"{g} 天前",
                    "ok" if g <= 2 else ("mute" if g <= 7 else ("warn" if g <= 14 else "bad")),
                    "最后一次本人发言距今天数"))
    return "".join(f'<div class="mg"><span class="mgk">{k}</span>'
                   f'<span class="mgv {c}" title="{_html.escape(t or "")}">{v}</span></div>'
                   for k, v, c, t in out)


def _drop_fsrate(html):
    """删掉「一次有效评级都没有」的评级趋势块 —— 此时序列条只剩四个「未」，
    而作业分项里的 0/4 已经把同一件事说清楚，留着纯属占位。"""
    if not html or "一次有效评级都没有" not in html:
        return html
    m = re.search(r'<div class="fsrate">', html)
    if not m:
        return html
    i, d = m.start(), 0
    for mm in re.finditer(r"<div\b|</div>", html[i:]):
        if mm.group(0).startswith("</"):
            d -= 1
            if d == 0:
                return html[:i] + html[i + mm.end():]
        else:
            d += 1
    return html


def act_brief(todo):
    """动作上屏：剥掉括号里的依据（改 hover），**保留冒号前的现象描述** ——
    删了会让后半句变成没头没尾的指令（「针对性辅导或订正跟进」不知道在说哪件事）。
    返回 (上屏句, 完整原文)。"""
    p, a, b = todo
    return re.sub(r"（[^）]*）", "", a).strip(), a


# ══════════════════════════════════════════════════════════════════════════
# v4.39 教务视角：在读科目 / 是否续报 / 扩科意向（数据来自 snapshot/enroll.json
#   与扫 snapshot/chats.json 的扩科意向识别）—— 仅在 edu 维度展示。
#   ⚠️ 反编造：扩科只认**学生/家长本人**发言（排除发言名含「教务/老师/主讲/顾问」
#   的招生文案），且剔除「英语老师/英语作业」这类非扩科语境；命中附原文供人工核对。
#   续报字段（v4.44 起为真实数据）：来源 `getCoTeacherStudents` 的 `renewed`（"是"/"否"/"未设置"），
#   按 studentSeq 写入 enroll.json 顶层 `renew`；"未设置"＝接口已返回但学生尚未在后台选择。
#   v4.46：卡头 `.cjw` **有内容才渲染** —— 续报只有「是/否」才露面（"未设置"/接口未返回整组隐藏），
#   扩科无意向、在读科目为空同样隐藏；续报「未确认」仍计入 Tab② KPI 与明细表 data-r="wait"。
# ══════════════════════════════════════════════════════════════════════════
_WD = ""                      # 主流程设置：当前 workdir
_ENROLL_CACHE = {}            # {wd: (by_name, renew_by_name)}
_KUAKE_CACHE = {}             # {wd: kuake}

_KUAKE_SUBJ = ["物理", "化学", "英语", "语文", "生物", "历史", "地理", "政治", "科学"]
_KUAKE_EXCLUDE_SPK = ("教务", "老师", "主讲", "顾问", "客服")
_KUAKE_NOISE = ("老师", "作业", "试卷", "考试", "课代表", "竞赛", "课本", "辅导")
# v4.39 收口：命中「其他科目」后，须在 ±窗口内出现下列「扩科意图词」才记为扩科意向，
# 过滤掉 ambient 提及（如「学校有物理活动」「跟物理打个平手」）。过泛词（考虑/准备/计划）易误中数学推理，已剔除。
_KUAKE_INTENT = ("报名", "想报", "想学", "想上", "想补", "想加", "想了解", "想跟", "想让",
                "咨询", "问问", "问下", "问一", "了解", "打算",
                "报个", "报课", "报班", "报一", "续报", "续费",
                "加个", "加一下", "加科", "扩科", "加课",
                "试听", "约个", "约一下", "学一下", "学一学",
                "补习", "补一", "补补", "补下", "恶补")
# 营销/招生护栏：命中科目词周围出现这些词（教务发的「热报名中/🔥/名额」等），一律不算本人扩科意向。
_KUAKE_MKT = ("热报名", "报名中", "早鸟", "名额", "优惠", "🔥", "抢报", "招生", "暑秋")
# 「加课/报课」类指名某节课的词：若与科目相距 >8 字，多半是别科的课（如「导数的加课…和化学冲突」），须跳过。
_KUAKE_CLASS_BOUND = ("加课", "加一下", "加个", "加科", "报课", "报班", "报个")
_RENEW_YES = ("已续报", "是", "y", "Y", "1", "续报", "续费", "已续费")


def _detect_kuake(wd):
    """扫 snapshot/chats.json，识别每个学生本人提及的其他科目（扩科意向）。"""
    gp = os.path.join(wd, "snapshot", "groups.json")
    cp = os.path.join(wd, "snapshot", "chats.json")
    if not (os.path.exists(gp) and os.path.exists(cp)):
        return {}
    groups = json.load(open(gp, encoding="utf-8"))
    chats = json.load(open(cp, encoding="utf-8"))
    room2stu = {}
    for g in groups:
        rid = g.get("roomId")
        nm = common.norm_name(g.get("studentNameNorm") or g.get("studentName") or "")
        sj = g.get("subjectName") or ""
        if rid and nm:
            room2stu[rid] = (nm, sj)
    out = {}
    for rid, vv in chats.items():
        info = room2stu.get(rid)
        if not info:
            continue
        stu, cur = info
        for m in vv.get("messages", []):
            sn = m.get("speakerName") or ""
            if any(x in sn for x in _KUAKE_EXCLUDE_SPK):
                continue            # 招生 / 老师文案不算学生/家长本人意向
            t = m.get("text") or ""
            for sj in _KUAKE_SUBJ:
                if sj == cur:
                    continue
                if sj not in t:
                    continue
                if re.search(rf"{sj}(?:{'|'.join(_KUAKE_NOISE)})", t):
                    continue        # 「英语老师/英语作业」这类非扩科语境，剔除
                _pos = t.find(sj)
                # 营销/招生护栏：命中科目词周围出现招生话术，非本人意向
                _wm = t[max(0, _pos - 40):_pos + len(sj) + 40]
                if any(mk in _wm for mk in _KUAKE_MKT):
                    continue
                # v4.39 收口：命中科目词后，须在 ±25 字窗口内出现「扩科意图词」才记
                _lo, _hi = max(0, _pos - 25), min(len(t), _pos + len(sj) + 25)
                _win = t[_lo:_hi]
                _hit = None
                for it in _KUAKE_INTENT:
                    if it in _win:
                        _hit = it
                        break
                if not _hit:
                    continue
                # 「加课/报课」类须紧贴科目（≤8 字），否则多半指别科的课
                if _hit in _KUAKE_CLASS_BOUND and abs((_lo + _win.find(_hit)) - _pos) > 8:
                    continue
                recs = out.setdefault(stu, [])
                if not any(r["subject"] == sj for r in recs):
                    # 截一段「命中科目词」周围的上下文作证据，保证悬停可核验
                    _a = max(0, _pos - 25)
                    _b = min(len(t), _pos + len(sj) + 25)
                    _snip = t[_a:_b].replace("\n", " ").strip()
                    if _a > 0:
                        _snip = "…" + _snip
                    if _b < len(t):
                        _snip = _snip + "…"
                    recs.append({"subject": sj, "text": _snip})
                break
    return out


def _load_kuake(wd):
    if wd in _KUAKE_CACHE:
        return _KUAKE_CACHE[wd]
    p = os.path.join(wd, "snapshot", "kuake.json")
    k = None
    if os.path.exists(p):
        try:
            k = json.load(open(p, encoding="utf-8")).get("kuake")
        except Exception:
            k = None
    if not k:
        k = _detect_kuake(wd)
        try:
            json.dump({"kuake": k}, open(p, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except Exception:
            pass
    _KUAKE_CACHE[wd] = k or {}
    return _KUAKE_CACHE[wd]


def _load_enroll(wd):
    if wd in _ENROLL_CACHE:
        return _ENROLL_CACHE[wd]
    by_name, renew_by_name = {}, {}
    p = os.path.join(wd, "snapshot", "enroll.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        by_seq = d.get("bySeq") or {}
        seq2name = {}
        for seq, rec in by_seq.items():
            nm = common.norm_name(rec.get("name") or "")
            if nm:
                if nm not in by_name:
                    by_name[nm] = rec
                seq2name[str(seq)] = nm
        renew = d.get("renew") or {}
        for seq, st in renew.items():
            nm = seq2name.get(str(seq))
            if nm and nm not in renew_by_name:
                renew_by_name[nm] = st
    _ENROLL_CACHE[wd] = (by_name, renew_by_name)
    return _ENROLL_CACHE[wd]


def _cjw_line(r, edu):
    """教务卡头身份行：在读科目 + 续报 + 扩科意向（仅 edu 维度）。

    v4.46（用户口径）：**没有实质内容的分组直接不渲染**，不再用灰色「—」/橙色「未确认」占位——
      · 在读科目为空        → 整组隐藏
      · 续报为「未确认」    → 整组隐藏（未设置 / 接口未返回 / 空串都算未确认）
      · 扩科意向无命中      → 整组隐藏
      三组都无内容时整行返回空串，卡头不留空壳。
      注：续报「未确认」并非信息丢失——Tab② 顶部 KPI 仍统计「续报未确认」，明细表也保留该列。
    """
    if not edu:
        return ""
    nm = common.norm_name(r["stu"])
    enr_by_name, renew_by_name = _load_enroll(_WD)
    enr = enr_by_name.get(nm) or {}
    subjects = enr.get("subjects") or []
    renew = renew_by_name.get(nm)
    kuake = _load_kuake(_WD).get(nm, [])

    parts = []
    if subjects:
        parts.append('<span class="jwlab">在读科目</span>'
                     + "".join(f'<span class="subjtag">{s}</span>' for s in subjects))

    rv = "" if renew is None else str(renew).strip()
    if rv and rv != "未设置":
        # 「是」→ 已续报(green)｜「否」→ 未续报(red)；其余（未设置/空/接口未返回）不展示
        _yes = rv in _RENEW_YES
        cls = "ok" if _yes else "no"
        parts.append('<span class="jwlab">续报</span>'
                     f'<span class="jwv {cls}">{"已续报" if _yes else "未续报"}</span>')

    if kuake:
        # v4.42：扩科意向直接带依据（命中词 ±25 字原文截片），不只靠悬停
        chips = "".join(
            f'<span class="kuake-chip" title=\'{_html.escape(k["text"])}\'>{k["subject"]}</span>'
            f'<span class="kuevi" title=\'{_html.escape(k["text"])}\'>{_html.escape(k["text"])}</span>'
            for k in kuake)
        parts.append('<span class="jwlab">扩科意向</span>' + chips)

    if not parts:
        return ""
    return '<div class="cjw">' + "".join(parts) + '</div>'


def edu_overview_block(rows, course_order=None):
    """教务维度「学生情况概览」Tab 顶部整块：续报 / 多科目 / 扩科意向 统计 + 学生级明细。
    v4.40：5 张 KPI 卡可点击，联动下方明细（再点取消）。
    v4.41：课程筛选上移到页面顶部「课程标签条」（全局维度）；本块只跟随全局课程状态过滤。"""
    enr_by_name, renew_by_name = _load_enroll(_WD)
    kuake = _load_kuake(_WD)
    total = len(rows)

    # 课程清单：优先沿用主流程 course_order（与 Tab②③ 课程子标签同序），缺口补在后面
    courses = list(course_order or [])
    for r in rows:
        ck = r.get("course") or "未知课程"
        if ck not in courses:
            courses.append(ck)

    multi = single = renew_yes = renew_no = renew_wait = kuake_n = 0
    # 续报接口未接入（renew 全空）时，已续报/未续报显示「—」而非 0，避免被读成「一个都没续报」
    renew_na = not renew_by_name
    detail = []
    for r in rows:
        nm = common.norm_name(r["stu"])
        ck = r.get("course") or "未知课程"
        enr = enr_by_name.get(nm) or {}
        subs = enr.get("subjects") or []
        if len(subs) > 1:
            multi += 1
        elif len(subs) == 1:
            single += 1
        rv = renew_by_name.get(nm)
        if rv is None:
            renew_wait += 1
            rst = "wait"
        elif str(rv) == "未设置":
            # v4.44：接口已返回但学生未选 → 计入「续报未确认」
            renew_wait += 1
            rst = "wait"
        elif str(rv) in _RENEW_YES:
            renew_yes += 1
            rst = "yes"
        else:
            renew_no += 1
            rst = "no"
        kk = kuake.get(nm, [])
        if kk:
            kuake_n += 1
        subj = "·".join(subs) or "—"
        if rv is None:
            rvh = ('<span class="jwv wait" title="研发「是否续报」接口未返回该生数据，暂无法判断；'
                   '接口到位后此处直接显示 已续报 / 未续报">未确认</span>')
        elif str(rv) == "未设置":
            rvh = ('<span class="jwv wait" title="研发接口已返回该生续报字段，状态为「未设置」：'
                   '学生尚未在后台选择是否续报（真实状态，非接口缺失）">未确认</span>')
        else:
            _yes = str(rv) in _RENEW_YES
            rvh = f'<span class="jwv {"ok" if _yes else "no"}">{"已续报" if _yes else "未续报"}</span>'
        # v4.42：扩科意向 chip ＋ 可见依据（命中词 ±25 字原文截片）
        kh = ("".join(f'<span class="kuake-chip" title=\'{_html.escape(k["text"])}\'>{k["subject"]}</span>'
                      f'<span class="kuevi" title=\'{_html.escape(k["text"])}\'>{_html.escape(k["text"])}</span>'
                      for k in kk) or '<span class="jwv none">—</span>')
        # data-* 供前端筛选：kc=课程 key r=续报态 m=科目数(1多科/0单科/u未知) k=有扩科
        _m = "1" if len(subs) > 1 else ("0" if len(subs) == 1 else "u")
        detail.append(f'<tr data-kc="{_html.escape(ck)}" data-r="{rst}" data-m="{_m}"'
                      f' data-k="{1 if kk else 0}"><td>{_html.escape(nm)}</td><td>{subj}</td>'
                      f'<td>{rvh}</td><td>{kh}</td></tr>')
    tbody = "".join(detail)

    det = (f'<details class="slimdet"><summary>学生级明细（<span id="eduCnt">{total}</span> 人，点击展开）'
           f'</summary><table><thead><tr><th>学生</th><th>在读科目</th><th>续报</th>'
           f'<th>扩科意向</th></tr></thead><tbody id="eduDetailBody">{tbody}</tbody></table></details>')

    _na = "1" if renew_na else "0"
    _yes_txt = "—" if renew_na else str(renew_yes)
    _no_txt = "—" if renew_na else str(renew_no)
    _hint = ("续报字段研发接口尚未接入，「已续报 / 未续报」暂以「—」占位，接口到位后自动点亮；"
             "在读科目与扩科意向为当前实时口径。"
             if renew_na else
             "点击卡片可只看该类学生，再点一次取消；课程在页面顶部切换，本块与明细同步变化")

    js = """
<script>
(function(){
  var K='';
  function trs(){return document.querySelectorAll('#eduDetailBody tr');}
  window.eduKpi=function(k){
    K=(K===k)?'':k;
    var box=document.querySelectorAll('.edu-ov .kpi.kbl');
    for(var i=0;i<box.length;i++){
      box[i].classList.toggle('on', box[i].getAttribute('data-k')===K);
    }
    eduApply();
  };
  // v4.41：课程 = 页面顶部标签（全局状态 window.EDUC，'' = 全部课程）
  window.eduApply=function(){
    var ck=window.EDUC||'';
    var c={yes:0,no:0,wait:0,multi:0,single:0,kuake:0}, shown=0;
    var rs=trs();
    for(var i=0;i<rs.length;i++){
      var d=rs[i].dataset, inC=(!ck||d.kc===ck), ok=inC;
      if(ok&&K){ ok=(K==='multi')?(d.m==='1'):((K==='single')?(d.m==='0'):((K==='kuake')?(d.k==='1'):(d.r===K))); }
      rs[i].style.display=ok?'':'none';
      if(inC){
        if(c[d.r]!==undefined)c[d.r]++;
        if(d.m==='1')c.multi++;
        if(d.m==='0')c.single++;
        if(d.k==='1')c.kuake++;
      }
      if(ok)shown++;
    }
    var ks=['yes','no','wait','multi','single','kuake'];
    for(var j=0;j<ks.length;j++){
      var el=document.getElementById('edun-'+ks[j]);
      if(el&&el.getAttribute('data-na')!=='1')el.textContent=c[ks[j]];
    }
    var cnt=document.getElementById('eduCnt');
    if(cnt)cnt.textContent=shown;
    var det=document.querySelector('.edu-ov details');
    if(det&&(K||ck))det.open=true;
  };
  eduApply();
})();
</script>"""

    return f'''<div class="section edu-ov">
  <div class="edu-hd">
    <h2>📋 教务核心数据（续报 · 在读科目 · 扩科意向）</h2>
    <div class="edu-cnote">课程筛选在页面顶部（全局生效）</div>
  </div>
  <div class="kpis" style="margin-bottom:6px">
    <div class="kpi kbl" data-k="yes" onclick="eduKpi('yes')"><div class="n" id="edun-yes" data-na="{_na}">{_yes_txt}</div><div class="l">已续报</div></div>
    <div class="kpi kbl" data-k="no" onclick="eduKpi('no')"><div class="n" id="edun-no" data-na="{_na}">{_no_txt}</div><div class="l">未续报/其他</div></div>
    <div class="kpi kbl orange" data-k="wait" onclick="eduKpi('wait')" title="研发「是否续报」接口尚未接入，暂无法判断谁已续报，现显示「未确认」；接口到位后自动变为 已续报/未续报"><div class="n" id="edun-wait">{renew_wait}</div><div class="l">续报未确认</div></div>
    <div class="kpi kbl" data-k="multi" onclick="eduKpi('multi')" title="在读 2 个及以上科目（后台「在读」状态）"><div class="n" id="edun-multi">{multi}</div><div class="l">多科目在读</div></div>
    <div class="kpi kbl" data-k="single" onclick="eduKpi('single')" title="当前仅在读 1 个科目（后台「在读」状态；无在读记录的不计）"><div class="n" id="edun-single">{single}</div><div class="l">单科在读</div></div>
    <div class="kpi kbl green" data-k="kuake" onclick="eduKpi('kuake')" title="聊天记录里本人提及其他科目且带扩科意图词（报名/想学/咨询…）；依据见明细表与卡片"><div class="n" id="edun-kuake">{kuake_n}</div><div class="l">有扩科意向</div></div>
  </div>
  <div class="kpihint">{_hint}</div>
  {det}
</div>''' + js


def card_html(r, edu=False, show_co_tag=False, board=None):
    s, g = r["s"], r["g"]
    # v4.19.3 飞书表学习记录：按 (姓名归一, 课程名) 复合键取；取不到则不渲染该块。
    #   先试后台 productName 全名，再试 card 侧的 course_raw（已剥产品前缀）—— 两者都在 FS_MAP 里登记过。
    _fsn = common.norm_name(r["stu"])
    fs = (FS_MAP.get((_fsn, g.get("productName")))
          or FS_MAP.get((_fsn, r.get("course_raw"))))
    # v4.27：加课 / 电话沟通 → 卡片「① 学习情况」上方单独一条（没有这两类列的学生整条不渲染）
    _xsum = extra_line(fs)
    # v4.23：飞书表不再是「独立小模块」，而是拆成片段参与 ① 学习情况 ——
    #   提交情况 / 需要关注的点 / 评级轨迹 / 老师手记 分别插到不同位置（见 fs_parts）。
    fst = common.fs_stats(fs)
    co = g.get("coTeacherName") or "未分配联席"
    co_tag = (f'<span class="ctag">联席：{co}</span>') if (edu or show_co_tag) else ""
    bt = (s.get("buy_type") or "").strip()
    bt_cls = {"直播": "live", "回放": "replay"}.get(bt, "other")
    bt_tag = f'<span class="btag {bt_cls}">{bt}</span>' if bt else ""
    # v4.25 学校 → 姓名下方小字（身份信息，不是学习情况）
    _cschool = school_line(fst or fs)
    # v4.36 近期成绩 → 紧接学校名下面一行（用户要求「放到比较重要明显的位置」）
    _cexam, _exam_shown = exam_head(s, fst)
    # v4.31：薄弱知识点 chip **带原文依据**（悬停即显示「N 条问题信号 ＋ 首条原话」），可回群核对。
    #   ⚠️ 同时**停用 ERR_MAP 固定错因映射** —— 它把「函数」无条件翻成「求导与单调性讨论」，
    #   在还没学导数的高一课上等于凭空编造（业务原话「不知道你是怎么给出来的」）。
    #   错因只在 fine_weaknesses 给出**具体题型**时才写，没有就不写。
    #
    # v4.31.1：改成**双档显示**，不再一刀切。
    #   只保留「≥2 条」会走向另一个极端：六C肖 50 人里只剩 3 人、张巍 57 人里只剩 5 人，
    #   其余全是「—」，读者会误以为「查过、没问题」，而真实情况是「群里只提过一次」。
    #   故：≥2 条 = 实心 chip（可当结论看）；仅 1 条 = 虚线 chip（弱证据，附原文自证）。
    #   ⚠️ 显示名来自 TOPIC_UNITS 的**子单元**（只命中「函数」就写「函数」），
    #   不会再把「函数与导数」这种含未学内容的合并词带出来。
    _tev = s.get("top_topics_evi") or {}
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
    # v4.23：飞书表的高提交率是「作业习惯稳定」的直接证据 → 进「优势」，而不只是躺在数字里
    if fst and fst.get("pct") is not None and fst["pct"] >= 90 and (fst["D"] + fst["U"]) >= 4:
        adv = ((adv + "；" if adv not in ("—", "") else "")
               + f'飞书表各项提交 {fst["D"]}/{fst["D"] + fst["U"]}'
                 f'（{fst["pct"]:.0f}%），作业/笔记完成度高')
    # 薄弱知识点：聊天高频话题 × 小黑板实证 交叉验证（双证据更精准）
    chat_topics = list(s["top_topics"])
    # v4.31.1：**弱证据（仅 1 条）也要进卡**，按条数降序补进来（最多补到 3 个）。
    #   ⚠️ v4.47 用户/联席反馈推翻了这条：「我只是提一嘴它也会分析进去」——
    #   只提过 1 次的知识点**不再进「薄弱点」行**（那是结论位），改为折进「补充信息 →
    #   群内提及」，写清「各 1 次、未到定性门槛」。有 ≥2 条证据的才配当薄弱点。
    _csig = s.get("topic_signals") or {}
    _cand1 = []                                     # 仅 1 条提及的候选（后置过滤）
    if _csig:
        for _t, _v in sorted(_csig.items(), key=lambda kv: (-int(kv[1].get("n") or 1), kv[0])):
            _n = int(_v.get("n") or 1)
            if _n < 2:                              # 单条提及 → 只记，不判
                _cand1.append(_t)
                continue
            if len(chat_topics) >= 3:
                break
            if _t not in chat_topics:
                chat_topics.append(_t)
    merged = list(chat_topics)
    for t in board_err_topics:
        if not any(same_topic(t, x) for x in merged):
            merged.append(t)
    _mst = s.get("topic_mastered") or {}
    # v4.35：已掌握但已从 top_topics 掉出去的知识点也要在可见区列出来 ——
    #   否则「桂悦恒：不等式已自己搞懂」这类卡会什么都看不到（读者以为没查过）。
    for _t in _mst:
        if not any(same_topic(_t, x) for x in merged):
            merged.append(_t)
    # v4.35：同一知识点两种叫法（`空间向量` / `立体几何/空间向量`）合并为一枚 ——
    #   保留**更短的子单元名**（`空间向量`），避免卡上同时出现「已掌握」与「小黑板薄弱」。
    _uniq = []
    for t in merged:
        _hit = next((i for i, u in enumerate(_uniq) if same_topic(u, t)), None)
        if _hit is None:
            _uniq.append(t)
        elif len(topic_unit_key(t)) < len(topic_unit_key(_uniq[_hit])):
            _uniq[_hit] = t
    merged = _uniq
    # v4.47：仅 1 条提及、且没被小黑板实证或已掌握覆盖的知识点 → 折进「补充信息 → 群内提及」，
    #   不进「薄弱点」行（不必和 ≥2 条证据的知识点抢结论位）。
    _weak1 = [t for t in _cand1 if not any(same_topic(t, x) for x in merged)]
    weak_chips = ""
    # v4.32：已掌握闭环（学生问过 → 老师讲过 → 学生/老师确认）—— 只在**纯聊天侧**的 chip 上标；
    #   小黑板有实证错误（dual / kb_only）说明还没掌握，不标（那两个分支在上面已排除）。
    _cmap = {}
    for t in merged:
        dual = any(same_topic(t, x) for x in chat_topics) and any(same_topic(t, x) for x in board_err_topics)
        kb_only = any(same_topic(t, x) for x in board_err_topics) and not any(same_topic(t, x) for x in chat_topics)
        # v4.31：聊天侧 chip 悬停可看「几条问题信号 + 首条原话」，避免只有结论没有出处
        _e = _tev.get(t) or pick_topic(_tev, t) or _csig.get(t) or pick_topic(_csig, t) or {}
        _n = int((_csig.get(t) or pick_topic(_csig, t) or {}).get("n") or _e.get("n") or 0)
        _ctip = (f'{t}：群内 {_n or "?"} 条问题信号'
                 + (f'，如 {_e.get("at", "")}「{str(_e["text"])[:40]}」' if _e.get("text") else "")
                 if (_e or _n) else "")
        # v4.31.1 双档：≥2 条 = 实心（可当结论看）；仅 1 条 = 虚线（弱证据，只能说明提过）
        _weak = (not dual) and (not kb_only) and 0 < _n < _CARD_MIN_SIG
        _mv = (not dual) and (not kb_only) and (pick_topic(_mst, t) or None)
        if dual:
            _one = (f'<span class="chip dual" title="{_html.escape("聊天问题信号 ＋ 小黑板作答实证 ｜ " + _ctip)}">'
                    f'🖍 {_html.escape(t)}</span>')
        elif kb_only:
            _one = (f'<span class="chip kb" title="小黑板作答实证薄弱（聊天未提及）">'
                    f'🖍 {_html.escape(t)}</span>')
        elif _mv:
            # 已掌握：灰底 + ✓ 角标，视觉上明确「这条已闭环、不用再盯」；
            # hover 同时给「问的原文」和「闭环的原文」，可回群逐字核对（不做任何推断）。
            _mtip = (f'{t}：已掌握 —— {_mv.get("at", "")} {_mv.get("who", "")}'
                     f'「{str(_mv.get("text") or "")[:44]}」')
            if _ctip:
                _mtip += f'｜原始问题：{_ctip}'
            _one = (f'<span class="chip mst" title="{_html.escape(_mtip)}">'
                    f'{_html.escape(t)}<i class="msttag">已掌握</i></span>')
        elif _weak:
            _wtip = f'{t}：群内仅 1 条问题信号（弱证据 —— 说明提过，不足以单独定性）'
            if _e.get("text"):
                _wtip += f'，如 {_e.get("at", "")}「{str(_e["text"])[:40]}」'
            _one = f'<span class="chip weak1" title="{_html.escape(_wtip)}">{_html.escape(t)}</span>'
        else:
            _one = (f'<span class="chip" title="{_html.escape(_ctip)}">{_html.escape(t)}</span>'
                    if _ctip else f'<span class="chip">{_html.escape(t)}</span>')
        _cmap[t] = _one
        weak_chips += _one
    weak_chips = weak_chips or '<span class="chip">—</span>'
    # 卡片右上角（v4.0）：**只展示「学习分层」这一条分层结论**。
    # 旧版这里还叠了「重点跟进 / 需关注 / 正常推进」（红黄绿定档）与「响应迟缓 / 需回复」裸徽章——
    # 前者与学习分层语义混淆（用户反馈「两个分层不知道什么意思」），后者在 ② 信号条、③ To-Do 里已有，
    # 属重复。故头部只留学习分层；近 3 天待回应是硬提醒，作为唯一附加红徽标保留。
    lv_key, lv_cn, lv_ev = common.learning_level(s, board, fst)
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
    head_right = (f'<span class="lvbadge sm {lv_key}" title="学习分层：综合小黑板作答 + 作业闭环 + 老师反馈 + 学生成绩 + 飞书表提交 五源判定">{lv_cn}</span>')
    rt_items = (s.get("recent3") or {}).get("items") or []
    if rt_items:
        head_right += badge(f'近3天待回应 {len(rt_items)}', "red")
    coop = coop_conclusion(s)
    # 联席/课程视角：把「学习实证问题」并入 To-Do —— 聊天侧无「高」优先项时置顶，
    # 使「最该盯」优先落到学习问题上（而非「维持常规跟进」这类空结论）。
    # v4.23：飞书表的「连续未交 / 某项全未交 / 评级下滑」也走同一个入口（fs_focus），
    #   且「最近连续未交」优先级「高」→ 直接置顶（人正在掉队，比小黑板没写完更急）。
    todos_list = list(s["todos"])
    # v4.28：飞书表已确认提交时，chat 侧「催交作业未响应」的 To-Do 是**误判** ——
    #   学生交了，只是没在群里回话。这里把这条改写成「群里不回消息」，保留跟进价值
    #   （该打的电话还是要打），但不写成「再盯一次作业提交」。
    if common.fs_confirms_submit(fst):
        _fixed = []
        for _t in todos_list:
            if "催交作业未响应" in _t[1]:
                # 依据串里也不能留「N 次未见提交」—— 否则同一张卡还是自相矛盾
                _basis = re.sub(r"催交 (\d+) 次、\d+ 次未见提交",
                                r"群里提醒 \1 次未见回应", _t[2] or "")
                _fixed.append(("中", "群里多次未回作业提醒：电话/私信确认是否已收到作业要求"
                                    "（飞书表显示已提交，属不回消息）",
                               f"{_basis}｜飞书表已有提交记录"))
            else:
                _fixed.append(_t)
        todos_list = _fixed
    if not edu:
        _ff = fs_focus(fst)
        _bf = board_focus(board)
        for _it in [x for x in (_ff, _bf) if x]:
            _idx = 0 if (_it[0] == "高" or not todos_list or todos_list[0][0] == "低") else 1
            todos_list.insert(_idx, _it)
    # v4.34：出勤已成**按购课类型判定**的硬问题（回放漏看 / 直播真缺课），
    #   但 todos 里原来只有「维持常规跟进」这类兜底 → 诊断句说有问题、动作却让他常规跟进，自相矛盾。
    #   故：出勤异常且首条是兜底动作时，插一条针对出勤的动作到最前。
    _av0, _ac0, _at0, _iss0, _sev0 = att_view(r)
    if _iss0 and (not todos_list or "常规跟进" in todos_list[0][1]
                  or "常规家长沟通" in todos_list[0][1]):
        _buy0 = (s.get("buy_type") or "").strip()
        _act = (f"{_iss0}：确认是忘了看还是设备/时间冲突，给一个具体补看时间点"
                if _buy0 == "回放" else
                f"{_iss0}：确认原因（时间冲突 / 忘记），把该节回放发过去要求补看")
        todos_list.insert(0, ("高" if _sev0 >= 2 else "中", _act, f"购课类型 {_buy0 or '—'} · {_av0}"))
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
    fine_html = render_fine(fine, weak_chips, mastered=_mst)
    # v4.35：知识点提到**可见区**（原来在「补充信息」折叠区，要点两次才看得到）。
    #   可见 = 知识点名 ＋ 条数；每个知识点各自一枚 details，点开才展开该知识点下的题型明细。
    _tp_html, _tp_rest = topic_row_html(merged, _cmap, fine, _csig, _tev, _mst)
    # v4.37：飞书表「学生学情/问题点」里的问题句 → **可见区**（比薄弱点更靠上：它常是
    #   群里读不出来、只有表里写过的那部分，且往往直接决定教务/联席要不要马上联系）
    _issue_html = issue_row(fst)
    # 成绩（v4.4 第四类证据）：有成绩时在 ① 顶部前置展示，无则整行不出现（不占版面）
    # v4.36：成绩已提到**卡头**（学校名下面），这里不再重复算一遍；`_exam_shown` 由 exam_head 给出，
    #   用于让「需要关注」里的「考试偏低」不再重复同一条。
    _fs_exam_shown = _exam_shown
    fp = fs_parts(fst, exam_shown=_fs_exam_shown)
    _fp_risk, _fp_items = fp["risk"], fp["items"]
    _fp_track, _fp_note = fp["track"], fp["note"]
    _fp_extra, _fp_src = fp["extra"], fp["src"]
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

    # ══ v4.34 精简卡：一眼（诊断句 + 四格 + 动作）／两折（明细 + 剩余动作）══
    # ⚠️「学习分层判定依据（需重点关注 · 累计 −3）· 展开」整行**不渲染**（用户明确要求隐藏）——
    #    分档结论仍在卡头徽章，明细里不再重复一遍加减分，判档口径统一挪到报告页脚。
    _diag = diag_line(r, fst, lv_key)
    _mgrid = metrics_grid(r, fst)
    _a1, _a1_full = act_brief(todos_list[0]) if todos_list else ("—", "")
    _a2 = act_brief(todos_list[1])[0] if len(todos_list) > 1 else ""
    _a2_html = (f'<div class="act2"><span class="actk">其次</span>{_a2}</div>') if _a2 else ""

    def _kv(k, v):
        return f'<div class="kv"><span class="k">{k}</span><span class="v">{v}</span></div>'

    # ── 补充信息：只放「四格 / 诊断句里读不出来」的东西，凡与上方重复一律不放 ──
    _det = []
    # ⚠️ 成绩不进折叠区（v4.36 已提到卡头学校名下面）—— 否则同一分数出现两次
    # ⚠️「需要关注」红块与下面的作业分项是同一批数字 → 有分项时不再重复；没有分项才留
    #    （此时风险来自评级下滑 / 表内成绩等 fsitems 之外的信号）
    if _fp_risk and not (fst and fst.get("items")):
        _det.append(_fp_risk)
    if _fp_items:                                   # 提交分项 + 评级序列条（合计已在指标格）
        _it2 = re.sub(r'<span class="fsfrom">.*?</span>', '', _fp_items)
        # 只有 1 个分项时，这行与指标格是同一个数字（1/2 → 1/2）→ 不重复占行，只在评级序列条另有内容时保留
        if len((fst or {}).get("items") or []) <= 1:
            _it2 = re.sub(r'<div class="fsitems">.*?</div>', '', _it2, flags=re.S)
        _it2 = _drop_fsrate(_it2)
        if "fsrate" in _it2 or "fsitems" in _it2:
            _det.append(_it2)
    # v4.35：知识点已挪到**可见区**（点开看明细）→ 折叠区不再重复整块。
    #   只有两种残留情形要补：① 超出可见上限（4 个）的知识点；② 细粒度证据有、但粗知识点没对齐上
    #   （极罕见），此时整块明细放进折叠区，避免信息丢失。
    if _tp_rest:
        _det.append(_kv("其余",
                        f'<div class="tchips">{"".join(_tp_rest)}</div>'))
    # v4.47：只提过 1 次的知识点单独收在这里 —— 既不占「薄弱点」结论位，也不让信息丢失
    if _weak1:
        _w1 = "、".join(_html.escape(x) for x in _weak1[:6]) + ("…" if len(_weak1) > 6 else "")
        _det.append(_kv("群内提及",
                        f'<span class="mute">仅提过 1 次、未到定性门槛（不计入薄弱点）：{_w1}</span>'))
    if fine and not _tp_html:
        _fine = re.sub(r'<div class="note">.*?</div>', '', fine_html, flags=re.S)
        _fine = re.sub(r'<div class="fine-h">.*?</div>', '', _fine, flags=re.S)
        _det.append(_fine)
    if adv and adv != "—":                          # 优势（剥掉与指标格重复的前半句）
        _adv = re.sub(r'^飞书表各项提交[^，]*，', '', adv)
        # 群里几乎不发言，却写「沟通配合好、关系良好」= 自相矛盾 → 不渲染
        if not ((s.get("n_stu_sub") or 0) <= 2 and re.search(r"沟通|配合|关系", _adv)):
            _det.append(_kv("优势", _adv))
    if board and BOARDS_ON:                         # 小黑板（课堂实证）
        _det.append(board_block(board))
    _un, _uo = s.get("urge_n", 0), s.get("urge_ok", 0)
    if _un:                                         # 催交痕迹（没催过就不占行）
        if _uo == _un:
            _ut = f"提醒 {_un} 次，均在提醒后提交"
        elif common.fs_confirms_submit(fst):
            _ut = (f"提醒 {_un} 次，群内回应 {_uo} 次 —— 飞书表有提交记录，"
                   f"属<b>未回消息</b>、非未交作业")
        else:
            _ut = f"提醒 {_un} 次，提醒后仍未交 {_un - _uo} 次"
        _det.append(_kv("催交", _ut))
    _fn, _fo = s.get("fix_n", 0), s.get("fix_ok", 0)
    if _fn:                                         # 订正痕迹（没批改过就不占行）
        _det.append(_kv("订正", f"批改 {_fn} 次，均已改正" if _fo == _fn
                        else f"批改 {_fn} 次，未改 {_fn - _fo} 次"))
    _av, _ac, _at, _iss, _sev = att_view(r)
    if not r.get("att"):
        # v4.24 防误判：**无出勤记录 ≠ 退课**。群里有活跃沟通的，最可能是未排课 / 纯看回放，
        #   写成「可能已退课」会让教务打错电话（实测该课 3 人：群内 26~83 条、最近消息当天）。
        if (s.get("n_text") or 0):
            _det.append(_kv("出勤", f"后台无该生出勤记录（最近消息 {(s.get('newest') or '—')[:10]}、"
                                    f"群内 {s.get('n_text')} 条）→ 更可能是<b>未排课 / 纯看回放</b>，"
                                    f"不像退课；需向教务确认排课状态"))
        else:
            _det.append(_kv("出勤", "后台无该生出勤记录（可能已退课 / 未排入该课程）"))
    else:
        # 出勤细账：异常时必写；**直播课里混了回放**也要写（否则「到课 4/5」看不出含 2 次回放）
        _n2, _eff2, _att2, _pbo2, _pb2, _abs2, _ = common.att_stats(r["att"])
        _buy2 = (s.get("buy_type") or "").strip()
        if _buy2 == "回放" or _iss or _eff2 > _att2:
            if _buy2 == "回放":
                _vt = f"购回放课 · 已看 {_eff2}/{_n2} 节，看回放即完成"
            else:
                _vt = f"购直播课 · 出勤 {_eff2}/{_n2} 节"
                if _eff2 > _att2:
                    _vt += f"（到课 {_att2} ＋ 回放 {_eff2 - _att2}，看回放同样算出勤）"
                if _abs2:
                    _vt += f"，{_abs2} 节既没到课也没看回放"
            _det.append(_kv("出勤", _vt))
    if s.get("resp_max_h", 0) >= RESP_WARN_H:       # 响应时效（学生的消息等了多久）
        _rt2 = f"最长等待 {s['resp_max_h']:.0f} 小时"
        if "已说明" in (s.get("resp_evi") or ""):
            _rt2 += "（已说明安排）"
        _det.append(_kv("响应", _rt2))
    if s.get("neg"):                                # 情绪 / 压力表达
        _det.append(_kv("情绪", f"群内出现 {s['neg']} 处压力 / 情绪表达"))
    if (s.get("n_other") or 0) > 0:                 # 家长出面量（不提网名）
        _det.append(_kv("家长参与", f"窗口内家长发言 {s['n_other']} 条"))
    if _fp_note:                                    # 老师手记（团队手填原文）
        _det.append(_fp_note)
    if _fp_extra:                                   # 续报 / 星星榜 / 新生 / 沟通优先级
        _det.append(_fp_extra)
    if _fp_src:                                     # 飞书表溯源（表名 + 行号 + 口径）
        _det.append(_fp_src)
    _det_html = (f'<details class="slimdet"><summary>补充信息（作业明细 / '
                 f'跟进痕迹 / 老师手记）</summary>{"".join(_det)}</details>') if _det else ""
    _more_html = ""
    if len(todos_list) > 2:
        _mli = "".join(f'<li><span class="pri pri-{p}">{p}</span>{a}</li>'
                       for p, a, b in todos_list[2:])
        _more_html = (f'<details class="slimdet"><summary>其余跟进建议（{len(todos_list) - 2} 条）'
                      f'</summary><ol class="todos">{_mli}</ol>'
                      f'{_kv(cadence_label, cadence)}</details>')
    return f"""
    <div class="card slim" data-co="{co}" data-name="{r['stu']}" {kattr}>
      <div class="chead">
        <div class="cname">{r['stu']}{bt_tag}{_cschool}{_cexam}</div>
        <div class="cgroup">{r['course_raw']}{co_tag}</div>
        {_cjw_line(r, edu)}
        <div class="cstatus">{head_right}</div>
      </div>
      {_xsum}
      <div class="dx dx-{lv_key}">{_diag}</div>
      <div class="mgrid">{_mgrid}</div>
      {_issue_html}
      {_tp_html}
      <div class="actbox">
        <div class="act1" title="{_html.escape(_a1_full)}"><span class="actk actk-1">最该做</span><b>{_a1}</b></div>
        {_a2_html}
      </div>
      {recent3_block(s)}
      {_det_html}
      {_more_html}
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
    """学习轴一句话：出勤 ＋ 小黑板。返回 (出勤文案, 小黑板文案, 是否有学习证据)。

    v4.18：`nAtt` 已是**有效出勤（含看回放）**，文案必须把「回放补齐」写出来 ——
    否则「出勤 5/5」会被读成 5 节都到课了（实际可能一次直播都没上、全靠回放）。
    """
    n_att, n_les = v.get("nAtt") or 0, v.get("nLes") or 0
    n_pbo = v.get("nPbOnly") or 0
    bn, ball, br = v.get("boardN") or 0, v.get("boardAll") or 0, v.get("boardRounds") or 0
    att_txt = ((f"出勤 <b>{n_att}/{n_les}</b>"
                + (f"（其中 <b>{n_pbo}</b> 次为看回放补齐）" if n_pbo else ""))
               if n_les else "出勤接口无记录")
    if bn > 0:
        return att_txt, f"课堂小黑板作答 <b>{bn}/{ball} 张</b>（{br} 轮）", True
    if ball > 0:                      # 有图但全是「空白未提交」→ 人到了没写，不作学习证据
        return att_txt, f"课堂小黑板 {ball} 张<b>均为空白未提交</b>", n_att > 0
    return att_txt, "小黑板无记录", n_att > 0


def _warn_fs_line(v):
    """预警卡增补「飞书表」一行（v4.24）。

    ⭐ 为什么需要：预警卡的对象是「在册但没有企微群 / 没有聊天记录」的学生，
       而他们**往往照样在飞书表里有作业评级、打穿题记录**（本课 4 人全部有）。
       不把这条证据摆出来，结论就只能写「无学习痕迹」，把在读学生误判成失联。
    返回 (html, 是否有飞书学习痕迹, 学校行 html, 成绩行 html)。
    """
    _nn = common.norm_name(v.get("stuName") or "")
    _fs = (FS_MAP.get((_nn, v.get("productName")))
           or FS_MAP.get((_nn, v.get("_course_raw") or "")))
    st = common.fs_stats(_fs)
    if not st:
        return "", False, school_line(_fs), ""      # 只有档案无课次 → 仍可能填了学校
    # v4.36：预警卡同样把「近期成绩」放到学校名下面（这批学生没有聊天记录，
    #   飞书表成绩往往就是唯一的学习实证，藏进折叠区等于没有）
    _ex_head = exam_head({}, st)[0]
    bits = []
    if st["pct"] is not None:
        bits.append(f'提交合计 <b>{st["D"]}/{st["D"] + st["U"]}</b>（{st["pct"]:.0f}%）')
    for it in (st["items"] or [])[:3]:
        bits.append(f'{it["item"]} {it["done"]}/{it["total"]}')
    for _it, _bd, _cls in (st.get("badges") or [])[:2]:
        bits.append(f'{_it} {_bd}')
    # v4.37：表内「学生问题」提到最前 —— 预警卡没有卡片主体，这一行就是它的可见区；
    #   这批学生没有聊天记录，表里写的问题往往就是**唯一**的问题线索。
    if st.get("issues"):
        _iss = "；".join(str(x[1])[:40] for x in st["issues"][:2])
        bits.insert(0, '<span class="wiss">学生问题：' + _html.escape(_iss) + "</span>")
    elif st.get("note"):
        _n = str(st["note"])
        bits.append("手记：" + _html.escape(_n[:60]) + ("…" if len(_n) > 60 else ""))
    if not bits:
        return "", False, school_line(st), _ex_head
    html = ('<div class="warnrow"><span class="warnk">飞书表</span><span class="warnv">'
            + " ｜ ".join(bits) + "</span></div>")
    has = bool((st["pct"] or 0) > 0) or bool(st.get("ratings"))
    return html, has, school_line(st), _ex_head


def warn_card_html(v, chat_days=14, show_co_tag=False):
    """单张预警卡（左红边）—— 只渲染「未进入学生学情分析」的在册学生。"""
    tier = v.get("tier") or "失联"
    stu = v.get("stuName") or "—"
    course = v.get("_course") or common.course_name(v.get("productName"), "")
    bn = v.get("boardN") or 0
    fs_row, fs_has, _cschool, _cexam_w = _warn_fs_line(v)
    # v4.27：加课 / 电话沟通（这两类与「有没有群、有没有聊天」无关，按姓名兜底取）
    _wfs = fs_lookup(v.get("stuName"), v.get("productName"), v.get("_course_raw"))
    _xsum_w = extra_line(_wfs, as_warn_row=True)
    _has_xc = extra_has_record(_wfs)          # 有带日期的加课记录 → 属「有学习痕迹」
    _fs_submit = fs_has                       # 原来的口径：有作业提交 / 评级
    fs_has = fs_has or _has_xc
    has_ev = _warn_learn_axis(v)[2] or fs_has
    comm_txt = _warn_comm_axis(v, chat_days)
    att_txt, board_txt, _ = _warn_learn_axis(v)

    # v4.18：区分「到课」与「只看回放补齐」。看回放同样算有效出勤（口径已修），
    #   但措辞不能说成「到课」—— 否则教务电话里会把「一次直播没来、在家看回放」说成「正常上课」。
    n_att = v.get("nAtt") or 0
    pb_only_all = n_att > 0 and (v.get("nPbOnly") or 0) >= n_att
    if pb_only_all:
        ev_short, ev_long = "学生在看回放补课", "<b>未到直播、但已用回放把课补齐</b>（有效出勤全部来自回放）"
    elif n_att:
        ev_short, ev_long = "学生在正常上课", "<b>在正常上课</b>（有到课出勤记录）"
    elif _fs_submit:
        # v4.24：没有任何出勤记录、但飞书表有提交 → 不能说「正常上课」，只能说「有作业痕迹」
        ev_short, ev_long = "飞书表有作业提交记录", "<b>飞书表有作业提交 / 评级记录</b>（无出勤记录）"
    elif _has_xc:
        # v4.27：只有加课记录（无出勤 / 小黑板 / 作业）→ 仍属「有学习痕迹」，不能判「最需人工介入」
        ev_short, ev_long = "飞书表有加课记录", "<b>飞书表记有加课</b>（无出勤 / 小黑板 / 作业记录）"
    else:
        ev_short, ev_long = "", ""

    # ---- 结论（防伪核心）----
    if tier == "出勤但无沟通":
        concl = (f"企业微信沟通通道失效：学生{ev_long}，"
                 "但企微侧完全没有触达 —— 缺的是通道，不是学生。")
        if bn > 0:
            concl += f" 课堂小黑板有 <b>{bn} 张有效作答</b>，据此可确认<b>并非流失</b>。"
        elif _fs_submit:
            concl += " 飞书表里有该生的作业提交 / 评级记录，据此可确认<b>并非流失</b>（缺的只是企微通道）。"
        elif _has_xc:
            concl += " 飞书表里有该生的加课记录，据此可确认<b>并非流失</b>（缺的只是企微通道）。"
        act = ("① 教务<b>当日电话触达家长</b>：先说明孩子的课堂表现（有出勤 / 有作答），"
               "再确认服务群是漏建还是拉错；② 建群 / 纠正群后，联席在群内做一次"
               "<b>点名课堂反馈</b>，把通道真正用起来；③ 下次课前确认家长已进群、能收到消息。")
        if pb_only_all:
            act += ("④ 顺带问一句<b>为什么没来直播</b>（时间冲突 / 不知道排课 / 已习惯看回放），"
                    "把原因反馈给教务，避免长期只看回放。")
    elif has_ev:
        concl = (f"企业微信沟通通道失效：学习侧有实证（{ev_long}），"
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
                              else ("△ 有图但均空白未提交" if (v.get("boardAll") or 0) else "✗ 无记录"))
           + " ｜ 飞书表 " + ("✓ 有记录" if fs_has else "✗ 无记录"))
    return f'''
    <div class="card warncard" data-name="{stu}" data-warn="1" data-slow="0" data-unres="0"
         data-inter="0" data-urge="0" data-fix="0" data-absent="0" data-recent3="0"
         data-tier="{tier}">
      <div class="chead">
        <div class="cname">{stu}<span class="wtag {WARN_TIER_CLS.get(tier, 't1')}">{tier}</span>{_cschool}{_cexam_w}</div>
        <div class="cgroup">{course}{co_tag}</div>
        <div class="cstatus">{side_badge}</div>
      </div>
      {_xsum_w}
      <div class="warnrow"><span class="warnk">沟通轴</span><span class="warnv">{comm_txt}</span></div>
      <div class="warnrow"><span class="warnk">学习轴</span><span class="warnv">{att_txt} ｜ {board_txt}
        {f'→ <b>{ev_short}</b>' if has_ev else '→ 无学习侧证据'}</span></div>
      {fs_row}
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
/* v4.25 学校标记：挂在姓名正下方的一行小字（身份信息，随卡片头部一眼可见） */
.cschool{display:block;font-size:11.5px;font-weight:400;color:#7b838d;margin-top:3px;
  line-height:1.4;max-width:min(320px,68vw);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* v4.36 近期成绩：紧跟学校名下面一行，分数加粗放大（分层最强证据之一，不能藏进折叠区） */
.cexam{display:flex;align-items:baseline;gap:6px;flex-wrap:wrap;font-weight:400;margin-top:5px;
  font-size:12px;line-height:1.5;color:#3d4654}
.cexam .ck{font-size:10.5px;color:#8a9099;letter-spacing:.5px}
.cexam b{font-size:14.5px;font-weight:800;color:#1f2329;letter-spacing:.2px}
.cexam .cf{font-size:11.5px;font-weight:500;color:#8a9099;margin-left:-1px}
.cexam .cp{font-size:11.5px;color:#5c6470}
.cexam .exlv{line-height:1.55}
.cexam.low b{color:#cf1322}
.cexam .cword{font-size:13px;font-weight:700;letter-spacing:.2px}
.cexam.low .cword{color:#cf1322}
.cexam .csrc{font-size:10.5px;color:#a8afb8}
.cgroup{font-size:12px;color:#8a9099}
/* v4.39 教务核心数据：卡头身份行（在读科目 / 续报 / 扩科意向）+ Tab2 概览块 */
.cjw{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:6px;
  font-size:12px;line-height:1.6;padding:6px 9px;background:#fbfcfe;border:1px solid #eef1f6;border-radius:8px}
.jwlab{flex:0 0 auto;font-size:11px;color:#7b838d;background:#f0f2f5;border-radius:6px;padding:2px 7px;margin-left:4px}
.jwlab:first-child{margin-left:0}
.subjtag{display:inline-block;font-size:11.5px;font-weight:600;color:#2f6fd0;background:#eaf1fd;border:1px solid #d6e3fb;border-radius:6px;padding:1px 8px}
.subjtag.none{color:#a8afb8;background:#f4f5f7;border-color:#eceef1;font-weight:400}
.jwv{display:inline-block;font-size:11.5px;font-weight:600;border-radius:6px;padding:1px 8px}
.jwv.ok{color:#2ba471;background:#e8f7f0;border:1px solid #c7ecdb}
.jwv.no{color:#e54545;background:#fdecec;border:1px solid #f7caca}
.jwv.wait{color:#ff8f1f;background:#fff3e0;border:1px solid #ffe0b2}
.jwv.none{color:#a8afb8;background:#f4f5f7;border:1px solid #eceef1;font-weight:400}
.kuake-chip{display:inline-block;font-size:11px;font-weight:600;color:#8a5cc7;background:#f3edfc;border:1px solid #e3d6fb;border-radius:6px;padding:1px 7px;cursor:help}
/* v4.42：扩科意向依据直接可见（不只靠悬停） */
.kuevi{display:inline-block;font-size:11px;color:#8a9099;max-width:240px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;vertical-align:middle;cursor:help;margin-right:6px}
.edu-ov .kuevi{max-width:100%;white-space:normal;overflow:visible;text-overflow:unset;
  display:block;margin:2px 0 4px;line-height:1.5}
.edu-ov{background:#fff;border:1px solid #e6e8eb;border-radius:12px;padding:18px;margin-bottom:20px}
.edu-ov h2{font-size:16px;margin:0 0 12px}
/* v4.40 教务核心数据：KPI 联动（v4.41 起课程筛选上移到页面顶部） */
.edu-hd{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.edu-hd h2{margin:0}
.edu-cnote{font-size:11.5px;color:#98a1ac}
/* v4.41 顶部「课程」全局筛选标签条 */
.cfilter{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:14px 0 6px}
.cflabel{font-size:12px;color:#8a9099;flex:none}
.cfchips{display:flex;align-items:center;gap:6px;flex-wrap:wrap;min-width:0}
.cchip{display:inline-flex;align-items:center;gap:6px;max-width:230px;white-space:nowrap;
  font-size:12.5px;line-height:1;padding:8px 12px;border:1px solid #dcdfe6;border-radius:999px;
  background:#fff;color:#4a5568;cursor:pointer;font-family:inherit;
  transition:background .15s,border-color .15s,color .15s}
.cchip:hover{border-color:#9db2ee;color:#2f4fbf}
.cchip.on{background:#eef2ff;border-color:#3a5bdb;color:#2f4fbf;font-weight:600}
.cchip .ccn{font-size:11px;font-weight:600;color:#8a9099;background:#f2f4f7;border-radius:999px;padding:1px 6px}
.cchip.on .ccn{background:#dde4fd;color:#2f4fbf}
.cfcur{font-size:11.5px;color:#8a9099;margin-left:auto}
.cfhint{font-size:11.5px;color:#8a9099;margin:0 0 14px;line-height:1.7}
.edu-ov .kpi.kbl{position:relative}
.edu-ov .kpi.kbl.on{border-color:#3a5bdb}
.edu-ov .kpi .n{font-size:26px;font-weight:700}
.edu-ov .kpi .l{font-size:12px;color:#8a9099;margin-top:2px}
.slimdet{margin-top:14px;border-top:1px dashed #eceef1;padding-top:10px}
.slimdet>summary{font-size:12px;color:#5b6b8c;cursor:pointer;outline:none;list-style:none;
  display:inline-flex;align-items:center;gap:4px;user-select:none}
.slimdet>summary::-webkit-details-marker{display:none}
.slimdet>summary:before{content:"▸";font-size:10px;color:#8a97ab}
.slimdet[open]>summary:before{content:"▾"}
.slimdet>summary:hover{color:#2f6fd0}
.slimdet table{margin-top:8px}
.slimdet th{font-size:11.5px}
.slimdet td{font-size:12.5px;vertical-align:top}
/* v4.27 加课 / 电话沟通：学生情况上方单独一条（团队手填自由文本，两行一粗一细） */
.xsum{margin:8px 0 10px;padding:8px 11px;background:#f7f9fd;border:1px solid #e3eafa;border-radius:8px}
.kv.xsum .k{flex:0 0 84px;white-space:nowrap;color:#5a6b8c}
.xrow{display:flex;gap:8px;font-size:12.5px;line-height:1.65;align-items:flex-start}
.xrow+.xrow{margin-top:3px}
.xrow b.xk{flex:0 0 56px;font-weight:700;color:#2f6fd0}
.xrow b.xk.pc{color:#8a5cc7}
.xt{flex:1;color:#3d4654;word-break:break-word}
/* v4.27.1 「展开全部记录」默认收起（无 open 属性）；箭头样式与全站折叠块一致 */
.xdet{margin-top:5px}
.xdet>summary{font-size:11.5px;color:#5b6b8c;cursor:pointer;outline:none;list-style:none;
  display:inline-flex;align-items:center;gap:4px;user-select:none}
.xdet>summary::-webkit-details-marker{display:none}
.xdet>summary:before{content:"▸";font-size:10px;color:#8a97ab}
.xdet[open]>summary:before{content:"▾"}
.xdet>summary:hover{color:#2f6fd0}
.xall{font-size:12px;color:#49525e;line-height:1.7;margin-top:5px}
.xallgrp+.xallgrp{margin-top:6px}
.xgl{display:block;font-size:11.5px;color:#7b8794;margin-bottom:2px}
.xall div{padding-left:9px;border-left:2px solid #e3eafa;margin:2px 0;word-break:break-word}
.warnrow.wir .xw{display:block}
.warnrow.wir .xrow b.xk{flex:0 0 40px;font-size:12px}
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
/* v4.31.1 弱证据知识点：只有 1 条问题信号 —— 虚线、去底色，视觉上明显轻于实心 chip，
   避免读者把「群里提过一次」当成和「反复卡住」同级的结论（hover 另有文字说明） */
.chip.weak1{background:transparent;color:#8a9099;border:1px dashed #d5dae2;padding:1px 7px}
/* v4.32 已掌握：学生问过、老师讲过、且后续有闭环证据（学生说懂了 / 老师确认正确）。
   灰底 + 绿色 ✓ 角标 —— 保留展示（业务要求「继续展示并标识」），但一眼能看出不用再盯。 */
.chip.mst{background:#f2f4f7;color:#5b6470;box-shadow:inset 0 0 0 1px #e3e8ef}
.chip.mst .msttag{font-style:normal;margin-left:4px;padding:0 3px;border-radius:3px;
  background:#e8f7ee;color:#1a7f45;font-size:10px;font-weight:700;border:1px solid #b7e4c7}
.mstn{color:#1a7f45;font-size:11px;white-space:nowrap}
/* v4.32 已掌握行（fine 分支专用）：学生问过、老师讲过、后来本人确认听懂 —— 明确「不用再盯」 */
.mstline{margin:8px 0 2px;padding:6px 9px;background:#f6fbf8;border:1px solid #d6efe0;
  border-radius:6px;font-size:12px;line-height:1.75;color:#3d4b40}
.mstline .msth{font-weight:700;color:#1a7f45;margin-right:6px;white-space:nowrap}
.mstline .mstitem{display:inline-block;margin-right:10px}
.mstline .mstwhen{color:#7d8a80;font-size:11px;margin-left:5px}
.mstline .mstnote{display:block;color:#93a09a;font-size:11px;margin-top:2px}
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
/* 飞书表样式（v4.19.3 起；v4.23 起改为拆散融入 ① 学习情况，.fsitem/.rseq 仍复用） */
details.fsdet{margin-top:8px;border-top:1px dashed #eef0f3;padding-top:7px}
details.fsdet>summary{cursor:pointer;font-size:12px;font-weight:600;color:#1a7f5a;list-style:none;outline:none}
details.fsdet>summary::-webkit-details-marker{display:none}
details.fsdet>summary:before{content:"▸ "}
details.fsdet[open]>summary:before{content:"▾ "}
.fsitems{display:flex;gap:6px;flex-wrap:wrap;margin:9px 0 6px}
.fsitem{display:inline-flex;align-items:baseline;gap:5px;font-size:12px;padding:4px 9px;border-radius:8px;background:#f6f8fa;border:1px solid #eef0f3;color:#4b5563}
.fsitem b{font-weight:700;color:#1f2329}
.fsitem i{font-style:normal;font-size:11px;opacity:.85}
.fsitem.ok{background:#e8f7f0;border-color:#cdeadd;color:#2ba471}
.fsitem.warn{background:#fff8ec;border-color:#f5e3c0;color:#c47b00}
.fsitem.bad{background:#fdecec;border-color:#f5c9c9;color:#d93a3a}
.fsnote{font-size:12.5px;color:#1f2329;background:#f6f8fa;border-left:3px solid #2ba471;border-radius:4px;padding:7px 10px;margin:7px 0;line-height:1.65;word-break:break-word}
.fsnote b{color:#1a7f5a;margin-right:6px}
.fsmeta{font-size:11.5px;color:#8a9099;margin-top:7px;line-height:1.6}
/* v4.23 飞书内容拆散融入 ① 学习情况（不再是一个独立折叠块，见 fs_parts） */
.fsline{margin:5px 0 4px}
.fsfrom{display:block;font-size:11.5px;color:#a0a6ae;line-height:1.5;margin:0 0 4px}
.fsline .fsitems{margin:0}
.fsrisk{font-size:12.5px;line-height:1.7;border-radius:8px;padding:7px 10px;margin:6px 0 9px;
  display:flex;gap:7px;flex-wrap:wrap;align-items:baseline;border:1px solid}
.fsrisk b{font-weight:800;white-space:nowrap}
.fsrisk.sev0{background:#fdecec;border-color:#f5c9c9;color:#b3261e}
.fsrisk.sev1{background:#fff8ec;border-color:#f5e3c0;color:#9a6200}
.fsrisk.sev2{background:#f5f7f9;border-color:#e9edf1;color:#5b6b7c}
.fsrk+.fsrk:before{content:"·";margin:0 7px 0 0;opacity:.55}
/* ⭐ v4.21 评级趋势：逐节课的 ABCD 等级序列条（profile.feishu.ratings 声明了才有） */
.fsrate{margin-top:9px;border-top:1px dashed #eef0f3;padding-top:8px}
.fsrate .fshr{font-size:12px;font-weight:600;color:#1a7f5a;margin-bottom:6px;display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.rtb{font-size:11px;padding:1px 8px;border-radius:999px;font-weight:700}
.rtb.up{background:#e6f6ec;color:#1f7a44}
.rtb.down{background:#fdecea;color:#c0392b}
.rtb.flat{background:#eef1f4;color:#6b7783}
.rtsum{font-weight:400;color:#5b6b7c;font-size:12px}
.rseq{display:flex;flex-wrap:wrap;gap:3px;align-items:center}
.rseq i{width:20px;height:20px;line-height:20px;text-align:center;border-radius:4px;font-size:11px;font-style:normal;font-weight:700;color:#fff}
.rseq i.rg-A{background:#1f7a44}
.rseq i.rg-B{background:#1f5f8b}
.rseq i.rg-C{background:#b8741a}
.rseq i.rg-D{background:#c0392b}
.rseq i.rg-absent{background:#e3e7eb;color:#8b97a3;font-weight:400}
.rseq i.rg-none{background:#fff;border:1px dashed #d7dee5;color:#c3ccd4;font-weight:400}
.rseq .rgax{font-size:11px;color:#8a9099;margin-left:4px}
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
/* v4.9 学生情况概览（v4.30 由「主讲备课建议」改名；样式沿用） */
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
/* v4.13：Tab② 「需要重点关注的学生」子模块 */
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
.wiss{color:#cf1322;font-weight:600}
.warnconcl{font-size:13px;line-height:1.75;background:#fdecec;border-radius:6px;padding:7px 11px;margin:8px 0}
.warnact{font-size:13px;line-height:1.75;background:#fff7e6;border-radius:6px;padding:7px 11px;margin:0 0 8px}
.warnsrc{font-size:11.5px;color:#8a9099;line-height:1.6}
.warnnote{font-size:11px;color:#a0a6ae;margin-top:6px;line-height:1.6}
.warnbar{margin:12px 0 0;padding:9px 13px;border:1px dashed #f0b4b4;border-radius:8px;background:#fffafa;color:#b02525;font-size:12.5px;line-height:1.6;cursor:pointer;user-select:none}
.warnbar:hover{background:#fff3f3;border-color:#e54545}
.warnbar b{color:#cf1322}
.warnbar .warnbar-c{float:right;color:#cf1322;font-weight:600}
/* ══ v4.34 卡片精简：「一眼（诊断句 + 四格 + 动作）+ 两折（明细 / 剩余动作）」 ══
   目标是把原来「① 学习情况 / ② 配合度 / ③ To-Do」三块并列压成一条视线：
   先看一句话结论，再看四个数字，再看该做什么。明细全部收进折叠。 */
.card.slim{padding:14px 16px}
.dx{font-size:14px;line-height:1.65;margin:8px 0 10px;padding:9px 12px;border-radius:8px;
  background:#fafbfc;border-left:3px solid #c9ced6;color:#2b3038;font-weight:500}
.dx-red{background:#fff5f5;border-left-color:#f5222d;color:#a8071a}
.dx-orange{background:#fff9ec;border-left-color:#fa8c16;color:#ad4e00}
.dx-green{background:#f2fbf5;border-left-color:#28a745;color:#1a7f45}
.dx-blue{background:#f0f7ff;border-left-color:#1677ff;color:#0b4fc4}
.mgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:11px}
.mg{background:#fafbfc;border:1px solid #eef0f3;border-radius:8px;padding:7px 9px}
.mgk{display:block;font-size:11px;color:#8a9099;margin-bottom:1px}
.mgv{display:block;font-size:15px;font-weight:700;letter-spacing:-.2px}
.mgv.ok{color:#1a7f45}.mgv.warn{color:#d46b08}.mgv.bad{color:#cf1322}.mgv.mute{color:#8a9099}
/* v4.35 薄弱知识点（可见区）：名 + 条数常显，点某一枚才展开该知识点的题型明细 */
.trow{display:flex;align-items:flex-start;gap:9px;margin:-3px 0 10px}
.trowk{flex:0 0 42px;font-size:11.5px;color:#8a9099;font-weight:600;padding-top:3px;white-space:nowrap}
.tchips{display:flex;flex-wrap:wrap;gap:5px;flex:1;min-width:0}
.tchip{display:inline-block;max-width:100%}
.tchip>summary{display:inline-flex;align-items:center;cursor:pointer;list-style:none;border-radius:12px;
  max-width:100%;min-width:0}
.tchip>summary::-webkit-details-marker{display:none}
.tchip>summary::after{content:"▾";font-size:9px;color:#9aa4b2;margin-left:3px;line-height:1}
.tchip[open]>summary::after{content:"▴";color:#3a5bdb}
.tchip[open]{flex:0 0 100%}
.tchip[open]>summary .chip{background:#dfe8ff;box-shadow:inset 0 0 0 1px #b9ccf2}
.tchip.mstchip>summary .chip{background:#f2f4f7}
.tchipd{margin:5px 0 2px;padding:8px 11px;background:#fbfcfe;border:1px solid #e9eef6;
  border-radius:8px;font-size:12.5px;color:#333;max-width:100%}
.tchipd .fine-l li{margin-bottom:2px}
.tpda{color:#5c6370;line-height:1.65}
.tpdm{margin-top:5px;padding-top:5px;border-top:1px dashed #e3e8ef;color:#1a7f45;line-height:1.6}
.tchipd .fcnt{font-size:10.5px;color:#d46b08;margin-left:3px}
.tcnt{font-style:normal;font-size:10px;margin-left:4px;padding:0 3px;border-radius:3px;
  background:rgba(58,91,219,.12);font-weight:700}
.chip.mst .tcnt{background:rgba(0,0,0,.06)}
/* v4.37 学生问题（可见区）：表内「学生学情/问题点」原句 —— 红=行为/态度，橙=基础/状态 */
.trow.irow .trowk{flex:0 0 56px;color:#b23a48}
.chip.iss{font-weight:600;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip.iss.hard{background:#fff1f0;color:#cf1322;box-shadow:inset 0 0 0 1px #ffccc7}
.chip.iss.soft{background:#fff7e6;color:#c76a00;box-shadow:inset 0 0 0 1px #ffe1b3}
.chip.iss.more{background:#f5f6f8;color:#7a828c;font-weight:400;box-shadow:inset 0 0 0 1px #e3e8ef}
.ichip>summary::after{color:#c9a06a}
.tchipd .isrc{margin-top:4px;font-size:10.5px;color:#a3aab4}
.actbox{background:#fffdf7;border:1px solid #ffe6bf;border-radius:9px;padding:10px 12px}
.act1{font-size:13.5px;line-height:1.6}
.actk{display:inline-block;font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px;
  margin-right:7px;vertical-align:1px;background:#eee;color:#666}
.actk-1{background:#ff8f1f;color:#fff}
.act2{font-size:13px;line-height:1.6;color:#5c6370;margin-top:7px;padding-top:7px;
  border-top:1px dashed #f0e2c8}
details.slimdet{margin-top:11px;border-top:1px dashed #eef0f3;padding-top:8px}
details.slimdet>summary{cursor:pointer;font-size:12.5px;color:#5c6370;font-weight:600;
  list-style:none;padding:3px 0;user-select:none}
details.slimdet>summary::-webkit-details-marker{display:none}
details.slimdet>summary::before{content:"▸ ";color:#a0a6ae}
details.slimdet[open]>summary::before{content:"▾ "}
details.slimdet>summary:hover{color:#3a5bdb}
/* 折叠区里的 kv 标签 64px 装不下「薄弱知识点」5 字，会折成两行 */
.card.slim .kv .k{flex:0 0 78px;white-space:nowrap}
.card.slim .fsrisk{margin-top:8px}
.card.slim .sub-blk{margin-top:8px}
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


def md_inline(text):
    """转义 + 把 `**粗体**` 转成 <b>（v4.22）。

    ⚠️ 上游（适配器的 degraded.reason、脚本里的说明文案）用的是 **markdown 粗体**，
    直接塞进 HTML 会原样显示星号。凡是「人写的说明文字」进 HTML 前都该过它。
    """
    t = _html.escape(str(text if text is not None else ""))
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)


def coverage_block(cov, roster_n, analyzed_n, chat_days=14, degraded=None, fs_note="",
                   cov_filter=None):
    """报告顶部的「在册全覆盖与失联预警」（v4.8）。

    v4.41：`cov_filter`（仅教务）＝ {"key_by_pn": {productName: 课程key}, "kpi": {...}, "all": {...}}
    —— 让顶部「课程」标签条能按课程过滤本块的档位人数与名单。

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
        # v4.24：不再写「在正常上课」—— 这一档里可能多数是**只看回放**的学生（本课 4 人里 3 人纯回放），
        #   写成「正常上课」会让教务以为孩子每节都到课了。用「有出勤记录（含看回放）」并标注纯回放人数。
        _pb_only_n = sum(1 for v in (cov or {}).values()
                         if v.get("tier") == "出勤但无沟通"
                         and (v.get("nAtt") or 0) > 0
                         and (v.get("nPbOnly") or 0) >= (v.get("nAtt") or 0))
        _pb_txt = (f'（<span class="hl">{_pb_only_n}</span> 人纯靠回放、一次直播未到）'
                   if _pb_only_n else "")
        _tail = f'<span class="hl">{anc_n}</span> 人都有出勤记录' if anc_n == no_chat \
            else f'其中 <span class="hl">{anc_n}</span> 人有出勤记录'
        head += f'，这 {_tail}{_pb_txt}、仅企业微信沟通通道未打通' if anc_n == no_chat \
            else f'，{_tail}{_pb_txt}、仅企业微信沟通通道未打通'
    if analyzed_n and analyzed_n > recent_n:
        head += (f'<br>学生卡另详析 <b>{analyzed_n}</b> 人（按报告窗口统计）；'
                 f'其中 <b>{analyzed_n - recent_n}</b> 人窗口内有互动、但近 {chat_days} 天已沉默，'
                 f'同样计入上面的「无沟通」，属<b>需重新触达</b>对象。')
    # v4.10.2：本段（在册 / 沟通概览 ＋ 判定口径）默认**折叠收起**（用户要求「这块说明隐藏」）。
    # 档位人数与名单由下方档位表、分档名单承载，数字并未丢失；口径说明保留可展开，
    # 以符合「结论可回查」的既有要求 —— 不删内容，只改默认可见性。
    bar_inner = (f'{head}<br>'
                 f'口径：沟通轴取近 {chat_days} 天，「无聊天记录」= 群未采集到 或 群内近 {chat_days} 天零消息；'
                 f'学习轴取报告窗口全窗口出勤（<b>含看回放</b>：未到课但已看回放，同样算有出勤）。'
                 f'判定「失联」以<b>出勤</b>为准，<b>不以群是否存在为准</b>'
                 f'（群列表接口有损，实测会漏掉约四成学生）。')
    bar = ('<details class="covlist covhead"><summary>📖 覆盖概览与判定口径 —— 点击展开核对</summary>'
           f'<div class="in"><div class="covbar">{bar_inner}</div></div></details>')

    # ---- 档位总表（v4.10.3：点「人数」直接在本行下方展开该档名单）----
    # 原先名单另设三组折叠块挂在表下方，用户反馈「点击人数，直接展开明细」→ 改为行内展开，
    # 少一次「先找到人数、再滚到下面找同名折叠块」的跳转。
    by_tier = {}
    for v in cov.values():
        by_tier.setdefault(v.get("tier") or "未知", []).append(v)

    _kmap = (cov_filter or {}).get("key_by_pn") or {}   # v4.41：productName → 课程 key

    def _cov_rows(hits):
        out = []
        for v in hits:
            cz = CAUSE_CN.get(v.get("cause") or "", "")
            chip = f'<span class="covcause">{cz}</span> ' if cz else ""
            # v4.41：data-k = 课程 key，供顶部「课程」标签条过滤本档名单
            _k = _kmap.get(v.get("productName") or "") or ""
            out.append(f'<tr data-k="{_html.escape(_k)}"><td><b>{v.get("stuName")}</b></td>'
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
                     f'title="点击{"收起" if dflt else "展开"}本档 {len(hits)} 人名单">'
                     f'<span id="covn-{t}">{n}</span>'
                     f'<span class="covar"></span></span>')
        else:
            ncell = f'<span id="covn-{t}">{n}</span>'
        trows.append(
            f'<tr class="covrow"><td><span class="tierk {TIER_CLS.get(t, "t0")}">{t}</span></td>'
            f'<td>{ncell}</td><td><span id="covpct-{t}">{pct}</span></td>'
            f'<td>{TIER_DESC.get(t, "")}</td></tr>')
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
        items = "；".join(f'{md_inline(d.get("field"))}（{md_inline(d.get("reason"))}）' for d in degraded)
        lists += f'<div class="covdeg">⚠️ 数据降级声明（缺失即明写，不做静默兜底）：{items}</div>'
    if fs_note:
        # v4.23：飞书表已并入学生卡的学习情况（提交情况 / 评级轨迹 / 手记），
        # 这里交代覆盖到什么程度 —— 没进卡的学生不是漏了，是表里本来就没有他的课次记录。
        lists += f'<div class="covdeg">📗 {fs_note}</div>'

    # v4.41：教务视角 —— 把「课程 → 各档人数」交给前端，顶部课程标签条切换时重算本块
    _cjs = ""
    if cov_filter:
        _cjs = ("<script>\nwindow.COVT=" + json.dumps(list(COV_TIER_ORDER), ensure_ascii=False)
                + ";\nwindow.COVK=" + json.dumps(cov_filter.get("kpi") or {}, ensure_ascii=False)
                + ";\nwindow.COVALL=" + json.dumps(cov_filter.get("all") or {}, ensure_ascii=False)
                + ";\nwindow.covApply=function(k){"
                "var d=k?(window.COVK[k]||null):window.COVALL; if(!d)return;"
                "for(var i=0;i<window.COVT.length;i++){var t=window.COVT[i],n=(d.tiers||{})[t]||0;"
                "var e=document.getElementById('covn-'+t); if(e)e.textContent=n;"
                "var p=document.getElementById('covpct-'+t);"
                "if(p)p.textContent=(d.n?(n/d.n*100).toFixed(1)+'%':'—');}"
                "var rs=document.querySelectorAll('#covsec .covsub tr[data-k]');"
                "for(var i=0;i<rs.length;i++){var ok=(!k)||rs[i].getAttribute('data-k')===k;"
                "rs[i].style.display=ok?'':'none';}"
                "for(var i=0;i<window.COVT.length;i++){var t=window.COVT[i];"
                "var det=document.getElementById('covdet-'+t); if(!det)continue;"
                "var any=false,vs=det.querySelectorAll('.covsub tr[data-k]');"
                "for(var j=0;j<vs.length;j++){if(vs[j].style.display!=='none'){any=true;break;}}"
                "var openOnly = any && (k||t==='失联');"
                "det.style.display=openOnly?'table-row':'none';"
                "var sp=document.querySelector('.covn[data-covn=\"'+t+'\"]');"
                "if(sp)sp.classList.toggle('on',openOnly);}};\n</script>")

    return (f'<div class="covsec" id="covsec"><h2>🚨 总人数覆盖与失联预警</h2>'
            f'{bar}{tab}{ctab}{lists}</div>{_cjs}')


# ══════════════════════════════════════════════════════════════════════════
# v4.9：学生情况概览（v4.30 前名为「主讲备课建议」）—— 回答「这批学生普遍卡在哪 / 什么题型」
# ══════════════════════════════════════════════════════════════════════════
# 只用两类**已经发生的事实**，不生成任何新内容：
#   ① 群聊侧：学生主动提问 / 老师指出问题 命中的知识点（extract.top_topics / topic_signals）
#   ② 小黑板侧：读图实证「有错误轮次所关联的知识点」（boards_summary.error_topics）
#      ＋ 逐题卡点 / 原题（common.fine_from_board 下钻到题型）
# 排序按「覆盖学生数」降序（普遍性优先）；同覆盖时「有小黑板实证错误」优先 ——
# 小黑板是学生真实作答，比「群里提过」硬。
# 🔒 反编造铁律：题型名只能来自 FINE_TYPES 词表命中，或字段原文（形如「课堂作答卡点：…」）；
#    每条必附原文出处；无证据时不硬凑，如实说明。
TOPIC_ALIAS = {"解析几何": "直线和圆", "平面解析几何": "直线和圆"}
# 学生卡片「薄弱知识点」的信号门槛（与 extract.MIN_TOPIC_SIGNALS 同值）。
# 概览聚合时用它区分「强证据」与「只有 1 条」的弱证据；两处必须同值，改一处要同步。
_CARD_MIN_SIG = 2


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

    返回 {topic: {"chat": set(学生), "board": set(学生), "err": set(学生),
                  "weak": set(信号仅 1 条的学生), "mastered": set(已掌握的学生)}}，
    分别是「群聊提及」「小黑板涉及」「小黑板实证有错」「群聊证据偏薄」「已闭环掌握」。

    ⚠️ v4.31.1 分档口径：**概览是跨学生聚合，门槛必须比学生卡片宽**。
    extract 侧 `top_topics` 卡「同一知识点 ≥2 条问题信号」（防单条偶发被写成结论，
    那是业务投诉的根因），但概览讲的是「这批人共有的薄弱面」——一个人各提 1 次、
    凑够多人才有意义。用卡片门槛去筛概览，六E胡（9 人）会只剩 1 人达标、整张表消失。
    故这里改读 `topic_signals`（全量），但把「只有 1 条」的学生记进 weak，
    由渲染层在表格与摘要里显式标注证据强度，不把弱证据冒充强结论。
    """
    agg = {}
    for r in crows:
        stu = r["stu"]
        s = r["s"] or {}
        sig = s.get("topic_signals")
        mst = s.get("topic_mastered") or {}   # v4.32：该生已闭环的知识点
        if sig:
            pairs = [(t, int(v.get("n") or 1)) for t, v in sig.items()]
        else:   # 旧数据没这个字段 → 回落 top_topics（它本身就已是达标项）
            pairs = [(t, _CARD_MIN_SIG) for t in (s.get("top_topics") or [])]
        for t, n in pairs:
            k = _std_topic(t)
            if k:
                d = agg.setdefault(k, {"chat": set(), "board": set(),
                                       "err": set(), "weak": set(), "mastered": set()})
                d["chat"].add(stu)
                if n < _CARD_MIN_SIG:
                    d["weak"].add(stu)
                if t in mst:
                    d["mastered"].add(stu)
    for r in crows:
        stu = r["stu"]
        b = (boards_map or {}).get(common.norm_name(stu))
        if not b:
            continue
        for t in (b.get("kb_topics") or []):
            k = _std_topic(t)
            if k:
                agg.setdefault(k, {"chat": set(), "board": set(), "err": set(),
                                   "weak": set(), "mastered": set()})["board"].add(stu)
        for t in (b.get("error_topics") or []):
            k = _std_topic(t)
            if k:
                agg.setdefault(k, {"chat": set(), "board": set(), "err": set(),
                                   "weak": set(), "mastered": set()})["err"].add(stu)
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
# v4.13：Tab② 新增「需要重点关注的学生」模块（v4.30 去掉「下节课」，供联席/教务看）
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
_FOCUS_CLS = {"bad": "bad", "warn": "warn", "blank": "bd", "unfin": "bd", "hard": "bd", "fs": "bd"}
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
    """筛出「值得重点关注」的学生（v4.13；v4.30 去掉「下节课」限定）。

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
        # ③ v4.23 飞书表：附加理由（不作门槛）—— 「课上不爱说话 ＋ 作业也不交」的人最该先动
        _fs = (FS_MAP.get((common.norm_name(r["stu"]), (r.get("g") or {}).get("productName")))
               or FS_MAP.get((common.norm_name(r["stu"]), r.get("course_raw"))))
        _fst = common.fs_stats(_fs)
        _fs_risk = bool(_fst and _fst.get("risks"))
        if _fs_risk:
            kinds.append("fs")
            why.append(_fst["risks"][0][1])
            evi.append("飞书表：" + "；".join(t for _, t in _fst["risks"][:2]))
        out.append(dict(
            stu=r["stu"], co=r.get("co") or "未分配联席",
            sev=_FOCUS_SEV[ilv], lead=ilv, kinds=kinds, why=why, evi=evi,
            has_fs=_fs_risk,
            has_board=bool({"blank", "unfin", "hard"} & set(kinds)),
            board_lead=next((k for k in ("blank", "unfin", "hard") if k in kinds), "hard"),
            # 「沉默不回复」（窗口内 0 条实质发言）比「近期沉默」（只是最近没说话）更值得先点
            sub=(0 if iact == "沉默不回复" else 1),
            newest=(s.get("newest") or ""), strength=(s.get("n_stu_sub") or 0),
        ))
    if not out:
        return [], 0, ""
    out.sort(key=lambda x: (x["sev"], 0 if (x["has_board"] or x.get("has_fs")) else 1, x["sub"],
                            x["newest"], x["strength"], x["stu"]))
    n_bad = sum(1 for x in out if x["sev"] == 0)
    n_warn = len(out) - n_bad
    # v4.24：原来只数「小黑板作答问题」，没有小黑板数据的团队会恒为 0 ——
    #   而飞书表里「作业评级 4 次全未交」同样是硬证据，必须一起数（本课实测 0 → 1）。
    n_evid = sum(1 for x in out if x["has_board"] or x.get("has_fs"))
    if n_evid:
        _ev_txt = (f'其中 <b>{n_evid}</b> 人课堂作答或作业也有问题'
                   f'（小黑板未提交／未收尾／实证错误 · 飞书作业未交）——'
                   f'这些人既没在群里出现、作业也没落实，最该被主动点到。')
    else:
        _ev_txt = '这批人只是群里没怎么说话，课堂作答与作业提交暂无异常 —— 属「安静但在学」，按常规节奏点名即可。'
    summary = (f'全组 <b>{len(crows)}</b> 人中 <b>{len(out)}</b> 人群内互动不足'
               f'（沉默不回复／近期沉默 {n_bad} · 实质发言 ≤3 条的 {n_warn}），'
               + _ev_txt)
    return out[:limit], len(out), summary


def _focus_html(crows, boards_map, limit=6):
    """渲染「⚠️ 需要重点关注的学生」块（Tab② 内嵌）。无数据返回空串。

    v4.30：Tab② 定位从「给主讲备课」改为「给联席 / 教务看整体情况」，
    故标题去掉「下节课」，动作列由「课上可以做的事」改为中性的「可以做的事」
    （动作内容本身不变 —— 点名、盯练习落实，联席跟课时同样可直接执行）。
    """
    shown, total, summary = focus_students(crows, boards_map, limit=limit)
    if not shown:
        return ""
    more = total - len(shown)
    _bact = {"blank": "当场确认小黑板是漏做还是漏交，留一步让他补",
             "unfin": "讲这道题时盯他写完最后一步",
             "hard": "讲该题型时点他确认是否真的会",
             "fs": "课后单独问一次作业/笔记没交的原因，当场约定补齐时间（别在群里催）"}

    def _row(x):
        tags = "".join(
            f'<span class="foc-tag {_FOCUS_CLS[k]}">{w}</span>'
            for k, w in zip(x["kinds"], x["why"]))
        act = _FOCUS_ACT[x["lead"]]
        if x["has_board"]:
            act += "；" + _bact[x["board_lead"]]
        if x.get("has_fs") and "fs" in x["kinds"]:
            act += "；" + _bact["fs"]
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
                     f'<th>可以做的事</th><th>依据（可核对）</th></tr>'
                     + "".join(_row(x) for x in rest[len(shown):]) + '</table></details>')
    return (
        '<div class="tadv-focus">'
        '<div class="fadv-h">⚠️ 建议先跟进的学生</div>'
        f'<div class="fadv-s">{summary}'
        f'下表列最该先跟进的 {len(shown)} 人'
        + (f'，其余 {more} 人见折叠。' if more > 0 else '。') + '</div>'
        '<table class="foc-tab"><tr><th>学生</th><th>关注理由</th>'
        '<th>可以做的事</th><th>依据（可核对）</th></tr>'
        + body + '</table>'
        + more_html
        + '<div class="fadv-note">名单只复述已有证据（群聊互动状态 ／ 小黑板逐轮读图 ／ 飞书表提交与作业评级），'
          '不评价学习态度、不预测成绩。「互动不足」＝窗口内本人实质发言 ≤3 条或已沉默，'
          '不等于学生不用功（「一般」＝实质发言 4~11 条，属正常参与，已排除在名单外）。'
          '排序＝先互动、后练习：「课上沉默 ＋ 作业也不交」的排最前。'
          '互动状态与 Tab③「联席跟进 Action」同源，两处口径一致。'
          '<b>本名单只是「先跟谁」的优先级，不等于学习分层结论</b> ——'
          '分层看卡片右上角徽章，两者口径不同：作业全交但群里沉默的人会出现在这里，分层仍可能是「正常」。'
          '</div>'
        '</div>')


def teaching_advice(crows, boards_map, n_any=None):
    """渲染「📊 学生情况概览 · 共性薄弱面」区块。无任何证据时返回空串（不渲染空壳）。

    v4.30：原为「🎓 主讲备课建议 · 下节课重点讲什么」。Tab② 改由联席 / 教务阅读，
    标题与摘要句改为「这批学生的共性薄弱面」，**判据、排序、证据链一字未改**。
    """
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
            # v4.31.1：其中「只有 1 条问题信号」的人数 —— 渲染层据此标注证据强度
            "weak": len(d["weak"] & d["chat"]),
            # v4.32：其中「已闭环掌握」的人数（学生问过 → 老师讲过 → 有确认）
            "mst": len((d.get("mastered") or set()) & d["chat"]),
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
    # v4.30：Tab② 由「主讲备课建议」改为「学生情况概览」——
    #   读者从「主讲」变成「联席 / 教务」，语气一并改：不再说「下节课建议讲什么」，
    #   而是「这一批学生的共性薄弱面是什么」。口径与判据完全不变（同一份 rank 与证据）。
    if pct >= 60:
        line = (f'最集中的薄弱面是<b>「{top["topic"]}」</b> —— '
                f'本组 {top["un"]}/{n_any} 人（<span class="hit">{pct}%</span>）涉及{tail}。')
    elif pct >= 35:
        line = (f'较集中的薄弱面是<b>「{top["topic"]}」</b> —— {top["un"]} 人（{pct}%）涉及{tail}；'
                f'其余按覆盖人数依次排列。')
    else:
        line = (f'本组暂无压倒性共性问题，按覆盖人数依次看即可，首条为'
                f'<b>「{top["topic"]}」</b>（{top["un"]} 人，{pct}%）{tail}；'
                f'结论以个体卡片为准。')
    if len(rank) > 1 and rank[1]["un"] >= max(3, top["un"] * 0.7):
        _s = rank[1]
        line += (f' 其次是<b>「{_s["topic"]}」</b>'
                 f'（{_s["un"]} 人，{round(100 * _s["un"] / n_any)}%）。')
    # v4.31.1：证据强度单独交代 —— 概览允许「1 条提及」上榜（跨学生聚合才有普遍性），
    #   但绝不能让读者把它当成和卡片同级别的结论，故弱证据必须显式标注。
    if top["chat"] and top["weak"] >= top["chat"]:
        line += (' <span class="mute">（群聊侧均为单条提及，属弱证据：'
                 '只说明这些人在群里提过，不足以单独定性；学生卡片只认 ≥2 条）</span>')
    elif top["weak"]:
        line += (f' <span class="mute">（其中 {top["weak"]} 人仅 1 条提及，'
                 f'其余 ≥2 条）</span>')

    # ---- 优先级表（v4.10.1：显示全部知识点，长尾不再截断 —— 上方的「高频知识点」明细表
    # 已删除，这里成为唯一承载知识点清单的地方，故必须完整）----
    rows = ""
    for i, x in enumerate(rank, 1):
        its = "、".join(
            f'{k} <span class="mute">{len(v["stu"])}人</span>'
            + (f'<b class="hard">实证错误 {len(v["hard_stu"])}</b>' if v["hard_stu"] else '')
            for k, v in x["items"][:2]) or "—"
        bd = (f'{x["err"]}' if x["err"] else "—") + (f' <span class="mute">/涉及 {x["board"]}</span>' if x["board"] else "")
        chat_cell = str(x["chat"]) if x["chat"] else "—"
        if x["weak"]:
            chat_cell += f' <span class="mute">({x["weak"]} 人仅 1 条)</span>'
        # v4.32：已闭环掌握的人数单独提示 —— 说明这部分人「问过但已经解决」，
        #   不该再被当成还在薄弱的证据（概览仍保留覆盖人数，只是把其中多少人已闭环讲清楚）
        if x["mst"]:
            chat_cell += f' <span class="mstn">其中 {x["mst"]} 人已掌握</span>'
        rows += (f'<tr><td class="rk">{i}</td><td class="nm">{x["topic"]}</td>'
                 f'<td class="n"><b>{x["un"]}</b>/{n_any} · {round(100 * x["un"] / n_any)}%</td>'
                 f'<td class="n">{chat_cell}</td>'
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
        '<div class="tadv-h">📊 学生情况概览 · 共性薄弱面</div>'
        f'<div class="tadv-sum">{line}</div>'
        '<table class="tadv-tab"><tr><th>优先</th><th>薄弱知识点</th><th>覆盖学生</th>'
        '<th>群聊提及</th><th>小黑板实证</th><th>典型题型 / 卡点</th></tr>'
        + rows + '</table>'
        + f'<div class="tadv-cap">「覆盖学生」= 群聊提及 ∪ 小黑板涉及，分母为本组 {n_any} 人；'
          f'「群聊提及」只统计<b>问题信号</b>消息（学生/家长说不会、提问，老师指出错误），'
          f'讲课长文本与收费/资料/进度说明不计入；'
          f'「小黑板实证」= 该知识点上读图判定有错误／卡点的学生数（本组小黑板读图覆盖 {n_bd} 人，'
          f'未被素材覆盖的学生不在分母内）。'
          f'标 <span class="mstn">N 人已掌握</span> 的，是这些人问过之后又有学生本人确认听懂／老师确认做对的记录。'
        + ('<b>本视角未接入小黑板读图数据，「实证错误」维度为空，请以群聊高频为准。</b>'
           if n_bd == 0 else '')
        + '</div>'
        + _focus_html(crows, boards_map)
        + (f'<details class="tadv-ev"><summary>📎 展开题型与原文出处'
           f'（{len(rank)} 个知识点 · 可回群 / 回图逐条核对）</summary>'
           f'<div class="in">{ev_html}</div></details>' if ev_html else '')
        + '<div class="tadv-note">排序：按「覆盖人数」降序；同覆盖时「有小黑板实证错误」的优先。'
          '<b>证据门槛分两档</b>：学生卡片只认「同一知识点 ≥2 条问题信号」，'
          '本表是<b>跨学生聚合</b>，所以「1 条提及 × 多个人」也会进来 —— '
          '凡是只靠单条提及的，行内会标「N 人仅 1 条」，请按弱证据看。'
          '题型旁标 <b class="hard">实证错误 N</b> 的，是读图判定<b>确实做错</b>的学生数（真的不会）；'
          '标「仅未完成／卡点」的，是<b>只是没写完</b>（课堂节奏 / 书写速度，非能力问题）——'
          '两者已按 v4.4 口径区分，不要混为一谈。文案只复述已发生的证据'
          '（群聊提问 / 老师指出 / 小黑板作答），不预测考情、不生成内容。</div>'
        '</div>')


# 注（v4.10.1）：原 `_topic_table_html()` 已删除 —— 它渲染的「高频知识点 · 数据明细」表
# 与「学生情况概览」主表的列（覆盖学生 / 群聊提及 / 小黑板实证）完全重合，属同源重复。
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
    """Tab2 的一个分组：**学生情况概览**（这批学生的共性薄弱面 ＋ 需要重点关注的人）。

    v4.30：Tab② 由「主讲备课建议」改名为「学生情况概览」—— 内容模块不变（仍是
    共性薄弱面表 + 重点关注学生），只把「给主讲备课」的措辞改成「给联席 / 教务看整体情况」。
    目的是让联席 / 教务一进来就能拿到三件事：**多少人、普遍卡在哪、谁最该先跟进**。

    v4.10.1：原先建议下方还挂了一张「高频知识点 · 数据明细」表，但它的列
    （知识点 / 群聊提及 / 小黑板实证 / 合计涉及 / 占比）与建议表的
    「薄弱知识点 / 覆盖学生 / 群聊提及 / 小黑板实证」**完全重合**（用户指出「这两部分重复」），
    故整表删除，只保留一张表；表本身已改为**显示全部知识点**，长尾（数列、解三角形等）不丢。
    """
    if not crows:
        return ""
    disp = 'style="display:block"' if is_all else 'style="display:none"'
    adv = teaching_advice(crows, boards_map)
    if not adv:
        adv = ('<div class="tadv"><div class="tadv-h">📊 学生情况概览 · 共性薄弱面</div>'
               f'<div class="tadv-sum">本组（{title}）暂无可用于概览的证据 —— '
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
                     + ktile(c_urge_fail, "催交未回", "urge", "orange",
                             "老师在群里催交过、其后群内未见「已交」回应（v4.28：飞书表已确认提交的不计入" 
                             "「未交」，是未回群消息）")
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
    out = [f'<button class="tab active" onclick="showTabIn(\'{panel_id}\',\'{prefix}\',-1,1)">'
           f'📊 全部（{n_all}）</button>']
    for i, (title, n, t) in enumerate(groups):
        cnt = f'{n}人' + (f' · {t}迟缓' if t else '')
        # 末位 1 = 用户点击（v4.41：教务视角点课程子标签时回写顶部全局课程标签）
        out.append(f'<button class="tab" onclick="showTabIn(\'{panel_id}\',\'{prefix}\',{i},1)" '
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
    ap.add_argument("--end", default=None,
                    help="分析窗口止 YYYY-MM-DD（含当天）；给了历史止日期时，报告按「起 ~ 止」表述（v4.16）")
    # 可选：在报告顶部打一条醒目横幅说明数据来源（如用合成/演示数据试跑时，
    # 避免看报告的人拿演示话术去真实群里核对而找不到）。
    ap.add_argument("--note", default=None, help="顶部数据来源/口径提示横幅（可选）")
    ap.add_argument("--chat-days", type=int, default=14, help="失联预警沟通轴窗口天数（默认近两周）")
    args = ap.parse_args()
    role, name = common.resolve_scope(args)
    global _WD
    _WD = args.workdir or "."
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
    global BOARDS_ON
    BOARDS_ON = bool(boards_map)
    if not BOARDS_ON:
        print("   ℹ️ 无小黑板读图数据（boards_summary.json 缺失或为空）→ 卡片不渲染该子块")

    # v4.19.3 飞书表学习记录（可选）：snapshot/feishuLearning.json（adapt_feishu.py 产出）
    #   ⚠️ 按 (studentNameNorm, productName) **复合键**索引 —— 同一学生可能上多门课，只按姓名会串课。
    #   ⚠️ 同时登记「去掉产品前缀」的备用键：报告侧 card 用的是 `course_raw`
    #      （`common.course_name()` 会剥掉 `2026Q-SX-` 前缀），而飞书侧存的是**带前缀全名** ——
    #      只按一种形态建键会全部落空（实测命中率 40/70 → 0/70）。
    #   读失败时静默跳过（不阻断报告），因为它是补充证据、不是必需表。
    FS_MAP.clear()
    FS_BY_NAME.clear()
    _fsp = os.path.join(wd, "snapshot", "feishuLearning.json")
    if os.path.exists(_fsp):
        try:
            _frecs = json.load(open(_fsp, encoding="utf-8"))
            for _f in _frecs:
                _n, _pn = _f.get("studentNameNorm"), _f.get("productName")
                FS_MAP[(_n, _pn)] = _f
                _alt = common.COURSE_PREFIX_RE.sub("", str(_pn or "")).strip()
                if _alt and _alt != _pn:
                    FS_MAP.setdefault((_n, _alt), _f)
                # v4.27：姓名级兜底（只给「加课 / 电话沟通」用）——
                #   预警卡的学生没有课程维度卡片，且这两类信息与课程无关，按姓名取即可。
                #   ⚠️ 不用于提交率/评级（那些必须复合键，否则会串课）。
                FS_BY_NAME.setdefault(_n, _f)
            # v4.28：第二轮登记「宽松姓名」兜底键 —— 飞书表里有学生名带状态后缀
            #   （`某生（回）` / `沈子琳（回）` / `王悦然（转）` / `胡曦月（回退）`），
            #   花名册侧是干净的 `某生`，精确键对不上 → 卡片上「提交情况」整行消失。
            #   ⚠️ 必须用 setdefault（精确键优先），且**只读匹配**，不改分档口径。
            for _f in _frecs:
                _n, _pn = _f.get("studentNameNorm"), _f.get("productName")
                _lo = common.norm_name_loose(_n)
                if not _lo or _lo == _n:
                    continue
                FS_MAP.setdefault((_lo, _pn), _f)
                _alt = common.COURSE_PREFIX_RE.sub("", str(_pn or "")).strip()
                if _alt and _alt != _pn:
                    FS_MAP.setdefault((_lo, _alt), _f)
                FS_BY_NAME.setdefault(_lo, _f)
        except Exception:
            FS_MAP.clear()
            FS_BY_NAME.clear()

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
    # ---------- v4.29：卡片顺序 = 学习分层（需重点关注 → 需关注 → 正常 → 良好）----------
    # 用户要求（并明确「很久之前说过」）：**需要关注的排在上方，正常的在下方**。
    # ⚠️ 这确实是回归，不是新需求：v4.0 把卡片右上角的红黄绿定档换成「学习分层徽章」时，
    #    排序也跟着从「定档」换成了「发言条数降序」→ 卡片位置与分层结论彻底脱钩
    #    （实测六C肖：首屏全是发言几位数的「正常/良好」生，需重点的人埋在几十张卡后面）。
    #    SKILL.md 里一直写着「红黄绿用于卡片排序」，但那份代码在 v4.0 就被换掉了 —— 文档对了、实现错了。
    # 同档内保持原有次序（响应迟缓 → 未解疑问 → 发言条数），它们与分层正交、仍是「该先看谁」的次级信号。
    # ⚠️ 分层必须**与 card_html 同源同参**（board 按姓名、fs 按复合键），否则会出现
    #    「卡上写需重点关注，人却排在正常堆里」的自相矛盾。
    def _level_of(x):
        if "_lv" not in x:
            _n = common.norm_name(x["stu"])
            _b = (boards_map or {}).get(common.norm_name(x["stu"]))
            _f = (FS_MAP.get((_n, x["g"].get("productName")))
                  or FS_MAP.get((_n, x.get("course_raw"))))
            _k, _, _ev = common.learning_level(x["s"], _b, common.fs_stats(_f))
            x["_lv"] = _k
            # 同档内的次级排序：累计得分越低越靠前（分越低 = 证据越差，越该先看）。
            # ⚠️ 不能用「发言条数降序」当次级键 —— 那会把「话多但问题不大」顶到前面，
            #    真正「话少 + 分低」的人沉下去，等于换了个方式打乱优先级。
            x["_lvs"] = sum(d for _, _, d in _ev)
        return x["_lv"]

    rows.sort(key=lambda x: (common.LEVEL_RANK.get(_level_of(x), 9),
                             x.get("_lvs", 0),
                             -x["score"][0], -x["score"][1], -x["score"][2]))

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

    # v4.41：教务视角的「课程」全局筛选标签条（其余视角为空，不渲染）
    edu_chips, _covf = "", None

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

        # ── v4.41：页面顶部「课程」全局筛选标签条（标签切换，不再用下拉框）──
        #   课程 = 整体筛选维度：切换后 顶部 KPI / Tab① 档位与名单 / Tab②③ 学生范围 同步变化。
        def _ck_kpi(crows, pns):
            _cv = ({k: v for k, v in cov.items() if (v.get("productName") or "") in pns}
                   if pns is not None else cov)
            _t = Counter(v.get("tier") for v in _cv.values())
            _rt = [x for x in crows if (x["s"].get("recent3") or {}).get("items")]
            return ({"n": len(_cv) or len(crows),
                     "lost": _t.get("失联", 0), "anc": _t.get("出勤但无沟通", 0),
                     "slow": sum(1 for x in crows if x["slow"]),
                     "unres": sum(1 for x in crows if x["s"]["unresolved"]),
                     "rt": len(_rt),
                     "rti": sum(len(x["s"]["recent3"]["items"]) for x in _rt)}, _t)

        def _chip_label(ck):
            """标签用短名：去掉学科前缀、去掉「老师」二字；完整名放 title。"""
            parts = [p for p in str(ck).split("·") if p]
            if parts and "数学" in parts[0]:
                parts = parts[1:]
            s = "·".join(parts) or str(ck)
            return s.replace("老师）", "）")

        eduk, covk, key_by_pn = {}, {}, {}
        _d0, _t0 = _ck_kpi(rows, None)
        eduk[""], covk[""] = _d0, {"n": _d0["n"], "tiers": dict(_t0)}
        _chips = ['<button class="cchip on" data-ci="-1" data-k="" '
                  f'onclick="eduCourse(-1)">全部课程<span class="ccn">{len(rows)}</span></button>']
        for _i, _ck in enumerate(course_order):
            _pns = {(x.get("g") or {}).get("productName") or "" for x in by_course[_ck]}
            _pns.discard("")
            for _pn in _pns:
                key_by_pn[_pn] = _ck
            _d, _t = _ck_kpi(by_course[_ck], _pns)
            eduk[_ck] = _d
            covk[_ck] = {"n": _d["n"], "tiers": dict(_t)}
            _chips.append(
                f'<button class="cchip" data-ci="{_i}" data-k="{_html.escape(_ck)}" '
                f'onclick="eduCourse({_i})" title="{_html.escape(_ck)}｜分析 {len(by_course[_ck])} 人 · '
                f'在册 {_d["n"]} 人">{_html.escape(_chip_label(_ck))}'
                f'<span class="ccn">{len(by_course[_ck])}</span></button>')

        _cjs = ("<script>\nwindow.EDUK=" + json.dumps(eduk, ensure_ascii=False)
                + ";\nwindow.EDUALL=" + json.dumps(eduk[""], ensure_ascii=False)
                + ";\nwindow.EDUC='';\nwindow.eduCourse=function(i){"
                "var btns=document.querySelectorAll('.cchip');"
                "for(var j=0;j<btns.length;j++){btns[j].classList.toggle('on',"
                "btns[j].getAttribute('data-ci')===String(i));}"
                "var b=document.querySelector('.cchip[data-ci=\"'+i+'\"]');"
                "var ck=b?(b.getAttribute('data-k')||''):'';"
                "window.EDUC=(i<0)?'':ck;"
                "var d=(i<0)?window.EDUALL:window.EDUK[ck];"
                "function sn(id,v){var e=document.getElementById(id);if(e&&v!==undefined)e.textContent=v;}"
                "if(d){sn('topn-roster',d.n);sn('topn-lost',d.lost);sn('topn-anc',d.anc);"
                "sn('topn-slow',d.slow);sn('topn-unres',d.unres);sn('topn-rt',d.rt);sn('topn-rti',d.rti);"
                "var kl=document.getElementById('topk-lost'),ka=document.getElementById('topk-anc'),"
                "kr=document.getElementById('topk-rt');"
                "if(kl){kl.classList.toggle('red',d.lost>0);kl.classList.toggle('green',!d.lost);}"
                "if(ka){ka.classList.toggle('red',d.anc>0);ka.classList.toggle('green',!d.anc);}"
                "if(kr){kr.classList.toggle('red',d.rt>0);kr.classList.toggle('green',!d.rt);}}"
                "if(typeof covApply==='function')covApply(i<0?'':ck);"
                "function pshow(id,on){var p=document.getElementById(id);if(p)p.style.display=on?'block':'none';}"
                "window._EDUSYNC=1;"
                "if(i<0){pshow('t2-co',1);pshow('t2-course',0);pshow('t3-co',1);pshow('t3-course',0);"
                "if(typeof showTabIn==='function'){showTabIn('t2-co','t-co',-1);showTabIn('t3-co','co',-1);}}"
                "else{pshow('t2-course',1);pshow('t2-co',0);pshow('t3-course',1);pshow('t3-co',0);"
                "if(typeof showTabIn==='function'){showTabIn('t2-course','t-c',i);showTabIn('t3-course','c',i);}}"
                "window._EDUSYNC=0;"
                "if(typeof eduApply==='function')eduApply();"
                "var cur=document.getElementById('cfCur');"
                "if(cur)cur.innerHTML=(i<0)?'当前：全部课程':"
                "('当前：<b>'+(b?b.getAttribute('title').split('｜')[0]:'')+'</b>');};\n</script>")

        edu_chips = (f'<div class="cfilter"><span class="cflabel">课程</span>'
                     f'<div class="cfchips">{"".join(_chips)}</div>'
                     f'<span class="cfcur" id="cfCur">当前：全部课程</span></div>'
                     f'<div class="cfhint">课程为<b>全局筛选维度</b> —— 切换后顶部 KPI、'
                     f'Tab① 失联预警的档位与名单、Tab②③ 的学生范围同步变化；'
                     f'标签内数字为「有聊天记录、进入本次分析」的人数（悬停看该课程在册人数）。</div>'
                     + _cjs)
        _covf = {"key_by_pn": key_by_pn, "kpi": covk, "all": covk.get("")}

        edu_ov = edu_overview_block(rows, course_order)   # v4.39：教务核心数据（续报/科目/扩科）
        # v4.42：维度切换条与「按课程」子标签整体删除（用户拍板）—— 课程一律走顶部全局标签条；
        #   Tab2/Tab3 常态只保留「按联席」分组；选中具体课程时切到隐藏的课程面板（无标签栏）。
        topic_panels = f'''{edu_ov}
<div class="dim-panel" id="t2-co">
  <div class="tabs">{_tabs_html('t2-co', 't-co', len(rows), _og)}</div>
  {''.join(t2_co)}
</div>
<div class="dim-panel" id="t2-course" style="display:none">
  {''.join(t2_c)}
</div>'''
        card_panels = f'''
<div class="dim-panel" id="t3-co">
  <div class="tabs">{_tabs_html('t3-co', 'co', len(rows), _og)}</div>
  {''.join(t3_co)}
</div>
<div class="dim-panel" id="t3-course" style="display:none">
  {''.join(t3_c)}
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
    # v4.23 飞书表覆盖：报告内多少人有表内记录 / 多少人有课次数据（0 课次 = 只有档案）
    _fs_seen, _fs_has, _fs_extra = set(), set(), set()
    for _r in rows:
        _n = common.norm_name(_r["stu"])
        _f = (FS_MAP.get((_n, (_r.get("g") or {}).get("productName")))
              or FS_MAP.get((_n, _r.get("course_raw"))))
        if _f:
            _fs_seen.add(_n)
            if common.fs_stats(_f):
                _fs_has.add(_n)
            elif extra_line(_f):
                _fs_extra.add(_n)          # v4.27：无课次，但有加课 / 电话沟通记录
    _fs_note = ""
    if _fs_seen:
        _fs_note = (f'飞书表：报告内 {len(rows)} 名学生中 {len(_fs_seen)} 人有表内记录'
                    f'（{len(_fs_has)} 人有课次数据，已并入 ① 学习情况）')
        _rest = len(_fs_seen) - len(_fs_has)
        if _rest:
            _fs_note += f'；余 {_rest} 人表里只有档案、无课次记录'
            if _fs_extra:
                _fs_note += f'（其中 {len(_fs_extra)} 人有加课 / 电话沟通记录，已汇总到学生信息上方）'
    cov_block = coverage_block(cov, roster_n, analyzed_n, args.chat_days,
                               cov_meta.get("degraded"), _fs_note, cov_filter=_covf)
    kpi_cov = ""
    _cov_badge = ""
    if cov:
        # v4.10：顶部 KPI 卡全部可点击 —— 失联 / 出勤但无沟通 → 切到「失联预警」Tab 并展开对应档位
        # v4.41：加 id，供顶部课程标签切换时重算这些数字
        kpi_cov = (f'<div class="kpi {"red" if lost_n else "green"} clickable" id="topk-lost" '
                   f'onclick="topKpi(\'lost\')" '
                   f'title="点击查看失联名单"><div class="n" id="topn-lost">{lost_n}</div>'
                   f'<div class="l">失联（无出勤·无沟通）</div></div>'
                   f'<div class="kpi orange clickable" id="topk-anc" onclick="topKpi(\'anc\')" '
                   f'title="点击查看「出勤但无沟通」名单"><div class="n" id="topn-anc">{anc_n}</div>'
                   f'<div class="l">出勤但无沟通</div></div>')
        _warn_n = lost_n + anc_n
        _cov_badge = f'<span class="mcnt {"warn" if _warn_n else "ok"}">{_warn_n}</span>'

    # v4.16：分析窗口文案 —— 显式给了 --end 且不是今天，就按「起 ~ 止」表述（用户指定的截止日必须复述出来）；
    # 只有「没给 end」或「end 正好是今天」才写「至今」。⭐ v4.23：原来用 `end < today`，
    # 用户传了「明天」当截止日（如 10.17 跑「9.1-10.18」）就会被写成「至今」，抹掉了他要的区间。
    _today = datetime.now().date().isoformat()
    win_txt = (f"{args.window} ~ {args.end}"
               if (args.end and args.end != _today) else f"{args.window} 至今")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}（{role_cn}）学生学情分析</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<h1>{name}（{role_cn}）· 学生学情分析{ '（双维度：按课程 / 按联席）' if is_edu else ('（课程维度 · 按联席下钻）' if is_course else '（联席维度 · 按课程下钻）') }</h1>
<div class="sub">分析窗口：聊天内容 {win_txt} ｜ 出勤：学生管理后台「课节考勤」权威数据 ｜ 学科：{subject_line} ｜ 角色：{role_cn} ｜ 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
{ f'<div class="datanote">⚠️ {args.note}</div>' if args.note else '' }
{edu_chips}
<div class="kpis">
  <div class="kpi clickable" onclick="topKpi('info')" title="点击查看全部学生"><div class="n" id="topn-roster">{roster_n}</div><div class="l">{scope_label}学生（{'总人数' if cov else '全量'}）</div></div>
  {kpi_cov}
  <div class="kpi red clickable" onclick="topKpi('slow')" title="点击筛选「响应迟缓」的学生"><div class="n" id="topn-slow">{slow_n}</div><div class="l">响应迟缓群（聊天分析）</div></div>
  <div class="kpi orange clickable" onclick="topKpi('unres')" title="点击筛选「待回复」的学生"><div class="n" id="topn-unres">{unres_n}</div><div class="l">待回复群（聊天分析）</div></div>
  <div class="kpi {'red' if rt_n_stu else 'green'} clickable" id="topk-rt" onclick="topKpi('recent3')" title="点击筛选「近 3 天待回应」的学生"><div class="n" id="topn-rt">{rt_n_stu}</div><div class="l">近3天待回应（<span id="topn-rti">{rt_n_items}</span> 项）</div></div>
</div>
<div class="maintabs">
  <button class="mtab active" data-t="t1" onclick="showMain('t1')">🚨 失联预警{_cov_badge}</button>
  <button class="mtab" data-t="t2" onclick="showMain('t2')">📊 学生情况概览</button>
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
• <b>报告结构（v4.10 起为三大 Tab）</b>：① <b>🚨 失联预警</b> —— 总人数覆盖统计与分档名单；② <b>📊 学生情况概览</b> —— 该维度下学生的整体情况：多少人、普遍卡在哪、谁最该先跟进（含覆盖人数、双源证据、典型题型与原文出处）；③ <b>📋 学生学情 &amp; To-Do</b> —— 逐生卡片与筛选。顶部 KPI 卡<b>全部可点击</b>：失联 / 出勤但无沟通 → 切到 Tab① 并展开对应档位名单；响应迟缓 / 待回复 / 近 3 天待回应 → 切到 Tab③ 并筛出对应学生。<br>
• 取数（群聊）：后台 <code>wxworkSessionStat/getList</code> 月维度与日维度并集得到该{role_cn}的全部学生群；<code>wxworkChat/getChatList</code> 分页取每群<b>全量</b>消息（倒序；单群 500 条上限已取消，按 <code>total</code> 逐页拉满），文本与语音转写均纳入。<br>
• 分析窗口：仅分析 <b>{win_txt}</b>的聊天内容。<br>
• 出勤（后台权威）：来自学生管理后台「我的学生 → 跟进学情 → 课节考勤」接口 <code>cClassAttend/getCClassAttendList</code>，逐节课记录 出勤✓ / 缺勤✗ / 是否看回放 + 回放有效时长 + 上课日期。<br>
• 出勤口径（v3.8 修正）：<b>看回放也算有效出勤</b> —— 有效出勤 = 到课 + 未到课但已看回放；只有「既没到课、也没看回放」才算<b>真缺课</b>（修正前把已看回放的节次也计成缺课）。呈现统一为<b>一句话</b>：如「出勤 4/5 次（含回放 2 次），缺课 1 次，第 5 节既没到课也没看回放」。判定：全勤 → 🟢；缺课 ≥2 节 → 🔴；缺课 1 节 → 🟡。
<b>该口径同时用于 Tab① 的失联分档</b>：分档里的「有出勤记录」= <b>有效出勤（含看回放）</b>，出勤文案也会写明「其中 N 次为看回放补齐」；预警卡结论按「到课 / 仅看回放」分别措辞，不把「全靠回放」写成「正常上课」。<b>权重按角色区分：教务（为续报负责）在出勤一句话下另附折叠的逐节明细与「是否需关注」；联席（为学习效果负责）只有这一句话，不再单独成块，也不参与联席定档。</b><br>
• 请假备注：仅摘录学生在群内自述的请假/看回放缘由，后台无请假原因字段，具体以教务记录为准。<br>
• 报告结构（{view_cn}，v4.0 重排 · 去重）：<b>① 学习情况</b> —— 结论前置：<b>分层结论只出现在卡片右上角</b>（徽章：需重点关注 / 需关注 / 正常 / 良好），由四类证据<b>综合判档</b>：① 课堂小黑板作答（课次·轮次·空白/实质错误/仅未完成占比·典型错误·趋势）② 作业闭环（催交是否交、批改后是否订正）③ 老师反馈（群里指出问题 / 正面评价，<b>按密度归一化</b>）④ <b>学生成绩</b>（群聊里定期收集的月考 / 期中成绩，<b>最强证据</b>）；作业闭环做对、小黑板完整作答且无错都会加分，避免人人被判重点关注。判档依据与口径<b>折叠</b>在①末尾「学习分层判定依据」，<b>逐条给出加减分与累计总分</b>，点击展开即可核对。① 正文只保留<b>薄弱知识点</b>（<b>「聊天高频话题 × 小黑板实证」交叉验证</b>：两者都命中 → 🖍 双证据，仅小黑板命中 → 🖍 实证薄弱；聊天侧「错误类型」并入本行尾注，<b>不再单列</b> —— 它与薄弱点同源于高频话题，属同源重复）与<b>优势</b>；其下<b>课堂小黑板佐证</b>只留「一句话作答概况（轮数 / 分布 / 趋势）＋ 典型错误」，<b>逐次课情况表与逐轮明细表均默认折叠</b>，需要时点击展开。<b>② 配合度</b> —— 配合度结论 ＋{'<b>联席四维</b>（互动状态 / 催交作业是否交 / 批改后是否订正 / 提问意愿）＋' if not is_edu else ''}<b>出勤一句话</b>（{'教务另附折叠的逐节明细' if is_edu else '联席不再单独成块'}）＋ 风险信号条（不再另设「最该盯」独立块 —— 它与③第一条本是同一动作，v4.0 去重）。<b>③ {blk4_cn} To-Do</b>（优先级动作＋{'沟通节奏' if is_edu else '追踪节奏'}＋改善信号，<b>按优先级排序、第 1 条即「最该盯」并高亮；全报告动作清单只此一处</b>）——{'教务视角的 To-Do 面向「家长沟通 / 反馈学生问题 / 建立信任 / 推动续报」' if is_edu else '联席视角的 To-Do 严格按<b>联席四维</b>排序：互动状态 → 答疑闭环 → 催交作业 → 批改后订正 → 提问意愿；若小黑板暴露「多轮未收尾 / 多轮空白未提交」且聊天侧无更高优先级问题，该结论会置顶为「最该盯」。<b>出勤/请假不再进入联席 To-Do</b>（归教务关切）'}。{'教务视角支持双维度：① 按课程维度（课程 Tab 切换，每个课程下可「筛选联席」下钻到具体联席）；② 按联席维度（按下属联席下钻，学生卡标注所属联席）。' if is_edu else ('课程维度：单一课程下跨多个联席，顶部「筛选联席」下钻到具体联席，学生卡标注所属联席。' if is_course else '联席维度：同一联席名下的课程 Tab 切换，逐课程查看该联席负责的学生。')}<br>
• 小黑板目录层级（v3.8 明确）：第 1 层文件夹 = <b>一次课</b>（命名含上课日期，如 0906邵小黑板 / 0927邵清北打穿题）；第 2 层 = <b>该次课下的每一轮</b>（时间戳目录）；第 3 层 = 图片（带红笔批改痕迹的是联席批改后的图）。故报告按「课次 → 轮次」两层组织，轮次按第 2 层目录去重计数。<br>
• <b>配合度口径（v4.5 归因修正）</b>：结论由「学生自驱」＋「家长是否出面」两部分构成，且<b>家长侧只在学生自驱不足时才判负面</b>。修正的伪结论：<b>学生本人在群里很活跃时，家长往往就隐身了</b> —— 这不是「家长配合一般」，而是「家长没必要出面」，旧口径把「家长发言少」直接映射为负面定级，属<b>归因错位</b>。现口径：① 家长出面量改用<b>硬事实</b> —— 群内「除主导发言人外的其他发言人条数」（聊天数据带发言人昵称，可在卡片里逐条核对，<b>不再用会被学生本人话语污染的启发式分数</b>）；② 学生自驱达标（≥中等）时，家长少出面只做中性描述、<b>不下负面结论</b>；③ 家长零发言痕迹时改判「建议先联系家长确认」，不硬扣「配合偏弱」。<br>
• <b>学习分层口径（v4.4 重校）</b>：四档 <b>需重点关注（≤-3）／需关注（-2~-1）／正常（0~+1）／良好（≥+2）</b>，由四类证据加减分求和后落档，改动的三处 ——<br>
&nbsp;&nbsp;<b>(1) 老师反馈改为密度归一化</b>：旧版按「指出问题绝对次数」扣分（≥3 处即 -2），但真实数学群里老师高强度纠错是<b>教学常态</b>（实测三位学生分别在 2/8/8 次课里被指出 11/12/9 处），导致人人触发、人人扣分；现改为「每 10 轮小黑板（无小黑板数据时按每 100 条可分析消息）」的指出密度，≥10 → -2、≥5 → -1，未超基线不扣。<br>
&nbsp;&nbsp;<b>(2) 小黑板区分「仅未完成收尾」与「实质错误」</b>：「未完成收尾」在真实读图标注里占 25/33，大量轮次是「思路对了但没写完」——属课堂节奏 / 书写速度，<b>不记在能力账上</b>（仅在占比 ≥85% 时轻提示 -1）；只有剔除它之后的<b>实质错误</b>（计算错误 / 判断错误 / 思路偏差 / 推导跳步 / 漏解…）才按占比扣分（≥30% → -2、≥20% → -1）。空白未提交归参与度；「完整作答且无错」占比 ≥60% / ≥40% 给 +2 / +1。<br>
&nbsp;&nbsp;<b>(3) 新增学生成绩为最强证据</b>：从群聊里定期收集的月考 / 期中成绩中识别（<b>只匹配、不生成</b>，认不出即「未知」，绝不用裸数字硬凑）。口径按业务要求 —— <b>成绩好（得分率 ≥85%）→ 即使平时作业不做，也属学习效果较好，档位<b>托底「良好」</b>；成绩偏低（<65%）→ 即使作业都交、很配合，也属「学习效果不好」，档位<b>封顶「需关注」</b>；成绩中等（65%~85%）→ +1</b>。无满分标注时按学科推断（数学 150、其余 100）并在依据里注明「按 X 分制推断」。<br>
• <b>卡片排序（v4.29）</b>：卡片顺序 = <b>学习分层</b> —— 需重点关注 → 需关注 → 正常 → 良好，<b>最该处理的人排在最上面</b>；同档内按「累计得分从低到高」（分越低 = 证据越差），再按「响应迟缓 → 未解疑问 → 发言量」，确保优先级不会因为「谁话多」被打乱。<br>
• 定档规则（v4.0：红黄绿定档<b>不再渲染、也不再参与排序</b>，仅为兼容保留；卡片右上角只展示「学习分层」，避免两个分层标签让人混淆）：🔴 红=聊天分析判定「响应迟缓」（学生提问后 ≥24 小时才见老师回复）或「需回复」（会话尾部仍有未获回应提问）；🟡 黄=有未解疑问、作业负面信号、负面情绪{'、出勤异常（仅教务）' if is_edu else ''}；🟢 绿=其余正常推进。<br>
• 「是否需回复」结论：由聊天记录分析得出，不再依赖后台「是否回复」列（实测存在误差）——以「最后一条老师消息之后是否仍有学生/家长实质提问」为准；该结论同时驱动「待回复群」KPI 与定档红。<br>
• 响应时效：同样弃用后台「超时」标记（实测与聊天事实不符，如 6 分钟即回复的群被标「超时 19 小时」），改由聊天记录自行推算 —— 以「学生/家长的一条实质提问」到「其后第一条老师消息（含图片/讲义等任意动作）」的间隔为一次等待，同一轮多条连续提问只按首条起算；≥24 小时判「响应迟缓」并计入定档红，≥12 小时在卡片提示「最长响应等待」（不计定档），窗口末尾老师仍未回复的提问归入「需回复」。<br>
• <b>近 3 天待回应（v3.9 新增 · v4.0 改为顶部折叠入口）</b>：以窗口内最新一条消息为基准往前 3 天，逐条扫学生/家长的实质提问 —— ① <b>忘记回复</b>：提问之后（截至窗口末尾）群里再无任何老师消息；② <b>问题未解决</b>：老师确有回复，但回复命中延宕词（稍后 / 晚点 / 明天 / 回头…＝应允未答），或老师回复后 24 小时内学生就同类疑问再次追问（没讲透）。全量结果<b>汇总在报告顶部的「🚨 近 3 天待回应 N 项」入口条</b>（给管理者 / 教务的统一视图，<b>默认收起，点击展开</b>明细表：学生 / 日期 / 类型 / 提问原文 / 依据 / 建议动作），并在对应学生卡头部加红徽标、<b>② 配合度内置置顶清单</b>；同时生成最高优先级 To-Do（联席＝当天必须回应并跟进到学生确认「懂了」；教务＝提醒联席闭环并向家长同步进展）。全量无此类情况时入口条显示绿色「检查通过」。<br>
• <b>学生情况概览（v4.9 新增「主讲备课建议」· v4.30 改名并改为联席 / 教务视角 · v4.10.1 收敛为单表）</b>：回答「<b>这批学生普遍卡在哪、谁最该先跟进</b>」，而不是「主讲这节课该讲什么」。两组信息：① <b>共性薄弱面</b> —— 按<b>覆盖学生数降序</b>排列（普遍性优先），同覆盖时<b>有小黑板实证错误的优先</b>（小黑板是学生真实作答，比「群里提过」硬），每条给出「典型题型／卡点」，点开可看原文出处；② <b>需要重点关注的学生</b> —— 互动不足 ＋ 课堂练习没落实的人，先互动后练习排序，附「可以做的事」与原文依据。目的是<b>三分钟内看完这一批人的整体状态</b>，要逐个下钻再进 Tab③。<b>本表即为双源呈现</b> —— 此前「高频知识点」表只统计聊天命中（<code>extract.top_topics</code>），小黑板的实证错误完全没进；现并列展示「群聊提及」（聊天中命中该知识点的学生数）与「小黑板实证」（读图判定该知识点<b>有错误／卡点</b>的学生数），「覆盖学生」为两源并集。⚠️ <b>v4.10.1 删除了原先并列的「高频知识点 · 数据明细」表</b> —— 它的列（知识点／群聊提及／小黑板实证／合计涉及／占比）与主表完全重合（用户指出「这两部分重复了，保留上面的即可」），故主表改为<b>显示全部知识点</b>，长尾（数列、解三角形等）不再截断。③ ⚠️ <b>题型证据分硬／软</b>：标「实证错误 N」= 读图判「有错」或存在<b>非「没写完」</b>类实质错误（真的不会）；标「仅未完成／卡点」= 只是没写完（课堂节奏／书写速度，非能力）—— 与 v4.4 学习分层口径一致，不混为一谈。<b>读图模型对同一件事有「未完成收尾」与「收尾未完成」两种词序，后者才是真实语料主流写法（实测 176 条错误标注中占 94 条），故判定统一用包含式匹配（<code>common.is_finish_err</code>），精确匹配会静默漏掉最常见的写法。</b>④ 反编造：题型名只能来自 <code>FINE_TYPES</code> 词表命中或字段原文（形如「课堂作答卡点：…」），无证据时如实说明、留空，<b>绝不硬凑题型名</b>；不预测考情、不生成内容。<br>
• <b>总人数覆盖与失联预警（v4.8 新增 · 报告顶部独立区块）</b>：报告主体的学生卡<b>只覆盖「有聊天记录」的学生</b>，而总人数名单往往明显更大 —— 差额恰恰是最需要预警的一批人，本区块专门收口。判定用<b>两轴</b>：① 沟通轴（聊天，取近 {args.chat_days} 天）② 学习轴（出勤，取报告窗口全窗口）。分档：<b>失联</b>＝无聊天记录且无出勤记录；<b>出勤但无沟通</b>＝无聊天记录但有出勤记录（学生在正常上课，缺的是企业微信沟通通道）；<b>单向沟通</b>＝有聊天记录但学生·家长实质发言为 0（仅老师输出）；<b>低互动</b>＝学生·家长实质发言 1~2 条。⚠️ <b>判定「失联」一律以出勤为准，不以群是否存在为准</b> —— 群列表接口 <code>wxworkSessionStat/getList</code> 只收录有响应时效记录的群，实测有损（教务视角名单 243 人、群仅 144，缺口分散在全部 7 门课，其中 93 人仍在正常出勤），若拿群判失联会一次性误报约四成学生。群「采到与否」仅作根因标签展示。两轴窗口必须分开：出勤本稀疏，若也只看近两周，会把已结课学生全部误判为「无出勤」→ 误报失联。<br>
• <b>预警卡（v4.12 落地 · Tab③ 学生学情）</b>：常规学生卡来自「有聊天记录」的学生，<b>在册、却一条聊天记录都没有的学生在 Tab③ 原本完全缺席</b>。v4.12 把这批人（失联 / 出勤但无沟通）从覆盖率快照 <code>coverage.json</code> 里捞出来，以<b>左侧红边预警卡</b>呈现：把<b>沟通轴</b>（群在不在、有没有人说话）与<b>学习轴</b>（出勤 ＋ 小黑板作答）两句话摊开写清，给出<b>结论</b>与<b>可直接执行的动作</b>，并逐项标注<b>数据来源</b>（在册花名册 / 群列表 / 聊天 / 小黑板 各自 ✓ 或 ✗），便于回后台、回文件夹逐字核对。🔒 <b>防伪铁律</b>：学习轴一旦有证据（有出勤，或有小黑板作答）→ 结论只写「<b>企业微信沟通通道失效</b>」，<b>绝不写「学生流失」</b>（实测本课「出勤但无沟通」学生全部正常上课，如刘庭岩出勤 5/5、小黑板作答 21/29 张）；只有「既无沟通、也无出勤与小黑板」才写「无学习痕迹 · 需人工核实就读状态」。⚠️ <b>v4.14 起预警卡默认收起、并置于本组常规学生卡的下方</b> —— 常规学生卡才是主要信息，一进 Tab③ 不应被红卡占满首屏。展开入口有两处：本组底部的「⚠️ 另有 N 名在册学生未进入学情分析」入口条（点击就地展开 / 收起），以及上方 KPI 卡「<b>未触达预警</b>」（点击隐藏常规卡、只留预警卡）；点「学生数」或再点一次即恢复。搜索命中预警卡时该区自动展开。<br>
</p></details>
</div>
<script>
// ===== v4.10：三大 Tab（① 失联预警 ② 学生情况概览 ③ 学生学情 & To-Do）=====
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
function showTabIn(panelId, prefix, idx, user){{
  var pn=document.getElementById(panelId); if(!pn) return;
  pn.querySelectorAll('.grp').forEach(function(el){{el.style.display='none';}});
  pn.querySelectorAll('.tab').forEach(function(el){{el.classList.remove('active');}});
  var el=document.getElementById(prefix+'-'+(idx<0?'all':idx)); if(el) el.style.display='block';
  var tabs=pn.querySelectorAll('.tab');
  if(tabs.length){{ (idx<0?tabs[0]:tabs[idx+1]).classList.add('active'); }}
  // v4.41：用户点 Tab②③ 的课程子标签 → 回写顶部「课程」全局标签（同一状态，避免两处打架）
  if(user && !window._EDUSYNC && typeof eduCourse==='function' && (panelId==='t2-course'||panelId==='t3-course')){{
    eduCourse(idx);
  }}
}}
// 维度切换（教务双维度）；where = 't2'（概览 Tab）| 't3'（学生 Tab）
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
    try:
        main()
    finally:
        # 使用统计（每次执行最多一次；失败不影响主任务）。run_all 已认领时此处自动跳过。
        try:
            import usage_report
            usage_report.report_once(usage_report.workdir_from_argv())
        except Exception:
            pass
