"""refresh.py — 报告轻量刷新（方案 A：只刷续报，不重拉群聊）

背景（2026-10-21 用户拍板）：
    「学生情况概览」要能小时级更新，但**不能**为此重跑全链路 —— 全链路要拉
    250~400 次群聊请求、耗时 3~8 分钟，而且每次都要 agent 读一遍 126KB 的
    SKILL.md，token 消耗大。教务真正会变、且需要频繁看的只有「续报状态」。
    本脚本把刷新压缩成：N 次续报接口（教务 = 下属联席数）+ 本地重渲染。

做三件事：
    1. 拉 renewed（复用 pull_enroll._pull_renew_from_api，含教务名→联席展开、
       按 roster 白名单过滤、按 studentSeq 去重）
    2. 就地更新 snapshot/enroll.json 的 renew / renewDetail（**不动 bySeq**，
       在读科目与扩科意向保持原样，避免引入未经验证的变化）
    3. 用已有快照重渲染 HTML（复用 report.py；不拉群聊、不跑 extract）

设计给「定时任务」用：
    本脚本不需要 AI 参与，crontab / launchd 直接调用即可，模型 token 消耗为 0。
    例：0 */4 * * * cd <workdir> && <python> <skill>/scripts/refresh.py --workdir . --quiet

安全铁律：
    - token 失效或取数为空 → **不写盘、不渲染**，退出码 2。
      绝不能把空 renew 覆盖上去（那会把整份续报 KPI 清成 0）。
    - 接口只返回部分学生时，**未出现的保留旧值**（合并而非覆盖），避免因接口
      偶发缺数据而把「已续报」误降级成「未确认」。
    - 写盘用临时文件 + os.replace 原子替换，中途失败不产生半截 JSON。
    - 渲染失败不删除旧 HTML。

用法：
    # 教务视角（参数可从 snapshot/meta.json 自动推断）
    python3 scripts/refresh.py --workdir <wd> --edu 罗绂燕

    # 只看这一小时有没有变化，不写盘不渲染
    python3 scripts/refresh.py --workdir <wd> --dry-run

    # 刷新后同步到交付目录
    python3 scripts/refresh.py --workdir <wd> --copy-to ../../最终产物/罗绂燕_教务_学生学情分析.html

退出码：0 成功 ｜ 1 参数/环境错误 ｜ 2 token 失效或取数失败（未写盘）｜ 3 渲染失败
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import common                                  # noqa: E402
import pull_enroll                             # noqa: E402
import workdir                                 # noqa: E402

RENEW_YES, RENEW_NO, RENEW_WAIT = "是", "否", "未设置"
ARGS_FILE = os.path.join("snapshot", "report_args.json")
NOTIFY_STATE = os.path.join("snapshot", ".refresh_notify.json")


# ------------------------------------------------------------------ 小工具
def notify(kind, title, msg, wd=".", enabled=False):
    """macOS 桌面通知（同类问题每天最多一次）。

    定时任务是无人值守的：token 过期后它会一直失败，如果没人看到日志，
    报告就停在旧数据上而使用者不知道。所以失败要主动弹一次通知。
    """
    if not enabled or sys.platform != "darwin":
        return
    today = dt.date.today().isoformat()
    p = os.path.join(wd, NOTIFY_STATE)
    st = read_json(p, {}) or {}
    if (st.get(kind) or "") == today:
        return
    try:
        script = ('display notification "%s" with title "%s" sound name "Glass"'
                  % (msg.replace('"', "'"), title.replace('"', "'")))
        subprocess.run(["osascript", "-e", script], timeout=8,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    st[kind] = today
    try:
        write_json_atomic(p, st)
    except Exception:
        pass
def log(msg, quiet=False):
    if not quiet:
        print(msg, flush=True)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json_atomic(path, obj):
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


# ------------------------------------------------------------ 参数推断
def infer_args(wd, cli):
    """渲染参数的优先级：命令行 > snapshot/report_args.json > snapshot/meta.json。

    首次在旧工作目录上跑时没有 report_args.json，靠 meta.json 的 scope/window
    推断（scope.type/value 就是当初的 --edu/--co/--course，window.startDate 是 --window）。
    """
    saved = read_json(os.path.join(wd, ARGS_FILE), {}) or {}
    meta = read_json(os.path.join(wd, "snapshot", "meta.json"), {}) or {}
    scope = meta.get("scope") or {}
    win = meta.get("window") or {}

    a = {
        "edu": None, "co": None, "course": None,
        "window": win.get("startDate") or "2026-09-01",
        "end": None,                      # None = 至今（每次刷新自动推进）
        "chat_days": saved.get("chat_days", 14),
        "note": saved.get("note"),
    }
    t = scope.get("type")
    if t == "edu":
        a["edu"] = scope.get("value")
    elif t == "course":
        a["course"] = scope.get("value")
    else:
        a["co"] = scope.get("value")
    # 记录里的值覆盖推断值（同一 workdir 后来改过参数时以记录为准）
    for k in ("edu", "co", "course", "window", "chat_days", "note"):
        if saved.get(k):
            a[k] = saved[k]
    # 命令行最终覆盖
    for k in ("edu", "co", "course", "window", "end", "chat_days", "note"):
        if getattr(cli, k, None) not in (None, ""):
            a[k] = getattr(cli, k)
    return a


# -------------------------------------------------------------- token 探活
def probe_token(wd):
    """先用最轻的字典接口确认 token 还活着。返回 (ok, 原因)。

    为什么不直接靠续报接口判断：_pull_renew_from_api 会把单次失败吞掉、
    全部失败时只返回空 dict，无法区分「token 过期」和「确实 0 条」。
    """
    try:
        token = common.load_token(wd)
    except Exception as e:
        return False, f"token.txt 读取失败（{type(e).__name__}: {e}）"
    if not token:
        return False, "token.txt 为空"
    try:
        d = common.get("/sysDictionary/findSysDictionary?type=productType", token)
    except Exception as e:
        return False, f"探活请求异常（{type(e).__name__}: {e}）"
    if d.get("code") != 0:
        return False, f"探活返回 code={d.get('code')} msg={d.get('msg')}"
    return True, ""


# ------------------------------------------------------------------ 统计
def tally(renew):
    yes = sum(1 for v in renew.values() if str(v).strip() == RENEW_YES)
    no = sum(1 for v in renew.values() if str(v).strip() == RENEW_NO)
    wait = len(renew) - yes - no
    return {"yes": yes, "no": no, "wait": wait, "total": len(renew)}


def diff_renew(old, new):
    """返回 (changed, added, filled)。filled 是新拿到、旧里没有的。"""
    changed, added = {}, {}
    for seq, v in new.items():
        if seq in old:
            if str(old[seq]).strip() != str(v).strip():
                changed[seq] = (old[seq], v)
        else:
            added[seq] = v
    return changed, added


# ------------------------------------------------------- 报告 KPI（与界面同口径）
_KPI_ORDER = (("yes", "已续报"), ("no", "未续报"), ("wait", "续报未确认"),
              ("multi", "多科目"), ("single", "单科"), ("kuake", "扩科意向"))


def html_kpis(html_path):
    """从渲染出的 HTML 里读 6 张 KPI 的显示值。

    为什么不直接用 renew 字典统计：接口记录口径（237 条，含报告范围外的学生）
    与报告 KPI 口径（只统计进报告的学生，144）不同，直接打印会让人对不上号。
    """
    if not html_path or not os.path.exists(html_path):
        return {}
    try:
        s = open(html_path, encoding="utf-8").read()
    except Exception:
        return {}
    out = {}
    for m in re.finditer(r'id="edun-(yes|no|wait|multi|single|kuake)"[^>]*>([^<]*)<', s):
        out[m.group(1)] = m.group(2).strip()
    return out


def latest_html(wd):
    """workdir 下最近生成的 *_学情分析.html（渲染会覆盖它，先抓旧 KPI 做对比）。"""
    cands = [os.path.join(wd, f) for f in os.listdir(wd) if f.endswith("_学情分析.html")]
    return max(cands, key=os.path.getmtime) if cands else None


def fmt_kpis(before, after):
    if not after:
        return ""
    parts = []
    for key, label in _KPI_ORDER:
        if key not in after:
            continue
        if before and key in before and before[key] != after[key]:
            parts.append(f"{label} {before[key]}→{after[key]}")
        else:
            parts.append(f"{label} {after[key]}")
    return " ｜ ".join(parts)


# ------------------------------------------------------------------ 渲染
def render(wd, a, quiet=False):
    """调 report.py 重渲染。返回 (ok, html_path, 失败原因)。"""
    cmd = [sys.executable, os.path.join(HERE, "report.py"), "--workdir", wd]
    if a["edu"]:
        cmd += ["--edu", str(a["edu"])]
    elif a["course"]:
        cmd += ["--course", str(a["course"])]
    elif a["co"]:
        cmd += ["--co", str(a["co"])]
    else:
        return False, None, "三个视角参数都为空，无法确定报告类型"

    cmd += ["--window", str(a["window"]), "--chat-days", str(a["chat_days"])]
    if a.get("end"):
        cmd += ["--end", str(a["end"])]
    if a.get("note"):
        cmd += ["--note", str(a["note"])]

    log(f"   重渲染：report.py {' '.join(cmd[2:])}", quiet)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-6:]
        return False, None, "report.py 退出码 %d：%s" % (r.returncode, " | ".join(tail))

    name = a["edu"] or a["co"] or a["course"]
    if a["course"]:
        try:
            name = common.course_name(name)
        except Exception:
            pass
    html = os.path.join(wd, f"{name}_学情分析.html")
    if not os.path.exists(html):
        return False, None, f"渲染完成但找不到输出文件 {html}"
    return True, html, ""


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="轻量刷新报告的「学生情况概览」（只刷续报，不重拉群聊）")
    ap.add_argument("--workdir", default=None,
                    help="报告工作目录。★默认不传＝按 --edu/--co/--course 自动定位到固定目录 "
                         "~/.workbuddy/reports/<视角>-<姓名>；显式传路径则按路径走（兼容旧用法）")
    ap.add_argument("--edu", default=None, help="教务姓名（不传则从 meta.json 推断）")
    ap.add_argument("--co", default=None, help="联席姓名")
    ap.add_argument("--course", default=None, help="课程名")
    ap.add_argument("--window", default=None, help="分析窗口起始（不传则沿用上次）")
    ap.add_argument("--end", default=None, help="窗口结束（不传=至今，每次刷新自动推进）")
    ap.add_argument("--chat-days", type=int, default=None, help="失联预警沟通轴天数（不传则沿用上次）")
    ap.add_argument("--note", default=None, help="报告顶部横幅（不传则沿用上次）")
    ap.add_argument("--dry-run", action="store_true", help="只拉数据看差异，不写盘不渲染")
    ap.add_argument("--no-render", action="store_true", help="只更新 enroll.json，不重渲染")
    ap.add_argument("--copy-to", default=None, help="渲染后把 HTML 复制到该路径（交付目录同步）")
    ap.add_argument("--notify", action="store_true",
                    help="失败时发 macOS 桌面通知（token 失效 / 渲染失败；同类问题每天最多一次）")
    ap.add_argument("--quiet", action="store_true", help="只输出错误与变化摘要（给定时任务用）")
    args = ap.parse_args()

    # 固定工作目录（v4.49）：不显式给 --workdir 时按视角定位，和 run_all.py 同一套规则，
    # 这样「全量一次 → 之后天天刷新」始终落在同一个目录里。
    if args.workdir:
        wd = os.path.abspath(os.path.expanduser(args.workdir))
    else:
        wd = workdir.resolve(edu=args.edu, co=args.co, course=args.course, create=False)
        if not wd:
            print("❌ 请给 --edu / --co / --course 之一（用于定位固定报告目录），"
                  "或用 --workdir 显式指定目录")
            return 1
    if not os.path.isdir(wd):
        print(f"❌ 工作目录还没建过：{workdir.describe(wd)}")
        print("   说明该对象还没跑过完整流程。先跑一次全量（run_all.py），之后才能用轻量刷新。")
        return 1
    enroll_path = os.path.join(wd, "snapshot", "enroll.json")
    if not os.path.exists(enroll_path):
        print(f"❌ 缺少快照：{workdir.describe(wd)}/snapshot/enroll.json")
        print("   轻量刷新依赖首次全量跑出来的快照；请先跑一次完整流程（run_all.py）。")
        return 1
    log(f"   目录：{workdir.describe(wd)}", args.quiet)

    a = infer_args(wd, args)
    who = a["edu"] or a["co"] or a["course"]
    if not who:
        print("❌ 无法确定视角：meta.json 里没有 scope，且未传 --edu/--co/--course")
        return 1

    log(f"🔁 轻量刷新 · {who}", args.quiet)
    log(f"   窗口：{a['window']} ~ {a['end'] or '至今'} ｜ 沟通轴 {a['chat_days']} 天", args.quiet)

    # 1) token 探活 —— 失效就立刻停，绝不动数据
    ok, why = probe_token(wd)
    if not ok:
        print(f"❌ token 不可用（{why}）")
        print("   请重新登录取 token 写入 token.txt 后再刷新；本次未改动任何文件。")
        notify("token", "学情报告刷新已暂停",
               f"{who} 的报告刷新失败：登录凭证已过期，重登后自动恢复", wd, args.notify)
        return 2

    # 2) 拉续报
    old_enroll = read_json(enroll_path, {}) or {}
    old_renew = {str(k): v for k, v in (old_enroll.get("renew") or {}).items()}

    online = None
    if a["edu"] or a["co"]:
        try:
            online = pull_enroll._pull_renew_from_api(wd, who)
        except Exception as e:
            print(f"❌ 续报接口调用失败（{type(e).__name__}: {e}），本次未改动任何文件。")
            return 2

    if online is None:
        log("ℹ️ 课程视角没有续报字段可刷，只做重渲染。", args.quiet)
        new_renew, detail = dict(old_renew), {}
    else:
        new_renew, detail = (online if isinstance(online, tuple) else (online, {}))
        if not new_renew:
            print("❌ 续报接口返回 0 条，判定为取数失败（token 权限/接口异常），本次未改动任何文件。")
            notify("empty", "学情报告刷新失败",
                   f"{who} 的续报接口返回 0 条，本次刷新已跳过", wd, args.notify)
            return 2

    new_renew = {str(k): v for k, v in (new_renew or {}).items()}
    merged = dict(old_renew)
    merged.update(new_renew)                  # 未返回的保留旧值

    changed, added = diff_renew(old_renew, new_renew)
    t_old, t_new = tally(old_renew), tally(merged)
    log(f"   接口记录 {len(merged)} 条 ｜ 已续报 {t_old['yes']} → {t_new['yes']} ｜ "
        f"未续报 {t_old['no']} → {t_new['no']} ｜ 未确认 {t_old['wait']} → {t_new['wait']}"
        f"（原始口径；报告 KPI 只统计进报告的学生，数字会更小）", args.quiet)
    if changed:
        log(f"   ⚡ 本次变化 {len(changed)} 人：", args.quiet)
        for seq, (o, n) in list(changed.items())[:20]:
            log(f"      {seq}: {o} → {n}", args.quiet)
    if added:
        log(f"   ＋ 新增记录 {len(added)} 条（旧快照里没有这些学生）", args.quiet)
    if not changed and not added:
        log("   （无变化）", args.quiet)

    if args.dry_run:
        log("🧪 dry-run：不写盘、不渲染。", args.quiet)
        return 0

    # 3) 写回 enroll.json（bySeq 不动）
    old_enroll["renew"] = merged
    if detail:
        rd = dict(old_enroll.get("renewDetail") or {})
        rd.update(detail)
        old_enroll["renewDetail"] = rd
    old_enroll["renewSource"] = "getCoTeacherStudents（renewed 字段，refresh.py 轻量刷新）"
    old_enroll["refreshedAt"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    write_json_atomic(enroll_path, old_enroll)
    log(f"   ✅ 已更新 {os.path.relpath(enroll_path, wd)}（bySeq 未动）", args.quiet)

    # 4) 记住本次参数，供下次定时刷新沿用
    write_json_atomic(os.path.join(wd, ARGS_FILE), {k: a[k] for k in
                      ("edu", "co", "course", "window", "end", "chat_days", "note")})

    if args.no_render:
        log("⏭  跳过渲染（--no-render）。", args.quiet)
        return 0

    # 5) 重渲染（先抓旧 KPI，渲染会覆盖同名文件）
    kpi_before = html_kpis(latest_html(wd))
    ok, html, why = render(wd, a, args.quiet)
    if not ok:
        print(f"❌ 渲染失败（{why}）\n   数据已更新，旧 HTML 未删除；修好后可加 --no-render 之外的方式重跑渲染。")
        notify("render", "学情报告刷新失败",
               f"{who} 的报告渲染出错：数据已更新，但页面未生成", wd, args.notify)
        return 3
    kpi_after = html_kpis(html)
    log(f"   ✅ 报告已重渲染：{os.path.basename(html)}（{os.path.getsize(html) // 1024} KB）", args.quiet)
    if kpi_after:
        log(f"   报告 KPI：{fmt_kpis(kpi_before, kpi_after)}", args.quiet)

    if args.copy_to:
        try:
            target = os.path.abspath(args.copy_to)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(html, target)
            log(f"   ✅ 已同步到 {target}", args.quiet)
        except Exception as e:
            print(f"⚠️ 复制到交付目录失败（{type(e).__name__}: {e}），但报告本身已生成。")
            return 3

    # 定时任务模式下也留一行成功痕迹，否则日志里只有失败才出现，无法判断是否在跑
    if args.quiet:
        print(f"[{dt.datetime.now():%Y-%m-%d %H:%M}] 刷新完成 ｜ {fmt_kpis(kpi_before, kpi_after)}")
    else:
        log("🎉 刷新完成。", args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
