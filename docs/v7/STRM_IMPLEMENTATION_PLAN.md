# bili_curator V7 STRM扩展实现方案

## 概述

本文档描述了bili_curator从V6升级到V7版本的STRM（流媒体）支持扩展方案。V7版本在保持V6所有功能的基础上，新增STRM模式，允许用户订阅B站内容时仅生成轻量级的流媒体文件，而非下载完整视频，实现按需播放。

## 版本定位

- **V6版本**：当前稳定版本，专注本地下载功能
- **V7版本**：STRM扩展版本，支持本地下载 + 流媒体双模式

## 核心设计理念

### 目标
- **轻量级订阅**：订阅时仅抓取元数据，不下载视频文件
- **按需播放**：通过代理服务实时获取播放链接
- **存储优化**：相比本地下载节省99%存储空间
- **无缝集成**：与现有LOCAL模式完全兼容

### 技术架构
```
订阅扫描 → 元数据存储 → .strm文件生成 → Emby扫描 → 用户播放 → 代理服务 → 实时HLS转换 → 播放器
```

## 数据模型扩展

### 1. 枚举类型定义
```python
# 在models.py中添加
from enum import Enum

class DownloadMode(str, Enum):
    LOCAL = "local"  # 本地下载模式
    STRM = "strm"    # 流媒体模式
```

### 2. Subscription模型扩展
```python
# 在Subscription类中添加字段
download_mode = Column(Enum(DownloadMode), default=DownloadMode.LOCAL, nullable=False)
```

### 3. 数据库迁移
```python
# 在_migrate_schema方法中添加
if not has_column('subscriptions', 'download_mode'):
    conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN download_mode VARCHAR(10) DEFAULT 'local'")
```

## 环境变量配置

### docker-compose.yml扩展
```yaml
environment:
  # 现有配置...
  - DOWNLOAD_PATH=/app/downloads          # 本地视频目录
  - STRM_PATH=/app/strm                   # STRM文件目录
  - STRM_PROXY_PORT=8081                  # 代理服务端口
  - STRM_DEFAULT_QUALITY=720p             # 默认播放清晰度
  - STRM_CACHE_TTL=300                    # 链接缓存时间（秒）
  - BILIBILI_SESSDATA=                    # B站登录Cookie
  - BILIBILI_BILI_JCT=                    # CSRF Token
  - BILIBILI_BUVID3=                      # 设备标识
```

### 卷挂载扩展
```yaml
volumes:
  - /path/to/strm:/app/strm               # STRM文件目录
  - /path/to/downloads:/app/downloads     # 本地下载目录（现有）
```

## 代理服务架构

### 1. 服务模块：`app/services/strm_proxy_service.py`
```python
class BilibiliStreamProxy:
    def __init__(self):
        self.cache = {}  # 内存缓存
        self.cache_ttl = int(os.getenv('STRM_CACHE_TTL', '300'))
        self.active_streams = {}
        
    async def get_stream_url(self, bvid: str) -> str:
        """获取HLS流地址"""
        # 1. 缓存检查
        cache_key = f"stream:{bvid}"
        if cache_key in self.cache:
            cached_data = self.cache[cache_key]
            if datetime.now() < cached_data['expires']:
                return cached_data['url']
        
        # 2. 实时解析B站流
        stream_info = await self._parse_bilibili_stream(bvid)
        hls_url = await self._convert_to_hls(stream_info)
        
        # 3. 缓存结果
        self.cache[cache_key] = {
            'url': hls_url,
            'expires': datetime.now() + timedelta(seconds=self.cache_ttl)
        }
        
        return hls_url
    
    async def _parse_bilibili_stream(self, bvid: str):
        """使用现有cookie_manager解析B站流"""
        # 复用现有的yt-dlp调用逻辑
        # 集成cookie轮换和重试机制
        pass
    
    async def _convert_to_hls(self, stream_info):
        """FFmpeg转换DASH为HLS"""
        # ffmpeg -i video.m4s -i audio.m4s -c copy -f hls -hls_time 6 -hls_list_size 0 pipe:1
        pass
```

### 2. API路由扩展
```python
# 在api.py中添加
@app.get("/api/v1/stream/{bvid}")
async def stream_video(bvid: str):
    """STRM代理播放接口"""
    if not BilibiliDownloaderV6._is_bvid(bvid):
        raise HTTPException(400, "Invalid BVID")
    
    try:
        hls_url = await strm_proxy_service.get_stream_url(bvid)
        return StreamingResponse(
            proxy_stream(hls_url),
            media_type="application/vnd.apple.mpegurl"
        )
    except Exception as e:
        raise HTTPException(500, f"Stream error: {e}")
```

## 下载器STRM分支逻辑

