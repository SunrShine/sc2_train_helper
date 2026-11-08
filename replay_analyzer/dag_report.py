"""把 :mod:`replay_analyzer.dag` 的转移图和"反应标注"渲染成一份离线 HTML + JSON。

产出
----
- ``reports/dag.html``：纯 SVG 手绘的 DAG（不引前端库，双击就能开），
  左右并排"我的 TvZ"和"Maru 的 TvZ"两套图，加最常见转移表和局限说明。
- ``reports/dag.json``：契约第三节的结构（顶层就是"我的"那份 DAG），
  Maru 的对照放在 ``compare.pro``，另外带 ``reaction``（侦察触发 vs 到点）
  和 ``precision_note``（时间精度）。

用法
----
    python -m replay_analyzer.dag_report                       # 读 reports/bo.json
    python -m replay_analyzer.dag_report --bo reports/bo.json --mine 30 --pro 12
    python -m replay_analyzer.dag_report --bo .tmp/sample_bo.json --out .tmp/dag-sample.html

``--bo`` 不给时默认读 ``reports/bo.json``（bo.py 的产物）。
"""

import argparse
import glob
import hashlib
import html
import json
import math
import os
import re
import sys
import time

from . import dag as dagmod
from . import loader
from .micro_report import CSS

h = html.escape

PHASE_ORDER = dagmod.PHASES
PHASE_LABELS = {
    "opening": "opening · 0–180s",
    "early": "early · 180–360s",
    "mid": "mid · 360–600s",
    "late": "late · 600s+",
}
PHASE_COLORS = {
    "opening": "#6ba7ff",
    "early": "#57c7a0",
    "mid": "#f2b45a",
    "late": "#e07a9a",
}
CONDITION_COLORS = {
    "time": "#5b8dd6",
    "scout": "#e8893a",
    "none": "#8899aa",
}

EVENT_CACHE_VERSION = 4
DEFAULT_CACHE = os.path.join(".tmp", "dag-cache")

# --------------------------------------------------------------------------- 额外样式
EXTRA_CSS = """
.dag-card{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);
border-radius:14px;padding:12px 14px;overflow-x:auto}
.dag-card svg{display:block;width:100%;height:auto}
.dag-legend{display:flex;flex-wrap:wrap;gap:14px;margin:10px 2px 2px;font-size:12.5px;color:var(--muted)}
.dag-legend .lg{display:inline-flex;align-items:center;gap:6px}
.dag-legend .dot{width:11px;height:11px;border-radius:50%;display:inline-block}
.dag-legend .line{width:26px;height:3px;border-radius:2px;display:inline-block}
.split{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:960px){.split{grid-template-columns:1fr}}
.bar3{display:flex;height:16px;border-radius:999px;overflow:hidden;background:#202836}
.bar3 span{display:block;height:100%}
.answer{font-size:15px;line-height:1.85}
.answer b{color:var(--accent)}
.note{color:var(--muted);font-size:12.5px;line-height:1.8}
.warnbox{border-left:3px solid var(--warn);padding:10px 14px;background:#1c1a12;border-radius:0 8px 8px 0}
"""


# --------------------------------------------------------------------------- BO 读取

def load_bo(path):
    """读 bo.json：既吃 dict（{"games": [...]}）也吃裸 list。

    返回 (games, meta)。meta 里顺手带上 bo 顶层的 clusters / comparison，
    报告的"样本口径"一节要引用它们（clusters 现在不固定两类，别假设 k=2）。
    """
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    games = dagmod.normalize_games(data)
    meta = dict(data.get("meta") or {}) if isinstance(data, dict) else {}
    if isinstance(data, dict):
        for key in ("clusters", "comparison", "sampling", "caveats"):
            if data.get(key) is not None and key not in meta:
                meta[key] = data[key]
        if isinstance(data.get("matchups"), dict):
            # task-8 之后的 bo.json：每个对位一本账（clusters / comparison / median）
            meta["matchups_detail"] = data["matchups"]
    return games, meta


def game_file(game):
    return dagmod._game_file(game)


def game_path(game):
    return ((game.get("game") or {}).get("path") or "").strip()


RACE_BO_CACHE = os.path.join(".tmp", "pro_race_bo.json")


def _clean_player_name(name):
    """去掉 replay 名字里的战队标签。

    replay 里的名字本身就带 HTML 转义（``&lt;BASKGG&gt;Serral``），
    所以先 unescape 再剥 ``<...>`` 标签，否则报告里会出现 ``&amp;lt;`` 残留。
    """
    text = html.unescape(name or "")
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or (name or "?")


def _folder_focus(folder, manifest):
    """从 manifest 判断这个目录代表哪位选手、什么种族。"""
    names = []
    for item in manifest.values():
        for player in item.get("players") or []:
            names.append((player.get("name") or "", player.get("race") or ""))
    lowered = folder.lower()
    for name, race in names:
        plain = name.lower().replace(" ", "")
        if plain in lowered or lowered in plain:
            return name, race
    return None, None


