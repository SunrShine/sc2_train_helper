# SC2 对局分析工具（replay_analyzer）

把《星际争霸 II》的 `.SC2Replay` 解析成结构化数据，算出经济、军队、操作、交战等指标，
最后生成一份**可离线打开的单文件 HTML 报告**（内联 SVG 图表，不联网、不依赖任何前端库）。

- 纯 Python 标准库 + 两个随仓库内置的库（`mpyq` 读 MPQ 容器、`s2protocol` 解码二进制事件流）
- 不需要 `pip install`，不需要联网，双击 HTML 就能看
- 已在 **999 局真实 replay**（2 / 4 / 9 / 12 人、10+ 个游戏版本、天梯与自定义地图）上跑通，解析失败 0 局

## 快速开始

```powershell
# 分析单局，生成 HTML
python -m replay_analyzer analyze "D:\Replays\MyGame.SC2Replay"

# 指定输出路径，并同时导出 JSON；--open 生成后自动用浏览器打开
python -m replay_analyzer analyze "D:\Replays\MyGame.SC2Replay" `
    -o "reports\我的对局.html" --json "reports\我的对局.json" --open

# 批量分析一个目录（递归找 *.SC2Replay），额外生成 index.html 索引
python -m replay_analyzer batch "C:\Users\sun\Documents\StarCraft II\Accounts\556922532\5-S2-1-8354391\Replays\Multiplayer" `
    -o reports

# 导出解析后的原始结构（调试 / 二次开发用）
python -m replay_analyzer dump "D:\Replays\MyGame.SC2Replay" -o raw.json
```

`python -m replay_analyzer "D:\Replays\MyGame.SC2Replay"` 也可以直接用，等价于 `analyze`。

Windows 上还可以把 replay 文件直接拖到 `sc2analyze.cmd` 上（脚本会自动切到项目目录并调用上面的命令）。

环境要求：Python 3.8+（开发和验证环境为 Windows + Python 3.14）。

## 操作细节分析（micro）：多线重叠与操作顺序

除了"这局谁赢了"，工具还能只看**操作层面**：视角节奏、多线重叠窗口、操作顺序、编队使用、交火反应时间。

```powershell
# 单局：生成操作细节报告（含每个多线窗口的完整操作序列）
python -m replay_analyzer micro "D:\Replays\MyGame.SC2Replay" -o reports\micro.html --open

# 批量：拿一个目录里最近 N 局算指标趋势（练习打卡用）
python -m replay_analyzer micro "C:\Users\sun\Documents\StarCraft II\Accounts\556922532\5-S2-1-8354391\Replays\Multiplayer" --limit 40 -o reports\micro-trend.html
```

报告里有：视角切换频率 / 单次停留、每分钟多线窗口数、多线覆盖时长、不下指令的空档、
交火后首次指令延迟、编队槽使用，以及**每个多线窗口按时间顺序展开的操作序列**
（`0.0s 视角→我方二矿 / 0.5s 指令→敌方三矿 / 1.2s 调出编队 4 …`），可以直接拿来复盘顺序。

配套的练习方法见 [docs/多线练习手册.md](docs/多线练习手册.md)。

指标口径：**多线窗口** = 6 秒内在两个相距 ≥25 格的位置各下 ≥2 条指令；**指令** = 实际下达的命令，
不含选中和视角移动；**空档** = 连续 8 秒以上没有下任何指令。

## 录像库与对照组（和职业选手比）

目录约定（不是强制的，`library` 只按文件夹归类，不改你的文件）：

```
<我的录像目录>/            天梯/自定义，随便分不分子目录
<职业根目录>/<选手名>/     一个选手一个文件夹，比如 replays/pro/maru/
```

```powershell
# 1) 建库索引：扫描、解析、标出"放在 maru 文件夹里但没有 Maru"的文件
python -m replay_analyzer library --me "…\Replays\Multiplayer" --pro replays\pro -o reports\library.html

# 2) 对照组：同一对位下，我和职业选手的操作指标 + 结构最像的多线窗口配对
python -m replay_analyzer compare --me "…\Replays\Multiplayer" --pro replays\pro\maru `
    --matchup TvZ --limit 30 -o reports\compare-tvz.html --open

# 3) 抓职业录像（示例：Maru；换选手改 tools\fetch_maru.py 里的名字）
python tools\fetch_maru.py 8
```

`compare` 产出：中位数对比表（视角 / 多线密度 / 覆盖 / 指令 / 空档）、逐局明细，以及**窗口配对**——
把你的一个多线窗口和职业选手"形状最像"的窗口并排放在一起（跨度、指令数、时长、切换次数、
指令间隔五个维度归一化后算距离，优先同阶段），左右两栏各自展开完整操作序列，
直接看"同样的两条线，他第一条指令给了哪边"。

### GIF 卡片：把一段多线做成模板

```powershell
# 单张：默认挑这局最"满"的多线窗口，也可以 --at 7:09 指定时间
python -m replay_analyzer card "replays\pro\maru\xxx.SC2Replay" --player Maru --duration 8 -o reports\cards\maru.gif

