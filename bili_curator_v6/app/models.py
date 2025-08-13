"""
SQLite数据模型定义 - V6简化版
"""
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, Date, Float, ForeignKey
from sqlalchemy.orm import relationship, sessionmaker
try:
    from sqlalchemy.orm import declarative_base
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import os

Base = declarative_base()

class Subscription(Base):
    """订阅表"""
    __tablename__ = 'subscriptions'
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    type = Column(String(50), nullable=False)  # collection, uploader, keyword, specific_urls
    url = Column(Text)  # 合集URL
    uploader_id = Column(String(100))  # UP主ID
    keyword = Column(String(255))  # 搜索关键词
    specific_urls = Column(Text)  # JSON格式存储特定URL列表
    
    # 筛选条件
    date_after = Column(Date)  # 只下载此日期之后的视频
    date_before = Column(Date)  # 只下载此日期之前的视频
    min_likes = Column(Integer)  # 最小点赞数
    min_favorites = Column(Integer)  # 最小收藏数
    min_views = Column(Integer)  # 最小播放数
    
    # 统计信息
    total_videos = Column(Integer, default=0)  # 总视频数
    downloaded_videos = Column(Integer, default=0)  # 已下载视频数
    
    is_active = Column(Boolean, default=True)
    last_check = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关联的视频
    videos = relationship("Video", back_populates="subscription")

class Video(Base):
    """视频表"""
    __tablename__ = 'videos'
    
    id = Column(Integer, primary_key=True, index=True)
    bilibili_id = Column(String(50), unique=True, nullable=False)  # BV号或av号
    title = Column(Text, nullable=False)
    uploader = Column(String(255))
    uploader_id = Column(String(100))
    duration = Column(Integer, default=0)  # 时长(秒)
    upload_date = Column(DateTime)
    description = Column(Text)
    tags = Column(Text)  # JSON字符串存储标签
    
    # 文件路径
    video_path = Column(Text)  # 视频文件路径
    json_path = Column(Text)  # JSON文件路径
    thumbnail_path = Column(Text)  # 缩略图文件路径
    
    # 统计信息
    file_size = Column(Integer)  # 文件大小(字节)
    view_count = Column(Integer, default=0)  # 播放量
    
    # 状态信息
    downloaded = Column(Boolean, default=False)
    downloaded_at = Column(DateTime)  # 下载完成时间
    
    # 关联信息
    subscription_id = Column(Integer, ForeignKey('subscriptions.id'))  # 关联的订阅ID
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关联的订阅
    subscription = relationship("Subscription", back_populates="videos")

class Cookie(Base):
    """Cookie表"""
    __tablename__ = 'cookies'
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    sessdata = Column(Text, nullable=False)
    bili_jct = Column(String(255))
    dedeuserid = Column(String(100))
    is_active = Column(Boolean, default=True)
    usage_count = Column(Integer, default=0)
    last_used = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

class Settings(Base):
    """系统设置表"""
    __tablename__ = 'settings'
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class DownloadTask(Base):
    """下载任务表"""
    __tablename__ = 'download_tasks'
    
    id = Column(Integer, primary_key=True)
    bilibili_id = Column(String(50), nullable=False)  # 统一使用bilibili_id
    subscription_id = Column(Integer)
    status = Column(String(50), default='pending')  # pending, downloading, completed, failed
    progress = Column(Float, default=0.0)
    error_message = Column(Text)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

# 数据库连接和会话管理
class Database:
    def __init__(self, db_path: str = "data/bili_curator.db"):
        # 确保数据目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        self.engine = create_engine(f'sqlite:///{db_path}', echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        # 创建所有表
        Base.metadata.create_all(self.engine)
        
        # 初始化默认设置
        self._init_default_settings()
    
    def _init_default_settings(self):
        """初始化默认设置"""
        session = self.get_session()
        try:
            # 检查是否已有设置
            if session.query(Settings).count() == 0:
                default_settings = [
                    Settings(key="download_path", value="/app/downloads", description="默认下载路径"),
                    Settings(key="max_concurrent_downloads", value="3", description="最大并发下载数"),
                    Settings(key="check_interval", value="1800", description="订阅检查间隔(秒)"),
                    Settings(key="video_quality", value="1080p", description="视频质量偏好"),
                ]
                
                for setting in default_settings:
                    session.add(setting)
                
                session.commit()
        except Exception as e:
            session.rollback()
            print(f"初始化默认设置失败: {e}")
        finally:
            session.close()
    
    def get_session(self):
        """获取数据库会话"""
        return self.SessionLocal()

# 全局数据库实例
db = Database()

def get_db():
    """依赖注入：获取数据库会话"""
    database = db.get_session()
    try:
        yield database
    finally:
        database.close()
