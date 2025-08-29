"""
简化版Cookie管理器
"""
import os
import time
from datetime import datetime
from typing import Optional, Dict, List
from sqlalchemy.orm import Session
from loguru import logger

from .services.http_utils import get_user_agent

# 延迟导入避免循环依赖
def _get_cookie_model():
    from .models import Cookie
    return Cookie

class SimpleCookieManager:
    def __init__(self):
        self.current_cookie_id = None
        self.last_switch_time = 0
        self.min_switch_interval = 300  # 5分钟最小切换间隔
        # 失败阈值策略
        self.failure_threshold = 3            # 在时间窗口内最多允许失败次数
        self.failure_window_minutes = 15      # 时间窗口分钟数
        self._checked_schema = False
        self._has_failure_columns = False

    def _ensure_failure_columns(self, db: Session):
        """检查 SQLite 表是否存在 failure_count/last_failure_at 列，避免老库报错"""
        if self._checked_schema:
            return
        try:
            rows = db.execute("PRAGMA table_info(cookies)").fetchall()
            cols = {r[1] for r in rows}
            self._has_failure_columns = {'failure_count', 'last_failure_at'}.issubset(cols)
        except Exception:
            self._has_failure_columns = False
        finally:
            self._checked_schema = True
    
    def get_available_cookie(self, db: Session) -> Optional:
        """获取可用的Cookie"""
        Cookie = _get_cookie_model()
        # 获取所有活跃的Cookie
        active_cookies = db.query(Cookie).filter(Cookie.is_active == True).all()
        
        if not active_cookies:
            logger.warning("没有可用的Cookie")
            return None
        
        # 如果当前没有Cookie或需要切换
        if (self.current_cookie_id is None or 
            time.time() - self.last_switch_time > self.min_switch_interval):
            
            # 选择使用次数最少的Cookie
            cookie = min(active_cookies, key=lambda c: c.usage_count)
            self.current_cookie_id = cookie.id
            self.last_switch_time = time.time()
            
            logger.info(f"切换到Cookie: {cookie.name}")
            return cookie
        
        # 返回当前Cookie
        Cookie = _get_cookie_model()
        current_cookie = db.query(Cookie).filter(Cookie.id == self.current_cookie_id).first()
        if current_cookie and current_cookie.is_active:
            return current_cookie
        
        # 当前Cookie不可用，重新选择
        self.current_cookie_id = None
        return self.get_available_cookie(db)
    
    async def get_valid_cookies(self) -> List:
        """为STRM代理服务提供可用的Cookie对象列表"""
        try:
            from .models import db
            with db.get_session() as session:
                cookie = self.get_available_cookie(session)
                if cookie:
                    return [cookie]
                return []
        except Exception as e:
            logger.error(f"获取可用Cookie失败: {e}")
            return []

    async def get_valid_cookies_dict(self) -> Dict[str, str]:
        """为外部服务提供可用的Cookie字典。
        优先读取环境变量(BILIBILI_SESSDATA/BILIBILI_BILI_JCT/BILIBILI_BUVID3)，
        否则从数据库选择一个活跃Cookie。
        返回键名与B站兼容：SESSDATA、bili_jct、DedeUserID、buvid3(可选)。
        """
        # 1) env 优先
        sess = os.getenv("BILIBILI_SESSDATA")
        jct = os.getenv("BILIBILI_BILI_JCT")
        buvid3 = os.getenv("BILIBILI_BUVID3")
        if sess and jct:
            cookies = {"SESSDATA": sess, "bili_jct": jct}
            if buvid3:
                cookies["buvid3"] = buvid3
            return cookies

        # 2) 回退到数据库
        try:
            from .models import db
            with db.get_session() as session:
                cookie = self.get_available_cookie(session)
                if cookie:
                    return {
                        "SESSDATA": cookie.sessdata,
                        "bili_jct": cookie.bili_jct,
                        "DedeUserID": cookie.dedeuserid,
                    }
                return {}
        except Exception as e:
            logger.error(f"获取可用Cookie失败: {e}")
            return {}

    def update_cookie_usage(self, db: Session, cookie_id: int):
        """更新Cookie使用统计"""
        Cookie = _get_cookie_model()
        cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
        if cookie:
            cookie.usage_count += 1
            cookie.last_used = datetime.now()
            db.commit()
    
    def mark_cookie_banned(self, db: Session, cookie_id: int, reason: str = ""):
        """标记Cookie为被封禁"""
        Cookie = _get_cookie_model()
        cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
        if cookie:
            cookie.is_active = False
            db.commit()
            logger.warning(f"Cookie {cookie.name} 被标记为不可用: {reason}")
            
            # 强制切换到其他Cookie
            self.current_cookie_id = None

    def reset_failures(self, db: Session, cookie_id: int):
        """重置失败计数（验证成功或下载成功时调用）"""
        self._ensure_failure_columns(db)
        if not self._has_failure_columns:
            return
        Cookie = _get_cookie_model()
        cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
        if cookie:
            cookie.failure_count = 0
            cookie.last_failure_at = None
            db.commit()

    def record_failure(self, db: Session, cookie_id: int, reason: str = ""):
        """记录一次失败，若在窗口内达到阈值则禁用"""
        self._ensure_failure_columns(db)
        Cookie = _get_cookie_model()
        cookie = db.query(Cookie).filter(Cookie.id == cookie_id).first()
        if not cookie:
            return
        if not self._has_failure_columns:
            # 回退策略：老库不支持失败计数，保持原行为直接禁用，避免无限错误循环
            self.mark_cookie_banned(db, cookie_id, reason or "old schema, direct ban")
            return

        now = datetime.now()
        # 窗口外失败重置
        if not cookie.last_failure_at or (now - cookie.last_failure_at).total_seconds() > self.failure_window_minutes * 60:
            cookie.failure_count = 0
        cookie.failure_count = (cookie.failure_count or 0) + 1
        cookie.last_failure_at = now
        db.commit()

        logger.warning(f"Cookie {cookie.name} 失败计数: {cookie.failure_count}/{self.failure_threshold}，原因: {reason}")

        if cookie.failure_count >= self.failure_threshold:
            self.mark_cookie_banned(db, cookie_id, f"达到失败阈值({self.failure_threshold})，原因: {reason}")
    
    async def validate_cookie(self, cookie) -> bool:
        """验证Cookie是否有效"""
        try:
            headers = {
                'User-Agent': get_user_agent(True),
                'Cookie': f'SESSDATA={cookie.sessdata}; bili_jct={cookie.bili_jct}; DedeUserID={cookie.dedeuserid}'
            }
            
            # 测试访问用户信息接口
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    'https://api.bilibili.com/x/web-interface/nav',
                    headers=headers,
                    timeout=10
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == 0:
                        logger.debug(f"Cookie {cookie.name} 验证成功")
                        return True
                    else:
                        logger.warning(f"Cookie {cookie.name} 验证失败: {data.get('message')}")
                        return False
                else:
                    logger.warning(f"Cookie {cookie.name} 验证失败: HTTP {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Cookie {cookie.name} 验证异常: {e}")
            return False
    
    def get_cookie_headers(self, cookie) -> Dict[str, str]:
        """获取Cookie请求头"""
        return {
            'User-Agent': get_user_agent(True),
            'Cookie': f'SESSDATA={cookie.sessdata}; bili_jct={cookie.bili_jct}; DedeUserID={cookie.dedeuserid}',
            'Referer': 'https://www.bilibili.com/'
        }
    
    async def batch_validate_cookies(self, db: Session):
        """批量验证所有Cookie"""
        Cookie = _get_cookie_model()
        cookies = db.query(Cookie).filter(Cookie.is_active == True).all()
        
        for cookie in cookies:
            try:
                is_valid = await self.validate_cookie(cookie)
                if not is_valid:
                    self.record_failure(db, cookie.id, "验证失败")
                else:
                    self.reset_failures(db, cookie.id)
                await asyncio.sleep(random.uniform(2, 5))  # 避免请求过快
            except Exception as e:
                logger.error(f"验证Cookie {cookie.name} 时出错: {e}")

# 全局Cookie管理器实例
cookie_manager = SimpleCookieManager()

class RateLimiter:
    """简单的请求频率限制器"""
    def __init__(self, min_interval: int = 5, max_interval: int = 15):
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.last_request = 0
    
    async def wait(self):
        """智能等待，避免请求过于频繁"""
        now = time.time()
        elapsed = now - self.last_request
        
        if elapsed < self.min_interval:
            wait_time = random.uniform(
                self.min_interval - elapsed,
                self.max_interval - elapsed
            )
            logger.debug(f"请求限速等待 {wait_time:.1f} 秒")
            await asyncio.sleep(wait_time)
        
        self.last_request = time.time()

# 全局限速器实例
rate_limiter = RateLimiter()

def simple_retry(max_retries: int = 3, base_delay: int = 10):
    """简单的重试装饰器"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt == max_retries - 1:
                        logger.error(f"重试 {max_retries} 次后仍然失败: {e}")
                        raise e
                    
                    # 指数退避
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 5)
                    logger.warning(f"第 {attempt + 1} 次尝试失败: {e}, {delay:.1f}秒后重试")
                    await asyncio.sleep(delay)
            
            raise last_exception
        return wrapper
    return decorator
