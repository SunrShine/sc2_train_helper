"""单位与升级的名称、分类、造价字典。

replay 的 tracker 事件里单位是以内部英文名出现的（如 ``Marine``、``WarpGate``），
这里把它们翻译成中文、划分到"农民 / 基地 / 建筑 / 部队"等类别，并给出造价，
供建造顺序、战损、构成分析使用。

字典不需要 100% 覆盖：查不到的名字会自动退化成 "SupplyDepot -> Supply Depot"
这样的驼峰拆分显示，不会让工具崩掉。
"""

import re

# --------------------------------------------------------------------------- 集合
WORKER_UNITS = {"SCV", "Probe", "Drone"}

TOWNHALL_UNITS = {
    "CommandCenter", "CommandCenterFlying", "OrbitalCommand", "OrbitalCommandFlying",
    "PlanetaryFortress", "Nexus", "Hatchery", "Lair", "Hive",
}

# 基地类建筑（含生产/科技建筑），用于建造顺序上色
PRODUCTION_UNITS = {
    "Barracks", "Factory", "Starport", "BarracksReactor", "FactoryReactor",
    "StarportReactor", "BarracksTechLab", "FactoryTechLab", "StarportTechLab",
    "Gateway", "WarpGate", "RoboticsFacility", "Stargate", "SpawningPool",
    "RoachWarren", "BanelingNest", "HydraliskDen", "InfestationPit", "Spire",
    "GreaterSpire", "UltraliskCavern", "NydusNetwork", "Lair", "Hive",
}

TECH_UNITS = {
    "EngineeringBay", "Armory", "GhostAcademy", "FusionCore", "SensorTower",
    "CyberneticsCore", "Forge", "TwilightCouncil", "TemplarArchives", "DarkShrine",
    "FleetBeacon", "RoboticsBay", "EvolutionChamber",
}

DEFENSE_UNITS = {
    "Bunker", "MissileTurret", "PhotonCannon", "ShieldBattery", "SpineCrawler",
    "SporeCrawler", "PlanetaryFortress", "Pylon",
}

ECONOMY_UNITS = {"SupplyDepot", "SupplyDepotLowered", "Refinery", "Assimilator",
                 "Extractor", "Pylon", "MULE", "Nexus", "CommandCenter", "Hatchery"}

ADDON_UNITS = {
    "BarracksReactor", "FactoryReactor", "StarportReactor",
    "BarracksTechLab", "FactoryTechLab", "StarportTechLab",
}

ECONOMY_STRUCTURES = {"SupplyDepot", "SupplyDepotLowered", "Refinery",
                      "Assimilator", "Extractor", "Pylon"}

# 所有建筑：判断"这是一栋楼还是一个兵"时以名字为准，
# 不能看它是用 UnitInit 还是 UnitBorn 事件出现的——
# 星灵的跃迁门兵种（狂热者/追猎者…）同样是 UnitInit + UnitDone。
STRUCTURE_UNITS = (PRODUCTION_UNITS | TECH_UNITS | DEFENSE_UNITS |
                   ECONOMY_STRUCTURES | ADDON_UNITS | TOWNHALL_UNITS |
                   {"Forge", "EvolutionChamber", "NydusCanal"})

# 不进入"建造顺序"的噪音单位
NOISE_UNITS = {
    "Larva", "Egg", "CreepTumor", "CreepTumorBurrowed", "CreepTumorQueen",
    "Interceptor", "Locust", "LocustMP", "Broodling", "BroodlingEscort",
    "AutoTurret", "PointDefenseDrone", "MULE", "RavenRepairDrone",
    "InfestedTerran", "Changeling", "ChangelingZealot", "ChangelingMarine",
    "AdeptPhaseShift", "MineralField", "MineralField750", "VespeneGeyser",
    "SpacePlatformGeyser", "RichMineralField", "RichMineralField750",
    "DestructibleRockEx1", "DestructibleRockEx2", "CollapsibleRockTower",
    "UnbuildablePlatesDestructible", "XelNagaTower", "Nuke", "NukePersistent",
    "SprayProtoss", "SprayTerran", "SprayZerg", "BeaconArmy", "BeaconDefend",
    "BeaconAttack", "BeaconHarass", "BeaconIdle", "BeaconMining",
    "BeaconReturn", "BeaconScout", "BeaconExpand", "BeaconRally", "BeaconClaim",
    "BeaconCustom1", "BeaconCustom2", "BeaconCustom3", "BeaconCustom4",
}

