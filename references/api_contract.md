# 学生管理后台 API 契约（stadminv2.xqmsx.cn）

本文件记录「联席学情报告」技能逆向得到的学生管理后台接口。所有接口域名前缀为
`https://stadminv2.xqmsx.cn/api`，鉴权头为 `x-token: <JWT>`（来自登录后 Cookie 里的 token 字段）。

> ⚠️ 安全提示：早期探测发现 `wxworkSessionStat/getList` 与 `wxworkChat/getChatList` 在未带
> token 时仍可访问（越权风险）。脚本统一带 token 调用；如发现该风险，应反馈给平台方修复。

---

## 1. 登录与鉴权

- 登录页：`https://stadminv2.xqmsx.cn/#/login`（账户密码 + 图形验证码）。
- 登录成功后，浏览器 Cookie 中 `token` 字段即 JWT；也可从 `localStorage` 读取。
- token 有效期约 24 小时（JWT `exp`），过期需重新登录。
- 取得后写入 `<workdir>/token.txt`（纯文本，一行），脚本自动读取。

---

## 2. 学生花名册

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

> 花名册与「我的学生」列表同源（一对一，一个学生一条记录）。购课类型/课程类型的**中文映射**见第 7 节字典接口。

---

## 3. 群聊列表（含响应时效）

`GET /wxworkSessionStat/getList`

参数：`year=4&term=3&timeDimension=month|day&statDate=YYYY-MM-DD&page=&pageSize=`

- **月维度**：`timeDimension=month&statDate=<当月第一天>`（如 `2026-09-01`）。
  注意：当前月后台尚未聚合，月维度返回 0，需用日维度补。
- **日维度**：`timeDimension=day&statDate=<当天>`。
- 实践：月维度（窗口起始月）与日维度（今天）并集，按 `roomId` 去重，得到完整群集合。

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

## 4. 群聊消息

`GET /wxworkChat/getChatList?roomId=<room>&page=1&pageSize=500`

- 倒序返回，单群最多 500 条。
- `data.list[]` 每条消息：`msgTime, role(1=老师/2=学生家长), msgType(text/voice), name, content`
  - 文本：`content` 为字符串，或 `{ "content": "..." }`
  - 语音：`content` 为 `{ "content": { "content": "转写文本" } }`

---

## 5. 逐节课出勤（后台权威）

`GET /cClassAttend/getCClassAttendList?page=&pageSize=500&productId=<X>`

- 按课程产品 `productId` 分页拉取；或全量拉取后按 `coTeacherName` 过滤。
- 返回 `data.list[]` 字段（技能用到）：
  - `studentName` / `studentSeq`
  - `classSeq` 第几节课
  - `classId` 课节 ID（去重键）
  - `timeStart` 上课时间
  - `isAttend` 1/0（出勤 / 缺勤）
  - `isLate` / `isGone` 迟到 / 早退
  - `isPlayback` 是否看回放
  - `playbackTotalValidTime` 回放有效时长
  - `cProduct.name` 课程产品名
  - `coTeacherName` / `classTeacherName`

> 后台无「请假原因」字段；请假原因只能从群聊里学生/家长自述提取（见 `leave_notes.py`）。

---

## 6. 学生详情（可选，人工深读用）

`GET /cStudentFile/findProductStats?productStudentId=<X>` → `data.classStats[]`（与出勤表同源）、
`data.comms[]`（沟通记录，实测多为空）、`data.parentNeedsList[]`。

---

## 7. 数据字典（枚举中文映射）

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
