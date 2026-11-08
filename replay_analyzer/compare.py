"""对照组：把自己的录像和职业选手的录像放在一起比操作指标。

两个功能：

1. **对照组**：同一对位（比如 TvZ）下，你的多线密度 / 视角节奏 / 空档 与职业选手的中位数对比；
2. **窗口相似度**：把每个"多线窗口"抽成特征向量（跨度、指令数、时长、视角切换、指令间隔），
   在职业选手里找结构最接近的窗口，并排显示两边的操作序列——用来学"同样的局面，他先做什么"。
"""

import glob
import math
import os
import statistics

from . import micro


# --------------------------------------------------------------------------- 读组
_RACE = {"人族": "T", "星灵": "P", "异虫": "Z", "随机": "R",
         "Terran": "T", "Protoss": "P", "Zerg": "Z"}
def _paths(root):
    root = os.path.abspath(root)
    if os.path.isfile(root):
        return [root]
    return sorted(glob.glob(os.path.join(root, "**", "*.SC2Replay"), recursive=True))


def _guess_name(root):
    """replays/pro/maru -> maru"""
    name = os.path.basename(os.path.abspath(root).rstrip("\\/"))
    return name if name.lower() not in ("pro", "replays", "replay") else ""


def _pick_player(players, name, prefer_local=True):
    if prefer_local:
        local = next((p for p in players if p["is_local"]), None)
        if local:
            return local
    if name:
        target = name.lower()
        for player in players:
            if player["name"].lower() == target:
                return player
        for player in players:
            if target in player["name"].lower():
                return player
    return players[0] if players else None


def load_group(root, label, player_name=None, limit=None, matchup=None):
    """把一个目录里的 replay 逐个分析，抽出指定玩家的指标行和多线窗口。"""
    from .loader import load_replay

    rows, windows = [], []
    paths = _paths(root)
    for path in paths:
        if limit and len(rows) >= limit:
            break
        try:
            parsed = load_replay(path)
            data = micro.analyze_micro(parsed)
        except Exception:
            continue
        if not data["players"]:
            continue
        player = _pick_player(data["players"], player_name)
        if player is None:
            continue
        opponent = next((p for p in data["players"]
                         if p["player_id"] != player["player_id"]), None)
        race = player.get("race_code") or _RACE.get(player["race"], "?")
        other = (opponent.get("race_code") or _RACE.get(opponent["race"], "?")) if opponent else "?"
        if matchup:
            first, second = matchup[:1].upper(), matchup[-1:].upper()
            if not (race == first and other == second):
                continue
        date = parsed.replay_time.astimezone().strftime("%Y-%m-%d") if parsed.replay_time else ""
        rows.append({
            "file": os.path.basename(path),
            "map": parsed.map_name,
            "date": date,
            "duration": micro.fmt_time(parsed.duration_seconds),
            "player": player["name"],
            "opponent": opponent["name"] if opponent else "",
            "matchup": "%sv%s" % (race, other),
            "result": player["result_zh"],
            "cam_switches_per_min": player["cam_switches_per_min"],
            "cam_dwell_median": player["cam_dwell_median"],
            "windows_per_min": player["windows_per_min"],
            "window_share": player["window_share"],
            "cmd_per_min": player["cmd_per_min"],
            "cmd_gap_total": player["cmd_gap_total"],
            "window_count": player["window_count"],
        })
        for window in player["windows"]:
            windows.append({
                "game": os.path.basename(path),
                "path": path,
                "map": parsed.map_name,
                "date": date,
                "player": player["name"],
                "opponent": opponent["name"] if opponent else "",
                "matchup": "%sv%s" % (race, other),
                "time": window["time"],
                "t0": window["t0"],
                "duration": window["duration"],
                "actions": window["actions"],
                "spread": window["spread"],
                "switch_count": window.get("switch_count", 0),
                "cmd_interval": window.get("cmd_interval") or 0.0,
                "strength": window.get("strength", 0),
                "labels": window.get("labels", []),
                "lines": window.get("lines", []),
            })
    return {"label": label, "root": os.path.abspath(root), "rows": rows, "windows": windows}


# --------------------------------------------------------------------------- 统计
METRICS = (
    ("cam_switches_per_min", "视角切换 / 分"),
    ("cam_dwell_median", "单次停留（秒）"),
    ("windows_per_min", "多线窗口 / 分"),
    ("window_share", "多线覆盖（%）"),
    ("cmd_per_min", "指令 / 分"),
    ("cmd_gap_total", "不下指令空档（秒）"),
)


def _stats(rows):
    out = {}
    for key, _label in METRICS:
        values = [row[key] for row in rows if row.get(key) is not None]
        if not values:
            out[key] = None
            continue
        out[key] = {"median": round(statistics.median(values), 2),
                    "mean": round(sum(values) / len(values), 2),
                    "min": round(min(values), 2),
                    "max": round(max(values), 2)}
    return out


# --------------------------------------------------------------------------- 相似度
FEATURES = ("spread", "actions", "duration", "switch_count", "cmd_interval")
SCALE = {"spread": 60.0, "actions": 10.0, "duration": 5.0,
         "switch_count": 6.0, "cmd_interval": 0.6}


def _vector(window):
    return [float(window.get(key) or 0) / SCALE[key] for key in FEATURES]


