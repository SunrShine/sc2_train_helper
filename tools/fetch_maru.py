"""从 sc2rep.ru 抓 Maru 的人族 replay。

1. 用站点自带搜索（player=Maru, 1v1）翻几页
2. 解析每一行的：id / 日期 / 游戏版本 / 双方选手与种族 / 地图
3. 只留 5.x 版本的、Maru 打人族的
4. 下载到 replays/pro/maru/，并用我们自己的解析器验证
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://sc2rep.ru"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
OUT_DIR = os.path.join("replays", "pro", "maru")

ROW_SPLIT = re.compile(r'<tr class="trgreen"', re.I)
ID_RE = re.compile(r"/show\.php\?id=(\d+)")
VER_RE = re.compile(r"(\d\.\d+\.\d+\.\d+)")
DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
TIME_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})<BR>(\d{2}:\d{2})")
PLAYER_RE = re.compile(
    r'alt="(Terran|Zerg|Protoss)"[^>]*></td>\s*<td align=left>\s*'
    r'<span[^>]*>([^<]+)</span>(?:<BR>\s*<span[^>]*>APM: (\d+))?', re.I)
MAP_RE = re.compile(r'<td class="index_row">([^<]{2,80})</td>')
LEN_RE = re.compile(r'hrefCode\(\'/show\.php\?id=\d+\'\)">(\d{1,2}:\d{2})</td>')


def fetch(url, data=None):
    request = urllib.request.Request(url, data=data, headers=HEADERS)
    return urllib.request.urlopen(request, timeout=60).read()


def search_pages(pages):
    rows = {}
    for page in pages:
        query = urllib.parse.urlencode({"player": "Maru", "gt": "1", "page": page})
        url = "%s/search.php?%s" % (BASE, query)
        try:
            html = fetch(url).decode("utf-8", "replace")
        except Exception as exc:
            print("  搜索失败 %s: %s" % (page, exc))
            continue
        for block in ROW_SPLIT.split(html)[1:]:
            block = block[:4000]
            match = ID_RE.search(block)
            if not match:
                continue
            replay_id = match.group(1)
            if replay_id in rows:
                continue
            players = PLAYER_RE.findall(block)
            if not players:
                continue
            version = ""
            version_match = VER_RE.search(block)
            if version_match:
                version = version_match.group(1)
            date_match = TIME_RE.search(block)
            date = date_match.group(1) if date_match else ""
            clock = date_match.group(2) if date_match else ""
            maps = [m for m in MAP_RE.findall(block) if not m.strip().isdigit()]
            rows[replay_id] = {
                "id": replay_id,
                "version": version,
                "date": date,
                "time": clock,
                "players": [{"race": race[:1].upper(), "name": name.strip(),
                             "apm": int(apm) if apm else None}
                            for race, name, apm in players],
                "map": maps[0].strip() if maps else "",
            }
        print("  第 %s 页累计 %d 条" % (page, len(rows)))
        time.sleep(1.2)
    return list(rows.values())


def pick(rows, limit):
    chosen = []
    for row in rows:
        if not row["version"].startswith("5."):
            continue
        maru = [p for p in row["players"] if p["name"].lower() == "maru"]
        if not maru or maru[0]["race"] != "T":
            continue
        chosen.append(row)
    def sort_key(row):
        build = int(row["version"].split(".")[-1]) if row["version"] else 0
        return (build, row["date"].split(".")[::-1], row["time"])
    chosen.sort(key=sort_key, reverse=True)
    return chosen[:limit]


def safe(text):
    return re.sub(r"[^\w\-. ]", "_", text).strip() or "unknown"


def download(row):
    raw = fetch("%s/down.php?id=%s" % (BASE, row["id"]))
    if not raw.startswith(b"MPQ"):
        raise ValueError("不是 MPQ 文件（前 4 字节 %r）" % raw[:4])
    opponent = next((p for p in row["players"] if p["name"].lower() != "maru"), None)
    name = "maru_vs_%s_%s_%s_%s.SC2Replay" % (
        safe(opponent["name"] if opponent else "unknown"),
        safe(row["map"]), row["date"].replace(".", "-"), row["id"])
    path = os.path.join(OUT_DIR, name)
    with open(path, "wb") as handle:
        handle.write(raw)
    return path, len(raw)


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    pages = [0, 20, 40, 60, 80, 100, 120, 140]
    print("搜索 Maru 的 1v1 录像…")
    rows = search_pages(pages)
    chosen = pick(rows, limit)
    print("\n候选 %d 条（5.x 版本、Maru 人族）：" % len(chosen))
    for row in chosen:
        print("  %s %s  %-14s %s" % (row["date"], row["time"], row["map"][:14],
                                     " vs ".join("%s(%s)" % (p["name"], p["race"])
                                                 for p in row["players"])))
    if not chosen:
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    sys.path.insert(0, os.path.abspath("."))
    from replay_analyzer.loader import load_replay

    manifest = []
    for row in chosen:
        try:
            path, size = download(row)
            time.sleep(1.5)
            parsed = load_replay(path)
            info = {
                "file": os.path.basename(path),
                "id": row["id"],
                "map": parsed.map_name,
                "build": parsed.version.get("build"),
                "protocol_build": parsed.protocol_build,
                "duration": parsed.duration_seconds,
                "players": [{"name": p.name, "race": p.race,
                             "toon": p.toon, "result": p.result}
                            for p in parsed.players],
                "source": "%s/show.php?id=%s" % (BASE, row["id"]),
                "contest_date": row["date"],
                "map_listed": row["map"],
                "bytes": size,
            }
            manifest.append(info)
            print("  ok  %-46s build=%s %.0fKB %s" % (
                info["file"][:46], info["build"], size / 1024.0, info["map"]))
        except Exception as exc:
            print("  fail id=%s: %s" % (row["id"], exc))

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print("\n完成 %d 个，写入 %s" % (len(manifest), os.path.abspath(OUT_DIR)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
