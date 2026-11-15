"""英语学科识别词表 + 全学段知识树（v5.20：为英语学科的学生分析打底）

════════════════════════════════════════════════════════════════

背景：知识识别此前建了 数学（以后台树为准） / 物理（初中后台为准＋高中教材
自建） / 化学（教材自建） / 语文（教材自建）四科词表。英语学科的教务报告
已接入（呆呆团队：孙紫菁 v5.19、刘颖越 v5.20），英语课硬套其他词表会把
「函数」「文言文」这类话术认成英语薄弱点，所以英语必须走自己的词表。

三层依据（与 chinese_dict 同构）：

  ① **范围：人教版英语教材目录**（`TEXTBOOK_TREE`，逐册逐单元）：
     初一 = 七上（2024秋修订版）+ 七下（2025春修订版）
     初二 = 八上（2025秋修订版）+ 八下（2026春修订版）
     初三 = 九上（2026秋修订版）+ 九下（2012课标旧版，修订版 2027 春才启用）
     高一 = 必修第一册 + 必修第二册（人教版 2019）
     高二 = 必修第三册 + 选择性必修第一册（教学惯例进度）
     高三 = 选择性必修第二/三/四册 ＋ 高考一轮复习
     目录来源 = 人教社官网配套音频页 / 电子课本网（2026-11-15 核对各册
     实际版次；初中正处修订换版期，每秋推进一个年级，与语文同节奏）。

  ② **措辞：英语课群聊自然说法**。英语知识点 = 「语言能力 + 语法模块 +
  题型」，老师/家长在群里说的是「背单词」「默写」「时态」「完形填空」
  「作文范文」「听力」「音标」这类**任务与模块**词。词表按此设计，
  并甄别掉危险裸词（见下方坑清单）。
     ⚠️ 本表尚未经过真实英语课群聊语料反查（首个英语课程跑批后必须
     人工抽查一次命中，按「教材直采」规则补 UNIT_EXCL 排除词）。

  ③ **后台对齐：后台英语知识树为空**（2026-11-15 实拉 --subject 6：
     653 个节点里英语 0 个，全为数理化语），英语采用「教材为准」自建，
     与语文/高中物理同构，**不做**数学式「后台为准剔除」。若后台日后
     建了英语树（pull_knowledge.py --subject 6 --stages 1,2），再评估对齐。

语料设计坑清单（新加词前先读一遍，别再踩）
────────────────────────────────────────
  · 本表只在**英语课**启用（knowledge_dict 按课程科目分派），所以
    「默写」「翻译」「背诵」「改错」这类跨科目歧义词在英语语境下可收；
    但绝不能回流到其他科目的词表（语文 v4.47 删裸词「计算」的教训同源）。
  · 「阅读」不裸收 ——「阅读打卡」「课外阅读」边界模糊，
    靠「阅读理解」「阅读表达」「完形填空」等复合词撑识别。
  · 「听力」「口语」在英语课语境几乎只指听力题/口语任务，可收；
    「听写」是单词默写任务，归词汇单元不归听力。
  · 「单词」「词汇」「背单词」是英语课最强任务信号，必收；
    「打卡」跨场景（阅读打卡/单词打卡）不裸收，只收「单词打卡」。
  · 「作文」「写作」可收（英语课语境指书面表达）；「日记」既是应用文
    题型也是日常词，暂收「英语日记」不带裸「日记」。
  · 「选择题」「大题」「卷子」是泛考试词，任何科目都命中，不收。
  · 「音标」「发音」「语调」初中最强，高中「重音」「连读」收。
  · 语法术语（时态/从句/比较级…）都是「术语词」不是日常词，安全。
"""
import re

