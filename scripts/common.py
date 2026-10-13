"""联席学情报告技能 · 公共工具

提供：token 读取、HTTP 调用封装、姓名归一化、课程名解析。
所有脚本统一把中间文件读写在 --workdir 下（默认当前目录）。
"""
import json
import os
import sys
import urllib.request
import urllib.parse

BASE_HOST = "https://stadminv2.xqmsx.cn/api"


def workdir(args):
    return args.workdir or "."


def load_token(wd):
    p = os.path.join(wd, "token.txt")
    if not os.path.exists(p):
        raise SystemExit(f"未找到 {p}，请先登录后台并把 x-token 写入该文件（见 SKILL.md 登录步骤）。")
    return open(p, "r", encoding="utf-8").read().strip()


def api(method, path, token, body=None, timeout=60):
    url = BASE_HOST + path
    headers = {"x-token": token, "Content-Type": "application/json"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path, token, timeout=60):
    return api("GET", path, token, timeout=timeout)


def post(path, body, token, timeout=60):
    return api("POST", path, token, body, timeout=timeout)


ROLE_LABEL = {"co": "联席", "edu": "教务", "course": "课程"}
SCOPE_FIELD = {"co": "coTeacherName", "edu": "classTeacherName"}

# 购课类型：花名册字段 cIncomeProductType（后台「我的学生」列表的「购课类型」列）。
# 取值映射来自后台字典接口 GET /sysDictionary/findSysDictionary?type=productType。
BUY_TYPE_LABEL = {0: "未标注", 1: "直播", 2: "回放", 3: "试听（插班）", 4: "测试"}


def buy_type_label(v):
    """把后台「购课类型」cIncomeProductType 数值转为中文标签；未知/空值回退为原值或空串。

    1=直播 2=回放 3=试听（插班）4=测试（0=未标注）。
    """
    if v is None or v == "":
        return ""
    try:
        return BUY_TYPE_LABEL.get(int(v), f"类型{int(v)}")
    except (TypeError, ValueError):
        return str(v)


def resolve_scope(args):
    """解析查询角色：--co <联席名> / --edu <教务名> / --course <课程名>，三者只能选一为主维度。

    返回 (role, name)，role ∈ {'co','edu','course'}。
    - 课程维度（--course）：分析该课程下全部学生；课程跨多个联席，可另传 --co <联席名>
      作为课程内子筛选（仅保留该联席负责的部分），此子筛选不冲突。
    - 后台把「教务」存在 classTeacherName 字段（响应时效页的「教务」列即此）。
    """
    course = (getattr(args, "course", None) or "").strip()
    co = (getattr(args, "co", None) or "").strip()
    edu = (getattr(args, "edu", None) or "").strip()
    if edu and (co or course):
        raise SystemExit("--edu 与 --co/--course 只能二选一（教务视角 / 联席视角 / 课程维度）。")
    if course:
        return "course", course
    if edu:
        return "edu", edu
    if co:
        return "co", co
    raise SystemExit("必须指定 --co <联席名>（联席视角）/ --edu <教务名>（教务视角）/ --course <课程名>（课程维度）之一。")


def scope_field(role):
    """该角色在学生花名册/出勤表里对应的过滤字段名。"""
    return SCOPE_FIELD.get(role, "coTeacherName")


def norm_name(s):
    """姓名归一化：去尾部数字后缀（如「曾忻怡3662」→「曾忻怡」），去空格。"""
    import re
    return re.sub(r"\s*\d+$", "", str(s or "")).strip()


def clean_course_name(product_name):
    """把后台 product 名收敛成课程标签，例如
    '2026Q-SX-高二数学985层进化班六D谢胡' -> '高二数学·985层·六D班（谢胡老师）'
    """
    import re
    s = str(product_name or "")
    s = re.sub(r"^2026Q-SX-", "", s)
    s = s.replace("进化班", "")
    m = re.match(r"^(高二数学)(清北层|985层|211层|[\u4e00-\u9fa5]+层?)(.+?)(邵|谢胡|胡[\u4e00-\u9fa5]*)$", s)
    if m:
        layer, cls, teacher = m.group(2), m.group(3), m.group(4)
        return f"高二数学·{layer}·{cls}班（{teacher}老师）"
    return s or "未命名课程"


SUBJECTS = ("数学", "物理", "化学", "英语", "语文", "生物", "历史", "地理", "政治", "科学", "信息", "编程")


def subject_of(text):
    """从课程名/群名/产品名里提取学科关键词（如「高二化学鲁科985层」→「化学」）。

    用于「学科」等处的动态展示；提取不到时返回空串。
    """
    s = str(text or "")
    for w in SUBJECTS:
        if w in s:
            return w
    return ""


