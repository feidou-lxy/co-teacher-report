"""步骤6：信号提取（聊天 -> 学情结构化）。

仅分析 --window 起点之后、--end（可选，含当天）之前的聊天（默认 2026-09-01 起、不限止）。
提取：薄弱知识点、出勤/作业/提问/情绪信号、配合度（学生自驱 vs 家长支持，启发式）、
未闭环疑问、**近 N 天待回应（联席忘记回复 / 学生问题未解决，v3.9）**、总评与待办。
配合度数据源未区分发言者 ID，为启发式估算。

To-Do 按角色生成（--co 联席 / --edu 教务）：
  - 联席：面向「跟课辅导 / 答疑 / 作业订正 / 出勤补课」；
  - 教务：面向「家长沟通 / 反馈学生问题 / 建立信任 / 推动续报」。

输出 <workdir>/analysis.json ：{ studentName: {...} }
"""
import argparse
import datetime
import json
import re
import sys
from collections import Counter

sys.path.insert(0, sys.path[0])
import common

WINDOW_DEFAULT = "2026-09-01"

ATT = ["请假", "病", "发烧", "医院", "回放", "没来", "缺课", "到课", "出勤", "迟到", "早退", "身体", "不舒服", "康复", "休"]
HW = ["作业", "提交", "交作业", "没写", "没做", "不会做", "漏交", "订正", "打卡", "练习", "错题", "刷题", "补"]
Q = ["?", "？", "怎么", "为什么", "如何", "不懂", "听不懂", "不会", "求解", "求", "请教", "迷茫", "卡住", "思路", "咋"]
# 真实「提问」判定（用于未闭环）：比 Q 更严，剔除「求」「不会」「思路」等描述性易误命中词
Q_ASK = ["?", "？", "不懂", "听不懂", "没听懂", "不理解", "没明白", "不会做", "不会写", "求解",
         "怎么", "为什么", "为啥", "如何", "请教", "没思路", "卡住", "咋", "求助", "帮忙看", "对不对"]
POS = ["谢谢", "感谢", "辛苦", "麻烦您", "麻烦你", "感激", "太好了", "棒"]
NEG = ["焦虑", "着急", "担心", "烦躁", "抱怨", "不满意", "无奈", "心烦", "压力", "崩溃", "难受", "愁", "委屈"]
# —— 联席四维关注（v3.7）：互动 / 催交作业 / 批改后订正 / 提问意愿 ——
# 说明：这四维是「联席为学习效果负责」的直接抓手；出勤/请假归属教务关切，不进入联席的「最该盯」。
URGE = ["交作业", "作业交", "交一下", "提交", "补交", "还没交", "没交", "催", "记得交", "别忘了",
        "抓紧", "按时", "今天交", "尽快交", "别忘", "要交", "快交"]
SUBMIT = ["交了", "已交", "提交了", "已经交", "交了作业", "发你了", "发了", "发了哦", "上传了",
          "写完了", "做完了", "完成了", "拍照发了", "拍给你", "传了", "发了老师"]
FIX_REQ = ["订正", "改正", "改一下", "改下", "重做", "重新写", "重新做", "改错", "错题",
           "再算一遍", "再看一遍", "重新算", "更正", "纠正"]
FIX_ACK = ["改好了", "订正了", "改正了", "改完", "重做了", "重新写了", "重新做了", "已改", "改过来了",
           "改好了老师", "更正了", "改过了"]
# 老师反馈（只统计老师消息）：用于「学习情况分层」的第三类证据
TEA_POS = ["全对", "很好", "不错", "有进步", "进步", "正确", "规范", "做得好", "继续保持", "完全正确", "很棒"]
TEA_NEG = ["错了", "算错", "不对", "错误", "粗心", "漏了", "没交", "没写完", "没做完", "没掌握", "薄弱"]

STU_SELF = ["我自己", "我这道题", "我算", "我听", "我做", "我提交", "我交了", "我错了", "我漏",
            "我忘", "我这次", "我自己做", "我理解", "我懂", "我试", "我写", "我的作业", "我来看"]
PARENT = ["妈妈", "爸爸", "家长", "我帮", "我替", "帮他", "替他", "已交", "帮交", "孩子", "我家",
          "他作业", "他今天", "他这个", "他没", "他不会", "他那个", "他有点", "他最近"]

# —— 「无需回复」消息（礼节性收尾：确认/致谢/应承）——
# 这类消息不构成待回应提问，也不计入实质互动；统一在未闭环判定与配合度统计中剔除。
ACK_SET = {
    "好的", "好", "好的好的", "好的呢", "好嘞", "好滴", "好勒", "好哒", "好呀", "好的呀", "好噢", "好喔",
    "行", "可以", "没问题", "没事", "没事的", "收到", "收到啦", "收到谢谢", "收到谢谢老师", "谢谢", "谢谢啦",
    "谢谢老师", "谢谢您", "谢谢啊", "谢谢哈", "谢谢老师啦", "谢谢老师辛苦了", "老师谢谢", "感谢", "感谢老师",
    "感谢您", "感恩", "感谢感谢", "谢谢谢谢", "多谢", "有劳", "老师辛苦", "老师辛苦了", "辛苦", "辛苦了",
    "麻烦您", "麻烦你", "麻烦了", "您辛苦", "明白", "明白啦", "明白了", "明白了谢谢老师", "了解", "了解了",
    "知道了", "知道啦", "晓得", "晓得了", "懂了", "懂啦", "懂了谢谢", "嗯", "嗯嗯", "嗯呢", "嗯呐", "嗯好",
    "嗯好的",     "ok", "okay", "ok啦", "ok谢谢", "不客气", "好的晓得", "好滴谢谢", "好嘞谢谢老师",
    # 简短确认应答（同样无需回复）
    "是的", "是滴", "对", "对的", "对呀", "哦对的", "嗯对", "是的呢", "在的", "加了", "进了",
    "来了", "来了来了", "没有", "不是", "收到收到", "好的收到", "嗯嗯好的", "好的老师",
    "我知道了", "好的我知道了", "好的好的我知道了", "我晓得了", "我明白了", "好我知道了",
}
# 组合词表：整条消息若可由这些词拼接而成（如「好的谢谢老师」「明白了，谢谢老师🍵🍵」），也判为礼节性
ACK_TOKENS = ["好的好的", "谢谢老师", "老师辛苦了", "老师辛苦", "收到", "谢谢", "感谢", "多谢", "感恩",
              "辛苦", "麻烦您", "麻烦你", "有劳", "明白", "了解", "知道", "晓得", "懂了", "没问题",
              "不客气", "好的", "好嘞", "好滴", "好哒", "好呀", "好噢", "好喔", "好的呀",
              "嗯", "ok", "okay", "好", "行", "可以", "没事",
              "老师", "您", "了", "的", "呢", "啦", "呀", "吧", "哦", "啊", "嘛", "哒", "噢", "喔", "哟",
              "呦", "咧", "咯", "呐", "哈"]
# 表情/符号（emoji、带圈字符、几何图形、箭头、变体选择符等）：礼节判定前先剥离，
# 避免「明白了，谢谢老师🍵🍵🍵」因尾巴上的表情而漏判为实质消息
EMOJI_RE = re.compile(
    "["
    "\u2190-\u21FF"          # 箭头
    "\u2300-\u23FF"          # 杂项技术符号
    "\u2460-\u24FF"          # 带圈数字/字母
    "\u25A0-\u27BF"          # 几何图形 / 杂项符号 / 装饰符号（✓✔☀✦…）
    "\u2B00-\u2BFF"          # 杂项符号与箭头
    "\u3030\u303D\u3297\u3299\u00A9\u00AE\u2122"
    "\U0001F000-\U0001FAFF"  # 麻将/扑克/象形文字补充/扩展（😂👍🍵🥳…）
    "\U0001F1E6-\U0001F1FF"  # 区域指示符（国旗）
    "\uFE0F\u200D\u20E3"     # 变体选择符 / 零宽连接符 / 键帽
    "]+"
)
# 微信「文本表情」代号（[玫瑰][抱拳][捂脸][呲牙][合十]…）：与 emoji 等价，判定前先剥离
ACK_EXPR_RE = re.compile(r"\[[^\[\]]{1,6}\]")
# 致谢/致歉类词：与极短称呼搭配时（「谢谢丹姐」「辛苦老师了」）同样视为无需回复
THANKS_WORDS = ("谢谢", "感谢", "多谢", "感恩", "辛苦", "麻烦您", "麻烦你", "有劳")
# 占位噪声（无正文、不可解析的消息类型），不参与任何分析
NOISE_KEYS = ("图片", "语音", "视频", "文件", "该消息类型", "revoke", "撤回", "表情", "链接", "位置",
              "动画", "名片", "小程序", "合并转发", "系统消息", "文件传输", "红包", "转账")

