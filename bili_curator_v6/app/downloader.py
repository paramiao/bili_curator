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

# UA 策略（与 API 层保持一致语义）：
# - requires_cookie=True 时使用稳定 UA
# - requires_cookie=False 时可使用随机 UA（此文件主要走 Cookie 通道）
_UA_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
]

def get_user_agent(requires_cookie: bool) -> str:
    try:
        if requires_cookie:
            return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        return random.choice(_UA_POOL)
    except Exception:
        return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'

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
    
    async def download_collection(self, subscription_id: int, db: Session) -> Dict[str, Any]:
        """下载合集"""
        subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
        if not subscription:
            raise ValueError(f"订阅 {subscription_id} 不存在")
        
        logger.info(f"开始下载合集: {subscription.name}")

        # 订阅目录（先计算，便于缓存列表）
        subscription_dir = Path(self._create_subscription_directory(subscription))

        # 获取合集视频列表（失败时回退到本地缓存）
        # 同一订阅加互斥，避免与 expected-total/parse 并发外网请求
        sub_lock = get_subscription_lock(subscription.id)
        cache_path = subscription_dir / 'playlist.json'
        try:
            async with sub_lock:
                video_list = await self._get_collection_videos(subscription.url, db, subscription_id=subscription.id)
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
        
        # 扫描已有文件（限定在当前订阅目录，避免跨合集干扰），数据库仍做全局去重
        existing_videos = self._scan_existing_files(db, subscription_dir=subscription_dir)
        
        # 过滤需要下载的视频
        new_videos = []
        for video_info in video_list:
            video_id = video_info.get('id')
            if video_id not in existing_videos:
                new_videos.append(video_info)
        
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
                except Exception:
                    pass
        
        # 更新订阅检查时间
        subscription.last_check = datetime.now()
        db.commit()
        
        return {
            'subscription_id': subscription_id,
            'total_videos': len(video_list),
            'new_videos': len(new_videos),
            'download_results': download_results
        }
    

    async def _get_collection_videos(self, collection_url: str, db: Session, subscription_id: Optional[int] = None) -> List[Dict]:
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

            # 公共 yt-dlp 参数（与V4对齐）：UA/Referer/重试/忽略警告/轻睡眠
            common_args = [
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
                '--no-download',
                '--cookies', cookies_path,
            ]

            # 允许通过环境变量传入 extractor-args（例如为 bilibili 指定解析策略）
            extractor_args = os.getenv('YT_DLP_EXTRACTOR_ARGS')
            if extractor_args and isinstance(extractor_args, str) and extractor_args.strip():
                common_args += ['--extractor-args', extractor_args.strip()]

            async def run_cmd(args):
                async with yt_dlp_semaphore:
                    proc = await asyncio.create_subprocess_exec(
                        *args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    out, err = await proc.communicate()
                    return proc.returncode, out, err

            videos: List[Dict] = []
            last_err = None

            # 优先：手动分页抓取，避免一次性请求过大触发风控
            # 分段大小、重试与退避由环境变量控制
            chunk_size = self.list_chunk_size
            max_chunks = 50  # 安全上限（最多约 5000 条）
            chunk_idx = 0
            while chunk_idx < max_chunks:
                start = chunk_idx * chunk_size + 1
                end = start + chunk_size - 1
                cmd_chunk = common_args + [
                    '--dump-json', '--flat-playlist',
                    '--playlist-items', f'{start}-{end}',
                    collection_url
                ]

                # 每段重试次数与退避区间
                attempt = 0
                out, err = b'', b''
                rc = 0
                while attempt < self.list_retry:
                    rc, out, err = await run_cmd(cmd_chunk)
                    if rc == 0:
                        break
                    attempt += 1
                    last_err = (err.decode('utf-8', errors='ignore') or '').strip()
                    logger.warning(f"分页抓取 {start}-{end} 失败(rc={rc}) 第{attempt}次: {last_err}")
                    try:
                        await asyncio.sleep(random.uniform(self.list_backoff_min, self.list_backoff_max))
                    except Exception:
                        pass
                if rc != 0:
                    # 当前段最终失败，结束分页，转入后续回退策略
                    break
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
                # 本段不足 chunk_size，说明已到结尾
                if count_this < chunk_size:
                    break
                chunk_idx += 1
                # 段与段之间轻量延时（可配置），降低风控风险
                try:
                    await asyncio.sleep(random.uniform(self.list_page_gap_min, self.list_page_gap_max))
                except Exception:
                    pass

            # 回退：若分页抓取失败或未取到任何内容，走原有 A/B/C 策略
            if not videos:
                # A. 扁平化单次JSON
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
                    except Exception as e:
                        last_err = e
                else:
                    last_err = err.decode('utf-8', errors='ignore')

            if not videos:
                # B. 全量JSON（-J）
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
                    except Exception as e:
                        last_err = e
                else:
                    last_err = err.decode('utf-8', errors='ignore')

            if not videos:
                # C. 行式输出兜底
                cmd_c = common_args + ['--dump-json', '--flat-playlist', collection_url]
                rc, out, err = await run_cmd(cmd_c)
                if rc == 0:
                    for line in (out.decode('utf-8', errors='ignore') or '').strip().split('\n'):
                        if not line.strip():
                            continue
                        try:
                            info = json.loads(line)
                            if isinstance(info, dict):
                                videos.append(info)
                        except json.JSONDecodeError:
                            continue
                else:
                    last_err = err.decode('utf-8', errors='ignore')

            if not videos:
                err_msg = f"yt-dlp未能解析到视频列表; last_error={last_err!s}"
                logger.error(f"yt-dlp获取视频列表失败: {last_err}")
                raise Exception(f"获取视频列表失败: {err_msg}")

            cookie_manager.update_cookie_usage(db, cookie.id)
            logger.info(f"获取到 {len(videos)} 个视频")
            # 成功路径：标记队列完成，释放信号量/计数
            try:
                if job_id:
                    await request_queue.mark_done(job_id)
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
            if cookies_path and os.path.exists(cookies_path):
                try:
                    os.remove(cookies_path)
                except Exception:
                    pass
    
    def _scan_existing_files(self, db: Session, subscription_dir: Optional[Path] = None) -> Dict[str, str]:
        """扫描已有文件，构建视频ID映射
        - 数据库：全局已下载视频（任何订阅）
        - 文件系统：仅扫描当前订阅目录（若提供），避免跨合集误判与性能问题
        """
        existing_videos = {}
        
        # 从数据库获取已下载的视频
        downloaded_videos = db.query(Video).filter(Video.downloaded == True).all()
        for video in downloaded_videos:
            existing_videos[video.bilibili_id] = video.video_path
        
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
                # 获取Cookie
                cookie = cookie_manager.get_available_cookie(db)
                if not cookie:
                    raise Exception("没有可用的Cookie")
                
                await rate_limiter.wait()

                # 如果标题缺失或为 Unknown，则补抓元数据以保证命名正确
                if not title or str(title).strip().lower() in ('', 'unknown'):
                    try:
                        detail_url = video_info.get('webpage_url') or video_info.get('url') or f"https://www.bilibili.com/video/{video_id}"
                        meta = await self._fetch_video_metadata(detail_url, cookie)
                        if meta:
                            # 回填关键信息
                            video_info.update(meta)
                            title = meta.get('title') or title
                    except Exception as e:
                        logger.warning(f"获取视频元数据失败，使用回退命名: {video_id} - {e}")
                        if (not title) or (str(title).strip().lower() in ('', 'unknown')):
                            title = str(video_id)
                
                # 生成安全的文件名
                safe_title = self._sanitize_filename(title)
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
                
                # 下载视频
                video_path = await self._download_with_ytdlp(
                    video_info.get('url', f"https://www.bilibili.com/video/{video_id}"),
                    base_filename,
                    subscription_dir,
                    subscription_id,
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
                stdout, stderr = await process.communicate()
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
    
    async def _download_with_ytdlp(self, url: str, base_filename: str, subscription_dir: Path, subscription_id: int, cookie, task_id: int, db: Session) -> Path:
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
                    requires_cookie=True
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
                    out, err = await proc.communicate()
                return proc.returncode, out, err

            # 首选 1080 限制，失败则回退到通用最佳
            try:
                ret, out, err = await run_yt_dlp('bestvideo*[height<=1080]+bestaudio/best')
                if ret != 0 and b'Requested format is not available' in err:
                    ret, out, err = await run_yt_dlp('bv+ba/b')
                if ret != 0:
                    error_msg = (err or b'').decode('utf-8', errors='ignore')
                    raise Exception(f"yt-dlp下载失败: {error_msg}")
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
            # 关键词订阅：使用关键词
            base = subscription.keyword or subscription.name or "关键词订阅"
            dir_name = self._sanitize_filename(f"关键词_{base}")
        elif subscription.type == 'uploader':
            # UP主订阅：使用UP主名称/ID
            base = subscription.name or getattr(subscription, 'uploader_id', None) or "UP主订阅"
            dir_name = self._sanitize_filename(f"UP主_{base}")
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
