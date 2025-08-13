#!/usr/bin/env python3
"""
B站合集增量下载器
基于已有视频ID列表进行增量更新
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

class BilibiliIncrementalDownloader:
    def __init__(self, output_dir, max_videos=None, quality='best', cookies=None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_videos = max_videos
        self.quality = quality
        self.cookies = cookies
        
        # 设置日志
        self.setup_logging()
        
        # 格式回退列表
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
        
        # 加载已下载的视频ID
        self.downloaded_ids = self.load_downloaded_ids()
    
    def setup_logging(self):
        """设置日志记录"""
        log_file = self.output_dir / 'incremental_download.log'
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
    
    def load_downloaded_ids(self):
        """加载已下载的视频ID"""
        id_file = self.output_dir / 'downloaded_video_ids.txt'
        downloaded_ids = set()
        
        if id_file.exists():
            try:
                with open(id_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        video_id = line.strip()
                        if video_id:
                            downloaded_ids.add(video_id)
                
                self.logger.info(f"✓ 加载已下载视频ID: {len(downloaded_ids)}个")
                
                # 显示最近几个ID
                if downloaded_ids:
                    recent_ids = list(downloaded_ids)[-5:]
                    self.logger.info(f"  最近ID: {', '.join(recent_ids)}")
                
            except Exception as e:
                self.logger.warning(f"加载视频ID文件失败: {e}")
        else:
            self.logger.info("未找到已下载视频ID文件，将下载所有视频")
        
        return downloaded_ids
    
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
    
    def get_collection_videos(self, collection_url):
        """获取合集中的所有视频信息"""
        self.logger.info("获取合集视频列表...")
        
        cmd = self.get_yt_dlp_base_args() + [
            '--dump-json',
            '--no-download',
            '--flat-playlist',
            collection_url
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode != 0:
                self.logger.error(f"获取视频列表失败: {result.stderr}")
                return []
            
            videos = []
            lines = result.stdout.strip().split('\n')
            
            for line in lines:
                if line.strip():
                    try:
                        video_info = json.loads(line)
                        video_id = video_info.get('id', '')
                        title = video_info.get('title', '')
                        url = video_info.get('url', '')
                        
                        if video_id and title:
                            videos.append({
                                'id': video_id,
                                'title': title,
                                'url': url,
                                'info': video_info
                            })
                    except json.JSONDecodeError:
                        continue
            
            self.logger.info(f"✓ 找到 {len(videos)} 个视频")
            return videos
            
        except Exception as e:
            self.logger.error(f"获取视频列表出错: {e}")
            return []
    
    def filter_new_videos(self, all_videos):
        """过滤出新视频"""
        new_videos = []
        
        for video in all_videos:
            video_id = video['id']
            if video_id not in self.downloaded_ids:
                new_videos.append(video)
            else:
                self.logger.info(f"跳过已下载: {video_id} - {video['title']}")
        
        self.logger.info(f"✓ 发现 {len(new_videos)} 个新视频需要下载")
        return new_videos
    
    def download_incremental(self, collection_url, collection_name=None):
        """增量下载合集"""
        self.logger.info(f"开始增量下载合集")
        self.logger.info(f"URL: {collection_url}")
        
        # 获取所有视频
        all_videos = self.get_collection_videos(collection_url)
        if not all_videos:
            self.logger.error("无法获取视频列表")
            return False
        
        # 过滤新视频
        new_videos = self.filter_new_videos(all_videos)
        
        if not new_videos:
            self.logger.info("✓ 没有新视频需要下载")
            return True
        
        # 限制下载数量
        if self.max_videos and len(new_videos) > self.max_videos:
            new_videos = new_videos[:self.max_videos]
            self.logger.info(f"限制下载数量: {len(new_videos)}")
        
        # 获取或创建合集目录
        if not collection_name:
            collection_name = self.get_collection_name(collection_url)
        
        collection_dir = self.output_dir / collection_name
        collection_dir.mkdir(exist_ok=True)
        
        # 下载新视频
        success_count = 0
        new_downloaded_ids = []
        
        for i, video in enumerate(new_videos, 1):
            self.logger.info(f"\n下载进度: {i}/{len(new_videos)}")
            self.logger.info(f"视频: {video['id']} - {video['title']}")
            
            if self.download_single_video(video, collection_dir, i):
                success_count += 1
                new_downloaded_ids.append(video['id'])
            
            # 随机延时
            if i < len(new_videos):
                delay = random.uniform(5, 10)
                self.logger.info(f"等待 {delay:.1f} 秒...")
                time.sleep(delay)
        
        # 更新已下载ID列表
        if new_downloaded_ids:
            self.update_downloaded_ids(new_downloaded_ids)
        
        self.logger.info(f"\n✅ 增量下载完成: {success_count}/{len(new_videos)} 个视频")
        
        # 如果有新下载的视频，运行目录管理器
        if success_count > 0:
            self.logger.info("运行目录管理器...")
            self.run_directory_manager(collection_dir)
        
        return success_count > 0
    
    def download_single_video(self, video, collection_dir, index):
        """下载单个视频"""
        video_id = video['id']
        video_url = f"https://www.bilibili.com/video/{video_id}"
        
        # 使用格式回退
        for format_selector in self.format_fallbacks:
            self.logger.info(f"  尝试格式: {format_selector}")
            
            if self.download_with_format(video_url, collection_dir, format_selector, index, video_id):
                self.logger.info(f"  ✓ 下载成功: {format_selector}")
                return True
            else:
                self.logger.warning(f"  格式失败: {format_selector}")
        
        self.logger.error(f"  ✗ 所有格式都失败")
        return False
    
    def download_with_format(self, video_url, collection_dir, format_selector, index, video_id):
        """使用指定格式下载"""
        try:
            cmd = self.get_yt_dlp_base_args() + [
                '--format', format_selector,
                '--output', f'{index:02d}_{video_id}.%(ext)s',
                '--write-info-json',
                '--write-thumbnail',
                '--convert-thumbnails', 'jpg',
                video_url
            ]
            
            result = subprocess.run(
                cmd, 
                cwd=collection_dir, 
                capture_output=True, 
                text=True,
                timeout=1800  # 30分钟超时
            )
            
            if result.returncode == 0:
                return True
            else:
                if "Requested format is not available" in result.stderr:
                    return False  # 格式不可用，尝试下一个
                else:
                    self.logger.warning(f"    下载失败: {result.stderr}")
                    return False
                
        except subprocess.TimeoutExpired:
            self.logger.warning(f"    下载超时")
            return False
        except Exception as e:
            self.logger.warning(f"    下载出错: {e}")
            return False
    
    def get_collection_name(self, collection_url):
        """获取合集名称"""
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
                                return self.sanitize_filename(collection_name)
                        except json.JSONDecodeError:
                            continue
            
            return "Unknown_Collection"
            
        except Exception as e:
            self.logger.warning(f"获取合集名称失败: {e}")
            return "Unknown_Collection"
    
    def update_downloaded_ids(self, new_ids):
        """更新已下载ID列表"""
        id_file = self.output_dir / 'downloaded_video_ids.txt'
        
        # 添加新ID到集合
        self.downloaded_ids.update(new_ids)
        
        # 保存到文件
        try:
            with open(id_file, 'w', encoding='utf-8') as f:
                for video_id in sorted(self.downloaded_ids):
                    f.write(f"{video_id}\n")
            
            self.logger.info(f"✓ 更新视频ID列表: 新增 {len(new_ids)} 个，总计 {len(self.downloaded_ids)} 个")
            
        except Exception as e:
            self.logger.error(f"更新视频ID列表失败: {e}")
    
    def run_directory_manager(self, collection_dir):
        """运行目录管理器"""
        try:
            # 导入目录管理器
            sys.path.append(str(Path(__file__).parent))
            from bilibili_directory_manager import BilibiliDirectoryManager
            
            manager = BilibiliDirectoryManager(collection_dir)
            manager.process_directory()
            
        except Exception as e:
            self.logger.warning(f"运行目录管理器失败: {e}")
            self.logger.info("请手动运行目录管理器:")
            self.logger.info(f"python bilibili_directory_manager.py \"{collection_dir}\"")
    
    def sanitize_filename(self, filename):
        """清理文件名"""
        if not filename or filename.strip() == '':
            return 'untitled'
        
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = re.sub(r'[\r\n\t]', ' ', filename)
        filename = re.sub(r'\s+', ' ', filename).strip()
        filename = filename.strip('. ')
        filename = re.sub(r'[【】\[\]()（）]', '', filename)
        filename = re.sub(r'[!！@#$%^&*+={}|;:,.<>?~`]', '_', filename)
        
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
    parser = argparse.ArgumentParser(
        description='B站合集增量下载器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
功能:
- 基于已有视频ID列表进行增量更新
- 只下载新视频，跳过已下载的视频
- 自动更新视频ID列表
- 下载完成后自动运行目录管理器

示例:
  %(prog)s "合集URL" "./downloads" --cookies "SESSDATA=xxx" --max-videos 5
        """
    )
    
    parser.add_argument('url', help='B站合集URL')
    parser.add_argument('output', help='输出根目录')
    parser.add_argument('--name', help='自定义合集名称')
    parser.add_argument('--max-videos', type=int, help='最大下载新视频数量')
    parser.add_argument('--quality', default='best', help='视频质量')
    parser.add_argument('--cookies', help='Cookie字符串或Cookie文件路径')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')
    
    args = parser.parse_args()
    
    if 'bilibili.com' not in args.url or 'lists' not in args.url:
        print("错误: 请提供有效的B站合集URL")
        sys.exit(1)
    
    downloader = BilibiliIncrementalDownloader(
        output_dir=args.output,
        max_videos=args.max_videos,
        quality=args.quality,
        cookies=args.cookies
    )
    
    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)
    
    print(f"🚀 B站合集增量下载器")
    print(f"  输出目录: {args.output}")
    print(f"  最大新视频数: {args.max_videos or '无限制'}")
    print(f"  视频质量: {args.quality}")
    print(f"  Cookie: {'✓' if args.cookies else '✗'}")
    print(f"  功能: 增量更新 + 自动管理")
    print()
    
    try:
        success = downloader.download_incremental(args.url, args.name)
        if success:
            print(f"\n✅ 增量下载完成!")
        else:
            print(f"\n❌ 增量下载失败!")
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

