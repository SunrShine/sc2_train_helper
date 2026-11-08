"""单位视野半径（sight radius）字典。

为什么需要这张表
----------------
replay 只记录"单位在哪儿、什么时候死"，**不记录迷雾状态**。
想知道"某一刻谁能不能看到谁"，只能自己用几何算：

    看得见  <=>  目标点在某个己方单位的视野圆里

所以每个单位类型的视野半径是这套计算的地基。

数据来源与误差
--------------
数值按游戏内单位数据整理（社区资料 / 手册口径），常见兵种可信度高，
冷门建筑和召唤物可能有 ±1 的出入。可用下面两种方式覆盖：

1. 在 ``replay_analyzer/data/sight_radius.json`` 里写
   ``{"marine": 10, "observersiege": 12}``，同名键优先级最高；
2. 调用时传 ``scale=1.1`` 做整体放缩，用来做敏感度检查
   （"如果视野都多 10%，结论会变吗"）。

这张表故意保持"可改"，因为把一个近似值包装成精确值才是真的坑人。
"""

import json
import os

# --------------------------------------------------------------------------- 数据
# 键统一用小写内部名（tracker 里的 m_unitTypeName）。
SIGHT_RADIUS = {
    # ---------------- 人族 ----------------
    "scv": 9.0,
    "marine": 9.0,
    "marauder": 10.0,
    "reaper": 9.0,
    "ghost": 11.0,
    "hellion": 10.0,
    "helliontank": 10.0,
    "widowmine": 7.0,
    "widowmineburrowed": 7.0,
    "siegetank": 11.0,
    "siegetanksieged": 12.0,
    "cyclone": 11.0,
    "thor": 11.0,
    "vikingfighter": 11.0,
    "vikingassault": 11.0,
    "medivac": 11.0,
    "liberator": 11.0,
    "liberatorag": 11.0,
    "banshee": 11.0,
    "raven": 11.0,
    "battlecruiser": 12.0,
    "mule": 8.0,
    "commandcenter": 11.0,
    "commandcenterflying": 11.0,
    "orbitalcommand": 11.0,
    "orbitalcommandflying": 11.0,
    "planetaryfortress": 11.0,
    "supplydepot": 8.0,
    "supplydepotlowered": 8.0,
    "refinery": 8.0,
    "barracks": 9.0,
    "barracksflying": 9.0,
    "barracksreactor": 9.0,
    "barrackstechlab": 9.0,
    "factory": 9.0,
    "factoryflying": 9.0,
    "factoryreactor": 9.0,
    "factorytechlab": 9.0,
    "starport": 9.0,
    "starportflying": 9.0,
    "starportreactor": 9.0,
    "starporttechlab": 9.0,
    "engineeringbay": 9.0,
    "armory": 9.0,
    "ghostacademy": 9.0,
    "fusioncore": 9.0,
    "bunker": 9.0,
    "missileturret": 11.0,
    "sensortower": 11.0,
    "autoturret": 11.0,
    # ---------------- 星灵 ----------------
    "probe": 9.0,
    "zealot": 9.0,
    "stalker": 10.0,
    "sentry": 10.0,
    "adept": 9.0,
    "hightemplar": 10.0,
    "darktemplar": 8.0,
    "archon": 10.0,
    "immortal": 10.0,
    "colossus": 11.0,
    "disruptor": 10.0,
    "phoenix": 10.0,
    "oracle": 11.0,
    "voidray": 10.0,
    "carrier": 11.0,
    "tempest": 12.0,
    "observer": 11.0,
    "warpprism": 10.0,
    "warpprismphasing": 10.0,
    "mothership": 12.0,
    "nexus": 11.0,
    "pylon": 8.0,
    "assimilator": 8.0,
    "gateway": 9.0,
    "warpgate": 9.0,
    "forge": 9.0,
    "cyberneticscore": 9.0,
    "twilightcouncil": 9.0,
    "roboticsfacility": 9.0,
    "roboticsbay": 9.0,
    "stargate": 9.0,
    "templararchives": 9.0,
    "darkshrine": 9.0,
    "fleetbeacon": 9.0,
    "photoncannon": 11.0,
    "shieldbattery": 9.0,
    # ---------------- 异虫 ----------------
    "drone": 8.0,
    "zergling": 8.0,
    "zerglingburrowed": 8.0,
    "baneling": 8.0,
    "banelingburrowed": 8.0,
    "queen": 9.0,
    "queenburrowed": 9.0,
    "roach": 9.0,
    "roachburrowed": 9.0,
    "ravager": 9.0,
    "ravagerburrowed": 9.0,
    "hydralisk": 9.0,
    "hydraliskburrowed": 9.0,
    "lurker": 9.0,
    "lurkerburrowed": 9.0,
    "mutalisk": 10.0,
    "corruptor": 10.0,
    "broodlord": 11.0,
    "swarmhost": 10.0,
    "swarmhostburrowed": 10.0,
    "infestor": 10.0,
    "infestorburrowed": 10.0,
    "viper": 10.0,
    "ultralisk": 10.0,
    "ultraliskburrowed": 10.0,
    "overlord": 11.0,
    "overseer": 11.0,
    "overseermorph": 11.0,
    "hatchery": 11.0,
    "lair": 11.0,
    "hive": 11.0,
    "extractor": 8.0,
    "spawningpool": 9.0,
    "evolutionchamber": 9.0,
    "roachwarren": 9.0,
    "banelingnest": 9.0,
    "hydraliskden": 9.0,
    "spire": 9.0,
    "greaterspire": 9.0,
    "infestationpit": 9.0,
    "nydusnetwork": 9.0,
    "nyduscanal": 9.0,
    "ultraliskcavern": 9.0,
    "spinecrawler": 9.0,
    "spinecrawleruprooted": 9.0,
    "sporecrawler": 11.0,
    "sporecrawleruprooted": 11.0,
    "creeptumor": 8.0,
    "creeptumorburrowed": 8.0,
    "creeptumorqueen": 8.0,
}

