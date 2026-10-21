# 中间契约 data_contract（v4.19）

> **这层是干什么的**：把「取数」和「分析」隔开。分析层只读这里定义的快照，**不认识任何后台接口**。
> 接口换了（现在 7 个拼凑 → 将来研发统一封装），只重写一个适配器文件，分析层零改动。
>
> **谁写谁读**：
> `adapt_legacy.py`（旧链路）/ `adapt_api.py`（**新接口链路，v4.11 已落地**）
> / `adapt_feishu.py`（**飞书表补充证据，v4.19**）→ 写 `<workdir>/snapshot/`
> `coverage.py` / `extract.py` / `report.py` → **只读** `snapshot/`，不碰原始产物。

---

## 0. 两条铁律

1. **契约由我们定，不由接口定。** 接口字段名/嵌套怎么变，都在适配器里映射成下面的形态。适配器顶部集中放一张映射表，改字段只动那几行。
2. **缺失必须显式，绝不静默兜底。** 任何取不到的字段，写进 `snapshot/meta.json` 的 `degraded[]`，报告里明写「该项数据缺失，按现有数据判定」。**宁可不说，不可说假。**

---

## 1. 目录与文件

```
<workdir>/
  snapshot/
    meta.json          # 元信息 + 降级清单 + 计数校验
    roster.json        # 在册名单          ← 所有比例的分母
    groups.json        # 群标识
    groupActivity.json # 群活跃度聚合      ← 失联预警核心
    chats.json         # 消息明细
    attendance.json    # 出勤
    boards.json        # 小黑板读图结果（可选）
    feishuLearning.json # 飞书表补充的学习证据（可选，v4.19；见 §2.7）
    feishuMeta.json     #  ↑ 的元信息：表头指纹 + 解析自检 + 降级清单
  coverage.json        # 覆盖模型（派生，见第 3 节）
```

`meta.json`：

```json
{
  "generatedAt": "2026-10-15T14:40:00+08:00",
  "source": "legacy",              // legacy | api
  "scope": {"type": "course", "value": "2026Q-SX-高二数学清北层日E邵",
            "extra": {"co": null}},
  "window": {"startDate": "2026-09-01", "endDate": "2026-10-15"},
  "counts": {"roster": 57, "groups": 39, "groupActivity": 39,
             "chats": 39, "attendance": 57, "boards": 47},
  "degraded": [
    {"field": "roster[].roomId", "reason": "接口未提供学生↔群映射，改用群名内姓名匹配",
     "impact": "群未采到的学生无法判定是真无群还是采集遗漏 → 见 P3"}
  ]
}
```

---

## 2. 表结构

`✔` 必填 ｜ `○` 可选（缺失须进 `degraded[]`）｜ `派生` 由适配器计算，非接口字段

### 2.1 `roster[]` — 在册名单

**这是权威分母。** 覆盖率、预警比例一律以它为准，不再用「有聊天的人数」当分母。

| 字段 | 类型 | | 来源 | 说明 |
|---|---|---|---|---|
| `stuName` | str | ✔ | 花名册 `stuName` | 原始姓名，可能带数字后缀 |
| `stuNameNorm` | str | 派生 | `common.norm_name()` | 归一化姓名，**join 主键** |
| `stuSeq` | str | ✔ | `stuSeq` | 学号 |
| `productName` | str | ✔ | `productName` | 完整课程名，含 `2026Q-SX-` 前缀 |
| `productStudentId` | int | ○ | 旧接口 `productStudentId` | 课程内学生 ID。**新接口不提供**；经核查**全流程无下游消费**（出勤按 `ProductId` 拉取），仅旧链路透传 |
| `ProductId` | int | ✔ | 接口 `productId` | 课程 ID（出勤接口认它） |
| `coTeacherName` | str | ✔ | 接口 `coTeacherName` | 联席 |
| `classTeacherName` | str | ✔ | 接口 `classTeacherName` | 教务 |
| `teacherName` | str | ○ | 旧接口 `teacherName` | 主讲。**新接口不提供**（无下游消费） |
| `subjectName` | str | 派生 | `common.subject_of()` | 学科（数学/物理…） |
| `buyType` | str | ○ | 接口 `purchaseType` | 购课类型中文（**新接口直接给「直播/回放」**；旧接口给数字码 `cIncomeProductType`，适配器已反推对齐） |
| `schoolName` | str | ○ | 旧接口 `schoolName` | 校区。新接口不提供（无下游消费） |
| **`roomId`** | str\|null | ○ | **新接口 `roomId`** | 学生↔群映射。实测覆盖 **84%~86%**；为 null 者由 `coverage` 结合出勤判档 |
| `roomIdSource` | str\|null | 派生 | — | `api`（接口直给）/ `fill_groupname`（群名匹配补全，见 §6.1）/ `null`（无群） |
| `enrollStatus` | int | ○ | 派生 | 在册状态；新接口已按 `product_student_status=1` 过滤 → 名单内一律 `1` |

### 2.2 `groups[]` — 群标识

