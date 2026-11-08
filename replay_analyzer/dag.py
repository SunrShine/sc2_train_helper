"""BO 转移 DAG：把每局的段序列拼成一张转移图，并给每条转移标"是看到才变，还是到点就变"。

这一层要回答的问题只有一个
--------------------------
**我是主动执行，还是被动反应？** 把 bo.py 分出来的段按 ``(phase, signature)`` 聚成节点，
段之间的先后关系聚成边；再看每条边的起点段"开始时"往前 20 秒内，我方有没有刚拿到新的
信息（侦察到对方、看见对方主力、看见科技建筑……）。有就是 ``scout:<type>``，没有就是
``time``。两类边的比例就是"主动/被动"的直接量化。

诚实边界（报告里必须照抄，不许淡化）
------------------------------------
1. **信息事件是几何推断，不是真值。** SC2 replay 里没有"玩家当时知道什么"这个字段，
   这里用 :mod:`replay_analyzer.fog` 的视野圆并集反推"谁看得见谁"。看得见 ≠ 看懂了，
   也 ≠ 据此改了打法。
2. **位置采样从 4 分半才开始。** 实测 tracker 的 ``SUnitPositionsEvent`` 最早出现在
   loop 4320 = **270 秒**（2022 与 2026 build 都一样），而且同一个单位中位 45 秒才给
   一个点（90 分位 270 秒）。所以 ``t < 270s`` 的侦察（最典型的 SCV 探路）**完全算不出来**，
   算出来的事件时间精度也只有 ±20 秒左右。
3. **地形遮挡没算。** 高地/悬崖/岩石挡视线在 replay 里没有数据，这里当平地处理，
   所以"隔着悬崖看见"会被高估。
4. **隐形/潜地/雷达（scan）不在几何模型里**，所以 ``scan`` 事件没有实现——
   ``condition`` 里该是 ``none`` 的地方就写 ``none``，不瞎猜。

输入输出严格按 ``docs/BO数据契约.md`` 第三、四节。

关于"段的开始时刻"
------------------
契约里段的 ``t0`` 是按时间表切的（0/180/360/600），如果直接拿它当转移时刻，那么所有
转移都恰好卡在整点上，``condition`` 会退化成"看 3:00 前 20 秒有没有看到东西"。
所以这里用**段内第一条实际建造/训练事件的时刻**当锚点（``anchor``，没有 steps 时退回
``t0``）：它才是"真的开始切过去"的那一秒。条件窗口和滞后都按锚点算。
"""

import bisect
import datetime
import math

from . import fog
from . import sight
from . import units as unitdata
from .loader import LOOPS_PER_SECOND

# --------------------------------------------------------------------------- 常量

PHASES = ("opening", "early", "mid", "late")

# BO 签名的封闭词表（Lead 与 bo 敲定）——这是**整局**的开局签名（GameBO.signature）。
# DAG 节点用的是**段**签名（bo 的 token 链，如 rax-gas-cc），两者不是一回事：
# 段签名没法用这 7 个值概括，所以这里把它当"已知标签集"来校验和展示。
SIGNATURES = ("1-rax-fast-expand", "2-rax-fast-expand", "1-1-1", "2-1-1",
              "3-rax", "mech", "unknown")

# 段签名允许出现的 token（bo._SIG_TOKEN 的值 + 兜底标签）。出现不认识的 token 时，
# 节点保留原样但打上 unknown_token 标记，报告里能查到。
SIGNATURE_TOKENS = frozenset((
    "rax", "fac", "sp", "gate", "robo", "sg", "gas", "cc", "nexus", "hatch",
    "reac", "lab", "ebay", "armory", "core", "twilight", "roboBay", "archives",
    "shrine", "forge", "cannon", "battery", "bunker", "turret", "pool", "bane",
    "roachWarren", "evo", "hydra", "spire", "infest", "ultra", "nydus",
    # 兜底标签
    "opening", "farm", "army", "bio", "mech", "air", "mixed", "unknown", "plain",
))

# DAG 节点签名 = bo 段签名**粗化**之后的结果，理由见 coarsen_signature：
# addon（反应堆/科技实验室）和防御建筑只是细节，去掉之后开局/前期的形状才收得住。
NODE_DROP_TOKENS = frozenset((
    "reac", "lab", "bunker", "turret", "cannon", "battery", "spine", "spore",
))
NODE_TOKEN_CAP = 2

# 位置采样可信阈值：单位在某秒的位置，只有在距最近采样不超过这个秒数时才采用。
# 实测同一单位相邻采样的中位间隔是 45 秒、90 分位 270 秒，取 45 秒就是"只信局部"。
POS_TRUST_GAP = 45.0

#: 实测：tracker 第一条 SUnitPositionsEvent 在 loop 4320 = 270 秒（16 loops/s）。
POSITION_SAMPLING_START = 270.0

SCAN_STEP = 1.0            # 视野扫描时间粒度（秒）
DEFAULT_MAX_SECONDS = 2400.0

# 触发条件窗口：段锚点往前这么多秒内有 InfoEvent -> scout:<type>
EVENT_WINDOW = 20.0
#: 敏感性：窗口放大后侦察占比会怎么变（20 秒比采样精度还小，必须让人看到这个尺度）
EVENT_WINDOWS = (20.0, 40.0, 60.0)

# army_seen 的门槛
ARMY_SEEN_UNITS = 5
ARMY_SEEN_VALUE = 500

# 主基地：所有能当"家"的建筑
TOWNHALL_NAMES = ("CommandCenter", "CommandCenterFlying", "OrbitalCommand",
                  "OrbitalCommandFlying", "PlanetaryFortress",
                  "Nexus", "Hatchery", "Lair", "Hive")

