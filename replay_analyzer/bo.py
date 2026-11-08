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
避免 2-1-1 把 2-rax-fast-expand 吃掉。

## 虫族 / 星灵的 signature（够 DAG 用，不做过度设计）

* 虫族：``hatch-first`` / ``pool-first`` / ``greedy-3hatch`` / ``unknown``。
  看 0~6 分钟：二矿（Hatchery）与孵化池（SpawningPool）谁先下、6 分钟内下了几个基地。
* 星灵：``nexus-first`` / ``gateway-expand`` / ``2gate-pressure`` / ``proxy`` / ``unknown``。
  看二矿（Nexus）与兵营（Gateway）谁先下、5 分钟内兵营数量、以及兵营是否建在
  离主基地 > 50 格的位置（proxy 的粗略代理）。

## 聚类轴：0–180 秒的建造序列（不是整局关键时间点）

用户说的"两种 BO"指的是**开局 0~3 分钟的流程**，而且可能不止两种。所以
:func:`cluster_openers` 把每局压缩成 opening 段（0–180s）的结构 token 序列
（如 ``depot-rax-gas-cc``），然后：

* 距离 = 归一化 Levenshtein（编辑距离 / 较长序列长度）；
* 聚类 = 平均连接（average-linkage）层次聚类，确定性、无随机种子；
* 选 k：k=2..6 各跑一遍，用平均轮廓系数（silhouette）最高者，且最小类占比 < 8%
  的方案不参与竞争（避免切出只有 1 局的噪声类）；每个 k 的分数都会写进
  ``summary.meta.clustering``，报告里列表可复核。
* 输出每类的占比、典型序列（簇内最常见序列，票数相同取 medoid）、
  序列里每个 token 的中位出现时间、以及整局 key_timings 的中位/平均值。

整局的 ``key_timings`` 保留不动，只用于"我 vs 职业"对照表和逐局明细。

## 版本过滤：8 农民 vs 12 农民必须分开

用户录像跨了多个版本：2026-07~10 那批是 **8 农民开局**，其余（2026-01~06、2026-10 部分、
2026-11）是 **12 农民开局**。8 农民版本会让同一个 build 的开局时间点整体前移 20~30 秒，
混在一起聚出来的类就是版本差而不是打法差。所以 ``mine_folder`` 默认按
``start_workers == 12`` 硬过滤（版本无关、不会错杀），``--since/--until`` 只是可选的
日期区间；报告里会写明实际用了哪段时间、排除了多少局、以及扫到的版本分布表。


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
    """开局签名。分种族判定，词表封闭（见模块 docstring）。

    人族用"扩张时机 + 扩张前兵营数 + 6~8 分钟生产建筑构成"；
    虫族/星灵用 0~6 分钟的建筑顺序（够 DAG 用即可，不追求精确战术名）。
    """
    if player.race == "T":
        return _terran_signature(parsed, player, steps, timings)
    if player.race == "Z":
        return _zerg_signature(parsed, player, steps, timings)
    if player.race == "P":
        return _protoss_signature(parsed, player, steps, timings)
    return "unknown"


def _terran_signature(parsed, player, steps, timings):
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


def _zerg_signature(parsed, player, steps, timings):
    """虫族：hatch-first / pool-first / greedy-3hatch / unknown。

    只看 0~6 分钟：扩张基地（Hatchery）与孵化池（SpawningPool）的先后 + 扩张数量。
    """
    hatches = _builds(steps, ("Hatchery",))
    pools = _builds(steps, ("SpawningPool",))
    hatches_360 = [t for t in hatches if t <= 360.0]
    pool = pools[0] if pools else None

    # greedy-3hatch：6 分钟内已经下到第 2、第 3 个基地（含主基地共 3 个）。
    if len(hatches_360) >= 2 and (pool is None or hatches_360[0] <= pool):
        return "greedy-3hatch"
    # hatch-first：先开二矿（或先下基地）再补池子。
    if hatches and (pool is None or hatches[0] < pool):
        return "hatch-first"
    # pool-first：池子先于扩张（含不开矿的单矿池开）。
    if pool is not None:
        return "pool-first"
    return "unknown"


