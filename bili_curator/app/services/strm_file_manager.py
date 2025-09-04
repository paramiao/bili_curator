"""
STRM文件管理系统 - .strm文件生成和目录结构管理

该模块实现：
1. .strm文件生成和更新
2. 目录结构创建和维护
3. NFO元数据文件生成
4. 文件清理和同步
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import aiofiles

from ..core.config import get_config
from ..core.exceptions import DownloadError, ValidationError
from ..models import Video, Subscription
from ..schemas.video import VideoResponse
from ..schemas.subscription import SubscriptionResponse

logger = logging.getLogger(__name__)


class STRMFileManager:
    """STRM文件管理器 - 处理.strm文件和目录结构"""
    
    def __init__(self):
        self.config = get_config()
        self.strm_base_path = Path(self.config.download.strm_path)
        
    async def create_strm_file(
        self, 
        video: VideoResponse, 
        subscription: SubscriptionResponse,
        stream_url: str
    ) -> Path:
        """
        创建.strm文件
        
        Args:
            video: 视频信息
            subscription: 订阅信息
            stream_url: 流媒体URL
            
        Returns:
            .strm文件路径
        """
        try:
            # 创建目录结构
            video_dir = await self._create_video_directory(video, subscription)
            
            # 生成安全的文件名
            safe_title = self._sanitize_filename(video.title)
            strm_filename = f"{safe_title}.strm"
            strm_path = video_dir / strm_filename
            
            # 写入.strm文件
            async with aiofiles.open(strm_path, 'w', encoding='utf-8') as f:
                await f.write(stream_url)
            
            # 生成NFO文件
            await self._create_nfo_file(video, video_dir, safe_title)
            
            # 生成缩略图文件（如果有）
            if video.pic:
                await self._create_thumbnail_file(video, video_dir, safe_title)
            
            logger.info(f"创建STRM文件: {strm_path}")
            return strm_path
            
        except Exception as e:
            logger.error(f"创建STRM文件失败: {video.bilibili_id}, {e}")
            raise DownloadError(video.bilibili_id, f"创建STRM文件失败: {str(e)}")
    
    async def _create_video_directory(
        self, 
        video: VideoResponse, 
        subscription: SubscriptionResponse
    ) -> Path:
        """创建视频目录结构 - 扁平化结构与LOCAL模式保持一致"""
        try:
            # 使用扁平化目录结构，与LOCAL模式一致
            if subscription.type == "uploader":
                # UP主订阅: /strm/UP主名称/
                uploader_name = self._sanitize_filename(video.uploader or "未知UP主")
                video_dir = self.strm_base_path / uploader_name
                
            elif subscription.type == "collection":
                # 合集订阅: /strm/合集名称/
                collection_name = self._sanitize_filename(subscription.name)
                video_dir = self.strm_base_path / collection_name
                
            elif subscription.type == "keyword":
                # 关键词订阅: /strm/关键词/
                keyword = self._sanitize_filename(subscription.keyword or subscription.name)
                video_dir = self.strm_base_path / keyword
                
            else:  # specific_video
                # 特定视频: /strm/特定视频/
                video_dir = self.strm_base_path / "特定视频"
            
            # 创建目录
            video_dir.mkdir(parents=True, exist_ok=True)
            
            return video_dir
            
        except Exception as e:
            logger.error(f"创建视频目录失败: {video.bilibili_id}, {e}")
            raise DownloadError(video.bilibili_id, f"创建视频目录失败: {str(e)}")
    
    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名，移除非法字符"""
        if not filename:
            return "未命名"
        
        # 移除或替换非法字符
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', filename)
        
        # 限制长度
        if len(filename) > 100:
            filename = filename[:97] + "..."
        
        # 移除首尾空格和点
        filename = filename.strip(' .')
        
        return filename or "未命名"
    
    async def _create_nfo_file(
        self, 
        video: VideoResponse, 
        video_dir: Path, 
        safe_title: str
    ):
        """创建NFO元数据文件"""
        try:
            nfo_path = video_dir / f"{safe_title}.nfo"
            
            # 构建NFO内容
            nfo_content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
    <title>{video.title}</title>
    <originaltitle>{video.title}</originaltitle>
    <plot>{video.desc or ''}</plot>
    <year>{video.pubdate.year if video.pubdate else ''}</year>
    <premiered>{video.pubdate.strftime('%Y-%m-%d') if video.pubdate else ''}</premiered>
    <runtime>{video.duration or 0}</runtime>
    <director>{video.uploader or ''}</director>
    <studio>Bilibili</studio>
    <genre>网络视频</genre>
    <tag>Bilibili</tag>
    <tag>STRM</tag>
    <uniqueid type="bilibili">{video.bilibili_id}</uniqueid>
    <thumb>{video.pic or ''}</thumb>
    <fanart>{video.pic or ''}</fanart>
