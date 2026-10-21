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
    "  A.（推荐）把后台网址 + 账号密码发给 WorkBuddy：它自动打开登录页、填好账号密码，\n"
    "     并把「图形验证码」截图发给你；你回复 4 位验证码，它自动登录并取到 token。\n"
    "  B.（兜底）你自己登录 https://stadminv2.xqmsx.cn/#/login 后按 F12 → Console 输入\n"
    "     document.cookie 回车，把 token= 那一串整段粘过来。\n"
    "（账号密码只用于本次登录、不会写入任何文件；token 约 24 小时过期）"
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


# v4.28：飞书表里学生名可能带**状态后缀**（`某生（回）` / `沈子琳（回）` / `王悦然（转）` /
#   `胡曦月（回退）` / `孙博言（退）`），而花名册侧是干净的 `某生` —— 只按 norm_name 对不上，
#   报告里这些人的「提交情况」会整行消失（实测 2026Q-SX-高二数学211层六C肖 2 人）。
#   ⚠️ 只做**只读匹配的兜底**（`report.py` 建索引时 setdefault，精确键优先），
#      不改 norm_name 本身 —— 聊天侧发言人归一依赖它，动它会牵连全部团队的既有分档。
_STATUS_SUFFIX_RE = re.compile(r"\s*[（(]\s*(?:退|回|转|回退|退课|转班|休学|停课)\s*[）)]\s*$")


def norm_name_loose(s):
    """宽松归一：norm_name 之后再去掉尾部状态括号。**仅用于「精确匹配不到时」的兜底**。"""
    return _STATUS_SUFFIX_RE.sub("", norm_name(s)).strip()


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
# v4.28：裸数字后面紧跟这些字 → 更像「题号 / 序号」，不是分数
#   （实测：老师原话「10求对称点咋能不会捏」被当成 10 分）
EXAM_BARE_BAD_TAIL = "求|道|这|那|题|页|步|行|讲|说|问|看|写|改|做|算|的|是|和|与|再|又|也|都|就"
EXAM_PAIR_RE = re.compile(r"(\d{2,3}(?:\.\d)?)\s*/\s*(\d{2,3})")        # 128/150
EXAM_SCORE_RE = re.compile(r"(\d{2,3}(?:\.\d)?)\s*分")                  # 128分
EXAM_BARE_RE = re.compile(r"(?<![\d.%/])(\d{2,3})(?![\d.%/])"
                          r"(?!\s*(?:人|名|位|个|题|分钟|小时|天|次|" + EXAM_BARE_BAD_TAIL + r"))")
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
# ⚠️ v4.28 误命中修复：这些词里**包含**考试关键词，但语义与考试无关 ——
#   实测踩坑：老师原话「10求对称点咋能不会捏…和咱们课上第一题也是**一模一样**滴」，
#   被「一模」命中 → 又把题号 10 当 10 分 → 得分率 7% → 触发「成绩偏低封顶需关注」。
#   这是**反向**误判（把人从正常打进需重点），比漏判更危险，必须挡掉。
EXAM_NAME_FAKE = {
    "一模": ["一模一样", "一个模", "模一样", "一个模子"],
    "二模": ["二模一样"],
    "三模": ["三模一样"],
}


def _exam_kws(s):
    """句子里命中的考试名关键词（已剔除「一模一样」这类假命中）。"""
    out = []
    for k in EXAM_NAME_KWS:
        if k not in s:
            continue
        if k in EXAM_NAME_FAKE and any(f in s for f in EXAM_NAME_FAKE[k]):
            # 「一模一样」：只有当该句**没有**出现真的考试语境时才整句放弃这个关键词
            rest = s
            for f in EXAM_NAME_FAKE[k]:
                rest = rest.replace(f, "")
            if k not in rest:
                continue
        out.append(k)
    return out


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
            kws = _exam_kws(s)
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


# ══════════════════════════════════════════════════════════════════════════
# v4.23 飞书表 → 学习信号（提交情况 / 作业评级 / 学情手记 / 考试成绩）
#
# 背景（用户要求）：飞书表的内容原来在报告里是**一个独立折叠小模块**，与学情分析并列。
# 但那才是团队每周真实记录的学习过程（作业交没交、评级升降、考试分数），应该**作为证据
# 参与分层结论与跟进动作**，而不是挂在卡片末尾让人自己看。
#
# 本模块的输出被两处共用（口径唯一，避免两处打架）：
#   ① `learning_level()` 的「⑤ 飞书提交」证据（改档位）；
#   ② `report.py` 的 ① 学习情况（提交情况行 / 需要关注点 / 评级轨迹 / 老师手记）。
# ══════════════════════════════════════════════════════════════════════════
FS_ITEM_ORDER = ["作业", "作业评级", "作业闭环", "打穿题", "打穿题测试", "几综模块测",
                 "精题", "出门测", "框架笔记", "课前测", "讲题视频", "总结"]