| 字段 | 类型 | | 说明 |
|---|---|---|---|
| `roomId` | str | ✔ | 群 ID，全局唯一（实测无重复） |
| `studentName` | str | ✔ | 群对应学生（接口链路由 roster 派生，旧链路来自群名） |
| `studentNameNorm` | str | 派生 | 归一化，join 主键 |
| `groupName` | str | ○ | 群名。**新接口不提供**（置空串）；本字段**不参与任何判定**，报告一律用 `productName` |
| `roomIdSource` | str | 派生 | `api` / `fill_groupname`（见 §6.1） |
| `productName` | str | ○ | 后台课程名（v4.7 起一律取后台值，废弃群名解析） |
| `coTeacherName` / `classTeacherName` / `ownerName` / `subjectName` | str | ○ | 归属字段 |

### 2.3 `groupActivity[]` — 群活跃度聚合 ← **失联预警的核心数据源**

有了这张表，预警判定不需要读全量消息。新接口 ③ 应直接提供；旧链路由 `chats` 现算。

| 字段 | 类型 | | 说明 |
|---|---|---|---|
| `roomId` | str | ✔ | |
| `msgTotal` | int | ✔ | 群内消息总数（去重后） |
| `msgTeacher` | int | ✔ | 老师（role=1）条数 |
| `msgStudent` | int | ○ | 学生本人条数（**需 `speakerRole`，新接口 ④**） |
| `msgParent` | int | ○ | 家长条数（同上） |
| `lastMsgTime` | str\|null | ✔ | 最后一条消息时间；null = 群内零消息 |
| `firstMsgTime` | str\|null | ○ | |
| `studentSubstantive` | int | 派生 | 学生/家长**实质**发言数（剔除「好的/收到/谢谢老师/纯表情」，复用 `extract.is_ack()`） |
| `parentSubstantive` | int | 派生 | 家长实质发言数 |

> ⚠️ `msgStudent` / `msgParent` 拿不到时：`studentSubstantive` 退化为「非主导发言人实质条数」，并在 `degraded[]` 标注，报告里写明。

### 2.4 `chats{}` — 消息明细（按 roomId 分组）

```json
{ "<roomId>": { "messages": [ {...} ] } }
```

| 字段 | 类型 | | 说明 |
|---|---|---|---|
| `seq` | int | ✔ | 消息序号，去重主键 |
| `ts` | str | ✔ | **ISO8601 带时区**（`2026-10-10T19:43:16+08:00`）；适配器负责把空格分隔的时间也统一成这个格式 |
| `role` | int | ✔ | 1=老师侧，2=学生/家长侧 |
| `speakerName` | str | ✔ | 发言人昵称（**家长真名无关，靠昵称区分**） |
| `speakerRole` | str | ○ | `teacher`/`student`/`parent`/`unknown`（**新接口 ④ 新增**） |
| `msgType` | str | ✔ | `text`/`voice`/`image`/… |
| `text` | str | ✔ | 消息文本（语音已转写） |

**已废弃**：旧链路只取到最后 500 条 —— **该限制已取消**，`wxworkChat/getChatList` 按 `total` 逐页拉满全量（实测单群最多 1399 条）。

### 2.5 `attendance[]` — 出勤

| 字段 | 类型 | | 说明 |
|---|---|---|---|
| `studentNameNorm` | str | ✔ | |
| `productName` | str | ✔ | 课程名（区分同名学生在不同课） |
| `lessonCount` / `attendCount` / `absentCount` / `playbackCount` | int | ✔ | 课次 / **到课（不含回放）** / 真缺课 / 看回放节数 |
| `effectiveAttendCount` / `playbackOnlyCount` | int | ✔ | **有效出勤（到课＋仅靠回放补齐）** / 未到课仅靠回放补齐的节数（v4.18 补） |
| `classes[]` | list | ○ | `{classSeq, classId, timeStart, isAttend, isPlayback, playbackValidTime}` |

> ⚠️ **`attendCount` 是纯到课数，不含回放**。判断「学生算不算上课了」必须用
> `effectiveAttendCount`（或 `common.att_effective()`）。v4.18 前 `coverage.py` 误读 `attendCount`，
> 把「没到课但看了回放」判成无出勤 → 误落「失联」。

> **实测覆盖面**：出勤覆盖率远高于群（教务视角 243 在册 → 出勤 243 全命中，群仅 144）。
> 因此**「无群但有出勤」是本轮最关键的组合**：说明学生在正常上课，问题在企微群通道，不是学生流失。

### 2.6 `boards[]` — 小黑板读图结果（可选）

| 字段 | 类型 | | 说明 |
|---|---|---|---|
| `studentNameNorm` | str | ✔ | |
| `path` | str | ✔ | 图片绝对路径（**读图缓存主键**） |
| `round` | str | ✔ | 轮次标识 |
| `lessonDate` | str | ○ | 课次日期 |
| `problem` | str | ○ | 题面 |
| `topicKnowledge` | str | ○ | 知识点 |
| `status` | str | ✔ | 作答状态（含「空白未提交」） |
| `correct` | str | ○ | 正误判定 |
| `errors[]` | list | ○ | 错误标注 |
| `stuckAt` / `note` | str | ○ | 卡点 / 备注 |

### 2.7 `feishuLearning[]` — 飞书表补充的学习证据（可选，v4.19 新增 · v4.20 扩展到 7 种形态 · v4.21 加评级序列）

