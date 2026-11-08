"""读取 .SC2Replay 的 MPQ 容器。

.SC2Replay 是一个 MPQ（MoPaQ）压缩包，里面装着 replay.header、replay.details、
replay.tracker.events 等若干"文件"。这里只做容器层的事情：
打开、列出、取出原始字节。字节的语义由 protocols / loader 负责。
"""

import os

from ._vendor import ensure_vendor_path

ensure_vendor_path()

import mpyq  # noqa: E402  (必须在 _vendor.ensure_vendor_path() 之后导入)


class ReplayArchive(object):
    """对一个 .SC2Replay 文件的只读访问封装。"""

    def __init__(self, path):
        if not os.path.isfile(path):
            raise IOError("找不到 replay 文件：%s" % path)
        self.path = os.path.abspath(path)
        self._archive = mpyq.MPQArchive(self.path, listfile=True)
        self.header = self._archive.header

    # ------------------------------------------------------------------ 基本信息
    @property
    def files(self):
        return [name.decode("utf-8", "replace") for name in self._archive.files]

    @property
    def user_data_header(self):
        return self.header.get("user_data_header")

    # ------------------------------------------------------------------ 读数据
    def read(self, name):
        """按文件名取出解压后的字节；不存在返回 None。"""
        data = self._archive.read_file(name.encode("ascii"))
        return data

    def require(self, name):
        data = self.read(name)
        if data is None:
            raise KeyError("replay 中缺少文件：%s（可用文件：%s）"
                           % (name, ", ".join(sorted(self.files))))
        return data

    def close(self):
        try:
            self._archive.file.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False
