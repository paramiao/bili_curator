"""
定时任务调度器 - 使用APScheduler替代Celery
"""
import asyncio
from datetime import datetime, timedelta
import json
import re
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from loguru import logger

from .models import get_db
from .models import Subscription, Settings
from .downloader import downloader
from .services.remote_sync_service import remote_sync_service
from .services.local_index_service import local_index_service
from .services.download_plan_service import download_plan_service
from .queue_manager import request_queue
from .auto_import import auto_import_service
from .cookie_manager import cookie_manager
from .services.subscription_stats import recompute_all_subscriptions
from .models import DownloadTask

def _get_int_setting(db: Session, key: str, default: int) -> int:
    """从 Settings 读取整数配置，读取失败返回默认值。"""
    try:
        s = db.query(Settings).filter(Settings.key == key).first()
        if not s or s.value is None:
            return default
        return int(str(s.value).strip())
    except Exception:
        return default

class SimpleScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.running = False
    
    def start(self):
        """启动调度器"""
        if not self.running:
            self.scheduler.start()
            self.running = True
            logger.info("定时任务调度器已启动")
            
            # 添加默认任务
            self._add_default_jobs()
    
    def stop(self):
        """停止调度器"""
        if self.running:
            self.scheduler.shutdown()
            self.running = False
            logger.info("定时任务调度器已停止")
    
    def _add_default_jobs(self):
        """添加默认定时任务"""
        # 检查订阅更新 - 每30分钟
        self.scheduler.add_job(
            func=self.check_subscriptions,
            trigger=IntervalTrigger(minutes=30),
            id='check_subscriptions',
            replace_existing=True,
            max_instances=1
        )
        
        # 验证Cookie - 每6小时
        self.scheduler.add_job(
            func=self.validate_cookies,
            trigger=IntervalTrigger(hours=6),
            id='validate_cookies',
            replace_existing=True,
            max_instances=1
        )
        
        # 清理旧任务 - 每天凌晨2点
        self.scheduler.add_job(
            func=self.cleanup_old_tasks,
            trigger=CronTrigger(hour=2, minute=0),
            id='cleanup_old_tasks',
            replace_existing=True,
            max_instances=1
        )
        
        # 检查并修正僵尸同步状态 - 每10分钟
        self.scheduler.add_job(
            func=self.check_stale_sync_status,
            trigger=IntervalTrigger(minutes=10),
            id='check_stale_sync_status',
            replace_existing=True,
            max_instances=1
        )

        # 僵尸回收：清理 list_fetch RUNNING 超时任务 - 每5分钟
        self.scheduler.add_job(
            func=self.zombie_reaper,
            trigger=IntervalTrigger(minutes=5),
            id='zombie_reaper',
            replace_existing=True,
            max_instances=1
        )

        # 周期性后台任务：自动导入 + 自动关联 + 统一重算订阅统计 - 间隔可配置
        # 读取 Settings.auto_import_interval_minutes，若无则回退到 Settings.check_interval，再回退 15
        minutes = 15
        db = next(get_db())
        try:
            minutes = _get_int_setting(db, 'auto_import_interval_minutes', minutes)
            if minutes == 15:
                minutes = _get_int_setting(db, 'check_interval', minutes)
        finally:
            db.close()

        # 容错：限制合理区间，防止过于频繁或过于稀疏
        minutes = max(1, min(24 * 60, minutes))
        self.scheduler.add_job(
            func=self.run_auto_import_and_recompute,
            trigger=IntervalTrigger(minutes=minutes),
            id='auto_import_and_recompute',
            replace_existing=True,
            max_instances=2
        )
        logger.info(f"注册周期任务 auto_import_and_recompute，间隔 {minutes} 分钟")
        
        # 新增：入队协调任务（短周期，轻量，只做数据库计算 + 按订阅节流调用下载）
        # 读取 Settings.enqueue_interval_minutes，回退 3 分钟
        enqueue_minutes = 3
        try:
            db = next(get_db())
            try:
                enqueue_minutes = _get_int_setting(db, 'enqueue_interval_minutes', enqueue_minutes)
            finally:
                db.close()
        except Exception:
            enqueue_minutes = 3
        enqueue_minutes = max(1, min(60, enqueue_minutes))
        self.scheduler.add_job(
            func=self.enqueue_coordinator,
            trigger=IntervalTrigger(minutes=enqueue_minutes),
            id='enqueue_coordinator',
            replace_existing=True,
            max_instances=3
        )
        logger.info(f"注册周期任务 enqueue_coordinator，间隔 {enqueue_minutes} 分钟")

        # 周期刷新远端头部快照（M2）：用于驱动增量管线（仅合集订阅）
        try:
            db = next(get_db())
            try:
                refresh_minutes = _get_int_setting(db, 'sync:global:head_refresh_interval_minutes', 60)
            finally:
                db.close()
        except Exception:
            refresh_minutes = 60
        refresh_minutes = max(5, min(24 * 60, refresh_minutes))
        self.scheduler.add_job(
            func=self.refresh_head_snapshots,
            trigger=IntervalTrigger(minutes=refresh_minutes),
            id='refresh_head_snapshots',
            replace_existing=True,
            max_instances=2
        )
        logger.info(f"注册周期任务 refresh_head_snapshots，间隔 {refresh_minutes} 分钟")
        
        logger.info("默认定时任务已添加")

    async def zombie_reaper(self):
        """僵尸回收任务：
        - 读取阈值（Settings: zombie:list_fetch:timeout_minutes，默认20）
        - 回收 RUNNING 超时的 list_fetch 任务
        - 输出结构化日志
        """
        db = next(get_db())
        try:
            try:
                threshold_minutes = _get_int_setting(db, 'zombie:list_fetch:timeout_minutes', 20)
            except Exception:
                threshold_minutes = 20
        finally:
            db.close()
        try:
            stats = await request_queue.reap_zombies(threshold_minutes=threshold_minutes, target_types=['list_fetch'])
            logger.bind(component='zombie_reaper').info({
                'event': 'reaper_run',
                'threshold_minutes': threshold_minutes,
                'stats': stats,
            })
        except Exception as e:
            logger.bind(component='zombie_reaper').error({
                'event': 'reaper_error',
                'error': str(e),
            })

    @staticmethod
    def _is_bvid(vid: str) -> bool:
        """校验是否为合法 BVID（BV 开头 + 10 位字母数字）。"""
        try:
            return bool(vid) and bool(re.match(r'^BV[0-9A-Za-z]{10}$', str(vid)))
        except Exception:
            return False

    @staticmethod
    def _safe_bilibili_url(vid: str) -> str:
        """仅当 vid 为合法 BVID 时返回标准视频页 URL，否则返回 None。"""
        return f"https://www.bilibili.com/video/{vid}" if SimpleScheduler._is_bvid(vid) else None

    async def refresh_head_snapshots(self):
        """周期刷新远端头部快照（仅合集订阅）：
        - 跳过正在 running 的订阅
        - 仅在 head_snapshot 缺失或过期时刷新
        - cap 支持全局/订阅级配置
        """
        logger.info("开始执行周期任务：refresh_head_snapshots")
        db = next(get_db())
        try:
            # 读取阈值配置
            try:
                stale_minutes = _get_int_setting(db, 'sync:global:head_snapshot_stale_minutes', 180)
            except Exception:
                stale_minutes = 180
            stale_minutes = max(15, min(7 * 24 * 60, stale_minutes))
            stale_before = datetime.now() - timedelta(minutes=stale_minutes)

            try:
                default_cap = _get_int_setting(db, 'sync:global:head_cap', 200)
            except Exception:
                default_cap = 200
            default_cap = max(10, min(5000, default_cap))

            # 获取所有活跃合集订阅
            subs = db.query(Subscription).filter(Subscription.is_active == True, Subscription.type == 'collection').all()
            logger.info(f"快照刷新候选订阅：{len(subs)} 个")

            for sub in subs:
                try:
                    status_key = f"sync:{sub.id}:status"
                    head_key = f"sync:{sub.id}:head_snapshot"
                    # 跳过运行中
                    s_status = db.query(Settings).filter(Settings.key == status_key).first()
                    running = False
                    if s_status and s_status.value:
                        try:
                            data = json.loads(s_status.value)
                            running = (isinstance(data, dict) and data.get('status') == 'running')
                        except Exception:
                            running = False
                    if running:
                        logger.debug(f"跳过刷新（running）sid={sub.id}")
                        continue

                    # 判断是否需要刷新：无快照或快照过期
                    s_head = db.query(Settings).filter(Settings.key == head_key).first()
                    need = False
                    if not s_head or not s_head.value:
                        need = True
                    else:
                        try:
                            # 依赖 Settings.updated_at 字段
                            if getattr(s_head, 'updated_at', None) and s_head.updated_at < stale_before:
                                need = True
                        except Exception:
                            need = False
                    if not need:
                        continue

                    # cap：订阅级覆盖全局
                    try:
                        s_cap = db.query(Settings).filter(Settings.key == f"sync:{sub.id}:head_cap").first()
                        cap = default_cap
                        if s_cap and s_cap.value is not None:
                            cap = int(str(s_cap.value).strip())
                            cap = max(10, min(5000, cap))
                    except Exception:
                        cap = default_cap

                    # 执行刷新
                    try:
                        await remote_sync_service.refresh_head_snapshot(db, sub.id, cap=cap, reset_cursor=True)
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        logger.warning(f"订阅 {sub.id} 快照刷新失败：{e}")
                except Exception as ie:
                    logger.debug(f"订阅 {sub.id} 刷新评估异常：{ie}")
            logger.info("周期任务完成：refresh_head_snapshots")
        finally:
            db.close()

    
    async def check_subscriptions(self):
        """检查所有活跃订阅的更新"""
        logger.info("开始检查订阅更新...")
        
        db = next(get_db())
        try:
            # 获取所有活跃订阅
            active_subscriptions = db.query(Subscription).filter(
                Subscription.is_active == True
            ).all()
            
            logger.info(f"发现 {len(active_subscriptions)} 个活跃订阅")
            
            for subscription in active_subscriptions:
                try:
                    await self._process_subscription(subscription, db)
                    
                    # 避免请求过快
                    await asyncio.sleep(10)
                    
                except asyncio.CancelledError:
                    logger.info(f"订阅 {subscription.name} 处理被取消")
                    return
                except Exception as e:
                    logger.error(f"处理订阅 {subscription.name} 失败: {e}")
            
            logger.info("订阅检查完成")
            
        except asyncio.CancelledError:
            logger.info("订阅检查任务被取消")
        except Exception as e:
            logger.error(f"检查订阅时出错: {e}")
        finally:
            db.close()

    async def run_auto_import_and_recompute(self):
        """后台周期任务：自动导入 + 自动关联 + 统一重算（去抖在统计服务内部可选触发）"""
        logger.info("开始周期任务：auto_import + auto_associate + recompute_all_subscriptions")
        try:
            # 1) 扫描并导入（IO阻塞，放到线程池）
            try:
                await asyncio.to_thread(auto_import_service.scan_and_import)
            except Exception as e:
                logger.warning(f"自动导入阶段异常：{e}")

            # 2) 自动关联订阅（IO/DB阻塞，放到线程池）
            try:
                await asyncio.to_thread(auto_import_service.auto_associate_subscriptions)
            except Exception as e:
                logger.warning(f"自动关联阶段异常：{e}")

            # 3) 统一重算所有订阅统计
            db = next(get_db())
            try:
                recompute_all_subscriptions(db, touch_last_check=False)
                db.commit()
            except Exception as e:
                db.rollback()
                logger.warning(f"统一重算订阅统计失败：{e}")
            finally:
                db.close()

            logger.info("周期任务完成：auto_import + auto_associate + recompute_all_subscriptions")
        except Exception as e:
            logger.error(f"周期任务执行失败：{e}")
    
    async def enqueue_coordinator(self):
        """入队协调任务：
        - 轮询启用订阅，调用 downloader.compute_pending_list() 获取待下载视频
        - 每订阅最多入队 Settings.max_enqueue_per_subscription 个视频（默认2）
        - 复用 downloader._download_single_video() 进行入队（内部含全局队列与去重）
        - 轻量延时，避免瞬时突发
        """
        logger.info("开始执行入队协调任务")
        db = next(get_db())
        try:
            # 自恢复锁：防止并发/僵尸占用
            lock_key = 'job:enqueue_coordinator:lock'
            now = datetime.now()
            lock_expire_minutes = 5
            try:
                s_lock = db.query(Settings).filter(Settings.key == lock_key).first()
                locked = False
                if s_lock and s_lock.value:
                    try:
                        from datetime import datetime as _dt
                        last = _dt.fromisoformat(str(s_lock.value).strip())
                        if (now - last) < timedelta(minutes=lock_expire_minutes):
                            locked = True
                    except Exception:
                        locked = False
                if locked:
                    logger.warning("enqueue_coordinator 跳过：检测到未过期的运行锁")
                    return
                # 抢占或建立锁
                if not s_lock:
                    db.add(Settings(key=lock_key, value=now.isoformat(), description='入队协调运行锁'))
                else:
                    s_lock.value = now.isoformat()
                db.commit()
            except Exception as le:
                db.rollback()
                logger.debug(f"入队协调运行锁设置失败，继续尝试执行：{le}")

            # 软超时：整轮时间预算（秒）
            try:
                time_budget_seconds = _get_int_setting(db, 'enqueue_time_budget_seconds', 90)
            except Exception:
                time_budget_seconds = 90
            start_ts = datetime.now()
            try:
                max_per_sub = _get_int_setting(db, 'max_enqueue_per_subscription', 2)
            except Exception:
                max_per_sub = 2
            max_per_sub = max(1, min(20, max_per_sub))

            # 每轮处理的订阅数上限（轮转游标，以降低一次性扫描成本）
            try:
                subs_per_cycle = _get_int_setting(db, 'enqueue_max_subscriptions_per_cycle', 5)
            except Exception:
                subs_per_cycle = 5
            subs_per_cycle = max(1, min(1000, subs_per_cycle))

            active_subs = db.query(Subscription).filter(Subscription.is_active == True).all()
            total_subs = len(active_subs)
            logger.info(f"入队协调：启用订阅 {total_subs} 个，上限/订阅 {max_per_sub}，本轮处理上限 {subs_per_cycle}")

            # 读取与更新轮转游标
            def _get_setting(key: str) -> str:
                try:
                    s = db.query(Settings).filter(Settings.key == key).first()
                    return s.value if s and s.value is not None else None
                except Exception:
                    return None

            def _set_setting(key: str, value: str):
                try:
                    s = db.query(Settings).filter(Settings.key == key).first()
                    if not s:
                        s = Settings(key=key, value=value)
                        db.add(s)
                    else:
                        s.value = value
                    db.commit()
                except Exception:
                    db.rollback()

            cursor_key = 'enqueue_cursor'
            cursor_val = 0
            try:
                sv = _get_setting(cursor_key)
                if sv is not None:
                    cursor_val = int(str(sv).strip() or '0')
            except Exception:
                cursor_val = 0

            # 选择本轮要处理的订阅子集
            selected_subs = []
            if total_subs > 0:
                start = cursor_val % total_subs
                # 线性取 subs_per_cycle 个，环绕
                for i in range(min(subs_per_cycle, total_subs)):
                    idx = (start + i) % total_subs
                    selected_subs.append(active_subs[idx])
                new_cursor = (start + len(selected_subs)) % total_subs
                _set_setting(cursor_key, str(new_cursor))
            else:
                selected_subs = []

            for sub in selected_subs:
                try:
                    # 超时保护：若超出本轮时间预算则提前结束
                    try:
                        if (datetime.now() - start_ts).total_seconds() > max(10, time_budget_seconds):
                            logger.warning(f"入队协调超出时间预算 {time_budget_seconds}s，本轮提前结束")
                            break
                    except Exception:
                        pass
                    if sub.type != 'collection' or not sub.url:
                        continue
                    # 1) 失败回补优先：从 retry 队列取少量入队
                    try:
                        try:
                            retry_per_sub = _get_int_setting(db, 'retry_backfill_per_sub', 3)
                        except Exception:
                            retry_per_sub = 3
                        retry_per_sub = max(0, min(20, retry_per_sub))

                        if retry_per_sub > 0:
                            key = f"retry:{sub.id}:failed_backfill"
                            s = db.query(Settings).filter(Settings.key == key).first()
                            arr = []
                            if s and s.value:
                                try:
                                    arr = json.loads(s.value)
                                    if not isinstance(arr, list):
                                        arr = []
                                except Exception:
                                    arr = []
                            # 先进后出（从尾部取），回补最近失败
                            pick = []
                            for _ in range(min(retry_per_sub, len(arr))):
                                vid = arr.pop()  # 从尾部取一个
                                if isinstance(vid, str) and vid:
                                    pick.append(vid)
                            # 提交回存（提前更新，避免并发重复）
                            try:
                                val = json.dumps(arr, ensure_ascii=False)
                                if s:
                                    s.value = val
                                    s.description = s.description or '失败回补队列'
                                else:
                                    db.add(Settings(key=key, value=val, description='失败回补队列'))
                                db.commit()
                            except Exception:
                                db.rollback()

                            # 实际入队回补项
                            enq_retry = 0
                            for vid in pick:
                                try:
                                    # 过滤永久失败
                                    try:
                                        fkey = f"fail:{vid}"
                                        f = db.query(Settings).filter(Settings.key == fkey).first()
                                        if f and f.value:
                                            data = json.loads(f.value)
                                            if isinstance(data, dict) and (data.get('class') == 'permanent'):
                                                logger.info(f"跳过回补入队（永久失败）: {vid}")
                                                continue
                                    except Exception:
                                        pass
                                    url = SimpleScheduler._safe_bilibili_url(vid)
                                    if not url:
                                        logger.info(f"跳过回补入队（非法视频ID，非BVID）：{vid}")
                                        continue
                                    await downloader._download_single_video({
                                        'id': vid,
                                        'title': vid,
                                        'webpage_url': url,
                                        'url': url,
                                    }, sub.id, db)
                                    enq_retry += 1
                                    await asyncio.sleep(0.1)
                                except Exception as rie:
                                    logger.warning(f"订阅 {sub.id} 回补入队失败 {vid}：{rie}")
                            if enq_retry:
                                logger.info(f"订阅 {sub.id} 回补入队 {enq_retry}/{retry_per_sub}")
                    except Exception as re:
                        logger.debug(f"读取/处理失败回补队列异常：{re}")

                    # 2) 正常待下：优先走增量管线（M1骨架），失败或关闭时回退旧路径
                    use_incremental = False
                    try:
                        # 订阅级覆盖全局级
                        key_sub = f"sync:{sub.id}:enable_incremental"
                        s_sub = db.query(Settings).filter(Settings.key == key_sub).first()
                        if s_sub and (str(s_sub.value).strip() in ('1', 'true', 'True')):
                            use_incremental = True
                        elif s_sub and (str(s_sub.value).strip() in ('0', 'false', 'False')):
                            use_incremental = False
                        else:
                            s_glb = db.query(Settings).filter(Settings.key == 'sync:global:enable_incremental_pipeline').first()
                            use_incremental = bool(s_glb and str(s_glb.value).strip() in ('1', 'true', 'True'))
                    except Exception:
                        use_incremental = False

                    videos = []
                    incremental_ok = False
                    # 统计观测：失败队列长度
                    try:
                        failq_key = f"retry:{sub.id}:failed_backfill"
                        s_failq = db.query(Settings).filter(Settings.key == failq_key).first()
                        arr_len = 0
                        if s_failq and s_failq.value:
                            import json as _json
                            try:
                                _arr = _json.loads(s_failq.value)
                                if isinstance(_arr, list):
                                    arr_len = len(_arr)
                            except Exception:
                                arr_len = 0
                        s_agg = db.query(Settings).filter(Settings.key == f"agg:{sub.id}:fail_queue_size").first()
                        if not s_agg:
                            db.add(Settings(key=f"agg:{sub.id}:fail_queue_size", value=str(arr_len)))
                        else:
                            s_agg.value = str(arr_len)
                        db.commit()
                    except Exception:
                        db.rollback()

                    if use_incremental:
                        try:
                            # 读取每批增量入队限制（默认50）
                            try:
                                batch_limit = _get_int_setting(db, 'sync:global:incremental_batch_limit', 50)
                            except Exception:
                                batch_limit = 50
                            inc = remote_sync_service.get_remote_incremental_ids(db, sub.id, limit=max(1, batch_limit))
                            remote_ids = inc.get('ids', []) or []
                            if remote_ids:
                                local_idx = local_index_service.scan_local_index(db, sub.id)
                                plan = download_plan_service.compute_plan_from_sets(db, sub.id, remote_ids, local_idx)
                                # 过滤永久失败
                                import json as _json
                                ids_filtered = []
                                for vid in plan.get('ids', []):
                                    try:
                                        fkey = f"fail:{vid}"
                                        f = db.query(Settings).filter(Settings.key == fkey).first()
                                        if f and f.value:
                                            data = _json.loads(f.value)
                                            if isinstance(data, dict) and (data.get('class') == 'permanent'):
                                                logger.info(f"跳过增量入队（永久失败）: {vid}")
                                                continue
                                    except Exception:
                                        pass
                                    ids_filtered.append(vid)
                                # 构造 candidates（与旧路径一致的轻量字段）
                                for vid in ids_filtered:
                                    url = SimpleScheduler._safe_bilibili_url(vid)
                                    if not url:
                                        logger.info(f"跳过增量候选（非法视频ID，非BVID）：{vid}")
                                        continue
                                    videos.append({'id': vid, 'title': vid, 'webpage_url': url, 'url': url, 'is_queued': False})
                                incremental_ok = True
                            # 写观测键（移除旧的 pending_estimated 缓存，已统一到 compute_subscription_metrics）
                            try:
                                ts_key = f"agg:{sub.id}:last_incremental_at"
                                s2 = db.query(Settings).filter(Settings.key == ts_key).first()
                                now_iso = datetime.now().isoformat()
                                if not s2:
                                    db.add(Settings(key=ts_key, value=now_iso))
                                else:
                                    s2.value = now_iso
                                db.commit()
                            except Exception:
                                db.rollback()
                        except Exception as iex:
                            incremental_ok = False
                            logger.warning(f"订阅 {sub.id} 增量管线异常，回退旧路径：{iex}")

                    if not incremental_ok:
                        # 回退：compute_pending_list
                        try:
                            pending_info = await downloader.compute_pending_list(sub.id, db)
                            videos = pending_info.get('videos', []) or []
                        except asyncio.CancelledError:
                            logger.info(f"订阅 {sub.id} 任务被取消，跳过处理")
                            return
                        except Exception as e:
                            logger.warning(f"订阅 {sub.id} compute_pending_list 失败: {e}")
                            continue
                        # 写观测：last_full_refresh_at
                        try:
                            ts_key = f"agg:{sub.id}:last_full_refresh_at"
                            s2 = db.query(Settings).filter(Settings.key == ts_key).first()
                            now_iso = datetime.now().isoformat()
                            if not s2:
                                db.add(Settings(key=ts_key, value=now_iso))
                            else:
                                s2.value = now_iso
                            # 移除旧的 pending_estimated 缓存写入（已统一到 compute_subscription_metrics）
                            db.commit()
                        except Exception:
                            db.rollback()

                    # 仅入队未在队列中的视频；考虑回补已占用配额
                    candidates = [v for v in videos if not v.get('is_queued')]
                    logger.info(f"订阅 {sub.id} 候选视频: {len(videos)} 总数, {len(candidates)} 未入队")
                    if not candidates:
                        logger.info(f"订阅 {sub.id} 无候选视频，跳过处理")
                        continue
                    try:
                        remaining = max(0, max_per_sub - (enq_retry if 'enq_retry' in locals() else 0))
                    except Exception:
                        remaining = max_per_sub
                    if remaining <= 0:
                        continue
                    to_enqueue = candidates[:remaining]

                    enq = 0
                    for v in to_enqueue:
                        try:
                            vid = v.get('id')
                            title = v.get('title')
                            url = v.get('webpage_url') or (SimpleScheduler._safe_bilibili_url(vid) if vid else None)
                            if not vid or not url:
                                continue
                            # 过滤永久失败
                            try:
                                fkey = f"fail:{vid}"
                                f = db.query(Settings).filter(Settings.key == fkey).first()
                                if f and f.value:
                                    data = json.loads(f.value)
                                    if isinstance(data, dict) and (data.get('class') == 'permanent'):
                                        logger.info(f"跳过入队（永久失败）: {vid}")
                                        continue
                            except Exception:
                                pass
                            await downloader._download_single_video({
                                'id': vid,
                                'title': title,
                                'webpage_url': url,
                                'url': url,
                            }, sub.id, db)
                            enq += 1
                            await asyncio.sleep(0.1)
                        except asyncio.CancelledError:
                            logger.info(f"订阅 {sub.id} 入队任务被取消")
                            return
                        except Exception as ie:
                            logger.warning(f"订阅 {sub.id} 视频入队失败：{ie}")
                    if enq:
                        logger.info(f"订阅 {sub.id} 入队协调新增 {enq}/剩余{remaining} (总配额{max_per_sub}, 回补{enq_retry if 'enq_retry' in locals() else 0})")
                except Exception as se:
                    logger.warning(f"订阅 {sub.id} 入队协调异常：{se}")

            logger.info("入队协调任务完成")
        except asyncio.CancelledError:
            logger.info("入队协调任务被取消")
        except Exception as e:
            logger.error(f"入队协调任务异常：{e}")
        finally:
            # 释放运行锁
            try:
                s_lock2 = db.query(Settings).filter(Settings.key == 'job:enqueue_coordinator:lock').first()
                if s_lock2:
                    # 清空锁值，避免保留错误时间
                    s_lock2.value = ''
                    db.commit()
            except Exception:
                db.rollback()
            db.close()

    async def _process_subscription(self, subscription: Subscription, db: Session):
        """处理单个订阅"""
        logger.info(f"检查订阅: {subscription.name} ({subscription.type})")
        
        try:
            if subscription.type == 'collection':
                # 处理合集订阅
                result = await downloader.download_collection(subscription.id, db)
                logger.info(f"合集 {subscription.name} 检查完成: {result['new_videos']} 个新视频")
                
            elif subscription.type == 'uploader':
                # 处理UP主订阅
                result = await downloader.download_uploader(subscription.id, db)
                logger.info(f"UP主 {subscription.name} 检查完成: {result['new_videos']} 个新视频")
                
            elif subscription.type == 'keyword':
                # 处理关键词订阅
                result = await downloader.download_keyword(subscription.id, db)
                logger.info(f"关键词 {subscription.name} 检查完成: {result['new_videos']} 个新视频")
                
        except Exception as e:
            logger.error(f"处理订阅 {subscription.name} 失败: {e}")
    
    async def validate_cookies(self):
        """验证所有Cookie的有效性"""
        logger.info("开始验证Cookie...")
        
        db = next(get_db())
        try:
            await cookie_manager.batch_validate_cookies(db)
            logger.info("Cookie验证完成")
        except Exception as e:
            logger.error(f"验证Cookie时出错: {e}")
        finally:
            db.close()
    
    async def cleanup_old_tasks(self):
        """清理超过30天的已完成任务"""
        logger.info("开始清理旧任务...")
        
        db = next(get_db())
        try:
            # 删除30天前的已完成任务
            cutoff_date = datetime.now() - timedelta(days=30)
            
            old_tasks = db.query(DownloadTask).filter(
                DownloadTask.status.in_(['completed', 'failed']),
                DownloadTask.completed_at < cutoff_date
            ).all()
            
            for task in old_tasks:
                db.delete(task)
            
            db.commit()
            logger.info(f"清理了 {len(old_tasks)} 个旧任务")
            
        except Exception as e:
            logger.error(f"清理旧任务时出错: {e}")
            db.rollback()
        finally:
            db.close()
    
    async def check_stale_sync_status(self):
        """检查并清理过期的同步状态"""
        logger.debug("开始检查过期同步状态...")
        
        db = next(get_db())
        try:
            import json
            # 查找超过30分钟仍为 running 的状态
            stale_threshold = datetime.now() - timedelta(minutes=30)
            
            stale_settings = db.query(Settings).filter(
                Settings.key.like('sync:%:status'),
                Settings.updated_at < stale_threshold
            ).all()
            
            healed_count = 0
            for setting in stale_settings:
                try:
                    data = json.loads(setting.value)
                    if data.get('status') == 'running':
                        # 标记为失败状态
                        data['status'] = 'failed'
                        data['error'] = 'Process timeout or crashed'
                        data['completed_at'] = datetime.now().isoformat()
                        setting.value = json.dumps(data, ensure_ascii=False)
                        healed_count += 1
                        
                except json.JSONDecodeError:
                    continue
                    
            if healed_count > 0:
                db.commit()
                logger.info(f"修正了 {healed_count} 个过期的同步状态")
            else:
                logger.debug("未发现需要修正的过期同步状态")
                
        except Exception as e:
            logger.error(f"检查过期同步状态时出错: {e}")
            db.rollback()
        finally:
            db.close()
    
    def add_custom_job(self, func, trigger, job_id: str, **kwargs):
        """添加自定义任务"""
        self.scheduler.add_job(
            func=func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            **kwargs
        )
        logger.info(f"添加自定义任务: {job_id}")
    
    def remove_job(self, job_id: str):
        """移除任务"""
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"移除任务: {job_id}")
        except Exception as e:
            logger.warning(f"移除任务 {job_id} 失败: {e}")
    
    def get_jobs(self):
        """获取所有任务信息"""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'name': job.name or job.id,
                'next_run': job.next_run_time.isoformat() if job.next_run_time else None,
                'trigger': str(job.trigger)
            })
        return jobs
    
    def update_subscription_check_interval(self, minutes: int):
        """更新订阅检查间隔"""
        self.scheduler.modify_job(
            'check_subscriptions',
            trigger=IntervalTrigger(minutes=minutes)
        )
        logger.info(f"订阅检查间隔已更新为 {minutes} 分钟")

