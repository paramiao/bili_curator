"""
V6下载核心 - 基于V5版本改进
"""
import asyncio
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from loguru import logger

from .models import Video, DownloadTask, Subscription, Settings, get_db
from .cookie_manager import cookie_manager, rate_limiter, simple_retry

class BilibiliDownloaderV6:
    def __init__(self, output_dir: str = None):
        # 从环境变量获取下载路径，默认为/app/downloads
        if output_dir is None:
            output_dir = os.getenv('DOWNLOAD_PATH', '/app/downloads')
        
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.concurrent_downloads = 3
        self.download_semaphore = asyncio.Semaphore(self.concurrent_downloads)
    
    async def download_collection(self, subscription_id: int, db: Session) -> Dict[str, Any]:
        """下载合集"""
        subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not subscription:
            raise ValueError(f"订阅 {subscription_id} 不存在")
        
        logger.info(f"开始下载合集: {subscription.name}")
        
        # 获取合集视频列表
        video_list = await self._get_collection_videos(subscription.url, db)
        
        # 扫描已有文件
        existing_videos = self._scan_existing_files(db)
        
        # 过滤需要下载的视频
        new_videos = []
        for video_info in video_list:
            video_id = video_info.get('id')
            if video_id not in existing_videos:
                new_videos.append(video_info)
        
        logger.info(f"发现 {len(new_videos)} 个新视频需要下载")
        
        # 创建下载任务
        download_results = []
        for video_info in new_videos:
            try:
                result = await self._download_single_video(video_info, subscription_id, db)
                download_results.append(result)
            except Exception as e:
                logger.error(f"下载视频 {video_info.get('title', 'Unknown')} 失败: {e}")
                download_results.append({
                    'video_id': video_info.get('id'),
                    'success': False,
                    'error': str(e)
                })
        
        # 更新订阅检查时间
        subscription.last_check = datetime.now()
        db.commit()
        
        return {
            'subscription_id': subscription_id,
            'total_videos': len(video_list),
            'new_videos': len(new_videos),
            'download_results': download_results
        }
    
    @simple_retry(max_retries=3)
    def _scan_existing_files(self, db: Session) -> Dict[str, Dict]:
        """扫描现有文件，返回已下载视频的映射"""
        existing_videos = {}
        
        # 从数据库中获取已有视频
        videos = db.query(Video).all()
        for video in videos:
            if video.video_path and os.path.exists(video.video_path):
                existing_videos[video.bilibili_id] = {
                    'video_path': video.video_path,
                    'title': video.title,
                    'duration': video.duration
                }
        
        return existing_videos
    
    async def _download_single_video(self, video_info: Dict, subscription_id: int, db: Session) -> Dict:
        """下载单个视频"""
        try:
            video_id = video_info.get('id')
            video_title = video_info.get('title', 'Unknown')
            video_url = video_info.get('url', video_info.get('webpage_url'))
            
            if not video_url:
                return {'success': False, 'error': '视频URL为空'}
            
            # 检查视频是否已存在
            existing_video = db.query(Video).filter(Video.bilibili_id == video_id).first()
            if existing_video and existing_video.video_path and os.path.exists(existing_video.video_path):
                return {'success': True, 'message': '视频已存在，跳过下载'}
            
            # 获取可用Cookie
            cookie = cookie_manager.get_available_cookie(db)
            if not cookie:
                return {'success': False, 'error': '没有可用的Cookie'}
            
            # 获取订阅信息，创建订阅目录
            subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
            if not subscription:
                return {'success': False, 'error': '订阅不存在'}
            
            # 创建订阅目录
            subscription_dir = self._create_subscription_directory(subscription)
            
            # 设置输出路径（在订阅目录下）
            safe_title = self._sanitize_filename(video_title)
            output_template = os.path.join(subscription_dir, f"{safe_title}.%(ext)s")
            
            # 构建yt-dlp命令
            cmd = [
                'yt-dlp',
                '--format', 'best[height<=1080]',
                '--output', output_template,
                '--write-info-json',
                '--write-thumbnail',
                '--convert-thumbnails', 'jpg',
                video_url
            ]
            
            # 添加Cookie
            cookie_str = f"SESSDATA={cookie.sessdata}; bili_jct={cookie.bili_jct}; DedeUserID={cookie.dedeuserid}"
            cmd.extend(['--add-header', f'Cookie:{cookie_str}'])
            
            # 执行下载
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                error_msg = stderr.decode('utf-8', errors='ignore')
                cookie_manager.handle_download_error(db, cookie.id, error_msg)
                return {'success': False, 'error': f'下载失败: {error_msg}'}
            
            # 查找下载的文件（在订阅目录下）
            video_path = None
            json_path = None
            thumbnail_path = None
            
            for ext in ['.mp4', '.mkv', '.webm']:
                potential_path = os.path.join(subscription_dir, f"{safe_title}{ext}")
                if os.path.exists(potential_path):
                    video_path = potential_path
                    break
            
            json_path = os.path.join(subscription_dir, f"{safe_title}.info.json")
            thumbnail_path = os.path.join(subscription_dir, f"{safe_title}.jpg")
            
            if not video_path or not os.path.exists(video_path):
                return {'success': False, 'error': '下载的视频文件未找到'}
            
            # 读取视频信息
            video_data = {}
            if os.path.exists(json_path):
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        video_data = json.load(f)
                except Exception as e:
                    logger.warning(f"读取视频信息文件失败: {e}")
            
            # 保存到数据库
            if existing_video:
                # 更新现有记录
                existing_video.title = video_title
                existing_video.video_path = video_path
                existing_video.json_path = json_path if os.path.exists(json_path) else None
                existing_video.thumbnail_path = thumbnail_path if os.path.exists(thumbnail_path) else None
                existing_video.duration = video_data.get('duration', 0)
                existing_video.view_count = video_data.get('view_count', 0)
                existing_video.upload_date = video_data.get('upload_date', '')
                existing_video.description = video_data.get('description', '')[:1000]  # 限制长度
                existing_video.downloaded_at = datetime.now()
            else:
                # 创建新记录
                new_video = Video(
                    bilibili_id=video_id,
                    title=video_title,
                    subscription_id=subscription_id,
                    video_path=video_path,
                    json_path=json_path if os.path.exists(json_path) else None,
                    thumbnail_path=thumbnail_path if os.path.exists(thumbnail_path) else None,
                    duration=video_data.get('duration', 0),
                    view_count=video_data.get('view_count', 0),
                    upload_date=video_data.get('upload_date', ''),
                    description=video_data.get('description', '')[:1000],
                    downloaded_at=datetime.now()
                )
                db.add(new_video)
            
            # 生成NFO文件
            nfo_path = os.path.join(self.output_dir, f"{safe_title}.nfo")
            self._generate_nfo_file(video_data, nfo_path)
            
            # 更新Cookie使用统计
            cookie_manager.update_cookie_usage(db, cookie.id)
            
            db.commit()
            
            return {
                'success': True, 
                'video_path': video_path,
                'title': video_title
            }
            
        except Exception as e:
            logger.error(f"下载视频失败: {e}")
            return {'success': False, 'error': str(e)}

    async def _get_collection_videos(self, collection_url: str, db: Session) -> List[Dict]:
        """获取合集视频列表"""
        await rate_limiter.wait()
        
        # 获取Cookie
        cookie = cookie_manager.get_available_cookie(db)
        if not cookie:
            raise Exception("没有可用的Cookie")
        
        headers = cookie_manager.get_cookie_headers(cookie)
        
        try:
            # 使用yt-dlp获取视频列表
            cmd = [
                'yt-dlp',
                '--dump-json',
                '--flat-playlist',
                collection_url
            ]
            
            # 添加自定义cookie
            cookie_str = f"SESSDATA={cookie.sessdata}; bili_jct={cookie.bili_jct}; DedeUserID={cookie.dedeuserid}"
            cmd.extend(['--add-header', f'Cookie:{cookie_str}'])
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                error_msg = stderr.decode('utf-8', errors='ignore')
                logger.error(f"yt-dlp获取视频列表失败: {error_msg}")
                raise Exception(f"获取视频列表失败: {error_msg}")
            
            # 解析输出
            video_list = []
            for line in stdout.decode('utf-8', errors='ignore').strip().split('\n'):
                if line.strip():
                    try:
                        video_info = json.loads(line)
                        video_list.append(video_info)
                    except json.JSONDecodeError:
                        continue
            
            # 更新Cookie使用统计
            cookie_manager.update_cookie_usage(db, cookie.id)
            
            logger.info(f"获取到 {len(video_list)} 个视频")
            return video_list
            
        except Exception as e:
            logger.error(f"获取合集视频列表失败: {e}")
            # 如果是认证错误，标记Cookie为不可用
            if "403" in str(e) or "401" in str(e):
                cookie_manager.mark_cookie_banned(db, cookie.id, str(e))
            raise
    
    def _scan_existing_files(self, db: Session) -> Dict[str, str]:
        """扫描已有文件，构建视频ID映射"""
        existing_videos = {}
        
        # 从数据库获取已下载的视频
        downloaded_videos = db.query(Video).filter(Video.downloaded == True).all()
        for video in downloaded_videos:
            existing_videos[video.bilibili_id] = video.video_path
        
        # 同时扫描文件系统中的JSON文件（兼容V5格式）
        for json_file in self.output_dir.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    video_id = data.get('id')
                    if video_id and video_id not in existing_videos:
                        # 检查对应的视频文件是否存在
                        base_name = json_file.stem
                        video_file = self.output_dir / f"{base_name}.mp4"
                        if video_file.exists():
                            existing_videos[video_id] = str(video_file)
            except Exception as e:
                logger.warning(f"读取JSON文件 {json_file} 失败: {e}")
        
        logger.info(f"发现 {len(existing_videos)} 个已存在的视频")
        return existing_videos
    
    async def _download_single_video(self, video_info: Dict[str, Any], subscription_id: int, db: Session) -> Dict[str, Any]:
        """下载单个视频"""
        async with self.download_semaphore:
            video_id = video_info.get('id')
            title = video_info.get('title', 'Unknown')
            
            logger.info(f"开始下载: {title} ({video_id})")
            
            # 创建下载任务记录
            task = DownloadTask(
                video_id=video_id,
                subscription_id=subscription_id,
                status='downloading',
                started_at=datetime.now()
            )
            db.add(task)
            db.commit()
            
            try:
                # 获取Cookie
                cookie = cookie_manager.get_available_cookie(db)
                if not cookie:
                    raise Exception("没有可用的Cookie")
                
                await rate_limiter.wait()
                
                # 生成安全的文件名
                safe_title = self._sanitize_filename(title)
                base_filename = f"{safe_title}"
                
                # 下载视频
                video_path = await self._download_with_ytdlp(
                    video_info.get('url', f"https://www.bilibili.com/video/{video_id}"),
                    base_filename,
                    cookie,
                    task.id,
                    db
                )
                
                # 创建NFO文件
                await self._create_nfo_file(video_info, base_filename)
                
                # 保存视频信息到数据库
                video = Video(
                    video_id=video_id,
                    title=title,
                    uploader=video_info.get('uploader', ''),
                    uploader_id=video_info.get('uploader_id', ''),
                    duration=video_info.get('duration'),
                    upload_date=self._parse_upload_date(video_info.get('upload_date')),
                    description=video_info.get('description', ''),
                    video_path=str(video_path),
                    downloaded=True,
                    subscription_id=subscription_id
                )
                db.add(video)
                
                # 更新任务状态
                task.status = 'completed'
                task.progress = 100.0
                task.completed_at = datetime.now()
                db.commit()
                
                # 更新Cookie使用统计
                cookie_manager.update_cookie_usage(db, cookie.id)
                
                logger.info(f"下载完成: {title}")
                return {
                    'video_id': video_id,
                    'title': title,
                    'success': True,
                    'video_path': str(video_path)
                }
                
            except Exception as e:
                logger.error(f"下载失败: {title} - {e}")
                
                # 更新任务状态
                task.status = 'failed'
                task.error_message = str(e)
                task.completed_at = datetime.now()
                db.commit()
                
                # 如果是认证错误，标记Cookie为不可用
                if "403" in str(e) or "401" in str(e):
                    if cookie:
                        cookie_manager.mark_cookie_banned(db, cookie.id, str(e))
                
                return {
                    'video_id': video_id,
                    'title': title,
                    'success': False,
                    'error': str(e)
                }
    
    async def _download_with_ytdlp(self, url: str, base_filename: str, cookie, task_id: int, db: Session) -> Path:
        """使用yt-dlp下载视频"""
        output_template = str(self.output_dir / f"{base_filename}.%(ext)s")
        
        cmd = [
            'yt-dlp',
            '--format', 'best[height<=1080]',
            '--output', output_template,
            '--write-info-json',
            '--write-thumbnail',
            '--convert-thumbnails', 'jpg',
            url
        ]
        
        # 添加Cookie
        cookie_str = f"SESSDATA={cookie.sessdata}; bili_jct={cookie.bili_jct}; DedeUserID={cookie.dedeuserid}"
        cmd.extend(['--add-header', f'Cookie:{cookie_str}'])
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='ignore')
            raise Exception(f"yt-dlp下载失败: {error_msg}")
        
        # 查找下载的视频文件
        video_file = self.output_dir / f"{base_filename}.mp4"
        if not video_file.exists():
            # 尝试其他可能的扩展名
            for ext in ['.webm', '.mkv', '.flv']:
                alt_file = self.output_dir / f"{base_filename}{ext}"
                if alt_file.exists():
                    video_file = alt_file
                    break
        
        if not video_file.exists():
            raise Exception("下载的视频文件未找到")
        
        return video_file
    
    async def _create_nfo_file(self, video_info: Dict[str, Any], base_filename: str):
        """创建NFO文件"""
        nfo_path = self.output_dir / f"{base_filename}.nfo"
        
        nfo_content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
    <title>{self._escape_xml(video_info.get('title', ''))}</title>
    <originaltitle>{self._escape_xml(video_info.get('title', ''))}</originaltitle>
    <plot>{self._escape_xml(video_info.get('description', ''))}</plot>
    <year>{self._extract_year(video_info.get('upload_date'))}</year>
    <premiered>{self._format_date(video_info.get('upload_date'))}</premiered>
    <studio>Bilibili</studio>
    <director>{self._escape_xml(video_info.get('uploader', ''))}</director>
    <runtime>{video_info.get('duration', 0)}</runtime>
    <id>{video_info.get('id', '')}</id>
    <uniqueid type="bilibili">{video_info.get('id', '')}</uniqueid>
