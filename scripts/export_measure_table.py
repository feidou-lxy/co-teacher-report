"""导出「三列表」：标签 / 该标签概括的问题 / 联席措施（空）—— 2026-10-27 v1

为什么不再用 export_tags.py 那份：
  那份是「给人通读的全景文档」（5 列 + 报告位置 + 算法口径 + 批注），
  用户的实际用途是**给联席逐条填措施**，粘进飞书表格。多一列都是噪声，
  还得他自己删一遍 —— 他要的是**能直接用的表**。

本脚本因此只做三列，且：
  · 描述一律「问题概括」，不写分数、不写代码口径、不写报告位置；
  · 不用 markdown 语法（`**粗体**` 粘到飞书会原样显示星号）；
  · 同一件事只出现一次（小黑板「空白/实质错误」并入问题标签，
    To-Do 20 条是标签的派生动作，移进页尾附录查对应关系，不占正表）。

⚠️ 只读脚本，不改报告、不改判定。
   标签名从 problems.PROBLEMS 取（同源），描述是本文件维护的短版。

用法：
    python3 export_measure_table.py     # → ~/Desktop/联席措施表_v1.{html,tsv,csv}
"""
import csv
import io
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import problems as pb                                     # noqa: E402

OUT_DIR = os.path.expanduser("~/Desktop")
VER = "v1"

# ══════════════════════════════════════════════════════════════════════
# 问题标签的短描述（21 条，key 与 problems.py 一致）
#   短 ≠ 含糊：「怎么算出来的」关键条件必须留着，联席才知道该不该认同。
# ══════════════════════════════════════════════════════════════════════
SHORT = {
    "interact_silent":
        "整段时间里，学生本人在群里一条实质发言都没有（「收到」「谢谢老师」不算），"
        "家长也不说话 —— 属于完全没打通。",
    "interact_quiet":
        "以前说过话，但最近 14 天没有任何实质发言。是「最近不说了」，不是「从来不说」。",
    "interact_passive":
        "本人实质发言 ≤3 条，问一句应一句，不主动参与讨论。",
    "interact_chat":
        "发言不少，但没有一条跟学习有关（闲聊、表情、无关话题）。不算沉默，"
        "但也没在学习上交流过 —— 这一档不进 Tab② 榜单，只在卡片互动结论里出现。",
    "interact_noask":
        "会回话，但从来不主动提问（整段时间本人提问 0 次）。",
    "submit_tail":
        "某一项作业最近连续 2 次以上评级为空 —— 人正在掉队，优先级最高。",
    "submit_zero":
        "某一项（如「作业一刷」）所有课次都是空。不是最近掉队，是从来就没交过。",
    "submit_low":
        "各项合计提交率低于 60%，多数作业都没交。",
    "submit_item_low":
        "某一项完成率 ≤60%（如「作业二刷 3/8」）。不是全不交，是这一项一直补不上。",
    "quality_down":
        "作业评级从开头到结尾下降（如 A+ → B），且不是连续未交造成的。",
    "quality_low":
        "这一项多数课次没有评级（交了但没人评），或评过的次数不足 3 次 —— "
        "不是质量差，是「看不清质量」。",
    "board_blank":
        "题目发了，白板上什么都没写。属参与度问题 —— 这节课没跟上来。",
    "board_unfin":
        "思路方向对，但来不及写完 / 没写结论。是课堂节奏或书写速度问题，不是不会。",
    "board_hard":
        "白板写完了，但方法或答案错（已剔除「没写完」的）。是能力缺口，最需要针对性补。",
    "score_low":
        "考试得分率低于 65%（或明显低于同班）。这是最强证据，会直接把分层封顶在「需关注」。",
    "service_noresp":
        "学生在群里提了问题，到今天还没有任何人回。",
    "service_unresolved":
        "回复过了，但问题本身没解决（学生继续追问，或老师只给了「再看看」这类话）。",
    "service_slow":
        "学生提问后隔了 24 小时以上才回，且至今仍有部分没交代。",
    "service_open":
        "群里还有没讲完、没确认听懂的知识点。学生没再问，老师也没收尾。",
    "attend_issue":
        "群里提到请假、缺课、只看回放、迟到早退。",
    "emotion":
        "家长或学生提到焦虑、跟不上、不想学、烦这类表达。",
}

