# 学生管理后台 API 契约（stadminv2.xqmsx.cn）

本文件记录「联席学情报告」技能用到的学生管理后台接口。所有接口域名前缀为
`https://stadminv2.xqmsx.cn/api`。

- **推荐接口**（研发封装，v4.11）：`GET /ai/getCourseStudents` / `GET /ai/getCoTeacherStudents`
  —— 见 §2，鉴权头 `X-Token`。
- **逆向接口**（旧链路仍用）：鉴权头 `x-token`（来自登录后 Cookie 的 token 字段）。
  两个头名大小写不同但 HTTP 头不区分大小写，实测都能通。

> ⚠️ 安全提示：早期探测发现 `wxworkSessionStat/getList` 与 `wxworkChat/getChatList` 在未带
> token 时仍可访问（越权风险）。脚本统一带 token 调用；如发现该风险，应反馈给平台方修复。

---

## 1. 登录与鉴权

- 登录页：`https://stadminv2.xqmsx.cn/#/login`（账户密码 + 图形验证码）。
- **取 token 的正路（v4.17）**：用户提供账号密码 → 浏览器自动化打开登录页并代填账号密码 →
  **把图形验证码截图请用户辨认** → 用户回复 4 位数字 → 代填并提交 → 读 Cookie 里的 `token`。
  ⛔ **不要**去调 `/login` 之类的接口用账号密码自己登（等于让机器过图形验证码，不稳定也不该做）；
  **账号密码不落任何文件**，用完即弃。浏览器工具不可用时退「用户自己登录后复制 `token`」。
- 登录成功后，浏览器 Cookie 中 `token` 字段即 JWT；也可从 `localStorage` 读取。
- token 有效期约 24 小时（JWT `exp`），过期需重新登录。
- 取得后写入 `<workdir>/token.txt`（纯文本，一行），脚本自动读取。

---

## 2. 在读学生（研发封装 · v4.11 首选）★

> **这是唯一由研发按需求封装的接口**，课程 / 联席两个维度已上线，**优先用它取花名册**。
> 它一次返回「在读学生 + 购课类型 + 联席 + 教务 + **roomId**」，把旧链路最缺的
> 「学生 ↔ 群映射」补齐了（旧链路只能用群名反查，且群列表接口本身有损）。
> 旧接口（§3 `cStudentFile`）保留：**教务维度**与兜底仍需它。

```
GET /ai/getCourseStudents?productName=<课程名>          课程维度
GET /ai/getCoTeacherStudents?coTeacherName=<联席姓名>     联席维度
```

- 鉴权：请求头 `X-Token: <登录Token>`（**仅校验登录态，不校验 Casbin API 权限**）。
- 参数**精确匹配**：课程须带前缀全串（`2026Q-SX-高二数学清北层日E邵`），联席须与后台姓名完全一致。
- 中文参数须 URL 编码（交给请求库的 `params` 处理）。
- **筛选规则（接口侧已完成，调用方不要重复过滤）**：
  - 课程：未删除 且 `is_classin_callback=2`；**不限制** `product_status`；同名课程全部返回（按 `productId` 区分）。
  - 联席：课程满足 `product_status=1`（**进行中的课程**）、`is_classin_callback=2`、未删除；
    按学生课程关系中的联席匹配姓名；同名联席数据全部返回。
  - 共同：仅未删除、**在读 `product_student_status=1`** 的学生课程关系；购课类型限 `product_type=1/2`
    （**直播 / 回放，不含试听**）；同一学生报读多门课会返回多条。
- **全量返回、不分页**，按课程 ID + 学生课程关系 ID 升序；无数据时 `list=[]`、`total=0`。
- `roomId` 选择规则：由学生课程关系关联服务群，仅取**已匹配且未解散**的群
  （`biz_status=1`、`status=0`）；多个群取 `last_message_time` 最新，时间同则按群记录 ID 倒序；
  均无时间也按 ID 倒序；**无匹配群返回空字符串**。

返回格式：

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

- `code=0` 成功、`code=7` 失败（原因见 `msg`）。**HTTP 200 不代表业务成功，必须同时判 `code`**。
- `purchaseType`：中文 `直播` / `回放`。⚠️ 与旧花名册的数字码 `cIncomeProductType` 不同，
  适配器已反推对齐（直播=1、回放=2），下游无需改动。
- `coTeacherName` / `classTeacherName`：无有效关联老师时为空串。
- `roomId`：无群时为空串。**该字段并非全覆盖，且实测会漏掉真实存在的群** —— 详见 `data_contract.md` §6.1。