# —— 响应时效（v4：完全由聊天记录自行推算，不依赖后台「超时/是否回复」标记）——
# 口径：一轮「学生/家长实质提问 → 由老师消息打断」记一次等待，取窗口内最大值。
# 后台 isTimeout / isReplied 实测与聊天事实不符（例如 6 分钟即回复的群被标「超时 19小时」），故弃用。
RESP_SLOW_H = 24.0     # ≥ 24 小时才未回应 → 判「响应迟缓」，计入定档红
RESP_WARN_H = 12.0     # ≥ 12 小时 → 展示为「偏慢」提示（多为深夜提问次日回复），不计定档
# 老师已在回复中说明缘由（假期/休息日/稍后细看）→ 属「已沟通的延迟」，不判迟缓、不计定档
RESP_EXCUSE = ("休息", "放假", "假期", "节后", "调休", "这两天", "晚一点", "晚些", "晚些时候",
               "尽快", "稍后", "抽时间", "回头", "出差", "忙完")

TOPICS = {
    "立体几何/空间向量": ["立体几何", "空间向量", "法向量", "建系", "异面直线", "二面角",
                          "外接球", "线面角", "面面角", "三视图", "线面垂直", "面面平行"],
    "直线和圆": ["直线和圆", "直线方程", "圆的方程", "圆心距", "圆的切线", "位置关系"],
    "函数与导数": ["函数", "定义域", "值域", "解析式", "奇偶性", "零点", "单调性",
                   "导数", "求导", "极值", "切线方程"],
    "数列": ["数列", "等差", "等比", "通项", "前n项和"],
    "圆锥曲线": ["椭圆", "双曲线", "抛物线", "离心率", "焦点", "准线"],
    "三角函数": ["三角函数", "正弦", "余弦", "诱导公式", "辅助角"],
    "概率统计": ["概率", "统计", "排列组合", "期望", "方差", "频率分布"],
    "解三角形": ["解三角形", "正弦定理", "余弦定理"],
    "不等式/参数": ["不等式", "恒成立", "含参", "参数讨论", "取值范围"],
    "计算/粗心": ["粗心", "算错", "计算", "符号", "漏解", "增根", "少一种"],
}

# ============================================================================
# v4.31 「薄弱知识点」口径重建（业务反馈：没学过的知识点被当成薄弱点）
#
# 旧实现（v4.0 起）是 **整群纯词频统计 + 固定错因映射**：
#   tc[name] += all_txt.count(kw)  →  取前 3  →  ERR_MAP[name] 套一句固定错因。
# 实测三处硬伤（2026Q-SX-高一数学B版清北层六E胡，9 个群逐条核过原文）：
#   ① 老师**讲课录音**反复出现的词被算成「学生薄弱」——
#      某生「函数与导数」的全部依据 = 老师一句「写函数的时候，需要写一下定义域」（1 次「函数」）；
#   ② **事务/进度/收费**类消息被算进去——某生「概率统计」来自家长问
#      「你发的让学排列组合是不是还得收费」；「导数还没学」这类进度说明同样会被算成薄弱；
#   ③ 合并词条把**还没学的内容**带出来——「函数与导数」只要命中「函数」就整条显示，
#      而高一上学期根本没学导数；错因再固定映射成「求导与单调性讨论」，等于凭空编造。
#
# 新口径三条：
#   A. **只统计「问题信号」消息**（学生/家长表达不会/疑问、老师指出错误/要求强化），
#      讲课长文本（>120 字）、事务与进度说明（收费/资料/还没学…）一律不计；
#   B. **显示名按实际命中的子单元收敛** —— 只命中「函数」就显示「函数」，不写「函数与导数」；
#   C. **门槛 ≥2 条问题信号**，且每条附首条原文（报告里 hover 可看，可回群核对）。
# ============================================================================
TOPIC_UNITS = [
    # (显示名, 命中词, 归属大类)  —— 大类用于对齐 FINE_TYPES / 备课建议，显示名用于卡片
    ("立体几何", ["立体几何", "三视图", "外接球", "异面直线"], "立体几何/空间向量"),
    ("空间向量", ["空间向量", "法向量", "建系", "线面角", "面面角", "二面角", "线面垂直", "面面平行"],
     "立体几何/空间向量"),
    ("直线", ["直线和圆", "直线方程", "位置关系"], "直线和圆"),
    ("圆", ["圆的方程", "圆心距", "圆的切线"], "直线和圆"),
    ("函数", ["函数", "定义域", "值域", "解析式", "奇偶性", "零点", "单调性"], "函数与导数"),
    ("导数", ["导数", "求导", "极值", "切线方程"], "函数与导数"),
    ("数列", ["数列", "等差", "等比", "通项", "前n项和"], "数列"),
    ("圆锥曲线", ["椭圆", "双曲线", "抛物线", "离心率", "焦点", "准线"], "圆锥曲线"),
    ("三角函数", ["三角函数", "正弦", "余弦", "诱导公式", "辅助角"], "三角函数"),
    ("概率统计", ["概率", "统计", "排列组合", "期望", "方差", "频率分布"], "概率统计"),
    ("解三角形", ["解三角形", "正弦定理", "余弦定理"], "解三角形"),
    ("不等式", ["不等式", "恒成立", "取值范围"], "不等式/参数"),
    ("含参问题", ["含参", "参数讨论"], "不等式/参数"),
    # ⚠️ v4.47 删掉裸词「计算」：命中面太广（计算量 / 计算过程 / 算一下 / 课前测计算量），
    #   实测把「今天课前测的计算量还是有的」判成「计算粗心 ×20」。只保留真正的错误词。
    ("计算/粗心", ["粗心", "算错", "符号", "漏解", "增根", "少一种"], "计算/粗心"),
]

# ── v4.47 反误判：把「消息级一刀切」升级为「知识点级语境判定」 ─────────────────
# 联席反馈（真实场景）：「有时候我只是提一嘴它也会分析进去」「我跟学生说之后学导数的时候
# 我们也会碰见，就会识别成导数不会」。
#
# 旧口径的两个洞：
#   ① 只有「以后学 / 下节课学」等极少数固定说法在 TOPIC_SKIP_KWS 里，且是**整条消息**
#      命中就丢弃 —— 「之后学」「会碰见」「后面讲」「再讲」全都漏网；
#   ② 只要整条消息里出现知识点词 + 任一问题信号词，知识点就计数，**不看那个词附近的语境**，
#      于是「空间向量没问题就看看这个吧」被判成空间向量薄弱（命中「错题」的「错」）。
#
# 新口径：
#   A. 事务/通知类 → 整条跳过（TOPIC_SKIP_MSG）；
#   B. 未来时 / 进度预告、正面评价 → **只对命中的那个知识点词做 ±16 字窗口判定**，
#      窗口干净才计数（同一个词在别的分句里可能仍是真问题）；
#   C. 微信「引用/回复」消息先剥掉引用块，只分析引用后的正文（引用内容常是几天前别人的话）。
TOPIC_SKIP_MSG = ("收费", "报名", "缴费", "付款", "链接", "资料", "讲义", "上课时间", "请假",
                  "调到", "调课", "换班", "进度", "错题本", "巩固知识")
# 未来时 / 进度预告（窗口级）
TOPIC_FUTURE = ("还没学", "没有学", "没学到", "未学", "没接触", "还没讲", "没讲到", "还没讲到",
                "后面才学", "以后学", "之后学", "后面学", "接下来学", "下节课学", "下节讲",
                "下次讲", "即将学", "将要学", "马上学", "会学到", "会讲到", "会碰见", "会碰到",
                "会遇到", "将来", "到时候", "届时", "开学后", "再讲", "才学")
# 正面 / 明确否定（窗口级）
TOPIC_POS = ("不错", "没错", "没算错", "没做错", "没问题", "全对", "都对了", "做对了", "答对了",
             "完全正确", "掌握", "很好", "很棒")
TOPIC_CTX_WIN = 16         # 知识点词前后各 16 字作为语境窗口
# 学生 / 家长侧「问题信号」：表达不会、疑问、做错
TOPIC_STU_PROB = ("不会", "不懂", "没懂", "没听明白", "听不懂", "做错", "算错", "错了", "有点难",
                  "太难", "跟不上", "不熟", "卡住", "没思路", "怎么做", "请问", "问一下",
                  "这道题", "这个题")