# 全局调度器实例
scheduler = SimpleScheduler()

class TaskManager:
    """任务管理器 - 管理手动触发的任务"""
    
    def __init__(self):
        self.running_tasks = {}
    
    async def start_download_task(self, subscription_id: int) -> str:
        """启动下载任务"""
        task_id = f"manual_download_{subscription_id}_{datetime.now().timestamp()}"
        
        if task_id in self.running_tasks:
            raise ValueError("任务已在运行中")
        
        # 创建异步任务
        task = asyncio.create_task(self._run_download_task(subscription_id, task_id))
        self.running_tasks[task_id] = {
            'task': task,
            'subscription_id': subscription_id,
            'started_at': datetime.now(),
            'status': 'running'
        }
        
        logger.info(f"启动手动下载任务: {task_id}")
        return task_id
    
    async def _run_download_task(self, subscription_id: int, task_id: str):
        """运行下载任务"""
        try:
            db = next(get_db())
            try:
                result = await downloader.download_collection(subscription_id, db)
                
                # 更新任务状态
                if task_id in self.running_tasks:
                    self.running_tasks[task_id]['status'] = 'completed'
                    self.running_tasks[task_id]['result'] = result
                    self.running_tasks[task_id]['completed_at'] = datetime.now()
                
                logger.info(f"手动下载任务完成: {task_id}")
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"手动下载任务失败: {task_id} - {e}")
            
            # 更新任务状态
            if task_id in self.running_tasks:
                self.running_tasks[task_id]['status'] = 'failed'
                self.running_tasks[task_id]['error'] = str(e)
                self.running_tasks[task_id]['completed_at'] = datetime.now()
    
    def get_task_status(self, task_id: str) -> dict:
        """获取任务状态"""
        if task_id not in self.running_tasks:
            return {'status': 'not_found'}
        
        task_info = self.running_tasks[task_id].copy()
        task_info.pop('task', None)  # 移除asyncio.Task对象
        
        return task_info
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        if task_id not in self.running_tasks:
            return False
        
        task_info = self.running_tasks[task_id]
        if task_info['status'] == 'running':
            task_info['task'].cancel()
            task_info['status'] = 'cancelled'
            task_info['completed_at'] = datetime.now()
            logger.info(f"取消任务: {task_id}")
            return True
        
        return False
    
    def cleanup_completed_tasks(self):
        """清理已完成的任务"""
        completed_tasks = [
            task_id for task_id, info in self.running_tasks.items()
            if info['status'] in ['completed', 'failed', 'cancelled']
        ]
        
        for task_id in completed_tasks:
            if task_id in self.running_tasks:
                # 保留最近1小时的任务记录
                completed_at = self.running_tasks[task_id].get('completed_at')
                if completed_at and (datetime.now() - completed_at).total_seconds() > 3600:
                    del self.running_tasks[task_id]
    
    def get_all_tasks(self) -> dict:
        """获取所有任务状态"""
        self.cleanup_completed_tasks()
        
        tasks = {}
        for task_id, info in self.running_tasks.items():
            task_copy = info.copy()
            task_copy.pop('task', None)  # 移除asyncio.Task对象
            tasks[task_id] = task_copy
        
        return tasks

# 全局任务管理器实例
task_manager = TaskManager()
