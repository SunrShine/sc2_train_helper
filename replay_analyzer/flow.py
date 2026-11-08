"""整局「流程（flow）」分类：不是 0–3 分钟的开局，而是这一局整体怎么打的。

背景（用户原话）："分析职业录像不能只比数据，因为流程不同，导致时间点可能不同，
所以先分析归类几条流程。" bo.py 的聚类只覆盖 0~3 分钟开局；两局开局一样、后面
一个转机械化、一个两兵营空投，直接比中位数是错的。所以这里先把**整局**归成几类流程，
再在同一类内部比时间点。

本模块只复用 bo.py 的函数（不修改 bo.py）：

* ``bo.extract_bo`` / ``bo.opening_sequence`` / ``bo.opening_token_times``：0~180s 开局序列
* ``bo.army_moveout``：第一次主力离开己方半场（fog 几何推断）

## 封闭词表（TvT 先给 7 类）

| key | 展示名 | 判定（按顺序命中即返回） |
|---|---|---|
| ``one-base-allin`` | 单矿一波 | 12 分钟时基地数 ≤ 1（且对局 ≥ 5 分钟） |
| ``air-flow`` | 空军路线 | 5~10 分钟空军占比 ≥ 30%，或 有聚变核心 / 大和 / 女妖 / 渡鸦 ≥ 2 个 |
| ``mech-macro`` | 机械化多矿运营 | 机械占比 ≥ 55% 且 10 分钟基地数 ≥ 3 |
| ``mech-timing`` | 机械化压制（少矿） | 机械占比 ≥ 55% 且 10 分钟基地数 ≤ 2 |
| ``bio-macro`` | 生化多矿运营 | 生化占比 ≥ 55% 且 10 分钟基地数 ≥ 3 |
| ``bio-timing`` | 生化压制（少矿/timing） | 生化占比 ≥ 55% 且 10 分钟基地数 ≤ 2 |
| ``mixed-flow`` | 生化机械混编 | 不是纯生化/纯机械，但 5~10 分钟确实在正常出兵（≥3 个） |
| ``other`` | 其他/判不出来 | 5~10 分钟几乎没有出兵（<3 个，通常是 5 分钟前就结束的局） |

占比的分母是 5~10 分钟出产的 **bio + mech + air**（医疗运输机等辅助不算）；分类顺序
故意把"单矿"和"空军"放前面：单矿一波即使出兵被算成生化，它的流程身份是"一波"。

## 每局双方都标

``analyze_matchup(paths, matchup)`` 对每个 replay 的**两个玩家**分别抽特征+分类
（TvT 双方都是 T）。我的局 role=``me`` / ``opponent``；职业局 role=``pro`` /
``pro_opponent``。

## playable

本机能否直接播放看的是 SC2 客户端 ``Versions/Base<build>`` 目录里**有没有程序文件**：
实测 ``D:\\StarCraft II\\Versions`` 有 18 个 ``Base*`` 目录，但只有 **Base93333 /
Base98370** 真的有文件，其余（95405…97579、96163）都是 0 文件空壳，照样播不了。
所以 ``playable`` 只认"探测到 + 目录非空"的版本；探测不到 Versions 时才退回
"build ≥ 93333" 的粗略判断。

职业语料有两个来源：

* ``replays/pro/<选手>/``：按文件夹认选手（``bo.PRO_NON_PLAYER_DIRS`` 以外的目录）；
* ``replays/pro/tvt/``：批量下载的 TvT 对位合集，**双方都标 role="pro"**；
  里面对应 build 93333 的那几局能本机播放，用来核对命名。这个目录**不是选手目录**，
  bo 按选手抽样时会跳过（``bo.PRO_NON_PLAYER_DIRS``），避免把 "tvt" 当选手名。

## 职业身份门槛（比 playable 重要）

**能播放 ≠ 是职业**。职业样本只收：

* ``replays/pro/tvt/`` 里 manifest ``quality == "pro"`` 的局（ladder/路人局即使能播也不收）；
* ``replays/pro/<选手>/`` 里**目录名能匹配到对局中选手名**的局。

不合格的局直接丢，并逐条记进 ``meta.excluded_non_pro``（file/path/reason/detail），
不让它们进聚类、统计和示例。
"""

import glob
import json
import math
import os
import statistics
import sys
import time

from . import bo
from .loader import load_replay

#: 本机 SC2 客户端支持的最低 replay build（Versions/Base93333 起）。
MIN_PLAYABLE_BUILD = 93333

#: 可能的 SC2 安装目录（只读探测，不写）。**目录存在不等于能播**：实测
#: ``Versions/Base95405`` 之类是 0 文件的空壳，只有真正装着程序文件的版本目录
#: 才算出可播放版本（本机实测只有 Base93333 / Base98370 有文件）。
SC2_VERSION_ROOTS = (
    r"D:\StarCraft II\Versions",
    r"C:\Program Files (x86)\StarCraft II\Versions",
    r"C:\Program Files\StarCraft II\Versions",
    r"E:\StarCraft II\Versions",
)