# ══════════════════════════════════════════════════════════════════════
# 一、全学段知识树（人教版英语，初一到高三，v5.20）
#    结构：年级 → 册 → (单元, [课文/小节,…])。单元标题保持教材原文
#    （Section A/B 不逐条展开，课文粒度=单元），供口径展示 / 备课对齐用；
#    识别走下面的 TOPIC_UNITS（能力模块颗粒度，比单元粗）。
# ══════════════════════════════════════════════════════════════════════
TEXTBOOK_TREE = [
    {
        "grade": "初一", "books": [
            {"book": "七年级上册（2024秋修订版）", "units": [
                ("Starter Units", ["Starter Unit 1 Hello!", "Starter Unit 2 Keep Tidy!", "Starter Unit 3 Welcome!"]),
                ("Unit 1-7", ["Unit 1 You and Me", "Unit 2 We're Family!", "Unit 3 My School",
                              "Unit 4 My Favourite Subject", "Unit 5 Fun Clubs",
                              "Unit 6 A Day in the Life", "Unit 7 Happy Birthday!"]),
            ]},
            {"book": "七年级下册（2025春修订版）", "units": [
                ("Unit 1-8", ["Unit 1 Animal Friends", "Unit 2 No Rules, No Order", "Unit 3 Keep Fit",
                              "Unit 4 Eat Well", "Unit 5 Here and Now", "Unit 6 Rain or Shine",
                              "Unit 7 A Day to Remember", "Unit 8 Once upon a Time"]),
            ]},
        ],
    },
    {
        "grade": "初二", "books": [
            {"book": "八年级上册（2025秋修订版）", "units": [
                ("Unit 1-8", ["Unit 1 Happy Holiday", "Unit 2 Home Sweet Home", "Unit 3 Same or Different?",
                              "Unit 4 Amazing Plants and Animals", "Unit 5 What a Delicious Meal!",
                              "Unit 6 Plan for Yourself", "Unit 7 When Tomorrow Comes",
                              "Unit 8 Let's Communicate!"]),
            ]},
            {"book": "八年级下册（2026春修订版）", "units": [
                ("Unit 1-8", ["Unit 1 Time to Relax", "Unit 2 Stay Healthy", "Unit 3 Growing Up",
                              "Unit 4 The Wonders of Nature", "Unit 5 Nature's Temper",
                              "Unit 6 Crossing Cultures", "Unit 7 A Good Read", "Unit 8 Making a Difference"]),
            ]},
        ],
    },
    {
        "grade": "初三", "books": [
            {"book": "九年级上册（2026秋修订版）", "units": [
                ("Unit 1-8", ["Unit 1 The Changing World", "Unit 2 Inspiring People", "Unit 3 Smart Learning",
                              "Unit 4 Our Memory", "Unit 5 Power of Ideas", "Unit 6 Beyond Earth",
                              "Unit 7 Feel the Rhythm", "Unit 8 More than a Game"]),
            ]},
            {"book": "九年级全一册下段（2012课标旧版；修订版 2027 春启用）", "units": [
                ("Unit 1-7", ["Unit 1 How can we become good learners?", "Unit 2 I think that mooncakes are delicious!",
                              "Unit 3 Could you please tell me where the restrooms are?", "Unit 4 I used to be afraid of the dark.",
                              "Unit 5 What are the shirts made of?", "Unit 6 When was it invented?",
                              "Unit 7 Teenagers should be allowed to choose their own clothes."]),
                ("Unit 8-14", ["Unit 8 It must belong to Carla.", "Unit 9 I like music that I can dance to.",
                               "Unit 10 You're supposed to shake hands.", "Unit 11 Sad movies make me cry.",
                               "Unit 12 Life is full of the unexpected.", "Unit 13 We're trying to save the earth!",
                               "Unit 14 I remember meeting all of you in Grade 7."]),
            ]},
        ],
    },
    {
        "grade": "高一", "books": [
            {"book": "必修 第一册（人教版2019）", "units": [
                ("Welcome Unit + Unit 1-5", ["Welcome Unit", "Unit 1 Teenage Life", "Unit 2 Travelling Around",
                                             "Unit 3 Sports and Fitness", "Unit 4 Natural Disasters",
                                             "Unit 5 Languages Around the World"]),
            ]},
            {"book": "必修 第二册（人教版2019）", "units": [
                ("Unit 1-5", ["Unit 1 Cultural Heritage", "Unit 2 Wildlife Protection", "Unit 3 The Internet",
                              "Unit 4 History and Traditions", "Unit 5 Music"]),
            ]},
        ],
    },
    {
        "grade": "高二", "books": [
            {"book": "必修 第三册（人教版2019）", "units": [
                ("Unit 1-5", ["Unit 1 Festivals and Celebrations", "Unit 2 Morals and Virtues",
                              "Unit 3 Diverse Cultures", "Unit 4 Space Exploration", "Unit 5 The Value of Money"]),
            ]},
            {"book": "选择性必修 第一册（人教版2019）", "units": [
                ("Unit 1-5", ["Unit 1 People of Achievement", "Unit 2 Looking into the Future",
                              "Unit 3 Fascinating Parks", "Unit 4 Body Language", "Unit 5 Working the Land"]),
            ]},
        ],
    },
    {
        "grade": "高三", "books": [
            {"book": "选择性必修 第二册（人教版2019）", "units": [
                ("Unit 1-5", ["Unit 1 Science and Scientists", "Unit 2 Bridging Cultures", "Unit 3 Food and Culture",
                              "Unit 4 Journey Across a Vast Land", "Unit 5 First Aid"]),
            ]},
            {"book": "选择性必修 第三册（人教版2019）", "units": [
                ("Unit 1-5", ["Unit 1 Art", "Unit 2 Healthy Lifestyle", "Unit 3 Environmental Protection",
                              "Unit 4 Adversity and Course", "Unit 5 Poems"]),
            ]},
            {"book": "选择性必修 第四册（人教版2019）", "units": [
                ("Unit 1-5", ["Unit 1 Science Fiction", "Unit 2 Iconic Attractions", "Unit 3 Sea Exploration",
                              "Unit 4 Sharing", "Unit 5 Launching Your Career"]),
            ]},
            {"book": "高考一轮复习（教学惯例，非教材册）", "units": [
                ("高考板块", ["听力理解", "阅读理解（细节/主旨/推理/猜词）", "七选五", "完形填空",
                       "语法填空", "短文改错", "书面表达（应用文/读后续写）", "口语听说"]),
            ]},
        ],
    },
]

