# bili_curator V6 简化版设计

> 🏠 专为家用个人设计的简单易用版本

## 🎯 设计理念

**简单 > 复杂**  
**易用 > 功能全面**  
**稳定 > 高性能**

## 🚀 核心功能（保留）

### ✅ 必要功能
1. **Docker一键部署** - 简化安装
2. **Web界面管理** - 告别命令行
3. **订阅管理** - 合集/UP主自动更新
4. **Cookie管理** - 避免手动配置
5. **防封禁策略** - 稳定下载
6. **去重检测** - 避免重复下载

### ❌ 去除复杂功能
- ~~PostgreSQL数据库~~ → SQLite本地数据库
- ~~Redis + Celery队列~~ → 简单后台任务
- ~~多服务编排~~ → 单容器部署
- ~~监控系统~~ → 简单日志
- ~~代理池~~ → 可选单代理
- ~~集群部署~~ → 单机运行

## 🏗️ 简化架构

### 技术栈
```
Frontend: Vue.js 3 + Element Plus (更轻量)
Backend:  FastAPI + SQLite
Task:     APScheduler (替代Celery)
Deploy:   单个Docker容器
```

### 架构图
```
┌─────────────────────────────────────┐
│           Docker Container          │
│  ┌─────────────┐  ┌─────────────┐   │
│  │ Web UI      │  │ FastAPI     │   │
│  │ (Vue.js)    │◄─┤ Backend     │   │
│  └─────────────┘  └─────────────┘   │
│         │               │           │
│         ▼               ▼           │
│  ┌─────────────┐  ┌─────────────┐   │
│  │ SQLite DB   │  │ APScheduler │   │
│  │ (本地文件)   │  │ (定时任务)   │   │
│  └─────────────┘  └─────────────┘   │
└─────────────────────────────────────┘
```

## 📁 项目结构

```
bili_curator_v6/
├── Dockerfile              # 单容器配置
├── docker-compose.yml      # 可选，方便挂载目录
├── requirements.txt        # Python依赖
├── main.py                 # FastAPI入口
├── app/
│   ├── models.py          # SQLite数据模型
│   ├── api.py             # API路由
│   ├── scheduler.py       # 定时任务
│   ├── downloader.py      # 下载核心（基于V5）
│   ├── cookie_manager.py  # Cookie管理
│   └── utils.py           # 工具函数
├── web/                   # Vue.js前端
│   ├── src/
│   ├── package.json
│   └── dist/              # 构建后的静态文件
└── data/                  # 数据目录
    ├── bili_curator.db    # SQLite数据库
    ├── cookies.json       # Cookie存储
    └── downloads/         # 下载文件
```

## 🐳 超简单Docker部署

### Dockerfile
> 说明：该简化示例针对 V6 放在 `bili_curator_v6/` 目录内，镜像构建阶段依赖从该目录下的 `requirements.txt` 安装。
> 仓库根目录的 `requirements.txt` 属于历史版本（V4/V5 工具脚本用），不会被 V6 容器构建使用。
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装必要依赖
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# 复制并安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/data/downloads

# 暴露端口
EXPOSE 8080

# 启动命令
CMD ["python", "main.py"]
```

### docker-compose.yml（可选）
```yaml
version: '3.8'

services:
  bili-curator:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data          # 数据持久化
      - ./downloads:/app/downloads # 下载目录
    environment:
      - TZ=Asia/Shanghai
    restart: unless-stopped
```

### 一键启动
```bash
# 方式1：直接Docker
docker run -d -p 8080:8080 -v $(pwd)/downloads:/app/downloads bili-curator

# 方式2：Docker Compose
docker-compose up -d

# 访问Web界面
open http://localhost:8080
```

## 💾 简化数据存储

### SQLite数据库
```python
# models.py
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class Subscription(Base):
    __tablename__ = 'subscriptions'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    type = Column(String(50), nullable=False)  # 'collection', 'uploader', 'keyword'
    url = Column(Text)
    keyword = Column(String(255))
    active = Column(Boolean, default=True)
    last_check = Column(DateTime)
    created_at = Column(DateTime)

class Video(Base):
    __tablename__ = 'videos'
    
    id = Column(Integer, primary_key=True)
    video_id = Column(String(50), unique=True, nullable=False)
    title = Column(Text, nullable=False)
    uploader = Column(String(255))
    file_path = Column(Text)
    downloaded = Column(Boolean, default=False)
    created_at = Column(DateTime)

