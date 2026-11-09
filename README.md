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

**仓库里不带录像**（赛事素材 + 体积原因）。要用职业对局对照，先自己抓：

```powershell
python tools\fetch_pro_replays.py --player Maru,Clem,Serral --limit 10 --out replays\pro
```

自己的录像不用拷贝，直接在命令里用 `--mine "C:\Users\…\Replays\Multiplayer"` 指过去即可。

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

## 经济模型（econ）：先对账，再谈预测

**核心发现**：replay 的 `SPlayerStatsEvent` 自带一本按用途分类的账本，
所以收入不用猜，可以直接算：

```
累计收入 = 手上剩的 + Σ存活单位已花 + Σ建造中已花 + Σ已损失
```

econ 把这个公式做成可运行的对账工具，并顺手拟合了收入模型：

```powershell
python -m replay_analyzer econ --limit 90          # 约 1 分钟
```

- **收入模型**（从 90 局拟合）：`矿物/分钟 = a × min(农民, 16×基地)`，a = 25.1；气 = 67.4/气矿
- **两个采速系数必须分开**（这是最容易踩的坑）：
  - 理论上限 **45 矿/分钟/农民**（p90 收敛测出来的硬上限，仿真器用）
  - 实战有效值 **25.1**（含走路、闲置、气矿占人；复盘预测用）
  - 两者之比就是**采矿效率**：本机我的局 52%，职业局 76%（同一条测量规则下）
  - 气体没有这个折损：3 农民满采 = **120 气/分钟**，实测 p50/p90 全是 120
- **对账**：单位造价合计 vs 账本总收入，四局合计误差 +0.26%，单局有 ±5% 量级的口径噪声
- 报告里明确写了误差来源（取消退款、morph、死亡时点、未知升级），没有粉饰

## 建造顺序（bo）：按时间线分段，再聚类

```powershell
python -m replay_analyzer bo --limit 60 --matchup TvZ --open
```

从 replay 的建造事件提取**按时间线分段的 BO**（opening 0-3 分 / early 3-6 分 / mid 6-10 分 / late 10 分后），
每段带时间戳、造价、关键动作，并能自动识别开局签名（人族 `1-rax-fast-expand` / `2-1-1` / `1-1-1` / `3-rax` / `mech`；
异虫 `hatch-first` / `pool-first` / `greedy-3hatch`；星灵 `nexus-first` / `gateway-expand` / `2gate-pressure` / `proxy`）。

### 版本过滤：开局农民数才是硬判据

本机 950 局跨了两个版本，**开局农民数**能干净地区分：

| 时间 | 开局农民 | build |
|---|---|---|
| 2026-01 ~ 2026-06 | 12 | 96163 … 96999 |
| **2026-07 ~ 2026-10** | **8** | 97377 / 97426 / 97579（10 月混有 12） |
| 2026-10 ~ 2026-11 | 12 | 98370 |

不加过滤直接混在一起统计，开局时间点会整体前移 20~30 秒，聚出来的"打法差异"其实是**版本差异**。
所以默认 `--start-workers 12` 硬过滤（版本无关，比月份可靠）。

### 三个对位分开做（TvZ / TvP / TvT）

**不同对位是不同的对抗，不能混在一起统计**：开局节奏、对手威胁、常见打法都不一样。
所以每个对位独立过滤版本、独立聚类、独立和**同对位**职业选手对照。
默认一次跑三个（`--matchups TvZ,TvP,TvT`），每个对位上限 60 局。

本机实测（244 局 = 我 176 + 职业 68，全部 12 农民版本）：

| 对位 | 我的局数 | 开局（0–3 分钟序列） | 同对位职业样本 |
|---|---|---|---|
| **TvZ** | 60 | `rax-gas-cc` **88%** ／ `rax-gas-gas-fac-reac` 12% | 28（Maru 17 / Cure 6 / Clem 5） |
| **TvP** | 56（全库就这些） | `rax-gas-cc` **98%** ／ `cc-gas-rax-gas` 2% | 20（Maru 14 / Clem 4 / HeroMarine 2） |
| **TvT** | 60 | `rax-gas-gas-fac` **58%** ／ `rax-gas-rax-gas` 33% ／ 气先 5% ／ 双兵营 3% | 20（Maru 10 / ByuN 6 / HeroMarine 4） |

