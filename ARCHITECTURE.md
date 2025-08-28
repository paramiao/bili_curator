# bili_curator V7 架构概览

## 🏗️ 整体架构

bili_curator 采用现代化微服务架构，支持 LOCAL 本地下载和 STRM 流媒体双模式。

```
┌─────────────────────────────────────────────────────────────┐
│                    用户访问层                                │
├─────────────────────────────────────────────────────────────┤
│  主SPA应用 (/)           │    功能模块页面 (/static/*)      │
│  • 统一导航界面           │    • 队列管理工具                │
│  • 订阅管理              │    • STRM配置界面                │
│  • 任务监控              │    • 视频检测工具                │
│  • 系统设置              │    • 订阅详情页面                │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    API 服务层                               │
├─────────────────────────────────────────────────────────────┤
│  FastAPI 7.0.0 + 模块化路由                                │
│  • /api/subscriptions    • /api/cache                      │
│  • /api/cookies          • /api/strm                       │
│  • /api/migrations       • /api/data                       │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    业务服务层                               │
├─────────────────────────────────────────────────────────────┤
│  核心服务 (20+ 组件)                                        │
│  • EnhancedDownloader    • DataConsistencyService          │
│  • STRMProxyService      • UnifiedCacheService             │
│  • MetricsService        • RemoteSyncService               │
│  • PendingListService    • SubscriptionStats               │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    数据存储层                               │
├─────────────────────────────────────────────────────────────┤
│  SQLite + Redis (V7)                                       │
│  • 订阅/视频元数据        • 缓存和会话存储                   │
│  • 任务队列状态          • STRM代理缓存                     │
└─────────────────────────────────────────────────────────────┘
```

## 🎯 前端架构

### 主SPA应用
- **入口**: `web/dist/index.html` (216KB 打包文件)
- **路由**: `/` 优先返回统一SPA界面
- **功能**: 6大核心模块的统一导航和管理

### 功能模块页面
- **位置**: `static/*.html` (5个独立页面)
- **用途**: 专项管理工具，SPA的功能补充
- **关系**: 独立访问，非主应用架构组成部分

| 页面 | 用途 | 访问路径 |
|------|------|----------|
| queue_admin.html | 请求队列管理 | /static/queue_admin.html |
| strm_management.html | STRM流媒体配置 | /static/strm_management.html |
| subscription_detail.html | 订阅同步详情 | /static/subscription_detail.html |
| video_detection.html | 视频检测工具 | /static/video_detection.html |
| test.html | 开发测试页面 | /static/test.html |

## 🔧 后端架构

### 核心组件 (49个Python文件)

#### API层 (`app/api_endpoints/`)
- `subscription_management.py` - 订阅管理API
- `strm_management.py` - STRM流媒体API  
- `cache_management.py` - 缓存管理API
- `cookie_management.py` - Cookie管理API
- `data_maintenance.py` - 数据维护API
- `migration_management.py` - 迁移管理API

#### 服务层 (`app/services/`)
**下载服务**:
- `enhanced_downloader.py` - 增强下载器（支持STRM）
- `strm_downloader.py` - STRM专用下载器
- `download_plan_service.py` - 下载计划服务

**STRM服务**:
- `strm_proxy_service.py` - STRM代理服务
- `strm_file_manager.py` - STRM文件管理
- `strm_performance_optimizer.py` - STRM性能优化

**数据服务**:
- `data_consistency_service.py` - 数据一致性检查
- `unified_cache_service.py` - 统一缓存服务
- `metrics_service.py` - 统计指标服务
- `remote_sync_service.py` - 远程同步服务
- `pending_list_service.py` - 待下载列表服务

#### 核心层 (`app/core/`)
- `config.py` - 统一配置管理
- `dependencies.py` - 依赖注入
- `exceptions.py` - 异常定义
- `exception_handlers.py` - 异常处理器

## 📊 双模式支持

### LOCAL模式 (传统下载)
- **存储**: `/downloads` 目录
- **特点**: 完整视频文件本地存储
- **适用**: 离线观看、存档需求

### STRM模式 (流媒体)
- **存储**: `/strm` 目录 (仅.strm文件)
- **特点**: 实时流媒体代理，节省99%存储空间
- **适用**: 在线观看、存储受限环境

### 模式切换
数据模型中 `Subscription.download_mode` 字段控制：
- `'local'` - LOCAL模式 (默认)
- `'strm'` - STRM模式

调度器 (`scheduler.py`) 自动识别并分流到对应处理逻辑。

## 🔄 调度器架构

### 定时任务
- **订阅检查**: 每30分钟检查新视频
- **Cookie验证**: 每6小时验证有效性  
- **任务清理**: 每天凌晨2点清理旧任务
- **僵尸回收**: 每5分钟清理超时任务
- **入队协调**: 每3分钟协调下载队列

### STRM分流逻辑
```python
# 调度器中的STRM检查 (scheduler.py:530-538)
download_mode = getattr(sub, 'download_mode', 'local')
if download_mode == 'strm':
    # STRM模式：使用增强下载器
    await self._process_subscription(sub, db)
    continue
else:
    # LOCAL模式：使用传统下载器
    await downloader.compute_pending_list(sub.id, db)
```

## 📈 性能指标

### 存储效率
- **LOCAL模式**: ~500MB/视频
- **STRM模式**: ~50KB/视频 (99%节省)

### 响应性能
- **API响应**: <200ms
- **流启动**: <2s
- **缓存命中率**: >80%

## 🛠️ 技术栈

### 后端
- **框架**: FastAPI 7.0.0
- **数据库**: SQLite + SQLAlchemy 2.0.32
- **缓存**: Redis 5.0.1 (V7新增)
- **任务调度**: APScheduler 3.10.4
- **视频处理**: yt-dlp 2025.8.20 + FFmpeg

### 前端
- **主应用**: 现代化SPA框架
- **工具页面**: 原生HTML + JavaScript
- **样式**: CSS3 + 响应式设计

### 基础设施
- **部署**: Docker Compose
- **日志**: Loguru 0.7.2
- **监控**: psutil 5.9.8
- **HTTP**: aiohttp 3.9.5 + httpx 0.27.2

## 📁 目录结构

```
bili_curator/
├── web/                     # 主SPA应用
│   ├── dist/index.html     # SPA打包入口 (216KB)
│   └── src/                # SPA源码
├── static/                  # 功能模块页面
│   ├── queue_admin.html    # 队列管理
│   ├── strm_management.html # STRM配置
│   └── *.html              # 其他工具页面
├── app/                     # 后端应用
│   ├── api_endpoints/      # API路由模块
│   ├── services/           # 业务服务层 (20+组件)
│   ├── core/               # 核心配置和异常
│   ├── database/           # 数据库连接
│   └── schemas/            # 数据模型定义
├── downloads/               # LOCAL模式存储
├── strm/                    # STRM模式存储
└── docs/                    # 技术文档
```

## 🚀 部署架构

### Docker容器化
- **单容器部署**: `docker-compose up -d`
- **端口映射**: 8080 (Web) + 8889 (STRM代理)
- **数据持久化**: 卷挂载 `/downloads`、`/strm`、`/data`

### 环境配置
- **配置文件**: `.env` 环境变量
- **数据库**: 自动初始化 SQLite
- **依赖管理**: `requirements.txt` + `requirements-v7.txt`

---

**架构设计原则**: 模块化、可扩展、高性能、易维护
**版本**: V7.0.0 (2025-08-28)
