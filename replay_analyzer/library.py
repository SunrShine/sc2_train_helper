"""录像库：扫描目录、读元信息、按文件夹归类。

约定（不是强制的，你手动整理成什么样都行）：

    <my_dir>/              我的天梯/自定义录像（可以再分子目录）
    <pro_root>/<player>/   某个职业选手的录像，一个选手一个文件夹

`library` 只读不写，产出 JSON + HTML 索引，并标出"放在 pro/maru 里但 Maru 不在对局里"
这类疑似放错的文件——移动还是你自己来。
"""

import glob
import json
import os

from . import micro
from .loader import load_replay


def _guess_pro_name(path, root):
    """pro_root/maru/xxx.SC2Replay -> maru"""
    relative = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    parts = relative.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else ""


def scan(root, tag="", player_filter=None):
    """扫描一个目录，返回该目录下所有 replay 的元信息。"""
    root = os.path.abspath(root)
    paths = sorted(glob.glob(os.path.join(root, "**", "*.SC2Replay"), recursive=True))
    entries = []
    for path in paths:
        try:
            parsed = load_replay(path, decode_events=False)
        except Exception as exc:
            entries.append({"path": path, "file": os.path.basename(path),
                            "tag": tag, "error": str(exc)[:120]})
            continue
        pro_name = _guess_pro_name(path, root) if tag == "pro" else ""
        players = [{"name": p.name, "race": p.race, "result": p.result,
                    "toon": p.toon, "is_local": p.is_local} for p in parsed.players]
        local = next((p for p in players if p["is_local"]), None)
        suspect = ""
        if pro_name:
            names = {p["name"].lower() for p in players}
            if pro_name.lower() not in names:
                suspect = "文件夹是 %s，但对局里没有这个人" % pro_name
        entries.append({
            "path": path,
            "file": os.path.basename(path),
            "tag": tag,
            "pro_name": pro_name,
            "map": parsed.map_name,
            "build": parsed.version.get("build"),
            "protocol_build": parsed.protocol_build,
            "duration": parsed.duration_seconds,
            "date": parsed.replay_time.astimezone().strftime("%Y-%m-%d %H:%M")
                    if parsed.replay_time else "",
            "players": players,
            "local": local,
            "matchup": "".join(p["race"] for p in players[:2]),
            "suspect": suspect,
            "bytes": os.path.getsize(path),
        })
    return entries


def build_index(roots):
    """roots: [(path, tag), ...]"""
    entries = []
    for path, tag in roots:
        if not os.path.isdir(path):
            continue
        entries.extend(scan(path, tag=tag))
    ok = [e for e in entries if not e.get("error")]
    pro_players = {}
    for entry in ok:
        if entry["tag"] == "pro" and entry.get("pro_name"):
            pro_players.setdefault(entry["pro_name"], []).append(entry)
    summary = {
        "total": len(entries),
        "parsed": len(ok),
        "failed": len(entries) - len(ok),
        "me": len([e for e in ok if e["tag"] == "me"]),
        "pro": len([e for e in ok if e["tag"] == "pro"]),
        "suspect": [e for e in ok if e.get("suspect")],
        "pro_players": {name: len(items) for name, items in pro_players.items()},
        "matchups": {},
    }
    for entry in ok:
        key = entry["matchup"] if entry["tag"] == "me" else "%s（职业）" % entry["matchup"]
        summary["matchups"][key] = summary["matchups"].get(key, 0) + 1
    return {"entries": entries, "summary": summary}


def write_manifest(index, output_path):
    directory = os.path.dirname(os.path.abspath(output_path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=1)
    return output_path
