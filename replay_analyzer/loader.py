"""把 .SC2Replay 解析成结构化的 :class:`ParsedReplay`。

这一层只做"解码 + 整理"，不做任何分析结论；所有统计口径放在 metrics.py。
"""

import datetime
import json
import os
import re

from . import protocols, units as unitdata
from .sc2mpq import ReplayArchive


LOOPS_PER_SECOND = 16.0

RACE_BY_ATTRIBUTE = {"Terr": "T", "Prot": "P", "Zerg": "Z", "Rand": "R"}
RACE_BY_LOCALIZED = {
    "terran": "T", "人类": "T", "人族": "T", "テラン": "T", "테란": "T", " terrestre": "T",
    "protoss": "P", "星灵": "P", "神族": "P", "プロトス": "P", "프로토스": "P",
    "zerg": "Z", "异虫": "Z", "虫族": "Z", "ザーグ": "Z", "저그": "Z",
    "random": "R", "随机": "R",
}
RACE_NAME_ZH = {"T": "人族", "P": "星灵", "Z": "异虫", "R": "随机"}
RACE_NAME_EN = {"T": "Terran", "P": "Protoss", "Z": "Zerg", "R": "Random"}

RESULT_MAP = {0: "unknown", 1: "win", 2: "loss", 3: "tie"}

GAME_SPEED = {0: "Slower", 1: "Slow", 2: "Normal", 3: "Fast", 4: "Faster"}
GAME_SPEED_ZH = {0: "更慢", 1: "慢", 2: "普通", 3: "快", 4: "更快"}

_TOON_IN_PATH = re.compile(r"(\d)-S2-(\d+)-(\d+)")


def _as_text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if value is None:
        return ""
    return value


def _color(value):
    if not value:
        return (128, 128, 128)
    return (value.get("m_r", 128), value.get("m_g", 128), value.get("m_b", 128))


class Player(object):
    def __init__(self, index):
        self.index = index          # 0-based，等于 details 里 m_playerList 的下标
        self.user_id = index        # 游戏事件里的 m_userId
        self.player_id = index + 1  # tracker 事件里的 playerId（1 起）
        self.name = ""
        self.race = "R"
        self.race_localized = ""
        self.team = 0
        self.result = "unknown"
        self.color = (128, 128, 128)
        self.toon = ""
        self.is_local = False
        self.is_ai = False
        self.difficulty = ""

    # ------------------------------------------------------------------ 展示
    @property
    def race_name(self):
        return RACE_NAME_ZH.get(self.race, "?")

    @property
    def race_name_en(self):
        return RACE_NAME_EN.get(self.race, "?")

    @property
    def result_zh(self):
        return {"win": "胜", "loss": "负", "tie": "平", "unknown": "未知"}[self.result]

    @property
    def color_hex(self):
        return "#%02x%02x%02x" % self.color

    def to_dict(self):
        return {
            "index": self.index,
            "user_id": self.user_id,
            "player_id": self.player_id,
            "name": self.name,
            "race": self.race,
            "race_name": self.race_name,
            "race_name_en": self.race_name_en,
            "team": self.team,
            "result": self.result,
            "result_zh": self.result_zh,
            "color": list(self.color),
            "color_hex": self.color_hex,
            "toon": self.toon,
            "is_local": self.is_local,
            "is_ai": self.is_ai,
            "difficulty": self.difficulty,
        }


class UnitRecord(object):
    """一个单位从出现到死亡的完整记录。"""

    __slots__ = ("tag", "player_id", "name", "init_loop", "born_loop", "done_loop",
                 "died_loop", "killer_player_id", "x", "y", "is_structure", "morphs")

    def __init__(self, tag, player_id, name):
        self.tag = tag
        self.player_id = player_id
        self.name = name
        self.init_loop = None
        self.born_loop = None
        self.done_loop = None
        self.died_loop = None
        self.killer_player_id = 0
        self.x = None
        self.y = None
        self.is_structure = False
        self.morphs = []

    @property
    def start_loop(self):
        return self.init_loop if self.init_loop is not None else self.born_loop

    @property
    def end_name(self):
        return self.morphs[-1][1] if self.morphs else self.name

    @property
    def cost(self):
        return unitdata.cost(self.name)


