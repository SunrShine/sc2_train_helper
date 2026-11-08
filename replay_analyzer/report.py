"""把分析结果渲染成单文件 HTML 报告。

设计目标：双击就能看、不联网、不依赖 CDN；所有图表都是内联 SVG。
"""

import html
import os

from . import charts

h = html.escape


CSS = """
:root {
  --bg: #0e1116;
  --bg-soft: #151a22;
  --card: #1a2029;
  --card-2: #202836;
  --line: #2a3340;
  --text: #e6ecf5;
  --muted: #93a1b5;
  --good: #46d17e;
  --warn: #f2c45a;
  --bad: #ff6b6b;
  --info: #62b0ff;
  --accent: #7c9cff;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 0 64px;
  background: radial-gradient(1200px 600px at 50% -200px, #1b2436 0%, var(--bg) 60%);
  color: var(--text);
  font: 15px/1.6 "Segoe UI", "Microsoft YaHei", -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 0 20px; }
header.top { padding: 34px 0 18px; }
h1 { font-size: 30px; margin: 0 0 6px; letter-spacing: .5px; }
.sub { color: var(--muted); font-size: 13.5px; }
.sub b { color: var(--text); font-weight: 600; }
.pill {
  display: inline-block; padding: 1px 9px; border-radius: 999px;
  background: var(--card-2); border: 1px solid var(--line);
  font-size: 12px; color: var(--muted); margin-right: 6px;
}
h2 { font-size: 18px; margin: 34px 0 12px; display: flex; align-items: center; gap: 8px; }
h2::before { content: ""; width: 4px; height: 16px; border-radius: 2px; background: var(--accent); }
.card {
  background: linear-gradient(180deg, var(--card) 0%, var(--bg-soft) 100%);
  border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px;
}
.scoreboard { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.summary { margin-top: 16px; color: #c6d2e3; font-size: 14.5px; line-height: 1.75; }
.pcard { position: relative; overflow: hidden; }
.pcard::after {
  content: ""; position: absolute; inset: 0 0 auto 0; height: 3px; background: var(--pcolor);
}
.pcard .who { display: flex; align-items: baseline; gap: 10px; }
.pcard .name { font-size: 19px; font-weight: 700; }
.pcard .race { color: var(--muted); font-size: 13px; }
.pcard .result {
  margin-left: auto; font-size: 13px; font-weight: 700; padding: 2px 10px;
  border-radius: 999px; border: 1px solid currentColor;
}
.result.win { color: var(--good); }
.result.loss { color: var(--bad); }
.result.tie, .result.unknown { color: var(--muted); }
.ptable { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13.5px; }
.ptable td { padding: 5px 0; border-top: 1px solid var(--line); }
.ptable td:first-child { color: var(--muted); }
.ptable td:last-child { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
.you { color: var(--accent); font-size: 12px; border: 1px solid var(--accent);
       border-radius: 6px; padding: 0 6px; margin-left: 6px; }
.insights { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.insight { border-left: 3px solid var(--line); padding: 11px 14px; }
.insight.good { border-left-color: var(--good); }
.insight.warn { border-left-color: var(--warn); }
.insight.bad { border-left-color: var(--bad); }
.insight.info { border-left-color: var(--info); }
.insight .t { font-weight: 700; margin-bottom: 2px; font-size: 14.5px; }
.insight .d { color: var(--muted); font-size: 13.5px; }
.chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.chart-card h3 { font-size: 14.5px; margin: 0 0 2px; font-weight: 700; }
.chart-card .desc { color: var(--muted); font-size: 12.5px; margin-bottom: 6px; }
.chart { display: block; width: 100%; height: auto; aspect-ratio: 560 / 230;
  overflow: visible; }
.legend { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 4px;
  font-size: 12px; color: var(--muted); }
.legend .lg { display: inline-flex; align-items: center; gap: 5px; }
.legend i { display: inline-block; width: 12px; height: 3px; border-radius: 2px; }
.chart .grid { stroke: #263041; stroke-width: 1; }
.chart .axis { stroke: #3a4658; stroke-width: 1; }
.chart .axis-label { fill: #7d8ca3; font-size: 10px; font-family: inherit; }
.chart .legend { fill: #aab8cc; font-size: 10.5px; font-family: inherit; }
.empty-chart { color: var(--muted); font-size: 13px; padding: 30px 0; text-align: center; }
table.data { width: 100%; border-collapse: collapse; font-size: 13.5px; }
table.data th {
  text-align: right; color: var(--muted); font-weight: 600; font-size: 12.5px;
  padding: 8px 10px; border-bottom: 1px solid var(--line); white-space: nowrap;
}
table.data th:first-child, table.data td:first-child { text-align: left; }
table.data td { padding: 8px 10px; border-bottom: 1px solid #222b38; text-align: right;
  font-variant-numeric: tabular-nums; }
table.data tr:last-child td { border-bottom: none; }
table.data td.win { color: var(--good); font-weight: 700; }
table.data td.lose { color: var(--bad); }
.battles { display: grid; grid-template-columns: 1fr; gap: 10px; }
.battle { display: grid; grid-template-columns: 92px 1fr; gap: 14px; align-items: center; }
.battle .when { font-size: 16px; font-weight: 700; font-variant-numeric: tabular-nums; }
.battle .when small { display: block; color: var(--muted); font-weight: 400; font-size: 11.5px; }
.battle .sides { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.side { border-radius: 10px; padding: 8px 12px; background: var(--card-2); }
.side .n { font-size: 13px; color: var(--muted); }
.side .v { font-size: 15px; font-weight: 700; }
.side .u { font-size: 12px; color: var(--muted); margin-top: 2px; }
.tabs { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.tab {
  padding: 6px 14px; border-radius: 999px; background: var(--card-2);
  border: 1px solid var(--line); color: var(--muted); cursor: pointer; font-size: 13px;
}
.tab.active { color: #0e1116; background: var(--accent); border-color: var(--accent); font-weight: 700; }
.chips { display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; }
.chip {
  font-size: 12px; padding: 3px 10px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--line); color: var(--muted); background: transparent;
}
.chip.active { color: var(--text); border-color: var(--accent); background: #1d2740; }
.bo { max-height: 620px; overflow: auto; border-radius: 12px; border: 1px solid var(--line); }
.bo table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.bo td { padding: 6px 12px; border-bottom: 1px solid #1f2836; }
.bo tr:hover td { background: #1d2530; }
.bo td.t { color: var(--muted); width: 70px; font-variant-numeric: tabular-nums; }
.bo td.dot { width: 22px; }
.bo .d { display: inline-block; width: 7px; height: 7px; border-radius: 50%; }
.bo td.k { color: var(--muted); font-size: 12px; text-align: right; width: 90px; }
.eco-dot { background: #4cc9f0; }
.prod-dot { background: #f2c45a; }
.tech-dot { background: #b98cff; }
.army-dot { background: #ff8a65; }
.defense-dot { background: #9ccc65; }
.expansion-dot { background: #46d17e; }
.upgrade-dot { background: #ffd54f; }
.mut { color: var(--muted); }
.small { font-size: 12.5px; }
footer { margin-top: 44px; color: #66748a; font-size: 12.5px; border-top: 1px solid var(--line); padding-top: 16px; }
.bar-row { display: grid; grid-template-columns: 150px 1fr 64px; gap: 10px; align-items: center;
  padding: 4px 0; font-size: 13px; }
.bar-label { color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-track { height: 8px; background: #202836; border-radius: 999px; overflow: hidden; }
.bar-fill { display: block; height: 100%; border-radius: 999px; }
.bar-value { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
.cols2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 860px) {
  .scoreboard, .insights, .chart-grid, .cols2, .battle .sides { grid-template-columns: 1fr; }
  .battle { grid-template-columns: 1fr; }
}
"""

