"""步骤1（新接口版 v4.11）：拉取该课程 / 联席下的**在读学生花名册 + 学生↔群映射**。

替代旧链路的 pull_students.py + pull_groups.py 两步。研发已封装：

    GET /api/ai/getCourseStudents?productName=<课程名>        课程维度
    GET /api/ai/getCoTeacherStudents?coTeacherName=<联席姓名>   联席维度

接口侧已完成筛选（我们不需要再做）：课程未删除、is_classin_callback=2、在读
（product_student_status=1）、购课类型仅直播/回放（product_type=1/2，不含试听）；
全量返回不分页。roomId 由「学生课程关系 → 服务群」关联，取 last_message_time 最新的
一个已匹配、未解散的群（biz_status=1、status=0）；无群则返回空串。

**这是本技能最关键的一次取数升级**：旧链路没有「学生↔群」映射，只能用群名里的学生姓名
反查，且群列表接口只收录有响应时效记录的群（天然有损）。实测张巍视角：
roomId 覆盖率 旧 70%（57/81）→ 新 **84%**（68/81），**多出 14 个旧链路从未拉到的群**；
两者都有 roomId 的 54 人里 **54 一致、0 冲突**（同 ID 体系，可信）。

⚠️ 教务维度（--edu）**暂无对应接口**，本脚本会明确拒绝，请走旧链路 pull_students.py。

输出（<workdir>/）
    students.json        花名册（字段与旧链路对齐 + roomId），供 pull_attendance / merge 复用
    groups.json          群标识（由 roster 的 roomId 派生），供 pull_chats 复用
    roster_api_raw.json  接口原始响应（供 adapt_api.py 复用，避免重复调接口）

字段映射（接口 -> students.json）：见 _row()。「接口没给的」一律写 None，不臆造。
"""
import argparse
import datetime
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, sys.path[0])
import common

API_COURSE = "/ai/getCourseStudents"
API_CO = "/ai/getCoTeacherStudents"

# 接口 purchaseType 是**中文原串**（只会是「直播」/「回放」），而下游（merge.py 等）
# 读的是数字码 cIncomeProductType。此处反查回数字码，做到**下游零改动**；
# 同时原串也保留在 purchaseType 字段里，便于核对。
# 取值来自 common.BUY_TYPE_LABEL：1=直播 2=回放 3=试听（插班）4=测试。
PURCHASE_TO_CODE = {"直播": 1, "回放": 2, "试听": 3, "试听（插班）": 3, "测试": 4}


def fetch(token, path, params, retry=3):
    """带重试的 GET（中文参数由 common.get + urlencode 负责编码）。"""
    import urllib.parse
    qs = urllib.parse.urlencode(params)
    last = None
    for i in range(retry):
        try:
            r = common.get(f"{path}?{qs}", token)
        except Exception as e:                      # 网络类异常重试
            last = e
            if i == retry - 1:
                raise
            time.sleep(2)
            continue
        # HTTP 200 不代表业务成功（文档第五节），必须同时判 code
        if r.get("code") == 0:
            return r
        last = RuntimeError(f"code={r.get('code')} msg={r.get('msg')}")
        time.sleep(1)
    raise SystemExit(f"❌ 接口 {path} 调用失败：{last}")


def _row(x):
    """接口单条 -> students.json 单条（字段名与旧链路对齐，下游零改动）。"""
    pt = (x.get("purchaseType") or "").strip()
    room = (x.get("roomId") or "").strip() or None
    return {
        "stuName": x.get("studentName"),
        "stuSeq": x.get("studentSeq"),
        "productName": x.get("productName"),
        # ⚠️ 新接口不提供 productStudentId；旧链路有此字段但**全流程无下游消费**
        #    （出勤接口认的是 ProductId），故置 None 并在自检里声明。
        "productStudentId": None,
        "ProductId": x.get("productId"),
        "teacherName": None,            # 主讲：新接口未提供
        "classTeacherName": x.get("classTeacherName") or "",
        "schoolName": None,             # 校区：新接口未提供
        "grade": None,                  # 年级：新接口未提供
        "coTeacherName": x.get("coTeacherName") or "",
        "cIncomeProductType": PURCHASE_TO_CODE.get(pt),   # 反推数字码，兼容 merge.py
        "purchaseType": pt,             # 原串，便于核对（新增字段，不影响旧逻辑）
        "roomId": room,                 # ★ 旧链路缺的就是它
        "roomIdSource": "api" if room else None,          # api | fill_groupname | None
    }


def _groups_from_roster(rows):
    """由 roster 的 roomId 派生 groups.json（供 pull_chats 遍历拉聊天）。

    ⚠️ 新接口不返回群名 groupName —— 报告侧一律以**后台课程名 productName** 为准
    （v4.7 起群名仅供参考），故 groupName 置空串，不影响任何判定。
    """
    out = []
    for x in rows:
        room = x.get("roomId")
        if not room:
            continue
        out.append({
            "roomId": room,
            "studentName": x.get("stuName"),
            "groupName": "",                       # 新接口未提供；不用于任何判定
            "productName": x.get("productName") or "",
            "coTeacherName": x.get("coTeacherName") or "",
            "classTeacherName": x.get("classTeacherName") or "",
            "ownerName": x.get("coTeacherName") or "",
            "subjectName": common.subject_of(x.get("productName") or ""),
            "roomIdSource": "api",
        })
    return out


