# 后端实现说明（Backend Implementation）
## 1.1 启动流程与自动修复（本地优先，V6 新增）

- 触发方式：`FastAPI` 启动事件 `@app.on_event("startup")`。
- 行为概述（非阻塞）：
  - 启动轻量调度器：`scheduler.start()`。
  - 在后台线程执行启动一致性检查：`asyncio.to_thread(startup_consistency_check)`，避免阻塞事件循环。
  - 全量重算订阅统计：`recompute_all_subscriptions()`，统计口径遵循“本地优先”。
- 本地优先统计口径：
  - 以磁盘实际存在的产物（视频/配套 JSON）为权威来源。
  - DB 作为缓存/索引，在启动修复与自动关联后被刷新，杜绝“DB 大于本地”的统计偏差。
- 失败与重试：
  - 启动阶段异常会记录日志，不阻塞进程；可在运行中通过本地扫描/自动关联等操作自愈。
- 与轻量同步的关系：
  - 启动修复只处理“本地一致性与统计”。
  - 远端计数/轻量同步通过 `/api/sync/trigger` 与 `/api/sync/status` 独立完成，避免启动阶段外网阻塞。
- 前端入口与旧页面：
  - 单页应用（SPA）入口统一为 `web/dist/index.html`。
  - 历史分散页面（`static/*.html`）已标记为废弃，推荐从 SPA 入口统一访问与导航。


更新时间：2025-08-17 17:30 (Asia/Shanghai)

## 1. 关键代码路径
- API：`bili_curator_v6/app/api.py`
- 下载器：`bili_curator_v6/app/downloader.py`
- 自动导入：`bili_curator_v6/app/auto_import.py`
- 任务管理器：`bili_curator_v6/app/task_manager.py`
- 模型：`bili_curator_v6/app/models.py`
- 队列：`bili_curator_v6/app/queue_manager.py`

## 2. 订阅解析与命名
- `parse-collection`：优先使用 yt-dlp 合集层级元数据（`--flat-playlist --dump-single-json`）。
- 命名：采用 `uploader + playlist_title`，并清洗为目录安全名；下载与导入/关联使用同一规则。

## 3. 自动导入与关联
- `POST /api/auto-import/scan`：扫描 `/app/downloads`，导入 JSON（兼容 `.json` / `.info.json`）与视频，解析 `entries/list` 结构；写入 `video_path/json_path/thumbnail_path`。
- `POST /api/auto-import/associate`：对所有视频按“是否位于订阅下载目录”匹配关联，刷新统计。
- `POST /api/subscriptions/{id}/associate`：同上，但仅针对单订阅。

## 4. 下载任务与去重
- `EnhancedTaskManager.start_subscription_download()` 创建任务、校验并发。
- `_run_download_task()`：
  - 获取合集视频列表 → 目录内去重（调用 `downloader._scan_existing_files(db, subscription_dir)`） → 逐个下载。
  - 日志与进度更新统一通过 `TaskProgress`。
- `downloader._scan_existing_files()`：
  - DB：读取 `Video.downloaded=True` 的已下载记录；
  - FS：仅扫描“当前订阅目录”（若传入）或全局目录（兜底），兼容 `.json/.info.json` 与 `entries/list` 结构；尝试匹配多后缀产物。

## 5. 统计与远端总数
- 本地统计：`total_videos/downloaded_videos/pending_videos` 来源于 DB + 目录关联；
- 远端总数：`GET /api/subscriptions/{id}/expected-total` 采用 yt-dlp 的快速元数据路径获取计数（不进行分页枚举）：
  - 优先 `--flat-playlist --dump-single-json` 读取 `n_entries`，回退 `entries.length`；
  - 次选 `-J` 读取 `n_entries`/`playlist_count`/`entries.length`；
  - 兜底 `--dump-json --playlist-items 1` 读取首条中的计数字段；
  - 所有调用均设置超时（`EXPECTED_TOTAL_TIMEOUT`，默认 30s），失败再走 Cookie 回退。

## 6. Cookie 与 UA 策略
- 统一通过 `--cookies` 传入 yt-dlp，使用临时 Netscape 格式文件；
- 解析/下载一致；出现 401/403 自动禁用该 Cookie 并记录。
- UA 统一：在 `downloader.py` 内提供 `get_user_agent(requires_cookie)`；
  - `requires_cookie=True` 使用稳定桌面 UA；
  - `requires_cookie=False` 使用内置池随机 UA；
  - 所有下载链路的 yt-dlp 命令均使用 `get_user_agent(True)`。