def _protoss_signature(parsed, player, steps, timings):
    """星灵：nexus-first / gateway-expand / 2gate-pressure / proxy / unknown。

    判据：第一个 Nexus（二矿）与第一个 Gateway 的先后、Gateway 数量、
    以及 Gateway 是否建在远离主基地的位置（proxy 的粗略代理：直线距离 > 50）。
    """
    nexus = _builds(steps, ("Nexus",))
    gates = _builds(steps, ("Gateway",))
    first_nexus = nexus[0] if nexus else None
    first_gate = gates[0] if gates else None

    # proxy：0~5 分钟内造的兵营离主基地特别远（用单位坐标，不用事件流名字猜）。
    try:
        main_base = None
        for record in parsed.unit_records:
            if (record.player_id == player.player_id and record.name in ("Nexus", "Hatchery", "CommandCenter")
                    and (record.start_loop or 0) <= 0 and record.x is not None):
                main_base = (record.x, record.y)
                break
        if main_base:
            for record in parsed.unit_records:
                if (record.player_id == player.player_id and record.name == "Gateway"
                        and record.x is not None and record.start_loop is not None
                        and 0 < record.start_loop / LOOPS_PER_SECOND <= 300.0
                        and math.hypot(record.x - main_base[0], record.y - main_base[1]) > 50.0):
                    return "proxy"
    except Exception:
        pass

    if first_nexus is not None and first_nexus <= 240.0 and (first_gate is None or first_nexus < first_gate):
        return "nexus-first"
    if len([t for t in gates if t <= 300.0]) >= 2 and (first_nexus is None or first_nexus > 420.0):
        return "2gate-pressure"
    if first_gate is not None and first_nexus is not None and first_gate < first_nexus:
        return "gateway-expand"
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


def start_workers(parsed, player_id):
    """开局农民数：loop=0 的 SCV/Probe/Drone 记录数。

    这是**版本无关**的过滤依据：8 农民和 12 农民是两个不同的游戏版本，混在一起
    会让开局时间点整体前移，聚出来的类就是版本差而不是打法差。
    正常 12 农民版本返回 12，老版本返回 8；读不到（事件缺失）返回 0。
    """
    return len([event for event in parsed.build_events
                if event.get("player_id") == player_id
                and (event.get("loop") or 0) == 0
                and event.get("name") in ud.WORKER_UNITS])


def _replay_day(parsed):
    """replay 的日期（本地时区 YYYY-MM-DD），读不到返回空串。"""
    if parsed.replay_time is None:
        return ""
    return parsed.replay_time.astimezone().strftime("%Y-%m-%d")


def mine_folder(paths, limit=60, matchup=None, prefer_name=None,
                seed=20261108, with_moveout=True, progress=None,
                ladder_only=False, min_duration=0.0,
                require_local=False, exclude_ai=False,
                since=None, until=None, start_workers_only=None):
    """抽样解析一批 replay，返回 ``[GameBO]``。

    * ``paths`` 可以是目录或路径列表（递归找 .SC2Replay）
    * ``limit`` 最多返回多少局（匹配 matchup 的才算）
    * ``matchup`` 形如 ``"TvZ"``：本地玩家种族 + 对手种族都匹配才收
    * ``prefer_name`` 职业录像用来指定"我要分析谁"
    * ``ladder_only`` 只收暴雪官方地图上的局（过滤自建图/街机图）
    * ``min_duration`` 丢掉太短的对局（默认叫 120 秒的残局不算 BO）
    * ``require_local`` 只收"我在里面"的局（排除下载来的别人的录像）
    * ``exclude_ai`` 只收人类对手（人机局的节奏跟天梯不是一回事）
    * ``since`` / ``until``：按 replay 日期（YYYY-MM-DD，含端点）保留区间，默认都不设
    * ``start_workers_only``：只收开局农民数等于该值的局（版本硬过滤，**默认 12**）

    两段式：先用 ``decode_events=False`` 做**便宜预筛**（只读元信息，约 0.05s/局），
    筛中了才 ``decode_events=True`` 深解析（约 1.2s/局）。所以即使 1000 局里
    只有 10% 是目标 matchup，也不用把 1000 局全解码一遍。

    确定性抽样：``random.Random(seed)`` 打乱后顺序扫，同一批文件永远抽到同一组。
    被过滤掉多少局、各是什么原因，都记在 ``mine_folder.last_stats`` 里（报告要用）。
    """
    files = expand_paths(paths)
    order = list(files)
    random.Random(seed).shuffle(order)

    cheap_only = bool(matchup or require_local or exclude_ai or ladder_only or min_duration
                      or since or until or start_workers_only is not None)
    out = []
    errors = []
    stats = {"scanned": 0, "matched": 0, "skipped_filter": 0, "deep_parsed": 0,
             "skipped_workers": 0, "skipped_date": 0,
             "start_workers_seen": {}, "dates_seen": {},
             "versions_seen": []}
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
        # 版本过滤：开局农民数（版本无关）比日期可靠，所以放在日期前面
        workers = start_workers(parsed, player.player_id)
        stats["start_workers_seen"][workers] = stats["start_workers_seen"].get(workers, 0) + 1
        day = _replay_day(parsed)
        if day:
            stats["dates_seen"][day] = stats["dates_seen"].get(day, 0) + 1
        stats["versions_seen"].append({
            "file": os.path.basename(path),
            "date": day,
            "build": parsed.version.get("build"),
            "start_workers": workers,
        })
        if start_workers_only is not None and workers != start_workers_only:
            stats["skipped_workers"] += 1
            continue
        if since and day and day < since:
            stats["skipped_date"] += 1
            continue
        if until and day and day > until:
            stats["skipped_date"] += 1
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
        stats.setdefault("matched_dates", []).append(day or "")
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


