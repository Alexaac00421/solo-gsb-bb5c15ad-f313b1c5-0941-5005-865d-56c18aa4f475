# 🌍 跨时区软件发布窗口编排

在多时区环境下编排软件发布：给定**服务依赖、候选发布时间窗、团队禁发时段、维护冻结、回滚耗时与共享资源**，
系统用 Google OR-Tools CP-SAT 求出满足全部约束的可执行排程；无解时返回**阻塞它的约束组合**而不是部分结果。
排程调整时还可提交**已确认服务的锁定开始时刻**（必须准时部署）与**未确认服务的参考开始时刻**
（按"偏离服务数 → 偏移总时长 → 尽早发布"三层词典序优化），二者均不豁免任何既有约束。
发布评审时可要求返回 **1~5 个备选方案**：首项沿用同一优化目标，后续每项满足全部约束，
且与此前每项方案至少有一个未锁定服务的部署开始时刻相差达到最小差距。
发布评审还可查询**单项服务延迟容忍度**：在基线排程上对每个未锁定服务逐分钟推迟，
给出从 0 分钟起连续仍满足全部约束的最大延迟、上限内首个失效分钟及触发的约束组合。

## 约束语义

| 约束 | 规则 |
|---|---|
| 候选时间窗 | 每个服务的**部署 + 回滚整个占用区间**必须完整落在其某个候选窗内；支持多窗 |
| 依赖先后 | `下游开始 ≥ 上游部署结束 + gap_minutes` |
| 团队禁发 | 仅**部署区间**不得与所属团队禁发时段重叠（回滚允许进入禁发期） |
| 维护冻结 | **占用区间（部署+回滚）**不得与任何冻结重叠 |
| 共享资源 | 同名资源在任意时刻的**占用数（含回滚窗口）**不得超过其容量。容量由请求顶层的 `resource_capacities` 声明（正整数）；**未声明的资源容量为 1**，即同一时间只能被一个服务占用。容量为 2 时两个服务可并行，第三个必须错峰。还可用 `resource_capacity_periods` 为资源声明**临时容量时段**（开始、结束、时区、非负整数容量，左闭右开）：时段内任意时刻占用数不得超过当时容量（**0 表示该时段不可占用**），时段外沿用 `resource_capacities` 基准容量；降容时刻落在既有占用期间同样生效 |
| 夏令时 | 不带偏移量的本地墙上时间若在 DST 切换时**重复**（回拨）或**不存在**（前拨），请求被拒绝（422），并给出 gap 两侧 / 重复两次的绝对时间供修正；带偏移量的绝对时间（如 `...+01:00`、`...Z`）无歧义，直接通过 |
| 无解 | 求解器返回一个足以导致矛盾的约束子集（sufficient assumption core），即“阻塞组合”，放松其中任意一项后可重新求解；**不返回部分排程** |

目标函数：所有服务部署开始时刻之和最小（排程尽量早、结果确定可复现）。

## 快速开始（Docker Compose）

```bash
docker compose up --build
```

- 操作页面：http://localhost:8000/
- 健康检查：http://localhost:8000/api/health
- 交互式 API 文档（Swagger）：http://localhost:8000/docs

停止：`docker compose down`。

## 仅用 Docker

```bash
docker build -t release-orchestrator:1.0 .
docker run --rm -p 8000:8000 release-orchestrator:1.0
```

## 本地直接运行（Python 3.11+）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

> 时区数据：镜像内同时安装了系统 `tzdata` 与 Python `tzdata` 包，保证 `zoneinfo` 可解析全部 IANA 时区
> （`Asia/Shanghai`、`Europe/Berlin`、`America/New_York` 等）。

## HTTP API

### `POST /api/schedule`

请求体：

```json
{
  "services": [
    {
      "id": "payments",
      "name": "支付服务",
      "timezone": "Europe/Berlin",
      "duration_minutes": 45,
      "rollback_minutes": 60,
      "resources": ["payment-db", "k8s-prod-east"],
      "windows": [
        {"start": "2026-11-13T22:00", "end": "2026-11-14T05:00", "timezone": "Europe/Berlin"}
      ]
    }
  ],
  "dependencies": [
    {"upstream": "payments", "downstream": "api-gateway", "gap_minutes": 30}
  ],
  "service_teams": [
    {"service": "payments", "team": "payment-team"}
  ],
  "team_blackouts": [
    {"team": "payment-team", "start": "2026-11-13T23:30", "end": "2026-11-14T00:30", "timezone": "Europe/Berlin"}
  ],
  "freezes": [
    {"label": "周末封网", "start": "2026-11-14T02:00", "end": "2026-11-14T03:00", "timezone": "UTC"}
  ],
  "resource_capacities": {
    "payment-db": 1,
    "k8s-prod-east": 2
  },
  "resource_capacity_periods": [
    {"resource": "k8s-prod-east", "start": "2026-11-14T01:00", "end": "2026-11-14T02:00",
     "timezone": "UTC", "capacity": 0}
  ],
  "pinned_starts": [
    {"service": "payments", "start": "2026-11-13T22:30", "timezone": "Europe/Berlin"}
  ],
  "reference_starts": [
    {"service": "api-gateway", "start": "2026-11-14T07:00", "timezone": "Asia/Shanghai"},
    {"service": "ledger", "start": "2026-11-13T21:00:00-05:00"}
  ],
  "alternative_count": 3,
  "min_gap_minutes": 60,
  "max_solve_seconds": 10
}
```

