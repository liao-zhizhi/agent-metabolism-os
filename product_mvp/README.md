# CEL — 因果执行与信任监控台 (MVP)

一个 FastAPI 服务 + 一块暗黑极客风监控大屏，演示三件事：

1. **假完成会被拦住**（不是靠模型自查，是靠"见证"与"声明"的两边对账）；
2. **信任会被扣掉**（每笔判决进入该 Agent 自己的惩罚账本，红灯不可否认）；
3. **账本改不了**（每条记录进 SHA-256 哈希链，任何一处改写都会让 `chain_ok` 变 `false`）。

底层直接复用本仓库的 `agent_metabolism`（`DoubleEntryLedger` + `PenaltyLedger`，标准库实现，不额外安装）。

![CEL 监控大屏](assets/cel_dashboard.png)

---

## 一、最快跑起来

```bash
cd product_mvp
pip install -r requirements.txt
uvicorn api_server:app --port 8000
# 或：python api_server.py
```

打开 http://localhost:8000

Docker：

```bash
cd product_mvp
docker compose up --build      # 构建上下文是仓库根目录，见下方说明
```

> 镜像需要本仓库根目录当构建上下文（它要 `COPY agent_metabolism/`）。因此
> `docker-compose.yml` 里写的是 `context: ..`，仓库根的 `.dockerignore` 负责把
> 测试、实验产物和论文挡在上下文之外。如果你的机器只有 `docker` 没有 compose 插件：

```bash
cd ..            # 仓库根
docker build -f product_mvp/Dockerfile -t cel-console:0.2.0 .
docker run -p 8000:8000 -v cel-ledger:/data cel-console:0.2.0
```

跑测试：

```bash
cd product_mvp
python3 -m unittest discover -s tests -v            # 引擎层，零第三方依赖
.venv/bin/python -m unittest discover -s tests -v   # 含 HTTP 层（需 fastapi+httpx）
```

---

## 一·五、写接口的鉴权

`POST /api/v1/verify` 和 `POST /api/v1/agents/{id}/unlock` 会写入信任账本，所以要
过一道共享 key 的检查：

```bash
export CEL_API_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
python api_server.py
```

- 请求头 `X-API-Key` 与 `CEL_API_KEY` 不符、或缺失 → **403**；
- `CEL_API_KEY` 未设置 → 写入开放（方便本地 demo），但启动日志会打 `auth: DISABLED`，
  大屏顶部会出现红色 **鉴权未启用** 横幅，`/api/v1/stats` 也会报 `auth_enabled: false`。
  一个没设防的实例绝不会长得像一个设了防的实例。
- `GET /api/v1/stats`、`GET /api/v1/ledger`、`GET /api/v1/ledger/verify` 保持匿名，
  给大屏轮询用。

大屏上的 key 由人工粘贴进控制台的输入框，存在 `sessionStorage`（关掉标签页就没了），
不会写进 HTML —— 所以"查看源代码"看不到任何 key。

公网部署（持久化磁盘、`/data`、平台环境变量、验收清单）见 **[DEPLOYMENT.md](DEPLOYMENT.md)**。

---

## 二、文件结构

```
product_mvp/
├── api_server.py           FastAPI 应用：路由、请求模型、故障码
├── cel_engine.py           网关本体：判决 / 信任账本 / 哈希链（可脱离 HTTP 单测）
├── example_client.py       客户 Agent 的接入示例（requests）
├── requirements.txt        只有 fastapi + uvicorn，两行
├── Dockerfile
├── docker-compose.yml
├── templates/
│   └── index.html          单页大屏（无模板引擎，直接读盘）
├── static/
│   ├── app.js              轮询 stats/ledger + 提交模拟 + 告警
│   ├── style.css           暗黑终端风：扫描线、辉光、红灯闪烁
│   └── favicon.svg
└── tests/
    ├── test_engine.py      23 项：判决、隔离、解锁、链完整性
    └── test_api.py         34 项：页面渲染、三个接口、423 语义、403 鉴权
```

