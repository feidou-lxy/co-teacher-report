"""飞书表格适配器（v4.19）：把团队自维护飞书表折算成「中间契约」的第 8 张表。

    pull_feishu.py（原始二维数组） → 本脚本 → snapshot/feishuLearning.json + feishuMeta.json

为什么单独一张表：飞书提供的是**学习证据的补充**（作业 / 打穿题 / 精题 / 出门测 /
框架笔记 / 课前测），**不替代任何现有表** —— 出勤仍以系统为准（用户 2026-10-16 拍板：
「出勤数据只采纳系统的，飞书的出勤忽略」）。

━━━ 实测 6 种结构形态（同一张表内，没有一种统一）━━━
    wide2   宽表·2 行表头   每课 6~7 子列   NPMSWD / MbVgf8 / mslXT3 / KR6aiI / 罗
    wide3   宽表·3 行表头   每课 10 列      某生（次课名 → 分类 → 子项）
    narrow  窄表·1 行表头   每课 1 列       张寒 / 马（格内是 `出勤,作业,框架笔记`）
    flat    扁平无课次列                   轩轩（已按用户口径排除）

━━━ 三条铁律 ━━━
1. **绝不硬编码列号**。实测「打穿题」是第 3 次课才插入的列：第 1、2 次课每课 6 列、
   第 3 次课起 7 列 —— 按固定偏移读会从第 3 次课之后**全列错位且不报错**。
   一律按表头文字动态建映射，并存「表头指纹」，与上次对不上就显式报错。
2. **缺失必须显式**。未知子列名 / 未知分词 / 课程名解析不出 / 姓名对不上花名册，
   全部进 feishuMeta.degraded[]，绝不静默丢弃。
3. **出勤类词一律丢弃**（出勤 / 回放 / 请假）—— 用户口径：出勤只看系统。
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, sys.path[0])
import common

TZ = "+08:00"
# ⚠️ 加第二组：单行表头的表把子列名写在课次格里（`第1次课-出勤`）。
#    续行表头（孙一文类）里该组为空串 —— 两种形态共用一条正则。
LESSON_RE = re.compile(r"^第\s*(\d+)\s*[次节]\s*课\s*[-－—_:：]?\s*(.*)$")

# ⚠️ 公式辅助列：飞书表习惯把「课次数字 / 成绩数字」放在尾部给公式用。
#     实测娜娜团队尾部有 80 个 `第N次课数字`/`模拟测试N数字` —— 若不排除，
#     它们会被当成**额外的课次标记**（`第N次课数字` 同样匹配课次正则），把课次区撑到 218 列。
#     默认按「以数字结尾」跳过，可在 profile.feishu.columnSkipPatterns 覆盖（置空数组 = 不跳过）。
SKIP_COL_PATTERNS_DEFAULT = [r"数字$"]

# ---- 默认口径（profile.feishu.items 可覆盖）
# ⚠️ v4.50：加入「总结」—— 娜娜团队每节课最后一组子列是
#    `第N次课-出勤 / 作业评级 / 作业闭环 / 讲题视频 / 总结 / 打穿题测试 / 得星`。
#    「总结」是学生课后自己写的复盘，取值 `已交`917 / `佳作`503 / `未交`128 / `#N/A`2322
#    （17 个 sheet 共 306 列 3870 个非空格，实测 2026-10-16 快照）——
#    漏掉它等于漏掉「课后复盘」这一整条学习证据链。
DEFAULT_DONE = ["作业", "打穿题", "精题", "出门测", "框架笔记", "课前测", "总结"]
DEFAULT_IGNORE = ["出勤", "回放", "请假", "红包"]
DEFAULT_ALIAS = {"课前侧": "课前测", "2号打穿题": "打穿题", "打穿题2": "打穿题"}
# 已知存在、但属「记录型」（数值 / 星数 / 打分）而非「完成项」的子列：
# 保留原值、不计入完成率、也不报「未登记」。缺少它们会造成大量假告警。
DEFAULT_PASSIVE = ["额外加分", "当日星星", "模块打分"]

# ---- 完成 / 未完成判定（v4.19.3）
#   ⚠️ 原实现把「该子列**有值**」当「已完成」—— 但宽表的值是**文本**：
#      实测 ⭐=完成、✘=未完成。不分清会把 ✘ 计入完成率
#      （本项目：框架笔记 237/667=36% 是 ✘、作业 103/1200=9%、精题 125/616=20%）。
#   ⭐ 窄表的值恒为 True（拆分命中 = 出现即完成），不受影响。
NOT_DONE_EXACT = {"✘", "✗", "✖", "✕", "❌", "❌️", "x", "X", "×", "╳",
                  "无", "/", "—", "－", "-", "未完成"}
DONE_EXACT = {"⭐", "🌟", "√", "✓", "✔", "好", "完成", "已交"}
#   ⚠️ 自由文本默认判「完成」，所以**否定式写法必须显式列出**，否则完成率虚高。
#      实测娜娜团队「周文峰」表的 `几综模块测`（12 格）：`通关！`5 / `思路没问题，细节需完善`4 /
#      `未通关，需加强练习`3 —— 前两种是完成，第三种是**未完成**；不列 `未通关` 会把 3 格算成完成。
NOT_DONE_PREFIX = ("未完成", "未交", "未做", "未提交", "没交", "没做", "没写",
                   "未写", "缺交", "漏交",
                   "未通关", "没通关", "未通过", "没通过", "未达标", "没达标")

# ⚠️ Excel / 飞书的**公式错误值**：它不是「完成」、也不是「未完成」，而是**根本没拿到数据**
#    —— 必须和空白同待遇（不进完成率分母），否则完成率被虚高。
#    实测娜娜团队「作业评级」一列 4412 格里 **864 格是 `#N/A`**：原实现按「自由文本=完成」
#    处理 → 该列完成率 75.8%；剔除后真实值是 **70.0%**（3346→2482 / 4412→3548）。
ERROR_VALS = {"#N/A", "#N/A!", "#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!",
              "#NUM!", "#GETTING_DATA", "N/A", "NA", "nan", "NaN", "null", "None"}

# ⚠️ 占位/表头词：团队的表里常出现「单元格被粘成了表头文字」的行。
#    实测娜娜团队「何树威」第 41/69/88 行、且「某生」第 16/30/56 行：
#    序号在、老师/教务在、课次区甚至有 `√`/`⭐`，但
#    **课程/班型 列写成了「课程」、学生姓名 列写成了「学生」**（多半是复制表头时误粘）。
#    这类行无法归属到任何学生 → 跳过并**显式报告**，绝不猜名字。
PLACEHOLDER_NAMES = {"学生姓名", "姓名", "学生", "学生名字"}
# 超过此长度的值多半是手记串列（写到了课次格里）→ 报进 degraded 供核对。
# ⚠️ 各团队「正常值」的长度差异很大：孙一文表是 `⭐`/`正确率高`（短），
#    娜娜表是 `A 优秀：全面掌握知识点，解题逻辑清晰`（评级描述，24 字）——
#    用同一个阈值会让娜娜表**每次刷 236 条误报**。故可由 profile.feishu.longValLen 覆盖。
LONG_VAL_LEN = 15


def is_done_val(v):
    """完成项取值 → True（完成）/ False（未完成）/ None（无法判断）。"""
    if v is True:
        return True
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if s in ERROR_VALS or s.startswith("#"):
        return None      # 公式错误值 = 没拿到数据 → 与空白同待遇（不进分母）
    if s in DONE_EXACT:
        return True
    if s in NOT_DONE_EXACT or s.startswith(NOT_DONE_PREFIX):
        return False
    return True          # 其余自由文本（`正确率高` / `已完成，且优秀` / `写了，并积极改错`）视为完成


# ---- 元数据列名 → 标准字段
# ⚠️ 匹配一律「按别名长度降序 + startswith」，否则「课程」会吃掉「课程类型」、
#    「学生」会吃掉「学生学情/问题点」。
META_FIELDS = [
    ("note",             ["学生学情/问题点", "学生学情", "10月沟通结果", "电话沟通情况",
                          "沟通情况", "学情", "问题点"]),
    ("priority",         ["沟通优先级"]),
    ("examScore",        ["月考/期中考试成绩", "月考成绩", "月考分数", "考试成绩"]),
    ("examTime",         ["月考/期中考试时间", "考试时间"]),
    ("examScope",        ["月考/期中考试范围", "考试范围", "丢分原因"]),
    ("renew",            ["是否续报"]),
    ("renewLevel",       ["续报等级"]),
    ("stars",            ["总星星榜", "总星星", "星星榜"]),
    ("school",           ["学校名称", "学校"]),
    ("isNew",            ["本学期新生", "新生"]),
    ("buyType",          ["课程类型", "购课类型"]),
    ("teacher",          ["考标课老师", "主讲"]),
    ("classTeacherName", ["教务主任", "教务"]),
    ("coTeacherName",    ["联席教师", "联席"]),
    ("productName",      ["课程名称", "课程", "班级"]),
    ("stuName",          ["学生姓名", "学生", "姓名"]),
]
ALIAS_PAIRS = sorted(((a, f) for f, al in META_FIELDS for a in al),
                     key=lambda x: -len(x[0]))


def cell_text(v):
    """单元格原始值 → 文本（v4.27）。

    ⚠️ 飞书返回的单元格**不总是标量**：
       · 图片 = `{"fileToken": "...", "height": 435, ...}`
       · 富文本 = `[{"segmentStyle": {...}, "text": "..."}]`
       原实现对任何值都 `str(v)`，会把整段 Python repr（含 fileToken）灌进 meta，
       既不可读，也会让「加课记录」这种本来要展示给人看的字段变成一串乱码。
    """
    if v is None:
        return ""
    if isinstance(v, dict):
        for k in ("text", "name", "title"):          # 富文本段 / 链接文字
            if isinstance(v.get(k), str) and v[k].strip():
                return v[k].strip()
        if v.get("fileToken") or v.get("file_token"):
            return "[图片]"
        return ""
    if isinstance(v, (list, tuple)):
        return "".join(x for x in (cell_text(i) for i in v) if x).strip()
    return str(v).strip()


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def colname(n):
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def col_index(x):
    """列字母("A"/"AB") 或 1-based 数字 → 0-based 索引。非法返回 None。"""
    if x is None or x == "":
        return None
    if isinstance(x, int):
        return x - 1 if x >= 1 else None
    s = str(x).strip().upper()
    if s.isdigit():
        return int(s) - 1
    if not s.isalpha():
        return None
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def match_field(header, extra=None):
    """表头文字 → 标准字段。startswith + 最长别名优先。

    extra: profile.feishu.metaAlias —— **团队自定义别名，优先级高于默认表**。
      ⚠️ 必须有这个入口，否则娜娜团队的「学生情况备注」会被默认别名「学生」吃掉、
         错映射成 stuName（默认表里 stuName 有短别名「学生」）。
         且各团队元数据列**顺序不固定**（同团队内某生表把「学校/城区」放在末尾），
         所以只能按名字匹配，不能按列号。
    """
    h = str(header).strip()
    if not h:
        return None
    if extra:
        if h in extra:                          # 完全同名：最高优先
            return extra[h]
        for alias, field in sorted(extra.items(), key=lambda x: -len(x[0])):
            if h.startswith(alias):
                return field
    for alias, field in ALIAS_PAIRS:
        if h.startswith(alias):
            return field
    return None


# ================================================================ 结构探测
def detect(values, max_probe=6, skip_pats=None, meta_alias=None):
    """探测表头布局。返回 spec 或 None（无课次列 = 非学情表）。

    ⭐ 表头/数据分界判据（实测得出）：
       「课次名行的下一行，在**元数据区**是否基本为空」——
       表头续行在元数据区是空的（子列名只写在课次区），
       数据行在元数据区有人名/备注。用**非空率 > 20%** 划线。
       ⚠️ 不能用绝对阈值：某生的表元数据区只有 2 列（A/B），
          数据行也才 1~2 个非空 → 必须归一化。
       ⚠️ 一旦遇到第一个数据行就停止探测，避免把「元数据几乎全空的数据行」误判成表头。

    ⭐ v4.20 新增：**单行表头内联子列名**（第 7 种形态 `wide1-inline`）。
       娜娜团队的表把「第N次课」和子列名写在**同一格**：`第1次课-出勤`，
       于是同一节课的 7 个子列**重复出现 7 次课次名**。
       若不特判，会有两个后果：
         ① 每列自成一组 → per_lesson=1 → 被误判成 `narrow`（格子值 `√` 被当分词拆）；
         ② 课次区右边界被尾部公式列撑开 → 最后一节课吃掉 80 列。
       故：把「seq 连续相同且列号连续」的标记**合并成一节课**，子列名取标记格的 `-` 后缀。

    skip_pats: 表头命中即跳过的正则列表（尾部公式辅助列）。
    """
    skip_pats = SKIP_COL_PATTERNS_DEFAULT if skip_pats is None else skip_pats

    def _skip(txt):
        t = str(txt or "").strip()
        return bool(t) and any(re.search(p, t) for p in skip_pats)

    best, best_n = None, 0
    for i in range(min(max_probe, len(values))):
        n = sum(1 for v in values[i]
                if v and LESSON_RE.match(str(v).strip()) and not _skip(v))
        if n > best_n:
            best, best_n = i, n
    if best is None or best_n < 2:
        return None

    # marks: (列号, 课次序号, 内联子列名)
    marks, skipped_cols = [], []
    for j, v in enumerate(values[best]):
        if not v:
            continue
        if _skip(v):
            skipped_cols.append({"col": colname(j + 1), "header": str(v).strip()})
            continue
        m = LESSON_RE.match(str(v).strip())
        if m:
            marks.append((j, int(m.group(1)), (m.group(2) or "").strip()))
    if not marks:
        return None
    first_col = marks[0][0]
    meta_cols = first_col                      # 元数据区 = 列 0 .. first_col-1

    # ⚠️ 课次区右边界：**不能用「前几行的最大宽度」**——尾部成绩/辅助列会让它一路撑到表尾，
    #    最后一节课就会吃掉几十上百列。改为「最后一个课次标记 + 最大课次间距 - 1」，
    #    再用原始宽度封顶（取小），这样续行表头与单行表头都对。
    gaps = [marks[k + 1][0] - marks[k][0] for k in range(len(marks) - 1)]
    max_gap = max(gaps) if gaps else 1
    raw_hi = max(len(values[r]) for r in
                 [best] + list(range(best + 1, min(best + 5, len(values))))) - 1
    # 先给个**保守上界**用于探测续行（真正的 hi 要等知道有没有续行才能定，见下）
    hi_probe = max(marks[-1][0], min(raw_hi, marks[-1][0] + max_gap - 1))
    hi = hi_probe

    # ---- 向下探表头续行
    header_rows = []
    r = best + 1
    while r < len(values) and len(header_rows) < 4:
        row = values[r]
        ne = sum(1 for j in range(0, meta_cols) if j < len(row) and row[j] not in (None, ""))
        rate = ne / meta_cols if meta_cols else 0.0
        if rate > 0.20:                        # 数据行
            break
        les_ne = sum(1 for j in range(first_col, hi_probe + 1)
                     if j < len(row) and row[j] not in (None, ""))
        if les_ne == 0:                        # 课次区全空 —— 既不是表头也不是数据
            break
        header_rows.append(r)
        r += 1
    data_start = r
    hdr_idx = [best] + header_rows

    # ⚠️ 单行表头（wide1-inline）**不需要向右扩展**：每个子列自己都带着课次名
    #    （`第1次课-得星` 就是该课最后一列的标记）→ hi 直接取最后一个标记列。
    #    若强行用「+ 最大间距 - 1」扩展，会把课次区右边的普通列吃掉：
    #    实测「某生」表因课次区内插列导致最大间距=4 → hi 多出 3 列，
    #    把「平时累计得星 / 当前总星」当成课次子项报成未登记。
    hi = marks[-1][0] if not header_rows else hi_probe

    # ---- 课次分组
    # ⚠️ 单行表头形态：同一节课的子列**各自重复一遍课次名**（`第1次课-出勤`/`第1次课-作业评级`…）
    #    → 必须把「课次号相同」的标记合并成一节课。
    #    否则每列自成一组、per_lesson=1 → 被误判成 narrow，格子值 `√` 会被当逗号分词拆开。
    #    续行表头（孙一文类）相邻两节课 seq 不同，天然不会合并。
    # ⚠️ 判据是「**只看课次号**」，不加「列号连续」——
    #    实测娜娜团队「某生」表在课次区**中间**插了 23 列额外学情列
    #    （精题1刷/精题2刷/精题3刷/出门测/课前测/改错），且**每节课插的位置还不一样**
    #    （第 1 次课插在「总结」后，第 3 次课插在「打穿题测试」后）。
    #    要求列号连续会把一节课拆成 2 组；按课次号合并则插列自然落进所属课次的区间。
    groups = []
    for col, seq, leaf in marks:
        last = groups[-1] if groups else None
        if last and last["seq"] == seq:
            last["_last"] = col
            last["leaves"].append(leaf)
        else:
            groups.append({"seq": seq, "start": col, "end": col,
                           "_last": col, "leaves": [leaf]})
    for k, g in enumerate(groups):
        g["end"] = groups[k + 1]["start"] - 1 if k + 1 < len(groups) else hi
        g["_last"] = g["end"]
    per_lesson = (groups[0]["end"] - groups[0]["start"] + 1) if groups else 0
    kind = "narrow" if per_lesson <= 1 else "wide%d" % (1 + len(header_rows))

    # ---- 子列名
    #   (a) 宽表·续行表头：子列名在课次名行的**下一行**，逐列取值，串成 "分类/子项"
    #       ⚠️ 合并单元格：分类名只写在分组首列，其余列必须 forward fill 继承
    #          （实测某生：row1 仅 C=出勤 D=课堂表现 F=课后任务 K=课程服务；
    #           不平铺 → 同组内的子项有的带前缀「课后任务/框架笔记」、有的不带「作业」，
    #           下游无法按名字归组）
    #          判据：某续行在课次区的非空率 < 80% 即认定存在合并单元格 → 该行 forward fill。
    #          2 行表头的子列名行通常 100% 填满，故不受影响（实测 NPMSWD 124/124）。
    #   (b) 宽表·单行表头（wide1-inline，v4.20）：子列名与课次名**同格**，取 `-` 后缀。
    span = hi - first_col + 1
    fill = []
    inline_leaf = {col: leaf for col, _seq, leaf in marks}
    for i in header_rows:
        ne = sum(1 for j in range(first_col, hi + 1)
                 if j < len(values[i]) and values[i][j] not in (None, ""))
        fill.append(ne < span * 0.8)
    for g in groups:
        g["items"] = []
        if kind == "narrow":
            continue
        if not header_rows:                     # (b) 内联子列名
            for j in range(g["start"], g["end"] + 1):
                # ⚠️ 插列（课次区中间那些不带 `第N次课-` 前缀的列，如「精题1刷」「出门测」）
                #    没有内联子列名 → 退回用**该列自己的表头文字**，这样它才能被
                #    profile.feishu.items 的词表命中；否则会变成 col28 这种无名项。
                leaf = inline_leaf.get(j) or (
                    cell_text(values[best][j])
                    if j < len(values[best]) and values[best][j] not in (None, "") else "")
                is_inline = j in inline_leaf
                g["items"].append({"col": j, "name": leaf, "leaf": leaf,
                                   "inline": is_inline})
            continue
        cur = ["" for _ in header_rows]         # (a) 续行子列名
        for j in range(g["start"], g["end"] + 1):
            txt = []
            for k, i in enumerate(header_rows):
                v = (cell_text(values[i][j])            # v4.27：表头也可能是富文本
                     if j < len(values[i]) and values[i][j] not in (None, "") else "")
                if v:
                    cur[k] = v
                txt.append(cur[k] if fill[k] else v)
            txt = [t for t in txt if t]
            g["items"].append({"col": j, "name": "/".join(txt),
                               "leaf": txt[-1] if txt else ""})

    # ---- 元数据列映射
    # ⚠️ v4.20：允许多列映射到同一字段（娜娜表把教务沟通拆成
    #    「首课沟通 / 中段回访 / 评价回访」三列）。第一列进 metaMap，
    #    其余进 metaDup，由 main 合并成一条（不静默丢弃任何列）。
    meta_map, meta_dup = {}, {}
    for j in range(0, meta_cols):
        txt = [cell_text(values[i][j]) for i in hdr_idx      # v4.27：富文本表头（六D谢胡「沟通优先级」）
               if j < len(values[i]) and values[i][j] not in (None, "")]
        if not txt:
            continue
        f = match_field(txt[0], meta_alias) or match_field("/".join(txt), meta_alias)
        if not f:
            continue
        if f not in meta_map:
            meta_map[f] = j
        elif meta_map[f] != j:
            meta_dup.setdefault(f, []).append(j)

    return {
        "kind": kind,
        "lessonRow": best,
        "headerRows": header_rows,
        "dataStart": data_start,
        "metaCols": meta_cols,
        "hi": hi,
        "groups": groups,
        "metaMap": meta_map,
        "metaDup": meta_dup,
        # ⚠️ 用**合并后的组数**，不是课次标记数：单行表头形态下每节课有 7 个标记，
        #    直接用标记数会把 18 节课报成 126 节。
        "lessons": len(groups),
        "perLesson": per_lesson,
        "skippedCols": skipped_cols,
    }


def fingerprint(values, spec):
    parts = []
    for i in [spec["lessonRow"]] + spec["headerRows"]:
        row = values[i] if i < len(values) else []
        parts.append("\x1f".join(str(v or "") for v in row))
    return hashlib.sha1("\x1e".join(parts).encode("utf-8")).hexdigest()[:16]


def count_done(lessons, done_set, alias):
    """按课次明细统计完成 / 未完成次数（v4.20 提为函数，供去重合并后**重算**用）。"""
    done_cnt, undone_cnt = {}, {}
    for ls in lessons:
        for k, v in ls["items"].items():
            leaf = k.split("/")[-1]            # 三维表：`课后任务/框架笔记` → 框架笔记
            base = alias.get(leaf, leaf)
            key = base if base in done_set else (leaf if leaf in done_set else None)
            if not key:
                continue
            st = is_done_val(v)
            if st is False:
                undone_cnt[key] = undone_cnt.get(key, 0) + 1
            elif st is True:
                done_cnt[key] = done_cnt.get(key, 0) + 1
    return done_cnt, undone_cnt


# ================================================================ 评级序列（v4.21）
# 「作业评级」这类列的值是 `A 优秀：全面掌握知识点，解题逻辑清晰` ——
# 只看「完成 / 未完成」会把**等级本身**丢掉。这里把逐节课的等级抽出来排成序列，
# 供报告侧判断**趋势**（上升 / 下滑 / 稳定）。
# ⭐ 由 profile.feishu.ratings 声明；**不声明 = 不启用**（孙一文团队的值是 ⭐/✘，天然不受影响）。
DEFAULT_RATING_LEVELS = ["A", "B", "C", "D"]
DEFAULT_RATING_ABSENT = ["未提交", "未交", "没交", "缺交", "未完成", "没做"]
RATING_LEVEL_RE = re.compile(r"^\s*([A-Za-z])(?![A-Za-z0-9])")   # 开头单个字母，后面不再跟字母数字
RATING_TREND_EPS = 0.5      # 前后半段均分差超过此值才算「上升/下滑」（A=4 B=3，差 1 分即明显变化）
RATING_MIN_RATED = 3        # 判趋势所需的**最少有效评级次数**
#   ⚠️ 实测娜娜团队：有效次数=2 的 187 人里 up 39 / down 38 —— **几乎对半，纯噪声**（两个点比不出趋势）。
#      卡到 ≥3 次后，「下滑」从 60 人收敛到 22 人（真实信号）；可用 rating.minRated 覆盖。


def extract_level(v, cfg):
    """从单元格值抽等级；不是**已声明**的等级 → None。

    ⚠️ 必须用 `levels` 白名单兜底：否则 `True` 会被抽成 `T`、`False` 抽成 `F`。
    """
    s = "" if v is True else str(v if v is not None else "").strip()
    if not s:
        return None
    m = (cfg.get("_re") or RATING_LEVEL_RE).match(s)
    if not m:
        return None
    lv = (m.group(1) or "").strip().upper()
    return lv if lv in cfg["_set"] else None


def build_ratings(lessons, ratings_cfg, alias):
    """逐节课抽等级 → 序列 + 趋势指标。`ratings_cfg` 为空则直接返回 {}（零开销）。

    产出结构（每个声明项一份）：
      seq          与 lessons 一一对齐的等级序列，None = 该课次没有有效等级
      absent       与 seq 对齐，1 = 明确缺席（「未提交」这类），0 = 其他
      counts       各等级次数（如 {"A": 2, "B": 1}）
      nRated / nAbsent / nLessons
      mean         按 levels 计分的均分（A=4 B=3 C=2 D=1）
      first / last 首次 / 最近一次有效等级
      early / late / delta / trend   前半 vs 后半均分及趋势（up / down / flat / na）
    """
    if not ratings_cfg:
        return {}
    out = {}
    for item, cfg in ratings_cfg.items():
        if item.startswith("_") or not isinstance(cfg, dict):
            continue
        levels = [str(x).strip().upper() for x in (cfg.get("levels") or DEFAULT_RATING_LEVELS)]
        score = {lv: len(levels) - i for i, lv in enumerate(levels)}   # A=4 B=3 C=2 D=1
        sub = {"_set": set(levels)}
        if cfg.get("extract"):
            sub["_re"] = re.compile(cfg["extract"])
        absent_words = {str(x).strip() for x in (cfg.get("absent") or DEFAULT_RATING_ABSENT)}

        seq, absent = [], []
        for ls in lessons:
            leaves = {k.split("/")[-1]: v for k, v in (ls.get("items") or {}).items()}
            raw = leaves.get(item)
            if raw is None:
                raw = leaves.get(alias.get(item, item))
            lv = extract_level(raw, sub)
            sv = "" if raw is True else str(raw if raw is not None else "").strip()
            seq.append(lv)
            # 「未提交」= 明确缺席；`#N/A`/空白 = 无记录（两者都无等级，但含义不同）
            absent.append(1 if (lv is None and sv in absent_words) else 0)

        rated = [x for x in seq if x]
        n_r = len(rated)
        counts = {}
        for x in rated:
            counts[x] = counts.get(x, 0) + 1
        rec = {"item": item, "levels": levels, "seq": seq, "absent": absent,
               "counts": {lv: counts[lv] for lv in levels if counts.get(lv)},
               "nRated": n_r, "nAbsent": sum(absent), "nLessons": len(seq),
               "minRated": max(2, int(cfg.get("minRated") or RATING_MIN_RATED)),
               "trend": "na"}

        # 末尾**连续未提交**次数 —— 不受样本量影响，是最直接的「人正在掉队」信号
        #（比「A→C」这种两点比较可靠得多：一直没交比降一级严重）
        tail_absent = 0
        for a in reversed(absent):
            if a:
                tail_absent += 1
            else:
                break
        rec["tailAbsent"] = tail_absent

        if n_r:
            scores = [score[x] for x in rated]
            rec["mean"] = round(sum(scores) / n_r, 2)
            rec["first"], rec["last"] = rated[0], rated[-1]
            if n_r >= rec["minRated"]:                 # ⚠️ 样本不足时**不下趋势结论**
                h = (n_r + 1) // 2                     # 前半（有效次数为奇数时多含一个）
                early, late = scores[:h], scores[h:]
                if late:
                    em, lm = sum(early) / len(early), sum(late) / len(late)
                    rec["early"], rec["late"] = round(em, 2), round(lm, 2)
                    rec["delta"] = round(lm - em, 2)
                    rec["trend"] = ("up" if lm - em > RATING_TREND_EPS else
                                    "down" if lm - em < -RATING_TREND_EPS else "flat")
        out[item] = rec
    return out


def merge_records(recs):
    """把同一 `(姓名, 课程)` 的多条记录**合并**成一条（dedupe.mode = "merge"）。

    什么时候用：两个维度**互为补充**而不是重复维护时。
      实测娜娜团队：4 个「教务-XXX」sheet 与 13 个联席 sheet 覆盖同一批学生
      （992 组撞键），**教务侧课次 ≥ 联席侧（45 更多 / 947 相同 / 0 更少）**，
      但教务表**没有**「学生情况备注 / 首课沟通 / 中段回访 / 评价回访」这些元数据
      —— 取任何单边都会丢东西。
    怎么合：**按课次号**逐节课取「子项更多」的那份作**基底**，
      再用其余份补上**基底没有的项**（键并集，冲突时以基底为准）—— 元数据逐字段取第一个非空值。
      这样既不重复计数，也不漏掉任何一节课、不漏掉**任何一项**。

    ⚠️ v4.21.1 修：原实现「同 seq 只取 items 更多的那份」（不做键并集）会**丢掉另一份独有的项**。
      实测娜娜「周文峰」表行75 王子轩：该课次只有 1 项（`几综模块测=通关！`），
      而同键的「教务-邓琨薇」表行24 该课次有 7 项（**全是 `#N/A` 无数据**）→ 7 > 1 ⇒ 取了教务那份，
      学生真实的「几综模块测 通关」被 7 个 `#N/A` 顶掉，该列凭空少一格（11→10）。
      这是「取更多」与「取全部」的语义差异 —— **多不等于全**。
    """
    base = max(recs, key=lambda x: (len(x["lessons"]),
                                    sum(len(l["items"]) for l in x["lessons"])))
    groups = collections.defaultdict(list)
    for r in recs:
        for l in r["lessons"]:
            groups[l["seq"]].append((l, r))
    byseq, n_filled = {}, 0
    for seq in sorted(groups):
        cand = sorted(groups[seq], key=lambda x: -len(x[0]["items"]))
        l0, r0 = cand[0]
        if len(cand) > 1:
            items = dict(l0["items"])
            for l, _r in cand[1:]:
                for k, v in l["items"].items():
                    if k not in items:
                        items[k] = v
                        n_filled += 1
            if len(items) != len(l0["items"]):
                l0 = dict(l0)
                l0["items"] = items
        byseq[seq] = (l0, r0)
    out = dict(base)
    out["lessons"] = [byseq[k][0] for k in sorted(byseq)]
    out["nLessons"] = len(out["lessons"])
    out["mergeFilled"] = n_filled          # v4.21.1：本次合并补回的「独有项」个数
    meta = {}
    for r in recs:
        for k, v in (r.get("meta") or {}).items():
            if v and not meta.get(k):
                meta[k] = v
    out["meta"] = meta
    out["mergedFrom"] = [{"sheetId": r["sourceSheetId"], "sheetTitle": r.get("sourceSheetTitle"),
                          "row": r["sourceRow"], "nLessons": r["nLessons"],
                          "usedLessons": sum(1 for l in out["lessons"]
                                             if byseq[l["seq"]][1] is r)}
                         for r in recs]
    out["sourceSheetTitle"] = " + ".join(
        dict.fromkeys(str(r.get("sourceSheetTitle")) for r in recs))
    return out


# ================================================================ 拆分
def split_items(raw, done_set, ignore_set, alias):
    """窄表格子 `出勤,作业,框架笔记` → (命中标准项, 未知词, 被忽略词)。"""
    got, unknown, ignored = [], [], []
    for w in re.split(r"[,，;；/、\s]+", str(raw)):
        w = w.strip()
        if not w:
            continue
        w2 = alias.get(w, w)
        if w2 in ignore_set or w in ignore_set:
            ignored.append(w)
        elif w2 in done_set:
            got.append(w2)
        else:
            unknown.append(w)
    return got, unknown, ignored


# ================================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--raw", default=None, help="默认 <workdir>/feishu_raw.json")
    ap.add_argument("--out", default=None, help="默认 <workdir>/snapshot")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--no-fingerprint-check", action="store_true")
    args = ap.parse_args()

    wd = common.workdir(args)
    rpath = args.raw or os.path.join(wd, "feishu_raw.json")
    if not os.path.exists(rpath):
        raise SystemExit(f"❌ 找不到 {rpath}\n   请先跑：python scripts/pull_feishu.py --workdir {wd}")
    raw = load_json(rpath)

    ppath = args.profile or os.path.join(wd, "profile.json")
    prof = load_json(ppath, {}) or {}
    fs = prof.get("feishu") or {}
    items_cfg = fs.get("items") or {}
    DONE = set(items_cfg.get("done") or DEFAULT_DONE)
    IGNORE = set(items_cfg.get("ignore") or DEFAULT_IGNORE)
    PASSIVE = set(items_cfg.get("passive") or DEFAULT_PASSIVE)
    ALIAS = dict(DEFAULT_ALIAS)
    ALIAS.update(items_cfg.get("alias") or {})
    PROD_MAP = fs.get("sheetProductMap") or {}     # sheetId → 课程全名（sheet 名不是课程名时用）
    # ⭐ 列角色覆盖：表头文字匹配不到的列（表头为空 / 按课程分段的合并单元格）。
    #    实测某生表用 A 列按课程分段，但 A 列表头为空 → 只能显式声明。
    ROLE_OVERRIDE = {k: v for k, v in (fs.get("columnRoles") or {}).items()
                     if not k.startswith("_")}
    # ⭐ 课程名别名：飞书表里写的是**简写**（如「进度二211层日E王」），
    #    后台课程名是**全名**（「2026Q-SX-高二数学进度二211层日E王」）。
    #    解析出的课程名最后统一过一遍这张表，命中即换成全名。
    COURSE_ALIAS = {k: v for k, v in (fs.get("courseAlias") or {}).items()
                    if not k.startswith("_")}
    # ⭐ v4.20：元数据列别名覆盖（团队自定义叫法，优先级高于内置默认表）
    META_ALIAS = {k: v for k, v in (fs.get("metaAlias") or {}).items()
                  if not k.startswith("_")}
    # ⭐ v4.20：尾部公式辅助列（表头命中即跳过，常见 `第N次课数字`）
    SKIP_PATS = fs.get("columnSkipPatterns")
    if SKIP_PATS is None:
        SKIP_PATS = SKIP_COL_PATTERNS_DEFAULT
    if SKIP_PATS is False:
        SKIP_PATS = []
    # ⭐ v4.20：长文本告警阈值（各团队正常值长度差异大，见 LONG_VAL_LEN 注释）
    LONG_LEN = fs.get("longValLen")
    if not isinstance(LONG_LEN, (int, float)) or LONG_LEN <= 0:
        LONG_LEN = LONG_VAL_LEN
    # ⭐ v4.21：评级序列声明（有 ABCD 等级的团队用；不声明 = 不启用）
    RATINGS = {k: v for k, v in (fs.get("ratings") or {}).items()
               if not k.startswith("_") and isinstance(v, dict)}

    out_dir = args.out or os.path.join(wd, "snapshot")
    os.makedirs(out_dir, exist_ok=True)

    roster = load_json(os.path.join(out_dir, "roster.json"), []) or []
    roster_norm = {r.get("stuNameNorm") for r in roster if r.get("stuNameNorm")}
    in_roster = bool(roster_norm)

    degraded = []
    fingerprints = {}
    sheet_reports = []
    students = []

    # ⚠️ v4.20：取数中途失败时 pull_feishu 会落下 `partial: true` 的半成品文件
    #    （这样中断后 --resume 能续，不必重跑 20 分钟）。
    #    拿着半成品出报告 = 静默漏学生，必须显式拦下来告知。
    if raw.get("partial"):
        degraded.append({
            "field": "feishu.raw",
            "reason": f"feishu_raw.json 是**半成品**：只拉到 {len(raw.get('sheets') or [])}"
                      f"/{raw.get('plannedSheets')} 个 sheet",
            "impact": "本次分析**只覆盖已拉到的那些 sheet**，其余 sheet 的学生会整批缺席。"
                      "请重跑 pull_feishu.py --resume 补齐后再生成报告。",
        })
        print(f"⚠️⚠️ 注意：feishu_raw.json 只拉到 {len(raw.get('sheets') or [])}"
              f"/{raw.get('plannedSheets')} 个 sheet（半成品）")

    for sh in raw.get("sheets") or []:
        vals = sh.get("values") or []
        sid, title = sh.get("sheetId"), sh.get("title") or ""
        spec = detect(vals, skip_pats=SKIP_PATS, meta_alias=META_ALIAS)
        if spec is None:
            sheet_reports.append({"sheetId": sid, "title": title, "structure": "flat",
                                  "students": 0, "note": "无课次表头，非学情表 → 跳过"})
            degraded.append({
                "field": f"feishu.sheet[{sid}]",
                "reason": f"「{title}」未探测到「第N次课/第N节课」形式的课次表头",
                "impact": "该 sheet 未纳入；如它确实是学情表，需人工确认表头写法",
            })
            continue

        fp = fingerprint(vals, spec)
        fingerprints[sid] = {"hash": fp, "kind": spec["kind"],
                             "headerRows": [spec["lessonRow"]] + spec["headerRows"],
                             "lessons": spec["lessons"], "title": title}

        mm = spec["metaMap"]

        # ---- 列角色覆盖（profile.feishu.columnRoles[sid]）
        #      表头文字匹配不到的列在这里补：实测某生表 A 列是课程名，
        #      但 A 列表头为空、且按课程用了合并单元格 → 文字匹配必然落空。
        fill_fields = set()
        for field, cfg in (ROLE_OVERRIDE.get(sid) or {}).items():
            if field.startswith("_"):
                continue
            if isinstance(cfg, (str, int)):
                cfg = {"col": cfg}
            ci = col_index(cfg.get("col"))
            if ci is None:
                degraded.append({
                    "field": f"feishu.columnRoles[{sid}].{field}",
                    "reason": f"列角色配置的 col 无法解析：{cfg.get('col')!r}",
                    "impact": "该覆盖未生效，此字段仍按表头文字匹配",
                })
                continue
            if field in mm and mm[field] != ci:
                degraded.append({
                    "field": f"feishu.columnRoles[{sid}].{field}",
                    "reason": f"列角色覆盖把 {field} 由 {colname(mm[field]+1)} 列为 {colname(ci+1)} 列",
                    "impact": "人工覆盖优先于表头文字匹配，请确认无误",
                })
            mm[field] = ci
            if cfg.get("fillDown"):
                fill_fields.add(field)
        carry = {}                     # fillDown 用：跨行记住上一个非空值

        # ⚠️ 课程名解析链：① 行内「课程」列（含 columnRoles 覆盖）> ② profile.sheetProductMap
        #    > ③ sheet 名；最后统一过 courseAlias（简写 → 后台全名）。
        #    实测 sheet 名约 1/3 不是课程全名（「高二肖211」「某生」），必须显式声明。
        #    ⚠️ 判空必须用 `is None` —— 列 A 的索引是 0，而 `not 0` 为真会把 A 列误判成「没有课程列」。
        prod_col = mm.get("productName")
        prod_fallback = PROD_MAP.get(sid)
        if (prod_col is None) and (not prod_fallback) and not re.search(r"20\d\d[QS]-", title):
            degraded.append({
                "field": f"feishu.sheet[{sid}].productName",
                "reason": f"「{title}」表内无「课程」列，sheet 名也不是课程全名",
                "impact": "该 sheet 学生的 productName 保留 sheet 名原文，**与花名册对齐会全部落空** → "
                          "请在 profile.feishu.sheetProductMap 里补 {\"%s\": \"<课程全名>\"}" % sid,
            })
        sheet_unknown, sheet_ignored, sheet_badname = [], [], 0
        sheet_placeholder = 0
        sheet_undone, sheet_longval = {}, []
        n_ok = 0
        for ri in range(spec["dataStart"], len(vals)):
            row = vals[ri]

            def cell(j, field=None):
                raw = row[j] if (j is not None and j < len(row)) else None
                v = cell_text(raw)                     # v4.27：兼容图片/富文本单元格
                if field and field in fill_fields:     # 合并单元格：继承上一个非空值
                    if v:
                        carry[field] = v
                    return carry.get(field, "")
                return v

            name = cell(mm.get("stuName"))
            if not name:
                # 整行空白 / 只有备注 —— 不计
                if not any(v not in (None, "") for v in row):
                    continue
                sheet_badname += 1
                continue
            if name in PLACEHOLDER_NAMES:
                # ⚠️ 姓名列写的是表头文字（多为误粘）→ 无法归属，跳过并报告
                sheet_placeholder += 1
                continue

            lessons = []
            for g in spec["groups"]:
                if spec["kind"] == "narrow":
                    rawv = cell(g["start"])
                    if not rawv:
                        continue
                    got, unk, ign = split_items(rawv, DONE, IGNORE, ALIAS)
                    sheet_unknown += unk
                    sheet_ignored += ign
                    lessons.append({"seq": g["seq"], "raw": rawv,
                                    "items": {k: True for k in got}})
                else:
                    items, unk = {}, []
                    for it in g["items"]:
                        v = cell(it["col"])
                        if not v:
                            continue
                        nm = it["name"] or ("col%s" % colname(it["col"] + 1))
                        items[nm] = v
                        leaf = it.get("leaf") or nm
                        base = ALIAS.get(leaf, leaf)
                        if base in DONE or leaf in DONE:
                            pass                              # 计入完成率
                        elif (base in IGNORE or leaf in IGNORE
                              or base in PASSIVE or leaf in PASSIVE):
                            pass                              # 出勤类 / 记录型：保留原值，不统计
                        else:
                            unk.append(leaf)
                    if not items:
                        continue
                    sheet_unknown += unk
                    lessons.append({"seq": g["seq"], "raw": None, "items": items})

            # ---- 课程名：列 → 兜底映射 → sheet 名，最后统一过别名（简写 → 全名）
            pname_raw = cell(prod_col, field="productName") if prod_col is not None else ""
            from_col = bool(pname_raw)
            if not pname_raw:
                pname_raw = prod_fallback or title
            pname = COURSE_ALIAS.get(pname_raw, pname_raw)
            # ---- 完成 / 未完成分开计（v4.19.3）：别把 ✘ 当完成
            done_cnt, undone_cnt = count_done(lessons, DONE, ALIAS)
            # v4.50：「总结」列的三档是 已交 / 佳作 / 未交 —— 「佳作」是**质量更高的完成**
            #    （实测娜娜团队 503 格），只算进完成数会把「写得特别好」和「按时交了」抹平，
            #    故单独计数，供报告在 hover 里点出来。
            n_excellent = 0
            for _ls in lessons:
                for _k, _v in (_ls.get("items") or {}).items():
                    if _k.split("/")[-1].strip() == "总结" and _v is not True:
                        if "佳作" in str(_v):
                            n_excellent += 1
            if n_excellent:
                excellent_cnt = {"总结": n_excellent}
            else:
                excellent_cnt = {}
            for k, v in undone_cnt.items():
                sheet_undone[k] = sheet_undone.get(k, 0) + v
            # v4.19.3：超长值多半是**手记串列**（写到了课次格里）→ 报出来核对
            for ls in lessons:
                for k, v in ls["items"].items():
                    sv = "" if v is True else str(v).strip()
                    if len(sv) > LONG_LEN:
                        sheet_longval.append(f"{k.split('/')[-1]}={sv[:36]}")

            meta = {f: cell(j) for f, j in mm.items()
                    if f not in ("stuName", "productName") and cell(j)}
            # ⚠️ v4.20：同一字段的额外列（娜娜表把教务沟通拆成 3 列）合并成一条，
            #    保证「不静默丢弃任何一列」。
            for f, cols in (spec.get("metaDup") or {}).items():
                parts = [meta.get(f) or ""] + [cell(j) for j in cols if cell(j)]
                parts = [p for p in parts if p]
                if parts:
                    meta[f] = " / ".join(parts)
            # v4.27：宽表的「加课」是**课次级子列**（某生表每节课「课程服务」下都有），
            #   非空时并进 meta.extraClass，与元数据列里的加课记录合成同一条（不丢）。
            _xc_lessons = []
            for _l in lessons:
                for _k, _v in (_l.get("items") or {}).items():
                    _leaf = _k.split("/")[-1].strip()
                    _sv = "" if _v is True else str(_v).strip()
                    if _leaf == "加课" and _sv not in ("", "True", "False"):
                        _xc_lessons.append(f"第{_l.get('seq')}次课：{_sv}")
            if _xc_lessons:
                _prev = meta.get("extraClass")
                meta["extraClass"] = "；".join(([_prev] if _prev else []) + _xc_lessons)
            students.append({
                "studentNameNorm": common.norm_name(name),
                "stuName": name,
                "productName": pname,
                "productRaw": (pname_raw if pname_raw != pname else None),
                "productFrom": ("column" if from_col
                                else ("profile" if prod_fallback else "sheet-name")),
                "coTeacherName": cell(mm.get("coTeacherName")) or None,
                "classTeacherName": cell(mm.get("classTeacherName")) or None,
                "sourceSheetId": sid,
                "sourceSheetTitle": title,
                "sourceRow": ri + 1,
                "structure": spec["kind"],
                "nLessons": len(lessons),
                "done": done_cnt,
                "undone": undone_cnt,
                "excellent": excellent_cnt,
                "ratings": build_ratings(lessons, RATINGS, ALIAS),
                "lessons": lessons,
                "meta": meta,
            })
            n_ok += 1

        uniq_unk = sorted(set(sheet_unknown))
        if uniq_unk:
            degraded.append({
                "field": f"feishu.sheet[{sid}].items",
                "reason": f"「{title}」出现未登记的子列名/词：{uniq_unk}",
                "impact": "这些项**已原样保留**在 lessons.items 中，但未计入任何「完成率」统计；"
                          "如属学情项，请加进 profile.feishu.items.done",
            })
        if sheet_badname:
            degraded.append({
                "field": f"feishu.sheet[{sid}].stuName",
                "reason": f"「{title}」有 {sheet_badname} 行**有数据但学生姓名列为空**",
                "impact": "这些行未纳入（多为残留/预留行：只有个别课次格有 √）。"
                          "如确认是真实学生，请回表补姓名。",
            })
        if sheet_placeholder:
            degraded.append({
                "field": f"feishu.sheet[{sid}].stuName",
                "reason": f"「{title}」有 {sheet_placeholder} 行的姓名列写成了表头文字（如「学生」）",
                "impact": "这些行**有课次记录但无法归属到学生**（实测同行的「课程/班型」列也写成了「课程」，"
                          "多为复制表头时误粘）→ 已跳过。**请回表修正**，否则这几个学生会漏掉。",
            })
        # v4.19.3：超长值多半是**手记串列**（写到了课次列里）→ 报出来核对，别静默当成完成
        if sheet_longval:
            uniq_lv = sorted(set(sheet_longval))[:5]
            degraded.append({
                "field": f"feishu.sheet[{sid}].items",
                "reason": f"「{title}」有 {len(sheet_longval)} 处课次格内是长文本（疑似手记串列）：{uniq_lv}",
                "impact": "这些值已按「完成」计入（无法判定为未完成）；请团队核对该格是否填错位置",
            })
        if sheet_undone:
            print(f"       ↳ 未完成项："
                  + ", ".join(f"{k} {v}" for k, v in sorted(sheet_undone.items())))
        sheet_reports.append({
            "sheetId": sid, "title": title, "structure": spec["kind"],
            "lessonRow": spec["lessonRow"] + 1,
            "headerRows": [i + 1 for i in spec["headerRows"]],
            "dataStart": spec["dataStart"] + 1,
            "metaCols": spec["metaCols"],
            "lessons": spec["lessons"],
            "perLesson": spec.get("perLesson"),
            "skippedCols": spec.get("skippedCols") or [],
            "students": n_ok,
            "studentRowsSkipped": {"noName": sheet_badname, "placeholderName": sheet_placeholder},
            "metaMapped": {f: colname(j + 1) for f, j in sorted(mm.items())},
            "metaMissing": [f for f, _ in META_FIELDS if f not in mm],
            "unknownItems": uniq_unk,
            "ignoredWords": sorted(set(sheet_ignored)),
            "undoneItems": sheet_undone,          # v4.19.3：该 sheet 各完成项的「未完成」次数
            # ---- 课程名溯源（v4.19.1）：来源 / 列 / 解析出的课程清单 / 被别名替换的原值
            "productSource": ("column" if prod_col is not None
                              else ("profile" if prod_fallback else "sheet-name")),
            "productCol": (colname(prod_col + 1) if prod_col is not None else None),
            "courses": [{"name": k, "n": v} for k, v in
                        collections.Counter(s["productName"] for s in students
                                            if s["sourceSheetId"] == sid).most_common()],
            "aliasedRaw": sorted({s["productRaw"] for s in students
                                  if s["sourceSheetId"] == sid and s["productRaw"]}),
        })
        meta_info = ", ".join(f"{f}←{colname(j+1)}" for f, j in sorted(mm.items()))
        skip_info = ""
        if spec.get("skippedCols"):
            skip_info = f" ｜ 按模式跳过 {len(spec['skippedCols'])} 列"
        print(f"✔ {sid:8s} {title[:26]:28s} {spec['kind']:7s} "
              f"表头{1+len(spec['headerRows'])}行 数据起第{spec['dataStart']+1}行 "
              f"{spec['lessons']}课×{spec.get('perLesson')}列 {n_ok} 人{skip_info}")
        print(f"    元数据: {meta_info}")

    # ---- 重名统计（**去重前**，全量）：同一学生出现在多张 sheet
    #      ⚠️ 课程名**归一之后**才看得出「同课程重复」—— 这是归一化的收益，不是 bug。
    #      ⇒ join 主键必须是 **(studentNameNorm, productName) 复合键**，
    #        只按姓名 join 会把两门课的数据混在一起。
    by_norm = collections.defaultdict(list)
    for s in students:
        by_norm[s["studentNameNorm"]].append(s)
    dup_norm = {n: v for n, v in by_norm.items() if len(v) > 1}
    n_cross = sum(1 for v in dup_norm.values()
                  if len({x["productName"] for x in v}) > 1)
    n_same = len(dup_norm) - n_cross
    if n_same:
        degraded.append({
            "field": "feishuLearning[]",
            "reason": f"{n_same} 人在**同一课程**下出现多条记录（不同 sheet）",
            "impact": "已按 profile.feishu.dedupe.order 取优先级最高的一份入主表；"
                      "被覆盖的记录见 feishuMeta.dedupe.dropped（可追溯，**未静默丢弃**）",
        })

    # ---- 复合键去重（v4.19.2）：同一 (studentNameNorm, productName) 多条 → 按 sheet 优先级取一份
    #      用户 2026-10-16 拍板「固定取马表」（马表记录更全：实测 27/28 人课次数更多、
    #      且 27/28 两份课次有交集 ⇒ 属重复维护，不是互补）。
    #      ⚠️ order 里**没列出**的 sheet 排在后面（按各 sheet 的解析顺序，稳定排序）。
    order = (fs.get("dedupe") or {}).get("order") or []
    # ⭐ v4.20：`mode` 决定撞键怎么处理
    #    pick（默认）= 按 order 取优先级最高的一份，其余丢进 dropped[]
    #    merge       = **合并**成一条（按课次号取更完整的子项）—— 用于两个维度**互为补充**时
    dedupe_cfg = fs.get("dedupe") or {}
    mode = str(dedupe_cfg.get("mode") or "pick").lower()
    rank = {sid: i for i, sid in enumerate(order)}
    # ⭐ preferNonEmpty（默认 true）：优先级最高的那份若**零课次**（只有元数据、等于没有任何学习证据），
    #    改取有课次的那份 —— 否则会把有数据的学生「去重成空」。
    #    这正是「取更全」的意图；属于**显式例外**，每次命中都记进 anomalies + degraded。
    prefer_nonempty = (fs.get("dedupe") or {}).get("preferNonEmpty", True)
    kept, dropped, anomalies, n_collide = [], [], [], 0
    n_merged_away = 0
    n_merge_filled = 0
    merge_log = []
    if order and mode == "merge":
        # ---- 合并模式：两个维度互为补充（如「教务表」有课次但没备注、「联席表」反之）
        bykey = collections.defaultdict(list)
        for s in students:
            bykey[(s["studentNameNorm"], s["productName"])].append(s)
        merge_log = []
        for k, v in bykey.items():
            if len(v) == 1:
                kept.append(v[0])
                continue
            n_collide += 1
            m = merge_records(v)
            m["done"], m["undone"] = count_done(m["lessons"], DONE, ALIAS)
            # ⭐ v4.21：合并后 lessons 变了，评级序列必须跟着重算（否则序列与课次错位）
            m["ratings"] = build_ratings(m["lessons"], RATINGS, ALIAS)
            kept.append(m)
            n_merged_away += len(v) - 1
            n_merge_filled += m.get("mergeFilled") or 0
            merge_log.append({
                "studentNameNorm": k[0], "productName": k[1],
                "from": [{"sheetId": r["sourceSheetId"], "sheetTitle": r.get("sourceSheetTitle"),
                          "row": r["sourceRow"], "nLessons": r["nLessons"]} for r in v],
                "mergedLessons": m["nLessons"],
                "filledItems": m.get("mergeFilled") or 0,
            })
        # ⚠️ 合并**不是**「丢弃」：记录数变少但学习证据没少。
        #    仍要记进 meta，否则「行数守恒」自检对不上会让人以为丢数据。
        degraded.append({
            "field": "feishuLearning[].dedupe",
            "reason": f"{n_collide} 组 `(学生, 课程)` 撞键，已按 **merge 模式合并**"
                      f"（共合并掉 {n_merged_away} 条重复记录）"
                      + (f"；合并时**补回 {n_merge_filled} 项**只存在于其中一份的独有项"
                         f"（v4.21.1 修复：原「取项更多的那份」会把它们丢掉）"
                         if n_merge_filled else ""),
            "impact": "merge = 按**课次号**逐节课取「子项更多」的那份作基底，"
                      "**再补上其余份独有的项**（键并集，冲突以基底为准）+ 元数据逐字段取第一个非空值。"
                      "**不丢弃任何一节课、也不丢弃任何一项**；逐组明细见 feishuMeta.dedupe.merged。"
                      "⚠️ 若两表其实是在重复维护同一批数据，应改用 mode=pick + order。",
        })
        students = kept

    elif order:
        bykey = collections.defaultdict(list)
        for s in students:
            bykey[(s["studentNameNorm"], s["productName"])].append(s)
        for v in bykey.values():
            v2 = sorted(v, key=lambda x: rank.get(x["sourceSheetId"], len(order) + 1))
            win = v2[0]
            if prefer_nonempty and not win["lessons"]:
                alt = sorted((x for x in v2[1:] if x["lessons"]),
                             key=lambda x: -x["nLessons"])
                if alt:
                    anomalies.append({
                        "stuName": win["stuName"],
                        "productName": win["productName"],
                        "wouldPick": {"sheetId": win["sourceSheetId"], "nLessons": 0},
                        "actuallyPicked": {"sheetId": alt[0]["sourceSheetId"],
                                           "nLessons": alt[0]["nLessons"]},
                    })
                    v2 = [alt[0]] + [x for x in v2 if x is not alt[0]]
            kept.append(v2[0])
            if len(v2) > 1:
                n_collide += 1
                for x in v2[1:]:
                    dropped.append({
                        "stuName": x["stuName"],
                        "studentNameNorm": x["studentNameNorm"],
                        "productName": x["productName"],
                        "sourceSheetId": x["sourceSheetId"],
                        "sourceRow": x["sourceRow"],
                        "nLessons": x["nLessons"],
                        "done": x["done"],
                        "keptFrom": v2[0]["sourceSheetId"],
                    })
        # ⚠️ keptFrom 与自己同 sheet = **表内重复**（不是优先级问题）→ 单独报，属数据错误
        #    （实测马表内有 1 人出现两次）
        same_sheet = [d for d in dropped if d["sourceSheetId"] == d["keptFrom"]]
        if same_sheet:
            degraded.append({
                "field": "feishuLearning[]",
                "reason": f"{len(same_sheet)} 条记录在**同一张 sheet 内**重复（同一学生 + 同一课程）",
                "impact": "这不是 sheet 优先级问题，而是**表内数据重复** → 建议请团队核对；"
                          "当前保留行号靠前的那条",
            })
        if anomalies:
            degraded.append({
                "field": "feishuLearning[].dedupe",
                "reason": f"{len(anomalies)} 人优先级最高的那份**零课次**（只有元数据），"
                          f"已改取有课次的那份",
                "impact": "属**显式例外**（「取更全」的意图）：不改的话会把有学习数据的学生去重成空。"
                          "逐条详见 feishuMeta.dedupe.anomalies；如需严格按 order，"
                          "把 profile.feishu.dedupe.preferNonEmpty 设为 false",
            })
        students = kept

    # ---- 空课次学生（只有元数据、未填课次）—— 如实报告，不当成「未完成」
    n_empty = sum(1 for s in students if not s["lessons"])
    if n_empty:
        degraded.append({
            "field": "feishuLearning[].lessons",
            "reason": f"{n_empty} 人在飞书表里只有元数据、没有任何课次记录",
            "impact": "这些学生的飞书学习证据为空（属**数据缺失**，不等于「未完成」）；"
                      "报告里其学习轴仍以系统出勤 / 小黑板为准",
        })

    # ---- 课程名「不像课程全名」（v4.20，娜娜团队实测 30/1117 条）
    #      ⚠️ 这些学生的飞书块**匹配不上报告**（报告按 (姓名, 课程) 复合键取），
    #        属于**表内数据污染**，必须点名到行让团队去修，否则会被当成「这个学生没填表」。
    #      ⚠️ 「课程名不是全名」有**两种成因**，团队要做的修表动作完全不同，必须分开点名：
    #         ① 课程列**空** → 适配器按 productFrom="sheet-name" 兜底成 sheet 名（娜娜的 sheet 名=老师名）
    #            ⇒ 团队要做的是**补填**课程名（不是改错字）
    #         ② 课程列**有值但不是全名**（`#N/A` 公式错、误粘老师名、简写）
    #            ⇒ 团队要做的是**改值**（简写可先用 courseAlias 顶上）
    bad_prod = collections.Counter()
    bad_rows = collections.defaultdict(list)
    bad_by_from = collections.Counter()          # 成因分类
    bad_detail = []                              # 完整清单（供待修清单页/报告消费）
    for s in students:
        p = str(s.get("productName") or "")
        if re.search(r"20\d\d[QS]-", p):
            continue
        src = str(s.get("productFrom") or "")
        cause = "emptyCol" if src == "sheet-name" else "badValue"
        bad_prod[p] += 1
        bad_by_from[cause] += 1
        bad_detail.append({
            "cause": cause, "productName": p, "productRaw": s.get("productRaw"),
            "sheetId": s.get("sourceSheetId"), "sheetTitle": s.get("sourceSheetTitle"),
            "row": s.get("sourceRow"), "stuName": s.get("stuName"),
            "coTeacherName": s.get("coTeacherName"),
            "classTeacherName": s.get("classTeacherName"),
            "nLessons": s.get("nLessons"),
        })
        if len(bad_rows[p]) < 3:
            bad_rows[p].append(f'{s.get("sourceSheetTitle")}#{s["sourceRow"]}({s["stuName"]})')
    if bad_prod:
        detail = "；".join(f"`{k}`×{v}（如 {', '.join(bad_rows[k])}）"
                          for k, v in bad_prod.most_common(5))
        n_empty, n_bad = bad_by_from.get("emptyCol", 0), bad_by_from.get("badValue", 0)
        cause_txt = []
        if n_empty:
            cause_txt.append(f"**{n_empty} 条是「课程列没填」**（凑巧与 sheet 名同名，"
                             f"如娜娜的 sheet 名=老师名）→ 要**补填**课程名")
        if n_bad:
            cause_txt.append(f"**{n_bad} 条是「课程列填了非课程内容」**"
                             f"（`#N/A` 公式错误值 / 误粘老师名 / 写了简写）→ 要**改值**")
        degraded.append({
            "field": "feishuLearning[].productName",
            "reason": f"{sum(bad_prod.values())} 条记录的课程名**不是后台课程全名**：{detail}。"
                      + "；".join(cause_txt) + "。",
            "impact": "这些学生的飞书学习记录**不会出现在报告里**（报告按 (姓名, 课程) 复合键匹配）"
                      "—— 不是「显示得不准」，而是**整条静默缺席**。逐行坐标见 "
                      "feishuMeta.badCourses（可直接拿去核对表格）。**请回表修正**。",
        })

    # ---- 对齐花名册（去重后）
    joined = unmatched = 0
    unjoined_names = []
    if in_roster:
        for s in students:
            if s["studentNameNorm"] in roster_norm:
                joined += 1
            else:
                unmatched += 1
                unjoined_names.append(s["stuName"])
        if unmatched:
            degraded.append({
                "field": "feishuLearning[].studentNameNorm",
                "reason": f"{unmatched} 名飞书表学生不在花名册中（花名册 {len(roster_norm)} 人）",
                "impact": "这些学生的飞书数据**不会参与报告**（报告以花名册为分母）；"
                          "若人数偏多，通常意味着该 sheet 属于别的课程/联席",
            })

    # ---- 表头指纹比对（防「团队插了一列 → 全列静默错位」）
    old_meta = load_json(os.path.join(out_dir, "feishuMeta.json"), {}) or {}
    old_fp = old_meta.get("fingerprints") or {}
    for sid, info in fingerprints.items():
        o = old_fp.get(sid)
        if o and o.get("hash") and o["hash"] != info["hash"]:
            degraded.append({
                "field": f"feishu.sheet[{sid}].header",
                "reason": f"「{info['title']}」表头已变化（指纹 {o['hash']} → {info['hash']}）",
                "impact": "列映射已按新表头重建。若团队只是插了一列，请抽查 items 是否仍对得上；"
                          "若列的语义变了，需更新 profile.feishu.items",
            })

    meta = {
        "generatedAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + TZ,
        "source": "feishu",
        "spreadsheetToken": raw.get("spreadsheetToken"),
        "wikiSource": raw.get("wikiSource"),
        "wikiTitle": raw.get("wikiTitle"),
        # ⚠️ 团队名以 **profile 为准**（它是配置真身）；raw 里的 team 只是「拉取那一刻」的快照，
        #    改了 profile 的 team 却读 raw 会导致改了名不生效（实测孙一文改名后 meta 仍报旧名）。
        "team": prof.get("team") or raw.get("team"),
        "counts": {"sheets": len(sheet_reports), "students": len(students),
                   "withLessons": sum(1 for s in students if s["lessons"]),
                   "distinctStudents": len({s["studentNameNorm"] for s in students}),
                   "multiCourse": n_cross,
                   "droppedByDedupe": len(dropped)},
        "joinKey": "(studentNameNorm, productName) —— 复合键；只按姓名 join 会串课",
        "dedupe": ({"mode": mode, "order": order, "preferNonEmpty": prefer_nonempty,
                    "collidedKeys": n_collide, "droppedCount": len(dropped),
                    "mergedAway": n_merged_away,
                    "filledItems": n_merge_filled,      # v4.21.1：补回的独有项总数
                    "anomalies": anomalies, "dropped": dropped[:300],
                    "merged": (merge_log[:300]
                               if order and mode == "merge" else None)}
                   if order else None),
        "items": {"done": sorted(DONE), "ignore": sorted(IGNORE),
                  "passive": sorted(PASSIVE), "alias": ALIAS},
        "ratings": {k: {"levels": v.get("levels") or DEFAULT_RATING_LEVELS,
                        "labels": v.get("labels") or {},
                        "extract": v.get("extract"),
                        "absent": v.get("absent") or DEFAULT_RATING_ABSENT}
                    for k, v in RATINGS.items()},
        "courseAlias": COURSE_ALIAS,
        "sheetProductMap": PROD_MAP,
        # ⭐ v4.21：课程名有问题的**逐行完整清单**（供「待修清单」页 / 报告直接消费，
        #    免得下游再解析一遍原始表）。cause: emptyCol=课程列空 / badValue=填了非课程内容。
        "badCourses": {
            "total": len(bad_detail),
            "byCause": dict(bad_by_from),
            "rows": bad_detail,
        },
        "sheets": sheet_reports,
        "fingerprints": fingerprints,
        "rosterJoin": ({"roster": len(roster_norm), "joined": joined,
                        "unmatched": unmatched,
                        "pct": round(joined / len(students) * 100, 1) if students else 0.0,
                        "unmatchedNames": unjoined_names[:30]}
                       if in_roster else None),
        "degraded": degraded,
    }

    with open(os.path.join(out_dir, "feishuLearning.json"), "w", encoding="utf-8") as f:
        json.dump(students, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "feishuMeta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    print(f"\n✅ snapshot/feishuLearning.json（{len(students)} 人）"
          f" + feishuMeta.json（{len(sheet_reports)} sheet）")
    if in_roster:
        print(f"   花名册对齐：{joined}/{len(students)} = "
              f"{meta['rosterJoin']['pct']}%（未命中 {unmatched}）")
    else:
        print("   （未发现 snapshot/roster.json，跳过花名册对齐；先跑完 roster 适配器再重跑本步）")
    if degraded:
        print(f"   ⚠️ 降级声明 {len(degraded)} 项 → snapshot/feishuMeta.json")
        for d in degraded[:6]:
            print(f"      · {d['reason']}")


if __name__ == "__main__":
    main()
