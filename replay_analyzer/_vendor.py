"""让 vendor/ 目录里的第三方库（mpyq、s2protocol）可被导入。

工具本身不依赖 pip / 联网：这两个库已经随仓库一起提供。
如果系统里已经安装了同名库，优先使用仓库内的版本，保证结果可复现。
"""

import os
import sys

VENDOR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor"
)


def ensure_vendor_path():
    if not os.path.isdir(VENDOR_DIR):
        raise RuntimeError(
            "找不到 vendor 目录：%s（依赖 mpyq / s2protocol 应放在这里）" % VENDOR_DIR
        )
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    return VENDOR_DIR
