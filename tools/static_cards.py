"""挑出视频里的「静止画面」（标题卡 / 步骤总结 / 片头片尾），拼成一张图。

为什么这个能行：UP 主常把整局 BO 的步骤列表做成一张静态卡（有时还带游戏内时间点，
例如「3、后续正常运营（前期可以在 3:55 偷 3 基地）」）。这类卡片连续几秒不动，
用「逐秒整帧哈希不变」就能稳定挑出来，比逐帧读整屏便宜一个数量级。

    python tools\\static_cards.py video.mp4 --out .tmp\\cards\\BVxxx --rows 4
"""

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys

W, H = 320, 180


def ffmpeg():
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit("找不到 ffmpeg")
    return path


def probe(input_path):
    out = subprocess.run([ffmpeg(), "-hide_banner", "-i", input_path],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace").stdout
    for line in out.splitlines():
        if "Video:" in line:
            for token in line.replace(",", " ").split():
                parts = token.split("x")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    return int(parts[0]), int(parts[1])
    return 1280, 720


def frames(input_path, fps=1.0):
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", input_path,
           "-vf", "fps=%s,scale=%d:%d,format=gray" % (fps, W, H),
           "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = W * H
    while True:
        chunk = proc.stdout.read(size)
        if len(chunk) < size:
            break
        yield chunk
    proc.stdout.close()
    proc.wait()


def similar(a, b, tolerance=6):
    """低分辨率下比较两帧：采样 1/16 像素，平均差小于 tolerance 认为几乎一样。"""
    total = 0
    count = 0
    for index in range(0, len(a), 16):
        total += abs(a[index] - b[index])
        count += 1
    return count > 0 and (total / float(count)) <= tolerance


def extract_frame(input_path, out_png, seconds, width):
    vf = "scale=%d:-2" % width
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", "%.2f" % seconds,
                    "-i", input_path, "-vf", vf, "-frames:v", "1", out_png],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def write_png_rgb(path, width, height, rgb):
    import zlib

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * stride:(y + 1) * stride])

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(bytes(raw), 6)) +
           chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(png)


def stack(items, out_dir, rows):
    sheets = []
    for start in range(0, len(items), rows):
        group = items[start:start + rows]
        width = max(item[1] for item in group)
        height = sum(item[2] for item in group)
        canvas = bytearray(width * height * 3)
        offset = 0
        for png, cw, ch in group:
            raw = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", png,
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                 stdout=subprocess.PIPE, check=True).stdout
            for y in range(ch):
                row = raw[y * cw * 3:(y + 1) * cw * 3]
                start_byte = (offset + y) * width * 3
                canvas[start_byte:start_byte + len(row)] = row
            offset += ch
        path = os.path.join(out_dir, "cards-%03d.png" % (start // rows + 1))
        write_png_rgb(path, width, height, canvas)
        sheets.append(path)
    return sheets


def main(argv=None):
    parser = argparse.ArgumentParser(description="挑出静止画面（标题/步骤总结卡）")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-hold", type=float, default=1.2, help="至少静止几秒")
    parser.add_argument("--rows", type=int, default=4, help="每张图拼几张卡")
    parser.add_argument("--width", type=int, default=760, help="卡片宽度")
    parser.add_argument("--text-only", dest="text_only", action="store_true",
                        help="只保留「有文字」的卡（亮像素数量过滤）")
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    groups = []
    current = None
    previous = None
    for index, frame in enumerate(frames(args.input)):
        if previous is not None and similar(previous, frame):
            current["end"] = index
        else:
            if current is not None:
                groups.append(current)
            current = {"start": index, "end": index}
        previous = frame
        current["bright"] = sum(1 for value in frame if value >= 210)
    if current is not None:
        groups.append(current)

    groups = [g for g in groups if (g["end"] - g["start"] + 1) / 1.0 >= args.min_hold]
    before = len(groups)
    if args.text_only:
        groups = [g for g in groups if 200 <= g.get("bright", 0) <= 4000]
    print("静止段 %d 个（>= %ss，文字过滤后 %d）" % (before, args.min_hold, len(groups)))

    src_w, src_h = probe(args.input)
    items = []
    for index, group in enumerate(groups):
        middle = (group["start"] + group["end"]) / 2.0
        png = os.path.join(args.out, "card%03d.png" % index)
        extract_frame(args.input, png, middle, args.width)
        items.append((png, args.width, int(args.width * src_h / float(src_w))))
        print("  t=%5.0fs  %s" % (middle, os.path.basename(png)))
    if not items:
        return 0
    for path in stack(items, args.out, args.rows):
        print("拼图", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
