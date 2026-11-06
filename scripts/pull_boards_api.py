"""pull_boards_api.py — 小黑板取数（v5.03）：从后台接口取课节分析结果。

背景（2026-11-06 用户拍板）：
    旧链路 = pull_boards.py 扫本地图片目录 + read_boards.py 逐张调视觉模型读图。
    现在后台直接提供逐课节的分析结果与图片 URL（GET /ai/getClassBlackboardAnalysis），
    本地扫目录 + 读图整段废弃，改为「拉接口 → 按学生聚合」。

输入：snapshot/attendance.json 的 classes[].classId（考勤落盘的课节 ID，
      与后台分析表 class_id 精确对齐——v4.99 已验证谢胡课 18 个 classId 全部命中）。
输出：
  snapshot/boards_api.json   原始返回（每课节原样 list，反编造留证：报告里的每条
                             结论都能回溯到接口原话）
  boards_summary.json        学生聚合，与 read_boards.py **同一 schema**
                             （rounds/status_counts/error_types/kb_topics/error_topics/
                              trend/details/lessons/lesson_count），report.py 无感消费。
                             另外每条 detail 多带 image（图片 URL）与 board_matches。

⚠️ 接口实测要点（2026-11-06）：
  · 单课节返回可能很大（谢胡 -5 节实测 833 条 / 548KB，99 人 × 9 轮）；
  · 一张图片的 analysisResult 是**逐题数组**（约 22% 图片多题）→ 一个 detail = 一道题，
    同图的多题行共用同一个 image URL；
  · status 取值：完整作答 / 部分作答 / 空白未提交（空白图也返回一条占位分析）；
    correct 取值：正确 / 基本正确 / 有错 / 待判（空白图的 correct 恒为「待判」，
    读聚合时**不计入正确率分母**——与 read_boards「正确/有错才算」的口径天然一致）；
  · analysisResult 为空数组（实测 5/833）= 后台分析缺失/失败 → 记 failed=True，
    **绝不写成「空白未提交」**（v4.66 铁律：技术失败不能当学生没交）；
  · studentName 为空 = 未关联到学生 → 进 "_未关联" 桶只计数，不进报告；
  · 接口「不归并父子课节」：考勤里的 classId 就是分析所属课节 ID（同一来源），
    但个别课节可能无分析数据（返回空 list）→ 只计数、不算失败；
  · 全部课节请求失败 → 不写盘（旧 boards_summary.json 保留），退出码 2。

用法：
    python3 scripts/pull_boards_api.py --workdir <wd>
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

BASE = "https://stadminv2.xqmsx.cn/api"
GAP = 0.25            # 课节间隔秒数（礼貌限速）
RETRY = 2             # 单课节失败重试次数


def _get(path_qs, token, timeout=60):
    req = urllib.request.Request(BASE + path_qs, headers={"x-token": token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def write_json_atomic(path, obj):
    """临时文件 + os.replace 原子替换（中途失败不产生半截 JSON）。"""
    import tempfile
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def load_class_map(wd):
    """attendance.json → {classId: {"date": timeStart, "prod": productName}}。"""
    p = os.path.join(wd, "snapshot", "attendance.json")
    if not os.path.exists(p):
        raise SystemExit(f"❌ 缺少 {p}——小黑板接口按课节 ID 拉，必须先有考勤快照。")
    att = json.load(open(p, encoding="utf-8"))
    if isinstance(att, dict):
        att = att.get("students") or list(att.values())
    ids = {}
    for rec in att or []:
        if not isinstance(rec, dict):
            continue
        for c in (rec.get("classes") or []):
            cid = c.get("classId")
            if cid and int(cid) > 0:
                ids[int(cid)] = {"date": (c.get("timeStart") or "").strip(),
                                 "prod": (rec.get("productName") or "").strip()}
    return ids


def split_topics(s):
    """「A、B、C」→ [A, B, C]（与 read_boards.split_topics 同口径）。"""
    import re
    if not s:
        return []
    return [x.strip() for x in re.split(r"[、,，;；/]", str(s)) if x.strip()]


def fetch_all(wd, token, class_map):
    """逐课节拉取。返回 (records, ok_ids, empty_ids, err_ids)。"""
    records, ok_ids, empty_ids, err_ids = [], [], [], []
    n = len(class_map)
    for i, cid in enumerate(sorted(class_map), 1):
        rows = None
        last_err = ""
        for attempt in range(1, RETRY + 2):
            try:
                d = _get(f"/ai/getClassBlackboardAnalysis?classId={cid}", token)
                if d.get("code") == 0:
                    rows = (d.get("data") or {}).get("list") or []
                    break
                last_err = f"code={d.get('code')} {d.get('msg') or ''}"
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
            time.sleep(1.0)
        if rows is None:
            err_ids.append((cid, last_err))
            print(f"   ⚠️ [{i}/{n}] classId={cid} 拉取失败（{last_err}）")
        elif not rows:
            empty_ids.append(cid)          # 无分析数据 ≠ 失败（该课本来就没拍板书）
        else:
            ok_ids.append(cid)
            cm = class_map[cid]
            for x in rows:
                x["_lesson_date"] = cm["date"]
                x["_prod"] = cm["prod"]
            records.extend(rows)
        if i % 10 == 0:
            print(f"   …课节 {i}/{n}")
        time.sleep(GAP)
    print(f"   课节 {n} 个：有数据 {len(ok_ids)} ｜ 无分析 {len(empty_ids)} ｜ 请求失败 {len(err_ids)}")
    return records, ok_ids, empty_ids, err_ids


def build_summary(records):
    """接口记录 → 与 read_boards.py 同 schema 的学生聚合。一个 detail = 一道题。"""
    from collections import Counter
    summary, unattributed = {}, 0
    for x in records:
        name = (x.get("studentName") or "").strip()
        if not name:
            unattributed += len(x.get("analysisResult") or []) or 1
            continue
        stu = common.norm_name(name)
        cid = x.get("classId")
        rnd = int(x.get("roundNo") or 0)
        round_key = f"{cid}#{rnd}"
        items = x.get("analysisResult") or []
        sm = summary.setdefault(stu, {"rounds": 0, "analyzed": 0, "details": [],
                                      "source": "api"})
        if not sm.get("class_names"):
            sm["class_names"] = set()
        sm["class_names"].add(x.get("className") or "")
        if not items:
            # 后台分析缺失/失败：记一条 failed，绝不写成「空白未提交」（v4.66 铁律）
            sm["analyzed"] += 1
            sm["details"].append({
                "round": round_key, "lesson": x.get("className") or str(cid),
                "lesson_date": x.get("_lesson_date") or "",
                "failed": True,
                "fail_reason": "后台未返回逐题分析（分析缺失或失败，不计入判定）",
                "errors": [], "status": "", "correct": "", "image": x.get("imageUrl") or "",
            })
            continue
        for it in items:
            it = it or {}
            sm["analyzed"] += 1
            sm["details"].append({
                "round": round_key,
                "lesson": x.get("className") or str(cid),
                "lesson_date": x.get("_lesson_date") or "",
                "problem": (it.get("problem") or "").strip(),
                "topic": (it.get("topic") or "").strip(),
                "topic_knowledge": (it.get("topic_knowledge") or "").strip(),
                "status": (it.get("status") or "").strip(),
                "correct": (it.get("correct") or "").strip(),
                "failed": False, "fail_reason": "",
                "errors": it.get("errors") or [],
                "stuck_at": (it.get("stuck_at") or "").strip(),
                "note": (it.get("note") or "").strip(),
                "board_matches": (it.get("board_matches") or "").strip(),
                "image": x.get("imageUrl") or "",
            })
    # ---- 聚合（口径与 read_boards.summarize 一致）----
    for stu, sm in summary.items():
        ds = sm["details"]
        sm["rounds"] = len({d.get("round") for d in ds if d.get("round")}) or sm["analyzed"]
        ok_ds = [d for d in ds if not d.get("failed")]
        bad_ds = [d for d in ds if d.get("failed")]
        sm["failed_rounds"] = len({d.get("round") for d in bad_ds if d.get("round")}) or len(bad_ds)
        sm["analyzed_ok"] = len(ok_ds)
        sm["status_counts"] = dict(Counter(d["status"] for d in ok_ds if d["status"]))
        ec = Counter(e for d in ds for e in (d.get("errors") or []))
        sm["error_types"] = [k for k, _ in ec.most_common(5)]
        kb, err_kb = [], []
        for d in ds:
            _ts = split_topics(d.get("topic_knowledge")) or split_topics(d.get("topic"))
            for t in _ts:
                if t not in kb:
                    kb.append(t)
                if d.get("errors") and t not in err_kb:
                    err_kb.append(t)
        sm["kb_topics"] = kb
        sm["error_topics"] = err_kb
        # 趋势：前后半段正确率（只算「正确/有错」的题，待判不算——与 read_boards 同口径）
        judge = [d for d in ok_ds if d.get("correct") in ("正确", "有错")]
        if len(judge) >= 2:
            half = len(judge) // 2
            early, late = judge[:half], judge[half:]
            er = sum(1 for d in early if d["correct"] == "正确") / max(1, len(early))
            lr = sum(1 for d in late if d["correct"] == "正确") / max(1, len(late))
            sm["trend"] = "逐步改善" if lr > er else ("下滑" if lr < er else "基本持平")
        else:
            sm["trend"] = "样本不足"
        # 按课次分组
        ls_order, ls_map = [], {}
        for d in ds:
            k = (d.get("lesson") or "其他", d.get("lesson_date") or "")
            if k not in ls_map:
                ls_map[k] = {"lesson": k[0], "lesson_date": k[1], "rounds": 0,
                             "status_counts": {}, "details": []}
                ls_order.append(k)
            ls_map[k]["details"].append(d)
        for k in ls_order:
            g = ls_map[k]
            g["rounds"] = len({d.get("round") for d in g["details"] if d.get("round")})
            g["status_counts"] = dict(Counter(d["status"] for d in g["details"]
                                              if d["status"] and not d.get("failed")))
        sm["lessons"] = [ls_map[k] for k in ls_order]
        sm["lesson_count"] = len(sm["lessons"])
        cn = {c for c in (sm.get("class_names") or set()) if c}
        sm["class_names"] = sorted(cn)
        sm["source"] = "后台接口 getClassBlackboardAnalysis"
    return summary, unattributed


def main():
    ap = argparse.ArgumentParser(description="小黑板取数（后台接口版）：课节分析 → 学生聚合")
    ap.add_argument("--workdir", default=".")
    args = ap.parse_args()
    wd = common.workdir(args)

    token = common.load_token(wd)
    if not token:
        raise SystemExit(f"❌ {workdir_desc(wd)} token.txt 无有效 token，先登录后台。")

    class_map = load_class_map(wd)
    if not class_map:
        raise SystemExit("❌ attendance.json 里没有任何课节 ID（classId），无法拉小黑板。")
    print(f"🔁 小黑板接口取数 · {len(class_map)} 个课节")

    records, ok_ids, empty_ids, err_ids = fetch_all(wd, token, class_map)
    if not ok_ids:
        print("❌ 所有课节都拉取失败/无数据 → 不写盘，旧 boards_summary.json 原样保留。")
        return 2

    # 原始留证
    snap_dir = os.path.join(wd, "snapshot")
    os.makedirs(snap_dir, exist_ok=True)
    raw = {
        "pulledAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "api": "GET /ai/getClassBlackboardAnalysis",
        "classIds_ok": ok_ids, "classIds_empty": empty_ids,
        "classIds_err": [{"classId": c, "err": e} for c, e in err_ids],
        "records": records,
    }
    write_json_atomic(os.path.join(snap_dir, "boards_api.json"), raw)

    summary, unattr = build_summary(records)
    n_img = len({d.get("image") for sm in summary.values() for d in sm["details"]
                 if d.get("image")})
    # 写 boards_summary.json（先备份旧的本地读图版本）
    dst = os.path.join(wd, "boards_summary.json")
    if os.path.exists(dst):
        bdir = os.path.join(wd, "_not_deliverable")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(dst, os.path.join(bdir, "boards_summary_local_"
                                       + dt.datetime.now().strftime("%Y%m%d_%H%M") + ".json"))
    write_json_atomic(dst, summary)
    print(f"✅ {dst}")
    print(f"   学生 {len(summary)} 人 ｜ 分析图片 {n_img} 张 ｜ 逐题明细 "
          f"{sum(len(sm['details']) for sm in summary.values())} 条 ｜ 未关联学生 {unattr} 条")
    return 0


def workdir_desc(wd):
    return os.path.basename(wd.rstrip("/")) or wd


if __name__ == "__main__":
    sys.exit(main())