class ParsedReplay(object):
    """解析结果：元信息 + 玩家 + 原始事件流。"""

    def __init__(self):
        self.path = ""
        self.file_size = 0
        self.version = {}
        self.protocol_build = 0
        self.protocol_note = ""
        self.map_name = ""
        self.map_author = ""
        self.is_ladder = False
        self.game_speed = 4
        self.game_speed_name = "Faster"
        self.duration_loops = 0
        self.replay_time = None
        self.players = []
        self.stats = {}          # player_id -> [ {loop, ...} ]
        self.unit_records = []
        self.build_events = []   # 排序后的建造顺序事件
        self.upgrades = []
        self.chat = []
        self.game_actions = []   # (loop, user_id, event_name)
        self.actions = []        # 结构化的操作记录，供 micro.py 分析多线/视角/编队
        self.unit_positions = {}  # 单位 tagIndex -> [(loop, x, y), ...]（位置采样）
        self.game_event_counts = {}
        self.raw_details = {}
        self.raw_init = {}
        self.attributes = {}
        self.gamemetadata = {}

    # ------------------------------------------------------------------ 便捷属性
    @property
    def duration_seconds(self):
        return self.duration_loops / LOOPS_PER_SECOND

    def player_by_player_id(self, player_id):
        for player in self.players:
            if player.player_id == player_id:
                return player
        return None

    def player_by_user_id(self, user_id):
        for player in self.players:
            if player.user_id == user_id:
                return player
        return None

    def to_dict(self):
        return {
            "path": self.path,
            "map": self.map_name,
            "map_author": self.map_author,
            "duration_seconds": round(self.duration_seconds, 2),
            "game_speed": self.game_speed_name,
            "version": self.version,
            "protocol_build": self.protocol_build,
            "protocol_note": self.protocol_note,
            "players": [p.to_dict() for p in self.players],
        }


# --------------------------------------------------------------------------- 解析
def load_replay(path, decode_events=True):
    """解析 replay 文件，返回 :class:`ParsedReplay`。"""
    parsed = ParsedReplay()
    parsed.path = os.path.abspath(path)
    parsed.file_size = os.path.getsize(path)

    with ReplayArchive(path) as archive:
        meta = archive.header.get("user_data_header") or {}
        from s2protocol.versions import latest  # noqa: E402  (延迟导入，保证 vendor 已就绪)

        header = latest().decode_replay_header(meta.get("content") or b"")
        parsed.version = _version_dict(header.get("m_version", {}))
        parsed.duration_loops = header.get("m_elapsedGameLoops", 0)

        build = parsed.version.get("build") or 0
        protocol, used_build, note = protocols.resolve(build)
        parsed.protocol_build = used_build
        parsed.protocol_note = note

        details = protocol.decode_replay_details(archive.require("replay.details"))
        parsed.raw_details = details
        _fill_details(parsed, details)

        try:
            init = protocol.decode_replay_initdata(archive.require("replay.initData"))
        except Exception:
            init = protocol.decode_replay_initdata(archive.require("replay.initData.backup"))
        parsed.raw_init = init

        try:
            raw = archive.read("replay.gamemetadata.json")
            if raw:
                parsed.gamemetadata = json.loads(_as_text(raw))
        except Exception:
            parsed.gamemetadata = {}

        attributes = {}
        try:
            attributes = protocol.decode_replay_attributes_events(
                archive.require("replay.attributes.events"))
        except Exception:
            attributes = {}
        parsed.attributes = attributes
        _fill_players_from_attributes(parsed, attributes)

        _read_tracker_events(parsed, protocol, archive)
        if decode_events:
            _read_game_events(parsed, protocol, archive)
            _read_chat(parsed, protocol, archive)

    _finalize_players(parsed)
    _build_build_events(parsed)
    return parsed