def _cache_key(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    raw = "%s|%s|%s|v%d|gap%.0f" % (os.path.abspath(path), stat.st_size,
                                     int(stat.st_mtime), EVENT_CACHE_VERSION,
                                     dagmod.POS_TRUST_GAP)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_read(cache_dir, key):
    if not cache_dir or not key:
        return None
    target = os.path.join(cache_dir, key + ".json")
    if not os.path.isfile(target):
        return None
    try:
        with open(target, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _cache_write(cache_dir, key, events):
    if not cache_dir or not key:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        target = os.path.join(cache_dir, key + ".json")
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(events, handle, ensure_ascii=False)
        os.replace(tmp, target)
    except OSError:
        pass


def compute_info_events(games, cache_dir=DEFAULT_CACHE, force=False, verbose=True):
    """每局跑一遍 info_events，返回 ({file: [event]}, 统计)。"""
    events = {}
    missing = []
    cached_hits = 0
    sampled_from = []
    started = time.time()
    total = len(games)
    for index, game in enumerate(games, 1):
        path = game_path(game)
        name = game_file(game)
        player = (game.get("player") or {}).get("pid")
        key = _cache_key(path) if path else None
        cached = _cache_read(cache_dir, key) if not force else None
        if cached is not None:
            payload = cached.get("events") if isinstance(cached, dict) else cached
            events[name] = payload or []
            if isinstance(cached, dict) and cached.get("sampling_start") is not None:
                sampled_from.append(float(cached["sampling_start"]))
            cached_hits += 1
            continue
        if not path or not os.path.isfile(path):
            missing.append({"file": name, "path": path, "reason": "replay 文件不存在"})
            events[name] = []
            continue
        try:
            parsed = loader.load_replay(path)
            result = dagmod.info_events(parsed, player)
            payload = [event.to_dict() for event in result]
            sampling_start = _first_sample_seconds(parsed)
        except Exception as error:  # 单局失败不该拖垮整批
            missing.append({"file": name, "path": path,
                            "reason": "%s: %s" % (type(error).__name__, error)})
            events[name] = []
            continue
        if sampling_start is not None:
            sampled_from.append(sampling_start)
        _cache_write(cache_dir, key,
                     {"events": payload, "sampling_start": sampling_start})
        events[name] = payload
        if verbose:
            print("  [%d/%d] %-52s %2d events  %.1fs"
                  % (index, total, name[:52], len(payload), time.time() - started),
                  flush=True)
    sampled_from.sort()
    sampling = None
    if sampled_from:
        sampling = {"min": sampled_from[0],
                    "median": sampled_from[len(sampled_from) // 2]}
    return events, {"missing": missing, "cached": cached_hits,
                    "seconds": time.time() - started,
                    "sampling_start": sampled_from[0] if sampled_from else None,
                    "sampling_values": sampled_from,
                    "sampling": sampling}


def _first_sample_seconds(parsed):
    """本局位置采样最早出现在第几秒（实测 270s 左右）。"""
    firsts = [track[0][0] for track in (parsed.unit_positions or {}).values() if track]
    if not firsts:
        return None
    return round(min(firsts) / float(loader.LOOPS_PER_SECOND), 1)


# --------------------------------------------------------------------------- 布局

NODE_MIN_R = 15.0
NODE_MAX_R = 42.0
COLUMN_GAP = 282.0
MARGIN_LEFT = 96.0
MARGIN_RIGHT = 70.0
HEADER_HEIGHT = 62.0
LABEL_BLOCK = 40.0
NODE_GAP = 18.0


def node_radius(node):
    share = max(0.0, min(1.0, float(node.get("share") or 0.0)))
    return NODE_MIN_R + (NODE_MAX_R - NODE_MIN_R) * math.sqrt(share)


def _wrap(text, width=16, max_lines=2):
    """按短横线/空格断行；超长再硬切。"""
    words = []
    for chunk in str(text).split("-"):
        words.append(chunk)
    lines = []
    current = ""
    for word in words:
        piece = word if not current else "-" + word
        if len(current) + len(piece) <= width or not current:
            current += piece
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    lines = [line if len(line) <= width else line[:width - 1] + "…"
             for line in lines[:max_lines]]
    return lines or [""]


def layout_dag(dag, min_node=2, max_nodes_per_phase=6):
    """算出 SVG 几何：phase 从左到右分列，节点在列内按局数从上往下排。

    ``min_node``：少于这么多局的节点不画（画出来就是一堆只出现一次的碎片），
    但会作为 ``hidden`` 统计返回，报告里照实写"有多少没画出来"。
    """
    all_nodes = list(dag.get("nodes") or [])
    all_edges = list(dag.get("edges") or [])
    visible = [node for node in all_nodes if (node.get("games") or 0) >= min_node]
    if not visible and all_nodes:
        visible = sorted(all_nodes, key=lambda node: -(node.get("games") or 0))[:8]
    by_phase = {}
    for node in visible:
        phase = node.get("phase") if node.get("phase") in PHASE_ORDER else "mid"
        by_phase.setdefault(phase, []).append(node)
    columns = [phase for phase in PHASE_ORDER if by_phase.get(phase)]
    for phase in by_phase:
        if phase not in columns:
            columns.append(phase)

    hidden_nodes = [node for node in all_nodes if node not in visible]
    visible_ids = {node["id"] for node in visible}
    hidden_edges = [edge for edge in all_edges
                    if edge["from"] not in visible_ids or edge["to"] not in visible_ids]

    if not columns:
        return {"width": 640.0, "height": 220.0, "nodes": [], "edges": [],
                "columns": [], "empty": True,
                "labels": [],
                "visible": {"nodes": 0, "edges": 0},
                "hidden": {"nodes": len(hidden_nodes), "edges": len(hidden_edges),
                           "transitions": sum(edge.get("count") or 0
                                              for edge in hidden_edges),
                           "transitions_total": sum(edge.get("count") or 0
                                                    for edge in all_edges)}}

    placed = {}
    max_bottom = HEADER_HEIGHT
    for index, phase in enumerate(columns):
        center_x = MARGIN_LEFT + index * COLUMN_GAP
        cursor = HEADER_HEIGHT + 16.0
        rows = sorted(by_phase[phase], key=lambda node: (-node["games"], node["id"]))
        rows = rows[:max_nodes_per_phase]
        for node in rows:
            radius = node_radius(node)
            center_y = cursor + radius
            placed[node["id"]] = {
                "id": node["id"], "phase": phase, "signature": node["signature"],
                "games": node["games"], "share": node["share"],
                "median_t0": node.get("median_t0"),
                "median_anchor": node.get("median_anchor"),
                "x": center_x, "y": center_y, "r": radius,
                "label_lines": _wrap(node["signature"]),
                "color": PHASE_COLORS.get(phase, "#6ba7ff"),
            }
            cursor = center_y + radius + LABEL_BLOCK + NODE_GAP
        max_bottom = max(max_bottom, cursor)

    width = (MARGIN_LEFT + (len(columns) - 1) * COLUMN_GAP + MARGIN_RIGHT)
    height = max(max_bottom + 26.0, 260.0)
    drawn_edges = [edge for edge in all_edges
                   if edge["from"] in placed and edge["to"] in placed]
    max_count = max([edge["count"] for edge in drawn_edges] or [1])

    laid_edges = []
    labels = []
    for edge in drawn_edges:
        source = placed.get(edge["from"])
        target = placed.get(edge["to"])
        if source is None or target is None:
            continue
        x1 = source["x"] + source["r"]
        y1 = source["y"]
        x2 = target["x"] - target["r"]
        y2 = target["y"]
        same_column = edge["from"].split(":", 1)[0] == edge["to"].split(":", 1)[0]
        if same_column:
            bulge = source["r"] + 52.0
            path = ("M %.1f %.1f C %.1f %.1f %.1f %.1f %.1f %.1f"
                    % (x1, y1, x1 + bulge, y1 + 6, x1 + bulge, y2 - 6, x1, y2))
            candidates = [(x1 + bulge * 0.72, (y1 + y2) / 2.0)]
        else:
            dx = max(46.0, (x2 - x1) * 0.42)
            path = ("M %.1f %.1f C %.1f %.1f %.1f %.1f %.1f %.1f"
                    % (x1, y1, x1 + dx, y1, x2 - dx, y2, x2, y2))
            candidates = [_bezier_point(x1, y1, x2, y2, dx, t)
                          for t in (0.5, 0.36, 0.64, 0.26, 0.74)]
        condition = edge.get("condition") or "none"
        kind = "scout" if condition.startswith("scout:") else condition
        if kind not in CONDITION_COLORS:
            kind = "none"
        width_px = 1.1 + 5.2 * math.sqrt(edge["count"] / float(max_count))
        laid = {
            "from": edge["from"], "to": edge["to"],
            "count": edge["count"], "share": edge["share"],
            "condition": condition, "kind": kind,
            "median_lag": edge.get("median_lag"),
            "median_at": edge.get("median_at"),
            "median_event_lag": edge.get("median_event_lag"),
            "stroke": CONDITION_COLORS[kind],
            "width": round(width_px, 2),
            "path": path, "mid_x": candidates[0][0], "mid_y": candidates[0][1],
        }
        laid_edges.append(laid)
        labels.append({"candidates": candidates, "edge": laid,
                       "text": _edge_label(edge, kind),
                       "w": _estimate_text_width(_edge_label(edge, kind))})

    _place_labels(labels, placed, width, height)
    for label in labels:
        label["box"] = {
            "x": label["x"] - label["w"] / 2.0 - 4.0,
            "y": label["y"] - 9.0,
            "w": label["w"] + 8.0,
            "h": 15.0,
        }

    hidden_edges = [edge for edge in all_edges
                    if edge["from"] not in placed or edge["to"] not in placed]
    dropped = dag.get("dropped_edges") or []
    drawn_transitions = sum(edge.get("count") or 0 for edge in laid_edges)
    total_transitions = (dag.get("reaction") or {}).get("transitions") or drawn_transitions
    return {
        "width": round(width, 1), "height": round(height, 1),
        "nodes": list(placed.values()), "edges": laid_edges,
        "labels": labels,
        "columns": [{"phase": phase, "label": PHASE_LABELS.get(phase, phase),
                     "x": MARGIN_LEFT + index * COLUMN_GAP}
                    for index, phase in enumerate(columns)],
        "legend_y": height - 12.0,
        "empty": False,
        "hidden": {
            "nodes": len(hidden_nodes),
            "edges": len(hidden_edges),
            "dropped_edges": len(dropped),
            "transitions": max(0, total_transitions - drawn_transitions),
            "transitions_total": total_transitions,
            "transitions_drawn": drawn_transitions,
        },
        "visible": {"nodes": len(placed), "edges": len(laid_edges)},
    }


def _edge_label(edge, kind):
    icon = {"time": "⏱", "scout": "👁", "none": "?"}[kind]
    return "%s ×%d" % (icon, edge["count"])


def _estimate_text_width(text, font_size=10.5):
    width = 0.0
    for char in text:
        width += font_size * (0.56 if ord(char) < 0x2E80 else 1.0)
    return width


def _bezier_point(x1, y1, x2, y2, dx, t):
    """三次贝塞尔上参数 t 的点（控制点按水平方向外推 dx）。"""
    p1 = (x1 + dx, y1)
    p2 = (x2 - dx, y2)
    inv = 1.0 - t
    x = (inv ** 3) * x1 + 3 * (inv ** 2) * t * p1[0] + 3 * inv * (t ** 2) * p2[0] + (t ** 3) * x2
    y = (inv ** 3) * y1 + 3 * (inv ** 2) * t * p1[1] + 3 * inv * (t ** 2) * p2[1] + (t ** 3) * y2
    return (x, y)


def _label_overlap_cost(box, placed_boxes, nodes):
    cost = 0.0
    for other in placed_boxes:
        overlap_x = min(box[2], other[2]) - max(box[0], other[0])
        overlap_y = min(box[3], other[3]) - max(box[1], other[1])
        if overlap_x > -4 and overlap_y > -4:
            cost += (max(0.0, overlap_x) + 6.0) * (max(0.0, overlap_y) + 6.0)
    for node in nodes:
        closest_x = min(max(node["x"], box[0]), box[2])
        closest_y = min(max(node["y"], box[1]), box[3])
        distance = math.hypot(node["x"] - closest_x, node["y"] - closest_y)
        if distance < node["r"] + 3.0:
            cost += (node["r"] + 3.0 - distance) ** 2 * 40.0
    return cost


def _place_labels(labels, placed_nodes, width, height):
    """给每条边挑一个标签位置：候选点（贝塞尔上不同的 t）+ 贪心避让。

    先放粗边（粗边更重要），每个候选点算一个代价：压到别的标签 / 压到节点圈都算坏。
    """
    nodes = list(placed_nodes.values())
    boxes = []
    for label in sorted(labels, key=lambda item: -item["edge"]["count"]):
        text_width = label["w"]
        best = None
        for x, y in label["candidates"]:
            half = text_width / 2.0 + 4.0
            x = max(half + 6.0, min(width - half - 6.0, x))
            y = max(HEADER_HEIGHT - 8.0, min(height - 16.0, y))
            box = (x - half, y - 9.0, x + half, y + 6.0)
            cost = _label_overlap_cost(box, boxes, nodes)
            if best is None or cost < best[0]:
                best = (cost, x, y, box)
        label["x"], label["y"] = best[1], best[2]
        boxes.append(best[3])
    for label in labels:
        label["x"] = max(label["w"] / 2.0 + 6.0,
                         min(width - label["w"] / 2.0 - 6.0, label["x"]))
        label["y"] = max(HEADER_HEIGHT - 8.0, min(height - 16.0, label["y"]))


def check_layout(layout, skip_empty=True):
    """自检：节点不重叠、坐标不出画布、文字不空。返回问题列表。"""
    problems = []
    width = layout["width"]
    height = layout["height"]
    nodes = layout["nodes"]
    if skip_empty and layout.get("empty"):
        return problems
    for node in nodes:
        if node["x"] - node["r"] < 4 or node["x"] + node["r"] > width - 4:
            problems.append("节点 %s 横向出界" % node["id"])
        if node["y"] - node["r"] < HEADER_HEIGHT - 10 or node["y"] + node["r"] > height - 4:
            problems.append("节点 %s 纵向出界" % node["id"])
        if not node["signature"]:
            problems.append("节点 %s 无文字" % node["id"])
        for line in node["label_lines"]:
            if not line or line == "None" or "%%" in line:
                problems.append("节点 %s 文字异常: %r" % (node["id"], line))
    for i, first in enumerate(nodes):
        for second in nodes[i + 1:]:
            distance = math.hypot(first["x"] - second["x"], first["y"] - second["y"])
            if distance <= first["r"] + second["r"] + 6.0:
                problems.append("节点重叠: %s / %s (%.0f < %.0f)"
                                % (first["id"], second["id"], distance,
                                   first["r"] + second["r"] + 6.0))
    for label in layout.get("labels") or []:
        if "None" in label["text"] or "%%" in label["text"]:
            problems.append("边标签文字异常: %r" % label["text"])
        if label["y"] < 8 or label["y"] > height - 6:
            problems.append("边标签出界: %r y=%.0f" % (label["text"], label["y"]))
    for edge in layout["edges"]:
        if edge["count"] < 1:
            problems.append("边 %s→%s 次数异常" % (edge["from"], edge["to"]))
    return problems


# --------------------------------------------------------------------------- SVG

ARROW_MARKERS = "".join(
    '<marker id="arrow-%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
    'markerHeight="7" orient="auto-start-reverse">'
    '<path d="M 0 0 L 10 5 L 0 10 z" fill="%s"/></marker>'
    % (kind, color) for kind, color in CONDITION_COLORS.items()
)


def _svg_text(x, y, text, size=11.0, fill="#e6ecf5", anchor="middle", weight="400"):
    return ('<text x="%.1f" y="%.1f" font-size="%.1f" fill="%s" text-anchor="%s" '
            'font-weight="%s" font-family="inherit">%s</text>'
            % (x, y, size, fill, anchor, weight, h(text)))


def _svg_node(node):
    parts = []
    parts.append('<g class="node"><title>%s · %d 局 · 占比 %.0f%% · 中位进入 %.0fs</title>'
                 % (h(node["id"]), node["games"], node["share"] * 100,
                    node.get("median_anchor") or node.get("median_t0") or 0))
    parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" fill-opacity="0.20" '
                 'stroke="%s" stroke-width="2"/>'
                 % (node["x"], node["y"], node["r"], node["color"], node["color"]))
    # 圈内：局数；字大一点，直接能看出样本量
    inner = "%d" % node["games"]
    size = min(16.0, max(10.5, node["r"] * 0.62))
    parts.append(_svg_text(node["x"], node["y"] + size * 0.35, inner, size=size,
                           fill="#f2f6fc", weight="700"))
    label_top = node["y"] + node["r"] + 15.0
    for line_index, line in enumerate(node["label_lines"]):
        parts.append(_svg_text(node["x"], label_top + line_index * 13.0, line,
                               size=11.0, fill="#c9d6e8"))
    last = label_top + (len(node["label_lines"]) - 1) * 13.0
    parts.append(_svg_text(node["x"], last + 12.5,
                           "%d 局 · %.0f%%" % (node["games"], node["share"] * 100),
                           size=10.0, fill="#7d8ca3"))
    parts.append("</g>")
    return "".join(parts)


def _svg_edge(edge):
    return ('<g class="edge"><title>%s → %s · %d 次 · 条件 %s · 中位滞后 %ss</title>'
            '<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" '
            'stroke-opacity="%.2f" marker-end="url(#arrow-%s)"/></g>'
            % (h(edge["from"]), h(edge["to"]), edge["count"], h(edge["condition"]),
               edge.get("median_lag"), edge["path"], edge["stroke"], edge["width"],
               0.45 + 0.5 * min(1.0, edge["count"] / 8.0), edge["kind"]))


def _svg_label(label):
    box = label["box"]
    return ('<g><rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="4.5" '
            'fill="#0f141b" fill-opacity="0.82" stroke="%s" stroke-opacity="0.35"/>'
            '%s</g>'
            % (box["x"], box["y"], box["w"], box["h"],
               CONDITION_COLORS[label["edge"]["kind"]],
               _svg_text(label["x"], label["y"] + 4.0, label["text"], size=10.5,
                         fill=CONDITION_COLORS[label["edge"]["kind"]])))


def render_svg(layout, title=""):
    width = layout["width"]
    height = layout["height"]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %.1f %.1f" '
             'role="img" aria-label="%s">'
             % (width, height, h(title or "BO 转移图"))]
    parts.append("<defs>%s</defs>" % ARROW_MARKERS)
    parts.append('<rect x="0" y="0" width="%.1f" height="%.1f" fill="#11161e"/>'
                 % (width, height))
    # phase 分列的表头与竖向分隔
    for column in layout["columns"]:
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#243044" stroke-width="1"/>'
                     % (column["x"], HEADER_HEIGHT - 14, column["x"], height - 22))
        parts.append(_svg_text(column["x"], HEADER_HEIGHT - 26, column["label"],
                               size=12.5, fill="#8fa3bf", weight="600"))
    for edge in layout["edges"]:
        parts.append(_svg_edge(edge))
    for label in layout["labels"]:
        parts.append(_svg_label(label))
    for node in layout["nodes"]:
        parts.append(_svg_node(node))
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- HTML 片段

def _pct(value, digits=0):
    return ("%." + str(digits) + "f%%") % (float(value or 0.0) * 100.0)


def _mmss(seconds):
    if seconds is None:
        return "—"
    seconds = max(0.0, float(seconds))
    return "%d:%02d" % (int(seconds) // 60, int(seconds) % 60)


def _metric(label, value, note=""):
    return ('<div class="metric"><div class="k">%s</div><div class="v">%s</div>'
            '<div class="n">%s</div></div>' % (h(label), h(value), h(note)))


def _bar3(reaction):
    total = max(1, reaction.get("transitions") or 1)
    segments = [
        ("scout", reaction.get("scout", 0), CONDITION_COLORS["scout"], "侦察触发"),
        ("time", reaction.get("time", 0), CONDITION_COLORS["time"], "到点"),
        ("none", reaction.get("none", 0), CONDITION_COLORS["none"], "无法判定"),
    ]
    parts = ['<div class="bar3">']
    for _key, count, color, _label in segments:
        if count:
            parts.append('<span style="width:%.2f%%;background:%s"></span>'
                         % (100.0 * count / total, color))
    parts.append("</div>")
    return "".join(parts)


def _wilson(count, total, z=1.96):
    """二项比例的 Wilson 95% 区间（小样本比正态近似靠谱）。"""
    total = float(total)
    if total <= 0:
        return (0.0, 0.0)
    p = float(count) / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    margin = (z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total))
              / denominator)
    return (max(0.0, center - margin), min(1.0, center + margin))


