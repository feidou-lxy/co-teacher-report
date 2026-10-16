"""端到端编排：登录取得 token 后，依次跑完整链路。

用法（在 workdir 内执行，token 已就位）：
  # —— 新接口链路（v4.11，推荐；课程 / 联席维度）——
  python scripts/run_all.py --api --course 2026Q-SX-高二数学清北层日E邵 --window 2026-09-01
  python scripts/run_all.py --api --co 张巍 --window 2026-09-01
  python scripts/run_all.py --api --co 张巍 --fill        # 接口未关联到群的学生用群名补全

  # —— 旧链路（教务维度目前只有这条路）——
  python scripts/run_all.py --edu 罗绂燕 --window 2026-09-01
  python scripts/run_all.py --course 2026Q-SX-高二数学清北层日E邵 --boards <图片目录>

两条链路的差别**只在取花名册与折算快照这两步**，中间步骤完全相同：

  新（--api）：pull_roster.py ─┬─→ pull_chats / pull_attendance / … ─→ adapt_api.py
  旧（默认）  ：pull_students.py + pull_groups.py ─┘                        → adapt_legacy.py

v4.8 起新增两步（在 merge 与 report 之间）：
  adapt_*.py       把原始产物折算成「中间契约」快照（snapshot/）
  coverage.py      算覆盖率与四档预警 → coverage.json（报告 Tab① 依赖它）

前提：<workdir>/token.txt 已写入后台 x-token（见 SKILL.md 登录步骤）。
各步骤脚本位于本目录，依赖：Python3（v3.9 起不再产出 Excel，无需 openpyxl）。
"""
import argparse
import datetime
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from common import resolve_scope

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
    ap.add_argument("--edu", default=None, help="教务姓名（教务视角；⚠️ 暂无新接口）")
    ap.add_argument("--course", default=None, help="课程名（课程维度），如 2026Q-SX-高二数学清北层日E邵")
    ap.add_argument("--boards", default=None, help="小黑板图片根目录（提供后追加小黑板读图分析）")
    ap.add_argument("--window", default="2026-09-01")
    ap.add_argument("--end", default=None, help="报告窗口止（YYYY-MM-DD），默认今天")
    ap.add_argument("--month", default="2026-09", help="群列表窗口起始月 YYYY-MM")
    ap.add_argument("--chat-days", type=int, default=14,
                    help="失联预警的沟通轴窗口天数（默认近两周）")
    ap.add_argument("--api", action="store_true",
                    help="★ 用研发封装的「在读学生」接口取花名册与 roomId（课程 / 联席维度）")
    ap.add_argument("--fill", action="store_true",
                    help="（配合 --api）为接口未关联到群的学生，用群列表「群名内姓名」补一次 roomId")
    args = ap.parse_args()

    wd = args.workdir
    role, name = resolve_scope(args)
    end = args.end or datetime.date.today().isoformat()
    base = ["--workdir", wd]
    if role == "course":
        base += ["--course", name]
        if args.co:                       # 课程内按联席子筛选（可选）
            base += ["--co", args.co]
    elif role == "edu":
        base += ["--edu", name]
    else:
        base += ["--co", name]
    # 凭证自检：缺文件 / 内容不是有效 token 时，load_token 会给出「怎么取」的可照做提示
    common.load_token(wd)

    # ---------------- 阶段一：取花名册（两条链路在此分叉）----------------
    if args.api:
        if role == "edu":
            sys.exit("❌ 教务维度暂无对应接口，无法使用 --api。\n"
                     "   请去掉 --api 走旧链路（--edu），或请研发补 "
                     "GET /ai/getClassTeacherStudents?classTeacherName=<教务姓名>。")
        print("★ 新接口链路（研发封装的在读学生接口）")
        run("pull_roster.py", base + (["--fill", "--month", args.month] if args.fill else []))
    else:
        run("pull_students.py", base)
        run("pull_groups.py", base + ["--month", args.month])

    # ---------------- 阶段二：中间步骤（两链路完全相同，第 2/3 点沿用旧方式）----------------
    run("pull_chats.py", base)
    run("pull_attendance.py", base)
    run("build_attendance.py", base)
    run("extract.py", base + ["--window", args.window])
    run("leave_notes.py", ["--workdir", wd, "--window", args.window])
    run("merge.py", ["--workdir", wd])
    if args.boards:
        run("pull_boards.py", base + ["--boards", args.boards])
        run("read_boards.py", base + ["--boards", args.boards])

    # ---------------- 阶段三：折算快照 + 覆盖模型 + 报告 ----------------
    scope_flag = (["--course", name] if role == "course"
                  else (["--edu", name] if role == "edu" else ["--co", name]))
    adapter = "adapt_api.py" if args.api else "adapt_legacy.py"
    run(adapter, ["--workdir", wd] + scope_flag
        + ["--start", args.window, "--end", end])
    run("coverage.py", ["--workdir", wd, "--chat-days", str(args.chat_days)])
    run("report.py", base + ["--window", args.window])

    from common import course_name as _cn
    out = f"{wd}/{_cn(name) if role == 'course' else name}_学情分析.html"
    print(f"\n🎉 完成。分析报告：{out}（主交付）")
    print(f"   覆盖率与失联分档：{wd}/coverage.json")
    print(f"   中间契约快照：{wd}/snapshot/（source={'api' if args.api else 'legacy'}）")
    if args.boards:
        print(f"   小黑板：boards_summary.json（读图结论）。若未配置视觉模型，请按 board_analysis_template.json 人工回填。")


if __name__ == "__main__":
    main()
