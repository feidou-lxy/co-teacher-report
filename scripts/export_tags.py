"""导出「全量标签字典」给业务方（联席 / 教务）核对并补措施。

为什么需要这个脚本：
  报告里能判出来的标签分四层，散在四个文件里 ——
    · 沟通覆盖档位   → coverage.py       （6 类）
    · 学习分层档位   → common.learning_level（4 档 + 17 项加减分）
    · 问题级标签     → problems.py       （20 条 / 7 类）
    · 待办信号       → extract.py        （教务 9 条 / 联席 11 条）
  业务方只看报告看不到全貌（很多标签要触发了才出现），所以要一次性摊开。

⚠️ 本脚本**只读**，不改报告、不改判定。数据源：
    problems.PROBLEMS 直接引用（同源）；其余在本文件内以常量维护，
    任何一项判定改了（阈值 / 文案），必须同步这里，否则清单会和报告对不上。

用法：
    python3 export_tags.py            # 输出到 ~/Desktop/问题标签全集_v2.{html,txt}
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import problems as pb                                     # noqa: E402

OUT_DIR = os.path.expanduser("~/Desktop")


# 每个问题标签**在报告里到底显示在哪** —— 不给这一列，联席会拿着清单去找，
# 找不到就以为「系统没判」。2026-10-27 逐个对着 report.py 的渲染点核对过。
WHERE = {
    "interact_silent":  "卡片「关注理由」＋ Tab② 跟进表（入榜门槛）",
    "interact_quiet":   "卡片「关注理由」＋ Tab② 跟进表（入榜门槛）",
    "interact_passive": "卡片「关注理由」＋ Tab② 跟进表（入榜门槛）",
    "interact_chat":    "只在卡片「互动」结论里；**不进 Tab② 榜单**",
    "interact_noask":   "不显示为标签；转为 To-Do「引导提问」（提问密度低/无时）",
    "submit_tail":      "卡片「关注理由」（飞书风险，最多取 1 条）",
    "submit_zero":      "卡片「关注理由」（飞书风险）",
    "submit_low":       "卡片「关注理由」（飞书风险，严重度最低）",
    "submit_item_low":  "卡片「关注理由」（飞书风险）",
    "quality_down":     "卡片「关注理由」＋ 评级序列徽章「下滑 ↓」",
    "quality_low":      "评级徽章「无评级 / 样本不足」；**不进关注理由**",
    "board_blank":      "卡片「关注理由」（小黑板附加理由）",
    "board_unfin":      "卡片「关注理由」（小黑板附加理由）",
    "board_hard":       "卡片「关注理由」（小黑板附加理由，最优先补）",
    "score_low":        "卡片「成绩」行 ＋ 「关注理由」",
    "service_noresp":   "卡片 To-Do（高）",
    "service_unresolved": "卡片 To-Do（高）",
    "service_slow":     "卡片 To-Do（高）；距报告日 ≥7 天且已恢复沟通的降为灰底「后续注意」",
    "service_open":     "卡片 To-Do（高）",
    "attend_issue":     "卡片 To-Do（中）",
    "emotion":          "卡片 To-Do（中）",
}

# 教务侧经营类标签（不是「问题」，但教务会一起看，列出来免得再问一遍）
BIZ = [
    ("已续报", "后台「是否续报」= 是", "常规维护"),
    ("未续报 / 其他", "后台明确不是「是」", "了解原因，别默认是价格问题"),
    ("未确认", "后台值为「未设置」，或该生不在接口返回中", "**必须先确认**：这是真实业务状态，不是接口缺失"),
    ("多科目在读", "在读 ≥2 个科目", "扩科基础，优先维护"),
    ("单科在读", "仅 1 个科目在读", "扩科目标人群"),
    ("有扩科意向", "本人提及其他科目 + 报名/想学/咨询等意图词", "48 小时内跟进，热度会掉"),
]
VER = "v2"

# ══════════════════════════════════════════════════════════════════════
# 第 1 层：沟通与覆盖档位（coverage.py）—— 决定「归到哪一群」
# ══════════════════════════════════════════════════════════════════════
TIERS = [
    ("失联", "无聊天记录 **且** 无出勤记录（含只看回放）",
     "逐一核实是否退课 / 从未建群；确认仍在上就补建群并首次触达家长"),
    ("出勤但无沟通", "无聊天记录，**但**有出勤（含只看回放）",
     "人来了但群里没打通：核对群是否漏建，主动向家长发第一次反馈"),
    ("仅缺沟通通道", "有飞书表记录或小黑板作答，**只差企微群**",
     "不是失联，按常规补建群即可；学习侧已有证据，不必从头问起"),
    ("单向沟通", "有聊天记录，但学生·家长**实质发言 0 条**（只有老师在发）",
     "换沟通方式：把「有没有问题」换成需要回答的封闭问题"),
    ("低互动", "学生·家长实质发言 **1~2 条**（近 14 天）",
     "关注并适度引导，暂不必当风险处理"),
    ("正常", "沟通与出勤均正常", "—"),
]

# ══════════════════════════════════════════════════════════════════════
# 第 2 层：学习分层档位（common.learning_level）—— 决定「要不要重点跟」
#
# ⚠️ 2026-10-27 用户反馈：「累计分 ≤ −3 这种分值对联席是无感的」。
#   联席看不懂累计分，也不会因为 −3 去做什么。所以主列**只写问题本身**
#   （这类学生现在到底怎么了、拿什么证据判的、会踩哪些坑），
#   分数/位置压到最后一行小字，仅供回溯口径 —— 不要放回主列。
#   字段：档位 / 一句话问题 / 这类学生实际是什么样 / 在报告里怎么看（含口径小字）
# ══════════════════════════════════════════════════════════════════════
TIER_LV = [
    ("需重点关注",
     "**已经在掉队，而且不是单点问题** —— 这类学生是这一轮最该动的",
     "通常同时命中 2~3 条问题标签：群里只回「收到」或干脆不出声 ＋ 作业催了也不交 ＋ "
     "小黑板连续空白或反复同一类错 ＋ 成绩偏低（得分率 <65%）。"
     "不是「一次没考好」，而是**几类证据一起指向同一个结论**。\n"
     "⚠️ 两种容易误判的情况也要知道：① 成绩 <65% 会把人**封顶**在这一档，"
     "哪怕作业全交、群里很配合；② 白板连续空白会把人推进来，所以要先确认那不是「没交」而是「读图失败」。",
     "红色徽章 · Tab② 跟进表置顶第一段（口径：累计分 ≤ −3）"),
    ("需关注",
     "**有 1~2 处明确硬伤，其他地方还看得过去** —— 最容易漏掉的一批",
     "典型样子：作业连着 2 次没交，但课都上了；小黑板错得多，但每次都交了；"
     "群里只回「收到」不参与讨论，作业却不错；或者成绩偏低（<65%）把本来不错的表现压了下来。"
     "这类学生**表面还在**，掉下去不会有人喊，所以要在这一档就接住。",
     "橙色徽章 · Tab② 跟进表中段（口径：累计分 −2 ~ −1）"),
    ("正常",
     "**这一档有两种人，必须分开看：真没事的，和「没看见」的**",
     "① 真正常：该交的交了、该答的答了、有成绩且不难看，只是没亮点。\n"
     "② **证据不足**：没有考试成绩、没有小黑板读图、群里也没什么动静 —— "
     "落在这一档只是因为「没有东西可扣分」，不等于学得好。"
     "第二类要当成「缺数据」处理（补一次小黑板 / 问一次成绩），不要当成「不用管」。",
     "绿色徽章（口径：累计分 0 ~ +1）"),
    ("良好",
     "**有正面证据撑着，但不代表样样没问题**",
     "判进来的依据通常是其中一条：成绩得分率 ≥85%、或小黑板大部分轮次完整作答且无错、"
     "或老师在群里正面评价 ≥3 处。加分主要来自这几项，所以**作业漏交、群里不吭声仍可能同时存在**，"
     "别把它当成「什么都不用管」。",
     "蓝色徽章（口径：累计分 ≥ +2）"),
]

# 累计分的加减分明细（17 项，决定「凭什么判这一档」）
SCORES = [
    ("成绩", "得分率 ≥85%", "+3", "托底：再好也不会低于「良好」"),
    ("成绩", "得分率 65%~85%", "+1", ""),
    ("成绩", "得分率 <65%", "−3", "封顶：作业/配合再好，最高只能到「需关注」"),
    ("小黑板", "空白未提交 ≥40%", "−2", "参与度问题"),
    ("小黑板", "空白未提交 ≥20%", "−1", ""),
    ("小黑板", "存在实质错误 ≥30%", "−2", "能力证据，最需要针对性补"),
    ("小黑板", "存在实质错误 ≥20%", "−1", ""),
    ("小黑板", "仅未完成收尾 ≥85%", "−1", "课堂节奏 / 书写速度，不是不会"),
    ("小黑板", "完整作答且无错 ≥60%", "+2", ""),
    ("小黑板", "完整作答且无错 ≥40%", "+1", ""),
    ("小黑板", "作答趋势下滑 / 改善", "−1 / +1", ""),
    ("作业", "催交后 ≥2 次未见提交", "−2", "1 次为 −1"),
    ("作业", "催交后均已提交", "+1", ""),
    ("作业", "批改后 ≥2 次未改正", "−2", "1 次为 −1"),
    ("作业", "批改后均已改正", "+1", ""),
    ("作业", "自述「没写 / 不会做」≥2 处", "−1", ""),
    ("老师反馈", "群里指出问题，密度 ≥10（每 10 单位）", "−2", "≥5 为 −1，未超基线不加不减"),
    ("老师反馈", "正面评价 ≥3 处", "+2", "≥1 处为 +1"),
]

# ══════════════════════════════════════════════════════════════════════
# 第 3 层：待办信号（extract.py todos）—— 卡片上的「最该做 / 其次」
#   教务视角与联席视角的责任人不同，文案也不同，分两张表
# ══════════════════════════════════════════════════════════════════════
TODOS_EDU = [
    ("高", "提问 · 联席未回复", "学生提问后，直到窗口末尾群里**再无任何老师消息**",
     "提醒联席当日回应，必要时先向家长说明"),
    ("高", "提问 · 仍未解决", "老师有回复，但学生继续追问 / 老师只给「稍后、明天、回头」这类应允",
     "盯联席闭环，并把进展同步家长"),
    ("高", "回复迟缓", "提问后**约 ≥24 小时**才获回复（v4.60 起：距今 ≥7 天且后续仍在沟通、家长无不满的，降为灰底「后续注意」，不进此列）",
     "主动向家长说明并同步答疑进展"),
    ("高", "疑问 · 未闭环", "群里还有没讲完 / 没确认听懂的知识点",
     "把跟进进展反馈家长，让家长看到服务闭环"),
    ("中", "作业信号", "群里出现「没写 / 没交 / 不会做 / 忘了」这类表述",
     "与家长沟通并商定家校督促方案"),
    # ⚠️ v4.91：本行是**标签定义**（群里出现什么词算「出勤信号」），不是逐生结论。
    #   report.py 出卡时会再按后台出勤校准：有效出勤已满勤的学生，这条动作不再进「最该做」。
    ("中", "出勤信号", "请假 / 缺课 / 只看回放 / 迟到早退",
     "与家长确认并同步补课、回放安排"),
    ("中", "情绪 · 压力", "家长或学生提到焦虑、跟不上、不想学、烦",
     "主动关怀沟通、建立信任"),
    ("中", "疑问密度高", "同一学生疑问 ≥3 处，集中在某几个知识点",
     "向家长反馈薄弱点与提升建议，铺垫续报沟通"),
    ("低", "常规沟通", "窗口内无明显风险信号",
     "同步学习近况、维护关系、铺垫续报"),
]

TODOS_CO = [
    ("高", "提问 · 至今未回复", "学生提问后群里**再无老师消息**（含未识别出的 @ 提问）",
     "今天内必须回应，先接住情绪再解题"),
    ("高", "提问 · 应允后未解决", "老师回复过，但只给了应允词 / 学生 24h 内又追问同一问题",
     "跟进到学生回复「懂了」为止"),
    ("高", "互动 · 沉默不回复", "本人实质发言 0 条（「收到」不算），或近 14 天无发言",
     "主动发起一次一对一沟通，确认学习状态与真实卡点"),
    ("高", "回复迟缓 · 补问确认", "提问后约 ≥24 小时才回复",
     "补一句追问确认问题已解决，避免悬空"),
    ("高", "未解答疑问", "老师回复了但没回答那个具体问题",
     "把那个疑问单独讲一遍"),
    ("中", "互动 · 不积极", "本人实质发言 ≤3 条",
     "课上多给一次点名提问 / 小黑板任务，把人调动起来"),
    ("中", "催交作业未响应", "群里催了但学生没交、也没回话",
     "改「私信 + 明确截止时间」再盯一次"),
    ("中", "批改后未订正", "批改过了，但没见改正",
     "挑最近一次错题要求回批，盯到改正为止"),
    ("中", "学生自述作业不会", "学生自己说「不会做 / 没思路」",
     "针对性辅导或订正跟进"),
    ("低", "引导提问", "学生极少主动提问（0~2 次）",
     "课后抛一个「你觉得哪步最容易错」的引导题，把提问习惯带起来"),
    ("低", "常规跟进", "无明显风险信号", "关注下次作业与提问"),
]

# ══════════════════════════════════════════════════════════════════════
# 第 4 层：小黑板作答判定（pull_boards_api.py 产出 boards_summary + learning_level）
# ══════════════════════════════════════════════════════════════════════
BOARDS = [
    ("空白未提交", "题目发了，白板上什么都没写", "参与度问题 —— 这节课没跟上来",
     "**先分辨**是不是没交（读图失败也长这样）；确认没交就要问「卡在哪」，别只催交"),
    ("实质错误", "写完了，但方法或答案错了（已剔除「没写完」）", "能力问题，最需要针对性补",
     "**优先级最高**：这是唯一能直接定位到「哪个知识点不会」的证据，其他都可以往后放"),
    ("仅未完成收尾", "思路方向对，但来不及写完 / 没写结论", "课堂节奏、书写速度问题",
     "**不用单独补课**：提醒课上写快一点、先写结论，别按「不会」处理"),
    ("完整且无错", "写完整且没有实质错误", "正向素材，可用于家长沟通",
     "**不用处理**：直接拿去跟家长说「这节课他上得很扎实」"),
    ("读图失败", "视觉模型调用异常（技术故障）", "**不属于以上任何一类**，不进分母、不参与判定",
     "**不是学生的问题**：重跑读图即可，绝不能写进报告当「没交」"),
]


# ══════════════════════════════════════════════════════════════════════
# 渲染
# ══════════════════════════════════════════════════════════════════════
def md2html(t):
    """**粗体** → <b>（先转义再替换，避免 XSS / 星号残留）。"""
    import html as _h
    import re
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _h.escape(str(t or "")))


def md2txt(t):
    import re
    return re.sub(r"\*\*(.+?)\*\*", r"\1", str(t or ""))


CSS = """
*{box-sizing:border-box}
body{margin:0;padding:26px 28px 44px;background:#f5f6f8;
  font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1f2430}
