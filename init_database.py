#!/usr/bin/env python3
"""
初始化数据库脚本
创建所有必要的表结构
"""

import sys
import os
sys.path.insert(0, '/Users/paramiao/development/bili_curator/bili_curator')

from sqlalchemy import create_engine
from app.models import Base
from app.core.config import get_config

def init_database():
    """初始化数据库"""
    print("🗄️ 初始化数据库")
    print("=" * 50)
    
    try:
        config = get_config()
        db_url = config.get_database_url()
        print(f"📍 数据库路径: {config.database.db_path}")
        
        # 创建数据库引擎
        engine = create_engine(db_url, echo=True)
        
        # 创建所有表
        print("🔨 创建数据库表...")
        Base.metadata.create_all(bind=engine)
        
        print("✅ 数据库初始化完成")
        
        # 验证表创建
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        print(f"📋 已创建的表: {', '.join(tables)}")
        
        return True
        
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = init_database()
    sys.exit(0 if success else 1)
