"""步骤7：从聊天中摘录学生/家长自述的请假/看回放缘由（备注用）。

后台无「请假原因」字段，故从近窗聊天里捞学生/家长本人发言中与请假/出勤相关的短句，
作为出勤块的简单备注。仅取 role==2（学生/家长），并过滤系统话术。

输出 <workdir>/leave_notes.json ：{ studentName: [短语1, 短语2] }
"""
import argparse
import json
import re
import sys

sys.path.insert(0, sys.path[0])
import common

PRIMARY = ['请假', '病假', '生病', '发烧', '感冒', '医院', '不舒服', '身体', '有事', '事儿',
           '比赛', '活动', '夏令营', '冲突', '家事', '补课', '来不了', '上不了', '没法上', '来不及']
SECOND = ['回放', '回看', '缺课', '没来', '迟到']
INTENT = ['申请', '请', '今天', '明天', '晚', '先不', '不上', '不了', '吧', '冲突', '没看']
BAD = ['私信我请假', '提前请假', '@所有人', '请家长或学生', '申请自行看回放', '麻烦发我']


def get_text(m):
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        for k in ("content", "text"):
            if k in c and isinstance(c[k], str):
                return c[k]
    return ""


def clean(seg):
    seg = seg.strip()
    seg = re.sub(r'\[[^\]]{1,8}\]', '', seg)
    seg = re.sub(r'@[^\s，,。！？]{1,20}', '', seg)
    seg = re.sub(r'（[^）]{0,20}）|\([^)]{0,20}\)', '', seg)
    return seg.strip('，,。！？、~ 　')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--window", default="2026-09-01")
    ap.add_argument("--end", default=None,
                    help="分析窗口止 YYYY-MM-DD（含当天；默认不限）")
    args = ap.parse_args()
    wd = common.workdir(args)
    CUT = args.window
    CUT_END = args.end          # v4.16：闭区间上界；None = 不设上界（与旧行为一致）
    d = json.load(open(f"{wd}/chats.json", encoding="utf-8"))

    notes = {}
    for stu, blk in d.items():
        prim, sec = [], []
        for m in blk["messages"]:
            _t = m.get("msgTime") or ""
            if _t < CUT:                       # msgTime 有 "YYYY-MM-DD HH:MM" 与 "YYYY-MM-DDT..." 两种形态
                continue
            if CUT_END and _t[:10] > CUT_END:
                continue
            if m.get("role") != 2:
                continue
            t = get_text(m)
            if not t or any(b in t for b in BAD):
                continue
            for seg in re.split(r'[。！？\n；;]', t):
                s = clean(seg)
                if not s or not (3 <= len(s) <= 34):
                    continue
                if any(b in s for b in BAD):
                    continue
                if any(k in s for k in PRIMARY):
                    prim.append(s)
                elif any(k in s for k in SECOND) and any(k in s for k in INTENT):
                    sec.append(s)
        picked = []
        for s in prim + sec:
            if s not in picked:
                picked.append(s)
            if len(picked) >= 2:
                break
        if picked:
            notes[stu] = picked

    json.dump(notes, open(f"{wd}/leave_notes.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"✅ 有请假/出勤备注的学生 {len(notes)} 人 -> leave_notes.json")


if __name__ == "__main__":
    main()