`resource_capacities` 为可选字段：

- 键是资源名，值是该资源的**并行容量（正整数）**。服务 `resources` 中每列出一个资源，
  就在自己的部署 + 回滚占用区间内占用该资源 **1 个名额**；任意时刻同资源占用数不得超过容量。
  容量为 2 时两项发布可并行，第三项必须错峰。
- **未在 `resource_capacities` 中出现的资源按容量 1 处理**；省略整个字段或传 `{}` 时，
  行为与旧版本完全一致（所有共享资源串行）。
- 容量必须为正整数（`0`、负数按 422 拒绝，错误码 `bad_resource_capacity`）；
  同一服务在 `resources` 中重复列出同一资源也按 422 拒绝（`duplicate_resource`）。
- 容量不满足时（如三个服务只能挤在容量 2 的同一时段）仍返回 **200、`feasible=false`**，
  `blockers` 中给出含资源容量约束的阻塞组合，不返回部分排程。

`resource_capacity_periods` 为可选字段（临时限流/扩容），每项是一个容量时段：

- `resource` 为资源名，`start` / `end` / `timezone` 与候选窗写法相同（墙上时间 + 时区，
  或带偏移量的绝对时间），`capacity` 为该时段内的并行容量（**非负整数**）。
- 时段按**左闭右开**处理：占用区间恰在时段起点结束、或恰在时段终点开始的，不受该时段限制。
- 时段内任意时刻，同资源所有服务的部署 + 回滚占用并发数不得超过**当时容量**；
  `capacity: 0` 表示该时段内资源完全不可占用（常用于发布前临时限流）。
  降容时刻落在既有占用期间也须遵守——例如容量在 01:00 降为 1，则 01:00 之后
  仍在占用中的服务也计入这 1 个名额。
- 时段外沿用 `resource_capacities` 的基准容量；未声明基准容量的资源仍为 1。
  时段容量高于基准容量时表示临时扩容，同样生效。
- 同一资源的容量时段**不得重叠**（首尾相接允许），违反按 422 拒绝
  （`capacity_period_overlap`）；容量为负数按 422 拒绝（`bad_capacity_period`）；
  结束不晚于开始、时区未知、本地时间 DST 歧义等与候选窗相同的输入错误格式拒绝。
- 省略该字段或传 `[]` 时，行为与旧版本完全一致。
- 临时容量导致无解时同样返回 **200、`feasible=false`**，`blockers` 中给出对应的
  资源阻塞组合（基准容量阻塞码为 `resource:<资源名>`，临时时段阻塞码为
  `resource:<资源名>:<时段开始时间戳>`），不返回部分排程。

### 排程调整：已确认锁定时刻与参考开始时刻（本轮新增）

排程调整评审时，调用方可在同一份请求中通过两个**可选**字段提交两类开始时刻：

- `pinned_starts`：**已确认服务**的锁定部署开始时刻。这些服务**必须在指定时刻准时部署**，
  等式约束带 `lock:<服务ID>` 假设字面量；锁定**不豁免**候选窗、依赖、团队禁发、维护冻结与
  资源分时容量中的任何一项——部署与回滚占用仍须满足全部既有约束。
- `reference_starts`：**未确认服务**的参考部署开始时刻。参考时刻**只是优化目标、不是约束**，
  同样不能豁免任何约束；参考点落在窗外 / 冻结期 / 资源被占满的时刻时，服务照常排不进该点，
  只在目标函数中计一次"偏离"。

两项的每个元素都是 `{"service": <服务ID>, "start": <ISO 8601>, "timezone"?: <IANA 时区>}`，
时间写法**完全沿用现有规则**：不带偏移量的墙上时间按 `timezone`（默认 `UTC`）解释并参与
DST 重复 / 不存在检查；带偏移量的绝对时间（`...Z`、`...+08:00`）无歧义直接通过。

**分层目标（仅当出现 `reference_starts` 时启用）**，按词典序逐层求解：

1. **最小化偏离参考时刻的服务数**——先让尽可能多的服务精确落在参考点上
   （`|实际开始 − 参考| == 0` 才算命中，早于/晚于一个整数秒都计为偏离）；