实测（2026-10-15）：

| 维度 | 输入 | 返回 | roomId 非空 |
|---|---|---|---|
| 课程 | `2026Q-SX-高二数学清北层日E邵` | 56 人（1 门课） | 47（84%） |
| 联席 | `张巍` | 81 人（4 门课） | 68（84%） |

⚠️ **教务维度（按 `classTeacherName`）尚无对应接口**，暂时只能走 §3 的旧花名册接口
（全机构拉取后按 `classTeacherName` 过滤）。建议向研发补
`GET /ai/getClassTeacherStudents?classTeacherName=<教务姓名>`（照抄联席维度即可）。

---

## 3. 学生花名册（旧接口 · 教务维度仍用）

`POST /cStudentFile/getCStudentFileList`

请求体：
```json
{ "page": 1, "pageSize": 500, "productStatus": [0, 1], "attendStatus": 1 }
```
返回 `data.list[]` 字段（技能用到）：
- `stuName` 学生姓名
- `stuSeq` 学生序号
- `productName` 课程产品名（如 `2026Q-SX-高二数学985层进化班六D谢胡`）
- `productStudentId` 产品-学生 ID（详情接口用）
- `ProductId` 课程产品 ID（出勤接口用）
- `coTeacherName` **联席姓名**（联席视角按此字段过滤得到某联席的学生）
- `classTeacherName` **教务姓名**（教务视角按此字段过滤，得到该教务管辖的全部学生，跨多个下属联席；响应时效页的「教务」列即此字段）
- `teacherName` 主讲
- `cIncomeProductType` **购课类型**（1=直播 2=回放 3=试听（插班）4=测试）——后台「我的学生」列表的「购课类型」列
- `proBroadcastType` 课程播放类型（1=直播课 2=录播课）——对应「课程类型」列

> 花名册与「我的学生」列表同源（一对一，一个学生一条记录）。购课类型/课程类型的**中文映射**见第 8 节字典接口。

---

## 4. 群聊列表（含响应时效）

`GET /wxworkSessionStat/getList`

参数：`year=4&term=3&timeDimension=month|day&statDate=YYYY-MM-DD&page=&pageSize=`

- **月维度**：`timeDimension=month&statDate=<当月第一天>`（如 `2026-09-01`）。
  注意：**当前月后台尚未聚合，月维度返回 0**，必须用日维度补（见下）。过去月已聚合、覆盖完整。
- **日维度**：`timeDimension=day&statDate=<当天>`。
- 实践（v4.7 修正）：月维度（窗口起始月）＋**当前月逐日（1 号 ~ 今天）** 并集，按 `roomId` 去重。
  仅补「当天」会漏掉当月 1~(今天-1) 号有活动、今天无活动的群（实测一门课因此少 8 个群）。
  ⚠️ **该接口是有损清单，不是课程全部群**：只收录「有过会话/响应时效记录」的群。
  某课程在册 58 人，但后台此接口跨全年各月 + 当月逐日去重后仅 39 个 roomId（21 名在册学员的群从未产生响应时效记录，故查不到 roomId）；
  花名册 `cStudentFile/getCStudentFileList` 才是**在册学员权威来源**（含 `ProductId`，但**无 roomId**）。
  若需枚举课程「全部在册学员」，应以花名册为准、再用本接口 backfill roomId（无 roomId 的群无聊天可分析）。

返回 `data.list[]` 字段（技能用到）：
- `roomId` 群 ID（拉聊天用）
- `studentName` 学生姓名（与花名册 join）
- **`productName` 课程名（v4.7 新增 · 报告取课程名的唯一来源）**——
  如 `2026Q-SX-高二数学985层进化班六D谢胡`（`YYYYQ-学科码-` 前缀 + 课程名；
  学科码：SX 数学 / WL 物理 / HX 化学 / YW 语文 / YY 英语）。实测 3781/3781 条非空。
- `groupName` 群名（如 `张喜然(W)-高二数学985层六D谢胡-26Q`）：**v4.7 起不再用于解析课程**，
  仅在按课程筛选时作兜底 + 不一致诊断（见下）
