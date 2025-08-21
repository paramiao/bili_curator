# [已归档] bili_curator V6 架构设计（历史版本）

本文件为历史存档，仅供参考。最新、权威的架构与进度请以以下文档为准：
- 实时进度与权威信息：docs/PROJECT_STATUS.md
- 路线图：docs/ROADMAP_V6.md
- 数据模型：docs/DATA_MODEL_DESIGN.md
- 变更记录：CHANGELOG.md

以下为当时的架构说明原文：

---

# bili_curator V6 架构设计（现行版）

> ✅ 与当前代码与部署一致的精简架构（单容器、SQLite、本地轻量队列）

## 🎯 当前版本核心目标

### 1. 一键容器化部署
- 单容器（FastAPI + 后台执行器 + 前端静态资源）
- `scripts/manage.sh` 一键 up/down/rebuild/logs/health
- 健康检查 `/health`

### 2. Web 管理与任务队列
- 简洁 Web 界面（静态页面挂载 `bili_curator_v6/static/`、`web/`）
- 任务排队与状态流转（内置轻量队列，非 Celery）
- 进度与统计统一口径（快速路径 expected-total）

### 3. 智能订阅与去重
- 订阅集合/空间，按周期检查新增
- 去重与已下载识别（基于产物与元数据）

### 4. 稳定性与风控
- 全链路超时与子进程终止：`LIST_FETCH_CMD_TIMEOUT`、`EXPECTED_TOTAL_TIMEOUT` 等
- 分页上限与退避：`LIST_MAX_CHUNKS`、指数退避
- Cookie 可选，最小化配置

## 🏗️ 系统架构设计（现行）

### 技术栈选择
```
Frontend:  单页应用（SPA，构建产物位于 `web/dist/index.html`；`static/` 为历史页面，已废弃）
Backend:   FastAPI + Python 3.11
Database:  SQLite（本地文件 DB_PATH）
Queue:     内置轻量队列（Python 队列/状态机），无 Redis/Celery
Container: Docker + Docker Compose（单服务）
Monitor:   健康检查 / 日志（无 Prometheus）
```

### 启动事件与本地优先一致性修复（V6 新增）

- 触发：服务启动时（FastAPI `startup` 事件）。
- 行为：
  - 启动内部调度器（APScheduler/后台循环）。
  - 后台线程执行“一致性检查 + 统计重算”，统计以“磁盘实际存在产物”为准，刷新 DB 缓存。
  - 不阻塞应用对外提供 API。
- 与远端同步的边界：
  - 启动阶段不访问外网获取远端总数；远端计数/轻量同步由 `/api/sync/trigger`、`/api/sync/status` 负责，按需触发。
- 前端入口：
  - 统一单页应用（SPA）入口 `web/dist/index.html`，历史 `static/*.html` 为兼容保留，已不建议直接访问。

### 服务架构
```
┌───────────────────────────────────────────────────────────────┐
│                         单容器（bili-curator）                 │
│  ┌───────────────┐   ┌────────────────┐   ┌────────────────┐ │
│  │  Web 静态页   │   │  FastAPI API   │   │  内置任务队列   │ │
│  │ (static/web)  │   │ (/api, /health)│   │  (下载/统计)    │ │
│  └───────────────┘   └────────────────┘   └────────────────┘ │
│                │                │                 │            │
│                ▼                ▼                 ▼            │
│         下载目录(/app/downloads)   SQLite(DB_PATH)   日志(/app/logs) │
└───────────────────────────────────────────────────────────────┘
```

## 📊 数据模型（SQLite）

### 核心表（示例）
```sql
-- 订阅
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,            -- collection/uploader
    url TEXT,
    is_active INTEGER DEFAULT 1,
    last_checked_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 任务
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,            -- expected_total/list/download
    status TEXT DEFAULT 'pending', -- pending/running/success/failed
    progress INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    message TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT
);

-- 视频（去重/索引）
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bilibili_id TEXT UNIQUE,
    title TEXT,
    file_path TEXT,
    file_size INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
```

## 🐳 容器与部署

