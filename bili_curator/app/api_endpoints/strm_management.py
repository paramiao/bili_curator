"""
STRM管理API端点 - 流媒体功能管理接口

该模块提供：
1. STRM流创建和管理
2. HLS播放列表和片段服务
3. STRM文件管理
4. 流统计和监控
"""

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import PlainTextResponse, StreamingResponse

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
from ..services.strm_file_manager import STRMFileManager
from ..services.unified_cache_service import UnifiedCacheService
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strm", tags=["STRM管理"])


@router.get("/stream/{bilibili_id}")
async def get_strm_stream(
    bilibili_id: str,
    quality: str = "720p",
    strm_proxy: STRMProxyService = Depends(get_strm_proxy_service)
) -> StreamingResponse:
    """
    获取STRM流媒体内容 - 直接播放端点
    
    Args:
        bilibili_id: B站视频ID
        quality: 视频质量 (1080p, 720p, 480p, 360p)
    """
    try:
        # 获取流URL并重定向
        stream_url = await strm_proxy.get_video_stream_url(bilibili_id, quality)
        
        # 返回重定向到实际流URL
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=stream_url, status_code=302)
        
    except ExternalAPIError as e:
        logger.error(f"获取STRM流失败 - 外部API错误: {bilibili_id}, {e}")
        raise HTTPException(status_code=502, detail=f"外部API错误: {str(e)}")
    
    except DownloadError as e:
        logger.error(f"获取STRM流失败 - 下载错误: {bilibili_id}, {e}")
        raise HTTPException(status_code=500, detail=f"下载错误: {str(e)}")
    
    except Exception as e:
        logger.error(f"获取STRM流失败 - 未知错误: {bilibili_id}, {e}")
        raise HTTPException(status_code=500, detail=f"获取STRM流失败: {str(e)}")


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
