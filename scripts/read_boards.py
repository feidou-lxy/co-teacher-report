"""步骤 B：逐张读取小黑板上学生手写作答，结构化分析 → boards_summary.json。

三种运行方式（按可用配置自动选择）：
  1) 自动视觉模型：在 <workdir>/boards_llm.json 写
       {"base_url":"https://xxx/v1","api_key":"...","model":"gpt-4o-mini"}
     或在环境变量设 BOARDS_LLM_BASE_URL / BOARDS_LLM_API_KEY / BOARDS_LLM_MODEL。
     脚本逐张调用多模态接口，自动产出 board_analysis.json 并聚合。
  2) 代理/人工读图：无视觉模型时，脚本先生成 board_analysis_template.json（待填清单）；
     由人或代理逐张读图后把结论填入 board_analysis.json，再运行本脚本聚合。
  3) 直接聚合：若 board_analysis.json 已存在，仅执行聚合 → boards_summary.json。

单图结构化结论字段：
  problem(题目简述) / topic(题目模块) / topic_knowledge(关联大知识点，用于与聊天高频交叉验证薄弱)
  / status(完整作答|部分作答|空白未提交) / correct(正确|有错|待判)
  / errors(错误类型列表) / stuck_at(卡点) / note(一句话)

聚合后 boards_summary.json：
  { 姓名: { rounds, analyzed, status_counts, error_types, kb_topics, error_topics, trend, details } }
  - kb_topics：小黑板涉及到的全部大知识点
  - error_topics：有错误 / 卡点轮次所关联的大知识点（＝小黑板实证的薄弱点）
report.py ①「学习情况」块直接消费此文件（小黑板佐证）。
"""
import argparse
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

PROMPT = """你是高二数学老师，正在批改学生的课堂小黑板手写作答。图片上半部分是题目（或参考题干），下半部分是该生的手写过程。
请只依据图片内容判断，输出 JSON（不要额外文字）：
{
  "problem": "题目简述（一句话，含考点，如：四面体相对棱中点距离）",
  "topic": "题目所属模块（如：立体几何/空间向量、函数与导数、数列、解析几何）",
  "topic_knowledge": "该生作答 / 卡点关联的『大知识点』，务必从下列候选中选最贴切的一个或多个（用顿号分隔）；若无法归入则填『其他』：立体几何/空间向量、直线和圆、函数与导数、数列、圆锥曲线、三角函数、概率统计、解三角形、不等式/参数、计算/粗心、其他",
  "status": "作答完整度，三选一：完整作答 / 部分作答 / 空白未提交",
  "correct": "正确性，三选一：正确 / 有错 / 待判",
  "errors": ["错误类型，如：建系错误、法向量算错、计算符号、漏解、思路偏差、未代入、收尾未完成"],
  "stuck_at": "该生卡在哪一步（简短；若顺畅作答写空字符串）",
  "note": "一句话点评（指出最关键的优点或问题）"
}"""