# 飞书「考试成绩」列里不是分数的写法（团队填「无月考」「考了」很常见）
FS_EXAM_NONE = ("无月考", "没月考", "没有月考", "未考", "没考", "没考呢", "考了", "待定",
                "未出分", "还没出", "没出分", "无成绩", "无数据")
# ⚠️ 含数字时只能按「无歧义短语」排除：单字「无」「/」会把 `70/150` 直接误杀（踩过）
FS_EXAM_NONE_HARD = ("无月考", "没月考", "没有月考", "未考", "没考", "考了", "待定",
                     "未出分", "还没出", "没出分", "无成绩", "无数据")
FS_EXAM_BAD = ("非常不理想", "很不理想", "不理想", "很差", "较差", "偏低", "退步",
               "砸了", "崩了", "没考好", "考砸")


def parse_fs_exam(txt, subject="数学"):
    """飞书表「考试成绩」**自由文本** → {text, shown, pct, level, assumed} 或 None。

    🔒 只解析、不推断人名、不补造分数；解析不出（「无月考」「考了」这种）就返回 None。
    支持形态：`70/150`、`141`、`91/100`、`104/150,班排7，年排68`、`非常不理想`。
    ⚠️ 无分母的数字要**显式标注按几分制推断**（与聊天侧 `extract_exam` 的 assumed 同口径），
       否则「141」被当成 141/150 还是 141/150 之外的制度会让得分率失真。
    """
    raw = str(txt or "").strip()
    if not raw:
        return None
    t = raw.replace(" ", "")
    has_digit = bool(re.search(r"\d", t))
    if not has_digit:
        if any(k in t for k in FS_EXAM_NONE):
            return None
        if any(k in t for k in FS_EXAM_BAD):
            return {"text": raw, "shown": raw, "pct": None, "level": "偏低", "assumed": ""}
        return None                    # 无法判断 → 当没有，不硬凑
    if any(k in t for k in FS_EXAM_NONE_HARD):
        return None                    # 「无月考，11.11考」这类 → 没有当前成绩

    pct, shown, assumed = None, "", ""
    # ⚠️ 先剔掉「班排7 / 年排68 / 第3名」这类**名次**，否则「年排68」会被当成 68 分
    #    （实测踩过：68/150 = 45% → 误判成绩偏低 → 封顶「需关注」）
    t_score = re.sub(r"(?:班排|年排|级排|校排|排名|名次|第|前)\s*[:：]?\s*\d+(?:\.\d+)?\s*"
                     r"(?:名|位|人)?", "", t)
    m = EXAM_PAIR_RE.search(t_score)                 # 128/150
    if m:
        v, full = float(m.group(1)), float(m.group(2))
        if int(full) in EXAM_PLAUSIBLE_FULL or full >= 100:
            pct, shown = v / full, f"{v:g}/{full:g}"
    if pct is None:
        m2 = (re.search(r"(?<![\d.])(\d{2,3}(?:\.\d)?)\s*分", t_score)
              or re.search(r"(?<![\d.])(\d{2,3}(?:\.\d)?)(?![\d.])", t_score))
        if m2:
            v = float(m2.group(1))
            full = EXAM_SUBJ_FULL.get(subject, 150)
            if 30 <= v <= full:                     # 低于 30 更像名次/日期，不当分数
                pct, shown, assumed = v / full, f"{v:g}", f"按 {full:g} 分制推断"
    if pct is None:
        if any(k in t for k in FS_EXAM_BAD):
            return {"text": raw, "shown": raw, "pct": None, "level": "偏低", "assumed": ""}
        return None
    return {"text": raw, "shown": shown, "pct": pct, "level": _exam_level(pct),
            "assumed": assumed}


