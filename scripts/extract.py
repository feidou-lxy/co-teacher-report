"""步骤6：信号提取（聊天 -> 学情结构化）。

仅分析 --window 起点之后的聊天（默认 2026-09-01）。
提取：薄弱知识点、出勤/作业/提问/情绪信号、配合度（学生自驱 vs 家长支持，启发式）、
未闭环疑问、总评与待办。配合度数据源未区分发言者 ID，为启发式估算。

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
    "立体几何/空间向量": ["立体几何", "空间向量", "法向量", "建系", "异面直线", "投影", "外接球", "二面角", "线面", "面面", "垂直", "平行", "坐标", "几何体"],
    "直线和圆": ["直线和圆", "圆", "圆心距", "切线", "直线"],
    "函数与导数": ["导数", "函数", "单调性", "极值", "求导", "切线"],
    "数列": ["数列", "等差", "等比", "求和", "通项"],
    "圆锥曲线": ["椭圆", "双曲线", "抛物线", "圆锥", "焦点", "离心率"],
    "三角函数": ["三角函数", "正弦", "余弦", "周期", "诱导"],
    "概率统计": ["概率", "统计", "排列", "组合", "期望", "方差"],
    "解三角形": ["解三角形", "正弦定理", "余弦定理"],
    "不等式/参数": ["不等式", "恒成立", "参数", "取值范围"],
    "计算/粗心": ["粗心", "算错", "计算", "失误", "符号", "少一种", "漏解", "增根"],
}


def get_text(m):
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        for k in ("content", "text"):
            if k in c and isinstance(c[k], str):
                return c[k]
    return ""


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
    """解析消息时间戳（YYYY-MM-DDTHH:MM:SS）为 datetime，失败返回 None。"""
    try:
        return datetime.datetime.strptime((ts or "")[:19], "%Y-%m-%dT%H:%M:%S")
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
    args = ap.parse_args()
    role = common.resolve_scope(args)[0]
    is_edu = role == "edu"
    wd = common.workdir(args)
    CUT = args.window
    d = json.load(open(f"{wd}/chats.json", encoding="utf-8"))

    students = {}
    for stu, blk in d.items():
        g = blk["group"]
        # 接口返回为倒序（最新在前）；此处显式按时间升序排序，消除后续对顺序的隐式依赖
        msgs = [m for m in blk["messages"] if (m.get("msgTime") or "") >= CUT]
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
                "student_coop": 0, "parent_coop": 0, "top_topics": [],
                "unresolved": False, "unresolved_at": "", "unresolved_q": "",
                "unres_evi": "窗口内无聊天",
                "resp_n": 0, "resp_max_h": 0.0, "resp_slow": False, "resp_slow_cnt": 0,
                "resp_at": "", "resp_q": "", "resp_pend_n": 0,
                "resp_evi": "窗口内无聊天",
                "summary": f"{CUT} 起该群无可分析聊天记录（窗口内无互动）。",
                "todos": empty_todos,
                # 联席四维（v3.7）：无聊天 → 互动沉默、无催交/订正记录、提问为零
                "interact": "沉默不回复", "interact_lv": "bad", "interact_evi": "窗口内无互动",
                "gap_days": None,
                "urge_n": 0, "urge_ok": 0, "urge_fail": [],
                "fix_n": 0, "fix_ok": 0, "fix_fail": [],
                "ask_lv": "无",
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
        # 配合度：仅用实质消息判定；致谢最多计 4 分，避免「收到/谢谢」把配合度刷满
        student_coop = 0 if n_stu_sub == 0 else 1 + min(4, stu_self // 4)
        parent_coop = 0 if n_stu_sub == 0 else 1 + min(4, (parent + min(pos, 4)) // 2)
        tc = Counter()
        for name, kws in TOPICS.items():
            c = hits(all_txt, kws)
            if c:
                tc[name] = c
        top_topics = [t for t, _ in tc.most_common(3)]
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
        if is_edu:
            # 教务视角：To-Do 面向「家长沟通 / 反馈学生问题 / 建立信任 / 推动续报」，并注入该生具体信息
            if resp_slow:
                todos.append(("高", f"{resp_at} 该生提问后约 {resp_max_h:.0f} 小时才获回复：主动向家长说明并同步答疑进展，避免满意度下滑", resp_evi))
            if unresolved:
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
            if interact_lv == "bad":
                todos.append(("高", f"学生互动{interact}（{interact_evi}）：主动发起一次一对一沟通，确认学习状态与真实卡点", interact_evi))
            elif interact_lv == "warn":
                todos.append(("中", f"学生互动{interact}（{interact_evi}）：课上多给一次点名提问 / 小黑板任务，把人调动起来", interact_evi))
            if resp_slow:
                todos.append(("高", f"{resp_at} 该生提问后约 {resp_max_h:.0f} 小时才回复：补一句追问确认问题已解决，避免悬空", resp_evi))
            if unresolved:
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
            "top_topics": top_topics, "unresolved": unresolved,
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
        }

    json.dump(students, open(f"{wd}/analysis.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_unres = sum(1 for v in students.values() if v["unresolved"])
    print(f"✅ 学生数 {len(students)} -> analysis.json（未闭环疑问 {n_unres} 人）")


if __name__ == "__main__":
    main()