2. 在偏离服务数最少的前提下，**最小化偏移总时长**（各参考服务绝对偏差之和，单位秒）；
3. 在前两层都最优的前提下，**沿用原"尽早发布"目标**——所有服务部署开始时刻之和最小。

未给参考时刻的服务（含已锁定服务）**只参与第 3 层**。由于是词典序目标，三层都必须在
`max_solve_seconds` 的**共享时限**内分别证明最优；任一层未能证明最优即返回
**200、`status: "unknown"`、`schedule: null`**（不输出排程），可增大时限后重试。
省略 `reference_starts`（或传 `[]`）时退回原有单层"尽早发布"求解，未证明最优的
FEASIBLE 解仍照常返回，旧行为不变。

**锁定与其他约束冲突**时不返回部分排程，`blockers` 给出的阻塞组合中**必然包含锁定条件**
`lock:<服务ID>`（与它冲突的候选窗 / 冻结 / 禁发 / 资源容量 / 依赖假设同时出现），放松其中
任意一项即可重新求解。例如锁定时刻落在维护冻结期内：

```json
{
  "feasible": false,
  "status": "infeasible",
  "schedule": null,
  "blockers": [
    {"code": "lock:a", "message": "服务 A（a）已确认必须在 2026-12-01T03:00:00+00:00 UTC 准时部署（锁定开始时刻不可移动；…）"},
    {"code": "freeze:维护冻结：封网:a", "message": "…期间服务 A（a）不得部署或处于回滚窗口内"}
  ]
}
```

为使"锁在全部候选窗之外"这类矛盾也能被阻塞核解释，建模时间轴会延伸覆盖每个锁定时刻
（及其部署+回滚占用结束）；因此锁定时刻无论多远都会得到含 `lock:` 的完整阻塞组合，
而不是报错或部分排程。

**输入错误按现有 422 格式一次性拒绝**（与其他字段相同的 `errors[].code/field/message` 结构）：

| 情形 | 错误码 | field 示例 |
|---|---|---|
| 锁定/参考引用了 `services` 中不存在的服务 | `unknown_service` | `pinned_starts[0]` / `reference_starts[2]` |
| 同一服务在 `pinned_starts` 中重复出现 | `duplicate_pinned_starts` | `pinned_starts[1]` |
| 同一服务在 `reference_starts` 中重复出现 | `duplicate_reference_starts` | `reference_starts[1]` |
| 同一服务同时列入两类（两类互斥） | `pinned_and_reference` | `pinned_starts/reference_starts` |
| 时间串无法解析 | `bad_time` | `reference_starts[0].start` |
| 墙上时间 DST 重复 / 不存在 | `dst_ambiguous` / `dst_nonexistent` | `pinned_starts[0].start`（含修正建议） |
| 未知 IANA 时区 | `bad_timezone` | `reference_starts[0].timezone` |

调用示例（curl）：

```bash
curl -X POST http://localhost:8000/api/schedule \
  -H 'Content-Type: application/json' \
  -d '{
    "services": [
      {"id":"a","name":"服务 A","timezone":"UTC",
       "duration_minutes":60,"rollback_minutes":0,"resources":["db"],
       "windows":[{"start":"2026-12-01T00:00:00Z","end":"2026-12-01T08:00:00Z","timezone":"UTC"}]},
      {"id":"b","name":"服务 B","timezone":"UTC",
       "duration_minutes":60,"rollback_minutes":0,"resources":["db"],
       "windows":[{"start":"2026-12-01T00:00:00Z","end":"2026-12-01T08:00:00Z","timezone":"UTC"}]}
    ],
    "pinned_starts": [
      {"service":"a","start":"2026-12-01T04:00:00Z"}
    ],
    "reference_starts": [
      {"service":"b","start":"2026-12-01T04:00:00Z"}
    ]
  }'
```

上例中 `a`、`b` 共享容量 1 的 `db` 无法同刻部署：`a` 锁定 04:00 不动，`b` 的参考 04:00
无法命中，分层目标先保证命中数（`a` 锁定必然命中，`b` 偏离计数为 1），再最小化 `b` 的
偏移，最后取尽早和——`b` 排在 03:00（紧邻 `a` 之前）。把 `a` 的锁定改到冻结段内，
则返回含 `lock:a` 的阻塞组合。

`/api/schedule/diff` 的两侧请求体同样可携带这两个字段并各自独立求解：例如 current 无锁定、
modified 新增 `pinned_starts`，差异会体现在被移动的服务上；modified 一侧锁定冲突
（infeasible/unknown）或输入错误（422）时，仍只在 `failures.modified` 给出含 `lock:` 条件的
阻塞组合 / 完整错误列表，`diff` 为 `null`。

### 发布评审：备选方案（本轮新增）

发布评审需要从**同一请求**查看不同的可执行时序时，在 `/api/schedule` 请求体中加入两个**可选**字段：

