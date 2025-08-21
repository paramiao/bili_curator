# V6 项目过程管理与阶段进展

更新时间：2025-08-14 13:16 (Asia/Shanghai)

> 状态说明（2025-08）：
> - 实时推送（SSE/WebSocket）与仪表盘聚合接口暂缓，当前以 2s 轮询为主。
> - Specific URLs 已改为“仅工具型一次性导入”，不作为订阅类型参与调度。

## 一、状态总览
- 目标：V6 订阅/下载全链路与 V5/Emby 标准一致，支持 Web 管理与自动化服务化。
- 当前进度：端到端联调闭环已打通（解析→订阅→下载→前端轮询/控制，SSE 暂缓）；下载端统一目录/命名/Cookie；API 解析与下载一致；ID/路径入库一致；历史不一致实现已清理。

## 二、已完成（Done）
- 目录结构：按订阅/合集分目录，输出到 `DOWNLOAD_PATH/<订阅名>/`（兼容 Emby 与历史数据）。
- 命名规则：以“视频标题”为主的同名四件套（.mp4|.mkv|.webm + .info.json + .jpg + .nfo）。
- 冲突兜底：同目录发生“不同视频同标题”时，自动回退为“标题 - BV号”。
- Cookie 策略：Web/DB 管理 Cookie，调用 yt-dlp 前临时生成 Netscape cookies.txt，经 `--cookies` 传入；用后删除。
- API 解析对齐：`/api/subscriptions/parse-collection` 改为使用 `--cookies`（统一策略，彻底移除 `--add-header`）。
- 入库字段：写入 `Video.video_path`，并补齐 `json_path`、`thumbnail_path`（存在即写）。
- ID 一致性：统一使用 `bilibili_id`，避免 `video_id/bilibili_id` 混用。
- 旧实现清理：移除早期版本的 `/_scan_existing_files` 与 `/_download_single_video`，避免走错路径。

新增（下载稳定性与产物一致性）：
- 产物查找与命名规范化：兼容 `.fXXXXX.mp4` 等后缀，通配回退选择最大文件；将最终视频重命名为标准 `*.mp4`。
- NFO 同步生成：在视频落盘后立即生成同名 `.nfo` 并记录日志（便于验证 Emby 识别）。
- 日志增强：输出“产物查找回退匹配/规范化重命名/已生成NFO”，定位问题更直观。

新增：
- 前端任务页：适配 `/api/tasks` 返回对象结构，使用 `task_id`/`progress_percent` 渲染，支持暂停/恢复/取消与轮询刷新（SSE 暂缓）。
- 字段一致性：输入统一 `is_active`（兼容 `active`），模型/更新路径补齐 `updated_at` 并对外返回。
- 兼容旧库：创建 `DownloadTask` 时同步写入 `video_id` 与 `bilibili_id`，避免旧库 `NOT NULL` 约束错误。
- 联调闭环：解析→订阅→下载→前端轮询/控制 全流程打通。

新增（自动导入/统计与去重修复）：
- Auto-Import 扫描与入库：新增 `POST /api/auto-import/scan`，扫描 `/app/downloads` 并将本地 JSON/视频入库（兼容 `.json` 与 `.info.json`，兼容 `entries/list` 结构）。
- 自动关联与统计刷新：新增 `POST /api/auto-import/associate` + `POST /api/subscriptions/{id}/associate`，按订阅目录进行视频→订阅匹配并刷新 `total/downloaded/pending`。
- 远端总数独立：新增 `GET /api/subscriptions/{id}/expected-total`，与本地统计分离展示。
- 目录内去重（关键修复）：`EnhancedTaskManager._run_download_task()` 将去重扫描限定为当前订阅目录，避免跨合集误判“任务已完成”。
- 目录内关联（关键修复）：`auto_import.py` 的匹配逻辑仅在订阅下载目录内判断，避免跨合集误匹配。
- Cookie 头部兼容：修复 `cookies.txt` Netscape 头，统一通过 `--cookies` 传入 yt-dlp。

新增（风控友好策略，已落地）：
- 列表分段抓取：`--playlist-items` 每段 100，段间 2–4s 延时，减少一次性大拉取；与 `expected-total` 统计一致。
- 下载节流：并发=1，视频间 5–10s 延时；统一 UA/Referer/重试/轻睡眠。
- 列表缓存回退：实时拉取成功即写 `playlist.json`，失败时回退使用，避免重复触发风控。
- Cookie 最小化：支持仅 SESSDATA。
- 模型一致：下载任务仅写 `bilibili_id`；自动迁移补齐 `download_tasks.video_id/bilibili_id` 并迁移旧数据。

