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
echo "  1) 登录 stadminv2.xqmsx.cn，取出请求头里的 x-token（约 24h 有效）"
echo "  2) mkdir -p ~/co-report && echo \"粘贴你的x-token\" > ~/co-report/token.txt"
echo "  3) python $DEST/scripts/run_all.py --co <联席名> --window 2026-09-01 --month 2026-09"
echo
echo "详细说明见仓库 README.md 与技能内 SKILL.md。"
