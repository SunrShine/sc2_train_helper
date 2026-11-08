"""SC2 对局分析工具 (sc2_analyzer).

把 StarCraft II 的 .SC2Replay 解析成结构化数据，计算经济/军队/操作等指标，
并生成可离线打开的单文件 HTML 报告。

模块划分::

    sc2mpq      MPQ 容器读取（.SC2Replay 本质是一个 MPQ 压缩包）
    protocols   根据 replay build 选择 s2protocol 协议版本（带降级回退）
    units       单位/升级的名称与分类字典
    loader      解析 replay -> ParsedReplay
    metrics     指标计算（曲线、建造顺序、战斗、洞察）
    charts      纯 Python 生成内联 SVG 图表
    report      HTML 报告渲染
    cli         命令行入口
"""

__version__ = "1.0.0"
