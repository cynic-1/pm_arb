"""
流动性提供模式 - Opinion vs Polymarket

变体：在 Opinion 上挂满足套利阈值的最高价（最多三位小数），提高成交率。
其余逻辑与 modular_arbitrage_mm_clean.py 保持一致。
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Dict, Optional, Tuple

from arbitrage_core import ArbitrageConfig, MarketMatch
from arbitrage_core.utils import setup_logger
from modular_arbitrage_mm_clean import ModularArbitrageMM

# Opinion SDK
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide

# Polymarket SDK
from py_clob_client.order_builder.constants import BUY

logger = logging.getLogger(__name__)


class ModularArbitrageMMBestPrice(ModularArbitrageMM):
    """Opinion 挂单价格改为满足套利阈值的最高价。"""

    def _round_opinion_price(self, price: Optional[float]) -> Optional[float]:
        rounded = self.fee_calculator.round_price(price)
        if rounded is None:
            return None
        # Opinion 支持的价格最多三位小数
        return round(rounded, 3)

    def _find_best_opinion_price_for_threshold(
        self,
        match: MarketMatch,
        opinion_book: Any,
        hedge_price: float,
        available_hedge: float,
    ) -> Optional[Tuple[float, Dict[str, Any]]]:
        bids = getattr(opinion_book, "bids", []) or []
        if not bids:
            return None

        tick = 10 ** -self.config.price_decimals

        best_bid = max((level.price for level in bids if level and level.price is not None), default=None)
        if best_bid is None:
            return None

        best_bid = self._round_opinion_price(best_bid)
        if best_bid is None or best_bid <= 0:
            return None

        max_price = best_bid
        asks = getattr(opinion_book, "asks", []) or []
        if asks:
            best_ask = min((level.price for level in asks if level and level.price is not None), default=None)
            if best_ask is not None and best_ask > 0:
                candidate = self._round_opinion_price(best_ask - tick)
                if candidate is not None and candidate > 0:
                    max_price = max(max_price, candidate)

        max_price = self._round_opinion_price(max_price)
        min_price = self._round_opinion_price(best_bid)
        if max_price is None or min_price is None:
            return None

        if max_price < min_price:
            max_price = min_price

        # 从高到低搜索满足套利阈值的最高价
        price = max_price
        while price is not None and price + 1e-9 >= min_price:
            metrics = self.compute_profitability_metrics(
                match,
                "opinion",
                price,
                "polymarket",
                hedge_price,
                available_hedge,
                is_maker_order=True,
            )
            if metrics:
                annualized = metrics.get("annualized_rate")
                if annualized is not None and annualized >= self.liquidity_min_annualized:
                    return price, metrics

            price = self._round_opinion_price(price - tick)

        return None

    def _evaluate_liquidity_pair(
        self,
        match: MarketMatch,
        opinion_book: Any,
        poly_book: Any,
        opinion_token: Optional[str],
        polymarket_token: Optional[str],
        direction: str,
    ) -> Optional[Dict[str, Any]]:
        if not opinion_book or not poly_book or not opinion_token or not polymarket_token:
            return None

        hedge_level = poly_book.best_ask()
        if not hedge_level:
            return None

        available_hedge = hedge_level.size or 0.0
        if available_hedge < self.liquidity_min_size:
            return None

        best_price_result = self._find_best_opinion_price_for_threshold(
            match=match,
            opinion_book=opinion_book,
            hedge_price=hedge_level.price,
            available_hedge=available_hedge,
        )
        if not best_price_result:
            return None

        opinion_price, metrics = best_price_result

        target_size = min(self.liquidity_target_size, available_hedge)
        if target_size < self.liquidity_min_size:
            return None

        key = self._make_liquidity_key(match, opinion_token, direction)
        return {
            "key": key,
            "match": match,
            "opinion_token": opinion_token,
            "opinion_price": opinion_price,
            "opinion_side": OrderSide.BUY,
            "polymarket_token": polymarket_token,
            "polymarket_price": hedge_level.price,
            "polymarket_available": available_hedge,
            "hedge_side": BUY,
            "direction": direction,
            "min_size": target_size,
            "annualized_rate": metrics.get("annualized_rate"),
            "profit_rate": metrics.get("profit_rate"),
            "cost": metrics.get("cost"),
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="流动性提供模式 - Opinion vs Polymarket (最高价挂单变体)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python modular_arbitrage_mm_best_price.py --liquidity --matches-file market_matches.json
  python modular_arbitrage_mm_best_price.py --liquidity-once --matches-file market_matches.json
""",
    )

    parser.add_argument(
        "--matches-file",
        type=str,
        default="market_matches.json",
        help="市场匹配结果文件路径，支持多个文件用逗号分隔 (默认: market_matches.json)",
    )

    parser.add_argument("--liquidity", action="store_true", help="运行流动性提供模式")
    parser.add_argument("--liquidity-once", action="store_true", help="仅运行一次流动性扫描，不进入循环")
    parser.add_argument(
        "--liquidity-interval",
        type=float,
        default=None,
        help="流动性模式循环间隔（秒），默认读取 LIQUIDITY_LOOP_INTERVAL 环境变量",
    )

    args = parser.parse_args()

    try:
        config = ArbitrageConfig()
        setup_logger(config.log_dir, config.arbitrage_log_pointer)

        scanner = ModularArbitrageMMBestPrice(config)

        if args.liquidity or args.liquidity_once:
            if not scanner.polymarket_trading_enabled:
                logger.error("⚠️ 未配置 Polymarket 交易密钥，无法执行对冲。")
                return

            if not scanner.load_market_matches(args.matches_file):
                logger.error("⚠️ 无法加载市场匹配")
                return

            liquidity_interval = (
                max(0.0, args.liquidity_interval)
                if args.liquidity_interval is not None
                else config.liquidity_loop_interval
            )

            if args.liquidity_once or liquidity_interval <= 0:
                scanner.run_liquidity_provider_cycle()
                scanner.wait_for_liquidity_orders()
            else:
                scanner.run_liquidity_provider_loop(liquidity_interval)
            return

        print("ℹ️ 请使用 --liquidity 或 --liquidity-once 参数")

    except KeyboardInterrupt:
        logger.warning("\n\n⚠️ 用户中断")
    except Exception as exc:
        logger.error(f"\n❌ 发生错误: {exc}")


if __name__ == "__main__":
    main()
