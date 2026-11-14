#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书档案表「成绩」列提取（呆呆团队英语档案表 / 听口专项班）。FE_EXAM_VER = v1.0

背景：高中 8 张英语档案表（07b44d/f36jYD/WR7EOF/9zmRN9/ud4DJk/mzVPjj/8hYJKo/BKGYFV）
+ 初三听口专项班（UmEQZ2）的「学习信息 → 成绩」子列里塞着真分数（教务手工填写）：
    英语96 / 99（初三一二模考得不好）/ 期中97/期末95 / 中考成绩39+45=84 /
    93-95 / 110-120 / 90左右 / 80➕ / 80多分 / 四十分左右 / 46/平均分40 / 50+

用法：
    python3 extract_feishu_exam.py --raw <feishu_raw*.json> [--out <out.json>]

解析规则（只收实证形态，逐条对应实测原话）：
  - 单值：96 / 96分 / 82.5 / 90➕(=90+) / 99（备注括号）
  - 区间：93-95 / 100-115 / 135-142 / 37、39  → [lo, hi]
  - 约数：90左右 / 80多分 / 四十分左右 / 50+ / 90➕ → 约数（single 附近值）
  - 多考次：期中97/期末95 → 拆两条（各带 exam 标签）
  - 算式：中考成绩39+45=84 → 84（中考）
  - 中文数字：四十分左右 → 40左右；「四十几/四十X」→ 约 40
  - 挡非分数：没出来 / 不回复 / 没考过 / 还没测过 / 「/」/ 纯描述文字
  - 「35，目标46以上」→ 取当前值 35（目标不取）

满分推断（score_full）：
  - 显式标注：「67（满分150）」→ 150；「A/满分B」
  - 高一档案（中考语境）→ 100（北京中考英语）
  - 高二/高三档案 → 150（北京高中英语期末/月考）
  - 听口专题（高中）→ 听口满分制不确定，full=None（不算得分率，只记原始分）
  - 初三听口专项班 → 40（北京中考英语听口机考满分 40）
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import common                      # 复用 _exam_level（同技能内公共件，无环）
except Exception:
    common = None

FE_EXAM_VER = "v1.0"

# ---------------------------------------------------------------- 文本归一
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}


