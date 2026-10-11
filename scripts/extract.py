"""步骤6：信号提取（聊天 -> 学情结构化）。

仅分析 --window 起点之后的聊天（默认 2026-09-01）。
提取：薄弱知识点、出勤/作业/提问/情绪信号、配合度（学生自驱 vs 家长支持，启发式）、
未闭环疑问、总评与待办。配合度数据源未区分发言者 ID，为启发式估算。

输出 <workdir>/analysis.json ：{ studentName: {...} }
"""
import argparse
import json
import re
import sys
from collections import Counter

sys.path.insert(0, sys.path[0])
import common

WINDOW_DEFAULT = "2026-09-01"

ATT = ["请假", "病", "发烧", "医院", "回放", "没来", "缺课", "到课", "出勤", "迟到", "早退", "身体", "不舒服", "康复", "休"]
HW = ["作业", "提交", "交作业", "没写", "没做", "不会做", "漏交", "订正", "打卡", "练习", "错题", "刷题", "补"]
Q = ["?", "？", "怎么", "为什么", "如何", "不懂", "听不懂", "不会", "求解", "求", "请教", "迷茫", "卡住", "思路", "咋"]
POS = ["谢谢", "感谢", "辛苦", "麻烦您", "麻烦你", "感激", "太好了", "棒"]
NEG = ["焦虑", "着急", "担心", "烦躁", "抱怨", "不满意", "无奈", "心烦", "压力", "崩溃", "难受", "愁", "委屈"]
STU_SELF = ["我自己", "我这道题", "我算", "我听", "我做", "我提交", "我交了", "我错了", "我漏",
            "我忘", "我这次", "我自己做", "我理解", "我懂", "我试", "我写", "我的作业", "我来看"]
PARENT = ["妈妈", "爸爸", "家长", "我帮", "我替", "帮他", "替他", "已交", "帮交", "孩子", "我家",
          "他作业", "他今天", "他这个", "他没", "他不会", "他那个", "他有点", "他最近"]
TOPICS = {
    "立体几何/空间向量": ["立体几何", "空间向量", "法向量", "建系", "异面直线", "投影", "外接球", "二面角", "线面", "面面", "垂直", "平行", "坐标", "几何体"],
    "直线和圆": ["直线和圆", "圆", "圆心距", "切线", "直线"],
    "函数与导数": ["导数", "函数", "单调性", "极值", "求导", "切线"],
    "数列": ["数列", "等差", "等比", "求和", "通项"],
    "圆锥曲线": ["椭圆", "双曲线", "抛物线", "圆锥", "焦点", "离心率"],
    "三角函数": ["三角函数", "正弦", "余弦", "周期", "诱导"],
    "概率统计": ["概率", "统计", "排列", "组合", "期望", "方差"],
    "解三角形": ["解三角形", "正弦定理", "余弦定理"],
    "不等式/参数": ["不等式", "恒成立", "参数", "取值范围"],
    "计算/粗心": ["粗心", "算错", "计算", "失误", "符号", "少一种", "漏解", "增根"],
}


def get_text(m):
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        for k in ("content", "text"):
            if k in c and isinstance(c[k], str):
                return c[k]
    return ""


