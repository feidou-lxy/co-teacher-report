"""联席学情报告技能 · 公共工具

提供：token 读取、HTTP 调用封装、姓名归一化、课程名解析。
所有脚本统一把中间文件读写在 --workdir 下（默认当前目录）。
"""
import json
import os
import re
import sys
import urllib.request
import urllib.parse

BASE_HOST = "https://stadminv2.xqmsx.cn/api"


def workdir(args):
    return args.workdir or "."


# ---------------------------------------------------------------- token 读取
# 后台 x-token 写成一行纯文本即可。但用户从浏览器复制过来时，格式五花八门，
# 容易「复制了整段 cookie」「带了 token= 前缀」「前后带引号或换行」而失败。
# 这里统一做容错清洗，避免把「怎么又没登录上」的错误抛回给业务同学。
_TOKEN_HINT = (
    "获取方式（二选一）：\n"
    "  A. 让 WorkBuddy 在浏览器打开 https://stadminv2.xqmsx.cn/#/login，你自己输账号密码 + 图形验证码；\n"
    "  B. 自己在浏览器登录后按 F12 → Console 输入 document.cookie 回车，把 token= 那一串整段粘过来。\n"
    "（无需提供账号密码；token 约 24 小时过期）"
)


def clean_token(raw):
    """从各种「粘贴格式」里抽出真正的 x-token。抽不出返回空串。

    接受：裸 token / `token=xxx` / 整段 cookie / JSON / 带引号或多余空白。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # JSON 形式：{"token": "..."} / {"x-token": "..."}
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            for k in ("token", "x-token", "xToken", "access_token", "accessToken"):
                v = obj.get(k) if isinstance(obj, dict) else None
                if isinstance(v, str) and v.strip():
                    return v.strip().strip('"').strip("'")
        except Exception:
            pass
    # 去引号
    s2 = s.strip().strip('"').strip("'").strip()
    # 字符串里带 "token":"xxx"（例如粘了整段接口响应 / DevTools 复制对象）
    m = re.search(r'"x?-?token"\s*:\s*"([^"]+)"', s2, re.I)
    if m and m.group(1).strip():
        return m.group(1).strip()
    # `token=xxx` / 整段 cookie：取 token= 之后到下一个分隔符为止（值里可含 = 的 base64 补齐）
    m = re.search(r"(?:^|[;\s,\"'])x?-?token\s*=\s*([^;\s,\"']+)", s2, re.I)
    if m and m.group(1).strip():
        return m.group(1).strip()
    # 单行无空白 → 直接当 token
    parts = s2.split()
    if len(parts) == 1:
        return parts[0]
    # 多行 / 多段：取最长的一段当 token
    cand = [x for x in re.split(r"[\s;,]+", s2) if len(x) >= 20]
    return max(cand, key=len) if cand else ""


def load_token(wd):
    p = os.path.join(wd, "token.txt")
    if not os.path.exists(p):
        raise SystemExit(
            f"未找到 {p}（登录凭证）。\n{_TOKEN_HINT}\n"
            "拿到后写入该文件（一行纯文本）再重跑本步。"
        )
    raw = open(p, "r", encoding="utf-8").read()
    tok = clean_token(raw)
    if len(tok) < 8:
        raise SystemExit(
            f"{p} 里的内容不像有效的 x-token（只解析到 {len(tok)} 个字符）。\n"
            f"文件开头是：{raw.strip()[:60]!r}\n{_TOKEN_HINT}\n"
            "把 token 写成一行纯文本（形如 token=eyJhbGciOi...）再重跑本步。"
        )
    return tok


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


# ============================================================================
# 课程名（v4.7）：后台「响应时效」列表已**直接给出课程名** `productName`
#   productName = 「2026Q-SX-高二数学985层进化班六D谢胡」   ← 权威、含班型
#   groupName   = 「张喜然(W)-高二数学985层六D谢胡-26Q」     ← 学生名 + 课程 + 26Q
# 旧做法（v4.6 及更早）从 groupName 切段再正则解析 —— 班号写法不一、
# 同一课程在不同群里写法还可能不同（群名缺「进化班」）。现改为**优先取 productName**，
# groupName 仅作回退（兼容已归档、不含 productName 的 groups.json）。
# ============================================================================
COURSE_PREFIX_RE = re.compile(r"^\s*\d{4}\s*Q\s*-\s*[A-Za-z]{2,4}\s*-\s*")


def course_name(product_name=None, group_name=None):
    """取「课程名」原串（去掉 `2026Q-SX-` 这类产品前缀，保留班型如「进化班」）。

    优先用后台 `productName`；缺失时回退从群名第二段取（`学生名-课程名-26Q`）。
    两者都没有时返回空串。
    """
    pn = COURSE_PREFIX_RE.sub("", str(product_name or "").strip()).strip()
    if pn:
        return pn
    gn = str(group_name or "").strip()
    parts = [p for p in gn.split("-") if p.strip()]
    return (parts[1] if len(parts) > 1 else gn).strip()


# 层级词（清北层 / 985层 / 211层 / 培优层 …）；「进化班」是层级后缀（985层进化班）
# 先按**已知层级**精确匹配（覆盖当前全部课程）；未知层级才走通用 `..层`，
# 且要先把误吞的学科词（数学/物理…）退回前缀 —— 否则「高二数学清北层」
# 会被通用分支切在「数学清北层」上，展示名变成「高二·数学清北层·…」。
_LAYER_RE = re.compile(r"清北层|985层|211层|培优层|尖子层|提高层|基础层")
_LAYER_ANY_RE = re.compile(r"[\u4e00-\u9fa5]{2,4}层")
_SUBJECT_WORDS = ("数学", "物理", "化学", "英语", "语文", "生物", "历史", "地理", "政治", "科学", "信息")


def _split_layer(s):
    """在课程名里定位「层级」，返回 (层级起始下标, 层级文本)；定位不到返回 (None, None)。"""
    m = _LAYER_RE.search(s)
    if m:
        return m.start(), m.group(0)
    m = _LAYER_ANY_RE.search(s)
    if not m:
        return None, None
    txt = m.group(0)
    for w in _SUBJECT_WORDS:          # 通用分支误吞学科词 → 退回前缀
        if txt.startswith(w) and len(txt) > len(w):
            return m.start() + len(w), txt[len(w):]
    return m.start(), txt


# 班号：日E / 六D / 日A …（「日/六」等 + 单个大写字母）
_CLASS_RE = re.compile(r"([日一二三四五六][A-Z])")


def clean_course_name(text):
    """课程名 → 展示名（含层级与班号），例如：
        '2026Q-SX-高二数学985层进化班六D谢胡' → '高二数学·985层进化班·六D班（谢胡老师）'
        '高二数学清北层日E邵'                 → '高二数学·清北层·日E班（邵老师）'
        '高二数学进度二985层日D肖'            → '高二数学进度二·985层·日D班（肖老师）'
    解析不出层级 / 班号时**原样返回**（绝不硬凑），如 '高二数学快进度定制1班日B袁'。
    """
    s = COURSE_PREFIX_RE.sub("", str(text or "").strip()).strip()
    if not s:
        return "未命名课程"
    li, layer = _split_layer(s)
    if li is None:
        return s
    head, rest = s[:li], s[li + len(layer):]
    mc = _CLASS_RE.search(rest)
    if not mc:
        return s
    # 层级与班号之间的成分**一律保留**（如「进化班」「零点五加三直升班」），避免丢信息
    mid = rest[:mc.start()].strip()
    cls, tea = mc.group(1), rest[mc.end():].strip()
    if not tea:
        return s
    return f"{head}·{layer}{mid}·{cls}班（{tea}老师）".lstrip("·")


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


# ============================================================================
# 学习分层（v3.8 引入 · v4.4 重校口径）
# ----------------------------------------------------------------------------
# 四档：需重点关注 ≤-3 ｜ 需关注 -2~-1 ｜ 正常 0~+1 ｜ 良好 ≥+2
# 四类证据：① 课堂小黑板作答 ② 作业闭环 ③ 群里老师反馈 ④ 学生成绩（月考/期中）
#
# v4.4 为什么要重校（真实 58 群 / 三位学生回归实测）：
#   旧口径下「老师指出问题 ≥3 处 → -2」按**绝对次数**计，而真实数学群里老师高强度纠错
#   是教学常态（三位学生分别在 2/8/8 次课里被指出 11/12/9 处）→ 人人触发、人人扣分；
#   加上真实窗口内往往**没有催交/批改记录**（唯一正分来源缺席），结果所有学生都掉进
#   「需重点关注」，四档退化成一档、徽章失去区分度。故 v4.4 做三件事：
#     1) 反馈改为**密度归一化**（每 10 轮小黑板 / 每 100 条可分析消息的指出率）
#     2) 小黑板区分「**仅未完成收尾**」（课堂节奏 / 书写速度，不是能力）与「**实质错误**」
#        —— 「未完成收尾」在真实语料里占错误标注的 25/33，属常态，不再按能力扣分
#     3) 新增**学生成绩**作为最强证据，并对档位做托底 / 封顶
# ============================================================================

# —— 小黑板：这些「错误类型」其实是没写完，不记在能力账上 ——
# ⚠️ 读图模型对同一件事有两种词序：「未完成收尾」与「**收尾未完成**」，后者在真实语料里
#    才是主流写法（实测 176 条错误标注中「收尾未完成」占 94 条）。故一律用 is_finish_err()
#    做**包含式**判定，精确匹配会静默漏掉最常见的那个写法。
NO_FINISH_ERR = {"未完成收尾", "未完成", "未收尾", "未写完"}
_FINISH_KEYS = ("未完成", "未收尾", "未写完", "没写完", "未做完")


def is_finish_err(e):
    """该条小黑板「错误」标注是否只是没写完（课堂节奏 / 书写速度，非能力问题）。

    包含式匹配，兼容「未完成收尾 / 收尾未完成 / 未完成 / 未收尾 / 未写完」等写法。
    """
    s = (e or "").strip()
    if not s:
        return False
    return s in NO_FINISH_ERR or any(k in s for k in _FINISH_KEYS)

LEVEL_ORDER = ["red", "orange", "green", "blue"]        # 由差到好
LEVEL_CN = {"red": "需重点关注", "orange": "需关注", "green": "正常", "blue": "良好"}
LEVEL_RANK = {"red": 0, "orange": 1, "green": 2, "blue": 3}


def level_by_score(score):
    """累计得分 → 档位 key。"""
    if score <= -3:
        return "red"
    if score <= -1:
        return "orange"
    if score >= 2:
        return "blue"
    return "green"


# —— ④ 学生成绩：群聊里定期收集的月考 / 期中成绩 ——
EXAM_NAME_KWS = ["月考", "期中", "期末", "一模", "二模", "三模", "结课测", "阶段测", "单元测",
                 "周测", "联考", "摸底考", "开学考", "诊断考", "小测", "测验", "考试成绩", "测试成绩"]
EXAM_FULL_KWS = ["满分", "总分", "卷面", "共", "总共"]
EXAM_PAIR_RE = re.compile(r"(\d{2,3}(?:\.\d)?)\s*/\s*(\d{2,3})")        # 128/150
EXAM_SCORE_RE = re.compile(r"(\d{2,3}(?:\.\d)?)\s*分")                  # 128分
EXAM_BARE_RE = re.compile(r"(?<![\d.%/])(\d{2,3})(?![\d.%/])(?!\s*(?:人|名|位|个|题|分钟|小时|天|次))")
EXAM_FULL_RE = re.compile(r"(?:满分|总分|卷面)\s*[:：]?\s*(\d{2,3})")
EXAM_RANK_RE = re.compile(r"(?:第\s*(\d{1,3})\s*名|排名\s*[:：]?\s*(\d{1,3}))")
EXAM_TOTAL_RE = re.compile(r"(?:/\s*(\d{2,4})|共\s*(\d{2,4})\s*人|总人数\s*[:：]?\s*(\d{2,4})|(\d{2,4})\s*人)")
# 「得分/满分」形态里第二个数只有这些才可能是卷面满分（避免把「排名 210/900」当成分数）
EXAM_PLAUSIBLE_FULL = {60, 70, 100, 120, 150, 200, 300, 750}
# 分数前的「非人名」词：出现在数字前也不算他人姓名
EXAM_NON_NAME = ["成绩", "得分", "分数", "总分", "满分", "考了", "本次", "这次", "我的", "我们",
                 "全班", "平均", "年级", "班平", "老师", "同学", "排名", "名次", "以上", "大概",
                 "约", "为", "是", "得", "考", "测", "卷"]
EXAM_SUBJ_FULL = {"数学": 150, "语文": 150, "英语": 150, "物理": 100, "化学": 100,
                  "生物": 100, "政治": 100, "历史": 100, "地理": 100}
# 成绩关键词附近的「事务性」表述（考试范围 / 时间 / 报名…不算成绩）
EXAM_SKIP_KWS = ["范围", "时间", "安排", "报名", "几点", "考场", "记得", "准备", "复习", "加油"]


def _exam_level(pct):
    """得分率 → 成绩档（高 ≥85% ｜ 中 ≥65% ｜ 偏低 <65%）。"""
    if pct is None:
        return "未知"
    if pct >= 0.85:
        return "高"
    if pct >= 0.65:
        return "中"
    return "偏低"


def _exam_attributed(sent, name, value_str):
    """判断句子里的分数是否属于该生（避免把「成绩收集」里别人的分数算到本人头上）。

    规则：① 句内出现本人姓名 → 是；② 分数紧邻的 2~4 个汉字是考试名/非人名词 → 是；
    ③ 该词是别的人名（如「李四 140」）→ 否；④ 分数前没有名字 → 单人群语境下归本人。
    """
    if name and name in sent:
        return True
    if value_str:
        m = re.search(r"([\u4e00-\u9fa5]{2,4})\s*[:：]?\s*" + re.escape(value_str), sent)
        if m:
            tok = m.group(1)
            if any(k in tok for k in EXAM_NAME_KWS) or any(k in tok for k in EXAM_NON_NAME):
                return True
            return False
    return True


def extract_exam(triples, stu_name, subject=""):
    """从聊天记录里识别该生的**成绩**（月考 / 期中 / 成绩收集）。

    🔒 只做匹配、不生成任何文本；认不出就返回「未知」，**绝不用裸数字硬凑**。
    保守识别（宁缺勿滥）：
      · 必须命中考试名关键词（月考/期中/期末/联考/测验…），且不含「范围/时间/报名」等事务词
      · 分数形态按可信度取：`128/150` → `128分` → 裸数字（裸数字需排除「48 人 / 第 8 名」这类）
      · 必须能归因到本人（句内出现姓名，或分数前不是别人的名字），避免成绩收集里的他人成绩
      · 排除「满分／总分／卷面」后面的数字（那是卷面分，不是学生得分）
      · 无满分时按学科推断（数学 150，其余 100）并在 why 里标注「按 X 制推断」
    返回 {"latest": {...}|None, "history": [...], "level": "高|中|偏低|未知", "why": "..."}
    """
    name = norm_name(stu_name)
    found = []
    for t, _role, text in (triples or []):
        txt = text or ""
        if not txt:
            continue
        for sent in re.split(r"[。！？；;\n]", txt):
            s = (sent or "").strip()
            if not s or len(s) > 120:
                continue
            kws = [k for k in EXAM_NAME_KWS if k in s]
            if not kws or any(k in s for k in EXAM_SKIP_KWS):
                continue
            full = None
            mf = EXAM_FULL_RE.search(s)
            if mf:
                try:
                    full = int(mf.group(1))
                except ValueError:
                    full = None
            rank = total = None
            mr = EXAM_RANK_RE.search(s)
            if mr:
                rank = int(mr.group(1) or mr.group(2))
                mt = EXAM_TOTAL_RE.search(s)
                if mt:
                    tv = next((x for x in mt.groups() if x), None)
                    total = int(tv) if tv else None
            # —— 分数候选：按可信度优先 ——
            value = None
            mp = EXAM_PAIR_RE.search(s)
            if mp:
                try:
                    v2, f2 = float(mp.group(1)), int(mp.group(2))
                except ValueError:
                    v2 = f2 = None
                if v2 is not None and f2 in EXAM_PLAUSIBLE_FULL:
                    value, full = v2, f2
            if value is None:
                ms = EXAM_SCORE_RE.search(s)
                if ms and not any(k in s[max(0, ms.start() - 4):ms.start()] for k in EXAM_FULL_KWS):
                    value = float(ms.group(1))
            if value is None and rank is None:
                mb = EXAM_BARE_RE.search(s)
                if mb and not any(k in s[max(0, mb.start() - 4):mb.start()] for k in EXAM_FULL_KWS):
                    value = float(mb.group(1))
            if value is None and rank is None:
                continue
            if not _exam_attributed(s, name, (f"{value:g}" if value is not None else None)):
                continue
            assumed = False
            pct = None
            if value is not None:
                f = full or EXAM_SUBJ_FULL.get(subject, 100)
                if f and value > f:      # 超出卷面 → 大概率不是分数（作业分 / 题号）
                    continue
                if f:
                    assumed = full is None
                    pct = value / f
            elif rank and total:
                pct = 1 - (rank / total)      # 名次换成分位（越靠前越高）
            found.append({"date": (t or "")[:10], "label": kws[0], "value": value,
                          "full": (full or (EXAM_SUBJ_FULL.get(subject, 100) if value is not None else None)),
                          "rank": rank, "total": total, "pct": pct, "assumed": assumed,
                          "at": t or "", "evi": clip_txt(s, 60)})
    if not found:
        return {"latest": None, "history": [], "level": "未知", "why": ""}
    found.sort(key=lambda x: (x["at"] or "", x["label"]))
    latest = found[-1]
    lv = _exam_level(latest["pct"])
    if latest["value"] is not None:
        why = f"{latest['label']} {latest['value']:g}/{latest['full']:g}"
        if latest["pct"] is not None:
            why += f"（得分率 {latest['pct']:.0%}）"
        why += f"· {latest['date']}"
        if latest["assumed"]:
            why += f"（无满分标注，按 {latest['full']:g} 分制推断）"
    else:
        why = f"{latest['label']} 第 {latest['rank']} 名"
        if latest["total"]:
            why += f"/{latest['total']}"
        if latest["pct"] is not None:
            why += f"（前 {1 - latest['pct']:.0%}）"
        why += f"· {latest['date']}"
    return {"latest": latest, "history": found, "level": lv, "why": why}


def learning_level(s, board):
    """学习分层（v4.4）：综合四类证据判档 —— ① 小黑板 ② 作业闭环 ③ 老师反馈 ④ 学生成绩。

    report.py 统一使用本口径，避免多处漂移。
    返回 (档位key, 档位中文, 依据列表[(来源, 文本, 加减分)])
    """
    ev, score = [], 0

    def add(src, text, d=0):
        nonlocal score
        score += d
        ev.append((src, text, d))

    # ---------------- ④ 学生成绩（最强证据；用于档位托底 / 封顶，故先算） ----------------
    exam = s.get("exam") or {}
    ex_lv = exam.get("level") or "未知"
    ex_why = exam.get("why") or ""
    floor_key = cap_key = None
    if ex_lv == "高":
        floor_key = "blue"
        add("成绩", f"{ex_why} → 成绩好，学习效果以成绩为准（档位托底「良好」）", 3)
    elif ex_lv == "中":
        add("成绩", f"{ex_why} → 成绩中等", 1)
    elif ex_lv == "偏低":
        cap_key = "orange"
        add("成绩", f"{ex_why} → 成绩偏低，即使作业/配合到位，学习效果仍不足（封顶「需关注」）", -3)
    else:
        add("成绩", "窗口内未见成绩信息（月考 / 期中成绩收集）", 0)

    # ---------------- ① 课堂小黑板（最硬的学习证据） ----------------
    if board and board.get("details"):
        ds = board.get("details") or []
        rounds = board.get("rounds") or len(ds)
        r = max(1, rounds)
        lc = board.get("lesson_count") or len(board.get("lessons") or [])
        head = f"{lc} 次课 · {rounds} 轮"
        blank = [d for d in ds if (d.get("status") or "") in ("空白未提交", "空白")]
        real_err = [d for d in ds if [e for e in (d.get("errors") or []) if e not in NO_FINISH_ERR]]
        unfin = [d for d in ds if d not in blank and d not in real_err]
        clean = [d for d in ds if d.get("status") == "完整作答" and not (d.get("errors") or [])
                 and (d.get("correct") in ("正确", "基本正确"))]
        rb, rr, ru, rf = len(blank) / r, len(real_err) / r, len(unfin) / r, len(clean) / r
        add("小黑板", f"{head}作答分布：空白 {len(blank)}（{rb:.0%}）｜实质错误 {len(real_err)}（{rr:.0%}）"
                      f"｜仅未完成收尾 {len(unfin)}（{ru:.0%}）｜完整且无错 {len(clean)}（{rf:.0%}）", 0)
        if rb >= 0.4:      # 参与度
            add("小黑板", f"空白未提交 {len(blank)} 轮（{rb:.0%} ≥40%，属参与度问题）", -2)
        elif rb >= 0.2:
            add("小黑板", f"空白未提交 {len(blank)} 轮（{rb:.0%} ≥20%）", -1)
        if rr >= 0.3:      # 能力：只认剔除「未完成收尾」后的实质错误
            add("小黑板", f"存在实质错误 {len(real_err)} 轮（{rr:.0%} ≥30%，能力证据）", -2)
        elif rr >= 0.2:
            add("小黑板", f"存在实质错误 {len(real_err)} 轮（{rr:.0%} ≥20%，能力证据）", -1)
        if ru >= 0.85:     # 节奏：只在极端时轻提示，不按能力计
            add("小黑板", f"仅未完成收尾 {len(unfin)} 轮（{ru:.0%} ≥85%，课堂节奏 / 书写速度问题）", -1)
        if rf >= 0.6:      # 正分：完整作答且无错
            add("小黑板", f"完整作答且无错 {len(clean)} 轮（{rf:.0%} ≥60%）", 2)
        elif rf >= 0.4:
            add("小黑板", f"完整作答且无错 {len(clean)} 轮（{rf:.0%} ≥40%）", 1)
        trend = board.get("trend", "")
        if trend == "下滑":
            add("小黑板", "作答趋势下滑", -1)
        elif trend == "逐步改善":
            add("小黑板", "作答趋势逐步改善", 1)
        errs = [e for e in (board.get("error_types") or []) if e not in NO_FINISH_ERR]
        if errs:
            add("小黑板", "典型错误：" + "、".join(errs[:3]), 0)
    else:
        add("小黑板", "暂无小黑板读图数据", 0)

    # ---------------- ② 作业闭环（催交 → 是否交 / 批改 → 是否改） ----------------
    un, uo = s.get("urge_n", 0), s.get("urge_ok", 0)
    fn, fo = s.get("fix_n", 0), s.get("fix_ok", 0)
    if un > uo:
        miss = un - uo
        add("作业", f"催交 {un} 次、{miss} 次未见提交", -2 if miss >= 2 else -1)
    elif un:
        add("作业", f"催交 {un} 次均已提交", 1)
    else:
        add("作业", "窗口内无催交记录", 0)
    if fn > fo:
        miss = fn - fo
        add("作业", f"批改 {fn} 次、{miss} 次未见改正", -2 if miss >= 2 else -1)
    elif fn:
        add("作业", f"批改 {fn} 次均已改正", 1)
    else:
        add("作业", "窗口内无批改记录", 0)
    if s.get("hw_neg", 0) >= 2:
        add("作业", f"自述「没写 / 不会做」{s['hw_neg']} 处（≥2）", -1)

    # ---------------- ③ 群里老师反馈（v4.4：密度归一化，不再按绝对次数） ----------------
    tp, tn = s.get("tea_pos", 0), s.get("tea_neg", 0)
    rounds = (board or {}).get("rounds") or 0
    n_text = s.get("n_text", 0) or 0
    if rounds:
        dens, base = tn / max(1, rounds) * 10, f"{rounds} 轮小黑板"
    elif n_text:
        dens, base = tn / max(1, n_text) * 100, f"{n_text} 条可分析消息"
    else:
        dens, base = 0.0, "无基数"
    if dens >= 10:
        add("老师反馈", f"指出问题 {tn} 处（密度 {dens:.1f}/10 单位 · {base} ≥10）", -2)
    elif dens >= 5:
        add("老师反馈", f"指出问题 {tn} 处（密度 {dens:.1f}/10 单位 · {base} ≥5）", -1)
    else:
        add("老师反馈", f"指出问题 {tn} 处（密度 {dens:.1f}/10 单位 · {base}，未超基线）", 0)
    if tp >= 3:
        add("老师反馈", f"正面评价 {tp} 处（≥3）", 2)
    elif tp >= 1:
        add("老师反馈", f"正面评价 {tp} 处（≥1）", 1)

    # ---------------- 落档（成绩托底 / 封顶） ----------------
    key = level_by_score(score)
    if floor_key and LEVEL_RANK[floor_key] > LEVEL_RANK[key]:
        ev.append(("成绩", f"按成绩托底：不低于「{LEVEL_CN[floor_key]}」", 0))
        key = floor_key
    if cap_key and LEVEL_RANK[cap_key] < LEVEL_RANK[key]:
        ev.append(("成绩", f"按成绩封顶：不高于「{LEVEL_CN[cap_key]}」", 0))
        key = cap_key
    return key, LEVEL_CN[key], ev


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
        # —— v4.3：下面三项由真实课堂题目（题干原话）补充，此前一律落到「未归类」——
        ("向量线性相关／点集判定", ["线性相关", "点集", "Λ", "λ1", "不全为零"]),
        ("动点与多结论判断", ["动点", "运动", "判断四个结论", "正确结论的序号",
                              "四个叙述", "结论正确的是", "结论中正确", "四个结论"]),
        ("平行与垂直的证明", ["线面平行", "面面垂直", "∥平面", "平行于平面", "证平行", "证垂直"]),
    ],
    "直线和圆": [
        ("含参直线过定点", ["含参", "过定点", "定点"]),
        ("斜率是否存在（分类讨论）", ["斜率不存在", "斜率", "两种情况", "分类讨论"]),
        ("弦长／截线问题", ["弦长", "截得", "所截", "公共弦", "弦"]),
        ("点到直线距离", ["点到直线", "圆心到直线"]),
        ("切线与对称转化", ["切线", "对称"]),
        ("圆的一般式与圆心坐标", ["圆心坐标", "一般式", "标准式", "配方"]),
        ("面积与最值转化", ["最大值", "最值", "面积"]),
        # —— v4.3：同上，由真实课堂题目补充 ——
        ("存在性与构造", ["存在点", "是否存在", "使四边形", "存在A", "存在M", "存在一点"]),
        ("轨迹与阿波罗尼斯圆", ["阿波罗尼斯", "轨迹", "PA=√2PB", "MA=2MO", "满足PA"]),
        ("两圆／两定点最值", ["两圆", "PA+PB", "PA-PB", "|PA|", "两定点", "距离之和"]),
        ("平行线间距离", ["平行直线", "两平行线", "平行线"]),
    ],
    "圆锥曲线": [
        ("联立方程与韦达定理", ["韦达", "联立", "判别式"]),
        ("离心率", ["离心率"]),
        ("定点定值问题", ["定值", "定点"]),
        ("点差法", ["点差"]),
    ],
    # v4.6（用户口径）：**求轨迹类题目归「曲线与方程」，不属「圆锥曲线」**——
    # 教材「圆锥曲线与方程」一章里，「曲线与方程」讲求轨迹方程，「圆锥曲线」讲三种曲线的几何性质，
    # 二者应分开归属。真实数据里「定义与轨迹判定」被读图标到「圆锥曲线」是错的。
    "曲线与方程": [
        ("定义与轨迹判定", ["求轨迹方程", "轨迹方程", "点的轨迹", "到定直线", "到定点",
                            "距离之和", "距离之差", "定直线", "定点F", "轨迹"]),
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

# 题干是「密集数学词汇」文本，这些词在聊天口语里是有效信号，但在题干里遍地都是
# （「定点 B(6,2)」「投影」「斜率 k」「关于…对称」…），会让小黑板侧大量误命中。
# 故**仅在小黑板侧**屏蔽它们，聊天侧照旧使用。
BOARD_KW_BLOCK = {"定点", "投影", "斜率", "对称", "轨迹"}

# 题型 → 强制归属知识点（v4.6，用户口径）：有些题型无论读图把所属章节标成什么，都应归到固定大类。
# 用途：① 修正读图侧把「定义与轨迹判定」误标为「圆锥曲线」；② 兼容此前已归档的读图结果（无需重读图）。
ITEM_TOPIC = {"定义与轨迹判定": "曲线与方程"}


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
        # 有强制归属的题型（如「定义与轨迹判定」→ 曲线与方程）优先，避免被同句里的泛化题型抢走
        t, i, _ = max(out, key=lambda x: (2 if x[1] in ITEM_TOPIC else 0, x[2]))
        return [(t, i)]
    return [(t, i) for t, i, _ in out]


def clip_txt(t, n=38):
    import re as _re
    return _re.sub(r"\s+", " ", (t or "")).strip()[:n]


def fine_from_board(board, per_topic=6):
    """小黑板逐轮读图 → 细粒度薄弱点（v4.2 起**全量轮次**参与，不再只取一轮）。

    每轮读图结论自带 `problem`（具体题目）与 `stuck_at`（卡点），正是最细的证据。

    口径（v4.3 收紧，避免把「没作答」误当「知识点薄弱」）：
      · 跳过 `空白未提交` —— 没作答就没有知识点证据，属出勤 / 参与度，由作答分布与学习分层承接
      · 跳过 topic 为空 /「其他」/「未归类」—— 问卷、调查表等非学科内容
      · 跳过「完整作答 ＋ 正确 ＋ 无错」—— 无薄弱证据
    题型名优先取 FINE_TYPES 命中项（**优先落在本题知识点下**），其次取读图给出的**卡点原文**，
    两者都没有才退化为题面摘要 —— **绝不硬凑题型名**。
    返回 {知识点: [{"item","kind","at","evi","count"}]}，同 (知识点, 题型) 归并计数。
    """
    if not board:
        return {}
    res = {}
    sev = {}
    for d in board.get("details", []) or []:
        st = d.get("status") or ""
        ok = d.get("correct") or ""
        errs = d.get("errors") or []
        stuck = (d.get("stuck_at") or "").strip()
        prob = d.get("problem") or ""
        topic = (d.get("topic") or d.get("topic_knowledge") or "").strip()
        if st == "空白未提交" or topic in ("", "其他", "未归类"):
            continue
        if st == "完整作答" and ok == "正确" and not errs:
            continue
        # 题型名：在题面 + 卡点里找词表命中项，**同知识点的命中优先、其次命中的关键词更长者优先**。
        # BOARD_KW_BLOCK 里的泛词在题干语境下不可靠，小黑板侧一律不采信（聊天侧不受影响）。
        blob = f"{prob} {stuck}"
        best = None
        for t2, items2 in FINE_TYPES.items():
            for it2, kws in items2:
                hit = [k for k in kws if k in blob and k not in BOARD_KW_BLOCK]
                if hit:
                    # 有强制归属的题型（ITEM_TOPIC）不受读图 topic 影响，直接给最高优先级 ——
                    # 读图把「求轨迹」类题标成「圆锥曲线」是常见误标，此处以口径为准。
                    score = ((2 if it2 in ITEM_TOPIC else (1 if t2 == topic else 0)),
                             max(len(k) for k in hit))
                    if best is None or score > best[0]:
                        best = (score, it2)
        item = best[1] if best else ""
        if not item:
            item = (f"课堂作答卡点：{clip_txt(stuck, 20)}" if stuck
                    else f"课堂作答卡点：{clip_txt(prob, 18) or '未完成'}")
        evi = f"原题：{clip_txt(prob, 26)}" if prob else "小黑板作答"
        if stuck:
            evi += f"｜卡点：{clip_txt(stuck, 22)}"
        elif errs:
            evi += "｜错误：" + "、".join(errs[:2])
        # 归属知识点：读图的 topic 默认沿用，但若该题型有强制归属（ITEM_TOPIC），以强制值为准
        # ——「定义与轨迹判定」这类求轨迹题属「曲线与方程」，即使读图把它标成了「圆锥曲线」。
        topic = ITEM_TOPIC.get(item, topic)
        # hard = 硬证据（读图明确判「有错」，或存在非「没写完」类的实质错误）。
        # 供「主讲备课建议」区分「真的不会」与「只是没写完」—— v4.4 已确立该口径：
        # 「未完成收尾」属课堂节奏 / 书写速度，不是能力问题，不能作为「该知识点薄弱」的证据。
        _hard = (ok == "有错") or any(not is_finish_err(e) for e in errs)
        res.setdefault(topic, []).append(
            {"item": item, "kind": "小黑板", "at": d.get("lesson_date") or "",
             "evi": evi, "count": 1, "hard": _hard})
        # 严重度：读图明确判「有错」> 有卡点/待判 > 仅未完成收尾（用于同题型内挑代表条目）
        _s = 2 if ok == "有错" else (1 if stuck else 0)
        k2 = (topic, item)
        if _s > sev.get(k2, -1):
            sev[k2] = _s
    merged = {}
    for topic, rows in res.items():
        agg = {}
        for r in rows:
            k = r["item"]
            if k in agg:
                agg[k]["count"] += 1
                # 硬证据可累加：同题型下只要有一轮是实质错误，该题型就算「有实证错误」
                agg[k]["hard"] = bool(agg[k].get("hard")) or bool(r.get("hard"))
            else:
                agg[k] = dict(r)
        items = sorted(agg.values(), key=lambda r: (-r["count"], -sev.get((topic, r["item"]), 0)))
        merged[topic] = items[:per_topic]
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
                    slot[k]["hard"] = bool(slot[k].get("hard")) or bool(r.get("hard"))
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
