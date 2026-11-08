"""操作细节分析：视角分布、多线重叠、编队使用、操作顺序。

数据来源是 replay.game.events 里的原始操作流（loader 已经换算成"格"坐标）：

* ``cam``   相机移动（1/256 格定点数 → 格）
* ``sel``   框选 / 点选
* ``cg``    编队操作（0 清空、1 新建、2 召回、5 追加）
* ``cmd``   指令（带目标点或目标单位）
* ``cmdp``  指令落点更新（拖拽施法 / 点地板）
* ``cmdu``  指令目标单位更新

"多线"在这里的定义：同一玩家在 6 秒内，在地图上相距 ≥25 格的两个（或更多）位置
都发出了指令，且每个位置至少 2 条指令。这是个启发式指标，用来找"真正同时在打两条线"
的窗口，而不是把同一场团战里的微操算成多线。
"""

import math
import statistics

from .metrics import fmt_time, to_seconds

LOOPS_PER_SECOND = 16.0
SWITCH_DIST = 6.0      # 相机移动超过 6 格算一次换视角
MULTI_GAP = 6.0        # 一个多线窗口的最大时间跨度（秒）
MULTI_DIST = 25.0      # 两个操作点相距多少格才算"两条线"
CLUSTER_LINK = 14.0    # 同一个位置的操作点聚合半径
IDLE_GAP = 5.0         # 超过 5 秒没有指令 = 空窗
MIN_WINDOW_ACTIONS = 4

CG_ACTION = {0: "清空编队", 1: "新建编队", 2: "调出编队", 3: "追加编队",
             4: "剔除编队", 5: "替换编队"}


# --------------------------------------------------------------------------- 基础
def _t(loop):
    return loop / LOOPS_PER_SECOND


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _landmarks(parsed):
    """把所有基地（含对手）整理成命名地标，用于把坐标翻译成人话。"""
    marks = []
    for player in parsed.players:
        bases = [u for u in parsed.unit_records
                 if u.player_id == player.player_id
                 and u.name in ("CommandCenter", "Nexus", "Hatchery", "Lair", "Hive")
                 and u.x is not None]
        bases.sort(key=lambda u: (u.start_loop if u.start_loop is not None else 0))
        for index, unit in enumerate(bases[:5]):
            if index == 0:
                label = "主基地"
            elif index == 1:
                label = "二矿"
            else:
                label = "%d 矿" % (index + 1)
            marks.append({"x": float(unit.x), "y": float(unit.y),
                          "owner": player.player_id, "label": label})
    return marks


def _map_bounds(marks):
    if not marks:
        return (0.0, 190.0, 60.0, 180.0)
    xs = [m["x"] for m in marks]
    ys = [m["y"] for m in marks]
    return (min(xs) - 25, max(xs) + 25, min(ys) - 25, max(ys) + 25)


def _region(point, marks, viewer_pid, bounds):
    """把坐标翻译成“我方二矿”“野外·右上”这样的人话。"""
    best = None
    for mark in marks:
        distance = _dist(point, (mark["x"], mark["y"]))
        if best is None or distance < best[0]:
            best = (distance, mark)
    if best:
        distance, mark = best
        owner = "我方" if mark["owner"] == viewer_pid else "敌方"
        if distance <= 18.0:
            return owner + mark["label"]
        if distance <= 40.0:
            return "野外·近%s%s" % (owner, mark["label"])

    left, right, bottom, top = bounds
    width = max(1.0, right - left)
    height = max(1.0, top - bottom)
    col = min(2, max(0, int((point[0] - left) / (width / 3.0))))
    row = min(2, max(0, int((point[1] - bottom) / (height / 3.0))))
    return "野外·%s%s" % ("左中右"[col], "下中上"[row])




# --------------------------------------------------------------------------- 相机
def _camera_visits(cam_points):
    visits = []
    current = None
    for t, x, y in cam_points:
        if current is None or _dist((x, y), (current["x"], current["y"])) >= SWITCH_DIST:
            if current is not None:
                visits.append(current)
            current = {"t0": t, "x": x, "y": y, "t1": t}
        current["t1"] = t
    if current is not None:
        visits.append(current)
    for visit in visits:
        visit["dur"] = max(0.0, visit["t1"] - visit["t0"])
    return visits