#: 职业语料里"批量下载的对位合集"目录（不是选手目录）：flow 把它当职业来源，
#: 但 bo 按选手抽样要跳过它（bo.PRO_NON_PLAYER_DIRS）。
PRO_TVT_DIR = os.path.join(bo.PRO_DIR, "tvt")

#: 职业身份门槛：**能播放 ≠ 是职业**。职业样本只收两种情况（见 ``analyze_game``）：
#:   * ``replays/pro/tvt/`` 里 manifest ``quality == "pro"``；
#:   * ``replays/pro/<选手>/`` 且目录名能与对局里的选手名对上。
#: 不满足的直接丢，并记进 ``meta.excluded_non_pro``。
IDENTITY_RULE = ("tvt 合集：manifest.quality == 'pro'；选手目录：目录名必须匹配对局里的选手名。"
                 "两条都不满足的局不进职业样本。")


class NotProGame(Exception):
    """不是职业局（身份门槛没过），用于把局丢进 excluded_non_pro。"""

    def __init__(self, reason, detail=""):
        Exception.__init__(self, reason)
        self.reason = reason
        self.detail = detail

#: 兵种分类（5~10 分钟出产的单位）。
BIO_UNITS = frozenset(("Marine", "Marauder", "Reaper", "Ghost"))
MECH_UNITS = frozenset(("Hellion", "Hellbat", "SiegeTank", "Cyclone", "Thor", "WidowMine"))
AIR_UNITS = frozenset(("Viking", "Banshee", "Raven", "Liberator", "Battlecruiser"))
SUPPORT_UNITS = frozenset(("Medivac",))

#: 科技选择里要看的兵种（整局是否出过）。
TECH_UNIT_FLAGS = {
    "banshee": "Banshee", "raven": "Raven", "viking": "Viking",
    "battlecruiser": "Battlecruiser", "liberator": "Liberator",
    "siege_tank": "SiegeTank", "hellion": "Hellion", "cyclone": "Cyclone",
    "thor": "Thor", "widow_mine": "WidowMine",
    "ghost": "Ghost", "marauder": "Marauder",
}
TECH_BUILDING_FLAGS = {
    "engineering_bay": "EngineeringBay", "armory": "Armory",
    "ghost_academy": "GhostAcademy", "fusion_core": "FusionCore",
}
TECH_UPGRADE_FLAGS = {
    "stim": ("Stimpack",),
    "combat_shield": ("ShieldWall",),
    "infantry_weapons": ("TerranInfantryWeaponsLevel1",),
    "vehicle_weapons": ("TerranVehicleWeaponsLevel1",),
}

#: 5~10 分钟统计窗口（秒）。
FLOW_WINDOW = (300.0, 600.0)
#: 基地数采样点（秒）。
BASE_SNAPSHOTS = (480.0, 600.0, 720.0)
#: 第一次进攻：≥5 个战斗单位进入敌方主基地多少格内。
ATTACK_RADIUS = 40.0
ATTACK_MIN_UNITS = 5

#: 展示名。
FLOW_NAMES = {
    "one-base-allin": "单矿一波",
    "air-flow": "空军路线（女妖/渡鸦/维京/大和）",
    "mech-macro": "机械化多矿运营",
    "mech-timing": "机械化压制（少矿）",
    "bio-macro": "生化多矿运营",
    "bio-timing": "生化压制（少矿/timing）",
    "mixed-flow": "生化机械混编",
    "other": "其他/判不出来",
}


# --------------------------------------------------------------------------- 本机版本

def _local_version_builds():
    """本机 SC2 客户端**真正装了程序文件**的 replay 版本（Versions/Base<build>）。

    只统计目录里至少有一个文件的版本：实测 ``D:\\StarCraft II\\Versions`` 里 18 个目录
    只有 Base93333 / Base98370 有文件，其余是 0 文件空壳——空壳目录是播不了的。
    """
    builds = set()
    for root in SC2_VERSION_ROOTS:
        try:
            entries = os.listdir(root)
        except Exception:
            continue
        for name in entries:
            if not (name.lower().startswith("base") and name[4:].isdigit()):
                continue
            path = os.path.join(root, name)
            try:
                if not os.path.isdir(path):
                    continue
                if any(os.path.isfile(os.path.join(path, child))
                       for child in os.listdir(path)[:20]):
                    builds.add(int(name[4:]))
            except Exception:
                continue
    return builds


_LOCAL_BUILDS = None


def local_playable_builds():
    """把本机可播放的 build 集合暴露出来（报告里用）。"""
    global _LOCAL_BUILDS
    if _LOCAL_BUILDS is None:
        _LOCAL_BUILDS = _local_version_builds()
    return sorted(_LOCAL_BUILDS)


def playable_build(build):
    """build 能否被本机客户端直接播放。

    优先用探测到的 Versions/Base<build>（只认有文件的那几个）；探测不到才退回
    "build ≥ 93333" 的粗略判断。
    """
    builds = local_playable_builds()
    if builds:
        return bool(build and int(build) in builds)
    return bool(build and int(build) >= MIN_PLAYABLE_BUILD)


