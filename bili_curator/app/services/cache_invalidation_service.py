"""
缓存失效服务 - 处理数据变更时的缓存失效逻辑
确保数据一致性和缓存新鲜度
"""
from __future__ import annotations
from typing import List, Optional, Set
from sqlalchemy.orm import Session
from loguru import logger

from .unified_cache_service import unified_cache


class CacheInvalidationService:
    """缓存失效服务"""
    
    def __init__(self):
        self.invalidation_rules = {
            # 订阅相关缓存失效规则
            'subscription_updated': [
                'remote_total',
                'pending_list', 
                'subscription_stats',
                'local_index'
            ],
            'video_downloaded': [
                'pending_list',
                'subscription_stats',
                'local_index'
            ],
            'video_failed': [
                'pending_list',
                'subscription_stats'
            ],
            'remote_sync_completed': [
                'remote_total',
                'pending_list'
            ]
        }
    
    def invalidate_subscription_caches(self, db: Session, subscription_id: int, 
                                     event_type: str = 'subscription_updated') -> None:
        """失效指定订阅的相关缓存"""
        try:
            namespaces = self.invalidation_rules.get(event_type, [])
            invalidated_count = 0
            
            for namespace in namespaces:
                # 构造订阅相关的缓存键模式
                cache_keys = self._get_subscription_cache_keys(subscription_id, namespace)
                
                for key in cache_keys:
                    if unified_cache.delete(db, namespace, key):
                        invalidated_count += 1
            
            logger.info(f"Invalidated {invalidated_count} cache items for subscription {subscription_id}, event: {event_type}")
            
        except Exception as e:
            logger.error(f"Cache invalidation error for subscription {subscription_id}: {e}")
    
    def invalidate_video_caches(self, db: Session, video_id: str, subscription_id: int,
                              event_type: str = 'video_downloaded') -> None:
        """失效指定视频的相关缓存"""
        try:
            # 视频状态变更影响订阅级别的缓存
            self.invalidate_subscription_caches(db, subscription_id, event_type)
            
            # 特定视频缓存（如果有的话）
            unified_cache.delete(db, 'video_info', video_id)
            
        except Exception as e:
            logger.error(f"Cache invalidation error for video {video_id}: {e}")
    
    def invalidate_global_caches(self, db: Session, event_type: str = 'global_update') -> None:
        """失效全局缓存"""
        try:
            global_namespaces = ['system_stats', 'global_metrics']
            invalidated_count = 0
            
            for namespace in global_namespaces:
                count = unified_cache.clear_namespace(db, namespace)
                invalidated_count += count
            
            logger.info(f"Invalidated {invalidated_count} global cache items, event: {event_type}")
            
        except Exception as e:
            logger.error(f"Global cache invalidation error: {e}")
    
    def _get_subscription_cache_keys(self, subscription_id: int, namespace: str) -> List[str]:
        """获取订阅相关的缓存键列表"""
        keys = []
        
        if namespace == 'remote_total':
            keys.extend([
                str(subscription_id),  # remote_total:{subscription_id}
                f"legacy_{subscription_id}"  # 兼容旧键格式
            ])
        elif namespace == 'pending_list':
            keys.append(str(subscription_id))  # pending_list:{subscription_id}
        elif namespace == 'subscription_stats':
            keys.extend([
                str(subscription_id),
                f"aggregated_{subscription_id}"
            ])
        elif namespace == 'local_index':
            keys.append(f"bvids_{subscription_id}")  # local_index:bvids_{subscription_id}
        
        return keys
    
    def batch_invalidate(self, db: Session, invalidations: List[dict]) -> None:
        """批量失效缓存"""
        try:
            total_invalidated = 0
            
            for item in invalidations:
                event_type = item.get('event_type', 'unknown')
                subscription_id = item.get('subscription_id')
                video_id = item.get('video_id')
                
                if subscription_id:
                    if video_id:
                        self.invalidate_video_caches(db, video_id, subscription_id, event_type)
                    else:
                        self.invalidate_subscription_caches(db, subscription_id, event_type)
                    total_invalidated += 1
            
            logger.info(f"Batch invalidated caches for {total_invalidated} items")
            
        except Exception as e:
            logger.error(f"Batch cache invalidation error: {e}")
    
    def register_invalidation_hook(self, event_type: str, namespaces: List[str]) -> None:
        """注册新的失效规则"""
        if event_type not in self.invalidation_rules:
            self.invalidation_rules[event_type] = []
        
        self.invalidation_rules[event_type].extend(namespaces)
        logger.info(f"Registered invalidation hook for {event_type}: {namespaces}")


# 全局实例
cache_invalidation = CacheInvalidationService()


# 便捷函数
def invalidate_subscription_caches(db: Session, subscription_id: int, event_type: str = 'subscription_updated') -> None:
    """便捷的订阅缓存失效函数"""
    cache_invalidation.invalidate_subscription_caches(db, subscription_id, event_type)


def invalidate_video_caches(db: Session, video_id: str, subscription_id: int, event_type: str = 'video_downloaded') -> None:
    """便捷的视频缓存失效函数"""
    cache_invalidation.invalidate_video_caches(db, video_id, subscription_id, event_type)
