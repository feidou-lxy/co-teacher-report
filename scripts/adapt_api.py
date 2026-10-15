"""适配器（新接口版 v4.11）：把研发封装的「在读学生」接口产物折算成「中间契约」快照。

与 adapt_legacy.py 的唯一差别，就在 **roster / groups** 这两张表的来源：
    旧：花名册无 roomId → 用「群名内学生姓名」反查（降级路径，群列表接口还有损）
    新：接口**直接给出** roomId（学生课程关系 → 服务群，取 last_message_time 最新）
其余表（chats / groupActivity / attendance / boards）**仍走旧方式**，按用户口径复用
`adapt_legacy.py` 的同一批函数 —— 保证两套适配器产出的快照形态 100% 一致，分析层零改动。

输入（<workdir>/）
    roster_api_raw.json    接口原始响应（由 pull_roster.py 落盘）★ 若有则优先用它
    students.json          由 pull_roster.py 落盘的花名册（含 roomId），兜底来源
    chats.json / attendance_clean.json / board_analysis.json  旧方式产物（不变）

输出（<workdir>/snapshot/）：与 adapt_legacy.py 完全相同的 7 个文件。
契约定义见 references/data_contract.md。

⚠️ 铁律：缺失必须显式。新接口没给的字段（productStudentId / teacherName / schoolName /
   grade / groupName）一律写 None 并进 `degraded[]`，**绝不臆造**。
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, sys.path[0])
import common
import adapt_legacy as L          # 复用 chats/activity/attendance/boards 的构建函数

TZ = "+08:00"


def _load(wd, name, default=None):
    return L._load(wd, name, default)


# ---------------------------------------------------------------- roster / groups
def build_roster(rows):
    """在册名单 —— roomId **直接来自接口**，不再靠群名反查（本轮最大改进）。"""
    out = []
    for x in rows:
        pn = x.get("productName") or ""
        out.append({
            "stuName": x.get("stuName"),
            "stuNameNorm": common.norm_name(x.get("stuName")),
            "stuSeq": x.get("stuSeq"),
            "productName": pn,
            "productStudentId": x.get("productStudentId"),   # 接口未提供 -> None
            "ProductId": x.get("ProductId"),
            "coTeacherName": x.get("coTeacherName"),
            "classTeacherName": x.get("classTeacherName"),
            "teacherName": x.get("teacherName"),             # 接口未提供 -> None（主讲）
            "subjectName": common.subject_of(pn),
            # 接口给中文「直播/回放」，cIncomeProductType 已由 pull_roster 反推；
            # 两者都没有时回退取 purchaseType 原串（buy_type_label 对中文原样返回）
            "buyType": (common.buy_type_label(x.get("cIncomeProductType"))
                        or x.get("purchaseType") or ""),
            "schoolName": x.get("schoolName"),               # 接口未提供 -> None
            "roomId": x.get("roomId"),
            # 溯源：api（接口直给）/ fill_groupname（群名匹配补全）/ None（无群）
            "roomIdSource": x.get("roomIdSource") or ("api" if x.get("roomId") else None),
            # 接口已按 product_student_status=1 过滤 → 名单内一律「在读」
            "enrollStatus": 1,
        })
    return out


def build_groups(rows):
    """群标识 —— 从 roster 的 roomId 派生（接口不返回群名，groupName 置空）。"""
    out = []
    for x in rows:
        room = x.get("roomId")
        if not room:
            continue
        out.append({
            "roomId": room,
            "studentName": x.get("stuName"),
            "studentNameNorm": common.norm_name(x.get("stuName")),
            "groupName": "",                       # 接口未提供；不用于任何判定
            "productName": x.get("productName") or "",
            "coTeacherName": x.get("coTeacherName") or "",
            "classTeacherName": x.get("classTeacherName") or "",
            "ownerName": x.get("coTeacherName") or "",
            "subjectName": common.subject_of(x.get("productName") or ""),
            "roomIdSource": x.get("roomIdSource") or "api",
        })
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--out", default=None, help="快照目录，默认 <workdir>/snapshot")
    ap.add_argument("--co", default=None)
    ap.add_argument("--edu", default=None)
    ap.add_argument("--course", default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    wd = common.workdir(args)
    out_dir = args.out or os.path.join(wd, "snapshot")
    os.makedirs(out_dir, exist_ok=True)

    raw = _load(wd, "roster_api_raw.json", None)
    rows = _load(wd, "students.json", None)
    if rows:
        # ★ 优先用 students.json：它是 pull_roster.py 的**最终产物**，
        #   含 `--fill` 的群名补全结果与 roomIdSource 溯源标记。
        #   （roster_api_raw.json 只是接口原始响应，不含补全，仅作兜底/溯源。）
        if not any("roomId" in x for x in rows):
            raise SystemExit(
                f"❌ {wd}/students.json 里没有 roomId 字段 —— 这不是 pull_roster.py 的产物。\n"
                f"   旧链路请用 adapt_legacy.py；新接口链路请先跑 pull_roster.py。")
        src_note = "students.json（pull_roster.py 产物，含补全）"
    elif raw and (raw.get("list") or []):
        rows = [{
            "stuName": x.get("studentName"),
            "stuSeq": x.get("studentSeq"),
            "productName": x.get("productName"),
            "productStudentId": None,
            "ProductId": x.get("productId"),
            "teacherName": None,
            "classTeacherName": x.get("classTeacherName") or "",
            "schoolName": None,
            "coTeacherName": x.get("coTeacherName") or "",
            "cIncomeProductType": {"直播": 1, "回放": 2}.get((x.get("purchaseType") or "").strip()),
            "purchaseType": (x.get("purchaseType") or "").strip(),
            "roomId": (x.get("roomId") or "").strip() or None,
            "roomIdSource": "api" if (x.get("roomId") or "").strip() else None,
        } for x in raw["list"]]
        src_note = "roster_api_raw.json（接口原始响应，无补全）"
    else:
        rows = []

    if not rows:
        raise SystemExit(
            f"❌ {wd}/roster_api_raw.json 与 students.json 均为空。\n"
            f"   请先跑：python scripts/pull_roster.py --workdir {wd} --course/--co <名称>")

    raw_groups = _load(wd, "groups.json", []) or []
    raw_chats = _load(wd, "chats.json", {}) or {}
    att = _load(wd, "attendance_clean.json", {}) or {}
    boards = _load(wd, "board_analysis.json", None)
    if boards is None:
        boards = _load(wd, "boards_analysis.json", []) or []

    roster = build_roster(rows)
    # groups 一律以 roster 派生为准（接口 roomId 是权威）；仅当它为空的极端情况才回退文件
    groups = build_groups(rows) or L.build_groups(raw_groups)
    chats, activity = L.build_chats_and_activity(raw_chats)
    attendance = L.build_attendance(att)
    board_rows = L.build_boards(boards)

    # ---------------- 一致性核查 ----------------
    room_ids = {g["roomId"] for g in groups}
    orphan = [r for r in chats if r not in room_ids]
    roster_rooms = [x["roomId"] for x in roster if x["roomId"]]

    degraded = []
    if not any(g.get("groupName") for g in groups):
        degraded.append({
            "field": "groups[].groupName",
            "reason": "新接口不返回群名",
            "impact": "无 —— 报告一律以后台课程名 productName 为准（v4.7 起已不用群名判课程），"
                      "群名不参与任何判定与展示",
        })
    missing_f = [f for f in ("productStudentId", "teacherName", "schoolName")
                 if all(x.get(f) is None for x in roster)]
    if missing_f:
        degraded.append({
            "field": "roster[]." + " / roster[].".join(missing_f),
            "reason": "新接口未提供这些字段",
            "impact": "无 —— 三者在本技能全流程均无下游消费（出勤按 ProductId 拉取，"
                      "主讲/校区未参与任何判定）",
        })
    degraded.append({
        "field": "groupActivity.msgStudent / msgParent",
        "reason": "聊天接口 role 只分「老师/学生家长」，无法细分为学生本人与家长",
        "impact": "「实质发言数」为高估值（含家长）；家长侧结论只能作中性描述",
    })
    if orphan:
        degraded.append({
            "field": "chats{}",
            "reason": f"{len(orphan)} 个 roomId 不在 groups 表中",
            "impact": "该群学生未进在册名单，消息不参与分析",
        })
    # ⚠️ 与旧适配器的差别：**没有**「roster[].roomId 缺失」这条 —— 接口已直给
    n_api = sum(1 for x in roster if x.get("roomIdSource") == "api")
    n_fill = sum(1 for x in roster if x.get("roomIdSource") == "fill_groupname")
    if n_fill:
        degraded.append({
            "field": "roster[].roomId（来源=群名补全）",
            "reason": f"{n_fill} 人的 roomId 并非接口直给，而是由**群名内姓名匹配**补全 —— "
                      f"接口的「学生课程关系 → 服务群」未关联到该群。实测成因：群名与后台课程名"
                      f"不一致时关联失败（例：群名「高二数学清北层E邵」缺「日」字）",
            "impact": f"这 {n_fill} 人的群聊证据与旧链路同源（已逐条核对为真实群，优于漏判），"
                      f"但存在错配可能；建议向研发反馈该关联缺陷",
        })
    if not roster_rooms:
        degraded.append({
            "field": "roster[].roomId",
            "reason": "接口返回的 roomId 全为空串（学生课程关系未关联到服务群）",
            "impact": "全部学生无法拉到群聊 → 沟通轴判据缺失，只能依赖出勤判档",
        })

    meta = {
        "generatedAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + TZ,
        "source": "api",
        "endpoint": (raw or {}).get("endpoint"),
        "endpointParams": (raw or {}).get("params"),
        "rosterFrom": src_note,
        "scope": {
            "type": "course" if args.course else ("edu" if args.edu else ("co" if args.co else "unknown")),
            "value": args.course or args.edu or args.co or "",
            "extra": {"co": args.co if args.course else None},
        },
        "window": {"startDate": args.start, "endDate": args.end},
        "counts": {
            "roster": len(roster), "groups": len(groups), "groupActivity": len(activity),
            "chats": len(chats), "attendance": len(attendance), "boards": len(board_rows),
        },
        "roomIdCoverage": {
            "withRoom": len(roster_rooms), "total": len(roster),
            "pct": round(len(roster_rooms) / len(roster) * 100, 1) if roster else 0.0,
            "bySource": {"api": n_api, "fill_groupname": n_fill,
                         "none": len(roster) - len(roster_rooms)},
        },
        "degraded": degraded,
    }

    for name, obj in [("meta", meta), ("roster", roster), ("groups", groups),
                      ("groupActivity", list(activity.values())), ("chats", chats),
                      ("attendance", attendance), ("boards", board_rows)]:
        with open(os.path.join(out_dir, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)

    print(f"✅ 快照已写入 {out_dir}/（source=api，roster 取自 {src_note}）")
    for k, v in meta["counts"].items():
        print(f"   {k:<15}{v}")
    cov = meta["roomIdCoverage"]
    bs = cov["bySource"]
    print(f"   roomId 覆盖     {cov['withRoom']}/{cov['total']} = {cov['pct']}%"
          f"（接口直给 {bs['api']} ｜ 群名补全 {bs['fill_groupname']} ｜ 无群 {bs['none']}）")
    if orphan:
        print(f"   ⚠️ {len(orphan)} 个 roomId 不在 groups 表")
    print(f"   降级声明 {len(degraded)} 项 → snapshot/meta.json")
    print(f"\n下一步：python scripts/coverage.py --workdir {wd}")


if __name__ == "__main__":
    main()