> **它是什么**：各团队**自维护飞书表**里的「作业 / 打穿题 / 精题 / 出门测 / 框架笔记 / 课前测」
> 完成情况。**不替代任何现有表** —— 出勤仍以系统为准
> （用户 2026-10-16 拍板：「出勤数据只采纳系统的，飞书的出勤忽略」）。
>
> **⭐ join 主键 = `(studentNameNorm, productName)` 复合键。**
> 实测同一学生会同时上多门课（本项目 40 人 82 条记录）→
> **只按姓名 join 会把两门课的数据串在一起**。与 `coverage.json` 的
> `<productName>::<stuNameNorm>` 键保持一致。
>
> ⚠️ **课程名归一化（v4.19.1）暴露出的新问题**：把飞书表里的**简写课程名**换成后台全名后，
> 原本看不出重叠的记录会合并到同一课程下（本项目 40 人里从「0 人同课程」变成 **28 人同课程**）。
> 这不是 bug，而是归一化的**预期收益**（它让重复记录显形）；但**同课程多份记录不能直接 join**，
> 必须先定去重/合并规则，否则复合键会撞键。

| 字段 | 类型 | | 来源 / 说明 |
|---|---|---|---|
| `studentNameNorm` | str | ✔ | `common.norm_name()`（剥尾号） |
| `stuName` | str | ✔ | 原始姓名 |
| `productName` | str | ✔ | 课程名。优先级：行内「课程」列（含 `columnRoles` 覆盖）> `profile.feishu.sheetProductMap` > sheet 名；**最后统一过 `profile.feishu.courseAlias`**（简写 → 后台全名） |
| `productRaw` | str\|null | 派生 | **仅当别名命中时**才有值 = 替换前的原文（可溯源「这个全名是从哪句简写换来的」） |
| `productFrom` | str | 派生 | `column` / `profile` / `sheet-name`。⚠️ `sheet-name` 且名字不像课程名 → **不可用于 join**（必进 `degraded`） |
| `coTeacherName` / `classTeacherName` | str\|null | ○ | 联席 / 教务 |
| `sourceSheetId` / `sourceSheetTitle` / `sourceRow` | | ✔ | 溯源：出自哪张 sheet 的第几行（可回表核对） |
| `structure` | str | 派生 | `wide2`（2 行表头）/ `wide3`（3 行表头）/ **`wide1`（1 行表头·子列名与课次名同格，v4.20）** / `narrow`（1 行，格内复合值） |
| `nLessons` | int | 派生 | 有记录的课次数。**0 = 只有元数据 → 属「数据缺失」，不等于「未完成」** |
| `done` | map | 派生 | `{完成项: **完成**次数}`，只统计 `profile.feishu.items.done` 命中的项。⚠️ **v4.19.3 前语义是「有值次数」** —— 会把 `✘`（未完成）也算进去（实测框架笔记高估 36%、精题 20%、作业 9%） |
| `undone` | map | 派生 | `{完成项: **未完成**次数}`（v4.19.3 新增）。⭐ **完成率 = `done / (done + undone)`，分母不是 `nLessons`** —— 同一节课并非每一项都填，「空白」= 未记录（不等于未完成），不计入分母 |
| `lessons[]` | list | ✔ | `{seq, raw, items}`，见下 |
| `ratings{}` | map | ○ | **评级序列（v4.21）** —— 只有 `profile.feishu.ratings` 声明的项才有键。每项：`seq`（等级序列，与 `lessons` **一一对齐**）/ `absent`（同长，1 =「未提交」）/ `counts` / `nRated` / `nAbsent` / `minRated` / `mean`（A=4 B=3 C=2 D=1）/ `first`/`last` / `early`→`late`/`delta` / `trend`（`up`/`down`/`flat`/`na`）/ `tailAbsent`（末尾**连续**未交次数）。⚠️ 判趋势要求 `nRated ≥ minRated`（默认 3）—— 2 次比较是噪声；`tailAbsent` 不受样本量影响 |
| `meta{}` | map | ○ | 元数据：`note`（学情手记）/ `examScore` / `renewLevel` / `stars` / `school` / `isNew` / `extraClass`（加课，v4.27）/ `phoneComm`（电话沟通，v4.27）… |

`lessons[].items` 三种记法（同一语义，不同表的写法不同）：

| 形态 | items 长什么样 | 来源 |
|---|---|---|
| **宽表·续行表头**（`wide2`/`wide3`） | `{"课后任务/框架笔记": "未完成", "作业": "正确率高"}` | 课次名写在分组首列，子列名写在**下一行**；每课摊开成 6~10 个子列 |
| **宽表·内联表头**（`wide1`，v4.20） | `{"作业评级": "B 良好：…", "讲题视频": "√"}` | 课次名与子列名写在**同一格**：`第1次课-作业评级`；每课 7 个子列 |
| **窄表**（`narrow`） | `{"作业": true, "框架笔记": true}` | 格内 `出勤,作业,框架笔记` **拆布尔位**所得 |

> ⭐ **`wide1` 两个必须特判的点**（娜娜团队实测，不特判会**静默错判**）：
> 1. **按课次号合并分组，且判据只看课次号、不加「列号连续」**。
>    同一节课的子列各自重复一遍课次名，不合并则每列自成一组、`per_lesson=1` → **被误判成窄表**
>    （格子值 `√` 被当逗号分词拆开）。而实测「某生」表在课次区**中间**插了 23 列额外学情列
>    （精题1刷/精题2刷/精题3刷/出门测/课前测/改错），**且每节课插的位置还不一样** ——
>    要求列号连续会把一节课拆成两组。
> 2. **尾部公式辅助列必须跳过**（`columnSkipPatterns`，默认 `["数字$"]`）。
>    `第N次课数字` 同样匹配课次正则，不排除会把课次区从 138 列撑到 218 列。
>
> 插列（无 `第N次课-` 前缀的列）没有内联子列名 → **退回用该列自己的表头文字**作叶子名，
> 这样它才能被 `items` 词表命中；否则会变成 `col28` 这种无名项。

