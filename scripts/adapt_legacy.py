"""适配器（现有链路版）：把现有取数脚本的原始产物折算成「中间契约」快照。

为什么需要这一层
----------------
现有取数要拼 7 个接口，将来研发会统一封装。**分析层不应该认识任何一个接口。**
本文件是「现有链路」对契约的实现；将来新接口到位后，只需再写一个 `adapt_api.py`
输出**同一份** `snapshot/`，分析层（coverage / extract / report）一个字都不用改。

输入（<workdir> 下的原始产物，由 pull_* 生成）
    students.json          花名册（在册名单，权威分母）
    groups.json            群标识
    chats.json             群消息 {学生名: {group, total, messages}}
    attendance_clean.json  出勤 {归一化姓名: {...}}
    board_analysis.json    小黑板读图结果（可选，list）

输出（<workdir>/snapshot/）
    meta.json  roster.json  groups.json  groupActivity.json
    chats.json  attendance.json  boards.json

契约定义见 references/data_contract.md。
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, sys.path[0])
import common
from extract import is_ack, parse_dt

TZ = "+08:00"


def _iso(ts):
    """统一时间戳为 ISO8601 带时区；解析失败返回 None（不猜）。"""
    d = parse_dt(ts)
    if d is None:
        return None
    return d.strftime("%Y-%m-%dT%H:%M:%S") + TZ


def _load(wd, name, default=None):
    p = os.path.join(wd, name)
    if not os.path.exists(p):
        return default
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  ⚠️ 读取 {name} 失败：{e}")
        return default


# ---------------------------------------------------------------- roster
def build_roster(students, groups):
    """在册名单。roomId 由「群名内学生姓名」反查 —— 这是**降级路径**（后台未给映射）。"""
    room_by_norm = {}
    for g in groups:
        n = common.norm_name(g.get("studentName"))
        if n and n not in room_by_norm:
            room_by_norm[n] = g.get("roomId")

    out, no_room = [], 0
    for x in students:
        nm = x.get("stuName")
        norm = common.norm_name(nm)
        room = room_by_norm.get(norm)
        if not room:
            no_room += 1
        out.append({
            "stuName": nm,
            "stuNameNorm": norm,
            "stuSeq": x.get("stuSeq"),
            "productName": x.get("productName"),
            "productStudentId": x.get("productStudentId"),
            "ProductId": x.get("ProductId"),
            "coTeacherName": x.get("coTeacherName"),
            "classTeacherName": x.get("classTeacherName"),
            "teacherName": x.get("teacherName"),
            "subjectName": common.subject_of(x.get("productName") or ""),
            "buyType": common.buy_type_label(x.get("cIncomeProductType")),
            "schoolName": x.get("schoolName"),
            "roomId": room,
            "enrollStatus": None,
        })
    return out, no_room


# ---------------------------------------------------------------- groups
def build_groups(raw_groups):
    out = []
    for g in raw_groups:
        out.append({
            "roomId": g.get("roomId"),
            "studentName": g.get("studentName"),
            "studentNameNorm": common.norm_name(g.get("studentName")),
            "groupName": g.get("groupName"),
            "productName": g.get("productName"),
            "coTeacherName": g.get("coTeacherName"),
            "classTeacherName": g.get("classTeacherName"),
            "ownerName": g.get("ownerName"),
            "subjectName": g.get("subjectName"),
        })
    return out


# ---------------------------------------------------------------- chats / activity
def build_chats_and_activity(raw_chats):
    """同时产出消息明细表与群活跃度聚合表（后者是失联预警的核心数据源）。"""
    chats, activity = {}, {}
    for _student, rec in (raw_chats or {}).items():
        grp = rec.get("group") or {}
        room = grp.get("roomId")
        if not room:
            continue
        msgs, seen = [], set()
        for m in rec.get("messages") or []:
            seq = m.get("seq")
            if seq in seen:
                continue
            seen.add(seq)
            content = m.get("content")
            if isinstance(content, dict):
                text = content.get("content") or content.get("text") or ""
            else:
                text = content or ""
            msgs.append({
                "seq": seq,
                "ts": _iso(m.get("msgTime")),
                "role": m.get("role"),
                "speakerName": m.get("name"),
                "speakerRole": None,          # 现有接口无法区分学生/家长 → 见 degraded
                "msgType": m.get("msgType"),
                "text": text,
            })
        msgs.sort(key=lambda x: (x["ts"] or ""))
        # 合流：同一 roomId 可能被多个学生条目引用
        prev = chats.setdefault(room, {"messages": []})
        if msgs:
            known = {m["seq"] for m in prev["messages"]}
            prev["messages"].extend([m for m in msgs if m["seq"] not in known])
            prev["messages"].sort(key=lambda x: (x["ts"] or ""))

    for room, blk in chats.items():
        msgs = blk["messages"]
        teachers = [m for m in msgs if m.get("role") == 1]
        students = [m for m in msgs if m.get("role") == 2]
        # 学生/家长无法细分 → 「实质发言」= 非礼节语的全部学生侧消息，属**高估**
        substantive = [m for m in students if not is_ack(m.get("text"))]
        times = [m["ts"] for m in msgs if m["ts"]]
        activity[room] = {
            "roomId": room,
            "msgTotal": len(msgs),
            "msgTeacher": len(teachers),
            "msgStudent": None,        # 需 speakerRole → 见 degraded
            "msgParent": None,
            "lastMsgTime": times[-1] if times else None,
            "firstMsgTime": times[0] if times else None,
            "studentSubstantive": len(substantive),
            "parentSubstantive": None,
        }
    return chats, activity


# ---------------------------------------------------------------- attendance / boards
def build_attendance(att):
    out = []
    for norm, r in (att or {}).items():
        # v4.18：快照里把「有效出勤（含看回放）」一并算好固化，下游不必各自回退计算，
        #   也避免源产物缺字段时被误当成「没出勤」。口径定义见 common.att_effective。
        eff, pb_only, pb, _ = common.att_effective(r)
        out.append({
            "studentNameNorm": common.norm_name(r.get("name") or norm),
            "studentName": r.get("name") or norm,
            "productName": r.get("product"),
            "courseName": r.get("course"),
            "lessonCount": r.get("lessonCount"),
            "attendCount": r.get("attendCount"),          # 纯到课数（不含回放）
            "absentCount": r.get("absentCount"),          # 真缺课（已含回放口径）
            "playbackCount": pb,
            "effectiveAttendCount": eff,                  # 有效出勤 = 到课 + 仅回放补齐
            "playbackOnlyCount": pb_only,                 # 未到课、仅靠回放补齐的节数
            "classes": r.get("classes") or [],
        })
    return out


def build_boards(boards):
    out = []
    for b in boards or []:
        stu = b.get("student")
        if not stu:
            continue
        out.append({
            "studentNameNorm": common.norm_name(stu),
            "studentName": stu,
            "path": b.get("path"),
            "round": b.get("round"),
            "lessonDate": b.get("lesson_date"),
            "problem": b.get("problem"),
            "topicKnowledge": b.get("topic_knowledge") or b.get("topic"),
            "status": b.get("status"),
            "correct": b.get("correct"),
            "errors": b.get("errors") or [],
            "stuckAt": b.get("stuck_at"),
            "note": b.get("note"),
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
    ap.add_argument("--start", default=None, help="报告窗口起（YYYY-MM-DD）")
    ap.add_argument("--end", default=None, help="报告窗口止（YYYY-MM-DD）")
    args = ap.parse_args()

    wd = common.workdir(args)
    out_dir = args.out or os.path.join(wd, "snapshot")
    os.makedirs(out_dir, exist_ok=True)

    students = _load(wd, "students.json", []) or []
    raw_groups = _load(wd, "groups.json", []) or []
    raw_chats = _load(wd, "chats.json", {}) or {}
    att = _load(wd, "attendance_clean.json", {}) or {}
    boards = _load(wd, "board_analysis.json", None)
    if boards is None:
        boards = _load(wd, "boards_analysis.json", []) or []

    if not students:
        raise SystemExit(f"❌ {wd}/students.json 为空或不存在 —— 适配器的分母来自花名册，必须先跑 pull_students.py")

    roster, no_room = build_roster(students, raw_groups)
    groups = build_groups(raw_groups)
    chats, activity = build_chats_and_activity(raw_chats)
    attendance = build_attendance(att)
    board_rows = build_boards(boards)

    # 校验：chats 的 key 必须都在 groups 里
    room_ids = {g["roomId"] for g in groups}
    orphan = [r for r in chats if r not in room_ids]

    degraded = []
    if no_room:
        degraded.append({
            "field": "roster[].roomId",
            "reason": f"后台未提供学生↔群映射，改由群名内姓名匹配；{no_room}/{len(roster)} 人未匹配到群",
            "impact": "群采到与否只能作根因标签，不能作分档依据 → 分档改用「出勤×聊天」两轴",
        })
    if any(g.get("productName") is None for g in groups):
        degraded.append({
            "field": "groups[].productName",
            "reason": "原始 groups.json 未含后台课程名（v4.7 前的归档产物）",
            "impact": "课程维度过滤需回退群名解析，可能漏人",
        })
    degraded.append({
        "field": "groupActivity.msgStudent / msgParent",
        "reason": "现有接口 role 只分「老师/学生家长」，无法细分为学生本人与家长",
        "impact": "「实质发言数」为高估值（含家长）；家长侧结论只能作中性描述",
    })
    if orphan:
        degraded.append({
            "field": "chats{}",
            "reason": f"{len(orphan)} 个 roomId 不在 groups 表中",
            "impact": "该群学生未进在册名单，消息不参与分析",
        })

    meta = {
        "generatedAt": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + TZ,
        "source": "legacy",
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
        "degraded": degraded,
    }

    for name, obj in [("meta", meta), ("roster", roster), ("groups", groups),
                      ("groupActivity", list(activity.values())), ("chats", chats),
                      ("attendance", attendance), ("boards", board_rows)]:
        with open(os.path.join(out_dir, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)

    print(f"✅ 快照已写入 {out_dir}/")
    for k, v in meta["counts"].items():
        print(f"   {k:<15}{v}")
    if no_room:
        print(f"   ⚠️ {no_room} 人未匹配到群（占 {(no_room/len(roster)*100):.0f}%）→ 计入根因标签，不影响分档")
    if orphan:
        print(f"   ⚠️ {len(orphan)} 个 roomId 不在 groups 表")
    print(f"   降级声明 {len(degraded)} 项 → snapshot/meta.json")


if __name__ == "__main__":
    main()
