"""
全局请求队列/限流/互斥管理（内存版，最小可用）
- 全局 yt-dlp 并发信号量（可通过环境变量配置）
- 订阅级互斥锁
- 简单任务登记与查询（便于 Web 可视化）

后续可扩展：Cookie/无Cookie双通道、优先级、暂停/恢复、SSE 等。
"""
import asyncio
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Dict, Optional, List, Any
import os

from loguru import logger


# 全局 yt-dlp 并发（默认1，可通过 YTDLP_CONCURRENCY 配置，范围1-4）
def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.getenv(name, str(default)))
        return max(lo, min(hi, v))
    except Exception:
        return default

yt_dlp_semaphore = asyncio.Semaphore(_env_int('YTDLP_CONCURRENCY', 1, 1, 4))

# 分级并发：需要 Cookie 与不需要 Cookie 可分开控制
_sem_cookie = asyncio.Semaphore(1000)      # 大容量，实际并发由 _cap_* 与运行计数控制
_sem_nocookie = asyncio.Semaphore(1000)

# 期望的并发容量（可配置，范围：cookie 1-3；nocookie 1-5）
_cap_cookie = _env_int('QUEUE_CAP_COOKIE', 1, 1, 3)
_cap_nocookie = _env_int('QUEUE_CAP_NOCOOKIE', 2, 1, 5)

# 当前运行计数
_run_cookie = 0
_run_nocookie = 0

# 暂停标志（内存版）
_paused_all = False
_paused_cookie = False
_paused_nocookie = False

# 订阅级互斥
_subscription_locks: Dict[int, asyncio.Lock] = {}
_dedup_keys: Dict[str, str] = {}  # dedup_key -> job_id


def get_subscription_lock(subscription_id: int) -> asyncio.Lock:
    if subscription_id not in _subscription_locks:
        _subscription_locks[subscription_id] = asyncio.Lock()
    return _subscription_locks[subscription_id]


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class RequestJob:
    id: str
    type: str  # expected_total | parse | list_fetch | download | other
    subscription_id: Optional[int]
    requires_cookie: bool
    video_id: Optional[str] = None
    status: str = JobStatus.QUEUED
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    last_error: str = ""
    priority: int = 0
    acquired_scope: Optional[str] = None  # 'cookie' | 'nocookie' | None
    # 诊断字段（内存态）
    wait_cycles: int = 0
    wait_ms: int = 0
    last_wait_reason: str = ""

    # 辅助：计算运行耗时（毫秒）
    def runtime_ms(self) -> int:
        try:
            if self.started_at is None:
                return 0
            end = self.finished_at or datetime.now()
            return int((end - self.started_at).total_seconds() * 1000)
        except Exception:
            return 0


class RequestQueueManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, RequestJob] = {}
        self._order: List[str] = []  # 简单双端队列可扩展为优先级队列
        self._lock = asyncio.Lock()

    # 结构化日志输出
    def _emit(self, event: str, job: Optional[RequestJob], **extra):
        try:
            payload = {
                'event': event,
                'job_id': getattr(job, 'id', None),
                'type': getattr(job, 'type', None),
                'subscription_id': getattr(job, 'subscription_id', None),
                'requires_cookie': getattr(job, 'requires_cookie', None),
                'status': getattr(job, 'status', None),
                'priority': getattr(job, 'priority', None),
                'acquired_scope': getattr(job, 'acquired_scope', None),
                'created_at': getattr(job, 'created_at', None).isoformat() if getattr(job, 'created_at', None) else None,
                'started_at': getattr(job, 'started_at', None).isoformat() if getattr(job, 'started_at', None) else None,
                'finished_at': getattr(job, 'finished_at', None).isoformat() if getattr(job, 'finished_at', None) else None,
                'wait_ms': getattr(job, 'wait_ms', None),
                'run_ms': job.runtime_ms() if job else None,
            }
            if extra:
                payload.update(extra)
            logger.bind(component='request_queue').info(payload)
        except Exception:
            # 降级为普通日志，避免影响主流程
            logger.info(f"queue_event_fallback event={event} job_id={getattr(job, 'id', None)} extra={extra}")

    async def enqueue(self, job_type: str, subscription_id: Optional[int], requires_cookie: bool, priority: Optional[int] = None, dedup_key: Optional[str] = None, video_id: Optional[str] = None) -> str:
        # 计算去重键：默认使用 job_type:subscription_id（若提供）
        key = None
        if dedup_key and isinstance(dedup_key, str) and dedup_key.strip():
            key = dedup_key.strip()
        elif subscription_id is not None:
            key = f"{job_type}:{subscription_id}"

        job_id = str(uuid.uuid4())
        job = RequestJob(id=job_id, type=job_type, subscription_id=subscription_id, requires_cookie=requires_cookie, video_id=video_id)
        job.acquired_scope = None  # 明确初始化
        if priority is not None:
            try:
                job.priority = int(priority)
            except Exception:
                job.priority = 0
        async with self._lock:
            # 基础去重：如已有相同 dedup_key 的任务处于队列或运行中，则直接返回现有 job_id
            if key is not None:
                exist = _dedup_keys.get(key)
                if exist and exist in self._jobs:
                    existing_job = self._jobs[exist]
                    self._emit('enqueue_dedup_hit', existing_job, dedup_key=key)
                    return exist
                # 预占去重键
                _dedup_keys[key] = job_id
            # 正常入队
            self._jobs[job_id] = job
            # 简单策略：有显式优先级则插入队首，否则追加到队尾
            if priority is not None:
                self._order.insert(0, job_id)
            else:
                self._order.append(job_id)
        self._emit('enqueue', job, dedup_key=key)
        return job_id

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self._jobs.get(job_id)
        return asdict(job) if job else None

    def list(self) -> List[Dict[str, Any]]:
        return [asdict(self._jobs[j]) for j in list(self._order)]

    async def mark_running(self, job_id: str):
        """切换为 RUNNING，并根据暂停标志与分级并发控制进行等待与信号量获取。"""
        global _run_cookie, _run_nocookie
        # 在锁外轮询暂停状态，避免长时间持锁
        while True:
            # 读取当前暂停状态
            paused_all = _paused_all
            paused_cookie = _paused_cookie
            paused_nocookie = _paused_nocookie

            async with self._lock:
                job = self._jobs.get(job_id)
                if not job:
                    return
                # 若任务已被取消，直接返回
                if job.status == JobStatus.CANCELED:
                    return

            # 检查暂停条件
            if paused_all or (job.requires_cookie and paused_cookie) or ((not job.requires_cookie) and paused_nocookie):
                reason = 'paused_all' if paused_all else ('paused_cookie' if job.requires_cookie else 'paused_nocookie')
                async with self._lock:
                    job = self._jobs.get(job_id)
                    if job:
                        job.wait_cycles += 1
                        job.last_wait_reason = reason
                await asyncio.sleep(0.2)
                continue

            # 检查容量上限
            if job.requires_cookie:
                if _run_cookie >= _cap_cookie:
                    async with self._lock:
                        job = self._jobs.get(job_id)
                        if job:
                            job.wait_cycles += 1
                            job.last_wait_reason = 'cap_cookie'
                    await asyncio.sleep(0.1)
                    continue
            else:
                if _run_nocookie >= _cap_nocookie:
                    async with self._lock:
                        job = self._jobs.get(job_id)
                        if job:
                            job.wait_cycles += 1
                            job.last_wait_reason = 'cap_nocookie'
                    await asyncio.sleep(0.1)
                    continue

            # 获取对应信号量
            if job.requires_cookie:
                await _sem_cookie.acquire()
                acquired = 'cookie'
            else:
                await _sem_nocookie.acquire()
                acquired = 'nocookie'

            async with self._lock:
                job = self._jobs.get(job_id)
                if not job:
                    # 回滚信号量
                    if acquired == 'cookie':
                        _sem_cookie.release()
                    else:
                        _sem_nocookie.release()
                    return
                # 若在等待期间被取消
                if job.status == JobStatus.CANCELED:
                    if acquired == 'cookie':
                        _sem_cookie.release()
                    else:
                        _sem_nocookie.release()
                    return
                job.status = JobStatus.RUNNING
                job.started_at = datetime.now()
                job.acquired_scope = acquired
                # 记录等待时长（ms）
                try:
                    job.wait_ms = int((job.started_at - job.created_at).total_seconds() * 1000)
                except Exception:
                    job.wait_ms = 0
                # 递增运行计数
                if acquired == 'cookie':
                    _run_cookie += 1
                else:
                    _run_nocookie += 1
                self._emit('start', job, wait_cycles=job.wait_cycles, last_wait_reason=job.last_wait_reason)
            return

    async def mark_done(self, job_id: str):
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.DONE
                job.finished_at = datetime.now()
                acquired = job.acquired_scope
                job.acquired_scope = None
                # 清理去重键
                self._clear_dedup_for(job_id)
        # 在锁外释放信号量并递减计数
        if 'acquired' in locals() and acquired:
            (_sem_cookie if acquired == 'cookie' else _sem_nocookie).release()
            global _run_cookie, _run_nocookie
            if acquired == 'cookie':
                _run_cookie = max(0, _run_cookie - 1)
            else:
                _run_nocookie = max(0, _run_nocookie - 1)
        if 'job' in locals() and job:
            self._emit('finish', job)

    async def mark_failed(self, job_id: str, err: str):
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.FAILED
                job.finished_at = datetime.now()
                job.last_error = err
                acquired = job.acquired_scope
                job.acquired_scope = None
                # 清理去重键
                self._clear_dedup_for(job_id)
        if 'acquired' in locals() and acquired:
            # 释放信号量并递减运行计数，避免运行计数“卡死”
            (_sem_cookie if acquired == 'cookie' else _sem_nocookie).release()
            global _run_cookie, _run_nocookie
            if acquired == 'cookie':
                _run_cookie = max(0, _run_cookie - 1)
            else:
                _run_nocookie = max(0, _run_nocookie - 1)
        if 'job' in locals() and job:
            # 结构化失败日志，尝试推断错误类别
            err_class = 'unknown'
            try:
                s = (err or '').lower()
                if any(k in s for k in ['timeout', 'time out']):
                    err_class = 'timeout'
                elif any(k in s for k in ['forbidden', '403', 'unauthorized', '401', 'permission']):
                    err_class = 'auth'
                elif any(k in s for k in ['not found', '404', 'deleted', 'private']):
                    err_class = 'not_found'
            except Exception:
                pass
            self._emit('fail', job, error=err, error_class=err_class)

    async def remove(self, job_id: str):
        async with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
            if job_id in self._order:
                self._order.remove(job_id)
            # 清理去重键
            self._clear_dedup_for(job_id)

    # 控制操作
    async def cancel(self, job_id: str, reason: str = ""):
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            # 若已完成，直接返回
            if job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELED):
                return True
            job.status = JobStatus.CANCELED
            job.finished_at = datetime.now()
            job.last_error = reason or job.last_error
            acquired = job.acquired_scope
            job.acquired_scope = None
            # 清理去重键
            self._clear_dedup_for(job_id)
        if acquired:
            (_sem_cookie if acquired == 'cookie' else _sem_nocookie).release()
            global _run_cookie, _run_nocookie
            if acquired == 'cookie':
                _run_cookie = max(0, _run_cookie - 1)
            else:
                _run_nocookie = max(0, _run_nocookie - 1)
        if 'job' in locals() and job:
            self._emit('cancel', job, reason=reason)
        return True

    async def prioritize(self, job_id: str, new_priority: Optional[int] = None):
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if new_priority is not None:
                job.priority = int(new_priority)
            # 简化：直接移动到队首，表示最高优先
            if job_id in self._order:
                self._order.remove(job_id)
                self._order.insert(0, job_id)
        return True

    async def pause(self, scope: str = 'all'):
        global _paused_all, _paused_cookie, _paused_nocookie
        if scope == 'all':
            _paused_all = True
        elif scope in ('requires_cookie', 'cookie'):
            _paused_cookie = True
        elif scope in ('no_cookie', 'nocookie'):
            _paused_nocookie = True
        else:
            raise ValueError('unknown scope')

    async def resume(self, scope: str = 'all'):
        global _paused_all, _paused_cookie, _paused_nocookie
        if scope == 'all':
            _paused_all = False
        elif scope in ('requires_cookie', 'cookie'):
            _paused_cookie = False
        elif scope in ('no_cookie', 'nocookie'):
            _paused_nocookie = False
        else:
            raise ValueError('unknown scope')

    def stats(self) -> Dict[str, Any]:
        # 计算可用槽位（不小于0）
        available_cookie = max(0, _cap_cookie - _run_cookie)
        available_nocookie = max(0, _cap_nocookie - _run_nocookie)

        # 分通道排队统计（仅 QUEUED）
        queued_cookie = sum(1 for j in self._jobs.values() if j.status == JobStatus.QUEUED and j.requires_cookie)
        queued_nocookie = sum(1 for j in self._jobs.values() if j.status == JobStatus.QUEUED and not j.requires_cookie)

        return {
            'paused': {
                'all': _paused_all,
                'requires_cookie': _paused_cookie,
                'no_cookie': _paused_nocookie,
            },
            # semaphores 字段仅用于调试，不代表容量
            'semaphores': {
                'cookie_value': _sem_cookie._value if hasattr(_sem_cookie, '_value') else None,
                'no_cookie_value': _sem_nocookie._value if hasattr(_sem_nocookie, '_value') else None,
            },
            'counts': {
                'total': len(self._jobs),
                'queued': sum(1 for j in self._jobs.values() if j.status == JobStatus.QUEUED),
                'running': sum(1 for j in self._jobs.values() if j.status == JobStatus.RUNNING),
                'done': sum(1 for j in self._jobs.values() if j.status == JobStatus.DONE),
                'failed': sum(1 for j in self._jobs.values() if j.status == JobStatus.FAILED),
                'canceled': sum(1 for j in self._jobs.values() if j.status == JobStatus.CANCELED),
            },
            'counts_by_channel': {
                'queued_cookie': queued_cookie,
                'queued_nocookie': queued_nocookie,
            },
            'capacity': {
                # 配置的目标并发
                'requires_cookie': _cap_cookie,
                'no_cookie': _cap_nocookie,
                # 当前运行数
                'running_cookie': _run_cookie,
                'running_nocookie': _run_nocookie,
                # 可用槽位（派生值，便于前端直观展示）
                'available_cookie': available_cookie,
                'available_nocookie': available_nocookie,
            }
        }

    def _clear_dedup_for(self, job_id: str):
        """清理与指定 job 关联的 dedup 键（若存在）"""
        try:
            # 线性扫描（当前任务量有限，足够用；后续可维护反向索引）
            keys_to_del = [k for k, v in _dedup_keys.items() if v == job_id]
            for k in keys_to_del:
                _dedup_keys.pop(k, None)
        except Exception:
            pass

    async def set_capacity(self, requires_cookie: Optional[int] = None, no_cookie: Optional[int] = None):
        global _cap_cookie, _cap_nocookie
        if requires_cookie is not None:
            _cap_cookie = max(0, int(requires_cookie))
        if no_cookie is not None:
            _cap_nocookie = max(0, int(no_cookie))

    async def reap_zombies(self, threshold_minutes: int = 20, target_types: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        僵尸回收：将 RUNNING 且超时的任务标记为 FAILED，并安全释放信号量与运行计数。
        默认仅针对 list_fetch 类型，可通过 target_types 覆盖。
        返回回收统计数据。
        """
        if target_types is None:
            target_types = ['list_fetch']
        now = datetime.now()
        threshold = threshold_minutes * 60
        reaped = []
        async with self._lock:
            for jid in list(self._order):
                job = self._jobs.get(jid)
                if not job:
                    continue
                if job.status != JobStatus.RUNNING:
                    continue
                if job.type not in target_types:
                    continue
                try:
                    started = job.started_at or job.created_at
                    elapsed = (now - started).total_seconds()
                except Exception:
                    elapsed = 0
                if elapsed >= threshold:
                    # 标记失败并准备释放资源
                    job.status = JobStatus.FAILED
                    job.finished_at = datetime.now()
                    job.last_error = f"zombie_reaped: running_timeout_{threshold_minutes}m"
                    acquired = job.acquired_scope
                    job.acquired_scope = None
                    self._clear_dedup_for(jid)
                    reaped.append((job, acquired))
        # 锁外释放信号量与计数，记录日志
        for job, acquired in reaped:
            if acquired == 'cookie':
                try:
                    _sem_cookie.release()
                except Exception:
                    pass
                global _run_cookie
                _run_cookie = max(0, _run_cookie - 1)
            elif acquired == 'nocookie':
                try:
                    _sem_nocookie.release()
                except Exception:
                    pass
                global _run_nocookie
                _run_nocookie = max(0, _run_nocookie - 1)
            self._emit('zombie_reap', job, reason='running_timeout', threshold_minutes=threshold_minutes)
        return {
            'checked': len(self._order),
            'reaped': len(reaped),
            'types': target_types,
            'threshold_minutes': threshold_minutes,
        }


# 全局实例
request_queue = RequestQueueManager()