> **⭐ 宽表的值是文本，必须判完成/未完成**（`adapt_feishu.is_done_val()`）：
> - **未完成**：`✘ ✗ ✖ ✕ ❌ x X × ╳ 无 / — -` 及以 `未完成/未交/未做/未提交/没交/没做/没写/未写/缺交/漏交` **开头**的值；
> - **完成**：`⭐ 🌟 √ ✓ ✔ 好 完成 已交`，**其余自由文本**（`正确率高` / `已完成，且优秀` / `写了，并积极改错`）也算完成；
> - ⚠️ 实测未完成只有 3 种原始值（`✘` 507 / `❌️` 5 / `未完成` 1）→ **判定零误判**；
> - ⚠️ 值长度 > 15 字的多半是**手记串列**（写到了课次格里）→ 记进 `degraded`，仍按完成计但不静默。

> **为什么窄表必须拆**：窄表用逗号分隔的多个词，与宽表的子列名**是同一批词**
> （实测 5 个完全同名同义）。拆开才能与宽表算同一个「作业提交率」；
> 不拆则窄表学生进不了同一口径，8 个团队无法对同一指标。
> 阈值：**宽表子列名凡属完成项 → 计入；出勤类 → 丢弃；记录型（额外加分/当日星星/模块打分）→ 保留原值但不统计。**

⚠️ **出勤类词（`出勤` / `回放` / `请假`）一律丢弃**，不进 `items`。
⚠️ **未知子列名/词**：原样保留在 `items` 里，但**同时进 `feishuMeta.degraded[]`** ——
不静默丢弃，也不静默计入。

`feishuMeta.json` 关键字段：`sheets[]`（每张表的结构、列映射、未知项，**v4.19.1 起含
`productSource` / `productCol` / `courses[]`（该 sheet 解析出的课程清单+人数）/ `aliasedRaw[]`
—— 课程名出问题时先看这四个字段**）、
`fingerprints{}`（**表头指纹**，与上次不一致即报 `degraded`）、
`rosterJoin`（与花名册的对齐命中率）、`counts`、`joinKey`、`badCourses`（见下）、
`courseAlias` / `sheetProductMap`（回显本次生效的映射，便于核对）。

⭐ **`badCourses`（v4.21.1）** —— 课程名不对的**逐行清单**，供「待修清单」页与报告直接消费
（免得下游再解析一遍原始表）：

```json
"badCourses": { "total": 24, "byCause": {"emptyCol": 15, "badValue": 9},
                "rows": [{"cause":"emptyCol","productName":"何树威","sheetTitle":"何树威",
                          "row":112,"stuName":"靳天琪","nLessons":3, ...}] }
```

| `cause` | 含义 | 团队要做的动作 |
|---|---|---|
| `emptyCol` | 行内「课程」列**空** → 适配器按 `sheetProductMap` / **sheet 名**兜底（而 sheet 名可能是老师名） | **补填**课程名（不是改错字） |
| `badValue` | 列**有值**但不是课程全名（`#N/A` / 误粘老师名 / 简写） | **改值**（简写可先用 `courseAlias` 顶上） |

> ⚠️ **为什么必须点名到行**：报告按 `(姓名, 课程名)` 复合键取飞书块 —— 课程名对不上不是
> 「显示得不准」，而是这个学生的飞书学习记录**整条静默缺席**。
> 两种成因的**修表动作完全不同**（一个补填、一个改值），所以要分开报。
> 生成清单页：`scripts/fixlist_feishu.py --workdir <工作区> --out <路径>`。

#### 团队要填的声明表（`profile.feishu` 下）

飞书表「给人看」，所以有几类信息机器猜不出来，必须在 profile 里声明：

| 声明表 | 什么时候需要 | 长什么样 |
|---|---|---|
| `sheetProductMap` | sheet 名不是课程全名、表内又**没有**「课程」列 | `{"mslXT3": "2026Q-SX-高二数学211层六C肖"}`（key 是 **sheetId**，不是 sheet 名 —— sheet 改名不会失效） |
| `courseAlias` | 表里写的是**简写**课程名，后台是**全名** | `{"进度二211层日E王": "2026Q-SX-高二数学进度二211层日E王"}`（key = 表内原文） |
| `columnRoles` | 某列**表头为空**、或按段合并单元格（文字匹配必然落空） | `{"7nJWyD": {"productName": {"col": "A", "fillDown": true}}}`（`col` 可写 `"A"` 或 `1`） |
| `metaAlias`（v4.20） | 团队对元数据列有自己的叫法 | `{"老师": "coTeacherName", "学生情况备注": "note", "加课时间，内容": "extraClass", "电话沟通情况": "phoneComm"}` |
| `columnSkipPatterns`（v4.20） | 表尾有公式辅助列 | `["数字$"]`（默认值；置 `[]` 关闭） |
| `longValLen`（v4.20） | 课次格的**正常值**偏长（如评级描述） | `40`（默认 15） |
| `ratings`（v4.21） | 有 **ABCD 等级列**（如「作业评级」），想在报告里看**逐课趋势** | `{"作业评级": {"levels": ["A","B","C","D"], "labels": {"A":"优秀"}, "minRated": 3}}`。不声明 = 不启用（`⭐`/`✘` 这类无等级的团队天然不受影响）；`levels` 是抽取白名单（防 `True`→`T` 误判） |