# 批量：对照组每个配对各出一张（我一张 + 他一张）+ cards.html 索引
python -m replay_analyzer compare --me "…\Multiplayer" --pro replays\pro\maru --matchup TvZ --cards reports\cards
```

卡片是小地图动画（420×400，8~10 秒）：**白框**是当时的视角，**金/白十字**是刚下的指令（越亮越新），
**红色虚线圈 A/B** 是这一波的两条线。用 ffmpeg 编码（有就自动用，GIF 约 20–40KB/张）；
没装 ffmpeg 会退化成 PNG 序列 + `play.html` 播放页。

配套的练习方法见 [docs/多线练习手册.md](docs/多线练习手册.md)。

## 战争迷雾（fog）：复盘式的"开图"

先说清楚边界：**这不是联机开图外挂**。它只读 replay 文件、只在本机内存里重算，
不注入游戏进程、不读内存、对进行中的对局没有任何作用。它做的事情是：
把"全图视角的 replay"翻译回"当时双方各自的眼睛"，让你看清自己什么时候是透明的。

```powershell
# 生成迷雾复盘报告；--gif 顺带出一段迷雾动画
python -m replay_analyzer fog "D:\Replays\MyGame.SC2Replay" --gif --open

# 从对手视角看同一段时间（"在他眼里你是什么样的"）
python -m replay_analyzer fog "D:\Replays\MyGame.SC2Replay" --me "对手名字" --gif --at 14:10 --duration 32 -o reports\fog-opp.html

# 敏感度检查：视野半径整体放大 15%，看结论会不会翻
python -m replay_analyzer fog "D:\Replays\MyGame.SC2Replay" --sight-scale 1.15
```

模型很简单：每个存活单位是一个视野圆（半径见 `replay_analyzer/sight.py`），目标点落在任意
一个己方圆内就算看得见；三个状态分别是**从没看过 / 看过但现在没视野 / 当前可见**，
报告里的动画就是这三层配色。指标是双向的：我的部队有多少时间在他的视野里、他的部队有多少
时间在我的视野里、各自视野覆盖多大面积、第一次被看见是什么时候、哪些区域最容易暴露。

**这套数的误差必须说在前面**（报告里也写了）：

- SC2 的 replay 是"指令日志 + 定期位置快照"，**不是逐帧轨迹**：大多数单位整局只有两三个位置点，
  中间靠线性插值。所以"某一秒他在哪"本身就有几十秒量级的模糊，百分比只能当量级看；
  报告会同时给出"插值 / 不插值"两个口径，两个数的差就是这类误差的尺度。
- 没有地形数据，所以不判高地/悬崖/岩石的遮挡——隔着悬崖的"看见"会被高估。
- 视野半径是手册值（个别 ±1），不判隐形与探测单位。

## 报告里有什么

| 板块 | 内容 |
| --- | --- |
| 对局概览 | 地图、时长、游戏速度、版本、日期、模式、一段自动生成的"这局发生了什么" |
| 双方数据 | APM / 峰值 APM / 农民峰值 / 军队价值峰值 / 人口峰值 / 累计采集 / 损失 / 击杀 / 基地数 |
| 关键结论 | 自动洞察：开矿节奏、农民差、军队价值差、人口卡顿、生产空窗、最亏/最赚的一波、操作频率 |
| 曲线 | 军队价值、农民数、采集效率、未花资源、人口、科技投入、累计损失、APM（8 张图） |
| 关键时刻对比 | 5 / 8 / 10 / 12 / 15 / 20 分钟的农民、军队、收入横向对比（赢的一侧标绿） |
| 基地与扩张 | 每个基地的建造时间 |
| 主要交战 | 按损失资源排序的战斗，双方损失了多少单位 / 资源、打了多久 |
| 建造顺序 | 双方完整建造顺序（时间 / 事件 / 分类），可按"经济 / 生产 / 科技 / 部队 / 升级 / 扩张"筛选 |
| 单位构成 | 按资源价值的单位构成条 + 生产 / 存活 / 损失表 |
| 聊天记录 | 局内聊天（如果有） |

## 指标口径

这些口径写死在 `replay_analyzer/metrics.py` 里，改起来也方便：

- **时间**：`游戏秒 = gameloop / 16`，和游戏内计时器一致（所以 18:11 就是游戏里的 18:11）
- **APM**：每分钟的"操作事件"数，包含指令、选中、编队、资源转移；镜头拖动单独统计，不计入 APM
- **军队价值**：`已投入当前军队的矿 + 气`（气按 1:1 计入，与社区常用的"资源价值"一致）
- **采集效率**：直接用游戏统计里的 `collection rate`（资源/分钟）
- **损失 / 击杀**：用游戏结算统计的累计值（按矿、气、军队/经济/科技分类）
- **交战**：把 25 秒内发生的部队死亡聚成一波，按双方死亡单位的造价折算损失
- **人口卡顿**：统计快照里"已用人口 ≥ 人口上限"且上限 < 200 的持续时间，忽略开局 1 分钟
- **生产空窗**：相邻两个部队出生事件间隔 ≥ 75 秒，且期间手头资源 ≥ 500
- **本机玩家**：从 replay 路径里的 `5-S2-1-8354391` 这样的 toon 反查，命中的玩家标成"你"

## 目录结构

```
replay_analyzer/
  sc2mpq.py     MPQ 容器读取（.SC2Replay 本质是 MPQ 压缩包）
  protocols.py  按 replay build 选协议版本，缺失时向相邻版本降级
  loader.py     解析 header/details/initData/tracker/game/message 事件 → ParsedReplay
  units.py      单位与升级的中文名、分类、造价字典
  metrics.py    指标计算与自动洞察（纯数据，可 JSON 序列化）
  micro.py      操作细节分析：视角、多线重叠窗口、编队、空档、操作顺序
  sight.py      单位视野半径字典（可被 data/sight_radius.json 覆盖）
  fog.py        战争迷雾还原：视野圆并集、双向暴露统计、数据质量
  fogrender.py  迷雾三态动画（黑 / 阴影 / 可见）GIF
  fog_report.py 迷雾复盘报告
  library.py    录像库扫描与归类（只读）
  compare.py    对照组：我和职业选手的指标对比 + 窗口相似度配对
  charts.py     纯 Python 生成内联 SVG 折线图
  report.py     对局分析 HTML 报告
  micro_report.py  操作细节报告 + 批量趋势报告
  compare_report.py  对照组报告 + 录像库索引
  cli.py        命令行入口（analyze / batch / micro / library / compare / card / fog / dump）