def _ci_text(count, total):
    low, high = _wilson(count, total)
    return "%d/%d（95%% 区间 %.0f%%–%.0f%%）" % (count, total, low * 100, high * 100)


def _reaction_answer(reaction, label=""):
    share = float(reaction.get("scout_share") or 0.0)
    games = reaction.get("games") or 0
    if reaction.get("transitions", 0) == 0:
        return "这组里没有可分析的转移。"
    # 60 秒窗口比 20 秒窗口稳（20 秒和位置采样精度同量级），它是"反应有多慢"的主证据
    by_window = reaction.get("by_window") or {}
    window60 = by_window.get("60") or {}
    share60 = float(window60.get("scout_share") or 0.0)
    if share < 0.12:
        verdict = ("基本是<b>到点执行</b>：只有 %s 的转移发生在「刚拿到新信息」的 20 秒内，"
                   "练的主要是背下来的流程。" % _pct(share, 1))
    elif share < 0.3:
        verdict = ("<b>混合</b>：%s 的转移和刚获得的信息同时发生，"
                   "其余按时间推进——大框架是背的，局部会看情况动。" % _pct(share, 1))
    else:
        verdict = ("偏<b>反应式</b>：%s 的转移发生在刚拿到新信息的 20 秒内，"
                   "说明打法大量取决于侦察到的东西。" % _pct(share, 1))
    latency = ""
    if share60 > share * 1.4 and share60 >= 0.12:
        latency = ("<br>但把窗口放宽到 60 秒，比例升到 %s（%d/%d）——"
                   "这组不是「看到 20 秒内就改」，而是<b>看到之后一分钟内会动</b>，"
                   "反应有几十秒的滞后。这条比单看 20 秒更稳（20 秒和位置采样的时间精度同量级）。"
                   % (_pct(share60, 1), window60.get("scout", 0),
                      window60.get("transitions", 0)))
    return ("这 %d 局里一共 %d 次段间转移，其中 %d 次（%s）在段开始前 20 秒内"
            "有信息事件，%d 次（%s）是纯到点，%d 次无法判定。%s%s"
            % (games, reaction.get("transitions", 0), reaction.get("scout", 0),
               _pct(reaction.get("scout_share"), 1),
               reaction.get("time", 0), _pct(reaction.get("time_share"), 1),
               reaction.get("none", 0), verdict, latency))


def _sensitivity_line(reaction):
    """20 秒窗口比采样精度还小，必须把 40/60 秒的敏感性摊开给人看。"""
    by_window = reaction.get("by_window") or {}
    if not by_window:
        return ""
    chunks = []
    for window in sorted(by_window, key=lambda item: int(item)):
        bucket = by_window[window]
        chunks.append("%s 秒 → %s（%d/%d 次）"
                      % (window, _pct(bucket.get("scout_share"), 1),
                         bucket.get("scout", 0), bucket.get("transitions", 0)))
    return ('<div class="note" style="margin-top:6px">窗口敏感性：'
            "把「段开始前 N 秒内有没有信息事件」的 N 从 20 秒放宽——"
            + "；".join(chunks) + "。20 秒是主口径（和契约一致），"
            "但 20 秒和采样精度同量级，<b>更长窗口那两档更能反映「反应有多慢」</b>。</div>")


def _transitions_table(dag, limit=10):
    edges = dag.get("edges") or []
    if not edges:
        return '<div class="note">没有达到阈值的转移。</div>'
    rows = []
    for index, edge in enumerate(edges[:limit], 1):
        condition = edge.get("condition") or "none"
        icon = "👁" if condition.startswith("scout:") else ("⏱" if condition == "time" else "?")
        detail = condition.split(":", 1)[1] if ":" in condition else ""
        if detail and condition == "scout:%s" % detail:
            detail = ""      # "scout:contact（contact）" 这种重复不要
        mix = edge.get("conditions") or {}
        mix_text = " · ".join("%s%d" % (key.split(":", 1)[-1], count)
                              for key, count in sorted(mix.items(),
                                                       key=lambda item: -item[1]))
        event_lag = edge.get("median_event_lag")
        rows.append(
            "<tr><td>%d</td><td class='l'>%s</td><td class='l'>%s</td><td>%d</td>"
            "<td>%s</td><td class='l'>%s %s</td><td class='l'>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td></tr>"
            % (index, h(edge["from"]), h(edge["to"]), edge["count"],
               _pct(edge.get("share"), 1),
               icon, h(condition + (" " + detail if detail else "")),
               h(mix_text or "—"),
               _mmss(edge.get("median_at")),
               "—" if edge.get("median_lag") is None
               else "%ds" % int(round(edge["median_lag"])),
               ("—" if not condition.startswith("scout:") or event_lag is None
                else "%ds" % int(round(event_lag)))))
    return ('<table class="data"><thead><tr><th>#</th><th>从</th><th>到</th>'
            "<th>次数</th><th>占起点段</th><th>条件</th><th>条件分布</th>"
            "<th>中位切换时刻</th><th>中位滞后</th><th>信息→转移</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>"
            '<div class="note">「条件」是这条边的多数决标签，「条件分布」是它的逐次明细'
            "（time = 到点，其余 = 侦察到的事件类型）。「中位切换时刻」= 后一段第一条实际"
            "建造事件的时刻；「中位滞后」= 后一段锚点 − 前一段锚点（起点段停留了多久）；"
            "「信息→转移」只在侦察触发的边上给（信息事件到锚点的中位间隔）。</div>")


