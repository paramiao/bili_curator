"""
V6下载核心 - 基于V5版本改进
"""
import asyncio
import json
import os
import re
import subprocess
import random
from datetime import datetime
import tempfile
import os
from sqlalchemy.exc import IntegrityError
from pathlib import Path
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from loguru import logger
import subprocess

from .models import Video, DownloadTask, Subscription, Settings, get_db
from .cookie_manager import cookie_manager, rate_limiter, simple_retry
from .queue_manager import yt_dlp_semaphore, get_subscription_lock, request_queue
from .services.subscription_stats import recompute_subscription_stats
from .services.http_utils import get_user_agent



class BilibiliDownloaderV6:
    def __init__(self, output_dir: str = None):
        # 从环境变量获取下载路径，默认为/app/downloads
        if output_dir is None:
            output_dir = os.getenv('DOWNLOAD_PATH', '/app/downloads')
        
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # 每订阅并发（可通过环境变量 PER_SUB_DOWNLOADS 配置，范围1-3）
        def _env_int(name: str, default: int, lo: int, hi: int) -> int:
            try:
                v = int(os.getenv(name, str(default)))
                return max(lo, min(hi, v))
            except Exception:
                return default
        self.concurrent_downloads = _env_int('PER_SUB_DOWNLOADS', 1, 1, 3)
        self.download_semaphore = asyncio.Semaphore(self.concurrent_downloads)
        # 相邻视频下载的休眠区间（秒），可通过 INTER_DOWNLOAD_DELAY_MIN/MAX 配置
        try:
            dmin = float(os.getenv('INTER_DOWNLOAD_DELAY_MIN', '5'))
            dmax = float(os.getenv('INTER_DOWNLOAD_DELAY_MAX', '10'))
            if dmin > dmax:
                dmin, dmax = dmax, dmin
            self.delay_min = max(0.0, dmin)
            self.delay_max = max(self.delay_min, dmax)
        except Exception:
            self.delay_min, self.delay_max = 5.0, 10.0
        # list_fetch 可调参数（分页与退避）
        try:
            lc = int(os.getenv('LIST_CHUNK_SIZE', '50'))
            self.list_chunk_size = max(10, min(200, lc))
        except Exception:
            self.list_chunk_size = 50
        try:
            lr = int(os.getenv('LIST_RETRY', '3'))
            self.list_retry = max(0, min(5, lr))
        except Exception:
            self.list_retry = 3
        try:
            bmin = float(os.getenv('LIST_BACKOFF_MIN', '2'))
            bmax = float(os.getenv('LIST_BACKOFF_MAX', '5'))
            if bmin > bmax:
                bmin, bmax = bmax, bmin
            self.list_backoff_min = max(0.0, bmin)
            self.list_backoff_max = max(self.list_backoff_min, bmax)
        except Exception:
            self.list_backoff_min, self.list_backoff_max = 2.0, 5.0
        try:
            pgmin = float(os.getenv('LIST_PAGE_GAP_MIN', '3'))
            pgmax = float(os.getenv('LIST_PAGE_GAP_MAX', '6'))
            if pgmin > pgmax:
                pgmin, pgmax = pgmax, pgmin
            self.list_page_gap_min = max(0.0, pgmin)
            self.list_page_gap_max = max(self.list_page_gap_min, pgmax)
        except Exception:
            self.list_page_gap_min, self.list_page_gap_max = 3.0, 6.0
        self.list_max_chunks = _env_int('LIST_MAX_CHUNKS', 200, 10, 1000) # 分页抓取最大块数

        # yt-dlp 进程超时（秒）
        self.list_fetch_cmd_timeout = _env_int('LIST_FETCH_CMD_TIMEOUT', 300, 10, 600)  # 增加到5分钟
        self.download_cmd_timeout = _env_int('DOWNLOAD_CMD_TIMEOUT', 1800, 60, 7200)
        self.meta_cmd_timeout = _env_int('META_CMD_TIMEOUT', 60, 10, 300)
        
    @staticmethod
    def _is_bvid(vid: str) -> bool:
        """校验是否为合法 BVID（BV 开头 + 10 位字母数字）。"""
        try:
            return bool(vid) and bool(re.match(r'^BV[0-9A-Za-z]{10}$', str(vid)))
        except Exception:
            return False

    @staticmethod
    def _safe_bilibili_url(vid: Optional[str]) -> Optional[str]:
        """仅当 vid 为合法 BVID 时返回标准视频页 URL，否则返回 None。"""
        if not vid:
            return None
        return f"https://www.bilibili.com/video/{vid}" if BilibiliDownloaderV6._is_bvid(vid) else None

    async def download_collection(self, subscription_id: int, db: Session) -> Dict[str, Any]:
        """下载合集"""
        subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not subscription:
            raise ValueError(f"订阅 {subscription_id} 不存在")
        
        logger.info(f"开始下载合集: {subscription.name}")

        # 同步状态：标记本次订阅同步开始（用于UI显示“周期更新/同步中”）
        try:
            self._set_sync_status(db, subscription_id, status='running', extra={
                'name': subscription.name,
                'type': subscription.type,
                'started_at': datetime.now().isoformat()
            })
            db.commit()
        except Exception as e:
            logger.debug(f"设置同步状态失败（开始）: {e}")

        # 订阅目录（先计算，便于缓存列表）
        subscription_dir = Path(self._create_subscription_directory(subscription))

        # 获取合集视频列表（失败时回退到本地缓存）
        # 同一订阅加互斥，避免与 expected-total/parse 并发外网请求
        sub_lock = get_subscription_lock(subscription.id)
        cache_path = subscription_dir / 'playlist.json'
        try:
            async with sub_lock:
                # 禁用增量提前停止，确保抓到完整列表，避免写回较小的remote_total（如30）
                video_list = await self._get_collection_videos(subscription.url, db, subscription_id=subscription.id, disable_incremental=True)
            try:
                with open(cache_path, 'w', encoding='utf-8') as cf:
                    json.dump(video_list, cf, ensure_ascii=False)
            except Exception as ce:
                logger.warning(f"写入列表缓存失败: {ce}")
        except Exception as e:
            logger.warning(f"实时获取视频列表失败，尝试使用缓存: {e}")
            if cache_path.exists():
                try:
                    with open(cache_path, 'r', encoding='utf-8') as cf:
                        video_list = json.load(cf)
                    logger.info(f"使用缓存视频列表，共 {len(video_list)} 条")
                except Exception as re:
                    logger.error(f"读取缓存失败: {re}")
                    raise
            else:
                raise
        
        # 高性能比对：针对大合集优化的智能查询策略
        remote_video_count = len([vi for vi in video_list if vi.get('id')])
        
        # 智能查询：统一使用批量查询确保数据一致性
        remote_ids = [vi.get('id') for vi in video_list if vi.get('id')]
        if remote_video_count > 100:
            logger.info(f"大合集模式：远端 {remote_video_count} 个视频，使用批量查询优化")
            existing_videos = self._batch_check_existing(db, remote_ids, subscription_id=subscription_id, subscription_dir=subscription_dir)
        else:
            logger.info(f"标准模式：远端 {remote_video_count} 个视频，使用批量查询确保一致性")
            existing_videos = self._batch_check_existing(db, remote_ids, subscription_id=subscription_id, subscription_dir=subscription_dir)
        
        existing_ids = set(existing_videos.keys())
        
        # 保持远端顺序的新视频过滤（O(n)时间复杂度）
        new_videos = [vi for vi in video_list if (vid := vi.get('id')) and vid not in existing_ids]
        
        logger.info(f"远端视频: {remote_video_count}, 本地已有: {len(existing_ids)}, 待下载: {len(new_videos)}")
        # 同步状态：更新一次汇总数据，便于UI计算“剩余待下”
        try:
            self._set_sync_status(db, subscription_id, status='running', extra={
                'remote_total': remote_video_count,
                'existing': len(existing_ids),
                'pending': len(new_videos),
                'updated_at': datetime.now().isoformat()
            })
            db.commit()
        except Exception as e:
            logger.debug(f"设置同步状态失败（中间）: {e}")
        
        logger.info(f"发现 {len(new_videos)} 个新视频需要下载")
        
        # 创建下载任务
        download_results = []
        for idx, video_info in enumerate(new_videos, start=1):
            try:
                result = await self._download_single_video(video_info, subscription_id, db)
                download_results.append(result)
            except Exception as e:
                logger.error(f"下载视频 {video_info.get('title', 'Unknown')} 失败: {e}")
                download_results.append({
                    'video_id': video_info.get('id'),
                    'success': False,
                    'error': str(e)
                })
            # 视频之间随机延时（可配置），降低风控几率
            if idx < len(new_videos):
                delay = random.uniform(self.delay_min, self.delay_max)
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    logger.info("下载任务被取消")
                    raise
        
        # 更新订阅检查时间并刷新统计
        subscription.last_check = datetime.now()
        try:
            recompute_subscription_stats(db, subscription_id, touch_last_check=False)
        except Exception as e:
            logger.warning(f"刷新订阅统计失败(下载完成后)：{e}")
        db.commit()
        # 同步状态：标记完成
        try:
            self._set_sync_status(db, subscription_id, status='completed', extra={
                'completed_at': datetime.now().isoformat(),
                'remote_total': remote_video_count,
                'new_downloads': len(new_videos)
            })
            db.commit()
        except Exception as e:
            logger.debug(f"设置同步状态失败（完成）: {e}")
        
        return {
            'subscription_id': subscription_id,
            'total_videos': len(video_list),
            'new_videos': len(new_videos),
            'download_results': download_results
        }
    

    async def compute_pending_list(self, subscription_id: int, db: Session) -> Dict[str, Any]:
        """计算远端-本地的差值列表（不触发下载）。
        返回：{ subscription_id, remote_total, existing, pending, videos: [ {id,title,webpage_url} ] }
        仅对 type=collection 有效。
        """
        subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not subscription:
            raise ValueError(f"订阅 {subscription_id} 不存在")
        if subscription.type != 'collection' or not subscription.url:
            raise ValueError("仅支持合集订阅或订阅缺少URL")

        # 订阅目录，便于现有去重路径使用
        subscription_dir = Path(self._create_subscription_directory(subscription))

        # 获取远端视频列表（与下载路径一致，但不创建任何下载任务）
        sub_lock = get_subscription_lock(subscription.id)
        async with sub_lock:
            # 禁用增量提前停止，确保远端总数准确
            video_list = await self._get_collection_videos(subscription.url, db, subscription_id=subscription.id, disable_incremental=True)

        remote_video_count = len([vi for vi in video_list if vi.get('id')])

        # 统一使用批量检查策略确保数据一致性
        remote_ids = [vi.get('id') for vi in video_list if vi.get('id')]
        if remote_video_count > 100:
            logger.info(f"大合集模式：远端 {remote_video_count} 个视频，使用批量查询优化")
            existing_videos = self._batch_check_existing(db, remote_ids, subscription_id=subscription.id, subscription_dir=subscription_dir)
        else:
            logger.info(f"标准模式：远端 {remote_video_count} 个视频，使用批量查询确保一致性")
            existing_videos = self._batch_check_existing(db, remote_ids, subscription_id=subscription.id, subscription_dir=subscription_dir)

        existing_ids = set(existing_videos.keys())
        new_videos_full = [vi for vi in video_list if (vid := vi.get('id')) and vid not in existing_ids]

        # 精简返回字段
        def compact(vi: Dict[str, Any]) -> Dict[str, Any]:
            return {
                'id': vi.get('id'),
                'title': vi.get('title') or vi.get('fulltitle') or vi.get('alt_title') or '',
                'webpage_url': vi.get('webpage_url') or vi.get('url') or ''
            }

        pending_list = [compact(vi) for vi in new_videos_full]

        # 结合全局请求队列，标记哪些视频已经在下载队列中（queued/running）
        try:
            jobs = request_queue.list()
            queued_ids = {
                j.get('video_id')
                for j in jobs
                if j.get('type') == 'download'
                and j.get('subscription_id') == subscription_id
                and j.get('status') in ('queued', 'running')
                and j.get('video_id')
            }
        except Exception:
            queued_ids = set()

        for it in pending_list:
            try:
                it['is_queued'] = bool(it.get('id') in queued_ids)
            except Exception:
                it['is_queued'] = False

        # 同步状态中写入一次统计，供前端 UI 使用（不改变状态机）
        try:
            self._set_sync_status(db, subscription_id, status='running', extra={
                'remote_total': remote_video_count,
                'existing': len(existing_ids),
                'pending': len(pending_list),
                'updated_at': datetime.now().isoformat()
            })
            db.commit()
        except Exception:
            pass

        # 确保最终状态回落为 idle，避免 UI 长期显示 running
        try:
            self._set_sync_status(db, subscription_id, status='idle', extra={
                'remote_total': remote_video_count,
                'existing': len(existing_ids),
                'pending': len(pending_list),
                'updated_at': datetime.now().isoformat()
            })
            db.commit()
        except Exception:
            pass

        return {
            'subscription_id': subscription_id,
            'remote_total': remote_video_count,
            'existing': len(existing_ids),
            'pending': len(pending_list),
            'videos': pending_list,
        }

    async def download_uploader(self, subscription_id: int, db: Session) -> Dict[str, Any]:
        """下载UP主订阅"""
        subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not subscription:
            raise ValueError(f"订阅 {subscription_id} 不存在")
        
        if not subscription.uploader_id:
            raise ValueError(f"UP主订阅 {subscription_id} 缺少uploader_id")
        
        logger.info(f"开始下载UP主: {subscription.name} (ID: {subscription.uploader_id})")

        # 同步状态：标记开始
        try:
            self._set_sync_status(db, subscription_id, status='running', extra={
                'name': subscription.name,
                'type': subscription.type,
                'started_at': datetime.now().isoformat()
            })
            db.commit()
        except Exception as e:
            logger.debug(f"设置同步状态失败（开始）: {e}")

        # 订阅目录
        subscription_dir = Path(self._create_subscription_directory(subscription))

        # 获取UP主视频列表
        sub_lock = get_subscription_lock(subscription.id)
        cache_path = subscription_dir / 'playlist.json'
        try:
            async with sub_lock:
                video_list = await self._get_uploader_videos(subscription.uploader_id, db, subscription_id=subscription.id)
            try:
                with open(cache_path, 'w', encoding='utf-8') as cf:
                    json.dump(video_list, cf, ensure_ascii=False)
            except Exception as ce:
                logger.warning(f"写入列表缓存失败: {ce}")
        except Exception as e:
            logger.warning(f"实时获取UP主视频列表失败，尝试使用缓存: {e}")
            if cache_path.exists():
                try:
                    with open(cache_path, 'r', encoding='utf-8') as cf:
                        video_list = json.load(cf)
                    logger.info(f"使用缓存视频列表，共 {len(video_list)} 条")
                except Exception as re:
                    logger.error(f"读取缓存失败: {re}")
                    raise
            else:
                raise
        
        # 统一使用批量检查策略确保数据一致性
        remote_video_count = len([vi for vi in video_list if vi.get('id')])
        remote_ids = [vi.get('id') for vi in video_list if vi.get('id')]
        
        if remote_video_count > 100:
            logger.info(f"大UP主模式：远端 {remote_video_count} 个视频，使用批量查询优化")
            existing_videos = self._batch_check_existing(db, remote_ids, subscription_id=subscription.id, subscription_dir=subscription_dir)
        else:
            logger.info(f"标准模式：远端 {remote_video_count} 个视频，使用批量查询确保一致性")
            existing_videos = self._batch_check_existing(db, remote_ids, subscription_id=subscription.id, subscription_dir=subscription_dir)

        existing_ids = set(existing_videos.keys())
        new_videos = [vi for vi in video_list if (vid := vi.get('id')) and vid not in existing_ids]

        # 更新订阅统计
        try:
            subscription.expected_total = remote_video_count
            subscription.expected_total_synced_at = datetime.now()
            subscription.last_check = datetime.now()
            db.commit()
        except Exception as e:
            logger.warning(f"更新订阅统计失败: {e}")
            db.rollback()

        # 同步状态：完成
        try:
            self._set_sync_status(db, subscription_id, status='idle', extra={
                'remote_total': remote_video_count,
                'existing': len(existing_ids),
                'pending': len(new_videos),
                'updated_at': datetime.now().isoformat()
            })
            db.commit()
        except Exception:
            pass

        logger.info(f"UP主 {subscription.name} 检查完成: 远端 {remote_video_count}, 本地已有 {len(existing_ids)}, 新增 {len(new_videos)}")
        
        return {
            'subscription_id': subscription_id,
            'remote_total': remote_video_count,
            'existing_videos': len(existing_ids),
            'new_videos': len(new_videos),
            'videos': new_videos
        }

    async def download_keyword(self, subscription_id: int, db: Session) -> Dict[str, Any]:
        """下载关键词订阅"""
        subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not subscription:
            raise ValueError(f"订阅 {subscription_id} 不存在")
        
        if not subscription.keyword:
            raise ValueError(f"关键词订阅 {subscription_id} 缺少keyword")
        
        logger.info(f"开始搜索关键词: {subscription.name} (关键词: {subscription.keyword})")

        # 同步状态：标记开始
        try:
            self._set_sync_status(db, subscription_id, status='running', extra={
                'name': subscription.name,
                'type': subscription.type,
                'started_at': datetime.now().isoformat()
            })
            db.commit()
        except Exception as e:
            logger.debug(f"设置同步状态失败（开始）: {e}")

        # 订阅目录
        subscription_dir = Path(self._create_subscription_directory(subscription))

        # 获取关键词搜索结果
        sub_lock = get_subscription_lock(subscription.id)
        cache_path = subscription_dir / 'playlist.json'
        try:
            async with sub_lock:
                video_list = await self._get_keyword_videos(subscription.keyword, db, subscription_id=subscription.id)
            try:
                with open(cache_path, 'w', encoding='utf-8') as cf:
                    json.dump(video_list, cf, ensure_ascii=False)
            except Exception as ce:
                logger.warning(f"写入列表缓存失败: {ce}")
        except Exception as e:
            logger.warning(f"实时搜索关键词失败，尝试使用缓存: {e}")
            if cache_path.exists():
                try:
                    with open(cache_path, 'r', encoding='utf-8') as cf:
                        video_list = json.load(cf)
                    logger.info(f"使用缓存搜索结果，共 {len(video_list)} 条")
                except Exception as re:
                    logger.error(f"读取缓存失败: {re}")
                    raise
            else:
                raise
        
        # 统一使用批量检查策略确保数据一致性
        remote_video_count = len([vi for vi in video_list if vi.get('id')])
        remote_ids = [vi.get('id') for vi in video_list if vi.get('id')]
        
        logger.info(f"关键词模式：搜索到 {remote_video_count} 个视频，使用批量查询确保一致性")
        existing_videos = self._batch_check_existing(db, remote_ids, subscription_id=subscription.id, subscription_dir=subscription_dir)

        existing_ids = set(existing_videos.keys())
        new_videos = [vi for vi in video_list if (vid := vi.get('id')) and vid not in existing_ids]

        # 更新订阅统计
        try:
            subscription.expected_total = remote_video_count
            subscription.expected_total_synced_at = datetime.now()
            subscription.last_check = datetime.now()
            db.commit()
        except Exception as e:
            logger.warning(f"更新订阅统计失败: {e}")
            db.rollback()

        # 同步状态：完成
        try:
            self._set_sync_status(db, subscription_id, status='idle', extra={
                'remote_total': remote_video_count,
                'existing': len(existing_ids),
                'pending': len(new_videos),
                'updated_at': datetime.now().isoformat()
            })
            db.commit()
        except Exception:
            pass

        logger.info(f"关键词 {subscription.name} 搜索完成: 搜索到 {remote_video_count}, 本地已有 {len(existing_ids)}, 新增 {len(new_videos)}")
        
        return {
            'subscription_id': subscription_id,
            'remote_total': remote_video_count,
            'existing_videos': len(existing_ids),
            'new_videos': len(new_videos),
            'videos': new_videos
        }
    

    async def _get_uploader_videos(self, uploader_id: str, db: Session, subscription_id: Optional[int] = None, *, disable_incremental: bool = False, limit: int = 500) -> List[Dict]:
        """获取UP主视频列表"""
        await rate_limiter.wait()

        cookie = cookie_manager.get_available_cookie(db)
        if not cookie:
            raise Exception("没有可用的Cookie")

        # 构建UP主空间URL
        uploader_url = f"https://space.bilibili.com/{uploader_id}/video"
        
        # 队列登记：list_fetch
        job_id: Optional[str] = None
        try:
            job_id = await request_queue.enqueue(
                job_type="list_fetch",
                subscription_id=subscription_id,
                requires_cookie=True
            )
            await request_queue.mark_running(job_id)
        except Exception:
            job_id = None

        cookies_path = None
        try:
            # 写入临时 cookies.txt
            fd, cookies_path = tempfile.mkstemp(prefix='cookies_', suffix='.txt')
            os.close(fd)
            with open(cookies_path, 'w', encoding='utf-8') as cf:
                cf.write("# Netscape HTTP Cookie File\n")
                cf.write("# This file was generated by bili_curator V6\n\n")
                if getattr(cookie, 'sessdata', None):
                    cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{cookie.sessdata}\n")
                if getattr(cookie, 'bili_jct', None):
                    if str(cookie.bili_jct).strip():
                        cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\t{cookie.bili_jct}\n")
                if getattr(cookie, 'dedeuserid', None):
                    if str(cookie.dedeuserid).strip():
                        cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tDedeUserID\t{cookie.dedeuserid}\n")

            # 公共 yt-dlp 参数
            common_args_base = [
                'yt-dlp',
                '--referer', 'https://www.bilibili.com/',
                '--force-ipv4',
                '--sleep-interval', '2',
                '--max-sleep-interval', '5',
                '--retries', '5',
                '--fragment-retries', '5',
                '--retry-sleep', '3',
                '--ignore-errors',
                '--no-warnings',
                '--playlist-end', str(limit)  # 限制获取数量
            ]

            videos: List[Dict] = []
            last_err = None
            current_cookie = cookie
            ua_requires_cookie = True

            def _rebuild_common_args() -> List[str]:
                ua = get_user_agent(ua_requires_cookie)
                args = common_args_base + ['--user-agent', ua]
                if cookies_path:
                    args += ['--cookies', cookies_path]
                return args

            common_args = _rebuild_common_args()

            # 尝试获取UP主视频列表
            try:
                cmd = common_args + ['--flat-playlist', '--dump-single-json', uploader_url]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                out, err = await asyncio.wait_for(proc.communicate(), timeout=self.list_fetch_cmd_timeout)
                
                if proc.returncode == 0:
                    try:
                        data = json.loads(out.decode('utf-8', errors='ignore') or '{}')
                        
                        # 提取UP主名称用于目录命名
                        uploader_name = data.get('uploader') or data.get('channel') or data.get('title')
                        if uploader_name and subscription_id:
                            # 更新订阅名称为UP主真实名称
                            try:
                                subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
                                if subscription and subscription.type == 'uploader':
                                    # 如果当前名称包含ID或是默认格式，则更新为UP主名称
                                    if (not subscription.name or 
                                        subscription.uploader_id in subscription.name or 
                                        subscription.name.startswith('UP主订阅')):
                                        new_name = f"UP主订阅 - {uploader_name}"
                                        subscription.name = new_name
                                        db.commit()
                                        logger.info(f"更新UP主订阅名称: {new_name}")
                            except Exception as e:
                                logger.warning(f"更新UP主订阅名称失败: {e}")
                        
                        entries = data.get('entries', [])
                        if isinstance(entries, list):
                            for entry in entries:
                                if isinstance(entry, dict) and entry.get('id'):
                                    videos.append(entry)
                        logger.info(f"UP主 {uploader_id} 获取到 {len(videos)} 个视频")
                    except Exception as e:
                        last_err = str(e)
                        logger.error(f"解析UP主视频列表失败: {e}")
                else:
                    last_err = err.decode('utf-8', errors='ignore')
                    logger.error(f"获取UP主视频列表失败: {last_err}")

            except asyncio.TimeoutError:
                logger.warning(f"UP主视频列表获取超时: {uploader_url}")
                last_err = "获取超时"
            except Exception as e:
                logger.error(f"UP主视频列表获取异常: {e}")
                last_err = str(e)

            if not videos and last_err:
                raise Exception(f"获取UP主视频列表失败: {last_err}")

            return videos

        finally:
            if cookies_path and os.path.exists(cookies_path):
                try:
                    os.unlink(cookies_path)
                except Exception:
                    pass
            if job_id:
                try:
                    await request_queue.mark_completed(job_id)
                except Exception:
                    pass

    async def _get_keyword_videos(self, keyword: str, db: Session, subscription_id: Optional[int] = None, *, disable_incremental: bool = False, limit: int = 50) -> List[Dict]:
        """获取关键词搜索视频列表"""
        await rate_limiter.wait()

        cookie = cookie_manager.get_available_cookie(db)
        if not cookie:
            raise Exception("没有可用的Cookie")

        # 构建搜索URL
        search_url = f"ytsearch{limit}:{keyword} site:bilibili.com"
        
        # 队列登记：list_fetch
        job_id: Optional[str] = None
        try:
            job_id = await request_queue.enqueue(
                job_type="list_fetch",
                subscription_id=subscription_id,
                requires_cookie=True
            )
            await request_queue.mark_running(job_id)
        except Exception:
            job_id = None

        cookies_path = None
        try:
            # 写入临时 cookies.txt
            fd, cookies_path = tempfile.mkstemp(prefix='cookies_', suffix='.txt')
            os.close(fd)
            with open(cookies_path, 'w', encoding='utf-8') as cf:
                cf.write("# Netscape HTTP Cookie File\n")
                cf.write("# This file was generated by bili_curator V6\n\n")
                if getattr(cookie, 'sessdata', None):
                    cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{cookie.sessdata}\n")
                if getattr(cookie, 'bili_jct', None):
                    if str(cookie.bili_jct).strip():
                        cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\t{cookie.bili_jct}\n")
                if getattr(cookie, 'dedeuserid', None):
                    if str(cookie.dedeuserid).strip():
                        cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tDedeUserID\t{cookie.dedeuserid}\n")

            # 公共 yt-dlp 参数
            common_args_base = [
                'yt-dlp',
                '--referer', 'https://www.bilibili.com/',
                '--force-ipv4',
                '--sleep-interval', '3',
                '--max-sleep-interval', '8',
                '--retries', '3',
                '--fragment-retries', '3',
                '--retry-sleep', '5',
                '--ignore-errors',
                '--no-warnings'
            ]

            videos: List[Dict] = []
            last_err = None
            ua_requires_cookie = True

            def _rebuild_common_args() -> List[str]:
                ua = get_user_agent(ua_requires_cookie)
                args = common_args_base + ['--user-agent', ua]
                if cookies_path:
                    args += ['--cookies', cookies_path]
                return args

            common_args = _rebuild_common_args()

            # 尝试搜索关键词视频
            try:
                cmd = common_args + ['--flat-playlist', '--dump-single-json', search_url]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                out, err = await asyncio.wait_for(proc.communicate(), timeout=self.list_fetch_cmd_timeout)
                
                if proc.returncode == 0:
                    try:
                        data = json.loads(out.decode('utf-8', errors='ignore') or '{}')
                        entries = data.get('entries', [])
                        if isinstance(entries, list):
                            for entry in entries:
                                if isinstance(entry, dict) and entry.get('id'):
                                    # 过滤只保留B站视频
                                    url = entry.get('webpage_url', '')
                                    if 'bilibili.com' in url and ('BV' in entry.get('id', '') or 'av' in entry.get('id', '')):
                                        videos.append(entry)
                        logger.info(f"关键词 '{keyword}' 搜索到 {len(videos)} 个B站视频")
                    except Exception as e:
                        last_err = str(e)
                        logger.error(f"解析关键词搜索结果失败: {e}")
                else:
                    last_err = err.decode('utf-8', errors='ignore')
                    logger.error(f"关键词搜索失败: {last_err}")

            except asyncio.TimeoutError:
                logger.warning(f"关键词搜索超时: {keyword}")
                last_err = "搜索超时"
            except Exception as e:
                logger.error(f"关键词搜索异常: {e}")
                last_err = str(e)

            if not videos and last_err:
                raise Exception(f"关键词搜索失败: {last_err}")

            return videos

        finally:
            if cookies_path and os.path.exists(cookies_path):
                try:
                    os.unlink(cookies_path)
                except Exception:
                    pass
            if job_id:
                try:
                    await request_queue.mark_completed(job_id)
                except Exception:
                    pass

    async def _get_collection_videos(self, collection_url: str, db: Session, subscription_id: Optional[int] = None, *, disable_incremental: bool = False) -> List[Dict]:
        """获取合集视频列表（对齐V4：支持仅SESSDATA、UA/Referer、重试参数、三段回退解析）"""
        await rate_limiter.wait()

        cookie = cookie_manager.get_available_cookie(db)
        if not cookie:
            raise Exception("没有可用的Cookie")

        # 队列登记：list_fetch
        job_id: Optional[str] = None
        try:
            job_id = await request_queue.enqueue(
                job_type="list_fetch",
                subscription_id=subscription_id,
                requires_cookie=True
            )
            await request_queue.mark_running(job_id)
        except Exception:
            # 队列模块异常不影响主流程
            job_id = None

        cookies_path = None
        try:
            # 写入临时 cookies.txt (仅写入存在的键，支持只有 SESSDATA)
            fd, cookies_path = tempfile.mkstemp(prefix='cookies_', suffix='.txt')
            os.close(fd)
            with open(cookies_path, 'w', encoding='utf-8') as cf:
                cf.write("# Netscape HTTP Cookie File\n")
                cf.write("# This file was generated by bili_curator V6\n\n")
                if getattr(cookie, 'sessdata', None):
                    cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{cookie.sessdata}\n")
                if getattr(cookie, 'bili_jct', None):
                    if str(cookie.bili_jct).strip():
                        cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\t{cookie.bili_jct}\n")
                if getattr(cookie, 'dedeuserid', None):
                    if str(cookie.dedeuserid).strip():
                        cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tDedeUserID\t{cookie.dedeuserid}\n")

            # 公共 yt-dlp 参数（与V4对齐）：Referer/重试/忽略警告/轻睡眠（UA 与 cookies 由后续函数动态注入）
            common_args_base = [
                'yt-dlp',
                '--referer', 'https://www.bilibili.com/',
                '--force-ipv4',
                '--sleep-interval', '2',
                '--max-sleep-interval', '5',
                '--retries', '5',
                '--fragment-retries', '5',
                '--retry-sleep', '3',
                '--ignore-errors',
                '--no-warnings',
                '--no-download',
            ]

            # 允许通过环境变量传入 extractor-args（例如为 bilibili 指定解析策略）
            extractor_args = os.getenv('YT_DLP_EXTRACTOR_ARGS')
            if extractor_args and isinstance(extractor_args, str) and extractor_args.strip():
                common_args_base += ['--extractor-args', extractor_args.strip()]

            # 写入 cookie 文件的辅助函数，返回新的临时路径
            cookie_files: List[str] = []
            def _write_cookie_file(cobj) -> str:
                fd, cpath = tempfile.mkstemp(prefix='cookies_', suffix='.txt')
                os.close(fd)
                with open(cpath, 'w', encoding='utf-8') as cf:
                    cf.write("# Netscape HTTP Cookie File\n")
                    cf.write("# This file was generated by bili_curator V6\n\n")
                    if getattr(cobj, 'sessdata', None):
                        cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{cobj.sessdata}\n")
                    if getattr(cobj, 'bili_jct', None):
                        if str(cobj.bili_jct).strip():
                            cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\t{cobj.bili_jct}\n")
                    if getattr(cobj, 'dedeuserid', None):
                        if str(cobj.dedeuserid).strip():
                            cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tDedeUserID\t{cobj.dedeuserid}\n")
                cookie_files.append(cpath)
                return cpath

            async def run_cmd(args):
                async with yt_dlp_semaphore:
                    proc = await asyncio.create_subprocess_exec(
                        *args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    try:
                        out, err = await asyncio.wait_for(proc.communicate(), timeout=self.list_fetch_cmd_timeout)
                        return proc.returncode, out, err
                    except asyncio.TimeoutError:
                        logger.warning(f"列表抓取命令超时 (>{self.list_fetch_cmd_timeout}s)，正在终止: {' '.join(args)}")
                        try:
                            proc.terminate()
                            await asyncio.wait_for(proc.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            proc.kill()
                        raise

            videos: List[Dict] = []
            trace_events: List[Dict[str, Any]] = []
            last_err = None
            # 当前使用的 Cookie（支持失败后轮换）
            current_cookie = cookie
            # 初始 cookies.txt，与命令行参数组合
            cookies_path = _write_cookie_file(current_cookie)
            # UA 模式：默认按需带 Cookie 的 UA；在重试过程中会在 cookie/nocookie 模式之间轮换
            ua_requires_cookie = True

            def _rebuild_common_args() -> List[str]:
                # 根据当前 UA 模式与 cookies_path 动态重建参数
                ua = get_user_agent(ua_requires_cookie)
                args = common_args_base + ['--user-agent', ua]
                if cookies_path:
                    args += ['--cookies', cookies_path]
                return args

            common_args = _rebuild_common_args()

            # 优先尝试一次性抓取，减少多次 extractor 调用带来的风控与不稳定
            # 可通过环境变量 LIST_PREFETCH_FIRST 关闭（默认开启）
            def _env_true(v: Optional[str]) -> bool:
                return str(v).strip().lower() in ('1', 'true', 'yes', 'on')

            async def try_single_fetch_first() -> None:
                nonlocal videos, last_err
                # 1) 优先 -J 全量 JSON
                cmd_b = common_args + ['-J', collection_url]
                rc, out, err = await run_cmd(cmd_b)
                if rc == 0:
                    try:
                        data = json.loads(out.decode('utf-8', errors='ignore') or '{}')
                        entries = data.get('entries')
                        if isinstance(entries, list):
                            for it in entries:
                                if isinstance(it, dict):
                                    videos.append(it)
                            trace_events.append({'type': 'prefetch_ok', 'method': '-J', 'count': len(entries or []), 'ts': datetime.now().isoformat()})
                            return
                    except Exception as e:
                        last_err = e
                else:
                    last_err = err.decode('utf-8', errors='ignore')

                # 2) 再试 --flat-playlist --dump-single-json（扁平化单次JSON）
                cmd_a = common_args + ['--flat-playlist', '--dump-single-json', collection_url]
                rc, out, err = await run_cmd(cmd_a)
                if rc == 0:
                    try:
                        data = json.loads(out.decode('utf-8', errors='ignore') or '{}')
                        entries = data.get('entries')
                        if isinstance(entries, list):
                            for it in entries:
                                if isinstance(it, dict):
                                    videos.append(it)
                            trace_events.append({'type': 'prefetch_ok', 'method': '--dump-single-json', 'count': len(entries or []), 'ts': datetime.now().isoformat()})
                            return
                    except Exception as e:
                        last_err = e
                else:
                    last_err = err.decode('utf-8', errors='ignore')

                # 3) 最后行式兜底
                cmd_c = common_args + ['--dump-json', '--flat-playlist', collection_url]
                rc, out, err = await run_cmd(cmd_c)
                if rc == 0:
                    count_this = 0
                    for line in (out.decode('utf-8', errors='ignore') or '').strip().split('\n'):
                        if not line.strip():
                            continue
                        try:
                            info = json.loads(line)
                            if isinstance(info, dict):
                                videos.append(info)
                                count_this += 1
                        except json.JSONDecodeError:
                            continue
                    if count_this > 0:
                        trace_events.append({'type': 'prefetch_ok', 'method': '--dump-json', 'count': count_this, 'ts': datetime.now().isoformat()})
                else:
                    last_err = err.decode('utf-8', errors='ignore')

            try:
                if _env_true(os.getenv('LIST_PREFETCH_FIRST', '1')):
                    await try_single_fetch_first()
            except Exception:
                # 预抓取失败不影响后续分页
                pass

            # 分页抓取（与预抓取合并）：
            # - 预抓取先尝试一次性拿到尽可能多的条目；
            # - 无论预抓取是否拿到部分条目，都继续分页补全，最终以去重合并的全集为准；
            # - 分段大小、重试与退避由环境变量控制。
            chunk_size = self.list_chunk_size
            max_chunks = self.list_max_chunks
            # 窗口化：允许通过环境变量传入 extractor-args（例如为 bilibili 指定解析策略）
            if not disable_incremental:
                try:
                    # 优先订阅级：sync:{sid}:scan_window_chunks
                    eff_chunks = None
                    if subscription_id is not None:
                        skey_sub = f"sync:{subscription_id}:scan_window_chunks"
                        srow = db.query(Settings).filter(Settings.key == skey_sub).first()
                        if srow and srow.value is not None:
                            eff_chunks = int(str(srow.value).strip())
                    # 全局级：sync:global:scan_window_chunks
                    if eff_chunks is None:
                        srowg = db.query(Settings).filter(Settings.key == 'sync:global:scan_window_chunks').first()
                        if srowg and srowg.value is not None:
                            eff_chunks = int(str(srowg.value).strip())
                    if isinstance(eff_chunks, int) and eff_chunks > 0:
                        max_chunks = max(1, min(max_chunks, eff_chunks))
                        logger.info(f"窗口化扫描：限制分页块数为 {max_chunks} (chunk_size={chunk_size})")
                except Exception:
                    pass
            # 若预抓取已获得部分条目，为减少重复请求，可跳过已覆盖的整段
            chunk_idx = 0
            try:
                if videos and isinstance(videos, list):
                    # 用当前已得条目数近似估算可跳过的块数
                    chunk_idx = max(0, int(len(videos) // max(1, chunk_size)))
            except Exception:
                chunk_idx = 0
            # 连续失败阈值：若连续失败达到阈值则结束分页，避免无谓重试
            try:
                fail_streak_limit = int(os.getenv('LIST_FAIL_STREAK_LIMIT', '3'))
            except Exception:
                fail_streak_limit = 3
            fail_streak = 0

            # 增量扫描：读取订阅级“头部快照”，用于遇到连续已见项时提前停止
            head_snap_key = f"sync:{subscription_id}:head_snapshot"
            head_seen: List[str] = []
            try:
                s_snap = db.query(Settings).filter(Settings.key == head_snap_key).first()
                if s_snap and s_snap.value:
                    arr = json.loads(s_snap.value) if isinstance(s_snap.value, str) else []
                    if isinstance(arr, list):
                        head_seen = [str(x) for x in arr if isinstance(x, (str, int))]
            except Exception:
                head_seen = []

            try:
                # 禁用增量时关闭提前停止（阈值置0）
                if disable_incremental:
                    stop_seen_threshold = 0
                else:
                    stop_seen_threshold = int(os.getenv('CURSOR_SEEN_THRESHOLD', '30'))
                head_snap_cap = int(os.getenv('CURSOR_HEAD_SNAPSHOT_CAP', '200'))
            except Exception:
                stop_seen_threshold, head_snap_cap = (0 if disable_incremental else 30), 200
            consecutive_seen = 0
            early_stop = False

            # 预抓取不再跳过分页；始终进行分页以补全全集
            while chunk_idx < max_chunks and not early_stop:
                start = chunk_idx * chunk_size + 1
                end = start + chunk_size - 1
                cmd_chunk = common_args + [
                    '--dump-json', '--flat-playlist',
                    '--playlist-items', f'{start}-{end}',
                    collection_url
                ]

                # 每段重试次数与退避区间
                attempt = 0
                prefetch_triggered = False
                out, err = b'', b''
                rc = 0
                t0 = datetime.now()
                while attempt < self.list_retry:
                    rc, out, err = await run_cmd(cmd_chunk)
                    if rc == 0:
                        break
                    attempt += 1
                    last_err = (err.decode('utf-8', errors='ignore') or '').strip()
                    logger.warning(
                        f"分页抓取 {start}-{end} 失败(rc={rc}) 第{attempt}次: {last_err} | "
                        f"cookie_id={getattr(current_cookie,'id',None)} ua_mode={'cookie' if ua_requires_cookie else 'nocookie'}"
                    )

                    # 命中 yt-dlp 提示的 KeyError('data')/Extractor error 时，立即切换到一次性预抓取
                    try:
                        low = last_err.lower()
                        if "keyerror('data')" in low or 'extractor error' in low:
                            prefetch_triggered = True
                            break
                    except Exception:
                        pass
                    # 第2次及以后尝试：轮换 Cookie 与 UA，并重建参数
                    try:
                        if attempt >= 1:
                            alt = cookie_manager.get_available_cookie(db)
                            if alt and getattr(alt, 'id', None) and getattr(current_cookie, 'id', None) and alt.id != current_cookie.id:
                                current_cookie = alt
                                # 重建 cookie 文件并替换命令参数中的 --cookies 路径
                                try:
                                    cookies_path = _write_cookie_file(current_cookie)
                                    # 轮换 UA：在多次失败后切换 UA 模式
                                    ua_requires_cookie = not ua_requires_cookie
                                    common_args = _rebuild_common_args()
                                    cmd_chunk = common_args + [
                                        '--dump-json', '--flat-playlist',
                                        '--playlist-items', f'{start}-{end}',
                                        collection_url
                                    ]
                                    logger.info(
                                        f"分页重试切换 Cookie/UA: cookie={getattr(current_cookie,'name','unknown')} "
                                        f"ua_mode={'cookie' if ua_requires_cookie else 'nocookie'} range={start}-{end}"
                                    )
                                except Exception as ce:
                                    logger.debug(f"重建cookie文件失败: {ce}")
                            else:
                                # 即使 Cookie 未切换成功，也尝试仅轮换 UA 以增加多样性
                                try:
                                    ua_requires_cookie = not ua_requires_cookie
                                    common_args = _rebuild_common_args()
                                    cmd_chunk = common_args + [
                                        '--dump-json', '--flat-playlist',
                                        '--playlist-items', f'{start}-{end}',
                                        collection_url
                                    ]
                                    logger.info(
                                        f"分页重试轮换 UA: ua_mode={'cookie' if ua_requires_cookie else 'nocookie'} range={start}-{end}"
                                    )
                                except Exception as ue:
                                    logger.debug(f"轮换UA失败: {ue}")
                    except Exception:
                        pass
                    try:
                        await asyncio.sleep(random.uniform(self.list_backoff_min, self.list_backoff_max))
                    except Exception:
                        pass

                # 若触发了预抓取快速分支，优先尝试一次性抓取
                if prefetch_triggered:
                    try:
                        await try_single_fetch_first()
                        if videos:
                            # 成功则结束分页流程
                            early_stop = True
                            trace_events.append({'type': 'prefetch_switch_ok', 'range': f'{start}-{end}', 'ts': datetime.now().isoformat()})
                            break
                        else:
                            trace_events.append({'type': 'prefetch_switch_failed', 'range': f'{start}-{end}', 'error': last_err, 'ts': datetime.now().isoformat()})
                    except Exception as ee:
                        trace_events.append({'type': 'prefetch_switch_exception', 'range': f'{start}-{end}', 'error': str(ee), 'ts': datetime.now().isoformat()})

                if rc != 0:
                    # 当前段最终失败：记录并继续后续分页，允许跳过个别失败区间
                    fail_streak += 1
                    logger.warning(
                        f"分页抓取 {start}-{end} 最终失败，跳过该区间 (连续失败 {fail_streak}/{fail_streak_limit}) | "
                        f"cookie_id={getattr(current_cookie,'id',None)} ua_mode={'cookie' if ua_requires_cookie else 'nocookie'}"
                    )
                    trace_events.append({
                        'type': 'chunk_failed',
                        'range': f'{start}-{end}',
                        'attempts': attempt,
                        'duration_ms': int((datetime.now() - t0).total_seconds()*1000),
                        'error': last_err,
                        'cookie_id': getattr(current_cookie, 'id', None),
                        'ua_mode': 'cookie' if ua_requires_cookie else 'nocookie'
                    })
                    if fail_streak >= fail_streak_limit:
                        logger.warning("连续失败达到阈值，提前结束分页")
                        break
                    # 跳到下一段
                    chunk_idx += 1
                    # 段与段之间轻量延时（可配置），降低风控风险
                    try:
                        await asyncio.sleep(random.uniform(self.list_page_gap_min, self.list_page_gap_max))
                    except Exception:
                        pass
                    continue
                
                # 成功获取当前段，重置连续失败计数
                fail_streak = 0
                count_this = 0
                for line in (out.decode('utf-8', errors='ignore') or '').strip().split('\n'):
                    if not line.strip():
                        continue
                    try:
                        info = json.loads(line)
                        if isinstance(info, dict):
                            videos.append(info)
                            count_this += 1
                            # 增量提前停止：若该视频ID在“头部快照”中，计入连续命中
                            try:
                                vid = info.get('id')
                                if isinstance(vid, (str, int)) and str(vid) in head_seen:
                                    consecutive_seen += 1
                                else:
                                    consecutive_seen = 0
                            except Exception:
                                pass
                            if stop_seen_threshold > 0 and consecutive_seen >= stop_seen_threshold:
                                early_stop = True
                                break
                    except json.JSONDecodeError:
                        continue
                trace_events.append({
                    'type': 'chunk_ok',
                    'range': f'{start}-{end}',
                    'count': count_this,
                    'duration_ms': int((datetime.now() - t0).total_seconds()*1000),
                    'cookie_id': getattr(current_cookie, 'id', None),
                    'ua_mode': 'cookie' if ua_requires_cookie else 'nocookie'
                })
                # 本段不足 chunk_size，说明已到结尾
                if count_this < chunk_size:
                    break
                chunk_idx += 1
                # 段与段之间轻量延时（可配置），降低风控风险
                try:
                    await asyncio.sleep(random.uniform(self.list_page_gap_min, self.list_page_gap_max))
                except Exception:
                    pass

            # 统一去重合并（无论分页成功还是后续回退都需要）
            def dedup_videos(video_list):
                if not video_list:
                    return video_list
                try:
                    uniq = {}
                    for it in video_list:
                        vid = it.get('id') if isinstance(it, dict) else None
                        if isinstance(vid, str) and vid:
                            uniq[vid] = it
                    return list(uniq.values())
                except Exception:
                    return video_list
            
            videos = dedup_videos(videos)

            # 回退：若分页抓取未取到任何内容，再走 A/B/C（为兼容关闭预抓取的场景）
            if not videos:
                try:
                    await try_single_fetch_first()
                except Exception:
                    pass

            # 最终去重（回退策略可能产生重复）
            videos = dedup_videos(videos)

            # 更新“头部快照”：按抓取顺序取前 head_snap_cap 个ID
            try:
                if videos and subscription_id is not None:
                    head_ids: List[str] = []
                    for it in videos:
                        vid = it.get('id') if isinstance(it, dict) else None
                        if isinstance(vid, (str, int)):
                            head_ids.append(str(vid))
                        if len(head_ids) >= head_snap_cap:
                            break
                    if head_ids:
                        val = json.dumps(head_ids, ensure_ascii=False)
                        srow = db.query(Settings).filter(Settings.key == head_snap_key).first()
                        if srow:
                            srow.value = val
                            srow.description = srow.description or '订阅头部快照用于增量扫描'
                        else:
                            db.add(Settings(key=head_snap_key, value=val, description='订阅头部快照用于增量扫描'))
                        db.commit()
            except Exception:
                pass

            if not videos:
                err_msg = f"yt-dlp未能解析到视频列表; last_error={last_err!s}"
                logger.error(f"yt-dlp获取视频列表失败: {last_err}")
                # 写入一次trace，便于UI显示失败原因
                try:
                    self._set_sync_trace(db, subscription_id, [{
                        'type': 'list_failed',
                        'error': str(last_err),
                        'ts': datetime.now().isoformat()
                    }])
                    db.commit()
                except Exception:
                    pass
                raise Exception(f"获取视频列表失败: {err_msg}")

            cookie_manager.update_cookie_usage(db, cookie.id)
            logger.info(f"获取到 {len(videos)} 个视频")
            # 成功路径：标记队列完成，释放信号量/计数
            try:
                if job_id:
                    await request_queue.mark_done(job_id)
            except Exception:
                pass
            # 写入trace（保留最近若干）
            try:
                # 仅保留头尾与失败的关键事件，避免过大
                compact = []
                for ev in trace_events[-20:]:
                    compact.append(ev)
                compact.append({'type': 'list_done', 'count': len(videos), 'ts': datetime.now().isoformat()})
                self._set_sync_trace(db, subscription_id, compact)
                db.commit()
            except Exception:
                pass
            return videos
        except Exception as e:
                logger.error(f"获取合集视频列表失败: {e}")
                if "403" in str(e) or "401" in str(e):
                    # 记录失败，按阈值禁用
                    try:
                        cookie_manager.record_failure(db, cookie.id, str(e))
                    except Exception:
                        cookie_manager.mark_cookie_banned(db, cookie.id, str(e))
                # 412/风控：仅告警不计失败（避免误伤）
                if "412" in str(e) or "precondition" in str(e).lower():
                    logger.warning("远端可能触发风控(412)，已跳过失败计数")
                # 队列标记失败
                try:
                    if job_id:
                        await request_queue.mark_failed(job_id, str(e))
                except Exception:
                    pass
                raise
        finally:
                # 清理所有临时 cookie 文件
                try:
                    for cpath in list(set(locals().get('cookie_files', []) or [])):
                        if cpath and os.path.exists(cpath):
                            try:
                                os.remove(cpath)
                            except Exception:
                                pass
                except Exception:
                    pass

    # ----------------------
    # 同步状态与Trace辅助
    # ----------------------
    def _set_sync_status(self, db: Session, subscription_id: int, *, status: str, extra: Optional[Dict[str, Any]] = None) -> None:
        try:
            key = f"sync:{subscription_id}:status"
            payload = {'status': status, 'ts': datetime.now().isoformat()}
            if extra:
                payload.update(extra)
            val = json.dumps(payload, ensure_ascii=False)
            
            # 使用 UPSERT 避免并发写入问题
            from sqlalchemy import text
            db.execute(text("""
                INSERT INTO settings (key, value, description) 
                VALUES (:key, :val, '订阅同步状态')
                ON CONFLICT(key) DO UPDATE SET 
                value = :val, updated_at = CURRENT_TIMESTAMP
            """), {"key": key, "val": val})
        except Exception as e:
            logger.debug(f"记录同步状态失败: {e}")

    def _set_sync_trace(self, db: Session, subscription_id: int, events: List[Dict[str, Any]]) -> None:
        try:
            key = f"sync:{subscription_id}:trace"
            s = db.query(Settings).filter(Settings.key == key).first()
            # 读取现有并合并，保留最近50条
            arr: List[Dict[str, Any]] = []
            if s and s.value:
                try:
                    arr = json.loads(s.value)
                    if not isinstance(arr, list):
                        arr = []
                except Exception:
                    arr = []
            arr.extend(events)
            if len(arr) > 50:
                arr = arr[-50:]
            val = json.dumps(arr, ensure_ascii=False)
            if s:
                s.value = val
                s.description = s.description or '订阅同步Trace'
            else:
                db.add(Settings(key=key, value=val, description='订阅同步Trace'))
        except Exception as e:
            logger.debug(f"记录同步Trace失败: {e}")
    
    def _scan_existing_files(self, db: Session, subscription_id: Optional[int] = None, subscription_dir: Optional[Path] = None) -> Dict[str, str]:
        """扫描数据库（优先）或文件系统，返回已存在的视频ID->路径映射
        优先数据库，因为更快；如果数据库查询失败，则尝试文件系统扫描（仅限指定目录）
        """
        existing_videos = {}
        try:
            # 查询数据库中已下载的视频，仅取必要列
            scope = os.getenv('DEDUP_SCOPE', 'global').strip().lower()
            query = db.query(Video.bilibili_id, Video.video_path).filter(Video.downloaded == True)
            if scope == 'subscription' and subscription_id is not None:
                query = query.filter(Video.subscription_id == subscription_id)
            results = query.all()
            for vid, vpath in results:
                existing_videos[vid] = vpath or ''
            logger.info(f"发现 {len(existing_videos)} 个已存在的视频")
            return existing_videos
        except Exception as e:
            logger.warning(f"数据库查询失败，尝试文件系统扫描: {e}")
            # 降级：如果优化查询失败，回退到原始方法
            downloaded_videos = db.query(Video).filter(Video.downloaded == True).all()
            for video in downloaded_videos:
                existing_videos[video.bilibili_id] = video.video_path
        
        # 可配置：是否扫描文件系统加速去重（默认关闭以提升大合集性能）
        scan_fs = os.getenv('DEDUP_SCAN_FILESYSTEM', '0')
        if str(scan_fs).strip() in ('1', 'true', 'True', 'yes', 'on'):
            # 同时扫描文件系统中的JSON文件（兼容V5格式），递归子目录，支持 *.json 与 *.info.json
            # 仅限当前订阅目录（若提供），否则回退到全局下载目录
            base_dir = Path(subscription_dir) if subscription_dir else self.output_dir
            scanned = set()
            for json_file in list(base_dir.rglob("*.json")) + list(base_dir.rglob("*.info.json")):
                # 避免重复处理相同路径
                if json_file in scanned:
                    continue
                scanned.add(json_file)
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        ids: List[str] = []
                        # 兼容不同结构：dict含id、dict含entries、list[dict]
                        if isinstance(data, dict):
                            if 'id' in data and isinstance(data['id'], str):
                                ids.append(data['id'])
                            if 'entries' in data and isinstance(data['entries'], list):
                                for it in data['entries']:
                                    if isinstance(it, dict) and isinstance(it.get('id'), str):
                                        ids.append(it['id'])
                        elif isinstance(data, list):
                            for it in data:
                                if isinstance(it, dict) and isinstance(it.get('id'), str):
                                    ids.append(it['id'])

                        if not ids:
                            continue

                        # 计算可能的视频文件名（与json同名）并登记
                        name = json_file.name
                        if name.endswith('.info.json'):
                            base_name = name[:-10]
                        elif name.endswith('.json'):
                            base_name = name[:-5]
                        else:
                            base_name = json_file.stem
                        # 假设视频文件与json同目录（多后缀尝试）
                        for vid in ids:
                            if vid in existing_videos:
                                continue
                            for ext in ['.mp4', '.mkv', '.webm']:
                                video_file = json_file.parent / f"{base_name}{ext}"
                                if video_file.exists():
                                    existing_videos[vid] = str(video_file)
                                    break
                except Exception as e:
                    logger.warning(f"读取JSON文件 {json_file} 失败: {e}")
        
        logger.info(f"发现 {len(existing_videos)} 个已存在的视频")
        return existing_videos
    
    def _batch_check_existing(self, db: Session, remote_video_ids: List[str], subscription_id: Optional[int] = None, subscription_dir: Optional[Path] = None) -> Dict[str, str]:
        """大合集优化：批量检查指定视频ID是否已存在
        只查询远端视频ID列表中的视频，避免全表扫描
        """
        existing_videos = {}
        
        # 分批查询，避免 IN 子句过长
        batch_size = int(os.getenv('BATCH_IN_SIZE', '500'))  # SQLite IN 子句建议不超过999个参数
        
        for i in range(0, len(remote_video_ids), batch_size):
            batch_ids = remote_video_ids[i:i + batch_size]
            try:
                # 只查询当前批次的视频ID
                scope = os.getenv('DEDUP_SCOPE', 'global').strip().lower()
                query = db.query(Video.bilibili_id, Video.video_path).filter(
                    Video.bilibili_id.in_(batch_ids),
                    Video.downloaded == True
                )
                if scope == 'subscription' and subscription_id is not None:
                    query = query.filter(Video.subscription_id == subscription_id)
                batch_results = query.all()
                
                # 将结果加入 existing_videos
                for vid, vpath in batch_results:
                    existing_videos[vid] = vpath or ''
                
                logger.debug(f"批次 {i//batch_size + 1}: 检查 {len(batch_ids)} 个视频，找到 {len(batch_results)} 个已存在")
                
            except Exception as e:
                logger.warning(f"批量查询失败，回退到逐个检查: {e}")
                # 降级处理：逐个查询这个批次
                for video_id in batch_ids:
                    try:
                        result = db.query(Video.bilibili_id, Video.video_path).filter(
                            Video.bilibili_id == video_id,
                            Video.downloaded == True
                        ).first()
                        if result:
                            existing_videos[result[0]] = result[1]
                    except Exception:
                        continue
        
        # 可选：仍然扫描文件系统（如果启用）
        scan_fs = os.getenv('DEDUP_SCAN_FILESYSTEM', '0')
        if str(scan_fs).strip() in ('1', 'true', 'True', 'yes', 'on'):
            logger.debug("大合集模式下仍启用文件系统扫描")
            # 只扫描当前订阅目录，不做全局扫描
            if subscription_dir:
                fs_existing = self._scan_filesystem_in_dir(subscription_dir, set(remote_video_ids))
                existing_videos.update(fs_existing)
        
        total_batches = (len(remote_video_ids) + batch_size - 1) // batch_size
        logger.info(f"批量检查完成：{total_batches} 批次，{len(existing_videos)} 个视频已存在（scope={os.getenv('DEDUP_SCOPE','global')}）")
        return existing_videos
    
    def _scan_filesystem_in_dir(self, base_dir: Path, target_ids: set) -> Dict[str, str]:
        """在指定目录扫描文件系统，只查找目标视频ID"""
        found_videos = {}
        scanned = set()
        
        for json_file in list(base_dir.rglob("*.json")) + list(base_dir.rglob("*.info.json")):
            if json_file in scanned:
                continue
            scanned.add(json_file)
            
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    ids = []
                    
                    if isinstance(data, dict):
                        if 'id' in data and isinstance(data['id'], str):
                            ids.append(data['id'])
                        if 'entries' in data and isinstance(data['entries'], list):
                            for it in data['entries']:
                                if isinstance(it, dict) and isinstance(it.get('id'), str):
                                    ids.append(it['id'])
                    elif isinstance(data, list):
                        for it in data:
                            if isinstance(it, dict) and isinstance(it.get('id'), str):
                                ids.append(it['id'])
                    
                    # 只处理目标ID集合中的视频
                    relevant_ids = [vid for vid in ids if vid in target_ids]
                    if not relevant_ids:
                        continue
                    
                    # 计算视频文件路径
                    name = json_file.name
                    if name.endswith('.info.json'):
                        base_name = name[:-10]
                    elif name.endswith('.json'):
                        base_name = name[:-5]
                    else:
                        base_name = json_file.stem
                    
                    for vid in relevant_ids:
                        if vid in found_videos:
                            continue
                        for ext in ['.mp4', '.mkv', '.webm']:
                            video_file = json_file.parent / f"{base_name}{ext}"
                            if video_file.exists():
                                found_videos[vid] = str(video_file)
                                break
                                
            except Exception as e:
                logger.debug(f"扫描JSON文件失败 {json_file}: {e}")
                continue
        
        return found_videos
    
    async def _download_single_video(self, video_info: Dict[str, Any], subscription_id: int, db: Session) -> Dict[str, Any]:
        """下载单个视频"""
        async with self.download_semaphore:
            video_id = video_info.get('id')
            title = video_info.get('title', 'Unknown')
            
            logger.info(f"开始下载: {title} ({video_id})")
            
            # 防御性回滚，确保会话处于干净状态（避免上一条异常影响本条）
            try:
                db.rollback()
            except Exception:
                pass

            # 创建下载任务记录（仅使用统一的 bilibili_id，legacy: video_id 不再写入）
            task = DownloadTask(
                bilibili_id=video_id,
                subscription_id=subscription_id,
                status='downloading',
                started_at=datetime.now()
            )
            db.add(task)
            db.commit()
            
            try:
                # 若数据库已存在且文件存在，则直接跳过后续流程（避免重复下载/刷新nfo/info.json）
                try:
                    existing = db.query(Video).filter_by(bilibili_id=video_id).first()
                    if existing and existing.video_path and Path(existing.video_path).exists():
                        task.status = 'completed'
                        task.progress = 100.0
                        task.completed_at = datetime.now()
                        db.commit()
                        logger.info(f"已存在本地文件，跳过下载与NFO生成: {video_id} -> {existing.video_path}")
                        # 清理历史失败记录
                        try:
                            fkey = f"fail:{str(video_id)}"
                            frow = db.query(Settings).filter(Settings.key == fkey).first()
                            if frow:
                                db.delete(frow)
                                db.commit()
                        except Exception:
                            db.rollback()
                        return {
                            'video_id': video_id,
                            'title': existing.title or video_info.get('title') or video_id,
                            'success': True,
                            'video_path': existing.video_path
                        }
                except Exception:
                    # 容错：查询失败不影响主流程
                    pass

                # 基于文件系统的预下载检查：扫描订阅目录的 info.json，匹配到相同 id 则直接短路
                subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
                if not subscription:
                    raise Exception("订阅不存在")
                subscription_dir = Path(self._create_subscription_directory(subscription))
                try:
                    fmap = self._scan_existing_files(db, subscription_id=subscription_id, subscription_dir=subscription_dir)
                    fpath = fmap.get(str(video_id)) or fmap.get(video_id)
                    if fpath and Path(fpath).exists():
                        # DB 回填：若 DB 缺失记录则根据 info.json 尝试补全最小字段
                        existing = db.query(Video).filter_by(bilibili_id=video_id).first()
                        if not existing:
                            base = Path(fpath).with_suffix('')
                            json_p = base.with_suffix('.info.json')
                            thumb_p = base.with_suffix('.jpg')
                            meta_title = None
                            uploader = ''
                            uploader_id = ''
                            duration = None
                            upload_date = None
                            description = ''
                            try:
                                if json_p.exists():
                                    with open(json_p, 'r', encoding='utf-8') as jf:
                                        meta = json.load(jf)
                                    if isinstance(meta, dict):
                                        meta_title = meta.get('title') or meta.get('fulltitle') or None
                                        uploader = meta.get('uploader', '')
                                        uploader_id = meta.get('uploader_id', '')
                                        duration = meta.get('duration')
                                        upload_date = self._parse_upload_date(meta.get('upload_date'))
                                        description = meta.get('description', '')
                            except Exception as je:
                                logger.debug(f"读取已有 JSON 失败（跳过DB补全但继续短路）: {je}")
                            vrow = Video(
                                bilibili_id=video_id,
                                title=meta_title or (video_info.get('title') or str(video_id)),
                                uploader=uploader,
                                uploader_id=uploader_id,
                                duration=duration,
                                upload_date=upload_date,
                                description=description,
                                video_path=str(fpath),
                                json_path=str(json_p) if json_p.exists() else None,
                                thumbnail_path=str(thumb_p) if thumb_p.exists() else None,
                                downloaded=True,
                                subscription_id=subscription_id
                            )
                            try:
                                db.add(vrow)
                                db.commit()
                            except Exception as de:
                                db.rollback()
                                logger.debug(f"写入 DB 回填记录失败（忽略）: {de}")
                        # 标记任务完成并返回
                        task.status = 'completed'
                        task.progress = 100.0
                        task.completed_at = datetime.now()
                        db.commit()
                        logger.info(f"文件系统已存在（通过 info.json 关联），跳过下载: {video_id} -> {fpath}")
                        # 清理历史失败记录
                        try:
                            fkey = f"fail:{str(video_id)}"
                            frow = db.query(Settings).filter(Settings.key == fkey).first()
                            if frow:
                                db.delete(frow)
                                db.commit()
                        except Exception:
                            db.rollback()
                        return {
                            'video_id': video_id,
                            'title': (existing.title if existing else (video_info.get('title') or str(video_id))),
                            'success': True,
                            'video_path': str(fpath)
                        }
                except Exception as se:
                    logger.debug(f"文件系统预检查失败（忽略，继续下载流程）: {se}")

                # 如果标题缺失/Unknown，或标题等于ID/BV号样式，则补抓元数据以保证命名正确
                def _looks_like_id(s: Optional[str]) -> bool:
                    try:
                        if not s:
                            return True
                        s = str(s).strip()
                        if s == '':
                            return True
                        # 直接等于当前视频ID
                        if video_id and s == str(video_id):
                            return True
                        # BV 号样式（容忍大小写）
                        import re
                        if re.fullmatch(r"[Bb][Vv][0-9A-Za-z]+", s):
                            return True
                        return False
                    except Exception:
                        return False

                if _looks_like_id(title) or str(title).strip().lower() in ('', 'unknown'):
                    try:
                        detail_url = video_info.get('webpage_url') or video_info.get('url') or BilibiliDownloaderV6._safe_bilibili_url(video_id)
                        # 获取 Cookie 在真正需要网络请求时再获取
                        cookie = cookie_manager.get_available_cookie(db)
                        if not cookie:
                            raise Exception("没有可用的Cookie")
                        await rate_limiter.wait()
                        meta = await self._fetch_video_metadata(detail_url, cookie)
                        if meta:
                            # 回填关键信息
                            video_info.update(meta)
                            title = meta.get('title') or title
                    except Exception as e:
                        logger.warning(f"获取视频元数据失败，使用回退命名: {video_id} - {e}")
                        # 留给下游文件名生成时统一处理为 "Untitled - {id}"
                        pass
                
                # 生成安全的文件名（若标题仍不可用，则回退为“<uploader> - <date> - <ID>”的可读形式）
                safe_title = self._sanitize_filename(title or '')
                if (not safe_title) or safe_title.lower() in ('unknown', 'untitled') or safe_title == str(video_id) or safe_title.lower().startswith('bv'):
                    uploader = self._sanitize_filename(video_info.get('uploader') or '')
                    fmt_date = self._format_date(video_info.get('upload_date')) if video_info.get('upload_date') else ''
                    parts = []
                    if uploader:
                        parts.append(uploader)
                    if fmt_date:
                        parts.append(fmt_date)
                    # 确保至少包含一个可读前缀
                    if not parts:
                        parts.append('bilibili')
                    parts.append(str(video_id))
                    base_filename = ' - '.join(parts)
                    base_filename = self._sanitize_filename(base_filename)
                else:
                    base_filename = f"{safe_title}"
                
                # 标题重名冲突处理：若同名文件存在且其JSON的id与当前不同，则回退为“标题 - BV号”
                subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
                if not subscription:
                    raise Exception("订阅不存在")
                subscription_dir = Path(self._create_subscription_directory(subscription))
                existing_base = None
                for ext in ['.mp4', '.mkv', '.webm']:
                    p = subscription_dir / f"{base_filename}{ext}"
                    if p.exists():
                        existing_base = p.with_suffix('')
                        break
                if existing_base:
                    info_path = existing_base.with_suffix('.info.json')
                    try:
                        if info_path.exists():
                            with open(info_path, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                                if data.get('id') and data.get('id') != video_id:
                                    base_filename = f"{safe_title} - {video_id}"
                    except Exception:
                        # 无法读取则保守地回退加上BV号
                        base_filename = f"{safe_title} - {video_id}"
                
                # 下载视频（到此刻才需要 Cookie 与速率限制）
                cookie = cookie_manager.get_available_cookie(db)
                if not cookie:
                    raise Exception("没有可用的Cookie")
                await rate_limiter.wait()
                # 下载视频
                video_path = await self._download_with_ytdlp(
                    (video_info.get('url') or BilibiliDownloaderV6._safe_bilibili_url(video_id)),
                    base_filename,
                    subscription_dir,
                    subscription_id,
                    video_id,
                    cookie,
                    task.id,
                    db
                )
                
                # 创建NFO文件
                await self._create_nfo_file(video_info, base_filename, subscription_dir)
                
                # 保存视频信息到数据库（查重避免唯一键冲突）
                existing = db.query(Video).filter_by(bilibili_id=video_id).first()
                if existing:
                    # 已存在：标记任务完成并返回现有路径
                    task.status = 'completed'
                    task.progress = 100.0
                    task.completed_at = datetime.now()
                    db.commit()
                    logger.info(f"已存在，跳过入库: {title} ({video_id})")
                    return {
                        'video_id': video_id,
                        'title': title,
                        'success': True,
                        'video_path': existing.video_path
                    }

                # 计算JSON与缩略图路径
                json_path = subscription_dir / f"{base_filename}.info.json"
                thumbnail_path = subscription_dir / f"{base_filename}.jpg"

                video = Video(
                    bilibili_id=video_id,
                    title=title,
                    uploader=video_info.get('uploader', ''),
                    uploader_id=video_info.get('uploader_id', ''),
                    duration=video_info.get('duration'),
                    upload_date=self._parse_upload_date(video_info.get('upload_date')),
                    description=video_info.get('description', ''),
                    video_path=str(video_path),
                    json_path=str(json_path) if json_path.exists() else None,
                    thumbnail_path=str(thumbnail_path) if thumbnail_path.exists() else None,
                    downloaded=True,
                    subscription_id=subscription_id
                )
                db.add(video)
                
                # 更新任务状态
                task.status = 'completed'
                task.progress = 100.0
                task.completed_at = datetime.now()
                db.commit()
                
                # 更新Cookie使用统计
                cookie_manager.update_cookie_usage(db, cookie.id)
                # 成功后重置失败计数
                try:
                    cookie_manager.reset_failures(db, cookie.id)
                except Exception:
                    pass
                # 清理该视频的失败记录
                try:
                    fkey = f"fail:{str(video_id)}"
                    frow = db.query(Settings).filter(Settings.key == fkey).first()
                    if frow:
                        db.delete(frow)
                        db.commit()
                except Exception:
                    db.rollback()
                
                logger.info(f"下载完成: {title}")
                return {
                    'video_id': video_id,
                    'title': title,
                    'success': True,
                    'video_path': str(video_path)
                }
                
            except IntegrityError as e:
                # 唯一键或约束异常处理
                db.rollback()
                task.status = 'failed'
                task.error_message = str(e)
                task.completed_at = datetime.now()
                db.commit()
                logger.error(f"下载失败(数据库约束): {title} - {e}")
                return {
                    'video_id': video_id,
                    'title': title,
                    'success': False,
                    'error': str(e)
                }
            except Exception as e:
                logger.error(f"下载失败: {title} - {e}")
                
                # 更新任务状态
                task.status = 'failed'
                task.error_message = str(e)
                task.completed_at = datetime.now()
                db.commit()
                
                # 如果是认证错误，标记Cookie为不可用
                if "403" in str(e) or "401" in str(e):
                    if cookie:
                        try:
                            cookie_manager.record_failure(db, cookie.id, str(e))
                        except Exception:
                            cookie_manager.mark_cookie_banned(db, cookie.id, str(e))
                # 失败回补：排除永久不可用/已删除视频，不写入重试队列
                try:
                    msg = str(e) if e else ''
                    lower = msg.lower()
                    permanent = False
                    # 常见永久性不可用标记（英文）
                    for tok in ['404', '410', 'not found', 'gone', 'removed', 'deleted', 'copyright', 'no longer available', 'private video', 'unavailable']:
                        if tok in lower:
                            permanent = True
                            break
                    # 常见永久性不可用标记（中文）
                    if not permanent:
                        for tok in ['已删除', '已下架', '不存在', '不可用', '权限不足', '视频不见了', '违规', '版权原因']:
                            if tok in msg:
                                permanent = True
                                break
                    # 写入失败分类（Settings: fail:{bvid}）
                    try:
                        fkey = f"fail:{str(video_id)}"
                        frow = db.query(Settings).filter(Settings.key == fkey).first()
                        payload = {}
                        if frow and frow.value:
                            try:
                                payload = json.loads(frow.value)
                                if not isinstance(payload, dict):
                                    payload = {}
                            except Exception:
                                payload = {}
                        retry_count = int(payload.get('retry_count') or 0)
                        payload.update({
                            'class': 'permanent' if permanent else 'temporary',
                            'message': (msg[:500] if isinstance(msg, str) else str(msg)) if msg else '',
                            'last_at': datetime.now().isoformat(),
                            'sid': int(subscription_id) if subscription_id is not None else None,
                            'retry_count': (retry_count + 1)
                        })
                        val = json.dumps(payload, ensure_ascii=False)
                        if frow:
                            frow.value = val
                            frow.description = frow.description or '失败分类'
                        else:
                            db.add(Settings(key=fkey, value=val, description='失败分类'))
                        db.commit()
                    except Exception as fe:
                        db.rollback()
                        logger.debug(f"写入失败分类失败: {fe}")
                    if permanent:
                        logger.info(f"检测到永久不可用视频，跳过加入重试队列: {video_id} - {msg}")
                    else:
                        key = f"retry:{subscription_id}:failed_backfill"
                        s = db.query(Settings).filter(Settings.key == key).first()
                        arr = []
                        if s and s.value:
                            try:
                                arr = json.loads(s.value)
                                if not isinstance(arr, list):
                                    arr = []
                            except Exception:
                                arr = []
                        vid = str(video_id) if video_id else None
                        if vid:
                            if vid in arr:
                                arr = [x for x in arr if x != vid]
                            arr.append(vid)
                            try:
                                cap = int(os.getenv('RETRY_BACKFILL_CAP', '100'))
                            except Exception:
                                cap = 100
                            if len(arr) > cap:
                                arr = arr[-cap:]
                            val = json.dumps(arr, ensure_ascii=False)
                            if s:
                                s.value = val
                                s.description = s.description or '失败回补队列'
                            else:
                                db.add(Settings(key=key, value=val, description='失败回补队列'))
                            db.commit()
                except Exception as ee:
                    logger.debug(f"写入失败回补队列失败: {ee}")
                
                return {
                    'video_id': video_id,
                    'title': title,
                    'success': False,
                    'error': str(e)
                }

    async def _fetch_video_metadata(self, url: str, cookie) -> Optional[Dict[str, Any]]:
        """使用 yt-dlp 拉取单视频的详细元数据（含标题），用于命名与NFO补全"""
        cookies_path = None
        try:
            fd, cookies_path = tempfile.mkstemp(prefix='cookies_', suffix='.txt')
            os.close(fd)
            with open(cookies_path, 'w', encoding='utf-8') as cf:
                cf.write("# Netscape HTTP Cookie File\n")
                cf.write("# This file was generated by bili_curator V6\n\n")
                if getattr(cookie, 'sessdata', None):
                    cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{cookie.sessdata}\n")
                if getattr(cookie, 'bili_jct', None) and str(cookie.bili_jct).strip():
                    cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\t{cookie.bili_jct}\n")
                if getattr(cookie, 'dedeuserid', None) and str(cookie.dedeuserid).strip():
                    cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tDedeUserID\t{cookie.dedeuserid}\n")

            cmd = [
                'yt-dlp',
                '--user-agent', get_user_agent(True),
                '--referer', 'https://www.bilibili.com/',
                '--force-ipv4',
                '--sleep-interval', '2',
                '--max-sleep-interval', '5',
                '--retries', '5',
                '--fragment-retries', '5',
                '--retry-sleep', '3',
                '--ignore-errors',
                '--no-warnings',
                '--dump-single-json',
                '--no-playlist',
                '--cookies', cookies_path,
                url,
            ]
            async with yt_dlp_semaphore:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.meta_cmd_timeout)
                except asyncio.TimeoutError:
                    logger.warning(f"元数据获取命令超时 (>{self.meta_cmd_timeout}s)，正在终止: {' '.join(cmd)}")
                    try:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        process.kill()
                    return None
            if process.returncode != 0:
                err = stderr.decode('utf-8', errors='ignore')
                logger.warning(f"获取视频元数据失败: {err}")
                return None
            try:
                data = json.loads(stdout.decode('utf-8', errors='ignore') or '{}')
                # 兼容某些站点的外层entries结构
                if isinstance(data, dict) and 'title' in data:
                    return data
                if isinstance(data, dict) and 'entries' in data and data['entries']:
                    return data['entries'][0]
                return None
            except Exception as e:
                logger.warning(f"解析视频元数据失败: {e}")
                return None
        finally:
            if cookies_path and os.path.exists(cookies_path):
                try:
                    os.remove(cookies_path)
                except Exception:
                    pass
    
    async def _download_with_ytdlp(self, url: str, base_filename: str, subscription_dir: Path, subscription_id: int, video_id: Optional[str], cookie, task_id: int, db: Session) -> Path:
        """使用yt-dlp下载视频（输出到订阅目录）。
        同时在全局请求队列登记一个 download 作业（requires_cookie=True），以便分通道并发与可视化统计。
        """
        output_template = str(subscription_dir / f"{base_filename}.%(ext)s")

        # 写入临时 cookies.txt（Netscape 格式最简行）
        cookies_path = None
        try:
            fd, cookies_path = tempfile.mkstemp(prefix='cookies_', suffix='.txt')
            os.close(fd)
            with open(cookies_path, 'w', encoding='utf-8') as cf:
                cf.write("# Netscape HTTP Cookie File\n")
                cf.write("# This file was generated by bili_curator V6\n\n")
                if getattr(cookie, 'sessdata', None):
                    cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{cookie.sessdata}\n")
                if getattr(cookie, 'bili_jct', None) and str(cookie.bili_jct).strip():
                    cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\t{cookie.bili_jct}\n")
                if getattr(cookie, 'dedeuserid', None) and str(cookie.dedeuserid).strip():
                    cf.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\tDedeUserID\t{cookie.dedeuserid}\n")

            # 队列登记：download（强制 Cookie 通道）
            job_id: Optional[str] = None
            try:
                job_id = await request_queue.enqueue(
                    job_type="download",
                    subscription_id=subscription_id,
                    requires_cookie=True,
                    dedup_key=(f"download:{subscription_id}:{video_id}" if video_id else None),
                    video_id=video_id
                )
                await request_queue.mark_running(job_id)
            except Exception:
                job_id = None

            async def run_yt_dlp(format_str: str):
                cmd = [
                    'yt-dlp',
                    '--user-agent', get_user_agent(True),
                    '--referer', 'https://www.bilibili.com/',
                    '--force-ipv4',
                    '--sleep-interval', '2',
                    '--max-sleep-interval', '5',
                    '--retries', '5',
                    '--fragment-retries', '5',
                    '--retry-sleep', '3',
                    '--ignore-errors',
                    '--no-warnings',
                    '--no-overwrites',
                    '--format', format_str,
                    '--output', output_template,
                    '--write-info-json',
                    '--write-thumbnail',
                    '--convert-thumbnails', 'jpg',
                    '--merge-output-format', 'mp4',
                    '--cookies', cookies_path,
                    url
                ]

                # 显式指定ffmpeg路径，若环境变量存在
                ffmpeg_path = os.getenv('FFMPEG_PATH')
                if ffmpeg_path and os.path.exists(ffmpeg_path):
                    cmd.extend(['--ffmpeg-location', ffmpeg_path])
                async with yt_dlp_semaphore:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    try:
                        out, err = await asyncio.wait_for(proc.communicate(), timeout=self.download_cmd_timeout)
                        return proc.returncode, out, err
                    except asyncio.TimeoutError:
                        logger.warning(f"下载命令超时 (>{self.download_cmd_timeout}s)，正在终止: {' '.join(cmd)}")
                        try:
                            proc.terminate()
                            await asyncio.wait_for(proc.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            proc.kill()
                        raise

            # 多级格式回退策略，确保能下载到视频文件
            try:
                max_h = int(os.getenv('MAX_HEIGHT', '1080'))
                if max_h <= 0:
                    max_h = 1080
            except Exception:
                max_h = 1080
            formats_to_try = [
                f"bestvideo*[height<={max_h}]+bestaudio/best[height<={max_h}]",  # 首选受控分辨率
                'bv*+ba/b*',                                                      # 提前尝试通用最佳组合，覆盖部分站点限制
                'bestvideo*+bestaudio/best',                                      # 无分辨率上限的双轨兜底
                f"best[height<={max_h}]",                                       # 单流受控分辨率
                'best[height<=720]',                                              # 进一步降级
                'best'                                                            # 最后的兜底
            ]
            
            success = False
            last_error = ""
            
            for format_str in formats_to_try:
                try:
                    logger.debug(f"尝试格式: {format_str}")
                    ret, out, err = await run_yt_dlp(format_str)
                    if ret == 0:
                        success = True
                        logger.info(f"下载成功，使用格式: {format_str}")
                        break
                    else:
                        error_msg = (err or b'').decode('utf-8', errors='ignore')
                        last_error = error_msg
                        logger.warning(f"格式 {format_str} 失败: {error_msg}")
                        
                        # 如果是格式不可用，继续尝试下一个格式
                        if any(keyword in error_msg.lower() for keyword in [
                            'requested format is not available',
                            'no video formats found',
                            'format not available'
                        ]):
                            continue
                        else:
                            # 其他错误，直接抛出
                            raise Exception(f"yt-dlp下载失败: {error_msg}")
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"格式 {format_str} 异常: {e}")
                    continue
            
            # 若预设回退全部失败，且失败原因为“格式不可用”，进行一次格式探测并按可用清单重试（仅针对当前视频）
            if not success:
                lower_err = (last_error or '').lower()
                if any(k in lower_err for k in [
                    'requested format is not available', 'no video formats found', 'format not available'
                ]):
                    logger.info("预设格式均失败，尝试进行一次格式探测(-J)以选择可用格式")
                    async def probe_formats() -> Optional[dict]:
                        cmd = base_cmd + common_args + [
                            '-J',  # 输出JSON包含可用formats
                            '--no-warnings',
                            url
                        ]
                        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        try:
                            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.download_cmd_timeout)
                        except asyncio.TimeoutError:
                            try:
                                proc.terminate(); await asyncio.wait_for(proc.wait(), timeout=5.0)
                            except asyncio.TimeoutError:
                                proc.kill()
                            return None
                        if proc.returncode != 0:
                            logger.warning(f"格式探测失败: {(err or b'').decode('utf-8', errors='ignore')}")
                            return None
                        try:
                            data = json.loads((out or b'{}').decode('utf-8', errors='ignore'))
                            return data
                        except Exception as e:
                            logger.warning(f"解析格式探测JSON失败: {e}")
                            return None

                    probe = await probe_formats()
                    picked_format = None
                    if probe and isinstance(probe.get('formats'), list):
                        fmts = probe['formats']
                        # 选择策略：
                        # 1) 优先选不高于 max_h 的视频轨最高高度 + 最佳音轨
                        # 2) 若无≤max_h的视频，则选最高高度的视频 + 最佳音轨
                        # 3) 若无分离轨，仅有单轨best，选择扩展名mp4优先
                        def parse_int(x, default=0):
                            try:
                                return int(x) if x is not None else default
                            except Exception:
                                return default
                        # 拆分视频轨/音频轨/单轨
                        videos_f = [f for f in fmts if f.get('vcodec', 'none') != 'none']
                        audios_f = [f for f in fmts if f.get('acodec', 'none') != 'none' and f.get('vcodec', 'none') == 'none']
                        single_f = [f for f in fmts if f.get('vcodec', 'none') != 'none' and f.get('acodec', 'none') != 'none']
                        # 目标上限与排序
                        try:
                            max_h = int(os.getenv('MAX_HEIGHT', '1080'))
                            if max_h <= 0:
                                max_h = 1080
                        except Exception:
                            max_h = 1080
                        # 按高度排序视频轨
                        videos_f.sort(key=lambda f: (parse_int(f.get('height')), parse_int(f.get('tbr'))), reverse=True)
                        audios_f.sort(key=lambda f: (parse_int(f.get('abr')), parse_int(f.get('tbr'))), reverse=True)
                        single_f.sort(key=lambda f: (parse_int(f.get('height')), parse_int(f.get('tbr'))), reverse=True)

                        def pick_audio():
                            return audios_f[0] if audios_f else None

                        def pick_video_le_cap():
                            for f in videos_f:
                                if parse_int(f.get('height')) <= max_h:
                                    return f
                            return None

                        v = pick_video_le_cap() or (videos_f[0] if videos_f else None)
                        a = pick_audio()
                        if v and a and v.get('format_id') and a.get('format_id'):
                            picked_format = f"{v['format_id']}+{a['format_id']}"
                        elif single_f:
                            # 单轨兜底（尽量选mp4）
                            mp4_single = [f for f in single_f if str(f.get('ext')).lower() == 'mp4']
                            s = mp4_single[0] if mp4_single else single_f[0]
                            if s.get('format_id'):
                                picked_format = s['format_id']

                    if picked_format:
                        logger.info(f"格式探测选择: {picked_format}，尝试重新下载")
                        ret, out, err = await run_yt_dlp(picked_format)
                        if ret == 0:
                            success = True
                        else:
                            last_error = (err or b'').decode('utf-8', errors='ignore')
                            logger.warning(f"探测后下载仍失败: {last_error}")
                    else:
                        logger.warning("未能从格式探测中选出有效格式")

            if not success:
                raise Exception(f"所有格式尝试失败，最后错误: {last_error}")
                
            # 下载成功，标记队列完成
            try:
                if job_id:
                    await request_queue.mark_done(job_id)
            except Exception:
                pass
                
        except Exception as e:
            # 下载失败，标记队列失败
            try:
                if job_id:
                    await request_queue.mark_failed(job_id, str(e))
            except Exception:
                pass
            raise
        finally:
            if cookies_path and os.path.exists(cookies_path):
                try:
                    os.remove(cookies_path)
                except Exception:
                    pass
        
        # 查找下载的视频文件（在订阅目录下）
        video_file = subscription_dir / f"{base_filename}.mp4"
        if not video_file.exists():
            # 先尝试常见扩展名
            for ext in ['.webm', '.mkv', '.flv', '.m4v', '.ts', '.mov', '.avi']:
                alt_file = subscription_dir / f"{base_filename}{ext}"
                if alt_file.exists():
                    video_file = alt_file
                    break
        
        if not video_file.exists():
            # 通配回退：处理 base_filename.fXXXX.mp4 等情况
            candidates = []
            try:
                for p in subscription_dir.glob(f"{base_filename}.*"):
                    name = p.name
                    # 过滤临时/非媒体文件
                    if name.endswith(('.part', '.ytdl', '.temp', '.aria2', '.info.json', '.jpg', '.png', '.nfo')):
                        continue
                    if p.is_file():
                        candidates.append(p)
            except Exception:
                pass
            if candidates:
                # 选择最大文件作为视频文件
                candidates.sort(key=lambda x: x.stat().st_size if x.exists() else 0, reverse=True)
                picked = candidates[0]
                logger.info(f"产物查找回退匹配: 选择 {picked}")
                video_file = picked
        
        if not video_file.exists():
            # 打印该前缀下的文件，便于排查
            try:
                listing = [p.name for p in subscription_dir.glob(f"{base_filename}.*")]
                logger.error(f"未找到下载产物，前缀匹配文件: {listing}")
            except Exception:
                pass
            raise Exception("下载的视频文件未找到")

        # 若文件名包含格式后缀（例如 .f100113.mp4），规范化重命名为 base_filename.mp4（如无冲突）
        try:
            expected = subscription_dir / f"{base_filename}.mp4"
            if video_file.suffix.lower() == '.mp4' and video_file.name != expected.name:
                # 仅当目标不存在时重命名
                if not expected.exists():
                    logger.info(f"规范化重命名: {video_file.name} -> {expected.name}")
                    video_file.rename(expected)
                    video_file = expected
        except Exception as e:
            logger.warning(f"重命名标准化失败，保持原名: {video_file} - {e}")

        # 兜底：若mp4无音轨，尝试与同目录同名的m4a合并
        try:
            self._ensure_audio_embedded(video_file)
        except Exception as e:
            logger.warning(f"下载后音轨校验/合并过程出现异常，跳过: {e}")

        return video_file
    
    async def _create_nfo_file(self, video_info: Dict[str, Any], base_filename: str, subscription_dir: Path):
        """创建增强版NFO文件（与V5一致）"""
        nfo_path = subscription_dir / f"{base_filename}.nfo"
        # 已存在则直接跳过，避免重复刷新
        try:
            if nfo_path.exists():
                logger.debug(f"NFO已存在，跳过生成: {nfo_path}")
                return
        except Exception:
            # 安全兜底：异常时不中断写入
            pass
        title = video_info.get('title', 'Unknown')
        video_id = video_info.get('id', '')
        uploader = video_info.get('uploader', '')
        upload_date = video_info.get('upload_date', '')
        duration = video_info.get('duration', 0) or 0
        description = video_info.get('description', '') or ''
        tags = video_info.get('tags', []) or []
        view_count = video_info.get('view_count', 0) or 0
        like_count = video_info.get('like_count', 0) or 0
        webpage_url = video_info.get('webpage_url', video_info.get('url', ''))

        # 格式化
        formatted_date = self._format_date(upload_date)
        runtime_minutes = duration // 60 if duration else 0
        clean_desc = self._escape_xml(description)
        if len(clean_desc) > 500:
            clean_desc = clean_desc[:500] + '...'

        lines = [
            '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
            '<movie>',
            f'  <title>{self._escape_xml(title)}</title>',
            f'  <sorttitle>{self._escape_xml(title)}</sorttitle>',
            f'  <plot>{clean_desc}</plot>',
            f'  <outline>{clean_desc}</outline>',
            f'  <runtime>{runtime_minutes}</runtime>',
            f'  <year>{upload_date[:4] if upload_date else ""}</year>',
            f'  <studio>{self._escape_xml(uploader)}</studio>',
            f'  <director>{self._escape_xml(uploader)}</director>',
            f'  <credits>{self._escape_xml(uploader)}</credits>',
            f'  <uniqueid type="bilibili">{self._escape_xml(video_id)}</uniqueid>',
            f'  <dateadded>{formatted_date} 00:00:00</dateadded>',
            f'  <premiered>{formatted_date}</premiered>',
            f'  <playcount>{view_count}</playcount>',
            f'  <userrating>{min(10, like_count // 1000) if like_count else 0}</userrating>',
            f'  <trailer>{self._escape_xml(webpage_url)}</trailer>',
        ]
        for tag in tags[:10]:
            lines.append(f'  <tag>{self._escape_xml(tag)}</tag>')
        lines.extend([
            '  <fileinfo>',
            '    <streamdetails>',
            '      <video>',
            '        <codec>h264</codec>',
            '        <aspect>16:9</aspect>',
            '        <width>1920</width>',
            '        <height>1080</height>',
            '      </video>',
            '      <audio>',
            '        <codec>aac</codec>',
            '        <language>zh</language>',
            '        <channels>2</channels>',
            '      </audio>',
            '    </streamdetails>',
            '  </fileinfo>',
            '</movie>'
        ])

        with open(nfo_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        logger.info(f"已生成NFO: {nfo_path}")
    
    def _create_subscription_directory(self, subscription: Subscription) -> str:
        """创建订阅目录，返回目录路径"""
        from .models import Subscription
        
        # 根据订阅类型确定目录名
        if subscription.type == 'collection':
            # 合集订阅：优先使用订阅名称
            dir_name = self._sanitize_filename(subscription.name or "")
            if not dir_name:
                # 兜底，避免落在根目录
                dir_name = self._sanitize_filename(f"合集订阅_{subscription.id}")
        elif subscription.type == 'keyword':
            # 关键词订阅：使用统一前缀“关键词：”
            base = subscription.keyword or subscription.name or "关键词订阅"
            dir_name = self._sanitize_filename(f"关键词：{base}")
        elif subscription.type == 'uploader':
            # UP主订阅：使用统一前缀“up 主：”；无名称时回退为 uploader_id
            base = subscription.name or getattr(subscription, 'uploader_id', None) or "UP主订阅"
            dir_name = self._sanitize_filename(f"up 主：{base}")
        else:
            # 其他类型：使用订阅名称，必要时兜底
            dir_name = self._sanitize_filename(subscription.name or "")
            if not dir_name:
                dir_name = self._sanitize_filename(f"订阅_{subscription.id}")
        
        # 创建目录路径
        subscription_dir = os.path.join(self.output_dir, dir_name)
        os.makedirs(subscription_dir, exist_ok=True)
        
        logger.info(f"创建订阅目录: {subscription_dir}")
        return subscription_dir
    
    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名，移除非法字符"""
        # 移除或替换非法字符
        illegal_chars = r'[<>:"/\\|?*]'
        filename = re.sub(illegal_chars, '_', filename)
        
        # 移除前后空格和点
        filename = filename.strip(' .')
        
        # 限制长度
        if len(filename) > 100:
            filename = filename[:100]
        
        # 保证非空，避免回退到纯ID
        if not filename:
            return 'untitled'
        return filename
    
    def _parse_upload_date(self, date_str: str) -> Optional[datetime]:
        """解析上传日期"""
        if not date_str:
            return None
        
        try:
            # yt-dlp通常返回YYYYMMDD格式
            if len(date_str) == 8 and date_str.isdigit():
                return datetime.strptime(date_str, '%Y%m%d')
            return datetime.fromisoformat(date_str)
        except:
            return None
    
    def _extract_year(self, date_str: str) -> str:
        """提取年份"""
        date_obj = self._parse_upload_date(date_str)
        return str(date_obj.year) if date_obj else ""
    
    def _format_date(self, date_str: str) -> str:
        """格式化日期为YYYY-MM-DD"""
        date_obj = self._parse_upload_date(date_str)
        return date_obj.strftime('%Y-%m-%d') if date_obj else ""
    
    def _escape_xml(self, text: str) -> str:
        """转义XML特殊字符"""
        if not text:
            return ""
        
        text = str(text)
        text = text.replace('&', '&amp;')
        text = text.replace('<', '&lt;')
        text = text.replace('>', '&gt;')
        text = text.replace('"', '&quot;')
        text = text.replace("'", '&apos;')
        return text

    # ============== 媒体处理兜底：确保视频含音轨 ==============
    def _has_audio(self, mp4_path: Path) -> bool:
        try:
            cmd = ['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'csv=p=0', str(mp4_path)]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return any(line.strip() for line in (proc.stdout or '').splitlines())
        except Exception:
            return True  # 保守：探测失败视为有音轨，避免误操作

    def _pick_m4a_candidate(self, video_file: Path) -> Optional[Path]:
        stem = video_file.stem
        parent = video_file.parent
        # 兼容 base.mp4 与 base.fXXXX.mp4 两种情况，尝试多种候选名
        candidates: List[Path] = []
        # 1) 同名 .m4a
        candidates.append(parent / f"{stem}.m4a")
        # 2) 去除 .fXXXXX 后缀后的 .m4a
        try:
            import re
            m = re.search(r"\.f\d{3,6}$", stem)
            if m:
                base = stem[:m.start()]
                candidates.append(parent / f"{base}.m4a")
        except Exception:
            pass
        # 3) 目录下同前缀的 .m4a（挑选最大/最新）
        try:
            more = [p for p in parent.glob("*.m4a") if p.exists()]
            more.sort(key=lambda p: (p.stat().st_size, p.name), reverse=True)
            candidates.extend(more[:3])
        except Exception:
            pass
        for p in candidates:
            if p and p.exists():
                return p
        return None

    def _ensure_audio_embedded(self, video_file: Path):
        if video_file.suffix.lower() != '.mp4':
            return
        if self._has_audio(video_file):
            return
        m4a = self._pick_m4a_candidate(video_file)
        if not m4a:
            logger.info(f"未找到可用的音频文件用于合并: {video_file}")
            return
        tmp = video_file.with_suffix('').as_posix() + '.__mux.tmp.mp4'
        bak = str(video_file) + '.bak'
        def ff(cmd: List[str]) -> int:
            return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE).returncode
        logger.info(f"尝试为无音轨视频合并音频: video='{video_file.name}', audio='{m4a.name}'")
        # ffmpeg路径
        ffmpeg_path = os.getenv('FFMPEG_PATH')
        ffmpeg_bin = ffmpeg_path if (ffmpeg_path and os.path.exists(ffmpeg_path)) else 'ffmpeg'

        # 先尝试流复制
        rc = ff([ffmpeg_bin, '-y', '-v', 'error', '-i', str(video_file), '-i', str(m4a),
                 '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'copy', '-movflags', '+faststart', tmp])
        if rc != 0:
            # 回退AAC转码
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except OSError: pass
            rc = ff([ffmpeg_bin, '-y', '-v', 'error', '-i', str(video_file), '-i', str(m4a),
                     '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', tmp])
        if rc != 0 or (not os.path.exists(tmp)):
            logger.warning(f"自动合并失败，保留原文件: {video_file}")
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except OSError: pass
            return
        # 原子替换
        try:
            if os.path.exists(bak):
                os.remove(bak)
            os.rename(str(video_file), bak)
            os.rename(tmp, str(video_file))
            logger.info(f"自动合并音轨成功: {video_file.name}")
        except Exception as e:
            logger.warning(f"替换合并结果失败，尝试回滚: {e}")
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except OSError: pass
            if (not os.path.exists(str(video_file))) and os.path.exists(bak):
                try: os.rename(bak, str(video_file))
                except Exception:
                    pass

# 全局下载器实例
downloader = BilibiliDownloaderV6()