docs/
  多线练习手册.md   配合 micro / compare 的练习流程（drill、打分卡、一周排期）
tools/
  fetch_maru.py     从 sc2rep.ru 抓指定选手的 replay（默认 Maru）
vendor/
  mpyq.py       MPQ 读取库（MIT）
  s2protocol/   暴雪官方的 replay 二进制协议解码库（MIT，已打补丁适配 Python 3.12+）
```

## 实现说明（踩过的坑）

1. **.SC2Replay 不是单个文件格式**，而是一个 MPQ 压缩包：开头可能是 `MPQ\x1b` 的 user data 头，
   真正的 MPQ 头在偏移 1024 处；里面装着 `replay.header`、`replay.details`、`replay.tracker.events` 等 15 个文件。
2. **事件是位打包的二进制**，字段定义随游戏版本变化，因此必须用对应 build 的协议模块解码。
   暴雪不会为每个 hotfix build 都生成模块（例如本局的 build 98370 就没有，`s2protocol` 最新只到 98310），
   工具会自动选择最接近的可用版本并降级解码，报告页脚会标注"协议近似"。
3. **`s2protocol` 用了 Python 3.12 已移除的 `imp` 模块**，仓库内的 `vendor/s2protocol/versions/__init__.py`
   已替换成 `importlib` 实现，行为保持一致。
4. **玩家编号有两套**：tracker 事件里的 `playerId` 从 1 开始，game 事件里的 `m_userId` 从 0 开始，
   通过 `SPlayerSetupEvent` 对齐；种族优先取 `replay.attributes.events` 里语言无关的 `3001` 属性
   （`Terr` / `Prot` / `Zerg`），取不到再从单位反推。

## 已知限制

- 单位中文名表覆盖主流单位（约 200 条），冷门、战役、合作模式单位会退化成英文驼峰名，但不会报错
- 自定义地图 / 合作模式 / 12 人乱斗的"胜负"和"经济"含义与天梯不同，指标仅供参考
- 洞察规则按 1v1 天梯设计，多人局会只用"本机玩家 + 一个对手"做对比
- 对话、镜头轨迹等数据已解析但报告里只展示了聊天
- 费用表用于折算战损，个别单位（如执政官、虫族变体）造价是近似值

## 二次开发

```python
from replay_analyzer.loader import load_replay
from replay_analyzer import metrics, report

parsed = load_replay(r"D:\Replays\MyGame.SC2Replay")   # 原始事件、单位生命周期、统计快照
analysis = metrics.analyze(parsed)                     # dict，可直接 JSON 化
report.write_report(analysis, "out.html")              # 渲染 HTML
```

- 加单位中文名 / 造价：改 `units.py` 的 `UNIT_NAMES_ZH`、`UNIT_COST`
- 加新的洞察：在 `metrics._insights()` 里加一条 `add(级别, 标题, 文案, 权重)`
- 加新的图表：`metrics._build_timelines()` 里加一条定义即可，`report.py` 会自动遍历渲染