def hits(text, kws):
    return sum(text.count(k) for k in kws)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--window", default=WINDOW_DEFAULT, help="分析窗口起点 YYYY-MM-DD")
    args = ap.parse_args()
    wd = common.workdir(args)
    CUT = args.window
    d = json.load(open(f"{wd}/chats.json", encoding="utf-8"))

    students = {}
    for stu, blk in d.items():
        g = blk["group"]
        msgs = [m for m in blk["messages"] if (m.get("msgTime") or "") >= CUT]
        texts = [(m.get("role"), get_text(m)) for m in msgs]
        texts = [(r, t) for r, t in texts if t]
        if not texts:
            students[stu] = {
                "group": g, "n_text": 0, "n_stu": 0, "n_tea": 0, "voice": 0,
                "newest": None, "oldest": None, "att": 0, "hw": 0, "q": 0,
                "pos": 0, "neg": 0, "hw_neg": 0, "att_leave": 0,
                "student_coop": 0, "parent_coop": 0, "top_topics": [],
                "unresolved": False,
                "summary": f"{CUT} 起该群无可分析聊天记录（窗口内无互动）。",
                "todos": [("低", "维持常规跟进，关注后续作业与提问", "窗口内无聊天")],
            }
            continue
        stu_txt = [t for r, t in texts if r == 2]
        tea_txt = [t for r, t in texts if r == 1]
        all_txt = " ".join(t for _, t in texts)
        n_stu = len(stu_txt)
        n_tea = len(tea_txt)
        voice = sum(1 for m in msgs if m.get("msgType") == "voice")
        times = [m.get("msgTime") for m in msgs if m.get("msgTime")]
        newest = times[0] if times else None
        oldest = times[-1] if times else None
        att = hits(all_txt, ATT)
        hw = hits(all_txt, HW)
        q = sum(hits(t, Q) for t in stu_txt)
        pos = hits(all_txt, POS)
        neg = hits(all_txt, NEG)
        stu_self = q + hits(stu_txt, STU_SELF)
        parent = hits(stu_txt, PARENT)
        hw_neg = hits(all_txt, ["没写", "没做", "不会做", "漏交"])
        att_leave = hits(all_txt, ["请假", "回放", "没来", "缺课"])
        student_coop = 0 if n_stu == 0 else 1 + min(4, stu_self // 4)
        parent_coop = 0 if n_stu == 0 else 1 + min(4, (parent + pos) // 2)
        tc = Counter()
        for name, kws in TOPICS.items():
            c = hits(all_txt, kws)
            if c:
                tc[name] = c
        top_topics = [t for t, _ in tc.most_common(3)]
        unresolved = False
        for r, t in texts[-8:]:
            if r == 2 and hits(t, Q):
                unresolved = True
            if r == 1:
                unresolved = False
        parts = [f"近窗覆盖 {oldest[:10]} ~ {newest[:10]}（共 {len(texts)} 条可分析消息；学生/家长 {n_stu} 条、老师 {n_tea} 条）。"]
        parts.append(f"高频问题集中在：{ '、'.join(top_topics) }。" if top_topics else "未提取到明确知识点主题。")
        if att:
            parts.append(f"出勤/健康方面出现 {att} 次相关提及（请假、回放、身体等）。")
        if hw:
            parts.append(f"作业/练习相关提及 {hw} 次。")
        if neg:
            parts.append(f"负面情绪/压力信号 {neg} 处，需关注沟通态度。")
        elif pos:
            parts.append(f"正向反馈 {pos} 处（致谢/配合），关系良好。")
        summary = "".join(parts)
        todos = []
        if str(g.get("isTimeout")) == "1" or g.get("isTimeout") is True:
            todos.append(("高", f"跟进超时未回复咨询（后台标记超时 {g.get('timeoutDuration')}）", "后台响应时效标记"))
        if unresolved:
            todos.append(("高", "回复学生近期提出的未解答疑问", "会话尾部存在未获回应提问"))
        if hw and (hits(all_txt, ["没写", "没做", "不会做", "漏交"]) > 0):
            todos.append(("中", "针对作业未完成/不会做进行辅导或订正跟进", "作业相关负面信号"))
        if att and (hits(all_txt, ["请假", "回放", "没来", "缺课"]) > 0):
            todos.append(("中", "确认出勤与请假情况，必要时补回放/补课", "出勤相关信号"))
        if q >= 5:
            todos.append(("中", "整理该生高频疑问，统一集中答疑一次", f"学生疑问密度高（{q} 处）"))
        if not todos:
            todos.append(("低", "维持常规跟进，关注下次作业与提问", "无明显风险信号"))
        students[stu] = {
            "group": g, "n_text": len(texts), "n_stu": n_stu, "n_tea": n_tea,
            "voice": voice, "newest": newest, "oldest": oldest,
            "att": att, "hw": hw, "q": q, "pos": pos, "neg": neg,
            "hw_neg": hw_neg, "att_leave": att_leave,
            "student_coop": student_coop, "parent_coop": parent_coop,
            "top_topics": top_topics, "unresolved": unresolved,
            "summary": summary, "todos": todos,
        }

    json.dump(students, open(f"{wd}/analysis.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✅ 学生数 {len(students)} -> analysis.json")


if __name__ == "__main__":
    main()
