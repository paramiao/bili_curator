# 技术架构（Technical Architecture）

更新时间：2025-08-14 13:19 (Asia/Shanghai)

## 1. 总览
- 架构：Web 后端（FastAPI） + 前端（轻量页面） + 下载器（yt-dlp 适配器） + 数据层（SQLite/PostgreSQL）。
- 运行环境：Docker（挂载 NAS 目录至 `/app/downloads`）。

## 2. 关键模块与文件
- 后端 API：`bili_curator_v6/app/api.py`
- 下载器与产物处理：`bili_curator_v6/app/downloader.py`
- 自动导入与目录匹配：`bili_curator_v6/app/auto_import.py`
- 任务管理器（下载）：`bili_curator_v6/app/task_manager.py`
- 数据模型：`bili_curator_v6/app/models.py`

## 3. 目录与命名
- 订阅目录：`/app/downloads/<订阅名(清洗后)>/`
- 视频四件套：`*.mp4|*.mkv|*.webm + *.info.json + *.jpg + *.nfo`
- 命名冲突策略：标题优先；冲突退化为“标题 - BV号”。

## 4. 去重与关联口径
- 任务侧去重：`EnhancedTaskManager._run_download_task()` 调用 `downloader._scan_existing_files(db, subscription_dir)`，限定目录范围。
- 导入侧关联：`auto_import.py` 仅判断视频/JSON 是否处于订阅目录下。
- 数据库层全局去重：按 `bilibili_id` 保证唯一。

## 5. 关键端点
- Auto-Import：`POST /api/auto-import/scan`、`POST /api/auto-import/associate`、`POST /api/subscriptions/{id}/associate`
- 下载控制：`POST /api/subscriptions/{id}/download`、`GET /api/subscriptions/{id}/tasks`
- 任务管理：`POST /api/tasks/{task_id}/pause`、`POST /api/tasks/{task_id}/resume`、`POST /api/tasks/{task_id}/cancel`
- 任务查询：`GET /api/tasks`、`GET /api/tasks/{task_id}`、`GET /api/tasks/{task_id}/status`
- 统计/详情：`GET /api/subscriptions`、`GET /api/subscriptions/{id}`、`GET /api/subscriptions/{id}/expected-total`
- 解析：`POST /api/subscriptions/parse-collection`
- 调度：`POST /api/scheduler/check-subscriptions`
- 健康：`GET /health`

## 6. 可观测性与日志
- 任务管理器内维护 `TaskProgress`（状态/进度/日志/时间戳）。
- 前端可按需订阅 SSE（后续提供），或轮询 `GET /api/subscriptions/{id}/tasks`。
- 容器日志用于深度排障：`docker logs -f bili_curator_v6`。

> 使用 docker compose 时，建议指定 compose 文件：`docker compose -f bili_curator_v6/docker-compose.yml logs -f`

## 7. 配置建议（家用）
- 并发=1；检查间隔=6–12h；错误重试=2；日志保留最近N条（任务内限制）。
- Cookie 失效（401/403）自动禁用；在 UI 上显著提示。

## 8. 风控友好与一致性（V6）
- 列表/统计分段：`--playlist-items` 每段 100，段间 2–4s 延时；下载器与 `expected-total` 保持一致策略。
- 下载节流：并发=1；单视频间 5–10s 延时；统一 UA/Referer/重试/轻睡眠。
- Cookie 最小化：支持仅 SESSDATA，通过 `--cookies` 传入 yt-dlp。
- 列表缓存：实时拉取成功后写入 `playlist.json`，失败时回退使用，减少重复请求。
- 数据模型对齐：统一使用 `bilibili_id`；`DownloadTask` 仅写 `bilibili_id`。
- 轻量迁移：启动时自动补齐 `download_tasks.video_id/bilibili_id` 等缺失列，并迁移旧数据。
