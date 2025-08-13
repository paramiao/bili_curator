# bili_curator V6 架构设计

> 🚀 从命令行工具到企业级Web应用的全面升级

## 🎯 V6版本核心目标

### 1. **容器化部署**
- Docker容器化，一键部署
- 支持Docker Compose多服务编排
- 自动启动和健康检查

### 2. **Web任务管理**
- 现代化Web界面
- 实时任务进度追踪
- 任务队列管理

### 3. **智能订阅系统**
- 合集地址订阅
- UP主动态监控
- 关键词搜索订阅

### 4. **Cookie管理与防封禁**
- Cookie池管理与轮换
- 智能防封禁策略
- 自动重试与恢复

### 5. **内容管理与去重**
- 已有视频数据库
- 下载前智能比对
- 重复内容检测

## 🏗️ 系统架构设计

### 技术栈选择
```
Frontend:  React + TypeScript + Ant Design
Backend:   FastAPI + Python 3.11
Database:  PostgreSQL + Redis
Queue:     Celery + Redis
Container: Docker + Docker Compose
Monitor:   Prometheus + Grafana (可选)
```

### 服务架构
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Web Frontend  │    │   API Gateway   │    │   Task Queue    │
│   (React SPA)   │◄──►│   (FastAPI)     │◄──►│   (Celery)      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                │                        │
                                ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   PostgreSQL    │    │     Redis       │    │  File Storage   │
│   (主数据库)     │    │  (缓存+队列)     │    │  (视频文件)     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## 📊 数据库设计

### 核心表结构
```sql
-- 订阅管理
CREATE TABLE subscriptions (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL, -- 'collection', 'uploader', 'keyword'
    url TEXT,
    uploader_id VARCHAR(100),
    keyword TEXT,
    status VARCHAR(50) DEFAULT 'active',
    last_check TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Cookie管理
CREATE TABLE cookies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    sessdata TEXT NOT NULL,
    bili_jct TEXT,
    dedeuserid TEXT,
    status VARCHAR(50) DEFAULT 'active', -- active, expired, banned
    last_used TIMESTAMP,
    usage_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 任务管理
CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    subscription_id INTEGER REFERENCES subscriptions(id),
    task_type VARCHAR(50) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    progress INTEGER DEFAULT 0,
    total_videos INTEGER DEFAULT 0,
    downloaded_videos INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 视频内容管理
CREATE TABLE videos (
    id SERIAL PRIMARY KEY,
    video_id VARCHAR(50) UNIQUE NOT NULL, -- BV号或av号
    title TEXT NOT NULL,
    uploader VARCHAR(255),
    duration INTEGER,
    upload_date DATE,
    description TEXT,
    tags TEXT[],
    file_path TEXT,
    file_size BIGINT,
    download_status VARCHAR(50) DEFAULT 'pending',
    subscription_id INTEGER REFERENCES subscriptions(id),
    created_at TIMESTAMP DEFAULT NOW()
);

-- 下载历史
CREATE TABLE download_history (
    id SERIAL PRIMARY KEY,
    video_id VARCHAR(50) REFERENCES videos(video_id),
    task_id INTEGER REFERENCES tasks(id),
    status VARCHAR(50),
    error_message TEXT,
    download_time TIMESTAMP DEFAULT NOW()
);
```

## 🐳 Docker容器化设计

### Dockerfile
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建非root用户
RUN useradd -m -u 1000 bili && chown -R bili:bili /app
USER bili

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker-compose.yml
```yaml
version: '3.8'

services:
  web:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://bili:password@db:5432/bili_curator
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis
    volumes:
      - ./downloads:/app/downloads
    restart: unless-stopped

  worker:
    build: .
    command: celery -A app.celery worker --loglevel=info
    environment:
      - DATABASE_URL=postgresql://bili:password@db:5432/bili_curator
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis
    volumes:
      - ./downloads:/app/downloads
    restart: unless-stopped

  scheduler:
    build: .
    command: celery -A app.celery beat --loglevel=info
    environment:
      - DATABASE_URL=postgresql://bili:password@db:5432/bili_curator
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis
    restart: unless-stopped

  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=bili_curator
      - POSTGRES_USER=bili
      - POSTGRES_PASSWORD=password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    restart: unless-stopped

volumes:
  postgres_data:
```