# 老师侧「问题信号」：明确指出错误 / 要求强化
# ⚠️ v4.47：删「还是有」（「计算量还是有的」×20 的来源）、删「巩固」（「做一下巩固知识」是布置作业）、
#   裸「错」改词组（「错题感觉如何」「易错点」都会误命中）。
TOPIC_TEA_PROB = ("做错", "算错", "写错", "答错", "错了", "出错", "错在", "改错", "不对",
                  "再强化", "需要加强", "订正", "不熟", "薄弱", "扣分", "丢分", "不规范",
                  "重新做", "漏解", "没掌握", "不到位", "马虎", "粗心")
MIN_TOPIC_SIGNALS = 2      # 同一知识点至少 2 条问题信号才算「薄弱」（1 条多为偶发）
MIN_TOPIC_TEA_ONLY = 3     # v4.47：信号全部来自老师单方、且不足此数 → 视为「提一嘴」，不判薄弱
TOPIC_MSG_MAXLEN = 120     # 超过此长度的消息按「讲课/转写长文本」处理，不参与统计


def _strip_quote(txt):
    """剥掉微信「引用/回复」消息的引用块，只留引用后的正文。"""
    parts = re.split(r"-{4,}", txt or "")
    return (parts[-1] if len(parts) > 1 else (txt or "")).strip()


def _kw_windows(txt, kw, win=TOPIC_CTX_WIN):
    """产出知识点词每次出现处的 ±win 字语境窗口。"""
    i = txt.find(kw)
    while i >= 0:
        yield txt[max(0, i - win): i + len(kw) + win]
        i = txt.find(kw, i + 1)


def _topic_hit_clean(txt, kws):
    """该知识点在句内是否有「干净」的出现位置（语境窗口里没有预告/正面/事务词）。"""
    for kw in kws:
        if kw not in txt:
            continue
        for ctx in _kw_windows(txt, kw):
            if any(k in ctx for k in TOPIC_FUTURE):
                continue
            if any(k in ctx for k in TOPIC_POS):
                continue
            if any(k in ctx for k in TOPIC_SKIP_MSG):
                continue
            return True
    return False


def topic_problem_stats(triples):
    """从聊天三元组里提取「薄弱知识点」候选（v4.31 新口径）。

    返回 (Counter{显示名: 信号条数}, {显示名: (时间, 原文)}, {显示名: [(idx, 时间, 原文), …]})。
    ⚠️ 只统计**问题信号**消息，且一条消息对同一显示名只计 1 次（防单条长消息刷分）。
    第三个返回值 `seq` 是**按时间序的完整信号位置**，供 v4.32「是否已掌握」回看使用 ——
    只看最后一条信号之后有没有闭环，所以必须保留全部位置，不能只留首条。
    """
    cnt, ev, seq = Counter(), {}, {}
    for idx, (ts, role, t) in enumerate(triples):
        txt = _strip_quote((t or "").strip())
        if not txt or len(txt) > TOPIC_MSG_MAXLEN:
            continue
        if any(k in txt for k in TOPIC_SKIP_MSG):
            continue
        if role == 2:
            if not any(k in txt for k in TOPIC_STU_PROB):
                continue
        elif role == 1:
            if not any(k in txt for k in TOPIC_TEA_PROB):
                continue
        else:
            continue
        seen = set()
        for disp, kws, _key in TOPIC_UNITS:
            if disp in seen:
                continue
            if not _topic_hit_clean(txt, kws):      # v4.47：知识点级语境判定
                continue
            seen.add(disp)
            cnt[disp] += 1
            ev.setdefault(disp, ((ts or "")[:10], txt))
            seq.setdefault(disp, []).append((idx, (ts or "")[:10], txt))
    return cnt, ev, seq


# ============================================================================
# v4.32「已掌握」判定（业务要求：学生问过、老师讲过、已闭环的知识点要标出来）
#
# 为什么必须做：v4.31 之后「薄弱知识点」只剩问题信号，但问题**解决之后**的信号还挂在卡上
# 会被读成「现在还薄弱」。业务要的是「这条已经闭环了」的显式标识，而不是让人自己翻聊天记录。
#
# 判据（三条，全部要求「发生在该知识点**最后一次**问题信号之后」，且 7 天内、10 条消息内）：
#   A. 学生/家长本人说「懂了 / 明白了 / 学会了 / 弄懂了 / 原来如此…」—— 且不含否定（没懂/还是不会…）；
#   B. 老师给出**结果性确认**：「完全正确 / 全对 / 做对了 / 答对了 / 掌握得很好…」；
#   C. 老师给出**带知识点的肯定**：「很好 / 很棒 / 不错」＋消息里出现该知识点的词。
# ⚠️ 三条**硬性排除**（否则误报会很多，实测过）：
#   ① 老师在讲**期望/要求**（「一定要掌握」「你一定能掌握」「熟练掌握」「预习」）≠ 已掌握；
#   ② 红包 / 总结 / 加油 / 下次 / 作业 这类泛消息里出现的「很好」不算；
#   ③ 若问题信号之后**又出现同一知识点的新问题信号** → 直接判「没掌握」，不可标已掌握。
# 🔒 反编造：只认聊天里**原话**，不做「大概懂了」的推断；找不到就返回空，宁可不标。
# ============================================================================
STU_MASTER_KWS = ("懂了", "明白了", "学会了", "学明白", "搞懂了", "弄懂了", "听懂了",
                  "理解了", "清楚多了", "原来是这样", "原来如此", "恍然大悟",
                  "知道了怎么", "没问题了", "会做了")
STU_MASTER_NEG = ("没懂", "不懂", "没听懂", "听不懂", "不太懂", "没太懂", "还是不会",
                  "还是不懂", "不会做", "没学会", "不清楚", "没明白")
TEA_MASTER_RESULT = ("完全正确", "全对", "做对了", "答对了", "全部正确", "都对了",
                     "掌握得不错", "掌握得很好", "已经会了", "学会了")
TEA_MASTER_PRAISE = ("很好", "很棒", "不错", "优秀", "漂亮", "棒")
# 期望 / 要求 / 泛消息：出现这些词时，上面的肯定语**不算**掌握证据
TEA_MASTER_BLOCK = ("红包", "预习", "总结", "继续", "加油", "下次", "记得", "别忘", "作业",
                    "视频", "上课", "一定要", "要掌握", "能掌握", "会掌握", "熟练掌握",
                    "掌握一下", "需要掌握", "应该掌握")
MASTER_LOOKAHEAD = 10      # 只在之后 10 条消息内找
MASTER_STU_GAP = 6         # 学生侧「明白了」须在 6 条内（再远就不是这轮对话了）
MASTER_TEA_GAP = 3         # 老师侧「完全正确」若不提知识点，须在 3 条内
MASTER_MAX_DAYS = 7        # 跨超过 7 天不算同一轮闭环


def _days_between(d1, d2):
    try:
        from datetime import datetime as _dt
        return abs((_dt.strptime(d2[:10], "%Y-%m-%d") - _dt.strptime(d1[:10], "%Y-%m-%d")).days)
    except Exception:
        return 999


def _mastery_after(triples, kws, start_i):
    """从 start_i（该知识点最后一条问题信号）之后找「已掌握」证据。找到返回 (who, 日期, 原文)。"""
    base_d = triples[start_i][0]
    for j in range(start_i + 1, min(start_i + 1 + MASTER_LOOKAHEAD, len(triples))):
        ts, role, txt = triples[j]
        txt = (txt or "").strip()
        if not txt or len(txt) > TOPIC_MSG_MAXLEN:
            continue
        if _days_between(base_d, ts) > MASTER_MAX_DAYS:
            return None
        gap = j - start_i
        has_kw = any(k in txt for k in kws)
        # ③ 又来问同一个知识点 → 说明没掌握
        if has_kw and any(k in txt for k in TOPIC_STU_PROB + TOPIC_TEA_PROB):
            return None
        if role == 2:
            if gap <= MASTER_STU_GAP and any(k in txt for k in STU_MASTER_KWS) \
                    and not any(k in txt for k in STU_MASTER_NEG):
                return ("学生", ts[:10], txt)
        elif role == 1:
            if any(k in txt for k in TEA_MASTER_BLOCK):
                continue
            if any(k in txt for k in TEA_MASTER_RESULT) and (has_kw or gap <= MASTER_TEA_GAP):
                return ("老师", ts[:10], txt)
            if any(k in txt for k in TEA_MASTER_PRAISE) and has_kw:
                return ("老师", ts[:10], txt)
    return None