def _paths_table(dag, limit=10):
    paths = dag.get("top_paths") or []
    games = dag["meta"].get("games") or 1
    if not paths:
        return '<div class="note">没有完整路径。</div>'
    rows = []
    for index, item in enumerate(paths[:limit], 1):
        rows.append("<tr><td>%d</td><td class='l'>%s</td><td>%d</td><td>%s</td></tr>"
                    % (index, h(" → ".join(item["path"])), item["count"],
                       _pct(item["count"] / float(games), 1)))
    return ('<table class="data"><thead><tr><th>#</th><th>路径（段签名链）</th>'
            "<th>局数</th><th>占样本</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def _fragmentation_line(dag):
    """节点碎片化程度：如果大部分节点只出现一次，那"图"其实是一条条一次性路径。"""
    nodes = dag.get("nodes") or []
    if not nodes:
        return ""
    once = sum(1 for node in nodes if (node.get("games") or 0) == 1)
    edges = dag.get("edges") or []
    transitions = (dag.get("reaction") or {}).get("transitions") or 0
    top5 = sum(edge["count"] for edge in edges[:5])
    share = (top5 / float(transitions)) if transitions else 0.0
    phases = (dag.get("meta") or {}).get("phases") or []
    if phases and "mid" not in phases and "late" not in phases:
        tail = ("——这是只看 opening/early 的结果：碎片化主要来自 early 段里"
                "各选手的建造顺序差异，不影响「开局选择面」那一节的结论。")
    else:
        tail = ("——这说明中期以后<b>没有固定模板</b>，或者 bo 的段签名把执行细节放得太细，"
                "图只在开局到前期收敛。")
    return ('<div class="note" style="margin-top:6px">碎片化：%d 个节点里 <b>%d 个只出现 1 次</b>；'
            "转移次数最多的 5 条边覆盖 %s 的转移（共 %d 次）。%s</div>"
            % (len(nodes), once, _pct(share, 0), transitions,
               tail if once > len(nodes) * 0.4 else ""))


def _game_signature_line(dag):
    signatures = (dag.get("meta") or {}).get("game_signatures") or {}
    if not signatures:
        return ""
    total = sum(signatures.values()) or 1
    items = ["%s %d 局（%s）" % (key, value, _pct(value / float(total), 0))
             for key, value in signatures.items()]
    return ('<div class="note" style="margin-top:6px">这组的开局签名（整局封闭词表，'
            "和上面的段签名不是一回事）：" + "；".join(items) + "。</div>")


def opening_concentration(dag):
    """开局（第一个段）的集中度：职业 Z/P 的"选择面"用这个比。

    返回 opening 首段的分布、最粗开局占比、归一化香农熵（0=只此一家，1=完全平均）。
    """
    counts = {}
    for sequence in dag.get("sequences") or []:
        path = sequence.get("path") or []
        if not path or not path[0].startswith("opening:"):
            continue
        counts[path[0]] = counts.get(path[0], 0) + 1
    games = sum(counts.values())
    if not games:
        return None
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    top_id, top_count = ordered[0]
    entropy = -sum((count / float(games)) * math.log(count / float(games))
                   for count in counts.values())
    normalized = entropy / math.log(len(counts)) if len(counts) > 1 else 0.0
    return {
        "games": games,
        "opening_nodes": len(counts),
        "top": top_id,
        "top_count": top_count,
        "top_share": round(top_count / float(games), 3),
        "entropy": round(normalized, 3),
        "signatures": {key: value for key, value in ordered},
    }


def _opening_paths_table(dag, limit=3):
    """开局阶段最常见的 N 条路径（只在 opening/early 的 DAG 上用）。"""
    paths = dag.get("top_paths") or []
    games = dag["meta"].get("games") or 1
    if not paths:
        return '<div class="note">没有完整路径。</div>'
    rows = []
    for index, item in enumerate(paths[:limit], 1):
        rows.append("<tr><td>%d</td><td class='l'>%s</td><td>%d</td><td>%s</td></tr>"
                    % (index, h(" → ".join(item["path"])), item["count"],
                       _pct(item["count"] / float(games), 1)))
    return ('<div class="note" style="margin-top:4px"><b>开局最常见 %d 条路径</b>'
            "（从 opening 段到 early 段的段签名链）：</div>"
            '<table class="data"><thead><tr><th>#</th><th>路径</th><th>局数</th>'
            "<th>占样本</th></tr></thead><tbody>" % limit
            + "".join(rows) + "</tbody></table>")


def _dag_block(dag, title, subtitle, min_node=2, paths_limit=0, extra_note="",
               show_reaction=True):
    """一张 DAG 的 HTML 块。

    ``show_reaction=False`` 用于职业 Z/P 的"开局选择面"图：那个窗口里
    位置采样还没开始，谈"侦察触发"没有意义，所以换成开局集中度指标。
    """
    layout = layout_dag(dag, min_node=min_node)
    problems = check_layout(layout)
    reaction = dag.get("reaction") or {}
    concentration = opening_concentration(dag)
    parts = ['<div class="card"><div class="desc">%s</div>' % h(subtitle)]
    parts.append('<div class="grid4" style="margin-top:10px">')
    parts.append(_metric("样本", "%d 局" % (dag["meta"].get("games") or 0),
                         "有信息事件 %d 局" % (dag["meta"].get("games_with_info_events") or 0)))
    parts.append(_metric("节点 / 边", "%d / %d" % (len(dag["nodes"]), len(dag["edges"])),
                         "图上画了 %d / %d 条" % ((layout.get("visible") or {}).get("nodes", 0),
                                                  (layout.get("visible") or {}).get("edges", 0))))
    if show_reaction:
        parts.append(_metric("侦察触发占比", _pct(reaction.get("scout_share"), 1),
                             "%d/%d 次转移" % (reaction.get("scout", 0),
                                              reaction.get("transitions", 0))))
        parts.append(_metric("纯到点占比", _pct(reaction.get("time_share"), 1),
                             "%d 次转移" % reaction.get("time", 0)))
    else:
        parts.append(_metric("开局打法", "%d 种" % (concentration or {}).get("opening_nodes", 0),
                             "最粗那套占 %s" % _pct((concentration or {}).get("top_share"), 0)))
        parts.append(_metric("开局集中度（熵）", "%.2f" % ((concentration or {}).get("entropy") or 0.0),
                             "0=只此一套，1=完全平均"))
    parts.append("</div>")
    if show_reaction:
        parts.append('<div class="answer" style="margin:12px 0 6px">%s</div>'
                     % _reaction_answer(reaction))
        parts.append(_bar3(reaction))
        parts.append(_sensitivity_line(reaction))
    else:
        parts.append('<div class="note" style="margin-top:10px">这两张图只看 '
                     "<b>opening/early 的开局选择面</b>：不做「侦察触发 vs 到点」的归因——"
                     "opening→early 的切换大约在 3:03，而位置采样最早也要 3:30 之后才出现，"
                     "这个窗口里根本没有可用的信息事件，谈反应没有意义。"
                     "所以边一律按「到点」画（灰色/蓝色），重点看节点的分布和分支。</div>")
    parts.append(_game_signature_line(dag))
    if concentration:
        top_items = ["%s %d 局（%s）"
                     % (h(key.split(":", 1)[-1]), value,
                        _pct(value / float(concentration["games"]), 0))
                     for key, value in list(concentration["signatures"].items())[:5]]
        parts.append('<div class="note" style="margin-top:6px">开局（第一段）分布：'
                     + "、".join(top_items) + "。</div>")
    parts.append(_fragmentation_line(dag))
    if paths_limit:
        parts.append(_opening_paths_table(dag, paths_limit))
    if extra_note:
        parts.append('<div class="note" style="margin-top:6px">%s</div>' % extra_note)
    parts.append('<div class="dag-legend">')
    for kind, color, label in (("time", CONDITION_COLORS["time"], "⏱ 到点转移"),
                               ("scout", CONDITION_COLORS["scout"], "👁 侦察触发转移"),
                               ("none", CONDITION_COLORS["none"], "? 无法判定")):
        parts.append('<span class="lg"><i class="line" style="background:%s"></i>%s</span>'
                     % (color, h(label)))
    parts.append('<span class="lg">圆圈大小 / 圈内数字 = 该节点的局数</span>')
    parts.append('<span class="lg">线粗 = 转移次数</span>')
    parts.append("</div>")
    if layout.get("empty"):
        parts.append('<div class="note" style="margin-top:10px">这组没有可用分段，画不出图。</div>')
    else:
        parts.append('<div class="dag-card" style="margin-top:12px">%s</div>'
                     % render_svg(layout, title))
        hidden = layout.get("hidden") or {}
        visible = layout.get("visible") or {}
        total = hidden.get("transitions_total") or 1
        if hidden.get("transitions"):
            parts.append('<div class="note" style="margin-top:6px">图上画的是「出现 ≥%d 局的节点」'
                         "之间的转移（<b>%d 个节点 / %d 条边 / %d 次转移</b>）。"
                         "另外 <b>%d 次转移（%s）</b> 没画出来——它们来自只出现 1 次的节点或"
                         "只走过 1 次的转移（%d 个单例节点、%d 条单例边），完整数据在 "
                         "<code>dag.json</code> 和下面的节点表里。</div>"
                         % (min_node, visible.get("nodes", 0), visible.get("edges", 0),
                            hidden.get("transitions_drawn", 0),
                            hidden.get("transitions", 0),
                            _pct(hidden.get("transitions", 0) / float(total), 0),
                            hidden.get("nodes", 0), hidden.get("dropped_edges", 0)))
        else:
            parts.append('<div class="note" style="margin-top:6px">图上节点 %d 个、转移 %d 条，'
                         "已覆盖全部 %d 次转移。</div>"
                         % (visible.get("nodes", 0), visible.get("edges", 0),
                            hidden.get("transitions_drawn", 0)))
    if problems:
        parts.append('<div class="warnbox note" style="margin-top:10px">布局自检没全过：%s</div>'
                     % h("；".join(problems[:6])))
    else:
        parts.append('<div class="note" style="margin-top:8px">布局自检通过：'
                     "节点无重叠、坐标不出画布、边标签不互相压住。</div>")
    parts.append("</div>")
    return "".join(parts), layout


def _answer_box(mine, pro, pro_label="Maru"):
    mine_r = (mine or {}).get("reaction") or {}
    pro_r = (pro or {}).get("reaction") or {}
    if not mine_r:
        return ""
    pro_games = (pro or {}).get("meta", {}).get("games") or 0
    mine_total = mine_r.get("transitions") or 0
    pro_total = pro_r.get("transitions") or 0
    compare = ""
    if pro_total:
        mine_count = mine_r.get("scout", 0)
        pro_count = pro_r.get("scout", 0)
        mine_low, mine_high = _wilson(mine_count, mine_total)
        pro_low, pro_high = _wilson(pro_count, pro_total)
        overlap = mine_low <= pro_high and pro_low <= mine_high
        window_line = ("20 秒口径：我 %s vs %s %s。"
                       % (_ci_text(mine_count, mine_total), h(pro_label),
                          _ci_text(pro_count, pro_total)))
        if overlap:
            compare = ("<br><b>和%s 比：样本不足以区分。</b>" % h(pro_label) + window_line +
                       " 两边的 95%% 区间是重叠的——对照那组 %d 次转移里 %d 次，"
                       "上界就有 %.0f%%，和我的点估计差不多。"
                       "别读成「我比他更爱（或更不爱）被动反应」，只能说这个样本量下"
                       "两组分不开（对照只有 %d 局，且可能含补样本）。"
                       % (pro_total, pro_count, pro_high * 100, pro_games))
        else:
            direction = "高" if mine_count / float(mine_total) >= pro_count / float(pro_total) else "低"
            compare = ("<br>和%s比，我被侦察推着走的比例%s一些，" % (h(pro_label), direction)
                       + window_line + " 两组区间不重叠，但样本仍然不大，"
                       "当量级看别当精确排名。")
        # 窗口敏感性：20 秒窗口本身比采样精度还小，单看这个数会晃；60 秒那档更稳
        mine_win = (mine_r.get("by_window") or {})
        pro_win = (pro_r.get("by_window") or {})
        parts_window = []
        for window in ("20", "40", "60"):
            mine_bucket = mine_win.get(window) or {}
            pro_bucket = pro_win.get(window) or {}
            parts_window.append("%s 秒：%s vs %s"
                                % (window,
                                   _pct(mine_bucket.get("scout_share"), 1),
                                   _pct(pro_bucket.get("scout_share"), 1)))
        compare += ("<br>窗口敏感性（同一批转移，只改「往前看多少秒」）："
                    + "；".join(parts_window) +
                    "。60 秒那一档比 20 秒稳得多，结论优先看它；"
                    "对照只有 %d 局，比例波动大。" % pro_games)
    elif pro is not None:
        compare = "%s 那一组没算出转移，无法对比。" % h(pro_label)
    return ('<div class="card"><div class="answer">%s%s</div>'
            '<div class="note" style="margin-top:8px">注意：这里的「侦察触发」只是'
            "<b>相关性</b>——事件和转移落在同一个 20 秒窗口里，不等于因果。"
            "而且事件本身是几何推断，时间精度中位就有 ±20 秒，和窗口同量级。"
            "更要紧的是：bo 的段边界本来就按时间切（0/180/360/600），"
            "「到点」这个结论有一半是分段方式自带的。</div></div>"
            % (_reaction_answer(mine_r), compare))


LIMITS = """
<ol>
<li><b>信息事件是几何推断，不是真值。</b>replay 里没有「玩家当时知道什么」这个字段，
这里用 <code>replay_analyzer.fog</code> 的视野圆并集反推「谁看得见谁」。看得见 ≠ 看懂了，
更 ≠ 据此改了打法。所以「侦察触发」只能当<b>相关性</b>读，不是因果。</li>
<li><b>位置采样到 4 分钟以后才开始。</b>实测 tracker 的 <code>SUnitPositionsEvent</code>
多数局要到 <b>4:00–4:30</b> 才有第一个点（本批中位 4:00、最早的一局 2:00），而且同一个
单位中位 45 秒才给一个点（90 分位 270 秒）。结果是：<b>4 分钟之前的侦察基本算不出来</b>
（最典型的 SCV 探路正好在这一段），算出来的事件时间精度也只有 ±20 秒左右。
所有时间只能当「几分钟」看，别当秒级真值。</li>
<li><b>20 秒窗口和采样精度是同一量级。</b>事件时间误差中位数就有 ±20 秒，
所以「段开始前 20 秒内有事件」这个判定，本质上是「大致同时」。报告里附了
40 / 60 秒窗口的敏感性，就是为了让你看到这个尺度上结论会怎么晃。</li>
<li><b>视野没算地形遮挡。</b>高地、悬崖、岩石挡视线在 replay 里没有数据，这里当平地处理，
所以「隔着悬崖看见」会被高估，贴边的接触可能被提前。</li>
<li><b>扫描（scan）没有实现。</b>雷达扫描没有单位载体，不在几何视野模型里；潜地/隐形的
判定也没做。这类信息拿不到就写 <code>none</code>，不猜。</li>
<li><b>段签名和整局签名是两套东西。</b>节点用的是 bo 的<b>段</b>签名（token 链，
例如 <code>rax-gas-cc</code>，见契约第三节的例子）；整局那个封闭词表
（<code>1-rax-fast-expand</code> / <code>2-1-1</code> …）单独统计，放在每个 DAG 的
「开局签名」那一行和节点表的备注里。段签名出现陌生 token 时保留原样并标注。</li>
<li><b>段的起点用锚点。</b>bo 的段 <code>t0</code> 是按时间表切的整点（0/180/360/600），
直接拿它当转移时刻会退化成「看整点前 20 秒」。所以这里用段内第一条实际建造/训练事件的
时刻当锚点（没有 steps 才退回 t0）。</li>
<li><b>bo 的扩张时间有 tag 回收错配（已知数据噪音）。</b>ESL/GameHeart 老 replay
（build 88500）里个别建筑的 <code>start_loop</code> 会被回收的 tag 带偏（比如「二矿」
出现在 81 秒），bo 那边在修。DAG 这一层用的是<b>时间桶分阶段</b> + <b>段内第一条建造事件的
loop</b> 当锚点，不依赖 <code>start_loop</code>，所以节点的层位不受影响；
受影响的主要是开局签名的 token 和 bo 报的 <code>key_timings</code>。</li>
<li><b>信息事件的阈值对所有对位统一。</b>army_seen 的门槛是「一次看到对方 ≥5 个
部队、造价合计 ≥500」，contact / scout_arrive / tech_seen 的定义也不分种族和对位。
好处是三个对位可比，代价是 TvT（单位贵、交手早）比 TvZ 更容易先满足 army_seen、
而 TvZ 更常靠 tech_seen/contact。每个对位各自的分布见那一节的「信息来源」表。</li>
<li><b>边条件按多数决。</b>一条边只要有几次转移落在信息事件窗口里，就可能被标成
<code>scout:</code>；完整分布见 <code>reports/dag.json</code> 里每条边的
<code>conditions</code> 和 <code>conditions_by_window</code> 字段。</li>
</ol>
"""


def _limits_block():
    return ('<div style="margin-top:26px"><h2>这套数的边界（必读）</h2>'
            '<div class="card"><div class="hint">%s</div></div></div>' % LIMITS)


# --------------------------------------------------------------------------- 对位层面的采样

MIN_GROUP_GAMES = 15          # 少于这么多局就不画图，只标"样本不足"
MATCHUP_ORDER = ("TvT", "TvZ", "TvP")


def _dir_fingerprint(directory):
    """目录指纹：文件数 + 最新 mtime（够 cheap，不用每个文件都 stat 内容）。"""
    if not directory or not os.path.isdir(directory):
        return ""
    count = 0
    newest = 0
    for item in glob.glob(os.path.join(directory, "*.SC2Replay")):
        try:
            stat = os.stat(item)
        except OSError:
            continue
        count += 1
        newest = max(newest, int(stat.st_mtime))
    return "%d|%d" % (count, newest)


def extract_mine_matchups(directory, matchups, cache_path=None, limit=300,
                          rebuild=False, verbose=True):
    """从我的 replay 目录里按对位抽 BO（只补 bo.json 里没有的对位）。

    bo.json（task-8 之前）只挖了 TvZ，这里用 bo 自己的 ``mine_folder``
    （同样的 12 农民 / 官方图 / 时长过滤）把 TvP / TvT 补出来，缓存到 .tmp。
    等 bo 的新 bo.json 带全部对位时，这一步会自动变成空操作。
    """
    cache_path = cache_path or os.path.join(".tmp", "mine_bo_by_matchup.json")
    fingerprint = _dir_fingerprint(directory)
    cached = {}
    if not rebuild and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if (payload.get("meta") or {}).get("fingerprint") == fingerprint:
                cached = payload.get("games") or {}
                if verbose:
                    print("[mine-bo] cache hit: %s"
                          % {key: len(value) for key, value in cached.items()},
                          flush=True)
        except (OSError, ValueError):
            cached = {}

    try:
        from . import bo as bo_module
    except Exception as error:
        print("[warn] 无法 import bo.py（%s），不补我的对位样本" % error, flush=True)
        return {matchup: cached.get(matchup) or [] for matchup in matchups}

    changed = False
    for matchup in matchups:
        if cached.get(matchup):
            continue
        if verbose:
            print("[mine-bo] extracting %s ..." % matchup, flush=True)
        try:
            games = bo_module.mine_folder(
                directory, limit=limit, matchup=matchup,
                require_local=True, exclude_ai=True, ladder_only=True,
                min_duration=120.0, start_workers_only=12)
        except Exception as error:
            print("[warn] mine_folder(%s) 失败：%s" % (matchup, error), flush=True)
            games = []
        cached[matchup] = games
        changed = True
        if verbose:
            print("[mine-bo] %s -> %d games" % (matchup, len(games)), flush=True)
    if changed:
        try:
            payload = {"meta": {"fingerprint": fingerprint,
                                "generated": dagmod.datetime.date.today().isoformat()},
                       "games": cached}
            directory_out = os.path.dirname(os.path.abspath(cache_path))
            if directory_out:
                os.makedirs(directory_out, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        except OSError:
            pass
    return {matchup: cached.get(matchup) or [] for matchup in matchups}


PRO_BO_CACHE = os.path.join(".tmp", "pro_bo_all.json")


def extract_pro_bos(root="replays/pro", races=("T", "Z", "P"),
                    cache_path=PRO_BO_CACHE, rebuild=False, verbose=True):
    """抽所有职业 replay 的 GameBO（T/Z/P 都抽），按 (对位, 选手) 分组用。

    只抽"这个目录代表的选手种族在 races 里"的目录。缓存按全部 replay 的
    (路径, 大小, mtime) 指纹失效；bo.py 换了签名逻辑时用 --force 重抽。
    """
    files = sorted(glob.glob(os.path.join(root, "*", "*.SC2Replay")))
    fingerprint = hashlib.sha1()
    for item in files:
        try:
            stat = os.stat(item)
        except OSError:
            continue
        fingerprint.update(("%s|%s|%s;" % (os.path.abspath(item), stat.st_size,
                                            int(stat.st_mtime))).encode("utf-8"))
    fingerprint = fingerprint.hexdigest()
    if not rebuild and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            if (cached.get("meta") or {}).get("fingerprint") == fingerprint:
                if verbose:
                    print("[pro-bo] cache hit -> %d games (%s)"
                          % (len(cached.get("games") or []), cache_path), flush=True)
                return cached.get("games") or []
        except (OSError, ValueError):
            pass

    try:
        from . import bo as bo_module
    except Exception as error:
        print("[warn] 无法 import bo.py（%s），跳过职业抽取" % error, flush=True)
        return []

    games = []
    started = time.time()
    for index, path in enumerate(files, 1):
        folder = os.path.basename(os.path.dirname(path))
        manifest_path = os.path.join(os.path.dirname(path), "manifest.json")
        manifest = {}
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
                for item in raw if isinstance(raw, list) else []:
                    manifest[item.get("file")] = item
            except (OSError, ValueError):
                manifest = {}
        prefer, folder_race = _folder_focus(folder, manifest)
        if folder_race not in races:
            continue
        try:
            game = bo_module.extract_bo_path(path, prefer_name=prefer,
                                             with_moveout=False)
        except Exception as error:
            print("[warn] pro-bo 失败 %s (%s)" % (os.path.basename(path),
                                                  type(error).__name__), flush=True)
            continue
        player = game.get("player") or {}
        player["name"] = _clean_player_name(player.get("name"))
        games.append(game)
        if verbose:
            print("  [pro-bo %d/%d] %-50s %-8s %-6s %-16s segs=%d"
                  % (index, len(files), os.path.basename(path)[:50],
                     player.get("race"), game.get("game", {}).get("matchup"),
                     game.get("signature"), len(game.get("segments") or [])),
                  flush=True)
    try:
        directory_out = os.path.dirname(os.path.abspath(cache_path))
        if directory_out:
            os.makedirs(directory_out, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"meta": {"fingerprint": fingerprint,
                                "generated": dagmod.datetime.date.today().isoformat()},
                       "games": games}, handle, ensure_ascii=False)
    except OSError:
        pass
    if verbose:
        print("[pro-bo] extracted %d games in %.0fs -> %s"
              % (len(games), time.time() - started, cache_path), flush=True)
    return games


def group_games(games, matchup, who="mine", limit=100):
    """挑某一组：who="mine" 本地玩家；who="pro" 职业选手。"""
    picked = []
    for game in games:
        meta = game.get("game") or {}
        player = game.get("player") or {}
        if (meta.get("matchup") or "").lower() != matchup.lower():
            continue
        if who == "mine":
            if player.get("is_local") is not True:
                continue
        else:
            if player.get("is_local") is True:
                continue
        picked.append(game)
    picked.sort(key=lambda game: ((game.get("player") or {}).get("name") or "",
                                  (game.get("game") or {}).get("file") or ""))
    if limit:
        picked = picked[:int(limit)]
    return picked


PRO_NAME_CANON = {
    "maru": "Maru", "byun": "ByuN", "clem": "Clem", "cure": "Cure",
    "heromarine": "HeroMarine", "ty": "TY", "trap": "Trap", "zest": "Zest",
    "classic": "Classic", "stats": "Stats", "showtime": "ShoWTimE",
    "dark": "Dark", "solar": "Solar", "rogue": "Rogue", "serral": "Serral",
    "reynor": "Reynor",
}


def canonical_player_name(name):
    """把 'maru' / 'Maru' / '&lt;RBLN&gt;&lt;sp/&gt;ByuN' 归一成干净显示名。"""
    plain = _clean_player_name(name or "?")
    key = plain.lower().replace(" ", "")
    return PRO_NAME_CANON.get(key, plain)


def player_counts(games):
    """{选手名: 局数}，报告里要写清每个选手几局。"""
    counts = {}
    for game in games:
        name = canonical_player_name((game.get("player") or {}).get("name"))
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


# --------------------------------------------------------------------------- 表格 / 卡片

def _event_type_compare_table(mine, pro):
    """同一个对位下，我 vs 职业 T 的"信息事件类型"分布 + 它们触发转移的次数。"""
    names = {"scout_arrive": "看到对方主基地",
             "contact": "双方部队首次互相可见",
             "army_seen": "看到对方成建制部队",
             "tech_seen": "看到对方科技建筑"}
    rows = []
    mine_note = (mine or {}).get("precision_note") or {}
    pro_note = (pro or {}).get("precision_note") or {}
    mine_events = mine_note.get("event_types") or {}
    pro_events = pro_note.get("event_types") or {}
    mine_reaction = (mine or {}).get("reaction") or {}
    pro_reaction = (pro or {}).get("reaction") or {}
    mine_games = ((mine or {}).get("meta") or {}).get("games") or 1
    pro_games = ((pro or {}).get("meta") or {}).get("games") or 1
    mine_by_type = mine_reaction.get("by_type") or {}
    pro_by_type = pro_reaction.get("by_type") or {}
    keys = sorted(set(mine_events) | set(pro_events) | set(mine_by_type) | set(pro_by_type))
    for kind in keys:
        mine_count = mine_events.get(kind, 0)
        pro_count = pro_events.get(kind, 0)
        rows.append(
            "<tr><td class='l'>%s</td><td class='l'>%s</td>"
            "<td>%d</td><td>%.1f</td><td>%d</td>"
            "<td>%d</td><td>%.1f</td><td>%d</td></tr>"
            % (h(kind), h(names.get(kind, kind)),
               mine_count, mine_count / float(mine_games), mine_by_type.get(kind, 0),
               pro_count, pro_count / float(pro_games), pro_by_type.get(kind, 0)))
    if not rows:
        return '<div class="note">这个对位没算出信息事件。</div>'
    return ('<table class="data"><thead><tr><th>type</th><th>含义</th>'
            "<th>我·事件数</th><th>我·每局</th><th>我·触发转移</th>"
            "<th>T·事件数</th><th>T·每局</th><th>T·触发转移</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>"
            '<div class="note">「触发转移」= 因为这类事件落在 20 秒窗口里而被标成'
            "「侦察触发」的转移次数（同一事件只算最近的一次）。</div>")


def _top_info_line(reaction, dag):
    """一句话：这个对位主要靠哪类信息在动。"""
    by_type = reaction.get("by_type") or {}
    if not by_type:
        return "这个对位没算出「侦察触发」的转移，归因列不出来。"
    parts = []
    for kind, count in list(by_type.items())[:3]:
        parts.append("%s %d 次" % (h(kind), count))
    return ("这个对位里，侦察触发的转移主要来自：" + "、".join(parts)
            + "（共 %d 次侦察触发 / %d 次转移）。"
            % (reaction.get("scout", 0), reaction.get("transitions", 0)))


BO_TIMING_KEYS = (
    ("first_gas", "一气"), ("second_gas", "二气"), ("first_expand", "二矿"),
    ("first_factory", "重工"), ("first_starport", "星港"),
    ("third_rax", "三兵营"), ("army_moveout", "主力出门"),
)


def _median(values):
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(float(ordered[middle]), 1)
    return round((ordered[middle - 1] + ordered[middle]) / 2.0, 1)


def first_event_times(dag):
    """每组里"每局第一次某类信息事件"的中位时刻（秒）。"""
    buckets = {}
    for events in ((dag or {}).get("info_events") or {}).get("per_game", {}).values():
        first = {}
        for event in events:
            kind = event.get("type")
            when = event.get("t")
            if kind is None or when is None:
                continue
            if kind not in first or when < first[kind]:
                first[kind] = when
        for kind, when in first.items():
            buckets.setdefault(kind, []).append(when)
    return {kind: _median(values) for kind, values in buckets.items()}


def _bo_timeline_card(entry, bo_meta, matchup):
    """bo 的逐对位关键时间点：我 vs 该对位职业 T。"""
    detail = ((bo_meta or {}).get("matchups_detail") or {}).get(matchup) or {}
    mine_median = detail.get("median") or {}
    pro_median = detail.get("pro_median") or {}
    rows = []
    gaps = []
    for key, label in BO_TIMING_KEYS:
        mine_value = mine_median.get(key)
        pro_value = pro_median.get(key)
        if mine_value is None and pro_value is None:
            continue
        delta = None
        if mine_value is not None and pro_value is not None:
            delta = mine_value - pro_value
            gaps.append((label, delta))
        rows.append(
            "<tr><td class='l'>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (h(label), _mmss(mine_value), _mmss(pro_value),
               "—" if delta is None else "%+.0f 秒" % delta))
    if not rows:
        return ""
    largest = sorted(gaps, key=lambda item: -abs(item[1]))[:3]
    summary = "；".join("%s %+.0f 秒" % (label, delta)
                       for label, delta in largest if abs(delta) >= 5)
    note = ""
    if summary:
        note = ("<div class=\"note\" style=\"margin-top:6px\">差得最多的三项："
                + h(summary) + "（正数 = 我比职业晚）。</div>")
    return ('<div class="card" style="margin-top:14px">'
            "<h3 style=\"margin:0 0 8px\">%s · 关键时间点对照（bo 的口径）</h3>"
            "<table class=\"data\"><thead><tr><th>时间点</th><th>我·中位</th>"
            "<th>职业 T·中位</th><th>我 − 职业</th></tr></thead><tbody>%s</tbody></table>%s"
            "</div>" % (h(matchup), "".join(rows), note))


