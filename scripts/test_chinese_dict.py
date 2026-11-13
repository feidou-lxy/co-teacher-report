"""test_chinese_dict.py — 语文词表 + 分派链路自检（v5.14）

跑法：python3 scripts/test_chinese_dict.py
全部通过打印 ALL PASS，否则抛 AssertionError（退出码非 0）。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chinese_dict as c
import knowledge_dict as kd

_ok = 0


def check(name, cond, detail=""):
    global _ok
    if not cond:
        raise AssertionError(f"FAIL: {name} {detail}")
    _ok += 1
    print(f"  ✓ {name}")


# ── ① 结构完整性 ─────────────────────────────────────────────────────
check("TOPIC_UNITS 非空", len(c.TOPIC_UNITS) >= 10, f"实际 {len(c.TOPIC_UNITS)}")
check("单元显示名唯一", len({u[0] for u in c.TOPIC_UNITS}) == len(c.TOPIC_UNITS))
check("每个单元都有命中词", all(len(u[1]) >= 3 for u in c.TOPIC_UNITS))
check("归属大类 = 显示名", all(u[0] == u[2] for u in c.TOPIC_UNITS))

# ⚠️ 单字命中词是语文词表的头号坑（之/而/其/曲/阅读 全是灾难），绝不许出现
for disp, kws, _ in c.TOPIC_UNITS:
    for w in kws:
        check(f"命中词「{w}」不是单字", len(w) >= 2)

# UNIT_GRADE 必须覆盖全部单元（口径展示不缺项）
check("UNIT_GRADE 覆盖全部单元",
      set(c.UNIT_GRADE) == {u[0] for u in c.TOPIC_UNITS})

# TEXTBOOK_TREE 完整性：6 个年级 × 各 2 册（高三第二册是复习惯例册）
grades = [g["grade"] for g in c.TEXTBOOK_TREE]
check("年级齐全", grades == ["初一", "初二", "初三", "高一", "高二", "高三"])
check("每年级 2 册", all(len(g["books"]) == 2 for g in c.TEXTBOOK_TREE))
check("每册有单元", all(len(b["units"]) >= 1 for g in c.TEXTBOOK_TREE for b in g["books"]))
check("教材册单元数充足（复习册除外）",
      all(len(b["units"]) >= 5 for g in c.TEXTBOOK_TREE for b in g["books"]
          if "复习" not in b["book"]))
check("每单元有课文", all(len(ls) >= 1 for g in c.TEXTBOOK_TREE for b in g["books"]
                    for _n, ls in b["units"]))

# 教材树关键课文抽检（修订版标志性篇目，防抓错版次）
_all = json.dumps(c.TEXTBOOK_TREE, ensure_ascii=False)
for key in ("往事依依", "我的白鸽", "谁是最可爱的人", "山地回忆", "活板",
            "天上有颗", "月亮是从哪里来的", "敬业与乐业", "中国人首次进入自己的空间站",
            "沁园春·长沙", "红楼梦", "乡土中国", "将进酒", "陈情表"):
    check(f"教材树含「{key}」", key in _all)

# tree_stats 与树一致
tu, tl, per = c.tree_stats()
_tu = sum(len(b["units"]) for g in c.TEXTBOOK_TREE for b in g["books"])
_tl = sum(len(ls) for g in c.TEXTBOOK_TREE for b in g["books"] for _n, ls in b["units"])
check("tree_stats 单元数一致", tu == _tu, f"{tu} vs {_tu}")
check("tree_stats 课文数一致", tl == _tl, f"{tl} vs {_tl}")
check("per_grade 三键", set(per) == set(grades))

# SOURCE_NOTE 动态引用 stats（树改了文案自动跟）
check("SOURCE_NOTE 含数字", str(tu) in c.SOURCE_NOTE and str(tl) in c.SOURCE_NOTE)


# ── ② 分派链路：语文课程 → chinese_dict；数学课程 → 不受影响 ──────────
tmp = tempfile.mkdtemp(prefix="ctr_chinese_")
try:
    snap = os.path.join(tmp, "snapshot")
    os.makedirs(snap, exist_ok=True)

    def roster(products):
        return [{"stuName": "某生", "productName": pn} for pn in products]

    # 语文课 → builtin_chinese
    json.dump(roster(["2026Q-YW-初一语文阅读写作班某"]), open(os.path.join(tmp, "students.json"), "w"), ensure_ascii=False)
    units, info = kd.augment([], tmp)
    check("语文课分派到 chinese_dict", info.get("mode") == "builtin_chinese", info.get("mode"))
    check("语文课 vocab_subject=语文", info.get("vocab_subject") == "语文")
    check("语文课 units = chinese TOPIC_UNITS",
          [u[0] for u in units] == [u[0] for u in c.TOPIC_UNITS])
    check("语文课 source_note 带教材口径", "教材目录" in (info.get("source_note") or ""))
    check("语文课 unit_excl 通道存在", "unit_excl" in info)

    # 语文课 + 初中学段：语文不分流学段（能力模块跨学段），仍走同一词表
    json.dump(roster(["2026Q-YW-初三语文顶尖班某"]), open(os.path.join(tmp, "students.json"), "w"), ensure_ascii=False)
    units2, info2 = kd.augment([], tmp)
    check("语文初中课仍走 chinese_dict", info2.get("mode") == "builtin_chinese")
    check("语文初中课 course_stage 识别为初中", kd._course_stage(tmp) == "初中")

    # 数学课不受影响（回归防「把 VOCAB_SUBJECT 改成语文」式事故）
    json.dump(roster(["2026Q-SX-高一数学985层六C谢胡"]), open(os.path.join(tmp, "students.json"), "w"), ensure_ascii=False)
    units3, info3 = kd.augment([], tmp)
    check("数学课不走 chinese", info3.get("vocab_subject") in ("数学", None),
          str(info3.get("vocab_subject")))
    check("BUILTIN_SUBJECTS 含语文", "语文" in kd.BUILTIN_SUBJECTS)
    check("BUILTIN_SUBJECTS 仍含数学/物理/化学",
          all(s in kd.BUILTIN_SUBJECTS for s in ("数学", "物理", "化学")))

    # 关键词真样本：语文课群聊常见说法应命中、且不串单元
    text = "这篇课文的默写明天检查，文言文的翻译课后自己先过一遍，作文素材本周积累三个"
    hits = {}
    for disp, kws, _ in units:
        for w in kws:
            if w in text:
                hits.setdefault(disp, []).append(w)
    check("「默写」命中基础知识与默写", "基础知识与默写" in hits, str(hits))
    check("「文言文/翻译」命中文言文阅读", "文言文阅读" in hits, str(hits))
    check("「作文素材」命中写作与作文", "写作与作文" in hits, str(hits))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"ALL PASS（{_ok} 项）")
