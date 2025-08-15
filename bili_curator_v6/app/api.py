# UA 策略：无 Cookie 使用随机 UA，Cookie 模式可用稳定 UA
_UA_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
]

def get_user_agent(requires_cookie: bool) -> str:
    try:
        if requires_cookie:
            return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        return random.choice(_UA_POOL)
    except Exception:
        return _UA_POOL[0]

"""
FastAPI API路由定义
"""
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime
import logging
import asyncio
import random
import json
import os
from pathlib import Path

from .models import (
    Subscription, Video, DownloadTask, Cookie, Settings, SubscriptionUpdate, CookieCreate, CookieUpdate, SettingUpdate,
    get_db
)
from .scheduler import scheduler, task_manager
from .cookie_manager import cookie_manager
from .downloader import downloader
from .video_detection_service import video_detection_service
from .queue_manager import yt_dlp_semaphore, get_subscription_lock, request_queue

# Logger
logger = logging.getLogger(__name__)

# UA 策略：无 Cookie 使用随机 UA，Cookie 模式使用稳定 UA
_UA_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
]

def get_user_agent(requires_cookie: bool) -> str:
    try:
        if requires_cookie:
            return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        return random.choice(_UA_POOL)
    except Exception:
        return _UA_POOL[0]

# Pydantic模型定义
class SubscriptionCreate(BaseModel):
    name: str
    type: str  # 'collection', 'uploader', 'keyword'
    url: Optional[str] = None
    uploader_id: Optional[str] = None
    keyword: Optional[str] = None

class SubscriptionUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    active: Optional[bool] = None
    url: Optional[str] = None
    uploader_id: Optional[str] = None
    keyword: Optional[str] = None

class CookieCreate(BaseModel):
    name: str
    sessdata: str
    bili_jct: Optional[str] = ""
    dedeuserid: Optional[str] = ""

class CookieUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    active: Optional[bool] = None
    sessdata: Optional[str] = None
    bili_jct: Optional[str] = None
    dedeuserid: Optional[str] = None

class SettingUpdate(BaseModel):
    value: str

# 创建FastAPI应用
app = FastAPI(
    title="bili_curator V6",
    description="B站视频下载管理系统",
    version="6.0.0"
)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/web", StaticFiles(directory="web/dist"), name="web")