> ⭐ **v4.20：元数据列只能按表头文字匹配，不能按列号。**
> 实测同一团队内表间列序都不一致 —— 娜娜团队「某生」表把「学校/城区」放在了**最末尾**。
> 所以 `columnRoles`（按列号）只用于「表头为空」的特殊列，常规列一律走文字匹配。

> ⚠️ **`metaAlias` 优先级高于内置默认表，且必须用它来解决短别名误吃**：
> 内置表里 `stuName` 有短别名「学生」，而娜娜团队的「学生情况备注」会 `startswith("学生")`
> → **被错映射成学生姓名**。只有显式声明 `"学生情况备注": "note"` 才能纠正。

> ⭐ **`fillDown` 是给「合并单元格」用的**：合并单元格在接口里只有首行有值、其余为空，
> 必须向下继承上一个非空值。实测某生表用 A 列按课程分段（3 段 = 3 门课，
> 与 `courseAlias` 的 3 条一一对应）——**这正是「表头为空 + 合并单元格」的典型场景**。
>
> ⚠️ **判空必须用 `is None` 而不是 falsy**：列 A 的索引是 `0`，而 `not 0` 为真，
> 会把 A 列误判成「没有课程列」→ 该表全体课程名落空。
>
> ⚠️ `columnRoles` 若与表头文字匹配冲突，**人工声明优先**，并进 `degraded` 提示。

> ⭐ **多列可以映射到同一字段**（v4.20）：娜娜团队把教务沟通拆成
> 「首课沟通 / 中段回访 / 评价回访」三列，全部指向 `note`，由适配器**合并成一条**
> （`" / "` 连接）—— 保证「不静默丢弃任何一列」。落地：第一列进 `metaMap`，其余进 `metaDup`。

#### 复合键撞键的取舍：`dedupe`（v4.19.2）

```
"dedupe": { "mode": "pick", "order": ["QAVvKs", "7nJWyD"], "preferNonEmpty": true }
```

- ⭐ **`mode`（v4.20）**—— 撞键怎么处理，**两种语义差别很大**：
  | 模式 | 行为 | 什么时候用 |
  |---|---|---|
  | `pick`（默认） | 按 `order` **取优先级最高的一份**，其余进 `dropped[]` | 两表在**重复维护同一批数据**（取谁都一样，选记录更全的那份） |
  | `merge` | **合并**成一条：按课次号取「子项更多」的那份作**基底**，**再补上其余份独有的项**（键并集，冲突以基底为准）；元数据逐字段取第一个非空值 | 两个维度**互为补充**（娜娜：教务表有课次但没备注、联席表反之） |
- `order` = sheet 优先级（从高到低）；**未列出**的排在后面（按各 sheet 解析顺序，稳定排序）。
  ⚠️ `merge` 模式下 `order` 只作**开关**（顺序不影响合并结果）。
- ⚠️ **归一化会让隐藏的重复显形** —— 课程名换成全名后，原本「看起来是两门课」的记录现出真身是同一门课
  （本项目重名 40 人里，同课程重复从 **0 变成 28**）。所以**归一之后必须重查撞键**。
- 被覆盖的记录**不静默丢弃**：全量记进 `feishuMeta.dedupe.dropped[]`（含 `sourceSheetId` / `sourceRow` /
  `nLessons` / `keptFrom`，可回表核对）。`keptFrom` 与自己同 sheet = **表内重复**（数据错误）→ 单独报。
- ⭐ `preferNonEmpty`（默认 true）：优先级最高那份若**零课次**（只有元数据、等于零学习证据），
  改取有课次的那份 —— 否则会把有数据的学生「去重成空」（本项目命中 1 人）。
  命中即记进 `dedupe.anomalies[]` + `degraded`；要严格按 `order` 就设为 `false`。
- ⚠️ **`merge` 的「取更全」≠「取全部」（v4.21.1 修）**：原实现按课次号**只取子项更多的那份** →
  丢掉另一份**独有的项**。实测「周文峰」表行 75 王子轩：该课次只有 1 项（`几综模块测=通关！`），
  同键「教务-邓琨薇」表行 24 该课次有 7 项（**但全是 `#N/A` 无数据**）→ 7 > 1 ⇒ 取了教务那份，
  真实的「通关」被 7 个空值顶掉。现为**键并集**；娜娜团队本次共**补回 52 项**。
  补回数见 `dedupe.filledItems`（总）与 `dedupe.merged[].filledItems`（逐组）。
- 实测：孙一文（`pick`）867 条 → 主表 **837**（撞键 30 组、弃 30 条、例外 1 条、**残留撞键 0**）；
  跨课程 12 人（一人多课）**完整保留**，未被误去重。
  娜娜（`merge`）2117 条 → **1117**（撞键 992 组、合并 994 条、占位行 6、丢弃 0，
  守恒：`2117 = 1117 + 994 + 6`）。

#### 报告怎么消费（`report.py`）

`snapshot/feishuLearning.json` 存在时，Tab③ 学生卡的 ① 学习情况 内会多一个
**📗 飞书表学习记录**折叠块（绿色系）：完成项 chips（`完成/总数` + 百分比，<70% 标红）+
学情手记 + 沟通优先级 + 考试/续报等元数据 + 溯源行。

