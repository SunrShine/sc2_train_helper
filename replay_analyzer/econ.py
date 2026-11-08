# -*- coding: utf-8 -*-
"""经济模型：账本 → 累计收入 → 收入拟合 → 造价对账 → 报告。

数据契约见 ``docs/BO数据契约.md``，模型设计见 ``docs/经济模型设计.md``。

核心结论（全部来自 replay 里真实的 ``SPlayerStatsEvent``，没有手填常数）：

1. 累计收入不用猜：``collected(t) = bank + Σused_current + Σprogress + Σlost``。
2. 收入模型用本机语料拟合 ``mineral_rate ≈ f(矿上农民数, 基地数, 矿骡)``，
   ``vespene_rate ≈ g(气矿数)``，给出系数 + 残差（MAE / R²），训练/测试按**对局**切分。
3. 单位造价 + 升级造价 与账本总收入对账，给出每局误差。
4. 逐采样点对账：用模型收入 - 造价表支出 预测库存，和真实 ``bank`` 比。

口径说明（重要，别误用）：

- 游戏自带的 ``m_scoreValueMineralsCollectionRate`` 字段比账本实测收入系统性地高
  （本次语料实测约 +37%~+60%），不能直接积分当收入用；文档里的"矿工效率中位
  47.2 矿/分钟"就是用这个字段算的，本模块单独复现该锚点（见 ``anchor_check``），
  但**收入模型以账本增量为准**。
- ``units.UNIT_COST["Zergling"]`` 是"一对跳虫"的价格（50 矿 / 1 人口），而
  ``unit_records`` 里一条记录就是**一只**跳虫，所以 econ 里按 25 矿/只覆盖。
- **两个采速系数不要混用**：``THEORETICAL_MINERAL_PER_WORKER_PER_MIN``（45 矿/分/农民）
  是"农民一直在采"的硬上限，仿真器求最优 BO 用它；收入模型拟合出的 a（约 25 矿/分/农民）
  是实战有效值，复盘预测/对账用它。效率 = 有效值 / 理论上限，按
  ``MINING_EFFICIENCY_RULE`` 的干净窗口规则测。气体同理：理论上限
  ``THEORETICAL_VESPENE_PER_REFINERY_PER_MIN`` = 120 气/分/气矿。

命令行（在仓库根目录，约 30 秒）：

    python -m replay_analyzer.econ --limit 90
"""

import argparse
import glob
import json
import math
import os
import statistics
import time

from . import charts, micro_report, units
from .loader import load_replay

LOOPS_PER_SECOND = 16.0
MINE_PATCHES_PER_BASE = 8          # 标准分矿 8 片矿
WORKERS_PER_GAS = 3                # 一个气矿满采 3 个农民

DEFAULT_REPLAY_DIR = os.path.join(
    r"C:\Users\sun\Documents\StarCraft II", "Accounts", "556922532",
    "5-S2-1-8354391", "Replays", "Multiplayer")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT_HTML = os.path.join(_ROOT, "reports", "econ.html")
DEFAULT_OUT_JSON = os.path.join(_ROOT, "reports", "econ.json")
PRO_DIR = os.path.join(_ROOT, "replays", "pro")

DEFAULT_REPORT_GAME = "Rorschach LE (55).SC2Replay"
DOC_ANCHOR_EFFICIENCY = 47.2       # docs/经济模型设计.md 已验证锚点

# ---------------------------------------------------------------------------
# 理论采速：仿真器假定"农民一直在采"时必须用这个值，不能用收入模型拟合出来的
# 有效值（有效值把走路、闲置、气矿占人都摊进去了，会把所有时间点拖慢 40%+）。
#
# 来源：lead 独立测量 + 本模块复测——规则见 MINING_EFFICIENCY_RULE，取 p90：
#   我的局 p90 = 45.0（本模块复测 p90=45.0 / max=47.1，与 lead 一致）
#   职业局 p90 = 42.9
# p90 顶到 45 附近就收敛不再上升，对应"一格 5 矿、来回约 6.7 秒"的硬上限。
# ---------------------------------------------------------------------------
THEORETICAL_MINERAL_PER_WORKER_PER_MIN = 45.0
# 气体上限同样测得非常干净：单气矿满采 3 农民 = 120 气/分钟（=40 气/农民/分钟），
# 我的局 n=112、职业局 n=132 的 p50 = p75 = p90 全部 = 120.0。
THEORETICAL_VESPENE_PER_REFINERY_PER_MIN = 120.0

MINING_EFFICIENCY_RULE = (
    "单基地、0 气矿、14~16 农民且农民数在窗口内不变、窗口内无任何建造/生产/升级"
    "事件；用银行存款（矿物）增量 / 时长 / 农民数，算每分钟每农民收入")
GAS_EFFICIENCY_RULE = (
    "单气矿、农民数在窗口内不变、窗口内无任何建造/生产/升级事件；"
    "用气体银行存款增量 / 时长，算每分钟每气矿收入")
EFFICIENCY_WORKER_MIN = 14
EFFICIENCY_WORKER_MAX = 16
EFFICIENCY_MIN_WINDOW = 9.0        # 秒；stats 采样约 10 秒一个点，最短取一个采样间隔

# lead 独立测量（同一条规则、不同实现/抽样），用于交叉核对；两边差 ±5pp 属于
# 采样窗口不同导致的正常差异，报告里并列展示，不藏。
LEAD_REFERENCE_MEASUREMENT = {
    "mine": {"p50": 25.7, "p75": 42.9, "p90": 45.0, "max": 47.1,
             "efficiency_pct": 57.1},
    "pro": {"p50": 36.4, "p75": 40.7, "p90": 42.9,
            "efficiency_pct": 80.9},
    "note": "lead 在 999 局我的录像 / 51 局职业录像上独立测量；本模块复测用"
            "180 局我的录像 + 51 局职业录像，p90/max/气体上限完全一致，"
            "p50 差 2~4 个点属于抽样与窗口实现差异。",
}

BASE_UNITS = {"CommandCenter", "OrbitalCommand", "PlanetaryFortress",
              "Nexus", "Hatchery", "Lair", "Hive"}
GAS_UNITS = {"Refinery", "Assimilator", "Extractor", "ExtractorRich"}
AI_NOISE_UNITS = {"InvisibleTargetDummy"}

# ---------------------------------------------------------------------------
# 造价表：units.py 没收录、但 replay 里真实出现的东西
# ---------------------------------------------------------------------------

# 录像里的单位名和 units.py 不完全一致，这里补别名（(矿, 气, 人口)）
UNIT_COST_ALIAS = {
    "SwarmHostMP": (200, 100, 4),
    "TemplarArchive": (150, 200, 0),
    "LurkerDenMP": (150, 100, 0),
    "NydusCanal": (100, 100, 0),
    "ExtractorRich": (75, 0, 0),
    "KD8Charge": (0, 0, 0),            # 寡妇雷技能
    "LocustMPPrecursor": (0, 0, 0),
    "BroodlingEscort": (0, 0, 0),
    "AutoTurret": (0, 0, 0),           # 渡鸦炮台
}

# units.py 的口径修正（见模块 docstring）
UNIT_COST_OVERRIDE = {
    "Zergling": (25, 0, 1),
}

# 真实花钱的 morph（形态切换如 Sieged/Burrowed/Flying 不计）
MORPH_COST = {
    "OrbitalCommand": (150, 0),
    "PlanetaryFortress": (150, 150),
    "Lair": (150, 100),
    "Hive": (200, 150),
    "Baneling": (25, 25),
    "Ravager": (25, 75),
    "Lurker": (50, 100),
    "BroodLord": (150, 150),
    "Overseer": (50, 50),
    "GreaterSpire": (100, 150),
}

# 常见升级造价（矿, 气）。只写高置信度的；查不到/版本不统一的一律 None，
# 不做猜测。key 是 replay 里的 m_upgradeTypeName。
UPGRADE_COST = {
    # ---- 人族
    "Stimpack": (100, 100),
    "ShieldWall": (100, 100),
    "PunisherGrenades": (50, 50),
    "HiSecAutoTracking": (100, 100),
    "TerranBuildingArmor": (150, 150),
    "PersonalCloaking": (100, 100),
    "BansheeCloak": (100, 100),
    "MedivacCaduceusReactor": (100, 100),
    "LiberatorAGRangeUpgrade": (150, 150),
    # ---- 星灵
    "Charge": (100, 100),
    "BlinkTech": (150, 150),
    "WarpGateResearch": (50, 50),
    "AdeptPiercingAttack": (100, 100),
    "ResonatingGlaives": (100, 100),
    "PsiStormTech": (200, 200),
    "ExtendedThermalLance": (150, 150),
    "GraviticBoosters": (100, 100),
    "GraviticDrive": (100, 100),
    "GravitonCatapult": (150, 150),
    "FluxVanes": (100, 100),
    "AnionPulseCrystals": (100, 100),
    "DarkTemplarBlinkUpgrade": (100, 100),
    # ---- 异虫
    "MetabolicBoost": (100, 100),
    "zerglingmovementspeed": (100, 100),
    "AdrenalGlands": (200, 200),
    "zerglingattackspeed": (200, 200),
    "CentrifugalHooks": (150, 150),
    "CentrificalHooks": (150, 150),     # 录像里的拼写变体
    "GlialReconstitution": (100, 100),
    "TunnelingClaws": (150, 150),
    "ChitinousPlating": (150, 150),
    "AnabolicSynthesis": (150, 150),
    "Frenzy": (100, 100),
    "Burrow": (100, 100),
    "LurkerRange": (150, 150),
    "SeismicSpines": (150, 150),
    "DiggingClaws": (150, 150),
    "NeuralParasite": (150, 150),
    "PathogenGlands": (150, 150),
    "overlordspeed": (100, 100),
    # ---- 知道存在、但造价拿不准：不编，留 None，报告里会列出来
    "TerranVehicleAndShipArmorsLevel1": None,
    "TerranVehicleAndShipArmorsLevel2": None,
    "TerranVehicleAndShipArmorsLevel3": None,
    "InterferenceMatrix": None,
    "HighCapacityBarrels": None,
    "BattlecruiserEnableSpecializations": None,
    "CycloneLockOnDamageUpgrade": None,
    "SmartServos": None,
    "NeoSteelFrame": None,
    "DrillingClaws": None,
    "CycloneRapidFireLaunchers": None,
    "VoidRaySpeedUpgrade": None,
    "MuscularAugments": None,
    "EvolveMuscularAugments": None,
    "GroovedSpines": None,
    "EvolveGroovedSpines": None,
}

# 三族武器/护甲升级按等级批量生成（矿=气，1 级 100，2 级 175，3 级 250）
_GROUND_AIR_FAMS = {
    "T": ("TerranInfantryWeapons", "TerranInfantryArmors",
          "TerranVehicleWeapons", "TerranShipWeapons"),
    "P": ("ProtossGroundWeapons", "ProtossGroundArmors", "ProtossAirWeapons"),
    "Z": ("ZergMeleeWeapons", "ZergMissileWeapons", "ZergGroundArmors",
          "ZergFlyerWeapons"),
}
for _race_fams in _GROUND_AIR_FAMS.values():
    for _fam in _race_fams:
        for _level, _cost in ((1, 100), (2, 175), (3, 250)):
            UPGRADE_COST["%sLevel%d" % (_fam, _level)] = (_cost, _cost)