# ══════════════════════════════════════════════════════════════════════════
# v4.37 飞书表「学生学情 / 问题点」列 → 可上卡片可见区的「学生问题」句
# ══════════════════════════════════════════════════════════════════════════
# ⭐ 用户要求：飞书表里识别到的「学生问题」要优先展示在卡片的**重要位置**，
#    不能只躺在「补充信息 → 老师手记」折叠区里（要点两次才看得到）。
#
# 🔒 反编造铁律：这里**只做切句 ＋ 分类，绝不改写 / 归纳 / 生成文本** ——
#    上屏的每一句都是表内原文的子串，可回飞书表逐字核对（点开还给出表名 + 行号）。
#    分类只用于「要不要上屏 / 排在第几条 / 用什么颜色」，不改变句子本身。
#
# 分类口径（三档，宁可漏判也不硬凑）：
#   hard = 行为 / 态度 / 沟通问题（不交作业、不写小黑板、联系不上、走神…）→ 红色
#   soft = 基础 / 状态 / 外部压力（基础差、跟不上、粗心、焦虑、作业多…）→ 橙色
#   其余（正面评价、报名转班优惠等教务事务）→ **全部丢弃**，不上屏
ISSUE_SPLIT = re.compile(r"[。；;！!？?\n]+|\s+/\s+|\s{2,}")
ISSUE_CLAUSE = re.compile(r"[，,]+")
# 句首日期 / 课次前缀（`10.2基础差` / `9.11：转985B`）—— 纯格式，剥掉不改语义
ISSUE_DATE_PREFIX = re.compile(
    r"^\s*(?:\d{1,2}[.月]\d{1,2}日?|\d{1,2}月\d{1,2}日?|第[一二三四五六七八九十\d]+次课)[：:、,，\s]*")
# 句首连接词（长句按逗号切开后残留的「但是 / 只是」）—— 同上，纯格式清理
ISSUE_LEAD_CONJ = re.compile(r"^(?:但是|但|不过|只是|就是|而且|然后|所以|而且说|另外)[，,\s]*")

ISSUE_HARD = [
    # 作业 / 提交
    "不交作业", "不写作业", "没交作业", "作业不交", "作业不写", "作业完成不好", "作业不积极",
    "不按时交", "不按时", "没交", "缺交", "漏交", "督促", "没时间写", "没时间看", "经常没有时间",
    # 课堂 / 小黑板
    "不写小黑板", "小黑板不写", "不爱写小黑板", "小黑板偷懒", "不写过程", "不爱写过程",
    "不写", "催不上", "催才", "催了才", "催着才", "要催", "不爱写",
    # 沟通 / 联系
    "联系不上", "找不到人", "不好联系", "不回消息", "不回群", "不回话", "不回复", "问不回",
    "不爱回", "不回", "不主动", "不问问题", "不提问", "不愿意沟通", "不沟通", "不吭声",
    # 态度 / 状态
    "不积极", "积极性不强", "不想学", "不愿学", "不主动学", "厌学", "惰性", "偷懒", "走神",
    "玩手机", "爱玩手机", "打游戏", "开科技", "上课睡觉", "课上睡觉", "爱睡觉", "控制不住",
    "不认真", "不太认真", "不专心", "注意力不集中", "不配合", "劝不动", "没收心",
    "不想开摄像头", "迟到", "逃课", "缺课", "不听", "抗拒", "抵触", "顶嘴", "态度不",
    "没咋做笔记", "没做笔记", "不做笔记",
    # v4.37 补充（实测张巍表漏判：失联 / 不回 / 交流为 0 这类教务最该知道的）
    "失联", "没理我", "不理我", "不理会", "交流为0", "交流为 0", "联系不到",
]
ISSUE_SOFT = [
    # 基础 / 方法 / 计算
    "基础差", "基础一般", "基础不好", "基础薄弱", "基础较弱", "零基础", "全忘了", "忘了",
    "出错频繁", "计算出错", "不应该出错", "出错", "计算错误", "计算问题", "粗心", "马虎",
    "不扎实", "不太规范", "不规范", "不熟练", "不够细致", "不细致", "不够仔细", "做题慢",
    "方法不会", "思路不清", "就不会", "没听懂", "听不懂", "没学会",
    # 状态 / 外部压力
    "跟不上", "费劲", "吃力", "有点难", "觉得难", "有难度", "难度大", "节奏快", "作业多",
    "写到12点", "写得挺老晚", "焦虑", "担心", "着急", "压力", "情绪", "畏难", "不适应",
    "疲惫", "心态不稳", "发挥不好", "发挥不太好", "考的不好", "考得不好", "考的不理想",
    "没考好", "考的差", "成绩不好", "家长不好联系", "家长会生气", "家长有些焦虑",
    "家长比较担心", "有想法调班", "想退费", "怕难", "怀疑",
    # 「作业完成得不好」的各种写法（表内是自由文本，写法不统一）
    "完成的不好", "完成的不太好", "完成不好", "做的不好", "做得不好",
]
# 明确的「已经好了」收尾 —— 只认强收尾词。
# ⚠️ 不能用「能跟上」这类弱正面词去覆盖：实测「怀疑自己能不能跟上」会被误判成正面而丢掉真问题。
ISSUE_RELIEF = ["好转", "跟上了", "适应了", "稳定了", "没问题了", "现在没什么问题", "已经适应"]


