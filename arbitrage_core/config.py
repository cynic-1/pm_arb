"""
配置管理模块
集中管理所有配置参数，支持环境变量
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ArbitrageConfig:
    """套利系统配置"""

    # ==================== 平台配置 ====================
    # Opinion 配置
    opinion_host: str = field(default_factory=lambda: os.getenv('OP_HOST', 'https://proxy.opinion.trade:8443'))
    opinion_api_key: Optional[str] = field(default_factory=lambda: os.getenv('OP_API_KEY'))
    opinion_chain_id: int = field(default_factory=lambda: int(os.getenv('OP_CHAIN_ID', '56')))
    opinion_rpc_url: Optional[str] = field(default_factory=lambda: os.getenv('OP_RPC_URL'))
    opinion_private_key: Optional[str] = field(default_factory=lambda: os.getenv('OP_PRIVATE_KEY'))
    opinion_multi_sig_addr: Optional[str] = field(default_factory=lambda: os.getenv('OP_MULTI_SIG_ADDRESS'))

    # Polymarket 配置
    polymarket_host: str = "https://clob.polymarket.com"
    polymarket_chain_id: int = 137
    polymarket_private_key: Optional[str] = field(default_factory=lambda: os.getenv("PM_KEY"))
    polymarket_funder: Optional[str] = field(default_factory=lambda: os.getenv("PM_FUNDER"))
    gamma_api: str = field(default_factory=lambda: os.getenv("GAMMA_API", "https://gamma-api.polymarket.com"))

    # ==================== 订单簿配置 ====================
    orderbook_batch_size: int = field(default_factory=lambda: max(1, int(os.getenv("ORDERBOOK_BATCH_SIZE", "20"))))
    polymarket_books_chunk: int = field(default_factory=lambda: max(1, int(os.getenv("POLYMARKET_BOOKS_BATCH", "25"))))
    opinion_orderbook_workers: int = field(default_factory=lambda: max(1, int(os.getenv("OPINION_ORDERBOOK_WORKERS", "5"))))
    opinion_max_rps: float = field(default_factory=lambda: float(os.getenv("OPINION_MAX_RPS", "15")))
    max_orderbook_skew: float = field(default_factory=lambda: max(0.0, float(os.getenv("MAX_ORDERBOOK_SKEW", "3.0"))))
    opinion_orderbook_timeout: Optional[float] = None
    polymarket_orderbook_timeout: Optional[float] = None

    # ==================== 下单配置 ====================
    order_max_retries: int = field(default_factory=lambda: max(1, int(os.getenv("ORDER_MAX_RETRIES", "3"))))
    order_retry_delay: float = field(default_factory=lambda: max(0.0, float(os.getenv("ORDER_RETRY_DELAY", "1.0"))))

    # ==================== 价格和手续费配置 ====================
    price_decimals: int = 3
    opinion_min_fee: float = field(default_factory=lambda: max(0.0, float(os.getenv("OPINION_MIN_FEE", "0.5"))))

    # ==================== 盈利性配置 ====================
    roi_reference_size: float = field(default_factory=lambda: max(1.0, float(os.getenv("ROI_BASE_SIZE", "200"))))
    seconds_per_year: float = field(default_factory=lambda: float(os.getenv("SECONDS_PER_YEAR", str(365 * 24 * 60 * 60))))
    min_annualized_percent: float = field(default_factory=lambda: float(os.getenv("MIN_ANNUALIZED_PERCENT", "18.0")))

    # ==================== 即时执行配置 ====================
    immediate_exec_enabled: bool = field(default_factory=lambda: os.getenv("IMMEDIATE_EXEC_ENABLED", "1") not in {"0", "false", "False"})
    immediate_min_percent: float = field(default_factory=lambda: float(os.getenv("IMMEDIATE_MIN_PERCENT", "2.0")))
    immediate_max_percent: float = field(default_factory=lambda: float(os.getenv("IMMEDIATE_MAX_PERCENT", "50.0")))
    immediate_order_size: float = field(default_factory=lambda: float(os.getenv("IMMEDIATE_ORDER_SIZE", "200")))

    # ==================== 流动性提供配置 ====================
    liquidity_min_annualized: float = field(default_factory=lambda: float(os.getenv("LIQUIDITY_MIN_ANNUALIZED_PERCENT", "20.0")))
    liquidity_min_size: float = field(default_factory=lambda: max(1.0, float(os.getenv("LIQUIDITY_MIN_SIZE", "100"))))
    liquidity_target_size: float = field(default_factory=lambda: max(100.0, float(os.getenv("LIQUIDITY_TARGET_SIZE", "250"))))
    max_liquidity_orders: int = field(default_factory=lambda: max(1, int(os.getenv("LIQUIDITY_MAX_ACTIVE", "20"))))
    liquidity_price_tolerance: float = field(default_factory=lambda: max(0.0, float(os.getenv("LIQUIDITY_PRICE_TOLERANCE", "0.003"))))
    liquidity_status_poll_interval: float = field(default_factory=lambda: max(0.5, float(os.getenv("LIQUIDITY_STATUS_POLL_INTERVAL", "1.5"))))
    liquidity_loop_interval: float = field(default_factory=lambda: max(5.0, float(os.getenv("LIQUIDITY_LOOP_INTERVAL", "12"))))
    liquidity_requote_increment: float = field(default_factory=lambda: max(0.0, float(os.getenv("LIQUIDITY_REQUOTE_INCREMENT", "0.0"))))
    liquidity_wait_timeout: float = field(default_factory=lambda: max(0.0, float(os.getenv("LIQUIDITY_WAIT_TIMEOUT", "0"))))
    liquidity_trade_poll_interval: float = field(default_factory=lambda: max(0.5, float(os.getenv("LIQUIDITY_TRADE_POLL_INTERVAL", "2.0"))))
    liquidity_trade_limit: int = field(default_factory=lambda: max(10, int(os.getenv("LIQUIDITY_TRADE_LIMIT", "40"))))
    liquidity_debug: bool = field(default_factory=lambda: os.getenv("LIQUIDITY_DEBUG", "1") not in {"0", "false", "False"})

    # ==================== 监控配置 ====================
    account_monitor_interval: float = field(default_factory=lambda: float(os.getenv("ACCOUNT_MONITOR_INTERVAL", "3.0")))
    order_status_fallback_after: Optional[float] = None

    # ==================== 循环配置 ====================
    pro_loop_interval: float = field(default_factory=lambda: max(0.0, float(os.getenv("PRO_LOOP_INTERVAL", "90"))))
    pending_exec_timeout: float = field(default_factory=lambda: float(os.getenv("PENDING_EXEC_TIMEOUT", "300")))
    pending_poll_interval: float = field(default_factory=lambda: float(os.getenv("PENDING_POLL_INTERVAL", "5")))

    # ==================== 日志配置 ====================
    log_dir: str = "logs"
    arbitrage_log_pointer: Optional[str] = field(default_factory=lambda: os.getenv("ARBITRAGE_LOG_POINTER"))

    def __post_init__(self):
        """初始化后处理，确保配置合理性"""
        # 确保流动性目标大小 >= 最小大小
        if self.liquidity_target_size < self.liquidity_min_size:
            self.liquidity_target_size = self.liquidity_min_size

        # 解析 order_status_fallback_after
        fallback_env = os.getenv("ORDER_STATUS_FALLBACK_AFTER")
        if fallback_env:
            try:
                self.order_status_fallback_after = float(fallback_env)
            except ValueError:
                pass

        # 解析订单簿超时
        timeout_env = os.getenv("OPINION_ORDERBOOK_TIMEOUT")
        if timeout_env:
            try:
                self.opinion_orderbook_timeout = float(timeout_env)
            except ValueError:
                pass

        timeout_env = os.getenv("POLYMARKET_ORDERBOOK_TIMEOUT")
        if timeout_env:
            try:
                self.polymarket_orderbook_timeout = float(timeout_env)
            except ValueError:
                pass

    @property
    def polymarket_trading_enabled(self) -> bool:
        """Polymarket 交易是否启用"""
        return bool(self.polymarket_private_key)

    def display_summary(self) -> None:
        """显示配置摘要"""
        print("📋 配置摘要:")
        print(f"  - Opinion: {self.opinion_host}")
        print(f"  - Polymarket: {self.polymarket_host}")
        print(f"  - 订单簿批处理大小: {self.orderbook_batch_size}")
        print(f"  - Opinion 最大 RPS: {self.opinion_max_rps}")
        print(f"  - 最小年化收益率: {self.min_annualized_percent}%")

        if self.immediate_exec_enabled:
            print(f"  - 即时执行: 启用 (利润率 {self.immediate_min_percent}%-{self.immediate_max_percent}%)")
        else:
            print(f"  - 即时执行: 禁用")

        if self.polymarket_trading_enabled:
            print(f"  - Polymarket 交易: 启用")
        else:
            print(f"  - Polymarket 交易: 禁用 (只读模式)")