def _cluster(points):
    """单链式聚类：距离质心 < CLUSTER_LINK 就归到同一堆。

    points 的元素是 (秒, x, y)，所以坐标是 [1]、[2]，别把时间当坐标用。
    """
    clusters = []
    for point in points:
        px, py = point[1], point[2]
        placed = False
        for cluster in clusters:
            if _dist((px, py), (cluster["x"], cluster["y"])) <= CLUSTER_LINK:
                cluster["pts"].append(point)
                n = len(cluster["pts"])
                cluster["x"] = sum(p[1] for p in cluster["pts"]) / n
                cluster["y"] = sum(p[2] for p in cluster["pts"]) / n
                placed = True
                break
        if not placed:
            clusters.append({"x": px, "y": py, "pts": [point]})
    return clusters


# --------------------------------------------------------------------------- 多线
def _multitask_windows(cmd_points):
    """滑窗找"同一时间在两个相距很远的位置下指令"的窗口。"""
    windows = []
    n = len(cmd_points)
    i = 0
    while i < n:
        t0 = cmd_points[i][0]
        block = []
        j = i
        while j < n and cmd_points[j][0] - t0 <= MULTI_GAP:
            block.append(cmd_points[j])
            j += 1
        if len(block) >= MIN_WINDOW_ACTIONS:
            clusters = _cluster(block)
            real = [c for c in clusters if len(c["pts"]) >= 2]
            if len(real) >= 2:
                spread = max(
                    _dist((a["x"], a["y"]), (b["x"], b["y"]))
                    for a in real for b in real)
                if spread >= MULTI_DIST:
                    windows.append({
                        "t0": t0,
                        "t1": block[-1][0],
                        "clusters": sorted(real, key=lambda c: -len(c["pts"])),
                        "spread": spread,
                        "actions": len(block),
                    })
                    i = max(i + 1, j - 1)
                    continue
        i += 1

    # 合并时间上重叠/紧邻的窗口
    merged = []
    for window in windows:
        # 合并相邻窗口，但单段最长 12 秒——否则高密度对局会连成一分钟
        if (merged and window["t0"] - merged[-1]["t1"] <= 1.0
                and window["t1"] - merged[-1]["t0"] <= 12.0):
            last = merged[-1]
            last["t1"] = max(last["t1"], window["t1"])
            last["actions"] += window["actions"]
            last["windows"] = last.get("windows", 1) + 1
            continue
        window["windows"] = 1
        merged.append(window)
    return merged


def _sequence(actions, t0, t1, marks, pid, bounds):
    """把一个时间窗内的操作流翻译成"先干嘛后干嘛"的文字时间线。"""
    lines = []
    last_kind = None
    last_t = -99.0
    for action in actions:
        t = _t(action["l"])
        if t < t0 - 0.2 or t > t1 + 0.2:
            continue
        kind = action["k"]
        # 连续拖屏只记一次
        if kind == "cam" and last_kind == "cam" and t - last_t < 0.35:
            continue
        where = None
        if "x" in action:
            where = _region((action["x"], action["y"]), marks, pid, bounds)
        if kind == "cam":
            text = "视角→%s" % where
        elif kind == "sel":
            size = action.get("n") or 0
            text = "选中（%d 个）" % size if size else "选中"
        elif kind == "cg":
            action_name = CG_ACTION.get(action.get("up"), "编队操作")
            text = "%s %s" % (action_name, (action.get("g") or 0) + 1)
        elif kind in ("cmd", "cmdp", "cmdu"):
            text = "指令→%s" % (where or "目标单位")
            if action.get("ab"):
                text += "（技能 %s）" % action["ab"]
        else:
            text = kind
        lines.append({"t": t, "text": text})
        last_kind = kind
        last_t = t
    return lines


# --------------------------------------------------------------------------- 反应
def _reaction_times(parsed, player_id, cmd_points):
    deaths = sorted(_t(u.died_loop) for u in parsed.unit_records
                    if u.player_id == player_id and u.died_loop is not None and not u.is_structure)
    if not deaths or not cmd_points:
        return []
    clusters = []
    last = None
    for t in deaths:
        if last is None or t - last > 10:
            clusters.append(t)
        last = t
    times = []
    for start in clusters:
        for t, _x, _y in cmd_points:
            if 0 <= t - start <= 6:
                times.append(t - start)
                break
    return times