#: 聚类轴：**opening 段（0–180s）的建造序列**。整局 key_timings 只用于报告对照/对照表，
#: 不参与聚类——用户说的"两种 BO"指的是开局 0~3 分钟的流程。
OPENING_PHASE = "opening"

#: 序列里 token 的中文短名（"典型序列"和簇名用）。
TOKEN_ZH = {
    "depot": "补给站", "rax": "兵营", "gas": "气矿", "cc": "二矿",
    "fac": "重工", "sp": "星港", "reac": "反应堆", "lab": "科技实验室",
    "ebay": "工程站", "armory": "军械库", "bunker": "地堡", "turret": "导弹塔",
    "gate": "传送门", "core": "控制核心", "twilight": "暮光议会",
    "robo": "机械厂", "roboBay": "机械湾", "sg": "星门",
    "forge": "锻炉", "cannon": "光子炮", "battery": "护盾电池",
    "archives": "圣堂文库", "shrine": "黑暗圣所",
    "pool": "孵化池", "bane": "爆虫巢", "roachWarren": "蟑螂巢",
    "evo": "进化腔", "hydra": "刺蛇巢", "spire": "尖塔",
    "ultra": "雷兽窟", "nydus": "虫道网络", "infect": "感染深渊",
    "hatch": "基地", "nexus": "基地",
}

#: 序列参与聚类的最大长度（0~180s 正常 6~12 个建筑，截断防止个别局刷屏）。
SEQUENCE_LIMIT = 10


def opening_sequence(bo, limit=SEQUENCE_LIMIT):
    """opening 段（0–180s）的建造序列：只取能代表思路的建筑 token。"""
    tokens = []
    for segment in bo["segments"]:
        if segment["phase"] != OPENING_PHASE:
            continue
        for step in segment["steps"]:
            if step["kind"] != "structure":
                continue
            token = _SIG_TOKEN.get(step["name"])
            if token:
                tokens.append(token)
    return tokens[:limit]


def opening_token_times(bo):
    """opening 段里每个 token 第一次出现的时间（秒）。"""
    times = {}
    for segment in bo["segments"]:
        if segment["phase"] != OPENING_PHASE:
            continue
        for step in segment["steps"]:
            if step["kind"] != "structure":
                continue
            token = _SIG_TOKEN.get(step["name"])
            if token and token not in times:
                times[token] = step["t"]
    return times


def _edit_distance(a, b):
    """Levenshtein 距离（token 序列）。"""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, token_a in enumerate(a, 1):
        cur = [i]
        for j, token_b in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (0 if token_a == token_b else 1)))
        prev = cur
    return prev[-1]


def sequence_distance(seq_a, seq_b):
    """两条开局序列的距离（0 = 一样，1 = 完全不同）。

    关键点：0–180s 的时间窗会把不同局截在流程的不同位置，所以"一个序列是另一个的
    前缀"（比如 rax-gas-cc vs rax-gas-cc-fac）不算打法差异，只按长度差轻微惩罚；
    只有**分叉之后**的部分才用编辑距离算差异。
    """
    if not seq_a and not seq_b:
        return 0.0
    denominator = float(max(len(seq_a), len(seq_b), 1))
    prefix = 0
    while prefix < min(len(seq_a), len(seq_b)) and seq_a[prefix] == seq_b[prefix]:
        prefix += 1
    if prefix == min(len(seq_a), len(seq_b)):
        # 一个是另一个的前缀：差异只来自时间窗截断
        return abs(len(seq_a) - len(seq_b)) / denominator
    return _edit_distance(seq_a[prefix:], seq_b[prefix:]) / denominator


