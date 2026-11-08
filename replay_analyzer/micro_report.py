"""把 micro.analyze_micro() 的结果渲染成一份离线 HTML 报告。"""

import html
import os

from . import charts

h = html.escape

CSS = """
:root{--bg:#0e1116;--bg2:#151a22;--card:#1a2029;--card2:#202836;--line:#2a3340;
--text:#e6ecf5;--muted:#93a1b5;--good:#46d17e;--warn:#f2c45a;--bad:#ff6b6b;--info:#62b0ff;--accent:#7c9cff}
*{box-sizing:border-box}
body{margin:0;padding:0 0 60px;color:var(--text);
background:radial-gradient(1200px 600px at 50% -200px,#1b2436 0%,var(--bg) 60%);
font:15px/1.6 "Segoe UI","Microsoft YaHei",-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1150px;margin:0 auto;padding:0 20px}
header.top{padding:32px 0 16px}
h1{font-size:28px;margin:0 0 6px}
.sub{color:var(--muted);font-size:13.5px}
h2{font-size:18px;margin:32px 0 12px;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:4px;height:16px;border-radius:2px;background:var(--accent)}
.card{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);
border-radius:14px;padding:16px 18px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.metric{background:var(--card2);border-radius:10px;padding:10px 12px}
.metric .k{color:var(--muted);font-size:12px}
.metric .v{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.metric .n{color:var(--muted);font-size:11.5px}
.pcard{position:relative;overflow:hidden}
.pcard::after{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--pcolor)}
.pcard .who{display:flex;align-items:baseline;gap:8px;margin-bottom:10px}
.pcard .name{font-size:18px;font-weight:700}
.pcard .tag{color:var(--muted);font-size:12.5px}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:4px;font-size:12px;color:var(--muted)}
.legend .lg{display:inline-flex;align-items:center;gap:5px}
.legend i{display:inline-block;width:12px;height:3px;border-radius:2px}
.chart{display:block;width:100%;height:auto;aspect-ratio:560/230}
.chart .grid{stroke:#263041;stroke-width:1}
.chart .axis{stroke:#3a4658}
.chart .axis-label{fill:#7d8ca3;font-size:10px;font-family:inherit}
.chart-card h3{font-size:14.5px;margin:0 0 2px}
.chart-card .desc{color:var(--muted);font-size:12.5px;margin-bottom:6px}
.bar-row{display:grid;grid-template-columns:140px 1fr 64px;gap:10px;align-items:center;padding:3px 0;font-size:13px}
.bar-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{height:8px;background:#202836;border-radius:999px;overflow:hidden}
.bar-fill{display:block;height:100%;border-radius:999px}
.bar-value{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
table.data{width:100%;border-collapse:collapse;font-size:13.5px}
table.data th{text-align:right;color:var(--muted);font-weight:600;font-size:12.5px;padding:8px 10px;
border-bottom:1px solid var(--line);white-space:nowrap}
table.data th:first-child,table.data td:first-child{text-align:left}
table.data td{padding:8px 10px;border-bottom:1px solid #222b38;text-align:right;font-variant-numeric:tabular-nums}
table.data td.l{text-align:left}
table.data tr:last-child td{border-bottom:none}
.insight{border-left:3px solid var(--line);padding:10px 14px;margin-bottom:10px}
.insight.good{border-left-color:var(--good)}
.insight.warn{border-left-color:var(--warn)}
.insight.bad{border-left-color:var(--bad)}
.insight.info{border-left-color:var(--info)}
.insight .t{font-weight:700;font-size:14.5px;margin-bottom:2px}
.insight .d{color:var(--muted);font-size:13.5px}
.insight .x{font-size:13px;margin-top:6px;padding-top:6px;border-top:1px dashed #2a3340}
.insight .x b{color:var(--accent)}
details{margin:0}
details summary{cursor:pointer;color:var(--accent);font-size:12.5px;outline:none}
.seq{font-size:12.5px;color:#c2cfe0;background:#141a23;border:1px solid #222b38;border-radius:10px;
padding:8px 10px;margin-top:6px;max-height:260px;overflow:auto;font-variant-numeric:tabular-nums}
.seq div{padding:1px 0;white-space:nowrap}
.seq .t{color:#7d8ca3;display:inline-block;width:76px}
.hint{color:var(--muted);font-size:12.5px;line-height:1.75}
.hint b{color:var(--text)}
.pill{display:inline-block;padding:1px 9px;border-radius:999px;background:var(--card2);
border:1px solid var(--line);font-size:12px;color:var(--muted);margin:0 6px 6px 0}
.region-chip{display:inline-block;padding:1px 8px;border-radius:6px;background:#1d2740;
border:1px solid #2c3c60;font-size:12px;margin-right:4px}
footer{margin-top:40px;color:#66748a;font-size:12.5px;border-top:1px solid var(--line);padding-top:14px}
@media (max-width:860px){.grid2,.grid4{grid-template-columns:1fr}}
"""


