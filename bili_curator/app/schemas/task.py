"""
任务相关Pydantic模型定义
"""
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    """任务类型枚举"""
    VIDEO_DOWNLOAD = "video_download"
    SUBSCRIPTION_SYNC = "subscription_sync"
    CACHE_REFRESH = "cache_refresh"
    DATA_MIGRATION = "data_migration"
    SYSTEM_MAINTENANCE = "system_maintenance"


class TaskPriority(str, Enum):
    """任务优先级枚举"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class DownloadTaskCreate(BaseModel):
    """创建下载任务请求模型"""
    video_id: str = Field(..., description="视频ID")
    subscription_id: Optional[int] = Field(None, description="关联订阅ID")
    priority: TaskPriority = Field(TaskPriority.NORMAL, description="任务优先级")
    retry_count: int = Field(0, ge=0, le=5, description="重试次数")
    
    @validator('video_id')
    def validate_video_id(cls, v):
        if not v or not v.strip():
            raise ValueError('视频ID不能为空')
        return v.strip()


class DownloadTaskResponse(BaseModel):
    """下载任务响应模型"""
    id: int
    video_id: str
    subscription_id: Optional[int] = None
    status: TaskStatus
    priority: TaskPriority
    
    # 进度信息
    progress: float = 0.0
    current_step: Optional[str] = None
    total_steps: int = 0
    completed_steps: int = 0
    
    # 时间信息
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    estimated_completion: Optional[str] = None
    
    # 错误信息
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    
    # 资源使用
    cpu_usage: Optional[float] = None
    memory_usage: Optional[int] = None
    disk_usage: Optional[int] = None
    
    class Config:
        from_attributes = True


class TaskQueueStats(BaseModel):
    """任务队列统计模型"""
    total_tasks: int = 0
    pending_tasks: int = 0
    running_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    cancelled_tasks: int = 0
    
    # 按优先级统计
    urgent_tasks: int = 0
    high_priority_tasks: int = 0
    normal_priority_tasks: int = 0
    low_priority_tasks: int = 0
    
    # 性能指标
    average_completion_time: Optional[float] = None
    success_rate: Optional[float] = None
    queue_throughput: Optional[float] = None


class TaskBatchOperation(BaseModel):
    """任务批量操作模型"""
    task_ids: List[int] = Field(..., min_items=1, max_items=100, description="任务ID列表")
    operation: str = Field(..., description="操作类型: cancel, retry, delete")
    
    @validator('operation')
    def validate_operation(cls, v):
        allowed_operations = ['cancel', 'retry', 'delete', 'pause', 'resume']
        if v not in allowed_operations:
            raise ValueError(f'操作类型必须是: {", ".join(allowed_operations)}')
        return v


class TaskProgressUpdate(BaseModel):
    """任务进度更新模型"""
    task_id: int
    progress: float = Field(..., ge=0.0, le=100.0, description="进度百分比")
    current_step: Optional[str] = Field(None, description="当前步骤")
    message: Optional[str] = Field(None, description="进度消息")
    metadata: Optional[Dict[str, Any]] = Field(None, description="额外元数据")


class TaskLogEntry(BaseModel):
    """任务日志条目模型"""
    task_id: int
    level: str = Field(..., description="日志级别: DEBUG, INFO, WARNING, ERROR")
    message: str = Field(..., description="日志消息")
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: Optional[Dict[str, Any]] = None
    
    @validator('level')
    def validate_level(cls, v):
        allowed_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
        if v.upper() not in allowed_levels:
            raise ValueError(f'日志级别必须是: {", ".join(allowed_levels)}')
        return v.upper()


class TaskScheduleRequest(BaseModel):
    """任务调度请求模型"""
    task_type: TaskType = Field(..., description="任务类型")
    schedule_time: Optional[datetime] = Field(None, description="调度时间")
    recurring: bool = Field(False, description="是否重复执行")
    cron_expression: Optional[str] = Field(None, description="Cron表达式")
    parameters: Optional[Dict[str, Any]] = Field(None, description="任务参数")
    
    @validator('cron_expression')
    def validate_cron(cls, v, values):
        if values.get('recurring') and not v:
            raise ValueError('重复任务必须提供Cron表达式')
        return v
