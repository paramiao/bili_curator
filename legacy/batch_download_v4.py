#!/usr/bin/env python3
"""
B站合集批量下载脚本 V4
支持Cookie认证、真实名称和视频真实标题
"""

import json
import subprocess
import sys
from pathlib import Path
import argparse

def load_collections_config(config_file):
    """加载合集配置文件"""
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"加载配置文件失败: {e}")
        return None

def download_collection(collection, base_output_dir, downloader_script, global_args):
    """下载单个合集"""
    print(f"\n{'='*60}")
    print(f"开始下载: {collection['name']}")
    print(f"URL: {collection['url']}")
    print(f"{'='*60}")
    
    # 确定输出目录
    if 'output_dir' in collection:
        # 使用合集指定的目录
        output_dir = Path(collection['output_dir'])
        if not output_dir.is_absolute():
            # 如果是相对路径，则相对于基础目录
            output_dir = base_output_dir / collection['output_dir']
    else:
        # 使用基础目录，让脚本自动获取合集名称
        output_dir = base_output_dir
    
    print(f"输出目录: {output_dir}")
    
    # 构建命令
    cmd = [
        sys.executable, downloader_script,
        collection['url'],
        str(output_dir)
    ]
    
    # 添加合集名称（如果指定了自定义名称）
    if 'custom_name' in collection:
        cmd.extend(['--name', collection['custom_name']])
    elif 'name' in collection and not collection.get('use_auto_name', True):
        # 如果配置中指定了name且不使用自动名称
        cmd.extend(['--name', collection['name']])
    # 否则让脚本自动获取合集真实名称
    
    # 添加合集特定参数
    if 'max_videos' in collection:
        cmd.extend(['--max-videos', str(collection['max_videos'])])
    
    if 'quality' in collection:
        cmd.extend(['--quality', collection['quality']])
    
    # 添加Cookie支持
    if 'cookies' in collection:
        cmd.extend(['--cookies', collection['cookies']])
    elif global_args.get('cookies'):
        cmd.extend(['--cookies', global_args['cookies']])
    
    # 添加全局参数
    if global_args.get('delay_min'):
        cmd.extend(['--delay-min', str(global_args['delay_min'])])
    
    if global_args.get('delay_max'):
        cmd.extend(['--delay-max', str(global_args['delay_max'])])
    
    if global_args.get('verbose'):
        cmd.append('--verbose')
    
    try:
        # 执行下载
        result = subprocess.run(cmd, check=False)
        
        if result.returncode == 0:
            print(f"✓ 合集 '{collection['name']}' 下载完成")
            return True
        else:
            print(f"✗ 合集 '{collection['name']}' 下载失败")
            return False
            
    except KeyboardInterrupt:
        print(f"\n用户中断下载")
        raise
    except Exception as e:
        print(f"✗ 下载合集 '{collection['name']}' 时出错: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(
        description='B站合集批量下载器 V4 - 支持Cookie认证',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
配置文件格式:
[
  {
    "name": "显示名称",
    "url": "合集URL",
    "use_auto_name": true,              // 是否使用自动获取的合集名称
    "custom_name": "自定义名称",         // 可选：覆盖自动获取的名称
    "output_dir": "子目录",             // 可选：输出子目录
    "max_videos": 50,                  // 可选：最大下载数量
    "quality": "best[height<=720]",    // 可选：视频质量
    "cookies": "SESSDATA=xxx"          // 可选：合集专用Cookie
  }
]

Cookie使用:
  --cookies "SESSDATA=xxx"             // 全局Cookie，应用于所有合集
  配置文件中的cookies字段              // 合集专用Cookie，优先级更高

示例:
  %(prog)s collections_v4.json ./downloads --cookies "SESSDATA=xxx"
  %(prog)s collections_v4.json ./downloads --cookies-file cookies.txt
        """
    )
    
    parser.add_argument('config', help='配置文件路径')
    parser.add_argument('output', help='输出根目录')
    parser.add_argument('--downloader', default='bilibili_collection_downloader_v4.py',
                       help='下载器脚本路径')
    parser.add_argument('--cookies', help='全局Cookie字符串或Cookie文件路径')
    parser.add_argument('--cookies-file', help='全局Cookie文件路径')
    parser.add_argument('--delay-min', type=float, default=3.0,
                       help='最小延时秒数')
    parser.add_argument('--delay-max', type=float, default=8.0,
                       help='最大延时秒数')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='详细输出')
    parser.add_argument('--continue-on-error', action='store_true',
                       help='出错时继续下载其他合集')
    
    args = parser.parse_args()
    
    # 检查下载器脚本
    downloader_path = Path(args.downloader)
    if not downloader_path.exists():
        print(f"错误: 找不到下载器脚本: {downloader_path}")
        sys.exit(1)
    
    # 加载配置
    collections = load_collections_config(args.config)
    if not collections:
        sys.exit(1)
    
    # 创建输出目录
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 处理Cookie参数
    cookies = args.cookies or args.cookies_file
    
    # 全局参数
    global_args = {
        'delay_min': args.delay_min,
        'delay_max': args.delay_max,
        'verbose': args.verbose,
        'cookies': cookies
    }
    
    # 批量下载
    success_count = 0
    total_count = len(collections)
    
    print(f"开始批量下载 {total_count} 个合集...")
    print("注意: 将使用合集的真实名称作为文件夹名，视频真实标题作为文件名")
    if cookies:
        print("使用Cookie进行认证下载")
    
    try:
        for i, collection in enumerate(collections, 1):
            print(f"\n进度: {i}/{total_count}")
            
            success = download_collection(
                collection, output_dir, downloader_path, global_args
            )
            
            if success:
                success_count += 1
            elif not args.continue_on_error:
                print("下载失败，停止批量下载")
                break
        
        print(f"\n批量下载完成: {success_count}/{total_count} 个合集成功")
        
    except KeyboardInterrupt:
        print(f"\n用户中断批量下载")
        print(f"已完成: {success_count}/{total_count} 个合集")

if __name__ == '__main__':
    main()