# --------------------------------------------------------------------------- 中文名
UNIT_NAMES_ZH = {
    # ---- 人族 ----
    "SCV": "SCV",
    "MULE": "矿骡",
    "CommandCenter": "指挥中心",
    "CommandCenterFlying": "指挥中心（升空）",
    "OrbitalCommand": "轨道控制基地",
    "OrbitalCommandFlying": "轨道控制基地（升空）",
    "PlanetaryFortress": "行星要塞",
    "SupplyDepot": "补给站",
    "SupplyDepotLowered": "补给站（放下）",
    "Refinery": "精炼厂",
    "Barracks": "兵营",
    "BarracksFlying": "兵营（升空）",
    "BarracksReactor": "兵营-反应堆",
    "BarracksTechLab": "兵营-科技实验室",
    "Factory": "重工厂",
    "FactoryFlying": "重工厂（升空）",
    "FactoryReactor": "重工厂-反应堆",
    "FactoryTechLab": "重工厂-科技实验室",
    "Starport": "星港",
    "StarportFlying": "星港（升空）",
    "StarportReactor": "星港-反应堆",
    "StarportTechLab": "星港-科技实验室",
    "EngineeringBay": "工程站",
    "Armory": "军械库",
    "GhostAcademy": "幽灵学院",
    "FusionCore": "聚变核心",
    "SensorTower": "感应塔",
    "Bunker": "地堡",
    "MissileTurret": "导弹塔",
    "Marine": "陆战队员",
    "Marauder": "劫掠者",
    "Reaper": "收割者",
    "Ghost": "幽灵",
    "Hellion": "恶火",
    "HellionTank": "恶蝠",
    "WidowMine": "寡妇雷",
    "WidowMineBurrowed": "寡妇雷（埋地）",
    "SiegeTank": "攻城坦克",
    "SiegeTankSieged": "攻城坦克（架起）",
    "Cyclone": "旋风",
    "Thor": "雷神",
    "VikingFighter": "维京战机",
    "VikingAssault": "维京（机甲）",
    "Medivac": "医疗运输机",
    "Liberator": "解放者",
    "LiberatorAG": "解放者（架起）",
    "Raven": "渡鸦",
    "Banshee": "女妖",
    "Battlecruiser": "战列巡航舰",
    "AutoTurret": "自动机炮",
    "PointDefenseDrone": "点防御无人机",
    # ---- 星灵 ----
    "Probe": "探机",
    "Nexus": "星核",
    "Pylon": "水晶塔",
    "Gateway": "传送门",
    "WarpGate": "跃迁门",
    "CyberneticsCore": "控制核心",
    "Assimilator": "吸收场",
    "Forge": "熔炉",
    "TwilightCouncil": "暮光议会",
    "RoboticsFacility": "机械工厂",
    "Stargate": "星门",
    "TemplarArchives": "圣堂档案馆",
    "DarkShrine": "黑暗圣所",
    "FleetBeacon": "瓦伦里安信标",
    "RoboticsBay": "机械湾",
    "PhotonCannon": "光子炮台",
    "ShieldBattery": "护盾电池",
    "Zealot": "狂热者",
    "Stalker": "追猎者",
    "Sentry": "哨兵",
    "Adept": "使徒",
    "HighTemplar": "高阶圣堂武士",
    "DarkTemplar": "黑暗圣堂武士",
    "Archon": "执政官",
    "Immortal": "不朽者",
    "Colossus": "巨像",
    "Disruptor": "干扰者",
    "Observer": "观察者",
    "ObserverSiegeMode": "观察者（定身）",
    "WarpPrism": "曲棱棱镜",
    "WarpPrismPhasing": "曲棱棱镜（相位）",
    "Phoenix": "凤凰",
    "VoidRay": "虚空辉光舰",
    "Oracle": "先知",
    "Tempest": "暴风舰",
    "Carrier": "航母",
    "Interceptor": "拦截机",
    "Mothership": "母舰",
    "AdeptPhaseShift": "使徒幻影",
    # ---- 异虫 ----
    "Drone": "工蜂",
    "Overlord": "王虫",
    "Overseer": "眼虫",
    "Queen": "后虫",
    "Zergling": "跳虫",
    "Baneling": "爆虫",
    "BanelingCocoon": "爆虫茧",
    "Roach": "蟑螂",
    "Ravager": "破坏者",
    "Hydralisk": "刺蛇",
    "Lurker": "潜伏者",
    "Mutalisk": "飞龙",
    "Corruptor": "腐化者",
    "BroodLord": "巢虫领主",
    "SwarmHost": "宿主",
    "Infestor": "感染虫",
    "Viper": "破坏蛇",
    "Ultralisk": "雷兽",
    "Hatchery": "孵化场",
    "Lair": "巢穴",
    "Hive": "蜂巢",
    "SpawningPool": "孵化池",
    "EvolutionChamber": "进化室",
    "RoachWarren": "蟑螂温室",
    "BanelingNest": "爆虫巢",
    "SpineCrawler": "脊针爬虫",
    "SporeCrawler": "孢子爬虫",
    "HydraliskDen": "刺蛇巢",
    "InfestationPit": "感染深渊",
    "Spire": "尖塔",
    "GreaterSpire": "大利维坦尖塔",
    "NydusNetwork": "虫道网络",
    "NydusCanal": "虫道",
    "UltraliskCavern": "雷兽洞穴",
    "Extractor": "汲取场",
    "Larva": "幼虫",
    "Egg": "虫卵",
    "Locust": "蝗虫",
    "Broodling": "巢虫",
    "CreepTumor": "菌毯肿瘤",
    "CreepTumorBurrowed": "菌毯肿瘤（埋地）",
}

