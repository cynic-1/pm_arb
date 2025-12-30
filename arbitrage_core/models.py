"""
数据模型定义模块
包含所有套利系统使用的数据类
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional
import time


@dataclass
class OrderBookLevel:
    """标准化的订单簿档位"""
    price: float
    size: float


@dataclass
class OrderBookSnapshot:
    """订单簿快照，包含前 N 档买卖单"""
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    source: str
    token_id: str
    timestamp: float

    def best_bid(self) -> Optional[OrderBookLevel]:
        """获取最优买单"""
        return self.bids[0] if self.bids else None

    def best_ask(self) -> Optional[OrderBookLevel]:
        """获取最优卖单"""
        return self.asks[0] if self.asks else None


@dataclass
class MarketMatch:
    """匹配的市场对"""
    question: str  # 市场问题

    # Opinion 市场信息
    opinion_market_id: int
    opinion_yes_token: str
    opinion_no_token: str

    # Polymarket 市场信息
    polymarket_condition_id: str
    polymarket_yes_token: str
    polymarket_no_token: str
    polymarket_slug: str

    # 相似度分数
    similarity_score: float = 1.0
    cutoff_at: Optional[int] = None

    # Polymarket 市场属性（用于避免不必要的API请求）
    polymarket_neg_risk: bool = False  # 是否为 neg-risk 市场


@dataclass
class ArbitrageOpportunity:
    """套利机会"""
    market_match: MarketMatch

    # 套利类型
    strategy: str  # "opinion_yes_poly_no" 或 "poly_yes_opinion_no"

    # Opinion 价格
    opinion_yes_bid: Optional[float] = None
    opinion_yes_ask: Optional[float] = None
    opinion_no_bid: Optional[float] = None
    opinion_no_ask: Optional[float] = None

    # Polymarket 价格
    poly_yes_bid: Optional[float] = None
    poly_yes_ask: Optional[float] = None
    poly_no_bid: Optional[float] = None
    poly_no_ask: Optional[float] = None

    # 套利计算
    cost: float = 0.0  # 总成本
    profit: float = 0.0  # 潜在利润
    profit_rate: float = 0.0  # 利润率

    timestamp: str = ""
    opinion_yes_book: Optional[OrderBookSnapshot] = None
    opinion_no_book: Optional[OrderBookSnapshot] = None
    poly_yes_book: Optional[OrderBookSnapshot] = None
    poly_no_book: Optional[OrderBookSnapshot] = None


@dataclass
class LiquidityOrderState:
    """跟踪 Opinion 流动性挂单及其对冲状态"""
    key: str
    order_id: str
    match: MarketMatch
    opinion_token: str
    opinion_price: float
    opinion_side: Any
    opinion_order_size: float
    effective_size: float
    hedge_token: str
    hedge_side: Any
    hedge_price: float
    status: str = "pending"  # 新订单初始状态为 pending，与 Opinion API 一致
    filled_size: float = 0.0
    hedged_size: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_roi: Optional[float] = None
    last_annualized: Optional[float] = None
    last_reported_status: Optional[str] = None
    last_status_log: float = 0.0
    last_status_check: float = 0.0
