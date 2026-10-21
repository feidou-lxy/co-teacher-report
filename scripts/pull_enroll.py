#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pull_enroll.py — 拉「学生在读科目」并生成 snapshot/enroll.json（v4.39 · 教务视角）。

背景（用户 2026-10-21）：
  教务视角要回答「这个学生报了哪些科目」——数据取系统【在读】状态下的【科目】。
  后台「我的学生」花名册（getCoTeacherStudents）**只返回本角色名下的那门课**，
  教务只看得到自己管的科目（罗绂燕 243 人全是数学）。
  真正的跨科目名单在企微群统计接口 `wxworkSessionStat/getList` 里：
  一行 = 一个「学生 × 课程群」，带 `subjectName` / `productName`，覆盖全机构全部学科。

「在读」怎么判：
  接口不返回状态字段，但已关/已结课的课程 `productName` 带 `【已关班】` 这类前缀
  （实测 3781 行里 2 条）→ 有「已关班 / 已结课 / 已退费 / 退课 / 停课」前缀的**排除**，
  其余视为在本期（year=4 & term=3）在读。⚠️ 这是**代理口径**，不是后台原始状态位；
  研发给出【在读】状态字段后，直接在本文件 `courses[].active` 上替换即可。

续报（v4.45 起 · 真实数据 + 教务自动展开）：
  研发封装的「在读学生」接口 GET /ai/getCoTeacherStudents?coTeacherName=<联席姓名> 的
  data.list 每条记录新增 `renewed`（string："是" / "否" / "未设置"，来源 c_product_student.renewed
  的 DB 值 1/2/0）、`subject`（int 科目字典值）、`subjectName`（string 科目名，未匹配到为空串）。
  pull_enroll.py 在 `--edu <教务名>` 在线模式下，把 `renewed` 按 studentSeq 对齐写入
  `enroll.json["renew"]`（原 renew.json 兜底仍保留）。
  ⭐ 接口参数是「联席姓名」，而教务名（如"罗绂燕"）往往不是联席、直接查返回 0 条。此时自动
  从 snapshot/roster.json 提取该教务辖下全部 coTeacherName，逐个联席拉取后按 studentSeq 合并去重。
  语义：renewed="是" → 已续报；"否" → 未续报；"未设置" → 接口已返回但学生尚未在后台选择（显示「未确认」）。
  注意：cProductStudent/getCProductStudentList 的 renewed 仍返回原数字值，不在此次范围。

用法：
  # 在线拉（需要 workdir/token.txt）
  python3 pull_enroll.py --workdir <wd> --edu <教务名>      # 含续报接口拉取
  python3 pull_enroll.py --workdir <wd>                     # 仅企微群统计（续报走 renew.json 兜底）

  # 离线：从已存的接口原始行转换（_roundK/sessionstat_raw.json 这类）
  python3 pull_enroll.py --workdir <wd> --from /path/to/sessionstat_raw.json

输出：<workdir>/snapshot/enroll.json
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

# 科目展示顺序（与 common.SUBJECTS 一致，但固定排序便于跨学生对比）
SUBJECT_ORDER = ("数学", "物理", "化学", "英语", "语文", "生物", "历史", "地理", "政治", "科学")
# 课程名里的「非在读」标记（已关班 / 已结课 / 退费 / 停课 …）
CLOSED_RE = re.compile(r"【[^】]*(已关|结课|退费|退课|停课|转班|取消)[^】]*】")
BASE = "https://stadminv2.xqmsx.cn/api"


def _subject_of(row):
    s = (row.get("subjectName") or "").strip()
    if s:
        return s
    pn = row.get("productName") or ""
    for w in SUBJECT_ORDER:
        if w in pn:
            return w
    return ""