- `alternative_count`：备选方案总数（**含首项**，整数 **1~5**）。省略（或传 `null`）时不启用
  备选方案，响应与旧版本完全一致（`alternatives` 为 `null`）。
- `min_gap_minutes`：方案间最小差距（**正整数分钟**）。仅在 `alternative_count` 给出时生效，
  省略按 **1 分钟**处理；只给 `min_gap_minutes` 而不给 `alternative_count` 时不启用备选。

启用后按**同一优化目标**（无参考时刻时的单层"尽早发布"，或有 `reference_starts` 时的
三层词典序目标）逐项求解：

- **首项**与未启用备选时的排程完全一致；
- **后续每项**同样满足全部候选窗、依赖、团队禁发、维护冻结、资源（分时）容量及锁定约束，
  并且相对**每个**此前方案，至少一个**未锁定服务**的部署开始 UTC 时刻相差达到最小差距
  （`|开始时刻差| ≥ min_gap_minutes`；锁定服务的时刻固定，不参与差距判定）；
- 每项都是在"与此前各项拉开差距"的约束下按同一目标重新择优的**完整方案**，
  不是对首项的局部扰动。

响应新增 `alternatives` 对象（`plans[0]` 即首项，与顶层 `schedule` 内容一致）：

```json
{
  "feasible": true,
  "status": "optimal",
  "schedule": [ "…首项排程…" ],
  "blockers": null,
  "alternatives": {
    "requested": 3,
    "min_gap_minutes": 60,
    "status": "complete",
    "plans": [
      {"index": 0, "schedule": [ "…首项…" ]},
      {"index": 1, "schedule": [ "…第二项…" ]},
      {"index": 2, "schedule": [ "…第三项…" ]}
    ]
  }
}
```

`alternatives.status` 三种取值：

| 取值 | 含义 |
|---|---|
| `complete` | 已凑够请求数量（`plans` 长度 == `requested`） |
| `exhausted` | **已证实无更多方案**：继续求解被判定不可行，或全部服务均已锁定；`plans` 为已找到的全部方案 |
| `unknown` | 求解超时，是否还有更多方案**未能判定**；`plans` 中已返回的方案均已验证，可增大 `max_solve_seconds` 后重试 |

规则细节：

- **全部服务锁定**时不存在可移动的未锁定服务，任何方案都无法与首项拉开差距，
  至多返回首项（`status: "exhausted"`）。
- **首项无解**沿用现有行为：返回阻塞组合（`feasible=false`、`blockers`），不返回部分排程，
  `alternatives` 为 `null`；首项求解超时同样沿用 `status: "unknown"` 的现有响应。
- **输入非法**沿用校验错误：`alternative_count` 超出 1~5、`min_gap_minutes` 非正整数等按 422 拒绝。
- 全部方案共享 `max_solve_seconds` 总时限；后续项求解超时**不会误报穷尽**，
  也不会返回未验证的方案（`status: "unknown"`，已返回项均完整有效）。
- `/api/schedule/diff` 的两侧请求可携带备选字段并各自独立求解，但差异预演
  **仍只比较各侧首项**，不为备选方案逐一求差。

调用示例（curl）：

```bash
curl -X POST http://localhost:8000/api/schedule \
  -H 'Content-Type: application/json' \
  -d '{
    "services": [
      {"id":"a","name":"服务 A","timezone":"UTC",
       "duration_minutes":60,"rollback_minutes":0,"resources":["db"],
       "windows":[{"start":"2026-12-01T00:00:00Z","end":"2026-12-01T08:00:00Z","timezone":"UTC"}]},
      {"id":"b","name":"服务 B","timezone":"UTC",
       "duration_minutes":60,"rollback_minutes":0,"resources":["db"],
       "windows":[{"start":"2026-12-01T00:00:00Z","end":"2026-12-01T08:00:00Z","timezone":"UTC"}]}
    ],
    "alternative_count": 3,
    "min_gap_minutes": 60
  }'
```

上例中 `a`、`b` 共享容量 1 的 `db` 无法同刻部署，返回（200，节选）：

```json
{
  "feasible": true,
  "status": "optimal",
  "alternatives": {
    "requested": 3,
    "min_gap_minutes": 60,
    "status": "complete",
    "plans": [
      {"index": 0, "schedule": ["…a 00:00 → b 01:00…"]},
      {"index": 1, "schedule": ["…b 00:00 → a 01:00…"]},
      {"index": 2, "schedule": ["…b 00:00 → a 02:00…"]}
    ]
  }
}
```

首项 `a` 00:00 / `b` 01:00；第二项交换顺序；第三项 `b` 00:00 / `a` 02:00——
任意两项之间都至少有一个服务相差 ≥ 60 分钟。把窗口缩短到只容得下两种排法时，
请求 5 项会返回 `"status": "exhausted"` 与已找到的 2 项；两个服务都加
`pinned_starts` 锁定时则只回首项。

时间串支持两种写法：