前端**没有构建步骤**：Tailwind 走 CDN，`app.js` 是普通 `<script>`，改完刷新即可。
`api_server.py` 每次请求都从磁盘读 `index.html`，所以改 HTML 不用重启服务。

---

## 三、三个接口

### `POST /api/v1/verify`

```json
{
  "agent_id": "robot_arm_01",
  "intent": "write to database",
  "check": true,
  "witness": false,
  "artefact": "",     // 可选：真实文件路径会被 sha256 摘要后入账
  "cost": 0.0
}
```

判决表（`check` 是 Agent 的声明，`witness` 是外部检查的结果）：

| check | witness | verdict | 含义 | 记账 |
|---|---|---|---|---|
| true | true | `PASS` | 效果落地且被检查到 | 信任 +2 |
| false | false | `PASS` | **诚实的失败**：如实报告没做成 | 不奖不罚 |
| true | false | `FAIL` | **假完成** | 罚 50、信任 −25 |
| false | true | `FAIL` | 虚报失败（做成了却说没做） | 罚 20、信任 −10 |
| 任意 | `null`/缺失 | `INVALID` | 没人看过，无法判定 | 不罚，但**直接锁账** |

`FAIL` 返回 **200**：审计成功不等于接口出错。只有"账本已关"返回 **423**，因为那时什么都没判。

### `GET /api/v1/stats`

```json
{
  "intercepted_false_completions": 2,
  "avg_trust_score": 85.0,
  "inflamed_agents": 2,
  "locked_agents": 1,
  "agents_total": 4,
  "health_state": "CRITICAL",
  "verdicts": {"pass": 4, "fail": 3, "invalid": 0},
  "chain_head": "694cbf17…",
  "agents": [{"agent_id": "robot_arm_01", "trust_score": 75.0, "penalty_state": "CRITICAL", "inflamed": true, "locked": false}]
}
```

所有数字都是从条目里**算出来的**，没有一个是计数器存下来的。

### `GET /api/v1/ledger?limit=50`

返回最近 N 条（上限 500），`chain_ok` 是**每次重新走一遍链**的结果，不是存下来的布尔值。

附带两个便于演示的端点：`GET /api/v1/ledger/verify`、`POST /api/v1/agents/{id}/unlock`。

---

## 四、几处刻意的设计（以及为什么）

**`check` 用 `StrictBool`。** Pydantic 默认把 `"yes"`、`1` 都当 `true`。而"一个真值字符串被默默当成成功"正是这套账本要防的那类 bug，所以线上类型必须拒绝它——`"yes"` 是 422，不是 PASS。

**`INVALID` 一票锁账，`FAIL` 三票锁账。** 不是严重性排序，是证据问题：没人看过就没法和解，一次就够；说假话第一次是罚单，第二次还犯说明罚单没用。阈值 `CEL_FAIL_LIMIT` 可调，最小值 2（1 会让 Agent 第一次犯错就停机，是过度指控）。

**锁定只能人来解。** `unlock` 故意不在 `verify` 的代码路径上：能自己写一条 PASS 解封的 Agent，手里的权力正好就是锁要收走的那个。解锁不擦历史，也不清 `fail_streak`。

**拒绝也入账。** 被隔离后的每次尝试都带 `REFUSED` 写进链——"它还在试"本身是证据。

**账本序号自己维护。** 核心的 receipt 序号只数"判过的条目"，拒绝和解锁会让它停住；直接拿来当链序号会出现重复 `#011`，而前端是按 `seq > lastSeq` 增量取新行的，重复号等于**静默丢行**。所以 `_append` 丢掉 receipt 的序号、用链自己的计数器（`test_seq_is_monotonic_across_refusals_and_unlocks` 守这一点）。

---

## 五、部署到公网（Render / Vercel / Fly）

先看清楚这个 MVP 的两个物理约束，再谈环境变量：

1. **状态在进程内存里。** 账本、信任分、链头都在 `CelGateway` 实例上。多副本 = 每个副本
   一本账；平台把请求轮流打到不同实例 = 同一个 Agent 的信任分跳来跳去。所以：
   **要么单副本，要么先把账本搬进外部存储**（Postgres / Redis），后者不是改环境变量能解决的。
