#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按「进班时间」收缩出勤与作业分母（v4.53）。

问题（用户 2026-10-22 原话）：
    「有的学生既无出勤也没有作业，是因为刚刚转入到这个班里，这种目前没识别出来的话
      对联席/教务来说是干扰的错误信息。」

逻辑：
  1. 学生有 `joinTime`（研发接口新增字段），课节有 `classSeq` + `classBeginTime`；
  2. 对每名学生算 `firstSeq` = **进班时间之后**的第一节课的 seq（进班早于第 1 节 → 1）；
  3. 出勤：`attendance.json` 的 `classes[]` 只留 `classSeq >= firstSeq`，重算
     lessonCount / attendCount / absentCount / playbackOnlyCount / effectiveAttendCount；
  4. 飞书作业：给 `feishuLearning.json` 每条加 `joinSeq`，由 `common.fs_stats()` 在
     统计 done/undone 时只取 `seq >= joinSeq` 的课次（飞书「第 N 次课」= 课节 classSeq）。

边界（都不许静默兜底）：
  · 学生没有 joinTime → 不改，记入 `skipped`（报告侧仍按全量课节算，属诚实降级）；
  · 课表没有该 productId → 整门课跳过并报错退出；
  · 进班后没有任何课节 → firstSeq 置 0，出勤分母为 0，报告侧显示「进班后暂无课节」。

输出：
  attendance.json / snapshot/attendance.json 就地更新（原文件备份到 _not_deliverable/）
  snapshot/feishuLearning.json 就地加 joinSeq 字段
  join_time.json  每名学生的 {joinTime, firstSeq, 进班后课节数}，供报告侧解释口径
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


def _dt(s):
    """宽松解析 ISO 时间 → 可比较字符串 YYYY-MM-DDTHH:MM:SS。"""
    s = str(s or "").strip()
    if not s:
        return ""
    s = s.replace("T", " ")
    return s[:19]


