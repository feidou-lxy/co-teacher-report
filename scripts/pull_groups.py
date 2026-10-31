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
import os
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


def merge_prev_groups(wd, out, keep):
    """v4.55：本次接口没返回、但历史 groups.json 里已确认属于本范围的群，保留下来。

    为什么：`wxworkSessionStat` 返回的**不是「群存在」的全集**，而是某个统计口径下的
    子集 —— 月维度只覆盖指定月，`day` 维度实测严重不全（v4.56 实测：9 月月维度
    本课程 24 个群，10 月逐日每天只回 1~9 个，9 个群一次都没出现）。
    于是重拉一次就可能把 25 个群变成 23 个，报告里有人从「有群」退回「无群」。
    群一旦建了就存在，不该因一次拉取而消失。
    """
    p = f"{wd}/groups.json"
    if not os.path.exists(p):
        return out
    try:
        prev = json.load(open(p, encoding="utf-8"))
    except Exception:
        return out
    have = {x.get("roomId") for x in out if x.get("roomId")}
    extra = [x for x in prev
             if x.get("roomId") and x["roomId"] not in have and keep(x)]
    if extra:
        names = "、".join(sorted({x.get("studentName") or "?" for x in extra}))
        print(f"   ↺ 保留历史群 {len(extra)} 个（本次接口未返回）：{names}")
    return out + extra


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

    # 月维度：**逐月**拉（v4.56）
    # ⚠️ 实测（2026-10-22 六C谢胡）：`day` 维度**不是「当天有消息」的全集** ——
    #   10 月 1~22 日每天只回 1~9 个本课程群（9 月月维度有 24 个）；
    #   朱鹏羽 / 董芳菲 / 于熙桐 等 9 个群一次都没出现过，尽管月维度里
    #   lastAskTime 明确落在 10-18 / 10-21 / 10-22。
    #   当月月维度又要等下月才聚合（当月 total=0）。所以：
    #     ① 月维度从「起始月」逐月拉到当前月，任一月返回 0 就显式提示；
    #     ② `day` 只作补充，**不能**据此判「学生无群」。
    # `--month` 可传 "2026-09" 也可传 "2026-08,2026-09"（取最早一个月为起点）。
    today = datetime.date.today()
    cur_m = f"{today.year}-{today.month:02d}"
    starts = [m.strip() for m in str(args.month or "").split(",") if m.strip()]
    months = []
    if starts:
        y, mo = int(starts[0][:4]), int(starts[0][5:7])
        while f"{y}-{mo:02d}" <= cur_m:
            months.append(f"{y}-{mo:02d}")
            mo += 1
            if mo > 12:
                y, mo = y + 1, 1
    rows = []
    for mm in months:
        got = pull_dimension(token, "month", f"{mm}-01", args.year, args.term)
        flag = "  ⚠️ 该月月维度无数据（当月未聚合 / 该月未开展）" if not got else ""
        print(f"  月维度 {mm}：{len(got)} 行{flag}")
        rows += got
    for d in range(1, today.day + 1):
        rows += pull_dimension(token, "day",
                               f"{today.year}-{today.month:02d}-{d:02d}",
                               args.year, args.term)

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

        _roster = set()
        _sp = f"{wd}/students.json"
        if os.path.exists(_sp):
            try:
                _roster = {common.norm_name(x.get("stuName"))
                           for x in json.load(open(_sp, encoding="utf-8"))}
            except Exception:
                _roster = set()

        # v4.55：接口会返回「已退课学生」的旧群（群还没解散，productName 仍是本课程）。
        #   本课实测 29 个群里有 4 个是已退课学生（周若愚 / 明达 / 贾一丁 / 张博越），
        #   他们不在 students.json，却会被下游当成在册学生拉聊天、进分析 → 报告里混进无关的人。
        #   只按**非空 studentName** 过滤（studentName 为空时保留，避免误删只有课程名的群）。
        if _roster:
            _before = len(out)
            out = [x for x in out
                   if not (x.get("studentName")
                           and common.norm_name(x["studentName"]) not in _roster)]
            if len(out) < _before:
                print(f"   ⤷ 剔除 {_before - len(out)} 个「学生已不在册」的旧群（群未解散）")

        # v4.55：与历史 groups.json 合并，避免「本次接口未返回」把已确认的群清掉
        def _keep(x):
            pn = x.get("productName") or ""
            gn = x.get("groupName") or ""
            if course not in pn and course not in gn:
                return False
            if _roster and common.norm_name(x.get("studentName")) not in _roster:
                return False      # 已退课学生的旧群不再计入
            return True

        out = merge_prev_groups(wd, out, _keep)

        # v4.56：**花名册 roomId 兜底**。后台「学生-群」绑定关系不依赖消息统计，
        #   比群列表接口更硬：凡是 students.json 里有 roomId、而群清单没收录的，
        #   一律补进来（roomIdSource=roster_roomid）。实测本课 25 人正好对上 25 群，
        #   但接口侧只要少返回一次就可能把有群的人打成「无群」，这层兜底不能省。
        _have = {x.get("roomId") for x in out if x.get("roomId")}
        _sp_stu = f"{wd}/students.json"
        if os.path.exists(_sp_stu):
            try:
                _stu = json.load(open(_sp_stu, encoding="utf-8"))
            except Exception:
                _stu = []
            _add = []
            for s in _stu:
                rid = (s.get("roomId") or "").strip()
                if not rid or rid in _have:
                    continue
                _add.append({
                    "roomId": rid,
                    "studentName": s.get("stuName"),
                    "groupName": s.get("groupName")
                                 or f"{s.get('stuName')} - {course}",
                    "productName": s.get("productName") or course,
                    "coTeacherName": s.get("coTeacherName") or "",
                    "classTeacherName": s.get("classTeacherName") or "",
                    "ownerName": s.get("coTeacherName") or "",
                    "subjectName": s.get("subjectName") or "数学",
                    "roomIdSource": "roster_roomid",
                })
                _have.add(rid)
            if _add:
                out += _add
                print(f"   ＋ 花名册 roomId 兜底补 {len(_add)} 个群："
                      + "、".join(str(g.get("studentName")) for g in _add[:12])
                      + ("…" if len(_add) > 12 else ""))
        for x in out:
            x.setdefault("roomIdSource", "api")

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
    # v4.55：同课程维度 —— 保留历史已确认的群（本次接口按「当天有消息」返回，会漏零消息的群）
    out = merge_prev_groups(
        wd, out,
        lambda x: (common.norm_name(x.get("studentName")) in names
                   and owner_matches(x.get(owner_field), scope)))
    json.dump(out, open(f"{wd}/groups.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"  ⤷ 已按「归本{label}管理」过滤，剔除 {skipped_other_owner} 个属其他{label}的群")
    print(f"\n✅ 本{label}「{scope}」命中群 {len(out)} 个 -> groups.json")


if __name__ == "__main__":
    main()