> ⚠️ **v4.20：报告侧不得硬编码完成项名。** 完成项按**数据里实际出现的键**渲染
> （`ORDER_PREF` 只定展示顺序，未列到的项排后面）。
> 硬编码会让换词表的团队**整块不渲染** —— 娜娜团队的「作业评级/作业闭环/讲题视频/总结/打穿题测试」
> 一个都不在孙一文的词表里，写死就直接空白。
>
> ⚠️ **匹配必须用 `(姓名归一, 课程名)` 复合键，且课程名要试两种形态**：
> 报告侧 `r["course_raw"]` 是 `common.course_name()` 的产物 —— 它**剥掉了 `2026Q-SX-` 产品前缀**；
> 而飞书侧存的是**带前缀全名**。只按一种形态建键会**全部落空**（实测命中率 40/70 → **0/70**）。
> 落地做法：建索引时**两种键都登记**（全名 + 去前缀名），取值时先试 productName 再试 course_raw。
>
> 手记/考试等元数据是**团队手填的自由文本** → 一律 `html.escape` 后再拼（防 `<>` 破坏页面结构）；
> 该表**缺失时不渲染该块**（向后兼容，实测无飞书数据的目录仍正常出报告）。



---


## 3. 覆盖模型 `coverage.json` ← **防误报的核心**

三层四态。每个在册学生一条：

```json
{ "<productName>::<stuNameNorm>": {
    "productName": "2026Q-SX-高二数学清北层日E邵",
    "stuName": "张三",
    "group":      "found | missing | unknown",
    "chat":       "has | empty | n/a",
    "attendance": "has | empty | n/a",
    "board":      "has | none | n/a",
    "tier":       "失联 | 出勤但无沟通 | 单向沟通 | 低互动 | 正常",
    "cause":      "group_missing | group_empty | single_way | ok",
    "evidence":   "判定依据行（含可回群/回后台核对的事实）",
    "nAtt":       5,      // ⚠️ v4.18 起 = 有效出勤（含看回放），非纯到课数
    "nLes":       5,      // 总节数
    "nPbOnly":    3,      // 仅靠回放补齐的节数（v4.18 新增）
    "nPb":        3       // 看回放总节数（v4.18 新增）
} }
```

- `found`/`has` = 有；`missing`/`empty`/`none` = **确定没有**；`n/a` = **取不到，无法判定**
- `group=missing` → `chat` 必为 `n/a`（群都没有，谈不上群内消息）
- `tier` / `cause` 的判定规则见第 4 节

### 判定规则（伪代码）

```
for stu in roster:
    ga = activity_by_room.get(stu.roomId) 或 按姓名匹配到的群
    if ga is None:
        group, chat = "missing", "n/a"
    else:
        group = "found"
        chat  = "empty" if ga.msgTotal == 0 else "has"

    attendance = "has" if att[stu].attendCount > 0 else "empty"   # 取不到 = n/a
    board      = "has" if 有读图记录且非「空白未提交」 else ("none" if 有读图数据源 else "n/a")
```

### ⚠️ 口径红线

> **判「人还在不在」看出勤，不看群。**
> 实测教务视角：243 在册 → 群仅 144（缺 99，分散在全部 7 门课），而 **99 人里 93 人有实际上课记录**。
> 群列表接口天然有损（只收录有响应时效记录的群），**拿群当「学生是否失联」的判据会一次性误报 93 人**。
> 所以：**群采到与否只作「根因」记录，不作分档依据**；分档一律用「出勤 × 聊天」两轴（见第 4 节）。

---

## 4. 预警分档（由 `coverage` 派生）

**两轴**：学习轴（出勤／小黑板，判「人还在不在」）× 沟通轴（群聊，判「通道通没通」）。
群「采到与否」不进档位，只作 `cause` 根因记录。

| 档 | 名称 | 判定 | 主要动作 |
|---|---|---|---|
| **1** | **失联** | 无聊天记录 **且** 无出勤记录 | 最高优先级；人工核实是否退课 / 从未建群 |
| **2** | **出勤但无沟通** | 无聊天记录 **且** 有出勤记录 | 学生在正常上课，**企微沟通通道未打通** → 补建 / 核对群 |

> ⚠️ **两档中的「出勤记录」= 有效出勤（含看回放）**：未到课但已看回放，同样算有出勤。
> 口径出口 `common.att_effective()`；**不要用 `attendCount`（纯到课数）**——v4.18 前 `coverage.py`
> 误读它，把「看了回放」判成无出勤而误落「失联」（实测联席 5 人、教务 6 人档位写错）。
> 出勤文案须写明「其中 N 次为看回放补齐」；对「一次直播都没到、全靠回放」的人，
> 预警卡措辞为「**未到直播、但已用回放把课补齐**」，**不写「正常上课」**。
| **3** | **单向沟通** | 有聊天记录，但学生·家长实质发言 = 0（仅老师输出） | 检查是否只在发通知，未真正触达学生 |
| **4** | **低互动** | 有聊天记录，学生·家长实质发言 1~2 | 关注 |
| — | 正常 | 其余 | — |

### 根因子标签 `cause`（与档位正交，只用于解释）

