"""
统一异常处理系统
解决错误处理不一致问题
"""
from typing import Optional, Dict, Any
from fastapi import HTTPException, status
from enum import Enum


class ErrorCode(str, Enum):
    """错误码枚举"""
    # 通用错误
    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    
    # 业务错误
    SUBSCRIPTION_NOT_FOUND = "SUBSCRIPTION_NOT_FOUND"
    SUBSCRIPTION_ALREADY_EXISTS = "SUBSCRIPTION_ALREADY_EXISTS"
    VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"
    VIDEO_ALREADY_EXISTS = "VIDEO_ALREADY_EXISTS"
    COOKIE_NOT_FOUND = "COOKIE_NOT_FOUND"
    COOKIE_INVALID = "COOKIE_INVALID"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_ALREADY_RUNNING = "TASK_ALREADY_RUNNING"
    
    # 下载错误
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    DOWNLOAD_TIMEOUT = "DOWNLOAD_TIMEOUT"
    DOWNLOAD_QUOTA_EXCEEDED = "DOWNLOAD_QUOTA_EXCEEDED"
    DOWNLOAD_PERMISSION_DENIED = "DOWNLOAD_PERMISSION_DENIED"
    
    # 外部API错误
    BILIBILI_API_ERROR = "BILIBILI_API_ERROR"
    BILIBILI_API_TIMEOUT = "BILIBILI_API_TIMEOUT"
    BILIBILI_API_RATE_LIMITED = "BILIBILI_API_RATE_LIMITED"
    BILIBILI_VIDEO_UNAVAILABLE = "BILIBILI_VIDEO_UNAVAILABLE"
    
    # 缓存错误
    CACHE_ERROR = "CACHE_ERROR"
    CACHE_CONSISTENCY_ERROR = "CACHE_CONSISTENCY_ERROR"
    CACHE_MIGRATION_ERROR = "CACHE_MIGRATION_ERROR"
    
    # 数据库错误
    DATABASE_ERROR = "DATABASE_ERROR"
    DATABASE_CONNECTION_ERROR = "DATABASE_CONNECTION_ERROR"
    DATABASE_CONSTRAINT_ERROR = "DATABASE_CONSTRAINT_ERROR"
    
    # 文件系统错误
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_PERMISSION_DENIED = "FILE_PERMISSION_DENIED"
    DISK_SPACE_INSUFFICIENT = "DISK_SPACE_INSUFFICIENT"


class BiliCuratorException(Exception):
    """基础异常类"""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None
    ):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.cause = cause
        super().__init__(message)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        result = {
            "error_code": self.error_code.value,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


class ValidationError(BiliCuratorException):
    """验证错误"""
    
    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        value: Optional[Any] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        error_details = details or {}
        if field:
            error_details["field"] = field
        if value is not None:
            error_details["value"] = str(value)
        
        super().__init__(
            message=message,
            error_code=ErrorCode.VALIDATION_ERROR,
            details=error_details
        )


class NotFoundError(BiliCuratorException):
    """资源未找到错误"""
    
    def __init__(
        self,
        resource_type: str,
        resource_id: Optional[Any] = None,
        message: Optional[str] = None
    ):
        if not message:
            if resource_id:
                message = f"{resource_type} (ID: {resource_id}) 未找到"
            else:
                message = f"{resource_type} 未找到"
        
        details = {"resource_type": resource_type}
        if resource_id:
            details["resource_id"] = str(resource_id)
        
        super().__init__(
            message=message,
            error_code=ErrorCode.NOT_FOUND,
            details=details
        )


class BusinessError(BiliCuratorException):
    """业务逻辑错误"""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            details=details
        )


class ExternalAPIError(BiliCuratorException):
    """外部API错误"""
    
    def __init__(
        self,
        service: str,
        message: str,
        status_code: Optional[int] = None,
        response_data: Optional[Dict[str, Any]] = None,
        error_code: ErrorCode = ErrorCode.BILIBILI_API_ERROR
    ):
        details = {"service": service}
        if status_code:
            details["status_code"] = status_code
        if response_data:
            details["response_data"] = response_data
        
        super().__init__(
            message=f"{service} API错误: {message}",
            error_code=error_code,
            details=details
        )


class DownloadError(BiliCuratorException):
    """下载错误"""
    
    def __init__(
        self,
        video_id: str,
        message: str,
        error_code: ErrorCode = ErrorCode.DOWNLOAD_FAILED,
        details: Optional[Dict[str, Any]] = None
    ):
        error_details = details or {}
        error_details["video_id"] = video_id
        
        super().__init__(
            message=f"视频 {video_id} 下载失败: {message}",
            error_code=error_code,
            details=error_details
        )


