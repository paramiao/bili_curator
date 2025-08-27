"""
视频相关Pydantic模型定义
"""
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from enum import Enum


class VideoStatus(str, Enum):
    """视频状态枚举"""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


class VideoResponse(BaseModel):
    """视频响应模型"""
    id: int
    bilibili_id: str = Field(..., description="B站视频ID")
    title: str
    uploader: Optional[str] = None
    uploader_id: Optional[str] = None
    duration: int = 0
    upload_date: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    pic: Optional[str] = None  # 添加缩略图字段
    pubdate: Optional[str] = None  # 添加发布日期字段
    desc: Optional[str] = None  # 添加描述字段别名
    
    # 文件路径
    video_path: Optional[str] = None
    json_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    
    # 统计信息
    file_size: Optional[int] = None
    audio_size: Optional[int] = None
    total_size: Optional[int] = None
    view_count: int = 0
    
    # 状态信息
    downloaded: bool = False
    downloaded_at: Optional[str] = None
    download_failed: bool = False
    failure_reason: Optional[str] = None
    failure_count: int = 0
    last_failure_at: Optional[str] = None
    
    # 关联信息
    subscription_id: Optional[int] = None
    created_at: str
    updated_at: str
    
    class Config:
        from_attributes = True


class VideoUpdate(BaseModel):
    """视频更新模型"""
    title: Optional[str] = Field(None, min_length=1, description="视频标题")
    uploader: Optional[str] = Field(None, description="UP主名称")
    uploader_id: Optional[str] = Field(None, description="UP主ID")
    duration: Optional[int] = Field(None, ge=0, description="时长(秒)")
    description: Optional[str] = Field(None, description="视频描述")
    tags: Optional[str] = Field(None, description="标签(JSON格式)")
    view_count: Optional[int] = Field(None, ge=0, description="播放量")
    subscription_id: Optional[int] = Field(None, description="关联订阅ID")


class VideoEnqueueRequest(BaseModel):
    """视频入队请求模型"""
    video_id: str = Field(..., description="视频ID")
    title: Optional[str] = Field(None, description="视频标题")
    webpage_url: Optional[str] = Field(None, description="视频URL")
    
    @validator('video_id')
    def validate_video_id(cls, v):
        if not v or not v.strip():
            raise ValueError('视频ID不能为空')
        return v.strip()


class VideoSearchParams(BaseModel):
    """视频搜索参数"""
    subscription_id: Optional[int] = Field(None, description="订阅ID")
    uploader: Optional[str] = Field(None, description="UP主名称")
    title: Optional[str] = Field(None, description="标题关键词")
    downloaded: Optional[bool] = Field(None, description="是否已下载")
    failed: Optional[bool] = Field(None, description="是否下载失败")
    date_from: Optional[str] = Field(None, description="开始日期")
    date_to: Optional[str] = Field(None, description="结束日期")


class PendingVideoInfo(BaseModel):
    """待下载视频信息"""
    id: str = Field(..., description="视频ID")
    title: str = Field(..., description="视频标题")
    uploader: Optional[str] = None
    duration: Optional[int] = None
    upload_date: Optional[str] = None
    view_count: Optional[int] = None
    url: Optional[str] = None


class PendingVideosResponse(BaseModel):
    """待下载视频列表响应"""
    subscription_id: int
    remote_total: Optional[int] = None
    existing: int = 0
    pending: int = 0
    failed: int = 0
    videos: List[PendingVideoInfo] = []
    cached: bool = False
    cache_time: Optional[str] = None
