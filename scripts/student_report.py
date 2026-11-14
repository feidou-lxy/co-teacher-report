#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学生个人阶段性反馈报告（家长版 + 内部教务折叠区）。SR_VER = v1.2

v1.2 新增：群聊沟通记录时间线、催交/订正明细、学习态度与情绪分析（复用 extract.py 的
POS/NEG/STUDY 词表，不另设第二套判定）、薄弱知识点（群聊 topic_signals + 小黑板错误 + 未解决提问）。

用法：
    python3 student_report.py --course <工作目录名或完整路径> --student <姓名> [--team 团队名] [--out 输出路径]

数据来源（只读，不重算判定）：
    <workdir>/analysis_merged.json     —— 出勤/作业/互动/成绩判定结果（extract+merge 产物）
    <workdir>/snapshot/attendance.json —— 花名册与课节
    <workdir>/snapshot/boards_api.json —— 小黑板批改后台（含图片 URL 与批改分析）
    <workdir>/snapshot/feishuLearning.json —— 飞书学习表（作业评级/打穿题测试/得星/逐节明细）
    <workdir>/snapshot/kuake.json      —— 扩科意向信号（仅内部区）
    <workdir>/profile.json             —— 团队名

红线（沿用主报告）：
    - 只匹配已有快照，不生成任何聊天原文；
    - 无记录必须显式降级（横幅说明），绝不编造小黑板图片或成绩；
    - 对外段落不写累计分、不写 ±分、不出现 Markdown 星号。
"""
import argparse
import base64
import datetime
import json
import os
import re
import sys
import urllib.request

SR_VER = "v1.2"
REPORTS_ROOT = os.path.join(os.path.expanduser("~"), ".workbuddy", "reports")

# 情绪/礼貌词表与 extract.py 保持同源（单一判定口径），导入失败才退回本地副本
try:
    from extract import NEG as EMOTION_NEG, POS as POLITE_POS  # noqa: F401
except Exception:  # pragma: no cover
    EMOTION_NEG = ["焦虑", "着急", "担心", "烦躁", "抱怨", "不满意", "无奈", "心烦", "压力", "崩溃", "难受", "愁", "委屈"]
    POLITE_POS = ["谢谢", "感谢", "辛苦", "麻烦您", "麻烦你", "感激", "太好了", "棒"]

CSS = """
:root { --ink:#1f2329; --sub:#646a73; --line:#e5e6eb; --bg:#f5f6f7; --card:#ffffff;
        --blue:#2f66d8; --green:#0a7d43; --orange:#b26a00; --red:#bb3b3b; --purple:#6a4bd8; }
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif; color:var(--ink);
       background:var(--bg); line-height:1.65; font-size:15px; padding:24px 12px 48px; }
.wrap { max-width:860px; margin:0 auto; }
.hero { background:var(--card); border-radius:14px; padding:26px 30px; border:1px solid var(--line); margin-bottom:16px; }
.hero h1 { font-size:21px; letter-spacing:.5px; }
.hero .course { color:var(--sub); font-size:14px; margin-top:6px; }
.meta-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px 24px; margin-top:16px;
             font-size:13.5px; }
