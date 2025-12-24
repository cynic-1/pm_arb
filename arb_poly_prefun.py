"""
模块化跨平台套利检测器 - Polymarket vs PredictFun
使用 arbitrage_core 模块实现 Polymarket 和 PredictFun 之间的立即套利
"""

import os
import sys
import argparse
import time
import threading
import traceback
import json
import requests
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 导入核心模块
from arbitrage_core import ArbitrageConfig
from arbitrage_core.utils import setup_logger
from arbitrage_core.utils.helpers import to_float, to_int, dedupe_tokens
from arbitrage_core.predictfun_client import PredictFunClient
from arbitrage_core.predictfun_fees import PredictFunFeeCalculator

# Polymarket SDK
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, BookParams
from py_clob_client.order_builder.constants import BUY, SELL

# PredictFun SDK
from predict_sdk import (
    OrderBuilder, ChainId, Side, BuildOrderInput,
    LimitHelperInput, MarketHelperInput, Book
)
from datetime import datetime, timezone, timedelta

import logging

logger = logging.getLogger(__name__)


# ==================== 数据模型 ====================

class OrderBookLevel:
    """订单簿档位"""
    def __init__(self, price: float, size: float):
        self.price = price
        self.size = size


class OrderBookSnapshot:
    """订单簿快照"""
    def __init__(
        self,
        bids: List[OrderBookLevel],
        asks: List[OrderBookLevel],
        source: str,
        token_id: str,
        timestamp: float,
    ):
        self.bids = bids
        self.asks = asks
        self.source = source
        self.token_id = token_id
        self.timestamp = timestamp


class MarketMatch:
    """市场匹配对"""
    def __init__(self, data: Dict):
        self.question = data["question"]
        self.predictfun_market_id = data["predictfun_market_id"]
        self.predictfun_yes_token = data["predictfun_yes_token"]
        self.predictfun_no_token = data["predictfun_no_token"]
        self.polymarket_condition_id = data["polymarket_condition_id"]
        self.polymarket_yes_token = data["polymarket_yes_token"]
        self.polymarket_no_token = data["polymarket_no_token"]
        self.polymarket_slug = data["polymarket_slug"]
        self.similarity_score = data.get("similarity_score", 1.0)
        self.cutoff_at = data.get("cutoff_at")
        self.predictfun_fee_rate_bps = data.get("predictfun_fee_rate_bps", 0)
        self.is_neg_risk = data.get("is_neg_risk", False)


# ==================== 主套利类 ====================

