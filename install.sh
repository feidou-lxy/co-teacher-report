#!/usr/bin/env bash
#
# 联席学情报告技能 · 一键安装脚本
#
# 同事用法（命令由《安装手册》给出，**不再携带任何密钥**）：
#   bash <(curl -sL https://raw.githubusercontent.com/feidou-lxy/co-teacher-report/main/install.sh)
#
# 飞书数据走 **lark-cli 个人授权**（v5.04）：装好后首次取数会给出浏览器授权链接，
# 你本人点一下「同意授权」即可（全程不用发飞书密码/验证码/token 给任何人）；
# 之后日常运行复用本机登录态，不会重复弹授权。
#
# 其他参数：
#   --team=娜娜组       顺便把配置部署到某个工作目录（配合 --workdir）
#   --workdir=<路径>    见上
# ── 旧共享应用身份（存量兼容，已非默认）──
#   --appid=cli_xxx --secret=xxx   走旧的应用凭证通道（需要同时给 --account 或 --no-verify）
#   --account=138xxx    公司手机号/邮箱（仅旧通道的身份验证用）
#   --no-verify         跳过旧通道的手机号验证（仅调试用）
#
# 也可用环境变量覆盖源仓库：
#   REPO=https://github.com/其他账号/co-teacher-report BRANCH=main bash <(curl -sL <install.sh地址>)
#
set -euo pipefail

REPO="${REPO:-https://github.com/feidou-lxy/co-teacher-report}"
BRANCH="${BRANCH:-main}"
NAME="co-teacher-report"
# 正常安装到 WorkBuddy 技能目录；CTR_DEST 仅用于测试/自定义位置
DEST="${CTR_DEST:-$HOME/.workbuddy/skills/$NAME}"

APPID="$(
  # 默认内置的 App ID（不是敏感信息）
  echo "${CTR_APP_ID:-cli_a95f759091bddcd5}"
)"
SECRET="${CTR_APP_SECRET:-}"
VERIFY=1
TEAM=""
WORKDIR=""
ACCOUNT="${CTR_EMPLOYEE_ACCOUNT:-}"

# ── 参数解析（兼容 --k=v 与 --k v） ────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --secret=*)  SECRET="${1#*=}" ;;
    --secret)    SECRET="${2:-}"; shift ;;
    --appid=*)   APPID="${1#*=}" ;;
    --appid)     APPID="${2:-}"; shift ;;
    --team=*)    TEAM="${1#*=}" ;;
    --team)      TEAM="${2:-}"; shift ;;
    --workdir=*) WORKDIR="${1#*=}" ;;
    --workdir)   WORKDIR="${2:-}"; shift ;;
    --account=*) ACCOUNT="${1#*=}" ;;
    --account)   ACCOUNT="${2:-}"; shift ;;
    --no-verify) VERIFY=0 ;;
    -h|--help)   sed -n '2,25p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "⚠️  忽略未知参数：$1" >&2 ;;
  esac
  shift
done

echo "📦 正在从 $REPO (分支 $BRANCH) 下载技能包…"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

TGZ="$TMP/$NAME.tgz"

# 优先直连 codeload：github.com/archive 会 302 到 codeload，部分代理/网络
# 在重定向那一步失败（实测报 502 或 HTTP2 framing）。直连少一跳，明显更稳。
SLUG="${REPO#https://github.com/}"; SLUG="${SLUG%/}"
if [ "$SLUG" != "$REPO" ]; then
  PRIMARY="https://codeload.github.com/$SLUG/tar.gz/refs/heads/$BRANCH"
else
  PRIMARY="$REPO/archive/refs/heads/$BRANCH.tar.gz"
fi
FALLBACK="$REPO/archive/refs/heads/$BRANCH.tar.gz"

# --http1.1：避开 HTTP2 framing 错误；--retry：国内网络偶发中断
CURL_OPTS=(-fsSL --http1.1 --retry 2 --retry-delay 1 --connect-timeout 20)

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl "${CURL_OPTS[@]}" "$1" -o "$TGZ"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$TGZ" "$1"
  else
    echo "❌ 需要 curl 或 wget，请先安装其中之一。" >&2
    exit 1
  fi
}

if ! fetch "$PRIMARY"; then
  echo "   直连下载失败，换备用地址重试…"
  if ! fetch "$FALLBACK"; then
    echo "❌ 下载失败（网络问题）。请检查网络后重跑同一条命令；" >&2
    echo "   若在开代理/VPN，先关掉再试。" >&2
    exit 1
  fi
fi

tar -xzf "$TGZ" -C "$TMP"
# GitHub 归档顶层目录形如 co-teacher-report-<branch>/
SRC="$(find "$TMP" -maxdepth 1 -type d -name "${NAME}-*" | head -n1)"
if [ -z "$SRC" ]; then
  echo "❌ 解压后未找到 $NAME 目录，请检查仓库地址/分支是否正确。" >&2
  exit 1
fi

# ── 生成团队配置 ────────────────────────────────────────────────────────
# 默认（无 --secret）：模板原样生成本机配置 —— via=cli（lark-cli 个人授权），
# 不含任何 appSecret。有 --secret 时走旧的应用凭证通道（存量兼容，已非默认）。
if [ -z "$SECRET" ]; then
  python3 - "$SRC" <<'PY'
