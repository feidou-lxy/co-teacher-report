"""处方层（v4.86）：把「诊断」变成「下一步做什么」。

设计约束（用户 2026-10-31 定，别照抄到别处）：
  1. **不动判定层**。本模块只读 `analysis_merged.json` / 小黑板读图结果，
     不重新判定任何东西，也不写回任何判定字段。回退 = 不调用它。
  2. **处方必须可复现**。文字由「固定模板 + 现场证据（题型名 / 老师原话 / 日期）」拼装，
     不让模型自由发挥 —— 同一份数据跑两次，结果必须一字不差。
     模板里能变的部分只有：知识点名、题型名（字段原文）、原话（证据原文）、日期。
  3. **按分层配额**。需重点关注 2 条、需关注 1 条、正常/良好 0 条。
     74 人 × 3 条 = 222 条等于没重点；配额后才 40 条左右，才是做得完的量。
  4. **允许「无动作」**。良好 / 正常的学生不出处方，这是这套东西能用下去的前提。
  5. **闭环单位是「一个知识点」**。任务身份 = 学生 + 知识点（归一后），
     同一学生同一知识点只有一条，下次不新增、只加轮次（去重靠这个）。
  6. **只读不写**。业务自己在飞书里记「我讲了 / 布置了」，本模块只读取回，
     读不到就按「待处理」显示，并在概览里显式说明，不猜、不伪造状态。

角色分工（2026-10-31 讨论定稿）：
  - 主讲：班级维度，按课程进度讲「当期 × 多人」的共性点；「遗留」不进正课，走补丁包。
  - 联席：学生维度，追到「会了为止」，遗留点由他扛（主讲不管历史遗留）。
  - 教务：沟通维度（谁要联系、问什么），本模块不产出处方 —— 那部分沿用既有 To-Do。

「当期 / 遗留」判据（只用现有字段，不引新数据源）：
  - 遗留 ①：学生曾确认听懂（`topic_mastered` 有记录），之后同一知识点又出错 → 讲了没会。
  - 遗留 ②：该知识点的错误证据首末跨 ≥14 天 → 跨周期没解决。
  - 其余为当期。
"""
import csv
import os
import re

# ── 口径常量 ────────────────────────────────────────────────────────────
TYPE_CUR = "当期"
TYPE_LEG = "遗留"

QUOTA = {"red": 2, "orange": 1, "green": 0, "blue": 0}

LEG_SPAN_DAYS = 14      # 首末错误证据跨 ≥14 天 → 遗留
DETECT_DAYS = 7         # 「会了」的检测窗口
DRILL_N = 3             # 同类题条数

TASKLOG_TPL = "处方任务表_{}.csv"   # 按维度命名：同一目录下多个联席/课程各一份，避免串表
TASKLOG_NAME = "处方任务表.csv"

STATUS_TODO = "待处理"


# ============================================================
# 一、证据 → 事实（每个知识点一条）
# ============================================================
def _d10(x):
    """任意日期串 → 'YYYY-MM-DD'（取不到返回空串，绝不猜）。"""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(x or ""))
    return m.group(0) if m else ""


def _days(a, b):
    """两个 YYYY-MM-DD 相差天数；任一缺失返回 None。"""
    if not a or not b:
        return None
    try:
        from datetime import date
        y1, m1, d1 = (int(v) for v in a.split("-"))
        y2, m2, d2 = (int(v) for v in b.split("-"))
        return (date(y2, m2, d2) - date(y1, m1, d1)).days
    except Exception:
        return None


def _clip(t, n=26):
    """原话截断：保留可核对的前半句，加了省略号说明被截。"""
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    t = t.replace('"', "”")
    return t if len(t) <= n else t[:n] + "…"


def _evi_core(t, n=26):
    """从读图 / 聊天证据里抽「错在哪」那一段。

    读图原始串形如 `题型：X｜错误：Y` / `原题：X｜卡点：Y`。直接从头部截断，
    会把最关键的错误描述截掉 —— 实测 62/76 条都断在「错误…」，等于没说。
    题型另有单列，所以这里只留错误描述，不丢信息。
    """
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    m = re.search(r"(?:错误|卡点|错因|问题)[:：]\s*(.+)$", t)
    if m and m.group(1).strip():
        t = m.group(1).strip()
    return _clip(t, n)


