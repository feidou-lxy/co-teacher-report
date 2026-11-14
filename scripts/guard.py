#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
团队配置布署 + （旧共享身份的）企业门禁（v4.84；v5.04 起个人授权为主路径）

v5.04（2026-11-06 用户拍板）：飞书取数改为 **lark-cli 个人授权**（feishu_auth.py）。
  · 新安装不带 --secret：团队配置默认 `via=cli`、**不含任何 appSecret**；
  · 个人授权路径**不做**手机号查通讯录 / 180 天本地验证（那是旧共享应用的门禁）；
  · 旧工作目录迁移：`--migrate-feishu <workdir>` 把 profile.json 里的
    via=api + appSecret 移除（备份到 _not_deliverable/），换成 via=cli；
    团队映射（source / spreadsheetToken / sheets / items）与报告快照**原样保留**。
  · --verify / --status / --reset 为旧共享身份的存量能力，保留不动。

原理（旧共享身份，务必理解，不然会误以为它是保险箱）：
  1. 取数走**应用身份**（appId + appSecret → tenant_access_token），零配置、永不过期；
  2. 应用凭证分不清"谁在用"——所以加一道入职校验（公司手机号/邮箱查通讯录，
     缓存 180 天）。
⚠️ 这是"门锁"不是"保险箱"：拿到 secret 的人可以绕过本脚本直接调飞书接口。
   个人授权路径没有这个问题——读的是「谁授权、谁的权限」。

用法：
  # 布署一个团队到工作目录（v5.04：不再要求手机号验证）
  python3 guard.py --workdir ~/.workbuddy/reports/edu-张三 --team 娜娜组
  # 旧配置迁移到个人授权
  python3 guard.py --migrate-feishu ~/.workbuddy/reports/edu-张豫
  # 旧共享身份的门禁（存量用）
  python3 guard.py --status / --reset / --verify
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

# ── 常量 ────────────────────────────────────────────────────────────────
EXPECT_TENANT = "1026f4e82a93d75f"          # 双提数学
APP_ID = "cli_a95f759091bddcd5"
APP_SECRET = ""                              # 运行时从模板读取，此处不落明文
ACCESS_PATH = os.path.expanduser("~/.workbuddy/.co-teacher-report/access.json")
CACHE_DAYS = 180
BASE = "https://open.feishu.cn/open-apis"

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEAM_DIR = os.path.join(SKILL_ROOT, "assets", "teams")

# 团队名 → 模板文件。新增团队在这里加一行即可。
TEAMS = {
    "娜娜组": "nana.json",
    "高一数学 · 娜娜组": "nana.json",
    "孙一文组": "sunyiwen.json",
    "高二数学 · 孙一文组": "sunyiwen.json",
    # v5.09：叶子团队（26秋季初三数学）—— via=cli 无密钥形态，配置可进仓库
    "叶子团队": "yezi.json",
    "初三数学 · 叶子团队": "yezi.json",
    # v5.13：小白团队（两个工作簿，分年级）—— via=cli 无密钥形态
    "小白团队（初二）": "xiaobai2.json",
    "初二数学 · 小白团队": "xiaobai2.json",
    "小白团队（小学初一）": "xiaobai1.json",
    "小学初一 · 小白团队": "xiaobai1.json",
    # v5.16：呆呆团队（语英，两个工作簿）—— via=cli 无密钥形态
    "呆呆团队（初中）": "daidai1.json",
    "语英 · 呆呆团队（初中）": "daidai1.json",
    "呆呆团队（高中）": "daidai2.json",
    "语英 · 呆呆团队（高中）": "daidai2.json",
}


# ── 飞书调用 ────────────────────────────────────────────────────────────
def _call(method, path, body=None, token=None, timeout=20):
    url = BASE + path
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    # 绕过本机代理（Clash 之类会劫持 open.feishu.cn）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"code": -1, "msg": f"HTTP {e.code}", "detail": e.read().decode("utf-8")[:300]}


def tenant_token(app_id, app_secret):
    j = _call("POST", "/auth/v3/tenant_access_token/internal",
              {"app_id": app_id, "app_secret": app_secret})
    if j.get("code") != 0:
        raise SystemExit(f"❌ 应用凭证无效（code={j.get('code')} msg={j.get('msg')}）\n"
                         f"   检查 assets/teams/*.json 里的 appId / appSecret。")
    return j["tenant_access_token"]


def my_tenant(token):
    j = _call("GET", "/tenant/v2/tenant/query", token=token)
    return ((j.get("data") or {}).get("tenant") or {})


def _fetch_name(token, open_id):
    """补姓名（batch_get_id 不返回 name）。缺权限时静默返回空串，不影响验证。"""
    try:
        j = _call("GET", f"/contact/v3/users/{open_id}?user_id_type=open_id", token=token)
        return (((j.get("data") or {}).get("user") or {}).get("name") or "")
    except Exception:
        return ""


