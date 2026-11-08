"""从 sc2rep.ru 批量抓职业人族 replay，并逐个验证。

和 tools/fetch_maru.py 的区别：那个是给 Maru 专用的一次性脚本；这个是参数化的，
支持多名选手、多种输出目录，并且下载后**必须**通过三道校验才会留在仓库里：

1. 文件头是 MPQ（前 4 字节 ``MPQ\\x1a``/``MPQ\\x1b``，或 0x200 处有 MPQ 头）
   —— 站点对无效 id 会 302 回首页返回 HTML 广告页，必须挡掉；
2. 能过我们自己的 loader（``load_replay`` 不抛异常）；
3. 对局里确实有目标选手，且是 5.x（build > 80000）的 1v1。

不满足的立即删除，绝不留在 replays/pro/ 下。

用法::

    # 只看候选，不下载
    python tools\\fetch_pro_replays.py --player Clem,Cure --limit 8 --list

    # 下载（--limit 是每个选手最多"新增成功"多少个）
    python tools\\fetch_pro_replays.py --player Maru --limit 10 --out replays\\pro
    python tools\\fetch_pro_replays.py --all-terran --limit 6 --out replays\\pro

    # 不联网，重新校验已有目录并刷新 manifest.json
    python tools\\fetch_pro_replays.py --verify-only --out replays\\pro

输出：
- ``replays/pro/<player>/<file>.SC2Replay``
- 每个选手目录一份 ``manifest.json``（map / matchup / duration / build / 谁打谁）
- 抓取日志写到 ``.tmp/fetch_pro_replays.json``（不污染 replays/）
"""

import argparse
import html as html_lib
import json
import os
import random
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
    "Referer": BASE + "/",
}
DEFAULT_OUT = os.path.join("replays", "pro")
DEFAULT_PLAYERS = ["Maru", "Clem", "ByuN", "TY", "Cure", "HeroMarine"]
MIN_BUILD = 80001          # 只要 5.x（5.0 的 build 都 > 80000）
MIN_SECONDS = 120          # 太短的残局对 BO 分析没意义
CHUNK = 64 * 1024
PAGE_STEP = 20

ROW_SPLIT = re.compile(r'<tr class="trgreen"', re.I)
ID_RE = re.compile(r"/show\.php\?id=(\d+)")
VER_RE = re.compile(r"(\d\.\d+\.\d+\.\d+)")
TIME_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})<BR>(\d{2}:\d{2})")
PLAYER_RE = re.compile(
    r'alt="(Terran|Zerg|Protoss)"[^>]*></td>\s*<td align=left>\s*'
    r'<span[^>]*>([^<]+)</span>(?:<BR>\s*<span[^>]*>APM: (\d+))?', re.I)
MAP_RE = re.compile(r'<td class="index_row">([^<]{2,80})</td>')
LEN_RE = re.compile(r'hrefCode\(\'/show\.php\?id=\d+\'\)">(\d{1,2}:\d{2})</td>')

MPQ_MAGICS = (b"MPQ\x1a", b"MPQ\x1b")


class HttpError(Exception):
    """重试完仍然拿不到。"""


class Rejected(Exception):
    """文件不合格（不是 replay / 解析失败 / 没有目标选手 / 版本太旧）。"""


# --------------------------------------------------------------------------- 工具
def _sleep_between(gap_min, gap_max):
    time.sleep(random.uniform(max(0.0, gap_min), max(0.0, gap_max)))


def http_get(url, retries=2, timeout=60, gap=(0.5, 1.0)):
    """GET 一个 URL，返回 bytes；失败重试 retries 次后抛 HttpError。"""
    last = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
            _sleep_between(*gap)
            return payload
        except Exception as exc:          # 网络/超时/5xx 都算可重试
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise HttpError("%s: %s" % (url, last))


def is_mpq(raw):
    """文件头判断：MPQ 头在 0 或者 0x200（user data header 形态）。"""
    if len(raw) < 0x20:
        return False
    if raw[:4] in MPQ_MAGICS:
        return True
    return len(raw) > 0x204 and raw[0x200:0x204] in MPQ_MAGICS


