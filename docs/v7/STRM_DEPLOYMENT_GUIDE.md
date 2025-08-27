# STRM功能部署指南

## 概述

本指南详细说明如何部署和配置bili_curator V7的STRM流媒体功能，包括环境准备、依赖安装、配置设置和验证测试。

## 系统要求

### 硬件要求
- **CPU**: 2核心以上，推荐4核心
- **内存**: 最小2GB，推荐4GB以上
- **存储**: 至少10GB可用空间
- **网络**: 稳定的互联网连接，上行带宽至少10Mbps

### 软件要求
- **操作系统**: Linux (Ubuntu 20.04+), macOS (10.15+), Windows 10+
- **Python**: 3.8或更高版本
- **FFmpeg**: 4.4或更高版本
- **数据库**: SQLite (默认) 或 PostgreSQL

## 环境准备

### 1. 安装Python依赖

```bash
# 进入项目目录
cd bili_curator

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

# 安装依赖
pip install -r bili_curator/requirements.txt
```

### 2. 安装FFmpeg

#### Ubuntu/Debian
```bash
sudo apt update
sudo apt install ffmpeg
```

#### macOS (使用Homebrew)
```bash
brew install ffmpeg
```

#### Windows
1. 下载FFmpeg预编译版本: https://ffmpeg.org/download.html#build-windows
2. 解压到 `C:\ffmpeg`
3. 将 `C:\ffmpeg\bin` 添加到系统PATH环境变量

### 3. 验证FFmpeg安装
```bash
ffmpeg -version
```

## 配置设置

### 1. 基础配置

创建配置文件 `bili_curator/.env`:
```bash
# STRM功能配置
STRM_ENABLED=true
STRM_PATH=/path/to/strm/files
FFMPEG_PATH=/usr/local/bin/ffmpeg

# 数据库配置
DATABASE_URL=sqlite:///./data/bilibili_curator.db

# 日志配置
LOG_LEVEL=INFO
```

### 2. 目录结构设置

```bash
# 创建STRM文件目录
mkdir -p /path/to/strm/files
chmod 755 /path/to/strm/files

# 创建数据目录
mkdir -p bili_curator/data
```

### 3. 媒体服务器集成

#### Plex配置
1. 打开Plex Web界面
2. 添加媒体库 → 选择"电影"或"电视节目"
3. 添加文件夹: `/path/to/strm/files`
4. 高级设置:
   - 启用"本地媒体资产"
   - 启用"使用本地资产命名"

#### Jellyfin配置
1. 打开Jellyfin管理界面
2. 媒体库 → 添加媒体库
3. 内容类型: "电影"或"节目"
4. 文件夹: `/path/to/strm/files`
5. 元数据下载器: 启用"NFO"

## 部署步骤

### 1. 启动服务

```bash
cd bili_curator
python -m bili_curator.main
```

### 2. 验证部署

访问管理界面: http://localhost:8080

检查STRM管理页面: http://localhost:8080/static/strm_management.html

### 3. 环境验证

在STRM管理界面中:
1. 点击"设置"标签
2. 配置FFmpeg路径和STRM目录
3. 点击"🔍 验证环境"
4. 确保所有组件状态为"正常"

## 故障排除

### 常见问题

#### 1. FFmpeg不可用
**症状**: 环境验证显示"FFmpeg不可用"
**解决**: 
- 检查FFmpeg是否正确安装: `ffmpeg -version`
- 确认路径配置正确
- macOS用户可能需要: `brew install ffmpeg`

#### 2. 代理服务异常
**症状**: 无法播放STRM文件
**解决**:
- 检查端口8889是否被占用: `lsof -i :8889`
- 确认防火墙设置允许该端口
- 重启服务

#### 3. 权限问题
**症状**: 无法创建STRM文件
**解决**:
```bash
# 设置正确权限
chmod -R 755 /path/to/strm/files
chown -R $USER:$USER /path/to/strm/files
```

#### 4. 依赖缺失
**症状**: 服务启动失败
**解决**:
```bash
# 重新安装依赖
pip install -r bili_curator/requirements.txt
```

### 日志调试

查看详细日志:
```bash
# 启用调试模式
export LOG_LEVEL=DEBUG
python -m bili_curator.main
```

日志文件位置: `bili_curator/logs/app.log`

## 性能优化

### 1. 系统优化
- 确保足够的内存和CPU资源
- 使用SSD存储提升I/O性能
- 配置合适的网络带宽

### 2. 缓存优化
- 启用Redis缓存(可选)
- 调整缓存过期时间
- 监控缓存命中率

### 3. 并发控制
- 根据硬件配置调整并发数
- 监控系统资源使用情况
- 设置合理的超时时间

## 安全考虑

### 1. 网络安全
- 使用防火墙限制访问
- 考虑使用HTTPS
- 定期更新依赖包

### 2. 数据安全
- 定期备份数据库
- 保护Cookie和认证信息
- 监控异常访问

## 维护建议

### 1. 定期维护
- 清理过期的STRM文件
- 监控磁盘空间使用
- 更新依赖包版本

