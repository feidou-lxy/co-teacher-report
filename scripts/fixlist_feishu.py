#!/usr/bin/env python3
"""飞书表「待修清单」页（v4.21，v4.48 泛化到任意团队）。

回答业务方的一个具体问题：**课程名列有问题的，到底是哪些行？**
团队名取自 `<workdir>/profile.json` 的 `team` 字段（娜娜组 / 孙一文组…通用）。

数据源（都从原始表直接扫，保证可复现，不吃 snapshot 的二手结论）：
  <workdir>/feishu_raw.json              —— pull_feishu.py 拉下来的原始格（含行号）
  <workdir>/snapshot/feishuMeta.json     —— 交叉校验：badCourses 是适配器实际判定的清单

四类（同一根因「课程名不是后台全名」，但**团队要做的动作完全不同**）：
  A emptyCol   课程列**空**          → 要**补填**（适配器按 sheet 名兜底，而 sheet 名=老师名）
  B #N/A       课程列是公式错误值      → 要**改值**（重填或修公式）
  C alias      简写课程名（已配 courseAlias 归位） → 已修，列出来供核对
  D placeholder 姓名列与课程列都是表头文字 → **模板残留行**，不是真学生

⚠️ 本页是**团队内部业务工具**，含学生姓名，勿外传。
"""
import argparse
import collections
import datetime
import html
import json
import os
import re

TZ = "+08:00"

CUR_COL_NAMES = {"课程/班型", "课程", "课程名", "班型", "课程名称"}
NAME_COL_NAMES = {"学生姓名", "学生", "姓名"}
CO_COL_NAMES = {"老师", "联席教师", "联席"}
JIAO_COL_NAMES = {"教务/主讲", "教务", "教务主任"}
SEQ_COL_NAMES = {"序号", "#"}
PLACEHOLDER_NAMES = {"学生姓名", "姓名", "学生", "学生名字"}


def load(p, d=None):
    if not os.path.exists(p):
        return d
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def esc(v):
    return html.escape(str(v if v is not None else ""))


def find_cols(vals):
    """按**表头文字**定位列（元数据列序不固定 → 不能按列号）。"""
    out = {}
    for r in (vals or [])[:4]:
        for i, c in enumerate(r or []):
            s = str(c or "").strip()
            for key, names in (("cur", CUR_COL_NAMES), ("nm", NAME_COL_NAMES),
                               ("co", CO_COL_NAMES), ("jiao", JIAO_COL_NAMES),
                               ("seq", SEQ_COL_NAMES)):
                if key not in out and s in names:
                    out[key] = i
    return out


def col_index(v):
    """列声明 → 0-based 索引。支持 'A'/1（1-based 数字）/0（0-based）与 'AA'。"""
    if isinstance(v, (int, float)):
        return int(v) - 1
    s = str(v or "").strip().upper()
    if not s:
        return None
    if s.isdigit():
        return int(s) - 1
    n = 0
    for ch in s:
        if not ("A" <= ch <= "Z"):
            return None
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def scan(raw, alias_keys=(), prod_map=None, col_roles=None):
    """扫描原始表 → 按成因分组。

    ⚠️ 只读原始格（不用 snapshot 的二手结论），保证可复现。
    ⚠️ 课程名的来源**每个团队不同**，必须先判来源再判对错：
        · 有「课程」列 → 逐行检查列值（娜娜）
        · 表头为空/合并单元格 → 靠 `columnRoles` 显式声明列（孙一文「某生」表）
        · 无课程列 → 课程名来自 **sheet 名**（若 sheet 名本身就是课程全名）或 `sheetProductMap`
          —— 这两种是**表级**问题（整表），不该拆成行级清单。
    """
    alias_keys = set(alias_keys or ())
    prod_map = prod_map or {}
    col_roles = col_roles or {}
    groups, sheet_level = {}, []
    for sh in raw.get("sheets") or []:
        sid = sh.get("sheetId")
        title = sh.get("title") or ""
        vals = sh.get("values") or []
        m = find_cols(vals)
        # profile.columnRoles 的显式覆盖优先（表头文字匹配不到时唯一办法）
        ro = (col_roles.get(sid) or {}).get("productName") or {}
        ro = ro if isinstance(ro, dict) else {}
        if "col" in ro:
            ci = col_index(ro.get("col"))
            if ci is not None:
                m["cur"] = ci
        fill_down = bool(ro.get("fillDown"))       # 合并单元格按课程分段 → 向下继承
        if m.get("cur") is None:
            # 课程名不来自列 → 表级判断：sheet 名本身是课程全名？或已配 sheetProductMap？
            if not re.search(r"20\d\d[QS]-", title) and not prod_map.get(sid):
                sheet_level.append({"sheetId": sid, "sheetTitle": title,
                                    "rows": len(vals)})
            continue

        def g(row, k):
            j = m.get(k)
            if j is None or j >= len(row or []):
                return ""
            return str(row[j] or "").strip()

        carry = ""
        for ri, row in enumerate(vals):
            if ri == 0:
                continue
            nm = g(row, "nm")
            cur = g(row, "cur")
            if fill_down:                          # ⚠️ 不留 carry 会把整列判成空
                if cur:
                    carry = cur
                else:
                    cur = carry
            if not nm and not cur:
                continue
            rec = {"sheetId": sid, "sheetTitle": title,
                   "row": ri + 1, "seq": g(row, "seq"), "stuName": nm,
                   "course": cur, "co": g(row, "co"), "jiao": g(row, "jiao")}
            if nm in PLACEHOLDER_NAMES and cur in ("课程", "课程/班型"):
                groups.setdefault("placeholder", []).append(rec)
            elif not cur:
                if nm:
                    groups.setdefault("emptyCol", []).append(rec)
            elif cur.startswith("2026Q-"):
                continue
            elif cur in alias_keys:
                groups.setdefault("alias", []).append(rec)
            elif cur.startswith("#") or cur in ("N/A", "NA"):
                groups.setdefault("errorVal", []).append(rec)
            else:
                groups.setdefault("otherBad", []).append(rec)
    for k in groups:
        groups[k].sort(key=lambda r: (r["sheetTitle"], r["row"]))
    return groups, sheet_level