三个对位的开局**形态完全不同**：TvZ 是"兵营→气矿→二矿"一条路走到黑；
TvT 主流是**双气先起重工**（58%），跟 TvZ 正好相反；TvP 则几乎只有一套（98%）。

### 我和职业选手的差距（同对位中位数，秒；+ 表示我更晚）

| 节点 | TvZ | TvP | TvT |
|---|---|---|---|
| 第一气矿 | +3 | +2 | +3 |
| 二矿 | +4 | **−38** | −7 |
| 第二气矿 | **−56** | **+110** | +7 |
| 重工 | −3 | **+33** | **+10** |
| 星港 | −8 | **+26** | −13 |
| 第三兵营 | **−26** | **−50** | **−50** |
| 主力出门 | **+60** | −10 | **+115** |

三条结论都不太一样：

- **TvZ**：运营节奏基本对齐，第二气矿提前 56 秒、第三兵营提前 26 秒，但**出门晚 60 秒**
- **TvP**：二矿比职业早 38 秒，换来的是**第二气矿晚 110 秒、重工晚 33 秒、星港晚 26 秒**——扩张换科技，代价是中期兵力成型慢
- **TvT**：星港和三兵营都比职业早 50 秒左右，但**主力出门晚 115 秒**——这是三个对位里最扎眼的一条

**重要更正**：更早一版报告的"慢节奏 58% / 快节奏 42%"是**版本假象**（8 农民 vs 12 农民）。
按农民数过滤后，TvZ 一气矿 p10–p90 只有 1:00–1:16、二矿 2:19–2:31，快慢维度消失；
但在 TvP / TvT 里扩张时间跨度仍有 130 秒以上，**那两个对位的"慢"是真差异，不是版本**。

数据契约见 [docs/BO数据契约.md](docs/BO数据契约.md)。

## 建造顺序 DAG（dag）：到点执行，还是看到了才动

```powershell
python -m replay_analyzer dag --open
```

把每局的段按 `阶段:段签名` 聚成节点，转移聚成边，然后用**视野几何**判断每条转移前面有没有"看到了什么"：
段开始前 20 秒内出现信息事件（侦察抵达 / 部队接触 / 看到科技建筑 / 看到成规模部队）就标成侦察触发，
否则标成到点触发。这样能直接回答"我的流程里有多少是主动执行、多少是被动反应"。

**三个对位分别出图**（244 局里的 176 局我的录像）：

| 对位 | 局数 | 节点/边 | 转移 | 侦察触发 20s / 40s / 60s | 主要信息源 |
|---|---|---|---|---|---|
| TvT | 60 | 95 / 16 | 166 | 10.8% / 19.3% / **24.1%** | **contact 10**、tech_seen 3 |
| TvZ | 60 | 90 / 22 | 170 | 11.8% / 18.2% / **23.5%** | tech_seen 8、army_seen 5 |
| TvP | 56 | 81 / 18 | 155 | **14.2%** / 15.5% / 23.2% | tech_seen 8、scout_arrive 7 |

**三个对位的信息来源不一样**，这正是不能混着做的原因：

- **TvZ 主要靠"看到科技"**（看到毒爆巢/三基地）
- **TvP 主要靠"看到科技 + 侦察兵摸到基地"**
- **TvT 反过来，主要靠"双方部队互相撞见"**（contact 10 次）——镜像战里没有明确的开局分叉，
  信息来自正面接触，而不是谁摸到了谁家

60 秒窗口都在 23~24%，说明你的反应滞后量级在三个对位里一致：
**看到之后一分钟内会调整**，不是立刻。

### 职业 T 的同对位对照

| 对位 | 职业样本 | 侦察触发 20s / 60s |
|---|---|---|
| TvT | 20（Maru 10 / ByuN 6 / HeroMarine 4） | 13.0% / 35.2% |
| TvZ | 28（Maru 17 / Cure 6 / Clem 5） | 3.7% / 23.2% |
| TvP | 20（Maru 14 / Clem 4 / HeroMarine 2） | 15.3% / 25.4% |

逐对位看差异都不显著（Wilson 区间重叠），**能说的只有"你的反应速度在职业量级上"**，
不能说"你比他们更爱被动反应"。