### 1. 路径管理统一
```python
# 在downloader.py中扩展
def get_subscription_base_path(self, subscription: Subscription) -> str:
    """根据订阅模式返回基础路径"""
    if subscription.download_mode == DownloadMode.STRM:
        return Path(os.getenv('STRM_PATH', '/app/strm'))
    return self.output_dir  # 现有的DOWNLOAD_PATH

def _create_subscription_directory(self, subscription: Subscription) -> str:
    """扩展现有方法支持STRM模式"""
    base_path = self.get_subscription_base_path(subscription)
    subscription_dir = base_path / self._sanitize_dirname(subscription.name)
    subscription_dir.mkdir(parents=True, exist_ok=True)
    return str(subscription_dir)
```

### 2. 下载分支逻辑
```python
async def _download_single_video(self, video_info: Dict[str, Any], subscription_id: int, db: Session) -> Dict[str, Any]:
    subscription = db.query(Subscription).filter_by(id=subscription_id).first()
    
    if subscription.download_mode == DownloadMode.STRM:
        return await self._create_strm_entry(video_info, subscription, db)
    else:
        # 现有的本地下载逻辑保持不变
        return await self._download_video_file(video_info, subscription, db)
```

### 3. STRM文件生成
```python
async def _create_strm_entry(self, video_info: Dict[str, Any], subscription: Subscription, db: Session):
    """STRM模式：创建.strm文件和元数据"""
    bvid = video_info.get('bilibili_id') or video_info.get('id')
    title = video_info.get('title', 'Unknown')
    
    subscription_dir = Path(self._create_subscription_directory(subscription))
    safe_title = self._sanitize_filename(title)
    
    # 1. 下载缩略图（小文件）
    thumbnail_path = None
    if video_info.get('thumbnail'):
        thumbnail_path = await self._download_thumbnail_only(
            video_info['thumbnail'], 
            subscription_dir / f"{safe_title}.jpg"
        )
    
    # 2. 生成.strm文件
    strm_content = f"http://localhost:{os.getenv('STRM_PROXY_PORT', '8081')}/api/v1/stream/{bvid}"
    strm_path = subscription_dir / f"{safe_title}.strm"
    with open(strm_path, 'w', encoding='utf-8') as f:
        f.write(strm_content)
    
    # 3. 生成.nfo文件
    nfo_content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
    <title>{html.escape(title)}</title>
    <plot>{html.escape(video_info.get('description', ''))}</plot>
    <premiered>{video_info.get('upload_date', '')}</premiered>
    <studio>{html.escape(video_info.get('uploader', ''))}</studio>
    <director>{html.escape(video_info.get('uploader', ''))}</director>
    <genre>Bilibili</genre>
    <genre>{subscription.type}</genre>
    <runtime>{video_info.get('duration', 0)}</runtime>
    <thumb>{thumbnail_path or ''}</thumb>
</movie>"""
    
    nfo_path = subscription_dir / f"{safe_title}.nfo"
    with open(nfo_path, 'w', encoding='utf-8') as f:
        f.write(nfo_content)
    
    # 4. 创建Video记录
    video_record = Video(
        bilibili_id=bvid,
        title=title,
        uploader=video_info.get('uploader'),
        uploader_id=video_info.get('uploader_id'),
        duration=video_info.get('duration', 0),
        upload_date=video_info.get('upload_date'),
        description=video_info.get('description'),
        video_path=str(strm_path),  # 指向.strm文件
        thumbnail_path=thumbnail_path,
        downloaded=True,  # STRM文件已生成
        downloaded_at=datetime.now(),
        subscription_id=subscription.id
    )
    
    db.add(video_record)
    return {"status": "strm_created", "path": str(strm_path)}
```

## 文件组织结构

### LOCAL模式（现有）
```
/downloads/
├── UP主-张三/
│   ├── 视频标题1.mp4
│   ├── 视频标题1.info.json
│   └── 视频标题1.jpg
└── 合集-Python教程/
    ├── 第1课.mp4
    ├── 第1课.info.json
    └── 第1课.jpg
```

### STRM模式（新增）
```
/strm/
├── UP主-张三/
│   ├── 视频标题1.strm      # 内容: http://localhost:8081/api/v1/stream/BV1234567890
│   ├── 视频标题1.nfo       # XML元数据
│   └── 视频标题1.jpg       # 缩略图
└── 合集-Python教程/
    ├── 第1课.strm
    ├── 第1课.nfo
    └── 第1课.jpg
```

## API接口扩展

### 1. 订阅创建接口扩展
```python
class SubscriptionCreate(BaseModel):
    name: str
    type: str
    url: Optional[str] = None
    uploader_id: Optional[str] = None
    keyword: Optional[str] = None
    download_mode: Optional[str] = "local"  # 新增字段

@app.post("/api/subscriptions")
async def create_subscription(subscription: SubscriptionCreate, db: Session = Depends(get_db)):
    # 验证download_mode
    if subscription.download_mode not in ["local", "strm"]:
        raise HTTPException(400, "Invalid download_mode")
    
    new_subscription = Subscription(
        name=subscription.name,
        type=subscription.type,
        url=subscription.url,
        uploader_id=subscription.uploader_id,
        keyword=subscription.keyword,
        download_mode=DownloadMode(subscription.download_mode)  # 新增
    )
    # 现有创建逻辑...
```