# 数据库初始化
engine = create_engine('sqlite:///data/bili_curator.db')
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)
```

## 🌐 简洁Web界面

### Vue.js 3 + Element Plus
```vue
<!-- App.vue -->
<template>
  <el-container>
    <el-header>
      <h1>🎬 bili_curator</h1>
    </el-header>
    
    <el-main>
      <el-tabs v-model="activeTab">
        <!-- 仪表板 -->
        <el-tab-pane label="仪表板" name="dashboard">
          <Dashboard />
        </el-tab-pane>
        
        <!-- 订阅管理 -->
        <el-tab-pane label="订阅管理" name="subscriptions">
          <Subscriptions />
        </el-tab-pane>
        
        <!-- 下载管理 -->
        <el-tab-pane label="下载管理" name="downloads">
          <Downloads />
        </el-tab-pane>
        
        <!-- 设置 -->
        <el-tab-pane label="设置" name="settings">
          <Settings />
        </el-tab-pane>
      </el-tabs>
    </el-main>
  </el-container>
</template>
```

### 主要页面
1. **仪表板** - 显示下载统计、最近任务
2. **订阅管理** - 添加/删除订阅，查看状态
3. **下载管理** - 查看下载进度、历史记录
4. **设置** - Cookie管理、下载参数配置

## 🔄 简化任务调度

### APScheduler替代Celery
```python
# scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging

class SimpleScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
        
    def start_subscription_check(self):
        """每30分钟检查订阅更新"""
        self.scheduler.add_job(
            func=self.check_subscriptions,
            trigger=IntervalTrigger(minutes=30),
            id='subscription_check',
            replace_existing=True
        )
        
    def check_subscriptions(self):
        """检查所有订阅的更新"""
        logging.info("开始检查订阅更新...")
        # 实现订阅检查逻辑
        pass
```

## 🍪 简化Cookie管理

### 文件存储Cookie
```python
# cookie_manager.py
import json
import random
from pathlib import Path

class SimpleCookieManager:
    def __init__(self):
        self.cookie_file = Path("data/cookies.json")
        self.cookies = self.load_cookies()
        self.current_index = 0
    
    def load_cookies(self):
        """从文件加载Cookie"""
        if self.cookie_file.exists():
            with open(self.cookie_file, 'r') as f:
                return json.load(f)
        return []
    
    def save_cookies(self):
        """保存Cookie到文件"""
        with open(self.cookie_file, 'w') as f:
            json.dump(self.cookies, f, indent=2)
    
    def get_random_cookie(self):
        """获取随机Cookie"""
        if not self.cookies:
            return None
        return random.choice([c for c in self.cookies if c.get('active', True)])
    
    def add_cookie(self, name, sessdata, bili_jct="", dedeuserid=""):
        """添加新Cookie"""
        cookie = {
            "name": name,
            "sessdata": sessdata,
            "bili_jct": bili_jct,
            "dedeuserid": dedeuserid,
            "active": True,
            "usage_count": 0
        }
        self.cookies.append(cookie)
        self.save_cookies()
```

## 🛡️ 简化防封禁策略

```python
# utils.py
import asyncio
import random
import time

class SimpleRateLimiter:
    def __init__(self):
        self.last_request = 0
        self.min_interval = 5  # 最小5秒间隔
        self.max_interval = 15 # 最大15秒间隔
    
    async def wait(self):
        """智能等待"""
        now = time.time()
        elapsed = now - self.last_request
        
        if elapsed < self.min_interval:
            wait_time = random.uniform(
                self.min_interval - elapsed,
                self.max_interval - elapsed
            )
            await asyncio.sleep(wait_time)
        
        self.last_request = time.time()

# 简单的重试装饰器
def simple_retry(max_retries=3):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise e
                    await asyncio.sleep(random.uniform(10, 30))
            return wrapper
    return decorator
```

## 📋 开发计划（简化版）

### Phase 1: 核心后端 (1-2周)
- [ ] FastAPI + SQLite基础框架
- [ ] 基于V5的下载核心集成
- [ ] 简单Cookie管理
- [ ] APScheduler定时任务

### Phase 2: Web界面 (1-2周)
- [ ] Vue.js 3 + Element Plus界面
- [ ] 订阅管理页面
- [ ] 下载管理页面
- [ ] 设置页面

### Phase 3: Docker化 (1周)
- [ ] Dockerfile编写
- [ ] 一键部署脚本
- [ ] 文档完善

**总计：3-5周完成家用版**

## 🎯 预期效果

V6简化版将实现：
- 🐳 **一键部署**：单个Docker容器，无需复杂配置
- 🌐 **简洁界面**：Vue.js轻量级Web界面
- 🤖 **自动订阅**：后台定时检查更新
- 🍪 **Cookie管理**：Web界面管理，无需手动配置
- 🛡️ **防封禁**：简单有效的限速策略
- 💾 **本地存储**：SQLite文件数据库，数据安全

**完美适合家用个人场景！** 🏠✨

这个简化版本怎么样？保留了核心功能但大大降低了复杂度！
