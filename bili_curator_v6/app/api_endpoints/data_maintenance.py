"""
数据维护相关API端点
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..services.data_consistency_service import data_consistency_service

router = APIRouter(prefix="/api/maintenance", tags=["数据维护"])


@router.post("/check-consistency")
async def check_data_consistency(db: Session = Depends(get_db)):
    """执行数据一致性检查和自动修复"""
    try:
        result = await data_consistency_service.auto_fix_data_issues(db)
        return {
            "success": True,
            "result": result,
            "summary": {
                "remote_totals_refreshed": result["remote_totals"].get("refreshed", 0),
                "pending_mismatches": len(result["pending_counts"].get("mismatches", [])),
                "failed_videos_cleaned": result["failed_videos"].get("cleaned_count", 0)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"数据一致性检查失败: {e}")


@router.get("/consistency-report")
async def get_consistency_report(db: Session = Depends(get_db)):
    """获取数据一致性报告（只检查不修复）"""
    try:
        pending_check = data_consistency_service.check_pending_counts_accuracy(db)
        return {
            "success": True,
            "report": {
                "subscriptions_checked": pending_check.get("checked", 0),
                "data_mismatches": pending_check.get("mismatches", []),
                "recommendations": pending_check.get("recommendations", [])
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成一致性报告失败: {e}")


@router.post("/refresh-remote-totals")
async def refresh_all_remote_totals(db: Session = Depends(get_db)):
    """强制刷新所有订阅的远端总数缓存"""
    try:
        result = await data_consistency_service.check_and_fix_remote_totals(db)
        return {
            "success": True,
            "result": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新远端总数失败: {e}")
