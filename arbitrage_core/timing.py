"""
高精度时间测量工具

用于追踪套利程序从发现机会到订单提交成功的各个阶段耗时
"""

import time
import logging
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from collections import defaultdict
import statistics
from functools import wraps

logger = logging.getLogger(__name__)


@dataclass
class TimingPoint:
    """时间测量点"""
    name: str
    timestamp: float
    elapsed_from_start: float
    delta_from_previous: float


@dataclass
class TimingSession:
    """完整的时间测量会话"""
    session_id: str
    start_time: float
    points: List[TimingPoint] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_elapsed(self) -> float:
        """总耗时（毫秒）"""
        if not self.points:
            return 0.0
        return (self.points[-1].timestamp - self.start_time) * 1000

    @property
    def success(self) -> bool:
        """是否成功完成（有t7标记）"""
        return any(p.name == "t7_order_success" for p in self.points)


class TimingTracker:
    """时间追踪器 - 记录从t0到t7的各个阶段耗时"""

    def __init__(self):
        self._sessions: Dict[str, TimingSession] = {}
        self._current_session_id: Optional[str] = None
        self._stats: Dict[str, List[float]] = defaultdict(list)
        self._session_counter = 0

    def start_session(self, session_id: Optional[str] = None, **metadata) -> str:
        """
        开始新的计时会话

        Args:
            session_id: 可选的会话ID，如果不提供则自动生成
            **metadata: 会话元数据（如利润率、订单大小等）

        Returns:
            会话ID
        """
        if session_id is None:
            self._session_counter += 1
            session_id = f"session_{self._session_counter}_{int(time.time() * 1000)}"

        start_time = time.perf_counter()
        self._sessions[session_id] = TimingSession(
            session_id=session_id,
            start_time=start_time,
            metadata=metadata
        )
        self._current_session_id = session_id

        # 记录t0
        self.mark("t0_opportunity_found")

        logger.debug(f"⏱️ [TIMING] 开始会话 {session_id}")
        return session_id

    def mark(self, point_name: str, session_id: Optional[str] = None) -> float:
        """
        标记一个时间点

        Args:
            point_name: 时间点名称（如 t1_auto_execute, t2_thread_start 等）
            session_id: 可选的会话ID，如果不提供则使用当前会话

        Returns:
            从上一个时间点到现在的耗时（毫秒）
        """
        if session_id is None:
            session_id = self._current_session_id

        if session_id is None or session_id not in self._sessions:
            logger.warning(f"⚠️ [TIMING] 无效的会话ID: {session_id}")
            return 0.0

        session = self._sessions[session_id]
        current_time = time.perf_counter()
        elapsed_from_start = (current_time - session.start_time) * 1000  # 转换为毫秒

        # 计算与上一个点的时间差
        if session.points:
            delta_from_previous = (current_time - session.points[-1].timestamp) * 1000
        else:
            delta_from_previous = elapsed_from_start

        point = TimingPoint(
            name=point_name,
            timestamp=current_time,
            elapsed_from_start=elapsed_from_start,
            delta_from_previous=delta_from_previous
        )
        session.points.append(point)

        # 记录统计
        self._stats[point_name].append(delta_from_previous)

        logger.debug(
            f"⏱️ [TIMING] {point_name}: "
            f"Δt={delta_from_previous:.2f}ms, "
            f"累计={elapsed_from_start:.2f}ms"
        )

        return delta_from_previous

    def end_session(self, session_id: Optional[str] = None, success: bool = True) -> TimingSession:
        """
        结束计时会话并生成报告

        Args:
            session_id: 可选的会话ID，如果不提供则使用当前会话
            success: 是否成功完成

        Returns:
            完整的会话数据
        """
        if session_id is None:
            session_id = self._current_session_id

        if session_id is None or session_id not in self._sessions:
            logger.warning(f"⚠️ [TIMING] 无效的会话ID: {session_id}")
            return None

        session = self._sessions[session_id]

        # 记录t7（如果成功）
        if success:
            self.mark("t7_order_success", session_id)

        # 生成报告
        self._log_session_report(session)

        # 清理当前会话ID
        if self._current_session_id == session_id:
            self._current_session_id = None

        return session

    def _log_session_report(self, session: TimingSession):
        """生成并记录会话报告"""
        total_elapsed = session.total_elapsed

        # 判断是否在150ms阈值内
        threshold = 150.0
        status = "✅ SUCCESS" if total_elapsed < threshold else "❌ TIMEOUT"

        logger.info(f"\n{'='*60}")
        logger.info(f"⏱️ [TIMING REPORT] {session.session_id}")
        logger.info(f"{'='*60}")
        logger.info(f"总耗时: {total_elapsed:.2f}ms {status} (阈值: {threshold}ms)")

        if session.metadata:
            logger.info(f"元数据: {session.metadata}")

        logger.info(f"\n{'阶段':<30} {'耗时(ms)':<12} {'累计(ms)':<12}")
        logger.info(f"{'-'*54}")

        for point in session.points:
            logger.info(
                f"{point.name:<30} "
                f"{point.delta_from_previous:>10.2f}ms "
                f"{point.elapsed_from_start:>10.2f}ms"
            )

        logger.info(f"{'='*60}\n")

    def get_statistics(self) -> Dict[str, Dict[str, float]]:
        """
        获取各阶段的统计信息

        Returns:
            各阶段的平均值、中位数、最小值、最大值
        """
        stats = {}
        for point_name, timings in self._stats.items():
            if not timings:
                continue

            stats[point_name] = {
                "count": len(timings),
                "mean": statistics.mean(timings),
                "median": statistics.median(timings),
                "min": min(timings),
                "max": max(timings),
                "stdev": statistics.stdev(timings) if len(timings) > 1 else 0.0,
            }

        return stats

    def log_statistics(self):
        """记录统计信息到日志"""
        stats = self.get_statistics()

        if not stats:
            logger.info("⏱️ [TIMING STATS] 暂无统计数据")
            return

        logger.info(f"\n{'='*80}")
        logger.info(f"⏱️ [TIMING STATISTICS] 各阶段耗时统计")
        logger.info(f"{'='*80}")
        logger.info(
            f"{'阶段':<30} {'次数':<8} {'平均':<10} {'中位数':<10} {'最小':<10} {'最大':<10} {'标准差':<10}"
        )
        logger.info(f"{'-'*80}")

        for point_name in sorted(stats.keys()):
            stat = stats[point_name]
            logger.info(
                f"{point_name:<30} "
                f"{stat['count']:<8} "
                f"{stat['mean']:>8.2f}ms "
                f"{stat['median']:>8.2f}ms "
                f"{stat['min']:>8.2f}ms "
                f"{stat['max']:>8.2f}ms "
                f"{stat['stdev']:>8.2f}ms"
            )

        logger.info(f"{'='*80}\n")

    def get_session(self, session_id: str) -> Optional[TimingSession]:
        """获取指定会话的数据"""
        return self._sessions.get(session_id)

    def get_all_sessions(self) -> List[TimingSession]:
        """获取所有会话数据"""
        return list(self._sessions.values())

    def clear_old_sessions(self, keep_last_n: int = 100):
        """清理旧会话，只保留最近的N个"""
        if len(self._sessions) <= keep_last_n:
            return

        # 按时间排序，删除旧的
        sorted_sessions = sorted(
            self._sessions.items(),
            key=lambda x: x[1].start_time,
            reverse=True
        )

        # 只保留最近的N个
        self._sessions = dict(sorted_sessions[:keep_last_n])
        logger.debug(f"⏱️ [TIMING] 清理旧会话，保留最近 {keep_last_n} 个")


