"""
跨平台套利系统核心模块
提供 Opinion 和 Polymarket 之间的套利检测和执行功能
"""

__version__ = "1.0.0"
__author__ = "Arbitrage System"

# 导入已实现的模块
from .models import (
    OrderBookLevel,
    OrderBookSnapshot,
    MarketMatch,
    ArbitrageOpportunity,
    LiquidityOrderState,
)
from .config import ArbitrageConfig
from .clients import PlatformClients
from .fees import FeeCalculator
from .websocket_manager import (
    WebSocketManager,
    PolymarketWebSocket,
    OpinionWebSocket,
    OrderBookUpdate,
)

# 待实现的模块（已注释）：
# from .orderbook import OrderBookManager
# from .order_execution import OrderExecutor
# from .profitability import ProfitabilityAnalyzer

__all__ = [
    # 数据模型
    "OrderBookLevel",
    "OrderBookSnapshot",
    "MarketMatch",
    "ArbitrageOpportunity",
    "LiquidityOrderState",
    # 核心模块
    "ArbitrageConfig",
    "PlatformClients",
    "FeeCalculator",
    # WebSocket
    "WebSocketManager",
    "PolymarketWebSocket",
    "OpinionWebSocket",
    "OrderBookUpdate",
    # 待实现模块（已注释）：
    # "OrderBookManager",
    # "OrderExecutor",
    # "ProfitabilityAnalyzer",
]
