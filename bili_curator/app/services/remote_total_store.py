"""
远端总数缓存访问工具：集中封装 Settings 读写，统一键与兼容逻辑。
禁止在其他模块直接拼接 Settings 键名。
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


def read_remote_total_raw(db: Session, sub_id: int) -> Optional[Dict[str, Any]]:
    """读取远端总数缓存（不判断新鲜度）。
    返回结构：{ total: int, timestamp: iso, url: str } 或 None
    优先读取新键，回退旧键。
    """
    try:
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
                    return data
            except Exception:
                continue
        return None
    except Exception as e:
        logger.debug(f"read_remote_total_raw error: {e}")
        return None


def read_remote_total_fresh(db: Session, sub_id: int, max_age_hours: int = 1) -> Optional[int]:
    """读取新鲜的远端总数（默认1小时内）。返回 int 或 None。"""
    data = read_remote_total_raw(db, sub_id)
    if not data:
        return None
    try:
        ts = data.get('timestamp')
        if not ts:
            return None
        t = datetime.fromisoformat(ts)
        if datetime.now() - t <= timedelta(hours=max_age_hours):
            return int(data.get('total') or 0)
    except Exception:
        return None
    return None


def write_remote_total(db: Session, sub_id: int, total: int, url: Optional[str]) -> None:
    """写入远端总数缓存，同时写入新旧键（过渡期兼容），内部提交事务。"""
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