.meta-grid .k { color:var(--sub); }
.banner { background:#fff8e6; border:1px solid #f0d9a0; border-radius:12px; padding:14px 18px; margin-bottom:16px;
          font-size:13.5px; color:#6b5200; }
.banner b { color:#8a5b00; }
.banner ul { margin:8px 0 0 18px; }
.section { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px 26px;
           margin-bottom:16px; }
.section h2 { font-size:16.5px; margin-bottom:12px; padding-left:10px; border-left:4px solid var(--blue); }
.section h2.internal { border-left-color:var(--red); }
.narrative { background:#f0f6ff; border:1px solid #c9dcf5; border-radius:12px; padding:18px 20px;
             font-size:15.5px; line-height:1.9; color:#173a6b; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:10px; margin-bottom:14px; }
.kpi { background:var(--bg); border:1px solid var(--line); border-radius:10px; padding:10px 14px; text-align:center; }
.kpi .n { font-size:22px; font-weight:700; color:var(--blue); }
.kpi .t { font-size:12px; color:var(--sub); margin-top:2px; }
.kpi.warn .n { color:var(--red); } .kpi.ok .n { color:var(--green); }
table { width:100%; border-collapse:collapse; font-size:13.5px; margin-top:6px; }
th, td { border-bottom:1px solid var(--line); padding:7px 10px; text-align:left; vertical-align:top; }
th { color:var(--sub); font-weight:600; background:var(--bg); }
.tag { display:inline-block; padding:1px 9px; border-radius:20px; font-size:12px; white-space:nowrap; }
.tag.ok   { background:#e6f4ec; color:var(--green); }
.tag.play { background:#eef3fd; color:var(--blue); }
.tag.bad  { background:#fdecec; color:var(--red); }
.tag.na   { background:#f2f3f5; color:var(--sub); }
.note { color:var(--sub); font-size:13px; margin-top:10px; }
.degrade { background:#fafafa; border:1px dashed var(--line); border-radius:10px; padding:14px 16px;
           font-size:13.5px; color:var(--sub); }
.evi { background:var(--bg); border-left:3px solid #c9dcf5; padding:8px 12px; margin:8px 0; font-size:13px;
       color:#3d4757; border-radius:0 8px 8px 0; }
.evi .d { color:var(--sub); }
.board-card { border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin:12px 0; }
.board-card .hd { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:8px; font-size:13px; }
.board-card img { max-width:100%; border-radius:8px; border:1px solid var(--line); margin-top:8px; }
.board-card .chip { background:#eef3fd; color:var(--blue); border-radius:6px; padding:1px 8px; }
.board-card .chip.warn { background:#fdecec; color:var(--red); }
.board-card .chip.ok { background:#e6f4ec; color:var(--green); }
details { border:1px solid var(--line); border-radius:12px; margin-top:12px; }
details summary { cursor:pointer; padding:12px 16px; font-size:14px; font-weight:600; color:#8a5b00;
                  background:#fff8e6; border-radius:12px; }
details .body { padding:14px 18px; font-size:13.5px; }
.warn-item { margin:8px 0; padding:10px 14px; background:#fdf3f3; border-left:3px solid var(--red);
             border-radius:0 8px 8px 0; }
.warn-item .lv { font-weight:700; color:var(--red); margin-right:6px; }
footer { color:var(--sub); font-size:12.5px; text-align:center; margin-top:20px; line-height:1.8; }
h3.sub { font-size:14px; color:var(--sub); margin:14px 0 4px; font-weight:600; }
"""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def norm(name):
    return re.sub(r"\d+$", "", str(name or "")).strip()


def resolve_workdir(course):
    if os.path.isdir(course):
        return course
    p = os.path.join(REPORTS_ROOT, course)
    if os.path.isdir(p):
        return p
    raise SystemExit(f"找不到工作目录：{course}（既不是目录，也不在 {REPORTS_ROOT} 下）")


def fetch_image(url, cache_dir, stu, idx):
    """下载小黑板图片并返回 base64 data URI；失败返回 None（显式降级，不放假图）。"""
    try:
        os.makedirs(cache_dir, exist_ok=True)
        ext = os.path.splitext(url.split("?")[0])[1] or ".png"
        local = os.path.join(cache_dir, f"{stu}_{idx}{ext}")
        if not os.path.exists(local) or os.path.getsize(local) == 0:
            req = urllib.request.Request(url, headers={"User-Agent": "ctr-student-report/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            with open(local, "wb") as f:
                f.write(data)
        mime = "image/jpeg" if ext.lower() in (".jpg", ".jpeg") else "image/png"
        with open(local, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        print(f"  [warn] 图片下载失败 {url[:60]}…：{e}", file=sys.stderr)
        return None


def att_status(c):
    if c.get("isAttend"):
        return ("到课", "ok")
    if c.get("isPlayback"):
        return ("回看完成", "play")
    return ("未到课", "bad")


GOOD_CORRECT = ("正确", "基本正确")


def _msg_text(m):
    """群聊消息 → 可展示文本；媒体消息只标类型，不猜测内容。"""
    c = m.get("content")
    if isinstance(c, dict):
        t = c.get("content")
        if t:
            return str(t)
        mt = m.get("msgType") or "media"
        fn = c.get("filename") or ""
        if fn:
            return f"[{mt}：{str(fn)[-40:]}]"
        return f"[{mt}]"
    return str(c or "")


def _chat_timeline(msgs, w_from, w_to, limit=15):
    """窗口内的群聊时间线（老师+学生家长），取最近 limit 条。"""
    out = []
    for m in msgs:
        d = (m.get("msgTime") or "")[:10]
        if w_from and d < w_from:
            continue
        if w_to and d > w_to:
            continue
        out.append(m)
    out.sort(key=lambda m: m.get("msgTime") or "")
    return out[-limit:]


def _emotion_evidence(msgs, w_from, w_to, words):
    """学生/家长消息中命中情绪词的原文证据 [(date, kw, text)]；只引用原文，不改写。"""
    ev, seen = [], set()
    for m in msgs:
        if m.get("role") == 1:
            continue
        d = (m.get("msgTime") or "")[:10]
        if w_from and d < w_from:
            continue
        if w_to and d > w_to:
            continue
        t = _msg_text(m)
        for k in words:
            if k in t:
                key = (d, k, t[:40])
                if key in seen:
                    break
                seen.add(key)
                ev.append((d, k, t[:90]))
                break
    return ev


def _board_card(r, i, img_uri):
    ar = (r.get("analysisResult") or [{}])[0] if r.get("analysisResult") else {}
    if not isinstance(ar, dict):
        ar = {}
    chips = []
    if ar.get("status"):
        chips.append(f"<span class='chip'>{esc(ar['status'])}</span>")
    if ar.get("correct"):
        cls = "ok" if ar.get("correct") in GOOD_CORRECT else "warn"
        chips.append(f"<span class='chip {cls}'>{esc(ar['correct'])}</span>")
    if ar.get("topic_knowledge"):
        chips.append(f"<span class='chip'>知识点：{esc(ar['topic_knowledge'])}</span>")
    errors = ar.get("errors") or []
    note = ar.get("note", "")
    return (
        f"<div class='board-card'><div class='hd'><b>第 {i} 次提交</b>"
        f"<span class='d'>{esc(r.get('className', ''))} · {esc(r.get('_lesson_date', ''))}</span>"
        + "".join(chips)
        + f"</div><div>题面：{esc(ar.get('problem', '') or '—')}</div>"
        + (f"<div>亮点：{esc(note)}</div>" if note and not errors else "")
        + (f"<div style='color:var(--red)'>待改进：{esc('；'.join(errors))}</div>" if errors else "")
        + (f"<img src='{img_uri}' alt='小黑板'/>" if img_uri
           else "<div class='degrade'>图片拉取失败，可在后台查看原图。</div>")
        + "</div>")


def render_board_summary(boards, att, wd, stu):
    """小黑板总结：亮点 / 掌握 / 薄弱 / 进步，全部由批改数据推导；明细收进折叠区。"""
    from collections import Counter

    recs = sorted(boards, key=lambda r: (r.get("_lesson_date", ""), r.get("roundNo") or 0))
    cls_seq = {c.get("classId"): c.get("classSeq") for c in (att.get("classes") or [])}

    def lesson_of(r):
        seq = cls_seq.get(r.get("classId"))
        return f"第{seq}讲" if seq else ""

    def ar_of(r):
        ars = r.get("analysisResult") or []
        return (ars[0] if ars else {}) if ars and isinstance(ars[0], dict) else {}

    graded, pending, blanks = [], [], []
    for r in recs:
        ar = ar_of(r)
        if "空白" in (ar.get("status") or ""):
            blanks.append((r, ar))
        elif (ar.get("correct") or "") in GOOD_CORRECT or ar.get("correct") == "有错":
            graded.append((r, ar))
        elif ar.get("status"):
            pending.append((r, ar))

    n_ok = sum(1 for _, a in graded if a.get("correct") in GOOD_CORRECT)
    n_err = len(graded) - n_ok
    denom = len(graded) + len(pending)
    pct = round(100.0 * n_ok / denom) if denom else None

    by_date = {}
    for r, a in graded:
        by_date.setdefault(r.get("_lesson_date", ""), []).append((r, a))
    dates = sorted(by_date)
    lessons = sorted({lesson_of(r) for r in recs if lesson_of(r)})

    # —— 亮点 ——
    hi = []
    for d in dates:
        items = by_date[d]
        if len(items) >= 2 and all(a.get("correct") in GOOD_CORRECT for _, a in items):
            hi.append(f"{d}（{lesson_of(items[0][0])}）的 {len(items)} 题全部判对，是本阶段状态最好的一次")
    n_sub = len(recs) - len(blanks)
    if len(recs) and n_sub / len(recs) >= 0.8:
        hi.append(f"提交积极：覆盖的 {len(recs)} 轮小黑板中有 {n_sub} 轮有实际作答")
    praises = [(d, lesson_of(r), a.get("note", "")) for d in dates for r, a in by_date[d]
               if (a.get("note") or "") and len(a.get("note", "")) >= 6 and a.get("correct") in GOOD_CORRECT]
    # 过滤纠错性质批注，且每次课最多取 2 条
    NEG_NOTE = ("应", "错", "但", "注意", "不要", "未", "仍", "漏", "宜", "疑似")
    by_d_praise = {}
    for d, les, note in praises:
        if not any(w in note for w in NEG_NOTE):
            by_d_praise.setdefault(d, []).append((les, note))
    for d, items in sorted(by_d_praise.items()):
        for les, note in items[:2]:
            hi.append(f"老师批注（{d} {les}）：{note[:46]}")

    # —— 掌握 ——
    master = Counter()
    for r, a in graded:
        if a.get("correct") in GOOD_CORRECT and not (a.get("errors") or []):
            for t in re.split(r"[、，,;；]", a.get("topic_knowledge") or ""):
                t = t.strip()
                if t:
                    master[t] += 1

    # —— 薄弱 ——
    weak, seen = [], set()
    for r, a in graded:
        for e in (a.get("errors") or []):
            k = e[:30]
            if k not in seen:
                seen.add(k)
                weak.append(f"{lesson_of(r)} · {(a.get('topic_knowledge') or '')[:22]}：{e[:80]}")
    stuck_seen = set()
    for r, a in graded + pending:
        s = a.get("stuck_at") or ""
        if s and s[:30] not in stuck_seen:
            stuck_seen.add(s[:30])
            weak.append(f"{lesson_of(r)} 卡点：{s[:80]}")
    for r, a in blanks:
        weak.append(f"{lesson_of(r)} 第 {r.get('roundNo')} 轮（{(a.get('problem') or '')[:24]}）整轮空白未作答")

    # —— 进步 ——
    prog = []
    # 错误类型关键词：每条有错记录只取第一条错误的冒号前缀，避免同题续条重复计数
    err_kinds = sorted({((a.get("errors") or [""])[0]).split("：")[0][:12]
                        for _, a in graded if a.get("correct") == "有错" and (a.get("errors") or [])})
    if len(dates) >= 2:
        d1, d2 = dates[0], dates[-1]
        g1, g2 = by_date[d1], by_date[d2]
        p1 = sum(1 for _, a in g1 if a.get("correct") in GOOD_CORRECT) / max(len(g1), 1)
        p2 = sum(1 for _, a in g2 if a.get("correct") in GOOD_CORRECT) / max(len(g2), 1)
        if p2 > p1:
            errs1 = [e for _, a in g1 for e in (a.get("errors") or [])]
            line = (f"{d1}（{lesson_of(g1[0][0])}）判对题比例 {round(p1*100)}% → "
                    f"{d2}（{lesson_of(g2[0][0])}）{round(p2*100)}%")
            if errs1:
                line += f"；此前出现的 {len(errs1)} 处错误在 {d2} 未再出现"
            prog.append(line)
        t1 = {t.strip() for _, a in g1 for t in re.split(r"[、，,;；]", a.get("topic_knowledge") or "") if t.strip()}
        # 新知识点必须「判对且无错误」才算新掌握，有错误记录的不进进步轨迹
        t2_good = {t.strip() for _, a in g2
                   if a.get("correct") in GOOD_CORRECT and not (a.get("errors") or [])
                   for t in re.split(r"[、，,;；]", a.get("topic_knowledge") or "") if t.strip()}
        new_ok = sorted(t2_good - t1)
        if new_ok:
            prog.append(f"{d2} 新接触的「{'、'.join(new_ok[:3])}」内容，提交题目全部做对")

    # —— KPI 与结论段 ——
    kpis = f"""
    <div class='kpis'>
      <div class='kpi ok'><div class='n'>{n_sub}</div><div class='t'>提交轮次（共 {len(recs)} 轮）</div></div>
      <div class='kpi'><div class='n'>{len(lessons)}</div><div class='t'>覆盖课讲</div></div>
      <div class='kpi ok'><div class='n'>{n_ok}</div><div class='t'>判对题数</div></div>
      <div class='kpi {'warn' if n_err else 'ok'}'><div class='n'>{pct if pct is not None else '—'}%</div><div class='t'>判对率（有效作答 {denom} 题）</div></div>
      <div class='kpi {'warn' if n_err else 'ok'}'><div class='n'>{n_err}</div><div class='t'>有错题数</div></div>
    </div>"""

    concl = f"本阶段小黑板批改后台共记录 {len(recs)} 轮，覆盖{'、'.join(lessons) or '—'}。"
    if denom:
        if n_err:
            wts = []
            for r, a in graded:
                if a.get("correct") == "有错":
                    for t in re.split(r"[、，,;；]", a.get("topic_knowledge") or ""):
                        if t.strip() and t.strip() not in wts:
                            wts.append(t.strip())
            concl += f"有效作答 {denom} 题做对 {n_ok} 题，失分集中在「{'、'.join(wts[:2]) or '计算细节'}」。"
        else:
            concl += f"有效作答 {denom} 题全部判对。"
    for p in prog[:1]:
        concl += f"趋势：{p}。"

    def _blk(title, color, items, empty):
        if not items:
            return f"<h3 class='sub'>{title}</h3><p class='note'>{empty}</p>"
        lis = "".join(f"<li style='margin:4px 0'>{i}</li>" for i in items)
        return f"<h3 class='sub' style='color:{color}'>{title}</h3><ul style='margin-left:18px'>{lis}</ul>"

    body = kpis + f"<p style='margin:10px 0'>{concl}</p>"
    body += _blk("亮点", "var(--green)", hi[:5], "暂无突出的正面记录")
    body += _blk("已掌握（判对且无错误的知识点，按次数排序）", "var(--blue)",
                 [f"{esc(t)} × {n}" for t, n in master.most_common(6)],
                 "暂无完整判对的知识点记录")
    body += _blk("薄弱点与卡点", "var(--red)", [esc(w) for w in weak[:6]],
                 "无错误与卡点记录")
    body += _blk("进步轨迹", "var(--green)", [esc(p) for p in prog],
                 "现有数据只覆盖一讲，暂无法对比趋势")
    if len(lessons) < len(cls_seq):
        body += (f"<p class='note'>口径说明：现有小黑板后台数据仅覆盖 {esc('、'.join(lessons))}，"
                 "以上结论基于这些提交；其余课讲的后台无该生小黑板数据。</p>")

    # —— 明细折叠区（含图片） ——
    cache = os.path.join(wd, "student_report_assets")
    cards = []
    for i, r in enumerate(recs, 1):
        img = fetch_image(r.get("imageUrl", ""), cache, stu, i)
        cards.append(_board_card(r, i, img))
    body += ("<details><summary>展开查看全部 "
             f"{len(recs)} 次提交明细（含图片与老师批注原文）</summary><div class='body'>"
             + "".join(cards) + "</div></details>")
    facts = {"hi": hi, "master": master.most_common(), "weak": weak, "prog": prog,
             "lessons": lessons, "n_sub": n_sub, "n_ok": n_ok, "denom": denom, "n_err": n_err,
             "err_kinds": err_kinds}
    return body, facts


def build_narrative(stu, att, fs, boards, hw_n, urge, interact, exam_note, note_from_co):
    """给家长的一段话：全部由数据条件拼装，不编造。"""
    eff = att.get("effectiveAttendCount", 0)
    total = att.get("lessonCount", 0)
    play_only = att.get("playbackOnlyCount", 0)
    live = att.get("attendCount", 0)
    absent = att.get("absentCount", 0)
    board_n = len(boards)
    seg = []
    seg.append(f"{stu}同学本阶段共 {total} 讲课程。")
    if live:
        seg.append(f"其中直播到课 {live} 次")
        if play_only:
            seg[-1] = seg[-1].rstrip("。") + f"，另有 {play_only} 次通过回看坚持完成了课程内容"
    elif play_only:
        seg.append(f"虽然没能赶上直播，但孩子通过回看坚持完成了 {play_only} 讲的学习，没有掉队，这份坚持值得肯定")
    if absent:
        seg.append(f"近期的 {absent} 次课暂未看到学习记录")
    if note_from_co:
        seg.append(f"协教老师对孩子的观察反馈是：{note_from_co}")
    # 三个抓手
    gaps = []
    if absent:
        gaps.append("优先恢复到课（或当天完成回看）")
    if fs and fs.get("undone"):
        items = "、".join(f"{k} {v} 次未提交" for k, v in fs["undone"].items())
        gaps.append(f"把作业补起来（{items}）")
    if not board_n:
        gaps.append("开始使用小黑板（每讲确认点拍照提交，老师会逐题批改反馈）")
    if interact in ("bad",) or urge:
        gaps.append("在答疑群里保持沟通（老师询问成绩与近况时回复一下，我们好及时调整辅导重点）")
    if gaps:
        seg.append("接下来最值得一起努力的事：" + "；".join(gaps) + "。")
    if exam_note:
        seg.append(exam_note)
    # 段落断句：除最后一段外，缺句号的补句号
    out = ""
    for i, s in enumerate(seg):
        s = s.replace("**", "")
        if i < len(seg) - 1 and not s.endswith(("。", "！", "？", "；")):
            s += "。"
        out += s
    return out


def render(args):
    wd = resolve_workdir(args.course)
    stu = args.student
    snap = os.path.join(wd, "snapshot")
    meta = load_json(os.path.join(snap, "meta.json")) or {}
    merged = load_json(os.path.join(wd, "analysis_merged.json")) or {}
    boards_api = load_json(os.path.join(snap, "boards_api.json")) or {}
    feishu = load_json(os.path.join(snap, "feishuLearning.json")) or []
    kuake = load_json(os.path.join(snap, "kuake.json")) or {}
    profile = load_json(os.path.join(wd, "profile.json")) or {}
    chats = load_json(os.path.join(wd, "chats.json")) or {}

    a = merged.get(stu)
    if a is None:  # 兼容后台姓名带数字后缀
        for k, v in merged.items():
            if norm(k) == norm(stu):
                stu, a = k, v
                break
    if a is None:
        raise SystemExit(f"analysis_merged.json 中找不到学生「{args.student}」。可先跑 extract → merge 刷新。")

    win = meta.get("window", {})
    w_from, w_to = win.get("startDate", "?"), win.get("endDate", "?")
    team = args.team or profile.get("team", "")
    att = a.get("att", {}) or {}
    fs_rec = next((x for x in feishu if x.get("studentNameNorm") in (stu, norm(stu))), None)
    note_from_co = ((fs_rec or {}).get("meta") or {}).get("note", "")
    co_name = (a.get("group") or {}).get("coTeacherName", "")

    degrades = []
    boards = [r for r in (boards_api.get("records") or [])
              if r.get("studentName") in (stu, norm(stu))]
    board_rounds = sorted({r.get("className", "") for r in (boards_api.get("records") or [])})
    if not boards:
        covered = "、".join(board_rounds) or "（后台本次未返回任何轮次）"
        degrades.append(
            f"<b>小黑板：</b>小黑板批改后台已覆盖 {covered}，未查询到该生的提交记录；"
            "因此本报告没有该生的小黑板图片可展示 —— 不放假图、不推测内容。"
            "建议协教老师确认孩子是否知道小黑板提交渠道。")
    exam = a.get("exam") or {}
    if not exam.get("history") and not exam.get("latest"):
        degrades.append(
            "<b>考试成绩：</b>窗口内群聊未收到该生任何成绩消息，暂无法给出成绩分析。"
            "下文「考试成绩」一节有给家长的建议。")
    g = a.get("group") or {}
    if a.get("n_stu", 0) == 0 and a.get("n_text", 0) > 0:
        degrades.append(
            "<b>沟通通道：</b>本阶段答疑群里老师发送了 "
            f"{a.get('n_text', 0)} 条消息，学生与家长暂无发言 —— 互动维度的结论为「单向沟通」，"
            "不代表学习态度，请结合线下情况判断。")

    # —— 出勤表 ——
    rows = []
    for c in att.get("classes", []):
        st, cls = att_status(c)
        note = ""
        if st == "回看完成":
            note = "通过回看完成本讲学习"
        elif st == "未到课":
            note = "无到课与回看记录"
        rows.append(
            f"<tr><td>第 {c.get('classSeq')} 讲</td><td>{esc(c.get('timeStart', ''))}</td>"
            f"<td><span class='tag {cls}'>{st}</span></td><td>{esc(note)}</td></tr>")
    eff = att.get("effectiveAttendCount", 0)
    total = att.get("lessonCount", 0)
    pct = round(100.0 * eff / total) if total else 0

    # —— 作业（飞书学习表） ——
    hw_html = ""
    fs_rows = []
    undone = (fs_rec or {}).get("undone", {}) or {}
    if fs_rec:
        for les in fs_rec.get("lessons", []):
            items = les.get("items", {}) or {}
            def cell(v):
                if v in (None, "", "—"):
                    return "<span class='tag na'>—</span>"
                if "未提交" in str(v):
                    return f"<span class='tag bad'>{esc(v)}</span>"
                if str(v).strip() in ("X", "×", "x"):
                    return "<span class='tag bad'>缺勤</span>"
                if str(v).strip() in ("√", "✓"):
                    return "<span class='tag ok'>出勤</span>"
                return f"<span class='tag ok'>{esc(v)}</span>"
            fs_rows.append(
                f"<tr><td>第 {les.get('seq')} 节</td>"
                f"<td>{cell(items.get('作业评级'))}</td>"
                f"<td>{cell(items.get('打穿题测试'))}</td>"
                f"<td>{cell(items.get('出勤'))}</td></tr>")
        hw_html = f"""
    <table><tr><th>节次</th><th>作业评级</th><th>打穿题测试</th><th>出勤（协教记录）</th></tr>
    {''.join(fs_rows)}</table>"""
        undone_txt = "、".join(f"{k} {v} 次" for k, v in undone.items()) or "无未提交项"
        hw_html += f"<p class='note'>口径：以上来自协教老师维护的飞书学习表（{esc(fs_rec.get('sourceSheetTitle',''))}）；未提交合计：{esc(undone_txt)}。</p>"
    else:
        degrades.append("<b>作业（飞书学习表）：</b>未在飞书学习表中找到该生记录，作业情况以下方群聊证据为准。")
    urge = a.get("urge_fail", []) or []
    if urge:
        ev = "".join(
            f"<div class='evi'><span class='d'>{esc(d)}</span> 老师提醒（节选）：{esc(t[:60])}…</div>"
            for d, t in urge[:3])
        hw_html += f"<h3 class='sub'>作业沟通记录（群聊原文节选）</h3>{ev}"
    fix_n = a.get("fix_n", 0)
    fix_ok = a.get("fix_ok", 0)
    fix_img = a.get("fix_img", 0)
    if fix_n or fix_img:
        hw_html += (f"<h3 class='sub'>订正 / 改错</h3>"
                    f"<p>老师提出订正 {fix_n} 次，确认完成 {fix_ok} 次；发过图片但无文字确认 {fix_img} 次"
                    f"（发图不计入完成，建议老师核一眼）。</p>")
    else:
        hw_html += (f"<h3 class='sub'>订正 / 改错</h3>"
                    f"<p class='note'>窗口内没有出现订正要求或订正提交记录（发图也不计完成）。</p>")

    # —— 小黑板 ——
    board_facts = None
    if boards:
        board_section, board_facts = render_board_summary(boards, att, wd, norm(stu))
    else:
        board_degrade = next((d.split("</b>", 1)[1] for d in degrades if d.startswith("<b>小黑板")),
                             "窗口内未查询到该生的小黑板提交记录。")
        board_section = f"<div class='degrade'>{board_degrade.strip()}</div>"

    # —— 群聊记录与分析 ——
    room_msgs = (chats.get(stu) or chats.get(norm(stu)) or {}).get("messages") or []
    tl = _chat_timeline(room_msgs, w_from, w_to, limit=15)
    tl_html = ""
    for m in tl:
        who = "老师" if m.get("role") == 1 else "学生/家长"
        cls = "" if m.get("role") == 1 else "play"
        sender = esc(str(m.get("name") or "").split("（")[0][:12])
        txt = esc(_msg_text(m).replace("\n", " ")[:110])
        tl_html += (f"<div class='evi'><span class='d'>{esc((m.get('msgTime') or '')[:16].replace('T',' '))}</span>"
                    f" <span class='tag {cls}'>{who}</span> {sender}：{txt}</div>")
    if not tl_html:
        tl_html = "<div class='degrade'>窗口内该群无可展示消息。</div>"
    chat_html = f"""
    <div class='kpis'>
      <div class='kpi'><div class='n'>{a.get('n_text', 0)}</div><div class='t'>老师消息</div></div>
      <div class='kpi {'ok' if a.get('n_stu_sub', 0) else 'warn'}'><div class='n'>{a.get('n_stu', 0)}</div><div class='t'>学生/家长发言（实质 {a.get('n_stu_sub', 0)}）</div></div>
      <div class='kpi ok'><div class='n'>{a.get('n_study', 0)}</div><div class='t'>学习相关发言</div></div>
      <div class='kpi'><div class='n'>{a.get('q', 0)}</div><div class='t'>主动提问</div></div>
      <div class='kpi {'warn' if a.get('neg', 0) else 'ok'}'><div class='n'>{a.get('neg', 0)}</div><div class='t'>负面情绪词命中</div></div>
    </div>
    <h3 class='sub'>最近消息时间线（窗口内，最多 15 条）</h3>
    {tl_html}
    <p class='note'>口径：聊天接口的角色只有「老师 / 学生家长」两类，学生与家长无法细分；
    「学习相关发言」按技能统一的学习相关性词表判定，闲聊不计入活跃。</p>
    """

    # —— 习惯、态度与情绪 ——
    interact = a.get("interact_lv", "")
    inter_label = a.get("interact") or {"good": "主动积极", "ok": "正常", "normal": "正常", "bad": "暂未互动"}.get(interact, "—")
    leave_n = a.get("att_leave", 0)
    emo = _emotion_evidence(room_msgs, w_from, w_to, EMOTION_NEG)
    pos_n = a.get("pos", 0)
    habit_html = f"""
    <p>互动画像：<b>{esc(inter_label)}</b>。{esc(a.get('interact_evi', ''))}</p>
    <p>答疑响应：{esc(a.get('resp_evi', ''))}</p>
    """
    if pos_n:
        habit_html += f"<p>积极信号：学生/家长侧礼貌致谢 {pos_n} 次；家长配合度评分 {a.get('parent_coop', 0)}/5，学生配合度评分 {a.get('student_coop', 0)}/5。</p>"
    if emo:
        ev = "".join(
            f"<div class='evi' style='border-left-color:var(--orange)'><span class='d'>{esc(d)} · 命中「{esc(k)}」</span>{esc(t)}</div>"
            for d, k, t in emo[:4])
        habit_html += f"<h3 class='sub' style='color:var(--orange)'>情绪信号（群聊原文，共 {len(emo)} 处）</h3>{ev}"
    else:
        habit_html += ("<h3 class='sub' style='color:var(--green)'>情绪信号</h3>"
                       "<p>窗口内学生/家长消息未检出焦虑、压力大、畏难等负面情绪词"
                       f"（礼貌致谢 {pos_n} 次）。</p>")
    if leave_n:
        habit_html += f"<p class='note'>另有 {leave_n} 次出勤/健康相关提及（请假、身体、回放等），详见第一节。</p>"
    if note_from_co:
        habit_html += f"<div class='evi'>协教备注（飞书学习表原文）：{esc(note_from_co)}</div>"

    # —— 知识点：群聊信号 + 小黑板错误 + 未解决提问 ——
    sig = a.get("topic_signals", {}) or {}
    kp_items = []
    for t, e in list(sig.items())[:6]:
        kp_items.append(f"<li style='margin:4px 0'><b>{esc(t)}</b> · {esc(e.get('at', ''))}：{esc(e.get('text', ''))[:80]}</li>")
    if board_facts and board_facts.get("err_kinds"):
        kp_items.append(f"<li style='margin:4px 0'><b>小黑板批改暴露</b>：{esc('、'.join(board_facts['err_kinds'][:4]))}（明细见第二节）</li>")
    if a.get("unresolved"):
        kp_items.append(f"<li style='margin:4px 0' style='color:var(--red)'><b>有未获回应的提问</b>：{esc(a.get('unresolved_q', ''))[:80]}</li>")
    if kp_items:
        kp_html = (f"<p>以下知识点信号来自群聊原话与小黑板批改，按出现时间排列：</p>"
                   f"<ul style='margin-left:18px'>{''.join(kp_items)}</ul>")
        if a.get("resp_n"):
            kp_html += f"<p class='note'>提问响应：{esc(a.get('resp_evi', ''))}</p>"
    else:
        kp_html = ("<div class='degrade'>暂无法提炼知识点画像：小黑板与群聊是本报告知识点的主要证据来源，"
                   "该生本阶段两者均无提交/发言。从下一次作业或小黑板提交开始，报告会自动积累"
                   "「掌握了什么 / 卡在哪里」的记录。</div>")

    # —— 成绩 ——
    if exam.get("history") or exam.get("latest"):
        latest = exam.get("latest") or {}
        rows_ex = "".join(
            f"<tr><td>{esc(h.get('date', ''))}</td><td>{esc(h.get('name', ''))}</td>"
            f"<td>{esc(h.get('score', ''))}</td><td>{esc(h.get('evidence', '') or '')}</td></tr>"
            for h in reversed(exam.get("history", [])))
        exam_html = f"<table><tr><th>日期</th><th>考试</th><th>成绩</th><th>来源依据</th></tr>{rows_ex}</table>"
    else:
        exam_html = ("<div class='degrade'>窗口内没有收到孩子的成绩消息（老师曾询问月考成绩，暂未得到回复）。"
                     "建议家长方便时把月考/期中成绩发给协教老师：我们会做逐题分析，"
                     "把失分点归类成「该拿没拿 / 方法不会 / 计算失误」，再给出针对性的补弱安排。</div>")

    # —— 内部教务区 ——
    warns = []
    for lv, txt, evi in a.get("todos", []) or []:
        warns.append(f"<div class='warn-item'><span class='lv'>{esc(lv)}</span>{esc(txt)}"
                     f"<br><span style='color:var(--sub);font-size:12.5px'>依据：{esc(evi)}</span></div>")
    kuake_rec = None
    ku_map = kuake.get("kuake", {}) if isinstance(kuake, dict) else {}
    ku_list = (ku_map.get(stu) or ku_map.get(norm(stu)) or []) if isinstance(ku_map, dict) else []
    if ku_list:
        kuake_rec = ku_list
    if kuake_rec:
        items = "".join(
            f"<div style='margin-top:4px'>{esc(x.get('subject',''))} · 信号 {esc(x.get('kind',''))} · 原文：{esc(x.get('text',''))}</div>"
            for x in kuake_rec)
        warns.append(f"<div class='warn-item'><span class='lv'>扩科</span>{items}</div>")
    # 情绪 / 家长不满信号（内部）
    PARENT_KW = ["妈妈", "爸爸", "家长", "我帮", "我替", "帮他", "替他", "已交", "帮交", "孩子", "我家"]
    emo_parent = [(d, k, t) for d, k, t in emo if any(p in t for p in PARENT_KW)]
    if emo:
        ev_txt = "；".join(f"{d}「{k}」{t[:36]}" for d, k, t in emo[:3])
        warns.append(f"<div class='warn-item'><span class='lv'>情绪</span>"
                     f"学生/家长消息命中负面情绪词 {len(emo)} 处：{esc(ev_txt)}"
                     f"<br><span style='color:var(--sub);font-size:12.5px'>建议先电话沟通确认状态，再决定是否调整辅导节奏</span></div>")
    if emo_parent:
        ev2 = "；".join(f"{d}「{k}」{t[:36]}" for d, k, t in emo_parent[:2])
        warns.append(f"<div class='warn-item'><span class='lv'>家长不满风险</span>"
                     f"家长侧消息带负面情绪：{esc(ev2)}"
                     f"<br><span style='color:var(--sub);font-size:12.5px'>按业务口径需单独同步教务，优先安抚</span></div>")
    if a.get("hw_neg"):
        warns.append(f"<div class='warn-item'><span class='lv'>作业情绪</span>"
                     f"作业相关沟通中出现负面/抵触语气 {a.get('hw_neg')} 次，催交方式建议改为私信+明确截止时间</div>")
    internal_html = "".join(warns) if isinstance(warns, list) else warns
    if not internal_html:
        internal_html = "<p style='color:var(--sub)'>本阶段无自动识别的警示项。</p>"

    narrative = build_narrative(norm(stu), att, fs_rec, boards, a.get("hw", 0), urge,
                                interact, "", note_from_co)

    banner = "<ul>" + "".join(f"<li>{d}</li>" for d in degrades) + "</ul>" if degrades else ""

    gen_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    snap_at = (meta.get("generatedAt", "") or "")[:16].replace("T", " ")

    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(stu)} · 阶段性学习反馈</title><style>{CSS}</style></head><body><div class="wrap">

<div class="hero">
  <h1>{esc(norm(stu))} · 阶段性学习反馈报告</h1>
  <div class="course">{esc(att.get('course') or (a.get('group') or {}).get('productName', ''))}</div>
  <div class="meta-grid">
    <div><span class="k">统计周期：</span>{esc(w_from)} ~ {esc(w_to)}</div>
    <div><span class="k">协教老师：</span>{esc(co_name)}</div>
    <div><span class="k">班教老师：</span>{esc((a.get('group') or {}).get('classTeacherName', ''))}</div>
    <div><span class="k">团队：</span>{esc(team)}</div>
  </div>
</div>

{f'<div class="banner"><b>数据说明（请先读）</b>{banner}</div>' if degrades else ''}

<div class="section">
  <h2>给家长的一段话</h2>
  <div class="narrative">{narrative}</div>
</div>

<div class="section">
  <h2>一、出勤情况</h2>
  <div class="kpis">
    <div class="kpi"><div class="n">{total}</div><div class="t">应出勤课节</div></div>
    <div class="kpi ok"><div class="n">{eff}</div><div class="t">有效出勤（到课+回看）</div></div>
    <div class="kpi"><div class="n">{pct}%</div><div class="t">有效出勤率</div></div>
    <div class="kpi {'ok' if att.get('attendCount') else 'warn'}"><div class="n">{att.get('attendCount', 0)}</div><div class="t">直播到课</div></div>
    <div class="kpi {'warn' if att.get('absentCount') else 'ok'}"><div class="n">{att.get('absentCount', 0)}</div><div class="t">未到课</div></div>
  </div>
  <table><tr><th>课节</th><th>日期</th><th>状态</th><th>说明</th></tr>{''.join(rows)}</table>
  <p class="note">口径：「应出勤课节」为入班后出勤数据已到位的课节；购买直播课的学生，当天回看也计入有效出勤。
  数据来自课程后台，截至 {esc(snap_at)}。</p>
</div>

<div class="section">
  <h2>二、小黑板完成情况</h2>
  {board_section}
</div>

<div class="section">
  <h2>三、作业情况</h2>
  {hw_html}
</div>

<div class="section">
  <h2>四、考试成绩</h2>
  {exam_html}
</div>

<div class="section">
  <h2>五、群聊沟通记录</h2>
  {chat_html}
</div>

<div class="section">
  <h2>六、学习习惯、态度与情绪</h2>
  {habit_html}
</div>

<div class="section">
  <h2>七、知识点掌握与薄弱点</h2>
  {kp_html}
</div>

<div class="section">
  <h2 class="internal">八、教务警示区（内部，不对家长展示）</h2>
  {internal_html}
</div>

<footer>
  数据来源：课程后台（出勤/课节）· 小黑板批改后台 · 企微答疑群（作业与沟通）· 飞书学习表（作业评级）<br>
  快照时间 {esc(snap_at)} · 报告生成 {esc(gen_at)} · 学情分析技能 student_report {SR_VER}<br>
  本报告由系统自动汇总生成，仅供参考；如与实际情况有出入，请以老师核实为准。
</footer>
</div></body></html>"""

    out = args.out or os.path.join(wd, f"{norm(stu)}_阶段性反馈.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    body = html
    assert "**" not in body, "正文出现 Markdown 星号"
    leftovers = re.findall(r"\{[a-z_][a-z_0-9]*\}", body)
    assert not leftovers, f"占位符残留：{leftovers[:5]}"
    print(f"OK  {out}")
    print(f"    出勤 {eff}/{total}（直播 {att.get('attendCount',0)} / 回看 {att.get('playbackOnlyCount',0)} / 缺 {att.get('absentCount',0)}）"
          f" · 小黑板 {len(boards)} 条 · 降级说明 {len(degrades)} 条")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", required=True, help="工作目录名（reports 下）或完整路径")
    ap.add_argument("--student", required=True)
    ap.add_argument("--team", default="", help="团队名，缺省取 profile.json 的 team")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    render(args)


if __name__ == "__main__":
    main()
