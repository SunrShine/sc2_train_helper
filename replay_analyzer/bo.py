"""按时间线分段的建造顺序（BO）：提取 / 开局签名 / 兵种风格 / 跨局聚类。

严格遵循 ``docs/BO数据契约.md``：

    GameBO = {game, player, signature, style, segments[], key_timings{}}
    Segment = {index, phase, t0, t1, signature, trigger, steps[], key_actions[], note}

实测口径（Lead 2026-11-08 用真实 replay 校准过）：

* ``parsed.build_events`` 的 ``kind`` 只有 structure / unit / morph / upgrade；
  农民是 ``kind="unit"`` + ``stage="eco"``（SCV / Probe / Drone），**没有 worker**。
* ``morph`` 是变形事件（SupplyDepotLowered / OrbitalCommand / Lair / WarpGate …），
  不当新建造重复计费：morph 的 ``cost`` 一律写 ``null``（造价表里查到的往往是
  目标建筑本身的造价，不是这次变形的增量花费）。
* ``build_events`` 没有 ``cost``，这里用 ``units.cost(name)`` 补成 ``[矿, 气]``。
* 初始基地在 loop=0 出现，算 key_timings 时排除。
* 两个玩家的事件混在同一个列表里，按 ``player_id`` 过滤。

聚类用纯标准库实现（没有 numpy），确定性：同一批 replay 永远得到同一个分组。

## 人族 signature 的封闭词表 + 判定顺序（同一局永远得同一标签）

词表（机器值全英文；中文只出现在 HTML 展示层）：

    mech / 3-rax / 2-rax-fast-expand / 2-1-1 / 1-rax-fast-expand / 1-1-1 / unknown

统计窗口固定为开局到 8:00（480s）。判据只用三件事——**生产建筑数量 + 气矿时机 +
扩张时机**，不看文件名、地图名、玩家名猜。按顺序命中即返回（先命中先算）：

0. 先算两个前置量：``first_expand``（第一次下基地，loop=0 的初始基地排除）、
   ``rax_before_expand``（扩张之前/同时下了几个兵营；没扩张就算到 5:00）。
1. ``3-rax``：``rax_before_expand >= 3``。
2. ``2-rax-fast-expand``：``rax_before_expand >= 2`` 且扩张早于第一个重工厂。
3. ``1-rax-fast-expand``：``rax_before_expand == 1`` 且扩张 ≤ 3:30 且早于第一个重工厂。
4. ``mech``：8:00 内重工 ≥ 2 且兵营 ≤ 2；或重工早于第一个兵营；或兵营 ≤ 1 + 有重工 +
   无星港 + 没速扩。
5. ``3-rax``：5:00 内已下 3 个兵营（前面没命中说明扩张不早）。
6. ``2-1-1``：7:00 内 ≥ 2 兵营 + 重工厂 ≤ 7:00 + 星港 ≤ 8:00。
7. ``1-1-1``：有兵营 + 重工厂 ≤ 8:00 + 星港 ≤ 9:00。
8. 其余一律 ``unknown``——宁可标 unknown，也不硬塞进某一类。

判定顺序的意义：先看"扩张时机 + 扩张前兵营数"（这套 build 的身份），
再看"6~8 分钟的生产建筑构成"（有没有转重工/星港）。越极端的打法越先判，
避免 2-1-1 把 2-rax-fast-expand 吃掉。非人族的 signature 直接 ``unknown``
（这套词表是给人族开局用的）。

## key_timings 的扩张时间：要过滤掉"假扩张"

ESL/GameHeart 老 replay（例如 build 88500 的 2022 比赛录像）里有 overlay 单位，加上
tracker 的 tag 回收，会让某些建筑的 ``start_loop`` 错配到别的单位上，出现"1:22 就下
二矿"这种物理不可能的记录。``_expand_times()`` 因此做了两道合理性过滤：

1. 时间必须 ≥ 100 秒。二矿不可能早于 2:00 左右出现（400 矿的成本摆在那），
   而这批老 replay 的 tracker 时间本身不可信。
2. ``done_loop - start_loop`` 必须与该建筑的建造时长接近。
   **时间单位就是游戏内秒**：``loop / 16`` 与 Blizzard 自己写进
   ``replay.gamemetadata.json`` 的 ``Duration`` 字段完全一致（Ruby Rock
   Duration=2737 vs loops/16=2737.44；Rorschach Duration=1091 vs 1090.75），
   所以不需要任何真实时间换算。CommandCenter 建造 100 秒，实测干净 replay 的
   done-start 中位数就是 100.0。偏差超过 50% 才判为记录损坏。

不满足条件就顺延到下一个候选，全都不满足则第一个/第二个扩张给 ``null``。
被丢弃的记录会记进 summary 的 ``sampling.expand_rejects`` 和报告 caveats 里，不静默吞掉。

## style 的封闭词表

    bio / mech / air / mixed / unknown

用 5:00–10:00 出产的战斗单位计数判（Medivac 算辅助，不参与）：某类占比 ≥ 60% 就取该类，
bio 与 mech 都 ≥ 20%、或 air ≥ 20% 且另有 ≥ 20% 的地面伴随时判 ``mixed``；
窗口内不足 5 个兵就退化成 3:00–6:00 再数一次；一个兵都没有 → ``unknown``。
"""

import glob
import json
import math
import os
import random
import statistics

from . import units as ud
from .loader import load_replay

LOOPS_PER_SECOND = 16.0

# --------------------------------------------------------------------------- 常量

#: 契约第二节的阶段表（默认按时间切，不按事件动态切）。
PHASES = (
    ("opening", 0.0, 180.0),
    ("early", 180.0, 360.0),
    ("mid", 360.0, 600.0),
    ("late", 600.0, None),
)

PHASE_ZH = {"opening": "开局", "early": "前期", "mid": "中期", "late": "后期"}

TOWNHALL_BUILDS = frozenset(("CommandCenter", "Nexus", "Hatchery"))
GAS_BUILDINGS = frozenset(("Refinery", "Assimilator", "Extractor"))
TERRAN_PRODUCTION = ("Barracks", "Factory", "Starport")

#: 扩张（开矿）只认这些建筑。注意 morph 形态（Lair / Hive / OrbitalCommand /
#: PlanetaryFortress）是老基地变形，不是新扩张，所以这里只取 kind="structure" 的事件。
EXPAND_NAMES = frozenset(("CommandCenter", "Nexus", "Hatchery", "Lair", "Hive",
                          "OrbitalCommand", "PlanetaryFortress"))

#: 扩张建筑的建造时长，单位就是**游戏内秒**（loop/16）。
#: loop/16 与 Blizzard 写进 gamemetadata.json 的 Duration 完全一致，不需要任何换算；
#: CommandCenter 建造 100 秒，实测干净 replay 的 done-start 中位数正好 100.0。
EXPAND_BUILD_SECONDS = {"CommandCenter": 100.0, "Nexus": 100.0, "Hatchery": 100.0}

