"""把一段"多线操作"渲染成 GIF 卡片（练习模板）。

思路：replay 里已经有位置采样（SUnitPositionsEvent）、视角轨迹（CameraUpdate）和指令落点，
把它们叠加成一张"小地图动画"：单位是点、建筑是方块、白框是当时在看的屏幕、金色十字是刚下的指令，
窗口里两条线的位置用圆圈标出来。不依赖任何绘图库，GIF 编码器也是自己写的（约 100 行）。

用法见 cli 里的 `card` 子命令。
"""

import math
import os
import struct

from . import units as unitdata

LOOPS_PER_SECOND = 16.0

# --------------------------------------------------------------------------- 5x7 点阵字（只做时间、数字和少量字母）
FONT = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    " ": ("00000",) * 7,
    "s": ("00000", "00000", "01110", "10000", "01110", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
}


def _draw_text(buf, width, x0, y0, text, color):
    cursor = x0
    for char in text:
        glyph = FONT.get(char) or FONT.get(char.upper()) or FONT[" "]
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit == "1":
                    px, py = cursor + col, y0 + row
                    if 0 <= px < width:
                        index = py * width + px
                        if 0 <= index < len(buf):
                            buf[index] = color
        cursor += 6


# --------------------------------------------------------------------------- 调色板
def _palette(player_a, player_b):
    """索引调色板：0 背景、1 网格 … 后面的单位/建筑/相机/指令各自固定索引。"""
    def mix(color, factor):
        return tuple(max(0, min(255, int(channel * factor))) for channel in color)

    return [
        (11, 15, 20),        # 0 背景
        (24, 32, 42),        # 1 网格
        (26, 36, 48),        # 2 基地暗
        mix(player_a, 0.55),  # 3 建筑 A
        mix(player_b, 0.55),  # 4 建筑 B
        player_a,            # 5 单位 A
        player_b,            # 6 单位 B
        mix(player_a, 0.65),  # 7 农民 A
        mix(player_b, 0.65),  # 8 农民 B
        (240, 244, 250),     # 9 相机
        (255, 196, 64),      # 10 指令
        (255, 96, 96),       # 11 多线标记
        (200, 214, 230),     # 12 文字
        (34, 43, 56),        # 13 底栏
        (124, 156, 255),     # 14 进度
        (120, 140, 165),     # 15 次要文字
        (255, 255, 255),     # 16 高亮
        (60, 78, 100),       # 17 边界
    ]


BG, GRID, BASE, STRUCT_A, STRUCT_B, UNIT_A, UNIT_B, WORK_A, WORK_B = range(9)
CAMERA, CMD, RING, TEXT, BAR, PROGRESS, DIM_TEXT, HI, EDGE = range(9, 18)


# --------------------------------------------------------------------------- 数据
def _positions(parsed):
    """tagIndex -> [(seconds, x, y)]，按时间排序。"""
    out = {}
    for index, track in parsed.unit_positions.items():
        out[index] = [(loop / LOOPS_PER_SECOND, x, y) for loop, x, y in track]
    return out


def _bounds(parsed):
    xs, ys = [], []
    for record in parsed.unit_records:
        if record.x is not None:
            xs.append(record.x)
            ys.append(record.y)
    for track in parsed.unit_positions.values():
        for _loop, x, y in track:
            xs.append(x)
            ys.append(y)
    if not xs:
        return (0.0, 200.0, 0.0, 200.0)
    return (min(xs) - 4, max(xs) + 4, min(ys) - 4, max(ys) + 4)


def _at(track, seconds):
    """取 <= seconds 的最后一个采样（二分）。"""
    low, high = 0, len(track) - 1
    best = None
    while low <= high:
        mid = (low + high) // 2
        if track[mid][0] <= seconds:
            best = track[mid]
            low = mid + 1
        else:
            high = mid - 1
    return best