class PolyPrefunArbitrage:
    """Polymarket vs PredictFun 跨平台套利检测器"""

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

        # Polymarket 客户端
        self._init_polymarket_client()

        # PredictFun 客户端
        self._init_predictfun_client()

        # 手续费计算器
        self.poly_fee_calc = 0.0  # Polymarket 手续费率
        self.prefun_fee_calc = PredictFunFeeCalculator(
            price_decimals=self.config.price_decimals,
            has_discount=os.getenv("PREDICTFUN_FEE_DISCOUNT", "0") == "1",
        )

        # 市场匹配缓存
        self.market_matches: List[MarketMatch] = []

        # 线程控制
        self._monitor_stop_event = threading.Event()
        self._active_exec_threads: List[threading.Thread] = []

        # 即时执行配置
        self.immediate_exec_enabled = self.config.immediate_exec_enabled
        self.immediate_min_percent = self.config.immediate_min_percent
        self.immediate_max_percent = self.config.immediate_max_percent

        # PredictFun API 配置
        self.predictfun_api = os.getenv(
            "PREDICTFUN_API", "https://api.predict.fun"
        )

        print("✅ 模块化套利检测器初始化完成!\n")

    def _init_polymarket_client(self) -> None:
        """初始化 Polymarket 客户端"""
        print("🔧 初始化 Polymarket 客户端...")
        HOST = "https://clob.polymarket.com"
        CHAIN_ID = 137
        PRIVATE_KEY = os.getenv("PM_KEY")
        FUNDER = os.getenv("PM_FUNDER")

        if PRIVATE_KEY:
            self.polymarket_client = ClobClient(
                HOST,
                key=PRIVATE_KEY,
                chain_id=CHAIN_ID,
                signature_type=2,
                funder=FUNDER
            )
            self.polymarket_client.set_api_creds(
                self.polymarket_client.create_or_derive_api_creds()
            )
            print("✅ Polymarket 客户端初始化完成 (交易模式)")
        else:
            self.polymarket_client = ClobClient(HOST)
            print("✅ Polymarket 客户端初始化完成 (只读模式)")

    def _init_predictfun_client(self) -> None:
        """初始化 PredictFun 客户端"""
        print("🔧 初始化 PredictFun 客户端...")
        PRIVATE_KEY = os.getenv("PREDICTFUN_KEY")
        PREDICT_ACCOUNT = os.getenv("PREDICTFUN_ACCOUNT")

        self.predictfun_client = PredictFunClient(
            private_key=PRIVATE_KEY,
            predict_account=PREDICT_ACCOUNT,
            chain_id=ChainId.BNB_MAINNET,
        )

        if PRIVATE_KEY:
            print("✅ PredictFun 客户端初始化完成 (交易模式)")
        else:
            print("✅ PredictFun 客户端初始化完成 (只读模式)")

    # ==================== 订单簿管理 ====================

    def get_polymarket_orderbook(
        self, token_id: str, depth: int = 5
    ) -> Optional[OrderBookSnapshot]:
        """获取 Polymarket 订单簿"""
        try:
            book = self.polymarket_client.get_order_book(token_id)
            logger.info(f"Polymarket order book for {token_id}")

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
            print(f"⚠️ Polymarket 订单簿获取失败 ({token_id[:20]}...): {exc}")
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
                books = self.polymarket_client.get_order_books(params=params)
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
                print(f"⚠️ 批量获取 Polymarket 订单簿失败: {exc}")

        return snapshots

    def get_predictfun_orderbook(
        self, market_id: int, depth: int = 5
    ) -> Optional[OrderBookSnapshot]:
        """获取单个 PredictFun 订单簿"""
        try:
            # 获取 API Key
            api_key = os.getenv("PREDICTFUN_API_KEY")
            headers = {}
            if api_key:
                headers["x-api-key"] = api_key

            # 从 PredictFun API 获取订单簿
            response = requests.get(
                f"{self.predictfun_api}/v1/markets/{market_id}/orderbook",
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            result = response.json()

            if not result.get("success"):
                raise Exception(f"API 返回失败: {result}")

            data = result.get("data", {})

            bids = self._normalize_predictfun_levels(
                data.get("bids", []), depth, reverse=True
            )
            asks = self._normalize_predictfun_levels(
                data.get("asks", []), depth, reverse=False
            )

            return OrderBookSnapshot(
                bids=bids,
                asks=asks,
                source="predictfun",
                token_id=str(market_id),
                timestamp=data.get("updateTimestampMs", time.time() * 1000) / 1000,
            )
        except Exception as exc:
            print(f"⚠️ PredictFun 订单簿获取失败 (market_id={market_id}): {exc}")
            return None

    def get_predictfun_orderbooks_bulk(
        self, market_ids: List[int], depth: int = 5
    ) -> Dict[int, OrderBookSnapshot]:
        """
        批量并行获取 PredictFun 订单簿

        Args:
            market_ids: 市场 ID 列表
            depth: 订单簿深度

        Returns:
            市场 ID -> 订单簿快照的字典
        """
        snapshots: Dict[int, OrderBookSnapshot] = {}

        if not market_ids:
            return snapshots

        # 去重
        unique_ids = list(set(market_ids))

        # 获取 API Key
        api_key = os.getenv("PREDICTFUN_API_KEY")
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key

        # 使用线程池并行获取
        def fetch_single_orderbook(market_id: int) -> tuple:
            """获取单个订单簿，返回 (market_id, snapshot)"""
            try:
                response = requests.get(
                    f"{self.predictfun_api}/v1/markets/{market_id}/orderbook",
                    headers=headers,
                    timeout=10,
                )
                response.raise_for_status()
                result = response.json()

                if not result.get("success"):
                    return (market_id, None)

                data = result.get("data", {})

                bids = self._normalize_predictfun_levels(
                    data.get("bids", []), depth, reverse=True
                )
                asks = self._normalize_predictfun_levels(
                    data.get("asks", []), depth, reverse=False
                )

                snapshot = OrderBookSnapshot(
                    bids=bids,
                    asks=asks,
                    source="predictfun",
                    token_id=str(market_id),
                    timestamp=data.get("updateTimestampMs", time.time() * 1000) / 1000,
                )
                return (market_id, snapshot)
            except Exception as exc:
                logger.debug(f"获取 PredictFun 订单簿失败 (market_id={market_id}): {exc}")
                return (market_id, None)

        # 并行获取，最多 10 个并发
        max_workers = min(10, len(unique_ids))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = executor.map(fetch_single_orderbook, unique_ids)

        # 收集结果
        for market_id, snapshot in results:
            if snapshot:
                snapshots[market_id] = snapshot

        return snapshots

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
            price = self._round_price(to_float(getattr(entry, "price", None)))
            size = to_float(
                getattr(entry, "size", None)
                or getattr(entry, "quantity", None)
                or getattr(entry, "remaining", None)
            )

            if price is None or size is None:
                continue

            levels.append(OrderBookLevel(price=price, size=size))

        return levels

    def _normalize_predictfun_levels(
        self, raw_levels: List, depth: int, reverse: bool
    ) -> List[OrderBookLevel]:
        """标准化 PredictFun 订单簿档位"""
        levels: List[OrderBookLevel] = []
        if not raw_levels:
            return levels

        # PredictFun API 返回的是 (price, size) 元组列表
        sorted_levels = sorted(raw_levels, key=lambda x: x[0], reverse=reverse)

        for price_val, size_val in sorted_levels[:depth]:
            price = self._round_price(to_float(price_val))
            size = to_float(size_val)

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
            price = self._round_price(1.0 - level.price)
            if price is None:
                continue
            no_bids.append(OrderBookLevel(price=price, size=level.size))
        no_bids.sort(key=lambda x: x.price, reverse=True)

        # NO的asks来自YES的bids
        no_asks: List[OrderBookLevel] = []
        for level in yes_book.bids:
            price = self._round_price(1.0 - level.price)
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

    def load_market_matches(self, filename: str = "market_matches_poly_prefun.json") -> bool:
        """从文件加载市场匹配"""
        if not os.path.exists(filename):
            print(f"⚠️ 文件不存在: {filename}")
            return False

        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)

            matches = [MarketMatch(item) for item in data if isinstance(item, dict)]
            self.market_matches = matches
            print(f"✅ 从 {filename} 加载 {len(matches)} 条匹配\n")
            return True
        except Exception as e:
            print(f"⚠️ 读取 {filename} 时出错: {e}")
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
        """
        计算盈利性指标（包含手续费）

        即时套利策略使用 Taker 订单（立即成交），需要计算 PredictFun 的 Taker 手续费
        """
        assumed_size = max(self.config.roi_reference_size, min_size or 0.0)

        # 计算有效价格（含手续费）
        if first_platform == "predictfun":
            # PredictFun Taker 订单：需要计算手续费
            if first_price is None:
                return None
            eff_first = self.prefun_fee_calc.calculate_effective_buy_price(
                first_price,
                match.predictfun_fee_rate_bps,
                is_maker=False  # 即时套利使用 Taker
            )
        else:
            # Polymarket: 手续费很低，暂时忽略（约 0.1%）
            eff_first = self._round_price(first_price)

        if second_platform == "predictfun":
            # PredictFun Taker 订单：需要计算手续费
            if second_price is None:
                return None
            eff_second = self.prefun_fee_calc.calculate_effective_buy_price(
                second_price,
                match.predictfun_fee_rate_bps,
                is_maker=False  # 即时套利使用 Taker
            )
        else:
            # Polymarket: 手续费很低，暂时忽略
            eff_second = self._round_price(second_price)

        if eff_first is None or eff_second is None:
            return None

        total_cost = self._round_price(eff_first + eff_second)
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

    # ==================== 辅助方法 ====================

    def _round_price(self, value: Optional[float]) -> Optional[float]:
        """四舍五入价格到配置的小数位数"""
        if value is None:
            return None
        try:
            return round(float(value), self.config.price_decimals)
        except (TypeError, ValueError):
            return None

    # ==================== 套利执行 ====================

    def execute_arbitrage_pro(self):
        """专业套利执行模式"""
        if not self.market_matches:
            print("❌ 没有可用的市场匹配")
            return

        THRESHOLD_PRICE = 0.97
        THRESHOLD_SIZE = 200

        print(f"\n{'='*100}")
        print(f"开始扫描所有市场的套利机会...")
        print(f"条件: 成本 < ${THRESHOLD_PRICE:.2f}, 最小数量 > {THRESHOLD_SIZE}")
        print(f"{'='*100}\n")

        start_time = time.time()
        total_matches = len(self.market_matches)
        completed_count = 0
        batch_size = self.config.orderbook_batch_size

        for batch_start in range(0, total_matches, batch_size):
            batch_matches = self.market_matches[batch_start : batch_start + batch_size]

            # 批量获取 Polymarket 订单簿
            poly_tokens = [
                m.polymarket_yes_token for m in batch_matches if m.polymarket_yes_token
            ]
            poly_books = self.get_polymarket_orderbooks_bulk(poly_tokens)

            # 批量获取 PredictFun 订单簿
            prefun_market_ids = [
                m.predictfun_market_id for m in batch_matches if m.predictfun_market_id
            ]
            prefun_books = self.get_predictfun_orderbooks_bulk(prefun_market_ids)

            # 扫描每个市场
            for match in batch_matches:
                completed_count += 1
                print(f"[{completed_count}/{total_matches}] 扫描: {match.question[:70]}...")

                # 获取 Polymarket 订单簿
                poly_yes_book = poly_books.get(match.polymarket_yes_token)
                if not poly_yes_book:
                    poly_yes_book = self.get_polymarket_orderbook(match.polymarket_yes_token)

                if not poly_yes_book:
                    print("  ⚠️ 无法获取 Polymarket 订单簿")
                    continue

                # 推导 Polymarket NO 订单簿
                poly_no_book = self.derive_no_orderbook(
                    poly_yes_book, match.polymarket_no_token
                )

                # 获取 PredictFun 订单簿（从批量获取结果中）
                prefun_yes_book = prefun_books.get(match.predictfun_market_id)
                if not prefun_yes_book:
                    # 如果批量获取失败，尝试单独获取
                    prefun_yes_book = self.get_predictfun_orderbook(match.predictfun_market_id)

                if not prefun_yes_book:
                    print("  ⚠️ 无法获取 PredictFun 订单簿")
                    continue

                # 推导 PredictFun NO 订单簿
                prefun_no_book = self.derive_no_orderbook(
                    prefun_yes_book, str(match.predictfun_market_id) + "_no"
                )

                # 检测套利机会（双向检查）
                opportunities = self._scan_market_opportunities(
                    match,
                    poly_yes_book,
                    poly_no_book,
                    prefun_yes_book,
                    prefun_no_book,
                    THRESHOLD_PRICE,
                    THRESHOLD_SIZE,
                )

                # 尝试自动执行发现的机会
                for opp in opportunities:
                    logger.info(f"Detected opportunity: {opp}")
                    self._maybe_auto_execute(opp)

            # 批次之间稍微等待，避免请求过快
            time.sleep(0.5)

        elapsed = time.time() - start_time
        print(f"\n✅ 扫描完成，耗时 {elapsed:.2f}s\n")

    def _scan_market_opportunities(
        self,
        match: MarketMatch,
        poly_yes_book: OrderBookSnapshot,
        poly_no_book: Optional[OrderBookSnapshot],
        prefun_yes_book: OrderBookSnapshot,
        prefun_no_book: Optional[OrderBookSnapshot],
        threshold_price: float,
        threshold_size: float,
    ) -> List[Dict[str, Any]]:
        """
        扫描单个市场的套利机会，返回机会列表

        检测 4 种策略：
        1. Polymarket YES ask + PredictFun NO ask
        2. Polymarket NO ask + PredictFun YES ask
        3. PredictFun YES ask + Polymarket NO ask
        4. PredictFun NO ask + Polymarket YES ask
        """
        opportunities = []

        # 策略1: Polymarket YES ask + PredictFun NO ask
        if (
            poly_yes_book
            and poly_yes_book.asks
            and prefun_no_book
            and prefun_no_book.asks
        ):
            poly_yes_ask = poly_yes_book.asks[0]
            prefun_no_ask = prefun_no_book.asks[0]
            print(f"Poly YES ask: {poly_yes_ask.price} size: {poly_yes_ask.size}")
            print(f"Prefun NO ask: {prefun_no_ask.price} size: {prefun_no_ask.size}")

            if poly_yes_ask and prefun_no_ask and poly_yes_ask.price is not None and prefun_no_ask.price is not None:
                min_size = min(poly_yes_ask.size or 0, prefun_no_ask.size or 0)
                metrics = self.compute_profitability_metrics(
                    match,
                    "polymarket",
                    poly_yes_ask.price,
                    "predictfun",
                    prefun_no_ask.price,
                    min_size,
                )

                if metrics and metrics["cost"] < threshold_price and min_size > threshold_size:
                    opportunity = {
                        'match': match,
                        'type': 'immediate',
                        'strategy': 'poly_yes_ask_prefun_no_ask',
                        'name': '立即套利: Polymarket YES ask + PredictFun NO ask',
                        'cost': metrics['cost'],
                        'profit_rate': metrics['profit_rate'],
                        'annualized_rate': metrics['annualized_rate'],
                        'min_size': min_size,
                        'first_platform': 'polymarket',
                        'first_token': match.polymarket_yes_token,
                        'first_price': self._round_price(poly_yes_ask.price),
                        'first_side': BUY,
                        'second_platform': 'predictfun',
                        'second_token': match.predictfun_no_token,
                        'second_price': self._round_price(prefun_no_ask.price),
                        'second_side': 'BUY',
                        'fee_rate_bps': match.predictfun_fee_rate_bps,
                        'is_neg_risk': match.is_neg_risk,
                    }
                    opportunities.append(opportunity)
                    self._report_opportunity("Poly YES ask + PredictFun NO ask", metrics, min_size)

        # 策略2: Polymarket NO ask + PredictFun YES ask
        if (
            poly_no_book
            and poly_no_book.asks
            and prefun_yes_book
            and prefun_yes_book.asks
        ):
            poly_no_ask = poly_no_book.asks[0]
            prefun_yes_ask = prefun_yes_book.asks[0]
            print(f"Poly NO ask: {poly_no_ask.price} size: {poly_no_ask.size}")
            print(f"Prefun YES ask: {prefun_yes_ask.price} size: {prefun_yes_ask.size}")

            if poly_no_ask and prefun_yes_ask and poly_no_ask.price is not None and prefun_yes_ask.price is not None:
                min_size = min(poly_no_ask.size or 0, prefun_yes_ask.size or 0)
                metrics = self.compute_profitability_metrics(
                    match,
                    "polymarket",
                    poly_no_ask.price,
                    "predictfun",
                    prefun_yes_ask.price,
                    min_size,
                )

                if metrics and metrics["cost"] < threshold_price and min_size > threshold_size:
                    opportunity = {
                        'match': match,
                        'type': 'immediate',
                        'strategy': 'poly_no_ask_prefun_yes_ask',
                        'name': '立即套利: Polymarket NO ask + PredictFun YES ask',
                        'cost': metrics['cost'],
                        'profit_rate': metrics['profit_rate'],
                        'annualized_rate': metrics['annualized_rate'],
                        'min_size': min_size,
                        'first_platform': 'polymarket',
                        'first_token': match.polymarket_no_token,
                        'first_price': self._round_price(poly_no_ask.price),
                        'first_side': BUY,
                        'second_platform': 'predictfun',
                        'second_token': match.predictfun_yes_token,
                        'second_price': self._round_price(prefun_yes_ask.price),
                        'second_side': 'BUY',
                        'fee_rate_bps': match.predictfun_fee_rate_bps,
                        'is_neg_risk': match.is_neg_risk,
                    }
                    opportunities.append(opportunity)
                    self._report_opportunity("Poly NO ask + PredictFun YES ask", metrics, min_size)

        # 策略3: PredictFun YES ask + Polymarket NO ask
        if (
            prefun_yes_book
            and prefun_yes_book.asks
            and poly_no_book
            and poly_no_book.asks
        ):
            prefun_yes_ask = prefun_yes_book.asks[0]
            poly_no_ask = poly_no_book.asks[0]

            if prefun_yes_ask and poly_no_ask and prefun_yes_ask.price is not None and poly_no_ask.price is not None:
                min_size = min(prefun_yes_ask.size or 0, poly_no_ask.size or 0)
                metrics = self.compute_profitability_metrics(
                    match,
                    "predictfun",
                    prefun_yes_ask.price,
                    "polymarket",
                    poly_no_ask.price,
                    min_size,
                )

                if metrics and metrics["cost"] < threshold_price and min_size > threshold_size:
                    opportunity = {
                        'match': match,
                        'type': 'immediate',
                        'strategy': 'prefun_yes_ask_poly_no_ask',
                        'name': '立即套利: PredictFun YES ask + Polymarket NO ask',
                        'cost': metrics['cost'],
                        'profit_rate': metrics['profit_rate'],
                        'annualized_rate': metrics['annualized_rate'],
                        'min_size': min_size,
                        'first_platform': 'predictfun',
                        'first_token': match.predictfun_yes_token,
                        'first_price': self._round_price(prefun_yes_ask.price),
                        'first_side': 'BUY',
                        'second_platform': 'polymarket',
                        'second_token': match.polymarket_no_token,
                        'second_price': self._round_price(poly_no_ask.price),
                        'second_side': BUY,
                        'fee_rate_bps': match.predictfun_fee_rate_bps,
                        'is_neg_risk': match.is_neg_risk,
                    }
                    opportunities.append(opportunity)
                    self._report_opportunity("PredictFun YES ask + Poly NO ask", metrics, min_size)

        # 策略4: PredictFun NO ask + Polymarket YES ask
        if (
            prefun_no_book
            and prefun_no_book.asks
            and poly_yes_book
            and poly_yes_book.asks
        ):
            prefun_no_ask = prefun_no_book.asks[0]
            poly_yes_ask = poly_yes_book.asks[0]

            if prefun_no_ask and poly_yes_ask and prefun_no_ask.price is not None and poly_yes_ask.price is not None:
                min_size = min(prefun_no_ask.size or 0, poly_yes_ask.size or 0)
                metrics = self.compute_profitability_metrics(
                    match,
                    "predictfun",
                    prefun_no_ask.price,
                    "polymarket",
                    poly_yes_ask.price,
                    min_size,
                )

                if metrics and metrics["cost"] < threshold_price and min_size > threshold_size:
                    opportunity = {
                        'match': match,
                        'type': 'immediate',
                        'strategy': 'prefun_no_ask_poly_yes_ask',
                        'name': '立即套利: PredictFun NO ask + Polymarket YES ask',
                        'cost': metrics['cost'],
                        'profit_rate': metrics['profit_rate'],
                        'annualized_rate': metrics['annualized_rate'],
                        'min_size': min_size,
                        'first_platform': 'predictfun',
                        'first_token': match.predictfun_no_token,
                        'first_price': self._round_price(prefun_no_ask.price),
                        'first_side': 'BUY',
                        'second_platform': 'polymarket',
                        'second_token': match.polymarket_yes_token,
                        'second_price': self._round_price(poly_yes_ask.price),
                        'second_side': BUY,
                        'fee_rate_bps': match.predictfun_fee_rate_bps,
                        'is_neg_risk': match.is_neg_risk,
                    }
                    opportunities.append(opportunity)
                    self._report_opportunity("PredictFun NO ask + Poly YES ask", metrics, min_size)

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

    def _maybe_auto_execute(self, opportunity: Dict[str, Any]) -> None:
        """在满足配置阈值时尝试自动执行即时套利"""
        if not self.immediate_exec_enabled:
            return

        profit_rate = opportunity.get('profit_rate')
        if profit_rate is None:
            return

        lower = self.immediate_min_percent
        upper = self.immediate_max_percent

        if lower <= profit_rate <= upper:
            print(f"  ⚡ 利润率 {profit_rate:.2f}% 在阈值 [{lower:.2f}%,{upper:.2f}%]，启动即时执行")
            print(f"  ⚠️  注意: 即时执行功能尚未完全实现，需要补充订单构建逻辑")
            # TODO: 实现实际的订单执行逻辑
        else:
            print(f"  🔶 利润率 {profit_rate:.2f}% 不在阈值范围 [{lower:.2f}%,{upper:.2f}%]，跳过自动执行")

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

                elapsed = time.time() - cycle_start
                sleep_time = max(0.0, min_interval - elapsed)

                if sleep_time > 0:
                    print(f"🕒 {sleep_time:.1f}s 后进行下一轮扫描")
                    self._monitor_stop_event.wait(timeout=sleep_time)
        finally:
            self._monitor_stop_event.set()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="模块化跨平台套利检测器 - Polymarket vs PredictFun"
    )

    parser.add_argument(
        "--matches-file",
        type=str,
        default="market_matches_poly_prefun.json",
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
        setup_logger(config.log_dir, "arb_poly_prefun.log")

        # 显示配置摘要
        config.display_summary()

        # 创建套利检测器
        arbitrage = PolyPrefunArbitrage(config)

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
