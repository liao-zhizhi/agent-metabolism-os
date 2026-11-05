# 客户演示指南 · CEL 监控大屏

> 目标：**十分钟内**，让客户看到自己的 Agent 因为一次假完成而被拦下、被扣信任、被写进账本。
> 不需要读源码，不需要懂 CEL 理论。

---

## 第 0 步：你需要什么

| 项目 | 要求 |
|---|---|
| 系统 | macOS / Linux / WSL2 |
| 软件 | Docker（演示机自带即可，无需 Python 环境） |
| 端口 | 8000 未被占用（`lsof -i :8000`） |
| 浏览器 | Chrome / Edge / Safari 任一现代浏览器 |

客户自己的 Python 代码需要 `requests`：`pip install requests`。

---

## 第 1 步：一键启动（约 1 分钟）

```bash
cd product_mvp
docker compose up --build
```

看到下面这行就算起来了：

```
cel-console  | INFO:     Application startup complete.
cel-console  | INFO:     Uvicorn running on http://0.0.0.0:7860
```

> 容器里监听 7860（Hugging Face Space 的端口），compose 把它映射到本机 8000，所以浏览器
> 还是打开 **http://localhost:8000**。

浏览器打开 **http://localhost:8000** ，应当看到：

- 顶部徽章是绿色的 `HEALTHY`
- 四张指标卡全是 `0` / `100.0`
- 中间终端里只有一行 `[boot] cel-gateway v0.3.0 · double-entry membrane armed…`
- 右上角 `CHAIN` 显示 `0x0000…`（64 个 0 = 空链的创世值）

### 如果 `docker compose` 报 `unknown command`

这台机器只装了 Docker CLI、没装 compose 插件。用等价的裸命令：

```bash
# 就在 product_mvp 目录里执行（构建上下文就是它）
docker build -t cel-console:0.3.0 .
docker run -d --name cel-console -p 8000:7860 \
  -v cel-ledger:/data \
  -e CEL_LEDGER_LOG=/data/ledger.jsonl \
  cel-console:0.3.0
```

> 构建上下文是 `product_mvp/` 本身：核心库不靠 `COPY ../agent_metabolism` 搬运，而是
> 在构建时按发布 tag 下载（`ARG CEL_CORE_REF`）。这样同一个 Dockerfile 既能本地构建，
> 也能直接交给 Hugging Face Space（它只能从自己仓库根目录构建）。

### 收工

```bash
docker compose down          # 或 docker rm -f cel-console
```

数据卷 `cel-ledger` 里留着链的追加日志；要彻底清空演示态：`docker volume rm cel-ledger`。

---

## 第 2 步：模拟一个真实场景

**这一幕的台词**：机器人手臂报告"我把数据写进数据库了"，而外部检查说"没有"。
两种说法都对不上，网关必须**在信任受损之前**就拦下来。

### 2.1 复制这段代码（`demo_agent.py`）

```python
"""客户 Agent 侧的真实接入代码。就是这一段，没有别的。"""
import json
import requests

CEL = "http://localhost:8000"

# 你的 Agent 已经"执行完"了动作，现在向网关报账。
# check   = Agent 自己的声明（我成功了）
# witness = 不由 Agent 决定的外部检查结果（数据库里到底有没有那行）
payload = {
    "agent_id": "robot_arm_01",
    "intent": "write to database",
    "check": True,
    "witness": False,          # ← 故意制造矛盾：声称成功，见证否认
}

response = requests.post(f"{CEL}/api/v1/verify", json=payload, timeout=10)
verdict = response.json()

print("HTTP", response.status_code)
print(json.dumps(verdict, indent=2, ensure_ascii=False))

# 你的 Agent harness 应当据此分支：被拦下就不要继续往下走。
if verdict["verdict"] == "FAIL":
    raise SystemExit("被网关拦截：不许把这次执行当作成功")
```

运行：

```bash
python demo_agent.py
```

### 2.2 你会看到什么（真实输出，非示意）

