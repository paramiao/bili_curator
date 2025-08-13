"""
定时任务调度器 - 使用APScheduler替代Celery
"""
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from loguru import logger

from .models import Subscription, DownloadTask, Cookie, Settings, get_db
from .downloader import downloader
from .cookie_manager import cookie_manager

class SimpleScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.running = False
    
    def start(self):
        """启动调度器"""
        if not self.running:
            self.scheduler.start()
            self.running = True
            logger.info("定时任务调度器已启动")
            
            # 添加默认任务
            self._add_default_jobs()
    
    def stop(self):
        """停止调度器"""
        if self.running:
            self.scheduler.shutdown()
            self.running = False
            logger.info("定时任务调度器已停止")
    
    def _add_default_jobs(self):
        """添加默认定时任务"""
        # 检查订阅更新 - 每30分钟
        self.scheduler.add_job(
            func=self.check_subscriptions,
            trigger=IntervalTrigger(minutes=30),
            id='check_subscriptions',
            replace_existing=True,
            max_instances=1
        )
        
        # 验证Cookie - 每6小时
        self.scheduler.add_job(
            func=self.validate_cookies,
            trigger=IntervalTrigger(hours=6),
            id='validate_cookies',
            replace_existing=True,
            max_instances=1
        )
        
        # 清理旧任务 - 每天凌晨2点
        self.scheduler.add_job(
            func=self.cleanup_old_tasks,
            trigger=CronTrigger(hour=2, minute=0),
            id='cleanup_old_tasks',
            replace_existing=True,
            max_instances=1
        )
        
        logger.info("默认定时任务已添加")
    
    async def check_subscriptions(self):
        """检查所有活跃订阅的更新"""
        logger.info("开始检查订阅更新...")
        
        db = next(get_db())
        try:
            # 获取所有活跃订阅
            active_subscriptions = db.query(Subscription).filter(
                Subscription.is_active == True
            ).all()
            
            logger.info(f"发现 {len(active_subscriptions)} 个活跃订阅")
            
            for subscription in active_subscriptions:
                try:
                    await self._process_subscription(subscription, db)
                    
                    # 避免请求过快
                    await asyncio.sleep(10)
                    
                except Exception as e:
                    logger.error(f"处理订阅 {subscription.name} 失败: {e}")
            
            logger.info("订阅检查完成")
            
        except Exception as e:
            logger.error(f"检查订阅时出错: {e}")
        finally:
            db.close()
    
    async def _process_subscription(self, subscription: Subscription, db: Session):
        """处理单个订阅"""
        logger.info(f"检查订阅: {subscription.name} ({subscription.type})")
        
        try:
            if subscription.type == 'collection':
                # 处理合集订阅
                result = await downloader.download_collection(subscription.id, db)
                logger.info(f"合集 {subscription.name} 检查完成: {result['new_videos']} 个新视频")
                
            elif subscription.type == 'uploader':
                # 处理UP主订阅 (待实现)
                logger.info(f"UP主订阅 {subscription.name} 暂未实现")
                
            elif subscription.type == 'keyword':
                # 处理关键词订阅 (待实现)
                logger.info(f"关键词订阅 {subscription.name} 暂未实现")
                
        except Exception as e:
            logger.error(f"处理订阅 {subscription.name} 失败: {e}")
    
    async def validate_cookies(self):
        """验证所有Cookie的有效性"""
        logger.info("开始验证Cookie...")
        
        db = next(get_db())
        try:
            await cookie_manager.batch_validate_cookies(db)
            logger.info("Cookie验证完成")
        except Exception as e:
            logger.error(f"验证Cookie时出错: {e}")
        finally:
            db.close()
    
    async def cleanup_old_tasks(self):
        """清理超过30天的已完成任务"""
        logger.info("开始清理旧任务...")
        
        db = next(get_db())
        try:
            # 删除30天前的已完成任务
            cutoff_date = datetime.now() - timedelta(days=30)
            
            old_tasks = db.query(DownloadTask).filter(
                DownloadTask.status.in_(['completed', 'failed']),
                DownloadTask.completed_at < cutoff_date
            ).all()
            
            for task in old_tasks:
                db.delete(task)
            
            db.commit()
            logger.info(f"清理了 {len(old_tasks)} 个旧任务")
            
        except Exception as e:
            logger.error(f"清理旧任务时出错: {e}")
            db.rollback()
        finally:
            db.close()
    
    def add_custom_job(self, func, trigger, job_id: str, **kwargs):
        """添加自定义任务"""
        self.scheduler.add_job(
            func=func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            **kwargs
        )
        logger.info(f"添加自定义任务: {job_id}")
    
    def remove_job(self, job_id: str):
        """移除任务"""
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"移除任务: {job_id}")
        except Exception as e:
            logger.warning(f"移除任务 {job_id} 失败: {e}")
    
    def get_jobs(self):
        """获取所有任务信息"""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'name': job.name or job.id,
                'next_run': job.next_run_time.isoformat() if job.next_run_time else None,
                'trigger': str(job.trigger)
            })
        return jobs
    
    def update_subscription_check_interval(self, minutes: int):
        """更新订阅检查间隔"""
        self.scheduler.modify_job(
            'check_subscriptions',
            trigger=IntervalTrigger(minutes=minutes)
        )
        logger.info(f"订阅检查间隔已更新为 {minutes} 分钟")