def lookup_employee(token, account):
    """用手机号或邮箱反查通讯录。返回 (open_id, name, 脱敏账号) 或 (None, None, None)。"""
    account = account.strip()
    is_mobile = bool(re.fullmatch(r"1[3-9]\d{9}", account))
    if is_mobile:
        path = "/contact/v3/users/batch_get_id?user_id_type=open_id"
        body = {"mobiles": [account]}
        masked = account[:3] + "****" + account[-4:]
    else:
        path = "/contact/v3/users/batch_get_id?user_id_type=open_id"
        body = {"emails": [account]}
        name, _, domain = account.partition("@")
        masked = (name[:2] + "***@" + domain) if domain else account[:2] + "***"

    j = _call("POST", path, body, token=token)
    if j.get("code") != 0:
        raise SystemExit(f"❌ 通讯录查询失败（code={j.get('code')} msg={j.get('msg')}）\n"
                         f"   多半是应用缺「通过手机号或邮箱获取用户 ID」权限，找管理员补。")
    for u in ((j.get("data") or {}).get("user_list") or []):
        if u.get("user_id"):
            oid = u["user_id"]
            return oid, (u.get("name") or _fetch_name(token, oid)), masked
    return None, None, masked


# ── 缓存 ────────────────────────────────────────────────────────────────
def _now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def read_access():
    try:
        with open(ACCESS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_access(rec):
    os.makedirs(os.path.dirname(ACCESS_PATH), exist_ok=True)
    with open(ACCESS_PATH, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.chmod(ACCESS_PATH, 0o600)


def access_ok(rec=None):
    rec = rec if rec is not None else read_access()
    if not rec:
        return False
    if rec.get("tenant_key") != EXPECT_TENANT:
        return False
    try:
        exp = datetime.datetime.fromisoformat(rec["expires_at"])
    except Exception:
        return False
    return _now() < exp


# ── 主流程 ──────────────────────────────────────────────────────────────
def ensure_access(app_id=None, app_secret=None, account=None, quiet=False):
    """确认本机已被验证为公司员工。通过返回 True，否则 SystemExit。"""
    if access_ok():
        if not quiet:
            rec = read_access()
            print(f"✅ 本机已验证：{rec.get('employee', {}).get('name', '公司员工')}"
                  f"（有效期至 {rec['expires_at'][:10]}）")
        return True

    app_id = app_id or APP_ID
    app_secret = app_secret or APP_SECRET
    if not app_secret:
        raise SystemExit("❌ 缺少应用凭证：assets/teams/*.json 里没有 appSecret。\n"
                         "   如果你是第一次用，请先跑：python3 scripts/guard.py --team 娜娜组 --workdir <工作目录>")

    token = tenant_token(app_id, app_secret)
    info = my_tenant(token)
    if info.get("tenant_key") and info["tenant_key"] != EXPECT_TENANT:
        raise SystemExit(f"❌ 应用所属企业是「{info.get('name')}」，与技能绑定企业不一致，拒绝取数。")

    # 已有记录但过期 → 静默续期（同一个人，不用重新登记）
    old = read_access()
    if old and old.get("tenant_key") == EXPECT_TENANT and old.get("employee", {}).get("account_masked"):
        who = old["employee"]
        acc = account or os.environ.get("CTR_EMPLOYEE_ACCOUNT")
        if not acc:
            # 用缓存里的账号重新核一遍（脱敏值不能反查，只能请用户重输）
            acc = input(f"验证已过期（每 {CACHE_DAYS} 天一次）。"
                        f"请再输一次公司手机号/邮箱：").strip()
        open_id, name, masked = lookup_employee(token, acc)
        if not open_id:
            raise SystemExit(f"❌ 通讯录里查不到「{masked}」，不是本公司员工或不在此应用可见范围。")
        _save(open_id, name, masked)
        return True

    account = (account or os.environ.get("CTR_EMPLOYEE_ACCOUNT") or "").strip()
    # 非交互传入（--account / 环境变量）：先直接查一次，通过就走
    if account:
        open_id, name, masked = lookup_employee(token, account)
        if open_id:
            _save(open_id, name, masked)
            print(f"✅ 验证通过：{name or '公司员工'}（{masked}），{CACHE_DAYS} 天内不用再验证。")
            return True
        print(f"❌ 通讯录里没有「{masked}」，改为手动输入。")

    attempts = 0
    while not account and attempts < 3:
        attempts += 1
        print("─" * 56)
        print("这是第一次在这台电脑上使用本技能，需要验证一次公司身份（之后 %d 天免验证）。" % CACHE_DAYS)
        print("请输入你的**公司手机号或公司邮箱**（只用于查公司通讯录，不做他用）：")
        try:
            account = input("> ").strip()
        except EOFError:
            account = ""
        if not account:
            print("   输入为空，请重试。")
            continue
        open_id, name, masked = lookup_employee(token, account)
        if open_id:
            _save(open_id, name, masked)
            print(f"✅ 验证通过：{name or '公司员工'}（{masked}），{CACHE_DAYS} 天内不用再验证。")
            return True
        print(f"❌ 通讯录里没有「{masked}」。请确认：① 是不是公司账号 ② 手机号是否前缀 1 后 11 位")
        account = ""  # 允许继续试
    raise SystemExit("❌ 未通过公司身份验证，已停止取数。\n"
                     "   如确认是本人员工，请联系管理员把你的账号加入飞书通讯录。")


def _save(open_id, name, masked):
    now = _now()
    write_access({
        "tenant_key": EXPECT_TENANT,
        "employee": {"name": name, "open_id": open_id, "account_masked": masked},
        "verified_at": now.isoformat(),
        "expires_at": (now + datetime.timedelta(days=CACHE_DAYS)).isoformat(),
        "method": "contact_lookup",
    })


def load_local_credentials():
    """从本机已生成的 assets/teams/*.json 取一套可用凭证（install.sh 注入的那份）。"""
    import glob
    for p in sorted(glob.glob(os.path.join(TEAM_DIR, "*.json"))):
        if p.endswith(".template.json"):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                auth = ((json.load(f).get("feishu") or {}).get("auth") or {})
        except Exception:
            continue
        if auth.get("appId") and auth.get("appSecret"):
            return auth["appId"], auth["appSecret"]
    return None, None


def migrate_feishu(workdir):
    """v5.04：把工作目录的 profile.json 从「共享应用身份」迁移到「个人授权」。

    · 备份原 profile 到 _not_deliverable/profile_api_<时间戳>.json（可回溯）；
    · feishu.via → "cli"；删除 feishu.auth（appId/appSecret 一并移除，本机不再存共享密钥）；
    · 团队映射（team / source / spreadsheetToken / sheets / items 等）**原样保留**；
    · 报告快照（snapshot/、feishu_raw.json、HTML）一律不动。
    返回 (migrated, 原因)。
    """
    import datetime as _dt
    p = os.path.join(workdir, "profile.json")
    if not os.path.exists(p):
        return False, f"{p} 不存在"
    with open(p, encoding="utf-8") as f:
        prof = json.load(f)
    fs = prof.get("feishu") or {}
    if not fs:
        return False, "该 profile 没有 feishu 段（本就是「无飞书」配置），无需迁移"
    auth = fs.get("auth") or {}
    if fs.get("via") != "api" and not auth.get("appSecret"):
        return False, "该 profile 已经是个人授权（via=cli 且无 appSecret），无需迁移"
    bdir = os.path.join(workdir, "_not_deliverable")
    os.makedirs(bdir, exist_ok=True)
    bak = os.path.join(bdir, "profile_api_" + _dt.datetime.now().strftime("%Y%m%d_%H%M") + ".json")
    with open(bak, "w", encoding="utf-8") as f:
        json.dump(prof, f, ensure_ascii=False, indent=1)
    os.chmod(bak, 0o600)
    fs["via"] = "cli"
    fs["_via_note"] = ("cli = 走本机 lark-cli 个人授权（v5.04 默认）；"
                       "旧共享应用身份已迁移移除（备份见 _not_deliverable/）")
    fs.pop("auth", None)
    prof["feishu"] = fs
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prof, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    print(f"✅ 已迁移到个人授权：{p}")
    print(f"   旧配置（含共享密钥）备份：{bak}（确认无需回退后可删除）")
    print("   团队映射与报告快照未动。下次取数若提示未授权，")
    print("   运行：python3 <skill>/scripts/feishu_auth.py login（浏览器点一下即可）")
    return True, ""


def deploy_team(workdir, team):
    """把内置模板复制成 <workdir>/profile.json（已存在则不覆盖）。

    v4.85：团队不在内置清单（还没接飞书表）时**不再报错**，而是降级成「无飞书」配置 ——
    出勤 / 群聊 / 小黑板 / 薄弱点照常，只有飞书来源的几块（作业提交、评级、成绩核对）为空。
    原因：其他团队的同事也要能用技能，不能因为「组名不认识」就整条链路停住。
    """
    os.makedirs(workdir, exist_ok=True)
    dst = os.path.join(workdir, "profile.json")
    if os.path.exists(dst):
        print(f"ℹ️ {dst} 已存在，保留原文件不覆盖。")
        return dst
    fname = TEAMS.get(team)
    src = os.path.join(TEAM_DIR, fname) if fname else None
    if src and os.path.exists(src):
        with open(src, encoding="utf-8") as f:
            cfg = json.load(f)
    elif fname:
        raise SystemExit(f"❌ 技能里缺少模板 {src}（安装包不完整，请重新安装技能）。")
    else:
        cfg = {
            "team": team,
            "_no_feishu_note": (
                "该团队尚未接入飞书表。作业提交 / 作业评级 / 成绩核对等飞书来源的模块会显示「无记录」，"
                "出勤、群聊、小黑板、薄弱点、分层（按剩余证据）照常。"
                "要补齐这几块，把该团队的飞书表地址交给管理员，加进技能后重装。"
            ),
            "feishu": None,
        }
        print(f"ℹ️ 「{team}」还没有接入飞书表 → 按「无飞书」模式配置。")
        print("   报告里作业提交 / 作业评级 / 成绩核对会是「无记录」，其余照常。")
        print("   要把这几块补齐，把该团队的飞书表地址发给管理员。")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    if fname:
        print(f"✅ 已按「{team}」布署配置 → {dst}")
    return dst


# ── 命令行 ──────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="团队配置布署 + 公司身份验证")
    ap.add_argument("--workdir", help="报告工作目录（如 ~/.workbuddy/reports/edu-张三）")
    ap.add_argument("--team", help="团队名：娜娜组 / 孙一文组（其他团队也可，未接飞书表时自动降级为「无飞书」）")
    ap.add_argument("--status", action="store_true", help="查看本机验证状态")
    ap.add_argument("--reset", action="store_true", help="清除本机验证记录")
    ap.add_argument("--account", help="公司手机号或邮箱（不给则交互式询问；也可用环境变量 CTR_EMPLOYEE_ACCOUNT）")
    ap.add_argument("--verify", action="store_true", help="只做公司身份验证，不部署任何配置（旧共享身份用）")
    ap.add_argument("--migrate-feishu", dest="migrate_feishu", metavar="WORKDIR",
                    help="v5.04：把该工作目录 profile.json 从共享应用身份迁移到 lark-cli 个人授权"
                         "（移除 appSecret 并备份，团队映射与快照不动）")
    args = ap.parse_args()

    if getattr(args, "migrate_feishu", None):
        migrated, why = migrate_feishu(os.path.expanduser(args.migrate_feishu))
        if not migrated:
            print(f"ℹ️ {why}")
            return

    if args.verify:
        aid, sec = load_local_credentials()
        if not sec:
            raise SystemExit("❌ 技能里还没有可用凭证（assets/teams/*.json）。\n"
                             "   请用《操作手册》里的完整安装命令重装一次（命令里带 --secret=…）。")
        ensure_access(aid, sec, account=args.account)
        return

    if args.status:
        rec = read_access()
        if not rec:
            print("未验证（第一次跑取数时会要求验证公司身份）")
        else:
            emp = rec.get("employee", {})
            print(f"已验证：{emp.get('name', '?')}（{emp.get('account_masked', '?')}）")
            print(f"  公司 tenant_key: {rec.get('tenant_key')}")
            print(f"  验证时间: {rec.get('verified_at', '')[:16]}")
            print(f"  到期时间: {rec.get('expires_at', '')[:16]}  → {'有效' if access_ok(rec) else '已过期'}")
        return

    if args.reset:
        if os.path.exists(ACCESS_PATH):
            os.remove(ACCESS_PATH)
            print("✅ 已清除本机验证记录，下次取数会重新验证。")
        else:
            print("本机本来就没有验证记录。")
        return

    if args.workdir and args.team:
        dst = deploy_team(os.path.expanduser(args.workdir), args.team)
        # v5.04：个人授权（via=cli，无 appSecret）是默认路径 —— 不做手机号验证，
        # 只提示授权状态；旧共享身份（有 appSecret）才走 ensure_access。
        try:
            with open(dst, encoding="utf-8") as f:
                fs = (json.load(f).get("feishu") or {})
        except Exception as e:
            raise SystemExit(f"❌ 读取 {dst} 失败：{e}")
        if fs.get("auth", {}).get("appSecret"):
            ensure_access(fs["auth"].get("appId"), fs["auth"]["appSecret"], account=args.account)
            return
        if not fs:
            print("ℹ️ 本团队未接飞书表（feishu=null）→ 无需飞书授权。")
            return
        print("ℹ️ 本配置走 lark-cli 个人授权（不含任何共享密钥）。")
        try:
            import feishu_auth
            ok, _ = feishu_auth.check_status(verbose=False)
        except ImportError:
            print("   （缺 feishu_auth.py，跳过授权检查——请重新安装技能）")
            return
        if ok:
            print("✅ 飞书个人授权已就绪，取数将复用本机登录态。")
        else:
            print("   尚未授权：首次取数时会给出引导，或直接运行：")
            print("     python3 <skill>/scripts/feishu_auth.py login")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
