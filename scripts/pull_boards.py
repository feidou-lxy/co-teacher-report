"""步骤 A：扫描小黑板图片目录，建立「学生 → 逐轮」索引 boards.json。

文件名规律：
  N_[W/Z]-姓名[数字].png
  - N ＝ 当轮序号（每轮会变，仅排序用，不要当成学生ID）
  - W / Z ＝ 仅分批上传标记，无教学含义（不当过关/未过关）
  - 姓名 ＝ 学生姓名，可稳定匹配花名册（尾部数字为系统后缀，归一化时去掉）
  - 小白板根目录下按「日期 / 时间戳」分层，每个时间戳目录 = 一次抓拍/一题

输出 <workdir>/boards.json：
  { 归一化姓名: [ {round, date, path, raw_name, flag}, ... ] }

后续 read_boards.py 读图分析、merge 聚合，再交给 report / xlsx 渲染第⑤块。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

# 捕获：序号_ [（( W/Z )）]- 姓名 数字 .png
PAT = re.compile(r"^(\d+)_(?:[（(]?([WZ])[）)]?\s*-)?(.+?)(\d*)\.png$", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--boards", required=True, help="小黑板图片根目录（从桌面拖入其绝对路径）")
    ap.add_argument("--course", default=None)
    ap.add_argument("--co", default=None)
    args = ap.parse_args()

    root = os.path.expanduser(args.boards)
    if not os.path.isdir(root):
        sys.exit(f"未找到小黑板目录：{root}（请确认路径，必要时用绝对路径）")

    boards = {}
    n_img = 0
    for dirpath, _dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        date = rel.split(os.sep)[0] if os.sep in rel else rel
        for f in files:
            if not f.lower().endswith(".png"):
                continue
            m = PAT.match(f)
            if not m:
                continue
            _num, flag, name, _suf = m.groups()
            name = common.norm_name(name)
            if not name:
                continue
            n_img += 1
            rec = {
                "round": rel,
                "date": date,
                "path": os.path.join(dirpath, f),
                "raw_name": name,
                "flag": flag or "",   # W / Z / 空（空白模板）
            }
            boards.setdefault(name, []).append(rec)

    for k in boards:
        boards[k].sort(key=lambda r: r["round"])

    out = os.path.join(args.workdir, "boards.json")
    json.dump(boards, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✅ 小黑板索引完成｜ 图片 {n_img} 张 ｜ 学生 {len(boards)} 人 ｜ {out}")


if __name__ == "__main__":
    main()