def topic_facts(stu, s, board, std, merge_fine, fine_from_board):
    """把一名学生的证据整理成「每个知识点一条事实」。

    `std` 是 report._std_topic（知识点名 → 后台 L1 模块名），由调用方注入，
    避免 prescribe 反向 import report（会成环）。

    ⚠️ 两级证据都要吃：
      ① `fine`（题型级，聊天 + 小黑板读图合并）→ 能具体到「哪个题型」；
      ② `top_topics` / `topic_signals`（知识点级）→ 只知道「卡的模块」，说不出题型。
    只用 ① 会让一批人（实测张巍名下 13 人）明明有知识点信号却开不出处方 ——
    他们的档位来自出勤 / 空白 / 作业，而群里只提到模块名、没到题型粒度。
    这种情形照样出处方，只是题型列留空、讲法退回模块级，不硬凑题型名。

    返回 [dict]，字段：
      topic     归一后的知识点
      item      题型 / 卡点（FINE_TYPES 词表或字段原文；取不到就是空，不编造）
      kind      证据来源（学生提问 / 老师指出 / 小黑板 / 群聊提及）
      evi       证据原话
      at        最近一次证据日期
      dates     全部证据日期（去重升序）
      hard      是否「真的做错」（读图判错 / 非「没写完」的实质错误）
      n         证据条数
      legacy    是否遗留
      why       遗留理由（写进报告，让人能自己判断）
      lvl       证据粒度：题型 / 知识点
    """
    merged = merge_fine((s or {}).get("fine") or {}, fine_from_board(board))
    mst = (s or {}).get("topic_mastered") or {}
    sig = (s or {}).get("topic_signals") or {}
    tev = (s or {}).get("top_topics_evi") or {}
    out, seen = [], set()
    for topic, rows in (merged or {}).items():
        k = std(topic)
        if not k:
            continue
        rows = [r for r in (rows or []) if (r.get("item") or "").strip()]
        if not rows:
            continue
        seen.add(k)
        dates = sorted({_d10(r.get("at")) for r in rows if _d10(r.get("at"))})
        # 主证据：优先「真的做错」，其次最近
        hard_rows = [r for r in rows if r.get("hard")]
        pick = sorted(hard_rows or rows,
                      key=lambda r: (_d10(r.get("at")), str(r.get("item") or "")),
                      reverse=True)[0]
        # ── 遗留判定 ──
        legacy, why = False, ""
        mat = _d10((mst.get(k) or mst.get(topic) or {}).get("at"))
        if mat and dates and dates[-1] > mat:
            legacy = True
            why = f"{mat} 已确认听懂，之后又错"
        elif len(dates) >= 2:
            sp = _days(dates[0], dates[-1])
            if sp is not None and sp >= LEG_SPAN_DAYS:
                # 只有单一题型、只出现一次的两端不算「跨周期」
                if sum(int(r.get("count") or 1) for r in rows) >= 2:
                    legacy = True
                    why = f"{dates[0]} → {dates[-1]}，跨 {sp} 天仍在错"
        out.append({
            "stu": stu, "topic": k, "item": (pick.get("item") or "").strip(),
            "kind": (pick.get("kind") or "").strip(),
            "evi": (pick.get("evi") or "").strip(),
            "at": dates[-1] if dates else "", "dates": dates,
            "hard": bool(hard_rows),
            "n": sum(int(r.get("count") or 1) for r in rows),
            "legacy": legacy, "why": why, "lvl": "题型",
        })

    # ── ② 知识点级兜底：题型说不出来，但模块级信号够硬（≥2 条）──
    for topic, n in (sig or {}).items():
        k = std(topic)
        if not k or k in seen:
            continue
        try:
            n = int((n or {}).get("n") or 1)
        except Exception:
            n = 1
        if n < 2:                      # 与卡片同一门槛：只认 ≥2 条，单条不定性
            continue
        seen.add(k)
        ev = tev.get(topic) or tev.get(k) or {}
        dates = sorted({_d10(ev.get("at"))} - {""})
        out.append({
            "stu": stu, "topic": k, "item": "",
            "kind": "群聊提及", "evi": (ev.get("text") or "").strip(),
            "at": dates[-1] if dates else "", "dates": dates,
            "hard": False, "n": n, "legacy": False, "why": "", "lvl": "知识点",
        })
    return out