def fs_issues(note):
    """飞书表手记 → [(严重度, 子句, 整句原文)]，严重度 0=行为/态度，1=基础/状态。

    ⚠️ 返回的句子是**表内原文子串**（仅剥日期/课次前缀与句首连接词、去首尾标点），
       不拼接、不改写 —— 报告里点开每条都会给出「飞书表『sheet 名』第 N 行」。
    ⚠️ 长句（家长口述转写常 100+ 字）先按逗号切，只保留命中问题的子句 ——
       否则整段会被塞进一枚 chip，可见区直接失守。
    """
    if not note:
        return []
    out, seen, cur = [], set(), str(note)

    def push(sent, origin):
        s = ISSUE_LEAD_CONJ.sub("", ISSUE_DATE_PREFIX.sub("", sent)).strip(" ，,、：:;；")
        if len(s) < 3:
            return
        hard = [k for k in ISSUE_HARD if k in s]
        soft = [k for k in ISSUE_SOFT if k in s]
        if not hard and not soft:
            return
        if not hard and any(k in s for k in ISSUE_RELIEF):
            return                              # 「先有点吃力，后面跟上了」→ 已缓解，不报
        key = re.sub(r"[\s，,。；;、]", "", s)
        if key in seen:
            return
        seen.add(key)
        # 第三个元素永远给**整句原文**（长句按逗号切出来的子句另存）——
        # 卡片上 chip 只放子句（读得快），展开「表内原话」放整句（不丢转折与补充）。
        _full = ISSUE_LEAD_CONJ.sub("", ISSUE_DATE_PREFIX.sub("", origin)).strip(" ，,、：:;；")
        out.append((0 if hard else 1, s, _full or s))

    for _sent in ISSUE_SPLIT.split(cur):
        _sent = (_sent or "").strip()
        if not _sent:
            continue
        if len(_sent) <= 40:
            push(_sent, _sent)
            continue
        for _c in ISSUE_CLAUSE.split(_sent):
            if len(_c.strip()) >= 3:
                push(_c, _sent)
    out.sort(key=lambda x: x[0])
    return out


def _fs_risks(st):
    """从飞书信号里挑出「需要关注的点」（最多 3 条，按严重度排序）。

    这是用户要的核心：飞书不该只是数据展示，要**说出哪里有问题**。
    排序：近 N 次未交 / 某项全未交 ＞ 评级下滑 / 单项完成率低 / 考试偏低 ＞ 合计提交率低。
    """
    out = []
    for g in st["ratings"]:
        ta = int(g.get("tailAbsent") or 0)
        if ta >= 2:
            out.append((0, f'{g.get("item")} 最近连续 {ta} 次未提交'))
    # 已被「连续未交」点名的项，不再重复报它的完成率（同一条链说三遍谁都读不下去）
    _tail_items = {g.get("item") for g in st["ratings"] if int(g.get("tailAbsent") or 0) >= 2}
    for i in st["zeros"]:
        if i["item"] in _tail_items:
            continue
        out.append((0, f'{i["item"]} {i["total"]} 次全部未交'))
    for g in st["ratings"]:
        if int(g.get("tailAbsent") or 0) < 2 and g.get("trend") == "down":
            out.append((1, f'{g["item"]} 评级 {g.get("first")} → {g.get("last")}，呈下滑'))
    w = st["worst"]
    if w and 0 < w["pct"] <= 60 and w["item"] not in _tail_items:
        out.append((1, f'{w["item"]} 只完成 {w["done"]}/{w["total"]}（{w["pct"]:.0f}%）'))
    ex = st["exam"]
    if ex and ex.get("level") == "偏低":
        p = f'（得分率 {ex["pct"]:.0%}）' if ex.get("pct") is not None else ""
        out.append((1, f'考试 {ex["text"]}{p}，偏低'))
    if st["pct"] is not None and st["pct"] < 60:
        w = st["worst"]
        # 若「最弱项」就是全部记录（如只有「作业 1/2」这一项），上面已说过，不再重复同一句
        _dup = (w and w["total"] == st["D"] + st["U"] and 0 < w["pct"] <= 60
                and abs(st["pct"] - w["pct"]) < 0.01)
        if not _dup:
            out.append((2, f'各项合计提交率仅 {st["pct"]:.0f}%（{st["D"]}/{st["D"] + st["U"]}）'))
    seen, res = set(), []
    for sev, txt in sorted(out, key=lambda x: x[0]):
        if txt in seen:
            continue
        seen.add(txt)
        res.append((sev, txt))
    return res[:3]