# "科技建筑"：能说明对方走什么流派的楼。
# 明确排除基础产能（Barracks / Gateway / SpawningPool）和纯防御（Bunker/Turret/Cannon）。
TECH_BUILDINGS = frozenset({
    # 人族
    "Factory", "Starport", "EngineeringBay", "Armory", "GhostAcademy", "FusionCore",
    # 星灵
    "CyberneticsCore", "TwilightCouncil", "TemplarArchives", "DarkShrine",
    "RoboticsFacility", "RoboticsBay", "Stargate", "FleetBeacon", "Forge",
    # 异虫
    "Lair", "Hive", "RoachWarren", "BanelingNest", "HydraliskDen", "InfestationPit",
    "Spire", "GreaterSpire", "UltraliskCavern", "NydusNetwork", "EvolutionChamber",
})

ARMY_CATEGORY = "army"
STRUCTURE_CATEGORIES = ("structure", "townhall")


# --------------------------------------------------------------------------- 小工具

def _alive(record, seconds):
    start = record.start_loop
    if start is not None and start / LOOPS_PER_SECOND > seconds:
        return False
    if record.died_loop is not None and record.died_loop / LOOPS_PER_SECOND <= seconds:
        return False
    return True


def _interp(track, seconds, trust=POS_TRUST_GAP):
    """稀疏采样插值，返回 (x, y, 最近采样距离, 时间误差尺度)。

    ``最近采样距离`` 用来判断这个位置可不可信；``时间误差尺度`` 是这段局部采样的
    分辨率（把前后采样夹到 ``±trust`` 秒内取一半），也就是"这一秒的位置能有多不可靠"。
    """
    if not track:
        return None, None, float("inf"), float("inf")
    if seconds <= track[0][0]:
        gap = track[0][0] - seconds
        return track[0][1], track[0][2], gap, 0.5 * min(gap, trust)
    if seconds >= track[-1][0]:
        gap = seconds - track[-1][0]
        return track[-1][1], track[-1][2], gap, 0.5 * min(gap, trust)
    index = bisect.bisect_left(track, (seconds,))
    prev = track[index - 1]
    nxt = track[index]
    nearest = min(seconds - prev[0], nxt[0] - seconds)
    low = max(prev[0], seconds - trust)
    high = min(nxt[0], seconds + trust)
    half = 0.5 * max(0.0, high - low)
    span = nxt[0] - prev[0]
    if span <= 0:
        return prev[1], prev[2], nearest, half
    ratio = (seconds - prev[0]) / span
    return (prev[1] + (nxt[1] - prev[1]) * ratio,
            prev[2] + (nxt[2] - prev[2]) * ratio, nearest, half)


def _point_at(parsed, tag_index, seconds, trust=POS_TRUST_GAP):
    track = parsed.unit_positions.get(tag_index)
    if not track:
        return None
    track = [(loop / LOOPS_PER_SECOND, x, y) for loop, x, y in track]
    return _interp(track, seconds, trust)


# --------------------------------------------------------------------------- 主基地

def townhalls(parsed, player_id):
    """该玩家的所有"家"（按出现时间排序）。"""
    found = [u for u in parsed.unit_records
             if u.player_id == player_id
             and u.name in TOWNHALL_NAMES and u.x is not None]
    found.sort(key=lambda u: (u.start_loop if u.start_loop is not None else 0))
    return found


def home_base(parsed, player_id):
    """主基地坐标。没有就返回 None（这时所有依赖它的信息事件都不标）。"""
    for record in townhalls(parsed, player_id):
        return float(record.x), float(record.y)
    return None


def first_army_tag(parsed, player_id):
    """该玩家的第一个非农民单位（按出现时间）。找不到返回 None。"""
    best = None
    for record in parsed.unit_records:
        if record.player_id != player_id:
            continue
        if unitdata.category(record.name) != ARMY_CATEGORY:
            continue
        start = record.start_loop
        if start is None:
            continue
        if best is None or start < best[0]:
            best = (start, record.tag)
    return best[1] if best else None


# --------------------------------------------------------------------------- 人的解析

def resolve_player(parsed, player):
    """把 pid / Player / dict 统一成一个 Player。"""
    if player is None:
        raise ValueError("info_events 需要 player")
    if hasattr(player, "player_id"):
        return player
    if isinstance(player, dict):
        pid = player.get("pid", player.get("player_id"))
        found = parsed.player_by_player_id(pid)
        if found is None:
            raise ValueError("找不到 pid=%r 的玩家" % (pid,))
        return found
    found = parsed.player_by_player_id(int(player))
    if found is None:
        raise ValueError("找不到 pid=%r 的玩家" % (player,))
    return found


def opponent_of(parsed, player):
    """对手：优先取不同队伍的非 AI 玩家。"""
    candidates = [p for p in parsed.players
                  if p.player_id != player.player_id and not p.is_ai]
    if not candidates:
        candidates = [p for p in parsed.players if p.player_id != player.player_id]
    if not candidates:
        return None
    same_team = [p for p in candidates if p.team != player.team]
    return (same_team or candidates)[0]


# --------------------------------------------------------------------------- 视野模型

