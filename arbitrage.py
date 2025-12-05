"""
跨平台套利检测器 - Opinion vs Polymarket
检测在两个平台之间同一市场的套利机会
套利条件: Opinion_YES_Price + Polymarket_NO_Price < 1
         或 Polymarket_YES_Price + Opinion_NO_Price < 1
"""

from concurrent.futures import ThreadPoolExecutor
import logging
import os
import json
import time
import argparse
import threading
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict
from datetime import datetime
from dotenv import load_dotenv


# Opinion SDK
from opinion_clob_sdk import Client as OpinionClient
from opinion_clob_sdk.model import TopicStatusFilter, TopicType

# Polymarket SDK
from py_clob_client.client import ClobClient
import requests
from py_clob_client.clob_types import OpenOrderParams

# 加载环境变量
load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Replace builtin print() with a logger-backed function that writes to a
# timestamped log file (filename includes time suffix) and prints to stdout.
# Logs include timestamp and caller filename:line via the logging format.
import builtins as _builtins

def _replace_print_with_logger(log_dir: str = "logs"):
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logfile = os.path.join(log_dir, f"test_arb_{ts}.log")

    # Reconfigure root handlers so we have a file handler with desired format
    for h in list(logging.root.handlers):
        logging.root.removeHandler(h)

    fmt = logging.Formatter('%(asctime)s %(filename)s:%(lineno)d %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    fh = logging.FileHandler(logfile, encoding='utf-8')
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)

    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(fh)
    logging.root.addHandler(sh)

    _logger = logging.getLogger(__name__)

    def _print(*args, sep=' ', end='\n', file=None, flush=False, level=logging.INFO):
        # Build message similar to print
        try:
            msg = sep.join(str(a) for a in args)
        except Exception:
            # Fallback if objects cannot be converted normally
            msg = ' '.join([repr(a) for a in args])

        # Use stacklevel so logging shows the original caller file/line.
        # Wrapper adds one extra frame, so use stacklevel=3 to point to caller.
        try:
            _logger.log(level, msg, stacklevel=3)
        except TypeError:
            # Older Python without stacklevel support: include caller info manually
            try:
                import inspect
                frame = inspect.currentframe()
                if frame is not None:
                    caller = frame.f_back.f_back
                    if caller is not None:
                        info = f"{os.path.basename(caller.f_code.co_filename)}:{caller.f_lineno} "
                        _logger.log(level, info + msg)
                        return
            except Exception:
                pass
            _logger.log(level, msg)

    # Override builtin print globally in this module/runtime
    _builtins.print = _print


# Install the print -> logger replacement immediately
_replace_print_with_logger()

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
        return self.bids[0] if self.bids else None

    def best_ask(self) -> Optional[OrderBookLevel]:
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