def load_llm_cfg(wd):
    p = os.path.join(wd, "boards_llm.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    base = os.environ.get("BOARDS_LLM_BASE_URL")
    key = os.environ.get("BOARDS_LLM_API_KEY")
    model = os.environ.get("BOARDS_LLM_MODEL")
    if base and key:
        return {"base_url": base, "api_key": key, "model": model or "gpt-4o-mini"}
    return None


def call_vision(cfg, img_path):
    with open(img_path, "rb") as f:
        import base64
        b64 = base64.b64encode(f.read()).decode("ascii")
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": "你是严谨的数学批改助手，只输出 JSON。"},
            {"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    return json.loads(data["choices"][0]["message"]["content"])


def split_topics(t):
    """把模型输出的『关联知识点』拆成知识点列表（支持顿号/逗号分隔多个）。"""
    if not t:
        return []
    return [x.strip() for x in re.split(r"[、,，]", str(t))
            if x.strip() and x.strip() != "其他"]


def aggregate(analysis_list, boards=None):
    from collections import Counter
    summary = {}
    # 课次映射：round（第 2 层时间戳目录）→ 所属课次（第 1 层目录名）
    lesson_of_round = {}
    if boards:
        for _stu, recs in boards.items():
            for r in recs:
                if r.get("round") and r.get("lesson"):
                    lesson_of_round[r["round"]] = (r["lesson"], r.get("lesson_date", ""))
    for item in analysis_list:
        stu = common.norm_name(item.get("student"))
        _rd = item.get("round", "")
        _ls, _ld = lesson_of_round.get(_rd, ("", ""))
        if not _ls and "/" in _rd:                 # 兜底：从 round 路径反推课次
            _ls = _rd.split("/")[0]
            _md = re.match(r"^(\d{2})(\d{2})", _ls)
            _ld = f"{_md.group(1)}-{_md.group(2)}" if _md else ""
        d = {
            "round": _rd,
            "lesson": _ls,
            "lesson_date": _ld,
            "problem": item.get("problem", ""),
            "topic": item.get("topic", ""),
            "topic_knowledge": (item.get("topic_knowledge") or "").strip(),
            "status": item.get("status", ""),
            "correct": item.get("correct", ""),
            "errors": item.get("errors", []) or [],
            "stuck_at": item.get("stuck_at", "") or "",
            "note": item.get("note", ""),
        }
        sm = summary.setdefault(stu, {"rounds": 0, "analyzed": 0, "details": []})
        sm["analyzed"] += 1
        sm["details"].append(d)
    for stu, sm in summary.items():
        # v3.8：rounds 按「轮次」去重（一个时间戳目录 = 一轮；同轮可能上传多张，不能按图片数计）
        sm["rounds"] = len({d.get("round") for d in sm["details"] if d.get("round")}) or sm["analyzed"]
        sc = Counter(d["status"] for d in sm["details"] if d["status"])
        sm["status_counts"] = dict(sc)
        ec = Counter(e for d in sm["details"] for e in d["errors"])
        sm["error_types"] = [k for k, _ in ec.most_common(5)]
        # 小黑板关联知识点：kb_topics=涉及的全部，error_topics=有错误轮次实证的（薄弱）
        kb, err_kb = [], []
        for d in sm["details"]:
            # topic_knowledge 为空时退回 topic：读图 / 人工填表常只给出 topic（题目模块），
            # 若不退回，kb_topics / error_topics 会整体为空，报告的「小黑板实证薄弱」就断链。
            _ts = split_topics(d.get("topic_knowledge")) or split_topics(d.get("topic"))
            for t in _ts:
                if t not in kb:
                    kb.append(t)
                if d.get("errors") and t not in err_kb:
                    err_kb.append(t)
        sm["kb_topics"] = kb
        sm["error_topics"] = err_kb
        # 趋势：前后半段正确率对比
        ds = sm["details"]
        if len(ds) >= 2:
            half = len(ds) // 2
            early = [d for d in ds[:half] if d["correct"] in ("正确", "有错")]
            late = [d for d in ds[half:] if d["correct"] in ("正确", "有错")]
            er = sum(1 for d in early if d["correct"] == "正确") / max(1, len(early))
            lr = sum(1 for d in late if d["correct"] == "正确") / max(1, len(late))
            sm["trend"] = "逐步改善" if lr > er else ("下滑" if lr < er else "基本持平")
        else:
            sm["trend"] = "样本不足"
        # v3.8：按「课次」分组（第 1 层目录）→ 便于报告按课次呈现，而不是一长串时间戳
        ls_order, ls_map = [], {}
        for d in sm["details"]:
            k = (d.get("lesson") or "其他", d.get("lesson_date") or "")
            if k not in ls_map:
                ls_map[k] = {"lesson": k[0], "lesson_date": k[1], "rounds": 0,
                             "status_counts": {}, "details": []}
                ls_order.append(k)
            g = ls_map[k]
            g["details"].append(d)
        for k in ls_order:
            g = ls_map[k]
            g["rounds"] = len({d.get("round") for d in g["details"] if d.get("round")})
            g["status_counts"] = dict(Counter(d["status"] for d in g["details"] if d["status"]))
        sm["lessons"] = [ls_map[k] for k in ls_order]
        sm["lesson_count"] = len(sm["lessons"])
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--boards", default=None, help="小黑板根目录（与 pull_boards 一致，用于定位图片）")
    args = ap.parse_args()
    wd = args.workdir
    boards_path = os.path.join(wd, "boards.json")
    if not os.path.exists(boards_path):
        sys.exit("未找到 boards.json，请先运行 pull_boards.py --boards <目录>。")
    boards = json.load(open(boards_path, encoding="utf-8"))

    analysis_path = os.path.join(wd, "board_analysis.json")
    cfg = load_llm_cfg(wd)
    template_path = os.path.join(wd, "board_analysis_template.json")

    if cfg:
        # 自动视觉模型
        analysis = []
        for stu, recs in boards.items():
            for r in recs:
                if not os.path.exists(r["path"]):
                    continue
                try:
                    res = call_vision(cfg, r["path"])
                except Exception as e:
                    print(f"⚠️ 读图失败 {r['path']}: {e}")
                    res = {"status": "空白未提交", "correct": "待判"}
                res["student"] = stu
                res["round"] = r["round"]
                analysis.append(res)
                print(f"  ✔ {stu} / {r['round']}: {res.get('status')} / {res.get('correct')}")
        json.dump(analysis, open(analysis_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"✅ 自动读图完成 {len(analysis)} 张 ｜ {analysis_path}")
    else:
        # 无视觉模型：若已有分析结果则聚合；否则生成待填模板
        if os.path.exists(analysis_path):
            analysis = json.load(open(analysis_path, encoding="utf-8"))
            print(f"🔍 发现已填 board_analysis.json（{len(analysis)} 条），直接聚合。")
        else:
            template = []
            for stu, recs in boards.items():
                for r in recs:
                    template.append({"student": stu, "round": r["round"],
                                     "lesson": r.get("lesson", ""), "lesson_date": r.get("lesson_date", ""),
                                     "path": r["path"],
                                     "problem": "", "topic": "", "topic_knowledge": "", "status": "",
                                     "correct": "", "errors": [], "stuck_at": "", "note": ""})
            json.dump(template, open(template_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"ℹ️ 未配置视觉模型，已生成待填模板：{template_path}\n"
                  f"   请逐张读图后把结论填入该文件（或 board_analysis.json），再重跑本脚本聚合。")
            return

    summary = aggregate(analysis, boards)
    out = os.path.join(wd, "boards_summary.json")
    json.dump(summary, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✅ 聚合完成｜ 覆盖学生 {len(summary)} 人 ｜ {out}")


if __name__ == "__main__":
    main()
