"""
流动性评分器 - 评估配对市场的流动性质量

评分标准（确定性、可解释）：
1. 订单簿深度（在中间价附近带宽内的可成交深度）
2. 订单簿均衡度（买卖盘深度越均衡，分数越高）
3. 相对价差（价差越小，流动性越好）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class LiquidityScore:
    """流动性评分结果"""

    market_key: str  # 市场唯一标识
    total_score: float  # 综合得分（0-100）

    # Opinion 市场指标
    opinion_depth_score: float  # 深度得分
    opinion_price_score: float  # 价格得分
    opinion_spread_score: float  # 价差得分
    opinion_best_bid: Optional[float] = None
    opinion_best_ask: Optional[float] = None
    opinion_bid_size: Optional[float] = None
    opinion_ask_size: Optional[float] = None

    # Polymarket 市场指标
    poly_depth_score: float = 0.0
    poly_price_score: float = 0.0
    poly_spread_score: float = 0.0
    poly_best_bid: Optional[float] = None
    poly_best_ask: Optional[float] = None
    poly_bid_size: Optional[float] = None
    poly_ask_size: Optional[float] = None

    # 组合指标
    cross_platform_balance: float = 0.0  # 跨平台均衡度

    def __repr__(self) -> str:
        return (
            f"LiquidityScore(key={self.market_key[:40]}..., "
            f"total={self.total_score:.2f}, "
            f"opinion_depth={self.opinion_depth_score:.2f}, "
            f"poly_depth={self.poly_depth_score:.2f})"
        )


class LiquidityScorer:
    """流动性评分器"""

    def __init__(
        self,
        depth_weight: float = 0.5,  # 深度权重
        price_weight: float = 0.3,  # 均衡度权重（沿用字段名）
        spread_weight: float = 0.2,  # 价差权重
        min_value_threshold: float = 10.0,  # 最小深度阈值（单位：份额）
        max_value_for_score: float = 5000.0,  # 深度评分上限（单位：份额）
        max_relative_spread: float = 0.35,  # 最大相对价差（35%）
        depth_band: float = 0.05,  # 深度带宽（相对中间价）
        min_price_band: float = 0.02,  # 最小绝对带宽，防止过小
        depth_levels: int = 20,  # 计算深度时最多使用的档位数
    ):
        self.depth_weight = depth_weight
        self.price_weight = price_weight
        self.spread_weight = spread_weight
        self.min_value_threshold = min_value_threshold
        self.max_value_for_score = max_value_for_score
        self.max_relative_spread = max_relative_spread
        self.depth_band = depth_band
        self.min_price_band = min_price_band
        self.depth_levels = depth_levels

        # 验证权重和为1
        total_weight = depth_weight + price_weight + spread_weight
        if abs(total_weight - 1.0) > 0.01:
            logger.warning(f"权重和不为1.0: {total_weight}，将自动归一化")
            self.depth_weight /= total_weight
            self.price_weight /= total_weight
            self.spread_weight /= total_weight

    def score_orderbook(
        self,
        orderbook: Any,
        platform: str = "unknown"
    ) -> Tuple[float, float, float, Dict[str, Any]]:
        """
        评分单个订单簿

        Returns:
            (depth_score, price_score, spread_score, metrics)
        """
        if not orderbook:
            return 0.0, 0.0, 0.0, {}

        best_bid = orderbook.best_bid()
        best_ask = orderbook.best_ask()

        if not best_bid or not best_ask:
            return 0.0, 0.0, 0.0, {}

        bid_price = best_bid.price
        ask_price = best_ask.price
        bid_size = best_bid.size or 0.0
        ask_size = best_ask.size or 0.0

        # 无效订单簿过滤
        if bid_price <= 0 or ask_price <= 0 or bid_price >= ask_price:
            return 0.0, 0.0, 0.0, {}

        # 中间价
        mid_price = (bid_price + ask_price) / 2.0

        # 订单簿带宽内深度（份额）
        band = max(mid_price * self.depth_band, self.min_price_band)
        bids = getattr(orderbook, "bids", []) or []
        asks = getattr(orderbook, "asks", []) or []

        def _depth_within(levels: List[Any], is_bid: bool) -> float:
            depth = 0.0
            for level in levels[: self.depth_levels]:
                price = getattr(level, "price", None)
                size = getattr(level, "size", None)
                if price is None or size is None:
                    continue
                if is_bid:
                    if price >= mid_price - band:
                        depth += size
                else:
                    if price <= mid_price + band:
                        depth += size
            return depth

        bid_depth = _depth_within(bids, True)
        ask_depth = _depth_within(asks, False)
        effective_depth = (bid_depth * ask_depth) ** 0.5 if bid_depth > 0 and ask_depth > 0 else 0.0

        # 深度得分：基于带宽深度（份额）
        if effective_depth < self.min_value_threshold:
            depth_score = 0.0
        else:
            import math
            normalized = min(effective_depth / self.max_value_for_score, 1.0)
            depth_score = 100.0 * math.sqrt(normalized)

        # 订单簿均衡度评分（替代原价格接近度，避免高价档位偏置）
        if bid_depth + ask_depth > 0:
            imbalance = abs(bid_depth - ask_depth) / (bid_depth + ask_depth)
            price_score = 100.0 * (1.0 - imbalance)
        else:
            price_score = 0.0

        # 相对价差评分
        spread = ask_price - bid_price
        if spread < 0:
            spread_score = 0.0
        else:
            # 计算相对价差
            if mid_price > 0.01:
                relative_spread = spread / mid_price
            else:
                relative_spread = spread

            # 相对价差从0到max_relative_spread映射到100到0
            spread_ratio = min(relative_spread / self.max_relative_spread, 1.0)
            spread_score = 100.0 * (1.0 - spread_ratio)

        # 价格区间与价差惩罚：超出合理区间或价差过大降为惩罚性低分
        penalty_factor = 1.0
        if bid_price < 0.05 or bid_price > 0.95 or ask_price < 0.05 or ask_price > 0.95:
            penalty_factor = min(penalty_factor, 0.1)
        if spread > 0.02:
            penalty_factor = min(penalty_factor, 0.3)

        if penalty_factor < 1.0:
            depth_score *= penalty_factor
            price_score *= penalty_factor
            spread_score *= penalty_factor

        metrics = {
            "bid_price": bid_price,
            "ask_price": ask_price,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "mid_price": mid_price,
            "spread": spread,
            "relative_spread": relative_spread if spread >= 0 else None,
            "band": band,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "effective_depth": effective_depth,
        }

        return depth_score, price_score, spread_score, metrics

    def score_market_pair(
        self,
        market_key: str,
        opinion_book: Any,
        poly_book: Any,
    ) -> Optional[LiquidityScore]:
        """
        评分配对市场

        Args:
            market_key: 市场唯一标识
            opinion_book: Opinion订单簿
            poly_book: Polymarket订单簿

        Returns:
            LiquidityScore对象，如果无法评分则返回None
        """
        # 评分 Opinion 订单簿
        opinion_depth, opinion_price, opinion_spread, opinion_metrics = \
            self.score_orderbook(opinion_book, "opinion")

        # 评分 Polymarket 订单簿
        poly_depth, poly_price, poly_spread, poly_metrics = \
            self.score_orderbook(poly_book, "polymarket")

        # 如果两个平台都没有有效订单簿，返回None
        if not opinion_metrics and not poly_metrics:
            return None

        # 计算各平台的综合得分
        opinion_total = (
            self.depth_weight * opinion_depth +
            self.price_weight * opinion_price +
            self.spread_weight * opinion_spread
        )

        poly_total = (
            self.depth_weight * poly_depth +
            self.price_weight * poly_price +
            self.spread_weight * poly_spread
        )

        # 跨平台均衡度：两个平台得分越接近，均衡度越高
        if opinion_total + poly_total > 0:
            balance = 1.0 - abs(opinion_total - poly_total) / (opinion_total + poly_total)
        else:
            balance = 0.0

        # 最终得分：取两个平台的平均值，并考虑均衡度
        # 均衡度作为加成因子（0-20%的加成）
        base_score = (opinion_total + poly_total) / 2.0
        total_score = base_score * (1.0 + 0.2 * balance)

        return LiquidityScore(
            market_key=market_key,
            total_score=total_score,
            opinion_depth_score=opinion_depth,
            opinion_price_score=opinion_price,
            opinion_spread_score=opinion_spread,
            opinion_best_bid=opinion_metrics.get("bid_price"),
            opinion_best_ask=opinion_metrics.get("ask_price"),
            opinion_bid_size=opinion_metrics.get("bid_size"),
            opinion_ask_size=opinion_metrics.get("ask_size"),
            poly_depth_score=poly_depth,
            poly_price_score=poly_price,
            poly_spread_score=poly_spread,
            poly_best_bid=poly_metrics.get("bid_price"),
            poly_best_ask=poly_metrics.get("ask_price"),
            poly_bid_size=poly_metrics.get("bid_size"),
            poly_ask_size=poly_metrics.get("ask_size"),
            cross_platform_balance=balance,
        )

    def rank_markets(
        self,
        scores: List[LiquidityScore],
        top_n: Optional[int] = None,
        bottom_n: Optional[int] = None,
    ) -> Dict[str, List[LiquidityScore]]:
        """
        对市场进行排名

        Args:
            scores: 流动性评分列表
            top_n: 返回流动性最好的前N个市场
            bottom_n: 返回流动性最差的前N个市场

        Returns:
            {"top": [...], "bottom": [...]}
        """
        if not scores:
            return {"top": [], "bottom": []}

        # 按总分排序
        sorted_scores = sorted(scores, key=lambda x: x.total_score, reverse=True)

        result = {}

        if top_n is not None and top_n > 0:
            result["top"] = sorted_scores[:top_n]
        else:
            result["top"] = []

        if bottom_n is not None and bottom_n > 0:
            result["bottom"] = sorted_scores[-bottom_n:]
        else:
            result["bottom"] = []

        return result

    def log_score_summary(self, score: LiquidityScore) -> None:
        """打印评分摘要"""
        logger.info("=" * 80)
        logger.info(f"市场: {score.market_key[:60]}")
        logger.info(f"综合得分: {score.total_score:.2f}/100")

        # 计算Opinion金额深度
        opinion_value = 0.0
        if score.opinion_best_bid and score.opinion_bid_size:
            opinion_value += score.opinion_best_bid * score.opinion_bid_size
        if score.opinion_best_ask and score.opinion_ask_size:
            opinion_value += score.opinion_best_ask * score.opinion_ask_size

        logger.info(f"  Opinion - 深度:{score.opinion_depth_score:.1f} 价格:{score.opinion_price_score:.1f} 价差:{score.opinion_spread_score:.1f}")
        if score.opinion_best_bid and score.opinion_best_ask:
            logger.info(f"           买:{score.opinion_best_bid:.4f}×{score.opinion_bid_size:.0f} 卖:{score.opinion_best_ask:.4f}×{score.opinion_ask_size:.0f} [金额:{opinion_value:.0f}U]")
        else:
            logger.info(f"           无订单簿数据")

        # 计算Polymarket金额深度
        poly_value = 0.0
        if score.poly_best_bid and score.poly_bid_size:
            poly_value += score.poly_best_bid * score.poly_bid_size
        if score.poly_best_ask and score.poly_ask_size:
            poly_value += score.poly_best_ask * score.poly_ask_size

        logger.info(f"  Poly    - 深度:{score.poly_depth_score:.1f} 价格:{score.poly_price_score:.1f} 价差:{score.poly_spread_score:.1f}")
        if score.poly_best_bid and score.poly_best_ask:
            logger.info(f"           买:{score.poly_best_bid:.4f}×{score.poly_bid_size:.0f} 卖:{score.poly_best_ask:.4f}×{score.poly_ask_size:.0f} [金额:{poly_value:.0f}U]")
        else:
            logger.info(f"           无订单簿数据")
        logger.info(f"  跨平台均衡度: {score.cross_platform_balance:.2f}")
        logger.info("=" * 80)