class _Vision(object):
    """只在"位置可信"的单位上算视野。

    fog.Model 会无条件插值，两个采样点隔 180 秒时它能画出一条横穿全图的假直线。
    这里加一道闸：离最近采样超过 ``trust_gap`` 的单位直接不参与视野计算。
    建筑不倒，用出生坐标当位置（精度 0）。
    """

    def __init__(self, parsed, model=None, trust_gap=POS_TRUST_GAP):
        self.parsed = parsed
        self.model = model or fog.Model(parsed, step=1.0)
        self.trust_gap = float(trust_gap)
        self._by_player = {}
        for record in parsed.unit_records:
            self._by_player.setdefault(record.player_id, []).append(record)

    def units(self, player_id, seconds):
        """[(record, x, y, name, category, half_gap)]，只含位置可信的单位。

        ``half_gap`` 是这条位置信息的时间误差尺度（± 秒）。
        """
        out = []
        for record in self._by_player.get(player_id, ()):
            if not _alive(record, seconds):
                continue
            name = self.model.name_at(record, seconds)
            category = unitdata.category(name)
            track = self.parsed.unit_positions.get(record.tag[0])
            if track:
                x, y, nearest, half = _point_at(self.parsed, record.tag[0], seconds)
                if x is None or nearest > self.trust_gap:
                    continue
                out.append((record, x, y, name, category, half))
            elif record.x is not None and category in STRUCTURE_CATEGORIES:
                out.append((record, float(record.x), float(record.y),
                            name, category, 0.0))
        return out

    def sources(self, units):
        return [(x, y, sight.sight_radius(name, category, 1.0))
                for _record, x, y, name, category, _gap in units]

    def grid(self, units):
        return fog._bucket(self.sources(units))


def _sees(grid, x, y):
    return fog._sees(grid, x, y)


def _dist2(x1, y1, x2, y2):
    return (x1 - x2) ** 2 + (y1 - y2) ** 2


def _witness_gap(units, x, y):
    """某个点被看到时，最不可信的那个"目击者"的误差尺度（最保守）。"""
    worst = 0.0
    for _record, ux, uy, name, category, half in units:
        radius = sight.sight_radius(name, category, 1.0)
        if _dist2(ux, uy, x, y) <= radius * radius and half > worst:
            worst = half
    return worst


# --------------------------------------------------------------------------- 信息事件

class InfoEvent(object):
    """一条"我方当时看到了什么"。结构见契约第四节。"""

    __slots__ = ("t", "type", "detail", "unit", "precision")

    def __init__(self, t, type, detail, unit=None, precision=0.0):
        self.t = float(t)
        self.type = type
        self.detail = detail
        self.unit = unit
        self.precision = float(precision)

    def to_dict(self):
        out = {"t": round(self.t, 1), "type": self.type, "detail": self.detail}
        if self.unit:
            out["unit"] = self.unit
        # 采样间隔带来的时间误差尺度（± 秒），报告里据此说明精度
        out["precision"] = round(self.precision, 1)
        return out

    def __repr__(self):
        return "InfoEvent(t=%.1f, %s, %s)" % (self.t, self.type, self.detail)


def info_events(parsed, player, step=SCAN_STEP, trust_gap=POS_TRUST_GAP,
                max_seconds=DEFAULT_MAX_SECONDS):
    """算这个玩家在这局里"什么时候看到了什么"。

    返回按时间排序的 :class:`InfoEvent` 列表。判定保守：宁可不标。
    没有主基地坐标 / 没有位置数据时返回空列表（而不是瞎标）。
    """
    me = resolve_player(parsed, player)
    opp = opponent_of(parsed, me)
    if opp is None:
        return []
    opp_home = home_base(parsed, opp.player_id)
    vision = _Vision(parsed, trust_gap=trust_gap)

    events = []
    end = min(float(parsed.duration_seconds), float(max_seconds))
    step = max(0.25, float(step))

    scout_done = False
    contact_done = False
    army_done = False
    tech_done = set()
    scout_tag = first_army_tag(parsed, me.player_id)

    t = 0.0
    while t <= end:
        mine = vision.units(me.player_id, t)
        theirs = vision.units(opp.player_id, t)
        my_grid = vision.grid(mine)
        their_grid = vision.grid(theirs)

        # ---- scout_arrive：我方非建筑单位第一次把对方主基地罩进视野
        #
        # 判定用"第一个（进入视野的）非农民单位"：农民探路是最典型的触发者，
        # 但也可能是第一个往前压的兵（比如 3 分钟的收割者/巡航舰飞到他家门口）。
        # 两者都是真信息事件；detail 里会写清楚是"侦察"还是"部队"，不混为一谈。
        if not scout_done and opp_home is not None:
            best = None
            for record, x, y, name, category, half in mine:
                if category in STRUCTURE_CATEGORIES:
                    continue
                radius = sight.sight_radius(name, category, 1.0)
                if _dist2(x, y, opp_home[0], opp_home[1]) <= radius * radius:
                    distance = math.sqrt(_dist2(x, y, opp_home[0], opp_home[1]))
                    if best is None or distance < best[0]:
                        best = (distance, unitdata.unit_label(name), category, half,
                                record.tag == scout_tag)
            if best is not None:
                _distance, label, category, half, is_first = best
                if category == "worker":
                    kind = "侦察"
                elif is_first:
                    kind = "第一个非农民单位"
                else:
                    kind = "部队"
                events.append(InfoEvent(
                    t, "scout_arrive",
                    "%s（%s）进入敌方主基地视野" % (label, kind),
                    unit=label,
                    precision=max(half, _witness_gap(mine, opp_home[0], opp_home[1]))))
                scout_done = True

        # ---- army_seen：一次性看到对方 ≥5 个部队、造价合计 ≥500
        if not army_done:
            seen = []
            value = 0
            precision = 0.0
            for record, x, y, name, category, half in theirs:
                if category != ARMY_CATEGORY:
                    continue
                if not _sees(my_grid, x, y):
                    continue
                seen.append(name)
                precision = max(precision, half,
                                _witness_gap(mine, x, y))
                cost = unitdata.cost(name)
                if cost:
                    value += cost[0] + cost[1]
            if len(seen) >= ARMY_SEEN_UNITS and value >= ARMY_SEEN_VALUE:
                events.append(InfoEvent(
                    t, "army_seen",
                    "看到对方 %d 个部队（造价合计 %d）" % (len(seen), value),
                    precision=precision))
                army_done = True

        # ---- contact：双方部队互相可见（两边同一秒都成立才算）
        if not contact_done:
            mine_army = [(x, y) for _r, x, y, _n, c, _g in mine
                         if c == ARMY_CATEGORY and _sees(their_grid, x, y)]
            their_army = [(x, y) for _r, x, y, _n, c, _g in theirs
                          if c == ARMY_CATEGORY and _sees(my_grid, x, y)]
            if mine_army and their_army:
                precision = 0.0
                for x, y in mine_army:
                    precision = max(precision, _witness_gap(theirs, x, y))
                for x, y in their_army:
                    precision = max(precision, _witness_gap(mine, x, y))
                events.append(InfoEvent(
                    t, "contact",
                    "双方部队首次互相可见（我方 %d / 对方 %d 个）"
                    % (len(mine_army), len(their_army)), precision=precision))
                contact_done = True

        # ---- tech_seen：第一次看到对方每一种科技建筑（每种只记一次）
        for record, x, y, name, category, half in theirs:
            if name not in TECH_BUILDINGS or name in tech_done:
                continue
            if category not in STRUCTURE_CATEGORIES:
                continue
            if not _sees(my_grid, x, y):
                continue
            tech_done.add(name)
            events.append(InfoEvent(
                t, "tech_seen", "看到对方科技建筑：%s" % unitdata.unit_label(name),
                unit=name, precision=max(half, _witness_gap(mine, x, y))))

        t += step

    events.sort(key=lambda event: event.t)
    return events


