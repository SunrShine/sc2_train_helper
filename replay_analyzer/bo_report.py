"""把 :func:`replay_analyzer.bo.build_summary` 的结果渲染成一份离线 HTML 报告。

风格沿用 ``micro_report.CSS``（深色），图表用内联 SVG（``charts.py`` + 本文件里的
时间轴），**不引用任何 CDN / 外部字体 / JS**，双击就能离线打开。

报告结构：

1. 关键结论（含必须说清楚的 caveat）
2. 我的两类开局：横向时间轴 + 均值/中位数 + 兵种构成
3. 职业选手（Maru）同类分析
4. 我 vs Maru 关键时间点对照
5. 签名 / 风格分布
6. 逐局明细
7. 判读口径（signature 词表 + 判定顺序 + 时间分段）
"""

import html
import os

from . import charts
from .bo import PHASES, PHASE_ZH, STYLE_WINDOW
from .micro_report import CSS

h = html.escape

#: 时间轴上的节点：(key_timing 字段, 展示名, 颜色)
TIMELINE_NODES = (
    ("first_gas", "气矿", "#62b0ff"),
    ("first_expand", "二矿", "#46d17e"),
    ("first_factory", "重工厂", "#f2c45a"),
    ("first_starport", "星港", "#b48cff"),
    ("third_rax", "三兵营", "#ff8b6b"),
    ("army_moveout", "主力出门", "#ff6b6b"),
)

PHASE_COLORS = {
    "opening": "#171d27",
    "early": "#141a23",
    "mid": "#171d27",
    "late": "#141a23",
}

EXTRA_CSS = """
.bo-title{font-size:22px;margin:0 0 4px}
.bo-tl{display:block;width:100%;height:auto}
.bo-tl text{font-family:inherit}
.bo-tl .axis{stroke:#3a4658}
.bo-tl .rail{stroke:#2f3b4d;stroke-width:2}
.bo-tl .tick{fill:#7d8ca3;font-size:10px}
.bo-tl .phase-label{fill:#4f5e73;font-size:9.5px;letter-spacing:.6px}
.bo-tl .node-label{fill:#c2cfe0;font-size:10.5px}
.bo-tl .node-time{fill:#e6ecf5;font-size:10.5px;font-weight:700}
.chip{display:inline-block;padding:2px 9px;border-radius:999px;background:#1d2740;border:1px solid #2c3c60;
font-size:12px;margin:0 6px 6px 0;color:#c2cfe0}
.chip b{color:#fff;font-weight:700}
.cluster-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;margin-bottom:2px}
.cluster-head .name{font-size:17px;font-weight:700}
.cluster-head .meta{color:#93a1b5;font-size:12.5px}
.share-bar{height:10px;border-radius:999px;background:#202836;overflow:hidden;margin:8px 0 10px}
.share-bar span{display:block;height:100%}
.tl-wrap{background:#121822;border:1px solid #222b38;border-radius:12px;padding:8px 10px 4px;margin:10px 0}
.bo-note{border-left:3px solid #f2c45a;padding:8px 12px;margin:8px 0;color:#c2cfe0;font-size:13px;background:#161c26}
.bo-note b{color:#f2c45a}
.mini{color:#93a1b5;font-size:12px}
td.bo-file{max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.right{text-align:right}
table.data th.l,table.data td.l{text-align:left}
"""


# --------------------------------------------------------------------------- 小工具

