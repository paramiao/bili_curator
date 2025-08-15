# 后端实现说明（Backend Implementation）

更新时间：2025-08-15 17:49 (Asia/Shanghai)

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
