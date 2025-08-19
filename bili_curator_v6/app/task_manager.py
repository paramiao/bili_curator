"""
增强的下载任务管理器
"""
import asyncio
import json
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from sqlalchemy.orm import Session
from loguru import logger

from .models import DownloadTask, Video, Subscription, get_db
from .services.subscription_stats import record_recompute_event, maybe_try_recompute_all
from .downloader import downloader
from .queue_manager import get_subscription_lock

# 本地工具：BVID 校验与安全 URL 构造（避免非法ID拼接URL）
import re
def _is_bvid(vid: str) -> bool:
    try:
        return bool(vid) and bool(re.match(r'^BV[0-9A-Za-z]{10}$', str(vid)))
    except Exception:
        return False

def _safe_bilibili_url(vid: Optional[str]) -> Optional[str]:
    if not vid:
        return None
    return f"https://www.bilibili.com/video/{vid}" if _is_bvid(vid) else None

class TaskStatus(Enum):
    PENDING = "pending"
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class TaskProgress:
    task_id: str
    subscription_id: int
    subscription_name: str
    status: TaskStatus
    total_videos: int = 0
    new_videos: int = 0
    downloaded_videos: int = 0
    current_video: str = ""
    progress_percent: float = 0.0
    error_message: str = ""
    logs: List[str] = None
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def __post_init__(self):
        if self.logs is None:
            self.logs = []

