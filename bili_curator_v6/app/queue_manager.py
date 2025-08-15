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


class RequestQueueManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, RequestJob] = {}
        self._order: List[str] = []  # 简单双端队列可扩展为优先级队列
        self._lock = asyncio.Lock()

    async def enqueue(self, job_type: str, subscription_id: Optional[int], requires_cookie: bool, priority: Optional[int] = None) -> str:
        job_id = str(uuid.uuid4())
        job = RequestJob(id=job_id, type=job_type, subscription_id=subscription_id, requires_cookie=requires_cookie)
        if priority is not None:
            try:
                job.priority = int(priority)
            except Exception:
                job.priority = 0
        async with self._lock:
            self._jobs[job_id] = job
            # 简单策略：有显式优先级则插入队首，否则追加到队尾
            if priority is not None:
                self._order.insert(0, job_id)
            else:
                self._order.append(job_id)
        logger.info(f"入队: {job.type} sid={subscription_id} id={job_id} prio={job.priority}")
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
                logger.info(f"开始执行: {job.type} sid={job.subscription_id} id={job_id} scope={acquired} wait_ms={job.wait_ms} cycles={job.wait_cycles} reason={job.last_wait_reason}")
            return

    async def mark_done(self, job_id: str):
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.DONE
                job.finished_at = datetime.now()
                acquired = job.acquired_scope
                job.acquired_scope = None
        # 在锁外释放信号量并递减计数
        if 'acquired' in locals() and acquired:
            (_sem_cookie if acquired == 'cookie' else _sem_nocookie).release()
            global _run_cookie, _run_nocookie
            if acquired == 'cookie':
                _run_cookie = max(0, _run_cookie - 1)
            else:
                _run_nocookie = max(0, _run_nocookie - 1)

    async def mark_failed(self, job_id: str, err: str):
        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.FAILED
                job.finished_at = datetime.now()
                job.last_error = err
                acquired = job.acquired_scope
                job.acquired_scope = None
        if 'acquired' in locals() and acquired:
            # 释放信号量并递减运行计数，避免运行计数“卡死”
            (_sem_cookie if acquired == 'cookie' else _sem_nocookie).release()
            global _run_cookie, _run_nocookie
            if acquired == 'cookie':
                _run_cookie = max(0, _run_cookie - 1)
            else:
                _run_nocookie = max(0, _run_nocookie - 1)

    async def remove(self, job_id: str):
        async with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
            if job_id in self._order:
                self._order.remove(job_id)

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
        if acquired:
            (_sem_cookie if acquired == 'cookie' else _sem_nocookie).release()
            global _run_cookie, _run_nocookie
            if acquired == 'cookie':
                _run_cookie = max(0, _run_cookie - 1)
            else:
                _run_nocookie = max(0, _run_nocookie - 1)
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

    async def set_capacity(self, requires_cookie: Optional[int] = None, no_cookie: Optional[int] = None):
        global _cap_cookie, _cap_nocookie
        if requires_cookie is not None:
            _cap_cookie = max(0, int(requires_cookie))
        if no_cookie is not None:
            _cap_nocookie = max(0, int(no_cookie))


# 全局实例
request_queue = RequestQueueManager()
