"""
STRM性能优化器
负责STRM功能的性能监控、缓存优化和资源管理
"""

import asyncio
import time
import psutil
import threading
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict, deque
from pathlib import Path
import json

from ..core.config import Config
from ..core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PerformanceMetrics:
    """性能指标数据类"""
    timestamp: float
    memory_usage: float  # MB
    cpu_usage: float  # %
    active_streams: int
    cache_hit_rate: float  # %
    response_time: float  # ms
    disk_io_read: float  # MB/s
    disk_io_write: float  # MB/s
    network_io: float  # MB/s


@dataclass
class CacheStats:
    """缓存统计数据类"""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size: int = 0
    max_size: int = 1000
    
    @property
    def hit_rate(self) -> float:
        """计算缓存命中率"""
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0


class STRMCacheManager:
    """STRM缓存管理器"""
    
    def __init__(self, config: Config):
        self.config = config
        self.stream_cache: Dict[str, Any] = {}
        self.hls_cache: Dict[str, Any] = {}
        self.metadata_cache: Dict[str, Any] = {}
        
        # 缓存统计
        self.stream_stats = CacheStats(max_size=config.STRM_STREAM_CACHE_SIZE)
        self.hls_stats = CacheStats(max_size=config.STRM_HLS_CACHE_SIZE)
        self.metadata_stats = CacheStats(max_size=config.STRM_METADATA_CACHE_SIZE)
        
        # 缓存过期时间
        self.stream_ttl = config.STRM_STREAM_TTL
        self.hls_ttl = config.STRM_HLS_TTL
        self.metadata_ttl = config.STRM_METADATA_TTL
        
        # 启动清理任务
        self._start_cleanup_task()
    
    async def get_stream_data(self, stream_key: str) -> Optional[Any]:
        """获取流数据"""
        if stream_key in self.stream_cache:
            data, timestamp = self.stream_cache[stream_key]
            if time.time() - timestamp < self.stream_ttl:
                self.stream_stats.hits += 1
                return data
            else:
                # 过期删除
                del self.stream_cache[stream_key]
        
        self.stream_stats.misses += 1
        return None
    
    async def set_stream_data(self, stream_key: str, data: Any) -> None:
        """设置流数据"""
        # 检查缓存大小限制
        if len(self.stream_cache) >= self.stream_stats.max_size:
            await self._evict_oldest_stream()
        
        self.stream_cache[stream_key] = (data, time.time())
        self.stream_stats.size = len(self.stream_cache)
    
    async def get_hls_segment(self, segment_key: str) -> Optional[bytes]:
        """获取HLS片段"""
        if segment_key in self.hls_cache:
            data, timestamp = self.hls_cache[segment_key]
            if time.time() - timestamp < self.hls_ttl:
                self.hls_stats.hits += 1
                return data
            else:
                del self.hls_cache[segment_key]
        
        self.hls_stats.misses += 1
        return None
    
    async def set_hls_segment(self, segment_key: str, data: bytes) -> None:
        """设置HLS片段"""
        if len(self.hls_cache) >= self.hls_stats.max_size:
            await self._evict_oldest_hls()
        
        self.hls_cache[segment_key] = (data, time.time())
        self.hls_stats.size = len(self.hls_cache)
    
    async def get_metadata(self, video_id: str) -> Optional[Dict]:
        """获取视频元数据"""
        if video_id in self.metadata_cache:
            data, timestamp = self.metadata_cache[video_id]
            if time.time() - timestamp < self.metadata_ttl:
                self.metadata_stats.hits += 1
                return data
            else:
                del self.metadata_cache[video_id]
        
        self.metadata_stats.misses += 1
        return None
    
    async def set_metadata(self, video_id: str, data: Dict) -> None:
        """设置视频元数据"""
        if len(self.metadata_cache) >= self.metadata_stats.max_size:
            await self._evict_oldest_metadata()
        
        self.metadata_cache[video_id] = (data, time.time())
        self.metadata_stats.size = len(self.metadata_cache)
    
    async def _evict_oldest_stream(self) -> None:
        """淘汰最旧的流数据"""
        if not self.stream_cache:
            return
        
        oldest_key = min(self.stream_cache.keys(), 
                        key=lambda k: self.stream_cache[k][1])
        del self.stream_cache[oldest_key]
        self.stream_stats.evictions += 1
    
    async def _evict_oldest_hls(self) -> None:
        """淘汰最旧的HLS片段"""
        if not self.hls_cache:
            return
        
        oldest_key = min(self.hls_cache.keys(),
                        key=lambda k: self.hls_cache[k][1])
        del self.hls_cache[oldest_key]
        self.hls_stats.evictions += 1
    
    async def _evict_oldest_metadata(self) -> None:
        """淘汰最旧的元数据"""
        if not self.metadata_cache:
            return
        
        oldest_key = min(self.metadata_cache.keys(),
                        key=lambda k: self.metadata_cache[k][1])
        del self.metadata_cache[oldest_key]
        self.metadata_stats.evictions += 1
    
    def _start_cleanup_task(self) -> None:
        """启动定期清理任务"""
        def cleanup_worker():
            while True:
                try:
                    asyncio.create_task(self._cleanup_expired())
                    time.sleep(300)  # 每5分钟清理一次
                except Exception as e:
                    logger.error(f"缓存清理任务错误: {e}")
        
        cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
        cleanup_thread.start()
    
    async def _cleanup_expired(self) -> None:
        """清理过期缓存"""
        current_time = time.time()
        
        # 清理过期流数据
        expired_streams = [
            key for key, (_, timestamp) in self.stream_cache.items()
            if current_time - timestamp > self.stream_ttl
        ]
        for key in expired_streams:
            del self.stream_cache[key]
        
        # 清理过期HLS片段
        expired_hls = [
            key for key, (_, timestamp) in self.hls_cache.items()
            if current_time - timestamp > self.hls_ttl
        ]
        for key in expired_hls:
            del self.hls_cache[key]
        
        # 清理过期元数据
        expired_metadata = [
            key for key, (_, timestamp) in self.metadata_cache.items()
            if current_time - timestamp > self.metadata_ttl
        ]
        for key in expired_metadata:
            del self.metadata_cache[key]
        
        # 更新统计
        self.stream_stats.size = len(self.stream_cache)
        self.hls_stats.size = len(self.hls_cache)
        self.metadata_stats.size = len(self.metadata_cache)
        
        if expired_streams or expired_hls or expired_metadata:
            logger.info(f"清理过期缓存: 流({len(expired_streams)}) "
                       f"HLS({len(expired_hls)}) 元数据({len(expired_metadata)})")
    
    async def clear_all_cache(self) -> Dict[str, int]:
        """清空所有缓存"""
        stream_count = len(self.stream_cache)
        hls_count = len(self.hls_cache)
        metadata_count = len(self.metadata_cache)
        
        self.stream_cache.clear()
        self.hls_cache.clear()
        self.metadata_cache.clear()
        
        # 重置统计
        self.stream_stats = CacheStats(max_size=self.stream_stats.max_size)
        self.hls_stats = CacheStats(max_size=self.hls_stats.max_size)
        self.metadata_stats = CacheStats(max_size=self.metadata_stats.max_size)
        
        return {
            'stream_cleared': stream_count,
            'hls_cleared': hls_count,
            'metadata_cleared': metadata_count
        }
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        return {
            'stream_cache': {
                'hits': self.stream_stats.hits,
                'misses': self.stream_stats.misses,
                'hit_rate': self.stream_stats.hit_rate,
                'size': self.stream_stats.size,
                'max_size': self.stream_stats.max_size,
                'evictions': self.stream_stats.evictions
            },
            'hls_cache': {
                'hits': self.hls_stats.hits,
                'misses': self.hls_stats.misses,
                'hit_rate': self.hls_stats.hit_rate,
                'size': self.hls_stats.size,
                'max_size': self.hls_stats.max_size,
                'evictions': self.hls_stats.evictions
            },
            'metadata_cache': {
                'hits': self.metadata_stats.hits,
                'misses': self.metadata_stats.misses,
                'hit_rate': self.metadata_stats.hit_rate,
                'size': self.metadata_stats.size,
                'max_size': self.metadata_stats.max_size,
                'evictions': self.metadata_stats.evictions
            }
        }


