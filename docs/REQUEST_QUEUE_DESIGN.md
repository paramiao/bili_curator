# 全局请求队列与分级限流设计

更新时间：2025-08-15 17:48 (Asia/Shanghai)

## 目标
- 统一管理所有对 B 站的外网请求（expected-total、parse-collection、列表抓取、下载等），防止并发叠加引发风控。
- 区分“需 Cookie”与“无 Cookie”两类请求，分别采用不同并发与 UA 策略。
- 提供 Web 管理后台的可视化与可控能力：可查看队列、暂停/恢复、取消、调整优先级。
- 对同一订阅（subscription_id）实现强互斥，确保同一订阅的外网请求严格串行。

## 核心原则
- 全链路一致：端点 → 队列 → 执行器（yt-dlp/HTTP 调用）参数一致（UA/Referer/重试/延时/分段/回退）。
- 可观测可控：队列状态可查询、可订阅（SSE），支持工单级操作。
- 家用稳定优先：默认严格限流（Cookie 通道 = 1），可按需微调。

## 模型与组件

### RequestJob（队列任务）
- 字段：
  - `id`（uuid）
  - `type`：`expected_total | parse | list_fetch | download`
  - `subscription_id`（可选）
  - `url`（可选）
  - `requires_cookie`（bool）
  - `ua_profile`：`desktop | mobile | alt`（仅无 Cookie 通道生效）
  - `status`：`queued | running | paused | done | failed | canceled`
  - `priority`（int，默认 0，可正负）
  - `attempts`/`max_attempts`/`last_error`
  - `created_at/started_at/finished_at`

### RequestQueueManager（队列管理器）
- 通道（lane）：
  - Cookie Lane：需要 Cookie 的任务；默认并发=1，严格限流。
  - NoCookie Lane：不需要 Cookie 的任务；并发可配置（默认 1–2），UA 可轮换。
- 执行约束：
  - 全局 `yt_dlp_semaphore = Semaphore(1)`：所有 yt-dlp 子进程串行，控压风控；并为子进程调用设置独立超时（见“配置与默认值”）。
  - 订阅级互斥 `locks[sid] = asyncio.Lock()`：同订阅任务严格串行。
- 调度策略：按 `priority`、创建时间排序，同订阅任务保持顺序。
- 重试与退避：指数退避（如 2–5s 起步），最大重试 N 次，可按 `type` 配置。

## API 设计（对齐当前实现）
- `GET /api/requests`：查询任务（支持 `lane/status/type/subscription_id` 过滤，分页）。
- `GET /api/queue/stats`：总体统计（并发配置、运行计数、分通道排队数等）。
- `POST /api/requests/enqueue`：入队（传入 RequestJob 必要字段）。
- `POST /api/requests/{id}/pause`、`/resume`、`/cancel`、`/prioritize`。
- `GET /api/requests/events`：SSE 推送任务状态变化（可选）。

## 端点改造为“入队”模式
- `GET /api/subscriptions/{id}/expected-total` → 入队 `type=expected_total`；
  - 内部采用 yt-dlp 的“快速元数据路径”（`--flat-playlist --dump-single-json` / `-J` / `--dump-json --playlist-items 1`）读取计数字段，不做分页枚举；
  - `sync=true` 支持在队列内执行并阻塞返回（等待任务完成或超时）。
- `POST /api/subscriptions/{id}/download` → 入队 `type=list_fetch`，完成后再入队 `type=download`；
  - 下载类任务统一强制 `requires_cookie=true`，以提升成功率与稳定性；
  - 或采用“两阶段”显式按钮：`refresh-list` 与 `download(use_cache_only=true)`。
- `POST /api/subscriptions/parse-collection` → 入队 `type=parse`。

## UA 与 Cookie 策略（统一）
- `requires_cookie = true`：
  - 注入 Cookie（SESSDATA 为主），使用稳定桌面 UA（通过 `get_user_agent(True)` 获取）；
  - 并发=1，严格限流；订阅级互斥生效。
- `requires_cookie = false`：
  - 不注入 Cookie，UA 在内置 UA 池轮换（`get_user_agent(False)`）；
  - 并发可配置（默认 1–2），适合轻量公共查询等。

