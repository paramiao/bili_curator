#!/bin/bash

# bili_curator V7 → V6 回滚脚本
# 使用方法: ./scripts/rollback_to_v6.sh

set -e

echo "🔄 开始从V7回滚到V6..."

# 检查当前版本
if ! docker ps | grep -q bili_curator_v7; then
    echo "❌ 未检测到运行中的V7容器"
    echo "ℹ️  如果V6已在运行，无需回滚"
    exit 1
fi

# 1. 停止V7服务
echo "🛑 停止V7服务..."
docker-compose -f docker-compose.v7.yml down

# 2. 检查V6镜像
echo "🔍 检查V6镜像..."
if ! docker images | grep -q "bili_curator.*v6"; then
    echo "📥 拉取V6镜像..."
    docker pull bili_curator:v6 || {
        echo "❌ V6镜像拉取失败，请检查网络连接"
        exit 1
    }
fi

# 3. 启动V6服务
echo "🚀 启动V6服务..."
docker-compose -f docker-compose.v6.yml up -d

# 4. 等待服务启动
echo "⏳ 等待服务启动..."
sleep 20

# 5. 健康检查
echo "🔍 执行健康检查..."
for i in {1..10}; do
    if curl -f http://localhost:8080/health > /dev/null 2>&1; then
        echo "✅ V6服务启动成功！"
        break
    fi
    if [ $i -eq 10 ]; then
        echo "❌ V6服务启动失败"
        exit 1
    fi
    echo "⏳ 等待服务响应... ($i/10)"
    sleep 5
done

# 6. 验证V6功能
echo "🧪 验证V6功能..."
VERSION_INFO=$(curl -s http://localhost:8080/health | jq -r '.version' 2>/dev/null || echo "unknown")
if [[ "$VERSION_INFO" == v6* ]]; then
    echo "✅ V6版本验证成功: $VERSION_INFO"
else
    echo "⚠️  版本信息异常: $VERSION_INFO"
fi

# 7. 显示回滚结果
echo ""
echo "🎉 回滚完成！"
echo "📊 回滚摘要:"
echo "  - 版本: V7 → V6"
echo "  - Web界面: http://localhost:8080"
echo "  - 功能: 本地下载模式"
echo ""
echo "📝 注意事项:"
echo "  - V7创建的STRM订阅将显示为本地模式"
echo "  - STRM文件不会被删除，可在升级回V7时继续使用"
echo "  - 所有V6功能完全正常"
echo ""
echo "🔄 如需重新升级到V7，请运行: ./scripts/upgrade_to_v7.sh"
