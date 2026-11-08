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
    return games, meta


def game_file(game):
    return dagmod._game_file(game)


def game_path(game):
    return ((game.get("game") or {}).get("path") or "").strip()


def select_games(games, matchup=None, who=None, limit=None, race=None):
    """挑对局。

    who="mine" 只挑本地玩家；who="<选手名>" 挑指定职业选手；race="Z"/"P" 挑职业选手种族。
    这三个条件是"与"关系；matchup 给了就按被分析玩家视角的对局种族过滤。
    """
    picked = []
    for game in games:
        meta = game.get("game") or {}
        player = game.get("player") or {}
        if matchup and (meta.get("matchup") or "").lower() != matchup.lower():
            continue
        if race and (player.get("race") or "").upper() != race.upper():
            continue
        if who == "mine" and player.get("is_local") is not True:
            continue
        if who and who not in ("mine",):
            name = (player.get("name") or "").lower()
            if name != who.lower():
                continue
        picked.append(game)
    picked.sort(key=lambda game: ((game.get("game") or {}).get("file") or ""))
    if limit:
        picked = picked[:int(limit)]
    return picked


def pro_games_by_race(games, limit=None):
    """{race: [GameBO]}：职业选手按种族分组（不含本地玩家）。"""
    out = {}
    for game in games:
        player = game.get("player") or {}
        if player.get("is_local") is True:
            continue
        race = (player.get("race") or "").upper()
        if not race:
            continue
        out.setdefault(race, []).append(game)
    for race in out:
        out[race].sort(key=lambda game: ((game.get("game") or {}).get("file") or ""))
        if limit:
            out[race] = out[race][:int(limit)]
    return out


