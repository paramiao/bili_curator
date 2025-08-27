"""
缓存管理 API 端点
提供缓存统计、一致性检查、清理等功能
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
from loguru import logger

from ..models import get_db
from ..services.unified_cache_service import unified_cache
from ..services.cache_invalidation_service import cache_invalidation
from ..services.cache_migration_service import cache_migration

router = APIRouter()


@router.get("/cache/stats")
async def get_cache_stats(db: Session = Depends(get_db)):
    """获取缓存统计信息"""
    try:
        stats = unified_cache.get_stats()
        return {
            "status": "success",
            "data": stats
        }
    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cache/consistency")
async def check_cache_consistency(
    namespace: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """检查缓存一致性"""
    try:
        results = unified_cache.check_consistency(db, namespace)
        return {
            "status": "success",
            "data": results
        }
    except Exception as e:
        logger.error(f"Failed to check cache consistency: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/clear/{namespace}")
async def clear_cache_namespace(
    namespace: str,
    db: Session = Depends(get_db)
):
    """清理指定命名空间的缓存"""
    try:
        cleared_count = unified_cache.clear_namespace(db, namespace)
        return {
            "status": "success",
            "message": f"Cleared {cleared_count} cache items from namespace: {namespace}",
            "cleared_count": cleared_count
        }
    except Exception as e:
        logger.error(f"Failed to clear cache namespace {namespace}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cache/{namespace}/{key}")
async def delete_cache_item(
    namespace: str,
    key: str,
    db: Session = Depends(get_db)
):
    """删除指定缓存项"""
    try:
        deleted = unified_cache.delete(db, namespace, key)
        return {
            "status": "success",
            "deleted": deleted,
            "message": f"Cache item {namespace}:{key} {'deleted' if deleted else 'not found'}"
        }
    except Exception as e:
        logger.error(f"Failed to delete cache item {namespace}:{key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cache/{namespace}/{key}")
async def get_cache_item(
    namespace: str,
    key: str,
    ttl_hours: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """获取指定缓存项"""
    try:
        value = unified_cache.get(db, namespace, key, ttl_hours=ttl_hours)
        return {
            "status": "success",
            "data": {
                "namespace": namespace,
                "key": key,
                "value": value,
                "found": value is not None
            }
        }
    except Exception as e:
        logger.error(f"Failed to get cache item {namespace}:{key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/{namespace}/{key}")
async def set_cache_item(
    namespace: str,
    key: str,
    request_data: Dict[str, Any],
    db: Session = Depends(get_db)
):
    """设置缓存项"""
    try:
        value = request_data.get("value")
        ttl_hours = request_data.get("ttl_hours")
        description = request_data.get("description", "")
        
        if value is None:
            raise HTTPException(status_code=400, detail="Value is required")
        
        unified_cache.set(db, namespace, key, value, ttl_hours=ttl_hours, description=description)
        
        return {
            "status": "success",
            "message": f"Cache item {namespace}:{key} set successfully"
        }
    except Exception as e:
        logger.error(f"Failed to set cache item {namespace}:{key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/invalidate/subscription/{subscription_id}")
async def invalidate_subscription_cache(
    subscription_id: int,
    event_type: str = "subscription_updated",
    db: Session = Depends(get_db)
):
    """手动失效订阅相关缓存"""
    try:
        cache_invalidation.invalidate_subscription_caches(db, subscription_id, event_type)
        return {
            "status": "success",
            "message": f"Invalidated caches for subscription {subscription_id}"
        }
    except Exception as e:
        logger.error(f"Failed to invalidate subscription cache {subscription_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/invalidate/video")
async def invalidate_video_cache(
    request_data: Dict[str, Any],
    db: Session = Depends(get_db)
):
    """手动失效视频相关缓存"""
    try:
        video_id = request_data.get("video_id")
        subscription_id = request_data.get("subscription_id")
        event_type = request_data.get("event_type", "video_updated")
        
        if not video_id or not subscription_id:
            raise HTTPException(status_code=400, detail="video_id and subscription_id are required")
        
        cache_invalidation.invalidate_video_caches(db, video_id, subscription_id, event_type)
        return {
            "status": "success",
            "message": f"Invalidated caches for video {video_id}"
        }
    except Exception as e:
        logger.error(f"Failed to invalidate video cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cache/health")
async def cache_health_check(db: Session = Depends(get_db)):
    """缓存系统健康检查"""
    try:
        # 基本功能测试
        test_namespace = "health_check"
        test_key = "test"
        test_value = {"timestamp": "2024-01-01T00:00:00", "test": True}
        
        # 写入测试
        unified_cache.set(db, test_namespace, test_key, test_value, description="Health check test")
        
        # 读取测试
        retrieved_value = unified_cache.get(db, test_namespace, test_key)
        
        # 删除测试
        deleted = unified_cache.delete(db, test_namespace, test_key)
        
        # 统计信息
        stats = unified_cache.get_stats()
        
        health_status = {
            "write_test": "passed",
            "read_test": "passed" if retrieved_value == test_value else "failed",
            "delete_test": "passed" if deleted else "failed",
            "stats": stats
        }
        
        overall_status = all(
            status == "passed" 
            for key, status in health_status.items() 
            if key.endswith("_test")
        )
        
        return {
            "status": "healthy" if overall_status else "unhealthy",
            "data": health_status
        }
        
    except Exception as e:
        logger.error(f"Cache health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }


@router.get("/cache/migration/analyze")
async def analyze_cache_migration(db: Session = Depends(get_db)):
    """分析当前缓存状态，评估迁移需求"""
    try:
        analysis = cache_migration.analyze_current_cache(db)
        return {
            "status": "success",
            "data": analysis
        }
    except Exception as e:
        logger.error(f"Failed to analyze cache migration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/migration/migrate")
async def migrate_cache_system(
    dry_run: bool = True,
    db: Session = Depends(get_db)
):
    """迁移旧缓存系统到统一缓存服务"""
    try:
        results = cache_migration.migrate_legacy_caches(db, dry_run)
        return {
            "status": "success",
            "data": results
        }
    except Exception as e:
        logger.error(f"Failed to migrate cache system: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/migration/cleanup")
async def cleanup_legacy_cache(
    dry_run: bool = True,
    db: Session = Depends(get_db)
):
    """清理已迁移的旧格式缓存"""
    try:
        results = cache_migration.cleanup_legacy_caches(db, dry_run)
        return {
            "status": "success",
            "data": results
        }
    except Exception as e:
        logger.error(f"Failed to cleanup legacy cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cache/migration/validate")
async def validate_cache_migration(db: Session = Depends(get_db)):
    """验证缓存迁移结果"""
    try:
        results = cache_migration.validate_migration(db)
        return {
            "status": "success",
            "data": results
        }
    except Exception as e:
        logger.error(f"Failed to validate cache migration: {e}")
        raise HTTPException(status_code=500, detail=str(e))