def events_by_file(parsed, player):
    """便于拼进 reports/bo.json 的 {file: [InfoEvent]}。"""
    name = parsed.path.replace("\\", "/").rsplit("/", 1)[-1]
    return {name: [event.to_dict() for event in info_events(parsed, player)]}


# --------------------------------------------------------------------------- DAG 构建

def _game_meta(game):
    meta = game.get("game") or {}
    return meta


def _game_file(game):
    meta = _game_meta(game)
    name = meta.get("file") or meta.get("path") or ""
    return name.replace("\\", "/").rsplit("/", 1)[-1] if name else ""


def normalize_signature(signature):
    """段签名归一。

    段的签名是 bo 的 token 链（``rax-gas-cc`` / ``rax2-gas``），不是整局那个封闭词表
    （``2-1-1`` 之类）。这里只做清洗：小写、去空白、把空格换成短横线；如果出现
    非法字符（中文、括号……）才折成 ``unknown``，原值另外记在 raw_signatures 里。
    """
    text = (signature or "").strip().lower()
    if not text:
        return "unknown"
    text = text.replace(" ", "-").replace("_", "-")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789+-")
    if any(char not in allowed for char in text):
        return "unknown"
    return text


def signature_tokens(signature):
    """把段签名拆成 token，顺便报告有没有不认识的 token。"""
    tokens = []
    unknown = []
    for chunk in str(signature or "").split("-"):
        if not chunk or chunk.startswith("+"):
            continue
        token = chunk.rstrip("0123456789") or chunk
        tokens.append(chunk)
        if token not in SIGNATURE_TOKENS and chunk not in SIGNATURE_TOKENS:
            unknown.append(chunk)
    return tokens, unknown


def coarsen_signature(signature):
    """段签名 -> DAG 节点签名。

    bo 的段签名是"这一段按顺序下了哪些建筑"的 token 链（``rax-gas-cc``），拿来当节点 id
    颗粒度太细：同一套打法，操作顺序差一点就是两个节点，30 局能散出上百个节点，图直接读不了。
    所以粗化三步（规则固定、可复现）：

    1. **去掉 addon 和防御建筑**（``reac`` / ``lab`` / 塔 / 地堡）——这些是执行细节，
       不是"我选了哪套打法"；
    2. **按多重集合合并**（无序）——token 的先后是操作顺序噪声；
    3. **数量截到 2**（``rax`` / ``rax2`` / ``rax2+``），再多也只是"很多兵营"。

    粗化前后都保留：节点的 ``segment_signatures`` 里能看到原始 bo 签名分布。
    一个 token 都不剩（这一阶段只下了补给站之类）时记 ``plain``。
    """
    counts = {}
    for chunk in str(signature or "").split("-"):
        if not chunk or chunk.startswith("+"):
            continue
        base = chunk.rstrip("0123456789") or chunk
        rest = chunk[len(base):]
        try:
            count = int(rest) if rest else 1
        except ValueError:
            count = 1
        if base in NODE_DROP_TOKENS:
            continue
        counts[base] = counts.get(base, 0) + count
    if not counts:
        return "plain"
    parts = []
    for base, count in sorted(counts.items()):
        count = min(count, NODE_TOKEN_CAP)
        parts.append(base if count == 1 else "%s%d" % (base, count))
    return "-".join(parts)


def normalize_phase(phase):
    text = (phase or "").strip().lower()
    return text if text in PHASES else "mid"


def segment_anchor(segment):
    """段的"真实起点"：段内第一条步骤的时刻；没有步骤就退回 t0。

    bo 的段 t0 是时间表边界（0/180/360/600），拿它当转移时刻会让所有转移卡在整点。
    真正"开始切过去"的是这一段第一条建造/训练事件发生的时刻。
    """
    t0 = _as_float(segment.get("t0"))
    steps = segment.get("steps") or []
    first = None
    for step in steps:
        when = _as_float(step.get("t"))
        if when is None:
            continue
        if first is None or when < first:
            first = when
    if first is None:
        return t0
    if t0 is not None and first < t0:
        return t0
    return first


