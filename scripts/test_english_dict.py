"""test_english_dict.py — 英语词表 + 分派链路自检（v5.20）

跑法：python3 scripts/test_english_dict.py
全部通过打印 ALL PASS，否则抛 AssertionError（退出码非 0）。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import english_dict as e
import knowledge_dict as kd

_ok = 0


def check(name, cond, detail=""):
    global _ok
    if not cond:
        raise AssertionError(f"FAIL: {name} {detail}")
    _ok += 1
    print(f"  ✓ {name}")


# ── ① 结构完整性 ─────────────────────────────────────────────────────
check("TOPIC_UNITS 非空", len(e.TOPIC_UNITS) >= 10, f"实际 {len(e.TOPIC_UNITS)}")
check("单元显示名唯一", len({u[0] for u in e.TOPIC_UNITS}) == len(e.TOPIC_UNITS))
check("每个单元都有命中词", all(len(u[1]) >= 3 for u in e.TOPIC_UNITS))
check("归属大类 = 显示名", all(u[0] == u[2] for u in e.TOPIC_UNITS))

# ⚠️ 单字命中词是词表头号坑（「读」「背」「听」都会大面积误命中），绝不许出现
for disp, kws, _ in e.TOPIC_UNITS:
    for w in kws:
        check(f"命中词「{w}」不是单字", len(w) >= 2)

# 危险裸词黑名单（坑清单：泛考试词/跨场景词绝不裸收）
# ⚠️「听力」不进黑名单 —— 英语课语境它几乎只指听力题/听说任务（坑清单已论证）
_BANNED = {"阅读", "打卡", "选择题", "大题", "卷子", "考试", "订正", "口语课"}
for disp, kws, _ in e.TOPIC_UNITS:
    for w in kws:
        check(f"「{disp}」不含危险裸词「{w}」", w not in _BANNED)

# UNIT_GRADE 必须覆盖全部单元（口径展示不缺项）
check("UNIT_GRADE 覆盖全部单元",
      set(e.UNIT_GRADE) == {u[0] for u in e.TOPIC_UNITS})

# TEXTBOOK_TREE 完整性：6 个年级，每年级 ≥2 册（高三含复习册共 4 册）
grades = [g["grade"] for g in e.TEXTBOOK_TREE]
check("年级齐全", grades == ["初一", "初二", "初三", "高一", "高二", "高三"])
check("每年级 ≥2 册", all(len(g["books"]) >= 2 for g in e.TEXTBOOK_TREE))
check("每册有单元", all(len(b["units"]) >= 1 for g in e.TEXTBOOK_TREE for b in g["books"]))
check("每单元有条目", all(len(ls) >= 1 for g in e.TEXTBOOK_TREE for b in g["books"]
                    for _n, ls in b["units"]))

# 教材树关键条目抽检（修订版标志性单元名，防抓错版次/凭记忆编造）
_all = json.dumps(e.TEXTBOOK_TREE, ensure_ascii=False)
for key in ("You and Me", "We're Family!", "Happy Birthday!",           # 七上2024
            "Animal Friends", "No Rules, No Order", "Once upon a Time",  # 七下2025
            "Happy Holiday", "Home Sweet Home", "Let's Communicate!",    # 八上2025
            "Time to Relax", "Making a Difference",                      # 八下2026
            "The Changing World", "More than a Game",                    # 九上2026
            "How can we become good learners", "mooncakes",              # 九下旧版
            "Teenage Life", "Natural Disasters", "Cultural Heritage",    # 必修一二
            "Festivals and Celebrations", "Working the Land",            # 必修三/选必一
            "Science and Scientists", "First Aid", "Poems",              # 选必二三四
            "Launching Your Career"):
    check(f"教材树含「{key}」", key in _all)

# tree_stats 与树一致
tu, tl, per = e.tree_stats()
_tu = sum(len(b["units"]) for g in e.TEXTBOOK_TREE for b in g["books"])
_tl = sum(len(ls) for g in e.TEXTBOOK_TREE for b in g["books"] for _n, ls in b["units"])
check("tree_stats 单元数一致", tu == _tu, f"{tu} vs {_tu}")
check("tree_stats 条目数一致", tl == _tl, f"{tl} vs {_tl}")
check("per_grade 六键", set(per) == set(grades))

# SOURCE_NOTE 动态引用 stats（树改了文案自动跟）
check("SOURCE_NOTE 含数字", str(tu) in e.SOURCE_NOTE and str(tl) in e.SOURCE_NOTE)


# ── ② 分派链路：英语课程 → english_dict；数学/语文课不受影响 ──────────
tmp = tempfile.mkdtemp(prefix="ctr_english_")
try:
    def roster(products):
        return [{"stuName": "某生", "productName": pn} for pn in products]

    # 英语课（高中）→ builtin_english
    json.dump(roster(["2026Q-YY-高一英语145层日A赵"]), open(os.path.join(tmp, "students.json"), "w"), ensure_ascii=False)
    units, info = kd.augment([], tmp)
    check("英语课分派到 english_dict", info.get("mode") == "builtin_english", info.get("mode"))
    check("英语课 vocab_subject=英语", info.get("vocab_subject") == "英语")
    check("英语课 units = english TOPIC_UNITS",
          [u[0] for u in units] == [u[0] for u in e.TOPIC_UNITS])
    check("英语课 source_note 带教材口径", "教材目录" in (info.get("source_note") or ""))
    check("英语课 unit_excl 通道存在", "unit_excl" in info)
    check("英语课 backend_nodes=0（后台英语树为空，防冒充）", info.get("backend_nodes") == 0)

    # 英语初中课：英语不分流学段（能力模块跨学段），仍走同一词表
    json.dump(roster(["2026Q-YY-初三英语听口专项班赵"]), open(os.path.join(tmp, "students.json"), "w"), ensure_ascii=False)
    units2, info2 = kd.augment([], tmp)
    check("英语初中课仍走 english_dict", info2.get("mode") == "builtin_english")
    check("英语初中课 course_stage 识别为初中", kd._course_stage(tmp) == "初中")

    # 数学课不受影响（回归防「把 VOCAB_SUBJECT 改成英语」式事故）
    json.dump(roster(["2026Q-SX-高一数学985层六C谢胡"]), open(os.path.join(tmp, "students.json"), "w"), ensure_ascii=False)
    units3, info3 = kd.augment([], tmp)
    check("数学课不走 english", info3.get("vocab_subject") in ("数学", None),
          str(info3.get("vocab_subject")))
    # 语文课不受影响
    json.dump(roster(["2026Q-YW-初三语文顶尖班某"]), open(os.path.join(tmp, "students.json"), "w"), ensure_ascii=False)
    units4, info4 = kd.augment([], tmp)
    check("语文课仍走 chinese_dict", info4.get("mode") == "builtin_chinese", info4.get("mode"))
    check("BUILTIN_SUBJECTS 含英语", "英语" in kd.BUILTIN_SUBJECTS)
    check("BUILTIN_SUBJECTS 仍含数学/物理/化学/语文",
          all(s in kd.BUILTIN_SUBJECTS for s in ("数学", "物理", "化学", "语文")))

    # 关键词真样本：英语课群聊常见说法应命中、且不串单元
    text = "今天的单词默写错得多，时态又混了，完形填空全对，作文回去重写，明天听写 Unit 3 的词组"
    hits = {}
    for disp, kws, _ in units:
        for w in kws:
            if w in text:
                hits.setdefault(disp, []).append(w)
    check("「单词/默写/听写」命中单词与词汇", "单词与词汇" in hits, str(hits))
    check("「时态」命中时态与语态", "语法-时态与语态" in hits, str(hits))
    check("「完形填空」命中完形与语法填空", "完形与语法填空" in hits, str(hits))
    check("「作文」命中书面表达", "书面表达与句型练习" in hits, str(hits))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"ALL PASS（{_ok} 项）")
