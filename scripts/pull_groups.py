"""步骤2：拉取该角色「名下学生 + 归属该角色」的群聊列表（仅群标识信息）。

逻辑：
  - wxworkSessionStat/getList 月维度覆盖「整月所有有聊天的群」（需传当月第一天；
    当前月后台未聚合，故月维度取窗口起始月，再用日维度补当前月）。
  - **双条件过滤**（缺一不可）：
      ① 群内学生名 ∈ students.json 名单（这名学生确实是本角色的学生）；
      ② 群自带的归属字段 == 本角色 —— 联席看 coTeacherName、教务看 classTeacherName。
    仅有 ① 是不够的：一名学生可能同期报了多科，这些科的群由**别的联席/教务**负责，
    若只按学生名交集会把别的角色的群也拉进来（如「张巍（数学联席）」被混入学生的
    物理/化学/英语群）。② 保证只保留真正归本角色管的群。
    若后台某群归属字段为空，则回退保留（避免漏群）。

输出 <workdir>/groups.json ：[{roomId, studentName, groupName,
coTeacherName, classTeacherName, subjectName}]
（注：「是否回复」与「响应超时」均不再采用后台列 —— 实测与聊天事实不符；
  统一由 extract.py 依据聊天记录自行推算，故此处不再拉取 isReplied / isTimeout / timeoutDuration）
"""
import argparse
import datetime
import json
import re
import sys
import time
import urllib.parse

sys.path.insert(0, sys.path[0])
import common


def fetch_page(token, params):
    path = "/wxworkSessionStat/getList?" + urllib.parse.urlencode(params)
    return common.get(path, token)


def pull_dimension(token, dim, stat_date, year="4", term="3"):
    base = {"year": year, "term": term, "timeDimension": dim, "statDate": stat_date}
    r = fetch_page(token, {**base, "page": 1, "pageSize": 1})
    total = (r.get("data") or {}).get("total") or 0
    rows = []
    page = 1
    PAGE = 200
    while len(rows) < total:
        r = fetch_page(token, {**base, "page": page, "pageSize": PAGE})
        if r.get("code") != 0:
            print("  stop:", r.get("code"), r.get("msg"))
            break
        lst = (r.get("data") or {}).get("list") or []
        if not lst:
            break
        rows.extend(lst)
        print(f"  [{dim} {stat_date}] page {page} got {len(lst)} accum {len(rows)}")
        if len(lst) < PAGE:
            break
        page += 1
        time.sleep(0.1)
    return rows


def owner_matches(owner, target):
    """群自带归属名是否指向本角色。

    群接口的归属字段可能带学科后缀（如「张宇-化学」），故取「-／空格」前的首段比对。
    归属字段为空时返回 True（回退保留，避免因后台缺字段漏群）。
    """
    if not owner:
        return True
    first = re.split(r"[-－\s]", str(owner))[0].strip()
    return first == str(target or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", default=None, help="联席姓名（联席视角）")
    ap.add_argument("--edu", default=None, help="教务姓名（教务视角）")
    ap.add_argument("--month", default="2026-09", help="窗口起始月 YYYY-MM（月维度取该月第一天）")
    ap.add_argument("--year", default="4")
    ap.add_argument("--term", default="3")
    args = ap.parse_args()

    wd = common.workdir(args)
    token = common.load_token(wd)
    students = json.load(open(f"{wd}/students.json", encoding="utf-8"))
    names = set(common.norm_name(x["stuName"]) for x in students)
    # 学生 -> 所属联席/教务 映射，供下游按联席分组与标注
    co_of, edu_of = {}, {}
    for x in students:
        nk = common.norm_name(x["stuName"])
        co_of[nk] = x.get("coTeacherName") or co_of.get(nk) or ""
        edu_of[nk] = x.get("classTeacherName") or edu_of.get(nk) or ""
    scope = args.edu or args.co or "未指定"
    label = "教务" if args.edu else "联席"
    # 本角色的群归属字段：联席看群自带 coTeacherName，教务看群自带 classTeacherName
    owner_field = "classTeacherName" if args.edu else "coTeacherName"
    print(f"{label}「{scope}」学生姓名（归一化）{len(names)} 个")

    # 月维度：窗口起始月第一天
    month_first = f"{args.month}-01"
    rows = pull_dimension(token, "month", month_first, args.year, args.term)
    # 日维度：补当前月（当天）
    today = datetime.date.today().isoformat()
    rows += pull_dimension(token, "day", today, args.year, args.term)

    # 按 roomId 去重
    by_room = {}
    for g in rows:
        rid = g.get("roomId")
        if rid:
            by_room[rid] = g
    print(f"群列表去重后 {len(by_room)} 个")

    out = []
    skipped_other_owner = 0
    for g in by_room.values():
        sn = common.norm_name(g.get("studentName"))
        if sn not in names:
            continue
        # 条件②：群必须归本角色管（否则是这名学生别的科目群，由别的联席/教务负责）
        if not owner_matches(g.get(owner_field), scope):
            skipped_other_owner += 1
            continue
        out.append({
            "roomId": g.get("roomId"),
            "studentName": g.get("studentName"),
            "groupName": g.get("groupName"),
            # 注：不再采信后台 isTimeout / isReplied（响应时效），实测与聊天事实不符，
            # 统一由 extract.py 依据聊天记录自行推算（见 resp_wait_stats）。
            "coTeacherName": co_of.get(sn, ""),
            "classTeacherName": edu_of.get(sn, ""),
            # 群自带归属与学科（诊断用；过滤已按 owner_field 完成）
            "ownerName": g.get(owner_field) or "",
            "subjectName": g.get("subjectName") or "",
        })
    json.dump(out, open(f"{wd}/groups.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"  ⤷ 已按「归本{label}管理」过滤，剔除 {skipped_other_owner} 个属其他{label}的群")
    print(f"\n✅ 本{label}「{scope}」命中群 {len(out)} 个 -> groups.json")


if __name__ == "__main__":
    main()
