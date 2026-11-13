"""后台「知识点树」→ 学情报告识别词表 的桥接层（v4.76：**以后台为准**）。

口径（2026-10-29 用户确认）
────────────────────────
「学情分析里的知识点（知识 & 方法）以后台知识树为准」。落地分两层，别混：

  ① **范围以后台为准** —— 后台树里没有的模块，报告不出现。本版据此**剔除** 立体几何 /
     导数 / 数列 / 概率统计 四个单元（实测后台 653 节点全量里一个都没有，且 9.1–10.24
     窗口内它们合计只有 1 条信号 / 1 人，剔除近乎零损失）。
  ② **措辞仍用群聊自然说法** —— 命中词保留「恒成立」「取值范围」这类学生真会打的词，
     不要求逐字等于节点名。后台节点名是录题系统的术语（「一元二次方程的韦达定理」），
     学生只说「韦达定理」。**若连措辞也强制对齐后台，识别率会崩**，那不是「以后台为准」，
     是「以后台为唯一文本」。

为什么要这一层，而不是直接在 extract.TOPIC_UNITS 里删
──────────────────────────────────────────────────
后台会陆续更新（用户已确认：当前是暂态全量，后续补录）。把「哪几个单元有后台支撑」
放在这里做运行时判定 → 后台补了节点，`augment()` 自动把它们放回来，不用改 extract.py。
`dropped` 会落进 `knowledge_used.json` 并由报告口径区写出，读者能看见「本版没识别哪几块」。

方法（category=2）维度
────────────────────
高中数学方法树当前 0 节点（方法树只建在初中数学和物理上）。本版不虚构方法维度：
`info["method_nodes"]=0` 时报告口径写明「方法维度暂无后台节点，未呈现」。

降级必须显式
────────────
没拉到知识字典时，本模块**不静默返回原样**，而是返回原词表 + `degraded=True`：这样
「报告里没有后台节点」这件事能被看到，而不是让人以为是后台本来就没有。
"""
import json
import os

SNAPSHOT = os.path.join("snapshot", "knowledge_points.json")

# ── 后台节点名 → 追加到某个展示单元的命中词 ────────────────────────────────
# 约定：只写后台真实存在的节点名，或它去掉「的/法/运算」等虚词后的自然短名；
#       每行的来源节点写在注释里，便于后台改结构后逐条核对。
KP_EXTRA = {
    "空间向量": [                               # ← 空间向量的运算 / 坐标 / 法向量 / 夹角公式 / 距离公式
        "空间向量的运算", "空间向量的坐标", "空间向量基本定理", "空间直角坐标系",
        "空间向量的夹角", "空间向量的距离",
    ],
    "直线": [                                   # ← 直线方程 / 三种直线 / 直线的五种方程 / 直线的斜率
        "直线方程", "直线的五种方程", "直线的斜率", "距离公式",
    ],
    "圆": [                                     # ← 圆的方程 / 圆的四种方程 / 点与圆、圆与圆的位置关系
        "圆的方程", "圆的四种方程", "点与圆", "圆与圆",
    ],
    "函数": [                                   # ← 函数的概念 / 指数函数 / 对数函数 / 换底公式
        "函数的概念", "指数函数", "对数函数", "指数的运算", "对数的运算", "换底公式",
    ],
    "圆锥曲线": [                               # ← 椭圆方程 / 椭圆的定义 / 双曲线的渐近线 …
        "椭圆方程", "椭圆的定义", "椭圆的性质",
        "双曲线方程", "双曲线的定义", "双曲线的渐近线", "双曲线的离心率",
    ],
    "三角函数": [                               # ← 弧度制 / 三角函数定义 / 三角函数图象与性质 / 图像变换
        "弧度制", "三角函数定义", "三角函数图象与性质", "三角函数的图像变换",
    ],
    "解三角形": ["解三角形的公式与定理"],        # ← 解三角形的公式与定理
    "不等式": [                                 # ← 基本不等式的推导/使用 / 解一元二次不等式 / 穿针引线法
        "基本不等式", "基本不等式的推导", "基本不等式的使用",
        "一元二次不等式", "解一元二次不等式", "分式不等式", "解分式不等式", "穿针引线",
        # v4.76：用户截图圈定此节点 → 归属以后台为准（后台挂在「不等式与二次函数」下）。
        #   ⚠️ 与课堂用法的偏差仍在：真实语料里它多出现在圆锥曲线联立题，
        #   一旦本课程进入圆锥曲线章节，需复查是否要把联立语境排除（见 UNIT_EXCL 机制）。
        "一元二次方程的韦达定理", "韦达定理",
    ],
}