JS = """
document.querySelectorAll('[data-tabs]').forEach(function (group) {
  var tabs = group.querySelectorAll('.tab');
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      tabs.forEach(function (t) { t.classList.remove('active'); });
      tab.classList.add('active');
      var target = tab.getAttribute('data-target');
      group.querySelectorAll('[data-tabpane]').forEach(function (pane) {
        pane.hidden = pane.getAttribute('data-tabpane') !== target;
      });
    });
  });
});
document.querySelectorAll('[data-chips]').forEach(function (group) {
  var chips = group.querySelectorAll('.chip');
  var pane = document.querySelector(group.getAttribute('data-chips'));
  chips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      chips.forEach(function (c) { c.classList.remove('active'); });
      chip.classList.add('active');
      var stage = chip.getAttribute('data-stage');
      pane.querySelectorAll('tr[data-stage]').forEach(function (row) {
        row.hidden = stage !== 'all' && row.getAttribute('data-stage') !== stage;
      });
    });
  });
});
"""

STAGE_LABEL = {
    "eco": "经济",
    "prod": "生产",
    "tech": "科技",
    "army": "部队",
    "defense": "防守",
    "expansion": "扩张",
    "upgrade": "升级",
}


def _num(value, suffix=""):
    if value is None:
        return "-"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return h(str(value))
    if suffix:
        return "%s%s" % (charts._fmt(value), suffix)
    return charts._fmt(value)


