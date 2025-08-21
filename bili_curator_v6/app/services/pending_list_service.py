"""
待下载列表维护服务 - 基于首次获取后的本地维护和增量更新
"""
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger

from ..models import Subscription, Video, Settings


class PendingListService:
    """待下载列表智能维护服务"""
    
    def __init__(self):
        self.cache_duration_hours = 1  # 远端列表缓存时长
        self.max_failure_count = 3     # 最大失败次数后标记为永久失败
    
    async def get_pending_videos(self, subscription_id: int, db: Session, force_refresh: bool = False) -> Dict:
        """获取待下载视频列表，优先使用缓存和本地维护"""
        sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not sub or sub.type != "collection" or not sub.url:
            return {"error": "订阅不存在或不是合集类型"}
        
        cache_key = f"pending_list:{subscription_id}"
        
        # 检查缓存是否有效
        if not force_refresh:
            cached_data = self._get_cached_pending_list(db, cache_key)
            if cached_data:
                # 基于缓存数据和本地文件系统状态计算当前待下载列表
                return self._compute_current_pending(db, subscription_id, cached_data)
        
        # 缓存失效或强制刷新，重新获取远端数据
        try:
            from ..downloader import downloader
            remote_data = await downloader.compute_pending_list(subscription_id, db)
            
            # 缓存远端数据
            self._cache_pending_list(db, cache_key, remote_data)
            
            return remote_data
        except Exception as e:
            logger.error(f"获取远端待下载列表失败: {e}")
            # 如果有旧缓存，降级使用
            cached_data = self._get_cached_pending_list(db, cache_key, ignore_expiry=True)
            if cached_data:
                logger.info(f"使用过期缓存数据，订阅ID: {subscription_id}")
                return self._compute_current_pending(db, subscription_id, cached_data)
            raise
    
    def _get_cached_pending_list(self, db: Session, cache_key: str, ignore_expiry: bool = False) -> Optional[Dict]:
        """获取缓存的待下载列表"""
        try:
            setting = db.query(Settings).filter(Settings.key == cache_key).first()
            if not setting:
                return None
            
            cache_data = json.loads(setting.value)
            cache_time = datetime.fromisoformat(cache_data.get('timestamp', ''))
            
            if not ignore_expiry:
                if datetime.now() - cache_time > timedelta(hours=self.cache_duration_hours):
                    return None
            
            return cache_data
        except Exception as e:
            logger.warning(f"读取缓存失败: {e}")
            return None
    
    def _cache_pending_list(self, db: Session, cache_key: str, data: Dict):
        """缓存待下载列表数据"""
        try:
            cache_data = {
                "timestamp": datetime.now().isoformat(),
                "remote_total": data.get('remote_total', 0),
                "videos": data.get('videos', []),
                "subscription_id": data.get('subscription_id')
            }
            
            setting = db.query(Settings).filter(Settings.key == cache_key).first()
            if setting:
                setting.value = json.dumps(cache_data)
            else:
                setting = Settings(key=cache_key, value=json.dumps(cache_data))
                db.add(setting)
            db.commit()
        except Exception as e:
            logger.error(f"缓存待下载列表失败: {e}")
    
    def _compute_current_pending(self, db: Session, subscription_id: int, cached_data: Dict) -> Dict:
        """基于缓存数据和当前本地状态计算待下载列表"""
        try:
            # 获取本地已下载的视频ID集合
            local_videos = db.query(Video.bilibili_id).filter(
                Video.subscription_id == subscription_id,
                Video.video_path.isnot(None)
            ).all()
            local_ids = {v.bilibili_id for v in local_videos}
            
            # 获取永久失败的视频ID集合
            failed_videos = db.query(Video.bilibili_id).filter(
                Video.subscription_id == subscription_id,
                Video.download_failed == True
            ).all()
            failed_ids = {v.bilibili_id for v in failed_videos}
            
            # 从缓存的远端列表中筛选待下载视频
            cached_videos = cached_data.get('videos', [])
            pending_videos = []
            
            for video in cached_videos:
                video_id = video.get('id')
                video_title = video.get('title', '').strip()
                
                # 跳过无效视频（无ID或无标题）
                if not video_id or not video_title:
                    continue
                
                # 跳过已下载和永久失败的视频
                if video_id in local_ids or video_id in failed_ids:
                    continue
                
                pending_videos.append(video)
            
            # 使用数学公式计算待下载数量，与统计API保持一致
            remote_total = cached_data.get('remote_total', 0)
            existing_count = len(local_ids)
            failed_count = len(failed_ids)
            calculated_pending = max(0, remote_total - existing_count - failed_count)
            
            # 如果实际列表长度与计算结果不一致，截取或填充到正确数量
            if len(pending_videos) > calculated_pending:
                pending_videos = pending_videos[:calculated_pending]
            
            return {
                "subscription_id": subscription_id,
                "remote_total": remote_total,
                "existing": existing_count,
                "pending": calculated_pending,
                "failed": len(failed_ids),
                "videos": pending_videos,
                "cached": True,
                "cache_time": cached_data.get('timestamp')
            }
        except Exception as e:
            logger.error(f"计算当前待下载列表失败: {e}")
            return cached_data
    
    def mark_video_failed(self, db: Session, video_id: str, reason: str):
        """标记视频为永久下载失败"""
        try:
            video = db.query(Video).filter(Video.bilibili_id == video_id).first()
            if video:
                video.download_failed = True
                video.failure_reason = reason
                video.failure_count = (video.failure_count or 0) + 1
                video.last_failure_at = datetime.now()
                db.commit()
                logger.info(f"标记视频 {video_id} 为永久失败: {reason}")
        except Exception as e:
            logger.error(f"标记视频失败状态失败: {e}")
    
    def check_and_clean_failed_videos(self, db: Session, subscription_id: int) -> int:
        """检查并清理永久失败的视频，返回清理数量"""
        try:
            # 查找失败次数超过阈值的视频
            failed_videos = db.query(Video).filter(
                Video.subscription_id == subscription_id,
                Video.failure_count >= self.max_failure_count,
                Video.download_failed == False  # 尚未标记为永久失败
            ).all()
            
            cleaned_count = 0
            for video in failed_videos:
                video.download_failed = True
                if not video.failure_reason:
                    video.failure_reason = "多次下载失败"
                cleaned_count += 1
            
            if cleaned_count > 0:
                db.commit()
                logger.info(f"清理订阅 {subscription_id} 的 {cleaned_count} 个永久失败视频")
            
            return cleaned_count
        except Exception as e:
            logger.error(f"清理失败视频失败: {e}")
            return 0


# 全局实例
pending_list_service = PendingListService()