- **墙上时间 + 时区字段**：`"2026-11-13T22:00"` + `"timezone": "Europe/Berlin"`，按该时区解释，参与 DST 检查；
- **绝对时间**：`"2026-11-13T21:00:00Z"` 或 `"2026-11-13T22:00:00+01:00"`，无 DST 歧义。

**可行（200）**：

```json
{
  "feasible": true,
  "status": "optimal",
  "schedule": [
    {
      "service_id": "payments",
      "name": "支付服务",
      "deploy_start_utc": "2026-11-13T21:00:00+00:00",
      "deploy_end_utc": "2026-11-13T21:45:00+00:00",
      "occupied_until_utc": "2026-11-13T22:45:00+00:00",
      "local_time": "2026-11-13T22:00:00+01:00",
      "timezone": "Europe/Berlin",
      "window_used": "2026-11-13T21:00:00+00:00 ~ 2026-11-14T04:00:00+00:00 UTC（候选窗1）",
      "resources": ["payment-db"]
    }
  ],
  "blockers": null
}
```

**不可行（200，`feasible=false`）**：`schedule` 为 `null`，`blockers` 为联合导致无解的约束组合：

```json
{
  "feasible": false,
  "status": "infeasible",
  "schedule": null,
  "blockers": [
    {"code": "freeze:周末封网:a", "message": "周末封网（…）期间服务 A 不得部署或处于回滚窗口内"},
    {"code": "dependency:a->b", "message": "依赖顺序：B 必须在 A 部署完成之后才能开始"},
    {"code": "resource:shared-db", "message": "共享资源 shared-db 同时只能被一个服务占用…"}
  ]
}
```

**输入错误（422）**：DST 重复/不存在、未知时区、依赖环、区间非法、引用不存在的服务、候选窗短于部署+回滚占用、资源容量非正（`bad_resource_capacity`）、同一服务重复列出资源（`duplicate_resource`）、容量时段容量为负（`bad_capacity_period`）、同资源容量时段重叠（`capacity_period_overlap`）等：

```json
{
  "errors": [
    {
      "code": "dst_nonexistent",
      "field": "services[x].windows[0].start",
      "message": "…本地时间在夏令时前拨时不存在（该时刻被跳过）…",
      "suggestions": [
        "2027-03-14T06:30:00+00:00  （gap 前，偏移 UTC-05:00）",
        "2027-03-14T07:30:00+00:00  （gap 后，偏移 UTC-04:00）"
      ]
    }
  ]
}
```

`dst_ambiguous`（重复时间）的建议分别给出回拨前后的两个绝对时刻（第一次/第二次）。
这些值可直接粘贴回输入框重新提交。

#### 共享资源并行容量示例（HTTP）

三个服务都要占用 `canary-pool`，声明容量 2：前两项并行，第三项自动错峰一小时。

```bash
curl -X POST http://localhost:8000/api/schedule \
  -H 'Content-Type: application/json' \
  -d '{
    "services": [
      {"id":"canary-a","name":"灰度 A","timezone":"UTC",
       "duration_minutes":30,"rollback_minutes":30,"resources":["canary-pool"],
       "windows":[{"start":"2026-12-01T00:00:00Z","end":"2026-12-01T03:00:00Z","timezone":"UTC"}]},
      {"id":"canary-b","name":"灰度 B","timezone":"UTC",
       "duration_minutes":30,"rollback_minutes":30,"resources":["canary-pool"],
       "windows":[{"start":"2026-12-01T00:00:00Z","end":"2026-12-01T03:00:00Z","timezone":"UTC"}]},
      {"id":"canary-c","name":"灰度 C","timezone":"UTC",
       "duration_minutes":30,"rollback_minutes":30,"resources":["canary-pool"],
       "windows":[{"start":"2026-12-01T00:00:00Z","end":"2026-12-01T03:00:00Z","timezone":"UTC"}]}
    ],
    "resource_capacities": {"canary-pool": 2}
  }'
```

返回（200，节选）：`canary-a` 与 `canary-b` 同在 `00:00` 开始、占用至 `01:00`，
`canary-c` 错峰到 `01:00` 开始、占用至 `02:00` —— 任意时刻池内占用数均为 2，未超容量。
把容量改为 1（或直接省略该字段）时三者串行到 `00:00 / 01:00 / 02:00` 开始；
窗口只够容纳两个占用时则返回 `feasible=false`，`blockers` 含
`{"code": "resource:canary-pool", ...}`。容量写成 `0` 或负数返回 422：

```json
{
  "errors": [
    {
      "code": "bad_resource_capacity",
      "field": "resource_capacities[canary-pool]",
      "message": "资源 canary-pool 的容量必须为正整数（实际: 0）；未声明的资源按容量 1 处理",
      "suggestions": null
    }
  ]
}
```

#### 共享资源临时容量时段示例（HTTP）