## 7. 请求队列与并发控制
- 全局队列管理：`RequestQueueManager`（内存实现）。
  - 入队：`enqueue(job_type, subscription_id, requires_cookie, priority)` → `job_id`。
  - 运行：`mark_running(job_id)`；完成/失败：`mark_done/mark_failed(job_id)`；控制：暂停/恢复/取消/置顶。
  - 统计：`stats()` 返回并发容量、运行计数、分通道排队数（`queued_cookie/queued_nocookie`）。
- 并发原语：
  - `yt_dlp_semaphore = asyncio.Semaphore(1)`：全局 yt-dlp 串行；所有 yt-dlp 子进程均配置超时与终止策略，避免卡死。
  - `get_subscription_lock(subscription_id)`：订阅级互斥，确保同订阅严格串行。
- 下载类任务强制 `requires_cookie=True` 并入队登记，以保证成功率与可观测。

## 8. 定时任务
- `POST /api/scheduler/check-subscriptions` 触发检查；后续计划：后台周期任务 + 自动下载开关。

## 9. 一致性原则
- 字段统一：仅使用 `bilibili_id`；输入使用 `is_active`；更新补齐 `updated_at`；
- 目录口径统一：导入/关联/去重都以“订阅下载目录”为边界，避免跨合集。

## 10. API 映射（与队列的集成）
- 只读：`GET /api/requests`、`GET /api/requests/{id}`。
- 队列管理：`GET /api/queue/stats`、`POST /api/queue/pause|resume`、`POST /api/queue/capacity`、`POST /api/requests/{id}/cancel|prioritize`。
- 业务端点（内部入队）：
  - 远端总数：`GET /api/subscriptions/{id}/expected-total` → `type=expected_total`（默认无 Cookie，失败回退 Cookie 并提升优先级）。
  - 合集列表：`list_fetch`（Cookie）。
  - 下载：`download`（强制 Cookie）。

## 🔄 任务队列与调度系统

### 队列架构（当前实现）
- **内置轻量队列**：基于 Python 内存队列，非 Redis/Celery
- **任务管理器**：`task_manager.py` - 任务生命周期管理，并发控制
- **队列管理器**：`queue_manager.py` - 状态流转，优先级调度
- **调度器**：`scheduler.py` - APScheduler 定时任务

### 任务类型与状态
```python
# 任务类型
TASK_TYPES = [
    'expected_total',  # 远端总数获取
    'list',           # 视频列表抓取
    'download'        # 视频下载
]

# 任务状态流转
TASK_STATUS = [
    'pending',    # 等待中
    'running',    # 执行中
    'success',    # 成功
    'failed',     # 失败
    'cancelled'   # 已取消
]
```

### 超时与子进程控制
**环境变量配置**：
- `EXPECTED_TOTAL_TIMEOUT=20`：远端总数快速路径超时
- `LIST_MAX_CHUNKS=5`：分页上限，防止枚举过深
- `LIST_FETCH_CMD_TIMEOUT=120`：列表抓取命令超时
- `DOWNLOAD_CMD_TIMEOUT=3600`：下载命令超时
- `META_CMD_TIMEOUT=60`：元数据提取超时

**实现策略**：
- yt-dlp 子进程强制超时 + 终止，避免 RUNNING 挂死
- expected-total 采用"快速路径"，不枚举分页
- 全局信号量控制 yt-dlp 并发，防止风控

### 并发控制与互斥
```python
# 全局 yt-dlp 信号量（防风控）
yt_dlp_semaphore = asyncio.Semaphore(1)

# 订阅级互斥锁（同订阅任务串行）
subscription_locks = {}

def get_subscription_lock(subscription_id):
    if subscription_id not in subscription_locks:
        subscription_locks[subscription_id] = asyncio.Lock()
    return subscription_locks[subscription_id]
```

## 🍪 Cookie 管理系统

### Cookie 池架构
- **存储方式**：SQLite 数据库存储
- **轮换策略**：轮询使用，失效自动切换
- **验证机制**：定期检查 Cookie 有效性
- **格式支持**：SESSDATA + bili_jct + DedeUserID

### Cookie 管理接口
```python
# Cookie 数据模型
class Cookie(Base):
    __tablename__ = 'cookies'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    sessdata = Column(Text, nullable=False)
    bili_jct = Column(Text)
    dedeuserid = Column(Text)
    is_active = Column(Boolean, default=True)
    last_used = Column(DateTime)
    usage_count = Column(Integer, default=0)
```

