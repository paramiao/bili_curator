# V6 项目过程管理与阶段进展

更新时间：2025-08-13 21:35 (Asia/Shanghai)

## 一、状态总览
- 目标：V6 订阅/下载全链路与 V5/Emby 标准一致，支持 Web 管理与自动化服务化。
- 当前进度：下载端已统一目录/命名/Cookie 策略；API 解析与下载一致；ID/路径入库一致；移除历史不一致实现。

## 二、已完成（Done）
- 目录结构：按订阅/合集分目录，输出到 `DOWNLOAD_PATH/<订阅名>/`（兼容 Emby 与历史数据）。
- 命名规则：以“视频标题”为主的同名四件套（.mp4|.mkv|.webm + .info.json + .jpg + .nfo）。
- 冲突兜底：同目录发生“不同视频同标题”时，自动回退为“标题 - BV号”。
- Cookie 策略：Web/DB 管理 Cookie，调用 yt-dlp 前临时生成 Netscape cookies.txt，经 `--cookies` 传入；用后删除。
- API 解析对齐：`/api/subscriptions/parse-collection` 改为使用 `--cookies`（统一策略，彻底移除 `--add-header`）。
- 入库字段：写入 `Video.video_path`，并补齐 `json_path`、`thumbnail_path`（存在即写）。
- ID 一致性：统一使用 `bilibili_id`，避免 `video_id/bilibili_id` 混用。
- 旧实现清理：移除早期版本的 `/_scan_existing_files` 与 `/_download_single_video`，避免走错路径。

关键文件与函数：
- `bili_curator_v6/app/downloader.py`：`download_collection()`、`_get_collection_videos()`、`_download_single_video()`、`_download_with_ytdlp()`、`_create_nfo_file()`。
- `bili_curator_v6/app/api.py`：`parse_collection_info()` 统一 Cookies；顶部补充 `import asyncio`。

## 三、待办（TODO | 按优先级）
1) 高优先级
- 容器重启使改动生效：`docker compose restart`（当前解析接口报错为旧进程未载入 `import asyncio`）。
- 全链路字段一致性复核：
  - 统一使用 `is_active`（替代历史 `active`）。
  - 模型/API 补齐 `updated_at`，前端/后端统一引用。
  - 全局确保 `bilibili_id` 唯一使用。
- 前端任务进度与统计：订阅任务 Tab 轮询展示；合集总数/已入库/待下载统计。

2) 中优先级
- 自动导入与定时：Docker 启动自动扫描导入；定时任务自动更新；Cookie 轮换/禁用可视化。
- 下载器回归扩展：UP 主/关键词订阅下载（API/任务/去重接入现有流程）。
- NFO 字段对齐复核：确保与 V5 模式一致（uniqueid、dateadded、premiered、tags 等）。

3) 低优先级
- 文档完善：Cookie 使用与轮换说明、目录与命名规范、任务管理与故障排查。
- 监控告警（轻量）：下载失败数、403/401 次数的简单统计与展示。

## 四、回归计划（本次指定合集）
- 合集 URL：`https://space.bilibili.com/351754674/lists/5843232?type=season`
- 步骤：
  1. 重启容器生效：`docker compose restart`。
  2. 解析合集名：`POST /api/subscriptions/parse-collection { url }`。
  3. 创建订阅：`POST /api/subscriptions { name, type: collection, url }`。
  4. 启动下载：`POST /api/subscriptions/{id}/download`。
  5. 轮询任务：`GET /api/tasks` 或 `GET /api/tasks/{task_id}/status`。
  6. 验证落盘：`DOWNLOAD_PATH/<订阅名>/` 生成四件套，命名为标题；若冲突则“标题 - BV号”。
  7. 验证入库：`video_path/json_path/thumbnail_path` 指向订阅目录；`bilibili_id` 匹配。
  8. Emby 扫描识别 NFO 元数据。

## 五、风险与注意事项
- 容器热更新：代码已更新但容器未重启时，API 使用旧版本代码会报错（已见 `asyncio` 未导入）。
- 重名文件：保持“标题优先”，仅在真实冲突时回退“标题 - BV号”，避免批量改变命名策略。
- Cookie 失效：403/401 时自动禁用当前 Cookie；前端应可见并允许切换。
- 不要单点修复：涉及字段/模型/API/目录/下载流程的变更，必须做全链路一致性检查（遵循用户全局记忆）。

## 六、下一步执行清单（可立即动作）
- [ ] 重启容器并执行回归（解析→建订阅→下载→校验）。
- [ ] 字段一致性修复（is_active/updated_at/bilibili_id）与前端联动。
- [ ] 前端任务页轮询/统计显示完善。
- [ ] 定时自动导入与更新任务串接。

---
如需我现在直接重启容器并跑此次回归，请确认，我将执行并同步关键日志与结果。