```json
{
  "verdict": "FAIL",
  "agent_id": "robot_arm_01",
  "intent": "write to database",
  "check": true,
  "witness": false,
  "trust_score": 75.0,
  "penalty_state": "CRITICAL",
  "inflamed": true,
  "penalty": 50.0,
  "charged_as": "FALSE_COMPLETION",
  "grounded": false,
  "locked": false,
  "seq": 1,
  "chain_hash": "11e088b0058d1890e406de45170d8dc536e8b6bcdeeb01d29ed6b65d5c6d68d6",
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "message": "拦截假完成：声称成功，但见证否认（扣除 25 信任）"
}
```

**给客户指这四个字段就够了**：

| 字段 | 值 | 意义 |
|---|---|---|
| `verdict` | `FAIL` | 判了，而且判的是"不成立" |
| `trust_score` | `75.0` | 100 → 75，一次假完成扣 25 |
| `penalty_state` | `CRITICAL` | 一次就跨过红线（罚 50 ≥ 阈值 40） |
| `chain_hash` | `11e088…` | 已经封进哈希链，事后改不了 |

注意 **HTTP 是 200，不是 4xx**：审计成功不等于接口出错。网关拦住了，这就是它该干的活。

### 2.3 同一句话，换个说法（可选，用来加深印象）

```bash
# 诚实的失败：如实报告"我没做成"——PASS，不扣分
curl -s -X POST http://localhost:8000/api/v1/verify -H 'Content-Type: application/json' \
  -d '{"agent_id":"robot_arm_01","intent":"grasp vial","check":false,"witness":false}'

# 没有见证：谁都没看过——INVALID，直接锁账
curl -s -X POST http://localhost:8000/api/v1/verify -H 'Content-Type: application/json' \
  -d '{"agent_id":"nav_stack_02","intent":"corridor cleared","check":true,"witness":null}'
```

讲法：**"说没做成"不罚，"说做成了但没人看见"要锁。** 这套账本奖励的是准确，不是乐观。

---

## 第 3 步：在网页上看到那道红灯

回到 **http://localhost:8000**，不需要刷新（前端每 2 秒自动轮询）。逐项指给客户看：

| 位置 | 变化 |
|---|---|
| ① 右上角徽章 | 绿色 `HEALTHY` → 红色 `CRITICAL` |
| ② 指标卡「成功拦截假完成数」 | `0` → `1` |
| ③ 指标卡「平均信任分」 | `100.0` → `75.0`，绿色进度条缩到 75% |
| ④ 指标卡「隔离中智能体」 | `0` → `1` |
| ⑤ 实时雷达（终端） | 多出一行红色 `#001 FAIL robot_arm_01 write to database [FALSE_COMPLETION · trust 75.0]`，行尾有 `NEW` |
| ⑥ 测试控制台下方 | 弹出判决卡：红色 `FAIL` 徽章 + "拦截假完成：声称成功，但见证否认（扣除 25 信任）" |
| ⑦ 右下角 | 红色告警 toast，整屏边框闪一次红光 |
| ⑧ 「在册智能体」 | 出现 `robot_arm_01 75.0 CRITICAL`，红色描边 |

### 不用写代码的演示路径（现场演示推荐）

直接点测试控制台里那颗红色按钮 **「⚡ 模拟一次假完成」**（`check=true · witness=false`）。
它会替你做一次真实的 POST —— 前端没造假，终端里那一行是从 `/api/v1/ledger` 拉回来的，**和 API 写进去的是同一条**。

下面四个灰按钮演示同一套规则的另外三种结局：

| 按钮 | check / witness | 预期 |
|---|---|---|
| 诚实失败 | `false / false` | `PASS`，信任不动 |
| 验证通过 | `true / true` | `PASS`，信任 +2 |
| 无见证 | `true / null` | `INVALID`，**立刻锁账** |
| 虚报失败 | `false / true` | `FAIL`，罚 20 扣 10 |

