"""
Cookie管理API端点
使用统一的schemas和异常处理
"""
from typing import List
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from ..models import Cookie
from ..schemas.cookie import (
    CookieCreate, CookieUpdate, CookieResponse,
    CookieToggleRequest, CookieValidationResponse
)
from ..schemas.common import SuccessResponse, PaginatedResponse, PaginationParams
from ..core.dependencies import DatabaseDep, CookieManagerDep
from ..core.exceptions import (
    cookie_not_found, validation_failed, BiliCuratorException,
    ErrorCode, BusinessError
)

router = APIRouter()


@router.get("/cookies", response_model=List[CookieResponse])
async def list_cookies(
    active_only: bool = None,
    db: Session = DatabaseDep
):
    """获取Cookie列表"""
    try:
        query = db.query(Cookie)
        
        if active_only is not None:
            query = query.filter(Cookie.is_active == active_only)
        
        cookies = query.order_by(Cookie.created_at.desc()).all()
        
        return [
            CookieResponse(
                id=cookie.id,
                name=cookie.name,
                sessdata=cookie.sessdata,
                bili_jct=cookie.bili_jct,
                dedeuserid=cookie.dedeuserid,
                is_active=cookie.is_active,
                failure_count=cookie.failure_count or 0,
                last_failure_at=cookie.last_failure_at.isoformat() if cookie.last_failure_at else None,
                usage_count=cookie.usage_count or 0,
                last_used=cookie.last_used.isoformat() if cookie.last_used else None,
                created_at=cookie.created_at.isoformat() if cookie.created_at else None,
                updated_at=cookie.updated_at.isoformat() if cookie.updated_at else None
            )
            for cookie in cookies
        ]
        
    except Exception as e:
        raise BusinessError(
            message="获取Cookie列表失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"cause": str(e)}
        )


@router.post("/cookies", response_model=CookieValidationResponse)
async def create_cookie(
    cookie: CookieCreate,
    background_tasks: BackgroundTasks,
    db: Session = DatabaseDep,
    cookie_manager = CookieManagerDep
):
    """创建新Cookie"""
    try:
        # 检查重名
        existing = db.query(Cookie).filter(Cookie.name == cookie.name).first()
        creating = False
        
        if not existing:
            new_cookie = Cookie(
                name=cookie.name,
                sessdata=cookie.sessdata,
                bili_jct=cookie.bili_jct or "",
                dedeuserid=cookie.dedeuserid or "",
                is_active=cookie.is_active if hasattr(cookie, 'is_active') else True,
                usage_count=0,
                failure_count=0
            )
            db.add(new_cookie)
            creating = True
        else:
            # 更新现有Cookie
            existing.sessdata = cookie.sessdata
            existing.bili_jct = cookie.bili_jct or ""
            existing.dedeuserid = cookie.dedeuserid or ""
            existing.is_active = cookie.is_active if hasattr(cookie, 'is_active') else True
            new_cookie = existing
        
        db.commit()
        db.refresh(new_cookie)
        
        # 后台验证Cookie
        is_valid = False
        try:
            is_valid = await cookie_manager.validate_cookie(new_cookie)
            if is_valid:
                cookie_manager.reset_failures(db, new_cookie.id)
            else:
                cookie_manager.record_failure(db, new_cookie.id, reason="create_validate_failed")
        except Exception:
            cookie_manager.record_failure(db, new_cookie.id, reason="create_validate_exception")
        
        return CookieValidationResponse(
            id=new_cookie.id,
            name=new_cookie.name,
            is_valid=is_valid,
            is_active=new_cookie.is_active,
            creating=creating
        )
        
    except BiliCuratorException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise BusinessError(
            message="创建Cookie失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"cause": str(e)}
        )


@router.get("/cookies/{cookie_id}", response_model=CookieResponse)
async def get_cookie(
    cookie_id: int,
    db: Session = DatabaseDep
):
    """获取单个Cookie详情"""
    cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
    if not cookie:
        raise cookie_not_found(cookie_id)
    
    return CookieResponse(
        id=cookie.id,
        name=cookie.name,
        sessdata=cookie.sessdata,
        bili_jct=cookie.bili_jct,
        dedeuserid=cookie.dedeuserid,
        is_active=cookie.is_active,
        failure_count=cookie.failure_count or 0,
        last_failure_at=cookie.last_failure_at.isoformat() if cookie.last_failure_at else None,
        usage_count=cookie.usage_count or 0,
        last_used=cookie.last_used.isoformat() if cookie.last_used else None,
        created_at=cookie.created_at.isoformat() if cookie.created_at else None,
        updated_at=cookie.updated_at.isoformat() if cookie.updated_at else None
    )


