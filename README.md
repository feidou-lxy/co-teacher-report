# 联席学情报告技能 · co-teacher-report

把企业微信班级群聊天 + 后台课节考勤，一键生成「联席 / 教务」双视角学情报告（HTML + 待办 Excel）。

## 一键安装（推荐）

```bash
bash <(curl -sL https://raw.githubusercontent.com/feidou-lxy/co-teacher-report/main/install.sh)
```

脚本会把技能装到 `~/.workbuddy/skills/co-teacher-report/`。
（如需指定其他仓库/分支：`REPO=https://github.com/其他账号/co-teacher-report BRANCH=main bash <(curl -sL <install.sh地址>)`）

## 手动安装

```bash
git clone https://github.com/feidou-lxy/co-teacher-report.git /tmp/co-teacher-report
mkdir -p ~/.workbuddy/skills
rm -rf ~/.workbuddy/skills/co-teacher-report
mv /tmp/co-teacher-report ~/.workbuddy/skills/co-teacher-report
```

## 使用（用你自己的后台账号）

1. 登录 `stadminv2.xqmsx.cn`，从任意请求头取出 `x-token`（JWT，约 24 小时有效）
2. 写入工作目录：
   ```bash
   mkdir -p ~/co-report && echo "粘贴你的x-token" > ~/co-report/token.txt
   ```
3. 生成报告（缺 `openpyxl` 会自动安装）：
   ```bash
   python ~/.workbuddy/skills/co-teacher-report/scripts/run_all.py \
     --co <联席名> --window 2026-09-01 --month 2026-09
   ```
4. 产物：`<联席>_学情分析报告.html` ＋ `<联席>_学生待办清单.xlsx`

## 版本与口径（v3.3）

- **双视角**：`--co <联席名>` 出联席视角（按课程切换）；`--edu <教务名>` 出教务视角（按下属联席下钻，顶部可切「按课程 / 按联席」）。两种视角共用同一套分析逻辑。
- **数据以聊天记录为准**（v3.2 / v3.3 重要变更）：
  - 「是否需回复」不再采信后台「是否回复」列，改为识别会话尾部是否仍有未获回应提问。
  - 「响应时效」不再采信后台「超时未回复」列，改为由聊天记录自算（学生提问 → 老师首条回复的最长等待；≥24h 且老师未说明缘由才判迟缓；老师已说明休息 / 放假等则豁免）。
  - 后台监管标记（`isReplied` / `isTimeout`）实测与聊天事实不符，已全部弃用。
- **礼节性消息**：学生/家长的「好的 / 谢谢老师 / 明白了」等收尾语（含表情）识别为无需回复，不计入未闭环、不刷配合度。
- 报告速览卡以「一行信号条」呈现量化风险信号（情绪 / 出勤 / 作业 / 礼貌回应），不堆长文字。
- 详细字段与判定见技能内 `SKILL.md` 与 `references/api_contract.md`。

## 安全提醒

后台「群列表」接口无需 token 也能访问（越权风险）。请**只用自己账号**取 token、**不要外传 token**，也不要查询非本人名下的班级数据。

## 说明

- 本仓库仅含 `SKILL.md` + `scripts/` + `references/`，**不含任何 token**，可放心公开/转发。
- 换联席改 `--co`，换月份改 `--window` / `--month`，互不干扰。
- 详细口径与字段说明见技能内 `SKILL.md` 与 `references/api_contract.md`。
