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
        min_depth_threshold: float = 10.0,  # 最小深度阈值
        max_depth_for_score: float = 1000.0,  # 深度评分上限
        max_spread_for_score: float = 0.1,  # 价差评分上限
    ):
        self.depth_weight = depth_weight
        self.price_weight = price_weight
        self.spread_weight = spread_weight
        self.min_depth_threshold = min_depth_threshold
        self.max_depth_for_score = max_depth_for_score
        self.max_spread_for_score = max_spread_for_score

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

        # 1. 深度得分：对数缩放，避免极端值主导
        total_depth = bid_size + ask_size
        if total_depth < self.min_depth_threshold:
            depth_score = 0.0
        else:
            # 使用对数缩放，将 [min_threshold, max_depth] 映射到 [0, 100]
            import math
            normalized = min(total_depth / self.max_depth_for_score, 1.0)
            depth_score = 100.0 * math.sqrt(normalized)  # 平方根缩放，更平滑

        # 2. 价格得分：价格越接近0.5，得分越高
        mid_price = (bid_price + ask_price) / 2.0
        # 距离0.5的偏离度，范围[0, 0.5]
        price_deviation = abs(mid_price - 0.5)
        # 转换为得分：偏离度0时100分，偏离度0.5时0分
        price_score = 100.0 * (1.0 - 2.0 * price_deviation)
        price_score = max(0.0, price_score)

        # 3. 价差得分：价差越小，得分越高
        spread = ask_price - bid_price
        if spread < 0:
            spread_score = 0.0
        else:
            # 价差从0到max_spread映射到100到0
            spread_ratio = min(spread / self.max_spread_for_score, 1.0)
            spread_score = 100.0 * (1.0 - spread_ratio)

        metrics = {
            "bid_price": bid_price,
            "ask_price": ask_price,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "mid_price": mid_price,
            "spread": spread,
            "total_depth": total_depth,
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
        logger.info(f"  Opinion - 深度:{score.opinion_depth_score:.1f} 价格:{score.opinion_price_score:.1f} 价差:{score.opinion_spread_score:.1f}")
        logger.info(f"           买:{score.opinion_best_bid:.4f}({score.opinion_bid_size:.0f}) 卖:{score.opinion_best_ask:.4f}({score.opinion_ask_size:.0f})")
        logger.info(f"  Poly    - 深度:{score.poly_depth_score:.1f} 价格:{score.poly_price_score:.1f} 价差:{score.poly_spread_score:.1f}")
        logger.info(f"           买:{score.poly_best_bid:.4f}({score.poly_bid_size:.0f}) 卖:{score.poly_best_ask:.4f}({score.poly_ask_size:.0f})")
        logger.info(f"  跨平台均衡度: {score.cross_platform_balance:.2f}")
        logger.info("=" * 80)
