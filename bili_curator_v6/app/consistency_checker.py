"""
本地目录与数据库一致性检查服务
以本地目录为准，同步数据库状态
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .models import Video, get_db

logger = logging.getLogger(__name__)


class ConsistencyChecker:
    """本地目录与数据库一致性检查器"""
    
    def __init__(self, download_path: str = None):
        self.download_path = Path(download_path or os.getenv('DOWNLOAD_PATH', '/app/downloads'))
        
    def check_and_sync(self, db: Session = None) -> Dict[str, int]:
        """执行完整的一致性检查和同步（包含自动导入）"""
        if db is None:
            db = next(get_db())
            
        logger.info("开始执行本地目录与数据库一致性检查...")
        
        stats = {
            'total_db_records': 0,
            'files_found': 0,
            'files_missing': 0,
            'records_updated': 0,
            'records_cleaned': 0,
            'orphan_files': 0,
            'imported_videos': 0
        }
        
        try:
            # 1. 首先导入本地目录中的新视频文件
            imported_count = self._import_local_videos(db)
            stats['imported_videos'] = imported_count
            
            # 1.5 基于目录名进行自动关联订阅（修正 subscription_id）
            assoc_updated = self._associate_by_directory(db)
            stats['records_updated'] += assoc_updated
            
            # 2. 统计数据库记录总数
            stats['total_db_records'] = db.query(Video).count()
            
            # 3. 检查数据库记录对应的文件是否存在
            missing_files, updated_records = self._check_db_files(db)
            stats['files_missing'] = len(missing_files)
            stats['records_updated'] += updated_records
            
            # 4. 清理已删除文件的记录
            cleaned_records = self._clean_missing_files(db, missing_files)
            stats['records_cleaned'] = cleaned_records
            
            # 5. 扫描目录，统计实际文件数
            stats['files_found'] = self._count_video_files()
            
            # 6. 查找孤立文件（存在于目录但不在数据库中）
            stats['orphan_files'] = self._count_orphan_files(db)
            
            db.commit()
            
            logger.info(f"一致性检查完成: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"一致性检查失败: {e}")
            db.rollback()
            raise
            
    def _check_db_files(self, db: Session) -> Tuple[List[int], int]:
        """检查数据库记录对应的文件是否存在"""
        missing_files = []
        updated_records = 0
        
        # 分批处理，避免内存占用过大
        batch_size = 500
        offset = 0
        
        while True:
            videos = (
                db.query(Video)
                .filter(Video.video_path.isnot(None))
                .offset(offset)
                .limit(batch_size)
                .all()
            )
            
            if not videos:
                break
                
            for video in videos:
                if video.video_path:
                    file_path = Path(video.video_path)
                    if not file_path.exists():
                        missing_files.append(video.id)
                        # 更新数据库状态：文件已丢失
                        video.downloaded = False
                        updated_records += 1
                    elif not video.downloaded:
                        # 文件存在但状态为未下载，修正状态
                        video.downloaded = True
                        updated_records += 1
                        
            offset += batch_size
            
            # 每批次提交一次
            if updated_records > 0:
                db.commit()
                
        return missing_files, updated_records
        
    def _clean_missing_files(self, db: Session, missing_file_ids: List[int]) -> int:
        """清理已删除文件的记录（可选：删除记录或仅标记）"""
        if not missing_file_ids:
            return 0
            
        # 策略1: 仅清空路径，保留记录用于重新下载
        cleaned = 0
        for video_id in missing_file_ids:
            video = db.query(Video).filter(Video.id == video_id).first()
            if video:
                video.video_path = None
                video.downloaded = False
                cleaned += 1
                
        return cleaned
        
    def _count_video_files(self) -> int:
        """统计下载目录中的视频文件数量"""
        if not self.download_path.exists():
            return 0
            
        video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.m4v'}
        count = 0
        
        try:
            for file_path in self.download_path.rglob('*'):
                if file_path.is_file() and file_path.suffix.lower() in video_extensions:
                    count += 1
        except Exception as e:
            logger.warning(f"扫描目录失败: {e}")
            
        return count
    
    def _import_local_videos(self, db: Session) -> int:
        """导入本地目录中的视频文件到数据库"""
        if not self.download_path.exists():
            logger.warning(f"下载目录不存在: {self.download_path}")
            return 0
            
        imported_count = 0
        
        # 查找所有JSON元数据文件
        json_files = list(self.download_path.rglob("*.json"))
        logger.info(f"找到 {len(json_files)} 个JSON元数据文件")
        
        # 获取数据库中已有的视频路径与bilibili_id，避免重复导入
        existing_paths = set()
        existing_ids = set()
        for vp, in db.query(Video.video_path).filter(Video.video_path.isnot(None)).all():
            if vp:
                existing_paths.add(str(Path(vp).resolve()))
        for bid, in db.query(Video.bilibili_id).all():
            if bid:
                existing_ids.add(bid)
        
        for json_file in json_files:
            try:
                # 读取JSON元数据
                with open(json_file, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                
                # 查找对应的视频文件
                video_file = self._find_video_file(json_file)
                if not video_file or not video_file.exists():
                    continue
                
                # 检查是否已存在相同文件路径
                video_path_resolved = str(video_file.resolve())
                if video_path_resolved in existing_paths:
                    continue
                
                # 检查是否已存在相同 bilibili_id
                bilibili_id = metadata.get('id', '') or metadata.get('bvid') or metadata.get('bv_id') or ''
                if not bilibili_id:
                    # 没有ID无法去重，跳过以避免脏数据
                    logger.warning(f"元数据缺少 bilibili_id，跳过: {json_file}")
                    continue

                if bilibili_id in existing_ids:
                    # 尝试为已存在记录补齐路径与下载状态
                    try:
                        with db.no_autoflush:
                            existing = db.query(Video).filter(Video.bilibili_id == bilibili_id).first()
                        if existing:
                            if not existing.video_path:
                                existing.video_path = str(video_file)
                            existing.downloaded = True
                            # 不计入imported_count，仅修复记录
                            existing_paths.add(video_path_resolved)
                        continue
                    except Exception as e:
                        logger.error(f"更新已存在记录失败({bilibili_id}): {e}")
                        continue

                # 创建或获取订阅
                subscription = self._get_or_create_subscription(db, metadata)
                
                # 创建视频记录
                video = Video(
                    bilibili_id=bilibili_id,
                    title=metadata.get('title', ''),
                    description=metadata.get('description', ''),
                    duration=metadata.get('duration'),
                    upload_date=self._parse_upload_date(metadata.get('upload_date')),
                    uploader=metadata.get('uploader', ''),
                    uploader_id=metadata.get('uploader_id', ''),
                    view_count=metadata.get('view_count'),
                    video_path=str(video_file),
                    downloaded=True,
                    subscription_id=subscription.id if subscription else None
                )
                
                try:
                    db.add(video)
                    imported_count += 1
                    existing_paths.add(video_path_resolved)
                    existing_ids.add(bilibili_id)
                except IntegrityError as ie:
                    # 回滚并跳过该记录，避免打断后续导入
                    db.rollback()
                    logger.warning(f"唯一性冲突，跳过 {bilibili_id}: {ie}")
                    continue
                
                # 每100个提交一次
                if imported_count % 100 == 0:
                    db.commit()
                    logger.info(f"已导入 {imported_count} 个视频...")
                    
            except Exception as e:
                # 其他异常：回滚以保证会话可继续
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.error(f"导入视频文件 {json_file} 失败: {e}")
                continue
        
        if imported_count > 0:
            db.commit()
            logger.info(f"成功导入 {imported_count} 个视频文件")
        
        return imported_count
    
    def _associate_by_directory(self, db: Session) -> int:
        """按目录名将视频自动关联到对应的合集订阅。
        规则：下载根目录下的一级目录名与某个订阅（type=collection）的 name 完全相同，则该目录内的视频归属该订阅。
        返回：更新的记录数。
        """
        try:
            from .models import Subscription
            download_root = self.download_path.resolve()
            # 建立 目录名 -> 订阅ID 映射（仅 collection 类型）
            subs = db.query(Subscription).filter(Subscription.type == 'collection').all()
            name_to_sid = {s.name: s.id for s in subs if s and s.name}
            if not name_to_sid:
                return 0
            updated = 0
            # 仅处理有路径的视频
            for v in db.query(Video).filter(Video.video_path.isnot(None)).yield_per(500):
                try:
                    vp = Path(v.video_path).resolve()
                    first_dir = None
                    try:
                        rel = vp.relative_to(download_root)
                        first_dir = rel.parts[0] if len(rel.parts) > 0 else None
                    except Exception:
                        first_dir = None
                    if not first_dir:
                        continue
                    target_sid = name_to_sid.get(first_dir)
                    if target_sid and v.subscription_id != target_sid:
                        v.subscription_id = target_sid
                        updated += 1
                except Exception:
                    continue
            if updated:
                db.commit()
            if updated:
                logger.info(f"按目录名自动关联订阅：更新 {updated} 条记录")
            return updated
        except Exception as e:
            logger.warning(f"目录关联订阅步骤失败：{e}")
            try:
                db.rollback()
            except Exception:
                pass
            return 0

    def _find_video_file(self, json_file: Path) -> Optional[Path]:
        """根据JSON文件查找对应的视频文件"""
        base_name = json_file.stem
        video_dir = json_file.parent
        
        # 常见视频格式
        video_extensions = ['.mp4', '.mkv', '.webm', '.flv', '.avi']
        
        for ext in video_extensions:
            video_file = video_dir / f"{base_name}{ext}"
            if video_file.exists():
                return video_file
        
        return None
    
    def _get_or_create_subscription(self, db: Session, metadata: dict) -> Optional['Subscription']:
        """根据元数据获取或创建订阅"""
        from .models import Subscription
        
        # 尝试从元数据中提取订阅信息
        uploader = metadata.get('uploader', '')
        channel_id = metadata.get('channel_id', '')
        
        if not uploader and not channel_id:
            return None
        
        # 查找现有订阅
        subscription = None
        if channel_id:
            subscription = db.query(Subscription).filter(Subscription.url.contains(channel_id)).first()
        
        if not subscription and uploader:
            subscription = db.query(Subscription).filter(Subscription.name == uploader).first()
        
        # 创建新订阅
        if not subscription:
            subscription = Subscription(
                name=uploader or f"频道_{channel_id}",
                url=f"https://space.bilibili.com/{channel_id}" if channel_id else "",
                type="user"
            )
            db.add(subscription)
            db.flush()  # 获取ID
        
        return subscription
    
    def _parse_upload_date(self, date_str: str) -> Optional[datetime]:
        """解析上传日期"""
        if not date_str:
            return None
        
        try:
            # 尝试多种日期格式
            for fmt in ['%Y%m%d', '%Y-%m-%d', '%Y/%m/%d']:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
            return None
        except Exception:
            return None
        
    def _count_orphan_files(self, db: Session) -> int:
        """统计孤立文件数量（存在于目录但不在数据库中）"""
        if not self.download_path.exists():
            return 0
            
        # 获取数据库中所有文件路径
        db_paths = set()
        videos = db.query(Video.video_path).filter(Video.video_path.isnot(None)).all()
        for video in videos:
            if video.video_path:
                db_paths.add(str(Path(video.video_path).resolve()))
                
        # 扫描目录，找出不在数据库中的文件
        video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.m4v'}
        orphan_count = 0
        
        try:
            for file_path in self.download_path.rglob('*'):
                if (file_path.is_file() and 
                    file_path.suffix.lower() in video_extensions and
                    str(file_path.resolve()) not in db_paths):
                    orphan_count += 1
        except Exception as e:
            logger.warning(f"扫描孤立文件失败: {e}")
            
        return orphan_count
        
    def quick_stats(self, db: Session = None) -> Dict[str, int]:
        """快速统计（仅查数据库，性能优化后使用）"""
        if db is None:
            db = next(get_db())
            
        return {
            'total_videos': db.query(Video).count(),
            'downloaded_videos': db.query(Video).filter(Video.downloaded == True).count(),
            'with_path': db.query(Video).filter(Video.video_path.isnot(None)).count(),
            'without_path': db.query(Video).filter(Video.video_path.is_(None)).count(),
        }


# 全局实例
consistency_checker = ConsistencyChecker()


def startup_consistency_check():
    """服务启动时执行的一致性检查"""
    try:
        logger.info("服务启动：执行一致性检查...")
        stats = consistency_checker.check_and_sync()
        
        # 记录关键指标
        if stats['files_missing'] > 0:
            logger.warning(f"发现 {stats['files_missing']} 个文件丢失，已更新数据库状态")
        if stats['orphan_files'] > 0:
            logger.info(f"发现 {stats['orphan_files']} 个孤立文件，可考虑导入")
            
        logger.info("启动时一致性检查完成")
        return stats
        
    except Exception as e:
        logger.error(f"启动时一致性检查失败: {e}")
        # 不阻塞服务启动
        return None


def periodic_consistency_check():
    """定期执行的轻量级一致性检查（可通过定时任务调用）"""
    try:
        logger.info("执行定期一致性检查...")
        stats = consistency_checker.check_and_sync()
        logger.info(f"定期检查完成: {stats}")
        return stats
    except Exception as e:
        logger.error(f"定期一致性检查失败: {e}")
        return None