# ============================================================
# 二、事实 → 处方（联席：学生维度）
# ============================================================
def _lecture(f):
    """讲法：只用现场证据说「怎么开口」，不新增教学内容。"""
    topic, item, evi, kind = f["topic"], f["item"], f["evi"], f["kind"]
    head = f"{topic} · {item}" if item and item != topic else topic
    if topic == "计算/粗心":
        return ("计算稳定性：拿他算错的那道，让他把**每一步写全** —— "
                "不是重算一遍，是找出断在第几步"
                + (f"（上次：「{_clip(evi, 20)}」）" if evi else ""))
    if f["legacy"]:
        return (f"{head}：**换讲法**，别再重复上次那套 —— 先问你「从哪一步开始不确定」，"
                f"定位到具体那一步再讲")
    if kind == "学生提问" and evi:
        return f"{head}：你当时问的是「{_clip(evi)}」→ 从这个问法切入，先让你说卡在哪一步"
    if kind == "老师指出" and evi:
        return f"{head}：上次指出的是「{_clip(evi)}」→ 当面让你把这一步重做一遍"
    if kind == "小黑板" and evi:
        return (f"{head}：小黑板上就错在这一步（{_evi_core(evi, 26)}）→ "
                f"用他这张当例题，先让他讲一遍思路")
    if kind == "群聊提及" and evi:
        return (f"{head}：群里他提的是「{_clip(evi)}」→ "
                f"先让他做一道同类题，看是概念不清还是算不动")
    if evi:
        return f"{head}：拿错的那道当例题（「{_clip(evi, 20)}」），先让你讲一遍思路"
    return f"{head}：让他现场做一道同类题，看卡在第几步"


def _drill(f):
    item = f["item"] or f["topic"]
    if f["topic"] == "计算/粗心":
        return (f"挑 {DRILL_N} 道他算错过的同类计算，要求**写全步骤**、限时 10 分钟"
                f"（看断在第几步，不是看答案对不对）")
    if f["legacy"]:
        return (f"配 {DRILL_N} 道「{item}」，**其中 1 道必须是上次错过的那道原题**"
                f"（看是真会了还是记住了答案）")
    return f"配 {DRILL_N} 道「{item}」同类题（1 道同结构 + 2 道变式）"


def _check(f):
    return (f"{DETECT_DAYS} 天内「{f['topic']}」在作业评级 / 小黑板 / 群内答题"
            f"出现全对 ≥1 次 → 判已会；仍错 → 轮次 +1，换讲法")


def _when(f):
    return "单独约 10 分钟（不占正课）" if f["legacy"] else "下次课前 10 分钟"


def _facts_score(f):
    """排序：遗留优先（一直没会的最耗人），其次「真的做错」，再次最近。"""
    return (0 if f["legacy"] else 1, 0 if f["hard"] else 1,
            tuple(-ord(c) for c in (f["at"] or "0")[:10]))


def build_student(stu, s, board, std, merge_fine, fine_from_board, level_key):
    """一名学生的处方（按分层配额裁剪）。返回 [task]。

    task 字段：key / stu / topic / type / item / lecture / drill / check / when / evi / legacy / why
    """
    quota = QUOTA.get(level_key, 0)
    if quota <= 0:
        return []
    facts = topic_facts(stu, s, board, std, merge_fine, fine_from_board)
    if not facts:
        return []
    facts.sort(key=_facts_score)
    tasks = []
    for f in facts[:quota]:
        tasks.append({
            "key": f"{stu}|{f['topic']}",
            "stu": stu,
            "topic": f["topic"],
            "type": TYPE_LEG if f["legacy"] else TYPE_CUR,
            "item": f["item"],
            "lecture": _lecture(f),
            "drill": _drill(f),
            "check": _check(f),
            "when": _when(f),
            "evi": (f"{f['at']}「{_evi_core(f['evi'], 60)}」" if f["at"]
                    else (f"「{_evi_core(f['evi'], 60)}」" if f["evi"] else "")),
            "legacy": f["legacy"],
            "why": f["why"],
            "lvl": f.get("lvl") or "题型",
        })
    return tasks


