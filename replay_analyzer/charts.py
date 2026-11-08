"""用纯 Python 生成内联 SVG 折线图。

不依赖任何前端库 / CDN，报告可以完全离线打开。
鼠标悬停时靠 SVG 原生 ``<title>`` 显示数值，不写 JS。
"""

import math


def _fmt(value, suffix=""):
    value = float(value)
    if abs(value) >= 10000:
        text = "%.1fk" % (value / 1000.0)
    elif abs(value) >= 1000:
        text = "%.1fk" % (value / 1000.0)
    elif abs(value) >= 100:
        text = "%.0f" % value
    elif abs(value) >= 10:
        text = "%.0f" % value
    elif abs(value) >= 1:
        text = "%.1f" % value
    else:
        text = "%.2f" % value
    return text + suffix


def _nice_max(value):
    if value <= 0:
        return 10
    exponent = math.floor(math.log10(value))
    base = 10 ** exponent
    for factor in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        candidate = factor * base
        if candidate >= value:
            return candidate
    return 10 * base


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def line_chart(series, width=560, height=230, x_max=None, y_max=None,
               x_label="分钟", y_suffix="", y_min=0, show_legend=False,
               area=False, columns=60):
    """series: [{"label": str, "color": "#hex", "points": [[x, y], ...], "dash": bool}]

    返回一段 <svg> 字符串。
    """
    series = [s for s in series if s.get("points")]
    if not series:
        return '<div class="empty-chart">无数据</div>'

    pad_left = 52
    pad_right = 12
    pad_top = 14
    pad_bottom = 30
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    if x_max is None:
        x_max = max(max(p[0] for p in s["points"]) for s in series)
    x_max = max(x_max, 0.5)
    if y_max is None:
        y_max = max(max(p[1] for p in s["points"]) for s in series)
    y_max = _nice_max(max(y_max, 1))
    y_min = min(y_min, 0)

    def sx(x):
        return pad_left + min(max(x, 0.0), x_max) / x_max * plot_w

    def sy(y):
        ratio = (y - y_min) / (y_max - y_min) if y_max > y_min else 0
        return pad_top + plot_h - min(max(ratio, 0.0), 1.0) * plot_h

    parts = ['<svg viewBox="0 0 %d %d" class="chart" '
             'xmlns="http://www.w3.org/2000/svg">' % (width, height)]

    # ---- 网格与 Y 轴刻度
    ticks = 4
    for i in range(ticks + 1):
        value = y_min + (y_max - y_min) * i / ticks
        y = sy(value)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="grid"/>'
                     % (pad_left, y, pad_left + plot_w, y))
        parts.append('<text x="%d" y="%.1f" class="axis-label" text-anchor="end">%s</text>'
                     % (pad_left - 6, y + 3.5, _fmt(value, y_suffix)))

    # ---- X 轴刻度
    step = 1
    for candidate in (1, 2, 3, 5, 10, 15, 20, 30):
        step = candidate
        if x_max / candidate <= 12:
            break
    tick = 0
    while tick <= x_max + 0.001:
        x = sx(tick)
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" class="grid"/>'
                     % (x, pad_top, x, pad_top + plot_h))
        parts.append('<text x="%.1f" y="%d" class="axis-label" text-anchor="middle">%d</text>'
                     % (x, height - 10, tick))
        tick += step

    # ---- 数据线
    for item in series:
        points = sorted(item["points"], key=lambda p: p[0])
        path = " ".join("%.1f,%.1f" % (sx(x), sy(y)) for x, y in points)
        dash = ' stroke-dasharray="5 4"' if item.get("dash") else ""
        if area and not item.get("dash"):
            base_y = sy(max(y_min, 0))
            area_path = "M %.1f,%.1f L %s L %.1f,%.1f Z" % (
                sx(points[0][0]), base_y, path, sx(points[-1][0]), base_y)
            parts.append('<path d="%s" fill="%s" opacity="0.10" stroke="none"/>'
                         % (area_path, item["color"]))
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.8" '
                     'stroke-linejoin="round" stroke-linecap="round"%s/>'
                     % (path, item["color"], dash))

    # ---- 悬停热区（原生 title 提示）
    cols = max(2, min(columns, int(round(x_max))))
    bucket_w = plot_w / float(cols)
    for index in range(cols):
        x0 = pad_left + index * bucket_w
        x_start = x_max * index / cols
        x_end = x_max * (index + 1) / cols
        lines = ["%.1f-%s" % (x_start, _fmt(x_end)) + " 分钟"]
        for item in series:
            best = None
            for p in item["points"]:
                if x_start <= p[0] < x_end or (index == 0 and p[0] < x_end):
                    if best is None or p[0] > best[0]:
                        best = p
            if best is not None:
                lines.append("%s: %s" % (item["label"], _fmt(best[1], y_suffix)))
        parts.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="transparent">'
                     '<title>%s</title></rect>'
                     % (x0, pad_top, bucket_w, plot_h, _escape("\n".join(lines))))

    # ---- 图例
    if show_legend:
        legend = []
        x = pad_left
        y = pad_top - 4
        for item in series:
            legend.append('<rect x="%d" y="%d" width="8" height="8" rx="2" fill="%s"/>'
                          % (x, y - 8, item["color"]))
            legend.append('<text x="%d" y="%d" class="legend">%s</text>'
                          % (x + 12, y, _escape(item["label"])))
            x += 16 + len(str(item["label"])) * 7.2
        parts.append('<g>%s</g>' % "".join(legend))

    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" class="axis"/>'
                 % (pad_left, pad_top + plot_h, pad_left + plot_w, pad_top + plot_h))
    parts.append("</svg>")
    return "".join(parts)


def bar_row(label, value, max_value, color, suffix=""):
    """HTML 进度条行（用于构成 / 对比表）。"""
    width = 0 if max_value <= 0 else max(1.0, 100.0 * value / max_value)
    return (
        '<div class="bar-row">'
        '<span class="bar-label">%s</span>'
        '<span class="bar-track"><span class="bar-fill" style="width:%.1f%%;background:%s"></span></span>'
        '<span class="bar-value">%s</span>'
        "</div>" % (_escape(label), width, color, _escape(_fmt(value, suffix)))
    )
