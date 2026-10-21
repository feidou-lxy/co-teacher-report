"""workdir.py — 统一「固定工作目录」解析（v4.49）

为什么要有这个文件
------------------
以前每个报告都用一个临时目录（`_roundK/w_edu2`、`rpt_course_6cxiao`、
`rpt_test`…），带来四个实际麻烦：

1. **轻量刷新失效**：`refresh.py` 依赖 `snapshot/` 里的旧快照重渲染。
   目录一变，快照就没了，只能重跑全链路（3~8 分钟、250~400 次请求）。
2. **token 反复重取**：`token.txt` 存在目录里。新目录 = 没 token = 又要
   人工认一次图形验证码。而 token 有效期约 24 小时，本该能复用。
3. **同一对象的历史散落**：教务问「上次那份报告」，得在几十个目录里找。
4. **定时任务无处挂**：launchd 的 `WorkingDirectory` 得写死一个路径，
   目录每次都变的话，定时任务根本没法配。

所以约定：**同一个报告对象，永远用同一个目录**。

目录约定
--------
    ~/.workbuddy/reports/
        edu-罗绂燕/                       教务视角（续报/科目/扩科）
        co-张巍/                          联席视角（单老师全部学生）
        course-高二数学211层六C肖/         课程视角（课程下全体学生）
        course-高二数学清北层日E邵@邵清北/  课程视角 + 课程内按联席筛选

- 主名优先级：`course` > `edu` > `co`；`co` 与 `course` 同时给时，`co` 作 `@后缀`。
- 课程名会剥掉 `2026Q-SX-` 这类季度前缀（与 `common.course_name()` 同口径），
  否则目录名里塞一串编号，人根本认不出来。
- 目录名只保留中文/字母/数字/`-@_`，其它字符（`/ : * ? " < > |` 与空白）
  一律替换成 `_`，避免建目录失败或产生嵌套路径。

可覆盖
------
    环境变量 CTR_REPORTS_HOME   改根目录（例如放到 iCloud 盘或共享盘）

用法
----
    from workdir import resolve
    wd = resolve(edu="罗绂燕")                    # -> ~/.workbuddy/reports/edu-罗绂燕
    wd = resolve(co="张巍", course="高二数学211层六C肖")
    wd = resolve(...)                              # 目录不存在会自动创建

命令行自检：
    python3 scripts/workdir.py --edu 罗绂燕
    python3 scripts/workdir.py --co 张巍 --course 2026Q-SX-高二数学211层六C肖
"""
import os
import re
import sys

DEFAULT_HOME = os.path.join(os.path.expanduser("~"), ".workbuddy", "reports")

# 与 common.course_name() 同口径：剥季度课程编号前缀与括号状态后缀
_COURSE_PREFIX = re.compile(r"^\d{4}Q\d*-?[A-Z]{0,3}-?")
_COURSE_TAIL = re.compile(r"[（(][^）)]*[）)]\s*$")
_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')

# 这些词单独出现时不能作为目录名主体（防止把「全部学生」写进路径）
_PLACEHOLDERS = {"", "none", "null", "全部", "全部学生", "所有", "-", "—"}


def home():
    """报告根目录。可用 CTR_REPORTS_HOME 覆盖（备份盘 / 共享盘 / iCloud）。"""
    p = (os.environ.get("CTR_REPORTS_HOME") or "").strip()
    return os.path.abspath(os.path.expanduser(p)) if p else DEFAULT_HOME


def clean(s):
    """把任意名字清洗成安全的目录名片段。"""
    s = (s or "").strip()
    s = _UNSAFE.sub("_", s).strip("_.")
    return s[:60] if len(s) > 60 else s


def course_name(course):
    """剥掉 2026Q-SX- 前缀与末尾括号（与 common.course_name 保持一致）。"""
    s = (course or "").strip()
    s = _COURSE_PREFIX.sub("", s)
    s = _COURSE_TAIL.sub("", s)
    return clean(s)


def dirname(edu=None, co=None, course=None):
    """按视角算出目录名（纯函数，不碰文件系统）。没有可用主体时返回 None。"""
    edu, co = clean(edu), clean(co)
    edu = "" if edu.lower() in _PLACEHOLDERS else edu
    cn = course_name(course)
    co = "" if co.lower() in _PLACEHOLDERS else co

    if cn:
        return f"course-{cn}@{co}" if co else f"course-{cn}"
    if edu:
        return f"edu-{edu}"
    if co:
        return f"co-{co}"
    return None


def resolve(edu=None, co=None, course=None, create=True):
    """返回固定工作目录的绝对路径；目录不存在则创建。

    三者都没给时返回 None —— 由调用方决定是报错还是退回当前目录，
    不在这里替调用方做主（`report.py` 之类的底层脚本允许 `--workdir .`）。
    """
    name = dirname(edu=edu, co=co, course=course)
    if not name:
        return None
    path = os.path.join(home(), name)
    if create:
        os.makedirs(os.path.join(path, "snapshot"), exist_ok=True)
    return path


def describe(path):
    """给日志用的一句人话（相对路径更短，绝对路径更准）。"""
    try:
        rel = os.path.relpath(path, os.path.expanduser("~"))
        return "~/" + rel if not rel.startswith("..") else path
    except Exception:
        return path


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="打印固定工作目录（不创建时加 --dry-run）")
    ap.add_argument("--edu", default=None, help="教务姓名")
    ap.add_argument("--co", default=None, help="联席姓名")
    ap.add_argument("--course", default=None, help="课程名（可带 2026Q-SX- 前缀）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不创建目录")
    ap.add_argument("--list", action="store_true", help="列出已有报告目录")
    a = ap.parse_args(argv)

    if a.list:
        root = home()
        if not os.path.isdir(root):
            print(f"（尚无报告目录：{describe(root)}）")
            return 0
        for d in sorted(os.listdir(root)):
            p = os.path.join(root, d)
            if not os.path.isdir(p):
                continue
            snap = os.path.join(p, "snapshot")
            n = len([f for f in os.listdir(snap)]) if os.path.isdir(snap) else 0
            tok = "有token" if os.path.exists(os.path.join(p, "token.txt")) else "无token"
            print(f"  {d:<44} 快照 {n:>2} 个 ｜ {tok}")
        return 0

    p = resolve(edu=a.edu, co=a.co, course=a.course, create=not a.dry_run)
    if not p:
        print("❌ 至少要给 --edu / --co / --course 之一")
        return 1
    print(p)
    print(f"   （人话：{describe(p)}）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