# --------------------------------------------------------------------------- 造价
# (矿, 气, 人口)
UNIT_COST = {
    "SCV": (50, 0, 1), "Probe": (50, 0, 1), "Drone": (50, 0, 1),
    "MULE": (0, 0, 0),
    "Marine": (50, 0, 1), "Marauder": (100, 25, 2), "Reaper": (50, 50, 1),
    "Ghost": (150, 125, 2), "Hellion": (100, 0, 2), "HellionTank": (100, 0, 2),
    "WidowMine": (75, 25, 1), "SiegeTank": (175, 125, 3), "Cyclone": (150, 100, 3),
    "Thor": (300, 200, 6), "VikingFighter": (150, 75, 2), "Medivac": (100, 100, 2),
    "Liberator": (175, 125, 3), "Raven": (100, 200, 2), "Banshee": (150, 100, 3),
    "Battlecruiser": (400, 300, 6),
    "Zealot": (100, 0, 2), "Stalker": (125, 50, 2), "Sentry": (50, 100, 2),
    "Adept": (100, 25, 2), "HighTemplar": (50, 150, 2), "DarkTemplar": (125, 125, 2),
    "Archon": (0, 0, 4), "Immortal": (275, 100, 4), "Colossus": (300, 200, 6),
    "Disruptor": (150, 150, 3), "Observer": (25, 75, 1), "WarpPrism": (200, 0, 2),
    "Phoenix": (150, 100, 2), "VoidRay": (250, 150, 4), "Oracle": (150, 150, 3),
    "Tempest": (250, 175, 5), "Carrier": (350, 250, 6), "Mothership": (400, 400, 8),
    "Zergling": (50, 0, 1), "Baneling": (25, 25, 0), "Roach": (75, 25, 2),
    "Ravager": (100, 75, 3), "Hydralisk": (100, 50, 2), "Lurker": (150, 100, 3),
    "Mutalisk": (100, 100, 2), "Corruptor": (150, 100, 3), "BroodLord": (300, 250, 4),
    "SwarmHost": (200, 100, 4), "Infestor": (100, 150, 2), "Viper": (100, 200, 3),
    "Ultralisk": (300, 200, 6), "Queen": (150, 0, 2), "Overlord": (100, 0, 0),
    "Overseer": (50, 50, 0),
    # 建筑
    "CommandCenter": (400, 0, 0), "OrbitalCommand": (150, 0, 0),
    "PlanetaryFortress": (150, 150, 0), "Nexus": (400, 0, 0),
    "Hatchery": (300, 0, 0), "Lair": (150, 100, 0), "Hive": (200, 150, 0),
    "SupplyDepot": (100, 0, 0), "Pylon": (100, 0, 0), "Refinery": (75, 0, 0),
    "Assimilator": (75, 0, 0), "Extractor": (75, 0, 0),
    "Barracks": (150, 0, 0), "Factory": (150, 100, 0), "Starport": (150, 100, 0),
    "Gateway": (150, 0, 0), "CyberneticsCore": (150, 0, 0),
    "RoboticsFacility": (150, 100, 0), "Stargate": (150, 150, 0),
    "SpawningPool": (200, 0, 0), "RoachWarren": (150, 0, 0), "BanelingNest": (100, 50, 0),
    "HydraliskDen": (100, 100, 0), "InfestationPit": (100, 100, 0), "Spire": (200, 200, 0),
    "UltraliskCavern": (150, 200, 0), "NydusNetwork": (150, 200, 0),
    "EngineeringBay": (125, 0, 0), "Armory": (150, 100, 0),
    "TwilightCouncil": (150, 100, 0), "Forge": (150, 0, 0),
    "TemplarArchives": (150, 200, 0), "DarkShrine": (150, 150, 0),
    "FleetBeacon": (200, 150, 0), "RoboticsBay": (200, 200, 0),
    "GhostAcademy": (150, 50, 0), "FusionCore": (150, 150, 0),
    "Bunker": (100, 0, 0), "MissileTurret": (100, 0, 0), "PhotonCannon": (150, 0, 0),
    "ShieldBattery": (100, 0, 0), "SpineCrawler": (100, 0, 0), "SporeCrawler": (75, 0, 0),
    "SensorTower": (125, 50, 0), "EvolutionChamber": (75, 0, 0),
    "BarracksReactor": (50, 50, 0), "FactoryReactor": (50, 50, 0),
    "StarportReactor": (50, 50, 0), "BarracksTechLab": (50, 25, 0),
    "FactoryTechLab": (50, 25, 0), "StarportTechLab": (50, 25, 0),
}

