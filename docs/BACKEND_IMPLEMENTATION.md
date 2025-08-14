# 后端实现说明（Backend Implementation）

更新时间：2025-08-14 13:19 (Asia/Shanghai)

## 1. 关键代码路径
- API：`bili_curator_v6/app/api.py`
- 下载器：`bili_curator_v6/app/downloader.py`
- 自动导入：`bili_curator_v6/app/auto_import.py`
- 任务管理器：`bili_curator_v6/app/task_manager.py`
- 模型：`bili_curator_v6/app/models.py`

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
- 远端总数：`GET /api/subscriptions/{id}/expected-total` 独立获取，前端单独展示与刷新。

## 6. Cookie 策略
- 统一通过 `--cookies` 传入 yt-dlp，使用临时 Netscape 格式文件；
- 解析/下载一致；出现 401/403 自动禁用该 Cookie 并记录。

## 7. 定时任务
- `POST /api/scheduler/check-subscriptions` 触发检查；后续计划：后台周期任务 + 自动下载开关。

## 8. 一致性原则
- 字段统一：仅使用 `bilibili_id`；输入使用 `is_active`；更新补齐 `updated_at`；
- 目录口径统一：导入/关联/去重都以“订阅下载目录”为边界，避免跨合集。
