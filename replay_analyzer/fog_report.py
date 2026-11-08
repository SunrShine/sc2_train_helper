"""把 fog.analyze() 的结果渲染成一份离线 HTML（含机制解读与局限）。"""

import html
import os

from . import charts
from .micro_report import CSS

h = html.escape


def _mmss(seconds):
    seconds = max(0.0, float(seconds or 0))
    return "%d:%02d" % (int(seconds) // 60, int(seconds) % 60)


def _pct(value):
    return "%.0f%%" % (float(value) * 100.0)


def _span(first, second, formatter=_pct, min_gap=0.03):
    """两个口径给出的值 -> "A–B"；差得太小就只显示一个。"""
    if second is None:
        return formatter(first)
    low, high = sorted((float(first), float(second)))
    if high - low < min_gap:
        return formatter(first)
    return "%s–%s" % (formatter(low), formatter(high))


def _quality(result):
    dq = result.get("data_quality") or {}
    if not dq:
        return ""
    return (
        "位置数据：本局 %s 个单位里只有 %s 个有位置采样，一共 %s 个点，"
        "相邻采样的中位间隔 %.0f 秒（90 分位 %.0f 秒）。单位轨迹是拿这些点插值重建的，"
        "所以下面所有百分比都请当“量级”看——卡片上的两个数字区间，就是插入方式不同带来的误差尺度。"
        % ("%.0f" % dq["units_total"], "%.0f" % dq["units_tracked"], "%.0f" % dq["samples"],
           dq["gap_median"], dq["gap_p90"])
    )


def _metric(label, value, note=""):
    return ('<div class="metric"><div class="k">%s</div><div class="v">%s</div>'
            '<div class="n">%s</div></div>' % (h(label), h(value), h(note)))


def _insights(result):
    me = result["me"]
    opp = result["opponent"]
    army = result["army"]
    theirs = result["theirs_army"]
    vision = result["vision"]
    out = []

    gap = army["ratio"] - theirs["ratio"]
    if abs(gap) < 0.04:
        level, title = "info", "双方差不多：你们互相都看得挺清楚"
        text = ("按主口径，你的部队有 %s 的时间在 %s 的视野里，%s 的部队有 %s 的时间在你的视野里，"
                "差距不到 4 个百分点。" % (_pct(army["ratio"]), opp["name"],
                                            opp["name"], _pct(theirs["ratio"])))
    elif gap > 0:
        level, title = "warn", "你更透明：被看得比看得多"
        text = ("按主口径，你的部队有 %s 的时间在 %s 的视野里，而他的部队只有 %s 的时间在你的视野里。"
                "差出来的 %s 个单位·秒，就是他能提前布局、你只能被动反应的部分。"
                % (_pct(army["ratio"]), opp["name"], _pct(theirs["ratio"]),
                   _pct(abs(gap))))
    else:
        level, title = "good", "你更占视野便宜：看得比被看多"
        text = ("按主口径，他的部队有 %s 的时间在你的视野里，你的部队只有 %s 的时间在他的视野里。"
                "你的侦察/前压是有效的，注意别为了保持这个数字把兵送进不明区域。"
                % (_pct(theirs["ratio"]), _pct(army["ratio"])))
    out.append({"level": level, "title": title, "text": text})

    if result.get("first_contact") is not None:
        out.append({
            "level": "info",
            "title": "第一次被看见：%s" % _mmss(result["first_contact"]),
            "text": ("从这一刻开始，你的部队第一次进入了对方单位的视野圆。"
                     "在此之前你的动向对他来说是纯黑区——这类时间窗口就是运营里说的“暗牌”。"),
        })

    blind = result.get("blind_windows") or []
    if blind:
        total = sum(w["dur"] for w in blind)
        out.append({
            "level": "good",
            "title": "%d 段“完全不被看见”，共 %s" % (len(blind), _mmss(total)),
            "text": ("这些时间段里你至少 3 个部队单位存活、且没有一个落在他的视野圆里。"
                     "最长的两段：" +
                     "、".join("%s-%s" % (_mmss(w["t0"]), _mmss(w["t1"])) for w in
                              sorted(blind, key=lambda w: -w["dur"])[:2]) +
                     "。想练“绕路/偷家”，先把这类窗口用起来。"),
        })
    else:
        out.append({
            "level": "warn",
            "title": "没有成段的盲区",
            "text": ("整局你只要有几个兵活着，就总有至少一个落在他视野里。"
                     "要么是他的侦察覆盖太好，要么是你的部队一直贴在他的视野边缘。"),
        })

    regions = result.get("regions") or []
    if regions:
        top = regions[0]
        share = top[1] / max(1.0, sum(v for _k, v in regions))
        out.append({
            "level": "info",
            "title": "最常被看见的位置：%s" % top[0],
            "text": ("这里有 %s 的单位·秒被对方看到（占全部暴露时间的 %s）。"
                     "如果这是你的主基地/二矿，说明他的侦察或前压一直贴着你的经济区。"
                     % ("%.0f" % top[1], _pct(share))),
        })

    cov_gap = vision["theirs_coverage"] - vision["mine_coverage"]
    if abs(cov_gap) > 0.02:
        out.append({
            "level": "info",
            "title": "视野面积：他覆盖 %s，你覆盖 %s" % (_pct(vision["theirs_coverage"]),
                                                        _pct(vision["mine_coverage"])),
            "text": ("按整张地图面积算，他平均有 %s 的区域同时在他的视野里，你是 %s。"
                     "这个数字高不代表“看得准”，但代表他更少走进纯黑区。"
                     % (_pct(vision["theirs_coverage"]), _pct(vision["mine_coverage"]))),
        })
    return out


def _timeline_chart(result):
    timeline = result["timeline"]
    mine = [[row["t"] / 60.0, row["mine_seen"]] for row in timeline]
    theirs = [[row["t"] / 60.0, row["theirs_seen"]] for row in timeline]
    series = [
        {"label": "我的部队被看见（个）", "color": "#ff6b6b", "points": mine},
        {"label": "他被我看见（个）", "color": "#62b0ff", "points": theirs},
    ]
    return charts.line_chart(series, x_label="分钟", show_legend=True, area=False)


def _coverage_chart(result):
    samples = result.get("coverage") or []
    if not samples:
        return '<div class="empty-chart">无数据</div>'
    mine = [[row["t"] / 60.0, row["mine"] * 100.0] for row in samples]
    theirs = [[row["t"] / 60.0, row["theirs"] * 100.0] for row in samples]
    series = [
        {"label": "我的视野覆盖（%）", "color": "#46d17e", "points": mine},
        {"label": "他的视野覆盖（%）", "color": "#f2c45a", "points": theirs},
    ]
    return charts.line_chart(series, x_label="分钟", y_suffix="%", show_legend=True)


def _phase_table(result):
    rows = []
    for phase in result["phases"]:
        rows.append(
            "<tr><td class='l'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (h(phase["label"]),
               "%.0f" % phase["mine_alive"], _pct(phase["mine_ratio"]),
               "%.0f" % phase["theirs_alive"], _pct(phase["theirs_ratio"])))
    return ("<table class='data'><thead><tr><th>阶段</th><th>我的部队·秒</th><th>我被看见</th>"
            "<th>他的部队·秒</th><th>他被看见</th></tr></thead><tbody>%s</tbody></table>"
            % "".join(rows))


