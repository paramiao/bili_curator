# 更新日志

## [V7.0.0] - 2025-08-29

### 🚀 重大更新 - STRM流媒体支持
- **双模式架构**：支持本地下载(LOCAL) + 流媒体(STRM)两种订阅模式
- **按需播放**：STRM模式仅生成轻量级文件，通过代理服务实时播放
- **存储优化**：STRM模式每视频仅占用~50KB，相比本地模式节省99%存储空间
- **完全兼容**：V6所有功能保持不变，新增功能完全隔离

### 🌟 核心特性
- **STRM代理服务**：实时解析B站视频流，支持720p/1080p质量选择
- **智能缓存**：多层缓存策略，提升播放响应速度
- **目录分离**：LOCAL模式使用/downloads，STRM模式使用/strm
- **媒体服务器集成**：完美支持Emby/Jellyfin等媒体服务器

### 📦 技术架构
- **数据模型扩展**：Subscription添加download_mode字段
- **代理服务**：新增8081端口的流媒体代理服务
- **文件格式**：生成.strm、.nfo、.jpg三种文件类型
- **API扩展**：新增STRM专用接口和状态监控

### 🔧 V7.0.0 修复内容
- **Cookie管理修复**：解决Cookie模型导入路径错误，确保B站API调用正常
- **播放URL修复**：优化B站API参数(fnval=0)，确保视频播放链接获取成功
- **缩略图功能完善**：STRM文件生成时自动下载缩略图，完整支持媒体服务器
- **现有数据修复**：提供缩略图补充脚本，修复历史STRM文件缺失的缩略图

### 🔄 升级路径
- **平滑升级**：V6→V7无缝升级，数据自动迁移
- **版本回退**：支持一键回退到V6稳定版本
- **Docker标签**：bili_curator:v6 (稳定版) / bili_curator:v7 (STRM版)

---

## [V6.1.0] - 当前稳定版

### 🆕 新增/变更
- 远端总数字段统一：
  - 后端统一返回 `expected_total`（标准），同时保留兼容字段 `remote_total`/`expected_total_videos`（与标准字段等值，标记 deprecated）
  - `/api/subscriptions` 列表返回体已包含 `expected_total`、`remote_status`
- 概览与订阅快照新鲜度/TTL 显示：
  - 概览页：前端读取 `/api/overview` 的 `computed_at` 显示“快照新鲜度”，TTL=60 秒，超时标注“已过期”
  - 订阅列表与详情页：显示 `expected_total` 的“快照新鲜度/TTL（1小时）”，基于 `expected_total_snapshot_at` 与 `expected_total_cached`；严格依赖统一字段
- 前端订阅管理：
  - 仅对 `collection` 类型显示“远端总数（获取/刷新）”控件
  - 列表渲染优先读取 `expected_total`，兼容回退 `remote_total`
  - 合并“获取/刷新”为单一“刷新远端快照”按钮，并增加 10s 节流与请求期间按钮禁用

### 🔧 优化改进
- `/api/overview` 增加 60 秒轻量缓存，减少高频访问时的 DB/磁盘遍历开销（不影响一致性口径）
- `pending_estimated` 前端仅在后端 `pending` 缺失时作为兜底显示，并标注“(估算)”来源，避免覆盖统一口径
- 一致性回填找不到视频产物（长尾不下降）：
  - 修复 `app/consistency_checker.py::_find_video_file()` 对 `*.info.json` 的处理，剥离 `.info` 再匹配 `.mp4/.mkv/.webm/...`。
  - 修复 `app/auto_import.py::_find_video_file()` 同步逻辑，确保增量导入对 `*.info.json` 也能回填 `video_path`/`downloaded`。
  - 修复 `app/auto_import.py::_find_thumbnail_file()` 的 `.info` 兼容，缩略图可正确关联。
- 调度器入队节流键名修正：统一读取 `enqueue_time_budget_seconds` 与 `max_enqueue_per_subscription`，避免误用旧键导致轮次入队不足。
- 代码去重与可维护性：抽取 `app/utils/path_utils.py` 提供 `strip_info_suffix()`、`base_name_from_json_path()`，并重构 `auto_import.py`、`consistency_checker.py` 统一使用；新增 `app/utils/__init__.py`。

### 🧪 测试
- 新增 `tests/test_path_utils.py`，覆盖 `*.info.json` 与普通 `.json` 的基名解析；包含中文文件名用例。

### 🐛 问题修复
- 聚合页待下载口径统一：`GET /api/download/aggregate` 改为复用 `metrics_service.compute_subscription_metrics` 的 `pending` 字段，确保与 `/api/subscriptions`、`/api/overview` 一致。
- 容量统计三级回退：`total_size`/`file_size`/磁盘文件大小三级回退已在 `metrics_service._compute_sizes()` 生效，修复 DB 字段为 NULL 时容量统计为 0 的问题，并加入兜底。

### 📦 依赖与构建
- 固定 `yt-dlp` 版本为 `==2025.8.20`，解决 bilibili 抽取器不兼容导致的列表抓取失败；已重建镜像并验证容器内版本。

