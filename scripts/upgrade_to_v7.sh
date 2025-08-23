#!/bin/bash

# bili_curator V6 → V7 升级脚本
# 使用方法: ./scripts/upgrade_to_v7.sh

set -e

echo "🚀 开始从V6升级到V7..."

# 检查当前版本
if ! docker ps | grep -q bili_curator_v6; then
    echo "❌ 未检测到运行中的V6容器，请先确保V6正常运行"
    exit 1
fi

# 1. 备份当前数据
BACKUP_DIR="./data_backup_$(date +%Y%m%d_%H%M%S)"
echo "📦 备份当前数据到: $BACKUP_DIR"
docker-compose -f docker-compose.v6.yml down
cp -r ~/bilibili_config "$BACKUP_DIR"
echo "✅ 数据备份完成"

# 2. 检查V7镜像
echo "🔍 检查V7镜像..."
if ! docker images | grep -q "bili_curator.*v7"; then
    echo "📥 拉取V7镜像..."
    docker pull bili_curator:v7 || {
        echo "❌ V7镜像拉取失败，请检查网络连接"
        exit 1
    }
fi

# 3. 创建STRM目录
echo "📁 创建STRM目录..."
STRM_DIR="/Volumes/nas-mk/xiaoya_emby/xiaoya/bilibili_strm"
mkdir -p "$STRM_DIR"
chmod 755 "$STRM_DIR"
echo "✅ STRM目录创建完成: $STRM_DIR"

# 4. 数据库迁移
echo "🗄️ 执行数据库迁移..."
# V7容器启动时会自动执行迁移，添加download_mode字段

# 5. 启动V7服务
echo "🚀 启动V7服务..."
docker-compose -f docker-compose.v7.yml up -d

# 6. 等待服务启动
echo "⏳ 等待服务启动..."
sleep 30

# 7. 健康检查
echo "🔍 执行健康检查..."
for i in {1..10}; do
    if curl -f http://localhost:8080/health > /dev/null 2>&1; then
        echo "✅ V7服务启动成功！"
        break
    fi
    if [ $i -eq 10 ]; then
        echo "❌ V7服务启动失败，开始回滚..."
        docker-compose -f docker-compose.v7.yml down
        docker-compose -f docker-compose.v6.yml up -d
        echo "🔄 已回滚到V6版本"
        exit 1
    fi
    echo "⏳ 等待服务响应... ($i/10)"
    sleep 10
done

# 8. 验证V7功能
echo "🧪 验证V7功能..."
VERSION_INFO=$(curl -s http://localhost:8080/health | jq -r '.version' 2>/dev/null || echo "unknown")
if [[ "$VERSION_INFO" == v7* ]]; then
    echo "✅ V7版本验证成功: $VERSION_INFO"
else
    echo "⚠️  版本信息异常: $VERSION_INFO"
fi

# 9. 显示升级结果
echo ""
echo "🎉 升级完成！"
echo "📊 升级摘要:"
echo "  - 版本: V6 → V7"
echo "  - Web界面: http://localhost:8080"
echo "  - STRM代理: http://localhost:8081"
echo "  - 备份位置: $BACKUP_DIR"
echo ""
echo "📝 后续步骤:"
echo "  1. 访问Web界面验证功能正常"
echo "  2. 创建STRM模式订阅测试流媒体功能"
echo "  3. 配置B站Cookie以支持高清播放"
echo ""
echo "🔄 如需回滚到V6，请运行: ./scripts/rollback_to_v6.sh"
