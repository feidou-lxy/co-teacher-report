"""覆盖模型与预警分档：读「中间契约」快照 → 产出 coverage.json。

**只读 `snapshot/`，不认识任何后台接口。** 接口换了只需换适配器，本文件不动。

四档（口径由用户 2026-10-15 定稿）
--------------------------------
  失联            = 无聊天记录 且 无出勤记录
  出勤但无沟通     = 无聊天记录 且 有出勤记录
  单向沟通         = 有聊天记录，但学生·家长实质发言 = 0（仅老师输出）
  低互动           = 有聊天记录，学生·家长实质发言 1~2
  正常            = 其余

两轴窗口必须分开（否则误报）
--------------------------
  沟通轴（聊天/实质发言）：近两周（14 天滑动）—— 全月窗口会掩盖「近期才沉默」
  学习轴（出勤/小黑板）  ：报告窗口全窗口 —— 出勤本稀疏，只看近两周会把已结课学生全判「无出勤」

⚠️ 红线：群「采到与否」只作 `cause` 根因标签，**不作分档依据**。
  实测教务视角群列表有损（243 在册 / 群仅 144），拿群判失联会一次性误报 93 人。
"""
import argparse
import datetime
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, sys.path[0])
import common
from extract import is_ack, parse_dt

TIER_LOST = "失联"
TIER_ATTEND = "出勤但无沟通"
TIER_SINGLE = "单向沟通"
TIER_LOW = "低互动"
TIER_OK = "正常"
TIER_ORDER = [TIER_LOST, TIER_ATTEND, TIER_SINGLE, TIER_LOW, TIER_OK]

# 小黑板「空白未提交」属参与度问题，不算学习证据
BOARD_BLANK = {"空白未提交", "未提交", "空白", ""}