# --------------------------------------------------------------------------- 渲染
class Frame(object):
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.buf = bytearray(width * height)

    def clear(self, color=BG):
        self.buf[:] = bytes([color]) * (self.width * self.height)

    def rect(self, x0, y0, x1, y1, color):
        x0 = max(0, min(self.width - 1, int(x0)))
        x1 = max(0, min(self.width, int(x1)))
        if x1 <= x0:
            return
        row = bytes([color]) * (x1 - x0)
        for y in range(max(0, int(y0)), min(self.height, int(y1))):
            start = y * self.width + x0
            self.buf[start:start + len(row)] = row

    def ring(self, cx, cy, radius, color):
        cx, cy = int(cx), int(cy)
        steps = max(16, int(radius * 2))
        for step in range(steps):
            angle = step * 2 * math.pi / steps
            x = int(cx + radius * math.cos(angle))
            y = int(cy + radius * math.sin(angle))
            if 0 <= x < self.width and 0 <= y < self.height:
                self.buf[y * self.width + x] = color

    def cross(self, cx, cy, size, color):
        cx, cy = int(cx), int(cy)
        for delta in range(-size, size + 1):
            for (x, y) in ((cx + delta, cy), (cx, cy + delta)):
                if 0 <= x < self.width and 0 <= y < self.height:
                    self.buf[y * self.width + x] = color


class Projector(object):
    def __init__(self, bounds, width, height, pad=10):
        left, right, bottom, top = bounds
        self.width = width
        self.height = height
        self.pad = pad
        self.scale = min((width - 2 * pad) / max(1, right - left),
                         (height - 2 * pad) / max(1, top - bottom))
        self.offset_x = pad + ((width - 2 * pad) - (right - left) * self.scale) / 2.0
        self.offset_y = pad + ((height - 2 * pad) - (top - bottom) * self.scale) / 2.0
        self.left = left
        self.bottom = bottom

    def __call__(self, x, y):
        return (self.offset_x + (x - self.left) * self.scale,
                self.height - self.offset_y - (y - self.bottom) * self.scale)


def _player_units(parsed, player_id, seconds, positions):
    """当前存活单位 + 坐标。"""
    out = []
    for record in parsed.unit_records:
        if record.player_id != player_id:
            continue
        start = record.start_loop
        if start is None or start / LOOPS_PER_SECOND > seconds:
            continue
        if record.died_loop is not None and record.died_loop / LOOPS_PER_SECOND <= seconds:
            continue
        track = positions.get(record.tag[0])
        point = _at(track, seconds) if track else None
        if point:
            out.append((record, point[1], point[2]))
        elif record.x is not None:
            out.append((record, record.x, record.y))
    return out


def _camera_at(parsed, player, seconds):
    target = None
    for action in parsed.actions:
        if action["u"] != player.user_id or action["k"] != "cam":
            continue
        moment = action["l"] / LOOPS_PER_SECOND
        if moment > seconds:
            break
        if "x" in action:
            target = (action["x"], action["y"])
    return target


