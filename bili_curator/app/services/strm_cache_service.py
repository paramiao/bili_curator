"""
STRM视频按需缓存服务
实现访问时下载、本地文件服务、缓存管理
"""
import os
import asyncio
import aiofiles
import aiohttp
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import logging
import hashlib

from ..core.config import get_settings
from ..cookie_manager import SimpleCookieManager

logger = logging.getLogger(__name__)

class STRMCacheService:
    """STRM视频按需缓存服务"""
    
    def __init__(self):
        self.settings = get_settings()
        self.cache_dir = Path(self.settings.download.strm_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_manager = SimpleCookieManager()
        
        # 缓存配置
        self.max_cache_size_gb = 10  # 最大缓存10GB
        self.cache_expire_hours = 24  # 24小时过期
        self.download_timeout = 300  # 5分钟下载超时
        
        # 下载状态跟踪
        self._downloading: Dict[str, asyncio.Task] = {}
        self._download_progress: Dict[str, float] = {}
    
    def _get_cache_path(self, bilibili_id: str) -> Path:
        """获取缓存文件路径"""
        # 使用bilibili_id作为文件名，避免特殊字符
        safe_filename = f"{bilibili_id}.mp4"
        return self.cache_dir / safe_filename
    
    def _get_temp_path(self, bilibili_id: str) -> Path:
        """获取临时下载文件路径"""
        return self.cache_dir / f"{bilibili_id}.tmp"
    
    async def is_cached(self, bilibili_id: str) -> bool:
        """检查视频是否已缓存且有效"""
        cache_path = self._get_cache_path(bilibili_id)
        
        if not cache_path.exists():
            return False
            
        # 检查文件是否过期
        file_time = datetime.fromtimestamp(cache_path.stat().st_mtime)
        if datetime.now() - file_time > timedelta(hours=self.cache_expire_hours):
            # 异步删除过期文件
            try:
                cache_path.unlink()
            except:
                pass
            return False
            
        return True
    
    async def get_cached_file_path(self, bilibili_id: str) -> Optional[Path]:
        """获取缓存文件路径（如果存在）"""
        if await self.is_cached(bilibili_id):
            return self._get_cache_path(bilibili_id)
        return None
    
    async def is_downloading(self, bilibili_id: str) -> bool:
        """检查是否正在下载"""
        task = self._downloading.get(bilibili_id)
        return task is not None and not task.done()
    
    async def get_download_progress(self, bilibili_id: str) -> float:
        """获取下载进度 (0.0-1.0)"""
        return self._download_progress.get(bilibili_id, 0.0)
    
    async def download_video(self, bilibili_id: str, video_url: str) -> Path:
        """下载视频到缓存"""
        cache_path = self._get_cache_path(bilibili_id)
        temp_path = self._get_temp_path(bilibili_id)
        
        # 如果已经在下载，等待完成
        if bilibili_id in self._downloading:
            await self._downloading[bilibili_id]
            return cache_path
        
        # 创建下载任务
        download_task = asyncio.create_task(
            self._download_file(bilibili_id, video_url, temp_path, cache_path)
        )
        self._downloading[bilibili_id] = download_task
        
        try:
            await download_task
            return cache_path
        finally:
            # 清理下载状态
            self._downloading.pop(bilibili_id, None)
            self._download_progress.pop(bilibili_id, None)
    
    async def _download_file(self, bilibili_id: str, video_url: str, 
                           temp_path: Path, cache_path: Path):
        """实际下载文件的内部方法"""
        logger.info(f"开始下载视频缓存: {bilibili_id}")
        
        # 获取Cookie
        cookies = await self.cookie_manager.get_valid_cookies_dict()
        
        timeout = aiohttp.ClientTimeout(
            total=self.download_timeout,
            connect=30,
            sock_read=60
        )
        
        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                cookies=cookies,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                    'Referer': f'https://www.bilibili.com/video/{bilibili_id}',
                }
            ) as session:
                
                async with session.get(video_url) as response:
                    if response.status != 200:
                        raise Exception(f"下载失败: HTTP {response.status}")
                    
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = 0
                    
                    # 写入临时文件
                    async with aiofiles.open(temp_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
                            downloaded += len(chunk)
                            
                            # 更新进度
                            if total_size > 0:
                                progress = downloaded / total_size
                                self._download_progress[bilibili_id] = progress
            
            # 下载完成，移动到正式位置
            temp_path.rename(cache_path)
            logger.info(f"视频缓存下载完成: {bilibili_id} ({downloaded} bytes)")
            
        except Exception as e:
            logger.error(f"下载视频缓存失败 {bilibili_id}: {e}")
            # 清理临时文件
            if temp_path.exists():
                temp_path.unlink()
            raise
    
    async def cleanup_cache(self):
        """清理过期和超量缓存"""
        try:
            cache_files = list(self.cache_dir.glob("*.mp4"))
            
            # 按修改时间排序
            cache_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            
            total_size = 0
            expired_files = []
            
            now = datetime.now()
            max_size_bytes = self.max_cache_size_gb * 1024 * 1024 * 1024
            
            for cache_file in cache_files:
                file_stat = cache_file.stat()
                file_time = datetime.fromtimestamp(file_stat.st_mtime)
                
                # 检查过期
                if now - file_time > timedelta(hours=self.cache_expire_hours):
                    expired_files.append(cache_file)
                    continue
                
                total_size += file_stat.st_size
                
                # 检查超量
                if total_size > max_size_bytes:
                    expired_files.append(cache_file)
            
            # 删除过期和超量文件
            for expired_file in expired_files:
                try:
                    expired_file.unlink()
                    logger.info(f"清理缓存文件: {expired_file.name}")
                except Exception as e:
                    logger.error(f"清理缓存文件失败 {expired_file}: {e}")
            
            if expired_files:
                logger.info(f"缓存清理完成，删除 {len(expired_files)} 个文件")
                
        except Exception as e:
            logger.error(f"缓存清理失败: {e}")
    
    async def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        try:
            cache_files = list(self.cache_dir.glob("*.mp4"))
            total_size = sum(f.stat().st_size for f in cache_files)
            
            return {
                "total_files": len(cache_files),
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "max_size_gb": self.max_cache_size_gb,
                "cache_dir": str(self.cache_dir),
                "downloading_count": len([t for t in self._downloading.values() if not t.done()])
            }
        except Exception as e:
            logger.error(f"获取缓存统计失败: {e}")
            return {"error": str(e)}

# 全局缓存服务实例
_cache_service = None

def get_cache_service() -> STRMCacheService:
    """获取缓存服务单例"""
    global _cache_service
    if _cache_service is None:
        _cache_service = STRMCacheService()
    return _cache_service
