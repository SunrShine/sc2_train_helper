"""按固定横带 + 变化检测挑帧：一条视频只出 1~2 张「标注条」拼图。

思路：UP 主的步骤标注基本固定在某一条横带里（每期位置略不同）。所以：
1. 1 fps 解码成 320x180 灰度；
2. 只取指定横带，逐秒算哈希；
3. 哈希变化 = 换了新标注 → 取这一段的中帧；
4. 回到原分辨率裁这条横带、拼成一张竖图。

    python tools\\annot_band.py video.mp4 --out .tmp\\band\\BVxxx --band 0.50,0.80

比逐帧读整屏省一个数量级：一条 6 分钟视频通常只有 15~25 条标注。
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


def band_hash(frame, y0, y1, threshold=200):
    """只对「亮像素的形状」做哈希：标注文字亮、背景暗，形状会在几秒内保持稳定。"""
    start = y0 * W
    end = y1 * W
    band = frame[start:end]
    mask = bytes(1 if value >= threshold else 0 for value in band)
    return hashlib.md5(mask).hexdigest()


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


def crop_band(input_path, out_png, seconds, src_w, src_h, y0, y1, scale):
    top = int(y0 * src_h)
    bottom = max(top + 4, int(y1 * src_h))
    height = bottom - top
    height -= height % 2
    vf = "crop=%d:%d:0:%d,scale=iw*%s:ih*%s" % (src_w, height, top, scale, scale)
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", "%.2f" % seconds,
                    "-i", input_path, "-vf", vf, "-frames:v", "1", out_png],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return int(src_w * scale), int(height * scale)


def main(argv=None):
    parser = argparse.ArgumentParser(description="按横带变化挑标注帧并拼图")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    parser.add_argument("--band", default="0.50,0.80", help="y0,y1 占画面高度的比例")
    parser.add_argument("--rows", type=int, default=12, help="每张拼几条")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--min-hold", type=float, default=1.0, help="标注至少持续几秒才收")
    args = parser.parse_args(argv)

    y0, y1 = [float(part) for part in args.band.split(",")]
    top = int(y0 * H)
    bottom = int(y1 * H)
    os.makedirs(args.out, exist_ok=True)

    groups = []
    current = None
    for index, frame in enumerate(frames(args.input)):
        digest = band_hash(frame, top, bottom)
        if current is None or digest != current["hash"]:
            if current is not None:
                groups.append(current)
            current = {"hash": digest, "start": index, "end": index}
        else:
            current["end"] = index
    if current is not None:
        groups.append(current)

    groups = [g for g in groups if (g["end"] - g["start"]) >= args.min_hold]
    print("横带 %s 上检测到 %d 段（过滤持续 < %ss 的）" % (args.band, len(groups), args.min_hold))

    src_w, src_h = probe(args.input)
    crops_dir = os.path.join(args.out, "crops")
    os.makedirs(crops_dir, exist_ok=True)
    items = []
    for index, group in enumerate(groups):
        middle = (group["start"] + group["end"]) / 2.0
        png = os.path.join(crops_dir, "b%03d.png" % index)
        cw, ch = crop_band(args.input, png, middle, src_w, src_h, y0, y1, args.scale)
        items.append((png, cw, ch, middle))
        print("  t=%5.0fs  %dx%d" % (middle, cw, ch))

    rows = args.rows
    for sheet_index in range(0, len(items), rows):
        group = items[sheet_index:sheet_index + rows]
        width = max(item[1] for item in group)
        height = sum(item[2] for item in group)
        canvas = bytearray(width * height * 3)
        offset = 0
        for png, cw, ch, _t in group:
            raw = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", png,
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                 stdout=subprocess.PIPE, check=True).stdout
            for y in range(ch):
                row = raw[y * cw * 3:(y + 1) * cw * 3]
                start = (offset + y) * width * 3
                canvas[start:start + len(row)] = row
            offset += ch
        path = os.path.join(args.out, "band-%03d.png" % (sheet_index // rows + 1))
        write_png_rgb(path, width, height, canvas)
        print("拼图 %s (%dx%d)" % (path, width, height))
    return 0


if __name__ == "__main__":
    sys.exit(main())
