"""v4.97 化学词表 & 分派回归测试（与 test_physics_dict 同风格，直接跑）。

用例来源：v4.97 建化学词表时的边界 ——
  ① 化学关键词是**教材直采**（本机无化学课语料），必须锁住「未验证」状态：
     knowledge_used 的 source_note 必须带「尚未经真实语料检验」，不能假装验证过；
  ② 高危裸词不得进词表（文件头「误伤源排查」的清单）；
  ③ 分派六路径：化学/物理 × 有树无树、数学回归、未知科目停用。
跑法：`python3 test_chemistry_dict.py`，全过打印 ✅。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import knowledge_dict                                   # noqa: E402
from extract import TOPIC_UNITS as MATH_UNITS           # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("✅" if cond else "❌"), name, detail)
    if not cond:
        fails.append(name)


# ── 词表本体 ────────────────────────────────────────────────────────────
import chemistry_dict                                   # noqa: E402

check("化学词表单元数 ≥ 24", len(chemistry_dict.TOPIC_UNITS) >= 24,
      f"实际 {len(chemistry_dict.TOPIC_UNITS)}")
_mathsub = {"函数", "圆锥曲线", "立体几何", "三角函数", "数列", "不等式",
            "集合", "静电场", "机械能", "动量"}
check("化学词表无数学/物理单元泄漏",
      not any(u[0] in _mathsub for u in chemistry_dict.TOPIC_UNITS))
_kws = [w for u in chemistry_dict.TOPIC_UNITS for w in u[1]]
# 文件头「误伤源排查」的清单：这些裸词一旦进词表就是回归
check("无高危裸词（转化/充电/酚/NA/mol/周期/图像/实验）",
      not ({"转化", "充电", "酚", "NA", "mol", "周期", "图像", "实验"} & set(_kws)),
      str({"转化", "充电", "酚", "NA", "mol", "周期", "图像", "实验"} & set(_kws)))
check("每单元归属大类非空", all(u[0] and u[1] and u[2] for u in chemistry_dict.TOPIC_UNITS))

# v4.97：全学段知识树（教材口径 5 册 20 章 60 节）
_ch, _sec, _per = chemistry_dict.tree_stats()
check("知识树 = 人教版化学 20 章 60 节", (_ch, _sec) == (20, 60), f"实际 {_ch} 章 {_sec} 节")
check("年级分布 = 高一8章23节/高二7章22节/高三5章15节",
      _per == {"高一": (8, 23), "高二": (7, 22), "高三": (5, 15)}, str(_per))
_no_grade = [u[0] for u in chemistry_dict.TOPIC_UNITS
             if u[0] not in ("计算/粗心", "化学实验") and u[0] not in chemistry_dict.UNIT_GRADE]
check("每个知识点单元都有年级归属（跨年级/错误类型除外）", not _no_grade, str(_no_grade))

# ── 分派：化学课（临时目录 × 有树/无树） ────────────────────────────────
tmp = tempfile.mkdtemp()
try:
    json.dump([{"productName": "2026Q-HX-高二化学985层X"}],
              open(os.path.join(tmp, "students.json"), "w"))
    # 无树 → 化学词表照常（教材直采，不依赖后台树）
    u1, i1 = knowledge_dict.augment(MATH_UNITS, tmp)
    check("化学课（无树）→ builtin_chemistry",
          i1.get("mode") == "builtin_chemistry" and len(u1) == 24,
          f"{i1.get('mode')} units={len(u1)}")
    check("化学 info 诚实标注验证状态（部分章节仍教材直采待验证）",
          "教材直采待验证" in (i1.get("source_note") or ""))
    check("化学 backend_nodes 不冒充（无化学后台节点）",
          i1.get("backend_nodes") == 0 and not i1.get("backend_backed_units"))

    # 有树（树里是数学节点）→ 仍走化学词表，且 backend_nodes 只数化学节点
    os.makedirs(os.path.join(tmp, "snapshot"))
    json.dump({"points": [
        {"name": "集合", "level": 2, "category": 1, "subjectName": "数学"},
        {"name": "函数", "level": 2, "category": 1, "subjectName": "数学"},
    ]}, open(os.path.join(tmp, "snapshot", "knowledge_points.json"), "w"))
    u2, i2 = knowledge_dict.augment(MATH_UNITS, tmp)
    check("化学课（有树但树是数学）→ 仍化学词表",
          i2.get("mode") == "builtin_chemistry" and len(u2) == 24)
    check("backend_nodes 只数化学节点（不拿数学树冒充）",
          i2.get("backend_nodes") == 0, f"实际 {i2.get('backend_nodes')}")

    # 未知科目 → 停用，且空态文案列出全部已建科目
    json.dump([{"productName": "2026Q-SW-高二生物985层X"}],
              open(os.path.join(tmp, "students.json"), "w"))
    u3, i3 = knowledge_dict.augment(MATH_UNITS, tmp)
    check("生物课 → 停用", i3.get("mode") == "subject_mismatch" and u3 == [])
    check("停用文案列出已建科目（数学、物理、化学、语文）",
          i3.get("vocab_subjects") == ["数学", "物理", "化学", "语文"])
finally:
    shutil.rmtree(tmp)

print()
if fails:
    print(f"结果：{len(fails)} 条失败 → {fails}")
    sys.exit(1)
print("结果：全部通过 ✅")
