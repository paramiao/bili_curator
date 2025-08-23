import asyncio
from typing import Optional, Tuple
import httpx
from loguru import logger

from ..cookie_manager import cookie_manager
from ..services.http_utils import get_user_agent
from ..models import get_db

# 参考接口（可能存在风控/签名需求，实际运行时优先携带可用 Cookie 提升成功率）：
# - 根据 mid 获取用户信息（含 name）
#   https://api.bilibili.com/x/space/wbi/acc/info?mid={mid}
# - 根据关键字搜索用户列表（取最匹配项）
#   https://api.bilibili.com/x/web-interface/search/type?search_type=bili_user&keyword={name}

DEFAULT_TIMEOUT = 10.0

async def _request_json(url: str, cookies: Optional[dict], requires_cookie: bool) -> Optional[dict]:
    headers = {
        'User-Agent': get_user_agent(requires_cookie),
        'Referer': 'https://www.bilibili.com/',
        'Accept': 'application/json, text/plain, */*',
    }
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers) as client:
        try:
            resp = await client.get(url, cookies=cookies or None)
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception as e:
            logger.debug(f"uploader_resolver request failed: {e}")
            return None

async def resolve_name_from_mid(mid: str, db_session) -> Optional[str]:
    """根据 mid 解析用户名。返回 name 或 None。优先使用可用 Cookie。"""
    if not mid:
        return None
    cookie = None
    cookies_jar = None
    try:
        cookie = cookie_manager.get_available_cookie(db_session)
        if cookie:
            cookies_jar = {
                'SESSDATA': cookie.sessdata or '',
                'bili_jct': cookie.bili_jct or '',
                'DedeUserID': cookie.dedeuserid or '',
            }
    except Exception:
        cookies_jar = None
    # 首选 wbi acc/info 接口
    url1 = f"https://api.bilibili.com/x/space/wbi/acc/info?mid={mid}"
    data1 = await _request_json(url1, cookies_jar, requires_cookie=bool(cookie))
    if data1:
        try:
            if data1.get('code') == 0:
                info = data1.get('data') or {}
                name = (info.get('name') or info.get('uname') or '').strip()
                if name:
                    return name
            else:
                logger.debug(f"resolve_name_from_mid wbi acc/info non-zero code: {data1.get('code')}, msg={data1.get('message')}")
        except Exception as e:
            logger.debug(f"resolve_name_from_mid wbi acc/info parse error: {e}")
    else:
        logger.debug("resolve_name_from_mid wbi acc/info returned no data")

    # 兜底：使用 x/web-interface/card 接口（通常无需签名）
    url2 = f"https://api.bilibili.com/x/web-interface/card?mid={mid}"
    data2 = await _request_json(url2, cookies_jar, requires_cookie=bool(cookie))
    if not data2:
        logger.debug("resolve_name_from_mid card returned no data")
        return None
    try:
        if data2.get('code') == 0:
            card = (data2.get('data') or {}).get('card') or {}
            name = (card.get('name') or card.get('uname') or '').strip()
            return name or None
        else:
            logger.debug(f"resolve_name_from_mid card non-zero code: {data2.get('code')}, msg={data2.get('message')}")
    except Exception as e:
        logger.debug(f"resolve_name_from_mid card parse error: {e}")
    return None

async def resolve_mid_from_name(name: str, db_session) -> Optional[str]:
    """根据用户名关键词解析 mid。简单采用搜索接口，取第一个最相关结果。
    注意：名称存在歧义时结果可能不准，调用方可根据需要再做确认。
    """
    if not name:
        return None
    cookie = None
    cookies_jar = None
    try:
        cookie = cookie_manager.get_available_cookie(db_session)
        if cookie:
            cookies_jar = {
                'SESSDATA': cookie.sessdata or '',
                'bili_jct': cookie.bili_jct or '',
                'DedeUserID': cookie.dedeuserid or '',
            }
    except Exception:
        cookies_jar = None
    import urllib.parse
    q = urllib.parse.quote(name)
    url = f"https://api.bilibili.com/x/web-interface/search/type?search_type=bili_user&keyword={q}"
    data = await _request_json(url, cookies_jar, requires_cookie=bool(cookie))
    if not data:
        return None
    try:
        if data.get('code') == 0:
            rs = data.get('data') or {}
            lst = rs.get('result') or []
            if lst:
                # 取第一条（可扩展为相似度/完全匹配优先）
                mid = str(lst[0].get('mid') or '').strip()
                return mid or None
    except Exception:
        return None
    return None

class UploaderResolverService:
    async def resolve(self, name: Optional[str], mid: Optional[str], db_session) -> Tuple[Optional[str], Optional[str]]:
        """在允许的时间内尽力解析 name/mid。
        返回 (resolved_name or name, resolved_mid or mid)
        """
        orig_name = (name or '').strip()
        orig_mid = (mid or '').strip()
        resolved_name = orig_name or None
        resolved_mid = orig_mid or None
        tasks = []
        # 并发解析：如果缺 name 且有 mid -> 解析 name；如果缺 mid 且有 name -> 解析 mid
        if (not resolved_name) and resolved_mid:
            tasks.append(resolve_name_from_mid(resolved_mid, db_session))
        if (not resolved_mid) and resolved_name:
            tasks.append(resolve_mid_from_name(resolved_name, db_session))
        if not tasks:
            return resolved_name, resolved_mid
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            results = []
        # 回填
        idx = 0
        if (not resolved_name) and resolved_mid:
            val = results[idx] if idx < len(results) else None
            idx += 1
            if isinstance(val, str) and val:
                resolved_name = val
        if (not resolved_mid) and resolved_name:
            val = results[idx] if idx < len(results) else None
            if isinstance(val, str) and val:
                resolved_mid = val
        return resolved_name, resolved_mid

uploader_resolver_service = UploaderResolverService()
