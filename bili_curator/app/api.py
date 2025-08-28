# UA 策略：无 Cookie 使用随机 UA，Cookie 模式可用稳定 UA
# UA 策略已抽取至 services.http_utils.get_user_agent

"""
FastAPI API路由定义
"""
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy import func, case
from typing import Any, Dict, List, Optional, Tuple
import re
from pydantic import BaseModel
from datetime import datetime, timedelta
import logging
import asyncio
import random
import json
import os
from pathlib import Path

from .models import (
    Subscription, Video, DownloadTask, Cookie, Settings,
    get_db
)
from .schemas import (
    SubscriptionCreate, SubscriptionUpdate, SubscriptionResponse,
    CookieCreate, CookieUpdate, CookieResponse,
    SettingUpdate, SuccessResponse, ErrorResponse
)
from .core.config import get_settings
from .core.exceptions import (
    subscription_not_found, video_not_found, cookie_not_found,
    validation_failed, BiliCuratorException
)
from .core.exception_handlers import setup_exception_handlers
from .scheduler import scheduler, task_manager
from .services.subscription_stats import recompute_all_subscriptions
from .cookie_manager import cookie_manager
from .api_endpoints.cache_management import router as cache_router
from .api_endpoints.subscription_management import router as subscription_router
from .api_endpoints.cookie_management import router as cookie_router
from .api_endpoints.migration_management import router as migration_router
from .api_endpoints.strm_management import router as strm_router
from .downloader import downloader
from .video_detection_service import video_detection_service
from .queue_manager import yt_dlp_semaphore, get_subscription_lock, request_queue
from .services.http_utils import get_user_agent
from .consistency_checker import consistency_checker, periodic_consistency_check, startup_consistency_check
from .services.remote_sync_service import remote_sync_service
from .services.pending_list_service import pending_list_service
from .services.data_consistency_service import data_consistency_service
from .auto_import import auto_import_service
from .services.uploader_resolver_service import uploader_resolver_service
from .constants import (
    API_FIELD_EXPECTED_TOTAL,
    API_FIELD_EXPECTED_TOTAL_COMPAT,
)
from .services.remote_total_store import (
    read_remote_total_fresh,
    write_remote_total,
)
from .services.metrics_service import compute_subscription_metrics, compute_overview_metrics

# Logger
logger = logging.getLogger(__name__)

# 本地工具：BVID 校验与安全 URL 构造（避免非法ID拼接URL）
def _is_bvid(vid: str) -> bool:
    try:
        return bool(vid) and bool(re.match(r'^BV[0-9A-Za-z]{10}$', str(vid)))
    except Exception:
        return False

def _safe_bilibili_url(vid: Optional[str]) -> Optional[str]:
    if not vid:
        return None
    return f"https://www.bilibili.com/video/{vid}" if _is_bvid(vid) else None



# 移除重复的Pydantic模型定义，使用统一的schemas模块

class ResolveUploaderBody(BaseModel):
    name: Optional[str] = None
    uploader_id: Optional[str] = None
    bili_jct: Optional[str] = None
    dedeuserid: Optional[str] = None

# SettingUpdate已移至schemas.common模块

# 创建FastAPI应用
app = FastAPI(title="Bilibili Curator API", version="7.0.0")

# 启用 CORS（跨域）
try:
    _origins_raw = os.getenv('CORS_ALLOW_ORIGINS', '*')
    _origins = [o.strip() for o in _origins_raw.split(',') if o.strip()] if _origins_raw else ['*']
    _allow_all = (len(_origins) == 1 and _origins[0] == '*')
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=(not _allow_all),  # 当为通配符时，浏览器不允许携带凭据
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
        max_age=600,
    )
except Exception as _e:
    logger = logging.getLogger(__name__)
    logger.warning(f"CORS middleware setup failed: {_e}")

# 设置全局异常处理器
setup_exception_handlers(app)

# 注册API路由
app.include_router(cache_router, prefix="/api")
app.include_router(subscription_router, prefix="/api")
app.include_router(cookie_router, prefix="/api")
app.include_router(migration_router, prefix="/api")
app.include_router(strm_router, prefix="/api")

# 路径常量：指向包根目录 bili_curator/，以及前端打包目录 web/dist
BASE_DIR = Path(__file__).resolve().parents[1]
SPA_DIST = BASE_DIR / "web" / "dist"

# 静态文件挂载：功能模块页面（管理工具，非主SPA）
app.mount("/static", StaticFiles(directory="static"), name="static")
# SPA资源挂载：主应用打包资源
try:
    spa_assets_dir = SPA_DIST / "assets"
    if spa_assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(spa_assets_dir)), name="assets")
except Exception:
    pass