CSS = """
:root{--ink:#1c2430;--sub:#5b6b7c;--line:#e3e8ee;--bg:#f6f8fa;--card:#fff;
--ok:#1f7a44;--warn:#b8741a;--bad:#c0392b;--acc:#1f5f8b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 22px 80px}
h1{font-size:25px;margin:0 0 6px}
h2{font-size:19px;margin:36px 0 12px;padding-bottom:8px;border-bottom:2px solid var(--line)}
h3{font-size:15.5px;margin:20px 0 8px}
.sub{color:var(--sub);font-size:13.5px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px 18px;margin:12px 0;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.tldr{background:linear-gradient(135deg,#eef6f1,#eaf1f8);border-color:#cfe3d8}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
th{background:#f0f3f7;font-weight:600;font-size:12.5px;color:#39485a}
td.c{text-align:center;white-space:nowrap}
td.nm{font-weight:600;white-space:nowrap}
td.row{font-variant-numeric:tabular-nums;color:var(--acc);font-weight:600}
code{background:#eef2f6;padding:1px 5px;border-radius:4px;font-size:12.5px}
.kpis{display:flex;gap:10px;flex-wrap:wrap;margin:4px 0 6px}
.kpi{flex:1 1 140px;background:#fff;border:1px solid var(--line);border-radius:10px;padding:11px 13px}
.kpi .n{font-size:23px;font-weight:800;line-height:1.2}
.kpi .l{font-size:12px;color:var(--sub);margin-top:2px}
.kpi.bad{border-color:#f3cfcf;background:#fdf6f6}.kpi.bad .n{color:var(--bad)}
.kpi.ok{border-color:#cdeadd;background:#f4fbf7}.kpi.ok .n{color:var(--ok)}
.pill{display:inline-block;font-size:12px;padding:1.5px 8px;border-radius:20px;
background:#eef2f6;color:var(--sub);margin-right:5px}
.note{background:#fffaf0;border:1px solid #f0e0c0;border-radius:10px;padding:12px 14px;
font-size:13.5px;color:#6b5426}
.foot{margin-top:44px;padding-top:14px;border-top:1px solid var(--line);
color:var(--sub);font-size:12.5px}
@media print{body{background:#fff}.card{box-shadow:none}}
"""


