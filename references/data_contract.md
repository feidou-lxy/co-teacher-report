# 中间契约 data_contract（v4.8）

> **这层是干什么的**：把「取数」和「分析」隔开。分析层只读这里定义的快照，**不认识任何后台接口**。
> 接口换了（现在 7 个拼凑 → 将来研发统一封装），只重写一个适配器文件，分析层零改动。
>
> **谁写谁读**：
> `adapt_legacy.py`（旧链路）/ `adapt_api.py`（**新接口链路，v4.11 已落地**）→ 写 `<workdir>/snapshot/`
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
| `lessonCount` / `attendCount` / `absentCount` / `playbackCount` | int | ✔ | 课次 / 到课 / 缺课 / 回放 |
| `classes[]` | list | ○ | `{classSeq, classId, timeStart, isAttend, isPlayback, playbackValidTime}` |

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
    "evidence":   "判定依据行（含可回群/回后台核对的事实）"
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
    learn       = (cov.attendance == "has") or (cov.board == "has")   # 学习证据=出勤 或 小黑板

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

> ⚠️ **学习证据 = 出勤 或 小黑板**。真人实测存在「无出勤记录但有小黑板作答」的学生，若只看出勤会误判为失联。
> 此时档位仍为「出勤但无沟通」，但依据行须注明「无出勤记录，但有小黑板作答 N 张」。

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
    "studentName": "刘静怡",
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
