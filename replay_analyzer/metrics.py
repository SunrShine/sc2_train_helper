"""指标计算：把 :class:`ParsedReplay` 变成报告需要的数字与结论。

产出是一个纯 dict / list 结构（可直接 JSON 序列化），交给 report.py 渲染。
所有"分钟"都是游戏内时间（1 游戏秒 = 16 loop，与游戏内计时器一致）。
"""

import re

from . import units as unitdata

LOOPS_PER_SECOND = 16.0
MINUTE = 60.0

_HAN = r"\u4e00-\u9fff"
_WIDE = r"\u3000-\u303f\u4e00-\u9fff\uff00-\uffef"
_PUNCT = r"\u3000-\u303f\uff01-\uff5e"


def _tidy(text):
    """中英文混排的空白整理：中文之间不留空格，中英文之间补一个空格。"""
    text = re.sub(r"(?<=[%s]) +(?=[%s])" % (_WIDE, _WIDE), "", text)
    text = re.sub(r"([%s])([A-Za-z0-9])" % _HAN, r"\1 \2", text)
    text = re.sub(r"([A-Za-z0-9%%])([%s])" % _HAN, r"\1 \2", text)
    text = re.sub(r"(?<=[%s]) +" % _PUNCT, "", text)
    text = re.sub(r" +(?=[%s])" % _PUNCT, "", text)
    return text


# --------------------------------------------------------------------------- 工具
def to_seconds(loop):
    return loop / LOOPS_PER_SECOND


