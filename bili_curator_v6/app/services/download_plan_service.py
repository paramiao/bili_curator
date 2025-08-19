import json
from typing import Dict, List
from loguru import logger
from sqlalchemy.orm import Session

from ..models import Settings


class DownloadPlanService:
    """
    下载计划服务（M2骨架）：
    - 输入远端IDs与本地索引，输出待下载计划（集合差）。
    - 写入轻量观测：agg:{sid}:pending_estimated。
    """

    def compute_plan_from_sets(self, db: Session, sid: int, remote_ids: List[str], local_ids: List[str]) -> Dict:
        rset = set([x for x in (remote_ids or []) if isinstance(x, str) and x])
        lset = set([x for x in (local_ids or []) if isinstance(x, str) and x])
        pending = list(rset - lset)
        # 简单稳定化：按字母排序，避免批次抖动
        pending.sort()

        # 写观测键（轻量）
        try:
            key = f"agg:{sid}:pending_estimated"
            val = str(len(pending))
            s = db.query(Settings).filter(Settings.key == key).first()
            if not s:
                db.add(Settings(key=key, value=val))
            else:
                s.value = val
            db.commit()
        except Exception:
            db.rollback()

        return {"ids": pending, "source": "set_diff"}


download_plan_service = DownloadPlanService()
