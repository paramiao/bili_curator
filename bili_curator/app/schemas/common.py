"""
通用Pydantic模型定义
"""
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, date
from enum import Enum


class ResponseStatus(str, Enum):
    """响应状态枚举"""
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"


class BaseResponse(BaseModel):
    """标准API响应基类"""
    status: ResponseStatus = ResponseStatus.SUCCESS
    message: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class SuccessResponse(BaseResponse):
    """成功响应"""
    data: Optional[Any] = None


class ErrorResponse(BaseResponse):
    """错误响应"""
    status: ResponseStatus = ResponseStatus.ERROR
    error_code: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class StandardResponse(BaseModel):
    """与V6保持兼容的标准响应模型
    字段：success/message/data
    用于现有端点的快速迁移，避免大范围改动。
    """
    success: bool = True
    message: Optional[str] = None
    data: Optional[Any] = None


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(1, ge=1, description="页码")
    size: int = Field(20, ge=1, description="每页大小（>100 将自动截断为 100）")
    
    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    @validator('size', pre=True)
    def _clamp_size(cls, v):
        """将过大的 size 截断为 100，避免客户端传入 1000 导致异常。
        也确保非常小/非法值回退到合规范围。
        """
        try:
            n = int(v)
        except Exception:
            return 20
        if n < 1:
            return 1
        return min(n, 100)


class PaginatedResponse(BaseResponse):
    """分页响应"""
    data: List[Any] = []
    total: int = 0
    page: int = 1
    size: int = 20
    pages: int = 0
    
    def __init__(self, **data):
        super().__init__(**data)
        if self.total > 0 and self.size > 0:
            self.pages = (self.total + self.size - 1) // self.size


class SettingUpdate(BaseModel):
    """系统设置更新模型"""
    value: str = Field(..., description="设置值")
    
    @validator('value')
    def validate_value(cls, v):
        if not isinstance(v, str):
            return str(v)
        return v.strip()


class BulkOperationRequest(BaseModel):
    """批量操作请求"""
    ids: List[int] = Field(..., min_items=1, max_items=100, description="ID列表")
    operation: str = Field(..., description="操作类型")
    params: Optional[Dict[str, Any]] = None


class BulkOperationResponse(BaseResponse):
    """批量操作响应"""
    total: int = 0
    success: int = 0
    failed: int = 0
    errors: List[Dict[str, Any]] = []