class STRMPerformanceMonitor:
    """STRM性能监控器"""
    
    def __init__(self, config: Config):
        self.config = config
        self.metrics_history: deque = deque(maxlen=1000)  # 保留最近1000个指标
        self.active_streams: Dict[str, float] = {}  # 活跃流及其开始时间
        self.response_times: deque = deque(maxlen=100)  # 最近100个响应时间
        
        # 性能阈值
        self.memory_threshold = config.STRM_MEMORY_THRESHOLD  # MB
        self.cpu_threshold = config.STRM_CPU_THRESHOLD  # %
        self.response_time_threshold = config.STRM_RESPONSE_TIME_THRESHOLD  # ms
        
        # 启动监控任务
        self._start_monitoring()
    
    def _start_monitoring(self) -> None:
        """启动性能监控"""
        def monitor_worker():
            while True:
                try:
                    asyncio.create_task(self._collect_metrics())
                    time.sleep(30)  # 每30秒收集一次指标
                except Exception as e:
                    logger.error(f"性能监控错误: {e}")
        
        monitor_thread = threading.Thread(target=monitor_worker, daemon=True)
        monitor_thread.start()
    
    async def _collect_metrics(self) -> None:
        """收集性能指标"""
        try:
            # 系统资源指标
            memory_info = psutil.virtual_memory()
            cpu_percent = psutil.cpu_percent(interval=1)
            disk_io = psutil.disk_io_counters()
            network_io = psutil.net_io_counters()
            
            # 计算平均响应时间
            avg_response_time = (
                sum(self.response_times) / len(self.response_times)
                if self.response_times else 0
            )
            
            # 创建性能指标
            metrics = PerformanceMetrics(
                timestamp=time.time(),
                memory_usage=memory_info.used / 1024 / 1024,  # MB
                cpu_usage=cpu_percent,
                active_streams=len(self.active_streams),
                cache_hit_rate=0.0,  # 将由缓存管理器提供
                response_time=avg_response_time,
                disk_io_read=disk_io.read_bytes / 1024 / 1024 if disk_io else 0,
                disk_io_write=disk_io.write_bytes / 1024 / 1024 if disk_io else 0,
                network_io=network_io.bytes_sent / 1024 / 1024 if network_io else 0
            )
            
            self.metrics_history.append(metrics)
            
            # 检查性能阈值
            await self._check_performance_thresholds(metrics)
            
        except Exception as e:
            logger.error(f"收集性能指标失败: {e}")
    
    async def _check_performance_thresholds(self, metrics: PerformanceMetrics) -> None:
        """检查性能阈值"""
        warnings = []
        
        if metrics.memory_usage > self.memory_threshold:
            warnings.append(f"内存使用过高: {metrics.memory_usage:.1f}MB > {self.memory_threshold}MB")
        
        if metrics.cpu_usage > self.cpu_threshold:
            warnings.append(f"CPU使用过高: {metrics.cpu_usage:.1f}% > {self.cpu_threshold}%")
        
        if metrics.response_time > self.response_time_threshold:
            warnings.append(f"响应时间过长: {metrics.response_time:.1f}ms > {self.response_time_threshold}ms")
        
        if warnings:
            logger.warning(f"性能警告: {'; '.join(warnings)}")
    
    def record_response_time(self, response_time: float) -> None:
        """记录响应时间"""
        self.response_times.append(response_time)
    
    def start_stream(self, stream_key: str) -> None:
        """记录流开始"""
        self.active_streams[stream_key] = time.time()
    
    def stop_stream(self, stream_key: str) -> None:
        """记录流结束"""
        if stream_key in self.active_streams:
            del self.active_streams[stream_key]
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计"""
        if not self.metrics_history:
            return {}
        
        recent_metrics = list(self.metrics_history)[-10:]  # 最近10个指标
        
        return {
            'current': {
                'memory_usage': recent_metrics[-1].memory_usage,
                'cpu_usage': recent_metrics[-1].cpu_usage,
                'active_streams': recent_metrics[-1].active_streams,
                'response_time': recent_metrics[-1].response_time
            },
            'average': {
                'memory_usage': sum(m.memory_usage for m in recent_metrics) / len(recent_metrics),
                'cpu_usage': sum(m.cpu_usage for m in recent_metrics) / len(recent_metrics),
                'response_time': sum(m.response_time for m in recent_metrics) / len(recent_metrics)
            },
            'peak': {
                'memory_usage': max(m.memory_usage for m in recent_metrics),
                'cpu_usage': max(m.cpu_usage for m in recent_metrics),
                'response_time': max(m.response_time for m in recent_metrics)
            },
            'thresholds': {
                'memory_threshold': self.memory_threshold,
                'cpu_threshold': self.cpu_threshold,
                'response_time_threshold': self.response_time_threshold
            }
        }


class STRMPerformanceOptimizer:
    """STRM性能优化器主类"""
    
    def __init__(self, config: Config):
        self.config = config
        self.cache_manager = STRMCacheManager(config)
        self.performance_monitor = STRMPerformanceMonitor(config)
        
        # 优化策略配置
        self.optimization_enabled = config.STRM_OPTIMIZATION_ENABLED
        self.auto_scaling_enabled = config.STRM_AUTO_SCALING_ENABLED
        
    async def optimize_cache_strategy(self) -> Dict[str, Any]:
        """优化缓存策略"""
        cache_stats = self.cache_manager.get_cache_stats()
        optimizations = []
        
        # 分析缓存命中率
        for cache_type, stats in cache_stats.items():
            hit_rate = stats['hit_rate']
            
            if hit_rate < 50:  # 命中率低于50%
                if cache_type == 'stream_cache':
                    # 增加流缓存TTL
                    self.cache_manager.stream_ttl = min(
                        self.cache_manager.stream_ttl * 1.2,
                        3600  # 最大1小时
                    )
                    optimizations.append(f"增加{cache_type} TTL到{self.cache_manager.stream_ttl}秒")
                
                elif cache_type == 'hls_cache':
                    # 增加HLS缓存大小
                    self.cache_manager.hls_stats.max_size = min(
                        self.cache_manager.hls_stats.max_size * 1.1,
                        5000  # 最大5000个片段
                    )
                    optimizations.append(f"增加{cache_type}大小到{self.cache_manager.hls_stats.max_size}")
            
            elif hit_rate > 90:  # 命中率很高
                if stats['size'] < stats['max_size'] * 0.5:
                    # 可以减少缓存大小
                    if cache_type == 'hls_cache':
                        self.cache_manager.hls_stats.max_size = max(
                            self.cache_manager.hls_stats.max_size * 0.9,
                            1000  # 最小1000个片段
                        )
                        optimizations.append(f"减少{cache_type}大小到{self.cache_manager.hls_stats.max_size}")
        
        return {
            'optimizations_applied': optimizations,
            'cache_stats': cache_stats
        }
    
    async def optimize_resource_usage(self) -> Dict[str, Any]:
        """优化资源使用"""
        perf_stats = self.performance_monitor.get_performance_stats()
        optimizations = []
        
        if not perf_stats:
            return {'optimizations_applied': [], 'message': '暂无性能数据'}
        
        current = perf_stats['current']
        thresholds = perf_stats['thresholds']
        
        # 内存优化
        if current['memory_usage'] > thresholds['memory_threshold'] * 0.8:
            # 清理部分缓存
            cleared = await self.cache_manager.clear_all_cache()
            optimizations.append(f"清理缓存释放内存: {cleared}")
        
        # CPU优化
        if current['cpu_usage'] > thresholds['cpu_threshold'] * 0.8:
            # 减少并发流数量
            if current['active_streams'] > 5:
                optimizations.append("建议减少并发流数量")
        
        # 响应时间优化
        if current['response_time'] > thresholds['response_time_threshold'] * 0.8:
            # 预加载热门内容
            optimizations.append("启用内容预加载策略")
        
        return {
            'optimizations_applied': optimizations,
            'performance_stats': perf_stats
        }
    
    async def get_optimization_recommendations(self) -> List[str]:
        """获取优化建议"""
        recommendations = []
        
        # 缓存优化建议
        cache_stats = self.cache_manager.get_cache_stats()
        for cache_type, stats in cache_stats.items():
            if stats['hit_rate'] < 70:
                recommendations.append(f"考虑调整{cache_type}的TTL或大小以提高命中率")
            
            if stats['evictions'] > stats['hits'] * 0.1:
                recommendations.append(f"{cache_type}淘汰率过高，建议增加缓存大小")
        
        # 性能优化建议
        perf_stats = self.performance_monitor.get_performance_stats()
        if perf_stats:
            current = perf_stats['current']
            average = perf_stats['average']
            
            if current['memory_usage'] > average['memory_usage'] * 1.5:
                recommendations.append("当前内存使用异常高，检查是否有内存泄漏")
            
            if current['response_time'] > average['response_time'] * 2:
                recommendations.append("响应时间异常，检查网络连接和服务器负载")
            
            if current['active_streams'] > 10:
                recommendations.append("活跃流数量较多，考虑启用负载均衡")
        
        # 通用建议
        recommendations.extend([
            "定期监控缓存命中率和性能指标",
            "根据使用模式调整缓存策略",
            "考虑使用CDN加速内容分发",
            "实施流量控制防止过载",
            "定期清理过期缓存和临时文件"
        ])
        
        return recommendations
    
    async def generate_performance_report(self) -> Dict[str, Any]:
        """生成性能报告"""
        cache_stats = self.cache_manager.get_cache_stats()
        perf_stats = self.performance_monitor.get_performance_stats()
        recommendations = await self.get_optimization_recommendations()
        
        # 计算总体健康评分
        health_score = 100
        
        # 缓存健康评分
        avg_hit_rate = sum(stats['hit_rate'] for stats in cache_stats.values()) / len(cache_stats)
        if avg_hit_rate < 50:
            health_score -= 20
        elif avg_hit_rate < 70:
            health_score -= 10
        
        # 性能健康评分
        if perf_stats:
            current = perf_stats['current']
            thresholds = perf_stats['thresholds']
            
            if current['memory_usage'] > thresholds['memory_threshold']:
                health_score -= 15
            if current['cpu_usage'] > thresholds['cpu_threshold']:
                health_score -= 15
            if current['response_time'] > thresholds['response_time_threshold']:
                health_score -= 10
        
        health_score = max(0, health_score)
        
        return {
            'timestamp': time.time(),
            'health_score': health_score,
            'cache_performance': cache_stats,
            'system_performance': perf_stats,
            'recommendations': recommendations,
            'optimization_status': {
                'optimization_enabled': self.optimization_enabled,
                'auto_scaling_enabled': self.auto_scaling_enabled
            }
        }