#: 扩张时间的下限：二矿不可能早于 2:00 左右出现（400 矿的成本摆在那），
#: 而这批 ESL/GameHeart 老 replay 的 tracker 时间本身不可信。
EXPAND_MIN_SECONDS = 100.0

#: done_loop - start_loop 偏离真实建造时长超过这个比例，就当这条记录不可信。
EXPAND_SPAN_TOLERANCE = 0.5

#: 段签名 token 表：只挑"能概括打法"的建筑，补给站/地堡之类不进去。
_SIG_TOKEN = {
    "Barracks": "rax", "Factory": "fac", "Starport": "sp",
    "Gateway": "gate", "RoboticsFacility": "robo", "Stargate": "sg",
    "Refinery": "gas", "Assimilator": "gas", "Extractor": "gas",
    "CommandCenter": "cc", "Nexus": "nexus", "Hatchery": "hatch",
    "BarracksReactor": "reac", "FactoryReactor": "reac", "StarportReactor": "reac",
    "BarracksTechLab": "lab", "FactoryTechLab": "lab", "StarportTechLab": "lab",
    "EngineeringBay": "ebay", "Armory": "armory",
    "CyberneticsCore": "core", "TwilightCouncil": "twilight",
    "RoboticsBay": "roboBay", "TemplarArchives": "archives", "DarkShrine": "shrine",
    "Forge": "forge", "PhotonCannon": "cannon", "ShieldBattery": "battery",
    "Bunker": "bunker", "MissileTurret": "turret",
    "SpawningPool": "pool", "BanelingNest": "bane", "RoachWarren": "roachWarren",
    "EvolutionChamber": "evo", "HydraliskDen": "hydra", "Spire": "spire",
    "InfestationPit": "infest", "UltraliskCavern": "ultra", "NydusNetwork": "nydus",
}

#: 兵种构成 → style 的三类权重。Medivac 是辅助，不参与判定。
_STYLE_BIO = frozenset(("Marine", "Marauder", "Reaper", "Ghost"))
_STYLE_MECH = frozenset(("Hellion", "Hellbat", "WidowMine", "SiegeTank", "Cyclone", "Thor"))
_STYLE_AIR = frozenset(("Viking", "Banshee", "Raven", "Liberator", "Battlecruiser"))

#: style 统计窗口（契约要求"用 5~10 分钟出产的兵种构成判"）。
STYLE_WINDOW = (300.0, 600.0)
#: 对局太短时的兜底窗口。
STYLE_WINDOW_SHORT = (180.0, 360.0)


