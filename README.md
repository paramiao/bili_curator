# bili_curator V6 - B站视频下载管理系统

> 🚀 专为家用个人设计的 B站视频下载与管理平台，支持现代化 Web 界面、智能订阅管理、Docker 一键部署

## 🆕 V6 最新特性

- **🌐 现代化 Web 管理界面**：统一的单页应用（SPA），支持实时监控、可视化统计、直观操作
- **📊 智能统计与监控**：目录统计、订阅统计、系统监控三位一体，数据一致性自动校验
- **🔄 增强订阅管理**：支持合集、UP主、关键词订阅，自动增量更新，智能去重
- **🛠️ 数据维护工具**：一致性检查、容量回填、同步缓存管理，确保数据准确性
- **🍪 Cookie 池管理**：Web 界面管理多账号，自动轮换，支持会员内容下载
- **📦 Docker 一键部署**：单容器部署，零配置启动，内置健康检查
- **🔧 数据一致性优化**：远端总数缓存自动刷新，失败视频可视化管理，数据准确性保障

## ✨ V6 核心特性

- 🌐 **Web 管理界面**：告别命令行，现代化 Web 操作
- 📦 **Docker 一键部署**：单容器部署，最小化配置
- 🔄 **智能订阅管理**：合集自动监控，增量更新
- 🎯 **精准去重检测**：基于文件系统和数据库的智能去重
- 🍪 **Cookie 池管理**：Web 界面管理，自动轮换
- 📊 **实时任务监控**：任务队列、进度追踪、状态管理
- 🎬 **Emby/Jellyfin 集成**：自动生成 NFO 元数据文件

## 🚀 快速开始

使用一键脚本 `scripts/manage.sh`：

```bash
# 克隆项目
git clone https://github.com/paramiao/bili_curator.git
cd bili_curator

# 首次赋权
chmod +x scripts/manage.sh

# 启动服务（自动构建）
./scripts/manage.sh up

# 访问 Web 界面
open http://localhost:8080
```

### Docker Compose 部署

```bash
# 进入 V6 目录
cd bili_curator_v6

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 健康检查
./scripts/manage.sh health
```

## 🌐 Web 界面功能

### 统一管理界面
访问地址：`http://localhost:8080`（统一单页应用入口）

### 核心功能模块
- **📈 总览仪表板**：系统状态、媒体统计、订阅概览、目录分析
- **📺 订阅管理**：添加/编辑订阅、状态监控、手动触发同步、失败视频清理
- **⏳ 任务队列**：实时任务监控、进度追踪、错误处理
- **🍪 Cookie 管理**：多账号管理、有效性验证、自动轮换
- **⚙️ 系统设置**：下载配置、同步状态、数据维护工具
- **📊 系统监控**：健康状态、任务执行、队列状态、资源使用
- **🔧 数据维护**：一致性检查、缓存刷新、数据修复工具

### API 使用示例

```bash
# 系统健康检查
curl -s http://localhost:8080/health | jq .

# 查看系统总览
curl -s http://localhost:8080/api/overview | jq .

# 订阅管理
curl -s http://localhost:8080/api/subscriptions | jq .

# 目录统计
curl -s http://localhost:8080/api/media/directories | jq .

# 订阅统计
curl -s http://localhost:8080/api/media/subscription-stats | jq .

# 一致性检查
curl -s http://localhost:8080/api/system/consistency-stats | jq .

# 容量数据回填
curl -s -X POST http://localhost:8080/api/media/refresh-sizes | jq .

# 数据维护API端点
curl -s -X POST http://localhost:8080/api/maintenance/check-consistency | jq .

# 刷新远端总数缓存
curl -s -X POST http://localhost:8080/api/maintenance/refresh-remote-totals | jq .

# 清理失败视频记录
curl -s -X POST http://localhost:8080/api/subscriptions/1/clear-failed | jq .
```


### 支持的订阅类型
- ✅ **合集订阅**：完整支持，自动监控更新，智能增量下载
- ✅ **UP主订阅**：支持UP主全部视频订阅，定期检查更新
- ✅ **关键词订阅**：基于搜索关键词的视频订阅
- ✅ **特定视频订阅**：指定视频URL列表的批量下载

## 🔧 使用说明

### 1. 添加订阅
```
1. 访问 http://localhost:8080
2. 进入"订阅管理"页面
3. 点击"添加订阅"
4. 输入 B站合集 URL
5. 系统自动解析并创建订阅
```

