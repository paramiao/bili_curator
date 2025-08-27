"""
全局异常处理器
统一错误响应格式和日志记录
"""
import logging
import traceback
from datetime import datetime
from typing import Union
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from pydantic import ValidationError as PydanticValidationError

from .exceptions import (
    BiliCuratorException, 
    create_http_exception,
    ErrorCode,
    DatabaseError,
    ValidationError
)
from .config import get_settings

logger = logging.getLogger(__name__)


async def bili_curator_exception_handler(
    request: Request, 
    exc: BiliCuratorException
) -> JSONResponse:
    """处理自定义业务异常"""
    
    # 记录异常日志
    logger.error(
        f"BiliCurator异常: {exc.error_code.value} - {exc.message}",
        extra={
            "error_code": exc.error_code.value,
            "details": exc.details,
            "path": request.url.path,
            "method": request.method,
            "cause": str(exc.cause) if exc.cause else None,
            "traceback": traceback.format_exc() if exc.cause else None,
        }
    )
    
    # 确定HTTP状态码
    http_exc = create_http_exception(exc)
    
    # 构建响应
    response_data = {
        "success": False,
        "error": {
            "code": exc.error_code.value,
            "message": exc.message,
            "details": exc.details
        },
        "timestamp": datetime.now().isoformat(),
        "path": request.url.path
    }
    # 在调试模式下，附加底层 cause 与 traceback，便于定位
    settings = get_settings()
    if getattr(settings, "debug", False):
        response_data["error"].setdefault("details", {})
        if exc.cause:
            response_data["error"]["details"]["cause"] = str(exc.cause)
            try:
                response_data["error"]["details"]["traceback"] = "".join(traceback.format_exception(type(exc.cause), exc.cause, exc.cause.__traceback__))
            except Exception:
                response_data["error"]["details"]["traceback"] = traceback.format_exc()
    
    return JSONResponse(
        status_code=http_exc.status_code,
        content=response_data
    )


async def http_exception_handler(
    request: Request, 
    exc: HTTPException
) -> JSONResponse:
    """处理HTTP异常"""
    
    logger.warning(
        f"HTTP异常: {exc.status_code} - {exc.detail}",
        extra={
            "status_code": exc.status_code,
            "path": request.url.path,
            "method": request.method
        }
    )
    
    # 如果detail已经是我们的格式，直接返回
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        detail = exc.detail.copy()
        detail["timestamp"] = datetime.now().isoformat()
        detail["path"] = request.url.path
        return JSONResponse(
            status_code=exc.status_code,
            content=detail
        )
    
    # 标准化响应格式
    response_data = {
        "success": False,
        "error": {
            "code": f"HTTP_{exc.status_code}",
            "message": str(exc.detail) if exc.detail else "HTTP错误",
            "details": {}
        },
        "timestamp": datetime.now().isoformat(),
        "path": request.url.path
    }
    
    return JSONResponse(
        status_code=exc.status_code,
        content=response_data
    )


async def validation_exception_handler(
    request: Request, 
    exc: RequestValidationError
) -> JSONResponse:
    """处理请求验证异常"""
    
    logger.warning(
        f"请求验证失败: {len(exc.errors())} 个错误",
        extra={
            "errors": exc.errors(),
            "path": request.url.path,
            "method": request.method
        }
    )
    
    # 格式化验证错误
    validation_errors = []
    for error in exc.errors():
        field_path = " -> ".join(str(loc) for loc in error["loc"])
        validation_errors.append({
            "field": field_path,
            "message": error["msg"],
            "type": error["type"],
            "input": error.get("input")
        })
    
    response_data = {
        "success": False,
        "error": {
            "code": ErrorCode.VALIDATION_ERROR.value,
            "message": "请求参数验证失败",
            "details": {
                "validation_errors": validation_errors,
                "error_count": len(validation_errors)
            }
        },
        "timestamp": datetime.now().isoformat(),
        "path": request.url.path
    }
    
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=response_data
    )


async def pydantic_validation_exception_handler(
    request: Request, 
    exc: PydanticValidationError
) -> JSONResponse:
    """处理Pydantic验证异常"""
    
    logger.warning(
        f"Pydantic验证失败: {len(exc.errors())} 个错误",
        extra={
            "errors": exc.errors(),
            "path": request.url.path,
            "method": request.method
        }
    )
    
    # 转换为统一格式
    validation_errors = []
    for error in exc.errors():
        field_path = " -> ".join(str(loc) for loc in error["loc"])
        validation_errors.append({
            "field": field_path,
            "message": error["msg"],
            "type": error["type"]
        })
    
    response_data = {
        "success": False,
        "error": {
            "code": ErrorCode.VALIDATION_ERROR.value,
            "message": "数据验证失败",
            "details": {
                "validation_errors": validation_errors,
                "error_count": len(validation_errors)
            }
        },
        "timestamp": datetime.now().isoformat(),
        "path": request.url.path
    }
    
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=response_data
    )


async def sqlalchemy_exception_handler(
    request: Request, 
    exc: SQLAlchemyError
) -> JSONResponse:
    """处理SQLAlchemy数据库异常"""
    
    # 确定错误类型和消息
    if isinstance(exc, IntegrityError):
        error_code = ErrorCode.DATABASE_CONSTRAINT_ERROR
        message = "数据库约束违反"
        status_code = status.HTTP_409_CONFLICT
    else:
        error_code = ErrorCode.DATABASE_ERROR
        message = "数据库操作失败"
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    
    logger.error(
        f"数据库异常: {type(exc).__name__} - {str(exc)}",
        extra={
            "exception_type": type(exc).__name__,
            "path": request.url.path,
            "method": request.method,
            "traceback": traceback.format_exc()
        }
    )
    
    response_data = {
        "success": False,
        "error": {
            "code": error_code.value,
            "message": message,
            "details": {
                "exception_type": type(exc).__name__,
                "database_error": True
            }
        },
        "timestamp": datetime.now().isoformat(),
        "path": request.url.path
    }
    settings = get_settings()
    if getattr(settings, "debug", False):
        response_data["error"]["details"]["traceback"] = traceback.format_exc()
    
    return JSONResponse(
        status_code=status_code,
        content=response_data
    )


async def general_exception_handler(
    request: Request, 
    exc: Exception
) -> JSONResponse:
    """处理未捕获的通用异常"""
    
    logger.error(
        f"未处理异常: {type(exc).__name__} - {str(exc)}",
        extra={
            "exception_type": type(exc).__name__,
            "path": request.url.path,
            "method": request.method,
            "traceback": traceback.format_exc()
        }
    )
    
    response_data = {
        "success": False,
        "error": {
            "code": ErrorCode.INTERNAL_ERROR.value,
            "message": "服务器内部错误",
            "details": {
                "exception_type": type(exc).__name__,
                "internal_error": True
            }
        },
        "timestamp": datetime.now().isoformat(),
        "path": request.url.path
    }
    settings = get_settings()
    if getattr(settings, "debug", False):
        response_data["error"]["details"]["traceback"] = traceback.format_exc()
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=response_data
    )


def setup_exception_handlers(app):
    """设置全局异常处理器"""
    
    # 自定义业务异常
    app.add_exception_handler(BiliCuratorException, bili_curator_exception_handler)
    
    # HTTP异常
    app.add_exception_handler(HTTPException, http_exception_handler)
    
    # 验证异常
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(PydanticValidationError, pydantic_validation_exception_handler)
    
    # 数据库异常
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
    
    # 通用异常（必须放在最后）
    app.add_exception_handler(Exception, general_exception_handler)