# --------------------------------------------------------------------------- 主入口
def analyze_micro(parsed, player_ids=None):
    marks = _landmarks(parsed)
    bounds = _map_bounds(marks)
    by_user = {}
    for action in parsed.actions:
        by_user.setdefault(action["u"], []).append(action)

    result = {"meta": {"map": parsed.map_name,
                       "duration": fmt_time(parsed.duration_seconds),
                       "duration_seconds": parsed.duration_seconds,
                       "path": parsed.path,
                       "protocol_build": parsed.protocol_build},
              "players": []}

    for player in parsed.players:
        if player_ids and player.player_id not in player_ids:
            continue
        actions = by_user.get(player.user_id, [])
        if not actions:
            continue
        cam_points = [(_t(a["l"]), a["x"], a["y"]) for a in actions
                      if a["k"] == "cam" and "x" in a]
        cmd_points = [(_t(a["l"]), a["x"], a["y"]) for a in actions
                      if a["k"] in ("cmd", "cmdp", "cmdu") and "x" in a]
        cmd_points.sort()
        visits = _camera_visits(cam_points)
        real_visits = [v for v in visits if v["dur"] >= 0.4]

        attention = {}
        for visit in real_visits:
            label = _region((visit["x"], visit["y"]), marks, player.player_id, bounds)
            attention[label] = attention.get(label, 0.0) + visit["dur"]

        windows = _multitask_windows(cmd_points)
        for window in windows:
            labels = []
            for cluster in window["clusters"][:3]:
                labels.append(_region((cluster["x"], cluster["y"]), marks,
                                      player.player_id, bounds))
            window["labels"] = labels
            window["time"] = fmt_time(window["t0"])
            window["duration"] = window["t1"] - window["t0"]
            inner = [a for a in actions
                     if window["t0"] <= _t(a["l"]) <= window["t1"]]
            window["switch_count"] = sum(1 for a in inner if a["k"] == "cam")
            times = sorted(_t(a["l"]) for a in inner if a["k"] in ("cmd", "cmdp", "cmdu"))
            window["cmd_interval"] = (round(statistics.median(
                [b - a for a, b in zip(times, times[1:])]), 2) if len(times) > 1 else None)
            window["clusters"] = [{"x": c["x"], "y": c["y"], "n": len(c["pts"])}
                                  for c in window["clusters"]]
            window["lines"] = _sequence(actions, window["t0"], window["t1"],
                                        marks, player.player_id, bounds)

        # 空窗按"任何操作"算（只看指令会高估空窗）
        gaps = []
        last_t = None
        for action in actions:
            t = _t(action["l"])
            if last_t is not None and t - last_t >= IDLE_GAP and t > 150:
                gaps.append({"t0": last_t, "t1": t, "dur": t - last_t})
            last_t = t

        # 只算“指令”的空窗：手在动但没下命令
        cmd_gaps = []
        last_t = None
        for t, _x, _y in cmd_points:
            if last_t is not None and t - last_t >= 8.0 and t > 180:
                cmd_gaps.append({"t0": last_t, "t1": t, "dur": t - last_t})
            last_t = t

        # 窗口强度：指令数 × 跨度，用来排“含金量”
        for window in windows:
            window["strength"] = round(window["actions"] * window["spread"] / 100.0, 1)

        hotkeys = {}
        for action in actions:
            if action["k"] == "cg":
                group = action.get("g")
                if group is not None:
                    entry = hotkeys.setdefault(group, {"recall": 0, "set": 0})
                    if action.get("up") == 2:
                        entry["recall"] += 1
                    else:
                        entry["set"] += 1

        reactions = _reaction_times(parsed, player.player_id, cmd_points)
        minutes = max(0.1, parsed.duration_seconds / 60.0)
        window_seconds = sum(w["duration"] for w in windows)

        # 按分钟分桶，给图表用
        total_minutes = int(parsed.duration_seconds // 60) + 1
        switches_series = [0] * total_minutes
        for visit in real_visits:
            switches_series[min(total_minutes - 1, int(visit["t0"] // 60))] += 1
        window_series = [0] * total_minutes
        for window in windows:
            window_series[min(total_minutes - 1, int(window["t0"] // 60))] += 1

        result["players"].append({
            "player_id": player.player_id,
            "name": player.name,
            "short_name": player.name if len(player.name) <= 12 else player.name[:11] + "…",
            "race": player.race_name,
            "race_code": player.race,
            "color": player.color_hex,
            "is_local": player.is_local,
            "result_zh": player.result_zh,
            "cam_switches": len(real_visits),
            "cam_switches_per_min": round(len(real_visits) / minutes, 1),
            "cam_dwell_median": round(statistics.median([v["dur"] for v in real_visits]), 1)
                               if real_visits else 0,
            "cam_dwell_max": round(max([v["dur"] for v in real_visits]), 1) if real_visits else 0,
            "attention": sorted(({"label": k, "seconds": round(v, 1)}
                                 for k, v in attention.items()),
                                key=lambda item: -item["seconds"]),
            "cmd_count": len(cmd_points),
            "cmd_per_min": round(len(cmd_points) / minutes, 1),
            "windows": windows,
            "window_count": len(windows),
            "windows_per_min": round(len(windows) / minutes, 2),
            "window_seconds": round(window_seconds, 1),
            "window_share": round(100.0 * window_seconds / max(1.0, parsed.duration_seconds), 1),
            "hotkeys": sorted(({"group": g + 1, "recall": v["recall"], "set": v["set"]}
                               for g, v in hotkeys.items()),
                              key=lambda item: -(item["recall"] + item["set"])),
            "gaps": sorted(gaps, key=lambda g: -g["dur"])[:6],
            "gap_total": round(sum(g["dur"] for g in gaps), 1),
            "cmd_gaps": sorted(cmd_gaps, key=lambda g: -g["dur"])[:6],
            "cmd_gap_total": round(sum(g["dur"] for g in cmd_gaps), 1),
            "top_windows": sorted(windows, key=lambda w: -w["strength"])[:3],
            "reaction_median": round(statistics.median(reactions), 1) if reactions else None,
            "reaction_count": len(reactions),
            "cam_track": [[round(t / 60.0, 2), round(x, 1)] for t, x, y in cam_points[::3]],
            "switches_series": switches_series,
            "window_series": window_series,
            "camera_points": len(cam_points),
        })

    result["marks"] = marks
    result["drills"] = _drills(result)
    return result


# --------------------------------------------------------------------------- 批量
def analyze_folder(paths, limit=None, local_only=True):
    """对一批 replay 逐个算操作指标，用于看趋势（练习打卡用）。

    ``paths`` 应该已经按时间倒序排好；跳过的局不计入 limit。
    """
    from .loader import load_replay

    rows = []
    for path in paths:
        if limit and len(rows) >= limit:
            break
        try:
            parsed = load_replay(path)
            micro = analyze_micro(parsed)
        except Exception:
            continue
        players = micro["players"]
        if not players:
            continue
        player = next((p for p in players if p["is_local"]), players[0])
        if local_only and not any(p["is_local"] for p in players):
            continue
        opponent = next((p for p in players if p["player_id"] != player["player_id"]), None)
        rows.append({
            "path": path,
            "file": path.replace("\\", "/").split("/")[-1],
            "map": parsed.map_name,
            "date": parsed.replay_time.astimezone().strftime("%Y-%m-%d %H:%M")
                    if parsed.replay_time else "",
            "duration": fmt_time(parsed.duration_seconds),
            "race": player["race"],
            "opponent_race": opponent["race"] if opponent else "?",
            "result": player["result_zh"],
            "cam_switches_per_min": player["cam_switches_per_min"],
            "cam_dwell_median": player["cam_dwell_median"],
            "windows_per_min": player["windows_per_min"],
            "window_share": player["window_share"],
            "cmd_gap_total": player["cmd_gap_total"],
            "cmd_per_min": player["cmd_per_min"],
            "reaction_median": player["reaction_median"],
            "hotkey_slots": len([h for h in player["hotkeys"]
                                 if h["recall"] + h["set"] > 0]),
        })
    rows.sort(key=lambda row: row["date"])
    return rows


# --------------------------------------------------------------------------- 评分
def _score(value, good_low, good_high):
    if value < good_low:
        return "low"
    if value > good_high:
        return "high"
    return "ok"


def _drills(result):
    """根据指标给出三条可执行的练习方向。"""
    players = result["players"]
    local = next((p for p in players if p["is_local"]), players[0] if players else None)
    if local is None:
        return []
    tips = []

    switches = local["cam_switches_per_min"]
    state = _score(switches, 8, 30)
    if state == "low":
        tips.append({"level": "warn", "title": "视角停留太久",
                     "text": "每分钟只换 %s 次视角。多线的前提是频繁但短促的注视：每条线看 1–2 秒就切走。"
                             % switches,
                     "drill": "下一局每 4 秒强制定时切一次视角（主基地→前线→生产），用鼠标中键/空格回到主基地。"})
    elif state == "high":
        tips.append({"level": "info", "title": "视角切得偏碎",
                     "text": "每分钟 %s 次换视角，平均只停留 %s 秒。切得太碎会让每条线的指令都不完整。"
                             % (switches, local["cam_dwell_median"]),
                     "drill": "练习“切过去→做完 2–3 个动作→再切走”，而不是瞄一眼就走。"})
    else:
        tips.append({"level": "good", "title": "视角节奏正常",
                     "text": "每分钟 %s 次换视角，平均停留 %s 秒，这个频率是健康的。"
                             % (switches, local["cam_dwell_median"]),
                     "drill": "保持频率，重点练“窗口里先做哪条线”。"})

    if local["window_count"] == 0:
        tips.append({"level": "warn", "title": "整局没有多线窗口",
                     "text": "没有出现“6 秒内在两个相距 25 格以上的位置各下 2 条指令”的情况。",
                     "drill": "从“一队兵 A 到前线 + 运输机同时起飞”开始练：先用编队 1 下令，再用编队 2 下另一条线的令。"})
    elif local["windows_per_min"] < 0.4:
        tips.append({"level": "warn", "title": "多线密度偏低",
                     "text": "全场 %d 个多线窗口，平均每分钟 %s 个，占对局时间 %s%%。"
                             % (local["window_count"], local["windows_per_min"], local["window_share"]),
                     "drill": "把“主部队接战前 5 秒必须给二线一条新指令”当成硬性规则。"})
    else:
        tips.append({"level": "good", "title": "多线密度不错",
                     "text": "全场 %d 个多线窗口，每分钟 %s 个，占对局时间 %s%%。"
                             % (local["window_count"], local["windows_per_min"], local["window_share"]),
                     "drill": "下一步压窗口里的耗时：同样的操作数，用更少的视角切换完成。"})

    if local["cmd_gap_total"] >= 60:
        worst = local["cmd_gaps"][0] if local["cmd_gaps"] else None
        text = "整局有 %s 秒“手在动但没有下指令”的空档" % local["cmd_gap_total"]
        if worst:
            text += "，最长一次 %s 秒（%s 起）" % (round(worst["dur"], 1), fmt_time(worst["t0"]))
        tips.append({"level": "warn", "title": "有长时间不下指令",
                     "text": text + "。视角在切、单位在选，但没转化成命令。",
                     "drill": "给自己定一条：任何一次视角切换，落地后 2 秒内必须下至少 1 条指令。"})

    used = [h for h in local["hotkeys"] if h["recall"] + h["set"] > 0]
    if len(used) < 3:
        tips.append({"level": "info", "title": "编队用得少",
                     "text": "整局只用了 %d 个编队槽。" % len(used),
                     "drill": "固定下来：1 主部队 / 2 二线（空投或包抄）/ 3 生产 / 4 偷家，每 30 秒重编一次。"})

    if local["top_windows"]:
        best = local["top_windows"][0]
        tips.append({"level": "info", "title": "拿去复盘的样板窗口",
                     "text": "%s 起的那个窗口最“满”：%.1f 秒内 %d 条指令、跨 %.0f 格。"
                             % (best["time"], best["duration"], best["actions"], best["spread"]),
                     "drill": "把它当模板：同样的两线，试着用更少的视角切换、更短的间隔复现一遍。"})

    return tips