### 2. 监控指标
- 系统资源使用率
- API响应时间
- 错误日志统计
- 活跃流数量

### 3. 备份策略
- 数据库定期备份
- 配置文件备份
- 重要日志归档

## 配置设置

### 1. 环境变量配置

创建 `.env` 文件：

```bash
cp .env.example .env
```

编辑 `.env` 文件，添加STRM相关配置：

```env
# STRM配置
STRM_ENABLED=true
STRM_PATH=/app/strm
STRM_HLS_SEGMENT_TIME=10
STRM_CACHE_TTL=3600
STRM_MAX_CONCURRENT_STREAMS=10

# 缓存配置
STRM_STREAM_CACHE_SIZE=1000
STRM_HLS_CACHE_SIZE=5000
STRM_METADATA_CACHE_SIZE=2000

# 性能配置
STRM_MEMORY_THRESHOLD=1024
STRM_CPU_THRESHOLD=80
STRM_RESPONSE_TIME_THRESHOLD=2000
STRM_OPTIMIZATION_ENABLED=true
STRM_AUTO_SCALING_ENABLED=false

# TTL配置
STRM_STREAM_TTL=1800
STRM_HLS_TTL=300
STRM_METADATA_TTL=3600
```

### 2. 目录结构创建

```bash
# 创建STRM目录
mkdir -p /app/strm
mkdir -p /app/downloads

# 设置权限
chmod 755 /app/strm
chmod 755 /app/downloads
```

### 3. 数据库初始化

```bash
# 运行数据库迁移
python -m bili_curator.scripts.migrate_database

# 验证数据库
python -c "from bili_curator.app.database.models import *; print('数据库连接成功')"
```

## 部署步骤

### 1. 生产环境部署

#### 使用Docker (推荐)

创建 `docker-compose.yml`:

```yaml
version: '3.8'

services:
  bili-curator:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
      - ./strm:/app/strm
      - ./downloads:/app/downloads
    environment:
      - STRM_ENABLED=true
      - STRM_PATH=/app/strm
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/api/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

部署命令：

```bash
# 构建和启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

#### 直接部署

```bash
# 启动应用
python -m bili_curator.main

# 或使用Gunicorn (生产环境推荐)
pip install gunicorn
gunicorn -w 4 -k uvicorn.workers.UvicornWorker bili_curator.main:app --bind 0.0.0.0:8080
```

### 2. 反向代理配置 (Nginx)

创建 `/etc/nginx/sites-available/bili-curator`:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 主应用（统一 8080 端口）
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    # STRM 接口与 HLS（同 8080）
    location /api/strm/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        
        # HLS特殊配置
        location ~* \.(m3u8|ts)$ {
            proxy_pass http://127.0.0.1:8080;
            add_header Cache-Control "no-cache, no-store, must-revalidate";
            add_header Pragma "no-cache";
            add_header Expires "0";
        }
    }

    # 静态文件
    location /static/ {
        alias /app/bili_curator/static/;
        expires 1d;
        add_header Cache-Control "public, immutable";
    }
}
```

启用配置：

```bash
sudo ln -s /etc/nginx/sites-available/bili-curator /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## 验证部署

### 1. 运行环境验证脚本

```bash
python tests/test_deployment_verification.py
```

### 2. 手动验证步骤

#### 基础功能验证
```bash
# 检查服务状态
curl http://localhost:8080/api/health

# 检查STRM健康状态
curl http://localhost:8080/api/strm/health

# 检查API端点
curl http://localhost:8080/api/strm/stats/streams
curl http://localhost:8080/api/strm/stats/files
```

#### STRM功能验证
1. 访问Web界面: `http://localhost:8080`
2. 创建STRM模式订阅
3. 验证STRM文件生成
4. 测试流媒体播放

### 3. 性能验证

```bash
# 运行性能测试
python tests/test_strm_performance.py

# 检查资源使用
htop
df -h
```

## 监控和维护

### 1. 日志监控

```bash
# 查看应用日志
tail -f logs/bili_curator.log

# 查看STRM日志
tail -f logs/strm.log

# 查看性能日志
tail -f logs/performance.log
```

### 2. 性能监控

访问性能监控面板：
- 系统状态: `http://localhost:8000/api/strm/health`
- 缓存统计: `http://localhost:8000/api/strm/stats/cache`
- 性能指标: `http://localhost:8000/api/strm/stats/performance`

### 3. 定期维护

#### 每日维护
```bash
# 清理过期缓存
curl -X POST http://localhost:8000/api/strm/cache/clear

# 检查磁盘空间
df -h /app/strm

# 检查服务状态
systemctl status bili-curator
```

#### 每周维护
```bash
# 数据库优化
python -m bili_curator.scripts.optimize_database

# 日志轮转
logrotate /etc/logrotate.d/bili-curator

# 性能报告
python -m bili_curator.scripts.generate_performance_report
```

## 故障排除

### 常见问题

#### 1. FFmpeg未找到
```bash
# 检查FFmpeg路径
which ffmpeg

# 添加到PATH
export PATH=$PATH:/usr/local/bin
```