# ============================================================
# 三、事实 → 班级处方（主讲：课程维度）
# ============================================================
def build_class(crows, boards_map, std, merge_fine, fine_from_board,
                norm_name, min_share=0.15, agg=None):
    """按知识点聚合全班，拆成两个通道（2026-10-31 定）。

    - must（正课通道）：当期 × 多人 —— 主讲按进度讲这个，不额外加课。
    - patch（补丁通道）：遗留 × 多人 —— 不进正课，走专题小课 / 录播 / 统一资料。

    ⚠️ 为什么必须拆开：课要往前走。把历史遗留塞进正课，主讲一定拒绝；
       而如果主讲视图不排除遗留，他打开就看到一堆「一年前的问题」，然后就再也不打开了。

    ⚠️ `agg` 必须传 report._collect_topics() 的结果：**人数一律以它为准**。
       v4.86 第一版用「有题型证据的人」计数，结果与同一页下方的「共性薄弱面」表
       对不上（18/22 vs 14/22，实测差 4 人）—— 同一屏两个数字打架，读者只会
       认为数据不可信。现在人数取自概览同一份集合，题型/原话仍来自本模块的事实。

    返回 {"must": [...], "patch": [...], "n": 参评人数, "topic_n": 知识点数}
    """
    n = len(crows)
    if not n:
        return {"must": [], "patch": [], "n": 0, "topic_n": 0}
    # 事实层：每个知识点的「谁、是否遗留、卡在哪个题型、原话」
    by_topic = {}
    for r in crows:
        stu = r["stu"]
        b = (boards_map or {}).get(norm_name(stu))
        for f in topic_facts(stu, r["s"], b, std, merge_fine, fine_from_board):
            d = by_topic.setdefault(f["topic"], {"leg": set(), "items": {}, "evi": []})
            if f["legacy"]:
                d["leg"].add(stu)
            if f["item"]:
                d["items"].setdefault(f["item"], set()).add(stu)
            if f["evi"] and len(d["evi"]) < 3:
                d["evi"].append(f"{stu} {f['at']}「{_evi_core(f['evi'], 30)}」")
    if agg is None:          # 无概览聚合时退化为「有证据的人」（仅单测用）
        agg = {t: {"chat": set(), "board": set()} |
               {"chat": set(), "board": set()} for t in by_topic}
        for r in crows:
            b = (boards_map or {}).get(norm_name(r["stu"]))
            for f in topic_facts(r["stu"], r["s"], b, std, merge_fine, fine_from_board):
                agg.setdefault(f["topic"], {"chat": set(), "board": set()})["chat"].add(r["stu"])

    must_min = max(3, int(round(n * min_share)))
    must, patch = [], []
    for t, d0 in agg.items():
        stu_set = (d0.get("chat") or set()) | (d0.get("board") or set())
        if not stu_set:
            continue
        f = by_topic.get(t) or {"leg": set(), "items": {}, "evi": []}
        leg = f["leg"] & stu_set
        items = sorted(f["items"].items(), key=lambda kv: (-len(kv[1]), kv[0]))
        base = {"topic": t, "items": items[:2], "evi": f["evi"],
                "cur_n": len(stu_set) - len(leg), "leg_n": len(leg),
                "cur": stu_set - leg, "leg": leg}
        if base["cur_n"] >= must_min or (base["cur_n"] >= 3 and base["cur_n"] >= n * 0.10):
            must.append(base)
        if base["leg_n"] >= 2:
            patch.append(base)
    must.sort(key=lambda x: (-x["cur_n"], -len(x["items"]), x["topic"]))
    patch.sort(key=lambda x: (-x["leg_n"], x["topic"]))
    return {"must": must[:2], "patch": [p for p in patch if p["topic"] not in
                                        {m["topic"] for m in must[:2]}][:3],
            "n": n, "topic_n": len(agg), "must_min": must_min}


# ============================================================
# 四、渲染
# ============================================================
def _names_html(names, limit=8):
    names = sorted(names)      # set 无序 → 不排序的话每次跑的名单顺序都不一样
    s = "、".join(names[:limit])
    if len(names) > limit:
        s += f" 等 {len(names)} 人"
    return s