| `cause` | 触发 | 展示文案 |
|---|---|---|
| `group_missing` | `group=missing` | 企微群未采集到 |
| `group_empty` | `group=found` 且 `chat=empty` | 群已建但零消息 |
| `single_way` | 群有消息但学生·家长零实质发言 | 仅老师单向输出 |
| `ok` | 其它 | — |

### 判定伪代码

```
for stu in roster:
    cov = coverage[stu]
    chat_recent = 近两周窗口内该群的消息与实质发言
    # 学习证据 = 出勤 或 小黑板；其中「出勤」= 有效出勤（含看回放），见 common.att_effective
    learn       = (cov.attendance == "has") or (cov.board == "has")

    if chat_recent.msgTotal == 0:              # 无聊天记录（近两周）
        cause = "group_missing" if cov.group == "missing" else "group_empty"
        tier  = "出勤但无沟通" if learn else "失联"
    elif chat_recent.studentSubstantive == 0:  # 有聊天但学生家长零实质发言
        tier, cause = "单向沟通", "single_way"
    elif chat_recent.studentSubstantive <= 2:
        tier, cause = "低互动", "ok"
    else:
        tier, cause = "正常", "ok"

assert #coverage(失联+出勤但无沟通+单向沟通+低互动+正常) == #roster   # 每人有且只有一档
```

### 窗口（两轴不同，务必分开）

| 轴 | 窗口 | 理由 |
|---|---|---|
| 沟通轴（聊天 / 实质发言） | **近两周（14 天滑动）** | 全月窗口会把「近期才沉默」掩盖掉 |
| 学习轴（出勤 / 小黑板） | **报告窗口全窗口** | 出勤本稀疏，只看近两周会把已结课的学生全判成「无出勤」→ 误报失联 |

> ⚠️ **学习证据 = 出勤（含看回放） 或 小黑板**。真人实测存在两类会被误判失联的学生：
> ① 「无出勤记录但有小黑板作答」；② 「一次直播都没到、但把回放看完了」。
> 前者依据行须注明「无出勤记录，但有小黑板作答 N 张」；后者出勤文案须写明「其中 N 次为看回放补齐」。

**双轴防伪**：只要学习轴有证据（档 2），**一律不得写「学生流失」**，只能写「企微沟通通道失效」。
档 1（失联）才是唯一按真失联处理的候选。

---

## 5. 视角参数 `scope`

| `scope.type` | `value` | 过滤逻辑 |
|---|---|---|
| `course` | 课程名（支持前缀）：`2026Q-SX-高二数学清北层日E邵` | `productName` 包含 value；`extra.co` 可再按联席子筛 |
| `co` | 联席姓名：`张巍` | 花名册 `coTeacherName == value` |
| `edu` | 教务姓名：`罗绂燕` | 花名册 `classTeacherName == value` |

> 课程名一律取后台 `productName`，**不解析群名**（约 1 成不一致，会静默漏人）。
> **本轮落地范围：`course` + `co`**；`edu` 逻辑同构，待本轮验证后再开。

---

## 6. 接口映射（v4.11 实况）

> 研发已按本契约封装前两步。**实际只做到「第 1 点」**：拿在读学生 + 购课类型 + 联席 / 教务 + roomId。
> 第 2、3 点（聊天、出勤）**仍走旧接口**，第 4 点（小黑板）仍是本地素材 —— 与分析层无关。

| 契约字段 | 旧链路（`adapt_legacy.py`） | 新链路（`adapt_api.py`） |
|---|---|---|
| `roster[]` | `cStudentFile/getCStudentFileList`（全机构拉取后按角色筛） | ✅ **`GET /api/ai/getCourseStudents?productName=`**<br>✅ **`GET /api/ai/getCoTeacherStudents?coTeacherName=`** |
| `roster[].roomId` | ❌ 无 → 群名内姓名匹配（**有损**） | ✅ 接口直给（`purchaseType` 同源） |
| `groups[]` | `wxworkSessionStat/getList`（**有损**：只收录有响应时效记录的群） | 由 `roster` 的 `roomId` **派生**（不再依赖群列表） |
| `groupActivity[]` | 由 `chats.json` 现算 | 同旧（用 roomId 拉全量消息后现算） |
| `chats{}` | `wxworkChat/getChatList`（分页拉满全量） | **同旧**（按 `groups[].roomId` 逐群拉） |
| `attendance[]` | `cClassAttend/getCClassAttendList`（按 `ProductId`） | **同旧** |
| `boards[]` | 本地读图（`board_cache.py` 增量缓存） | 同旧（本地素材） |
| **教务维度** | `cStudentFile` + `classTeacherName` 筛 | ⚠️ **暂无接口**（研发只做了课程 / 联席两个维度） |

### 6.1 ⚠️ 新接口的一处**关联缺陷**（实测，已兜底）

接口的 `roomId` 取自「学生课程关系 → 服务群」，**并非全覆盖，且会漏掉真实存在的群**。

实测课程 `2026Q-SX-高二数学清北层日E邵`（56 人）：

| 项 | 值 |
|---|---|
| 接口直给 roomId | 47 / 56 = 84% |
| 旧链路群名匹配 | 37 / 58 = 64% |
| **两者都有 roomId 的一致性** | **54 一致 / 0 冲突**（同 ID 体系，可信） |
| 接口漏掉的真实群 | **黄五琦**（群名「黄五琦（W）-高二数学清北层**E邵**-26」，**缺「日」字**，108 条消息、后台课程名与本人一致） |

