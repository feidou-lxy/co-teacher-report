"""把 problems.py 的问题目录导出成「给联席填措施」的清单（HTML + TXT）。

用法：
    python3 export_problems.py                  # 输出到 ~/Desktop
    python3 export_problems.py --out /tmp       # 指定目录

为什么用脚本导出而不是手写清单：
  之前 v1 是手写的，一旦代码里加了/改了一个标签，桌面清单就和报告对不上。
  现在清单与报告**读同一份 problems.py**，改一处、导一次，两边永远一致。
"""

import argparse
import html as _html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import problems as pb


def md2html(t):
    """problems.py 里的文字带 **粗体** → <b>（转义后替换，安全）。"""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _html.escape(str(t or "")))


def md2txt(t):
    return re.sub(r"\*\*(.+?)\*\*", r"「\1」", str(t or ""))


def build_html(ver="v1"):
    rows, nav = [], []
    _rank = {k: i + 1 for i, k in enumerate(pb.PRIORITY)}
    _no = 0
    for cat, cname, cdesc in pb.CATS:
        items = pb.by_cat(cat)
        if not items:
            continue
        nav.append(f'<a href="#c-{cat}">{cname}<i>{len(items)}</i></a>')
        rows.append(f'<section class="cat" id="c-{cat}">'
                    f'<h2>{cname}<span class="cd">{cdesc}</span></h2>')
        for p in items:
            _no += 1
            rows.append(f'''
<div class="item">
  <div class="ih"><span class="lab"><i class="no">#{_no}</i>{md2html(p["label"])}</span>
    <span class="cnt">优先级 {_rank.get(p["k"], "-")}</span></div>
  <div class="line"><span class="k">怎么判出来</span><span class="v">{md2html(p["cond"])}</span></div>
  <div class="line"><span class="k">现在的措施</span><span class="v act">{md2html(p["act"])}</span></div>
  <div class="line"><span class="k">完成标准</span><span class="v done">{md2html(p["done"])}</span></div>
  <div class="fill"><span class="fk">你们的实际做法</span>
    <div class="w1"></div><div class="w2"></div>
  </div>
</div>''')
        rows.append('</section>')

    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>问题标签 · 措施补充清单 {ver}</title>