### Cookie 使用策略
- **可选配置**：支持无 Cookie 匿名下载
- **自动轮换**：按使用次数和时间轮换
- **失效处理**：遇到 403/429 错误时自动切换
- **Netscape 格式**：自动生成 yt-dlp 兼容的 cookies.txt

### Cookie 获取指南
**浏览器开发者工具方法**：
1. 打开B站并登录
2. 按F12打开开发者工具
3. 切换到"Application"或"存储"标签
4. 在左侧找到"Cookies" → "https://www.bilibili.com"
5. 找到名为"SESSDATA"的Cookie并复制其值

**地址栏快速获取**：
```javascript
javascript:alert(document.cookie.match(/SESSDATA=([^;]+)/)[1])
```

## 11. 配置与环境变量
- 分页与上限：
  - `LIST_MAX_CHUNKS=5`（合集列表抓取的最大分页数上限，默认 5；防止枚举过深）。
- 子进程超时：
  - `LIST_FETCH_CMD_TIMEOUT=120`（列表抓取子进程超时秒数）。
  - `DOWNLOAD_CMD_TIMEOUT=3600`（下载子进程超时秒数）。
  - `META_CMD_TIMEOUT=60`（视频元数据子进程超时秒数）。
  - `EXPECTED_TOTAL_TIMEOUT=20`（expected-total 快速探测超时秒数）。

### 11.1 下载格式选择与失败自适应（V6 增强）

- **MAX_HEIGHT（可选）**：通过环境变量限制视频最高分辨率（默认 `1080`）。例如：`MAX_HEIGHT=720` 将在自适应选择时不选取高于 720p 的视频编码；该限制仅作用于当前失败视频的重选流程，不会“全局降级”。
- **格式回退顺序优化**：基础格式表达式从“指定清晰度 → 通用组合 `bv*+ba/b*` → 兜底 `bestvideo*+bestaudio/best`”，尽量减少“请求的清晰度不可用”导致的失败。
- **按失败原因自适应探测**：当连续尝试仍提示“Requested format is not available”等不可用错误时，触发一次 `yt-dlp -J` 格式探测，解析可用 `formats`，在不超过 `MAX_HEIGHT` 前提下动态挑选“最佳 video+audio（优先 mp4 容器），否则退回单轨 mp4/best”。随后对该视频重试一次。
- **作用范围**：仅对当前失败视频进行自适应选择与重试；不会影响队列中的其他视频与默认清晰度偏好，从而避免“全局分辨率降级”。
- **可观测性**：相关日志会标注触发原因、选中的 `format_id` 与高度限制，用于问题定位。

备注：为兼容家用 NAS 与媒体库，优先选择 mp4 容器；若 mp4 不可用，再回落至其它容器（如 mkv/flv）。

## 12. 订阅管理（前端交互与 API 映射）— 近期变更

本节记录订阅管理页面近期的交互调整与对应后端接口，便于端到端联调与维护。

### 12.1 UI 变更摘要

- 合并操作入口：将“查看详情”和“编辑”合并为单一按钮“详情/编辑”。
  - 点击后打开统一的编辑模态框，顶部显示只读详情（ID、类型、创建时间、最后检查），下方为可编辑字段。
- 弹窗关闭逻辑加固：支持“取消”按钮、点击遮罩、按 ESC 任意方式关闭；统一 `closeEditModal()` 清理监听。
- 按钮顺序优化：在订阅卡片的 `subscription-controls` 区域，将“启用/暂停”按钮置于第一个位置。
- 精简工具栏：移除“刷新列表”按钮。列表刷新由各操作成功后自动调用 `loadSubscriptions()` 统一完成。

对应实现参考：`bili_curator_v6/web/dist/index.html`

### 12.2 前端按钮 → 后端 API 映射

- 订阅卡片内：
  - 启用/暂停：`toggleSubscription(id, is_active)` → `PUT /api/subscriptions/{id}`，payload `{ is_active: boolean }`
  - 查看待下载：`viewPending(id)` → 读取待下载状态（内部查询，若有单独 API 请在此补充）
  - 详情/编辑：`editSubscription(id)` → `GET /api/subscriptions/{id}` 拉取详情；保存时 `PUT /api/subscriptions/{id}` 提交修改
  - 删除：`deleteSubscription(id)` → `DELETE /api/subscriptions/{id}`
  - 本地同步（按订阅）：`localSyncSubscription(id)` → 触发“仅该订阅目录”的扫描与自动关联（前端顺序调用两步或后端聚合端点，见下）
  - 远端同步（按订阅，轻量）：`triggerLiteSyncSubscription(id)` → 触发快速远端计数或轻量同步端点（根据实现对齐）