.wrap{max-width:1240px;margin:0 auto}
h1{font-size:23px;margin:0 0 6px;letter-spacing:.3px}
.sub{color:#7b838d;font-size:13px;margin-bottom:20px;line-height:1.7}
.lead{background:#fff;border:1px solid #e6e9ee;border-left:4px solid #4a7fd6;
  border-radius:10px;padding:15px 18px;font-size:13px;line-height:1.85;margin-bottom:22px}
.lead b{color:#1f2430}
.lead .q{color:#8a9099}
h2{font-size:16.5px;margin:30px 0 8px;padding-left:10px;border-left:4px solid #1f2430}
h2 .cnt{font-size:12.5px;color:#9aa1ab;font-weight:400;margin-left:8px}
.desc{font-size:12.5px;color:#7b838d;margin:0 0 11px;line-height:1.7}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e6e9ee;
  border-radius:10px;overflow:hidden;margin-bottom:6px;font-size:13px}
th{background:#f0f2f6;text-align:left;padding:9px 11px;font-weight:700;
  font-size:12.5px;color:#3c4451}
td{padding:9px 11px;border-top:1px solid #eef1f6;vertical-align:top;line-height:1.7}
td.lab{font-weight:700;width:150px;color:#1f2430}
td.lab .no{font-weight:400;font-size:11.5px;color:#aab1ba;margin-right:5px}
td.cond{color:#4a515c;width:288px;font-size:12.6px}
td.sys{color:#8a7a5c;width:250px;font-size:12.4px}
td.fill{background:#fffdf3;width:225px}
td.fill2{background:#fffdf3;width:200px}
td.fill::after,td.fill2::after{content:"";display:block;height:32px}
tr.pri td.lab{color:#b8460f}
td.lab .where{display:block;margin-top:3px;font-size:10.8px;color:#9aa1ab;font-weight:400;line-height:1.55}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:20px;
  background:#eef2f8;color:#5a7ba6;margin-right:5px}
.tag.hi{background:#fdeceb;color:#c0392b}
.tag.mid{background:#fff4e2;color:#a86a00}
.note{background:#fff;border:1px solid #e6e9ee;border-radius:10px;
  padding:15px 18px;font-size:13px;line-height:1.9;margin-top:8px}
.note h4{margin:0 0 8px;font-size:13.5px}
.note ol{margin:0;padding-left:22px}
.wline{border-bottom:1px dashed #dfe3e9;height:30px;font-size:12.5px;
  color:#9aa1ab;line-height:26px;margin-top:8px}
.foot{margin-top:26px;font-size:12px;color:#9aa1ab;text-align:center;line-height:1.8}
@media print{
  @page{size:A4 landscape;margin:9mm}
  body{background:#fff;padding:0;font-size:12px}
  table{font-size:11.4px}
  td.fill::after,td.fill2::after{height:44px}
  h2{page-break-after:avoid}
  tr{page-break-inside:avoid}
}
"""

HEAD = """<tr><th>标签</th><th>怎么判出来（人话）</th><th>系统现在给的动作</th>
<th>你们实际会怎么做</th><th>需要什么支持</th></tr>"""


def esc(t):
    return md2html(t)


def build_html():
    H = []
    H.append(f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>问题标签全集（供联席补措施）</title><style>{CSS}</style></head><body><div class="wrap">
<h1>学情报告 · 问题标签全集</h1>
<div class="sub">系统当前<b>能判出来</b>的全部标签：{len(TIERS)} 档位 + {len(TIER_LV)} 分层 +
{len(pb.PROBLEMS)} 问题 + {len(TODOS_EDU) + len(TODOS_CO)} 待办信号 + {len(BOARDS)} 小黑板判定
= <b>{len(TIERS) + len(TIER_LV) + len(pb.PROBLEMS) + len(TODOS_EDU) + len(TODOS_CO) + len(BOARDS)} 条</b>
（另有 {len(SCORES)} 项加减分、{len(BIZ)} 条经营类）　｜　导出 {__import__('datetime').datetime.now():%Y-%m-%d %H:%M}</div>

<div class="lead">
<b>这份表是干什么的</b>：把系统<b>现在能判出来的一切</b>摊开，请你们在<span class="q">浅黄色两列</span>里补
<b>真实会怎么做</b>。现在很多标签后面跟的动作只是规则话术（「与家长沟通并商定家校督促方案」），
落地还得靠自己想办法 —— 补完我按标签回写，以后报告直接给可执行动作。<br>
<b>标签分五层，从粗到细</b>：① <b>沟通与覆盖</b>（归到哪一群）→ ② <b>学习分层</b>（要不要重点跟）→
③ <b>问题级标签</b>（他到底卡在哪，<b>最需要你们补</b>）→ ④ <b>待办信号 / 小黑板</b>（今天该先做哪件事）→
⑤ <b>经营类</b>（续报、扩科，不算问题）。<br>
<b>不用全填</b>。建议顺序：<b>第三节（问题标签）→ 第五节（待办信号）→ 其余</b>。
每条标签下面那行 📍 写的是「它在报告哪里能看到」，方便你们对着报告核对。
反映「系统判错了」比补措施更重要 —— 那是判定 bug，我优先修。<br>
<b>⚠️ 别再讲分数</b>：报告里的「累计分 / −3 分」只是内部算法产物，对联席、对家长都没有意义。
要说明一个学生的情况，请用第二节（这类学生实际是什么样）和第三节（他卡在哪一条）的人话。
</div>""")

    # ── 一、沟通与覆盖 ──
    H.append(f'<h2>一 · 沟通与覆盖档位<span class="cnt">{len(TIERS)} 类 ｜ 报告 Tab①「失联预警」</span></h2>'
             '<p class="desc">按「有没有群、群里有没有人说话」分档。这一层决定学生被归到哪一组，'
             '也决定顶部 KPI 的四个数字。<b>一个学生只会落在其中一档。</b></p><table>' + HEAD)
    for i, (lab, cond, act) in enumerate(TIERS, 1):
        H.append(f'<tr><td class="lab"><span class="no">{i}</span>{esc(lab)}</td>'
                 f'<td class="cond">{esc(cond)}</td><td class="sys">{esc(act)}</td>'
                 f'<td class="fill"></td><td class="fill2"></td></tr>')
    H.append('</table>')

    # ── 二、学习分层 ──
    H.append(f'<h2>二 · 学习分层档位<span class="cnt">{len(TIER_LV)} 档 ｜ 卡片右上角徽章</span></h2>'
             '<p class="desc">由成绩、小黑板、作业、老师反馈几类证据累计得出（算法见第四节）。'
             '成绩有「托底 / 封顶」效果：成绩好至少「良好」，成绩偏低最多「需关注」。<br>'
             '<b>这一节请只看第二列</b> —— 联席不用理解分数，直接看「这类学生现在是什么状况、该担心什么」。'
             '分数写在最后一列括号里，只用于回溯口径。</p>'
             '<table><tr><th>档位</th><th>这类学生实际是什么样（人话）</th><th>在报告里怎么看</th>'
             '<th>你们实际会怎么做</th><th>需要什么支持</th></tr>')
    for i, (lab, headline, cond, where) in enumerate(TIER_LV, 1):
        # cond 用 \n 分段：md2html 会整段转义，所以必须先按行切再转义，不能直接塞 <br>
        _c = "<br>".join(esc(x) for x in str(cond).split("\n"))
        H.append(f'<tr><td class="lab" style="width:196px"><span class="no">{i}</span>{esc(lab)}'
                 f'<div style="font-weight:500;color:#5a6270;font-size:12px;'
                 f'line-height:1.65;margin-top:5px">{esc(headline)}</div></td>'
                 f'<td class="cond" style="width:auto">{_c}</td>'
                 f'<td class="sys" style="width:158px;font-size:11.8px;color:#8a9099">{esc(where)}</td>'
                 f'<td class="fill"></td><td class="fill2"></td></tr>')
    H.append('</table>')

    # ── 三、问题级标签（problems.py，核心）──
    H.append(f'<h2>三 · 问题级标签<span class="cnt">{len(pb.PROBLEMS)} 条 / {len(pb.CATS)} 类 ｜ '
             f'卡片上的「关注理由」＋ Tab② 跟进表 —— 本节最需要你们补</span></h2>'
             '<p class="desc">这一层是「他到底卡在哪」。报告卡片上那几个彩色小标签就是这些，'
             '同一个学生通常同时命中 2~3 条（如「沉默不回复」＋「连续 3 次未提交」）。<br>'
             '「系统现在给的动作」这一列就是<b>现状</b>：有的还是规则话术（「与家长沟通并商定家校督促方案」），'
             '有的已经比较具体但未必符合你们的实际打法 —— 你们补的那一列会<b>直接替换</b>它。</p>')
    n = 0
    for cat, cname, cdesc in pb.CATS:
        items = pb.by_cat(cat)
        if not items:
            continue
        H.append(f'<h2 style="font-size:14.5px;border-left-color:#8fa8c8;margin-top:20px">'
                 f'{esc(cname)}<span class="cnt">{len(items)} 条 ｜ {esc(cdesc)}</span></h2><table>' + HEAD)
        for p in items:
            n += 1
            _w = WHERE.get(p["k"], "")
            H.append(f'<tr><td class="lab"><span class="no">{n}</span>{esc(p["label"])}'
                     + (f'<span class="where">📍 {esc(_w)}</span>' if _w else "")
                     + '</td>'
                     f'<td class="cond">{esc(p["cond"])}<br>'
                     f'<span style="color:#a8afb8;font-size:11.6px">完成标准：{esc(p["done"])}</span></td>'
                     f'<td class="sys">{esc(p["act"])}</td>'
                     f'<td class="fill"></td><td class="fill2"></td></tr>')
        H.append('</table>')

    # ── 四、学习分层的加减分明细 ──
    H.append(f'<h2>四 · 档位是怎么算出来的<span class="cnt">{len(SCORES)} 项 ｜ 内部口径，不用给联席解释</span></h2>'
             '<p class="desc"><b>分数只用来排序和算档，对外请直接说问题</b>（「这孩子连续 3 次作业没交」'
             '比「他 −3 分」有用得多）。这一层不显示成标签，但直接决定第二层的档位。'
             '如果你们觉得某个学生「不该被判需重点关注」，多半是这里某一项的权重不对 —— 请直接说哪一项。</p>'
             '<table><tr><th>证据来源</th><th>触发条件</th><th>加减分</th><th>这一项说明什么</th>'
             '<th>你们觉得权重对不对 / 想怎么改</th></tr>')
    for src, cond, d, memo in SCORES:
        H.append(f'<tr><td class="lab" style="width:86px">{esc(src)}</td>'
                 f'<td class="cond" style="width:300px">{esc(cond)}</td>'
                 f'<td style="width:70px;font-weight:700;color:{"#c0392b" if d.startswith("−") else "#1e7b45" if d.startswith("+") else "#8a9099"}">{esc(d)}</td>'
                 f'<td class="sys" style="width:240px">{esc(memo) or "—"}</td>'
                 f'<td class="fill"></td></tr>')
    H.append('</table>')

    # ── 五、待办信号 ──
    for title, rows, who in [
        ("五 · 待办信号（教务视角）", TODOS_EDU, "报告给<b>教务</b>看的 To-Do：家长沟通、续报铺垫为主"),
        ("六 · 待办信号（联席视角）", TODOS_CO, "报告给<b>联席</b>看的 To-Do：学习闭环、答疑、批改为主"),
    ]:
        H.append(f'<h2>{title}<span class="cnt">{len(rows)} 条</span></h2>'
                 f'<p class="desc">{who}。同一件事在两个视角下动作不同，所以分两张表。</p>'
                 '<table><tr><th>优先级</th><th>信号</th><th>怎么判出来</th>'
                 '<th>系统现在给的动作</th><th>你们实际会怎么做</th></tr>')
        for pri, lab, cond, act in rows:
            cls = "hi" if pri == "高" else ("mid" if pri == "中" else "")
            H.append(f'<tr><td class="lab" style="width:74px">'
                     f'<span class="tag {cls}">{esc(pri)}</span></td>'
                     f'<td style="width:150px;font-weight:600">{esc(lab)}</td>'
                     f'<td class="cond">{esc(cond)}</td><td class="sys">{esc(act)}</td>'
                     f'<td class="fill" style="width:280px"></td></tr>')
        H.append('</table>')

    # ── 七、小黑板 ──
    H.append(f'<h2>七 · 小黑板作答判定<span class="cnt">{len(BOARDS)} 类 ｜ 卡片「课堂小黑板」块</span></h2>'
             '<p class="desc">一节课的板书按轮次判定。判出来的是<b>事实</b>（写没写、对不对），'
             '这里直接给「该怎么看待这类学生」（权重口径见第四节，不用管分数）。'
             '但「空白」到底是没听、还是设备问题，仍需要你们补。</p>'
             '<table><tr><th>判定</th><th>什么情况</th><th>系统怎么理解</th>'
             '<th>这类要不要单独动</th><th>你们实际会怎么处理</th></tr>')
    for lab, cond, mean, sc in BOARDS:
        H.append(f'<tr><td class="lab" style="width:110px">{esc(lab)}</td>'
                 f'<td class="cond">{esc(cond)}</td><td class="sys" style="width:200px">{esc(mean)}</td>'
                 f'<td style="width:280px;color:#4a515c;font-size:12.4px;line-height:1.7">{esc(sc)}</td>'
                 f'<td class="fill"></td></tr>')
    H.append('</table>')

    # ── 八、教务经营类标签（附录，不算「问题」）──
    H.append(f'<h2>八 · 教务经营类标签（附录）<span class="cnt">{len(BIZ)} 条 ｜ '
             f'报告顶部 KPI / 教务 Tab</span></h2>'
             '<p class="desc">这一层不是「学生的问题」，是教务日常要分的人群。'
             '列出来是为了避免重复讨论 —— 如果这几条也需要细化动作，告诉我。</p>'
             '<table><tr><th>标签</th><th>怎么判出来</th><th>系统现在给的动作</th>'
             '<th>你们实际会怎么做</th><th>需要什么支持</th></tr>')
    for lab, cond, act in BIZ:
        H.append(f'<tr><td class="lab" style="width:120px">{esc(lab)}</td>'
                 f'<td class="cond">{esc(cond)}</td><td class="sys">{esc(act)}</td>'
                 f'<td class="fill"></td><td class="fill2"></td></tr>')
    H.append('</table>')

    # ── 九、组合打法与反馈 ──
    H.append(f"""<h2>九 · 组合打法（比单条更重要）</h2>
<p class="desc">一个学生通常同时命中 2~3 条。单条措施各自都合理，组合起来往往打架
（例如「成绩偏低要补基础」＋「连续未交要施压」同时做，学生会更躲）。
请写 2~3 套你们常用的组合，我据此定「先做哪个」。</p>
<div class="note"><h4>常见组合（已有雏形，请补充 / 纠正）</h4>
<div class="wline">组合 1：（命中哪些标签）→ 先做什么 → 后做什么</div>
<div class="wline">组合 2：</div>
<div class="wline">组合 3：</div>
</div>

<h2>十 · 反馈方式</h2>
<div class="note"><h4>三种都可以，怎么方便怎么来</h4><ol>
<li><b>直接在表上写</b>：打印后手写，拍照发我（表格留白已按 A4 横向加大）。</li>
<li><b>按编号回</b>：第三节的 {len(pb.PROBLEMS)} 个问题标签带编号 <b>#1 ~ #{len(pb.PROBLEMS)}</b>，
微信里说「#13 我们一般先私信再电话」即可；其他章节直接说名称（如「出勤异常」）。</li>
<li><b>只标问题</b>：如果某条你觉得「系统判错了 / 根本不是问题」，标出来就行 —— 这个优先处理。</li>
</ol>
<p style="margin:11px 0 0;color:#7b838d;font-size:12.4px">
两个口径请留意：① 所有「条数」都按<b>报告的分析窗口</b>统计（当前一般为近 14 天），不是全学期；
② 「实质发言」不含「收到」「谢谢老师」「好的」这类应答，只算带内容的。</p>
</div>

<div class="foot">本表由学情报告技能自动导出（export_tags.py），标签与报告判定逻辑同源（判定以代码为准）。<br>
标签名或判据如有调整，本表随之更新 —— 请勿在旧版本上批注，以免对不上。</div>
</div></body></html>""")
    return "".join(H)


def build_txt():
    L = []
    A = L.append
    A("学情报告 · 全量标签清单（供联席补措施）")
    A("=" * 62)
    A("把系统现在能判出来的一切摊开，请在每条「你们实际会怎么做：」后面直接写。")
    A("现在很多标签后面跟的动作只是规则话术（「与家长沟通并商定家校督促方案」），")
    A("落地还得靠自己想办法 —— 补完我按标签回写，以后报告直接给可执行动作。")
    A("")
    A(f"共 {len(TIERS) + len(TIER_LV) + len(pb.PROBLEMS) + len(TODOS_EDU) + len(TODOS_CO) + len(BOARDS)} 条："
      f"{len(TIERS)} 档位 + {len(TIER_LV)} 分层 + {len(pb.PROBLEMS)} 问题"
      f" + {len(TODOS_EDU) + len(TODOS_CO)} 待办信号 + {len(BOARDS)} 小黑板判定"
      f"（另有 {len(SCORES)} 项加减分、{len(BIZ)} 条经营类）")
    A("")
    A("分五层，从粗到细：")
    A("  ① 沟通与覆盖（归到哪一群）→ ② 学习分层（要不要重点跟）")
    A("  → ③ 问题级标签（他到底卡在哪，最需要补）")
    A("  → ④ 待办信号 / 小黑板（今天该先做哪件事）→ ⑤ 经营类（续报扩科，不算问题）")
    A("")
    A("建议填写顺序：三（问题标签）→ 五/六（待办信号）→ 其余。不用全填。")
    A("每条「报告里在」写的是它在报告哪里能看到，方便对着报告核对。")
    A("反映「系统判错了」比补措施更重要，那是判定 bug，优先修。")
    A("")

    A("─" * 62)
    A(f"一 · 沟通与覆盖档位（{len(TIERS)} 类）｜ 报告 Tab① 失联预警")
    A("─" * 62)
    for i, (lab, cond, act) in enumerate(TIERS, 1):
        A(f"{i}. {md2txt(lab)}")
        A(f"   判据：{md2txt(cond)}")
        A(f"   现有动作：{md2txt(act)}")
        A("   你们实际会怎么做：")
        A("")

    A("─" * 62)
    A(f"二 · 学习分层档位（{len(TIER_LV)} 档）｜ 卡片右上角徽章")
    A("─" * 62)
    A("不用管分数，只看「这类学生现在是什么状况」。分数在括号里，仅用于回溯口径。")
    A("")
    for i, (lab, headline, cond, where) in enumerate(TIER_LV, 1):
        A(f"{i}. 【{md2txt(lab)}】{md2txt(headline)}")
        A(f"   在报告里：{md2txt(where)}")
        for ln in md2txt(cond).split("\n"):
            A(f"   {ln.strip()}")
        A("   你们实际会怎么做：")
        A("")

    A("─" * 62)
    A(f"三 · 问题级标签（{len(pb.PROBLEMS)} 条 / {len(pb.CATS)} 类）★ 最需要补")
    A("─" * 62)
    n = 0
    for cat, cname, cdesc in pb.CATS:
        items = pb.by_cat(cat)
        if not items:
            continue
        A("")
        A(f"【{cname}】{md2txt(cdesc)}（{len(items)} 条）")
        for p in items:
            n += 1
            A(f"")
            A(f"{n}. {md2txt(p['label'])}")
            if WHERE.get(p["k"]):
                A(f"   报告里在：{md2txt(WHERE[p['k']])}")
            A(f"   判据：{md2txt(p['cond'])}")
            A(f"   现有措施：{md2txt(p['act'])}")
            A(f"   完成标准：{md2txt(p['done'])}")
            A(f"   你们实际会怎么做：")

    A("")
    A("─" * 62)
    A(f"四 · 学习分层的加减分明细（{len(SCORES)} 项）｜ 解释「凭什么判这一档」")
    A("─" * 62)
    A("觉得某学生「不该被判需重点关注」，多半是这里某项权重不对，请直接说哪一项。")
    A("")
    for src, cond, d, memo in SCORES:
        A(f"· [{src}] {md2txt(cond)} → {d}" + (f"（{md2txt(memo)}）" if memo else ""))
    A("")
    A("   你们觉得哪一项需要调整：")

    for title, rows, who in [
        (f"五 · 待办信号（教务视角，{len(TODOS_EDU)} 条）", TODOS_EDU, "家长沟通、续报铺垫为主"),
        (f"六 · 待办信号（联席视角，{len(TODOS_CO)} 条）", TODOS_CO, "学习闭环、答疑、批改为主"),
    ]:
        A("")
        A("─" * 62)
        A(title + f"｜{who}")
        A("─" * 62)
        for pri, lab, cond, act in rows:
            A(f"[{pri}] {md2txt(lab)}")
            A(f"   判据：{md2txt(cond)}")
            A(f"   现有动作：{md2txt(act)}")
            A("   你们实际会怎么做：")
            A("")

    A("")
    A("─" * 62)
    A(f"七 · 小黑板作答判定（{len(BOARDS)} 类）")
    A("─" * 62)
    for lab, cond, mean, sc in BOARDS:
        A(f"· {md2txt(lab)}：{md2txt(cond)}")
        A(f"   系统理解：{md2txt(mean)}")
        A(f"   这类要不要单独动：{md2txt(sc)}")
        A("   你们实际会怎么处理：")
        A("")

    A("")
    A("─" * 62)
    A(f"八 · 教务经营类标签（附录，{len(BIZ)} 条）｜ 不算「问题」，是教务分人群用的")
    A("─" * 62)
    for lab, cond, act in BIZ:
        A(f"· {md2txt(lab)}：{md2txt(cond)}")
        A(f"   现有动作：{md2txt(act)}")
        A("   你们实际会怎么做：")
        A("")

    A("")
    A("─" * 62)
    A("九 · 组合打法（比单条更重要）")
    A("─" * 62)
    A("一个学生常同时命中 2~3 条，单条措施组合起来会打架。")
    A("请写 2~3 套你们常用的组合，我据此定「先做哪个」。")
    A("")
    A("组合 1（命中哪些标签 → 先做什么 → 后做什么）：")
    A("组合 2：")
    A("组合 3：")
    A("")
    A("─" * 62)
    A("十 · 反馈方式")
    A("─" * 62)
    A("① 打印后手写拍照；② 微信按编号回（如「#13 我们一般先私信再电话」）；")
    A("③ 只标「系统判错了」的条目 —— 这个优先处理。")
    A("")
    A("口径提醒：所有「条数」按报告的分析窗口统计（当前一般为近 14 天），非全学期；")
    A("「实质发言」不含「收到」「谢谢老师」「好的」这类应答。")
    A("")
    A("（本表由 export_tags.py 自动导出，与报告判定同源。请勿在旧版本上批注。）")
    return "\n".join(L)


def main():
    html = build_html()
    txt = build_txt()
    p1 = os.path.join(OUT_DIR, f"全量标签清单_{VER}.html")
    p2 = os.path.join(OUT_DIR, f"全量标签清单_{VER}.txt")
    open(p1, "w", encoding="utf-8").write(html)
    open(p2, "w", encoding="utf-8").write(txt)
    n = len(TIERS) + len(TIER_LV) + len(pb.PROBLEMS) + len(TODOS_EDU) + len(TODOS_CO) + len(BOARDS)
    print(f"✅ 标签总数 {n}：档位 {len(TIERS)} + 分层 {len(TIER_LV)} + 问题 {len(pb.PROBLEMS)}"
          f" + 待办 {len(TODOS_EDU) + len(TODOS_CO)} + 小黑板 {len(BOARDS)}")
    print(f"   加减分明细 {len(SCORES)} 项")
    print(f"   {p1}\n   {p2}")


if __name__ == "__main__":
    main()