def _version_dict(version):
    return {
        "flags": version.get("m_flags"),
        "major": version.get("m_major"),
        "minor": version.get("m_minor"),
        "revision": version.get("m_revision"),
        "build": version.get("m_build"),
        "base_build": version.get("m_baseBuild"),
    }


def _fill_details(parsed, details):
    parsed.map_name = _as_text(details.get("m_title")) or "(未知地图)"
    parsed.is_ladder = bool(details.get("m_isBlizzardMap"))
    parsed.game_speed = details.get("m_gameSpeed", 4)
    parsed.game_speed_name = GAME_SPEED.get(parsed.game_speed, "Faster")
    parsed.map_author = ""
    try:
        lobby = parsed.raw_init.get("m_syncLobbyState", {})
        parsed.map_author = _as_text(lobby.get("m_gameDescription", {}).get("m_mapAuthorName", ""))
    except Exception:
        pass

    utc = details.get("m_timeUTC")
    offset = details.get("m_timeLocalOffset") or 0
    if utc:
        try:
            epoch_seconds = (utc - 116444736000000000) / 10000000.0  # 1601-01-01 -> 1970
            parsed.replay_time = datetime.datetime.fromtimestamp(
                epoch_seconds + (offset / 10000000.0), datetime.timezone.utc)
        except Exception:
            parsed.replay_time = None

    for index, raw in enumerate(details.get("m_playerList", [])):
        player = Player(index)
        player.name = _as_text(raw.get("m_name")) or ("Player %d" % (index + 1))
        player.race_localized = _as_text(raw.get("m_race"))
        player.team = raw.get("m_teamId", 0)
        player.result = RESULT_MAP.get(raw.get("m_result"), "unknown")
        player.color = _color(raw.get("m_color"))
        player.is_ai = raw.get("m_control") == 3
        player.difficulty = _as_text(raw.get("m_difficulty"))
        toon = raw.get("m_toon") or {}
        if toon:
            player.toon = "%d-S2-%d-%d" % (toon.get("m_region", 0),
                                           toon.get("m_realm", 0),
                                           toon.get("m_id", 0))
        parsed.players.append(player)

    _mark_local_player(parsed)


def _mark_local_player(parsed):
    """路径里带着 "5-S2-1-8354391" 这样的 toon，能对上就是本机玩家。"""
    match = _TOON_IN_PATH.search(os.path.abspath(parsed.path))
    if not match:
        return
    toon = "%s-S2-%s-%s" % match.groups()
    for player in parsed.players:
        if player.toon == toon:
            player.is_local = True
            return


def _fill_players_from_attributes(parsed, attributes):
    """attributes.events 里的 3001 属性是语言无关的种族标记（Terr/Prot/Zerg）。"""
    scopes = (attributes or {}).get("scopes", {})
    for player in parsed.players:
        scope = scopes.get(str(player.player_id)) or {}
        race = ""
        entries = scope.get("3001") or scope.get(3001) or []
        if entries:
            race = RACE_BY_ATTRIBUTE.get(_as_text(entries[0].get("value")), "")
        if not race:
            lowered = player.race_localized.strip().lower()
            race = RACE_BY_LOCALIZED.get(lowered, "")
        if not race:
            for key, value in RACE_BY_LOCALIZED.items():
                if key in lowered:
                    race = value
                    break
        player.race = race or "R"


