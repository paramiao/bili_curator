### 队列诊断信息
```http
GET /api/queue/insights
```

### 获取队列快照（所有请求项）
```http
GET /api/queue/list
```
# API 规范文档

本文档描述了 bili_curator V6 的完整 API 接口规范。

更新时间：2025-08-21

## 基础信息

- **Base URL**: `http://localhost:8080`
- **API 版本**: V6
- **数据格式**: JSON
- **认证方式**: 无需认证（本地部署）
- **前端入口**: 统一单页应用 (SPA)

## 系统状态 API

### 健康检查
```http
GET /health
```
返回系统健康状态和基本信息。

### 系统总览
```http
GET /api/overview
```
返回系统运行状态、统计数据和队列状态。

响应（示例）：
```json
{
  "remote_total": 1520,
  "local_total": 1498,
  "db_total": 1503,
  "failed_perm_total": 22,
  "pending_total": 0,
  "downloaded_size_bytes": 9876543210,
  "computed_at": "2025-08-21T08:20:31.000Z",  
  "queue": { /* 省略 */ },
  "recent_failed_24h": 3
}
```

说明：
- 前端展示“概览快照新鲜度”使用 `computed_at`（缓存TTL=60秒），超过60秒标记“已过期”。

## 核心 API 端点

### 健康检查
```http
GET /health
```
**响应**:
```json
{
  "status": "healthy",
  "timestamp": "2025-08-17T11:24:00+08:00",
  "version": "6.0.0"
}
```

## 9. 解析与回填接口（UP 主）

### 手动解析：名字 ↔ ID（不改数据库）
```http
POST /api/uploader/resolve
Content-Type: application/json

{
  "name": "某UP主"
  // 或者
  // "uploader_id": 123456
}
```
**说明**: 提供名字→ID 或 ID→名字的解析能力，不对数据库做持久化，仅返回解析结果。内部优先使用可用 Cookie 发起请求。
**响应（示例）**:
```json
{
  "ok": true,
  "name": "某UP主",
  "uploader_id": 123456
}
```
**错误**:
- 400 参数缺失或格式错误
- 404 未找到匹配的 UP 主
- 429 触发风控/限流（未来可能）

### 手动解析并回填订阅
```http
POST /api/subscriptions/{id}/resolve
```
**说明**: 针对 `type = "uploader"` 的订阅，尝试解析缺失的 `name` 或 `uploader_id`，并将成功的解析结果回填到数据库。
**响应（示例）**:
```json
{
  "subscription_id": 18,
  "updated": true,
  "name": "某UP主",
  "uploader_id": 123456
}
```
**错误**:
- 404 订阅不存在或类型不为 `uploader`
- 409 订阅字段已完整，无需回填
- 502 上游接口失败（解析失败）

### 目录命名前缀（说明）
- 关键词订阅：目录前缀统一为“关键词：{keyword}”。
- UP 主订阅：目录前缀统一为“up 主：{uploader_name}”，如名字不可得回退为“up 主：{mid}”。
- 该规范自新建目录起生效，历史目录不做批量迁移。

### 系统状态
```http
GET /api/status
```
**说明**: 返回后端运行状态、核心统计与调度/任务观测信息。
**响应**:
```json
{
  "status": "running",
  "timestamp": "2025-08-18T13:43:00+08:00",
  "version": "6.0.0",
  "statistics": {
    "total_subscriptions": 12,
    "active_subscriptions": 8,
    "total_videos": 963,
    "downloaded_videos": 963,
    "active_cookies": 1
  },
  "scheduler_jobs": [
    { "id": "enqueue_coordinator", "name": "enqueue_coordinator", "next_run": "2025-08-18T13:45:00+08:00", "trigger": "interval[minutes=2]" }
  ],
  "running_tasks": [
    {
      "task_id": "download_5_1699999999",
      "subscription_id": 5,
      "subscription_name": "合集A",
      "status": "downloading",
      "progress_percent": 25.0,
      "downloaded_videos": 5,
      "total_videos": 20,
      "current_video": "某标题",
      "error_message": null,
      "started_at": "2025-08-18T13:20:00+08:00",
      "updated_at": "2025-08-18T13:40:00+08:00"
    }
  ],
  "recent_tasks": [
    {
      "id": 123,
      "bilibili_id": "BVxxxx",
      "subscription_id": 5,
      "status": "completed",
      "progress": 100.0,
      "started_at": "2025-08-18T12:10:00+08:00",
      "completed_at": "2025-08-18T12:20:00+08:00",
      "updated_at": "2025-08-18T12:20:00+08:00",
      "error_message": null
    }
  ]
}
```