<style>
  *{{box-sizing:border-box}}
  body{{margin:0;padding:24px 26px 40px;background:#f5f6f8;color:#20242b;line-height:1.7;
       font-family:-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}}
  .page{{max-width:1000px;margin:0 auto}}
  h1{{font-size:22px;margin:0 0 6px}}
  .sub{{font-size:12.5px;color:#8a9099;margin-bottom:16px}}
  .intro{{background:#fff;border:1px solid #e6e9ee;border-radius:10px;padding:14px 18px;margin-bottom:16px}}
  .intro h2{{font-size:13.5px;margin:0 0 8px;color:#3d4550}}
  .intro ol{{margin:0;padding-left:20px;font-size:13px;color:#4a515c}}
  .intro li{{margin:4px 0}}
  .intro b{{color:#c0392b}}
  .nav{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:20px}}
  .nav a{{text-decoration:none;font-size:12.5px;color:#3a5bdb;background:#fff;border:1px solid #e6e9ee;
          border-radius:8px;padding:4px 9px}}
  .nav a i{{font-style:normal;color:#9aa1ab;margin-left:5px;font-size:11px}}
  .cat{{margin-bottom:26px}}
  .cat h2{{font-size:15px;margin:0 0 10px;padding-left:2px}}
  .cat .cd{{font-weight:400;font-size:12px;color:#8a9099;margin-left:8px}}
  .item{{background:#fff;border:1px solid #e6e9ee;border-radius:10px;padding:12px 16px 14px;margin-bottom:10px}}
  .ih{{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px}}
  .lab{{font-size:14px;font-weight:700}}
  .lab .no{{font-style:normal;font-size:11.5px;color:#9aa1ab;margin-right:7px}}
  .cnt{{font-size:11px;color:#b6bcc5}}
  .line{{display:flex;gap:10px;font-size:12.8px;margin:3px 0}}
  .line .k{{flex:0 0 68px;color:#9aa1ab;font-size:11.5px;padding-top:2px}}
  .line .v{{color:#4a515c}}
  .line .v.act{{color:#7c5c1e}}
  .line .v.done{{color:#2f6b46}}
  .fill{{margin-top:9px;border-top:1px dashed #dfe3e9;padding-top:8px}}
  .fk{{font-size:11.5px;color:#c0872b;font-weight:600}}
  .w1,.w2{{border-bottom:1px dashed #e6e9ee;height:26px}}
  .w2{{margin-top:6px}}
  .foot{{background:#fff;border:1px solid #e6e9ee;border-radius:10px;padding:14px 18px;font-size:12.8px;color:#4a515c}}
  .foot h3{{font-size:13px;margin:0 0 6px;color:#3d4550}}
  .foot .w{{border-bottom:1px dashed #dfe3e9;height:28px;margin-top:8px;color:#9aa1ab;font-size:12.5px;line-height:24px}}
  @page{{size:A4;margin:12mm}}
  @media print{{body{{background:#fff;padding:0}} .item,.intro,.foot{{break-inside:avoid}}
    .w1,.w2{{height:34px}}}}
</style></head>
<body><div class="page">
<h1>问题标签 · 措施补充清单</h1>
<div class="sub">{len(pb.CATS)} 类问题 · {len(pb.PROBLEMS)} 个标签 ｜ 每个标签对应一套措施，请改成你们真会做的那套</div>

<div class="intro">
  <h2>这份清单是怎么来的 / 怎么用</h2>
  <ol>
    <li>报告现在会给每个学生打<b>问题标签</b>（就是卡片上「互动『沉默不回复』」「作业评级 最近连续 3 次未提交」这些）。
        以前标签后面只跟一句通用话，落地不了；现在<b>每个标签配一套具体措施</b>，就是下面这份。</li>
    <li>措施我已经写了一版初稿（照着「谁·什么时候·做什么·怎么算完成」写的）。
        <b>你们不用从零写，只改不对的地方</b> —— 改一个词也行。</li>
    <li>改完发我，我直接改进系统：<b>以后报告里同类学生就出你们这套话</b>，不用再人工翻译一遍。</li>
    <li>如果觉得<b>标签本身判错了</b>（这学生其实不属于这一类），标一下 —— 那是判定规则的问题，比改措施更要紧。</li>
  </ol>
</div>

<div class="nav">{''.join(nav)}</div>

{''.join(rows)}

<div class="foot">
  <h3>组合情况（可选）</h3>
  <div>一个学生常常同时命中 2~3 个标签。报告现在会按优先级挑一个作为主动作，其余放在「另需处理」。
       如果你们有固定的组合打法，写在这里：</div>
  <div class="w">组合 1：</div>
  <div class="w">组合 2：</div>
  <div class="w">组合 3：</div>
  <h3 style="margin-top:14px">优先级对不对？</h3>
  <div>现在「谁先做」的顺序是：老师没回的提问 ＞ 最近连续未交 ＞ 成绩偏低 ＞ 能力型错误（实证错误）
       ＞ 参与度（白板空白）＞ 互动沉默 ＞ 节奏（没写完）。
       顺序不对也直接说 —— 我会改 <code>problems.py</code> 里的 PRIORITY。</div>
</div>
</div></body></html>'''


def build_txt(ver="v1"):
    L = [f"问题标签 · 措施补充清单（{ver}）",
         f"{len(pb.CATS)} 类问题 · {len(pb.PROBLEMS)} 个标签", "",
         "──────── 怎么用 ────────",
         "报告会给每个学生打「问题标签」（如「互动『沉默不回复』」「作业评级 最近连续 3 次未提交」）。",
         "以前标签后面只跟一句通用话，落地不了；现在每个标签配一套具体措施（下面就是）。",
         "措施我写了一版初稿，你们不用从零写，只改不对的地方 —— 改一个词也行。",
         "如果觉得标签本身判错了（这学生不属于这类），标一下，那比改措施更要紧。", ""]
    _rank = {k: i + 1 for i, k in enumerate(pb.PRIORITY)}
    _no = 0
    for cat, cname, cdesc in pb.CATS:
        items = pb.by_cat(cat)
        if not items:
            continue
        L.append("")
        L.append(f"══════ {cname}（{len(items)}）{cdesc} ══════")
        for p in items:
            _no += 1
            L.append("")
            L.append(f"#{_no} {md2txt(p['label'])}   （优先级 {_rank.get(p['k'], '-')}）")
            L.append(f"   怎么判出来：{md2txt(p['cond'])}")
            L.append(f"   现在的措施：{md2txt(p['act'])}")
            L.append(f"   完成标准：{md2txt(p['done'])}")
            L.append("   你们的实际做法：")
    L += ["", "", "══════ 组合情况（可选）══════",
          "一个学生常同时命中 2~3 个标签。报告现在按优先级挑一个作主动作，其余放「另需处理」。",
          "组合 1：", "组合 2：", "组合 3：", "",
          "══════ 优先级对不对 ══════",
          "现在「谁先做」：老师没回的提问 ＞ 最近连续未交 ＞ 成绩偏低 ＞ 能力型错误",
          "＞ 参与度（白板空白）＞ 互动沉默 ＞ 节奏（没写完）。顺序不对也直接说。"]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/Desktop"))
    ap.add_argument("--ver", default="v2")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    hp = os.path.join(a.out, f"问题标签措施清单_{a.ver}.html")
    tp = os.path.join(a.out, f"问题标签措施清单_{a.ver}.txt")
    open(hp, "w", encoding="utf-8").write(build_html(a.ver))
    open(tp, "w", encoding="utf-8").write(build_txt(a.ver))
    print(f"✅ {hp}\n✅ {tp}\n   共 {len(pb.PROBLEMS)} 个标签 / {len(pb.CATS)} 类问题")


if __name__ == "__main__":
    main()
