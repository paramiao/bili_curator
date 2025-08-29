"""
bili_curator V6 主入口文件
"""
import uvicorn
import asyncio
from contextlib import asynccontextmanager
from loguru import logger
import sys
import os

# 添加app目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.api import app
from app.scheduler import scheduler
from app.models import db
from app.core.config import get_config

_LOGGING_CONFIGURED = False

def _setup_logging_once():
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    # 配置日志（一次性）
    logger.remove()  # 移除默认处理器，避免重复
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO"
    )
    logger.add(
        "logs/bili_curator.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        compression="zip"
    )
    _LOGGING_CONFIGURED = True

# 初始化日志（防重复）
_setup_logging_once()

@asynccontextmanager
async def lifespan(app):
    """应用生命周期管理"""
    # 启动时执行
    version = os.getenv('VERSION', 'v6')
    if version == 'v7':
        logger.info("🎬 bili_curator V7 - B站视频智能管理平台")
        logger.info("📝 版本: 7.0.0")
        logger.info("🚀 支持本地下载 + STRM流媒体双模式")
        logger.info("🚀 bili_curator V7 正在启动...")
    else:
        logger.info("🎬 bili_curator V6 - B站视频下载管理系统")
        logger.info("📝 版本: 6.0.0")
        logger.info("🏠 专为家用个人设计的简化版本")
        logger.info("🚀 bili_curator V6 正在启动...")
    
    # 确保必要目录存在
    os.makedirs("data", exist_ok=True)
    os.makedirs("data/downloads", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    # V7版本：创建STRM目录
    if version == 'v7':
        strm_path = os.getenv('STRM_PATH', '/app/strm')
        os.makedirs(strm_path, exist_ok=True)
        logger.info(f"📁 STRM目录已创建: {strm_path}")
    
    # 初始化数据库
    logger.info("📊 初始化数据库...")
    
    # 执行启动时一致性检查
    logger.info("🔍 执行本地目录与数据库一致性检查...")
    try:
        from app.consistency_checker import startup_consistency_check
        consistency_stats = startup_consistency_check()
        if consistency_stats:
            logger.info(f"✅ 一致性检查完成: 数据库记录 {consistency_stats['total_db_records']}, 本地文件 {consistency_stats['files_found']}")
            if consistency_stats['files_missing'] > 0:
                logger.warning(f"⚠️ 发现 {consistency_stats['files_missing']} 个文件丢失，已同步数据库状态")
        else:
            logger.warning("⚠️ 一致性检查失败，但不影响服务启动")
    except Exception as e:
        logger.error(f"❌ 一致性检查失败: {e}")
    
    # 启动视频检测服务
    logger.info("🎬 启动视频检测服务...")
    try:
        from app.video_detection_service import video_detection_service
        await video_detection_service.start_service()
        logger.info("✅ 视频检测服务启动成功")
    except Exception as e:
        logger.error(f"❌ 视频检测服务启动失败: {e}")
    
    # 启动STRM代理服务 (V7)
    if version == 'v7':
        logger.info("🎯 启动STRM代理服务...")
        try:
            from app.core.dependencies import get_strm_proxy_service
            strm_proxy = get_strm_proxy_service()
            await strm_proxy.start()
            logger.info("✅ STRM代理服务启动成功")
        except Exception as e:
            logger.error(f"❌ STRM代理服务启动失败: {e}")
    
    # 启动调度器
    logger.info("⏰ 启动定时任务调度器...")
    scheduler.start()
    
    if version == 'v7':
        logger.info("✅ bili_curator V7 启动完成!")
        logger.info("🌐 Web界面: http://localhost:8080")
        logger.info("📚 API文档: http://localhost:8080/docs")
        logger.info("🎯 STRM流媒体服务: http://localhost:8080/strm")
    else:
        logger.info("✅ bili_curator V6 启动完成!")
        logger.info("🌐 Web界面: http://localhost:8080")
        logger.info("📚 API文档: http://localhost:8080/docs")
    
    yield
    
    # 关闭时执行
    if version == 'v7':
        logger.info("🛑 bili_curator V7 正在关闭...")
    else:
        logger.info("🛑 bili_curator V6 正在关闭...")
    
    # 停止视频检测服务
    try:
        from app.video_detection_service import video_detection_service
        await video_detection_service.stop_service()
        logger.info("⏹️ 视频检测服务已停止")
    except Exception as e:
        logger.error(f"❌ 停止视频检测服务失败: {e}")
    
    scheduler.stop()
    logger.info("👋 再见!")

# 设置应用生命周期
app.router.lifespan_context = lifespan

def main():
    """主函数"""
    # 运行FastAPI应用
    cfg = get_config()
    uvicorn.run(
        app,
        host=cfg.web_server.host,
        port=cfg.web_server.port,
        reload=cfg.web_server.reload,
        access_log=True,
        log_config=None  # 使用自定义日志配置
    )

if __name__ == "__main__":
    main()