# ── 后台有、原报告完全没有的展示单元 ──────────────────────────────────────
# key = 归属大类（用于对齐 FINE_TYPES；FINE_TYPES 暂时没有这两个键 → 细分题型只显示到单元级，
#       这是可接受的降级：本来「集合」「平面向量」的细分题型也没有被业务确认过）。
KP_NEW = [
    ("集合", ["集合", "集合的运算", "集合的概念", "集合的表示",
              "交集", "并集", "补集", "子集", "真子集", "空集", "韦恩图",
              "逻辑命题", "充分必要", "充要条件", "命题的否定", "任意存在"], "集合"),
    # ⚠️ 平面向量只收「平面向量的 X」全名，**不收**「向量的线性运算」这类泛化短名 ——
    #   后者在「空间向量的线性运算」里也出现，会让空间向量的卡点同时记到平面向量上（串味）。
    #   「平面向量」这个词本身已能覆盖「平面向量不会」「平面向量那章」等说法。
    ("平面向量", ["平面向量", "平面向量的概念", "平面向量的线性运算", "平面向量的坐标运算",
                  "平面向量的数量积运算", "平面向量基本定理"], "平面向量"),
]

# ── 后台未建、本版不识别（后台补录后自动放回）─────────────────────────────
BACKEND_MISSING = ("立体几何", "导数", "数列", "概率统计")

# ── 后台未建、但按教学口径**保留**的例外（v4.81，用户 2026-10-30 批准）──────
# 用户原话：「目前函数和导数并没有在一起讲，是分开的」。导数是独立一章，若按①剔除，
# 它会连同「函数」这个合并模块名一起被折进函数，报告里就看不到导数了 —— 与教学事实相反。
# 故列为「待后台补录」的保留项：不剔除、单独成行，但报告口径里必须**显式写明后台暂无节点**，
# 不能让人以为后台已经建了导数。后台补了节点后 `_exists` 为真，它会自动从 pending 里消失。
# ⚠️ 全表只此一个例外，别在其他模块上照抄 —— 那等于架空「以后台为准」。
BACKEND_PENDING = ("导数",)

# ── 非知识点单元：不来自知识树，永远保留 ──────────────────────────────────
#   「含参问题」是解法特征、「计算/粗心」是错误类型 —— 后台知识树里本就没有对应节点，
#   用它们判「后台没建」会把两类有效信号一起删掉。
NON_TOPIC_UNITS = ("含参问题", "计算/粗心")

# v4.95：数学词表留在本文件（TOPIC_UNITS 之外的 KP_EXTRA / BACKEND_MISSING 等）；
#   物理词表在 physics_dict.py（人教版教材目录自建 + 真实语料反查，后台物理树只覆盖
#   必修一，不能沿用「后台为准做剔除」）。augment() 按课程科目分派两套词表；
#   其他科目（化学 / 生物…）仍整维度停用。⚠️ 给新科目建词表 = 新建一个 dict 文件
#   + augment 里加一个分支，不是把 VOCAB_SUBJECT 改成那个科目 —— 那会让数学课全部停用。
VOCAB_SUBJECT = "数学"


def _exists(w, names):
    """该关键词在后台树里「找得到」吗。

    不能只做 `w in names` 全名匹配：命中词里有大量**自然短名**
    （「穿针引线」对「穿针引线法」、「基本不等式」对「基本不等式的推导」），
    全名匹配会把它们全部丢掉 —— 实测这一条就让 53 个并入词缩水成纯节点全名，
    而报告侧真正能用上的恰恰是短名。故放宽为「是某节点名的子串」。
    """
    return w in names or any(w in n for n in names)

# ── 单元级排除词：词本身在群里另有日常含义，命中这些搭配不算知识点 ─────────
# 只对指定单元生效（不改全局 TOPIC_SKIP_MSG，那条是整条消息跳过，太粗）。
UNIT_EXCL = {
    "集合": ("集合一下", "集合啦", "集合了", "集合时间", "集合地点"),
}


