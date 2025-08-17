### 队列诊断信息
```http
GET /api/queue/insights
```

### 获取队列快照（所有请求项）
```http
GET /api/queue/list
```
# API 接口规范（API Specification）

更新时间：2025-08-17 16:20 (Asia/Shanghai)

## 基础信息

- **Base URL**: `http://localhost:8080`
- **Content-Type**: `application/json`
- **认证方式**: 无需认证（内网服务）

## 1. 系统管理接口

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

### 系统状态
```http
GET /api/status
```
**说明**: 返回后端运行状态与核心统计（订阅/视频/Cookie等）。
**响应**:
```json
{
  "status": "running",
  "timestamp": "2025-08-17T16:20:00+08:00",
  "version": "6.0.0",
  "statistics": {
    "total_subscriptions": 12,
    "active_subscriptions": 8,
    "total_videos": 963,
    "downloaded_videos": 963,
    "active_cookies": 1
  }
}
```

### 扫描并自动关联（全局聚合端点）
```http
POST /api/auto-import/scan-associate
```
**说明**: 一次请求内完成“扫描本地文件 + 自动关联 + 统计重算”的聚合操作，便于前端单按钮触发。
**响应**:
```json
{
  "message": "完成",
  "scan": { "scanned_files": 150, "imported_files": 12 },
  "associate": { "associated_count": 8 }
}
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

### 获取订阅列表
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
    "total_videos": 120,
    "remote_total": 150,
    "downloaded_videos": 120,
    "pending_videos": 30,
    "created_at": "2025-01-15T10:00:00Z",
    "updated_at": "2025-01-15T10:00:00Z"
  }
]
```

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
  "last_updated": "2025-01-15T21:00:00Z"
}
```

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