</movie>"""
            
            async with aiofiles.open(nfo_path, 'w', encoding='utf-8') as f:
                await f.write(nfo_content)
            
            logger.debug(f"创建NFO文件: {nfo_path}")
            
        except Exception as e:
            logger.error(f"创建NFO文件失败: {video.bilibili_id}, {e}")
    
    async def _create_thumbnail_file(
        self, 
        video: VideoResponse, 
        video_dir: Path, 
        safe_title: str
    ):
        """创建缩略图文件链接"""
        try:
            # 创建缩略图URL文件
            thumb_path = video_dir / f"{safe_title}.jpg.url"
            
            async with aiofiles.open(thumb_path, 'w', encoding='utf-8') as f:
                await f.write(video.pic)
            
            logger.debug(f"创建缩略图链接: {thumb_path}")
            
        except Exception as e:
            logger.error(f"创建缩略图文件失败: {video.bilibili_id}, {e}")
    
    async def update_strm_file(
        self, 
        video: VideoResponse, 
        subscription: SubscriptionResponse,
        new_stream_url: str
    ) -> bool:
        """
        更新现有.strm文件
        
        Args:
            video: 视频信息
            subscription: 订阅信息
            new_stream_url: 新的流媒体URL
            
        Returns:
            是否更新成功
        """
        try:
            # 查找现有.strm文件
            strm_path = await self._find_strm_file(video, subscription)
            if not strm_path:
                logger.warning(f"未找到STRM文件: {video.bilibili_id}")
                return False
            
            # 更新.strm文件内容
            async with aiofiles.open(strm_path, 'w', encoding='utf-8') as f:
                await f.write(new_stream_url)
            
            logger.info(f"更新STRM文件: {strm_path}")
            return True
            
        except Exception as e:
            logger.error(f"更新STRM文件失败: {video.bilibili_id}, {e}")
            return False
    
    async def _find_strm_file(
        self, 
        video: VideoResponse, 
        subscription: SubscriptionResponse
    ) -> Optional[Path]:
        """查找现有的.strm文件"""
        try:
            # 重建目录路径
            video_dir = await self._create_video_directory(video, subscription)
            
            # 查找.strm文件
            safe_title = self._sanitize_filename(video.title)
            strm_path = video_dir / f"{safe_title}.strm"
            
            if strm_path.exists():
                return strm_path
            
            # 如果精确匹配失败，尝试模糊匹配
            for file_path in video_dir.glob("*.strm"):
                return file_path
            
            return None
            
        except Exception as e:
            logger.error(f"查找STRM文件失败: {video.bilibili_id}, {e}")
            return None
    
    async def delete_strm_file(
        self, 
        video: VideoResponse, 
        subscription: SubscriptionResponse
    ) -> bool:
        """
        删除.strm文件和相关文件
        
        Args:
            video: 视频信息
            subscription: 订阅信息
            
        Returns:
            是否删除成功
        """
        try:
            # 查找.strm文件
            strm_path = await self._find_strm_file(video, subscription)
            if not strm_path:
                logger.warning(f"未找到要删除的STRM文件: {video.bilibili_id}")
                return False
            
            video_dir = strm_path.parent
            safe_title = strm_path.stem
            
            # 删除相关文件
            files_to_delete = [
                strm_path,  # .strm文件
                video_dir / f"{safe_title}.nfo",  # NFO文件
                video_dir / f"{safe_title}.jpg.url",  # 缩略图链接
            ]
            
            for file_path in files_to_delete:
                if file_path.exists():
                    file_path.unlink()
                    logger.debug(f"删除文件: {file_path}")
            
            # 如果目录为空，删除目录
            if video_dir.exists() and not any(video_dir.iterdir()):
                video_dir.rmdir()
                logger.debug(f"删除空目录: {video_dir}")
            
            logger.info(f"删除STRM文件: {strm_path}")
            return True
            
        except Exception as e:
            logger.error(f"删除STRM文件失败: {video.bilibili_id}, {e}")
            return False
    
    async def sync_strm_directory(self, subscription: SubscriptionResponse) -> Dict:
        """
        同步订阅的STRM目录
        
        Args:
            subscription: 订阅信息
            
        Returns:
            同步结果统计
        """
        try:
            stats = {
                "total_files": 0,
                "valid_files": 0,
                "orphaned_files": 0,
                "cleaned_files": 0
            }
            
            # 根据订阅类型确定搜索路径
            search_paths = await self._get_subscription_search_paths(subscription)
            
            for search_path in search_paths:
                if not search_path.exists():
                    continue
                
                # 扫描.strm文件
                for strm_file in search_path.rglob("*.strm"):
                    stats["total_files"] += 1
                    
                    # 检查文件是否有效
                    if await self._validate_strm_file(strm_file):
                        stats["valid_files"] += 1
                    else:
                        # 删除无效文件
                        await self._cleanup_invalid_strm_file(strm_file)
                        stats["orphaned_files"] += 1
                        stats["cleaned_files"] += 1
            
            logger.info(f"同步STRM目录完成: {subscription.name}, {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"同步STRM目录失败: {subscription.name}, {e}")
            return {"error": str(e)}
    
    async def _get_subscription_search_paths(
        self, 
        subscription: SubscriptionResponse
    ) -> List[Path]:
        """获取订阅的搜索路径"""
        search_paths = []
        
        if subscription.type == "uploader":
            # UP主订阅路径
            if subscription.uploader_name:
                uploader_name = self._sanitize_filename(subscription.uploader_name)
                search_paths.append(self.strm_base_path / uploader_name)
        
        elif subscription.type == "collection":
            # 合集订阅路径
            collection_name = self._sanitize_filename(subscription.name)
            search_paths.append(self.strm_base_path / collection_name)
        
        elif subscription.type == "keyword":
            # 关键词订阅路径
            keyword = self._sanitize_filename(subscription.keyword or subscription.name)
            search_paths.append(self.strm_base_path / keyword)
        
        else:  # specific_video
            # 特定视频路径
            search_paths.append(self.strm_base_path / "特定视频")
        
        return search_paths
    
    async def _validate_strm_file(self, strm_file: Path) -> bool:
        """验证.strm文件是否有效"""
        try:
            if not strm_file.exists():
                return False
            
            # 检查文件大小
            if strm_file.stat().st_size == 0:
                return False
            
            # 检查文件内容
            async with aiofiles.open(strm_file, 'r', encoding='utf-8') as f:
                content = await f.read()
            
            # 验证URL格式
            content = content.strip()
            if not content.startswith('http'):
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"验证STRM文件失败: {strm_file}, {e}")
            return False
    
    async def _cleanup_invalid_strm_file(self, strm_file: Path):
        """清理无效的STRM文件"""
        try:
            video_dir = strm_file.parent
            safe_title = strm_file.stem
            
            # 删除相关文件
            files_to_delete = [
                strm_file,
                video_dir / f"{safe_title}.nfo",
                video_dir / f"{safe_title}.jpg.url",
            ]
            
            for file_path in files_to_delete:
                if file_path.exists():
                    file_path.unlink()
            
            # 如果目录为空，删除目录
            if video_dir.exists() and not any(video_dir.iterdir()):
                video_dir.rmdir()
            
            logger.info(f"清理无效STRM文件: {strm_file}")
            
        except Exception as e:
            logger.error(f"清理无效STRM文件失败: {strm_file}, {e}")
    
    def get_strm_stats(self) -> Dict:
        """获取STRM文件统计信息"""
        try:
            stats = {
                "total_strm_files": 0,
                "total_directories": 0,
                "total_size": 0,
                "by_subscription_type": {}
            }
            
            if not self.strm_base_path.exists():
                return stats
            
            # 统计文件和目录
            for item in self.strm_base_path.rglob("*"):
                if item.is_file():
                    if item.suffix == ".strm":
                        stats["total_strm_files"] += 1
                    stats["total_size"] += item.stat().st_size
                elif item.is_dir():
                    stats["total_directories"] += 1
            
            return stats
            
        except Exception as e:
            logger.error(f"获取STRM统计失败: {e}")
            return {"error": str(e)}