def norm_name(name):
    """把 replay 里的名字规范化，方便模糊匹配。"""
    text = html_lib.unescape(name or "")
    text = text.replace("<sp/>", "").replace("<sp />", "")
    text = re.sub(r"<[^>]{1,12}>", "", text)
    text = text.lower()
    return re.sub(r"[^a-z0-9]", "", text)


def name_matches(target, name):
    """目标选手名是否就是这个玩家。

    职业选手的 replay 名字常带战队标签（``<RBLN><sp/>ByuN``），所以去掉标签后
    再比。名字很短（<=3，例如 TY）时只认完全相等，避免 "ty" 误配到 "Tony"。
    """
    want = norm_name(target)
    got = norm_name(name)
    if not want or not got:
        return False
    if want == got:
        return True
    if len(want) <= 3:
        return False
    return want in got or got in want


def clean_display_name(name):
    text = html_lib.unescape(name or "")
    text = text.replace("<sp/>", " ").replace("<sp />", " ")
    text = re.sub(r"<[^>]{1,12}>", "", text)
    return " ".join(text.split()) or "?"


def canonical_matchup(races):
    """['T','Z'] -> 'TvZ'；两种族按 T,P,Z 排序，镜像就是 TvT。"""
    order = {"T": 0, "P": 1, "Z": 2}
    races = [r for r in races if r in order]
    if len(races) < 2:
        return "?"
    races = sorted(races, key=lambda r: order[r])
    return "%sv%s" % (races[0], races[1])


def safe_slug(text, limit=40):
    text = re.sub(r"[^\w\-. ]", "_", text or "", flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"_+", "_", text)
    return text.strip("._")[:limit].strip("._") or "unknown"


def entry_matchup(entry):
    races = [p.get("race") for p in entry.get("players") or [] if p.get("race")]
    return canonical_matchup(races)


def canonical_player(name):
    """目录名/小写选手名 -> 规范写法（maru -> Maru）。"""
    for known in DEFAULT_PLAYERS:
        if known.lower() == (name or "").lower():
            return known
    return name


def entry_key(entry):
    """同一局比赛的指纹：地图 + 双方 toon + 时长。用来跨目录去重。"""
    players = entry.get("players") or []
    ids = sorted((p.get("toon") or norm_name(p.get("name"))) for p in players)
    try:
        duration = round(float(entry.get("duration") or 0))
    except (TypeError, ValueError):
        duration = 0
    return (str(entry.get("map") or "").strip().lower(), tuple(ids), duration)


# --------------------------------------------------------------------------- 搜索
def parse_search_page(html_text):
    rows = {}
    for block in ROW_SPLIT.split(html_text)[1:]:
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
        version_match = VER_RE.search(block)
        date_match = TIME_RE.search(block)
        length_match = LEN_RE.search(block)
        maps = [m for m in MAP_RE.findall(block) if not m.strip().isdigit()]
        rows[replay_id] = {
            "id": replay_id,
            "version": version_match.group(1) if version_match else "",
            "date": date_match.group(1) if date_match else "",
            "time": date_match.group(2) if date_match else "",
            "length": length_match.group(1) if length_match else "",
            "players": [{"race": race[:1].upper(), "name": name.strip(),
                         "apm": int(apm) if apm else None}
                        for race, name, apm in players],
            "map": maps[0].strip() if maps else "",
        }
    return rows


def search_player(player, pages, gap, timeout):
    """翻若干页搜索某选手的 1v1，返回 id -> row。"""
    rows = {}
    for page_index in range(max(1, pages)):
        page = page_index * PAGE_STEP
        query = urllib.parse.urlencode({"player": player, "gt": "1", "page": page})
        url = "%s/search.php?%s" % (BASE, query)
        try:
            html_text = http_get(url, timeout=timeout, gap=gap).decode("utf-8", "replace")
        except HttpError as exc:
            print("  search fail p%d: %s" % (page, exc))
            continue
        found = parse_search_page(html_text)
        added = 0
        for replay_id, row in found.items():
            if replay_id not in rows:
                rows[replay_id] = row
                added += 1
        print("  search %-11s page=%-3d rows=%-3d new=%d" % (player, page, len(found), added))
        if not found:
            break
    return rows


def build_of(version):
    try:
        return int(str(version).split(".")[-1])
    except (TypeError, ValueError):
        return 0


