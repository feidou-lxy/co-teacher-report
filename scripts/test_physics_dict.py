"""v4.95 物理词表 & 科目分派回归测试（无 pytest 依赖，与 test_exam 同风格直接跑）。

用例来源：2026-11-03 建物理词表时的真实事故与边界 ——
  ① 分派必须在 load() 之前（旧版化学课没拉树会拿数学词表照跑）；
  ② 物理词表不得泄漏数学单元（v4.94 的原始事故）；
  ③ 数学路径行为不得被物理分支改变（回归保护）。
跑法：`python3 test_physics_dict.py`，全过打印 ✅。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import knowledge_dict                                   # noqa: E402
from extract import TOPIC_UNITS as MATH_UNITS           # noqa: E402

PHYS_WD = os.path.expanduser("~/.workbuddy/reports/course-高二物理985层六B孙")
MATH_WD = os.path.expanduser("~/.workbuddy/reports/course-高一数学B版985层进化班六C谢胡")

fails = []


def check(name, cond, detail=""):
    print(("✅" if cond else "❌"), name, detail)
    if not cond:
        fails.append(name)


# ── 词表本体 ────────────────────────────────────────────────────────────
import physics_dict                                     # noqa: E402

check("物理词表单元数 ≥ 22（v4.96 全学段）", len(physics_dict.TOPIC_UNITS) >= 22,
      f"实际 {len(physics_dict.TOPIC_UNITS)}")
_maths = {"函数", "圆锥曲线", "立体几何", "三角函数", "数列", "不等式", "集合"}
check("物理词表无数学单元泄漏",
      not any(u[0] in _maths for u in physics_dict.TOPIC_UNITS))
_kws = [w for u in physics_dict.TOPIC_UNITS for w in u[1]]
check("无高危裸词（功/势能/速度/周期/图像/实验/重力/介质/电视）",
      not ({"功", "势能", "速度", "周期", "图像", "实验", "重力", "介质", "电视"} & set(_kws)))
check("每单元归属大类非空", all(u[0] and u[1] and u[2] for u in physics_dict.TOPIC_UNITS))

# v4.96：全学段知识树
_ch, _sec, _per = physics_dict.tree_stats()
check("知识树 = 人教版 27 章 125 节", (_ch, _sec) == (27, 125), f"实际 {_ch} 章 {_sec} 节")
check("年级分布 = 高一8章37节/高二14章65节/高三5章23节",
      _per == {"高一": (8, 37), "高二": (14, 65), "高三": (5, 23)}, str(_per))
_units_no_grade = [u[0] for u in physics_dict.TOPIC_UNITS
                   if u[0] != "计算/粗心" and u[0] not in physics_dict.UNIT_GRADE]
check("每个知识点单元都有年级归属", not _units_no_grade, str(_units_no_grade))
_grade3 = {"高一", "高二", "高三"}
check("三个年级都有单元覆盖",
      {v for v in physics_dict.UNIT_GRADE.values() if v} == _grade3)

# ── 分派：物理课 ────────────────────────────────────────────────────────
if os.path.exists(os.path.join(PHYS_WD, "students.json")):
    u1, i1 = knowledge_dict.augment(MATH_UNITS, PHYS_WD)
    check("物理课 → builtin_physics", i1.get("mode") == "builtin_physics", i1.get("mode"))
    check("物理课 22 单元", len(u1) == 22, f"实际 {len(u1)}")
    check("物理 info 写明来源",
          "教材" in (i1.get("source") or "") and "人教版" in (i1.get("source") or ""))
    check("knowledge_used 带知识树统计",
          (i1.get("tree") or {}).get("sections") == 125)
else:
    print("⏭ 物理 workdir 不存在，跳过物理课分派用例（本机才有 ~/.workbuddy/reports）")

# ── 分派：数学课（回归） ────────────────────────────────────────────────
if os.path.exists(os.path.join(MATH_WD, "students.json")):
    u2, i2 = knowledge_dict.augment(MATH_UNITS, MATH_WD)
    check("数学课 → backend 模式不变", i2.get("mode") == "backend", i2.get("mode"))
    check("数学课仍剔除后台未建（立体几何等）",
          "立体几何" in (i2.get("dropped") or []))
    check("数学课保留导数 pending", "导数" in (i2.get("pending") or []))
else:
    print("⏭ 数学 workdir 不存在，跳过数学回归用例")

# ── 分派：临时目录边界（化学/物理 × 有树/无树） ─────────────────────────
tmp = tempfile.mkdtemp()
try:
    # 生物无树 → 必须停用（v4.94 之前会拿数学词表照跑；化学 v4.97 起已建词表，
    #   未知科目探针改用生物）
    json.dump([{"productName": "2026Q-SW-高二生物985层X"}],
              open(os.path.join(tmp, "students.json"), "w"))
    u3, i3 = knowledge_dict.augment(MATH_UNITS, tmp)
    check("生物（无树）→ 停用",
          i3.get("mode") == "subject_mismatch" and u3 == [], i3.get("mode"))

    # 生物有树 → 停用
    os.makedirs(os.path.join(tmp, "snapshot"))
    json.dump({"points": [{"name": "x", "level": 2, "category": 1, "subjectName": "生物"}]},
              open(os.path.join(tmp, "snapshot", "knowledge_points.json"), "w"))
    u4, i4 = knowledge_dict.augment(MATH_UNITS, tmp)
    check("生物（有树）→ 停用", i4.get("mode") == "subject_mismatch" and u4 == [])

    # 物理无树 → 教材词表照常（物理词表不依赖后台树）
    shutil.rmtree(tmp)
    tmp2 = tempfile.mkdtemp()
    json.dump([{"productName": "2026Q-WL-高二物理985层X"}],
              open(os.path.join(tmp2, "students.json"), "w"))
    u5, i5 = knowledge_dict.augment(MATH_UNITS, tmp2)
    check("物理（无树）→ 教材词表照常",
          i5.get("mode") == "builtin_physics" and len(u5) == 22
          and i5.get("backend_nodes") == 0)
    shutil.rmtree(tmp2)
finally:
    if os.path.exists(tmp):
        shutil.rmtree(tmp)

# ── v4.99：初中物理词表（以后台为准） ────────────────────────────────────
check("初中词表 = 9 单元（7 知识块 + 其他 + 计算粗心）",
      len(physics_dict.JUNIOR_TOPIC_UNITS) == 9, f"实际 {len(physics_dict.JUNIOR_TOPIC_UNITS)}")
_jkws = [w for u in physics_dict.JUNIOR_TOPIC_UNITS for w in u[1]]
check("初中词表无高中专属词泄漏（楞次/洛伦兹/简谐/光电效应/万有引力）",
      not ({"楞次", "洛伦兹", "洛仑兹", "简谐", "光电效应", "万有引力", "动量"} & set(_jkws)))
check("初中词表无高危裸词（运动/速度/压力/扩散/焦点/发散/近视）",
      not ({"运动", "速度", "压力", "扩散", "焦点", "发散", "近视", "乐音"} & set(_jkws)))
check("初中排除词覆盖（脑子短路/条件反射）",
      "脑子短路" in physics_dict.JUNIOR_UNIT_EXCL.get("电学", ())
      and "条件反射" in physics_dict.JUNIOR_UNIT_EXCL.get("光学", ()))
check("初中口径文案模板含占位符",
      "{n_nodes}" in physics_dict.JUNIOR_SOURCE_NOTE and "{pulled_at}" in physics_dict.JUNIOR_SOURCE_NOTE)
_junits = [u[0] for u in physics_dict.JUNIOR_TOPIC_UNITS]
check("初中单元名 = 后台知识类 L1（电学/运动/力学/声学/光学/透镜/热学/其他）",
      set(_junits) == {"电学", "运动", "力学", "声学", "光学", "透镜", "热学", "其他", "计算/粗心"},
      str(_junits))

# ── v4.99：学段分派（初中物理 → 以后台为准；高中物理 → 教材） ────────────
tmp2 = tempfile.mkdtemp()
try:
    # ① 初中物理 + 有初中后台树 → builtin_physics_junior，backend_nodes 只认初中物理节点
    os.makedirs(os.path.join(tmp2, "snapshot"))
    json.dump([{"productName": "2026Q-WL-初二物理尖子班X"}],
              open(os.path.join(tmp2, "students.json"), "w"))
    _jr_pts = [
        {"name": "欧姆定律", "level": 2, "category": 1, "subjectName": "物理", "schoolStages": 1},
        {"name": "参照物", "level": 2, "category": 1, "subjectName": "物理", "schoolStages": 1},
        {"name": "浮力的认识", "level": 2, "category": 1, "subjectName": "物理", "schoolStages": 1},
        {"name": "音调与频率", "level": 2, "category": 1, "subjectName": "物理", "schoolStages": 1},
        {"name": "光的反射定律", "level": 2, "category": 1, "subjectName": "物理", "schoolStages": 1},
        {"name": "凸透镜成像规律", "level": 2, "category": 1, "subjectName": "物理", "schoolStages": 1},
        {"name": "比热容", "level": 2, "category": 1, "subjectName": "物理", "schoolStages": 1},
        {"name": "电磁波", "level": 2, "category": 1, "subjectName": "物理", "schoolStages": 1},
        {"name": "三读", "level": 2, "category": 2, "subjectName": "物理", "schoolStages": 1},
        # 干扰项：高中物理节点、数学节点 —— 都不许冒充初中物理后台节点
        {"name": "楞次定律", "level": 2, "category": 1, "subjectName": "物理", "schoolStages": 2},
        {"name": "函数的概念", "level": 2, "category": 1, "subjectName": "数学", "schoolStages": 2},
    ]
    json.dump({"points": _jr_pts, "pulled_at": "2026-11-04T18:00:00"},
              open(os.path.join(tmp2, "snapshot", "knowledge_points.json"), "w"))
    uj, ij = knowledge_dict.augment(MATH_UNITS, tmp2)
    check("初中物理课 → builtin_physics_junior",
          ij.get("mode") == "builtin_physics_junior", ij.get("mode"))
    check("初中物理 backend_nodes = 9（只认物理+初中学段，不冒充）",
          ij.get("backend_nodes") == 9, f"实际 {ij.get('backend_nodes')}")
    check("初中物理 method_nodes = 1", ij.get("method_nodes") == 1)
    check("初中物理 source_note 已填入节点数",
          "9 个节点" in (ij.get("source_note") or ""), (ij.get("source_note") or "")[:60])
    check("初中物理 info 带排除词（unit_excl 接线）",
          "条件反射" in (ij.get("unit_excl") or {}).get("光学", ()))
    check("初中物理 9 单元全保留（后台全有）", len(uj) == 9, f"实际 {len(uj)}")
    check("初中物理 degraded=False", ij.get("degraded") is False)

    # ② 初中物理 + 无树 → 显式降级（绝不静默拿高中教材词表）
    import shutil as _sh
    _sh.rmtree(os.path.join(tmp2, "snapshot"))
    uj2, ij2 = knowledge_dict.augment(MATH_UNITS, tmp2)
    check("初中物理（无树）→ 显式降级 builtin_physics_junior",
          ij2.get("mode") == "builtin_physics_junior" and ij2.get("degraded") is True,
          f"{ij2.get('mode')} / {ij2.get('degraded')}")
    check("初中物理（无树）词表 9 单元原样保留", len(uj2) == 9)

    # ③ 高中物理课不受影响（回归）
    _sh.rmtree(tmp2)
    tmp3 = tempfile.mkdtemp()
    json.dump([{"productName": "2026Q-WL-高二物理985层X"}],
              open(os.path.join(tmp3, "students.json"), "w"))
    u6, i6 = knowledge_dict.augment(MATH_UNITS, tmp3)
    check("高中物理（无树）仍 → builtin_physics 教材词表",
          i6.get("mode") == "builtin_physics" and len(u6) == 22)
    check("高中物理 info 也带排除词（v4.99 接线修复）",
          "期待与你们相遇" in (i6.get("unit_excl") or {}).get("匀变速直线运动", ()))
    # ④ 学段词缺失 → 按高中处理（显式可查的降级，口径区能看出来）
    _sh.rmtree(tmp3)
    tmp4 = tempfile.mkdtemp()
    json.dump([{"productName": "2026Q-WL-物理刷题班X"}],
              open(os.path.join(tmp4, "students.json"), "w"))
    u7, i7 = knowledge_dict.augment(MATH_UNITS, tmp4)
    check("学段词缺失 → 按高中教材词表（显式可查）",
          i7.get("mode") == "builtin_physics", i7.get("mode"))
    shutil.rmtree(tmp4)
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

print()
if fails:
    print(f"结果：{len(fails)} 条失败 → {fails}")
    sys.exit(1)
print("结果：全部通过 ✅")