def clock(seconds):
    """秒 -> ``M:SS``；None -> ``-``。"""
    if seconds is None:
        return "-"
    whole = max(0, int(round(float(seconds))))
    return "%d:%02d" % (whole // 60, whole % 60)


def _pct(value):
    if value is None:
        return "-"
    return "%.0f%%" % (float(value) * 100.0)


def _share_bar(share, colors=("#7c9cff", "#46d17e", "#f2c45a")):
    width = max(1.0, min(100.0, float(share) * 100.0))
    return ('<div class="share-bar"><span style="width:%.1f%%;background:%s"></span></div>'
            % (width, colors[0]))


def _chip(text, value):
    return '<span class="chip">%s <b>%s</b></span>' % (h(text), h(value))


def _durations(summary):
    games = summary["games"]
    spans = [g["game"]["duration"] for g in games if g["game"]["duration"]]
    return max(spans) if spans else 900.0


# --------------------------------------------------------------------------- 时间轴

def timeline_svg(median, max_seconds=720.0, width=920.0, height=150.0, title=""):
    """一条横向时间轴：阶段底色 + 关键节点。纯 SVG，无 JS。"""
    pad_left, pad_right = 54.0, 18.0
    rail_y = height - 66.0

    nodes_raw = []
    for key, label, color in TIMELINE_NODES:
        value = (median or {}).get(key)
        if value is None:
            continue
        nodes_raw.append((key, label, color, float(value)))
    latest = max([item[3] for item in nodes_raw] or [max_seconds])
    span = max(360.0, min(1800.0, max(latest * 1.18, min(max_seconds, 900.0) * 0.6)))
    plot_w = width - pad_left - pad_right

    def x_of(seconds):
        seconds = max(0.0, min(span, float(seconds)))
        return pad_left + plot_w * (seconds / span)

    parts = ['<svg class="bo-tl" viewBox="0 0 %.0f %.0f" role="img" '
             'xmlns="http://www.w3.org/2000/svg">' % (width, height)]
    if title:
        parts.append('<title>%s</title>' % h(title))

    # 阶段底色
    for name, start, end in PHASES:
        left = x_of(start)
        right = x_of(span if end is None else min(end, span))
        if right <= left:
            continue
        parts.append('<rect x="%.1f" y="10" width="%.1f" height="%.1f" fill="%s"/>'
                     % (left, right - left, height - 34.0, PHASE_COLORS.get(name, "#141a23")))
        parts.append('<text class="phase-label" x="%.1f" y="22">%s %s</text>'
                     % (left + 6.0, PHASE_ZH.get(name, name), clock(start)))

    # 分钟刻度
    step = 60.0 if span <= 900.0 else 120.0
    seconds = 0.0
    while seconds <= span + 0.1:
        x = x_of(seconds)
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#253041" stroke-width="1"/>'
                     % (x, rail_y - 8.0, x, rail_y + 8.0))
        parts.append('<text class="tick" x="%.1f" y="%.1f" text-anchor="middle">%s</text>'
                     % (x, rail_y + 24.0, clock(seconds)))
        seconds += step

    # 主轴
    parts.append('<line class="rail" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>'
                 % (pad_left, rail_y, width - pad_right, rail_y))

    # 节点
    nodes = sorted([(x_of(value), key, label, color, value)
                    for key, label, color, value in nodes_raw], key=lambda item: item[0])
    used_left = True
    for x, key, label, color, value in nodes:
        up = used_left
        used_left = not used_left
        y_text = rail_y - 26.0 if up else rail_y + 34.0
        stem = rail_y - 10.0 if up else rail_y + 10.0
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1" opacity="0.55"/>'
                     % (x, rail_y, x, stem, color))
        parts.append('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" stroke="#0e1116" stroke-width="1.5"/>'
                     % (x, rail_y, color))
        parts.append('<text class="node-label" x="%.1f" y="%.1f" text-anchor="middle">%s</text>'
                     % (x, y_text, h(label)))
        parts.append('<text class="node-time" x="%.1f" y="%.1f" text-anchor="middle">%s</text>'
                     % (x, y_text + 13.0, h(clock(value))))
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- 区块

def _sequence_chips(cluster):
    """典型开局序列：token 中文名 + 中位出现时间，横向串联。"""
    rows = cluster.get("sequence_medians") or []
    if not rows:
        return ""
    parts = []
    for index, row in enumerate(rows):
        parts.append('<span class="chip"><b>%s</b> %s</span>' % (
            h(row.get("label") or row.get("token")),
            h(clock(row.get("t")))))
    return "".join(parts)


