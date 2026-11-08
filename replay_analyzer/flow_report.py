"""把 flow.py 产出的 reports/tvt-flows.json 渲染成"流程命名核对表"。

用途：用户要打开录像、逐个核对流程命名。所以这张表的重点是
**每个流程 -> 代表录像的完整路径 + 能不能直接播 + 我的建议命名 + 你的命名（空着）**。

    python tools\\make_flow_table.py --json reports/tvt-flows.json -o reports/tvt-flows.html
"""

import html
import os

h = html.escape

CSS = """
:root{--bg:#0e1116;--bg2:#151a22;--card:#1a2029;--card2:#202836;--line:#2a3340;
--text:#e6ecf5;--muted:#93a1b5;--good:#46d17e;--warn:#f2c45a;--bad:#ff6b6b;--accent:#7c9cff}
*{box-sizing:border-box}
body{margin:0;padding:0 0 70px;color:var(--text);
background:radial-gradient(1200px 600px at 50% -200px,#1b2436 0%,var(--bg) 60%);
font:15px/1.65 "Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px}
header.top{padding:34px 0 10px}
h1{font-size:27px;margin:0 0 6px}
.sub{color:var(--muted);font-size:13.5px}
h2{font-size:18px;margin:34px 0 12px;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:4px;height:16px;border-radius:2px;background:var(--accent)}
.card{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);
border-radius:14px;padding:16px 18px;margin-bottom:14px}
.flow{border-left:3px solid var(--accent)}
.flow .head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.flow .name{font-size:18px;font-weight:700}
.flow .tag{color:var(--muted);font-size:12.5px}
.flow .reason{color:#c2cfe0;font-size:13px;margin:6px 0 10px}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0}
.kv div{background:var(--card2);border-radius:9px;padding:8px 10px;font-size:12.5px}
.kv b{display:block;color:var(--muted);font-weight:600;font-size:11.5px;margin-bottom:2px}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;padding:7px 8px;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:7px 8px;border-bottom:1px solid #222b38;vertical-align:top}
tr:last-child td{border-bottom:none}
.mono{font-family:Consolas,"Courier New",monospace;font-size:12px;word-break:break-all;color:#c2cfe0}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;
border:1px solid var(--line);background:#202836;color:var(--muted);white-space:nowrap}
.ok{color:var(--good);border-color:#2b5a3f}
.no{color:var(--warn);border-color:#5a4b2b}
.slot{border:1px dashed #3a4658;border-radius:8px;min-height:26px;min-width:130px;
background:#141a23;padding:3px 8px;color:#66748a;font-size:12px}
.hint{color:var(--muted);font-size:13px;line-height:1.85}
.hint b{color:var(--text)}
code{background:#141a23;padding:1px 6px;border-radius:5px;font-size:12.5px}
.big{font-size:15px}
footer{margin-top:38px;color:#66748a;font-size:12.5px;border-top:1px solid var(--line);padding-top:14px}
"""

OPEN_HELP = """
<div class="hint">
<p><b>怎么打开：</b>把 <code>.SC2Replay</code> 拖到
<code>D:\\StarCraft II\\Support64\\SC2Switcher_x64.exe</code> 上；
或者先进游戏，<b>F10 → 回放 → 打开回放</b>，再选文件；
也可以把文件复制到
<code>C:\\Users\\sun\\Documents\\StarCraft II\\Accounts\\556922532\\5-S2-1-8354391\\Replays\\Multiplayer\\</code>
下，它就会出现在游戏内的录像列表里。</p>
<p><b>版本限制：</b>SC2 只能播当前版本。本机 <code>Versions</code> 里装的是
Base93333 ~ Base98370，所以只有 build 93333 以上、且对应版本目录存在的 replay 能直接播
（你的对局是 98370，没问题）。职业录像里 build 84643 / 87702 / 88500 的那批
（2021–2022 年 ESL）现在打不开，要等客户端下载旧版本。</p>
</div>
"""


