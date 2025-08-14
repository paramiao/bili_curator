
注：`expected-total` 与下载链路保持一致的分段统计策略，确保口径一致且更友好于风控。
# V6 需求说明（Requirements）

更新时间：2025-08-14 13:19 (Asia/Shanghai)

## 1. 背景与目标
- 家庭个人使用，核心诉求：省心、稳定、自动化。
- V6 目标：在 V5 增量下载能力基础上，引入 Web 服务化与订阅管理，实现“解析→订阅→下载→入库→统计→媒体库识别”的全链路自动化。
- 全局原则：不要单点修复，涉及模型/字段/目录/接口的改动必须全链路一致。

## 2. 用户场景
- 订阅合集后，系统定时检查是否有新增视频，有则自动下载到 NAS。
- 现有本地历史产物能被自动扫描入库，并正确关联到对应订阅，统计准确。
- 前端能在“订阅页”直接看到远端总数、本地统计、任务状态、最近日志，一键发起“扫描/关联/下载”。

## 3. 功能需求
- 订阅管理：创建/编辑/删除，激活开关 `is_active`，支持类型：collection（合集）；后续扩展 uploader/keyword。
- 命名与目录：订阅目录使用基于 yt-dlp 合集层级元数据自动生成的名称（`uploader + playlist_title` 的清洗版），确保与下载/导入/关联一致。
- 自动导入：扫描下载目录(JSON/视频)，入库；自动关联视频到订阅（按订阅目录匹配），更新统计。
- 下载任务：去重仅限订阅目录；并发限制（家用建议=1）；任务可开始/暂停/恢复/取消；提供进度与最近日志。
- 统计口径：`total_videos/downloaded_videos/pending_videos` 与 `expected_total`（远端）分离展示。
- Cookie 管理：统一通过 `--cookies`；修复 Netscape 头部；可禁用失效 Cookie。
- 定时任务：可配置检查间隔（家用建议6–12小时）；可自动下载新增。

## 4. 非功能需求
- 稳定性：即使 Cookie 失效或网络波动，也应提供清晰的错误提示与手动恢复路径。
- 可观测性：订阅页呈现状态与日志（最近N条即可），便于排障。
- 一致性：字段统一（`is_active/updated_at/bilibili_id`）、目录与命名统一、统计一致。
- 性能：扫描与去重限定目录范围，避免全盘扫描与跨合集误判。

### 风控友好与稳定性（V6 新增）
- 列表/统计分段：合集列表与 `expected_total` 统计均采用 `--playlist-items` 手动分页（每段100），分段间 2–4s 延时，降低一次请求规模。
- 下载节流：并发=1；单视频间 5–10s 延时，降低短时峰值请求。
- 统一参数：yt-dlp 调用统一 UA/Referer/重试/轻睡眠，避免链路差异导致失败。
- Cookie 最小化：支持仅 SESSDATA 传入，通过 `--cookies` 统一注入。
- 列表缓存回退：实时拉取失败时回退使用本地 `playlist.json`，避免重复触发风控。
- 412/风控响应：规划为仅告警不计失败（实现中）；401/403 计失败并结合阈值禁用 Cookie（实现中，见下）。

## 5. 接口与交互（关键）
- Auto-Import：`POST /api/auto-import/scan`、`POST /api/auto-import/associate`、`POST /api/subscriptions/{id}/associate`
- 下载控制：`POST /api/subscriptions/{id}/download`、`GET /api/subscriptions/{id}/tasks`
- 统计与详情：`GET /api/subscriptions`、`GET /api/subscriptions/{id}`、`GET /api/subscriptions/{id}/expected-total`
- 健康与调度：`GET /health`、`POST /api/scheduler/check-subscriptions`
- 任务控制：`POST /api/tasks/{task_id}/pause`、`POST /api/tasks/{task_id}/resume`、`POST /api/tasks/{task_id}/cancel`
- 任务查询：`GET /api/tasks`、`GET /api/tasks/{task_id}`、`GET /api/tasks/{task_id}/status`

## 6. 验收标准（DoD）
- 本地历史产物入库与订阅自动关联准确（无跨合集误判）。
- 订阅统计真实反映目录内产物状态；远端总数可独立刷新。
- 点击“开始下载”如有新增则进入下载流程，不再出现“秒完成未下载”。
- 家用默认配置下载并发=1，检查间隔=6–12h；日志有提示、错误可定位。

## 7. 兼容性与迁移
- 数据库自动迁移：启动时自动补齐旧库缺失列（如 `download_tasks.video_id/bilibili_id`），并将旧 `video_id` 迁移到 `bilibili_id`；新链路统一使用 `bilibili_id`。
- 事务一致性：创建下载任务前做防御性 `rollback()`，避免上一异常影响当前事务。

## 8. 运维与日志
- Docker 启动/构建：`docker compose -f bili_curator_v6/docker-compose.yml up -d --build`
- 查看日志（需指定 compose 文件）：`docker compose -f bili_curator_v6/docker-compose.yml logs -f`
