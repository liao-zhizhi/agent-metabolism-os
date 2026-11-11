# 具身物理审计数据契约

**版本 ID：** `gradia.embodied_physics.v1`

这不是训练数据格式。这是一份**可被物理审计的最小数据契约**：日志里必须留下物体在世界系中的见证，审计器才能判定连续性，而不是从 RGB、语言或动作命令里猜。

权威字段表见同目录 `schema.md`。本页面向采集团队，字段名与 `schema.md` 一致，不增不改。

单位一律 SI。整个 episode 使用**同一个世界坐标系**。

---

## 必填字段

### Episode 级

| 字段 | 类型 | 单位 | 必填 | 说明 |
|:---|:---|:---|:---|:---|
| `schema_id` | string | — | 必填 | 必须是 `gradia.embodied_physics.v1`。 |
| `source` | string | — | 必填 | 非空来源标签（机台名、仿真器、采集批次）。 |
| `dt_sec` | number `> 0` | 秒 | 必填 | 相邻帧的均匀步长。丢帧记成缺口，禁止静默拉长 `dt_sec`。 |
| `frames` | array，长度 `≥ 1` | — | 必填 | 稠密帧序列。空数组不是缺字段，见下方审计顺序。 |
| `episode_id` | string | — | 可选 | 建议填写，v1 规则不读。 |
| `rig_id` | string | — | 可选 | 建议填写，v1 规则不读。 |
| `world_frame` | string | — | 可选 | 建议 `"world"`。v1 规则不读；缺省仍视为单一世界系。 |

### 每一帧（`frames[]` 上全部必填）

| 字段 | 类型 | 单位 | 必填 | 说明 |
|:---|:---|:---|:---|:---|
| `t` | integer `≥ 0` | 帧序号 | 必填 | 必须等于稠密下标：第 0 帧 `t=0`，随后 `t`, `t+1`, …。 |
| `object_xyz` | `[x, y, z]` | 米 | 必填 | 物体中心，世界系。不是 2D 像素框，不是末端。 |
| `object_aabb` | `{min: [x,y,z], max: [x,y,z]}` | 米 | 必填 | 物体轴对齐包围盒，世界系。每个轴 `max[i] > min[i]`。 |
| `hand_xyz` | `[x, y, z]` | 米 | 必填 | 手 / 夹爪 TCP，同一世界系。 |
| `contact` | boolean | — | 必填 | 本帧是否与物体机械接触。 |
| `contact_force_n` | number `≥ 0` | 牛顿 | 必填 | 接触力大小。`contact` 为 `false` 时必须是 `0`。只有布尔开关、没有力传感器时，也要显式写 `0.0`，不要省略键。 |

`contact` 是状态标志。`contact_force_n` 是力见证。v1 缺力键即 Schema 不齐。

---

## 不能顶替

下列字段**不能**代替上表。送这些进来，审计仍会因缺契约字段而 `INVALID`。

| 送来的东西 | 不能顶替 | 原因 |
|:---|:---|:---|
| 2D `boxes` / 像素框（含 Ego4D FHO 稀疏关键帧） | `object_xyz`、`object_aabb`、`hand_xyz` | 像素不是米；稀疏关键帧不是均匀 `dt_sec`。 |
| 末端位姿 / `cartesian_position` / `eef_pose` | `object_xyz` | 末端不是物体中心。 |
| 7-DoF `action` 或其它末端命令 | `object_xyz` | 命令不是送达的物体状态。 |
| RGB、深度、语言 | 上表任一物理字段 | 不能从图像发明 3D 物体见证。 |
| clip 级一个 “contact” 关键帧 | 逐帧 `contact` | 无逐帧 `contact` **不能**推断接触；不能用整段标签填每一帧。 |

---

## 审计顺序

采集接入时，判词按这次序产生。缺字段不会被写成物理失败。

1. `frames == []` → `NOT_RUN`（没有可审计的帧，不是 Schema 错误）。
2. Schema 不齐（缺键、类型/单位不对、`t` 不连续、`schema_id` 不是 v1）→ **立刻 `INVALID`**，列出缺/错字段，**不跑**物理规则。
3. 只有合法 Schema，才跑 R1–R3：
   - **R1 瞬移：** 相邻 `object_xyz` 位移 `> 0.15` m → `FAIL`
   - **R2 速度反向：** 物体速度反向且两端速率都 `> 0.05` m/s → `FAIL`
   - **R3 穿模：** `contact == false` 且手在 AABB 内深度 `> 0.005` m → `FAIL`