def _region_rows(result):
    regions = result.get("regions") or []
    if not regions:
        return '<div class="hint">没有暴露记录。</div>'
    top = max(v for _k, v in regions)
    return "".join(charts.bar_row(name, value, top, "#ff6b6b", " 单位·秒")
                   for name, value in regions[:10])


def _window_table(windows, label):
    if not windows:
        return '<div class="hint">没有。</div>'
    rows = []
    for window in sorted(windows, key=lambda w: -w["dur"])[:8]:
        rows.append("<tr><td class='l'>%s - %s</td><td>%.1fs</td><td>%d</td></tr>"
                    % (_mmss(window["t0"]), _mmss(window["t1"]),
                       window["dur"], window.get("n", 0)))
    return ("<table class='data'><thead><tr><th>%s</th><th>时长</th><th>采样点</th></tr></thead>"
            "<tbody>%s</tbody></table>" % (h(label), "".join(rows)))


def _unit_table(result):
    units = result.get("units") or []
    if not units:
        return '<div class="hint">没有足够的部队单位样本。</div>'
    rows = []
    for unit in units:
        rows.append("<tr><td class='l'>%s</td><td>%.0f</td><td>%s</td><td>%s</td></tr>"
                    % (h(unit["name"]), unit["alive"], _pct(unit["ratio"]),
                       _mmss(unit["first"]) if unit["first"] is not None else "-"))
    return ("<table class='data'><thead><tr><th>单位</th><th>存活单位·秒</th>"
            "<th>被看见比例</th><th>第一次被发现</th></tr></thead><tbody>%s</tbody></table>"
            % "".join(rows))