环境变量（建议）：
- `QUEUE_COOKIE_LANE_CONCURRENCY=1`
- `QUEUE_NOCOOKIE_LANE_CONCURRENCY=1`
- `UA_PROFILES=desktop,mobile`
- `YT_DLP_EXTRACTOR_ARGS=...`
- `LIST_MAX_CHUNKS=200`（合集列表抓取的最大分页数上限，默认 200；配合单页 100 → 上限约 20,000 条）
- `LIST_FETCH_CMD_TIMEOUT=120`（列表抓取子进程超时秒数）
- `DOWNLOAD_CMD_TIMEOUT=1800`（下载子进程超时秒数）
- `META_CMD_TIMEOUT=60`（视频元数据子进程超时秒数）
- `EXPECTED_TOTAL_TIMEOUT=30`（expected-total 快速探测超时秒数）

## 前端管理后台
- “请求队列”页/卡片：
  - 列表：`id/type/subscription/需Cookie/status/timestamps/error`
  - 操作：暂停/恢复/取消/置顶、调整优先级
  - 过滤：按订阅/类型/状态/是否需要 Cookie（后续）
  - 实时：2s 轮询
  - 概览卡片：新增分通道“排队”数展示（`queued_cookie/queued_nocookie`）
- 联动：订阅处于下载/抓取中时，禁用同订阅其他对外触发按钮（提示：已在队列中/进行中）。

## 渐进式落地
1. 实现内存版 `RequestQueueManager` 与执行器封装（接管 yt-dlp 调用）。
2. 改造端点为“入队”模式；补充查询/控制 API。
3. 前端新增“请求队列”视图与操作，订阅页按钮联动禁用。
4. 文档更新与回归测试（999 合集、多订阅、并发点按等）。
5. 需要时将队列持久化（SQLite）以支持历史审计与重启恢复。

## 风险与缓解
- 队列阻塞：提供优先级与置顶能力；必要时可临时提升无 Cookie 通道并发。
- Cookie 失效：在 Cookie Lane 记录失败并结合阈值禁用；412 风控仅告警不记失败。
- 观测不足：建议开启 SSE 并前端可视化，便于快速定位瓶颈与失败点。

## 当前实现对比与完成度

本仓库已落地“全局请求队列与分级限流”的核心闭环，整体符合本文档目标与原则：

- 【已实现】全局队列与分级限流
  - Cookie Lane / NoCookie Lane 独立并发容量，支持暂停/恢复（全局与按通道）、取消、置顶优先；统计接口返回容量、当前运行计数，以及分通道排队数（`counts_by_channel.queued_cookie/queued_nocookie`）。
  - 端点：
    - 只读：`GET /api/requests`, `GET /api/requests/{id}`
    - 管理：`GET /api/queue/stats`, `POST /api/queue/pause|resume`, `POST /api/requests/{id}/cancel|prioritize`, `POST /api/queue/capacity`
  - 并发容量支持运行时配置，默认 Cookie=1、NoCookie=2。

- 【已实现】严格串行与订阅互斥
  - 全局 `yt-dlp` 信号量串行，降低外部请求并发；同 `subscription_id` 加互斥锁，确保同订阅严格串行。

- 【已实现】端点入队与全链路一致
  - expected-total / 列表拉取 / 解析 / 下载等端点改为入队执行，参数策略（UA/Referer/重试/分段延时/本地缓存回退）与下载链路保持一致；下载类任务统一强制 `requires_cookie=true`。

- 【部分实现】调度与优先级
  - 已支持“置顶”和记录 `priority`，当前调度更接近 FIFO+置顶；后续可升级为“priority+创建时间”的稳定排序策略。

- 【部分实现】API 完备度
  - `GET /api/queue/stats` 提供容量、运行计数、分通道排队数等；
  - 通用入队 `POST /api/requests/enqueue` 未单独暴露，采用“业务端点内入队”；
  - `GET /api/requests` 当前为全量返回，尚无过滤/分页参数（计划补齐：`status/type/subscription_id/lane/page/size`）。

- 【未实现/可选优化】实时与持久化
  - SSE/WebSocket 实时推送未实现，前端管理页采用 2s 轮询。
  - 队列持久化未实现（当前为内存队列）；如需重启恢复/审计可引入 SQLite。

- 【待抽象】UA 策略集中化
  - 目前 UA 助手在执行模块内提供（如 `downloader.get_user_agent()`）；后续可在队列执行层集中配置并通过环境变量注入（如 `UA_PROFILES`）。

## 前端管理入口