发布前对 `canary-pool` 临时限流：`2026-12-01 00:30 ~ 01:30 UTC` 容量降为 0（不可占用）。
两个服务基准容量 2 本可都在 `00:00` 并行，限流后占用区间（各 1 小时）不得与该时段重叠，
双双顺延到 `01:30` 开始：

```bash
curl -X POST http://localhost:8000/api/schedule \
  -H 'Content-Type: application/json' \
  -d '{
    "services": [
      {"id":"canary-a","name":"灰度 A","timezone":"UTC",
       "duration_minutes":30,"rollback_minutes":30,"resources":["canary-pool"],
       "windows":[{"start":"2026-12-01T00:00:00Z","end":"2026-12-01T03:00:00Z","timezone":"UTC"}]},
      {"id":"canary-b","name":"灰度 B","timezone":"UTC",
       "duration_minutes":30,"rollback_minutes":30,"resources":["canary-pool"],
       "windows":[{"start":"2026-12-01T00:00:00Z","end":"2026-12-01T03:00:00Z","timezone":"UTC"}]}
    ],
    "resource_capacities": {"canary-pool": 2},
    "resource_capacity_periods": [
      {"resource":"canary-pool","start":"2026-12-01T00:30:00Z",
       "end":"2026-12-01T01:30:00Z","timezone":"UTC","capacity":0}
    ]
  }'
```

返回（200，节选）：`canary-a` 与 `canary-b` 都在 `2026-12-01T01:30:00+00:00` 开始、
占用至 `02:30` —— 限流时段 `[00:30, 01:30)` 内无任何占用，时段外仍按基准容量 2 并行。

时段按左闭右开处理：若限流改为 `01:00 ~ 02:00` 容量 0，则 `00:00` 开始、`01:00` 结束占用
的服务不受影响（占用恰在时段起点结束）。降容落在既有占用期间也须遵守：基准容量 2、
`01:00 ~ 02:00` 降为 1 时，一个 90 分钟占用从 `00:00` 跨到 `01:30`，第二个服务必须等到
`01:30` 才能开始（`01:00` 之后池内同时只能有 1 个占用）。

限流时段覆盖全部候选窗时返回 `feasible=false`，`blockers` 含临时时段阻塞：

```json
{
  "feasible": false,
  "status": "infeasible",
  "schedule": null,
  "blockers": [
    {"code": "resource:canary-pool:1796083200",
     "message": "共享资源 canary-pool 在 2026-12-01T00:00:00+00:00 ~ 2026-12-01T02:00:00+00:00 UTC 期间临时限流为 0（不可占用）；涉及服务的部署+回滚窗口不得与该时段重叠: 灰度 A"}
  ]
}
```

同一资源的时段重叠、容量为负数均按 422 拒绝：

```json
{
  "errors": [
    {
      "code": "capacity_period_overlap",
      "field": "resource_capacity_periods[canary-pool]",
      "message": "资源 canary-pool 的容量时段重叠：…；同一资源的容量时段不得重叠（首尾相接允许）",
      "suggestions": null
    }
  ]
}
```

### `POST /api/schedule/tolerance` — 单项服务延迟容忍度（本轮新增）

发布评审需要知道**现有排程能容忍某个服务推迟多久**时，提交一份现有排程请求和一个
正整数延迟上限（分钟）。系统先**按现有规则求基线排程**（与 `/api/schedule` 首项完全一致，
含锁定 / 参考时刻的分层目标），再对每个**未锁定服务**：保持其他服务的基线开始时刻不变，
把该服务的部署开始时刻**逐分钟推迟**（0、1、2 … 直到上限），每一分钟都用与排程完全相同的
规则检查候选时间窗、依赖先后、团队禁发、维护冻结与共享资源分时容量
（延迟后的部署及回滚占用均按原规则判断）。

请求体：

```json
{
  "max_delay_minutes": 120,
  "request": { ...一份完整的 /api/schedule 请求体... }
}
```

- `max_delay_minutes`：延迟上限，**正整数分钟**（非正整数按 422 拒绝）。
- `request`：现有排程请求，字段与校验规则同 `/api/schedule`；`alternative_count`
  不影响基线首项，可省略。

响应中每个服务一项 `tolerances`（按基线开始时刻排序）：

| 字段 | 含义 |
|---|---|
| `locked` | 已确认（`pinned_starts`）服务为 `true`：**不可推迟**，不计算容忍度 |
| `baseline_start_utc` | 该服务在基线排程中的部署开始时刻 |
| `max_delay_minutes` | 从 0 分钟起**连续**仍满足全部约束的最大延迟（不超过上限） |
| `first_failure_minute` | 上限内**首个失效分钟**；上限内逐分钟均可行时为 `null` |
| `first_failure_start_utc` | 首个失效分钟对应的部署开始时刻（无失效分钟为 `null`） |
| `triggered` | 首个失效分钟**触发的约束组合**（与无解时 `blockers` 同源的阻塞核；无失效分钟为 `null`） |