def _load(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _board_state(rows, has_source):
    """返回 (state, 文案, 是否可作学习证据)。

    区分三种「没有」：
      has   = 有非空白的有效作答                  → 学习证据
      blank = 有记录但全是「空白未提交」（人到了没写）→ 不作为证据，措辞写明
      none  = 该生无任何读图记录                  → 不说明「从未提交」，只说无记录
      n/a   = 完全没有小黑板数据源
    """
    if not has_source:
        return "n/a", "无小黑板数据源", False
    if not rows:
        return "none", "小黑板无记录", False
    valid = [r for r in rows if (r.get("status") or "").strip() not in BOARD_BLANK]
    if valid:
        return "has", f"小黑板作答 {len(valid)}/{len(rows)} 张", True
    return "blank", f"小黑板 {len(rows)} 张均为空白未提交", False


def _att_for(att_by_norm, norm, product):
    """出勤匹配：优先同课程，否则退回同名任意课程。"""
    rows = att_by_norm.get(norm) or []
    if not rows:
        return None
    for r in rows:
        if r.get("productName") and product and r["productName"] == product:
            return r
    return rows[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--snapshot", default=None, help="快照目录，默认 <workdir>/snapshot")
    ap.add_argument("--chat-days", type=int, default=14, help="沟通轴窗口天数（默认近两周）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    wd = common.workdir(args)
    snap = args.snapshot or os.path.join(wd, "snapshot")

    roster = _load(os.path.join(snap, "roster.json"), []) or []
    groups = _load(os.path.join(snap, "groups.json"), []) or []
    chats = _load(os.path.join(snap, "chats.json"), {}) or {}
    attendance = _load(os.path.join(snap, "attendance.json"), []) or []
    boards = _load(os.path.join(snap, "boards.json"), []) or []
    meta = _load(os.path.join(snap, "meta.json"), {}) or {}

    if not roster:
        raise SystemExit(f"❌ {snap}/roster.json 为空 —— 请先跑 adapt_legacy.py 生成快照")

    # ---------- 索引 ----------
    room_by_norm = {}
    for g in groups:
        n = g.get("studentNameNorm")
        if n and n not in room_by_norm:
            room_by_norm[n] = g.get("roomId")

    att_by_norm = defaultdict(list)
    for a in attendance:
        att_by_norm[a.get("studentNameNorm")].append(a)

    board_by_norm = defaultdict(list)
    for b in boards:
        board_by_norm[b.get("studentNameNorm")].append(b)

    # ---------- 沟通轴锚点与切点 ----------
    all_ts = []
    for blk in chats.values():
        for m in blk.get("messages") or []:
            d = parse_dt(m.get("ts"))
            if d:
                all_ts.append(d)
    end = meta.get("window", {}).get("endDate")
    end_dt = parse_dt((end or "") + " 23:59:59") if end else None
    anchor = max(all_ts) if all_ts else (end_dt or datetime.datetime.now())
    cutoff = anchor - datetime.timedelta(days=args.chat_days)

    # ---------- 逐人判定 ----------
    cov, counts = {}, Counter()
    for stu in roster:
        norm = stu.get("stuNameNorm") or common.norm_name(stu.get("stuName"))
        product = stu.get("productName")
        key = f"{product}::{norm}"

        room = stu.get("roomId") or room_by_norm.get(norm)

        # 沟通轴（近 chat-days 天）
        sub_recent, msg_recent, last_ts = 0, 0, None
        if room and room in chats:
            group_state = "found"
            for m in chats[room].get("messages") or []:
                d = parse_dt(m.get("ts"))
                if d is None or d < cutoff:
                    continue
                msg_recent += 1
                last_ts = m.get("ts")
                if m.get("role") == 2 and not is_ack(m.get("text")):
                    sub_recent += 1
            chat_state = "has" if msg_recent else "empty"
        elif room:
            group_state, chat_state = "found", "empty"
        else:
            group_state, chat_state = "missing", "n/a"

        # 学习轴（全窗口）
        att = _att_for(att_by_norm, norm, product)
        if att is None:
            att_state = "n/a"
            att_txt = "出勤接口无记录"
            learn = False
        else:
            n_att = att.get("attendCount") or 0
            n_les = att.get("lessonCount") or 0
            att_state = "has" if n_att > 0 else "empty"
            att_txt = f"出勤 {n_att}/{n_les}"
            learn = n_att > 0

        brows = board_by_norm.get(norm) or []
        board_state, board_txt, board_ok = _board_state(brows, bool(boards))
        learn = learn or board_ok

        # ---------- 分档 ----------
        if chat_state in ("empty", "n/a"):
            cause = "group_missing" if group_state == "missing" else "group_empty"
            tier = TIER_ATTEND if learn else TIER_LOST
        elif sub_recent == 0:
            tier, cause = TIER_SINGLE, "single_way"
        elif sub_recent <= 2:
            tier, cause = TIER_LOW, "ok"
        else:
            tier, cause = TIER_OK, "ok"

        ev = [att_txt, board_txt]
        if group_state == "missing":
            ev.append("企微群未采集到")
        elif msg_recent == 0:
            ev.append(f"群已建但近 {args.chat_days} 天零消息")
        else:
            ev.append(f"近 {args.chat_days} 天 {msg_recent} 条消息、学生·家长实质 {sub_recent} 条")
        if last_ts:
            ev.append(f"最近消息 {last_ts[:10]}")

        cov[key] = {
            "productName": product,
            "stuName": stu.get("stuName"),
            "stuNameNorm": norm,
            "roomId": room,
            "group": group_state,
            "chat": chat_state,
            "attendance": att_state,
            "board": board_state,
            "tier": tier,
            "cause": cause,
            "evidence": " ｜ ".join(ev),
        }
        counts[tier] += 1

    # ---------- 守恒校验 ----------
    assert len(cov) == len(roster), f"覆盖率 {len(cov)} != 在册 {len(roster)}"
    assert sum(counts.values()) == len(roster), f"分档合计 {sum(counts.values())} != 在册 {len(roster)}"

    out = args.out or os.path.join(wd, "coverage.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cov, f, ensure_ascii=False, indent=1)

    # ---------- 摘要 ----------
    print(f"✅ {out}")
    print(f"   在册 {len(roster)} 人 ｜ 沟通轴近 {args.chat_days} 天（锚点 {anchor.strftime('%Y-%m-%d')}）"
          f" ｜ 学习轴全窗口 {meta.get('window', {}).get('startDate')}~{meta.get('window', {}).get('endDate')}")
    print()
    print(f"   {'档位':<12}{'人数':>6}{'占比':>8}")
    for t in TIER_ORDER:
        n = counts.get(t, 0)
        print(f"   {t:<12}{n:>6}{n/len(roster)*100:>7.1f}%")

    by_cause = Counter(v["cause"] for v in cov.values())
    print()
    print("   根因分布：" + " ｜ ".join(f"{k} {v}" for k, v in by_cause.most_common()))

    by_course = defaultdict(Counter)
    for v in cov.values():
        by_course[v["productName"]][v["tier"]] += 1
    if len(by_course) > 1:
        print()
        print(f"   {'课程':<44}{'在册':>5}{'失联':>6}{'出勤无沟通':>10}")
        for p, c in sorted(by_course.items(), key=lambda kv: -sum(kv[1].values())):
            tot = sum(c.values())
            print(f"   {str(p)[:42]:<44}{tot:>5}{c.get(TIER_LOST,0):>6}{c.get(TIER_ATTEND,0):>10}")

    # 细节清单（便于回群/回后台核对）
    for t in TIER_ORDER[:2]:
        hits = [v for v in cov.values() if v["tier"] == t]
        if not hits:
            continue
        print()
        print(f"   — {t}（{len(hits)}）—")
        for v in hits[:40]:
            print(f"     {v['stuName']:<10} {v['evidence']}")
        if len(hits) > 40:
            print(f"     … 另 {len(hits)-40} 人见 coverage.json")


if __name__ == "__main__":
    main()