def _clock(seconds):
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    return "%d:%02d.%d" % (whole // 60, whole % 60, int(round((seconds - whole) * 10)) % 10)


def render(micro, title=None):
    meta = micro["meta"]
    players = micro["players"]
    local = next((p for p in players if p["is_local"]), players[0] if players else None)

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>%s</title>" % h(title or ("%s · 操作细节分析" % meta["map"])),
             "<style>%s</style></head><body><div class=\"wrap\">" % CSS]

    parts.append('<header class="top"><h1>操作细节：多线与视角</h1>')
    parts.append('<div class="sub">%s · %s · %s · 解析 build %s</div>'
                 % (h(meta["map"]), h(meta["duration"]),
                    h(os.path.basename(meta.get("path", ""))), h(str(meta.get("protocol_build")))))
    parts.append("</header>")

    # ---- 指标卡
    parts.append('<h2>一、核心指标</h2><div class="grid2">')
    for player in players:
        parts.append(_player_card(player))
    parts.append("</div>")

    # ---- 练习建议
    if micro.get("drills"):
        parts.append('<h2>二、这局最该练什么</h2><div class="card">')
        for item in micro["drills"]:
            parts.append('<div class="insight %s"><div class="t">%s</div>'
                         '<div class="d">%s</div><div class="x"><b>练法：</b>%s</div></div>'
                         % (h(item["level"]), h(item["title"]), h(item["text"]),
                            h(item["drill"])))
        parts.append("</div>")

    # ---- 图表
    x_max = meta["duration_seconds"] / 60.0
    parts.append('<h2>三、节奏曲线</h2><div class="grid2">')
    series = []
    for player in players:
        series.append({"label": player["short_name"], "color": player["color"],
                       "points": [[i, v] for i, v in enumerate(player["switches_series"])]})
    parts.append(_chart_card("每分钟视角切换次数", "低于 8 次/分：视角黏住；高于 30 次/分：切得太碎",
                             series, x_max))
    series = []
    for player in players:
        series.append({"label": player["short_name"], "color": player["color"],
                       "points": [[i, v] for i, v in enumerate(player["window_series"])]})
    parts.append(_chart_card("每分钟多线窗口数", "同一时刻在相距 25 格以上的两个位置各下 ≥2 条指令",
                             series, x_max))
    for player in players:
        if player.get("cam_track"):
            parts.append(_chart_card("视角横坐标（%s）" % player["short_name"],
                                     "曲线在左右两端之间来回 = 在两条线之间切换",
                                     [{"label": player["short_name"], "color": player["color"],
                                       "points": player["cam_track"]}], x_max))
    parts.append("</div>")

    # ---- 注意力分布
    parts.append('<h2>四、注意力花在哪</h2><div class="grid2">')
    for player in players:
        top = player["attention"][:8]
        max_seconds = max([a["seconds"] for a in top], default=1) or 1
        bars = "".join(charts.bar_row(a["label"], a["seconds"], max_seconds,
                                      player["color"], suffix="s") for a in top)
        parts.append('<div class="card chart-card"><h3>%s</h3>'
                     '<div class="desc">按停留时长排序的视角分布</div>%s</div>'
                     % (h(player["short_name"]), bars))
    parts.append("</div>")

    # ---- 多线窗口
    parts.append('<h2>五、多线窗口（可以逐条复盘）</h2>')
    parts.append('<div class="hint card">下面每一行是一次“同时在两条线上下指令”的窗口'
                 '（6 秒内两个相距 ≥25 格的位置各 ≥2 条指令，相邻窗口会合并成一段）。'
                 '点“展开操作序列”能看到当时<b>先做了什么、后做了什么</b>——'
                 '复盘时问自己：这个顺序是不是最优？哪一条线的指令是多余的？'
                 '把视角切换次数少、指令间隔短的窗口当作模板。</div>')
    for player in players:
        parts.append(_window_table(player))
        if player.get("top_windows"):
            best = player["top_windows"][0]
            parts.append(_window_detail("含金量最高：%s（%s）" % (best["time"], player["short_name"]),
                                        best))

    # ---- 空窗 + 编队
    parts.append('<h2>六、空窗与编队</h2><div class="grid2">')
    for player in players:
        parts.append(_gap_card(player))
    parts.append("</div>")
    parts.append('<div class="grid2" style="margin-top:14px">')
    for player in players:
        parts.append(_hotkey_card(player))
    parts.append("</div>")

    parts.append("<footer>由 SC2 对局分析工具（micro 模式）生成 · 数据全部来自 replay 的操作事件流 ·"
                 " 多线窗口是启发式判定（%s 秒窗口 / %s 格距离）</footer>"
                 % ("6", "25"))
    parts.append("</div></body></html>")
    return "".join(parts)