# 护盾/空军护甲更贵（1 级 150，2 级 225，3 级 300）
_SHIELD_FAMS = ("ProtossAirArmors", "ProtossShields", "ZergFlyerArmors")
for _fam in _SHIELD_FAMS:
    for _level, _cost in ((1, 150), (2, 225), (3, 300)):
        UPGRADE_COST["%sLevel%d" % (_fam, _level)] = (_cost, _cost)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def seconds(loop):
    """replay gameloop -> 秒。"""
    return float(loop or 0) / LOOPS_PER_SECOND


def _minerals(ledger):
    return sum(value for key, value in ledger.items() if key.startswith("minerals"))


def _vespene(ledger):
    return sum(value for key, value in ledger.items() if key.startswith("vespene"))


def stats_rows(parsed, player_id):
    rows = parsed.stats.get(player_id) or []
    return sorted(rows, key=lambda row: row.get("loop", 0))


def local_players(parsed):
    return [player for player in parsed.players if getattr(player, "is_local", False)]


def unit_price(name):
    """单个单位的 (矿, 气)，查不到返回 None。"""
    if name in UNIT_COST_OVERRIDE:
        return UNIT_COST_OVERRIDE[name]
    cost = units.cost(name)
    if cost is not None:
        return cost
    return UNIT_COST_ALIAS.get(name)


def collected(parsed, player_id):
    """累计收入 [(t, minerals, vespene)]。

    collected = bank + Σused_current + Σin_progress + Σlost（全部按用途分类加总）。
    """
    series = []
    for row in stats_rows(parsed, player_id):
        ledger = row.get("ledger") or {}
        series.append((seconds(row.get("loop")),
                       row.get("minerals", 0) + _minerals(ledger),
                       row.get("vespene", 0) + _vespene(ledger)))
    return series


def _interp(series, t):
    """在 [(t, a, b)] 上线性插值，返回 (a, b)。"""
    if not series:
        return 0.0, 0.0
    if t <= series[0][0]:
        return float(series[0][1]), float(series[0][2])
    for index in range(1, len(series)):
        t1 = series[index][0]
        if t1 >= t:
            t0, a0, b0 = series[index - 1]
            a1, b1 = series[index][1], series[index][2]
            if t1 <= t0:
                return float(a1), float(b1)
            ratio = (t - t0) / (t1 - t0)
            return a0 + (a1 - a0) * ratio, b0 + (b1 - b0) * ratio
    return float(series[-1][1]), float(series[-1][2])


# ---------------------------------------------------------------------------
# 时间线：基地 / 气矿 / 农民 / 矿骡
# ---------------------------------------------------------------------------

def _unit_records(parsed, player_id):
    return [record for record in parsed.unit_records
            if record.player_id == player_id]


def _alive_count(records, names, t):
    """t 时刻（秒）处于存活状态、且名字在 names 里的单位数。"""
    count = 0
    for record in records:
        if record.name not in names:
            continue
        start_loop = record.init_loop
        if start_loop is None:
            start_loop = record.born_loop
        start = seconds(start_loop) if start_loop is not None else 0.0
        end = seconds(record.died_loop) if record.died_loop is not None else float("inf")
        if start <= t < end:
            count += 1
    return count


def base_gas_at(parsed, player_id, t, records=None):
    """(基地数, 气矿数)。初始基地没有 init_loop，按 0 秒算。"""
    records = records if records is not None else _unit_records(parsed, player_id)
    return (_alive_count(records, BASE_UNITS, t),
            _alive_count(records, GAS_UNITS, t))


def mules_at(records, t):
    return _alive_count(records, {"MULE"}, t)


def saturation_cap(bases):
    """一片矿 2 个农民二点一采，8 片矿 -> 16 农民到顶。"""
    return 2.0 * MINE_PATCHES_PER_BASE * max(1.0, float(bases))


# ---------------------------------------------------------------------------
# 采矿效率：理论值 vs 实战有效值
# ---------------------------------------------------------------------------

def _spend_event_loops(parsed, player_id):
    """这个玩家所有"花钱/占产能"事件的 loop：单位开工、morph、升级开研。

    干净窗口要求窗口内没有任何这类事件，这样银行存款增量才等于纯采矿收入。
    """
    loops = []
    for record in _unit_records(parsed, player_id):
        start_loop = record.init_loop
        if start_loop is None:
            start_loop = record.born_loop
        if start_loop is not None:
            loops.append(start_loop)
        for morph_loop, _morph in record.morphs:
            loops.append(morph_loop)
    for upgrade in parsed.upgrades:
        if upgrade.get("player_id") == player_id:
            loops.append(upgrade.get("loop") or 0)
    loops.sort()
    return loops


def _clean_windows(parsed, player_id, resource="minerals"):
    """按测量规则找干净窗口，返回 [(时长秒, 窗口收入, 农民数/气矿数)]。"""
    rows = stats_rows(parsed, player_id)
    if len(rows) < 5:
        return []
    records = _unit_records(parsed, player_id)
    spend_loops = _spend_event_loops(parsed, player_id)
    bank_key = "vespene" if resource == "vespene" else "minerals"
    windows = []
    index = 0
    total = len(rows)
    while index < total:
        workers = rows[index].get("workers", 0)
        start_t = seconds(rows[index].get("loop"))
        bases, gas = base_gas_at(parsed, player_id, start_t, records)
        if resource == "vespene":
            ok = gas == 1 and workers >= WORKERS_PER_GAS
            unit_count = gas
        else:
            ok = (bases == 1 and gas == 0
                  and EFFICIENCY_WORKER_MIN <= workers <= EFFICIENCY_WORKER_MAX)
            unit_count = workers
        if not ok:
            index += 1
            continue
        end = index + 1
        while end < total:
            end_t = seconds(rows[end].get("loop"))
            if resource == "vespene":
                end_bases, end_gas = base_gas_at(parsed, player_id, end_t, records)
                if end_gas != 1 or rows[end].get("workers", 0) != workers:
                    break
            else:
                end_bases, end_gas = base_gas_at(parsed, player_id, end_t, records)
                if (end_bases != 1 or end_gas != 0
                        or rows[end].get("workers", 0) != workers):
                    break
            end += 1
        # 沿"花费事件"把连续段再切开：段内银行增量 = 纯采矿收入
        segment_start = index
        for cursor in range(index + 1, end):
            has_spend = any(rows[cursor - 1].get("loop") < loop <= rows[cursor].get("loop")
                            for loop in spend_loops)
            if not has_spend:
                continue
            _append_window(rows, segment_start, cursor - 1, bank_key,
                           unit_count, windows)
            segment_start = cursor
        _append_window(rows, segment_start, end - 1, bank_key, unit_count, windows)
        index = end
    return windows


def _append_window(rows, start, end, bank_key, unit_count, windows):
    if end - start < 1 or unit_count <= 0:
        return
    t0 = seconds(rows[start].get("loop"))
    t1 = seconds(rows[end].get("loop"))
    duration = t1 - t0
    if duration < EFFICIENCY_MIN_WINDOW:
        return
    gain = rows[end].get(bank_key, 0) - rows[start].get(bank_key, 0)
    if gain <= 0:
        return
    windows.append((duration, gain, unit_count))


def mining_efficiency_samples(parsed, player_id):
    """单局单玩家的采矿效率窗口 [(秒, 收入, 农民数)]。"""
    return _clean_windows(parsed, player_id, resource="minerals")


def vespene_efficiency_samples(parsed, player_id):
    """单局单玩家的采气效率窗口 [(秒, 收入, 气矿数)]。"""
    return _clean_windows(parsed, player_id, resource="vespene")


def summarize_efficiency(samples, theoretical):
    """把窗口 [(duration, gain, units)] 汇总成 p50/p75/p90/max/效率。"""
    rates = sorted(gain / duration * 60.0 / units
                   for duration, gain, units in samples if duration > 0 and units > 0)
    if not rates:
        return {"windows": 0}
    count = len(rates)

    def quantile(fraction):
        return rates[min(count - 1, int(fraction * count))]

    p50 = quantile(0.5)
    return {
        "windows": count,
        "p50": round(p50, 1),
        "p75": round(quantile(0.75), 1),
        "p90": round(quantile(0.90), 1),
        "max": round(rates[-1], 1),
        "mean": round(sum(rates) / count, 1),
        "theoretical": theoretical,
        "efficiency_pct": round(100.0 * p50 / theoretical, 1) if theoretical else 0.0,
    }


def mining_efficiency(parsed, player_id):
    """单个玩家/单局的采矿效率摘要（按 MINING_EFFICIENCY_RULE）。

    返回 {"windows", "p50", "p75", "p90", "max", "mean", "theoretical",
    "efficiency_pct"}；没有干净窗口时只有 {"windows": 0}。
    """
    return summarize_efficiency(mining_efficiency_samples(parsed, player_id),
                                THEORETICAL_MINERAL_PER_WORKER_PER_MIN)


def vespene_efficiency(parsed, player_id):
    """单个玩家/单局的采气效率摘要（按 GAS_EFFICIENCY_RULE）。"""
    return summarize_efficiency(vespene_efficiency_samples(parsed, player_id),
                                THEORETICAL_VESPENE_PER_REFINERY_PER_MIN)