### 2. STRM状态查询接口
```python
@app.get("/api/strm/status")
async def get_strm_status():
    """获取STRM服务状态"""
    return {
        "proxy_running": strm_proxy_service.is_running(),
        "cache_size": len(strm_proxy_service.cache),
        "active_streams": strm_proxy_service.get_active_count(),
        "cache_hit_rate": strm_proxy_service.get_cache_hit_rate()
    }

@app.get("/api/strm/cache")
async def get_strm_cache():
    """获取缓存状态"""
    return {
        "entries": len(strm_proxy_service.cache),
        "memory_usage": strm_proxy_service.get_cache_memory_usage(),
        "hit_rate": strm_proxy_service.get_cache_hit_rate()
    }
```

## 前端UI支持

### 1. 订阅创建界面
- **模式选择**：单选按钮组
  - 本地下载：完整视频文件，占用存储空间大
  - 在线流媒体：轻量级文件，按需播放
- **存储预估**：显示两种模式的存储占用对比
- **路径预览**：显示文件存储位置

### 2. 订阅列表界面
- **模式标识**：图标区分（📁 LOCAL / 📺 STRM）
- **存储统计**：
  - LOCAL：显示实际文件大小
  - STRM：显示元数据文件大小
- **状态显示**：STRM订阅显示代理服务状态

### 3. 系统状态页面
- **STRM服务监控**：代理服务运行状态
- **缓存统计**：命中率、内存使用
- **活跃流数量**：当前播放的视频数

## 实施计划

### Phase 1: 核心基础设施（2-3天）
**Milestone 1.1: 数据模型扩展**
- [x] 设计DownloadMode枚举和数据库字段
- [ ] 实现数据库迁移逻辑
- [ ] 测试现有数据兼容性

**Milestone 1.2: 路径管理统一**
- [ ] 实现路径管理函数
- [ ] 更新环境变量配置
- [ ] 测试目录创建逻辑

### Phase 2: STRM核心功能（3-4天）
**Milestone 2.1: 代理服务实现**
- [ ] 创建代理服务模块
- [ ] 实现B站流解析和缓存
- [ ] 集成FFmpeg转换

**Milestone 2.2: 下载器STRM分支**
- [ ] 实现STRM文件生成逻辑
- [ ] 扩展下载器分支判断
- [ ] 测试元数据文件生成

### Phase 3: API和UI集成（2-3天）
**Milestone 3.1: API接口扩展**
- [ ] 扩展订阅创建API
- [ ] 实现流媒体代理路由
- [ ] 添加状态查询接口

**Milestone 3.2: 前端界面支持**
- [ ] 订阅创建页面模式选择
- [ ] 订阅列表模式标识
- [ ] 系统状态监控页面

### Phase 4: 测试和优化（2天）
**Milestone 4.1: 集成测试**
- [ ] 端到端功能测试
- [ ] 性能和并发测试
- [ ] 兼容性测试

**Milestone 4.2: 生产就绪**
- [ ] 日志和监控完善
- [ ] 配置文档更新
- [ ] 部署和升级指南

## 性能指标

### 存储优化
- **LOCAL模式**：每视频约500MB
- **STRM模式**：每视频约50KB（缩略图+元数据）
- **节省比例**：99.99%

### 播放性能
- **首次播放**：3-5秒启动时间
- **缓存命中**：<1秒响应时间
- **并发支持**：10个同时播放流

## 风险控制

### 技术风险
1. **B站反爬虫**：复用现有cookie轮换机制
2. **FFmpeg依赖**：容器预装，提供降级方案
3. **网络稳定性**：实现重试和降级机制

### 兼容性风险
1. **数据库迁移**：充分测试，提供回滚
2. **现有功能**：严格隔离，零影响
3. **媒体服务器**：标准HLS格式，广泛兼容

### 运维风险
1. **配置复杂度**：提供默认配置
2. **监控告警**：关键指标监控
3. **故障恢复**：自动重启和健康检查

## 成功标准

1. **功能完整性**：用户可选择订阅模式，STRM正常播放
2. **性能指标**：存储节省>99%，播放启动<5秒
3. **稳定性**：7x24小时运行无重大故障
4. **用户体验**：界面友好，操作简单，文档清晰

## 后续扩展

### 短期优化
- Redis缓存替代内存缓存
- 多清晰度自适应播放
- 播放统计和分析

### 长期规划
- 支持其他视频平台
- 智能预缓存机制
- 分布式代理服务

---

**文档版本**：v1.0  
**最后更新**：2025-08-23  
**维护者**：bili_curator开发团队
