# 数据模型设计（Data Model Design）

更新时间：2025-08-17 16:22 (Asia/Shanghai)

## 数据库表结构

### 1. 订阅表（subscriptions）

```sql
CREATE TABLE subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                  -- 订阅名称
    type TEXT NOT NULL,                  -- 订阅类型：collection/uploader/keyword/specific_urls
    url TEXT,                            -- 合集URL（type=collection）
    uploader_id TEXT,                    -- UP主ID（type=uploader）
    keyword TEXT,                        -- 关键词（type=keyword）
    specific_urls TEXT,                  -- JSON字符串，特定URL列表（type=specific_urls）
    -- 筛选条件
    date_after DATE,
    date_before DATE,
    min_likes INTEGER,
    min_favorites INTEGER,
    min_views INTEGER,
    -- 统计信息
    total_videos INTEGER DEFAULT 0,
    downloaded_videos INTEGER DEFAULT 0,
    expected_total INTEGER DEFAULT 0,
    expected_total_synced_at TIMESTAMP,
    is_active BOOLEAN DEFAULT 1,
    last_check TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

> 统计口径（本地优先）：`subscriptions.total_videos/downloaded_videos` 的计算以“磁盘真实存在的产物”为准（视频或配套 JSON），应用启动与关联操作后会按本地为准刷新统计，避免 DB 计数大于本地的情况。

### 2. 视频表（videos）

```sql
CREATE TABLE videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bilibili_id TEXT NOT NULL UNIQUE,     -- B站视频ID（BV或av），全局唯一
    title TEXT NOT NULL,                  -- 视频标题
    uploader TEXT,                        -- UP主名称
    uploader_id TEXT,                     -- UP主ID
    duration INTEGER DEFAULT 0,           -- 时长（秒）
    upload_date TIMESTAMP,                -- 上传时间
    description TEXT,                     -- 描述
    tags TEXT,                            -- JSON字符串标签
    -- 文件路径
    video_path TEXT,                      -- 视频文件路径
    json_path TEXT,                       -- JSON文件路径
    thumbnail_path TEXT,                  -- 缩略图路径
    -- 大小与统计
    file_size INTEGER,
    audio_size INTEGER,
    total_size INTEGER,                   -- 便于聚合计算
    view_count INTEGER DEFAULT 0,
    -- 状态
    downloaded BOOLEAN DEFAULT 0,
    downloaded_at TIMESTAMP,
    -- 关联
    subscription_id INTEGER,              -- 关联订阅，可空
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions (id)
);
```

### 3. 下载任务表（download_tasks）

```sql
CREATE TABLE download_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 兼容旧库：video_id 可能存在且非空约束，保留为可空；新逻辑统一使用 bilibili_id
    video_id TEXT,
    bilibili_id TEXT NOT NULL,
    subscription_id INTEGER,
    status TEXT DEFAULT 'pending',     -- pending/downloading/completed/failed
    progress REAL DEFAULT 0.0,
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 4. Cookie管理表（cookies）

```sql
CREATE TABLE cookies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                 -- Cookie名称/标识
    sessdata TEXT NOT NULL,             -- SESSDATA
    bili_jct TEXT,                      -- bili_jct
    dedeuserid TEXT,                    -- DedeUserID
    is_active BOOLEAN DEFAULT 1,        -- 是否激活
    failure_count INTEGER DEFAULT 0,    -- 失败次数（用于阈值禁用）
    last_failure_at TIMESTAMP,          -- 最近失败时间
    usage_count INTEGER DEFAULT 0,      -- 使用次数
    last_used TIMESTAMP,                -- 最后使用时间
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 5. 设置表（settings，KV 缓存）

```sql
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

用途：
- 缓存轻量同步状态与统计：`sync:{sid}:status`（JSON），示例：`{"status":"running","remote_total":120,"updated_at":"..."}`
- 缓存待下载估算：`agg:{sid}:pending_estimated`（字符串整数）
- 缓存失败重试队列：`retry:{sid}:failed_backfill`（JSON 数组）
 - 远端总数缓存（标准）：`remote_total:{sid}`（JSON，示例：`{"total":180,"url":"...","timestamp":1699999999}`）
   - 兼容旧键：`expected_total:{sid}` 同步双写，读取时优先新键，逐步下线旧键
   - 统一读写封装：参见后端 `app/services/remote_total_store.py` 与常量定义 `app/constants.py`

## 统一统计口径与快照字段（新增）

- 统一统计与聚合由 `app/services/metrics_service.py` 提供，前端仅消费以下统一字段，不得在前端自行计算或覆盖：
  - `expected_total`（远端应有总数，来自最近快照）
  - `expected_total_cached`（布尔，是否命中快照TTL）
  - `expected_total_snapshot_at`（ISO 时间，快照生成时间）
  - `on_disk_total`、`db_total`、`failed_perm`、`pending`、`sizes.downloaded_size_bytes`
- 快照与TTL规范：
  - 概览端点 `GET /api/overview` 返回 `computed_at`，TTL=60 秒（仅读侧防抖，不改变统计口径）。
  - 订阅端点 `GET /api/subscriptions` 返回 `expected_total_*` 字段，TTL=1 小时（远端总数快照）。
  - 前端展示“新鲜度/TTL”时基于 `computed_at` 或 `expected_total_snapshot_at` 与 `expected_total_cached` 判定有效/已过期。
- 兼容字段：
  - `remote_total`、`expected_total_videos`、`downloaded_videos`、`pending_videos` 仅为过渡用途，保证与统一字段等值，逐步下线。


## 数据关系图

```
subscriptions (1) -----> (N) videos
     |
     |
     v
download_tasks (N)

cookies (独立表，通过应用逻辑关联)
```

## 索引设计

```sql
-- 订阅表索引
CREATE INDEX idx_subscriptions_type ON subscriptions(type);
CREATE INDEX idx_subscriptions_active ON subscriptions(is_active);
CREATE INDEX idx_subscriptions_updated ON subscriptions(updated_at);

-- 视频表索引
CREATE INDEX idx_videos_subscription ON videos(subscription_id);
CREATE INDEX idx_videos_bilibili_id ON videos(bilibili_id);
CREATE INDEX idx_videos_downloaded ON videos(downloaded);
CREATE INDEX idx_videos_upload_date ON videos(upload_date);

-- 任务表索引
CREATE INDEX idx_tasks_subscription ON download_tasks(subscription_id);
CREATE INDEX idx_tasks_status ON download_tasks(status);
CREATE INDEX idx_tasks_created ON download_tasks(created_at);

-- Cookie表索引
CREATE INDEX idx_cookies_active ON cookies(is_active);
CREATE INDEX idx_cookies_last_used ON cookies(last_used);
```

## 数据完整性约束

### 级联删除规则
- 删除订阅时，级联删除相关视频记录和任务记录
- Cookie表独立管理，不参与级联删除

### 数据验证规则
- `subscription_type` 必须为预定义枚举值
- `download_status` 和 `task_status` 必须为预定义枚举值
- `priority` 范围：1-10
- `progress` 范围：0.0-1.0
- `retry_count` 不能超过 `max_retries`

## 数据迁移策略

### 版本升级处理
- 新增字段使用 `ALTER TABLE ADD COLUMN` 
- 字段默认值确保向后兼容
- 应用启动时自动执行轻量迁移脚本

### 数据备份建议
- 定期备份 SQLite 数据库文件
- 重要操作前创建数据快照
- 支持数据导出为 JSON 格式