def topic_mastery(triples, seq):
    """对每个知识点判定「是否已掌握」。返回 {显示名: {"who","at","text"}}。

    只看该知识点**最后一条**问题信号之后的闭环（前面闭环过、后面又问 = 仍薄弱）。
    """
    out = {}
    for disp, items in (seq or {}).items():
        kws = next((u[1] for u in TOPIC_UNITS if u[0] == disp), ())
        if not kws:
            continue
        for rec in reversed(items):
            hit = _mastery_after(triples, kws, rec[0])
            if hit:
                out[disp] = {"who": hit[0], "at": hit[1], "text": hit[2],
                             "q_at": rec[1], "q_text": rec[2]}
                break
    return out


# 微信「引用回复」的文本形态：`「引用人：被引用内容」- - - - 真正的回复正文`
# 被引用内容常常只是 [图片]/[文件]，此时整条消息的正文为空 —— 但旧实现会把
# `「某生：[图片]」- - - - - -` 整串当成一句「学生提问」，从而在近 3 天待回应里
# 产生假条目（真实数据实测）。故统一剥离引用头，只留正文。
# ⚠️ 分隔符实际是「- - - -」（连字符之间有空格），必须写成 (?:[-—]\s*){2,}；
# 早期版本用 [-—]{2,} 直接匹配连续连字符，真实数据里**一条都匹配不上**（实测 58 群）。
QUOTE_HEAD_RE = re.compile(r"^「[^」]{0,80}：(?:\[[^\]]{1,14}\]|[^」]{0,80})」\s*(?:[-—]\s*){2,}")
QUOTE_ONLY_RE = re.compile(r"^「[^」]{0,80}：(?:\[[^\]]{1,14}\]|[^」]{0,80})」\s*(?:[-—]\s*){2,}\s*$")


def strip_quote(t):
    """剥离微信引用回复头部，返回真正的回复正文（无引用头时原样返回）。"""
    s = (t or "").strip()
    if not s.startswith("「"):
        return s
    if QUOTE_ONLY_RE.match(s):
        return ""                          # 只引用了图片/文件、没有正文
    m = QUOTE_HEAD_RE.match(s)
    return s[m.end():].strip() if m else s


def get_text(m):
    c = m.get("content")
    text = ""
    if isinstance(c, str):
        text = c
    elif isinstance(c, dict):
        for k in ("content", "text"):
            if k in c and isinstance(c[k], str):
                text = c[k]
                break
    return strip_quote(text)


def hits(text, kws):
    return sum(text.count(k) for k in kws)


def norm_text(t):
    """归一化：剥离表情/符号后，去掉空白与中英文标点，便于判定礼节短句。"""
    s = EMOJI_RE.sub("", t or "")
    return re.sub(r"[\s，。！!~、,.;；:：?？/\\\-—…·\"'“”‘’（）()\[\]【】]+", "", s)


def is_ack(t):
    """判断是否为「无需回复」的礼节性收尾消息（确认/致谢/应承/纯表情）。"""
    raw = (t or "").strip()
    if not raw:
        return False
    cleaned = ACK_EXPR_RE.sub("", raw)      # 先剥离 [玫瑰][抱拳] 这类文本表情
    s = norm_text(cleaned)
    if not s:
        # 纯表情或纯标点（👍👍👍 / 🍵🍵🍵 / [玫瑰][抱拳] / 。。。）——同样无需回复；
        # 但单独的「？」属于求答疑，仍视为需要响应
        return not any(ch in cleaned for ch in "?？")
    # 纯呼名（「老师」「老师在吗」）属于请求响应，不算礼节性收尾
    if s in ("老师", "老师们", "在吗", "在么", "老师在吗", "老师在么"):
        return False
    if s.lower() in ACK_SET:
        return True
    if len(s) > 16:
        return False
    r = s.lower()
    for tok in sorted((x.lower() for x in ACK_TOKENS), key=len, reverse=True):
        r = r.replace(tok, "")
    if r == "":
        return True
    # 致谢/致歉词 + 极短称呼（「谢谢丹姐」「辛苦老师了」）同样无需回复
    return len(r) <= 2 and any(p in s for p in THANKS_WORDS)


def is_noise(t):
    """判断是否为占位噪声消息（[图片]/[revoke]/[该消息类型暂不能展示] 等）。"""
    s = (t or "").strip()
    if not s:
        return True
    if re.fullmatch(r"[\[【（(][^\[\]【】（）()]{0,24}[\]】）)]", s):
        return any(k in s for k in NOISE_KEYS)
    return False


def parse_dt(ts):
    """解析消息时间戳为 datetime，失败返回 None。

    兼容两种分隔符：`YYYY-MM-DDTHH:MM:SS`（后台接口）与 `YYYY-MM-DD HH:MM:SS`（导出/演示数据）。
    """
    s = (ts or "")[:19].strip()
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s.replace(" ", "T"), "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def resp_wait_stats(triples_all):
    """由聊天记录推算响应时效（不依赖后台标记）。

    一轮 =「学生/家长的首条实质提问」→「其后第一条老师消息（含图片/讲义等，只要有动作即算已响应）」。
    返回 (waits, pending)：waits=[(小时, 提问时间, 提问摘要, 老师回复正文)]，
    pending=[(提问时间, 提问摘要)]（窗口末尾仍无老师消息）。
    """
    waits, pending = [], []
    open_ts = open_q = None
    for ts, r, t in triples_all:
        if r == 1:                                   # 老师有任何动作 → 本轮闭环
            if open_ts:
                d1, d2 = parse_dt(open_ts), parse_dt(ts)
                if d1 and d2 and d2 >= d1:
                    waits.append(((d2 - d1).total_seconds() / 3600.0, open_ts, open_q, t or ""))
                open_ts = open_q = None
            continue
        if r == 2 and t and not is_noise(t) and not is_ack(t) and hits(t, Q_ASK):
            if open_ts is None:                      # 同一轮内多条连续提问只计一次（以首条起算）
                open_ts, open_q = ts, re.sub(r"\s+", " ", t)
    if open_ts:
        pending.append((open_ts, open_q))
    return waits, pending


# —— 近 N 天待回应（v3.9）：联席忘记回复 / 学生问题仍未解决 ——
# 用户口径：结合近 3 天聊天，若「联席忘记回复」或「学生问题未解决」，必须重点标记提醒。
RECENT_DAYS = 3
# 老师回复里出现这些「延宕 / 应允」词 → 该提问仍算未解决（说了会答但没有答）。
# v4.1 收紧：移除「明天 / 下次 / 再讲」—— 真实数据实测它们高频出现在**正常教学安排**里
# （例：「明天下午3:40-5:40正常上课」被误判成「应允未答」），造成假条目。
DEFER_KWS = ["稍后", "稍等", "晚点", "晚些", "晚一些", "一会儿", "等下", "等会", "待会", "过会儿",
             "回头", "有时间", "再想想", "到时候", "改天", "之后说", "后头", "这两天"]
# 命中延宕词但回复同时含这些词 → 属排课/说明性安排，不算「应允未答」
DEFER_EXCLUDE = ["正常上课", "上课时间", "课程安排", "调课", "上课", "加课", "请假", "调休", "休息", "放假"]
# 「学生问题未解决」的第二类证据（v4.1 收紧）：老师在 24h 内回复过，但学生**紧接着又追问同一困惑**。
# 旧口径只要 24h 内有下一次提问就算「没讲透」，导致师生正常的多轮深挖（真实数据里一名积极学生
# 被连判 7 项）被误报。现要求追问**明确表达仍然困惑/没解决**，才算「没讲透」。
REASK_KWS = ["还是不会", "还是不懂", "还是没懂", "还是不明白", "还是没", "还是不太", "仍然不会", "仍然不懂",
             "没懂", "没听懂", "看不懂", "不理解", "不明白", "没解决", "没讲", "讲的不对",
             "你上面", "您上面", "老师上面", "上个问题"]


def _short(t, n=30):
    return re.sub(r"\s+", " ", (t or "")).strip()[:n]


# 分句：细粒度薄弱点必须逐句判断（长消息里优缺点混在一条，整条判断会误判）
_SENT_SPLIT = re.compile(r"[。！？；!?;\n]+")


