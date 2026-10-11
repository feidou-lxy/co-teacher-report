"""步骤9：生成「联席视角」学情报告 HTML（4 块结构 + 按课程 Tab 切换）。

读取 analysis_merged.json，输出 <workdir>/<联席>_学情分析报告.html。

报告结构（每张学生卡）：
  ① 速览卡（定档 + 配合度结论式 + 最该盯 1 件事 + 一句话总评）
  ② 出勤（后台权威）：逐节 ✓/回放/✗ + 是否需关注 + 请假备注
  ③ 知识掌握与薄弱点
  ④ 联席跟进 Action（优先级动作 + 追踪节奏 + 改善信号）
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, sys.path[0])
import common

WINDOW_DEFAULT = "2026-09-01"


def course_of(group_name):
    gn = group_name or ""
    parts = gn.split("-")
    raw = parts[1] if len(parts) > 1 else gn
    if raw == "高二数学清北层E邵":
        raw = "高二数学清北层日E邵"
    m = re.match(r"高二数学(清北层|985层|211层|[\u4e00-\u9fa5]+层)(.+?)(邵|谢胡|胡[\u4e00-\u9fa5]*)$", raw)
    if m:
        return f"高二数学·{m.group(1)}·{m.group(2)}班（{m.group(3)}老师）", raw
    return raw, raw


ERR_MAP = {
    "立体几何/空间向量": "建系/法向量/坐标运算",
    "直线和圆": "位置关系与计算",
    "函数与导数": "求导与单调性讨论",
    "数列": "通项与求和",
    "圆锥曲线": "方程与几何性质",
    "三角函数": "恒等变换",
    "概率统计": "模型识别",
    "解三角形": "正余弦定理解算",
    "不等式/参数": "分类讨论",
    "计算/粗心": "计算符号与漏解",
}
IMPROVE = {
    "立体几何/空间向量": "同类建系/法向量题计算正确率提升",
    "直线和圆": "位置关系与计算类题正确率提升",
    "计算/粗心": "同类题计算错误次数下降",
    "函数与导数": "求导与单调性判断准确率提升",
    "数列": "通项/求和正确率提升",
}


def badge(text, cls):
    return f'<span class="badge {cls}">{text}</span>'


def coop_conclusion(s):
    n_stu, q = s["n_stu"], s["q"]
    sc, pc = s.get("student_coop", 0), s.get("parent_coop", 0)
    if n_stu == 0:
        return "窗口内学生/家长均无发言，暂无法判断配合度，需先主动确认学生状态与学习意愿。"
    stu_lv = {0: "学生本人几乎不露面", 1: "学生自驱偏低", 2: "学生自驱偏低",
              3: "学生自驱中等", 4: "学生自驱较强", 5: "学生自驱标杆级"}[sc]
    par_lv = {0: "无家长互动", 1: "家长参与较少", 2: "家长配合偏弱",
              3: "家长配合一般", 4: "家长配合度较高", 5: "家长高度配合"}[pc]
    if sc <= 2 and pc >= 4:
        typ = "典型“家长推着走”型——家长高度配合但学生本人疏离，建议把学习主动权交还学生（要求学生本人提问、本人交作业）。"
    elif sc >= 4 and pc >= 4:
        typ = "学生自驱与家长配合双高——放手培养型，无需家长强力推动，重点给足深度练习与挑战题。"
    elif sc >= 4 and pc <= 2:
        typ = "学生自驱强、家长参与较少——良性自驱型，可适度引导家长了解学习进度。"
    elif sc <= 2 and pc <= 2:
        typ = "学生与家长配合均偏弱——需重点激活，先确认学习意愿与到课状态。"
    else:
        typ = "配合度中等——维持常规引导即可。"
    ev = f"（窗口内本人主动提问 {q} 处）" if q >= 5 else ""
    return f"{stu_lv}{ev}；{par_lv}。{typ}"


def grade(r):
    s = r["s"]
    if r["timeout"] or r["unreply"]:
        return "红", "重点跟进"
    if s["unresolved"] or s["neg"] > 0 or s.get("hw_neg", 0) > 0 or s.get("att_leave", 0) > 0:
        return "黄", "需关注"
    return "绿", "正常推进"


def att_block(r):
    a = r["att"]
    lv2 = r["s"].get("leave")
    lvnote = (f'<div class="lvnote">聊天记录提及：{ " / ".join(lv2) }</div>') if lv2 else ""
    if not a:
        return ('<div class="blk"><div class="blk-h">② 出勤（后台权威）</div>'
                '<div class="noatt">后台无该生出勤记录（可能已退课 / 未排入该课程）</div>'
                f'{lvnote}</div>')
    chips = []
    for c in a["classes"]:
        seq, dt = c["classSeq"], c["timeStart"]
        if c["isAttend"] == 1:
            cls, txt = "ok", "✓"
        elif c["isPlayback"] == 1:
            cls, txt = "pb", "回放"
        else:
            cls, txt = "no", "✗"
        chips.append(f'<span class="lchip {cls}">第{seq}节 {txt}<i>{dt[5:]}</i></span>')
    n, ab, pbc = a["lessonCount"], a["absentCount"], a["playbackCount"]
    absent_seqs = [c["classSeq"] for c in a["classes"] if c["isAttend"] != 1]
    no_pb = [c["classSeq"] for c in a["classes"] if c["isAttend"] != 1 and c["isPlayback"] != 1]
    if ab == 0:
        lv, txt = "green", "全勤，无需特殊关注"
    elif ab == n:
        lv, txt = "red", f'全部 {n} 节未出勤' + ("（均已看回放）" if pbc == n else "（部分未看回放）") + " —— 建议电话确认是否继续跟课"
    elif ab >= 2 or len(no_pb) >= 1:
        lv, txt = "red", f'缺课 {ab} 节' + (f'，其中第{"、".join(map(str,no_pb))}节未看回放' if no_pb else "，均已看回放") + " —— 需关注"
    else:
        lv, txt = ("yellow", "缺 1 节且未看回放 —— 轻关注") if no_pb else ("green", "缺 1 节但已看回放 —— 常规")
    conc = f'共 {n} 节：出勤 {n-ab} · 缺 {ab} · 有回放 {pbc}。' + (f'缺席第 {"、".join(map(str,absent_seqs))} 节。' if absent_seqs else '')
    return f'''<div class="blk">
      <div class="blk-h">② 出勤（后台权威）</div>
      <div class="attbar">{''.join(chips)}</div>
      <div class="attconc">{conc}</div>
      <div class="attflag {lv}">是否需关注：{txt}</div>
      {lvnote}
    </div>'''


def card_html(r):
    s, g = r["s"], r["g"]
    topics = "".join(f'<span class="chip">{t}</span>' for t in s["top_topics"]) or '<span class="chip">—</span>'
    errs = [ERR_MAP[t] for t in s["top_topics"] if t in ERR_MAP]
    err_html = "、".join(errs) if errs else "—"
    if s["neg"] == 0 and (s["pos"] > 0 or s["q"] >= 5):
        adv = "学习意愿强、沟通配合好，无明显情绪风险" if s["q"] >= 5 else "沟通配合好，关系良好"
    else:
        adv = "—"
    gd, gd_label = grade(r)
    gdbadge = badge(gd_label, "red" if gd == "红" else ("orange" if gd == "黄" else "green"))
    status = ""
    if r["timeout"]:
        status += badge(f'超时 {g.get("timeoutDuration")}', "red")
    if r["unreply"]:
        status += badge("未回复", "red")
    if not r["timeout"] and not r["unreply"]:
        status += badge("正常", "green")
    if s["unresolved"]:
        status += badge("有未解疑问", "orange")
    coop = coop_conclusion(s)
    top_todo = s["todos"][0][1] if s["todos"] else "维持常规跟进"
    todos_html = "".join(
        f'<li><span class="pri pri-{p}">{p}</span> {a} <span class="basis">«{b}»</span></li>'
        for p, a, b in s["todos"]
    )
    cadence = {"高": "每 2–3 天回访一次", "中": "每周一次", "低": "常规跟进即可"}.get(
        s["todos"][0][0], "常规跟进") if s["todos"] else "常规跟进"
    imp = IMPROVE.get(s["top_topics"][0], "该生相关薄弱点正确率提升、疑问闭环") if s["top_topics"] else "学生整体状态稳定"
    return f"""
    <div class="card">
      <div class="chead">
        <div class="cname">{r['stu']}</div>
        <div class="cgroup">{r['course_raw']}</div>
        <div class="cstatus">{gdbadge}{status}</div>
      </div>
      <div class="cmeta">近窗 {s['oldest'][:10]} ~ {s['newest'][:10]} ｜ 可分析 {s['n_text']} 条（生/家长 {s['n_stu']} · 师 {s['n_tea']}）｜ 语音 {s['voice']} 条</div>
      <div class="blk"><div class="blk-h">① 速览卡</div>
        <div class="coop"><b>配合度</b>{coop}</div>
        <div class="watch">最该盯：{top_todo}</div>
        <div class="summary">{s['summary']}</div>
      </div>
      {att_block(r)}
      <div class="blk"><div class="blk-h">③ 知识掌握与薄弱点</div>
        <div class="kv"><span class="k">薄弱</span><span class="chips">{topics}</span></div>
        <div class="kv"><span class="k">错误类型</span><span class="v">{err_html}</span></div>
        <div class="kv"><span class="k">优势</span><span class="v">{adv}</span></div>
      </div>
      <div class="blk"><div class="blk-h">④ 联席跟进 Action</div>
        <ul class="todos">{todos_html}</ul>
        <div class="kv"><span class="k">追踪节奏</span><span class="v">{cadence}</span></div>
        <div class="kv"><span class="k">改善信号</span><span class="v">{imp}</span></div>
      </div>
    </div>"""


CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f6f8;color:#1f2329;margin:0;padding:24px}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:24px;margin:0 0 4px}
.sub{color:#8a9099;font-size:13px;margin-bottom:14px}
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
.kpi{background:#fff;border:1px solid #e6e8eb;border-radius:10px;padding:14px 18px;flex:1;min-width:130px}
.kpi .n{font-size:26px;font-weight:700}
.kpi .l{font-size:12px;color:#8a9099;margin-top:2px}
.kpi.red .n{color:#e54545} .kpi.green .n{color:#2ba471} .kpi.orange .n{color:#ff8f1f}
.section{background:#fff;border:1px solid #e6e8eb;border-radius:12px;padding:18px;margin-bottom:20px}
.section h2{font-size:17px;margin:0 0 12px}
.section h3{font-size:15px;margin:0 0 10px;color:#3a5bdb}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #f0f1f3}
th{color:#8a9099;font-weight:600}
.badge{display:inline-block;font-size:11px;padding:2px 8px;border-radius:10px;margin-left:4px}
.badge.red{background:#fdecec;color:#e54545} .badge.green{background:#e8f7f0;color:#2ba471} .badge.orange{background:#fff3e0;color:#ff8f1f}
.card{background:#fff;border:1px solid #e6e8eb;border-radius:12px;padding:16px;margin-bottom:14px}
.chead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.cname{font-size:17px;font-weight:700}
.cgroup{font-size:12px;color:#8a9099}
.cstatus{margin-left:auto}
.cmeta{font-size:12px;color:#8a9099;margin:6px 0}
.chips{margin:6px 0}
.chip{display:inline-block;background:#eef2ff;color:#3a5bdb;font-size:11px;padding:2px 8px;border-radius:10px;margin:2px 4px 2px 0}
.summary{font-size:13.5px;line-height:1.7;margin:8px 0}
.todos ul{margin:0;padding-left:18px}
.todos li{font-size:13px;line-height:1.7}
.pri{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;border-radius:8px;margin-right:6px}
.pri-高{background:#fdecec;color:#e54545} .pri-中{background:#fff3e0;color:#ff8f1f} .pri-低{background:#eef2f5;color:#6b7280}
.basis{color:#8a9099;font-size:12px}
.blk{border-top:1px dashed #eef0f3;padding-top:10px;margin-top:10px}
.blk-h{font-size:13px;font-weight:700;color:#3a5bdb;margin-bottom:6px}
.coop{font-size:13px;color:#1f2329;margin:2px 0 6px;line-height:1.7}
.coop b{color:#3a5bdb;margin-right:6px}
.watch{font-size:13px;background:#fff7ec;border-left:3px solid #ff8f1f;padding:6px 10px;border-radius:4px;margin:4px 0 8px}
.kv{display:flex;gap:10px;font-size:13px;line-height:1.7;margin:4px 0;align-items:flex-start}
.kv .k{flex:0 0 64px;color:#8a9099;font-weight:600}
.kv .v{flex:1}
.note{font-size:12px;color:#8a9099;line-height:1.6}
.attbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.lchip{display:inline-flex;flex-direction:column;align-items:center;line-height:1.25;font-size:12px;font-weight:700;padding:5px 10px;border-radius:8px;min-width:64px}
.lchip i{font-style:normal;font-size:10px;font-weight:400;opacity:.75}
.lchip.ok{background:#e8f7f0;color:#2ba471}
.lchip.pb{background:#eef2ff;color:#3a5bdb}
.lchip.no{background:#fdecec;color:#e54545}
.attconc{font-size:13px;color:#4b5563;margin-bottom:6px}
.attflag{font-size:13px;border-radius:6px;padding:6px 10px;font-weight:600}
.attflag.red{background:#fdecec;color:#e54545}
.attflag.yellow{background:#fff3e0;color:#ff8f1f}
.attflag.green{background:#e8f7f0;color:#2ba471}
.lvnote{font-size:12px;color:#8a9099;margin-top:6px;line-height:1.6}
.noatt{font-size:13px;color:#8a9099}
.tabs{position:sticky;top:0;z-index:20;background:#f5f6f8;padding:10px 0;display:flex;gap:8px;flex-wrap:wrap;border-bottom:2px solid #e6e8eb;margin-bottom:18px}
.tab{border:1px solid #d8dce3;background:#fff;color:#3a5bdb;font-size:13px;font-weight:600;padding:8px 14px;border-radius:10px;cursor:pointer;line-height:1.3;text-align:left;transition:.15s}
.tab .tcount{font-size:11px;font-weight:400;color:#8a9099}
.tab:hover{border-color:#3a5bdb}
.tab.active{background:#3a5bdb;color:#fff;border-color:#3a5bdb}
.tab.active .tcount{color:#dce3ff}
.ckpis{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", required=True)
    ap.add_argument("--window", default=WINDOW_DEFAULT)
    args = ap.parse_args()
    wd = common.workdir(args)
    A = json.load(open(f"{wd}/analysis_merged.json", encoding="utf-8"))

    rows = []
    for stu, s in A.items():
        g = s["group"]
        timeout = str(g.get("isTimeout")) == "1" or g.get("isTimeout") is True
        unreply = str(g.get("isReplied")) == "0" or g.get("isReplied") is False
        course, course_raw = course_of(g.get("groupName", ""))
        score = (1 if timeout else 0, 1 if s["unresolved"] else 0, s["n_text"])
        rows.append(dict(score=score, stu=stu, s=s, g=g, timeout=timeout,
                         unreply=unreply, course=course, course_raw=course_raw, att=s.get("att")))
    rows.sort(key=lambda x: (-x["score"][0], -x["score"][1], -x["score"][2]))
    course_counter = Counter(r["course"] for r in rows)
    course_order = [c for c, _ in course_counter.most_common()]

    total_msgs = sum(r["s"]["n_text"] for r in rows)
    timeout_n = sum(1 for r in rows if r["timeout"])
    unreply_n = sum(1 for r in rows if r["unreply"])
    unres_n = sum(1 for r in rows if r["s"]["unresolved"])
    glob_topic = Counter()
    for r in rows:
        for t in r["s"]["top_topics"]:
            glob_topic[t] += 1

    course_sections = []
    for ci, course in enumerate(course_order):
        crows = [r for r in rows if r["course"] == course]
        c_timeout = sum(1 for r in crows if r["timeout"])
        c_unres = sum(1 for r in crows if r["s"]["unresolved"])
        c_msgs = sum(r["s"]["n_text"] for r in crows)
        c_absent = sum(1 for r in crows if r["att"] and r["att"]["absentCount"] > 0)
        c_topic = Counter()
        for r in crows:
            for t in r["s"]["top_topics"]:
                c_topic[t] += 1
        topic_rows = "".join(
            f"<tr><td>{t}</td><td>{c}</td><td>{round(100*c/len(crows))}%</td></tr>"
            for t, c in c_topic.most_common())
        cards = "".join(card_html(r) for r in crows)
        course_sections.append(f"""
    <div class="course-sec" id="course-{ci}" {'style="display:none"' if ci>0 else ''}>
      <div class="ckpis">
        <div class="kpi"><div class="n">{len(crows)}</div><div class="l">学生数</div></div>
        <div class="kpi"><div class="n">{c_msgs}</div><div class="l">可分析消息</div></div>
        <div class="kpi red"><div class="n">{c_timeout}</div><div class="l">超时群</div></div>
        <div class="kpi orange"><div class="n">{c_unres}</div><div class="l">未解疑问群</div></div>
        <div class="kpi orange"><div class="n">{c_absent}</div><div class="l">有缺勤(后台)</div></div>
      </div>
      <div class="section"><h3>高频知识点（本课程）</h3>
        <table><tr><th>知识点</th><th>涉及学生数</th><th>占比</th></tr>{topic_rows}</table></div>
      <div class="section"><h3>逐学生表现 &amp; 联席 To-Do（超时置顶）</h3>{cards}</div>
    </div>""")

    tabs = ['<button class="tab active" onclick="showCourse(-1)">📊 全部（{0}）</button>'.format(len(rows))]
    for ci, course in enumerate(course_order):
        n = course_counter[course]
        t = sum(1 for r in rows if r["course"] == course and r["timeout"])
        tabs.append(f'<button class="tab" onclick="showCourse({ci})" title="{course}">{course}<br><span class="tcount">{n}人{t and f" · {t}超时" or ""}</span></button>')
    tabs_html = "".join(tabs)

    glob_topic_rows = "".join(
        f"<tr><td>{t}</td><td>{c}</td><td>{round(100*c/len(rows))}%</td></tr>"
        for t, c in glob_topic.most_common())

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{args.co}（联席）学生学情分析报告 · 按课程</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<h1>{args.co}（联席）· 学生学情分析报告（按课程切换）</h1>
<div class="sub">分析窗口：聊天内容 {args.window} 至今 ｜ 出勤：学生管理后台「课节考勤」权威数据 ｜ 学科：高二数学 ｜ 角色：联席 ｜ 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
<div class="kpis">
  <div class="kpi"><div class="n">{len(rows)}</div><div class="l">负责学生（全量）</div></div>
  <div class="kpi"><div class="n">{total_msgs}</div><div class="l">可分析消息（近窗）</div></div>
  <div class="kpi red"><div class="n">{timeout_n}</div><div class="l">超时群</div></div>
  <div class="kpi red"><div class="n">{unreply_n}</div><div class="l">未回复群</div></div>
  <div class="kpi orange"><div class="n">{unres_n}</div><div class="l">未解疑问群</div></div>
</div>
<div class="tabs" id="tabs">{tabs_html}</div>
<div class="course-sec" id="course-all">
  <div class="section"><h2>一、整体画像：高频知识点</h2>
  <table><tr><th>知识点</th><th>涉及学生数</th><th>占比</th></tr>{glob_topic_rows}</table></div>
  <div class="section"><h2>二、全部学生表现总结 &amp; 联席 To-Do（超时群置顶）</h2>
  {''.join(card_html(r) for r in rows)}</div>
</div>
{''.join(course_sections)}
<div class="section"><h2>三、方法论与口径说明</h2>
<p class="note">
• 取数（群聊）：后台 <code>wxworkSessionStat/getList</code> 月维度与日维度并集得到该联席全部学生群；<code>wxworkChat/getChatList</code> 取每群最近消息（倒序，单群上限 500 条），文本与语音转写均纳入。<br>
• 分析窗口：仅分析 <b>{args.window} 至今</b>的聊天内容。<br>
• 出勤（后台权威）：来自学生管理后台「我的学生 → 跟进学情 → 课节考勤」接口 <code>cClassAttend/getCClassAttendList</code>，逐节课记录 出勤✓ / 缺勤✗ / 是否看回放 + 回放有效时长 + 上课日期。<br>
• 出勤判定：「是否需关注」规则 —— 全缺 → 🔴 高风险建议电话；缺 ≥2 节 或 “缺了且未看回放” → 🔴 需关注；缺 1 节未看回放 → 🟡 轻关注；缺 1 节但已看回放 / 全勤 → 🟢 常规。<b>出勤为独立提示，不改变红黄绿定档。</b><br>
• 请假备注：仅摘录学生在群内自述的请假/看回放缘由，后台无请假原因字段，具体以教务记录为准。<br>
• 报告结构（联席视角）：① 速览卡（定档＋配合度结论＋最该盯 1 件事＋一句话总评）；② 出勤（后台权威，含是否需关注＋请假备注）；③ 知识掌握与薄弱点；④ 联席跟进 Action（优先级动作＋追踪节奏＋改善信号）。<br>
• 配合度（结论式）：依据 学生本人发言密度/主动提问 与 家长代交/代沟通措辞 综合判定类型（如“家长推着走”型 / 放手培养型），数据源未区分发言者 ID，为启发式估算，供跟进策略参考。<br>
• 定档规则：🔴 红=后台标记超时或未回复，或聊天有未闭环关键疑问/负面情绪；🟡 黄=有未解疑问、作业负面信号、出勤异常或负面情绪；🟢 绿=其余正常推进。响应时效（超时/未回复）按监管维度计入定档。<br>
</p></div>
</div>
<script>
function showCourse(idx){{
  document.querySelectorAll('.course-sec').forEach(function(el){{el.style.display='none';}});
  document.querySelectorAll('.tab').forEach(function(el){{el.classList.remove('active');}});
  if(idx<0){{ document.getElementById('course-all').style.display='block'; }}
  else {{ document.getElementById('course-'+idx).style.display='block'; }}
  var tabs=document.querySelectorAll('.tab');
  if(idx<0) tabs[0].classList.add('active'); else tabs[idx+1].classList.add('active');
  window.scrollTo({{top:0,behavior:'smooth'}});
}}
</script>
</body></html>"""
    out = f"{wd}/{args.co}_学情分析报告.html"
    open(out, "w", encoding="utf-8").write(html)
    print(f"✅ HTML 生成完成 ｜ 课程数 {len(course_order)} ｜ {out}")


if __name__ == "__main__":
    main()
