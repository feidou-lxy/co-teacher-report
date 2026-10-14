"""步骤2：拉取该角色/课程下的群聊列表（仅群标识信息）。

接口 wxworkSessionStat/getList 为**机构级**（不带 co/edu 过滤），月维度覆盖整月、
再用日维度补当前月，按 roomId 去重。三种视角在此之上做不同过滤：

  - 联席/教务视角（双条件，缺一不可）：
      ① 群内学生名 ∈ students.json 名单；② 群归属字段 == 本角色（联席看 coTeacherName、
         教务看 classTeacherName）。② 避免把学生别的科目群（由别的角色负责）混入。
  - **课程维度（--course）**：**后台课程名 `productName`** 包含课程串即收
     （如「高二数学清北层日E邵」「高二数学985层进化班六D谢胡」），
     无需学生名/归属过滤——课程天然跨多个联席，正是要一次性取出该课全部群；
     另传 --co <联席名> 可作课程内子筛选（只保留该联席负责的部分）。
     ⚠️ v4.7 起**不再用群名 groupName 判归属**：实测二者会不一致
     （群名「高二数学985层六D谢胡」而后台课程名是「高二数学清北层进化班日D谢胡」，
     另有群名缺字如「高二数学清北层E邵」）。课程名一个都没命中时，才退回群名匹配并提示。

输出 <workdir>/groups.json ：[{roomId, studentName, groupName, productName,
coTeacherName, classTeacherName, ownerName, subjectName}]
其中 productName（v4.7）= 后台「响应时效」列表直接给出的**课程名**（如
「2026Q-SX-高二数学985层进化班六D谢胡」），报告侧据此取课程名，不再切群名解析。
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
    ap.add_argument("--co", default=None, help="联席姓名（联席视角 / 课程内子筛选）")
    ap.add_argument("--edu", default=None, help="教务姓名（教务视角）")
    ap.add_argument("--course", default=None, help="课程名（课程维度），如 高二数学清北层日E邵")
    ap.add_argument("--month", default="2026-09", help="窗口起始月 YYYY-MM（月维度取该月第一天）")
    ap.add_argument("--year", default="4")
    ap.add_argument("--term", default="3")
    args = ap.parse_args()
    role, name = common.resolve_scope(args)
    wd = common.workdir(args)
    token = common.load_token(wd)

    # 月维度：窗口起始月第一天
    month_first = f"{args.month}-01"
    rows = pull_dimension(token, "month", month_first, args.year, args.term)
    # 日维度：补当前月（当天）
    today = datetime.date.today().isoformat()
    rows += pull_dimension(token, "day", today, args.year, args.term)

    # 按 roomId 去重（co/edu/课程 三视角共用机构级群列表）
    by_room = {}
    for g in rows:
        rid = g.get("roomId")
        if rid:
            by_room[rid] = g
    print(f"机构级群列表去重后 {len(by_room)} 个")

    if role == "course":
        # —— 课程维度：**以后台课程名 productName 为唯一口径**（v4.7）——
        # 课程天然跨多个联席，一次性取出该课全部群；--co 可作课程内子筛选。
        # ⚠️ 实测群名与后台课程名会不一致（例：群名「高二数学985层六D谢胡」，
        #    后台课程名却是「高二数学清北层进化班日D谢胡」），故**不再用群名判归属**，
        #    只把这类群作为诊断列出，避免把别的课的学生算进来。
        course = name
        sub_co = (getattr(args, "co", None) or "").strip()

        def rec(g, pn):
            return {
                "roomId": g.get("roomId"),
                "studentName": g.get("studentName"),
                "groupName": g.get("groupName"),
                # v4.7：课程名（后台产品名），报告侧据此取课程，不再解析 groupName
                "productName": pn,
                # 课程维度直接用群自带的归属/学科（无需学生名映射）
                "coTeacherName": g.get("coTeacherName") or "",
                "classTeacherName": g.get("classTeacherName") or "",
                "ownerName": g.get("coTeacherName") or "",
                "subjectName": g.get("subjectName") or "",
            }

        # 诊断用：把「进化班」这类班型后缀抹掉再比，双向识别「群名像、课程名不对」的群
        _nt = lambda x: str(x or "").replace("进化班", "")
        out, mismatch = [], []
        for g in by_room.values():
            pn = g.get("productName") or ""
            if course not in pn:
                gn_course = common.course_name(None, g.get("groupName"))
                if _nt(course) and (_nt(course) in _nt(gn_course)
                                    or course in (g.get("groupName") or "")):
                    mismatch.append(g)     # 群名像、课程名不是 → 仅诊断
                continue
            if sub_co and not owner_matches(g.get("coTeacherName"), sub_co):
                continue
            out.append(rec(g, pn))

        # 课程名一个都没命中 → 退回群名匹配（兼容按旧群名写法输入；口径风险会提示）
        by_gn = False
        if not out:
            by_gn = True
            for g in by_room.values():
                if course not in (g.get("groupName") or ""):
                    continue
                if sub_co and not owner_matches(g.get("coTeacherName"), sub_co):
                    continue
                out.append(rec(g, g.get("productName") or ""))

        json.dump(out, open(f"{wd}/groups.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        how = "⚠️ 按群名兜底匹配（后台课程名未命中）" if by_gn else "按后台课程名匹配"
        print(f"\n✅ 课程「{course}」命中群 {len(out)} 个 -> groups.json（{how}）"
              + (f" · 已按联席「{sub_co}」子筛选" if sub_co else ""))
        if by_gn:
            print("   ⚠️ 你输入的是**群名写法**，后台课程名里没有这个串。"
                  "群名与后台课程名可能不一致（实测约 1 成群如此），建议改从后台复制课程名重跑。")
            pns = sorted({g.get("productName") or "" for g in out})
            for p in pns[:6]:
                print(f"      该口径下实际涉及课程：{p}")
        elif mismatch:
            print(f"   ⚠️ 另有 {len(mismatch)} 个群「群名含该串、但后台课程名是别的课程」，"
                  f"**已排除**（群名与后台不符，建议回后台核对）：")
            for g in mismatch[:8]:
                print(f"      {g.get('studentName')}：群名 {g.get('groupName')}"
                      f" → 后台课程名 {g.get('productName')}")
            if len(mismatch) > 8:
                print(f"      …另 {len(mismatch) - 8} 个")
        return

    # —— 联席/教务视角（双条件过滤）——
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
            # v4.7：课程名（后台产品名，如 2026Q-SX-高二数学985层进化班六D谢胡），
            # 报告侧据此取课程，不再解析 groupName（缺失时自动回退群名）
            "productName": g.get("productName") or "",
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
