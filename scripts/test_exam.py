#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成绩识别回归测试（v4.64）。

血泪来源：李昊祺卡「成绩 11/150 得分率 7% 偏低」—— 家长原话是
「没有月考，11月2日期中考试」，把「11月」的 11 当成了分数。

用法：python3 test_exam.py        # 全过 exit 0，有失败 exit 1
每次改 common.py 的成绩相关规则，**必须**跑这个。
"""
import sys

sys.path.insert(0, ".")
import common  # noqa: E402

# (学生名, 原话, 期望 value, 期望 full)   value=None 表示「不该识别出成绩」
CASES = [
    # ── 必须挡掉：日期 ────────────────────────────────────────────
    ("李昊祺", "没有月考，11月2日期中考试", None, None),
    ("李语辰", "期中11月9日开考", None, None),
    ("某生", "月考定在10月14日晚上", None, None),
    ("某生", "11.11 期中考试", None, None),
    ("某生", "月考 3 月 20 号", None, None),
    # ── 必须挡掉：时间（考前通知） ───────────────────────────────
    ("某生", "线上摸底考明天 10:00—12:00 腾讯会议进行", None, None),
    ("某生", "月考明天上午 9:30 开始", None, None),
    ("某生", "摸底考 10：00 准时开考", None, None),
    ("某生", "月考 10 点开始", None, None),
    # ── 必须挡掉：题号 / 列举 ─────────────────────────────────────
    ("某生", "这次月考卷我看完了，第 17、18、19、20 题大题思路完整结果正确", None, None),
    ("某生", "月考第 15 题整数解个数空着了", None, None),
    ("某生", "月考选择 10 道全对", None, None),
    # ── 必须挡掉：班级平均 ───────────────────────────────────────
    ("乐语", "今天考的一个算是单元测吧，平均60多分", None, None),
    ("某生", "月考班平 82 分，题目挺难", None, None),
    # ── 必须挡掉：账号 / 编号里的数字（v4.89）──────────────────────
    #   实测：吴朱灏宇 群消息「直线和圆，期中考试的重点@ACE-007」→ 取到 007 的 7 →
    #   7/150=5% → 封顶「需关注」。账号编号不是分数，且前导零也不是分。
    ("吴朱灏宇", "直线和圆，期中考试的重点[旺柴]@ACE-007", None, None),
    ("某生", "期中复习资料 A-128 我发群里了", None, None),
    ("某生", "#052 号同学把期中试卷交一下", None, None),
    # ── 必须挡掉：未考 / 时间安排 ────────────────────────────────
    ("某生", "还没月考呢老师", None, None),
    ("某生", "月考成绩还没出", None, None),
    ("某生", "考完再说吧", None, None),
    ("某生", "期中考试范围是函数", None, None),
    # ── 必须保住：真成绩 ────────────────────────────────────────
    ("常梦越", "这次月考121分 班级第五 年级28", 121, 150),
    ("徐麦", "老师，徐麦月考60分，她说很多知识点会", 60, 150),
    ("朱若琪", "月考73，满分100", 73, 100),
    ("李语辰", "这次小测100哈，题简单", 100, 150),
    ("某生", "月考 128/150", 128, 150),
    # ── 必须保住：「没考好」是真实低分，不能被「没考」误杀 ──────────
    ("某生", "这次月考没考好，才 62 分", 62, 150),
    ("某生", "月考没考好", None, None),          # 只有形容词、无分数 → 未知
]

# ── v4.65：卡片上的「原话」必须**包含命中的分数** ────────────────────────
# 用户截图事故：卡片显示「「@联席-小尹老师（嘿袜精 ） 老师，徐…」」——
# 从句首截 22 字，把「60分」切在窗口外，摆了一句话却依然无法溯源。
# 这里锁死：每条的窗口原文里，必须真的出现那个分数（含「分」或「/满分」写法）。
EVI_CASES = [
    ("徐麦", "@联席-小尹老师（嘿袜精 ） 老师，徐麦月考60分，她说很多知识点会，但碰见题就没思路，"
             "考完老师一题又都会，这是什么问题", "60分"),
    ("常梦越", "这次月考121分 班级第五 年级28 老师帮忙分析一下最好给点专项练习 谢谢老师", "121分"),
    ("某生", "老师您好，孩子回来说这次期中考试的卷子特别难，考了88分，我觉得他最近状态不太好", "88分"),
]


def run_evi():
    """原话窗口回归：窗口里必须含命中分数，且 evi_full 是完整句。"""
    ok = fail = 0
    fails = []
    for name, text, must in EVI_CASES:
        ex = common.extract_exam([("2026-10-14T10:00:00", 2, text)], name, "数学")
        lt = ex.get("latest") or {}
        evi = lt.get("evi") or ""
        full = lt.get("evi_full") or ""
        good = must in evi and must in full and len(full) >= len(evi)
        if good:
            ok += 1
        else:
            fail += 1
            fails.append((name, must, evi, full))
    print(f"原话窗口 {len(EVI_CASES)} 条：通过 {ok}，失败 {fail}")
    for nm, must, evi, full in fails:
        print(f"  ❌ {nm} | 窗口里找不到「{must}」")
        print(f"     窗口：{evi}")
        print(f"     全文：{full}")
    return 0 if fail == 0 else 1


def run():
    ok = fail = 0
    fails = []
    for name, text, exp_v, exp_f in CASES:
        tri = [("2026-10-14T10:00:00", 2, text)]
        ex = common.extract_exam(tri, name, "数学")
        latest = ex.get("latest")
        got_v = latest.get("value") if latest else None
        got_f = latest.get("full") if latest else None
        # 期望 None 时，必须完全没有识别结果
        good = (got_v is None and latest is None) if exp_v is None else (got_v == exp_v)
        if good and exp_v is not None:
            # 有明确满分时核对满分
            if exp_f is not None and got_f != exp_f:
                good = False
        if good:
            ok += 1
        else:
            fail += 1
            fails.append((name, text, exp_v, exp_f, got_v, got_f, ex.get("why")))
    print(f"用例 {len(CASES)} 条：通过 {ok}，失败 {fail}")
    for f in fails:
        print(f"  ❌ {f[0]} | 原话「{f[1]}」")
        print(f"     期望 {f[2]}/{f[3]}，实际 {f[4]}/{f[5]}  why={f[6]}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    rc = run()
    rc2 = run_evi()
    sys.exit(rc or rc2)
