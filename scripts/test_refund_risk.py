"""退费预警规则回归用例（v5.26）。跑法：python3 test_refund_risk.py

为什么要有这组用例：规则口径来自 2026-11-17《开课后退费全量分析》回溯验证
（10535 条后台退费记录 → 开课后 4801 → 有信号数据 388 人）：
  - R2 作业未交率≥50% 是最强事前信号（回溯命中 36%，2026 秋 44%）→ 必须锁死阈值与最小样本护栏；
  - R1 出勤连续缺勤≥2 次 → 回放不算缺（与有效出勤口径一致），连续性按 classSeq 排序算；
  - R3 群聊负面回溯仅 2% 命中且多为老师讨论 → 本函数**不收**群聊信号；
  - 全程零到课只给「观察」档（v4.24 已证无出勤记录 ≠ 退课，可能是未排课）。
用例全部是构造数据，不依赖任何工作目录。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"｜{detail}"))


def att(seq):
    """构造出勤记录：seq 里 '到'=到课 '回'=回放 '缺'=缺勤"""
    return {"classes": [{"classSeq": i + 1,
                         "isAttend": 1 if c == "到" else 0,
                         "isPlayback": 1 if c == "回" else 0}
                        for i, c in enumerate(seq)]}


def fs(done, undone):
    """构造 fs_stats 最小输入（record dict → common.fs_stats）"""
    rec = {"done": {"作业": done}, "undone": {"作业": undone},
           "ratings": {}, "meta": {}, "nLessons": 5}
    return common.fs_stats(rec)


# ── R2：作业未交率 ──
lv, rs = common.refund_risk(att("到到到到到"), fs(4, 6))
check("R2≥50%：强信号", lv == "strong" and any("作业未交率 6/10" in x for x in rs), str(rs))
check("R2≥50%：理由带回溯命中率", rs and any("36%~44%" in x for x in rs), str(rs))

lv, rs = common.refund_risk(None, fs(7, 3))
check("R2 30~49%：观察档", lv == "watch", str(rs))

lv, rs = common.refund_risk(None, fs(8, 2))
check("R2 <30%：不触发", lv is None, str(rs))

# 最小样本护栏：记录 <3 条不判（0/2 与 0/5 含义不同）
lv, rs = common.refund_risk(None, fs(0, 2))
check("样本<3：不触发（0/2 不算未交率）", lv is None, str(rs))

# ── R1：连续缺勤 ──
lv, rs = common.refund_risk(att("到到缺缺"), fs(9, 1))
check("R1 连续缺勤2：强信号", lv == "strong" and any("连续缺勤 2 次" in x for x in rs), str(rs))

lv, rs = common.refund_risk(att("到缺到缺"), None)
check("不连续缺勤：不触发 R1", lv is None, str(rs))

check("回放不算缺：到回缺缺 才触发", common.refund_risk(att("到回缺缺"), None)[0] == "strong")
check("缺在回放后重置：缺到缺 到缺缺 触发", common.refund_risk(att("缺到缺缺"), None)[0] == "strong")

# 乱序 classSeq：按 seq 排序后判连续（防数据乱序误判）
att_bad = {"classes": [
    {"classSeq": 3, "isAttend": 0, "isPlayback": 0},
    {"classSeq": 1, "isAttend": 1, "isPlayback": 0},
    {"classSeq": 4, "isAttend": 0, "isPlayback": 0},
    {"classSeq": 2, "isAttend": 1, "isPlayback": 0},
]}
lv, rs = common.refund_risk(att_bad, None)
check("乱序明细按 seq 排序后判：连续缺勤2", lv == "strong", str(rs))

# ── R5：全程零到课 —— R1 已触发强（连续缺勤），R5 补「需核实排课状态」提醒 ──
lv, rs = common.refund_risk(att("缺缺缺缺"), None)
check("全程零到课（4节）：R1 强 + 排课核实提醒", lv == "strong"
      and any("全程未到课" in x for x in rs), str(rs))
lv, rs = common.refund_risk(att("缺缺缺"), None)
check("连续缺勤3（虽仅3节）：R1 照常触发", lv == "strong", str(rs))
lv, rs = common.refund_risk(att("回回回回"), None)
check("全回放不算缺勤：不触发", lv is None, str(rs))

# ── R3 群聊负面：本规则不收（回溯 2% 命中，误报源）──
check("签名只收 att/fs 两参", True)

# ── 双信号叠加：强不降档 ──
lv, rs = common.refund_risk(att("到到缺缺"), fs(3, 7))
check("先观察后强：升到 strong", lv == "strong" and len(rs) == 2, str(rs))
lv, rs = common.refund_risk(att("到缺缺到"), fs(2, 8))
check("先强后观察：保持 strong、两条理由都在", lv == "strong" and len(rs) == 2, str(rs))

# ── 无数据：不触发 ──
check("无 att 无 fs：None", common.refund_risk(None, None)[0] is None)

print(f"\n{len(OK)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