# 查不到具体单位时的兜底：宁可按"偏小"给，避免虚报视野。
FALLBACK = {
    "worker": 9.0,
    "townhall": 11.0,
    "structure": 9.0,
    "army": 9.0,
    "other": 9.0,
}

# 这些单位提供视野的方式特殊或干脆不提供（卵/幼虫/炮塔类装饰），
# 给它们一个很小的圈，避免把"卵"当成侦察单位。
TINY_VISION = {"larva", "egg", "broodling", "broodlingescort", "interceptor",
               "locust", "locustmp", "infestedterran", "changeling"}
TINY_RADIUS = 4.0

_OVERRIDE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "data", "sight_radius.json")
_override_cache = None


def load_overrides():
    """读取用户覆盖文件；没有就返回空字典。"""
    global _override_cache
    if _override_cache is not None:
        return _override_cache
    data = {}
    if os.path.isfile(_OVERRIDE_PATH):
        try:
            with open(_OVERRIDE_PATH, "r", encoding="utf-8-sig") as handle:
                raw = json.load(handle)
            data = {str(k).lower(): float(v) for k, v in raw.items()
                    if not str(k).startswith("_")}
        except Exception:
            data = {}
    _override_cache = data
    return data


def reset_overrides():
    global _override_cache
    _override_cache = None


def sight_radius(name, category=None, scale=1.0):
    """返回某单位类型的视野半径（格）。"""
    key = (name or "").lower()
    overrides = load_overrides()
    if key in overrides:
        return overrides[key] * scale
    if key in SIGHT_RADIUS:
        return SIGHT_RADIUS[key] * scale
    if key in TINY_VISION:
        return TINY_RADIUS * scale
    fallback = FALLBACK.get(category or "other", FALLBACK["other"])
    return fallback * scale


def known(name):
    key = (name or "").lower()
    return key in load_overrides() or key in SIGHT_RADIUS or key in TINY_VISION


def missing_types(parsed):
    """统计这局里出现过、但表里没有视野数据的单位类型（用于报告的可信度提示）。"""
    seen = {}
    for record in parsed.unit_records:
        for unit_name in [record.name] + [m[1] for m in record.morphs]:
            if not unit_name:
                continue
            key = unit_name.lower()
            if key in SIGHT_RADIUS or key in TINY_VISION or key in load_overrides():
                continue
            if unit_name.startswith(("Beacon", "Spray", "Destructible", "MineralField",
                                     "VespeneGeyser", "RichMineralField", "XelNaga",
                                     "Unbuildable", "Collapsible", "SpacePlatform")):
                continue
            seen[unit_name] = seen.get(unit_name, 0) + 1
    return sorted(seen.items(), key=lambda kv: -kv[1])