#### 2. 端口冲突
```bash
# 检查端口占用
netstat -tlnp | grep :8080
```

#### 3. 权限问题
```bash
# 设置目录权限
sudo chown -R www-data:www-data /app/strm
sudo chmod -R 755 /app/strm
```

#### 4. 内存不足
```bash
# 检查内存使用
free -h

# 调整缓存大小
STRM_STREAM_CACHE_SIZE=500
STRM_HLS_CACHE_SIZE=2000
```

#### 5. 网络连接问题
```bash
# 测试B站连接
curl -I https://www.bilibili.com

# 检查DNS解析
nslookup www.bilibili.com

# 测试代理连接
curl -x your-proxy:port https://www.bilibili.com
```

### 日志分析

#### 错误日志位置
- 应用日志: `logs/bili_curator.log`
- STRM日志: `logs/strm_proxy.log`
- 系统日志: `/var/log/syslog`

#### 常见错误模式
```bash
# 查找FFmpeg错误
grep "FFmpeg" logs/strm_proxy.log

# 查找网络错误
grep "Connection" logs/bili_curator.log

# 查找缓存错误
grep "Cache" logs/performance.log
```

## 性能优化

### 1. 系统级优化

```bash
# 增加文件描述符限制
echo "* soft nofile 65536" >> /etc/security/limits.conf
echo "* hard nofile 65536" >> /etc/security/limits.conf

# 优化网络参数
echo "net.core.rmem_max = 16777216" >> /etc/sysctl.conf
echo "net.core.wmem_max = 16777216" >> /etc/sysctl.conf
sysctl -p
```

### 2. 应用级优化

```env
# 增加缓存大小
STRM_STREAM_CACHE_SIZE=2000
STRM_HLS_CACHE_SIZE=10000

# 调整TTL
STRM_STREAM_TTL=3600
STRM_HLS_TTL=600

# 启用优化
STRM_OPTIMIZATION_ENABLED=true
STRM_AUTO_SCALING_ENABLED=true
```

### 3. 数据库优化

```sql
-- 创建索引
CREATE INDEX idx_videos_bilibili_id ON videos(bilibili_id);
CREATE INDEX idx_videos_subscription_id ON videos(subscription_id);
CREATE INDEX idx_subscriptions_download_mode ON subscriptions(download_mode);

-- 定期清理
DELETE FROM videos WHERE created_at < datetime('now', '-30 days') AND downloaded = 0;
```

## 安全配置

### 1. 访问控制

```nginx
# 限制STRM API访问
location /api/strm/ {
    allow 192.168.1.0/24;
    allow 10.0.0.0/8;
    deny all;
    
    proxy_pass http://127.0.0.1:8888;
}
```

### 2. 防火墙配置

```bash
# UFW配置
sudo ufw allow 22
sudo ufw allow 80
sudo ufw allow 443
sudo ufw allow from 192.168.1.0/24 to any port 8000
sudo ufw enable
```

### 3. SSL/TLS配置

```bash
# 使用Let's Encrypt
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## 备份和恢复

### 1. 数据备份

```bash
#!/bin/bash
# backup.sh

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backup/bili_curator_$DATE"

mkdir -p $BACKUP_DIR

# 备份数据库
cp /app/data/bilibili_curator.db $BACKUP_DIR/

# 备份配置
cp .env $BACKUP_DIR/

# 备份STRM文件
tar -czf $BACKUP_DIR/strm_files.tar.gz /app/strm/

# 清理旧备份 (保留7天)
find /backup -name "bili_curator_*" -mtime +7 -exec rm -rf {} \;
```

### 2. 数据恢复

```bash
#!/bin/bash
# restore.sh

BACKUP_DIR=$1

if [ -z "$BACKUP_DIR" ]; then
    echo "用法: $0 <备份目录>"
    exit 1
fi

# 停止服务
systemctl stop bili-curator

# 恢复数据库
cp $BACKUP_DIR/bilibili_curator.db /app/data/

# 恢复配置
cp $BACKUP_DIR/.env ./

# 恢复STRM文件
tar -xzf $BACKUP_DIR/strm_files.tar.gz -C /

# 启动服务
systemctl start bili-curator
```

## 升级指南

### 1. 版本升级

```bash
# 备份当前版本
./backup.sh

# 拉取新版本
git pull origin main

# 更新依赖
pip install -r bili_curator/requirements.txt

# 运行迁移
python -m bili_curator.scripts.migrate_database

# 重启服务
systemctl restart bili-curator
```

### 2. 配置迁移

```bash
# 检查配置变更
python -m bili_curator.scripts.check_config_changes

# 更新配置文件
python -m bili_curator.scripts.update_config
```

## 联系支持

如果遇到部署问题，请：

1. 查看故障排除章节
2. 检查日志文件
3. 运行诊断脚本
4. 提交Issue到GitHub仓库

---

**注意**: 本指南假设用户具备基本的Linux系统管理知识。如需更详细的帮助，请参考相关文档或联系技术支持。
