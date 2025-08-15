# 错误处理与重试策略（Error Handling Strategy）

更新时间：2025-01-15 21:15 (Asia/Shanghai)

## 错误分类体系

### 1. 网络相关错误

#### 连接错误
- **ConnectionError**: 网络连接失败
- **TimeoutError**: 请求超时
- **DNSError**: DNS解析失败
- **处理策略**: 可重试，指数退避

#### HTTP状态码错误
- **429 Too Many Requests**: 请求频率限制
  - 处理：等待后重试，增加延迟
  - 重试间隔：60-300秒随机
- **503 Service Unavailable**: 服务不可用
  - 处理：短暂等待后重试
  - 重试间隔：30-120秒
- **502/504 Gateway Error**: 网关错误
  - 处理：可重试，较短间隔

### 2. 认证与权限错误

#### Cookie相关
- **401 Unauthorized**: Cookie无效或过期
  - 处理：标记Cookie失效，切换到下一个可用Cookie
  - 不重试当前请求，记录失败原因
- **403 Forbidden**: 权限不足或内容受限
  - 处理：检查是否为会员内容，记录跳过原因
  - 不重试，标记为跳过状态

#### 风控检测
- **412 Precondition Failed**: 触发风控
  - 处理：仅记录警告，不计入失败
  - 增加延迟，降低请求频率
- **-412 风控响应**: B站特殊风控码
  - 处理：全局中间件拦截，延长休眠时间

### 3. 内容相关错误

#### 视频不存在
- **404 Not Found**: 视频已删除或不存在
  - 处理：标记为已删除，不重试
  - 更新数据库状态为`deleted`

#### 内容解析错误
- **ParsingError**: 页面结构变化导致解析失败
  - 处理：可重试，可能是临时问题
  - 超过重试限制后报告给开发者

### 4. 系统资源错误

#### 磁盘空间
- **DiskSpaceError**: 磁盘空间不足
  - 处理：暂停所有下载任务
  - 发送通知给用户，不自动重试

#### 内存不足
- **MemoryError**: 内存不足
  - 处理：降低并发数，清理缓存
  - 可重试，但降低优先级

#### 文件权限
- **PermissionError**: 文件写入权限不足
  - 处理：检查目录权限，不重试
  - 记录详细错误信息供用户排查

## 重试策略配置

### 指数退避算法
```python
def exponential_backoff(attempt: int, base_delay: int = 1) -> int:
    """
    指数退避算法
    attempt: 重试次数 (0, 1, 2, ...)
    base_delay: 基础延迟时间（秒）
    """
    delay = base_delay * (2 ** attempt)
    jitter = random.uniform(0.5, 1.5)  # 添加随机抖动
    return min(delay * jitter, 300)  # 最大5分钟
```

### 任务类型重试配置
```yaml
retry_config:
  download:
    max_retries: 3
    base_delay: 60
    timeout: 1800
    retryable_errors: [ConnectionError, TimeoutError, 503, 502, 504]
  
  scan:
    max_retries: 2
    base_delay: 30
    timeout: 300
    retryable_errors: [ConnectionError, TimeoutError]
  
  check:
    max_retries: 1
    base_delay: 10
    timeout: 60
    retryable_errors: [ConnectionError, TimeoutError, 503]
```

### 全局限流保护
```python
class RateLimiter:
    def __init__(self):
        self.request_times = []
        self.max_requests_per_minute = 30
        self.cooldown_period = 300  # 5分钟冷却期
    
    def should_throttle(self) -> bool:
        """检查是否需要限流"""
        now = time.time()
        # 清理1分钟前的记录
        self.request_times = [t for t in self.request_times if now - t < 60]
        
        if len(self.request_times) >= self.max_requests_per_minute:
            return True
        return False
```

## Cookie管理策略

### Cookie池轮换
```python
class CookieManager:
    def get_next_cookie(self) -> Optional[Cookie]:
        """获取下一个可用Cookie"""
        available_cookies = [c for c in self.cookies if c.is_valid and c.is_active]
        
        if not available_cookies:
            return None
            
        # 选择最少使用的Cookie
        return min(available_cookies, key=lambda c: c.failure_count)
    
    def mark_cookie_failed(self, cookie_id: int, error_type: str):
        """标记Cookie失败"""
        cookie = self.get_cookie(cookie_id)
        cookie.failure_count += 1
        cookie.last_failed = datetime.now()
        
        # 连续失败3次则暂时禁用
        if cookie.failure_count >= 3:
            cookie.is_active = False
            self.logger.warning(f"Cookie {cookie.name} disabled due to repeated failures")
```

### Cookie验证机制
```python
async def validate_cookie(cookie: Cookie) -> bool:
    """验证Cookie有效性"""
    try:
        # 调用B站API检查登录状态
        response = await self.session.get(
            "https://api.bilibili.com/x/web-interface/nav",
            cookies=cookie.to_dict()
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get("code") == 0
        return False
        
    except Exception as e:
        self.logger.error(f"Cookie validation failed: {e}")
        return False
```

## 错误监控与告警

### 错误统计
```python
class ErrorMonitor:
    def __init__(self):
        self.error_counts = defaultdict(int)
        self.error_rates = {}
    
    def record_error(self, error_type: str, task_type: str):
        """记录错误"""
        key = f"{task_type}:{error_type}"
        self.error_counts[key] += 1
        
        # 计算错误率
        total_tasks = self.get_total_tasks(task_type)
        if total_tasks > 0:
            self.error_rates[key] = self.error_counts[key] / total_tasks
    
    def should_alert(self, error_type: str, task_type: str) -> bool:
        """判断是否需要告警"""
        key = f"{task_type}:{error_type}"
        rate = self.error_rates.get(key, 0)
        
        # 错误率超过20%时告警
        return rate > 0.2 and self.error_counts[key] > 5
```

### 告警通知
```python
class AlertManager:
    def send_alert(self, alert_type: str, message: str, severity: str = "warning"):
        """发送告警通知"""
        alert = {
            "type": alert_type,
            "message": message,
            "severity": severity,
            "timestamp": datetime.now().isoformat(),
            "hostname": socket.gethostname()
        }
        
        # 记录到日志
        self.logger.warning(f"ALERT: {alert}")
        
        # 可扩展：发送到外部监控系统
        # self.send_to_external_monitor(alert)
```

## 恢复策略

### 自动恢复机制
1. **Cookie自动恢复**: 定期重新验证被禁用的Cookie
2. **任务自动重启**: 系统重启后自动恢复未完成任务
3. **队列自动清理**: 清理长时间卡住的任务

### 手动恢复工具
1. **重置Cookie状态**: 手动重新激活Cookie
2. **强制重试任务**: 忽略重试限制强制重试
3. **清理异常状态**: 清理卡在中间状态的任务

### 数据一致性保证
1. **事务性操作**: 关键操作使用数据库事务
2. **状态检查点**: 定期保存任务执行状态
3. **回滚机制**: 失败时回滚到上一个稳定状态

## 用户友好的错误提示

### 错误分类展示
- **网络问题**: "网络连接不稳定，请检查网络设置"
- **Cookie问题**: "登录信息已过期，请更新Cookie"
- **权限问题**: "该内容需要会员权限或已被删除"
- **存储问题**: "磁盘空间不足，请清理存储空间"

### 解决建议
- **提供具体步骤**: 如何获取新Cookie、如何检查网络
- **相关链接**: 指向详细的故障排除文档
- **联系方式**: 提供技术支持渠道