class EnhancedTaskManager:
    def __init__(self):
        self.active_tasks: Dict[str, TaskProgress] = {}
        self.task_controls: Dict[str, asyncio.Event] = {}  # 用于暂停/恢复控制
        self.task_cancellations: Dict[str, bool] = {}  # 用于取消控制
    
    def _safe_log_title(self, video_info: Dict[str, Any]) -> str:
        """为日志生成更稳妥的标题：优先 title，其次 bilibili_id/id，最后 Unknown"""
        title = (video_info or {}).get('title')
        if title and str(title).strip().lower() not in ('', 'unknown', 'none', 'null'):
            return str(title).strip()
        # 兼容不同字段命名
        vid = (video_info or {}).get('bilibili_id') or (video_info or {}).get('id')
        return str(vid) if vid else 'Unknown'
    
    async def start_subscription_download(self, subscription_id: int) -> str:
        """启动订阅下载任务"""
        db = next(get_db())
        try:
            subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
            if not subscription:
                raise ValueError(f"订阅 {subscription_id} 不存在")
            
            # 检查是否已有运行中的任务
            existing_task = self._find_running_task_by_subscription(subscription_id)
            if existing_task:
                raise ValueError(f"订阅 {subscription.name} 已有运行中的任务")
            
            # 创建任务ID
            task_id = f"download_{subscription_id}_{int(datetime.now().timestamp())}"
            
            # 创建任务进度对象
            task_progress = TaskProgress(
                task_id=task_id,
                subscription_id=subscription_id,
                subscription_name=subscription.name,
                status=TaskStatus.PENDING,
                started_at=datetime.now(),
                updated_at=datetime.now()
            )
            
            # 创建控制事件
            self.task_controls[task_id] = asyncio.Event()
            self.task_controls[task_id].set()  # 默认为运行状态
            self.task_cancellations[task_id] = False
            
            # 注册任务
            self.active_tasks[task_id] = task_progress
            
            # 启动异步任务
            asyncio.create_task(self._run_download_task(task_id, db))
            
            logger.info(f"启动下载任务: {task_id} - {subscription.name}")
            return task_id
            
        finally:
            db.close()
    
    async def _run_download_task(self, task_id: str, db: Session):
        """运行下载任务"""
        task_progress = self.active_tasks[task_id]
        
        try:
            # 阶段1: 检查订阅
            await self._update_task_status(task_id, TaskStatus.CHECKING, "正在检查订阅...")
            
            subscription = db.query(Subscription).filter(
                Subscription.id == task_progress.subscription_id
            ).first()
            
            if not subscription:
                raise Exception("订阅不存在")
            
            # 阶段2: 获取视频列表
            await self._update_task_status(task_id, TaskStatus.CHECKING, "正在获取视频列表...")
            
            if subscription.type == 'collection':
                # 订阅级互斥，避免与 expected-total/下载并发外网请求
                sub_lock = get_subscription_lock(subscription.id)
                async with sub_lock:
                    video_list = await downloader._get_collection_videos(subscription.url, db, subscription_id=subscription.id)
            elif subscription.type == 'keyword':
                # 对于关键词订阅，从数据库中查找匹配的视频
                video_list = await self._get_keyword_videos(subscription, db)
            elif subscription.type == 'uploader':
                # TODO: 实现UP主订阅
                raise Exception(f"暂不支持 {subscription.type} 类型的订阅")
            else:
                raise Exception(f"未知的订阅类型: {subscription.type}")
            
            task_progress.total_videos = len(video_list)
            # 在获取远端列表后，记录期望总数并持久化同步时间
            try:
                subscription.expected_total = len(video_list)
                subscription.expected_total_synced_at = datetime.now()
                db.commit()
            except Exception:
                db.rollback()
            await self._update_task_log(task_id, f"发现 {len(video_list)} 个视频")
            
            # 阶段3: 检查重复视频（限定当前订阅目录，避免跨合集误判）
            await self._update_task_status(task_id, TaskStatus.CHECKING, "正在检查重复视频...")
            
            # 计算订阅目录（与下载器目录规则一致）
            try:
                sub_dir_path = downloader._create_subscription_directory(subscription)
            except Exception:
                sub_dir_path = None
            from pathlib import Path
            existing_videos = downloader._scan_existing_files(
                db,
                subscription_id=subscription.id,
                subscription_dir=Path(sub_dir_path) if sub_dir_path else None,
            )
            new_videos = []
            
            for video_info in video_list:
                video_id = video_info.get('id')
                if video_id not in existing_videos:
                    new_videos.append(video_info)
                else:
                    await self._update_task_log(task_id, f"跳过重复视频: {video_info.get('title', video_id)} ({video_id})")
            
            task_progress.new_videos = len(new_videos)
            await self._update_task_log(task_id, f"需要下载 {len(new_videos)} 个新视频")
            
            if len(new_videos) == 0:
                await self._update_task_status(task_id, TaskStatus.COMPLETED, "没有新视频需要下载")
                # 记录一次统计重算事件，并尝试按阈值触发全量重算
                try:
                    record_recompute_event(db)
                    maybe_try_recompute_all(db, max_events=20, max_age_seconds=300)
                    db.commit()
                except Exception:
                    db.rollback()
                return
            
            # 阶段4: 开始下载
            await self._update_task_status(task_id, TaskStatus.DOWNLOADING, "开始下载视频...")
            
            for i, video_info in enumerate(new_videos):
                # 检查是否被取消
                if self.task_cancellations.get(task_id, False):
                    await self._update_task_status(task_id, TaskStatus.CANCELLED, "任务已取消")
                    return
                
                # 等待暂停控制
                await self.task_controls[task_id].wait()
                
                # 更新当前下载视频（避免 Unknown 展示）
                video_title = self._safe_log_title(video_info)
                task_progress.current_video = video_title
                task_progress.progress_percent = (i / len(new_videos)) * 100
                await self._update_task_log(task_id, f"开始下载: {video_title}")
                
                try:
                    # 下载单个视频
                    result = await downloader._download_single_video(
                        video_info, subscription.id, db
                    )
                    
                    if result['success']:
                        task_progress.downloaded_videos += 1
                        # 优先使用下载结果返回的实际标题
                        done_title = result.get('title') or video_title
                        await self._update_task_log(task_id, f"下载完成: {done_title}")
                    else:
                        fail_title = result.get('title') or video_title
                        await self._update_task_log(task_id, f"下载失败: {fail_title} - {result.get('error', 'Unknown error')}")
                        
                except Exception as e:
                    err_title = self._safe_log_title(video_info)
                    await self._update_task_log(task_id, f"下载异常: {err_title} - {str(e)}")
            
            # 任务完成
            task_progress.progress_percent = 100.0
            await self._update_task_status(
                task_id, 
                TaskStatus.COMPLETED, 
                f"下载完成: {task_progress.downloaded_videos}/{task_progress.new_videos}"
            )
            # 记录一次统计重算事件，并尝试按阈值触发全量重算
            try:
                record_recompute_event(db)
                maybe_try_recompute_all(db, max_events=20, max_age_seconds=300)
                db.commit()
            except Exception:
                db.rollback()
            
        except Exception as e:
            logger.error(f"下载任务 {task_id} 失败: {e}")
            await self._update_task_status(task_id, TaskStatus.FAILED, str(e))
        
        finally:
            # 清理控制对象
            if task_id in self.task_controls:
                del self.task_controls[task_id]
            if task_id in self.task_cancellations:
                del self.task_cancellations[task_id]
    
    async def pause_task(self, task_id: str) -> bool:
        """暂停任务"""
        if task_id not in self.active_tasks:
            return False
        
        task_progress = self.active_tasks[task_id]
        if task_progress.status != TaskStatus.DOWNLOADING:
            return False
        
        if task_id in self.task_controls:
            self.task_controls[task_id].clear()
            await self._update_task_status(task_id, TaskStatus.PAUSED, "任务已暂停")
            logger.info(f"暂停任务: {task_id}")
            return True
        
        return False
    
    async def resume_task(self, task_id: str) -> bool:
        """恢复任务"""
        if task_id not in self.active_tasks:
            return False
        
        task_progress = self.active_tasks[task_id]
        if task_progress.status != TaskStatus.PAUSED:
            return False
        
        if task_id in self.task_controls:
            self.task_controls[task_id].set()
            await self._update_task_status(task_id, TaskStatus.DOWNLOADING, "任务已恢复")
            logger.info(f"恢复任务: {task_id}")
            return True
        
        return False
    
    async def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        if task_id not in self.active_tasks:
            return False
        
        task_progress = self.active_tasks[task_id]
        if task_progress.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            return False
        
        # 设置取消标志
        self.task_cancellations[task_id] = True
        
        # 如果任务被暂停，先恢复以便能够检查取消标志
        if task_id in self.task_controls:
            self.task_controls[task_id].set()
        
        logger.info(f"取消任务: {task_id}")
        return True
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        if task_id not in self.active_tasks:
            return None
        
        task_progress = self.active_tasks[task_id]
        return {
            "task_id": task_progress.task_id,
            "subscription_id": task_progress.subscription_id,
            "subscription_name": task_progress.subscription_name,
            "status": task_progress.status.value,
            "total_videos": task_progress.total_videos,
            "new_videos": task_progress.new_videos,
            "downloaded_videos": task_progress.downloaded_videos,
            "current_video": task_progress.current_video,
            "progress_percent": task_progress.progress_percent,
            "error_message": task_progress.error_message,
            "logs": task_progress.logs[-50:],  # 只返回最近50条日志
            "started_at": task_progress.started_at.isoformat() if task_progress.started_at else None,
            "updated_at": task_progress.updated_at.isoformat() if task_progress.updated_at else None
        }
    
    def get_all_tasks(self) -> Dict[str, Dict[str, Any]]:
        """获取所有任务状态"""
        return {
            task_id: self.get_task_status(task_id)
            for task_id in self.active_tasks
        }
    
    def get_subscription_tasks(self, subscription_id: int) -> List[Dict[str, Any]]:
        """获取指定订阅的所有任务"""
        tasks = []
        for task_progress in self.active_tasks.values():
            if task_progress.subscription_id == subscription_id:
                tasks.append(self.get_task_status(task_progress.task_id))
        return tasks
    
    async def _update_task_status(self, task_id: str, status: TaskStatus, message: str = ""):
        """更新任务状态"""
        if task_id in self.active_tasks:
            task_progress = self.active_tasks[task_id]
            task_progress.status = status
            task_progress.updated_at = datetime.now()
            
            if message:
                task_progress.error_message = message if status == TaskStatus.FAILED else ""
                await self._update_task_log(task_id, message)
    
    async def _update_task_log(self, task_id: str, message: str):
        """添加任务日志"""
        if task_id in self.active_tasks:
            task_progress = self.active_tasks[task_id]
            timestamp = datetime.now().strftime("%H:%M:%S")
            log_entry = f"[{timestamp}] {message}"
            task_progress.logs.append(log_entry)
            
            # 限制日志数量，避免内存占用过多
            if len(task_progress.logs) > 200:
                task_progress.logs = task_progress.logs[-100:]
            
            logger.info(f"任务 {task_id}: {message}")
    
    def _find_running_task_by_subscription(self, subscription_id: int) -> Optional[str]:
        """查找指定订阅的运行中任务"""
        for task_id, task_progress in self.active_tasks.items():
            if (task_progress.subscription_id == subscription_id and 
                task_progress.status in [TaskStatus.PENDING, TaskStatus.CHECKING, TaskStatus.DOWNLOADING, TaskStatus.PAUSED]):
                return task_id
        return None
    
    def cleanup_completed_tasks(self, hours: int = 24):
        """清理已完成的任务"""
        current_time = datetime.now()
        tasks_to_remove = []
        
        for task_id, task_progress in self.active_tasks.items():
            if (task_progress.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED] and
                task_progress.updated_at and 
                (current_time - task_progress.updated_at).total_seconds() > hours * 3600):
                tasks_to_remove.append(task_id)
        
        for task_id in tasks_to_remove:
            del self.active_tasks[task_id]
            logger.info(f"清理了 {len(tasks_to_remove)} 个过期任务")
    
    async def _get_keyword_videos(self, subscription: Subscription, db: Session) -> List[Dict[str, Any]]:
        """获取关键词匹配的视频列表"""
        from .models import Video
        
        # 从数据库中查找标题包含关键词的视频
        keyword = subscription.keyword
        if not keyword:
            return []
        
        # 查询匹配的视频
        videos = db.query(Video).filter(
            Video.title.contains(keyword)
        ).all()
        
        # 转换为下载器期望的格式
        video_list = []
        for video in videos:
            video_info = {
                'id': video.bilibili_id,
                'title': video.title,
                'uploader': video.uploader,
                'uploader_id': video.uploader_id,
                'duration': video.duration,
                'upload_date': video.upload_date.strftime('%Y%m%d') if video.upload_date else None,
                'view_count': video.view_count,
                'url': _safe_bilibili_url(video.bilibili_id),
                'webpage_url': _safe_bilibili_url(video.bilibili_id)
            }
            video_list.append(video_info)
        
        return video_list

# 全局任务管理器实例
enhanced_task_manager = EnhancedTaskManager()
