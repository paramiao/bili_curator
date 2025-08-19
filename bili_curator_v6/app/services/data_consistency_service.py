"""
数据一致性检查和自动修复服务
"""
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from loguru import logger

from ..models import Subscription, Video, Settings
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
                    # 检查expected-total缓存
                    cache_key = f"expected_total:{sub.id}"
                    cache_setting = db.query(Settings).filter(Settings.key == cache_key).first()
                    
                    needs_refresh = False
                    if not cache_setting:
                        needs_refresh = True
                        logger.info(f"订阅 {sub.id} 缺少远端总数缓存")
                    else:
                        try:
                            cache_data = json.loads(cache_setting.value)
                            cache_time = datetime.fromisoformat(cache_data.get('timestamp', ''))
                            if datetime.now() - cache_time > timedelta(hours=self.max_cache_age_hours):
                                needs_refresh = True
                                results["outdated"] += 1
                                logger.info(f"订阅 {sub.id} 远端总数缓存过期")
                        except Exception as e:
                            needs_refresh = True
                            logger.warning(f"订阅 {sub.id} 缓存数据格式错误: {e}")
                    
                    # 刷新过期或缺失的缓存
                    if needs_refresh:
                        from ..downloader import downloader
                        try:
                            # 使用双阶段策略获取远端总数
                            expected_total = await self._get_remote_total_with_fallback(sub.url, db)
                            
                            # 更新缓存
                            cache_data = {
                                "total": expected_total,
                                "timestamp": datetime.now().isoformat(),
                                "url": sub.url
                            }
                            
                            if cache_setting:
                                cache_setting.value = json.dumps(cache_data)
                            else:
                                cache_setting = Settings(
                                    key=cache_key,
                                    value=json.dumps(cache_data),
                                    description=f"订阅{sub.id}远端总数缓存"
                                )
                                db.add(cache_setting)
                            
                            db.commit()
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
        
        # 第一阶段：无Cookie尝试
        try:
            expected_total = await downloader._get_collection_total_count(url, db, use_cookie=False)
            if expected_total and expected_total > 0:
                return expected_total
        except Exception as e:
            logger.debug(f"无Cookie获取远端总数失败: {e}")
        
        # 第二阶段：使用Cookie回退
        try:
            cookie = cookie_manager.get_available_cookie(db)
            if cookie:
                expected_total = await downloader._get_collection_total_count(url, db, use_cookie=True)
                if expected_total and expected_total > 0:
                    return expected_total
        except Exception as e:
            logger.warning(f"Cookie回退获取远端总数失败: {e}")
        
        raise Exception("双阶段策略均失败")
    
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
                    
                    # 获取远端总数
                    cache_key = f"expected_total:{sub.id}"
                    cache_setting = db.query(Settings).filter(Settings.key == cache_key).first()
                    remote_total = None
                    
                    if cache_setting:
                        try:
                            cache_data = json.loads(cache_setting.value)
                            remote_total = cache_data.get('total', 0)
                        except Exception:
                            pass
                    
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
                            cache_data = json.loads(cache_setting.value)
                            cache_time = datetime.fromisoformat(cache_data.get('timestamp', ''))
                            if datetime.now() - cache_time > timedelta(hours=24):
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
