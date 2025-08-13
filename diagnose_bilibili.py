#!/usr/bin/env python3
"""
B站下载器诊断工具
检查Cookie设置、网络连接和视频信息获取
"""

import os
import sys
import json
import subprocess
from pathlib import Path
import argparse

def test_yt_dlp():
    """测试yt-dlp是否正常工作"""
    print("🔍 测试yt-dlp...")
    try:
        result = subprocess.run(['yt-dlp', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ yt-dlp版本: {result.stdout.strip()}")
            return True
        else:
            print(f"❌ yt-dlp测试失败: {result.stderr}")
            return False
    except FileNotFoundError:
        print("❌ yt-dlp未安装，请运行: pip install yt-dlp")
        return False

def test_cookie_file(cookie_path):
    """测试Cookie文件"""
    print(f"🍪 测试Cookie文件: {cookie_path}")
    
    cookie_file = Path(cookie_path)
    
    # 检查文件是否存在
    if not cookie_file.exists():
        print(f"❌ Cookie文件不存在: {cookie_path}")
        
        # 尝试创建目录
        try:
            cookie_file.parent.mkdir(parents=True, exist_ok=True)
            print(f"✅ 已创建目录: {cookie_file.parent}")
        except Exception as e:
            print(f"❌ 创建目录失败: {e}")
        
        return False
    
    # 检查文件内容
    try:
        with open(cookie_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if 'SESSDATA' in content:
            print("✅ Cookie文件包含SESSDATA")
            lines = content.strip().split('\n')
            valid_lines = [line for line in lines if not line.startswith('#') and line.strip()]
            print(f"✅ 有效Cookie行数: {len(valid_lines)}")
            return True
        else:
            print("❌ Cookie文件不包含SESSDATA")
            return False
            
    except Exception as e:
        print(f"❌ 读取Cookie文件失败: {e}")
        return False

def create_cookie_file(cookie_string, cookie_path):
    """创建Cookie文件"""
    print(f"📝 创建Cookie文件: {cookie_path}")
    
    try:
        cookie_file = Path(cookie_path)
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 解析Cookie
        cookie_content = "# Netscape HTTP Cookie File\n"
        
        if cookie_string.startswith('SESSDATA='):
            # 单个SESSDATA
            sessdata_value = cookie_string.split('=', 1)[1]
            cookie_content += f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{sessdata_value}\n"
        elif ';' in cookie_string:
            # 多个Cookie
            for cookie in cookie_string.split(';'):
                cookie = cookie.strip()
                if '=' in cookie:
                    name, value = cookie.split('=', 1)
                    name = name.strip()
                    value = value.strip()
                    cookie_content += f".bilibili.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n"
        else:
            # 假设是纯SESSDATA值
            cookie_content += f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{cookie_string}\n"
        
        # 写入文件
        with open(cookie_file, 'w', encoding='utf-8') as f:
            f.write(cookie_content)
        
        print(f"✅ Cookie文件创建成功")
        print(f"📄 文件内容:")
        print(cookie_content)
        
        return True
        
    except Exception as e:
        print(f"❌ 创建Cookie文件失败: {e}")
        return False

def test_bilibili_access(cookie_path=None):
    """测试B站访问"""
    print("🌐 测试B站访问...")
    
    # 测试URL
    test_url = "https://www.bilibili.com/video/BV1da4y1278s"
    
    cmd = ['yt-dlp', '--dump-json', '--no-download', test_url]
    
    if cookie_path and Path(cookie_path).exists():
        cmd.extend(['--cookies', cookie_path])
        print(f"使用Cookie: {cookie_path}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            try:
                info = json.loads(result.stdout)
                title = info.get('title', 'Unknown')
                uploader = info.get('uploader', 'Unknown')
                print(f"✅ B站访问成功")
                print(f"📺 测试视频: {title}")
                print(f"👤 UP主: {uploader}")
                return True
            except json.JSONDecodeError:
                print(f"❌ 解析视频信息失败")
                return False
        else:
            print(f"❌ B站访问失败: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ B站访问超时")
        return False
    except Exception as e:
        print(f"❌ B站访问出错: {e}")
        return False

def test_collection_info(collection_url, cookie_path=None):
    """测试合集信息获取"""
    print(f"📋 测试合集信息获取...")
    print(f"🔗 合集URL: {collection_url}")
    
    cmd = [
        'yt-dlp',
        '--dump-json',
        '--no-download',
        '--flat-playlist',
        '--playlist-items', '1:3',  # 只获取前3个视频
        collection_url
    ]
    
    if cookie_path and Path(cookie_path).exists():
        cmd.extend(['--cookies', cookie_path])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            videos = []
            
            for line in lines:
                if line.strip():
                    try:
                        info = json.loads(line)
                        if info.get('_type') == 'url':
                            videos.append(info)
                    except json.JSONDecodeError:
                        continue
            
            if videos:
                print(f"✅ 成功获取 {len(videos)} 个视频信息")
                for i, video in enumerate(videos, 1):
                    title = video.get('title', 'Unknown')
                    video_id = video.get('id', 'Unknown')
                    print(f"  {i}. {title} (ID: {video_id})")
                return True
            else:
                print("❌ 未找到视频信息")
                return False
        else:
            print(f"❌ 获取合集信息失败: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ 获取合集信息超时")
        return False
    except Exception as e:
        print(f"❌ 获取合集信息出错: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='B站下载器诊断工具')
    parser.add_argument('--collection-url', 
                       default='https://space.bilibili.com/351754674/lists/2416048?type=season',
                       help='要测试的合集URL')
    parser.add_argument('--cookie-file', 
                       default='/Volumes/nas-mk/xiaoya_emby/xiaoya/bilibili/cookies.txt',
                       help='Cookie文件路径')
    parser.add_argument('--cookie-string', help='Cookie字符串（用于创建Cookie文件）')
    parser.add_argument('--create-cookie', action='store_true', help='创建Cookie文件')
    
    args = parser.parse_args()
    
    print("🚀 B站下载器诊断工具")
    print("=" * 50)
    
    # 1. 测试yt-dlp
    if not test_yt_dlp():
        print("\n❌ 请先安装yt-dlp: pip install yt-dlp")
        return
    
    print()
    
    # 2. 处理Cookie
    if args.create_cookie and args.cookie_string:
        if create_cookie_file(args.cookie_string, args.cookie_file):
            print()
        else:
            return
    
    # 3. 测试Cookie文件
    cookie_valid = test_cookie_file(args.cookie_file)
    print()
    
    # 4. 测试B站访问
    bilibili_access = test_bilibili_access(args.cookie_file if cookie_valid else None)
    print()
    
    # 5. 测试合集信息
    collection_access = test_collection_info(args.collection_url, args.cookie_file if cookie_valid else None)
    print()
    
    # 总结
    print("📊 诊断结果:")
    print(f"  yt-dlp: {'✅' if True else '❌'}")
    print(f"  Cookie文件: {'✅' if cookie_valid else '❌'}")
    print(f"  B站访问: {'✅' if bilibili_access else '❌'}")
    print(f"  合集信息: {'✅' if collection_access else '❌'}")
    
    if cookie_valid and bilibili_access and collection_access:
        print("\n🎉 所有测试通过！可以开始下载了")
        print(f"\n建议使用命令:")
        print(f"python bilibili_collection_downloader_v4_fixed.py \\")
        print(f"  \"{args.collection_url}\" \\")
        print(f"  \"./downloads\" \\")
        print(f"  --cookies \"{args.cookie_file}\" \\")
        print(f"  --max-videos 5 \\")
        print(f"  --verbose")
    else:
        print("\n⚠️ 存在问题，请检查上述失败项")
        
        if not cookie_valid:
            print("\n🔧 Cookie问题解决方案:")
            print("1. 确保Cookie文件路径正确")
            print("2. 使用 --create-cookie --cookie-string 创建Cookie文件")
            print("3. 手动创建Cookie文件，格式参考文档")

if __name__ == '__main__':
    main()

