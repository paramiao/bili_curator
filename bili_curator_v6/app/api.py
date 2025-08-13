"""
FastAPI API路由定义
"""
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime
import asyncio
import json

from .models import (
    Subscription, Video, DownloadTask, Cookie, Settings, SubscriptionUpdate, CookieCreate, CookieUpdate, SettingUpdate,
    get_db
)
from .scheduler import scheduler, task_manager
from .cookie_manager import cookie_manager
from .downloader import downloader
from .video_detection_service import video_detection_service

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
        
        db_subscription = Subscription(
            name=subscription["name"],
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
            
            db.commit()
            
            return {
                "message": "订阅创建成功", 
                "id": db_subscription.id,
                "associated_videos": associated_count,
                "total_videos": len(matching_videos)
            }
        except Exception as e:
            # 如果关联失败，不影响订阅创建
            logger.warning(f"订阅创建成功，但自动关联失败: {e}")
            return {"message": "订阅创建成功", "id": db_subscription.id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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
        # 获取可用Cookie
        cookie = cookie_manager.get_available_cookie(db)
        if not cookie:
            return {"error": "没有可用的Cookie，无法获取合集信息"}
        
        # 使用yt-dlp获取合集信息
        import json as json_lib
        import tempfile, os
        
        # 写入临时 cookies.txt (Netscape 格式)
        cookies_path = None
        fd, cookies_path = tempfile.mkstemp(prefix='cookies_', suffix='.txt')
        os.close(fd)
        with open(cookies_path, 'w', encoding='utf-8') as cf:
            # Netscape cookie file header is required by yt-dlp
            cf.write("# Netscape HTTP Cookie File\n")
            cf.write("# This file was generated by bili_curator V6\n\n")
            cf.writelines([
                f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{cookie.sessdata}\n",
                f".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\t{cookie.bili_jct}\n",
                f".bilibili.com\tTRUE\t/\tFALSE\t0\tDedeUserID\t{cookie.dedeuserid}\n",
            ])
        
        cmd = [
            'yt-dlp',
            '--dump-json',
            '--playlist-items', '1',  # 只获取第一个视频的信息
            '--no-download',
            '--cookies', cookies_path,
            url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='ignore')
            return {"error": f"无法获取合集信息: {error_msg}"}
        
        # 解析输出
        for line in stdout.decode('utf-8', errors='ignore').strip().split('\n'):
            if line.strip():
                try:
                    video_info = json_lib.loads(line)
                    uploader = video_info.get('uploader', '')
                    playlist_title = video_info.get('playlist_title', '')
                    
                    if uploader and playlist_title:
                        name = f"{uploader}：{playlist_title}"
                    elif playlist_title:
                        name = playlist_title
                    elif uploader:
                        name = f"{uploader}的合集"
                    else:
                        name = "未知合集"
                    
                    # 更新Cookie使用统计
                    cookie_manager.update_cookie_usage(db, cookie.id)
                    
                    return {
                        "name": name,
                        "uploader": uploader,
                        "playlist_title": playlist_title
                    }
                    
                except json_lib.JSONDecodeError:
                    continue
        
        return {"error": "无法解析合集信息"}
        
    except Exception as e:
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

@app.delete("/api/videos/{video_id}")
async def delete_video(video_id: int, db: Session = Depends(get_db)):
    """删除视频记录"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    
    db.delete(video)
    db.commit()
    
    return {"message": "视频记录删除成功"}

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
    
    if not is_valid:
        db_cookie.is_active = False
        db.commit()
    
    return {"valid": is_valid, "message": "验证完成"}

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
