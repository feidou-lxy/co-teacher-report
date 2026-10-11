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
- `groupName` 群名（报告里解析课程）
- `isTimeout` / `isReplied` / `timeoutDuration` 响应时效标记（监管维度，计入定档）

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

## 字段 join 关系

```
students.json (coTeacherName=联席)
   ├─ stuName ──────────────► groups.json (studentName 交集) ──► chats.json
   └─ ProductId ────────────► attendance_raw.json (productId) ──► attendance_clean.json
                                                                          │
chats.json ──► analysis.json ──► leave_notes.json ──┐                  │
                                                    └─► merge.py ◄─────┘
                                                          │
                                                   analysis_merged.json
                                                          │
                                                  report.py + xlsx.py
```
