"""把 B 站合集索引（reports/tutorial-index.json）渲染成"教学视频索引表"。

只做索引层：编号、对位、标题、时长、链接、分析状态。
不下载视频、不跑 ASR —— 这一步是决定"先分析哪些"的盘点。

    python tools\\make_tutorial_index.py --json reports/tutorial-index.json -o reports/tutorial-index.html
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
--text:#e6ecf5;--muted:#93a1b5;--good:#46d17e;--warn:#f2c45a;--accent:#7c9cff}
*{box-sizing:border-box}
body{margin:0;padding:0 0 70px;color:var(--text);
background:radial-gradient(1200px 600px at 50% -200px,#1b2436 0%,var(--bg) 60%);
font:14.5px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px}
header.top{padding:32px 0 8px}
h1{font-size:25px;margin:0 0 6px}
.sub{color:var(--muted);font-size:13.5px}
h2{font-size:17px;margin:30px 0 10px;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:4px;height:15px;border-radius:2px;background:var(--accent)}
.card{background:linear-gradient(180deg,var(--card),var(--bg2));border:1px solid var(--line);
border-radius:13px;padding:14px 16px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kv{background:var(--card2);border-radius:9px;padding:9px 11px}
.kv b{display:block;color:var(--muted);font-size:11.5px;font-weight:600}
.kv span{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;padding:7px 8px;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid #222b38;vertical-align:top}
tr:last-child td{border-bottom:none}
.num{color:#7d8ca3;font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.pill{display:inline-block;padding:0 7px;border-radius:999px;font-size:11px;
border:1px solid var(--line);background:#202836;color:var(--muted)}
.tz{color:#8fd18f;border-color:#2b5a3f}.tp{color:#a9c2ff;border-color:#2c3c60}
.tt{color:#f2c45a;border-color:#5a4b2b}.other{color:#93a1b5}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.hint{color:var(--muted);font-size:13px;line-height:1.8}
.hint b{color:var(--text)}
footer{margin-top:34px;color:#66748a;font-size:12.5px;border-top:1px solid var(--line);padding-top:14px}
"""


def _clock(seconds):
    try:
        seconds = max(0, int(round(float(seconds))))
    except Exception:
        return "?"
    return "%d:%02d" % (seconds // 60, seconds % 60)


def _matchup(title):
    found = re.search(r"TV([ZPT])", (title or "").upper())
    return "Tv" + found.group(1) if found else "其他"


def _number(title):
    found = re.search(r"星际一招鲜\s*(\d+)", title or "")
    return int(found.group(1)) if found else None


def _build_keywords(title):
    """从标题里抽 build 描述：招式和副标题。"""
    text = re.sub(r"【星际一招鲜[^】]*】", "", title or "")
    text = re.sub(r"^\s*TV[TZP]\s*", "", text, flags=re.I)
    parts = re.split(r"\s{2,}|\s*[-—]\s*", text)
    return [part.strip() for part in parts if part.strip()]


def render(data, title="星际一招鲜 · 教学视频索引"):
    eps = data.get("episodes") or []
    season = data.get("season") or {}
    rows = []
    for ep in eps:
        rows.append({
            "bvid": ep.get("bvid"),
            "title": ep.get("title") or "",
            "duration": ep.get("duration") or 0,
            "num": _number(ep.get("title") or ""),
            "matchup": _matchup(ep.get("title") or ""),
        })
    rows.sort(key=lambda row: (row["num"] is None, row["num"] or 0))

    counts = {}
    for row in rows:
        counts[row["matchup"]] = counts.get(row["matchup"], 0) + 1
    total_seconds = sum(row["duration"] for row in rows)
    numbered = [row for row in rows if row["num"] is not None]

    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>%s</title><style>%s</style></head><body><div class=\"wrap\">" % (h(title), CSS)]
    parts.append("<header class=\"top\"><h1>%s</h1>" % h(season.get("title") or title))
    parts.append('<div class="sub">B 站合集 · UP 主 MakChao · 共 %d 条 · 总时长 %.1f 小时 · '
                 '编号招式 %d 条</div></header>'
                 % (len(rows), total_seconds / 3600.0, len(numbered)))

    parts.append('<h2>一、盘点</h2><div class="card"><div class="grid">')
    for label, value in (("总条数", len(rows)),
                         ("TvZ", counts.get("TvZ", 0)),
                         ("TvT", counts.get("TvT", 0)),
                         ("TvP", counts.get("TvP", 0)),
                         ("其他", counts.get("其他", 0)),
                         ("总时长", "%.1f 小时" % (total_seconds / 3600.0)),
                         ("中位时长", _clock(sorted(r["duration"] for r in rows)[len(rows) // 2]))):
        parts.append('<div class="kv"><b>%s</b><span>%s</span></div>' % (h(label), h(str(value))))
    parts.append("</div></div>")

    parts.append('<div class="card"><div class="hint">'
                 "<b>怎么用这张表：</b>标题本身就写了打法（比如「TVT 野单兵营光头」「TVZ 速三开 8 火车 2 女妖」），"
                 "所以第一层分析不用看视频 —— 先把标题拆成「对位 + 招式」两段，就能得到一份 %d 条的流派清单。<br>"
                 "<b>第二层</b>才需要逐个看视频：口播里是「第 1 步…第 N 步」的编号流程，用 ASR 抽出来的步骤可以直接变成 BO 模板。<br>"
                 "<b>建议顺序：</b>先做 TvT（%d 条，总时长约 %.1f 小时），因为你的对局里 TvT 最多、且这几集的步骤讲得最细。"
                 "</div></div>" % (len(numbered), counts.get("TvT", 0),
                                   sum(r["duration"] for r in rows if r["matchup"] == "TvT") / 3600.0))

    for matchup in ("TvT", "TvZ", "TvP", "其他"):
        subset = [row for row in rows if row["matchup"] == matchup]
        if not subset:
            continue
        parts.append('<h2>%s（%d 条）</h2><div class="card"><table><thead><tr>'
                     "<th>#</th><th>标题</th><th>招式（标题拆解）</th><th>时长</th><th>链接</th>"
                     "</tr></thead><tbody>" % (h(matchup), len(subset)))
        badge_class = {"TvZ": "tz", "TvP": "tp", "TvT": "tt"}.get(matchup, "other")
        for row in subset:
            keywords = _build_keywords(row["title"])
            keyword_text = " · ".join(keywords[:2]) if keywords else "-"
            parts.append(
                "<tr><td class='num'>%s</td><td>%s</td><td>%s</td><td class='num'>%s</td>"
                "<td><a href=\"https://www.bilibili.com/video/%s/\" target=\"_blank\">%s</a></td></tr>"
                % (row["num"] if row["num"] is not None else "-",
                   h(row["title"]), h(keyword_text), _clock(row["duration"]),
                   h(row["bvid"]), h(row["bvid"])))
        parts.append("</tbody></table></div>")

    parts.append("<footer>由 tools/make_tutorial_index.py 从 reports/tutorial-index.json 生成；"
                 "索引来自 B 站公开合集（season 179867）。"
                 "本页只做盘点，不含视频内容。</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成教学视频索引表")
    parser.add_argument("--json", default=os.path.join("reports", "tutorial-index.json"))
    parser.add_argument("-o", "--output", default=os.path.join("reports", "tutorial-index.html"))
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
    print("%d 条 -> %s" % (len(data.get("episodes") or []), os.path.abspath(args.output)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