def _distance_matrix(sequences):
    n = len(sequences)
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            value = sequence_distance(sequences[i], sequences[j])
            dist[i][j] = dist[j][i] = value
    return dist


def _avg_link(dist, left, right):
    return sum(dist[i][j] for i in left for j in right) / float(len(left) * len(right))


def _agglomerate(dist, k):
    """平均连接（average linkage）层次聚类，切到 k 簇。确定性，无随机。"""
    n = len(dist)
    clusters = [[i] for i in range(n)]
    while len(clusters) > k:
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                candidate = _avg_link(dist, clusters[i], clusters[j])
                if best is None or candidate < best[0]:
                    best = (candidate, i, j)
        _, i, j = best
        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]
    clusters.sort(key=lambda idx: (-len(idx), min(idx)))
    labels = [0] * n
    for label, indices in enumerate(clusters):
        for index in indices:
            labels[index] = label
    return labels


def _silhouette(dist, labels):
    """平均轮廓系数（用预计算的距离矩阵，0 附近 = 没有结构，越接近 1 越分明）。"""
    n = len(dist)
    groups = {}
    for index, label in enumerate(labels):
        groups.setdefault(label, []).append(index)
    if len(groups) < 2:
        return -1.0
    scores = []
    for index in range(n):
        own = [j for j in groups[labels[index]] if j != index]
        if not own:
            scores.append(0.0)
            continue
        a = sum(dist[index][j] for j in own) / float(len(own))
        b = None
        for label, indices in groups.items():
            if label == labels[index]:
                continue
            mean = sum(dist[index][j] for j in indices) / float(len(indices))
            if b is None or mean < b:
                b = mean
        if b is None or max(a, b) == 0.0:
            scores.append(0.0)
        else:
            scores.append((b - a) / max(a, b))
    return round(sum(scores) / float(n), 3)


def _within_mean(dist, labels):
    total, count = 0.0, 0
    for i in range(len(dist)):
        for j in range(i + 1, len(dist)):
            if labels[i] == labels[j]:
                total += dist[i][j]
                count += 1
    return round(total / count, 3) if count else 0.0


#: 离群判定：到最近邻的距离超过它，就当"不典型的一局"——单独归到 other 类，
#: 不参与选 k（否则一两个怪局会把 k 顶到 5~6，切出一堆只有 1 局的"类"）。
OUTLIER_DISTANCE = 0.55


def _outlier_flags(dist):
    flags = []
    for index, row in enumerate(dist):
        nearest = min((row[j] for j in range(len(row)) if j != index), default=1.0)
        flags.append(nearest > OUTLIER_DISTANCE)
    return flags


def choose_k(dist, k_min=2, k_max=6):
    """k=2..6 各跑一遍，用平均轮廓系数选 k。

    * 会产生只有 1 局的碎类的方案直接淘汰；
    * 在"轮廓系数不低于最高分 - 0.03"的方案里取**最小的 k**（简约优先，避免过度切分）。

    返回 ``(chosen_k, scores)``；scores 每个 k 带 silhouette / within_mean / min_share，
    报告里会列表复核。
    """
    n = len(dist)
    k_max = min(k_max, max(2, n - 1))
    scores = []
    chosen = k_min
    for k in range(k_min, k_max + 1):
        labels = _agglomerate(dist, k)
        if len(set(labels)) < 2:
            continue
        sizes = {}
        for label in labels:
            sizes[label] = sizes.get(label, 0) + 1
        if min(sizes.values()) < 2:                 # 单例方案不参与竞争
            continue
        scores.append({
            "k": k,
            "silhouette": _silhouette(dist, labels),
            "within_mean": _within_mean(dist, labels),
            "min_share": round(min(sizes.values()) / float(n), 3),
            "sizes": sizes,
        })
    if scores:
        best_score = max(s["silhouette"] for s in scores)
        chosen = min(s["k"] for s in scores if s["silhouette"] >= best_score - 0.03)
    return chosen, scores


def _pace_of(median):
    """快/慢节奏标签（只用于展示）：看第一气矿与二矿的中位时间。"""
    gas = (median or {}).get("first_gas")
    expand = (median or {}).get("first_expand")
    if (gas is not None and gas < 75.0) or (expand is not None and expand < 155.0):
        return "fast"
    return "slow"


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


