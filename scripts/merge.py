"""步骤8：把出勤（后台）与请假备注（聊天）并入分析结果，得到最终分析对象。

按姓名归一化 join：analysis.json 的 key 与学生名可能带数字后缀，attendance_clean.json
以归一化名为主键。

输出 <workdir>/analysis_merged.json ：{ studentName: { ...analysis字段, att, leave, buy_type } }
  att = 出勤聚合记录（无则 None）
  leave = 请假备注列表（无则 []）
  buy_type = 购课类型中文（来自花名册 cIncomeProductType，如 直播/回放）
"""
import argparse
import json
import sys

sys.path.insert(0, sys.path[0])
import common


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    args = ap.parse_args()
    wd = common.workdir(args)
    an = json.load(open(f"{wd}/analysis.json", encoding="utf-8"))
    att = json.load(open(f"{wd}/attendance_clean.json", encoding="utf-8"))
    leave = json.load(open(f"{wd}/leave_notes.json", encoding="utf-8"))
    try:
        students = json.load(open(f"{wd}/students.json", encoding="utf-8"))
    except Exception:
        students = []
    # 购课类型（花名册 cIncomeProductType：1直播/2回放/3试听/4测试）按归一化姓名映射
    buy_by_norm = {common.norm_name(x.get("stuName")): common.buy_type_label(x.get("cIncomeProductType"))
                   for x in students}

    att_by_norm = {common.norm_name(k): v for k, v in att.items()}
    merged = {}
    hit = 0
    for name, rec in an.items():
        a = att_by_norm.get(common.norm_name(name))
        if a:
            hit += 1
        r = dict(rec)
        r["att"] = a
        r["leave"] = leave.get(name, [])
        r["buy_type"] = buy_by_norm.get(common.norm_name(name), "")
        merged[name] = r
    json.dump(merged, open(f"{wd}/analysis_merged.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    miss = [n for n, r in merged.items() if not r["att"]]
    print(f"✅ 学生 {len(merged)}，命中后台出勤 {hit}，未命中（可能已退课/未排课）{len(miss)} -> analysis_merged.json")
    if miss:
        print("   未命中：", miss)


if __name__ == "__main__":
    main()
