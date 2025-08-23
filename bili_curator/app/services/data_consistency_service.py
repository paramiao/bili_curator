"""
数据一致性检查和自动修复服务
"""
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from loguru import logger

from ..models import Subscription, Video, Settings
from .remote_total_store import (
    read_remote_total_raw,
    read_remote_total_fresh,
    write_remote_total,
)
from .pending_list_service import pending_list_service


class DataConsistencyService:
    """数据一致性检查和自动修复服务"""
    
    def __init__(self):
        self.max_cache_age_hours = 2  # 缓存最大有效期
    
    async def check_and_fix_remote_totals(self, db: Session) -> Dict:
        """检查并修复远端总数缓存"""
        results = {
            "checked": 0,
            "outdated": 0,
            "refreshed": 0,
            "errors": []
        }
        
        try:
            # 获取所有合集类型订阅
            subscriptions = db.query(Subscription).filter(
                Subscription.type == "collection",
                Subscription.url.isnot(None)
            ).all()
            
            for sub in subscriptions:
                results["checked"] += 1
                
                try:
                    # 读取原始缓存数据（统一入口，兼容新旧键）
                    data = read_remote_total_raw(db, sub.id)
                    needs_refresh = False
                    if not data:
                        needs_refresh = True
                        logger.info(f"订阅 {sub.id} 缺少远端总数缓存")
                    else:
                        try:
                            cache_time = datetime.fromisoformat(data.get('timestamp', ''))
                            if datetime.now() - cache_time > timedelta(hours=self.max_cache_age_hours):
                                needs_refresh = True
                                results["outdated"] += 1
                                logger.info(f"订阅 {sub.id} 远端总数缓存过期")
                        except Exception as e:
                            needs_refresh = True
                            logger.warning(f"订阅 {sub.id} 缓存数据格式错误: {e}")
                    
                    # 刷新过期或缺失的缓存
                    if needs_refresh:
                        try:
                            # 使用双阶段策略获取远端总数
                            expected_total = await self._get_remote_total_with_fallback(sub.url, db)
                            
                            # 统一写入缓存（内部处理新旧键兼容与提交）
                            write_remote_total(db, sub.id, expected_total, sub.url)
                            results["refreshed"] += 1
                            logger.info(f"订阅 {sub.id} 远端总数缓存已更新: {expected_total}")
                            
                        except Exception as e:
                            error_msg = f"订阅 {sub.id} 缓存刷新失败: {e}"
                            results["errors"].append(error_msg)
                            logger.error(error_msg)
                
                except Exception as e:
                    error_msg = f"订阅 {sub.id} 一致性检查失败: {e}"
                    results["errors"].append(error_msg)
                    logger.error(error_msg)
            
            return results
            
        except Exception as e:
            logger.error(f"数据一致性检查失败: {e}")
            results["errors"].append(str(e))
            return results
    
    async def _get_remote_total_with_fallback(self, url: str, db: Session) -> int:
        """双阶段策略获取远端总数"""
        from ..downloader import downloader
        from ..cookie_manager import cookie_manager
        
        # 直接使用现有方法获取远端总数
        try:
            videos = await downloader._get_collection_videos(url, db)
            expected_total = len(videos) if videos else 0
            if expected_total > 0:
                return expected_total
        except Exception as e:
            logger.warning(f"获取远端总数失败: {e}")
        
        raise Exception("获取远端总数失败")
    
    def check_pending_counts_accuracy(self, db: Session) -> Dict:
        """检查待下载数量计算准确性"""
        results = {
            "checked": 0,
            "mismatches": [],
            "recommendations": []
        }
        
        try:
            subscriptions = db.query(Subscription).filter(
                Subscription.type == "collection",
                Subscription.url.isnot(None)
            ).all()
            
            for sub in subscriptions:
                results["checked"] += 1
                
                try:
                    # 获取统计API的计算结果
                    local_videos = db.query(Video).filter(
                        Video.subscription_id == sub.id,
                        Video.video_path.isnot(None)
                    ).count()
                    
                    # 获取远端总数（统一读取）
                    cache_data = read_remote_total_raw(db, sub.id)
                    remote_total = cache_data.get('total') if cache_data else None
                    
                    if remote_total:
                        calculated_pending = max(0, remote_total - local_videos)
                        
                        # 检查是否存在明显不合理的数据
                        if local_videos > remote_total:
                            results["mismatches"].append({
                                "subscription_id": sub.id,
                                "name": sub.name,
                                "issue": "本地文件数超过远端总数",
                                "local": local_videos,
                                "remote": remote_total,
                                "suggestion": "检查远端总数缓存是否过期"
                            })
                        
                        # 检查缓存时效性
                        try:
                            cache_time = datetime.fromisoformat(cache_data.get('timestamp', '')) if cache_data else None
                            if cache_time and datetime.now() - cache_time > timedelta(hours=24):
                                results["recommendations"].append({
                                    "subscription_id": sub.id,
                                    "name": sub.name,
                                    "suggestion": "远端总数缓存超过24小时，建议刷新"
                                })
                        except Exception:
                            pass
                
                except Exception as e:
                    logger.warning(f"订阅 {sub.id} 待下载数量检查失败: {e}")
            
            return results
            
        except Exception as e:
            logger.error(f"待下载数量准确性检查失败: {e}")
            results["errors"] = [str(e)]
            return results
    
    async def auto_fix_data_issues(self, db: Session) -> Dict:
        """自动修复数据问题"""
        results = {
            "remote_totals": {},
            "pending_counts": {},
            "failed_videos": {}
        }
        
        # 修复远端总数缓存
        results["remote_totals"] = await self.check_and_fix_remote_totals(db)
        
        # 检查待下载数量准确性
        results["pending_counts"] = self.check_pending_counts_accuracy(db)
        
        # 清理失败视频
        subscriptions = db.query(Subscription).filter(
            Subscription.type == "collection"
        ).all()
        
        total_cleaned = 0
        for sub in subscriptions:
            try:
                cleaned = pending_list_service.check_and_clean_failed_videos(db, sub.id)
                total_cleaned += cleaned
            except Exception as e:
                logger.warning(f"订阅 {sub.id} 失败视频清理失败: {e}")
        
        results["failed_videos"] = {
            "cleaned_count": total_cleaned
        }
        
        return results


# 全局实例
data_consistency_service = DataConsistencyService()