### 两个模块互相印证

bo 看的是"建造时间点"，dag 看的是"什么时候看见对方"，两者独立，可以交叉验证：

| 对位 | bo：主力出门 | dag：首次看到对方成建制部队 | 是否一致 |
|---|---|---|---|
| TvT | **+115 秒**（我晚） | **+156 秒** | 一致：不只是出门晚，是**更晚才和对方主力照面** |
| TvZ | +60 秒 | +39 秒 | 一致 |
| TvP | −10 秒（我反而早） | 基本持平 | 一致：**"出门晚"是 TvT 特有的问题，不是全局** |

这条交叉验证把 TvT 那个反常现象钉死了：**三兵营和星港都比职业早 50 秒，却不压出去**，
结果主力照面比职业晚两分半。这是三个对位里最明确的一个改进点。

注意：dag 的视野几何有 ±20~45 秒误差，两个模块冲突时以 bo 为准（bo 直接看单位位置）。

### 职业 Z / P 的开局 DAG

语料里有 Zerg 25 局（Serral / Reynor / Dark / Rogue / Solar）和 Protoss 25 局
（Stats / Zest / Classic / Trap / ShoWTimE），各自出一张开局转移图：

| 组 | 开局种类 | 最主流 | 熵（0=只有一套） |
|---|---|---|---|
| 我（59 局 T） | 9 种 | cc-gas-rax 46% | 0.65 |
| Maru（17 局 T） | 6 种 | 35% | **0.83** |
| 职业 Zerg（25 局） | 3 种 | gas2-hatch-pool 52% | 0.68 |
| 职业 Protoss（25 局） | 3 种 | core-gas2-gate-nexus 92% | 0.30 |

**一个反直觉的结果**：「职业选手开局更花」在这批样本里不成立。Z 和你差不多，P 比你还集中；
真正比你分散的是 **Maru**（同为 T）。他们更多是把同一套开局的细节做扎实，不是靠变招取胜。

注意 Z/P 那两张图只看 opening/early：opening→early 的切换约在 3:03，
而位置采样多数局 3:30 之后才有，那个窗口里没有可用的信息事件，所以**不做侦察触发归因**。

精度上限必须知道：位置采样多数局从 4:00 才开始、同单位中位 45 秒一点，
所以信息事件的时间精度中位 **±22 秒**（90 分位 ±42 秒），20 秒窗口和采样精度同量级，
条件只能当"大致同时"读，**不是因果**。地形遮挡未计算，雷达/隐形/潜地未实现。

## 教学视频抽流程（tutorial）：把 B 站视频变成 BO 步骤表

录像能回答「实际发生了什么」，但回答不了「作者想让你怎么做」。所以补了一条支线：
从 B 站合集【星际一招鲜】（UP 主 MakChao，100 条编号招式）里抽结构化流程。

```powershell
# 1) 盘点合集（103 条：对位、时长、招式拆解）
python tools\make_tutorial_index.py

# 2) 从一条视频里挑出「运营 / 思路」总结卡（按红黄文字检测）
python tools\summary_cards.py "视频.mp4" --out ".tmp\summary\BVxxxx" --top 2

# 3) 把总结卡上的建造串解析成结构化步骤
python -m replay_analyzer.flowtext

# 4) 生成流程库页面
python tools\make_tutorial_library.py
```

**关键发现：每期视频结尾都有一张「运营：」总结卡**，作者用一行 token 写完整建造顺序，
比如 #30：

```
运营：BS兵营气矿气矿VF死神星轨BS VF双倍 火车2死神BS火车VS2死神火车双科技
思路：1、此开局比死吗流（28 期）TIMING 快 30S；4 分钟强换，后续导弹车渡鸦标准运营
      2、可以有效克制对手 3 死神 2 火车开
      3、也可以作为死吗流的 2 种变化，欺骗性开局
```

**一张卡 = 一整套 BO**，比逐帧读画面高效一个数量级。已抽 20 条 / 259 个步骤，
其中 15 条是完整流程（12~20 步）。报告：[reports/tutorial-library.html](reports/tutorial-library.html)。

踩过的坑（详见 [docs/教学视频抽取笔记.md](docs/教学视频抽取笔记.md)）：

