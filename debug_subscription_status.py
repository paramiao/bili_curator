#!/usr/bin/env python3
"""
调试订阅启用状态脚本
检查数据库中订阅的is_active字段状态
"""

import sys
import os
import sqlite3

# 直接使用SQLite连接，避免配置问题
DB_PATH = os.getenv('DB_PATH', '/Users/paramiao/development/bili_curator/bili_curator/data/bilibili_curator.db')

def debug_subscription_status():
    """调试订阅状态"""
    print("🔍 检查订阅启用状态")
    print("=" * 50)
    
    if not os.path.exists(DB_PATH):
        print(f"❌ 数据库文件不存在: {DB_PATH}")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 查询所有订阅
        cursor.execute("SELECT COUNT(*) FROM subscriptions")
        total_count = cursor.fetchone()[0]
        print(f"📊 总订阅数: {total_count}")
        
        # 查询启用的订阅
        cursor.execute("SELECT COUNT(*) FROM subscriptions WHERE is_active = 1")
        active_count = cursor.fetchone()[0]
        print(f"✅ 启用订阅数: {active_count}")
        
        # 查询禁用的订阅
        cursor.execute("SELECT COUNT(*) FROM subscriptions WHERE is_active = 0")
        inactive_count = cursor.fetchone()[0]
        print(f"❌ 禁用订阅数: {inactive_count}")
        
        # 查询is_active为NULL的订阅
        cursor.execute("SELECT COUNT(*) FROM subscriptions WHERE is_active IS NULL")
        null_count = cursor.fetchone()[0]
        print(f"❓ is_active为NULL的订阅数: {null_count}")
        
        # 查询所有订阅详情
        cursor.execute("""
            SELECT id, name, type, is_active, download_mode 
            FROM subscriptions 
            ORDER BY id
        """)
        all_subs = cursor.fetchall()
        
        print("\n📋 订阅详情:")
        print("-" * 80)
        print(f"{'ID':<5} {'名称':<20} {'类型':<12} {'is_active':<10} {'下载模式':<10}")
        print("-" * 80)
        
        for sub in all_subs:
            sub_id, name, sub_type, is_active, download_mode = sub
            name_display = (name[:18] if name else "未命名")
            download_mode_display = download_mode or "local"
            print(f"{sub_id:<5} {name_display:<20} {sub_type:<12} {is_active:<10} {download_mode_display:<10}")
        
        # 检查STRM模式订阅
        cursor.execute("SELECT COUNT(*) FROM subscriptions WHERE download_mode = 'strm'")
        strm_count = cursor.fetchone()[0]
        print(f"\n🎬 STRM模式订阅数: {strm_count}")
        
        if strm_count > 0:
            cursor.execute("""
                SELECT id, name, is_active 
                FROM subscriptions 
                WHERE download_mode = 'strm'
            """)
            strm_subs = cursor.fetchall()
            print("STRM订阅详情:")
            for sub in strm_subs:
                sub_id, name, is_active = sub
                print(f"  - {name} (ID: {sub_id}, 启用: {is_active})")
        
        # 检查数据库字段信息
        print(f"\n🔧 数据库字段信息:")
        cursor.execute("PRAGMA table_info(subscriptions)")
        columns = cursor.fetchall()
        for col in columns:
            if col[1] == 'is_active':
                print(f"  is_active字段: 类型={col[2]}, 非空={col[3]}, 默认值={col[4]}")
                break
        
        # 修复NULL值
        if null_count > 0:
            print(f"\n🔧 修复 {null_count} 个is_active为NULL的订阅...")
            cursor.execute("UPDATE subscriptions SET is_active = 1 WHERE is_active IS NULL")
            conn.commit()
            print("✅ 修复完成")
            
            # 重新检查
            cursor.execute("SELECT COUNT(*) FROM subscriptions WHERE is_active = 1")
            active_count_after = cursor.fetchone()[0]
            print(f"🔄 修复后启用订阅数: {active_count_after}")
        
    except Exception as e:
        print(f"❌ 检查过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == '__main__':
    debug_subscription_status()
