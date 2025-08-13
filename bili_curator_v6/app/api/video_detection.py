#!/usr/bin/env python3
"""
视频检测服务API端点
提供Web界面管理视频检测功能
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Dict, Any
import logging

from ..video_detection_service import video_detection_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/video-detection", tags=["video-detection"])

@router.get("/status")
async def get_detection_status() -> Dict[str, Any]:
    """获取视频检测服务状态"""
    try:
        status = video_detection_service.get_status()
        return {
            "success": True,
            "data": status
        }
    except Exception as e:
        logger.error(f"获取检测服务状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/start")
async def start_detection_service() -> Dict[str, Any]:
    """启动视频检测服务"""
    try:
        if video_detection_service.is_running:
            return {
                "success": True,
                "message": "视频检测服务已在运行中"
            }
        
        await video_detection_service.start_service()
        return {
            "success": True,
            "message": "视频检测服务启动成功"
        }
    except Exception as e:
        logger.error(f"启动检测服务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/stop")
async def stop_detection_service() -> Dict[str, Any]:
    """停止视频检测服务"""
    try:
        await video_detection_service.stop_service()
        return {
            "success": True,
            "message": "视频检测服务停止成功"
        }
    except Exception as e:
        logger.error(f"停止检测服务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/scan/full")
async def trigger_full_scan(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """触发完整扫描"""
    try:
        # 在后台执行扫描任务
        background_tasks.add_task(video_detection_service.full_scan)
        
        return {
            "success": True,
            "message": "完整扫描任务已启动，将在后台执行"
        }
    except Exception as e:
        logger.error(f"触发完整扫描失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/scan/incremental")
async def trigger_incremental_scan(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """触发增量扫描"""
    try:
        # 在后台执行增量扫描
        background_tasks.add_task(video_detection_service.incremental_scan)
        
        return {
            "success": True,
            "message": "增量扫描任务已启动，将在后台执行"
        }
    except Exception as e:
        logger.error(f"触发增量扫描失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/scan/result")
async def get_last_scan_result() -> Dict[str, Any]:
    """获取最后一次扫描结果"""
    try:
        status = video_detection_service.get_status()
        return {
            "success": True,
            "data": {
                "last_scan_time": status.get("last_scan_time"),
                "is_running": status.get("is_running"),
                "scan_interval": status.get("scan_interval")
            }
        }
    except Exception as e:
        logger.error(f"获取扫描结果失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/config")
async def update_detection_config(scan_interval: int = 300) -> Dict[str, Any]:
    """更新检测服务配置"""
    try:
        if scan_interval < 60:
            raise HTTPException(status_code=400, detail="扫描间隔不能少于60秒")
        
        video_detection_service.scan_interval = scan_interval
        
        return {
            "success": True,
            "message": f"扫描间隔已更新为{scan_interval}秒"
        }
    except Exception as e:
        logger.error(f"更新检测配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