def _event_timing_card(mine, pro, matchup):
    """DAG 侧的证据：两边「第一次看到某类信息」的中位时刻，用来和 bo 的时间线互相印证。"""
    mine_times = first_event_times(mine)
    pro_times = first_event_times(pro)
    names = {"contact": "双方部队首次互见", "army_seen": "看到成建制部队",
             "tech_seen": "看到科技建筑", "scout_arrive": "看到对方主基地"}
    rows = []
    gaps = []
    for kind in ("contact", "army_seen", "tech_seen", "scout_arrive"):
        mine_value = mine_times.get(kind)
        pro_value = pro_times.get(kind)
        if mine_value is None and pro_value is None:
            continue
        delta = (mine_value - pro_value
                 if (mine_value is not None and pro_value is not None) else None)
        if delta is not None:
            gaps.append((kind, delta))
        rows.append("<tr><td class='l'>%s</td><td class='l'>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td></tr>"
                    % (h(kind), h(names.get(kind, kind)), _mmss(mine_value),
                       _mmss(pro_value), "—" if delta is None else "%+.0f 秒" % delta))
    if not rows:
        return ""
    return ('<div class="card" style="margin-top:14px">'
            "<h3 style=\"margin:0 0 8px\">%s · 信息事件出现时刻对照（DAG 口径）</h3>"
            "<table class=\"data\"><thead><tr><th>type</th><th>含义</th>"
            "<th>我·首次中位</th><th>职业 T·首次中位</th><th>我 − 职业</th>"
            "</tr></thead><tbody>%s</tbody></table>"
            '<div class="note">这是「每局第一次出现该类信息事件」的中位时刻。'
            "和上面 bo 的时间线对着看：如果我这两项都更晚，基本能互证"
            "「出门更晚」；如果不一致，以 bo 的口径为准（它直接看单位位置，"
            "这里的视野几何有 ±20~45 秒误差）。</div></div>"
            % (h(matchup), "".join(rows)))


