#!/usr/bin/env python3
"""
B站合集下载器 V5 - 全面改进版本
主要改进：
1. 智能增量下载 - 基于目录现有文件
2. 统一文件命名规则
3. 增强NFO文件内容
4. 改进下载策略和错误处理
5. 文件完整性验证
"""

import os
import sys
import json
import subprocess
import re
import time
import random
import argparse
import hashlib
from pathlib import Path
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET

class BilibiliCollectionDownloaderV5:
    def __init__(self, output_dir, max_videos=None, quality='best[height<=1080]', 
                 cookies=None, naming_strategy='title', max_workers=3):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_videos = max_videos
        self.quality = quality
        self.cookies = cookies
        self.naming_strategy = naming_strategy  # 'title' or 'id' (默认使用title)
        self.max_workers = max_workers
        
        # 设置日志
        self.setup_logging()
        
        # 改进的格式回退列表
        self.format_fallbacks = [
            'best[height<=1080][ext=mp4]',
            'best[height<=720][ext=mp4]',
            'best[height<=1080]',
            'best[height<=720]',
            'best[ext=mp4]',
            'bestvideo[height<=1080]+bestaudio[ext=m4a]/best[height<=1080]',
            'best',
        ]
        
        # 处理Cookie
        self.cookie_file = None
        if self.cookies:
            self.setup_cookies()
        
        # 已下载文件信息
        self.existing_files = self.scan_existing_files()
        
    def setup_logging(self):
        """设置日志记录"""
        log_file = self.output_dir / 'download_v5.log'
        
        # 创建logger
        self.logger = logging.getLogger('BilibiliDownloaderV5')
        self.logger.setLevel(logging.INFO)
        
        # 避免重复添加handler
        if not self.logger.handlers:
            # 文件handler
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            
            # 控制台handler
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            
            # 格式化
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)
            console_handler.setFormatter(formatter)
            
            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)
    
    def setup_cookies(self):
        """设置Cookie文件"""
        try:
            self.cookie_file = self.output_dir / 'temp_cookies.txt'
            
            # 创建Cookie文件内容
            cookie_content = "# Netscape HTTP Cookie File\n"
            
            if self.cookies.startswith('SESSDATA='):
                # 解析SESSDATA格式的Cookie
                sessdata = self.cookies.replace('SESSDATA=', '')
                cookie_content += f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{sessdata}\n"
            else:
                # 假设是完整的Cookie字符串
                for cookie in self.cookies.split(';'):
                    cookie = cookie.strip()
                    if '=' in cookie:
                        name, value = cookie.split('=', 1)
                        cookie_content += f".bilibili.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n"
            
            with open(self.cookie_file, 'w', encoding='utf-8') as f:
                f.write(cookie_content)
                
            self.logger.info(f"Cookie文件已创建: {self.cookie_file}")
            
        except Exception as e:
            self.logger.error(f"创建Cookie文件失败: {e}")
            self.cookie_file = None
    
    def scan_existing_files(self):
        """扫描现有文件，构建已下载视频的映射"""
        existing_files = {}
        
        self.logger.info("扫描现有文件...")
        
        # 首先收集所有JSON文件，从中提取视频ID
        json_files = {}
        for file_path in self.output_dir.iterdir():
            if file_path.is_file() and file_path.suffix == '.json':
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        video_id = data.get('id')
                        if video_id:
                            base_name = file_path.stem  # 不含扩展名的文件名
                            json_files[video_id] = base_name
                except Exception as e:
                    self.logger.warning(f"无法读取JSON文件 {file_path.name}: {e}")
        
        # 然后基于JSON文件中的ID来匹配其他文件
        for video_id, base_name in json_files.items():
            existing_files[video_id] = {}
            
            # 查找对应的文件
            for ext in ['.mp4', '.nfo', '.json', '.jpg']:
                file_path = self.output_dir / f"{base_name}{ext}"
                if file_path.exists():
                    if ext == '.mp4':
                        existing_files[video_id]['video'] = file_path
                    elif ext == '.nfo':
                        existing_files[video_id]['nfo'] = file_path
                    elif ext == '.json':
                        existing_files[video_id]['json'] = file_path
                    elif ext == '.jpg':
                        existing_files[video_id]['thumbnail'] = file_path
        
        self.logger.info(f"发现 {len(existing_files)} 个已下载的视频")
        return existing_files
    
    def extract_video_id_from_json(self, json_path):
        """从JSON文件中提取视频ID"""
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('id')
        except Exception as e:
            self.logger.warning(f"无法从JSON文件提取ID {json_path}: {e}")
            return None
    
    def sanitize_filename(self, filename, max_length=80):
        """清理文件名"""
        if not filename or filename.strip() == '':
            return 'untitled'
        
        # 移除或替换非法字符
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = re.sub(r'[\r\n\t]', ' ', filename)
        filename = re.sub(r'\s+', ' ', filename).strip()
        filename = filename.strip('. ')
        
        # 移除一些可能导致问题的字符
        filename = re.sub(r'[【】\[\]]', '', filename)
        filename = re.sub(r'[!@#$%^&*+={}|;,.<>?~`]', '_', filename)
        
        # 限制长度
        if len(filename) > max_length:
            filename = filename[:max_length].rsplit(' ', 1)[0]
        
        return filename or 'untitled'
    
    def create_enhanced_nfo(self, video_info, nfo_path):
        """创建增强的NFO文件"""
        try:
            title = video_info.get('title', 'Unknown')
            video_id = video_info.get('id', 'Unknown')
            uploader = video_info.get('uploader', 'Unknown')
            upload_date = video_info.get('upload_date', '')
            duration = video_info.get('duration', 0)
            description = video_info.get('description', '')
            tags = video_info.get('tags', [])
            view_count = video_info.get('view_count', 0)
            like_count = video_info.get('like_count', 0)
            webpage_url = video_info.get('webpage_url', '')
            
            # 格式化日期
            formatted_date = ''
            if upload_date:
                try:
                    if len(upload_date) == 8:  # YYYYMMDD格式
                        date_obj = datetime.strptime(upload_date, '%Y%m%d')
                        formatted_date = date_obj.strftime('%Y-%m-%d')
                except:
                    pass
            
            # 格式化时长
            runtime_minutes = duration // 60 if duration else 0
            
            # 清理描述
            clean_desc = description.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            if len(clean_desc) > 500:
                clean_desc = clean_desc[:500] + '...'
            
            # 创建NFO内容
            nfo_content = f'''<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<movie>
  <title>{title}</title>
  <sorttitle>{title}</sorttitle>
  <plot>{clean_desc}</plot>
  <outline>{clean_desc}</outline>
  <runtime>{runtime_minutes}</runtime>
  <year>{upload_date[:4] if upload_date else ''}</year>
  <studio>{uploader}</studio>
  <director>{uploader}</director>
  <credits>{uploader}</credits>
  <uniqueid type="bilibili">{video_id}</uniqueid>
  <dateadded>{formatted_date} 00:00:00</dateadded>
  <premiered>{formatted_date}</premiered>
  <playcount>{view_count}</playcount>
  <userrating>{min(10, like_count // 1000) if like_count else 0}</userrating>
  <trailer>{webpage_url}</trailer>'''
            
            # 添加标签
            for tag in tags[:10]:  # 限制标签数量
                clean_tag = tag.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                nfo_content += f'\n  <tag>{clean_tag}</tag>'
            
            nfo_content += '''
  <fileinfo>
    <streamdetails>
      <video>
        <codec>h264</codec>
        <aspect>16:9</aspect>
        <width>1920</width>
        <height>1080</height>
      </video>
      <audio>
        <codec>aac</codec>
        <language>zh</language>
        <channels>2</channels>
      </audio>
    </streamdetails>
  </fileinfo>
</movie>'''
            
            # 写入NFO文件
            with open(nfo_path, 'w', encoding='utf-8') as f:
                f.write(nfo_content)
            
            return True
            
        except Exception as e:
            self.logger.error(f"创建NFO文件失败: {e}")
            return False
    
    def get_collection_info(self, collection_url):
        """获取合集信息"""
        self.logger.info("获取合集信息...")
        
        cmd = ['yt-dlp', '--flat-playlist', '--dump-json']
        
        if self.cookie_file and self.cookie_file.exists():
            cmd.extend(['--cookies', str(self.cookie_file)])
        
        cmd.append(collection_url)
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                self.logger.error(f"获取合集信息失败: {result.stderr}")
                return None, []
            
            videos = []
            collection_title = None
            
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    try:
                        video_info = json.loads(line)
                        
                        # 获取合集标题
                        if not collection_title and 'playlist_title' in video_info:
                            collection_title = video_info['playlist_title']
                        
                        # 收集视频信息
                        if video_info.get('_type') == 'url':
                            videos.append({
                                'id': video_info.get('id'),
                                'title': video_info.get('title'),
                                'url': video_info.get('url'),
                                'webpage_url': video_info.get('webpage_url')
                            })
                    except json.JSONDecodeError:
                        continue
            
            self.logger.info(f"合集: {collection_title}, 视频数量: {len(videos)}")
            return collection_title, videos
            
        except subprocess.TimeoutExpired:
            self.logger.error("获取合集信息超时")
            return None, []
        except Exception as e:
            self.logger.error(f"获取合集信息出错: {e}")
            return None, []
    
    def is_video_complete(self, video_id):
        """检查视频文件是否完整"""
        if video_id not in self.existing_files:
            return False
        
        files = self.existing_files[video_id]
        
        # 检查必需文件是否存在
        required_files = ['video', 'json']
        for file_type in required_files:
            if file_type not in files or not files[file_type].exists():
                return False
        
        # 检查视频文件大小（简单的完整性检查）
        video_file = files['video']
        if video_file.stat().st_size < 1024:  # 小于1KB认为不完整
            return False
        
        return True
    
    def download_single_video(self, video_info, collection_dir):
        """下载单个视频"""
        video_id = video_info['id']
        video_title = video_info['title']
        video_url = video_info.get('webpage_url', video_info.get('url'))
        
        self.logger.info(f"处理视频: {video_title} ({video_id})")
        
        # 检查是否已下载且完整
        if self.is_video_complete(video_id):
            self.logger.info(f"视频已存在且完整，跳过: {video_id}")
            return True
        
        # 确定文件名
        if self.naming_strategy == 'id':
            base_filename = video_id
        else:
            base_filename = self.sanitize_filename(video_title)
        
        # 下载视频
        success = False
        for format_selector in self.format_fallbacks:
            try:
                cmd = [
                    'yt-dlp',
                    '--format', format_selector,
                    '--write-info-json',
                    '--write-thumbnail',
                    '--output', str(collection_dir / f"{base_filename}.%(ext)s")
                ]
                
                if self.cookie_file and self.cookie_file.exists():
                    cmd.extend(['--cookies', str(self.cookie_file)])
                
                cmd.append(video_url)
                
                self.logger.info(f"尝试格式: {format_selector}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0:
                    self.logger.info(f"下载成功: {video_title}")
                    success = True
                    break
                else:
                    self.logger.warning(f"格式 {format_selector} 失败: {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                self.logger.error(f"下载超时: {video_title}")
            except Exception as e:
                self.logger.error(f"下载出错: {e}")
        
        if not success:
            self.logger.error(f"所有格式都失败: {video_title}")
            return False
        
        # 创建增强的NFO文件
        try:
            info_json_path = collection_dir / f"{base_filename}.info.json"
            nfo_path = collection_dir / f"{base_filename}.nfo"
            
            if info_json_path.exists():
                with open(info_json_path, 'r', encoding='utf-8') as f:
                    detailed_info = json.load(f)
                
                self.create_enhanced_nfo(detailed_info, nfo_path)
                self.logger.info(f"NFO文件已创建: {nfo_path.name}")
        
        except Exception as e:
            self.logger.error(f"创建NFO文件失败: {e}")
        
        return success
    
    def download_collection(self, collection_url, custom_collection_name=None):
        """下载整个合集"""
        self.logger.info(f"开始下载合集: {collection_url}")
        
        # 获取合集信息
        collection_title, videos = self.get_collection_info(collection_url)
        
        if not videos:
            self.logger.error("无法获取合集视频列表")
            return False
        
        # 确定合集目录名
        if custom_collection_name:
            collection_name = self.sanitize_filename(custom_collection_name)
        elif collection_title:
            collection_name = self.sanitize_filename(collection_title)
        else:
            collection_name = "Unknown_Collection"
        
        collection_dir = self.output_dir / collection_name
        collection_dir.mkdir(exist_ok=True)
        
        self.logger.info(f"合集目录: {collection_dir}")
        
        # 限制视频数量
        if self.max_videos:
            videos = videos[:self.max_videos]
        
        # 保存视频详情
        video_details_path = collection_dir / 'video_details.json'
        try:
            with open(video_details_path, 'w', encoding='utf-8') as f:
                json.dump(videos, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"保存视频详情失败: {e}")
        
        # 并发下载
        success_count = 0
        total_count = len(videos)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交下载任务
            future_to_video = {
                executor.submit(self.download_single_video, video, collection_dir): video
                for video in videos
            }
            
            # 处理完成的任务
            for future in as_completed(future_to_video):
                video = future_to_video[future]
                try:
                    if future.result():
                        success_count += 1
                    
                    self.logger.info(f"进度: {success_count}/{total_count}")
                    
                except Exception as e:
                    self.logger.error(f"下载任务异常: {e}")
        
        self.logger.info(f"下载完成: {success_count}/{total_count} 成功")
        
        # 清理Cookie文件
        if self.cookie_file and self.cookie_file.exists():
            try:
                self.cookie_file.unlink()
                self.logger.info("临时Cookie文件已清理")
            except:
                pass
        
        return success_count > 0

def main():
    parser = argparse.ArgumentParser(description='B站合集下载器 V5')
    parser.add_argument('url', help='合集URL')
    parser.add_argument('output_dir', help='输出目录')
    parser.add_argument('--max-videos', type=int, help='最大下载视频数')
    parser.add_argument('--quality', default='best[height<=1080]', help='视频质量')
    parser.add_argument('--cookies', help='Cookie字符串')
    parser.add_argument('--naming', choices=['title', 'id'], default='title', 
                       help='文件命名策略: title(标题，推荐) 或 id(视频ID)')
    parser.add_argument('--collection-name', help='自定义合集名称')
    parser.add_argument('--max-workers', type=int, default=3, help='并发下载数')
    
    args = parser.parse_args()
    
    try:
        downloader = BilibiliCollectionDownloaderV5(
            output_dir=args.output_dir,
            max_videos=args.max_videos,
            quality=args.quality,
            cookies=args.cookies,
            naming_strategy=args.naming,
            max_workers=args.max_workers
        )
        
        success = downloader.download_collection(args.url, args.collection_name)
        
        if success:
            print("✅ 下载完成!")
            sys.exit(0)
        else:
            print("❌ 下载失败!")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ 程序异常: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