- 静态页面：`/static/queue_admin.html`
  - 功能：暂停/恢复（全局/按通道）、并发容量配置（Cookie/NoCookie）、按 `job_id` 取消/置顶、查看统计与列表（2s 轮询）。
  - 依赖 API：`GET /api/queue/stats`, `POST /api/queue/pause|resume`, `POST /api/queue/capacity`, `GET /api/requests`, `POST /api/requests/{id}/cancel|prioritize`。

## 回归与验收建议

- 分级限流/暂停恢复：调整容量与暂停状态，验证 Cookie/NoCookie 通道并发上限与 queued→running 行为。
- 订阅互斥：同一 `subscription_id` 下并发触发多操作，确认严格串行。
- 队列控制：取消与置顶在 queued/running 两种状态下的行为与资源释放正确。
  - 链路一致性：expected-total（快速路径、无枚举）/列表/下载在 UA/Referer/重试/延时/超时策略上保持一致；校验超大合集（999）健壮性。
- 前端闭环：管理页操作与 API 响应一致，stats 与实际并发计数相符。

## 后续迭代计划（建议）

- 列表 API 过滤/分页：`GET /api/requests?status=&type=&subscription_id=&lane=&page=&size=`，支撑订阅页联动与大规模可观测。
- 优先级调度升级：采用“priority（高优先）+ 创建时间”的稳定排序；置顶通过设置较高 priority 实现，减少直接队列结构操作。
- SSE/WebSocket：`GET /api/requests/events` 推送任务状态变化，降低轮询负载并增强实时性。
- UA 策略抽象：对无 Cookie 通道统一注入 UA 轮换策略，并通过 `UA_PROFILES` 配置。
- 队列持久化：落地 SQLite，支持重启恢复与历史审计；内存实现在轻负载场景继续可用。

## 实现细节与代码映射（当前版本）

> 代码位置以 `bili_curator_v6/` 为根。

- 【核心类】`app/queue_manager.py`
  - `RequestQueueManager`
    - 任务入队：`enqueue(type, requires_cookie, subscription_id, ...) -> job_id`
    - 调度与运行：`mark_running(job_id)` 执行容量与暂停检查，申请通道信号量，设置 `RUNNING`
    - 完成/失败：`mark_done(job_id)`、`mark_failed(job_id, err)` 释放资源与计数
    - 控制：`pause(scope)`、`resume(scope)`、`cancel(job_id, reason)`、`prioritize(job_id, priority)`
    - 观测：`list()`、`get(job_id)`、`stats()`、`set_capacity(requires_cookie, no_cookie)`
  - 并发原语
    - 全局：`yt_dlp_semaphore = asyncio.Semaphore(1)`（yt-dlp 串行）
    - 分通道（高容量 + 软容量门控）：`_sem_cookie/_sem_nocookie = asyncio.Semaphore(1000)`；实际并发由 `_cap_*` 与 `_run_*` 控制
    - 暂停标志：`_paused_all/_paused_cookie/_paused_nocookie`
    - 订阅互斥（在各业务执行路径中使用）：`get_subscription_lock(subscription_id)`

- 【后端 API】`app/api.py`
  - 只读：`GET /api/requests`、`GET /api/requests/{id}`
  - 管理：
    - `GET /api/queue/stats`
    - `POST /api/queue/pause?scope=all|requires_cookie|no_cookie`
    - `POST /api/queue/resume?scope=all|requires_cookie|no_cookie`
    - `POST /api/queue/capacity?requires_cookie=1&no_cookie=2`
    - `POST /api/requests/{id}/cancel`、`POST /api/requests/{id}/prioritize`
  - 静态资源挂载：`/static`、`/web`

- 【前端管理页】`static/queue_admin.html`
  - 操作：暂停/恢复、并发容量、取消/置顶
  - 数据：`/api/queue/stats`、`/api/requests`（2s 轮询）

- 【入队调用点（示例，不完全列表）】
  - `app/api.py`：若干业务端点内部 `await request_queue.enqueue(...)`
  - `app/downloader.py`：列表拉取/下载等触发前统一入队（下载类 `requires_cookie=true`）

## 状态机与流转

- `QUEUED` → `RUNNING`：满足条件（未暂停、未超容量、获通道信号量）后进入运行，记录 `started_at` 与 `acquired_scope`
- `RUNNING` → `DONE|FAILED|CANCELED`：结束时释放通道信号量，递减运行计数并记录 `finished_at`/`last_error`
- 取消逻辑：
  - `QUEUED` 任务直接标记 `CANCELED`
  - `RUNNING` 任务标记 `CANCELED` 并释放资源（软取消，具体中断由执行方感知与轮询对接）

