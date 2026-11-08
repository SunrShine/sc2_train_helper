"""把战争迷雾画出来：三态（黑 / 阴影 / 可见）+ 单位点的 GIF 动画。

配色刻意贴近游戏里的观感：
- 从没看过 -> 接近纯黑；
- 看过、但现在没视野 -> 暗蓝灰（"记忆中的地图"）；
- 当前可见 -> 亮一档，单位点才看得清。

只画 viewer 自己的视野：敌人的兵在圈外就不会出现（和真实客户端一致）。
想教学"他就在那但你看不见"时，用 ``reveal_hidden=True`` 画出灰点。
"""

import math
import os

from . import card
from .fog import Model, _bucket, _fill_disc

# --------------------------------------------------------------------------- 调色板
BLACK = (6, 9, 13)          # 0 没探索过
SHROUD = (22, 28, 38)       # 1 探索过、当前无视野
LIT = (64, 82, 106)         # 2 当前可见
GRID = (32, 41, 54)         # 3
TEXT = (214, 226, 240)      # 10
DIM = (120, 136, 158)       # 11
EDGE = (250, 252, 255)      # 12
PROGRESS = (124, 156, 255)  # 13
GHOST = (74, 84, 100)       # 14 看不见的敌人（仅教学模式）

I_BLACK, I_SHROUD, I_LIT, I_GRID = 0, 1, 2, 3
I_ME_STRUCT, I_ME_UNIT, I_ME_WORK = 4, 5, 6
I_OPP_STRUCT, I_OPP_UNIT, I_OPP_WORK = 7, 8, 9
I_TEXT, I_DIM, I_EDGE, I_PROGRESS, I_GHOST = 10, 11, 12, 13, 14


def _mix(color, factor):
    return tuple(max(0, min(255, int(channel * factor))) for channel in color)


def palette_for(viewer_color, opponent_color):
    colors = [BLACK, SHROUD, LIT, GRID,
              _mix(viewer_color, 0.55), viewer_color, _mix(viewer_color, 0.72),
              _mix(opponent_color, 0.55), opponent_color, _mix(opponent_color, 0.72),
              TEXT, DIM, EDGE, PROGRESS, GHOST]
    # 补到 256 色，方便任何索引都不越界
    colors.extend([BLACK] * (256 - len(colors)))
    return colors


# 点阵字里补几个字母，用来写 "FOG"
_EXTRA_GLYPHS = {
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
}
for _key, _glyph in _EXTRA_GLYPHS.items():
    card.FONT.setdefault(_key, _glyph)


# --------------------------------------------------------------------------- 绘制
def _fill(mask, width, height, cx, cy, radius, value):
    _fill_disc(mask, width, height, cx, cy, radius, value)


def _draw_units(frame, model, viewer, opponent, seconds, projector, grid, reveal_hidden):
    for side in (viewer, opponent):
        own = side.player_id == viewer.player_id
        color_struct = I_ME_STRUCT if own else I_OPP_STRUCT
        color_unit = I_ME_UNIT if own else I_OPP_UNIT
        color_work = I_ME_WORK if own else I_OPP_WORK
        for record, ux, uy in model.units_at(side.player_id, seconds):
            name = model.name_at(record, seconds)
            kind = model.kind_at(record, seconds)
            px, py = projector(ux, uy)
            if not own and not _visible(grid, ux, uy):
                if reveal_hidden and kind != "structure":
                    frame.rect(px, py, px + 2, py + 2, I_GHOST)
                continue
            if kind == "structure":
                big = name in ("CommandCenter", "Nexus", "Hatchery", "Lair", "Hive",
                               "OrbitalCommand", "PlanetaryFortress")
                size = 3 if big else 2
                frame.rect(px - size, py - size, px + size, py + size, color_struct)
            elif kind == "worker":
                frame.rect(px - 1, py - 1, px + 2, py + 2, color_work)
            else:
                frame.rect(px - 1, py - 1, px + 3, py + 3, color_unit)


def _visible(grid, x, y):
    from .fog import _sees
    return _sees(grid, x, y)