2. **没有鉴权。** 任何人都能 POST `/api/v1/verify` 来给你的 Agent 扣分。公网部署前必须
   加一层（API Key 中间件、平台自带的 password protection、或 Cloudflare Access）。
   只保护 `/api/*` 而把 `/` 留空是不行的——大屏的 JS 会以匿名身份打那些接口。

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8000` | **Render / Railway / Heroku / Fly 注入这个**。镜像已改为读它（`CEL_PORT` 优先）。不读它会绑定 8000、健康检查探别的端口，日志写着 running 而平台判定服务已死 |
| `HOST` | `0.0.0.0` | 同上，平台可能注入；`CEL_HOST` 优先 |
| `CEL_PORT` / `CEL_HOST` | — | 本服务自己的旋钮，本地或 compose 用；优先级高于 `PORT`/`HOST` |
| `CEL_CORE_PATH` | 自动探测 | 核心库路径。不设时按 `../agent-metabolism-os` → `..` 顺序找 `agent_metabolism/double_entry.py`；**镜像里写死 `/app`**，自建镜像时必须自己给 |
| `CEL_FAIL_LIMIT` | `3` | 连续几次 `FAIL` 隔离一个 Agent（核心最小值 2） |
| `CEL_LEDGER_LOG` | 空 | 把链追加到文件。必须落在**可写且持久**的路径：容器里写 `/data/...` 且挂卷；Vercel 只有 `/tmp` 可写，**重启即丢** |

### 按平台

**Render（推荐，Docker 部署）**

- New → Web Service → Docker，Dockerfile 路径填 `product_mvp/Dockerfile`，
  **构建上下文必须是仓库根**（`product_mvp` 的上一级），否则 `COPY agent_metabolism/` 失败。
- 环境变量：`CEL_CORE_PATH=/app`、`CEL_FAIL_LIMIT=3`、
  `CEL_LEDGER_LOG=/data/ledger.jsonl`；**不要**手动设 `PORT`，Render 会注入。
- 加一个 Persistent Disk 挂到 `/data`（否则重启后落盘日志跟着容器一起没了）。
- **Instances 保持 1。** 开两个副本，就是两本互不知情的账。
- Health check path 填 `/healthz`。

**Vercel（不建议，除了做静态演示）**

Serverless 每个请求可能是全新冷启动、文件系统只读（除 `/tmp`），进程内存不跨请求存活。
结果：`/api/v1/stats` 永远是空的，`chain_head` 永远是 64 个 0——这块屏幕的核心卖点会当场消失。
真要在 Vercel 上跑，得把 `CelGateway` 的状态外置（KV / Postgres），那就不是"调环境变量"了。
纯前端展示可以直接把 `templates/index.html` + `static/` 当静态站部署，但接口得指向别处。

**Fly.io / Railway**

读 `PORT`，配一个 volume 挂 `/data`，同样 `CEL_CORE_PATH` + 单副本。

### Docker 部署时的一个坑

镜像不再写死 `--port 8000`，而是 `python api_server.py`，由 `resolve_bind()` 决定端口。
这是为平台注入 `PORT` 准备的；本地 `docker compose up` 不受影响（没设就是 8000）。

---

## 六、诚实的边界

- **罚多少是政策，不是测量。** 权重来自 `penalty.DEFAULT_WEIGHTS`，没有任何一行代码算过"一次假完成对客户到底值多少钱"。
- **状态在内存里。** 重启清空账本。设了 `CEL_LEDGER_LOG` 会把链追加到磁盘，但**重放进信任账本没做**——重启后是"有历史文件、无历史信任"。
- **`witness` 是调用方给的。** 服务信任这个布尔值；`artefact` 给真实路径时会被 sha256 摘要，这才让 PASS 落在字节上而不是句子上。要让见证本身不可伪造，得接 `agent-harness-audit` 里的 `MembraneGateway`（它要 artefact + digest 才发 warrant），那是另一层，本 MVP 没接。
- **前端每 2 秒轮询**，不是 WebSocket。演示够用，几十个并发也够用；上千个就不够了。
