"""协议版本选择。

replay.header 里有 m_baseBuild（游戏 build 号），s2protocol 需要与之对应的
protocolXXXXX 模块来解码二进制事件流。暴雪并不会为每个 build 生成模块
（例如 5.0.15 的 98370 就没有对应文件），实践中相邻 build 的协议是兼容的：
优先精确匹配，其次向下找最近的，再其次向上找。
"""

import re

from ._vendor import ensure_vendor_path

ensure_vendor_path()

from s2protocol.versions import list_all  # noqa: E402


_PATTERN = re.compile(r"protocol(\d+)\.py$")


def available_builds():
    """返回仓库内可用的全部 build 号（升序）。"""
    builds = []
    for name in list_all():
        match = _PATTERN.match(name)
        if match:
            builds.append(int(match.group(1)))
    builds.sort()
    return builds


def resolve(build, available=None):
    """给定 replay 的 build，返回 (模块, 实际使用的 build, 提示信息)。

    提示信息在 build 不完全一致时给出，用于报告里标注"协议近似"。
    """
    from s2protocol.versions import build as load_protocol

    if available is None:
        available = available_builds()
    if not available:
        raise RuntimeError("s2protocol 里没有任何可用协议版本")

    if build in available:
        return load_protocol(build), build, ""

    lower = [b for b in available if b < build]
    if lower:
        chosen = lower[-1]
        module = load_protocol(chosen)
        note = ("replay build %d 没有对应协议定义，已用最接近的 build %d 解码"
                "（相邻版本协议通常兼容）" % (build, chosen))
        return module, chosen, note

    chosen = available[0]
    return load_protocol(chosen), chosen, (
        "replay build %d 比本地协议库更旧，使用 %d 解码" % (build, chosen))