- `grade` / `gradeName` 年级（`5` → `高二`）
- `subject` / `subjectName` 学科（如 `数学` / `物理` / `英语`）
- `mainTeacherName` 主讲（如 `谢全民,胡鹏`）
- `coTeacherName` 该群归属**联席**（群自带）
- `classTeacherName` 该群归属**教务**（群自带）
- `workAvgResponseTime` / `restAvgResponseTime` 工作时段/休息时段平均响应（后台自算，如 `147小时50分`）
- `lastAskTime` 最近提问时间；`isMetStandard` 是否达标
- `isTimeout` / `timeoutDuration`（后台响应时效标记）：**已弃用（v4）**——实测与聊天事实不符
  （学生 6 分钟即获回复的群被标「超时 19 小时 59 分」），技能不再拉取
- `isReplied`（是否回复）：**已弃用（v3.2）**——该列实测存在误差，技能不再拉取

**⚠️ 课程名口径（v4.7）：`productName` 为准，`groupName` 会与它不一致**

实测两者确有对不上的情况（3781 群抽样，约 1 成）：

| 群名 groupName | 后台课程名 productName | 说明 |
|---|---|---|
| `高二数学985层六D谢胡` | `高二数学985层进化班六D谢胡` | 群名漏了班型「进化班」 |
| `高二数学985层六D谢胡` | `高二数学清北层进化班日D谢胡` | **群名写成了另一个层级**，共 5 例 |
| `高二数学清北层E邵` | `高二数学清北层日E邵` | 群名缺字（旧代码靠 `course_of` 特判修补） |
| `高二数学进度一985层六D肖` | `高二数学985层进化班六D谢胡` | 群名与课程完全不同 |

故 `pull_groups.py --course <课程名>` **只按 `productName` 匹配**；仅当课程名一个都没命中时
才退回群名匹配并打印风险提示。凡「群名像、课程名不是」的群会被**排除并列出**，便于回后台核对。
> 「是否需回复」与「响应时效」现均由**聊天记录自行分析**：`unresolved`（会话尾部仍有未获回应提问）、
> `resp_slow` / `resp_max_h`（学生提问 → 老师首次回复的最长等待，≥24h 且未说明缘由才判迟缓）。
> 实现见 `extract.py` 的 `resp_wait_stats()`；`pull_groups.py` 已不再映射上述两个后台字段。

**取群口径（v3.4，必须双条件）**：`pull_groups.py` 对群列表做双重过滤，缺一不可——
1. 群内 `studentName` ∈ 该角色花名册（这名学生确实是本角色的学生）；
2. 群的归属字段 == 本角色：**联席**看 `coTeacherName`、**教务**看 `classTeacherName`。

> 为何必须加条件 2：一名学生可能同期报读多科，这些科的群由**别的联席/教务**负责。
> 若只按学生名交集（旧口径），会把别的角色的群也拉进来——例如「张巍（只带数学）」
> 曾被混入其学生的物理/化学/英语群（82 群 → 正确应为 57 群，全部数学）。
> 归属字段为空时回退保留，避免因后台缺字段漏群。

**课程维度（`--course`，v4.7）**：按 `productName` 包含课程串收群（不再按群名子串），
可另传 `--co <联席名>` 做课程内子筛选。

---

## 5. 群聊消息

`GET /wxworkChat/getChatList?roomId=<room>&page=N&pageSize=500`

- 倒序返回（`page=1` 为最新）；返回体含 `data.total`（该群消息总条数）。
- ⚠️ **单群条数上限已取消**（v3.5 起）：按页累加直到拉满 `total` 即可取全量。
  实测单群最多 **1399 条**（近 3 页）也能完整拉取；6 个群超过 500 条均正常取全。
  旧写法「只取 `page=1` + `pageSize=500`」会把活跃群较早历史静默截断，**不要退回该写法**（实现见 `pull_chats.py:get_chat()`）。
- `data.list[]` 每条消息：`msgTime, role(1=老师/2=学生家长), msgType(text/voice), name, content`
  - 文本：`content` 为字符串，或 `{ "content": "..." }`
  - 语音：`content` 为 `{ "content": { "content": "转写文本" } }`

---

## 6. 逐节课出勤（后台权威）

`GET /cClassAttend/getCClassAttendList?page=&pageSize=500&productId=<X>`

- 按课程产品 `productId` 分页拉取；或全量拉取后按 `coTeacherName` 过滤。
- 返回 `data.list[]` 字段（技能用到）：
  - `studentName` / `studentSeq`
  - `classSeq` 第几节课
  - `classId` 课节 ID（去重键）
  - `timeStart` 上课时间
  - `isAttend` 1/0（到课 / 未到课）
  - `isLate` / `isGone` 迟到 / 早退
  - `isPlayback` 是否看回放
  - `playbackTotalValidTime` 回放有效时长