def sheet_tables(rows, note=""):
    """按 sheet 分组出表（联席/教务拿到自己那张表就能直接改）。"""
    by = collections.OrderedDict()
    for r in rows:
        by.setdefault(r["sheetTitle"], []).append(r)
    out = []
    for title, rs in by.items():
        body = []
        for r in rs:
            body.append(
                f'<tr><td class="row">{r["row"]}</td>'
                f'<td class="c">{esc(r["seq"] or "—")}</td>'
                f'<td class="nm">{esc(r["stuName"] or "（空）")}</td>'
                f'<td class="c">{esc(r["co"] or "—")}</td>'
                f'<td class="c">{esc(r["jiao"] or "—")}</td>'
                f'<td>{esc(r["course"]) if r["course"] else "<i>（空）</i>"}</td></tr>')
        out.append(
            f'<h3>表「{esc(title)}」 · {len(rs)} 行</h3>'
            '<table><tr><th>飞书行号</th><th>序号</th><th>学生</th>'
            '<th>联席</th><th>教务</th><th>课程/班型 现值</th></tr>'
            + "".join(body) + "</table>")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    wd = args.workdir
    # v4.48：团队名从 profile.json 的 team 字段读（原版写死「娜娜团队」，
    #   给孙一文团队跑时标题/文件名会挂错团队名）。兼容缺字段的老 profile。
    prof = load(os.path.join(wd, "profile.json"), {}) or {}
    team = (prof.get("team") or "").strip() or "本团队"
    team_short = team.split("·")[-1].strip() or team
    out = args.out or os.path.join(wd, "..", "最终产物", f"{team_short}_飞书表待修清单.html")

    raw = load(os.path.join(wd, "feishu_raw.json"), {}) or {}
    meta = load(os.path.join(wd, "snapshot", "feishuMeta.json"), {}) or {}
    fsc = prof.get("feishu") or {}
    prod_map = {k: v for k, v in (fsc.get("sheetProductMap") or {}).items()
                if not str(k).startswith("_")}
    col_roles = {k: v for k, v in (fsc.get("columnRoles") or {}).items()
                 if not str(k).startswith("_")}
    aliases = {k: v for k, v in (meta.get("courseAlias") or {}).items()}
    groups, sheet_level = scan(raw, aliases.keys(), prod_map, col_roles)

    A = groups.get("emptyCol") or []
    B = groups.get("errorVal") or []
    C = groups.get("alias") or []
    D = groups.get("placeholder") or []
    O = groups.get("otherBad") or []
    todo = len(A) + len(B) + len(O)

    # 交叉校验：适配器实际判定的清单（badCourses）与本次扫描是否一致
    bc = (meta.get("badCourses") or {})
    bc_rows = {(r.get("sheetTitle"), r.get("row")) for r in (bc.get("rows") or [])}
    scan_rows = {(r["sheetTitle"], r["row"]) for r in A + B + O}
    only_meta = sorted(bc_rows - scan_rows)
    only_scan = sorted(scan_rows - bc_rows)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    H = [f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{team} · 飞书表待修清单</title><style>{CSS}</style></head><body><div class="wrap">
<h1>{team} · 飞书表待修清单</h1>
<div class="sub">{team} 学生档案 · 生成于 {now} {TZ} · 本页含学生姓名，仅供内部跟进使用</div>

<div class="card tldr">
<p style="margin-top:0"><b>一句话</b>：有 <b>{todo} 行</b>的「课程/班型」列不对，
这些学生在正式学情报告里会<b>整条不出现</b>（报告按「<b>学生姓名 + 课程名</b>」两个都对上才显示）。
不是显示得不准，是<b>静默缺席</b>。</p>
<p style="margin-bottom:0"><b>怎么用</b>：下表的「飞书行号」就是飞书表格里左侧的行号，
按 sheet 分组，谁的表谁改。改完重新跑一次流程即可，<b>不用改任何代码</b>。</p>
</div>

<div class="kpis">
  <div class="kpi bad"><div class="n">{len(A)}</div><div class="l">要<b>补填</b>课程名（列是空的）</div></div>
  <div class="kpi bad"><div class="n">{len(B)}</div><div class="l">要<b>改值</b>（填的是 <code>#N/A</code>）</div></div>
  <div class="kpi ok"><div class="n">{len(C)}</div><div class="l">已自动归位（简写课程名）</div></div>
  <div class="kpi"><div class="n">{len(D)}</div><div class="l">模板残留行（不是真学生）</div></div>
</div>
"""]

    H.append(f"""<h2>1. 要「<span style="color:#c0392b">补填</span>」—— 课程列是空的（{len(A)} 行）</h2>
<div class="card">
<p style="margin-top:0">这 {len(A)} 行的「课程/班型」格子<b>没填</b>。
适配器不会瞎猜，只能退一步拿 <b>sheet 名</b>当课程名 —— 偏偏娜娜团队的 sheet 名是<b>老师名</b>，
于是课程名变成了「何树威」「教务-邓琨薇」这种，自然对不上任何一门课。</p>
<div class="note">动作：在这些行补上正确的课程全名（形如 <code>2026Q-SX-高一数学B版985层…</code>）。
同一张表里其他行就是现成的写法，复制一下即可。</div>
{sheet_tables(A)}
</div>""")

    H.append(f"""<h2>2. 要「<span style="color:#c0392b">改值</span>」—— 课程列填了 <code>#N/A</code>（{len(B)} 行）</h2>
<div class="card">
<p style="margin-top:0">这些格子的内容是飞书公式的<b>错误值 <code>#N/A</code></b>（公式没取到数），
不是被谁写坏的。全部集中在「教务-张珊珊」一张表。</p>
<div class="note">动作：把格里的公式去掉、直接手填课程全名；
或先修好它引用的那几个格子，让公式能算出结果。</div>
{sheet_tables(B)}
</div>""")

    if O:
        H.append(f"""<h2>3. 其它非法课程名（{len(O)} 行）</h2>
<div class="card">{sheet_tables(O)}</div>""")

    alias_list_txt = "、".join(f"<code>{esc(k)}</code>" for k in aliases) or "（无）"
    H.append(f"""<h2>{4 if O else 3}. 已经修好的 —— 简写课程名（{len(C)} 行）</h2>
<div class="card">
<p style="margin-top:0">这 {len(C)} 行写的是<b>简写</b>（{alias_list_txt}），
已在团队配置里映射到后台全名 → <b>已归位，不用动</b>。列出来是让你核对一下映射对不对。</p>
{sheet_tables(C)}
</div>""")

    if D:
        H.append(f"""<h2>{5 if O else 4}. 模板残留行（{len(D)} 行）—— 不是真学生</h2>
<div class="card">
<p style="margin-top:0">这些行的<b>姓名列写着「学生」、课程列写着「课程」</b>，是模板/表头文字被当成了数据。
适配器已跳过（不计入任何统计），<b>不影响任何结果</b>。</p>
<div class="note">建议：顺手在飞书里删掉，免得以后又被当成一个「学生」。</div>
{sheet_tables(D)}
</div>""")

    chk = ""
    if only_meta or only_scan:
        chk = (f'<div class="note">⚠️ 交叉校验有差异：仅在适配器清单 {only_meta}；'
               f'仅在本页扫描 {only_scan}。请核对。</div>')
    else:
        chk = (f'<div class="note" style="background:#f4fbf7;border-color:#cdeadd;color:#1f5a3c">'
               f'✅ 交叉校验通过：本页扫描的 {len(scan_rows)} 行与适配器实际判定的清单'
               f'（feishuMeta.badCourses，{bc.get("total")} 行）<b>逐行一致</b>。</div>')

    alias_txt = "、".join(f"<code>{esc(k)}</code> → {esc(v)}" for k, v in aliases.items()) or "（无）"
    H.append(f"""<h2>{6 if O else 5}. 核对与追溯</h2>
<div class="card">{chk}
<p style="margin-bottom:0"><b>行号说明</b>：本页「飞书行号」= 飞书表格里左侧显示的行号（第 1 行是表头）。
若表中间插入过行，以<b>学生姓名</b>为准定位。<br>
<b>已配置的课程名映射</b>：{alias_txt}</p>
</div>
""")

    if sheet_level:
        rows = "".join(
            f'<tr><td class="nm">{esc(s["sheetTitle"])}</td>'
            f'<td><code>{esc(s["sheetId"])}</code></td>'
            f'<td class="c">{s["rows"]}</td>'
            f'<td>在 <code>profile.feishu.sheetProductMap</code> 里补 '
            f'<code>"{esc(s["sheetId"])}": "&lt;课程全名&gt;"</code></td></tr>'
            for s in sheet_level)
        H.append(f"""<h2>附 · 表级问题（{len(sheet_level)} 张表）</h2>
<div class="card">
<p style="margin-top:0">下列 sheet <b>没有「课程」列</b>、<b>sheet 名也不是课程全名</b>、
也没配 <code>sheetProductMap</code> → 整张表的学生都取不到课程名（**表级**问题，不是某几行）。
这类问题**改行没用**，要么把 sheet 名改成课程全名，要么加一条映射。</p>
<table><tr><th>sheet 名</th><th>sheetId</th><th>行数</th><th>怎么办</th></tr>
{rows}
</table></div>
""")

    H.append(f"""<div class="foot">
⚠️ 本页含学生真实姓名，仅供团队内部跟进使用，请勿外传。<br>
生成脚本 <code>scripts/fixlist_feishu.py</code> ·
数据源 <code>feishu_raw.json</code>（原始格）+ <code>profile.json</code>（列声明）+
<code>snapshot/feishuMeta.json</code>（适配器判定，用于交叉校验）·
清单从原始表<b>直接扫描</b>，并与适配器结论逐行交叉校验。
</div>
</div></body></html>""")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("".join(H))
    print(f"✅ 已生成 {out}")
    print(f"   补填 {len(A)} / 改值 {len(B)} / 其它非法 {len(O)} / 已修 {len(C)} / 模板行 {len(D)}")
    if sheet_level:
        print(f"   ⚠️ 表级问题 {len(sheet_level)} 张表: "
              + ", ".join(f"{s['sheetTitle']}({s['sheetId']})" for s in sheet_level))
    print(f"   交叉校验: 一致={not (only_meta or only_scan)}"
          + (f"  仅适配器={only_meta}  仅本页={only_scan}" if (only_meta or only_scan) else ""))


if __name__ == "__main__":
    main()