def render_frame(parsed, player, seconds, window, projector, frame, palette_size):
    frame.clear(BG)
    width, height = frame.width, frame.height

    # 网格
    for gx in range(0, 220, 20):
        p0 = projector(gx, -50)
        p1 = projector(gx, 300)
        frame.rect(p0[0], 0, p0[0] + 1, height, GRID)
    for gy in range(0, 220, 20):
        p = projector(-50, gy)
        frame.rect(0, p[1], width, p[1] + 1, GRID)

    positions = parsed._card_positions
    # 双方单位
    for side in parsed.players:
        color_unit = UNIT_A if side.player_id == player.player_id else UNIT_B
        color_work = WORK_A if side.player_id == player.player_id else WORK_B
        color_struct = STRUCT_A if side.player_id == player.player_id else STRUCT_B
        for record, ux, uy in _player_units(parsed, side.player_id, seconds, positions):
            px, py = projector(ux, uy)
            if record.is_structure:
                big = record.name in ("CommandCenter", "Nexus", "Hatchery", "Lair", "Hive",
                                      "OrbitalCommand", "PlanetaryFortress")
                size = 4 if big else 3
                frame.rect(px - size, py - size, px + size, py + size, color_struct)
                if big:
                    frame.rect(px - size - 1, py - size - 1, px + size + 1, py - size, EDGE)
                    frame.rect(px - size - 1, py + size, px + size + 1, py + size + 1, EDGE)
            elif record.name in unitdata.WORKER_UNITS:
                frame.rect(px - 1, py - 1, px + 2, py + 2, color_work)
            else:
                frame.rect(px - 2, py - 2, px + 3, py + 3, color_unit)

    # 相机框（featured player）
    camera = _camera_at(parsed, player, seconds)
    if camera:
        cx, cy = projector(camera[0], camera[1])
        half_w = 12 * projector.scale
        half_h = 9 * projector.scale
        frame.rect(cx - half_w, cy - half_h, cx + half_w, cy - half_h + 1, CAMERA)
        frame.rect(cx - half_w, cy + half_h - 1, cx + half_w, cy + half_h, CAMERA)
        frame.rect(cx - half_w, cy - half_h, cx - half_w + 1, cy + half_h, CAMERA)
        frame.rect(cx + half_w - 1, cy - half_h, cx + half_w, cy + half_h, CAMERA)

    # 最近 1.5 秒内的指令落点
    for action in parsed.actions:
        if action["u"] != player.user_id or action["k"] not in ("cmd", "cmdp", "cmdu"):
            continue
        moment = action["l"] / LOOPS_PER_SECOND
        age = seconds - moment
        if age < 0 or age > 3.0 or "x" not in action:
            continue
        px, py = projector(action["x"], action["y"])
        if age < 0.4:
            frame.cross(px, py, 6, HI)
        elif age < 1.2:
            frame.cross(px, py, 4, CMD)
        elif age < 2.2:
            frame.cross(px, py, 2, CMD)

    # 多线窗口的两条线
    if window:
        for order, cluster in enumerate(window["clusters"][:2]):
            px, py = projector(cluster["x"], cluster["y"])
            frame.ring(px, py, 20, RING)
            frame.ring(px, py, 19, RING)
            label = "AB"[order]
            text_x = int(px + 24) if px < width - 40 else int(px - 32)
            text_y = int(py - 18)
            for offset, color in ((-1, BG), (0, HI)):
                _draw_text(frame.buf, width, text_x + offset, text_y + offset, label, color)

    # 底栏
    bar_top = height - 22
    frame.rect(0, bar_top, width, height, BAR)
    clock = "%d:%02d" % (int(seconds) // 60, int(seconds) % 60)
    _draw_text(frame.buf, width, 8, bar_top + 7, clock, TEXT)
    total = max(0.001, window["t1"] - window["t0"]) if window else 1.0
    ratio = max(0.0, min(1.0, (seconds - (window["t0"] if window else seconds)) / total))
    frame.rect(70, bar_top + 9, 70 + int((width - 90) * ratio), bar_top + 14, PROGRESS)


# --------------------------------------------------------------------------- GIF 编码
def _lzw(data, min_code_size):
    clear = 1 << min_code_size
    end = clear + 1
    code_size = min_code_size + 1
    dictionary = {}
    next_code = end + 1
    out = []
    bit_buffer = 0
    bit_count = 0

    def emit(code):
        nonlocal bit_buffer, bit_count
        bit_buffer |= code << bit_count
        bit_count += code_size
        while bit_count >= 8:
            out.append(bit_buffer & 0xFF)
            bit_buffer >>= 8
            bit_count -= 8

    emit(clear)
    string = data[0]
    for byte in data[1:]:
        combined = (string << 8) | byte
        if combined in dictionary:
            string = dictionary[combined]
            continue
        emit(string)
        if next_code < 4096:
            dictionary[combined] = next_code
            next_code += 1
            if next_code == (1 << code_size) and code_size < 12:
                code_size += 1
        else:
            emit(clear)
            dictionary.clear()
            next_code = end + 1
            code_size = min_code_size + 1
        string = byte
    emit(string)
    emit(end)
    if bit_count:
        out.append(bit_buffer & 0xFF)
    return bytes(out)


def _sub_blocks(payload):
    blocks = []
    for start in range(0, len(payload), 255):
        chunk = payload[start:start + 255]
        blocks.append(bytes([len(chunk)]) + chunk)
    blocks.append(b"\x00")
    return b"".join(blocks)


def _ffmpeg_path():
    import shutil
    return shutil.which("ffmpeg")


def write_gif_ffmpeg(path, frames, palette, fps=8, mp4=None):
    """用 ffmpeg 把帧序列编码成 GIF（有 ffmpeg 时优先用它，画质和兼容性都更好）。"""
    import shutil
    import subprocess
    import tempfile

    binary = _ffmpeg_path()
    if not binary:
        return None

    # 临时帧必须写在工作区里：系统 temp 目录在沙箱下不可写
    temp_dir = os.path.splitext(os.path.abspath(path))[0] + "_frames"
    if os.path.isdir(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir)
    try:
        for index, frame in enumerate(frames):
            write_png(os.path.join(temp_dir, "f%04d.png" % index), frame, palette)
        source = os.path.join(temp_dir, "f%04d.png")
        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)

        filter_graph = ("split[a][b];[a]palettegen=max_colors=64[p];"
                        "[b][p]paletteuse=dither=bayer:bayer_scale=3")
        command = [binary, "-y", "-loglevel", "error", "-framerate", str(fps),
                   "-i", source, "-vf", filter_graph, "-loop", "0", path]
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True)
        if mp4:
            video = [binary, "-y", "-loglevel", "error", "-framerate", str(fps),
                     "-i", source, "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", mp4]
            subprocess.run(video, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True)
        return path
    except Exception:
        return None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def write_gif(path, frames, palette, delay_cs=8, width=None, height=None):
    width = width or frames[0].width
    height = height or frames[0].height
    size = max(8, 1 << (len(palette) - 1).bit_length())
    table = b"".join(bytes(color) for color in palette)
    table += b"\x00\x00\x00" * (size - len(palette))

    chunks = [b"GIF89a", struct.pack("<HHBBB", width, height, 0xF0 | (size.bit_length() - 2), 0, 0),
              table, b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"]

    for frame in frames:
        chunks.append(b"\x21\xF9\x04" + struct.pack("<H", delay_cs) + b"\x00\x00")
        chunks.append(b"\x2C" + struct.pack("<HHHH", 0, 0, width, height) + b"\x00")
        chunks.append(bytes([5]))
        chunks.append(_sub_blocks(_lzw(bytes(frame.buf), 5)))
    chunks.append(b"\x3B")

    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"".join(chunks))
    return path


