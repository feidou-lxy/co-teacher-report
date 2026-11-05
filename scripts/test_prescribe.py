"""处方层回归用例（v4.86）。跑法：python3 test_prescribe.py

为什么要有这组用例：处方层的东西**不能靠「跑出来看着像」验收**。
  - 「当期 / 遗留」是这条产品线最关键的一个判断（主讲只讲当期，遗留走补丁包），
    而真实数据里 74 人只命中 0~1 例 —— 没有用例，这个分支等于从没被验证过。
  - 配额（red 2 / orange 1 / green 0）一改就会让「人人三条」回来，必须锁死。
  - 任务表要「合并写、保留业务填的状态」，写错一次就把人家一周的勾选抹了。

用例全部是构造数据，不依赖任何工作目录。
"""
import os
import sys
import tempfile
import csv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prescribe

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"｜{detail}"))


def std(t):
    """测试用归一：只把「空间向量」的两写法合一，其余原样。"""
    return {"立体几何/空间向量": "空间向量"}.get(t, t)


def no_merge(a, b):
    return dict(a or {})


def no_board(b, per_topic=6):
    return {}


def mk(fine=None, mst=None, sig=None, tev=None):
    return {"fine": fine or {}, "topic_mastered": mst or {},
            "topic_signals": sig or {}, "top_topics_evi": tev or {}}


def item(it, at, kind="老师指出", evi="这里算错了", hard=True, cnt=1):
    return {"item": it, "kind": kind, "at": at, "evi": evi, "count": cnt}


print("— 配额 —")
s = mk(fine={"空间向量": [item("建系", "2026-09-10")],
            "函数": [item("定义域", "2026-09-12")]})
check("需重点关注给 2 条（两个知识点各 1 条）", len(prescribe.build_student(
    "甲", s, None, std, no_merge, no_board, "red")) == 2)
# 同一知识点下有 2 个题型 → 仍然只有 1 条任务（去重单位是知识点，不是题型）
s1 = mk(fine={"空间向量": [item("建系", "2026-09-10"), item("基底", "2026-09-12")]})
check("同一知识点只出 1 条（去重靠这个）", len(prescribe.build_student(
    "甲", s1, None, std, no_merge, no_board, "red")) == 1)
check("需关注给 1 条", len(prescribe.build_student(
    "甲", s, None, std, no_merge, no_board, "orange")) == 1)
check("正常 0 条（允许无动作）", prescribe.build_student(
    "甲", s, None, std, no_merge, no_board, "green") == [])
check("良好 0 条", prescribe.build_student(
    "甲", s, None, std, no_merge, no_board, "blue") == [])

print("— 当期 / 遗留 —")
# ① 跨 14 天以上、且不止一次 → 遗留
s = mk(fine={"空间向量": [item("建系", "2026-09-01"), item("建系", "2026-09-20")]})
t = prescribe.build_student("乙", s, None, std, no_merge, no_board, "red")
check("首末跨 19 天 → 遗留", t and t[0]["legacy"], t[0]["type"] if t else "无")
check("遗留动作是「换讲法」", t and "换讲法" in t[0]["lecture"], t[0]["lecture"] if t else "")
check("遗留检测题含「原题」", t and "原题" in t[0]["drill"], t[0]["drill"] if t else "")
check("遗留时间锚不占正课", t and "不占正课" in t[0]["when"], t[0]["when"] if t else "")

# ② 讲过、学生确认听懂，之后又错 → 遗留
s = mk(fine={"空间向量": [item("建系", "2026-10-10")]},
       mst={"空间向量": {"who": "学生", "at": "2026-10-05", "text": "明白了"}})
t = prescribe.build_student("丙", s, None, std, no_merge, no_board, "red")
check("听懂后又错 → 遗留", t and t[0]["legacy"], t[0].get("type") if t else "无")
check("遗留理由写成可核对的一句话", t and "已确认听懂" in t[0]["why"], t[0]["why"] if t else "")

# ③ 只错一次、且在同一天 → 当期（不能因为跨天就判遗留）
s = mk(fine={"空间向量": [item("建系", "2026-09-01", cnt=3)]})
t = prescribe.build_student("丁", s, None, std, no_merge, no_board, "red")
check("单日多次不算遗留", t and not t[0]["legacy"], t[0].get("type") if t else "无")

# ④ 先说明白、之后没再错 → 当期（且不该被判遗留）
s = mk(fine={"空间向量": [item("建系", "2026-09-01")]},
       mst={"空间向量": {"who": "学生", "at": "2026-09-05"}})
t = prescribe.build_student("戊", s, None, std, no_merge, no_board, "red")
check("确认听懂后无新错 → 当期", t and not t[0]["legacy"], t[0].get("type") if t else "无")

print("— 知识点级兜底（只有模块名、没有题型）—")
s = mk(sig={"函数": {"n": 3, "at": "2026-09-08", "text": "这道函数题不会"}},
       tev={"函数": {"n": 3, "at": "2026-09-08", "text": "这道函数题不会"}})