def render(report, title=None):
    """report 为 metrics.analyze() 的返回值。"""
    meta = report["meta"]
    players = report["players"]
    local = report.get("local") or {}
    local_index = local.get("index")
    page_title = title or ("%s · 对局分析" % meta["map"])

    parts = []
    parts.append("<!doctype html><html lang=\"zh-CN\"><head>")
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append("<title>%s</title>" % h(page_title))
    parts.append("<style>%s</style>" % CSS)
    parts.append("</head><body>")
    parts.append('<div class="wrap">')

    # ---- 顶部信息
    parts.append('<header class="top">')
    parts.append("<h1>%s</h1>" % h(meta["map"]))
    sub = ['<span class="pill">时长 %s</span>' % h(meta["duration"]),
           '<span class="pill">%s</span>' % h(meta["game_speed"]),
           '<span class="pill">%s</span>' % h(meta.get("version") or ""),
           '<span class="pill">%s</span>' % ("天梯排位" if meta.get("is_ladder") else "自定义"),
           '<span class="pill">%d 人</span>' % meta.get("players_count", 0)]
    if meta.get("date"):
        sub.append('<span class="pill">%s</span>' % h(meta["date"]))
    parts.append('<div class="sub">%s</div>' % "".join(sub))
    parts.append('<div class="sub small mut" style="margin-top:6px">%s</div>'
                 % h(os.path.basename(meta.get("path", ""))))
    parts.append("</header>")

    if report.get("summary"):
        parts.append('<section class="card summary">%s</section>' % h(report["summary"]))

    # ---- 双方总览
    parts.append('<h2>双方数据</h2><div class="scoreboard">')
    for player in players:
        parts.append(_player_card(player, report))
    parts.append("</div>")

    # ---- 洞察
    if report.get("insights"):
        parts.append("<h2>关键结论</h2><div class=\"insights\">")
        for insight in report["insights"]:
            parts.append('<div class="card insight %s"><div class="t">%s</div>'
                         '<div class="d">%s</div></div>'
                         % (h(insight["level"]), h(insight["title"]), h(insight["text"])))
        parts.append("</div>")

    # ---- 曲线
    parts.append("<h2>经济与军队曲线</h2><div class=\"chart-grid\">")
    x_max = meta["duration_seconds"] / 60.0
    for timeline in report["timelines"]:
        series = []
        for item in timeline["series"]:
            series.append({
                "label": item["label"],
                "color": item["color"],
                "points": item["points"],
                "dash": item.get("dash", False),
            })
        parts.append(_chart_card(timeline["title"], timeline["desc"], series, x_max))
    apm_series = []
    for player in players:
        data = report["apm"]["series"].get(str(player["player_id"]))
        if data and data.get("apm"):
            apm_series.append({
                "label": player["short_name"],
                "color": player["color"],
                "points": [[i, v] for i, v in enumerate(data["apm"]) if i <= x_max],
            })
    if apm_series:
        parts.append(_chart_card("APM 曲线", "每分钟操作数（含选中 / 编队等操作）",
                                 apm_series, x_max))
    parts.append("</div>")

    # ---- 里程碑对比
    if report.get("milestones"):
        parts.append("<h2>关键时刻对比</h2>")
        parts.append(_milestone_table(report, players))
    parts.append(_expansion_card(report, players))

    # ---- 战斗
    if report.get("battles"):
        parts.append("<h2>主要交战</h2><div class=\"battles\">")
        for battle in report["battles"]:
            parts.append(_battle_card(battle, players))
        parts.append("</div>")

    # ---- 建造顺序
    parts.append("<h2>建造顺序</h2>")
    parts.append(_build_order_block(report, players))

    # ---- 构成
    parts.append("<h2>单位构成</h2><div class=\"cols2\">")
    for player in players:
        rows = report["composition"].get(str(player["player_id"])) or []
        parts.append(_composition_card(player, rows))
    parts.append("</div>")

    # ---- 聊天
    if report.get("chat"):
        parts.append("<h2>聊天记录</h2><div class=\"card chat\">%s</div>"
                     % "".join('<div><span class="mut">%s</span> '
                               '<b style="color:%s">%s</b>：%s</div>'
                               % (h(item["time"]), item.get("color", "#ccc"),
                                  h(item["name"]), h(item["text"]))
                               for item in report["chat"]))

    # ---- 页脚
    note = ""
    if meta.get("protocol_note"):
        note = "<div class=\"small mut\" style=\"margin-top:6px\">解析提示：%s</div>" % h(meta["protocol_note"])
    parts.append('<footer>由 SC2 对局分析工具生成 · 解析 build %s · 文件 %s%s</footer>'
                 % (h(str(meta.get("protocol_build"))), h(meta.get("path", "")), note))
    parts.append("</div>")
    parts.append("<script>%s</script>" % JS)
    parts.append("</body></html>")
    return "".join(parts)