def load(wd="."):
    """读知识字典；不存在或为空返回 (None, 原因)。

    ⚠️「文件不存在」和「points 为空」都返回 None：两者都不能被当成「后台就是没有知识点」。
    """
    p = os.path.join(wd, SNAPSHOT) if wd else SNAPSHOT
    if not os.path.exists(p):
        return None, f"未找到 {p}"
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (ValueError, OSError) as e:
        return None, f"{p} 读取失败：{e}"
    pts = d.get("points") or []
    if not pts:
        return None, f"{p} 的 points 为空"
    return d, ""


def _course_subject(wd="."):
    """本次课程的科目（从课程名里取多数票），取不到返回空串。

    ⚠️ 为什么不用 `common.subject_of`：knowledge_dict 是**词表层**，保持零依赖更好调试；
    且这里只需要「课程名里有没有这个科目词」，不需要 common 的完整解析。
    """
    for fn in ("students.json", os.path.join("snapshot", "roster.json")):
        p = os.path.join(wd, fn) if wd else fn
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, list) or not d:
            continue
        cnt = {}
        for x in d:
            pn = str((x or {}).get("productName") or "")
            for w in ("物理", "化学", "生物", "英语", "语文", "历史", "地理", "政治", "数学"):
                if w in pn:
                    cnt[w] = cnt.get(w, 0) + 1
                    break
        if cnt:
            return max(cnt, key=cnt.get)
    return ""


def _dispatch_subject(wd="."):
    """本次课程的科目。空串 = 取不到（此时不切块，按数学词表 + degraded 走）。"""
    return _course_subject(wd)


# ── 学段分派（v4.99）：物理按学段分流词表 ─────────────────────────────────
#   初中物理「以后台为准」（后台初中树 324 节点覆盖完整）；
#   高中物理走人教版教材自建词表（后台高中树仅必修一，照搬「后台为准」会删成空）。
#   ⚠️ 学段词从课程名取：现有课程名规范必带年级（「2026Q-WL-高二物理…」），
#   初中课名须含 初一/初二/初三/七年级/八年级/九年级/初中 之一，否则按高中处理 ——
#   这是**显式可查的降级**（report 口径区会写出所用词表来源，错了能看出来）。
STAGE_JUNIOR_WORDS = ("初一", "初二", "初三", "七年级", "八年级", "九年级", "初中")
STAGE_SENIOR_WORDS = ("高一", "高二", "高三", "高中")


def _course_stage(wd="."):
    """本次课程的学段：「初中」/「高中」，取不到返回空串（按高中处理）。"""
    for fn in ("students.json", os.path.join("snapshot", "roster.json")):
        p = os.path.join(wd, fn) if wd else fn
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, list) or not d:
            continue
        for x in d:
            pn = str((x or {}).get("productName") or "")
            if any(w in pn for w in STAGE_JUNIOR_WORDS):
                return "初中"
            if any(w in pn for w in STAGE_SENIOR_WORDS):
                return "高中"
    return ""


# ── 教材自建词表模块注册表（v4.97：物理＋化学；数学走本文件的后台对齐逻辑）──
#   ⚠️ 给新科目建词表 = ① 新建一个 dict 文件（结构同 physics_dict）
#   ② 在这里加一行注册 ③ 用真实语料验证关键词。不是把 VOCAB_SUBJECT 改成那个
#   科目 —— 那会让数学课全部停用。
BUILTIN_MODULES = {
    "物理": "physics_dict",
    "化学": "chemistry_dict",
    "语文": "chinese_dict",     # v5.14：语文知识树打底（教材自建，与高中物理同构）
}
# 全部已建词表的科目（subject_mismatch 空态文案用，让读者知道还差什么）
BUILTIN_SUBJECTS = ("数学",) + tuple(BUILTIN_MODULES)


