#!/usr/bin/env python3
"""
V6视频检测服务模块
自动化检测和导入已有视频文件的标准服务
"""

import os
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from sqlalchemy.orm import Session
from .models import Video, get_db
from .services.subscription_stats import recompute_all_subscriptions
import os

logger = logging.getLogger(__name__)

class VideoDetectionService:
    """视频检测服务 - V6核心服务模块"""
    
    def __init__(self):
        self.download_path = Path(os.getenv('DOWNLOAD_PATH', '/app/downloads'))
        self.scan_interval = 300  # 5分钟扫描一次
        self.is_running = False
        self.last_scan_time = None
        
    async def start_service(self):
        """启动视频检测服务"""
        logger.info("🎬 启动V6视频检测服务...")
        self.is_running = True
        
        # 启动时立即执行一次完整扫描
        await self.full_scan()
        
        # 启动定期扫描任务
        asyncio.create_task(self.periodic_scan())
        
    async def stop_service(self):
        """停止视频检测服务"""
        logger.info("⏹️ 停止V6视频检测服务...")
        self.is_running = False
        
    async def full_scan(self):
        """完整扫描所有视频文件"""
        logger.info("🔍 开始完整视频扫描...")
        start_time = datetime.now()
        
        try:
            # 扫描所有视频文件
            video_pairs = await self._scan_video_files()
            
            # 导入到数据库
            imported_count = await self._import_videos(video_pairs)
            
            scan_duration = datetime.now() - start_time
            self.last_scan_time = datetime.now()
            
            logger.info(f"✅ 完整扫描完成: 发现{len(video_pairs)}个视频，导入{imported_count}个，耗时{scan_duration.total_seconds():.1f}秒")
            
            return {
                "status": "success",
                "videos_found": len(video_pairs),
                "videos_imported": imported_count,
                "scan_duration": scan_duration.total_seconds(),
                "last_scan_time": self.last_scan_time
            }
            
        except Exception as e:
            logger.error(f"❌ 完整扫描失败: {e}")
            return {
                "status": "error",
                "error": str(e),
                "last_scan_time": self.last_scan_time
            }
    
    async def incremental_scan(self):
        """增量扫描新增视频文件"""
        logger.info("🔄 开始增量视频扫描...")
        
        try:
            # 只扫描最近修改的文件
            cutoff_time = self.last_scan_time or (datetime.now() - timedelta(hours=1))
            video_pairs = await self._scan_video_files(since=cutoff_time)
            
            if not video_pairs:
                logger.info("📊 增量扫描: 未发现新视频文件")
                return {"status": "success", "videos_found": 0, "videos_imported": 0}
            
            # 导入新发现的视频
            imported_count = await self._import_videos(video_pairs)
            self.last_scan_time = datetime.now()
            
            logger.info(f"✅ 增量扫描完成: 发现{len(video_pairs)}个新视频，导入{imported_count}个")
            
            return {
                "status": "success",
                "videos_found": len(video_pairs),
                "videos_imported": imported_count,
                "last_scan_time": self.last_scan_time
            }
            
        except Exception as e:
            logger.error(f"❌ 增量扫描失败: {e}")
            return {"status": "error", "error": str(e)}
    
    async def periodic_scan(self):
        """定期扫描任务"""
        while self.is_running:
            try:
                await asyncio.sleep(self.scan_interval)
                if self.is_running:
                    await self.incremental_scan()
            except Exception as e:
                logger.error(f"❌ 定期扫描任务异常: {e}")
                await asyncio.sleep(60)  # 出错后等待1分钟再重试
    
    async def _scan_video_files(self, since: Optional[datetime] = None) -> List[Tuple[Path, Path, str]]:
        """扫描视频文件和对应的JSON元数据"""
        video_pairs = []
        
        # 支持的视频格式
        video_extensions = ['.mp4', '.mkv', '.flv', '.webm']
        
        for video_file in self.download_path.rglob("*"):
            if not video_file.is_file() or video_file.suffix.lower() not in video_extensions:
                continue
                
            # 如果指定了时间过滤，检查文件修改时间
            if since and datetime.fromtimestamp(video_file.stat().st_mtime) < since:
                continue
            
            # 查找对应的JSON文件
            json_file = await self._find_json_file(video_file)
            if not json_file:
                continue
                
            # 提取视频ID
            video_id = await self._extract_video_id(json_file)
            if video_id:
                video_pairs.append((video_file, json_file, video_id))
        
        return video_pairs
    
    async def _find_json_file(self, video_file: Path) -> Optional[Path]:
        """查找视频文件对应的JSON元数据文件"""
        # 策略1: 查找同名的.json文件
        potential_json = video_file.with_suffix('.json')
        if potential_json.exists():
            return potential_json
        
        # 策略2: 查找同名的.info.json文件
        potential_info_json = video_file.parent / f"{video_file.stem}.info.json"
        if potential_info_json.exists():
            return potential_info_json
            
        return None
    
    async def _extract_video_id(self, json_file: Path) -> Optional[str]:
        """从JSON文件中提取视频ID"""
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            if isinstance(metadata, dict) and 'id' in metadata:
                return metadata['id']
                
        except Exception as e:
            logger.warning(f"⚠️ JSON解析失败: {json_file.name} - {e}")
            
        return None
    
    async def _import_videos(self, video_pairs: List[Tuple[Path, Path, str]]) -> int:
        """将视频信息导入数据库"""
        imported_count = 0
        
        db = next(get_db())
        try:
            for video_file, json_file, video_id in video_pairs:
                # 检查是否已存在
                existing = db.query(Video).filter_by(bilibili_id=video_id).first()
                if existing:
                    continue
                
                # 读取完整元数据
                metadata = await self._load_metadata(json_file)
                if not metadata:
                    continue
                
                # 创建视频记录
                video = Video(
                    bilibili_id=video_id,
                    title=metadata.get('title', ''),
                    uploader=metadata.get('uploader', ''),
                    uploader_id=metadata.get('uploader_id', ''),
                    duration=metadata.get('duration', 0),
                    upload_date=self._parse_upload_date(metadata.get('upload_date')),
                    description=metadata.get('description', ''),
                    tags=json.dumps(metadata.get('tags', []), ensure_ascii=False),
                    video_path=str(video_file),
                    json_path=str(json_file),
                    thumbnail_path=self._find_thumbnail(video_file),
                    file_size=video_file.stat().st_size,
                    view_count=metadata.get('view_count', 0),
                    downloaded=True,
                    downloaded_at=datetime.fromtimestamp(video_file.stat().st_mtime)
                )
                
                db.add(video)
                imported_count += 1
                logger.debug(f"✅ 导入视频: {metadata.get('title', video_id)}")
            
            db.commit()
            # 导入完成后刷新所有订阅统计（检测服务无法可靠定位订阅归属时采用全量刷新）
            try:
                recompute_all_subscriptions(db, touch_last_check=False)
                db.commit()
            except Exception as e:
                db.rollback()
                logger.warning(f"刷新订阅统计失败(视频检测导入后)：{e}")
            
        except Exception as e:
            db.rollback()
            logger.error(f"❌ 数据库导入失败: {e}")
            raise
        finally:
            db.close()
            
        return imported_count
    
    async def _load_metadata(self, json_file: Path) -> Optional[dict]:
        """加载JSON元数据"""
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            return metadata if isinstance(metadata, dict) else None
        except Exception as e:
            logger.warning(f"⚠️ 元数据加载失败: {json_file.name} - {e}")
            return None
    
    def _parse_upload_date(self, upload_date_str: Optional[str]) -> Optional[datetime]:
        """解析上传日期"""
        if not upload_date_str:
            return None
            
        try:
            if len(upload_date_str) == 8:
                return datetime.strptime(upload_date_str, '%Y%m%d')
            else:
                return datetime.fromisoformat(upload_date_str.replace('Z', '+00:00'))
        except:
            return None
    
    def _find_thumbnail(self, video_file: Path) -> Optional[str]:
        """查找缩略图文件"""
        for ext in ['.jpg', '.jpeg', '.png', '.webp']:
            potential_thumb = video_file.with_suffix(ext)
            if potential_thumb.exists():
                return str(potential_thumb)
        return None
    
    def get_status(self) -> dict:
        """获取服务状态"""
        return {
            "is_running": self.is_running,
            "last_scan_time": self.last_scan_time,
            "scan_interval": self.scan_interval,
            "download_path": str(self.download_path)
        }

# 全局服务实例
video_detection_service = VideoDetectionService()
