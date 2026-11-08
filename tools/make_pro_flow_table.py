"""职业录像 × 流程类型 核对表。

用户要"看录像核对流程命名"，所以这张表把**每一局职业录像**的
双方流程标签、完整路径、能不能直接播放在一起，并按流程类型分组。

    python tools\\make_pro_flow_table.py --json reports/tvt-flows.json -o reports/pro-flows.html
"""

import argparse
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

h = html.escape

CSS = """
:root{--bg:#0e1116;--bg2:#151a22;--card:#1a2029;--card2:#202836;--line:#2a3340;
--text:#e6ecf5;--muted:#93a1b5;--good:#46d17e;--warn:#f2c45a;--accent:#7c9cff}
*{box-sizing:border-box}
body{margin:0;padding:0 0 70px;color:var(--text);
background:radial-gradient(1200px 600px at 50% -200px,#1b2436 0%,var(--bg) 60%);
font:15px/1.65 "Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:0 20px}
header.top{padding:34px 0 8px}
h1{font-size:26px;margin:0 0 6px}
.sub{color:var(--muted);font-size:13.5px}
h2{font-size:17px;margin:30px 0 10px;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:4px;height:15px;border-radius:2px;background:var(--accent)}
.card{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);
border-radius:13px;padding:14px 16px;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;padding:7px 8px;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:7px 8px;border-bottom:1px solid #222b38;vertical-align:top}
tr:last-child td{border-bottom:none}
.mono{font-family:Consolas,"Courier New",monospace;font-size:11.5px;word-break:break-all;color:#9fb0c8}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;
border:1px solid var(--line);background:#202836;white-space:nowrap}
.ok{color:var(--good);border-color:#2b5a3f}
.no{color:var(--warn);border-color:#5a4b2b}
.flowchip{display:inline-block;padding:1px 8px;border-radius:6px;font-size:12px;
background:#1d2740;border:1px solid #2c3c60;margin:1px 0}
.hint{color:var(--muted);font-size:13px;line-height:1.85}
.hint b{color:var(--text)}
code{background:#141a23;padding:1px 6px;border-radius:5px;font-size:12.5px}
.grp{display:grid;grid-template-columns:120px 1fr;gap:10px;padding:6px 0;font-size:13px}
.grp .k{color:var(--muted)}
footer{margin-top:34px;color:#66748a;font-size:12.5px;border-top:1px solid var(--line);padding-top:14px}
"""

OPEN_HELP = """
<div class="hint">
<p><b>打开方式：</b>拖到 <code>D:\\StarCraft II\\Support64\\SC2Switcher_x64.exe</code>；
或游戏内 <b>F10 → 回放 → 打开回放</b>；或复制到
<code>…\\5-S2-1-8354391\\Replays\\Multiplayer\\</code> 后从录像列表打开。</p>
<p><b>只有 build 93333 / 98370 能直接播</b>（本机仅这两个版本目录里有游戏程序）。
职业目录里 84643 / 87702 / 88500 / 93272 的局都播不了，只有
<code>replays\\pro\\tvt\\</code> 里那批 build 93333 的能播。</p>
</div>
"""


