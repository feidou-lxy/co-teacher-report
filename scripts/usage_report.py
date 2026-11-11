"""Skill 使用统计上报（每次执行最多一次）。

规范（改动需同步 SKILL.md「使用统计」章节）：

- installation_id 持久化在平台约定路径（见 id_path()），首次运行生成标准
  UUID v4（36 字符含连字符），**原子创建**（O_CREAT|O_EXCL）避免并发写坏；
  写入后 fsync 并回读校验，确认「已成功保存」才上报。
  后续运行只读复用，**不重新生成、不覆盖已有 ID**。
- 上报：HTTPS POST https://stadminv2.xqmsx.cn/api/ai/reportSkillUsage
  请求头 Content-Type: application/json + x-token（与本站其他接口一致）。
  请求体固定四字段（skill / version / installation_id / event），仅
  installation_id 变化，不发送任何其他数据。
- 每次执行最多发送一次；总超时 3 秒；不重试；不跟随重定向；正常校验证书。
- **任何失败都不影响主任务**：ID 读写失败、内容无效、token 缺失、网络异常，
  一律只打印一行说明并返回 False，主流程继续。

调用方：report.py（产出报告时）与 run_all.py（端到端链路收尾）。
run_all 会在启动时认领上报权（见 claim()），避免子进程重复上报。
"""
import http.client
import json
import os
import re
import ssl
import sys
import time
import uuid

SKILL_NAME = "co-teacher-report"
REPORT_VERSION = "1.0.0"          # 上报协议版本（固定值，非技能内部版本号）
EVENT = "run"
HOST = "stadminv2.xqmsx.cn"
PATH = "/api/ai/reportSkillUsage"
TOTAL_TIMEOUT = 3.0               # 秒（整体预算，不重试）

# 跨进程去重：父流程认领后，子进程继承该环境变量即跳过（保证一次执行只报一次）
_ENV_DONE = "CO_TEACHER_REPORT_USAGE_DONE"

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)


def _say(msg):
    print(f"   [使用统计] {msg}")


def workdir_from_argv(argv=None):
    """从命令行解析上报用的工作目录（v5.12 修复「跑成功却跳过上报」）。

    以前只认显式 --workdir，没传就取 CWD —— 而 run_all 支持 --edu/--co/--course
    自动定位报告目录（workdir.resolve），token.txt 在报告目录里、CWD 里没有，
    于是日志出现「跳过上报（./token.txt 无有效 x-token）」，统计就漏了一笔
    （张豫报告实测，同事反馈 2026-11-11）。现在按序解析：
      ① --workdir（展开 ~ 与相对路径；支持 --workdir= 两种写法）
      ② --edu/--co/--course 经 workdir.resolve 定位（与 run_all 同口径，不创建目录）
      ③ 都没有 → 返回 None（调用方按 "." 处理，token 兜底逻辑再接一层）
    """
    a = list(sys.argv[1:] if argv is None else argv)

    def _pick(flag):
        for i, x in enumerate(a):
            if x == flag and i + 1 < len(a):
                return a[i + 1]
            if x.startswith(flag + "="):
                return x.split("=", 1)[1]
        return None

    w = _pick("--workdir")
    if w:
        return os.path.abspath(os.path.expanduser(w))
    try:
        import workdir as _wdmod      # 同目录；找不到时静默退回 None
        p = _wdmod.resolve(edu=_pick("--edu"), co=_pick("--co"),
                           course=_pick("--course"), create=False)
        if p:
            return p
    except Exception:
        pass
    return None


# ------------------------------------------------------------ installation_id
def id_dir():
    """平台约定的 installation_id 目录。"""
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/skill-usage")
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return os.path.join(base, "skill-usage")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "skill-usage")


def id_path():
    return os.path.join(id_dir(), "installation_id")


