"""
远端总数缓存访问工具：基于统一缓存服务的重构版本
迁移到 UnifiedCacheService，保持向后兼容
"""
from __future__ import annotations
from typing import Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
import json
from sqlalchemy.orm import Session
from loguru import logger

from ..models import Settings
from ..constants import (
    settings_key_remote_total,
    settings_key_remote_total_legacy,
)
from .unified_cache_service import unified_cache
from .cache_invalidation_service import invalidate_subscription_caches


def read_remote_total_raw(db: Session, sub_id: int) -> Optional[Dict[str, Any]]:
    """读取远端总数缓存（不判断新鲜度）。
    返回结构：{ total: int, timestamp: iso, url: str } 或 None
    使用统一缓存服务，保持向后兼容。
    """
    try:
        # 使用统一缓存服务读取
        cache_data = unified_cache.get(db, 'remote_total', str(sub_id), ttl_hours=24*7)  # 7天TTL，不判断新鲜度
        if cache_data and isinstance(cache_data, dict) and 'total' in cache_data:
            return cache_data
        
        # 回退到旧的直接数据库访问（兼容性）
        keys = [settings_key_remote_total(sub_id), settings_key_remote_total_legacy(sub_id)]
        rows = db.query(Settings).filter(Settings.key.in_(keys)).all()
        smap = {r.key: r.value for r in rows if r and r.key}
        for k in keys:
            val = smap.get(k)
            if not val:
                continue
            try:
                data = json.loads(val)
                if isinstance(data, dict) and 'total' in data:
                    # 迁移到统一缓存
                    unified_cache.set(db, 'remote_total', str(sub_id), data, 
                                    description=f"Remote total for subscription {sub_id}")
                    return data
            except Exception:
                continue
        return None
    except Exception as e:
        logger.debug(f"read_remote_total_raw error: {e}")
        return None


def read_remote_total_fresh(db: Session, sub_id: int, max_age_hours: int = 1) -> Optional[int]:
    """读取新鲜的远端总数（默认1小时内）。返回 int 或 None。"""
    try:
        # 使用统一缓存服务，自动处理TTL
        cache_data = unified_cache.get(db, 'remote_total', str(sub_id), ttl_hours=max_age_hours)
        if cache_data and isinstance(cache_data, dict):
            return int(cache_data.get('total') or 0)
        return None
    except Exception as e:
        logger.debug(f"read_remote_total_fresh error: {e}")
        return None


def write_remote_total(db: Session, sub_id: int, total: int, url: Optional[str]) -> None:
    """写入远端总数缓存，使用统一缓存服务，保持向后兼容。"""
    payload = {
        'total': int(total),
        'timestamp': datetime.now().isoformat(),
        'url': url or '',
    }
    
    try:
        # 使用统一缓存服务写入
        unified_cache.set(db, 'remote_total', str(sub_id), payload, 
                         description=f"Remote total for subscription {sub_id}")
        
        # 触发缓存失效（更新相关缓存）
        invalidate_subscription_caches(db, sub_id, 'remote_sync_completed')
        
        logger.debug(f"Updated remote total cache for subscription {sub_id}: {total}")
        
    except Exception as e:
        logger.error(f"Failed to write remote total for subscription {sub_id}: {e}")
        # 回退到旧方式（确保数据不丢失）
        _write_remote_total_legacy(db, sub_id, total, url)


def _write_remote_total_legacy(db: Session, sub_id: int, total: int, url: Optional[str]) -> None:
    """旧版写入方式，作为回退机制"""
    payload = {
        'total': int(total),
        'timestamp': datetime.now().isoformat(),
        'url': url or '',
    }
    new_key = settings_key_remote_total(sub_id)
    legacy_key = settings_key_remote_total_legacy(sub_id)
    rows = db.query(Settings).filter(Settings.key.in_([new_key, legacy_key])).all()
    smap = {r.key: r for r in rows}

    for k in (new_key, legacy_key):
        s = smap.get(k)
        if s:
            s.value = json.dumps(payload)
        else:
            db.add(Settings(key=k, value=json.dumps(payload)))
    db.commit()
