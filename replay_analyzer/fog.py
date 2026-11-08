"""战争迷雾还原：算清楚"那一刻谁看得见谁"，并把迷雾画出来。

边界
----
这是**复盘**工具：只用 replay 里已有的数据（单位位置、出生/死亡时间）在内存里
重算视野，不读游戏内存、不注入进程、不碰正在进行的对局。SC2 的观战和复盘本来
就能全图看，这里做的是把"全图"翻译回"当时双方各自的眼睛"。

模型
----
1. 每个存活单位都是一个视野圆，半径来自 :mod:`replay_analyzer.sight`；
2. 目标点落在任意一个己方圆内 -> 看得见；
3. 三个状态：从没看过（黑）、看过但现在没视野（阴影/记忆）、当前可见（亮）。

不知道的部分（也是这套东西的诚实边界）
--------------------------------------
- **地形遮挡没算**：高地打低地、悬崖边缘、岩石挡视线，replay 里没有地形数据，
  这里只能当平地处理。所以"隔着悬崖看见"会被高估。
- **视野半径是手册数值**，个别 ±1；用 ``--sight-scale`` 可以做敏感度检查。
- **探测器/隐形**：隐形单位在"看不见"时不会暴露，但这里不做隐形判定，
  只有视野几何，所以对隐形单位的结果要自己再想一层。
"""

import bisect
import math

from . import card, micro, sight
from . import units as unitdata
from .loader import LOOPS_PER_SECOND

GRID_CELL = 15.0        # 视野源空间哈希的格子边长（格）
COVERAGE_STEP = 4.0     # 视野覆盖率采样间隔（格）
COVERAGE_EVERY = 5.0    # 每多少秒算一次覆盖率

EPS = 1e-6


# --------------------------------------------------------------------------- 基础
def _alive(record, seconds):
    start = record.start_loop
    if start is not None and start / LOOPS_PER_SECOND > seconds:
        return False
    if record.died_loop is not None and record.died_loop / LOOPS_PER_SECOND <= seconds:
        return False
    return True


