"""
订阅相关Pydantic模型定义
"""
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import date
from enum import Enum
import json


class SubscriptionType(str, Enum):
    """订阅类型枚举"""
    COLLECTION = "collection"
    UPLOADER = "uploader"
    KEYWORD = "keyword"
    SPECIFIC_URLS = "specific_urls"


class DownloadMode(str, Enum):
    """下载模式枚举（为V7 STRM扩展预留）"""
    LOCAL = "local"
    STRM = "strm"


class SubscriptionCreate(BaseModel):
    """创建订阅请求模型"""
    name: str = Field(..., min_length=1, max_length=255, description="订阅名称")
    type: SubscriptionType = Field(..., description="订阅类型")
    url: Optional[str] = Field(None, description="合集URL")
    uploader_id: Optional[str] = Field(None, max_length=100, description="UP主ID")
    keyword: Optional[str] = Field(None, max_length=255, description="搜索关键词")
    specific_urls: Optional[str] = Field(None, description="特定URL列表(JSON格式)")
    
    # 筛选条件
    date_after: Optional[date] = Field(None, description="只下载此日期之后的视频")
    date_before: Optional[date] = Field(None, description="只下载此日期之前的视频")
    min_likes: Optional[int] = Field(None, ge=0, description="最小点赞数")
    min_favorites: Optional[int] = Field(None, ge=0, description="最小收藏数")
    min_views: Optional[int] = Field(None, ge=0, description="最小播放数")
    
    # V7扩展字段
    download_mode: DownloadMode = Field(DownloadMode.LOCAL, description="下载模式")
    
    # 兼容历史/前端旧值：将 'user' 归一化为 'uploader'
    @validator('type', pre=True)
    def normalize_type_create(cls, v):
        if isinstance(v, str):
            s = v.strip().lower()
            if s == 'user':
                return 'uploader'
            return s
        return v

    @validator('name')
    def validate_name(cls, v):
        return v.strip()
    
    @validator('url')
    def validate_url(cls, v, values):
        # 统一去除首尾空白
        if isinstance(v, str):
            v = v.strip()
        if values.get('type') == SubscriptionType.COLLECTION and not v:
            raise ValueError('合集订阅必须提供URL')
        return v
    
    @validator('uploader_id')
    def validate_uploader_id(cls, v, values):
        if isinstance(v, str):
            v = v.strip()
        if values.get('type') == SubscriptionType.UPLOADER and not v:
            raise ValueError('UP主订阅必须提供UP主ID')
        return v
    
    @validator('keyword')
    def validate_keyword(cls, v, values):
        if isinstance(v, str):
            v = v.strip()
        if values.get('type') == SubscriptionType.KEYWORD and not v:
            raise ValueError('关键词订阅必须提供关键词')
        return v

    @validator('specific_urls')
    def normalize_specific_urls(cls, v):
        # 支持列表与字符串两种输入：列表将自动转为JSON字符串
        if isinstance(v, list):
            try:
                return json.dumps(v, ensure_ascii=False)
            except Exception:
                return str(v)
        return v.strip() if isinstance(v, str) else v

    # download_mode 大小写宽容
    @validator('download_mode', pre=True)
    def normalize_download_mode_create(cls, v):
        if isinstance(v, str):
            return v.strip().lower()
        return v


class SubscriptionUpdate(BaseModel):
    """更新订阅请求模型"""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="订阅名称")
    type: Optional[SubscriptionType] = Field(None, description="订阅类型")
    url: Optional[str] = Field(None, description="合集URL")
    uploader_id: Optional[str] = Field(None, max_length=100, description="UP主ID")
    keyword: Optional[str] = Field(None, max_length=255, description="搜索关键词")
    specific_urls: Optional[str] = Field(None, description="特定URL列表(JSON格式)")
    is_active: Optional[bool] = Field(None, description="是否激活")
    
    # 筛选条件
    date_after: Optional[date] = Field(None, description="只下载此日期之后的视频")
    date_before: Optional[date] = Field(None, description="只下载此日期之前的视频")
    min_likes: Optional[int] = Field(None, ge=0, description="最小点赞数")
    min_favorites: Optional[int] = Field(None, ge=0, description="最小收藏数")
    min_views: Optional[int] = Field(None, ge=0, description="最小播放数")
    
    # V7扩展字段
    download_mode: Optional[DownloadMode] = Field(None, description="下载模式")

    # 兼容历史/前端旧值：将 'user' 归一化为 'uploader'
    @validator('type', pre=True)
    def normalize_type_update(cls, v):
        if isinstance(v, str):
            s = v.strip().lower()
            if s == 'user':
                return 'uploader'
            return s
        return v

    # 统一去除首尾空白（仅对提供的字段生效）
    @validator('name', 'url', 'uploader_id', 'keyword', 'specific_urls', pre=True)
    def strip_strings(cls, v):
        return v.strip() if isinstance(v, str) else v

    # download_mode 大小写宽容
    @validator('download_mode', pre=True)
    def normalize_download_mode_update(cls, v):
        if isinstance(v, str):
            return v.strip().lower()
        return v


class SubscriptionResponse(BaseModel):
    """订阅响应模型"""
    id: int
    name: str
    type: SubscriptionType
    url: Optional[str] = None
    uploader_id: Optional[str] = None
    uploader_name: Optional[str] = None  # 添加UP主名称字段
    keyword: Optional[str] = None
    specific_urls: Optional[str] = None
    is_active: bool
    
    # 统计信息
    total_videos: int = 0
    downloaded_videos: int = 0
    expected_total: int = 0
    
    # 筛选条件
    date_after: Optional[date] = None
    date_before: Optional[date] = None
    min_likes: Optional[int] = None
    min_favorites: Optional[int] = None
    min_views: Optional[int] = None
    
    # V7扩展字段
    download_mode: DownloadMode = DownloadMode.LOCAL
    # 便于前端直接判断：是否为 STRM 订阅（派生字段，不改变既有契约）
    is_strm: bool = False
    
    # 时间戳
    created_at: str
    updated_at: str
    last_check: Optional[str] = None
    expected_total_synced_at: Optional[str] = None
    
    class Config:
        from_attributes = True

    # 兼容历史数据：将 'user' 归一化为 'uploader'，避免因旧值导致校验失败
    @validator('type', pre=True)
    def normalize_type(cls, v):
        if isinstance(v, str) and v.strip().lower() == 'user':
            return 'uploader'
        return v

    # 根据 download_mode 自动派生 is_strm
    @validator('is_strm', always=True)
    def derive_is_strm(cls, v, values):
        dm = values.get('download_mode')
        try:
            return (dm == DownloadMode.STRM) or (isinstance(dm, str) and dm.lower() == 'strm')
        except Exception:
            return False


class SubscriptionStats(BaseModel):
    """订阅统计模型"""
    subscription_id: int
    subscription_name: str
    type: SubscriptionType
    total_videos: int = 0
    local_total: int = 0
    remote_total: Optional[int] = None
    downloaded_videos: int = 0
    pending_videos: int = 0
    failed_videos: int = 0
    total_size: int = 0
    last_upload_date: Optional[str] = None
    download_mode: DownloadMode = DownloadMode.LOCAL


class ParseCollectionRequest(BaseModel):
    """解析合集请求模型"""
    url: str = Field(..., description="合集URL")
    
    @validator('url')
    def validate_url(cls, v):
        if not v or not v.strip():
            raise ValueError('URL不能为空')
        return v.strip()


class ParseCollectionResponse(BaseModel):
    """解析合集响应模型"""
    name: Optional[str] = None
    error: Optional[str] = None