def matchup_counts(games):
    """{matchup: 局数}，用来说明职业 Z/P 样本覆盖了哪些对局。"""
    counts = {}
    for game in games:
        matchup = ((game.get("game") or {}).get("matchup") or "?")
        counts[matchup] = counts.get(matchup, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def _matchup_note(zerg_games, protoss_games):
    pieces = []
    for label, games in (("Z", zerg_games), ("P", protoss_games)):
        counts = matchup_counts(games)
        if not counts:
            continue
        pieces.append("%s 组：" % label + " / ".join(
            "%s %d 局" % (key, value) for key, value in counts.items()))
    if not pieces:
        return ""
    return ("职业 Z/P 样本覆盖的对局种族：" + "；".join(pieces) +
            "。所以这两张图是「这些选手在各种对局里的开局选择」，"
            "不是只针对 TvZ 的应对。")


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


def extract_race_bos(root="replays/pro", races=("Z", "P"),
                     cache_path=RACE_BO_CACHE, rebuild=False, verbose=True):
    """用 bo 的公开接口把职业 Z/P 的 GameBO 抽出来（带缓存）。

    只扫"这个目录属于 Z/P 选手"的目录（T 选手直接跳过，不浪费解析时间）。
    缓存指纹 = 所有 replay 的 (路径, 大小, mtime)，replay 变了会自动重抽。
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
                    print("[race-bo] cache hit -> %d games (%s)"
                          % (len(cached.get("games") or []), cache_path), flush=True)
                return cached.get("games") or []
        except (OSError, ValueError):
            pass

    try:
        from . import bo as bo_module
    except Exception as error:  # bo 还没落地也不该把 dag 报告搞挂
        print("[warn] 无法 import bo.py（%s），跳过职业 Z/P 抽取" % error, flush=True)
        return []

    games = []
    total = len(files)
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
            print("[warn] race-bo 失败 %s (%s)" % (os.path.basename(path),
                                                   type(error).__name__), flush=True)
            continue
        player = game.get("player") or {}
        race = (player.get("race") or "").upper()
        if race not in races:
            continue
        player["name"] = _clean_player_name(player.get("name"))
        game["group"] = "pro-%s" % race.lower()
        games.append(game)
        if verbose:
            print("  [race-bo %d/%d] %-52s %s %-16s segs=%d"
                  % (index, total, os.path.basename(path)[:52], race,
                     game.get("signature"), len(game.get("segments") or [])),
                  flush=True)
    payload = {"meta": {"fingerprint": fingerprint,
                        "generated": dagmod.datetime.date.today().isoformat(),
                        "root": root, "games": len(games)},
               "games": games}
    try:
        directory = os.path.dirname(os.path.abspath(cache_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
    except OSError:
        pass
    if verbose:
        print("[race-bo] extracted %d games in %.0fs -> %s"
              % (len(games), time.time() - started, cache_path), flush=True)
    return games


def player_counts(games):
    """{选手名: 局数}，报告里要写清每个选手几局。"""
    counts = {}
    for game in games:
        name = ((game.get("player") or {}).get("name") or "?")
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


# --------------------------------------------------------------------------- 信息事件（带缓存）

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


def _phase_table(dag):
    reaction = dag.get("reaction") or {}
    by_phase = reaction.get("by_phase") or {}
    rows = []
    for phase in PHASE_ORDER:
        bucket = by_phase.get(phase)
        if not bucket:
            continue
        rows.append("<tr><td class='l'>%s</td><td>%d</td><td>%d</td>"
                    "<td>%s</td><td>%s</td></tr>"
                    % (h(PHASE_LABELS.get(phase, phase)), bucket["transitions"],
                       bucket.get("scout", 0), _pct(bucket.get("scout_share"), 1),
                       _pct(bucket.get("time_share"), 1)))
    if not rows:
        return '<div class="note">没有分段。</div>'
    return ('<table class="data"><thead><tr><th>阶段（按起点段）</th><th>转移次数</th>'
            "<th>侦察触发</th><th>侦察占比</th><th>到点占比</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>")


def _event_type_table(dag):
    stats = (dag.get("precision_note") or {}).get("event_types") or {}
    names = {"scout_arrive": "侦察抵达（看到对方主基地）",
             "contact": "双方部队首次互相可见",
             "army_seen": "看到对方成建制部队",
             "tech_seen": "看到对方科技建筑"}
    if not stats:
        return '<div class="note">这组没有算出任何信息事件。</div>'
    total = sum(stats.values()) or 1
    rows = []
    for kind, count in sorted(stats.items(), key=lambda item: -item[1]):
        rows.append("<tr><td class='l'>%s</td><td class='l'>%s</td><td>%d</td>"
                    "<td>%.1f</td></tr>"
                    % (h(kind), h(names.get(kind, kind)), count,
                       count / float((dag["meta"].get("games") or 1))))
    return ('<table class="data"><thead><tr><th>type</th><th>含义</th><th>次数</th>'
            "<th>每局平均</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


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


def _node_table(dag):
    nodes = dag.get("nodes") or []
    if not nodes:
        return '<div class="note">没有节点。</div>'
    rows = []
    for node in nodes:
        notes = []
        if node.get("raw_signatures"):
            notes.append("原值: " + "、".join(node["raw_signatures"]))
        if node.get("unknown_tokens"):
            notes.append("陌生 token: " + "、".join(node["unknown_tokens"]))
        segments = node.get("segment_signatures") or {}
        if segments and (node.get("signature") in ("plain", "unknown")
                         or len(segments) > 1):
            notes.append("bo 原签名: " + " / ".join(
                "%s×%d" % (key, value) for key, value in list(segments.items())[:3]))
        signatures = node.get("game_signatures") or {}
        if signatures:
            notes.append("整局签名: " + " / ".join(
                "%s×%d" % (key, value) for key, value in list(signatures.items())[:3]))
        rows.append("<tr><td class='l'>%s</td><td class='l'>%s</td><td>%d</td>"
                    "<td>%s</td><td>%s</td><td class='l'>%s</td></tr>"
                    % (h(node["id"]), h(node["signature"]), node["games"],
                       _pct(node["share"], 1), _mmss(node.get("median_anchor")),
                       h("；".join(notes))))
    return ('<table class="data"><thead><tr><th>节点</th><th>段签名</th><th>局数</th>'
            "<th>占样本</th><th>中位进入</th><th>备注</th></tr></thead><tbody>"
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


def _dispersion_table(groups):
    """四组"开局选择面"对比：只看第一个段（opening）的分布。"""
    rows = []
    labels = [("mine", "我的 TvZ"), ("maru", "Maru（职业 T）"),
              ("zerg", "职业 Zerg"), ("protoss", "职业 Protoss")]
    values = {}
    for key, label in labels:
        dag = groups.get(key)
        if not dag:
            continue
        concentration = opening_concentration(dag)
        if not concentration:
            continue
        values[key] = concentration
        rows.append(
            "<tr><td class='l'>%s</td><td>%d</td><td>%d</td>"
            "<td class='l'>%s</td><td>%s</td><td>%.2f</td></tr>"
            % (h(label), concentration["games"], concentration["opening_nodes"],
               h(concentration["top"].split(":", 1)[-1]),
               _pct(concentration["top_share"], 0), concentration["entropy"]))
    if not rows:
        return "", {}
    table = ('<table class="data"><thead><tr><th>组</th><th>局数</th><th>开局节点数</th>'
             "<th>最常见的开局</th><th>它的占比</th><th>归一化熵（越大越分散）</th>"
             "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
    return table, values


def _dispersion_verdict(values):
    """回答"职业 Z/P 的开局是不是比我更分散"。"""
    mine = values.get("mine") or {}
    if not mine:
        return ""
    lines = []
    for key, label in (("maru", "Maru"), ("zerg", "职业 Zerg"),
                       ("protoss", "职业 Protoss")):
        concentration = values.get(key)
        if not concentration:
            continue
        diff = mine["top_share"] - concentration["top_share"]
        if diff > 0.15:
            relation = "比<b>%s</b>集中 %.0f 个百分点" % (label, diff * 100)
        elif diff < -0.15:
            relation = "比<b>%s</b>分散 %.0f 个百分点" % (label, abs(diff) * 100)
        else:
            relation = "和<b>%s</b>差不多（差 %.0f 个百分点）" % (label, abs(diff) * 100)
        lines.append("%s：你这边最粗的开局占 %s，%s 最粗的 %s 占 %s"
                     % (relation, _pct(mine["top_share"], 0), label,
                        concentration["top"].split(":", 1)[-1],
                        _pct(concentration["top_share"], 0)))
    summary = ""
    zerg = values.get("zerg") or {}
    protoss = values.get("protoss") or {}
    if zerg and protoss:
        # "职业不比我分散" = 他们最粗的那套开局占比都不比我低（差 5 个点以内算同档）
        if zerg.get("top_share", 0) >= mine["top_share"] - 0.05 \
                and protoss.get("top_share", 0) >= mine["top_share"] - 0.05:
            summary = ("<br><b>直接回答：不是。</b>本批职业 Z/P 的开局选择面并没有比你的宽——"
                       "Z 最常见的一套占 %s（你 %s），P 更是 %s 都走同一套（你 %s）。"
                       "真正比你分散的是 Maru（同为 T，最常见一套只占 %s）。"
                       "换句话说，「职业选手开局更花」在这批样本里不成立，"
                       "他们更多是把同一套开局的细节做扎实。"
                       % (_pct(zerg.get("top_share"), 0), _pct(mine["top_share"], 0),
                          _pct(protoss.get("top_share"), 0), _pct(mine["top_share"], 0),
                          _pct((values.get("maru") or {}).get("top_share"), 0)))
        else:
            summary = ("<br>直接回答：职业 Z/P 里至少一组的开局比你的更分散，"
                       "具体看图上的占比和熵。")
    return ('<div class="card"><div class="answer">开局集中度：'
            + "；".join(lines) + "。" + summary + "</div>"
            '<div class="note" style="margin-top:8px">'
            "注意三件事：① 熵对样本量敏感（我 59 局、Maru 17 局、职业 Z/P 各 25 局），"
            "小样本会低估分散度，看趋势别抠小数；② 职业 Z/P 那两张图回答的是"
            "<b>「职业选手在做什么选择」</b>，不是「对手会怎么针对我」——"
            "后者要按对手单独分析，不是这个样本能回答的；"
            "③ 职业组的开局画像只覆盖 opening/early，后期不追。</div></div>")


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


def _answer_box(mine, pro):
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
        window_line = ("20 秒口径：我 %s vs Maru %s。"
                       % (_ci_text(mine_count, mine_total),
                          _ci_text(pro_count, pro_total)))
        if overlap:
            compare = ("<br><b>和 Maru 比：样本不足以区分。</b>" + window_line +
                       " 两边的 95%% 区间是重叠的——Maru 那组 %d 次转移里 %d 次，"
                       "上界就有 %.0f%%，和我的点估计差不多。"
                       "别读成「我比他更爱（或更不爱）被动反应」，只能说这个样本量下"
                       "两组分不开（他只有 %d 局）。"
                       % (pro_total, pro_count, pro_high * 100, pro_games))
        else:
            direction = "高" if mine_count / float(mine_total) >= pro_count / float(pro_total) else "低"
            compare = ("<br>和 Maru 比，我被侦察推着走的比例%s一些，" % direction
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
                    "Maru 只有 %d 局，比例波动大。" % pro_games)
    elif pro is not None:
        compare = "Maru 那一组没算出转移，无法对比。"
    return ('<div class="card"><div class="answer">%s%s</div>'
            '<div class="note" style="margin-top:8px">注意：这里的「侦察触发」只是'
            "<b>相关性</b>——事件和转移落在同一个 20 秒窗口里，不等于因果。"
            "而且事件本身是几何推断，时间精度中位就有 ±20 秒，和窗口同量级。"
            "更要紧的是：bo 的段边界本来就按时间切（0/180/360/600），"
            "「到点」这个结论有一半是分段方式自带的。</div></div>"
            % (_reaction_answer(mine_r), compare))


def _compare_table(mine, pro):
    def row(name, mine_value, pro_value):
        return ("<tr><td class='l'>%s</td><td>%s</td><td>%s</td></tr>"
                % (h(name), h(str(mine_value)), h(str(pro_value))))

    if not pro:
        return ""
    mine_meta = mine["meta"]
    pro_meta = pro["meta"]
    mine_r = mine.get("reaction") or {}
    pro_r = pro.get("reaction") or {}
    rows = [
        row("样本局数", mine_meta.get("games"), pro_meta.get("games")),
        row("节点数", len(mine["nodes"]), len(pro["nodes"])),
        row("边数（≥阈值）", len(mine["edges"]), len(pro["edges"])),
        row("转移次数", mine_r.get("transitions"), pro_r.get("transitions")),
        row("侦察触发占比", _pct(mine_r.get("scout_share"), 1),
            _pct(pro_r.get("scout_share"), 1)),
        row("纯到点占比", _pct(mine_r.get("time_share"), 1),
            _pct(pro_r.get("time_share"), 1)),
        row("最常见转移",
            (mine["edges"][0]["from"] + " → " + mine["edges"][0]["to"]
             + " ×%d" % mine["edges"][0]["count"]) if mine["edges"] else "—",
            (pro["edges"][0]["from"] + " → " + pro["edges"][0]["to"]
             + " ×%d" % pro["edges"][0]["count"]) if pro["edges"] else "—"),
    ]
    return ('<table class="data"><thead><tr><th>指标</th><th>我的 TvZ</th>'
            "<th>Maru 的 TvZ</th></tr></thead><tbody>" + "".join(rows)
            + "</tbody></table>")


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
<li><b>边条件按多数决。</b>一条边只要有几次转移落在信息事件窗口里，就可能被标成
<code>scout:</code>；完整分布见 <code>reports/dag.json</code> 里每条边的
<code>conditions</code> 和 <code>conditions_by_window</code> 字段。</li>
</ol>
"""


def _caliber_block(mine, pro, groups, bo_meta, extra):
    """写清这一版 bo 的样本口径（59 局是怎么筛出来的、快慢维度去哪了）。"""
    bo_meta = bo_meta or {}
    parts = ['<div class="card" style="margin-top:12px">'
             '<h3 style="margin:0 0 8px">样本口径（这一版 bo 的筛选条件）</h3>'
             '<div class="hint">']
    version = bo_meta.get("version_filter") or {}
    pace = bo_meta.get("pace_dimension") or {}
    clustering = bo_meta.get("clustering") or {}
    if version:
        parts.append("我的样本 %d 局，是「TvZ + 本地 + 人类 + 官方图 + ≥120 秒 + "
                     "<b>%d 农民</b>开局」筛出来的；因开局农民版本被打掉的局数："
                     "<b>%d 局</b>（8 农民版本的经济改动太大，和现在不是一个游戏）。"
                     % (mine["meta"].get("games") or 0,
                        int(version.get("start_workers") or 12),
                        int(version.get("excluded_by_workers") or 0)))
    if pace:
        parts.append("<br>版本过滤之后「快慢」这个维度基本消失了：一气 p10–p90 = "
                     "%s–%s（跨 %s 秒）、二矿 p10–p90 = %s–%s（跨 %s 秒）。"
                     "所以 DAG 里凡是体现「我打得快/慢」的节点差别，现在应该被压扁。"
                     % (_mmss(pace.get("gas_p10")), _mmss(pace.get("gas_p90")),
                        ("%.0f" % (pace.get("gas_p90") or 0)
                         if pace.get("gas_p10") is None
                         else "%.0f" % ((pace.get("gas_p90") or 0)
                                        - (pace.get("gas_p10") or 0))),
                        _mmss(pace.get("expand_p10")), _mmss(pace.get("expand_p90")),
                        ("%.0f" % ((pace.get("expand_p90") or 0)
                                   - (pace.get("expand_p10") or 0)))))
    clusters = bo_meta.get("clusters") or {}
    mine_clusters = clusters.get("mine") if isinstance(clusters, dict) else clusters
    if mine_clusters:
        items = []
        for cluster in mine_clusters:
            name = cluster.get("name") or cluster.get("title") or ""
            share = cluster.get("share")
            members = cluster.get("games") or cluster.get("count") or 0
            if not name:
                continue
            items.append("%s（%s）"
                         % (h(name),
                            _pct(share, 0) if share is not None else "%d 局" % members))
        if items:
            parts.append("<br>bo 的开场聚类（不固定两类，按数据选 k）："
                         + "；".join(items) + "。")
    concentration = opening_concentration(mine)
    if concentration:
        top_items = ["%s %d 局（%s）" % (h(key.split(":", 1)[-1]), value,
                                        _pct(value / float(concentration["games"]), 0))
                     for key, value in list(concentration["signatures"].items())[:4]]
        top_share = concentration["top_share"]
        if top_share >= 0.7:
            shape = "基本就是<b>一套流程</b>，分叉很少"
        elif top_share >= 0.5:
            shape = "是<b>一套主流 + 少量分叉</b>"
        else:
            shape = "已经<b>明显分成几套</b>"
        parts.append("<br>DAG 这边互相印证：我的 opening 首段分布是 "
                     + "、".join(top_items) + "，也就是 %s。"
                     % shape)
    parts.append("</div></div>")
    return "".join(parts)


def _limits_block():
    return ('<h2>十、这套数的边界（必读）</h2><div class="card"><div class="hint">%s</div>'
            "</div>" % LIMITS)


# --------------------------------------------------------------------------- 主渲染

def render_report(mine, pro, meta=None, extra=None, min_node=2,
                  groups=None):
    meta = meta or {}
    extra = extra or {}
    groups = groups or {}
    zerg = groups.get("zerg")
    protoss = groups.get("protoss")
    mine_meta = mine["meta"]
    pro_meta = (pro or {}).get("meta") or {}
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>BO 转移图 · 我是主动执行还是被动反应</title>",
             "<style>%s%s</style></head><body><div class=\"wrap\">" % (CSS, EXTRA_CSS)]
    parts.append('<header class="top"><h1>BO 转移图：我是主动执行，还是被动反应？</h1>')
    parts.append('<div class="sub">%s · 我的 %d 局 / Maru %d 局 / 职业 Zerg %d 局 / '
                 '职业 Protoss %d 局 · 边 = 段之间的转移，条件分"到点"和"侦察到"'
                 % (h(mine_meta.get("matchup") or "TvZ"),
                    mine_meta.get("games") or 0, pro_meta.get("games") or 0,
                    ((zerg or {}).get("meta") or {}).get("games") or 0,
                    ((protoss or {}).get("meta") or {}).get("games") or 0))
    parts.append("</div></header>")

    # 一句话结论放最上面：20 秒口径（契约口径）+ 60 秒口径（更稳）
    mine_reaction = mine.get("reaction") or {}
    mine_window60 = (mine_reaction.get("by_window") or {}).get("60") or {}
    parts.append('<div class="card" style="margin-bottom:14px"><div class="answer">'
                 "一句话：这 %d 局里 <b>%s</b> 的段间转移发生在「刚看到东西」的 20 秒内，"
                 "把窗口放宽到 60 秒是 <b>%s</b>——说明打法的骨架是到点执行的，"
                 "但<b>看到之后一分钟内会调整</b>，反应有几十秒的滞后（后半句比前半句稳）。"
                 "</div></div>"
                 % (mine_reaction.get("games") or 0,
                    _pct(mine_reaction.get("scout_share"), 1),
                    _pct(mine_window60.get("scout_share"), 1)))

    parts.append('<h2>一、结论</h2>')
    parts.append(_answer_box(mine, pro))
    parts.append('<div class="card" style="margin-top:12px"><h3 style="margin:0 0 8px">'
                 "我 vs Maru（同一口径）</h3>%s</div>" % _compare_table(mine, pro))
    parts.append(_caliber_block(mine, pro, groups, extra.get("bo_meta"), extra))

    parts.append('<h2>二、我的 TvZ DAG</h2>')
    mine_block, mine_layout = _dag_block(mine, "二、我的 TvZ DAG",
                                         "节点 = (阶段, 段签名)：阶段按时间切，"
                                         "段签名 = bo 的 token 链去掉 addon/防御建筑、"
                                         "无序合并、数量截到 2。圈内数字是局数，"
                                         "线粗是转移次数。", min_node=min_node)
    parts.append(mine_block)

    if pro:
        parts.append('<h2>三、Maru 的 TvZ DAG（对照）</h2>')
        pro_block, pro_layout = _dag_block(pro, "三、Maru 的 TvZ DAG",
                                           "同一个口径、同一套信息事件算法，"
                                           "换成职业选手的 TvZ 样本。", min_node=min_node)
        parts.append(pro_block)
    else:
        pro_layout = None
        parts.append('<h2>三、Maru 的 TvZ DAG（对照）</h2><div class="card">'
                     '<div class="note">没有 Maru 的样本。</div></div>')

    zerg_games_count = ((zerg or {}).get("meta") or {}).get("games") or 0
    if zerg:
        zerg_block, _zerg_layout = _dag_block(
            zerg, "四、职业 Zerg 的开局 DAG",
            "只看 opening/early 两段（职业选手在做什么，不是对手会怎么应对你）。"
            "节点 = (阶段, 段签名)，同样做了 token 粗化；样本小，边阈值放宽到 1 次。",
            min_node=min_node, paths_limit=3, show_reaction=False,
            extra_note="选手：" + "、".join(
                "%s %d 局" % (name, count)
                for name, count in (extra.get("zerg_players") or {}).items()))
        parts.append('<h2>四、职业 Zerg 的开局 DAG（%d 局）</h2>' % zerg_games_count)
        parts.append(zerg_block)
    else:
        parts.append('<h2>四、职业 Zerg 的开局 DAG</h2><div class="card">'
                     '<div class="note">没有 Zerg 职业样本（待补）。</div></div>')

    protoss_games_count = ((protoss or {}).get("meta") or {}).get("games") or 0
    if protoss:
        protoss_block, _protoss_layout = _dag_block(
            protoss, "五、职业 Protoss 的开局 DAG",
            "只看 opening/early 两段。样本是职业 P 选手，不是你的对手模型；"
            "样本小，边阈值放宽到 1 次。",
            min_node=min_node, paths_limit=3, show_reaction=False,
            extra_note="选手：" + "、".join(
                "%s %d 局" % (name, count)
                for name, count in (extra.get("protoss_players") or {}).items()))
        parts.append('<h2>五、职业 Protoss 的开局 DAG（%d 局）</h2>' % protoss_games_count)
        parts.append(protoss_block)
    else:
        parts.append('<h2>五、职业 Protoss 的开局 DAG</h2><div class="card">'
                     '<div class="note">没有 Protoss 职业样本（待补）。</div></div>')

    dispersion_table, dispersion_values = _dispersion_table(
        {"mine": mine, "maru": pro, "zerg": zerg, "protoss": protoss})
    if dispersion_table:
        parts.append('<h2>六、开局选择面：谁更分散</h2><div class="card">')
        parts.append('<div class="desc">只看每局第一个段（opening）：'
                     "「最常见的开局占多少、一共几种、熵多大」。"
                     "熵 0 = 只走一套，1 = 完全平均。</div>")
        parts.append('<div style="margin-top:8px">%s</div>' % dispersion_table)
        matchup_note = extra.get("matchup_note") or ""
        if matchup_note:
            parts.append('<div class="note" style="margin-top:6px">%s</div>'
                         % h(matchup_note))
        parts.append('<div style="margin-top:12px">%s</div>'
                     % _dispersion_verdict(dispersion_values))
        parts.append("</div>")

    parts.append('<h2>七、最常见 10 条转移</h2><div class="split">')
    parts.append('<div class="card"><h3>我的 TvZ</h3>%s</div>' % _transitions_table(mine))
    parts.append('<div class="card"><h3>Maru 的 TvZ</h3>%s</div>'
                 % (_transitions_table(pro) if pro else '<div class="note">没有样本。</div>'))
    parts.append("</div>")

    parts.append('<h2>八、最常见 10 条完整路径</h2><div class="split">')
    parts.append('<div class="card"><h3>我的 TvZ</h3>%s</div>' % _paths_table(mine))
    parts.append('<div class="card"><h3>Maru 的 TvZ</h3>%s</div>'
                 % (_paths_table(pro) if pro else '<div class="note">没有样本。</div>'))
    parts.append("</div>")

    parts.append('<h2>九、分阶段：反应式转移集中在哪个阶段</h2><div class="split">')
    parts.append('<div class="card"><h3>我的 TvZ</h3>%s</div>' % _phase_table(mine))
    parts.append('<div class="card"><h3>Maru 的 TvZ</h3>%s</div>'
                 % (_phase_table(pro) if pro else '<div class="note">没有样本。</div>'))
    parts.append("</div>")

    parts.append('<div class="split" style="margin-top:14px">')
    parts.append('<div class="card"><h3>信息事件类型</h3>%s</div>' % _event_type_table(mine))
    parts.append('<div class="card"><h3>我的 DAG 节点</h3>'
                 '<details><summary>展开 %d 个节点</summary>%s</details></div>'
                 % (len(mine["nodes"]), _node_table(mine)))
    parts.append("</div>")

    parts.append(_limits_block())

    precision = (mine.get("precision_note") or {})
    reading = precision.get("reading") or ""
    parts.append('<h2>十一、数据质量</h2><div class="card"><div class="hint">%s'
                 '<br>本组一共算到 %s 条信息事件；位置采样可信阈值 %.0f 秒。'
                 '</div></div>'
                 % (h(reading), precision.get("events_total", 0),
                    float(mine_meta.get("position_trust_gap") or 0)))

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


