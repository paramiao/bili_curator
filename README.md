# bili_curator - B站合集智能下载器

> 🚀 专业的B站合集视频下载和管理工具，支持智能增量下载、Emby媒体库集成

## 🔥 V6 近期更新（服务化/订阅管理）

V6 在保留 V5 本地增量下载能力的基础上，引入 Web 后端与订阅管理，新增自动导入与目录内去重等能力，前后端口径统一。

### ✅ 新增/变更点（后端与数据一致性）
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

# 启动某订阅下载（示例：ID=1）
curl -s -X POST http://localhost:8080/api/subscriptions/1/download

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
- 若点击“开始下载”秒完成：请先升级到包含“目录内去重”修复的版本并重启容器。

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

## 🛠️ Legacy CLI（V4/V5）
详见 `docs/LEGACY_CLI.md`。

## ⚙️ 配置选项

### 命令行参数
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--naming` | `title` | 文件命名策略：`title`(标题) 或 `id`(视频ID) |
| `--max-videos` | 无限制 | 最大下载视频数量 |
| `--max-workers` | `3` | 并发下载线程数 |
| `--quality` | `best[height<=1080]` | 视频质量选择 |
| `--cookies` | 无 | Cookie字符串（支持会员内容） |
| `--collection-name` | 自动获取 | 自定义合集目录名 |

### Cookie配置
```bash
# 从浏览器获取SESSDATA
--cookies "SESSDATA=your_sessdata_value"

# 完整Cookie字符串
--cookies "SESSDATA=xxx; bili_jct=yyy; DedeUserID=zzz"
```

## 📝 NFO文件增强

V5版本生成的NFO文件包含丰富元数据：
- ✅ 详细视频描述
- ✅ 上传者信息
- ✅ 上传日期和时长
- ✅ 观看数和点赞数
- ✅ 视频标签
- ✅ 流媒体信息
- ✅ 唯一标识符

## 🔧 故障排除

### 常见问题

**Q: 下载失败怎么办？**
A: 重新运行相同命令，系统会自动检测并重新下载失败的文件。

**Q: 如何更新已下载的合集？**
A: 直接运行相同的下载命令，系统会自动下载新增的视频。

**Q: Cookie过期怎么办？**
A: 从浏览器重新获取SESSDATA，更新命令中的`--cookies`参数。

**Q: 文件命名有问题？**
A: 使用`--naming id`切换到ID命名模式，或手动重命名后重新运行。

### 日志查看
```bash
# 查看详细下载日志
tail -f /output/dir/download_v5.log
```

## 🆚 版本对比

| 特性 | V4版本 | V5版本 |
|------|--------|--------|
| 增量下载 | 基于JSON配置文件 | 基于实际文件扫描 |
| 文件命名 | 不一致 | 统一规范 |
| NFO质量 | 基础信息 | 丰富元数据 |
| 并发支持 | 无 | 多线程 |
| 错误处理 | 基础 | 智能重试 |
| 完整性检查 | 无 | 自动验证 |

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