# --------------------------------------------------------------------------- 特征

def _sec(loop):
    return (loop or 0) / 16.0


def _median(values):
    values = [v for v in values if v is not None]
    return round(float(statistics.median(values)), 1) if values else None


def _share(counts):
    total = counts.get("bio", 0) + counts.get("mech", 0) + counts.get("air", 0)
    if not total:
        return {"bio": 0.0, "mech": 0.0, "air": 0.0, "total": 0}
    return {"bio": round(counts.get("bio", 0) / float(total), 3),
            "mech": round(counts.get("mech", 0) / float(total), 3),
            "air": round(counts.get("air", 0) / float(total), 3),
            "total": total}


def _unit_counts(parsed, player_id, low, high):
    """窗口内出产的兵，按 bio / mech / air / support 分类计数。"""
    counts = {"bio": 0, "mech": 0, "air": 0, "support": 0}
    names = {}
    for event in parsed.build_events:
        if event.get("player_id") != player_id or event.get("kind") != "unit":
            continue
        seconds = _sec(event.get("loop"))
        if not (low <= seconds < high):
            continue
        name = event.get("name") or ""
        if name in BIO_UNITS:
            counts["bio"] += 1
        elif name in MECH_UNITS:
            counts["mech"] += 1
        elif name in AIR_UNITS:
            counts["air"] += 1
        elif name in SUPPORT_UNITS:
            counts["support"] += 1
        else:
            continue
        names[name] = names.get(name, 0) + 1
    return counts, names


def _structure_counts(parsed, player_id, deadline):
    """deadline 前造下去的生产建筑 / 挂件数量。"""
    production = {"Barracks": 0, "Factory": 0, "Starport": 0}
    addons = {"reactor": 0, "techlab": 0}
    for event in parsed.build_events:
        if event.get("player_id") != player_id or event.get("kind") != "structure":
            continue
        if _sec(event.get("loop")) > deadline:
            continue
        name = event.get("name") or ""
        if name in production:
            production[name] += 1
        elif name.endswith("Reactor"):
            addons["reactor"] += 1
        elif name.endswith("TechLab"):
            addons["techlab"] += 1
    return production, addons


def _base_count(parsed, player_id, deadline):
    """deadline 时的基地数（初始 1 + 该时刻前下过的 基地建筑）。"""
    extra = 0
    for event in parsed.build_events:
        if event.get("player_id") != player_id or event.get("kind") != "structure":
            continue
        if event.get("name") not in bo.TOWNHALL_BUILDS:
            continue
        seconds = _sec(event.get("loop"))
        if 0 < seconds <= deadline:
            extra += 1
    return 1 + extra


def _tech_flags(parsed, player_id):
    """整局是否出过某兵种 / 某科技建筑 / 某升级。"""
    unit_flags = dict((key, False) for key in TECH_UNIT_FLAGS)
    building_flags = dict((key, False) for key in TECH_BUILDING_FLAGS)
    upgrade_flags = dict((key, False) for key in TECH_UPGRADE_FLAGS)
    starports = 0
    for event in parsed.build_events:
        if event.get("player_id") != player_id:
            continue
        name = event.get("name") or ""
        if event.get("kind") == "unit":
            for key, unit_name in TECH_UNIT_FLAGS.items():
                if name == unit_name:
                    unit_flags[key] = True
        elif event.get("kind") == "structure":
            for key, building in TECH_BUILDING_FLAGS.items():
                if name == building:
                    building_flags[key] = True
            if name == "Starport":
                starports += 1
        elif event.get("kind") == "upgrade":
            for key, names in TECH_UPGRADE_FLAGS.items():
                if name in names:
                    upgrade_flags[key] = True
    flags = {}
    flags.update(unit_flags)
    flags.update(building_flags)
    flags.update(upgrade_flags)
    flags["starport_count"] = starports
    return flags


def _main_base(parsed, player_id):
    for record in parsed.unit_records:
        if (record.player_id == player_id
                and record.name in ("CommandCenter", "Nexus", "Hatchery", "Lair", "Hive")
                and record.x is not None and (record.start_loop or 0) <= 0):
            return (record.x, record.y)
    for record in parsed.unit_records:
        if (record.player_id == player_id
                and record.name in ("CommandCenter", "Nexus", "Hatchery", "Lair", "Hive")
                and record.x is not None):
            return (record.x, record.y)
    return None