def _cluster_card(cluster, max_seconds, tone=0, show_members=True):
    colors = ("#7c9cff", "#46d17e", "#f2c45a", "#b48cff", "#ff8b6b", "#62b0ff")
    color = colors[tone % len(colors)]
    parts = ['<div class="card" style="margin-bottom:14px">',
             '<div class="cluster-head">',
             '<span class="name">%s</span>' % h(cluster["name"]),
             '<span class="meta">%d 局 · 占比 %s · 节奏 %s</span>' % (
                 cluster["games"], _pct(cluster["share"]),
                 "偏快" if cluster.get("pace") == "fast" else "偏慢"),
             "</div>",
             '<div class="share-bar"><span style="width:%.1f%%;background:%s"></span></div>'
             % (max(1.0, cluster["share"] * 100.0), color)]

    parts.append('<div class="mini">典型 0–3 分钟序列：%s</div>' % h(
        " → ".join("%s" % (row.get("label") or row.get("token"))
                   for row in (cluster.get("sequence_medians") or []))))
    parts.append(_sequence_chips(cluster))

    median = cluster.get("median") or {}
    mean = cluster.get("mean") or {}
    chips = []
    for key, label, _color in TIMELINE_NODES:
        value = median.get(key)
        chips.append(_chip(label, clock(value)))
    parts.append('<div class="mini">整局关键点（中位）：</div>')
    parts.append("".join(chips))
    parts.append('<div class="mini">平均值：%s</div>' % h(" · ".join(
        "%s %s" % (label, clock(mean.get(key))) for key, label, _c in TIMELINE_NODES)))

    parts.append('<div class="tl-wrap">%s</div>' % timeline_svg(
        median, max_seconds=max_seconds,
        title="%s 关键节点中位时间" % cluster["name"]))

    styles = cluster.get("styles") or {}
    armies = cluster.get("first_army") or {}
    sigs = cluster.get("signatures") or {}
    parts.append('<div class="mini">兵种构成（5~10 分钟）：%s</div>' % h(
        " · ".join("%s %d" % (k, v) for k, v in styles.items())))
    parts.append('<div class="mini">第一个战斗单位：%s</div>' % h(
        " · ".join("%s %d" % (k, v) for k, v in armies.items())))
    parts.append('<div class="mini">开局签名：%s</div>' % h(
        " · ".join("%s %d" % (k, v) for k, v in sigs.items())))
    if show_members and cluster.get("members"):
        examples = cluster["members"][:3]
        parts.append('<div class="mini">示例：%s</div>' % h(" / ".join(examples)))
    parts.append("</div>")
    return "".join(parts)


def _k_selection_block(meta):
    """选 k 的依据：k=2..6 的 silhouette / 类内平均距离 / 最小类占比。"""
    clustering = meta.get("clustering") or {}
    scores = clustering.get("scores") or []
    if not scores:
        return ""
    chosen = clustering.get("k")
    rows = []
    for entry in scores:
        mark = ' style="color:#46d17e;font-weight:700"' if entry["k"] == chosen else ""
        rows.append("<tr><td>k=%d</td><td%s>%.3f</td><td>%.3f</td><td>%s</td></tr>" % (
            entry["k"], mark, entry["silhouette"], entry["within_mean"],
            _pct(entry.get("min_share"))))
    outliers = clustering.get("outliers") or []
    return (
        '<div class="card" style="margin-bottom:12px">'
        "<h3>为什么选 k=%s</h3>"
        '<div class="mini">口径：%s。</div>'
        '<table class="data" style="margin-top:8px"><thead><tr>'
        "<th>k</th><th>平均轮廓系数 ↑</th><th>类内平均距离 ↓</th><th>最小类占比</th>"
        "</tr></thead><tbody>%s</tbody></table>"
        '<div class="mini" style="margin-top:6px">离群（最近邻距离大于阈值、单独归入「其他」）'
        "：%d 局。</div></div>" % (
            h(str(chosen)), h(clustering.get("criterion") or "-"),
            "".join(rows), len(outliers)))


