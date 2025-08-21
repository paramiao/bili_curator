import json
from typing import Dict, List
from loguru import logger
from sqlalchemy.orm import Session

from ..models import Settings


class DownloadPlanService:
    """
    下载计划服务（M2骨架）：
    - 输入远端IDs与本地索引，输出待下载计划（集合差）。
    - 已移除 agg:{sid}:pending_estimated 缓存写入，统一使用 compute_subscription_metrics。
    """

    def compute_plan_from_sets(self, db: Session, sid: int, remote_ids: List[str], local_ids: List[str]) -> Dict:
        rset = set([x for x in (remote_ids or []) if isinstance(x, str) and x])
        lset = set([x for x in (local_ids or []) if isinstance(x, str) and x])
        pending = list(rset - lset)
        # 简单稳定化：按字母排序，避免批次抖动
        pending.sort()

        # 移除旧的 pending_estimated 缓存写入（已统一到 compute_subscription_metrics）
        # 不再需要写入 Settings 缓存

        return {"ids": pending, "source": "set_diff"}


download_plan_service = DownloadPlanService()
