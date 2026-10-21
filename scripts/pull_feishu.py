"""飞书电子表格取数（v4.19）：把团队自维护的飞书表拉成原始 JSON。

为什么需要它：8 个团队共用同一套分析内核，但各自的飞书表结构不同。
本脚本只负责「把表原样取回来」，**不做任何解析**——解析与映射在 adapt_feishu.py。

两个通道（同一份 profile.json，只换凭证）：
    --via cli   走本机 lark-cli（用户身份；本机调试 / 摸表结构用）
    --via api   直连飞书开放平台（tenant_access_token；**生产 8 团队走这条**）

输入：<workdir>/profile.json 的 feishu 段
输出：<workdir>/feishu_raw.json

⚠️ 飞书三个实测坑（已处理）：
  1. **wiki 链接不能直接用**：/wiki/{node_token} 里的 token 是节点 ID，必须先换 obj_token；
  2. **range 必须用 sheetId**（不能用中文 sheet 名，改名/特殊字符会挂）；
  3. **日期默认是 1899-12-30 起的浮点数** → api 通道显式带 dateTimeRenderOption=FormattedString；
     cli 通道用 FormattedValue（lark-cli 无该参数，日期列可能仍是数字，不影响学情字段）。

⚠️ 第四个坑（最坑，本脚本显式拦）：**「调用成功但返回数据为空」**。
  应用未被加为表格协作者时，接口不报错、静默返回空数组 → 会被误读成「这个团队没数据」。
  故 `list_sheets` / `read` 拿到空结果一律**显式报错**，绝不静默兜底。
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, sys.path[0])
import common

BASE = "https://open.feishu.cn/open-apis"
TZ = "+08:00"


def colname(n):
    """1 -> A, 27 -> AA, 139 -> EI"""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class FeishuError(RuntimeError):
    pass


def _trim(rows):
    """裁掉尾部整行空 + 行尾空单元格，减小落盘体积。"""
    out = []
    for r in rows or []:
        r = ["" if v is None else v for v in r]
        while r and r[-1] == "":
            r.pop()
        out.append(r)
    while out and not any(out[-1]):
        out.pop()
    return out


RETRYABLE_HINTS = ("time out", "timeout", "data not ready", "90235", "无有效输出",
                   "无输出", "too many request", "429", "500", "502", "503", "504",
                   "900015215", "server_error", "while queued")


def _retryable(err):
    """飞书侧偶发错误（超时 / 数据未就绪 / 排队超时 / 限流 / 5xx）→ 值得重试。
    实测 `90235 Data not ready` 与 `900015215 ai tool timeout while queued` 都是**随机的**：
    同尺寸的请求有时成功有时失败；而**请求越大越容易排队超时**（小范围基本必成）。"""
    s = str(err).lower()
    return any(h.lower() in s for h in RETRYABLE_HINTS)


def _norm(vals, width):
    """补齐列宽。⚠️ 某块右侧若全空，飞书会裁掉尾部空列，
    不平铺就会让后面所有列整体左移（静默错位，最难查）。"""
    out = []
    for row in vals or []:
        row = ["" if v is None else v for v in row]
        if len(row) < width:
            row += [""] * (width - len(row))
        out.append(row[:width])
    return out


def _read_block(ch, tk, sid, c0, c1, r0, r1, tries, col_chunk, depth=0):
    """读一个矩形块；整段失败则**按列细分**再试（并递归）。

    ⚠️ 飞书繁忙时会返回 `server time out` / `90235` / `900015215 排队超时`，
       而**请求越小越容易过**（实测同一时刻：整表必挂、20 列必成）。
       所以这里做「整段 → 减半 → 再减半」的自适应降级，而不是一上来就切成小块
       （正常时段整段 1 次请求 1 秒，切太碎反而慢）。
    """
    width = c1 - c0 + 1
    rng = f"{colname(c0 + 1)}{r0 + 1}:{colname(c1 + 1)}{r1 + 1}"
    last_err = None
    for attempt in range(tries):
        try:
            return _norm(ch.read(tk, sid, rng), width)
        except FeishuError as e:
            last_err = e
            if not _retryable(e):
                break
            time.sleep(2.0 * (attempt + 1))
    if width > col_chunk and depth < 4:
        mid = c0 + col_chunk - 1
        left = _read_block(ch, tk, sid, c0, mid, r0, r1, tries, col_chunk, depth + 1)
        right = _read_block(ch, tk, sid, mid + 1, c1, r0, r1, tries, col_chunk, depth + 1)
        n = max(len(left), len(right))
        lw, rw = mid - c0 + 1, c1 - mid
        left = left + [[""] * lw] * (n - len(left))
        right = right + [[""] * rw] * (n - len(right))
        return [a + b for a, b in zip(left, right)]
    raise FeishuError(f"读取 {sid}!{rng} 失败：{last_err}")


def read_wide(ch, tk, sheet_id, ncol, nrow, col_chunk=20, row_chunk=60,
              tries=4, gap=0.15):
    """读整表。

    ⭐ **按行分块，而不是按列**（v4.20 实测结论）：
       飞书的读取开销**对行数远比列数敏感** —— 同一时刻
       `A1:HJ20`（218 列 × 20 行）只要 **1 秒**，而
       `A1:T204`（20 列 × 204 行）要 **7 秒**。
       行块既快又少（204 行 ÷ 60 = 4 块），且块内失败会自动细分列。
    ⚠️ 但**必须保留分块能力**：飞书某些时段会对所有较大的请求排队超时
       （实测 20:00 前后连 20 列 × 204 行都连续失败），那时整表读必挂。
    """
    ncol, nrow = max(ncol, 1), max(nrow, 1)
    grid = []
    for r0 in range(0, nrow, row_chunk):
        r1 = min(r0 + row_chunk - 1, nrow - 1)
        block = _read_block(ch, tk, sheet_id, 0, ncol - 1, r0, r1, tries, col_chunk)
        grid.extend(block)
        if gap and r1 < nrow - 1:
            time.sleep(gap)
    return grid


# ================================================================ 通道
class CliChannel:
    """走本机 lark-cli。本机调试用；生产团队机器上不一定有。"""

    def __init__(self, identity="user", path=None):
        self.identity = identity
        self.path = (path or os.environ.get("LARK_CLI")
                     or shutil.which("lark-cli"))
        if not self.path:
            raise FeishuError(
                "找不到 lark-cli。请用 --lark-cli 指定路径，或改用 --via api 走应用凭证。")
        # ⚠️ 必须禁代理：本机 Clash 代理会让请求打不通（实测）
        self.env = dict(os.environ, LARK_CLI_NO_PROXY="1")

    def _run(self, args, what):
        try:
            p = subprocess.run([self.path] + args, capture_output=True,
                               text=True, env=self.env, timeout=180)
        except subprocess.TimeoutExpired:
            raise FeishuError(f"{what}：lark-cli 超时（180s）")
        # ⚠️ stdout 优先但**不能只看 stdout**：实测 `90235 Data not ready` 这类错误
        #    会整个落在 **stderr**，此时 stdout 为空 —— 只读 stdout 会报成
        #    「lark-cli 无输出」，真错因被吞掉、也无法按错误类型重试。
        #    ⚠️ 但 stderr 里也可能混着代理警告（非 JSON）→ 用「截取首个 { 到末个 }」兜底。
        j = None
        for s in ((p.stdout or "").strip(), (p.stderr or "").strip()):
            if not s:
                continue
            for cand in (s, s[s.find("{"):s.rfind("}") + 1] if "{" in s and "}" in s else ""):
                if not cand:
                    continue
                try:
                    j = json.loads(cand)
                    break
                except ValueError:
                    continue
            if j is not None:
                break
        if j is None:
            raise FeishuError(
                f"{what}：lark-cli 无有效输出\nstdout: {(p.stdout or '')[:200]}\n"
                f"stderr: {(p.stderr or '')[:300]}")
        # ⚠️ lark-cli 的失败形态有两种：`ok:false + error{}` 与 `code!=0`。
        #    只判 code 会把 `ok:false` 当成功 → 后续拿到空 values。
        if j.get("ok") is False:
            err = j.get("error") or {}
            msg = err.get("message") or err.get("msg") or j.get("msg") or json.dumps(err)[:200]
            raise FeishuError(f"{what}：{err.get('type') or 'error'} "
                              f"{('[%s] ' % err['code']) if err.get('code') else ''}{msg}")
        if j.get("code") not in (0, None):
            raise FeishuError(f"{what}：飞书返回 code={j.get('code')} msg={j.get('msg')}")
        return j

    def resolve_wiki(self, node_token):
        j = self._run(["api", "GET", "/open-apis/wiki/v2/spaces/get_node",
                       "--params", json.dumps({"token": node_token}),
                       "--as", self.identity], "解析 wiki 节点")
        node = (j.get("data") or {}).get("node") or {}
        tok = node.get("obj_token")
        if not tok:
            raise FeishuError(f"wiki 节点未返回 obj_token：{json.dumps(node)[:300]}")
        return tok, node.get("title") or "", node.get("obj_type") or ""

    def list_sheets(self, tk):
        j = self._run(["sheets", "+info", "--spreadsheet-token", tk,
                       "--as", self.identity], "列出 sheet")
        raw = (((j.get("data") or {}).get("sheets") or {}).get("sheets")) or []
        if not raw:
            raise FeishuError(
                "列出 sheet 返回空 —— 可能是应用未被加为表格协作者（飞书会静默返回空）。")
        return [{"sheetId": s.get("sheet_id"), "title": s.get("title") or "",
                 "rows": (s.get("grid_properties") or {}).get("row_count"),
                 "cols": (s.get("grid_properties") or {}).get("column_count"),
                 "hidden": bool(s.get("hidden"))} for s in raw]

    def read(self, tk, sheet_id, rng):
        j = self._run(["sheets", "+read", "--spreadsheet-token", tk,
                       "--sheet-id", sheet_id, "--range", rng,
                       "--value-render-option", "FormattedValue",
                       "--as", self.identity], f"读取 {sheet_id}!{rng}")
        vals = ((j.get("data") or {}).get("valueRange") or {}).get("values")
        if vals is None:
            raise FeishuError(f"读取 {sheet_id}!{rng} 未返回 values 字段")
        return vals


class ApiChannel:
    """直连飞书开放平台。**生产路径** —— 8 个团队各拿一份应用凭证即可。"""

    def __init__(self, app_id, app_secret):
        if not app_id or not app_secret:
            raise FeishuError("via=api 需要 appId + appSecret（profile.feishu.auth）")
        self.app_id, self.app_secret = app_id, app_secret
        self._tok, self._exp = None, 0

    def _token(self):
        now = datetime.datetime.now().timestamp()
        if self._tok and now < self._exp - 120:
            return self._tok
        body = json.dumps({"app_id": self.app_id,
                           "app_secret": self.app_secret}).encode()
        req = urllib.request.Request(
            BASE + "/auth/v3/tenant_access_token/internal", data=body,
            headers={"Content-Type": "application/json; charset=utf-8"})
        j = json.loads(self._open(req, "换取 tenant_access_token"))
        if j.get("code") != 0:
            raise FeishuError(f"换 token 失败：code={j.get('code')} msg={j.get('msg')}")
        self._tok = j["tenant_access_token"]
        self._exp = now + int(j.get("expire", 7200))
        return self._tok

    def _open(self, req, what):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise FeishuError(f"{what}：HTTP {e.code} {(e.read() or b'').decode('utf-8', 'ignore')[:300]}")
        except Exception as e:
            raise FeishuError(f"{what}：{e}")

    def _get(self, path, params=None):
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + self._token(),
            "Content-Type": "application/json; charset=utf-8"})
        j = json.loads(self._open(req, path))
        if j.get("code") != 0:
            raise FeishuError(f"{path}：code={j.get('code')} msg={j.get('msg')}")
        return j

    def resolve_wiki(self, node_token):
        j = self._get("/wiki/v2/spaces/get_node", {"token": node_token})
        node = (j.get("data") or {}).get("node") or {}
        tok = node.get("obj_token")
        if not tok:
            raise FeishuError(f"wiki 节点未返回 obj_token：{json.dumps(node)[:300]}")
        return tok, node.get("title") or "", node.get("obj_type") or ""

    def list_sheets(self, tk):
        j = self._get(f"/sheets/v3/spreadsheets/{tk}/sheets/query")
        raw = (j.get("data") or {}).get("sheets") or []
        if not raw:
            raise FeishuError(
                "列出 sheet 返回空 —— 可能是应用未被加为表格协作者（飞书会静默返回空），"
                "或缺少 sheets:spreadsheet:readonly 权限。")
        return [{"sheetId": s.get("sheet_id"), "title": s.get("title") or "",
                 "rows": (s.get("grid_properties") or {}).get("row_count"),
                 "cols": (s.get("grid_properties") or {}).get("column_count"),
                 "hidden": bool(s.get("hidden"))} for s in raw]

    def read(self, tk, sheet_id, rng):
        j = self._get(f"/sheets/v2/spreadsheets/{tk}/values/{sheet_id}!{rng}",
                      {"valueRenderOption": "FormattedValue",
                       "dateTimeRenderOption": "FormattedString"})
        vals = ((j.get("data") or {}).get("valueRange") or {}).get("values")
        if vals is None:
            raise FeishuError(f"读取 {sheet_id}!{rng} 未返回 values 字段")
        return vals


# ================================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--profile", default=None, help="默认 <workdir>/profile.json")
    ap.add_argument("--out", default=None, help="默认 <workdir>/feishu_raw.json")
    ap.add_argument("--via", default=None, choices=["cli", "api"], help="覆盖 profile 的通道")
    ap.add_argument("--as", dest="identity", default=None, help="cli 通道身份 user|bot")
    ap.add_argument("--lark-cli", default=None, help="lark-cli 可执行文件路径")
    ap.add_argument("--sheet", action="append", default=None,
                    help="只拉指定 sheetId（可重复；调试用，覆盖 profile 的 include）")
    ap.add_argument("--with-excluded", action="store_true", help="连 exclude 的 sheet 一起拉")
    ap.add_argument("--row-chunk", type=int, default=60,
                    help="每个请求读多少行（默认 60；⭐ 飞书对行数更敏感，行块比列块快得多）")
    ap.add_argument("--col-chunk", type=int, default=20,
                    help="请求被打回时**细分到多少列**再试（默认 20；正常时段用不到）")
    ap.add_argument("--gap", type=float, default=0.15,
                    help="行块之间的间隔秒数（默认 0.15）")
    ap.add_argument("--resume", action="store_true",
                    help="续传：已有 feishu_raw.json 里**成功拉到**的 sheet 不重拉。"
                         "⚠️ 表已更新时不要用（会拿到旧数据）。")
    args = ap.parse_args()

    wd = common.workdir(args)
    ppath = args.profile or os.path.join(wd, "profile.json")
    if not os.path.exists(ppath):
        raise SystemExit(f"❌ 找不到 {ppath}\n   先复制一份 profile.json 模板到工作目录。")
    with open(ppath, encoding="utf-8") as f:
        prof = json.load(f)
    fs = prof.get("feishu") or {}
    if not fs:
        raise SystemExit("❌ profile.json 里没有 feishu 段。")

    via = args.via or fs.get("via") or "cli"
    if via == "cli":
        ch = CliChannel(args.identity or fs.get("identity") or "user", args.lark_cli)
    else:
        auth = fs.get("auth") or {}
        ch = ApiChannel(os.environ.get("FEISHU_APP_ID") or auth.get("appId"),
                        os.environ.get("FEISHU_APP_SECRET") or auth.get("appSecret"))

    # ---- 1. 解析表格 token（wiki 链接不能直接用）
    tk = fs.get("spreadsheetToken") or ""
    wiki_tok, wiki_title, wiki_type = "", "", ""
    if not tk:
        src = fs.get("source") or ""
        if (fs.get("sourceType") or "wiki") == "wiki":
            print(f"→ 解析 wiki 节点 {src} …")
            tk, wiki_title, wiki_type = ch.resolve_wiki(src)
            print(f"  ✅ obj_type={wiki_type} title={wiki_title!r} obj_token={tk}")
        else:
            tk = src
    else:
        src = fs.get("source") or ""
        if src and (fs.get("sourceType") or "") == "wiki":
            _t, wiki_title, wiki_type = None, "", ""
            try:
                _x, wiki_title, wiki_type = ch.resolve_wiki(src)
            except FeishuError:
                pass

    if not tk:
        raise SystemExit("❌ 无法确定 spreadsheetToken（profile.feishu.source / spreadsheetToken 均为空）")

    # ---- 2. 列 sheet
    print(f"→ 列出工作表（{via} 通道）…")
    all_sheets = ch.list_sheets(tk)
    print(f"  共 {len(all_sheets)} 个 sheet")

    inc = args.sheet or (fs.get("sheets") or {}).get("include") or []
    exc = {} if args.with_excluded else ((fs.get("sheets") or {}).get("exclude") or {})
    picked, skipped = [], []
    for s in all_sheets:
        if args.sheet:
            (picked if s["sheetId"] in inc else skipped).append(s)
        elif s["sheetId"] in inc:
            picked.append(s)
        else:
            skipped.append(s)
    if not picked:
        raise SystemExit(
            f"❌ 没有可拉的 sheet。include={inc}\n   实际存在的 sheetId："
            f"{[s['sheetId'] for s in all_sheets]}")

    # ---- 3. 逐个读取
    opath = args.out or os.path.join(wd, "feishu_raw.json")
    prev = {}
    if args.resume and os.path.exists(opath):
        try:
            with open(opath, encoding="utf-8") as f:
                _p = json.load(f)
            if _p.get("spreadsheetToken") == tk:
                prev = {s["sheetId"]: s for s in (_p.get("sheets") or [])}
                if prev:
                    print(f"→ 续传：沿用已有 {len(prev)} 个 sheet 的数据")
            else:
                print("⚠️ 已有 feishu_raw.json 是**另一个表格** → 忽略续传")
        except Exception as e:
            print(f"⚠️ 续传读取失败，改为全量拉取：{e}")

    def _build(sheets, done=False):
        return {
            "generatedAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + TZ,
            "via": via,
            "team": prof.get("team"),
            "spreadsheetToken": tk,
            "wikiSource": fs.get("source") if (fs.get("sourceType") == "wiki") else None,
            "wikiTitle": wiki_title,
            "sheets": sheets,
            "skipped": [{"sheetId": s["sheetId"], "title": s["title"],
                         "reason": exc.get(s["sheetId"], "不在 include 列表")} for s in skipped],
            "items": fs.get("items") or {},
            "profileTeam": prof.get("team"),
            "partial": (not done),          # ⚠️ 半成品标记：拉取中途落盘时为 true
            "plannedSheets": len(picked),
        }

    def _dump(sheets, done=False):
        # ⚠️ 每拉完一个 sheet 就落盘一次：单张表要读十几分钟，
        #    中途一次网络抖动就全废（实测第 9 张挂在 90235，前面 8 张全丢）。
        if args.resume:
            with open(opath, "w", encoding="utf-8") as f:
                json.dump(_build(sheets, done), f, ensure_ascii=False, indent=1)

    out_sheets = []
    # ⭐ v4.20：`readCols` 可截断读取宽度。飞书大范围读取会**排队超时**，
    #    而很多表在课次区之后还有大段**用不到**的成绩区 / 公式辅助列
    #    （实测娜娜团队 218~254 列里，学情只用前 ~150 列）→ 截断能直接减掉三成请求。
    _max_cols = fs.get("readCols")
    try:
        _max_cols = int(_max_cols) if _max_cols else 0
    except (TypeError, ValueError):
        _max_cols = 0
    if _max_cols:
        print(f"→ 读取宽度上限 readCols={_max_cols}（更宽的部分不读）")

    for s in picked:
        cols = s["cols"] or 26
        if _max_cols:
            cols = min(cols, _max_cols)
        rows = s["rows"] or 100
        if s["sheetId"] in prev:
            keep = prev[s["sheetId"]]
            print(f"→ 续用 {s['sheetId']:8s} {s['title'][:28]:30s} "
                  f"{keep.get('readRows') or len(keep.get('values') or [])} 行（上次已拉）")
            out_sheets.append(keep)
            continue
        print(f"→ 读取 {s['sheetId']:8s} {s['title'][:28]:30s} {cols}×{rows} …", end="", flush=True)
        vals = read_wide(ch, tk, s["sheetId"], cols, rows,
                         col_chunk=args.col_chunk, row_chunk=args.row_chunk, gap=args.gap)
        vals = _trim(vals)
        nonempty = sum(1 for r in vals for v in r if v != "")
        print(f" {len(vals)} 行 / {nonempty} 个非空格")
        out_sheets.append({
            "sheetId": s["sheetId"], "title": s["title"],
            "gridRows": rows, "gridCols": cols,
            "hidden": s.get("hidden", False),
            "readRows": len(vals),
            "values": vals,
        })
        _dump(out_sheets, done=False)

    raw = _build(out_sheets, done=True)

    # opath 已在读取前定义（供 --resume 使用）
    with open(opath, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)

    print(f"\n✅ 原始数据已写入 {opath}")
    print(f"   纳入 {len(out_sheets)} 个 sheet ｜ 跳过 {len(skipped)} 个")
    for s in out_sheets:
        n = len(s["values"])
        print(f"   ✔ {s['sheetId']:8s} {s['title'][:30]:32s} {n} 行")
    print(f"\n下一步：python scripts/adapt_feishu.py --workdir {wd}")


if __name__ == "__main__":
    main()