# --------------------------------------------------------------------------- PNG（调试用）
def write_png(path, frame, palette):
    raw = bytearray()
    width, height = frame.width, frame.height
    table = [bytes(palette[i]) if i < len(palette) else b"\x00\x00\x00" for i in range(256)]
    for y in range(height):
        raw.append(0)
        row = frame.buf[y * width:(y + 1) * width]
        raw.extend(b"".join([table[index] for index in row]))

    import zlib
    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(bytes(raw), 6)) +
           chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(png)
    return path


# --------------------------------------------------------------------------- 组装
def pick_player(parsed, name=None, prefer_local=True):
    """按名字挑玩家；给了名字就优先按名字匹配，否则用本地玩家。"""
    if name:
        target = name.strip().lower()
        for player in parsed.players:
            if player.name.lower() == target:
                return player
        for player in parsed.players:
            if target and target in player.name.lower():
                return player
    if prefer_local:
        local = next((p for p in parsed.players if p.is_local), None)
        if local:
            return local
    return parsed.players[0] if parsed.players else None


def _find_window(parsed, player, t0, duration):
    """找和 [t0, t0+duration] 重叠、且最"满"的多线窗口。"""
    from .micro import analyze_micro

    data = analyze_micro(parsed, [player.player_id])
    windows = data["players"][0]["windows"] if data["players"] else []
    best = None
    for window in windows:
        if window["t1"] < t0 or window["t0"] > t0 + duration:
            continue
        if best is None or window["strength"] > best["strength"]:
            best = window
    return best


