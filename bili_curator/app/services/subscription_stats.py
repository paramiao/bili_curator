"""
订阅统计服务：统一刷新 Subscription 的统计字段
- total_videos: 该订阅关联的视频总数
- downloaded_videos: 已标记为下载完成的视频数（Video.downloaded == True）
- last_check: 最近一次统计时间（可选，根据调用场景设定）

提供两个入口：
- recompute_subscription_stats(db, subscription_id, touch_last_check=True)
- recompute_all_subscriptions(db, touch_last_check=False)
"""
from datetime import datetime, timedelta
from typing import Optional, Dict
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import func, case

from ..models import Subscription, Video, Settings


def recompute_subscription_stats(db: Session, subscription_id: int, *, touch_last_check: bool = True) -> None:
    """按订阅ID重算统计并写回 Subscription（本地优先：以磁盘实际存在的视频文件为准）。
    在已开启的事务中调用，调用方负责提交。
    """
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        return

    videos = db.query(Video).filter(Video.subscription_id == sub.id).all()
    existing_count = 0
    for v in videos:
        vp = Path(v.video_path) if getattr(v, 'video_path', None) else None
        if vp and vp.exists():
            existing_count += 1

    # 本地为准：total 与 downloaded 都以“磁盘存在的视频文件数”为准
    sub.total_videos = existing_count
    sub.downloaded_videos = existing_count
    if touch_last_check:
        sub.last_check = datetime.now()
    sub.updated_at = datetime.now()


def recompute_all_subscriptions(db: Session, *, touch_last_check: bool = False) -> None:
    """为所有订阅重算统计（本地优先的文件系统口径）。
    - 以磁盘存在的视频文件为准计算 total/downloaded。
    - 在已开启的事务中调用，调用方负责提交。
    """
    now = datetime.now()

    # 读取所有视频的 subscription_id 和 video_path，一次性在内存归并
    rows = db.query(Video.subscription_id, Video.video_path).all()
    cnt: Dict[int, int] = {}
    for sid, vpath in rows:
        if sid is None:
            continue
        try:
            vp = Path(vpath) if vpath else None
            if vp and vp.exists():
                cnt[sid] = cnt.get(sid, 0) + 1
        except Exception:
            # 任何异常不影响统计，按不存在处理
            pass

    # 更新所有订阅（包括没有任何视频文件的订阅，填充为0）
    subs = db.query(Subscription).all()
    for sub in subs:
        existing = cnt.get(sub.id, 0)
        sub.total_videos = existing
        sub.downloaded_videos = existing
        if touch_last_check:
            sub.last_check = now
        sub.updated_at = now


# ------------------------------
# 去抖与合并重算辅助（使用 Settings 持久化状态）
# ------------------------------
_SET_KEY_EVENT_COUNT = "stats_recompute_event_count"
_SET_KEY_LAST_RUN_AT = "stats_recompute_last_run_at"

def _get_setting(db: Session, key: str) -> Optional[Settings]:
    return db.query(Settings).filter(Settings.key == key).first()

def _set_setting(db: Session, key: str, value: str, description: str = "") -> None:
    s = _get_setting(db, key)
    if s:
        s.value = value
        s.description = s.description or description
    else:
        s = Settings(key=key, value=value, description=description)
        db.add(s)

def record_recompute_event(db: Session) -> None:
    """记录一次统计待重算事件，调用方在事务中调用，提交由调用方负责。"""
    s = _get_setting(db, _SET_KEY_EVENT_COUNT)
    try:
        curr = int(s.value) if s and s.value is not None else 0
    except Exception:
        curr = 0
    curr += 1
    _set_setting(db, _SET_KEY_EVENT_COUNT, str(curr), "累计的统计重算事件计数")

def maybe_try_recompute_all(db: Session, *, max_events: int = 20, max_age_seconds: int = 300) -> bool:
    """当满足阈值时触发一次全量重算。
    返回是否执行了重算。
    由调用方在合适时机（如任务结束）调用，并负责提交事务。
    """
    # 读取计数与上次执行时间
    s_count = _get_setting(db, _SET_KEY_EVENT_COUNT)
    s_last = _get_setting(db, _SET_KEY_LAST_RUN_AT)
    try:
        count = int(s_count.value) if s_count and s_count.value is not None else 0
    except Exception:
        count = 0
    try:
        last_dt = datetime.fromisoformat(s_last.value) if s_last and s_last.value else None
    except Exception:
        last_dt = None

    now = datetime.now()
    age_ok = (last_dt is None) or ((now - last_dt) >= timedelta(seconds=max_age_seconds))
    events_ok = count >= max_events

    if not (age_ok or events_ok):
        return False

    # 执行重算
    recompute_all_subscriptions(db, touch_last_check=False)
    # 重置计数与更新时间
    _set_setting(db, _SET_KEY_EVENT_COUNT, "0", "累计的统计重算事件计数")
    _set_setting(db, _SET_KEY_LAST_RUN_AT, now.isoformat(), "最近一次全量统计重算时间")
    return True
