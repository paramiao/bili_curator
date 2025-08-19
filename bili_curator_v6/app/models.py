"""
SQLite数据模型定义 - V6简化版
"""
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, Date, Float, ForeignKey, text
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
    # 远端期望总数（例如合集远端返回的总视频数），以及最近同步时间
    expected_total = Column(Integer, default=0)
    expected_total_synced_at = Column(DateTime)
    
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
    file_size = Column(Integer)  # 视频文件大小(字节)
    audio_size = Column(Integer)  # 音频文件大小(字节，分离封装时存在)
    total_size = Column(Integer)  # 总大小(字节，视频+音频)，用于聚合加速
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
    # 失败计数与最近失败时间，用于按阈值禁用
    failure_count = Column(Integer, default=0)
    last_failure_at = Column(DateTime)
    usage_count = Column(Integer, default=0)
    last_used = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class Settings(Base):
    """系统设置表"""
    __tablename__ = 'settings'
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

# Pydantic models for API validation
from pydantic import BaseModel
from typing import Optional, List
from datetime import date

class SubscriptionUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    url: Optional[str] = None
    uploader_id: Optional[str] = None
    keyword: Optional[str] = None
    is_active: Optional[bool] = None
    date_after: Optional[date] = None
    date_before: Optional[date] = None
    min_likes: Optional[int] = None
    min_favorites: Optional[int] = None
    min_views: Optional[int] = None
    specific_urls: Optional[str] = None

class CookieCreate(BaseModel):
    name: str
    sessdata: str
    bili_jct: str
    dedeuserid: str
    is_active: Optional[bool] = True

class CookieUpdate(BaseModel):
    name: Optional[str] = None
    sessdata: Optional[str] = None
    bili_jct: Optional[str] = None
    dedeuserid: Optional[str] = None
    is_active: Optional[bool] = None

class SettingUpdate(BaseModel):
    value: str

class DownloadTask(Base):
    """下载任务表"""
    __tablename__ = 'download_tasks'
    
    id = Column(Integer, primary_key=True)
    # legacy 兼容列：旧库存在 NOT NULL download_tasks.video_id 约束
    # 为兼容旧数据流，保留该列（可空），新代码统一使用 bilibili_id
    video_id = Column(String(50), nullable=True)
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
    def __init__(self, db_path: str = None):
        # 统一数据库路径：优先环境变量 DB_PATH，其次默认 /app/data/bili_curator.db
        if db_path is None:
            db_path = os.environ.get("DB_PATH", "/app/data/bili_curator.db")
        # 确保数据目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        self.engine = create_engine(f'sqlite:///{db_path}', echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        # 创建所有表
        Base.metadata.create_all(self.engine)

        # 迁移旧表结构，补齐缺失列
        self._migrate_schema()

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

    def _migrate_schema(self):
        """轻量级迁移：为旧SQLite数据库补齐缺失的列"""
        conn = self.engine.connect()
        try:
            # 查询表结构辅助函数
            def has_column(table: str, col: str) -> bool:
                rows = conn.exec_driver_sql(f"PRAGMA table_info('{table}')").fetchall()
                cols = {r[1] for r in rows}
                return col in cols

            # subscriptions 表缺失列
            if not has_column('subscriptions', 'updated_at'):
                conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN updated_at DATETIME")
            if not has_column('subscriptions', 'total_videos'):
                conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN total_videos INTEGER DEFAULT 0")
            if not has_column('subscriptions', 'downloaded_videos'):
                conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN downloaded_videos INTEGER DEFAULT 0")
            # 新增：远端期望总数及其同步时间
            if not has_column('subscriptions', 'expected_total'):
                conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN expected_total INTEGER DEFAULT 0")
            if not has_column('subscriptions', 'expected_total_synced_at'):
                conn.exec_driver_sql("ALTER TABLE subscriptions ADD COLUMN expected_total_synced_at DATETIME")

            # cookies 表缺失列
            if not has_column('cookies', 'updated_at'):
                conn.exec_driver_sql("ALTER TABLE cookies ADD COLUMN updated_at DATETIME")
            # 新增：失败阈值相关列（幂等）
            if not has_column('cookies', 'failure_count'):
                conn.exec_driver_sql("ALTER TABLE cookies ADD COLUMN failure_count INTEGER DEFAULT 0")
            if not has_column('cookies', 'last_failure_at'):
                conn.exec_driver_sql("ALTER TABLE cookies ADD COLUMN last_failure_at DATETIME")

            # download_tasks 表：补齐缺失列
            # 1) 如缺少 video_id（旧库可能没有），则新增可空的 video_id 以兼容当前模型
            if not has_column('download_tasks', 'video_id'):
                try:
                    conn.exec_driver_sql("ALTER TABLE download_tasks ADD COLUMN video_id VARCHAR(50)")
                except Exception as ee:
                    print(f"新增 download_tasks.video_id 失败: {ee}")

            # 2) 新增 bilibili_id，并从旧的 video_id 迁移数据
            if not has_column('download_tasks', 'bilibili_id'):
                conn.exec_driver_sql("ALTER TABLE download_tasks ADD COLUMN bilibili_id VARCHAR(50)")
                # 如果存在旧列 video_id，将其数据迁移到 bilibili_id
                if has_column('download_tasks', 'video_id'):
                    try:
                        conn.exec_driver_sql("UPDATE download_tasks SET bilibili_id = video_id WHERE bilibili_id IS NULL")
                    except Exception as ee:
                        print(f"迁移download_tasks.video_id到bilibili_id失败: {ee}")

            # videos 表：补齐大小相关列（audio_size、total_size）
            if not has_column('videos', 'audio_size'):
                conn.exec_driver_sql("ALTER TABLE videos ADD COLUMN audio_size INTEGER")
            if not has_column('videos', 'total_size'):
                conn.exec_driver_sql("ALTER TABLE videos ADD COLUMN total_size INTEGER")

            # settings 表：为旧库补齐时间列，并确保 key 上存在唯一索引
            # 1) 时间列（部分旧库可能没有 created_at/updated_at，避免 UPSERT 更新 updated_at 报错）
            if not has_column('settings', 'created_at'):
                try:
                    conn.exec_driver_sql("ALTER TABLE settings ADD COLUMN created_at DATETIME")
                except Exception as ee:
                    print(f"新增 settings.created_at 失败: {ee}")
            if not has_column('settings', 'updated_at'):
                try:
                    conn.exec_driver_sql("ALTER TABLE settings ADD COLUMN updated_at DATETIME")
                except Exception as ee:
                    print(f"新增 settings.updated_at 失败: {ee}")

            # 2) 唯一索引（支持 ON CONFLICT(key) 语法；旧库若未声明 UNIQUE 约束将导致 UPSERT 失败）
            try:
                conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS idx_settings_key_unique ON settings(key)")
            except Exception as ee:
                print(f"创建 settings.key 唯一索引失败: {ee}")

        except Exception as e:
            print(f"数据库迁移失败: {e}")
        finally:
            conn.close()
    
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