def _version_block(meta):
    """版本过滤：8/12 农民两个版本、排除多少局、实际用了哪段时间。"""
    version = meta.get("version_filter") or {}
    if not version:
        return ""
    table = version.get("version_table") or []
    dates = version.get("dates_used") or {}
    rows = "".join(
        "<tr><td>%s</td><td>%s 农民</td><td>%s</td><td>%d 局</td></tr>" % (
            h(entry.get("month") or "?"), h(str(entry.get("start_workers"))),
            h(str(entry.get("build"))), entry.get("games", 0))
        for entry in table)
    pace = meta.get("pace_dimension") or {}
    pace_text = "仍然存在" if pace.get("survives") else "基本消失（已被版本过滤吸收）"
    pace_detail = "第一气矿 p10~p90 = %s~%s（跨度 %s 秒），二矿 %s~%s（跨度 %s 秒）" % (
        clock(pace.get("gas_p10")), clock(pace.get("gas_p90")), h(_num(pace.get("gas_spread"))),
        clock(pace.get("expand_p10")), clock(pace.get("expand_p90")),
        h(_num(pace.get("expand_spread"))))
    return (
        '<div class="card" style="margin-bottom:12px">'
        "<h3>版本过滤（开局农民数 = %s）</h3>"
        '<div class="mini">排除 8 农民版本 %d 局、按日期排除 %d 局；'
        "实际使用日期 %s ~ %s。判据用开局农民数（版本无关），日期只是可选项。</div>"
        '<table class="data" style="margin-top:8px"><thead><tr>'
        "<th>月份</th><th>开局农民</th><th>replay build</th><th>扫到局数</th>"
        "</tr></thead><tbody>%s</tbody></table>"
        '<div class="mini" style="margin-top:8px">8 农民版本会让同一个 build 的开局时间点'
        "整体前移 20~30 秒，混在一起聚出来的是版本差而不是打法差——所以整批排除。"
        "过滤后「慢/快节奏」这个维度<b>%s</b>：%s。</div></div>" % (
            h(str(version.get("start_workers"))),
            version.get("excluded_by_workers", 0), version.get("excluded_by_date", 0),
            h(dates.get("min") or "-"), h(dates.get("max") or "-"),
            rows, h(pace_text), h(pace_detail)))


def _num(value, suffix=""):
    if value is None:
        return "-"
    if isinstance(value, float):
        return ("%.1f%s" % (value, suffix)) if value != int(value) else ("%d%s" % (int(value), suffix))
    return "%s%s" % (value, suffix)


def _comparison_rows(comparison):
    rows = []
    for row in comparison["rows"]:
        delta = row.get("delta")
        if row["key"] == "rax_before_expand":
            mine = "-" if row["mine"] is None else "%.1f 个" % row["mine"]
            pro = "-" if row["pro"] is None else "%.1f 个" % row["pro"]
            delta_text = "-" if delta is None else "%+.1f" % delta
        else:
            mine = clock(row["mine"])
            pro = clock(row["pro"])
            delta_text = "-" if delta is None else "%+.1f 秒" % delta
        tone = ""
        if delta is not None:
            if row["key"] == "rax_before_expand":
                tone = ""
            elif abs(delta) <= 5.0:
                tone = "good"
            elif delta > 20.0:
                tone = "warn"
            elif delta > 5.0:
                tone = "info"
        rows.append(
            '<tr><td class="l">%s</td><td>%s</td><td>%s</td><td class="%s">%s</td></tr>'
            % (h(row["label"]), h(mine), h(pro), tone, h(delta_text)))
    return "".join(rows)


def _distribution_bars(counts, total, colors=("#7c9cff", "#46d17e", "#f2c45a",
                                             "#b48cff", "#ff8b6b", "#62b0ff")):
    parts = []
    for index, (name, count) in enumerate(counts.items()):
        color = colors[index % len(colors)]
        parts.append(charts.bar_row(name, count, max(1, total), color, suffix=" 局"))
    return "".join(parts)


def _games_table(games):
    rows = []
    for game in games:
        timings = game["key_timings"]
        cells = [
            '<td class="l bo-file">%s</td>' % h(game["game"]["file"]),
            '<td class="l">%s</td>' % h(game["game"]["date"] or "-"),
            '<td class="l">%s</td>' % h(game["game"]["matchup"] or "-"),
            '<td class="l">%s</td>' % h(game["game"]["map"] or "-"),
            "<td>%s</td>" % h(game["signature"]),
            "<td>%s</td>" % h(game["style"]),
            "<td>%s</td>" % clock(timings.get("first_expand")),
            "<td>%s</td>" % clock(timings.get("first_gas")),
            "<td>%s</td>" % clock(timings.get("first_factory")),
            "<td>%s</td>" % clock(timings.get("first_starport")),
            "<td>%s</td>" % clock(timings.get("army_moveout")),
        ]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return "".join(rows)