### Dockerfile（简要）
```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### docker-compose.yml（现行）
```yaml
services:
  bili-curator:
    build: .
    container_name: bili_curator_v6
    ports:
      - "8080:8080"
    volumes:
      - /Volumes/nas-mk/xiaoya_emby/xiaoya/bilibili:/app/downloads
      - ~/bilibili_config:/app/data
      - ~/bilibili_config/logs:/app/logs
      - ./static:/app/static:ro
      - ./web:/app/web:ro
      - ./app:/app/app:ro
      - ./main.py:/app/main.py:ro
    environment:
      - PYTHONUNBUFFERED=1
      - DOWNLOAD_PATH=/app/downloads
      - TZ=Asia/Shanghai
      - DB_PATH=/app/data/bilibili_curator.db
      - EXPECTED_TOTAL_TIMEOUT=20
      - LIST_MAX_CHUNKS=5
      - LIST_FETCH_CMD_TIMEOUT=120
      - DOWNLOAD_CMD_TIMEOUT=3600
      - META_CMD_TIMEOUT=60
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

## 🏗️ 模块化后端设计

### 核心模块结构
```
bili_curator_v6/app/
├── api.py                    # FastAPI 主路由与接口
├── models.py                 # SQLAlchemy 数据模型
├── downloader.py             # yt-dlp 封装与下载逻辑
├── task_manager.py           # 任务管理与队列控制
├── queue_manager.py          # 轻量队列实现
├── scheduler.py              # APScheduler 定时任务
├── auto_import.py            # 自动扫描与导入服务
├── video_detection_service.py # 视频检测与元数据提取
├── cookie_manager.py         # Cookie 管理与轮换
└── api/                      # API 子模块
    └── __init__.py
```

### 模块职责说明
- **api.py**：主 API 路由，订阅管理、任务控制、统计接口
- **models.py**：SQLite 数据模型（订阅、任务、视频、Cookie）
- **downloader.py**：yt-dlp 封装，超时控制，产物命名与 NFO 生成
- **task_manager.py**：任务生命周期管理，并发控制，进度追踪
- **queue_manager.py**：内置轻量队列，状态流转
- **scheduler.py**：定时检查订阅更新，清理过期任务
- **auto_import.py**：扫描下载目录，导入元数据，关联订阅
- **video_detection_service.py**：后台视频检测服务
- **cookie_manager.py**：Cookie 池管理，验证与轮换

## 🌐 Web与接口

### 主要页面
1. **概览/健康**：总数统计、进行中任务、失败重试入口
2. **订阅管理**：合集/空间订阅，状态监控
3. **队列管理**：任务列表、进度追踪、手动控制
4. **设置**：Cookie 管理、下载参数配置

### 前端架构（当前实现：统一首页）
```
bili_curator_v6/
└── web/
    ├── dist/
    │   └── index.html        # 唯一入口（SPA）
    └── src/                  # 源码（逐步完善）
```

> 历史分散页面（已废弃，仅保留文件不再直接访问）：
> - `bili_curator_v6/static/admin.html`
> - `bili_curator_v6/static/queue_admin.html`
> - `bili_curator_v6/static/subscription_detail.html`
> - `bili_curator_v6/static/video_detection.html`
> - `bili_curator_v6/static/test.html`

### 家用简化版特性
- **设计理念**：简单 > 复杂，易用 > 功能全面，稳定 > 高性能
- **技术简化**：
  - SQLite 替代 PostgreSQL
  - 内置队列替代 Redis + Celery
  - 单容器替代多服务编排
  - 统一单页应用（SPA）入口，减少多页面维护复杂度
- **部署简化**：一键脚本，最小化配置，本地文件存储

## 🔐 超时与Cookie（简化）

环境变量：
- `EXPECTED_TOTAL_TIMEOUT`：远端总数快速路径超时
- `LIST_MAX_CHUNKS`：分页上限，防止枚举过深
- `LIST_FETCH_CMD_TIMEOUT` / `DOWNLOAD_CMD_TIMEOUT` / `META_CMD_TIMEOUT`

策略：
- yt-dlp 子进程强制超时 + 终止，避免 RUNNING 挂死
- expected-total 采用“快速路径”，不枚举分页
- Cookie 可配置，可选轮换；默认单 Cookie/或匿名