规则细节：

- **连续可行、不跳过失效分钟**：一旦某分钟失效即停止该服务的扫描——即使更晚的时段
  （例如越过一段冻结之后）重新可行，也不越过该失效分钟；`max_delay_minutes` 只统计
  从 0 分钟起的连续可行段。
- **锁定服务**（`pinned_starts`）必须准时部署，标为 `locked: true`，
  `max_delay_minutes` / `first_failure_minute` / `triggered` 均为 `null`。
- **基线无解或未定**（`infeasible` / `unknown`）时返回原有的阻塞组合或未定状态，
  `tolerances` 为 `null`，不输出容忍度；**输入非法**沿用现有 422 校验错误格式。
- 占用终点越出该服务最后候选窗终点时，直接触发候选窗约束（`window:<服务ID>`）；
  其余失效分钟触发的约束由求解器阻塞核给出（如 `resource:db`、`dependency:a->b`、
  `freeze:…`、`blackout:…`、`resource:<资源>:<时段起点>` 等）。
- 容忍度扫描使用一份独立的 `max_solve_seconds` 预算；扫描超时返回
  `status: "unknown"`、`tolerances: null`，可增大时限或减小上限后重试。

调用示例（curl）：两个服务共享容量 1 的 `db`，`a` 排在 `00:00`、`b` 排在 `01:00`：

```bash
curl -X POST http://localhost:8000/api/schedule/tolerance \
  -H 'Content-Type: application/json' \
  -d '{
    "max_delay_minutes": 120,
    "request": {
      "services": [
        {"id":"a","name":"服务 A","timezone":"UTC",
         "duration_minutes":60,"rollback_minutes":0,"resources":["db"],
         "windows":[{"start":"2026-12-01T00:00:00Z","end":"2026-12-01T08:00:00Z","timezone":"UTC"}]},
        {"id":"b","name":"服务 B","timezone":"UTC",
         "duration_minutes":60,"rollback_minutes":0,"resources":["db"],
         "windows":[{"start":"2026-12-01T00:00:00Z","end":"2026-12-01T08:00:00Z","timezone":"UTC"}]}
      ]
    }
  }'
```

返回（200，节选）：`a` 后面紧贴着 `b`，推迟 1 分钟即与 `b` 的占用冲突；
`b` 之后无其他占用，上限 120 分钟内均可推迟：

```json
{
  "feasible": true,
  "status": "optimal",
  "baseline": ["…a 00:00 → b 01:00…"],
  "max_delay_minutes": 120,
  "tolerances": [
    {"service_id": "a", "locked": false, "baseline_start_utc": "2026-12-01T00:00:00+00:00",
     "max_delay_minutes": 0, "first_failure_minute": 1,
     "first_failure_start_utc": "2026-12-01T00:01:00+00:00",
     "triggered": [{"code": "resource:db", "message": "共享资源 db 同时只能被一个服务占用…"}]},
    {"service_id": "b", "locked": false, "baseline_start_utc": "2026-12-01T01:00:00+00:00",
     "max_delay_minutes": 120, "first_failure_minute": null,
     "first_failure_start_utc": null, "triggered": null}
  ]
}
```

若把 `a` 加入 `pinned_starts`，则 `a` 的容忍度项变为 `locked: true`（不可推迟）。
基线无解时（例如冻结覆盖全部候选窗）返回 `feasible: false`、原有 `blockers`，
`tolerances` 为 `null`。

### `POST /api/schedule/diff` — 变更影响预演

变更评审时，提交一份**当前排程请求**和一份**修改后的请求**，系统分别独立求解，
再按**服务 ID** 对齐两侧结果，预演变更会让哪些服务的发布时刻发生移动。

请求体：

```json
{
  "current": { ...一份完整的 /api/schedule 请求体... },
  "modified": { ...一份完整的 /api/schedule 请求体... }
}
```

比较规则：

- `changed`：两侧都存在、且**部署开始**或**占用结束**（部署结束 + 回滚窗口）的 UTC 时刻发生变化的服务，
  给出变化前后的 UTC 时刻与变化分钟数（`delta_minutes` 正为推迟、负为提前；未变化的字段为 `null`）；
- `added` / `removed`：仅在 `modified` 中出现（新增）/ 仅在 `current` 中出现（被移除）的服务；
- 两份请求结果完全一致时，三个列表均为空（空差异）。
- 两侧请求**各自携带自己的 `resource_capacities` 与 `resource_capacity_periods` 并独立求解**：
  例如 current 未声明容量（资源串行）、modified 把同一资源声明为容量 2（两项并行），
  或 modified 新增一段临时限流时段（容量 0），差异会体现在被提前/推迟的服务上；
  某一侧容量或容量时段非法（422）、或容量约束导致无解时，同样只在 `failures` 的
  该侧给出完整错误 / 阻塞组合（含资源约束），`diff` 为 `null`，不给部分排程。