def timing_decorator(point_name: str):
    """
    装饰器：自动测量函数执行时间

    Args:
        point_name: 时间点名称

    Example:
        @timing_decorator("t3_order_prepare")
        def prepare_order():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 查找self参数（如果是方法）
            tracker = None
            if args and hasattr(args[0], '_timing_tracker'):
                tracker = args[0]._timing_tracker

            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = (time.perf_counter() - start) * 1000

                if tracker:
                    tracker.mark(point_name)
                else:
                    logger.debug(f"⏱️ [TIMING] {point_name}: {elapsed:.2f}ms")

        return wrapper
    return decorator


class TokenBucketMonitor:
    """Token Bucket 等待时间监控"""

    def __init__(self):
        self._wait_times: List[float] = []
        self._blocked_count = 0
        self._total_count = 0

    def record_wait(self, wait_time_ms: float):
        """记录等待时间"""
        self._total_count += 1
        if wait_time_ms > 0:
            self._wait_times.append(wait_time_ms)
            self._blocked_count += 1

            if wait_time_ms > 1000:  # 超过1秒
                logger.warning(
                    f"⚠️ [TOKEN BUCKET] 严重阻塞: {wait_time_ms:.2f}ms "
                    f"(已阻塞 {self._blocked_count}/{self._total_count} 次)"
                )
            elif wait_time_ms > 100:  # 超过100ms
                logger.info(
                    f"⏱️ [TOKEN BUCKET] 阻塞: {wait_time_ms:.2f}ms"
                )

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        if not self._wait_times:
            return {
                "total_requests": self._total_count,
                "blocked_count": 0,
                "blocked_rate": 0.0,
            }

        return {
            "total_requests": self._total_count,
            "blocked_count": self._blocked_count,
            "blocked_rate": self._blocked_count / self._total_count if self._total_count > 0 else 0.0,
            "mean_wait_time": statistics.mean(self._wait_times),
            "median_wait_time": statistics.median(self._wait_times),
            "max_wait_time": max(self._wait_times),
            "p95_wait_time": statistics.quantiles(self._wait_times, n=20)[18] if len(self._wait_times) > 20 else max(self._wait_times),
        }

    def log_statistics(self):
        """记录统计信息"""
        stats = self.get_statistics()

        logger.info(f"\n{'='*60}")
        logger.info(f"⏱️ [TOKEN BUCKET STATS]")
        logger.info(f"{'='*60}")
        logger.info(f"总请求数: {stats['total_requests']}")
        logger.info(f"阻塞次数: {stats['blocked_count']}")
        logger.info(f"阻塞率: {stats['blocked_rate']*100:.2f}%")

        if self._wait_times:
            logger.info(f"平均等待: {stats['mean_wait_time']:.2f}ms")
            logger.info(f"中位数等待: {stats['median_wait_time']:.2f}ms")
            logger.info(f"最大等待: {stats['max_wait_time']:.2f}ms")
            logger.info(f"P95等待: {stats['p95_wait_time']:.2f}ms")

        logger.info(f"{'='*60}\n")


# 全局单例
_global_timing_tracker = TimingTracker()
_global_token_bucket_monitor = TokenBucketMonitor()


def get_timing_tracker() -> TimingTracker:
    """获取全局时间追踪器"""
    return _global_timing_tracker


def get_token_bucket_monitor() -> TokenBucketMonitor:
    """获取全局Token Bucket监控器"""
    return _global_token_bucket_monitor
