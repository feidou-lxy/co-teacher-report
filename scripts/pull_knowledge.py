"""拉取后台「知识点树」（知识 & 方法），落盘成学情报告可用的知识字典。

接口：GET /knowledge/getKnowledgePointTreeList（不分页，返回全部上架+下架节点）
说明：https://stadminv2.xqmsx.cn/api/knowledge/getKnowledgePointTreeList

为什么单独一步（v4.75）：
  在这之前，「知识点」在报告里是**硬编码的大模块词表**（函数与导数 / 数列 / 圆锥曲线…14 个），
  由 `extract.py:TOPIC_UNITS` 和小黑板读图口径各写一份，两边还容易不一致。
  用户 2026-10-29 明确要求：学情分析里的知识点（知识 & 方法）**一律以后台知识树为准**。
  所以改为：拉树 → 落盘 → 识别 / 展示 / 教材对齐全部读同一份。

产物（<workdir>/snapshot/）：
  knowledge_tree_raw.json   接口原样返回（可回溯）
  knowledge_points.json     扁平化后的知识字典（本条链路真正读的文件）

层级：level 1 根 / 2 二级 / 3 三级；category 1 知识 / 2 方法。
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common   # noqa: E402

PATH = "/knowledge/getKnowledgePointTreeList"
CATEGORY = {1: "知识", 2: "方法"}
STAGE = {1: "初中", 2: "高中"}
SUBJECT = {1: "数学", 2: "物理", 3: "化学", 4: "生物", 5: "语文", 6: "英语", 99: "全部"}
STATUS = {1: "上架", 2: "下架"}


def walk(roots):
    """把树摊平成列表，附带 path（父链名称用 / 连接）与 depth。

    ⚠️ 用**迭代**而不是递归：树深不确定，脏数据里若有环或超深链，递归会 RecursionError
    且报错信息指向不到是哪条数据。迭代 + seen 去重可以同时防环。
    """
    out, seen = [], set()
    stack = [(n, []) for n in reversed(roots or [])]
    while stack:
        node, parents = stack.pop()
        if not isinstance(node, dict):
            continue
        nid = node.get("ID", node.get("id"))
        name = str(node.get("name") or "").strip()
        names = parents + ([name] if name else [])
        if nid is not None and nid in seen:      # 脏数据自引用 / 重复挂载
            continue
        if nid is not None:
            seen.add(nid)
        out.append({
            "id": nid,
            "parentId": node.get("parentId"),
            "name": name,
            "path": "/".join(names),
            "depth": len(names),
            "category": node.get("category"),
            "categoryName": CATEGORY.get(node.get("category"), ""),
            "level": node.get("level"),
            "difficultyLevel": node.get("difficultyLevel"),
            "examFrequency": node.get("examFrequency"),
            "subject": node.get("subject"),
            "subjectName": SUBJECT.get(node.get("subject"), ""),
            "schoolStages": node.get("schoolStages"),
            "stageName": STAGE.get(node.get("schoolStages"), ""),
            "status": node.get("status"),
            "statusName": STATUS.get(node.get("status"), ""),
        })
        ch = node.get("children") or []
        if isinstance(ch, list):
            for c in reversed(ch):
                stack.append((c, names))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".", help="工作目录（默认当前目录）")
    ap.add_argument("--subject", type=int, default=1,
                    help="科目编号过滤（1数学 2物理 … 99全部）；默认 1=数学，传 99 表示不过滤")
    ap.add_argument("--stages", default="",
                    help="学段过滤，如 2=高中、1,2=初高中都要；留空=不过滤")
    ap.add_argument("--only-online", action="store_true",
                    help="只要上架（status=1）的节点；默认保留全部并标注状态")
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不落盘")
    args = ap.parse_args()

    wd = common.workdir(args)
    snap = os.path.join(wd, "snapshot")
    os.makedirs(snap, exist_ok=True)
    token = common.load_token(wd)

    resp = common.get(PATH, token, timeout=90)
    if not isinstance(resp, dict) or resp.get("code") != 0:
        msg = (resp or {}).get("msg") if isinstance(resp, dict) else resp
        raise SystemExit(
            f"拉取知识点树失败：code={(resp or {}).get('code') if isinstance(resp, dict) else '?'} msg={msg}\n"
            "  授权过期就重新取 token（token.txt 覆盖成新的一行），其余情况先看接口是否改了字段。"
        )
    roots = resp.get("data") or []
    if not isinstance(roots, list) or not roots:
        raise SystemExit("接口返回的 data 为空 —— 不写入空字典（否则下游会把「没有知识点」当成正常）。")

    flat = walk(roots)

    # ── 过滤（只影响 knowledge_points.json，raw 保留全量可回溯）─────────────
    stages = [int(x) for x in str(args.stages).replace("，", ",").split(",") if x.strip().isdigit()]
    kept, drop = [], Counter()
    for p in flat:
        if args.subject != 99 and p.get("subject") != args.subject:
            drop["科目不符"] += 1
            continue
        if stages and p.get("schoolStages") not in stages:
            drop["学段不符"] += 1
            continue
        if args.only_online and p.get("status") != 1:
            drop["下架"] += 1
            continue
        kept.append(p)

    raw_p = os.path.join(snap, "knowledge_tree_raw.json")
    out_p = os.path.join(snap, "knowledge_points.json")
    if not args.dry_run:
        json.dump(resp, open(raw_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump({
            "pulled_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "filters": {"subject": args.subject, "stages": stages,
                        "only_online": bool(args.only_online)},
            "total_raw": len(flat), "total": len(kept),
            "points": kept,
        }, open(out_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ── 统计（每次都要打，方便一眼看出「拉到的到底是不是这门课」）──────────
    print(f"接口返回节点 {len(flat)} 个 → 过滤后 {len(kept)} 个"
          + (f"（剔除 {dict(drop)}）" if drop else ""))
    by_cat = Counter(p["categoryName"] or "未知" for p in kept)
    by_lv = Counter(f"L{p['level']}" if p.get("level") else "L?" for p in kept)
    by_stage = Counter(p["stageName"] or "未标" for p in kept)
    print(f"  分类：{dict(by_cat)}")
    print(f"  层级：{dict(by_lv)}")
    print(f"  学段：{dict(by_stage)}")
    off = sum(1 for p in kept if p.get("status") == 2)
    if off:
        print(f"  ⚠️ 其中下架 {off} 个（保留在字典里但标记 status=2，展示时优先上架）")

    # 名称重复：同名不同 id 会让「按名字反查」产生歧义，必须报出来
    dup = {k: v for k, v in Counter(p["name"] for p in kept if p["name"]).items() if v > 1}
    if dup:
        top = sorted(dup.items(), key=lambda x: -x[1])[:8]
        print(f"  ⚠️ 重名节点 {len(dup)} 个（按名字反查取路径最短的上架节点）：{top}")

    leaf = sum(1 for p in kept if p.get("level") == 3)
    print(f"  三级（最细）节点 {leaf} 个；无三级时以二级为最细。")
    if not args.dry_run:
        print(f"已写入 {out_p}")
    else:
        print("（dry-run，未落盘）")


if __name__ == "__main__":
    main()