def _cn2num(s):
    """中文小数字（十以内/十几/几十）→ int；失败返回 None。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    m = re.fullmatch(r"([一二三四五六七八九])?十([一二三四五六七八九])?", s)
    if m:
        tens = _CN_NUM.get(m.group(1), 1) if m.group(1) else 1
        ones = _CN_NUM.get(m.group(2), 0) if m.group(2) else 0
        return tens * 10 + ones
    return None


def norm_text(s):
    s = str(s or "")
    s = s.replace("➕", "+").replace("＋", "+").replace("～", "-").replace("~", "-")
    # ⚠️ 全角「，」不转半角——它是考次边界（_parse_seg 子拆分依赖）；
    #    ASCII 逗号留给「满分100,90」结构
    return s.strip()


# ---------------------------------------------------------------- 考试类型标签
_EXAM_KWS = [
    ("期中", "期中"), ("期末", "期末"), ("月考", "月考"),
    ("中考", "中考"), ("一摸", "一模"), ("一模", "一模"), ("二模", "二模"),
    ("听口", "听口"), ("听力", "听口"), ("天学网", "听口"), ("e听说", "听口"),
    ("区模拟", "模拟"), ("模拟", "模拟"), ("学校测试", "校内"), ("校内", "校内"),
]


def exam_tags(s):
    return sorted({lab for kw, lab in _EXAM_KWS if kw in s})


# ---------------------------------------------------------------- 分数解析
_NUM = r"\d{1,3}(?:\.\d{1,2})?"


def _mk(score, kind, note=None):
    return {"score": score, "kind": kind,
            "note": re.sub(r"\s+", " ", note).strip() if note else None}


def parse_score(raw):
    """把一格「成绩」文本解析为结构化条目列表（一格可能多考次）。

    返回 [{"score": 96, "kind": "single|range|approx", "exam": ["中考"], "note": …}, …]
    解析不出任何分数返回 []。
    """
    s = norm_text(raw)
    if not s or s in {"/", "-", "—"}:
        return []
    out = []
    # 挡一类明显无分数的描述（含"没/不/还没"+考过/出来/回复）
    if re.search(r"(没(有)?(考过|出来|测过)|(暂时)?不回复|还没(考|测|出))", s) and \
            not re.search(r"\d{2}", s):
        return []

    # ① 算式：39+45=84 → 84
    m = re.search(rf"({_NUM})\+({_NUM})=({_NUM})", s)
    if m:
        tags = exam_tags(s)
        out.append({"score": float(m.group(3)), "kind": "single",
                    "exam": tags or ["中考"], "note": f"原算式 {m.group(1)}+{m.group(2)}={m.group(3)}"})
        return out

    # ② 多考次拆分：「期中97/期末95」「中考99，第一次月考满分100,90分」
    segs = [x for x in re.split(r"[;/；]", s) if x.strip()]
    prev = None
    for seg in segs:
        seg = seg.strip()
        # 「平均分40」是上一条的语境，不是独立成绩
        if re.match(r"^(?:平均分|班级平均|均分|平均)", seg):
            if prev is not None:
                prev["note"] = re.sub(r"\s+", " ", (prev["note"] or "") + " " + seg).strip()
            continue
        seg_out = _parse_seg(seg)
        for it in seg_out:
            # 补全考次标签：段内无标签时继承全句标签
            if not it["exam"]:
                it["exam"] = exam_tags(s)
        if seg_out:
            prev = seg_out[-1]
        out.extend(seg_out)
    return out


def _parse_seg(seg):
    out = []
    if not seg:
        return out
    # 全角逗号 = 考次/话题边界：「中考99，第一次月考满分100,90分」
    #   （ASCII 逗号留给「满分100,90」结构，不在此拆）
    if "，" in seg:
        parts = [p.strip() for p in seg.split("，") if p.strip()]
        if len(parts) > 1:
            sub = []
            for p in parts:
                for it in _parse_seg(p):
                    if not it["exam"]:
                        it["exam"] = exam_tags(seg)
                    sub.append(it)
            return sub
    tags = exam_tags(seg)
    # 显式满分标注提出来：「67（满分150）」「满分100,90分」
    note = None
    mm = re.search(rf"[（(]\s*满分\s*({_NUM})\s*[)）]", seg)
    full_mark = float(mm.group(1)) if mm else None
    if mm:
        seg = seg[:mm.start()] + seg[mm.end():]
    mm = re.search(rf"满分\s*({_NUM})\s*[,，]\s*({_NUM})", seg)
    if mm:
        full_mark = float(mm.group(1))
        # 「满分100,90分」→ 满分 100、成绩 90：保留逗号后的分数本体
        seg = seg[:mm.start()] + mm.group(2) + seg[mm.end():]

    # 去掉括号备注但保留为 note
    pm = re.search(r"[（(]([^（()）]{2,})[)）]", seg)
    if pm and not full_mark:
        note = pm.group(1)
        seg_no_paren = seg[:pm.start()] + seg[pm.end():]
    else:
        seg_no_paren = seg

    # 中文数字约数：四十分左右 / 四十几
    cm = re.search(r"([一二两三四五六七八九]十[一二三四五六七八九]?)([多几])?(?:分)?(?:左右|上下)?", seg_no_paren)
    if cm and not re.search(r"\d{2}", seg_no_paren):
        v = _cn2num(cm.group(1))
        if v is not None:
            kind = "approx" if (cm.group(2) or "左右" in cm.group(0) or "上下" in cm.group(0)) else "single"
            out.append(_mk(float(v), kind, note))
            out[-1]["exam"] = tags
            return out

    # 区间：A-B / A,B（仅当两者都是 2 位数以上、A<B 且差值合理 ≤40）
    for m in re.finditer(rf"({_NUM})\s*[-]\s*({_NUM})", seg_no_paren):
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo <= hi and (hi - lo) <= 40 and hi >= 30:
            out.append(_mk([lo, hi], "range", note))
            out[-1]["exam"] = tags
            return out

    # 约数/单值：A左右 / A多分 / A+ / 裸 A / A分
    core = seg_no_paren
    m = re.search(rf"({_NUM})\s*(?:分)?\s*(?:左右|上下|多分|多|＋|\+|➕)", core)
    if m:
        out.append(_mk(float(m.group(1)), "approx", note))
        out[-1]["exam"] = tags
        return out
    # 裸数字（取最后一个 2 位数，避免误取目标分——目标词后面的不取）
    tm = re.search(r"目标", core)
    search_zone = core[:tm.start()] if tm else core
    nums = re.findall(rf"(?<![\d.])(\d{{2,3}}(?:\.\d{{1,2}})?)(?![\d.])", search_zone)
    if nums:
        # 跳过疑似年份/日期（19xx/20xx 且 >130）
        cands = [x for x in nums if float(x) <= 150]
        if cands:
            out.append(_mk(float(cands[-1]), "single", note))
            out[-1]["exam"] = tags
            return out
    # 只有小数分数（如 34.24）带语境
    nums1 = re.findall(rf"(?<![\d.])(\d{{1,2}}(?:\.\d{{1,2}})?)(?![\d.])", search_zone)
    if nums1 and ("分" in core or tags):
        cands = [float(x) for x in nums1 if float(x) <= 150]
        if cands:
            out.append(_mk(cands[-1], "single", note))
            out[-1]["exam"] = tags
    return out


# ---------------------------------------------------------------- 满分推断
def infer_full(sheet_title, items):
    """按 sheet **表名**语境推断满分。返回 (full, full_note)。

    ⚠️ 只看表名不看行内容——行里提「听口」不代表整表是听口分（如「中考88，听口满分」）。
    """
    if "听口" in sheet_title:
        if sheet_title.startswith("初三"):
            return 40, "北京中考英语听口机考满分 40"
        return None, "高中听口满分制待确认，不算得分率"
    if sheet_title.startswith("高一"):
        return 100, "高一档案成绩多为中考英语（北京中考满分 100）"
    if sheet_title.startswith(("高二", "高三")):
        return 150, "高中英语期末/月考 150 制"
    return None, None


def to_fs_exam(raw, parsed, full, full_note=None):
    """结构化解析结果 → fs_stats 通用 exam 形状 {text, shown, pct, level, assumed}。

    - 单值+满分已知 → pct/level 照算（沿用 common._exam_level 阈值）
    - 区间/约数/满分未知 → 只给 shown（原样区间/原文），pct=None、level=None
      —— **不判档**（并存口径：不改既有分层）
    - 多考次取**最后一条**（期中97/期末95 → 期末 95，时间上最新）
    """
    try:
        _exam_level = common._exam_level
    except Exception:
        _exam_level = lambda p: None          # noqa: E731
    if not parsed:
        return None
    it = parsed[-1]
    sc = it["score"]
    tag = "+".join(it.get("exam") or [])
    if isinstance(sc, list):
        return {"text": raw, "shown": f"{sc[0]:g}-{sc[1]:g}", "pct": None,
                "level": None, "assumed": full_note or "", "examTag": tag}
    if it.get("kind") == "approx":
        return {"text": raw, "shown": None, "pct": None, "level": None,
                "assumed": full_note or "", "examTag": tag}
    if full:
        return {"text": raw, "shown": f"{sc:g}/{full:g}", "pct": sc / full,
                "level": _exam_level(sc / full), "assumed": "", "examTag": tag}
    # 单值但满分未知（如高中听口）→ 只显示原值，不判档
    return {"text": raw, "shown": f"{sc:g}", "pct": None, "level": None,
            "assumed": full_note or "", "examTag": tag}


# ---------------------------------------------------------------- 主流程
def extract_sheet(sheet):
    vals = sheet.get("values") or []
    score_col = name_col = hdr_row = None
    for i, r in enumerate(vals[:3]):
        for j, c in enumerate(r):
            t = str(c).strip()
            if t == "成绩" or t.startswith("成绩（"):
                score_col, hdr_row = j, i
            if t == "学生姓名" and name_col is None:
                name_col = j
        if score_col is not None:
            break
    if score_col is None:
        return []
    recs = []
    for ri, r in enumerate(vals[hdr_row + 1:], start=hdr_row + 1):
        nm = str(r[name_col]).strip() if (name_col is not None and name_col < len(r)) else ""
        sc = str(r[score_col]).strip() if score_col < len(r) else ""
        if not nm or not sc:
            continue
        parsed = parse_score(sc)
        recs.append({"row": ri, "student": nm, "raw": sc,
                     "parsed": [{**it, "exam": it.get("exam", [])} for it in parsed]})
    return recs


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    d = json.load(open(args.raw, encoding="utf-8"))
    all_recs = {}
    for s in d["sheets"]:
        recs = extract_sheet(s)
        if not recs:
            continue
        full, full_note = infer_full(s["title"], recs)
        for r in recs:
            for it in r["parsed"]:
                it["full"] = full
        all_recs[s["sheetId"]] = {
            "title": s["title"], "full": full, "fullNote": full_note,
            "records": recs,
        }
    n_rows = sum(len(v["records"]) for v in all_recs.values())
    n_scored = sum(1 for v in all_recs.values() for r in v["records"] if r["parsed"])
    print(f"✅ {n_scored}/{n_rows} 行解析出分数，{len(all_recs)} 张表：")
    for sid, v in all_recs.items():
        ns = sum(1 for r in v["records"] if r["parsed"])
        print(f"   {sid} {v['title']}: {ns} 行（满分={v['full']}）")
    if args.out:
        json.dump({"ver": FE_EXAM_VER, "sheets": all_recs},
                  open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"-> {args.out}")


CASES = [
    # (原文, 期望 [(kind, score), …])
    ("英语96", [("single", 96.0)]),
    ("96分", [("single", 96.0)]),
    ("99（初三一二模考得不好）", [("single", 99.0)]),
    ("期中97/期末95", [("single", 97.0), ("single", 95.0)]),
    ("中考成绩39+45=84", [("single", 84.0)]),
    ("93-95", [("range", [93.0, 95.0])]),
    ("110-120", [("range", [110.0, 120.0])]),
    ("90左右", [("approx", 90.0)]),
    ("80多分", [("approx", 80.0)]),
    ("80➕", [("approx", 80.0)]),
    ("50+", [("approx", 50.0)]),
    ("四十分左右", [("approx", 40.0)]),
    ("中考95分", [("single", 95.0)]),
    ("中考英语90分", [("single", 90.0)]),
    ("英语成绩94", [("single", 94.0)]),
    ("100（一模二模还有学校的测试在96-98之间）", [("single", 100.0)]),
    ("中考99，第一次月考满分100,90分", [("single", 99.0), ("single", 90.0)]),
    ("46/平均分40", [("single", 46.0)]),
    ("35，目标46分以上", [("single", 35.0)]),
    ("82.5", [("single", 82.5)]),
    ("93分", [("single", 93.0)]),
    ("目前35分，目标46分以上", [("single", 35.0)]),
    ("没出来", []),
    ("不回复", []),
    ("/", []),
    ("cd，作文，单词是大砍", []),
    ("基础还可以，孩子高一做高三的一摸，就扣了七分", []),
]


def selftest():
    bad = []
    for raw, want in CASES:
        got = [(x["kind"], x["score"]) for x in parse_score(raw)]
        if got != want:
            bad.append((raw, got, want))
    for raw, got, want in bad:
        print(f"❌ {raw!r}: got {got}, want {want}")
    print(f"用例 {len(CASES)} 条：通过 {len(CASES)-len(bad)}，失败 {len(bad)}")
    return not bad


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(0 if selftest() else 1)
    run()
