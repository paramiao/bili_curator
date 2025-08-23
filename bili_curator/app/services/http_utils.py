"""
HTTP 相关的工具方法（统一 UA 策略）
- 无 Cookie 模式：使用随机 UA
- 需要 Cookie 模式：使用稳定 UA（减少风控/指纹漂移）
"""
from __future__ import annotations

import random
import os

# UA 池（可按需扩充/更新）
_UA_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
]

# 稳定 UA（用于需要 Cookie 的场景）
_STABLE_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'


def get_user_agent(requires_cookie: bool) -> str:
    """按照统一策略返回合适的 User-Agent 字符串。

    requires_cookie: True 表示请求需要携带 Cookie，使用稳定 UA；
                     False 表示无 Cookie 场景，可使用随机 UA。
    """
    try:
        # 允许通过环境变量覆盖
        # STABLE_UA: 覆盖稳定 UA
        # UA_POOL: 使用 '|' 分隔的 UA 列表
        stable_ua = os.getenv('STABLE_UA') or _STABLE_UA
        pool_env = os.getenv('UA_POOL')
        pool = [ua.strip() for ua in pool_env.split('|')] if pool_env else _UA_POOL

        if requires_cookie:
            return stable_ua
        # 随机从配置化的池中选择
        return random.choice(pool) if pool else stable_ua
    except Exception:
        # 极端情况下的兜底
        return os.getenv('STABLE_UA') or _STABLE_UA