def recent_pending(triples_all, anchor_ts, days=RECENT_DAYS):
    """近 N 天「待回应」清单（v3.9）：以窗口内最新一条消息为基准往前推 N 天。

    两类问题（都要重点标记提醒）：
      ① 忘记回复 —— 近 N 天内学生/家长实质提问，其后（截至窗口末尾）再无任何老师消息；
      ② 问题未解决 —— 老师确有回复，但回复命中延宕词（应允未答），
                      或老师在回复后 24 小时内学生就同类疑问再次追问（没讲透）。

    同一轮内（老师回复前的）连续多条提问只按首条计，避免刷量。
    返回 {"days","since","anchor","items":[{at,q,kind,evi}],"missed_n","open_n"}。
    """
    d_anchor = parse_dt(anchor_ts)
    if not d_anchor:
        return {"days": days, "since": "", "anchor": "", "items": [], "missed_n": 0, "open_n": 0}
    since_dt = d_anchor - datetime.timedelta(days=days)
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%S")
    items, n = [], len(triples_all)
    i = 0
    while i < n:
        ts, r, t = triples_all[i]
        d_q = parse_dt(ts)
        # 只看近 N 天内的学生/家长实质提问（用 datetime 比较：msgTime 可能是空格或 T 分隔）
        if (r != 2 or not t or is_noise(t) or is_ack(t) or not hits(t, Q_ASK)
                or not d_q or d_q < since_dt):
            i += 1
            continue
        j = i + 1                                   # 同一轮内老师的回复前的其它学生消息直接跳过
        while j < n and triples_all[j][1] == 2:
            j += 1
        kind = evi = ""
        if j >= n:                                  # 直到窗口末尾都没有老师动作 → 忘记回复
            kind = "忘记回复"
            evi = f"{ts[:10]} 学生/家长提问「{_short(t)}」，至今群内无老师回复"
        else:
            ts_t, _, t_t = triples_all[j]
            # 应允未答：老师回复命中延宕词，且不是在说排课/假期等正常安排（v4.1）
            defer = (bool(t_t) and any(k in t_t for k in DEFER_KWS)
                     and not any(x in t_t for x in DEFER_EXCLUDE))
            if defer:
                kind = "问题未解决"
                evi = f"{ts[:10]} 提问「{_short(t)}」，老师 {ts_t[:10]} 应允「{_short(t_t, 18)}」后仍无下文"
            else:
                d_rep = parse_dt(ts_t)
                reask, reask_txt = False, ""
                for k in range(j + 1, n):           # 老师回复后，学生再次追问
                    ts2, r2, t2 = triples_all[k]
                    if r2 != 2 or not t2 or is_noise(t2) or is_ack(t2) or not hits(t2, Q_ASK):
                        continue
                    d2 = parse_dt(ts2)
                    # v4.1 收紧：24h 内再问 **且** 明确表达仍然困惑（还是不会 / 没懂 / 不明白…）
                    # 才算「没讲透」；否则只是师生正常的多轮深挖，不计入。
                    if (d_rep and d2 and d2 > d_rep
                            and (d2 - d_rep).total_seconds() <= 24 * 3600
                            and any(x in t2 for x in REASK_KWS)):
                        reask, reask_txt = True, t2
                    break
                if reask:
                    kind = "问题未解决"
                    evi = (f"{ts[:10]} 提问「{_short(t)}」，老师已回复，但学生当日再问"
                           f"「{_short(reask_txt, 16)}」，疑未讲透")
        if kind:
            items.append({"at": (ts or "")[:10], "q": _short(t, 22), "kind": kind, "evi": evi})
        i = j + 1 if j < n else n
    return {"days": days, "since": since_dt.strftime("%Y-%m-%d"), "anchor": (anchor_ts or "")[:10],
            "items": items[:8],
            "missed_n": sum(1 for x in items if x["kind"] == "忘记回复"),
            "open_n": sum(1 for x in items if x["kind"] == "问题未解决")}


def fine_weaknesses(triples_all, per_topic=4, per_student=8):
    """细粒度薄弱题型（v4.2 · 聊天侧）：把「直线和圆」这类粗知识点，下钻到
    「含参直线过定点 / 斜率是否存在的分类讨论」这类**具体方法或题型**。

    证据只来自两类真实发言（词表见 `common.FINE_TYPES` / `TEA_POINT_KWS` / `STU_LOST_KWS`）：
      ① **老师指出**（「你这题错在…」「没有彻底理解…」）—— 已确认的薄弱点，优先展示；
      ② **学生卡住**（「这一步不会 / 为什么…」）—— 疑似薄弱点。
    每条都带**原文出处**（原文片段 + 日期），报告里逐条可见、可回到群里核对。
    返回 {知识点: [{"item","kind","at","evi","count"}]}
    """
    buckets = {}
    for ts, r, t in triples_all:
        if not t or is_noise(t) or is_ack(t) or common.is_notice(t):
            continue
        # ⚠️ 逐句判断，不整条判断：一条长消息（如学生的「复盘总结」）可以既列优点又列问题，
        # 整条判断会把「做得比较好的地方：直线与圆基础题型…」这种**优势**误判成薄弱点（真实数据实测）。
        for sent in _SENT_SPLIT.split(t):
            sent = sent.strip()
            if len(sent) < 4 or common.is_positive(sent):
                continue
            # v4.47：未来时 / 进度预告不算薄弱（「之后学导数的时候我们也会碰见」）
            if common.is_future(sent):
                continue
            if r == 1:                                # 老师：必须「具体指出问题」（不带假设/询问语气）
                if not common.is_tea_point(sent):
                    continue
                kind, pri = "老师指出", 0
            elif r == 2:                              # 学生：必须「表达困惑 / 提问」
                # ⚠️ v4.47：「为什么」已从 STU_LOST_KWS 移除，陈述式感慨（「理解了为什么…就不想念」
                #    ）不再命中；真提问仍由句尾问号兜住，漏判风险低。
                if not (any(k in sent for k in common.STU_LOST_KWS)
                        or sent.endswith(("?", "？"))):
                    continue
                kind, pri = "学生提问", 1
            else:
                continue
            for topic, item in common.classify_fine(sent, best_only=True):
                slot = buckets.setdefault(topic, {}).setdefault(
                    item, {"item": item, "kind": kind, "pri": pri, "at": (ts or "")[:10],
                           "evi": common.clip_txt(sent, 44), "count": 0})
                slot["count"] += 1
                if pri < slot["pri"]:                 # 老师指出 → 换作展示依据（更可信）
                    slot.update({"pri": pri, "kind": kind, "at": (ts or "")[:10],
                                 "evi": common.clip_txt(sent, 44)})
    out = {}
    for topic, items in buckets.items():
        rows = sorted(items.values(), key=lambda x: (x["pri"], -x["count"]))
        out[topic] = [{k: v for k, v in r.items() if k != "pri"} for r in rows[:per_topic]]
    # 全局上限：只保留证据最强的若干条（老师指出优先 → 出现次数多者优先），
    # 避免卡片被铺满；顺序同时决定报告里的展示顺序。
    flat = [(t, r) for t, rows in out.items() for r in rows]
    flat.sort(key=lambda x: (0 if x[1]["kind"] == "老师指出" else 1, -x[1]["count"]))
    keep = {}
    for t, r in flat[:per_student]:
        keep.setdefault(t, []).append(r)
    return keep


