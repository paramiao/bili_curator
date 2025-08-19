import json
from typing import Dict, List, Set
from loguru import logger
from sqlalchemy.orm import Session

from ..models import Settings


class LocalIndexService:
    """
    本地索引服务（M2骨架）：
    - 现阶段仅从 Settings 读取缓存的本地 bvid 列表：key = local_index:{sid}:bvids -> JSON[list[str]]
    - 若不存在则返回空列表（表示“未知本地情况”，后续由计划层作集合差时自然全量候选）。
    - 后续可扩展：扫描订阅目录、读取 .json/.nfo 缓存、引入 TTL 的索引缓存。
    """

    def scan_local_index(self, db: Session, sid: int) -> List[str]:
        key = f"local_index:{sid}:bvids"
        try:
            s = db.query(Settings).filter(Settings.key == key).first()
            if not s or not s.value:
                logger.debug(f"[local-index] sid={sid} 无缓存，返回空")
                return []
            try:
                arr = json.loads(s.value)
                if isinstance(arr, list):
                    # 仅保留非空字符串
                    return [x for x in arr if isinstance(x, str) and x]
                return []
            except Exception:
                return []
        except Exception:
            return []


local_index_service = LocalIndexService()