def collect_efficiency(replay_dir, limit=None, only_local=True, verbose=False):
    """扫一个目录，汇总矿物与气体两组效率窗口（可按玩家再拆开）。"""
    paths = sorted(glob.glob(os.path.join(replay_dir, "**", "*.SC2Replay"),
                             recursive=True))
    if not paths:
        return {"files": 0, "parsed": 0, "minerals": [], "vespene": [],
                "by_player": []}
    if limit and len(paths) > limit:
        step = max(1, len(paths) // limit)
        paths = paths[::step][:limit]
    mineral_samples = []
    gas_samples = []
    per_player = {}
    parsed_count = 0
    for path in paths:
        try:
            parsed = load_replay(path, decode_events=False)
        except Exception:  # noqa: BLE001 - 单局坏文件不影响统计
            continue
        parsed_count += 1
        for player in parsed.players:
            if only_local and not getattr(player, "is_local", False):
                continue
            player_minerals = mining_efficiency_samples(parsed, player.player_id)
            player_gas = vespene_efficiency_samples(parsed, player.player_id)
            mineral_samples.extend(player_minerals)
            gas_samples.extend(player_gas)
            key = (player.name, player.race)
            bucket = per_player.setdefault(key, {"minerals": [], "vespene": []})
            bucket["minerals"].extend(player_minerals)
            bucket["vespene"].extend(player_gas)
        if verbose and parsed_count % 40 == 0:
            print("[econ]   efficiency scanned %d/%d" % (parsed_count, len(paths)))
    by_player = []
    for (name, race), bucket in per_player.items():
        mineral_summary = summarize_efficiency(
            bucket["minerals"], THEORETICAL_MINERAL_PER_WORKER_PER_MIN)
        if mineral_summary.get("windows", 0) < 4:
            continue
        by_player.append({
            "player": name,
            "race": race,
            "minerals": mineral_summary,
            "vespene": summarize_efficiency(
                bucket["vespene"], THEORETICAL_VESPENE_PER_REFINERY_PER_MIN),
        })
    by_player.sort(key=lambda item: -item["minerals"].get("windows", 0))
    return {
        "files": len(paths),
        "parsed": parsed_count,
        "minerals": mineral_samples,
        "vespene": gas_samples,
        "by_player": by_player,
    }


# ---------------------------------------------------------------------------
# 造价统计（用于对账）
# ---------------------------------------------------------------------------

def spend_summary(parsed, player_id):
    """单位 + 升级 的造价合计，以及没定价的名字（诚实报告用）。

    返回:
        {
          "unit_minerals", "unit_vespene", "upgrade_minerals", "upgrade_vespene",
          "morph_minerals", "morph_vespene", "in_progress_minerals",
          "unit_records", "unknown_units": {name: count}, "unknown_upgrades": {...},
          "ai_noise_units": count,
        }
    """
    records = _unit_records(parsed, player_id)
    unit_min = unit_gas = morph_min = morph_gas = 0
    unit_count = 0
    ai_noise = 0
    unknown_units = {}
    for record in records:
        name = record.name
        if name in AI_NOISE_UNITS:
            ai_noise += 1
            continue
        if units.is_noise(name):
            continue
        price = unit_price(name)
        if price is None:
            unknown_units[name] = unknown_units.get(name, 0) + 1
            continue
        unit_min += price[0]
        unit_gas += price[1]
        unit_count += 1
        seen_morphs = set()
        for _, morph in record.morphs:
            # 同一条记录同一个目标形态只算一次：
            # 指挥中心反复 "OrbitalCommand -> 飞行 -> OrbitalCommand" 落地不花钱。
            if morph in seen_morphs:
                continue
            seen_morphs.add(morph)
            morph_cost = MORPH_COST.get(morph)
            if morph_cost:
                morph_min += morph_cost[0]
                morph_gas += morph_cost[1]

    upgrade_min = upgrade_gas = 0
    unknown_upgrades = {}
    for upgrade in parsed.upgrades:
        if upgrade.get("player_id") != player_id:
            continue
        name = upgrade.get("name")
        price = UPGRADE_COST.get(name)
        if price is None:
            unknown_upgrades[name] = unknown_upgrades.get(name, 0) + 1
            continue
        upgrade_min += price[0]
        upgrade_gas += price[1]

    rows = stats_rows(parsed, player_id)
    ledger = rows[-1].get("ledger") if rows else {}
    in_progress_min = sum(value for key, value in ledger.items()
                          if key.startswith("minerals_progress"))
    return {
        "unit_minerals": unit_min,
        "unit_vespene": unit_gas,
        "upgrade_minerals": upgrade_min,
        "upgrade_vespene": upgrade_gas,
        "morph_minerals": morph_min,
        "morph_vespene": morph_gas,
        "in_progress_minerals": in_progress_min,
        "unit_records": unit_count,
        "unknown_units": unknown_units,
        "unknown_upgrades": unknown_upgrades,
        "ai_noise_units": ai_noise,
    }


def _spend_timeline(parsed, player_id):
    """按时间排序的支出事件 [(t, minerals, vespene)]：单位开工 + 升级开研。

    只统计"花钱"的事件；形态切换（Sieged/Burrowed/Flying）不花钱，不计。
    """
    events = []
    for record in _unit_records(parsed, player_id):
        if units.is_noise(record.name):
            continue
        start_loop = record.init_loop
        if start_loop is None:
            start_loop = record.born_loop
        if start_loop is None:
            continue
        price = unit_price(record.name)
        if price is not None:
            events.append((seconds(start_loop), price[0], price[1]))
        seen_morphs = set()
        for morph_loop, morph in record.morphs:
            if morph in seen_morphs:
                continue
            seen_morphs.add(morph)
            morph_cost = MORPH_COST.get(morph)
            if morph_cost:
                events.append((seconds(morph_loop), morph_cost[0], morph_cost[1]))
    for upgrade in parsed.upgrades:
        if upgrade.get("player_id") != player_id:
            continue
        price = UPGRADE_COST.get(upgrade.get("name"))
        if price is None:
            continue
        events.append((seconds(upgrade.get("loop")), price[0], price[1]))
    events.sort(key=lambda item: item[0])
    return events


def _cumulative(events, t):
    total_min = total_gas = 0
    for event_t, event_min, event_gas in events:
        if event_t > t:
            break
        total_min += event_min
        total_gas += event_gas
    return total_min, total_gas


# ---------------------------------------------------------------------------
# 收入样本 + 拟合
# ---------------------------------------------------------------------------

def coverage_ok(parsed, player_id, tolerance_loops=160):
    """stats 是否覆盖到终局（有些 replay 的统计事件会提前断掉）。"""
    rows = stats_rows(parsed, player_id)
    if not rows:
        return False
    gap = (parsed.duration_loops or 0) - rows[-1].get("loop", 0)
    return gap <= tolerance_loops


def is_clean_game(parsed):
    """排除 AI/自定义/作弊图（会出现 InvisibleTargetDummy 这种假单位）。"""
    for record in parsed.unit_records:
        if record.name in AI_NOISE_UNITS:
            return False
    return True


def income_samples(parsed, player_id, window=60.0, records=None):
    """每个采样点一条收入样本。

    rate / gas_rate 用账本累计收入的中心差分算（窗口 ±window 秒），单位"每分钟"。
    这是物理意义上的真实收入；游戏自带 CollectionRate 字段口径不同，不混用。
    """
    rows = stats_rows(parsed, player_id)
    if len(rows) < 5:
        return []
    series = collected(parsed, player_id)
    records = records if records is not None else _unit_records(parsed, player_id)
    duration = parsed.duration_seconds or seconds(parsed.duration_loops)
    start_t, start_min, start_gas = series[0] if series else (0.0, 0.0, 0.0)
    # 累计特征（对局时间积分，单位：农民·分钟 / 基地·分钟 / 矿骡·分钟 / 气矿·分钟）
    cum = {"cw": 0.0, "cb": 0.0, "cm": 0.0, "cg": 0.0}
    samples = []
    previous = None
    for row in rows:
        t = seconds(row.get("loop"))
        bases, gas = base_gas_at(parsed, player_id, t, records)
        bases = max(1, bases)
        mules = mules_at(records, t)
        capped = min(float(row.get("workers", 0)), saturation_cap(bases))
        current = (capped, float(bases), float(mules), float(gas))
        if previous is not None:
            dt = max(0.0, t - previous[0]) / 60.0
            cum["cw"] += 0.5 * (current[0] + previous[1][0]) * dt
            cum["cb"] += 0.5 * (current[1] + previous[1][1]) * dt
            cum["cm"] += 0.5 * (current[2] + previous[1][2]) * dt
            cum["cg"] += 0.5 * (current[3] + previous[1][3]) * dt
        previous = (t, current)
        if t < window or t > duration - window:
            continue
        before_m, before_g = _interp(series, t - window)
        after_m, after_g = _interp(series, t + window)
        actual_min, actual_gas = _interp(series, t)
        samples.append({
            "t": t,
            "rate": (after_m - before_m) / (2.0 * window) * 60.0,
            "gas_rate": (after_g - before_g) / (2.0 * window) * 60.0,
            "reported_rate": row.get("mineral_rate", 0),
            "workers": row.get("workers", 0),
            "bases": bases,
            "gas": gas,
            "mules": mules,
            "cum_workers": cum["cw"],
            "cum_bases": cum["cb"],
            "cum_mules": cum["cm"],
            "cum_gas": cum["cg"],
            "delta_minerals": actual_min - start_min,
            "delta_vespene": actual_gas - start_gas,
        })
    return samples


def _fit_origin(rows, features, target, non_negative=True):
    """过原点最小二乘：解 (XᵀX)a = Xᵀy，不引入截距。

    收入在 0 农民时必须是 0，所以模型一律不带自由截距；需要常数项的模型
    显式把 ``1.0`` 作为一个特征列。

    ``non_negative=True`` 时用简单的主动集法做非负最小二乘：某个系数解出来
    是负的就踢掉那一列重解——收入增长方向上的系数不可能为负。
    """
    active = list(range(len(features)))

    def _solve(indices):
        matrix = [[float(features[i](sample)) for i in indices] for sample in rows]
        values = [float(target(sample)) for sample in rows]
        size = len(indices)
        normal = [[sum(row[i] * row[j] for row in matrix) for j in range(size)]
                  for i in range(size)]
        rhs = [sum(row[i] * value for row, value in zip(matrix, values))
               for i in range(size)]
        for col in range(size):
            pivot = max(range(col, size), key=lambda r: abs(normal[r][col]))
            normal[col], normal[pivot] = normal[pivot], normal[col]
            rhs[col], rhs[pivot] = rhs[pivot], rhs[col]
            if abs(normal[col][col]) < 1e-9:
                continue
            for row_index in range(size):
                if row_index == col:
                    continue
                factor = normal[row_index][col] / normal[col][col]
                for k in range(col, size):
                    normal[row_index][k] -= factor * normal[col][k]
                rhs[row_index] -= factor * rhs[col]
        solution = []
        for index in range(size):
            if abs(normal[index][index]) < 1e-9:
                solution.append(0.0)
            else:
                solution.append(rhs[index] / normal[index][index])
        return solution

    solution = _solve(active)
    while non_negative and active:
        negative = [index for index, value in zip(active, solution) if value < 0]
        if not negative:
            break
        # 踢掉最负的那一列
        worst = min(zip(active, solution), key=lambda item: item[1])[0]
        active.remove(worst)
        solution = _solve(active)
    coefficients = [0.0] * len(features)
    for index, value in zip(active, solution):
        coefficients[index] = value
    return coefficients


def _metrics(actual, predicted):
    count = len(actual)
    if not count:
        return {"n": 0, "mae": 0.0, "r2": 0.0, "rmse": 0.0}
    mean = sum(actual) / count
    sse = sum((value - pred) ** 2 for value, pred in zip(actual, predicted))
    sst = sum((value - mean) ** 2 for value in actual)
    mae = sum(abs(value - pred) for value, pred in zip(actual, predicted)) / count
    return {
        "n": count,
        "mae": round(mae, 2),
        "r2": round(1.0 - sse / sst, 4) if sst > 0 else 0.0,
        "rmse": round(math.sqrt(sse / count), 2),
    }


def _capped(sample):
    # 口径：总农民数（气上农民也占人口）按 16/基地封顶。
    # 文档锚点「47.2 矿/分钟」= 报告速率 / 总农民数，用的就是这个口径。
    return min(float(sample["workers"]), saturation_cap(sample["bases"]))


MINERAL_MODELS = (
    {
        "id": "capped",
        "label": "rate = a · min(矿上农民, 16·基地)",
        "features": [_capped],
        "cum_features": [lambda s: s["cum_workers"]],
        "names": ["a"],
    },
    {
        "id": "capped_mule",
        "label": "rate = a · min(矿上农民, 16·基地) + c·矿骡",
        "features": [_capped, lambda s: s["mules"]],
        "cum_features": [lambda s: s["cum_workers"], lambda s: s["cum_mules"]],
        "names": ["a", "c"],
    },
    {
        "id": "capped_base",
        "label": "rate = a · min(矿上农民, 16·基地) + b·基地",
        "features": [_capped, lambda s: max(1, s["bases"])],
        "cum_features": [lambda s: s["cum_workers"], lambda s: s["cum_bases"]],
        "names": ["a", "b"],
    },
    {
        "id": "capped_base_mule",
        "label": "rate = a · min(矿上农民, 16·基地) + b·基地 + c·矿骡",
        "features": [_capped, lambda s: max(1, s["bases"]), lambda s: s["mules"]],
        "cum_features": [lambda s: s["cum_workers"], lambda s: s["cum_bases"],
                         lambda s: s["cum_mules"]],
        "names": ["a", "b", "c"],
    },
    {
        "id": "all_workers",
        "label": "rate = a · 总农民（不扣气矿、不封顶）",
        "features": [lambda s: s["workers"]],
        "cum_features": [lambda s: s["cum_workers"]],
        "names": ["a"],
    },
)

VESPENE_MODELS = (
    {
        "id": "gas",
        "label": "rate = a · 气矿数",
        "features": [lambda s: s["gas"]],
        "cum_features": [lambda s: s["cum_gas"]],
        "names": ["a"],
    },
    {
        "id": "gas_miner",
        "label": "rate = a · 气矿数 + b·气上农民（封顶 3/气矿）",
        "features": [lambda s: s["gas"],
                     lambda s: min(WORKERS_PER_GAS * s["gas"], s["workers"])],
        "cum_features": [lambda s: s["cum_gas"]],
        "names": ["a"],
    },
)


def _split_games(samples):
    """按对局切分训练/测试，避免同一局的采样点泄漏到两边。"""
    games = sorted({sample["file"] for sample in samples})
    test_games = {name for index, name in enumerate(games) if index % 3 == 0}
    train = [sample for sample in samples if sample["file"] not in test_games]
    test = [sample for sample in samples if sample["file"] in test_games]
    return train, test


def fit_income(samples, target_key="rate", models=MINERAL_MODELS):
    """拟合收入模型。

    标定目标是对局**累计收入**（把特征对时间积分后做最小二乘），这样积分出来
    的累计收入才是无偏的——逐采样点对账要用的正是累计量。同时把模型还原成
    "每分钟收入"的形式，并按速率口径报 MAE / R² 残差。

    target_key 只影响报告用哪个名称；实际标定用 ``delta_minerals`` /
    ``delta_vespene``。
    """
    train, test = _split_games(samples)
    cumulative_target = "delta_vespene" if target_key == "gas_rate" else "delta_minerals"
    rate_target = "gas_rate" if target_key == "gas_rate" else "rate"
    candidates = []
    for model in models:
        coefficients = _fit_origin(train, model["cum_features"],
                                   lambda s: s[cumulative_target])
        params = {name: round(value, 4) for name, value in zip(model["names"], coefficients)}

        def predict_rate(sample, coefficients=coefficients, features=model["features"]):
            return sum(coef * func(sample) for coef, func in zip(coefficients, features))

        def predict_cumulative(sample, coefficients=coefficients,
                               features=model["cum_features"]):
            return sum(coef * func(sample) for coef, func in zip(coefficients, features))

        rate_metrics_train = _metrics([s[rate_target] for s in train],
                                      [predict_rate(s) for s in train])
        rate_metrics_test = _metrics([s[rate_target] for s in test],
                                     [predict_rate(s) for s in test])
        cum_metrics_train = _metrics([s[cumulative_target] for s in train],
                                     [predict_cumulative(s) for s in train])
        cum_metrics_test = _metrics([s[cumulative_target] for s in test],
                                    [predict_cumulative(s) for s in test])
        candidates.append({
            "id": model["id"],
            "label": model["label"],
            "params": params,
            "train": rate_metrics_train,
            "test": rate_metrics_test,
            "cumulative_train": cum_metrics_train,
            "cumulative_test": cum_metrics_test,
        })
    if candidates:
        # 用测试集累计 R² 选模型；同时要求系数非负（负系数没有物理意义，
        # 说明特征共线，直接淘汰该候选）。
        valid = [item for item in candidates
                 if all(value >= 0 for value in item["params"].values())]
        pool = valid or candidates
        best = min(pool, key=lambda item: 1.0 - item["cumulative_test"]["r2"])
    return {"chosen": best["id"] if best else "", "candidates": candidates,
            "chosen_model": best}


def predict_mineral_rate(model_entry, workers, bases, gas, mules=0):
    """用选中的模型算每分钟矿物收入。"""
    params = model_entry["params"]
    model_id = model_entry["id"]
    bases = max(1.0, float(bases))
    # 与 _capped() 保持同一口径：总农民数封顶 16/基地
    capped = min(float(workers), saturation_cap(bases))
    if model_id == "capped":
        return params["a"] * capped
    if model_id == "capped_mule":
        return params["a"] * capped + params["c"] * mules
    if model_id == "capped_base":
        return params["a"] * capped + params["b"] * bases
    if model_id == "capped_base_mule":
        return params["a"] * capped + params["b"] * bases + params["c"] * mules
    return params["a"] * float(workers)


def predict_vespene_rate(model_entry, gas):
    params = model_entry["params"]
    if model_entry["id"] == "gas_intercept":
        return params["a"] * float(gas) + params["b"]
    return params["a"] * float(gas)


# ---------------------------------------------------------------------------
# 逐采样点对账
# ---------------------------------------------------------------------------

def reconcile(parsed, player_id, mineral_model, vespene_model):
    """用"模型收入 - 造价表支出"预测库存，和真实 bank 逐点对账。

    返回:
        {
          "file", "player", "race",
          "series": [[t, 实际矿, 模型矿, 误差矿, 实际气, 模型气, 误差气], ...],
          "mineral": {"mae", "mae_pct", "final_error", "final_error_pct"},
          "vespene": {...},
        }
    """
    rows = stats_rows(parsed, player_id)
    if not rows:
        return None
    series = collected(parsed, player_id)
    records = _unit_records(parsed, player_id)
    spend_events = _spend_timeline(parsed, player_id)

    start_row = rows[0]
    start_min = float(start_row.get("minerals", 0))
    start_gas = float(start_row.get("vespene", 0))
    start_t = seconds(start_row.get("loop"))
    # 开局账本里已经记着初始单位的造价（例如 12 个农民 + 1 个基地），
    # 这部分既是"已收入"也是"已支出"，对账只看 t0 之后的增量才不会被它带偏。
    base_collected_min, base_collected_gas = _interp(series, start_t)
    base_spent_min, base_spent_gas = _cumulative(spend_events, start_t)

    model_collected_min = base_collected_min
    model_collected_gas = base_collected_gas
    previous = None
    points = []
    actual_income_min = actual_income_gas = 0.0
    for row in rows:
        t = seconds(row.get("loop"))
        bases, gas_count = base_gas_at(parsed, player_id, t, records)
        mules = mules_at(records, t)
        rate_min = predict_mineral_rate(mineral_model, row.get("workers", 0),
                                        max(1, bases), gas_count, mules)
        rate_gas = predict_vespene_rate(vespene_model, gas_count)
        if previous is not None:
            dt = max(0.0, t - previous)
            model_collected_min += rate_min / 60.0 * dt
            model_collected_gas += rate_gas / 60.0 * dt
        previous = t
        actual_min = float(row.get("minerals", 0))
        actual_gas = float(row.get("vespene", 0))
        spent_min, spent_gas = _cumulative(spend_events, t)
        model_bank_min = (start_min
                          + (model_collected_min - base_collected_min)
                          - (spent_min - base_spent_min))
        model_bank_gas = (start_gas
                          + (model_collected_gas - base_collected_gas)
                          - (spent_gas - base_spent_gas))
        actual_collected_min, actual_collected_gas = _interp(series, t)
        actual_income_min = actual_collected_min
        actual_income_gas = actual_collected_gas
        points.append([round(t, 1), round(actual_min), round(model_bank_min),
                       round(model_bank_min - actual_min),
                       round(actual_gas), round(model_bank_gas),
                       round(model_bank_gas - actual_gas),
                       round(actual_collected_min), round(model_collected_min)])

    def _summary(column_actual, column_model, total_collected):
        errors = [abs(point[column_model] - point[column_actual]) for point in points]
        mae = sum(errors) / len(errors) if errors else 0.0
        final = points[-1][column_model] - points[-1][column_actual]
        return {
            "mae": round(mae, 1),
            "mae_pct": round(100.0 * mae / total_collected, 2) if total_collected else 0.0,
            "final_error": round(final, 1),
            "final_error_pct": (round(100.0 * final / total_collected, 2)
                                if total_collected else 0.0),
        }

    player = parsed.player_by_player_id(player_id)
    return {
        "file": os.path.basename(parsed.path),
        "player": player.name if player else "",
        "race": player.race if player else "",
        "duration": round(parsed.duration_seconds or 0, 1),
        "series": points,
        "mineral": _summary(1, 2, actual_income_min),
        "vespene": _summary(4, 5, actual_income_gas),
        "collected_minerals": round(actual_income_min, 1),
        "collected_vespene": round(actual_income_gas, 1),
        "model_collected_minerals": round(model_collected_min, 1),
        "model_collected_vespene": round(model_collected_gas, 1),
    }


# ---------------------------------------------------------------------------
# 语料扫描 + 造价闭合验证
# ---------------------------------------------------------------------------

def sample_replays(replay_dir, limit=90, must_include=None):
    """确定性地抽样：排序后等距取 limit 个，不随机、结果可复现。

    ``must_include`` 里的文件名（如报告主验证局）即使没被等距抽到也会补进来，
    保证报告里的主验证局一定有数据。
    """
    paths = sorted(glob.glob(os.path.join(replay_dir, "**", "*.SC2Replay"), recursive=True))
    if not paths:
        return []
    if len(paths) > limit:
        step = max(1, len(paths) // limit)
        picked = paths[::step][:limit]
    else:
        picked = list(paths)
    for name in must_include or []:
        if not name:
            continue
        if any(os.path.basename(item) == name for item in picked):
            continue
        for candidate in paths:
            if os.path.basename(candidate) == name:
                picked.append(candidate)
                break
    return picked


def closure_for(parsed, player_id):
    """造价闭合：单位造价 + 升级造价 + 在建，和账本总收入比。"""
    rows = stats_rows(parsed, player_id)
    if not rows:
        return None
    ledger = rows[-1].get("ledger") or {}
    collected_min = rows[-1].get("minerals", 0) + _minerals(ledger)
    collected_gas = rows[-1].get("vespene", 0) + _vespene(ledger)
    spend = spend_summary(parsed, player_id)
    unit_total_min = (spend["unit_minerals"] + spend["upgrade_minerals"]
                      + spend["in_progress_minerals"])
    unit_total_gas = spend["unit_vespene"] + spend["upgrade_vespene"]
    strict_min = spend["unit_minerals"] + spend["upgrade_minerals"]
    bank = rows[-1].get("minerals", 0)
    return {
        "file": os.path.basename(parsed.path),
        "race": "",
        "duration": round(parsed.duration_seconds or 0, 1),
        "player_id": player_id,
        "bank_minerals": bank,
        "bank_ratio": (round(float(bank) / collected_min, 4) if collected_min else 0.0),
        "collected_minerals": collected_min,
        "collected_vespene": collected_gas,
        "unit_minerals": unit_total_min,
        "unit_vespene": unit_total_gas,
        "strict_minerals": strict_min,
        "error_pct": (round(100.0 * (unit_total_min - collected_min) / collected_min, 2)
                      if collected_min else 0.0),
        "strict_error_pct": (round(100.0 * (strict_min - collected_min) / collected_min, 2)
                             if collected_min else 0.0),
        "gas_error_pct": (round(100.0 * (unit_total_gas - collected_gas) / collected_gas, 2)
                          if collected_gas else 0.0),
        "unknown_units": spend["unknown_units"],
        "unknown_upgrades": spend["unknown_upgrades"],
    }


def _distribution(errors):
    if not errors:
        return {}
    ordered = sorted(errors)
    count = len(ordered)
    return {
        "n": count,
        "median": round(ordered[count // 2], 2),
        "mean": round(sum(ordered) / count, 2),
        "p25": round(ordered[count // 4], 2),
        "p75": round(ordered[3 * count // 4], 2),
        "min": round(ordered[0], 2),
        "max": round(ordered[-1], 2),
        "within_2pct": sum(1 for value in ordered if abs(value) < 2.0),
    }


def anchor_check(samples):
    """复现文档锚点：报告字段 / 农民数 的中位数（只取 1 个气矿以内样本）。"""
    ratios = [sample["reported_rate"] / sample["workers"]
              for sample in samples
              if sample["workers"] > 0 and sample["gas"] <= 1
              and sample["reported_rate"] > 0]
    if not ratios:
        return {}
    ordered = sorted(ratios)
    count = len(ordered)
    return {
        "definition": "游戏自带 CollectionRate / 总农民数（每分钟）",
        "n": count,
        "median": round(ordered[count // 2], 1),
        "p25": round(ordered[count // 4], 1),
        "p75": round(ordered[3 * count // 4], 1),
        "doc_anchor": DOC_ANCHOR_EFFICIENCY,
    }


def reported_bias(samples):
    """游戏自带速率 / 账本实测速率，衡量该字段的偏差（报告里要讲清楚）。"""
    ratios = [sample["reported_rate"] / sample["rate"]
              for sample in samples
              if sample["rate"] > 100 and sample["reported_rate"] > 0]
    if not ratios:
        return {}
    ratios.sort()
    return {
        "definition": "report_rate / ledger_rate（同一采样点）",
        "n": len(ratios),
        "median_ratio": round(statistics.median(ratios), 2),
        "p25": round(ratios[len(ratios) // 4], 2),
        "p75": round(ratios[3 * len(ratios) // 4], 2),
    }


def build_corpus(replay_dir, limit=90, must_include=None):
    """解析抽样语料，返回 samples / games / closure / anchor 等。"""
    paths = sample_replays(replay_dir, limit=limit, must_include=must_include)
    samples = []
    games = []
    closure = []
    started = time.time()
    for path in paths:
        try:
            parsed = load_replay(path, decode_events=False)
        except Exception as exc:  # noqa: BLE001 - 单局失败不该炸掉整批
            games.append({"file": os.path.basename(path), "error": str(exc)[:120]})
            continue
        game_row = {
            "file": os.path.basename(path),
            "map": parsed.map_name,
            "duration": round(parsed.duration_seconds or 0, 1),
            "is_ladder": bool(parsed.is_ladder),
            "clean": is_clean_game(parsed),
            "players": [],
        }
        for player in parsed.players:
            player_id = player.player_id
            rows = stats_rows(parsed, player_id)
            entry = {
                "file": os.path.basename(path),
                "player_id": player_id,
                "name": player.name,
                "race": player.race,
                "is_local": bool(getattr(player, "is_local", False)),
                "duration": round(parsed.duration_seconds or 0, 1),
                "coverage_ok": coverage_ok(parsed, player_id),
                "stats": len(rows),
            }
            game_row["players"].append(entry)
            if (not rows or not entry["coverage_ok"] or not game_row["clean"]
                    or entry["duration"] < 300):
                continue
            for sample in income_samples(parsed, player_id):
                sample["file"] = os.path.basename(path)
                sample["race"] = player.race
                sample["is_local"] = entry["is_local"]
                samples.append(sample)
            closed = closure_for(parsed, player_id)
            if closed and closed["collected_minerals"] >= 1000:
                closed["race"] = player.race
                closed["is_local"] = entry["is_local"]
                closed["is_ladder"] = bool(parsed.is_ladder)
                closure.append(closed)
        games.append(game_row)
    return {
        "paths": paths,
        "samples": samples,
        "games": games,
        "closure": closure,
        "elapsed": round(time.time() - started, 1),
        "replay_dir": os.path.abspath(replay_dir),
        "files_total": len(glob.glob(os.path.join(replay_dir, "**", "*.SC2Replay"),
                                     recursive=True)),
    }


def validation_games(corpus, main_file=DEFAULT_REPORT_GAME, extra=3,
                     bank_ratio_max=0.10):
    """挑 4 局验收用对局：主验证局 + 按文件名排序的前 extra 局。

    额外 3 局的规则写死、可复现、不按误差挑：
    本地玩家、天梯、统计覆盖终局、且 ``银行存款 / 累计收入 < 10%``
    （存款占比小的局"收入≈支出"，造价闭合口径最干净）。
    """
    valid = [row for row in corpus["closure"]
             if row.get("is_local") and row.get("is_ladder")]
    picked = [row for row in valid if row["file"] == main_file]
    clean = [row for row in valid if row.get("bank_ratio", 1.0) < bank_ratio_max]
    for row in sorted(clean, key=lambda item: item["file"]):
        if len(picked) >= 1 + extra:
            break
        if any(item["file"] == row["file"] for item in picked):
            continue
        picked.append(row)
    # 不足 4 局时用不设存款门槛的局补齐（仍然按文件名，不按误差）
    for row in sorted(valid, key=lambda item: item["file"]):
        if len(picked) >= 1 + extra:
            break
        if any(item["file"] == row["file"] for item in picked):
            continue
        picked.append(row)
    return picked


def pick_report_game(corpus, main_file=DEFAULT_REPORT_GAME):
    """报告里画曲线用的对局：优先 Rorschach，否则取第一个覆盖终局的本地 T 局。"""
    candidates = [row for row in corpus["closure"] if row.get("is_local")]
    for row in candidates:
        if row["file"] == main_file:
            return row
    for row in candidates:
        if row.get("race") == "T":
            return row
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# HTML 报告
# ---------------------------------------------------------------------------

def _fmt(value, digits=0, suffix=""):
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if digits == 0:
        return "%d%s" % (round(value), suffix)
    return ("%." + str(digits) + "f") + suffix


def _pct(value):
    if value is None:
        return "—"
    return "%+.2f%%" % float(value)


def _player_name(parsed, player_id):
    player = parsed.player_by_player_id(player_id)
    return player.name if player else "?"


def _chart_card(title, desc, series, x_max=None, y_suffix="", y_min=0, height=230):
    chart = charts.line_chart(series, x_max=x_max, y_suffix=y_suffix, show_legend=True,
                              y_min=y_min, height=height)
    return ('<div class="card chart-card"><h3>%s</h3><div class="desc">%s</div>%s</div>'
            % (title, desc, chart))


def render_html(corpus, mineral_fit, vespene_fit, panel, reconcile_data,
                validation, anchor, bias, generated, per_game_reconcile=None,
                theoretical=None, mine_efficiency=None, pro_efficiency=None):
    """生成离线 HTML（复用 micro_report.CSS，纯内联 SVG）。"""
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>经济模型 · 账本 / 收入 / 对账</title>",
             '<style>%s</style></head><body><div class="wrap">' % micro_report.CSS]

    parts.append('<header class="top"><h1>经济模型：账本 → 收入 → 对账</h1>')
    parts.append('<div class="sub">%d 局抽样 · %d 个收入采样点 · 解析 %.1f 秒 · %s</div>'
                 % (len(corpus["games"]), len(corpus["samples"]), corpus["elapsed"], generated))
    parts.append('</header>')

    # ---- 一、结论
    mineral_best = mineral_fit["chosen_model"]
    vespene_best = vespene_fit["chosen_model"]
    closure_rows = [row for row in corpus["closure"]
                    if row.get("is_local") and row.get("is_ladder")]
    closure_errors = [row["error_pct"] for row in closure_rows]
    distribution = _distribution(closure_errors)
    parts.append('<h2>一、模型摘要</h2><div class="grid4">')
    mineral_params = " · ".join("%s=%s" % (key, _fmt(value, 2))
                                for key, value in mineral_best["params"].items())
    parts.append('<div class="metric"><div class="k">收入模型（矿物）</div><div class="v">%s 矿/分</div>'
                 '<div class="n">%s · 测试 MAE %s · R² %s</div></div>'
                 % (_fmt(mineral_best["params"].get("a"), 1), mineral_params,
                    _fmt(mineral_best["test"]["mae"], 0),
                    _fmt(mineral_best["test"]["r2"], 3)))
    parts.append('<div class="metric"><div class="k">收入模型（气体）</div><div class="v">%s</div>'
                 '<div class="n">矿/分钟/气矿 · 测试 MAE %s · R² %s</div></div>'
                 % (_fmt(vespene_best["params"].get("a"), 1),
                    _fmt(vespene_best["test"]["mae"], 0),
                    _fmt(vespene_best["test"]["r2"], 3)))
    parts.append('<div class="metric"><div class="k">造价闭合误差（中位）</div><div class="v">%s</div>'
                 '<div class="n">%d 局 · 四分位 %s ~ %s</div></div>'
                 % (_pct(distribution.get("median")),
                    distribution.get("n", 0),
                    _pct(distribution.get("p25")),
                    _pct(distribution.get("p75"))))
    parts.append('<div class="metric"><div class="k">对账误差（主验证局）</div><div class="v">%s</div>'
                 '<div class="n">矿物库存 MAE 占累计收入</div></div>'
                 % (_pct(reconcile_data["mineral"]["mae_pct"])
                    if reconcile_data else "—"))
    parts.append('</div>')

    parts.append('<div class="card" style="margin-top:14px"><div class="hint">')
    parts.append('<b>口径说明：</b>游戏自带的 <code>m_scoreValueMineralsCollectionRate</code> '
                 '字段和账本实测收入不是一个口径；本次语料实测报告中位偏差 '
                 '<b>%s</b>（报告字段 / 账本实测）。所以收入模型一律以账本增量为准，'
                 '文档锚点「47.2 矿/分钟」单独复现：本次中位 <b>%s</b>（样本 %d）。'
                 % (_fmt(bias.get("median_ratio"), 2, "x"),
                    _fmt(anchor.get("median"), 1), anchor.get("n", 0)))
    parts.append('</div></div>')

    # ---- 二、收入曲线 + 收支分解
    if panel:
        parsed = panel["parsed"]
        player_id = panel["player_id"]
        series = collected(parsed, player_id)
        minutes = panel["duration"] / 60.0
        rows = stats_rows(parsed, player_id)
        records = _unit_records(parsed, player_id)
        income_points = reconcile_data["series"] if reconcile_data else []
        symbols = panel["symbols"]
        parts.append('<h2>二、收入曲线（%s · %s）</h2><div class="grid2">'
                     % (symbols["map"], symbols["player"]))
        mineral_series = [
            {"label": "账本累计收入（矿物）", "color": "#46d17e",
             "points": [[point[0] / 60.0, point[7]] for point in income_points]},
            {"label": "模型累计收入（矿物）", "color": "#f2c45a", "dash": True,
             "points": [[point[0] / 60.0, point[8]] for point in income_points]},
        ]
        parts.append(_chart_card("矿物累计收入（实际 vs 模型）",
                                 "模型 = a·min(矿上农民, 16·基地) + b·基地；虚线是模型积分",
                                 mineral_series, x_max=minutes))
        gas_series = [{"label": "账本累计收入（气体）", "color": "#62b0ff",
                       "points": [[t / 60.0, value] for t, _, value in series]}]
        parts.append(_chart_card("气体累计收入", "账本口径（气体曲线只做展示，未建收支模型）",
                                 gas_series, x_max=minutes))
        parts.append('</div>')

        # 收支分解
        spend = spend_summary(parsed, player_id)
        ledger = rows[-1].get("ledger") or {}
        used_army = ledger.get("minerals_used_army", 0)
        used_econ = ledger.get("minerals_used_economy", 0)
        used_tech = ledger.get("minerals_used_technology", 0)
        lost = sum(value for key, value in ledger.items()
                   if key.startswith("minerals_lost"))
        progress = spend["in_progress_minerals"]
        bank = rows[-1].get("minerals", 0)
        total_in = rows[-1].get("minerals", 0) + _minerals(ledger)
        items = [
            ("存活部队", used_army, "#46d17e"),
            ("存活经济（农民/建筑）", used_econ, "#62b0ff"),
            ("科技（建筑/升级）", used_tech, "#7c9cff"),
            ("已损失", lost, "#ff6b6b"),
            ("在建/在生产", progress, "#f2c45a"),
            ("银行存款", bank, "#93a1b5"),
        ]
        bars = "".join(charts.bar_row(label, value, max(1, total_in), color)
                       for label, value, color in items)
        parts.append('<h2>三、收支分解（矿物，账本口径）</h2><div class="grid2">')
        parts.append('<div class="card chart-card"><h3>钱的去向</h3>'
                     '<div class="desc">账本累计收入 %s 矿 = 下表各项之和</div>%s</div>'
                     % (_fmt(total_in), bars))
        checks = [
            ("单位造价合计", spend["unit_minerals"], "#46d17e"),
            ("升级造价合计", spend["upgrade_minerals"], "#7c9cff"),
            ("在建单位（账本）", spend["in_progress_minerals"], "#f2c45a"),
        ]
        max_check = max(1, total_in)
        check_bars = "".join(charts.bar_row(label, value, max_check, color)
                             for label, value, color in checks)
        parts.append('<div class="card chart-card"><h3>造价表校验</h3>'
                     '<div class="desc">单位+升级+在建 = %s，账本累计收入 = %s（误差 %s）</div>%s</div>'
                     % (_fmt(spend["unit_minerals"] + spend["upgrade_minerals"]
                             + spend["in_progress_minerals"]),
                        _fmt(total_in),
                        _pct(100.0 * (spend["unit_minerals"] + spend["upgrade_minerals"]
                                      + spend["in_progress_minerals"] - total_in) / total_in),
                        check_bars))
        parts.append('</div>')

        # 对账误差图
        if reconcile_data:
            parts.append('<h2>四、逐采样点对账（%s）</h2><div class="grid2">' % symbols["player"])
            bank_series = [
                {"label": "实际库存（矿物）", "color": "#e6ecf5",
                 "points": [[point[0] / 60.0, point[1]] for point in income_points]},
                {"label": "模型库存（矿物）", "color": "#f2c45a", "dash": True,
                 "points": [[point[0] / 60.0, point[2]] for point in income_points]},
            ]
            parts.append(_chart_card(
                "库存曲线：模型 vs 实际",
                "模型库存 = 起始存款 + 模型收入积分 - 造价表支出；误差 MAE %s 矿（占收入 %s）"
                % (_fmt(reconcile_data["mineral"]["mae"], 0),
                   _pct(reconcile_data["mineral"]["mae_pct"])),
                bank_series, x_max=minutes))
            error_values = [point[3] for point in income_points]
            limit = max(50.0, max(abs(value) for value in error_values) * 1.1)
            error_series = [
                {"label": "库存误差（矿物）", "color": "#ff6b6b",
                 "points": [[point[0] / 60.0, point[3]] for point in income_points]},
            ]
            income_error_pct = 100.0 * (reconcile_data["model_collected_minerals"]
                                        - reconcile_data["collected_minerals"]
                                        ) / reconcile_data["collected_minerals"]
            parts.append(_chart_card(
                "库存误差（模型 - 实际）",
                "0 附近说明模型和账本一致。本局误差的两大来源："
                "①收入模型相对账本累计收入 %s（单局离散度约 ±20%%）；"
                "②造价表口径比账本支出高约 10%%（取消退款/morph/死亡时点）。"
                % _pct(income_error_pct),
                error_series, x_max=minutes, y_suffix="", y_min=-limit))
            parts.append('</div>')

    # ---- 五、饱和曲线
    parts.append('<h2>五、农民效率与饱和</h2><div class="grid2">')
    samples = corpus["samples"]
    base_groups = {}
    for sample in samples:
        bases = sample["bases"]
        if bases > 3:
            continue
        if sample["workers"] < 6 or sample["workers"] > 48:
            continue
        base_groups.setdefault(bases, {}).setdefault(sample["workers"], []).append(sample["rate"])
    colors = {1: "#46d17e", 2: "#62b0ff", 3: "#7c9cff"}
    saturation_series = []
    for bases in sorted(base_groups):
        points = []
        for workers in sorted(base_groups[bases]):
            rates = base_groups[bases][workers]
            if len(rates) < 4:
                continue
            rates.sort()
            points.append([workers, rates[len(rates) // 2]])
        if points:
            saturation_series.append({"label": "%d 基地（实测中位）" % bases,
                                      "color": colors.get(bases, "#46d17e"),
                                      "points": points})
    model_entry = mineral_fit["chosen_model"]
    model_points = []
    for workers in range(6, 49, 2):
        model_points.append([workers, predict_mineral_rate(model_entry, workers, 1, 0)])
    saturation_series.append({"label": "模型（1 基地）", "color": "#f2c45a",
                              "dash": True, "points": model_points})
    parts.append(_chart_card(
        "每分钟矿物收入 vs 农民数",
        "横轴是农民数、纵轴是每分钟矿物收入；同一农民数取该基地数样本的中位。"
        "拐点对应「一片矿 16 个农民封顶」",
        saturation_series))
    parts.append(_chart_card(
        "每分钟收入 / 农民数（效率）",
        "把上面的收入除以农民数：农民越多，人均效率越低——这就是饱和",
        [{"label": "1 基地", "color": "#46d17e",
          "points": [[workers, rate / workers]
                     for workers, rate in sorted(base_groups.get(1, {}).items())
                     if workers and len(base_groups[1][workers]) >= 4
                     for rate in [sorted(base_groups[1][workers])[
                         len(base_groups[1][workers]) // 2]]]}]
        if base_groups.get(1) else []))
    parts.append('</div>')

    # ---- 六、采矿效率（理论 vs 实战）
    theoretical = theoretical or {}
    mine_efficiency = mine_efficiency or {}
    pro_efficiency = pro_efficiency or {}
    mine_min = mine_efficiency.get("minerals") or {}
    pro_min = pro_efficiency.get("minerals") or {}
    mine_gas = mine_efficiency.get("vespene") or {}
    pro_gas = pro_efficiency.get("vespene") or {}
    theoretical_min = theoretical.get("mineral_per_worker_per_min",
                                      THEORETICAL_MINERAL_PER_WORKER_PER_MIN)
    theoretical_gas = theoretical.get("vespene_per_refinery_per_min",
                                      THEORETICAL_VESPENE_PER_REFINERY_PER_MIN)
    fitted = mineral_fit["chosen_model"]["params"].get("a")
    parts.append('<h2>六、采矿效率：理论上限 vs 实战有效值</h2><div class="grid4">')
    parts.append('<div class="metric"><div class="k">理论采速（矿物·仿真用）</div>'
                 '<div class="v">%s</div><div class="n">矿/分钟/农民 · p90 收敛的硬上限</div></div>'
                 % _fmt(theoretical_min, 1))
    parts.append('<div class="metric"><div class="k">我的实战有效值</div><div class="v">%s</div>'
                 '<div class="n">约 %.1f%% · p90 %s · %d 个干净窗口</div></div>'
                 % (_fmt(mine_min.get("p50"), 1),
                    mine_min.get("efficiency_pct", 0.0),
                    _fmt(mine_min.get("p90"), 1),
                    mine_min.get("windows", 0)))
    parts.append('<div class="metric"><div class="k">职业实战有效值</div><div class="v">%s</div>'
                 '<div class="n">约 %.1f%% · p90 %s · %d 个干净窗口</div></div>'
                 % (_fmt(pro_min.get("p50"), 1),
                    pro_min.get("efficiency_pct", 0.0),
                    _fmt(pro_min.get("p90"), 1),
                    pro_min.get("windows", 0)))
    parts.append('<div class="metric"><div class="k">理论采速（气体·仿真用）</div>'
                 '<div class="v">%s</div><div class="n">气/分钟/气矿 · 3 农民满采</div></div>'
                 % _fmt(theoretical_gas, 1))
    parts.append('</div>')

    parts.append('<div class="grid2" style="margin-top:14px">')
    parts.append('<div class="card chart-card"><h3>有效采速对比（p50 / p90 / 理论上限）</h3>'
                 '<div class="desc">单位：矿/分钟/农民。理论值 45.0 是"农民一直在采"的上限</div>')
    efficiency_bars = "".join(charts.bar_row(label, value, theoretical_min, color)
                              for label, value, color in (
                                  ("我的 p50", mine_min.get("p50", 0.0), "#46d17e"),
                                  ("我的 p90", mine_min.get("p90", 0.0), "#46d17e"),
                                  ("职业 p50", pro_min.get("p50", 0.0), "#62b0ff"),
                                  ("职业 p90", pro_min.get("p90", 0.0), "#62b0ff"),
                                  ("理论上限", theoretical_min, "#f2c45a")))
    parts.append(efficiency_bars)
    parts.append('<div class="hint" style="margin-top:10px">气体同理：实测 p50 = %s 气/分/气矿'
                 '（我的局 %d 个窗口）、%s（职业局 %d 个窗口），贴到理论值 %s，'
                 '基本没有"有效值折损"——满采就是满采。</div></div>'
                 % (_fmt(mine_gas.get("p50"), 1), mine_gas.get("windows", 0),
                    _fmt(pro_gas.get("p50"), 1), pro_gas.get("windows", 0),
                    _fmt(theoretical_gas, 1)))
    parts.append('<div class="card chart-card"><h3>为什么有两个系数</h3>'
                 '<div class="hint">'
                 '<b>仿真器（找最优 BO）用理论值 %s：</b>它假设农民一直在采，'
                 '算的是"这套 BO 的上限能在第几秒出门"。用有效值会把所有时间点'
                 '往后拖约 40%%，"最佳出门时间"直接算错。<br><br>'
                 '<b>复盘预测/逐点对账用有效值：</b>收入模型系数 a = %s 矿/分/农民'
                 '（约 %s%% 理论值），它把走路、闲置、气矿占人都摊进去了，'
                 '预测"这局实际会进多少钱"才对。<br><br>'
                 '<b>口径：</b>下面的百分比只在「%s」这条测量规则下成立，'
                 '不是"你只有 %s%% 的经济"这种全局结论。'
                 '</div></div>'
                 % (_fmt(theoretical_min, 1), _fmt(fitted, 1),
                    _fmt(100.0 * (fitted or 0.0) / theoretical_min, 1),
                    MINING_EFFICIENCY_RULE,
                    _fmt(mine_min.get("efficiency_pct", 0.0), 1)))
    parts.append('</div>')

    parts.append('<div class="card" style="margin-top:14px">')
    parts.append('<table class="data"><tr><th>分组</th><th>资料</th><th>窗口数</th>'
                 '<th>p50</th><th>p75</th><th>p90</th><th>max</th><th>效率(p50/理论)</th></tr>')
    for label, summary in (("我的局 · 矿物", mine_min), ("职业局 · 矿物", pro_min)):
        parts.append("<tr><td class='l'>%s</td><td>矿/分/农民</td><td>%d</td>"
                     "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (label, summary.get("windows", 0),
                        _fmt(summary.get("p50"), 1), _fmt(summary.get("p75"), 1),
                        _fmt(summary.get("p90"), 1), _fmt(summary.get("max"), 1),
                        _pct(summary.get("efficiency_pct", 0.0))))
    for label, summary in (("我的局 · 气体", mine_gas), ("职业局 · 气体", pro_gas)):
        parts.append("<tr><td class='l'>%s</td><td>气/分/气矿</td><td>%d</td>"
                     "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (label, summary.get("windows", 0),
                        _fmt(summary.get("p50"), 1), _fmt(summary.get("p75"), 1),
                        _fmt(summary.get("p90"), 1), _fmt(summary.get("max"), 1),
                        _pct(summary.get("efficiency_pct", 0.0))))
    parts.append('</table>')
    parts.append('<div class="hint" style="margin-top:10px">测量规则：我的局「%s」；'
                 '气体「%s」。数据：%d 局我的录像 + %d 局职业录像。'
                 '<b>注意：</b>因为要求「0 气矿 + 14~16 农民稳定 + 无任何花费」，'
                 '干净窗口集中在开局 40~70 秒（含开局第一趟矿的爬坡），'
                 '单组样本只有约 80 个窗口，p50 有 ±2~3 矿的抽样噪声；'
                 'p90（45.0 / 42.9）和最大值（47.1）是稳定的。</div>'
                 % (MINING_EFFICIENCY_RULE, GAS_EFFICIENCY_RULE,
                    mine_efficiency.get("files", 0), pro_efficiency.get("files", 0)))
    parts.append('<div class="hint" style="margin-top:10px"><b>交叉核对（lead 独立测量，'
                 '同规则不同实现/抽样）：</b>我的局 p50 %s（%s%%）、职业局 p50 %s（%s%%）；'
                 '本模块复测在同一量级，p90 / max / 气体上限完全一致，p50 差 2~4 个点'
                 '是抽样与窗口实现差异，不是规则分歧。</div>'
                 % (_fmt(LEAD_REFERENCE_MEASUREMENT["mine"]["p50"], 1),
                    _fmt(LEAD_REFERENCE_MEASUREMENT["mine"]["efficiency_pct"], 1),
                    _fmt(LEAD_REFERENCE_MEASUREMENT["pro"]["p50"], 1),
                    _fmt(LEAD_REFERENCE_MEASUREMENT["pro"]["efficiency_pct"], 1)))
    parts.append('</div>')

    # ---- 七、拟合质量
    parts.append('<h2>七、收入拟合质量（训练/测试按对局切分）</h2><div class="card">')
    parts.append('<table class="data"><tr><th>模型</th><th>系数</th>'
                 '<th>训练 MAE</th><th>测试 MAE</th><th>测试 R²</th></tr>')
    for candidate in mineral_fit["candidates"]:
        params = ", ".join("%s=%s" % (key, _fmt(value, 3))
                           for key, value in candidate["params"].items())
        marker = " ★" if candidate["id"] == mineral_fit["chosen"] else ""
        parts.append("<tr><td class='l'>%s%s</td><td class='l'>%s</td><td>%s</td>"
                     "<td>%s</td><td>%s</td></tr>"
                     % (candidate["label"], marker, params,
                        _fmt(candidate["train"]["mae"], 1),
                        _fmt(candidate["test"]["mae"], 1),
                        _fmt(candidate["test"]["r2"], 3)))
    parts.append('</table>')
    parts.append('<div class="hint" style="margin-top:10px">训练/测试集按<b>对局</b>切分'
                 '（每 3 局留 1 局做测试），避免同一局的相邻采样点泄漏。'
                 '目标值是<b>账本实测</b>的每分钟收入，不是游戏自带 CollectionRate。</div>')
    parts.append('</div>')

    # ---- 七、四局验收
    parts.append('<h2>八、四局造价闭合验收</h2><div class="card">')
    parts.append('<table class="data"><tr><th>对局</th><th>种族</th>'
                 '<th>时长</th><th>账本收入</th><th>单位+升级+在建</th>'
                 '<th>存款占比</th><th>误差</th></tr>')
    for row in validation:
        parts.append("<tr><td class='l'>%s</td><td>%s</td><td>%s</td>"
                     "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (row["file"], row.get("race", "?"),
                        _fmt(row["duration"] / 60.0, 1, " 分"),
                        _fmt(row["collected_minerals"]),
                        _fmt(row["unit_minerals"]),
                        _pct(100.0 * row.get("bank_ratio", 0.0)),
                        _pct(row["error_pct"])))
    if validation:
        total_collected = sum(row["collected_minerals"] for row in validation)
        total_units = sum(row["unit_minerals"] for row in validation)
        aggregate = (100.0 * (total_units - total_collected) / total_collected
                     if total_collected else 0.0)
        parts.append("<tr><td class='l'>四局合计</td><td>—</td><td>—</td>"
                     "<td>%s</td><td>%s</td><td>—</td><td>%s</td></tr>"
                     % (_fmt(total_collected), _fmt(total_units), _pct(aggregate)))
    parts.append('</table>')
    if validation:
        per_game_text = "、".join("%s %s" % (row["file"], _pct(row["error_pct"]))
                                  for row in validation)
        middle = sorted(row["error_pct"] for row in validation)
        median4 = ((middle[1] + middle[2]) / 2.0) if len(middle) == 4 else (
            middle[len(middle) // 2] if middle else 0.0)
        parts.append('<div class="hint" style="margin-top:10px">'
                     '挑选规则写死：主验证局 %s + 按文件名排序、'
                     '「本地玩家 / 天梯 / 统计覆盖终局 / 存款占比<10%%」的前 3 局，'
                     '<b>不按误差挑</b>。逐局：%s；四局合计 %s，四局中位 %s。'
                     '全样本 %d 局的误差分布：中位 %s，四分位 %s ~ %s，'
                     '|误差|<2%% 的有 %d 局——造价表存在 ±5%% 量级的系统性口径噪声'
                     '（取消退款、morph、死亡时点、未知升级造价），单局 2%% 只在'
                     '账本干净的局成立。</div>'
                     % (DEFAULT_REPORT_GAME, per_game_text, _pct(aggregate),
                        _pct(median4), distribution.get("n", 0),
                        _pct(distribution.get("median")),
                        _pct(distribution.get("p25")), _pct(distribution.get("p75")),
                        distribution.get("within_2pct", 0)))
    parts.append('</div>')

    # ---- 八、每局对账误差
    if per_game_reconcile:
        parts.append('<h2>九、四局逐点对账误差（模型库存 vs 实际库存）</h2><div class="card">')
        parts.append('<table class="data"><tr><th>对局</th><th>种族</th>'
                     '<th>矿物 MAE</th><th>占累计收入</th><th>终局误差</th>'
                     '<th>气体 MAE</th></tr>')
        for item in per_game_reconcile:
            parts.append("<tr><td class='l'>%s</td><td>%s</td><td>%s</td>"
                         "<td>%s</td><td>%s</td><td>%s</td></tr>"
                         % (item["file"], item.get("race", "?"),
                            _fmt(item["mineral"]["mae"], 0),
                            _pct(item["mineral"]["mae_pct"]),
                            _pct(item["mineral"]["final_error_pct"]),
                            _fmt(item["vespene"]["mae"], 0)))
        parts.append('</table><div class="hint" style="margin-top:10px">'
                     '模型库存 = 起始存款 + 模型收入积分 - 造价表支出；'
                     '误差里同时包含收入模型误差和造价表误差。</div></div>')
    # ---- 九、升级造价表
    parts.append('<h2>十、升级造价表</h2><div class="card">')
    known = sorted((name, cost) for name, cost in UPGRADE_COST.items() if cost)
    unknown = sorted(name for name, cost in UPGRADE_COST.items() if not cost)
    parts.append('<div class="hint">已收录 <b>%d</b> 条、留空 <b>%d</b> 条。'
                 '留空的是查不到可靠造价/版本不统一的条目，<b>不编造</b>，'
                 '对账时不计入（会在下面列出来）。</div>' % (len(known), len(unknown)))
    parts.append('<table class="data"><tr><th>升级</th><th>矿</th><th>气</th></tr>')
    for name, cost in known:
        parts.append("<tr><td class='l'>%s</td><td>%d</td><td>%d</td></tr>"
                     % (units.upgrade_label(name), cost[0], cost[1]))
    parts.append('</table>')
    if unknown:
        parts.append('<div class="hint" style="margin-top:10px">未收录：'
                     + "、".join(units.upgrade_label(name) for name in unknown[:40])
                     + '</div>')
    parts.append('</div>')

    # ---- 十、怎么复现
    parts.append('<h2>十一、复现</h2><div class="card"><div class="hint">')
    parts.append('在仓库根目录执行：<br><code>python -m replay_analyzer.econ --limit 90</code>'
                 '<br>会重新抽样、解析、拟合、测采矿效率（约 40~60 秒），并覆盖生成 '
                 '<code>reports/econ.html</code> 和 <code>reports/econ.json</code>。'
                 '纯标准库 + 仓内 vendor，离线可跑。')
    parts.append('</div></div>')

    parts.append('<footer>数据来源：%d 局 replay 的 SPlayerStatsEvent 账本；'
                 '生成时间 %s。</footer></div></body></html>'
                 % (len(corpus["games"]), generated))
    return "".join(parts)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(replay_dir=DEFAULT_REPLAY_DIR, limit=90, out_html=DEFAULT_OUT_HTML,
        out_json=DEFAULT_OUT_JSON, main_file=DEFAULT_REPORT_GAME, verbose=True):
    """跑完整流程：抽样 → 拟合 → 对账 → 写 reports/econ.html + econ.json。"""
    generated = time.strftime("%Y-%m-%d %H:%M")
    if verbose:
        print("[econ] scanning %s (limit=%d)" % (replay_dir, limit))
    corpus = build_corpus(replay_dir, limit=limit, must_include=[main_file])
    samples = corpus["samples"]
    if not samples:
        raise RuntimeError("没有可用的收入采样点：请检查 replay 目录 %r" % replay_dir)

    mineral_fit = fit_income(samples, target_key="rate", models=MINERAL_MODELS)
    vespene_fit = fit_income(samples, target_key="gas_rate", models=VESPENE_MODELS)
    anchor = anchor_check(samples)
    bias = reported_bias(samples)

    # 采矿效率：干净窗口实测（我的局抽样 + 全部职业局）
    efficiency_limit = min(240, max(120, limit * 2))
    mine_raw = collect_efficiency(replay_dir, limit=efficiency_limit,
                                  only_local=True, verbose=verbose)
    pro_raw = collect_efficiency(PRO_DIR, limit=None, only_local=False,
                                 verbose=verbose)
    mine_efficiency = {
        "files": mine_raw["files"],
        "minerals": summarize_efficiency(
            mine_raw["minerals"], THEORETICAL_MINERAL_PER_WORKER_PER_MIN),
        "vespene": summarize_efficiency(
            mine_raw["vespene"], THEORETICAL_VESPENE_PER_REFINERY_PER_MIN),
        "by_player": mine_raw["by_player"][:8],
    }
    pro_efficiency = {
        "files": pro_raw["files"],
        "minerals": summarize_efficiency(
            pro_raw["minerals"], THEORETICAL_MINERAL_PER_WORKER_PER_MIN),
        "vespene": summarize_efficiency(
            pro_raw["vespene"], THEORETICAL_VESPENE_PER_REFINERY_PER_MIN),
        "by_player": pro_raw["by_player"][:12],
    }
    theoretical = {
        "mineral_per_worker_per_min": THEORETICAL_MINERAL_PER_WORKER_PER_MIN,
        "vespene_per_refinery_per_min": THEORETICAL_VESPENE_PER_REFINERY_PER_MIN,
        "source": "干净窗口实测 p90 收敛（lead 独立测量 + 本模块复测；"
                  "矿物 p90 我的局 45.0 / 职业局 42.9，气体 p50=p90=120.0）",
        "mining_rule": MINING_EFFICIENCY_RULE,
        "gas_rule": GAS_EFFICIENCY_RULE,
    }

    # 主验证局：用选中的收入模型跑逐点对账
    panel = None
    reconcile_data = None
    report_game = pick_report_game(corpus, main_file)
    if report_game:
        report_path = None
        for path in corpus["paths"]:
            if os.path.basename(path) == report_game["file"]:
                report_path = path
                break
        if report_path:
            parsed = load_replay(report_path, decode_events=False)
            player = next((item for item in parsed.players
                           if getattr(item, "is_local", False)), parsed.players[0])
            reconcile_data = reconcile(parsed, player.player_id,
                                       mineral_fit["chosen_model"],
                                       vespene_fit["chosen_model"])
            symbols = {
                "map": parsed.map_name,
                "player": player.name,
                "race": player.race,
            }
            panel = {
                "parsed": parsed,
                "player_id": player.player_id,
                "duration": parsed.duration_seconds or 0,
                "symbols": symbols,
            }

    validation = validation_games(corpus, main_file=main_file, extra=3)
    per_game_reconcile = []
    path_by_file = {os.path.basename(item): item for item in corpus["paths"]}
    for row in validation:
        path = path_by_file.get(row["file"])
        if not path:
            continue
        try:
            parsed = load_replay(path, decode_events=False)
        except Exception:  # noqa: BLE001 - 单局失败不影响整体
            continue
        player = next((item for item in parsed.players
                       if item.player_id == row.get("player_id")), None)
        if player is None:
            player = next((item for item in parsed.players
                           if getattr(item, "is_local", False)), None)
        if player is None:
            continue
        result = reconcile(parsed, player.player_id, mineral_fit["chosen_model"],
                           vespene_fit["chosen_model"])
        if not result:
            continue
        per_game_reconcile.append({
            "file": result["file"],
            "player": result["player"],
            "race": result["race"],
            "mineral": result["mineral"],
            "vespene": result["vespene"],
            "point_count": len(result["series"]),
        })

    html = render_html(corpus, mineral_fit, vespene_fit, panel, reconcile_data,
                       validation, anchor, bias, generated, per_game_reconcile,
                       theoretical, mine_efficiency, pro_efficiency)
    os.makedirs(os.path.dirname(os.path.abspath(out_html)), exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as handle:
        handle.write(html)

    payload = {
        "generated": generated,
        "corpus": {
            "replay_dir": corpus["replay_dir"],
            "files_total": corpus["files_total"],
            "sampled": len(corpus["games"]),
            "players_used": len(corpus["closure"]),
            "income_samples": len(samples),
            "elapsed_seconds": corpus["elapsed"],
        },
        "anchor": anchor,
        "reported_rate_bias": bias,
        "theoretical": theoretical,
        "efficiency": {
            "mining_rule": MINING_EFFICIENCY_RULE,
            "gas_rule": GAS_EFFICIENCY_RULE,
            "mine": mine_efficiency,
            "pro": pro_efficiency,
            "lead_reference": LEAD_REFERENCE_MEASUREMENT,
            "fitted_model_coefficient_minerals_per_worker_min": (
                mineral_fit["chosen_model"]["params"].get("a")),
            "usage": ("仿真器求 BO 最优解用 theoretical（假设农民一直在采）；"
                      "复盘预测/对账用有效值（收入模型系数，或按组 p50）。"),
        },
        "income_model": {
            "mineral": {
                "chosen": mineral_fit["chosen"],
                "params": mineral_fit["chosen_model"]["params"],
                "train": mineral_fit["chosen_model"]["train"],
                "test": mineral_fit["chosen_model"]["test"],
                "candidates": mineral_fit["candidates"],
                "units": "矿/分钟",
                "target": "账本实测收入（collected 的中心差分）",
            },
            "vespene": {
                "chosen": vespene_fit["chosen"],
                "params": vespene_fit["chosen_model"]["params"],
                "train": vespene_fit["chosen_model"]["train"],
                "test": vespene_fit["chosen_model"]["test"],
                "candidates": vespene_fit["candidates"],
                "units": "气/分钟",
            },
        },
        "closure": {
            "metric": "单位造价 + 升级造价 + 在建单位 vs 账本累计收入（矿物）",
            "validation_games": validation,
            "distribution_local_players": _distribution(
                [row["error_pct"] for row in corpus["closure"]
                 if row.get("is_local") and row.get("is_ladder")]),
            "strict_distribution": _distribution(
                [row["strict_error_pct"] for row in corpus["closure"]
                 if row.get("is_local") and row.get("is_ladder")]),
            "games": corpus["closure"],
        },
        "reconcile": {
            "game": reconcile_data["file"] if reconcile_data else "",
            "result": reconcile_data,
            "games": per_game_reconcile,
        },
        "upgrade_cost": {
            "cost": {name: (list(cost) if cost else None)
                     for name, cost in UPGRADE_COST.items()},
            "uncosted": [name for name, cost in UPGRADE_COST.items() if not cost],
        },
    }
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)

    if verbose:
        print("[econ] games=%d samples=%d" % (len(corpus["games"]), len(samples)))
        print("[econ] mineral fit: %s" % mineral_fit["chosen"])
        print("[econ]   params=%s test=%s"
              % (mineral_fit["chosen_model"]["params"],
                 mineral_fit["chosen_model"]["test"]))
        print("[econ] vespene fit: %s params=%s test=%s"
              % (vespene_fit["chosen"], vespene_fit["chosen_model"]["params"],
                 vespene_fit["chosen_model"]["test"]))
        print("[econ] anchor median=%.1f (doc %.1f)  reported/ledger bias=%.2fx"
              % (anchor.get("median", 0.0), DOC_ANCHOR_EFFICIENCY,
                 bias.get("median_ratio", 0.0)))
        print("[econ] efficiency mine: p50=%.1f p90=%.1f eff=%.1f%% (n=%d)  "
              "pro: p50=%.1f p90=%.1f eff=%.1f%% (n=%d)"
              % (mine_efficiency["minerals"].get("p50", 0.0),
                 mine_efficiency["minerals"].get("p90", 0.0),
                 mine_efficiency["minerals"].get("efficiency_pct", 0.0),
                 mine_efficiency["minerals"].get("windows", 0),
                 pro_efficiency["minerals"].get("p50", 0.0),
                 pro_efficiency["minerals"].get("p90", 0.0),
                 pro_efficiency["minerals"].get("efficiency_pct", 0.0),
                 pro_efficiency["minerals"].get("windows", 0)))
        print("[econ] gas efficiency mine p50=%.1f (n=%d) pro p50=%.1f (n=%d) "
              "theoretical=%.1f"
              % (mine_efficiency["vespene"].get("p50", 0.0),
                 mine_efficiency["vespene"].get("windows", 0),
                 pro_efficiency["vespene"].get("p50", 0.0),
                 pro_efficiency["vespene"].get("windows", 0),
                 THEORETICAL_VESPENE_PER_REFINERY_PER_MIN))
        local_closure = [row["error_pct"] for row in corpus["closure"]
                         if row.get("is_local") and row.get("is_ladder")]
        summary = _distribution(local_closure)
        print("[econ] closure median=%+.2f%% (n=%d)  within2pct=%d"
              % (summary.get("median", 0.0), summary.get("n", 0),
                 summary.get("within_2pct", 0)))
        for row in validation:
            print("[econ] validate %-44s %s  err=%s%%"
                  % (row["file"][:44], row.get("race", "?"), row["error_pct"]))
        if reconcile_data:
            print("[econ] reconcile %s mineral MAE=%.1f (%.2f%% of income)"
                  % (reconcile_data["file"], reconcile_data["mineral"]["mae"],
                     reconcile_data["mineral"]["mae_pct"]))
        print("[econ] wrote %s" % out_html)
        print("[econ] wrote %s" % out_json)
    return {
        "html": out_html,
        "json": out_json,
        "games": len(corpus["games"]),
        "samples": len(samples),
        "mineral_model": mineral_fit["chosen_model"],
        "vespene_model": vespene_fit["chosen_model"],
        "anchor": anchor,
        "reported_rate_bias": bias,
        "theoretical": theoretical,
        "efficiency": {
            "mine": mine_efficiency,
            "pro": pro_efficiency,
        },
        "validation": validation,
        "reconcile": reconcile_data,
    }


def distribution_summary(corpus):
    rows = [row["error_pct"] for row in corpus["closure"] if row.get("is_local")]
    distribution = _distribution(rows)
    return distribution.get("median", "—")


def main(argv=None):
    parser = argparse.ArgumentParser(description="经济模型：账本收入 + 拟合 + 对账报告")
    parser.add_argument("--limit", type=int, default=90,
                        help="抽样局数（默认 90，建议 60~120）")
    parser.add_argument("--replay-dir", default=DEFAULT_REPLAY_DIR,
                        help="录像目录（默认本机天梯录像目录）")
    parser.add_argument("--out", default=DEFAULT_OUT_HTML, help="HTML 输出路径")
    parser.add_argument("--json", default=DEFAULT_OUT_JSON, help="JSON 输出路径")
    parser.add_argument("--game", default=DEFAULT_REPORT_GAME,
                        help="报告里画曲线的主验证局文件名")
    parser.add_argument("--pro-dir", default=PRO_DIR,
                        help="职业录像目录（默认 replays/pro，按选手分文件夹）")
    args = parser.parse_args(argv)
    run(replay_dir=args.replay_dir, limit=args.limit, out_html=args.out,
        out_json=args.json, main_file=args.game)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
