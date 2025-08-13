#!/usr/bin/env python3
"""
数据库迁移脚本 - 更新表结构以支持增强订阅功能
"""
import sqlite3
import os
from pathlib import Path

def migrate_database():
    """迁移数据库到新结构"""
    # 数据库文件路径
    db_path = Path("data/bili_curator.db")
    
    if not db_path.exists():
        print("数据库文件不存在，将创建新的数据库")
        return
    
    print(f"开始迁移数据库: {db_path}")
    
    # 连接数据库
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 检查subscriptions表的现有列
        cursor.execute("PRAGMA table_info(subscriptions)")
        existing_columns = [row[1] for row in cursor.fetchall()]
        print(f"现有列: {existing_columns}")
        
        # 需要添加的新列
        new_columns = [
            ("specific_urls", "TEXT"),
            ("date_after", "DATE"),
            ("date_before", "DATE"), 
            ("min_likes", "INTEGER"),
            ("min_favorites", "INTEGER"),
            ("min_views", "INTEGER"),
            ("total_videos", "INTEGER DEFAULT 0"),
            ("downloaded_videos", "INTEGER DEFAULT 0"),
            ("is_active", "BOOLEAN DEFAULT 1")
        ]
        
        # 添加缺失的列
        for column_name, column_type in new_columns:
            if column_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE subscriptions ADD COLUMN {column_name} {column_type}")
                    print(f"✅ 添加列: {column_name}")
                except sqlite3.OperationalError as e:
                    print(f"⚠️  添加列 {column_name} 失败: {e}")
        
        # 检查是否需要重命名active列为is_active
        if "active" in existing_columns and "is_active" not in existing_columns:
            try:
                # SQLite不支持直接重命名列，需要创建新表
                print("正在重命名 active 列为 is_active...")
                
                # 创建临时表
                cursor.execute("""
                    CREATE TABLE subscriptions_temp AS 
                    SELECT id, name, type, url, uploader_id, keyword, 
                           active as is_active, last_check, created_at
                    FROM subscriptions
                """)
                
                # 删除原表
                cursor.execute("DROP TABLE subscriptions")
                
                # 重命名临时表
                cursor.execute("ALTER TABLE subscriptions_temp RENAME TO subscriptions")
                
                # 重新添加新列
                for column_name, column_type in new_columns:
                    if column_name != "is_active":  # is_active已经处理过了
                        try:
                            cursor.execute(f"ALTER TABLE subscriptions ADD COLUMN {column_name} {column_type}")
                            print(f"✅ 重新添加列: {column_name}")
                        except sqlite3.OperationalError as e:
                            print(f"⚠️  重新添加列 {column_name} 失败: {e}")
                            
                print("✅ 成功重命名 active 列为 is_active")
                
            except sqlite3.OperationalError as e:
                print(f"⚠️  重命名列失败: {e}")
        
        # 检查videos表结构
        cursor.execute("PRAGMA table_info(videos)")
        video_columns = [row[1] for row in cursor.fetchall()]
        print(f"videos表现有列: {video_columns}")
        
        # 更新videos表字段名
        video_updates = [
            ("video_id", "bilibili_id"),  # 重命名video_id为bilibili_id
            ("file_path", "video_path"),   # 重命名file_path为video_path
        ]
        
        # 添加新的视频表字段
        new_video_columns = [
            ("json_path", "TEXT"),
            ("thumbnail_path", "TEXT"),
            ("view_count", "INTEGER DEFAULT 0"),
            ("downloaded_at", "DATETIME")
        ]
        
        for column_name, column_type in new_video_columns:
            if column_name not in video_columns:
                try:
                    cursor.execute(f"ALTER TABLE videos ADD COLUMN {column_name} {column_type}")
                    print(f"✅ 添加视频表列: {column_name}")
                except sqlite3.OperationalError as e:
                    print(f"⚠️  添加视频表列 {column_name} 失败: {e}")
        
        # 提交更改
        conn.commit()
        print("✅ 数据库迁移完成")
        
        # 验证迁移结果
        cursor.execute("PRAGMA table_info(subscriptions)")
        final_columns = [row[1] for row in cursor.fetchall()]
        print(f"迁移后的subscriptions表列: {final_columns}")
        
    except Exception as e:
        print(f"❌ 迁移过程中发生错误: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_database()