GRADE_NOTE = "年级按主流教学进度标注（初一：七上七下；初二：八上八下；初三：九上+九下旧版；高一：必修一二；高二：必修三+选必一；高三：选必二三四＋高考一轮复习）。初中正处修订换版期（各册版次见册名，2027 春九下换修订版），各校进度有差异，不作硬性划分。"


def tree_stats():
    """知识树统计：(单元数, 册内条目数, {年级: (单元数, 条目数)})。自检 / 口径用。"""
    tot_u = tot_l = 0
    per = {}
    for g in TEXTBOOK_TREE:
        u = l = 0
        for b in g["books"]:
            for _name, lessons in b["units"]:
                u += 1
                l += len(lessons)
        per[g["grade"]] = (u, l)
        tot_u += u
        tot_l += l
    return tot_u, tot_l, per


# ══════════════════════════════════════════════════════════════════════
# 二、识别单元（显示名, 命中词, 归属大类）—— v5.20：10 个，初高中通盘
#   英语知识点 = 「词汇任务 + 语法模块 + 题型 + 听说」颗粒度，跨学段通用；
#   具体单元由 TEXTBOOK_TREE 承载（学生说「背 Unit 3 的单词」归「单词与
#   词汇 + 课文与背诵」比一课一表更贴近教学口径）。
#   ⚠️ 词表尚未经真实英语课语料反查 —— 首个英语课程跑批后人工抽查命中，
#   按「教材直采」规则补 UNIT_EXCL 排除词。
# ══════════════════════════════════════════════════════════════════════
TOPIC_UNITS = [
    ("单词与词汇",
     ["单词", "词汇", "背单词", "默写", "听写", "拼写", "生词", "词汇量",
      "词性", "固定搭配", "固定词组", "短语", "词组", "必背单词", "单词打卡",
      "认读", "单词过关", "词汇过关", "单词表", "乱序"],
     "单词与词汇"),
    ("音标与发音",
     ["音标", "发音", "自然拼读", "拼读规则", "语音语调", "语调", "重音",
      "连读", "爆破", "朗读练习", "口语发音"],
     "音标与发音"),
    ("语法-时态与语态",
     ["时态", "一般现在时", "一般过去时", "一般将来时", "现在进行时",
      "过去进行时", "现在完成时", "过去完成时", "过去将来时", "被动语态",
      "被动", "主谓一致", "三单", "第三人称单数", "动词变形", "动词形式",
      "不规则动词", "动词过去式", "动词ing"],
     "语法-时态与语态"),
    ("语法-词法与句型",
     ["名词单复数", "可数名词", "不可数名词", "冠词", "代词", "介词",
      "连词", "形容词", "副词", "比较级", "最高级", "情态动词", "动词不定式",
      "动名词", "非谓语", "感叹句", "祈使句", "倒装", "强调句", "there be",
      "句型转换", "同义句", "同义句转换", "一般疑问句", "特殊疑问句",
      "反意疑问句", "陈述句", "疑问句", "数词", "名词所有格"],
     "语法-词法与句型"),
    ("语法-从句",
     ["从句", "宾语从句", "定语从句", "状语从句", "主语从句", "表语从句",
      "引导词", "关系代词", "关系副词", "从句语序", "时态呼应"],
     "语法-从句"),
    ("听力与听说",
     ["听力", "听说", "听口", "口语", "听说考试", "听后回答", "听后转述",
      "听后记录", "情景对话", "对话练习", "角色扮演", "配音", "模仿朗读",
      "跟读", "复述"],
     "听力与听说"),
    ("完形与语法填空",
     ["完形填空", "完型填空", "语法填空", "选词填空", "短文填空", "首字母填空",
      "首字母", "十二选十", "选词补全短文", "盲填"],
     "完形与语法填空"),
    ("阅读理解",
     ["阅读理解", "阅读表达", "阅读题", "判断正误", "细节题", "主旨题",
      "推理判断", "词义猜测", "猜词题", "七选五", "读后续写", "篇章结构"],
     "阅读理解"),
    ("书面表达与句型练习",
     ["作文", "书面表达", "写作", "范文", "话题作文", "应用文", "书信写作",
      "邮件", "英语日记", "审题", "提纲", "草稿", "翻译", "汉译英", "英译汉",
      "连词成句", "完成句子", "句子改错", "改错", "高级句型", "亮点句型"],
     "书面表达与句型练习"),
    ("课文与背诵",
     ["课文", "背课文", "背诵", "熟读", "预习", "复述课文", "课文录音",
      "课文填空", "重点句型", "背重点"],
     "课文与背诵"),
    ("粗心/马虎",
     ["粗心", "马虎", "看错", "抄错", "漏词", "拼写错误", "大小写",
      "单复数错误", "时态错误", "字母写错"],
     "粗心/马虎"),
]