### 2. Cookie 配置（可选）
```
1. 进入"Cookie 管理"页面
2. 添加 B站 SESSDATA Cookie
3. 系统自动验证并启用
4. 支持高质量视频下载和会员内容
```

### 3. 监控下载
```
1. "总览"页面查看整体状态
2. "任务队列"页面监控实时进度
3. 支持手动暂停/恢复/取消任务
```

## 📋 V6 技术特性
- __订阅解析与命名统一__：`parse-collection` 优先使用 yt-dlp 的“合集层级”元数据（`--flat-playlist --dump-single-json`），自动生成订阅名（`uploader + playlist_title`），并做目录安全清洗，确保与下载目录一致。
- __目录内关联与去重__：
  - `auto_import.py` 仅按“订阅下载目录”进行视频匹配，避免跨合集误判；
  - `EnhancedTaskManager` 下载前查重改为传入当前订阅目录，避免“任务秒完成但未下载”的问题。
- __自动导入与统计刷新__：
  - `POST /api/auto-import/scan` 扫描 `/app/downloads` 下产物(JSON/视频)并入库；
  - `POST /api/auto-import/associate` 将入库视频按目录规则自动关联到订阅并刷新统计；
  - `POST /api/subscriptions/{id}/associate` 针对单个订阅进行关联与统计刷新。
- __远端总数独立__：新增 `GET /api/subscriptions/{id}/expected-total`，本地统计(`total/downloaded/pending`)与远端总数(`expected_total`)分离，前端可分别展示与刷新。
- __统一字段__：全链路统一使用 `is_active`、补齐 `updated_at`、仅使用 `bilibili_id`。
- __Cookie 兼容性__：修复 `cookies.txt` Netscape 头部问题，提升 yt-dlp 解析稳定性。

### 🖥️ 前端“总览”与统计改动（V6 合并）
- __导航合并__：将“仪表盘”和“视频管理”合并为“总览”，同页展示系统状态、媒体总览、订阅统计、目录统计/明细。
- __远端总数本地缓存__：前端对 `expected-total` 结果做 1 小时 `localStorage` 缓存；仅在点击“获取/刷新”时请求远端，避免频繁触发风控。
- __“待下载”口径统一__：优先显示“远端总数 − 已下载”，失败或未获取时回退“本地总计 − 已下载”，并做非负裁剪；界面标注来源（远端/本地）。
- __错误提示友好化__：当接口返回 4xx/5xx 或非 JSON 时，前端以明确的错误条提示，不再长时间停留在“加载中…”。

### 🛡️ 风控友好策略（V6 新增）
- __分段抓取列表__：下载器与 `expected-total` 统计均采用手动分页，`--playlist-items` 每段 100，段间随机延时 2–4s，减少一次性拉取 999 条造成的风控命中。
- __下载节流__：默认并发=1，单个视频间随机延时 5–10s，平滑请求节奏（家用 NAS 建议保持）。
- __统一请求参数__：yt-dlp 调用统一 UA/Referer/重试次数/轻量睡眠，减少不同链路间差异。
- __Cookie 最小化__：支持仅 SESSDATA 的 Cookie 传入，通过 `--cookies` 统一传给 yt-dlp。
- __本地缓存回退__：获取到的列表写入 `playlist.json`，实时拉取失败时回退使用缓存，避免重复触发远端风控。
- __统计与下载一致__：`GET /api/subscriptions/{id}/expected-total` 与下载端同样使用分段统计，确保口径一致。

### 🚀 快速操作（Docker 部署）

推荐使用一键脚本：`scripts/manage.sh`（见 `docs/DEPLOY_WITH_DOCKER.md`）

```bash
# 首次赋权
chmod +x scripts/manage.sh

# 启动/重启（自动构建）
./scripts/manage.sh up

# 查看日志
./scripts/manage.sh logs

# 健康检查
./scripts/manage.sh health
```

