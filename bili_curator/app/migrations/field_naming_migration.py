"""
数据模型字段命名统一化迁移
解决 bilibili_id vs video_id 不一致问题
"""
import logging
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect
from typing import Dict, List

from ..models import get_db, DownloadTask, Video

logger = logging.getLogger(__name__)


class FieldNamingMigration:
    """字段命名统一化迁移工具"""
    
    def __init__(self):
        self.migration_steps = [
            self._migrate_download_task_video_id,
            self._verify_bilibili_id_consistency,
            self._create_field_mapping_documentation
        ]
    
    def run_migration(self, db: Session = None) -> Dict[str, any]:
        """执行完整的字段命名迁移"""
        if not db:
            db = next(get_db())
        
        results = {
            "success": True,
            "steps_completed": 0,
            "total_steps": len(self.migration_steps),
            "details": [],
            "errors": []
        }
        
        try:
            for i, step in enumerate(self.migration_steps):
                try:
                    logger.info(f"执行迁移步骤 {i+1}/{len(self.migration_steps)}: {step.__name__}")
                    step_result = step(db)
                    results["details"].append({
                        "step": step.__name__,
                        "result": step_result,
                        "success": True
                    })
                    results["steps_completed"] += 1
                except Exception as e:
                    error_msg = f"迁移步骤 {step.__name__} 失败: {str(e)}"
                    logger.error(error_msg)
                    results["errors"].append(error_msg)
                    results["success"] = False
                    break
            
            if results["success"]:
                db.commit()
                logger.info("字段命名迁移完成")
            else:
                db.rollback()
                logger.error("字段命名迁移失败，已回滚")
                
        except Exception as e:
            db.rollback()
            results["success"] = False
            results["errors"].append(f"迁移过程异常: {str(e)}")
            logger.error(f"迁移过程异常: {str(e)}")
        
        return results
    
    def _migrate_download_task_video_id(self, db: Session) -> Dict[str, any]:
        """迁移DownloadTask表的video_id字段到bilibili_id"""
        
        # 检查表结构
        inspector = inspect(db.bind)
        columns = [col['name'] for col in inspector.get_columns('download_tasks')]
        
        has_video_id = 'video_id' in columns
        has_bilibili_id = 'bilibili_id' in columns
        
        result = {
            "has_video_id": has_video_id,
            "has_bilibili_id": has_bilibili_id,
            "migrated_records": 0,
            "null_bilibili_id_count": 0
        }
        
        if not has_bilibili_id:
            # 如果没有bilibili_id字段，添加它
            logger.info("添加bilibili_id字段到download_tasks表")
            db.execute(text("ALTER TABLE download_tasks ADD COLUMN bilibili_id VARCHAR(50)"))
        
        if has_video_id:
            # 统计需要迁移的记录
            null_bilibili_id = db.execute(text("""
                SELECT COUNT(*) FROM download_tasks 
                WHERE bilibili_id IS NULL AND video_id IS NOT NULL
            """)).scalar()
            
            result["null_bilibili_id_count"] = null_bilibili_id
            
            if null_bilibili_id > 0:
                # 迁移数据：将video_id复制到bilibili_id
                logger.info(f"迁移 {null_bilibili_id} 条记录的video_id到bilibili_id")
                migrated = db.execute(text("""
                    UPDATE download_tasks 
                    SET bilibili_id = video_id 
                    WHERE bilibili_id IS NULL AND video_id IS NOT NULL
                """)).rowcount
                
                result["migrated_records"] = migrated
                logger.info(f"成功迁移 {migrated} 条记录")
        
        # 验证迁移结果
        remaining_null = db.execute(text("""
            SELECT COUNT(*) FROM download_tasks WHERE bilibili_id IS NULL
        """)).scalar()
        
        result["remaining_null_bilibili_id"] = remaining_null
        
        if remaining_null > 0:
            logger.warning(f"仍有 {remaining_null} 条记录的bilibili_id为空")
        
        return result
    
    def _verify_bilibili_id_consistency(self, db: Session) -> Dict[str, any]:
        """验证bilibili_id字段的一致性"""
        
        result = {
            "download_tasks_total": 0,
            "download_tasks_with_bilibili_id": 0,
            "videos_total": 0,
            "videos_with_bilibili_id": 0,
            "consistency_issues": []
        }
        
        # 检查DownloadTask表
        dt_total = db.query(DownloadTask).count()
        dt_with_bilibili_id = db.query(DownloadTask).filter(
            DownloadTask.bilibili_id.isnot(None)
        ).count()
        
        result["download_tasks_total"] = dt_total
        result["download_tasks_with_bilibili_id"] = dt_with_bilibili_id
        
        # 检查Video表
        video_total = db.query(Video).count()
        video_with_bilibili_id = db.query(Video).filter(
            Video.bilibili_id.isnot(None)
        ).count()
        
        result["videos_total"] = video_total
        result["videos_with_bilibili_id"] = video_with_bilibili_id
        
        # 检查一致性问题
        if dt_total > 0 and dt_with_bilibili_id < dt_total:
            result["consistency_issues"].append(
                f"DownloadTask表中有 {dt_total - dt_with_bilibili_id} 条记录缺少bilibili_id"
            )
        
        if video_total > 0 and video_with_bilibili_id < video_total:
            result["consistency_issues"].append(
                f"Video表中有 {video_total - video_with_bilibili_id} 条记录缺少bilibili_id"
            )
        
        # 检查重复的bilibili_id
        duplicate_videos = db.execute(text("""
            SELECT bilibili_id, COUNT(*) as count 
            FROM videos 
            WHERE bilibili_id IS NOT NULL 
            GROUP BY bilibili_id 
            HAVING COUNT(*) > 1
        """)).fetchall()
        
        if duplicate_videos:
            result["consistency_issues"].append(
                f"Video表中有 {len(duplicate_videos)} 个重复的bilibili_id"
            )
        
        return result
    
    def _create_field_mapping_documentation(self, db: Session) -> Dict[str, any]:
        """创建字段映射文档"""
        
        field_mapping = {
            "standardized_fields": {
                "bilibili_id": {
                    "description": "B站视频唯一标识符（BV号或av号）",
                    "tables": ["videos", "download_tasks"],
                    "type": "VARCHAR(50)",
                    "constraints": ["NOT NULL", "UNIQUE (in videos table)"]
                }
            },
            "deprecated_fields": {
                "video_id": {
                    "description": "已废弃的视频ID字段，已迁移到bilibili_id",
                    "tables": ["download_tasks"],
                    "migration_status": "completed",
                    "removal_planned": "v8.0.0"
                }
            },
            "migration_rules": [
                "所有新代码必须使用bilibili_id字段",
                "video_id字段保留用于向后兼容，但不应在新功能中使用",
                "API响应统一使用bilibili_id字段名",
                "数据库查询优先使用bilibili_id索引"
            ]
        }
        
        # 将映射信息存储到Settings表中
        from ..models import Settings
        import json
        
        mapping_key = "field_naming_migration_mapping"
        existing = db.query(Settings).filter(Settings.key == mapping_key).first()
        
        if not existing:
            mapping_setting = Settings(
                key=mapping_key,
                value=json.dumps(field_mapping, ensure_ascii=False, indent=2),
                description="字段命名统一化迁移映射文档"
            )
            db.add(mapping_setting)
        else:
            existing.value = json.dumps(field_mapping, ensure_ascii=False, indent=2)
        
        return {
            "mapping_created": True,
            "mapping_key": mapping_key,
            "field_count": len(field_mapping["standardized_fields"]) + len(field_mapping["deprecated_fields"])
        }
    
    def get_migration_status(self, db: Session = None) -> Dict[str, any]:
        """获取迁移状态"""
        if not db:
            db = next(get_db())
        
        try:
            # 检查迁移是否已完成
            inspector = inspect(db.bind)
            columns = [col['name'] for col in inspector.get_columns('download_tasks')]
            
            has_bilibili_id = 'bilibili_id' in columns
            
            if not has_bilibili_id:
                return {"status": "not_started", "message": "迁移尚未开始"}
            
            # 检查数据迁移完成度
            null_count = db.execute(text("""
                SELECT COUNT(*) FROM download_tasks WHERE bilibili_id IS NULL
            """)).scalar()
            
            total_count = db.execute(text("""
                SELECT COUNT(*) FROM download_tasks
            """)).scalar()
            
            if null_count == 0:
                return {
                    "status": "completed",
                    "message": "迁移已完成",
                    "total_records": total_count
                }
            else:
                return {
                    "status": "partial",
                    "message": f"迁移部分完成，还有 {null_count} 条记录需要处理",
                    "total_records": total_count,
                    "remaining_records": null_count
                }
                
        except Exception as e:
            return {
                "status": "error",
                "message": f"检查迁移状态失败: {str(e)}"
            }


def run_field_naming_migration() -> Dict[str, any]:
    """运行字段命名统一化迁移的入口函数"""
    migration = FieldNamingMigration()
    return migration.run_migration()


def get_field_naming_status() -> Dict[str, any]:
    """获取字段命名迁移状态的入口函数"""
    migration = FieldNamingMigration()
    return migration.get_migration_status()