def _event_dicts(events):
    out = []
    for event in events or ():
        if isinstance(event, dict):
            out.append(event)
        elif hasattr(event, "to_dict"):
            out.append(event.to_dict())
        else:
            out.append(dict(event))
    return out


def normalize_games(game_bos):
    """bo.json 可能是 dict（{"games": [...]}）也可能是裸 list，两种都吃。"""
    if game_bos is None:
        return []
    if isinstance(game_bos, dict):
        games = game_bos.get("games")
        if games is None:
            # 也兼容 {file: GameBO} 这种按文件名索引的写法
            games = [value for value in game_bos.values()
                     if isinstance(value, dict) and value.get("segments") is not None]
        return list(games or [])
    return list(game_bos)


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _label(phase, signature):
    return "%s:%s" % (phase, signature)


def build_dag(game_bos, min_edge=2, info_events=None, sampling=None,
              sampling_start=None):
    """把多局的段序列拼成 DAG。

    参数
    ----
    game_bos : list[dict] 或 {"games": [...]}（bo.py 的 GameBO）
    min_edge : 次数少于此值的边不进 nodes/edges（但会记在 dropped_edges 里）
    info_events : {文件名: [InfoEvent 或 dict]}；不给就读 GameBO 里自带的
                  ``info_events`` 字段。
    sampling : 实测的位置采样起点，数字或 ``{"min": x, "median": y}``，
               只影响 precision_note 的文案（``sampling_start`` 是同义旧名）。

    返回严格符合契约第三节的 dict，另外多几个报告用得上的字段
    （``reaction`` / ``sequences`` / ``top_paths`` / ``dropped_edges`` / ``precision_note``）。

    字段口径
    --------
    * ``node.games`` = 该 (phase, 段签名) 出现在多少局里；``share`` = games / 总局数
    * ``edge.share`` = 该转移次数 / 出现起点节点的局数（≈ 转移概率）
    * ``edge.median_lag`` = 后一段锚点 − 前一段锚点（起点段停留了多久）
    * ``edge.median_at``  = 后一段锚点的中位绝对时刻（几分几秒切过去的）
    * ``edge.median_event_lag`` = 触发它的信息事件到锚点的中位间隔（秒）
    """
    games = normalize_games(game_bos)
    external = info_events or {}
    if isinstance(external, dict) and external.get("per_game") is not None:
        external = external["per_game"]

    node_stats = {}
    edge_stats = {}
    sequences = []
    event_map = {}
    games_with_events = 0
    game_signatures = {}

    for game in games:
        file = _game_file(game)
        events = _event_dicts(external.get(file) or game.get("info_events"))
        if events:
            games_with_events += 1
        event_map[file] = events

        # 整局的开局签名（封闭词表），单独统计，不参与节点聚合
        game_signature = normalize_signature(
            (game.get("signature") or (game.get("player") or {}).get("signature")))
        game_signatures[game_signature] = game_signatures.get(game_signature, 0) + 1

        segments = [segment for segment in (game.get("segments") or [])
                    if segment.get("signature") is not None]
        ids = []
        for segment in segments:
            phase = normalize_phase(segment.get("phase"))
            raw_signature = normalize_signature(segment.get("signature"))
            signature = coarsen_signature(raw_signature)
            node_id = _label(phase, signature)
            ids.append(node_id)
            bucket = node_stats.setdefault(node_id, {
                "id": node_id, "phase": phase, "signature": signature,
                "games": set(), "examples": [], "raw_signatures": set(),
                "t0_samples": [], "anchor_samples": [], "game_signatures": {},
                "segment_signatures": {},
            })
            bucket["games"].add(file)
            if len(bucket["examples"]) < 3:
                bucket["examples"].append(file)
            if signature == "unknown" or raw_signature == "unknown":
                bucket["raw_signatures"].add(str(segment.get("signature")))
            bucket["segment_signatures"][str(segment.get("signature"))] = (
                bucket["segment_signatures"].get(str(segment.get("signature")), 0) + 1)
            bucket["t0_samples"].append(_as_float(segment.get("t0")))
            bucket["anchor_samples"].append(segment_anchor(segment))
            bucket["game_signatures"][game_signature] = (
                bucket["game_signatures"].get(game_signature, 0) + 1)

        for index in range(len(segments) - 1):
            current = segments[index]
            nxt = segments[index + 1]
            source = ids[index]
            target = ids[index + 1]
            key = (source, target)
            bucket = edge_stats.setdefault(key, {
                "from": source, "to": target, "count": 0, "files": [],
                "lags": [], "ats": [], "gaps": [],
                "conditions": {}, "event_lags": [], "files_seen": set(),
                "by_window": {},
            })
            bucket["count"] += 1
            if len(bucket["files"]) < 3:
                bucket["files"].append(file)
            bucket["files_seen"].add(file)

            start = segment_anchor(nxt)
            previous_start = segment_anchor(current)
            previous_end = _as_float(current.get("t1"))
            bucket["ats"].append(start)
            bucket["lags"].append(start - previous_start)
            bucket["gaps"].append(max(0.0, start - previous_end))

            # 主口径：20 秒窗口；另外算 40/60 秒的敏感性
            for window in EVENT_WINDOWS:
                hit = _last_event_before(events, start, window)
                if not events:
                    condition = "none"
                elif hit is not None:
                    condition = "scout:%s" % hit.get("type")
                else:
                    condition = "time"
                window_conditions = bucket["by_window"].setdefault(str(int(window)), {})
                window_conditions[condition] = window_conditions.get(condition, 0) + 1
                if window == EVENT_WINDOW:
                    bucket["conditions"][condition] = (
                        bucket["conditions"].get(condition, 0) + 1)
                    if hit is not None:
                        bucket["event_lags"].append(start - _as_float(hit.get("t")))

        if ids:
            sequences.append({"file": file, "path": ids})

    total_games = len([game for game in games if (game.get("segments") or [])])

    nodes = []
    for node_id, bucket in sorted(node_stats.items(),
                                  key=lambda item: (PHASES.index(item[1]["phase"]),
                                                    -len(item[1]["games"]))):
        count = len(bucket["games"])
        tokens, unknown_tokens = signature_tokens(bucket["signature"])
        node = {
            "id": node_id,
            "phase": bucket["phase"],
            "signature": bucket["signature"],
            "games": count,
            "share": round(count / float(total_games), 3) if total_games else 0.0,
            "examples": bucket["examples"],
            "median_t0": _round(_median([value for value in bucket["t0_samples"]
                                         if value is not None])),
            "median_anchor": _round(_median([value for value in bucket["anchor_samples"]
                                             if value is not None])),
            "tokens": tokens,
            "game_signatures": dict(sorted(bucket["game_signatures"].items(),
                                           key=lambda item: -item[1])),
            # 粗化前的原始 bo 段签名分布，保留可追溯性
            "segment_signatures": dict(sorted(bucket["segment_signatures"].items(),
                                              key=lambda item: -item[1])),
        }
        if unknown_tokens:
            node["unknown_tokens"] = sorted(set(unknown_tokens))
        if bucket["raw_signatures"]:
            node["raw_signatures"] = sorted(bucket["raw_signatures"])
        nodes.append(node)

    edges = []
    dropped = []
    for (source, target), bucket in edge_stats.items():
        source_node = node_stats.get(source) or {"games": set()}
        denominator = len(source_node["games"]) or bucket["count"]
        record = {
            "from": source,
            "to": target,
            "count": bucket["count"],
            "share": round(bucket["count"] / float(denominator), 3),
            "median_lag": _round(_median(bucket["lags"])),
            "condition": _pick_condition(bucket["conditions"]),
            "examples": bucket["files"],
            "conditions": dict(sorted(bucket["conditions"].items())),
            "conditions_by_window": {
                window: dict(sorted(counts.items()))
                for window, counts in sorted(bucket["by_window"].items(),
                                             key=lambda item: int(item[0]))},
            "median_at": _round(_median(bucket["ats"])),
            "median_gap": _round(_median(bucket["gaps"])),
        }
        event_lag = _median(bucket["event_lags"])
        if event_lag is not None:
            record["median_event_lag"] = _round(event_lag)
        if bucket["count"] < max(1, int(min_edge)):
            dropped.append(record)
        else:
            edges.append(record)

    edges.sort(key=lambda edge: (-edge["count"], edge["from"], edge["to"]))
    dropped.sort(key=lambda edge: -edge["count"])

    reaction = _reaction_summary(edges, dropped, event_map, games)
    sampling_value = sampling if sampling is not None else sampling_start
    if isinstance(sampling_value, dict):
        sampling_min = sampling_value.get("min")
    else:
        sampling_min = sampling_value
    return {
        "meta": {
            "matchup": _guess_matchup(games),
            "games": total_games,
            "games_with_info_events": games_with_events,
            "source": _guess_source(games),
            "generated": datetime.date.today().isoformat(),
            "min_edge": int(min_edge),
            "event_window_seconds": EVENT_WINDOW,
            "event_windows_checked": list(EVENT_WINDOWS),
            "position_trust_gap": POS_TRUST_GAP,
            "position_sampling_start": (POSITION_SAMPLING_START
                                         if sampling_min is None
                                         else float(sampling_min)),
            "segmentation": "phase_buckets_with_step_anchor",
            "game_signatures": dict(sorted(game_signatures.items(),
                                           key=lambda item: -item[1])),
        },
        "nodes": nodes,
        "edges": edges,
        "info_events": {"per_game": event_map},
        "reaction": reaction,
        "sequences": sequences,
        "top_paths": top_paths(sequences, limit=10),
        "dropped_edges": dropped,
        "precision_note": precision_note(event_map, sampling_value),
    }


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value):
    return None if value is None else round(float(value), 1)