## 🌐 Web界面设计

### 主要页面
1. **仪表板** - 任务概览、系统状态
2. **订阅管理** - 添加/编辑/删除订阅
3. **任务管理** - 任务列表、进度追踪
4. **内容管理** - 已下载视频浏览
5. **Cookie管理** - Cookie池管理
6. **系统设置** - 下载策略配置

### React组件结构
```
src/
├── components/
│   ├── Dashboard/
│   ├── Subscriptions/
│   ├── Tasks/
│   ├── Videos/
│   ├── Cookies/
│   └── Settings/
├── services/
│   ├── api.ts
│   ├── websocket.ts
│   └── types.ts
├── hooks/
├── utils/
└── App.tsx
```

## 🔐 Cookie管理系统

### Cookie池设计
```python
class CookieManager:
    def __init__(self):
        self.cookie_pool = []
        self.current_index = 0
        self.banned_cookies = set()
    
    async def get_available_cookie(self):
        """获取可用的Cookie"""
        for _ in range(len(self.cookie_pool)):
            cookie = self.cookie_pool[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.cookie_pool)
            
            if await self.validate_cookie(cookie):
                await self.update_usage(cookie.id)
                return cookie
        
        raise NoCookieAvailableError("没有可用的Cookie")
    
    async def mark_cookie_banned(self, cookie_id: int):
        """标记Cookie为被封禁"""
        await self.update_cookie_status(cookie_id, 'banned')
        self.banned_cookies.add(cookie_id)
    
    async def validate_cookie(self, cookie) -> bool:
        """验证Cookie是否有效"""
        # 实现Cookie验证逻辑
        pass
```

### Cookie轮换策略
- **时间轮换**：每个Cookie使用一定时间后自动切换
- **请求计数**：达到一定请求数后切换
- **错误触发**：遇到403/429错误时立即切换
- **健康检查**：定期验证Cookie有效性

## 🛡️ 防封禁策略

### 1. **请求频率控制**
```python
class RateLimiter:
    def __init__(self):
        self.min_interval = 3  # 最小请求间隔(秒)
        self.max_interval = 10 # 最大请求间隔(秒)
        self.last_request_time = 0
    
    async def wait_if_needed(self):
        """智能等待，避免请求过于频繁"""
        current_time = time.time()
        elapsed = current_time - self.last_request_time
        
        if elapsed < self.min_interval:
            wait_time = random.uniform(
                self.min_interval - elapsed,
                self.max_interval - elapsed
            )
            await asyncio.sleep(wait_time)
        
        self.last_request_time = time.time()
```

### 2. **User-Agent轮换**
```python
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    # ... 更多User-Agent
]
```

### 3. **IP代理支持**
```python
class ProxyManager:
    def __init__(self):
        self.proxy_pool = []
        self.current_proxy = 0
    
    def get_next_proxy(self):
        """获取下一个代理"""
        if not self.proxy_pool:
            return None
        
        proxy = self.proxy_pool[self.current_proxy]
        self.current_proxy = (self.current_proxy + 1) % len(self.proxy_pool)
        return proxy
```

### 4. **智能重试机制**
```python
async def download_with_retry(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            # 尝试下载
            result = await download_video(url)
            return result
        except BannedError:
            # Cookie被封，切换Cookie
            await cookie_manager.switch_cookie()
        except RateLimitError:
            # 触发限流，增加等待时间
            wait_time = (2 ** attempt) * 60  # 指数退避
            await asyncio.sleep(wait_time)
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            await asyncio.sleep(random.uniform(5, 15))
```

## 📅 定时任务设计