## 并发控制实现要点

- __软容量门控__：以高容量信号量防止死锁，实际并发通过 `_cap_*` 与 `_run_*` 计数限流；容量可运行时更新
- __分通道限流__：`requires_cookie=true` 走 Cookie Lane（默认 1），`false` 走 NoCookie Lane（默认 2）
- __全局 yt-dlp 串行__：避免外网重度并发导致风控
- __订阅级互斥__：同一 `subscription_id` 保证严格串行

## 端点改造（入队模式）说明

- 业务端点在处理外网请求前，统一调用 `request_queue.enqueue(...)` 入队并调度执行
- 按设计可扩展 `sync=true` 阻塞式执行（当前建议异步 + 轮询/回查）

## 配置与默认值

- 并发容量：默认 Cookie=1、NoCookie=2；可通过 `POST /api/queue/capacity` 动态调整
- 建议环境变量（待实现持久化后生效）：
  - `QUEUE_COOKIE_LANE_CONCURRENCY=1`
  - `QUEUE_NOCOOKIE_LANE_CONCURRENCY=2`
  - `UA_PROFILES=desktop,mobile`

## 使用示例（cURL）

```bash
# 1) 查看统计
curl -s http://localhost:8000/api/queue/stats | jq

# 2) 暂停需要Cookie通道
curl -s -X POST 'http://localhost:8000/api/queue/pause?scope=requires_cookie'

# 3) 恢复全部
curl -s -X POST 'http://localhost:8000/api/queue/resume?scope=all'

# 4) 设置并发容量（Cookie=1，NoCookie=2）
curl -s -X POST 'http://localhost:8000/api/queue/capacity?requires_cookie=1&no_cookie=2'

# 5) 取消任务
curl -s -X POST http://localhost:8000/api/requests/<job_id>/cancel

# 6) 置顶任务
curl -s -X POST http://localhost:8000/api/requests/<job_id>/prioritize
```

## 已知差异与后续计划对齐

- `GET /api/requests/stats` 以 `GET /api/queue/stats` 提供等价能力
- 暂未提供通用 `POST /api/requests/enqueue`，当前采用“业务端点内部入队”；如需统一入口可增补
- 列表过滤/分页、SSE/WebSocket 推送、队列持久化、无 Cookie UA 轮换抽象将按“后续迭代计划”推进

## 评审修复与对齐（本轮）

- 【Bugfix】`RequestJob.created_at`：由静态 `datetime.now()` 改为 `field(default_factory=datetime.now)`，避免进程启动时默认值被“固化”，确保队列排序与统计的准确性。
- 【Bugfix】`mark_failed()`：失败路径现在与 `mark_done()/cancel()` 一致，都会释放信号量并递减 `_run_cookie/_run_nocookie`，避免运行计数“卡死”导致通道容量无法恢复。
- 【增强】`expected-total` 端点的订阅级互斥：
  - 采用“短临界区”持锁生成临时 Cookie 文件，缩短锁持有时间；
  - 将外部 `yt-dlp` 调用封装在 `run_and_parse()`，并在内部叠加 `sub_lock + yt_dlp_semaphore`，确保同一订阅全链路串行，进一步降低风控与竞态风险；
  - 状态流转保持单一：成功路径仅 `mark_done()`，异常路径仅 `mark_failed()`，无相互覆盖风险。
- 【可观测性说明】`/api/queue/stats` 中展示的信号量值读取自实现细节（`Semaphore._value`），该字段仅用于观测，不依赖其语义作决策；核心并发由 `_cap_*`、`_run_*` 与信号量配合保证。

以上变更已落地于：

- `bili_curator_v6/app/queue_manager.py`
  - `RequestJob.created_at` 使用 `default_factory`
  - `mark_failed()` 释放信号量并递减运行计数
- `bili_curator_v6/app/api.py`
  - `get_subscription_expected_total()`：短临界区写 Cookie 文件；`run_and_parse()` 内使用 `sub_lock + yt_dlp_semaphore`

建议按“回归与验收建议”章节执行快速回归，重点覆盖：分级限流、暂停/恢复、取消/置顶、订阅互斥、失败回收（运行计数恢复）。
