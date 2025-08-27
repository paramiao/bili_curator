"""
STRM下载器扩展 - 处理STRM模式的下载任务

该模块实现：
1. STRM模式的下载逻辑
2. 任务状态管理
3. 与代理服务和文件管理器的集成
4. 错误处理和重试机制
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from sqlalchemy.orm import Session

from ..core.config import get_config
from ..core.exceptions import DownloadError, ExternalAPIError, ValidationError
from ..models import Video, Subscription, DownloadTask
from ..schemas.video import VideoResponse
from ..schemas.subscription import SubscriptionResponse
from ..schemas.task import DownloadTaskResponse, TaskStatus, TaskType
from ..services.strm_proxy_service import STRMProxyService
from ..services.strm_file_manager import STRMFileManager
from ..services.unified_cache_service import UnifiedCacheService

logger = logging.getLogger(__name__)


class STRMDownloader:
    """STRM下载器 - 处理STRM模式的下载任务"""
    
    def __init__(
        self,
        strm_proxy: STRMProxyService,
        strm_file_manager: STRMFileManager,
        cache_service: UnifiedCacheService
    ):
        self.config = get_config()
        self.strm_proxy = strm_proxy
        self.strm_file_manager = strm_file_manager
        self.cache_service = cache_service
        self.active_tasks: Dict[str, Dict] = {}
        
    async def process_strm_task(
        self,
        task: DownloadTaskResponse,
        db: Session
    ) -> bool:
        """
        处理STRM下载任务
        
        Args:
            task: 下载任务信息
            db: 数据库会话
            
        Returns:
            是否处理成功
        """
        try:
            task_key = f"strm_{task.bilibili_id}"
            
            # 检查是否已在处理中
            if task_key in self.active_tasks:
                logger.warning(f"STRM任务已在处理中: {task.bilibili_id}")
                return False
            
            # 标记任务开始
            self.active_tasks[task_key] = {
                "task_id": task.id,
                "bilibili_id": task.bilibili_id,
                "started_at": datetime.now(),
                "status": "processing"
            }
            
            # 更新任务状态为处理中
            await self._update_task_status(
                task.id, TaskStatus.IN_PROGRESS, "开始处理STRM任务", db
            )
            
            # 获取视频和订阅信息
            video_info = await self._get_video_info(task.bilibili_id, db)
            subscription_info = await self._get_subscription_info(task.subscription_id, db)
            
            if not video_info or not subscription_info:
                raise DownloadError("获取视频或订阅信息失败")
            
            # 创建STRM流
            stream_url = await self.strm_proxy.get_video_stream_url(
                task.bilibili_id, 
                task.quality or "720p"
            )
            
            # 创建STRM文件
            strm_path = await self.strm_file_manager.create_strm_file(
                video_info, subscription_info, stream_url
            )
            
            # 更新视频记录
            await self._update_video_record(
                task.bilibili_id, str(strm_path), db
            )
            
            # 更新任务状态为完成
            await self._update_task_status(
                task.id, TaskStatus.COMPLETED, f"STRM文件创建成功: {strm_path}", db
            )
            
            # 更新缓存
            await self._update_cache_after_completion(task.bilibili_id, subscription_info.id)
            
            logger.info(f"STRM任务完成: {task.bilibili_id} -> {strm_path}")
            return True
            
        except ExternalAPIError as e:
            await self._handle_task_error(task.id, f"外部API错误: {str(e)}", db)
            return False
            
        except DownloadError as e:
            await self._handle_task_error(task.id, f"下载错误: {str(e)}", db)
            return False
            
        except Exception as e:
            await self._handle_task_error(task.id, f"未知错误: {str(e)}", db)
            return False
            
        finally:
            # 清理活跃任务
            if task_key in self.active_tasks:
                del self.active_tasks[task_key]
    
    async def _get_video_info(self, bilibili_id: str, db: Session) -> Optional[VideoResponse]:
        """获取视频信息"""
        try:
            video = db.query(Video).filter(Video.bilibili_id == bilibili_id).first()
            if video:
                return VideoResponse.from_orm(video)
            
            # 如果数据库中没有，尝试从B站API获取
            video_data = await self._fetch_video_from_bilibili(bilibili_id)
            if video_data:
                # 创建新的视频记录
                new_video = Video(
                    bilibili_id=bilibili_id,
                    title=video_data.get("title", ""),
                    uploader=video_data.get("uploader", ""),
                    duration=video_data.get("duration", 0),
                    pic=video_data.get("pic", ""),
                    desc=video_data.get("desc", ""),
                    pubdate=datetime.fromtimestamp(video_data.get("pubdate", 0)),
                    downloaded=False
                )
                db.add(new_video)
                db.commit()
                db.refresh(new_video)
                
                return VideoResponse.from_orm(new_video)
            
            return None
            
        except Exception as e:
            logger.error(f"获取视频信息失败: {bilibili_id}, {e}")
            return None
    
    async def _get_subscription_info(
        self, 
        subscription_id: int, 
        db: Session
    ) -> Optional[SubscriptionResponse]:
        """获取订阅信息"""
        try:
            subscription = db.query(Subscription).filter(
                Subscription.id == subscription_id
            ).first()
            
            if subscription:
                return SubscriptionResponse.from_orm(subscription)
            
            return None
            
        except Exception as e:
            logger.error(f"获取订阅信息失败: {subscription_id}, {e}")
            return None
    
    async def _fetch_video_from_bilibili(self, bilibili_id: str) -> Optional[Dict]:
        """从B站API获取视频信息"""
        try:
            # 使用代理服务的方法获取视频信息
            video_info = await self.strm_proxy._get_bilibili_video_info(bilibili_id)
            return video_info
            
        except Exception as e:
            logger.error(f"从B站获取视频信息失败: {bilibili_id}, {e}")
            return None
    
    async def _update_video_record(
        self, 
        bilibili_id: str, 
        strm_path: str, 
        db: Session
    ):
        """更新视频记录"""
        try:
            video = db.query(Video).filter(Video.bilibili_id == bilibili_id).first()
            if video:
                video.video_path = strm_path
                video.downloaded = True
                video.file_size = Path(strm_path).stat().st_size if Path(strm_path).exists() else 0
                db.commit()
                
        except Exception as e:
            logger.error(f"更新视频记录失败: {bilibili_id}, {e}")
    
    async def _update_task_status(
        self,
        task_id: int,
        status: TaskStatus,
        message: str,
        db: Session
    ):
        """更新任务状态"""
        try:
            task = db.query(DownloadTask).filter(DownloadTask.id == task_id).first()
            if task:
                task.status = status.value
                task.progress_message = message
                task.updated_at = datetime.now()
                
                if status == TaskStatus.COMPLETED:
                    task.completed_at = datetime.now()
                elif status == TaskStatus.FAILED:
                    task.failed_at = datetime.now()
                
                db.commit()
                
        except Exception as e:
            logger.error(f"更新任务状态失败: {task_id}, {e}")
    
    async def _handle_task_error(self, task_id: int, error_message: str, db: Session):
        """处理任务错误"""
        try:
            await self._update_task_status(
                task_id, TaskStatus.FAILED, error_message, db
            )
            logger.error(f"STRM任务失败: {task_id}, {error_message}")
            
        except Exception as e:
            logger.error(f"处理任务错误失败: {task_id}, {e}")
    
    async def _update_cache_after_completion(
        self, 
        bilibili_id: str, 
        subscription_id: int
    ):
        """任务完成后更新缓存"""
        try:
            # 清理相关缓存
            cache_keys = [
                f"video_{bilibili_id}",
                f"subscription_{subscription_id}_videos",
                f"subscription_{subscription_id}_stats",
                "global_stats"
            ]
            
            for key in cache_keys:
                await self.cache_service.delete(key)
            
            logger.debug(f"更新缓存完成: {bilibili_id}")
            
        except Exception as e:
            logger.error(f"更新缓存失败: {bilibili_id}, {e}")
    
    async def retry_failed_strm_task(
        self,
        task: DownloadTaskResponse,
        db: Session,
        max_retries: int = 3
    ) -> bool:
        """
        重试失败的STRM任务
        
        Args:
            task: 失败的任务
            db: 数据库会话
            max_retries: 最大重试次数
            
        Returns:
            是否重试成功
        """
        try:
            # 检查重试次数
            if task.retry_count >= max_retries:
                logger.warning(f"STRM任务重试次数超限: {task.bilibili_id}")
                return False
            
            # 增加重试次数
            db_task = db.query(DownloadTask).filter(DownloadTask.id == task.id).first()
            if db_task:
                db_task.retry_count += 1
                db.commit()
                
                # 更新任务对象
                task.retry_count = db_task.retry_count
            
            # 重新处理任务
            logger.info(f"重试STRM任务: {task.bilibili_id}, 第{task.retry_count}次")
            return await self.process_strm_task(task, db)
            
        except Exception as e:
            logger.error(f"重试STRM任务失败: {task.bilibili_id}, {e}")
            return False
    
    async def cleanup_strm_task(self, bilibili_id: str, subscription_id: int) -> bool:
        """
        清理STRM任务相关资源
        
        Args:
            bilibili_id: 视频ID
            subscription_id: 订阅ID
            
        Returns:
            是否清理成功
        """
        try:
            # 获取视频和订阅信息
            video_info = VideoResponse(bilibili_id=bilibili_id, title="", uploader="")
            subscription_info = SubscriptionResponse(id=subscription_id, name="", type="")
            
            # 删除STRM文件
            await self.strm_file_manager.delete_strm_file(video_info, subscription_info)
            
            # 清理缓存
            await self._update_cache_after_completion(bilibili_id, subscription_id)
            
            logger.info(f"清理STRM任务完成: {bilibili_id}")
            return True
            
        except Exception as e:
            logger.error(f"清理STRM任务失败: {bilibili_id}, {e}")
            return False
    
    def get_active_tasks_stats(self) -> Dict:
        """获取活跃任务统计"""
        return {
            "active_count": len(self.active_tasks),
            "tasks": [
                {
                    "task_id": info["task_id"],
                    "bilibili_id": info["bilibili_id"],
                    "started_at": info["started_at"].isoformat(),
                    "status": info["status"]
                }
                for info in self.active_tasks.values()
            ]
        }
    
    async def validate_strm_environment(self) -> Dict:
        """验证STRM环境配置"""
        try:
            validation_result = {
                "strm_path_exists": False,
                "ffmpeg_available": False,
                "proxy_service_healthy": False,
                "file_manager_healthy": False,
                "overall_status": "error"
            }
            
            # 检查STRM路径
            strm_path = Path(self.config.download.strm_host_path)
            validation_result["strm_path_exists"] = strm_path.exists()
            
            # 检查FFmpeg
            try:
                process = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await process.communicate()
                validation_result["ffmpeg_available"] = process.returncode == 0
            except:
                validation_result["ffmpeg_available"] = False
            
            # 检查代理服务
            try:
                stream_stats = self.strm_proxy.get_stream_stats()
                validation_result["proxy_service_healthy"] = True
            except:
                validation_result["proxy_service_healthy"] = False
            
            # 检查文件管理器
            try:
                file_stats = self.strm_file_manager.get_strm_stats()
                validation_result["file_manager_healthy"] = True
            except:
                validation_result["file_manager_healthy"] = False
            
            # 综合状态
            all_checks = [
                validation_result["strm_path_exists"],
                validation_result["ffmpeg_available"],
                validation_result["proxy_service_healthy"],
                validation_result["file_manager_healthy"]
            ]
            
            if all(all_checks):
                validation_result["overall_status"] = "healthy"
            elif any(all_checks):
                validation_result["overall_status"] = "partial"
            else:
                validation_result["overall_status"] = "error"
            
            return validation_result
            
        except Exception as e:
            logger.error(f"验证STRM环境失败: {e}")
            return {
                "overall_status": "error",
                "error": str(e)
            }