def build_card(replay_path, output, t0=None, duration=8.0, fps=10,
               player_name=None, width=420, height=400, window=None, mp4=False):
    """生成一张 GIF 卡片，返回 (path, info dict)。"""
    from .loader import load_replay

    parsed = load_replay(replay_path)
    player = pick_player(parsed, player_name)
    if player is None:
        raise ValueError("replay 里没有玩家")
    parsed._card_positions = _positions(parsed)

    if window is None:
        if t0 is None:
            found = _find_window(parsed, player, 0.0, parsed.duration_seconds)
            if found is None:
                raise ValueError("这局没有检测到多线窗口")
            t0 = found["t0"]
        window = _find_window(parsed, player, t0, duration)

    start = t0 if t0 is not None else (window["t0"] if window else 0.0)
    start = max(0.0, start - 0.6)  # 提前一点，让观众看清"切过去之前"
    stop = min(parsed.duration_seconds, start + duration + 0.6)

    projector = Projector(_bounds(parsed), width, height)
    palette = _palette(parsed.players[0].color if parsed.players[0].player_id == player.player_id
                       else parsed.players[1].color if len(parsed.players) > 1 else (0, 120, 255),
                       player.color)
    # 保证当前玩家用亮色
    palette[UNIT_A] = player.color
    palette[STRUCT_A] = tuple(int(c * 0.55) for c in player.color)
    for other in parsed.players:
        if other.player_id == player.player_id:
            continue
        palette[UNIT_B] = other.color
        palette[STRUCT_B] = tuple(int(c * 0.55) for c in other.color)

    frames = []
    steps = max(2, int((stop - start) * fps))
    for index in range(steps):
        moment = start + index / float(fps)
        frame = Frame(width, height)
        render_frame(parsed, player, moment, window, projector, frame, len(palette))
        frames.append(frame)

    mp4_path = os.path.splitext(output)[0] + ".mp4" if mp4 else None
    if _ffmpeg_path() and write_gif_ffmpeg(output, frames, palette, fps=fps, mp4=mp4_path):
        pass
    else:
        # 没有 ffmpeg：退化成 PNG 序列 + 一个 HTML 播放页（不用安装任何东西也能看）
        frame_dir = os.path.splitext(output)[0] + "_frames"
        os.makedirs(frame_dir, exist_ok=True)
        for index, frame in enumerate(frames):
            write_png(os.path.join(frame_dir, "f%04d.png" % index), frame, palette)
        _write_player(frame_dir, len(frames), fps, output)
    info = {
        "path": os.path.abspath(output),
        "replay": os.path.abspath(replay_path),
        "player": player.name,
        "map": parsed.map_name,
        "start": round(start, 2),
        "duration": round(stop - start, 2),
        "frames": len(frames),
        "window": ({"t0": window["t0"], "t1": window["t1"],
                    "labels": window.get("labels", []),
                    "lines": window.get("lines", []),
                    "actions": window["actions"],
                    "spread": round(window["spread"], 1)} if window else None),
        "bytes": os.path.getsize(output),
    }
    return output, info


# --------------------------------------------------------------------------- 配对卡片
def build_pair_cards(data, outdir, limit=4, duration=8.0, fps=8):
    """给 compare() 的每一个窗口配对生成 GIF 卡片 + 一个索引页。"""
    os.makedirs(outdir, exist_ok=True)
    made = []
    for index, pair in enumerate(data["pairs"][:limit], 1):
        entry = {"index": index, "pair": pair, "mine": None, "theirs": None}
        for side in ("mine", "theirs"):
            window = pair[side]
            path = window.get("path")
            if not path:
                continue
            name = "card%02d_%s_%s.gif" % (index, side, _slug(window["time"]))
            target = os.path.join(outdir, name)
            try:
                _out, info = build_card(path, target, t0=window["t0"],
                                        duration=min(duration, max(3.0, window["duration"])),
                                        fps=fps, player_name=window["player"])
                info["window_time"] = window["time"]
                info["labels"] = window.get("labels", [])
                info["lines"] = window.get("lines", [])
                info["side"] = side
                info["replay_file"] = window.get("game", "")
                info["matchup"] = window.get("matchup", "")
                entry[side] = info
            except Exception as exc:
                entry[side] = {"error": str(exc)[:160]}
        made.append(entry)

    html_path = os.path.join(outdir, "cards.html")
    _write_cards_html(data, made, html_path)
    return made, html_path


def _write_player(frame_dir, count, fps, output):
    """没有 ffmpeg 时的兜底：把 PNG 序列做成一个可播放的 HTML。"""
    import html as _html
    page = os.path.join(frame_dir, "play.html")
    text = ("<!doctype html><meta charset='utf-8'><title>卡片</title>"
            "<body style='margin:0;background:#0b0f14;text-align:center'>"
            "<img id='f' style='width:420px;image-rendering:pixelated'>"
            "<div style='color:#8aa;font:12px sans-serif;padding:6px'>空格暂停 / 左右键逐帧</div>"
            "<script>const n=" + str(count) + ",fps=" + str(fps) + ";"
            "let i=0,el=document.getElementById('f'),paused=false;"
            "function draw(){el.src='f'+String(i).padStart(4,'0')+'.png';}"
            "setInterval(()=>{if(!paused){i=(i+1)%n;draw();}},1000/fps);"
            "draw();document.addEventListener('keydown',e=>{"
            "if(e.key===' '){paused=!paused}if(e.key==='ArrowRight'){i=(i+1)%n;draw()}"
            "if(e.key==='ArrowLeft'){i=(i+n-1)%n;draw()}});</script></body>")
    with open(page, "w", encoding="utf-8") as handle:
        handle.write(text)
    return page