class CrossPlatformArbitrage:
    """跨平台套利检测器"""
    
    def __init__(self):
        """初始化两个平台的客户端"""
        
        # Opinion 客户端
        print("🔧 初始化 Opinion 客户端...")
        self.opinion_client = OpinionClient(
            host=os.getenv('OP_HOST', 'https://proxy.opinion.trade:8443'),
            apikey=os.getenv('OP_API_KEY'),
            chain_id=int(os.getenv('OP_CHAIN_ID', '56')),
            rpc_url=os.getenv('OP_RPC_URL'),
            private_key=os.getenv('OP_PRIVATE_KEY'),
            multi_sig_addr=os.getenv('OP_MULTI_SIG_ADDRESS'),
        )
        
        # Polymarket 客户端（参考 place_order.py）
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
        else:
            # 只读模式
            self.polymarket_client = ClobClient(HOST)
            print("READ-ONLY MODE: Polymarket client initialized without private key.\n")
        
        self.gamma_api = os.getenv("GAMMA_API", "https://gamma-api.polymarket.com")
        self.polymarket_trading_enabled = bool(PRIVATE_KEY)
        self.price_decimals = 3  # keep all prices at three decimal places

        # 下单重试配置
        try:
            self.order_max_retries = max(1, int(os.getenv("ORDER_MAX_RETRIES", "3")))
        except Exception:
            self.order_max_retries = 3
        try:
            self.order_retry_delay = max(0.0, float(os.getenv("ORDER_RETRY_DELAY", "1.0")))
        except Exception:
            self.order_retry_delay = 1.0
        
        # 缓存
        self.opinion_markets: List[Dict[str, Any]] = []
        self.polymarket_markets: List[Dict[str, Any]] = []
        self.market_matches: List[MarketMatch] = []

        # 账户监控
        self._account_state_lock = threading.Lock()
        self._monitor_control_lock = threading.Lock()
        self._monitor_stop_event = threading.Event()
        self._opinion_monitor_thread: Optional[threading.Thread] = None
        self._polymarket_monitor_thread: Optional[threading.Thread] = None
        self._opinion_account_state: Dict[str, Any] = {}
        self._polymarket_account_state: Dict[str, Any] = {}
        self._account_monitors_started = False
        self.account_monitor_interval = float(os.getenv("ACCOUNT_MONITOR_INTERVAL", "3.0"))
        self._opinion_refresh_event = threading.Event()
        self._polymarket_refresh_event = threading.Event()
        self._opinion_state_updated = threading.Event()
        self._polymarket_state_updated = threading.Event()
        # 即时执行配置（可通过环境变量设置）
        # 表示当扫描到的套利机会的利润率在[min,max]（百分比）之间时，立即用新线程执行
        self.immediate_exec_enabled = os.getenv("IMMEDIATE_EXEC_ENABLED", "1") not in {"0", "false", "False"}
        try:
            self.immediate_min_percent = float(os.getenv("IMMEDIATE_MIN_PERCENT", "3.0"))
        except Exception:
            self.immediate_min_percent = 3.0
        try:
            self.immediate_max_percent = float(os.getenv("IMMEDIATE_MAX_PERCENT", "20.0"))
        except Exception:
            self.immediate_max_percent = 20.0
        
        # 显示即时执行配置
        if self.immediate_exec_enabled:
            print(f"⚡ 即时执行已启用: 利润率在 [{self.immediate_min_percent:.2f}%, {self.immediate_max_percent:.2f}%] 范围内将自动执行")
        else:
            print("🚫 即时执行已禁用")

        # 跟踪启动的即时执行线程（仅用于信息/清理）
        self._active_exec_threads: List[threading.Thread] = []
        fallback_env = os.getenv("ORDER_STATUS_FALLBACK_AFTER")
        self.order_status_fallback_after: Optional[float] = None
        if fallback_env:
            try:
                self.order_status_fallback_after = float(fallback_env)
            except ValueError:
                print("⚠️ ORDER_STATUS_FALLBACK_AFTER 环境变量不是有效数字，将忽略。")

        try:
            self.roi_reference_size = max(1.0, float(os.getenv("ROI_BASE_SIZE", "200")))
        except Exception:
            self.roi_reference_size = 200.0
        try:
            self.seconds_per_year = float(os.getenv("SECONDS_PER_YEAR", str(365 * 24 * 60 * 60)))
        except Exception:
            self.seconds_per_year = float(365 * 24 * 60 * 60)
        try:
            self.opinion_min_fee = max(0.0, float(os.getenv("OPINION_MIN_FEE", "0.5")))
        except Exception:
            self.opinion_min_fee = 0.5
        try:
            self.min_annualized_percent = float(os.getenv("MIN_ANNUALIZED_PERCENT", "18.0"))
        except Exception:
            self.min_annualized_percent = 18.0
        
        print("✅ 初始化完成!\n")
    
    
    # ==================== Opinion 手续费计算 ====================
    def _round_price(self, value: Optional[float]) -> Optional[float]:
        """Round a numeric price to the configured number of decimal places."""
        if value is None:
            return None
        try:
            return round(float(value), self.price_decimals)
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
            print(f"💰 Opinion 手续费计算: price={price:.3f}, fee_rate={fee_rate:.6f}, "
                  f"预估手续费=${Fee_provisional:.4f} (百分比手续费)")
        else:
            # 适用最低手续费 $0.5
            A_order = target_amount + 0.5 / price
            print(f"💰 Opinion 手续费计算: price={price:.3f}, fee_rate={fee_rate:.6f}, "
                  f"预估手续费=${Fee_provisional:.4f} -> 最低手续费 $0.5")
        
        print(f"   目标数量: {target_amount:.2f} -> 修正后下单数量: {A_order:.2f}")
        return A_order
    
    def calculate_opinion_effective_amount(self, price: float, order_amount: float) -> float:
        """
        计算 Opinion 订单成交后实际得到的数量 (扣除手续费)
        
        关系: effective_amount = order_amount - fee / price
        
        Args:
            price: 订单价格
            order_amount: 下单数量
            
        Returns:
            实际得到的数量 (扣除手续费后)
        """
        # 计算手续费率
        fee_rate = self.calculate_opinion_fee_rate(price)
        
        # 计算订单价值
        value = price * order_amount
        
        # 计算手续费 (至少 $0.5)
        fee = max(value * fee_rate, 0.5)
        
        # 计算实际得到的数量
        effective_amount = order_amount - fee / price
        
        print(f"💰 Opinion 实际数量计算: 订单数量={order_amount:.2f}, "
              f"手续费=${fee:.4f}, 实际数量={effective_amount:.2f}")
        
        return effective_amount
    
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

    def _place_opinion_order_with_retries(self, order: Any, context: str = "") -> Tuple[bool, Optional[Any]]:
        """Opinion 下单带重试，返回 (success, result)。"""
        prefix = f"[{context}] " if context else ""
        last_result: Optional[Any] = None
        for attempt in range(1, self.order_max_retries + 1):
            try:
                result = self.opinion_client.place_order(order)
                last_result = result
                if getattr(result, "errno", 0) == 0:
                    return True, result
                err_msg = getattr(result, "errmsg", "unknown error")
                print(f"⚠️ {prefix}Opinion 下单失败 (尝试 {attempt}/{self.order_max_retries}): {err_msg}")
            except Exception as exc:
                print(f"⚠️ {prefix}Opinion 下单异常 (尝试 {attempt}/{self.order_max_retries}): {exc}")
                last_result = None
            if attempt < self.order_max_retries:
                time.sleep(self.order_retry_delay)
        return False, last_result

    def _place_polymarket_order_with_retries(
        self,
        order_args: Any,
        order_type: Any,
        context: str = ""
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Polymarket 下单带重试，返回 (success, result)。"""
        prefix = f"[{context}] " if context else ""
        last_result: Optional[Dict[str, Any]] = None
        for attempt in range(1, self.order_max_retries + 1):
            try:
                signed_order = self.polymarket_client.create_order(order_args)
                result = self.polymarket_client.post_order(signed_order, order_type)
                last_result = result if isinstance(result, dict) else None
                error_msg: Optional[str] = None
                if isinstance(result, dict):
                    if result.get("success") is False:
                        error_msg = result.get("message") or result.get("error")
                    elif result.get("error"):
                        error_msg = result.get("error")
                if not error_msg:
                    return True, result
                print(f"⚠️ {prefix}Polymarket 下单失败 (尝试 {attempt}/{self.order_max_retries}): {error_msg}")
            except Exception as exc:
                print(f"⚠️ {prefix}Polymarket 下单异常 (尝试 {attempt}/{self.order_max_retries}): {exc}")
                last_result = None
            if attempt < self.order_max_retries:
                time.sleep(self.order_retry_delay)
        return False, last_result
    
    
    # ==================== 账户监控 ====================
    def _extract_from_entry(self, entry: Any, candidate_keys: List[str]) -> Optional[Any]:
        """从对象或字典中提取字段"""
        if entry is None:
            return None
        if isinstance(entry, dict):
            for key in candidate_keys:
                if key in entry:
                    return entry[key]
        else:
            for key in candidate_keys:
                if hasattr(entry, key):
                    return getattr(entry, key)
        return None

    def _to_float(self, value: Any) -> Optional[float]:
        """安全地将值转换为 float"""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            try:
                return float(str(value))
            except (TypeError, ValueError):
                return None

    def _to_int(self, value: Any) -> Optional[int]:
        """安全地将值转换为 int"""
        if value is None:
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _format_levels(self, snapshot: Optional[OrderBookSnapshot]) -> str:
        """用于日志的档位摘要"""
        if not snapshot:
            return "n/a"
        best_bid = snapshot.best_bid()
        best_ask = snapshot.best_ask()
        bid_size = best_bid.size if (best_bid and best_bid.size is not None) else 0.0
        ask_size = best_ask.size if (best_ask and best_ask.size is not None) else 0.0
        bid_text = f"bid {bid_size:.2f}"
        ask_text = f"ask {ask_size:.2f}"
        return f"({bid_text}/{ask_text})"

    def _calculate_opinion_cost_per_token(self, price: Optional[float], size_tokens: float) -> Optional[float]:
        """计算在 Opinion 上获取给定净数量时的单位成本（包含手续费）。"""
        rounded_price = self._round_price(price)
        if rounded_price is None or rounded_price <= 0:
            return None
        size_tokens = max(size_tokens, 1e-6)
        fee_rate = self.calculate_opinion_fee_rate(rounded_price)
        if fee_rate >= 0.999:
            return None

        order_amount = size_tokens / (1.0 - fee_rate)
        trade_value = rounded_price * order_amount
        percentage_fee = trade_value * fee_rate

        if percentage_fee >= self.opinion_min_fee:
            effective_price = rounded_price / (1.0 - fee_rate)
        else:
            effective_price = rounded_price + (self.opinion_min_fee / size_tokens)

        return self._round_price(effective_price)

    def _compute_effective_price(self, platform: str, price: Optional[float], size_tokens: float) -> Optional[float]:
        """根据平台类型返回考虑手续费后的报价。"""
        if price is None:
            return None
        if platform == 'opinion':
            return self._calculate_opinion_cost_per_token(price, size_tokens)
        return self._round_price(price)

    def _compute_annualized_rate(self, roi_decimal: Optional[float], cutoff_at: Optional[int]) -> Optional[float]:
        """根据距结算时间计算年化收益率（简单线性外推）。"""
        if roi_decimal is None or cutoff_at is None:
            return None
        seconds_remaining = float(cutoff_at) - time.time()
        if seconds_remaining <= 0:
            return None
        annualized_decimal = roi_decimal * (self.seconds_per_year / seconds_remaining)
        return annualized_decimal * 100.0

    def _compute_profitability_metrics(
        self,
        match: MarketMatch,
        first_platform: str,
        first_price: Optional[float],
        second_platform: str,
        second_price: Optional[float],
        min_size: Optional[float],
    ) -> Optional[Dict[str, float]]:
        """计算包含手续费的成本、收益率及年化收益率。"""
        assumed_size = max(self.roi_reference_size, (min_size or 0.0))
        eff_first = self._compute_effective_price(first_platform, first_price, assumed_size)
        eff_second = self._compute_effective_price(second_platform, second_price, assumed_size)
        if eff_first is None or eff_second is None:
            return None

        total_cost = self._round_price(eff_first + eff_second)
        if total_cost is None or total_cost <= 0:
            return None

        profit = 1.0 - total_cost
        profit_rate_decimal = profit / total_cost
        profit_rate_pct = profit_rate_decimal * 100.0
        annualized_pct = self._compute_annualized_rate(profit_rate_decimal, match.cutoff_at)

        return {
            'cost': total_cost,
            'profit_rate': profit_rate_pct,
            'annualized_rate': annualized_pct,
            'assumed_size': assumed_size,
        }

    # ==================== 3. 获取订单簿 ====================
    
    def get_opinion_orderbook(self, token_id: str, depth: int = 5, max_retries: int = 1, timeout: Optional[float] = None) -> Optional[OrderBookSnapshot]:
        """获取 Opinion 订单簿前 N 档含价格和数量
        
        Args:
            token_id: Token ID
            depth: 订单簿深度
            max_retries: 最大重试次数（默认1次）
            timeout: 超时时间（秒），默认从环境变量 OPINION_ORDERBOOK_TIMEOUT 读取，未设置则无超时
            
        Returns:
            订单簿快照，失败返回 None
        """
        retry_delay = float(os.getenv("OPINION_RETRY_DELAY", "1.0"))  # 重试间隔（秒）
        if timeout is None:
            timeout_env = os.getenv("OPINION_ORDERBOOK_TIMEOUT")
            if timeout_env:
                try:
                    timeout = float(timeout_env)
                except ValueError:
                    timeout = None
        
        def _fetch_orderbook():
            response = self.opinion_client.get_orderbook(token_id)
            logger.info(f"Opinion order book for {token_id}")
            if response.errno != 0:
                raise Exception(f"Opinion API 返回错误码 {response.errno}")
            book = response.result
            bids = self._normalize_opinion_levels(getattr(book, "bids", []), depth, reverse=True)
            asks = self._normalize_opinion_levels(getattr(book, "asks", []), depth, reverse=False)
            return OrderBookSnapshot(
                bids=bids,
                asks=asks,
                source="opinion",
                token_id=token_id,
                timestamp=time.time(),
            )
        
        for attempt in range(max_retries):
            try:
                if timeout is not None:
                    # 使用 ThreadPoolExecutor 实现超时控制
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(_fetch_orderbook)
                        try:
                            result = future.result(timeout=timeout)
                            return result
                        except TimeoutError:
                            print(f"⏱️ Opinion 订单簿获取超时 ({token_id[:20]}...), 超时时间={timeout}s")
                            if attempt < max_retries - 1:
                                print(f"   🔄 第 {attempt + 1}/{max_retries} 次尝试")
                                time.sleep(retry_delay)
                                continue
                            return None
                else:
                    # 无超时限制
                    return _fetch_orderbook()
                    
            except KeyboardInterrupt:
                raise  # 允许用户中断
            except Exception as exc:
                error_msg = str(exc)
                is_retriable = "Request exception" in error_msg or "timeout" in error_msg.lower() or "connection" in error_msg.lower() or "timed out" in error_msg.lower()
                
                if is_retriable and attempt < max_retries - 1:
                    print(f"⚠️ Opinion 订单簿获取失败 ({token_id[:20]}...), 第 {attempt + 1}/{max_retries} 次尝试: {exc}")
                    print(f"   ⏳ 等待 {retry_delay}s 后重试...")
                    time.sleep(retry_delay)
                else:
                    print(f"❌ 获取 Opinion 订单簿失败 ({token_id[:20]}...), 已重试 {attempt + 1} 次: {exc}")
                    return None
        
        return None

    def _normalize_opinion_levels(
        self,
        raw_levels: Any,
        depth: int,
        reverse: bool,
    ) -> List[OrderBookLevel]:
        levels: List[OrderBookLevel] = []
        if not raw_levels:
            return levels
        sorted_levels = sorted(
            raw_levels,
            key=lambda x: float(getattr(x, "price", 0.0)),
            reverse=reverse,
        )
        for entry in sorted_levels[:depth]:
            price = self._round_price(self._to_float(getattr(entry, "price", None)))
            size = self._to_float(
                getattr(entry, "size", None)
                or getattr(entry, "quantity", None)
                or getattr(entry, "maker_amount", None)
                or getattr(entry, "base_amount", None)
                or getattr(entry, "amount", None)
                or getattr(entry, "makerAmountInBaseToken", None)
            )
            if price is None or size is None:
                continue
            # 价格精度控制：统一保留三位小数
            levels.append(OrderBookLevel(price=price, size=size))
        return levels

    def get_polymarket_orderbook(self, token_id: str, depth: int = 5, max_retries: int = 1, timeout: Optional[float] = None) -> Optional[OrderBookSnapshot]:
        """获取 Polymarket 订单簿前 N 档含价格和数量
        
        Args:
            token_id: Token ID
            depth: 订单簿深度
            max_retries: 最大重试次数（默认1次）
            timeout: 超时时间（秒），默认从环境变量 POLYMARKET_ORDERBOOK_TIMEOUT 读取，未设置则无超时
            
        Returns:
            订单簿快照，失败返回 None
        """
        retry_delay = float(os.getenv("POLYMARKET_RETRY_DELAY", "1.0"))  # 重试间隔（秒）
        if timeout is None:
            timeout_env = os.getenv("POLYMARKET_ORDERBOOK_TIMEOUT")
            if timeout_env:
                try:
                    timeout = float(timeout_env)
                except ValueError:
                    timeout = None
        
        def _fetch_orderbook():
            book = self.polymarket_client.get_order_book(token_id)
            logger.info(f"Polymarket order book for {token_id}")
            if not book:
                raise Exception("Polymarket 返回空订单簿")
            bids = self._normalize_polymarket_levels(getattr(book, "bids", []), depth, reverse=True)
            asks = self._normalize_polymarket_levels(getattr(book, "asks", []), depth, reverse=False)
            return OrderBookSnapshot(
                bids=bids,
                asks=asks,
                source="polymarket",
                token_id=token_id,
                timestamp=time.time(),
            )
        
        for attempt in range(max_retries):
            try:
                if timeout is not None:
                    # 使用 ThreadPoolExecutor 实现超时控制
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(_fetch_orderbook)
                        try:
                            result = future.result(timeout=timeout)
                            return result
                        except TimeoutError:
                            print(f"⏱️ Polymarket 订单簿获取超时 ({token_id[:20]}...), 超时时间={timeout}s")
                            if attempt < max_retries - 1:
                                print(f"   🔄 第 {attempt + 1}/{max_retries} 次尝试")
                                time.sleep(retry_delay)
                                continue
                            return None
                else:
                    # 无超时限制
                    return _fetch_orderbook()
                    
            except KeyboardInterrupt:
                raise  # 允许用户中断
            except Exception as exc:
                error_msg = str(exc)
                is_404 = "404" in error_msg
                if not is_404 and attempt < max_retries - 1:
                    print(f"⚠️ Polymarket 订单簿获取失败 ({token_id[:20]}...), 第 {attempt + 1}/{max_retries} 次尝试: {exc}")
                    print(f"   ⏳ 等待 {retry_delay}s 后重试...")
                    time.sleep(retry_delay)
                else:
                    print(f"❌ 获取 Polymarket 订单簿失败 ({token_id[:20]}...), 退出: {exc}")
                    return None
        
        return None

    def _normalize_polymarket_levels(
        self,
        raw_levels: Any,
        depth: int,
        reverse: bool,
    ) -> List[OrderBookLevel]:
        levels: List[OrderBookLevel] = []
        if not raw_levels:
            return levels
        sorted_levels = sorted(
            raw_levels,
            key=lambda x: float(getattr(x, "price", 0.0)),
            reverse=reverse,
        )
        for entry in sorted_levels[:depth]:
            raw_price = getattr(entry, "price", None)
            raw_size = (
                getattr(entry, "size", None)
                or getattr(entry, "quantity", None)
                or getattr(entry, "amount", None)
                or getattr(entry, "remaining", None)
            )
            price = self._round_price(self._to_float(raw_price))
            size = self._to_float(raw_size)
            if price is None or size is None:
                continue
            # 价格精度控制：统一保留三位小数
            levels.append(OrderBookLevel(price=price, size=size))
        return levels

    def _derive_no_orderbook(self, yes_book: OrderBookSnapshot, no_token_id: str) -> OrderBookSnapshot:
        """从 YES token 订单簿推导 NO token 订单簿
        
        关系:
        - YES buy price = NO sell price (YES的买单价格 = NO的卖单价格)
        - YES sell price = NO buy price (YES的卖单价格 = NO的买单价格)
        - price_no = 1 - price_yes
        
        因此:
        - NO bids = 从 YES asks 转换而来 (价格 = 1 - yes_ask_price)
        - NO asks = 从 YES bids 转换而来 (价格 = 1 - yes_bid_price)
        """
        if not yes_book:
            return None
        
        # NO的bids来自YES的asks (YES卖=NO买)
        no_bids: List[OrderBookLevel] = []
        for level in yes_book.asks:
            if level.price is None or level.size is None:
                continue
            price = self._round_price(1.0 - level.price)
            if price is None:
                continue
            no_bids.append(OrderBookLevel(price=price, size=level.size))
        # 按价格降序排列 (bids应该从高到低)
        no_bids.sort(key=lambda x: x.price, reverse=True)
        
        # NO的asks来自YES的bids (YES买=NO卖)
        no_asks: List[OrderBookLevel] = []
        for level in yes_book.bids:
            if level.price is None or level.size is None:
                continue
            price = self._round_price(1.0 - level.price)
            if price is None:
                continue
            no_asks.append(OrderBookLevel(price=price, size=level.size))
        # 按价格升序排列 (asks应该从低到高)
        no_asks.sort(key=lambda x: x.price)
        
        return OrderBookSnapshot(
            bids=no_bids,
            asks=no_asks,
            source=yes_book.source,
            token_id=no_token_id,
            timestamp=yes_book.timestamp,
        )
    # ==================== 5. 加载匹配市场 ====================
    
    def load_market_matches(self, filename: str = "market_matches.json") -> bool:
        """
        从本地加载市场匹配结果
        
        Args:
            filename: JSON 文件路径
            
        Returns:
            是否成功加载
        """
        # 支持传入单个文件名或逗号分隔 / 列表形式的多个文件
        files: List[str]
        if isinstance(filename, list):
            files = filename
        else:
            # 允许用户传入以逗号分隔的字符串
            if isinstance(filename, str) and "," in filename:
                files = [p.strip() for p in filename.split(',') if p.strip()]
            else:
                files = [filename]

        combined: List[MarketMatch] = []
        any_loaded = False

        for fname in files:
            if not fname:
                continue
            try:
                if not os.path.exists(fname):
                    print(f"⚠️ 文件不存在，跳过: {fname}")
                    continue

                with open(fname, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if not isinstance(data, list):
                    print(f"⚠️ 文件格式不符合预期（应为列表）: {fname}")
                    continue

                for item in data:
                    if isinstance(item, MarketMatch):
                        combined.append(item)
                    elif isinstance(item, dict):
                        try:
                            normalized_item = dict(item)
                            if 'cutoff_at' in normalized_item:
                                normalized_item['cutoff_at'] = self._to_int(normalized_item.get('cutoff_at'))
                            combined.append(MarketMatch(**normalized_item))
                        except TypeError:
                            # 尝试容错解析常见字段名
                            mm = MarketMatch(
                                question=item.get('question', ''),
                                opinion_market_id=item.get('opinion_market_id') or item.get('opinionMarketId') or 0,
                                opinion_yes_token=item.get('opinion_yes_token') or item.get('opinionYesToken') or '',
                                opinion_no_token=item.get('opinion_no_token') or item.get('opinionNoToken') or '',
                                polymarket_condition_id=item.get('polymarket_condition_id') or item.get('polymarketConditionId') or '',
                                polymarket_yes_token=item.get('polymarket_yes_token') or item.get('polymarketYesToken') or '',
                                polymarket_no_token=item.get('polymarket_no_token') or item.get('polymarketNoToken') or '',
                                polymarket_slug=item.get('polymarket_slug') or item.get('polymarketSlug') or '',
                                similarity_score=float(item.get('similarity_score', 1.0)),
                                cutoff_at=self._to_int(item.get('cutoff_at'))
                            )
                            combined.append(mm)

                print(f"✅ 从 {fname} 加载 {len(data)} 条匹配")
                any_loaded = any_loaded or (len(data) > 0)

            except Exception as e:
                print(f"⚠️ 读取 {fname} 时出错: {e}")
                import traceback
                traceback.print_exc()
                continue

        if combined:
            self.market_matches = combined
            print(f"✅ 共加载 {len(self.market_matches)} 个市场匹配（来自 {len(files)} 个文件）")
            return True

        print("❌ 未能从提供的文件加载到任何市场匹配")
        return False
    
    
    # ==================== 6. 专业套利执行 ====================
    
    def _find_best_valid_bid_ask_pair(
        self,
        first_bids: List[OrderBookLevel],
        second_asks: List[OrderBookLevel],
        threshold_price: float,
        threshold_size: float
    ) -> Optional[Tuple[OrderBookLevel, OrderBookLevel]]:
        """
        找到最佳的 bid-ask 配对用于套利
        
        逻辑：在第一平台挂 bid 单，如果成交，在第二平台用 ask 价买入对冲
        注意：bid 是我自己挂的，不需要检查数量；只需检查 ask 的数量是否足够
        
        Args:
            first_bids: 第一个平台的 bid 档位列表（我要挂单的价格参考）
            second_asks: 第二个平台的 ask 档位列表（对冲时要买入的价格）
            threshold_price: 成本阈值（如 0.97）
            threshold_size: 数量阈值（如 200）
            
        Returns:
            满足条件的最佳配对 (first_bid, second_ask)，如果没有则返回 None
        """
        # 遍历第二个平台的 asks（对冲价格，从最优开始）
        for second_ask in second_asks:
            if not second_ask or second_ask.price is None or second_ask.size is None:
                continue
            
            # 只检查第二平台 ask 数量是否满足阈值（因为这是对冲时需要买入的）
            if second_ask.size <= threshold_size:
                continue
            
            # 遍历第一个平台的 bids（挂单价格，从最优开始）
            first_bid = first_bids[0]    
            # bid 是我自己挂的，不需要检查数量
            
            # 计算总成本（挂单价 + 对冲价）
            cost = first_bid.price + second_ask.price
            
            # 检查是否满足成本条件
            if cost < threshold_price:
                return (first_bid, second_ask)
        
        return None
    
    def execute_arbitrage_pro(self):
        """
        专业套利执行模式
        
        流程:
        1. 扫描所有市场，检测立即套利（ask+ask）和潜在套利（bid+ask）
        2. 按利润率从高到低排序
        3. 用户选择要执行的套利机会 (如果 non_interactive=False)
        4. 打印该市场的订单簿
        5. 下单并监控
        
        Args:
            non_interactive: 如果为 True，只打印摘要后退出，不等待用户输入
        """
        from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
        from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
        from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL
        
        if not self.market_matches:
            print("❌ 没有可用的市场匹配")
            return
        
        THRESHOLD_PRICE = 0.97
        THRESHOLD_SIZE = 200
        
        print(f"\n{'='*100}")
        print(f"开始扫描所有市场的套利机会...")
        print(f"条件: 成本 < ${THRESHOLD_PRICE:.2f}, 最小数量 > {THRESHOLD_SIZE}")
        print(f"{'='*100}\n")
        
        # 并发获取所有订单簿 & 即时扫描
        from concurrent.futures import ThreadPoolExecutor, as_completed

        print(f"🚀 开始并发获取 {len(self.market_matches)} 个市场的订单簿并实时扫描...")
        start_time = time.time()
        immediate_opportunities: List[Dict[str, Any]] = []
        pending_opportunities: List[Dict[str, Any]] = []
        total_matches = len(self.market_matches)
        progress_step = max(1, total_matches // 10)

        def scan_opportunities(
            match: MarketMatch,
            opinion_yes_book: Optional[OrderBookSnapshot],
            poly_yes_book: Optional[OrderBookSnapshot],
        ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
            local_immediate: List[Dict[str, Any]] = []
            local_pending: List[Dict[str, Any]] = []

            if not opinion_yes_book and not poly_yes_book:
                return local_immediate, local_pending

            opinion_no_book = self._derive_no_orderbook(opinion_yes_book, match.opinion_no_token) if opinion_yes_book else None
            poly_no_book = self._derive_no_orderbook(poly_yes_book, match.polymarket_no_token) if poly_yes_book else None

            # ========== 策略1: Opinion YES vs Polymarket NO ==========
            if opinion_yes_book and opinion_yes_book.asks and poly_no_book and poly_no_book.asks:
                op_yes_ask = opinion_yes_book.asks[0]
                pm_no_ask = poly_no_book.asks[0]

                if op_yes_ask and pm_no_ask and op_yes_ask.price is not None and pm_no_ask.price is not None:
                    min_size = min(op_yes_ask.size or 0, pm_no_ask.size or 0)
                    metrics = self._compute_profitability_metrics(
                        match,
                        'opinion',
                        op_yes_ask.price,
                        'polymarket',
                        pm_no_ask.price,
                        min_size,
                    )

                    cost = metrics['cost'] if metrics else None

                    if cost is not None and cost < THRESHOLD_PRICE and min_size > THRESHOLD_SIZE:
                        profit_rate = metrics['profit_rate']
                        annualized_rate = metrics['annualized_rate']
                        annualized_threshold = max(0.0, self.min_annualized_percent)
                        meets_annualized = True
                        if annualized_threshold > 0:
                            if annualized_rate is None:
                                print(
                                    f"  ⚪ 跳过立即套利: 年化收益率缺失 (需 ≥ {annualized_threshold:.2f}%)"
                                )
                                meets_annualized = False
                            elif annualized_rate < annualized_threshold:
                                print(
                                    f"  ⚪ 跳过立即套利: 年化收益率 {annualized_rate:.2f}% < {annualized_threshold:.2f}%"
                                )
                                meets_annualized = False
                        if not meets_annualized:
                            pass
                        else:
                            first_price = self._round_price(op_yes_ask.price)
                            second_price = self._round_price(pm_no_ask.price)
                            local_immediate.append({
                            'match': match,
                            'type': 'immediate',
                            'strategy': 'opinion_yes_ask_poly_no_ask',
                            'name': '立即套利: Opinion YES ask + Polymarket NO ask',
                            'cost': cost,
                            'profit_rate': profit_rate,
                            'annualized_rate': annualized_rate,
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
                        })
                            ann_text = f", 年化收益率={annualized_rate:.2f}%" if annualized_rate is not None else ""
                            print(f"  ✓ 发现立即套利: Opinion YES ask + Poly NO ask, 成本(含手续费)=${cost:.3f}, 收益率={profit_rate:.2f}%{ann_text}")

            if opinion_yes_book and opinion_yes_book.bids and poly_no_book and poly_no_book.asks:
                pair = self._find_best_valid_bid_ask_pair(
                    opinion_yes_book.bids,
                    poly_no_book.asks,
                    THRESHOLD_PRICE,
                    THRESHOLD_SIZE
                )

                if pair:
                    op_yes_bid, pm_no_ask = pair
                    available_size = pm_no_ask.size
                    metrics = self._compute_profitability_metrics(
                        match,
                        'opinion',
                        op_yes_bid.price,
                        'polymarket',
                        pm_no_ask.price,
                        available_size,
                    )
                    cost = metrics['cost'] if metrics else None
                    if cost is not None and cost < THRESHOLD_PRICE:
                        profit_rate = metrics['profit_rate']
                        annualized_rate = metrics['annualized_rate']
                        first_price = self._round_price(op_yes_bid.price)
                        second_price = self._round_price(pm_no_ask.price)

                        local_pending.append({
                            'match': match,
                            'type': 'pending',
                            'strategy': 'opinion_yes_bid_poly_no_ask',
                            'name': '潜在套利: Opinion YES bid → Polymarket NO ask',
                            'cost': cost,
                            'profit_rate': profit_rate,
                            'annualized_rate': annualized_rate,
                            'min_size': available_size,
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
                        })
                        ann_text = f", 年化收益率={annualized_rate:.2f}%" if annualized_rate is not None else ""
                        print(f"  ✓ 发现潜在套利: Opinion YES bid → Poly NO ask, 成本(含手续费)=${cost:.3f}, 收益率={profit_rate:.2f}%{ann_text}")

            # ========== 策略2: Opinion NO vs Polymarket YES ==========
            if opinion_no_book and opinion_no_book.asks and poly_yes_book and poly_yes_book.asks:
                op_no_ask = opinion_no_book.asks[0]
                pm_yes_ask = poly_yes_book.asks[0]

                if op_no_ask and pm_yes_ask and op_no_ask.price is not None and pm_yes_ask.price is not None:
                    min_size = min(op_no_ask.size or 0, pm_yes_ask.size or 0)
                    metrics = self._compute_profitability_metrics(
                        match,
                        'opinion',
                        op_no_ask.price,
                        'polymarket',
                        pm_yes_ask.price,
                        min_size,
                    )
                    cost = metrics['cost'] if metrics else None

                    if cost is not None and cost < THRESHOLD_PRICE and min_size > THRESHOLD_SIZE:
                        profit_rate = metrics['profit_rate']
                        annualized_rate = metrics['annualized_rate']
                        annualized_threshold = max(0.0, self.min_annualized_percent)
                        meets_annualized = True
                        if annualized_threshold > 0:
                            if annualized_rate is None:
                                print(
                                    f"  ⚪ 跳过立即套利: 年化收益率缺失 (需 ≥ {annualized_threshold:.2f}%)"
                                )
                                meets_annualized = False
                            elif annualized_rate < annualized_threshold:
                                print(
                                    f"  ⚪ 跳过立即套利: 年化收益率 {annualized_rate:.2f}% < {annualized_threshold:.2f}%"
                                )
                                meets_annualized = False
                        if not meets_annualized:
                            pass
                        else:
                            first_price = self._round_price(op_no_ask.price)
                            second_price = self._round_price(pm_yes_ask.price)
                            local_immediate.append({
                            'match': match,
                            'type': 'immediate',
                            'strategy': 'opinion_no_ask_poly_yes_ask',
                            'name': '立即套利: Opinion NO ask + Polymarket YES ask',
                            'cost': cost,
                            'profit_rate': profit_rate,
                            'annualized_rate': annualized_rate,
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
                        })
                        ann_text = f", 年化收益率={annualized_rate:.2f}%" if annualized_rate is not None else ""
                        print(f"  ✓ 发现立即套利: Opinion NO ask + Poly YES ask, 成本(含手续费)=${cost:.3f}, 收益率={profit_rate:.2f}%{ann_text}")

            if opinion_no_book and opinion_no_book.bids and poly_yes_book and poly_yes_book.asks:
                pair = self._find_best_valid_bid_ask_pair(
                    opinion_no_book.bids,
                    poly_yes_book.asks,
                    THRESHOLD_PRICE,
                    THRESHOLD_SIZE
                )

                if pair:
                    op_no_bid, pm_yes_ask = pair
                    available_size = pm_yes_ask.size
                    metrics = self._compute_profitability_metrics(
                        match,
                        'opinion',
                        op_no_bid.price,
                        'polymarket',
                        pm_yes_ask.price,
                        available_size,
                    )
                    cost = metrics['cost'] if metrics else None
                    if cost is not None and cost < THRESHOLD_PRICE:
                        profit_rate = metrics['profit_rate']
                        annualized_rate = metrics['annualized_rate']

                        local_pending.append({
                            'match': match,
                            'type': 'pending',
                            'strategy': 'opinion_no_bid_poly_yes_ask',
                            'name': '潜在套利: Opinion NO bid → Polymarket YES ask',
                            'cost': cost,
                            'profit_rate': profit_rate,
                            'annualized_rate': annualized_rate,
                            'min_size': available_size,
                            'first_platform': 'opinion',
                            'first_token': match.opinion_no_token,
                            'first_price': self._round_price(op_no_bid.price),
                            'first_side': OrderSide.BUY,
                            'second_platform': 'polymarket',
                            'second_token': match.polymarket_yes_token,
                            'second_price': self._round_price(pm_yes_ask.price),
                            'second_side': BUY,
                            'opinion_yes_book': opinion_yes_book,
                            'opinion_no_book': opinion_no_book,
                            'poly_yes_book': poly_yes_book,
                            'poly_no_book': poly_no_book,
                        })
                        ann_text = f", 年化收益率={annualized_rate:.2f}%" if annualized_rate is not None else ""
                        print(f"  ✓ 发现潜在套利: Opinion NO bid → Poly YES ask, 成本(含手续费)=${cost:.3f}, 收益率={profit_rate:.2f}%{ann_text}")

            # ========== 策略3: Polymarket YES vs Opinion NO ==========
            if poly_yes_book and poly_yes_book.bids and opinion_no_book and opinion_no_book.asks:
                pair = self._find_best_valid_bid_ask_pair(
                    poly_yes_book.bids,
                    opinion_no_book.asks,
                    THRESHOLD_PRICE,
                    THRESHOLD_SIZE
                )

                if pair:
                    pm_yes_bid, op_no_ask = pair
                    available_size = op_no_ask.size
                    metrics = self._compute_profitability_metrics(
                        match,
                        'polymarket',
                        pm_yes_bid.price,
                        'opinion',
                        op_no_ask.price,
                        available_size,
                    )
                    cost = metrics['cost'] if metrics else None
                    if cost is not None and cost < THRESHOLD_PRICE:
                        profit_rate = metrics['profit_rate']
                        annualized_rate = metrics['annualized_rate']

                        local_pending.append({
                            'match': match,
                            'type': 'pending',
                            'strategy': 'poly_yes_bid_opinion_no_ask',
                            'name': '潜在套利: Polymarket YES bid → Opinion NO ask',
                            'cost': cost,
                            'profit_rate': profit_rate,
                            'annualized_rate': annualized_rate,
                            'min_size': available_size,
                            'first_platform': 'polymarket',
                            'first_token': match.polymarket_yes_token,
                            'first_price': self._round_price(pm_yes_bid.price),
                            'first_side': BUY,
                            'second_platform': 'opinion',
                            'second_token': match.opinion_no_token,
                            'second_price': self._round_price(op_no_ask.price),
                            'second_side': OrderSide.BUY,
                            'opinion_yes_book': opinion_yes_book,
                            'opinion_no_book': opinion_no_book,
                            'poly_yes_book': poly_yes_book,
                            'poly_no_book': poly_no_book,
                        })
                        ann_text = f", 年化收益率={annualized_rate:.2f}%" if annualized_rate is not None else ""
                        print(f"  ✓ 发现潜在套利: Poly YES bid → Opinion NO ask, 成本(含手续费)=${cost:.3f}, 收益率={profit_rate:.2f}%{ann_text}")

            # ========== 策略4: Polymarket NO vs Opinion YES ==========
            if poly_no_book and poly_no_book.bids and opinion_yes_book and opinion_yes_book.asks:
                pair = self._find_best_valid_bid_ask_pair(
                    poly_no_book.bids,
                    opinion_yes_book.asks,
                    THRESHOLD_PRICE,
                    THRESHOLD_SIZE
                )

                if pair:
                    pm_no_bid, op_yes_ask = pair
                    available_size = op_yes_ask.size
                    metrics = self._compute_profitability_metrics(
                        match,
                        'polymarket',
                        pm_no_bid.price,
                        'opinion',
                        op_yes_ask.price,
                        available_size,
                    )
                    cost = metrics['cost'] if metrics else None
                    if cost is not None and cost < THRESHOLD_PRICE:
                        profit_rate = metrics['profit_rate']
                        annualized_rate = metrics['annualized_rate']

                        local_pending.append({
                            'match': match,
                            'type': 'pending',
                            'strategy': 'poly_no_bid_opinion_yes_ask',
                            'name': '潜在套利: Polymarket NO bid → Opinion YES ask',
                            'cost': cost,
                            'profit_rate': profit_rate,
                            'annualized_rate': annualized_rate,
                            'min_size': available_size,
                            'first_platform': 'polymarket',
                            'first_token': match.polymarket_no_token,
                            'first_price': self._round_price(pm_no_bid.price),
                            'first_side': BUY,
                            'second_platform': 'opinion',
                            'second_token': match.opinion_yes_token,
                            'second_price': self._round_price(op_yes_ask.price),
                            'second_side': OrderSide.BUY,
                            'opinion_yes_book': opinion_yes_book,
                            'opinion_no_book': opinion_no_book,
                            'poly_yes_book': poly_yes_book,
                            'poly_no_book': poly_no_book,
                        })
                        ann_text = f", 年化收益率={annualized_rate:.2f}%" if annualized_rate is not None else ""
                        print(f"  ✓ 发现潜在套利: Poly NO bid → Opinion YES ask, 成本(含手续费)=${cost:.3f}, 收益率={profit_rate:.2f}%{ann_text}")

            return local_immediate, local_pending

        def fetch_pair(idx: int, match: MarketMatch) -> Tuple[int, MarketMatch, Optional[OrderBookSnapshot], Optional[OrderBookSnapshot]]:
            try:
                opinion_yes_book = self.get_opinion_orderbook(match.opinion_yes_token)
                poly_yes_book = self.get_polymarket_orderbook(match.polymarket_yes_token)
            except Exception as exc:
                print(f"⚠️ 获取订单簿失败 [{idx}]: {exc}")
                opinion_yes_book = None
                poly_yes_book = None
            return idx, match, opinion_yes_book, poly_yes_book

        completed_count = 0
        max_workers = int(os.getenv("ORDERBOOK_WORKERS", "7"))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_pair, idx, match): idx for idx, match in enumerate(self.market_matches)}

            for future in as_completed(futures):
                idx, match, opinion_yes_book, poly_yes_book = future.result()
                completed_count += 1
                print(f"[{idx+1}/{total_matches}] 扫描: {match.question[:70]}...")

                local_immediate, local_pending = scan_opportunities(match, opinion_yes_book, poly_yes_book)

                for opp in local_immediate:
                    immediate_opportunities.append(opp)
                    self._maybe_auto_execute(opp)

                pending_opportunities.extend(local_pending)

                if completed_count % progress_step == 0 or completed_count == total_matches:
                    progress = (completed_count / total_matches) * 100
                    print(f"📊 进度: {completed_count}/{total_matches} ({progress:.1f}%)")

        elapsed = time.time() - start_time
        avg_time = elapsed / total_matches if total_matches else 0.0
        print(f"✅ 扫描完成，耗时 {elapsed:.2f}s (平均 {avg_time:.3f}s/市场)\n")


    # ==================== 即时执行线程支持 ====================
    def _maybe_auto_execute(self, opportunity: Dict[str, Any]) -> None:
        """在满足配置阈值时尝试自动执行即时套利。"""
        if not self.immediate_exec_enabled:
            return

        profit_rate = opportunity.get('profit_rate')
        if profit_rate is None:
            return

        lower = self.immediate_min_percent
        upper = self.immediate_max_percent

        if lower <= profit_rate <= upper:
            print(f"  ⚡ 利润率 {profit_rate:.2f}% 在阈值 [{lower:.2f}%,{upper:.2f}%]，启动即时执行线程")
            try:
                self._spawn_execute_thread(opportunity)
            except Exception as exc:
                print(f"⚠️ 无法启动即时执行线程: {exc}")
        else:
            print(f"  🔶 利润率 {profit_rate:.2f}% 不在阈值范围 [{lower:.2f}%,{upper:.2f}%]，跳过自动执行")

    def _spawn_execute_thread(self, opportunity: Dict[str, Any]) -> None:
        """启动一个后台线程来执行给定的套利机会（非交互）。"""
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
        """等待所有即时执行线程完成，防止主程序提前退出。"""
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
        """在后台执行一个套利机会。支持 'immediate' 和 'pending' 类型的简单自动化执行。

        注意: 此函数尽量复用已有下单逻辑，但为避免复杂交互，采取保守策略：
        - immediate: 在两个平台分别下限价买单 (使用 opp['first_price']/['second_price'] 和默认数量)
        - pending: 在第一平台下限价挂单，然后监控其成交状态（轮询），一旦成交则在第二平台下市价/限价买入对冲。
        """
        try:
            # 读取最小下单量配置
            try:
                default_size = float(os.getenv("IMMEDIATE_ORDER_SIZE", "200"))
            except Exception:
                default_size = 200.0

            order_size = float(default_size)
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
                        from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
                        from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
                        from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER

                        order1 = PlaceOrderDataInput(
                            marketId=opp['match'].opinion_market_id,
                            tokenId=str(opp['first_token']),
                            side=opp['first_side'],
                            orderType=LIMIT_ORDER,
                            price=str(first_price if first_price is not None else opp['first_price']),
                            makerAmountInBaseToken=str(first_order_size)
                        )
                        success, res1 = self._place_opinion_order_with_retries(
                            order1,
                            context="即时执行首单"
                        )
                        if success and res1:
                            print("✅ Opinion 订单提交成功 (即时执行)")
                        else:
                            print(f"❌ Opinion 下单失败（已尝试 {self.order_max_retries} 次）")
                    except Exception as e:
                        print(f"❌ Opinion 下单异常: {e}")
                else:
                    try:
                        from py_clob_client.clob_types import OrderArgs, OrderType
                        order1 = OrderArgs(
                            token_id=opp['first_token'],
                            price=first_price if first_price is not None else opp['first_price'],
                            size=first_order_size,
                            side=opp['first_side']
                        )
                        success, res1 = self._place_polymarket_order_with_retries(
                            order1,
                            OrderType.GTC,
                            context="即时执行首单"
                        )
                        if success:
                            print(f"✅ Polymarket 订单提交成功 (即时执行): {res1}")
                        else:
                            print(f"❌ Polymarket 下单失败（已尝试 {self.order_max_retries} 次）")
                    except Exception as e:
                        print(f"❌ Polymarket 下单异常: {e}")

                # Place second order
                if opp.get('second_platform') == 'opinion':
                    try:
                        from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
                        from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER
                        order2 = PlaceOrderDataInput(
                            marketId=opp['match'].opinion_market_id,
                            tokenId=str(opp['second_token']),
                            side=opp['second_side'],
                            orderType=LIMIT_ORDER,
                            price=str(second_price if second_price is not None else opp['second_price']),
                            makerAmountInBaseToken=str(second_order_size)
                        )
                        success, res2 = self._place_opinion_order_with_retries(
                            order2,
                            context="即时执行对冲"
                        )
                        if success and res2:
                            print("✅ Opinion 对冲订单提交成功 (即时执行)")
                        else:
                            print(f"❌ Opinion 对冲下单失败（已尝试 {self.order_max_retries} 次）")
                    except Exception as e:
                        print(f"❌ Opinion 对冲下单异常: {e}")
                else:
                    try:
                        from py_clob_client.clob_types import OrderArgs, OrderType
                        order2 = OrderArgs(
                            token_id=opp['second_token'],
                            price=second_price if second_price is not None else opp['second_price'],
                            size=second_order_size,
                            side=opp['second_side']
                        )
                        success, res2 = self._place_polymarket_order_with_retries(
                            order2,
                            OrderType.GTC,
                            context="即时执行对冲"
                        )
                        if success:
                            print(f"✅ Polymarket 对冲订单提交成功 (即时执行): {res2}")
                        else:
                            print(f"❌ Polymarket 对冲下单失败（已尝试 {self.order_max_retries} 次）")
                    except Exception as e:
                        print(f"❌ Polymarket 对冲下单异常: {e}")

                print("🟢 即时套利执行线程完成 (immediate)")
                return

            # Pending execution: place first order and monitor
            if opp.get('type') == 'pending':
                first_price = self._round_price(opp.get('first_price'))
                second_price = self._round_price(opp.get('second_price'))
                # 计算第一笔挂单的下单数量(考虑手续费)
                first_order_size, first_effective_size = self.get_order_size_for_platform(
                    opp['first_platform'],
                    first_price if first_price is not None else opp.get('first_price', 0.0),
                    order_size
                )
                
                print(f"  挂单数量: {first_order_size:.2f} -> 预期实际: {first_effective_size:.2f}")
                
                first_order_id = None
                # 下第一笔限价挂单
                try:
                    if opp['first_platform'] == 'opinion':
                        from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
                        from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER
                        order = PlaceOrderDataInput(
                            marketId=opp['match'].opinion_market_id,
                            tokenId=str(opp['first_token']),
                            side=opp['first_side'],
                            orderType=LIMIT_ORDER,
                            price=str(first_price if first_price is not None else opp['first_price']),
                            makerAmountInBaseToken=str(first_order_size)
                        )
                        success, result = self._place_opinion_order_with_retries(
                            order,
                            context="即时执行挂单"
                        )
                        if not (success and result):
                            print(f"❌ Opinion 下单失败（已尝试 {self.order_max_retries} 次）")
                            return
                        order_info = getattr(result, 'result', None)
                        order_data = getattr(order_info, 'order_data', None) if order_info else None
                        first_order_id = getattr(order_data, 'order_id', None)
                        print(f"✅ Opinion 挂单已提交 (order_id={first_order_id})")
                    else:
                        from py_clob_client.clob_types import OrderArgs, OrderType
                        order = OrderArgs(
                            token_id=opp['first_token'],
                            price=first_price if first_price is not None else opp['first_price'],
                            size=first_order_size,
                            side=opp['first_side']
                        )
                        success, res = self._place_polymarket_order_with_retries(
                            order,
                            OrderType.GTC,
                            context="即时执行挂单"
                        )
                        if not success:
                            print(f"❌ Polymarket 下单失败（已尝试 {self.order_max_retries} 次）")
                            return
                        first_order_id = res.get('orderID') if isinstance(res, dict) else None
                        if not first_order_id and isinstance(res, dict):
                            first_order_id = res.get('order_id')
                        print(f"✅ Polymarket 挂单已提交 (order_id={first_order_id})")
                except Exception as e:
                    print(f"❌ 提交第一笔挂单失败: {e}")
                    return

                # 启动账户监控以便快速读取订单状态
                try:
                    self._ensure_account_monitors()
                except Exception:
                    pass

                # 监控订单是否成交
                timeout = float(os.getenv('PENDING_EXEC_TIMEOUT', '300'))
                poll_interval = float(os.getenv('PENDING_POLL_INTERVAL', '5'))
                elapsed = 0.0
                print(f"🔍 开始监控订单成交状态 (timeout={timeout}s, poll_interval={poll_interval}s)")
                while elapsed < timeout:
                    time.sleep(poll_interval)
                    elapsed += poll_interval
                    cached = self._check_cached_order_state(opp['first_platform'], first_order_id)
                    if cached:
                        if self._status_is_filled(cached.get('status'), cached.get('filled'), cached.get('total')):
                            print(f"✅ 首订单已成交，开始对冲下单")
                            
                            # 获取首单的实际成交数量
                            filled_amount = cached.get('filled', first_effective_size)
                            
                            # 计算对冲单数量(需要匹配首单的实际成交数量)
                            hedge_target = filled_amount if opp['first_platform'] == 'opinion' else filled_amount
                            hedge_order_size, hedge_effective_size = self.get_order_size_for_platform(
                                opp['second_platform'],
                                second_price if second_price is not None else opp.get('second_price', 0.0),
                                hedge_target,
                                is_hedge=True
                            )
                            
                            print(f"  对冲目标: {hedge_target:.2f} -> 对冲下单: {hedge_order_size:.2f}")
                            
                            # 下第二笔对冲单
                            try:
                                if opp.get('second_platform') == 'opinion':
                                    from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
                                    from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER
                                    order2 = PlaceOrderDataInput(
                                        marketId=opp['match'].opinion_market_id,
                                        tokenId=str(opp['second_token']),
                                        side=opp['second_side'],
                                        orderType=LIMIT_ORDER,
                                        price=str(second_price if second_price is not None else opp['second_price']),
                                        makerAmountInBaseToken=str(hedge_order_size)
                                    )
                                    success, res2 = self._place_opinion_order_with_retries(
                                        order2,
                                        context="即时执行对冲"
                                    )
                                    if success and res2:
                                        print("✅ Opinion 对冲订单提交")
                                    else:
                                        print(f"❌ Opinion 对冲下单失败（已尝试 {self.order_max_retries} 次）")
                                else:
                                    from py_clob_client.clob_types import OrderArgs, OrderType
                                    order2 = OrderArgs(
                                        token_id=opp['second_token'],
                                        price=second_price if second_price is not None else opp['second_price'],
                                        size=hedge_order_size,
                                        side=opp['second_side']
                                    )
                                    success, res2 = self._place_polymarket_order_with_retries(
                                        order2,
                                        OrderType.GTC,
                                        context="即时执行对冲"
                                    )
                                    if success:
                                        print(f"✅ Polymarket 对冲订单提交: {res2}")
                                    else:
                                        print(f"❌ Polymarket 对冲下单失败（已尝试 {self.order_max_retries} 次）")
                            except Exception as e:
                                print(f"❌ 对冲下单失败: {e}")
                            break
                        if self._status_is_cancelled(cached.get('status')):
                            print("⚠️ 首订单被取消或拒绝，停止即时执行")
                            break

                else:
                    # 超时未成交，尝试撤单（如果有能力）
                    print("⌛ 监控超时，未检测到成交，尝试撤单（如支持）并退出")
                    try:
                        if opp['first_platform'] == 'opinion' and hasattr(self.opinion_client, 'cancel_order') and first_order_id:
                            self.opinion_client.cancel_order(first_order_id)
                        elif opp['first_platform'] == 'polymarket' and hasattr(self.polymarket_client, 'cancel_order') and first_order_id:
                            self.polymarket_client.cancel_order(first_order_id)
                    except Exception:
                        pass

                print("🟢 即时套利执行线程完成 (pending)")
                return

        except Exception as e:
            print(f"❌ 即时执行线程异常: {e}")
            traceback.print_exc()


# ==================== 主程序 ====================

def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description='跨平台套利检测器 - Opinion vs Polymarket',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 正常运行 (重新获取和匹配市场)
  python cross_platform_arbitrage.py
  
  # 使用缓存的市场匹配结果
  python cross_platform_arbitrage.py --use-cached
  
  # 使用缓存 + 非交互模式
  python cross_platform_arbitrage.py --use-cached --no-interactive
  
  # 使用本地相似度匹配算法
  python cross_platform_arbitrage.py --no-search
  
  # 指定自定义的匹配文件
  python cross_platform_arbitrage.py --use-cached --matches-file my_matches.json
        """
    )
    
    parser.add_argument(
        '--use-cached',
        action='store_true',
        help='使用缓存的市场匹配结果 (默认: market_matches.json)'
    )
    
    parser.add_argument(
        '--matches-file',
        type=str,
        default='market_matches.json',
        help='市场匹配结果文件路径，支持多个文件用逗号分隔 (默认: market_matches.json)'
    )
    
    parser.add_argument(
        '--no-search',
        action='store_true',
        help='使用本地相似度算法匹配市场，而不是搜索 API'
    )
    
    parser.add_argument(
        '--no-interactive',
        action='store_true',
        help='不进入交互式执行模式，仅显示套利机会'
    )

    parser.add_argument(
        '--test',
        action='store_true',
        help='运行测试函数'
    )

    parser.add_argument(
        '--pro',
        action='store_true',
        help='运行专业套利执行模式'
    )
    
    args = parser.parse_args()
    
    try:
        scanner = CrossPlatformArbitrage()
        if args.test:
            scanner.test()
            return
        if args.pro:
            # 先加载市场匹配
            if not scanner.load_market_matches(args.matches_file):
                print("⚠️ 无法加载市场匹配，请先运行正常扫描")
                return
            try:
                scanner.execute_arbitrage_pro()
            finally:
                scanner.wait_for_active_exec_threads()
            return
        
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
