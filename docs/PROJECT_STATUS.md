# 项目进度与下一步计划（V6）

最后更新：2025-08-19 16:48 (+08:00)

## 1. 概览
- 代码主目录：`bili_curator_v6/app/`
- 关键模块：
  - 接口层：`bili_curator_v6/app/api.py`
  - 调度器：`bili_curator_v6/app/scheduler.py`
  - Cookie 管理：`bili_curator_v6/app/cookie_manager.py`
  - 队列管理、下载器、任务管理等（同目录下）
- 设计参考：`V6_ARCHITECTURE_DESIGN.md`、`docs/API_SPECIFICATION.md`
- 数据口径：本地优先（以本地索引与 Settings 缓存为主，避免频繁远端请求）

## 2. 已实现的主要功能
- 同步与状态
  - 轻量同步触发 `/api/sync/trigger`（单订阅/全局，后台异步计算与缓存）
  - 同步状态查询 `/api/sync/status`（订阅同步状态、远端总数估算、待下载估算、失败队列长度）
  - 手动同步 `/api/subscriptions/{id}/sync`（触发远端快照刷新、清理失败视频、数据一致性修复）
- 增量支持
  - 新增增量相关 API：`POST /api/incremental/refresh-head`、`GET /api/incremental/status/{sid}`
  - 入队协调优先走增量路径，异常时自动回退全量
- 队列管理
  - 统计、列表、取消任务、容量设置（见 `api.py` 队列相关路由）
- 失败管理
  - 失败记录、详情、解除封锁、重试（见 `api.py` 失败相关路由）
  - 失败视频清理 `/api/subscriptions/{id}/clear-failed`（批量删除失败记录）
- 数据一致性服务
  - 远端总数缓存自动刷新机制（1小时有效期，双层回退策略）
  - 数据一致性检查和自动修复服务 `DataConsistencyService`
  - 数据维护API端点 `/api/maintenance/*`（一致性检查、报告生成、缓存刷新）
- 系统状态
  - `/api/status` 返回版本、订阅数、视频数、活跃 Cookie 数等基础统计，且已扩展返回：
    - `scheduler_jobs`（APScheduler 任务列表：id/name/next_run/trigger）
    - `running_tasks`（运行中手动/后台下载任务摘要，来自 `EnhancedTaskManager`）
    - `recent_tasks`（最近 20 条 `DownloadTask`，按 `updated_at` 倒序）
- 调度器（`scheduler.py`）
  - APScheduler 周期任务：订阅检查、Cookie 验证、旧任务清理、僵尸修复
  - 自动导入与统计重算、轻量入队协调（轮转订阅、失败回补优先、增量管线支持、入队上限与时间预算控制）
  - 周期刷新远端头部快照 `refresh_head_snapshots`（仅合集订阅；cap 与过期阈值可配置）
- Cookie 管理（`cookie_manager.py`）
  - 轮换选择、使用计数、失败计数与禁用策略、批量验证、速率限制
- 启动一致性
  - 启动时一致性检查与订阅统计重算，确保"本地优先"数据口径
- 自动导入
  - 新增 `POST /api/auto-import/scan-associate`：异步触发本地扫描导入与自动关联订阅，支持完成后可选触发统计重算
- Web管理界面
  - 统一SPA应用，包含总览、订阅管理、任务队列、Cookie管理、系统设置、系统监控6大模块
  - 订阅统计表格增强：本地文件数、数据库记录数、待下载数、失败数等完整字段
  - 失败视频可视化标识和一键清理功能
  - 手动同步按钮，支持实时数据刷新

## 3. 最新完成（Recently Completed）
- 数据一致性优化（2025-08-19）
  - 修复远端总数缓存刷新机制，确保数据准确性
  - 建立数据一致性检查和自动修复服务
  - 优化待下载数量计算逻辑，基于实际API查询