class CacheError(BiliCuratorException):
    """缓存错误"""
    
    def __init__(
        self,
        operation: str,
        key: Optional[str] = None,
        message: Optional[str] = None,
        error_code: ErrorCode = ErrorCode.CACHE_ERROR,
        details: Optional[Dict[str, Any]] = None
    ):
        if not message:
            message = f"缓存操作失败: {operation}"
        
        error_details = details or {}
        error_details["operation"] = operation
        if key:
            error_details["key"] = key
        
        super().__init__(
            message=message,
            error_code=error_code,
            details=error_details
        )


class DatabaseError(BiliCuratorException):
    """数据库错误"""
    
    def __init__(
        self,
        operation: str,
        table: Optional[str] = None,
        message: Optional[str] = None,
        error_code: ErrorCode = ErrorCode.DATABASE_ERROR,
        cause: Optional[Exception] = None
    ):
        if not message:
            message = f"数据库操作失败: {operation}"
        
        details = {"operation": operation}
        if table:
            details["table"] = table
        
        super().__init__(
            message=message,
            error_code=error_code,
            details=details,
            cause=cause
        )


def create_http_exception(
    exc: BiliCuratorException,
    status_code: Optional[int] = None
) -> HTTPException:
    """将自定义异常转换为HTTP异常"""
    
    # 根据错误类型确定HTTP状态码
    if not status_code:
        error_code_to_status = {
            ErrorCode.VALIDATION_ERROR: status.HTTP_400_BAD_REQUEST,
            ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
            ErrorCode.SUBSCRIPTION_NOT_FOUND: status.HTTP_404_NOT_FOUND,
            ErrorCode.VIDEO_NOT_FOUND: status.HTTP_404_NOT_FOUND,
            ErrorCode.COOKIE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
            ErrorCode.TASK_NOT_FOUND: status.HTTP_404_NOT_FOUND,
            ErrorCode.FILE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
            ErrorCode.PERMISSION_DENIED: status.HTTP_403_FORBIDDEN,
            ErrorCode.FILE_PERMISSION_DENIED: status.HTTP_403_FORBIDDEN,
            ErrorCode.RATE_LIMIT_EXCEEDED: status.HTTP_429_TOO_MANY_REQUESTS,
            ErrorCode.BILIBILI_API_RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
            ErrorCode.SUBSCRIPTION_ALREADY_EXISTS: status.HTTP_409_CONFLICT,
            ErrorCode.VIDEO_ALREADY_EXISTS: status.HTTP_409_CONFLICT,
            ErrorCode.TASK_ALREADY_RUNNING: status.HTTP_409_CONFLICT,
            ErrorCode.DOWNLOAD_TIMEOUT: status.HTTP_408_REQUEST_TIMEOUT,
            ErrorCode.BILIBILI_API_TIMEOUT: status.HTTP_408_REQUEST_TIMEOUT,
        }
        status_code = error_code_to_status.get(
            exc.error_code, 
            status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
    return HTTPException(
        status_code=status_code,
        detail={
            "success": False,
            "error": exc.to_dict(),
            "timestamp": None  # 将在全局异常处理器中设置
        }
    )


# 常用异常创建函数
def subscription_not_found(subscription_id: int) -> NotFoundError:
    """订阅未找到异常"""
    return NotFoundError("订阅", subscription_id)


def video_not_found(video_id: str) -> NotFoundError:
    """视频未找到异常"""
    return NotFoundError("视频", video_id)


def cookie_not_found(cookie_id: int) -> NotFoundError:
    """Cookie未找到异常"""
    return NotFoundError("Cookie", cookie_id)


def task_not_found(task_id: int) -> NotFoundError:
    """任务未找到异常"""
    return NotFoundError("任务", task_id)


def bilibili_api_error(message: str, status_code: Optional[int] = None) -> ExternalAPIError:
    """B站API错误"""
    return ExternalAPIError("Bilibili", message, status_code)


def download_failed(video_id: str, reason: str) -> DownloadError:
    """下载失败错误"""
    return DownloadError(video_id, reason)


def validation_failed(field: str, value: Any, reason: str) -> ValidationError:
    """验证失败错误"""
    return ValidationError(f"字段 {field} 验证失败: {reason}", field, value)
