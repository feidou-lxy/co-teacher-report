"""知识点字典：加载后台知识树，供识别 / 展示 / 教材对齐使用（v4.75）

数据来源只有一处：`pull_knowledge.py` 落盘的 `snapshot/knowledge_points.json`。
**不再维护第二份知识点词表** —— 在这之前 `extract.py:TOPIC_UNITS`（14 个大模块）
和小黑板读图口径各写一份，加一个知识点要改两处，还容易对不上。

用法：
    import kp
    K = kp.load(wd)                 # 没拉过返回 None（调用方要显式降级，不能静默）
    K = kp.match(K, "这题考等差数列求和")   # → [{"id":..,"name":"等差数列求和","path":..}, ...]
    kp.candidates(K, stage=2, limit=80)

设计要点：
- **长名优先**：命中「等差数列求和」时不再重复命中它的父节点「数列」；
  否则一句话会同时挂上父子两级，报告里看着像两个薄弱点。
- **同分并列不猜**：一句话命中多个无关知识点时全部返回，由调用方按信号条数过滤，
  这里不做「只取最像的一个」的启发式（那会悄悄丢证据）。
"""
import json
import os

CACHE = {}


def _p(wd):
    return os.path.join(wd, "snapshot", "knowledge_points.json")


def load(wd):
    """加载知识字典；文件不存在返回 None。

    ⚠️ 返回 None 时**不要静默退回旧硬编码词表**：宁可让报告少一块，
    也不要出现「半份来自后台、半份来自旧词表」的混合口径。调用方要打印告警。
    """
    if wd in CACHE:
        return CACHE[wd]
    p = _p(wd)
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    pts = [x for x in (d.get("points") or []) if isinstance(x, dict) and x.get("name")]
    if not pts:
        return None
    # 名字反查：同名节点（重名）优先取「上架 + 路径最短」的，路径短的更靠上层、更好懂
    by_name = {}
    for x in sorted(pts, key=lambda y: (y.get("status") != 1, y.get("depth") or 9)):
        by_name.setdefault(x["name"], x)
    K = {
        "meta": {k: d.get(k) for k in ("pulled_at", "filters", "total", "total_raw")},
        "points": pts,
        "by_name": by_name,
        "by_id": {x.get("id"): x for x in pts if x.get("id") is not None},
        # 命中词：只取名字长度 ≥2 的（单字名如「圆」「角」会把整段话都命中）
        "terms": sorted(by_name, key=len, reverse=True),
    }
    CACHE[wd] = K
    return K


def match(K, text, max_hit=6):
    """在文本里找知识点名，返回命中列表（长名优先、父子去重）。

    父子去重的做法：命中「等差数列求和」后，把它的父链名字从待匹配集合里剔除，
    但这只对**这一次匹配**生效 —— 句子后半句可能确实在说父级概念。
    所以这里改用「命中区间已被更长名字覆盖就跳过」，不做全局剔除。
    """
    if not K or not text:
        return []
    hits, spans = [], []
    for t in K["terms"]:
        if len(hits) >= max_hit:
            break
        i = text.find(t)
        if i < 0:
            continue
        a, b = i, i + len(t)
        if any(a >= x and b <= y for x, y in spans):   # 已被更长的名字覆盖
            continue
        spans.append((a, b))
        hits.append(K["by_name"][t])
    return hits


def candidates(K, stage=None, limit=80, online_only=True):
    """给「读图要它从候选里选」这类场景用的候选清单。

    排序：考试频次高的靠前（先让模型看见高频知识点），同频次按层级浅的靠前。
    ⚠️ 不要按字母序或 ID 序 —— 截断时会把有价值的节点截掉。
    """
    if not K:
        return []
    pts = K["points"]
    if online_only:
        pts = [x for x in pts if x.get("status") == 1]
    if stage:
        pts = [x for x in pts if x.get("schoolStages") in (stage, 99, None)]
    pts = sorted(pts, key=lambda x: (-(x.get("examFrequency") or 0),
                                     x.get("level") or 9, x.get("name") or ""))
    # 父子同名去重（父节点本身也是知识点时，保留父但避免名字重复占位）
    seen, out = set(), []
    for x in pts:
        if x["name"] in seen:
            continue
        seen.add(x["name"])
        out.append(x)
        if len(out) >= limit:
            break
    return out


def by_category(K, cat):
    """cat: 1=知识 2=方法。报告里「方法」类问题（如「不会建系」）要能和「知识」类分开说。"""
    if not K:
        return []
    return [x for x in K["points"] if x.get("category") == cat]


def label(p):
    """展示用短名：优先用节点名；需要区分同名时用倒数两级路径。"""
    if not p:
        return ""
    path = p.get("path") or p.get("name") or ""
    parts = [x for x in path.split("/") if x]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[-1] if parts else ""