# --------------------------------------------------------------------------- 片段
def _chart_card(title, desc, series, x_max):
    svg = charts.line_chart(series, x_max=x_max, show_legend=False)
    legend = []
    for item in series:
        style = "background:%s" % item["color"]
        if item.get("dash"):
            style = ("background:transparent;border-top:2px dashed %s;height:0"
                     % item["color"])
        legend.append('<span class="lg"><i style="%s"></i>%s</span>'
                      % (style, h(item["label"])))
    return ('<div class="card chart-card"><h3>%s</h3><div class="desc">%s</div>%s'
            '<div class="legend">%s</div></div>'
            % (h(title), h(desc), svg, "".join(legend)))


def _expansion_card(report, players):
    rows = []
    for player in players:
        entries = (report.get("expansions") or {}).get(str(player["player_id"])) or []
        chips = []
        for entry in entries:
            label = "初始基地" if entry["index"] == 0 else "第 %d 基地" % (entry["index"] + 1)
            chips.append('<span class="pill">%s %s</span>'
                         % (h(label), h(_fmt_clock(entry["t"]))))
        rows.append('<div><div class="dim" style="color:%s;font-weight:700">%s%s</div>'
                    '<div>%s</div></div>'
                    % (player["color"], h(player["name"]),
                       "（你）" if player.get("is_local") else "",
                       "".join(chips) or '<span class="mut small">没有记录</span>'))
    return '<div class="card" style="margin-top:14px"><div class="cols2">%s</div></div>' % "".join(rows)


