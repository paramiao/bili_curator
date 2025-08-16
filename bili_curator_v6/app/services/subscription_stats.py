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
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, case

from ..models import Subscription, Video, Settings


def recompute_subscription_stats(db: Session, subscription_id: int, *, touch_last_check: bool = True) -> None:
    """按订阅ID重算统计并写回 Subscription。
    在已开启的事务中调用，调用方负责提交。
    """
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        return

    total_videos = db.query(Video).filter(Video.subscription_id == sub.id).count()
    downloaded_videos = db.query(Video).filter(
        Video.subscription_id == sub.id,
        Video.downloaded == True
    ).count()

    sub.total_videos = total_videos
    sub.downloaded_videos = downloaded_videos
    if touch_last_check:
        sub.last_check = datetime.now()
    sub.updated_at = datetime.now()


def recompute_all_subscriptions(db: Session, *, touch_last_check: bool = False) -> None:
    """为所有订阅重算统计（聚合优化版）。
    - 使用单次聚合查询获取每个订阅的统计，避免 N+1 COUNT 查询。
    - 在已开启的事务中调用，调用方负责提交。
    """
    now = datetime.now()

    # 统计每个 subscription_id 的总视频数与已下载视频数
    agg_rows = db.query(
        Video.subscription_id.label('sid'),
        func.count(Video.id).label('total'),
        func.coalesce(func.sum(case((Video.downloaded == True, 1), else_=0)), 0).label('downloaded')
    ).group_by(Video.subscription_id).all()

    stats_map = {row.sid: (row.total or 0, row.downloaded or 0) for row in agg_rows}

    # 更新所有订阅（包括没有任何视频的订阅，填充为0）
    subs = db.query(Subscription).all()
    for sub in subs:
        total_videos, downloaded_videos = stats_map.get(sub.id, (0, 0))
        sub.total_videos = total_videos
        sub.downloaded_videos = downloaded_videos
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