def class_html(cls, esc):
    """「下节课讲这个（主讲）」+「遗留补丁包」（v4.88 起挂在 Tab④，原在 Tab② 顶部）。无内容返回空串。"""
    must, patch, n = cls.get("must") or [], cls.get("patch") or [], cls.get("n") or 0
    if not must and not patch:
        return ""
    out = ['<div class="rxcls">']
    if must:
        out.append('<div class="rxcls-h">🎯 下节课讲这个<span class="rxcls-sub">'
                   '按课程进度讲，不额外加课 · 给主讲</span></div>')
        for i, x in enumerate(must, 1):
            its = "、".join(f'「{esc(k)}」' for k, _ in x["items"])
            drill = x["items"][0][0] if x["items"] else x["topic"]
            out.append(
                f'<div class="rxcls-c">'
                f'<div class="rxcls-t"><b>{i}. {esc(x["topic"])}</b>'
                f'<span class="rxcls-n">{x["cur_n"]}/{n} 人</span>'
                + (f'<span class="rxcls-legn">另 {x["leg_n"]} 人遗留</span>'
                   if x["leg_n"] else "")
                + f'</div>'
                f'<div class="rxcls-l"><span class="rxk">讲</span>'
                f'{esc(x["topic"])}{("，集中卡在 " + its) if its else ""}</div>'
                f'<div class="rxcls-l"><span class="rxk">练</span>'
                f'{DRILL_N}~5 道「{esc(drill)}」同类，课上留 8 分钟当堂做</div>'
                f'<div class="rxcls-l"><span class="rxk">验</span>'
                f'下节课小黑板 / 作业里同题型全对 ≥1 次 → 这批人转「待检测」，'
                f'交联席接着跟</div>'
                f'<div class="rxcls-nm">名单：{esc(_names_html(x["cur"]))}</div>'
                + (f'<div class="rxcls-ev">{esc(" ｜ ".join(x["evi"]))}</div>'
                   if x["evi"] else "")
                + '</div>')
    if patch:
        out.append('<div class="rxcls-h rxcls-h2">🧩 遗留补丁包<span class="rxcls-sub">'
                   '不进正课 · 走专题小课 / 录播 / 统一资料</span></div>')
        out.append('<div class="rxcls-p">')
        for x in patch:
            out.append(
                f'<div class="rxcls-pi"><b>{esc(x["topic"])}</b>'
                f'<span class="rxcls-n">{x["leg_n"]} 人</span>'
                f'<span class="rxcls-pn">{esc(_names_html(x["leg"], 6))}</span></div>')
        out.append('</div>')
        out.append('<div class="rxcls-f">为什么分开：课要往前走。这些是跨周期一直没解决的'
                   '历史遗留（讲过又错、或首末错跨两周以上），塞进正课会挤掉进度，'
                   '主讲也无法在 45 分钟里兼顾。</div>')
    out.append('</div>')
    return "".join(out)


def _status_of(log, key):
    """回读状态 → (文案, class)。只认业务自己填的，读不到就「待处理」。"""
    v = (log or {}).get(key) or {}
    st = (v.get("状态") or "").strip()
    if not st or st in ("待处理", "未处理", "—"):
        return STATUS_TODO, "todo"
    if st in ("已会", "已解决", "已关闭", "会了"):
        return "已会 · 关闭", "done"
    if st in ("未会", "仍不会", "无效"):
        return f"未会 · 轮次 +1" + (f"（{v.get('更新')}）" if v.get("更新") else ""), "bad"
    if "布置" in st:
        return f"已布置 · 待检测" + (f"（{v.get('更新')}）" if v.get("更新") else ""), "doing"
    if "讲" in st:
        return f"已讲 · 待检测" + (f"（{v.get('更新')}）" if v.get("更新") else ""), "doing"
    return st, "doing"


