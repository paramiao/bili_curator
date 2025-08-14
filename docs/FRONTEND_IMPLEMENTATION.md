# 前端实现说明（Frontend Implementation）

更新时间：2025-08-14 13:19 (Asia/Shanghai)

## 1. 页面结构
- 订阅列表：卡片/表格视图，展示名称/类型/is_active、本地统计、远端总数、当前状态、最近日志。
- 订阅详情（抽屉/页）：进度条、当前视频、已完成/待下载、最近日志(尾部若干条)、历史任务、定时检查与自动下载设置。

## 2. API 对接
- 列表与统计：`GET /api/subscriptions`
- 订阅详情：`GET /api/subscriptions/{id}`、`GET /api/subscriptions/{id}/expected-total`
- 下载任务：`POST /api/subscriptions/{id}/download`、`GET /api/subscriptions/{id}/tasks`
- 自动导入与关联：`POST /api/auto-import/scan`、`POST /api/auto-import/associate`、`POST /api/subscriptions/{id}/associate`
- 调度：`POST /api/scheduler/check-subscriptions`

## 3. 状态管理
- 统一以订阅为主键：每个订阅维持 `status/progress/logs` 的 UI 状态。
- 日志仅展示最近 N 条（建议 10–50 条），更多查看容器日志。

## 4. 实时方案
- 第一阶段：轮询 `GET /api/subscriptions/{id}/tasks`，低频刷新（3–5s）。
- 第二阶段（可选）：SSE `GET /api/subscriptions/{id}/events`，自动追加日志与更新进度。

## 5. 交互细节
- 远端总数与本地统计并列展示；若差值>0，显式提示“有 N 个可下载”。
- 点击“开始下载”后显示任务面板；失败时给出 Cookie/网络/目录权限等提示。
- 一键化“扫描→自动关联→统计刷新”。

## 6. 家用优化
- 全局设置：并发=1、检查间隔=6–12h、失败重试=2。
- UI 提示：Cookie 失效/无写权限/403/401 显著提示。