def _distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(_vector(a), _vector(b))))


def _phase(t0):
    if t0 < 360:
        return "前期"
    if t0 < 720:
        return "中期"
    return "后期"


def _shape(window):
    """窗口的粗略形状描述，用来给人看的。"""
    spread = window["spread"]
    if spread >= 90:
        scale = "远距离两线"
    elif spread >= 45:
        scale = "中距离两线"
    else:
        scale = "近距离两线"
    pace = window["actions"] / max(0.5, window["duration"])
    return "%s · %.1f 指令/秒" % (scale, pace)


def match_windows(mine, theirs, limit=4):
    """给我最好的几个窗口，各找职业侧最像的一个。"""
    if not mine or not theirs:
        return []
    ordered = sorted(mine, key=lambda w: -w["strength"])[:limit]
    pairs = []
    for window in ordered:
        phase = _phase(window["t0"])
        # 优先找同阶段（前期/中期/后期）的窗口，没有再放宽到全场
        same_phase = [w for w in theirs if _phase(w["t0"]) == phase]
        pool = same_phase or theirs
        best = min(pool, key=lambda other: _distance(window, other))
        pairs.append({
            "mine": window,
            "theirs": best,
            "distance": round(_distance(window, best), 2),
            "my_shape": _shape(window),
            "their_shape": _shape(best),
            "phase": phase,
            "same_phase": bool(same_phase),
        })
    return pairs


# --------------------------------------------------------------------------- 结论
def _verdict(group_me, group_pro, stats_me, stats_pro, matchup):
    if not group_me["rows"] or not group_pro["rows"]:
        return []
    lines = []

    def ratio(key):
        a = stats_me.get(key, {}).get("median")
        b = stats_pro.get(key, {}).get("median")
        if a is None or b is None or b == 0:
            return None
        return a / b

    windows_ratio = ratio("windows_per_min")
    if windows_ratio is not None:
        if windows_ratio < 0.7:
            lines.append({"level": "warn", "text":
                          "多线密度只有对方的 %.0f%%：你中位 %.2f 个/分，对方 %.2f 个/分。"
                          % (windows_ratio * 100,
                             stats_me["windows_per_min"]["median"],
                             stats_pro["windows_per_min"]["median"])})
        elif windows_ratio > 1.3:
            lines.append({"level": "good", "text":
                          "多线密度高于对方（%.0f%%）：你 %.2f vs %.2f 个/分。"
                          % (windows_ratio * 100,
                             stats_me["windows_per_min"]["median"],
                             stats_pro["windows_per_min"]["median"])})
        else:
            lines.append({"level": "good", "text":
                          "多线密度接近对方（你 %.2f vs %.2f 个/分）。"
                          % (stats_me["windows_per_min"]["median"],
                             stats_pro["windows_per_min"]["median"])})

    share_ratio = ratio("window_share")
    if share_ratio is not None:
        lines.append({"level": "info" if share_ratio >= 0.7 else "warn", "text":
                      "多线覆盖：你 %.1f%% vs 对方 %.1f%%。"
                      % (stats_me["window_share"]["median"],
                         stats_pro["window_share"]["median"])})

    mine_actions = statistics.median([w["actions"] for w in group_me["windows"]]) \
        if group_me["windows"] else 0
    their_actions = statistics.median([w["actions"] for w in group_pro["windows"]]) \
        if group_pro["windows"] else 0
    if mine_actions and their_actions:
        lines.append({"level": "info", "text":
                      "单个窗口的指令数中位：你 %.0f 条，对方 %.0f 条——差距往往不在“开了几条线”，"
                      "而在“每条线做了几件事”。" % (mine_actions, their_actions)})

    mine_switch = statistics.median([w["switch_count"] for w in group_me["windows"]]) \
        if group_me["windows"] else 0
    their_switch = statistics.median([w["switch_count"] for w in group_pro["windows"]]) \
        if group_pro["windows"] else 0
    if mine_switch and their_switch:
        lines.append({"level": "info", "text":
                      "窗口内的视角切换中位：你 %.0f 次，对方 %.0f 次；越多说明越依赖来回看，"
                      "越少则是靠编队直接下发。" % (mine_switch, their_switch)})
    return lines


# --------------------------------------------------------------------------- 入口
def compare(me_root, pro_root, me_name=None, pro_name=None, matchup=None,
            limit=30, match_limit=4):
    me_name = me_name or _guess_name(me_root)
    pro_name = pro_name or _guess_name(pro_root)
    group_me = load_group(me_root, "我", player_name=me_name, limit=limit, matchup=matchup)
    group_pro = load_group(pro_root, "职业", player_name=pro_name, limit=limit, matchup=matchup)
    stats_me = _stats(group_me["rows"])
    stats_pro = _stats(group_pro["rows"])
    return {
        "meta": {"matchup": matchup or "全部对位",
                 "me_root": group_me["root"], "pro_root": group_pro["root"],
                 "me_name": me_name or "我", "pro_name": pro_name or "职业选手"},
        "groups": [group_me, group_pro],
        "stats": [stats_me, stats_pro],
        "pairs": match_windows(group_me["windows"], group_pro["windows"], limit=match_limit),
        "verdict": _verdict(group_me, group_pro, stats_me, stats_pro, matchup),
        "metric_labels": [label for _key, label in METRICS],
    }