def _last_event_before(events, seconds, window=EVENT_WINDOW):
    """段开始时刻往前 ``window`` 秒内最后一条信息事件（最近的优先）。"""
    if not events or seconds is None:
        return None
    best = None
    for event in events:
        when = _as_float(event.get("t"))
        if when is None or when > seconds or when < seconds - window:
            continue
        if best is None or when > _as_float(best.get("t")):
            best = event
    return best


def _pick_condition(conditions):
    """边级别的条件标签：多数决；没有任何信息事件时写 none。"""
    if not conditions:
        return "none"
    if set(conditions) <= {"none"}:
        return "none"
    scout = {key: value for key, value in conditions.items() if key.startswith("scout:")}
    time_count = conditions.get("time", 0)
    none_count = conditions.get("none", 0)
    scout_count = sum(scout.values())
    if scout_count > time_count and scout_count > none_count:
        return max(scout.items(), key=lambda item: item[1])[0]
    if time_count == 0 and none_count > 0:
        return "none"
    return "time"


def _reaction_summary(edges, dropped, event_map, games):
    """"看到才变 vs 到点就变"的总账，外加分阶段 / 分窗口拆分。

    口径是**转移次数**（instance level），不是边的条数：同一秒窗口内有一次转移算一次。
    """
    totals = {"transitions": 0, "scout": 0, "time": 0, "none": 0}
    by_type = {}
    by_phase = {}
    by_window = {}
    for edge in list(edges) + list(dropped):
        phase = edge["from"].split(":", 1)[0]
        phase_bucket = by_phase.setdefault(phase, {"transitions": 0, "scout": 0,
                                                   "time": 0, "none": 0})
        for condition, count in (edge.get("conditions") or {}).items():
            kind = "scout" if condition.startswith("scout:") else condition
            totals["transitions"] += count
            totals[kind] = totals.get(kind, 0) + count
            phase_bucket["transitions"] += count
            phase_bucket[kind] = phase_bucket.get(kind, 0) + count
            if kind == "scout":
                label = condition.split(":", 1)[1]
                by_type[label] = by_type.get(label, 0) + count
        for window, conditions in (edge.get("conditions_by_window") or {}).items():
            window_bucket = by_window.setdefault(
                window, {"transitions": 0, "scout": 0, "time": 0, "none": 0})
            for window_condition, count in conditions.items():
                window_kind = ("scout" if window_condition.startswith("scout:")
                               else window_condition)
                window_bucket["transitions"] += count
                window_bucket[window_kind] = window_bucket.get(window_kind, 0) + count
    for bucket in [totals] + list(by_phase.values()) + list(by_window.values()):
        total = bucket.get("transitions", 0) or 1
        bucket["scout_share"] = round(bucket.get("scout", 0) / float(total), 3)
        bucket["time_share"] = round(bucket.get("time", 0) / float(total), 3)
        bucket["none_share"] = round(bucket.get("none", 0) / float(total), 3)
    ordered_phase = {phase: by_phase[phase] for phase in PHASES if phase in by_phase}
    return {
        "transitions": totals["transitions"],
        "scout": totals.get("scout", 0),
        "time": totals.get("time", 0),
        "none": totals.get("none", 0),
        "scout_share": totals["scout_share"],
        "time_share": totals["time_share"],
        "none_share": totals["none_share"],
        "by_type": dict(sorted(by_type.items(), key=lambda item: -item[1])),
        "by_phase": ordered_phase,
        "by_window": {key: by_window[key] for key in
                      sorted(by_window, key=lambda item: int(item))},
        "games_with_events": sum(1 for events in event_map.values() if events),
        "games": len([game for game in games if (game.get("segments") or [])]),
    }