def _builtin_info(mod_name, points):
    """教材自建词表（物理/化学…）的 knowledge_used 元信息（v4.97）。

    ⚠️ 与数学的差别要写清楚，不能让读者以为该科目也「以后台为准」：
    教材自建词表不依赖后台树；后台对齐情况由各模块的 SOURCE_NOTE /
    BACKEND_BACKED 自述（化学目前未核验、物理仅必修一）。
    backend_nodes 只统计**本科目**的后台节点 —— 否则拿数学树 653 节点冒充
    「化学后台节点」就是造假。
    """
    import importlib
    mod = importlib.import_module(mod_name)
    _sub = next(k for k, v in BUILTIN_MODULES.items() if v == mod_name)
    _ch, _sec, _per = mod.tree_stats()
    return {
        "degraded": False,
        "mode": "builtin_" + mod_name.replace("_dict", ""),   # builtin_physics / builtin_chemistry
        "vocab_subject": _sub,
        "source": "人教版2019教材目录自建",
        "source_note": mod.SOURCE_NOTE,
        "grade_note": getattr(mod, "GRADE_NOTE", ""),
        "tree": {"chapters": _ch, "sections": _sec, "per_grade": _per},
        "unit_grades": dict(mod.UNIT_GRADE),
        # v4.99：单元级排除词随 info 下发（此前物理/化学的 UNIT_EXCL 根本没接线，
        #   extract 只读本文件的数学版 —— 「期待与你们相遇」会误判成匀变速薄弱）
        "unit_excl": dict(getattr(mod, "UNIT_EXCL", {}) or {}),
        "backend_backed_units": list(getattr(mod, "BACKEND_BACKED", ())),
        "backend_nodes": sum(1 for p in points if p.get("subjectName") == _sub),
        "method_nodes": sum(1 for p in points if p.get("category") == 2),
        "added": 0, "new_units": [], "dropped": [], "pending": [],
    }


def _augment_physics_junior(kd, why, points):
    """初中物理分支（v4.99）：**以后台为准**，与数学同构。

    · 无树 → 显式降级（返回词表原样 + degraded，绝不静默）
    · 有树 → 只认「物理 + 初中学段」的节点（backend_nodes 防冒充：数学/高中
      节点不掺进来）；单元存在性按后台运行时校验，节点消失 → 剔除并显式写出。
    · 命中词不逐词校验（词表本身就是按后台树建的，见 physics_dict.JUNIOR 部分）。
    """
    import physics_dict
    units = physics_dict.JUNIOR_TOPIC_UNITS
    if not kd:
        return list(units), {
            "degraded": True, "why": why or "未拉到后台知识树",
            "mode": "builtin_physics_junior", "vocab_subject": "物理",
            "source_note": physics_dict.JUNIOR_SOURCE_NOTE.format(
                pulled_at="未拉取", n_nodes=0) + "（本次运行未拉到后台树，口径按词表内置状态呈现）",
            "grade_note": "", "backend_nodes": 0,
            "method_nodes": 0, "added": 0,
            "new_units": [], "dropped": [], "pending": [],
        }
    names = {p["name"] for p in points
             if p.get("subjectName") == "物理" and str(p.get("schoolStages")) == "1"
             and p.get("level", 0) >= 2}
    method_n = sum(1 for p in points
                   if p.get("subjectName") == "物理" and str(p.get("schoolStages")) == "1"
                   and p.get("category") == 2)
    out, dropped = [], []
    for disp, kws, key in units:
        if disp in NON_TOPIC_UNITS:          # 「计算/粗心」永远保留
            out.append((disp, list(kws), key))
            continue
        if not _exists(disp, names) and not any(_exists(w, names) for w in kws):
            dropped.append(disp)             # 后台整个单元没了 → 剔除，口径区可见
            continue
        out.append((disp, list(kws), key))
    kdj = kd or {}
    return out, {
        "degraded": False,
        "mode": "builtin_physics_junior",
        "vocab_subject": "物理",
        "source_note": physics_dict.JUNIOR_SOURCE_NOTE.format(
            pulled_at=str(kdj.get("pulled_at") or "")[:10] or "近期", n_nodes=len(names)),
        "grade_note": "",
        "backend_nodes": len(names),
        "method_nodes": method_n,
        "unit_excl": dict(physics_dict.JUNIOR_UNIT_EXCL),
        "added": 0, "new_units": [], "dropped": dropped, "pending": [],
    }