## 🛡️ 防封禁与重试（简化）

要点：
- 频率控制：统一最小请求间隔与抖动
- UA/代理：可选；默认不启用代理池
- 重试：指数退避 + 限次

## 📅 定时任务（现行）
- 由应用内部定时器/后台协程触发周期任务（订阅检查、清理、统计刷新）
- 无 Celery/Redis 依赖

## 🔄 订阅管理系统

### 订阅类型
1. **合集订阅**：监控指定合集的新增视频
2. **UP主订阅**：监控UP主的最新投稿
3. **关键词订阅**：基于关键词搜索新视频

### 订阅处理流程
```python
class SubscriptionProcessor:
    async def process_collection_subscription(self, subscription):
        """处理合集订阅"""
        # 获取合集最新视频列表
        # 与数据库中已有视频比对
        # 创建下载任务
        pass
    
    async def process_uploader_subscription(self, subscription):
        """处理UP主订阅"""
        # 获取UP主最新投稿
        # 过滤已下载视频
        # 创建下载任务
        pass
    
    async def process_keyword_subscription(self, subscription):
        """处理关键词订阅"""
        # 基于关键词搜索
        # 过滤重复和不相关内容
        # 创建下载任务
        pass
```

## 📈 监控与日志

### 关注指标
- 任务成功/失败率与耗时
- 远端总数 vs 本地产物数
- 存储空间与下载速率（按需）

### 日志管理
```python
import logging
from logging.handlers import RotatingFileHandler

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('logs/bili_curator.log', maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)
```

## 🚀 部署与运维

### 一键脚本
使用 `scripts/manage.sh`：
- `./scripts/manage.sh up|down|rebuild|logs|health`
- 支持 `COMPOSE_FILE` 与 `CONFIG_DIR` 环境变量

### 健康检查
- `GET /health` 返回状态、时间戳、版本与关键路径检测

## ✅ 当前已实现功能

### 核心功能
- ✅ **合集订阅**：完整支持，包括解析、下载、调度
- ✅ **手动下载**：单个视频/合集 URL 下载
- ✅ **自动导入与关联**：扫描下载目录，导入元数据，关联订阅
- ✅ **去重检测**：基于文件系统和数据库的智能去重
- ✅ **任务队列**：内置轻量队列，状态流转，并发控制
- ✅ **视频检测服务**：后台检测服务，元数据提取
- ✅ **定时调度**：APScheduler 定时检查订阅更新
- ✅ **超时控制**：全链路 yt-dlp 子进程超时与终止
- ✅ **Cookie 管理**：Cookie 池管理，验证与轮换
- ✅ **NFO 生成**：Emby/Jellyfin 兼容的元数据文件

### 部分实现功能
- 🟡 **UP主订阅**：数据模型完整，核心逻辑待实现
- 🟡 **关键词订阅**：基础匹配实现，主动搜索待完善

## 📋 开发计划

### 高优先级（核心功能补全）
- [ ] **实现 UP主订阅功能**：scheduler + task_manager 中补充获取UP主最新投稿逻辑
- [ ] **完善关键词订阅**：添加主动搜索B站新视频功能
- [ ] **队列观测性增强**：更多状态展示，支持任务取消

### 中优先级（功能增强）
- [ ] **订阅计划配置**：自定义检查周期，筛选条件应用
- [ ] **下载失败处理**：失败案例库，自动重试策略
- [ ] **高级筛选条件**：应用 date_after/before、min_likes/favorites/views

### 低优先级（扩展功能）
- [ ] **特定 URL 列表订阅**：支持自定义视频 URL 列表
- [ ] **代理池支持**：多代理轮换，IP 封禁规避
- [ ] **监控告警**：下载失败通知，存储空间预警

## 🎯 预期效果

当前版本将实现：
- 🐳 一键部署与健康检查
- 🌐 Web管理与轻量队列
- 🤖 订阅监控与增量更新
- 🛡️ 全链路超时与稳定下载
- 📊 统计口径统一与基础可观测

—— 以下为“历史方案（参考，不再作为现行目标）” ——