# --------------------------------------------------------------------------- 片段
def _num(value, suffix=""):
    if value is None:
        return "-"
    return "%s%s" % (charts._fmt(float(value)), suffix)


def _player_card(player):
    cells = [
        ("视角切换 / 分", _num(player["cam_switches_per_min"]), "共 %d 次" % player["cam_switches"]),
        ("单次停留中位", _num(player["cam_dwell_median"], "s"), "最长 %s" % _num(player["cam_dwell_max"], "s")),
        ("指令 / 分", _num(player["cmd_per_min"]), "共 %d 条" % player["cmd_count"]),
        ("多线窗口", "%d" % player["window_count"], "%.2f 个/分" % player["windows_per_min"]),
        ("多线覆盖", _num(player["window_share"], "%"), "%ss" % _num(player["window_seconds"])),
        ("无指令空档", _num(player["cmd_gap_total"], "s"), "手在动但没下令 ≥8s"),
        ("反应时间中位", _num(player["reaction_median"], "s"), "%d 次交火" % player["reaction_count"]),
        ("用到的编队", "%d" % len([x for x in player["hotkeys"] if x["recall"] + x["set"] > 0]), "槽位"),
    ]
    body = "".join('<div class="metric"><div class="k">%s</div><div class="v">%s</div>'
                   '<div class="n">%s</div></div>' % (h(k), h(v), h(n))
                   for k, v, n in cells)
    return ('<div class="card pcard" style="--pcolor:%s">'
            '<div class="who"><span class="name">%s</span>'
            '<span class="tag">%s · %s%s</span></div>'
            '<div class="grid4">%s</div></div>'
            % (player["color"], h(player["name"]), h(player["race"]), h(player["result_zh"]),
               " · 你" if player["is_local"] else "", body))


def _chart_card(title, desc, series, x_max):
    svg = charts.line_chart(series, x_max=x_max, show_legend=False)
    legend = "".join('<span class="lg"><i style="background:%s"></i>%s</span>'
                     % (item["color"], h(item["label"])) for item in series)
    return ('<div class="card chart-card"><h3>%s</h3><div class="desc">%s</div>%s'
            '<div class="legend">%s</div></div>' % (h(title), h(desc), svg, legend))


def _window_table(player):
    if not player["windows"]:
        return ('<div class="card" style="margin-top:14px"><h3>%s</h3>'
                '<div class="hint">这局没有检测到多线窗口：大部分指令都集中在同一个位置。</div></div>'
                % h(player["short_name"]))
    rows = []
    for window in player["windows"]:
        chips = "".join('<span class="region-chip">%s</span>' % h(label)
                        for label in window["labels"])
        sequence = "".join('<div><span class="t">%s</span>%s</div>'
                           % (h(_clock(line["t"])), h(line["text"]))
                           for line in window["lines"])
        rows.append(
            '<tr><td>%s</td><td>%ss</td><td class="l">%s</td><td>%d</td><td>%s</td>'
            '<td>%s</td><td class="l"><details><summary>展开操作序列</summary>'
            '<div class="seq">%s</div></details></td></tr>'
            % (h(window["time"]), int(round(window["duration"])), chips,
               window["actions"], int(round(window["spread"])),
               _num(window.get("cmd_interval"), "s") if window.get("cmd_interval") else "-",
               sequence))
    head = ("<tr><th>开始</th><th>时长</th><th>两条线</th><th>指令数</th>"
            "<th>相距</th><th>指令间隔</th><th>操作顺序</th></tr>")
    return ('<div class="card" style="margin-top:14px"><h3>%s%s · %d 个窗口</h3>'
            '<table class="data"><thead>%s</thead><tbody>%s</tbody></table></div>'
            % (h(player["short_name"]), "（你）" if player["is_local"] else "",
               len(player["windows"]), head, "".join(rows)))


def _window_detail(title, window):
    chips = "".join('<span class="region-chip">%s</span>' % h(label)
                    for label in window.get("labels", []))
    sequence = "".join('<div><span class="t">%s</span>%s</div>'
                       % (h(_clock(line["t"])), h(line["text"])) for line in window["lines"])
    return ('<div class="card" style="margin-top:14px"><h3>%s</h3>'
            '<div class="desc">%s · 时长 %ds · 指令 %d · 相距 %d 格 · 窗口内视角切换 %d 次</div>'
            '<div class="seq" style="max-height:none">%s</div></div>'
            % (h(title), chips, int(round(window["duration"])), window["actions"],
               int(round(window["spread"])), window.get("switch_count", 0), sequence))