def _clock(seconds):
    try:
        seconds = max(0, int(round(float(seconds))))
    except Exception:
        return "?"
    return "%d:%02d" % (seconds // 60, seconds % 60)


def _fmt_share(value):
    try:
        return "%.0f%%" % (float(value) * 100.0)
    except Exception:
        return "?"


def _fmt_time(seconds):
    if seconds in (None, ""):
        return "-"
    return _clock(seconds)


def _fingerprint(flow):
    fp = flow.get("fingerprint") or {}
    prod = fp.get("production_5_10") or {}
    shares = fp.get("shares_5_10") or {}
    parts = []
    if prod:
        parts.append("5–10 分钟建筑：" + " / ".join(
            "%s×%s" % (name, ("%.1f" % value).rstrip("0").rstrip("."))
            for name, value in prod.items() if value))
    if shares:
        parts.append("兵种构成：" + " / ".join(
            "%s %s" % (name, _fmt_share(value)) for name, value in shares.items() if value))
    if fp.get("bases_10m") is not None:
        parts.append("10 分钟基地数：%s" % ("%.0f" % fp["bases_10m"]))
    if fp.get("army_moveout") is not None:
        parts.append("主力出门（中位）：%s" % _fmt_time(fp["army_moveout"]))
    return parts


def render(data, title="TvT 流程命名核对表"):
    meta = data.get("meta") or {}
    flows = data.get("flows") or []
    games = data.get("games") or []
    total_player_games = meta.get("player_games") or sum(
        len(g.get("players") or []) for g in games)
    # 例子条目里没有时长，从 games 里按路径查
    duration_of = {g.get("path"): g.get("duration") for g in games}

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>%s</title><style>%s</style></head><body><div class=\"wrap\">" % (h(title), CSS)]
    parts.append("<header class=\"top\"><h1>%s</h1>" % h(title))
    parts.append('<div class="sub">%s · %d 局对局 / %d 个玩家局 · 来源：我的 %s 局 + 职业 %s 局 · '
                 '无法归类 %s</div>'
                 % (h(meta.get("matchup", "TvT")), len(games), total_player_games,
                    (meta.get("sources") or {}).get("mine", {}).get("games", "?"),
                    (meta.get("sources") or {}).get("pro", {}).get("games", "?"),
                    _fmt_share(meta.get("unknown_share", 0))))
    parts.append("</header>")

    parts.append('<h2>一、先看这个：怎么打开录像</h2><div class="card">%s</div>' % OPEN_HELP)

    parts.append('<h2>二、流程候选（%d 类）</h2>' % len(flows))
    parts.append('<div class="hint" style="margin-bottom:10px">'
                 "每一类下面是建议命名 + 判定依据 + 代表录像。"
                 "<b>看完录像后，在“你的命名”那一栏写你认可的名字</b>（直接告诉我也可以），"
                 "下一版就按你的命名重出报告。</div>")

    for index, flow in enumerate(flows, 1):
        name = flow.get("name") or flow.get("key") or "?"
        parts.append('<div class="card flow">')
        parts.append('<div class="head"><span class="name">%d. %s</span>'
                     '<span class="tag">%s 个玩家局 · 占比 %s</span></div>'
                     % (index, h(name), flow.get("player_games", "?"),
                        _fmt_share(flow.get("share", 0))))

        reason = flow.get("reason") or (flow.get("fingerprint") or {}).get("reason")
        if reason:
            parts.append('<div class="reason">%s</div>' % h(reason))

        parts.append('<div class="kv">')
        for item in _fingerprint(flow):
            label, _, value = item.partition("：")
            parts.append("<div><b>%s</b>%s</div>" % (h(label), h(value)))
        parts.append("</div>")

        examples = flow.get("examples") or []
        if examples:
            parts.append("<table><thead><tr><th>代表录像</th><th>地图 / 时长</th><th>版本</th>"
                         "<th>能直接播</th><th>为什么归这类</th><th>你的命名</th></tr></thead><tbody>")
            for example in examples:
                playable = example.get("playable")
                badge = ('<span class="pill ok">能播</span>' if playable
                         else '<span class="pill no">需旧版本</span>')
                length = example.get("duration")
                if length is None:
                    length = duration_of.get(example.get("path"))
                parts.append(
                    "<tr><td class='mono'>%s<div style='color:#66748a'>%s</div></td>"
                    "<td>%s<br>%s</td><td class='mono'>%s</td><td>%s</td><td>%s</td>"
                    "<td><div class='slot'>待填</div></td></tr>"
                    % (h(example.get("path", "")), h(example.get("file", "")),
                       h(example.get("map", "")), _clock(length),
                       h(str(example.get("build", ""))), badge,
                       h(example.get("reason", ""))))
            parts.append("</tbody></table>")
        parts.append("</div>")

    parts.append('<h2>三、全部对局（双方各自归类）</h2><div class="card"><table><thead><tr>'
                 "<th>文件</th><th>来源</th><th>版本</th><th>双方流程</th><th>时长</th>"
                 "</tr></thead><tbody>")
    for game in games:
        players = game.get("players") or []
        cells = []
        for player in players:
            role = "我" if player.get("role") == "me" else "对手"
            cells.append("%s：%s" % (role, h(player.get("flow_name") or player.get("flow") or "?")))
        playable = game.get("playable")
        parts.append("<tr><td class='mono'>%s<div style='color:#66748a'>%s</div></td>"
                     "<td>%s</td><td class='mono'>%s</td><td>%s</td><td>%s</td></tr>"
                     % (h(game.get("file", "")), h(game.get("path", "")),
                        h(game.get("source", "")), h(str(game.get("build", ""))),
                        "<br>".join(cells), _clock(game.get("duration"))))
    parts.append("</tbody></table></div>")

    parts.append("<footer>由 tools/make_flow_table.py 从 reports/tvt-flows.json 生成。"
                 "流程分类规则见 replay_analyzer/flow.py 的 docstring；"
                 "所有判定都是本地几何/建造事件推导，不是人工标注。</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)


def write(data, path, title="TvT 流程命名核对表"):
    text = render(data, title=title)
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path