def _cross_check_card(mine, pro, entry, matchup):
    """bo 的时间线 vs DAG 侧的信息事件时刻：两个模块互相印证。"""
    mine_median = (entry or {}).get("bo_median") or {}
    pro_median = (entry or {}).get("bo_pro_median") or {}
    moveout_mine = mine_median.get("army_moveout")
    moveout_pro = pro_median.get("army_moveout")
    mine_times = first_event_times(mine)
    pro_times = first_event_times(pro)
    rows = []
    if moveout_mine is not None and moveout_pro is not None:
        rows.append(("bo：主力出门", moveout_mine, moveout_pro))
    for key, label in (("contact", "DAG：双方部队首次互见"),
                       ("army_seen", "DAG：首次看到对方成建制部队")):
        if mine_times.get(key) is not None and pro_times.get(key) is not None:
            rows.append((label, mine_times[key], pro_times[key]))
    if not rows:
        return ""
    body = []
    gaps = {}
    for label, mine_value, pro_value in rows:
        delta = mine_value - pro_value
        gaps[label] = delta
        body.append("<tr><td class='l'>%s</td><td>%s</td><td>%s</td>"
                    "<td>%s</td></tr>"
                    % (h(label), _mmss(mine_value), _mmss(pro_value),
                       "%+.0f 秒" % delta))
    verdict = ""
    moveout_gap = gaps.get("bo：主力出门")
    contact_gap = gaps.get("DAG：双方部队首次互见")
    army_gap = gaps.get("DAG：首次看到对方成建制部队")
    if moveout_gap is not None and army_gap is not None:
        if moveout_gap > 30 and army_gap > 30:
            verdict = ("<div class=\"note\" style=\"margin-top:6px\">"
                       "两个模块方向一致：bo 说我的主力出门晚 %.0f 秒，"
                       "DAG 侧「第一次看到对方成建制部队」也晚 %.0f 秒"
                       "（首次互见晚 %.0f 秒）。"
                       "——也就是说我不只是「出门晚」，是<b>更晚才和对方主力照面</b>；"
                       "和上面时间线里「建造/科技节点更早、却迟迟不压出去」的反差"
                       "是同一个故事的两面。</div>"
                       % (moveout_gap, army_gap, contact_gap or 0.0))
        elif moveout_gap > 30:
            verdict = ("<div class=\"note\" style=\"margin-top:6px\">"
                       "bo 说我的主力出门晚 %.0f 秒，但 DAG 侧的首次照面没有明显更晚——"
                       "说明晚出门之后我补得快，或者对面也没压过来。</div>" % moveout_gap)
        else:
            verdict = ("<div class=\"note\" style=\"margin-top:6px\">"
                       "bo 和 DAG 侧的时序差都在 %+.0f 秒以内，基本互洽。</div>"
                       % moveout_gap)
    return ('<div class="card" style="margin-top:14px">'
            "<h3 style=\"margin:0 0 8px\">%s · 两个模块互相印证</h3>"
            "<table class=\"data\"><thead><tr><th>指标</th><th>我</th>"
            "<th>职业 T</th><th>我 − 职业</th></tr></thead><tbody>%s</tbody></table>%s"
            "</div>" % (h(matchup), "".join(body), verdict))


def _matchup_summary_rows(groups_by_matchup, min_games=MIN_GROUP_GAMES):
    """三对位横向对比表的行。"""
    rows = []
    for matchup in groups_by_matchup.get("_order") or MATCHUP_ORDER:
        entry = groups_by_matchup.get(matchup)
        if not entry:
            continue
        mine = entry.get("mine")
        pro = entry.get("pro")
        mine_games = ((mine or {}).get("meta") or {}).get("games") or 0
        pro_games = ((pro or {}).get("meta") or {}).get("games") or 0
        mine_r = (mine or {}).get("reaction") or {}
        pro_r = (pro or {}).get("reaction") or {}
        mine_win = mine_r.get("by_window") or {}
        pro_win = pro_r.get("by_window") or {}
        concentration = opening_concentration(mine) if mine else None
        top_type = next(iter((mine_r.get("by_type") or {})), "—")
        rows.append(
            "<tr><td class='l'><b>%s</b></td><td>%d</td><td>%d</td>"
            "<td>%d / %d</td><td>%d</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td class='l'>%s</td><td class='l'>%s</td>"
            "<td>%s</td><td>%.2f</td></tr>"
            % (h(matchup), mine_games, pro_games,
               len((mine or {}).get("nodes") or []), len((mine or {}).get("edges") or []),
               mine_r.get("transitions", 0),
               _pct((mine_win.get("20") or {}).get("scout_share"), 1),
               _pct((mine_win.get("40") or {}).get("scout_share"), 1),
               _pct((mine_win.get("60") or {}).get("scout_share"), 1),
               _pct((pro_win.get("60") or {}).get("scout_share"), 1),
               h(top_type), h(((concentration or {}).get("top") or "—").split(":", 1)[-1]),
               _pct((concentration or {}).get("top_share"), 0),
               (concentration or {}).get("entropy") or 0.0))
    if not rows:
        return '<div class="note">没有可用对位。</div>'
    return ('<table class="data"><thead><tr><th>对位</th><th>我·局数</th>'
            "<th>职业T·局数</th><th>我·节点/边</th><th>我·转移</th>"
            "<th>我·侦察20s</th><th>我·侦察40s</th><th>我·侦察60s</th>"
            "<th>职业T·60s</th>"
            "<th>我主要靠的信息</th><th>我最常见开局</th><th>它的占比</th>"
            "<th>开局熵</th></tr></thead><tbody>" + "".join(rows)
            + "</tbody></table>")


def _ask_verdict(groups_by_matchup):
    """把三对位的信息差异写成一段人话。"""
    entries = []
    for matchup in groups_by_matchup.get("_order") or MATCHUP_ORDER:
        entry = groups_by_matchup.get(matchup) or {}
        mine = entry.get("mine")
        if not mine:
            continue
        reaction = mine.get("reaction") or {}
        by_type = reaction.get("by_type") or {}
        window = reaction.get("by_window") or {}
        entries.append({
            "matchup": matchup,
            "share20": (window.get("20") or {}).get("scout_share") or 0.0,
            "share60": (window.get("60") or {}).get("scout_share") or 0.0,
            "top_type": next(iter(by_type), None),
            "by_type": by_type,
        })
    if not entries:
        return ""
    pieces = []
    for item in entries:
        if item["top_type"]:
            pieces.append("%s 主要靠 <b>%s</b>（%d 次触发）"
                          % (item["matchup"], h(item["top_type"]),
                             item["by_type"].get(item["top_type"], 0)))
        else:
            pieces.append("%s 没有侦察触发的转移" % item["matchup"])
    body = ("三对位的信息来源不一样：" + "；".join(pieces) + "。")
    return body


def _matchup_block(matchup, entry, min_node=2, min_games=MIN_GROUP_GAMES, meta=None):
    """一个对位一节：我的 DAG + 同对位职业 T 对照 + 信息类型分布。"""
    mine = entry.get("mine")
    pro = entry.get("pro")
    pro_players = entry.get("pro_players") or {}
    parts = []
    mine_games = ((mine or {}).get("meta") or {}).get("games") or 0
    pro_games = ((pro or {}).get("meta") or {}).get("games") or 0

    if not mine or mine_games < min_games:
        parts.append('<div class="card"><div class="hint"><b>样本不足</b>：'
                     "我的 %s 只有 <b>%d</b> 局（门槛 %d 局），不画图。"
                     "</div></div>" % (h(matchup), mine_games, min_games))
    else:
        parts.append('<h3 style="margin:14px 0 6px">我的 %s DAG</h3>' % h(matchup))
        block, _layout = _dag_block(
            mine, "%s · 我的" % matchup,
            "节点 = (阶段, 段签名)；边 = 段之间的转移，条件分「到点」和「侦察到」。",
            min_node=min_node)
        parts.append(block)

    if pro and pro_games >= min_games:
        players = "、".join("%s %d 局" % (name, count)
                            for name, count in list(pro_players.items())[:6])
        parts.append('<h3 style="margin:18px 0 6px">同对位职业 Terran（对照）</h3>')
        block, _layout = _dag_block(
            pro, "%s · 同对位职业 Terran" % matchup,
            "同一个口径、同一套信息事件算法，职业 Terran 的同对位样本。"
            "选手构成：%s。" % players,
            min_node=min_node)
        parts.append(block)
    else:
        parts.append('<div class="card"><div class="hint"><b>职业对照样本不足</b>：'
                     "%s 只有 <b>%d</b> 局（门槛 %d 局），不出对照图。"
                     "</div></div>" % (h(matchup), pro_games, min_games))

    clusters = entry.get("bo_clusters") or []
    if clusters:
        items = "；".join("%s（%s）"
                          % (h(cluster.get("name") or "?"),
                             _pct(cluster.get("share"), 0)
                             if cluster.get("share") is not None
                             else "%d 局" % (cluster.get("games") or 0))
                          for cluster in clusters[:4])
        sufficient = entry.get("bo_pro_sufficient")
        pro_note = ""
        if sufficient is False:
            pro_note = ("；bo 这一版判定该对位的职业对照样本<b>不足</b>，"
                        "本报告的对照图已经按上面写的补样本处理")
        parts.append('<div class="note" style="margin-top:6px">bo 这一版对该对位的'
                     "开场聚类：%s%s。</div>" % (items, pro_note))

    if mine and pro:
        parts.append('<div class="card" style="margin-top:14px">'
                     "<h3 style=\"margin:0 0 8px\">%s · 我 vs 职业 T 的信息来源</h3>%s"
                     '<div class="answer" style="margin-top:10px">%s</div></div>'
                     % (h(matchup), _event_type_compare_table(mine, pro),
                        _top_info_line(mine.get("reaction") or {}, mine)))
        only_maru = set(pro_players) <= {"Maru"} if pro_players else False
        parts.append(_answer_box(mine, pro,
                                 pro_label="Maru" if only_maru else "职业 T"))
        parts.append(_bo_timeline_card(entry, meta.get("bo_meta"), matchup))
        parts.append(_event_timing_card(mine, pro, matchup))
        parts.append(_cross_check_card(mine, pro, entry, matchup))

    if mine and mine_games >= min_games:
        parts.append('<div class="split" style="margin-top:14px">')
        parts.append('<div class="card"><h3>我的 10 条转移</h3>%s</div>'
                     % _transitions_table(mine))
        parts.append('<div class="card"><h3>我的 10 条完整路径</h3>%s</div>'
                     % _paths_table(mine))
        parts.append("</div>")
    return "".join(parts)


