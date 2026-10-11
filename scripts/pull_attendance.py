"""步骤4：拉取该联席名下学生的逐节课出勤（后台权威）。

接口 cClassAttend/getCClassAttendList?productId=<X>（按学生花名册里的 ProductId 逐个拉，
再按 coTeacherName 二次过滤）。每行列：studentName, classSeq, classId, timeStart,
isAttend(1/0), isLate, isGone, isPlayback, playbackTotalValidTime, cProduct.name,
coTeacherName, classTeacherName。

输出 <workdir>/attendance_raw.json ：[...rows]
"""
import argparse
import json
import sys
import time

sys.path.insert(0, sys.path[0])
import common


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", default=None, help="联席姓名（联席视角）")
    ap.add_argument("--edu", default=None, help="教务姓名（教务视角）")
    args = ap.parse_args()
    wd = common.workdir(args)
    token = common.load_token(wd)
    students = json.load(open(f"{wd}/students.json", encoding="utf-8"))

    pids = []
    for x in students:
        pid = x.get("ProductId")
        if pid and pid not in pids:
            pids.append(pid)
    scope = args.edu or args.co or "未指定"
    print(f"{'教务' if args.edu else '联席'}「{scope}」涉及课程 ProductId {len(pids)} 个：{pids}")

    all_rows = []
    for pid in pids:
        page = 1
        got = 0
        while True:
            r = common.get(f"/cClassAttend/getCClassAttendList?page={page}&pageSize=500&productId={pid}", token)
            d = r.get("data") or {}
            lst = d.get("list") or []
            total = d.get("total")
            all_rows.extend(lst)
            got += len(lst)
            if not lst or got >= (total or 0) or len(lst) < 500:
                break
            page += 1
            time.sleep(0.12)
        print(f"  productId={pid} fetched={got}")
        time.sleep(0.1)

    # 按学生白名单过滤：students.json 已按角色（联席/教务）筛选，名字集合即本角色学生。
    # 教务视角下学生跨多个联席，不能用单一 coTeacherName 过滤，按姓名集合过滤两种视角都正确。
    names = set(common.norm_name(x["stuName"]) for x in students)
    rows = [r for r in all_rows if common.norm_name(r.get("studentName")) in names]
    json.dump(rows, open(f"{wd}/attendance_raw.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n✅ 原始出勤行 {len(all_rows)} -> 本角色 {len(rows)} 行 -> attendance_raw.json")


if __name__ == "__main__":
    main()