> ⚠️ **出勤口径**：`isAttend=0` 但 `isPlayback=1`（**没到课、看了回放**）**同样算有效出勤**。
> `build_attendance.py` 据此汇总出 `effectiveAttendCount`（有效出勤）与 `playbackOnlyCount`（仅回放补齐），
> 这是全流程的权威口径；`attendCount` 只是**纯到课数**，不可直接当出勤用（v4.18 修正）。
  - `cProduct.name` 课程产品名
  - `coTeacherName` / `classTeacherName`

> 后台无「请假原因」字段；请假原因只能从群聊里学生/家长自述提取（见 `leave_notes.py`）。

---

## 7. 学生详情（可选，人工深读用）

`GET /cStudentFile/findProductStats?productStudentId=<X>` → `data.classStats[]`（与出勤表同源）、
`data.comms[]`（沟通记录，实测多为空）、`data.parentNeedsList[]`。

---

## 8. 数据字典（枚举中文映射）

`GET /sysDictionary/findSysDictionary?type=<type>`
→ `data.resysDictionary.sysDictionaryDetails[]`，每项 `{ value, label }`。

技能用到的字典：
- `type=productType` → **购课类型**：1=直播、2=回放、3=试听（插班）、4=测试（对应花名册 `cIncomeProductType`；映射已固化在 `common.BUY_TYPE_LABEL`）
- `type=productBroadcastType` → **课程播放类型**：1=直播课、2=录播课（对应花名册 `proBroadcastType`）

---

## 字段 join 关系

```
students.json (coTeacherName=联席)
   ├─ stuName ──────────────► groups.json (studentName 交集 ＋ 群归属 == 本角色) ──► chats.json
   └─ ProductId ────────────► attendance_raw.json (productId) ──► attendance_clean.json
                                                                          │
chats.json ──► analysis.json ──► leave_notes.json ──┐                  │
                                                    └─► merge.py ◄─────┘
                                                          │
                                                   analysis_merged.json
                                                          │
                                                  report.py
```

### analysis.json 关键字段口径（v4）

| 字段 | 含义 |
|---|---|
| `n_stu` | 学生/家长消息总数（已剔除占位噪声） |
| `n_stu_sub` | 其中**实质消息**数（提问/反馈/交作业等） |
| `n_ack` | 其中**礼节性回应**数（好的/收到/谢谢老师…，无需回复） |
| `q` | 学生疑问密度，**仅统计实质消息** |
| `student_coop` / `parent_coop` | 配合度 0–5，**仅用实质消息**；家长侧致谢最多计 4 分 |
| `unresolved` | 是否未闭环：尾部最后一条老师消息**之后**仍有实质提问，**礼节语不算** |
| `unres_evi` | 未闭环证据（日期 + 提问摘要），非未闭环时为「会话尾部无未获回应提问」 |
| `resp_n` | 窗口内「学生实质提问 → 老师首次回复」的轮数 |
| `resp_max_h` | 最长等待小时数（聊天自算，取代后台 `timeoutDuration`） |
| `resp_slow` | 是否存在 ≥24h 且**老师未说明缘由**的等待 → 计入定档红 |
| `resp_slow_cnt` | 上述迟缓轮数 |
| `resp_pend_n` | 窗口末尾老师仍未回复的提问数（并入「需回复」） |
| `resp_evi` | 响应时效证据/结论（如「2026-09-28 学生提问「…」后约 60 小时才见老师回复」或「老师已说明安排，不计迟缓」） |

> 「无需回复」判定：`extract.py` 的 `is_ack()` / `is_noise()`。判定顺序：
> ① 剥离 emoji/符号（`EMOJI_RE`：🍵👍🙏…）与微信文本表情代号（`ACK_EXPR_RE`：`[玫瑰][抱拳][捂脸]`）；
> ② 剩余为空 → 纯表情/纯标点，判礼节（单独「？」例外，属求答疑）；
> ③ 词表拼接（支持「明白了，谢谢老师🍵🍵🍵」「收到，谢谢老师[玫瑰]」）；
> ④ 兜底：致谢/致歉词 + ≤2 字称呼（「谢谢丹姐」「辛苦老师了」）判礼节。
> 反例（仍需响应）：`老师 / 在吗 / @某某 / 好的，我再证明一下 / 这个第四题怎么做 / 麻烦看一下 / ？`。