MECHANICS = """
<div class="hint">
<p><b>1. 视野是“圆 + 地形”的裁剪。</b>每个单位有一个视野半径（陆战队员 9 格、追猎者 10 格、
基地 11 格这一档），范围内才可见。地形会把圆裁掉一块：站在低地看不到高地上的单位，
悬崖、岩石、可破坏物都会挡视线；飞行单位不受地形阻挡。</p>
<p><b>2. 三种状态，而不是两种。</b>从没去过的地方是<b>全黑</b>；去过、但现在没有视野的地方是
<b>阴影</b>，那里会留着你上次看到的建筑/矿的轮廓（建筑一旦被看到就会一直显示轮廓，兵不会）；
真正被照亮的是<b>当前可见</b>。这套“黑 / 阴影 / 可见”就是本页动画的三层配色。</p>
<p><b>3. 隐形与探测是另一套判定。</b>隐形单位（黑暗圣堂、女妖、幽灵的隐形、埋地蟑螂的移动）
在没有探测器时即使落在视野圆里也不显示；探测器（导弹塔、光子炮台、孢子爬虫、观察者、
督察者，以及带侦测的建筑）把它们点亮。本工具只算视野几何，不判隐形。</p>
<p><b>4. 视野来源不止兵。</b>建筑本身有视野；侦察单位（观察者、督察者）看得更远；
雷达扫描（轨道控制基地）给一次短时间的临时视野；Xel'Naga 塔是占领后才给范围视野。</p>
<p><b>5. 迷雾影响的是“看见”，不影响“记得”。</b>对方部队离开视野后你仍然知道那片地形、
知道刚才的兵位，但看不到他现在的兵——所以“绕开视野”在实战里等于让对手拿到过期信息。</p>
</div>
"""

LIMITS = """
<div class="hint">
<p><b>1. 位置是稀疏快照，这是最大的误差源。</b>SC2 的 replay 本质是“指令日志 + 定期位置快照”，
不是逐帧轨迹：大多数单位整局只有两三个位置点。中间的位置是本工具线性插值出来的，
所以“某一秒他在哪”本身就有几十秒量级的模糊——百分比只能当量级看。</p>
<p><b>2. 没算地形遮挡。</b>replay 里没有地形高度/悬崖/岩石数据，所以这里当平地处理：
隔着悬崖的“看见”会被高估，实际能见范围只会更小。</p>
<p><b>3. 视野半径是手册值。</b>常见兵种可信，冷门单位和召唤物可能有 ±1 的出入；
用 <code>--sight-scale 1.15</code> 重跑一遍，如果结论不变，说明结论不依赖那几个像素。</p>
<p><b>4. 不判隐形与探测。</b>隐形单位的结果要自己再折一层。</p>
<p><b>5. 这不是开图外挂。</b>整个流程只读 replay 文件、只在本机内存里重算，
不注入游戏、不读进程内存，也不对进行中的对局生效。它给的是“复盘视角的透视”，
不是“对局中偷看对手”。</p>
</div>
"""