import glob, json, os, sys
root = sys.argv[1]
tpls = sorted(glob.glob(os.path.join(root, "assets", "teams", "*.template.json")))
if not tpls:
    sys.exit("❌ 安装包里没有 assets/teams/*.template.json，请重新下载。")
for tpl in tpls:
    with open(tpl, encoding="utf-8") as f:
        cfg = json.load(f)
    fs = cfg.get("feishu") or {}
    fs["via"] = "cli"
    fs["_via_note"] = ("cli = 走本机 lark-cli 个人授权（v5.04 默认）。"
                       "首次取数会引导授权；不含任何共享密钥。")
    fs.pop("auth", None)          # 保险：绝不落共享密钥
    cfg["feishu"] = fs
    out = tpl.replace(".template.json", ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.chmod(out, 0o600)
    print(f"   🧩 已生成 {os.path.basename(out)}（{cfg.get('team', '?')}，个人授权模式）")
PY
else
  echo "ℹ️  检测到 --secret：走旧共享应用身份通道（存量兼容）。"
  echo "    推荐尽快迁移到个人授权：对旧工作目录运行 guard.py --migrate-feishu <工作目录>。"
  python3 - "$SRC" "$APPID" "$SECRET" <<'PY'
import glob, json, os, sys
root, appid, secret = sys.argv[1], sys.argv[2], sys.argv[3]
tpls = sorted(glob.glob(os.path.join(root, "assets", "teams", "*.template.json")))
if not tpls:
    sys.exit("❌ 安装包里没有 assets/teams/*.template.json，请重新下载。")
for tpl in tpls:
    with open(tpl, encoding="utf-8") as f:
        cfg = json.load(f)
    fs = cfg.setdefault("feishu", {})
    auth = fs.setdefault("auth", {})
    if appid:
        auth["appId"] = appid
    auth["appSecret"] = secret
    fs["via"] = "api"
    out = tpl.replace(".template.json", ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.chmod(out, 0o600)
    print(f"   🔑 已生成 {os.path.basename(out)}（{cfg.get('team', '?')}，旧共享身份）")
PY
fi

mkdir -p "$HOME/.workbuddy/skills"
rm -rf "$DEST"
mv "$SRC" "$DEST"

echo "✅ 技能已安装到：$DEST"

# ── 旧共享身份的身份验证（仅 --secret 时执行；个人授权路径不做这一步） ──
if [ "$VERIFY" = "1" ] && [ -n "$SECRET" ]; then
  echo
  if [ -n "$ACCOUNT" ]; then
    # 账号随命令一起传入（终端里用 --account=，AI 代为执行时也走这条）
    python3 "$DEST/scripts/guard.py" --verify --account "$ACCOUNT" || {
      echo
      echo "⚠️  身份验证没通过，技能已装好，但暂时拉不到数据。" >&2
      echo "    常见原因：手机号不是公司飞书账号。" >&2
      echo "    改用公司邮箱重试：python3 $DEST/scripts/guard.py --verify" >&2
    }
  elif [ -t 0 ]; then
    # 有终端 → 正常交互询问
    python3 "$DEST/scripts/guard.py" --verify || {
      echo
      echo "⚠️  身份验证没通过，技能已装好，但暂时拉不到数据。" >&2
      echo "    常见原因：输入的手机号不是公司飞书账号。重试：" >&2
      echo "      python3 $DEST/scripts/guard.py --verify" >&2
    }
  else
    # 没有终端（AI / 脚本代为执行）：别去等 input()，直接给可执行的一步
    echo "ℹ️  技能已装好，还差最后一步：验证公司身份（需要你的 11 位公司手机号）。"
    echo "    把手机号填进下面这条命令后执行即可："
    echo
    echo "      python3 $DEST/scripts/guard.py --verify --account=你的手机号"
  fi
fi

# ── 个人授权预检（无 secret 的默认路径）────────────────────────────────
if [ -z "$SECRET" ]; then
  echo
  if python3 "$DEST/scripts/feishu_auth.py" status; then
    echo "   飞书个人授权已就绪，取数会自动复用本机登录态。"
  else
    echo "   首次取数时会引导你完成授权（浏览器点一下，不发任何密码/验证码/token）。"
    echo "   也可以现在就授权：python3 $DEST/scripts/feishu_auth.py login"
  fi
fi

# ── 可选：顺手把某个团队配置部署到工作目录 ─────────────────────────────
if [ -n "$TEAM" ] && [ -n "$WORKDIR" ]; then
  echo
  python3 "$DEST/scripts/guard.py" --workdir "$WORKDIR" --team "$TEAM" || true
fi

echo
echo "────────────────────────────────────────────────────────────"
echo "✅ 安装完成。接下来直接对 WorkBuddy 说："
echo
echo "   用学情分析技能，生成 <联席名> 2026年9月1日-9月30日 的学情分析"
echo
echo "   （首次会让你发一次后台账号密码，图形验证码会截图给你辨认；"
echo "    飞书表走你自己的授权，浏览器点一下即可，不需要发任何凭据）"
echo "────────────────────────────────────────────────────────────"
echo "自检：python3 $DEST/scripts/feishu_auth.py status"