def ensure_openpyxl():
    """依赖自检：若当前解释器没有 openpyxl，尝试用同解释器自动 pip 安装。

    这样同事解压技能后用任意受管 Python 起手都能自愈，不必手动建 venv。
    """
    try:
        import openpyxl  # noqa: F401
        return
    except ImportError:
        import subprocess
        print("⏳ 未检测到 openpyxl，正在用当前 Python 自动安装（仅首次）…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "openpyxl>=3.1"])
        print("✅ openpyxl 安装完成。")


def learning_level(s, board):
    """学习情况分层（v3.8）：综合三类证据判档 —— ① 课堂小黑板作答 ② 作业闭环 ③ 老师反馈。

    report.py 与 xlsx.py 共用同一份口径，避免两处漂移。
    返回 (档位key, 档位中文, 依据列表[(来源, 文本)])
    档位：需重点关注 / 需关注 / 正常 / 良好
    """
    ev, score = [], 0
    # ① 课堂小黑板（最硬的学习证据）
    if board and board.get("details"):
        ds = board.get("details", [])
        rounds = board.get("rounds") or len(ds)
        sc = board.get("status_counts", {})
        full = sc.get("完整作答", 0)
        part = sc.get("部分作答", 0)
        blank = sc.get("空白未提交", 0) + sc.get("空白", 0)
        errs = board.get("error_types", []) or []
        trend = board.get("trend", "")
        lc = board.get("lesson_count") or len(board.get("lessons") or [])
        head = f"{lc} 次课 · {rounds} 轮"
        r = max(1, rounds)
        if blank / r >= 0.4:
            score -= 3
            ev.append(("小黑板", f"{head}中 {blank} 轮空白未提交"))
        elif part / r >= 0.6:
            score -= 2
            ev.append(("小黑板", f"{head}中 {part} 轮未收尾"))
        elif part / r >= 0.4:
            score -= 1
            ev.append(("小黑板", f"{head}中 {part} 轮未收尾"))
        elif full / r >= 0.7:
            score += 2
            ev.append(("小黑板", f"{head}中 {full} 轮完整作答"))
        elif full / r >= 0.5:
            score += 1
            ev.append(("小黑板", f"{head}中 {full} 轮完整作答"))
        if len(errs) >= 2:
            score -= 1
        if errs:
            ev.append(("小黑板", "典型错误：" + "、".join(errs[:2])))
        if trend == "下滑":
            score -= 1
            ev.append(("小黑板", "作答趋势下滑"))
        elif trend == "逐步改善":
            score += 1
            ev.append(("小黑板", "作答趋势逐步改善"))
    else:
        ev.append(("小黑板", "暂无小黑板数据"))
    # ② 作业闭环（催交 → 是否交 / 批改 → 是否改）：做对要加分，否则人人被判重点关注
    un, uo = s.get("urge_n", 0), s.get("urge_ok", 0)
    fn, fo = s.get("fix_n", 0), s.get("fix_ok", 0)
    if un > uo:
        miss = un - uo
        score -= 2 if miss >= 2 else 1
        ev.append(("作业", f"催交 {un} 次、{miss} 次未见提交"))
    elif un:
        score += 1
        ev.append(("作业", f"催交 {un} 次均已提交"))
    if fn > fo:
        miss = fn - fo
        score -= 2 if miss >= 2 else 1
        ev.append(("作业", f"批改 {fn} 次、{miss} 次未见改正"))
    elif fn:
        score += 1
        ev.append(("作业", f"批改 {fn} 次均已改正"))
    if s.get("hw_neg", 0) >= 2:
        score -= 1
        ev.append(("作业", f"自述「没写 / 不会做」{s['hw_neg']} 处"))
    # ③ 老师反馈（群里老师对该生的评价）
    tp, tn = s.get("tea_pos", 0), s.get("tea_neg", 0)
    if tn >= 3:
        score -= 2
    elif tn == 2:
        score -= 1
    if tn:
        ev.append(("老师反馈", f"指出问题 {tn} 处"))
    if tp >= 2:
        score += 1
    if tp:
        ev.append(("老师反馈", f"正面评价 {tp} 处"))
    # 档位
    if score <= -3:
        key, cn = "red", "需重点关注"
    elif score <= -1:
        key, cn = "orange", "需关注"
    elif score >= 2:
        key, cn = "blue", "良好"
    else:
        key, cn = "green", "正常"
    return key, cn, ev


def att_stats(a):
    """出勤口径（v3.8）：看回放也算有效出勤；只有「既没到课也没看回放」才算真缺课。

    返回 (总节数, 有效出勤, 到课, 仅回放补齐, 看回放总数, 真缺课, 缺课节次列表)
    """
    n = a["lessonCount"]
    cls = a.get("classes", []) or []
    att = sum(1 for c in cls if c.get("isAttend") == 1)
    pb = sum(1 for c in cls if c.get("isPlayback") == 1)
    pb_only = sum(1 for c in cls if c.get("isAttend") != 1 and c.get("isPlayback") == 1)
    eff = a.get("effectiveAttendCount")
    if eff is None:                      # 兼容旧产物（未含新字段）
        eff = att + pb_only
    absent = max(0, n - eff)
    no_pb = [c.get("classSeq") for c in cls if c.get("isAttend") != 1 and c.get("isPlayback") != 1]
    return n, eff, att, pb_only, pb, absent, no_pb


def att_sentence(a):
    """出勤一句话（v3.8）：如「出勤 4/5 次（含回放 2 次），缺课 1 次，第 5 节既没到课也没看回放」。"""
    if not a:
        return "后台无该生出勤记录"
    n, eff, att, pb_only, pb, absent, no_pb = att_stats(a)
    if absent == 0 and pb_only == 0:
        return f"全勤（{n}/{n} 节）"
    if absent == 0:
        return f"出勤 {eff}/{n} 次（其中 {pb_only} 次为看回放补齐），无缺课"
    tail = f"，第{'、'.join(map(str, no_pb))}节既没到课也没看回放" if no_pb else ""
    return (f"出勤 {eff}/{n} 次" + (f"（含回放 {pb_only} 次）" if pb_only else "")
            + f"，缺课 {absent} 次{tail}")