def _typical_sequence(sequences):
    """簇内最常见的开场序列；票数相同就取"离其他成员最近"的那条（medoid）。"""
    tally = {}
    for seq in sequences:
        key = tuple(seq)
        tally[key] = tally.get(key, 0) + 1
    if not tally:
        return []
    top = max(tally.values())
    candidates = [key for key, count in tally.items() if count == top]
    if len(candidates) == 1:
        return list(candidates[0])

    def total_distance(candidate):
        return sum(sequence_distance(list(candidate), seq) for seq in sequences)

    return list(min(candidates, key=total_distance))


def _sequence_label(sequence, limit=5):
    return " → ".join(TOKEN_ZH.get(token, token) for token in sequence[:limit])


def _sequence_medians(members, sequence):
    """典型序列里每个 token 的中位出现时间。"""
    rows = []
    timing_sets = [opening_token_times(bo) for bo in members]
    for token in sequence:
        values = [times[token] for times in timing_sets if token in times]
        rows.append({
            "token": token,
            "label": TOKEN_ZH.get(token, token),
            "t": _median(values),
        })
    return rows


def cluster_openers(bos, k=None, k_range=(2, 6), info=None):
    """按 **0–180s 建造序列** 把同一批开局聚成若干类（k 自动选，默认候选 2..6）。

    * 距离 = 前缀感知的归一化编辑距离（见 :func:`sequence_distance`）；
    * 聚类 = 平均连接（average linkage）层次聚类，确定性、无随机；
    * 先剔除离群序列（最近邻距离 > :data:`OUTLIER_DISTANCE` 的局归到 other 类，
      不参与选 k），再在 2..6 里按平均轮廓系数选 k（简约优先，见 :func:`choose_k`）。

    ``info`` 传 dict 时会把选 k 的依据（每个 k 的 silhouette / 类内平均距离 / 最小类占比、
    离群局数）一起带出去，报告里要展示"为什么选这个 k"。

    返回 ``[{"id","key","name","typical_sequence","sequence_medians","games","share",
    "pace","mean","median","signatures","styles","first_army","builds","members"}]``，
    按局数从多到少排序。
    """
    races = {}
    for bo in bos:
        race = bo["player"]["race"]
        races[race] = races.get(race, 0) + 1
    dominant = max(races, key=lambda r: (races[r], r)) if races else None
    usable = [bo for bo in bos if bo["player"]["race"] == dominant]
    excluded = [bo["game"]["file"] for bo in bos if bo["player"]["race"] != dominant]
    if len(usable) < 2:
        return []

    sequences = [opening_sequence(bo) for bo in usable]
    full_dist = _distance_matrix(sequences)
    outlier_flags = _outlier_flags(full_dist)
    inliers = [index for index, flag in enumerate(outlier_flags) if not flag]
    outliers = [index for index, flag in enumerate(outlier_flags) if flag]
    # 离群太多（>40%）说明这批数据本来就散，别硬切：全部当 inlier
    if len(inliers) < 4 or len(outliers) > len(usable) * 0.4:
        inliers = list(range(len(usable)))
        outliers = []
    sub_dist = [[full_dist[i][j] for j in inliers] for i in inliers]

    if k is None:
        chosen_k, scores = choose_k(sub_dist, k_range[0],
                                    min(k_range[1], max(2, len(inliers) - 1)))
    else:
        chosen_k, scores = k, []
    if info is not None:
        info["k"] = chosen_k
        info["scores"] = scores
        info["race"] = dominant
        info["outliers"] = [usable[index]["game"]["file"] for index in outliers]
        info["criterion"] = (
            "先剔除离群序列（最近邻距离 > %.2f），再按平均轮廓系数选 k；"
            "会产生单例的方案淘汰，轮廓系数差距 0.03 以内取最小的 k" % OUTLIER_DISTANCE)
    labels = [chosen_k] * len(usable)                 # 离群局统一标成"最后那个类"
    for position, index in enumerate(inliers):
        labels[index] = None
    if inliers:
        for position, label in enumerate(_agglomerate(sub_dist, chosen_k)):
            labels[inliers[position]] = label

    clusters = []
    for label in sorted(set(labels)):
        indices = [index for index, lab in enumerate(labels) if lab == label]
        members = [usable[index] for index in indices]
        summary = _summarize(members)
        typical = _typical_sequence([sequences[index] for index in indices])
        pace = _pace_of(summary["median"])
        is_other = bool(outliers) and label == chosen_k
        clusters.append({
            "id": label,
            "key": "other" if is_other else ("-".join(typical[:6]) or ("cluster-%d" % (label + 1))),
            "name": ("其他（打法不典型，%d 局）" % len(members) if is_other
                     else (_sequence_label(typical) or ("开局 %d" % (label + 1)))),
            "typical_sequence": typical,
            "sequence_medians": _sequence_medians(members, typical),
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
    # key/name 去重（不同簇可能撞上同一典型序列）
    used = {}
    for cluster in clusters:
        base = cluster["key"]
        used[base] = used.get(base, 0) + 1
        if used[base] > 1:
            cluster["key"] = "%s-%d" % (base, used[base])
    if excluded:
        for cluster in clusters:
            cluster["excluded_other_race"] = excluded
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


def _version_table(stats, group="mine"):
    """把扫到的 (月份, 开局农民数, build) 组合汇总成表：版本分布一眼能看出来。"""
    seen = ((stats or {}).get(group) or {}).get("versions_seen") or []
    table = {}
    for entry in seen:
        month = (entry.get("date") or "")[:7] or "?"
        key = (month, entry.get("start_workers"), entry.get("build"))
        table[key] = table.get(key, 0) + 1
    return [{"month": month, "start_workers": workers, "build": build, "games": count}
            for (month, workers, build), count in sorted(table.items(),
                                                         key=lambda kv: (kv[0][0], str(kv[0][1]), str(kv[0][2])))]


def _percentile(values, p):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(float(vals[0]), 1)
    key = (len(vals) - 1) * (p / 100.0)
    low, high = int(key), min(int(key) + 1, len(vals) - 1)
    return round(vals[low] + (vals[high] - vals[low]) * (key - low), 1)


def _pace_dimension(mine, clusters):
    """版本过滤后，"慢/快节奏"这个维度还在不在。

    口径：看过滤后全部对局的第一气矿/二矿时间分布（p10~p90 的跨度）。
    之前那个"快/慢"双峰主要是 8 农民版本和 12 农民版本混在一起造成的；
    版本过滤后如果跨度收窄到 20 秒以内，就说明这个维度基本消失了。
    """
    gas_series = [bo["key_timings"].get("first_gas") for bo in mine]
    expand_series = [bo["key_timings"].get("first_expand") for bo in mine]
    gas_p10, gas_p50, gas_p90 = (_percentile(gas_series, p) for p in (10, 50, 90))
    expand_p10, expand_p50, expand_p90 = (_percentile(expand_series, p) for p in (10, 50, 90))
    gas_spread = round(gas_p90 - gas_p10, 1) if None not in (gas_p10, gas_p90) else None
    expand_spread = (round(expand_p90 - expand_p10, 1)
                     if None not in (expand_p10, expand_p90) else None)
    survives = bool((gas_spread is not None and gas_spread > 20.0)
                    or (expand_spread is not None and expand_spread > 25.0))
    return {
        "survives": survives,
        "gas_p10": gas_p10, "gas_p50": gas_p50, "gas_p90": gas_p90,
        "gas_spread": gas_spread,
        "expand_p10": expand_p10, "expand_p50": expand_p50, "expand_p90": expand_p90,
        "expand_spread": expand_spread,
        "per_cluster": [{"name": c["name"], "first_gas": c["median"].get("first_gas"),
                         "first_expand": c["median"].get("first_expand")} for c in clusters],
    }


def _version_note(stats):
    """版本过滤 caveat：写明 8/12 农民两个版本、排除了多少局、为什么必须排除。"""
    mine_stats = (stats or {}).get("mine") or {}
    seen = mine_stats.get("start_workers_seen") or {}
    skipped = mine_stats.get("skipped_workers", 0)
    table = _version_table(stats, "mine")
    rows = {}
    for entry in table:
        month = entry["month"]
        rows.setdefault(month, {"workers": set(), "builds": set()})
        rows[month]["workers"].add(entry["start_workers"])
        rows[month]["builds"].add(str(entry["build"]))
    lines = []
    for month in sorted(rows):
        info = rows[month]
        lines.append("%s：%s 农民 / build %s" % (
            month, "/".join(str(w) for w in sorted(info["workers"], key=str)),
            "、".join(sorted(info["builds"]))))
    detail = "；".join(lines) if lines else "-"
    return (
        "版本过滤（硬条件：开局农民数 = %s）：扫到的局里开局农民数分布 %s；本次因此排除 %d 局。"
        "实测版本表：%s。混进 8 农民版本会让开局时间点整体前移（同一个 build 在 8 农民版本上"
        "会早 20~30 秒），聚出来的类就是版本差而不是打法差——所以 8 农民版本必须整批排除。"
        % (
            (mine_stats.get("start_workers_filter") or 12),
            "、".join("%s 农民 %d 局" % (key, value) for key, value in
                     sorted(seen.items(), key=lambda kv: str(kv[0]))),
            skipped, detail))


def _caveats(clusters, mine, pro, matchup, rejects=None, stats=None, k_info=None):
    """报告里必须说清楚的话：哪些结论是数据支撑的，哪些只是"看起来像"。"""
    notes = []
    if stats:
        notes.append(_version_note(stats))
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
                "%d/%d 局（%.0f%%）都是同一套开局签名 %s。" % (
                    top_n, len(mine), 100.0 * top_n / len(mine), top_sig))
    if k_info:
        notes.append(
            "聚类口径：按 0~180 秒的建造序列（归一化编辑距离 + 平均连接层次聚类），"
            "k 在 %d~%d 里按平均轮廓系数选，本次 k=%d。" % (
                k_info.get("k_min", 2), k_info.get("k_max", 6), k_info.get("k", 0)))

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
                  stats=None, k_range=(2, 6)):
    """把所有分析结果整理成一个 dict（就是 reports/bo.json 的内容）。

    ``pro_all`` 是这个职业选手的全部录像；聚类/对照只用其中 matchup 匹配的局
    （Maru 的 TvP 不会污染 TvZ 开局聚类），但 ``games`` 里全都保留。
    聚类轴 = 0~180s 建造序列（见 :func:`cluster_openers`），k 自动选 2~6。
    """
    pro = [bo for bo in pro_all if not matchup or bo["game"]["matchup"] == matchup]
    mine_info, pro_info = {}, {}
    clusters = cluster_openers(mine, k=None, k_range=k_range, info=mine_info)
    pro_clusters = (cluster_openers(pro, k=None, k_range=k_range, info=pro_info)
                    if len(pro) >= 3 else [])
    k_info = {
        "axis": "opening-sequence-0-180s",
        "criterion": mine_info.get("criterion", ""),
        "k_min": k_range[0],
        "k_max": k_range[1],
        "k": mine_info.get("k"),
        "scores": mine_info.get("scores") or [],
        "outliers": mine_info.get("outliers") or [],
        "pro_k": pro_info.get("k"),
        "pro_scores": pro_info.get("scores") or [],
    }
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
                "hatch-first": "虫族：先开二矿再补池",
                "pool-first": "虫族：池子先于扩张",
                "greedy-3hatch": "虫族：6 分钟内三基地（贪婪开）",
                "nexus-first": "星灵：先二矿再兵营",
                "gateway-expand": "星灵：先兵营再开矿（1 兵营开矿）",
                "2gate-pressure": "星灵：双兵营压制、不开矿",
                "proxy": "星灵：兵营贴脸（proxy）",
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
            "clustering": k_info,
            "pace_dimension": _pace_dimension(mine, clusters),
            "version_filter": {
                "start_workers": ((stats or {}).get("mine") or {}).get("start_workers_filter"),
                "since": ((stats or {}).get("mine") or {}).get("since"),
                "until": ((stats or {}).get("mine") or {}).get("until"),
                "dates_used": _date_span((stats or {}).get("mine")),
                "excluded_by_workers": ((stats or {}).get("mine") or {}).get("skipped_workers", 0),
                "excluded_by_date": ((stats or {}).get("mine") or {}).get("skipped_date", 0),
                "version_table": _version_table(stats, "mine"),
            },
        },
        "games": mine + pro_all,
        "clusters": {
            "mine": clusters,
            "pro": pro_clusters,
        },
        "comparison": compare_profiles(mine, pro, mine_label, pro_label),
        "caveats": _caveats(clusters, mine, pro, matchup,
                            (stats or {}).get("expand_rejects"), stats, k_info),
        "sampling": {
            "mine": (stats or {}).get("mine") or {"matched": len(mine)},
            "pro": (stats or {}).get("pro") or {"matched": len(pro_all)},
            "expand_rejects": (stats or {}).get("expand_rejects") or {"mine": [], "pro": []},
        },
        "errors": mine_folder.last_errors,
    }