# 问题大类的显示名（problems.CATS 的 cdesc 一并写进分组行，方便联席理解这一类在讲什么）
CAT_TITLE = {
    "interact": "一 · 互动参与（人在群里，但不说话 / 不提问）",
    "submit":   "二 · 作业提交（没交 —— 先分辨「不想交」还是「不会做」）",
    "quality":  "三 · 作业质量（交了，但质量在下滑或看不清）",
    "board":    "四 · 课堂作答（小黑板上暴露的参与度 / 节奏 / 能力问题）",
    "score":    "五 · 成绩（最强证据，直接决定分层封顶）",
    "service":  "六 · 响应与服务（责任在老师侧，不是学生的问题）",
    "attend":   "七 · 出勤与状态（请假缺课、情绪压力）",
}

# 只以 To-Do 形式出现、没有独立标签的三条（联席实际会遇到，不能漏）
TODO_ONLY = [
    ("待办 · 催交作业未响应",
     "群里催了，但学生既没交作业、也没回话。"),
    ("待办 · 批改后未订正",
     "作业批改过了，但没见学生改正 —— 错误从头到尾没被处理。"),
    ("待办 · 学生自述作业不会",
     "学生自己说「不会做 / 没思路」。不是不想交，是做不出来 —— 处理方式完全不同。"),
]

# 小黑板：只补问题标签里没覆盖到的 3 类
BOARD_EXTRA = [
    ("小黑板 · 仅未完成收尾",
     "「来不及写完」，不是「不会」。按能力问题去补课会走错方向。"),
    ("小黑板 · 完整且无错",
     "写完整且没有实质错误 —— 正向素材，可以直接拿去跟家长说「这节课他上得很扎实」。"),
    ("小黑板 · 读图失败",
     "视觉模型调用异常，属技术故障。不是学生的任何一类表现，不进分母、不参与判定。"),
]

TIER = [
    ("分层 · 需重点关注",
     "已经在掉队，而且不是单点问题：群里不出声、作业催了不交、白板空白或反复同类错、"
     "成绩偏低，这些通常同时出现。"),
    ("分层 · 需关注",
     "有 1~2 处明确硬伤，其他方面看着还行（如作业连丢 2 次但课都上了）。"
     "表面还在，最容易漏掉的一批。"),
    ("分层 · 正常",
     "没判出问题。但要分开看：① 真正常；② 因为没成绩 / 没白板 / 群里没动静，"
     "才「没东西可扣分」—— 这是缺证据，不是学得好。"),
    ("分层 · 良好",
     "有正面证据撑着（成绩 ≥85%，或白板大面积答对，或老师多次表扬）。"
     "但作业漏交、群里不吭声仍可能同时存在。"),
]

COVER = [
    ("覆盖 · 失联",
     "既没有群聊记录也没有出勤记录。联系不上，也不知道人在不在上课。"),
    ("覆盖 · 出勤但无沟通",
     "课上了（含只看回放），但群里从没说过话，家长也没被触达过。"),
    ("覆盖 · 仅缺沟通通道",
     "飞书表有记录或小黑板有作答，学习在发生，只是没建企微群 —— 缺一条沟通渠道。"),
    ("覆盖 · 单向沟通",
     "群里有聊天，但学生和家长实质发言 0 条，全程只有老师在发通知。"),
    ("覆盖 · 低互动",
     "学生 / 家长近 14 天实质发言只有 1~2 条。问了才应，不问不动。"),
    ("覆盖 · 正常",
     "有群、有人说话、也来上课，沟通链路本身没问题。"),
]

# ══════════════════════════════════════════════════════════════════════
# To-Do 与标签的对应关系（页尾附录，不占正表）
# ══════════════════════════════════════════════════════════════════════
TODO_MAP_EDU = [
    ("提问 · 联席未回复", "→ 提问 · 至今未回复"),
    ("提问 · 仍未解决", "→ 提问 · 回而未解"),
    ("回复迟缓", "→ 回复迟缓 · 待处理"),
    ("疑问 · 未闭环", "→ 疑问 · 未闭环"),
    ("作业信号", "→ 作业评级 · 最近连续未提交 / 学生自述作业不会"),
    ("出勤信号", "→ 出勤 · 异常"),
    ("情绪 · 压力", "→ 情绪 / 压力"),
    ("疑问密度高", "→ 某题型实证错误 ＋ 疑问 · 未闭环"),
    ("常规沟通", "无对应标签（这期间没有风险信号）"),
]
TODO_MAP_CO = [
    ("提问 · 至今未回复", "→ 提问 · 至今未回复"),
    ("提问 · 应允后未解决", "→ 提问 · 回而未解"),
    ("互动 · 沉默不回复", "→ 互动 · 沉默不回复"),
    ("回复迟缓 · 补问确认", "→ 回复迟缓 · 待处理"),
    ("未解答疑问", "→ 提问 · 回而未解"),
    ("互动 · 不积极", "→ 互动 · 不积极"),
    ("催交作业未响应", "→ 待办 · 催交作业未响应"),
    ("批改后未订正", "→ 待办 · 批改后未订正"),
    ("学生自述作业不会", "→ 待办 · 学生自述作业不会"),
    ("引导提问", "→ 互动 · 只回应不提问"),
    ("常规跟进", "无对应标签（这期间没有风险信号）"),
]