### 📊 运行与验证
- 长尾治理：订阅 6/9 的 pending 均已清零，`pending_total=0`；一致性回填 `records_updated` 正常回填。
- 文档：更新 `docs/legacy/PROJECT_PROGRESS_V6.md` 第七节（长尾治理进展），记录根因、修复与验证过程，并标注“本地文件数 > 远端总数”的后续治理项。

### 📚 文档
- 更新 `docs/API_SPECIFICATION.md`：补充 `expected_total` 标准字段与兼容策略、`expected-total` 接口的 `cached`/`job_id` 说明；新增 `expected_total_cached`、`expected_total_snapshot_at` 与 `overview.computed_at`（含TTL说明）
- 更新 `docs/DATA_MODEL_DESIGN.md`：统一远端总数缓存键规范 `remote_total:{sid}`，兼容旧键 `expected_total:{sid}`，并指向封装模块
- 新增“统一统计口径与快照字段”小节：明确 `metrics_service` 输出字段与 TTL（overview=60s、subscriptions=1h），前端仅依赖统一字段
- 更新 `docs/PROJECT_STATUS.md`：记录概览/列表/详情页的新鲜度/TTL 展示与统一字段落地情况
- 前端订阅管理：为 `uploader` 类型订阅新增“立即解析”按钮，支持手动触发解析并回填
- 启用门控与友好提示：当 UP 主名称未解析成功时，启用（is_active=true）被拒绝并弹出提示，引导用户“立即解析”
- API 文档与端点：
  - 新增 `POST /api/uploader/resolve`（名字↔ID 解析，不落库）
  - 新增 `POST /api/subscriptions/{id}/resolve`（解析并回填订阅字段）
- 目录命名前缀统一（仅新建生效）：
  - 关键词订阅前缀“关键词：{keyword}”
  - UP 主订阅前缀“up 主：{uploader_name}”（无名则回退为 mid）

### 📚 文档
- 更新 `docs/PROJECT_STATUS.md`、`docs/API_SPECIFICATION.md`，补充解析端点、启用门控说明与目录前缀规范

---

## [V6.1.0] - 2025-08-19

### 🆕 新增功能
- **数据一致性服务**：新增 `DataConsistencyService` 类，提供自动数据一致性检查和修复
- **失败视频管理**：订阅管理界面支持失败视频可视化和一键清理
- **数据维护API**：新增 `/api/maintenance/*` 端点，支持一致性检查、报告生成、缓存刷新
- **手动同步增强**：同步操作自动触发数据一致性修复

### 🔧 优化改进
- **远端总数缓存机制**：优化缓存刷新策略，1小时有效期，双层回退机制
- **订阅统计表格**：增加"数据库记录"字段，便于数据一致性监控
- **失败视频显示**：失败数量红色高亮显示，提供清晰的视觉区分
- **待下载数量计算**：基于实时API查询结果，确保数据准确性

### 🐛 问题修复
- 修复远端总数缓存过期导致的数据不准确问题
- 修复订阅统计API中缺失字段的问题
- 优化数据库记录与本地文件数量不一致的检测

### 📚 文档更新
- 更新项目状态文档，反映最新功能和架构变更
- 完善README.md，增加数据维护相关API说明
- 新增CHANGELOG.md，记录版本更新历史

### 🔗 API变更
- 新增 `POST /api/subscriptions/{id}/clear-failed` - 清理失败视频记录
- 新增 `POST /api/maintenance/check-consistency` - 数据一致性检查
- 新增 `POST /api/maintenance/refresh-remote-totals` - 刷新远端总数缓存
- 新增 `GET /api/maintenance/consistency-report` - 获取一致性报告
- 扩展 `GET /api/media/subscription-stats` - 增加 `local_total` 字段

---

## [V6.0.0] - 2025-08-18

### 🚀 重大更新
- **V6架构重构**：全新的现代化Web界面和后端架构
- **统一SPA应用**：包含总览、订阅管理、任务队列、Cookie管理、系统设置、系统监控6大模块
- **Docker一键部署**：单容器部署，零配置启动
- **智能统计系统**：目录统计、订阅统计、系统监控三位一体

### 🌟 核心特性
- **现代化Web管理界面**：告别命令行，全Web操作
- **智能订阅管理**：支持合集、UP主、关键词、特定视频4种订阅类型
- **增强Cookie管理**：Web界面管理，自动轮换，支持会员内容
- **实时任务监控**：任务队列、进度追踪、状态管理
- **数据维护工具**：一致性检查、容量回填、同步缓存管理

### 🛡️ 防风控策略
- **智能限流**：默认并发=1，视频间随机延时5-10s
- **分段抓取**：手动分页，每段100条，段间延时2-4s
- **Cookie轮换**：支持多Cookie池，自动切换失效账号
- **本地缓存回退**：失败时使用本地缓存，减少重复请求

### 📦 技术栈
- **后端**：FastAPI + SQLite + APScheduler + yt-dlp
- **前端**：现代化SPA，统一导航和数据流
- **部署**：Docker Compose，访问地址 http://localhost:8080
