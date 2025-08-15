#!/usr/bin/env python3
"""
V6简单测试脚本 - 不依赖Docker直接测试
"""
import sys
import os
import asyncio
from pathlib import Path

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """测试所有模块导入"""
    print("🧪 测试模块导入...")
    
    try:
        # 测试基础模块
        from app.models import db, Subscription, Video, Cookie, Settings
        print("✅ 数据库模型导入成功")
        
        from app.cookie_manager import cookie_manager, rate_limiter
        print("✅ Cookie管理器导入成功")
        
        from app.scheduler import scheduler, task_manager
        print("✅ 任务调度器导入成功")
        
        from app.downloader import downloader
        print("✅ 下载器导入成功")
        
        from app.api import app
        print("✅ FastAPI应用导入成功")
        
        return True
        
    except Exception as e:
        print(f"❌ 模块导入失败: {e}")
        return False

def test_database():
    """测试数据库初始化"""
    print("\n🗄️ 测试数据库...")
    
    try:
        from app.models import db, get_db
        
        # 测试数据库连接
        session = next(get_db())
        session.close()
        print("✅ 数据库连接成功")
        
        return True
        
    except Exception as e:
        print(f"❌ 数据库测试失败: {e}")
        return False

def test_api_creation():
    """测试API应用创建"""
    print("\n🌐 测试API应用...")
    
    try:
        from app.api import app
        
        # 检查应用是否正确创建
        if app and hasattr(app, 'routes'):
            print(f"✅ FastAPI应用创建成功，包含 {len(app.routes)} 个路由")
            return True
        else:
            print("❌ FastAPI应用创建失败")
            return False
            
    except Exception as e:
        print(f"❌ API应用测试失败: {e}")
        return False

async def test_scheduler():
    """测试调度器"""
    print("\n⏰ 测试任务调度器...")
    
    try:
        from app.scheduler import scheduler
        
        # 测试调度器启动
        scheduler.start()
        print("✅ 调度器启动成功")
        
        # 获取任务列表
        jobs = scheduler.get_jobs()
        print(f"✅ 发现 {len(jobs)} 个定时任务")
        
        # 停止调度器
        scheduler.stop()
        print("✅ 调度器停止成功")
        
        return True
        
    except Exception as e:
        print(f"❌ 调度器测试失败: {e}")
        return False

def main():
    """主测试函数"""
    print("🚀 bili_curator V6 测试开始\n")
    
    # 创建必要目录
    os.makedirs("data", exist_ok=True)
    os.makedirs("data/downloads", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    # 运行测试
    tests = [
        ("模块导入", test_imports),
        ("数据库", test_database),
        ("API应用", test_api_creation),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        try:
            if test_func():
                passed += 1
        except Exception as e:
            print(f"❌ {test_name}测试异常: {e}")
    
    # 异步测试
    try:
        if asyncio.run(test_scheduler()):
            passed += 1
        total += 1
    except Exception as e:
        print(f"❌ 调度器测试异常: {e}")
        total += 1
    
    print(f"\n📊 测试结果: {passed}/{total} 通过")
    
    if passed == total:
        print("🎉 所有测试通过！V6核心功能正常")
        print("\n🌐 可以尝试启动Web服务:")
        print("   python main.py")
    else:
        print("⚠️ 部分测试失败，需要修复问题")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