def _caliber_block(matchup_infos, bo_meta, extra):
    """样本口径：这一版 bo 怎么筛的、每个对位多少局。"""
    bo_meta = bo_meta or {}
    parts = ['<div class="card" style="margin-top:12px">'
             '<h3 style="margin:0 0 8px">样本口径</h3><div class="hint">']
    version = bo_meta.get("version_filter") or {}
    table = bo_meta.get("matchup_table") or []
    if table:
        rows = []
        for row in table:
            dates = row.get("dates_used") or {}
            rows.append("<tr><td class='l'>%s</td><td>%s</td><td>%s</td>"
                        "<td>%s</td><td class='l'>%s ~ %s</td></tr>"
                        % (h(row.get("matchup") or "?"),
                           row.get("mine_games") or 0, row.get("pro_games") or 0,
                           "够" if row.get("pro_sufficient") else "不足",
                           h(str(dates.get("min") or "?")),
                           h(str(dates.get("max") or "?"))))
        parts.append("这一版 bo 的对位样本（我 / 该对位职业 T / 职业样本是否够 / "
                     "日期范围）：<table class=\"data\" style=\"margin-top:6px\">"
                     "<thead><tr><th>对位</th><th>我</th><th>职业 T</th>"
                     "<th>职业是否够</th><th>日期范围</th></tr></thead><tbody>"
                     + "".join(rows) + "</tbody></table>")
    else:
        pieces = []
        for matchup, info in (matchup_infos or {}).items():
            pieces.append("%s 我 %d 局 / 职业 T %d 局"
                          % (matchup, info.get("mine", 0), info.get("pro", 0)))
        parts.append("这一版的对位样本：" + "；".join(pieces) + "。")
    if version:
        parts.append("<br>筛选条件：本地人类 + 官方图 + ≥120 秒 + <b>%d 农民</b>开局"
                     "（8 农民版本因经济改动太大被整批排除 %d 局）。"
                     % (int(version.get("start_workers") or 12),
                        int(version.get("excluded_by_workers") or 0)))
    pace = bo_meta.get("pace_dimension") or {}
    if pace and pace.get("gas_p10") is not None:
        parts.append("<br>版本过滤后「快慢」维度基本消失：一气 p10–p90 %s–%s"
                     "（跨 %.0f 秒），二矿 %s–%s（跨 %.0f 秒）。"
                     % (_mmss(pace.get("gas_p10")), _mmss(pace.get("gas_p90")),
                        (pace.get("gas_p90") or 0) - (pace.get("gas_p10") or 0),
                        _mmss(pace.get("expand_p10")), _mmss(pace.get("expand_p90")),
                        (pace.get("expand_p90") or 0) - (pace.get("expand_p10") or 0)))
    source = extra.get("sample_source") or ""
    if source:
        parts.append("<br>" + h(source))
    parts.append("</div></div>")
    return "".join(parts)


# --------------------------------------------------------------------------- 主渲染

def render_report(matchups, appendix=None, meta=None, extra=None, min_node=2,
                  min_games=MIN_GROUP_GAMES):
    """三对位各一节 + 顶部横向对比 + Z/P 附注。"""
    meta = meta or {}
    extra = extra or {}
    appendix = appendix or {}
    order = [matchup for matchup in MATCHUP_ORDER if matchup in matchups]
    order += [matchup for matchup in matchups if matchup not in order]
    groups_by_matchup = dict(matchups)
    groups_by_matchup["_order"] = order

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>BO 转移图 · TvT / TvZ / TvP 分开看</title>",
             "<style>%s%s</style></head><body><div class=\"wrap\">" % (CSS, EXTRA_CSS)]
    parts.append('<header class="top"><h1>BO 转移图：三种对抗分开看（TvT / TvZ / TvP）</h1>')

    summary = []
    for matchup in order:
        entry = matchups.get(matchup) or {}
        mine = ((entry.get("mine") or {}).get("meta") or {}).get("games") or 0
        pro = ((entry.get("pro") or {}).get("meta") or {}).get("games") or 0
        summary.append("%s 我 %d / 职业 T %d" % (matchup, mine, pro))
    parts.append('<div class="sub">%s · 边 = 段之间的转移，条件分「到点」和「侦察到」'
                 " · 每个对位独立统计</div>" % h(" · ".join(summary)))
    parts.append("</div></header>")

    # 三对位结论
    parts.append('<div class="card" style="margin-bottom:14px"><div class="answer">'
                 "<b>结论：</b>%s</div>"
                 '<div class="note" style="margin-top:8px">'
                 "三对位的转移都主要靠「到点」，但信息类型不同；"
                 "看每节里的信息类型分布，别把一个对位的结论套到另一个对位。</div></div>"
                 % _ask_verdict(groups_by_matchup))

    parts.append('<h2>一、三对位横向对比</h2><div class="card">')
    parts.append(_matchup_summary_rows(groups_by_matchup, min_games=min_games))
    parts.append(_caliber_block(meta.get("matchup_infos") or {}, meta.get("bo_meta"), extra))
    parts.append("</div>")

    section_index = 2
    for matchup in order:
        entry = matchups.get(matchup) or {}
        heading = "二" if section_index == 2 else ("三" if section_index == 3 else "四")
        parts.append('<div style="margin-top:26px"><h2>%s、%s</h2>%s</div>'
                     % (heading, h(matchup),
                        _matchup_block(matchup, entry, min_node=min_node,
                                       min_games=min_games, meta=meta)))
        section_index += 1

    zerg = appendix.get("zerg")
    protoss = appendix.get("protoss")
    if zerg or protoss:
        parts.append('<div style="margin-top:26px"><h2>附注：职业 Z/P 的开局选择面</h2>'
                     '<div class="card"><div class="hint">'
                     "这两张图是<b>对手在做什么</b>（职业 Z/P 选手的开局选择），"
                     "不是你的对位分析；只看 opening/early，不做侦察归因。"
                     "</div></div></div>")
        if zerg:
            block, _layout = _dag_block(
                zerg, "职业 Zerg 的开局", "只看 opening/early；样本小，边阈值放宽到 1 次。",
                min_node=min_node, paths_limit=3, show_reaction=False,
                extra_note="选手：" + "、".join(
                    "%s %d 局" % (name, count)
                    for name, count in (extra.get("zerg_players") or {}).items()))
            parts.append(block)
        if protoss:
            block, _layout = _dag_block(
                protoss, "职业 Protoss 的开局",
                "只看 opening/early；样本小，边阈值放宽到 1 次。",
                min_node=min_node, paths_limit=3, show_reaction=False,
                extra_note="选手：" + "、".join(
                    "%s %d 局" % (name, count)
                    for name, count in (extra.get("protoss_players") or {}).items()))
            parts.append(block)

    parts.append(_limits_block())

    precision = (meta.get("precision") or {})
    parts.append('<h2>数据质量</h2><div class="card"><div class="hint">%s'
                 "<br>各对位的事件数见每节里的信息类型表；位置采样可信阈值 %.0f 秒。"
                 "</div></div>"
                 % (h(precision.get("reading") or ""),
                    float(meta.get("position_trust_gap") or 0)))

    if extra.get("missing"):
        parts.append('<div class="card" style="margin-top:14px"><div class="hint">'
                     '<b>没算成的局</b>：%s</div></div>'
                     % h("；".join("%s（%s）" % (item["file"], item["reason"])
                                  for item in extra["missing"][:6])))

    parts.append('<footer>由本地 replay 解析 + 视野几何重建，不读游戏内存。'
                 "信息事件为几何推断，非真值。生成 %s · bo 来源 %s</footer>"
                 % (h(meta.get("generated") or ""), h(meta.get("bo") or "reports/bo.json")))
    parts.append("</div></body></html>")
    return "".join(parts)


def build_json(matchups, appendix, meta=None, min_games=MIN_GROUP_GAMES):
    """JSON：顶层保留契约第三节的 TvZ 我的 DAG（向后兼容），
    三个对位完整放在 matchups，Z/P 放 appendix。"""
    order = [matchup for matchup in MATCHUP_ORDER if matchup in matchups]
    order += [matchup for matchup in matchups if matchup not in order]
    payload = {}
    # 向后兼容：顶层 = 第一个对位（默认 TvT）的"我的 DAG"，契约第三节结构
    primary = "TvZ" if "TvZ" in order else (order[0] if order else None)
    if primary and (matchups.get(primary) or {}).get("mine"):
        payload.update(matchups[primary]["mine"])
    payload["meta"] = dict(payload.get("meta") or {})
    payload["meta"].update({
        "matchups": order,
        "primary_matchup": primary,
        "min_games_guard": min_games,
        "contract": ("顶层是 primary_matchup 的「我的 DAG」（契约第三节）；"
                     "三对位的完整数据在 matchups.<TvT|TvZ|TvP>.{mine,pro}，"
                     "每个都是契约第三节的 DAG 对象。"),
        "generated": (meta or {}).get("generated"),
        "bo_source": (meta or {}).get("bo"),
    })
    payload["matchups"] = {
        matchup: {"mine": (matchups.get(matchup) or {}).get("mine"),
                  "pro": (matchups.get(matchup) or {}).get("pro"),
                  "compare": {
                      "games": [((matchups.get(matchup) or {}).get("mine", {}).get("meta") or {}).get("games"),
                                ((matchups.get(matchup) or {}).get("pro", {}) or {}).get("meta", {}).get("games")],
                      "pro_players": (matchups.get(matchup) or {}).get("pro_players") or {},
                      "bo_clusters": (matchups.get(matchup) or {}).get("bo_clusters") or [],
                      "bo_pro_sufficient": (matchups.get(matchup) or {}).get("bo_pro_sufficient"),
                      "bo_mine_games": (matchups.get(matchup) or {}).get("bo_mine_games"),
                      "bo_pro_games": (matchups.get(matchup) or {}).get("bo_pro_games"),
                      "opening_concentration": {
                          "mine": opening_concentration((matchups.get(matchup) or {}).get("mine"))
                          if (matchups.get(matchup) or {}).get("mine") else None,
                          "pro": opening_concentration((matchups.get(matchup) or {}).get("pro"))
                          if (matchups.get(matchup) or {}).get("pro") else None,
                      },
                  }}
        for matchup in order}
    appendix_out = {}
    for key in ("zerg", "protoss"):
        if appendix.get(key):
            entry = dict(appendix[key])
            entry["opening_concentration"] = opening_concentration(appendix[key])
            appendix_out[key] = entry
    if appendix_out:
        payload["appendix"] = appendix_out
    payload["generator"] = {
        "tool": "replay_analyzer.dag_report",
        "generated": (meta or {}).get("generated"),
        "bo_source": (meta or {}).get("bo"),
        "limits": ["info events are geometric inference, not ground truth",
                   "position sampling starts around 120-270s and is sampled ~every 45s",
                   "terrain occlusion is not modelled",
                   "scan / cloak / burrow are not modelled",
                   "node signature = bo segment token chain, coarsened",
                   "army_seen / contact / scout_arrive thresholds are matchup-agnostic "
                   "(>=5 units and >=500 value for army_seen); the per-matchup event-type "
                   "table shows where that bites",
                   "zerg/protoss appendix covers opening+early only"],
    }
    return payload


def write_report(html_text, path):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(html_text)
    return path


def write_json(payload, path):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    return path


# --------------------------------------------------------------------------- 入口

def _default_bo():
    return os.path.join("reports", "bo.json")


MINE_DIR = (r"C:\Users\sun\Documents\StarCraft II\Accounts\556922532"
            r"\5-S2-1-8354391\Replays\Multiplayer")


