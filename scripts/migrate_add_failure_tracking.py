#!/usr/bin/env python3
"""
数据库迁移脚本：为Video表添加失败跟踪字段
"""
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'bili_curator_v6'))

from sqlalchemy import text
from app.database import get_db_session

def migrate_add_failure_tracking():
    """添加失败跟踪字段到Video表"""
    
    # 要添加的字段定义
    migrations = [
        "ALTER TABLE videos ADD COLUMN download_failed BOOLEAN DEFAULT FALSE",
        "ALTER TABLE videos ADD COLUMN failure_reason VARCHAR(255)",
        "ALTER TABLE videos ADD COLUMN failure_count INTEGER DEFAULT 0",
        "ALTER TABLE videos ADD COLUMN last_failure_at DATETIME"
    ]
    
    db = get_db_session()
    try:
        for migration_sql in migrations:
            try:
                print(f"执行: {migration_sql}")
                db.execute(text(migration_sql))
                db.commit()
                print("✓ 成功")
            except Exception as e:
                if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                    print(f"⚠ 字段已存在，跳过: {e}")
                else:
                    print(f"✗ 失败: {e}")
                    raise
        
        print("\n✅ 数据库迁移完成！")
        
        # 验证字段是否添加成功
        print("\n验证新字段...")
        result = db.execute(text("PRAGMA table_info(videos)")).fetchall()
        failure_fields = [row[1] for row in result if row[1] in ['download_failed', 'failure_reason', 'failure_count', 'last_failure_at']]
        
        if len(failure_fields) == 4:
            print("✅ 所有失败跟踪字段已成功添加")
        else:
            print(f"⚠ 部分字段可能未添加成功，当前字段: {failure_fields}")
            
    except Exception as e:
        print(f"❌ 迁移失败: {e}")
        db.rollback()
        return False
    finally:
        db.close()
    
    return True

if __name__ == "__main__":
    print("开始数据库迁移：添加失败跟踪字段...")
    success = migrate_add_failure_tracking()
    sys.exit(0 if success else 1)