def build_compare(mine, pro, groups=None):
    """写进 dag.json 的对照块：pro（Maru）+ 职业 Z/P 开局图 + 开局集中度。"""
    groups = groups or {}
    out = {"matchup": mine["meta"].get("matchup")}
    if pro:
        out["pro"] = pro
        out["table"] = {
            "games": [mine["meta"]["games"], pro["meta"]["games"]],
            "nodes": [len(mine["nodes"]), len(pro["nodes"])],
            "edges": [len(mine["edges"]), len(pro["edges"])],
            "scout_share": [(mine.get("reaction") or {}).get("scout_share"),
                            (pro.get("reaction") or {}).get("scout_share")],
            "time_share": [(mine.get("reaction") or {}).get("time_share"),
                           (pro.get("reaction") or {}).get("time_share")],
        }
    for key in ("zerg", "protoss"):
        if groups.get(key):
            out[key] = groups[key]
    concentration = {}
    for key, dag in (("mine", mine), ("maru", pro), ("zerg", groups.get("zerg")),
                     ("protoss", groups.get("protoss"))):
        if not dag:
            continue
        value = opening_concentration(dag)
        if value:
            concentration[key] = value
    if concentration:
        out["opening_concentration"] = concentration
    return out


def build_json(mine, pro, meta=None, groups=None):
    payload = dict(mine)
    payload["compare"] = build_compare(mine, pro, groups)
    payload["generator"] = {
        "tool": "replay_analyzer.dag_report",
        "generated": meta.get("generated") if meta else None,
        "bo_source": meta.get("bo") if meta else None,
        "limits": ["info events are geometric inference, not ground truth",
                   "position sampling starts around 120-270s and is sampled ~every 45s",
                   "terrain occlusion is not modelled",
                   "scan / cloak / burrow are not modelled",
                   "node signature = bo segment token chain, coarsened "
                   "(addons/defense dropped, order-insensitive, counts capped at 2)",
                   "zerg/protoss pro DAGs cover opening+early only (pro opening "
                   "repertoire, not an opponent response model)"],
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


def run(bo_path=None, matchup="TvZ", mine_limit=100, pro_limit=100,
        pro_name="Maru", min_edge=2, min_node=2, out_path=None, json_path=None,
        cache_dir=DEFAULT_CACHE, force=False, verbose=True, extra_bo=(),
        pro_race_limit=100, race_dir="replays/pro", skip_races=False):
    """跑完整条链路：bo.json -> 信息事件 -> 4 个 DAG -> HTML/JSON。

    ``extra_bo``：额外的 GameBO 文件（职业 Z/P 的开局样本），结构同 bo.json。
    职业 Z/P 只看 opening/early（``phases`` 过滤），T 组跑全阶段。
    """
    bo_path = bo_path or _default_bo()
    if not os.path.isfile(bo_path):
        raise SystemExit(
            "找不到 %s。DAG 依赖 bo.py 的分段结果：先跑 bo 的提取脚本产出 "
            "reports/bo.json，再用 --bo 指过去。" % bo_path)
    games, bo_meta = load_bo(bo_path)
    if verbose:
        print("[bo] %s -> %d games (meta=%s)" % (bo_path, len(games),
                                                 bool(bo_meta)), flush=True)

    extra_games = []
    for extra_path in extra_bo or ():
        if not os.path.isfile(extra_path):
            print("[warn] 找不到额外 BO 文件：%s" % extra_path, flush=True)
            continue
        loaded, _extra_meta = load_bo(extra_path)
        if verbose:
            print("[bo+] %s -> %d games" % (extra_path, len(loaded)), flush=True)
        extra_games.extend(loaded)
    if not extra_games and not skip_races:
        extra_games = extract_race_bos(root=race_dir, rebuild=force, verbose=verbose)

    mine_games = select_games(games, matchup=matchup, who="mine", limit=mine_limit)
    maru_games = select_games(games, matchup=matchup, who=pro_name, limit=pro_limit)
    by_race = pro_games_by_race(extra_games)
    zerg_games = select_games(by_race.get("Z") or [], limit=pro_race_limit)
    protoss_games = select_games(by_race.get("P") or [], limit=pro_race_limit)
    if verbose:
        print("[pick] mine=%d maru=%d zerg=%d protoss=%d"
              % (len(mine_games), len(maru_games), len(zerg_games),
                 len(protoss_games)), flush=True)

    group_games = [("mine", mine_games), ("maru", maru_games),
                   ("zerg", zerg_games), ("protoss", protoss_games)]
    all_events = {}
    all_stats = {}
    for label, picked in group_games:
        events, stats = compute_info_events(picked, cache_dir, force, verbose)
        all_events[label] = events
        all_stats[label] = stats

    sampling_values = sorted(value for stats in all_stats.values()
                             for value in (stats.get("sampling_values") or []))
    sampling = None
    if sampling_values:
        sampling = {"min": sampling_values[0],
                    "median": sampling_values[len(sampling_values) // 2]}
    if verbose and sampling:
        print("[data] position sampling starts at %.0fs (earliest %.0fs)"
              % (sampling["median"], sampling["min"]), flush=True)

    mine_dag = dagmod.build_dag(mine_games, min_edge=min_edge,
                                info_events=all_events["mine"], sampling=sampling)
    maru_dag = None
    if maru_games:
        maru_dag = dagmod.build_dag(maru_games, min_edge=min_edge,
                                    info_events=all_events["maru"],
                                    sampling=sampling)
    # 职业 Z/P 每组只有 25 局、又只看 opening/early，边阈值放宽到 1 次，
    # 否则每条 transfer 都只出现 1 次、图直接空掉。
    race_min_edge = 1 if (zerg_games or protoss_games) else min_edge
    zerg_dag = None
    if zerg_games:
        zerg_dag = dagmod.build_dag(zerg_games, min_edge=race_min_edge,
                                    info_events=all_events["zerg"],
                                    sampling=sampling, phases=("opening", "early"))
    protoss_dag = None
    if protoss_games:
        protoss_dag = dagmod.build_dag(protoss_games, min_edge=race_min_edge,
                                       info_events=all_events["protoss"],
                                       sampling=sampling, phases=("opening", "early"))
    groups = {"zerg": zerg_dag, "protoss": protoss_dag}

    generated = dagmod.datetime.date.today().isoformat()
    meta = {"generated": generated, "bo": bo_path,
            "extra_bo": list(extra_bo or ()),
            "mine": len(mine_games), "pro": len(maru_games),
            "zerg": len(zerg_games), "protoss": len(protoss_games)}
    missing = []
    for stats in all_stats.values():
        missing.extend(stats.get("missing") or [])
    extra = {
        "missing": missing,
        "bo_meta": bo_meta,
        "zerg_players": player_counts(zerg_games),
        "protoss_players": player_counts(protoss_games),
        "matchup_note": _matchup_note(zerg_games, protoss_games),
    }
    html_text = render_report(mine_dag, maru_dag, meta, extra,
                              min_node=min_node, groups=groups)
    payload = build_json(mine_dag, maru_dag, meta, groups=groups)

    out_path = out_path or os.path.join("reports", "dag.html")
    json_path = json_path or os.path.join("reports", "dag.json")
    write_report(html_text, out_path)
    write_json(payload, json_path)
    if verbose:
        print("[write] %s (%.1f KB)" % (out_path, os.path.getsize(out_path) / 1024.0),
              flush=True)
        print("[write] %s (%.1f KB)" % (json_path, os.path.getsize(json_path) / 1024.0),
              flush=True)
        for label, dag in (("mine", mine_dag), ("maru", maru_dag),
                           ("zerg", zerg_dag), ("protoss", protoss_dag)):
            if dag is None:
                continue
            layout = layout_dag(dag, min_node=min_node)
            problems = check_layout(layout)
            print("[check] %-8s nodes=%d edges=%d layout=%s"
                  % (label, len(dag["nodes"]), len(dag["edges"]),
                     "OK" if not problems else "%d problems" % len(problems)),
                  flush=True)
    return {"mine": mine_dag, "maru": maru_dag, "zerg": zerg_dag,
            "protoss": protoss_dag, "html": out_path, "json": json_path}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="BO 转移 DAG + 侦察触发标注（离线 HTML/JSON）")
    parser.add_argument("--bo", default=None, help="bo.py 输出的 reports/bo.json")
    parser.add_argument("--matchup", default="TvZ")
    parser.add_argument("--mine", type=int, default=100,
                        help="我的样本局数上限（默认全用；给 30 就是前 30 局）")
    parser.add_argument("--pro", type=int, default=100,
                        help="职业样本局数上限（默认全用）")
    parser.add_argument("--pro-name", default="Maru")
    parser.add_argument("--min-edge", type=int, default=2,
                        help="少于这么多次的边不进 edges")
    parser.add_argument("--min-node", type=int, default=2,
                        help="图上只画出现 >= 这么多局的节点")
    parser.add_argument("--out", default=None, help="HTML 输出路径")
    parser.add_argument("--json", dest="json_path", default=None, help="JSON 输出路径")
    parser.add_argument("--extra-bo", nargs="*", default=(),
                        help="额外的 GameBO 文件（职业 Z/P 开局样本，结构同 bo.json）")
    parser.add_argument("--race-dir", default="replays/pro",
                        help="职业录像根目录（会按目录选手种族抽 Z/P 的 BO）")
    parser.add_argument("--skip-races", action="store_true",
                        help="不抽职业 Z/P，只出我 + Maru 两张图")
    parser.add_argument("--pro-race-limit", type=int, default=100,
                        help="每个职业种族最多用多少局")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE)
    parser.add_argument("--force", action="store_true", help="忽略信息事件缓存")
    args = parser.parse_args(argv)
    run(bo_path=args.bo, matchup=args.matchup, mine_limit=args.mine,
        pro_limit=args.pro, pro_name=args.pro_name, min_edge=args.min_edge,
        min_node=args.min_node,
        out_path=args.out, json_path=args.json_path, cache_dir=args.cache_dir,
        force=args.force, extra_bo=tuple(args.extra_bo or ()),
        pro_race_limit=args.pro_race_limit, race_dir=args.race_dir,
        skip_races=args.skip_races)
    return 0


if __name__ == "__main__":
    sys.exit(main())
