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


def ensure_py_deps():
    """依赖自检：当前仅标准库即可跑通全流程（v3.9 起不再产出 Excel，无第三方依赖）。"""
    return


def learning_level(s, board):
    """学习情况分层（v3.8）：综合三类证据判档 —— ① 课堂小黑板作答 ② 作业闭环 ③ 老师反馈。

    report.py 统一使用本口径，避免多处漂移。
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


# ============================================================================
# 细粒度薄弱题型（v4.2）
# ----------------------------------------------------------------------------
# 背景：原先「薄弱知识点」只到 `立体几何/空间向量`、`直线和圆` 这一层（课程大类），
# 对联席没有可操作性。v4.2 把它下钻一层 —— 「直线和圆」下面到底是「含参直线过定点」
# 还是「斜率是否存在的分类讨论」有问题，得说清楚。
#
# 下面这张表只是**识别维度**（题型名 → 关键词），不产生任何内容：
# 每条结论都必须落到某条真实消息或某轮真实小黑板读图，并带原文出处（evi）供核对。
# 关键词取自真实语料的实际说法（老师/学生的原话）。
# ============================================================================
FINE_TYPES = {
    "立体几何/空间向量": [
        ("建系与点坐标书写", ["点坐标", "坐标写错", "写错", "建系", "坐标系"]),
        ("点到平面距离（投影法）", ["点到平面的距离", "投影", "垂足"]),
        ("异面直线夹角／线面角／二面角", ["异面直线", "线面角", "二面角", "所成角"]),
        ("外接球／内切球", ["外接球", "内切球"]),
        ("截面与展开", ["截面", "一刀切"]),
        ("基底法与共面向量", ["基底", "共面", "系数和"]),
        ("法向量求法", ["法向量"]),
    ],
    "直线和圆": [
        ("含参直线过定点", ["含参", "过定点", "定点"]),
        ("斜率是否存在（分类讨论）", ["斜率不存在", "斜率", "两种情况", "分类讨论"]),
        ("弦长公式", ["弦长"]),
        ("点到直线距离", ["点到直线", "圆心到直线"]),
        ("切线与对称转化", ["切线", "对称"]),
        ("圆的一般式与圆心坐标", ["圆心坐标", "一般式", "标准式", "配方"]),
        ("面积与最值转化", ["最大值", "最值", "面积"]),
    ],
    "圆锥曲线": [
        ("联立方程与韦达定理", ["韦达", "联立", "判别式"]),
        ("离心率", ["离心率"]),
        ("定点定值问题", ["定值", "定点"]),
        ("点差法", ["点差"]),
    ],
    "函数与导数": [
        ("复合函数求导", ["复合函数", "复合"]),
        ("单调性与极值", ["单调", "极值"]),
        ("导数的几何意义", ["切线", "求导"]),
    ],
    "三角函数": [
        ("图象与性质", ["三角函数", "周期", "诱导", "正弦", "余弦"]),
    ],
    "解三角形": [
        ("正余弦定理应用", ["正弦定理", "余弦定理"]),
    ],
    "概率统计": [
        ("计数与分布", ["概率", "排列", "组合", "统计"]),
    ],
    "计算/粗心": [
        ("计算与化简错误", ["算错", "计算错误", "算不出来", "化简", "抄错", "符号"]),
        ("漏解／漏讨论情况", ["漏", "少考虑", "少一种", "没考虑"]),
    ],
}

# 老师「指出具体问题」的语气词 —— 必须明确指向学生的错 / 卡点（v4.2 收紧，去掉过泛词）
TEA_POINT_KWS = ["你错", "错误的原因", "写错", "算错", "抄错", "没有彻底理解", "没理解", "少考虑",
                 "要注意", "一定要考虑", "再好好想", "是不是写错", "是不是算错", "问题所在",
                 "没看出来", "做错", "你是不是写", "你怎么写", "你这一步", "你上面"]
# 老师这句话若含「假设 / 询问」语气 → 不是在指出问题（如「看有哪里是否做错」）
TEA_POINT_EXCLUDE = ["是否", "如果", "假如", "会不会", "对不对", "有没有", "要是"]
# 学生表达困惑 / 卡点的语气词
STU_LOST_KWS = ["不会", "不懂", "没懂", "不明白", "卡住", "求思路", "怎么做", "怎么算",
                "为什么", "看不懂", "没看出", "哪里错", "急急急"]
# 正面语境 —— 学生自述「做得好的地方 / 已掌握」时提到的题型属**优势**，不能当薄弱点
POSITIVE_KWS = ["做得比较好", "做得不错", "比较顺", "已经掌握", "都会了", "思路是对的",
                "做对了", "答对了", "完全正确", "没什么问题", "没有问题", "比较熟练"]
# 群通知 / 事务性消息（不参与薄弱点提取）
NOTICE_KWS = ["上课通知", "课表", "讲义", "报名", "扫码", "教室", "群公告", "放假", "调课安排",
              "作业记得", "记得完成", "记得交", "上课时间", "考试范围"]


def is_notice(text):
    """群通知 / 事务性消息（不参与薄弱知识点提取）。"""
    return any(k in (text or "") for k in NOTICE_KWS)


def is_positive(text):
    """正面语境（自述已掌握 / 做得较好）。"""
    return any(k in (text or "") for k in POSITIVE_KWS)


def is_tea_point(text):
    """是否是老师「具体指出问题」的句子（不能带假设/询问语气）。"""
    s = text or ""
    if any(k in s for k in TEA_POINT_EXCLUDE):
        return False
    return any(k in s for k in TEA_POINT_KWS)


def classify_fine(text, best_only=False):
    """把一段文本归类到「知识点 → 具体题型」。

    best_only=True 时**只返回最具体的一个**（按命中关键词长度判断）——
    否则「没有彻底理解复合函数的求导」会同时命中「复合函数求导」和「导数的几何意义」，
    同一条证据被算作两个薄弱点（真实数据实测的重复问题）。
    """
    out = []
    for topic, items in FINE_TYPES.items():
        for item, kws in items:
            hit = [k for k in kws if k in text]
            if hit:
                out.append((topic, item, max(len(k) for k in hit)))
    if best_only and out:
        t, i, _ = max(out, key=lambda x: x[2])
        return [(t, i)]
    return [(t, i) for t, i, _ in out]


def clip_txt(t, n=38):
    import re as _re
    return _re.sub(r"\s+", " ", (t or "")).strip()[:n]


def fine_from_board(board, per_topic=5):
    """小黑板读图 → 细粒度薄弱点。只取「有错 / 卡点 / 未完成」的轮次。

    每轮读图结论自带 `problem`（具体题目）与 `stuck_at`（卡点），正是最细的证据。
    返回 {知识点: [{"item","kind","at","evi","count"}]}
    """
    if not board:
        return {}
    res = {}
    for d in board.get("details", []) or []:
        st = d.get("status") or ""
        ok = d.get("correct") or ""
        errs = d.get("errors") or []
        stuck = (d.get("stuck_at") or "").strip()
        if st == "完整作答" and ok == "正确" and not errs:
            continue                              # 完整且正确 → 不算薄弱证据
        topic = d.get("topic") or d.get("topic_knowledge") or "未归类"
        prob = d.get("problem") or ""
        # 题型名：优先用读图给出的卡点原文，其次用题面里命中的题型词
        item = ""
        for t2, it2 in classify_fine(prob + " " + stuck):
            if t2 == topic or not item:
                item = it2
                break
        if not item:
            # 没命中题型词表时，直接用读图给出的**卡点原文**（最具体、可回溯），
            # 并加「课堂作答卡点：」前缀，与命中词表的题型名区分开。
            item = (f"课堂作答卡点：{stuck}" if stuck
                    else f"课堂作答卡点：{clip_txt(prob, 18) or '未完成'}")
        evi = f"原题：{clip_txt(prob, 26)}" if prob else "小黑板作答"
        if stuck:
            evi += f"｜卡点：{clip_txt(stuck, 22)}"
        elif errs:
            evi += "｜错误：" + "、".join(errs[:2])
        res.setdefault(topic, []).append(
            {"item": item, "kind": "小黑板", "at": d.get("lesson_date") or "", "evi": evi, "count": 1})
    # 同类归并
    merged = {}
    for topic, rows in res.items():
        agg = {}
        for r in rows:
            k = r["item"]
            if k in agg:
                agg[k]["count"] += 1
            else:
                agg[k] = r
        merged[topic] = list(agg.values())[:per_topic]
    return merged


def merge_fine(*maps):
    """合并多个 {知识点: [条目]}，同 (知识点, 题型) 归并（count 相加、保留首条出处）。"""
    out = {}
    for m in maps:
        for topic, rows in (m or {}).items():
            slot = out.setdefault(topic, {})
            for r in rows:
                k = r.get("item") or "—"
                if k in slot:
                    slot[k]["count"] = slot[k].get("count", 1) + r.get("count", 1)
                else:
                    slot[k] = dict(r)
    return {t: list(v.values()) for t, v in out.items()}


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
