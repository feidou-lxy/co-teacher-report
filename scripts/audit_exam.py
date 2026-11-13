#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成绩识别人工核验清单（v4.64）。

为什么需要这个脚本：
  正则和词表永远有边界。v4.64 就是这样翻车的 —— 自动扫描「疑似误判」跑了两次都说 0 残留，
  结果把「线上摸底考明天 10:00—12:00」的 10:00 漏了，13 个学生被打成 10/150 = 7%。
  所以最后一道防线必须是**把每条成绩连原话一起列出来，人眼看一遍**。
  本课全部才 8 人，扫一眼不到半分钟。

用法：
  python3 audit_exam.py                          # 扫 ~/.workbuddy/reports 下所有工作目录
  python3 audit_exam.py --workdir <目录>          # 只扫一个
输出：每人一行「分数 + 命中依据（原话）」。看到不像成绩的 → 补 test_exam.py 用例再改规则。
v5.08：给 --workdir 时自动把清单**落盘**到 <workdir>/exam_audit.txt（与 run_all.py 的
  run_tee 同一落点）—— 第③道防线要求「人眼过」，手工重跑也必须留档，不能只打印。

v4.65 起：这是成绩识别的**第③道防线**，`run_all.py` 出完报告会自动调用本脚本，
并把清单落到 `<workdir>/exam_audit.txt`。也就是说正常跑链路就一定会看到它 ——
不需要谁记得手动跑。SKILL.md「交付前必过的防线」一节是配套说明。
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, ".")
import common  # noqa: E402


def scan(wd):
    p = os.path.join(wd, "chats.json")
    if not os.path.exists(p):
        return None
    c = json.load(open(p, encoding="utf-8"))
    # v5.14：审计的满分推断要与正式链路同科目（宋婕语文回测发现写死「数学」
    #   → 审计清单按 150 推断、报告按 100，两边不一致会误导人眼复核）。
    #   从花名册 productName 取多数票科目；取不到时退回「数学」（历史默认）。
    subj = "数学"
    try:
        st = json.load(open(os.path.join(wd, "students.json"), encoding="utf-8"))
        cnt = Counter()
        for x in st:
            s = common.subject_of(x.get("productName") or "")
            if s:
                cnt[s] += 1
        if cnt:
            top, n = cnt.most_common(1)[0]
            if n * 2 > sum(cnt.values()):
                subj = top
    except Exception:
        pass
    rows = []
    for nm, v in c.items():
        ms = v.get("messages") or []
        tri = list(reversed([
            ((m.get("msgTime") or ""), m.get("role"),
             (m.get("content") or {}).get("content", "") if isinstance(m.get("content"), dict) else "")
            for m in ms]))
        ex = common.extract_exam(tri, nm, subj)
        if ex.get("latest"):
            # v4.65：审计要看的必须是**整句原文**（原来截 70 字，分数偶尔正落在截断处）
            lt = ex["latest"]
            rows.append((nm, ex["why"], lt.get("evi_full") or lt.get("evi") or ""))
    return len(c), rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="")
    a = ap.parse_args()
    dirs = ([a.workdir] if a.workdir
            else sorted(d for d in glob.glob(os.path.expanduser("~/.workbuddy/reports/*"))
                        if os.path.isdir(d)))
    # v5.08：单跑也落盘（v4.65 只有 run_all 的 run_tee 会写 exam_audit.txt，
    #   手工重跑只打印 → 防线没留档）。给 --workdir 且目录存在时，输出同步写文件。
    save_to = ""
    if a.workdir:
        wd_abs = os.path.abspath(os.path.expanduser(a.workdir))
        if os.path.isdir(wd_abs):
            save_to = os.path.join(wd_abs, "exam_audit.txt")
    _buf = []

    def out(line=""):
        _buf.append(line)
        print(line)

    total = 0
    scanned = 0
    for d in dirs:
        r = scan(d)
        if r is None:
            # ⚠️ 不能静默跳过：缺 chats.json 时打「0 人」会被误读成「本课没有成绩」——
            #    防线的假阴性比防线失效更危险（v4.65 实测踩到：--workdir 指错目录 → 静默 0 人）。
            out(f"\n=== {os.path.basename(d)}：⚠️ 缺 chats.json，无法审计 ===")
            out("    → 这不等于「没有成绩」，是审计没跑到。请确认 --workdir 是否指对了。")
            continue
        scanned += 1
        n_group, rows = r
        total += len(rows)
        out(f"\n=== {os.path.basename(d)}：{n_group} 群 → 识别出成绩 {len(rows)} 人 ===")
        for nm, why, evi in rows:
            out(f"  {nm:10s} | {why}")
            out(f"             原话：{evi}")
    out(f"\n合计 {total} 人（已审计 {scanned} 个目录）。"
        f"请逐条对照原话确认：原话里真的有分数吗？")
    if scanned == 0:
        out("⚠️ 没有任何目录被审计 —— 这不算「通过」。正确目录里应有 chats.json。")
    if save_to:
        try:
            with open(save_to, "w", encoding="utf-8") as f:
                f.write("\n".join(_buf) + "\n")
            print(f"\n📄 清单已落盘：{save_to}（第③道防线要求留档人眼过）")
        except OSError as e:
            print(f"[warn] 审计清单写盘失败：{e}")
    if scanned == 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
