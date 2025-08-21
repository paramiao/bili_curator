# 项目进度与下一步计划（V6）

最后更新：2025-08-21 17:52 (+08:00)

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
 - 订阅类型支持现状
   - 已支持：`collection`（合集/列表）、`keyword`（关键词）、`uploader`（UP主，已接入解析/回填与启用门控）
   - 待决策：`specific_urls`（一次性导入还是持续订阅）

## 3. 最新完成（Recently Completed）
- 数据一致性优化（2025-08-19）
  - 修复远端总数缓存刷新机制，确保数据准确性
  - 建立数据一致性检查和自动修复服务
  - 优化待下载数量计算逻辑，基于实际API查询
- Web界面增强（2025-08-19）
  - 订阅管理表格字段完善：本地文件、数据库记录、待下载、失败数等完整字段
  - 失败视频可视化标识和一键清理功能
  - 手动同步按钮，支持实时数据刷新
- UP主订阅能力（2025-08-21）
  - 新增“UP主名字↔ID”解析与自动回填服务（优先使用可用 Cookie）
  - 启用门控：当 UP 主名称未成功解析（空或“待解析UP主”）时，禁止 `is_active=true`
  - 目录命名规范统一：关键词用“关键词：…”，UP 主用“up 主：…”，仅新建生效
  - 新增手动解析端点：`POST /api/uploader/resolve`、`POST /api/subscriptions/{sid}/resolve`
  - 前端适配：在订阅列表为 `uploader` 类型提供“立即解析”按钮；当启用因未解析而被拒绝（400）时，弹出友好提示并引导解析
  
- 远端总数统一（2025-08-21）
  - 字段统一：标准字段为 `expected_total`；兼容字段 `remote_total`/`expected_total_videos` 仍返回且等值（标记 deprecated）
  - 接口对齐：`GET /api/subscriptions` 返回体新增 `expected_total` 与可选 `remote_status`
  - 前端调整：仅对 `collection` 类型显示“远端总数（获取/刷新）”控件；列表渲染优先读取 `expected_total`，兼容回退 `remote_total`
  - 缓存键规范：标准 `remote_total:{sid}`，兼容旧键 `expected_total:{sid}`；封装见 `app/services/remote_total_store.py`

- 统计口径统一（2025-08-21）
  - 新增统一统计服务：`app/services/metrics_service.py`
    - 字段：`expected_total`、`expected_total_cached`、`expected_total_snapshot_at`、`on_disk_total`、`db_total`、`failed_perm`、`pending`、`sizes.downloaded_size_bytes`
    - 公式：`pending = max(0, expected_total - on_disk_total - failed_perm)`
    - 容量：三级回退（`Video.total_size` → `Video.file_size` → 磁盘文件大小）
  - 修正待下载列表口径：`app/services/pending_list_service.py::_compute_current_pending()` 扣除失败数，明细与数量保持一致
  - 已验证一致性：`GET /api/download/aggregate`、`GET /api/subscriptions`、`GET /api/overview` 在 `pending`/`expected_total`/快照口径上一致（以 `metrics_service` 为准）

- 概览性能优化（2025-08-21）
  - `/api/overview` 增加 60 秒轻量缓存，降低高频访问时的 DB/磁盘遍历开销
  - 不改变统计口径，仅对读侧聚合做防抖

- 快照新鲜度/TTL 提示（2025-08-21）
  - 概览页：前端读取 `GET /api/overview` 的 `computed_at` 显示“快照新鲜度”，TTL=60 秒，超时标注“已过期”
  - 订阅列表页：显示 `expected_total` 的“快照新鲜度/TTL（1小时）”，基于 `expected_total_snapshot_at` 与 `expected_total_cached`
  - 订阅详情页：与列表页一致显示远端总数与快照提示（TTL=1小时），严格依赖统一字段

- 前端统一口径与交互收敛（2025-08-21）
  - 合并“获取/刷新”为单一“刷新远端快照”按钮，增加 10 秒节流，请求期间禁用按钮
  - `pending_estimated` 仅在后端 `pending` 缺失时兜底显示，并标注“(估算)”；默认不覆盖后端 `pending`
  - 列表页 pending 严格依赖后端统一口径，取消任何前端自算覆盖

## 4. 正在进行（In Progress）
- 增量管线小样本联调、守护与日志完善：`remote_sync_service -> local_index -> download_plan -> 入队`
- 订阅增量同步支持扩展（按订阅开关/批量/回填）
- 解析服务缓存与限流（TTL 缓存、失败计数与退避、速率限制）

 - 远端统计统一落地（已完成）
  - 后端：`/api/subscriptions`、`/api/subscriptions/{id}`、`/api/overview` 三端点统一接入 `metrics_service`
  - 前端：字段收敛（仅消费统一字段），展示 `expected_total_cached` 与快照时间，提供“刷新远端快照”入口
  - 前端（进行中）：详情页/总览页补充“快照新鲜度/TTL”提示与禁用策略，避免误解数据时效
  - 文档：更新 `docs/API_SPECIFICATION.md`、`docs/DATA_MODEL_DESIGN.md`、`CHANGELOG.md`、本文件

## 5. 待办与优先级（Backlog & Priority）
- 高优先级
  - 增量入队流水线审计与结构化日志完善（ID: `todo_inc_audit_logs`）
  - 端到端集成测试（入队协调器 + 任务管理器 + 去重/并发）（ID: `todo_inc_e2e_tests`）
  - 解析服务缓存与限流（TTL 缓存、失败计数/退避、速率限制）（ID: `todo_resolver_cache_ratelimit`）
  - API 改造：三端点接入 `metrics_service`（ID: `refactor-api-to-metrics`）
  - 契约与快照测试：pending/failed/expected_total 算法与端点一致性（ID: `add-contract-and-snapshot-tests`）
