"""
流动性评分器 - 评估配对市场的流动性质量

评分标准：
1. 订单簿深度（深度越大，分数越高）
2. 价格接近度（价格越接近0.5，分数越高，表示市场更均衡）
3. 买卖价差（价差越小，流动性越好）
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
        price_weight: float = 0.3,  # 价格权重
        spread_weight: float = 0.2,  # 价差权重
        min_value_threshold: float = 50.0,  # 最小金额阈值（USDC）
        max_value_for_score: float = 5000.0,  # 金额评分上限（USDC）
        max_relative_spread: float = 0.2,  # 最大相对价差（20%）
    ):
        self.depth_weight = depth_weight
        self.price_weight = price_weight
        self.spread_weight = spread_weight
        self.min_value_threshold = min_value_threshold
        self.max_value_for_score = max_value_for_score
        self.max_relative_spread = max_relative_spread

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

        # 【改进1】计算金额深度（USDC价值）而非股份深度
        bid_value = bid_size * bid_price
        ask_value = ask_size * ask_price
        total_value = bid_value + ask_value

        # 深度得分：基于金额深度
        if total_value < self.min_value_threshold:
            depth_score = 0.0
        else:
            import math
            normalized = min(total_value / self.max_value_for_score, 1.0)
            depth_score = 100.0 * math.sqrt(normalized)

        # 【改进2】二次函数价格评分，极端价格衰减更快
        mid_price = (bid_price + ask_price) / 2.0
        price_deviation = abs(mid_price - 0.5)
        # 使用二次函数：偏离度越大，惩罚越重
        price_score = 100.0 * (1.0 - (2.0 * price_deviation) ** 2)
        price_score = max(0.0, price_score)

        # 【改进3】极端价格额外惩罚
        if mid_price < 0.1 or mid_price > 0.9:
            extreme_penalty = 0.2  # 仅保留20%
        elif mid_price < 0.2 or mid_price > 0.8:
            extreme_penalty = 0.5  # 保留50%
        else:
            extreme_penalty = 1.0  # 无惩罚

        price_score *= extreme_penalty

        # 【改进4】相对价差评分
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

        metrics = {
            "bid_price": bid_price,
            "ask_price": ask_price,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "bid_value": bid_value,
            "ask_value": ask_value,
            "total_value": total_value,
            "mid_price": mid_price,
            "spread": spread,
            "relative_spread": relative_spread if spread >= 0 else None,
            "extreme_penalty": extreme_penalty,
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

        # 如果两个平台都没有流动性，返回None
        if opinion_depth == 0 and poly_depth == 0:
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

        # 【改进】提取极端价格惩罚因子，应用到总分
        opinion_penalty = opinion_metrics.get("extreme_penalty", 1.0)
        poly_penalty = poly_metrics.get("extreme_penalty", 1.0)
        # 取两个平台中更严格的惩罚（较小的值）
        extreme_penalty = min(opinion_penalty, poly_penalty)

        # 应用极端价格惩罚到总分
        opinion_total *= extreme_penalty
        poly_total *= extreme_penalty

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