### 扫描并自动关联（全局触发）
```http
POST /api/auto-import/scan-associate
```
**说明**: 后台触发“扫描本地文件 → 自动关联订阅”的串行任务，立即返回，不阻塞请求。可选在完成后触发一次订阅统计重算。
**请求体（可选）**:
```json
{ "recompute": true }
```
**响应**:
```json
{ "triggered": true }
```

### 扫描并自动关联（仅针对某订阅）
```http
POST /api/subscriptions/{id}/scan-associate
```
**说明**: 仅扫描并关联该订阅下载目录下的视频产物，并重算该订阅统计。
**响应**:
```json
{
  "subscription_id": 1,
  "message": "完成",
  "scan": { "scanned_files": 34, "imported_files": 2 },
  "associate": { "associated_count": 2 }
}
```

## 2. 订阅管理接口

### 获取订阅列表（统一口径）
```http
GET /api/subscriptions
```
**响应**:
注意：实现直接返回数组（未包裹在对象中）。
```json
[
  {
    "id": 1,
    "name": "订阅名称",
    "type": "collection",
    "url": "https://www.bilibili.com/list/xxx",
    "is_active": true,
    // 统一字段（前端仅依赖以下字段进行展示与计算口径）
    "expected_total": 150,                     // 远端应有总数（来自最近快照）
    "expected_total_cached": true,             // 是否命中1小时内快照
    "expected_total_snapshot_at": "2025-08-21T07:30:00Z", // 快照时间（ISO）
    "on_disk_total": 120,                      // 本地有文件数
    "db_total": 1503,                          // 数据库视频记录数
    "failed_perm": 22,                         // 永久失败数
    "pending": 30,                             // 统一口径：max(0, expected_total - on_disk_total - failed_perm)
    "sizes": { "downloaded_files": 120, "downloaded_size_bytes": 9876543210 },

    // 兼容字段（deprecated）：与统一字段等值，仅为过渡保留
    "total_videos": 120,
    "remote_total": 150,
    "expected_total_videos": 150,
    "downloaded_videos": 120,
    "pending_videos": 30,
    "last_check": "2025-01-15T10:00:00Z",
    "created_at": "2025-01-15T10:00:00Z",
    "updated_at": "2025-01-15T10:00:00Z"
  }
]
```

说明：
- 仅依赖统一字段：`expected_total`、`expected_total_cached`、`expected_total_snapshot_at`、`on_disk_total`、`db_total`、`failed_perm`、`pending`、`sizes`。
- 历史兼容字段 `remote_total`、`expected_total_videos`、`downloaded_videos`、`pending_videos` 等与统一字段等值，后续将逐步移除。
- 详情页与列表页展示“快照新鲜度/TTL（1小时）”，基于 `expected_total_snapshot_at` 与 `expected_total_cached` 判断“有效/已过期”。

### 获取订阅详情
```http
GET /api/subscriptions/{id}
```
**响应**:
```json
{
  "id": 1,
  "name": "订阅名称",
  "type": "collection",
  "url": "https://space.bilibili.com/...",
  "is_active": true,
  "video_count": 150,
  "downloaded_count": 120,
  "created_at": "2025-01-15T10:00:00Z",
  "updated_at": "2025-01-15T10:00:00Z"
}
```