def _read_tracker_events(parsed, protocol, archive):
    raw = archive.read("replay.tracker.events")
    if raw is None:
        return

    by_id = {player.player_id: player for player in parsed.players}
    units_by_tag = {}
    upgrade_seen = []

    for event in protocol.decode_replay_tracker_events(raw):
        name = event.get("_event", "")
        loop = event.get("_gameloop", 0)

        if name.endswith("SPlayerStatsEvent"):
            player_id = event.get("m_playerId")
            stats = _stats_dict(event.get("m_stats", {}), loop)
            parsed.stats.setdefault(player_id, []).append(stats)

        elif name.endswith("SPlayerSetupEvent"):
            player_id = event.get("m_playerId")
            user_id = event.get("m_userId")
            player = by_id.get(player_id)
            if player is not None and user_id is not None:
                player.user_id = user_id

        elif name.endswith("SUnitInitEvent") or name.endswith("SUnitBornEvent"):
            player_id = event.get("m_controlPlayerId") or 0
            tag = (event.get("m_unitTagIndex"), event.get("m_unitTagRecycle"))
            unit_name = _as_text(event.get("m_unitTypeName"))
            record = units_by_tag.get(tag)
            if record is None:
                record = UnitRecord(tag, player_id, unit_name)
                units_by_tag[tag] = record
                parsed.unit_records.append(record)
            if name.endswith("SUnitInitEvent"):
                if record.init_loop is None:
                    record.init_loop = loop
                record.name = unit_name or record.name
            else:
                if record.born_loop is None:
                    record.born_loop = loop
                if not record.name:
                    record.name = unit_name
            if record.x is None:
                record.x = event.get("m_x")
                record.y = event.get("m_y")

        elif name.endswith("SUnitDoneEvent"):
            tag = (event.get("m_unitTagIndex"), event.get("m_unitTagRecycle"))
            record = units_by_tag.get(tag)
            if record is not None:
                record.done_loop = loop

        elif name.endswith("SUnitTypeChangeEvent"):
            tag = (event.get("m_unitTagIndex"), event.get("m_unitTagRecycle"))
            record = units_by_tag.get(tag)
            morph = _as_text(event.get("m_unitTypeName"))
            if record is not None and morph:
                record.morphs.append((loop, morph))

        elif name.endswith("SUnitDiedEvent"):
            tag = (event.get("m_unitTagIndex"), event.get("m_unitTagRecycle"))
            record = units_by_tag.get(tag)
            if record is not None:
                record.died_loop = loop
                record.killer_player_id = event.get("m_killerPlayerId") or 0

        elif name.endswith("SUnitPositionsEvent"):
            items = event.get("m_items") or []
            index = event.get("m_firstUnitIndex") or 0
            store = parsed.unit_positions
            for position in range(0, len(items) - 2, 3):
                index += items[position]
                store.setdefault(index, []).append(
                    (loop, items[position + 1], items[position + 2]))

        elif name.endswith("SUpgradeEvent"):
            player_id = event.get("m_playerId")
            upgrade = _as_text(event.get("m_upgradeTypeName"))
            count = event.get("m_count") or 1
            if upgrade.startswith(("Reward", "Spray", "MasteryBundle")):
                continue  # 装饰性奖励不进入建造顺序
            upgrade_seen.append({
                "loop": loop,
                "player_id": player_id,
                "name": upgrade,
                "label": unitdata.upgrade_label(upgrade),
                "kind": "upgrade",
                "count": count,
                "stage": "upgrade",
            })

    # 判定"是建筑还是兵"只看单位类型：
    # 星灵跃迁门的兵种同样用 UnitInit/UnitDone 事件，不能据此当建筑。
    for record in parsed.unit_records:
        record.is_structure = unitdata.category(record.name) in ("structure", "townhall")

    parsed.upgrades = upgrade_seen