def fmt_time(seconds):
    seconds = max(0, int(round(seconds)))
    return "%d:%02d" % (seconds // 60, seconds % 60)


def fmt_num(value):
    if value is None:
        return "-"
    value = float(value)
    if abs(value) >= 10000:
        return "%.1fk" % (value / 1000.0)
    if abs(value) >= 100:
        return "%d" % round(value)
    if abs(value) >= 10:
        return "%.1f" % value
    return "%.1f" % value


# --------------------------------------------------------------------------- APM
CAMERA_EVENTS = {"NNet.Game.SCameraUpdateEvent", "NNet.Game.SCameraSaveEvent"}
COMMAND_EVENTS = {
    "NNet.Game.SCmdEvent",
    "NNet.Game.SCmdUpdateTargetPointEvent",
    "NNet.Game.SCmdUpdateTargetUnitEvent",
    "NNet.Game.SSelectionDeltaEvent",
    "NNet.Game.SSelectionEvent",
    "NNet.Game.SControlGroupUpdateEvent",
    "NNet.Game.SResourceTransferEvent",
}
PURE_COMMAND_EVENTS = {
    "NNet.Game.SCmdEvent",
    "NNet.Game.SCmdUpdateTargetPointEvent",
    "NNet.Game.SCmdUpdateTargetUnitEvent",
}


def _apm_series(parsed):
    """按分钟统计每个玩家的事件数，返回 {user_id: {"apm": [...], "epm": [...], "camera": [...]}}"""
    duration_minutes = max(1, int(parsed.duration_seconds / 60.0) + 1)
    buckets = {}
    cameras = {}
    commands = {}
    for loop, user_id, name in parsed.game_actions:
        minute = int(to_seconds(loop) // 60)
        if name in COMMAND_EVENTS:
            commands.setdefault(user_id, {}).setdefault(minute, 0)
            commands[user_id][minute] += 1
        if name in PURE_COMMAND_EVENTS:
            buckets.setdefault(user_id, {}).setdefault(minute, 0)
            buckets[user_id][minute] += 1
        if name in CAMERA_EVENTS:
            cameras.setdefault(user_id, {}).setdefault(minute, 0)
            cameras[user_id][minute] += 1

    # 最后不足一分钟的桶按实际时长折算，避免尾段虚低
    last_minute = int(parsed.duration_seconds // 60)
    tail = parsed.duration_seconds - last_minute * 60.0

    result = {}
    for player in parsed.players:
        uid = player.user_id
        series_apm = _expand(commands.get(uid, {}), last_minute, tail)
        series_epm = _expand(buckets.get(uid, {}), last_minute, tail)
        series_cam = _expand(cameras.get(uid, {}), last_minute, tail)
        result[uid] = {"apm": series_apm, "epm": series_epm, "camera": series_cam}
    return result


def _expand(buckets, last_minute, tail):
    series = []
    total = 0
    for minute in range(last_minute + 1):
        count = buckets.get(minute, 0)
        total += count
        if minute == last_minute and tail > 1:
            value = count * (60.0 / tail)
        else:
            value = float(count)
        series.append(round(value, 1))
    return series


# --------------------------------------------------------------------------- 时间线
def _stats_series(parsed, player_id, key):
    points = []
    for sample in parsed.stats.get(player_id, []):
        points.append([round(to_seconds(sample["loop"]) / 60.0, 3), sample.get(key, 0)])
    return points


def _build_timelines(parsed):
    """把 stats 采样转成图表用的时间线。"""
    defs = [
        ("army", "军队价值", "当前存活部队的资源价值", "value"),
        ("workers", "农民数量", "活跃农民数", "count"),
        ("income", "采集效率", "资源采集速率（资源/分钟）", "rate"),
        ("bank", "未花资源", "手头矿+气的存量", "value"),
        ("supply", "人口", "已用人口 / 人口上限", "supply"),
        ("tech", "科技投入", "已投入在科技上的资源", "value"),
        ("losses", "累计损失", "累计损失的资源价值", "value"),
    ]
    timelines = []
    for key, title, desc, kind in defs:
        series = []
        for player in parsed.players:
            if key == "income":
                points = _combine(parsed, player.player_id, "mineral_rate", "vespene_rate")
            elif key == "bank":
                points = _combine(parsed, player.player_id, "minerals", "vespene")
            elif key == "supply":
                used = _stats_series(parsed, player.player_id, "food_used")
                made = _stats_series(parsed, player.player_id, "food_made")
                points = [[a[0], a[1]] for a in used]
                series.append({"label": "%s 人口" % _short_name(player),
                               "color": player.color_hex,
                               "points": points})
                series.append({"label": "%s 上限" % _short_name(player),
                               "color": player.color_hex,
                               "dash": True,
                               "points": [[b[0], b[1]] for b in made]})
                continue
            elif key == "losses":
                points = _combine(parsed, player.player_id, "lost_army", "lost_economy")
            elif key == "army":
                points = _stats_series(parsed, player.player_id, "army_value")
            elif key == "workers":
                points = _stats_series(parsed, player.player_id, "workers")
            else:
                points = _stats_series(parsed, player.player_id, key)
            series.append({"label": _short_name(player), "color": player.color_hex,
                           "points": points})
        timelines.append({"key": key, "title": title, "desc": desc,
                          "kind": kind, "series": series})
    return timelines


def _combine(parsed, player_id, key_a, key_b):
    points = []
    for sample in parsed.stats.get(player_id, []):
        points.append([round(to_seconds(sample["loop"]) / 60.0, 3),
                       sample.get(key_a, 0) + sample.get(key_b, 0)])
    return points


def _short_name(player):
    return player.name if len(player.name) <= 12 else player.name[:11] + "…"


# --------------------------------------------------------------------------- 建造顺序
def _build_order(parsed):
    orders = {}
    for player in parsed.players:
        events = []
        for event in parsed.build_events:
            if event["player_id"] != player.player_id:
                continue
            seconds = to_seconds(event["loop"])
            events.append({
                "t": round(seconds, 1),
                "time": fmt_time(seconds),
                "label": event.get("label") or unitdata.unit_label(event["name"]),
                "name": event.get("name", ""),
                "kind": event.get("kind", "upgrade"),
                "stage": event.get("stage", "army"),
                "minute": int(seconds // 60),
            })
        orders[player.player_id] = events
    return orders


# --------------------------------------------------------------------------- 扩张
NATURAL_NAMES = {"CommandCenter", "Nexus", "Hatchery"}


def _expansions(parsed):
    result = {}
    for player in parsed.players:
        bases = []
        for record in parsed.unit_records:
            if record.player_id != player.player_id:
                continue
            if record.name not in NATURAL_NAMES:
                continue
            start = record.start_loop
            if start is None:
                continue
            bases.append((start, record))
        bases.sort(key=lambda item: item[0])
        entries = []
        for index, (loop, record) in enumerate(bases):
            entries.append({
                "index": index,
                "t": to_seconds(loop),
                "label": "初始基地" if index == 0 else "第 %d 基地" % (index + 1),
                "x": record.x,
                "y": record.y,
            })
        result[player.player_id] = entries
    return result


# --------------------------------------------------------------------------- 人口卡顿
def _supply_blocks(parsed):
    result = {}
    for player in parsed.players:
        samples = parsed.stats.get(player.player_id, [])
        blocks = []
        total = 0.0
        open_since = None
        for index, sample in enumerate(samples):
            used = sample["food_used"]
            made = sample["food_made"]
            capped = made > 0 and used >= made - 0.01 and made < 200
            if capped and open_since is None:
                open_since = sample["loop"]
            elif not capped and open_since is not None:
                duration = to_seconds(sample["loop"] - open_since)
                if duration >= 5:
                    blocks.append({"t": to_seconds(open_since), "duration": duration})
                open_since = None
        # 开局前 1 分钟的人口卡顿属于正常建造节奏，不计入
        blocks = [b for b in blocks if b["t"] > 60]
        total = sum(b["duration"] for b in blocks)
        result[player.player_id] = {
            "total": total,
            "count": len(blocks),
            "worst": max(blocks, key=lambda b: b["duration"]) if blocks else None,
            "blocks": blocks,
        }
    return result


# --------------------------------------------------------------------------- 战斗
def _battles(parsed, gap_seconds=25.0):
    """把单位死亡按时间聚成"战斗"，统计双方损失。"""
    deaths = []
    for record in parsed.unit_records:
        if record.died_loop is None or not record.player_id:
            continue
        if record.is_structure:
            continue
        if unitdata.is_noise(record.name):
            continue
        deaths.append(record)
    deaths.sort(key=lambda r: r.died_loop)

    battles = []
    current = []
    last_loop = None
    for record in deaths:
        if last_loop is not None and (record.died_loop - last_loop) / LOOPS_PER_SECOND > gap_seconds:
            battles.append(current)
            current = []
        current.append(record)
        last_loop = record.died_loop
    if current:
        battles.append(current)

    result = []
    for cluster in battles:
        if len(cluster) < 2:
            continue
        per_player = {}
        for record in cluster:
            cost = record.cost
            value = (cost[0] + cost[1]) if cost else 0
            entry = per_player.setdefault(record.player_id, {"units": 0, "value": 0,
                                                            "names": {}})
            entry["units"] += 1
            entry["value"] += value
            label = unitdata.unit_label(record.name)
            entry["names"][label] = entry["names"].get(label, 0) + 1
        start = to_seconds(cluster[0].died_loop)
        end = to_seconds(cluster[-1].died_loop)
        xs = [r.x for r in cluster if r.x is not None]
        ys = [r.y for r in cluster if r.y is not None]
        result.append({
            "t": start,
            "time": fmt_time(start),
            "duration": end - start,
            "units": len(cluster),
            "per_player": per_player,
            "x": sum(xs) / len(xs) if xs else None,
            "y": sum(ys) / len(ys) if ys else None,
        })
    result.sort(key=lambda b: sum(p["value"] for p in b["per_player"].values()), reverse=True)
    return result


# --------------------------------------------------------------------------- 单位构成
def _composition(parsed):
    """战斗单位构成（农民单独在曲线里体现，这里不重复）。"""
    result = {}
    for player in parsed.players:
        stats = {}
        for record in parsed.unit_records:
            if record.player_id != player.player_id:
                continue
            if record.is_structure or unitdata.is_noise(record.name):
                continue
            if record.name in unitdata.WORKER_UNITS:
                continue
            if unitdata.category(record.name) != "army":
                continue
            entry = stats.setdefault(record.name, {
                "name": record.name,
                "label": unitdata.unit_label(record.name),
                "built": 0, "died": 0, "alive": 0, "value": 0,
            })
            entry["built"] += 1
            cost = record.cost
            if cost:
                entry["value"] += cost[0] + cost[1]
            if record.died_loop is None:
                entry["alive"] += 1
            else:
                entry["died"] += 1
        rows = sorted(stats.values(), key=lambda r: (-r["value"], -r["built"]))
        result[player.player_id] = rows
    return result


# --------------------------------------------------------------------------- 里程碑
MILESTONES = (5, 8, 10, 12, 15, 20)


def _snapshot_at(parsed, player_id, minute):
    samples = parsed.stats.get(player_id, [])
    target = minute * 60.0 * LOOPS_PER_SECOND
    best = None
    for sample in samples:
        if best is None or abs(sample["loop"] - target) < abs(best["loop"] - target):
            best = sample
    return best


def _milestones(parsed):
    minutes = [m for m in MILESTONES if m * 60 <= parsed.duration_seconds]
    rows = []
    for minute in minutes:
        row = {"minute": minute, "cells": {}}
        for player in parsed.players:
            sample = _snapshot_at(parsed, player.player_id, minute)
            row["cells"][player.player_id] = {
                "workers": sample["workers"] if sample else None,
                "army": sample["army_value"] if sample else None,
                "income": (sample["mineral_rate"] + sample["vespene_rate"]) if sample else None,
                "supply": (sample["food_used"], sample["food_made"]) if sample else None,
                "tech": sample["tech_value"] if sample else None,
            }
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- 洞察
def _insights(parsed, ctx):
    insights = []
    players = parsed.players
    if len(players) < 2:
        return insights
    local = ctx["local"]
    rival = ctx["rival"]

    def add(level, title, text, weight=0):
        insights.append({"level": level, "title": title, "text": _tidy(text),
                         "weight": weight})

    duration = parsed.duration_seconds

    # 1) 结果
    if local.result == "win":
        add("good", "结果：胜利", "%s用时 %s 击败 %s。" % (
            _who(local), fmt_time(duration), _quote(rival.name)), 100)
    elif local.result == "loss":
        add("bad", "结果：失利", "%s在 %s 被 %s 击败。" % (
            _who(local), fmt_time(duration), _quote(rival.name)), 100)

    # 2) 农民 / 经济对比
    for minute in (5, 8, 12):
        if minute * 60 > duration:
            continue
        a = _snapshot_at(parsed, local.player_id, minute)
        b = _snapshot_at(parsed, rival.player_id, minute)
        if not a or not b:
            continue
        diff = a["workers"] - b["workers"]
        if abs(diff) >= 5:
            level = "good" if diff > 0 else "warn"
            add(level, "%d 分钟农民差" % minute,
                "%d 分钟时%s %d 个农民，%s %d 个（%s%d）。" % (
                    minute, _who(local), a["workers"], _who(rival), b["workers"],
                    "+" if diff > 0 else "", diff), 40)

    # 3) 收入
    a = _snapshot_at(parsed, local.player_id, max(3, int(duration // 120)))
    b = _snapshot_at(parsed, rival.player_id, max(3, int(duration // 120)))
    if a and b:
        rate_a = a["mineral_rate"] + a["vespene_rate"]
        rate_b = b["mineral_rate"] + b["vespene_rate"]
        if rate_b > rate_a * 1.2 and rate_b - rate_a > 150:
            add("warn", "中后期收入落后",
                "对局中段%s的采集约 %d/分钟，%s约 %d/分钟，经济差距在扩大。" % (
                    _who(local), rate_a, _who(rival), rate_b), 30)

    # 4) 扩张节奏
    exp = ctx["expansions"]
    local_exp = [e for e in exp.get(local.player_id, []) if e["index"] > 0]
    rival_exp = [e for e in exp.get(rival.player_id, []) if e["index"] > 0]
    if local_exp and rival_exp:
        la, rb = local_exp[0]["t"], rival_exp[0]["t"]
        if abs(la - rb) >= 20:
            if la < rb:
                add("good", "开矿更早",
                    "%s的二基地 %s，%s %s，早了 %s。" % (
                        _who(local), fmt_time(la), _who(rival), fmt_time(rb),
                        fmt_time(rb - la)), 45)
            else:
                add("warn", "开矿晚于对手",
                    "%s的二基地 %s，%s %s，晚了 %s。" % (
                        _who(local), fmt_time(la), _who(rival), fmt_time(rb),
                        fmt_time(la - rb)), 45)
    elif local_exp and not rival_exp:
        add("good", "对手没有开矿", "%s在 %s 就开了二基地，对手到结束都没开矿。" % (
            _who(local), fmt_time(local_exp[0]["t"])), 25)
    elif rival_exp and not local_exp:
        add("warn", "你没有开矿", "%s在 %s 就开了二基地，你全程单矿。" % (
            _who(rival), fmt_time(rival_exp[0]["t"])), 25)

    # 5) 军队价值领先/落后
    for lead in _army_leads(parsed, local.player_id, rival.player_id):
        add(lead["level"], lead["title"], lead["text"], lead["weight"])

    # 6) 人口卡顿
    blocks = ctx["supply_blocks"]
    lb = blocks.get(local.player_id)
    if lb and lb["total"] >= 20:
        worst = lb["worst"]
        add("warn", "人口卡顿",
            "%s累计有 %d 秒人口卡满（%d 次），最长一次 %s 发生在 %s。" % (
                _who(local), int(lb["total"]), lb["count"],
                fmt_time(worst["duration"]), fmt_time(worst["t"])),
            35 if lb["total"] < 60 else 55)

    # 7) 闲置资源 & 生产停顿
    idle = _idle_production(parsed, local.player_id)
    if idle:
        add("warn", "生产空窗", idle, 50)

    # 8) 战损
    battles = ctx["battles"]
    if battles:
        biggest = battles[0]
        losses = biggest["per_player"]
        mine = losses.get(local.player_id, {"units": 0, "value": 0})
        theirs = losses.get(rival.player_id, {"units": 0, "value": 0})
        if mine["value"] > theirs["value"] * 1.3 and mine["value"] > 300:
            add("bad", "最亏的一波交战",
                "%s 的交战%s损失约 %d 资源（%d 个单位），%s损失 %d 资源。" % (
                    fmt_time(biggest["t"]), _who(local), mine["value"], mine["units"],
                    _who(rival), theirs["value"]), 70)
        elif theirs["value"] > mine["value"] * 1.3 and theirs["value"] > 300:
            add("good", "最赚的一波交战",
                "%s 的交战%s只损失 %d 资源，%s损失约 %d 资源，净赚 %d。" % (
                    fmt_time(biggest["t"]), _who(local), mine["value"],
                    _who(rival), theirs["value"], theirs["value"] - mine["value"]), 55)

        # 屠农：一波里死 8 个以上农民几乎必然是崩盘点
        worker_labels = {unitdata.unit_label(name) for name in unitdata.WORKER_UNITS}
        for battle in battles[:4]:
            entry = battle["per_player"].get(local.player_id)
            if not entry:
                continue
            lost_workers = sum(count for label, count in entry["names"].items()
                               if label in worker_labels)
            if lost_workers >= 8:
                add("bad", "农民被杀",
                    "%s 的战斗里%s损失了 %d 个农民（约 %d 资源），这是这局最伤的损失。" % (
                        fmt_time(battle["t"]), _who(local), lost_workers,
                        lost_workers * 50), 80)
                break

    # 8b) 总战损比
    totals = ctx["totals"]
    mine_total = totals.get(str(local.player_id), {})
    rival_total = totals.get(str(rival.player_id), {})
    if mine_total.get("killed") and rival_total.get("killed"):
        ratio = mine_total["killed"] / float(max(1, mine_total["lost"]))
        if ratio < 0.85:
            add("warn", "总体战损比",
                "整局你损失 %d 资源、击杀 %d 资源（%.2f:1），对手损失 %d、击杀 %d。" % (
                    mine_total["lost"], mine_total["killed"], ratio,
                    rival_total["lost"], rival_total["killed"]), 65)

    # 9) 操作
    apm = ctx["apm"]
    mine_apm = apm["players"].get(str(local.player_id), {}).get("apm_avg", 0)
    their_apm = apm["players"].get(str(rival.player_id), {}).get("apm_avg", 0)
    if their_apm and mine_apm < their_apm * 0.75 and mine_apm > 0:
        add("warn", "操作频率偏低",
            "%s平均 %.0f APM，%s %.0f APM。" % (_who(local), mine_apm,
                                               _who(rival), their_apm), 20)

    # 10) 单位构成偏向
    comp = ctx["composition"].get(local.player_id, [])
    army_value = sum(row["value"] for row in comp
                     if row["name"] not in unitdata.WORKER_UNITS)
    if army_value > 0:
        top = [row for row in comp if row["name"] not in unitdata.WORKER_UNITS][:3]
        if top:
            add("info", "主力构成",
                "资源占比最高的单位：%s。" % "、".join(
                    "%s（%d 个）" % (row["label"], row["built"]) for row in top), 10)

    # 11) 科技投入
    a = _snapshot_at(parsed, local.player_id, max(3, int(duration // 60) - 1))
    b = _snapshot_at(parsed, rival.player_id, max(3, int(duration // 60) - 1))
    if a and b and b["tech_value"] > a["tech_value"] * 1.5 and b["tech_value"] - a["tech_value"] > 400:
        add("warn", "科技投入落后",
            "结束时%s的科技投入约 %d，%s约 %d。" % (
                _who(rival), b["tech_value"], _who(local), a["tech_value"]), 30)

    insights.sort(key=lambda item: -item["weight"])
    return insights


def _build_summary(parsed, ctx):
    """自动生成一段"这局怎么回事"的叙述。"""
    local = ctx["local"]
    rival = ctx["rival"]
    if local is None or rival is None:
        return ""
    duration = parsed.duration_seconds
    sentences = []

    verb = "赢下" if local.result == "win" else ("输掉" if local.result == "loss" else "打完")
    sentences.append(
        "%s %s 的 1v1 对局，%s（%s）%s了这局。" % (
            fmt_time(duration), parsed.map_name, _who(local), local.race_name,
            verb) if local.is_local else
        "%s %s 的对局，%s（%s）对 %s（%s）。" % (
            fmt_time(duration), parsed.map_name, local.name, local.race_name,
            rival.name, rival.race_name))

    # 经济节奏
    exp = ctx["expansions"]
    local_exp = [e for e in exp.get(local.player_id, []) if e["index"] > 0]
    rival_exp = [e for e in exp.get(rival.player_id, []) if e["index"] > 0]
    if local_exp and rival_exp:
        if local_exp[0]["t"] <= rival_exp[0]["t"]:
            sentences.append("你 %s 开二基地，比对手早 %s。" % (
                fmt_time(local_exp[0]["t"]),
                fmt_time(rival_exp[0]["t"] - local_exp[0]["t"])))
        else:
            sentences.append("对手 %s 先开二基地，你 %s 才跟上。" % (
                fmt_time(rival_exp[0]["t"]), fmt_time(local_exp[0]["t"])))
    elif local_exp:
        sentences.append("你 %s 开了二基地，对手全程没有扩张。" % fmt_time(local_exp[0]["t"]))

    a = _snapshot_at(parsed, local.player_id, 8)
    b = _snapshot_at(parsed, rival.player_id, 8)
    if a and b and abs(a["workers"] - b["workers"]) >= 4:
        sentences.append("8 分钟时农民 %d 对 %d，%s。" % (
            a["workers"], b["workers"],
            "你经济领先" if a["workers"] > b["workers"] else "你经济落后"))

    battles = ctx["battles"]
    if battles:
        biggest = battles[0]
        mine = biggest["per_player"].get(local.player_id, {"value": 0, "units": 0})
        theirs = biggest["per_player"].get(rival.player_id, {"value": 0, "units": 0})
        sentences.append("最大的交火发生在 %s，你损失 %d 个单位（约 %d 资源），对手损失 %d 个（约 %d 资源）。" % (
            fmt_time(biggest["t"]), mine["units"], mine["value"],
            theirs["units"], theirs["value"]))

    leads = [lead for lead in _army_leads(parsed, local.player_id, rival.player_id)
             if lead["level"] == "bad"]
    if leads:
        sentences.append(leads[0]["text"])
    return _tidy("".join(sentences))


# 是否检测到了本机玩家；没检测到时用名字而不是"对手"，避免"对手 vs 对手"
_HAS_LOCAL = True


def _quote(name):
    return "「%s」" % name


def _who(player):
    if player.is_local:
        return "你"
    return "对手" if _HAS_LOCAL else player.name


def _army_leads(parsed, local_id, rival_id):
    """找出"领先最多"和"落后最多"的时刻（排除最后 45 秒的收尾阶段）。"""
    samples_local = parsed.stats.get(local_id, [])
    samples_rival = {round(to_seconds(s["loop"]), 1): s
                     for s in parsed.stats.get(rival_id, [])}
    cutoff = parsed.duration_seconds - 45
    best_pos = None
    best_neg = None
    for sample in samples_local:
        seconds = to_seconds(sample["loop"])
        if seconds > cutoff:
            continue
        other = samples_rival.get(round(seconds, 1))
        if not other:
            continue
        diff = sample["army_value"] - other["army_value"]
        if diff > 0 and (best_pos is None or diff > best_pos[0]):
            best_pos = (diff, seconds)
        if diff < 0 and (best_neg is None or diff < best_neg[0]):
            best_neg = (diff, seconds)

    leads = []
    if best_pos and best_pos[0] >= 400:
        leads.append({"level": "good", "title": "军队价值领先",
                      "text": "%s 你的军队价值领先对手最多约 %d，这是应该主动开战的时间点。"
                              % (fmt_time(best_pos[1]), best_pos[0]), "weight": 55})
    if best_neg and -best_neg[0] >= 400:
        leads.append({"level": "bad", "title": "军队价值落后",
                      "text": "%s 你的军队价值落后最多约 %d，这个时间点不该接团。"
                              % (fmt_time(best_neg[1]), -best_neg[0]), "weight": 60})
    return leads


def _idle_production(parsed, player_id):
    """找"手里有钱但连续很久没造出兵"的空窗。"""
    productions = [e for e in parsed.build_events
                   if e["player_id"] == player_id and e.get("kind") == "unit"
                   and e.get("stage") == "army"]
    if len(productions) < 2:
        return None
    samples = parsed.stats.get(player_id, [])
    worst = None
    for prev, nxt in zip(productions, productions[1:]):
        gap = to_seconds(nxt["loop"] - prev["loop"])
        if gap < 60:
            continue
        bank = 0
        for sample in samples:
            if prev["loop"] <= sample["loop"] <= nxt["loop"]:
                bank = max(bank, sample["minerals"] + sample["vespene"])
        if bank >= 500 and gap >= 75:
            if worst is None or gap > worst[0]:
                worst = (gap, prev["loop"], bank)
    if not worst:
        return None
    return ("%s - %s 之间有 %d 秒没造部队，手头最高积压 %d 资源。" % (
        fmt_time(to_seconds(worst[1])), fmt_time(to_seconds(worst[1]) + worst[0]),
        int(worst[0]), worst[2]))


# --------------------------------------------------------------------------- 主入口
def analyze(parsed):
    """返回报告所需的完整分析结果（可 JSON 序列化）。"""
    global _HAS_LOCAL
    players = parsed.players
    local = next((p for p in players if p.is_local), players[0] if players else None)
    rival = next((p for p in players if p is not local), None)
    _HAS_LOCAL = local is not None and local.is_local

    apm_data = _apm_series(parsed)
    apm = {"series": {}, "players": {}}
    for player in players:
        series = apm_data.get(player.user_id, {"apm": [], "epm": [], "camera": []})
        values = series["apm"][:-1] or series["apm"] or [0]
        apm["series"][str(player.player_id)] = series
        apm["players"][str(player.player_id)] = {
            "apm_avg": round(sum(values) / len(values), 1) if values else 0,
            "apm_peak": round(max(values), 1) if values else 0,
            "epm_avg": round(sum(series["epm"][:-1]) / max(1, len(series["epm"]) - 1), 1),
            "camera_avg": round(sum(series["camera"][:-1]) / max(1, len(series["camera"]) - 1), 1),
            "curve": series["apm"],
        }

    ctx = {
        "local": local,
        "rival": rival,
        "apm": apm,
        "expansions": _expansions(parsed),
        "supply_blocks": _supply_blocks(parsed),
        "battles": _battles(parsed),
        "composition": _composition(parsed),
        "totals": _totals(parsed),
    }
    ctx["insights"] = _insights(parsed, ctx)
    summary = _build_summary(parsed, ctx)

    # 玩家总览卡片
    overview = []
    for player in players:
        samples = parsed.stats.get(player.player_id, [])
        last = samples[-1] if samples else {}
        overview.append({
            "index": player.index,
            "player_id": player.player_id,
            "name": player.name,
            "short_name": _short_name(player),
            "race": player.race,
            "race_name": player.race_name,
            "result": player.result,
            "result_zh": player.result_zh,
            "color": player.color_hex,
            "is_local": player.is_local,
            "apm": apm["players"].get(str(player.player_id), {}).get("apm_avg", 0),
            "apm_peak": apm["players"].get(str(player.player_id), {}).get("apm_peak", 0),
            "epm": apm["players"].get(str(player.player_id), {}).get("epm_avg", 0),
            "workers_max": max((s["workers"] for s in samples), default=0),
            "army_peak": max((s["army_value"] for s in samples), default=0),
            "supply_peak": max((s["food_used"] for s in samples), default=0),
            "lost": (last.get("lost_army", 0) + last.get("lost_economy", 0)
                     + last.get("lost_tech", 0)) if last else 0,
            "killed": (last.get("killed_army", 0) + last.get("killed_economy", 0)
                       + last.get("killed_tech", 0)) if last else 0,
            "bases": len(ctx["expansions"].get(player.player_id, [])),
        })

    return {
        "meta": {
            "map": parsed.map_name,
            "map_author": parsed.map_author,
            "duration_seconds": parsed.duration_seconds,
            "duration": fmt_time(parsed.duration_seconds),
            "game_speed": parsed.game_speed_name,
            "is_ladder": parsed.is_ladder,
            "version": _version_text(parsed.version),
            "build": parsed.version.get("build"),
            "protocol_build": parsed.protocol_build,
            "protocol_note": parsed.protocol_note,
            "date": parsed.replay_time.astimezone().strftime("%Y-%m-%d %H:%M")
                    if parsed.replay_time else "",
            "path": parsed.path,
            "file_size": parsed.file_size,
            "players_count": len(players),
        },
        "players": overview,
        "summary": summary,
        "local": local.to_dict() if local else None,
        "rival": rival.to_dict() if rival else None,
        "timelines": _build_timelines(parsed),
        "apm": apm,
        "build_orders": _build_order(parsed),
        "expansions": {str(k): v for k, v in ctx["expansions"].items()},
        "supply_blocks": {str(k): v for k, v in ctx["supply_blocks"].items()},
        "battles": [_battle_view(b, players) for b in ctx["battles"][:8]],
        "composition": {str(k): v for k, v in ctx["composition"].items()},
        "milestones": _milestone_view(parsed),
        "insights": ctx["insights"],
        "chat": _chat_view(parsed),
        "stats_totals": _totals(parsed),
    }


def _chat_view(parsed):
    rows = []
    by_user = {player.user_id: player for player in parsed.players}
    for message in parsed.chat:
        text = message.get("text") or ""
        if not text.strip():
            continue
        player = by_user.get(message.get("user_id"))
        rows.append({
            "time": fmt_time(to_seconds(message["loop"])),
            "name": player.name if player else "观战者",
            "color": player.color_hex if player else "#93a1b5",
            "text": text,
        })
    return rows


def _version_text(version):
    return "%s.%s.%s (build %s)" % (version.get("major"), version.get("minor"),
                                    version.get("revision"), version.get("build"))


def _battle_view(battle, players):
    rows = []
    for player in players:
        entry = battle["per_player"].get(player.player_id)
        rows.append({
            "name": player.name,
            "short_name": _short_name(player),
            "color": player.color_hex,
            "is_local": player.is_local,
            "units": entry["units"] if entry else 0,
            "value": entry["value"] if entry else 0,
            "names": entry["names"] if entry else {},
        })
    return {
        "time": battle["time"],
        "t": battle["t"],
        "duration": round(battle["duration"], 1),
        "units": battle["units"],
        "rows": rows,
    }


def _milestone_view(parsed):
    rows = []
    for row in _milestones(parsed):
        rows.append({
            "minute": row["minute"],
            "cells": [{"player_id": pid, **cell} for pid, cell in row["cells"].items()],
        })
    return rows


def _totals(parsed):
    totals = {}
    for player in parsed.players:
        samples = parsed.stats.get(player.player_id, [])
        collected = 0.0
        for prev, cur in zip(samples, samples[1:]):
            dt = to_seconds(cur["loop"] - prev["loop"])
            collected += (prev["mineral_rate"] + prev["vespene_rate"]) * dt / 60.0
        totals[str(player.player_id)] = {
            "collected": round(collected),
            "spent_army": samples[-1]["army_value"] if samples else 0,
            "lost": (samples[-1]["lost_army"] + samples[-1]["lost_economy"]
                     + samples[-1]["lost_tech"]) if samples else 0,
            "killed": (samples[-1]["killed_army"] + samples[-1]["killed_economy"]
                       + samples[-1]["killed_tech"]) if samples else 0,
        }
    return totals