def _date_span(group_stats):
    """**入选**对局实际覆盖的日期区间（不是扫描范围）。"""
    days = [day for day in ((group_stats or {}).get("matched_dates") or []) if day]
    if not days:
        seen = (group_stats or {}).get("dates_seen") or {}
        if not seen:
            return None
        return {"min": min(seen), "max": max(seen), "games": sum(seen.values())}
    return {"min": min(days), "max": max(days), "games": len(days)}


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
        matchup="TvZ", seed=20261108, progress=None,
        since=None, until=None, start_workers_only=12, k_range=(2, 6)):
    """挖矿 + 聚类 + 对照，返回 summary dict。

    ``pro_limit`` 默认 30：职业录像文件夹现在有 24 局，得放够才不会抽样截断。
    ``start_workers_only`` 默认 12 = 只收 12 农民版本（8 农民版本的开局时间点整体前移，
    混在一起聚出来的是版本差，先整批排除）；``since`` / ``until`` 是按 replay 日期的
    可选区间（默认不设，报告里会写明实际用了哪段时间）。
    """
    mine = mine_folder(mine_dir, limit=limit, matchup=matchup, seed=seed,
                       progress=progress, ladder_only=True, min_duration=120.0,
                       require_local=True, exclude_ai=True,
                       since=since, until=until, start_workers_only=start_workers_only)
    mine_stats = dict(mine_folder.last_stats)
    mine_stats["start_workers_filter"] = start_workers_only
    mine_stats["since"] = since
    mine_stats["until"] = until
    mine_rejects = mine_stats.pop("rejected_expands", [])
    # 职业选手的录像全收（TvP/TvT 也留着），聚类和对照再按 matchup 过滤
    pro = mine_folder(pro_dir, limit=pro_limit, matchup=None, prefer_name="maru",
                      seed=seed, progress=progress, start_workers_only=start_workers_only)
    pro_stats = dict(mine_folder.last_stats)
    pro_stats["start_workers_filter"] = start_workers_only
    pro_rejects = pro_stats.pop("rejected_expands", [])
    return build_summary(mine, pro, "我", "Maru", matchup, stats={
        "mine": mine_stats,
        "pro": pro_stats,
        "expand_rejects": {"mine": mine_rejects, "pro": pro_rejects},
    }, k_range=k_range)


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
    parser.add_argument("--since", default=None,
                        help="只收该日期之后的局（YYYY-MM-DD，含端点）；默认不设")
    parser.add_argument("--until", default=None,
                        help="只收该日期之前的局（YYYY-MM-DD，含端点）；默认不设")
    parser.add_argument("--start-workers", type=int, default=12, dest="start_workers",
                        help="只收开局农民数 = N 的局（版本硬过滤，默认 12；传 0 关闭）")
    parser.add_argument("--k-min", type=int, default=2, help="聚类 k 搜索下界（默认 2）")
    parser.add_argument("--k-max", type=int, default=6, help="聚类 k 搜索上界（默认 6）")
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
                  progress=lambda done, name: sys.stderr.write("\r  parsed %d/%d games" % (done, args.limit)),
                  since=args.since, until=args.until,
                  start_workers_only=(args.start_workers or None),
                  k_range=(args.k_min, args.k_max))
    summary["replay_checks"] = checks
    summary["meta"]["elapsed_seconds"] = round(time.time() - started, 1)
    write_json(summary, args.json)

    from . import bo_report
    bo_report.write_report(summary, args.html)
    sys.stderr.write("\n")
    clustering = summary["meta"].get("clustering") or {}
    version = summary["meta"].get("version_filter") or {}
    sys.stdout.write("wrote %s (%d games) and %s in %.1fs\n" % (
        args.json, len(summary["games"]), args.html, time.time() - started))
    sys.stdout.write("clustering: axis=%s k=%s  (start_workers=%s, excluded by workers=%s by date=%s)\n" % (
        clustering.get("axis"), clustering.get("k"), version.get("start_workers"),
        version.get("excluded_by_workers"), version.get("excluded_by_date")))
    for cluster in summary["clusters"]["mine"]:
        sys.stdout.write("  mine cluster: %-30s %2d games  share=%3.0f%%  seq=%s\n" % (
            cluster["key"][:30], cluster["games"], cluster["share"] * 100,
            "-".join(cluster.get("typical_sequence") or [])[:48]))
    for cluster in summary["clusters"]["pro"]:
        sys.stdout.write("  pro  cluster: %-30s %2d games  share=%3.0f%%  seq=%s\n" % (
            cluster["key"][:30], cluster["games"], cluster["share"] * 100,
            "-".join(cluster.get("typical_sequence") or [])[:48]))
    return summary


if __name__ == "__main__":
    main()
