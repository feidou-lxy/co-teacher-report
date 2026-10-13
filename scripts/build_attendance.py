"""步骤5：清洗出勤原始行 -> 每个学生每节课的去重聚合。

按 (studentName, classId) 去重（保留信息最全的一行：有回放时长 > 有回放标记 > 已出勤）。
逐学生聚合为 classes[]，并统计 出勤/缺勤/回放 节数。

输出 <workdir>/attendance_clean.json ：{ 归一化姓名: {
  name, course, product, lessonCount, attendCount, playbackCount, playbackOnlyCount,
  effectiveAttendCount, absentCount, classes:[...] } }
classes[i] = {classSeq, classId, timeStart(YYYY-MM-DD), isAttend, isPlayback, playbackValidTime}

v3.8 口径：**看回放也算有效出勤**（补课已闭环）。
  attendCount          到课节数
  playbackCount        看回放节数（含到课后回看）
  playbackOnlyCount    未到课、仅靠回放补上的节数
  effectiveAttendCount 有效出勤 = 到课 + 未到课但已看回放
  absentCount          真缺课 = 既未到课也未看回放（修正前：未到课即算缺，会把已看回放的误判为缺课）
"""
import argparse
import json
import re
import sys
from collections import defaultdict

sys.path.insert(0, sys.path[0])
import common


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", default=None)
    ap.add_argument("--edu", default=None)
    ap.add_argument("--course", default=None)
    args = ap.parse_args()
    wd = common.workdir(args)
    rows = json.load(open(f"{wd}/attendance_raw.json", encoding="utf-8"))

    def score(x):
        return ((1 if x.get("playbackTotalValidTime") else 0)
                + (1 if x.get("isPlayback") == 1 else 0)
                + (1 if x.get("isAttend") == 1 else 0))

    best = {}
    for r in rows:
        key = (r.get("studentName"), r.get("classId"))
        prev = best.get(key)
        if prev is None or score(r) > score(prev):
            best[key] = r

    stu = defaultdict(lambda: {"name": "", "norm": "", "product": "", "course": "", "classes": {}})
    for (nm, cid), r in best.items():
        prod = (r.get("cProduct") or {}).get("name") or ""
        k = common.norm_name(nm)
        s = stu[k]
        s["name"] = nm
        s["norm"] = k
        s["product"] = prod
        s["course"] = common.clean_course_name(prod)
        seq = r.get("classSeq")
        if seq is None:
            seq = len(s["classes"]) + 1
        prev = s["classes"].get(seq)
        if prev is None or (r.get("isAttend") == 1 and prev.get("isAttend") != 1):
            s["classes"][seq] = {
                "classSeq": seq,
                "classId": cid,
                "timeStart": (r.get("timeStart") or "")[:10],
                "isAttend": r.get("isAttend"),
                "isPlayback": r.get("isPlayback"),
                "playbackValidTime": r.get("playbackTotalValidTime"),
            }

    out = {}
    for k, s in stu.items():
        cls = list(s["classes"].values())
        cls.sort(key=lambda x: x["classSeq"])
        n = len(cls)
        att = sum(1 for c in cls if c["isAttend"] == 1)                          # 到课
        pb = sum(1 for c in cls if c["isPlayback"] == 1)                         # 看回放（含到课后回看）
        pb_only = sum(1 for c in cls if c["isAttend"] != 1 and c["isPlayback"] == 1)  # 未到课、靠回放补上
        # v3.8 口径修正：看回放也算有效出勤（补课已闭环），只有「既未到课也没看回放」才算真缺课
        eff_att = att + pb_only
        absent = n - eff_att
        out[k] = {
            "name": s["name"],
            "course": s["course"],
            "product": s["product"],
            "lessonCount": n,
            "attendCount": att,                 # 到课节数
            "playbackCount": pb,                # 看回放节数（全部）
            "playbackOnlyCount": pb_only,       # 未到课、仅靠回放补上的节数
            "effectiveAttendCount": eff_att,    # 有效出勤 = 到课 + 未到课但已看回放
            "absentCount": absent,              # 真缺课 = 既未到课也未看回放
            "classes": cls,
        }

    json.dump(out, open(f"{wd}/attendance_clean.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"✅ 清洗后学生 {len(out)} 人 -> attendance_clean.json")
    from collections import Counter
    c = Counter(v["absentCount"] for v in out.values())
    print("缺勤节数分布 {缺:人数}:", dict(sorted(c.items())))


if __name__ == "__main__":
    main()
