# 数据模型设计（Data Model Design）

更新时间：2025-01-15 21:00 (Asia/Shanghai)

## 数据库表结构

### 1. 订阅表（subscriptions）

```sql
CREATE TABLE subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                    -- 订阅名称
    subscription_type TEXT NOT NULL,       -- 订阅类型：collection/uploader/keyword/url_list
    url TEXT NOT NULL,                     -- 订阅 URL
    target_directory TEXT NOT NULL,        -- 目标下载目录
    is_active BOOLEAN DEFAULT 1,           -- 是否激活
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**字段说明**：
- `subscription_type`：支持四种类型
  - `collection`：B站合集订阅（已实现）
  - `uploader`：UP主订阅（数据模型支持，逻辑待实现）
  - `keyword`：关键词订阅（数据模型支持，逻辑待实现）
  - `url_list`：特定URL列表订阅（规划中）

### 2. 视频表（videos）

```sql
CREATE TABLE videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL,      -- 关联订阅ID
    bv_id TEXT NOT NULL,                   -- B站视频BV号
    title TEXT NOT NULL,                   -- 视频标题
    uploader TEXT,                         -- UP主名称
    upload_date TEXT,                      -- 上传日期
    duration INTEGER,                      -- 视频时长（秒）
    view_count INTEGER,                    -- 播放数
    like_count INTEGER,                    -- 点赞数
    file_path TEXT,                        -- 本地文件路径
    file_size INTEGER,                     -- 文件大小（字节）
    download_status TEXT DEFAULT 'pending', -- 下载状态
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions (id),
    UNIQUE(subscription_id, bv_id)         -- 同一订阅下BV号唯一
);
```

**下载状态枚举**：
- `pending`：待下载
- `downloading`：下载中
- `completed`：已完成
- `failed`：下载失败
- `skipped`：已跳过

### 3. 下载任务表（download_tasks）

```sql
CREATE TABLE download_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL,      -- 关联订阅ID
    task_type TEXT NOT NULL,               -- 任务类型
    status TEXT DEFAULT 'pending',         -- 任务状态
    priority INTEGER DEFAULT 5,            -- 优先级（1-10，数字越小优先级越高）
    progress REAL DEFAULT 0.0,             -- 进度（0.0-1.0）
    error_message TEXT,                    -- 错误信息
    retry_count INTEGER DEFAULT 0,         -- 重试次数
    max_retries INTEGER DEFAULT 3,         -- 最大重试次数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,                  -- 开始时间
    completed_at TIMESTAMP,                -- 完成时间
    FOREIGN KEY (subscription_id) REFERENCES subscriptions (id)
);
```

**任务类型**：
- `download`：下载任务
- `scan`：扫描任务
- `associate`：关联任务
- `check`：检查任务

**任务状态**：
- `pending`：等待中
- `running`：运行中
- `completed`：已完成
- `failed`：失败
- `cancelled`：已取消
- `paused`：已暂停

### 4. Cookie管理表（cookies）

```sql
CREATE TABLE cookies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                    -- Cookie名称/标识
    sessdata TEXT NOT NULL,                -- SESSDATA值
    bili_jct TEXT,                         -- bili_jct值
    buvid3 TEXT,                           -- buvid3值
    is_active BOOLEAN DEFAULT 1,           -- 是否激活
    is_valid BOOLEAN DEFAULT 1,            -- 是否有效
    last_used TIMESTAMP,                   -- 最后使用时间
    last_validated TIMESTAMP,              -- 最后验证时间
    failure_count INTEGER DEFAULT 0,       -- 失败次数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

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
CREATE INDEX idx_subscriptions_type ON subscriptions(subscription_type);
CREATE INDEX idx_subscriptions_active ON subscriptions(is_active);

-- 视频表索引
CREATE INDEX idx_videos_subscription ON videos(subscription_id);
CREATE INDEX idx_videos_bv_id ON videos(bv_id);
CREATE INDEX idx_videos_status ON videos(download_status);
CREATE INDEX idx_videos_upload_date ON videos(upload_date);

-- 任务表索引
CREATE INDEX idx_tasks_subscription ON download_tasks(subscription_id);
CREATE INDEX idx_tasks_status ON download_tasks(status);
CREATE INDEX idx_tasks_priority ON download_tasks(priority);
CREATE INDEX idx_tasks_created ON download_tasks(created_at);

-- Cookie表索引
CREATE INDEX idx_cookies_active ON cookies(is_active);
CREATE INDEX idx_cookies_valid ON cookies(is_valid);
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
