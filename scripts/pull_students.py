"""步骤1：拉取指定联席（--co）或教务（--edu）名下的学生花名册。

后台字段对应：联席 = coTeacherName；教务 = classTeacherName（响应时效页的「教务」列）。

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
    ap.add_argument("--co", default=None, help="联席姓名，如 张巍（联席视角）")
    ap.add_argument("--edu", default=None, help="教务姓名，如 罗绂燕（教务视角）")
    args = ap.parse_args()
    role, name = common.resolve_scope(args)
    field = common.scope_field(role)

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

    sel = [x for x in rows if (x.get(field) or "").strip() == name]
    out = []
    for x in sel:
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
    label = common.ROLE_LABEL.get(role, "联席")
    print(f"\n✅ {label}「{name}」学生 {len(out)} 人 -> students.json")
    c = Counter((x.get("productName") or "") for x in out)
    for k, v in c.most_common():
        print(f"   {v:>3}  {k}")
    if role == "edu":
        cc = Counter((x.get("coTeacherName") or "-") for x in out)
        print(f"   涉及联席 {len(cc)} 人：")
        for k, v in cc.most_common():
            print(f"     {v:>3}  {k}")


if __name__ == "__main__":
    main()