4. 任一条规则 `FAIL` → episode `FAIL`。全部 `PASS` → `PASS`。

阈值写在 `audit_episode.py` 顶部，是演示值，不是已标定的机型限。

---

## INVALID 的语义

**`INVALID` 是拒收采集。**

它不是「机器人安全」，不是策略 `FAIL`，不是物理失败。它只说明：这份日志没有提供可计算的物理见证，审计器拒绝评分。

| 判词 | 对采集团队的含义 |
|:---|:---|
| `NOT_RUN` | 没有帧。补数据，不要当通过。 |
| `INVALID` | 契约没填满。改采集管线，不要改策略。 |
| `FAIL` | 契约合法，物理规则未通过。这才是物理不一致。 |
| `PASS` | 契约合法，且 R1–R3 未触发。不是对整机安全的背书。 |

---

## 最小合法示例

完整 3 帧见 [`schema.example.json`](schema.example.json)。这是格式样例，不是 PASS/FAIL 测试用例。前 2 帧：

```json
{
  "schema_id": "gradia.embodied_physics.v1",
  "source": "schema.example",
  "dt_sec": 0.05,
  "frames": [
    {
      "t": 0,
      "object_xyz": [0.00, 0.00, 0.08],
      "object_aabb": {
        "min": [-0.04, -0.04, 0.04],
        "max": [0.04, 0.04, 0.12]
      },
      "hand_xyz": [-0.06, 0.00, 0.08],
      "contact": false,
      "contact_force_n": 0.0
    },
    {
      "t": 1,
      "object_xyz": [0.01, 0.00, 0.08],
      "object_aabb": {
        "min": [-0.03, -0.04, 0.04],
        "max": [0.05, 0.04, 0.12]
      },
      "hand_xyz": [-0.05, 0.00, 0.08],
      "contact": false,
      "contact_force_n": 0.0
    }
  ]
}
```

第三帧同样字段齐全，`t` 为 `2`。接入时用现有 `audit_episode.py` 读该文件：不得因缺字段得到 `INVALID`。物理规则结果以脚本输出为准，本页不把该文件标成 PASS 或 FAIL。示例 PASS 只证明格式可读，不证明真实采集可行。

---

## 接入检查清单

上线前逐项打勾。任一项没有，预期是 `INVALID`，不是 `PASS`。

- [ ] **同一时钟落盘。** `object_xyz`、`object_aabb`、`hand_xyz`、`contact`、`contact_force_n` 与本体感觉同一 `dt_sec`。丢帧记缺口，不用插值假装连续。
- [ ] **世界系坐标。** 物体中心、AABB、TCP 在同一世界系，单位米。不要用相机像素或基座关节角顶替。
- [ ] **AABB 单位与朝向。** `object_aabb.min` / `max` 为米；每轴 `max > min`；盒跟着物体中心走。
- [ ] **逐帧 contact 与力。** 每一帧都有布尔 `contact` 和 `contact_force_n`。`contact=false` 时力为 `0`。没有力传感器也要写 `0.0`。
- [ ] **时间戳单调。** `dt_sec > 0`；`frames[i].t == i`；相邻帧序号连续递增，不跳号、不倒退。

---

## 版本与兼容

- 当前 major 为 **v1**，`schema_id` 固定为 `gradia.embodied_physics.v1`。
- **新增可选字段**（采集端可写、审计端可忽略）**不升 major**。`episode_id`、`rig_id`、`world_frame` 即属此类。
- **改必填集合、改单位、改现有字段含义或改 `schema_id` 字符串** → **升 major**（例如 `gradia.embodied_physics.v2`）。v1 审计器必须把未知 `schema_id` 判为 `INVALID`，不得默读成 v1。
- 删除或重命名 v1 已公布字段，视为不兼容，必须升 major。
- 物理阈值（`0.15` m 等）不是 Schema 的一部分；改阈值不改 `schema_id`。
