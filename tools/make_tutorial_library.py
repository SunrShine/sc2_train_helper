"""把已抽取的步骤数据渲染成《TvT 教学流程库》第一版。

数据来源：
- reports/tutorial-steps-pilot.json  已抽取的步骤（动作 / 标签 / 时钟 / 置信度）
- reports/tutorial-builds.json       100 条招式的清单（对位、标题、时长）

    python tools\\make_tutorial_library.py
"""

import argparse
import html
import json
import os
import re
import sys

h = html.escape

CSS = """
:root{--bg:#0e1116;--bg2:#151a22;--card:#1a2029;--card2:#202836;--line:#2a3340;
--text:#e6ecf5;--muted:#93a1b5;--good:#46d17e;--warn:#f2c45a;--bad:#ff6b6b;--accent:#7c9cff}
*{box-sizing:border-box}
body{margin:0;padding:0 0 70px;color:var(--text);
background:radial-gradient(1200px 600px at 50% -200px,#1b2436 0%,var(--bg) 60%);
font:14.5px/1.62 "Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px}
header.top{padding:32px 0 8px}
h1{font-size:25px;margin:0 0 6px}
.sub{color:var(--muted);font-size:13.5px}
h2{font-size:17px;margin:30px 0 10px;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:4px;height:15px;border-radius:2px;background:var(--accent)}
h3{font-size:15px;margin:18px 0 6px}
.card{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);
border-radius:13px;padding:14px 16px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.kv{background:var(--card2);border-radius:9px;padding:9px 11px}
.kv b{display:block;color:var(--muted);font-size:11.5px;font-weight:600}
.kv span{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:12.8px;margin-top:6px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:11.5px;padding:6px 8px;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:5px 8px;border-bottom:1px solid #222b38;vertical-align:top}
tr:last-child td{border-bottom:none}
.num{text-align:right;color:#7d8ca3;font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:Consolas,monospace;font-size:11.5px;color:#9fb0c8}
.tags code{background:#141a23;padding:0 5px;border-radius:4px;font-size:10.5px;color:#9fb0c8;margin-right:2px}
.hi{color:var(--good)}.md{color:var(--warn)}.lo{color:var(--bad)}
.pill{display:inline-block;padding:0 7px;border-radius:999px;font-size:11px;
border:1px solid var(--line);background:#202836;color:var(--muted)}
.hint{color:var(--muted);font-size:13px;line-height:1.8}
footer{margin-top:34px;color:#66748a;font-size:12.5px;border-top:1px solid var(--line);padding-top:14px}
"""