# 主应用入口：统一SPA架构
# 架构：主SPA (/) + 功能模块页面 (/static/*) 混合设计
@app.get("/", response_class=HTMLResponse)
async def read_root():
    """
    主应用路由 - 返回统一SPA应用
    
    架构说明：
    - 主SPA：web/dist/index.html (216KB打包文件，统一导航)
    - 功能页面：static/*.html (独立管理工具)
    - 优先级：SPA > 静态文件 > 回退页面
    """
    # 1) 优先返回打包后的 SPA 首页
    try:
        spa_index = SPA_DIST / "index.html"
        if spa_index.exists():
            with open(spa_index, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    except Exception:
        pass
    # 2) 其次尝试 static/index.html（若用户将前端拷贝至 static）
    try:
        static_index = Path("static") / "index.html"
        if static_index.exists():
            with open(static_index, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    except Exception:
        pass
    # 3) 最终回退：返回一个简单的入口页面
    return HTMLResponse(content=(
        """
        <!doctype html>
        <html lang=\"zh-CN\">
          <head>
            <meta charset=\"utf-8\" />
            <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
            <title>Bili Curator</title>
            <style>
              body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.6;padding:24px;color:#1f2937;background:#f9fafb}
              .card{max-width:720px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;border-radius:12px;box-shadow:0 1px 2px rgba(0,0,0,0.04)}
              .card h1{margin:0;padding:20px 24px;border-bottom:1px solid #f1f5f9;font-size:20px}
              .card .content{padding:20px 24px}
              a.btn{display:inline-block;margin-right:12px;margin-top:8px;padding:10px 14px;border-radius:8px;text-decoration:none;color:#fff;background:#3b82f6}
              a.btn.secondary{background:#10b981}
              code{background:#f1f5f9;padding:2px 6px;border-radius:6px}
            </style>
          </head>
          <body>
            <div class=\"card\">
              <h1>Bili Curator 已启动</h1>
              <div class=\"content\">
                <p>欢迎使用。主要 API 前缀为 <code>/api</code>。</p>
                <p>
                  <a class=\"btn\" href=\"/api/status\">系统状态</a>
                  <a class=\"btn secondary\" href=\"/legacy/admin\">管理页(旧版)</a>
                </p>
              </div>
            </div>
          </body>
        </html>
        """
    ))

# 使用中间件实现 SPA 回退：仅当 404 且非 /api、/static、/assets 下时，返回前端 index.html
@app.middleware("http")
async def spa_fallback_middleware(request, call_next):
    response = await call_next(request)
    try:
        if response.status_code == 404 and request.method.upper() == "GET":
            path = request.url.path or "/"
            if not (path.startswith("/api") or path.startswith("/static") or path.startswith("/assets")):
                spa_index = SPA_DIST / "index.html"
                if spa_index.exists():
                    with open(spa_index, "r", encoding="utf-8") as f:
                        return HTMLResponse(content=f.read())
    except Exception:
        # 任何异常都回退到原始响应
        pass
    return response

# ------------------------------
# 应用启动与关闭事件
# ------------------------------
@app.on_event("startup")
async def _on_startup():
    """服务启动自动执行：
    - 启动调度器
    - 执行一次本地优先的一致性修复（在后台线程避免阻塞事件循环）
    - 全量重算订阅统计（本地优先口径）
    """
    try:
        scheduler.start()
    except Exception as e:
        logger.warning(f"启动调度器失败：{e}")

    # 一致性修复：放到线程，避免阻塞
    try:
        await asyncio.to_thread(startup_consistency_check)
    except Exception as e:
        logger.warning(f"启动一致性修复异常：{e}")

    # 启动后统一重算订阅统计
    db = next(get_db())
    try:
        recompute_all_subscriptions(db, touch_last_check=False)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"启动后重算订阅统计失败：{e}")
    finally:
        db.close()

    # 启动后后台批量校验 Cookie（不阻塞启动）
    async def _validate_cookies_bg():
        try:
            await asyncio.to_thread(cookie_manager.batch_validate_cookies)
        except Exception as e:
            logger.warning(f"启动后 Cookie 批量校验异常：{e}")
    try:
        asyncio.create_task(_validate_cookies_bg())
    except Exception as e:
        logger.debug(f"schedule cookie batch validate failed: {e}")


# ------------------------------
# 自动导入/自动关联 API
# ------------------------------
class AutoImportBody(BaseModel):
    recompute: Optional[bool] = False  # 是否在完成后触发一次统计重算

@app.post("/api/auto-import/scan-associate")
async def api_auto_import_scan_associate(body: AutoImportBody = None):
    """触发一次后台的本地扫描导入 + 自动关联订阅。
    - 立即返回 {triggered: true}
    - 工作在后台线程中串行执行：scan_and_import -> auto_associate_subscriptions
    - 可选：完成后触发一次 recompute_all_subscriptions（当 body.recompute 为 True）
    """
    async def _run_job():
        # 放在线程池，避免阻塞事件循环
        try:
            await asyncio.to_thread(auto_import_service.scan_and_import)
        except Exception as e:
            logger.warning(f"scan_and_import 异常：{e}")
        try:
            await asyncio.to_thread(auto_import_service.auto_associate_subscriptions)
        except Exception as e:
            logger.warning(f"auto_associate_subscriptions 异常：{e}")
        if body and body.recompute:
            ldb = next(get_db())
            try:
                recompute_all_subscriptions(ldb, touch_last_check=False)
                ldb.commit()
            except Exception as e:
                ldb.rollback()
                logger.warning(f"recompute_all_subscriptions 失败：{e}")
            finally:
                ldb.close()

    asyncio.create_task(_run_job())
    return {"triggered": True}

# ------------------------------
# 轻量同步 API（触发 + 状态）
# ------------------------------

class SyncTriggerBody(BaseModel):
    sid: Optional[int] = None
    mode: Optional[str] = "lite_head"  # lite_head | backfill_failures | full_head_small (保留向前兼容)
    force: Optional[bool] = False  # 强制刷新：绕过TTL与缓存，直接执行远端对比

@app.post("/api/sync/trigger")
async def api_sync_trigger(body: SyncTriggerBody, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """触发轻量同步：
    - 指定 sid：后台刷新该订阅的 pending 估算（compute_pending_list）并写入缓存；可作为“刷新按钮”的轻量操作。
    - 未指定 sid：调用一次 enqueue_coordinator（按轮转/节流策略），触发全局轻量同步与入队协调。
    返回立刻，不阻塞前端。
    """

    async def _run_for_sid(sid: int):
        ldb = next(get_db())
        try:
            logger.info(f"开始处理订阅同步请求: sid={sid}, force={body.force}")
            # 读取最近一次 sync 状态，决定是否复用缓存或跳过重复抓取
            try:
                status_key = f"sync:{sid}:status"
                srow = ldb.query(Settings).filter(Settings.key == status_key).first()
                status_data = json.loads(srow.value) if (srow and srow.value) else {}
            except Exception:
                status_data = {}

            status_str = (status_data.get('status') or '').strip()
            remote_total_cached = status_data.get('remote_total')
            # 解析时间戳（updated_at 优先，其次 ts）
            ts_raw = status_data.get('updated_at') or status_data.get('ts')
            ts_dt = None
            try:
                if ts_raw:
                    ts_dt = datetime.fromisoformat(ts_raw)
            except Exception:
                ts_dt = None

            # TTL：REMOTE_TOTAL_TTL_MIN，默认60分钟
            try:
                ttl_min = int(os.getenv('REMOTE_TOTAL_TTL_MIN', '60'))
            except Exception:
                ttl_min = 60
            fresh = False
            if ts_dt is not None:
                try:
                    fresh = (datetime.now() - ts_dt) <= timedelta(minutes=max(1, ttl_min))
                except Exception:
                    fresh = False

            # 检查订阅的下载模式，STRM模式需要特殊处理
            subscription = ldb.query(Subscription).filter(Subscription.id == sid).first()
            if not subscription:
                raise ValueError(f"订阅 {sid} 不存在")
            
            download_mode = getattr(subscription, 'download_mode', 'local')
            logger.info(f"订阅 {sid} ({subscription.name}) 检测到模式: {download_mode}")
            
            # 若已有运行中：
            # - STRM模式：强制执行完整同步流程
            # - LOCAL模式：若缓存新鲜且已有 remote_total，则直接按本地下载数刷新 pending，并将状态置为 idle 后返回
            # - 否则保持早退，避免并发外网抓取
            if status_str == 'running':
                if download_mode == 'strm':
                    # STRM模式：不使用缓存优化，强制执行完整同步
                    logger.info(f"STRM订阅 {subscription.name} 跳过缓存优化，执行完整同步")
                elif fresh and isinstance(remote_total_cached, int):
                    try:
                        downloaded = ldb.query(Video).filter(Video.subscription_id == sid, Video.video_path.isnot(None)).count()
                        if downloaded > int(remote_total_cached):
                            raise RuntimeError("cached remote_total suspicious on running; skip idle override")
                        pend = max(0, int(remote_total_cached) - int(downloaded))
                        # 移除旧的 pending_estimated 缓存写入（已统一到 compute_subscription_metrics）
                        # 将状态置为 idle（UPSERT）
                        status_key = f"sync:{sid}:status"
                        payload = {
                            'status': 'idle',
                            'updated_at': datetime.now().isoformat(),
                            'remote_total': int(remote_total_cached),
                            'existing': int(downloaded),
                            'pending': int(pend),
                        }
                        val = json.dumps(payload, ensure_ascii=False)
                        ldb.execute(text("""
                            INSERT INTO settings (key, value, description)
                            VALUES (:key, :val, '订阅同步状态')
                            ON CONFLICT(key) DO UPDATE SET
                              value = :val,
                              updated_at = CURRENT_TIMESTAMP
                        """), {"key": status_key, "val": val})
                        ldb.commit()
                    except Exception as e:
                        logger.debug(f"running->idle override failed: {e}")
                        ldb.rollback()
                    return
                else:
                    return

            # 若未强制，且缓存新鲜且有 remote_total，则仅用本地下载数重新计算 pending，并写入缓存，避免外网抓取
            # STRM模式跳过此优化，确保完整同步流程
            if (not (body and body.force)) and fresh and isinstance(remote_total_cached, int) and download_mode != 'strm':
                try:
                    downloaded = ldb.query(Video).filter(Video.subscription_id == sid, Video.video_path.isnot(None)).count()
                    # 纠偏：如本地下载数 > 缓存远端数，认为缓存可疑，转为强制刷新
                    if downloaded > int(remote_total_cached):
                        raise RuntimeError("cached remote_total suspicious; force refresh")
                    pend = max(0, int(remote_total_cached) - int(downloaded))
                    # 移除旧的 pending_estimated 缓存写入（已统一到 compute_subscription_metrics）
                    # 提前返回前，确保 sync 状态被设置为 idle（避免历史 running 残留）
                    try:
                        status_key = f"sync:{sid}:status"
                        payload = {
                            'status': 'idle',
                            'updated_at': datetime.now().isoformat(),
                            'remote_total': int(remote_total_cached),
                        }
                        val = json.dumps(payload, ensure_ascii=False)
                        ldb.execute(text("""
                            INSERT INTO settings (key, value, description)
                            VALUES (:key, :val, '订阅同步状态')
                            ON CONFLICT(key) DO UPDATE SET
                              value = :val,
                              updated_at = CURRENT_TIMESTAMP
                        """), {"key": status_key, "val": val})
                        ldb.commit()
                    except Exception as e:
                        logger.debug(f"early-return set idle failed: {e}")
                        ldb.rollback()
                    return
                except Exception:
                    # 失败则退回到完整计算
                    pass

            # 走轻量路径：先把状态标记为 running，让前端立刻显示“获取中”，再后台计算
            try:
                sdata = {
                    'status': 'running',
                    'updated_at': datetime.now().isoformat(),
                }
                srow = ldb.query(Settings).filter(Settings.key == status_key).first()
                if not srow:
                    srow = Settings(key=status_key, value=json.dumps(sdata, ensure_ascii=False))
                    ldb.add(srow)
                else:
                    srow.value = json.dumps(sdata, ensure_ascii=False)
                ldb.commit()
            except Exception:
                ldb.rollback()

            try:
                
                if download_mode == 'strm':
                    # STRM模式：使用增强下载器
                    from .services.enhanced_downloader import EnhancedDownloader
                    from .services.strm_proxy_service import STRMProxyService
                    from .services.strm_file_manager import STRMFileManager
                    from .services.unified_cache_service import UnifiedCacheService
                    from .cookie_manager import cookie_manager
                    
                    # 初始化STRM服务
                    strm_proxy = STRMProxyService(cookie_manager=cookie_manager)
                    strm_file_manager = STRMFileManager()
                    cache_service = UnifiedCacheService()
                    
                    enhanced_downloader = EnhancedDownloader(
                        strm_proxy, strm_file_manager, cache_service
                    )
                    
                    logger.info(f"API触发STRM同步: {subscription.name} (ID: {sid})")
                    result = await enhanced_downloader.compute_pending_list(subscription, ldb)
                    logger.info(f"STRM同步完成: {subscription.name}, 结果: {result}")
                else:
                    # LOCAL模式：使用传统下载器
                    result = await downloader.compute_pending_list(sid, db)
                # 移除旧的 pending_estimated 缓存写入（已统一到 compute_subscription_metrics）
                # 成功：将 sync 状态从 running 更新为 idle（使用 UPSERT，避免被并发覆盖）
                try:
                    status_key = f"sync:{sid}:status"
                    payload = {
                        'status': 'idle',
                        'updated_at': datetime.now().isoformat(),
                        'remote_total': result.get('remote_total'),
                        'existing': result.get('existing'),
                        'pending': result.get('pending'),
                    }
                    val = json.dumps(payload, ensure_ascii=False)
                    db.execute(text("""
                        INSERT INTO settings (key, value, description)
                        VALUES (:key, :val, '订阅同步状态')
                        ON CONFLICT(key) DO UPDATE SET
                          value = :val,
                          updated_at = CURRENT_TIMESTAMP
                    """), {"key": status_key, "val": val})
                    db.commit()
                except Exception as e:
                    logger.debug(f"post-compute set idle failed: {e}")
                    db.rollback()
            except Exception as e:
                logger.error(f"sync trigger (sid={sid}) failed: {e}", exc_info=True)
                # 失败：将 sync 状态更新为 failed，避免长时间停留在 running
                try:
                    status_key = f"sync:{sid}:status"
                    payload = {
                        'status': 'failed',
                        'error': str(e),
                        'updated_at': datetime.now().isoformat(),
                    }
                    val = json.dumps(payload, ensure_ascii=False)
                    ldb.execute(text("""
                        INSERT INTO settings (key, value, description)
                        VALUES (:key, :val, '订阅同步状态')
                        ON CONFLICT(key) DO UPDATE SET
                          value = :val,
                          updated_at = CURRENT_TIMESTAMP
                    """), {"key": status_key, "val": val})
                    ldb.commit()
                except Exception as ee:
                    logger.debug(f"post-compute set failed failed: {ee}")
                    ldb.rollback()
        finally:
            ldb.close()

    async def _run_global():
        try:
            await scheduler.enqueue_coordinator()
        except Exception as e:
            logger.warning(f"sync trigger (global) failed: {e}")

    # 使用 BackgroundTasks 确保任务执行
    if body and body.sid:
        background_tasks.add_task(_run_for_sid, body.sid)
        return {"triggered": True, "scope": "subscription", "sid": body.sid}
    else:
        background_tasks.add_task(_run_global)
        return {"triggered": True, "scope": "global"}

# ------------------------------
# 增量管线管理 API（灰度与本地验证）
# ------------------------------
class IncrementalToggleBody(BaseModel):
    sid: Optional[int] = None  # None=全局
    enabled: bool

@app.post("/api/incremental/toggle")
async def incremental_toggle(body: IncrementalToggleBody, db: Session = Depends(get_db)):
    """开启/关闭增量管线：
    - 全局：sync:global:enable_incremental_pipeline = "1"|"0"
    - 订阅：sync:{sid}:enable_incremental = "1"|"0"（覆盖全局）
    """
    try:
        key = (
            'sync:global:enable_incremental_pipeline'
            if (body.sid is None) else f'sync:{int(body.sid)}:enable_incremental'
        )
        val = '1' if body.enabled else '0'
        row = db.query(Settings).filter(Settings.key == key).first()
        if not row:
            row = Settings(key=key, value=val)
            db.add(row)
        else:
            row.value = val
        db.commit()
        return {"ok": True, "key": key, "value": val}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

class CookieToggleBody(BaseModel):
    id: int
    is_active: bool

@app.post("/api/cookie/toggle")
async def cookie_toggle(body: CookieToggleBody, db: Session = Depends(get_db)):
    """启用/禁用指定 Cookie。"""
    try:
        row = db.query(Cookie).filter(Cookie.id == int(body.id)).first()
        if not row:
            raise HTTPException(status_code=404, detail="cookie not found")
        row.is_active = bool(body.is_active)
        db.commit()
        return {"id": row.id, "name": row.name, "is_active": bool(row.is_active)}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/cookie/validate-all")
async def cookie_validate_all():
    """后台触发批量 Cookie 校验，立即返回。"""
    async def _run():
        try:
            await asyncio.to_thread(cookie_manager.batch_validate_cookies)
        except Exception as e:
            logger.warning(f"validate-all 异常：{e}")
    try:
        asyncio.create_task(_run())
        return {"triggered": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class HeadSnapshotBody(BaseModel):
    sid: int
    head_ids: List[str]
    cap: Optional[int] = 200
    reset_cursor: Optional[bool] = True

@app.post("/api/incremental/head-snapshot")
async def set_head_snapshot(body: HeadSnapshotBody, db: Session = Depends(get_db)):
    """写入订阅的 head_snapshot（用于不出网验证M1增量入队）。支持可选重置 last_cursor。"""
    try:
        # 规范化与裁剪
        arr = [str(x) for x in (body.head_ids or []) if isinstance(x, (str, int))]
        if not arr:
            raise HTTPException(status_code=400, detail="head_ids 为空")
        cap = max(1, int(body.cap or 200))
        remote_sync_service.update_head_snapshot(db, int(body.sid), arr[:cap])
        if body.reset_cursor:
            remote_sync_service.set_last_cursor(db, int(body.sid), None)
        return {"ok": True, "sid": int(body.sid), "size": len(arr[:cap])}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RefreshHeadBody(BaseModel):
    sid: int
    cap: Optional[int] = 200
    reset_cursor: Optional[bool] = True

@app.post("/api/incremental/refresh-head")
async def refresh_head_snapshot(body: RefreshHeadBody, db: Session = Depends(get_db)):
    """触发订阅的远端头部快照刷新（后台异步，不阻塞）。
    - 调用 `remote_sync_service.refresh_head_snapshot()` 抓取前 cap 个 ID 并写入。
    - 刷新过程会更新 `sync:{sid}:status` 为 running/idle/failed。
    """
    if not body or not body.sid:
        raise HTTPException(status_code=400, detail="缺少 sid")

    async def _run():
        ldb = next(get_db())
        try:
            await remote_sync_service.refresh_head_snapshot(ldb, int(body.sid), cap=int(body.cap or 200), reset_cursor=bool(body.reset_cursor))
        except Exception as e:
            logger.warning(f"refresh head failed (sid={body.sid}): {e}")
        finally:
            ldb.close()

    # 使用 asyncio.create_task 启动后台任务
    asyncio.create_task(_run())
    return {"triggered": True, "sid": int(body.sid)}


@app.get("/api/incremental/status/{sid}")
async def get_incremental_status(sid: int, db: Session = Depends(get_db)):
    """查询订阅增量状态：
    返回 { sid, status, updated_at, remote_total_cached, head_size, last_cursor }。
    """
    try:
        status_key = f"sync:{sid}:status"
        head_key = f"sync:{sid}:head_snapshot"
        cursor_key = f"sync:{sid}:last_cursor"
        total_key = f"sync:{sid}:remote_total_cached"

        rows = db.query(Settings).filter(Settings.key.in_([status_key, head_key, cursor_key, total_key])).all()
        smap = {r.key: r.value for r in rows if r and r.key}

        # status
        status = None
        updated_at = None
        try:
            sval = smap.get(status_key)
            if sval:
                data = json.loads(sval)
                status = data.get('status')
                updated_at = data.get('updated_at') or data.get('ts')
        except Exception:
            pass

        # head size
        head_size = None
        try:
            hval = smap.get(head_key)
            if hval:
                arr = json.loads(hval)
                if isinstance(arr, list):
                    head_size = len(arr)
        except Exception:
            pass

        # last cursor
        last_cursor = None
        try:
            cval = smap.get(cursor_key)
            if cval:
                c = json.loads(cval)
                if isinstance(c, dict):
                    last_cursor = c.get('last_seen')
        except Exception:
            pass

        # remote total cached
        remote_total_cached = None
        try:
            tval = smap.get(total_key)
            if tval is not None:
                remote_total_cached = int(str(tval).strip())
        except Exception:
            pass

        return {
            'sid': int(sid),
            'status': status,
            'updated_at': updated_at,
            'remote_total_cached': remote_total_cached,
            'head_size': head_size,
            'last_cursor': last_cursor,
        }
    finally:
        db.close()


@app.get("/api/sync/status")
async def api_sync_status(sid: Optional[int] = None, db: Session = Depends(get_db)):
    """查询同步状态：返回 last_sync 状态、remote_total、pending_estimated、retry_backfill 队列长度等。
    - 若提供 sid，则仅返回该订阅；否则返回所有启用订阅。
    """
    try:
        subs = []
        if sid is not None:
            s = db.query(Subscription).filter(Subscription.id == sid).first()
            if s:
                subs = [s]
        else:
            subs = db.query(Subscription).filter(Subscription.is_active == True).all()
        if not subs:
            return {"items": []}

        ids = [s.id for s in subs]
        keys = []
        for i in ids:
            keys.append(f"sync:{i}:status")
            # 移除旧的 pending_estimated 读取（已统一到 compute_subscription_metrics）
            keys.append(f"retry:{i}:failed_backfill")
        rows = db.query(Settings).filter(Settings.key.in_(keys)).all()
        smap = {r.key: r.value for r in rows if r and r.key}

        items = []
        for s in subs:
            stat = {
                "subscription_id": s.id,
                "name": s.name,
                "remote_total": None,
                "pending_estimated": None,
                "retry_queue_len": 0,
                "fail_total": None,
                "fail_perm": None,
                "status": None,
                "updated_at": None,
                "is_fetching": False,
            }
            # sync status
            try:
                sval = smap.get(f"sync:{s.id}:status")
                if sval:
                    data = json.loads(sval)
                    stat["status"] = data.get("status")
                    stat["remote_total"] = data.get("remote_total")
                    stat["updated_at"] = data.get("updated_at") or data.get("ts")
                    # 远端总数尚未写入且状态为运行中，视为“获取中”
                    if (stat["status"] == 'running') and (stat["remote_total"] is None):
                        stat["is_fetching"] = True
            except Exception:
                pass
            # 移除旧的 pending_estimated 读取（已统一到 compute_subscription_metrics）
            # retry queue length
            try:
                rq = smap.get(f"retry:{s.id}:failed_backfill")
                if rq:
                    arr = json.loads(rq)
                    if isinstance(arr, list):
                        stat["retry_queue_len"] = len(arr)
            except Exception:
                pass
            items.append(stat)

        # 如仅查询单个订阅，补充该订阅的失败统计（避免全量查询过重）
        if sid is not None and len(items) == 1:
            try:
                fails = db.query(Settings).filter(Settings.key.like('fail:%')).all()
                total = 0
                perm = 0
                for r in fails:
                    try:
                        data = json.loads(r.value) if (r and r.value) else {}
                        if not isinstance(data, dict):
                            continue
                        if int(data.get('sid') or -1) == int(sid):
                            total += 1
                            if (data.get('class') == 'permanent'):
                                perm += 1
                    except Exception:
                        continue
                items[0]['fail_total'] = total
                items[0]['fail_perm'] = perm
            except Exception:
                pass

        return {"items": items}
    finally:
        db.close()

# ------------------------------
# 统一统计 Metrics API（纯指标，不含队列状态）
# ------------------------------

@app.get("/api/metrics/subscription/{sid}")
async def api_metrics_subscription(sid: int, ttl_hours: int = 1, db: Session = Depends(get_db)):
    """返回单个订阅的统一统计（带容量回退）。
    ttl_hours 控制远端快照新鲜度判断，默认1小时。
    """
    try:
        return compute_subscription_metrics(db, int(sid), ttl_hours=max(1, int(ttl_hours)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/metrics/overview")
async def api_metrics_overview(ttl_hours: int = 1, db: Session = Depends(get_db)):
    """返回全局聚合统计（带轻量缓存）。
    ttl_hours 控制各订阅远端快照新鲜度判断，默认1小时。
    """
    try:
        return compute_overview_metrics(db, ttl_hours=max(1, int(ttl_hours)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Legacy 管理页面：仅当仍保留文件时可访问
@app.get("/legacy/admin", response_class=HTMLResponse)
async def legacy_admin():
    try:
        with open("static/admin.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="legacy admin 已移除")

@app.get("/admin")
async def read_admin():
    """兼容旧入口，301 重定向到 /legacy/admin，避免与首页混淆"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/legacy/admin", status_code=301)

# 系统状态API
@app.get("/api/status")
async def get_system_status(db: Session = Depends(get_db)):
    """获取系统状态"""
    # 统计数据
    total_subscriptions = db.query(Subscription).count()
    active_subscriptions = db.query(Subscription).filter(Subscription.is_active == True).count()
    total_videos = db.query(Video).count()
    downloaded_videos = db.query(Video).filter(Video.video_path.isnot(None)).count()
    active_cookies = db.query(Cookie).filter(Cookie.is_active == True).count()
    total_cookies = db.query(Cookie).count()
    
    # 调度器任务列表
    try:
        scheduler_jobs = scheduler.get_jobs()
    except Exception:
        scheduler_jobs = []

    # 运行中任务摘要（EnhancedTaskManager）
    try:
        running_map = task_manager.get_all_tasks()
        running_tasks = list(running_map.values()) if isinstance(running_map, dict) else running_map
    except Exception:
        running_tasks = []

    # 最近下载任务（截取最近20条）
    try:
        recent_rows = (
            db.query(DownloadTask)
            .order_by(DownloadTask.updated_at.desc())
            .limit(20)
            .all()
        )
        recent_tasks = [
            {
                "id": r.id,
                "bilibili_id": r.bilibili_id,
                "subscription_id": r.subscription_id,
                "status": r.status,
                "progress": float(r.progress or 0.0),
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "error_message": r.error_message,
            }
            for r in recent_rows
        ]
    except Exception:
        recent_tasks = []

    return {
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "version": "6.0.0",
        "statistics": {
            "total_subscriptions": total_subscriptions,
            "active_subscriptions": active_subscriptions,
            "total_videos": total_videos,
            "downloaded_videos": downloaded_videos,
            "active_cookies": active_cookies
        },
        "cookie_summary": {
            "active": active_cookies,
            "total": total_cookies,
            "current_cookie_id": getattr(cookie_manager, 'current_cookie_id', None),
        },
        "recent_tasks": recent_tasks,
        "scheduler_jobs": scheduler_jobs,
        "running_tasks": running_tasks,
    }

# 队列调试接口
@app.get("/api/queue/stats")
async def queue_stats():
    """返回队列容量/运行/暂停与各通道排队数。"""
    try:
        return request_queue.stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ------------------------------
# Cookie 管理 API
# ------------------------------

@app.get("/api/cookie/status")
async def cookie_status(db: Session = Depends(get_db)):
    """返回 Cookie 列表与当前通道信息。
    - items: 每个 cookie 的基本信息（不含敏感字段）
    - current_cookie_id: 当前被 SimpleCookieManager 选择的 cookie id（可能为 None）
    - counts: 活跃/禁用 数量
    """
    try:
        rows = db.query(Cookie).all()
        items = []
        for r in rows:
            try:
                items.append({
                    'id': r.id,
                    'name': r.name,
                    'is_active': bool(r.is_active),
                    'usage_count': int(r.usage_count or 0),
                    'last_used': r.last_used.isoformat() if r.last_used else None,
                    'failure_count': int(r.failure_count or 0),
                    'last_failure_at': r.last_failure_at.isoformat() if r.last_failure_at else None,
                    'created_at': r.created_at.isoformat() if r.created_at else None,
                    'updated_at': r.updated_at.isoformat() if r.updated_at else None,
                })
            except Exception:
                continue
        active = sum(1 for x in items if x.get('is_active'))
        inactive = len(items) - active
        return {
            'items': items,
            'current_cookie_id': getattr(cookie_manager, 'current_cookie_id', None),
            'counts': {'active': active, 'inactive': inactive},
        }
    finally:
        db.close()


@app.post("/api/cookie/upload")
async def cookie_upload(body: CookieCreate, db: Session = Depends(get_db)):
    """上传/新增一个 Cookie。默认写库为 active=true；可选进行一次在线校验。
    返回 { id, name, is_valid, is_active }
    """
    try:
        # 幂等：若同名存在，更新内容；否则新增
        row = db.query(Cookie).filter(Cookie.name == body.name).first()
        creating = False
        if not row:
            row = Cookie(
                name=body.name.strip(),
                sessdata=body.sessdata.strip(),
                bili_jct=(body.bili_jct or '').strip(),
                dedeuserid=(body.dedeuserid or '').strip(),
                is_active=True,
                usage_count=0,
            )
            db.add(row)
            creating = True
        else:
            row.sessdata = body.sessdata.strip()
            row.bili_jct = (body.bili_jct or '').strip()
            row.dedeuserid = (body.dedeuserid or '').strip()
            row.is_active = True
        db.commit()

        # 在线校验（不抛异常，失败将仅标记失败计数/必要时禁用）
        is_valid = False
        try:
            is_valid = await cookie_manager.validate_cookie(row)
            if is_valid:
                # 清理历史失败状态
                try:
                    cookie_manager.reset_failures(db, row.id)
                except Exception:
                    pass
            else:
                # 记录失败并可能禁用
                try:
                    cookie_manager.record_failure(db, row.id, reason="upload_validate_failed")
                except Exception:
                    pass
        except Exception:
            # 网络或解析异常也按失败计一次
            try:
                cookie_manager.record_failure(db, row.id, reason="upload_validate_exception")
            except Exception:
                pass

        return {
            'id': row.id,
            'name': row.name,
            'is_valid': bool(is_valid),
            'is_active': bool(row.is_active),
            'creating': creating,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# 队列管理与洞察 API（用于可视化与容量调整）
class QueueCapacityBody(BaseModel):
    requires_cookie: Optional[int] = None  # 目标并发上限（cookie 通道）
    no_cookie: Optional[int] = None        # 目标并发上限（no-cookie 通道）
    persist: Optional[bool] = True         # 是否持久化到 Settings，便于重启后生效

@app.get("/api/queue/list")
async def queue_list():
    """列出当前内存队列的任务快照（仅用于调试/后台）。"""
    try:
        return request_queue.list()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/requests/{job_id}/cancel")
async def queue_cancel(job_id: str):
    """取消指定队列任务（运行中将释放对应通道信号量）。"""
    try:
        ok = await request_queue.cancel(job_id, reason="manual_cancel")
        if not ok:
            raise HTTPException(status_code=404, detail="job not found")
        # 返回最新快照
        job = None
        try:
            job = next((j for j in request_queue.list() if j.get('id') == job_id), None)
        except Exception:
            job = None
        return {"ok": True, "job": job or {"id": job_id, "status": "canceled"}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/queue/insights")
async def queue_insights():
    """返回队列洞察（与 /api/queue/stats 相同语义，兼容前端可能的命名）。"""
    try:
        return request_queue.stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/queue/capacity")
async def queue_capacity(db: Session = Depends(get_db)):
    """读取当前目标并发容量（来自内存状态，附带持久化建议值）。"""
    try:
        s = request_queue.stats()
        # 读取已持久化的建议值
        key_cookie = 'queue_cap_cookie'
        key_nocookie = 'queue_cap_nocookie'
        rowc = db.query(Settings).filter(Settings.key == key_cookie).first()
        r_own = db.query(Settings).filter(Settings.key == key_nocookie).first()
        persisted = {
            'requires_cookie': int(rowc.value) if rowc and rowc.value is not None else None,
            'no_cookie': int(r_own.value) if r_own and r_own.value is not None else None,
        }
        return {"capacity": s.get('capacity', {}), "persisted": persisted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/queue/capacity")
async def queue_set_capacity(body: QueueCapacityBody, db: Session = Depends(get_db)):
    """设置并发容量上限：立即对内存生效，可选持久化到 Settings。
    - requires_cookie: 合理范围 1-3（0 视为不变；安全起见禁止>3）
    - no_cookie:      合理范围 1-5（0 视为不变；禁止>5）
    """
    try:
        if body is None:
            raise HTTPException(status_code=400, detail="missing body")

        def _sanitize(v: Optional[int], lo: int, hi: int) -> Optional[int]:
            if v is None:
                return None
            try:
                iv = int(v)
            except Exception:
                raise HTTPException(status_code=400, detail="invalid value")
            if iv <= 0:
                return None
            if iv < lo:
                iv = lo
            if iv > hi:
                iv = hi
            return iv

        cap_cookie = _sanitize(body.requires_cookie, 1, 3)
        cap_nocookie = _sanitize(body.no_cookie, 1, 5)

        # 立即生效
        await request_queue.set_capacity(requires_cookie=cap_cookie, no_cookie=cap_nocookie)

        # 可选持久化
        if body.persist:
            try:
                if cap_cookie is not None:
                    sc = db.query(Settings).filter(Settings.key == 'queue_cap_cookie').first()
                    if not sc:
                        sc = Settings(key='queue_cap_cookie', value=str(cap_cookie), description='队列并发上限(cookie)')
                        db.add(sc)
                    else:
                        sc.value = str(cap_cookie)
                if cap_nocookie is not None:
                    sn = db.query(Settings).filter(Settings.key == 'queue_cap_nocookie').first()
                    if not sn:
                        sn = Settings(key='queue_cap_nocookie', value=str(cap_nocookie), description='队列并发上限(no_cookie)')
                        db.add(sn)
                    else:
                        sn.value = str(cap_nocookie)
                db.commit()
            except Exception:
                db.rollback()

        return {"ok": True, "applied": {"requires_cookie": cap_cookie, "no_cookie": cap_nocookie}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# 失败管理 API
@app.get("/api/failures")
async def list_failures(sid: Optional[int] = None, clazz: Optional[str] = None, limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    """列出失败记录（来源于 Settings: fail:{bvid}）。支持按订阅与分类过滤。
    - 参数：sid（可选）、clazz=temporary|permanent（可选）、limit/offset（简单分页）
    - 返回：items: [{ bvid, class, message, last_at, sid, retry_count }]
    """
    try:
        rows = db.query(Settings).filter(Settings.key.like('fail:%')).all()
        items = []
        for r in rows:
            try:
                data = json.loads(r.value) if (r and r.value) else {}
                if not isinstance(data, dict):
                    continue
                bvid = (r.key or '')[5:]
                if not bvid:
                    continue
                if sid is not None and int(data.get('sid') or -1) != int(sid):
                    continue
                if clazz and (data.get('class') != clazz):
                    continue
                items.append({
                    'bvid': bvid,
                    'class': data.get('class'),
                    'message': data.get('message'),
                    'last_at': data.get('last_at'),
                    'sid': data.get('sid'),
                    'retry_count': data.get('retry_count') or 0,
                })
            except Exception:
                continue
        # 简单分页
        items_sorted = sorted(items, key=lambda x: x.get('last_at') or '', reverse=True)
        return {
            'total': len(items_sorted),
            'items': items_sorted[offset: offset + max(1, min(1000, limit))]
        }
    finally:
        db.close()

@app.get("/api/failures/{bvid}")
async def get_failure_detail(bvid: str, db: Session = Depends(get_db)):
    """获取单条失败详情。"""
    try:
        key = f"fail:{bvid}"
        r = db.query(Settings).filter(Settings.key == key).first()
        if not r:
            raise HTTPException(status_code=404, detail="未找到失败记录")
        try:
            data = json.loads(r.value) if r.value else {}
        except Exception:
            data = {}
        return {'bvid': bvid, **({} if not isinstance(data, dict) else data)}
    finally:
        db.close()

class FailureRetryBody(BaseModel):
    sid: Optional[int] = None
    mode: Optional[str] = 'enqueue'  # enqueue | queue_only

@app.post("/api/failures/{bvid}/unblock")
async def unblock_failure(bvid: str, db: Session = Depends(get_db)):
    """解封：删除失败记录（允许后续入队）。"""
    try:
        key = f"fail:{bvid}"
        r = db.query(Settings).filter(Settings.key == key).first()
        if r:
            db.delete(r)
            db.commit()
        return {'ok': True}
    finally:
        db.close()

@app.post("/api/failures/{bvid}/retry")
async def retry_failure(bvid: str, body: FailureRetryBody, db: Session = Depends(get_db)):
    """重试：清理失败记录，并将视频加入该订阅的入队流程。
    - sid 取顺序：body.sid > fail记录内sid
    - 若无法确定 sid，则返回 400
    - mode=enqueue 直接调用下载入队；queue_only 仅将其放入失败回补队列尾部
    """
    # 读取记录
    key = f"fail:{bvid}"
    rec = db.query(Settings).filter(Settings.key == key).first()
    rec_sid = None
    if rec and rec.value:
        try:
            data = json.loads(rec.value)
            if isinstance(data, dict):
                rec_sid = data.get('sid')
        except Exception:
            pass
    target_sid = body.sid or rec_sid
    if not target_sid:
        raise HTTPException(status_code=400, detail="无法确定订阅ID")
    # 删除失败记录
    try:
        if rec:
            db.delete(rec)
            db.commit()
    except Exception:
        db.rollback()
    # 入队
    try:
        url = _safe_bilibili_url(bvid)
        if not url:
            raise HTTPException(status_code=400, detail="非法BVID")
        if (body.mode or 'enqueue') == 'queue_only':
            # 放入回补队列尾部
            k = f"retry:{int(target_sid)}:failed_backfill"
            s = db.query(Settings).filter(Settings.key == k).first()
            arr = []
            if s and s.value:
                try:
                    arr = json.loads(s.value)
                    if not isinstance(arr, list):
                        arr = []
                except Exception:
                    arr = []
            arr.append(bvid)
            val = json.dumps(arr, ensure_ascii=False)
            if s:
                s.value = val
                s.description = s.description or '失败回补队列'
            else:
                db.add(Settings(key=k, value=val, description='失败回补队列'))
            db.commit()
            return {'queued': True, 'mode': 'queue_only', 'sid': int(target_sid)}
        else:
            # 直接调用下载入队
            await downloader._download_single_video({
                'id': bvid,
                'title': bvid,
                'webpage_url': url,
                'url': url,
            }, int(target_sid), db)
            return {'enqueued': True, 'mode': 'enqueue', 'sid': int(target_sid)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 新增：一键本地同步（全量扫描 + 自动关联 + 统计重算）
_local_sync_lock = asyncio.Lock()

@app.post("/api/auto-import/scan-associate")
async def auto_import_scan_and_associate():
    """一键本地同步：顺序执行扫描导入与自动关联，并做一次全量统计重算。
    - 互斥：与自身并发互斥，避免重复重入。
    - 返回：整合两个阶段返回值与本次同步时间戳。
    """
    if _local_sync_lock.locked():
        # 返回 202 表示已在进行中
        return {"message": "本地同步已在运行中", "running": True}
    async with _local_sync_lock:
        try:
            from .auto_import import auto_import_service
            # 1) 扫描导入（线程池执行）
            scan_res = await asyncio.to_thread(auto_import_service.scan_and_import)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"扫描导入失败: {e}")
        try:
            # 2) 自动关联（线程池执行）
            assoc_res = await asyncio.to_thread(auto_import_service.auto_associate_subscriptions)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"自动关联失败: {e}")
        # 3) 统一重算订阅统计
        db = next(get_db())
        try:
            try:
                recompute_all_subscriptions(db, touch_last_check=False)
                db.commit()
            except Exception as re:
                db.rollback()
                # 不致命，纳入返回信息
                assoc_res = {**(assoc_res or {}), "recompute_error": str(re)}
        finally:
            db.close()

        return {
            "message": "本地同步完成",
            "running": False,
            "scan": scan_res or {},
            "associate": assoc_res or {},
            "completed_at": datetime.now().isoformat(),
        }

@app.post("/api/auto-import/scan-associate/{subscription_id}")
async def auto_import_scan_and_associate_for_subscription(subscription_id: int, db: Session = Depends(get_db)):
    """按订阅执行本地同步（仅该订阅目录扫描导入 + 仅该订阅自动关联 + 仅该订阅统计重算）。
    - 与相同订阅并发互斥（与下载等其他操作无强耦合，仅使用订阅级锁）。
    - 不与全局本地同步共用锁，以减少阻塞，但需注意用户侧避免同时触发全局与局部。
    """
    # 订阅是否存在
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")

    # 订阅级互斥
    lock = get_subscription_lock(subscription_id)
    if lock.locked():
        return {"message": "该订阅的本地同步已在运行中", "running": True}

    async with lock:
        try:
            from .auto_import import auto_import_service
            # 1) 仅扫描该订阅目录并导入
            scan_res = await asyncio.to_thread(auto_import_service.scan_and_import_for_subscription, subscription_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"扫描导入失败: {e}")

        # 2) 仅该订阅自动关联（以本地目录为准：若记录当前关联到其他订阅但路径落在本订阅目录下，也进行重关联修复）
        try:
            matches = auto_import_service._find_matching_videos(sub, db)
            associated_count = 0
            for v in matches:
                # 以本地目录为准进行强制修复：
                # - 原逻辑仅关联 subscription_id 为空的记录，无法修复“错关联到其他订阅”的情况
                # - 这里对所有命中本订阅目录的记录执行统一归属，确保DB与本地目录一致
                if v.subscription_id != subscription_id:
                    v.subscription_id = subscription_id
                    associated_count += 1
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"自动关联失败: {e}")

        # 3) 以本地为准的清理与纠偏：
        #    - 若视频与JSON均不存在：判定为“已删除”，直接删除DB记录（避免 total_videos 偏大）
        #    - 若仅视频文件缺失但JSON存在：标记 downloaded=False，清空 video_path 与文件大小相关字段
        try:
            from pathlib import Path
            from .models import Video
            videos = db.query(Video).filter(Video.subscription_id == subscription_id).all()
            removed_count = 0
            downgraded_count = 0
            for v in videos:
                vp = Path(v.video_path) if getattr(v, 'video_path', None) else None
                jp = Path(v.json_path) if getattr(v, 'json_path', None) else None
                v_exists = (vp and vp.exists())
                j_exists = (jp and jp.exists())
                if (not v_exists) and (not j_exists):
                    # 两者都不存在：删除记录
                    db.delete(v)
                    removed_count += 1
                    continue
                if (not v_exists) and j_exists:
                    # 仅视频缺：降级 downloaded 状态并清理路径/大小
                    v.downloaded = False
                    v.video_path = None
                    try:
                        v.file_size = 0
                    except Exception:
                        pass
                    downgraded_count += 1
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"[sub={subscription_id}] 本地一致性清理失败：{e}")

        # 4) 仅重算该订阅统计
        try:
            from .services.subscription_stats import recompute_subscription_stats
            recompute_subscription_stats(db, subscription_id, touch_last_check=False)
            db.commit()
        except Exception as re:
            db.rollback()
            # 不致命，纳入返回信息
            recompute_error = str(re)
        else:
            recompute_error = None

        return {
            "message": "本地同步完成（按订阅）",
            "running": False,
            "scan": scan_res or {},
            "associate": {"associated": associated_count},
            "subscription_id": subscription_id,
            "recompute_error": recompute_error,
            "completed_at": datetime.now().isoformat(),
        }

# 聚合下载管理 API：按订阅返回本地已下载、估算待下载、队列排队/运行中计数
@app.get("/api/download/aggregate")
async def download_aggregate(db: Session = Depends(get_db)):
    """返回每个启用订阅的聚合下载状态。
    字段：
    - subscription: { id, name, type }
    - downloaded: 本地有文件数（以目录为准）
    - pending_estimated: 估算待下载数（remote_total - 本地有文件数；remote_total 缺失时按0处理）
    - queue: { queued, running }
    - remote_total: 最近一次同步记录中的远端总数（如有）
    """
    try:
        # 1) 取启用订阅
        active_subs = db.query(Subscription).filter(Subscription.is_active == True).all()
        if not active_subs:
            return { 'items': [], 'totals': { 'downloaded': 0, 'pending_estimated': 0, 'queued': 0, 'running': 0 } }

        sub_ids = [s.id for s in active_subs]

        # 2) 队列快照 -> (sid -> queued/running)
        q_items = request_queue.list()
        q_index: Dict[int, Dict[str, int]] = {}
        for j in q_items:
            try:
                if j.get('type') != 'download':
                    continue
                sid = j.get('subscription_id')
                if sid is None:
                    continue
                status = j.get('status')
                if status not in ('queued', 'running'):
                    continue
                bucket = q_index.setdefault(int(sid), {'queued': 0, 'running': 0})
                bucket[status] += 1
            except Exception:
                continue

        # 3) 已下载计数（本地有文件）分组统计
        downloaded_rows = (
            db.query(Video.subscription_id, func.count(Video.id))
              .filter(Video.video_path.isnot(None), Video.subscription_id.in_(sub_ids))
              .group_by(Video.subscription_id)
              .all()
        )
        downloaded_map: Dict[int, int] = {sid: int(cnt) for sid, cnt in downloaded_rows}

        result = []
        total_downloaded = 0
        total_pending = 0
        total_queued = 0
        total_running = 0

        for sub in active_subs:
            # 统一口径：直接从 compute_subscription_metrics 获取
            m = compute_subscription_metrics(db, sub.id)
            # 本地已下载以统一字段 on_disk_total 为准（避免与 Video 表统计口径不一致）
            downloaded = int(m.get('on_disk_total') or 0)
            total_downloaded += downloaded

            # 远端总数优先 expected_total，否则回退 expected_total_cached
            remote_total = m.get('expected_total')
            if remote_total is None:
                remote_total = m.get('expected_total_cached')

            # 待下载直接使用统一字段 pending
            pending_estimated = int(m.get('pending') or 0)

            qbucket = q_index.get(sub.id, {'queued': 0, 'running': 0})
            total_pending += pending_estimated
            total_queued += qbucket['queued']
            total_running += qbucket['running']

            result.append({
                'subscription': {
                    'id': sub.id,
                    'name': sub.name,
                    'type': sub.type,
                },
                'downloaded': downloaded,
                'pending_estimated': pending_estimated,
                'queue': {
                    'queued': qbucket['queued'],
                    'running': qbucket['running'],
                },
                'remote_total': remote_total if isinstance(remote_total, int) else None,
            })

        return {
            'items': result,
            'totals': {
                'downloaded': total_downloaded,
                'pending_estimated': total_pending,
                'queued': total_queued,
                'running': total_running,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/subscriptions/{subscription_id}/enqueue_video")
async def enqueue_single_video(subscription_id: int, request: dict, db: Session = Depends(get_db)):
    """将指定视频加入该订阅的下载队列（立即进入全局请求队列，按并发策略执行）。
    请求体：{ video_id: str, title?: str, webpage_url?: str }
    返回：下载执行结果（开始执行后即按现有流程入队并下载）。
    """
    try:
        sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not sub:
            raise HTTPException(status_code=404, detail="订阅不存在")
        if sub.type != 'collection':
            raise HTTPException(status_code=400, detail="仅支持合集订阅")

        video_id = (request or {}).get('video_id')
        if not video_id:
            raise HTTPException(status_code=400, detail="缺少 video_id")
        title = (request or {}).get('title')
        url = (request or {}).get('webpage_url') or _safe_bilibili_url(video_id)
        if not url:
            raise HTTPException(status_code=400, detail="非法BVID或缺少URL")

        # 复用单视频下载流程：内部会将 download 任务写入全局队列并受并发控制
        video_info = { 'id': video_id, 'title': title, 'webpage_url': url, 'url': url }
        result = await downloader._download_single_video(video_info, subscription_id, db)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/queue/list")
async def queue_list():
    """返回所有任务的快照（包含 wait_ms、last_wait_reason 等诊断字段）。"""
    try:
        return request_queue.list()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 订阅管理API
@app.get("/api/subscriptions")
async def get_subscriptions(db: Session = Depends(get_db)):
    """获取所有订阅（统一口径）：expected_total/on_disk_total/failed_perm/pending 等。
    保留兼容字段：remote_total、expected_total_videos、downloaded_videos、db_total_videos。
    """
    subscriptions = db.query(Subscription).all()
    result = []

    for sub in subscriptions:
        m = compute_subscription_metrics(db, sub.id)
        result.append({
            "id": sub.id,
            "name": sub.name,
            "type": sub.type,
            "url": sub.url,
            "is_active": sub.is_active,
            # 统一字段
            "expected_total": m.get("expected_total"),
            "expected_total_cached": m.get("expected_total_cached"),
            "expected_total_snapshot_at": m.get("expected_total_snapshot_at"),
            "on_disk_total": m.get("on_disk_total"),
            "db_total": m.get("db_total"),
            "failed_perm": m.get("failed_perm"),
            "pending": m.get("pending"),
            "sizes": m.get("sizes"),
            # 兼容字段
            "total_videos": m.get("on_disk_total"),
            "db_total_videos": m.get("db_total"),
            "remote_total": m.get("expected_total"),
            "expected_total_videos": m.get("expected_total"),
            "downloaded_videos": m.get("on_disk_total"),
            "pending_videos": m.get("pending"),
            # 其他元信息
            "last_check": sub.last_check.isoformat() if sub.last_check else None,
            "created_at": sub.created_at.isoformat() if sub.created_at else None,
            "updated_at": sub.updated_at.isoformat() if sub.updated_at else None
        })

    db.commit()
    return result

@app.get("/api/overview")
async def get_overview(db: Session = Depends(get_db)):
    """全局总览（统一口径）：远端/本地/失败/待下载/容量汇总 + 队列统计。"""
    try:
        metrics = compute_overview_metrics(db)

        # 队列统计保持不变
        qstats = request_queue.stats()
        qlist = request_queue.list()
        now = datetime.now()
        recent_failed_24h = sum(
            1 for j in qlist
            if j.get('status') == 'failed' and j.get('finished_at') and isinstance(j.get('finished_at'), datetime)
            and (now - j['finished_at']) <= timedelta(hours=24)
        )

        return {
            # 统一聚合口径
            'remote_total': metrics.get('remote_total'),
            'local_total': metrics.get('local_total'),
            'db_total': metrics.get('db_total'),
            'failed_perm_total': metrics.get('failed_perm_total'),
            'pending_total': metrics.get('pending_total'),
            'downloaded_size_bytes': metrics.get('downloaded_size_bytes'),
            'computed_at': metrics.get('computed_at'),
            # 队列信息
            'queue': qstats,
            'recent_failed_24h': recent_failed_24h,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/queue/insights")
async def queue_insights():
    """队列诊断：等待原因分布、错误TopN、失败样本、容量与暂停状态。"""
    try:
        items = request_queue.list()
        # 等待原因分布
        wait_dist: Dict[str, int] = {}
        for j in items:
            reason = j.get('last_wait_reason') or ''
            if reason:
                wait_dist[reason] = wait_dist.get(reason, 0) + 1
        # 错误 TopN
        error_count: Dict[str, int] = {}
        failed_samples = []
        for j in items:
            if j.get('status') == 'failed':
                err = (j.get('last_error') or '').strip()
                if err:
                    error_count[err] = error_count.get(err, 0) + 1
                # 收集样本
                failed_samples.append({
                    'id': j.get('id'),
                    'type': j.get('type'),
                    'subscription_id': j.get('subscription_id'),
                    'video_id': j.get('video_id'),
                    'finished_at': j.get('finished_at').isoformat() if j.get('finished_at') else None,
                    'wait_ms': j.get('wait_ms'),
                    'wait_cycles': j.get('wait_cycles'),
                    'acquired_scope': j.get('acquired_scope'),
                    'last_error': err,
                })
        errors_top = sorted([
            {'error': k, 'count': v} for k, v in error_count.items()
        ], key=lambda x: x['count'], reverse=True)[:10]

        qstats = request_queue.stats()
        # 只返回必要字段，避免泄漏内部细节
        return {
            'wait_reasons': wait_dist,
            'errors_top': errors_top,
            'failed_samples': failed_samples[-20:],
            'paused': qstats.get('paused', {}),
            'capacity': qstats.get('capacity', {}),
            'counts': qstats.get('counts', {}),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/subscriptions")
async def create_subscription(subscription: dict, db: Session = Depends(get_db)):
    """创建新订阅"""
    try:
        # 解析日期字段
        date_after = None
        date_before = None
        if subscription.get("date_after"):
            try:
                date_after = datetime.strptime(subscription["date_after"], "%Y-%m-%d").date()
            except ValueError:
                pass
        if subscription.get("date_before"):
            try:
                date_before = datetime.strptime(subscription["date_before"], "%Y-%m-%d").date()
            except ValueError:
                pass

        # 规范化名称：当为合集订阅且未提供名称时，自动识别名称（优先使用合集层title + uploader）
        name_to_use = (subscription.get("name") or "").strip()
        sub_type = (subscription.get("type") or "").strip()
        url_for_parse = (subscription.get("url") or "").strip()

        if (not name_to_use) and sub_type == "collection" and url_for_parse:
            try:
                # 借用 cookies 以提高解析成功率
                cookie = cookie_manager.get_available_cookie(db)
                import tempfile, os
                cookies_path = None
                if cookie:
                    fd, cookies_path = tempfile.mkstemp(prefix='cookies_', suffix='.txt')
                    os.close(fd)
                    with open(cookies_path, 'w', encoding='utf-8') as cf:
                        cf.write("# Netscape HTTP Cookie File\n")
                        cf.write("# This file was generated by bili_curator V6\n\n")
                        cf.writelines([
                            f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{cookie.sessdata}\n",
                            f".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\t{cookie.bili_jct}\n",
                            f".bilibili.com\tTRUE\t/\tFALSE\t0\tDedeUserID\t{cookie.dedeuserid}\n",
                        ])

                # 方案A：优先获取合集层信息（更接近网站显示）：title + uploader
                import json as json_lib
                pl_cmd = [
                    'yt-dlp',
                    '--flat-playlist',
                    '--dump-single-json',
                    '--no-download',
                ]
                if cookies_path:
                    pl_cmd += ['--cookies', cookies_path]
                pl_cmd.append(url_for_parse)

                pl_proc = await asyncio.create_subprocess_exec(
                    *pl_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                pl_stdout, pl_stderr = await pl_proc.communicate()
                if pl_proc.returncode == 0:
                    try:
                        data = json_lib.loads(pl_stdout.decode('utf-8', errors='ignore') or '{}')
                    except Exception:
                        data = {}
                    uploader = ''
                    playlist_title = ''
                    if isinstance(data, dict):
                        # B站合集层常见字段
                        playlist_title = (data.get('title') or data.get('playlist_title') or '').strip()
                        uploader = (data.get('uploader') or data.get('channel') or '').strip()
                    # 去重：如果标题已包含uploader，避免重复
                    if uploader and playlist_title:
                        if playlist_title.startswith(uploader) or uploader in playlist_title:
                            name_to_use = playlist_title
                        else:
                            name_to_use = f"{uploader}：{playlist_title}"
                    elif playlist_title:
                        name_to_use = playlist_title
                    elif uploader:
                        name_to_use = f"{uploader}的合集"
                    # 更新cookie使用统计
                    if cookie:
                        try:
                            cookie_manager.update_cookie_usage(db, cookie.id)
                        except Exception:
                            pass

                # 方案B：如未获得有效名称，回退首条视频的 playlist_title
                if not name_to_use:
                    fe_cmd = [
                        'yt-dlp',
                        '--dump-json',
                        '--playlist-items', '1',
                        '--no-download',
                    ]
                    if cookies_path:
                        fe_cmd += ['--cookies', cookies_path]
                    fe_cmd.append(url_for_parse)
                    fe_proc = await asyncio.create_subprocess_exec(
                        *fe_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    fe_stdout, fe_stderr = await fe_proc.communicate()
                    if fe_proc.returncode == 0:
                        for line in fe_stdout.decode('utf-8', errors='ignore').strip().split('\n'):
                            if not line.strip():
                                continue
                            try:
                                info = json_lib.loads(line)
                                uploader = (info.get('uploader') or '').strip()
                                playlist_title = (info.get('playlist_title') or '').strip()
                                title = (info.get('title') or '').strip()
                                # 兼容个别情况下 playlist_title 为空时回退 title
                                base_title = playlist_title or title
                                if uploader and base_title:
                                    if base_title.startswith(uploader) or uploader in base_title:
                                        name_to_use = base_title
                                    else:
                                        name_to_use = f"{uploader}：{base_title}"
                                elif base_title:
                                    name_to_use = base_title
                                elif uploader:
                                    name_to_use = f"{uploader}的合集"
                                if name_to_use:
                                    break
                            except Exception:
                                continue

                # 清理临时 cookie 文件
                if cookies_path and os.path.exists(cookies_path):
                    try:
                        os.remove(cookies_path)
                    except Exception:
                        pass

            except Exception:
                # 解析失败则继续走兜底逻辑
                pass

            # 兜底：取 URL 最后一个路径段作为名称
            if not name_to_use and url_for_parse:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(url_for_parse)
                    last_seg = (parsed.path.rstrip('/') or '/').split('/')[-1]
                    name_to_use = last_seg or parsed.netloc or "未知合集"
                except Exception:
                    name_to_use = "未知合集"

        # 若仍为空，使用传入名称（兼容非合集类型）；确保非空以满足 NOT NULL 约束
        if not name_to_use:
            name_to_use = (subscription.get("name") or "").strip()

        # 对 UP主订阅执行名称↔ID 自动解析/回填（尽力而为，不抛错）
        uploader_id_to_use = (subscription.get("uploader_id") or "").strip() or None
        if sub_type == "uploader":
            try:
                resolved_name, resolved_mid = await uploader_resolver_service.resolve(name_to_use or None, uploader_id_to_use, db)
                if resolved_name:
                    name_to_use = resolved_name
                if (not uploader_id_to_use) and resolved_mid:
                    uploader_id_to_use = resolved_mid
            except Exception:
                pass

        # 启用 gating：当为 UP 主订阅且名称未解析成功时，强制禁用订阅
        # 约定：未解析成功的占位名称使用「待解析UP主」
        want_active = bool(subscription.get("is_active", True))
        unresolved_uploader = False
        if (sub_type == "uploader"):
            # 认为“有效名称”的条件：非空且不为占位
            has_valid_name = bool(name_to_use)
            if not has_valid_name:
                name_to_use = "待解析UP主"
                unresolved_uploader = True
            # 若未提供 uploader_id，也视为尚未完全解析（但本规则仅基于名称禁用）
        # 计算最终 is_active
        final_active = want_active
        if (sub_type == "uploader") and (unresolved_uploader):
            final_active = False

        # 创建约束：UP主订阅必须至少解析出 name 或 uploader_id 之一，否则拒绝创建
        if (sub_type == "uploader"):
            if (not uploader_id_to_use) and (not name_to_use or name_to_use == "待解析UP主"):
                raise HTTPException(status_code=400, detail="创建失败：需要有效的UP主ID或名称，且需可解析")
        
        db_subscription = Subscription(
            name=name_to_use,
            type=subscription["type"],
            url=subscription.get("url"),
            uploader_id=uploader_id_to_use,
            keyword=subscription.get("keyword"),
            specific_urls=subscription.get("specific_urls"),
            date_after=date_after,
            date_before=date_before,
            min_likes=subscription.get("min_likes"),
            min_favorites=subscription.get("min_favorites"),
            min_views=subscription.get("min_views"),
            is_active=final_active,
        )
        db.add(db_subscription)
        db.commit()
        db.refresh(db_subscription)
        
        # 自动关联已下载的视频
        try:
            from app.auto_import import auto_import_service
            matching_videos = auto_import_service._find_matching_videos(db_subscription, db)
            associated_count = 0
            
            for video in matching_videos:
                if not video.subscription_id:  # 只关联未关联的视频
                    video.subscription_id = db_subscription.id
                    associated_count += 1
            
            # 更新订阅统计
            db_subscription.downloaded_videos = len([v for v in matching_videos if v.downloaded])
            db_subscription.total_videos = len(matching_videos)
            pending_videos = max(0, (db_subscription.total_videos or 0) - (db_subscription.downloaded_videos or 0))
            db_subscription.updated_at = datetime.now()
            
            db.commit()
            
            return {
                "message": "订阅创建成功",
                "id": db_subscription.id,
                "associated_videos": associated_count,
                "total_videos": db_subscription.total_videos or 0,
                "downloaded_videos": db_subscription.downloaded_videos or 0,
                "pending_videos": pending_videos,
                "is_active": bool(db_subscription.is_active),
            }
        except Exception as e:
            # 如果关联失败，不影响订阅创建
            logger.warning(f"订阅创建成功，但自动关联失败: {e}")
            # 返回基础统计为0，前端可在列表拉取时刷新
            return {
                "message": "订阅创建成功",
                "id": db_subscription.id,
                "associated_videos": 0,
                "total_videos": 0,
                "downloaded_videos": 0,
                "pending_videos": 0,
                "is_active": bool(db_subscription.is_active),
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/subscriptions/{subscription_id}/expected-total")
async def get_subscription_expected_total(subscription_id: int, force: bool = False, db: Session = Depends(get_db)):
    """获取远端合集应有总计视频数（带1小时缓存），用于校准显示。
    仅对 type=collection 且存在 url 的订阅有效。
    """
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    if sub.type != "collection" or not sub.url:
        raise HTTPException(status_code=400, detail="该订阅不是合集或缺少URL")

    # 检查1小时内的缓存（非强制刷新时）
    if not force:
        cached_total = read_remote_total_fresh(db, subscription_id, max_age_hours=1)
        if isinstance(cached_total, int):
            return {
                API_FIELD_EXPECTED_TOTAL: int(cached_total),
                API_FIELD_EXPECTED_TOTAL_COMPAT: int(cached_total),
                "cached": True,
            }

    try:
        import tempfile, os, json as json_lib
        sub_lock = get_subscription_lock(sub.id)

        async def run_expected_total(cookies_path: Optional[str], requires_cookie: bool) -> Optional[int]:
            # 公共参数：UA/Referer/重试/轻睡眠
            common_args = [
                'yt-dlp',
                '--user-agent', get_user_agent(requires_cookie),
                '--referer', 'https://www.bilibili.com/',
                '--sleep-interval', '2',
                '--max-sleep-interval', '5',
                '--retries', '5',
                '--fragment-retries', '5',
                '--retry-sleep', '3',
                '--ignore-errors',
                '--no-warnings',
                '--no-download',
            ]
            if cookies_path:
                common_args += ['--cookies', cookies_path]

            async def run_and_parse(args):
                timeout_sec = int(os.getenv('EXPECTED_TOTAL_TIMEOUT', '30'))
                async with sub_lock:
                    async with yt_dlp_semaphore:
                        proc = await asyncio.create_subprocess_exec(
                            *args,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        try:
                            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
                            return proc.returncode, out, err
                        except asyncio.TimeoutError:
                            logger.warning(f"expected_total 命令超时 (>{timeout_sec}s)，正在终止: {' '.join(args)}")
                            try:
                                proc.terminate()
                                await asyncio.wait_for(proc.wait(), timeout=5.0)
                            except asyncio.TimeoutError:
                                proc.kill()
                            raise

            expected_total = None
            last_err = None
            # 只使用快速元数据路径（不做分页枚举）

            # A：dump-single-json
            try:
                cmd_a = common_args + ['--flat-playlist', '--dump-single-json', sub.url]
                rc, out, err = await run_and_parse(cmd_a)
                if rc == 0:
                    try:
                        data = json_lib.loads(out.decode('utf-8', errors='ignore') or '{}')
                        if isinstance(data, dict):
                            expected_total = data.get('n_entries')
                            if expected_total is None:
                                entries = data.get('entries')
                                if isinstance(entries, list):
                                    expected_total = len(entries)
                    except Exception as e:
                        last_err = e
                else:
                    last_err = err.decode('utf-8', errors='ignore')
            except Exception as e:
                last_err = e

            # B：-J
            if expected_total is None:
                try:
                    cmd_b = common_args + ['-J', sub.url]
                    rc, out, err = await run_and_parse(cmd_b)
                    if rc == 0:
                        try:
                            data = json_lib.loads(out.decode('utf-8', errors='ignore') or '{}')
                            if isinstance(data, dict):
                                expected_total = data.get('n_entries')
                                if expected_total is None:
                                    entries = data.get('entries')
                                    if isinstance(entries, list):
                                        expected_total = len(entries)
                                if expected_total is None:
                                    pc = data.get('playlist_count')
                                    if isinstance(pc, int):
                                        expected_total = pc
                        except Exception as e:
                            last_err = e
                    else:
                        last_err = err.decode('utf-8', errors='ignore')
                except Exception as e:
                    last_err = e

            # D：--dump-json --flat-playlist
            if expected_total is None:
                try:
                    cmd_d = common_args + ['--dump-json', '--flat-playlist', sub.url]
                    rc, out, err = await run_and_parse(cmd_d)
                    if rc == 0:
                        count = 0
                        for line in (out.decode('utf-8', errors='ignore') or '').strip().split('\n'):
                            if not line.strip():
                                continue
                            try:
                                _ = json_lib.loads(line)
                                count += 1
                            except json_lib.JSONDecodeError:
                                continue
                        if count > 0:
                            expected_total = count
                    else:
                        last_err = err.decode('utf-8', errors='ignore')
                except Exception as e:
                    last_err = e

            # C：首条 --dump-json
            if expected_total is None:
                try:
                    cmd_c = common_args + ['--dump-json', '--playlist-items', '1', sub.url]
                    rc, out, err = await run_and_parse(cmd_c)
                    if rc == 0:
                        first_line = (out.decode('utf-8', errors='ignore') or '').strip().split('\n')[0]
                        if first_line:
                            try:
                                info = json_lib.loads(first_line)
                                for key in ('n_entries', 'playlist_count', 'playlist_entries', 'total_count'):
                                    val = info.get(key)
                                    if isinstance(val, int):
                                        expected_total = val
                                        break
                                if expected_total is None:
                                    ents = info.get('entries')
                                    if isinstance(ents, list):
                                        expected_total = len(ents)
                            except Exception as e:
                                last_err = e
                    else:
                        last_err = err.decode('utf-8', errors='ignore')
                except Exception as e:
                    last_err = e

            return expected_total

        # 第一阶段：无 Cookie 通道
        job_nc = await request_queue.enqueue(job_type="expected_total", subscription_id=sub.id, requires_cookie=False)
        await request_queue.mark_running(job_nc)
        try:
            expected = await run_expected_total(None, requires_cookie=False)
            if expected is not None:
                await request_queue.mark_done(job_nc)
                total_val = int(expected)
                # 写入缓存，更新快照时间戳
                write_remote_total(db, sub.id, total_val, sub.url)
                return {API_FIELD_EXPECTED_TOTAL: total_val, API_FIELD_EXPECTED_TOTAL_COMPAT: total_val, "job_id": job_nc, "cached": False}
            else:
                await request_queue.mark_failed(job_nc, "need_cookie_fallback")
        except Exception as e:
            await request_queue.mark_failed(job_nc, str(e))

        # 第二阶段：Cookie 通道（高优先级）
        cookie = cookie_manager.get_available_cookie(db)
        if not cookie:
            raise HTTPException(status_code=502, detail="需要Cookie但没有可用Cookie")

        # 写 cookie 文件
        fd, cookies_path = tempfile.mkstemp(prefix='cookies_', suffix='.txt')
        os.close(fd)
        with open(cookies_path, 'w', encoding='utf-8') as cf:
            cf.write("# Netscape HTTP Cookie File\n")
            cf.write("# This file was generated by bili_curator V6\n\n")
            if getattr(cookie, 'sessdata', None):
                cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{cookie.sessdata}\n")
            if getattr(cookie, 'bili_jct', None) and str(cookie.bili_jct).strip():
                cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\t{cookie.bili_jct}\n")
            if getattr(cookie, 'dedeuserid', None) and str(cookie.dedeuserid).strip():
                cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tDedeUserID\t{cookie.dedeuserid}\n")

        job_c = await request_queue.enqueue(job_type="expected_total", subscription_id=sub.id, requires_cookie=True, priority=0)
        await request_queue.mark_running(job_c)
        try:
            expected2 = await run_expected_total(cookies_path, requires_cookie=True)
            if expected2 is None:
                await request_queue.mark_failed(job_c, "cookie_fallback_failed")
                raise HTTPException(status_code=502, detail="无法解析合集总数（Cookie 回退失败）")
            # 写入缓存
            write_remote_total(db, sub.id, int(expected2), sub.url)
            await request_queue.mark_done(job_c)
            total_val2 = int(expected2)
            return {
                API_FIELD_EXPECTED_TOTAL: total_val2,
                API_FIELD_EXPECTED_TOTAL_COMPAT: total_val2,
                "job_id": job_c,
                "cached": False
            }
        finally:
            try:
                if cookies_path and os.path.exists(cookies_path):
                    os.remove(cookies_path)
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"获取远端总数失败: {e}")
        raise HTTPException(status_code=502, detail="获取合集总数失败")

@app.get("/api/subscriptions/{subscription_id}")
async def get_subscription(subscription_id: int, db: Session = Depends(get_db)):
    """获取单个订阅详情（统一口径）。"""
    subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="订阅不存在")

    m = compute_subscription_metrics(db, subscription.id)

    return {
        "id": subscription.id,
        "name": subscription.name,
        "type": subscription.type,
        "url": subscription.url,
        "uploader_id": subscription.uploader_id,
        "keyword": subscription.keyword,
        "specific_urls": subscription.specific_urls,
        "date_after": subscription.date_after.isoformat() if subscription.date_after else None,
        "date_before": subscription.date_before.isoformat() if subscription.date_before else None,
        "min_likes": subscription.min_likes,
        "min_favorites": subscription.min_favorites,
        "min_views": subscription.min_views,
        # 统一字段
        "expected_total": m.get("expected_total"),
        "expected_total_cached": m.get("expected_total_cached"),
        "expected_total_snapshot_at": m.get("expected_total_snapshot_at"),
        "on_disk_total": m.get("on_disk_total"),
        "db_total": m.get("db_total"),
        "failed_perm": m.get("failed_perm"),
        "pending": m.get("pending"),
        "sizes": m.get("sizes"),
        # 兼容字段
        "total_videos": m.get("on_disk_total"),
        "db_total_videos": m.get("db_total"),
        "remote_total": m.get("expected_total"),
        "expected_total_videos": m.get("expected_total"),
        "downloaded_videos": m.get("on_disk_total"),
        "pending_videos": m.get("pending"),
        # 其他
        "is_active": subscription.is_active,
        "last_check": subscription.last_check.isoformat() if subscription.last_check else None,
        "created_at": subscription.created_at.isoformat() if subscription.created_at else None,
        "updated_at": subscription.updated_at.isoformat() if subscription.updated_at else None
    }

@app.post("/api/uploader/resolve")
async def uploader_resolve(body: ResolveUploaderBody, db: Session = Depends(get_db)):
    """手动解析UP主：根据提供的 name 或 uploader_id（mid）尽力解析回填另一项。
    不修改数据库，仅返回解析结果。
    """
    try:
        resolved_name, resolved_mid = await uploader_resolver_service.resolve(
            (body.name or None),
            (body.uploader_id or None),
            db
        )
        return {
            "input": {"name": body.name, "uploader_id": body.uploader_id},
            "resolved": {"name": resolved_name, "uploader_id": resolved_mid}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析失败: {str(e)}")

@app.put("/api/subscriptions/{subscription_id}")
async def update_subscription(
    subscription_id: int,
    subscription: SubscriptionUpdate,
    db: Session = Depends(get_db)
):
    """更新订阅"""
    db_subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not db_subscription:
        raise HTTPException(status_code=404, detail="订阅不存在")
    
    # 更新字段（兼容 active -> is_active）
    data = subscription.dict(exclude_unset=True)
    # 提取并规范 is_active
    is_active_value = data.pop("is_active", None)
    if is_active_value is None and "active" in data:
        is_active_value = data.pop("active")

    # 设置其余字段
    for field, value in data.items():
        setattr(db_subscription, field, value)

    # 若为 UP主订阅，尽力做一次名称↔ID 的自动补全（仅回填缺失项，不覆盖显式提供值）
    try:
        if db_subscription.type == 'uploader':
            proposed_name = (data.get('name') if 'name' in data else db_subscription.name) or None
            proposed_mid = (data.get('uploader_id') if 'uploader_id' in data else db_subscription.uploader_id) or None
            resolved_name, resolved_mid = await uploader_resolver_service.resolve(proposed_name, proposed_mid, db)
            # 回填缺失项
            if (not proposed_name) and resolved_name:
                db_subscription.name = resolved_name
            if (not proposed_mid) and resolved_mid:
                db_subscription.uploader_id = resolved_mid
    except Exception:
        pass

    # 单独处理 is_active（启用 gating：UP主订阅名称未解析成功前不允许启用）
    if is_active_value is not None:
        if bool(is_active_value) and (db_subscription.type == 'uploader'):
            nm = (db_subscription.name or '').strip()
            if not nm or nm == '待解析UP主':
                # 拒绝启用，并返回明确错误
                raise HTTPException(status_code=400, detail="UP主名称未解析成功，暂不能启用订阅")
        db_subscription.is_active = bool(is_active_value)
    
    db_subscription.updated_at = datetime.now()
    db.commit()
    
    return {"message": "订阅更新成功"}

@app.post("/api/subscriptions/{subscription_id}/resolve")
async def resolve_and_backfill_subscription(subscription_id: int, db: Session = Depends(get_db)):
    """手动为指定 UP 主订阅执行一次名称↔ID 解析，并回填缺失字段（若有变更则落库）。
    仅对 type='uploader' 生效。
    """
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    if sub.type != 'uploader':
        raise HTTPException(status_code=400, detail="仅支持UP主订阅")

    before = {"name": sub.name, "uploader_id": sub.uploader_id}
    try:
        resolved_name, resolved_mid = await uploader_resolver_service.resolve(sub.name, sub.uploader_id, db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析失败: {str(e)}")

    changed = False
    # 将占位名视为未解析，允许被覆盖
    if ((not (sub.name or '').strip()) or (sub.name or '').strip() == '待解析UP主') and resolved_name:
        sub.name = resolved_name
        changed = True
    if (not (sub.uploader_id or '').strip()) and resolved_mid:
        sub.uploader_id = resolved_mid
        changed = True
    if changed:
        sub.updated_at = datetime.now()
        db.commit()
    after = {"name": sub.name, "uploader_id": sub.uploader_id}
    return {"changed": changed, "before": before, "after": after}

@app.delete("/api/subscriptions/{subscription_id}")
async def delete_subscription(subscription_id: int, db: Session = Depends(get_db)):
    """删除订阅"""
    db_subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not db_subscription:
        raise HTTPException(status_code=404, detail="订阅不存在")
    
    db.delete(db_subscription)
    db.commit()
    
    return {"message": "订阅删除成功"}

@app.get("/api/subscriptions/{subscription_id}/pending")
async def get_subscription_pending(subscription_id: int, force_refresh: bool = False, db: Session = Depends(get_db)):
    """获取指定订阅的待下载视频列表（智能缓存+本地维护）。
    仅对 type=collection 且存在 url 的订阅有效。
    """
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    if sub.type != "collection" or not sub.url:
        raise HTTPException(status_code=400, detail="该订阅不是合集或缺少URL")

    try:
        data = await pending_list_service.get_pending_videos(subscription_id, db, force_refresh)
        return data
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"获取待下载列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取待下载列表失败")

@app.post("/api/subscriptions/parse-collection")
async def parse_collection_info(request: dict, db: Session = Depends(get_db)):
    """解析合集URL，自动识别合集名称"""
    url = request.get('url')
    if not url:
        raise HTTPException(status_code=400, detail="URL不能为空")
    
    try:
        import json as json_lib
        import tempfile, os

        async def run_parse(cookies_path: Optional[str], requires_cookie: bool) -> Optional[str]:
            # A. 合集层
            pl_cmd = [
                'yt-dlp',
                '--flat-playlist',
                '--dump-single-json',
                '--no-download',
                '--user-agent', get_user_agent(requires_cookie),
                url
            ]
            if cookies_path:
                pl_cmd += ['--cookies', cookies_path]
            async with yt_dlp_semaphore:
                pl_proc = await asyncio.create_subprocess_exec(
                    *pl_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                pl_stdout, pl_stderr = await pl_proc.communicate()
            if pl_proc.returncode == 0:
                try:
                    data = json_lib.loads(pl_stdout.decode('utf-8', errors='ignore') or '{}')
                except Exception:
                    data = {}
                uploader = ''
                playlist_title = ''
                if isinstance(data, dict):
                    playlist_title = (data.get('title') or data.get('playlist_title') or '').strip()
                    uploader = (data.get('uploader') or data.get('channel') or '').strip()
                name = None
                if uploader and playlist_title:
                    if playlist_title.startswith(uploader) or uploader in playlist_title:
                        name = playlist_title
                    else:
                        name = f"{uploader}：{playlist_title}"
                elif playlist_title:
                    name = playlist_title
                elif uploader:
                    name = f"{uploader}的合集"
                if name:
                    return name

            # B. 首条视频
            fe_cmd = [
                'yt-dlp',
                '--dump-json',
                '--playlist-items', '1',
                '--no-download',
                '--user-agent', get_user_agent(requires_cookie),
                url
            ]
            if cookies_path:
                fe_cmd += ['--cookies', cookies_path]
            async with yt_dlp_semaphore:
                fe_proc = await asyncio.create_subprocess_exec(
                    *fe_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                fe_stdout, fe_stderr = await fe_proc.communicate()
            if fe_proc.returncode == 0:
                for line in fe_stdout.decode('utf-8', errors='ignore').strip().split('\n'):
                    if not line.strip():
                        continue
                    try:
                        info = json_lib.loads(line)
                        uploader = (info.get('uploader') or '').strip()
                        playlist_title = (info.get('playlist_title') or '').strip()
                        title = (info.get('title') or '').strip()
                        base_title = playlist_title or title
                        name = None
                        if uploader and base_title:
                            if base_title.startswith(uploader) or uploader in base_title:
                                name = base_title
                            else:
                                name = f"{uploader}：{base_title}"
                        elif base_title:
                            name = base_title
                        elif uploader:
                            name = f"{uploader}的合集"
                        if name:
                            return name
                    except Exception:
                        continue
            return None

        # 第一阶段：无 Cookie 解析
        job_nc = await request_queue.enqueue(job_type="parse", subscription_id=None, requires_cookie=False)
        await request_queue.mark_running(job_nc)
        name_nc = await run_parse(None, requires_cookie=False)
        if name_nc:
            await request_queue.mark_done(job_nc)
            return {"name": name_nc}
        else:
            await request_queue.mark_failed(job_nc, "need_cookie_fallback")

        # 第二阶段：Cookie 通道（高优先级）
        cookie = cookie_manager.get_available_cookie(db)
        if not cookie:
            return {"error": "需要Cookie但没有可用Cookie"}
        fd, cookies_path = tempfile.mkstemp(prefix='cookies_', suffix='.txt')
        os.close(fd)
        with open(cookies_path, 'w', encoding='utf-8') as cf:
            cf.write("# Netscape HTTP Cookie File\n")
            cf.write("# This file was generated by bili_curator V6\n\n")
            cf.writelines([
                f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{cookie.sessdata}\n",
                f".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\t{cookie.bili_jct}\n",
                f".bilibili.com\tTRUE\t/\tFALSE\t0\tDedeUserID\t{cookie.dedeuserid}\n",
            ])
        job_c = await request_queue.enqueue(job_type="parse", subscription_id=None, requires_cookie=True, priority=0)
        await request_queue.mark_running(job_c)
        try:
            name_c = await run_parse(cookies_path, requires_cookie=True)
            if name_c:
                try:
                    cookie_manager.update_cookie_usage(db, cookie.id)
                except Exception:
                    pass
                await request_queue.mark_done(job_c)
                return {"name": name_c}
            else:
                await request_queue.mark_failed(job_c, "parse_failed")
                return {"error": "解析失败"}
        finally:
            try:
                if cookies_path and os.path.exists(cookies_path):
                    os.remove(cookies_path)
            except Exception:
                pass
    except Exception as e:
        # 内部已对 job_nc/job_c 做状态标记，这里仅返回错误
        return {"error": f"解析合集信息失败: {str(e)}"}

@app.post("/api/subscriptions/{subscription_id}/sync")
async def sync_subscription(subscription_id: int, db: Session = Depends(get_db)):
    """手动触发指定订阅的同步任务"""
    subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="订阅不存在")
    
    try:
        # 触发远端快照刷新
        await remote_sync_service.refresh_subscription_snapshot(subscription_id, db)
        
        # 清理失败视频
        cleaned_count = pending_list_service.check_and_clean_failed_videos(db, subscription_id)
        
        # 强制刷新待下载列表缓存
        await pending_list_service.get_pending_videos(subscription_id, db, force_refresh=True)
        
        # 触发数据一致性修复
        consistency_result = await data_consistency_service.check_and_fix_remote_totals(db)
        
        return {
            "message": "订阅同步已触发",
            "cleaned_failed_videos": cleaned_count,
            "consistency_check": consistency_result
        }
    except Exception as e:
        logger.error(f"手动同步订阅失败: {e}")
        raise HTTPException(status_code=500, detail="同步失败")

@app.post("/api/subscriptions/{subscription_id}/clear-failed")
async def clear_failed_videos(subscription_id: int, db: Session = Depends(get_db)):
    """清理订阅的失败视频记录"""
    try:
        # 获取失败视频数量
        failed_count = db.query(Video).filter(
            Video.subscription_id == subscription_id,
            Video.download_failed == True
        ).count()
        
        if failed_count == 0:
            return {"cleared_count": 0, "message": "没有失败视频需要清理"}
        
        # 删除失败视频记录
        db.query(Video).filter(
            Video.subscription_id == subscription_id,
            Video.download_failed == True
        ).delete()
        
        db.commit()
        
        return {
            "cleared_count": failed_count,
            "message": f"成功清理了 {failed_count} 个失败视频记录"
        }
        
    except Exception as e:
        logger.error(f"清理失败视频记录失败: {e}")
        raise HTTPException(status_code=500, detail="清理失败")

@app.post("/api/subscriptions/{subscription_id}/download")
async def start_download(subscription_id: int, db: Session = Depends(get_db)):
    """手动启动下载任务"""
    from .task_manager import enhanced_task_manager
    
    subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="订阅不存在")
    
    try:
        # 使用增强任务管理器启动下载
        task_id = await enhanced_task_manager.start_subscription_download(subscription_id)
        return {"message": "下载任务已启动", "task_id": task_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动下载任务失败: {str(e)}")

@app.post("/api/tasks/{task_id}/pause")
async def pause_task(task_id: str):
    """暂停下载任务"""
    from .task_manager import enhanced_task_manager
    
    success = await enhanced_task_manager.pause_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="任务不存在或无法暂停")
    
    return {"message": "任务已暂停"}

@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    """恢复下载任务"""
    from .task_manager import enhanced_task_manager
    
    success = await enhanced_task_manager.resume_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="任务不存在或无法恢复")
    
    return {"message": "任务已恢复"}

@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消下载任务"""
    from .task_manager import enhanced_task_manager
    
    success = await enhanced_task_manager.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="任务不存在或无法取消")
    
    return {"message": "任务已取消"}

@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态"""
    from .task_manager import enhanced_task_manager
    
    task_status = enhanced_task_manager.get_task_status(task_id)
    if not task_status:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return task_status

@app.get("/api/tasks")
async def get_all_tasks():
    """获取所有任务状态"""
    from .task_manager import enhanced_task_manager
    
    return enhanced_task_manager.get_all_tasks()

# —— 全局请求队列只读接口 ——
@app.get("/api/requests")
async def list_requests():
    items = request_queue.list()
    return {"count": len(items), "items": items}

@app.get("/api/requests/{job_id}")
async def get_request(job_id: str):
    item = request_queue.get(job_id)
    if not item:
        raise HTTPException(status_code=404, detail="请求不存在")
    return item

# —— 全局请求队列管理与可观测接口 ——
@app.get("/api/queue/stats")
def get_queue_stats():
    try:
        return request_queue.stats()
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/queue/pause")
async def pause_queue(scope: str = "all"):
    try:
        await request_queue.pause(scope)
        return {"ok": True, "scope": scope, "stats": request_queue.stats()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/queue/resume")
async def resume_queue(scope: str = "all"):
    try:
        await request_queue.resume(scope)
        return {"ok": True, "scope": scope, "stats": request_queue.stats()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/requests/{job_id}/cancel")
async def cancel_request(job_id: str, reason: str = ""):
    ok = await request_queue.cancel(job_id, reason)
    if not ok:
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True, "job": request_queue.get(job_id)}

@app.post("/api/requests/{job_id}/prioritize")
async def prioritize_request(job_id: str, priority: int = 0):
    ok = await request_queue.prioritize(job_id, priority)
    if not ok:
        raise HTTPException(status_code=404, detail="not_found")
    return {"ok": True, "job": request_queue.get(job_id)}

@app.post("/api/queue/capacity")
async def set_queue_capacity(requires_cookie: Optional[int] = None, no_cookie: Optional[int] = None):
    try:
        await request_queue.set_capacity(requires_cookie=requires_cookie, no_cookie=no_cookie)
        return {"ok": True, "stats": request_queue.stats()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/subscriptions/{subscription_id}/tasks")
async def get_subscription_tasks(subscription_id: int):
    """获取指定订阅的所有任务"""
    from .task_manager import enhanced_task_manager
    
    return enhanced_task_manager.get_subscription_tasks(subscription_id)

# 一致性检查API
@app.post("/api/system/consistency-check")
async def trigger_consistency_check(db: Session = Depends(get_db)):
    """手动触发一致性检查（同步执行，直接返回统计结果）。
    前端会等待本接口返回统计数据，因此不再使用后台任务方式。
    """
    try:
        stats = consistency_checker.check_and_sync(db)
        logger.info(f"手动触发的一致性检查完成: {stats}")
        # 附加一个时间戳，便于前端显示
        stats_with_time = dict(stats)
        stats_with_time["last_check_time"] = datetime.now().isoformat()
        return stats_with_time
    except Exception as e:
        logger.error(f"手动一致性检查失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/system/consistency-stats")
async def get_consistency_stats(db: Session = Depends(get_db)):
    """获取一致性统计信息"""
    try:
        stats = consistency_checker.quick_stats(db)
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 视频管理API
@app.get("/api/videos")
async def get_videos(
    page: int = 1,
    size: int = 20,
    subscription_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """获取视频列表"""
    query = db.query(Video)
    
    if subscription_id:
        query = query.filter(Video.subscription_id == subscription_id)
    
    total = query.count()
    videos = query.order_by(Video.created_at.desc()).offset((page - 1) * size).limit(size).all()
    
    return {
        "total": total,
        "page": page,
        "size": size,
        "videos": [
            {
                "id": video.id,
                "bilibili_id": video.bilibili_id,
                "title": video.title,
                "uploader": video.uploader,
                "duration": video.duration,
                "upload_date": video.upload_date.isoformat() if video.upload_date else None,
                "video_path": video.video_path,
                "file_path": video.video_path,
                "file_size": video.file_size,
                "downloaded": video.downloaded,
                "subscription_id": video.subscription_id,
                "created_at": video.created_at.isoformat() if video.created_at else None,
                "updated_at": video.updated_at.isoformat() if video.updated_at else None
            }
            for video in videos
        ]
    }

# 媒体统计与订阅维度统计
@app.get("/api/media/overview")
async def get_media_overview(scan: bool = False, db: Session = Depends(get_db)):
    """媒体目录总览统计
    - 默认基于数据库快速汇总（更轻量）
    - 当 scan=true 时，额外扫描 DOWNLOAD_PATH 计算实际占用（可能较慢）
    """
    total_videos = db.query(func.count(Video.id)).scalar() or 0
    downloaded_videos = db.query(func.count(Video.id)).filter(Video.video_path.isnot(None)).scalar() or 0
    total_size_db = db.query(func.coalesce(func.sum(Video.file_size), 0)).scalar() or 0

    overview: Dict[str, Any] = {
        "total_videos": total_videos,
        "downloaded_videos": downloaded_videos,
        "total_size": int(total_size_db),
        "scan": False,
    }

    if scan:
        download_root = Path(os.getenv('DOWNLOAD_PATH', '/app/downloads'))
        size_on_disk = 0
        files_on_disk = 0
        if download_root.exists():
            for p in download_root.rglob("*"):
                try:
                    if p.is_file():
                        files_on_disk += 1
                        size_on_disk += p.stat().st_size
                except Exception:
                    # 忽略个别文件读取异常
                    continue
        overview.update({
            "files_on_disk": files_on_disk,
            "size_on_disk": int(size_on_disk),
            "scan": True,
            "download_path": str(download_root),
        })

    return overview


@app.get("/api/media/subscription-stats")
async def get_subscription_stats(db: Session = Depends(get_db)):
    """按订阅聚合统计（数量、已下载、容量、最近上传、失败数）- 使用远端总数口径"""
    # 本地统计：已下载数量、最近上传、失败数量（容量需要单独计算）
    counts = (
        db.query(
            Video.subscription_id.label('sid'),
            func.count(Video.id).label('local_total'),
            func.sum(case((Video.video_path.isnot(None), 1), else_=0)).label('downloaded'),
            func.sum(case((Video.download_failed == True, 1), else_=0)).label('failed'),
            func.max(Video.upload_date).label('latest_upload')
        )
        .group_by(Video.subscription_id)
        .all()
    )

    # 订阅信息
    subs = {s.id: s for s in db.query(Subscription).all()}
    
    # 构建本地统计字典（包含失败数量）
    stats_dict = {}
    for c in counts:
        sid = c.sid
        if sid is None:
            continue
        stats_dict[sid] = {
            'local_total': int(c.local_total or 0),
            'downloaded': int(c.downloaded or 0),
            'failed': int(c.failed or 0),
            'latest_upload': c.latest_upload.isoformat() if c.latest_upload else None,
        }
    
    # 单独计算每个订阅的容量（与目录统计逻辑一致）
    for sid in subs.keys():
        videos = db.query(Video).filter(Video.subscription_id == sid, Video.video_path.isnot(None)).all()
        total_size = 0
        for v in videos:
            # 检查文件是否存在
            if not v.video_path:
                continue
            try:
                from pathlib import Path
                vp = Path(v.video_path).resolve()
                if not vp.exists():
                    continue
            except Exception:
                continue
            
            # 计算容量（与目录统计一致的三级回退）
            size_sum = (v.total_size if getattr(v, 'total_size', None) is not None else None)
            if size_sum is None:
                size_sum = int(v.file_size or 0) + int((getattr(v, 'audio_size', 0) or 0))
            # 如果数据库字段都为空，直接读取磁盘文件大小
            if size_sum == 0 and v.video_path:
                try:
                    actual_size = vp.stat().st_size
                    size_sum = actual_size
                except Exception:
                    size_sum = 0
            total_size += int(size_sum or 0)
        
        if sid in stats_dict:
            stats_dict[sid]['size'] = total_size
        else:
            stats_dict[sid] = {
                'local_total': 0,
                'downloaded': 0,
                'failed': 0,
                'size': total_size,
                'latest_upload': None,
            }

    from .models import Settings
    import json as json_lib
    from datetime import datetime, timedelta

    result = []
    for sid, sub in subs.items():
        local = stats_dict.get(sid, {
            'local_total': 0,
            'downloaded': 0,
            'failed': 0,
            'size': 0,
            'latest_upload': None,
        })
        
        # 读取远端总数 - 优先使用实时查询结果
        remote_total = None
        try:
            # 先尝试从expected-total缓存获取最新数据
            cache_key = f"expected_total:{sid}"
            cache_setting = db.query(Settings).filter(Settings.key == cache_key).first()
            if cache_setting:
                cache_data = json_lib.loads(cache_setting.value)
                cache_time = datetime.fromisoformat(cache_data.get('timestamp', ''))
                # 如果缓存在1小时内，使用缓存数据
                if datetime.now() - cache_time < timedelta(hours=1):
                    remote_total = cache_data.get('total', 0)
            
            # 如果没有有效缓存，尝试从sync状态获取
            if remote_total is None:
                key = f"sync:{sid}:status"
                s = db.query(Settings).filter(Settings.key == key).first()
                if s and s.value:
                    data = json_lib.loads(s.value)
                    rt = data.get("remote_total")
                    if isinstance(rt, int) and rt >= 0:
                        remote_total = rt
        except Exception:
            remote_total = None
        
        # 订阅统计口径改为“本地有文件的数量”，与目录统计一致
        on_disk_total = local['downloaded']
        
        result.append({
            "subscription_id": sid,
            "subscription_name": sub.name,
            "type": sub.type,
            "total_videos": on_disk_total,  # 本地有文件的数量
            "local_total": local['local_total'],  # 数据库记录总数
            "remote_total": remote_total,
            "downloaded_videos": local['downloaded'],
            # 待下载口径：远端期望 - 本地有文件数
            "pending_videos": max(0, (remote_total or on_disk_total) - on_disk_total),
            "total_size": local['size'],
            "latest_upload": local['latest_upload'],
            "failed": local.get('failed', 0),
        })

    # 排序：按总大小/有文件数量倒序
    result.sort(key=lambda x: (x["total_size"], x["total_videos"]), reverse=True)
    return result


@app.get("/api/media/subscriptions/{subscription_id}/videos")
async def get_subscription_videos_detail(
    subscription_id: int,
    page: int = 1,
    size: int = 20,
    include_disk: bool = True,
    db: Session = Depends(get_db)
):
    """订阅下视频明细（分页）
    - include_disk: 是否标记 on_disk（基于 video_path 存在性）
    """
    query = db.query(Video).filter(Video.subscription_id == subscription_id)
    total = query.count()
    items = (
        query.order_by(Video.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    def to_item(v: Video) -> Dict[str, Any]:
        on_disk = None
        if include_disk:
            try:
                on_disk = bool(v.video_path and os.path.exists(v.video_path))
            except Exception:
                on_disk = False
        return {
            "id": v.id,
            "bilibili_id": v.bilibili_id,
            "title": v.title,
            "uploader": v.uploader,
            "duration": v.duration,
            "upload_date": v.upload_date.isoformat() if v.upload_date else None,
            "video_path": v.video_path,
            "file_size": v.file_size,
            "downloaded": v.downloaded,
            "subscription_id": v.subscription_id,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "updated_at": v.updated_at.isoformat() if v.updated_at else None,
            "on_disk": on_disk,
        }

    return {
        "total": total,
        "page": page,
        "size": size,
        "videos": [to_item(v) for v in items],
    }


# —— 按下载目录聚合统计与目录视频分页 ——
@app.get("/api/media/directories")
async def get_directory_stats(db: Session = Depends(get_db)):
    """按下载根目录下的一级目录聚合统计（本地优先、仅统计真实存在文件）
    - 优先使用环境变量 DOWNLOAD_PATH；若与实际不符，则根据 DB 中 video_path 自动探测公共根目录
    - 分组口径：根目录下的一级目录名；根下直存文件计入 "_root"；不在根内的计入 "_others"
    - 统计字段：total_videos（存在文件数）、downloaded_videos（同 total_videos）、total_size
    """

    def _detect_download_root(paths: List[str]) -> Path:
        # 优先环境变量
        env_root = Path(os.getenv('DOWNLOAD_PATH', '/app/downloads')).resolve()
        try:
            if env_root.exists():
                return env_root
        except Exception:
            pass
        # 自动探测：取存在的文件路径，求公共路径
        existing = []
        for p in paths:
            try:
                if p and os.path.exists(p):
                    existing.append(str(Path(p).resolve()))
            except Exception:
                continue
        if not existing:
            # 回退到 env_root 即便不存在
            return env_root
        try:
            common = os.path.commonpath(existing)
            return Path(common)
        except Exception:
            return env_root

    # 仅考虑有路径的记录
    videos = db.query(Video).filter(Video.video_path.isnot(None)).all()
    all_paths = [v.video_path for v in videos if v.video_path]
    download_root = _detect_download_root(all_paths)

    # 与 downloader._create_subscription_directory 口径一致的目录名推导
    def _sanitize_filename(filename: str) -> str:
        import re
        illegal_chars = r'[<>:"/\\|?*]'
        filename = re.sub(illegal_chars, '_', filename or '')
        filename = filename.strip(' .')
        if len(filename) > 100:
            filename = filename[:100]
        return filename or 'untitled'

    def _expected_dir_for_sub(s: Subscription) -> str:
        if s.type == 'collection':
            base = s.name or ''
            d = _sanitize_filename(base)
            if not d:
                d = _sanitize_filename(f"合集订阅_{s.id}")
            return d
        elif s.type == 'keyword':
            base = s.keyword or s.name or '关键词订阅'
            return _sanitize_filename(f"关键词：{base}")
        elif s.type == 'uploader':
            base = s.name or getattr(s, 'uploader_id', None) or 'UP主订阅'
            return _sanitize_filename(f"up 主：{base}")
        else:
            d = _sanitize_filename(s.name or '')
            if not d:
                d = _sanitize_filename(f"订阅_{s.id}")
            return d

    subs = db.query(Subscription).all()
    subs_by_dir: Dict[str, Subscription] = { _expected_dir_for_sub(s): s for s in subs }

    stats: Dict[str, Dict[str, Any]] = {}
    for v in videos:
        v_path = (v.video_path or '').strip()
        if not v_path:
            continue
        # 仅统计磁盘上真实存在的文件
        try:
            vp = Path(v_path).resolve()
            if not vp.exists():
                continue
        except Exception:
            continue
        # 分组键：严格按下载根下的一级目录（恢复与历史一致的口径）
        try:
            rel = vp.relative_to(download_root)
            first = rel.parts[0] if len(rel.parts) > 0 else ""
        except Exception:
            first = "_others"
        if not first:
            first = "_root"
        s = stats.setdefault(first, {
            "dir": first,
            "subscription_id": (subs_by_dir.get(first).id if subs_by_dir.get(first) else None),
            "display_name": None,  # 若能解析到订阅，则用当前订阅名作为展示名
            "_sub_counts": {},     # 内部：统计该目录下各订阅出现次数
            "total_videos": 0,
            "downloaded_videos": 0,
            "total_size": 0,
        })
        s["total_videos"] += 1
        s["downloaded_videos"] += 1  # 仅统计存在文件，等同已下载
        try:
            sidv = int(v.subscription_id) if v.subscription_id is not None else None
        except Exception:
            sidv = None
        if sidv is not None:
            s["_sub_counts"][sidv] = int(s["_sub_counts"].get(sidv, 0)) + 1
        # 优先使用 total_size；否则回退到 file_size + audio_size；最后回退到实际文件大小
        size_sum = (v.total_size if getattr(v, 'total_size', None) is not None else None)
        if size_sum is None:
            size_sum = int(v.file_size or 0) + int((getattr(v, 'audio_size', 0) or 0))
        # 如果数据库字段都为空，直接读取磁盘文件大小
        if size_sum == 0 and v.video_path:
            try:
                actual_size = vp.stat().st_size
                size_sum = actual_size
            except Exception:
                size_sum = 0
        s["total_size"] += int(size_sum or 0)

    # 事后确定展示订阅：若目录名无法映射订阅，则取该目录内最多的订阅ID
    subs_by_id: Dict[int, Subscription] = {s.id: s for s in subs}
    for d, s in stats.items():
        if not s.get("subscription_id"):
            sc = s.get("_sub_counts") or {}
            if sc:
                # 选择出现最多的订阅ID
                sid_major = max(sc.items(), key=lambda kv: kv[1])[0]
                s["subscription_id"] = sid_major
        # 设置展示名
        sid_cur = s.get("subscription_id")
        if sid_cur is not None and sid_cur in subs_by_id:
            s["display_name"] = subs_by_id[sid_cur].name or s.get("dir")
        # 清理内部字段
        if "_sub_counts" in s:
            del s["_sub_counts"]

    # 转列表，按大小/数量倒序
    result = list(stats.values())
    result.sort(key=lambda x: (x["total_size"], x["downloaded_videos"], x["total_videos"]), reverse=True)
    return {
        "download_path": str(download_root),
        "items": result,
        "total_dirs": len(result),
    }


# —— 回填与校准：刷新视频/音频大小，写回 DB ——
@app.post("/api/media/refresh-sizes")
async def refresh_media_sizes(background: BackgroundTasks):
    """后台回填磁盘大小（视频+音频），写回到 videos.file_size / audio_size / total_size。
    - 非阻塞：启动后台任务后立即返回。
    - 扫描策略：仅处理有 video_path 的记录；音频尝试匹配同名 .m4a。
    """

    def _find_audio_path(video_path: str) -> Optional[str]:
        try:
            p = Path(video_path)
            stem = p.with_suffix("").name
            candidate = p.with_name(stem + ".m4a")
            if candidate.exists():
                return str(candidate)
        except Exception:
            return None
        return None

    def _worker():
        from .models import db as _db, Video as _Video
        session = _db.get_session()
        updated = 0
        try:
            q = session.query(_Video).filter(_Video.video_path.isnot(None))
            for v in q.yield_per(200):
                v_path = (v.video_path or '').strip()
                if not v_path:
                    continue
                video_size = None
                audio_size = None
                try:
                    if os.path.exists(v_path):
                        video_size = os.path.getsize(v_path)
                except Exception:
                    video_size = None
                # 仅当容器缺少音轨时，才统计旁路 .m4a 的体积，避免双重统计
                def _has_audio_track(pth: str) -> bool:
                    try:
                        import subprocess
                        proc = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'csv=p=0', pth],
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        return any(line.strip() for line in (proc.stdout or '').splitlines())
                    except Exception:
                        # 探测失败时，保守认为“有音轨”，以避免把残留 m4a 计入导致容量虚高
                        return True

                try:
                    # 只有当视频存在而且“无音轨”时，尝试统计同名 m4a
                    if v_path and os.path.exists(v_path) and (not _has_audio_track(v_path)):
                        a_path = _find_audio_path(v_path)
                        if a_path and os.path.exists(a_path):
                            audio_size = os.path.getsize(a_path)
                        else:
                            audio_size = 0
                    else:
                        # 有音轨或视频不存在：不计旁路音频
                        audio_size = 0 if video_size is not None else None
                except Exception:
                    audio_size = 0 if video_size is not None else None

                # 若两者皆为空，跳过
                if video_size is None and audio_size is None:
                    continue

                new_file_size = int(video_size) if video_size is not None else (v.file_size or 0)
                new_audio_size = int(audio_size) if audio_size is not None else (getattr(v, 'audio_size', 0) or 0)
                new_total = int(new_file_size) + int(new_audio_size)

                changed = False
                if v.file_size != new_file_size:
                    v.file_size = new_file_size
                    changed = True
                # 兼容旧库无字段的情况：getattr/setattr
                if getattr(v, 'audio_size', None) != new_audio_size:
                    try:
                        setattr(v, 'audio_size', new_audio_size)
                        changed = True
                    except Exception:
                        pass
                if getattr(v, 'total_size', None) != new_total:
                    try:
                        setattr(v, 'total_size', new_total)
                        changed = True
                    except Exception:
                        pass
                if changed:
                    updated += 1
                    # 分批提交，降低事务体积
                    if updated % 200 == 0:
                        try:
                            session.commit()
                        except Exception:
                            session.rollback()
            try:
                session.commit()
            except Exception:
                session.rollback()
        finally:
            session.close()

    # 后台执行
    background.add_task(_worker)
    return {"message": "刷新任务已启动", "status": "started"}


@app.get("/api/media/directory-videos")
async def get_directory_videos(dir: str = None, sid: int = None, page: int = 1, size: int = 20, db: Session = Depends(get_db)):
    """获取某一级目录下的全部视频（含子目录），分页返回（基于统一根目录探测）
    - 参数 dir: 由 get_directory_stats 返回的一级目录名
    - 仅返回磁盘真实存在的文件对应的记录
    """
    # 如果提供了订阅ID，则用其订阅名作为一级目录名
    if sid is not None:
        sub = db.query(Subscription).filter(Subscription.id == sid).first()
        if not sub or not sub.name:
            raise HTTPException(status_code=400, detail="无效的订阅ID或订阅名为空")
        dir = sub.name

    if not dir:
        raise HTTPException(status_code=400, detail="dir 不能为空")

    # 重用根目录探测逻辑，确保与目录聚合一致
    videos_all = db.query(Video).filter(Video.video_path.isnot(None)).all()
    paths_all = [v.video_path for v in videos_all if v.video_path]
    def _detect_download_root(paths: List[str]) -> Path:
        env_root = Path(os.getenv('DOWNLOAD_PATH', '/app/downloads')).resolve()
        try:
            if env_root.exists():
                return env_root
        except Exception:
            pass
        existing = []
        for p in paths:
            try:
                if p and os.path.exists(p):
                    existing.append(str(Path(p).resolve()))
            except Exception:
                continue
        if not existing:
            return env_root
        try:
            common = os.path.commonpath(existing)
            return Path(common)
        except Exception:
            return env_root

    download_root = _detect_download_root(paths_all)
    base = (download_root / dir).resolve()
    try:
        base.relative_to(download_root)
    except Exception:
        raise HTTPException(status_code=400, detail="非法目录")

    # SQLite 前缀匹配
    prefix = str(base) + os.sep
    query = db.query(Video).filter(Video.video_path.isnot(None), Video.video_path.like(f"{prefix}%"))
    total = query.count()
    items = (
        query.order_by(Video.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    def to_item(v: Video) -> Dict[str, Any]:
        return {
            "id": v.id,
            "bilibili_id": v.bilibili_id,
            "title": v.title,
            "uploader": v.uploader,
            "duration": v.duration,
            "upload_date": v.upload_date.isoformat() if v.upload_date else None,
            "video_path": v.video_path,
            "file_size": v.file_size,
            "downloaded": v.downloaded,
            "subscription_id": v.subscription_id,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "updated_at": v.updated_at.isoformat() if v.updated_at else None,
        }

    return {
        "dir": dir,
        "sid": sid,
        "download_path": str(download_root),
        "total": total,
        "page": page,
        "size": size,
        "videos": [to_item(v) for v in items],
    }

@app.delete("/api/videos/{video_id}")
async def delete_video(video_id: int, db: Session = Depends(get_db)):
    """删除视频记录"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    
    db.delete(video)
    db.commit()
    
    return {"message": "视频记录删除成功"}

# 自动导入与关联API
@app.post("/api/auto-import/scan")
async def auto_import_scan():
    """扫描下载目录并将未入库的视频导入数据库"""
    try:
        from .auto_import import auto_import_service
        result = auto_import_service.scan_and_import()
        return {"message": "扫描完成", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auto-import/associate")
async def auto_import_associate():
    """将已入库视频按订阅规则自动关联，刷新统计"""
    try:
        from .auto_import import auto_import_service
        result = auto_import_service.auto_associate_subscriptions()
        return {"message": "关联完成", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/subscriptions/{subscription_id}/associate")
async def associate_single_subscription(subscription_id: int, db: Session = Depends(get_db)):
    """仅对指定订阅执行自动关联并返回该订阅的最新统计"""
    try:
        sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not sub:
            raise HTTPException(status_code=404, detail="订阅不存在")
        from .auto_import import auto_import_service
        matches = auto_import_service._find_matching_videos(sub, db)
        for v in matches:
            if not v.subscription_id:
                v.subscription_id = sub.id
        # 刷新统计
        total_videos = db.query(Video).filter(Video.subscription_id == sub.id).count()
        downloaded_videos = db.query(Video).filter(
            Video.subscription_id == sub.id,
            Video.video_path.isnot(None)
        ).count()
        sub.total_videos = total_videos
        sub.downloaded_videos = downloaded_videos
        sub.updated_at = datetime.now()
        db.commit()
        pending_videos = max(0, total_videos - downloaded_videos)
        return {
            "message": "关联完成",
            "id": sub.id,
            "total_videos": total_videos,
            "downloaded_videos": downloaded_videos,
            "pending_videos": pending_videos,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 任务管理API
@app.get("/api/tasks")
async def get_tasks(db: Session = Depends(get_db)):
    """获取下载任务列表"""
    tasks = db.query(DownloadTask).order_by(DownloadTask.created_at.desc()).limit(50).all()
    
    return [
        {
            "id": task.id,
            "bilibili_id": task.bilibili_id,
            "subscription_id": task.subscription_id,
            "status": task.status,
            "progress": task.progress,
            "error_message": task.error_message,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "created_at": task.created_at.isoformat() if task.created_at else None
        }
        for task in tasks
    ]

@app.get("/api/tasks/{task_id}/status")
async def get_task_status(task_id: str):
    """获取手动任务状态"""
    status = task_manager.get_task_status(task_id)
    if status['status'] == 'not_found':
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return status

@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消手动任务"""
    success = task_manager.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="无法取消任务")
    
    return {"message": "任务已取消"}

@app.post("/api/tasks/clear-completed")
async def clear_completed_tasks(db: Session = Depends(get_db)):
    """清理已完成/失败/取消的任务记录，并清理内存中过期任务"""
    try:
        # 统计数量
        from sqlalchemy import or_
        q = db.query(DownloadTask).filter(
            or_(
                DownloadTask.status == 'completed',
                DownloadTask.status == 'failed',
                DownloadTask.status == 'cancelled'
            )
        )
        count = q.count()
        q.delete(synchronize_session=False)
        db.commit()

        # 清理内存任务（立即清理）
        try:
            from .task_manager import enhanced_task_manager
            enhanced_task_manager.cleanup_completed_tasks(hours=0)
        except Exception:
            pass

        return {"message": "清理完成", "cleared": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Cookie管理API
@app.get("/api/cookies")
async def get_cookies(db: Session = Depends(get_db)):
    """获取Cookie列表"""
    cookies = db.query(Cookie).order_by(Cookie.created_at.desc()).all()
    
    return [
        {
            "id": cookie.id,
            "name": cookie.name,
            "sessdata": cookie.sessdata,
            "bili_jct": cookie.bili_jct,
            "dedeuserid": cookie.dedeuserid,
            "is_active": cookie.is_active,
            "usage_count": cookie.usage_count,
            "last_used": cookie.last_used.isoformat() if cookie.last_used else None,
            "created_at": cookie.created_at.isoformat() if cookie.created_at else None,
            "updated_at": cookie.updated_at.isoformat() if cookie.updated_at else None
        }
        for cookie in cookies
    ]

@app.get("/api/cookies/{cookie_id}")
async def get_cookie(cookie_id: int, db: Session = Depends(get_db)):
    """获取单个Cookie详情"""
    cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
    if not cookie:
        raise HTTPException(status_code=404, detail="Cookie不存在")
    
    return {
        "id": cookie.id,
        "name": cookie.name,
        "sessdata": cookie.sessdata,
        "bili_jct": cookie.bili_jct,
        "dedeuserid": cookie.dedeuserid,
        "is_active": cookie.is_active,
        "usage_count": cookie.usage_count,
        "last_used": cookie.last_used.isoformat() if cookie.last_used else None,
        "created_at": cookie.created_at.isoformat() if cookie.created_at else None,
        "updated_at": cookie.updated_at.isoformat() if cookie.updated_at else None
    }

@app.post("/api/cookies")
async def create_cookie(cookie: CookieCreate, db: Session = Depends(get_db)):
    """添加新Cookie"""
    db_cookie = Cookie(
        name=cookie.name,
        sessdata=cookie.sessdata,
        bili_jct=cookie.bili_jct,
        dedeuserid=cookie.dedeuserid,
        created_at=datetime.now()
    )
    
    db.add(db_cookie)
    db.commit()
    db.refresh(db_cookie)
    
    return {"id": db_cookie.id, "message": "Cookie添加成功"}

@app.put("/api/cookies/{cookie_id}")
async def update_cookie(
    cookie_id: int,
    cookie: CookieUpdate,
    db: Session = Depends(get_db)
):
    """更新Cookie"""
    db_cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
    if not db_cookie:
        raise HTTPException(status_code=404, detail="Cookie不存在")
    
    # 更新字段（兼容 active -> is_active）
    data = cookie.dict(exclude_unset=True)
    # 提取并规范 is_active
    is_active_value = data.pop("is_active", None)
    if is_active_value is None and "active" in data:
        is_active_value = data.pop("active")

    # 设置其余字段
    for field, value in data.items():
        setattr(db_cookie, field, value)

    # 单独处理 is_active
    if is_active_value is not None:
        db_cookie.is_active = is_active_value
    
    db_cookie.updated_at = datetime.now()
    db.commit()
    
    return {"message": "Cookie更新成功"}

@app.delete("/api/cookies/{cookie_id}")
async def delete_cookie(cookie_id: int, db: Session = Depends(get_db)):
    """删除Cookie"""
    db_cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
    if not db_cookie:
        raise HTTPException(status_code=404, detail="Cookie不存在")
    
    db.delete(db_cookie)
    db.commit()
    
    return {"message": "Cookie删除成功"}

@app.post("/api/cookies/{cookie_id}/validate")
async def validate_cookie(cookie_id: int, db: Session = Depends(get_db)):
    """验证Cookie"""
    db_cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
    if not db_cookie:
        raise HTTPException(status_code=404, detail="Cookie不存在")
    
    is_valid = await cookie_manager.validate_cookie(db_cookie)
    
    # 重新查询Cookie以获取最新状态
    db.refresh(db_cookie)
    
    # 根据结果更新失败计数或重置
    if is_valid:
        try:
            cookie_manager.reset_failures(db, db_cookie.id)
            db.refresh(db_cookie)  # 刷新状态
        except Exception:
            pass
    else:
        try:
            cookie_manager.record_failure(db, db_cookie.id, "验证失败")
            db.refresh(db_cookie)  # 刷新状态
        except Exception:
            # 老库不支持失败字段则可能已被直接禁用
            pass

    # 读取当前失败计数（若存在）
    failure_info = {}
    if hasattr(db_cookie, 'failure_count'):
        failure_info = {
            "failure_count": db_cookie.failure_count or 0,
            "last_failure_at": db_cookie.last_failure_at.isoformat() if db_cookie.last_failure_at else None,
            "is_active": db_cookie.is_active,
        }
    
    return {"valid": is_valid and db_cookie.is_active, "message": "验证完成", **failure_info}

# 系统设置API
@app.get("/api/settings")
async def get_settings(db: Session = Depends(get_db)):
    """获取系统设置"""
    settings = db.query(Settings).all()
    
    return {
        setting.key: {
            "value": setting.value,
            "description": setting.description
        }
        for setting in settings
    }

@app.put("/api/settings/{key}")
async def update_setting(key: str, setting: SettingUpdate, db: Session = Depends(get_db)):
    """更新系统设置"""
    db_setting = db.query(Settings).filter(Settings.key == key).first()
    if not db_setting:
        raise HTTPException(status_code=404, detail="设置项不存在")
    
    db_setting.value = setting.value
    db_setting.updated_at = datetime.now()
    db.commit()
    
    # 如果是订阅检查间隔，更新调度器（兼容 key: auto_check_interval 与 check_interval）
    if key in ("auto_check_interval", "check_interval"):
        try:
            interval = int(setting.value)
            scheduler.update_subscription_check_interval(interval)
        except ValueError:
            pass
    
    return {"message": "设置更新成功"}

# 调度器管理API
@app.get("/api/scheduler/jobs")
async def get_scheduler_jobs():
    """获取调度器任务列表"""
    return scheduler.get_jobs()

@app.post("/api/scheduler/check-subscriptions")
async def trigger_subscription_check(background_tasks: BackgroundTasks):
    """手动触发订阅检查"""
    background_tasks.add_task(scheduler.check_subscriptions)
    return {"message": "订阅检查已启动"}

@app.post("/api/scheduler/validate-cookies")
async def trigger_cookie_validation(background_tasks: BackgroundTasks):
    """手动触发Cookie验证"""
    background_tasks.add_task(scheduler.validate_cookies)
    return {"message": "Cookie验证已启动"}

# 视频检测服务API
@app.get("/api/video-detection/status")
async def get_video_detection_status():
    """获取视频检测服务状态"""
    try:
        status = video_detection_service.get_status()
        return {
            "success": True,
            "data": status
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/video-detection/start")
async def start_video_detection_service():
    """启动视频检测服务"""
    try:
        if video_detection_service.is_running:
            return {
                "success": True,
                "message": "视频检测服务已在运行中"
            }
        
        await video_detection_service.start_service()
        return {
            "success": True,
            "message": "视频检测服务启动成功"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/video-detection/stop")
async def stop_video_detection_service():
    """停止视频检测服务"""
    try:
        await video_detection_service.stop_service()
        return {
            "success": True,
            "message": "视频检测服务停止成功"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/video-detection/scan/full")
async def trigger_full_video_scan(background_tasks: BackgroundTasks):
    """触发完整视频扫描"""
    try:
        # 在后台执行扫描任务
        background_tasks.add_task(video_detection_service.full_scan)
        
        return {
            "success": True,
            "message": "完整扫描任务已启动，将在后台执行"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/video-detection/scan/incremental")
async def trigger_incremental_video_scan(background_tasks: BackgroundTasks):
    """触发增量视频扫描"""
    try:
        # 在后台执行增量扫描
        background_tasks.add_task(video_detection_service.incremental_scan)
        
        return {
            "success": True,
            "message": "增量扫描任务已启动，将在后台执行"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/video-detection/config")
async def update_video_detection_config(scan_interval: int = 300):
    """更新视频检测服务配置"""
    try:
        if scan_interval < 60:
            raise HTTPException(status_code=400, detail="扫描间隔不能少于60秒")
        
        video_detection_service.scan_interval = scan_interval
        
        return {
            "success": True,
            "message": f"扫描间隔已更新为{scan_interval}秒"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 订阅同步状态相关API
@app.get("/api/subscriptions/{subscription_id}/sync_status")
async def get_sync_status(subscription_id: int, db: Session = Depends(get_db)):
    """获取订阅同步状态"""
    try:
        key = f"sync:{subscription_id}:status"
        setting = db.query(Settings).filter(Settings.key == key).first()
        
        if not setting or not setting.value:
            return {
                "subscription_id": subscription_id,
                "status": "idle",
                "started_at": None,
                "updated_at": None,
                "remote_total": 0,
                "existing": 0,
                "pending": 0
            }
        
        try:
            data = json.loads(setting.value)
            return {
                "subscription_id": subscription_id,
                "status": data.get("status", "idle"),
                "started_at": data.get("started_at"),
                "updated_at": data.get("updated_at"),
                "completed_at": data.get("completed_at"),
                "remote_total": data.get("remote_total", 0),
                "existing": data.get("existing", 0),
                "pending": data.get("pending", 0),
                "error": data.get("error")
            }
        except json.JSONDecodeError:
            logger.warning(f"Corrupted sync status JSON for subscription {subscription_id}")
            return {
                "subscription_id": subscription_id,
                "status": "idle",
                "started_at": None,
                "updated_at": None,
                "remote_total": 0,
                "existing": 0,
                "pending": 0
            }
    except Exception as e:
        logger.error(f"Failed to get sync status for subscription {subscription_id}: {e}")
        raise HTTPException(status_code=500, detail="获取同步状态失败")

@app.get("/api/subscriptions/{subscription_id}/sync_trace")
async def get_sync_trace(subscription_id: int, db: Session = Depends(get_db)):
    """获取订阅同步链路事件trace"""
    try:
        key = f"sync:{subscription_id}:trace"
        setting = db.query(Settings).filter(Settings.key == key).first()
        
        if not setting or not setting.value:
            return {
                "subscription_id": subscription_id,
                "events": []
            }
        
        try:
            events = json.loads(setting.value)
            if not isinstance(events, list):
                events = []
            return {
                "subscription_id": subscription_id,
                "events": events
            }
        except json.JSONDecodeError:
            logger.warning(f"Corrupted sync trace JSON for subscription {subscription_id}")
            return {
                "subscription_id": subscription_id,
                "events": []
            }
    except Exception as e:
        logger.error(f"Failed to get sync trace for subscription {subscription_id}: {e}")
        raise HTTPException(status_code=500, detail="获取同步trace失败")

@app.post("/api/subscriptions/sync_overview")
async def get_sync_overview(request: dict, db: Session = Depends(get_db)):
    """批量获取订阅同步状态概览"""
    try:
        subscription_ids = request.get('subscription_ids', [])
        
        # 限制批量大小，防止性能问题
        if len(subscription_ids) > 100:
            raise HTTPException(status_code=400, detail="最多支持100个订阅ID")
        
        if not subscription_ids:
            return {"items": []}
        
        # 使用 IN 查询减少数据库请求
        keys = [f"sync:{sid}:status" for sid in subscription_ids]
        settings = db.query(Settings).filter(Settings.key.in_(keys)).all()
        
        # 构建结果映射
        result_map = {}
        for setting in settings:
            try:
                sid = int(setting.key.split(':')[1])
                data = json.loads(setting.value)
                result_map[sid] = {
                    "id": sid,
                    "status": data.get('status', 'idle'),
                    "pending": data.get('pending', 0),
                    "remote_total": data.get('remote_total', 0),
                    "updated_at": data.get('updated_at')
                }
            except (ValueError, json.JSONDecodeError):
                continue
        
        # 填充缺失的订阅（返回默认状态）
        items = []
        for sid in subscription_ids:
            if sid in result_map:
                items.append(result_map[sid])
            else:
                items.append({
                    "id": sid,
                    "status": "idle",
                    "pending": 0,
                    "remote_total": 0,
                    "updated_at": None
                })
        
        return {"items": items}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get sync overview: {e}")
        raise HTTPException(status_code=500, detail="获取同步概览失败")

# 健康检查
@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "6.0.0"
    }
