#!/usr/bin/env python3
"""
最小化STRM同步测试脚本
直接调用增强下载器验证STRM同步流程
"""

import asyncio
import sys
import os
sys.path.insert(0, '/Users/paramiao/development/bili_curator')

from bili_curator.app.database import get_db
from bili_curator.app.models import Subscription
from bili_curator.app.services.enhanced_downloader import EnhancedDownloader
from bili_curator.app.services.strm_proxy_service import STRMProxyService
from bili_curator.app.services.strm_file_manager import STRMFileManager
from bili_curator.app.services.unified_cache_service import UnifiedCacheService
from bili_curator.app.cookie_manager import cookie_manager

async def test_strm_sync():
    """测试STRM同步流程"""
    print("🔍 开始STRM同步测试...")
    
    # 获取数据库会话
    db = next(get_db())
    
    try:
        # 查找STRM订阅
        subscription = db.query(Subscription).filter(
            Subscription.id == 14,
            Subscription.download_mode == 'strm'
        ).first()
        
        if not subscription:
            print("❌ 未找到STRM订阅 (ID: 14)")
            return
            
        print(f"✅ 找到STRM订阅: {subscription.name} (ID: {subscription.id})")
        print(f"   类型: {subscription.type}, 模式: {subscription.download_mode}")
        
        # 初始化STRM服务组件
        print("🔧 初始化STRM服务组件...")
        strm_proxy = STRMProxyService(cookie_manager=cookie_manager)
        strm_file_manager = STRMFileManager()
        cache_service = UnifiedCacheService()
        
        # 创建增强下载器
        enhanced_downloader = EnhancedDownloader(
            strm_proxy, strm_file_manager, cache_service
        )
        print("✅ STRM服务组件初始化完成")
        
        # 执行同步
        print("🚀 开始执行STRM同步...")
        result = await enhanced_downloader.compute_pending_list(subscription, db)
        
        print("✅ STRM同步完成!")
        print(f"📊 同步结果: {result}")
        
        # 检查生成的文件
        print("\n📁 检查生成的STRM文件...")
        import subprocess
        file_count = subprocess.run(
            ["docker", "exec", "bili_curator_v7", "find", "/app/strm", "-type", "f"],
            capture_output=True, text=True
        )
        
        if file_count.returncode == 0:
            files = file_count.stdout.strip().split('\n') if file_count.stdout.strip() else []
            print(f"📄 生成文件数量: {len(files)}")
            for file in files[:10]:  # 显示前10个文件
                print(f"   - {file}")
        else:
            print("❌ 无法检查STRM文件")
            
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(test_strm_sync())
