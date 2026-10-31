"""诊断：后台「知识点树」 vs 学情报告现有识别词表的覆盖差距。

为什么需要这一步（v4.75）：
  用户 2026-10-29 要求「学情分析里的知识点（方法&知识）一律以后台知识树为准」。
  但后台的高中数学树是**人工录入、持续补充**的，存在两种相反的坑：
    ① 报告有、后台没有（数列 / 导数 / 立体几何 / 概率统计 …）—— 直接改用后台会把它们悄悄丢掉；
    ② 后台有、报告认不出（集合 / 逻辑命题 / 弧度制 / 平面向量运算 …）—— 高一最核心的一块现在识别不到。
  两种坑都不会报错，只会「静默少识别」。所以接入前必须先把差距摊开，而不是直接替换词表。

用法：
  python3 align_knowledge.py --workdir <课程工作目录>
  python3 align_knowledge.py --workdir . --json out.json     # 机器可读
  python3 align_knowledge.py --workdir . --stages 2 --subject 1（仅提示：过滤在 pull_knowledge 阶段完成）

读：<workdir>/snapshot/knowledge_points.json（由 pull_knowledge.py 生成）、extract.TOPIC_UNITS
写：无（纯只读诊断，不改任何判定）
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common      # noqa: E402
import extract     # noqa: E402
import knowledge_dict  # noqa: E402

# 现有词表里「本就不是知识点」的条目：它们是题型 / 错因维度，后台知识树天然不会有。
# 不列出来的话，它们会永远挂在「后台缺录」清单里制造噪声。
NOT_KP = {
    "含参问题": "题型（参数讨论），不是知识点",
    "计算/粗心": "错因维度，不是知识点",
}


def _hit(name, kws):
    """name 与关键词是否相关：双向包含（后台节点名常比词表关键词更长）。"""
    return any(k and (k in name or name in k) for k in kws)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--json", default="", help="把结果另存为 JSON")
    ap.add_argument("--show-all", action="store_true", help="列出全部后台节点名")
    args = ap.parse_args()

    wd = common.workdir(args)
    kp_path = os.path.join(wd, "snapshot", "knowledge_points.json")
    if not os.path.exists(kp_path):
        raise SystemExit(f"没找到 {kp_path}\n  先跑：python3 pull_knowledge.py --workdir {wd} --subject 1 --stages 2")

    kd = json.load(open(kp_path, encoding="utf-8"))
    points = kd.get("points") or []
    if not points:
        # 「空字典」不能当成「后台就是没有知识点」，否则下游会静默少识别
        raise SystemExit(f"{kp_path} 里 points 为空 —— 不把它当作「后台无知识点」，先重拉一次确认。")

    leaf = [p for p in points if p.get("level", 0) >= 2]
    names = [p["name"] for p in leaf if p.get("name")]

    # ⚠️ ① 必须遍历**原始** TOPIC_UNITS，不能用 augment 后的表：
    #   v4.76 起 augment 会把后台未建的 4 个单元直接剔除，若拿 live 来遍历，
    #   这 4 块压根不进入循环 → ③ 报「后台缺录 0 个」，等于自检脚本把事实抹掉。
    #   （v4.75 已踩过一次同类：拿内置词表判覆盖 → 报 17 个假阴性。）
    #   live 只用于取「实际生效的命中词」，不用于决定遍历范围。
    live, kp_info = knowledge_dict.augment(extract.TOPIC_UNITS, wd)
    live_kws = {u[0]: u[1] for u in live}

    print(f"知识字典：{kd.get('pulled_at', '?')} 拉取，共 {len(points)} 个节点"
          f"（L1 模块 {sum(1 for p in points if p.get('level') == 1)}，"
          f"二级及以下 {len(names)}）")
    print(f"  分类：{dict(Counter(p['categoryName'] or '未标' for p in points))}")
    print(f"  学段：{dict(Counter(p['stageName'] or '未标' for p in points))}")
    if kd.get("filters"):
        print(f"  过滤条件：{kd['filters']}")
    if kp_info.get("degraded"):
        print(f"  ⚠️ 词表并集未生效：{kp_info['why']}")
    else:
        print(f"  现行词表（v4.76 以后台为准）：并入命中词 {kp_info['added']} 个"
              + (f"，新增单元 {'/'.join(kp_info['new_units'])}" if kp_info.get("new_units") else "")
              + (f"，剔除后台未建 {'/'.join(kp_info['dropped'])}" if kp_info.get("dropped") else ""))
    print()

    # ── ① 现行识别单元 → 后台落点 ─────────────────────────────────────────
    print(f"=== ① 词表 {len(extract.TOPIC_UNITS)} 个单元，在后台落在哪"
          f"（实际生效 {len(live)} 个）===")
    covered, orphan = [], []
    for disp, kws, _key in extract.TOPIC_UNITS:
        eff = live_kws.get(disp)
        hit = [n for n in names if _hit(n, list(eff or kws) + [disp])]
        if hit:
            covered.append((disp, hit))
            print(f"  ✅ {disp:8s} → {len(hit)} 个节点，如 {hit[:4]}")
        elif eff is None and disp in knowledge_dict.BACKEND_MISSING:
            orphan.append((disp, None))
            print(f"  ❌ {disp:8s} → 后台无 ← v4.76 已**剔除**，本版不识别（后台补录后自动放回）")
        else:
            note = NOT_KP.get(disp)
            orphan.append((disp, note))
            print(f"  {'·' if note else '❌'} {disp:8s} → 后台无"
                  + (f"（{note}，可忽略）" if note else " ← 走内置词表兜底"))
    print()

    # ── ② 并集之后仍认不出的后台节点 ─────────────────────────────────────
    allkw = [k for _d, ks, _k in live for k in ks] + [d for d, _, _ in live]
    miss = [n for n in names if not _hit(n, allkw)]
    print(f"=== ② 后台有、现行词表（含并集）仍识别不到：{len(miss)} 个 ===")
    for n in miss:
        p = next(x for x in leaf if x["name"] == n)
        print(f"  · {n:26s} 频度{p.get('examFrequency')} 难度{p.get('difficultyLevel')}  ← {p.get('path', '')}")
    if not miss:
        print("  （无：后台节点全部能被现行词表命中）")
    else:
        print("  说明：这些节点要么没进 knowledge_dict.KP_EXTRA / KP_NEW（结构改了要去补），")
        print("        要么是与课堂用法冲突、已显式留白的（见该文件里「显式不收录」注释）。")
    print()

    # ── ③ 词表有、后台缺录（真正要去后台补的）─────────────────────────────
    real_orphan = [(d, n) for d, n in orphan if not n]
    print(f"=== ③ 报告要用、但后台缺录：{len(real_orphan)} 个 ===")
    if real_orphan:
        for d, _ in real_orphan:
            print(f"  ❌ {d}")
        print("  说明：v4.76 起这些模块**本版不识别**（报告里不会出现），不是「本课没学」。")
        print("        去后台补录对应节点即可自动放回，无需改 extract.py（见 knowledge_dict.BACKEND_MISSING）。")
    else:
        print("  （无：报告需要的模块后台都建了）")
    print()

    # ── ④ 方法类 ──────────────────────────────────────────────────────────
    methods = [p for p in points if p.get("category") == 2]
    print(f"=== ④ 方法类节点：{len(methods)} 个 ===")
    if methods:
        for p in methods:
            print(f"  · {p['name']}  ← {p.get('path', '')}")
    else:
        print("  本节知识树里一个「方法」节点都没有（category=2 为空）。")
        print("  方法树目前只建在初中数学（新定义 / 二次函数压轴题无敌模式）和物理上。")
        print("  ⚠️ 因此「方法维度」暂时无法以后台为准；按原方案只能继续用课堂话术词表。")

    if args.show_all:
        print("\n=== 后台全部节点 ===")
        for p in points:
            print("  " * (p["depth"] - 1) + p["name"])

    if args.json:
        json.dump({
            "pulled_at": kd.get("pulled_at"),
            "filters": kd.get("filters"),
            "total_nodes": len(points),
            "covered": [{"unit": d, "nodes": h} for d, h in covered],
            "backend_missing": [d for d, _ in real_orphan],
            "not_kp": NOT_KP,
            "unrecognized": miss,
            "methods": [p["name"] for p in methods],
        }, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n已写入 {args.json}")


if __name__ == "__main__":
    main()
