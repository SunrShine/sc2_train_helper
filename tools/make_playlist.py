"""给一堆职业 replay 生成"观赛片单"：谁打谁、什么图、多长、能不能直接看。

为什么需要它：SC2 客户端只能播放**当前版本**的 replay。想直接双击看，
得先确认这局的 build 在 `Versions/Base<build>` 里存在（客户端会把旧版本目录留着）。

用法::

    python tools\\make_playlist.py replays\\pro\\maru -o reports\\pro-playlist.html
"""

import argparse
import glob
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from replay_analyzer.loader import load_replay  # noqa: E402

DEFAULT_SC2 = r"D:\StarCraft II"


def installed_builds(sc2_dir):
    versions = os.path.join(sc2_dir, "Versions")
    out = set()
    if os.path.isdir(versions):
        for name in os.listdir(versions):
            if name.lower().startswith("base") and name[4:].isdigit():
                out.add(int(name[4:]))
    return out


def _clean(name):
    text = (name or "").replace("<sp/>", " ").replace("&lt;", "<").replace("&gt;", ">")
    return " ".join(text.split())


def collect(root):
    rows = []
    for path in sorted(glob.glob(os.path.join(root, "**", "*.SC2Replay"), recursive=True)):
        try:
            parsed = load_replay(path, decode_events=False)
        except Exception as exc:
            rows.append({"path": path, "error": str(exc)[:120]})
            continue
        players = [{"name": _clean(p.name), "race": p.race, "result": p.result}
                   for p in parsed.players if not p.is_ai]
        rows.append({
            "path": path,
            "file": os.path.basename(path),
            "map": parsed.map_name,
            "seconds": parsed.duration_seconds,
            "build": parsed.version.get("build") or 0,
            "date": str(parsed.replay_time)[:10],
            "players": players,
            "size": os.path.getsize(path),
        })
    return rows


def render(rows, builds, sc2_dir, title):
    ok = [r for r in rows if "error" not in r]
    ok.sort(key=lambda r: (r["build"], -r["seconds"]))
    playable = sum(1 for r in ok if r["build"] in builds)

    parts = ['''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>
:root{--bg:#0e1116;--card:#1a2029;--line:#2a3340;--text:#e6ecf5;--muted:#93a1b5;
--good:#46d17e;--warn:#f2c45a;--bad:#ff6b6b;--accent:#7c9cff}
*{box-sizing:border-box}
body{margin:0;padding:28px 20px 60px;background:var(--bg);color:var(--text);
font:15px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:24px;margin:0 0 6px}
.sub{color:var(--muted);font-size:13.5px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:14px}
table{width:100%%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:12.5px;padding:8px;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px;border-bottom:1px solid #222b38;vertical-align:top}
tr:last-child td{border-bottom:none}
.mono{font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;
border:1px solid var(--line);background:#202836}
.ok{color:var(--good);border-color:#2b5a3f}
.no{color:var(--warn);border-color:#5a4b2b}
.win{color:var(--good)}.loss{color:var(--bad)}
code{background:#141a23;padding:1px 6px;border-radius:5px;font-size:12.5px}
.hint{color:var(--muted);font-size:13px;line-height:1.85}
.hint b{color:var(--text)}
</style></head><body><div class="wrap">''' % html.escape(title)]

    parts.append("<h1>职业录像片单</h1>")
    parts.append('<div class="sub">共 %d 局，其中 %d 局的版本（%s）在本机已安装，可以直接双击打开。</div>'
                 % (len(ok), playable,
                    "、".join(str(b) for b in sorted({r["build"] for r in ok if r["build"] in builds}))
                    or "无"))

    parts.append('<div class="card"><div class="hint">'
                 "<b>怎么打开：</b>把 <code>.SC2Replay</code> 拖到 "
                 "<code>%s\\Support64\\SC2Switcher_x64.exe</code> 上，"
                 "或者在游戏里 <b>F10 → 回放 → 打开回放</b>。<br>"
                 "<b>版本不对怎么办：</b>SC2 只能播当前版本；旧版本的回放通常需要客户端把对应 "
                 "<code>Versions\\Base&lt;build&gt;</code> 下载下来。表里标了每局的 build，"
                 "对不上就先启动一次游戏让它拉版本。<br>"
                 "<b>注意：</b>客户端播放的是完整画面，本工具不渲染游戏画面；"
                 "工具能做的是建造顺序、操作节奏、多线窗口和迷雾复盘。"
                 "</div></div>" % html.escape(sc2_dir))

    parts.append('<div class="card"><table><thead><tr><th>对局</th><th>地图</th><th>时长</th>'
                 "<th>结果</th><th>版本</th><th>能否直接看</th></tr></thead><tbody>")

    for row in ok:
        players = " vs ".join(
            '%s<span class="%s">(%s)</span>'
            % (html.escape(p["name"]) or "?",
               "win" if p["result"] == "win" else ("loss" if p["result"] == "loss" else ""),
               html.escape(p["race"]))
            for p in row["players"])
        seconds = int(row["seconds"])
        fit = row["build"] in builds
        results = " / ".join(
            "%s %s" % (html.escape(p["name"]) or "?", html.escape(p["result"]))
            for p in row["players"])
        parts.append(
            "<tr><td>%s<div style='color:#7d8ca3;font-size:12px'>%s</div></td>"
            "<td>%s</td><td class='mono'>%d:%02d</td><td>%s</td><td class='mono'>%d</td>"
            "<td><span class='pill %s'>%s</span></td></tr>"
            % (players, html.escape(row["date"]), html.escape(row["map"]),
               seconds // 60, seconds % 60, results, row["build"],
               "ok" if fit else "no", "可直接看" if fit else "需旧版本"))

    parts.append("</tbody></table></div>")
    parts.append("</div></body></html>")
    return "".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成职业录像观赛片单")
    parser.add_argument("root", help="replay 目录")
    parser.add_argument("-o", "--output", default=os.path.join("reports", "pro-playlist.html"))
    parser.add_argument("--sc2", default=DEFAULT_SC2, help="SC2 安装目录")
    args = parser.parse_args(argv)

    builds = installed_builds(args.sc2)
    rows = collect(args.root)
    text = render(rows, builds, args.sc2, "职业录像片单")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(text)
    ok = [r for r in rows if "error" not in r]
    fit = sum(1 for r in ok if r["build"] in builds)
    print("共 %d 局，%d 局当前版本可直接播放" % (len(ok), fit))
    print("片单：%s" % os.path.abspath(args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
