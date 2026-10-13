"""步骤3：逐群拉取聊天记录（文本 + 语音转写）。

接口 wxworkChat/getChatList?roomId=&page=N&pageSize=500（倒序；按页拉全量，无单群条数上限）。
输出 <workdir>/chats.json ：{ studentName: {group:{...}, total, messages:[...]} }
其中 message = {msgTime, role, msgType, name, content}
  - 文本：content 为字符串，或 {content: "..."}
  - 语音：content 为 {content:{content: "转写文本"}}
"""
import argparse
import json
import sys
import time
import urllib.parse

sys.path.insert(0, sys.path[0])
import common


def get_chat(token, room, ps=500):
    """按页拉全量（接口倒序返回，page=1 为最新）。

    旧实现只取 page=1、pageSize=500，导致活跃群较早历史被截断（接口返回
    的 total 明确告知总条数，但未被拉全）。现改为分页累加直到拉满 total。
    """
    all_msgs = []
    page = 1
    total = None
    while True:
        path = ("/wxworkChat/getChatList?"
                + urllib.parse.urlencode({"roomId": room, "page": page, "pageSize": ps}))
        r = None
        for attempt in range(3):
            try:
                r = common.get(path, token)
                break
            except Exception as e:
                if attempt == 2:
                    # 返回已拉到的部分，避免整群丢失
                    return {"code": -1, "msg": str(e), "data": {"list": all_msgs, "total": total}}
                time.sleep(2)
        dd = (r or {}).get("data") or {}
        if total is None:
            total = dd.get("total")
        lst = dd.get("list") or []
        all_msgs.extend(lst)
        # 终止条件：空页 / 已达 total / 本页不足一页
        if not lst:
            break
        if total is not None and len(all_msgs) >= total:
            break
        if len(lst) < ps:
            break
        page += 1
    return {"data": {"list": all_msgs, "total": total}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--co", default=None)
    ap.add_argument("--edu", default=None)
    ap.add_argument("--course", default=None)
    args = ap.parse_args()
    wd = common.workdir(args)
    token = common.load_token(wd)
    groups = json.load(open(f"{wd}/groups.json", encoding="utf-8"))
    scope = args.edu or args.co or "未指定"

    result = {}
    total = 0
    for i, g in enumerate(groups, 1):
        room = g.get("roomId")
        stu = g.get("studentName")
        r = get_chat(token, room)
        dd = r.get("data") or {}
        lst = dd.get("list") or []
        result[stu] = {"group": g, "total": dd.get("total"), "messages": lst}
        total += len(lst)
        print(f"[{i:3d}/{len(groups)}] {stu}: total={dd.get('total')} pulled={len(lst)}")
        time.sleep(0.3)
    json.dump(result, open(f"{wd}/chats.json", "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\n✅ 群数 {len(result)} 消息 {total} -> chats.json")


if __name__ == "__main__":
    main()
