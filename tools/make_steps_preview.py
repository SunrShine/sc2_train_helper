"""把步骤抽取的 JSON（reports/tutorial-steps-pilot.json）渲染成预览页。

    python tools\\make_steps_preview.py --json reports/tutorial-steps-pilot.json -o reports/tutorial-steps-pilot.html
"""

import argparse
import html
import json
import os
import sys

h = html.escape

CSS = """
:root{--bg:#0e1116;--bg2:#151a22;--card:#1a2029;--card2:#202836;--line:#2a3340;
--text:#e6ecf5;--muted:#93a1b5;--good:#46d17e;--warn:#f2c45a;--bad:#ff6b6b;--accent:#7c9cff}
*{box-sizing:border-box}
body{margin:0;padding:0 0 70px;color:var(--text);
background:radial-gradient(1200px 600px at 50% -200px,#1b2436 0%,var(--bg) 60%);
font:14.5px/1.65 "Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:0 20px}
header.top{padding:32px 0 8px}
h1{font-size:25px;margin:0 0 6px}
.sub{color:var(--muted);font-size:13.5px}
h2{font-size:17px;margin:28px 0 10px;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:4px;height:15px;border-radius:2px;background:var(--accent)}
.card{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);
border-radius:13px;padding:14px 16px;margin-bottom:12px}
.head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:6px}
.head .name{font-size:16px;font-weight:700}
.tag{color:var(--muted);font-size:12.5px}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;
border:1px solid var(--line);background:#202836}
.ok{color:var(--good);border-color:#2b5a3f}.warn{color:var(--warn);border-color:#5a4b2b}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;padding:6px 8px;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid #222b38}
tr:last-child td{border-bottom:none}
.num{text-align:right;color:#7d8ca3;font-variant-numeric:tabular-nums;white-space:nowrap}
.tags{margin-top:2px}.tags code{background:#141a23;padding:0 5px;border-radius:4px;font-size:11px;color:#9fb0c8}
.hi{color:var(--good)}.md{color:var(--warn)}.lo{color:var(--bad)}
.note{color:var(--muted);font-size:12.5px;margin-top:8px;line-height:1.7}
.hint{color:var(--muted);font-size:13px;line-height:1.85}
.hint b{color:var(--text)}
footer{margin-top:34px;color:#66748a;font-size:12.5px;border-top:1px solid var(--line);padding-top:14px}
"""


def _clock(seconds):
    try:
        seconds = int(round(float(seconds)))
    except Exception:
        return "-"
    return "%d:%02d" % (seconds // 60, seconds % 60)


def _confidence(value):
    mapping = {"high": ("hi", "高"), "med": ("md", "中"), "low": ("lo", "低")}
    cls, text = mapping.get(value, ("", value or "-"))
    return "<span class='%s'>%s</span>" % (cls, h(text))


def render(data, title="TvT 教学视频 · 步骤抽取试点"):
    meta = data.get("meta") or {}
    videos = data.get("videos") or []

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>%s</title><style>%s</style></head><body><div class=\"wrap\">" % (h(title), CSS)]
    parts.append("<header class=\"top\"><h1>%s</h1>" % h(title))
    parts.append('<div class="sub">%s · 试点 %d 条 · 方法：%s</div></header>'
                 % (h(meta.get("source", "")), len(videos), h(meta.get("method", ""))))

    parts.append('<h2>一、这个格式是什么</h2><div class="card"><div class="hint">')
    parts.append("<b>每条视频 -> 一套 BO 的步骤表</b>：步骤号（作者画面标注）、动作（我方术语）、"
                 "时间点（视频秒数）、来源（frame/asr/both）、置信度。<br>")
    parts.append("<b>为什么要标来源：</b>画面标注是作者手打的，最可靠；ASR 转写错字多，只能交叉核对。"
                 "标 <span class='lo'>低</span> 的条目需要人工看一眼原片。<br>")
    parts.append("<b>这张表的下游用途：</b>① 和 replay 的建造事件对齐，看实战第几步开始走偏；"
                 "② 把同一对位的多套 BO 并成流派树；③ 生成可打印的练习卡。")
    parts.append("</div></div>")

    for cover in meta.get("caveats") or []:
        parts.append('<div class="card"><div class="hint">⚠️ %s</div></div>' % h(cover))

    for video in videos:
        status = video.get("status")
        badge = ("<span class='pill ok'>已抽取</span>" if status == "partial"
                 else "<span class='pill warn'>待抽取</span>")
        parts.append('<div class="card">')
        parts.append('<div class="head"><span class="name">#%s %s</span>%s'
                     '<span class="tag">%s · %s · %s</span></div>'
                     % (video.get("number"), h(video.get("build", "")), badge,
                        h(video.get("matchup", "")), _clock(video.get("duration")),
                        h(video.get("bvid", ""))))
        parts.append('<div class="hint">%s</div>' % h(video.get("title", "")))
        steps = video.get("steps") or []
        if steps:
            parts.append("<table><thead><tr><th>步</th><th>动作 + 结构化标签</th>"
                         "<th>游戏时钟</th><th>视频时间</th>"
                         "<th>来源</th><th>置信度</th></tr></thead><tbody>")
            for step in steps:
                tags = step.get("tags") or []
                tag_html = " ".join("<code>%s</code>" % h(tag) for tag in tags) or "-"
                clock = step.get("clock") or "-"
                parts.append(
                    "<tr><td class='num'>%s</td><td>%s<div class='tags'>%s</div></td>"
                    "<td class='num'>%s</td><td class='num'>%s</td>"
                    "<td>%s</td><td>%s</td></tr>"
                    % (h(str(step.get("n"))) if step.get("n") is not None else "-",
                       h(step.get("action", "")), tag_html,
                       h(str(clock)), _clock(step.get("video_t")),
                       h(step.get("source", "")), _confidence(step.get("confidence"))))
            parts.append("</tbody></table>")
        else:
            parts.append("<div class='hint'>（这一步还没跑，格式同上）</div>")
        if video.get("note"):
            parts.append('<div class="note">%s</div>' % h(video["note"]))
        parts.append("</div>")

    parts.append("<footer>试点数据来自 B 站公开合集；步骤号为画面标注，动作已归一成本项目术语。"
                 "本页只做格式预览，未做完整抽取。</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成步骤抽取预览页")
    parser.add_argument("--json", default=os.path.join("reports", "tutorial-steps-pilot.json"))
    parser.add_argument("-o", "--output", default=os.path.join("reports", "tutorial-steps-pilot.html"))
    args = parser.parse_args(argv)

    if not os.path.isfile(args.json):
        print("找不到 %s" % args.json)
        return 1
    with open(args.json, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    text = render(data)
    directory = os.path.dirname(os.path.abspath(args.output))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(text)
    print("%d 条视频 -> %s" % (len(data.get("videos") or []), os.path.abspath(args.output)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
