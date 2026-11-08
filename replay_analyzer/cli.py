"""命令行入口。

用法::

    python -m replay_analyzer analyze "对局.SC2Replay"
    python -m replay_analyzer analyze "对局.SC2Replay" -o report.html --json data.json --open
    python -m replay_analyzer batch "Replays\\Multiplayer" -o reports
    python -m replay_analyzer dump "对局.SC2Replay" -o raw.json
    python -m replay_analyzer fog "对局.SC2Replay" --gif
"""

import argparse
import glob
import json
import os
import sys
import time

from . import (__version__, card, compare, compare_report, fog, fog_report, fogrender,
               library, micro, micro_report, metrics, report)
from .loader import load_replay


def _safe_stdout():
    """Windows 下把输出重定向到文件/管道时，避免中文因为 GBK 编码报错。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _default_output(replay_path, out_dir="reports"):
    base = os.path.splitext(os.path.basename(replay_path))[0]
    safe = "".join(ch if ch.isalnum() or ch in "-_. " else "_" for ch in base).strip()
    return os.path.join(out_dir, (safe or "replay") + ".html")


def build(replay_path, output=None, json_path=None, open_after=False, quiet=False):
    started = time.time()
    parsed = load_replay(replay_path)
    analysis = metrics.analyze(parsed)
    if output is None:
        output = _default_output(replay_path)
    report.write_report(analysis, output)

    if json_path:
        directory = os.path.dirname(os.path.abspath(json_path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(analysis, handle, ensure_ascii=False, indent=2)

    if not quiet:
        _print_summary(analysis, output, time.time() - started)

    if open_after:
        _open_in_browser(output)
    return analysis, output


def _print_summary(analysis, output, elapsed):
    meta = analysis["meta"]
    print("=" * 60)
    print("地图    : %s" % meta["map"])
    print("时长    : %s   速度: %s   版本: %s" % (meta["duration"], meta["game_speed"],
                                                  meta.get("version")))
    for player in analysis["players"]:
        print("玩家    : %-14s %-3s %s   APM %.0f   农民峰值 %d   军队峰值 %s" % (
            player["name"], player["race_name"], player["result_zh"],
            player["apm"], player["workers_max"], int(player["army_peak"])))
    print("结论    :")
    for insight in analysis["insights"][:6]:
        print("  - [%s] %s" % (insight["title"], insight["text"]))
    print("报告    : %s" % os.path.abspath(output))
    print("耗时    : %.1fs" % elapsed)
    print("=" * 60)


def _print_micro(data, output):
    meta = data["meta"]
    print("=" * 68)
    print("操作细节  : %s · %s" % (meta["map"], meta["duration"]))
    for player in data["players"]:
        print("-" * 68)
        print("%s（%s）%s%s" % (player["name"], player["race"], player["result_zh"],
                                "  ← 你" if player["is_local"] else ""))
        print("  视角   : %.1f 次/分 · 单次停留 %.1fs（最长 %.1fs）"
              % (player["cam_switches_per_min"], player["cam_dwell_median"],
                 player["cam_dwell_max"]))
        print("  指令   : %.1f 条/分（共 %d 条）"
              % (player["cmd_per_min"], player["cmd_count"]))
        print("  多线   : %d 个窗口 · %.2f 个/分 · 覆盖 %.1f%% 时间"
              % (player["window_count"], player["windows_per_min"],
                 player["window_share"]))
        print("  空档   : 不下指令合计 %.0f 秒（≥8s）· 完全停手 %.0f 秒"
              % (player["cmd_gap_total"], player["gap_total"]))
        if player["reaction_median"] is not None:
            print("  反应   : 单位阵亡后第一次指令的中位延迟 %.1fs" % player["reaction_median"])
        if player["windows"]:
            print("  多线窗口（前 3 个）:")
            for window in player["windows"][:3]:
                print("    %s  %ds  %s  指令 %d" % (
                    window["time"], int(round(window["duration"])),
                    " ↔ ".join(window["labels"]), window["actions"]))
                for line in window["lines"][:6]:
                    print("        %5.1fs  %s" % (line["t"], line["text"]))
    print("-" * 68)
    print("报告    : %s" % os.path.abspath(output))
    print("=" * 68)


def _print_trend(rows, output):
    print("=" * 60)
    print("操作指标趋势：%d 局" % len(rows))
    if rows:
        def avg(key):
            values = [r[key] for r in rows if r.get(key) is not None]
            return sum(values) / len(values) if values else 0
        print("  视角切换 : %.1f 次/分（单次停留 %.1fs）"
              % (avg("cam_switches_per_min"), avg("cam_dwell_median")))
        print("  多线窗口 : %.2f 个/分 · 覆盖 %.1f%%"
              % (avg("windows_per_min"), avg("window_share")))
        print("  不下指令 : %.0f 秒/局 · 指令 %.1f 条/分"
              % (avg("cmd_gap_total"), avg("cmd_per_min")))
        print("  用时     : %s → %s" % (rows[0]["date"], rows[-1]["date"]))
    print("报告    : %s" % os.path.abspath(output))
    print("=" * 60)


def _print_compare(data, output):
    meta = data["meta"]
    me_group, pro_group = data["groups"]
    print("=" * 68)
    print("对照组    : 我 vs %s（%s）" % (meta["pro_name"], meta["matchup"]))
    print("样本      : 我 %d 局 · 对方 %d 局" % (len(me_group["rows"]), len(pro_group["rows"])))
    if data["verdict"]:
        for line in data["verdict"]:
            print("  [%s] %s" % (line["level"], line["text"]))
    if data["pairs"]:
        print("-" * 68)
        print("窗口配对（结构最像的）：")
        for pair in data["pairs"][:3]:
            print("  我 %s（%s）  ↔  %s %s（%s）  距离 %.2f"
                  % (pair["mine"]["time"], pair["my_shape"], meta["pro_name"],
                     pair["theirs"]["time"], pair["their_shape"], pair["distance"]))
    print("报告      : %s" % os.path.abspath(output))
    print("=" * 68)


def _parse_clock(text):
    """'16:47' -> 1007.0"""
    parts = str(text).strip().replace(".", ":").split(":")
    try:
        numbers = [int(part) for part in parts if part != ""]
    except ValueError:
        return 0.0
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def _fog_moment(result):
    """自动挑一个适合做动画的时刻：优先最“满”的暴露窗口。"""
    windows = result.get("exposed_windows") or []
    if windows:
        best = max(windows, key=lambda w: (w["dur"], w.get("n", 0)))
        return max(0.0, best["t0"] - 4.0)
    if result.get("first_contact") is not None:
        return max(0.0, result["first_contact"] - 8.0)
    return 0.0


def _print_fog(result, output, gif=None):
    me = result["me"]
    opp = result["opponent"]
    print("=" * 68)
    print("迷雾复盘  : %s vs %s（%s）" % (me["name"], opp["name"], result["meta"]["map"]))
    print("被看见    : 我的部队 %s · 他的部队 %s"
          % ("%.1f%%" % (result["army"]["ratio"] * 100),
             "%.1f%%" % (result["theirs_army"]["ratio"] * 100)))
    print("视野覆盖  : 我 %s · 他 %s"
          % ("%.1f%%" % (result["vision"]["mine_coverage"] * 100),
             "%.1f%%" % (result["vision"]["theirs_coverage"] * 100)))
    if result.get("first_contact") is not None:
        moment = result["first_contact"]
        print("第一次被看到: %d:%02d" % (int(moment) // 60, int(moment) % 60))
    blind = result.get("blind_windows") or []
    if blind:
        total = sum(w["dur"] for w in blind)
        print("盲区窗口  : %d 段，共 %d:%02d" % (len(blind), int(total) // 60, int(total) % 60))
    if gif:
        print("动画      : %s" % os.path.abspath(gif))
    print("报告      : %s" % os.path.abspath(output))
    print("=" * 68)


def _open_in_browser(path):
    path = os.path.abspath(path)
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606 (Windows 专用)
        elif sys.platform == "darwin":
            os.system('open "%s"' % path)
        else:
            os.system('xdg-open "%s"' % path)
    except Exception as exc:
        print("自动打开失败，请手动打开：%s (%s)" % (path, exc))


def _iter_replays(target):
    target = os.path.abspath(target)
    if os.path.isfile(target):
        yield target
        return
    pattern = os.path.join(target, "**", "*.SC2Replay")
    for path in sorted(glob.glob(pattern, recursive=True)):
        yield path


def main(argv=None):
    _safe_stdout()
    parser = argparse.ArgumentParser(
        prog="replay_analyzer",
        description="星际争霸2 对局分析工具：解析 .SC2Replay 并生成 HTML 报告",
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = parser.add_subparsers(dest="command")

    analyze_cmd = sub.add_parser("analyze", help="分析单个 replay，生成 HTML 报告")
    analyze_cmd.add_argument("replay", help=".SC2Replay 文件路径")
    analyze_cmd.add_argument("-o", "--output", help="HTML 输出路径")
    analyze_cmd.add_argument("--json", dest="json_path", help="同时导出 JSON")
    analyze_cmd.add_argument("--open", dest="open_after", action="store_true",
                             help="生成后自动用浏览器打开")

    batch_cmd = sub.add_parser("batch", help="批量分析一个目录下的所有 replay")
    batch_cmd.add_argument("target", help="目录或文件")
    batch_cmd.add_argument("-o", "--outdir", default="reports", help="报告输出目录")
    batch_cmd.add_argument("--open", dest="open_after", action="store_true",
                           help="生成后打开索引页")

    micro_cmd = sub.add_parser("micro", help="操作细节分析：多线重叠、视角节奏、操作顺序")
    micro_cmd.add_argument("replay", help=".SC2Replay 文件路径，或一个目录（批量看趋势）")
    micro_cmd.add_argument("-o", "--output", help="HTML 输出路径")
    micro_cmd.add_argument("--json", dest="json_path", help="同时导出 JSON")
    micro_cmd.add_argument("--limit", type=int, default=40, help="批量模式下最多分析多少局")
    micro_cmd.add_argument("--player", type=int, default=None,
                           help="只看某个 playerId（默认双方都看）")
    micro_cmd.add_argument("--open", dest="open_after", action="store_true",
                           help="生成后自动用浏览器打开")

    library_cmd = sub.add_parser("library", help="扫描录像目录，生成归类索引（只读不改文件）")
    library_cmd.add_argument("--me", required=True, help="我的录像目录")
    library_cmd.add_argument("--pro", action="append", default=[], metavar="DIR",
                             help="职业选手目录（可重复；每个子文件夹视为一个选手）")
    library_cmd.add_argument("-o", "--output", default=os.path.join("reports", "library.html"),
                             help="HTML 输出路径")
    library_cmd.add_argument("--json", dest="json_path", help="同时导出 JSON")

    compare_cmd = sub.add_parser("compare", help="对照组：我 vs 职业选手（多线/视角指标 + 窗口配对）")
    compare_cmd.add_argument("--me", required=True, help="我的录像目录")
    compare_cmd.add_argument("--pro", required=True, help="职业选手录像目录")
    compare_cmd.add_argument("--matchup", default=None, help="只比对位，例如 TvZ / ZvP")
    compare_cmd.add_argument("--limit", type=int, default=30, help="每组最多分析多少局")
    compare_cmd.add_argument("--me-name", dest="me_name", default=None, help="我在 replay 里的名字")
    compare_cmd.add_argument("--pro-name", dest="pro_name", default=None, help="对方在 replay 里的名字")
    compare_cmd.add_argument("--pairs", type=int, default=4, help="窗口配对数量")
    compare_cmd.add_argument("--cards", default=None, metavar="DIR",
                             help="给每个配对各生成一张 GIF 卡片，输出到这个目录")
    compare_cmd.add_argument("-o", "--output", default=os.path.join("reports", "compare.html"),
                             help="HTML 输出路径")
    compare_cmd.add_argument("--json", dest="json_path", help="同时导出 JSON")
    compare_cmd.add_argument("--open", dest="open_after", action="store_true",
                             help="生成后自动用浏览器打开")

    dump_cmd = sub.add_parser("dump", help="导出解析后的原始结构（调试用）")
    dump_cmd.add_argument("replay", help=".SC2Replay 文件路径")
    dump_cmd.add_argument("-o", "--output", help="JSON 输出路径")

    card_cmd = sub.add_parser("card", help="把一段多线操作渲染成 GIF 卡片（练习模板）")
    card_cmd.add_argument("replay", help=".SC2Replay 文件路径")
    card_cmd.add_argument("--player", default=None, help="要展示谁（名字，可模糊匹配）")
    card_cmd.add_argument("--at", default=None, help="开始时间，例如 16:47；不给就自动挑最满的窗口")
    card_cmd.add_argument("--duration", type=float, default=8.0, help="时长（秒）")
    card_cmd.add_argument("--fps", type=int, default=8, help="帧率")
    card_cmd.add_argument("-o", "--output", default=None, help="GIF 输出路径")
    card_cmd.add_argument("--mp4", action="store_true", help="同时导出 mp4（有 ffmpeg 时）")
    card_cmd.add_argument("--open", dest="open_after", action="store_true",
                          help="生成后用默认程序打开")

    fog_cmd = sub.add_parser("fog", help="战争迷雾复盘：还原双方视野，量出被看见的时间")
    fog_cmd.add_argument("replay", help=".SC2Replay 文件路径")
    fog_cmd.add_argument("--me", default=None,
                         help="以谁的视角看（名字，可模糊匹配；默认本地玩家）")
    fog_cmd.add_argument("--opponent", default=None, help="指定对手（名字，可模糊匹配）")
    fog_cmd.add_argument("--sight-scale", dest="sight_scale", type=float, default=1.0,
                         help="视野半径整体缩放，做敏感度检查（例如 1.15）")
    fog_cmd.add_argument("--gif", nargs="?", const="", default=None, metavar="PATH",
                         help="同时生成迷雾动画 GIF（不给路径就用默认位置）")
    fog_cmd.add_argument("--at", default=None, help="动画起点，例如 14:10")
    fog_cmd.add_argument("--duration", type=float, default=24.0, help="动画时长（秒）")
    fog_cmd.add_argument("--fps", type=int, default=6, help="动画帧率")
    fog_cmd.add_argument("--reveal", action="store_true",
                         help="把看不见的敌人也画成灰点（教学用，默认和真实客户端一样隐藏）")
    fog_cmd.add_argument("-o", "--output", help="HTML 输出路径")
    fog_cmd.add_argument("--json", dest="json_path", help="同时导出 JSON")
    fog_cmd.add_argument("--open", dest="open_after", action="store_true",
                         help="生成后自动用浏览器打开")

    # ---- 经济 / 建造顺序 / DAG：参数转发给各自模块的 main()
    econ_cmd = sub.add_parser("econ", help="经济模型：账本对账 + 收入拟合 + 采矿效率")
    econ_cmd.add_argument("--limit", type=int, default=90, help="我的录像抽样局数")
    econ_cmd.add_argument("--replay-dir", dest="replay_dir", default=None, help="我的录像目录")
    econ_cmd.add_argument("--pro-dir", dest="pro_dir", default=None, help="职业录像目录")
    econ_cmd.add_argument("--game", dest="game", default=None, help="重点展示哪一局（文件名片段）")
    econ_cmd.add_argument("-o", "--output", dest="econ_out", default=None, help="HTML 输出路径")
    econ_cmd.add_argument("--json", dest="json_path", default=None, help="JSON 输出路径")
    econ_cmd.add_argument("--open", dest="open_after", action="store_true",
                          help="生成后自动用浏览器打开")

    bo_cmd = sub.add_parser("bo", help="建造顺序：按时间线分段 + 开局分类 + 我 vs 职业对照")
    bo_cmd.add_argument("--mine", default=None, help="我的录像目录")
    bo_cmd.add_argument("--pro", default=None, help="职业选手录像目录")
    bo_cmd.add_argument("--limit", type=int, default=60, help="我的录像抽样局数")
    bo_cmd.add_argument("--pro-limit", dest="pro_limit", type=int, default=30, help="职业录像局数")
    bo_cmd.add_argument("--matchup", default="TvZ", help="只比对位，例如 TvZ / TvP")
    bo_cmd.add_argument("--seed", type=int, default=None, help="抽样随机种子")
    bo_cmd.add_argument("--since", default=None, help="只收这一天之后的录像（YYYY-MM-DD）")
    bo_cmd.add_argument("--until", default=None, help="只收这一天之前的录像（YYYY-MM-DD）")
    bo_cmd.add_argument("--start-workers", dest="start_workers", type=int, default=None,
                        help="只收开局农民数为 N 的版本（默认 12；传 0 关闭）")
    bo_cmd.add_argument("--k-min", dest="k_min", type=int, default=None, help="聚类 k 下限")
    bo_cmd.add_argument("--k-max", dest="k_max", type=int, default=None, help="聚类 k 上限")
    bo_cmd.add_argument("--replay", action="append", default=[], metavar="FILE",
                        help="额外单独分析某局（可重复），会打印 signature")
    bo_cmd.add_argument("-o", "--output", dest="bo_html", default=None, help="HTML 输出路径")
    bo_cmd.add_argument("--json", dest="json_path", default=None, help="JSON 输出路径")
    bo_cmd.add_argument("--open", dest="open_after", action="store_true",
                        help="生成后自动用浏览器打开")

    dag_cmd = sub.add_parser("dag", help="BO 转移 DAG：到点执行还是侦察后反应")
    dag_cmd.add_argument("--bo", default=None, help="bo.py 产出的 reports/bo.json")
    dag_cmd.add_argument("--matchup", default="TvZ")
    dag_cmd.add_argument("--mine", type=int, default=100, help="我的录像最多几局")
    dag_cmd.add_argument("--pro", type=int, default=100, help="职业录像最多几局")
    dag_cmd.add_argument("--pro-name", dest="pro_name", default=None, help="职业选手名字，默认 Maru")
    dag_cmd.add_argument("--min-edge", dest="min_edge", type=int, default=2, help="边上图的最小次数")
    dag_cmd.add_argument("--force", action="store_true", help="丢弃信息事件缓存重新计算")
    dag_cmd.add_argument("-o", "--output", dest="dag_html", default=None, help="HTML 输出路径")
    dag_cmd.add_argument("--json", dest="json_path", default=None, help="JSON 输出路径")
    dag_cmd.add_argument("--open", dest="open_after", action="store_true",
                         help="生成后自动用浏览器打开")

    # 允许省略子命令：直接给一个 replay 路径就当作 analyze
    raw_args = list(sys.argv[1:] if argv is None else argv)
    known = {"analyze", "batch", "dump", "micro", "library", "compare", "card", "fog",
             "econ", "bo", "dag"}
    if raw_args and not raw_args[0].startswith("-") and raw_args[0] not in known:
        if os.path.isfile(raw_args[0]) or raw_args[0].lower().endswith(".sc2replay"):
            raw_args = ["analyze"] + raw_args
    args = parser.parse_args(raw_args)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "analyze":
        build(args.replay, args.output, args.json_path, args.open_after)
        return 0

    if args.command == "batch":
        paths = list(_iter_replays(args.target))
        if not paths:
            print("没有找到 .SC2Replay 文件：%s" % args.target)
            return 1
        os.makedirs(args.outdir, exist_ok=True)
        built = []
        for index, path in enumerate(paths, 1):
            print("[%d/%d] %s" % (index, len(paths), os.path.basename(path)))
            try:
                analysis, output = build(path, os.path.join(
                    args.outdir,
                    os.path.splitext(os.path.basename(path))[0] + ".html"), quiet=True)
                built.append((path, analysis, output))
            except Exception as exc:
                print("    失败：%s" % exc)
        index_path = os.path.join(args.outdir, "index.html")
        _write_index(built, index_path)
        print("完成 %d 局，索引：%s" % (len(built), os.path.abspath(index_path)))
        if args.open_after:
            _open_in_browser(index_path)
        return 0

    if args.command == "micro":
        if os.path.isdir(args.replay):
            paths = glob.glob(os.path.join(args.replay, "**", "*.SC2Replay"),
                              recursive=True)
            paths.sort(key=os.path.getmtime, reverse=True)
            rows = micro.analyze_folder(paths, limit=args.limit)
            output = args.output or os.path.join("reports", "micro-trend.html")
            micro_report.write_trend_report(rows, output)
            if args.json_path:
                with open(args.json_path, "w", encoding="utf-8") as handle:
                    json.dump(rows, handle, ensure_ascii=False, indent=2)
            _print_trend(rows, output)
            if args.open_after:
                _open_in_browser(output)
            return 0

        parsed = load_replay(args.replay)
        data = micro.analyze_micro(parsed, [args.player] if args.player else None)
        output = args.output
        if output is None:
            output = os.path.splitext(_default_output(args.replay))[0] + "-micro.html"
        micro_report.write_micro_report(data, output)
        if args.json_path:
            with open(args.json_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        _print_micro(data, output)
        if args.open_after:
            _open_in_browser(output)
        return 0

    if args.command == "library":
        roots = [(args.me, "me")] + [(path, "pro") for path in args.pro]
        index = library.build_index(roots)
        compare_report.write_library_report(index, args.output)
        if args.json_path:
            library.write_manifest(index, args.json_path)
        summary = index["summary"]
        print("=" * 60)
        print("录像库：%d 个文件，解析成功 %d，失败 %d" % (
            summary["total"], summary["parsed"], summary["failed"]))
        print("  我的 %d 局 · 职业 %d 局" % (summary["me"], summary["pro"]))
        for name, count in sorted(summary["pro_players"].items(), key=lambda kv: -kv[1]):
            print("  职业选手 %-12s %d 局" % (name, count))
        if summary["suspect"]:
            print("  疑似放错 %d 个：" % len(summary["suspect"]))
            for entry in summary["suspect"][:5]:
                print("    - %s（%s）" % (entry["file"], entry["suspect"]))
        print("报告    : %s" % os.path.abspath(args.output))
        print("=" * 60)
        return 0

    if args.command == "compare":
        data = compare.compare(args.me, args.pro, me_name=args.me_name,
                               pro_name=args.pro_name, matchup=args.matchup,
                               limit=args.limit, match_limit=args.pairs)
        compare_report.write_compare_report(data, args.output)
        if args.json_path:
            with open(args.json_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
        _print_compare(data, args.output)
        if args.cards:
            made, html_path = card.build_pair_cards(data, args.cards,
                                                    limit=args.pairs)
            print("卡片    : %d 组 → %s（索引 %s）" % (len(made), os.path.abspath(args.cards),
                                                      os.path.abspath(html_path)))
        if args.open_after:
            _open_in_browser(args.output)
        return 0

    if args.command == "card":
        start = _parse_clock(args.at) if args.at else None
        output = args.output
        if output is None:
            base = os.path.splitext(os.path.basename(args.replay))[0]
            output = os.path.join("reports", "cards",
                                  "".join(ch if ch.isalnum() else "-" for ch in base) + ".gif")
        path, info = card.build_card(args.replay, output, t0=start,
                                     duration=args.duration, fps=args.fps,
                                     player_name=args.player, mp4=args.mp4)
        print("=" * 60)
        print("卡片    : %s（%s）" % (info["player"], info["map"]))
        print("时间    : %s 起 %.1fs，%d 帧" % (
            "%d:%02d" % (int(info["start"]) // 60, int(info["start"]) % 60),
            info["duration"], info["frames"]))
        if info.get("window"):
            window = info["window"]
            print("窗口    : %s ↔ %s（%d 条指令，跨 %.0f 格）"
                  % ("、".join(window["labels"][:1]), "、".join(window["labels"][1:2]),
                     window["actions"], window["spread"]))
        print("文件    : %s（%.0f KB）" % (os.path.abspath(path), info["bytes"] / 1024.0))
        print("=" * 60)
        if args.open_after:
            _open_in_browser(path)
        return 0

    if args.command == "fog":
        parsed = load_replay(args.replay)
        me = card.pick_player(parsed, args.me) if args.me else None
        opponent = None
        if args.opponent:
            target = args.opponent.lower()
            opponent = next((p for p in parsed.players
                             if target in p.name.lower()), None)
        started = time.time()
        result = fog.analyze(parsed,
                             me_pid=me.player_id if me else None,
                             opp_pid=opponent.player_id if opponent else None,
                             scale=args.sight_scale, interpolate=False)
        # 位置采样很稀，插值与不插值会给出不同的量级；两种口径都算一遍，报告里给区间
        alt = fog.analyze(parsed,
                          me_pid=me.player_id if me else None,
                          opp_pid=opponent.player_id if opponent else None,
                          scale=args.sight_scale, interpolate=True)
        result["alt"] = {
            "army_ratio": alt["army"]["ratio"],
            "theirs_ratio": alt["theirs_army"]["ratio"],
            "mine_coverage": alt["vision"]["mine_coverage"],
            "theirs_coverage": alt["vision"]["theirs_coverage"],
            "blind_windows": len(alt["blind_windows"]),
            "exposed_windows": len(alt["exposed_windows"]),
            "first_contact": alt["first_contact"],
        }
        output = args.output
        if output is None:
            output = os.path.splitext(_default_output(args.replay))[0] + "-fog.html"
        gif = None
        if args.gif is not None:
            gif = args.gif or os.path.join(
                os.path.dirname(os.path.abspath(output)), "cards",
                os.path.splitext(os.path.basename(output))[0] + ".gif")
            start = _parse_clock(args.at) if args.at else _fog_moment(result)
            gif, _info = fogrender.build_fog_card(
                args.replay, gif, t0=start, duration=args.duration, fps=args.fps,
                viewer_name=result["me"]["name"], reveal_hidden=args.reveal)
        fog_report.write_fog_report(result, output, gif=gif)
        if args.json_path:
            payload = dict(result)
            payload["timeline"] = result["timeline"]
            with open(args.json_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1)
        _print_fog(result, output, gif)
        print("用时      : %.1fs" % (time.time() - started))
        if args.open_after:
            _open_in_browser(output)
        return 0

    if args.command == "econ":
        from . import econ as econ_module

        forwarded = ["--limit", str(args.limit)]
        if args.replay_dir:
            forwarded += ["--replay-dir", args.replay_dir]
        if args.pro_dir:
            forwarded += ["--pro-dir", args.pro_dir]
        if args.game:
            forwarded += ["--game", args.game]
        output = args.econ_out or os.path.join("reports", "econ.html")
        forwarded += ["--out", output]
        if args.json_path:
            forwarded += ["--json", args.json_path]
        econ_module.main(forwarded)
        if args.open_after:
            _open_in_browser(output)
        return 0

    if args.command == "bo":
        from . import bo as bo_module

        forwarded = ["--limit", str(args.limit), "--pro-limit", str(args.pro_limit),
                     "--matchup", args.matchup]
        if args.mine:
            forwarded += ["--mine", args.mine]
        if args.pro:
            forwarded += ["--pro", args.pro]
        if args.seed is not None:
            forwarded += ["--seed", str(args.seed)]
        if args.since:
            forwarded += ["--since", args.since]
        if args.until:
            forwarded += ["--until", args.until]
        if args.start_workers is not None:
            forwarded += ["--start-workers", str(args.start_workers)]
        if args.k_min is not None:
            forwarded += ["--k-min", str(args.k_min)]
        if args.k_max is not None:
            forwarded += ["--k-max", str(args.k_max)]
        for replay in args.replay:
            forwarded += ["--replay", replay]
        output = args.bo_html or os.path.join("reports", "bo.html")
        forwarded += ["--html", output]
        if args.json_path:
            forwarded += ["--json", args.json_path]
        bo_module.main(forwarded)
        if args.open_after:
            _open_in_browser(output)
        return 0

    if args.command == "dag":
        from . import dag_report as dag_module

        forwarded = ["--matchup", args.matchup, "--mine", str(args.mine),
                     "--pro", str(args.pro), "--min-edge", str(args.min_edge)]
        if args.bo:
            forwarded += ["--bo", args.bo]
        if args.pro_name:
            forwarded += ["--pro-name", args.pro_name]
        output = args.dag_html or os.path.join("reports", "dag.html")
        forwarded += ["--out", output]
        if args.json_path:
            forwarded += ["--json", args.json_path]
        if args.force:
            forwarded.append("--force")
        dag_module.main(forwarded)
        if args.open_after:
            _open_in_browser(output)
        return 0

    if args.command == "dump":
        parsed = load_replay(args.replay)
        payload = parsed.to_dict()
        payload.update({
            "build_events": parsed.build_events,
            "upgrades": parsed.upgrades,
            "chat": parsed.chat,
            "stats": {str(k): v for k, v in parsed.stats.items()},
            "units": [
                {"name": r.name, "player_id": r.player_id, "init": r.init_loop,
                 "born": r.born_loop, "done": r.done_loop, "died": r.died_loop,
                 "structure": r.is_structure, "morphs": r.morphs}
                for r in parsed.unit_records
            ],
            "game_event_counts": parsed.game_event_counts,
        })
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(text)
            print("已写入：%s" % os.path.abspath(args.output))
        else:
            print(text)
        return 0

    parser.print_help()
    return 1


def _write_index(built, index_path):
    rows = []
    for path, analysis, output in built:
        meta = analysis["meta"]
        names = " vs ".join("%s(%s)" % (p["name"], p["race_name"])
                            for p in analysis["players"])
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td><a href=\"%s\">报告</a></td></tr>"
                    % (meta["map"], meta["duration"], names,
                       os.path.basename(output)))
    style = (
        "body{background:#0e1116;color:#e6ecf5;"
        "font:15px/1.6 'Segoe UI','Microsoft YaHei',sans-serif;"
        "max-width:900px;margin:40px auto;padding:0 20px}"
        "table{width:100%;border-collapse:collapse}"
        "td,th{padding:9px 10px;border-bottom:1px solid #2a3340;text-align:left}"
        "a{color:#7c9cff}h1{font-size:24px}"
    )
    html_text = (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>对局索引</title><style>" + style + "</style></head><body>"
        "<h1>对局索引（" + str(len(built)) + " 局）</h1>"
        "<table><thead><tr><th>地图</th><th>时长</th><th>对阵</th><th></th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table></body></html>"
    )
    with open(index_path, "w", encoding="utf-8") as handle:
        handle.write(html_text)


if __name__ == "__main__":
    sys.exit(main())