def fs_stats(fs):
    """飞书表记录 → 结构化学习信号。无记录 / 只有档案无课次 → None。

    ⚠️ `fs` 是 adapt_feishu.py 产出的单条 record（done/undone/ratings/meta/nLessons）。
       返回 dict，供 `learning_level()` 与 `report.py` 共用。
    """
    if not isinstance(fs, dict) or not fs:
        return None
    done, undone = fs.get("done") or {}, fs.get("undone") or {}
    have = set(done) | set(undone)
    items = []
    for k in [x for x in FS_ITEM_ORDER if x in have] + sorted(k for k in have if k not in FS_ITEM_ORDER):
        d, u = int(done.get(k) or 0), int(undone.get(k) or 0)
        t = d + u
        if t <= 0:
            continue
        items.append({"item": k, "done": d, "undone": u, "total": t, "pct": d / t * 100})
    ratings = []
    for k in sorted(fs.get("ratings") or {}):
        g = (fs.get("ratings") or {}).get(k)
        if isinstance(g, dict) and g.get("seq"):
            ratings.append(g)
    meta = fs.get("meta") or {}
    exam = parse_fs_exam(meta.get("examScore") or meta.get("exam_score"))
    note = str(meta.get("note") or "").strip()
    if not items and not ratings and not exam and not note:
        return None
    D = sum(i["done"] for i in items)
    U = sum(i["undone"] for i in items)
    st = {
        "items": items, "D": D, "U": U,
        "pct": (D / (D + U) * 100) if (D + U) else None,
        "worst": min(items, key=lambda i: (i["pct"], -i["total"])) if items else None,
        "zeros": [i for i in items if i["pct"] == 0 and i["total"] >= 2],
        "ratings": ratings, "exam": exam, "note": note,
        "nLessons": fs.get("nLessons") or 0,
        "sheet": fs.get("sourceSheetTitle") or "", "row": fs.get("sourceRow"),
        # 原始引用（渲染评级序列条 / 背景信息时要用）
        "ratingsRaw": fs.get("ratings") or {}, "metaRaw": meta,
    }
    st["risks"] = _fs_risks(st)
    # v4.37：表内「学生学情/问题点」→ 可直接上卡片可见区的问题句（只切句分类，不改写）
    st["issues"] = fs_issues(note)
    # 评级结论徽章（与 report.rating_block 同一优先级：先连续未交，再趋势）
    st["badges"] = []
    for g in ratings:
        ta = int(g.get("tailAbsent") or 0)
        n_r = int(g.get("nRated") or 0)
        if ta >= 2:
            st["badges"].append((g["item"], f"近 {ta} 次未交", "down"))
        elif not n_r:
            st["badges"].append((g["item"], "无评级", "flat"))
        elif n_r < int(g.get("minRated") or 3):
            st["badges"].append((g["item"], "样本不足", "flat"))
        elif g.get("trend") == "up":
            st["badges"].append((g["item"], "上升 ↑", "up"))
        elif g.get("trend") == "down":
            st["badges"].append((g["item"], "下滑 ↓", "down"))
        else:
            st["badges"].append((g["item"], "稳定 →", "flat"))
    return st


