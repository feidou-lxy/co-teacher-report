"""步骤2：拉取该联席名下学生的群聊列表（含 roomId 与响应时效标记）。

逻辑：
  - wxworkSessionStat/getList 月维度覆盖「整月所有有聊天的群」（需传当月第一天；
    当前月后台未聚合，故月维度取窗口起始月，再用日维度补当前月）。
  - 用 students.json 里的学生姓名做交集，得到本联席的群。

输出 <workdir>/groups.json ：[{roomId, studentName, groupName, isTimeout,
isReplied, timeoutDuration, coTeacherName, classTeacherName}]
"""
import argparse
import datetime
import json
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
    for g in by_room.values():
        sn = common.norm_name(g.get("studentName"))
        if sn in names:
            out.append({
                "roomId": g.get("roomId"),
                "studentName": g.get("studentName"),
                "groupName": g.get("groupName"),
                "isTimeout": g.get("isTimeout"),
                "isReplied": g.get("isReplied"),
                "timeoutDuration": g.get("timeoutDuration"),
                "coTeacherName": co_of.get(sn, ""),
                "classTeacherName": edu_of.get(sn, ""),
            })
    json.dump(out, open(f"{wd}/groups.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n✅ 本{label}「{scope}」命中群 {len(out)} 个 -> groups.json")


if __name__ == "__main__":
    main()