def _stats_dict(stats, loop):
    # 资源账本：minerals/vespene 是"手上剩的"，Used/Lost 是按用途分类的**累计已花**。
    # 三项加起来就是"这局到目前为止一共进账多少"，是估算收入曲线的地基。
    used = {}
    for kind in ("Army", "Economy", "Technology"):
        used["minerals_used_" + kind.lower()] = stats.get(
            "m_scoreValueMineralsUsedCurrent" + kind, 0)
        used["vespene_used_" + kind.lower()] = stats.get(
            "m_scoreValueVespeneUsedCurrent" + kind, 0)
        used["minerals_progress_" + kind.lower()] = stats.get(
            "m_scoreValueMineralsUsedInProgress" + kind, 0)
        used["vespene_progress_" + kind.lower()] = stats.get(
            "m_scoreValueVespeneUsedInProgress" + kind, 0)
        used["minerals_lost_" + kind.lower()] = stats.get(
            "m_scoreValueMineralsLost" + kind, 0)
        used["vespene_lost_" + kind.lower()] = stats.get(
            "m_scoreValueVespeneLost" + kind, 0)

    return {
        "loop": loop,
        "minerals": stats.get("m_scoreValueMineralsCurrent", 0),
        "vespene": stats.get("m_scoreValueVespeneCurrent", 0),
        "mineral_rate": stats.get("m_scoreValueMineralsCollectionRate", 0),
        "vespene_rate": stats.get("m_scoreValueVespeneCollectionRate", 0),
        "workers": stats.get("m_scoreValueWorkersActiveCount", 0),
        "army_value": stats.get("m_scoreValueMineralsUsedCurrentArmy", 0)
                     + stats.get("m_scoreValueVespeneUsedCurrentArmy", 0),
        "army_minerals": stats.get("m_scoreValueMineralsUsedCurrentArmy", 0),
        "army_vespene": stats.get("m_scoreValueVespeneUsedCurrentArmy", 0),
        "economy_value": stats.get("m_scoreValueMineralsUsedCurrentEconomy", 0)
                        + stats.get("m_scoreValueVespeneUsedCurrentEconomy", 0),
        "tech_value": stats.get("m_scoreValueMineralsUsedCurrentTechnology", 0)
                     + stats.get("m_scoreValueVespeneUsedCurrentTechnology", 0),
        "army_in_progress": stats.get("m_scoreValueMineralsUsedInProgressArmy", 0),
        "lost_army": stats.get("m_scoreValueMineralsLostArmy", 0)
                    + stats.get("m_scoreValueVespeneLostArmy", 0),
        "lost_economy": stats.get("m_scoreValueMineralsLostEconomy", 0)
                       + stats.get("m_scoreValueVespeneLostEconomy", 0),
        "lost_tech": stats.get("m_scoreValueMineralsLostTechnology", 0)
                    + stats.get("m_scoreValueVespeneLostTechnology", 0),
        "killed_army": stats.get("m_scoreValueMineralsKilledArmy", 0)
                      + stats.get("m_scoreValueVespeneKilledArmy", 0),
        "killed_economy": stats.get("m_scoreValueMineralsKilledEconomy", 0)
                         + stats.get("m_scoreValueVespeneKilledEconomy", 0),
        "killed_tech": stats.get("m_scoreValueMineralsKilledTechnology", 0)
                      + stats.get("m_scoreValueVespeneKilledTechnology", 0),
        "food_used": stats.get("m_scoreValueFoodUsed", 0) / 4096.0,
        "food_made": stats.get("m_scoreValueFoodMade", 0) / 4096.0,
        "ledger": used,
    }


# 用户操作类事件（用于 APM）
CAMERA_EVENTS = {"NNet.Game.SCameraUpdateEvent", "NNet.Game.SCameraSaveEvent",
                 "NNet.Game.SCameraFollowEvent"}