def _gap_card(player):
    rows = "".join('<tr><td>%s</td><td>%d 秒</td></tr>'
                   % (h(_clock(g["t0"])), int(round(g["dur"]))) for g in player["cmd_gaps"])
    if not rows:
        rows = '<tr><td colspan="2" class="l">没有超过 8 秒不下指令的时段</td></tr>'
    return ('<div class="card chart-card"><h3>%s · 最长的不下指令时段</h3>'
            '<div class="desc">视角在切、单位在选，但连续 8 秒以上没有发出任何指令'
            '（完全无操作合计 %s）</div>'
            '<table class="data"><thead><tr><th>开始</th><th>时长</th></tr></thead>'
            '<tbody>%s</tbody></table></div>' % (h(player["short_name"]),
                                                 _num(player["gap_total"], "s"), rows))


def _hotkey_card(player):
    rows = "".join('<tr><td>编队 %d</td><td>%d</td><td>%d</td></tr>'
                   % (item["group"], item["recall"], item["set"]) for item in player["hotkeys"][:8])
    if not rows:
        rows = '<tr><td colspan="3" class="l">没有编队操作</td></tr>'
    return ('<div class="card chart-card"><h3>%s · 编队使用</h3>'
            '<div class="desc">“调出”= 按数字键召回该编队；“重设”= 新建/追加/替换</div>'
            '<table class="data"><thead><tr><th>编队</th><th>调出</th><th>重设</th></tr></thead>'
            '<tbody>%s</tbody></table></div>' % (h(player["short_name"]), rows))


def render_trend(rows, title=None):
    """一批 replay 的操作指标趋势（练习打卡）。"""
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>%s</title>" % h(title or "操作指标趋势"),
             "<style>%s</style></head><body><div class=\"wrap\">" % CSS]
    if not rows:
        parts.append("<h1>还没有可用的对局</h1></div></body></html>")
        return "".join(parts)

    parts.append('<header class="top"><h1>操作指标趋势</h1>')
    parts.append('<div class="sub">%d 局 · %s → %s</div>'
                 % (len(rows), h(rows[0]["date"]), h(rows[-1]["date"])))
    parts.append("</header>")

    def series(key):
        out = []
        for index, row in enumerate(rows):
            value = row.get(key)
            if value is None:
                continue
            out.append([index + 1, float(value)])
        return out

    parts.append('<h2>趋势</h2><div class="grid2">')
    parts.append(_chart_card("视角切换（次/分）", "目标区间 12–20：太低=视野黏住，太高=切得太碎",
                             [{"label": "切视角", "color": "#7c9cff", "points": series("cam_switches_per_min")}],
                             len(rows)))
    parts.append(_chart_card("多线窗口（个/分）", "目标 ≥0.8：同一时间在两条线上下指令的密度",
                             [{"label": "多线窗口", "color": "#46d17e", "points": series("windows_per_min")}],
                             len(rows)))
    parts.append(_chart_card("多线覆盖（%）", "多线窗口占对局时长的比例，目标 ≥10%",
                             [{"label": "覆盖", "color": "#f2c45a", "points": series("window_share")}],
                             len(rows)))
    parts.append(_chart_card("不下指令空档（秒）", "越少越好，目标 <60 秒",
                             [{"label": "空档", "color": "#ff6b6b", "points": series("cmd_gap_total")}],
                             len(rows)))
    parts.append("</div>")

    parts.append('<h2>逐局明细</h2><div class="card"><table class="data"><thead><tr>'
                 "<th>#</th><th>日期</th><th>地图</th><th>对位</th><th>结果</th><th>时长</th>"
                 "<th>视角/分</th><th>停留</th><th>多线/分</th><th>覆盖</th><th>指令/分</th>"
                 "<th>空档</th><th>反应</th><th>编队</th></tr></thead><tbody>")
    for index, row in enumerate(rows, 1):
        parts.append("<tr><td>%d</td><td>%s</td><td class=\"l\">%s</td><td>%s</td><td>%s</td>"
                     "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                     "<td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (index, h(row["date"]), h(row["map"]),
                        h("%s%s" % (row["race"], row["opponent_race"])),
                        h(row["result"]), h(row["duration"]),
                        _num(row["cam_switches_per_min"]), _num(row["cam_dwell_median"], "s"),
                        _num(row["windows_per_min"]), _num(row["window_share"], "%"),
                        _num(row["cmd_per_min"]), _num(row["cmd_gap_total"], "s"),
                        _num(row["reaction_median"], "s"), row["hotkey_slots"]))
    parts.append("</tbody></table></div>")
    parts.append("<footer>逐局指标来自每局的 replay 操作事件流；趋势看的是习惯，不是单局表现。</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)


def write_trend_report(rows, output_path, title=None):
    text = render_trend(rows, title=title)
    directory = os.path.dirname(os.path.abspath(output_path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return output_path


def write_micro_report(micro, output_path, title=None):
    text = render(micro, title=title)
    directory = os.path.dirname(os.path.abspath(output_path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return output_path