def _clock(seconds):
    try:
        seconds = max(0, int(round(float(seconds))))
    except Exception:
        return "?"
    return "%d:%02d" % (seconds // 60, seconds % 60)


def _split_players(game):
    """返回 (主要职业选手, 对手)。

    `replays/pro/tvt/` 里两边都标 role="pro"（不区分谁是目标选手），
    所以这里取第一个当"主"，另一个当对手，两边的流程都会显示。
    """
    players = list(game.get("players") or [])
    if not players:
        return None, None
    for index, player in enumerate(players):
        if player.get("role") == "pro":
            other = players[1 - index] if len(players) > 1 else None
            return player, other
    return players[0], (players[1] if len(players) > 1 else None)


def _flow_chip(player):
    if not player:
        return "<span class='pill'>?</span>"
    name = player.get("flow_name") or player.get("flow") or "?"
    return "<span class='flowchip'>%s</span>" % h(name)


def render(data, title="职业录像 × 流程类型 核对表"):
    games = [g for g in (data.get("games") or []) if g.get("source") == "pro"]
    games.sort(key=lambda g: (not g.get("playable"), g.get("build", 0), g.get("file", "")))
    playable = [g for g in games if g.get("playable")]

    # 按流程类型聚合（主选手的 flow）
    by_flow = {}
    for game in games:
        main, _other = _split_players(game)
        key = (main or {}).get("flow_name") or (main or {}).get("flow") or "?"
        by_flow.setdefault(key, []).append(game)

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>%s</title><style>%s</style></head><body><div class=\"wrap\">" % (h(title), CSS)]
    parts.append("<header class=\"top\"><h1>%s</h1>" % h(title))
    parts.append('<div class="sub">%d 局职业录像 · 其中 %d 局能直接播放 · 来源：选手目录 + '
                 'replays\\pro\\tvt\\</div>' % (len(games), len(playable)))
    parts.append("</header>")
    parts.append('<h2>怎么打开</h2><div class="card">%s</div>' % OPEN_HELP)

    parts.append('<h2>先看这 %d 局（能直接播的）</h2><div class="card"><table><thead><tr>'
                 "<th>选手</th><th>对手</th><th>地图 / 时长</th><th>双方流程</th>"
                 "<th>文件</th></tr></thead><tbody>" % len(playable))
    for game in playable:
        main, other = _split_players(game)
        parts.append(
            "<tr><td><b>%s</b></td><td>%s</td><td>%s<br>%s</td>"
            "<td>%s<br>%s</td><td class='mono'>%s</td></tr>"
            % (h((main or {}).get("name", "?")), h((other or {}).get("name", "?")),
               h(game.get("map", "")), _clock(game.get("duration")),
               _flow_chip(main), _flow_chip(other), h(game.get("path", ""))))
    parts.append("</tbody></table></div>")

    parts.append('<h2>按流程类型分组（全部 %d 局职业）</h2>' % len(games))
    for flow_name, items in sorted(by_flow.items(), key=lambda kv: -len(kv[1])):
        parts.append('<div class="card"><h2 style="margin-top:0">%s · %d 局</h2>' % (h(flow_name), len(items)))
        parts.append("<table><thead><tr><th>选手</th><th>对手</th><th>地图 / 时长</th>"
                     "<th>版本</th><th>能播</th><th>对手流程</th><th>文件</th></tr></thead><tbody>")
        for game in items:
            main, other = _split_players(game)
            badge = ("<span class='pill ok'>能播</span>" if game.get("playable")
                     else "<span class='pill no'>需旧版本</span>")
            parts.append(
                "<tr><td><b>%s</b></td><td>%s</td><td>%s<br>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td class='mono'>%s</td></tr>"
                % (h((main or {}).get("name", "?")), h((other or {}).get("name", "?")),
                   h(game.get("map", "")), _clock(game.get("duration")),
                   h(str(game.get("build", ""))), badge, _flow_chip(other),
                   h(game.get("path", ""))))
        parts.append("</tbody></table></div>")

    parts.append("<footer>由 tools/make_pro_flow_table.py 从 reports/tvt-flows.json 生成。"
                 "流程归类规则见 replay_analyzer/flow.py 的 docstring，判定依据是建造事件与单位构成，"
                 "不是人工标注。没有播放的局只按数据归类，看过之后可以推翻。</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成职业录像 × 流程核对表")
    parser.add_argument("--json", default=os.path.join("reports", "tvt-flows.json"))
    parser.add_argument("-o", "--output", default=os.path.join("reports", "pro-flows.html"))
    args = parser.parse_args(argv)

    if not os.path.isfile(args.json):
        print("找不到 %s" % args.json)
        return 1
    with open(args.json, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    text = render(data)
    directory = os.path.dirname(os.path.abspath(args.output))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(text)
    pro = [g for g in (data.get("games") or []) if g.get("source") == "pro"]
    print("%d 局职业（可播 %d） -> %s" % (
        len(pro), sum(1 for g in pro if g.get("playable")), os.path.abspath(args.output)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