- 中优先级
  - 订阅增量同步支持扩展与验证（按订阅开关/批量/回填）（ID: `todo_subscription_incremental`）
  - Specific URLs 订阅策略：一次性 vs 持续订阅（ID: `todo_specific_urls_strategy`）
  - 前端配置面板：Settings 关键键读写（并发、每订阅入队上限、时间预算、增量开关、间隔）（ID: `todo_frontend_config_panel`）
  - 数据模型对齐与迁移说明：本地优先统计、`expected_total` 与 `agg:*` 键口径文档化（ID: `todo_data_model_docs`）
  - CI 自检：扫描旧键 `expected_total:{sid}` 与旧字段直读，防止回归（ID: `todo_ci_guard_remote_total`)
  - 开发者指南与 CR 清单：仅允许通过 `metrics_service` 取数（ID: `dev-guide-and-cr-checklist`）
- 低优先级
  - 调度策略收敛：降低/改造 `check_subscriptions`，主推 `enqueue_coordinator` 轻量路径（ID: `todo_scheduler_converge`）
  - 观测指标与可视化：失败率、入队/完成吞吐、队列等待时长、失败回补队列长度（ID: `todo_observability`）
  - Cookie 表 schema 迁移与兼容验证：`failure_count`/`last_failure_at`（ID: `todo_cookie_schema`）

## 5. 近两周行动计划（更新）
- 第1周（当前优先）
  - **UP主订阅功能实现**：集成 B 站 UP 主空间 API，实现视频列表获取、待下载计算与下载调度
  - **增量入队流水线审计**：落实按订阅/全局开关、批量限制、回填上限，补充结构化日志
  - 完善订阅类型扩展与调度器集成，确保与现有合集/关键词流程一致
  - **统一统计口径落地（后端）**：改造 `/api/subscriptions`、`/api/subscriptions/{id}`、`/api/overview` 接入 `metrics_service`；保留兼容字段并标注 deprecated
  - **统一统计口径落地（前端）**：字段收敛与 UI 提示（快照过期与刷新入口）
- 第2周
  - 订阅增量同步支持扩展与验证，完善 `RemoteSyncService` 
  - 前端 SPA 联动新订阅类型，完善订阅创建和管理界面
  - 增量管线联调与端到端测试（包含 UP 主路径）
  - 契约与端点快照测试完善，加入预提交扫描规则（禁止在新代码中自行计算 pending/直连 Settings 拼键）

## 6. 风险与依赖
- 远端接口与 Cookie 稳定性：需确保 `cookie_manager.py` 的禁用与轮换策略在高失败率时不造成全局阻塞
- 数据口径一致性：增量与全量、远端估算与本地索引之间的口径需在 `docs/DATA_MODEL_DESIGN.md` 与本文件保持一致
- 队列与调度策略：`enqueue_coordinator` 与旧 `check_subscriptions` 并存期间的优先级与资源竞争需观察
 - 部署绑定：`docker-compose.yml` 存在 `./web` 只读挂载但仓库缺少该目录，需修正以免启动异常
 - 代码结构：`scheduler.py` 中方法定义位置需修复，避免任务注册代码悬空导致启动失败
 - 名字解析歧义：UP 主名字→ID 可能多解/重名，需做最优匹配与 disambiguation 策略；提供缓存与回退（以 ID 为准）

## 6.1 订阅标识与目录命名规范（新增）
- UP 主订阅：
  - 输入支持 name 或 id（两者至少其一）。
  - 仅 name 时：自动解析 mid 并回填；仅 id 时：自动回填名字（解析失败则保留 id）。
  - 解析具备：缓存（TTL，规划中）、限速（规划中）、重试与错误上报；对多解/重名进行最优匹配或提示。
- 目录命名（仅对新建生效）：
  - 关键词订阅：目录名为“关键词：{keyword}”。
  - UP 主订阅：目录名为“up 主：{uploader_name}”；若名字不可得，回退为“up 主：{mid}”。
  - 合集订阅：保持现有规则。

## 6.2 手动解析端点（新增）
- `POST /api/uploader/resolve`：传入 `name` 或 `uploader_id`，返回解析结果，不改数据库。
- `POST /api/subscriptions/{sid}/resolve`：对 `uploader` 类型订阅尝试解析并回填缺失字段，成功则落库。

## 7. 里程碑（Milestones）
- M1（已达成）
  - 基础 API、调度与 Cookie 管理打通；轻量同步与入队协调上线
- M2（目标：本月内，重新调整）
  - **UP主订阅功能完整实现**；增量管线 E2E 稳定；状态接口扩展；最小可用前端运维面板（关键词订阅已完成基础接入）
- M3（目标：次月）
  - 新订阅类型增量同步支持；观测指标初版；调度策略收敛；数据模型文档与迁移指南完善

## 8. 附录：关键参考
- 接口说明：`docs/API_SPECIFICATION.md`
- 架构设计：`V6_ARCHITECTURE_DESIGN.md`
- 数据模型：`docs/DATA_MODEL_DESIGN.md`
- 主要代码：
  - `bili_curator_v6/app/api.py`
  - `bili_curator_v6/app/scheduler.py`
  - `bili_curator_v6/app/cookie_manager.py`