也可直接使用 compose：
```bash
# 启动/重启服务
docker compose -f bili_curator_v6/docker-compose.yml up -d

# 健康检查
curl -s http://localhost:8080/health

# 扫描本地已下载产物并入库
curl -s -X POST http://localhost:8080/api/auto-import/scan

# 自动将本地视频关联到订阅并刷新统计（按订阅目录匹配）
curl -s -X POST http://localhost:8080/api/auto-import/associate

# 针对单个订阅执行关联与统计刷新（示例：ID=1）
curl -s -X POST http://localhost:8080/api/subscriptions/1/associate

# 刷新并查看远端总数（与本地统计口径独立）
curl -s http://localhost:8080/api/subscriptions/1/expected-total

# 查看订阅与其任务
curl -s http://localhost:8080/api/subscriptions | jq .
curl -s http://localhost:8080/api/subscriptions/1/tasks | jq .

# 查看容器日志（注意指定 compose 文件）
docker compose -f bili_curator_v6/docker-compose.yml logs -f

# —— 媒体总览/统计相关 API ——
# 媒体总览（可触发磁盘扫描）
curl -s "http://localhost:8080/api/media/overview?scan=true" | jq .

# 订阅聚合统计（总数/已下载/容量/最近上传）
curl -s http://localhost:8080/api/media/subscription-stats | jq .

# 目录聚合统计（下载根目录一级子目录）
curl -s http://localhost:8080/api/media/directories | jq .

# 查看某目录下的视频明细（分页）
curl -s "http://localhost:8080/api/media/directory-videos?dir=%2Fapp%2Fdownloads%2F某目录&page=1&size=20" | jq .
```

### 🗝️ Cookie 管理 API（启停与最小化）
- 列出/创建/启停：
  - `GET /api/cookies`
  - `POST /api/cookies`（创建）
  - `PATCH /api/cookies/{id}`（更新 `is_active`/字段）
- 建议：优先使用“仅 SESSDATA”，后端会生成 Netscape 格式并经 `--cookies` 注入 yt-dlp。

示例（禁用某个 Cookie）：
```bash
curl -s -X PATCH http://localhost:8080/api/cookies/1 \
  -H 'Content-Type: application/json' \
  -d '{"is_active": false}'
```

### 🧭 使用建议（家用场景）
- 并发下载：建议设为 1，减少 NAS 负载。
- 定时检查：每 6–12 小时检查一次，如有新增再下载。
- 遇到统计不一致：先执行“扫描 + 自动关联”，再看订阅统计。
- 启停下载：通过更新订阅 `is_active` 字段控制（启用=自动入队下载；暂停=停止入队），例如：

```bash
curl -s -X PATCH http://localhost:8080/api/subscriptions/1 \
  -H 'Content-Type: application/json' \
  -d '{"is_active": true}'
```

### 💾 运行时存储与迁移
- 数据库路径：环境变量 `DB_PATH`（默认 `/app/data/bili_curator.db`）。应用启动时会自动创建数据目录。
- 轻量迁移：启动时自动补齐旧库缺失列（如 `download_tasks.video_id/bilibili_id`），并将旧 `video_id` 迁移到 `bilibili_id`；需重启容器使迁移生效。

### 📜 playlist.json 缓存与回退
- 位置：订阅目录下的 `playlist.json`。
- 写入：成功获取到合集列表后写入；下一次下载优先使用实时获取，失败时回退到缓存。
- 覆盖策略：同名直接覆盖为最新；建议偶发风控时减少刷新频率。
- 风险：缓存可能过期，必要时先调用 `GET /api/subscriptions/{id}/expected-total` 以刷新。

### 🧩 解析端与 Cookies 一致性
- `POST /api/subscriptions/parse-collection` 已与下载/统计端对齐，统一通过 `--cookies` 注入（不再使用 `--add-header`）。

## ✨ 核心特性

### 🧠 智能增量下载
- **基于实际文件检测**：扫描目录中的JSON文件，从中读取真实视频ID
- **完整性自动验证**：检查文件大小和完整性，自动修复损坏下载
- **零配置增量**：无需维护下载列表，基于现有文件智能跳过

### 📁 智能文件管理
- **视频标题命名**：默认使用清晰的视频标题作为文件名
- **ID存储在JSON**：视频ID安全存储在info.json文件中
- **统一命名规则**：自动处理特殊字符，确保系统兼容性

### 🎬 完美媒体库支持
- **增强NFO文件**：丰富的元数据，完美支持Emby/Jellyfin
- **标准文件结构**：符合媒体服务器最佳实践
- **自动缩略图**：下载视频封面图片

