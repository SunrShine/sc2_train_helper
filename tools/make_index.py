"""扫描 reports/ 生成一个离线汇总页（reports/index.html）。

不依赖任何前端库：只是一个带分类的链接页，方便一次看到所有报告。

    python tools\\make_index.py
"""

import argparse
import datetime
import os
import sys

# 报告文件名 -> (分类, 标题, 一句话说明)
CATALOG = {
    "bo.html": ("建造顺序", "BO 时间线与聚类", "按对位分开：开局序列聚类、我 vs 职业关键时间点"),
    "dag.html": ("建造顺序", "BO 转移 DAG", "段与段之间怎么走、哪些是到点、哪些是看到才变"),
    "tvt-flows.html": ("建造顺序", "TvT 流程命名核对表", "整局流程分类 + 代表录像路径，供你逐个核对命名"),
    "econ.html": ("经济", "经济模型对账", "收入拟合、收支分解、模拟库存 vs 真实库存"),
    "compare-tvz.html": ("对照", "我 vs 职业选手", "操作指标对比 + 多线窗口配对"),
    "library.html": ("资料", "录像库索引", "扫描本机录像，按目录归类"),
    "micro-trend.html": ("操作", "操作趋势", "最近 N 局的多线/视角指标变化"),
    "pro-playlist.html": ("资料", "职业录像片单", "每局的地图、时长、版本、能不能直接播"),
}

CARD_REPORTS = ("micro", "fog")


def _clock(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _rel(path):
    return path.replace("\\", "/")


def collect(reports_dir):
    groups = {}
    for name in sorted(os.listdir(reports_dir)):
        if not name.lower().endswith(".html") or name == "index.html":
            continue
        full = os.path.join(reports_dir, name)
        if not os.path.isfile(full):
            continue
        meta = CATALOG.get(name)
        if meta:
            category, title, desc = meta
        elif any(key in name.lower() for key in CARD_REPORTS):
            category, title, desc = "单局", name, "单局分析报告"
        else:
            category, title, desc = "其他", name, ""
        groups.setdefault(category, []).append({
            "file": name,
            "title": title,
            "desc": desc,
            "size": os.path.getsize(full),
            "mtime": os.path.getmtime(full),
        })
    for items in groups.values():
        items.sort(key=lambda item: -item["mtime"])
    return groups


ORDER = ("建造顺序", "经济", "操作", "单局", "对照", "资料", "其他")


def render(groups, title="SC2 分析报告"):
    parts = ['''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>
:root{--bg:#0e1116;--card:#1a2029;--line:#2a3340;--text:#e6ecf5;--muted:#93a1b5;--accent:#7c9cff}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px 60px;background:radial-gradient(1200px 600px at 50%% -200px,#1b2436 0%%,var(--bg) 60%%);
color:var(--text);font:15px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:980px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px}
.sub{color:var(--muted);font-size:13.5px;margin-bottom:26px}
h2{font-size:16px;margin:26px 0 10px;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:4px;height:15px;border-radius:2px;background:var(--accent)}
a.card{display:block;text-decoration:none;color:inherit;background:var(--card);
border:1px solid var(--line);border-radius:12px;padding:13px 16px;margin-bottom:9px;
transition:border-color .15s,transform .15s}
a.card:hover{border-color:var(--accent);transform:translateX(2px)}
a.card .t{font-weight:700;font-size:15px;margin-bottom:2px}
a.card .d{color:var(--muted);font-size:13px}
a.card .m{color:#66748a;font-size:12px;margin-top:5px;font-variant-numeric:tabular-nums}
.empty{color:var(--muted);font-size:13.5px}
footer{margin-top:34px;color:#66748a;font-size:12.5px;border-top:1px solid var(--line);padding-top:14px}
</style></head><body><div class="wrap">
<h1>%s</h1>
<div class="sub">全部报告都是本地生成的单文件 HTML，离线可开，不依赖任何 CDN。</div>''' % (
        title, title)]

    printed = 0
    for category in ORDER:
        items = groups.get(category)
        if not items:
            continue
        printed += 1
        parts.append("<h2>%s</h2>" % category)
        for item in items:
            size_kb = max(1, int(round(item["size"] / 1024.0)))
            parts.append(
                '<a class="card" href="%s"><div class="t">%s</div>'
                '<div class="d">%s</div>'
                '<div class="m">%s · %d KB · %s</div></a>'
                % (_rel(item["file"]), _rel(item["title"]), _rel(item["desc"] or ""),
                   _rel(item["file"]), size_kb, _clock(item["mtime"])))

    if not printed:
        parts.append('<div class="empty">reports/ 里还没有 HTML 报告。先跑 '
                     "<code>python -m replay_analyzer bo</code> 之类生成一份。</div>")

    parts.append("<footer>由 tools/make_index.py 生成。所有分析只读本地 replay，"
                 "不读游戏内存、不联网。</footer></div></body></html>")
    return "".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成报告汇总页")
    parser.add_argument("--reports", default="reports", help="报告目录")
    parser.add_argument("-o", "--output", default=None, help="输出路径（默认 reports/index.html）")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.reports):
        print("找不到目录：%s" % args.reports)
        return 1
    groups = collect(args.reports)
    output = args.output or os.path.join(args.reports, "index.html")
    text = render(groups)
    with open(output, "w", encoding="utf-8") as handle:
        handle.write(text)
    total = sum(len(items) for items in groups.values())
    print("%d 份报告 -> %s" % (total, os.path.abspath(output)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