### 连点三次会怎样？（很好用的一击）

同一个 Agent 连续三次 `FAIL` 后：

1. 信任 `100 → 75 → 50 → 25`，罚单累计 150；
2. 账本**自动关闭**，第四次请求返回 **HTTP 423**，`verdict` 变成 `REFUSED`；
3. 「在册智能体」那一行变成 `LOCKED` 并出现「解锁」按钮。

**关键台词**：锁只能由人来解（点「解锁」按钮 → `POST /api/v1/agents/{id}/unlock`）。
`verify` 的代码路径上没有解锁入口——一个能自己写条 `PASS` 就解封的 Agent，手里的权力正好是这把锁要收走的那个。

### 想证明账本"真的改不了"？

```bash
curl -s http://localhost:8000/api/v1/ledger/verify
# {"chain_ok":true,"chain_head":"…","total_entries":7}
```

`chain_ok` 不是存下来的布尔值，是每次请求**重新走一遍整条链**算出来的。
改任何一条历史记录的 `intent`，下一次校验就会变 `false`（`tests/test_engine.py` 里有这一条）。

---

## 第 4 步：演示结束，把数字带走

```bash
curl -s http://localhost:8000/api/v1/stats | python -m json.tool
```

```json
{
  "intercepted_false_completions": 1,
  "blocked_unwitnessed": 0,
  "refused_attempts": 0,
  "verified_passes": 4,
  "avg_trust_score": 88.3,
  "inflamed_agents": 1,
  "locked_agents": 0,
  "agents_total": 3,
  "health_state": "CRITICAL",
  "total_entries": 7,
  "verdicts": { "pass": 4, "fail": 2, "invalid": 0 },
  "chain_head": "694cbf170720a652…",
  "agents": [
    { "agent_id": "nav_stack_02", "trust_score": 100.0, "penalty_state": "HEALTHY", "inflamed": false, "locked": false },
    { "agent_id": "robot_arm_01", "trust_score": 75.0,  "penalty_state": "CRITICAL", "inflamed": true,  "locked": false },
    { "agent_id": "lab_agent_03", "trust_score": 90.0,  "penalty_state": "WARNING",  "inflamed": false, "locked": false }
  ]
}
```

---

## 常见问题（现场救场）

| 现象 | 原因 / 处理 |
|---|---|
| 页面打不开 | `docker compose logs -f cel`；确认 8000 没被别的进程占（`lsof -i :8000`） |
| 点按钮没反应 | 看浏览器控制台 / 网络面板：`/api/v1/verify` 是否 4xx。`"check": "yes"` 会被 **422** 拒绝（字符串不被当成成功） |
| 一直返回 `REFUSED` / 423 | 该 Agent 已被隔离。换个 `agent_id`，或点「解锁」，或 `docker compose down` 后重启清空内存态 |
| `chain ✗ TAMPERED` | 内存里的链被外部改过——这正是它该说的话。重启进程即可复现干净状态 |
| 指标卡不动 | 前端每 2 秒轮询一次；确认标签页在前台且没被休眠 |
| 重建镜像很慢 | 第一次要下载 `python:3.12-slim` 并装 `fastapi/uvicorn`，之后走缓存 |

---

## 演示前 30 秒检查清单

- [ ] `docker compose up --build` 已跑完，无报错
- [ ] http://localhost:8000 打开是绿色 `HEALTHY`、四张卡全 0
- [ ] 终端只有一行 `[boot]`
- [ ] 客户机器的 `python demo_agent.py` 能连到 `localhost:8000`
- [ ] 浏览器缩放 100%，窗口够宽（>1020px 是双栏大屏，窄了会堆成单栏，也能用）

---

## 一句话收尾（给客户）

> 这个屏幕上的每一个数字都是从条目里算出来的，没有一个是计数器存下来的；
> 每一条记录都封在哈希链里，改一个字都会被下一次校验抓到。
> 我们不承诺"AI 不会犯错"——我们保证**它犯的错不会变成你的账单**。