def _fill_rooms(token, rows, role, name, month, year, term):
    """用群列表的「群名内姓名」为**接口没给 roomId** 的学生补一次（旧链路方式）。

    为什么需要（实测证据）：课程「高二数学清北层日E邵」的接口返回 56 人里，
    黄五琦 / 赵芷青 / 张奕鑫 的 roomId 是空串，但这三个群**真实存在且活跃**
    （群名分别含三人姓名，消息 108 / 373 / 84 条，后台课程名同为
    `2026Q-SX-高二数学清北层日E邵`）。说明接口的「学生课程关系 → 服务群」
    关联并不完整 —— 漏一个是漏一个学生的全部沟通证据。

    🔒 安全边界：**只对接口返回空 roomId 的学生生效**，接口已有的值一律不覆盖。

    返回 `(补全人数, 补全群列表)`。
    """
    import pull_groups as PG                     # 复用同一套群列表拉取与归属匹配

    month_first = f"{month}-01"
    # 从起始月**逐月**拉到当月（只拉一个月会漏：45 天窗口常跨 2~3 个自然月，
    # 而月维度是「该月第一天」一个点 —— 只拉起点月会整月漏掉后面几个月的群）。
    raw = []
    y, m = int(month[:4]), int(month[5:7])
    today = datetime.date.today()
    while (y, m) <= (today.year, today.month):
        raw += PG.pull_dimension(token, "month", f"{y}-{m:02d}-01", year, term)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for d in range(1, today.day + 1):            # 当月后台尚未聚合，逐日补
        raw += PG.pull_dimension(token, "day",
                                 f"{today.year}-{today.month:02d}-{d:02d}", year, term)
    print(f"   群列表（{month_first} ~ 今天）共 {len(raw)} 条")
    by_room = {}
    for g in raw:
        rid = g.get("roomId")
        if rid:
            by_room[rid] = g
    print(f"   群列表去重后 {len(by_room)} 个")

    # 待补学生：接口 roomId 为空
    need = {common.norm_name(x["stuName"]): x for x in rows if not x.get("roomId")}
    if not need:
        return 0, []
    have = {x["roomId"] for x in rows if x.get("roomId")}
    best = {}                                    # 姓名 -> (last_message_time, 群)
    for g in by_room.values():
        rid = g.get("roomId")
        if rid in have:
            continue
        nn = common.norm_name(g.get("studentName"))
        if nn not in need:
            continue
        if role == "course":
            pn = g.get("productName") or ""
            if name not in pn and name not in (g.get("groupName") or ""):
                continue                         # 课程外/别的课的群，不要
        else:                                    # 联席：群须归本人管
            if not PG.owner_matches(g.get("coTeacherName"), name):
                continue
        key = str(g.get("lastMessageTime") or g.get("last_message_time") or "")
        if nn not in best or key > best[nn][0]:  # 与接口口径一致：取最新
            best[nn] = (key, g)

    filled, extra = 0, []
    for nn, (_k, g) in best.items():
        x = need[nn]
        x["roomId"] = g.get("roomId")
        x["roomIdSource"] = "fill_groupname"
        extra.append({
            "roomId": g.get("roomId"),
            "studentName": x["stuName"],
            "groupName": g.get("groupName") or "",
            "productName": g.get("productName") or x.get("productName") or "",
            "coTeacherName": x.get("coTeacherName") or "",
            "classTeacherName": x.get("classTeacherName") or "",
            "ownerName": g.get("coTeacherName") or "",
            "subjectName": g.get("subjectName") or "",
            "roomIdSource": "fill_groupname",
        })
        filled += 1
    if filled:
        print(f"   群名补全 {filled} 人：{', '.join(x['studentName'] for x in extra)}")
    miss = len(need) - filled
    if miss:
        print(f"   仍有 {miss} 人无群（接口与群列表都没有）→ 交由 coverage 结合出勤判档")
    return filled, extra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", default=None, help="联席姓名，如 张巍（联席视角）")
    ap.add_argument("--edu", default=None, help="教务姓名（⚠️ 新接口暂不支持，请走旧链路）")
    ap.add_argument("--course", default=None, help="课程名（课程维度），如 2026Q-SX-高二数学清北层日E邵")
    ap.add_argument("--fill", action="store_true",
                    help="★ 兜底：为接口未关联到群的学生，用群列表「群名内姓名」补一次 roomId"
                         "（实测接口会漏掉真实存在的群，建议开启）")
    ap.add_argument("--month", default=None, help="补全时群列表的起始月 YYYY-MM（默认近 45 天所在月）")
    ap.add_argument("--year", default="4")
    ap.add_argument("--term", default="3")
    args = ap.parse_args()
    role, name = common.resolve_scope(args)

    if role == "edu":
        raise SystemExit(
            "❌ 教务维度（--edu）暂无对应接口。\n"
            "   研发目前只封装了「按课程」「按联席」两个维度。\n"
            "   教务视角请去掉 --api，走旧链路：\n"
            "     python scripts/pull_students.py --workdir <wd> --edu <教务名>\n"
            "   或请研发补一个 GET /ai/getClassTeacherStudents?classTeacherName=<教务姓名>。")

    wd = common.workdir(args)
    token = common.load_token(wd)

    if role == "course":
        path, params, label = API_COURSE, {"productName": name}, "课程"
    else:
        path, params, label = API_CO, {"coTeacherName": name}, "联席"

    print(f"→ GET {path}?{'&'.join(f'{k}={v}' for k, v in params.items())}")
    r = fetch(token, path, params)
    data = r.get("data") or {}
    raw_list = data.get("list") or []
    print(f"  接口返回 total={data.get('total')} 实收={len(raw_list)}（code=0 {r.get('msg')}）")

    if not raw_list:
        raise SystemExit(
            f"❌ {label}「{name}」接口返回 0 条。请核对名称是否为**精确匹配**的完整写法：\n"
            f"   课程需带前缀全串（如 2026Q-SX-高二数学清北层日E邵）；联席需与后台姓名完全一致。")

    rows = [_row(x) for x in raw_list]
    groups = _groups_from_roster(rows)      # ⚠️ 必须在 fill **之前**取，否则补全的群会计两次

    n_fill, extra = 0, []
    if args.fill:
        month = args.month or (datetime.date.today()
                               - datetime.timedelta(days=45)).strftime("%Y-%m")
        print(f"\n→ 群名补全（仅对接口未给 roomId 的学生；群列表起始月 {month}）")
        n_fill, extra = _fill_rooms(token, rows, role, name,
                                    month, args.year, args.term)
        groups += extra

    # ---------------- 落盘 ----------------
    json.dump(rows, open(f"{wd}/students.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(groups, open(f"{wd}/groups.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({
        "generatedAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + "+08:00",
        "endpoint": path, "params": params,
        "code": r.get("code"), "msg": r.get("msg"),
        "total": data.get("total"), "list": raw_list,
    }, open(f"{wd}/roster_api_raw.json", "w", encoding="utf-8"),
        ensure_ascii=False, indent=1)

    # ---------------- 自检 ----------------
    rooms = [x["roomId"] for x in rows if x["roomId"]]
    dup = {k: v for k, v in Counter(rooms).items() if v > 1}      # 契约要求 roomId 全局唯一
    norms = Counter(common.norm_name(x["stuName"]) for x in rows)
    clash = {k: v for k, v in norms.items() if v > 1}             # 归一化重名会污染 join
    no_room = [x["stuName"] for x in rows if not x["roomId"]]

    pids = sorted({x["ProductId"] for x in rows if x["ProductId"] is not None})
    n_api = sum(1 for x in rows if x.get("roomIdSource") == "api")
    n_fill2 = sum(1 for x in rows if x.get("roomIdSource") == "fill_groupname")
    print(f"\n✅ {label}「{name}」学生 {len(rows)} 人 -> students.json ｜ 群 {len(groups)} 个 -> groups.json")
    print(f"   roomId 覆盖率：{len(rooms)}/{len(rows)} = {len(rooms)/len(rows)*100:.0f}%"
          f"（旧链路按群名匹配约 64~70%）")
    print(f"     其中 接口直给 {n_api} 人" + (f" ｜ 群名补全 {n_fill2} 人" if n_fill2 else "")
          + (f"（未启用 --fill）" if not args.fill else ""))
    print(f"   课程 ProductId：{pids}")
    cn = Counter(common.course_name(x.get("productName")) for x in rows)
    for k, v in cn.most_common():
        print(f"     {v:>3}  {k}")
    bt = Counter(x.get("purchaseType") or "未标注" for x in rows)
    print("   购课类型：" + " ｜ ".join(f"{k} {v}人" for k, v in bt.most_common()))
    if role == "course":
        cc = Counter(x.get("coTeacherName") or "-" for x in rows)
        print(f"   涉及联席 {len(cc)} 人：" + " ｜ ".join(f"{k} {v}人" for k, v in cc.most_common()))

    warn = 0
    if dup:
        warn += 1
        print(f"   ⚠️ roomId 重复 {len(dup)} 组（契约要求唯一，将影响置信度）：{list(dup)[:3]}")
    if clash:
        warn += 1
        print(f"   ⚠️ 归一化后重名 {len(clash)} 组：{list(clash)[:3]}")
    if no_room:
        warn += 1
        print(f"   ℹ️ {len(no_room)} 人接口未关联到群（{len(no_room)/len(rows)*100:.0f}%）→ "
              f"将由 coverage 结合出勤判定「出勤但无沟通 / 失联」")
        print(f"      {no_room}")
    print(f"   自检：{'全部通过' if not warn else f'{warn} 项提示'}")

    print(f"\n下一步：python scripts/pull_chats.py --workdir {wd} ...（用 groups.json 的 roomId 拉聊天）")


if __name__ == "__main__":
    main()