@router.put("/cookies/{cookie_id}", response_model=SuccessResponse)
async def update_cookie(
    cookie_id: int,
    cookie_update: CookieUpdate,
    db: Session = DatabaseDep
):
    """更新Cookie"""
    cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
    if not cookie:
        raise cookie_not_found(cookie_id)
    
    try:
        # 更新字段
        update_data = cookie_update.dict(exclude_unset=True)
        
        # 处理兼容性字段
        if 'active' in update_data:
            update_data['is_active'] = update_data.pop('active')
        
        for field, value in update_data.items():
            if hasattr(cookie, field):
                setattr(cookie, field, value)
        
        db.commit()
        
        return SuccessResponse(
            message="Cookie更新成功",
            data={"id": cookie.id, "name": cookie.name}
        )
        
    except BiliCuratorException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise BusinessError(
            message="更新Cookie失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"cookie_id": cookie_id, "cause": str(e)}
        )


@router.delete("/cookies/{cookie_id}", response_model=SuccessResponse)
async def delete_cookie(
    cookie_id: int,
    db: Session = DatabaseDep
):
    """删除Cookie"""
    cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
    if not cookie:
        raise cookie_not_found(cookie_id)
    
    try:
        cookie_name = cookie.name
        db.delete(cookie)
        db.commit()
        
        return SuccessResponse(
            message="Cookie删除成功",
            data={"id": cookie_id, "name": cookie_name}
        )
        
    except Exception as e:
        db.rollback()
        raise BusinessError(
            message="删除Cookie失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"cookie_id": cookie_id, "cause": str(e)}
        )


@router.post("/cookies/{cookie_id}/toggle", response_model=SuccessResponse)
async def toggle_cookie(
    cookie_id: int,
    toggle_request: CookieToggleRequest,
    db: Session = DatabaseDep
):
    """启用/禁用Cookie"""
    cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
    if not cookie:
        raise cookie_not_found(cookie_id)
    
    try:
        cookie.is_active = toggle_request.is_active
        db.commit()
        
        return SuccessResponse(
            message=f"Cookie已{'启用' if toggle_request.is_active else '禁用'}",
            data={
                "id": cookie.id,
                "name": cookie.name,
                "is_active": cookie.is_active
            }
        )
        
    except Exception as e:
        db.rollback()
        raise BusinessError(
            message="切换Cookie状态失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"cookie_id": cookie_id, "cause": str(e)}
        )


@router.post("/cookies/validate-all", response_model=SuccessResponse)
async def validate_all_cookies(
    background_tasks: BackgroundTasks,
    cookie_manager = CookieManagerDep
):
    """批量验证所有Cookie"""
    try:
        # 后台执行批量验证
        background_tasks.add_task(cookie_manager.batch_validate_cookies)
        
        return SuccessResponse(
            message="Cookie批量验证已启动",
            data={"triggered": True}
        )
        
    except Exception as e:
        raise BusinessError(
            message="启动Cookie批量验证失败",
            error_code=ErrorCode.INTERNAL_ERROR,
            details={"cause": str(e)}
        )


@router.get("/cookies/status", response_model=dict)
async def get_cookie_status(
    db: Session = DatabaseDep,
    cookie_manager = CookieManagerDep
):
    """获取Cookie状态概览"""
    try:
        cookies = db.query(Cookie).all()
        
        items = []
        for cookie in cookies:
            items.append({
                'id': cookie.id,
                'name': cookie.name,
                'is_active': cookie.is_active,
                'usage_count': cookie.usage_count or 0,
                'last_used': cookie.last_used.isoformat() if cookie.last_used else None,
                'failure_count': cookie.failure_count or 0,
                'last_failure_at': cookie.last_failure_at.isoformat() if cookie.last_failure_at else None,
                'created_at': cookie.created_at.isoformat() if cookie.created_at else None,
                'updated_at': cookie.updated_at.isoformat() if cookie.updated_at else None,
            })
        
        active_count = sum(1 for item in items if item['is_active'])
        inactive_count = len(items) - active_count
        
        return {
            'items': items,
            'current_cookie_id': getattr(cookie_manager, 'current_cookie_id', None),
            'counts': {
                'active': active_count,
                'inactive': inactive_count,
                'total': len(items)
            }
        }
        
    except Exception as e:
        raise BusinessError(
            message="获取Cookie状态失败",
            error_code=ErrorCode.DATABASE_ERROR,
            details={"cause": str(e)}
        )