def pick_candidates(player, rows, limit, min_build, seen_ids, seen_keys):
    """过滤出：5.x、目标是 T、还没在语料里的对局，按版本/日期从新到旧。"""
    chosen = []
    for row in rows.values():
        if build_of(row["version"]) <= min_build:
            continue
        if not row["version"].startswith("5."):
            continue
        target = [p for p in row["players"] if name_matches(player, p["name"])]
        if not target or target[0]["race"] != "T":
            continue
        if len(row["players"]) != 2:
            continue
        if row["id"] in seen_ids:
            continue
        chosen.append(row)
    chosen.sort(key=lambda r: (build_of(r["version"]), tuple(r["date"].split(".")[::-1]),
                               r["time"], int(r["id"])), reverse=True)
    out = []
    for row in chosen:
        if len(out) >= limit:
            break
        out.append(row)
    return out


# --------------------------------------------------------------------------- 校验
def verify_replay(path, player=None, min_build=MIN_BUILD, max_bytes=None):
    """解析校验；合格返回 (parsed, info)，不合格抛 Rejected。"""
    if not os.path.isfile(path):
        raise Rejected("missing-file")
    size = os.path.getsize(path)
    if max_bytes and size > max_bytes:
        raise Rejected("too-big")
    if size < 8 * 1024:
        raise Rejected("too-small")
    with open(path, "rb") as handle:
        head = handle.read(0x208)
    if not is_mpq(head):
        raise Rejected("not-mpq")

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from replay_analyzer.loader import load_replay

    try:
        parsed = load_replay(path, decode_events=False)
    except Exception as exc:
        raise Rejected("parse-error: %s" % str(exc)[:160])

    build = int(parsed.version.get("build") or 0)
    if build <= min_build:
        raise Rejected("build-too-old(%d)" % build)
    players = list(parsed.players)
    if len(players) != 2 or any(p.is_ai for p in players):
        raise Rejected("not-1v1-human")
    if parsed.duration_seconds < MIN_SECONDS:
        raise Rejected("too-short(%ds)" % int(parsed.duration_seconds))
    if not parsed.map_name or parsed.map_name == "(未知地图)":
        raise Rejected("no-map-name")

    if player:
        matched = [p for p in players if name_matches(player, p.name)]
        if not matched:
            raise Rejected("no-target-player(%s)" % ",".join(p.name for p in players))
        if not any(p.race == "T" for p in matched):
            raise Rejected("target-not-terran(%s)" % ",".join(p.race for p in matched))

    info = {
        "build": build,
        "protocol_build": parsed.protocol_build,
        "map": parsed.map_name,
        "duration": round(parsed.duration_seconds, 2),
        "players": players,
        "replay_time": parsed.replay_time,
    }
    return parsed, info


