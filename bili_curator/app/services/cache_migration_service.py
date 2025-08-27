"""
缓存迁移服务 - 从旧的双套缓存系统迁移到统一缓存服务
支持渐进式迁移和回滚
"""
from __future__ import annotations
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
from loguru import logger
import json
from datetime import datetime

from ..models import Settings
from .unified_cache_service import unified_cache


class CacheMigrationService:
    """缓存迁移服务"""
    
    def __init__(self):
        self.migration_mapping = {
            # 旧键格式 -> 新命名空间和键
            'remote_total:': ('remote_total', lambda k: k.split(':')[1]),
            'expected_total:': ('remote_total', lambda k: f"legacy_{k.split(':')[1]}"),
            'pending_list:': ('pending_list', lambda k: k.split(':')[1]),
            'local_index:': ('local_index', lambda k: k.split(':', 1)[1]),
            'sync:': ('remote_sync', lambda k: k.split(':', 1)[1]),
            'agg:': ('subscription_stats', lambda k: k.split(':', 1)[1]),
        }
    
    def analyze_current_cache(self, db: Session) -> Dict[str, Any]:
        """分析当前缓存状态"""
        try:
            # 获取所有Settings记录
            settings = db.query(Settings).all()
            
            analysis = {
                'total_settings': len(settings),
                'cache_categories': {},
                'migration_candidates': [],
                'unified_cache_items': 0,
                'legacy_items': 0
            }
            
            for setting in settings:
                key = setting.key
                
                # 分类统计
                category = 'other'
                for prefix in self.migration_mapping.keys():
                    if key.startswith(prefix):
                        category = prefix.rstrip(':')
                        break
                
                if category not in analysis['cache_categories']:
                    analysis['cache_categories'][category] = 0
                analysis['cache_categories'][category] += 1
                
                # 检查是否为迁移候选
                if category != 'other':
                    try:
                        # 尝试解析值
                        value = json.loads(setting.value) if setting.value else None
                        is_unified_format = (
                            isinstance(value, dict) and 
                            'value' in value and 
                            'timestamp' in value
                        )
                        
                        if is_unified_format:
                            analysis['unified_cache_items'] += 1
                        else:
                            analysis['legacy_items'] += 1
                            analysis['migration_candidates'].append({
                                'key': key,
                                'category': category,
                                'size': len(setting.value) if setting.value else 0
                            })
                    except json.JSONDecodeError:
                        analysis['migration_candidates'].append({
                            'key': key,
                            'category': category,
                            'size': len(setting.value) if setting.value else 0,
                            'error': 'json_decode_error'
                        })
            
            return analysis
            
        except Exception as e:
            logger.error(f"Cache analysis failed: {e}")
            return {'error': str(e)}
    
    def migrate_legacy_caches(self, db: Session, dry_run: bool = True) -> Dict[str, Any]:
        """迁移旧格式缓存到统一缓存服务"""
        results = {
            'migrated': 0,
            'skipped': 0,
            'errors': [],
            'dry_run': dry_run
        }
        
        try:
            settings = db.query(Settings).all()
            
            for setting in settings:
                key = setting.key
                
                # 查找匹配的迁移规则
                namespace = None
                new_key = None
                
                for prefix, (ns, key_func) in self.migration_mapping.items():
                    if key.startswith(prefix):
                        namespace = ns
                        try:
                            new_key = key_func(key)
                            break
                        except Exception as e:
                            results['errors'].append(f"Key transformation failed for {key}: {e}")
                            continue
                
                if not namespace or not new_key:
                    results['skipped'] += 1
                    continue
                
                try:
                    # 解析旧值
                    old_value = json.loads(setting.value) if setting.value else None
                    
                    # 检查是否已经是统一格式
                    if (isinstance(old_value, dict) and 
                        'value' in old_value and 
                        'timestamp' in old_value):
                        results['skipped'] += 1
                        continue
                    
                    # 迁移到统一格式
                    if not dry_run:
                        unified_cache.set(
                            db, namespace, new_key, old_value,
                            description=f"Migrated from legacy key: {key}"
                        )
                    
                    results['migrated'] += 1
                    logger.debug(f"{'Would migrate' if dry_run else 'Migrated'} {key} -> {namespace}:{new_key}")
                    
                except Exception as e:
                    results['errors'].append(f"Migration failed for {key}: {e}")
            
            return results
            
        except Exception as e:
            logger.error(f"Cache migration failed: {e}")
            results['errors'].append(str(e))
            return results
    
    def cleanup_legacy_caches(self, db: Session, dry_run: bool = True) -> Dict[str, Any]:
        """清理已迁移的旧格式缓存"""
        results = {
            'cleaned': 0,
            'errors': [],
            'dry_run': dry_run
        }
        
        try:
            # 查找所有旧格式缓存
            legacy_keys = []
            settings = db.query(Settings).all()
            
            for setting in settings:
                key = setting.key
                
                # 检查是否为旧格式
                for prefix in self.migration_mapping.keys():
                    if key.startswith(prefix):
                        try:
                            old_value = json.loads(setting.value) if setting.value else None
                            # 如果不是统一格式，标记为清理候选
                            if not (isinstance(old_value, dict) and 
                                   'value' in old_value and 
                                   'timestamp' in old_value):
                                legacy_keys.append(key)
                                break
                        except json.JSONDecodeError:
                            legacy_keys.append(key)
                            break
            
            # 清理旧格式缓存
            if not dry_run:
                for key in legacy_keys:
                    try:
                        setting = db.query(Settings).filter(Settings.key == key).first()
                        if setting:
                            db.delete(setting)
                            results['cleaned'] += 1
                    except Exception as e:
                        results['errors'].append(f"Failed to delete {key}: {e}")
                
                if results['cleaned'] > 0:
                    db.commit()
            else:
                results['cleaned'] = len(legacy_keys)
            
            return results
            
        except Exception as e:
            logger.error(f"Cache cleanup failed: {e}")
            results['errors'].append(str(e))
            return results
    
    def validate_migration(self, db: Session) -> Dict[str, Any]:
        """验证迁移结果"""
        results = {
            'validation_passed': True,
            'issues': [],
            'stats': {}
        }
        
        try:
            # 检查统一缓存服务状态
            cache_stats = unified_cache.get_stats()
            results['stats']['unified_cache'] = cache_stats
            
            # 检查一致性
            consistency_results = unified_cache.check_consistency(db)
            results['stats']['consistency'] = consistency_results
            
            if consistency_results.get('inconsistent', 0) > 0:
                results['validation_passed'] = False
                results['issues'].append(f"Found {consistency_results['inconsistent']} inconsistent cache items")
            
            # 检查是否还有未迁移的旧格式缓存
            analysis = self.analyze_current_cache(db)
            if analysis.get('legacy_items', 0) > 0:
                results['issues'].append(f"Found {analysis['legacy_items']} legacy cache items not migrated")
            
            results['stats']['cache_analysis'] = analysis
            
            return results
            
        except Exception as e:
            logger.error(f"Migration validation failed: {e}")
            results['validation_passed'] = False
            results['issues'].append(str(e))
            return results
    
    def rollback_migration(self, db: Session, backup_data: Optional[Dict] = None) -> Dict[str, Any]:
        """回滚迁移（如果有备份数据）"""
        results = {
            'rollback_completed': False,
            'errors': []
        }
        
        try:
            if not backup_data:
                results['errors'].append("No backup data provided for rollback")
                return results
            
            # 清理统一缓存格式的数据
            unified_settings = db.query(Settings).all()
            for setting in unified_settings:
                try:
                    value = json.loads(setting.value) if setting.value else None
                    if (isinstance(value, dict) and 
                        'value' in value and 
                        'timestamp' in value and
                        'version' in value):
                        db.delete(setting)
                except json.JSONDecodeError:
                    continue
            
            # 恢复备份数据
            for key, value in backup_data.items():
                setting = Settings(key=key, value=value)
                db.add(setting)
            
            db.commit()
            results['rollback_completed'] = True
            
            return results
            
        except Exception as e:
            logger.error(f"Migration rollback failed: {e}")
            results['errors'].append(str(e))
            db.rollback()
            return results


# 全局实例
cache_migration = CacheMigrationService()


# 便捷函数
def analyze_cache_status(db: Session) -> Dict[str, Any]:
    """分析当前缓存状态"""
    return cache_migration.analyze_current_cache(db)


def migrate_to_unified_cache(db: Session, dry_run: bool = True) -> Dict[str, Any]:
    """迁移到统一缓存系统"""
    return cache_migration.migrate_legacy_caches(db, dry_run)


def validate_cache_migration(db: Session) -> Dict[str, Any]:
    """验证缓存迁移结果"""
    return cache_migration.validate_migration(db)
