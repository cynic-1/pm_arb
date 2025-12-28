"""
实时WebSocket套利检测器 - Opinion vs Polymarket
使用WebSocket实时监控订单簿变化并检测套利机会

与原modular_arbitrage_websocket.py的主要区别:
1. 使用WebSocket替代REST API轮询获取订单簿
2. 事件驱动: 每次订单簿更新时立即检查套利机会
3. 完全并行: 不同市场的套利检测可并发执行
4. 更低延迟: 无需等待轮询周期
"""

import os
import argparse
import time
import threading
import traceback
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 导入核心模块
from arbitrage_core import (
    ArbitrageConfig,
    PlatformClients,
    FeeCalculator,
    MarketMatch,
    OrderBookSnapshot,
    WebSocketManager,
    OrderBookUpdate,
)
from arbitrage_core.utils import setup_logger
from arbitrage_core.utils.helpers import to_int

# Opinion SDK
from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER

# Polymarket SDK
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

import logging
import json

logger = logging.getLogger(__name__)


class RealtimeArbitrage:
    """实时WebSocket套利检测器"""

    def __init__(self, config: Optional[ArbitrageConfig] = None):
        """
        初始化实时套利检测器

        Args:
            config: 配置对象，如果为 None 则创建默认配置
        """
        # 使用配置对象
        self.config = config or ArbitrageConfig()

        # 初始化核心组件
        print("🔧 初始化核心组件...")
        self.clients = PlatformClients(self.config)
        self.fee_calculator = FeeCalculator(self.config)
        self.ws_manager = WebSocketManager(self.config)

        # 市场匹配
        self.market_matches: List[MarketMatch] = []
        self.token_to_match: Dict[str, MarketMatch] = {}  # token_id -> MarketMatch

        # 订单簿缓存 (token_id -> OrderBookSnapshot)
        self.orderbook_cache: Dict[str, OrderBookSnapshot] = {}
        self.cache_lock = threading.Lock()

        # 套利执行线程
        self._active_exec_threads: List[threading.Thread] = []
        self._exec_lock = threading.Lock()
        self._insufficient_balance_flag = threading.Event()  # 余额不足标志

        # 去重机制：记录最近执行的套利机会
        self._recent_executions: Dict[str, float] = {}  # market_id_strategy -> timestamp
        self._execution_cooldown = 5.0  # 秒，同一个套利机会的冷却时间

        # 统计信息
        self.stats = {
            "orderbook_updates": 0,
            "opportunities_found": 0,
            "opportunities_executed": 0,
            "opportunities_deduplicated": 0,  # 去重的机会数
        }
        self.stats_lock = threading.Lock()

        print("✅ 实时套利检测器初始化完成!\n")

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

            # Build token -> match mapping
            for match in self.market_matches:
                self.token_to_match[match.opinion_yes_token] = match
                self.token_to_match[match.opinion_no_token] = match
                self.token_to_match[match.polymarket_yes_token] = match
                self.token_to_match[match.polymarket_no_token] = match

            print(f"✅ 共加载 {len(self.market_matches)} 个市场匹配\n")
            return True

        return False

    # ==================== WebSocket回调 ====================

    def on_orderbook_update(self, update: OrderBookUpdate):
        """
        订单簿更新回调 - 每次WebSocket收到订单簿更新时调用

        Args:
            update: 订单簿更新事件
        """
        # Update statistics
        with self.stats_lock:
            self.stats["orderbook_updates"] += 1

        # Update cache
        with self.cache_lock:
            self.orderbook_cache[update.token_id] = update.snapshot

        # Find which market this token belongs to
        match = self.token_to_match.get(update.token_id)
        if not match:
            return

        # 如果这是Polymarket YES token更新，自动推导NO token
        if update.source == "polymarket" and update.token_id == match.polymarket_yes_token:
            no_book = self.derive_no_orderbook(update.snapshot, match.polymarket_no_token)
            if no_book:
                with self.cache_lock:
                    self.orderbook_cache[match.polymarket_no_token] = no_book
                logger.debug(f"📊 自动推导Polymarket NO token订单簿: {match.polymarket_no_token[:20]}...")

        # 如果这是Opinion YES token更新，自动推导NO token
        if update.source == "opinion" and update.token_id == match.opinion_yes_token:
            no_book = self.derive_no_orderbook(update.snapshot, match.opinion_no_token)
            if no_book:
                with self.cache_lock:
                    self.orderbook_cache[match.opinion_no_token] = no_book
                logger.debug(f"📊 自动推导Opinion NO token订单簿: {match.opinion_no_token[:20]}...")

        # Check for arbitrage opportunities
        # 在后台线程中执行以避免阻塞WebSocket
        threading.Thread(
            target=self._check_arbitrage_for_market,
            args=(match,),
            daemon=True
        ).start()

    def _check_arbitrage_for_market(self, match: MarketMatch):
        """
        检查单个市场的套利机会

        Args:
            match: 市场匹配对象
        """
        try:
            # Get all 4 orderbooks for this market
            # NO books已经在on_orderbook_update中自动推导了
            with self.cache_lock:
                opinion_yes_book = self.orderbook_cache.get(match.opinion_yes_token)
                opinion_no_book = self.orderbook_cache.get(match.opinion_no_token)
                poly_yes_book = self.orderbook_cache.get(match.polymarket_yes_token)
                poly_no_book = self.orderbook_cache.get(match.polymarket_no_token)

            # Need at least the YES books to proceed
            # (NO books会在有YES books时自动推导)
            if not (opinion_yes_book and poly_yes_book):
                return

            # Scan for opportunities
            opportunities = self._scan_market_opportunities(
                match,
                opinion_yes_book,
                opinion_no_book,
                poly_yes_book,
                poly_no_book,
                threshold_price=0.99,
                threshold_size=200,
            )

            if opportunities:
                with self.stats_lock:
                    self.stats["opportunities_found"] += len(opportunities)

                logger.info(
                    f"🔍 发现 {len(opportunities)} 个套利机会: {match.question[:50]}..."
                )

                # Try to auto-execute
                for opp in opportunities:
                    self._maybe_auto_execute(opp)

        except Exception as e:
            logger.error(f"❌ 检查套利机会时出错: {e}")
            traceback.print_exc()

    # ==================== 订单执行辅助方法 ====================

    def _get_order_size_for_platform(
        self,
        platform: str,
        price: float,
        target_amount: float,
        is_hedge: bool = False
    ) -> Tuple[float, float]:
        """
        获取指定平台的下单数量 (从 modular_arbitrage.py 复制)

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
            order_size = self._calculate_opinion_adjusted_amount(price, target_amount)
            effective_size = target_amount  # 修正后应该能得到目标数量
            return order_size, effective_size
        else:
            # Polymarket 直接使用目标数量
            return target_amount, target_amount

    def _calculate_opinion_adjusted_amount(self, price: float, target_amount: float) -> float:
        """
        计算 Opinion 平台考虑手续费后应下单的数量 (从 modular_arbitrage.py 复制)

        目标: 使得扣除手续费后,实际得到的数量等于 target_amount
        """
        # 步骤1: 计算手续费率
        fee_rate = self._calculate_opinion_fee_rate(price)

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

    def _calculate_opinion_fee_rate(self, price: float) -> float:
        """
        计算 Opinion 平台的手续费率 (从 modular_arbitrage.py 复制)

        根据推导公式: fee_rate = 0.06 * price * (1 - price) + 0.0025
        """
        return 0.06 * price * (1 - price) + 0.0025

    def _place_opinion_order_with_retries(
        self, order: Any, context: str = ""
    ) -> Tuple[bool, Optional[Any]]:
        """Opinion 下单带重试 (从 modular_arbitrage.py 复制)"""
        prefix = f"[{context}] " if context else ""
        last_result = None

        for attempt in range(1, self.config.order_max_retries + 1):
            try:
                result = self.clients.opinion_client.place_order(order)
                last_result = result

                if getattr(result, "errno", 0) == 0:
                    return True, result

                err_msg = str(getattr(result, "errmsg", "unknown error"))
                logger.error(
                    f"⚠️ {prefix}Opinion 下单失败 (尝试 {attempt}/{self.config.order_max_retries}): {err_msg}"
                )

                # 检查余额不足错误
                if "insufficient balance" in err_msg.lower() or "balance" in err_msg.lower():
                    logger.error(f"\n❌ 检测到 Opinion 余额不足，立即退出程序")
                    logger.error(f"错误详情: {err_msg}")
                    self._insufficient_balance_flag.set()
                    os._exit(1)  # 强制退出整个进程

            except Exception as exc:
                exc_msg = str(exc)
                logger.error(f"⚠️ {prefix}Opinion 下单异常 (尝试 {attempt}/{self.config.order_max_retries}): {exc_msg}")

                # 检查余额不足错误
                if "insufficient balance" in exc_msg.lower() or "balance" in exc_msg.lower():
                    logger.error(f"\n❌ 检测到 Opinion 余额不足异常，立即退出程序")
                    logger.error(f"异常详情: {exc_msg}")
                    self._insufficient_balance_flag.set()
                    os._exit(1)  # 强制退出整个进程

            if attempt < self.config.order_max_retries:
                time.sleep(self.config.order_retry_delay)

        return False, last_result

    def _place_polymarket_order_with_retries(
        self, order_args: Any, order_type: Any, context: str = ""
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Polymarket 下单带重试 (从 modular_arbitrage.py 复制)"""
        prefix = f"[{context}] " if context else ""
        last_result = None

        for attempt in range(1, self.config.order_max_retries + 1):
            try:
                signed_order = self.clients.polymarket_client.create_order(order_args)
                result = self.clients.polymarket_client.post_order(signed_order, order_type)
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

    # ==================== 订单簿推导 ====================

    def derive_no_orderbook(
        self, yes_book: OrderBookSnapshot, no_token_id: str
    ) -> Optional[OrderBookSnapshot]:
        """从 YES token 订单簿推导 NO token 订单簿"""
        if not yes_book:
            return None

        from arbitrage_core.models import OrderBookLevel

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

    # ==================== 盈利性分析 ====================

    def compute_profitability_metrics(
        self,
        match: MarketMatch,
        first_platform: str,
        first_price: Optional[float],
        second_platform: str,
        second_price: Optional[float],
        min_size: Optional[float],
    ) -> Optional[Dict[str, float]]:
        """计算盈利性指标"""
        assumed_size = max(self.config.roi_reference_size, min_size or 0.0)

        # 计算有效价格（含手续费）
        eff_first = (
            self.fee_calculator.calculate_opinion_cost_per_token(
                first_price, assumed_size
            )
            if first_platform == "opinion"
            else self.fee_calculator.round_price(first_price)
        )

        eff_second = (
            self.fee_calculator.calculate_opinion_cost_per_token(
                second_price, assumed_size
            )
            if second_platform == "opinion"
            else self.fee_calculator.round_price(second_price)
        )

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

    # ==================== 套利机会扫描 ====================

    def _scan_market_opportunities(
        self,
        match: MarketMatch,
        opinion_yes_book: OrderBookSnapshot,
        opinion_no_book: Optional[OrderBookSnapshot],
        poly_yes_book: OrderBookSnapshot,
        poly_no_book: Optional[OrderBookSnapshot],
        threshold_price: float,
        threshold_size: float,
    ) -> List[Dict]:
        """扫描单个市场的套利机会"""
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

            if (
                op_yes_ask
                and pm_no_ask
                and op_yes_ask.price is not None
                and pm_no_ask.price is not None
            ):
                min_size = min(op_yes_ask.size or 0, pm_no_ask.size or 0)
                metrics = self.compute_profitability_metrics(
                    match,
                    "opinion",
                    op_yes_ask.price,
                    "polymarket",
                    pm_no_ask.price,
                    min_size,
                )

                if (
                    metrics
                    and metrics["cost"] < threshold_price
                    and min_size > threshold_size
                ):
                    opportunity = {
                        "match": match,
                        "type": "immediate",
                        "strategy": "opinion_yes_ask_poly_no_ask",
                        "name": "立即套利: Opinion YES ask + Polymarket NO ask",
                        "cost": metrics["cost"],
                        "profit_rate": metrics["profit_rate"],
                        "annualized_rate": metrics["annualized_rate"],
                        "min_size": min_size,
                        "first_platform": "opinion",
                        "first_token": match.opinion_yes_token,
                        "first_price": op_yes_ask.price,
                        "first_side": OrderSide.BUY,
                        "second_platform": "polymarket",
                        "second_token": match.polymarket_no_token,
                        "second_price": pm_no_ask.price,
                        "second_side": BUY,
                    }
                    opportunities.append(opportunity)

                    self._report_opportunity(
                        "Opinion YES ask + Poly NO ask", metrics, min_size
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

            if (
                op_no_ask
                and pm_yes_ask
                and op_no_ask.price is not None
                and pm_yes_ask.price is not None
            ):
                min_size = min(op_no_ask.size or 0, pm_yes_ask.size or 0)
                metrics = self.compute_profitability_metrics(
                    match,
                    "opinion",
                    op_no_ask.price,
                    "polymarket",
                    pm_yes_ask.price,
                    min_size,
                )

                if (
                    metrics
                    and metrics["cost"] < threshold_price
                    and min_size > threshold_size
                ):
                    opportunity = {
                        "match": match,
                        "type": "immediate",
                        "strategy": "opinion_no_ask_poly_yes_ask",
                        "name": "立即套利: Opinion NO ask + Polymarket YES ask",
                        "cost": metrics["cost"],
                        "profit_rate": metrics["profit_rate"],
                        "annualized_rate": metrics["annualized_rate"],
                        "min_size": min_size,
                        "first_platform": "opinion",
                        "first_token": match.opinion_no_token,
                        "first_price": op_no_ask.price,
                        "first_side": OrderSide.BUY,
                        "second_platform": "polymarket",
                        "second_token": match.polymarket_yes_token,
                        "second_price": pm_yes_ask.price,
                        "second_side": BUY,
                    }
                    opportunities.append(opportunity)

                    self._report_opportunity(
                        "Opinion NO ask + Poly YES ask", metrics, min_size
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
        logger.info(
            f"  ✓ 发现套利: {strategy}, "
            f"成本=${metrics['cost']:.3f}, "
            f"收益率={metrics['profit_rate']:.2f}%{ann_text}, "
            f"数量={min_size:.2f}"
        )

    # ==================== 即时执行 ====================

    def _maybe_auto_execute(self, opportunity: Dict):
        """根据配置自动执行即时套利（带去重）"""
        if not self.config.immediate_exec_enabled:
            return

        annualized_rate = opportunity.get("annualized_rate")
        if annualized_rate is None:
            return

        lower = self.config.immediate_min_percent
        upper = self.config.immediate_max_percent

        if lower <= annualized_rate <= upper:
            # 生成唯一标识：market_id + strategy
            match = opportunity.get("match")
            strategy = opportunity.get("strategy")
            exec_key = f"{match.opinion_market_id}_{strategy}"

            # 检查是否在冷却期内
            current_time = time.time()
            with self._exec_lock:
                last_exec_time = self._recent_executions.get(exec_key, 0)
                if current_time - last_exec_time < self._execution_cooldown:
                    # 在冷却期内，跳过执行
                    logger.debug(
                        f"  ⏭️ 跳过重复执行: {exec_key} (距上次执行 {current_time - last_exec_time:.1f}s)"
                    )
                    with self.stats_lock:
                        self.stats["opportunities_deduplicated"] += 1
                    return

                # 记录执行时间
                self._recent_executions[exec_key] = current_time

            logger.info(
                f"  ⚡ 年化收益率 {annualized_rate:.2f}% 在阈值范围，启动即时执行"
            )

            with self._exec_lock:
                thread = threading.Thread(
                    target=self._execute_opportunity,
                    args=(opportunity,),
                    daemon=False,
                    name=f"exec-{len(self._active_exec_threads) + 1}",
                )
                thread.start()
                self._active_exec_threads.append(thread)

            with self.stats_lock:
                self.stats["opportunities_executed"] += 1

    def _execute_opportunity(self, opp: Dict):
        """在后台执行套利机会 (从 modular_arbitrage.py 复制)"""
        try:
            # 读取最小下单量配置
            order_size = min(
                max(float(self.config.immediate_order_size), 0.9 * float(opp.get("min_size", 0.0))),
                1000.0,
            )

            if not order_size or order_size <= 0:
                order_size = self.config.immediate_order_size

            logger.info(
                f"🟢 即时执行: {opp.get('name')} | 利润率={opp.get('profit_rate'):.2f}% | 数量={order_size:.2f}"
            )

            # Immediate execution: place both orders
            if opp.get("type") == "immediate":
                first_price = self.fee_calculator.round_price(opp.get("first_price"))
                second_price = self.fee_calculator.round_price(opp.get("second_price"))

                # 计算第一个平台的下单数量(考虑手续费)
                first_order_size, first_effective_size = self._get_order_size_for_platform(
                    opp["first_platform"],
                    first_price if first_price is not None else opp.get("first_price", 0.0),
                    order_size
                )

                # 计算第二个平台的下单数量(需要匹配第一个平台的实际数量)
                second_order_size, second_effective_size = self._get_order_size_for_platform(
                    opp["second_platform"],
                    second_price if second_price is not None else opp.get("second_price", 0.0),
                    first_effective_size,
                    is_hedge=True
                )

                logger.info(f"  第一平台下单: {first_order_size:.2f} -> 预期实际: {first_effective_size:.2f}")
                logger.info(f"  第二平台下单: {second_order_size:.2f} -> 预期实际: {second_effective_size:.2f}")

                # Place first order
                if opp.get("first_platform") == "opinion":
                    try:
                        order1 = PlaceOrderDataInput(
                            marketId=opp["match"].opinion_market_id,
                            tokenId=str(opp["first_token"]),
                            side=opp["first_side"],
                            orderType=LIMIT_ORDER,
                            price=str(first_price if first_price is not None else opp["first_price"]),
                            makerAmountInBaseToken=str(first_order_size)
                        )
                        success, res1 = self._place_opinion_order_with_retries(
                            order1,
                            context="即时执行首单"
                        )
                        if success and res1:
                            logger.info("✅ Opinion 订单提交成功 (即时执行)")
                        else:
                            logger.error(f"❌ Opinion 下单失败（已尝试 {self.config.order_max_retries} 次）")
                    except Exception as e:
                        logger.error(f"❌ Opinion 下单异常: {e}")
                        traceback.print_exc()
                else:
                    try:
                        order1 = OrderArgs(
                            token_id=opp["first_token"],
                            price=first_price if first_price is not None else opp["first_price"],
                            size=first_order_size,
                            side=opp["first_side"]
                        )
                        success, res1 = self._place_polymarket_order_with_retries(
                            order1,
                            OrderType.GTC,
                            context="即时执行首单"
                        )
                        if success:
                            logger.info(f"✅ Polymarket 订单提交成功 (即时执行): {res1}")
                        else:
                            logger.error(f"❌ Polymarket 下单失败（已尝试 {self.config.order_max_retries} 次）")
                    except Exception as e:
                        logger.error(f"❌ Polymarket 下单异常: {e}")
                        traceback.print_exc()

                # Place second order
                if opp.get("second_platform") == "opinion":
                    try:
                        order2 = PlaceOrderDataInput(
                            marketId=opp["match"].opinion_market_id,
                            tokenId=str(opp["second_token"]),
                            side=opp["second_side"],
                            orderType=LIMIT_ORDER,
                            price=str(second_price if second_price is not None else opp["second_price"]),
                            makerAmountInBaseToken=str(second_order_size)
                        )
                        success, res2 = self._place_opinion_order_with_retries(
                            order2,
                            context="即时执行对冲"
                        )
                        if success and res2:
                            logger.info("✅ Opinion 对冲订单提交成功 (即时执行)")
                        else:
                            logger.error(f"❌ Opinion 对冲下单失败（已尝试 {self.config.order_max_retries} 次）")
                    except Exception as e:
                        logger.error(f"❌ Opinion 对冲下单异常: {e}")
                        traceback.print_exc()
                else:
                    try:
                        order2 = OrderArgs(
                            token_id=opp["second_token"],
                            price=second_price if second_price is not None else opp["second_price"],
                            size=second_order_size,
                            side=opp["second_side"]
                        )
                        success, res2 = self._place_polymarket_order_with_retries(
                            order2,
                            OrderType.GTC,
                            context="即时执行对冲"
                        )
                        if success:
                            logger.info(f"✅ Polymarket 对冲订单提交成功 (即时执行): {res2}")
                        else:
                            logger.error(f"❌ Polymarket 对冲下单失败（已尝试 {self.config.order_max_retries} 次）")
                    except Exception as e:
                        logger.error(f"❌ Polymarket 对冲下单异常: {e}")
                        traceback.print_exc()

                logger.info("🟢 即时套利执行线程完成")
                return

        except Exception as e:
            logger.error(f"❌ 即时执行线程异常: {e}")
            traceback.print_exc()

    # ==================== WebSocket连接管理 ====================

    def connect_websockets(self) -> bool:
        """连接到所有WebSocket"""
        if not self.market_matches:
            logger.error("❌ 没有市场匹配，无法连接WebSocket")
            return False

        # Prepare asset/market IDs
        # 优化: Polymarket只订阅YES tokens，NO tokens通过推导获得
        poly_assets = []
        opinion_markets = []

        for match in self.market_matches:
            poly_assets.append(match.polymarket_yes_token)
            # 不订阅NO token，将通过YES token推导
            opinion_markets.append(match.opinion_market_id)

        logger.info(
            f"📡 准备连接: {len(poly_assets)} Polymarket YES tokens (NO tokens将自动推导), {len(opinion_markets)} Opinion markets"
        )

        # Register callback
        self.ws_manager.add_update_callback(self.on_orderbook_update)

        # Connect
        success = self.ws_manager.connect_all(poly_assets, opinion_markets)

        if success:
            logger.info("✅ WebSocket连接成功，开始实时监控!")
        else:
            logger.error("❌ WebSocket连接失败")

        return success

    def run_realtime(self):
        """运行实时监控"""
        logger.info("\n" + "=" * 100)
        logger.info("🚀 实时套利监控已启动")
        logger.info("=" * 100 + "\n")

        try:
            # Print stats periodically
            while True:
                # 检查余额不足标志
                if self._insufficient_balance_flag.is_set():
                    logger.error("❌ 检测到余额不足标志，立即退出监控循环")
                    self.ws_manager.close_all()
                    os._exit(1)

                time.sleep(30)

                # 清理过期的执行记录（超过1分钟）
                current_time = time.time()
                with self._exec_lock:
                    expired_keys = [
                        k for k, t in self._recent_executions.items()
                        if current_time - t > 60
                    ]
                    for k in expired_keys:
                        del self._recent_executions[k]

                stats = self.ws_manager.get_stats()
                with self.stats_lock:
                    app_stats = dict(self.stats)

                logger.info(f"\n📊 实时统计:")
                logger.info(
                    f"  Polymarket: {stats['polymarket']['messages']} msgs, {stats['polymarket']['cached_books']} books, {'✅' if stats['polymarket']['connected'] else '❌'} connected"
                )
                logger.info(
                    f"  Opinion: {stats['opinion']['messages']} msgs, {stats['opinion']['cached_books']} books, {'✅' if stats['opinion']['connected'] else '❌'} connected"
                )
                logger.info(f"  订单簿更新: {app_stats['orderbook_updates']}")
                logger.info(f"  发现机会: {app_stats['opportunities_found']}")
                logger.info(f"  已执行: {app_stats['opportunities_executed']}")
                logger.info(f"  去重拦截: {app_stats['opportunities_deduplicated']}\n")

        except KeyboardInterrupt:
            logger.info("\n⚠️ 用户中断，正在关闭...")
            self.ws_manager.close_all()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="实时WebSocket套利检测器 - Opinion vs Polymarket"
    )

    parser.add_argument(
        "--matches-file",
        type=str,
        default="market_matches.json",
        help="市场匹配结果文件路径",
    )

    args = parser.parse_args()

    try:
        # 初始化日志
        config = ArbitrageConfig()
        setup_logger(config.log_dir, config.arbitrage_log_pointer)

        # 显示配置摘要
        config.display_summary()

        # 创建实时套利检测器
        arbitrage = RealtimeArbitrage(config)

        # 加载市场匹配
        if not arbitrage.load_market_matches(args.matches_file):
            print("⚠️ 无法加载市场匹配")
            return

        # 连接WebSocket
        if not arbitrage.connect_websockets():
            print("❌ WebSocket连接失败")
            return

        # 运行实时监控
        arbitrage.run_realtime()

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
