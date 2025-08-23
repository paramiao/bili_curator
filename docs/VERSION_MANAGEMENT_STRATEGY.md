# bili_curator版本管理和发布策略

## 版本定位策略

### 当前版本架构
```
bili_curator_v6 (当前稳定版)
├── 核心功能完整
├── 本地下载模式
├── 订阅管理
├── Cookie管理
├── 任务调度
└── Web界面

bili_curator_v7 (STRM扩展版)
├── 继承V6所有功能
├── 新增STRM流媒体模式
├── 代理服务架构
├── 双模式支持
└── 增强的UI界面
```

### 版本号规范

采用语义化版本控制 (Semantic Versioning)：`MAJOR.MINOR.PATCH`

```
V6.x.x - 当前稳定版本线
├── V6.0.0 - 当前生产版本
├── V6.0.1 - Bug修复版本
├── V6.1.0 - 功能增强版本
└── V6.x.x - 后续维护版本

V7.x.x - STRM扩展版本线
├── V7.0.0-alpha.1 - 第一个Alpha版本
├── V7.0.0-beta.1  - 第一个Beta版本
├── V7.0.0-rc.1    - 第一个RC版本
├── V7.0.0         - 正式发布版本
└── V7.x.x         - 后续功能版本
```

## Docker版本发布机制

### 1. 镜像标签策略

```bash
# 主版本标签
bili_curator:v6        # V6最新稳定版（自动更新）
bili_curator:v7        # V7最新稳定版（自动更新）

# 精确版本标签
bili_curator:v6.0.0    # 具体版本，永不变更
bili_curator:v6.0.1    # Bug修复版本
bili_curator:v6.1.0    # 功能增强版本

# 预发布版本标签
bili_curator:v7.0.0-alpha.1  # Alpha版本
bili_curator:v7.0.0-beta.1   # Beta版本
bili_curator:v7.0.0-rc.1     # Release Candidate

# 特殊标签
bili_curator:latest    # 指向最新稳定版（当前为v6）
bili_curator:stable    # 指向最新稳定版（当前为v6）
bili_curator:dev       # 开发版本（基于main分支）
```

### 2. 多架构支持

```bash
# 支持多架构镜像
bili_curator:v6.0.0-amd64    # x86_64架构
bili_curator:v6.0.0-arm64    # ARM64架构
bili_curator:v6.0.0          # 多架构manifest
```

### 3. Docker构建配置

```dockerfile
# Dockerfile.v6 (当前稳定版)
FROM python:3.11-slim
LABEL version="6.0.0"
LABEL description="bili_curator stable version with local download support"
# ... 现有构建逻辑

# Dockerfile.v7 (STRM扩展版)
FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg  # STRM功能需要
LABEL version="7.0.0"
LABEL description="bili_curator with STRM streaming support"
# ... V7构建逻辑
```

## 项目目录结构调整

### 建议的目录重组

```
bili_curator/
├── README.md
├── CHANGELOG.md
├── VERSION
├── docker-compose.yml          # 默认使用stable版本
├── docker-compose.v6.yml       # V6专用配置
├── docker-compose.v7.yml       # V7专用配置
├── docs/
│   ├── v6/                     # V6版本文档
│   │   ├── README.md
│   │   ├── DEPLOYMENT.md
│   │   └── API_REFERENCE.md
│   ├── v7/                     # V7版本文档
│   │   ├── README.md
│   │   ├── STRM_GUIDE.md
│   │   ├── MIGRATION_FROM_V6.md
│   │   └── API_REFERENCE.md
│   └── VERSION_HISTORY.md
├── src/
│   ├── v6/                     # V6源代码（当前bili_curator_v6/）
│   │   ├── app/
│   │   ├── main.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── v7/                     # V7源代码（基于V6扩展）
│       ├── app/
│       ├── main.py
│       ├── Dockerfile
│       └── requirements.txt
└── scripts/
    ├── build.sh                # 构建脚本
    ├── deploy.sh               # 部署脚本
    └── migrate.sh              # 迁移脚本
```

## Docker Compose配置策略

### 1. 默认配置 (docker-compose.yml)
```yaml
version: '3.8'
services:
  bili-curator:
    image: bili_curator:stable  # 默认使用稳定版
    container_name: bili_curator
    ports:
      - "8080:8080"
    volumes:
      - ./downloads:/app/downloads
      - ./data:/app/data
    environment:
      - VERSION=v6.0.0
    restart: unless-stopped
```

### 2. V6专用配置 (docker-compose.v6.yml)
```yaml
version: '3.8'
services:
  bili-curator:
    image: bili_curator:v6
    container_name: bili_curator_v6
    ports:
      - "8080:8080"
    volumes:
      - ./downloads:/app/downloads
      - ./data:/app/data
    environment:
      - VERSION=v6.0.0
      - DOWNLOAD_PATH=/app/downloads
    restart: unless-stopped
```

### 3. V7专用配置 (docker-compose.v7.yml)
```yaml
version: '3.8'
services:
  bili-curator:
    image: bili_curator:v7
    container_name: bili_curator_v7
    ports:
      - "8080:8080"
      - "8081:8081"  # STRM代理端口
    volumes:
      - ./downloads:/app/downloads
      - ./strm:/app/strm  # STRM文件目录
      - ./data:/app/data
    environment:
      - VERSION=v7.0.0
      - DOWNLOAD_PATH=/app/downloads
      - STRM_PATH=/app/strm
      - STRM_PROXY_PORT=8081
    restart: unless-stopped
```

