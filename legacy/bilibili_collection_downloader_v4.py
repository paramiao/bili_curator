#!/usr/bin/env python3
"""
B站合集下载器 - 基于成功版本，专门修复重命名问题
回到最初可以下载成功的逻辑，只修复文件名问题
"""

import os
import sys
import json
import subprocess
import re
import time
import random
import argparse
from pathlib import Path
import logging

class BilibiliWorkingDownloader:
    def __init__(self, output_dir, max_videos=None, quality='best', cookies=None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_videos = max_videos
        self.quality = quality
        self.cookies = cookies
        
        # 设置日志
        self.setup_logging()
        
        # 格式回退列表（基于之前成功的版本）
        self.format_fallbacks = [
            'best[height<=1080]',
            'best[height<=720]', 
            'best[height<=480]',
            'best[ext=mp4]',
            'best[ext=flv]',
            'bestvideo+bestaudio/best',
            'best',
            'worst'
        ]
        
        # 处理Cookie
        self.cookie_file = None
        if self.cookies:
            self.setup_cookies()
    
    def setup_logging(self):
        """设置日志记录"""
        log_file = self.output_dir / 'download.log'
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def setup_cookies(self):
        """设置Cookie文件"""
        try:
            self.logger.info(f"正在设置Cookie...")
            
            if self.cookies.startswith('/') and self.cookies.endswith('.txt'):
                self.cookie_file = Path(self.cookies)
                if self.cookie_file.exists():
                    self.logger.info(f"✓ 使用现有Cookie文件: {self.cookie_file}")
                    return
                else:
                    self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
            else:
                self.cookie_file = self.output_dir / 'cookies.txt'
            
            # 解析Cookie内容
            cookie_content = "# Netscape HTTP Cookie File\n"
            
            if self.cookies.startswith('SESSDATA='):
                sessdata_value = self.cookies.split('=', 1)[1]
                cookie_content += f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{sessdata_value}\n"
            elif ';' in self.cookies:
                for cookie in self.cookies.split(';'):
                    cookie = cookie.strip()
                    if '=' in cookie:
                        name, value = cookie.split('=', 1)
                        name = name.strip()
                        value = value.strip()
                        cookie_content += f".bilibili.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n"
            else:
                cookie_content += f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{self.cookies}\n"
            
            with open(self.cookie_file, 'w', encoding='utf-8') as f:
                f.write(cookie_content)
            
            self.logger.info(f"✓ Cookie文件已创建: {self.cookie_file}")
            
        except Exception as e:
            self.logger.error(f"✗ 设置Cookie失败: {e}")
            self.cookie_file = None
    
    def get_yt_dlp_base_args(self):
        """获取yt-dlp基础参数"""
        args = [
            'yt-dlp',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--sleep-interval', '3',
            '--max-sleep-interval', '8',
            '--retries', '5',
            '--fragment-retries', '5',
            '--retry-sleep', '3',
            '--ignore-errors',
            '--no-warnings',
        ]
        
        if self.cookie_file and self.cookie_file.exists():
            args.extend(['--cookies', str(self.cookie_file)])
            self.logger.info(f"✓ 使用Cookie文件: {self.cookie_file}")
        
        return args
    
    def get_collection_name(self, collection_url):
        """获取合集名称"""
        self.logger.info("正在获取合集信息...")
        
        cmd = self.get_yt_dlp_base_args() + [
            '--dump-json',
            '--no-download',
            '--flat-playlist',
            '--playlist-items', '1',
            collection_url
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if line.strip():
                        try:
                            info = json.loads(line)
                            collection_name = (
                                info.get('playlist_title') or 
                                info.get('uploader', 'Unknown') + '_Collection'
                            )
                            if collection_name and collection_name != 'NA':
                                self.logger.info(f"✓ 找到合集名称: {collection_name}")
                                return self.sanitize_filename(collection_name)
                        except json.JSONDecodeError:
                            continue
            
            return "Unknown_Collection"
            
        except Exception as e:
            self.logger.warning(f"获取合集信息失败: {e}")
            return "Unknown_Collection"
    
    def download_collection(self, collection_url, collection_name=None):
        """下载整个合集"""
        if not collection_name:
            collection_name = self.get_collection_name(collection_url)
        else:
            collection_name = self.sanitize_filename(collection_name)
        
        self.logger.info(f"开始下载合集: {collection_name}")
        self.logger.info(f"URL: {collection_url}")
        
        # 创建合集目录
        collection_dir = self.output_dir / collection_name
        collection_dir.mkdir(exist_ok=True)
        self.logger.info(f"合集目录: {collection_dir}")
        
        # 第一步：使用格式回退下载（恢复之前成功的逻辑）
        success = self.download_with_format_fallback(collection_url, collection_dir)
        
        if success:
            # 第二步：重命名文件（修复后的逻辑）
            self.logger.info("=" * 50)
            self.logger.info("开始重命名阶段...")
            self.logger.info("=" * 50)
            self.rename_files_correctly(collection_dir, collection_name)
        
        return success
    
    def download_with_format_fallback(self, collection_url, collection_dir):
        """使用格式回退下载（恢复之前成功的逻辑）"""
        self.logger.info("开始下载合集（使用格式回退）...")
        
        # 如果用户指定了特定格式，先尝试用户格式
        formats_to_try = []
        if self.quality not in self.format_fallbacks:
            formats_to_try.append(self.quality)
        formats_to_try.extend(self.format_fallbacks)
        
        for format_selector in formats_to_try:
            self.logger.info(f"尝试格式: {format_selector}")
            
            if self.download_with_format(collection_url, collection_dir, format_selector):
                self.logger.info(f"✓ 使用格式 {format_selector} 下载成功")
                return True
            else:
                self.logger.warning(f"格式 {format_selector} 失败，尝试下一个...")
        
        self.logger.error(f"✗ 所有格式都失败")
        return False
    
    def download_with_format(self, collection_url, collection_dir, format_selector):
        """使用指定格式下载"""
        try:
            cmd = self.get_yt_dlp_base_args() + [
                '--format', format_selector,
                '--output', '%(playlist_index)02d_%(id)s.%(ext)s',  # 使用序号_ID的命名方式
                '--write-info-json',
                '--write-thumbnail',
                '--convert-thumbnails', 'jpg',
                collection_url
            ]
            
            # 限制下载数量
            if self.max_videos:
                cmd.extend(['--playlist-end', str(self.max_videos)])
            
            self.logger.info(f"执行下载命令...")
            
            result = subprocess.run(
                cmd, 
                cwd=collection_dir, 
                capture_output=True, 
                text=True,
                timeout=3600  # 1小时超时
            )
            
            if result.returncode == 0:
                self.logger.info(f"✓ 下载完成")
                
                # 显示下载的文件
                downloaded_files = list(collection_dir.glob('*'))
                self.logger.info(f"下载了 {len(downloaded_files)} 个文件")
                
                return True
            else:
                # 检查是否是格式问题
                if "Requested format is not available" in result.stderr:
                    self.logger.warning(f"格式不可用: {format_selector}")
                    return False  # 格式不可用，不重试
                else:
                    self.logger.warning(f"下载失败: {result.stderr}")
                    return False
                
        except subprocess.TimeoutExpired:
            self.logger.warning(f"下载超时")
            return False
        except Exception as e:
            self.logger.warning(f"下载出错: {e}")
            return False
    
    def rename_files_correctly(self, collection_dir, collection_name):
        """正确重命名文件 - 修复后的逻辑"""
        self.logger.info("开始重命名文件（修复版本）...")
        
        # 查找所有info.json文件
        info_files = list(collection_dir.glob('*.info.json'))
        self.logger.info(f"找到 {len(info_files)} 个info.json文件")
        
        if not info_files:
            self.logger.warning("没有找到info.json文件")
            return
        
        renamed_count = 0
        
        for info_file in info_files:
            self.logger.info(f"\n📝 处理文件: {info_file.name}")
            
            try:
                # 读取并解析info.json
                with open(info_file, 'r', encoding='utf-8') as f:
                    video_info = json.load(f)
                
                # 获取标题 - 关键修复点
                title = video_info.get('title')
                if not title:
                    title = video_info.get('fulltitle')
                if not title:
                    title = video_info.get('display_id')
                
                if not title:
                    self.logger.warning(f"  ⚠️ 无法获取标题，跳过此文件")
                    continue
                
                self.logger.info(f"  📺 原始标题: '{title}'")
                
                # 清理标题作为文件名
                safe_title = self.sanitize_filename(title)
                self.logger.info(f"  📁 清理后文件名: '{safe_title}'")
                
                if not safe_title or safe_title == 'untitled':
                    self.logger.warning(f"  ⚠️ 文件名清理后为空，跳过")
                    continue
                
                # 获取原始文件前缀
                original_prefix = info_file.stem  # 去掉.info.json后缀
                self.logger.info(f"  🔤 原始前缀: '{original_prefix}'")
                
                # 执行重命名
                success = self.rename_file_group(collection_dir, original_prefix, safe_title)
                
                if success:
                    # 生成nfo文件
                    self.generate_nfo(video_info, collection_dir, safe_title, collection_name)
                    renamed_count += 1
                
            except Exception as e:
                self.logger.error(f"  ❌ 处理文件失败: {e}")
                import traceback
                self.logger.error(f"  详细错误: {traceback.format_exc()}")
        
        self.logger.info(f"\n🎉 重命名完成: {renamed_count}/{len(info_files)} 个文件组")
        
        # 显示最终结果
        self.show_final_results(collection_dir)
    
    def rename_file_group(self, collection_dir, original_prefix, new_name):
        """重命名一组相关文件"""
        # 查找所有相关文件
        pattern = f'{original_prefix}.*'
        related_files = list(collection_dir.glob(pattern))
        
        self.logger.info(f"  📂 查找模式: '{pattern}'")
        self.logger.info(f"  📂 找到相关文件: {len(related_files)}个")
        
        if not related_files:
            self.logger.warning(f"  ⚠️ 没有找到相关文件")
            return False
        
        # 显示找到的文件
        for file in related_files:
            self.logger.info(f"    - {file.name}")
        
        success_count = 0
        
        for file in related_files:
            try:
                # 确定新文件名
                if file.suffix == '.json' and 'info' in file.name:
                    new_filename = f'{new_name}.info.json'
                elif file.suffix.lower() in ['.mp4', '.flv', '.mkv', '.webm']:
                    new_filename = f'{new_name}{file.suffix}'
                elif file.suffix.lower() in ['.jpg', '.png', '.webp']:
                    new_filename = f'{new_name}{file.suffix}'
                else:
                    new_filename = f'{new_name}{file.suffix}'
                
                new_path = collection_dir / new_filename
                
                # 检查是否需要重命名
                if file.name == new_filename:
                    self.logger.info(f"    ✓ 文件名已正确: {file.name}")
                    success_count += 1
                    continue
                
                # 检查目标文件是否存在
                if new_path.exists():
                    self.logger.warning(f"    ⚠️ 目标文件已存在: {new_filename}")
                    continue
                
                # 执行重命名
                self.logger.info(f"    🔄 重命名: {file.name} -> {new_filename}")
                file.rename(new_path)
                self.logger.info(f"    ✅ 重命名成功")
                success_count += 1
                
            except Exception as e:
                self.logger.error(f"    ❌ 重命名失败 {file.name}: {e}")
        
        return success_count > 0
    
    def show_final_results(self, collection_dir):
        """显示最终结果"""
        final_files = list(collection_dir.glob('*'))
        self.logger.info(f"\n📁 最终文件列表 ({len(final_files)}个):")
        
        # 按类型分组显示
        video_files = [f for f in final_files if f.suffix.lower() in ['.mp4', '.flv', '.mkv', '.webm']]
        nfo_files = [f for f in final_files if f.suffix == '.nfo']
        info_files = [f for f in final_files if f.suffix == '.json' and 'info' in f.name]
        
        self.logger.info(f"  🎬 视频文件 ({len(video_files)}个):")
        for file in sorted(video_files):
            self.logger.info(f"    - {file.name}")
        
        if nfo_files:
            self.logger.info(f"  📄 NFO文件 ({len(nfo_files)}个):")
            for file in sorted(nfo_files):
                self.logger.info(f"    - {file.name}")
        
        if info_files:
            self.logger.info(f"  📋 Info文件 ({len(info_files)}个):")
            for file in sorted(info_files):
                self.logger.info(f"    - {file.name}")
    
    def generate_nfo(self, video_info, output_dir, filename, collection_name):
        """生成nfo文件"""
        try:
            nfo_path = output_dir / f'{filename}.nfo'
            
            if nfo_path.exists():
                self.logger.info(f"    ✓ NFO文件已存在: {filename}.nfo")
                return
            
            # 创建简单的nfo内容
            nfo_content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
    <title>{video_info.get('title', 'Unknown')}</title>
    <originaltitle>{video_info.get('title', 'Unknown')}</originaltitle>
    <plot>{video_info.get('description', '')[:500]}</plot>
    <director>{video_info.get('uploader', '')}</director>
    <runtime>{int(video_info.get('duration', 0) / 60) if video_info.get('duration') else 0}</runtime>
    <premiered>{video_info.get('upload_date', '')[:4]}-{video_info.get('upload_date', '')[4:6]}-{video_info.get('upload_date', '')[6:8] if len(video_info.get('upload_date', '')) >= 8 else ''}</premiered>
    <tag>{collection_name}</tag>
    <tag>Bilibili</tag>
    <uniqueid type="bilibili">{video_info.get('id', '')}</uniqueid>
    <website>{video_info.get('webpage_url', '')}</website>
</movie>"""
            
            with open(nfo_path, 'w', encoding='utf-8') as f:
                f.write(nfo_content)
            
            self.logger.info(f"    ✓ NFO文件生成: {filename}.nfo")
            
        except Exception as e:
            self.logger.error(f"    ✗ NFO生成失败: {e}")
    
    def sanitize_filename(self, filename):
        """清理文件名"""
        if not filename or filename.strip() == '':
            return 'untitled'
        
        # 移除非法字符
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = re.sub(r'[\r\n\t]', ' ', filename)
        filename = re.sub(r'\s+', ' ', filename).strip()
        filename = filename.strip('. ')
        
        # 移除特殊字符但保留中文
        filename = re.sub(r'[【】\[\]()（）]', '', filename)
        filename = re.sub(r'[!！@#$%^&*+={}|;:,.<>?~`]', '_', filename)
        
        # 限制长度
        if len(filename) > 80:
            filename = filename[:80].rsplit(' ', 1)[0]
        
        return filename or 'untitled'
    
    def cleanup(self):
        """清理临时文件"""
        if self.cookie_file and self.cookie_file.name == 'cookies.txt':
            try:
                if self.cookie_file.exists():
                    self.cookie_file.unlink()
            except:
                pass

def main():
    parser = argparse.ArgumentParser(description='B站合集下载器 - 基于成功版本修复重命名')
    parser.add_argument('url', help='B站合集URL')
    parser.add_argument('output', help='输出根目录')
    parser.add_argument('--name', help='自定义合集名称')
    parser.add_argument('--max-videos', type=int, help='最大下载视频数量')
    parser.add_argument('--quality', default='best', help='视频质量')
    parser.add_argument('--cookies', help='Cookie字符串或Cookie文件路径')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')
    
    args = parser.parse_args()
    
    if 'bilibili.com' not in args.url or 'lists' not in args.url:
        print("错误: 请提供有效的B站合集URL")
        sys.exit(1)
    
    downloader = BilibiliWorkingDownloader(
        output_dir=args.output,
        max_videos=args.max_videos,
        quality=args.quality,
        cookies=args.cookies
    )
    
    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)
    
    print(f"🚀 B站合集下载器 - 修复版本")
    print(f"  输出目录: {args.output}")
    print(f"  最大视频数: {args.max_videos or '无限制'}")
    print(f"  视频质量: {args.quality}")
    print(f"  Cookie: {'✓' if args.cookies else '✗'}")
    print(f"  策略: 格式回退 + 修复重命名")
    print()
    
    try:
        success = downloader.download_collection(args.url, args.name)
        if success:
            print(f"\n✅ 下载和重命名完成!")
            print(f"📁 文件已使用视频真实标题命名")
        else:
            print(f"\n❌ 下载失败!")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print(f"\n⚠️ 用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 程序出错: {e}")
        sys.exit(1)
    finally:
        downloader.cleanup()

if __name__ == '__main__':
    main()

