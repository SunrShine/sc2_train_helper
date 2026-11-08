"""对照组报告 + 录像库索引的 HTML 渲染。"""

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
.wrap{max-width:1180px;margin:0 auto;padding:0 20px}
header.top{padding:32px 0 16px}
h1{font-size:28px;margin:0 0 6px}
.sub{color:var(--muted);font-size:13.5px}
h2{font-size:18px;margin:32px 0 12px;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:4px;height:16px;border-radius:2px;background:var(--accent)}
h3{font-size:15px;margin:0 0 4px}
.card{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);
border-radius:14px;padding:16px 18px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.insight{border-left:3px solid var(--line);padding:9px 14px;margin-bottom:8px}
.insight.good{border-left-color:var(--good)}
.insight.warn{border-left-color:var(--warn)}
.insight.info{border-left-color:var(--info)}
.insight.bad{border-left-color:var(--bad)}
table.data{width:100%;border-collapse:collapse;font-size:13.5px}
table.data th{text-align:right;color:var(--muted);font-weight:600;font-size:12.5px;padding:8px 10px;
border-bottom:1px solid var(--line);white-space:nowrap}
table.data th:first-child,table.data td:first-child{text-align:left}
table.data td{padding:7px 10px;border-bottom:1px solid #222b38;text-align:right;font-variant-numeric:tabular-nums}
table.data td.l{text-align:left}
table.data td.win{color:var(--good);font-weight:700}
table.data td.lose{color:var(--warn)}
.bar-row{display:grid;grid-template-columns:150px 1fr 110px;gap:10px;align-items:center;padding:3px 0;font-size:13px}
.bar-track{height:8px;background:#202836;border-radius:999px;overflow:hidden}
.bar-fill{display:block;height:100%;border-radius:999px}
.bar-value{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums;font-size:12px}
.seq{font-size:12.5px;color:#c2cfe0;background:#141a23;border:1px solid #222b38;border-radius:10px;
padding:8px 10px;margin-top:6px;max-height:300px;overflow:auto;font-variant-numeric:tabular-nums}
.seq div{padding:1px 0;white-space:nowrap}
.seq .t{color:#7d8ca3;display:inline-block;width:70px}
.pair{border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:14px;background:var(--card)}
.pair .head{display:flex;justify-content:space-between;font-size:12.5px;color:var(--muted);margin-bottom:8px}
.pair .cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.pair .side{background:#141a23;border:1px solid #222b38;border-radius:12px;padding:10px}
.pair .side .who{font-size:13px;font-weight:700;margin-bottom:4px}
.pair .side .meta{font-size:12px;color:var(--muted);margin-bottom:6px}
.chip{display:inline-block;padding:1px 8px;border-radius:6px;background:#1d2740;border:1px solid #2c3c60;
font-size:12px;margin:0 4px 4px 0}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:4px;font-size:12px;color:var(--muted)}
.legend .lg{display:inline-flex;align-items:center;gap:5px}
.legend i{display:inline-block;width:12px;height:3px;border-radius:2px}
.chart{display:block;width:100%;height:auto;aspect-ratio:560/230}
.chart .grid{stroke:#263041}
.chart .axis{stroke:#3a4658}
.chart .axis-label{fill:#7d8ca3;font-size:10px;font-family:inherit}
.hint{color:var(--muted);font-size:12.5px;line-height:1.7}
footer{margin-top:40px;color:#66748a;font-size:12.5px;border-top:1px solid var(--line);padding-top:14px}
.warnrow{background:#2a1f1f}
@media (max-width:900px){.grid2,.pair .cols{grid-template-columns:1fr}}
"""


def _num(value, suffix=""):
    if value is None:
        return "-"
    try:
        return "%s%s" % (charts._fmt(float(value)), suffix)
    except (TypeError, ValueError):
        return h(str(value))


# --------------------------------------------------------------------------- 对照组
def render_compare(data):
    meta = data["meta"]
    me_group, pro_group = data["groups"]
    stats_me, stats_pro = data["stats"]
    labels = data["metric_labels"]

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>对照组 · %s</title>" % h(meta["matchup"]),
             "<style>%s</style></head><body><div class=\"wrap\">" % CSS]
    parts.append('<header class="top"><h1>对照组：我 vs %s</h1>' % h(meta["pro_name"]))
    parts.append('<div class="sub">对位 %s · 我 %d 局 · 对方 %d 局 · 指标取自每局的 replay 操作事件流</div>'
                 % (h(meta["matchup"]), len(me_group["rows"]), len(pro_group["rows"])))
    parts.append('<div class="sub small mut" style="margin-top:4px">%s</div></header>'
                 % h(me_group["root"] + "   ×   " + pro_group["root"]))

    if not me_group["rows"] or not pro_group["rows"]:
        parts.append('<div class="card">一组没有可用对局：检查一下录像目录、对位过滤（--matchup）和选手名。</div>')
        parts.append("</div></body></html>")
        return "".join(parts)

    # ---- 结论
    parts.append("<h2>一、结论</h2><div class=\"card\">")
    for line in data["verdict"]:
        parts.append('<div class="insight %s">%s</div>' % (h(line["level"]), h(line["text"])))
    parts.append("</div>")

    # ---- 指标对比
    parts.append('<h2>二、指标对比（中位数）</h2><div class="card"><table class="data"><thead><tr>'
                 "<th>指标</th><th>我</th><th>职业</th><th>倍数</th><th>我的区间</th><th>职业区间</th>"
                 "</tr></thead><tbody>")
    for index, (key, label) in enumerate(_metrics()):
        mine = stats_me.get(key) or {}
        theirs = stats_pro.get(key) or {}
        mine_median = mine.get("median")
        their_median = theirs.get("median")
        ratio = ""
        if mine_median is not None and their_median:
            ratio = "%.2f×" % (mine_median / their_median)
        parts.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s ~ %s</td>"
                     "<td>%s ~ %s</td></tr>"
                     % (h(labels[index]), _num(mine_median), _num(their_median), h(ratio),
                        _num(mine.get("min")), _num(mine.get("max")),
                        _num(theirs.get("min")), _num(theirs.get("max"))))
    parts.append("</tbody></table></div>")

    # ---- 逐局
    parts.append("<h2>三、逐局明细</h2><div class=\"grid2\">")
    for group in (me_group, pro_group):
        rows = "".join("<tr><td>%s</td><td class=\"l\">%s</td><td>%s</td><td>%s</td>"
                       "<td>%s</td><td>%s</td><td>%s</td></tr>"
                       % (h(row["date"][5:]), h(row["map"][:18]), h(row["matchup"]),
                          h(row["result"]), _num(row["cam_switches_per_min"]),
                          _num(row["windows_per_min"]), _num(row["window_share"], "%"))
                       for row in group["rows"][:30])
        parts.append('<div class="card"><h3>%s · %d 局</h3><table class="data"><thead><tr>'
                     "<th>日期</th><th>地图</th><th>对位</th><th>结果</th><th>视角/分</th>"
                     "<th>多线/分</th><th>覆盖</th></tr></thead><tbody>%s</tbody></table></div>"
                     % (h(group["label"]), len(group["rows"]), rows))
    parts.append("</div>")

    # ---- 相似窗口
    parts.append("<h2>四、窗口配对：同样的结构，他先做什么</h2>")
    parts.append('<div class="hint card" style="margin-bottom:14px">每个配对是这么找的：'
                 "把你的一个多线窗口抽成（跨度、指令数、时长、视角切换、指令间隔）五个数，"
                 "在职业侧所有窗口里找归一化距离最近的一个。距离越小，两边的“局面形状”越像。"
                 "看的时候只问一件事：<b>同样的两条线，他的第一条指令下给了哪边。</b></div>")
    if not data["pairs"]:
        parts.append('<div class="card">没有可配对的窗口（可能某一侧整局都没出现多线窗口）。</div>')
    for pair in data["pairs"]:
        parts.append(_pair_block(pair, meta))
    parts.append("<footer>对照组报告 · 指标为每局中位数 · 窗口配对用的是归一化特征距离，"
                 "不是“谁更强”的结论，只用来找模仿对象</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)


def _metrics():
    from .compare import METRICS
    return METRICS


def _pair_block(pair, meta):
    mine, theirs = pair["mine"], pair["theirs"]
    def side(window, who, shape):
        chips = "".join('<span class="chip">%s</span>' % h(label)
                        for label in window.get("labels", []))
        lines = "".join('<div><span class="t">%s</span>%s</div>'
                        % (h(_clock(line["t"])), h(line["text"]))
                        for line in window.get("lines", [])[:40])
        return ('<div class="side"><div class="who">%s</div>'
                '<div class="meta">%s · %s · %s · 时长 %ds · 指令 %d · 相距 %d 格 · 切换 %d 次</div>'
                '<div>%s</div><div class="seq">%s</div></div>'
                % (h(who), h(window["map"]), h(window["matchup"]), h(window["time"]),
                   int(round(window["duration"])), window["actions"],
                   int(round(window["spread"])), window["switch_count"], chips, lines))
    return ('<div class="pair"><div class="head"><span>%s · 距离 %.2f · 我的形状：%s · 他的形状：%s</span>'
            '<span>%s</span></div><div class="cols">%s%s</div></div>'
            % (h(pair.get("phase", "")), pair["distance"], h(pair["my_shape"]),
               h(pair["their_shape"]),
               ("" if pair.get("same_phase") else "（跨阶段匹配）") + h((mine.get("game") or "")[:40]),
               side(mine, "我：%s" % mine["player"], pair["my_shape"]),
               side(theirs, "%s：%s" % (meta["pro_name"], theirs["player"]), pair["their_shape"])))


def _clock(seconds):
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    return "%d:%02d.%d" % (whole // 60, whole % 60, int(round((seconds - whole) * 10)) % 10)


# --------------------------------------------------------------------------- 录像库
def render_library(index):
    summary = index["summary"]
    entries = index["entries"]
    ok = [e for e in entries if not e.get("error")]

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>录像库</title>",
             "<style>%s</style></head><body><div class=\"wrap\">" % CSS]
    parts.append('<header class="top"><h1>录像库</h1>')
    parts.append('<div class="sub">共 %d 个文件 · 解析成功 %d · 失败 %d · 我的 %d · 职业 %d</div>'
                 % (summary["total"], summary["parsed"], summary["failed"],
                    summary["me"], summary["pro"]))
    parts.append("</header>")

    if summary["pro_players"]:
        parts.append('<h2>职业选手文件夹</h2><div class="card">')
        for name, count in sorted(summary["pro_players"].items(), key=lambda kv: -kv[1]):
            parts.append('<span class="chip">%s · %d 局</span>' % (h(name), count))
        parts.append("</div>")

    if summary["suspect"]:
        parts.append("<h2>疑似放错位置</h2><div class=\"card\"><table class=\"data\"><thead><tr>"
                     "<th>文件</th><th>所在文件夹</th><th>问题</th></tr></thead><tbody>")
        for entry in summary["suspect"]:
            parts.append('<tr><td class="l">%s</td><td>%s</td><td class="l">%s</td></tr>'
                         % (h(entry["file"]), h(entry["pro_name"]), h(entry["suspect"])))
        parts.append("</tbody></table></div>")

    parts.append("<h2>全部录像</h2><div class=\"card\"><table class=\"data\"><thead><tr>"
                 "<th>归类</th><th>文件</th><th>地图</th><th>对位</th><th>时长</th>"
                 "<th>版本</th><th>日期</th></tr></thead><tbody>")
    for entry in sorted(ok, key=lambda e: (e["tag"], e.get("pro_name", ""), e["date"])):
        tag = "职业/%s" % entry["pro_name"] if entry["tag"] == "pro" else "我的"
        parts.append('<tr><td>%s</td><td class="l">%s</td><td class="l">%s</td><td>%s</td>'
                     "<td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (h(tag), h(entry["file"][:52]), h(entry["map"][:22]),
                        h(entry["matchup"]),
                        h("%d:%02d" % (int(entry["duration"]) // 60, int(entry["duration"]) % 60)),
                        h(str(entry["build"])), h(entry["date"][:10])))
    parts.append("</tbody></table></div>")

    failed = [e for e in entries if e.get("error")]
    if failed:
        parts.append("<h2>解析失败</h2><div class=\"card\"><table class=\"data\">"
                     "<thead><tr><th>文件</th><th>错误</th></tr></thead><tbody>")
        for entry in failed:
            parts.append('<tr><td class="l">%s</td><td class="l">%s</td></tr>'
                         % (h(entry["file"]), h(entry["error"])))
        parts.append("</tbody></table></div>")

    parts.append("<footer>库索引只读文件、不改目录；移动/归类请手动操作后重新生成。</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)


# --------------------------------------------------------------------------- 写文件
def _write(text, path):
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def write_compare_report(data, path):
    return _write(render_compare(data), path)


def write_library_report(index, path):
    return _write(render_library(index), path)
