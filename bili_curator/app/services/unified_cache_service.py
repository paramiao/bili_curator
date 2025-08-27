"""
统一缓存服务 - 解决双套缓存系统问题
实现策略：双写单读 + 统一服务层 + 渐进式迁移

核心功能：
1. 统一缓存访问接口，封装Settings表操作
2. 支持TTL和命名空间管理
3. 缓存失效钩子和一致性监控
4. 特性开关支持灰度控制
"""
from __future__ import annotations
from typing import Optional, Dict, Any, List, Union
from datetime import datetime, timedelta
import json
import os
from dataclasses import dataclass
from sqlalchemy.orm import Session
from loguru import logger

from ..models import Settings


@dataclass
class CacheConfig:
    """缓存配置"""
    default_ttl_hours: int = 1
    enable_monitoring: bool = True
    enable_dual_write: bool = True  # 特性开关：双写模式
    enable_settings_read: bool = True  # 特性开关：从Settings读取
    namespace_separator: str = ":"


class UnifiedCacheService:
    """统一缓存服务"""
    
    def __init__(self, config: Optional[CacheConfig] = None):
        self.config = config or CacheConfig()
        self._in_memory_cache: Dict[str, Dict[str, Any]] = {}
        self._access_stats: Dict[str, int] = {}
        self._consistency_errors: List[Dict[str, Any]] = []
    
    def _normalize_key(self, namespace: str, key: str) -> str:
        """标准化缓存键名"""
        return f"{namespace}{self.config.namespace_separator}{key}"
    
    def _get_ttl_hours(self, ttl_hours: Optional[int] = None) -> int:
        """获取TTL配置"""
        if ttl_hours is not None:
            return ttl_hours
        # 支持环境变量配置
        env_ttl = os.getenv('CACHE_DEFAULT_TTL_HOURS')
        if env_ttl:
            try:
                return int(env_ttl)
            except ValueError:
                pass
        return self.config.default_ttl_hours
    
    def get(self, db: Session, namespace: str, key: str, 
            ttl_hours: Optional[int] = None, 
            default: Any = None) -> Any:
        """
        统一缓存读取接口
        优先级：内存缓存 -> Settings表 -> 默认值
        """
        full_key = self._normalize_key(namespace, key)
        ttl = self._get_ttl_hours(ttl_hours)
        
        # 记录访问统计
        self._access_stats[full_key] = self._access_stats.get(full_key, 0) + 1
        
        try:
            # 1. 尝试内存缓存
            if full_key in self._in_memory_cache:
                cache_data = self._in_memory_cache[full_key]
                if self._is_cache_fresh(cache_data, ttl):
                    logger.debug(f"Cache hit (memory): {full_key}")
                    return cache_data['value']
                else:
                    # 过期，清理内存缓存
                    del self._in_memory_cache[full_key]
            
            # 2. 尝试Settings表
            if self.config.enable_settings_read:
                setting = db.query(Settings).filter(Settings.key == full_key).first()
                if setting and setting.value is not None:
                    try:
                        data = json.loads(setting.value)
                        if isinstance(data, dict) and 'timestamp' in data:
                            if self._is_settings_fresh(data, ttl):
                                value = data.get('value')
                                # 回填内存缓存
                                self._set_memory_cache(full_key, value)
                                logger.debug(f"Cache hit (settings): {full_key}")
                                return value
                        else:
                            # 兼容旧格式：直接存储值
                            logger.debug(f"Cache hit (settings, legacy): {full_key}")
                            return data
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Cache parse error for {full_key}: {e}")
            
            logger.debug(f"Cache miss: {full_key}")
            return default
            
        except Exception as e:
            logger.error(f"Cache get error for {full_key}: {e}")
            return default
    
    def set(self, db: Session, namespace: str, key: str, value: Any,
            ttl_hours: Optional[int] = None, description: str = "") -> None:
        """
        统一缓存写入接口
        双写模式：同时写入内存缓存和Settings表
        """
        full_key = self._normalize_key(namespace, key)
        ttl = self._get_ttl_hours(ttl_hours)
        
        try:
            # 1. 写入内存缓存
            self._set_memory_cache(full_key, value)
            
            # 2. 写入Settings表（如果启用）
            if self.config.enable_dual_write:
                self._set_settings_cache(db, full_key, value, description)
            
            logger.debug(f"Cache set: {full_key}")
            
        except Exception as e:
            logger.error(f"Cache set error for {full_key}: {e}")
            raise
    
    def delete(self, db: Session, namespace: str, key: str) -> bool:
        """删除缓存项"""
        full_key = self._normalize_key(namespace, key)
        
        try:
            deleted = False
            
            # 1. 删除内存缓存
            if full_key in self._in_memory_cache:
                del self._in_memory_cache[full_key]
                deleted = True
            
            # 2. 删除Settings表
            setting = db.query(Settings).filter(Settings.key == full_key).first()
            if setting:
                db.delete(setting)
                db.commit()
                deleted = True
            
            logger.debug(f"Cache delete: {full_key}, deleted: {deleted}")
            return deleted
            
        except Exception as e:
            logger.error(f"Cache delete error for {full_key}: {e}")
            db.rollback()
            return False
    
    def clear_namespace(self, db: Session, namespace: str) -> int:
        """清理指定命名空间的所有缓存"""
        prefix = f"{namespace}{self.config.namespace_separator}"
        cleared = 0
        
        try:
            # 1. 清理内存缓存
            keys_to_delete = [k for k in self._in_memory_cache.keys() if k.startswith(prefix)]
            for key in keys_to_delete:
                del self._in_memory_cache[key]
                cleared += 1
            
            # 2. 清理Settings表
            settings = db.query(Settings).filter(Settings.key.like(f"{prefix}%")).all()
            for setting in settings:
                db.delete(setting)
                cleared += 1
            
            db.commit()
            logger.info(f"Cleared {cleared} cache items from namespace: {namespace}")
            return cleared
            
        except Exception as e:
            logger.error(f"Cache clear namespace error for {namespace}: {e}")
            db.rollback()
            return 0
    
    def _set_memory_cache(self, full_key: str, value: Any) -> None:
        """设置内存缓存"""
        self._in_memory_cache[full_key] = {
            'value': value,
            'timestamp': datetime.now().isoformat(),
            'access_count': 0
        }
    
    def _set_settings_cache(self, db: Session, full_key: str, value: Any, description: str) -> None:
        """设置Settings表缓存"""
        cache_data = {
            'value': value,
            'timestamp': datetime.now().isoformat(),
            'version': '1.0'
        }
        
        setting = db.query(Settings).filter(Settings.key == full_key).first()
        if setting:
            setting.value = json.dumps(cache_data, ensure_ascii=False)
            setting.updated_at = datetime.now()
        else:
            setting = Settings(
                key=full_key,
                value=json.dumps(cache_data, ensure_ascii=False),
                description=description or f"Unified cache: {full_key}"
            )
            db.add(setting)
        
        db.commit()
    
    def _is_cache_fresh(self, cache_data: Dict[str, Any], ttl_hours: int) -> bool:
        """检查内存缓存是否新鲜"""
        try:
            timestamp_str = cache_data.get('timestamp')
            if not timestamp_str:
                return False
            
            timestamp = datetime.fromisoformat(timestamp_str)
            return datetime.now() - timestamp <= timedelta(hours=ttl_hours)
        except Exception:
            return False
    
    def _is_settings_fresh(self, data: Dict[str, Any], ttl_hours: int) -> bool:
        """检查Settings缓存是否新鲜"""
        try:
            timestamp_str = data.get('timestamp')
            if not timestamp_str:
                return False
            
            timestamp = datetime.fromisoformat(timestamp_str)
            return datetime.now() - timestamp <= timedelta(hours=ttl_hours)
        except Exception:
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        return {
            'memory_cache_size': len(self._in_memory_cache),
            'access_stats': dict(self._access_stats),
            'consistency_errors': len(self._consistency_errors),
            'config': {
                'default_ttl_hours': self.config.default_ttl_hours,
                'enable_dual_write': self.config.enable_dual_write,
                'enable_settings_read': self.config.enable_settings_read,
            }
        }
    
    def check_consistency(self, db: Session, namespace: Optional[str] = None) -> Dict[str, Any]:
        """检查缓存一致性"""
        results = {
            'checked': 0,
            'inconsistent': 0,
            'errors': []
        }
        
        try:
            # 获取要检查的键
            if namespace:
                prefix = f"{namespace}{self.config.namespace_separator}"
                memory_keys = [k for k in self._in_memory_cache.keys() if k.startswith(prefix)]
                settings_query = db.query(Settings).filter(Settings.key.like(f"{prefix}%"))
            else:
                memory_keys = list(self._in_memory_cache.keys())
                settings_query = db.query(Settings)
            
            settings_keys = {s.key for s in settings_query.all()}
            all_keys = set(memory_keys) | settings_keys
            
            for key in all_keys:
                results['checked'] += 1
                
                memory_value = None
                settings_value = None
                
                # 获取内存缓存值
                if key in self._in_memory_cache:
                    memory_value = self._in_memory_cache[key].get('value')
                
                # 获取Settings值
                if key in settings_keys:
                    setting = db.query(Settings).filter(Settings.key == key).first()
                    if setting and setting.value:
                        try:
                            data = json.loads(setting.value)
                            settings_value = data.get('value') if isinstance(data, dict) else data
                        except json.JSONDecodeError:
                            pass
                
                # 比较一致性
                if memory_value != settings_value:
                    results['inconsistent'] += 1
                    results['errors'].append({
                        'key': key,
                        'memory_value': memory_value,
                        'settings_value': settings_value
                    })
            
            return results
            
        except Exception as e:
            logger.error(f"Consistency check error: {e}")
            results['errors'].append({'error': str(e)})
            return results


# 全局实例
unified_cache = UnifiedCacheService()


# 便捷函数
def get_cache(db: Session, namespace: str, key: str, **kwargs) -> Any:
    """便捷的缓存读取函数"""
    return unified_cache.get(db, namespace, key, **kwargs)


def set_cache(db: Session, namespace: str, key: str, value: Any, **kwargs) -> None:
    """便捷的缓存写入函数"""
    unified_cache.set(db, namespace, key, value, **kwargs)


def delete_cache(db: Session, namespace: str, key: str) -> bool:
    """便捷的缓存删除函数"""
    return unified_cache.delete(db, namespace, key)


def clear_cache_namespace(db: Session, namespace: str) -> int:
    """便捷的命名空间清理函数"""
    return unified_cache.clear_namespace(db, namespace)
