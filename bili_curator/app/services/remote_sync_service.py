from datetime import datetime
import json
from typing import Dict, List, Optional
from loguru import logger
from sqlalchemy.orm import Session

from ..models import Settings, Subscription
from ..downloader import downloader
from .remote_total_store import write_remote_total


class RemoteSyncService:
    """
    远端增量同步服务（M2骨架，不出网）：
    - 基于 Settings 中的 head_snapshot/last_cursor 推导一小批增量 IDs。
    - 若未配置 head_snapshot，则返回空，调用方应回退旧路径。
    - 同步维护 remote_total_cached 与 last_cursor。
    键约定：
      sync:{sid}:head_snapshot -> JSON[list[str]]
      sync:{sid}:last_cursor   -> JSON{"last_seen": "BVxx", "updated_at": "ISO"}
      sync:{sid}:remote_total_cached -> "<int>"
    """

    def _get_setting(self, db: Session, key: str) -> Optional[str]:
        try:
            s = db.query(Settings).filter(Settings.key == key).first()
            return s.value if s and (s.value is not None) else None
        except Exception:
            return None

    def _set_setting(self, db: Session, key: str, value: str, description: Optional[str] = None):
        try:
            s = db.query(Settings).filter(Settings.key == key).first()
            if not s:
                s = Settings(key=key, value=value, description=description)
                db.add(s)
            else:
                s.value = value
                if description and not s.description:
                    s.description = description
            db.commit()
        except Exception:
            db.rollback()

    def get_remote_incremental_ids(self, db: Session, sid: int, limit: int = 50) -> Dict:
        """
        返回增量ID小批次：{"ids": [..], "source": "head_snapshot"}
        策略：
        - 读取 head_snapshot（list[str]），若无 → 返回空。
        - 根据 last_cursor.last_seen 在 head_snapshot 中的位置+1 继续，取 limit 个；若 last_seen 不在列表中，则从头开始。
        - 更新 last_cursor 为本轮取到的最后一个；同步写入 remote_total_cached=len(head_snapshot)。
        """
        limit = max(1, min(500, int(limit or 50)))
        key_head = f"sync:{sid}:head_snapshot"
        key_cursor = f"sync:{sid}:last_cursor"
        key_total = f"sync:{sid}:remote_total_cached"

        head_raw = self._get_setting(db, key_head)
        if not head_raw:
            logger.debug(f"[inc] sid={sid} 无 head_snapshot，返回空以触发回退")
            return {"ids": [], "source": "none"}
        try:
            head = json.loads(head_raw)
            if not isinstance(head, list):
                head = []
        except Exception:
            head = []
        if not head:
            return {"ids": [], "source": "head_snapshot"}

        # 统一缓存：同时更新新旧缓存系统
        try:
            # 更新新系统缓存
            self._set_setting(db, key_total, str(len(head)), description="远端总数缓存（基于快照）")
            
            # 同步更新旧系统缓存，确保数据一致性
            sub = db.query(Subscription).filter(Subscription.id == sid).first()
            if sub:
                write_remote_total(db, sid, len(head), sub.url)
                logger.debug(f"[sync] 统一缓存更新: sid={sid}, total={len(head)}")
        except Exception as e:
            logger.warning(f"[sync] 缓存统一更新失败: sid={sid}, error={e}")
            pass

        # 定位 last_seen
        last_seen = None
        try:
            cur_raw = self._get_setting(db, key_cursor)
            if cur_raw:
                cur = json.loads(cur_raw)
                if isinstance(cur, dict):
                    last_seen = cur.get("last_seen")
        except Exception:
            last_seen = None

        start_idx = 0
        if last_seen and last_seen in head:
            try:
                start_idx = head.index(last_seen) + 1
            except ValueError:
                start_idx = 0
        ids = [x for x in head[start_idx:start_idx + limit] if isinstance(x, str) and x]

        # 更新 last_cursor
        try:
            new_last = ids[-1] if ids else last_seen
            val = json.dumps({
                "last_seen": new_last,
                "updated_at": datetime.now().isoformat()
            }, ensure_ascii=False)
            self._set_setting(db, key_cursor, val, description="增量游标")
        except Exception:
            pass

        return {"ids": ids, "source": "head_snapshot"}

    def update_head_snapshot(self, db: Session, sid: int, head_ids: List[str]) -> None:
        """写入/更新 head_snapshot，并同步 remote_total_cached。"""
        try:
            # 规范化并去重，保持输入顺序
            seen = set()
            norm: List[str] = []
            for x in head_ids or []:
                s = str(x).strip()
                if not s or s in seen:
                    continue
                seen.add(s)
                norm.append(s)
            key_head = f"sync:{sid}:head_snapshot"
            self._set_setting(db, key_head, json.dumps(norm, ensure_ascii=False), description="增量头部ID快照")
            # 统一缓存：同时更新新旧缓存系统
            key_total = f"sync:{sid}:remote_total_cached"
            self._set_setting(db, key_total, str(len(norm)), description="远端总数缓存（基于快照）")
            
            # 同步更新旧系统缓存，确保数据一致性
            sub = db.query(Subscription).filter(Subscription.id == sid).first()
            if sub:
                write_remote_total(db, sid, len(norm), sub.url)
                logger.debug(f"[sync] head_snapshot更新统一缓存: sid={sid}, total={len(norm)}")
        except Exception:
            db.rollback()

    def set_last_cursor(self, db: Session, sid: int, last_seen: Optional[str]) -> None:
        """设置/重置增量游标。last_seen=None 表示从快照起点开始。"""
        try:
            payload = {
                "last_seen": (str(last_seen) if last_seen else None),
                "updated_at": datetime.now().isoformat(),
            }
            key_cursor = f"sync:{sid}:last_cursor"
            self._set_setting(db, key_cursor, json.dumps(payload, ensure_ascii=False), description="增量游标")
        except Exception:
            db.rollback()

    async def refresh_head_snapshot(self, db: Session, sid: int, cap: int = 200, reset_cursor: bool = True) -> Dict:
        """
        从远端抓取前 cap 个视频 ID，写入 head_snapshot，并同步 remote_total_cached；可选重置 last_cursor。
        仅支持合集订阅（type=collection）。写入状态键 sync:{sid}:status。
        返回：{ ok, size, remote_total, updated_at }
        """
        # 读取订阅
        sub = db.query(Subscription).filter(Subscription.id == sid).first()
        if not sub:
            raise ValueError(f"订阅 {sid} 不存在")
        if sub.type != 'collection' or not sub.url:
            raise ValueError("仅支持合集订阅且需要有效URL")

        # 标记 running
        status_key = f"sync:{sid}:status"
        try:
            sdata = {"status": "running", "updated_at": datetime.now().isoformat()}
            self._set_setting(db, status_key, json.dumps(sdata, ensure_ascii=False), description="订阅同步状态")
        except Exception:
            pass

        # 抓取远端列表（禁用增量以拿到准确总数）
        try:
            videos = await downloader._get_collection_videos(sub.url, db, subscription_id=sid, disable_incremental=True)
            # 提取前 cap 个 ID
            cap = max(1, min(5000, int(cap or 200)))
            ids: List[str] = []
            seen = set()
            for it in videos:
                try:
                    vid = it.get('id')
                    if vid is None:
                        continue
                    svid = str(vid)
                    if not svid or svid in seen:
                        continue
                    seen.add(svid)
                    ids.append(svid)
                    if len(ids) >= cap:
                        break
                except Exception:
                    continue
            # 写入 head_snapshot（内部会统一更新缓存）
            self.update_head_snapshot(db, sid, ids)
            
            # 确保远端总数与快照长度一致（避免不同步）
            remote_total = len(videos)
            if remote_total != len(ids):
                logger.warning(f"[sync] 远端总数({remote_total})与快照长度({len(ids)})不一致，以快照为准")
                remote_total = len(ids)
            # 写入完成状态
            try:
                payload = {
                    "status": "idle",
                    "updated_at": datetime.now().isoformat(),
                    "last_sync_total": len(ids),
                    "cache_unified": True,
                    "head_size": len(ids),
                }
                self._set_setting(db, status_key, json.dumps(payload, ensure_ascii=False), description="订阅同步状态")
            except Exception:
                pass
            return {"ok": True, "size": len(ids), "remote_total": len(videos), "updated_at": datetime.now().isoformat()}
        except Exception as e:
            # 失败状态
            try:
                payload = {
                    "status": "failed",
                    "error": str(e),
                    "updated_at": datetime.now().isoformat(),
                }
                self._set_setting(db, status_key, json.dumps(payload, ensure_ascii=False), description="订阅同步状态")
            except Exception:
                pass
            raise


    async def refresh_subscription_snapshot(self, subscription_id: int, db: Session, cap: int = 200, reset_cursor: bool = True):
        """刷新订阅快照，支持所有订阅类型"""
        from ..models import Subscription
        
        subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not subscription:
            raise ValueError(f"订阅 {subscription_id} 不存在")
        
        # 只有合集订阅才支持head_snapshot机制
        if subscription.type == 'collection':
            return await self.refresh_head_snapshot(subscription_id, db, cap, reset_cursor)
        else:
            # UP主和关键词订阅暂时跳过快照刷新
            logger.info(f"订阅 {subscription_id} ({subscription.type}) 暂不支持快照刷新")
            return {"ok": True, "message": f"{subscription.type} 订阅暂不支持快照刷新"}


remote_sync_service = RemoteSyncService()