- 顶部工具栏：
  - 添加订阅：`addSubscription()` → `POST /api/subscriptions`
  - 本地同步（全局）：`localSync()` → 触发全局扫描与自动关联（前端顺序调用或后端聚合端点，见下）
  - 检查订阅：`triggerCheckSubscriptions()` → `POST /api/scheduler/check-subscriptions`（差值入队并持续补齐）
  - 远端同步（全局，轻量）：`triggerLiteSyncGlobal()` → 触发全局轻量远端同步（根据实现对齐）

### 12.3 本地同步与自动关联（全局 / 按订阅）

当前支持两种触发路径，选择其一按需对齐后端：

- 方案 A（后端聚合端点，推荐）：
  - 全局：`POST /api/auto-import/scan-associate`（扫描 + 自动关联 + 统计重算）
  - 按订阅：`POST /api/subscriptions/{id}/scan-associate`

- 方案 B（前端顺序调用两端点）：
  - 扫描：`POST /api/auto-import/scan`（可选传 `subscription_id` 仅扫描该目录）
  - 自动关联：
    - 全局：`POST /api/auto-import/associate`
    - 按订阅：`POST /api/subscriptions/{id}/associate`

两方案均需保证并发互斥（全局 vs. 按订阅）与用户提示（禁用按钮、运行中状态、完成/错误提示）。

#### 12.3.1 远端同步（轻量）端点与前端映射

- 端点：
  - 触发：`POST /api/sync/trigger`（全局或携带 `sid` 针对某订阅）
  - 状态：`GET /api/sync/status`（可选 `?sid=xxx` 仅拉取单订阅状态）
- 前端绑定（`bili_curator_v6/web/dist/index.html`）：
  - 全局触发：`triggerLiteSyncGlobal()` → `POST /api/sync/trigger`（body `{}`）
  - 单订阅触发：`triggerLiteSyncSubscription(id, el)` → `POST /api/sync/trigger`（body `{ sid: id }`）
  - 状态拉取：`refreshSubscriptionsSyncStatus(onlySid?)` → `GET /api/sync/status[?sid=onlySid]`
- 响应结构（建议）：
  - 触发：`{ "message": "triggered" }`
  - 状态：`{ "items": [{ "subscription_id": 1, "status": "running|idle|failed", "last_synced_at": "ISO8601", "message": "..." }] }`

#### 12.3.2 并发与互斥建议

- 远端同步与“检查订阅”可以并行，但需共享全局 yt-dlp 信号量限制外部请求频次。
- 建议在前端禁用触发按钮直至返回，避免重复触发：
  - 全局：禁用 `#btn-sync-global`
  - 单订阅：禁用当前按钮 `el.disabled = true`，结束后恢复
- 若后端存在订阅级互斥锁 `get_subscription_lock(subscription_id)`，应复用，避免同订阅状态抖动。

### 12.4 列表刷新策略

- 移除独立的“刷新列表”按钮后，以下操作成功后均会自动 `loadSubscriptions()`：
  - 添加、编辑、删除、启用/暂停、检查订阅、（本地/远端）同步触发。
  - 编辑弹窗保存成功后自动关闭并刷新。

### 12.5 错误处理与用户提示

- 订阅更新/删除/启停/检查/同步均在错误时 `alert()` 提示具体原因；成功时给出反馈并刷新列表。
- 编辑弹窗：保存失败不关闭，保留表单便于用户修正。

### 12.6 相关代码位置索引

- 前端：`bili_curator_v6/web/dist/index.html`
  - `loadSubscriptions()`：拉取并渲染订阅列表
  - `editSubscription()` / `updateSubscription()`：编辑弹窗打开/保存
  - `toggleSubscription()` / `deleteSubscription()`：启停与删除
  - `triggerCheckSubscriptions()`：触发后台检查
  - `localSync()` / `localSyncSubscription()`：本地扫描 + 自动关联触发
  - `triggerLiteSyncGlobal()` / `triggerLiteSyncSubscription()`：轻量远端同步

后续若变更 API 路径或参数，请同步更新本节与 `docs/API_SPECIFICATION.md`。
