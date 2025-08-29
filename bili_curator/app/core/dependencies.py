"""
依赖注入系统
统一管理服务依赖和配置注入
"""
from typing import Generator, Optional
import logging
import traceback
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..models import Database
from .config import Settings, get_settings
from .exceptions import DatabaseError, ErrorCode
from ..services.unified_cache_service import UnifiedCacheService
from ..services.cache_invalidation_service import CacheInvalidationService
from ..services.cache_migration_service import CacheMigrationService
from ..cookie_manager import cookie_manager
from ..services.strm_proxy_service import STRMProxyService
from ..services.strm_file_manager import STRMFileManager
from ..services.enhanced_downloader import EnhancedDownloader


def get_database() -> Generator[Session, None, None]:
    """获取数据库会话（统一使用models.py中的全局实例）"""
    from ..models import db
    logger = logging.getLogger(__name__)
    session: Optional[Session] = None
    
    try:
        session = db.get_session()
        yield session
    except Exception as e:
        if session:
            try:
                session.rollback()
            except Exception:
                pass
        
        logger.error(f"数据库会话创建失败: {e}")
        raise DatabaseError(
            operation="get_session",
            message="数据库连接失败",
            cause=e
        )
    finally:
        if session:
            try:
                session.close()
            except Exception:
                pass


def get_config() -> Settings:
    """获取应用配置"""
    return get_settings()


def get_cache_service() -> UnifiedCacheService:
    """获取统一缓存服务"""
    return UnifiedCacheService()


def get_cache_invalidation_service() -> CacheInvalidationService:
    """获取缓存失效服务"""
    return CacheInvalidationService()


def get_cache_migration_service() -> CacheMigrationService:
    """获取缓存迁移服务"""
    return CacheMigrationService()


def get_cookie_manager():
    """获取Cookie管理器"""
    return cookie_manager


def get_strm_proxy_service() -> STRMProxyService:
    """获取STRM代理服务（单例模式）"""
    global _strm_proxy_service
    if _strm_proxy_service is None:
        cookie_manager = get_cookie_manager()
        _strm_proxy_service = STRMProxyService(cookie_manager)
    return _strm_proxy_service


def get_strm_file_manager() -> STRMFileManager:
    """获取STRM文件管理器（单例模式）"""
    global _strm_file_manager
    if _strm_file_manager is None:
        _strm_file_manager = STRMFileManager()
    return _strm_file_manager


def get_enhanced_downloader() -> EnhancedDownloader:
    """获取增强下载器（单例模式）"""
    global _enhanced_downloader
    if _enhanced_downloader is None:
        strm_proxy = get_strm_proxy_service()
        strm_file_manager = get_strm_file_manager()
        cache_service = get_cache_service()
        _enhanced_downloader = EnhancedDownloader(strm_proxy, strm_file_manager, cache_service)
    return _enhanced_downloader


# 常用依赖组合
DatabaseDep = Depends(get_database)
ConfigDep = Depends(get_config)
CacheServiceDep = Depends(get_cache_service)
CacheInvalidationDep = Depends(get_cache_invalidation_service)
CacheMigrationDep = Depends(get_cache_migration_service)
CookieManagerDep = Depends(get_cookie_manager)
STRMProxyDep = Depends(get_strm_proxy_service)
STRMFileManagerDep = Depends(get_strm_file_manager)
EnhancedDownloaderDep = Depends(get_enhanced_downloader)

# 简化的依赖别名
get_database_session = get_database
get_unified_cache_service = get_cache_service


def require_valid_subscription_id(subscription_id: int, db: Session = DatabaseDep):
    """验证订阅ID是否存在"""
    from ..models import Subscription
    subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not subscription:
        from .exceptions import subscription_not_found
        raise subscription_not_found(subscription_id)
    return subscription


def require_valid_video_id(video_id: str, db: Session = DatabaseDep):
    """验证视频ID是否存在"""
    from ..models import Video
    video = db.query(Video).filter(Video.bilibili_id == video_id).first()
    if not video:
        from .exceptions import video_not_found
        raise video_not_found(video_id)
    return video


def require_valid_cookie_id(cookie_id: int, db: Session = DatabaseDep):
    """验证Cookie ID是否存在"""
    from ..models import Cookie
    cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
    if not cookie:
        from .exceptions import cookie_not_found
        raise cookie_not_found(cookie_id)
    return cookie


class ServiceContainer:
    """服务容器，管理服务实例的生命周期"""
    
    def __init__(self):
        self._services = {}
        self._singletons = {}
    
    def register_singleton(self, service_type: type, instance):
        """注册单例服务"""
        self._singletons[service_type] = instance
    
    def register_factory(self, service_type: type, factory_func):
        """注册工厂函数"""
        self._services[service_type] = factory_func
    
    def get(self, service_type: type):
        """获取服务实例"""
        # 优先返回单例
        if service_type in self._singletons:
            return self._singletons[service_type]
        
        # 使用工厂函数创建
        if service_type in self._services:
            return self._services[service_type]()
        
        raise ValueError(f"Service {service_type} not registered")


# 全局服务容器
container = ServiceContainer()

# 注册核心服务
container.register_singleton(Settings, get_settings())
container.register_factory(UnifiedCacheService, lambda: UnifiedCacheService())
container.register_factory(CacheInvalidationService, lambda: CacheInvalidationService())
container.register_factory(CacheMigrationService, lambda: CacheMigrationService())

# 注册STRM相关服务为单例
_strm_proxy_service = None
_strm_file_manager = None
_enhanced_downloader = None


def get_service(service_type: type):
    """从容器获取服务实例"""
    return container.get(service_type)
