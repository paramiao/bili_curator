"""
增强下载器 - 集成STRM模式支持

该模块扩展现有下载器，添加：
1. LOCAL/STRM模式自动识别
2. 统一的下载任务处理
3. 模式特定的下载逻辑
4. 错误处理和重试机制
"""

import asyncio
import logging
import subprocess
import json
import tempfile
import time
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session

from ..core.config import get_config
from ..core.exceptions import DownloadError, ValidationError
from ..models import Video, Subscription, DownloadTask
from ..schemas.subscription import SubscriptionResponse
from ..schemas.task import DownloadTaskResponse, TaskStatus
from ..services.strm_downloader import STRMDownloader
from ..services.strm_proxy_service import STRMProxyService
from ..services.strm_file_manager import STRMFileManager
from ..services.unified_cache_service import UnifiedCacheService
from ..downloader import BilibiliDownloaderV6
from ..cookie_manager import cookie_manager

logger = logging.getLogger(__name__)


class EnhancedDownloader:
    """增强下载器 - 支持LOCAL和STRM双模式"""
    
    def __init__(
        self,
        strm_proxy: STRMProxyService,
        strm_file_manager: STRMFileManager,
        cache_service: UnifiedCacheService
    ):
        self.config = get_config()
        
        # 存储STRM服务组件
        self.strm_proxy = strm_proxy
        self.file_manager = strm_file_manager
        self.cache_service = cache_service
        
        # 初始化LOCAL模式下载器
        self.local_downloader = BilibiliDownloaderV6()
        
        # 初始化STRM模式下载器
        self.strm_downloader = STRMDownloader(
            strm_proxy, strm_file_manager, cache_service
        )
        
        # 任务统计
        self.task_stats = {
            "local_tasks": 0,
            "strm_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0
        }
    
    async def process_download_task(
        self,
        task: DownloadTaskResponse,
        db: Session
    ) -> bool:
        """
        处理下载任务 - 自动识别模式
        
        Args:
            task: 下载任务
            db: 数据库会话
            
        Returns:
            是否处理成功
        """
        try:
            # 获取订阅信息确定下载模式
            subscription = db.query(Subscription).filter(
                Subscription.id == task.subscription_id
            ).first()
            
            if not subscription:
                raise ValidationError(f"订阅不存在: {task.subscription_id}")
            
            download_mode = str(getattr(subscription, 'download_mode', 'local')).lower()
            
            logger.info(f"处理下载任务: {task.bilibili_id}, 模式: {download_mode}")
            
            # 根据模式选择下载器
            if download_mode == 'strm':
                success = await self._process_strm_task(task, db)
                self.task_stats["strm_tasks"] += 1
            else:  # LOCAL模式
                success = await self._process_local_task(task, db)
                self.task_stats["local_tasks"] += 1
            
            # 更新统计
            if success:
                self.task_stats["completed_tasks"] += 1
            else:
                self.task_stats["failed_tasks"] += 1
            
            return success
            
        except Exception as e:
            logger.error(f"处理下载任务失败: {task.bilibili_id}, {e}")
            self.task_stats["failed_tasks"] += 1
            return False
    
    async def _process_strm_task(
        self,
        task: DownloadTaskResponse,
        db: Session
    ) -> bool:
        """处理STRM模式任务"""
        try:
            logger.info(f"开始STRM任务: {task.bilibili_id}")
            return await self.strm_downloader.process_strm_task(task, db)
            
        except Exception as e:
            logger.error(f"STRM任务处理失败: {task.bilibili_id}, {e}")
            return False
    
    async def _process_local_task(
        self,
        task: DownloadTaskResponse,
        db: Session
    ) -> bool:
        """处理LOCAL模式任务"""
        try:
            logger.info(f"开始LOCAL任务: {task.bilibili_id}")
            
            # 获取订阅信息
            subscription = db.query(Subscription).filter(
                Subscription.id == task.subscription_id
            ).first()
            
            if not subscription:
                return False
            
            # 构造视频信息（兼容现有下载器接口）
            video_info = {
                'id': task.bilibili_id,
                'title': task.title or task.bilibili_id,
                'uploader': getattr(task, 'uploader', ''),
                'duration': getattr(task, 'duration', 0)
            }
            
            # 调用现有下载器
            result = await self.local_downloader._download_single_video(
                video_info, task.subscription_id, db
            )
            
            return result.get('success', False)
            
        except Exception as e:
            logger.error(f"LOCAL任务处理失败: {task.bilibili_id}, {e}")
            return False
    
    async def download_subscription_videos(
        self,
        subscription_id: int,
        db: Session,
        mode_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        下载订阅视频 - 支持模式覆盖
        
        Args:
            subscription_id: 订阅ID
            db: 数据库会话
            mode_override: 模式覆盖 (LOCAL/STRM)
            
        Returns:
            下载结果统计
        """
        try:
            # 获取订阅信息
            subscription = db.query(Subscription).filter(
                Subscription.id == subscription_id
            ).first()
            
            if not subscription:
                raise ValidationError(f"订阅不存在: {subscription_id}")
            
            # 确定下载模式
            dm = mode_override or getattr(subscription, 'download_mode', 'local')
            download_mode = str(dm).lower()
            
            logger.info(f"开始下载订阅: {subscription.name}, 模式: {download_mode}")
            
            # 根据模式选择下载方法
            if download_mode == 'strm':
                return await self._download_subscription_strm(subscription, db)
            else:
                return await self._download_subscription_local(subscription, db)
            
        except Exception as e:
            logger.error(f"下载订阅失败: {subscription_id}, {e}")
            return {
                "success": False,
                "error": str(e),
                "total": 0,
                "completed": 0,
                "failed": 0
            }
    
    async def compute_pending_list(
        self,
        subscription: Subscription,
        db: Session
    ) -> Dict[str, Any]:
        """
        计算STRM订阅的待处理视频列表
        
        Args:
            subscription: 订阅对象
            db: 数据库会话
            
        Returns:
            包含远端总数、已存在数量、待处理数量和视频列表的字典
        """
        try:
            logger.info(f"计算STRM订阅待处理列表: {subscription.name}")
            
            # 根据订阅类型使用不同的逻辑获取远端视频列表
            if subscription.type == 'collection':
                # 合集订阅：使用传统下载器逻辑
                from ..downloader import downloader
                remote_info = await downloader.compute_pending_list(subscription.id, db)
            elif subscription.type == 'uploader':
                # UP主订阅：使用UP主解析服务获取视频列表
                remote_info = await self._get_uploader_videos(subscription, db)
            else:
                logger.error(f"不支持的订阅类型: {subscription.type}")
                return {
                    "subscription_id": subscription.id,
                    "remote_total": 0,
                    "existing": 0,
                    "pending": 0,
                    "created": 0,
                    "videos": []
                }
            
            # 处理获取到的视频列表
            remote_videos = remote_info.get('videos', [])
            existing_count = 0
            pending_videos = []
            
            # 检查每个视频是否已存在
            for video in remote_videos:
                existing_video = db.query(Video).filter(
                    Video.subscription_id == subscription.id,
                    Video.bilibili_id == video.get('bvid', video.get('id', '')),
                    Video.video_path.isnot(None)
                ).first()
                
                if existing_video:
                    existing_count += 1
                else:
                    pending_videos.append(video)
            
            # 为待处理视频生成STRM文件
            created_count = 0
            for video_data in pending_videos:
                try:
                    # 直接使用STRMFileManager的简化接口
                    bvid = video_data.get('bvid', video_data.get('id', ''))
                    title = video_data.get('title', '')
                    uploader = video_data.get('uploader', '')
                    thumbnail = video_data.get('thumbnail', '')
                    
                    # 生成流媒体URL
                    stream_url = f"http://localhost:8080/api/strm/stream/{bvid}"
                    
                    # 创建STRM文件和目录
                    strm_path = await self._create_strm_file_direct(
                        subscription, bvid, title, uploader, thumbnail, stream_url
                    )
                    
                    if strm_path:
                        created_count += 1
                        
                        # 更新数据库记录
                        video_record = Video(
                            bilibili_id=bvid,
                            title=title,
                            uploader=uploader,
                            video_path=str(strm_path),
                            downloaded=True,
                            subscription_id=subscription.id
                        )
                        db.add(video_record)
                        
                        logger.debug(f"已创建STRM文件: {title}")
                        
                except Exception as e:
                    logger.error(f"创建STRM文件失败: {video_data.get('title', 'Unknown')}, {e}")
            
            # 提交数据库更改
            try:
                db.commit()
            except Exception as e:
                logger.error(f"提交数据库更改失败: {e}")
                db.rollback()
            
            result = {
                "subscription_id": subscription.id,
                "remote_total": remote_info.get('remote_total', 0),
                "existing": existing_count,
                "pending": len(pending_videos),
                "created": created_count,
                "videos": pending_videos
            }
            
            logger.info(f"STRM订阅 {subscription.name}: 远端 {result['remote_total']}, 已有 {result['existing']}, 待处理 {result['pending']}, 已创建 {created_count}")
            return result
            
        except Exception as e:
            logger.error(f"计算STRM订阅待处理列表失败: {subscription.name}, {e}")
            return {
                "subscription_id": subscription.id,
                "remote_total": 0,
                "existing": 0,
                "pending": 0,
                "videos": []
            }
    
    async def _create_strm_file_direct(
        self, 
        subscription, 
        bvid: str, 
        title: str, 
        uploader: str, 
        thumbnail: str, 
        stream_url: str
    ):
        """
        直接创建STRM文件，避免复杂的Schema转换
        """
        try:
            import os
            import re
            from pathlib import Path
            
            # 获取STRM基础路径
            strm_base_path = Path("/app/strm")
            
            # 清理文件名
            def sanitize_filename(filename: str) -> str:
                if not filename:
                    return "未命名"
                filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
                filename = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', filename)
                if len(filename) > 100:
                    filename = filename[:97] + "..."
                return filename.strip(' .') or "未命名"
            
            # 根据订阅类型创建目录结构
            if subscription.type == "uploader":
                uploader_name = sanitize_filename(uploader or "未知UP主")
                video_title = sanitize_filename(title)
                video_dir = strm_base_path / uploader_name / video_title
            elif subscription.type == "collection":
                collection_name = sanitize_filename(subscription.name)
                video_title = sanitize_filename(title)
                video_dir = strm_base_path / collection_name / video_title
            else:
                video_title = sanitize_filename(title)
                video_dir = strm_base_path / "其他" / video_title
            
            # 创建目录
            video_dir.mkdir(parents=True, exist_ok=True)
            
            # 创建STRM文件
            safe_title = sanitize_filename(title)
            strm_path = video_dir / f"{safe_title}.strm"
            
            with open(strm_path, 'w', encoding='utf-8') as f:
                f.write(stream_url)
            
            # 下载缩略图
            thumb_path = None
            if thumbnail:
                try:
                    import aiohttp
                    thumb_path = video_dir / f"{safe_title}.jpg"
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.get(thumbnail) as response:
                            if response.status == 200:
                                with open(thumb_path, 'wb') as f:
                                    f.write(await response.read())
                                logger.debug(f"下载缩略图: {thumb_path}")
                            else:
                                thumb_path = None
                except Exception as e:
                    logger.warning(f"下载缩略图失败: {bvid}, {e}")
                    thumb_path = None
            
            # 创建NFO文件
            nfo_path = video_dir / f"{safe_title}.nfo"
            thumb_ref = f"{safe_title}.jpg" if thumb_path and thumb_path.exists() else (thumbnail or '')
            nfo_content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
    <title>{title}</title>
    <originaltitle>{title}</originaltitle>
    <director>{uploader or ''}</director>
    <studio>Bilibili</studio>
    <genre>网络视频</genre>
    <tag>Bilibili</tag>
    <tag>STRM</tag>
    <uniqueid type="bilibili">{bvid}</uniqueid>
    <thumb>{thumb_ref}</thumb>
    <fanart>{thumb_ref}</fanart>
</movie>"""
            
            with open(nfo_path, 'w', encoding='utf-8') as f:
                f.write(nfo_content)
            
            logger.info(f"创建STRM文件: {strm_path}")
            return strm_path
            
        except Exception as e:
            logger.error(f"直接创建STRM文件失败: {bvid}, {e}")
            return None
    
    async def _get_uploader_videos(self, subscription: Subscription, db: Session) -> Dict[str, Any]:
        """
        获取UP主的视频列表（用于STRM模式）
        
        Args:
            subscription: UP主订阅对象
            db: 数据库会话
            
        Returns:
            包含视频列表和总数的字典
        """
        from ..models import Cookie
        
        try:
            # 从URL中提取uploader_id
            uploader_id = None
            if subscription.url:
                # URL格式: https://space.bilibili.com/1233089467
                match = re.search(r'space\.bilibili\.com/(\d+)', subscription.url)
                if match:
                    uploader_id = match.group(1)
            
            if not uploader_id:
                logger.error(f"UP主订阅 {subscription.name} 无法从URL中提取uploader_id: {subscription.url}")
                return {"videos": [], "remote_total": 0}
            
            uploader_url = f"https://space.bilibili.com/{uploader_id}/video"
            logger.info(f"获取UP主视频列表: {subscription.name} ({uploader_url})")
            
            # 创建临时Cookie文件
            cookie_file_path = None
            try:
                active_cookie = cookie_manager.get_available_cookie(db)
                if active_cookie:
                    # 使用TAB分隔的Netscape格式
                    expire_time = int(time.time()) + 86400
                    cookie_content = f"""# Netscape HTTP Cookie File
.bilibili.com\tTRUE\t/\tFALSE\t{expire_time}\tSESSDATA\t{active_cookie.sessdata}
.bilibili.com\tTRUE\t/\tFALSE\t{expire_time}\tbili_jct\t{active_cookie.bili_jct}
.bilibili.com\tTRUE\t/\tFALSE\t{expire_time}\tDedeUserID\t{active_cookie.dedeuserid}
"""
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                        f.write(cookie_content)
                        cookie_file_path = f.name
                    logger.info(f"使用Cookie: {active_cookie.name}")
            except Exception as e:
                logger.warning(f"获取Cookie失败: {e}")
            
            # 第一步：使用flat-playlist获取视频ID列表
            cmd_list = [
                'yt-dlp',
                '--flat-playlist',
                '--playlist-end', '20',  # 限制获取前20个视频
                '--dump-json',
                '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                '--sleep-interval', '3',
                '--socket-timeout', '60',
                '--retries', '5',
                '--retry-sleep', '5',
                '--no-check-certificate',
                '--extractor-args', 'bilibili:space_video_sort=pubdate'  # 按发布时间排序
            ]
            
            if cookie_file_path:
                cmd_list.extend(['--cookies', cookie_file_path])
            cmd_list.append(uploader_url)
            
            # 获取视频ID列表
            logger.info(f"执行yt-dlp命令: {' '.join(cmd_list)}")
            result_list = subprocess.run(cmd_list, capture_output=True, text=True, timeout=120)
            
            if result_list.returncode != 0:
                logger.error(f"获取视频列表失败，返回码: {result_list.returncode}")
                logger.error(f"stderr: {result_list.stderr}")
                logger.error(f"stdout: {result_list.stdout}")
                if cookie_file_path and os.path.exists(cookie_file_path):
                    os.unlink(cookie_file_path)
                return {"videos": [], "remote_total": 0}
            
            # 解析视频ID
            video_ids = []
            for line in result_list.stdout.strip().split('\n'):
                if line.strip():
                    try:
                        video_info = json.loads(line)
                        if video_info.get('id'):
                            video_ids.append(video_info['id'])
                    except json.JSONDecodeError:
                        continue
            
            logger.info(f"获取到 {len(video_ids)} 个视频ID: {video_ids[:10]}...")
            
            if not video_ids:
                logger.warning(f"UP主 {subscription.name} 没有获取到任何视频ID")
                if cookie_file_path and os.path.exists(cookie_file_path):
                    os.unlink(cookie_file_path)
                return {"videos": [], "remote_total": 0}
            
            # 第二步：批量获取视频详细信息（优化策略）
            videos = []
            # 获取更多视频详情，提高STRM文件创建成功率
            batch_size = min(30, len(video_ids))  # 最多获取30个视频详情
            sample_ids = video_ids[:batch_size]
            logger.info(f"开始获取 {len(sample_ids)} 个视频的详细信息...")
            
            for i, video_id in enumerate(sample_ids):
                try:
                    video_url = f"https://www.bilibili.com/video/{video_id}"
                    cmd_detail = [
                        'yt-dlp',
                        '--dump-json',
                        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        '--sleep-interval', '1',  # 适度间隔
                        '--socket-timeout', '15'  # 设置超时
                    ]
                    
                    if cookie_file_path:
                        cmd_detail.extend(['--cookies', cookie_file_path])
                    cmd_detail.append(video_url)
                    
                    result_detail = subprocess.run(cmd_detail, capture_output=True, text=True, timeout=20)
                    
                    if result_detail.returncode == 0 and result_detail.stdout:
                        try:
                            video_info = json.loads(result_detail.stdout.strip())
                            # 只有当获取到有效标题时才添加视频
                            if video_info.get('title') and video_info.get('uploader'):
                                videos.append({
                                    'bvid': video_info.get('id', video_id),
                                    'title': video_info.get('title', ''),
                                    'uploader': video_info.get('uploader', ''),
                                    'duration': video_info.get('duration', 0),
                                    'upload_date': video_info.get('upload_date', ''),
                                    'view_count': video_info.get('view_count', 0),
                                    'thumbnail': video_info.get('thumbnail', '')
                                })
                                logger.info(f"获取视频详情 {i+1}/{len(sample_ids)}: {video_info.get('title', video_id)[:50]}...")
                            else:
                                logger.warning(f"视频 {video_id} 缺少标题或UP主信息，跳过")
                        except json.JSONDecodeError as e:
                            logger.error(f"解析视频 {video_id} 的JSON数据失败: {e}")
                    else:
                        logger.debug(f"获取视频详情失败: {video_id}, 返回码: {result_detail.returncode}")
                        if result_detail.stderr and 'ERROR' in result_detail.stderr:
                            logger.debug(f"错误信息: {result_detail.stderr[:100]}")
                        
                except subprocess.TimeoutExpired:
                    logger.warning(f"获取视频 {video_id} 超时，跳过")
                    continue
                except Exception as e:
                    logger.debug(f"处理视频 {video_id} 时出错: {e}")
                    continue
            
            # 清理临时Cookie文件
            if cookie_file_path and os.path.exists(cookie_file_path):
                os.unlink(cookie_file_path)
            
            logger.info(f"UP主 {subscription.name} 获取到 {len(videos)} 个详细视频信息，总计 {len(video_ids)} 个视频")
            
            # 关键修复：只返回有完整元数据的视频，避免创建空标题的STRM文件
            if not videos:
                logger.warning(f"UP主 {subscription.name} 没有获取到任何有效的视频详情")
                
                # 即使没有视频，也要确保创建UP主目录
                try:
                    uploader_name = subscription.name or "未知UP主"
                    uploader_dir = os.path.join("/app/strm", uploader_name)
                    os.makedirs(uploader_dir, exist_ok=True)
                    logger.info(f"已创建STRM UP主目录: {uploader_dir}")
                except Exception as e:
                    logger.error(f"创建STRM UP主目录失败: {e}")
                
                return {"videos": [], "remote_total": len(video_ids), "existing": 0, "pending": 0}
            
            # 只返回有完整元数据的视频列表
            logger.info(f"返回 {len(videos)} 个有效视频（跳过 {len(video_ids) - len(videos)} 个无效视频）")
            
            return {
                "videos": videos,  # 只返回有完整元数据的视频
                "remote_total": len(video_ids),
                "existing": 0,
                "pending": len(videos)  # pending数量为有效视频数量
            }
            
        except Exception as e:
            logger.error(f"获取UP主视频列表失败: {subscription.name}, {e}")
            if 'cookie_file_path' in locals() and cookie_file_path and os.path.exists(cookie_file_path):
                os.unlink(cookie_file_path)
            return {"videos": [], "remote_total": 0}
    
    async def _download_subscription_strm(
        self,
        subscription: Subscription,
        db: Session
    ) -> Dict[str, Any]:
        """STRM模式下载订阅"""
        try:
            # 使用现有的合集获取逻辑
            result = await self.local_downloader.download_collection(subscription.id, db)
            
            # 转换结果格式
            return {
                "success": True,
                "mode": "STRM",
                "subscription_id": subscription.id,
                "subscription_name": subscription.name,
                "total": result.get("total_videos", 0),
                "completed": result.get("successful_downloads", 0),
                "failed": result.get("failed_downloads", 0),
                "details": result
            }
            
        except Exception as e:
            logger.error(f"STRM模式下载订阅失败: {subscription.id}, {e}")
            return {
                "success": False,
                "mode": "STRM",
                "error": str(e),
                "total": 0,
                "completed": 0,
                "failed": 0
            }
    
    async def _download_subscription_local(
        self,
        subscription: Subscription,
        db: Session
    ) -> Dict[str, Any]:
        """LOCAL模式下载订阅"""
        try:
            # 使用现有的下载逻辑
            result = await self.local_downloader.download_collection(subscription.id, db)
            
            # 转换结果格式
            return {
                "success": True,
                "mode": "LOCAL",
                "subscription_id": subscription.id,
                "subscription_name": subscription.name,
                "total": result.get("total_videos", 0),
                "completed": result.get("successful_downloads", 0),
                "failed": result.get("failed_downloads", 0),
                "details": result
            }
            
        except Exception as e:
            logger.error(f"LOCAL模式下载订阅失败: {subscription.id}, {e}")
            return {
                "success": False,
                "mode": "LOCAL",
                "error": str(e),
                "total": 0,
                "completed": 0,
                "failed": 0
            }
    
    async def retry_failed_task(
        self,
        task_id: int,
        db: Session,
        max_retries: int = 3
    ) -> bool:
        """重试失败任务"""
        try:
            # 获取任务信息
            task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
            if not task:
                logger.error(f"任务不存在: {task_id}")
                return False
            
            # 转换为响应模型
            task_response = DownloadTaskResponse.from_orm(task)
            
            # 获取订阅信息确定模式
            subscription = db.query(Subscription).filter(
                Subscription.id == task.subscription_id
            ).first()
            
            if not subscription:
                logger.error(f"订阅不存在: {task.subscription_id}")
                return False
            
            download_mode = str(getattr(subscription, 'download_mode', 'local')).lower()
            
            # 根据模式重试
            if download_mode == 'strm':
                return await self.strm_downloader.retry_failed_strm_task(
                    task_response, db, max_retries
                )
            else:
                # LOCAL模式重试 - 重新处理任务
                return await self._process_local_task(task_response, db)
            
        except Exception as e:
            logger.error(f"重试任务失败: {task_id}, {e}")
            return False
    
    async def cleanup_task_resources(
        self,
        task_id: int,
        db: Session
    ) -> bool:
        """清理任务资源"""
        try:
            # 获取任务信息
            task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
            if not task:
                return False
            
            # 获取订阅信息确定模式
            subscription = db.query(Subscription).filter(
                Subscription.id == task.subscription_id
            ).first()
            
            if not subscription:
                return False
            
            download_mode = str(getattr(subscription, 'download_mode', 'local')).lower()
            
            # 根据模式清理资源
            if download_mode == 'strm':
                return await self.strm_downloader.cleanup_strm_task(
                    task.bilibili_id, task.subscription_id
                )
            else:
                # LOCAL模式清理 - 删除本地文件
                video = db.query(Video).filter(
                    Video.bilibili_id == task.bilibili_id
                ).first()
                
                if video and video.video_path:
                    try:
                        video_path = Path(video.video_path)
                        if video_path.exists():
                            video_path.unlink()
                        
                        # 删除相关文件
                        json_path = video_path.with_suffix('.info.json')
                        if json_path.exists():
                            json_path.unlink()
                        
                        thumb_path = video_path.with_suffix('.jpg')
                        if thumb_path.exists():
                            thumb_path.unlink()
                        
                        logger.info(f"清理LOCAL任务文件: {video_path}")
                        return True
                        
                    except Exception as e:
                        logger.error(f"清理LOCAL任务文件失败: {e}")
                        return False
                
                return True
            
        except Exception as e:
            logger.error(f"清理任务资源失败: {task_id}, {e}")
            return False
    
    def get_downloader_stats(self) -> Dict:
        """获取下载器统计信息"""
        try:
            # 获取STRM下载器统计
            strm_stats = self.strm_downloader.get_active_tasks_stats()
            
            return {
                "enhanced_downloader": {
                    "local_tasks": self.task_stats["local_tasks"],
                    "strm_tasks": self.task_stats["strm_tasks"],
                    "completed_tasks": self.task_stats["completed_tasks"],
                    "failed_tasks": self.task_stats["failed_tasks"]
                },
                "strm_downloader": strm_stats,
                "local_downloader": {
                    "concurrent_downloads": self.local_downloader.concurrent_downloads,
                    "output_dir": str(self.local_downloader.output_dir)
                }
            }
            
        except Exception as e:
            logger.error(f"获取下载器统计失败: {e}")
            return {"error": str(e)}
    
    async def validate_download_environment(self) -> Dict:
        """验证下载环境"""
        try:
            validation_result = {
                "local_environment": {
                    "download_path_exists": False,
                    "ytdlp_available": False
                },
                "strm_environment": {},
                "overall_status": "error"
            }
            
            # 检查LOCAL环境
            download_path = Path(self.config.download.download_host_path)
            validation_result["local_environment"]["download_path_exists"] = download_path.exists()
            
            # 检查yt-dlp
            try:
                import subprocess
                result = subprocess.run(
                    ["yt-dlp", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                validation_result["local_environment"]["ytdlp_available"] = result.returncode == 0
            except:
                validation_result["local_environment"]["ytdlp_available"] = False
            
            # 检查STRM环境
            validation_result["strm_environment"] = await self.strm_downloader.validate_strm_environment()
            
            # 综合状态
            local_ok = all(validation_result["local_environment"].values())
            strm_ok = validation_result["strm_environment"].get("overall_status") == "healthy"
            
            if local_ok and strm_ok:
                validation_result["overall_status"] = "healthy"
            elif local_ok or strm_ok:
                validation_result["overall_status"] = "partial"
            else:
                validation_result["overall_status"] = "error"
            
            return validation_result
            
        except Exception as e:
            logger.error(f"验证下载环境失败: {e}")
            return {
                "overall_status": "error",
                "error": str(e)
            }