### ⚡ 高效下载策略
- **并发/节流（V6 服务模式）**：默认并发=1，视频间 5–10s 随机延时；可按需调整，但建议在风控窗口内保持保守配置。
- **智能格式回退**：自动尝试最佳视频格式
- **Cookie认证**：支持会员专享内容下载

> 注：V5 命令行模式仍保留多线程配置；V6 服务模式为风控友好与稳定优先，默认收敛并发与请求频率。

## 🚀 快速开始

### 环境准备
```bash
# 激活Python虚拟环境
source ~/.pydev/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 基本使用
```bash
# 下载合集（推荐用法）
python legacy/v5/bilibili_collection_downloader_v5.py \
  "https://space.bilibili.com/351754674/lists/2416048?type=season" \
  "/Volumes/nas-mk/" \
  --cookies "SESSDATA=your_sessdata_here"
```

### 高级配置
```bash
# 完整配置示例
python legacy/v5/bilibili_collection_downloader_v5.py \
  "collection_url" "/output/dir" \
  --cookies "SESSDATA=your_sessdata" \
  --naming title \
  --max-videos 50 \
  --max-workers 3 \
  --collection-name "自定义合集名"
```

## 📁 文件结构

下载完成后的文件结构：
```
/output/合集·乔布斯合集/
├── 经典时刻：乔布斯发布初代iPhone.mp4          # 视频文件
├── 经典时刻：乔布斯发布初代iPhone.info.json    # 元数据（包含视频ID）
├── 经典时刻：乔布斯发布初代iPhone.nfo          # Emby元数据
├── 经典时刻：乔布斯发布初代iPhone.jpg          # 缩略图
├── 乔布斯2005斯坦福演讲.mp4                    # 另一个视频
├── 乔布斯2005斯坦福演讲.info.json              # 对应元数据
├── video_details.json                          # 合集视频列表
└── download_v5.log                             # 下载日志
```

## 🔄 智能增量下载原理

### 工作流程
1. **扫描现有文件**：读取目录中所有`.info.json`文件
2. **提取视频ID**：从JSON文件中获取真实的视频ID
3. **完整性检查**：验证对应的mp4文件是否存在且完整
4. **智能跳过**：已完整下载的视频自动跳过
5. **增量下载**：只下载缺失或损坏的视频

### 支持的场景
- ✅ **首次下载**：下载合集中的所有视频
- ✅ **增量更新**：合集新增视频时，只下载新视频
- ✅ **修复下载**：自动检测并重新下载损坏的文件
- ✅ **任意文件名**：支持重命名后的文件（基于JSON内容识别）

## 🏗️ 技术架构

### 核心组件
- **FastAPI 后端**：RESTful API，WebSocket 实时通信
- **SQLite 数据库**：轻量级本地存储，无需外部依赖
- **APScheduler 调度**：定时任务，自动检查订阅更新
- **yt-dlp 下载引擎**：稳定的视频下载核心
- **内置任务队列**：轻量级队列管理，支持并发控制

### 防风控策略
- **智能限流**：默认并发=1，视频间随机延时 5-10s
- **分段抓取**：手动分页，每段 100 条，段间延时 2-4s
- **Cookie 轮换**：支持多 Cookie 池，自动切换失效账号
- **超时控制**：全链路 yt-dlp 子进程超时与强制终止
- **本地缓存**：失败时回退本地缓存，减少重复请求

## 📚 相关文档

### 架构与技术
- [V6 架构设计](V6_ARCHITECTURE_DESIGN.md) - 当前版本完整架构说明
- [后端实现详解](docs/BACKEND_IMPLEMENTATION.md) - 模块设计、队列、Cookie管理
- [前端实现详解](docs/FRONTEND_IMPLEMENTATION.md) - Web界面与交互设计
- [部署与运维](docs/DEPLOY_WITH_DOCKER.md) - Docker部署、一键脚本使用

### 问题与规划
- [已知问题](docs/KNOWN_ISSUES.md) - 常见问题与解决方案
- [开发路线图](docs/ROADMAP_V6.md) - 功能规划与优先级

### 历史文档
- [Legacy CLI（V4/V5）](docs/LEGACY_CLI.md) - 历史命令行工具使用
- [历史文档归档](docs/legacy/) - 过时的设计文档与需求

## 🤝 贡献

欢迎提交Issue和Pull Request！

## 📄 许可证

MIT License

---

**bili_curator** - 让B站合集下载变得简单而智能 🎯
