"""从教学视频里批量抽帧：宫格图（看画面标注）+ 时钟条（读游戏内计时器）。

为什么自己写：视频插图工具一次只出一张图、且按镜头切分；我们需要的是
「固定间隔取样 + 把很多帧拼成一张能读的图」，用 ffmpeg 的 tile 滤镜最省事。

用法::

    # 宫格：每 6 秒取一帧，3x3 拼一张，输出到 .tmp/frames/BVxxx/
    python tools\\video_frames.py input.mp4 --start 45 --end 400 --step 6 --out .tmp\\frames\\BVxxx

    # 时钟条：把右上角计时器裁出来放大，12 条拼一张
    python tools\\video_frames.py input.mp4 --start 0 --end 400 --step 8 --out .tmp\\frames\\BVxxx --mode clock
"""

import argparse
import os
import shutil
import subprocess
import sys


def ffmpeg_path():
    found = shutil.which("ffmpeg")
    if not found:
        raise SystemExit("找不到 ffmpeg，请先安装（winget install Gyan.FFmpeg）")
    return found


def run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-2000:])
        raise SystemExit("ffmpeg 失败（exit %d）" % result.returncode)
    return result.stdout


def grid(input_path, out_dir, start, end, step, cols, rows, width,
         clock_box=None, clock_scale=3):
    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(out_dir):
        if name.startswith("grid-"):
            os.remove(os.path.join(out_dir, name))
    pattern = os.path.join(out_dir, "grid-%03d.png")

    base = "fps=1/%s" % step
    if clock_box:
        x, y, w, h = clock_box
        chain = ("[0:v]%s,split=2[base][clk];"
                 "[clk]crop=%d:%d:%d:%d,scale=iw*%s:ih*%s[clock];"
                 "[base][clock]overlay=6:6,scale=%d:-2,tile=%dx%d"
                 % (base, w, h, x, y, clock_scale, clock_scale, width, cols, rows))
    else:
        chain = "[0:v]%s,scale=%d:-2,tile=%dx%d" % (base, width, cols, rows)

    run([ffmpeg_path(), "-y", "-ss", str(start), "-to", str(end), "-i", input_path,
         "-filter_complex" if clock_box else "-vf", chain,
         "-frames:v", "60", pattern])
    made = sorted(name for name in os.listdir(out_dir) if name.startswith("grid-"))
    return [os.path.join(out_dir, name) for name in made]


def clock(input_path, out_dir, start, end, step, width, height, x, y, scale, per_sheet):
    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(out_dir):
        if name.startswith("clock-"):
            os.remove(os.path.join(out_dir, name))
    pattern = os.path.join(out_dir, "clock-%03d.png")
    filters = ("fps=1/%s,crop=%d:%d:%d:%d,scale=iw*%s:ih*%s,tile=1x%d"
               % (step, width, height, x, y, scale, scale, per_sheet))
    run([ffmpeg_path(), "-y", "-ss", str(start), "-to", str(end), "-i", input_path,
         "-vf", filters, "-frames:v", "40", pattern])
    made = sorted(name for name in os.listdir(out_dir) if name.startswith("clock-"))
    return [os.path.join(out_dir, name) for name in made]


def probe_size(input_path):
    out = run([ffmpeg_path(), "-hide_banner", "-i", input_path])
    for line in out.splitlines():
        if "Video:" in line and "x" in line:
            for token in line.replace(",", " ").split():
                if "x" in token and token.split("x")[0].isdigit():
                    w, h = token.split("x")[:2]
                    if h.isdigit():
                        return int(w), int(h)
    return 1280, 720


def main(argv=None):
    parser = argparse.ArgumentParser(description="教学视频抽帧：宫格图 / 时钟条")
    parser.add_argument("input")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=None)
    parser.add_argument("--step", type=float, default=6.0, help="每隔多少秒取一帧")
    parser.add_argument("--mode", choices=("grid", "clock"), default="grid")
    parser.add_argument("--cols", type=int, default=3)
    parser.add_argument("--rows", type=int, default=3)
    parser.add_argument("--width", type=int, default=640, help="宫格单帧宽度")
    # 时钟条参数：默认取右上角（1280x720 下的经验值）
    parser.add_argument("--crop-w", type=int, default=190)
    parser.add_argument("--crop-h", type=int, default=38)
    parser.add_argument("--crop-x", type=int, default=1080)
    parser.add_argument("--crop-y", type=int, default=2)
    parser.add_argument("--scale", type=float, default=3.0)
    parser.add_argument("--per-sheet", type=int, default=12)
    parser.add_argument("--clock-box", default=None,
                        help="宫格模式下把时钟区域放大贴到每帧左上角，格式 x,y,w,h")
    parser.add_argument("--clock-scale", type=float, default=3.0)
    args = parser.parse_args(argv)

    end = args.end
    if end is None:
        end = 10 ** 6
    if args.mode == "grid":
        clock_box = None
        if args.clock_box:
            clock_box = tuple(int(part) for part in args.clock_box.split(","))
            if len(clock_box) != 4:
                raise SystemExit("--clock-box 需要 x,y,w,h")
        files = grid(args.input, args.out, args.start, end, args.step,
                     args.cols, args.rows, args.width,
                     clock_box=clock_box, clock_scale=args.clock_scale)
        print("%d 张宫格图（每张 %dx%d 帧，间隔 %gs）" % (len(files), args.cols, args.rows, args.step))
    else:
        files = clock(args.input, args.out, args.start, end, args.step,
                      args.crop_w, args.crop_h, args.crop_x, args.crop_y,
                      args.scale, args.per_sheet)
        print("%d 张时钟条（每张 %d 条，间隔 %gs）" % (len(files), args.per_sheet, args.step))
    for path in files:
        print("   ", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