def pair_after(triples, trig_kws, resp_kws, within_h=72.0):
    """配对判定：老师在 t 时刻发出「要求」（催交作业 / 要求订正），其后 within_h 小时内
    学生是否给出对应「响应」（交作业 / 已订正）。

    联席四维里的「催作业→是否交」「批改后→是否改正」两条都靠它。
    返回 (触发数, 已响应数, [(日期, 触发摘要), ...] 未响应明细)。
    同一轮内的连续催交（相邻触发之间无学生响应）只计一次，避免刷量。
    """
    trig, ok, fail, last_trig = 0, 0, [], None
    n = len(triples)
    for i, (ts, r, t) in enumerate(triples):
        if r != 1 or not t or not any(k in t for k in trig_kws):
            continue
        d0 = parse_dt(ts)
        if last_trig and d0 and (d0 - last_trig).total_seconds() / 3600.0 <= 6.0:
            continue                                    # 6 小时内的重复催 → 同一轮，只计一次
        last_trig = d0
        trig += 1
        got = False
        for j in range(i + 1, n):
            ts2, r2, t2 = triples[j]
            d1 = parse_dt(ts2)
            if d0 and d1 and (d1 - d0).total_seconds() / 3600.0 > within_h:
                break
            if r2 == 2 and t2 and any(k in t2 for k in resp_kws):
                got = True
                break
        if got:
            ok += 1
        else:
            fail.append(((ts or "")[:10], re.sub(r"\s+", " ", t)[:24]))
    return trig, ok, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", default=None, help="联席姓名（联席视角 / 课程内子筛选）")
    ap.add_argument("--edu", default=None, help="教务姓名（教务视角）")
    ap.add_argument("--course", default=None, help="课程名（课程维度）")
    ap.add_argument("--window", default=WINDOW_DEFAULT, help="分析窗口起点 YYYY-MM-DD")
    ap.add_argument("--end", default=None,
                    help="分析窗口止 YYYY-MM-DD（含当天；默认不限，即分析到最新一条消息）")
    args = ap.parse_args()
    role = common.resolve_scope(args)[0]
    is_edu = role == "edu"
    wd = common.workdir(args)
    CUT = args.window
    CUT_END = args.end          # v4.16：闭区间上界；None = 不设上界（与旧行为完全一致）
    d = json.load(open(f"{wd}/chats.json", encoding="utf-8"))

    students = {}
    for stu, blk in d.items():
        g = blk["group"]
        # 接口返回为倒序（最新在前）；此处显式按时间升序排序，消除后续对顺序的隐式依赖
        # v4.16：窗口为 [window, end] 闭区间（end 为空则只卡起点，等同旧行为）。
        # msgTime 有 "YYYY-MM-DD HH:MM:SS" 与 "YYYY-MM-DDT..." 两种形态，故统一取前 10 位比日期。
        msgs = [m for m in blk["messages"]
                if (m.get("msgTime") or "") >= CUT
                and (not CUT_END or (m.get("msgTime") or "")[:10] <= CUT_END)]
        msgs.sort(key=lambda m: m.get("msgTime") or "")
        triples_all = [((m.get("msgTime") or ""), m.get("role"), get_text(m)) for m in msgs]
        # 剔除「空文本」与「占位噪声」（[图片]/[revoke]/[该消息类型暂不能展示] 等，无正文不参与分析）
        triples = [x for x in triples_all if x[2] and not is_noise(x[2])]
        texts = [(r, t) for _, r, t in triples]
        if not texts:
            empty_todos = ([("低", "暂无可分析互动，主动联系家长了解学生近况与续报意愿", "窗口内无聊天")]
                           if is_edu else
                           [("低", "维持常规跟进，关注后续作业与提问", "窗口内无聊天")])
            students[stu] = {
                "group": g, "n_text": 0, "n_stu": 0, "n_stu_sub": 0, "n_ack": 0, "n_tea": 0, "voice": 0,
                "newest": None, "oldest": None, "att_n": 0, "hw": 0, "q": 0,
                "pos": 0, "neg": 0, "hw_neg": 0, "att_leave": 0,
                "student_coop": 0, "parent_coop": 0, "speakers": [], "n_other": 0, "parent_sig": 0,
                "same_person": 0,
                "top_topics": [], "top_topics_evi": {}, "topic_signals": {},
                "topic_mastered": {},
                "unresolved": False, "unresolved_at": "", "unresolved_q": "",
                "unres_evi": "窗口内无聊天",
                "resp_n": 0, "resp_max_h": 0.0, "resp_slow": False, "resp_slow_cnt": 0,
                "resp_at": "", "resp_q": "", "resp_pend_n": 0,
                "resp_evi": "窗口内无聊天",
                "summary": (f"{CUT} ~ {CUT_END} 该群无可分析聊天记录（窗口内无互动）。" if CUT_END
                            else f"{CUT} 起该群无可分析聊天记录（窗口内无互动）。"),
                "todos": empty_todos,
                # 联席四维（v3.7）：无聊天 → 互动沉默、无催交/订正记录、提问为零
                "interact": "沉默不回复", "interact_lv": "bad", "interact_evi": "窗口内无互动",
                "gap_days": None,
                "urge_n": 0, "urge_ok": 0, "urge_fail": [],
                "fix_n": 0, "fix_ok": 0, "fix_fail": [],
                "ask_lv": "无", "tea_pos": 0, "tea_neg": 0,
                # 学生成绩（v4.4）：窗口内无聊天 → 无成绩证据
                "exam": {"latest": None, "history": [], "level": "未知", "why": ""},
                "recent3": {"days": RECENT_DAYS, "since": "", "anchor": "",
                            "items": [], "missed_n": 0, "open_n": 0},
                "fine": {},
            }
            continue
        all_txt = " ".join(t for _, t in texts)
        stu_all = [t for r, t in texts if r == 2]
        tea_txt = [t for r, t in texts if r == 1]
        # 学生/家长消息一分为二：礼节性收尾（好的/收到/谢谢老师＝无需回复）vs 实质消息（提问/反馈/交作业等）
        ack_stu = [t for t in stu_all if is_ack(t)]
        sub_stu = [t for t in stu_all if not is_ack(t)]
        n_stu = len(stu_all)
        n_ack = len(ack_stu)
        n_stu_sub = len(sub_stu)
        n_tea = len(tea_txt)
        voice = sum(1 for m in msgs if m.get("msgType") == "voice")
        # 群内发言者分布（v4.5）：学生侧按 name 聚合 —— 学生群常态是「学生本人高频 + 家长个位数」，
        # 用它把「家长发言少」与「家长不配合」分开（归因修正），并让配合度结论可回群核查。
        _spk = Counter()
        for _m, (_t, _r, _x) in zip(msgs, triples_all):
            if _r == 2 and _x and not is_noise(_x):
                _spk[((_m.get("name") or "").strip() or "（未署名）")] += 1
        speakers = [{"name": _n, "n": _c} for _n, _c in _spk.most_common(6)]
        n_other = max(0, n_stu - (speakers[0]["n"] if speakers else 0))

        # 次发言者是否疑似「学生本人的第二个账号」（昵称含共同汉字，如「毛毛～💕」vs「毛星空～💕」）
        # —— 命中则不能把他当家长，避免造出「家长高度配合」这类反向伪结论。
        def _cn_chars(x):
            return {ch for ch in (x or "") if "\u4e00" <= ch <= "\u9fff"}

        same_person = 1 if (len(speakers) >= 2
                            and (_cn_chars(speakers[0]["name"]) & _cn_chars(speakers[1]["name"]))) else 0
        # 家长身份证据（v4.5）：只在**非主导发言者**的消息里查家长措辞（自报身份 / 代交 / 代沟通 / 孩子…），
        # 命中才敢把这个人称作「家长」——避免把「学生本人的第二个账号」误判成家长，造出反向伪结论。
        _main = speakers[0]["name"] if speakers else None
        _oth_txt = []
        for _m, (_t, _r, _x) in zip(msgs, triples_all):
            if _r == 2 and _x and not is_noise(_x):
                if (((_m.get("name") or "").strip() or "（未署名）") != _main):
                    _oth_txt.append(_x)
        parent_sig = hits(" ".join(_oth_txt), PARENT)
        times = [m.get("msgTime") for m in msgs if m.get("msgTime")]
        newest = times[-1] if times else None
        oldest = times[0] if times else None
        att = hits(all_txt, ATT)
        hw = hits(all_txt, HW)
        # 疑问/自驱/家长信号只统计「实质消息」，避免「好的/收到」等无意义刷量
        q = sum(hits(t, Q) for t in sub_stu)
        pos = hits(" ".join(stu_all), POS)          # 致谢/礼貌用语（学生/家长侧）
        neg = hits(all_txt, NEG)
        stu_self = q + hits(" ".join(sub_stu), STU_SELF)
        parent = hits(" ".join(sub_stu), PARENT)
        hw_neg = hits(all_txt, ["没写", "没做", "不会做", "漏交"])
        att_leave = hits(all_txt, ["请假", "回放", "没来", "缺课"])
        # 学生成绩（v4.4）：只匹配、不生成；认不出返回「未知」（见 common.extract_exam）
        # ⚠️ v4.11：学科优先取**后台课程名 productName**（含「数学」等学科词）—— 新接口链路
        #    的 groups.json 不提供群名，只读 groupName 会得到空学科，进而让成绩满分推断
        #    退化为 100 分制、算错得分率。groupName 仅作兜底。
        _subj = (common.subject_of(g.get("productName") or "")
                 or common.subject_of(g.get("groupName") or ""))
        exam = common.extract_exam(triples, stu, subject=_subj)
        # 配合度：仅用实质消息判定；致谢最多计 4 分，避免「收到/谢谢」把配合度刷满
        student_coop = 0 if n_stu_sub == 0 else 1 + min(4, stu_self // 4)
        parent_coop = 0 if n_stu_sub == 0 else 1 + min(4, (parent + min(pos, 4)) // 2)
        # v4.31：「薄弱知识点」改为**问题信号统计**（学生提问/不会 ＋ 老师指出错误），
        #   不再是整群纯词频。旧口径把老师讲课、收费咨询、进度说明都算成学生薄弱，
        #   还会用合并词条（函数与导数）带出高一没学的「导数」。见 TOPIC_UNITS 上方说明。
        tc, tc_evi, tc_seq = topic_problem_stats(triples)
        # ⚠️ v4.47 试过再加一条「信号全来自老师单方且不足 3 次 → 不算薄弱」的门槛，
        #   实测把 110 个有薄弱知识点的学生压到 4 个 —— 联席本就是群里的主要发言人，
        #   学生的卡点绝大多数是老师指出来的，按说话人卡门槛等于把有效信号一起删掉。
        #   「提一嘴」已被上游解决：通用提醒词要求指向该生（is_tea_point）、
        #   未来时/预告与正面评价在知识点窗口内排除（_topic_hit_clean）。
        top_topics = [t for t, n in tc.most_common() if n >= MIN_TOPIC_SIGNALS][:3]
        # 每个知识点附首条原文（时间 + 原话），供卡片 hover 与「可核对」使用
        top_topics_evi = {}
        for _t in top_topics:
            _at, _tx = tc_evi.get(_t, ("", ""))
            top_topics_evi[_t] = {"n": tc[_t], "at": _at, "text": _tx}
        # v4.31.1：**全量**信号（含仅 1 条的）单列输出 —— 卡片用 ≥2 条的高置信门槛，
        #   而 Tab②「共性薄弱面」是**跨学生聚合**：一个人各提 1 次、凑够多人才有普遍性意义，
        #   用卡片门槛会把概览页整个清空（六E胡实测：9 人只剩 1 人达标 → 整表消失）。
        #   ⚠️ 门槛属于表现层决策，数据层只提供计数，由 report.py 各自取舍。
        topic_signals = {}
        for _t, _n in tc.most_common():
            _at, _tx = tc_evi.get(_t, ("", ""))
            topic_signals[_t] = {"n": _n, "at": _at, "text": _tx}
        # v4.32：「已掌握」闭环判定 —— 学生问过、老师答过、且**最后一条问题信号之后**
        #   出现了学生确认或老师结果性确认的知识点，单独标记，卡片上以「已掌握」角标呈现。
        topic_mastered = topic_mastery(triples, tc_seq)
        # —— 未闭环判定：只看「会话尾部」（最新 12 条，triples_all 已升序、含图片/文件等非文本消息）——
        # 规则：若「最后一条老师消息之后」仍有学生/家长的实质提问，才视为未获回应；
        # 一旦老师在其后有过任意回复（文本答疑、发图片/讲义/视频、催看等），即视为已闭环。
        # 重要：「好的 / 收到 / 谢谢老师 / OK」等无需回复的礼节性收尾，不作为待回应提问。
        tail = triples_all[-12:]
        last_tea_idx = max([i for i, (_, r, _) in enumerate(tail) if r == 1], default=-1)
        unresolved = False
        unresolved_at = ""
        unresolved_q = ""
        for i in range(last_tea_idx + 1, len(tail)):
            ts, r, t = tail[i]
            if r == 2 and t and not is_ack(t) and hits(t, Q_ASK):
                unresolved = True
                unresolved_at = (ts or "")[:10]
                unresolved_q = re.sub(r"\s+", " ", t)[:24]
        if unresolved:
            unres_evi = f"{unresolved_at} 学生提问「{unresolved_q}…」，其后群内无老师回复"
        else:
            unres_evi = "会话尾部无未获回应提问"
        # —— 近 3 天待回应（v3.9）：联席忘记回复 / 学生问题仍未解决，需重点标记提醒 ——
        rt = recent_pending(triples_all, newest)
        # —— 响应时效：完全由聊天记录自算（弃用后台 isTimeout/isReplied，实测不准）——
        waits, pend = resp_wait_stats(triples_all)
        resp_n = len(waits)
        resp_max_h = round(max([w[0] for w in waits], default=0.0), 1)
        # 长等待中剔除「老师已在回复里说明缘由」的情形（假期集中休息、稍后细看等）——属已沟通的延迟，
        # 不判迟缓、不计定档，仅在卡片提示最长等待时长，避免把假期安排误报成服务问题。
        long_all = [w for w in waits if w[0] >= RESP_SLOW_H]
        long_real = [w for w in long_all if not any(k in (w[3] or "") for k in RESP_EXCUSE)]
        resp_slow = bool(long_real)
        resp_slow_cnt = len(long_real)
        resp_at = resp_q = ""
        if resp_slow:
            _w = max(long_real, key=lambda x: x[0])
            resp_at, resp_q = (_w[1] or "")[:10], (_w[2] or "")[:24]
            resp_evi = f"{resp_at} 学生提问「{resp_q}…」后约 {_w[0]:.0f} 小时才见老师回复"
        elif long_all:
            _w = max(long_all, key=lambda x: x[0])
            resp_evi = f"最长等待 {_w[0]:.0f} 小时（{(_w[1] or '')[:10]} 提问），老师已说明安排，不计迟缓"
        elif resp_n:
            resp_evi = f"窗口内 {resp_n} 次提问均获回复（最长等待 {resp_max_h:.1f} 小时）"
        elif pend:
            resp_evi = f"窗口末尾仍有 {len(pend)} 条学生/家长提问未获老师回应"
        else:
            resp_evi = "窗口内无学生实质提问"
        parts = [f"近窗覆盖 {oldest[:10]} ~ {newest[:10]}（共 {len(texts)} 条可分析消息；学生/家长 {n_stu} 条＝实质 {n_stu_sub} + 礼节 {n_ack}；老师 {n_tea} 条）。"]
        parts.append(f"高频问题集中在：{ '、'.join(top_topics) }。" if top_topics else "未提取到明确知识点主题。")
        if att:
            parts.append(f"出勤/健康方面出现 {att} 次相关提及（请假、回放、身体等）。")
        if hw:
            parts.append(f"作业/练习相关提及 {hw} 次。")
        if neg:
            parts.append(f"负面情绪/压力信号 {neg} 处，需关注沟通态度。")
        elif pos:
            parts.append(f"正向反馈 {pos} 处（致谢等礼貌用语，无需回复），关系良好。")
        summary = "".join(parts)
        todos = []
        hw_bad = hits(all_txt, ["没写", "没做", "不会做", "漏交"])
        att_bad = hits(all_txt, ["请假", "回放", "没来", "缺课"])
        # 命中的具体关键词（用于把 To-Do 写「具体」，避免千人一面）
        neg_kws = [k for k in NEG if k in all_txt]
        att_kws = [k for k in ["请假", "回放", "没来", "缺课", "迟到", "早退"] if k in all_txt]
        hw_kws = [k for k in ["没写", "没做", "不会做", "漏交"] if k in all_txt]
        tp = "、".join(top_topics[:2])
        tp_s = f"（{tp}）" if tp else ""
        att_s = "、".join(att_kws) if att_kws else "请假/缺课"
        hw_s = "、".join(hw_kws) if hw_kws else "未完成/不会做"
        neg_s = "、".join(neg_kws[:3]) if neg_kws else "情绪波动"
        # —— 联席四维（v3.7）：互动状态 / 催交作业是否交 / 批改后是否订正 / 提问意愿 ——
        # 四维是「联席为学习效果负责」的直接抓手；出勤/请假归教务关切，不进联席的「最该盯」。
        stu_ts = [ts for ts, r, t in triples if r == 2]
        last_stu = stu_ts[-1] if stu_ts else None
        gap_days = None
        d_ls, d_nw = parse_dt(last_stu), parse_dt(newest)
        if d_ls and d_nw and d_nw >= d_ls:
            gap_days = round((d_nw - d_ls).total_seconds() / 86400.0, 1)
        if n_stu_sub == 0:
            interact, interact_lv = "沉默不回复", "bad"
            interact_evi = "窗口内学生/家长无实质发言"
        elif gap_days is not None and gap_days >= 14:
            interact, interact_lv = "近期沉默", "bad"
            interact_evi = f"最近一次本人发言距窗口末尾 {gap_days:.0f} 天"
        elif q >= 3 or n_stu_sub >= 12:
            interact, interact_lv = "积极", "ok"
            interact_evi = f"本人实质发言 {n_stu_sub} 条、主动提问 {q} 处"
        elif n_stu_sub <= 3:
            interact, interact_lv = "不积极", "warn"
            interact_evi = f"本人实质发言仅 {n_stu_sub} 条"
        else:
            interact, interact_lv = "一般", "warn"
            interact_evi = f"本人实质发言 {n_stu_sub} 条"
        urge_n, urge_ok, urge_fail = pair_after(triples, URGE, SUBMIT)      # 催交 → 是否交
        fix_n, fix_ok, fix_fail = pair_after(triples, FIX_REQ, FIX_ACK)     # 要求订正 → 是否改
        ask_lv = "高" if q >= 8 else ("中" if q >= 3 else ("低" if q >= 1 else "无"))
        # 老师反馈（学习情况分层的第三类证据）：只统计老师侧消息
        tea_pos = sum(hits(t, TEA_POS) for t in tea_txt)
        tea_neg = sum(hits(t, TEA_NEG) for t in tea_txt)
        if is_edu:
            # 教务视角：To-Do 面向「家长沟通 / 反馈学生问题 / 建立信任 / 推动续报」，并注入该生具体信息
            # 近 3 天待回应（v3.9）：联席忘记回复 / 问题未解决 —— 教务要盯闭环并向家长同步
            for _it in rt["items"][:3]:
                if _it["kind"] == "忘记回复":
                    todos.append(("高", f"{_it['at']} 联席未回复学生提问「{_it['q']}」：提醒联席当日回应，必要时先向家长说明", _it["evi"]))
                else:
                    todos.append(("高", f"{_it['at']} 提问「{_it['q']}」仍未解决：盯联席闭环，并把进展同步家长", _it["evi"]))
            if resp_slow:
                todos.append(("高", f"{resp_at} 该生提问后约 {resp_max_h:.0f} 小时才获回复：主动向家长说明并同步答疑进展，避免满意度下滑", resp_evi))
            if unresolved and not rt["items"]:
                todos.append(("高", f"该生仍有未闭环疑问{tp_s}，把跟进进展反馈家长，让家长看到服务闭环", unres_evi))
            if hw_bad > 0:
                todos.append(("中", f"该生作业出现「{hw_s}」，与家长沟通并商定家校督促方案", f"作业负面信号 {hw_bad} 处"))
            if att_bad > 0:
                todos.append(("中", f"该生近况涉及「{att_s}」，与家长确认并同步补课/回放安排", f"出勤相关信号 {att_bad} 处"))
            if neg_kws:
                todos.append(("中", f"家长/学生提及「{neg_s}」，主动关怀沟通、建立信任", f"负面情绪信号 {neg} 处"))
            if q >= 5:
                todos.append(("中", f"该生高频疑问集中在{tp or '多个知识点'}（{q} 处），向家长反馈薄弱点与提升建议，铺垫续报沟通", f"学生疑问密度高（{q} 处）"))
            if not todos:
                todos.append(("低", "常规家长沟通：同步学习近况、维护关系、铺垫续报", "无明显风险信号"))
        else:
            # 联席视角（v3.7）：To-Do 严格按「联席四维」优先级排 —— 互动状态 → 答疑闭环 → 催交作业
            # → 批改后订正 → 提问意愿。出勤/请假属教务关切（教务为续报负责），不再进入联席 To-Do，
            # 避免「确认出勤/补回放」这类教务动作抢走联席的「最该盯」。
            # 近 3 天待回应（v3.9）优先级最高：忘记回复 / 问题未解决必须当天处理
            for _it in rt["items"][:3]:
                if _it["kind"] == "忘记回复":
                    todos.append(("高", f"{_it['at']} 学生提问「{_it['q']}」至今未回复：今天内必须回应，先接住情绪再解题", _it["evi"]))
                else:
                    todos.append(("高", f"{_it['at']} 提问「{_it['q']}」老师应允后仍未解决：跟进到学生回复「懂了」为止", _it["evi"]))
            if interact_lv == "bad":
                todos.append(("高", f"学生互动{interact}（{interact_evi}）：主动发起一次一对一沟通，确认学习状态与真实卡点", interact_evi))
            elif interact_lv == "warn":
                todos.append(("中", f"学生互动{interact}（{interact_evi}）：课上多给一次点名提问 / 小黑板任务，把人调动起来", interact_evi))
            if resp_slow:
                todos.append(("高", f"{resp_at} 该生提问后约 {resp_max_h:.0f} 小时才回复：补一句追问确认问题已解决，避免悬空", resp_evi))
            if unresolved and not rt["items"]:
                todos.append(("高", f"回复学生未解答疑问{tp_s}（{unresolved_at} 提问）", unres_evi))
            if urge_n > urge_ok:
                _n = urge_n - urge_ok
                _ev = f"催交 {urge_n} 次、{_n} 次未见提交" + (f"（最近 {urge_fail[-1][0]} 提醒后仍未交）" if urge_fail else "")
                todos.append(("中", f"催交作业未响应 {_n} 次：改「私信 + 明确截止时间」再盯一次作业提交", _ev))
            if fix_n > fix_ok:
                _n = fix_n - fix_ok
                _ev = f"布置订正 {fix_n} 次、{_n} 次未见改正" + (f"（最近 {fix_fail[-1][0]}）" if fix_fail else "")
                todos.append(("中", f"批改后未订正 {_n} 次：挑最近一次错题要求回批，盯到改正为止", _ev))
            elif urge_n == 0 and hw_bad > 0:
                todos.append(("中", f"学生自述作业「{hw_s}」：针对性辅导或订正跟进", f"作业负面信号 {hw_bad} 处"))
            if ask_lv in ("低", "无") and interact_lv != "bad":
                _w = "偏少" if ask_lv == "低" else "为零"
                todos.append(("低", f"学生主动提问{_w}：课后抛一个「你觉得哪步最容易错」的引导题，把提问习惯带起来", f"窗口内主动提问 {q} 处"))
            if not todos:
                todos.append(("低", "维持常规跟进，关注下次作业与提问", "无明显风险信号"))
        students[stu] = {
            "group": g, "n_text": len(texts), "n_stu": n_stu, "n_stu_sub": n_stu_sub, "n_ack": n_ack,
            "n_tea": n_tea,
            "voice": voice, "newest": newest, "oldest": oldest,
            # att_n = 聊天中「请假/回放/没来/缺课」的提及次数（与 merge.py 的后台出勤字段 att 区分，避免覆盖）
            "att_n": att, "hw": hw, "q": q, "pos": pos, "neg": neg,
            "hw_neg": hw_neg, "att_leave": att_leave,
            "student_coop": student_coop, "parent_coop": parent_coop,
            # 群内发言者分布（v4.5）：判断配合度时区分「学生本人高频」与「家长是否出面」
            "speakers": speakers, "n_other": n_other, "parent_sig": parent_sig,
            "same_person": same_person,
            "top_topics": top_topics, "top_topics_evi": top_topics_evi,
            "topic_signals": topic_signals,
            "topic_mastered": topic_mastered, "unresolved": unresolved,
            "unresolved_at": unresolved_at, "unresolved_q": unresolved_q,
            "unres_evi": unres_evi,
            # 响应时效（聊天记录自算，弃用后台 isTimeout/isReplied）
            "resp_n": resp_n, "resp_max_h": resp_max_h, "resp_slow": resp_slow,
            "resp_slow_cnt": resp_slow_cnt, "resp_at": resp_at, "resp_q": resp_q,
            "resp_pend_n": len(pend), "resp_evi": resp_evi,
            "summary": summary, "todos": todos,
            # 联席四维（v3.7）
            "interact": interact, "interact_lv": interact_lv, "interact_evi": interact_evi,
            "gap_days": gap_days,
            "urge_n": urge_n, "urge_ok": urge_ok, "urge_fail": urge_fail,
            "fix_n": fix_n, "fix_ok": fix_ok, "fix_fail": fix_fail,
            "ask_lv": ask_lv,
            "tea_pos": tea_pos, "tea_neg": tea_neg,   # 老师反馈（学习分层第三类证据）
            # 学生成绩（v4.4，学习分层第四类证据）：群聊里定期收集的月考/期中成绩，认不出即「未知」
            "exam": exam,
            # 近 3 天待回应（v3.9）：忘记回复 / 问题未解决，报告与待办共同消费
            "recent3": rt,
            # 细粒度薄弱题型（v4.2）：知识点 → 具体方法/题型（带原文出处）
            "fine": fine_weaknesses(triples_all),
        }

    json.dump(students, open(f"{wd}/analysis.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_unres = sum(1 for v in students.values() if v["unresolved"])
    n_miss = sum(v["recent3"]["missed_n"] for v in students.values())
    n_open = sum(v["recent3"]["open_n"] for v in students.values())
    n_stu_rt = sum(1 for v in students.values() if v["recent3"]["items"])
    print(f"✅ 学生数 {len(students)} -> analysis.json（未闭环疑问 {n_unres} 人；"
          f"近 {RECENT_DAYS} 天待回应 {n_stu_rt} 人：忘记回复 {n_miss} 项 / 问题未解决 {n_open} 项）")


if __name__ == "__main__":
    main()