### 创建订阅
```http
POST /api/subscriptions
Content-Type: application/json

{
  "name": "订阅名称",
  "type": "collection",
  "url": "https://space.bilibili.com/...",
  "is_active": true
}
```

### 更新订阅
```http
PUT /api/subscriptions/{id}
Content-Type: application/json

{
  "name": "新订阅名称",
  "is_active": false
}
```

### 删除订阅
```http
DELETE /api/subscriptions/{id}
```

### 获取远端视频总数
```http
GET /api/subscriptions/{id}/expected-total
```
**响应**:
```json
{
  "subscription_id": 1,
  "expected_total": 200,
  "expected_total_videos": 200,  // 兼容字段（deprecated）
  "cached": true,
  "last_updated": "2025-01-15T21:00:00Z"
}
```

说明：
- 若命中1小时内缓存，将返回 `cached: true`。订阅远端总数快照TTL=1小时。
- 在需要 Cookie 的回退路径运行时，响应可能包含 `job_id` 用于标识内部队列任务。

### 启用门控（说明）
对于 `type = "uploader"` 的订阅，若后端未成功解析出合法的 UP 主名称（例如为空或为占位“待解析UP主”），则禁止将 `is_active` 置为 `true`，返回 `400 Bad Request`，用于避免目录命名与数据模型不一致。

### 轻量远端同步（触发）
```http
POST /api/sync/trigger
```
**说明**: 触发轻量同步。若 body 未携带 `sid`，则触发全局轻量同步与入队协调。
**请求体（可选）**:
```json
{ "sid": 1, "mode": "lite_head" }
```
**响应**:
```json
{ "triggered": true, "scope": "subscription", "sid": 1 }
```

### 轻量远端同步（状态）
```http
GET /api/sync/status?sid=1
```
**说明**: 查询轻量同步状态与缓存的指标，如 `remote_total`、`pending_estimated` 等。无 `sid` 时返回所有启用订阅。
**响应**:
```json
{
  "items": [
    {
      "subscription_id": 1,
      "name": "某订阅",
      "remote_total": 120,
      "pending_estimated": 7,
      "retry_queue_len": 0,
      "status": "running",
      "updated_at": "2025-08-17T16:18:00+08:00"
    }
  ]
}
```

### 增量快照与状态

```http
POST /api/incremental/refresh-head
```
说明: 后台刷新指定订阅的远端“头部快照”，用于增量入队的参考。可选重置游标。
请求体:
```json
{ "sid": 1, "cap": 200, "reset_cursor": true }
```
响应:
```json
{ "triggered": true, "sid": 1 }
```

```http
GET /api/incremental/status/{sid}
```
说明: 查询增量相关状态与缓存。
响应:
```json
{
  "sid": 1,
  "status": "idle",
  "updated_at": "2025-08-18T13:30:00+08:00",
  "remote_total_cached": 180,
  "head_size": 200,
  "last_cursor": "BV1xxxx..."
}
```

## 3. 下载任务接口

### 下载行为说明（无手动开始下载 API）
启用订阅后（`is_active = true`），调度器会定期获取新增视频并自动入队、下载；暂停订阅（`is_active = false`）将停止入队与下载。
因此，不提供手动“开始下载”的 API。

### 获取订阅任务列表
```http
GET /api/subscriptions/{id}/tasks
```
**响应**:
```json
{
  "tasks": [
    {
      "id": 1,
      "subscription_id": 1,
      "task_type": "download",
      "status": "running",
      "priority": 5,
      "progress": 0.65,
      "error_message": null,
      "retry_count": 0,
      "created_at": "2025-01-15T20:00:00Z",
      "started_at": "2025-01-15T20:01:00Z"
    }
  ]
}
```

### 获取任务状态
```http
GET /api/tasks/{task_id}
```
**响应**:
```json
{
  "id": 1,
  "status": "running",
  "progress": 0.65,
  "error_message": null,
  "updated_at": "2025-01-15T21:00:00Z"
}
```

### 取消任务
```http
POST /api/tasks/{task_id}/cancel
```