def first_attack(parsed, player, limit=None):
    """第一次进攻对方基地的时刻：≥5 个战斗单位进入对方主基地 40 格内。

    复用 fog.Model 的位置采样（和 bo.army_moveout 同一个数据源），算不出来给 None。
    """
    try:
        from . import fog
        model = fog.Model(parsed)
    except Exception:
        return None
    target = None
    for other in parsed.players:
        if other.player_id == player.player_id:
            continue
        target = _main_base(parsed, other.player_id)
        if target:
            break
    if not target:
        return None
    deadline = min(parsed.duration_seconds, 1500.0 if limit is None else limit)
    seconds = 60.0
    while seconds <= deadline:
        try:
            units = model.units_at(player.player_id, seconds)
        except Exception:
            return None
        hits = 0
        for record, x, y in units:
            try:
                if model.kind_at(record, seconds) != "army":
                    continue
            except Exception:
                continue
            if math.hypot(x - target[0], y - target[1]) <= ATTACK_RADIUS:
                hits += 1
                if hits >= ATTACK_MIN_UNITS:
                    return round(seconds, 1)
        seconds += 10.0
    return None


def extract_flow(parsed, player, matchup="TvT"):
    """单个玩家的整局流程特征。

    字段：
      * ``opening`` / ``opening_tokens``：0~180s 开局序列（复用 bo）
      * ``production_5_10`` / ``addons_5_10``：到 10 分钟为止的生产建筑与挂件
      * ``units_5_10`` / ``shares_5_10``：5~10 分钟出产兵种的分类计数与占比
      * ``bases_8m`` / ``bases_10m`` / ``bases_12m``
      * ``tech``：兵种/建筑/升级三组布尔 + 星港数量
      * ``army_moveout``（bo 的"主力离开己方半场"）/ ``first_attack``（打到对方主基地）
      * ``result`` / ``duration`` / ``build`` 等元信息
    """
    game_bo = bo.extract_bo(parsed, player, with_moveout=True)
    opening_times = bo.opening_token_times(game_bo)
    counts, unit_names = _unit_counts(parsed, player.player_id, *FLOW_WINDOW)
    units_window = list(FLOW_WINDOW)
    if counts["bio"] + counts["mech"] + counts["air"] < 3:
        # 对局太短（5 分钟前就结束）时，退化成 3~6 分钟窗口再数一次
        fallback = _unit_counts(parsed, player.player_id, 180.0, 360.0)
        if fallback[0]["bio"] + fallback[0]["mech"] + fallback[0]["air"] > sum(
                counts[key] for key in ("bio", "mech", "air")):
            counts, unit_names = fallback
            units_window = [180.0, 360.0]
    production, addons = _structure_counts(parsed, player.player_id, 600.0)
    bases = dict((int(snapshot), _base_count(parsed, player.player_id, snapshot))
                 for snapshot in BASE_SNAPSHOTS)
    features = {
        "name": player.name,
        "pid": player.player_id,
        "race": player.race,
        "opening": "-".join(bo.opening_sequence(game_bo)) or "-",
        "opening_tokens": bo.opening_sequence(game_bo),
        "opening_times": dict((token, seconds) for token, seconds in opening_times.items()),
        "production_5_10": production,
        "addons_5_10": addons,
        "units_5_10": dict(counts),
        "units_window": units_window,
        "unit_names_5_10": unit_names,
        "shares_5_10": _share(counts),
        "bases_8m": bases[480],
        "bases_10m": bases[600],
        "bases_12m": bases[720],
        "tech": _tech_flags(parsed, player.player_id),
        "army_moveout": game_bo["key_timings"].get("army_moveout"),
        "first_attack": first_attack(parsed, player),
        "result": player.result,
        "duration": round(parsed.duration_seconds, 1),
        "build": parsed.version.get("build") or 0,
        "matchup": matchup,
    }
    return features


# --------------------------------------------------------------------------- 分类

def classify_flow(features):
    """按封闭词表给整局流程打标签（规则见模块 docstring 的表格，按顺序命中即返回）。

    ``features`` 是 :func:`extract_flow` 的返回；返回 ``(label, reason)``，reason 是
    一句人话解释"为什么归到这一类"，方便核对。
    """
    shares = features.get("shares_5_10") or {}
    bio = shares.get("bio", 0.0)
    mech = shares.get("mech", 0.0)
    air = shares.get("air", 0.0)
    total = shares.get("total", 0)
    tech = features.get("tech") or {}
    bases_10 = features.get("bases_10m") or 1
    bases_12 = features.get("bases_12m") or 1
    duration = features.get("duration") or 0.0

    if bases_12 <= 1 and duration >= 300.0:
        return "one-base-allin", "12 分钟只有 1 个基地（单矿一波）"

    air_units = sum(1 for key in ("banshee", "raven", "battlecruiser")
                    if tech.get(key))
    if (total >= 6 and air >= 0.30) or tech.get("fusion_core") or air_units >= 2:
        return "air-flow", "空军占比 %.0f%% / 聚变核心=%s / 空军单位 %d 种" % (
            air * 100.0, tech.get("fusion_core"), air_units)

    if mech >= 0.55:
        if bases_10 >= 3:
            return "mech-macro", "机械占比 %.0f%% + 10 分钟 %d 基地" % (mech * 100.0, bases_10)
        return "mech-timing", "机械占比 %.0f%% + 10 分钟只有 %d 基地" % (mech * 100.0, bases_10)

    if bio >= 0.55:
        if bases_10 >= 3:
            return "bio-macro", "生化占比 %.0f%% + 10 分钟 %d 基地" % (bio * 100.0, bases_10)
        return "bio-timing", "生化占比 %.0f%% + 10 分钟只有 %d 基地" % (bio * 100.0, bases_10)

    if total >= 3:
        # 既不是纯生化也不是纯机械、但确实在正常出兵：混编流程（算已知类，不算 unknown）
        return "mixed-flow", "生化 %.0f%% / 机械 %.0f%% / 空军 %.0f%%（混编）" % (
            bio * 100.0, mech * 100.0, air * 100.0)
    return "other", "5~10 分钟几乎没有出兵（%d 个）" % total