def _fmt_clock(seconds):
    seconds = int(round(seconds))
    return "%d:%02d" % (seconds // 60, seconds % 60)


def _player_card(player, report):
    totals = report.get("stats_totals", {}).get(str(player["player_id"]), {})
    you = '<span class="you">你</span>' if player.get("is_local") else ""
    result_class = player["result"] if player["result"] in ("win", "loss", "tie") else "unknown"
    rows = [
        ("APM", _num(player["apm"])),
        ("峰值 APM", _num(player["apm_peak"])),
        ("农民峰值", "%d" % player["workers_max"]),
        ("军队价值峰值", _num(player["army_peak"])),
        ("人口峰值", "%.0f" % player["supply_peak"]),
        ("累计采集", _num(totals.get("collected"))),
        ("损失资源", _num(player["lost"])),
        ("击杀资源", _num(player["killed"])),
        ("基地数", "%d" % player["bases"]),
    ]
    body = "".join("<tr><td>%s</td><td>%s</td></tr>" % (h(k), h(v)) for k, v in rows)
    return (
        '<div class="card pcard" style="--pcolor:%s">'
        '<div class="who"><span class="name">%s</span>%s'
        '<span class="race">%s</span>'
        '<span class="result %s">%s</span></div>'
        '<table class="ptable">%s</table></div>'
        % (player["color"], h(player["name"]), you, h(player["race_name"]),
           result_class, h(player["result_zh"]), body)
    )


def _milestone_table(report, players):
    head = ["时间"]
    for player in players:
        head.append("%s 农民" % player["short_name"])
        head.append("%s 军队" % player["short_name"])
        head.append("%s 收入" % player["short_name"])
    rows = []
    for row in report["milestones"]:
        cells = ["<td>%d 分</td>" % row["minute"]]
        values = [(cell["workers"], cell["army"], cell["income"]) for cell in row["cells"]]
        best_workers = max(v[0] for v in values) if values else 0
        best_army = max(v[1] for v in values) if values else 0
        best_income = max(v[2] for v in values) if values else 0
        for workers, army, income in values:
            cells.append('<td class="%s">%d</td>'
                         % ("win" if workers == best_workers and best_workers > 0 else "",
                            workers))
            cells.append('<td class="%s">%s</td>'
                         % ("win" if army == best_army and best_army > 0 else "",
                            _num(army)))
            cells.append('<td class="%s">%s</td>'
                         % ("win" if income == best_income and best_income > 0 else "",
                            _num(income)))
        rows.append("<tr>%s</tr>" % "".join(cells))
    header = "".join("<th>%s</th>" % h(item) for item in head)
    return ('<div class="card"><table class="data"><thead><tr>%s</tr></thead>'
            '<tbody>%s</tbody></table></div>' % (header, "".join(rows)))


def _battle_card(battle, players):
    sides = []
    for row in battle["rows"]:
        units = ", ".join("%s×%d" % (k, v) for k, v in list(row["names"].items())[:4])
        sides.append(
            '<div class="side"><div class="n">%s%s</div>'
            '<div class="v" style="color:%s">损失 %s 资源</div>'
            '<div class="u">%d 个单位%s</div></div>'
            % (h(row["short_name"]), '（你）' if row["is_local"] else "",
               row["color"], _num(row["value"]), row["units"],
               ("：" + h(units)) if units else ""))
    return ('<div class="card battle"><div class="when">%s<small>持续 %d 秒</small></div>'
            '<div class="sides">%s</div></div>' % (h(battle["time"]),
                                                    int(battle["duration"]), "".join(sides)))


def _build_order_block(report, players):
    tabs = []
    panes = []
    for index, player in enumerate(players):
        active = " active" if index == 0 else ""
        tabs.append('<div class="tab%s" data-target="bo-%d">%s%s</div>'
                    % (active, index, h(player["name"]), "（你）" if player.get("is_local") else ""))
        rows = report["build_orders"].get(str(player["player_id"])) or []
        body = []
        for event in rows:
            stage = event["stage"]
            label = event["label"]
            if event["kind"] == "upgrade":
                label = "升级：" + label
            body.append(
                '<tr data-stage="%s"><td class="t">%s</td>'
                '<td class="dot"><span class="d %s-dot"></span></td>'
                '<td>%s</td><td class="k">%s</td></tr>'
                % (h(stage), h(event["time"]), h(stage), h(label),
                   h(STAGE_LABEL.get(stage, ""))))
        panes.append('<div data-tabpane="bo-%d"%s><div class="bo"><table>%s</table></div></div>'
                     % (index, "" if index == 0 else " hidden", "".join(body)))

    chips = ['<span class="chip active" data-stage="all">全部</span>']
    for key, label in (("eco", "经济"), ("prod", "生产"), ("tech", "科技"),
                       ("army", "部队"), ("upgrade", "升级"), ("expansion", "扩张")):
        chips.append('<span class="chip" data-stage="%s">%s</span>' % (key, label))
    return ('<div class="card" data-tabs><div class="tabs">%s</div>'
            '<div data-chips="#bo-chips">%s</div>'
            '<div id="bo-chips">%s</div></div>'
            % ("".join(tabs), "".join(chips), "".join(panes)))


def _composition_card(player, rows):
    if not rows:
        return '<div class="card"><h3>%s</h3><div class="mut small">无数据</div></div>' % h(player["name"])
    max_value = max((row["value"] for row in rows), default=1) or 1
    bars = []
    for row in rows[:12]:
        bars.append(charts.bar_row(row["label"], row["value"], max_value, player["color"]))
    table_rows = "".join(
        "<tr><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%s</td></tr>"
        % (h(row["label"]), row["built"], row["alive"], row["died"], _num(row["value"]))
        for row in rows[:14])
    return ('<div class="card"><h3>%s%s</h3>'
            '<div class="small mut" style="margin-bottom:8px">按资源价值排序</div>'
            '<div>%s</div>'
            '<table class="data" style="margin-top:10px"><thead><tr>'
            '<th>单位</th><th>生产</th><th>存活</th><th>损失</th><th>资源</th>'
            '</tr></thead><tbody>%s</tbody></table></div>'
            % (h(player["name"]), "（你）" if player.get("is_local") else "",
               "".join(bars), table_rows))


def write_report(report, output_path, title=None):
    html_text = render(report, title=title)
    directory = os.path.dirname(os.path.abspath(output_path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(html_text)
    return output_path
