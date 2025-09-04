"""
统一配置管理系统
解决配置分散和硬编码问题
"""
import os
from typing import Optional, List
from pydantic_settings import BaseSettings
from pydantic import Field, validator
from pathlib import Path


class DatabaseConfig(BaseSettings):
    """数据库配置"""
    db_path: str = Field(
        default="/app/data/bilibili_curator.db",
        env="DB_PATH",
        description="数据库文件路径"
    )
    db_pool_size: int = Field(
        default=20,
        env="DB_POOL_SIZE",
        description="数据库连接池大小"
    )
    db_timeout: int = Field(
        default=30,
        env="DB_TIMEOUT",
        description="数据库连接超时时间(秒)"
    )


class DownloadConfig(BaseSettings):
    """下载配置"""
    download_path: str = Field(
        default="/app/downloads",
        env="DOWNLOAD_PATH",
        description="本地下载目录"
    )
    strm_path: str = Field(
        default="/app/strm",
        env="STRM_PATH",
        description="STRM文件目录"
    )
    strm_cache_dir: str = Field(
        default="/app/cache/strm",
        env="STRM_CACHE_DIR",
        description="STRM视频缓存目录"
    )
    max_concurrent_downloads: int = Field(
        default=3,
        env="MAX_CONCURRENT_DOWNLOADS",
        ge=1,
        le=10,
        description="最大并发下载数"
    )
    download_timeout: int = Field(
        default=1800,
        env="DOWNLOAD_TIMEOUT",
        ge=60,
        description="单个视频下载超时时间(秒)"
    )
    retry_attempts: int = Field(
        default=3,
        env="RETRY_ATTEMPTS",
        ge=0,
        le=10,
        description="下载失败重试次数"
    )
    retry_delay: int = Field(
        default=60,
        env="RETRY_DELAY",
        ge=10,
        description="重试延迟时间(秒)"
    )


class CacheConfig(BaseSettings):
    """缓存配置"""
    cache_ttl_hours: int = Field(
        default=1,
        env="CACHE_TTL_HOURS",
        ge=1,
        le=168,
        description="缓存TTL(小时)"
    )
    cache_max_size: int = Field(
        default=1000,
        env="CACHE_MAX_SIZE",
        ge=100,
        description="内存缓存最大条目数"
    )
    enable_dual_write: bool = Field(
        default=True,
        env="ENABLE_DUAL_WRITE",
        description="启用双写缓存"
    )
    enable_settings_read: bool = Field(
        default=True,
        env="ENABLE_SETTINGS_READ",
        description="启用从Settings表读取缓存"
    )
    cache_consistency_check_interval: int = Field(
        default=3600,
        env="CACHE_CONSISTENCY_CHECK_INTERVAL",
        ge=300,
        description="缓存一致性检查间隔(秒)"
    )


class ExternalAPIConfig(BaseSettings):
    """外部API配置"""
    bilibili_timeout: int = Field(
        default=30,
        env="BILIBILI_TIMEOUT",
        ge=5,
        le=120,
        description="B站API超时时间(秒)"
    )
    bilibili_retry_attempts: int = Field(
        default=3,
        env="BILIBILI_RETRY_ATTEMPTS",
        ge=1,
        le=10,
        description="B站API重试次数"
    )
    bilibili_rate_limit: int = Field(
        default=60,
        env="BILIBILI_RATE_LIMIT",
        ge=10,
        description="B站API请求频率限制(请求/分钟)"
    )
    yt_dlp_timeout: int = Field(
        default=300,
        env="YT_DLP_TIMEOUT",
        ge=60,
        description="yt-dlp超时时间(秒)"
    )
    user_agent: str = Field(
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        env="USER_AGENT",
        description="HTTP请求User-Agent"
    )
    ffmpeg_path: str = Field(
        default="ffmpeg",
        env="FFMPEG_PATH",
        description="FFmpeg 可执行文件路径或名称(位于PATH时可为 'ffmpeg')"
    )


class WebServerConfig(BaseSettings):
    """Web服务器配置"""
    host: str = Field(
        default="0.0.0.0",
        env="HOST",
        description="服务器监听地址"
    )
    port: int = Field(
        default=8080,
        env="PORT",
        ge=1,
        le=65535,
        description="服务器监听端口"
    )
    external_url: Optional[str] = Field(
        default=None,
        env="EXTERNAL_URL",
        description="外部访问URL，用于STRM文件生成（如：https://your-domain.com:8080）"
    )
    workers: int = Field(
        default=1,
        env="WORKERS",
        ge=1,
        le=8,
        description="工作进程数"
    )
    reload: bool = Field(
        default=False,
        env="RELOAD",
        description="开发模式自动重载"
    )
    log_level: str = Field(
        default="INFO",
        env="LOG_LEVEL",
        description="日志级别"
    )
    
    @validator('log_level')
    def validate_log_level(cls, v):
        allowed_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in allowed_levels:
            raise ValueError(f'日志级别必须是: {", ".join(allowed_levels)}')
        return v.upper()