def build_groups():
    """返回 [(分组标题, [(标签, 描述), ...]), ...]"""
    gs = []
    for cat, cname, _cdesc in pb.CATS:
        items = pb.by_cat(cat)
        if not items:
            continue
        rows = []
        for p in items:
            if p["k"] not in SHORT:
                raise SystemExit(f"❌ problems.py 新增了 {p['k']}，本文件缺短描述，请补 SHORT")
            rows.append((p["label"], SHORT[p["k"]]))
        if cat == "submit":
            rows.extend(TODO_ONLY)
        gs.append((CAT_TITLE.get(cat, cname), rows))
    gs.append(("八 · 小黑板判定（另外 3 类，与上面「小黑板」两条不重复）", BOARD_EXTRA))
    gs.append(("九 · 学习分层档位（卡片右上角徽章）", TIER))
    gs.append(("十 · 沟通与覆盖档位（决定归到哪一群）", COVER))
    return gs


CSS = """
*{box-sizing:border-box}
body{margin:0;padding:30px 26px 60px;background:#f3f4f6;
  font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1c2029;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto}
h1{font-size:25px;margin:0 0 8px;letter-spacing:.2px}
.sub{color:#6b7280;font-size:13.5px;line-height:1.8;margin-bottom:18px}
.how{background:#fff;border:1px solid #e3e6eb;border-left:5px solid #2f6fd0;border-radius:10px;
  padding:14px 18px;font-size:13.5px;line-height:1.95;margin-bottom:22px}
.how b{color:#1c2029}
.how code{background:#eef2f8;color:#2f6fd0;padding:1px 6px;border-radius:4px;
  font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;
  border:1px solid #e3e6eb;border-radius:10px;overflow:hidden;font-size:14px}
th{position:sticky;top:0;background:#eef1f6;text-align:left;padding:13px 16px;
  font-size:13px;font-weight:700;color:#39404d;border-bottom:1px solid #dfe3e9;z-index:2}
th.c1{width:252px}th.c3{width:272px}
td{padding:15px 16px;border-bottom:1px solid #f0f2f5;vertical-align:top;line-height:1.9}
tr:last-child td{border-bottom:0}
td.lab{font-weight:700;color:#1c2029;font-size:14px}
td.lab .no{display:inline-block;min-width:22px;font-weight:400;font-size:12px;
  color:#a3aab4;font-variant-numeric:tabular-nums}
td.desc{color:#3d4450}
td.act{background:#fffdf4}
tr.grp td{background:#e8ecf3;font-weight:700;font-size:13.5px;color:#33405a;
  padding:11px 16px;border-bottom:1px solid #d8dee8;letter-spacing:.2px}
.foot{margin-top:26px;font-size:12.5px;color:#8b929c;line-height:1.9}
.note{background:#fff;border:1px solid #e3e6eb;border-radius:10px;padding:16px 18px;margin-top:14px}
.note h4{margin:0 0 10px;font-size:14px;color:#33405a}
.note table{font-size:13px;border-radius:8px}
.note td{padding:9px 12px}
.note td:first-child{width:46%;color:#3d4450}
.note td:last-child{color:#6b7280}
.note .two{display:flex;gap:22px;flex-wrap:wrap}
.note .two>div{flex:1;min-width:340px}
.note .h5{font-size:13px;font-weight:700;color:#5a6270;margin:0 0 8px}
details summary{cursor:pointer;font-size:13.5px;color:#2f6fd0;font-weight:600;outline:none}
@media print{
  @page{size:A4 portrait;margin:12mm}
  body{background:#fff;padding:0;font-size:11.5px}
  th{position:static}
  tr.grp td{background:#eef1f6 !important;-webkit-print-color-adjust:exact}
  td.act{background:#fffdf4 !important;-webkit-print-color-adjust:exact}
  tr{page-break-inside:avoid}
}
"""


