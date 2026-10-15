"""小黑板读图缓存（增量读图）：**同一张图只读一次**，跨课程 / 跨月份 / 跨会话复用。

为什么需要：逐张读图是整个流程里最贵的一步。一个课程一学期会产生上千张图，
每次重跑报告都重读一遍是纯浪费 —— 新的一轮课通常只多出几十张。

缓存位置：<技能目录>/.cache/board_reads.json
  - 键 = 图片**绝对路径**
  - 校验位 = 文件大小 + mtime（图片被替换/重新导出会自动失效并重读）
  - 只增不改：merge 只写入，不会删除历史结论

三个子命令：
  1) sync  扫图 → 已缓存的直接复用，未读的进待读清单（可选自动分批）
       python board_cache.py sync --boards "桌面小黑板目录" --workdir <工作区> [--batch-size 28]
     产出：
       <工作区>/board_analysis.json   已复用（缓存命中）的读图结论，可直接给 read_boards.py
       <工作区>/board_todo.json       待读图片清单（student/round/lesson/path）
       <工作区>/board_batches/batch_N.json   待读清单分批（配 --batch-size，供读图子代理）
  2) merge  读完把结果并入缓存（可传多个文件 / 通配）
       python board_cache.py merge <工作区>/board_batches/result_*.json
  3) stats  查看缓存规模与更新时间
       python board_cache.py stats

典型用法（第二轮课只读新增图）：
  python board_cache.py sync --boards "$HOME/Desktop/邵清北课上小黑板" --workdir ./wd --batch-size 28
  # → 打印「命中 658 张（复用） / 待读 57 张 → 2 批」
  # 只把 batch_*.json 交给读图，读完后：
  python board_cache.py merge ./wd/board_batches/result_*.json
"""
import argparse
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pull_boards

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "board_reads.json")

FIELDS = ["problem", "topic", "topic_knowledge", "status", "correct", "errors", "stuck_at", "note"]


def _stat(path):
    try:
        st = os.stat(path)
        return st.st_size, round(st.st_mtime, 3)
    except OSError:
        return 0, 0


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            return json.load(open(CACHE_FILE, encoding="utf-8"))
        except Exception:
            print("⚠️ 缓存文件损坏，已重建（旧缓存备份为 board_reads.json.bak）")
            os.replace(CACHE_FILE, CACHE_FILE + ".bak")
    return {"_meta": {"version": 1, "count": 0}, "reads": {}}