- Web界面增强（2025-08-19）
  - 订阅管理表格字段完善：本地文件、数据库记录、待下载、失败数等
  - 失败视频红色高亮显示和一键清理功能
  - 手动同步按钮，支持实时数据刷新

## 4. 正在进行（In Progress）
- 增量管线小样本联调与日志完善：`remote_sync_service -> local_index -> download_plan -> 入队`

## 5. 待办与优先级（Backlog & Priority）
- 高优先级
  - 增量管线联调与日志完善（ID: `todo_inc_e2e`）
  - 前端 SPA 对齐新接口（ID: `todo_frontend_wire`）
- 中优先级
  - 订阅类型扩展：`uploader` / `keyword` 下载与同步策略（ID: `todo_feature_types`）
  - 前端配置面板：Settings 关键键读写（并发、每订阅入队上限、时间预算、增量开关、间隔）（ID: `todo_frontend_config_panel`）
  - 数据模型对齐与迁移说明：本地优先统计、`expected_total` 与 `agg:*` 键口径文档化（ID: `todo_data_model_docs`）
- 低优先级
  - 调度策略收敛：降低/改造 `check_subscriptions`，主推 `enqueue_coordinator` 轻量路径（ID: `todo_scheduler_converge`）
  - 观测指标与可视化：失败率、入队/完成吞吐、队列等待时长、失败回补队列长度（ID: `todo_observability`）
  - Cookie 表 schema 迁移与兼容验证：`failure_count`/`last_failure_at`（ID: `todo_cookie_schema`）

## 5. 近两周行动计划（建议）
- 第1周
  - 完成增量管线小样本 E2E 联调（选择 1-2 个订阅作为样本），补齐关键日志埋点（阶段产物：联调报告与问题清单）
  - 实现 `POST /api/auto-import/scan-associate` 并补充接口说明到 `docs/API_SPECIFICATION.md`（已完成）
  - 扩展 `/api/status` 返回调度任务与运行中任务摘要（含 recent_tasks），便于前端态势总览（已完成）
- 第2周
  - 前端 SPA 联动上述接口，完成最小可用运维面板（同步、队列、失败、容量设置）
  - 数据模型文档化与口径对齐（`expected_total`、`agg:*`、本地优先统计），输出迁移/兼容说明
  - 初步观测指标埋点方案（以 Settings/DB 汇总 + 简单接口返回为主，后续再可视化）

## 6. 风险与依赖
- 远端接口与 Cookie 稳定性：需确保 `cookie_manager.py` 的禁用与轮换策略在高失败率时不造成全局阻塞
- 数据口径一致性：增量与全量、远端估算与本地索引之间的口径需在 `docs/DATA_MODEL_DESIGN.md` 与本文件保持一致
- 队列与调度策略：`enqueue_coordinator` 与旧 `check_subscriptions` 并存期间的优先级与资源竞争需观察
 - 部署绑定：`docker-compose.yml` 存在 `./web` 只读挂载但仓库缺少该目录，需修正以免启动异常
 - 代码结构：`scheduler.py` 中方法定义位置需修复，避免任务注册代码悬空导致启动失败

## 7. 里程碑（Milestones）
- M1（已达成）
  - 基础 API、调度与 Cookie 管理打通；轻量同步与入队协调上线
- M2（目标：本月内）
  - 增量管线 E2E 稳定；自动导入手动触发 API；状态接口扩展；最小可用前端运维面板
- M3（目标：次月）
  - 订阅类型扩展；观测指标初版；调度策略收敛；数据模型文档与迁移指南完善

## 8. 附录：关键参考
- 接口说明：`docs/API_SPECIFICATION.md`
- 架构设计：`V6_ARCHITECTURE_DESIGN.md`
- 数据模型：`docs/DATA_MODEL_DESIGN.md`
- 主要代码：
  - `bili_curator_v6/app/api.py`
  - `bili_curator_v6/app/scheduler.py`
  - `bili_curator_v6/app/cookie_manager.py`