# 全局调度器实例
scheduler = SimpleScheduler()

class TaskManager:
    """任务管理器 - 管理手动触发的任务"""
    
    def __init__(self):
        self.running_tasks = {}
    
    async def start_download_task(self, subscription_id: int) -> str:
        """启动下载任务"""
        task_id = f"manual_download_{subscription_id}_{datetime.now().timestamp()}"
        
        if task_id in self.running_tasks:
            raise ValueError("任务已在运行中")
        
        # 创建异步任务
        task = asyncio.create_task(self._run_download_task(subscription_id, task_id))
        self.running_tasks[task_id] = {
            'task': task,
            'subscription_id': subscription_id,
            'started_at': datetime.now(),
            'status': 'running'
        }
        
        logger.info(f"启动手动下载任务: {task_id}")
        return task_id
    
    async def _run_download_task(self, subscription_id: int, task_id: str):
        """运行下载任务"""
        try:
            db = next(get_db())
            try:
                result = await downloader.download_collection(subscription_id, db)
                
                # 更新任务状态
                if task_id in self.running_tasks:
                    self.running_tasks[task_id]['status'] = 'completed'
                    self.running_tasks[task_id]['result'] = result
                    self.running_tasks[task_id]['completed_at'] = datetime.now()
                
                logger.info(f"手动下载任务完成: {task_id}")
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"手动下载任务失败: {task_id} - {e}")
            
            # 更新任务状态
            if task_id in self.running_tasks:
                self.running_tasks[task_id]['status'] = 'failed'
                self.running_tasks[task_id]['error'] = str(e)
                self.running_tasks[task_id]['completed_at'] = datetime.now()
    
    def get_task_status(self, task_id: str) -> dict:
        """获取任务状态"""
        if task_id not in self.running_tasks:
            return {'status': 'not_found'}
        
        task_info = self.running_tasks[task_id].copy()
        task_info.pop('task', None)  # 移除asyncio.Task对象
        
        return task_info
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        if task_id not in self.running_tasks:
            return False
        
        task_info = self.running_tasks[task_id]
        if task_info['status'] == 'running':
            task_info['task'].cancel()
            task_info['status'] = 'cancelled'
            task_info['completed_at'] = datetime.now()
            logger.info(f"取消任务: {task_id}")
            return True
        
        return False
    
    def cleanup_completed_tasks(self):
        """清理已完成的任务"""
        completed_tasks = [
            task_id for task_id, info in self.running_tasks.items()
            if info['status'] in ['completed', 'failed', 'cancelled']
        ]
        
        for task_id in completed_tasks:
            if task_id in self.running_tasks:
                # 保留最近1小时的任务记录
                completed_at = self.running_tasks[task_id].get('completed_at')
                if completed_at and (datetime.now() - completed_at).total_seconds() > 3600:
                    del self.running_tasks[task_id]
    
    def get_all_tasks(self) -> dict:
        """获取所有任务状态"""
        self.cleanup_completed_tasks()
        
        tasks = {}
        for task_id, info in self.running_tasks.items():
            task_copy = info.copy()
            task_copy.pop('task', None)  # 移除asyncio.Task对象
            tasks[task_id] = task_copy
        
        return tasks

# 全局任务管理器实例
task_manager = TaskManager()