def _bucket(sources, cell=GRID_CELL):
    grid = {}
    for x, y, radius in sources:
        key = (int(x // cell), int(y // cell))
        grid.setdefault(key, []).append((x, y, radius * radius))
    return grid


def _sees(grid, x, y, cell=GRID_CELL):
    gx, gy = int(x // cell), int(y // cell)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for sx, sy, r2 in grid.get((gx + dx, gy + dy), ()):
                if (x - sx) ** 2 + (y - sy) ** 2 <= r2:
                    return True
    return False


def _fill_disc(mask, width, height, cx, cy, radius, value):
    """把一个圆填进 bytearray 位图（按行切片写入，比逐像素快得多）。"""
    radius = float(radius)
    if radius <= 0:
        return
    top = max(0, int(cy - radius))
    bottom = min(height, int(cy + radius) + 1)
    fill = bytes([value])
    for y in range(top, bottom):
        dy = y - cy
        span = radius * radius - dy * dy
        if span <= 0:
            continue
        half = math.sqrt(span)
        x0 = max(0, int(cx - half))
        x1 = min(width, int(cx + half) + 1)
        if x1 > x0:
            start = y * width + x0
            mask[start:start + (x1 - x0)] = fill * (x1 - x0)



def _interp(track, seconds):
    """在两次位置采样之间做线性插值；超出两端就取端点。

    replay 里的单位位置是**稀疏快照**（每单位中位数只有两三个点），
    不是逐帧轨迹；不插值就会让单位长时间停在最后一次采样的位置。
    """
    if not track:
        return None
    if seconds <= track[0][0]:
        return track[0]
    if seconds >= track[-1][0]:
        return track[-1]
    index = bisect.bisect_left(track, (seconds,))
    prev = track[index - 1]
    nxt = track[index]
    span = nxt[0] - prev[0]
    if span <= 0:
        return prev
    ratio = (seconds - prev[0]) / span
    return (seconds,
            prev[1] + (nxt[1] - prev[1]) * ratio,
            prev[2] + (nxt[2] - prev[2]) * ratio)


class Model(object):
    """某个 replay 的视野模型：给定玩家和时间，返回视野源 / 单位列表。"""

    def __init__(self, parsed, step=1.0, scale=1.0, interpolate=True):
        self.parsed = parsed
        self.step = max(0.25, float(step))
        self.scale = float(scale)
        self.interpolate = bool(interpolate)
        self.positions = card._positions(parsed)
        self.duration = parsed.duration_seconds
        self.bounds = card._bounds(parsed)
        self._by_player = {}
        for record in parsed.unit_records:
            self._by_player.setdefault(record.player_id, []).append(record)

    # ---------------------------------------------------------------- 单位
    def name_at(self, record, seconds):
        name = record.name
        for loop, morph in record.morphs:
            if loop / LOOPS_PER_SECOND <= seconds:
                name = morph
            else:
                break
        return name

    def kind_at(self, record, seconds):
        return unitdata.category(self.name_at(record, seconds))

    def units_at(self, player_id, seconds):
        out = []
        for record in self._by_player.get(player_id, ()):
            if not _alive(record, seconds):
                continue
            track = self.positions.get(record.tag[0])
            point = (_interp(track, seconds) if self.interpolate else card._at(track, seconds)) if track else None
            if point is not None:
                out.append((record, point[1], point[2]))
            elif record.x is not None:
                out.append((record, record.x, record.y))
        return out

    # ---------------------------------------------------------------- 视野
    def sources(self, player_id, seconds):
        out = []
        for record, x, y in self.units_at(player_id, seconds):
            name = self.name_at(record, seconds)
            radius = sight.sight_radius(name, unitdata.category(name), self.scale)
            out.append((x, y, radius))
        return out

    def source_grid(self, player_id, seconds):
        return _bucket(self.sources(player_id, seconds))

    def sees_point(self, player_id, seconds, x, y):
        return _sees(self.source_grid(player_id, seconds), x, y)

    def visible_to(self, viewer_pid, target_pid, seconds):
        """目标玩家这一秒的每个单位，对 viewer 是否可见。"""
        grid = self.source_grid(viewer_pid, seconds)
        out = []
        for record, x, y in self.units_at(target_pid, seconds):
            out.append((record, x, y, _sees(grid, x, y)))
        return out

    # ---------------------------------------------------------------- 覆盖率
    def coverage_points(self, step=COVERAGE_STEP):
        left, right, bottom, top = self.bounds
        xs = []
        x = left
        while x <= right:
            xs.append(x)
            x += step
        points = []
        for y in [bottom + i * step for i in range(int((top - bottom) / step) + 1)]:
            for x in xs:
                points.append((x, y))
        return points

    def coverage(self, player_id, seconds, points=None):
        points = points if points is not None else self.coverage_points()
        if not points:
            return 0.0
        grid = self.source_grid(player_id, seconds)
        hits = sum(1 for x, y in points if _sees(grid, x, y))
        return hits / float(len(points))


# --------------------------------------------------------------------------- 分析
PHASES = ((0.0, 300.0, "0-5 分钟"), (300.0, 600.0, "5-10 分钟"),
          (600.0, 900.0, "10-15 分钟"), (900.0, 10 ** 9, "15 分钟后"))


def _phase_index(seconds):
    for index, (low, high, _label) in enumerate(PHASES):
        if low <= seconds < high:
            return index
    return len(PHASES) - 1


def _windows(samples, predicate, min_length=8.0):
    """把满足 predicate 的连续采样拼成时间窗。"""
    windows = []
    current = None
    for sample in samples:
        if predicate(sample):
            if current is None:
                current = {"t0": sample["t"], "t1": sample["t"], "n": 0}
            current["t1"] = sample["t"]
            current["n"] += 1
        elif current is not None:
            if current["t1"] - current["t0"] >= min_length - EPS:
                windows.append(current)
            current = None
    if current is not None and current["t1"] - current["t0"] >= min_length - EPS:
        windows.append(current)
    for window in windows:
        window["dur"] = window["t1"] - window["t0"] + 1.0
    return windows


def analyze(parsed, me_pid=None, opp_pid=None, step=1.0, scale=1.0, interpolate=True):
    """主入口：返回这局的双向视野统计。"""
    players = [p for p in parsed.players if not p.is_ai]
    if not players:
        players = parsed.players
    me = None
    if me_pid:
        me = parsed.player_by_player_id(me_pid)
    if me is None:
        me = next((p for p in players if p.is_local), players[0] if players else None)
    if me is None:
        raise ValueError("replay 里没有可分析的玩家")

    opp = None
    if opp_pid:
        opp = parsed.player_by_player_id(opp_pid)
    if opp is None:
        opp = next((p for p in players if p.player_id != me.player_id), None)
    if opp is None:
        raise ValueError("找不到对手")

    model = Model(parsed, step=step, scale=scale, interpolate=interpolate)
    points = model.coverage_points()
    landmarks = micro._landmarks(parsed)
    region_bounds = micro._map_bounds(landmarks)

    timeline = []
    mine_army = {"alive": 0.0, "seen": 0.0}
    mine_workers = {"alive": 0.0, "seen": 0.0}
    theirs_army = {"alive": 0.0, "seen": 0.0}
    theirs_workers = {"alive": 0.0, "seen": 0.0}
    by_phase = [[{"alive": 0.0, "seen": 0.0}, {"alive": 0.0, "seen": 0.0}]
                for _ in PHASES]
    by_region = {}
    per_unit = {}
    first_seen = {}
    last_seen = {}
    coverage_samples = []
    first_contact = None

    t = 0.0
    while t <= model.duration:
        mine_units = model.units_at(me.player_id, t)
        theirs_units = model.units_at(opp.player_id, t)
        my_grid = _bucket(model.sources(me.player_id, t))
        their_grid = _bucket(model.sources(opp.player_id, t))

        mine_seen = mine_alive_army = 0
        theirs_seen = theirs_alive_army = 0
        phase = _phase_index(t)

        for record, x, y in mine_units:
            kind = model.kind_at(record, t)
            if kind == "army":
                mine_alive_army += 1
                mine_army["alive"] += 1.0
                by_phase[phase][0]["alive"] += 1.0
                bucket = per_unit.setdefault(record.tag, {"name": record.name,
                                                          "alive": 0.0, "seen": 0.0,
                                                          "first": None})
                bucket["alive"] += 1.0
                if _sees(their_grid, x, y):
                    mine_seen += 1
                    mine_army["seen"] += 1.0
                    by_phase[phase][0]["seen"] += 1.0
                    bucket["seen"] += 1.0
                    if bucket["first"] is None:
                        bucket["first"] = t
                        first_seen[record.tag] = t
                    last_seen[record.tag] = t
                    region = micro._region((x, y), landmarks, me.player_id, region_bounds)
                    by_region[region] = by_region.get(region, 0.0) + 1.0
                    if first_contact is None:
                        first_contact = t
            elif kind == "worker":
                mine_workers["alive"] += 1.0
                if _sees(their_grid, x, y):
                    mine_workers["seen"] += 1.0

        for record, x, y in theirs_units:
            kind = model.kind_at(record, t)
            if kind == "army":
                theirs_alive_army += 1
                theirs_army["alive"] += 1.0
                by_phase[phase][1]["alive"] += 1.0
                if _sees(my_grid, x, y):
                    theirs_seen += 1
                    theirs_army["seen"] += 1.0
                    by_phase[phase][1]["seen"] += 1.0
            elif kind == "worker":
                theirs_workers["alive"] += 1.0
                if _sees(my_grid, x, y):
                    theirs_workers["seen"] += 1.0

        row = {
            "t": t,
            "mine_alive": mine_alive_army, "mine_seen": mine_seen,
            "theirs_alive": theirs_alive_army, "theirs_seen": theirs_seen,
            "mine_ratio": (mine_seen / float(mine_alive_army)) if mine_alive_army else 0.0,
            "theirs_ratio": (theirs_seen / float(theirs_alive_army)) if theirs_alive_army else 0.0,
        }
        timeline.append(row)

        if abs(t % COVERAGE_EVERY) < EPS or t == 0.0:
            mine_cov = model.coverage(me.player_id, t, points)
            theirs_cov = model.coverage(opp.player_id, t, points)
            coverage_samples.append({"t": t, "mine": mine_cov, "theirs": theirs_cov})
            row["mine_cov"] = mine_cov
            row["theirs_cov"] = theirs_cov

        t += step

    # 按单位类型聚合（同一类型可能有很多个 tag，逐个列没意义）
    aggregate = {}
    for bucket in per_unit.values():
        label = unitdata.unit_label(bucket["name"])
        item = aggregate.setdefault(label, {"name": label, "alive": 0.0, "seen": 0.0,
                                            "first": None})
        item["alive"] += bucket["alive"]
        item["seen"] += bucket["seen"]
        if bucket["first"] is not None and (item["first"] is None
                                            or bucket["first"] < item["first"]):
            item["first"] = bucket["first"]

    blind = _windows(timeline, lambda s: s["mine_alive"] >= 3 and s["mine_seen"] == 0, 10.0)
    exposed = _windows(timeline, lambda s: s["mine_alive"] >= 3 and s["mine_ratio"] >= 0.6, 8.0)

    def ratio(bucket):
        return (bucket["seen"] / bucket["alive"]) if bucket["alive"] else 0.0

    phases = []
    for index, (_low, _high, label) in enumerate(PHASES):
        item = by_phase[index]
        phases.append({
            "label": label,
            "mine_alive": item[0]["alive"], "mine_seen": item[0]["seen"],
            "mine_ratio": (item[0]["seen"] / item[0]["alive"]) if item[0]["alive"] else 0.0,
            "theirs_alive": item[1]["alive"], "theirs_seen": item[1]["seen"],
            "theirs_ratio": (item[1]["seen"] / item[1]["alive"]) if item[1]["alive"] else 0.0,
        })

    regions = sorted(by_region.items(), key=lambda kv: -kv[1])

    # 位置采样质量：这是整套重建最硬的约束，报告里必须说清楚
    gaps = []
    for track in parsed.unit_positions.values():
        for prev, nxt in zip(track, track[1:]):
            gaps.append((nxt[0] - prev[0]) / LOOPS_PER_SECOND)
    gaps.sort()
    data_quality = {
        "units_total": len(parsed.unit_records),
        "units_tracked": len(parsed.unit_positions),
        "samples": sum(len(track) for track in parsed.unit_positions.values()),
        "gap_median": gaps[len(gaps) // 2] if gaps else 0.0,
        "gap_p90": gaps[int(len(gaps) * 0.9)] if gaps else 0.0,
    }

    mine_cov_avg = (sum(s["mine"] for s in coverage_samples) / len(coverage_samples)
                    if coverage_samples else 0.0)
    theirs_cov_avg = (sum(s["theirs"] for s in coverage_samples) / len(coverage_samples)
                      if coverage_samples else 0.0)

    army_worst = [b for b in aggregate.values() if b["alive"] >= 120]
    army_worst.sort(key=lambda b: (-(b["seen"] / b["alive"]), -b["alive"]))
    army_worst = army_worst[:6]

    return {
        "meta": {
            "map": parsed.map_name,
            "path": parsed.path,
            "duration": parsed.duration_seconds,
            "step": step,
            "sight_scale": scale,
            "interpolate": bool(interpolate),
            "protocol_build": parsed.protocol_build,
            "bounds": list(model.bounds),
        },
        "me": {"player_id": me.player_id, "name": me.name, "race": me.race,
               "color": me.color_hex, "toon": me.toon},
        "opponent": {"player_id": opp.player_id, "name": opp.name, "race": opp.race,
                     "color": opp.color_hex},
        "army": {"alive": mine_army["alive"], "seen": mine_army["seen"], "ratio": ratio(mine_army)},
        "workers": {"alive": mine_workers["alive"], "seen": mine_workers["seen"],
                    "ratio": ratio(mine_workers)},
        "theirs_army": {"alive": theirs_army["alive"], "seen": theirs_army["seen"],
                        "ratio": ratio(theirs_army)},
        "theirs_workers": {"alive": theirs_workers["alive"], "seen": theirs_workers["seen"],
                           "ratio": ratio(theirs_workers)},
        "vision": {"mine_coverage": mine_cov_avg, "theirs_coverage": theirs_cov_avg,
                   "coverage_samples": len(coverage_samples)},
        "phases": phases,
        "regions": regions,
        "units": [{"name": b["name"], "alive": b["alive"], "seen": b["seen"],
                   "ratio": ratio(b), "first": b["first"]} for b in army_worst],
        "first_contact": first_contact,
        "blind_windows": blind,
        "exposed_windows": exposed,
        "timeline": timeline,
        "coverage": coverage_samples,
        "missing_types": sight.missing_types(parsed),
        "data_quality": data_quality,
    }
