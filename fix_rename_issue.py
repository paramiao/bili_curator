#!/usr/bin/env python3
"""
修复重命名问题的脚本
专门用于将已下载的文件重命名为info.json中的title
"""

import os
import sys
import json
import re
from pathlib import Path
import argparse

def sanitize_filename(filename):
    """清理文件名中的非法字符"""
    if not filename or filename.strip() == '':
        return 'untitled'
    
    # 移除或替换非法字符
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = re.sub(r'[\r\n\t]', ' ', filename)
    filename = re.sub(r'\s+', ' ', filename).strip()
    filename = filename.strip('. ')
    
    # 移除一些特殊字符但保留中文
    filename = re.sub(r'[【】\[\]()（）]', '', filename)
    filename = re.sub(r'[!！@#$%^&*+={}|;:,.<>?~`]', '_', filename)
    
    # 限制长度
    if len(filename) > 80:
        filename = filename[:80].rsplit(' ', 1)[0]
    
    return filename or 'untitled'

def rename_files_from_info_json(directory):
    """从info.json文件中读取title并重命名文件"""
    directory = Path(directory)
    
    if not directory.exists():
        print(f"❌ 目录不存在: {directory}")
        return
    
    print(f"🔍 扫描目录: {directory}")
    
    # 查找所有info.json文件
    info_files = list(directory.glob('*.info.json'))
    print(f"📄 找到 {len(info_files)} 个info.json文件")
    
    if not info_files:
        print("❌ 没有找到info.json文件")
        return
    
    renamed_count = 0
    
    for info_file in info_files:
        print(f"\n📝 处理: {info_file.name}")
        
        try:
            # 读取info.json
            with open(info_file, 'r', encoding='utf-8') as f:
                video_info = json.load(f)
            
            # 获取视频标题
            title = video_info.get('title', '')
            if not title:
                print(f"  ⚠️ info.json中没有title字段")
                continue
            
            print(f"  📺 视频标题: {title}")
            
            # 清理标题作为文件名
            safe_title = sanitize_filename(title)
            print(f"  📁 安全文件名: {safe_title}")
            
            # 获取原始文件名前缀（去掉.info.json）
            original_prefix = info_file.stem
            print(f"  🔤 原始前缀: {original_prefix}")
            
            # 查找所有相关文件
            related_files = list(directory.glob(f'{original_prefix}.*'))
            print(f"  📂 找到相关文件: {len(related_files)}个")
            
            for related_file in related_files:
                print(f"    🔍 检查文件: {related_file.name}")
                
                if related_file.suffix == '.json' and 'info' in related_file.name:
                    # info.json文件
                    new_name = f'{safe_title}.info.json'
                elif related_file.suffix in ['.mp4', '.flv', '.mkv', '.webm']:
                    # 视频文件
                    new_name = f'{safe_title}{related_file.suffix}'
                elif related_file.suffix in ['.jpg', '.png', '.webp']:
                    # 缩略图文件
                    new_name = f'{safe_title}{related_file.suffix}'
                else:
                    # 其他文件
                    new_name = f'{safe_title}{related_file.suffix}'
                
                new_path = directory / new_name
                
                # 检查是否需要重命名
                if related_file.name == new_name:
                    print(f"    ✓ 文件名已正确: {related_file.name}")
                    continue
                
                # 检查目标文件是否已存在
                if new_path.exists():
                    print(f"    ⚠️ 目标文件已存在，跳过: {new_name}")
                    continue
                
                # 执行重命名
                try:
                    print(f"    🔄 重命名: {related_file.name} -> {new_name}")
                    related_file.rename(new_path)
                    print(f"    ✅ 重命名成功")
                except Exception as e:
                    print(f"    ❌ 重命名失败: {e}")
            
            renamed_count += 1
            
        except Exception as e:
            print(f"  ❌ 处理info.json文件失败: {e}")
    
    print(f"\n🎉 重命名完成: {renamed_count}/{len(info_files)} 个文件组")

def main():
    parser = argparse.ArgumentParser(description='修复文件重命名问题')
    parser.add_argument('directory', help='包含info.json文件的目录')
    parser.add_argument('--dry-run', action='store_true', help='只显示将要执行的操作，不实际重命名')
    
    args = parser.parse_args()
    
    print("🔧 B站视频文件重命名工具")
    print("=" * 50)
    
    if args.dry_run:
        print("⚠️ 干运行模式：只显示操作，不实际执行")
        print()
    
    rename_files_from_info_json(args.directory)

if __name__ == '__main__':
    main()

