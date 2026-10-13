"""端到端编排：登录取得 token 后，依次跑步骤 1~10。

用法（在 workdir 内执行，token 已就位）：
  python scripts/run_all.py --co 张巍 --window 2026-09-01        # 联席视角
  python scripts/run_all.py --edu 罗绂燕 --window 2026-09-01     # 教务视角（按下属联席下钻）

前提：<workdir>/token.txt 已写入后台 x-token（见 SKILL.md 登录步骤）。
各步骤脚本位于本目录，依赖：Python3 + openpyxl（xlsx 步骤需要）。
"""
import argparse
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ensure_openpyxl, resolve_scope

HERE = os.path.dirname(os.path.abspath(__file__))


def run(py, extra):
    cmd = [sys.executable, os.path.join(HERE, py)] + extra
    print("\n>>>", " ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"{py} 失败（exit {r.returncode}），流程中断。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", default=None, help="联席姓名（联席视角 / 课程内子筛选）")
    ap.add_argument("--edu", default=None, help="教务姓名（教务视角）")
    ap.add_argument("--course", default=None, help="课程名（课程维度），如 高二数学清北层日E邵")
    ap.add_argument("--boards", default=None, help="小黑板图片根目录（提供后追加小黑板读图分析）")
    ap.add_argument("--window", default="2026-09-01")
    ap.add_argument("--month", default="2026-09")
    args = ap.parse_args()

    wd = args.workdir
    role, name = resolve_scope(args)
    ensure_openpyxl()
    base = ["--workdir", wd]
    if role == "course":
        base += ["--course", name]
        if args.co:                       # 课程内按联席子筛选（可选）
            base += ["--co", args.co]
    elif role == "edu":
        base += ["--edu", name]
    else:
        base += ["--co", name]
    if not os.path.exists(os.path.join(wd, "token.txt")):
        sys.exit(f"未找到 {wd}/token.txt，请先登录后台并写入 x-token。")

    run("pull_students.py", base)
    run("pull_groups.py", base + ["--month", args.month])
    run("pull_chats.py", base)
    run("pull_attendance.py", base)
    run("build_attendance.py", base)
    run("extract.py", base + ["--window", args.window])
    run("leave_notes.py", ["--workdir", wd, "--window", args.window])
    run("merge.py", ["--workdir", wd])
    run("report.py", base + ["--window", args.window])
    run("xlsx.py", base)
    if args.boards:
        run("pull_boards.py", base + ["--boards", args.boards])
        run("read_boards.py", base + ["--boards", args.boards])
        print(f"\n🎉 完成。分析：{wd}/{name}_学情分析.html ；待办：{wd}/{name}_学生待办清单.xlsx")
        print(f"   小黑板：boards_summary.json（读图结论）。若未配置视觉模型，请按 board_analysis_template.json 人工回填。")
    else:
        print(f"\n🎉 完成。分析：{wd}/{name}_学情分析.html ；待办：{wd}/{name}_学生待办清单.xlsx")


if __name__ == "__main__":
    main()