## 4. 队列管理接口

### 获取队列统计
```http
GET /api/queue/stats
```
**响应**:
```json
{
  "total_capacity": 2,
  "running_count": 1,
  "queued_count": 3,
  "failed_count": 0,
  "is_paused": false,
  "counts_by_channel": {
    "cookie": {
      "capacity": 1,
      "running": 1,
      "queued": 2
    },
    "nocookie": {
      "capacity": 1,
      "running": 0,
      "queued": 1
    }
  }
}
```

### 暂停队列
```http
POST /api/queue/pause
```

### 恢复队列
```http
POST /api/queue/resume
```

### 设置队列容量
```http
POST /api/queue/capacity
Content-Type: application/json

{
  "requires_cookie": 1,
  "no_cookie": 1
}
```

### 获取请求列表
```http
GET /api/requests
```
**响应**:
```json
{
  "count": 4,
  "items": [
    {
      "id": "job_xxx",
      "type": "download",
      "subscription_id": 1,
      "status": "queued",
      "priority": 5,
      "channel": "cookie",
      "created_at": "2025-01-15T20:00:00Z"
    }
  ]
}
```

### 取消请求
```http
POST /api/requests/{request_id}/cancel
```

### 提升请求优先级
```http
POST /api/requests/{request_id}/prioritize
```

## 5. 自动导入接口

### 扫描本地文件
```http
POST /api/auto-import/scan
```
**响应**:
```json
{
  "message": "扫描完成",
  "scanned_files": 150,
  "imported_files": 12
}
```

### 自动关联视频
```http
POST /api/auto-import/associate
```
**响应**:
```json
{
  "message": "关联完成",
  "associated_count": 8
}
```

### 关联指定订阅
```http
POST /api/subscriptions/{id}/associate
```

## 6. 调度器接口

### 检查所有订阅
```http
POST /api/scheduler/check-subscriptions
```
**响应**:
```json
{
  "message": "检查完成",
  "checked_subscriptions": 5,
  "new_videos_found": 12
}
```

## 7. Cookie 管理接口

### 获取 Cookie 列表
```http
GET /api/cookies
```
**响应**:
```json
{
  "cookies": [
    {
      "id": 1,
      "name": "主账号",
      "is_active": true,
      "is_valid": true,
      "last_used": "2025-01-15T20:00:00Z",
      "failure_count": 0
    }
  ]
}
```

### 添加 Cookie
```http
POST /api/cookies
Content-Type: application/json

{
  "name": "Cookie名称",
  "sessdata": "SESSDATA值",
  "bili_jct": "bili_jct值",
  "dedeuserid": "DedeUserID"
}
```

### 验证 Cookie
```http
POST /api/cookies/{id}/validate
```

### 删除 Cookie
```http
DELETE /api/cookies/{id}
```

## 8. 媒体统计接口

### 获取订阅统计
```http
GET /api/media/subscription-stats
```
**响应**:
```json
{
  "stats": [
    {
      "subscription_id": 1,
      "subscription_name": "订阅名称",
      "total_videos": 200,
      "downloaded_videos": 150,
      "total_size": 10737418240,
      "last_download": "2025-01-15T20:00:00Z"
    }
  ]
}
```

## 状态码说明

- `200 OK`: 请求成功
- `201 Created`: 资源创建成功
- `400 Bad Request`: 请求参数错误
- `404 Not Found`: 资源不存在
- `409 Conflict`: 资源冲突（如重复创建）
- `500 Internal Server Error`: 服务器内部错误

## 错误响应格式

```json
{
  "error": "错误类型",
  "message": "详细错误信息",
  "timestamp": "2025-01-15T21:00:00Z"
}
```

## 常见错误类型

- `validation_error`: 参数验证失败
- `resource_not_found`: 资源不存在
- `duplicate_resource`: 资源重复
- `operation_failed`: 操作执行失败
- `cookie_invalid`: Cookie 无效
- `network_error`: 网络连接错误
