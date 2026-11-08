# BO 提取 / DAG 的数据契约（所有模块共用）

这份文档是硬约定。改结构要先改这里，再改两边。

## 零、时间单位（所有人必须一致）

**`t = loop / 16` 就是游戏内时钟秒，不需要任何换算。**

证据：`replay.gamemetadata.json` 里的 `Duration` 与 `loop/16` 完全一致
（Ruby Rock：Duration=2737 / loops/16=2737.44；Rorschach：1091 / 1090.75），
而 `Duration` 正是 SC2 录像列表显示"时长"的字段。所以：

- 报告里的 `14:21` = 玩家在游戏里看到的 14:21
- 建造时长的参照值：CommandCenter/Nexus/Hatchery **100 秒**、Barracks 65、Factory 60、
  SupplyDepot/Refinery 30、Reactor 50、TechLab/MissileTurret 25（都是游戏内秒）

⚠️ 曾经踩过的坑：不要以为 `loop/16` 是"真实秒"再乘 1.4（那是把 Faster 倍速和 loop 频率搞混）。
仓库里凡是出现"×1.4 / 真实秒"的解释都是错的，已清除。

## 零之二、版本分组（本机语料实测，2026-11-08）

开局农民数是最可靠的版本判据，比日期可靠（实测 950 局）：

| 月份 | 开局农民 | build |
|---|---|---|
| 2026-01 ~ 2026-06 | 12 | 96163 / 96242 / 96314 / 96516 / 96828 / 96999 |
| **2026-07 ~ 2026-10** | **8**（10 月混有 12） | 97377 / 97426 / 97579 / 98370 |
| 2026-10 ~ 2026-11 | 12 | 98370 |

**做跨局统计时必须按 `start_workers` 过滤**，否则开局时间点会因版本整体偏移，
聚出来的"打法差异"其实是版本差异。默认取 12 农民版本。

检测方法：`loop == 0` 的 `SCV` / `Probe` / `Drone` 记录数。

## 术语

- **步骤（step）**：一条建造/训练/升级事件，带发生时刻（秒）。
- **段（segment）**：时间线上连续的一段步骤，属于同一"阶段"。
- **BO 签名（signature）**：能概括"这套打法是什么"的短标签，例如 `2-1-1`、`1-1-1`、`3-rax`。
- **DAG**：节点 = 段签名（同一阶段内），边 = 段之间的转移；边上可以挂"触发条件"。

## 一、BO 提取输出（`bo.py` → `GameBO`）

```json
{
  "game": {
    "path": "绝对路径",
    "file": "文件名",
    "map": "地图名",
    "date": "YYYY-MM-DD 或空串",
    "matchup": "TvZ",
    "build": 96163,
    "duration": 1090.75
  },
  "player": {"pid": 1, "name": "maru", "race": "T", "is_local": false},
  "signature": "2-1-1",
  "style": "bio",              // bio / mech / air / 混合 / unknown
  "segments": [Segment],
  "key_timings": {             // 关键时间点（秒），没有就是 null
    "first_expand": 140.0,
    "second_expand": 420.0,
    "first_gas": 95.0,
    "second_gas": 210.0,
    "third_rax": 330.0,
    "first_starport": 300.0,
    "first_factory": 260.0,
    "army_moveout": 402.0      // 第一次"主力离开己方半场"
  }
}
```

### Segment

```json
{
  "index": 1,
  "phase": "opening",          // opening / early / mid / late
  "t0": 0.0,
  "t1": 178.4,
  "signature": "rax-gas-cc",   // 这一段干了什么事，短标签
  "trigger": {"kind": "time", "detail": ""},   // 见第四节
  "steps": [
    {"t": 12.5, "name": "SupplyDepot", "label": "补给站",
     "kind": "structure", "cost": [100, 0]}
  ],
  "key_actions": ["补给站@0:25", "兵营@0:58"],   // 给人看的摘要，最多 8 条
  "note": ""
}
```

`kind` 取值：`structure` / `unit` / `morph` / `upgrade`。

⚠️ **实测修正**（2026-11-08，Lead 用真实 replay 验证）：

- 实际 `parsed.build_events` 里的 `kind` 只有上面四种，**没有 `worker`**：
  农民是 `kind="unit"` + `stage="eco"`（`SCV` / `Probe` / `Drone`）。
- `morph` 是变形事件（`SupplyDepotLowered`、`OrbitalCommand`、`Lair`、`WarpGate`、
  `SiegeTankSieged`、虫族各种形态切换）。**不要当新建造重复计费**。