# --------------------------------------------------------------------------- 升级中文名
UPGRADE_NAMES_ZH = {
    "Stimpack": "兴奋剂",
    "ShieldWall": "战斗护盾",
    "PunisherGrenades": "惩罚者榴弹",
    "SiegeTech": "攻城模式",
    "PersonalCloaking": "个人隐形",
    "DrillingClaws": "钻爪",
    "SmartServos": "智能伺服",
    "NeoSteelFrame": "新型钢架",
    "TerranBuildingArmor": "建筑装甲",
    "HiSecAutoTracking": "自动追踪",
    "AdvancedBallistics": "高级弹道学",
    "RavenCorvidReactor": "渡鸦：鸦式反应堆",
    "BansheeCloak": "女妖隐形",
    "BattlecruiserBehemothReactor": "战巡：巨兽反应堆",
    "MedivacCaduceusReactor": "医疗运输机：蛇杖反应堆",
    "LiberatorAGRangeUpgrade": "解放者：射程升级",
    "CycloneRapidFireLaunchers": "旋风：速射发射器",
    "WidowMineDrillingClaws": "寡妇雷：钻爪",
    "Charge": "冲锋",
    "BlinkTech": "闪现",
    "AdeptPiercingAttack": "使徒：穿刺攻击",
    "ResonatingGlaives": "共鸣剑刃",
    "PsionicStorm": "灵能风暴",
    "ExtendedThermalLance": "扩展热能矛",
    "GraviticBoosters": "重力加速器",
    "GraviticDrive": "重力驱动器",
    "GravitonCatapult": "重力弹射器",
    "FluxVanes": "通量叶轮",
    "AnionPulseCrystals": "负离子脉冲水晶",
    "DarkTemplarBlinkUpgrade": "黑暗圣堂：闪现",
    "ObserverGraviticBooster": "观察者：重力加速器",
    "PhoenixAnionPulseCrystals": "凤凰：负离子脉冲水晶",
    "OracleCloakingField": "先知：隐形场",
    "OracleStasisCalibration": "先知：停滞后校准",
    "TempestGroundAttackUpgrade": "暴风舰：对地攻击",
    "CarrierLaunchSpeedUpgrade": "航母：发射速度",
    "VoidRaySpeedUpgrade": "虚空辉光舰：速度",
    "AdeptKillMineRevive": "使徒：幻影重生",
    "MetabolicBoost": "代谢加速",
    "AdrenalGlands": "肾上腺体",
    "CentrifugalHooks": "离心钩",
    "GlialReconstitution": "胶质再生",
    "TunnelingClaws": "掘地之爪",
    "MuscularAugments": "肌肉强化",
    "GroovedSpines": "脊刺强化",
    "NeuralParasite": "神经寄生",
    "PathogenGlands": "病原腺体",
    "ChitinousPlating": "甲壳质板",
    "Burrow": "埋地",
    "AdaptiveTalons": "适应性利爪",
    "Frenzy": "狂暴",
    "AnabolicSynthesis": "同化合成",
    "LurkerRange": "潜伏者：射程",
    "SeismicSpines": "地震脊刺",
    "DiggingClaws": "挖掘之爪",
    "EvolveSaliva": "进化唾液",
    "EvolveGroovedSpines": "进化脊刺",
    "EvolveMuscularAugments": "进化肌肉强化",
    "SunderingBlades": "裂地之刃",
    "ScytheRendingClaws": "镰刀撕裂之爪",
    "WarpGateResearch": "跃迁门研究",
    "TerranVehicleAndShipPlating": "战车与舰船装甲",
    "HighTemplarKhaydarinAmulet": "高阶圣堂：凯达林护符",
    "PhasingArmor": "相位护甲",
    "AdeptResonatingGlaives": "共鸣剑刃",
    "CycloneLockOnDamageUpgrade": "旋风：锁定伤害",
    "RavenRecalibration": "渡鸦：重新校准",
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_LEVELS = re.compile(r"Level(\d)$")

_RACE_ZH = {"terran": "人族", "protoss": "星灵", "zerg": "异虫"}


def pretty(name):
    """Marine -> Marine, SupplyDepot -> Supply Depot"""
    if not name:
        return "?"
    return _CAMEL.sub(" ", name).strip()


def unit_label(name):
    """单位中文名；没有翻译时回退成英文驼峰拆分。"""
    if not name:
        return "?"
    return UNIT_NAMES_ZH.get(name, pretty(name))


_UPGRADE_PARTS = {
    "Terran": "人族", "Protoss": "星灵", "Zerg": "异虫",
    "Infantry": "步兵", "Vehicle": "战车", "Ship": "舰船", "Ground": "地面",
    "Air": "空中", "Melee": "近战", "Missile": "远程",
    "Weapons": "武器", "Armors": "护甲", "Armor": "护甲", "Plating": "装甲",
    "Shields": "护盾", "Shield": "护盾",
}
_UPGRADE_PATTERN = re.compile(
    r"^(?:(Terran|Protoss|Zerg))?([A-Za-z]*?)"
    r"(Weapons|Armors|Armor|Plating|Shields|Shield)Level(\d)$")


def upgrade_label(name):
    """升级的中文名，支持 TerranInfantryWeaponsLevel2 这类批量命名。"""
    if not name:
        return "?"
    if name in UPGRADE_NAMES_ZH:
        return UPGRADE_NAMES_ZH[name]

    match = _UPGRADE_PATTERN.match(name)
    if match:
        race, scope, kind, level = match.groups()
        parts = []
        if race:
            parts.append(_UPGRADE_PARTS.get(race, race))
        for chunk in re.findall(r"[A-Z][a-z]+", scope or ""):
            if chunk == "And":
                parts.append("与")
                continue
            parts.append(_UPGRADE_PARTS.get(chunk, chunk))
        parts.append(_UPGRADE_PARTS.get(kind, kind))
        return "%s %s级" % ("".join(parts), level)

    match = _LEVELS.search(name)
    if match:
        base = name[: match.start()]
        zh = UPGRADE_NAMES_ZH.get(base)
        if zh:
            return "%s %s级" % (zh, match.group(1))

    lowered = name.lower()
    for race_key, race_zh in _RACE_ZH.items():
        if lowered.startswith(race_key):
            return "%s%s" % (race_zh, pretty(name[len(race_key):]))
    return pretty(name)


# --------------------------------------------------------------------------- 分类
def category(name):
    """返回 worker / townhall / structure / army / neutral。"""
    if not name:
        return "neutral"
    if name in WORKER_UNITS:
        return "worker"
    if name in TOWNHALL_UNITS:
        return "townhall"
    if is_noise(name):
        return "neutral"
    if name in STRUCTURE_UNITS:
        return "structure"
    if _is_structure(name):
        return "structure"
    return "army"


_STRUCTURE_HINTS = (
    "Depot", "Refinery", "Assimilator", "Extractor", "Barracks", "Factory",
    "Starport", "Bay", "Core", "Council", "Archives", "Shrine", "Beacon",
    "Facility", "Stargate", "Gateway", "Pylon", "Bunker", "Turret", "Cannon",
    "Battery", "Crawler", "Pool", "Warren", "Nest", "Den", "Pit", "Spire",
    "Cavern", "Network", "Chamber", "Nexus", "Command", "Fortress", "Academy",
    "Armory", "Tower", "Lab", "Reactor", "Gate", "Egg", "Cocoon", "Hive",
    "Lair", "Hatchery",
)


def _is_structure(name):
    return any(hint in name for hint in _STRUCTURE_HINTS)


def cost(name):
    """返回 (矿, 气, 人口)；未知单位返回 None。"""
    return UNIT_COST.get(name)


def is_noise(name):
    if not name:
        return True
    if name in NOISE_UNITS:
        return True
    return name.startswith(NOISE_PREFIXES)


NOISE_PREFIXES = (
    "Beacon", "Spray", "Destructible", "Collapsible", "Unbuildable",
    "MineralField", "Vespene", "SpacePlatform", "RichMineral", "XelNaga",
    "Nuke", "CreepTumor", "Larva", "Egg",
)


def is_army_unit(name):
    return category(name) == "army" and name not in NOISE_UNITS


def stage(name):
    """建造顺序里的分组：eco / prod / tech / defense / army / expansion / upgrade"""
    if name in WORKER_UNITS:
        return "eco"
    if name in TOWNHALL_UNITS:
        return "expansion" if name in ("Nexus", "CommandCenter", "Hatchery") else "tech"
    if name in ECONOMY_STRUCTURES:
        return "eco"
    if name in DEFENSE_UNITS:
        return "defense"
    if name in PRODUCTION_UNITS or name in ADDON_UNITS:
        return "prod"
    if name in TECH_UNITS:
        return "tech"
    if name in STRUCTURE_UNITS:
        return "tech"
    return "army"
