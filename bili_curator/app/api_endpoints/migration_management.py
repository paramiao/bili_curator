"""
数据迁移管理API端点
处理字段命名统一化和其他数据迁移任务
"""
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from ..schemas.common import SuccessResponse
from ..core.dependencies import DatabaseDep
from ..core.exceptions import BusinessError, ErrorCode
from ..migrations.field_naming_migration import (
    FieldNamingMigration, run_field_naming_migration, get_field_naming_status
)

router = APIRouter()


@router.get("/migration/field-naming/status")
async def get_field_naming_migration_status(db: Session = DatabaseDep):
    """获取字段命名迁移状态"""
    try:
        status = get_field_naming_status()
        return SuccessResponse(
            message="获取迁移状态成功",
            data=status
        )
    except Exception as e:
        raise BusinessError(
            message="获取迁移状态失败",
            error_code=ErrorCode.INTERNAL_ERROR,
            details={"cause": str(e)}
        )


@router.post("/migration/field-naming/run")
async def run_field_naming_migration_endpoint(
    background_tasks: BackgroundTasks,
    db: Session = DatabaseDep
):
    """执行字段命名统一化迁移"""
    try:
        # 检查当前状态
        current_status = get_field_naming_status()
        
        if current_status.get("status") == "completed":
            return SuccessResponse(
                message="迁移已完成，无需重复执行",
                data=current_status
            )
        
        # 后台执行迁移
        def run_migration():
            try:
                result = run_field_naming_migration()
                return result
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e)
                }
        
        background_tasks.add_task(run_migration)
        
        return SuccessResponse(
            message="字段命名迁移已启动",
            data={"triggered": True, "current_status": current_status}
        )
        
    except Exception as e:
        raise BusinessError(
            message="启动字段命名迁移失败",
            error_code=ErrorCode.INTERNAL_ERROR,
            details={"cause": str(e)}
        )


@router.get("/migration/status")
async def get_all_migration_status(db: Session = DatabaseDep):
    """获取所有迁移任务的状态"""
    try:
        status = {
            "field_naming": get_field_naming_status(),
            "cache_migration": {
                "status": "available",
                "message": "缓存迁移功能可通过 /api/cache/migration 端点访问"
            }
        }
        
        return SuccessResponse(
            message="获取迁移状态成功",
            data=status
        )
        
    except Exception as e:
        raise BusinessError(
            message="获取迁移状态失败",
            error_code=ErrorCode.INTERNAL_ERROR,
            details={"cause": str(e)}
        )
