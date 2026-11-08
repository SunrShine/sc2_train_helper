"""把 flow.py 产出的 reports/tvt-flows.json 渲染成"流程命名核对表"。

用途：用户要打开录像、逐个核对流程命名。所以这张表的重点是
**每个流程 -> 代表录像的完整路径 + 能不能直接播 + 建议命名 + 你的命名（空着）**。

    python tools\\make_flow_table.py --json reports/tvt-flows.json -o reports/tvt-flows.html
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from replay_analyzer import flow_report  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成流程命名核对表")
    parser.add_argument("--json", default=os.path.join("reports", "tvt-flows.json"))
    parser.add_argument("-o", "--output", default=os.path.join("reports", "tvt-flows.html"))
    args = parser.parse_args(argv)

    if not os.path.isfile(args.json):
        print("找不到 %s，先跑 flow 分析" % args.json)
        return 1
    with open(args.json, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    flow_report.write(data, args.output)
    flows = data.get("flows") or []
    games = data.get("games") or []
    print("%d 类流程 / %d 局 -> %s" % (len(flows), len(games), os.path.abspath(args.output)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