> **成因**：群名与后台课程名不一致（缺字 / 写了别的层级）时，服务群关联不上 → `roomId` 返回空串。
> **影响**：漏 1 个群 = 漏 1 个学生的全部沟通证据 → 会被误判成「出勤但无沟通」甚至「失联」。
> （注意方向：这里的误判是**把有沟通的人当成没沟通**，与「看回放被当成没出勤」是两回事，两者都已修。）
> **兜底**：`pull_roster.py --fill` 用群列表的「群名内姓名」补一次，**只补接口返回空值的学生**，
> 补全项标 `roomIdSource: "fill_groupname"` 并进 `meta.degraded[]`，来源可追溯。
> **建议**：向研发反馈该关联规则；短期建议始终带 `--fill` 跑。

### 6.2 接口返回格式（实测）

```json
{"code": 0, "msg": "获取成功",
 "data": {"total": 56, "list": [
   {"productId": 5552,
    "productName": "2026Q-SX-高二数学清北层日E邵",
    "studentSeq": "2025021900004",
    "studentName": "刘同学",
    "purchaseType": "直播",
    "coTeacherName": "张巍",
    "classTeacherName": "罗绂燕",
    "roomId": "wrdyXFEQAAtXuveEx5zOOBjHW_C-mUtA"}]}}
```

- 鉴权：请求头 `X-Token`（**仅校验登录态，不校验 Casbin 权限**）。⚠️ 与旧接口的 `x-token` 大小写不同，`common.api()` 统一用小写、HTTP 头不区分大小写，实测两者都通。
- **全量返回、不分页**；`code=0` 才是成功（HTTP 200 不代表业务成功）。
- 筛选项（接口侧已完成，适配器**不再重复过滤**）：课程未删除、`is_classin_callback=2`、
  在读 `product_student_status=1`、购课类型 `product_type=1/2`（不含试听）。
- 相比旧花名册：课程视角 58 → **56 人**（剔除 2 名已不在读）；联席视角 81 → **81 人**，
  仅 1 人轮换（旧有张奕鑫[已退课]，新有曾子瑜）→ **在读口径更准**。

---

## 7. 适配器自检（每个适配器必须跑）

1. `roster` 非空，且人数与 scope 应得一致
2. `roomId` 全局唯一；`chats` 的每个 key ∈ `groups[].roomId`
3. `attendance` 覆盖率 < 80% 时报 warn（本轮实测 100%，低于即异常）
4. 所有 `ts` 统一 ISO8601 带时区（⚠️ 后台用 `T` 分隔、导出数据用空格，`extract.parse_dt()` 两种都要认）
5. `counts` 与各表实际长度一致；`degraded[]` 里每项都有 `reason`
6. **守恒校验**：`#roster == #coverage`；`#coverage(失联 + 出勤但无沟通 + 单向沟通 + 低互动 + 正常) == #roster`（每人都必须有且只有一个档）
7. **飞书适配器（v4.19，跑 `adapt_feishu.py` 时必查）**：
   - **行数守恒**：每张 sheet 的「可解析行数 == 解析出的人数」（本步实测 8/8 全等）；
   - **指纹**：与上次 `feishuMeta.fingerprints` 不一致 → 必须进 `degraded`（防团队插列导致静默错位）；
   - **课程名**：`productFrom == "sheet-name"` 且名字不含 `20xxQ-` → 必须进 `degraded`（否则该表全体对不上花名册）；
   - **课程名归一（v4.19.1）**：`feishuMeta.sheets[].courses[]` 逐表核对——
     ① 课程数是否与团队口径一致（实测某生表 A 列恰 3 段，与 `courseAlias` 3 条**必须一一对应**）；
     ② `productFrom == "column"` 但值不是 `20xxQ-` 全名 → 八成是简写，需加进 `courseAlias`；
     ③ `aliasedRaw[]` 与 `courses[]` 能对得上（每个原值都能说出换成了哪个全名）；
   - ⚠️ **归一后必须重查复合键撞键**：`(studentNameNorm, productName)` 出现重复 → 说明同一门课有多份记录，
     先定去重规则再入报告（**归一化会让原本隐藏的重复显形，这是好事，但要显式处理**）；
   - **去重可复核（v4.19.2）**：主表人数 + `dedupe.droppedCount` == 各 sheet 可解析行合计；
     丢弃项**课次数不得高于**保留项（除非命中 `dedupe.anomalies` 例外）；
   - **完成率可复核（v4.19.3）**：`done` 只含**完成**次数 → 抽查被判为「未完成」的原始值应全为
     `✘ / ❌️ / 未完成` 一类（出现 `正确率*` 之类即判定写错）；
   - ⭐ **`merge` 模式守恒（v4.21.1）**：`各 sheet 可解析行合计 == 主表人数 + mergedAway + placeholder + dropped`
     （娜娜实测 `2117 == 1117 + 994 + 6 + 0`）；且 `merged[].filledItems` 之和 == `dedupe.filledItems`；
   - ⭐ **课程名清单能回表（v4.21.1）**：`badCourses.total == sum(byCause.values()) == len(rows)`，
     且每行都带 `sheetTitle + row`（可直接定位到飞书行号）；跑 `scripts/fixlist_feishu.py`
     产出的清单应与它**逐行一致**（该脚本自带交叉校验，会打印 `一致=True/False`）；
   - **无兜底键**：`items` 的键不得出现 `colXX` 形式的占位（出现即说明表头识别失败）。
