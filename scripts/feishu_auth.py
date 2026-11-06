"""feishu_auth.py — 飞书个人授权引导（v5.04）：lark-cli 设备码流程。

背景（2026-11-06 用户拍板）：
    飞书取数从「公司共享应用凭证（appSecret）」切换为「同事本人 lark-cli 个人授权」。
    安装命令不再携带 --secret；本脚本负责：检查 lark-cli 是否可用、检查用户登录态、
    引导完成首次/重新授权。

安全设计（对用户的承诺）：
    · 全程**设备码流程**：用户自己在浏览器里点授权，AI 只拿到「授权完成」的结果，
      用户不需要把飞书密码、短信验证码或任何 token 发给 AI；
    · 默认只申请**最小权限**：sheets + wiki（读表格与解析 wiki 链接，只读）；
    · 授权失败**绝不自动切回共享应用身份**。

用法：
    python3 scripts/feishu_auth.py status    # 检查 lark-cli 与登录态（安装后自检用）
    python3 scripts/feishu_auth.py login     # 未授权时引导授权（打印链接 → 等待完成）
    python3 scripts/feishu_auth.py login --domain sheets,wiki,drive,contact   # 需要更多域时

退出码：0 已授权/授权成功 ｜ 1 未装 lark-cli ｜ 2 未授权/授权失败
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

# 最小权限域：sheets（读表格）+ wiki（解析 wiki 节点链接）。
# 不申请 im/contact/drive 等——本技能读表用不到。
DEFAULT_DOMAINS = "sheets,wiki"


def _cli_path():
    return (os.environ.get("LARK_CLI") or shutil.which("lark-cli"))


def _env():
    # ⚠️ 必须禁代理：本机 Clash 代理会劫持 open.feishu.cn（实测）
    return dict(os.environ, LARK_CLI_NO_PROXY="1")


def check_status(verbose=True):
    """返回 (ok, 原因)。ok=True 表示已有可用的用户登录态。"""
    path = _cli_path()
    if not path:
        if verbose:
            print("❌ 本机没有 lark-cli（飞书官方命令行工具）。")
            print("   它随 WorkBuddy 一起安装；若确实没有，请让 WorkBuddy 帮你安装 lark-cli。")
        return False, "not_installed"
    try:
        p = subprocess.run([path, "auth", "status"], capture_output=True,
                           text=True, env=_env(), timeout=30)
    except Exception as e:
        if verbose:
            print(f"❌ lark-cli 运行异常：{e}")
        return False, f"error: {e}"
    out = (p.stdout or "") + (p.stderr or "")
    try:
        j = json.loads(out[out.find("{"):out.rfind("}") + 1]) if "{" in out else {}
    except ValueError:
        j = {}
    note = j.get("note") or ""
    # 未登录形态：identity=bot 且 note 提示 token 不存在
    if j.get("identity") == "bot" or "Token does not exist" in note or "token" in note.lower():
        if verbose:
            print("ℹ️ 飞书尚未授权（当前只有应用默认身份，没有你的个人授权）。")
            print("   运行下面这条命令完成首次授权（浏览器里点一下即可，全程不用发密码/验证码）：")
            print("     python3 <skill>/scripts/feishu_auth.py login")
        return False, "not_authorized"
    user = j.get("userName") or ""
    if verbose:
        print(f"✅ 飞书已授权：{user or '已登录用户'}（lark-cli 个人登录态）")
    return True, ""


def login(domains=DEFAULT_DOMAINS, verbose=True):
    """设备码授权：--no-wait 拿验证链接 → 用户浏览器授权 → 本地轮询完成。"""
    path = _cli_path()
    if not path:
        print("❌ 本机没有 lark-cli，无法发起授权。请让 WorkBuddy 帮你安装 lark-cli。")
        return 1
    print(f"→ 发起飞书授权（申请权限域：{domains}，只读）…")
    try:
        p = subprocess.run(
            [path, "auth", "login", "--domain", domains, "--no-wait", "--json"],
            capture_output=True, text=True, env=_env(), timeout=60)
    except Exception as e:
        print(f"❌ 发起授权失败：{e}")
        return 2
    out = (p.stdout or "") + (p.stderr or "")
    try:
        j = json.loads(out[out.find("{"):out.rfind("}") + 1])
    except ValueError:
        print(f"❌ 授权发起失败（lark-cli 返回无法解析）：\n{out[:400]}")
        return 2
    url = j.get("verification_url") or ""
    code = ""
    if "&user_code=" in url:
        code = url.split("&user_code=")[-1].split("&")[0]
    dc = j.get("device_code") or ""
    if not url or not dc:
        print(f"❌ 授权发起失败：\n{json.dumps(j, ensure_ascii=False)[:400]}")
        return 2
    expires = int(j.get("expires_in") or 600)
    print()
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  请完成飞书授权（约 30 秒）：")
    print(f"║  1. 在浏览器打开：{url}")
    if code:
        print(f"║  2. 确认页面上的验证码是：{code}")
    print("║  3. 点「同意授权」。")
    print("║  ⚠️ 全程不需要把密码 / 验证码 / token 发给任何人（包括 AI）。")
    print(f"║  链接 {expires // 60} 分钟内有效，正在等待你完成授权…")
    print("╚════════════════════════════════════════════════════════════╝")
    try:
        p2 = subprocess.run([path, "auth", "login", "--device-code", dc, "--json"],
                            capture_output=True, text=True, env=_env(),
                            timeout=expires + 60)
    except subprocess.TimeoutExpired:
        print("❌ 等待超时。请重新运行本命令再试一次。")
        return 2
    out2 = (p2.stdout or "") + (p2.stderr or "")
    ok = False
    try:
        j2 = json.loads(out2[out2.find("{"):out2.rfind("}") + 1]) if "{" in out2 else {}
        ok = (p2.returncode == 0) and (j2.get("ok") is not False) and not j2.get("error")
        if not ok:
            print(f"❌ 授权未完成：{json.dumps(j2, ensure_ascii=False)[:300]}")
    except ValueError:
        ok = (p2.returncode == 0)
    if not ok:
        if p2.returncode == 0:
            ok = True
        else:
            print(f"   lark-cli 输出：{out2[:300]}")
            return 2
    print("✅ 飞书授权完成！本机已保存登录态，之后的取数会自动复用，不会重复弹授权。")
    print("   （换电脑或授权过期时，会再次提示你重新点一次授权。）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="飞书个人授权（lark-cli 设备码流程）")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="检查 lark-cli 与登录态")
    lg = sub.add_parser("login", help="发起/完成授权（浏览器点一下，不发任何凭据）")
    lg.add_argument("--domain", default=DEFAULT_DOMAINS,
                    help=f"权限域，逗号分隔（默认 {DEFAULT_DOMAINS}，最小权限）")
    args = ap.parse_args()
    if args.cmd == "login":
        return login(args.domain)
    ok, _ = check_status()
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