class SecurityConfig(BaseSettings):
    """安全配置"""
    secret_key: str = Field(
        default="your-secret-key-change-in-production",
        env="SECRET_KEY",
        min_length=32,
        description="应用密钥"
    )
    allowed_hosts: List[str] = Field(
        default=["*"],
        env="ALLOWED_HOSTS",
        description="允许的主机列表"
    )
    cors_origins: List[str] = Field(
        default=["*"],
        env="CORS_ORIGINS",
        description="CORS允许的源列表"
    )
    enable_https: bool = Field(
        default=False,
        env="ENABLE_HTTPS",
        description="启用HTTPS"
    )
    ssl_cert_path: Optional[str] = Field(
        default=None,
        env="SSL_CERT_PATH",
        description="SSL证书路径"
    )
    ssl_key_path: Optional[str] = Field(
        default=None,
        env="SSL_KEY_PATH",
        description="SSL私钥路径"
    )


class MonitoringConfig(BaseSettings):
    """监控配置"""
    enable_metrics: bool = Field(
        default=True,
        env="ENABLE_METRICS",
        description="启用性能指标收集"
    )
    metrics_interval: int = Field(
        default=60,
        env="METRICS_INTERVAL",
        ge=10,
        description="指标收集间隔(秒)"
    )
    enable_health_check: bool = Field(
        default=True,
        env="ENABLE_HEALTH_CHECK",
        description="启用健康检查"
    )
    health_check_interval: int = Field(
        default=30,
        env="HEALTH_CHECK_INTERVAL",
        ge=10,
        description="健康检查间隔(秒)"
    )
    log_file_path: Optional[str] = Field(
        default=None,
        env="LOG_FILE_PATH",
        description="日志文件路径"
    )
    log_max_size: int = Field(
        default=100,
        env="LOG_MAX_SIZE",
        ge=10,
        description="日志文件最大大小(MB)"
    )
    log_backup_count: int = Field(
        default=5,
        env="LOG_BACKUP_COUNT",
        ge=1,
        description="日志文件备份数量"
    )


class Settings(BaseSettings):
    """主配置类"""
    # 环境配置
    environment: str = Field(
        default="production",
        env="ENVIRONMENT",
        description="运行环境: development, staging, production"
    )
    debug: bool = Field(
        default=False,
        env="DEBUG",
        description="调试模式"
    )
    
    # 子配置
    database: DatabaseConfig = DatabaseConfig()
    download: DownloadConfig = DownloadConfig()
    cache: CacheConfig = CacheConfig()
    external_api: ExternalAPIConfig = ExternalAPIConfig()
    web_server: WebServerConfig = WebServerConfig()
    security: SecurityConfig = SecurityConfig()
    monitoring: MonitoringConfig = MonitoringConfig()
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        
    @validator('environment')
    def validate_environment(cls, v):
        allowed_envs = ['development', 'staging', 'production']
        if v.lower() not in allowed_envs:
            raise ValueError(f'环境必须是: {", ".join(allowed_envs)}')
        return v.lower()
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ensure_directories()
    
    def _ensure_directories(self):
        """确保必要的目录存在"""
        directories = [
            Path(self.database.db_path).parent,
            Path(self.download.download_path),
            Path(self.download.strm_path),
        ]
        
        if self.monitoring.log_file_path:
            directories.append(Path(self.monitoring.log_file_path).parent)
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    
    @property
    def is_development(self) -> bool:
        """是否为开发环境"""
        return self.environment == "development"
    
    @property
    def is_production(self) -> bool:
        """是否为生产环境"""
        return self.environment == "production"
    
    def get_database_url(self) -> str:
        """获取数据库URL"""
        return f"sqlite:///{self.database.db_path}"
    
    def get_cache_ttl_seconds(self) -> int:
        """获取缓存TTL(秒)"""
        return self.cache.cache_ttl_hours * 3600


# 全局配置实例
settings = Settings()


def get_settings() -> Settings:
    """获取配置实例（用于依赖注入）"""
    return settings


def get_config() -> Settings:
    """兼容别名：返回全局配置实例。
    注意：推荐使用 get_settings()，后续将统一该命名。
    """
    return settings


def reload_settings():
    """重新加载配置"""
    global settings
    settings = Settings()
    return settings