## 版本升级和回退策略

### 1. 升级流程

```bash
# 从V6升级到V7
./scripts/upgrade_to_v7.sh

# 升级脚本内容示例
#!/bin/bash
echo "开始从V6升级到V7..."

# 1. 备份当前数据
docker-compose down
cp -r ./data ./data_backup_$(date +%Y%m%d_%H%M%S)

# 2. 拉取V7镜像
docker pull bili_curator:v7

# 3. 使用V7配置启动
docker-compose -f docker-compose.v7.yml up -d

# 4. 验证升级成功
./scripts/health_check.sh

echo "升级完成！"
```

### 2. 回退流程

```bash
# 回退到V6
./scripts/rollback_to_v6.sh

# 回退脚本内容示例
#!/bin/bash
echo "开始回退到V6..."

# 1. 停止V7服务
docker-compose -f docker-compose.v7.yml down

# 2. 恢复V6配置
docker-compose -f docker-compose.v6.yml up -d

# 3. 验证回退成功
./scripts/health_check.sh

echo "回退完成！"
```

### 3. 数据迁移策略

```python
# scripts/migrate_v6_to_v7.py
"""V6到V7的数据迁移脚本"""

def migrate_database():
    """迁移数据库结构"""
    # 添加download_mode字段
    # 设置默认值为'local'
    pass

def migrate_config():
    """迁移配置文件"""
    # 添加STRM相关配置
    pass

def verify_migration():
    """验证迁移结果"""
    # 检查数据完整性
    pass
```

## CI/CD流水线设计

### 1. GitHub Actions配置

```yaml
# .github/workflows/build-and-release.yml
name: Build and Release

on:
  push:
    tags:
      - 'v*'
  pull_request:
    branches: [ main, develop ]

jobs:
  build-v6:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Build V6 Image
        run: |
          docker build -f src/v6/Dockerfile -t bili_curator:v6-${{ github.sha }} src/v6/
          
  build-v7:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Build V7 Image
        run: |
          docker build -f src/v7/Dockerfile -t bili_curator:v7-${{ github.sha }} src/v7/
          
  release:
    needs: [build-v6, build-v7]
    runs-on: ubuntu-latest
    if: startsWith(github.ref, 'refs/tags/')
    steps:
      - name: Push to Registry
        run: |
          # 推送到Docker Hub或私有仓库
          docker push bili_curator:${{ github.ref_name }}
```

### 2. 自动化测试

```yaml
# .github/workflows/test.yml
name: Test

on: [push, pull_request]

jobs:
  test-v6:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Test V6
        run: |
          cd src/v6
          python -m pytest tests/
          
  test-v7:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Test V7
        run: |
          cd src/v7
          python -m pytest tests/
```

## 版本发布流程

### 1. 开发流程

```
feature/strm-support → develop → release/v7.0.0 → main → tag v7.0.0
```

### 2. 发布检查清单

- [ ] 代码审查通过
- [ ] 所有测试用例通过
- [ ] 文档更新完成
- [ ] 迁移脚本测试通过
- [ ] Docker镜像构建成功
- [ ] 安全扫描通过
- [ ] 性能测试通过

### 3. 发布命令

```bash
# 发布V7.0.0版本
git tag -a v7.0.0 -m "Release v7.0.0: Add STRM streaming support"
git push origin v7.0.0

# 触发CI/CD构建和发布
# 自动构建Docker镜像并推送到仓库
```

## 用户升级指南

### 1. 升级前准备

```bash
# 1. 备份数据
cp -r ~/bilibili_config ~/bilibili_config_backup

# 2. 记录当前版本
docker inspect bili_curator_v6 | grep version

# 3. 停止当前服务
docker-compose down
```

### 2. 升级到V7

```bash
# 1. 下载V7配置文件
wget https://raw.githubusercontent.com/user/bili_curator/main/docker-compose.v7.yml

# 2. 启动V7服务
docker-compose -f docker-compose.v7.yml up -d

# 3. 验证升级
curl http://localhost:8080/health
```

### 3. 回退到V6（如需要）

```bash
# 1. 停止V7服务
docker-compose -f docker-compose.v7.yml down

# 2. 启动V6服务
docker-compose -f docker-compose.v6.yml up -d
```

## 监控和告警

### 1. 版本监控

```python
# 版本健康检查
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": os.getenv("VERSION", "unknown"),
        "features": ["local_download", "strm_streaming"] if is_v7() else ["local_download"]
    }
```

### 2. 升级监控

```bash
# 监控脚本
#!/bin/bash
# monitor_upgrade.sh

VERSION=$(curl -s http://localhost:8080/health | jq -r '.version')
echo "当前运行版本: $VERSION"

if [ "$VERSION" != "v7.0.0" ]; then
    echo "警告: 版本不匹配"
    # 发送告警
fi
```

---

**文档版本**：v1.0  
**创建日期**：2025-08-23  
**适用版本**：V6 → V7迁移
