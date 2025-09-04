"""
STRM管理API端点 - 流媒体功能管理接口

该模块提供：
1. STRM流创建和管理
2. HLS播放列表和片段服务
3. STRM文件管理
4. 流统计和监控
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, Response, PlainTextResponse, JSONResponse
from typing import List, Optional, Dict, Any
import logging
import re
import aiohttp
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from ..core.dependencies import (
    get_database_session,
    get_strm_proxy_service,
    get_strm_file_manager,
    get_unified_cache_service
)
from ..core.exceptions import DownloadError, ExternalAPIError, ValidationError
from ..schemas.common import StandardResponse, PaginatedResponse
from ..schemas.video import VideoResponse
from ..schemas.subscription import SubscriptionResponse
from ..services.strm_proxy_service import STRMProxyService
from ..services.enhanced_downloader import EnhancedDownloader
from ..services.strm_cache_service import get_cache_service
from ..core.config import get_settings
from ..cookie_manager import SimpleCookieManager
from ..services.strm_file_manager import STRMFileManager
from ..services.unified_cache_service import UnifiedCacheService
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strm", tags=["STRM管理"])

# 请求去重缓存 - 防止短时间内重复请求
_request_cache = {}
_cache_lock = asyncio.Lock()




@router.api_route("/stream/{bilibili_id}", methods=["GET", "HEAD"])
@router.api_route("/stream/{bilibili_id}.mp4", methods=["GET", "HEAD"])
async def get_strm_stream(
    bilibili_id: str,
    request: Request,
    quality: str = "720p",
    strm_proxy: STRMProxyService = Depends(get_strm_proxy_service)
):
    """
    获取STRM流媒体 - 流代理模式，适配Emby等媒体服务器，支持GET和HEAD方法
    
    Args:
        bilibili_id: B站视频ID
        quality: 视频质量 (1080p, 720p, 480p, 360p)
    """
    # 去掉文件扩展名，获取真实BV号
    original_bilibili_id = bilibili_id
    if bilibili_id.endswith('.mp4'):
        bilibili_id = bilibili_id[:-4]
    
    # 记录请求日志
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    range_header = request.headers.get("range") or request.headers.get("Range")
    method = request.method
    # 强制输出日志用于调试
    print(f"🔍 STRM请求 - 方法: {method}, 原始ID: {original_bilibili_id}, 处理后ID: {bilibili_id}, 质量: {quality}, 客户端: {client_ip}, UA: {user_agent}, Range: {range_header}")
    logger.warning(f"STRM请求 - 方法: {method}, 原始ID: {original_bilibili_id}, 处理后ID: {bilibili_id}, 质量: {quality}, 客户端: {client_ip}, UA: {user_agent}, Range: {range_header}")
    
    try:
        # 请求去重检查 - 防止短时间内重复请求
        request_key = f"{bilibili_id}_{method}_{client_ip}"
        current_time = time.time()
        
        async with _cache_lock:
            if request_key in _request_cache:
                last_request_time = _request_cache[request_key]
                # senplayer兼容性：放宽重复请求检测
                is_senplayer = "SenPlayer" in user_agent
                threshold = 0.5 if is_senplayer else 2.0
                
                if current_time - last_request_time < threshold:
                    print(f"🔄 忽略重复请求 - ID: {bilibili_id}, 间隔: {current_time - last_request_time:.2f}s, UA: {user_agent[:20]}")
                    # 对senplayer返回更友好的错误
                    if is_senplayer:
                        return JSONResponse({
                            "error": "播放器请求过于频繁，请稍后重试",
                            "retry_after": 1
                        }, status_code=429)
                    else:
                        return JSONResponse({
                            "error": "请求过于频繁",
                            "retry_after": 2
                        }, status_code=429)
            
            _request_cache[request_key] = current_time
            
            # 清理过期的缓存条目（超过10秒）
            expired_keys = [k for k, v in _request_cache.items() if current_time - v > 10]
            for k in expired_keys:
                del _request_cache[k]
        
        # 优先检查本地缓存（GET请求）
        if method == "GET":
            cache_service = get_cache_service()
            
            # 检查是否已缓存
            cached_path = await cache_service.get_cached_file_path(bilibili_id)
            if cached_path:
                print(f"🔍 使用本地缓存 - ID: {bilibili_id}")
                # 直接serve本地缓存文件（支持Range请求）
                return await serve_local_file(cached_path, request)
            
            # 检查是否正在下载
            if await cache_service.is_downloading(bilibili_id):
                # 返回下载进度信息
                progress = await cache_service.get_download_progress(bilibili_id)
                return JSONResponse({
                    "status": "downloading",
                    "progress": progress,
                    "message": f"视频正在下载中... {progress*100:.1f}%"
                }, status_code=202)
        
        # 获取B站真实播放URL
        stream_url = await strm_proxy.get_video_stream_url(bilibili_id, quality)
        if not stream_url:
            logger.error(f"无法获取视频流 - ID: {bilibili_id}")
            raise HTTPException(status_code=404, detail="无法获取视频流")
        
        print(f"🔍 获取到播放URL - ID: {bilibili_id}, URL: {stream_url[:80]}...")
        logger.warning(f"获取到播放URL - ID: {bilibili_id}, URL: {stream_url[:80]}...")
        
        # HEAD请求处理：返回元信息，支持Range
        if method == "HEAD":
            print(f"🔍 处理HEAD请求 - ID: {bilibili_id}")
            
            # 构建基础请求头
            base_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': f'https://www.bilibili.com/video/{bilibili_id}',
                'Accept': '*/*',
                'Accept-Encoding': 'identity',
                'Connection': 'keep-alive'
            }
            
            # 探测元信息
            meta = await probe_meta(stream_url, base_headers)
            total_size = meta["total"]
            
            # 检查是否有Range请求
            if range_header:
                print(f"🔍 HEAD Range请求 - ID: {bilibili_id}, Range: {range_header}")
                # 解析Range头
                range_match = re.match(r'bytes=(\d+)-(\d*)', range_header)
                if range_match and total_size:
                    start = int(range_match.group(1))
                    end = int(range_match.group(2)) if range_match.group(2) else total_size - 1
                    
                    # 验证范围有效性
                    if start >= total_size or (end is not None and end >= total_size):
                        return Response(
                            status_code=416,
                            headers={"Content-Range": f"bytes */{total_size}"}
                        )
                    
                    # 计算内容长度
                    content_length = end - start + 1
                    
                    response_headers = {
                        "Accept-Ranges": "bytes",
                        "Content-Type": meta["content_type"],
                        "Content-Range": f"bytes {start}-{end}/{total_size}",
                        "Content-Length": str(content_length)
                    }
                    
                    # 添加ETag和Last-Modified（如果有）
                    if meta["etag"]:
                        response_headers["ETag"] = meta["etag"]
                    if meta["last_modified"]:
                        response_headers["Last-Modified"] = meta["last_modified"]
                    
                    print(f"🔍 HEAD 206响应 - ID: {bilibili_id}, Range: {start}-{end}, Length: {content_length}")
                    
                    return Response(
                        content=b"",
                        status_code=206,  # 返回206 Partial Content
                        headers=response_headers
                    )
            
            # 无Range头：返回完整文件信息
            response_headers = {
                "Accept-Ranges": "bytes",
                "Content-Type": meta["content_type"]
            }
            
            # 添加Content-Length（如果有）
            if total_size:
                response_headers["Content-Length"] = str(total_size)
            
            # 添加ETag和Last-Modified（如果有）
            if meta["etag"]:
                response_headers["ETag"] = meta["etag"]
            if meta["last_modified"]:
                response_headers["Last-Modified"] = meta["last_modified"]
            
            print(f"🔍 HEAD 200响应 - ID: {bilibili_id}, Content-Length: {total_size}, Content-Type: {meta['content_type']}")
            
            return Response(
                content=b"",
                status_code=200,
                headers=response_headers
            )
        
        # 新方案：按需下载+本地文件服务（优先处理）
        cache_service = get_cache_service()
        
        # 检查是否已缓存
        cached_path = await cache_service.get_cached_file_path(bilibili_id)
        if cached_path:
            # 直接serve本地缓存文件（支持Range请求）
            return await serve_local_file(cached_path, request)
        
        # 检查是否正在下载
        if await cache_service.is_downloading(bilibili_id):
            # 返回下载进度信息
            progress = await cache_service.get_download_progress(bilibili_id)
            return JSONResponse({
                "status": "downloading",
                "progress": progress,
                "message": f"视频正在下载中... {progress*100:.1f}%"
            }, status_code=202)
        
        # 开始下载到缓存
        try:
            cached_path = await cache_service.download_video(bilibili_id, stream_url)
            # 下载完成，serve本地文件
            return await serve_local_file(cached_path, request)
        except Exception as e:
            logger.error(f"下载视频到缓存失败 {bilibili_id}: {e}")
            # 降级到原有代理模式
            return await fallback_proxy_stream(bilibili_id, stream_url, request)
        
        # 构建响应头，包含正确的Content-Length
        response_headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
        
        # 添加Content-Length（关键修复）
        if meta["total"]:
            response_headers["Content-Length"] = str(meta["total"])
            print(f"🔍 设置Content-Length - ID: {bilibili_id}, 大小: {meta['total']}")
        
        # 添加其他元信息头
        if meta["etag"]:
            response_headers["ETag"] = meta["etag"]
        if meta["last_modified"]:
            response_headers["Last-Modified"] = meta["last_modified"]
        
        return StreamingResponse(
            stream_generator(),
            media_type=meta["content_type"],
            headers=response_headers
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取STRM流失败: {bilibili_id}, {e}")
        raise HTTPException(status_code=500, detail=f"流媒体服务错误: {str(e)}")


async def probe_meta(stream_url: str, base_headers: dict) -> dict:
    """探测上游元信息"""
    timeout = aiohttp.ClientTimeout(connect=10, sock_read=300, total=None)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            # 先尝试HEAD请求
            async with session.head(stream_url, headers=base_headers, allow_redirects=True) as response:
                content_type = response.headers.get('Content-Type', 'video/mp4')
                total = response.headers.get('Content-Length')
                etag = response.headers.get('ETag')
                last_modified = response.headers.get('Last-Modified')
                
                # 如果HEAD没有获取到总大小，使用0-0探针
                if not total:
                    probe_headers = dict(base_headers)
                    probe_headers['Range'] = 'bytes=0-0'
                    async with session.get(stream_url, headers=probe_headers, allow_redirects=True) as probe_response:
                        content_range = probe_response.headers.get('Content-Range')
                        if content_range and '/' in content_range:
                            total = content_range.split('/')[-1]
                
                return {
                    'content_type': content_type,
                    'total': int(total) if total and total.isdigit() else None,
                    'etag': etag,
                    'last_modified': last_modified
                }
        except Exception as e:
            logger.warning(f"探测元信息失败: {e}")
            return {
                'content_type': 'video/mp4',
                'total': None,
                'etag': None,
                'last_modified': None
            }


async def handle_range_request(bilibili_id: str, stream_url: str, start: int, end: int = None, request: Request = None):
    """处理HTTP Range请求，返回部分内容"""
    
    # 构建基础请求头
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': f'https://www.bilibili.com/video/{bilibili_id}',
        'Accept': '*/*',
        'Accept-Encoding': 'identity'
    }
    
    # 透传缓存校验头
    if request:
        for header_name in ("If-Range", "If-Modified-Since", "If-None-Match"):
            if request.headers.get(header_name):
                headers[header_name] = request.headers[header_name]
    
    # 添加Range头到上游请求
    if end is not None:
        headers['Range'] = f'bytes={start}-{end}'
    else:
        headers['Range'] = f'bytes={start}-'
    
    logger.info(f"向B站CDN发送Range请求 - ID: {bilibili_id}, Range: {headers['Range']}")
    
    # 优化超时设置：避免Range请求连接中断
    timeout = aiohttp.ClientTimeout(connect=10, sock_read=60, total=120)
    
    session = None
    try:
        session = aiohttp.ClientSession(timeout=timeout)
        
        async with session.get(stream_url, headers=headers, allow_redirects=True) as response:
            logger.info(f"B站CDN Range响应 - ID: {bilibili_id}, 状态: {response.status}")
            
            # 获取总长度
            total = None
            content_range = response.headers.get('Content-Range')
            if content_range and '/' in content_range:
                total = int(content_range.split('/')[-1])
            
            # 如果没有获取到总长度，尝试探测
            if total is None:
                base_headers = {
                    'User-Agent': headers['User-Agent'],
                    'Referer': headers['Referer'],
                    'Accept': '*/*',
                    'Accept-Encoding': 'identity'
                }
                meta = await probe_meta(stream_url, base_headers)
                total = meta["total"]
            
            # 检查范围是否有效
            if total is not None and (start >= total or (end is not None and end >= total)):
                logger.warning(f"Range超出范围 - ID: {bilibili_id}, 请求: {start}-{end}, 总长度: {total}")
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{total}"}
                )
            
            if response.status in [200, 206]:  # 接受200或206状态码
                # 解析实际区间
                if content_range and ' ' in content_range and '/' in content_range:
                    range_part = content_range.split(' ')[1].split('/')[0]  # a-b
                    if '-' in range_part:
                        actual_start, actual_end = range_part.split('-')
                        actual_start, actual_end = int(actual_start), int(actual_end)
                    else:
                        actual_start = start
                        actual_end = end if end is not None else (total - 1 if total else None)
                else:
                    actual_start = start
                    actual_end = end if end is not None else (total - 1 if total else None)
                
                # 计算内容长度
                content_length = None
                if actual_end is not None and actual_start is not None:
                    content_length = actual_end - actual_start + 1
                elif response.headers.get('Content-Length'):
                    content_length = int(response.headers.get('Content-Length'))
                
                # 获取媒体类型
                media_type = response.headers.get('Content-Type', 'application/octet-stream')
                
                # 构造206响应头
                response_headers = {
                    "Accept-Ranges": "bytes",
                    "Content-Type": media_type
                }
                
                # 添加Content-Range
                if total is not None and actual_end is not None:
                    response_headers["Content-Range"] = f"bytes {actual_start}-{actual_end}/{total}"
                elif content_range:
                    response_headers["Content-Range"] = content_range
                
                # 添加Content-Length
                if content_length is not None:
                    response_headers["Content-Length"] = str(content_length)
                
                # 透传ETag和Last-Modified
                if response.headers.get('ETag'):
                    response_headers["ETag"] = response.headers.get('ETag')
                if response.headers.get('Last-Modified'):
                    response_headers["Last-Modified"] = response.headers.get('Last-Modified')
                
                logger.info(f"返回206响应 - ID: {bilibili_id}, Content-Length: {content_length}, Content-Range: {response_headers.get('Content-Range')}")
                
                async def range_stream_generator():
                    chunk_count = 0
                    try:
                        # 使用较小的chunk避免连接超时
                        async for chunk in response.content.iter_chunked(8192):
                            chunk_count += 1
                            if chunk_count == 1:
                                logger.info(f"开始传输Range数据块 - ID: {bilibili_id}")
                            yield chunk
                        logger.info(f"Range流代理完成 - ID: {bilibili_id}, 数据块: {chunk_count}")
                    except Exception as e:
                        logger.error(f"Range流传输异常 - ID: {bilibili_id}, 错误: {e}")
                        raise
                
                return StreamingResponse(
                    range_stream_generator(),
                    status_code=206,  # 返回206 Partial Content
                    media_type=media_type,
                    headers=response_headers
                )
            elif response.status == 416 and total is not None:
                # 透传416错误
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{total}"}
                )
            else:
                logger.error(f"B站CDN Range请求失败 - ID: {bilibili_id}, 状态: {response.status}")
                # 透传其他错误状态码
                return Response(status_code=response.status)
                
    except Exception as e:
        logger.error(f"Range请求处理异常 - ID: {bilibili_id}, 错误: {e}")
        raise HTTPException(status_code=500, detail=f"Range请求处理失败: {str(e)}")
    finally:
        if session and not session.closed:
            await session.close()


@router.post("/stream/create/{bilibili_id}")
async def create_strm_stream(
    bilibili_id: str,
    quality: str = "720p",
    strm_proxy: STRMProxyService = Depends(get_strm_proxy_service),
    db: Session = Depends(get_database_session)
) -> StandardResponse:
    """
    创建STRM流
    
    Args:
        bilibili_id: B站视频ID
        quality: 视频质量 (1080p, 720p, 480p, 360p)
    """
    try:
        # 获取流URL
        stream_url = await strm_proxy.get_video_stream_url(bilibili_id, quality)
        
        return StandardResponse(
            success=True,
            message="STRM流创建成功",
            data={
                "bilibili_id": bilibili_id,
                "quality": quality,
                "stream_url": stream_url
            }
        )
        
    except ExternalAPIError as e:
        logger.error(f"创建STRM流失败 - 外部API错误: {bilibili_id}, {e}")
        raise HTTPException(status_code=502, detail=f"外部API错误: {str(e)}")
    
    except DownloadError as e:
        logger.error(f"创建STRM流失败 - 下载错误: {bilibili_id}, {e}")
        raise HTTPException(status_code=500, detail=f"下载错误: {str(e)}")
    
    except Exception as e:
        logger.error(f"创建STRM流失败 - 未知错误: {bilibili_id}, {e}")
        raise HTTPException(status_code=500, detail=f"创建STRM流失败: {str(e)}")


@router.get("/stream/{stream_key}/playlist.m3u8")
async def get_hls_playlist(
    stream_key: str,
    strm_proxy: STRMProxyService = Depends(get_strm_proxy_service)
) -> PlainTextResponse:
    """获取HLS播放列表"""
    try:
        playlist_content = await strm_proxy.get_hls_playlist(stream_key)
        
        if playlist_content is None:
            raise HTTPException(status_code=404, detail="播放列表不存在")
        
        return PlainTextResponse(
            content=playlist_content,
            media_type="application/vnd.apple.mpegurl"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取HLS播放列表失败: {stream_key}, {e}")
        raise HTTPException(status_code=500, detail=f"获取播放列表失败: {str(e)}")


@router.get("/stream/{stream_key}/{segment_name}")
async def get_hls_segment(
    stream_key: str,
    segment_name: str,
    strm_proxy: STRMProxyService = Depends(get_strm_proxy_service)
) -> StreamingResponse:
    """获取HLS视频片段"""
    try:
        segment_data = await strm_proxy.get_hls_segment(stream_key, segment_name)
        
        if segment_data is None:
            raise HTTPException(status_code=404, detail="视频片段不存在")
        
        def generate():
            yield segment_data
        
        return StreamingResponse(
            generate(),
            media_type="video/mp2t",
            headers={
                "Cache-Control": "max-age=3600",
                "Content-Length": str(len(segment_data))
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取HLS片段失败: {stream_key}/{segment_name}, {e}")
        raise HTTPException(status_code=500, detail=f"获取视频片段失败: {str(e)}")


@router.post("/file/create")
async def create_strm_file(
    video_data: Dict,
    subscription_data: Dict,
    stream_url: str,
    strm_file_manager: STRMFileManager = Depends(get_strm_file_manager)
) -> StandardResponse:
    """
    创建STRM文件
    
    Args:
        video_data: 视频信息
        subscription_data: 订阅信息
        stream_url: 流媒体URL
    """
    try:
        # 转换为响应模型
        video = VideoResponse(**video_data)
        subscription = SubscriptionResponse(**subscription_data)
        
        # 创建STRM文件
        strm_path = await strm_file_manager.create_strm_file(
            video, subscription, stream_url
        )
        
        return StandardResponse(
            success=True,
            message="STRM文件创建成功",
            data={
                "strm_path": str(strm_path),
                "video_id": video.bilibili_id,
                "subscription_id": subscription.id
            }
        )
        
    except ValidationError as e:
        logger.error(f"创建STRM文件失败 - 验证错误: {e}")
        raise HTTPException(status_code=400, detail=f"数据验证错误: {str(e)}")
    
    except DownloadError as e:
        logger.error(f"创建STRM文件失败 - 下载错误: {e}")
        raise HTTPException(status_code=500, detail=f"文件创建错误: {str(e)}")
    
    except Exception as e:
        logger.error(f"创建STRM文件失败 - 未知错误: {e}")
        raise HTTPException(status_code=500, detail=f"创建STRM文件失败: {str(e)}")


@router.put("/file/update")
async def update_strm_file(
    video_data: Dict,
    subscription_data: Dict,
    new_stream_url: str,
    strm_file_manager: STRMFileManager = Depends(get_strm_file_manager)
) -> StandardResponse:
    """
    更新STRM文件
    
    Args:
        video_data: 视频信息
        subscription_data: 订阅信息
        new_stream_url: 新的流媒体URL
    """
    try:
        # 转换为响应模型
        video = VideoResponse(**video_data)
        subscription = SubscriptionResponse(**subscription_data)
        
        # 更新STRM文件
        success = await strm_file_manager.update_strm_file(
            video, subscription, new_stream_url
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="STRM文件不存在")
        
        return StandardResponse(
            success=True,
            message="STRM文件更新成功",
            data={
                "video_id": video.bilibili_id,
                "subscription_id": subscription.id,
                "updated": True
            }
        )
        
    except HTTPException:
        raise
    except ValidationError as e:
        logger.error(f"更新STRM文件失败 - 验证错误: {e}")
        raise HTTPException(status_code=400, detail=f"数据验证错误: {str(e)}")
    
    except Exception as e:
        logger.error(f"更新STRM文件失败 - 未知错误: {e}")
        raise HTTPException(status_code=500, detail=f"更新STRM文件失败: {str(e)}")


@router.delete("/file/delete")
async def delete_strm_file(
    video_data: Dict,
    subscription_data: Dict,
    strm_file_manager: STRMFileManager = Depends(get_strm_file_manager)
) -> StandardResponse:
    """
    删除STRM文件
    
    Args:
        video_data: 视频信息
        subscription_data: 订阅信息
    """
    try:
        # 转换为响应模型
        video = VideoResponse(**video_data)
        subscription = SubscriptionResponse(**subscription_data)
        
        # 删除STRM文件
        success = await strm_file_manager.delete_strm_file(video, subscription)
        
        if not success:
            raise HTTPException(status_code=404, detail="STRM文件不存在")
        
        return StandardResponse(
            success=True,
            message="STRM文件删除成功",
            data={
                "video_id": video.bilibili_id,
                "subscription_id": subscription.id,
                "deleted": True
            }
        )
        
    except HTTPException:
        raise
    except ValidationError as e:
        logger.error(f"删除STRM文件失败 - 验证错误: {e}")
        raise HTTPException(status_code=400, detail=f"数据验证错误: {str(e)}")
    
    except Exception as e:
        logger.error(f"删除STRM文件失败 - 未知错误: {e}")
        raise HTTPException(status_code=500, detail=f"删除STRM文件失败: {str(e)}")


@router.post("/sync/{subscription_id}")
async def sync_strm_directory(
    subscription_id: int,
    strm_file_manager: STRMFileManager = Depends(get_strm_file_manager),
    db: Session = Depends(get_database_session)
) -> StandardResponse:
    """
    同步订阅的STRM目录
    
    Args:
        subscription_id: 订阅ID
    """
    try:
        # 获取订阅信息
        from ..models import Subscription
        subscription = db.query(Subscription).filter(
            Subscription.id == subscription_id
        ).first()
        
        if not subscription:
            raise HTTPException(status_code=404, detail="订阅不存在")
        
        # 转换为响应模型
        subscription_data = SubscriptionResponse.from_orm(subscription)
        
        # 同步目录
        sync_stats = await strm_file_manager.sync_strm_directory(subscription_data)
        
        return StandardResponse(
            success=True,
            message="STRM目录同步完成",
            data={
                "subscription_id": subscription_id,
                "sync_stats": sync_stats
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"同步STRM目录失败: {subscription_id}, {e}")
        raise HTTPException(status_code=500, detail=f"同步目录失败: {str(e)}")


@router.get("/stats/streams")
async def get_stream_stats(
    strm_proxy: STRMProxyService = Depends(get_strm_proxy_service)
) -> StandardResponse:
    """获取流统计信息"""
    try:
        stats = strm_proxy.get_stream_stats()
        
        return StandardResponse(
            success=True,
            message="获取流统计成功",
            data=stats
        )
        
    except Exception as e:
        logger.error(f"获取流统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计失败: {str(e)}")


@router.get("/stats/files")
async def get_file_stats(
    strm_file_manager: STRMFileManager = Depends(get_strm_file_manager)
) -> StandardResponse:
    """获取STRM文件统计信息"""
    try:
        stats = strm_file_manager.get_strm_stats()
        
        return StandardResponse(
            success=True,
            message="获取文件统计成功",
            data=stats
        )
        
    except Exception as e:
        logger.error(f"获取文件统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计失败: {str(e)}")


@router.post("/cleanup/expired")
async def cleanup_expired_streams(
    strm_proxy: STRMProxyService = Depends(get_strm_proxy_service)
) -> StandardResponse:
    """清理过期流"""
    try:
        await strm_proxy.cleanup_expired_streams()
        
        return StandardResponse(
            success=True,
            message="过期流清理完成",
            data={"cleaned": True}
        )
        
    except Exception as e:
        logger.error(f"清理过期流失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")


@router.get("/health")
async def strm_health_check(
    strm_proxy: STRMProxyService = Depends(get_strm_proxy_service),
    strm_file_manager: STRMFileManager = Depends(get_strm_file_manager)
) -> StandardResponse:
    """STRM服务健康检查"""
    try:
        # 检查代理服务状态
        stream_stats = strm_proxy.get_stream_stats()
        ffmpeg_info = strm_proxy.check_ffmpeg_available()
        
        # 检查文件管理状态
        file_stats = strm_file_manager.get_strm_stats()
        
        health_data = {
            "proxy_service": {
                "status": "healthy",
                "active_streams": stream_stats.get("active_streams", 0),
                "hls_cache_size": stream_stats.get("hls_cache_size", 0),
                "ffmpeg": ffmpeg_info
            },
            "file_manager": {
                "status": "healthy",
                "total_strm_files": file_stats.get("total_strm_files", 0),
                "total_directories": file_stats.get("total_directories", 0)
            },
            "overall_status": "healthy" if (ffmpeg_info.get("available") and ffmpeg_info.get("version_ok")) else "degraded"
        }
        
        return StandardResponse(
            success=True,
            message="STRM服务运行正常",
            data=health_data
        )
    except Exception as e:
        logger.error(f"STRM健康检查失败: {e}")
        
        health_data = {
            "proxy_service": {"status": "error"},
            "file_manager": {"status": "error"},
            "overall_status": "error",
            "error": str(e)
        }
        
        return StandardResponse(
            success=False,
            message="STRM服务异常",
            data=health_data
        )


@router.get("/cache/stats", response_model=StandardResponse)
async def get_cache_stats():
    """获取STRM缓存统计信息"""
    try:
        cache_service = get_cache_service()
        stats = await cache_service.get_cache_stats()
        
        return StandardResponse(
            success=True,
            message="缓存统计获取成功",
            data=stats
        )
    except Exception as e:
        logger.error(f"获取缓存统计失败: {e}")
        return StandardResponse(
            success=False,
            message=f"获取缓存统计失败: {str(e)}"
        )


@router.post("/cache/cleanup", response_model=StandardResponse)
async def cleanup_cache():
    """手动清理STRM缓存"""
    try:
        cache_service = get_cache_service()
        await cache_service.cleanup_cache()
        
        return StandardResponse(
            success=True,
            message="缓存清理完成"
        )
    except Exception as e:
        logger.error(f"缓存清理失败: {e}")
        return StandardResponse(
            success=False,
            message=f"缓存清理失败: {str(e)}"
        )


async def serve_local_file(file_path, request: Request):
    """serve本地缓存文件，完美支持Range请求"""
    import aiofiles
    from pathlib import Path
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="缓存文件不存在")
    
    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")
    
    # 从文件路径提取bilibili_id（文件名格式：BV1234567890.mp4）
    bilibili_id = file_path.stem  # 获取不含扩展名的文件名
    
    # 网盘文件模式响应头 - 模拟小雅网盘文件链接
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": "video/mp4",
        "Content-Disposition": f"inline; filename={bilibili_id}.mp4",
        "Cache-Control": "public, max-age=3600",
        "Content-Length": str(file_size),
        "Server": "nginx/1.20.1"  # 模拟文件服务器
    }
    
    # Emby兼容性：检测是否为转码请求
    user_agent = request.headers.get("user-agent", "")
    is_emby_request = "Emby" in user_agent or "MediaBrowser" in user_agent
    
    print(f"🔍 serve_local_file - ID: {file_path.name}, 大小: {file_size}, Range: {range_header}, UA: {user_agent[:50]}")
    
    # 处理Range请求
    if range_header:
        range_match = re.match(r'bytes=(\d+)-(\d*)', range_header)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
            
            # 验证范围
            if start >= file_size or end >= file_size:
                return Response(
                    status_code=416,
                    headers={"Content-Range": f"bytes */{file_size}"}
                )
            
            content_length = end - start + 1
            headers.update({
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(content_length)
            })
            
            # 读取文件片段
            async def file_range_generator():
                async with aiofiles.open(file_path, 'rb') as f:
                    await f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk_size = min(8192, remaining)
                        chunk = await f.read(chunk_size)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            
            return StreamingResponse(
                file_range_generator(),
                status_code=206,
                headers=headers
            )
    
    # 完整文件响应
    async def file_generator():
        async with aiofiles.open(file_path, 'rb') as f:
            while True:
                chunk = await f.read(8192)
                if not chunk:
                    break
                yield chunk
    
    return StreamingResponse(
        file_generator(),
        status_code=200,
        headers=headers
    )


async def fallback_proxy_stream(bilibili_id: str, stream_url: str, request: Request):
    """降级到原有代理模式 - 支持Range请求接力传递"""
    import aiohttp
    
    # 构建上游请求头
    upstream_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': f'https://www.bilibili.com/video/{bilibili_id}',
        'Accept': '*/*',
        'Accept-Encoding': 'identity',
    }
    
    # 关键：接力传递Range头
    range_header = request.headers.get("range") or request.headers.get("Range")
    if range_header:
        upstream_headers['Range'] = range_header
        print(f"🔄 接力Range请求 - ID: {bilibili_id}, Range: {range_header}")
    
    logger.info(f"降级代理模式 - ID: {bilibili_id}, Range: {range_header}")
    
    session = None
    try:
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(connect=10, sock_read=60, total=120)
        )
        
        async with session.get(stream_url, headers=upstream_headers) as response:
            # 网盘文件模式响应头 - 模拟小雅网盘文件链接
            response_headers = {
                "Accept-Ranges": "bytes",
                "Content-Type": "video/mp4",
                "Content-Disposition": f"inline; filename={bilibili_id}.mp4",
                "Cache-Control": "public, max-age=3600",
                "Server": "nginx/1.20.1"  # 模拟文件服务器
            }
            
            # 接力传递Content-Length
            if "Content-Length" in response.headers:
                response_headers["Content-Length"] = response.headers["Content-Length"]
            
            # 接力传递Content-Range（206响应的关键）
            if "Content-Range" in response.headers:
                response_headers["Content-Range"] = response.headers["Content-Range"]
            
            # 接力传递ETag和Last-Modified
            if "ETag" in response.headers:
                response_headers["ETag"] = response.headers["ETag"]
            if "Last-Modified" in response.headers:
                response_headers["Last-Modified"] = response.headers["Last-Modified"]
            
            print(f"🔄 代理响应 - ID: {bilibili_id}, 状态: {response.status}, Content-Length: {response.headers.get('Content-Length')}")
            
            # 流式传输数据
            async def stream_generator():
                try:
                    async for chunk in response.content.iter_chunked(8192):
                        yield chunk
                except Exception as e:
                    logger.error(f"代理流传输异常 - ID: {bilibili_id}, 错误: {e}")
                    raise
            
            # 关键：接力传递状态码（200或206）
            return StreamingResponse(
                stream_generator(),
                status_code=response.status,  # 保持原始状态码
                headers=response_headers
            )
            
    except Exception as e:
        logger.error(f"降级代理异常 - ID: {bilibili_id}, 错误: {e}")
        raise HTTPException(status_code=502, detail=f"代理服务错误: {str(e)}")
    finally:
        if session and not session.closed:
            await session.close()
