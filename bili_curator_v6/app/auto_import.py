"""
自动导入服务 - Docker启动后自动扫描并导入新视频
"""
import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional
from .models import Database, Video, Subscription
from .utils.path_utils import strip_info_suffix, base_name_from_json_path
from .services.subscription_stats import (
    recompute_all_subscriptions,
    recompute_subscription_stats,
)
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

class AutoImportService:
    """自动导入服务"""
    
    def __init__(self, download_dir: str = "/app/downloads"):
        self.download_dir = Path(download_dir)
        self.db = Database()
        # 状态文件位于下载目录，记录上次成功扫描时间戳（秒）
        self.state_file = self.download_dir / ".auto_import_state.json"
    
    def scan_and_import(self) -> dict:
        """扫描目录并导入新视频"""
        logger.info("🔄 开始自动扫描并导入新视频...")
        
        if not self.download_dir.exists():
            logger.warning(f"下载目录不存在: {self.download_dir}")
            return {"imported": 0, "skipped": 0, "errors": 0}
        
        # 递归查找所有JSON文件（支持增量扫描）
        last_scan_ts = self._load_last_scan_ts()
        all_json_files = list(self.download_dir.rglob("*.json"))
        if last_scan_ts:
            json_files = []
            for p in all_json_files:
                try:
                    if p.stat().st_mtime > last_scan_ts:
                        json_files.append(p)
                except Exception:
                    # 读取文件状态失败则跳过该文件
                    continue
            logger.info(f"📄 增量模式：总 {len(all_json_files)}，待处理 {len(json_files)}（last_scan_ts={last_scan_ts}）")
        else:
            json_files = all_json_files
            logger.info(f"📄 首次/全量扫描：找到 {len(json_files)} 个JSON文件")
        
        session = self.db.get_session()
        try:
            imported_count = 0
            skipped_count = 0
            error_count = 0
            
            for json_file in json_files:
                try:
                    result = self._import_video_from_json(json_file, session)
                    if result == "imported":
                        imported_count += 1
                    elif result == "skipped":
                        skipped_count += 1
                    
                    # 每100个提交一次，避免长时间锁定
                    if (imported_count + skipped_count) % 100 == 0:
                        session.commit()
                        logger.info(f"✅ 已处理 {imported_count + skipped_count} 个文件...")
                
                except Exception as e:
                    logger.error(f"处理文件 {json_file} 失败: {e}")
                    error_count += 1
            
            session.commit()
            # 导入完成后刷新所有订阅统计（无法准确定位订阅归属时采用全量刷新）
            try:
                recompute_all_subscriptions(session, touch_last_check=False)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.warning(f"刷新订阅统计失败(自动导入后)：{e}")

            # 成功完成后更新扫描时间
            try:
                self._save_last_scan_ts(datetime.now())
            except Exception as e:
                logger.warning(f"保存增量扫描时间失败：{e}")
            
            result = {
                "imported": imported_count,
                "skipped": skipped_count,
                "errors": error_count
            }
            
            logger.info(f"🎉 自动导入完成: 成功 {imported_count}, 跳过 {skipped_count}, 错误 {error_count}")
            return result
            
        except Exception as e:
            session.rollback()
            logger.error(f"自动导入过程出错: {e}")
            raise
        finally:
            session.close()

    def scan_and_import_for_subscription(self, subscription_id: int) -> dict:
        """仅扫描指定订阅对应目录并导入新视频，最后只重算该订阅统计。
        注意：为避免与全局增量状态耦合，此处不使用 last_scan_ts，固定按订阅目录全量扫描。
        """
        logger.info(f"🔄 [sub={subscription_id}] 开始按订阅目录扫描并导入新视频...")

        session = self.db.get_session()
        try:
            # 定位订阅与其目录
            sub: Optional[Subscription] = session.query(Subscription).filter(Subscription.id == subscription_id).first()
            if not sub:
                logger.warning(f"订阅不存在: {subscription_id}")
                return {"imported": 0, "skipped": 0, "errors": 0}

            sub_dir = self._compute_subscription_dir(sub)
            if not sub_dir.exists():
                logger.info(f"订阅目录不存在或无内容: {sub_dir}")
                return {"imported": 0, "skipped": 0, "errors": 0}

            json_files = list(sub_dir.rglob("*.json"))
            logger.info(f"📄 [sub={subscription_id}] 订阅目录扫描：找到 {len(json_files)} 个JSON文件")

            imported_count = 0
            skipped_count = 0
            error_count = 0

            for json_file in json_files:
                try:
                    result = self._import_video_from_json(json_file, session)
                    if result == "imported":
                        imported_count += 1
                    elif result == "skipped":
                        skipped_count += 1
                    if (imported_count + skipped_count) % 100 == 0:
                        session.commit()
                except Exception as e:
                    logger.error(f"[sub={subscription_id}] 处理文件 {json_file} 失败: {e}")
                    error_count += 1

            # 提交导入
            session.commit()

            # 仅重算该订阅统计
            try:
                recompute_subscription_stats(session, subscription_id, touch_last_check=False)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.warning(f"[sub={subscription_id}] 刷新订阅统计失败(自动导入后)：{e}")

            result = {
                "imported": imported_count,
                "skipped": skipped_count,
                "errors": error_count,
            }
            logger.info(f"🎉 [sub={subscription_id}] 自动导入完成: 成功 {imported_count}, 跳过 {skipped_count}, 错误 {error_count}")
            return result
        except Exception as e:
            session.rollback()
            logger.error(f"[sub={subscription_id}] 自动导入过程出错: {e}")
            raise
        finally:
            session.close()

    def _load_last_scan_ts(self) -> float:
        """读取上次扫描的时间戳（秒）。不存在或解析失败时返回 0。"""
        try:
            if not self.state_file.exists():
                return 0
            import json as _json
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = _json.load(f)
            ts = float(data.get('last_scan_ts', 0))
            return ts if ts > 0 else 0
        except Exception:
            return 0

    def _save_last_scan_ts(self, dt: datetime) -> None:
        """保存本次扫描完成时间戳（秒）。"""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            import json as _json
            with open(self.state_file, 'w', encoding='utf-8') as f:
                _json.dump({'last_scan_ts': dt.timestamp()}, f)
        except Exception:
            # 静默失败，不影响主流程
            pass
    
    def _import_video_from_json(self, json_file: Path, session: Session) -> str:
        """从JSON文件导入单个视频"""
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            # 跳过非标准JSON文件（如某些配置文件）
            if not isinstance(metadata, dict):
                return "skipped"
            
            video_id = metadata.get('id')
            if not video_id:
                return "skipped"
            
            # 检查是否已存在
            existing_video = session.query(Video).filter(
                Video.bilibili_id == video_id
            ).first()
            
            if existing_video:
                return "skipped"
            
            # 查找对应的视频/缩略图文件（统一使用工具函数处理 *.info.json）
            base_name = base_name_from_json_path(json_file)
            video_file = self._find_video_file(json_file.parent, base_name)
            thumbnail_file = self._find_thumbnail_file(json_file.parent, base_name)
            
            # 处理上传日期
            upload_date = self._parse_upload_date(metadata.get('upload_date'))
            
            # 安全获取文件大小与修改时间（避免在 exists 与 stat 之间的竞态，并且只 stat 一次）
            file_size = 0
            downloaded_at = datetime.now()
            if video_file and video_file.exists():
                try:
                    stat_res = video_file.stat()
                    file_size = stat_res.st_size
                    downloaded_at = datetime.fromtimestamp(stat_res.st_mtime)
                except Exception:
                    # 读取文件状态失败则使用默认值
                    pass

            # 创建视频记录
            video = Video(
                bilibili_id=video_id,
                title=metadata.get('title', ''),
                uploader=metadata.get('uploader', ''),
                uploader_id=metadata.get('uploader_id', ''),
                duration=metadata.get('duration', 0),
                upload_date=upload_date,
                description=metadata.get('description', ''),
                tags=json.dumps(metadata.get('tags', []), ensure_ascii=False),
                video_path=str(video_file) if video_file else None,
                json_path=str(json_file),
                thumbnail_path=str(thumbnail_file) if thumbnail_file else None,
                file_size=file_size,
                view_count=metadata.get('view_count', 0),
                downloaded=True,
                downloaded_at=downloaded_at
            )
            
            session.add(video)
            return "imported"
            
        except Exception as e:
            logger.error(f"导入视频 {json_file} 失败: {e}")
            raise
    
    def _find_video_file(self, directory: Path, base_name: str) -> Optional[Path]:
        """查找对应的视频文件"""
        # 再次兜底剥离 .info，确保调用方传参异常时也能匹配
        base_name = strip_info_suffix(base_name)
        video_extensions = ['.mp4', '.mkv', '.flv', '.webm', '.avi']
        
        for ext in video_extensions:
            video_file = directory / f"{base_name}{ext}"
            if video_file.exists():
                return video_file
        
        return None
    
    def _find_thumbnail_file(self, directory: Path, base_name: str) -> Optional[Path]:
        """查找对应的缩略图文件"""
        base_name = strip_info_suffix(base_name)
        thumbnail_extensions = ['.jpg', '.jpeg', '.png', '.webp']
        
        for ext in thumbnail_extensions:
            thumbnail_file = directory / f"{base_name}{ext}"
            if thumbnail_file.exists():
                return thumbnail_file
        
        return None
    
    def _parse_upload_date(self, date_str: str) -> Optional[datetime]:
        """解析上传日期"""
        if not date_str:
            return None
        
        try:
            # yt-dlp通常返回YYYYMMDD格式
            if len(date_str) == 8 and date_str.isdigit():
                return datetime.strptime(date_str, '%Y%m%d')
            # 尝试ISO格式
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except:
            return None
    
    def auto_associate_subscriptions(self) -> dict:
        """自动关联订阅与已导入的视频"""
        logger.info("🔗 开始自动关联订阅与已导入视频...")
        
        session = self.db.get_session()
        try:
            # 获取所有活跃订阅
            subscriptions = session.query(Subscription).filter(
                Subscription.is_active == True
            ).all()
            
            associated_count = 0
            
            for subscription in subscriptions:
                # 根据订阅类型查找匹配的视频
                matching_videos = self._find_matching_videos(subscription, session)
                
                for video in matching_videos:
                    if not video.subscription_id:  # 只关联未关联的视频
                        video.subscription_id = subscription.id
                        associated_count += 1
                
                # 统一通过统计服务刷新该订阅的统计字段
                try:
                    recompute_subscription_stats(session, subscription.id, touch_last_check=False)
                except Exception as e:
                    logger.warning(f"刷新订阅统计失败(自动关联阶段 sub={subscription.id})：{e}")
                
            session.commit()
            
            logger.info(f"🎉 自动关联完成: {associated_count} 个视频已关联到订阅")
            return {"associated": associated_count}
            
        except Exception as e:
            session.rollback()
            logger.error(f"自动关联过程出错: {e}")
            raise
        finally:
            session.close()
    
    def _find_matching_videos(self, subscription: Subscription, session: Session) -> List[Video]:
        """根据订阅类型查找匹配的视频"""
        query = session.query(Video)

        if subscription.type == "uploader" and subscription.uploader_id:
            # UP主订阅：匹配uploader_id
            return query.filter(Video.uploader_id == subscription.uploader_id).all()

        elif subscription.type == "keyword" and subscription.keyword:
            # 关键词订阅：匹配标题或标签
            keyword = subscription.keyword.lower()
            return query.filter(
                Video.title.ilike(f"%{keyword}%")
            ).all()

        elif subscription.type == "collection" and (subscription.name or subscription.url):
            # 合集订阅：按订阅目录匹配（与下载器目录规则一致：/app/downloads/<sanitized(subscription.name)>）
            sub_dir = self._compute_subscription_dir(subscription)
            if sub_dir:
                prefix = str(sub_dir).rstrip('/') + '/'
                # 匹配视频/JSON路径落在该目录下的记录
                return query.filter(
                    (Video.video_path.isnot(None) & Video.video_path.ilike(f"{prefix}%")) |
                    (Video.json_path.isnot(None) & Video.json_path.ilike(f"{prefix}%"))
                ).all()

        return []

    def _compute_subscription_dir(self, subscription: Subscription) -> Optional[Path]:
        """计算订阅对应的目录（与下载器命名保持一致）"""
        base_download = self.download_dir
        name = (getattr(subscription, 'name', None) or '').strip()
        dir_name = self._sanitize_filename(name) if name else None
        if not dir_name:
            # 兜底
            dir_name = self._sanitize_filename(f"订阅_{subscription.id}")
        return base_download / dir_name

    def _sanitize_filename(self, filename: str) -> str:
        import re
        illegal = r'[<>:"/\\|?*]'
        s = re.sub(illegal, '_', filename or '')
        s = s.strip(' .')
        return s[:100] if len(s) > 100 else s

# 全局自动导入服务实例
auto_import_service = AutoImportService()