IGNORED_EVENTS = {
    "NNet.Game.SUserFinishedLoadingSyncEvent", "NNet.Game.SSetSyncLoadingTimeEvent",
    "NNet.Game.SSetSyncPlayingTimeEvent", "NNet.Game.SSyncLoadingProgressEvent",
    "NNet.Game.SDecrementGameTimeRemainingEvent", "NNet.Game.SPingEvent",
    "NNet.Game.SCommandManagerStateEvent", "NNet.Game.SUserOptionsEvent",
    "NNet.Game.SGameUserLeaveEvent", "NNet.Game.SGameUserJoinEvent",
    "NNet.Game.STriggerSoundLengthQueryEvent", "NNet.Game.STriggerSoundLengthSyncEvent",
    "NNet.Game.STriggerSoundOffsetEvent", "NNet.Game.STriggerTransmissionOffsetEvent",
    "NNet.Game.STriggerTransmissionCompleteEvent", "NNet.Game.STriggerMovieFunctionEvent",
    "NNet.Game.STriggerSkippedEvent", "NNet.Game.STriggerSoundtrackDoneEvent",
    "NNet.Game.STriggerPingEvent", "NNet.Game.STriggerDialogControlEvent",
    "NNet.Game.STriggerCinematicIndexUpdateEvent", "NNet.Game.STriggerReplySelectedEvent",
    "NNet.Game.STriggerSceneFadeEvent", "NNet.Game.STriggerFrameFadeEvent",
    "NNet.Game.STriggerCursorEvent", "NNet.Game.STriggerButtonPressedEvent",
    "NNet.Game.STriggerAnimLengthQueryByNameEvent",
    "NNet.Game.STriggerAnimLengthQueryByPropsEvent",
    "NNet.Game.STriggerAnimOffsetEvent", "NNet.Game.STriggerAssetDoneEvent",
    "NNet.Game.STriggerAssetGroupDoneEvent", "NNet.Game.STriggerTextureUpdateEvent",
    "NNet.Game.STriggerAbortMissionEvent", "NNet.Game.STriggerShopPurchaseEvent",
    "NNet.Game.SCameraUpdateEvent", "NNet.Game.SCameraSaveEvent",
    "NNet.Game.SResourceTransferEvent_temp",
}
COMMAND_EVENTS = {
    "NNet.Game.SCmdEvent",
    "NNet.Game.SCmdUpdateTargetPointEvent",
    "NNet.Game.SCmdUpdateTargetUnitEvent",
    "NNet.Game.SSelectionDeltaEvent",
    "NNet.Game.SSelectionEvent",
    "NNet.Game.SControlGroupUpdateEvent",
    "NNet.Game.SResourceTransferEvent",
}


def _read_game_events(parsed, protocol, archive):
    raw = archive.read("replay.game.events")
    if raw is None:
        return
    counts = parsed.game_event_counts
    actions = parsed.game_actions
    for event in protocol.decode_replay_game_events(raw):
        name = event.get("_event", "")
        loop = event.get("_gameloop", 0)
        counts[name] = counts.get(name, 0) + 1
        user = event.get("_userid") or {}
        user_id = user.get("m_userId")
        if user_id is None:
            continue
        actions.append((loop, user_id, name))
        record = _micro_record(name, loop, user_id, event)
        if record:
            parsed.actions.append(record)


# 操作细节分析只关心这几类事件
_MICRO_KINDS = {
    "NNet.Game.SCameraUpdateEvent": "cam",
    "NNet.Game.SCameraSaveEvent": "cam",
    "NNet.Game.SSelectionDeltaEvent": "sel",
    "NNet.Game.SSelectionEvent": "sel",
    "NNet.Game.SControlGroupUpdateEvent": "cg",
    "NNet.Game.SCmdEvent": "cmd",
    "NNet.Game.SCmdUpdateTargetPointEvent": "cmdp",
    "NNet.Game.SCmdUpdateTargetUnitEvent": "cmdu",
}

# 相机坐标是 1/256 格，指令坐标是 1/4096 格（暴雪的两种定点数）
CAMERA_SCALE = 256.0
POINT_SCALE = 4096.0


def _micro_record(name, loop, user_id, event):
    kind = _MICRO_KINDS.get(name)
    if kind is None:
        return None
    record = {"l": loop, "u": user_id, "k": kind}

    if kind == "cam":
        target = event.get("m_target") or {}
        if target.get("x") is not None:
            record["x"] = target["x"] / CAMERA_SCALE
            record["y"] = target["y"] / CAMERA_SCALE
    elif kind == "cmdp":
        target = event.get("m_target") or {}
        if target.get("x") is not None:
            record["x"] = target["x"] / POINT_SCALE
            record["y"] = target["y"] / POINT_SCALE
    elif kind == "cmdu":
        point = (event.get("m_target") or {}).get("m_snapshotPoint") or {}
        if point.get("x") is not None:
            record["x"] = point["x"] / POINT_SCALE
            record["y"] = point["y"] / POINT_SCALE
    elif kind == "cmd":
        data = event.get("m_data") or {}
        point = data.get("TargetPoint") if isinstance(data, dict) else None
        if point:
            record["x"] = point.get("x", 0) / POINT_SCALE
            record["y"] = point.get("y", 0) / POINT_SCALE
        else:
            unit = data.get("TargetUnit") if isinstance(data, dict) else None
            if unit:
                inner = unit.get("m_snapshotPoint") or {}
                if inner.get("x") is not None:
                    record["x"] = inner["x"] / POINT_SCALE
                    record["y"] = inner["y"] / POINT_SCALE
        ability = event.get("m_abil") or {}
        if ability.get("m_abilLink"):
            record["ab"] = ability["m_abilLink"]
    elif kind == "sel":
        group = event.get("m_controlGroupId")
        if group is not None:
            record["g"] = group
        delta = event.get("m_delta") or {}
        if isinstance(delta, dict):
            tags = delta.get("m_addUnitTags") or []
            record["n"] = len(tags)
            subgroups = delta.get("m_addSubgroups") or []
            if subgroups:
                record["lk"] = subgroups[0].get("m_unitLink")
    elif kind == "cg":
        record["g"] = event.get("m_controlGroupIndex")
        record["up"] = event.get("m_controlGroupUpdate")
    return record