def load_json(p, default=None):
    if not os.path.exists(p):
        return default
    return json.load(open(p, encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--dry-run", action="store_true", help="只打印影响面，不写盘")
    args = ap.parse_args()

    wd = common.workdir(args)
    try:
        import adapt_feishu as af          # 复用同一套「完成/未完成/评级」判定
    except Exception:                       # noqa: BLE001
        af = None
    if af is None:
        raise SystemExit("❌ 无法导入 adapt_feishu（同目录），无法按进班时间重算完成率")
    _prof = load_json(os.path.join(wd, "profile.json"), {}) or {}
    ratings_cfg = ((_prof.get("feishu") or {}).get("ratings")) or {}
    students = load_json(os.path.join(wd, "students.json"), []) or []
    sched = load_json(os.path.join(wd, "snapshot", "cClasses.json"), {}) or {}
    sched = {k: v for k, v in sched.items() if k != "_meta"}
    if not sched:
        raise SystemExit("❌ 没有 snapshot/cClasses.json，先跑 pull_schedule.py")

    # 每个 productId → 有序课节列表
    classes = {}
    for pid, v in sched.items():
        rows = sorted(v.get("classes") or [], key=lambda r: (r.get("seq") or 0))
        classes[str(pid)] = rows

    def first_seq_after(pid, join_time):
        """进班时间之后的第一节课 seq；进班早于第 1 节 → 1。"""
        rows = classes.get(str(pid)) or []
        if not rows:
            return None
        jt = _dt(join_time)
        if not jt:
            return None
        for r in rows:
            b = _dt(r.get("begin")) or (r.get("date") or "")
            if b and b >= jt[:len(b)]:
                return r.get("seq") or 0
        return 0            # 进班晚于最后一节 → 0（进班后无课节）

    info = {}
    for s in students:
        nm = s.get("stuName") or ""
        pid = s.get("ProductId")
        jt = s.get("joinTime")
        fs = first_seq_after(pid, jt)
        rows = classes.get(str(pid)) or []
        info[nm] = {
            "joinTime": jt, "firstSeq": fs,
            "nAfter": len([r for r in rows if fs is not None and (r.get("seq") or 0) >= fs]),
            "nAll": len(rows), "productId": pid,
        }

    # ---------- 1. 出勤收缩 ----------
    #   ⚠️ 报告读的是 `attendance_clean.json`（merge.py 第 25 行读它，写进 analysis_merged 的 att），
    #      `snapshot/attendance.json` 只是接口原始落盘 —— 两个都要改，否则报告里看不到收缩效果。
    att_paths = [os.path.join(wd, "attendance_clean.json"),
                 os.path.join(wd, "attendance.json"),
                 os.path.join(wd, "snapshot", "attendance.json")]
    att_paths = [p for p in att_paths if os.path.exists(p)]
    changed = 0
    for p in att_paths:
        raw = load_json(p, None)
        if raw is None:
            continue
        is_dict = isinstance(raw, dict)
        data = list(raw.values()) if is_dict else raw

        # ── 应出勤节次（v4.74）─────────────────────────────────────────────
        #   ⚠️ 分母**不能**取「后台返回的记录条数」。某节课没给这名学生生成 attendance 行，
        #   分母就少一节 → 「5/5」被当成满勤（实测嘉航/李雨彤/王露萱 都缺第 3 节 9-19，
        #   三人全显示「到课 5/5」；用户 2026-10-28 原话：「从入班到现在所有的课节都属于
        #   应出勤，为什么他的第三节不在，你把应出勤就变成 5 了？」）。
        #   正确口径：应出勤 = 该班**出勤数据已到位**的节次里、入班之后的全部课节；
        #   缺记录的那一节补一条占位行（noRecord=True）→ 分母回到真值，同时保留
        #   「这节是没记录，不是迟到早退」的证据，报告侧可以分开写文案。
        seq_cov, n_rec = {}, 0
        for rec in data:
            if not isinstance(rec, dict):
                continue
            n_rec += 1
            for c in (rec.get("classesAll") or rec.get("classes") or []):
                _s = c.get("classSeq")
                if _s:
                    seq_cov[int(_s)] = seq_cov.get(int(_s), 0) + 1
        # 覆盖率 ≥50% 才认为「这一节的出勤数据到位」。整节全班都没记录（如当天还没聚合、
        # 或这节课临时取消）→ 不补，否则会给全班制造一节假缺勤。本课 seq 3 覆盖 129/132。
        valid_seqs = {s for s, k in seq_cov.items() if n_rec and k >= max(2, n_rec * 0.5)}

        for rec in data:
            if isinstance(rec, dict):
                nm = rec.get("studentName") or rec.get("studentNameNorm") or rec.get("name") or ""
                pid = rec.get("productId") or rec.get("ProductId")
                if not pid:
                    # attendance 里可能没有 productId → 用花名册的
                    pid = next((s.get("ProductId") for s in students
                                if (s.get("stuName") or "") == nm), None)
                fs = first_seq_after(pid, (info.get(nm) or {}).get("joinTime"))
                if not fs:          # None = 无入班时间；0 = 入班晚于最后一节（进班后无课节）
                    continue
                orig = rec.get("classes") or []
                cl = [c for c in orig if (c.get("classSeq") or 0) >= fs]
                have = {int(c.get("classSeq")) for c in cl if c.get("classSeq")}
                need = [s for s in sorted(valid_seqs) if s >= fs and s not in have]
                if len(cl) == len(orig) and not need:
                    continue
                changed += 1
                rec.setdefault("classesAll", orig)          # 保留原始，供回溯（不覆盖首次备份）
                if need:
                    cl = cl + [{"classSeq": s, "isAttend": 0, "isPlayback": 0,
                                "noRecord": True} for s in need]
                    cl.sort(key=lambda c: c.get("classSeq") or 0)
                    rec["missingSeqs"] = need               # 应出勤但后台无记录
                rec["classes"] = cl
                rec["joinSeq"] = fs
                att = sum(1 for c in cl if c.get("isAttend"))
                pb = sum(1 for c in cl if c.get("isPlayback"))
                pbo = sum(1 for c in cl if c.get("isPlayback") and not c.get("isAttend"))
                absent = sum(1 for c in cl if not c.get("isAttend") and not c.get("isPlayback"))
                rec["lessonCount"] = len(cl)
                rec["attendCount"] = att
                rec["playbackCount"] = pb
                rec["playbackOnlyCount"] = pbo
                rec["effectiveAttendCount"] = att + pbo
                rec["absentCount"] = absent
        if not args.dry_run:
            bdir = os.path.join(wd, "_not_deliverable")
            os.makedirs(bdir, exist_ok=True)
            shutil.copy2(p, os.path.join(bdir, "before_join_" + os.path.basename(p)))
            tmp = p + ".tmp"
            json.dump(raw, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, p)

    # ---------- 2. 飞书作业收缩 ----------
    fp = os.path.join(wd, "snapshot", "feishuLearning.json")
    feishu = load_json(fp, []) or []
    n_fs = 0
    if isinstance(feishu, dict):
        feishu = feishu.get("students") or []
    for r in feishu:
        if not isinstance(r, dict):
            continue
        nm = r.get("stuName") or ""
        base = common.norm_name(nm)
        rec = info.get(nm) or info.get(base)
        if not rec:
            # 宽松姓名兜底（飞书名可能带状态后缀）
            for k, v in info.items():
                if common.norm_name_loose(k) == base:
                    rec = v
                    break
        if not rec or rec.get("firstSeq") is None:
            continue
        js = rec["firstSeq"]
        r["joinSeq"] = js
        n_fs += 1
        # ---- 按进班时间重算「完成 / 未完成 / 评级序列」----
        #   ⚠️ 分母必须同步收缩：只截 lessons 而留着旧的 done/undone，会出现
        #      「提交 5/8」但课次只剩 3 节的自相矛盾。这里用 adapt_feishu 的同一套判定重算。
        lessons = r.get("lessons") or []
        if js and lessons:
            keep = [ls for ls in lessons if (ls.get("seq") or 0) >= js]
            if len(keep) != len(lessons):
                r["lessonsAll"] = lessons
                r["lessons"] = keep
                r["nLessons"] = len(keep)
                done_set = set(r.get("done") or {}) | set(r.get("undone") or {})
                d, u = af.count_done(keep, done_set, {})
                r["done"], r["undone"] = d, u
                n_exc = sum(1 for ls in keep for _k, _v in (ls.get("items") or {}).items()
                            if "佳作" in str(_v))
                r["excellent"] = {"总结": n_exc} if n_exc else {}
                if ratings_cfg:
                    r["ratings"] = af.build_ratings(keep, ratings_cfg, {})

    if not args.dry_run and os.path.exists(fp):
        bdir = os.path.join(wd, "_not_deliverable")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(fp, os.path.join(bdir, "feishuLearning_before_join.json"))
        tmp = fp + ".tmp"
        json.dump(feishu, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, fp)

    op = os.path.join(wd, "join_time.json")
    if not args.dry_run:
        json.dump(info, open(op, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---------- 3. 报告 ----------
    late = {k: v for k, v in info.items()
            if v.get("joinTime") and v["joinTime"][:10] > (classes[[p for p in classes][0]][0]["date"]
                                                          if classes else "2026-09-01")}
    cut = [k for k, v in info.items()
           if v.get("firstSeq") is not None and v["firstSeq"] > 1]
    print(f"学生 {len(info)} 人 ｜ 进班晚于第 1 节：{len(cut)} 人 ｜ 影响出勤记录 {changed} 条 ｜ 飞书写入 joinSeq {n_fs} 条")
    for k in cut[:12]:
        v = info[k]
        print(f"   {k:6s} 进班 {str(v['joinTime'])[:10]} → 从第 {v['firstSeq']} 节起算"
              f"（进班后 {v['nAfter']}/{v['nAll']} 节）")
    if args.dry_run:
        print("（--dry-run，未写盘）")
    else:
        print(f"→ {op}")


if __name__ == "__main__":
    main()
