"""
Cookie相关Pydantic模型定义
"""
from pydantic import BaseModel, Field, validator
from typing import Optional


class CookieCreate(BaseModel):
    """创建Cookie请求模型"""
    name: str = Field(..., min_length=1, max_length=100, description="Cookie名称")
    sessdata: str = Field(..., min_length=1, description="SESSDATA值")
    bili_jct: str = Field(..., description="bili_jct值")
    dedeuserid: str = Field(..., description="DedeUserID值")
    is_active: Optional[bool] = Field(True, description="是否激活")
    
    @validator('name', 'sessdata', 'bili_jct', 'dedeuserid')
    def validate_not_empty(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError('字段不能为空')
        return v


class CookieUpdate(BaseModel):
    """更新Cookie请求模型"""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="Cookie名称")
    sessdata: Optional[str] = Field(None, min_length=1, description="SESSDATA值")
    bili_jct: Optional[str] = Field(None, description="bili_jct值")
    dedeuserid: Optional[str] = Field(None, description="DedeUserID值")
    is_active: Optional[bool] = Field(None, description="是否激活")
    
    @validator('name', 'sessdata', 'bili_jct', 'dedeuserid')
    def validate_not_empty(cls, v):
        if v is not None and isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError('字段不能为空')
        return v


class CookieResponse(BaseModel):
    """Cookie响应模型"""
    id: int
    name: str
    sessdata: str
    bili_jct: Optional[str] = None
    dedeuserid: Optional[str] = None
    is_active: bool
    failure_count: int = 0
    last_failure_at: Optional[str] = None
    usage_count: int = 0
    last_used: Optional[str] = None
    created_at: str
    updated_at: str
    
    class Config:
        from_attributes = True


class CookieToggleRequest(BaseModel):
    """Cookie启用/禁用请求模型"""
    id: int = Field(..., description="Cookie ID")
    is_active: bool = Field(..., description="是否激活")


class CookieValidationResponse(BaseModel):
    """Cookie验证响应模型"""
    id: int
    name: str
    is_valid: bool
    is_active: bool
    creating: bool = False
    error: Optional[str] = None
