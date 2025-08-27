#!/bin/bash
# STRM功能Docker环境测试脚本

set -e

echo "🐳 开始Docker环境STRM功能测试"
echo "=================================="

# 检查Docker环境
if ! command -v docker &> /dev/null; then
    echo "❌ Docker未安装或未启动"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose未安装"
    exit 1
fi

# 进入项目目录
cd /Users/paramiao/development/bili_curator

# 设置环境变量
export VERSION=v7
export STRM_HOST_PATH="/tmp/bili_strm_test"
export DOWNLOAD_HOST_PATH="/tmp/bili_downloads_test"
export CONFIG_HOST_PATH="/tmp/bili_config_test"

# 创建测试目录
mkdir -p "$STRM_HOST_PATH"
mkdir -p "$DOWNLOAD_HOST_PATH"
mkdir -p "$CONFIG_HOST_PATH"

echo "📁 测试目录已创建:"
echo "  STRM: $STRM_HOST_PATH"
echo "  Downloads: $DOWNLOAD_HOST_PATH"
echo "  Config: $CONFIG_HOST_PATH"

# 构建Docker镜像
echo "🔨 构建Docker镜像..."
docker-compose build --build-arg VERSION=v7

# 启动容器（后台运行）
echo "🚀 启动Docker容器..."
docker-compose up -d

# 等待服务启动
echo "⏳ 等待服务启动..."
sleep 30

# 检查容器状态
echo "📊 检查容器状态..."
docker-compose ps

# 检查健康状态
echo "🏥 检查服务健康状态..."
for i in {1..10}; do
    if curl -f http://localhost:8080/health &>/dev/null; then
        echo "✅ 服务健康检查通过"
        break
    else
        echo "⏳ 等待服务启动... ($i/10)"
        sleep 5
    fi
    
    if [ $i -eq 10 ]; then
        echo "❌ 服务启动超时"
        docker-compose logs
        exit 1
    fi
done

# 运行STRM修复验证测试
echo "🧪 运行STRM修复验证测试..."
docker-compose exec bili-curator python /app/test_strm_fix.py

# 检查STRM目录结构
echo "📂 检查STRM目录结构..."
if [ -d "$STRM_HOST_PATH" ]; then
    echo "STRM目录内容:"
    find "$STRM_HOST_PATH" -type f -name "*.strm" | head -10
    
    # 检查是否还有"未知UP主"目录
    if [ -d "$STRM_HOST_PATH/未知UP主" ]; then
        echo "⚠️ 发现'未知UP主'目录，可能仍存在问题"
        ls -la "$STRM_HOST_PATH/未知UP主/" | head -5
    else
        echo "✅ 未发现'未知UP主'目录，修复可能生效"
    fi
    
    # 列出UP主目录
    echo "📺 UP主目录列表:"
    ls -la "$STRM_HOST_PATH/" | grep "^d" | head -5
else
    echo "❌ STRM目录不存在"
fi

# 显示容器日志（最后50行）
echo "📋 容器日志（最后50行）:"
docker-compose logs --tail=50

echo "=================================="
echo "🏁 Docker环境测试完成"

# 询问是否清理
read -p "是否清理测试环境？(y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "🧹 清理测试环境..."
    docker-compose down
    rm -rf "$STRM_HOST_PATH" "$DOWNLOAD_HOST_PATH" "$CONFIG_HOST_PATH"
    echo "✅ 清理完成"
else
    echo "💡 测试环境保留，可继续调试"
    echo "停止服务: docker-compose down"
    echo "查看日志: docker-compose logs -f"
fi