# --------------------------------------------------------------------------- 分析一局

def analyze_game(path, matchup="TvT", source="mine", role="me",
                 pro_bucket=None, corpus=None, identity_required=False):
    """解析一局，给**双方**各抽流程特征并分类，返回一条游戏记录。

    ``source="pro"`` + ``pro_bucket="tvt"`` 表示来自 ``replays/pro/tvt/`` 这种
    批量对位合集：双方都是 T，都标 ``role="pro"``（不区分谁是目标选手）。
    ``identity_required=True`` 时先过**职业身份门槛**，不合格抛 :class:`NotProGame`：
    能播放不代表是职业，路人局不能混进职业样本。
    """
    parsed = load_replay(path, decode_events=True)
    if identity_required:
        if pro_bucket == "tvt":
            quality = (corpus or {}).get("quality")
            if quality != "pro":
                raise NotProGame("tvt-manifest-quality-not-pro", str(quality))
        elif pro_bucket == "player_dir":
            if bo._player_from_folder(parsed, path) is None:
                raise NotProGame("folder-name-not-a-player",
                                 os.path.basename(os.path.dirname(os.path.abspath(path))))
    target_race = matchup[0] if matchup else "T"
    players = [player for player in parsed.players if player.race == target_race]
    if matchup.endswith("v" + target_race):
        # 镜像战（TvT）：必须正好两个同族玩家，否则不是 1v1，跳过
        if len(players) != 2:
            return None
    elif not players:
        return None
    if source == "pro" and pro_bucket == "tvt":
        # 批量下载的对位合集：双方都当职业选手处理
        roles = dict((player.player_id, "pro") for player in players)
    elif source == "pro":
        primary = bo._player_from_folder(parsed, path)
        if primary is None:
            primary = next((player for player in players if player.is_local), players[0])
        roles = {}
        for player in players:
            roles[player.player_id] = "pro" if player is primary else "pro_opponent"
    else:
        local = next((player for player in players if player.is_local), players[0])
        roles = {}
        for player in players:
            roles[player.player_id] = "me" if player is local else "opponent"

    records = []
    for player in players:
        features = extract_flow(parsed, player, matchup)
        label, reason = classify_flow(features)
        records.append({
            "pid": player.player_id,
            "name": player.name,
            "role": roles.get(player.player_id, "?"),
            "result": player.result,
            "flow": label,
            "flow_name": FLOW_NAMES.get(label, label),
            "flow_reason": reason,
            "features": features,
        })
    build = parsed.version.get("build") or 0
    replay_day = bo._replay_day(parsed)
    return {
        "file": os.path.basename(path),
        "path": os.path.abspath(path),
        "map": parsed.map_name,
        "date": replay_day,
        "source": source,
        "pro_bucket": pro_bucket,
        "build": build,
        "playable": playable_build(build),
        "corpus": corpus,
        "duration": round(parsed.duration_seconds, 1),
        "players": records,
    }


def analyze_matchup(paths, matchup="TvT", source="mine", progress=None, max_games=None,
                    pro_bucket=None, corpus_by_file=None, identity_required=False):
    """把一批 replay 逐局分析（双方都标），返回 ``[game_record]``。

    ``max_games`` 给了就只保留前 N 个**成功解析的 1v1 局**（非 1v1 的会跳过，
    所以可以多喂一些候选路径来补足数量）。
    ``corpus_by_file`` 是 corpus manifest 的 ``{文件名: 条目}``，会把 quality/playable
    的原始判断一起写进 JSON（和自己探测的 playable 对照，方便发现分歧）。
    ``identity_required=True`` 时，职业身份不合格的局会被丢进
    ``analyze_matchup.last_excluded``（供 ``meta.excluded_non_pro`` 用）。
    """
    games = []
    errors = []
    excluded = []
    corpus_by_file = corpus_by_file or {}
    for index, path in enumerate(paths):
        usage_role = "me" if source == "mine" else "pro"
        corpus = corpus_by_file.get(os.path.basename(path))
        try:
            game = analyze_game(path, matchup=matchup, source=source, role=usage_role,
                                pro_bucket=pro_bucket, corpus=corpus,
                                identity_required=identity_required)
        except NotProGame as exc:
            excluded.append({
                "file": os.path.basename(path),
                "path": os.path.abspath(path),
                "source": source,
                "pro_bucket": pro_bucket,
                "reason": exc.reason,
                "detail": exc.detail,
            })
            continue
        except Exception as exc:                       # 坏档 / 版本不支持
            errors.append({"file": os.path.basename(path), "error": str(exc)[:160]})
            continue
        if game is None:
            errors.append({"file": os.path.basename(path), "error": "not 1v1 / no target race"})
            continue
        games.append(game)
        if progress:
            progress(len(games), len(paths), os.path.basename(path))
        if max_games is not None and len(games) >= max_games:
            break
    analyze_matchup.last_errors = errors
    analyze_matchup.last_excluded = excluded
    return games