def build_html(groups):
    n = sum(len(r) for _, r in groups)
    H = [f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>标签与联席措施对照表</title><style>{CSS}</style></head><body><div class="wrap">
<h1>标签与联席措施对照表</h1>
<div class="sub">共 {n} 条标签 ｜ 导出 {datetime.now():%Y-%m-%d %H:%M}　·　
第三列留空，请按行填写联席的实际做法</div>

<div class="how">
<b>怎么用这份表</b><br>
① 要粘进<b>飞书表格</b>：用同目录的 <code>联席措施表_{VER}.tsv</code>（纯三列、无格式），
全选复制 → 飞书表格里 <code>⌘V</code> 直接落三列，不会串行。<br>
② 要贴在<b>飞书文档</b>里：直接复制下面这张表，或把 tsv 粘进去后转成表格。<br>
③ 每条只要填第三列：<b>你们实际会怎么做</b>，写「谁 · 什么时间 · 做什么 · 做到什么算完」就够，
不用写解释。觉得某条判得不对，在备注里写「判错」，我按判定 bug 修。<br>
④ 编号可以用来说「第 17 条」，方便一对一沟通。
</div>

<table><thead><tr><th class="c1">标签</th><th>该标签概括的问题</th>
<th class="c3">联席措施（请填写）</th></tr></thead><tbody>"""]

    i = 0
    for gname, rows in groups:
        H.append(f'<tr class="grp"><td colspan="3">{gname}</td></tr>')
        for lab, desc in rows:
            i += 1
            H.append(f'<tr><td class="lab"><span class="no">{i}</span>{lab}</td>'
                     f'<td class="desc">{desc}</td><td class="act"></td></tr>')
    H.append('</tbody></table>')

    # ── 附录：To-Do ↔ 标签对应 ──
    def _map_tbl(rows):
        out = ['<table>']
        for a, b in rows:
            out.append(f'<tr><td>{a}</td><td>{b}</td></tr>')
        out.append('</table>')
        return "".join(out)

    H.append(f"""<div class="note">
<h4>附录 · 卡片上的 To-Do 与上表哪一条对应</h4>
<p style="margin:0 0 12px;font-size:13px;color:#6b7280;line-height:1.9">
报告卡片上的「最该做 / 其次」是从上表标签派生的动作，<b>没有单独的措施要填</b>，
所以不占正表。列在这里是为了你们对照报告时不会以为「这条标签漏了」。</p>
<div class="two">
<div><p class="h5">教务视角 To-Do（9 条）</p>{_map_tbl(TODO_MAP_EDU)}</div>
<div><p class="h5">联席视角 To-Do（11 条）</p>{_map_tbl(TODO_MAP_CO)}</div>
</div>
</div>
<div class="foot">判定逻辑与报告同源（problems.py / extract.py / coverage.py / learning_level）；
本表只做归纳，不改报告、不改判定。</div>
</div></body></html>""")
    return "".join(H)


def build_flat(groups):
    """纯三列数据：标签 / 描述 / 空。供粘飞书、导入 Excel。"""
    rows = []
    for _, items in groups:
        for lab, desc in items:
            rows.append([lab, desc, ""])
    return rows


def main():
    groups = build_groups()
    html = build_html(groups)

    p_html = os.path.join(OUT_DIR, f"联席措施表_{VER}.html")
    p_tsv = os.path.join(OUT_DIR, f"联席措施表_{VER}.tsv")
    p_csv = os.path.join(OUT_DIR, f"联席措施表_{VER}.csv")

    with io.open(p_html, "w", encoding="utf-8") as f:
        f.write(html)

    rows = build_flat(groups)
    with io.open(p_tsv, "w", encoding="utf-8", newline="") as f:
        f.write("标签\t该标签概括的问题\t联席措施\n")
        for r in rows:
            # 描述里有换行会破坏 tsv → 全部压成一行
            f.write("\t".join(x.replace("\n", " ").strip() for x in r) + "\n")

    with io.open(p_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["标签", "该标签概括的问题", "联席措施"])
        for r in rows:
            w.writerow([x.replace("\n", " ").strip() for x in r])

    print(f"✅ {len(rows)} 条 → {p_html}")
    print(f"   {p_tsv}")
    print(f"   {p_csv}")


if __name__ == "__main__":
    main()
