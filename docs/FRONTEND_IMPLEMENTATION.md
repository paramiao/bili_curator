# 前端实现说明（Frontend Implementation）

更新时间：2025-08-15 20:59 (Asia/Shanghai)

## 当前实现状态

### 静态页面实现（V6.0）
当前版本采用静态 HTML 页面 + JavaScript 的轻量级实现：

```
bili_curator_v6/static/
├── queue_admin.html      # 队列管理界面
├── video_detection.html  # 视频检测界面  
└── test.html            # API 测试页面
```

### 主要功能页面
- **队列管理**：`/static/queue_admin.html`
  - 任务列表展示，支持状态筛选
  - 队列统计信息（运行中/等待中/失败）
  - 任务操作：暂停/恢复/取消/优先级调整
  - 实时刷新（轮询模式）

- **视频检测**：`/static/video_detection.html`
  - 视频检测服务状态监控
  - 检测结果展示
  - 手动触发检测功能

- **API 测试**：`/static/test.html`
  - 基础 API 接口测试
  - 开发调试工具

## 2. API 对接
- 列表与统计：`GET /api/subscriptions`
- 订阅详情：`GET /api/subscriptions/{id}`、`GET /api/subscriptions/{id}/expected-total`
- 下载任务：`POST /api/subscriptions/{id}/download`、`GET /api/subscriptions/{id}/tasks`
- 自动导入与关联：`POST /api/auto-import/scan`、`POST /api/auto-import/associate`、`POST /api/subscriptions/{id}/associate`
- 调度：`POST /api/scheduler/check-subscriptions`
- 队列管理页：
  - 统计：`GET /api/queue/stats`
  - 操作：`POST /api/queue/pause|resume`、`POST /api/queue/capacity`、`POST /api/requests/{id}/cancel|prioritize`
  - 列表：`GET /api/requests`

## 3. 状态管理
- 统一以订阅为主键：每个订阅维持 `status/progress/logs` 的 UI 状态。
- 日志仅展示最近 N 条（建议 10–50 条），更多查看容器日志。

## 规划中的前端架构（V6.1+）

### 现代化 Web 界面
计划迁移到 `web/src/` 目录，采用现代前端框架：

```
bili_curator_v6/web/
├── src/
│   ├── components/
│   │   ├── Dashboard/      # 总览仪表板
│   │   ├── Subscriptions/  # 订阅管理
│   │   ├── Tasks/          # 任务队列
│   │   ├── Videos/         # 视频管理
│   │   ├── Cookies/        # Cookie 管理
│   │   └── Settings/       # 系统设置
│   ├── services/
│   │   ├── api.ts         # API 封装
│   │   └── websocket.ts   # WebSocket 连接
│   └── utils/
└── dist/                  # 构建输出
```

### 计划功能页面
- **总览仪表板**：系统状态、订阅统计、下载进度
- **订阅管理**：卡片/表格视图，展示名称/类型/状态、本地统计、远端总数
- **订阅详情**：进度条、当前视频、已完成/待下载、最近日志、历史任务
- **任务队列**：实时任务监控，支持暂停/恢复/取消/优先级调整
- **Cookie 管理**：添加/验证/轮换策略配置
- **系统设置**：下载参数、存储路径、定时任务配置

### 实时通信方案
- **当前**：轮询模式，`/api/queue/stats` 与 `/api/requests` 每 2s 刷新
- **计划**：WebSocket 或 SSE 实时推送任务状态变化

### 交互优化
- 远端总数与本地统计并列展示，差值>0 时提示"有 N 个可下载"
- 下载失败时提供具体错误提示（Cookie/网络/权限等）
- 一键化操作：扫描→关联→统计刷新
- 家用友好配置：并发=1、检查间隔=6-12h、失败重试=2

## 开发优先级

### Phase 1: 基础 Web 界面（规划中）
- [ ] 订阅管理页面：列表展示、添加/编辑订阅
- [ ] 总览仪表板：系统状态、统计信息
- [ ] Cookie 管理页面：添加/验证/状态监控

### Phase 2: 任务监控（规划中）
- [ ] 任务队列页面：实时状态、操作控制
- [ ] 下载进度展示：进度条、日志显示
- [ ] 错误处理界面：友好错误提示

### Phase 3: 高级功能（规划中）
- [ ] 实时通信：WebSocket/SSE 推送
- [ ] 移动端适配：响应式设计
- [ ] PWA 支持：离线访问、推送通知

## 技术实现细节

### 当前静态页面技术栈
- **HTML5** + **原生 JavaScript**
- **Bootstrap/CSS** 样式框架
- **轮询机制**：定时调用 REST API 更新状态
- **本地存储**：localStorage 保存用户配置

### API 集成模式
```javascript
// 示例：队列状态轮询
setInterval(async () => {
  const stats = await fetch('/api/queue/stats').then(r => r.json());
  updateQueueDisplay(stats);
}, 2000);
```

### 错误处理策略
- 网络错误：显示重试按钮
- 401/403：提示检查 Cookie
- 500：显示详细错误信息
- 超时：自动重试机制

## 4. 实时方案
- 第一阶段：轮询 `GET /api/subscriptions/{id}/tasks`，低频刷新（3–5s）。
- 第二阶段（可选）：SSE `GET /api/subscriptions/{id}/events`，自动追加日志与更新进度。
- 队列管理页：`/api/queue/stats` 与 `/api/requests` 每 2s 轮询刷新。

## 5. 交互细节
- 远端总数与本地统计并列展示；若差值>0，显式提示“有 N 个可下载”。
- 点击“开始下载”后显示任务面板；失败时给出 Cookie/网络/目录权限等提示。
- 一键化“扫描→自动关联→统计刷新”。
- 队列总览卡片：显示 Cookie/NoCookie 两通道的并发配置、运行计数、可用容量，以及“排队”数（`counts_by_channel.queued_cookie/queued_nocookie`）。

## 6. 家用优化
- 全局设置：并发=1、检查间隔=6–12h、失败重试=2。
- UI 提示：Cookie 失效/无写权限/403/401 显著提示。
