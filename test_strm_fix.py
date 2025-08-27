#!/usr/bin/env python3
"""
STRM功能修复验证脚本
用于在Docker环境中测试STRM目录创建功能
"""

import asyncio
import sys
import os
import logging
from pathlib import Path

# 添加项目路径
sys.path.insert(0, '/app')

from app.database.connection import get_db
from app.models import Subscription
from app.services.enhanced_downloader import EnhancedDownloader
from app.services.strm_proxy_service import STRMProxyService
from app.services.strm_file_manager import STRMFileManager
from app.services.unified_cache_service import UnifiedCacheService

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_strm_directory_creation():
    """测试STRM目录创建功能"""
    logger.info("🧪 开始STRM目录创建功能测试")
    
    try:
        # 初始化服务组件
        cache_service = UnifiedCacheService()
        strm_proxy = STRMProxyService()
        strm_file_manager = STRMFileManager()
        
        # 创建增强下载器
        downloader = EnhancedDownloader(
            strm_proxy=strm_proxy,
            strm_file_manager=strm_file_manager,
            cache_service=cache_service
        )
        
        # 获取数据库会话
        db = next(get_db())
        
        # 查找STRM模式的订阅
        strm_subscriptions = db.query(Subscription).filter(
            Subscription.download_mode == 'strm',
            Subscription.type == 'uploader'
        ).all()
        
        if not strm_subscriptions:
            logger.warning("⚠️ 未找到STRM模式的UP主订阅，创建测试订阅")
            # 创建测试订阅（KrillinAI）
            test_subscription = Subscription(
                name="KrillinAI测试",
                type="uploader",
                uploader_id="1835736645",  # KrillinAI的UP主ID
                download_mode="strm",
                enabled=True
            )
            db.add(test_subscription)
            db.commit()
            strm_subscriptions = [test_subscription]
        
        # 测试每个STRM订阅
        for subscription in strm_subscriptions[:1]:  # 只测试第一个
            logger.info(f"📺 测试订阅: {subscription.name} (ID: {subscription.uploader_id})")
            
            # 获取UP主视频列表
            result = await downloader._get_uploader_videos(subscription, db)
            
            logger.info(f"📊 获取结果统计:")
            logger.info(f"  - 总视频数: {result.get('remote_total', 0)}")
            logger.info(f"  - 有效视频数: {len(result.get('videos', []))}")
            logger.info(f"  - 待处理数: {result.get('pending', 0)}")
            
            # 检查视频元数据质量
            videos = result.get('videos', [])
            if videos:
                logger.info(f"🎬 视频元数据样本:")
                for i, video in enumerate(videos[:3]):  # 显示前3个视频
                    logger.info(f"  {i+1}. 标题: {video.get('title', '无标题')[:50]}")
                    logger.info(f"     UP主: {video.get('uploader', '未知UP主')}")
                    logger.info(f"     BVID: {video.get('bvid', '无ID')}")
                
                # 检查是否还有空标题的视频
                empty_title_count = sum(1 for v in videos if not v.get('title') or not v.get('uploader'))
                if empty_title_count > 0:
                    logger.error(f"❌ 发现 {empty_title_count} 个空标题视频，修复未完全生效")
                    return False
                else:
                    logger.info(f"✅ 所有 {len(videos)} 个视频都有完整的标题和UP主信息")
                
                # 测试STRM文件创建
                logger.info("📁 测试STRM文件创建...")
                test_video = videos[0]
                strm_path = await downloader._create_strm_file_direct(
                    test_video['bvid'],
                    test_video['title'],
                    test_video['uploader']
                )
                
                if strm_path and os.path.exists(strm_path):
                    logger.info(f"✅ STRM文件创建成功: {strm_path}")
                    
                    # 检查目录结构
                    strm_file = Path(strm_path)
                    uploader_dir = strm_file.parent.name
                    logger.info(f"📂 UP主目录: {uploader_dir}")
                    
                    if uploader_dir != "未知UP主":
                        logger.info("✅ STRM目录创建问题已修复！")
                        return True
                    else:
                        logger.error("❌ STRM目录仍然使用默认名称，修复未生效")
                        return False
                else:
                    logger.error("❌ STRM文件创建失败")
                    return False
            else:
                logger.error("❌ 未获取到任何有效视频，可能存在网络或认证问题")
                return False
                
    except Exception as e:
        logger.error(f"❌ 测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if 'db' in locals():
            db.close()

async def main():
    """主函数"""
    logger.info("🚀 启动STRM修复验证测试")
    
    # 检查环境
    strm_path = os.getenv('STRM_PATH', '/app/strm')
    if not os.path.exists(strm_path):
        logger.error(f"❌ STRM目录不存在: {strm_path}")
        return 1
    
    logger.info(f"📁 STRM目录: {strm_path}")
    
    # 运行测试
    success = await test_strm_directory_creation()
    
    if success:
        logger.info("🎉 STRM修复验证测试通过！")
        return 0
    else:
        logger.error("💥 STRM修复验证测试失败！")
        return 1

if __name__ == '__main__':
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