- `build_events` 里**没有 `cost` 字段**，要自己查 `units.cost(name)`；查不到写 `null`。
- 初始基地（`CommandCenter`/`Nexus`/`Hatchery`）在 `loop=0` 也会出现，算 key_timings 时注意排除。
- 每局两条玩家的事件都在同一个 `parsed.build_events` 列表里，用 `player_id` 过滤。
`cost` 是 `[minerals, vespene]`，查不到就写 `null`。

## 二、阶段划分（默认，秒）

| phase | 区间 |
|---|---|
| opening | 0 – 180 |
| early | 180 – 360 |
| mid | 360 – 600 |
| late | 600 – ∞ |

允许按"第一次扩张/第一次大规模交战"动态切，但**默认按时间**——
用户明确说过 BO 就是按时间线分段的。

### 聚类（2026-11-08 改版）

聚类轴从"整局 key_timings"改成 **opening 段（0–180s）的 token 序列**：

- 距离用**前缀感知**的归一化编辑距离（时间窗会把流程截在不同位置，
  一个序列是另一个的前缀时只按长度差轻罚）
- 平均连接层次聚类，先剔离群（最近邻距离 > 0.55）
- k 不写死：试 `k_min..k_max`（默认 2..6），按平均轮廓系数选；
  产生单例的方案淘汰；轮廓系数差 ≤0.03 取小 k
- 输出里 `clusters` **不再固定两类**，每类带 `typical_sequence` / `sequence_medians` / `pace`；
  `meta.clustering` 存选 k 的分数表，`meta.version_filter` 存版本过滤明细，
  `meta.pace_dimension` 存"快慢维度是否还存在"的检验

⚠️ 下游（dag / 报告）**不要假设 clusters 一定是 2 类**。

## 三、DAG 输出（`dag.py`）

```json
{
  "meta": {"matchup": "TvZ", "games": 42, "source": "mine|pro", "generated": "2026-11-08"},
  "nodes": [
    {"id": "opening:rax-gas-cc", "phase": "opening", "signature": "rax-gas-cc",
     "games": 30, "share": 0.71, "examples": ["file1.SC2Replay"]}
  ],
  "edges": [
    {"from": "opening:rax-gas-cc", "to": "early:2-1-1",
     "count": 27, "share": 0.9, "median_lag": 12.0,
     "condition": "scout:enemy_expand", "examples": ["file2.SC2Replay"]}
  ],
  "info_events": {"per_game": {"file1.SC2Replay": [InfoEvent]}}
}
```

`condition` 取值：`"time"` / `"scout:<标签>"` / `"none"`。

## 四、信息事件（`InfoEvent`）——"他看到了什么"

节点的触发条件要能区分"到点就变"和"看到了才变"。信息事件用 fog 的视野几何来算：

```json
{"t": 221.0, "type": "scout_arrive", "detail": "SCV 进入敌方主基地视野"}
```

`type` 取值：

| type | 含义 | 判定 |
|---|---|---|
| `scout_arrive` | 侦察单位首次进入敌方主基地视野 | 用 `fog.Model` |
| `contact` | 双方部队首次互相可见 | 用 `fog.Model` |
| `army_seen` | 我方单位首次看到对方大规模部队（≥5 个单位价值 ≥500） | 用 `fog.Model` |
| `tech_seen` | 首次看到对方科技建筑 | 用 `fog.Model` + 建筑名 |
| `scan` | 雷达扫描（T 专用） | 事件流里 `SUserOptionsEvent`/命令点落在无视野区 |

**定义要保守**：宁可少标，不要瞎标。标不了的事件就不标，`condition` 写 `"none"`。

## 五、代码归属（避免互相覆盖）

| 文件 | 负责人 |
|---|---|
| `replay_analyzer/econ.py` | econ |
| `replay_analyzer/bo.py` | bo |
| `replay_analyzer/dag.py` | dag |
| `tools/fetch_pro_replays.py`、`replays/pro/**` | corpus |
| `replay_analyzer/cli.py`、`README.md`、`reports/index.html` | lead |

其他人不要动别人的文件。需要改别人的接口，先在任务里说，让 lead 协调。

## 六、数据位置

- 我的录像：`C:\Users\sun\Documents\StarCraft II\Accounts\556922532\5-S2-1-8354391\Replays\Multiplayer`（约 999 个）
- 职业录像：`replays/pro/maru/`（18 个，2022 + 2026 两个版本）
- 解析入口：`replay_analyzer.loader.load_replay(path, decode_events=...)`
- 造价表：`replay_analyzer/units.py` 的 `UNIT_COST` / `cost()` / `category()` / `unit_label()`
