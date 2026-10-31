"""端到端编排：登录取得 token 后，依次跑完整链路。

★ 工作目录（v4.49 起）：**不用传 `--workdir`**。脚本按视角自动定位到固定目录
  `~/.workbuddy/reports/<视角>-<姓名>[@课程]`（`scripts/workdir.py` 同一套规则），
  这样 token 与 snapshot 能跨次复用，之后的 `refresh.py` 也有旧快照可用。
  只有需要临时目录时才显式传 `--workdir <path>`。

用法：
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

v4.65 起两头各加一道「成绩防线」（防的是「11/150」这种把日期/时间/题号当分数的反向误判）：
  开跑前  test_exam.py   25 条回归用例，红了直接中断（--skip-exam-guard 可跳过）
  出报告后 audit_exam.py 把每条成绩 + 原话列成 exam_audit.txt，供人眼核验

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
import workdir
from common import resolve_scope

HERE = os.path.dirname(os.path.abspath(__file__))

# v4.66：本次运行的状态。收尾时写 `run_log.jsonl`（本地记账）并据此决定上报 event。
#   起因：使用统计原来固定上报 "run"，中途失败也一样 —— 事后无法回答
#   「到底成功生成了多少份可用报告」。
_RUN = {"wd": ".", "role": "", "name": "", "window": "", "end": "", "report": "", "ok": False}


def _log_run():
    """把本次运行结果追加到 <workdir>/run_log.jsonl（一行一次，只追加）。"""
    import json as _json
    try:
        rec = {"at": datetime.datetime.now().isoformat(timespec="seconds"),
               "ok": bool(_RUN["ok"]), "event": "run" if _RUN["ok"] else "run_failed",
               "role": _RUN["role"], "name": _RUN["name"],
               "window": _RUN["window"], "end": _RUN["end"],
               "report": _RUN["report"], "argv": sys.argv[1:]}
        with open(os.path.join(_RUN["wd"], "run_log.jsonl"), "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def run(py, extra):
    cmd = [sys.executable, os.path.join(HERE, py)] + extra
    print("\n>>>", " ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"{py} 失败（exit {r.returncode}），流程中断。")


def run_tee(py, extra, save_to):
    """跑一步、把输出同时打到屏幕与文件（v4.65：成绩审计清单要留档给人眼过）。"""
    cmd = [sys.executable, os.path.join(HERE, py)] + extra
    print("\n>>>", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    print(out)
    if save_to:
        try:
            with open(save_to, "w", encoding="utf-8") as f:
                f.write(out)
        except OSError as e:
            print(f"[warn] 审计清单写盘失败：{e}")
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=None,
                    help="报告工作目录。★默认不传＝按 --edu/--co/--course 自动定位到固定目录 "
                         "~/.workbuddy/reports/<视角>-<姓名>[@@课程]，便于复用 token 与快照；"
                         "仅在需要临时目录时才显式传路径")
    ap.add_argument("--co", default=None, help="联席姓名（联席视角 / 课程内子筛选）")
    ap.add_argument("--edu", default=None, help="教务姓名（教务视角；续报 renewed 由 pull_enroll.py --edu 从接口拉取）")
    ap.add_argument("--course", default=None, help="课程名（课程维度），如 2026Q-SX-高二数学清北层日E邵")
    ap.add_argument("--boards", default=None, help="小黑板图片根目录（提供后追加小黑板读图分析）")
    ap.add_argument("--window", default="2026-09-01")
    ap.add_argument("--end", default=None,
                    help="分析窗口止（YYYY-MM-DD，含当天），默认今天；"
                         "给了早于今天的日期即出「时间段」报告（v4.16，聊天分析与沟通轴锚点一并卡到该日）")
    ap.add_argument("--month", default="2026-09", help="群列表窗口起始月 YYYY-MM")
    ap.add_argument("--chat-days", type=int, default=14,
                    help="失联预警的沟通轴窗口天数（默认近两周）")
    ap.add_argument("--api", action="store_true",
                    help="★ 用研发封装的「在读学生」接口取花名册与 roomId（课程 / 联席维度）")
    ap.add_argument("--fill", action="store_true",
                    help="（配合 --api）为接口未关联到群的学生，用群列表「群名内姓名」补一次 roomId")
    ap.add_argument("--feishu", action="store_true",
                    help="★ 先拉飞书表（pull_feishu.py → adapt_feishu.py）再出报告（v4.66）。"
                         "不加时报告只会沿用工作目录里**上一次**的飞书快照，并会在报告顶部标注采集时间")
    ap.add_argument("--skip-exam-guard", action="store_true",
                    help="跳过开跑前的「成绩识别回归测试」闸门（v4.65）。"
                         "仅在明确知道自己在做什么时使用（如只重渲染报告、离线补数据）")
    args = ap.parse_args()

    # 固定工作目录（v4.49）：不显式给 --workdir 时，按视角自动定位到
    # ~/.workbuddy/reports/<视角>-<姓名>[@课程]，这样 token 与 snapshot 能跨次复用，
    # refresh.py 也有旧快照可用。三种视角都没给才退回当前目录并提示。
    wd = args.workdir
    if not wd:
        wd = workdir.resolve(edu=args.edu, co=args.co, course=args.course)
        if wd:
            print(f"[工作目录] {workdir.describe(wd)}")
        else:
            wd = "."
            print("[工作目录] 未指定视角，退回当前目录；建议加 --edu / --co / --course 以复用快照与 token")
    else:
        wd = os.path.abspath(os.path.expanduser(wd))
    os.makedirs(os.path.join(wd, "snapshot"), exist_ok=True)
    role, name = resolve_scope(args)
    end = args.end or datetime.date.today().isoformat()
    _RUN.update(wd=wd, role=role, name=name, window=args.window, end=end)
    base = ["--workdir", wd]
    if role == "course":
        base += ["--course", name]
        if args.co:                       # 课程内按联席子筛选（可选）
            base += ["--co", args.co]
    elif role == "edu":
        base += ["--edu", name]
    else:
        base += ["--co", name]
    # ---------------- 阶段零：成绩识别回归闸门（v4.65 · 第②道防线，自动执行）----------------
    # 放在最前面：25 条纯字符串用例跑完不到 0.1 秒。一旦规则被改坏，宁可此刻立刻失败，
    # 也不要拉完 3 万条群聊、出完整份报告，才发现 13 个学生被打成「10/150 = 7%」。
    if not args.skip_exam_guard:
        run("test_exam.py", [])
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
    # v4.16：用户指定了「时间段止」(--end) 时，把上界一路传下去 —— 否则聊天分析会溢出到窗口止之后
    #        （例：出 9 月报告，10 月的聊天也会算进去）。未指定时行为与旧版完全一致。
    win_args = ["--window", args.window] + (["--end", end] if args.end else [])
    run("pull_chats.py", base)
    run("pull_attendance.py", base)
    run("build_attendance.py", base)
    run("extract.py", base + win_args)
    run("leave_notes.py", ["--workdir", wd] + win_args)
    run("merge.py", ["--workdir", wd])
    if args.boards:
        run("pull_boards.py", base + ["--boards", args.boards])
        run("read_boards.py", base + ["--boards", args.boards])

    # ---------------- 飞书表（v4.66：并入主链路，需显式 --feishu）----------------
    # 为什么以前不并：pull_feishu 依赖 lark-cli 授权，单次要跑几分钟，不适合默认执行。
    # 但不并的后果是「报告静默沿用旧快照」—— 多人复用目录时旧数据混进新报告且看不出来。
    # 折中：显式 --feishu 才拉；不拉时 report.py 的「数据体检」会把快照时间写到报告顶部。
    if args.feishu:
        _prof = os.path.join(wd, "profile.json")
        if not os.path.exists(_prof):
            sys.exit(f"❌ --feishu 需要 {_prof}（飞书表配置）。请先配好 profile.json，"
                     "或去掉 --feishu（报告将标注飞书数据为历史快照）。")
        run("pull_feishu.py", ["--workdir", wd])
        run("adapt_feishu.py", ["--workdir", wd])

    # ---------------- 阶段三：折算快照 + 覆盖模型 + 报告 ----------------
    scope_flag = (["--course", name] if role == "course"
                  else (["--edu", name] if role == "edu" else ["--co", name]))
    adapter = "adapt_api.py" if args.api else "adapt_legacy.py"
    run(adapter, ["--workdir", wd] + scope_flag
        + ["--start", args.window, "--end", end])
    run("coverage.py", ["--workdir", wd, "--chat-days", str(args.chat_days)])
    run("report.py", base + win_args)

    from common import course_name as _cn
    out = f"{wd}/{_cn(name) if role == 'course' else name}_学情分析.html"
    _RUN["report"] = out
    print(f"\n🎉 完成。分析报告：{out}（主交付）")
    print(f"   覆盖率与失联分档：{wd}/coverage.json")
    print(f"   中间契约快照：{wd}/snapshot/（source={'api' if args.api else 'legacy'}）")
    if args.boards:
        print(f"   小黑板：boards_summary.json（读图结论）。若未配置视觉模型，请按 board_analysis_template.json 人工回填。")

    # ---------------- 阶段四：成绩审计清单（v4.65 · 第③道防线）----------------
    # 前两道（规则排除 / 回归测试）都挡不住「没想到的形态」——v4.64 的 10:00 就是这么漏的，
    # 当时自写的「自动扫疑似误判」跑了两次都报 0 残留。能信的只有原话本身，所以必须列出来人眼看。
    audit_path = os.path.join(wd, "exam_audit.txt")
    if run_tee("audit_exam.py", ["--workdir", wd], audit_path) == 0:
        print("\n" + "=" * 72)
        print("🔎 第③道防线（必须人眼过）：成绩审计清单 →", audit_path)
        print("   逐条对照「原话」确认里面真的有分数：日期 / 题号 / 时间 / 班级平均 都不算。")
        print("   发现假成绩 → 先往 scripts/test_exam.py 补这条用例，再改 common.py 规则，然后重跑。")
        print("   （每份报告通常只有个位数学生命中，半分钟能看完）")
        print("=" * 72)
    else:
        print("\n" + "!" * 72)
        print("⚠️ 第③道防线没跑成 —— 这不等于「没问题」，而是「没检查」。")
        print(f"   检查 {audit_path} 是否生成、工作目录里有没有 chats.json，然后手工重跑：")
        print(f"   python3 scripts/audit_exam.py --workdir {wd}")
        print("!" * 72)


if __name__ == "__main__":
    # 使用统计：本进程负责本次执行的唯一一次上报（子进程继承认领标记后自动跳过），
    # 收尾时再真正发送。任何异常都不影响主流程。
    try:
        import usage_report as _ur
        _ur.claim()
    except Exception:
        _ur = None
    _ok = False
    try:
        main()
        _ok = True
    except SystemExit as e:                 # sys.exit(...) 的中断：code 非 0 即失败
        _ok = (e.code in (0, None))
        raise
    finally:
        _RUN["ok"] = _ok
        _log_run()
        if _ur is not None:
            try:
                _ur.report_once(_ur.workdir_from_argv(), force=True,
                                event="run" if _ok else "run_failed")
            except Exception:
                pass
