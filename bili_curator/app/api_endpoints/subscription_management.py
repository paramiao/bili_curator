"""
订阅管理API端点
使用统一的schemas和异常处理
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, BackgroundTasks, Request
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..models import Subscription, Video
from ..schemas.subscription import (
    SubscriptionCreate, SubscriptionUpdate, SubscriptionResponse,
    SubscriptionStats, ParseCollectionRequest, ParseCollectionResponse
)
from ..schemas.common import SuccessResponse, PaginationParams, PaginatedResponse
from ..core.dependencies import DatabaseDep, ConfigDep
from ..core.exceptions import (
    subscription_not_found, validation_failed, BiliCuratorException,
    ErrorCode, BusinessError
)
from ..services.subscription_stats import recompute_all_subscriptions
from ..downloader import downloader

router = APIRouter()


@router.get("/subscriptions", response_model=PaginatedResponse)
async def list_subscriptions(
    pagination: PaginationParams = Depends(),
    active_only: Optional[bool] = None,
    db: Session = DatabaseDep
):
    """获取订阅列表"""
    try:
        query = db.query(Subscription)
        
        if active_only is not None:
            query = query.filter(Subscription.is_active == active_only)
        
        # 获取总数
        total = query.count()
        
        # 分页查询
        subscriptions = (
            query.offset(pagination.offset)
            .limit(pagination.size)
            .all()
        )
        
        # 转换为响应模型
        items = []
        for sub in subscriptions:
            # 计算统计信息
            total_videos = db.query(Video).filter(Video.subscription_id == sub.id).count()
            downloaded_videos = db.query(Video).filter(
                Video.subscription_id == sub.id,
                Video.video_path.isnot(None)
            ).count()
            
            item = SubscriptionResponse(
                id=sub.id,
                name=sub.name,
                type=sub.type,
                url=sub.url,
                uploader_id=sub.uploader_id,
                keyword=sub.keyword,
                specific_urls=sub.specific_urls,
                is_active=sub.is_active,
                total_videos=total_videos,
                downloaded_videos=downloaded_videos,
                expected_total=sub.expected_total or 0,
                date_after=sub.date_after,
                date_before=sub.date_before,
                min_likes=sub.min_likes,
                min_favorites=sub.min_favorites,
                min_views=sub.min_views,
                download_mode=getattr(sub, 'download_mode', 'local'),
                created_at=sub.created_at.isoformat() if sub.created_at else None,
                updated_at=sub.updated_at.isoformat() if sub.updated_at else None,
                last_check=sub.last_check.isoformat() if sub.last_check else None,
                expected_total_synced_at=sub.expected_total_synced_at.isoformat() if sub.expected_total_synced_at else None
            )
            items.append(item)
        
        return PaginatedResponse(
            data=items,
            total=total,
            page=pagination.page,
            size=pagination.size
        )
        
    except BiliCuratorException:
        raise
    except Exception as e:
        raise BusinessError(
            message="获取订阅列表失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"cause": str(e)}
        )


@router.post("/subscriptions", response_model=SuccessResponse)
async def create_subscription(
    subscription: SubscriptionCreate,
    background_tasks: BackgroundTasks,
    db: Session = DatabaseDep
):
    """创建新订阅"""
    try:
        # 检查重名
        existing = db.query(Subscription).filter(Subscription.name == subscription.name).first()
        if existing:
            raise BusinessError(
                message=f"订阅名称 '{subscription.name}' 已存在",
                error_code=ErrorCode.SUBSCRIPTION_ALREADY_EXISTS
            )
        
        # 创建订阅
        new_subscription = Subscription(
            name=subscription.name,
            type=subscription.type.value,
            url=subscription.url,
            uploader_id=subscription.uploader_id,
            keyword=subscription.keyword,
            specific_urls=subscription.specific_urls,
            date_after=subscription.date_after,
            date_before=subscription.date_before,
            min_likes=subscription.min_likes,
            min_favorites=subscription.min_favorites,
            min_views=subscription.min_views,
            is_active=True
        )
        
        # V7扩展：添加download_mode字段（如果模型支持）
        if hasattr(new_subscription, 'download_mode'):
            new_subscription.download_mode = subscription.download_mode.value
        
        db.add(new_subscription)
        db.commit()
        db.refresh(new_subscription)
        
        # 后台重新计算统计
        background_tasks.add_task(
            recompute_all_subscriptions,
            db,
            touch_last_check=False
        )
        
        return SuccessResponse(
            message="订阅创建成功",
            data={"id": new_subscription.id, "name": new_subscription.name}
        )
        
    except BiliCuratorException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise BusinessError(
            message="创建订阅失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"cause": str(e)}
        )


@router.get("/subscriptions/{subscription_id}", response_model=SubscriptionResponse)
async def get_subscription(
    subscription_id: int,
    db: Session = DatabaseDep
):
    """获取单个订阅详情"""
    subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not subscription:
        raise subscription_not_found(subscription_id)
    
    try:
        # 计算统计信息
        total_videos = db.query(Video).filter(Video.subscription_id == subscription.id).count()
        downloaded_videos = db.query(Video).filter(
            Video.subscription_id == subscription.id,
            Video.video_path.isnot(None)
        ).count()
        
        return SubscriptionResponse(
            id=subscription.id,
            name=subscription.name,
            type=subscription.type,
            url=subscription.url,
            uploader_id=subscription.uploader_id,
            keyword=subscription.keyword,
            specific_urls=subscription.specific_urls,
            is_active=subscription.is_active,
            total_videos=total_videos,
            downloaded_videos=downloaded_videos,
            expected_total=subscription.expected_total or 0,
            date_after=subscription.date_after,
            date_before=subscription.date_before,
            min_likes=subscription.min_likes,
            min_favorites=subscription.min_favorites,
            min_views=subscription.min_views,
            download_mode=getattr(subscription, 'download_mode', 'local'),
            created_at=subscription.created_at.isoformat() if subscription.created_at else None,
            updated_at=subscription.updated_at.isoformat() if subscription.updated_at else None,
            last_check=subscription.last_check.isoformat() if subscription.last_check else None,
            expected_total_synced_at=subscription.expected_total_synced_at.isoformat() if subscription.expected_total_synced_at else None
        )
        
    except BiliCuratorException:
        raise
    except Exception as e:
        raise BusinessError(
            message="获取订阅详情失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"subscription_id": subscription_id, "cause": str(e)}
        )


@router.put("/subscriptions/{subscription_id}", response_model=SuccessResponse)
async def update_subscription(
    subscription_id: int,
    subscription_update: SubscriptionUpdate,
    background_tasks: BackgroundTasks,
    db: Session = DatabaseDep
):
    """更新订阅"""
    subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not subscription:
        raise subscription_not_found(subscription_id)
    
    try:
        # 更新字段
        update_data = subscription_update.dict(exclude_unset=True)
        
        # 处理兼容性字段
        if 'active' in update_data:
            update_data['is_active'] = update_data.pop('active')
        
        for field, value in update_data.items():
            if hasattr(subscription, field):
                # 枚举类型需要转换为值
                if hasattr(value, 'value'):
                    value = value.value
                setattr(subscription, field, value)
        
        db.commit()
        
        # 后台重新计算统计
        background_tasks.add_task(
            recompute_all_subscriptions,
            db,
            touch_last_check=False
        )
        
        return SuccessResponse(
            message="订阅更新成功",
            data={"id": subscription.id, "name": subscription.name}
        )
        
    except BiliCuratorException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise BusinessError(
            message="更新订阅失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"subscription_id": subscription_id, "cause": str(e)}
        )


@router.delete("/subscriptions/{subscription_id}", response_model=SuccessResponse)
async def delete_subscription(
    subscription_id: int,
    request: Request,
    keep_files: bool = True,
    force: bool = False,
    db: Session = DatabaseDep
):
    """删除订阅
    
    Args:
        subscription_id: 订阅ID
        keep_files: 是否保留本地文件 (默认True)
        force: 是否强制删除 (默认False，兼容旧版本)
    """
    subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not subscription:
        raise subscription_not_found(subscription_id)
    
    try:
        # 获取关联的视频记录
        videos = db.query(Video).filter(Video.subscription_id == subscription_id).all()
        video_count = len(videos)
        
        # 处理请求体参数（支持JSON格式）
        body = {}
        try:
            if request.headers.get('content-type') == 'application/json':
                body = await request.json()
                keep_files = body.get('keep_files', keep_files)
                force = body.get('force', force)
        except:
            pass  # 如果没有请求体，使用查询参数
        
        # 当传入keep_files参数时，自动设置force=True以允许删除
        if 'keep_files' in body:
            force = True
        
        if video_count > 0 and not force:
            # 仅阻止删除并记录清晰日志
            logger.warning(
                "删除订阅被阻止: subscription_id={}, video_count={}",
                subscription_id,
                video_count,
            )
            raise BusinessError(
                message=f"无法删除订阅，存在 {video_count} 个关联视频。请使用强制删除或通过前端界面选择删除模式。",
                error_code=ErrorCode.DATABASE_CONSTRAINT_ERROR,
                details={"subscription_id": subscription_id, "video_count": video_count}
            )
        
        deleted_files_count = 0
        if video_count > 0:
            # 如果不保留文件，删除本地文件
            if not keep_files:
                import os
                from pathlib import Path
                
                for video in videos:
                    # 删除视频文件
                    if video.video_path and os.path.exists(video.video_path):
                        try:
                            os.remove(video.video_path)
                            deleted_files_count += 1
                            logger.info(f"已删除视频文件: {video.video_path}")
                        except Exception as e:
                            logger.warning(f"删除视频文件失败: {video.video_path}, 错误: {e}")
                    
                    # 删除JSON文件
                    if video.json_path and os.path.exists(video.json_path):
                        try:
                            os.remove(video.json_path)
                            logger.info(f"已删除JSON文件: {video.json_path}")
                        except Exception as e:
                            logger.warning(f"删除JSON文件失败: {video.json_path}, 错误: {e}")
                    
                    # 删除缩略图文件
                    if video.thumbnail_path and os.path.exists(video.thumbnail_path):
                        try:
                            os.remove(video.thumbnail_path)
                            logger.info(f"已删除缩略图文件: {video.thumbnail_path}")
                        except Exception as e:
                            logger.warning(f"删除缩略图文件失败: {video.thumbnail_path}, 错误: {e}")
                
                # 尝试删除订阅目录（如果为空）
                try:
                    # 获取订阅目录路径
                    download_mode = getattr(subscription, 'download_mode', 'local')
                    if download_mode == 'strm':
                        base_dir = "/app/strm"
                    else:
                        base_dir = "/app/downloads"
                    
                    subscription_dir = os.path.join(base_dir, subscription.name)
                    if os.path.exists(subscription_dir) and os.path.isdir(subscription_dir):
                        # 检查目录是否为空
                        if not os.listdir(subscription_dir):
                            os.rmdir(subscription_dir)
                            logger.info(f"已删除空的订阅目录: {subscription_dir}")
                        else:
                            logger.info(f"订阅目录不为空，保留: {subscription_dir}")
                except Exception as e:
                    logger.warning(f"删除订阅目录失败: {e}")
            
            # 删除数据库中的视频记录
            deleted_rows = db.query(Video).filter(Video.subscription_id == subscription_id).delete(synchronize_session=False)
            logger.info(
                "删除关联视频记录: subscription_id={}, deleted_rows={}, keep_files={}, deleted_files={}",
                subscription_id,
                deleted_rows,
                keep_files,
                deleted_files_count
            )
        
        # 删除订阅记录
        db.delete(subscription)
        db.commit()
        
        return SuccessResponse(
            message=f"订阅删除成功{'（已保留本地文件）' if keep_files else '（已删除本地文件）'}",
            data={
                "id": subscription_id, 
                "name": subscription.name, 
                "keep_files": keep_files,
                "deleted_videos": video_count,
                "deleted_files": deleted_files_count
            }
        )
        
    except BiliCuratorException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise BusinessError(
            message="删除订阅失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"subscription_id": subscription_id, "cause": str(e)}
        )


@router.get("/subscriptions/stats", response_model=List[SubscriptionStats])
async def get_subscription_stats(
    active_only: Optional[bool] = True,
    db: Session = DatabaseDep
):
    """获取订阅统计信息"""
    try:
        query = db.query(Subscription)
        if active_only:
            query = query.filter(Subscription.is_active == True)
        
        subscriptions = query.all()
        stats = []
        
        for sub in subscriptions:
            # 计算统计信息
            total_videos = db.query(Video).filter(Video.subscription_id == sub.id).count()
            downloaded_videos = db.query(Video).filter(
                Video.subscription_id == sub.id,
                Video.video_path.isnot(None)
            ).count()
            failed_videos = db.query(Video).filter(
                Video.subscription_id == sub.id,
                Video.download_failed == True
            ).count()
            
            # 计算总大小
            total_size = db.query(func.sum(Video.total_size)).filter(
                Video.subscription_id == sub.id,
                Video.total_size.isnot(None)
            ).scalar() or 0
            
            # 最新上传日期
            latest_video = db.query(Video).filter(
                Video.subscription_id == sub.id
            ).order_by(Video.upload_date.desc()).first()
            
            stat = SubscriptionStats(
                subscription_id=sub.id,
                subscription_name=sub.name,
                type=sub.type,
                total_videos=total_videos,
                local_total=total_videos,
                remote_total=sub.expected_total,
                downloaded_videos=downloaded_videos,
                pending_videos=max(0, total_videos - downloaded_videos - failed_videos),
                failed_videos=failed_videos,
                total_size=total_size,
                last_upload_date=latest_video.upload_date.isoformat() if latest_video and latest_video.upload_date else None,
                download_mode=getattr(sub, 'download_mode', 'local')
            )
            stats.append(stat)
        
        return stats
        
    except Exception as e:
        raise BusinessError(
            message="获取订阅统计失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"cause": str(e)}
        )


@router.post("/subscriptions/parse-collection", response_model=ParseCollectionResponse)
async def parse_collection(
    request: ParseCollectionRequest,
    db: Session = DatabaseDep
):
    """解析合集URL获取名称"""
    try:
        # 调用downloader的解析功能
        result = await downloader.parse_collection_info(request.url)
        
        if result.get('error'):
            return ParseCollectionResponse(
                name=None,
                error=result['error']
            )
        
        return ParseCollectionResponse(
            name=result.get('name'),
            error=None
        )
        
    except Exception as e:
        return ParseCollectionResponse(
            name=None,
            error=f"解析失败: {str(e)}"
        )