analyze_matchup.last_errors = []
analyze_matchup.last_excluded = []


# --------------------------------------------------------------------------- 汇总

def _flow_fingerprint(players):
    """一个 flow 的"特征指纹"：中位扩张/生产/构成/出门 + 最常见开局序列。"""
    def med(key, records=players):
        return _median([(record["features"] or {}).get(key) for record in records])

    def med_nested(group, key):
        values = []
        for record in players:
            value = ((record["features"] or {}).get(group) or {}).get(key)
            if value is not None:
                values.append(value)
        return _median(values)

    openings = {}
    for record in players:
        opening = (record["features"] or {}).get("opening") or "-"
        openings[opening] = openings.get(opening, 0) + 1
    top_openings = sorted(openings.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    advanced = [record for record in players if record["features"].get("first_attack") is not None]
    return {
        "games": len(players),
        "bases_8m": med("bases_8m"),
        "bases_10m": med("bases_10m"),
        "bases_12m": med("bases_12m"),
        "army_moveout": med("army_moveout"),
        "first_attack": med("first_attack"),
        "production_5_10": {
            "Barracks": med_nested("production_5_10", "Barracks"),
            "Factory": med_nested("production_5_10", "Factory"),
            "Starport": med_nested("production_5_10", "Starport"),
        },
        "shares_5_10": {
            "bio": med_nested("shares_5_10", "bio"),
            "mech": med_nested("shares_5_10", "mech"),
            "air": med_nested("shares_5_10", "air"),
        },
        "top_openings": [{"opening": opening, "games": count}
                         for opening, count in top_openings],
        "attack_share": round(len(advanced) / float(len(players)), 3) if players else None,
    }


def _distance_to_median(player_record, fingerprint):
    """代表性排序用：到该 flow 中位数有多远（越小越典型）。"""
    features = player_record["features"]
    pairs = (("bases_10m", fingerprint.get("bases_10m")),
             ("army_moveout", fingerprint.get("army_moveout")),
             ("first_attack", fingerprint.get("first_attack")))
    total = 0.0
    for key, target in pairs:
        value = features.get(key)
        if value is None or target is None:
            total += 1.0
        else:
            total += min(2.0, abs(value - target) / 120.0)
    return total


def _pick_examples(players, fingerprint, games_by_key, limit=3):
    """每个 flow 挑 2~3 个代表性 replay。

    用户要开视频逐个核对命名，所以排序原则：
    1. **能本机播放的优先**；
    2. 可播放的**职业局优先占一个名额**（replays/pro/tvt/ 里那批 93333 的，
       用户想拿职业录像核对命名）；
    3. 其余按"离该 flow 中位数最近"补齐；同一路径只出现一次。
    """
    ranked = []
    for player_record in players:
        game = games_by_key.get(player_record["_game_key"])
        if game is None:
            continue
        ranked.append((game, player_record,
                       _distance_to_median(player_record, fingerprint)))
    ranked.sort(key=lambda item: (0 if item[0]["playable"] else 1, item[2]))
    playable_pro = [item for item in ranked
                    if item[0]["playable"] and item[0]["source"] == "pro"]
    pro_any = [item for item in ranked if item[0]["source"] == "pro"]

    ordered = []
    if playable_pro:
        ordered.append(playable_pro[0])                 # 先占一个可播放职业示例
    elif pro_any:
        # 这个 flow 没有可播放的职业局：退一条**不可播放的职业局**并如实标 playable=false
        ordered.append(pro_any[0])
    ordered.extend(ranked)

    examples = []
    seen = set()
    for game, player_record, _dist in ordered:
        if game["path"] in seen:
            continue
        seen.add(game["path"])
        examples.append({
            "path": game["path"],
            "file": game["file"],
            "source": game["source"],
            "pro_bucket": game.get("pro_bucket"),
            "role": player_record["role"],
            "player": player_record["name"],
            "build": game["build"],
            "playable": game["playable"],
            "date": game["date"],
            "map": game["map"],
            "reason": player_record["flow_reason"],
        })
        if len(examples) >= limit:
            break
    return examples


def summarize_flows(games, min_games=3):
    """按 flow 汇总：局数/占比/特征指纹/代表性 replay。少于 ``min_games`` 的并入 other。"""
    players = []
    games_by_key = {}
    for index, game in enumerate(games):
        games_by_key[index] = game
        for player_record in game["players"]:
            player_record["_game_key"] = index
            players.append(player_record)

    # 少于 3 局的 flow 合并进 other（验收要求）
    counts = {}
    for record in players:
        counts[record["flow"]] = counts.get(record["flow"], 0) + 1
    merged = [label for label, count in counts.items()
              if label != "other" and count < min_games]
    for record in players:
        if record["flow"] in merged:
            record["flow_merged_from"] = record["flow"]
            record["flow"] = "other"
            record["flow_name"] = FLOW_NAMES["other"]

    groups = {}
    for record in players:
        groups.setdefault(record["flow"], []).append(record)
    total = float(len(players)) or 1.0
    flows = []
    for label, records in groups.items():
        fingerprint = _flow_fingerprint(records)
        unique_games = len({record["_game_key"] for record in records})
        flows.append({
            "key": label,
            "name": FLOW_NAMES.get(label, label),
            "player_games": len(records),
            "games": unique_games,
            "share": round(len(records) / total, 3),
            "fingerprint": fingerprint,
            "examples": _pick_examples(records, fingerprint, games_by_key, limit=3),
            "merged_from": sorted(set(record.get("flow_merged_from")
                                      for record in records
                                      if record.get("flow_merged_from"))),
        })
    flows.sort(key=lambda item: (-item["player_games"], item["key"]))
    return flows, total


def _read_manifest(path):
    """读 corpus 写的 manifest.json（``{文件名: 条目}``）；没有就返回空 dict。"""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    if isinstance(data, list):
        return dict((entry.get("file"), entry) for entry in data if entry.get("file"))
    if isinstance(data, dict):
        return data
    return {}


def _manifest_playable_counts(manifest):
    counts = {}
    for entry in manifest.values():
        value = entry.get("playable") if isinstance(entry, dict) else None
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def build_report(mine_dir=None, pro_dir=None, matchup="TvT", limit=60, pro_limit=30,
                 seed=20261108, progress=None):
    """挖矿（我的 + 职业）→ 每局双方分类 → 汇总，返回 reports/tvt-flows.json 的内容。

    职业来源有两处：
      * ``replays/pro/<选手>/``：按文件夹认选手（bo.PRO_NON_PLAYER_DIRS 以外的目录）；
      * ``replays/pro/tvt/``：批量下载的 TvT 对位合集，**双方都标 role="pro"**，
        里那几局 build 93333 的可以本机播放，用来核对命名。
    """
    started = time.time()
    mine_dir = mine_dir or bo.MINE_DIR
    pro_dir = pro_dir or bo.PRO_DIR
    sources = {}

    mine_games = bo.mine_matchups(
        mine_dir, matchups=(matchup,), limit=limit + 15, seed=seed,
        ladder_only=True, min_duration=120.0, require_local=True, exclude_ai=True,
        start_workers_only=12)
    mine_bos = mine_games[0].get(matchup) or []
    mine_paths = [game_bo["game"]["path"] for game_bo in mine_bos]
    sources["mine"] = {"candidates": len(mine_paths)}

    pro_games = bo.mine_matchups(
        pro_dir, matchups=(matchup,), limit=pro_limit, seed=seed,
        folder_player=True, start_workers_only=12)
    pro_bos = pro_games[0].get(matchup) or []
    pro_paths = [game_bo["game"]["path"] for game_bo in pro_bos]
    sources["pro_player_dirs"] = {"candidates": len(pro_paths)}

    # 批量下载的对位合集目录（replays/pro/tvt/）：只收 manifest quality == "pro" 的，
    # 路人/ladder 局即使能播也不进职业样本（能播 ≠ 是职业）。
    manifest = _read_manifest(os.path.join(PRO_TVT_DIR, "manifest.json"))
    tvt_paths, tvt_excluded = [], []
    for path in bo.expand_paths(PRO_TVT_DIR):
        if os.path.basename(os.path.dirname(path)).lower() != "tvt":
            continue
        entry = manifest.get(os.path.basename(path)) or {}
        if entry.get("quality") == "pro":
            tvt_paths.append(path)
        else:
            tvt_excluded.append({
                "file": os.path.basename(path),
                "path": os.path.abspath(path),
                "source": "pro",
                "pro_bucket": "tvt",
                "reason": "tvt-manifest-quality-not-pro",
                "detail": entry.get("quality"),
            })
    sources["pro_tvt"] = {"candidates": len(tvt_paths) + len(tvt_excluded),
                          "manifest": len(manifest),
                          "playable_claim": _manifest_playable_counts(manifest),
                          "excluded_non_pro": len(tvt_excluded)}

    def sub_progress(done, total, name):
        if progress:
            progress(done, total, name)

    games = []
    excluded_non_pro = list(tvt_excluded)
    games.extend(analyze_matchup(mine_paths, matchup, source="mine",
                                 progress=sub_progress, max_games=limit))
    games.extend(analyze_matchup(pro_paths, matchup, source="pro",
                                 progress=sub_progress, max_games=pro_limit,
                                 pro_bucket="player_dir", identity_required=True))
    excluded_non_pro.extend(analyze_matchup.last_excluded)
    games.extend(analyze_matchup(tvt_paths, matchup, source="pro",
                                 progress=sub_progress,
                                 pro_bucket="tvt", corpus_by_file=manifest,
                                 identity_required=True))
    excluded_non_pro.extend(analyze_matchup.last_excluded)
    mine_count = len([game for game in games if game["source"] == "mine"])
    sources["mine"]["games"] = mine_count
    pro_dir_count = len([game for game in games
                         if game["source"] == "pro" and game.get("pro_bucket") == "player_dir"])
    sources["pro_player_dirs"]["games"] = pro_dir_count
    sources["pro_tvt"]["games"] = len([game for game in games
                                       if game.get("pro_bucket") == "tvt"])
    # 同一个文件可能同时出现在两个职业文件夹里，按绝对路径去重
    seen, unique = set(), []
    for game in games:
        if game["path"] in seen:
            continue
        seen.add(game["path"])
        unique.append(game)
    games = unique

    flows, player_total = summarize_flows(games, min_games=3)
    players = [player for game in games for player in game["players"]]
    unknown_count = len([record for record in players if record["flow"] == "other"])
    playable_games = len([game for game in games if game["playable"]])
    playable_pro = len([game for game in games
                        if game["playable"] and game["source"] == "pro"])
    playable_mine = len([game for game in games
                         if game["playable"] and game["source"] == "mine"])
    for flow in flows:
        flow["examples_playable"] = len([e for e in flow["examples"] if e.get("playable")])
        flow["examples_playable_pro"] = len(
            [e for e in flow["examples"] if e.get("playable") and e.get("source") == "pro"])
    return {
        "meta": {
            "generated": time.strftime("%Y-%m-%d"),
            "matchup": matchup,
            "games": len(games),
            "player_games": len(players),
            "sources": sources,
            "playable_games": playable_games,
            "playable_mine": playable_mine,
            "playable_pro": playable_pro,
            "local_playable_builds": local_playable_builds(),
            "playable_rule": ("本机 SC2 客户端 Versions/Base<build> 里**有程序文件**的版本才算"
                              "（空壳目录不算）；探测不到才退回 build ≥ %d" % MIN_PLAYABLE_BUILD),
            "min_playable_build": MIN_PLAYABLE_BUILD,
            "identity_rule": IDENTITY_RULE,
            "excluded_non_pro": excluded_non_pro,
            "excluded_non_pro_count": len(excluded_non_pro),
            "unknown_share": round(unknown_count / float(player_total or 1), 3),
            "merged_flows": sorted({label for flow in flows for label in flow["merged_from"]}),
            "vocabulary": FLOW_NAMES,
            "rules": ("单矿(12 分钟 ≤1 基地) → 空军(占比≥30% 或聚变核心/空军单位≥2 种) → "
                      "机械(≥55%, 多矿/少矿) → 生化(≥55%, 多矿/少矿) → 混编 → other"),
            "elapsed_seconds": round(time.time() - started, 1),
        },
        "flows": flows,
        "games": games,
        "errors": analyze_matchup.last_errors,
    }


# --------------------------------------------------------------------------- CLI

def write_report(report, output_path):
    directory = os.path.dirname(os.path.abspath(output_path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    return output_path


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m replay_analyzer.flow",
        description="整局流程（flow）分类：先只做 TvT，双方都标")
    parser.add_argument("--mine", default=bo.MINE_DIR, help="我的录像目录")
    parser.add_argument("--pro", default=bo.PRO_DIR, help="职业录像根目录")
    parser.add_argument("--matchup", default="TvT", help="对位（目前主要做 TvT）")
    parser.add_argument("--limit", type=int, default=60, help="我的局数上限")
    parser.add_argument("--pro-limit", type=int, default=30, help="职业局数上限（每对位）")
    parser.add_argument("--seed", type=int, default=20261108)
    parser.add_argument("--json", default=os.path.join("reports", "tvt-flows.json"))
    args = parser.parse_args(argv)

    report = build_report(args.mine, args.pro, args.matchup, args.limit,
                          args.pro_limit, args.seed,
                          progress=lambda done, total, name: sys.stderr.write(
                              "\r  [flow] %d/%d %s" % (done, total, name[:40])))
    write_report(report, args.json)
    sys.stderr.write("\n")
    sys.stdout.write("wrote %s (%d games / %d player-games) in %.1fs\n" % (
        args.json, report["meta"]["games"], report["meta"]["player_games"],
        report["meta"]["elapsed_seconds"]))
    sys.stdout.write("unknown share = %.1f%%  playable games = %d\n" % (
        report["meta"]["unknown_share"] * 100.0, report["meta"]["playable_games"]))
    for flow in report["flows"]:
        sys.stdout.write("  %-16s %3d player-games  share=%3.0f%%  examples=%d\n" % (
            flow["key"], flow["player_games"], flow["share"] * 100.0,
            len(flow["examples"])))
    return report


if __name__ == "__main__":
    main()
