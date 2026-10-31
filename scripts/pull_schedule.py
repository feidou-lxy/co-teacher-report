#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉「课程下每节课的上课时间」→ snapshot/cClasses.json（v4.53）。

为什么需要：研发接口新增了学生 `joinTime`（进班时间），但「进班」是相对**课节**才有意义 ——
只有知道第几节课在哪一天上，才能算出「这名学生进班后真正赶上了哪几节」。
否则转班新生会被算成「缺了进班前的 2 节课 + 没交进班前的 3 次作业」，
在联席/教务看来就是凭空多出来一个「既无出勤也无作业」的假问题学生。

接口（研发提供）：
    GET /cClass/getCClassList?page=1&pageSize=50&productStatus=-1
        &isClassinCallback=2&classroomDeliveryType=all&productId=<productId>
    → data.list[]：{classSeq, classDate, classBeginTime, classEndTime, classStatus, classinClassName, ...}
    ⚠️ 关键字段是 `classSeq`（第几节课）与 `classBeginTime`（这节课实际开始时间）。
       飞书表的「第 N 次课」列与此 classSeq 一一对应（本课实测 18 节 = 表格 18 课次）。

输出：<workdir>/snapshot/cClasses.json
    {"<productId>": {"productName": "...", "classes": [{seq, date, begin, end, status}]},
     "_meta": {"fetchedAt": "...", "n": {...}}}
"""
import argparse
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common


def fetch_classes(token, pid, page_size=50, retry=3):
    out, page = [], 1
    while True:
        q = urllib.parse.urlencode({
            "page": page, "pageSize": page_size, "productStatus": -1,
            "isClassinCallback": 2, "classroomDeliveryType": "all", "productId": pid,
        })
        last = None
        for i in range(retry):
            try:
                r = common.get("/cClass/getCClassList?" + q, token)
                if r.get("code") == 0:
                    break
                last = RuntimeError(f"code={r.get('code')} msg={r.get('msg')}")
            except Exception as e:      # noqa: BLE001
                last = e
            time.sleep(2)
        else:
            raise SystemExit(f"❌ 课节接口失败 productId={pid}：{last}")
        d = r.get("data") or {}
        lst = d.get("list") or []
        out.extend(lst)
        total = int(d.get("total") or 0)
        if not lst or len(out) >= total:
            break
        page += 1
    rows = []
    for x in out:
        rows.append({
            "seq": x.get("classSeq"),
            "date": str(x.get("classDate") or "")[:10],
            "begin": x.get("classBeginTime") or "",
            "end": x.get("classEndTime") or "",
            "status": x.get("classStatus"),
            "name": x.get("classinClassName") or "",
            "classId": x.get("ID"),
        })
    rows.sort(key=lambda r: (r["seq"] or 0))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--product-id", type=int, default=None,
                    help="不传则从 snapshot/roster.json 的 ProductId 推断（多条则全部拉）")
    ap.add_argument("--product-name", default=None, help="按课程名在 roster 里找 ProductId")
    args = ap.parse_args()

    wd = common.workdir(args)
    token = common.load_token(wd)

    pids = {}
    if args.product_id:
        pids[args.product_id] = args.product_name or ""
    else:
        p = os.path.join(wd, "snapshot", "roster.json")
        if not os.path.exists(p):
            raise SystemExit(f"❌ 找不到 {p}；先跑 pull_roster.py")
        roster = json.load(open(p, encoding="utf-8"))
        for s in roster:
            pid = s.get("ProductId")
            pn = s.get("productName") or ""
            if not pid:
                continue
            if args.product_name and pn != args.product_name:
                continue
            pids.setdefault(pid, pn)
    if not pids:
        raise SystemExit("❌ 没有可拉的 productId（roster 里 ProductId 全空？）")

    out = {}
    for pid, pn in pids.items():
        rows = fetch_classes(token, pid)
        out[str(pid)] = {"productName": pn, "classes": rows}
        print(f"✅ productId={pid} {pn} ｜ {len(rows)} 节课节 ｜ "
              f"第一节课 {rows[0]['date'] if rows else '—'} ｜ 最后一节 {rows[-1]['date'] if rows else '—'}")

    out["_meta"] = {"fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "n": {k: len(v["classes"]) for k, v in out.items() if k != "_meta"}}
    op = os.path.join(wd, "snapshot", "cClasses.json")
    tmp = op + ".tmp"
    json.dump(out, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, op)
    print(f"→ {op}")


if __name__ == "__main__":
    main()