关键文件与函数：
- `bili_curator_v6/app/downloader.py`：`download_collection()`、`_get_collection_videos()`、`_download_single_video()`、`_download_with_ytdlp()`、`_create_nfo_file()`。
- `bili_curator_v6/app/api.py`：`parse_collection_info()` 统一 Cookies；顶部补充 `import asyncio`。

## 三、待办（TODO | 按优先级）
1) 高优先级
- 任务状态端点一致性：统一 `GET /api/tasks/{task_id}/status` 与 `/api/tasks` 的数据源/索引，避免“任务不存在”。
- 前端源码治理：将 `web/dist/` 内逻辑迁移到 `web/src/`，建立构建流程，便于维护与扩展统计展示。
- 下载产物抽检：目录/命名/JSON/NFO/缩略图抽样核对，确保完全符合 V5 标准。

2) 中优先级
- 自动导入与定时：Docker 启动自动扫描导入；定时任务自动更新；Cookie 轮换/禁用可视化。
- 下载器回归扩展：UP 主/关键词订阅下载（API/任务/去重接入现有流程）。
- NFO 字段对齐复核：确保与 V5 模式一致（uniqueid、dateadded、premiered、tags 等）。

3) 低优先级
- 文档完善：Cookie 使用与轮换说明、目录与命名规范、任务管理与故障排除。
- 监控告警（轻量）：下载失败数、403/401 次数的简单统计与展示。

## 四、回归计划（本次指定合集）
- 合集 URL：`https://space.bilibili.com/351754674/lists/5843232?type=season`
- 步骤：
  1. 重启容器生效：`docker compose restart`。
  2. 解析合集名：`POST /api/subscriptions/parse-collection { url }`。
  3. 创建订阅：`POST /api/subscriptions { name, type: collection, url }`。
  4. 启动下载：`POST /api/subscriptions/{id}/download`。
  5. 轮询任务：`GET /api/tasks` 或 `GET /api/tasks/{task_id}/status`（SSE 暂缓；Dashboard 聚合接口暂缓）。
  6. 验证落盘：`DOWNLOAD_PATH/<订阅名>/` 生成四件套，命名为标题；若冲突则“标题 - BV号”。
  7. 验证入库：`video_path/json_path/thumbnail_path` 指向订阅目录；`bilibili_id` 匹配。
  8. Emby 扫描识别 NFO 元数据。

## 五、风险与注意事项
- 容器热更新：代码已更新但容器未重启时，API 使用旧版本代码会报错（已见 `asyncio` 未导入）。
- 重名文件：保持“标题优先”，仅在真实冲突时回退“标题 - BV号”，避免批量改变命名策略。
- Cookie 失效：403/401 时自动禁用当前 Cookie；前端应可见并允许切换。
- 不要单点修复：涉及字段/模型/API/目录/下载流程的变更，必须做全链路一致性检查（遵循用户全局记忆）。
 - 目录匹配口径：所有“导入/关联/查重”均以“订阅下载目录”为边界，避免跨合集误判；若历史目录名与订阅名不一致，需先对齐。

> 运维提示：使用 docker compose 时，建议指定 compose 文件查看日志：
> `docker compose -f bili_curator_v6/docker-compose.yml logs -f`

## 六、下一步执行清单（可立即动作）
- [x] 重启容器并执行回归（解析→建订阅→下载→校验）。
- [x] 字段一致性修复（is_active/updated_at/bilibili_id）与前后端联动（主体完成）。
- [x] 前端任务页轮询/统计显示完善。
- [x] Auto-Import 扫描/关联与统计刷新端点上线并通过验证。
- [x] 下载任务查重限定为订阅目录范围，修复“秒完成”。
- [ ] 修复 `/api/tasks/{task_id}/status` 与 `/api/tasks` 的一致性。
- [ ] 前端迁移至 `web/src/` 并增加构建链路。
- [ ] 下载产物抽样核对与报告。
- [ ] 定时自动导入与更新任务串接。
 - [ ]【暂缓】订阅仪表盘聚合接口与 SSE 实时推送（当前以轮询为主）。

---
### 附：常用命令
```bash
curl -s http://localhost:8080/health
curl -s -X POST http://localhost:8080/api/auto-import/scan
curl -s -X POST http://localhost:8080/api/auto-import/associate
curl -s -X POST http://localhost:8080/api/subscriptions/1/associate
curl -s http://localhost:8080/api/subscriptions/1/expected-total
curl -s -X POST http://localhost:8080/api/subscriptions/1/download
curl -s http://localhost:8080/api/subscriptions | jq .
curl -s http://localhost:8080/api/subscriptions/1/tasks | jq .
```

---
如需我现在直接重启容器并跑此次回归，请确认，我将执行并同步关键日志与结果。