- **ASR 完全不可用**：背景音乐大、语速快，「第 N 步」大量丢失，转写是「何必E」「普子战」这种串
- **标注位置每期都不一样**，不能按固定字幕带裁；要按**红/黄文字**检测（白色字幕会骗过亮度检测）
- **UP 主会插动漫片段**，片头片尾的动漫字幕经常被误判成总结卡
- 2022 年的老视频没有总结卡（片尾是动漫 ED），只能读画面上的「第 N 步」标注

## 野兵营检测（find_proxy）：从录像里找大招局

野兵营的特征是「兵营造得离自己基地很远」，用单位出生坐标就能判：

```powershell
python tools\find_proxy.py "C:\Users\sun\Documents\…\Replays\Multiplayer" --matchup TvT --json hits.json
```

本机实测：950 局里扫出 **30 局野兵营**，其中 **14 局是本机玩家自己野的**。
判据是兵营与最近基地距离 ≥ 25 格（正常开局 8~12 格，野兵营 60~130 格）。

它的用处是：**不用重打素材，库里已有的对局就能做「视频流程 vs 实战」对照**。
14 局自己野兵营的中位数——野兵营落地 **60 秒**（55~89 秒，跨度 34 秒）、
第一个死神 175 秒、重工 220 秒、星港 311 秒，对照见
[reports/野兵营-视频vs实战.md](reports/野兵营-视频vs实战.md)。

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
  econ.py       经济模型：账本对账、收入拟合、理论/有效采速与采矿效率
  bo.py         建造顺序：按时间线分段、开局签名识别、跨局聚类
  bo_report.py  BO 报告（时间轴 SVG）
  dag.py        段转移图：信息事件推断 + 到点/侦察触发标注
  dag_report.py DAG 报告（纯手绘 SVG）
  flowtext.py   把「运营：」总结卡的一行建造串解析成结构化步骤
  library.py    录像库扫描与归类（只读）
  compare.py    对照组：我和职业选手的指标对比 + 窗口相似度配对
  charts.py     纯 Python 生成内联 SVG 折线图
  report.py     对局分析 HTML 报告
  micro_report.py  操作细节报告 + 批量趋势报告
  compare_report.py  对照组报告 + 录像库索引
  cli.py        命令行入口（analyze / batch / micro / library / compare / card / fog /
                econ / bo / dag / flow / dump）
docs/
  多线练习手册.md   配合 micro / compare 的练习流程（drill、打分卡、一周排期）
  经济模型设计.md   经济/BO 模型的设计与推导
  BO数据契约.md     BO / DAG 的数据契约（改结构先改它）
  教学视频抽取笔记.md 从 B 站视频抽 BO 的方法、踩过的坑
tools/
  fetch_maru.py     从 sc2rep.ru 抓指定选手的 replay（默认 Maru）
  fetch_pro_replays.py  参数化抓取多名职业 Terran + 三道校验
  make_playlist.py  生成职业录像片单
  make_index.py     生成 reports/index.html 汇总页
  make_flow_table.py / make_pro_flow_table.py  flow 核对表
  find_proxy.py     从录像里找野兵营对局
  summary_cards.py  挑出视频里的「运营/思路」总结卡（红黄文字检测）
  static_cards.py   挑出视频里的静止画面卡
  video_frames.py   抽宫格图 / 时钟条
  make_tutorial_index.py / make_tutorial_library.py  教学视频索引与流程库
  rebuild_from_summary.py / rebuild_from_summary2.py  从总结卡重建步骤数据
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
- **经济对账有 ±5% 量级的单局口径噪声**（取消退款、morph、死亡时点、未收录升级），
  所以 econ 报的是分布和误差，不是"精确到个位"的结论
- **单位位置是稀疏快照**（中位 45 秒一点、多数局 4:00 才开始有采样），
  fog / dag 里的"同一时刻"只能读到 ±20~40 秒
- **ESL / GameHeart 老 replay（build 88500）有 overlay 单位和 tag 回收噪音**，
  早期建筑时间做了合理性过滤（扩张 ≥100 秒），96163 的新 replay 更干净
- 地形高度、悬崖、岩石遮挡未建模：fog 会高估视野，dag 的信息事件同样受影响
- 雷达扫描、隐形、潜地未实现，这些信息一律按"没拿到"处理，不瞎标

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
