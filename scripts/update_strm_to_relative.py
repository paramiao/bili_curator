#!/usr/bin/env python3
"""
批量更新现有STRM文件为相对路径
将绝对URL转换为相对路径，适配SenPlayer等外部客户端访问
"""

import os
import re
import argparse
from pathlib import Path
from typing import List, Tuple


def find_strm_files(base_path: str) -> List[Path]:
    """查找所有STRM文件"""
    strm_files = []
    base_path = Path(base_path)
    
    if base_path.exists():
        strm_files.extend(base_path.rglob("*.strm"))
    
    return strm_files


def update_strm_file(file_path: Path) -> Tuple[bool, str, str]:
    """
    更新单个STRM文件
    
    Returns:
        (是否更新, 原内容, 新内容)
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read().strip()
        
        # 匹配绝对URL模式
        # 支持: http://192.168.31.2:8080/api/strm/stream/BVxxxx
        # 支持: http://localhost:8080/api/strm/stream/BVxxxx
        # 支持: https://domain.com:8080/api/strm/stream/BVxxxx
        pattern = r'https?://[^/]+/api/strm/stream/([A-Za-z0-9]+)'
        match = re.search(pattern, original_content)
        
        if match:
            bilibili_id = match.group(1)
            new_content = f"/api/strm/stream/{bilibili_id}"
            
            # 写入新内容
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            return True, original_content, new_content
        else:
            # 检查是否已经是相对路径
            relative_pattern = r'^/api/strm/stream/[A-Za-z0-9]+$'
            if re.match(relative_pattern, original_content):
                return False, original_content, original_content  # 已经是相对路径
            else:
                return False, original_content, f"未识别的格式: {original_content}"
                
    except Exception as e:
        return False, "", f"错误: {str(e)}"


def main():
    parser = argparse.ArgumentParser(description='批量更新STRM文件为相对路径')
    parser.add_argument('--strm-path', default='/app/strm', help='STRM文件目录路径')
    parser.add_argument('--dry-run', action='store_true', help='预览模式，不实际修改文件')
    parser.add_argument('--verbose', action='store_true', help='详细输出')
    
    args = parser.parse_args()
    
    print(f"🔍 搜索STRM文件: {args.strm_path}")
    strm_files = find_strm_files(args.strm_path)
    
    if not strm_files:
        print("❌ 未找到任何STRM文件")
        return
    
    print(f"📁 找到 {len(strm_files)} 个STRM文件")
    
    updated_count = 0
    already_relative_count = 0
    error_count = 0
    
    for file_path in strm_files:
        updated, original, new_content = update_strm_file(file_path)
        
        if updated:
            updated_count += 1
            status = "🔄 [DRY-RUN]" if args.dry_run else "✅ [UPDATED]"
            print(f"{status} {file_path}")
            
            if args.verbose:
                print(f"    原内容: {original}")
                print(f"    新内容: {new_content}")
                
            # 如果是预览模式，恢复原内容
            if args.dry_run:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(original)
                    
        elif "未识别的格式" in new_content:
            error_count += 1
            print(f"❌ [ERROR] {file_path}: {new_content}")
            
        else:
            already_relative_count += 1
            if args.verbose:
                print(f"✓ [SKIP] {file_path}: 已经是相对路径")
    
    print(f"\n📊 处理结果:")
    print(f"   更新文件: {updated_count}")
    print(f"   已是相对路径: {already_relative_count}")
    print(f"   错误文件: {error_count}")
    
    if args.dry_run and updated_count > 0:
        print(f"\n💡 预览模式完成，实际更新请运行: python {__file__} --strm-path {args.strm_path}")


if __name__ == "__main__":
    main()