def _pull_month(token, stat_date, page_size=500):
    rows, page = [], 1
    while True:
        q = urllib.parse.urlencode({"year": "4", "term": "3", "timeDimension": "month",
                                    "statDate": stat_date, "page": page, "pageSize": page_size})
        req = urllib.request.Request(BASE + "/wxworkSessionStat/getList?" + q,
                                     headers={"x-token": token, "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode("utf-8"))
        if d.get("code") != 0:
            raise RuntimeError(f"接口返回 code={d.get('code')} msg={d.get('msg')}（token 可能已过期）")
        data = d.get("data") or {}
        lst = data.get("list") or []
        rows.extend(lst)
        if len(rows) >= (data.get("total") or 0) or len(lst) < page_size:
            return rows
        page += 1


def _pull_online(wd, months=4):
    tok_path = os.path.join(wd, "token.txt")
    if not os.path.exists(tok_path):
        raise RuntimeError(f"缺少 {tok_path}（需要先取 token）")
    token = open(tok_path, encoding="utf-8").read().strip()
    today = dt.date.today()
    dates = []
    for i in range(months - 1, 0, -1):
        m = (today.replace(day=1) - dt.timedelta(days=1))
        for _ in range(i - 1):
            m = (m.replace(day=1) - dt.timedelta(days=1))
        dates.append(m.replace(day=1).isoformat())
    dates.append(today.replace(day=1).isoformat())
    dates.append(today.isoformat())
    rows = []
    for d in dates:
        got = _pull_month(token, d)
        print(f"   {d}: {len(got)} 行")
        rows += got
    return rows, f"wxworkSessionStat/getList（{'、'.join(dates)}）"


def _load_renew(wd):
    p = os.path.join(wd, "snapshot", "renew.json")
    if not os.path.exists(p):
        return {}, ""
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}, ""
    src = d.get("source") or "snapshot/renew.json"
    out = {}
    for k, v in (d.get("bySeq") or {}).items():
        out[str(k)] = v
    for it in (d.get("list") or []):
        seq = str(it.get("studentSeq") or it.get("studentId") or "")
        if not seq:
            continue
        v = it.get("renewStatus", it.get("renew", it.get("isRenew")))
        out[seq] = v
    return out, src


def _pull_renew_from_api(wd, edu_name):
    """v4.45：从研发封装的「在读学生」接口拉「是否续报」(renewed)。

    接口 GET /ai/getCoTeacherStudents?coTeacherName=<联席姓名> 的 data.list 每条记录带
    `renewed`（"是"/"否"/"未设置"）、`subject`、`subjectName`。按 studentSeq 对齐回
    enroll.json 的 bySeq。接口无 token / 失败 / 返回 0 条时返回空 dict，由 renew.json 兜底。

    ⭐ 教务视角：接口按「联席姓名」过滤，教务名（如"罗绂燕"）直接查往往是 0 条。
    此时自动从 snapshot/roster.json 提取该教务辖下所有 coTeacherName，逐个联席拉取，
    再按 studentSeq 合并去重，从而覆盖教务名下全部学生。
    """
    tok_path = os.path.join(wd, "token.txt")
    if not os.path.exists(tok_path):
        print("   ⚠️ 缺 token.txt，跳过续报接口拉取（沿用 renew.json / 占位）")
        return {}
    try:
        token = common.load_token(wd)
    except Exception as e:
        print(f"   ⚠️ token 读取失败（{e}），跳过续报接口拉取")
        return {}

    # 先把 roster 读进来：① 该教务辖下的联席名单（用于展开拉取）
    #                      ② 该教务名下的学生 seq 白名单（用于过滤联席接口返回的全机构学生）
    co_names, roster_seqs = [], set()
    roster_path = os.path.join(wd, "snapshot", "roster.json")
    if os.path.exists(roster_path):
        try:
            with open(roster_path, encoding="utf-8") as f:
                roster = json.load(f)
            if isinstance(roster, dict):
                roster = roster.get("rows") or roster.get("list") or []
            co_names = sorted({str(x.get("coTeacherName") or "").strip()
                               for x in roster if x.get("coTeacherName")})
            roster_seqs = {str(x.get("stuSeq") or "").strip() for x in roster}
            roster_seqs.discard("")
        except Exception as e:
            print(f"   ⚠️ roster.json 读取失败（{e}），不展开联席名单、不按学生白名单过滤")

    def fetch(name):
        q = urllib.parse.urlencode({"coTeacherName": name})
        try:
            d = common.get(f"/ai/getCoTeacherStudents?{q}", token)
        except Exception as e:
            return None, f"调用异常 {e}"
        if d.get("code") != 0:
            return None, f"code={d.get('code')} msg={d.get('msg')}"
        return (d.get("data") or {}).get("list") or [], None

    # 1) 先按 edu_name 直接查（edu_name 本身即联席的情况）
    lst, err = fetch(edu_name)
    used = []
    if lst:
        used = [edu_name]
    else:
        # 2) 教务名查不到 → 展开为 roster.json 里的联席名单，逐个拉取合并
        if not co_names:
            print(f"   ⚠️「{edu_name}」非联席名，且无法从 roster 展开联席名单，跳过续报接口")
            return {}
        print(f"   「{edu_name}」非联席名，按 roster 展开 {len(co_names)} 位联席拉取续报："
              f"{'、'.join(co_names)}")
        lst = []
        for name in co_names:
            l2, e2 = fetch(name)
            if e2:
                print(f"     ⚠️ [{name}] {e2}，跳过该联席")
                continue
            if l2:
                used.append(name)
                lst.extend(l2)

    # ⭐ 关键：联席接口返回的是该联席在「全机构」的全部在读学生，远超本教务范围。
    #    必须按本教务 roster 的 studentSeq 白名单过滤，否则会把别的教务/年级/课程的学生也算进来
    #    （实测 9 联席全量 480 条 → 罗绂燕名下 243 人命中 237 条，其余 243 条不属于罗老师）。
    raw_n = len(lst)
    if roster_seqs:
        lst = [x for x in lst if str(x.get("studentSeq") or "").strip() in roster_seqs]

    out, detail = {}, {}
    for x in lst:
        seq = str(x.get("studentSeq") or "").strip()
        rv = (x.get("renewed") or "").strip()
        if not seq or not rv:
            continue
        out[seq] = rv
        detail[seq] = {"renewed": rv,
                       "subject": x.get("subject"),
                       "subjectName": (x.get("subjectName") or "").strip() or None}
    _filt = (f"（联席全量 {raw_n} → 按本教务 {len(roster_seqs)} 名学生白名单过滤后 {len(out)} 条）"
             if roster_seqs and raw_n != len(out) else "")
    print(f"   续报接口（getCoTeacherStudents，联席 {len(used)} 位）：{len(out)} 条 renewed{_filt}"
          f"（含 subject/subjectName {len(detail)} 条）")
    return out, detail


def build(rows, source, wd, renew_online=None):
    """聚合「学生 → 在读科目」，写 snapshot/enroll.json。

    renew_online：可选，(seq->renewed, seq->detail) 元组，来自 _pull_renew_from_api；
    非空时覆盖/补充 enroll.json["renew"]，并把 subject/subjectName 存到 renewDetail。
    """
    by_room = {}
    for g in rows:
        if g.get("roomId"):
            by_room[g["roomId"]] = g          # 同一群在多个月份重复出现 → 按 roomId 去重
    by_seq, by_name = {}, {}
    skipped_closed = 0
    for g in by_room.values():
        pn = (g.get("productName") or "").strip()
        if not pn or CLOSED_RE.search(pn):
            skipped_closed += 1
            continue
        seq = str(g.get("studentSeq") or "").strip()
        name = (g.get("studentName") or "").strip()
        if not name:
            continue
        subj = _subject_of(g)
        rec = by_seq.setdefault(seq, {"name": name, "subjects": [], "courses": []}) if seq else None
        if rec is None:
            rec = by_name.setdefault(name, {"name": name, "subjects": [], "courses": []})
        if subj and subj not in rec["subjects"]:
            rec["subjects"].append(subj)
        if pn not in [c["product"] for c in rec["courses"]]:
            rec["courses"].append({"product": pn, "subject": subj,
                                   "group": g.get("groupName") or "",
                                   "main": g.get("mainTeacherName") or "",
                                   "co": g.get("coTeacherName") or ""})
    renew, renew_src = _load_renew(wd)
    renew_detail = {}
    if renew_online:
        api_renew, api_detail = renew_online
        renew = dict(renew)
        for seq, rv in api_renew.items():
            renew[seq] = rv
        renew_detail = dict(api_detail)
        if not renew_src:
            renew_src = "getCoTeacherStudents（renewed 字段，v4.44）"
    for rec in list(by_seq.values()) + list(by_name.values()):
        rec["subjects"].sort(key=lambda s: (SUBJECT_ORDER.index(s) if s in SUBJECT_ORDER else 99, s))
    out = {
        "generatedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": source,
        "scope": "全机构企微群统计（含跨学科）",
        "activeRule": "productName 无【已关班/已结课/已退费/停课】前缀 = 本期在读（代理口径）",
        "skippedClosed": skipped_closed,
        "renewSource": renew_src,
        "renew": renew,
        "renewDetail": renew_detail,
        "bySeq": by_seq,
        "byName": by_name,
    }
    p = os.path.join(wd, "snapshot")
    os.makedirs(p, exist_ok=True)
    dst = os.path.join(p, "enroll.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    multi = sum(1 for r in list(by_seq.values()) + list(by_name.values()) if len(r["subjects"]) > 1)
    print(f"✅ {dst}\n   在读课程群 {len(by_room) - skipped_closed} 个 · 学生 {len(by_seq)} 人 · "
          f"多科目 {multi} 人 · 跳过已关班 {skipped_closed} 个")
    if renew:
        print(f"   续报字段：{len(renew)} 条（来源 {renew_src}）")
    else:
        print("   续报字段：无（snapshot/renew.json 未提供）→ 报告显示「未确认」")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--edu", default=None, help="教务姓名（v4.44：在线模式下同时拉续报 renewed 字段）")
    ap.add_argument("--from", dest="src", default=None, help="离线：接口原始行 JSON")
    ap.add_argument("--months", type=int, default=4)
    a = ap.parse_args()
    if a.src:
        rows = json.load(open(a.src, encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("all") or rows.get("list") or []
        src_desc = f"{os.path.basename(a.src)}（离线快照）"
        renew_online = None
    else:
        rows, src_desc = _pull_online(a.workdir, a.months)
        renew_online = _pull_renew_from_api(a.workdir, a.edu) if a.edu else None
    build(rows, src_desc, a.workdir, renew_online)
