"""挑出「整屏文字卡」（UP 主的 运营:/思路: 总结卡），每部视频只留最重要的几张。

为什么要单独挑：
- 总结卡是**整屏都是大字**（不是游戏画面），文字行数多、跨度宽；
- 它通常只出现几秒，而且常在视频最后；
- 一张这样的卡往往写着完整建造顺序（例如「BS兵营气矿气矿VF死神星轨…」），
  信息量远高于逐帧阅读。

判据（在 320x180 灰度上算）：
1. 亮像素（>=205）占比在 2%~25% 之间（纯白闪屏会被排除）；
2. 亮像素分布在 >=4 个不同的「文字行」上，且行的宽度跨度 >= 画面宽度 55%
   —— 这一条是为了排除「游戏画面里一小块白字横幅」。

    python tools\\summary_cards.py video.mp4 --out .tmp\\summary\\BVxxx
"""

import argparse
import os
import shutil
import struct
import subprocess
import sys

W, H = 320, 180
MIN_HOLD = 1.5


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


def frames(input_path, fps=1.0, start=0.0):
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", input_path,
            "-vf", "fps=%s,scale=%d:%d,format=rgb24" % (fps, W, H),
            "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = W * H * 3
    while True:
        chunk = proc.stdout.read(size)
        if len(chunk) < size:
            break
        yield chunk
    proc.stdout.close()
    proc.wait()


def is_colored(r, g, b):
    """运营/思路 卡片用的是红字和黄字；纯白字幕不算。"""
    if r > 140 and g < 110 and b < 110:
        return True
    if r > 175 and g > 165 and b < 110:
        return True
    return False


def text_rows(frame):
    """返回 [(y, x0, x1)]：把「红/黄字」像素按行归并成文字行。"""
    rows = []
    for y in range(H):
        count = 0
        first = None
        last = None
        for x in range(W):
            offset = (y * W + x) * 3
            r, g, b = frame[offset], frame[offset + 1], frame[offset + 2]
            if is_colored(r, g, b):
                count += 1
                if first is None:
                    first = x
                last = x
        if count >= 4 and first is not None and last is not None:
            if rows and y - rows[-1][0] <= 3:
                rows[-1] = (rows[-1][0], min(rows[-1][1], first), max(rows[-1][2], last))
            else:
                rows.append((y, first, last))
    return rows


def score(frame):
    """返回 (是否像文字卡, 行数, 彩色文字占比, 行宽跨度)。"""
    colored = 0
    for offset in range(0, len(frame), 3):
        if is_colored(frame[offset], frame[offset + 1], frame[offset + 2]):
            colored += 1
    ratio = colored / float(W * H)
    rows = text_rows(frame)
    if ratio < 0.015 or ratio > 0.30:
        return False, len(rows), ratio, 0.0
    if len(rows) < 4:
        return False, len(rows), ratio, 0.0
    span = (max(r[2] for r in rows) - min(r[1] for r in rows)) / float(W)
    return span >= 0.55, len(rows), ratio, span


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


def extract_frame(input_path, out_png, seconds, width):
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", "%.2f" % seconds,
                    "-i", input_path, "-vf", "scale=%d:-2" % width,
                    "-frames:v", "1", out_png],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def stack(items, out_path, rows):
    width = max(item[1] for item in items)
    height = sum(item[2] for item in items[:rows])
    canvas = bytearray(width * height * 3)
    offset = 0
    for png, cw, ch in items[:rows]:
        raw = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", png,
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             stdout=subprocess.PIPE, check=True).stdout
        for y in range(ch):
            row = raw[y * cw * 3:(y + 1) * cw * 3]
            start = (offset + y) * width * 3
            canvas[start:start + len(row)] = row
        offset += ch
    write_png_rgb(out_path, width, height, canvas)


def main(argv=None):
    parser = argparse.ArgumentParser(description="挑出整屏文字卡（运营/思路总结）")
    parser.add_argument("input")
    parser.add_argument("--out", required=True)
    parser.add_argument("--top", type=int, default=4, help="每部最多保留几张")
    parser.add_argument("--width", type=int, default=700)
    parser.add_argument("--start", type=float, default=0.0, help="从第几秒开始扫")
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    hits = []
    current = None
    for index, frame in enumerate(frames(args.input, start=args.start)):
        ok, rows, ratio, span = score(frame)
        if ok:
            if current is None:
                current = {"start": index, "end": index, "score": (rows, ratio, span)}
            else:
                current["end"] = index
                if (rows, ratio) > current["score"][:2]:
                    current["score"] = (rows, ratio, span)
        elif current is not None:
            hits.append(current)
            current = None
    if current is not None:
        hits.append(current)

    hits = [hit for hit in hits if (hit["end"] - hit["start"] + 1) >= MIN_HOLD]
    hits.sort(key=lambda hit: (-hit["score"][0], -hit["score"][2]))
    hits = hits[:args.top]
    print("文字卡候选 %d 张" % len(hits))

    src_w, src_h = probe(args.input)
    items = []
    for order, hit in enumerate(hits):
        middle = (hit["start"] + hit["end"]) / 2.0 + args.start
        png = os.path.join(args.out, "sum%02d.png" % order)
        extract_frame(args.input, png, middle, args.width)
        items.append((png, args.width, int(args.width * src_h / float(src_w))))
        print("  t=%5.0fs 行=%d 占比=%.3f 跨度=%.2f  %s"
              % (middle, hit["score"][0], hit["score"][1], hit["score"][2],
                 os.path.basename(png)))
    if items:
        out_path = os.path.join(args.out, "summary.png")
        stack(items, out_path, len(items))
        print("拼图", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
