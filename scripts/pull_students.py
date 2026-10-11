"""步骤1：拉取指定联席名下的学生花名册。

输出 <workdir>/students.json ：[{stuName, stuSeq, productName, productStudentId,
ProductId, teacherName, classTeacherName, schoolName, grade, coTeacherName}]
"""
import argparse
import json
import sys
import time
from collections import Counter

sys.path.insert(0, sys.path[0])
import common


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", required=True, help="联席姓名，如 张巍")
    args = ap.parse_args()

    wd = common.workdir(args)
    token = common.load_token(wd)

    rows = []
    page = 1
    PAGE = 500
    while True:
        r = common.post("/cStudentFile/getCStudentFileList",
                        {"page": page, "pageSize": PAGE,
                         "productStatus": [0, 1], "attendStatus": 1}, token)
        d = r.get("data") or {}
        lst = d.get("list") or []
        rows.extend(lst)
        total = d.get("total")
        print(f"page {page} got {len(lst)} accum {len(rows)} / total {total}")
        if not lst or len(rows) >= (total or 0) or len(lst) < PAGE:
            break
        page += 1
        time.sleep(0.15)

    zw = [x for x in rows if (x.get("coTeacherName") or "").strip() == args.co.strip()]
    out = []
    for x in zw:
        out.append({
            "stuName": x.get("stuName"),
            "stuSeq": x.get("stuSeq"),
            "productName": x.get("productName"),
            "productStudentId": x.get("productStudentId"),
            "ProductId": x.get("ProductId"),
            "teacherName": x.get("teacherName"),
            "classTeacherName": x.get("classTeacherName"),
            "schoolName": x.get("schoolName"),
            "grade": x.get("grade"),
            "coTeacherName": x.get("coTeacherName"),
        })
    json.dump(out, open(f"{wd}/students.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\n✅ 联席「{args.co}」学生 {len(out)} 人 -> students.json")
    c = Counter((x.get("productName") or "") for x in out)
    for k, v in c.most_common():
        print(f"   {v:>3}  {k}")


if __name__ == "__main__":
    main()
