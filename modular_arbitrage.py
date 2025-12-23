"""
模块化跨平台套利检测器 - Opinion vs Polymarket
使用 arbitrage_core 模块重构的版本

原始文件: arbitrage.py (1873 行)
重构后: ~300 行 (减少 84%)
"""

import os
import sys
import argparse
import time
import threading
import traceback
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

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
)
from arbitrage_core.utils import setup_logger
from arbitrage_core.utils.helpers import to_float, to_int, dedupe_tokens

# Opinion SDK
from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER

# Polymarket SDK
from py_clob_client.clob_types import OrderArgs, OrderType, BookParams
from py_clob_client.order_builder.constants import BUY, SELL
import logging
import json

logger = logging.getLogger(__name__)


class ModularArbitrage:
    """模块化跨平台套利检测器"""

    def __init__(self, config: Optional[ArbitrageConfig] = None):
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

        # 市场匹配缓存
        self.market_matches: List[MarketMatch] = []

        # 线程控制
        self._monitor_stop_event = threading.Event()
        self._active_exec_threads: List[threading.Thread] = []

        # 速率限制
        self._opinion_rate_lock = threading.Lock()
        self._opinion_last_request = 0.0

        print("✅ 模块化套利检测器初始化完成!\n")

    # ==================== 订单簿管理 ====================

    def _throttle_opinion_request(self) -> None:
        """Opinion API 速率限制"""
        max_rps = self.config.opinion_max_rps
        if max_rps <= 0:
            return

        min_interval = 1.0 / max_rps
        while True:
            with self._opinion_rate_lock:
                now = time.perf_counter()
                wait = min_interval - (now - self._opinion_last_request)
                if wait <= 0:
                    self._opinion_last_request = now
                    return
            time.sleep(min_interval / 2.0)

    def get_opinion_orderbook(
        self, token_id: str, depth: int = 5
    ) -> Optional[OrderBookSnapshot]:
        """获取 Opinion 订单簿"""
        try:
            self._throttle_opinion_request()
            response = self.clients.get_opinion_client().get_orderbook(token_id)
            logger.debug(f"Opinion order book for {token_id}")

            if response.errno != 0:
                raise Exception(f"Opinion API 返回错误码 {response.errno}")

            book = response.result
            bids = self._normalize_opinion_levels(
                getattr(book, "bids", []), depth, reverse=True
            )
            asks = self._normalize_opinion_levels(
                getattr(book, "asks", []), depth, reverse=False
            )

            return OrderBookSnapshot(
                bids=bids,
                asks=asks,
                source="opinion",
                token_id=token_id,
                timestamp=time.time(),
            )
        except Exception as exc:
            logger.error(f"⚠️ Opinion 订单簿获取失败 ({token_id[:20]}...): {exc}")
            return None

    def get_polymarket_orderbook(
        self, token_id: str, depth: int = 5
    ) -> Optional[OrderBookSnapshot]:
        """获取 Polymarket 订单簿"""
        try:
            book = self.clients.get_polymarket_client().get_order_book(token_id)

            if not book:
                raise Exception("Polymarket 返回空订单簿")

            bids = self._normalize_polymarket_levels(
                getattr(book, "bids", []), depth, reverse=True
            )
            asks = self._normalize_polymarket_levels(
                getattr(book, "asks", []), depth, reverse=False
            )

            return OrderBookSnapshot(
                bids=bids,
                asks=asks,
                source="polymarket",
                token_id=token_id,
                timestamp=time.time(),
            )
        except Exception as exc:
            logger.error(f"⚠️ Polymarket 订单簿获取失败 ({token_id[:20]}...): {exc}")
            return None

    def get_polymarket_orderbooks_bulk(
        self, token_ids: List[str], depth: int = 5
    ) -> Dict[str, OrderBookSnapshot]:
        """批量获取 Polymarket 订单簿"""
        snapshots: Dict[str, OrderBookSnapshot] = {}
        tokens = dedupe_tokens(token_ids)
        if not tokens:
            return snapshots

        chunk_size = self.config.polymarket_books_chunk
        for start in range(0, len(tokens), chunk_size):
            chunk = tokens[start : start + chunk_size]
            try:
                params = [BookParams(token_id=tid) for tid in chunk]
                books = self.clients.get_polymarket_client().get_order_books(
                    params=params
                )
                now = time.time()

                for idx, book in enumerate(books):
                    token_key = (
                        getattr(book, "asset_id", None)
                        or getattr(book, "token_id", None)
                        or (chunk[idx] if idx < len(chunk) else None)
                    )
                    if not token_key:
                        continue

                    bids = self._normalize_polymarket_levels(
                        getattr(book, "bids", []), depth, reverse=True
                    )
                    asks = self._normalize_polymarket_levels(
                        getattr(book, "asks", []), depth, reverse=False
                    )
                    snapshots[token_key] = OrderBookSnapshot(
                        bids=bids,
                        asks=asks,
                        source="polymarket",
                        token_id=token_key,
                        timestamp=now,
                    )
            except Exception as exc:
                logger.debug(f"⚠️ 批量获取 Polymarket 订单簿失败: {exc}")

        return snapshots

    def fetch_opinion_orderbooks_parallel(
        self, token_ids: List[str], depth: int = 5
    ) -> Dict[str, Optional[OrderBookSnapshot]]:
        """并发获取 Opinion 订单簿"""
        from concurrent.futures import as_completed

        snapshots: Dict[str, Optional[OrderBookSnapshot]] = {}
        tokens = dedupe_tokens(token_ids)
        if not tokens:
            return snapshots

        max_workers = self.config.opinion_orderbook_workers
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.get_opinion_orderbook, token, depth): token
                for token in tokens
            }
            for future in as_completed(futures):
                token = futures[future]
                try:
                    snapshots[token] = future.result()
                except Exception as exc:
                    logger.debug(f"⚠️ Opinion 订单簿获取失败 (token={token[:12]}...): {exc}")
                    snapshots[token] = None

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
    ) -> Optional[Dict[str, float]]:
        """计算盈利性指标"""
        assumed_size = max(self.config.roi_reference_size, min_size or 0.0)

        # 计算有效价格（含手续费）
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
        self, order: Any, context: str = ""
    ) -> Tuple[bool, Optional[Any]]:
        """Opinion 下单带重试"""
        prefix = f"[{context}] " if context else ""
        last_result = None

        for attempt in range(1, self.config.order_max_retries + 1):
            try:
                result = self.clients.get_opinion_client().place_order(order)
                last_result = result

                if getattr(result, "errno", 0) == 0:
                    return True, result

                err_msg = getattr(result, "errmsg", "unknown error")
                logger.error(
                    f"⚠️ {prefix}Opinion 下单失败 (尝试 {attempt}/{self.config.order_max_retries}): {err_msg}"
                )

                if "insufficient balance" in err_msg.lower():
                    logger.error(f"\n❌ 检测到余额不足，退出程序")
                    sys.exit(1)

            except Exception as exc:
                logger.error(f"⚠️ {prefix}Opinion 下单异常: {exc}")
                if "insufficient balance" in str(exc).lower():
                    sys.exit(1)

            if attempt < self.config.order_max_retries:
                time.sleep(self.config.order_retry_delay)

        return False, last_result

    def place_polymarket_order_with_retries(
        self, order_args: Any, order_type: Any, context: str = ""
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Polymarket 下单带重试"""
        prefix = f"[{context}] " if context else ""
        last_result = None

        for attempt in range(1, self.config.order_max_retries + 1):
            try:
                signed_order = self.clients.get_polymarket_client().create_order(
                    order_args
                )
                result = self.clients.get_polymarket_client().post_order(
                    signed_order, order_type
                )
                last_result = result if isinstance(result, dict) else None

                error_msg = None
                if isinstance(result, dict):
                    if result.get("success") is False:
                        error_msg = result.get("message") or result.get("error")
                    elif result.get("error"):
                        error_msg = result.get("error")

                if not error_msg:
                    return True, result

                logger.error(f"⚠️ {prefix}Polymarket 下单失败: {error_msg}")

                if error_msg and "not enough balance" in error_msg.lower():
                    sys.exit(1)

            except Exception as exc:
                logger.error(f"⚠️ {prefix}Polymarket 下单异常: {exc}")
                if "not enough balance" in str(exc).lower():
                    sys.exit(1)

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
            print(f"  ⚡ 年化收益率 {annualized_rate:.2f}% 在阈值 [{lower:.2f}%,{upper:.2f}%]，启动即时执行线程 (利润率={profit_rate:.2f}%)")
            try:
                self._spawn_execute_thread(opportunity)
            except Exception as exc:
                print(f"⚠️ 无法启动即时执行线程: {exc}")
        else:
            print(f"  🔶 年化收益率 {annualized_rate:.2f}% 不在阈值范围 [{lower:.2f}%,{upper:.2f}%]，跳过自动执行")

    def _spawn_execute_thread(self, opportunity: Dict[str, Any]) -> None:
        """启动一个后台线程来执行给定的套利机会（非交互）"""
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

            # Immediate execution: place both orders
            if opp.get('type') == 'immediate':
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

                # Place first order
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
                            context="即时执行首单"
                        )
                        if success and res1:
                            print("✅ Opinion 订单提交成功 (即时执行)")
                        else:
                            print(f"❌ Opinion 下单失败（已尝试 {self.config.order_max_retries} 次）")
                    except Exception as e:
                        print(f"❌ Opinion 下单异常: {e}")
                else:
                    try:
                        order1 = OrderArgs(
                            token_id=opp['first_token'],
                            price=first_price if first_price is not None else opp['first_price'],
                            size=first_order_size,
                            side=opp['first_side']
                        )
                        success, res1 = self.place_polymarket_order_with_retries(
                            order1,
                            OrderType.GTC,
                            context="即时执行首单"
                        )
                        if success:
                            print(f"✅ Polymarket 订单提交成功 (即时执行): {res1}")
                        else:
                            print(f"❌ Polymarket 下单失败（已尝试 {self.config.order_max_retries} 次）")
                    except Exception as e:
                        print(f"❌ Polymarket 下单异常: {e}")

                # Place second order
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
                        order2 = OrderArgs(
                            token_id=opp['second_token'],
                            price=second_price if second_price is not None else opp['second_price'],
                            size=second_order_size,
                            side=opp['second_side']
                        )
                        success, res2 = self.place_polymarket_order_with_retries(
                            order2,
                            OrderType.GTC,
                            context="即时执行对冲"
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

        THRESHOLD_PRICE = 0.97
        THRESHOLD_SIZE = 200

        logger.info(f"\n{'='*100}")

        start_time = time.time()
        total_matches = len(self.market_matches)
        completed_count = 0
        batch_size = self.config.orderbook_batch_size

        for batch_start in range(0, total_matches, batch_size):
            batch_matches = self.market_matches[batch_start : batch_start + batch_size]

            # 批量获取订单簿
            poly_tokens = [
                m.polymarket_yes_token for m in batch_matches if m.polymarket_yes_token
            ]
            opinion_tokens = [
                m.opinion_yes_token for m in batch_matches if m.opinion_yes_token
            ]

            with ThreadPoolExecutor(max_workers=2) as executor:
                future_poly = executor.submit(
                    self.get_polymarket_orderbooks_bulk, poly_tokens
                )
                future_opinion = executor.submit(
                    self.fetch_opinion_orderbooks_parallel, opinion_tokens
                )
                poly_books = future_poly.result()
                opinion_books = future_opinion.result()

            # 扫描每个市场
            for match in batch_matches:
                opinion_yes_book = opinion_books.get(match.opinion_yes_token)
                poly_yes_book = poly_books.get(match.polymarket_yes_token)

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
                    THRESHOLD_PRICE,
                    THRESHOLD_SIZE,
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
        min_interval = max(5.0, interval_seconds)
        print(f"♻️ 启动专业套利循环，间隔 {min_interval:.1f}s")

        try:
            while not self._monitor_stop_event.is_set():
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

    args = parser.parse_args()

    try:
        # 初始化日志
        config = ArbitrageConfig()
        setup_logger(config.log_dir, config.arbitrage_log_pointer)

        # 显示配置摘要
        config.display_summary()

        # 创建套利检测器
        arbitrage = ModularArbitrage(config)

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


if __name__ == "__main__":
    main()