def _fs_evidence(st, chat_exam_lv="未知"):
    """飞书信号 → 分层加减分。返回 (rows, score, exam_override)。

    ⚠️ 打分原则（避免「同一个因重复罚」）：
      · 合计提交率一档最多 ±2；单项全未交只在**整体还行**时补 -1（整体已低就不再叠）；
      · 评级趋势 / 连续未交一档 -1；
      · 成绩：**聊天侧没有成绩时才用飞书成绩**（不重复计分，只补空白）。
    """
    rows, score, exam_override = [], 0, None
    if st is None:
        return rows, score, exam_override

    p = st["pct"]
    n_rec = st["D"] + st["U"]
    if p is not None:
        # ⚠️ 最小样本：记录只有 1~2 条时（表刚开 / 学生才进班）不按百分比扣分 ——
        #    0/1 与 0/5 的「0%」含义完全不同，前者说明不了学习状态（v4.23 实测踩过）。
        if n_rec < 3:
            rows.append(("飞书提交", f'各项合计 {st["D"]}/{n_rec}（{p:.0f}%），'
                                    f'记录仅 {n_rec} 条、样本不足，不参与加减分', 0))
        else:
            # ⚠️ 只扣分、不因「全交」加分（v4.23 定）：交作业是**底线**，不是学习效果。
            #    实测给 +1 会把一批「没有小黑板、没有成绩、只是作业交齐」的学生整体抬到「良好」，
            #    与联席的直观不符。这里只作正向记录，档位仍由风险证据决定。
            d0 = -2 if p < 50 else (-1 if p < 70 else 0)
            _txt = f'各项合计 {st["D"]}/{n_rec}（{p:.0f}%）'
            if d0 == 0 and p >= 90:
                _txt += '，提交完整（只记正向，不加分 —— 交作业是底线）'
            rows.append(("飞书提交", _txt, d0))
            score += d0
            if st["zeros"] and p >= 50:
                z = "、".join(f'{i["item"]} {i["total"]} 次全空' for i in st["zeros"][:2])
                rows.append(("飞书提交", f"{z}（整体尚可但单项全缺）", -1))
                score -= 1

    for g in st["ratings"]:
        item = g.get("item")
        ta = int(g.get("tailAbsent") or 0)
        if ta >= 2:
            rows.append(("飞书提交", f'{item} 最近连续 {ta} 次未提交', -1))
            score -= 1
        elif int(g.get("nRated") or 0) >= int(g.get("minRated") or 3):
            if g.get("trend") == "down":
                rows.append(("飞书提交", f'{item} 评级 {g.get("first")} → {g.get("last")}，呈下滑', -1))
                score -= 1
            elif g.get("trend") == "up":
                rows.append(("飞书提交", f'{item} 评级 {g.get("first")} → {g.get("last")}，呈上升', 1))
                score += 1

    ex = st["exam"]
    if ex and chat_exam_lv in ("未知", "", None):
        _p = f'（得分率 {ex["pct"]:.0%}{"，" + ex["assumed"] if ex.get("assumed") else ""}）' \
            if ex.get("pct") is not None else ""
        if ex.get("level") == "高":
            rows.append(("成绩", f'飞书表考试 {ex["text"]}{_p}，成绩好', 3))
            score += 3
            exam_override = ("floor", "blue")
        elif ex.get("level") == "中":
            rows.append(("成绩", f'飞书表考试 {ex["text"]}{_p}，成绩中等', 1))
            score += 1
        elif ex.get("level") == "偏低":
            rows.append(("成绩", f'飞书表考试 {ex["text"]}{_p}，成绩偏低', -3))
            score -= 3
            exam_override = ("cap", "orange")
    elif ex:
        rows.append(("飞书提交", f'飞书表考试 {ex["text"]}（聊天侧已有成绩，此处并列参照、不重复计分）', 0))
    return rows, score, exam_override


def fs_confirms_submit(st):
    """⭐ v4.28：飞书表是否**已确认提交**（≥3 条学情记录且提交率 ≥80%）。

    用途：校正聊天侧「催交后未见提交」—— 那条证据的真实语义是**群里没回话**，
    不等于没写作业。飞书表是老师逐格打勾的提交事实（权威）。
    ⚠️ 判据集中在这里，`learning_level()` 与 `report.four_dims()` 共用，避免两处漂移。
    ⚠️ 门槛只要求 **≥1 条**记录（不同于 fs 自身扣分要求的 ≥3 条）：这里只用来**撤销**
       一个「没交作业」的负面结论，有一条已交的反证就够；不用于加分，不存在放大风险。
    """
    if not isinstance(st, dict):
        return False
    p = st.get("pct")
    n = int(st.get("D") or 0) + int(st.get("U") or 0)
    return p is not None and n >= 1 and p >= 80