def render(result, title=None, gif=None, base_dir=None):
    meta = result["meta"]
    me = result["me"]
    opp = result["opponent"]

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>%s</title>" % h(title or ("%s · 战争迷雾复盘" % meta["map"])),
             "<style>%s</style></head><body><div class=\"wrap\">" % CSS]

    parts.append('<header class="top"><h1>战争迷雾：当时谁看得见谁</h1>')
    parts.append('<div class="sub">%s · %s · %s vs %s · 视野口径 ×%.2f</div>'
                 % (h(meta["map"]), h(_mmss(meta["duration"])),
                    h(me["name"]), h(opp["name"]), meta.get("sight_scale", 1.0)))
    parts.append("</header>")

    # ---- 核心指标
    alt = result.get("alt") or {}
    parts.append('<h2>一、一句话结论</h2><div class="grid4">')
    parts.append(_metric("我的部队被看见",
                         _span(result["army"]["ratio"], alt.get("army_ratio")),
                         "%s 单位·秒样本" % ("%.0f" % result["army"]["alive"])))
    parts.append(_metric("他被我看见",
                         _span(result["theirs_army"]["ratio"], alt.get("theirs_ratio")),
                         "%s 单位·秒样本" % ("%.0f" % result["theirs_army"]["alive"])))
    parts.append(_metric("我的视野覆盖",
                         _span(result["vision"]["mine_coverage"], alt.get("mine_coverage")),
                         "单位活动范围占比"))
    parts.append(_metric("他的视野覆盖",
                         _span(result["vision"]["theirs_coverage"], alt.get("theirs_coverage")),
                         "单位活动范围占比"))
    parts.append("</div>")
    quality = _quality(result)
    if quality:
        parts.append('<div class="card" style="margin-top:10px"><div class="hint">%s</div></div>'
                     % h(quality))

    parts.append('<div class="card" style="margin-top:14px">')
    for item in _insights(result):
        parts.append('<div class="insight %s"><div class="t">%s</div><div class="d">%s</div></div>'
                     % (h(item["level"]), h(item["title"]), h(item["text"])))
    parts.append("</div>")

    # ---- 时间线
    parts.append('<h2>二、时间线</h2><div class="grid2">')
    parts.append('<div class="card chart-card"><h3>被看见的部队数量</h3>'
                 '<div class="desc">同一时刻两条线，红色是你的部队落在对方视野里的个数</div>%s</div>'
                 % _timeline_chart(result))
    parts.append('<div class="card chart-card"><h3>视野覆盖率</h3>'
                 '<div class="desc">各自视野圆并集占全图面积的比例（每 5 秒采样）</div>%s</div>'
                 % _coverage_chart(result))
    parts.append("</div>")

    # ---- 分阶段
    parts.append('<h2>三、分阶段</h2><div class="card">%s</div>' % _phase_table(result))

    # ---- 区域
    parts.append('<h2>四、你在哪儿被看见</h2><div class="card">%s</div>'
                 % _region_rows(result))

    # ---- 窗口
    parts.append('<h2>五、窗口</h2><div class="grid2">')
    parts.append('<div class="card"><h3>暴露窗口（≥60%% 部队被看见 ≥8 秒）</h3>%s</div>'
                 % _window_table(result.get("exposed_windows"), "时间"))
    parts.append('<div class="card"><h3>盲区窗口（完全没有部队被看见 ≥10 秒）</h3>%s</div>'
                 % _window_table(result.get("blind_windows"), "时间"))
    parts.append("</div>")

    # ---- 单位
    parts.append('<h2>六、哪些单位最容易被盯上</h2><div class="card">%s</div>'
                 % _unit_table(result))

    # ---- GIF
    if gif and os.path.isfile(gif):
        parts.append('<h2>七、迷雾动画</h2><div class="card">')
        parts.append('<div class="desc" style="color:#93a1b5;font-size:13px;margin-bottom:8px">'
                     '配色：全黑=从没探索过，暗蓝=探索过但当前没视野，亮蓝=当前可见。'
                     '灰色圈是视野边界扫过留下的“阴影”。</div>')
        source = gif
        if base_dir:
            try:
                source = os.path.relpath(os.path.abspath(gif), os.path.abspath(base_dir))
            except Exception:
                source = gif
        parts.append('<img src="%s" alt="fog" style="max-width:100%%;border-radius:10px">'
                     % h(source.replace("\\", "/")))
        parts.append("</div>")

    # ---- 机制
    parts.append('<h2>八、机制笔记</h2><div class="card">%s</div>' % MECHANICS)

    # ---- 局限
    parts.append('<h2>九、这套数的边界</h2><div class="card">%s</div>' % LIMITS)

    missing = result.get("missing_types") or []
    if missing:
        parts.append('<div class="card" style="margin-top:14px"><div class="hint">'
                     '<b>没查到视野半径的单位类型</b>（用了兜底值）：%s</div></div>'
                     % h("、".join("%s×%d" % (name, count) for name, count in missing[:10])))

    parts.append('<footer>由本地 replay 解析器重建，不读游戏内存、不注入进程。'
                 '解析 build %s · 采样间隔 %.1fs</footer>'
                 % (h(str(meta.get("protocol_build"))), meta.get("step", 1.0)))
    parts.append("</div></body></html>")
    return "".join(parts)


def write_fog_report(result, path, gif=None, title=None):
    text = render(result, title=title, gif=gif, base_dir=os.path.dirname(os.path.abspath(path)))
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path