def save_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache["_meta"]["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache["_meta"]["count"] = len(cache["reads"])
    tmp = CACHE_FILE + ".tmp"
    json.dump(cache, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE_FILE)


def _roster_names(path):
    """从 students.json 读在册姓名（可选过滤：只处理在册学生，跳过老师名/别班噪音）。

    与 board 扫描结果同样做 norm_name 归一化（花名册常带流水尾号，如 吴蔚9822 → 吴蔚），
    否则在册学生会被误判为「不在名单内」而跳过。
    """
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    names = set()
    if isinstance(data, list):
        for x in data:
            if isinstance(x, dict):
                n = x.get("stuName") or x.get("name")
            else:
                n = x
            if n:
                names.add(pull_boards.common.norm_name(str(n).strip()))
    return names


def cmd_sync(args):
    root = os.path.expanduser(args.boards)
    if not os.path.isdir(root):
        sys.exit(f"未找到小黑板目录：{root}")

    cache = load_cache()
    reads = cache.get("reads", {})
    boards, n_img = pull_boards.scan(root)

    roster = _roster_names(args.roster) if args.roster else None
    keep = None
    if args.students:
        keep = {s.strip() for s in args.students.split(",") if s.strip()}

    hits, todo, skipped = [], [], []
    for name, recs in boards.items():
        for r in recs:
            p = r["path"]
            if keep is not None and name not in keep:
                skipped.append(p)
                continue
            if roster is not None and name not in roster:
                skipped.append(p)
                continue
            size, mtime = _stat(p)
            c = reads.get(p)
            if c and c.get("size") == size and c.get("mtime") == mtime and c.get("status"):
                hits.append({
                    "student": name, "round": r["round"], "lesson": r["lesson"],
                    "lesson_date": r["lesson_date"], "path": p,
                    **{k: c.get(k) for k in FIELDS},
                })
            else:
                todo.append({
                    "student": name, "round": r["round"], "lesson": r["lesson"],
                    "lesson_date": r["lesson_date"], "path": p,
                })

    hits.sort(key=lambda x: (x["student"], x["round"]))
    todo.sort(key=lambda x: (x["student"], x["round"]))

    wd = args.workdir
    os.makedirs(wd, exist_ok=True)
    json.dump(hits, open(os.path.join(wd, "board_analysis.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(todo, open(os.path.join(wd, "board_todo.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    n_batch = 0
    if args.batch_size and todo:
        bdir = os.path.join(wd, "board_batches")
        os.makedirs(bdir, exist_ok=True)
        for i in range(0, len(todo), args.batch_size):
            n_batch += 1
            json.dump(todo[i:i + args.batch_size],
                      open(os.path.join(bdir, f"batch_{n_batch}.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)

    print(f"📚 扫图：{n_img} 张 / {len(boards)} 名学生")
    print(f"✅ 缓存命中（复用，不重读）：{len(hits)} 张 / {len({h['student'] for h in hits})} 人")
    print(f"🆕 待读（未命中缓存）：{len(todo)} 张 / {len({t['student'] for t in todo})} 人")
    if skipped:
        print(f"⏭  跳过（不在指定名单内）：{len(skipped)} 张")
    if n_batch:
        print(f"   已分 {n_batch} 批 → {os.path.join(wd, 'board_batches')}/batch_*.json（每批约 {args.batch_size} 张）")
    print(f"   已写 {os.path.join(wd, 'board_analysis.json')}（可直接给 read_boards.py 聚合）")
    if todo:
        print("   ⚠️ 待读清单非空：读完后务必执行  python board_cache.py merge <result_*.json>  写入缓存")


def cmd_merge(args):
    cache = load_cache()
    reads = cache.get("reads", {})
    files = []
    for pat in args.files:
        files.extend(sorted(glob.glob(pat)) or [pat])
    added = updated = 0
    for fp in files:
        if not os.path.exists(fp):
            print(f"  ⚠️ 跳过不存在的文件：{fp}")
            continue
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠️ 解析失败 {fp}: {e}")
            continue
        if not isinstance(data, list):
            print(f"  ⚠️ 非数组，跳过：{fp}")
            continue
        for r in data:
            p = r.get("path")
            if not p:
                continue
            size, mtime = _stat(p)
            rec = {
                "size": size, "mtime": mtime,
                "student": r.get("student"), "round": r.get("round"),
                "lesson": r.get("lesson"), "lesson_date": r.get("lesson_date"),
                **{k: r.get(k) for k in FIELDS},
                "read_at": datetime.date.today().isoformat(),
            }
            if p in reads:
                updated += 1
            else:
                added += 1
            reads[p] = rec
    cache["reads"] = reads
    save_cache(cache)
    print(f"✅ 缓存已更新：新增 {added} 张 / 覆盖 {updated} 张 ｜ 缓存合计 {len(reads)} 张")
    print(f"   {CACHE_FILE}")


def cmd_stats(args):
    cache = load_cache()
    reads = cache.get("reads", {})
    stus = {v.get("student") for v in reads.values() if v.get("student")}
    print(f"📦 读图缓存：{len(reads)} 张 / {len(stus)} 名学生")
    print(f"   更新时间：{cache.get('_meta', {}).get('updated', '—')}")
    print(f"   位置：{CACHE_FILE}")


def main():
    ap = argparse.ArgumentParser(description="小黑板读图缓存（增量读图）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="扫图并按缓存复用，输出已复用结论 + 待读清单")
    s.add_argument("--boards", required=True, help="小黑板图片根目录")
    s.add_argument("--workdir", default=".")
    s.add_argument("--batch-size", type=int, default=0, help="待读清单分批大小（0=不分批）")
    s.add_argument("--roster", default=None, help="students.json 路径（只留在册学生，可选）")
    s.add_argument("--students", default=None, help="逗号分隔的姓名白名单（可选）")
    s.set_defaults(func=cmd_sync)

    m = sub.add_parser("merge", help="把读图结果并入缓存")
    m.add_argument("files", nargs="+", help="结果 JSON（可通配，如 result_*.json）")
    m.set_defaults(func=cmd_merge)

    t = sub.add_parser("stats", help="查看缓存规模")
    t.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
