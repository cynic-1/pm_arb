"""
WebSocket跨平台套利检测器 - Opinion vs Polymarket
基于 modular_arbitrage.py 的 WebSocket 订单簿版本

最小运行说明:
1) 准备环境变量（.env）
    - Opinion: OPINION_API_KEY
    - Polymarket: 与现有 PlatformClients 配置一致（API key/secret/passphrase 或签名所需变量）

2) 准备市场匹配文件
    - 默认读取: market_matches.json
    - 或通过 --matches-file 指定

3) 启动（单次扫描）
    python arb_websocket.py --pro --pro-once --matches-file market_matches.json

4) 启动（循环扫描）
    python arb_websocket.py --pro --loop-interval 2 --matches-file market_matches.json

6) 可选开关
    - --opinion-bootstrap-rest : 在 Opinion 纯WS无初始簿时，启用 REST bootstrap 补偿
    - --ws-status-interval 10  : 每10秒输出一次两边 WebSocket 运行状态

5) 快速自检
    - 启动后应看到 “WebSocket 连接与订阅完成”
    - 若未连接成功，优先检查 API Key、网络与订阅 market/token 是否有效
"""

import os
import argparse
import time
import threading
import traceback
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv


class TokenBucket:
    """令牌桶算法实现，支持真正的并行请求速率限制"""

    def __init__(self, rate: float, capacity: int):
        """
        Args:
            rate: 每秒补充的令牌数（即最大RPS）
            capacity: 桶的容量（允许的突发请求数）
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_update = time.perf_counter()
        self.lock = threading.Lock()

    def acquire(self, timeout: float = 5.0) -> bool:
        """
        获取一个令牌，如果没有可用令牌则等待

        Args:
            timeout: 最大等待时间（秒）

        Returns:
            True 如果成功获取令牌，False 如果超时
        """
        deadline = time.perf_counter() + timeout

        while True:
            with self.lock:
                now = time.perf_counter()
                # 补充令牌
                elapsed = now - self.last_update
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_update = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True

                # 计算需要等待的时间
                wait_time = (1.0 - self.tokens) / self.rate

            # 检查是否超时
            if time.perf_counter() + wait_time > deadline:
                return False

            # 等待一小段时间后重试
            time.sleep(min(wait_time, 0.01))

# 加载环境变量
load_dotenv()

# 导入核心模块
from arbitrage_core import (
    ArbitrageConfig,
    PlatformClients,
    FeeCalculator,
    OrderBookLevel,
    OrderBookSnapshot,
    MarketMatch,
    ArbitrageOpportunity,
    WebSocketManager,
)
from arbitrage_core.utils import setup_logger
from arbitrage_core.utils.helpers import to_float, to_int, dedupe_tokens, infer_tick_size_from_price
from arbitrage_core.timing import get_timing_tracker, get_token_bucket_monitor

# Opinion SDK
from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER

# Polymarket SDK
from py_clob_client.clob_types import OrderArgs, OrderType, BookParams, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY, SELL
import logging
import json

logger = logging.getLogger(__name__)


class ModularArbitrage:
    """WebSocket 跨平台套利检测器"""

    def __init__(
        self,
        config: Optional[ArbitrageConfig] = None,
        opinion_bootstrap_rest: Optional[bool] = None,
        ws_status_interval: Optional[float] = None,
    ):
        """
        初始化套利检测器

        Args:
            config: 配置对象，如果为 None 则创建默认配置
        """
        # 使用配置对象
        self.config = config or ArbitrageConfig()

        # 初始化核心组件
        print("🔧 初始化核心组件...")
        self.clients = PlatformClients(self.config)
        self.fee_calculator = FeeCalculator(self.config)
        self.ws_manager = WebSocketManager(self.config, self.clients.get_opinion_client())
        self._ws_connected = False
        self._ws_lock = threading.Lock()
        self._poly_no_to_yes: Dict[str, str] = {}
        self._poly_yes_to_no: Dict[str, str] = {}
        self._opinion_bootstrap_rest = (
            opinion_bootstrap_rest
            if opinion_bootstrap_rest is not None
            else os.getenv("OPINION_BOOTSTRAP_REST", "0") in {"1", "true", "True"}
        )
        self._ws_status_interval = max(
            1.0,
            ws_status_interval
            if ws_status_interval is not None
            else float(os.getenv("WS_STATUS_INTERVAL", "10")),
        )
        self._ws_status_thread: Optional[threading.Thread] = None
        self._ws_status_stop_event = threading.Event()

        # 市场匹配缓存
        self.market_matches: List[MarketMatch] = []

        # 线程控制
        self._monitor_stop_event = threading.Event()
        self._active_exec_threads: List[threading.Thread] = []
        self._insufficient_balance_flag = threading.Event()  # 余额不足标志
        self._last_immediate_exec_time: float = 0.0  # 上次立即套利执行时间
        self._immediate_exec_lock = threading.Lock()  # 保护时间戳的锁

        # 速率限制 - 使用令牌桶算法
        # capacity 设置为 workers 数量，允许并行请求同时发出
        self._opinion_token_bucket = TokenBucket(
            rate=self.config.opinion_max_rps,
            capacity=self.config.opinion_orderbook_workers
        )

        # 时间测量追踪器
        self._timing_tracker = get_timing_tracker()
        self._token_bucket_monitor = get_token_bucket_monitor()

        print("✅ 模块化套利检测器初始化完成!\n")

    # ==================== 订单簿管理 ====================

    def _build_ws_subscriptions(self) -> Tuple[List[str], List[int]]:
        """根据市场匹配构建 WebSocket 订阅列表"""
        poly_assets: List[str] = []
        opinion_markets: List[int] = []

        for match in self.market_matches:
            if match.polymarket_yes_token:
                poly_assets.append(str(match.polymarket_yes_token))

            if match.opinion_market_id is not None:
                opinion_markets.append(int(match.opinion_market_id))

            if match.opinion_market_id is not None and match.opinion_yes_token:
                self.ws_manager.opinion_ws.set_market_token_mapping(
                    int(match.opinion_market_id),
                    str(match.opinion_yes_token),
                )

        return dedupe_tokens(poly_assets), sorted(set(opinion_markets))

    def ensure_websocket_ready(self) -> bool:
        """确保 WebSocket 连接可用"""
        if self._ws_connected:
            return True

        with self._ws_lock:
            if self._ws_connected:
                return True

            if not self.market_matches:
                logger.error("❌ 无法启动 WebSocket：市场匹配为空")
                return False

            poly_assets, opinion_markets = self._build_ws_subscriptions()
            if not poly_assets or not opinion_markets:
                logger.error("❌ 无法启动 WebSocket：订阅列表为空")
                return False

            # 纯 WebSocket 为默认；可通过开关启用 Opinion bootstrap 补偿
            self.config.opinion_rest_poll_enabled = bool(self._opinion_bootstrap_rest)
            if self.config.opinion_rest_poll_enabled:
                logger.info("🩹 Opinion bootstrap fallback: 已启用 REST 轮询补偿")
            else:
                logger.info("🧪 Opinion 纯 WebSocket 模式: 已禁用 REST 轮询补偿")

            print(f"📡 启动 WebSocket 订阅: Polymarket(YES资产)={len(poly_assets)}, Opinion市场={len(opinion_markets)}")
            self._ws_connected = self.ws_manager.connect_all(
                polymarket_assets=poly_assets,
                opinion_markets=opinion_markets,
            )
            if self._ws_connected:
                print("✅ WebSocket 连接与订阅完成")
                stats = self.ws_manager.get_stats()
                print(
                    "📊 初始缓存状态: "
                    f"Polymarket={stats['polymarket']['cached_books']}, "
                    f"Opinion={stats['opinion']['cached_books']}"
                )
                self._start_ws_status_logger()
                self._log_ws_runtime_status(force=True)
            else:
                print("❌ WebSocket 连接失败")

        return self._ws_connected

    def _wait_for_websocket_warmup(
        self,
        timeout_seconds: float = 20.0,
        min_poly_books: int = 1,
        min_opinion_books: int = 0,
    ) -> bool:
        """等待 WebSocket 订单簿缓存预热完成"""
        deadline = time.time() + max(0.0, timeout_seconds)

        while time.time() < deadline:
            stats = self.ws_manager.get_stats()
            poly_books = stats["polymarket"]["cached_books"]
            opinion_books = stats["opinion"]["cached_books"]

            if poly_books >= min_poly_books and opinion_books >= min_opinion_books:
                logger.info(
                    f"✅ 订单簿预热完成: Polymarket={poly_books}, Opinion={opinion_books}"
                )
                return True

            time.sleep(0.5)

        stats = self.ws_manager.get_stats()
        logger.warning(
            "⚠️ 订单簿预热超时: "
            f"Polymarket={stats['polymarket']['cached_books']}, "
            f"Opinion={stats['opinion']['cached_books']}"
        )
        return False

    def _log_ws_runtime_status(self, force: bool = False) -> None:
        """记录 WebSocket 运行状态"""
        if not self._ws_connected and not force:
            return

        stats = self.ws_manager.get_stats()
        poly = stats["polymarket"]
        op = stats["opinion"]

        logger.info(
            "📡 WS状态 | "
            f"Poly[connected={poly['connected']}, msgs={poly['messages']}, books={poly['cached_books']}] | "
            f"Opinion[connected={op['connected']}, msgs={op['messages']}, books={op['cached_books']}, "
            f"depth={op.get('depth_updates', 0)}, stable={op.get('stable_notices', 0)}, "
            f"unknown={op.get('unknown_messages', 0)}]"
        )

        if op["connected"] and op["cached_books"] == 0:
            logger.warning(
                "⚠️ Opinion 订单簿仍为空（可能 market.depth.diff 暂无增量），"
                "若持续为空可启用 --opinion-bootstrap-rest"
            )

    def _ws_status_loop(self) -> None:
        """后台循环输出 WebSocket 状态"""
        while not self._ws_status_stop_event.wait(self._ws_status_interval):
            try:
                self._log_ws_runtime_status()
            except Exception as exc:
                logger.debug(f"WS 状态日志线程异常: {exc}")

    def _start_ws_status_logger(self) -> None:
        """启动 WebSocket 状态日志线程"""
        if self._ws_status_thread and self._ws_status_thread.is_alive():
            return

        self._ws_status_stop_event.clear()
        self._ws_status_thread = threading.Thread(
            target=self._ws_status_loop,
            daemon=True,
            name="ws-status-logger",
        )
        self._ws_status_thread.start()

    def close_websockets(self) -> None:
        """关闭所有 WebSocket 连接"""
        self._ws_status_stop_event.set()
        with self._ws_lock:
            if self._ws_connected:
                self.ws_manager.close_all()
                self._ws_connected = False

    def _throttle_opinion_request(self) -> None:
        """Opinion API 速率限制 - 使用令牌桶算法"""
        if self.config.opinion_max_rps <= 0:
            return
        # 获取令牌，最多等待5秒（监控等待时间）
        start_time = time.perf_counter()
        self._opinion_token_bucket.acquire(timeout=5.0)
        wait_time_ms = (time.perf_counter() - start_time) * 1000
        if wait_time_ms > 0.1:  # 只记录有意义的等待
            self._token_bucket_monitor.record_wait(wait_time_ms)

    def get_opinion_orderbook(
        self, token_id: str, depth: int = 5
    ) -> Optional[OrderBookSnapshot]:
        """获取 Opinion 订单簿（WebSocket缓存）"""
        try:
            if not self.ensure_websocket_ready():
                return None

            snapshot = self.ws_manager.get_orderbook(token_id, "opinion")
            if not snapshot:
                return None

            bids = snapshot.bids[:depth]
            asks = snapshot.asks[:depth]
            return OrderBookSnapshot(
                bids=bids,
                asks=asks,
                source="opinion",
                token_id=token_id,
                timestamp=snapshot.timestamp,
            )
        except Exception as exc:
            logger.error(f"⚠️ Opinion 订单簿获取失败 ({token_id[:20]}...): {exc}")
            return None

    def get_polymarket_orderbook(
        self, token_id: str, depth: int = 5
    ) -> Optional[OrderBookSnapshot]:
        """获取 Polymarket 订单簿（WebSocket缓存）"""
        try:
            if not self.ensure_websocket_ready():
                return None

            snapshot = self.ws_manager.get_orderbook(token_id, "polymarket")

            # 仅订阅了 YES token：若请求的是 NO token，则由对应 YES 推导
            if snapshot is None and token_id in self._poly_no_to_yes:
                yes_token = self._poly_no_to_yes[token_id]
                yes_snapshot = self.ws_manager.get_orderbook(yes_token, "polymarket")
                if yes_snapshot:
                    snapshot = self.derive_no_orderbook(yes_snapshot, token_id)

            if not snapshot:
                return None

            bids = snapshot.bids[:depth]
            asks = snapshot.asks[:depth]
            return OrderBookSnapshot(
                bids=bids,
                asks=asks,
                source="polymarket",
                token_id=token_id,
                timestamp=snapshot.timestamp,
            )
        except Exception as exc:
            logger.error(f"⚠️ Polymarket 订单簿获取失败 ({token_id[:20]}...): {exc}")
            return None

    def get_polymarket_orderbooks_bulk(
        self, token_ids: List[str], depth: int = 5
    ) -> Dict[str, OrderBookSnapshot]:
        """批量获取 Polymarket 订单簿（WebSocket缓存）"""
        snapshots: Dict[str, OrderBookSnapshot] = {}
        tokens = dedupe_tokens(token_ids)
        if not tokens:
            return snapshots

        if not self.ensure_websocket_ready():
            return snapshots

        for token_id in tokens:
            snapshot = self.get_polymarket_orderbook(token_id, depth)
            if snapshot:
                snapshots[token_id] = snapshot

        return snapshots

    def fetch_opinion_orderbooks_parallel(
        self, token_ids: List[str], depth: int = 5
    ) -> Dict[str, Optional[OrderBookSnapshot]]:
        """批量获取 Opinion 订单簿（WebSocket缓存）"""

        snapshots: Dict[str, Optional[OrderBookSnapshot]] = {}
        tokens = dedupe_tokens(token_ids)
        if not tokens:
            return snapshots

        if not self.ensure_websocket_ready():
            return snapshots

        for token_id in tokens:
            snapshots[token_id] = self.get_opinion_orderbook(token_id, depth)

        return snapshots

    def _normalize_opinion_levels(
        self, raw_levels: Any, depth: int, reverse: bool
    ) -> List[OrderBookLevel]:
        """标准化 Opinion 订单簿档位"""
        levels: List[OrderBookLevel] = []
        if not raw_levels:
            return levels

        sorted_levels = sorted(
            raw_levels,
            key=lambda x: float(getattr(x, "price", 0.0)),
            reverse=reverse,
        )

        for entry in sorted_levels[:depth]:
            price = self.fee_calculator.round_price(
                to_float(getattr(entry, "price", None))
            )
            size = to_float(
                getattr(entry, "size", None)
                or getattr(entry, "quantity", None)
                or getattr(entry, "maker_amount", None)
                or getattr(entry, "makerAmountInBaseToken", None)
            )

            if price is None or size is None:
                continue

            levels.append(OrderBookLevel(price=price, size=size))

        return levels

    def _normalize_polymarket_levels(
        self, raw_levels: Any, depth: int, reverse: bool
    ) -> List[OrderBookLevel]:
        """标准化 Polymarket 订单簿档位"""
        levels: List[OrderBookLevel] = []
        if not raw_levels:
            return levels

        sorted_levels = sorted(
            raw_levels,
            key=lambda x: float(getattr(x, "price", 0.0)),
            reverse=reverse,
        )

        for entry in sorted_levels[:depth]:
            price = self.fee_calculator.round_price(
                to_float(getattr(entry, "price", None))
            )
            size = to_float(
                getattr(entry, "size", None)
                or getattr(entry, "quantity", None)
                or getattr(entry, "remaining", None)
            )

            if price is None or size is None:
                continue

            levels.append(OrderBookLevel(price=price, size=size))

        return levels

    def derive_no_orderbook(
        self, yes_book: OrderBookSnapshot, no_token_id: str
    ) -> Optional[OrderBookSnapshot]:
        """从 YES token 订单簿推导 NO token 订单簿"""
        if not yes_book:
            return None

        # NO的bids来自YES的asks
        no_bids: List[OrderBookLevel] = []
        for level in yes_book.asks:
            price = self.fee_calculator.round_price(1.0 - level.price)
            if price is None:
                continue
            no_bids.append(OrderBookLevel(price=price, size=level.size))
        no_bids.sort(key=lambda x: x.price, reverse=True)

        # NO的asks来自YES的bids
        no_asks: List[OrderBookLevel] = []
        for level in yes_book.bids:
            price = self.fee_calculator.round_price(1.0 - level.price)
            if price is None:
                continue
            no_asks.append(OrderBookLevel(price=price, size=level.size))
        no_asks.sort(key=lambda x: x.price)

        return OrderBookSnapshot(
            bids=no_bids,
            asks=no_asks,
            source=yes_book.source,
            token_id=no_token_id,
            timestamp=yes_book.timestamp,
        )

    # ==================== 市场匹配加载 ====================

    def load_market_matches(self, filename: str = "market_matches.json") -> bool:
        """从文件加载市场匹配"""
        files = (
            [filename]
            if isinstance(filename, str) and "," not in filename
            else [p.strip() for p in filename.split(",") if p.strip()]
        )

        combined: List[MarketMatch] = []

        for fname in files:
            if not os.path.exists(fname):
                print(f"⚠️ 文件不存在: {fname}")
                continue

            try:
                with open(fname, "r", encoding="utf-8") as f:
                    data = json.load(f)

                for item in data:
                    if isinstance(item, dict):
                        if "cutoff_at" in item:
                            item["cutoff_at"] = to_int(item.get("cutoff_at"))
                        combined.append(MarketMatch(**item))

                print(f"✅ 从 {fname} 加载 {len(data)} 条匹配")
            except Exception as e:
                print(f"⚠️ 读取 {fname} 时出错: {e}")

        if combined:
            self.market_matches = combined

            self._poly_no_to_yes.clear()
            self._poly_yes_to_no.clear()
            for match in self.market_matches:
                if match.polymarket_yes_token and match.polymarket_no_token:
                    yes_token = str(match.polymarket_yes_token)
                    no_token = str(match.polymarket_no_token)
                    self._poly_yes_to_no[yes_token] = no_token
                    self._poly_no_to_yes[no_token] = yes_token

            print(f"✅ 共加载 {len(self.market_matches)} 个市场匹配\n")
            return True

        return False

    # ==================== 盈利性分析 ====================

    def compute_profitability_metrics(
        self,
        match: MarketMatch,
        first_platform: str,
        first_price: Optional[float],
        second_platform: str,
        second_price: Optional[float],
        min_size: Optional[float],
        is_maker_order: bool = False,
    ) -> Optional[Dict[str, float]]:
        """计算盈利性指标

        Args:
            match: 市场匹配对象
            first_platform: 第一个平台名称
            first_price: 第一个平台价格
            second_platform: 第二个平台名称
            second_price: 第二个平台价格
            min_size: 最小数量
            is_maker_order: 是否为流动性做市订单（maker order 不收手续费）
        """
        assumed_size = max(self.config.roi_reference_size, min_size or 0.0)

        # 计算有效价格（含手续费）
        # 如果是 maker order，Opinion 平台不收取手续费，直接使用价格
        if is_maker_order:
            eff_first = self.fee_calculator.round_price(first_price)
            eff_second = self.fee_calculator.round_price(second_price)
        else:
            eff_first = self.fee_calculator.calculate_opinion_cost_per_token(
                first_price, assumed_size
            ) if first_platform == "opinion" else self.fee_calculator.round_price(first_price)

            eff_second = self.fee_calculator.calculate_opinion_cost_per_token(
                second_price, assumed_size
            ) if second_platform == "opinion" else self.fee_calculator.round_price(second_price)

        if eff_first is None or eff_second is None:
            return None

        total_cost = self.fee_calculator.round_price(eff_first + eff_second)
        if total_cost is None or total_cost <= 0:
            return None

        profit = 1.0 - total_cost
        profit_rate_decimal = profit / total_cost
        profit_rate_pct = profit_rate_decimal * 100.0

        # 计算年化收益率
        annualized_pct = None
        if match.cutoff_at:
            seconds_remaining = float(match.cutoff_at) - time.time()
            if seconds_remaining > 0:
                annualized_decimal = profit_rate_decimal * (
                    self.config.seconds_per_year / seconds_remaining
                )
                annualized_pct = annualized_decimal * 100.0

        return {
            "cost": total_cost,
            "profit_rate": profit_rate_pct,
            "annualized_rate": annualized_pct,
            "assumed_size": assumed_size,
        }

    # ==================== 订单执行 ====================

    def place_opinion_order_with_retries(
        self, order: Any, context: str = "", session_id: Optional[str] = None, enable_execution_protection: bool = True
    ) -> Tuple[bool, Optional[Any]]:
        """Opinion 下单带重试

        Args:
            order: 订单对象
            context: 上下文信息
            session_id: 会话ID（用于时间追踪）
            enable_execution_protection: 是否启用执行保护（默认True）
        """
        prefix = f"[{context}] " if context else ""
        last_result = None

        # t4: 进入Opinion SDK（即将调用place_order_fast）
        if session_id:
            self._timing_tracker.mark("t4_enter_opinion_sdk", session_id)

        # opinion下单不重试，因为重试耗时可能导致订单过期
        try:
            # t5和t6将在Opinion SDK内部测量（通过修改SDK）
            result = self.clients.get_opinion_client().place_order_fast(
                order,
                enable_execution_protection=enable_execution_protection
            )
            last_result = result

            if getattr(result, "errno", 0) == 0:
                # t7: 订单提交成功
                if session_id:
                    self._timing_tracker.mark("t7_order_success", session_id)
                return True, result

            err_msg = str(getattr(result, "errmsg", "unknown error"))
            logger.error(
                f"⚠️ {prefix}Opinion 下单失败 : {err_msg}"
            )

            # 检查余额不足错误
            if "insufficient balance" in err_msg.lower() or "balance" in err_msg.lower():
                logger.error(f"\n❌ 检测到 Opinion 余额不足，立即退出程序")
                logger.error(f"错误详情: {err_msg}")
                self._insufficient_balance_flag.set()
                os._exit(1)  # 强制退出整个进程

        except Exception as exc:
            exc_msg = str(exc)
            logger.error(f"⚠️ {prefix}Opinion 下单异常 : {exc_msg}")

            # 检查余额不足错误
            if "insufficient balance" in exc_msg.lower() or "balance" in exc_msg.lower():
                logger.error(f"\n❌ 检测到 Opinion 余额不足异常，立即退出程序")
                logger.error(f"异常详情: {exc_msg}")
                self._insufficient_balance_flag.set()
                os._exit(1)  # 强制退出整个进程


        return False, last_result

    def place_polymarket_order_with_retries(
        self, order_args: Any, order_type: Any, context: str = "", options: Any = None
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Polymarket 下单带重试"""
        prefix = f"[{context}] " if context else ""
        last_result = None

        for attempt in range(1, self.config.order_max_retries + 1):
            try:
                signed_order = self.clients.get_polymarket_client().create_order(
                    order_args,
                    options=options
                )
                result = self.clients.get_polymarket_client().post_order(
                    signed_order, order_type
                )
                last_result = result if isinstance(result, dict) else None

                error_msg = None
                if isinstance(result, dict):
                    if result.get("success") is False:
                        error_msg = str(result.get("message") or result.get("error"))
                    elif result.get("error"):
                        error_msg = str(result.get("error"))

                if not error_msg:
                    return True, result

                logger.error(f"⚠️ {prefix}Polymarket 下单失败 (尝试 {attempt}/{self.config.order_max_retries}): {error_msg}")

                # 检查余额不足错误 - 支持多种错误格式
                error_msg_lower = error_msg.lower()
                if ("not enough balance" in error_msg_lower or
                    "insufficient balance" in error_msg_lower or
                    "balance / allowance" in error_msg_lower):
                    logger.error(f"\n❌ 检测到 Polymarket 余额不足，立即退出程序")
                    logger.error(f"错误详情: {error_msg}")
                    self._insufficient_balance_flag.set()
                    os._exit(1)  # 强制退出整个进程

            except Exception as exc:
                exc_msg = str(exc)
                logger.error(f"⚠️ {prefix}Polymarket 下单异常 (尝试 {attempt}/{self.config.order_max_retries}): {exc_msg}")

                # 检查余额不足错误
                exc_msg_lower = exc_msg.lower()
                if ("not enough balance" in exc_msg_lower or
                    "insufficient balance" in exc_msg_lower or
                    "balance / allowance" in exc_msg_lower or
                    "balance" in exc_msg_lower):
                    logger.error(f"\n❌ 检测到 Polymarket 余额不足异常，立即退出程序")
                    logger.error(f"异常详情: {exc_msg}")
                    self._insufficient_balance_flag.set()
                    os._exit(1)  # 强制退出整个进程

            if attempt < self.config.order_max_retries:
                time.sleep(self.config.order_retry_delay)

        return False, last_result

    # ==================== 辅助方法 ====================

    def _round_price(self, value: Optional[float]) -> Optional[float]:
        """四舍五入价格到配置的小数位数"""
        if value is None:
            return None
        try:
            return round(float(value), self.config.price_decimals)
        except (TypeError, ValueError):
            return None

    def calculate_opinion_fee_rate(self, price: float) -> float:
        """
        计算 Opinion 平台的手续费率

        根据推导公式: fee_rate = 0.06 * price * (1 - price) + 0.0025

        Args:
            price: 订单价格

        Returns:
            手续费率 (小数形式)
        """
        return 0.06 * price * (1 - price) + 0.0025

    def calculate_opinion_adjusted_amount(self, price: float, target_amount: float) -> float:
        """
        计算 Opinion 平台考虑手续费后应下单的数量

        目标: 使得扣除手续费后,实际得到的数量等于 target_amount

        逻辑流程:
        1. 计算 fee_rate = 0.06 * price * (1 - price) + 0.0025
        2. 预计算: A_provisional = target_amount / (1 - fee_rate)
        3. 计算预估手续费: Fee_provisional = price * A_provisional * fee_rate
        4. 判断适用场景:
           - 如果 Fee_provisional > 0.5: 适用百分比手续费
             A_order = target_amount / (1 - fee_rate)
           - 如果 Fee_provisional <= 0.5: 适用最低手续费 $0.5
             A_order = target_amount + 0.5 / price

        Args:
            price: 订单价格
            target_amount: 期望最终得到的数量

        Returns:
            应下单的数量 (考虑手续费后)
        """
        # 步骤1: 计算手续费率
        fee_rate = self.calculate_opinion_fee_rate(price)

        # 步骤2: 预计算 (假设适用百分比手续费)
        A_provisional = target_amount / (1 - fee_rate)

        # 步骤3: 计算预估手续费
        Fee_provisional = price * A_provisional * fee_rate

        # 步骤4: 判断适用场景并返回最终数量
        if Fee_provisional > 0.5:
            # 适用百分比手续费
            A_order = target_amount / (1 - fee_rate)
        else:
            # 适用最低手续费 $0.5
            A_order = target_amount + 0.5 / price

        return A_order

    def get_order_size_for_platform(
        self,
        platform: str,
        price: float,
        target_amount: float,
        is_hedge: bool = False
    ) -> Tuple[float, float]:
        """
        获取指定平台的下单数量

        对于 Opinion 平台,需要考虑手续费进行修正
        对于 Polymarket 平台,直接使用目标数量

        Args:
            platform: 平台名称 ('opinion' 或 'polymarket')
            price: 订单价格
            target_amount: 目标数量（希望实际得到的数量）
            is_hedge: 是否是对冲单（对冲单需要精确匹配首单的实际数量）

        Returns:
            (order_size, effective_size): 下单数量和实际得到的数量
        """
        if platform == 'opinion':
            # Opinion 需要考虑手续费修正
            order_size = self.calculate_opinion_adjusted_amount(price, target_amount)
            effective_size = target_amount  # 修正后应该能得到目标数量
            return order_size, effective_size
        else:
            # Polymarket 直接使用目标数量
            return target_amount, target_amount

    # ==================== 即时执行方法 ====================

    def _maybe_auto_execute(self, opportunity: Dict[str, Any]) -> None:
        """在满足配置阈值时尝试自动执行即时套利（基于年化收益率）"""
        if not self.config.immediate_exec_enabled:
            return

        # 检查单笔收益率，如果大于3%则跳过
        profit_rate = opportunity.get('profit_rate', 0)
        if profit_rate > 3.0:
            print(f"  ⏭️  单笔收益率 {profit_rate:.2f}% > 3%，跳过该套利机会")
            return

        # 使用年化收益率作为判断标准
        annualized_rate = opportunity.get('annualized_rate')
        if annualized_rate is None:
            # 如果没有年化收益率，跳过自动执行
            logger.warning("⚠️ 无法进行自动执行: 缺少年化收益率数据")
            return

        lower = self.config.immediate_min_percent
        upper = self.config.immediate_max_percent

        if lower <= annualized_rate <= upper:
            profit_rate = opportunity.get('profit_rate', 0)

            # t0: 发现套利机会（启动时间测量会话）
            session_id = self._timing_tracker.start_session(
                profit_rate=profit_rate,
                annualized_rate=annualized_rate,
                opportunity_type=opportunity.get('type'),
                order_size=float(os.getenv("IMMEDIATE_ORDER_SIZE", "200"))
            )
            opportunity['_timing_session_id'] = session_id  # 保存session_id

            print(f"  ⚡ 年化收益率 {annualized_rate:.2f}% 在阈值 [{lower:.2f}%,{upper:.2f}%]，启动即时执行线程 (利润率={profit_rate:.2f}%)")

            # t1: 进入自动执行判断
            self._timing_tracker.mark("t1_auto_execute_check", session_id)

            try:
                self._spawn_execute_thread(opportunity)
            except Exception as exc:
                print(f"⚠️ 无法启动即时执行线程: {exc}")
                self._timing_tracker.end_session(session_id, success=False)
        else:
            print(f"  🔶 年化收益率 {annualized_rate:.2f}% 不在阈值范围 [{lower:.2f}%,{upper:.2f}%]，跳过自动执行")

    def _spawn_execute_thread(self, opportunity: Dict[str, Any]) -> None:
        """启动一个后台线程来执行给定的套利机会（非交互）"""
        session_id = opportunity.get('_timing_session_id')

        # 检查距离上次执行是否超过 1 秒
        with self._immediate_exec_lock:
            now = time.time()
            elapsed = now - self._last_immediate_exec_time
            if elapsed < 2.0:
                print(f"  ⏳ 距离上次立即套利下单仅 {elapsed:.2f}s，跳过本次执行 (需间隔 >= 2s)")
                if session_id:
                    self._timing_tracker.end_session(session_id, success=False)
                return
            # 更新上次执行时间
            self._last_immediate_exec_time = now

        # t2: 线程启动完成
        if session_id:
            self._timing_tracker.mark("t2_thread_spawn", session_id)

        thread_name = f"instant-exec-{len(self._active_exec_threads)+1}"
        t = threading.Thread(
            target=self._execute_opportunity,
            args=(opportunity,),
            daemon=False,
            name=thread_name
        )
        t.start()
        self._active_exec_threads.append(t)
        print(f"🧵 已启动即时执行线程 (线程数={len(self._active_exec_threads)})")

    def wait_for_active_exec_threads(self) -> None:
        """等待所有即时执行线程完成，防止主程序提前退出"""
        # 移除已经结束的线程，仅保留仍然活跃的
        self._active_exec_threads = [t for t in self._active_exec_threads if t.is_alive()]

        if not self._active_exec_threads:
            return

        print(f"\n⏳ 等待 {len(self._active_exec_threads)} 个即时执行线程完成...")
        try:
            for t in list(self._active_exec_threads):
                t.join()
        except KeyboardInterrupt:
            print("\n⚠️ 手动中断即时执行线程的等待，线程仍在后台运行")
            # 保留仍然活跃的线程引用，方便后续再次等待
            self._active_exec_threads = [t for t in self._active_exec_threads if t.is_alive()]
            raise

        self._active_exec_threads.clear()
        print("✅ 所有即时执行线程已完成")

    def _execute_opportunity(self, opp: Dict[str, Any]) -> None:
        """在后台执行一个套利机会

        注意: 此函数尽量复用已有下单逻辑，但为避免复杂交互，采取保守策略：
        - immediate: 在两个平台分别下限价买单
        """
        session_id = opp.get('_timing_session_id')

        try:
            # 读取最小下单量配置
            try:
                default_size = float(os.getenv("IMMEDIATE_ORDER_SIZE", "200"))
            except Exception:
                default_size = 200.0

            order_size = min(max(float(default_size), 0.9 * float(opp.get('min_size', 0.0))), 1000.0)
            # 保证不为零
            if not order_size or order_size <= 0:
                order_size = default_size

            print(f"🟢 即时执行机会: {opp.get('name')} | 利润率={opp.get('profit_rate'):.2f}% | 数量={order_size:.2f}")

            # t3: 订单准备完成（在线程内部）
            if session_id:
                self._timing_tracker.mark("t3_order_prepare_start", session_id)

            # Immediate execution: place both orders
            if opp.get('type') == 'immediate':
                # 提高限价单价格0.02以降低单边库存风险
                first_price = self._round_price(opp.get('first_price'))
                second_price = self._round_price(opp.get('second_price'))

                # 计算第一个平台的下单数量(考虑手续费)
                first_order_size, first_effective_size = self.get_order_size_for_platform(
                    opp['first_platform'],
                    first_price if first_price is not None else opp.get('first_price', 0.0),
                    order_size
                )

                # 计算第二个平台的下单数量(需要匹配第一个平台的实际数量)
                second_order_size, second_effective_size = self.get_order_size_for_platform(
                    opp['second_platform'],
                    second_price if second_price is not None else opp.get('second_price', 0.0),
                    first_effective_size,
                    is_hedge=True
                )

                print(f"  第一平台下单: {first_order_size:.2f} -> 预期实际: {first_effective_size:.2f}")
                print(f"  第二平台下单: {second_order_size:.2f} -> 预期实际: {second_effective_size:.2f}")

                # 检查平台下单金额是否满足最小限制 (1.3 USDT)
                MIN_ORDER_AMOUNT = 1.2

                # 检查第一个平台
                first_order_amount = first_order_size * (first_price if first_price is not None else opp['first_price'])
                if first_order_amount < MIN_ORDER_AMOUNT:
                    platform_name = opp.get('first_platform', 'Unknown').capitalize()
                    print(f"⚠️ 跳过套利: {platform_name} 首单金额 ${first_order_amount:.2f} 小于最小限制 ${MIN_ORDER_AMOUNT}")
                    if session_id:
                        self._timing_tracker.end_session(session_id, success=False)
                    return

                # 检查第二个平台
                second_order_amount = second_order_size * (second_price if second_price is not None else opp['second_price'])
                if second_order_amount < MIN_ORDER_AMOUNT:
                    platform_name = opp.get('second_platform', 'Unknown').capitalize()
                    print(f"⚠️ 跳过套利: {platform_name} 对冲单金额 ${second_order_amount:.2f} 小于最小限制 ${MIN_ORDER_AMOUNT}")
                    if session_id:
                        self._timing_tracker.end_session(session_id, success=False)
                    return

                # Place first order
                first_order_success = False
                if opp.get('first_platform') == 'opinion':
                    try:
                        order1 = PlaceOrderDataInput(
                            marketId=opp['match'].opinion_market_id,
                            tokenId=str(opp['first_token']),
                            side=opp['first_side'],
                            orderType=LIMIT_ORDER,
                            price=str(first_price if first_price is not None else opp['first_price']),
                            makerAmountInBaseToken=str(first_order_size)
                        )
                        success, res1 = self.place_opinion_order_with_retries(
                            order1,
                            context="即时执行首单",
                            session_id=session_id
                        )
                        if success and res1:
                            print("✅ Opinion 订单提交成功 (即时执行)")
                            first_order_success = True
                            # 结束时间测量会话
                            if session_id:
                                self._timing_tracker.end_session(session_id, success=True)
                        else:
                            print(f"❌ Opinion 下单失败，跳过对冲单")
                            if session_id:
                                self._timing_tracker.end_session(session_id, success=False)
                            return  # Opinion 首单失败，直接返回，不执行对冲单
                    except Exception as e:
                        print(f"❌ Opinion 下单异常: {e}，跳过对冲单")
                        if session_id:
                            self._timing_tracker.end_session(session_id, success=False)
                        return  # Opinion 首单异常，直接返回，不执行对冲单
                else:
                    try:
                        # 创建 Polymarket 订单参数
                        price_to_use = first_price if first_price is not None else opp['first_price']
                        order1 = OrderArgs(
                            token_id=opp['first_token'],
                            price=price_to_use,
                            size=first_order_size,
                            side=opp['first_side'],
                            fee_rate_bps=0  # Polymarket fee rate 统一为 0
                        )
                        # 创建选项以避免额外的网络请求
                        options1 = PartialCreateOrderOptions(
                            tick_size=infer_tick_size_from_price(price_to_use),
                            neg_risk=opp['match'].polymarket_neg_risk
                        )
                        success, res1 = self.place_polymarket_order_with_retries(
                            order1,
                            OrderType.GTC,
                            context="即时执行首单",
                            options=options1
                        )
                        if success:
                            print(f"✅ Polymarket 订单提交成功 (即时执行): {res1}")
                            first_order_success = True
                        else:
                            print(f"❌ Polymarket 下单失败（已尝试 {self.config.order_max_retries} 次），跳过对冲单")
                            return  # Polymarket 首单失败，直接返回，不执行对冲单
                    except Exception as e:
                        print(f"❌ Polymarket 下单异常: {e}，跳过对冲单")
                        return  # Polymarket 首单异常，直接返回，不执行对冲单

                # Place second order (only if first order succeeded)
                if opp.get('second_platform') == 'opinion':
                    try:
                        order2 = PlaceOrderDataInput(
                            marketId=opp['match'].opinion_market_id,
                            tokenId=str(opp['second_token']),
                            side=opp['second_side'],
                            orderType=LIMIT_ORDER,
                            price=str(second_price if second_price is not None else opp['second_price']),
                            makerAmountInBaseToken=str(second_order_size)
                        )
                        success, res2 = self.place_opinion_order_with_retries(
                            order2,
                            context="即时执行对冲"
                        )
                        if success and res2:
                            print("✅ Opinion 对冲订单提交成功 (即时执行)")
                        else:
                            print(f"❌ Opinion 对冲下单失败（已尝试 {self.config.order_max_retries} 次）")
                    except Exception as e:
                        print(f"❌ Opinion 对冲下单异常: {e}")
                else:
                    try:
                        # 创建 Polymarket 对冲订单参数
                        price_to_use2 = second_price if second_price is not None else opp['second_price']
                        order2 = OrderArgs(
                            token_id=opp['second_token'],
                            price=price_to_use2,
                            size=second_order_size,
                            side=opp['second_side'],
                            fee_rate_bps=0  # Polymarket fee rate 统一为 0
                        )
                        # 创建选项以避免额外的网络请求
                        options2 = PartialCreateOrderOptions(
                            tick_size=infer_tick_size_from_price(price_to_use2),
                            neg_risk=opp['match'].polymarket_neg_risk
                        )
                        success, res2 = self.place_polymarket_order_with_retries(
                            order2,
                            OrderType.GTC,
                            context="即时执行对冲",
                            options=options2
                        )
                        if success:
                            print(f"✅ Polymarket 对冲订单提交成功 (即时执行): {res2}")
                        else:
                            print(f"❌ Polymarket 对冲下单失败（已尝试 {self.config.order_max_retries} 次）")
                    except Exception as e:
                        print(f"❌ Polymarket 对冲下单异常: {e}")

                print("🟢 即时套利执行线程完成 (immediate)")
                return

        except Exception as e:
            print(f"❌ 即时执行线程异常: {e}")
            traceback.print_exc()

    # ==================== 套利执行 ====================

    def execute_arbitrage_pro(self):
        """专业套利执行模式"""
        if not self.market_matches:
            logger.error("❌ 没有可用的市场匹配")
            return

        if not self.ensure_websocket_ready():
            logger.error("❌ WebSocket 未就绪，无法执行扫描")
            return

        self._wait_for_websocket_warmup(timeout_seconds=20.0, min_poly_books=1, min_opinion_books=0)

        threshold_price = self.config.threshold_price
        threshold_size = self.config.threshold_size

        logger.info(f"\n{'='*100}")

        start_time = time.time()
        total_matches = len(self.market_matches)
        completed_count = 0
        batch_size = self.config.orderbook_batch_size

        for batch_start in range(0, total_matches, batch_size):
            batch_matches = self.market_matches[batch_start : batch_start + batch_size]

            # 扫描每个市场
            for match in batch_matches:
                opinion_yes_book = self.get_opinion_orderbook(match.opinion_yes_token)
                poly_yes_book = self.get_polymarket_orderbook(match.polymarket_yes_token)

                completed_count += 1
                logger.debug(f"[{completed_count}/{total_matches}] 扫描: {match.question[:70]}...")

                if not opinion_yes_book or not poly_yes_book:
                    continue

                # 推导 NO 订单簿
                opinion_no_book = self.derive_no_orderbook(
                    opinion_yes_book, match.opinion_no_token
                )
                poly_no_book = self.derive_no_orderbook(
                    poly_yes_book, match.polymarket_no_token
                )

                # 检测套利机会
                opportunities = self._scan_market_opportunities(
                    match,
                    opinion_yes_book,
                    opinion_no_book,
                    poly_yes_book,
                    poly_no_book,
                    threshold_price,
                    threshold_size,
                )

                if opportunities:
                    logger.info(f"🔍 在市场 '{match.question[:50]}...' 中发现 {len(opportunities)} 个套利机会")
                # 尝试自动执行发现的机会
                for opp in opportunities:
                    self._maybe_auto_execute(opp)

        elapsed = time.time() - start_time
        logger.info(f"\n✅ 扫描完成，耗时 {elapsed:.2f}s\n")

    def _scan_market_opportunities(
        self,
        match: MarketMatch,
        opinion_yes_book: OrderBookSnapshot,
        opinion_no_book: Optional[OrderBookSnapshot],
        poly_yes_book: OrderBookSnapshot,
        poly_no_book: Optional[OrderBookSnapshot],
        threshold_price: float,
        threshold_size: float,
    ) -> List[Dict[str, Any]]:
        """扫描单个市场的套利机会，返回机会列表"""
        opportunities = []

        # 策略1: Opinion YES ask + Polymarket NO ask
        if (
            opinion_yes_book
            and opinion_yes_book.asks
            and poly_no_book
            and poly_no_book.asks
        ):
            op_yes_ask = opinion_yes_book.asks[0]
            pm_no_ask = poly_no_book.asks[0]

            if op_yes_ask and pm_no_ask and op_yes_ask.price is not None and pm_no_ask.price is not None:
                min_size = min(op_yes_ask.size or 0, pm_no_ask.size or 0)
                metrics = self.compute_profitability_metrics(
                    match,
                    "opinion",
                    op_yes_ask.price,
                    "polymarket",
                    pm_no_ask.price,
                    min_size,
                )

                if metrics and metrics["cost"] < threshold_price and min_size > threshold_size:
                    # 创建机会对象
                    first_price = self._round_price(op_yes_ask.price)
                    second_price = self._round_price(pm_no_ask.price)

                    opportunity = {
                        'match': match,
                        'type': 'immediate',
                        'strategy': 'opinion_yes_ask_poly_no_ask',
                        'name': '立即套利: Opinion YES ask + Polymarket NO ask',
                        'cost': metrics['cost'],
                        'profit_rate': metrics['profit_rate'],
                        'annualized_rate': metrics['annualized_rate'],
                        'min_size': min_size,
                        'first_platform': 'opinion',
                        'first_token': match.opinion_yes_token,
                        'first_price': first_price,
                        'first_side': OrderSide.BUY,
                        'second_platform': 'polymarket',
                        'second_token': match.polymarket_no_token,
                        'second_price': second_price,
                        'second_side': BUY,
                        'opinion_yes_book': opinion_yes_book,
                        'opinion_no_book': opinion_no_book,
                        'poly_yes_book': poly_yes_book,
                        'poly_no_book': poly_no_book,
                    }
                    opportunities.append(opportunity)

                    self._report_opportunity(
                        "Opinion YES ask + Poly NO ask",
                        metrics,
                        min_size,
                    )

        # 策略2: Opinion NO ask + Polymarket YES ask
        if (
            opinion_no_book
            and opinion_no_book.asks
            and poly_yes_book
            and poly_yes_book.asks
        ):
            op_no_ask = opinion_no_book.asks[0]
            pm_yes_ask = poly_yes_book.asks[0]

            if op_no_ask and pm_yes_ask and op_no_ask.price is not None and pm_yes_ask.price is not None:
                min_size = min(op_no_ask.size or 0, pm_yes_ask.size or 0)
                metrics = self.compute_profitability_metrics(
                    match,
                    "opinion",
                    op_no_ask.price,
                    "polymarket",
                    pm_yes_ask.price,
                    min_size,
                )

                if metrics and metrics["cost"] < threshold_price and min_size > threshold_size:
                    # 创建机会对象
                    first_price = self._round_price(op_no_ask.price)
                    second_price = self._round_price(pm_yes_ask.price)

                    opportunity = {
                        'match': match,
                        'type': 'immediate',
                        'strategy': 'opinion_no_ask_poly_yes_ask',
                        'name': '立即套利: Opinion NO ask + Polymarket YES ask',
                        'cost': metrics['cost'],
                        'profit_rate': metrics['profit_rate'],
                        'annualized_rate': metrics['annualized_rate'],
                        'min_size': min_size,
                        'first_platform': 'opinion',
                        'first_token': match.opinion_no_token,
                        'first_price': first_price,
                        'first_side': OrderSide.BUY,
                        'second_platform': 'polymarket',
                        'second_token': match.polymarket_yes_token,
                        'second_price': second_price,
                        'second_side': BUY,
                        'opinion_yes_book': opinion_yes_book,
                        'opinion_no_book': opinion_no_book,
                        'poly_yes_book': poly_yes_book,
                        'poly_no_book': poly_no_book,
                    }
                    opportunities.append(opportunity)

                    self._report_opportunity(
                        "Opinion NO ask + Poly YES ask",
                        metrics,
                        min_size,
                    )

        return opportunities

    def _report_opportunity(
        self, strategy: str, metrics: Dict[str, float], min_size: float
    ):
        """报告套利机会"""
        ann_text = (
            f", 年化={metrics['annualized_rate']:.2f}%"
            if metrics["annualized_rate"]
            else ""
        )
        print(
            f"  ✓ 发现套利: {strategy}, "
            f"成本=${metrics['cost']:.3f}, "
            f"收益率={metrics['profit_rate']:.2f}%{ann_text}, "
            f"数量={min_size:.2f}"
        )

    def run_pro_loop(self, interval_seconds: float):
        """持续运行专业模式"""
        min_interval = max(0.5, interval_seconds)
        print(f"♻️ 启动专业套利循环，间隔 {min_interval:.1f}s")

        try:
            while not self._monitor_stop_event.is_set():
                # 检查余额不足标志
                if self._insufficient_balance_flag.is_set():
                    logger.error("❌ 检测到余额不足标志，立即退出主循环")
                    os._exit(1)

                cycle_start = time.time()

                try:
                    self.execute_arbitrage_pro()
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    print(f"❌ 扫描异常: {exc}")
                    traceback.print_exc()

                # 等待所有即时执行线程完成
                try:
                    self.wait_for_active_exec_threads()
                except KeyboardInterrupt:
                    raise

                # 再次检查余额不足标志
                if self._insufficient_balance_flag.is_set():
                    logger.error("❌ 检测到余额不足标志，立即退出主循环")
                    os._exit(1)

                elapsed = time.time() - cycle_start
                sleep_time = max(0.0, min_interval - elapsed)

                if sleep_time > 0:
                    logger.debug(f"🕒 {sleep_time:.1f}s 后进行下一轮扫描")
                    self._monitor_stop_event.wait(timeout=sleep_time)
        finally:
            self._monitor_stop_event.set()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="模块化跨平台套利检测器 - Opinion vs Polymarket"
    )

    parser.add_argument(
        "--matches-file",
        type=str,
        default="market_matches.json",
        help="市场匹配结果文件路径",
    )

    parser.add_argument("--pro", action="store_true", help="运行专业套利执行模式")

    parser.add_argument(
        "--pro-once", action="store_true", help="仅运行一次扫描，不进入循环"
    )

    parser.add_argument(
        "--loop-interval", type=float, default=None, help="循环间隔时间（秒）"
    )

    parser.add_argument(
        "--opinion-bootstrap-rest",
        action="store_true",
        help="启用 Opinion REST bootstrap 补偿（默认纯WebSocket）",
    )

    parser.add_argument(
        "--ws-status-interval",
        type=float,
        default=10.0,
        help="WebSocket 运行状态日志输出间隔（秒）",
    )

    args = parser.parse_args()

    arbitrage: Optional[ModularArbitrage] = None
    try:
        # 初始化日志
        config = ArbitrageConfig()
        setup_logger(config.log_dir, config.arbitrage_log_pointer)

        # 显示配置摘要
        config.display_summary()

        # 创建套利检测器
        arbitrage = ModularArbitrage(
            config,
            opinion_bootstrap_rest=args.opinion_bootstrap_rest,
            ws_status_interval=args.ws_status_interval,
        )

        # 加载市场匹配
        if not arbitrage.load_market_matches(args.matches_file):
            print("⚠️ 无法加载市场匹配")
            return

        # 运行套利扫描
        if args.pro:
            loop_interval = args.loop_interval or config.pro_loop_interval

            if args.pro_once or loop_interval <= 0:
                arbitrage.execute_arbitrage_pro()
            else:
                arbitrage.run_pro_loop(loop_interval)

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        traceback.print_exc()
    finally:
        if arbitrage is not None:
            arbitrage.close_websockets()

        # 打印时间测量统计
        print("\n" + "="*80)
        print("📊 性能统计报告")
        print("="*80 + "\n")

        timing_tracker = get_timing_tracker()
        tb_monitor = get_token_bucket_monitor()

        # 打印时间统计
        timing_tracker.log_statistics()

        # 打印Token Bucket统计
        tb_monitor.log_statistics()

        print("💡 提示: 使用 'python tools/timing_analyzer.py' 查看详细分析\n")


if __name__ == "__main__":
    main()
