---
name: co-teacher-report
description: 生成「联席 / 教务」双视角学生学情报告。从学生管理后台（stadminv2.xqmsx.cn）拉取某联席（coTeacherName）或某教务（classTeacherName）名下学生的群聊记录与逐节课出勤，逆向分析薄弱知识点、配合度（学生自驱 vs 家长支持）、响应时效与风险，输出 4 块式 HTML 报告（联席按课程切换 / 教务按下属联席下钻）+ 待办清单 Excel。This skill should be used when a 教育机构联席/教务/班主任/学管想要快速掌握名下学生的学情、出勤与跟进动作，或需要批量生成可下发的学情报告。
agent_created: true
---

# 联席/教务学情报告生成

## 用途

把分散在「企微群聊」和「学生管理后台」里的学情信号，汇成一份**一眼能看**的报告：
谁该重点盯、配合度如何、出勤有没有问题、薄弱点是什么、下一步怎么跟进。

支持两种视角：

- **联席视角**（`--co 张巍`）：输入一个**联席姓名**，输出该联席名下全部学生的学情报告 + 待办清单（按课程切换）。
- **教务视角**（`--edu 罗绂燕`）：输入一个**教务姓名**，输出该教务管辖的全部学生（跨多个下属联席），报告**按联席分组下钻**，每 Tab 对应一名下属联席，学生卡标注所属联席；总览 / 待办 Excel 同步增加「所属联席」列。

## 何时使用

- 用户说「帮张巍（或某联席）出一份学情报告 / 学情分析 / 跟进清单」
- 用户说「帮罗绂燕（或某教务）出一份管辖学生的学情报告」——即教务视角，看其名下所有联席的整体与分组情况
- 用户想要「按课程看学生表现」「按联席看下属学情」「看谁出勤异常 / 谁配合度低 / 谁有未解疑问」
- 用户要把群聊和出勤数据汇成可下发的报告给联席 / 教务团队

## 前置条件

- 用户本人的后台账号（stadminv2.xqmsx.cn）可登录，且该账号名下能看到目标联席 / 教务的学生。
- WorkBuddy 受管 Python（3.10+）。`openpyxl` 依赖**无需手动装**：`run_all.py` / `xlsx.py`
  启动时会自动检测，缺失则用当前 Python 的 `pip` 安装（仅首次，见 `common.ensure_openpyxl`）。
- 一个工作目录 `<workdir>`（建议新建空目录，所有中间文件落在这里）。

## 工作流程

### 第 0 步：登录并取 token

用 agent-browser 打开 `https://stadminv2.xqmsx.cn/#/login`，按页面提示登录
（账户密码 + 图形验证码；验证码可用截图读图识别后填入）。登录成功后，从浏览器会话里取出
`x-token` 并写入 `<workdir>/token.txt`（纯文本一行）。

在 agent-browser 里取 token 的片段（登录后执行）：

```js
(() => { try { return document.cookie.split('; ').find(c=>c.startsWith('token=')).split('=')[1]; }
        catch(e){ return localStorage.getItem('token') || 'NOT_FOUND'; } })()
```

把返回串写入 `<workdir>/token.txt`。token 有效期约 24 小时，过期重登。

> 详细接口契约见 `references/api_contract.md`（含越权风险提示）。

### 第 1~10 步：端到端运行

在 `<workdir>` 内执行编排脚本（token 已就位）：

```bash
# 联席视角（按课程切换）
python <skill>/scripts/run_all.py --co 张巍 --window 2026-09-01 --month 2026-09
# 教务视角（按下属联席下钻）
python <skill>/scripts/run_all.py --edu 罗绂燕 --window 2026-09-01 --month 2026-09
```

`<skill>` 为本技能目录。该命令依次执行：

1. `pull_students.py` — 拉花名册，按 `coTeacherName`（联席）或 `classTeacherName`（教务）过滤出目标学生 → `students.json`
2. `pull_groups.py` — 拉群聊列表（月维度+日维度并集），按学生名交集，并携带 `coTeacherName`/`classTeacherName` → `groups.json`
3. `pull_chats.py` — 逐群拉聊天（文本+语音转写）→ `chats.json`
4. `pull_attendance.py` — 拉逐节课出勤（按 ProductId），按 `students.json` 学生白名单过滤 → `attendance_raw.json`
5. `build_attendance.py` — 去重清洗 → `attendance_clean.json`
6. `extract.py` — 信号提取（窗口过滤、薄弱点、配合度、未闭环疑问）→ `analysis.json`
7. `leave_notes.py` — 从聊天捞学生/家长自述的请假缘由 → `leave_notes.json`
8. `merge.py` — 出勤 + 请假备注并入分析 → `analysis_merged.json`
9. `report.py` — 生成 4 块式 HTML 报告（联席按课程 / 教务按联席）→ `<姓名>_学情分析报告.html`
10. `xlsx.py` — 生成待办 Excel（总览/待办/出勤明细，含所属联席列）→ `<姓名>_学生待办清单.xlsx`