def _ecdf_chart(mine, pro, key="first_expand", label="二矿时间"):
    """按时间排序的散点/折线：每局一个点，横轴是"第几局"（排序后），纵轴秒数。"""
    def series(games, name, color, dash=False):
        values = sorted(g["key_timings"].get(key) for g in games
                        if g["key_timings"].get(key) is not None)
        if not values:
            return None
        points = [[index + 1, value] for index, value in enumerate(values)]
        return {"label": name, "color": color, "points": points, "dash": dash}

    rows = [
        series(mine, "我", "#7c9cff"),
        series(pro, "Maru", "#46d17e", dash=True),
    ]
    rows = [row for row in rows if row]
    if not rows:
        return ""
    return charts.line_chart(rows, width=560, height=210, x_label="局（按时间排序）",
                             y_suffix="s", show_legend=True)


def _expand_filter_block(meta, summary):
    """扩张时间合理性过滤的说明 + 被丢弃的候选明细。"""
    expand_filter = meta.get("expand_filter") or {}
    if not expand_filter:
        return ""
    rejects = ((summary.get("sampling") or {}).get("expand_rejects") or {})
    groups = [(group, rejects.get(group) or []) for group in ("mine", "pro")]
    total = sum(len(items) for _g, items in groups)
    expected = ((expand_filter.get("expected_build_seconds") or {}).get("CommandCenter"))

    def _fmt(value, suffix=""):
        if value is None:
            return "-"
        if isinstance(value, float) and value != int(value):
            return "%.1f%s" % (value, suffix)
        return "%s%s" % (value, suffix)

    tolerance = expand_filter.get("span_tolerance")
    parts = ['<div class="mini" style="margin-top:12px">扩张时间过滤：时间必须 ≥ %s'
             "（二矿不可能早于 2:00 左右出现，且 ESL/GameHeart 老 replay 的 tracker "
             "时间本身不可信）；done-start 与本建筑建造时长 %s 相差超过 %s 判为坏记录"
             "（时间单位是游戏内秒：loop/16 与 Blizzard gamemetadata 的 Duration 一致）。"
             "坏记录顺延到下一个候选，全坏给 null。本次丢弃 <b>%s</b> 条候选。</div>" % (
                 h(_fmt(expand_filter.get("min_seconds"), " 秒")),
                 h(_fmt(expected, " 秒")),
                 h("%.0f%%" % (float(tolerance) * 100.0) if tolerance is not None else "-"),
                 h(str(total)))]
    for group, items in groups:
        if not items:
            continue
        label = "我" if group == "mine" else "职业"
        rows = []
        for entry in items:
            detail = "、".join(
                "%s@%s（%s）" % (sub.get("name", "?"), clock(sub.get("t")),
                                "时间过早" if sub.get("reason") == "too_early"
                                else "建造时长异常")
                for sub in entry.get("items", []))
            rows.append("%s：%s" % (entry.get("file", "?"), detail))
        parts.append('<div class="mini">%s（%d 局）：%s</div>' % (
            h(label), len(items), h("；".join(rows))))
    return "".join(parts)