def learning_level(s, board, fs=None):
    """学习分层（v4.4 四类证据；v4.23 起并入「⑤ 飞书表提交」共五类）。

    ① 小黑板 ② 作业闭环 ③ 老师反馈 ④ 学生成绩 ⑤ 飞书表（提交率 / 作业评级趋势 / 考试）。
    `fs` 传 `fs_stats()` 的结果；不传或 None 时**与本改动前完全一致**（向后兼容）。

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
        # 聊天侧没有成绩但飞书表里有 → 说明来源切换，避免依据块里出现「没看到成绩」+「成绩偏低」两条互相打架
        add("成绩", ("聊天侧未见成绩信息（改用飞书表「考试成绩」，见下条）"
                     if (fs and fs.get("exam")) else "窗口内未见成绩信息（月考 / 期中成绩收集）"), 0)

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
    # ⭐ v4.28：聊天侧「催交未见提交」的语义是**群里没回话**，不等于没写作业 ——
    #   飞书表是老师**逐格打勾**的提交事实（权威）。实测 2026Q-SX-高二数学211层六C肖：
    #   50 人里 28 人同一张卡上既写「催交 2 次、2 次未见提交」又写「飞书 7/7（100%）」，
    #   自相矛盾，且这 -2 分是把人误推进「需重点关注」的主要来源。
    #   口径：飞书有 ≥3 条学情记录且提交率 ≥80% 时，这条**不扣分**，改写为「群里未回话」。
    _fp = (fs or {}).get("pct")
    _fn = int((fs or {}).get("D") or 0) + int((fs or {}).get("U") or 0)
    _fs_confirms = fs_confirms_submit(fs)
    if un > uo:
        miss = un - uo
        if _fs_confirms:
            add("作业", f"催交 {un} 次、群里 {miss} 次没回话；但飞书表 {int((fs or {}).get('D') or 0)}/{_fn}"
                        f"（{_fp:.0f}%）有提交记录 → 属「不回消息」，不按未交扣分", 0)
        else:
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

    # ---------------- ⑤ 飞书表（团队自维护：交没交 / 评级升降 / 考试） ----------------
    # 用户要求：飞书表不是「额外附一张表」，而是**学习过程的记录** —— 作业交没交、
    # 评级在升还是在降、考了多少分。这些进了分层，卡片右上角的结论才和实际一致。
    _frows, _fsc, _fov = _fs_evidence(fs, ex_lv)
    for _src, _txt, _d in _frows:
        add(_src, _txt, _d)
    if _fov:
        _kind, _lk = _fov
        if _kind == "floor":
            floor_key = _lk
        else:
            cap_key = _lk

    # ---------------- 落档（成绩托底 / 封顶） ----------------
    key = level_by_score(score)
    if floor_key and LEVEL_RANK[floor_key] > LEVEL_RANK[key]:
        ev.append(("成绩", f"按成绩托底：不低于「{LEVEL_CN[floor_key]}」", 0))
        key = floor_key
    if cap_key and LEVEL_RANK[cap_key] < LEVEL_RANK[key]:
        ev.append(("成绩", f"按成绩封顶：不高于「{LEVEL_CN[cap_key]}」", 0))
        key = cap_key
    return key, LEVEL_CN[key], ev


def att_effective(a):
    """有效出勤口径（v3.8 定，v4.18 统一出口）：**看回放也算出勤**。

    返回 (有效出勤 eff, 仅靠回放补齐 pb_only, 看回放总数 pb, 总节数 n)。

    取值优先级：显式字段 `effectiveAttendCount` → 由 `classes` 逐节重算 → 退回聚合字段。

    ⚠️ 为什么要单独有这个出口：`attendCount` 是**纯到课数（不含回放）**。直接拿它做出勤判定，
    会把「没到课但看了回放」的学生判成「未出勤」—— 实测联席视角 10 人、教务视角 16 人踩中，
    其中 5 人因此在失联分档里被写成「失联」（实为「出勤但无沟通」）。
    凡是要判断「这个学生到底算不算上课了」，一律走这里，不要直接读 attendCount。
    """
    cls = a.get("classes") or []
    n = a.get("lessonCount")
    if n is None:
        n = len(cls)
    if cls:                                   # 有逐节明细：以明细为准（最可靠）
        att = sum(1 for c in cls if c.get("isAttend") == 1)
        pb = sum(1 for c in cls if c.get("isPlayback") == 1)
        pb_only = sum(1 for c in cls if c.get("isAttend") != 1 and c.get("isPlayback") == 1)
        eff = a.get("effectiveAttendCount")
        if eff is None:
            eff = att + pb_only
        return eff, pb_only, pb, n
    pb = a.get("playbackCount") or 0          # 无明细：退回聚合字段（旧链路产物）
    pb_only = a.get("playbackOnlyCount") or 0
    eff = a.get("effectiveAttendCount")
    if eff is None:
        eff = (a.get("attendCount") or 0) + pb_only
    return eff, pb_only, pb, n


def att_stats(a):
    """出勤口径（v3.8）：看回放也算有效出勤；只有「既没到课也没看回放」才算真缺课。

    返回 (总节数, 有效出勤, 到课, 仅回放补齐, 看回放总数, 真缺课, 缺课节次列表)
    """
    n = a["lessonCount"]
    cls = a.get("classes", []) or []
    att = sum(1 for c in cls if c.get("isAttend") == 1)
    eff, pb_only, pb, _ = att_effective(a)   # v4.18：口径统一到 att_effective，避免各处复刻
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
                 "再好好想", "是不是写错", "是不是算错", "问题所在",
                 "没看出来", "做错", "你是不是写", "你怎么写", "你这一步", "你上面"]
# ⚠️ v4.47：以下两词是**通用提醒**，不指向该生 —— 「这类题要注意」「一定要考虑斜率不存在」
#   常是老师讲方法时顺口一说（联席反馈「我只是提一嘴它也会分析进去」）。
#   仅当同句出现学生指代（你/这道题/这个题）时才算指出问题。
TEA_POINT_WEAK_KWS = ["要注意", "一定要考虑", "注意一下", "得注意", "留意"]
TEA_POINT_WHO = ["你", "这道题", "这个题", "这题", "该生"]
# 老师这句话若含「假设 / 询问」语气 → 不是在指出问题（如「看有哪里是否做错」）
TEA_POINT_EXCLUDE = ["是否", "如果", "假如", "会不会", "对不对", "有没有", "要是"]
# 学生表达困惑 / 卡点的语气词
# ⚠️ v4.47：删掉「为什么」——「我现在逐渐理解了为什么圆锥曲线做多了就不想念立体几何了」
#   是感慨而非卡点，却把「立体几何」判成了薄弱。真正的疑问交给「带问号/吗呢」的
#   STU_ASK_WEAK_KWS 兜，以及「不会/不懂/没明白」这些强信号。
STU_LOST_KWS = ["不会", "不懂", "没懂", "不明白", "卡住", "求思路", "怎么做", "怎么算",
                "看不懂", "没看出", "哪里错", "急急急"]
# 弱疑问词：必须配疑问语气（？/吗/呢/么）才算「提问」，单独出现不算
STU_ASK_WEAK_KWS = ["为什么", "咋", "怎么", "啥", "什么意思"]
# 正面语境 —— 学生自述「做得好的地方 / 已掌握」时提到的题型属**优势**，不能当薄弱点
POSITIVE_KWS = ["做得比较好", "做得不错", "比较顺", "已经掌握", "都会了", "思路是对的",
                "做对了", "答对了", "完全正确", "没什么问题", "没有问题", "比较熟练",
                # v4.47：否定式正面（「没算错吧」「空间向量没问题」「课后测全对」）
                "没问题", "没错", "没算错", "没做错", "全对", "都对了", "掌握"]
# ⚠️ v4.47 **未来时 / 进度预告**：老师在群里预告后续内容（「之后学导数的时候我们也会碰见」
#   「下节课讲圆锥曲线」「还没学到数列」）**不是**该生当前的薄弱点。联席反馈的核心误判即此。
#   判定规则见 is_future()：整句是预告、且不含明确的当下卡点时才排除。
FUTURE_KWS = ["之后学", "以后学", "后面学", "接下来学", "下次学", "下节课学", "下节讲", "下次讲",
              "即将学", "将要学", "马上学", "将来", "会学到", "会讲到", "会碰见", "会碰到",
              "会遇到", "会考到", "后面讲", "之后再讲", "还没学", "没有学", "没学到", "未学",
              "没接触", "还没讲", "没讲到", "还没讲到", "到时候", "届时", "寒假学", "春季学",
              "开学后", "才学", "再讲"]
# 「当下明确卡点」—— 出现这些时，即使句子里带未来时词，也按真问题算（不能被预告逻辑吞掉）
FUTURE_STRONG_PROB = ["不会", "不懂", "没懂", "不明白", "做错", "算错", "错在", "卡住", "没思路"]
# 群通知 / 事务性消息（不参与薄弱点提取）
NOTICE_KWS = ["上课通知", "课表", "讲义", "报名", "扫码", "教室", "群公告", "放假", "调课安排",
              "作业记得", "记得完成", "记得交", "上课时间", "考试范围",
              "巩固知识", "做一下巩固", "及时做一下"]

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
    """正面语境（自述已掌握 / 做得较好 / 明确否定有问题）。"""
    return any(k in (text or "") for k in POSITIVE_KWS)


def is_future(text):
    """v4.47：是否为「未来时 / 进度预告」语境（老师说之后才学、下节课才讲）。

    联席反馈：只是预告「之后学导数的时候我们也会碰见」，报告就把「导数」判成该生薄弱。
    规则：句中出现预告词，**且**没有出现当下的明确卡点（不会/做错…）→ 视为预告，不参与薄弱判定。
    """
    s = text or ""
    if not any(k in s for k in FUTURE_KWS):
        return False
    if any(k in s for k in FUTURE_STRONG_PROB):
        return False        # 「之后学导数会用到，但我不会」→ 仍是真问题
    return True


def is_tea_point(text):
    """是否是老师「具体指出问题」的句子（不能带假设/询问语气）。

    v4.47：通用提醒词（要注意 / 一定要考虑）须指向该生（含「你 / 这道题」）才算，
    否则「这类题要注意先看定义域」这类讲课话术会把全班每个学生都判成薄弱。
    """
    s = text or ""
    if any(k in s for k in TEA_POINT_EXCLUDE):
        return False
    if any(k in s for k in TEA_POINT_KWS):
        return True
    if any(k in s for k in TEA_POINT_WEAK_KWS):
        return any(k in s for k in TEA_POINT_WHO)
    return False


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
