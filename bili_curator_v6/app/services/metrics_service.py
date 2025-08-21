"""
统一统计服务 metrics_service
- 提供单订阅与全局聚合的统一统计口径
- 远端总数：以最近一次成功快照为准（Settings 缓存），暴露是否过期
- 待下载：pending = max(0, remote_total - on_disk_total - failed_perm)
- 容量统计：三级回退（DB.total_size -> DB.file_size -> 磁盘文件大小）
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timedelta
import os
import time

from sqlalchemy.orm import Session
from loguru import logger

from ..models import Subscription, Video
from .remote_total_store import read_remote_total_raw, read_remote_total_fresh

# 概览结果的轻量缓存（60s），用于降低频繁调用时的遍历与聚合开销
_OVERVIEW_CACHE: Dict[str, Optional[object]] = {
    'ts': None,   # type: Optional[float]
    'data': None, # type: Optional[Dict]
}


@dataclass
class RemoteSnapshot:
    total: Optional[int]
    timestamp: Optional[datetime]
    url: Optional[str]
    fresh: bool


def _read_remote_snapshot(db: Session, sub_id: int, *, ttl_hours: int = 1) -> RemoteSnapshot:
    raw = read_remote_total_raw(db, sub_id)
    if not raw:
        return RemoteSnapshot(total=None, timestamp=None, url=None, fresh=False)
    try:
        ts = raw.get('timestamp')
        t = datetime.fromisoformat(ts) if ts else None
    except Exception:
        t = None
    fresh = False
    if t:
        try:
            fresh = (datetime.now() - t) <= timedelta(hours=ttl_hours)
        except Exception:
            fresh = False
    total_val = None
    try:
        if 'total' in raw:
            total_val = int(raw.get('total') or 0)
    except Exception:
        total_val = None
    return RemoteSnapshot(total=total_val, timestamp=t, url=raw.get('url'), fresh=fresh)


def _get_on_disk_total(db: Session, sub_id: int) -> int:
    # 以有文件为准（即 video_path 非空）
    return int(db.query(Video).filter(
        Video.subscription_id == sub_id,
        Video.video_path.isnot(None)
    ).count())


def _get_db_total(db: Session, sub_id: int) -> int:
    return int(db.query(Video).filter(Video.subscription_id == sub_id).count())


def _get_failed_perm(db: Session, sub_id: int) -> int:
    # 永久失败：download_failed == True
    return int(db.query(Video).filter(
        Video.subscription_id == sub_id,
        Video.download_failed == True
    ).count())


def _safe_filesize(path: Optional[str]) -> int:
    if not path:
        return 0
    try:
        return int(os.path.getsize(path)) if os.path.exists(path) else 0
    except Exception:
        return 0


def _compute_sizes(db: Session, sub_id: int) -> Dict:
    # 仅统计已下载的视频（有文件路径）
    videos: List[Video] = db.query(Video).filter(
        Video.subscription_id == sub_id,
        Video.video_path.isnot(None)
    ).all()

    total_bytes = 0
    count = 0
    for v in videos:
        size = None
        try:
            if v.total_size is not None:
                size = int(v.total_size)
            elif v.file_size is not None:
                size = int(v.file_size)
        except Exception:
            size = None
        if size is None or size <= 0:
            size = _safe_filesize(v.video_path)
        if size and size > 0:
            total_bytes += size
            count += 1

    return {
        'downloaded_files': count,
        'downloaded_size_bytes': int(total_bytes),
    }


def compute_subscription_metrics(db: Session, sub_id: int, *, ttl_hours: int = 1) -> Dict:
    """统一统计：返回单订阅的口径。
    字段：
      - expected_total: 远端应有总数（若无则为 None）
      - expected_total_cached: 是否来自新鲜缓存
      - expected_total_snapshot_at: 快照时间（ISO）
      - on_disk_total: 本地有文件数
      - db_total: 数据库视频记录数
      - failed_perm: 永久失败数
      - pending: max(0, expected_total - on_disk_total - failed_perm)，若 expected_total 缺失则为 None
      - sizes: { downloaded_files, downloaded_size_bytes }
    """
    sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
    if not sub:
        raise ValueError(f"订阅 {sub_id} 不存在")

    remote = _read_remote_snapshot(db, sub_id, ttl_hours=ttl_hours)
    on_disk_total = _get_on_disk_total(db, sub_id)
    db_total = _get_db_total(db, sub_id)
    failed_perm = _get_failed_perm(db, sub_id)

    if isinstance(remote.total, int):
        pending = max(0, int(remote.total) - on_disk_total - failed_perm)
    else:
        pending = None

    sizes = _compute_sizes(db, sub_id)

    return {
        'subscription_id': sub_id,
        'expected_total': remote.total,
        'expected_total_cached': bool(remote.fresh),
        'expected_total_snapshot_at': remote.timestamp.isoformat() if remote.timestamp else None,
        'on_disk_total': on_disk_total,
        'db_total': db_total,
        'failed_perm': failed_perm,
        'pending': pending,
        'sizes': sizes,
    }


def compute_overview_metrics(db: Session, *, ttl_hours: int = 1) -> Dict:
    """全局聚合视图，汇总所有订阅。
    - 对 expected_total 缺失的订阅不计入 pending（保持严格远端口径）
    """
    # 命中轻量缓存（60s）则直接返回，避免高频访问导致的磁盘与DB压力
    try:
        ts = _OVERVIEW_CACHE.get('ts')
        data = _OVERVIEW_CACHE.get('data')
        if isinstance(ts, (int, float)) and data is not None:
            if (time.time() - float(ts)) <= 60:
                # 返回缓存时补充计算时间
                cached = dict(data)
                try:
                    cached['computed_at'] = datetime.fromtimestamp(float(ts)).isoformat()
                except Exception:
                    cached['computed_at'] = None
                return cached
    except Exception:
        pass
    subs: List[Subscription] = db.query(Subscription).all()

    total_remote = 0
    total_local = 0
    total_db = 0
    total_failed = 0
    total_pending = 0
    total_size_bytes = 0

    for s in subs:
        m = compute_subscription_metrics(db, s.id, ttl_hours=ttl_hours)
        if isinstance(m.get('expected_total'), int):
            total_remote += m['expected_total'] or 0
            total_pending += m['pending'] or 0
        total_local += m['on_disk_total'] or 0
        total_db += m['db_total'] or 0
        total_failed += m['failed_perm'] or 0
        total_size_bytes += (m.get('sizes', {}).get('downloaded_size_bytes') or 0)

    result = {
        'remote_total': int(total_remote),
        'local_total': int(total_local),
        'db_total': int(total_db),
        'failed_perm_total': int(total_failed),
        'pending_total': int(total_pending),
        'downloaded_size_bytes': int(total_size_bytes),
        'computed_at': datetime.now().isoformat(),
    }

    # 写入缓存
    try:
        now_ts = time.time()
        _OVERVIEW_CACHE['ts'] = now_ts
        # 存缓存时不要把 computed_at 固化为字符串时间点，以 ts 为准
        _OVERVIEW_CACHE['data'] = {
            'remote_total': result['remote_total'],
            'local_total': result['local_total'],
            'db_total': result['db_total'],
            'failed_perm_total': result['failed_perm_total'],
            'pending_total': result['pending_total'],
            'downloaded_size_bytes': result['downloaded_size_bytes'],
        }
    except Exception:
        pass

    return result