def render(summary):
    meta = summary["meta"]
    comparison = summary["comparison"]
    mine_clusters = summary["clusters"]["mine"]
    pro_clusters = summary["clusters"]["pro"]
    games = summary["games"]
    mine_games = [g for g in games if g["player"]["is_local"]]
    pro_games = [g for g in games
                 if not g["player"]["is_local"] and g["game"]["matchup"] == meta["matchup"]]
    max_seconds = _durations(summary)
    elapsed = meta.get("elapsed_seconds")

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>BO 分析 · %s %s</title>" % (h(meta["mine"]), h(meta["pro"])),
             "<style>", CSS, EXTRA_CSS, "</style></head><body><div class=\"wrap\">"]

    # ---------------- header
    parts.append('<header class="top"><h1 class="bo-title">BO 分析：%s vs %s</h1>'
                 % (h(meta["mine"]), h(meta["pro"])))
    parts.append('<div class="sub">对局类型 %s · 我 %d 局 + 职业 %d 局（其中 %s 对局 %d 局）'
                 ' · 生成于 %s%s</div>' % (
                     h(meta["matchup"]), meta["mine_games"], meta["pro_games"],
                     h(meta["matchup"]), meta["pro_matchup_games"], h(meta["generated"]),
                     "" if not elapsed else " · 解析耗时 %.0f 秒" % elapsed))
    parts.append('<div class="sub">阶段按时间切：%s</div>' % h(
        " / ".join("%s %s–%s" % (PHASE_ZH[p], clock(s), clock(e) if e else "∞")
                   for p, s, e in PHASES)))
    sampling = (summary.get("sampling") or {}).get("mine") or {}
    if sampling.get("scanned"):
        parts.append('<div class="sub">语料：扫描 %d 个 replay 文件 → 入选 %d 局'
                     '（其余被 matchup / 人类对手 / 版本 / 时长等条件筛掉）。</div>' % (
                         sampling.get("scanned", 0), sampling.get("matched", 0)))
    parts.append("</header>")

    # ---------------- 结论
    parts.append("<h2>关键结论</h2>")
    unknown = meta.get("unknown") or {}
    sig_counts = comparison["mine"]["signatures"] or {}
    top_sig = next(iter(sig_counts.items()), ("-", 0))
    insight = [
        ("info", "开局签名集中在 %s" % h(top_sig[0]),
         "我 %d 局里 %d 局（%s）是 %s；签名是整局打法的短标签，开局细节看下面的序列聚类。" % (
             meta["mine_games"], top_sig[1],
             _pct(top_sig[1] / float(meta["mine_games"] or 1)), h(top_sig[0]))),
        ("good", "按 0–3 分钟序列聚出 %d 类（k=%s）" % (
            len(mine_clusters), (meta.get("clustering") or {}).get("k")),
         " / ".join("%s（%d 局，%s）" % (h(c["name"]), c["games"], _pct(c["share"]))
                    for c in mine_clusters) or "-"),
        ("info", "节奏对照",
         "二矿中位：我 %s / %s %s；气矿中位：我 %s / %s %s；版本过滤后「慢/快」维度%s。" % (
             clock(comparison["mine"]["median"].get("first_expand")),
             h(meta["pro"]), clock(comparison["pro"]["median"].get("first_expand")),
             clock(comparison["mine"]["median"].get("first_gas")),
             h(meta["pro"]), clock(comparison["pro"]["median"].get("first_gas")),
             "仍存在" if (meta.get("pace_dimension") or {}).get("survives")
             else "基本消失")),
    ]
    for tone, title, desc in insight:
        parts.append('<div class="insight %s"><div class="t">%s</div><div class="d">%s</div></div>'
                     % (tone, h(title), desc))

    for note in summary.get("caveats") or []:
        parts.append('<div class="bo-note"><b>注意</b>：%s</div>' % h(note))
    if unknown.get("mine_signature_share") is not None:
        parts.append('<div class="mini">unknown 占比：我的 signature %s / style %s；'
                     '职业 signature %s / style %s。</div>' % (
                         _pct(unknown.get("mine_signature_share")),
                         _pct(unknown.get("mine_style_share")),
                         _pct(unknown.get("pro_signature_share")),
                         _pct(unknown.get("pro_style_share"))))

    # ---------------- 单局验收（--replay 指定的局）
    checks = summary.get("replay_checks") or []
    if checks:
        parts.append("<h2>单局验收样例</h2>")
        for check in checks:
            timings = check.get("key_timings") or {}
            parts.append('<div class="card" style="margin-bottom:12px">')
            parts.append('<div class="cluster-head"><span class="name">%s</span>'
                         '<span class="meta">%s · signature <b>%s</b> · style %s</span></div>'
                         % (h(check.get("file", "")), h(check.get("matchup", "-")),
                            h(check.get("signature", "-")), h(check.get("style", "-"))))
            chips = "".join(_chip(label, clock(timings.get(key)))
                            for key, label, _c in TIMELINE_NODES)
            parts.append(chips)
            parts.append('<div class="tl-wrap">%s</div>' % timeline_svg(
                timings, max_seconds=max([v for v in timings.values() if v] or [900.0]) * 1.5,
                title="%s 关键节点" % check.get("file", "")))
            if check.get("opening_actions"):
                parts.append('<div class="mini">开局前 8 个动作：%s</div>'
                             % h(" / ".join(check["opening_actions"])))
            if check.get("segments"):
                parts.append('<div class="mini">分段：%s</div>' % h(" | ".join(
                    "%s %s %s–%s" % (s["phase"], s["signature"], clock(s["t0"]), clock(s["t1"]))
                    for s in check["segments"])))
            parts.append("</div>")

    # ---------------- 我的开局（按 0–3 分钟序列聚类）
    parts.append("<h2>我的 %s 开局：按 0–3 分钟建造序列聚类</h2>" % h(meta["matchup"]))
    parts.append(_version_block(meta))
    parts.append(_k_selection_block(meta))
    if not mine_clusters:
        parts.append('<div class="card mini">样本不足，聚不出开局。</div>')
    for tone, cluster in enumerate(mine_clusters):
        parts.append(_cluster_card(cluster, max_seconds, tone))

    # ---------------- Maru
    parts.append("<h2>%s 的 %s 开局（同样处理）</h2>" % (h(meta["pro"]), h(meta["matchup"])))
    if not pro_clusters:
        parts.append('<div class="card mini">职业样本里 %s 对局太少，没有聚类。'
                     % h(meta["matchup"]))
        parts.append('<div class="card mini">全部 %d 局的签名分布：%s</div>' % (
            meta["pro_games"], h(" · ".join("%s %d" % (k, v) for k, v in
                                            (comparison["pro"]["signatures"] or {}).items()))))
    else:
        for tone, cluster in enumerate(pro_clusters):
            parts.append(_cluster_card(cluster, max_seconds, tone + 1))

    # ---------------- 对照
    parts.append("<h2>我 vs %s：关键时间点（中位数）</h2>" % h(meta["pro"]))
    parts.append('<div class="card"><table class="data"><thead><tr>'
                 '<th class="l">节点</th><th>我</th><th>%s</th><th>差值（我 − 职业）</th>'
                 "</tr></thead><tbody>%s</tbody></table>"
                 % (h(meta["pro"]), _comparison_rows(comparison)))
    parts.append('<div class="mini">差值：+ 表示我比职业选手晚。差值 ≤ 5 秒算接近，'
                 "&gt; 20 秒标黄。</div></div>")

    parts.append('<h2>二矿时间的逐局分布</h2>')
    parts.append('<div class="card"><div class="grid2">')
    parts.append('<div class="chart-card"><h3>二矿（分钟）</h3>'
                 '<div class="desc">每局一个点，按时间排序；虚线是 %s</div>%s</div>'
                 % (h(meta["pro"]), _ecdf_chart(mine_games, pro_games, "first_expand")))
    parts.append('<div class="chart-card"><h3>第一气矿（分钟）</h3>'
                 '<div class="desc">同上；节奏差异主要看这张</div>%s</div>'
                 % _ecdf_chart(mine_games, pro_games, "first_gas"))
    parts.append("</div></div>")

    # ---------------- 分布
    parts.append("<h2>签名与兵种构成</h2>")
    parts.append('<div class="card"><div class="grid2">')
    parts.append('<div><h3 class="mini">我的开局签名</h3>%s</div>'
                 % _distribution_bars(comparison["mine"]["signatures"],
                                      max(1, meta["mine_games"])))
    parts.append('<div><h3 class="mini">我的兵种构成（style）</h3>%s</div>'
                 % _distribution_bars(comparison["mine"]["styles"],
                                      max(1, meta["mine_games"])))
    parts.append('<div><h3 class="mini">%s 的开局签名</h3>%s</div>'
                 % (h(meta["pro"]), _distribution_bars(comparison["pro"]["signatures"],
                                                       max(1, meta["pro_games"]))))
    parts.append('<div><h3 class="mini">%s 的兵种构成</h3>%s</div>'
                 % (h(meta["pro"]), _distribution_bars(comparison["pro"]["styles"],
                                                       max(1, meta["pro_games"]))))
    parts.append("</div></div>")

    # ---------------- 逐局明细
    parts.append("<h2>逐局明细（%d 局）</h2>" % len(games))
    parts.append('<div class="card"><table class="data"><thead><tr>'
                 '<th class="l">文件</th><th class="l">日期</th><th class="l">对局</th>'
                 '<th class="l">地图</th><th>签名</th><th>风格</th>'
                 "<th>二矿</th><th>气矿</th><th>重工</th><th>星港</th><th>出门</th>"
                 "</tr></thead><tbody>%s</tbody></table></div>" % _games_table(games))

    # ---------------- 口径
    parts.append("<h2>判读口径（可复现）</h2>")
    legend = meta.get("signature_legend") or {}
    style_legend = meta.get("style_legend") or {}
    parts.append('<div class="card"><div class="grid2"><div>')
    parts.append("<h3 class=\"mini\">signature 封闭词表</h3>")
    for key, desc in legend.items():
        parts.append('<div class="mini"><span class="chip">%s</span> %s</div>'
                     % (h(key), h(desc)))
    parts.append("</div><div>")
    parts.append("<h3 class=\"mini\">style 词表（%s）</h3>" % h(
        "%d–%d 秒" % (STYLE_WINDOW[0], STYLE_WINDOW[1])))
    for key, desc in style_legend.items():
        parts.append('<div class="mini"><span class="chip">%s</span> %s</div>'
                     % (h(key), h(desc)))
    parts.append("</div></div>")
    parts.append('<div class="hint" style="margin-top:10px">'
                 "判定顺序（先命中先算，同一局永远得同一标签）：扩张前兵营数 → 3-rax / "
                 "2-rax-fast-expand / 1-rax-fast-expand → mech → 3-rax(5 分钟内) → "
                 "2-1-1 → 1-1-1 → unknown。<br>"
                 "聚类轴 = <b>0–180 秒建造序列</b>：序列 → 归一化 Levenshtein 距离 → "
                 "平均连接层次聚类；k 在 2~6 里按平均轮廓系数选（见上面那张表）。<br>"
                 "版本过滤：只收开局农民数 = %s 的局（8 农民版本的开局时间点整体前移，"
                 "混进来聚出的就是版本差）。<br>"
                 "每局的 steps 来自 <b>parsed.build_events</b>（loop/16 = 秒），"
                 "cost 由 <b>units.cost()</b> 补齐，morph 事件不重复计费。</div>" % h(
                     str((meta.get("version_filter") or {}).get("start_workers"))))
    parts.append(_expand_filter_block(meta, summary))
    parts.append("</div>")

    if summary.get("errors"):
        parts.append("<h2>解析失败</h2><div class=\"card mini\">")
        for item in summary["errors"][:20]:
            parts.append("%s：%s<br>" % (h(item.get("file", "?")), h(item.get("error", ""))))
        parts.append("</div>")

    version = meta.get("version_filter") or {}
    parts.append('<footer>复现：<code>python -m replay_analyzer.bo --limit %d '
                 '--start-workers %s --replay "&lt;某个 .SC2Replay 路径&gt;"</code>'
                 ' · 可选 <code>--since/--until YYYY-MM-DD</code> 限定日期区间'
                 ' · 报告离线生成，无外部依赖</footer>' % (
                     meta.get("mine_games", 0), h(str(version.get("start_workers")))))
    parts.append("</div></body></html>")
    return "".join(parts)


def write_report(summary, output_path="./reports/bo.html"):
    directory = os.path.dirname(os.path.abspath(output_path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(render(summary))
    return output_path


if __name__ == "__main__":
    import json
    import sys

    with open(sys.argv[1] if len(sys.argv) > 1 else "reports/bo.json", encoding="utf-8") as fh:
        write_report(json.load(fh), sys.argv[2] if len(sys.argv) > 2 else "reports/bo.html")
    print("ok")