def run(bo_path=None, matchups=("TvT", "TvZ", "TvP"), mine_limit=100,
        pro_limit=100, pro_name="Maru", min_edge=2, min_node=2,
        min_games=MIN_GROUP_GAMES, out_path=None, json_path=None,
        cache_dir=DEFAULT_CACHE, force=False, verbose=True, extra_bo=(),
        race_dir="replays/pro", skip_appendix=False, mine_dir=None):
    """跑完整条链路：bo.json（+ 我的对位补样本 + 职业 replay）-> 每个对位一组 DAG。"""
    bo_path = bo_path or _default_bo()
    if not os.path.isfile(bo_path):
        raise SystemExit("找不到 %s。先跑 bo 的提取脚本产出 bo.json。" % bo_path)
    games, bo_meta = load_bo(bo_path)
    if verbose:
        print("[bo] %s -> %d games" % (bo_path, len(games)), flush=True)
    fixtures = []
    for extra_path in extra_bo or ():
        if not os.path.isfile(extra_path):
            print("[warn] 找不到额外 BO：%s" % extra_path, flush=True)
            continue
        loaded, _meta = load_bo(extra_path)
        fixtures.extend(loaded)
        if verbose:
            print("[bo+] %s -> %d games" % (extra_path, len(loaded)), flush=True)

    mine_from_bo = [game for game in games
                    if (game.get("player") or {}).get("is_local") is True]
    pro_from_bo = [game for game in games
                   if (game.get("player") or {}).get("is_local") is not True]
    by_matchup_mine = {matchup: group_games(mine_from_bo, matchup, "mine", mine_limit)
                       for matchup in matchups}
    missing_mine = [matchup for matchup in matchups
                    if not by_matchup_mine.get(matchup)]
    sample_notes = []
    if missing_mine and not any(True for _ in ()):
        if verbose:
            print("[mine-bo] bo.json 缺对位 %s，尝试从我的 replay 目录补"
                  % ",".join(missing_mine), flush=True)
        filled = extract_mine_matchups(mine_dir or MINE_DIR, missing_mine,
                                       rebuild=force, verbose=verbose)
        for matchup in missing_mine:
            by_matchup_mine[matchup] = filled.get(matchup) or []
        sample_notes.append("bo.json 里缺 %s，我从本地 replay 目录按同一套过滤补的"
                            % "、".join(missing_mine))

    primary_pro = list(pro_from_bo) + list(fixtures)
    if verbose:
        print("[pro] bo.json 职业 %d 局 + fixtures %d 局"
              % (len(pro_from_bo), len(fixtures)), flush=True)
    need_more_pro = any(len(group_games(primary_pro, matchup, "pro", 10000)) < min_games
                        for matchup in matchups)
    pro_corpus = []
    if need_more_pro or not skip_appendix:
        pro_corpus = extract_pro_bos(root=race_dir, rebuild=force, verbose=verbose)

    # 每个对位单独选对照样本：先只用 Maru（在海量数据下更干净），
    # 该对位不够 min_games 局才用其他 Terran 职业选手的同对位录像补。
    pro_by_matchup = {}
    supplement_notes = []
    for matchup in matchups:
        primary = group_games(primary_pro, matchup, "pro", pro_limit)
        if len(primary) >= min_games:
            pro_by_matchup[matchup] = primary
            continue
        known = {game_path(game) for game in primary}
        extras = [game for game in pro_corpus
                  if game_path(game) not in known
                  and ((game.get("game") or {}).get("matchup") or "") == matchup
                  and ((game.get("player") or {}).get("is_local") is not True)]
        pro_by_matchup[matchup] = (primary + extras)[:max(pro_limit, min_games)]
        supplement_notes.append("%s 的 %s 只有 %d 局，用其他 Terran 职业选手同对位补到 %d 局"
                                % (pro_name or "Maru",
                                   matchup, len(primary), len(pro_by_matchup[matchup])))
        if verbose:
            print("[pro] %s: Maru %d 局不够，补 %d 局其他 T 选手"
                  % (matchup, len(primary), len(pro_by_matchup[matchup]) - len(primary)),
                  flush=True)
    if supplement_notes:
        sample_notes.extend(supplement_notes)

    appendix = {}
    if not skip_appendix and pro_corpus:
        for race, key in (("Z", "zerg"), ("P", "protoss")):
            race_games = [game for game in pro_corpus
                          if ((game.get("player") or {}).get("race") or "").upper() == race]
            if not race_games:
                continue
            dag = dagmod.build_dag(race_games, min_edge=1, phases=("opening", "early"))
            appendix[key] = dag

    # ---- 每局信息事件（三对位 + 附录共用一套缓存）
    event_cache = {}
    group_games_map = {}
    for matchup in matchups:
        mine_games = by_matchup_mine.get(matchup) or []
        pro_games = pro_by_matchup.get(matchup) or []
        group_games_map[matchup] = {"mine": mine_games, "pro": pro_games}
    appendix_games = {}
    for key, dag in appendix.items():
        appendix_games[key] = []

    all_stats = {}
    for matchup, pair in group_games_map.items():
        events, stats = compute_info_events(pair["mine"], cache_dir, force, verbose)
        pair["mine_events"] = events
        pair["mine_stats"] = stats
        events_pro, stats_pro = compute_info_events(pair["pro"], cache_dir, force, verbose)
        pair["pro_events"] = events_pro
        pair["pro_stats"] = stats_pro
        all_stats[matchup] = (stats, stats_pro)

    sampling_values = []
    for stats_pair in all_stats.values():
        for stats in stats_pair:
            sampling_values.extend(stats.get("sampling_values") or [])
    sampling_values.sort()
    sampling = None
    if sampling_values:
        sampling = {"min": sampling_values[0],
                    "median": sampling_values[len(sampling_values) // 2]}
    if verbose and sampling:
        print("[data] position sampling starts at %.0fs (earliest %.0fs)"
              % (sampling["median"], sampling["min"]), flush=True)

    results = {}
    for matchup in matchups:
        pair = group_games_map[matchup]
        mine_dag = dagmod.build_dag(pair["mine"], min_edge=min_edge,
                                    info_events=pair["mine_events"],
                                    sampling=sampling) if pair["mine"] else None
        pro_dag = dagmod.build_dag(pair["pro"], min_edge=min_edge,
                                   info_events=pair["pro_events"],
                                   sampling=sampling) if pair["pro"] else None
        detail = (bo_meta.get("matchups_detail") or {}).get(matchup) or {}
        clusters = []
        for cluster in (detail.get("clusters") or []):
            clusters.append({"name": cluster.get("name") or "",
                             "games": cluster.get("games") or 0,
                             "share": cluster.get("share")})
        results[matchup] = {
            "mine": mine_dag,
            "pro": pro_dag,
            "pro_players": player_counts(pair["pro"]),
            "bo_median": detail.get("median") or {},
            "bo_pro_median": detail.get("pro_median") or {},
            "bo_clusters": clusters,
            "bo_pro_sufficient": detail.get("pro_sufficient"),
            "bo_mine_games": detail.get("mine_games"),
            "bo_pro_games": detail.get("pro_games"),
        }

    # 附录里 Z/P 也要信息事件（只用于显示事件数；不参与归因）
    for key, dag in appendix.items():
        race = "Z" if key == "zerg" else "P"
        race_games = [game for game in pro_corpus
                      if ((game.get("player") or {}).get("race") or "").upper() == race]
        events, _stats = compute_info_events(race_games, cache_dir, force, False)
        rebuilt = dagmod.build_dag(race_games, min_edge=1, info_events=events,
                                   sampling=sampling, phases=("opening", "early"))
        appendix[key] = rebuilt

    generated = dagmod.datetime.date.today().isoformat()
    matchup_infos = {matchup: {
        "mine": ((results[matchup]["mine"] or {}).get("meta") or {}).get("games") or 0,
        "pro": ((results[matchup]["pro"] or {}).get("meta") or {}).get("games") or 0,
    } for matchup in matchups}
    missing = []
    for stats_pair in all_stats.values():
        for stats in stats_pair:
            missing.extend(stats.get("missing") or [])
    precision_holder = None
    for matchup in matchups:
        mine_dag = results[matchup]["mine"]
        if mine_dag:
            precision_holder = mine_dag.get("precision_note")
            break
    extra = {
        "missing": missing,
        "zerg_players": player_counts([game for game in pro_corpus
                                       if ((game.get("player") or {}).get("race") or "").upper() == "Z"]),
        "protoss_players": player_counts([game for game in pro_corpus
                                          if ((game.get("player") or {}).get("race") or "").upper() == "P"]),
        "sample_source": "；".join(sample_notes),
    }
    meta = {"generated": generated, "bo": bo_path,
            "matchup_infos": matchup_infos,
            "bo_meta": bo_meta,
            "precision": precision_holder or {},
            "position_trust_gap": dagmod.POS_TRUST_GAP,
            "pro_name": pro_name}
    html_text = render_report(results, appendix, meta, extra, min_node=min_node,
                              min_games=min_games)
    payload = build_json(results, appendix, meta, min_games=min_games)

    out_path = out_path or os.path.join("reports", "dag.html")
    json_path = json_path or os.path.join("reports", "dag.json")
    write_report(html_text, out_path)
    write_json(payload, json_path)
    if verbose:
        print("[write] %s (%.1f KB)" % (out_path, os.path.getsize(out_path) / 1024.0),
              flush=True)
        print("[write] %s (%.1f KB)" % (json_path, os.path.getsize(json_path) / 1024.0),
              flush=True)
        for matchup, entry in results.items():
            for label in ("mine", "pro"):
                dag = entry.get(label)
                if not dag or ((dag.get("meta") or {}).get("games") or 0) < min_games:
                    continue
                layout = layout_dag(dag, min_node=min_node)
                problems = check_layout(layout)
                print("[check] %-4s %-4s nodes=%d edges=%d layout=%s"
                      % (matchup, label, len(dag["nodes"]), len(dag["edges"]),
                         "OK" if not problems else "%d problems" % len(problems)),
                      flush=True)
    return {"matchups": results, "appendix": appendix,
            "html": out_path, "json": json_path}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="BO 转移 DAG（按对位分开）+ 侦察触发标注（离线 HTML/JSON）")
    parser.add_argument("--bo", default=None, help="bo.py 输出的 reports/bo.json")
    parser.add_argument("--matchups", default="TvT,TvZ,TvP",
                        help="要出图的对位，逗号分隔（默认三个都做）")
    parser.add_argument("--mine", type=int, default=100, help="每对位我的局数上限")
    parser.add_argument("--pro", type=int, default=100, help="每对位职业 T 局数上限")
    parser.add_argument("--pro-name", default="Maru", help="首选职业对照选手")
    parser.add_argument("--min-edge", type=int, default=2)
    parser.add_argument("--min-node", type=int, default=2)
    parser.add_argument("--min-games", type=int, default=MIN_GROUP_GAMES,
                        help="某组少于这么多局就不画图")
    parser.add_argument("--out", default=None, help="HTML 输出路径")
    parser.add_argument("--json", dest="json_path", default=None, help="JSON 输出路径")
    parser.add_argument("--extra-bo", nargs="*", default=(),
                        help="额外的 GameBO 文件（测试用）")
    parser.add_argument("--race-dir", default="replays/pro",
                        help="职业录像根目录（抽 Z/P 附录用）")
    parser.add_argument("--skip-appendix", action="store_true",
                        help="不出 Z/P 附录")
    parser.add_argument("--mine-dir", default=None,
                        help="我的 replay 目录（bo.json 缺对位时补样本用）")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE)
    parser.add_argument("--force", action="store_true", help="忽略所有缓存重算")
    args = parser.parse_args(argv)
    chosen = tuple(item.strip() for item in (args.matchups or "").split(",")
                   if item.strip())
    run(bo_path=args.bo, matchups=chosen, mine_limit=args.mine,
        pro_limit=args.pro, pro_name=args.pro_name, min_edge=args.min_edge,
        min_node=args.min_node, min_games=args.min_games,
        out_path=args.out, json_path=args.json_path, cache_dir=args.cache_dir,
        force=args.force, extra_bo=tuple(args.extra_bo or ()),
        race_dir=args.race_dir, skip_appendix=args.skip_appendix,
        mine_dir=args.mine_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