# ── 单元 → 学段（口径展示用；英语能力模块大多跨学段）─────────────────────
UNIT_GRADE = {
    "单词与词汇": "初一至高三",
    "音标与发音": "初一至初三（高中并入语音语调训练）",
    "语法-时态与语态": "初一至高三",
    "语法-词法与句型": "初一至高三",
    "语法-从句": "初二至高三（初一仅接触宾语从句雏形）",
    "听力与听说": "初一至高三（北京中考/高考听说机考）",
    "完形与语法填空": "初一至高三",
    "阅读理解": "初一至高三",
    "书面表达与句型练习": "初一至高三",
    "课文与背诵": "初一至高三",
    "粗心/马虎": "全学段",
}

# ── 单元级排除词（首个英语课程语料反查后按需补充；机制同 physics_dict）──────
UNIT_EXCL = {}

# ── 后台对齐：后台英语树为空（2026-11-15 实拉 --subject 6 为 0 节点）────────
BACKEND_BACKED = ()

# 口径文案（报告口径区直接引用本句 —— 单一事实源）。
SOURCE_NOTE = (
    "英语知识点按<b>人教版英语教材目录</b>自建识别词表"
    "（初一至高三全学段：12 册 {u} 单元组 {l} 条目 → {n} 个识别单元；{per_grade}），"
    "英语的知识点以「词汇任务 + 语法模块 + 题型 + 听说」为主，跨学段通用，"
    "具体单元由教材树承载。后台英语知识树为空（实拉 --subject 6 为 0 节点），"
    "故英语<b>不采用</b>「后台为准做剔除」的口径。词表属教材直采＋教研常识，"
    "首个英语课程跑批后应人工抽查一次命中。"
).format(
    u=tree_stats()[0], l=tree_stats()[1], n=len(TOPIC_UNITS),
    per_grade="；".join(f"{g}{cu}单元组{cl}条目" for g, (cu, cl) in tree_stats()[2].items()),
)

VOCAB_SUBJECTS = ("数学", "物理", "化学", "语文", "英语")