</movie>"""
        
        with open(nfo_path, 'w', encoding='utf-8') as f:
            f.write(nfo_content)
    
    def _create_subscription_directory(self, subscription: Subscription) -> str:
        """创建订阅目录，返回目录路径"""
        from .models import Subscription
        
        # 根据订阅类型确定目录名
        if subscription.type == 'collection':
            # 合集订阅：使用订阅名称
            dir_name = self._sanitize_filename(subscription.name)
        elif subscription.type == 'keyword':
            # 关键词订阅：使用关键词
            dir_name = self._sanitize_filename(f"关键词_{subscription.keyword}")
        elif subscription.type == 'uploader':
            # UP主订阅：使用UP主名称
            dir_name = self._sanitize_filename(f"UP主_{subscription.name}")
        else:
            # 其他类型：使用订阅名称
            dir_name = self._sanitize_filename(subscription.name)
        
        # 创建目录路径
        subscription_dir = os.path.join(self.output_dir, dir_name)
        os.makedirs(subscription_dir, exist_ok=True)
        
        logger.info(f"创建订阅目录: {subscription_dir}")
        return subscription_dir
    
    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名，移除非法字符"""
        # 移除或替换非法字符
        illegal_chars = r'[<>:"/\\|?*]'
        filename = re.sub(illegal_chars, '_', filename)
        
        # 移除前后空格和点
        filename = filename.strip(' .')
        
        # 限制长度
        if len(filename) > 100:
            filename = filename[:100]
        
        return filename
    
    def _parse_upload_date(self, date_str: str) -> Optional[datetime]:
        """解析上传日期"""
        if not date_str:
            return None
        
        try:
            # yt-dlp通常返回YYYYMMDD格式
            if len(date_str) == 8 and date_str.isdigit():
                return datetime.strptime(date_str, '%Y%m%d')
            return datetime.fromisoformat(date_str)
        except:
            return None
    
    def _extract_year(self, date_str: str) -> str:
        """提取年份"""
        date_obj = self._parse_upload_date(date_str)
        return str(date_obj.year) if date_obj else ""
    
    def _format_date(self, date_str: str) -> str:
        """格式化日期为YYYY-MM-DD"""
        date_obj = self._parse_upload_date(date_str)
        return date_obj.strftime('%Y-%m-%d') if date_obj else ""
    
    def _escape_xml(self, text: str) -> str:
        """转义XML特殊字符"""
        if not text:
            return ""
        
        text = str(text)
        text = text.replace('&', '&amp;')
        text = text.replace('<', '&lt;')
        text = text.replace('>', '&gt;')
        text = text.replace('"', '&quot;')
        text = text.replace("'", '&apos;')
        return text

# 全局下载器实例
downloader = BilibiliDownloaderV6()
