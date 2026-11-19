"""课节点评（三段式）回归用例（v5.24）。跑法：python3 test_board_comment.py

为什么要有这组用例：点评话术最容易犯的错是「编造细节」和「下判词」——
  - 三段式骨架（我看见/这说明/这件事）的字段必须全部来自 boards_summary 真实字段，
    读图失败的轮不许混进来当证据；
  - 空白为主时**不能**写「不会/没做」，只能动笔动员（漏做还是漏交需要联席确认）；
  - 部分作答只能说「收尾」，不能说「不会」。
用例全部是构造数据，不依赖任何工作目录。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"｜{detail}"))


def d(status="完整作答", errors=None, stuck="", topic="函数", failed=False, **kw):
    r = {"status": status, "errors": errors or [], "stuck_at": stuck,
         "topic": topic, "topic_knowledge": topic, "correct": "待判"}
    r.update(kw)
    if failed:
        r["failed"] = True
        r["fail_reason"] = "视觉模型调用异常"
    return r


# ── 用例 1：全部完整作答、无错误 → 纯正向命名 ──
g = {"lesson": "0906邵小黑板", "lesson_date": "09-06", "rounds": 4,
     "details": [d() for _ in range(4)]}
c = report._lesson_comment3(g)
check("全对：能生成点评", c is not None)
check("全对①：轮数与状态如实", c and "4 轮" in c[0] and "完整写到了最后" in c[0], str(c))
check("全对②：命名是习惯不是判词", c and "习惯" in c[1])
check("全对③：价值落将来", c and "以后" in c[2])
check("全对：不出现禁词", c and not any(w in "".join(c) for w in ("粗心", "不认真", "基础差")))

# ── 用例 2：完整过半但有错点 → 肯定习惯 + 指错点 ──
g = {"lesson": "L", "lesson_date": "09-13", "rounds": 4,
     "details": [d(), d(errors=["收尾未完成"]), d(errors=["收尾未完成"]), d()]}
c = report._lesson_comment3(g)
check("过半有错①：错点被点名", c and "收尾未完成" in c[0], str(c))
check("过半有错②：仍先肯定习惯", c and "习惯" in c[1], str(c))
check("过半有错③：价值落在改错", c and "练熟" in c[2], str(c))

# ── 用例 3：部分作答为主 + stuck_at → 指出具体位置，说「收尾」不说「不会」 ──
g = {"lesson": "L", "lesson_date": "09-20", "rounds": 4,
     "details": [d("部分作答", stuck="设出坐标后未建立方程解最值"),
                 d("部分作答", stuck="算了 √5 后未完成证明"),
                 d(), d("部分作答", stuck="x")]}
c = report._lesson_comment3(g)
check("半途为主①：我看见含卡点位置", c and "设出坐标后未建立方程解最值" in c[0], str(c))
check("半途为主②：命名是「收尾」", c and "收尾" in c[1] and "不当「不会」处理" in c[1], str(c))
check("半途为主③：价值是捡回会做的分", c and "会做但没拿到" in c[2], str(c))

# ── 用例 4：空白为主 → 动笔动员，不下「不会/没做」判词 ──
g = {"lesson": "L", "lesson_date": "09-27", "rounds": 4,
     "details": [d("空白未提交"), d("空白"), d("空白未提交"), d()]}
c = report._lesson_comment3(g)
check("空白为主①：空白数如实", c and "3 轮是空白" in c[0], str(c))
check("空白为主②：不下「不会」判词", c and "不会" not in c[1] and "没做" not in c[1], str(c))
check("空白为主③：这件事指向动笔", c and "动笔" in c[1], str(c))

# ── 用例 5：读图失败的轮不进证据 ──
g = {"lesson": "L", "lesson_date": "10-11", "rounds": 3,
     "details": [d(), d(), d(failed=True)]}
c = report._lesson_comment3(g)
check("失败轮剔除：轮数只算成功的 2 轮", c and c is not None and "3 轮" not in c[0].replace("2 轮", ""), str(c))
check("失败轮剔除：证据轮数为 2", c and "2 轮" in c[0], str(c))

# ── 用例 6：全部失败 / 空 details → 不写（宁缺勿编）──
check("全失败：返回 None", report._lesson_comment3(
    {"lesson": "L", "lesson_date": "x", "details": [d(failed=True)]}) is None)
check("空 details：返回 None", report._lesson_comment3({"lesson": "L", "details": []}) is None)

# ── 用例 7：知识点进价值句（_bk_name 归一口径）──
g = {"lesson": "L", "lesson_date": "x", "rounds": 2,
     "details": [d(topic="函数与导数"), d(topic="函数与导数")]}
c = report._lesson_comment3(g)
check("知识点归一：函数与导数→函数", c and "函数这类题" in (c[2] or c[0]), str(c))

# ── 用例 8：board_block 渲染含点评块 ──
sm = {"rounds": 4, "analyzed": 4,
      "details": [d() for _ in range(4)],
      "status_counts": {"完整作答": 4}, "trend": "基本持平",
      "lessons": [{"lesson": "0906邵小黑板", "lesson_date": "09-06", "rounds": 4,
                   "status_counts": {"完整作答": 4},
                   "details": [d() for _ in range(4)]}],
      "lesson_count": 1}
html = report.board_block(sm)
check("渲染：三段标签齐全", all(k in html for k in ("我看见", "这说明", "这件事")))
check("渲染：默认折叠 + 课次名", 'details class="bdet"' in html and "0906邵小黑板" in html)
check("渲染：无数据课次不出空块", "课节点评" not in report.board_block(None))

print(f"\n{len(OK)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