- **任一请求输入错误或无解时，不输出差异结果**：在 `failures` 中按 `current` / `modified`
  分别给出该侧的完整输入错误（HTTP 422）或阻塞约束组合（HTTP 200、`feasible=false`），
  与直接调用 `/api/schedule` 得到的内容一致。

调用示例：

```bash
curl -X POST http://localhost:8000/api/schedule/diff \
  -H 'Content-Type: application/json' \
  -d '{
    "current": {
      "services": [
        {"id": "payments", "name": "支付服务", "timezone": "UTC",
         "duration_minutes": 60, "rollback_minutes": 30,
         "windows": [{"start": "2026-11-13T00:00:00Z", "end": "2026-11-13T06:00:00Z", "timezone": "UTC"}]}
      ],
      "max_solve_seconds": 10
    },
    "modified": {
      "services": [
        {"id": "payments", "name": "支付服务", "timezone": "UTC",
         "duration_minutes": 60, "rollback_minutes": 30,
         "windows": [{"start": "2026-11-13T00:00:00Z", "end": "2026-11-13T06:00:00Z", "timezone": "UTC"}]}
      ],
      "freezes": [
        {"label": "临时冻结", "start": "2026-11-13T00:00:00Z", "end": "2026-11-13T01:00:00Z", "timezone": "UTC"}
      ],
      "max_solve_seconds": 10
    }
  }'
```

**有变化（200）**：

```json
{
  "feasible": true,
  "diff": {
    "changed": [
      {
        "service_id": "payments",
        "name": "支付服务",
        "deploy_start": {
          "before_utc": "2026-11-13T00:00:00+00:00",
          "after_utc": "2026-11-13T01:00:00+00:00",
          "delta_minutes": 60.0
        },
        "occupied_until": {
          "before_utc": "2026-11-13T01:30:00+00:00",
          "after_utc": "2026-11-13T02:30:00+00:00",
          "delta_minutes": 60.0
        }
      }
    ],
    "added": [],
    "removed": []
  },
  "failures": null
}
```

只调整回滚时长、开始时刻不变时，`deploy_start` 为 `null`，仅 `occupied_until` 给出变化。
两份请求无差异时返回 `"changed": [], "added": [], "removed": []`。

**某一侧输入错误（422）**：不输出 `diff`，在 `failures` 中指明出错的一侧及完整错误列表：

```json
{
  "feasible": false,
  "diff": null,
  "failures": {
    "modified": {
      "kind": "validation",
      "status": null,
      "errors": [
        {"code": "bad_timezone", "field": "services[payments].timezone",
         "message": "服务 payments: 未知时区 'Mars/Olympus'", "suggestions": null}
      ],
      "blockers": null
    }
  }
}
```

**某一侧无解（200，`feasible=false`）**：`kind` 为 `infeasible`（求解器超时未判定时为 `unknown`），
`blockers` 为该侧的阻塞约束组合：

```json
{
  "feasible": false,
  "diff": null,
  "failures": {
    "current": {
      "kind": "infeasible",
      "status": "infeasible",
      "errors": null,
      "blockers": [
        {"code": "freeze:维护冻结：全天封网:payments", "message": "…期间服务 支付服务（payments）不得部署或处于回滚窗口内"}
      ]
    }
  }
}
```

两侧同时失败时 `failures` 中会同时包含 `current` 与 `modified` 两个键。

## 项目结构

```
app/
  main.py        FastAPI 入口（/api/schedule、/api/schedule/tolerance、/api/schedule/diff、/api/health、静态页面）
  models.py      请求/响应 Pydantic 模型
  times.py       时区解析与 DST 重复/不存在分类（zoneinfo fold 规则）
  validator.py   输入校验：DST、引用完整性、依赖环检测、区间合法性
  scheduler.py   CP-SAT 建模、求解、不可行假设核（blocking core）提取
  tolerance.py   单项服务延迟容忍度：基线求解与逐分钟推迟扫描
  diff.py        变更影响预演：双请求执行与按服务 ID 的时刻对齐求差
  web/index.html 操作页面（原生 JS，内置 3 个示例场景）
Dockerfile
docker-compose.yml
requirements.txt
```

## 不可行核的工作原理

每条业务约束都挂在一个布尔“强制字面量”上（通过可选区间实现：字面量为假时区间在
`NoOverlap` 中缺席），求解时把全部字面量作为 assumptions 断言为真。
CP-SAT 在判定 INFEASIBLE 时返回 `SufficientAssumptionsForInfeasibility`：
其中任意一个字面量放松（对应约束被修改/移除），其余约束才有可能成立。
因此返回的不是“所有相关约束”，而是一个最小化的、可直接据此谈判的阻塞组合。