def manifest_entry(info, player_name, row, path, source):
    replay_time = info["replay_time"]
    date = str(replay_time)[:10] if replay_time else ""
    return {
        "player": player_name,
        "file": os.path.basename(path),
        "id": row.get("id", ""),
        "map": info["map"],
        "matchup": canonical_matchup([p.race for p in info["players"]]),
        "duration": info["duration"],
        "build": info["build"],
        "protocol_build": info["protocol_build"],
        "date": date,
        "contest_date": row.get("date", ""),
        "map_listed": row.get("map", ""),
        "players": [{"name": clean_display_name(p.name), "race": p.race,
                     "toon": p.toon, "result": p.result}
                    for p in info["players"]],
        "source": source,
        "bytes": os.path.getsize(path),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# --------------------------------------------------------------------------- 清单
def load_manifest(directory):
    path = os.path.join(directory, "manifest.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def load_corpus_index(out_root):
    """收集已有语料：id / 文件 / 对局指纹，用于去重。"""
    seen_ids = set()
    seen_keys = set()
    manifests = {}
    if not os.path.isdir(out_root):
        return seen_ids, seen_keys, manifests
    for name in sorted(os.listdir(out_root)):
        directory = os.path.join(out_root, name)
        if not os.path.isdir(directory):
            continue
        entries = load_manifest(directory)
        manifests[name] = entries
        for entry in entries:
            if entry.get("id"):
                seen_ids.add(str(entry["id"]))
            if entry.get("file"):
                seen_keys.add(entry_key(entry))
    return seen_ids, seen_keys, manifests


def write_manifest(directory, new_entries):
    """合并旧 manifest（保留人工补充的字段）并写回，按 build/文件名排序。"""
    old_entries = load_manifest(directory)
    on_disk = {name for name in os.listdir(directory) if name.lower().endswith(".sc2replay")}
    merged = [entry for entry in old_entries if entry.get("file") in on_disk]
    by_file = {entry.get("file"): entry for entry in merged}
    for entry in new_entries:
        by_file[entry["file"]] = entry
    merged = list(by_file.values())
    for entry in merged:
        if not entry.get("matchup"):
            entry["matchup"] = entry_matchup(entry)
    merged.sort(key=lambda e: (-int(e.get("build") or 0), str(e.get("date") or ""),
                               str(e.get("file") or "")))
    path = os.path.join(directory, "manifest.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(merged, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return merged


# --------------------------------------------------------------------------- 列候选
def describe_row(player, row):
    return " vs ".join("%s(%s)" % (p["name"], p["race"]) for p in row["players"])


def cmd_list(args, players):
    seen_ids, seen_keys, _ = load_corpus_index(args.out)
    all_rows = []
    for player in players:
        print("searching %s ..." % player)
        rows = search_player(player, args.pages, (args.gap_min, args.gap_max), args.timeout)
        candidates = pick_candidates(player, rows, args.limit, args.min_build,
                                     seen_ids, seen_keys)
        all_rows.extend((player, row) for row in candidates)
        print("  %-11s total=%-3d usable=%d" % (player, len(rows), len(candidates)))
    print()
    print("candidates (not downloaded):")
    print("  %-4s %-11s %-6s %-7s %-11s %-28s %-44s %s" % (
        "#", "player", "build", "ver", "date", "map", "players", "id"))
    for index, (player, row) in enumerate(all_rows, 1):
        print("  %-4d %-11s %-6d %-7s %-11s %-28s %-44s %s" % (
            index, player, build_of(row["version"]), row["version"][:7], row["date"],
            (row["map"] or "-")[:28], describe_row(player, row)[:44], row["id"]))
    print()
    print("total candidates: %d" % len(all_rows))
    if args.json:
        payload = [dict(row, requested_player=player) for player, row in all_rows]
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print("json: %s" % os.path.abspath(args.json))
    return 0


# --------------------------------------------------------------------------- 下载
def filename_for(player, row, opponent, build, map_name=None):
    """文件名格式：<player>_vs_<opponent>_<map>_<date>_b<build>_<id>.SC2Replay。

    map_name 传解析出来的地图名（站点列表里地图可能为空，写 "unknown" 不好看）。
    """
    return "%s_vs_%s_%s_%s_b%s_%s.SC2Replay" % (
        safe_slug(player, 20).lower(),
        safe_slug(opponent, 24),
        safe_slug(map_name or row.get("map") or "unknown", 30),
        (row.get("date") or "").replace(".", "-"),
        build,
        row["id"])


def cmd_fetch(args, players):
    out_root = args.out
    seen_ids, seen_keys, manifests = load_corpus_index(out_root)
    print("corpus index: ids=%d games=%d dirs=%s"
          % (len(seen_ids), len(seen_keys), ",".join(sorted(manifests)) or "-"))

    log = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "players": {}, "files": []}
    total_ok = 0
    for player in players:
        display = canonical_player(player)
        player_dir = os.path.join(out_root, safe_slug(player, 40).lower())
        # 目录名沿用选手名小写；目录里已有文件的玩家名以 manifest 为准
        existing = load_manifest(player_dir)
        if existing and existing[0].get("player"):
            display = existing[0]["player"]
        print("\n[%s] search -> %s" % (player, player_dir))
        rows = search_player(player, args.pages, (args.gap_min, args.gap_max), args.timeout)
        candidates = pick_candidates(player, rows, args.limit, args.min_build,
                                     seen_ids, seen_keys)
        print("  candidates=%d (limit=%d)" % (len(candidates), args.limit))

        new_entries = []
        stats = {"ok": 0, "rejected": 0, "http_fail": 0}
        for row in candidates:
            if stats["ok"] >= args.limit:
                break
            source = "%s/show.php?id=%s" % (BASE, row["id"])
            download_url = "%s/down.php?id=%s" % (BASE, row["id"])
            try:
                raw = http_get(download_url, retries=args.retries, timeout=args.timeout,
                               gap=(args.gap_min, args.gap_max))
            except HttpError as exc:
                stats["http_fail"] += 1
                print("  http  id=%-6s %s" % (row["id"], str(exc)[-90:]))
                log["files"].append({"id": row["id"], "player": player,
                                     "status": "http-fail"})
                continue
            if not is_mpq(raw):
                stats["rejected"] += 1
                print("  ad    id=%-6s not MPQ (%r), skipped" % (row["id"], raw[:8]))
                log["files"].append({"id": row["id"], "player": player,
                                     "status": "not-mpq"})
                continue
            if args.max_bytes and len(raw) > args.max_bytes:
                stats["rejected"] += 1
                print("  big   id=%-6s %d bytes, skipped" % (row["id"], len(raw)))
                continue

            opponent = next((p["name"] for p in row["players"]
                             if not name_matches(player, p["name"])), "unknown")
            path = os.path.join(player_dir, filename_for(player, row, opponent,
                                                         build_of(row["version"])))
            os.makedirs(player_dir, exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(raw)
            try:
                _, info = verify_replay(path, player=player, min_build=args.min_build,
                                        max_bytes=args.max_bytes)
                key = (str(info["map"]).strip().lower(),
                       tuple(sorted((p.toon or norm_name(p.name)) for p in info["players"])),
                       round(info["duration"]))
                if key in seen_keys:
                    raise Rejected("duplicate-game")
            except Rejected as exc:
                stats["rejected"] += 1
                print("  drop  id=%-6s %s -> deleted" % (row["id"], exc))
                log["files"].append({"id": row["id"], "player": player,
                                     "status": "rejected", "reason": str(exc)})
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue

            # 用解析出来的真实地图名重命名（站点列表里偶尔没写地图）
            final_name = filename_for(player, row, opponent, build_of(row["version"]),
                                      map_name=info["map"])
            final_path = os.path.join(player_dir, final_name)
            if final_path != path:
                os.replace(path, final_path)
                path = final_path

            entry = manifest_entry(info, display, row, path, source)
            new_entries.append(entry)
            seen_ids.add(row["id"])
            seen_keys.add(key)
            stats["ok"] += 1
            total_ok += 1
            print("  ok    id=%-6s %-46s build=%-6d %-11s %4d:%02d  %s" % (
                row["id"], entry["file"][:46], entry["build"], entry["matchup"],
                int(entry["duration"]) // 60, int(entry["duration"]) % 60, info["map"]))
            log["files"].append({"id": row["id"], "player": player, "status": "ok",
                                 "file": entry["file"], "build": entry["build"],
                                 "map": entry["map"]})

        merged = write_manifest(player_dir, new_entries) if os.path.isdir(player_dir) else []
        log["players"][player] = {"ok": stats["ok"], "rejected": stats["rejected"],
                                  "http_fail": stats["http_fail"],
                                  "manifest_total": len(merged),
                                  "dir": player_dir}
        print("  -> %s: new=%d rejected=%d http_fail=%d manifest=%d" % (
            player, stats["ok"], stats["rejected"], stats["http_fail"], len(merged)))

    log["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    log["total_ok"] = total_ok
    if args.log:
        os.makedirs(os.path.dirname(os.path.abspath(args.log)) or ".", exist_ok=True)
        with open(args.log, "w", encoding="utf-8") as handle:
            json.dump(log, handle, ensure_ascii=False, indent=2)
        print("\nlog: %s" % os.path.abspath(args.log))
    print("\ndone: %d new replay(s)" % total_ok)
    return 0 if total_ok else 1


# --------------------------------------------------------------------------- 复验
def cmd_verify(args):
    """不联网，重扫输出目录：逐个解析、报告问题、（可选）删掉坏文件、刷新 manifest。"""
    out_root = args.out
    if not os.path.isdir(out_root):
        print("no such dir: %s" % out_root)
        return 1
    total = bad = 0
    dirs = 0
    for name in sorted(os.listdir(out_root)):
        directory = os.path.join(out_root, name)
        if not os.path.isdir(directory):
            continue
        files = sorted(fn for fn in os.listdir(directory) if fn.lower().endswith(".sc2replay"))
        if not files:
            continue
        dirs += 1
        old_entries = load_manifest(directory)
        old_by_file = {entry.get("file"): entry for entry in old_entries}
        display = ""
        for entry in old_entries:
            if entry.get("player"):
                display = entry["player"]
                break
        display = display or canonical_player(name)
        display = canonical_player(display)
        new_entries = []
        print("\n[%s] %d file(s)" % (name, len(files)))
        for fn in files:
            path = os.path.join(directory, fn)
            total += 1
            try:
                _, info = verify_replay(path, player=None, min_build=args.min_build)
            except Rejected as exc:
                bad += 1
                print("  BAD %-52s %s" % (fn[:52], exc))
                if args.prune:
                    os.remove(path)
                    print("      deleted")
                continue
            target_ok = ""
            if name.lower() != "maru":
                target_ok = "" if any(name_matches(name, p.name) for p in info["players"]) \
                    else "  (no %s in players)" % name
            print("  ok  %-52s build=%-6d %-11s %4d:%02d %s%s" % (
                fn[:52], info["build"],
                canonical_matchup([p.race for p in info["players"]]),
                int(info["duration"]) // 60, int(info["duration"]) % 60,
                info["map"][:26], target_ok))
            row = {}
            old = old_by_file.get(fn)
            if old:
                row = {"id": old.get("id", ""), "date": old.get("contest_date", ""),
                       "map": old.get("map_listed", "")}
            entry = manifest_entry(info, display, row, path, old.get("source", "") if old else "")
            if old:
                entry["id"] = old.get("id", "")
                entry["source"] = old.get("source", "")
                entry["contest_date"] = old.get("contest_date", "")
                entry["map_listed"] = old.get("map_listed", "")
            new_entries.append(entry)
        merged = write_manifest(directory, new_entries)
        print("  manifest: %d entries" % len(merged))
    print("\nverified %d file(s) in %d dir(s), bad=%d" % (total, dirs, bad))
    return 0 if bad == 0 else 2


# --------------------------------------------------------------------------- CLI
def split_players(values):
    players = []
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if part and part not in players:
                players.append(part)
    return players


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="从 sc2rep.ru 抓职业人族 replay（参数化 + 逐个校验）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--player", action="append", default=[],
                        help="选手名，可重复或逗号分隔（默认 %s）"
                             % "/".join(DEFAULT_PLAYERS))
    parser.add_argument("--all-terran", action="store_true",
                        help="抓默认的六名职业人族")
    parser.add_argument("--limit", type=int, default=10, help="每个选手最多新增多少局")
    parser.add_argument("--out", default=DEFAULT_OUT, help="输出根目录（默认 replays/pro）")
    parser.add_argument("--pages", type=int, default=2, help="每名选手翻几页搜索（每页 20 条）")
    parser.add_argument("--min-build", type=int, default=MIN_BUILD,
                        help="最低 build（默认 80001，即只要 5.x）")
    parser.add_argument("--max-bytes", type=int, default=20 * 1024 * 1024,
                        help="单文件上限，超过当广告/异常文件跳过")
    parser.add_argument("--retries", type=int, default=2, help="单个 URL 的失败重试次数")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP 超时（秒）")
    parser.add_argument("--gap-min", type=float, default=0.5, help="请求间隔下限（秒）")
    parser.add_argument("--gap-max", type=float, default=1.0, help="请求间隔上限（秒）")
    parser.add_argument("--list", action="store_true", help="只列候选，不下载")
    parser.add_argument("--json", default="", help="把候选列表另存为 json（配合 --list）")
    parser.add_argument("--log", default=os.path.join(".tmp", "fetch_pro_replays.json"),
                        help="抓取日志路径（默认 .tmp/fetch_pro_replays.json）")
    parser.add_argument("--verify-only", action="store_true",
                        help="不联网，重扫 --out 下所有目录并刷新 manifest")
    parser.add_argument("--prune", action="store_true",
                        help="配合 --verify-only：删掉解析不过的文件")
    args = parser.parse_args(argv)

    if args.verify_only:
        return cmd_verify(args)

    players = split_players(args.player)
    if not players or args.all_terran:
        players = list(DEFAULT_PLAYERS)
    print("players: %s" % ", ".join(players))
    if args.list:
        return cmd_list(args, players)
    return cmd_fetch(args, players)


if __name__ == "__main__":
    sys.exit(main())
