# API 接口规范（API Specification）

更新时间：2025-01-15 21:05 (Asia/Shanghai)

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
  "status": "ok",
  "timestamp": "2025-01-15T21:05:00Z",
  "version": "6.0.0"
}
```

## 2. 订阅管理接口

### 获取订阅列表
```http
GET /api/subscriptions
```
**响应**:
```json
{
  "subscriptions": [
    {
      "id": 1,
      "name": "订阅名称",
      "subscription_type": "collection",
      "url": "https://space.bilibili.com/...",
      "target_directory": "/path/to/download",
      "is_active": true,
      "created_at": "2025-01-15T10:00:00Z",
      "updated_at": "2025-01-15T10:00:00Z"
    }
  ]
}
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
  "subscription_type": "collection",
  "url": "https://space.bilibili.com/...",
  "target_directory": "/path/to/download",
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
  "subscription_type": "collection",
  "url": "https://space.bilibili.com/...",
  "target_directory": "/path/to/download",
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

## 3. 下载任务接口

### 开始下载
```http
POST /api/subscriptions/{id}/download
Content-Type: application/json

{
  "priority": 5,
  "force": false
}
```

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
GET /api/tasks/{task_id}/status
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
  "capacity": 3
}
```

### 获取请求列表
```http
GET /api/requests
```
**响应**:
```json
{
  "requests": [
    {
      "id": "req_123",
      "subscription_id": 1,
      "status": "running",
      "priority": 5,
      "channel": "cookie",
      "created_at": "2025-01-15T20:00:00Z",
      "started_at": "2025-01-15T20:01:00Z"
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
  "buvid3": "buvid3值"
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
