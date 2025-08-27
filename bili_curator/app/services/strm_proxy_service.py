"""
STRM代理服务器 - B站流媒体代理和HLS转换服务

该模块实现：
1. B站视频流获取和代理
2. 实时HLS转换
3. 认证管理和Cookie轮换
4. 错误处理和重试机制
"""

import asyncio
import json
import logging
import subprocess
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import aiofiles
import aiohttp
from fastapi import HTTPException

from ..core.config import get_config
from ..core.exceptions import ExternalAPIError, DownloadError
from ..cookie_manager import SimpleCookieManager as CookieManager

logger = logging.getLogger(__name__)


class STRMProxyService:
    """STRM代理服务器 - 处理B站流媒体代理和HLS转换"""
    
    def __init__(self, cookie_manager: CookieManager):
        self.config = get_config()
        self.cookie_manager = cookie_manager
        self.session: Optional[aiohttp.ClientSession] = None
        self.active_streams: Dict[str, Dict] = {}  # 活跃流缓存
        self.hls_cache: Dict[str, Dict] = {}  # HLS片段缓存
        # 可配置的FFmpeg路径（默认 'ffmpeg'，可通过环境变量 FFMPEG_PATH 覆盖）
        try:
            self.ffmpeg_path = self.config.external_api.ffmpeg_path
        except Exception:
            # 回退：旧配置兼容
            self.ffmpeg_path = "ffmpeg"
        
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.start()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.stop()
        
    async def start(self):
        """启动代理服务"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
        logger.info("STRM代理服务已启动")
        
    async def stop(self):
        """停止代理服务"""
        if self.session:
            await self.session.close()
            self.session = None
        
        # 清理活跃流
        for stream_id in list(self.active_streams.keys()):
            await self._cleanup_stream(stream_id)
            
        logger.info("STRM代理服务已停止")
        
    async def get_video_stream_url(self, bilibili_id: str, quality: str = "720p") -> str:
        """
        获取B站视频流URL
        
        Args:
            bilibili_id: B站视频ID
            quality: 视频质量 (1080p, 720p, 480p, 360p)
            
        Returns:
            代理流URL
        """
        try:
            # 检查是否已有活跃流
            stream_key = f"{bilibili_id}_{quality}"
            if stream_key in self.active_streams:
                stream_info = self.active_streams[stream_key]
                if time.time() - stream_info["created_at"] < 3600:  # 1小时有效期
                    return stream_info["proxy_url"]
                else:
                    await self._cleanup_stream(stream_key)
            
            # 获取B站视频信息
            video_info = await self._get_bilibili_video_info(bilibili_id)
            if not video_info:
                raise ExternalAPIError(f"无法获取视频信息: {bilibili_id}")
            
            # 获取播放URL
            play_url = await self._get_bilibili_play_url(bilibili_id, quality)
            if not play_url:
                raise ExternalAPIError(f"无法获取播放URL: {bilibili_id}")
            
            # 创建HLS代理流
            proxy_url = await self._create_hls_proxy_stream(
                stream_key, play_url, video_info
            )
            
            # 缓存流信息
            self.active_streams[stream_key] = {
                "bilibili_id": bilibili_id,
                "quality": quality,
                "proxy_url": proxy_url,
                "play_url": play_url,
                "video_info": video_info,
                "created_at": time.time(),
                "access_count": 0
            }
            
            logger.info(f"创建STRM代理流: {bilibili_id} -> {proxy_url}")
            return proxy_url
            
        except Exception as e:
            logger.error(f"获取视频流URL失败: {bilibili_id}, {e}")
            raise DownloadError(f"获取视频流失败: {str(e)}")
    
    async def _get_bilibili_video_info(self, bilibili_id: str) -> Optional[Dict]:
        """获取B站视频基本信息"""
        try:
            cookies = await self.cookie_manager.get_valid_cookies()
            if not cookies:
                raise ExternalAPIError("没有可用的Cookie")
            
            url = f"https://api.bilibili.com/x/web-interface/view?bvid={bilibili_id}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.bilibili.com/",
                "Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()])
            }
            
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("code") == 0:
                        return data.get("data", {})
                    else:
                        logger.error(f"B站API错误: {data.get('message')}")
                        return None
                else:
                    logger.error(f"HTTP错误: {response.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"获取视频信息失败: {bilibili_id}, {e}")
            return None
    
    async def _get_bilibili_play_url(self, bilibili_id: str, quality: str) -> Optional[str]:
        """获取B站视频播放URL"""
        try:
            cookies = await self.cookie_manager.get_valid_cookies()
            if not cookies:
                raise ExternalAPIError("没有可用的Cookie")
            
            # 质量映射
            quality_map = {
                "1080p": 80,
                "720p": 64,
                "480p": 32,
                "360p": 16
            }
            qn = quality_map.get(quality, 64)
            
            url = f"https://api.bilibili.com/x/player/playurl?bvid={bilibili_id}&qn={qn}&type=&otype=json&fourk=1&fnver=0&fnval=16"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": f"https://www.bilibili.com/video/{bilibili_id}",
                "Cookie": "; ".join([f"{k}={v}" for k, v in cookies.items()])
            }
            
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("code") == 0:
                        durl = data.get("data", {}).get("durl", [])
                        if durl:
                            return durl[0].get("url")
                        else:
                            logger.error("播放URL为空")
                            return None
                    else:
                        logger.error(f"获取播放URL失败: {data.get('message')}")
                        return None
                else:
                    logger.error(f"HTTP错误: {response.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"获取播放URL失败: {bilibili_id}, {e}")
            return None
    
    async def _create_hls_proxy_stream(
        self, 
        stream_key: str, 
        play_url: str, 
        video_info: Dict
    ) -> str:
        """创建HLS代理流"""
        try:
            # 创建临时目录存储HLS文件
            temp_dir = Path(tempfile.mkdtemp(prefix=f"strm_{stream_key}_"))
            
            # 生成HLS播放列表
            m3u8_path = temp_dir / "playlist.m3u8"
            
            # 使用FFmpeg创建HLS流
            ffmpeg_cmd = [
                self.ffmpeg_path,
                "-i", play_url,
                "-c", "copy",
                "-f", "hls",
                "-hls_time", "10",
                "-hls_list_size", "6",
                "-hls_flags", "delete_segments",
                "-hls_segment_filename", str(temp_dir / "segment_%03d.ts"),
                str(m3u8_path)
            ]
            
            # 启动FFmpeg进程
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # 生成代理URL
            proxy_url = f"http://localhost:{self.config.web_server.port}/strm/stream/{stream_key}/playlist.m3u8"
            
            # 存储HLS信息
            self.hls_cache[stream_key] = {
                "temp_dir": temp_dir,
                "m3u8_path": m3u8_path,
                "process": process,
                "created_at": time.time()
            }
            
            return proxy_url
            
        except Exception as e:
            logger.error(f"创建HLS代理流失败: {stream_key}, {e}")
            raise DownloadError(f"创建HLS流失败: {str(e)}")
    
    async def get_hls_playlist(self, stream_key: str) -> Optional[str]:
        """获取HLS播放列表"""
        try:
            if stream_key not in self.hls_cache:
                return None
            
            hls_info = self.hls_cache[stream_key]
            m3u8_path = hls_info["m3u8_path"]
            
            if m3u8_path.exists():
                async with aiofiles.open(m3u8_path, 'r') as f:
                    content = await f.read()
                
                # 更新访问计数
                if stream_key in self.active_streams:
                    self.active_streams[stream_key]["access_count"] += 1
                
                return content
            else:
                return None
                
        except Exception as e:
            logger.error(f"获取HLS播放列表失败: {stream_key}, {e}")
            return None
    
    async def get_hls_segment(self, stream_key: str, segment_name: str) -> Optional[bytes]:
        """获取HLS视频片段"""
        try:
            if stream_key not in self.hls_cache:
                return None
            
            hls_info = self.hls_cache[stream_key]
            temp_dir = hls_info["temp_dir"]
            segment_path = temp_dir / segment_name
            
            if segment_path.exists():
                async with aiofiles.open(segment_path, 'rb') as f:
                    content = await f.read()
                return content
            else:
                return None
                
        except Exception as e:
            logger.error(f"获取HLS片段失败: {stream_key}/{segment_name}, {e}")
            return None
    
    async def _cleanup_stream(self, stream_key: str):
        """清理流资源"""
        try:
            # 清理HLS缓存
            if stream_key in self.hls_cache:
                hls_info = self.hls_cache[stream_key]
                
                # 终止FFmpeg进程
                if "process" in hls_info:
                    process = hls_info["process"]
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
                
                # 删除临时文件
                temp_dir = hls_info.get("temp_dir")
                if temp_dir and temp_dir.exists():
                    import shutil
                    shutil.rmtree(temp_dir, ignore_errors=True)
                
                del self.hls_cache[stream_key]
            
            # 清理活跃流
            if stream_key in self.active_streams:
                del self.active_streams[stream_key]
            
            logger.info(f"清理流资源: {stream_key}")
            
        except Exception as e:
            logger.error(f"清理流资源失败: {stream_key}, {e}")
    
    async def cleanup_expired_streams(self):
        """清理过期流"""
        try:
            current_time = time.time()
            expired_streams = []
            
            for stream_key, stream_info in self.active_streams.items():
                # 1小时未访问的流标记为过期
                if current_time - stream_info["created_at"] > 3600:
                    expired_streams.append(stream_key)
            
            for stream_key in expired_streams:
                await self._cleanup_stream(stream_key)
            
            if expired_streams:
                logger.info(f"清理过期流: {len(expired_streams)}个")
                
        except Exception as e:
            logger.error(f"清理过期流失败: {e}")
    
    def get_stream_stats(self) -> Dict:
        """获取流统计信息"""
        return {
            "active_streams": len(self.active_streams),
            "hls_cache_size": len(self.hls_cache),
            "streams": [
                {
                    "stream_key": key,
                    "bilibili_id": info["bilibili_id"],
                    "quality": info["quality"],
                    "created_at": info["created_at"],
                    "access_count": info["access_count"]
                }
                for key, info in self.active_streams.items()
            ]
        }

    def check_ffmpeg_available(self) -> Dict:
        """检测FFmpeg可用性和路径解析情况，用于健康检查"""
        path = self.ffmpeg_path or "ffmpeg"
        resolved = shutil.which(path)
        available = resolved is not None
        info = {"configured_path": path, "resolved_path": resolved, "available": available}
        # 进一步验证版本（非阻塞，失败不抛异常）
        if available:
            try:
                result = subprocess.run([resolved, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
                info["version_ok"] = (result.returncode == 0)
            except Exception:
                info["version_ok"] = False
        else:
            info["version_ok"] = False
        return info