def _read_id(path):
    """返回 (id, 原因)。读不到或内容非法时 id 为 None，原因为中文说明。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = (f.read() or "").strip()
    except FileNotFoundError:
        return None, "文件不存在"
    except Exception as e:
        return None, f"读取失败（{type(e).__name__}: {e}）"
    if not s:
        return None, "文件内容为空"
    if not _UUID4_RE.match(s):
        return None, f"内容不是合法的 UUID v4（{s[:48]!r}）"
    return s, ""


def load_or_create_id():
    """读取已有 ID；首次运行时原子创建。返回 (id, 原因)。"""
    path = id_path()
    iid, why = _read_id(path)
    if iid:
        return iid, ""

    # 已存在但内容无效 → 按规范「不覆盖已有 ID」，跳过后由调用方说明原因
    if os.path.exists(path):
        return None, why

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception as e:
        return None, f"目录创建失败（{type(e).__name__}: {e}）"

    new_id = str(uuid.uuid4())
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # 并发：另一个进程刚建好，读它的（给写入留一点落盘时间）
        for _ in range(4):
            time.sleep(0.15)
            iid, why = _read_id(path)
            if iid:
                return iid, ""
        return None, f"并发创建后仍无法读取（{why}）"
    except Exception as e:
        return None, f"文件创建失败（{type(e).__name__}: {e}）"

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_id + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        # 自己刚创建的残件（空/半截）会永久挡住后续上报，尽力清掉
        try:
            os.unlink(path)
        except Exception:
            pass
        return None, f"写入失败（{type(e).__name__}: {e}）"

    # 规范要求：必须成功保存后再读取并上报
    iid, why = _read_id(path)
    if not iid:
        return None, f"写入后回读失败（{why}）"
    return iid, ""


# ------------------------------------------------------------------- token
def _read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _clean(raw):
    try:
        import common              # 同目录，复用统一的 token 清洗
        return common.clean_token(raw) or ""
    except Exception:
        return (raw or "").strip()


def _newest_token_in_reports():
    """兜底：报告根目录（workdir.home()）下找最近修改的 token.txt。

    x-token 与本站所有接口是同一枚（只看有效与否，不绑定目录），上报只要求
    「有一枚可能有效的 token」；token 约 24h 过期，最新的最可能有效。
    返回路径或 None。任何异常都吞掉（兜底不能影响主任务）。
    """
    try:
        import workdir as _wdmod
        root = _wdmod.home()
        best, best_t = None, -1.0
        for name in os.listdir(root):
            tp = os.path.join(root, name, "token.txt")
            if os.path.isfile(tp):
                t = os.path.getmtime(tp)
                if t > best_t:
                    best, best_t = tp, t
        return best
    except Exception:
        return None


def _token(wd):
    """读 <workdir>/token.txt（与 skill 其他接口同一枚 x-token）。

    v5.12：读不到时兜底扫报告根目录最近修改的一枚（统计口径不绑定目录）。
    返回 (token, 尝试过的路径列表)。
    """
    tried = []
    p = os.path.join(wd or ".", "token.txt")
    tried.append(p)
    raw = _read_file(p)
    if raw is not None:
        return _clean(raw), tried
    alt = _newest_token_in_reports()
    if alt and os.path.abspath(alt) != os.path.abspath(p):
        tried.append(alt)
        raw = _read_file(alt)
        if raw is not None:
            return _clean(raw), tried
    return "", tried


# -------------------------------------------------------------------- HTTP
def _post(payload, token):
    """HTTPS POST，返回 (status, body)。不跟随重定向（http.client 默认不跟）。"""
    deadline = time.time() + TOTAL_TIMEOUT
    ctx = ssl.create_default_context()     # 正常验证 HTTPS 证书
    conn = http.client.HTTPSConnection(HOST, 443, timeout=TOTAL_TIMEOUT, context=ctx)
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn.request("POST", PATH, body=body, headers={
            "Content-Type": "application/json",
            "x-token": token,
            "Connection": "close",
        })
        if conn.sock is not None:          # 把剩余预算给读响应，保证整体 ~3s
            conn.sock.settimeout(max(0.2, deadline - time.time()))
        r = conn.getresponse()
        return r.status, r.read(512)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 对外入口
def claim():
    """由 run_all 在启动时调用：认领本次执行的上报权，子进程继承后自动跳过。"""
    os.environ[_ENV_DONE] = "1"


def report_once(wd=".", force=False, event=None):
    """上报一次使用记录。返回是否发送成功；任何异常都被吞掉。

    force=True 供「已认领」的父流程在收尾时真正发送（run_all 用）。

    `event`（v4.66）：区分「跑成功」与「跑失败」——
      · "run"        —— 报告已生成（默认）
      · "run_failed" —— 链路中断 / 未产出报告
    ⚠️ 旧版固定写 "run"，于是「中途失败」和「成功出报告」在统计里长得一模一样，
       无法回答「到底成功生成了多少份可用报告」。后端若不认这个取值，上报失败会被
       本函数吞掉，不影响主任务；**本地另有 `run_log.jsonl` 兜底记账**（见 run_all.py）。
    """
    if not force and os.environ.get(_ENV_DONE) == "1":
        return False
    os.environ[_ENV_DONE] = "1"

    try:
        iid, why = load_or_create_id()
        if not iid:
            _say(f"跳过上报（installation_id {why}）")
            return False

        token, tried = _token(wd)
        if not token:
            _say(f"跳过上报（试过 { '、'.join(tried) } 都无有效 x-token）")
            return False

        payload = {
            "skill": SKILL_NAME,
            "version": REPORT_VERSION,
            "installation_id": iid,
            "event": (event or EVENT),
        }
        try:
            status, _ = _post(payload, token)
        except Exception as e:
            _say(f"上报失败（{type(e).__name__}: {e}），已忽略，不影响主任务")
            return False

        if 200 <= status < 300:
            if os.environ.get("SKILL_USAGE_DEBUG") == "1":
                _say(f"已上报（installation_id={iid[:8]}…）")
            return True
        _say(f"上报返回 HTTP {status}，已忽略，不影响主任务")
        return False
    except Exception as e:                 # 兜底：绝不影响主任务
        _say(f"上报异常（{type(e).__name__}: {e}），已忽略，不影响主任务")
        return False