def _clock_text(seconds):
    whole = int(max(0.0, seconds))
    return "%d:%02d" % (whole // 60, whole % 60)


def render(model, viewer, opponent, seconds, projector, explored, frame,
           reveal_hidden=False):
    """画一帧：explored 会被就地更新（累计探索区域）。"""
    width, height = frame.width, frame.height
    sources = model.sources(viewer.player_id, seconds)
    grid = _bucket(sources)

    for x, y, radius in sources:
        px, py = projector(x, y)
        _fill(explored, width, height, px, py, radius * projector.scale, I_SHROUD)

    mask = bytearray(explored)
    for x, y, radius in sources:
        px, py = projector(x, y)
        _fill(mask, width, height, px, py, radius * projector.scale, I_LIT)

    frame.buf[:] = mask

    # 没有地形数据，只能给个坐标系参考线（每 25 格一根）
    for gx in range(0, 300, 25):
        left = projector(gx, -100)
        frame.rect(left[0], 0, left[0] + 1, height, I_GRID)
    for gy in range(0, 300, 25):
        line = projector(-100, gy)
        frame.rect(0, line[1], width, line[1] + 1, I_GRID)

    _draw_units(frame, model, viewer, opponent, seconds, projector, grid, reveal_hidden)

    # 顶部时间 + FOG 标记
    card._draw_text(frame.buf, width, 6, 6, _clock_text(seconds), I_TEXT)
    card._draw_text(frame.buf, width, width - 26, 6, "FOG", I_DIM)

    # 底部进度条
    frame.rect(0, height - 4, width, height, I_GRID)
    if model.duration > 0:
        frame.rect(0, height - 4, width * min(1.0, seconds / model.duration), height, I_PROGRESS)
    return frame


def build_fog_card(replay_path, output, t0=0.0, duration=40.0, fps=8,
                   width=440, height=420, viewer_name=None,
                   reveal_hidden=False, mp4=None, interpolate=True):
    """生成"迷雾动画"GIF，返回 (路径, 信息字典)。"""
    from .loader import load_replay

    parsed = load_replay(replay_path)
    viewer = card.pick_player(parsed, viewer_name)
    if viewer is None:
        raise ValueError("replay 里没有玩家")
    opponent = next((p for p in parsed.players if p.player_id != viewer.player_id), None)
    if opponent is None:
        raise ValueError("找不到对手")

    model = Model(parsed, interpolate=interpolate)
    projector = card.Projector(model.bounds, width, height, pad=8)
    palette = palette_for(viewer.color, opponent.color)

    start = max(0.0, min(t0, max(0.0, model.duration - 2.0)))
    stop = min(model.duration, start + duration)
    if stop - start < 2.0:
        stop = min(model.duration, start + 2.0)

    explored = bytearray(width * height)
    warm = 0.0
    while warm < start:
        for x, y, radius in model.sources(viewer.player_id, warm):
            px, py = projector(x, y)
            _fill(explored, width, height, px, py, radius * projector.scale, I_SHROUD)
        warm += 0.5

    frames = []
    steps = max(2, int((stop - start) * fps))
    for index in range(steps):
        moment = start + index / float(fps)
        frame = card.Frame(width, height)
        render(model, viewer, opponent, moment, projector, explored, frame, reveal_hidden)
        frames.append(frame)

    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    mp4_path = os.path.splitext(output)[0] + ".mp4" if mp4 else None
    encoded = None
    if card._ffmpeg_path():
        encoded = card.write_gif_ffmpeg(output, frames, palette, fps=fps, mp4=mp4_path)
    if not encoded:
        frame_dir = os.path.splitext(output)[0] + "_frames"
        os.makedirs(frame_dir, exist_ok=True)
        for index, frame in enumerate(frames):
            card.write_png(os.path.join(frame_dir, "f%04d.png" % index), frame, palette)
        card._write_player(frame_dir, len(frames), fps, output)

    info = {
        "path": os.path.abspath(output),
        "replay": os.path.abspath(replay_path),
        "viewer": viewer.name,
        "opponent": opponent.name,
        "map": parsed.map_name,
        "start": round(start, 2),
        "duration": round(stop - start, 2),
        "frames": len(frames),
        "fps": fps,
        "reveal_hidden": bool(reveal_hidden),
        "mp4": mp4_path,
    }
    return output, info
