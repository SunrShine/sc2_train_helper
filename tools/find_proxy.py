"""从录像里找「野兵营」对局：兵营造得离自己主基地很远。

判据：某个玩家的第一座（或前两座）兵营，与他自己最近的基地距离超过阈值（默认 25 格）。
正常开局兵营就在主基地旁边（约 8~12 格）；野兵营一般在对手半场或地图角落，40+ 格。

    python tools\\find_proxy.py "C:\\...\\Replays\\Multiplayer" --matchup TvT --limit 400
"""

import argparse
import glob
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from replay_analyzer.loader import load_replay  # noqa: E402

TOWNHALLS = ("CommandCenter", "OrbitalCommand", "PlanetaryFortress",
             "Nexus", "Hatchery", "Lair", "Hive")
PROXY_UNITS = ("Barracks", "Factory", "Starport")


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def analyze(path, threshold=25.0):
    parsed = load_replay(path, decode_events=False)
    players = [p for p in parsed.players if not p.is_ai]
    if len(players) != 2:
        return None

    findings = []
    for player in players:
        own_halls = [u for u in parsed.unit_records
                     if u.player_id == player.player_id and u.name in TOWNHALLS
                     and u.x is not None]
        if not own_halls:
            continue
        for unit in parsed.unit_records:
            if unit.player_id != player.player_id:
                continue
            if unit.name not in PROXY_UNITS or unit.x is None:
                continue
            if unit.start_loop is None or unit.start_loop / 16.0 > 420:
                continue
            nearest = min(_dist((unit.x, unit.y), (h.x, h.y)) for h in own_halls)
            if nearest >= threshold:
                findings.append({
                    "player": player.name,
                    "is_local": player.is_local,
                    "unit": unit.name,
                    "time": round(unit.start_loop / 16.0, 1),
                    "distance": round(nearest, 1),
                })
    if not findings:
        return None
    local = next((p for p in players if p.is_local), None)
    opponent = next((p for p in players if p is not local), None)
    return {
        "path": path,
        "map": parsed.map_name,
        "duration": round(parsed.duration_seconds),
        "build": parsed.version.get("build"),
        "matchup": "%sv%s" % (local.race if local else "?",
                              opponent.race if opponent else "?"),
        "findings": findings,
        "proxy_by": sorted({f["player"] for f in findings}),
        "i_proxied": any(f["is_local"] for f in findings),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="找野兵营对局")
    parser.add_argument("target", help="录像目录")
    parser.add_argument("--matchup", default=None, help="只看某个对位，如 TvT")
    parser.add_argument("--limit", type=int, default=400, help="最多扫多少个文件")
    parser.add_argument("--threshold", type=float, default=25.0, help="离基地多少格算野")
    parser.add_argument("--json", default=None, help="把结果写成 JSON")
    args = parser.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.target, "**", "*.SC2Replay"), recursive=True))
    paths = paths[:args.limit]
    hits = []
    for index, path in enumerate(paths, 1):
        try:
            result = analyze(path, args.threshold)
        except Exception:
            continue
        if result and (not args.matchup or result["matchup"] == args.matchup):
            hits.append(result)
        if index % 100 == 0:
            sys.stderr.write("\r扫了 %d/%d，命中 %d   " % (index, len(paths), len(hits)))

    sys.stderr.write("\n")
    print("扫描 %d 局，野兵营 %d 局" % (len(paths), len(hits)))
    mine = [h for h in hits if h["i_proxied"]]
    print("其中我自己野兵营：%d 局" % len(mine))
    for hit in hits:
        who = "我" if hit["i_proxied"] else ""
        print("  %-46s %-8s %s  %s" % (
            os.path.basename(hit["path"])[:46], hit["matchup"],
            " / ".join("%s@%ss(%.0f格)" % (f["unit"], f["time"], f["distance"])
                       for f in hit["findings"]),
            who))
    if args.json:
        import json
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(hits, handle, ensure_ascii=False, indent=1)
        print("已写入 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
