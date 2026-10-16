"""覆盖模型与预警分档：读「中间契约」快照 → 产出 coverage.json。

**只读 `snapshot/`，不认识任何后台接口。** 接口换了只需换适配器，本文件不动。

四档（口径由用户 2026-10-15 定稿）
--------------------------------
  ⚠️ v4.18：「出勤」一律指**有效出勤**（到课 ＋ 未到课但看了回放）—— 与 `common.att_effective`
     同源。修正前这里读的是 `attendCount`（纯到课），把「看了回放」判成未出勤。

  失联            = 无聊天记录 且 无出勤记录（含看回放）
  出勤但无沟通     = 无聊天记录 且 有出勤记录（含看回放）
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
    # v4.16 锚点口径：anchor = min(全部消息最新时间, 报告窗口止)。
    #   窗口止 ≥ 最新消息时 = 旧行为（锚点即最新消息日），分档数字不受影响；
    #   只有用户指定了「历史时间段」（止日期早于最新消息）时才把锚点夹到窗口止，
    #   否则「近 chat-days 天」会滑出窗口（例：出 9 月报告、沟通轴却统计到 10 月上旬）。
    latest = max(all_ts) if all_ts else None
    anchor = (min(latest, end_dt) if (latest and end_dt)
              else (latest or end_dt or datetime.datetime.now()))
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
        n_tea_recent = 0          # 近窗老师发言数（预警卡「单向沟通」要摊开写）
        if room and room in chats:
            group_state = "found"
            for m in chats[room].get("messages") or []:
                d = parse_dt(m.get("ts"))
                if d is None or d < cutoff:
                    continue
                msg_recent += 1
                last_ts = m.get("ts")
                if m.get("role") == 1:
                    n_tea_recent += 1
                elif m.get("role") == 2 and not is_ack(m.get("text")):
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
            n_att, n_les, n_pb_only, n_pb = 0, 0, 0, 0
        else:
            # v4.18 修正：出勤口径改为**有效出勤（含看回放）**。
            #   修正前读的是 attendCount（纯到课数），于是「没到课但看了回放」的学生
            #   learn=False → 与「群内也无消息」叠加后被判成「失联」（实为「出勤但无沟通」）。
            #   实测联席视角 10 人、教务视角 16 人踩中，其中 5 人档位被写错。
            n_att, n_pb_only, n_pb, n_les = common.att_effective(att)
            att_state = "has" if n_att > 0 else "empty"
            att_txt = (f"出勤 {n_att}/{n_les}"
                       + (f"（含看回放补齐 {n_pb_only} 次）" if n_pb_only else ""))
            learn = n_att > 0

        brows = board_by_norm.get(norm) or []
        board_state, board_txt, board_ok = _board_state(brows, bool(boards))
        learn = learn or board_ok
        # v4.12：给预警卡留「数字」而非只留文案（判定逻辑一行未改，仅把已算出的量存下来）
        _bvalid = [r for r in brows if (r.get("status") or "").strip() not in BOARD_BLANK]
        board_n, board_all = len(_bvalid), len(brows)
        board_rounds = len({r.get("round") for r in brows if r.get("round")})

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
            # v4.12：联席 / 教务归属（来自花名册），供预警卡按维度归属与「谁的学生」标注
            "coTeacherName": stu.get("coTeacherName"),
            "classTeacherName": stu.get("classTeacherName"),
            "group": group_state,
            "chat": chat_state,
            "attendance": att_state,
            "board": board_state,
            "tier": tier,
            "cause": cause,
            "evidence": " ｜ ".join(ev),
            # ---- v4.12 结构化量（预警卡直接消费；不影响任何分档判定）----
            "msgRecent": msg_recent, "subRecent": sub_recent, "msgTeacher": n_tea_recent,
            "lastMsg": last_ts,
            # ⚠️ v4.18 起 `nAtt` = **有效出勤（含看回放）**，不再是纯到课数；
            #    纯到课数可用 nAtt - nPbOnly 反推。下游文案务必用 nPbOnly 把「回放补齐」写出来，
            #    否则「出勤 5/5」会被误读成 5 节都到课了。
            "nAtt": n_att, "nLes": n_les,
            "nPbOnly": n_pb_only,          # 未到课、仅靠回放补齐的节数（v4.18 新增）
            "nPb": n_pb,                   # 看回放总节数（含到课后回看，v4.18 新增）
            "boardN": board_n, "boardAll": board_all, "boardRounds": board_rounds,
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
    _anch_src = "窗口止" if (end_dt and (not latest or end_dt <= latest)) else "最新消息"
    print(f"   在册 {len(roster)} 人 ｜ 沟通轴近 {args.chat_days} 天（{cutoff.strftime('%Y-%m-%d')} ~ "
          f"{anchor.strftime('%Y-%m-%d')}，锚点取{_anch_src}）"
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