def clock(seconds):
    """秒 -> ``M:SS``，给 key_actions 用。"""
    if seconds is None:
        return "?"
    whole = max(0, int(round(float(seconds))))
    return "%d:%02d" % (whole // 60, whole % 60)


def _round(value, digits=1):
    return None if value is None else round(float(value), digits)


def _median(values):
    values = [v for v in values if v is not None]
    return round(float(statistics.median(values)), 1) if values else None


def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def _sec(loop):
    return (loop or 0) / LOOPS_PER_SECOND


def _phase_of(seconds):
    for name, start, end in PHASES:
        if end is None or seconds < end:
            return name
    return PHASES[-1][0]


# --------------------------------------------------------------------------- 玩家 / 元信息

def resolve_player(parsed, player=None, prefer_name=None):
    """把 ``player`` 参数解析成 loader.Player。

    支持：Player 对象 / player_id(int) / None（自动挑本地玩家）。
    职业录像没有本地玩家，退化成"名字匹配 / 第一个 T / 第一个玩家"。
    """
    players = [p for p in parsed.players if p.race != "R"] or list(parsed.players)
    if isinstance(player, int):
        found = parsed.player_by_player_id(player)
        if found is not None:
            return found
    elif player is not None and hasattr(player, "player_id"):
        return player

    if prefer_name:
        lowered = prefer_name.lower()
        for p in players:
            if lowered in (p.name or "").lower():
                return p
    for p in players:
        if p.is_local:
            return p
    for p in players:
        if p.race == "T":
            return p
    return players[0] if players else None


def opponent_of(parsed, player):
    for p in parsed.players:
        if p.player_id != player.player_id and p.team != player.team:
            return p
    for p in parsed.players:
        if p.player_id != player.player_id:
            return p
    return None


def game_meta(parsed, player=None):
    """对局元信息。matchup 用**被分析玩家视角**：本地 T 打 Z -> ``TvZ``。

    职业录像里 Maru 也永远是 ``Tv?``，和"我的录像"同一套口径，方便直接对比。
    """
    analyzed = player or resolve_player(parsed)
    opp = opponent_of(parsed, analyzed) if analyzed else None
    matchup = "%sv%s" % (analyzed.race, opp.race) if (analyzed and opp) else ""
    date = ""
    if parsed.replay_time is not None:
        date = parsed.replay_time.astimezone().strftime("%Y-%m-%d")
    return {
        "path": parsed.path,
        "file": os.path.basename(parsed.path),
        "map": parsed.map_name,
        "date": date,
        "matchup": matchup,
        "build": parsed.version.get("build") or parsed.protocol_build or 0,
        "duration": round(parsed.duration_seconds, 2),
    }


# --------------------------------------------------------------------------- 步骤

def collect_steps(parsed, player_id):
    """该玩家的建造顺序步骤（已补 cost、已排序、已排除 loop=0 的初始单位）。"""
    steps = []
    for event in parsed.build_events:
        if event.get("player_id") != player_id:
            continue
        loop = event.get("loop") or 0
        if loop <= 0:
            continue                      # 开局自带的 12 个农民 / 主基地不是 BO 的一部分
        name = event.get("name") or ""
        kind = event.get("kind") or ("structure" if ud.category(name) in ("structure", "townhall") else "unit")
        # morph 是变形：造价表里查到的往往是"目标建筑本身的造价"，不是这次变形的花费，
        # 写成 null，避免下游把它当新建造重复计费。
        raw_cost = None if kind == "morph" else ud.cost(name)
        steps.append({
            "t": _round(_sec(loop)),
            "name": name,
            "label": event.get("label") or ud.unit_label(name),
            "kind": kind,
            "cost": [int(raw_cost[0]), int(raw_cost[1])] if raw_cost else None,
            # 下面两个只在内部用，不写进 Segment.steps（契约里没有这两个字段）
            "_stage": event.get("stage") or ud.stage(name),
            "_done": _round(_sec(event["done_loop"])) if event.get("done_loop") else None,
        })
    steps.sort(key=lambda s: (s["t"], s["name"]))
    return steps


def _builds(steps, names):
    """挑出 kind=structure 且名字在 names 里的时间点（升序）。"""
    return [s["t"] for s in steps if s["kind"] == "structure" and s["name"] in names]


def army_moveout(parsed, player):
    """第一次"主力离开己方半场"（秒），算不出来返回 None。

    保守定义：用 ``fog.Model`` 取我方部队坐标，采样 10 秒一次；
    当**至少 5 个战斗单位**离敌方主基地比离我方主基地更近时，认定主力已经出门。
    单兵侦察（死神/农民/单架女妖）不会被算成出门口。
    """
    try:
        from . import fog
        model = fog.Model(parsed)
    except Exception:
        return None

    def main_base(pid):
        for record in parsed.unit_records:
            if (record.player_id == pid
                    and record.name in ("CommandCenter", "Nexus", "Hatchery", "Lair", "Hive")
                    and record.x is not None and record.y is not None):
                if (record.start_loop or 0) <= 0:
                    return (record.x, record.y)
        for record in parsed.unit_records:
            if (record.player_id == pid
                    and record.name in ("CommandCenter", "Nexus", "Hatchery", "Lair", "Hive")
                    and record.x is not None and record.y is not None):
                return (record.x, record.y)
        return None

    mine = main_base(player.player_id)
    enemy = None
    for other in parsed.players:
        if other.player_id == player.player_id:
            continue
        enemy = main_base(other.player_id)
        if enemy:
            break
    if not mine or not enemy:
        return None

    limit = min(parsed.duration_seconds, 1100.0)
    seconds = 60.0
    while seconds <= limit:
        try:
            units = model.units_at(player.player_id, seconds)
        except Exception:
            return None
        count = 0
        for record, x, y in units:
            try:
                if model.kind_at(record, seconds) != "army":
                    continue
            except Exception:
                continue
            if math.hypot(x - enemy[0], y - enemy[1]) < math.hypot(x - mine[0], y - mine[1]):
                count += 1
                if count >= 5:
                    return _round(seconds)
        seconds += 10.0
    return None


# --------------------------------------------------------------------------- key_timings

def _expand_times(steps, diag=None):
    """扩张（开矿）时间候选，带合理性过滤。

    过滤两条（task-7；ESL/GameHeart 老 replay 的 overlay + tag 回收会让早期
    tracker 事件错配到别的单位上，出现"1:22 就下二矿"这种物理不可能的记录）：

    1. 时间必须 ≥ :data:`EXPAND_MIN_SECONDS`：二矿不可能早于 2:00 左右出现
       （400 矿的成本摆在那），而这批老 replay 的 tracker 时间本身不可信；
    2. ``done_loop - start_loop`` 必须和该建筑建造时长（:data:`EXPAND_BUILD_SECONDS`，
       单位是游戏内秒）接近；偏差超过 :data:`EXPAND_SPAN_TOLERANCE` 的记录不采信
       （done 缺失时无法校验，采信）。

    不合格就顺延下一个候选；全都不合格返回空列表。``diag`` 传 dict 时会把被丢弃的
    候选记进去，报告里用来说明数据噪音。
    """
    kept, rejects = [], []
    for step in steps:
        if step["kind"] != "structure" or step["name"] not in EXPAND_NAMES:
            continue
        seconds = step["t"]
        if seconds < EXPAND_MIN_SECONDS:
            rejects.append({"t": _round(seconds), "name": step["name"],
                            "reason": "too_early", "span": None})
            continue
        done = step.get("_done")
        expected = EXPAND_BUILD_SECONDS.get(step["name"])
        if done is not None and expected:
            span = round(done - seconds, 1)
            if abs(span - expected) > expected * EXPAND_SPAN_TOLERANCE:
                rejects.append({"t": _round(seconds), "name": step["name"],
                                "reason": "span_mismatch", "span": span})
                continue
        kept.append(seconds)
    if diag is not None and rejects:
        diag.setdefault("rejected_expands", []).extend(rejects)
    return sorted(kept)


def key_timings(steps, race, moveout=None, diag=None):
    """契约第一节的 8 个关键时间点；没有就是 null。"""
    expands = _expand_times(steps, diag)
    gasses = _builds(steps, GAS_BUILDINGS)
    rax = _builds(steps, ("Barracks",))
    factory = _builds(steps, ("Factory",))
    starport = _builds(steps, ("Starport",))
    terran = (race == "T")
    return {
        "first_expand": _round(expands[0]) if expands else None,
        "second_expand": _round(expands[1]) if len(expands) > 1 else None,
        "first_gas": _round(gasses[0]) if gasses else None,
        "second_gas": _round(gasses[1]) if len(gasses) > 1 else None,
        "third_rax": _round(rax[2]) if (terran and len(rax) > 2) else None,
        "first_starport": _round(starport[0]) if (terran and starport) else None,
        "first_factory": _round(factory[0]) if (terran and factory) else None,
        "army_moveout": moveout,
    }


# --------------------------------------------------------------------------- signature / style

def _rax_before_expand(steps, first_expand):
    """扩张之前（含同时）下了几个兵营。没有扩张就数到 300 秒。"""
    deadline = (first_expand + 5.0) if first_expand is not None else 300.0
    return len([s for s in steps
                if s["kind"] == "structure" and s["name"] == "Barracks" and s["t"] <= deadline])


def signature(parsed, player, steps, timings):
    """开局签名。

    人族按"生产建筑数量 + 气矿时机 + 扩张时机"判，不看文件名/地图名猜。
    非人族返回 ``unknown``（契约里的签名集合是给人族用的）。
    """
    if player.race != "T":
        return "unknown"

    rax = _builds(steps, ("Barracks",))
    fac = _builds(steps, ("Factory",))
    sp = _builds(steps, ("Starport",))
    # 扩张时间用过滤后的口径（见 _expand_times），别让假扩张把签名带偏
    first_expand = timings.get("first_expand")

    rax_480 = len([t for t in rax if t <= 480.0])
    fac_480 = len([t for t in fac if t <= 480.0])
    rax_before_expand = _rax_before_expand(steps, first_expand)
    expanded = first_expand is not None
    fast_expand = expanded and first_expand <= 210.0

    # 判定顺序 = 先看扩张时机与扩张前的兵营数，再看 6~8 分钟的生产建筑构成。
    # 越"极端"的打法越先判，避免 2-1-1 把 2-rax-fast-expand 吃掉。

    # 1) 3-rax：扩张之前已经三兵营。
    if rax_before_expand >= 3:
        return "3-rax"

    # 2) 2-rax-fast-expand：两兵营之后、重工之前就扩张。
    if rax_before_expand >= 2 and expanded and (not fac or first_expand < fac[0]):
        return "2-rax-fast-expand"

    # 3) 1-rax-fast-expand：单兵营速二矿（扩张早于重工/星港）。
    if rax_before_expand == 1 and fast_expand and (not fac or first_expand < fac[0]):
        return "1-rax-fast-expand"

    # 4) mech：重工优先，兵营被压得很低。
    if fac_480 >= 2 and rax_480 <= 2:
        return "mech"
    if fac and (not rax or fac[0] < rax[0]):
        return "mech"                                     # 重工先于兵营
    if rax_480 <= 1 and fac and not sp and not fast_expand:
        return "mech"                                     # 单兵营重工、无星港、又没速扩

    # 5) 3-rax：5 分钟内三兵营（即使开矿晚/没开矿）。
    if len([t for t in rax if t <= 300.0]) >= 3:
        return "3-rax"

    # 6) 2-1-1：两兵营 + 重工 + 星港都早早下（经典 TvZ 收气 timing）。
    if len([t for t in rax if t <= 420.0]) >= 2 and fac and fac[0] <= 420.0 and sp and sp[0] <= 480.0:
        return "2-1-1"

    # 7) 1-1-1：单兵营 + 重工 + 星港（不急着开矿也认）。
    if rax and fac and sp and fac[0] <= 480.0 and sp[0] <= 540.0:
        return "1-1-1"

    return "unknown"


def style(steps, race):
    """bio / mech / air / mixed / unknown，用 5~10 分钟出产的兵种构成判。"""
    if race != "T":
        return "unknown"

    def count_window(low, high):
        bio = mech = air = 0
        for step in steps:
            if step["kind"] != "unit" or not (low <= step["t"] < high):
                continue
            name = step["name"]
            if name in _STYLE_BIO:
                bio += 1
            elif name in _STYLE_MECH:
                mech += 1
            elif name in _STYLE_AIR:
                air += 1
        return bio, mech, air

    bio, mech, air = count_window(*STYLE_WINDOW)
    if bio + mech + air < 5:
        bio2, mech2, air2 = count_window(*STYLE_WINDOW_SHORT)
        bio, mech, air = bio + bio2, mech + mech2, air + air2
    total = bio + mech + air
    if total == 0:
        return "unknown"
    shares = {
        "bio": bio / float(total),
        "mech": mech / float(total),
        "air": air / float(total),
    }
    best = max(shares, key=lambda k: shares[k])
    if shares[best] >= 0.6:
        return best
    if shares["bio"] >= 0.2 and shares["mech"] >= 0.2:
        return "mixed"
    if shares["air"] >= 0.2 and (shares["bio"] + shares["mech"]) >= 0.2:
        return "mixed"
    return best


# --------------------------------------------------------------------------- Segment

def _chain(tokens, limit=5):
    """token 序列 -> 短签名：连续同类折叠成 rax2 这种，最多 limit 段。"""
    runs = []
    for token in tokens:
        if runs and runs[-1][0] == token:
            runs[-1][1] += 1
        else:
            runs.append([token, 1])
    out = []
    for token, count in runs:
        out.append(token if count == 1 else "%s%d" % (token, count))
    if len(out) > limit:
        out = out[:limit - 1] + ["+%d" % (len(out) - limit + 1)]
    return "-".join(out)


def _phase_tokens(segment_steps):
    tokens = []
    for step in segment_steps:
        if step["kind"] not in ("structure",):
            continue
        token = _SIG_TOKEN.get(step["name"])
        if token:
            tokens.append(token)
    return tokens


def _phase_signature(phase, segment_steps, game_style):
    tokens = _phase_tokens(segment_steps)
    if tokens:
        return _chain(tokens)
    if phase == "opening":
        return "opening"
    # 没有新建筑：靠这一段的兵种构成说话
    units = [s for s in segment_steps if s["kind"] == "unit"]
    if units:
        return game_style if game_style != "unknown" else "army"
    return "farm"


def _key_actions(segment_steps, limit=8):
    """给人看的摘要：建筑 / 升级 / 首个兵种，超过 limit 条截断。"""
    picked = []
    seen_units = set()
    for step in segment_steps:
        if step["kind"] == "morph":
            continue
        if step["kind"] == "upgrade":
            picked.append(step)
            continue
        if step["kind"] == "structure":
            picked.append(step)
            continue
        # unit：跳过农民，每个兵种只留第一条
        if step["_stage"] == "eco":
            continue
        if step["name"] in seen_units:
            continue
        seen_units.add(step["name"])
        picked.append(step)
        if len(picked) > limit * 2:
            break
    picked.sort(key=lambda s: s["t"])
    return ["%s@%s" % (s["label"], clock(s["t"])) for s in picked[:limit]]


def build_segments(steps, duration, game_style):
    segments = []
    index = 0
    for phase, start, end in PHASES:
        low = start
        high = duration if end is None else min(end, duration)
        if high <= low and index > 0:
            continue
        chunk = [s for s in steps if low <= s["t"] < high]
        if not chunk:
            continue
        index += 1
        segments.append({
            "index": index,
            "phase": phase,
            "t0": _round(low),
            "t1": _round(high),
            "signature": _phase_signature(phase, chunk, game_style),
            "trigger": {"kind": "time", "detail": ""},
            "steps": [{k: v for k, v in s.items() if not k.startswith("_")} for s in chunk],
            "key_actions": _key_actions(chunk),
            "note": "",
        })
    return segments


# --------------------------------------------------------------------------- 主入口

def extract_bo(parsed, player=None, prefer_name=None, with_moveout=True, diag=None):
    """解析结果 -> GameBO（严格按契约）。

    ``player`` 可以是 loader.Player / player_id(int) / None（自动挑本地玩家）。
    职业录像没有本地玩家时用 ``prefer_name="maru"`` 指定。
    ``diag`` 传一个 dict 时，会把"因数据噪音被丢弃的扩张记录"记进去（不进 GameBO）。
    """
    player = resolve_player(parsed, player, prefer_name)
    if player is None:
        raise ValueError("replay 里找不到玩家: %s" % parsed.path)

    steps = collect_steps(parsed, player.player_id)
    timings = key_timings(steps,
                          player.race,
                          army_moveout(parsed, player) if with_moveout else None,
                          diag=diag)
    game_style = style(steps, player.race)
    return {
        "game": game_meta(parsed, player),
        "player": {
            "pid": player.player_id,
            "name": player.name,
            "race": player.race,
            "is_local": bool(player.is_local),
        },
        "signature": signature(parsed, player, steps, timings),
        "style": game_style,
        "segments": build_segments(steps, parsed.duration_seconds, game_style),
        "key_timings": timings,
    }


def extract_bo_path(path, player=None, prefer_name=None, with_moveout=True):
    parsed = load_replay(path, decode_events=True)
    return extract_bo(parsed, player, prefer_name, with_moveout)


# --------------------------------------------------------------------------- 批量挖掘

def expand_paths(paths):
    """目录（递归）或文件列表 -> 排序后的 .SC2Replay 绝对路径列表。"""
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    files = []
    for item in paths:
        item = os.fspath(item)
        if os.path.isdir(item):
            files.extend(glob.glob(os.path.join(item, "**", "*.SC2Replay"), recursive=True))
        elif item.lower().endswith(".sc2replay"):
            files.append(item)
    return sorted({os.path.abspath(f) for f in files})


def mine_folder(paths, limit=60, matchup=None, prefer_name=None,
                seed=20261108, with_moveout=True, progress=None,
                ladder_only=False, min_duration=0.0,
                require_local=False, exclude_ai=False):
    """抽样解析一批 replay，返回 ``[GameBO]``。

    * ``paths`` 可以是目录或路径列表（递归找 .SC2Replay）
    * ``limit`` 最多返回多少局（匹配 matchup 的才算）
    * ``matchup`` 形如 ``"TvZ"``：本地玩家种族 + 对手种族都匹配才收
    * ``prefer_name`` 职业录像用来指定"我要分析谁"
    * ``ladder_only`` 只收暴雪官方地图上的局（过滤自建图/街机图）
    * ``min_duration`` 丢掉太短的对局（默认叫 120 秒的残局不算 BO）
    * ``require_local`` 只收"我在里面"的局（排除下载来的别人的录像）
    * ``exclude_ai`` 只收人类对手（人机局的节奏跟天梯不是一回事）

    两段式：先用 ``decode_events=False`` 做**便宜预筛**（只读元信息，约 0.05s/局），
    筛中了才 ``decode_events=True`` 深解析（约 1.2s/局）。所以即使 1000 局里
    只有 10% 是 TvZ，也不用把 1000 局全解码一遍。

    确定性抽样：``random.Random(seed)`` 打乱后顺序扫，同一批文件永远抽到同一组。
    """
    files = expand_paths(paths)
    order = list(files)
    random.Random(seed).shuffle(order)

    cheap_only = bool(matchup or require_local or exclude_ai or ladder_only or min_duration)
    out = []
    errors = []
    stats = {"scanned": 0, "matched": 0, "skipped_filter": 0, "deep_parsed": 0}
    for path in order:
        if len(out) >= limit:
            break
        stats["scanned"] += 1
        try:
            parsed = load_replay(path, decode_events=not cheap_only)
        except Exception as exc:                       # 坏档 / 版本不支持
            errors.append({"file": os.path.basename(path), "error": str(exc)[:160]})
            continue
        if min_duration and parsed.duration_seconds < min_duration:
            stats["skipped_filter"] += 1
            continue
        if ladder_only and not parsed.is_ladder:
            stats["skipped_filter"] += 1
            continue
        if require_local and not any(p.is_local for p in parsed.players):
            stats["skipped_filter"] += 1
            continue
        player = resolve_player(parsed, None, prefer_name)
        if player is None:
            errors.append({"file": os.path.basename(path), "error": "no player"})
            continue
        opp = opponent_of(parsed, player)
        if exclude_ai and opp is not None and opp.is_ai:
            stats["skipped_filter"] += 1
            continue
        if matchup:
            if player.race != matchup[0] or opp is None or opp.race != matchup[2]:
                stats["skipped_filter"] += 1
                continue
        if cheap_only:                                 # 预筛过了，重新按事件解码
            try:
                parsed = load_replay(path, decode_events=True)
            except Exception as exc:
                errors.append({"file": os.path.basename(path), "error": str(exc)[:160]})
                continue
            player = resolve_player(parsed, player if hasattr(player, "player_id") else None,
                                    prefer_name)
            if player is None:
                continue
            stats["deep_parsed"] += 1
        diag = {}
        try:
            bo = extract_bo(parsed, player, with_moveout=with_moveout, diag=diag)
        except Exception as exc:
            errors.append({"file": os.path.basename(path), "error": str(exc)[:160]})
            continue
        if diag.get("rejected_expands"):
            stats.setdefault("rejected_expands", []).append({
                "file": os.path.basename(path),
                "build": parsed.version.get("build"),
                "items": diag["rejected_expands"],
            })
        out.append(bo)
        stats["matched"] += 1
        if progress:
            progress(len(out), bo["game"]["file"])
    mine_folder.last_errors = errors
    mine_folder.last_stats = stats
    return out


mine_folder.last_errors = []
mine_folder.last_stats = {}


# --------------------------------------------------------------------------- 开局特征 / 聚类

#: 聚类用的关键时间点（秒）。
CLUSTER_KEYS = ("first_gas", "first_expand", "second_gas", "first_factory",
                "first_starport", "third_rax")


def opener_features(bo):
    """从 GameBO 里还原"开局长什么样"的特征（不需要重新解析 replay）。"""
    timings = bo["key_timings"]
    steps = [s for seg in bo["segments"] for s in seg["steps"]]
    expand = timings.get("first_expand")
    rax = [s["t"] for s in steps if s["kind"] == "structure" and s["name"] == "Barracks"]
    rax_before = _rax_before_expand(
        [dict(s, _stage="") for s in steps], expand)
    gas_by_300 = len([s for s in steps
                      if s["kind"] == "structure" and s["name"] in GAS_BUILDINGS and s["t"] <= 300.0])
    first_army, first_army_t = None, None
    for step in steps:
        if step["kind"] != "unit" or ud.category(step["name"]) != "army":
            continue
        first_army, first_army_t = step["name"], step["t"]
        break
    return {
        "first_gas": timings.get("first_gas"),
        "first_expand": expand,
        "second_gas": timings.get("second_gas"),
        "first_factory": timings.get("first_factory"),
        "first_starport": timings.get("first_starport"),
        "third_rax": timings.get("third_rax"),
        "rax_before_expand": rax_before,
        "rax_total": len(rax),
        "gas_by_300": gas_by_300,
        "expanded": expand is not None,
        "first_army": first_army,
        "first_army_t": first_army_t,
        "duration": bo["game"]["duration"],
    }


#: 聚类使用的维度（只看"开局+第一个转型"的时间点，不看兵种风格）。
CLUSTER_FEATURES = ("first_gas", "first_expand", "second_gas",
                    "first_factory", "first_starport")


def _percentile(values, p):
    vals = sorted(values)
    if not vals:
        return 0.0
    key = (len(vals) - 1) * (p / 100.0)
    low, high = int(key), min(int(key) + 1, len(vals) - 1)
    return vals[low] + (vals[high] - vals[low]) * (key - low)


def _feature_matrix(bos, keys=CLUSTER_FEATURES):
    """特征矩阵：缺失值填"很晚"，再按列做 5%~95% 分位裁剪 + z-score。

    裁剪是为了别让"没开二矿/没下星港"的极端局把质心拽跑（k-means 对离群值很敏感）。
    """
    raw = []
    for bo in bos:
        row = []
        for key in keys:
            value = bo["key_timings"].get(key)
            row.append(900.0 if value is None else float(value))
        raw.append(row)
    cols = list(zip(*raw))
    bounds = [(_percentile(col, 5), _percentile(col, 95)) for col in cols]
    clean = [[min(max(v, lo), hi) for v, (lo, hi) in zip(row, bounds)] for row in raw]
    cols = list(zip(*clean))
    means = [sum(col) / len(col) for col in cols]
    scales = [max(1e-6, (sum((v - m) ** 2 for v in col) / len(col)) ** 0.5)
              for col, m in zip(cols, means)]
    return [[(v - m) / s for v, m, s in zip(row, means, scales)] for row in clean]


def _distance(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _kmeans2(vectors, iterations=25):
    """确定性 2-means：按扩张时间排序后对半分作为初始质心，再迭代。"""
    if len(vectors) < 2:
        return [0] * len(vectors), [list(v) for v in vectors][:2]
    order = sorted(range(len(vectors)), key=lambda i: (vectors[i][1], vectors[i][0]))
    mid = len(order) // 2
    left = [vectors[i] for i in order[:mid]] or [vectors[order[0]]]
    right = [vectors[i] for i in order[mid:]] or [vectors[order[-1]]]
    centers = [
        [sum(col) / len(col) for col in zip(*left)],
        [sum(col) / len(col) for col in zip(*right)],
    ]
    labels = [0] * len(vectors)
    for _ in range(iterations):
        changed = False
        new_labels = []
        for index, vec in enumerate(vectors):
            d0 = _distance(vec, centers[0])
            d1 = _distance(vec, centers[1])
            label = 0 if d0 <= d1 else 1
            new_labels.append(label)
            if label != labels[index]:
                changed = True
        labels = new_labels
        groups = [[], []]
        for index, label in enumerate(labels):
            groups[label].append(vectors[index])
        for label in (0, 1):
            if groups[label]:
                centers[label] = [sum(col) / len(col) for col in zip(*groups[label])]
        if not changed:
            break
    return labels, centers


def _cluster_pace(feat):
    """快/慢节奏：看第一气矿与二矿的中位时间（阈值来自样本分布的双峰位置）。"""
    gas = _median([f["first_gas"] for f in feat])
    expand = _median([f["first_expand"] for f in feat])
    if (gas is not None and gas < 75.0) or (expand is not None and expand < 155.0):
        return "fast"
    return "slow"


def _cluster_title(members):
    """给一簇开局起个人能读懂的名字（依据簇内的中位时间点，不是硬编码猜的）。"""
    if not members:
        return "未知开局"
    feat = [opener_features(bo) for bo in members]
    pace = _cluster_pace(feat)
    n_rax = _mean([f["rax_before_expand"] for f in feat]) or 0
    expand = _median([f["first_expand"] for f in feat])
    fac = _median([f["first_factory"] for f in feat])

    pace_text = "快节奏" if pace == "fast" else "慢节奏"
    if n_rax < 0.5:
        rax_text = "裸二矿"
    elif n_rax < 1.5:
        rax_text = "单兵营"
    elif n_rax < 2.5:
        rax_text = "两兵营"
    else:
        rax_text = "三兵营"
    if expand is None:
        if fac is not None:
            return "%s %s转机械" % (pace_text, rax_text)
        return "%s 单矿 %s" % (pace_text, rax_text)
    if expand <= 200.0:
        expand_text = "速二矿" if pace == "fast" else "稳二矿"
    elif expand <= 300.0:
        expand_text = "标准二矿" if pace == "fast" else "缓二矿"
    else:
        expand_text = "晚二矿"
    if n_rax < 0.5:
        return "%s %s开" % (pace_text, rax_text)
    return "%s %s%s" % (pace_text, rax_text, expand_text)


def _cluster_key(members, pace):
    """簇的机器可读 key（英文）：节奏 + 簇内最常见的**已知** signature。"""
    tally = {}
    for bo in members:
        sig = bo["signature"]
        if sig == "unknown":
            continue
        tally[sig] = tally.get(sig, 0) + 1
    if not tally:
        return pace + "-other"
    best = max(tally, key=lambda k: (tally[k], k))
    return "%s-%s" % (pace, best)


def _summarize(members):
    feats = [opener_features(bo) for bo in members]
    avg = {}
    med = {}
    for key in CLUSTER_KEYS + ("army_moveout",):
        if key == "army_moveout":
            values = [bo["key_timings"].get("army_moveout") for bo in members]
        else:
            values = [f[key] for f in feats]
        avg[key] = _mean(values)
        med[key] = _median(values)
    signatures, styles, armies, builds = {}, {}, {}, {}
    for bo, feat in zip(members, feats):
        signatures[bo["signature"]] = signatures.get(bo["signature"], 0) + 1
        styles[bo["style"]] = styles.get(bo["style"], 0) + 1
        army = feat["first_army"] or "?"
        armies[army] = armies.get(army, 0) + 1
        builds[str(bo["game"]["build"])] = builds.get(str(bo["game"]["build"]), 0) + 1
    order = lambda kv: (-kv[1], kv[0])
    return {
        "mean": avg,
        "median": med,
        "signatures": dict(sorted(signatures.items(), key=order)),
        "styles": dict(sorted(styles.items(), key=order)),
        "first_army": dict(sorted(armies.items(), key=order)),
        "builds": dict(sorted(builds.items(), key=order)),
        "games": len(members),
    }


def _distinguish_twins(clusters):
    """两簇名字撞车时（比如都是"快节奏 单兵营速二矿"），用差异最大的节点补一个后缀。

    否则报告里两个类看起来一模一样，读的人不知道该看哪。用中位数相对差最大的那个
    关键时间点来区分（双气 / 重工 / 星港 / 气矿）。
    """
    if len(clusters) != 2:
        return
    first, second = clusters
    if first["name"] != second["name"]:
        return
    labels = (("first_gas", "气矿早", "气矿晚"),
              ("second_gas", "双气早", "双气晚"),
              ("first_factory", "重工早", "重工晚"),
              ("first_starport", "星港早", "星港晚"))
    best = None
    for key, early_text, late_text in labels:
        a = (first.get("median") or {}).get(key)
        b = (second.get("median") or {}).get(key)
        if a is None or b is None:
            continue
        diff = abs(a - b) / max(1.0, min(a, b))
        if best is None or diff > best[0]:
            best = (diff, key, a, b, early_text, late_text)
    if best is None or best[0] < 0.08:
        return
    _diff, key, a, b, early_text, late_text = best
    first["name"] = "%s（%s）" % (first["name"], early_text if a < b else late_text)
    second["name"] = "%s（%s）" % (second["name"], late_text if a < b else early_text)

    def base_key(cluster):
        known = [sig for sig in (cluster.get("signatures") or {}) if sig != "unknown"]
        return "%s-%s" % (cluster.get("pace") or "pace", known[0] if known else "other")

    slug = key.replace("first_", "").replace("second_", "2-").replace("_", "-")
    first["key"] = "%s-%s-%s" % (base_key(first), slug, "early" if a < b else "late")
    second["key"] = "%s-%s-%s" % (base_key(second), slug, "late" if a < b else "early")


def cluster_openers(bos, k=2):
    """把一批 GameBO 的开局聚成 k 类（默认 2 类，用户说他常用两种）。

    用 5 个开局/转型时间点做确定性 2-means（分位裁剪 + z-score，见 _feature_matrix），
    返回 ``[{"id","key","name","pace","games","share","mean","median","signatures",
    "styles","first_army","builds","members":[file...]}]``，按局数从多到少排序。
    """
    usable = [bo for bo in bos if bo["player"]["race"] == "T"]
    excluded = [bo["game"]["file"] for bo in bos if bo["player"]["race"] != "T"]
    if not usable:
        return []

    if k != 2:
        raise ValueError("cluster_openers 目前只实现 k=2")
    labels, _centers = _kmeans2(_feature_matrix(usable))

    clusters = []
    for label in (0, 1):
        members = [bo for bo, lab in zip(usable, labels) if lab == label]
        if not members:
            continue
        summary = _summarize(members)
        feat = [opener_features(bo) for bo in members]
        pace = _cluster_pace(feat)
        clusters.append({
            "id": label,
            "key": _cluster_key(members, pace),
            "name": _cluster_title(members),
            "pace": pace,
            "games": summary["games"],
            "share": round(summary["games"] / float(len(usable)), 3),
            "mean": summary["mean"],
            "median": summary["median"],
            "signatures": summary["signatures"],
            "styles": summary["styles"],
            "first_army": summary["first_army"],
            "builds": summary["builds"],
            "members": [bo["game"]["file"] for bo in members],
        })
    clusters.sort(key=lambda c: (-c["games"], c["id"]))
    _distinguish_twins(clusters)
    # key 必须唯一（两类可能同 pace 同签名），万一还重名就加 -2 / -3
    used = {}
    for cluster in clusters:
        base = cluster["key"]
        used[base] = used.get(base, 0) + 1
        if used[base] > 1:
            cluster["key"] = "%s-%d" % (base, used[base])
    if excluded:
        for cluster in clusters:
            cluster["excluded_non_terran"] = excluded
    return clusters


# --------------------------------------------------------------------------- 我 vs 职业

def compare_profiles(mine, pro, mine_label="我", pro_label="职业"):
    """关键时间点的中位数对照 + 签名/风格分布。"""
    def medians(bos):
        out = {}
        for key in CLUSTER_KEYS + ("army_moveout",):
            out[key] = _median([bo["key_timings"].get(key) for bo in bos])
        out["rax_before_expand"] = _median(
            [opener_features(bo)["rax_before_expand"] for bo in bos])
        return out

    def distribution(bos, field):
        counts = {}
        for bo in bos:
            counts[bo[field]] = counts.get(bo[field], 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    rows = []
    labels = {
        "first_expand": "二矿", "first_gas": "第一气矿", "second_gas": "第二气矿",
        "first_factory": "重工厂", "first_starport": "星港", "third_rax": "第三兵营",
        "army_moveout": "主力出门", "rax_before_expand": "开矿前兵营数",
    }
    mine_med = medians(mine)
    pro_med = medians(pro)
    for key in CLUSTER_KEYS + ("army_moveout", "rax_before_expand"):
        rows.append({
            "key": key,
            "label": labels.get(key, key),
            "mine": mine_med.get(key),
            "pro": pro_med.get(key),
            "delta": (round(mine_med[key] - pro_med[key], 1)
                      if mine_med.get(key) is not None and pro_med.get(key) is not None
                      else None),
        })
    return {
        "mine": {"label": mine_label, "games": len(mine), "median": mine_med,
                 "signatures": distribution(mine, "signature"),
                 "styles": distribution(mine, "style")},
        "pro": {"label": pro_label, "games": len(pro), "median": pro_med,
                "signatures": distribution(pro, "signature"),
                "styles": distribution(pro, "style")},
        "rows": rows,
    }


def _unknown_share(bos, field):
    if not bos:
        return None
    hits = len([bo for bo in bos if bo.get(field) == "unknown"])
    return round(hits / float(len(bos)), 3)


def _caveats(clusters, mine, pro, matchup, rejects=None):
    """报告里必须说清楚的话：哪些结论是数据支撑的，哪些只是"看起来像"。"""
    notes = []
    for cluster in clusters:
        builds = cluster.get("builds") or {}
        total = sum(builds.values()) or 1
        top_build, top_count = max(builds.items(), key=lambda kv: kv[1])
        if top_count / float(total) >= 0.9 and total >= 5:
            notes.append(
                "%s：%d/%d 局来自同一个 replay 补丁 %s。这一类的差异更可能是"
                "版本/赛季差异，而不是两套不同的打法——看结论时请把这点考虑进去。"
                % (cluster["name"], top_count, total, top_build))
    if mine:
        sig_share = {}
        for bo in mine:
            sig_share[bo["signature"]] = sig_share.get(bo["signature"], 0) + 1
        top_sig, top_n = max(sig_share.items(), key=lambda kv: kv[1])
        if top_n / float(len(mine)) >= 0.8:
            notes.append(
                "%d/%d 局（%.0f%%）都是同一套开局签名 %s，所以聚出来的两类是同一套"
                "开局的两种节奏，不是两种完全不同的建造顺序。" % (
                    top_n, len(mine), 100.0 * top_n / len(mine), top_sig))

    rejects = rejects or {}
    games_with_rejects = [item for group in ("mine", "pro")
                          for item in (rejects.get(group) or [])]
    if games_with_rejects:
        items = [item for game in games_with_rejects for item in game["items"]]
        early = [item for item in items if item["reason"] == "too_early"]
        span = [item for item in items if item["reason"] == "span_mismatch"]
        builds = sorted({str(game.get("build")) for game in games_with_rejects})
        notes.append(
            "数据噪音：%d 条扩张记录被丢弃（%d 条时间 < %d 秒，%d 条 done-start 与真实"
            "建造时长不符，涉及 %d 局，replay 补丁 %s）。这类错配集中在 ESL/GameHeart "
            "老 replay：overlay 单位 + tag 回收会让 tracker 事件挂到别的单位上，"
            "出现 81~84 秒就下二矿这种物理不可能的记录。这些局的首个扩张已顺延到下一个"
            "可信候选（所以它们看起来开矿偏晚，签名也可能跟着变），不要当成真实打法。"
            % (len(items), len(early), int(EXPAND_MIN_SECONDS), len(span),
               len(games_with_rejects), "、".join(builds)))
    if pro:
        notes.append(
            "职业选手样本只有 %d 局（其中 %s 对局 %d 局），中位数波动会比较大。" % (
                len(pro), matchup,
                len([bo for bo in pro if bo["game"]["matchup"] == matchup])))
    return notes


def build_summary(mine, pro_all, mine_label="我", pro_label="Maru", matchup="TvZ",
                  stats=None):
    """把所有分析结果整理成一个 dict（就是 reports/bo.json 的内容）。

    ``pro_all`` 是这个职业选手的全部录像；聚类/对照只用其中 matchup 匹配的局
    （Maru 的 TvP 不会污染 TvZ 开局聚类），但 ``games`` 里全都保留。
    """
    pro = [bo for bo in pro_all if not matchup or bo["game"]["matchup"] == matchup]
    clusters = cluster_openers(mine, 2)
    pro_clusters = cluster_openers(pro, 2) if len(pro) >= 2 else []
    return {
        "meta": {
            "matchup": matchup,
            "mine": mine_label,
            "pro": pro_label,
            "generated": _today(),
            "games": len(mine) + len(pro_all),
            "mine_games": len(mine),
            "pro_games": len(pro_all),
            "pro_matchup_games": len(pro),
            "signature_legend": {
                "1-rax-fast-expand": "单兵营速二矿（重工/星港都在二矿之后）",
                "2-rax-fast-expand": "两兵营速二矿",
                "2-1-1": "两兵营 + 重工 + 星港（收气 timing）",
                "1-1-1": "单兵营 + 重工 + 星港",
                "3-rax": "三兵营压制，开矿晚",
                "mech": "重工优先（机械流）",
                "unknown": "没匹配到已知开局（诚实标注，不硬塞）",
            },
            "style_legend": {
                "bio": "生化（陆战队/劫掠者/死神）",
                "mech": "机械（恶火/坦克/雷神/寡妇雷）",
                "air": "空军（维京/女妖/渡鸦/解放者/大和）",
                "mixed": "混合",
                "unknown": "判不出来",
            },
            "phase_table": [{"phase": p, "t0": s, "t1": e} for p, s, e in PHASES],
            "unknown": {
                "mine_signature_share": _unknown_share(mine, "signature"),
                "mine_style_share": _unknown_share(mine, "style"),
                "pro_signature_share": _unknown_share(pro, "signature"),
                "pro_style_share": _unknown_share(pro, "style"),
            },
            "expand_filter": {
                "min_seconds": EXPAND_MIN_SECONDS,
                "span_tolerance": EXPAND_SPAN_TOLERANCE,
                "expected_build_seconds": dict(EXPAND_BUILD_SECONDS),
                "rejected": sum(len(entry["items"]) for entry in
                                ((stats or {}).get("expand_rejects", {}) or {})
                                .get("mine", []) +
                                ((stats or {}).get("expand_rejects", {}) or {})
                                .get("pro", [])),
            },
        },
        "games": mine + pro_all,
        "clusters": {
            "mine": clusters,
            "pro": pro_clusters,
        },
        "comparison": compare_profiles(mine, pro, mine_label, pro_label),
        "caveats": _caveats(clusters, mine, pro, matchup,
                            (stats or {}).get("expand_rejects")),
        "sampling": {
            "mine": (stats or {}).get("mine") or {"matched": len(mine)},
            "pro": (stats or {}).get("pro") or {"matched": len(pro_all)},
            "expand_rejects": (stats or {}).get("expand_rejects") or {"mine": [], "pro": []},
        },
        "errors": mine_folder.last_errors,
    }


def _today():
    import datetime
    return datetime.date.today().isoformat()


def write_json(summary, output_path):
    directory = os.path.dirname(os.path.abspath(output_path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=1)
    return output_path


# --------------------------------------------------------------------------- CLI

MINE_DIR = r"C:\Users\sun\Documents\StarCraft II\Accounts\556922532\5-S2-1-8354391\Replays\Multiplayer"
PRO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "replays", "pro", "maru")


def run(mine_dir=MINE_DIR, pro_dir=PRO_DIR, limit=60, pro_limit=30,
        matchup="TvZ", seed=20261108, progress=None):
    """挖矿 + 聚类 + 对照，返回 summary dict。

    ``pro_limit`` 默认 30：职业录像文件夹现在有 24 局，得放够才不会抽样截断
    （Maru 的 TvZ 一共 17 局，截断会漏）。
    """
    mine = mine_folder(mine_dir, limit=limit, matchup=matchup, seed=seed,
                       progress=progress, ladder_only=True, min_duration=120.0,
                       require_local=True, exclude_ai=True)
    mine_stats = dict(mine_folder.last_stats)
    mine_rejects = mine_stats.pop("rejected_expands", [])
    # 职业选手的录像全收（TvP/TvT 也留着），聚类和对照再按 matchup 过滤
    pro = mine_folder(pro_dir, limit=pro_limit, matchup=None, prefer_name="maru",
                      seed=seed, progress=progress)
    pro_stats = dict(mine_folder.last_stats)
    pro_rejects = pro_stats.pop("rejected_expands", [])
    return build_summary(mine, pro, "我", "Maru", matchup, stats={
        "mine": mine_stats,
        "pro": pro_stats,
        "expand_rejects": {"mine": mine_rejects, "pro": pro_rejects},
    })


def main(argv=None):
    import argparse
    import sys
    import time

    parser = argparse.ArgumentParser(prog="python -m replay_analyzer.bo",
                                     description="BO 提取 + 开局聚类 + 报告")
    parser.add_argument("--mine", default=MINE_DIR, help="我的录像目录")
    parser.add_argument("--pro", default=PRO_DIR, help="职业选手录像目录")
    parser.add_argument("--limit", type=int, default=60, help="我的录像抽样局数")
    parser.add_argument("--pro-limit", type=int, default=30, help="职业录像局数")
    parser.add_argument("--matchup", default="TvZ")
    parser.add_argument("--seed", type=int, default=20261108)
    parser.add_argument("--json", default=os.path.join("reports", "bo.json"))
    parser.add_argument("--html", default=os.path.join("reports", "bo.html"))
    parser.add_argument("--replay", action="append", default=[],
                        help="额外单独分析某局（可重复），会打印 signature")
    args = parser.parse_args(argv)

    started = time.time()
    checks = []
    for path in args.replay:
        game = extract_bo_path(path)
        checks.append({
            "file": game["game"]["file"],
            "path": game["game"]["path"],
            "matchup": game["game"]["matchup"],
            "signature": game["signature"],
            "style": game["style"],
            "key_timings": game["key_timings"],
            "segments": [{"phase": s["phase"], "signature": s["signature"],
                          "t0": s["t0"], "t1": s["t1"]} for s in game["segments"]],
            "opening_actions": (game["segments"][0]["key_actions"]
                                if game["segments"] else []),
        })
        sys.stdout.write("%s  signature=%s style=%s expand=%s\n" % (
            game["game"]["file"], game["signature"], game["style"],
            game["key_timings"]["first_expand"]))

    summary = run(args.mine, args.pro, args.limit, args.pro_limit,
                  args.matchup, args.seed,
                  progress=lambda done, name: sys.stderr.write("\r  parsed %d/%d games" % (done, args.limit)))
    summary["replay_checks"] = checks
    summary["meta"]["elapsed_seconds"] = round(time.time() - started, 1)
    write_json(summary, args.json)

    from . import bo_report
    bo_report.write_report(summary, args.html)
    sys.stderr.write("\n")
    sys.stdout.write("wrote %s (%d games) and %s in %.1fs\n" % (
        args.json, len(summary["games"]), args.html, time.time() - started))
    for cluster in summary["clusters"]["mine"]:
        sys.stdout.write("  mine cluster: %-32s %2d games  share=%3.0f%%  (median expand %s)\n" % (
            cluster["key"], cluster["games"], cluster["share"] * 100,
            cluster["median"].get("first_expand")))
    for cluster in summary["clusters"]["pro"]:
        sys.stdout.write("  pro  cluster: %-32s %2d games  share=%3.0f%%\n" % (
            cluster["key"], cluster["games"], cluster["share"] * 100))
    return summary


if __name__ == "__main__":
    main()