# 根路径返回前端页面
@app.get("/", response_class=HTMLResponse)
async def read_root():
    """返回前端页面"""
    try:
        with open("web/dist/index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="""
        <html>
            <head><title>bili_curator V6</title></head>
            <body>
                <h1>🎬 bili_curator V6</h1>
                <p>前端页面正在构建中...</p>
                <p>API文档: <a href="/docs">/docs</a></p>
            </body>
        </html>
        """)

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
        "recent_tasks": [],
        "scheduler_jobs": [],
        "running_tasks": []
    }

# 队列调试接口
@app.get("/api/queue/stats")
async def queue_stats():
    """返回队列容量/运行/暂停与各通道排队数。"""
    try:
        return request_queue.stats()
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
    """获取所有订阅"""
    subscriptions = db.query(Subscription).all()
    result = []
    
    for sub in subscriptions:
        # 计算统计信息
        total_videos = db.query(Video).filter(Video.subscription_id == sub.id).count()
        downloaded_videos = db.query(Video).filter(
            Video.subscription_id == sub.id,
            Video.video_path.isnot(None)
        ).count()
        pending_videos = max(0, total_videos - downloaded_videos)
        
        # 更新数据库中的统计信息
        sub.total_videos = total_videos
        sub.downloaded_videos = downloaded_videos
        
        result.append({
            "id": sub.id,
            "name": sub.name,
            "type": sub.type,
            "url": sub.url,
            "uploader_id": sub.uploader_id,
            "keyword": sub.keyword,
            "specific_urls": sub.specific_urls,
            "date_after": sub.date_after.isoformat() if sub.date_after else None,
            "date_before": sub.date_before.isoformat() if sub.date_before else None,
            "min_likes": sub.min_likes,
            "min_favorites": sub.min_favorites,
            "min_views": sub.min_views,
            "total_videos": total_videos,
            "downloaded_videos": downloaded_videos,
            "pending_videos": pending_videos,
            "is_active": sub.is_active,
            "last_check": sub.last_check.isoformat() if sub.last_check else None,
            "created_at": sub.created_at.isoformat() if sub.created_at else None,
            "updated_at": sub.updated_at.isoformat() if sub.updated_at else None
        })
    
    db.commit()
    return result

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
            name_to_use = (subscription.get("name") or "未知订阅").strip() or "未知订阅"
        
        db_subscription = Subscription(
            name=name_to_use,
            type=subscription["type"],
            url=subscription.get("url"),
            uploader_id=subscription.get("uploader_id"),
            keyword=subscription.get("keyword"),
            specific_urls=subscription.get("specific_urls"),
            date_after=date_after,
            date_before=date_before,
            min_likes=subscription.get("min_likes"),
            min_favorites=subscription.get("min_favorites"),
            min_views=subscription.get("min_views")
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
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/subscriptions/{subscription_id}/expected-total")
async def get_subscription_expected_total(subscription_id: int, db: Session = Depends(get_db)):
    """获取远端合集应有总计视频数（不依赖本地DB），用于校准显示。
    仅对 type=collection 且存在 url 的订阅有效。
    """
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    if sub.type != "collection" or not sub.url:
        raise HTTPException(status_code=400, detail="该订阅不是合集或缺少URL")

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
                async with sub_lock:
                    async with yt_dlp_semaphore:
                        proc = await asyncio.create_subprocess_exec(
                            *args,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        out, err = await proc.communicate()
                        return proc.returncode, out, err

            expected_total = None
            last_err = None
            # 手动分页
            try:
                chunk_size = 100
                max_chunks = 50
                total_count = 0
                chunk_idx = 0
                while chunk_idx < max_chunks:
                    start = chunk_idx * chunk_size + 1
                    end = start + chunk_size - 1
                    cmd_chunk = common_args + ['--dump-json', '--flat-playlist', '--playlist-items', f'{start}-{end}', sub.url]
                    rc, out, err = await run_and_parse(cmd_chunk)
                    if rc != 0:
                        last_err = err.decode('utf-8', errors='ignore')
                        break
                    count_this = 0
                    for line in (out.decode('utf-8', errors='ignore') or '').strip().split('\n'):
                        if not line.strip():
                            continue
                        try:
                            _ = json_lib.loads(line)
                            count_this += 1
                        except json_lib.JSONDecodeError:
                            continue
                    total_count += count_this
                    if count_this < chunk_size:
                        break
                    chunk_idx += 1
                    try:
                        await asyncio.sleep(random.uniform(2, 4))
                    except Exception:
                        pass
                if total_count > 0:
                    expected_total = total_count
            except Exception as e:
                last_err = e

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
                return {"expected_total_videos": int(expected), "job_id": job_nc}
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
            # Cookie 使用统计
            try:
                cookie_manager.update_cookie_usage(db, cookie.id)
            except Exception:
                pass
            await request_queue.mark_done(job_c)
            return {"expected_total_videos": int(expected2), "job_id": job_c}
        finally:
            try:
                if cookies_path and os.path.exists(cookies_path):
                    os.remove(cookies_path)
            except Exception:
                pass

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"获取合集总数失败: {e}")
        # 外层兜底异常，此时内部已尽力标记 job_nc/job_c 的状态
        raise HTTPException(status_code=500, detail="获取合集总数失败")

@app.get("/api/subscriptions/{subscription_id}")
async def get_subscription(subscription_id: int, db: Session = Depends(get_db)):
    """获取单个订阅详情"""
    subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="订阅不存在")

    # Calculate statistics
    total_videos = db.query(Video).filter(Video.subscription_id == subscription.id).count()
    downloaded_videos = db.query(Video).filter(
        Video.subscription_id == subscription.id,
        Video.video_path.isnot(None)
    ).count()
    pending_videos = max(0, total_videos - downloaded_videos)

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
        "total_videos": total_videos,
        "downloaded_videos": downloaded_videos,
        "pending_videos": pending_videos,
        "is_active": subscription.is_active,
        "last_check": subscription.last_check.isoformat() if subscription.last_check else None,
        "created_at": subscription.created_at.isoformat() if subscription.created_at else None,
        "updated_at": subscription.updated_at.isoformat() if subscription.updated_at else None
    }

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

    # 单独处理 is_active
    if is_active_value is not None:
        db_subscription.is_active = is_active_value
    
    db_subscription.updated_at = datetime.now()
    db.commit()
    
    return {"message": "订阅更新成功"}

@app.delete("/api/subscriptions/{subscription_id}")
async def delete_subscription(subscription_id: int, db: Session = Depends(get_db)):
    """删除订阅"""
    db_subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not db_subscription:
        raise HTTPException(status_code=404, detail="订阅不存在")
    
    db.delete(db_subscription)
    db.commit()
    
    return {"message": "订阅删除成功"}

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
    """按订阅聚合统计（数量、已下载、容量、最近上传）"""
    # 数量与容量
    counts = (
        db.query(
            Video.subscription_id.label('sid'),
            func.count(Video.id).label('total'),
            func.sum(case((Video.video_path.isnot(None), 1), else_=0)).label('downloaded'),
            func.coalesce(func.sum(Video.file_size), 0).label('size'),
            func.max(Video.upload_date).label('latest_upload')
        )
        .group_by(Video.subscription_id)
        .all()
    )

    # 订阅名称
    subs = {s.id: s for s in db.query(Subscription).all()}

    result = []
    for row in counts:
        sid = row.sid
        sub = subs.get(sid)
        result.append({
            "subscription_id": sid,
            "subscription_name": sub.name if sub else None,
            "type": sub.type if sub else None,
            "total_videos": int(row.total or 0),
            "downloaded_videos": int(row.downloaded or 0),
            "total_size": int(row.size or 0),
            "latest_upload": row.latest_upload.isoformat() if row.latest_upload else None,
        })

    # 确保包含没有视频记录但存在的订阅（total=0）
    for sid, sub in subs.items():
        if not any(item["subscription_id"] == sid for item in result):
            result.append({
                "subscription_id": sid,
                "subscription_name": sub.name,
                "type": sub.type,
                "total_videos": 0,
                "downloaded_videos": 0,
                "total_size": 0,
                "latest_upload": None,
            })

    # 按下载数量或大小排序，便于前端默认展示
    result.sort(key=lambda x: (x["downloaded_videos"], x["total_size"]), reverse=True)
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
    """按下载根目录下的一级目录聚合统计
    - 基于 DOWNLOAD_PATH 的直接子目录进行分组
    - 统计: total_videos、downloaded_videos、total_size
    """
    download_root = Path(os.getenv('DOWNLOAD_PATH', '/app/downloads')).resolve()
    # 仅统计有文件路径的视频
    videos = db.query(Video).filter(Video.video_path.isnot(None)).all()
    stats: Dict[str, Dict[str, Any]] = {}

    for v in videos:
        try:
            vp = Path(v.video_path).resolve()
            rel = vp.relative_to(download_root)
            first = rel.parts[0] if len(rel.parts) > 0 else ""
        except Exception:
            # 路径不在下载根内，归为 _others
            first = "_others"
        if not first:
            first = "_root"
        s = stats.setdefault(first, {
            "dir": first,
            "total_videos": 0,
            "downloaded_videos": 0,
            "total_size": 0,
        })
        s["total_videos"] += 1
        if v.video_path:
            s["downloaded_videos"] += 1
        # 优先使用 total_size；否则回退到 file_size + audio_size
        size_sum = (v.total_size if getattr(v, 'total_size', None) is not None else None)
        if size_sum is None:
            size_sum = int(v.file_size or 0) + int((getattr(v, 'audio_size', 0) or 0))
        s["total_size"] += int(size_sum or 0)

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
async def get_directory_videos(dir: str, page: int = 1, size: int = 20, db: Session = Depends(get_db)):
    """获取某一级目录下的全部视频（含子目录），分页返回
    - 参数 dir: DOWNLOAD_PATH 下的一级目录名（与 get_directory_stats 返回的 dir 对应）
    - 通过 SQL LIKE 前缀匹配提高效率
    """
    if not dir:
        raise HTTPException(status_code=400, detail="dir 不能为空")
    download_root = Path(os.getenv('DOWNLOAD_PATH', '/app/downloads')).resolve()
    base = (download_root / dir).resolve()
    # 安全校验，防止目录穿越
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
    # 根据结果更新失败计数或重置
    if is_valid:
        try:
            cookie_manager.reset_failures(db, db_cookie.id)
        except Exception:
            pass
    else:
        try:
            cookie_manager.record_failure(db, db_cookie.id, "验证失败")
        except Exception:
            # 老库不支持失败字段则可能已被直接禁用
            pass

    # 读取当前失败计数（若存在）
    failure_info = {}
    if hasattr(db_cookie, 'failure_count'):
        failure_info = {
            "failure_count": db_cookie.failure_count,
            "last_failure_at": db_cookie.last_failure_at.isoformat() if db_cookie.last_failure_at else None,
            "is_active": db_cookie.is_active,
        }
    
    return {"valid": is_valid, "message": "验证完成", **failure_info}

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
    
    # 如果是订阅检查间隔，更新调度器
    if key == "auto_check_interval":
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

# 健康检查
@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "6.0.0"
    }