def _read_chat(parsed, protocol, archive):
    raw = archive.read("replay.message.events")
    if raw is None:
        return
    for event in protocol.decode_replay_message_events(raw):
        name = event.get("_event", "")
        if not name.endswith("SChatMessageEvent"):
            continue
        user = event.get("_userid") or {}
        parsed.chat.append({
            "loop": event.get("_gameloop", 0),
            "user_id": user.get("m_userId"),
            "text": _as_text(event.get("m_string")),
        })


# --------------------------------------------------------------------------- 收尾
def _finalize_players(parsed):
    """补齐种族推断、结果推断，并给每个玩家附上 APM 等基础指标。"""
    for player in parsed.players:
        if player.race in ("R", ""):
            player.race = _infer_race(parsed, player.player_id)

    # 结果缺失（例如自定义局）时，用"最后一个单位死亡时间"粗略判断
    known = [p.result for p in parsed.players]
    if known.count("win") + known.count("loss") != len(parsed.players):
        last_alive = {}
        for record in parsed.unit_records:
            if record.player_id and record.died_loop is None and record.is_structure:
                last_alive[record.player_id] = True
        for player in parsed.players:
            if player.result == "unknown":
                player.result = "loss" if not last_alive.get(player.player_id) else "win"


def _infer_race(parsed, player_id):
    counts = {"T": 0, "P": 0, "Z": 0}
    signature = {
        "T": {"SCV", "Marine", "Barracks", "CommandCenter", "SupplyDepot"},
        "P": {"Probe", "Zealot", "Gateway", "Nexus", "Pylon"},
        "Z": {"Drone", "Zergling", "SpawningPool", "Hatchery", "Overlord"},
    }
    for record in parsed.unit_records:
        if record.player_id != player_id:
            continue
        for race, names in signature.items():
            if record.name in names:
                counts[race] += 1
    best = max(counts, key=counts.get)
    return best if counts[best] else "R"


def _build_build_events(parsed):
    """把所有"建造/生产/升级"整理成统一的时间线事件。"""
    events = []
    for record in parsed.unit_records:
        if not record.player_id:
            continue
        name = record.name
        if unitdata.is_noise(name):
            continue
        start = record.start_loop
        if start is None:
            continue
        events.append({
            "loop": start,
            "player_id": record.player_id,
            "name": name,
            "label": unitdata.unit_label(name),
            "kind": "structure" if record.is_structure else "unit",
            "stage": unitdata.stage(name),
            "done_loop": record.done_loop,
        })
        for morph_loop, morph in record.morphs:
            if unitdata.is_noise(morph):
                continue
            events.append({
                "loop": morph_loop,
                "player_id": record.player_id,
                "name": morph,
                "label": unitdata.unit_label(morph),
                "kind": "morph",
                "stage": unitdata.stage(morph),
                "done_loop": None,
            })
    events.extend(parsed.upgrades)
    events.sort(key=lambda item: (item["loop"], item["player_id"]))
    parsed.build_events = events
