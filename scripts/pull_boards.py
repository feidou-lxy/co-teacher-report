"""步骤 A：扫描小黑板图片目录，建立「学生 → 逐轮」索引 boards.json。

文件名规律：
  N_[W/Z]-姓名[数字].png
  - N ＝ 当轮序号（每轮会变，仅排序用，不要当成学生ID）
  - W / Z ＝ 仅分批上传标记，无教学含义（不当过关/未过关）
  - 姓名 ＝ 学生姓名，可稳定匹配花名册（尾部数字为系统后缀，归一化时去掉）
  - 小白板根目录层级（v3.8 明确）：
      第 1 层文件夹 = **一次课**（命名含上课日期，如 0906邵小黑板 / 0927邵清北打穿题）
      第 2 层文件夹 = **该次课中的一轮**作答（时间戳，如 小黑板_2026-09-06 19-27-57）
      第 3 层       = 图片（每人一张；**带红笔批改痕迹的是联席批改后的图**）
    故「课次」≠「轮次」：一次课下有多轮，轮次按第 2 层时间戳计。

输出 <workdir>/boards.json：
  { 归一化姓名: [ {round, lesson, lesson_date, date, path, raw_name, flag}, ... ] }
  - round = 第 2 层时间戳目录（一轮）
  - lesson = 第 1 层目录名（课次）；lesson_date = 从课次名里提取的日期 MM-DD

后续 read_boards.py 读图分析并按「课次 → 轮次」聚合，再交给 report.py 渲染。
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


def scan(root):
    """扫描小黑板根目录 → ({归一化姓名: [rec, ...]}, 图片总数)。

    抽成独立函数，便于 board_cache.py 复用（增量读图）。
    """
    boards = {}
    n_img = 0
    for dirpath, _dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        parts = rel.split(os.sep)
        lesson = parts[0] if len(parts) >= 1 else rel          # 第 1 层 = 课次
        date = lesson
        # 课次日期：从文件夹名前 4 位数字提取（0906 → 09-06；同一天可能有多个主题文件夹）
        _md = re.match(r"^(\d{2})(\d{2})", lesson)
        lesson_date = f"{_md.group(1)}-{_md.group(2)}" if _md else ""
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
                "lesson": lesson,
                "lesson_date": lesson_date,
                "date": date,
                "path": os.path.join(dirpath, f),
                "raw_name": name,
                "flag": flag or "",   # W / Z / 空（空白模板）
            }
            boards.setdefault(name, []).append(rec)

    for k in boards:
        boards[k].sort(key=lambda r: r["round"])
    return boards, n_img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--boards", required=True, help="小黑板图片根目录（从桌面拖入其绝对路径）")
    ap.add_argument("--course", default=None)
    ap.add_argument("--co", default=None)
    # v4.12：本步骤是纯本地扫图，与视角无关（course/co/edu 都接受、都不做过滤），
    # 但为保持与 run_all.py / 其它取数脚本「同一套 scope 参数」的一致，补上 --edu。
    ap.add_argument("--edu", default=None)
    args = ap.parse_args()

    root = os.path.expanduser(args.boards)
    if not os.path.isdir(root):
        sys.exit(f"未找到小黑板目录：{root}（请确认路径，必要时用绝对路径）")

    boards, n_img = scan(root)

    out = os.path.join(args.workdir, "boards.json")
    json.dump(boards, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✅ 小黑板索引完成｜ 图片 {n_img} 张 ｜ 学生 {len(boards)} 人 ｜ {out}")


if __name__ == "__main__":
    main()