### Celery定时任务
```python
from celery import Celery
from celery.schedules import crontab

app = Celery('bili_curator')

# 定时检查订阅更新
@app.task
def check_subscriptions():
    """检查所有活跃订阅的更新"""
    pass

# 定时清理过期任务
@app.task
def cleanup_old_tasks():
    """清理超过30天的已完成任务"""
    pass

# 定时验证Cookie
@app.task
def validate_cookies():
    """验证所有Cookie的有效性"""
    pass

# 定时任务调度
app.conf.beat_schedule = {
    'check-subscriptions': {
        'task': 'check_subscriptions',
        'schedule': crontab(minute='*/30'),  # 每30分钟检查一次
    },
    'cleanup-tasks': {
        'task': 'cleanup_old_tasks',
        'schedule': crontab(hour=2, minute=0),  # 每天凌晨2点清理
    },
    'validate-cookies': {
        'task': 'validate_cookies',
        'schedule': crontab(hour='*/6'),  # 每6小时验证一次
    },
}
```

## 🔄 订阅管理系统

### 订阅类型
1. **合集订阅**：监控指定合集的新增视频
2. **UP主订阅**：监控UP主的最新投稿
3. **关键词订阅**：基于关键词搜索新视频

### 订阅处理流程
```python
class SubscriptionProcessor:
    async def process_collection_subscription(self, subscription):
        """处理合集订阅"""
        # 获取合集最新视频列表
        # 与数据库中已有视频比对
        # 创建下载任务
        pass
    
    async def process_uploader_subscription(self, subscription):
        """处理UP主订阅"""
        # 获取UP主最新投稿
        # 过滤已下载视频
        # 创建下载任务
        pass
    
    async def process_keyword_subscription(self, subscription):
        """处理关键词订阅"""
        # 基于关键词搜索
        # 过滤重复和不相关内容
        # 创建下载任务
        pass
```

## 📈 监控与日志

### 系统监控指标
- 任务执行成功率
- Cookie使用情况
- 下载速度统计
- 存储空间使用
- 系统资源占用

### 日志管理
```python
import logging
from logging.handlers import RotatingFileHandler

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('logs/bili_curator.log', maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)
```

## 🚀 部署与运维

### 一键部署脚本
```bash
#!/bin/bash
# deploy.sh

echo "🚀 部署bili_curator V6..."

# 创建必要目录
mkdir -p downloads logs

# 启动服务
docker-compose up -d

# 等待服务启动
sleep 10

# 初始化数据库
docker-compose exec web python -m alembic upgrade head

# 创建默认管理员用户
docker-compose exec web python -m scripts.create_admin

echo "✅ 部署完成！"
echo "🌐 Web界面: http://localhost:8000"
```

### 健康检查
```python
@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "6.0.0",
        "services": {
            "database": await check_database(),
            "redis": await check_redis(),
            "storage": await check_storage()
        }
    }
```

## 📋 开发计划

### Phase 1: 基础架构 (2-3周)
- [ ] Docker容器化配置
- [ ] 数据库设计与迁移
- [ ] FastAPI后端框架搭建
- [ ] React前端框架搭建

### Phase 2: 核心功能 (3-4周)
- [ ] 订阅管理系统
- [ ] 任务队列与执行器
- [ ] Cookie管理与轮换
- [ ] 防封禁策略实现

### Phase 3: Web界面 (2-3周)
- [ ] 仪表板开发
- [ ] 订阅管理界面
- [ ] 任务监控界面
- [ ] 内容管理界面

### Phase 4: 优化与测试 (1-2周)
- [ ] 性能优化
- [ ] 安全加固
- [ ] 全面测试
- [ ] 文档完善

## 🎯 预期效果

V6版本将实现：
- 🐳 **一键部署**：Docker容器化，简化部署流程
- 🌐 **Web管理**：现代化界面，直观易用
- 🤖 **自动化**：订阅监控，定时更新
- 🛡️ **稳定性**：防封禁策略，高可用性
- 📊 **可观测**：完整监控，问题追踪

这将是一个企业级的B站内容管理平台！🚀