def precision_note(event_map, sampling=None):
    """把事件的时间精度说清楚（报告和完成消息里都要照抄）。

    ``sampling`` 可以是数字（实测最早采样秒数），也可以是
    ``{"min": 最快那一局, "median": 中位局}``。
    """
    if isinstance(sampling, dict):
        start_min = float(sampling.get("min") or POSITION_SAMPLING_START)
        start_median = float(sampling.get("median") or start_min)
    else:
        start_min = POSITION_SAMPLING_START if sampling is None else float(sampling)
        start_median = start_min
    gaps = []
    counts = {}
    for events in event_map.values():
        for event in events:
            counts[event.get("type")] = counts.get(event.get("type"), 0) + 1
            try:
                gaps.append(float(event.get("precision") or 0.0))
            except (TypeError, ValueError):
                pass
    gaps.sort()
    median = gaps[len(gaps) // 2] if gaps else 0.0
    p90 = gaps[int(len(gaps) * 0.9)] if gaps else 0.0
    return {
        "event_types": dict(sorted(counts.items(), key=lambda item: -item[1])),
        "events_total": len(gaps),
        "half_gap_median": round(median, 1),
        "half_gap_p90": round(p90, 1),
        "position_sampling_starts_at": round(start_min, 1),
        "position_sampling_starts_at_median": round(start_median, 1),
        "position_sample_gap_median": 45.0,
        "reading": ("位置采样：多数局要等 ~%d:%02d 之后才有第一个位置点（本批最早 %d:%02d），"
                    "同一个单位中位 45 秒才给一个点。事件时间精度约 ±%.0f 秒"
                    "（90 分位 ±%.0f 秒）。%d:%02d 之前的侦察完全算不出来，"
                    "而且这个精度和 20 秒的条件窗口是同一个量级——"
                    "条件只能当「大致同时」读，不是因果。"
                    % (int(start_median) // 60, int(start_median) % 60,
                       int(start_min) // 60, int(start_min) % 60,
                       median, p90,
                       int(start_median) // 60, int(start_median) % 60)),
    }


def _guess_matchup(games):
    for game in games:
        matchup = _game_meta(game).get("matchup")
        if matchup:
            return matchup
    return ""


def _guess_source(games):
    for game in games:
        player = game.get("player") or {}
        if player.get("is_local") is True:
            return "mine"
        if player.get("is_local") is False:
            return "pro"
    return ""


# --------------------------------------------------------------------------- 路径统计

def top_paths(sequences, limit=10):
    """最常见的完整路径（段签名串）。"""
    counter = {}
    for item in sequences:
        key = " → ".join(item["path"])
        bucket = counter.setdefault(key, {"path": item["path"], "count": 0,
                                          "files": []})
        bucket["count"] += 1
        if len(bucket["files"]) < 3:
            bucket["files"].append(item["file"])
    ordered = sorted(counter.values(), key=lambda item: (-item["count"], item["path"]))
    return ordered[:limit]


def top_transitions(dag, limit=10):
    """最常见的转移（边），报告里的表用它。"""
    return dag["edges"][:limit]


def summary_line(dag, label=""):
    """给完成消息用的一句话摘要（纯 ASCII 之外的都在这一个 dict 里）。"""
    reaction = dag.get("reaction") or {}
    return {
        "label": label,
        "games": dag["meta"]["games"],
        "nodes": len(dag["nodes"]),
        "edges": len(dag["edges"]),
        "transitions": reaction.get("transitions", 0),
        "scout_share": reaction.get("scout_share", 0.0),
        "time_share": reaction.get("time_share", 0.0),
        "none_share": reaction.get("none_share", 0.0),
        "by_type": reaction.get("by_type", {}),
        "games_with_info_events": reaction.get("games_with_events", 0),
    }


def info_event_index(parsed, player):
    """调试用：{秒: [type...]}。"""
    out = {}
    for event in info_events(parsed, player):
        out.setdefault(int(event.t), []).append(event.type)
    return out


# --------------------------------------------------------------------------- 自检

def _self_test():
    """不依赖 replay 的自检：造几局假 GameBO，检查 DAG 的聚合与条件标注。"""
    def segment(phase, signature, t0, t1, step_at=None):
        steps = [] if step_at is None else [{"t": step_at, "name": "Barracks"}]
        return {"phase": phase, "signature": signature, "t0": t0, "t1": t1,
                "steps": steps}

    sample = [
        {
            "game": {"file": "a.SC2Replay", "matchup": "TvZ"},
            "player": {"pid": 1, "name": "me", "is_local": True},
            "signature": "2-1-1",
            "segments": [
                segment("opening", "rax-gas-cc", 0.0, 180.0, 12.5),
                # early 段的真实起点是 191.0（不是 180）
                segment("early", "rax2-gas", 180.0, 360.0, 191.0),
                segment("mid", "mechanize", 360.0, 600.0, 372.0),
            ],
            "info_events": [{"t": 145.0, "type": "army_seen", "detail": "x"}],
        },
        {
            "game": {"file": "b.SC2Replay", "matchup": "TvZ"},
            "player": {"pid": 1, "name": "me", "is_local": True},
            "signature": "1-1-1",
            "segments": [
                segment("opening", "rax-gas-cc", 0.0, 180.0, 10.0),
                segment("early", "rax2-gas", 180.0, 360.0, 188.0),
            ],
            "info_events": [],
        },
        {
            "game": {"file": "c.SC2Replay", "matchup": "TvZ"},
            "player": {"pid": 1, "name": "me", "is_local": True},
            "signature": "3-rax",
            "segments": [
                segment("opening", "rax-gas-cc", 0.0, 180.0, 11.0),
                segment("early", "rax2-gas", 180.0, 360.0, 190.0),
            ],
        },
    ]
    dag = build_dag(sample, min_edge=2)
    assert dag["meta"]["games"] == 3, dag["meta"]
    ids = {node["id"] for node in dag["nodes"]}
    # 节点签名是粗化过的：rax-gas-cc -> cc-gas-rax（无序、按字母排）
    assert "opening:cc-gas-rax" in ids and "early:gas-rax2" in ids, ids
    opening = next(node for node in dag["nodes"] if node["id"] == "opening:cc-gas-rax")
    assert opening["games"] == 3 and abs(opening["share"] - 1.0) < 1e-9
    assert opening["median_anchor"] == 11.0, opening
    assert opening["game_signatures"] == {"2-1-1": 1, "1-1-1": 1, "3-rax": 1}, opening
    assert opening["segment_signatures"] == {"rax-gas-cc": 3}, opening
    # 锚点用的是段内第一条步骤（191s），145s 那条事件只落在 60 秒窗口里
    edge = next(edge for edge in dag["edges"] if edge["from"] == "opening:cc-gas-rax"
                and edge["to"] == "early:gas-rax2")
    assert edge["count"] == 3 and edge["condition"] == "time", edge
    assert edge["median_at"] == 190.0, edge
    assert edge["conditions_by_window"]["60"].get("scout:army_seen") == 1, edge
    assert dag["reaction"]["by_window"]["60"]["scout"] == 1, dag["reaction"]
    # 段签名是 token 链；不认识的 token 要留痕
    assert opening["tokens"] == ["cc", "gas", "rax"], opening
    assert opening.get("unknown_tokens") is None, opening
    known = build_dag([{"game": {"file": "e.SC2Replay"},
                        "segments": [{"phase": "early", "signature": "weird-token",
                                      "t0": 180.0, "t1": 360.0}]}])
    assert known["nodes"][0]["unknown_tokens"] == ["token", "weird"], known["nodes"][0]
    # 非法字符才折成 unknown，原值留档
    unknown = build_dag([{"game": {"file": "d.SC2Replay"},
                          "segments": [{"phase": "opening", "signature": "??",
                                        "t0": 0.0, "t1": 100.0}]}])
    assert unknown["nodes"][0]["signature"] == "unknown"
    assert unknown["nodes"][0]["raw_signatures"] == ["??"]
    # 只有 addon / 防御建筑的段 -> plain
    plain = build_dag([{"game": {"file": "f.SC2Replay"},
                        "segments": [{"phase": "mid", "signature": "reac-bunker-turret2",
                                      "t0": 360.0, "t1": 600.0}]}])
    assert plain["nodes"][0]["signature"] == "plain", plain["nodes"][0]
    # 时间精度说明必须能被拼出来，且用 270 秒（4:30）这个实测默认值
    note = precision_note(dag["info_events"]["per_game"])
    assert "4:30" in note["reading"], note["reading"]
    assert note["position_sampling_starts_at"] == 270.0
    dict_note = precision_note(dag["info_events"]["per_game"],
                               {"min": 120.0, "median": 240.0})
    assert dict_note["position_sampling_starts_at"] == 120.0
    assert "2:00" in dict_note["reading"] and "4:00" in dict_note["reading"]
    assert dag["meta"]["event_window_seconds"] == 20.0
    return True


if __name__ == "__main__":
    _self_test()
    print("dag.py self-test OK")