t = prescribe.build_student("己", s, None, std, no_merge, no_board, "orange")
check("只有模块级信号也出处方", len(t) == 1, t)
check("题型留空、不编造", t and t[0]["item"] == "", t[0]["item"] if t else "")
check("讲法引用原话", t and "这道函数题不会" in t[0]["lecture"], t[0]["lecture"] if t else "")
s = mk(sig={"函数": {"n": 1, "at": "2026-09-08", "text": "嗯"}})
check("只提过 1 次不出处方（与卡片同门槛）",
      prescribe.build_student("庚", s, None, std, no_merge, no_board, "red") == [])

print("— 计算/粗心 的专用措辞 —")
s = mk(fine={"计算/粗心": [item("计算与化简错误", "2026-09-10")]})
t = prescribe.build_student("辛", s, None, std, no_merge, no_board, "red")
check("计算类讲法强调写全步骤", t and "每一步写全" in t[0]["lecture"], t[0]["lecture"] if t else "")
check("计算类练习不是「同类题」模板", t and "限时" in t[0]["drill"], t[0]["drill"] if t else "")

print("— 班级处方：当期走正课、遗留走补丁包 —")
rows = []
for i in range(6):                       # 6 人都卡在「函数」，都不是遗留
    rows.append({"stu": f"学生{i}",
                 "s": mk(fine={"函数": [item("定义域", f"2026-09-0{i+1}")]})})
cls = prescribe.build_class(rows, None, std, no_merge, no_board, lambda x: x)
check("当期 × 多人 → 进正课清单", len(cls["must"]) == 1 and cls["must"][0]["topic"] == "函数", cls)
check("当期不进补丁包", cls["patch"] == [], cls["patch"])

rows2 = []
for i in range(3):                       # 3 人「函数」是跨周期遗留
    rows2.append({"stu": f"留{i}",
                  "s": mk(fine={"函数": [item("定义域", "2026-09-01"),
                                       item("定义域", "2026-09-25")]})})
agg2 = {"函数": {"chat": set(r["stu"] for r in rows2), "board": set()}}
cls2 = prescribe.build_class(rows2, None, std, no_merge, no_board, lambda x: x, agg=agg2)
check("遗留 × 多人 → 进补丁包", len(cls2["patch"]) == 1, cls2)
check("遗留不进正课（当期人数按 0 算）",
      all(m["topic"] != "函数" for m in cls2["must"]), cls2["must"])
check("补丁包里人数 = 遗留人数", cls2["patch"][0]["leg_n"] == 3, cls2["patch"][0])

print("— 任务表：合并写，保留业务填的状态 —")
d = tempfile.mkdtemp()
p = os.path.join(d, "t.csv")
tasks = [{"key": "甲|函数", "stu": "甲", "topic": "函数", "type": "当期", "item": "定义域",
          "lecture": "讲**定义域**", "drill": "3 道", "check": "7 天", "when": "下次课",
          "evi": "2026-09-01「算错」", "legacy": False, "why": ""}]
n1, a1 = prescribe.export_csv(p, tasks, "张巍")
check("首次写出 1 行、新增 1", (n1, a1) == (1, 1), (n1, a1))
check("讲法里的 ** 不落盘", "**" not in open(p, encoding="utf-8-sig").read())

rows_ = list(csv.DictReader(open(p, encoding="utf-8-sig")))
rows_[0]["状态"] = "已讲"
rows_[0]["备注"] = "讲过了"
with open(p, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=prescribe.COLS)
    w.writeheader()
    w.writerows(rows_)

n2, a2 = prescribe.export_csv(p, tasks, "张巍")
back = list(csv.DictReader(open(p, encoding="utf-8-sig")))
check("二次写不新增", a2 == 0, a2)
check("业务填的「已讲」被保留", back[0]["状态"] == "已讲", back[0]["状态"])
check("业务填的备注被保留", back[0]["备注"] == "讲过了", back[0]["备注"])
log = prescribe.load_log(p)
check("回读能拿到状态", log.get("甲|函数", {}).get("状态") == "已讲", log)
check("读不到文件返回空（不猜状态）", prescribe.load_log(os.path.join(d, "无.csv")) == {})

print("— 状态映射 —")
card = prescribe.card_html(tasks, {"甲|函数": {"状态": "已讲", "更新": "2026-10-30"}},
                           lambda x: str(x))
check("已讲 → 已讲 · 待检测", "已讲 · 待检测" in card, card[:200])
card2 = prescribe.card_html(tasks, {}, lambda x: str(x))
check("无回读 → 待处理", "待处理" in card2 and "已讲" not in card2, card2[:200])
card3 = prescribe.card_html(tasks, {"甲|函数": {"状态": "已会"}}, lambda x: str(x))
check("已会 → 关闭", "已会 · 关闭" in card3, card3[:200])
check("无任务不出块（正常学生）", prescribe.card_html([], {}, lambda x: str(x)) == "")

print("\n结果：通过 %d，失败 %d" % (len(OK), len(FAIL)))
if FAIL:
    print("失败用例：", "、".join(FAIL))
    sys.exit(1)