def card_html(tasks, log, esc):
    """学生卡「下一步（知识点处方）」。无任务返回空串（正常/良好不出处方）。"""
    if not tasks:
        return ""
    lis = []
    for t in tasks:
        st, cls = _status_of(log, t["key"])
        tag = ('<span class="rx-leg">遗留</span>' if t["legacy"]
               else '<span class="rx-cur">当期</span>')
        why = f'<span class="rx-why">{esc(t["why"])}</span>' if t.get("why") else ""
        lis.append(
            f'<li><div class="rx-h1">{tag}<b>{esc(t["topic"])}</b>{why}'
            f'<span class="rx-st {cls}">{esc(st)}</span></div>'
            f'<div class="rx-l"><span class="rxk">讲</span>{_md(t["lecture"], esc)}</div>'
            f'<div class="rx-l"><span class="rxk">练</span>{_md(t["drill"], esc)}</div>'
            f'<div class="rx-l"><span class="rxk">验</span>{esc(t["check"])}</div>'
            f'<div class="rx-m"><span class="rxw">什么时候</span>{esc(t["when"])}'
            + (f'<span class="rx-evi">{esc(t["evi"])}</span>' if t["evi"] else "")
            + '</div></li>')
    return ('<div class="rxbox"><div class="rxbox-h">🎯 下一步（知识点处方）'
            '<span class="rxbox-sub">只给需重点关注 / 需关注，正常学生不出处方</span></div>'
            f'<ol class="rxlist">{"".join(lis)}</ol></div>')


def _md(txt, esc):
    """模板里用 **粗体** 标记关键词 —— 统一转 <b>（正文里 `**` 必须为 0）。"""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(txt))


# ============================================================
# 五、任务表（导出 / 回读）
# ============================================================
COLS = ["任务编号", "学生", "知识点", "类型", "题型", "下一步（讲）", "练什么",
        "怎么算会了", "什么时候", "证据", "状态", "轮次", "负责人", "更新于", "备注"]


def _owner_of(row, task, co, owners):
    """「负责人」= 这名学生的联席老师。

    ⚠️ 不能用报告对象名兜底：课程维度下它是一串课程编号（`2026Q-SX-…`），
       业务拿到表看见「负责人＝课程名」，没法分派（实测 76/76 行都错）。
    """
    old = (row.get("负责人") or "").strip()
    right = ((owners or {}).get(task["stu"]) or "").strip() or co
    # 业务手改过的人名不覆盖；只有空值、或历史上误填成报告对象名时才纠正
    if old and old != (co or "").strip():
        return old
    return right or old


def export_csv(path, tasks, co="", owners=None):
    """导出 / 合并任务表（业务导入飞书多维表格后自己勾）。

    ⚠️ **合并写，不覆盖**：已存在的行按「任务编号」原地更新（知识点 / 题型 / 动作随证据刷新），
       `状态` `轮次` `更新于` `备注` 四列**一律保留业务自己填的内容** ——
       那是人的劳动成果，机器重跑一次就抹掉等于把闭环打断。
       已不在本次处方里的旧任务行也保留（就是这名学生的辅导履历），只是不再刷新。

    返回 (写入行数, 新增行数)。
    """
    old = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                old = [r for r in csv.DictReader(f) if (r.get("任务编号") or "").strip()]
        except Exception:
            old = []
    keep = {r["任务编号"].strip(): r for r in old}
    added = 0
    for t in tasks:
        r = keep.get(t["key"])
        if r is None:
            r = {"任务编号": t["key"], "状态": STATUS_TODO, "轮次": "1",
                 "更新于": "", "备注": ""}
            added += 1
        r.update({"学生": t["stu"], "知识点": t["topic"], "类型": t["type"],
                  "题型": t["item"],
                  "下一步（讲）": t["lecture"].replace("**", ""),
                  "练什么": t["drill"].replace("**", ""),
                  "怎么算会了": t["check"], "什么时候": t["when"],
                  "证据": t["evi"], "负责人": _owner_of(r, t, co, owners)})
        keep[t["key"]] = r
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for r in keep.values():
            w.writerow({c: r.get(c, "") for c in COLS})
    return len(keep), added


def load_log(path):
    """回读任务表。返回 {任务编号: {状态, 轮次, 更新}}；文件不存在返回 {}。

    ⚠️ 读不到就是读不到，**不推测状态**。调用方据此显示「待处理」，
       并在概览里说明「本次未回读到任务表」，而不是假装业务已经处理过。
    """
    if not path or not os.path.exists(path):
        return {}
    out = {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                k = (row.get("任务编号") or "").strip()
                if k:
                    out[k] = {"状态": (row.get("状态") or "").strip(),
                              "轮次": (row.get("轮次") or "").strip(),
                              "更新": (row.get("更新于") or "").strip()}
    except Exception:
        return {}
    return out