def augment(units, wd="."):
    """把后台节点词并进识别单元，并剔除后台没有的模块。返回 (新单元列表, info)。

    units 结构不变（(显示名, 命中词, 归属大类) 三元组），下游零改动。
    无字典时**不做剔除**（不知道该剔什么），返回原表并标 degraded。
    v4.97：按课程科目分派 —— 物理/化学/语文 → 各自教材自建词表（BUILTIN_MODULES），
    数学 → 本文件的后台对齐逻辑；其余科目 → 整维度停用（subject_mismatch）。

    ⚠️ 分派必须在 load(wd) **之前**：教材自建词表不依赖后台树，
    没拉知识树也能跑；化学等科目没拉树时若先走 load 分支，会拿数学内置词表
    照跑（degraded 横幅救不回「化学课识别出函数」这种错内容）。

    v4.99：物理按学段分流 —— 初中 → _augment_physics_junior（以后台为准），
    高中 → 人教版教材自建词表（BUILTIN_MODULES）；学段取不到按高中处理。
    """
    _csub = _dispatch_subject(wd)
    _cstage = _course_stage(wd)
    kd, why = load(wd)
    points = (kd or {}).get("points") or []
    if _csub == "物理" and _cstage == "初中":
        return _augment_physics_junior(kd, why, points)
    if _csub in BUILTIN_MODULES:
        return list(__import__(BUILTIN_MODULES[_csub]).TOPIC_UNITS), \
            _builtin_info(BUILTIN_MODULES[_csub], points)
    if _csub and _csub != VOCAB_SUBJECT:
        return [], {
            "degraded": True, "subject_mismatch": True,
            "why": (f"识别词表目前建了「{'、'.join(BUILTIN_SUBJECTS)}」，"
                    f"本次课程是「{_csub}」——"
                    f"硬套会把已建科目的说法认成{_csub}知识点，"
                    f"故本版不做知识点识别"),
            "course_subject": _csub, "vocab_subject": VOCAB_SUBJECT,
            "vocab_subjects": list(BUILTIN_SUBJECTS),
            "tree_subject": sorted({p.get("subjectName") for p in points if p.get("subjectName")}),
            "added": 0, "new_units": [], "dropped": [], "pending": [],
            "backend_nodes": len(points),
            "method_nodes": sum(1 for p in points if p.get("category") == 2),
            "mode": "subject_mismatch",
        }
    if not kd:
        return list(units), {"degraded": True, "why": why, "added": 0,
                             "new_units": [], "dropped": [], "mode": "builtin"}

    names = {p["name"] for p in points if p.get("level", 0) >= 2}
    method_n = sum(1 for p in points if p.get("category") == 2)

    # v5.13：学段一致性校验 —— 数学词表没有物理那样的学段分流，树拉错学段 =
    #   判据整个错位（初中课剔掉初中模块、并入高中词，或反之），augment 无法
    #   自行纠正，但必须显式报警（报告口径区红字可见）。不阻断：错树仍好过裸词表。
    stage_mismatch = ""
    if kd:
        _stg = ((kd.get("filters") or {}).get("stages")) or []
        _tree_stage = ({1: "初中", 2: "高中"}.get(_stg[0])
                       if len(_stg) == 1 else "")
        if _tree_stage and _cstage and _tree_stage != _cstage:
            stage_mismatch = (f"课程学段「{_cstage}」与知识树学段「{_tree_stage}」"
                              f"不一致 —— 请核对 pull_knowledge.py 的 --stages 参数")

    added, dropped, pending, out = 0, [], [], []
    for disp, kws, key in units:
        # ① 排除后台未建的模块（非知识点单元豁免；BACKEND_PENDING 里的例外也豁免）
        if (disp in BACKEND_MISSING and disp not in NON_TOPIC_UNITS
                and disp not in BACKEND_PENDING and not _exists(disp, names)):
            dropped.append(disp)
            continue
        if disp in BACKEND_PENDING and not _exists(disp, names):
            pending.append(disp)
        # ② 并入后台节点词
        kws = list(kws)
        for w in (KP_EXTRA.get(disp) or []):
            # 只并后台真的建了的节点（含短名）：表里写了但后台删了/改名 → 不硬加，
            # 避免词表与后台悄悄脱节（脱节信号见 align_knowledge.py 的 ② 清单）
            if _exists(w, names) and w not in kws:
                kws.append(w)
                added += 1
        out.append((disp, kws, key))

    new_units = []
    for disp, kws, key in KP_NEW:
        if any(u[0] == disp for u in out):
            continue
        # 新增单元也要求后台确有其节点，否则等于凭空造一个知识点
        if not any(_exists(w, names) for w in kws):
            continue
        new_units.append((disp, list(kws), key))
        added += len(kws)
    out.extend(new_units)

    return out, {
        "degraded": False,
        "mode": "backend",
        "pulled_at": kd.get("pulled_at"),
        "backend_nodes": len(names),
        "method_nodes": method_n,
        "added": added,
        "new_units": [u[0] for u in new_units],
        "dropped": dropped,
        "backend_missing": dropped,
        # v4.81：后台暂无节点、但按教学口径保留的单元（报告需显式说明，不能装作后台有）
        "pending": pending,
        # v5.13：学段一致性（树与课程学段不一致时为非空描述，报告口径区红字显示）
        "stage_mismatch": stage_mismatch,
    }