`--co` 与 `--edu` 二选一，不可同用。

若需分步调试，可单独调用各脚本（均支持 `--workdir` / `--co` / `--edu` / `--window` 等参数，
详见各脚本 `--help`）。

### 单跑某联席 / 教务 / 换窗口

- 换联席：改 `--co 王五`（脚本按 `coTeacherName` 重新过滤，互不影响）。
- **换教务**：改 `--edu 罗绂燕`（脚本按 `classTeacherName` 过滤出该教务管辖的全部学生，跨多个下属联席）。
  `--co` 与 `--edu` 二选一，不可同用。
- 换窗口：改 `--window 2026-10-01`（`extract.py` / `leave_notes.py` 仅分析该起点后的聊天）。
- 当前月群聊：月维度对「当前月」返回 0，编排脚本已自动补日维度，无需手改。

## 报告结构与口径（务必对齐后再大批量出）

每张学生卡 4 块：

1. **速览卡** — 定档（红/黄/绿）+ 配合度**结论式**（去星级）+ 最该盯 1 件事 + 一句话总评
2. **出勤（后台权威）** — 逐节 `✓/回放/✗` + 日期 → `出勤X/N·缺Y·回放Z` → **是否需关注**（红黄绿）+ 请假备注
3. **知识掌握与薄弱点** — 薄弱 / 错误类型 / 优势
4. **联席跟进 Action** — 优先级动作 + 追踪节奏 + 改善信号

口径约定（已与用户确认）：

- **定档**：🔴 红 = 后台标记超时/未回复（监管维度，必计入），或聊天有未闭环关键疑问/负面情绪；
  🟡 黄 = 有未解疑问、作业负面信号、出勤异常或负面情绪；🟢 绿 = 其余正常推进。
- **配合度**：结论式，区分「学生自驱」与「家长支持」（如“家长推着走”型 / 放手培养型）。
  数据源未区分发言者 ID，为启发式估算，供跟进策略参考。
- **出勤判定**：全缺 → 🔴 高风险建议电话；缺 ≥2 节 或 “缺了且未看回放” → 🔴 需关注；
  缺 1 节未看回放 → 🟡 轻关注；缺 1 节但已看回放 / 全勤 → 🟢 常规。**出勤为独立提示，不改变定档。**
- **请假备注**：后台无请假原因字段，仅从群聊里学生/家长本人发言摘录（如“课时间冲突要请假”），
  具体以教务记录为准。
- **窗口**：仅分析窗口起点之后的聊天；出勤取后台最新聚合的课节（新课时重跑刷新）。
- **教务视角分组**：`--edu` 时报告按学生所属「联席」(`coTeacherName`) 分组，每 Tab 一个下属联席，便于教务横向对比各联席的学情与跟进负荷；学生卡额外标注所属联席。总览 / 待办 Excel 同步增加「所属联席」列。

## 目录与产物

```
<workdir>/
├── token.txt                # 后台 x-token（登录后写入）
├── students.json            # 花名册（该联席 / 教务）
├── groups.json              # 群聊列表（含 roomId、coTeacherName、classTeacherName）
├── chats.json               # 群聊消息
├── attendance_raw.json      # 原始出勤行
├── attendance_clean.json    # 清洗后出勤
├── analysis.json            # 信号提取
├── leave_notes.json         # 请假备注
├── analysis_merged.json     # 合并最终分析
├── <姓名>_学情分析报告.html    # ← 主交付（联席按课程切换 / 教务按联席下钻）
└── <姓名>_学生待办清单.xlsx    # ← 主交付
```

## 注意事项

- token 约 24 小时过期，过期后重登并覆盖 `token.txt` 再重跑。
- 群聊单群上限 500 条；超活跃群可能截断，属已知限制。
- 当前脚本针对「高二数学」课程命名规则解析课程标签；其他学科接入时微调
  `common.clean_course_name` 与 `report.py` / `xlsx.py` 里的 `course_of` 正则即可。
- 接口为逆向所得，若后台升级改字段，优先核对 `references/api_contract.md` 并相应调整脚本。