def clock(seconds):
    try:
        seconds = int(round(float(seconds)))
    except Exception:
        return "-"
    return "%d:%02d" % (seconds // 60, seconds % 60)


def conf(value):
    return {"high": ("hi", "高"), "med": ("md", "中"), "low": ("lo", "低")}.get(value, ("", value or "-"))


ARCHETYPES = (
    ("野兵营系（大招）", ("野兵营", "野单兵营", "野2兵营", "野两兵营", "野 2 兵营", "野VS", "野2VS")),
    ("三兵营系", ("3兵营", "三兵营", "3 兵营")),
    ("机械化", ("机械化", "mech")),
    ("空军/渡鸦女妖", ("渡鸦", "女妖", "大和", "维京", "空军")),
    ("空投系", ("运输机", "装船", "空投", "船兵")),
    ("双气死神系（主流运营）", ("双气", "死神")),
)


def archetype(build):
    for name, keys in ARCHETYPES:
        if any(key.lower() in (build or "").lower() for key in keys):
            return name
    return "其他"


def render(steps_data, builds_data, title="TvT 教学流程库（第一版）"):
    videos = [v for v in (steps_data.get("videos") or []) if v.get("steps")]
    builds = {b["number"]: b for b in (builds_data.get("builds") or [])}
    total_steps = sum(len(v["steps"]) for v in videos)
    with_clock = sum(1 for v in videos for s in v["steps"] if s.get("clock"))

    groups = {}
    for video in videos:
        groups.setdefault(archetype(video.get("build")), []).append(video)

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>%s</title><style>%s</style></head><body><div class=\"wrap\">" % (h(title), CSS)]
    parts.append("<header class=\"top\"><h1>%s</h1>" % h(title))
    parts.append('<div class="sub">B 站【星际一招鲜】· UP 主 MakChao · '
                 '已抽 %d 条 / 100 条编号招式 · 共 %d 个步骤 · 其中 %d 个带游戏内时钟</div>'
                 % (len(videos), total_steps, with_clock))
    parts.append("</header>")

    parts.append('<h2>一、总览</h2><div class="card"><div class="grid">')
    for label, value in (("已抽视频", len(videos)), ("步骤总数", total_steps),
                         ("带时钟步骤", with_clock),
                         ("覆盖对位", "TvT"),
                         ("战术类型", len(groups))):
        parts.append('<div class="kv"><b>%s</b><span>%s</span></div>' % (h(label), h(str(value))))
    parts.append("</div></div>")

    parts.append('<div class="card"><div class="hint">'
                 "<b>怎么读这张库：</b>每条视频 = 一套 BO。步骤里的标签（build/unit/gas/…)可以直接和 replay 的建造事件对齐："
                 "同一套流程，视频说「第 5 步下 VF」，你的 replay 里第几秒下 VF，差多少一眼能看出来。<br>"
                 "<b>来源列：</b><span class='hi'>frame</span> = 从画面标注读到（可靠）；"
                 "<span class='lo'>asr</span> = 从转写补的（不可靠）；置信度标了高中低。<br>"
                 "<b>时钟列：</b>游戏内计时器读数；视频有剪辑时可能不连续。"
                 "</div></div>")

    for group_name, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        parts.append('<h2>%s（%d 套）</h2>' % (h(group_name), len(members)))
        for video in sorted(members, key=lambda v: v["number"]):
            meta = builds.get(video["number"], {})
            parts.append('<div class="card">')
            parts.append('<h3>#%s %s <span class="pill">%s</span> '
                         '<a href="https://www.bilibili.com/video/%s/" target="_blank">%s</a></h3>'
                         % (video["number"], h(video.get("build", "")), h(video.get("bvid", "")),
                            h(video.get("bvid", "")), h(video.get("bvid", ""))))
            parts.append('<div class="hint">%s</div>' % h(video.get("title") or meta.get("title") or ""))
            parts.append("<table><thead><tr><th>步</th><th>动作</th><th>标签</th>"
                         "<th>游戏时钟</th><th>视频时间</th><th>来源</th><th>置信</th></tr></thead><tbody>")
            for step in video["steps"]:
                tags = " ".join("<code>%s</code>" % h(tag) for tag in (step.get("tags") or [])) or "-"
                cls, label = conf(step.get("confidence"))
                parts.append(
                    "<tr><td class='num'>%s</td><td>%s</td><td class='tags'>%s</td>"
                    "<td class='num'>%s</td><td class='num'>%s</td><td>%s</td>"
                    "<td class='%s'>%s</td></tr>"
                    % (h(str(step.get("n"))) if step.get("n") is not None else "-",
                       h(step.get("action", "")), tags, h(step.get("clock") or "-"),
                       clock(step.get("video_t")), h(step.get("source", "")), cls, h(label)))
            parts.append("</tbody></table>")
            if video.get("note"):
                parts.append('<div class="hint" style="margin-top:6px">备注：%s</div>' % h(video["note"]))
            parts.append("</div>")

    parts.append("<footer>由 tools/make_tutorial_library.py 从 reports/tutorial-steps-pilot.json 生成。"
                 "步骤来自视频画面上的作者标注，ASR 只做交叉核对；视频有剪辑，时钟可能不连续。</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 TvT 教学流程库")
    parser.add_argument("--steps", default=os.path.join("reports", "tutorial-steps-pilot.json"))
    parser.add_argument("--builds", default=os.path.join("reports", "tutorial-builds.json"))
    parser.add_argument("-o", "--output", default=os.path.join("reports", "tutorial-library.html"))
    args = parser.parse_args(argv)

    for path in (args.steps, args.builds):
        if not os.path.isfile(path):
            print("找不到 %s" % path)
            return 1
    steps_data = json.load(open(args.steps, encoding="utf-8"))
    builds_data = json.load(open(args.builds, encoding="utf-8"))
    text = render(steps_data, builds_data)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(text)
    videos = [v for v in steps_data["videos"] if v.get("steps")]
    print("%d 条视频 -> %s" % (len(videos), os.path.abspath(args.output)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
