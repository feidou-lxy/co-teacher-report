"""联席学情报告技能 · 公共工具

提供：token 读取、HTTP 调用封装、姓名归一化、课程名解析。
所有脚本统一把中间文件读写在 --workdir 下（默认当前目录）。
"""
import json
import os
import sys
import urllib.request
import urllib.parse

BASE_HOST = "https://stadminv2.xqmsx.cn/api"


def workdir(args):
    return args.workdir or "."


def load_token(wd):
    p = os.path.join(wd, "token.txt")
    if not os.path.exists(p):
        raise SystemExit(f"未找到 {p}，请先登录后台并把 x-token 写入该文件（见 SKILL.md 登录步骤）。")
    return open(p, "r", encoding="utf-8").read().strip()


def api(method, path, token, body=None, timeout=60):
    url = BASE_HOST + path
    headers = {"x-token": token, "Content-Type": "application/json"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path, token, timeout=60):
    return api("GET", path, token, timeout=timeout)


def post(path, body, token, timeout=60):
    return api("POST", path, token, body, timeout=timeout)


ROLE_LABEL = {"co": "联席", "edu": "教务"}
SCOPE_FIELD = {"co": "coTeacherName", "edu": "classTeacherName"}


def resolve_scope(args):
    """解析查询角色：--co <联席名> 或 --edu <教务名>，二者只能选一。

    返回 (role, name)，role ∈ {'co','edu'}。
    后台把「教务」存在 classTeacherName 字段（响应时效页的「教务」列即此）。
    """
    co = (getattr(args, "co", None) or "").strip()
    edu = (getattr(args, "edu", None) or "").strip()
    if co and edu:
        raise SystemExit("--co 与 --edu 只能二选一（联席视角 / 教务视角）。")
    if edu:
        return "edu", edu
    if co:
        return "co", co
    raise SystemExit("必须指定 --co <联席名>（联席视角）或 --edu <教务名>（教务视角）。")


def scope_field(role):
    """该角色在学生花名册/出勤表里对应的过滤字段名。"""
    return SCOPE_FIELD.get(role, "coTeacherName")


def norm_name(s):
    """姓名归一化：去尾部数字后缀（如「曾忻怡3662」→「曾忻怡」），去空格。"""
    import re
    return re.sub(r"\s*\d+$", "", str(s or "")).strip()


def clean_course_name(product_name):
    """把后台 product 名收敛成课程标签，例如
    '2026Q-SX-高二数学985层进化班六D谢胡' -> '高二数学·985层·六D班（谢胡老师）'
    """
    import re
    s = str(product_name or "")
    s = re.sub(r"^2026Q-SX-", "", s)
    s = s.replace("进化班", "")
    m = re.match(r"^(高二数学)(清北层|985层|211层|[\u4e00-\u9fa5]+层?)(.+?)(邵|谢胡|胡[\u4e00-\u9fa5]*)$", s)
    if m:
        layer, cls, teacher = m.group(2), m.group(3), m.group(4)
        return f"高二数学·{layer}·{cls}班（{teacher}老师）"
    return s or "未命名课程"


def ensure_openpyxl():
    """依赖自检：若当前解释器没有 openpyxl，尝试用同解释器自动 pip 安装。

    这样同事解压技能后用任意受管 Python 起手都能自愈，不必手动建 venv。
    """
    try:
        import openpyxl  # noqa: F401
        return
    except ImportError:
        import subprocess
        print("⏳ 未检测到 openpyxl，正在用当前 Python 自动安装（仅首次）…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "openpyxl>=3.1"])
        print("✅ openpyxl 安装完成。")
