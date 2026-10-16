#!/usr/bin/env bash
#
# 联席学情报告技能 · 一键安装脚本
# 用法（默认从 GitHub 仓库安装）：
#   bash <(curl -sL https://raw.githubusercontent.com/feidou-lxy/co-teacher-report/main/install.sh)
#
# 也可用环境变量覆盖源仓库：
#   REPO=https://github.com/其他账号/co-teacher-report BRANCH=main bash <(curl -sL <install.sh地址>)
#
set -euo pipefail

REPO="${REPO:-https://github.com/feidou-lxy/co-teacher-report}"
BRANCH="${BRANCH:-main}"
NAME="co-teacher-report"
DEST="$HOME/.workbuddy/skills/$NAME"

echo "📦 正在从 $REPO (分支 $BRANCH) 下载技能包…"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

TGZ="$TMP/$NAME.tgz"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$REPO/archive/refs/heads/$BRANCH.tar.gz" -o "$TGZ"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$TGZ" "$REPO/archive/refs/heads/$BRANCH.tar.gz"
else
  echo "❌ 需要 curl 或 wget，请先安装其中之一。" >&2
  exit 1
fi

tar -xzf "$TGZ" -C "$TMP"
# GitHub 归档顶层目录形如 co-teacher-report-<branch>/
SRC="$(find "$TMP" -maxdepth 1 -type d -name "${NAME}-*" | head -n1)"
if [ -z "$SRC" ]; then
  echo "❌ 解压后未找到 $NAME 目录，请检查仓库地址/分支是否正确。" >&2
  exit 1
fi

mkdir -p "$HOME/.workbuddy/skills"
rm -rf "$DEST"
mv "$SRC" "$DEST"

echo "✅ 技能已安装到：$DEST"
echo
echo "下一步（用你自己的后台账号）："
echo "  ★ 业务同学最简单的一条：新开一个对话，把「后台网址 + 账号密码」发给 WorkBuddy，"
echo "    并说要出的报告，例如："
echo "      用 co-teacher-report 技能，生成 张巍（联席） 2026年9月1日-9月30日 的学情分析"
echo "      我的后台账号是 <账号>，密码是 <密码>"
echo "    它会自动登录后台（图形验证码会截图发给你辨认），然后取数出报告。"
echo
echo "  命令行用法（可选，供技术同学）："
echo "    mkdir -p ~/co-report && echo \"粘贴你的x-token\" > ~/co-report/token.txt"
echo "    python $DEST/scripts/run_all.py --api --fill --co <联席名> --window 2026-09-01 --end 2026-09-30"
echo
echo "📘 完整操作说明（业务向、含报告怎么看）：$DEST/操作手册.html"
echo "   开发向说明：$DEST/SKILL.md；接口契约：$DEST/references/api_contract.md"