def _slug(text):
    return "".join(ch if ch.isalnum() else "-" for ch in str(text)).strip("-")


def _clock(seconds):
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    return "%d:%02d.%d" % (whole // 60, whole % 60, int(round((seconds - whole) * 10)) % 10)


def _write_cards_html(data, made, path):
    import html as _html

    meta = data["meta"]
    rows = []
    for entry in made:
        blocks = []
        for side, title in (("mine", "我"), ("theirs", meta["pro_name"])):
            info = entry.get(side) or {}
            if info.get("error"):
                blocks.append('<div class="card"><h3>%s</h3><div class="mut">%s</div></div>'
                              % (title, _html.escape(info["error"])))
                continue
            if not info:
                continue
            file_name = os.path.basename(info["path"])
            chips = "".join('<span class="chip">%s</span>' % _html.escape(label)
                            for label in info.get("labels", []))
            lines = "".join('<div><span class="t">%s</span>%s</div>'
                            % (_clock(item["t"]), _html.escape(item["text"]))
                            for item in (info.get("lines") or [])[:24])
            blocks.append(
                '<div class="card"><h3>%s · %s</h3>'
                '<div class="meta">%s · %s · 从 %s 开始 %ds</div>'
                '<img class="gif" src="%s" alt="%s">'
                '<div>%s</div><div class="seq">%s</div></div>'
                % (_html.escape(title), _html.escape(info["player"]),
                   _html.escape(info.get("replay_file", "")), _html.escape(info.get("matchup", "")),
                   _html.escape(info.get("window_time", "")), int(info["duration"]),
                   _html.escape(file_name), _html.escape(info["player"]), chips, lines))
        rows.append('<div class="pair"><div class="head">配对 %d · 距离 %.2f</div>'
                    '<div class="cols">%s</div></div>'
                    % (entry["index"], entry["pair"]["distance"], "".join(blocks)))

    style = (
        "body{background:#0e1116;color:#e6ecf5;font:15px/1.6 'Segoe UI','Microsoft YaHei',sans-serif;"
        "max-width:1200px;margin:0 auto;padding:24px 20px 60px}"
        "h1{font-size:26px}.sub{color:#93a1b5;font-size:13.5px;margin-bottom:18px}"
        ".pair{border:1px solid #2a3340;border-radius:14px;padding:14px;margin-bottom:16px;background:#151a22}"
        ".head{color:#93a1b5;font-size:12.5px;margin-bottom:10px}"
        ".cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}"
        ".card{background:#1a2029;border:1px solid #2a3340;border-radius:12px;padding:12px}"
        "h3{font-size:14px;margin:0 0 4px}.meta{color:#93a1b5;font-size:12px;margin-bottom:8px}"
        ".gif{width:100%;height:auto;border-radius:10px;display:block;margin-bottom:8px;background:#0b0f14}"
        ".chip{display:inline-block;padding:1px 8px;border-radius:6px;background:#1d2740;"
        "border:1px solid #2c3c60;font-size:12px;margin:0 4px 4px 0}"
        ".seq{font-size:12px;color:#c2cfe0;background:#141a23;border:1px solid #222b38;border-radius:10px;"
        "padding:8px;margin-top:8px;max-height:240px;overflow:auto;font-variant-numeric:tabular-nums}"
        ".seq .t{color:#7d8ca3;display:inline-block;width:64px}"
        ".mut{color:#93a1b5}"
        "@media(max-width:900px){.cols{grid-template-columns:1fr}}"
    )
    text = ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>多线模板卡片</title><style>" + style + "</style></head><body>"
            "<h1>多线模板卡片</h1>"
            "<div class=\"sub\">对位 " + _html.escape(meta["matchup"]) + " · 我 vs "
            + _html.escape(meta["pro_name"]) + " · 左边是我，右边是对方；GIF 是当时的小地图重放"
            "（白框=视角，金十字=刚下的指令，红圈=这一波的两条线）</div>"
            + "".join(rows) + "</body></html>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path
