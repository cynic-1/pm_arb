"""
跨平台套利检测器 - Opinion vs Polymarket
检测在两个平台之间同一市场的套利机会
套利条件: Opinion_YES_Price + Polymarket_NO_Price < 1
         或 Polymarket_YES_Price + Opinion_NO_Price < 1
"""

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
            self.immediate_min_percent = float(os.getenv("IMMEDIATE_MIN_PERCENT", "4.0"))
        except Exception:
            self.immediate_min_percent = 4.0
        try:
            self.immediate_max_percent = float(os.getenv("IMMEDIATE_MAX_PERCENT", "20.0"))
        except Exception:
            self.immediate_max_percent = 20.0

        # 跟踪启动的即时执行线程（仅用于信息/清理）
        self._active_exec_threads: List[threading.Thread] = []
        fallback_env = os.getenv("ORDER_STATUS_FALLBACK_AFTER")
        self.order_status_fallback_after: Optional[float] = None
        if fallback_env:
            try:
                self.order_status_fallback_after = float(fallback_env)
            except ValueError:
                print("⚠️ ORDER_STATUS_FALLBACK_AFTER 环境变量不是有效数字，将忽略。")
        
        print("✅ 初始化完成!\n")
    
    
    # ==================== Opinion 手续费计算 ====================
    
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
            print(f"💰 Opinion 手续费计算: price={price:.4f}, fee_rate={fee_rate:.6f}, "
                  f"预估手续费=${Fee_provisional:.4f} (百分比手续费)")
        else:
            # 适用最低手续费 $0.5
            A_order = target_amount + 0.5 / price
            print(f"💰 Opinion 手续费计算: price={price:.4f}, fee_rate={fee_rate:.6f}, "
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
    
    
    # ==================== 账户监控 ====================
    
    def _ensure_account_monitors(self):
        """确保账户监控线程已启动"""
        if self._account_monitors_started:
            return
        with self._monitor_control_lock:
            if self._account_monitors_started:
                return
            self._monitor_stop_event.clear()
            self._opinion_state_updated.clear()
            self._polymarket_state_updated.clear()
            self._opinion_monitor_thread = threading.Thread(
                target=self._poll_opinion_account,
                name="OpinionAccountMonitor",
                daemon=True
            )
            self._opinion_monitor_thread.start()
            if self.polymarket_trading_enabled:
                self._polymarket_monitor_thread = threading.Thread(
                    target=self._poll_polymarket_account,
                    name="PolymarketAccountMonitor",
                    daemon=True
                )
                self._polymarket_monitor_thread.start()
            self._account_monitors_started = True
            self._opinion_refresh_event.set()
            if self.polymarket_trading_enabled:
                self._polymarket_refresh_event.set()
        # 给监控线程一点时间完成首次轮询
        time.sleep(min(self.account_monitor_interval, 1.0))

    def _poll_opinion_account(self):
        """周期性刷新 Opinion 账户状态"""
        while not self._monitor_stop_event.is_set():
            self._refresh_opinion_account_state()
            if self._monitor_wait_for_next(self._opinion_refresh_event):
                break

    def _poll_polymarket_account(self):
        """周期性刷新 Polymarket 账户状态"""
        while not self._monitor_stop_event.is_set():
            self._refresh_polymarket_account_state()
            if self._monitor_wait_for_next(self._polymarket_refresh_event):
                break

    def _monitor_wait_for_next(self, refresh_event: threading.Event) -> bool:
        """等待下一次刷新，响应立即刷新或停止信号"""
        if refresh_event.is_set():
            refresh_event.clear()
            return False
        interval = max(self.account_monitor_interval, 0.1)
        step = min(0.5, interval)
        remaining = interval
        while remaining > 0:
            wait_duration = min(step, remaining)
            if self._monitor_stop_event.wait(wait_duration):
                return True
            if refresh_event.is_set():
                refresh_event.clear()
                return False
            remaining -= wait_duration
        return False

    def _refresh_opinion_account_state(self):
        state: Dict[str, Any] = {"timestamp": time.time()}
#         try:
            # balances_resp = self.opinion_client.get_my_balances()
            # if getattr(balances_resp, "errno", None) == 0:
                # state["balances"] = getattr(balances_resp, "result", None)
            # else:
                # state["balance_error"] = getattr(balances_resp, "errmsg", "unknown error")
        # except Exception as exc:
            # state["balance_error"] = str(exc)
        orders: List[Dict[str, Any]] = []
        if hasattr(self.opinion_client, "get_my_orders"):
            try:
                orders_resp = self.opinion_client.get_my_orders()
                if getattr(orders_resp, "errno", None) == 0:
                    raw_orders = self._extract_iterable(getattr(orders_resp, "result", None))
                    for entry in raw_orders:
                        # logging.info(f"Processing order entry: {entry.order_shares}")
                        normalized = self._normalize_order_entry(entry, platform="opinion")
                        if normalized:
                            orders.append(normalized)
                else:
                    state["orders_error"] = getattr(orders_resp, "errmsg", "unknown error")
            except Exception as exc:
                state["orders_error"] = str(exc)
        if orders:
            state["orders"] = orders
        with self._account_state_lock:
            self._opinion_account_state = state
        self._opinion_state_updated.set()

    def _refresh_polymarket_account_state(self):
        state: Dict[str, Any] = {"timestamp": time.time()}
        try:
            open_orders = self.polymarket_client.get_orders(OpenOrderParams())
            # logging.info(open_orders)
            normalized_orders = []
            for entry in open_orders or []:
                normalized = self._normalize_order_entry(entry, platform="polymarket")
                if normalized:
                    normalized_orders.append(normalized)
            if normalized_orders:
                state["orders"] = normalized_orders
        except Exception as exc:
            state["orders_error"] = str(exc)
        try:
            trades = self.polymarket_client.get_trades()
            normalized_trades = []
            for entry in trades or []:
                normalized = self._normalize_trade_entry(entry)
                if normalized:
                    normalized_trades.append(normalized)
            if normalized_trades:
                state["trades"] = normalized_trades
        except Exception as exc:
            state["trades_error"] = str(exc)
        with self._account_state_lock:
            self._polymarket_account_state = state
        self._polymarket_state_updated.set()

    def _extract_iterable(self, payload: Any) -> List[Any]:
        """将返回结果标准化为列表"""
        if payload is None:
            return []
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("list", "orders", "data", "result"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
        if hasattr(payload, "list"):
            try:
                return list(payload.list)
            except TypeError:
                return payload.list
        return [payload]

    def _normalize_order_entry(self, entry: Any, platform: str) -> Optional[Dict[str, Any]]:
        """提取关注字段，便于快速判定订单状态"""
        order_id = self._extract_from_entry(entry, ["order_id", "orderId", "id"])
        if not order_id:
            return None
        status = self._extract_from_entry(entry, ["status", "state"])
        filled = self._to_float(self._extract_from_entry(entry, [
            "filled_shares",
            "filled_amount",
            "filledAmount",
            "size_matched",
            "sizeMatched",
            "quantity_filled"
        ]))
        total = self._to_float(self._extract_from_entry(entry, [
            "order_shares",
            "original_size",
            "original_amount",
            "total_amount",
            "amount",
            "size",
            "quantity"
        ]))
        return {
            "order_id": order_id,
            "status": status,
            "filled": filled,
            "total": total,
            "platform": platform,
        }

    def _normalize_trade_entry(self, entry: Any) -> Optional[Dict[str, Any]]:
        """统一化交易记录字段"""
        order_id = self._extract_from_entry(entry, ["order_id", "orderId", "id"])
        if not order_id:
            return None
        size = self._to_float(self._extract_from_entry(entry, ["size", "amount", "quantity"]))
        price = self._to_float(self._extract_from_entry(entry, ["price"]))
        status = self._extract_from_entry(entry, ["status"])
        return {
            "order_id": order_id,
            "size": size,
            "price": price,
            "status": status,
        }

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

    def _refresh_account_state_snapshot(
        self,
        platform: Optional[str] = None,
        force_direct: bool = False,
    ):
        """请求账户监控线程刷新；必要时直接调用 API"""
        direct_refresh = force_direct or not self._account_monitors_started

        if direct_refresh:
            if platform in (None, "opinion"):
                self._refresh_opinion_account_state()
            if platform in (None, "polymarket") and self.polymarket_trading_enabled:
                self._refresh_polymarket_account_state()
            return

        if platform in (None, "opinion"):
            self._opinion_refresh_event.set()
        if platform in (None, "polymarket") and self.polymarket_trading_enabled:
            self._polymarket_refresh_event.set()

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

    def _print_orderbook_levels(
        self,
        title: str,
        snapshot: Optional[OrderBookSnapshot],
        depth: int = 5,
    ) -> None:
        """打印订单簿前 N 档报价，并对齐显示 bid/ask"""
        print(f"  {title}:")
        if not snapshot or (not snapshot.bids and not snapshot.asks):
            print("    无可用订单簿数据")
            return

        header = "    #   BidPx   BidSz |  AskPx   AskSz"
        print(header)
        print("    -------------------------------------")

        bids = snapshot.bids[:depth] if snapshot else []
        asks = snapshot.asks[:depth] if snapshot else []
        max_rows = max(len(bids), len(asks))

        def _fmt(value: Optional[float], width: int, decimals: int) -> str:
            if value is None:
                return "--".rjust(width)
            return f"{value:>{width}.{decimals}f}"

        for idx in range(max_rows):
            bid_level = bids[idx] if idx < len(bids) else None
            ask_level = asks[idx] if idx < len(asks) else None

            bid_price = bid_level.price if bid_level else None
            bid_size = bid_level.size if bid_level else None
            ask_price = ask_level.price if ask_level else None
            ask_size = ask_level.size if ask_level else None

            row = (
                f"    {idx + 1:>2}  "
                f"{_fmt(bid_price, 7, 4)} "
                f"{_fmt(bid_size, 7, 2)} | "
                f"{_fmt(ask_price, 7, 4)} "
                f"{_fmt(ask_size, 7, 2)}"
            )
            print(row)

        source = snapshot.source if snapshot.source else "unknown"
        print(f"    来源: {source}")

    def _check_cached_order_state(self, platform: str, order_id: str) -> Optional[Dict[str, Any]]:
        """使用账户监控缓存快速判定订单状态"""
        with self._account_state_lock:
            state = self._opinion_account_state if platform == "opinion" else self._polymarket_account_state
            state_copy = dict(state) if state else {}
        if not state_copy:
            return None
        for order in state_copy.get("orders", []):
            if order.get("order_id") == order_id:
                normalized = dict(order)
                normalized["filled"] = self._to_float(order.get("filled"))
                normalized["total"] = self._to_float(order.get("total"))
                return normalized
        # 对于 Polymarket, trades 中包含已成交信息
        if platform == "polymarket":
            for trade in state_copy.get("trades", []):
                if trade.get("order_id") == order_id:
                    print("检测到订单成交")
                    return {
                        "order_id": order_id,
                        "status": trade.get("status", "filled"),
                        "filled": self._to_float(trade.get("size")),
                        "total": self._to_float(trade.get("size")),
                    }
        return None

    def _status_is_filled(self, status: Any, filled: Optional[float], total: Optional[float]) -> bool:
        if filled is not None and total and filled >= total:
            return True
        if status is None:
            return False
        if isinstance(status, (int, float)):
            return int(status) in {2, 6}
        status_str = str(status).lower()
        return status_str in {"filled", "done", "completed", "complete", "closed"}

    def _status_is_cancelled(self, status: Any) -> bool:
        if status is None:
            return False
        if isinstance(status, (int, float)):
            return int(status) in {3, 4}
        status_str = str(status).lower()
        return status_str in {"cancelled", "canceled", "rejected"}

    def _interpret_cached_order_state(
        self,
        platform: str,
        cached_state: Dict[str, Any],
        source: str = "缓存",
    ) -> Optional[bool]:
        """根据缓存状态输出信息并返回成交/取消结果"""
        status = cached_state.get("status")
        filled = cached_state.get("filled")
        total = cached_state.get("total")
        if self._status_is_filled(status, filled, total):
            print(f"✅ {platform.capitalize()} 订单已完全成交 ({source})")
            return True
        if self._status_is_cancelled(status):
            print(f"❌ {platform.capitalize()} 订单已被取消 ({source})")
            return False
        if total and filled is not None:
            try:
                fill_rate = (filled / total * 100) if total else 0
                print(f"   ({source}) 进度: {fill_rate:.1f}% ({filled:.4f}/{total:.4f} shares)")
            except ZeroDivisionError:
                pass
        return None
    
    # ==================== 3. 获取订单簿 ====================
    
    def get_opinion_orderbook(self, token_id: str, depth: int = 5) -> Optional[OrderBookSnapshot]:
        """获取 Opinion 订单簿前 N 档含价格和数量"""
        try:
            response = self.opinion_client.get_orderbook(token_id)
            logger.info(f"Opinion order book for {token_id}")
            if response.errno != 0:
                return None
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
        except Exception as exc:
            print(f"❌ 获取 Opinion 订单簿失败 ({token_id[:20]}...): {exc}")
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
            price = self._to_float(getattr(entry, "price", None))
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
            # 价格精度控制：截断到5位小数
            levels.append(OrderBookLevel(price=round(price, 5), size=size))
        return levels

    def get_polymarket_orderbook(self, token_id: str, depth: int = 5) -> Optional[OrderBookSnapshot]:
        """获取 Polymarket 订单簿前 N 档含价格和数量"""
        try:
            book = self.polymarket_client.get_order_book(token_id)
            logger.info(f"Polymarket order book for {token_id}")
            if not book:
                return None
            bids = self._normalize_polymarket_levels(getattr(book, "bids", []), depth, reverse=True)
            asks = self._normalize_polymarket_levels(getattr(book, "asks", []), depth, reverse=False)
            return OrderBookSnapshot(
                bids=bids,
                asks=asks,
                source="polymarket",
                token_id=token_id,
                timestamp=time.time(),
            )
        except Exception as exc:
            print(f"❌ 获取 Polymarket 订单簿失败 ({token_id[:20]}...): {exc}")
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
            price = self._to_float(raw_price)
            size = self._to_float(raw_size)
            if price is None or size is None:
                continue
            # 价格精度控制：截断到5位小数
            levels.append(OrderBookLevel(price=round(price, 5), size=size))
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
        # 价格精度控制：截断到5位小数，避免浮点数精度问题
        no_bids = [
            OrderBookLevel(price=round(1.0 - level.price, 5), size=level.size)
            for level in yes_book.asks
            if level.price is not None and level.size is not None
        ]
        # 按价格降序排列 (bids应该从高到低)
        no_bids.sort(key=lambda x: x.price, reverse=True)
        
        # NO的asks来自YES的bids (YES买=NO卖)
        # 价格精度控制：截断到5位小数，避免浮点数精度问题
        no_asks = [
            OrderBookLevel(price=round(1.0 - level.price, 5), size=level.size)
            for level in yes_book.bids
            if level.price is not None and level.size is not None
        ]
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
                            combined.append(MarketMatch(**item))
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
                                similarity_score=float(item.get('similarity_score', 1.0))
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
    
    def display_arbitrage_summary(self, opportunities: List[ArbitrageOpportunity]):
        """显示套利机会摘要"""
        if not opportunities:
            print("❌ 未发现套利机会")
            return
        
        # 按利润率排序
        sorted_opps = sorted(opportunities, key=lambda x: x.profit_rate, reverse=True)
        
        print(f"\n{'='*100}")
        print(f"套利机会总览 (共 {len(opportunities)} 个)")
        print(f"{'='*100}\n")
        
        for i, opp in enumerate(sorted_opps[:20], 1):  # 显示前20个
            match = opp.market_match
            print(f"{i}. {match.question[:70]}")
            print(f"   策略: {self._get_strategy_name(opp.strategy)}")
            print(f"   成本: ${opp.cost:.4f} | 利润: ${opp.profit:.4f} | 利润率: {opp.profit_rate:.2f}%")
            
            if opp.strategy == "opinion_yes_poly_no":
                best_bid = opp.opinion_yes_book.best_bid() if opp.opinion_yes_book else None
                best_ask = opp.poly_no_book.best_ask() if opp.poly_no_book else None
                bid_size = best_bid.size if best_bid and best_bid.size is not None else 0.0
                ask_size = best_ask.size if best_ask and best_ask.size is not None else 0.0
                print(f"   执行: Opinion YES @ ${opp.opinion_yes_ask:.4f} (bid size {bid_size:.2f}) + Polymarket NO @ ${opp.poly_no_ask:.4f} (ask size {ask_size:.2f})")
            else:
                best_bid = opp.poly_yes_book.best_bid() if opp.poly_yes_book else None
                best_ask = opp.opinion_no_book.best_ask() if opp.opinion_no_book else None
                bid_size = best_bid.size if best_bid and best_bid.size is not None else 0.0
                ask_size = best_ask.size if best_ask and best_ask.size is not None else 0.0
                print(f"   执行: Polymarket YES @ ${opp.poly_yes_ask:.4f} (bid size {bid_size:.2f}) + Opinion NO @ ${opp.opinion_no_ask:.4f} (ask size {ask_size:.2f})")
            
            print(f"   时间: {opp.timestamp}")
            print()
    
    def _get_strategy_name(self, strategy: str) -> str:
        """获取策略名称"""
        if strategy == "opinion_yes_poly_no":
            return "Opinion YES + Polymarket NO"
        elif strategy == "poly_yes_opinion_no":
            return "Polymarket YES + Opinion NO"
        else:
            return strategy
    
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
    
    def execute_arbitrage_pro(self, non_interactive: bool = False):
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
        
        THRESHOLD_PRICE = 0.981
        THRESHOLD_SIZE = 200
        
        print(f"\n{'='*100}")
        print(f"开始扫描所有市场的套利机会...")
        print(f"条件: 成本 < ${THRESHOLD_PRICE:.2f}, 最小数量 > {THRESHOLD_SIZE}")
        print(f"{'='*100}\n")
        
        # 扫描所有市场，收集立即套利和潜在套利
        immediate_opportunities = []  # ask + ask (立即执行)
        pending_opportunities = []    # bid + ask (等待成交)
        
        for idx, match in enumerate(self.market_matches, 1):
            print(f"[{idx}/{len(self.market_matches)}] 扫描: {match.question[:70]}...")
            
            # 只获取 YES token 的订单簿，NO token 通过计算得出
            # YES buy = NO sell, YES sell = NO buy
            opinion_yes_book = self.get_opinion_orderbook(match.opinion_yes_token)
            poly_yes_book = self.get_polymarket_orderbook(match.polymarket_yes_token)
            
            # 从 YES 订单簿计算 NO 订单簿
            opinion_no_book = self._derive_no_orderbook(opinion_yes_book, match.opinion_no_token) if opinion_yes_book else None
            poly_no_book = self._derive_no_orderbook(poly_yes_book, match.polymarket_no_token) if poly_yes_book else None
            
            # ========== 策略1: Opinion YES vs Polymarket NO ==========
            # 立即套利: 买 Opinion YES ask + 买 Polymarket NO ask
            if opinion_yes_book and opinion_yes_book.asks and poly_no_book and poly_no_book.asks:
                op_yes_ask = opinion_yes_book.asks[0]
                pm_no_ask = poly_no_book.asks[0]
                
                if op_yes_ask and pm_no_ask and op_yes_ask.price is not None and pm_no_ask.price is not None:
                    cost = op_yes_ask.price + pm_no_ask.price
                    min_size = min(op_yes_ask.size or 0, pm_no_ask.size or 0)
                    
                    if cost < THRESHOLD_PRICE and min_size > THRESHOLD_SIZE:
                        profit_rate = ((1.0 - cost) / cost) * 100
                        immediate_opportunities.append({
                            'match': match,
                            'type': 'immediate',
                            'strategy': 'opinion_yes_ask_poly_no_ask',
                            'name': '立即套利: Opinion YES ask + Polymarket NO ask',
                            'cost': cost,
                            'profit_rate': profit_rate,
                            'min_size': min_size,
                            'first_platform': 'opinion',
                            'first_token': match.opinion_yes_token,
                            'first_price': op_yes_ask.price,
                            'first_side': OrderSide.BUY,
                            'second_platform': 'polymarket',
                            'second_token': match.polymarket_no_token,
                            'second_price': pm_no_ask.price,
                            'second_side': BUY,
                            'opinion_yes_book': opinion_yes_book,
                            'opinion_no_book': opinion_no_book,
                            'poly_yes_book': poly_yes_book,
                            'poly_no_book': poly_no_book,
                        })
                        print(f"  ✓ 发现立即套利: Opinion YES ask + Poly NO ask, 成本=${cost:.4f}, 利润率={profit_rate:.2f}%")
                        # 如果利润率在即时执行配置范围内，立即以新线程执行（非交互）
                        try:
                            if self.immediate_exec_enabled and self.immediate_min_percent <= profit_rate <= self.immediate_max_percent:
                                print(f"  ⚡ 利润率 {profit_rate:.2f}% 在阈值 [{self.immediate_min_percent:.2f}%,{self.immediate_max_percent:.2f}%]，启动即时执行线程")
                                self._spawn_execute_thread(immediate_opportunities[-1])
                        except Exception as e:
                            print(f"⚠️ 无法启动即时执行线程: {e}")
            
            # 潜在套利: 挂 Opinion YES bid，如果成交，在 Polymarket 买入 NO ask
            if opinion_yes_book and opinion_yes_book.bids and poly_no_book and poly_no_book.asks:
                pair = self._find_best_valid_bid_ask_pair(
                    opinion_yes_book.bids,
                    poly_no_book.asks,
                    THRESHOLD_PRICE,
                    THRESHOLD_SIZE
                )
                
                if pair:
                    op_yes_bid, pm_no_ask = pair
                    cost = op_yes_bid.price + pm_no_ask.price
                    available_size = pm_no_ask.size  # 只看 ask 的数量（对冲时能买多少）
                    profit_rate = ((1.0 - cost) / cost * 100)
                    
                    pending_opportunities.append({
                        'match': match,
                        'type': 'pending',
                        'strategy': 'opinion_yes_bid_poly_no_ask',
                        'name': '潜在套利: Opinion YES bid → Polymarket NO ask',
                        'cost': cost,
                        'profit_rate': profit_rate,
                        'min_size': available_size,
                        'first_platform': 'opinion',
                        'first_token': match.opinion_yes_token,
                        'first_price': op_yes_bid.price,
                        'first_side': OrderSide.BUY,
                        'second_platform': 'polymarket',
                        'second_token': match.polymarket_no_token,
                        'second_price': pm_no_ask.price,
                        'second_side': BUY,
                        'opinion_yes_book': opinion_yes_book,
                        'opinion_no_book': opinion_no_book,
                        'poly_yes_book': poly_yes_book,
                        'poly_no_book': poly_no_book,
                    })
                    print(f"  ✓ 发现潜在套利: Opinion YES bid → Poly NO ask, 成本=${cost:.4f}, 利润率={profit_rate:.2f}%")
            
            # ========== 策略2: Opinion NO vs Polymarket YES ==========
            # 立即套利: 买 Opinion NO ask + 买 Polymarket YES ask
            if opinion_no_book and opinion_no_book.asks and poly_yes_book and poly_yes_book.asks:
                op_no_ask = opinion_no_book.asks[0]
                pm_yes_ask = poly_yes_book.asks[0]
                
                if op_no_ask and pm_yes_ask and op_no_ask.price is not None and pm_yes_ask.price is not None:
                    cost = op_no_ask.price + pm_yes_ask.price
                    min_size = min(op_no_ask.size or 0, pm_yes_ask.size or 0)
                    
                    if cost < THRESHOLD_PRICE and min_size > THRESHOLD_SIZE:
                        profit_rate = ((1.0 - cost) / cost) * 100
                        immediate_opportunities.append({
                            'match': match,
                            'type': 'immediate',
                            'strategy': 'opinion_no_ask_poly_yes_ask',
                            'name': '立即套利: Opinion NO ask + Polymarket YES ask',
                            'cost': cost,
                            'profit_rate': profit_rate,
                            'min_size': min_size,
                            'first_platform': 'opinion',
                            'first_token': match.opinion_no_token,
                            'first_price': op_no_ask.price,
                            'first_side': OrderSide.BUY,
                            'second_platform': 'polymarket',
                            'second_token': match.polymarket_yes_token,
                            'second_price': pm_yes_ask.price,
                            'second_side': BUY,
                            'opinion_yes_book': opinion_yes_book,
                            'opinion_no_book': opinion_no_book,
                            'poly_yes_book': poly_yes_book,
                            'poly_no_book': poly_no_book,
                        })
                        print(f"  ✓ 发现立即套利: Opinion NO ask + Poly YES ask, 成本=${cost:.4f}, 利润率={profit_rate:.2f}%")
                        try:
                            if self.immediate_exec_enabled and self.immediate_min_percent <= profit_rate <= self.immediate_max_percent:
                                print(f"  ⚡ 利润率 {profit_rate:.2f}% 在阈值，启动即时执行线程")
                                self._spawn_execute_thread(immediate_opportunities[-1])
                        except Exception as e:
                            print(f"⚠️ 无法启动即时执行线程: {e}")
            
            # 潜在套利: 挂 Opinion NO bid，如果成交，在 Polymarket 买入 YES ask
            if opinion_no_book and opinion_no_book.bids and poly_yes_book and poly_yes_book.asks:
                pair = self._find_best_valid_bid_ask_pair(
                    opinion_no_book.bids,
                    poly_yes_book.asks,
                    THRESHOLD_PRICE,
                    THRESHOLD_SIZE
                )
                
                if pair:
                    op_no_bid, pm_yes_ask = pair
                    cost = op_no_bid.price + pm_yes_ask.price
                    available_size = pm_yes_ask.size  # 只看 ask 的数量
                    profit_rate = ((1.0 - cost) / cost * 100)
                    
                    pending_opportunities.append({
                        'match': match,
                        'type': 'pending',
                        'strategy': 'opinion_no_bid_poly_yes_ask',
                        'name': '潜在套利: Opinion NO bid → Polymarket YES ask',
                        'cost': cost,
                        'profit_rate': profit_rate,
                        'min_size': available_size,
                        'first_platform': 'opinion',
                        'first_token': match.opinion_no_token,
                        'first_price': op_no_bid.price,
                        'first_side': OrderSide.BUY,
                        'second_platform': 'polymarket',
                        'second_token': match.polymarket_yes_token,
                        'second_price': pm_yes_ask.price,
                        'second_side': BUY,
                        'opinion_yes_book': opinion_yes_book,
                        'opinion_no_book': opinion_no_book,
                        'poly_yes_book': poly_yes_book,
                        'poly_no_book': poly_no_book,
                    })
                    print(f"  ✓ 发现潜在套利: Opinion NO bid → Poly YES ask, 成本=${cost:.4f}, 利润率={profit_rate:.2f}%")
            
            # ========== 策略3: Polymarket YES vs Opinion NO ==========
            # 立即套利: 买 Polymarket YES ask + 买 Opinion NO ask = 策略2
            
            # 潜在套利: 挂 Polymarket YES bid，如果成交，在 Opinion 买入 NO ask
            # 潜在套利: 挂 Polymarket YES bid，如果成交，在 Opinion 买入 NO ask
            if poly_yes_book and poly_yes_book.bids and opinion_no_book and opinion_no_book.asks:
                pair = self._find_best_valid_bid_ask_pair(
                    poly_yes_book.bids,
                    opinion_no_book.asks,
                    THRESHOLD_PRICE,
                    THRESHOLD_SIZE
                )
                
                if pair:
                    pm_yes_bid, op_no_ask = pair
                    cost = pm_yes_bid.price + op_no_ask.price
                    available_size = op_no_ask.size  # 只看 ask 的数量
                    profit_rate = ((1.0 - cost) / cost * 100)
                    
                    pending_opportunities.append({
                        'match': match,
                        'type': 'pending',
                        'strategy': 'poly_yes_bid_opinion_no_ask',
                        'name': '潜在套利: Polymarket YES bid → Opinion NO ask',
                        'cost': cost,
                        'profit_rate': profit_rate,
                        'min_size': available_size,
                        'first_platform': 'polymarket',
                        'first_token': match.polymarket_yes_token,
                        'first_price': pm_yes_bid.price,
                        'first_side': BUY,
                        'second_platform': 'opinion',
                        'second_token': match.opinion_no_token,
                        'second_price': op_no_ask.price,
                        'second_side': OrderSide.BUY,
                        'opinion_yes_book': opinion_yes_book,
                        'opinion_no_book': opinion_no_book,
                        'poly_yes_book': poly_yes_book,
                        'poly_no_book': poly_no_book,
                    })
                    print(f"  ✓ 发现潜在套利: Poly YES bid → Opinion NO ask, 成本=${cost:.4f}, 利润率={profit_rate:.2f}%")
            
            # ========== 策略4: Polymarket NO vs Opinion YES ==========
            # 立即套利: 买 Polymarket NO ask + 买 Opinion YES ask=策略1
            
            # 潜在套利: 挂 Polymarket NO bid，如果成交，在 Opinion 买入 YES ask
            if poly_no_book and poly_no_book.bids and opinion_yes_book and opinion_yes_book.asks:
                pair = self._find_best_valid_bid_ask_pair(
                    poly_no_book.bids,
                    opinion_yes_book.asks,
                    THRESHOLD_PRICE,
                    THRESHOLD_SIZE
                )
                
                if pair:
                    pm_no_bid, op_yes_ask = pair
                    cost = pm_no_bid.price + op_yes_ask.price
                    available_size = op_yes_ask.size  # 只看 ask 的数量
                    profit_rate = ((1.0 - cost) / cost * 100)
                    
                    pending_opportunities.append({
                        'match': match,
                        'type': 'pending',
                        'strategy': 'poly_no_bid_opinion_yes_ask',
                        'name': '潜在套利: Polymarket NO bid → Opinion YES ask',
                        'cost': cost,
                        'profit_rate': profit_rate,
                        'min_size': available_size,
                        'first_platform': 'polymarket',
                        'first_token': match.polymarket_no_token,
                        'first_price': pm_no_bid.price,
                        'first_side': BUY,
                        'second_platform': 'opinion',
                        'second_token': match.opinion_yes_token,
                        'second_price': op_yes_ask.price,
                        'second_side': OrderSide.BUY,
                        'opinion_yes_book': opinion_yes_book,
                        'opinion_no_book': opinion_no_book,
                        'poly_yes_book': poly_yes_book,
                        'poly_no_book': poly_no_book,
                    })
                    print(f"  ✓ 发现潜在套利: Poly NO bid → Opinion YES ask, 成本=${cost:.4f}, 利润率={profit_rate:.2f}%")
            
            time.sleep(0.2)  # 避免请求过快
        
        # 合并所有套利机会并分类显示
        if not immediate_opportunities and not pending_opportunities:
            print(f"\n❌ 未发现满足条件的套利机会")
            print(f"   条件: 成本 < ${THRESHOLD_PRICE:.2f}, 最小数量 > {THRESHOLD_SIZE}")
            return
        
        print(f"\n{'='*100}")
        print(f"套利机会总结")
        print(f"{'='*100}")
        print(f"立即套利（ask+ask）: {len(immediate_opportunities)} 个")
        print(f"潜在套利（bid+ask）: {len(pending_opportunities)} 个")
        print(f"{'='*100}\n")
        
        # 按利润率排序
        immediate_opportunities.sort(key=lambda x: x['profit_rate'], reverse=True)
        pending_opportunities.sort(key=lambda x: x['profit_rate'], reverse=True)
        
        # 显示所有套利机会
        all_opportunities = []
        
        if immediate_opportunities:
            print(f"【立即套利机会】 - 两个平台都用 ask 价买入，立即执行\n")
            for i, opp in enumerate(immediate_opportunities, 1):
                all_opportunities.append(opp)
                idx = len(all_opportunities)
                print(f"{idx}. [{opp['match'].question[:60]}...]")
                print(f"   {opp['name']}")
                print(f"   成本: ${opp['cost']:.4f} | 利润率: {opp['profit_rate']:.2f}% | 可用数量: {opp['min_size']:.2f}")
                print()
        
        if pending_opportunities:
            print(f"【潜在套利机会】 - 在一个平台挂 bid 单，等成交后在另一平台用 ask 价对冲\n")
            for i, opp in enumerate(pending_opportunities, 1):
                all_opportunities.append(opp)
                idx = len(all_opportunities)
                print(f"{idx}. [{opp['match'].question[:60]}...]")
                print(f"   {opp['name']}")
                print(f"   成本: ${opp['cost']:.4f} | 利润率: {opp['profit_rate']:.2f}% | 可用数量: {opp['min_size']:.2f}")
                print()
        
        # 如果是非交互模式，打印完摘要后退出
        if non_interactive:
            print(f"\n✅ 扫描完成 (非交互模式)")
            return
        
        # 用户选择
        try:
            choice_input = input(f"\n请选择要执行的套利 (1-{len(all_opportunities)}) 或按 Enter 选择第一个，'q' 退出: ").strip()
            
            if choice_input.lower() == 'q':
                print("已取消")
                return
            
            if choice_input == "":
                choice_idx = 0
            else:
                choice_idx = int(choice_input) - 1
            
            if choice_idx < 0 or choice_idx >= len(all_opportunities):
                print("❌ 无效选择")
                return
        
        except (ValueError, KeyboardInterrupt):
            print("\n❌ 输入无效或用户取消")
            return
        
        strategy = all_opportunities[choice_idx]
        match = strategy['match']
        
        print(f"\n{'='*100}")
        print(f"选择的套利机会:")
        print(f"{'='*100}")
        print(f"市场: {match.question}")
        print(f"策略: {strategy['name']}")
        print(f"成本: ${strategy['cost']:.4f}")
        print(f"利润率: {strategy['profit_rate']:.2f}%")
        print(f"可用数量: {strategy['min_size']:.2f}")
        print(f"{'='*100}\n")
        
        # 显示详细订单簿
        print("📊 当前订单簿:\n")
        self._print_orderbook_levels("Opinion YES", strategy['opinion_yes_book'])
        self._print_orderbook_levels("Opinion NO", strategy['opinion_no_book'])
        self._print_orderbook_levels("Poly YES", strategy['poly_yes_book'])
        self._print_orderbook_levels("Poly NO", strategy['poly_no_book'])
        
        # 确认执行
        confirm = input(f"\n确认执行此套利交易? (y/n): ").strip().lower()
        if confirm != 'y':
            print("已取消")
            return
        
        # 启动账户监控
        self._ensure_account_monitors()
        
        # 根据套利类型执行不同的流程
        target_amount = THRESHOLD_SIZE  # 目标数量（期望最终得到的数量）
        
        if strategy['type'] == 'immediate':
            # 立即套利：两个平台都用 ask 价立即买入
            print(f"\n{'='*80}")
            print("执行立即套利 - 两个平台同时买入")
            print(f"{'='*80}\n")
            
            # 计算第一个平台的下单数量
            first_order_size, first_effective_size = self.get_order_size_for_platform(
                strategy['first_platform'],
                strategy['first_price'],
                target_amount
            )
            
            # 计算第二个平台的下单数量（需要匹配第一个平台的实际数量）
            second_order_size, second_effective_size = self.get_order_size_for_platform(
                strategy['second_platform'],
                strategy['second_price'],
                first_effective_size,  # 使用第一个平台的实际数量
                is_hedge=True
            )
            
            try:
                # 第一个平台下单
                print(f"1️⃣ 在 {strategy['first_platform'].upper()} 下市价单（限价 = ask 价）...")
                print(f"   Token: {strategy['first_token'][:20]}...")
                print(f"   价格: ${strategy['first_price']:.4f}")
                print(f"   下单数量: {first_order_size:.2f}")
                print(f"   预期实际数量: {first_effective_size:.2f}")
                
                if strategy['first_platform'] == 'opinion':
                    order1 = PlaceOrderDataInput(
                        marketId=match.opinion_market_id,
                        tokenId=str(strategy['first_token']),
                        side=strategy['first_side'],
                        orderType=LIMIT_ORDER,
                        price=str(strategy['first_price']),
                        makerAmountInBaseToken=str(first_order_size)
                    )
                    result1 = self.opinion_client.place_order(order1)
                    if result1.errno != 0:
                        print(f"❌ {strategy['first_platform'].upper()} 下单失败: {result1.errmsg}")
                        return
                    print(f"✅ {strategy['first_platform'].upper()} 订单已提交")
                else:
                    order1 = OrderArgs(
                        token_id=strategy['first_token'],
                        price=strategy['first_price'],
                        size=first_order_size,
                        side=strategy['first_side']
                    )
                    signed_order1 = self.polymarket_client.create_order(order1)
                    result1 = self.polymarket_client.post_order(signed_order1, OrderType.GTC)
                    print(f"✅ {strategy['first_platform'].upper()} 订单已提交")
                
                # 第二个平台下单
                print(f"\n2️⃣ 在 {strategy['second_platform'].upper()} 下市价单（限价 = ask 价）...")
                print(f"   Token: {strategy['second_token'][:20]}...")
                print(f"   价格: ${strategy['second_price']:.4f}")
                print(f"   下单数量: {second_order_size:.2f}")
                print(f"   预期实际数量: {second_effective_size:.2f}")
                
                if strategy['second_platform'] == 'opinion':
                    order2 = PlaceOrderDataInput(
                        marketId=match.opinion_market_id,
                        tokenId=str(strategy['second_token']),
                        side=strategy['second_side'],
                        orderType=LIMIT_ORDER,
                        price=str(strategy['second_price']),
                        makerAmountInBaseToken=str(second_order_size)
                    )
                    result2 = self.opinion_client.place_order(order2)
                    if result2.errno != 0:
                        print(f"❌ {strategy['second_platform'].upper()} 下单失败: {result2.errmsg}")
                        return
                    print(f"✅ {strategy['second_platform'].upper()} 订单已提交")
                else:
                    order2 = OrderArgs(
                        token_id=strategy['second_token'],
                        price=strategy['second_price'],
                        size=second_order_size,
                        side=strategy['second_side']
                    )
                    signed_order2 = self.polymarket_client.create_order(order2)
                    result2 = self.polymarket_client.post_order(signed_order2, OrderType.GTC)
                    print(f"✅ {strategy['second_platform'].upper()} 订单已提交")
                
                print(f"\n{'='*80}")
                print("✅ 立即套利执行完成！")
                print(f"预期总成本: ${strategy['cost']:.4f}")
                print(f"预期利润率: {strategy['profit_rate']:.2f}%")
                print(f"{'='*80}\n")
                
            except Exception as e:
                print(f"\n❌ 执行立即套利时出错: {e}")
                traceback.print_exc()
            
            return
        
        # 潜在套利：先挂 bid 单，等成交后用 ask 价对冲
        print(f"\n{'='*80}")
        print("执行潜在套利 - 先挂 bid 单，等成交后对冲")
        print(f"{'='*80}\n")
        
        # 计算首单的下单数量
        first_order_size, first_effective_size = self.get_order_size_for_platform(
            strategy['first_platform'],
            strategy['first_price'],
            target_amount
        )
        
        first_order_id = None
        
        try:
            print(f"\n📝 在 {strategy['first_platform'].upper()} 下限价单...")
            print(f"   Token: {strategy['first_token'][:20]}...")
            print(f"   价格: ${strategy['first_price']:.4f}")
            print(f"   下单数量: {first_order_size:.2f}")
            print(f"   预期实际数量: {first_effective_size:.2f}")
            
            if strategy['first_platform'] == 'opinion':
                # Opinion 下单
                order = PlaceOrderDataInput(
                    marketId=match.opinion_market_id,
                    tokenId=str(strategy['first_token']),
                    side=strategy['first_side'],
                    orderType=LIMIT_ORDER,
                    price=str(strategy['first_price']),
                    makerAmountInBaseToken=str(first_order_size)
                )
                
                result = self.opinion_client.place_order(order)
                
                if result.errno != 0:
                    print(f"❌ 下单失败: {result.errmsg}")
                    return
                
                first_order_id = result.result.order_data.order_id
                print(f"✅ 订单已提交，订单 ID: {first_order_id}")
                
            else:
                # Polymarket 下单
                order = OrderArgs(
                    token_id=strategy['first_token'],
                    price=strategy['first_price'],
                    size=first_order_size,
                    side=strategy['first_side']
                )
                
                signed_order = self.polymarket_client.create_order(order)
                result = self.polymarket_client.post_order(signed_order, OrderType.GTC)
                
                first_order_id = result.get('orderID') or result.get('order_id')
                print(f"✅ 订单已提交，订单 ID: {first_order_id}")
            
            if not first_order_id:
                print("❌ 无法获取订单 ID")
                return
            
            # 刷新账户状态
            self._refresh_account_state_snapshot(strategy['first_platform'])
            
            # 使用标志位和锁来协调两个监控线程
            stop_monitoring = threading.Event()
            order_filled = threading.Event()
            filled_amount = [0.0]  # 使用列表以便在线程间共享
            monitoring_lock = threading.Lock()
            
            def monitor_orderbook():
                """监控订单簿，如果条件不满足则撤单；如果有人挂更优价格且新价格满足条件，则撤单重挂"""
                print("\n🔍 启动订单簿监控线程...")
                
                nonlocal first_order_id  # 允许修改外层的 first_order_id
                current_bid_price = strategy['first_price']  # 跟踪当前挂单价格
                
                while not stop_monitoring.is_set() and not order_filled.is_set():
                    time.sleep(10)
                    
                    if stop_monitoring.is_set() or order_filled.is_set():
                        break
                    
                    # 获取最新订单簿
                    first_book = self.get_opinion_orderbook(strategy['first_token']) if strategy['first_platform'] == 'opinion' else self.get_polymarket_orderbook(strategy['first_token'])
                    second_book = self.get_polymarket_orderbook(strategy['second_token']) if strategy['second_platform'] == 'polymarket' else self.get_opinion_orderbook(strategy['second_token'])
                    
                    # 检查条件
                    if not first_book or not second_book:
                        continue
                    
                    # 使用与检测套利机会相同的逻辑：检查多档订单簿
                    if not first_book.bids or not second_book.asks:
                        continue
                    
                    # 获取第一平台最优 bid 价格
                    best_first_bid = first_book.best_bid()
                    if not best_first_bid or best_first_bid.price is None:
                        continue
                    
                    # 检查是否有人在更优价格挂单（价格高于我们的挂单价格）
                    if best_first_bid.price > current_bid_price:
                        print(f"\n⚠️ 检测到更优挂单: 买1价格 ${best_first_bid.price:.4f} > 我的挂单 ${current_bid_price:.4f}")
                        
                        # 检查使用新价格是否依然满足套利条件
                        best_second_ask = second_book.best_ask()
                        if not best_second_ask or best_second_ask.price is None or best_second_ask.size is None:
                            continue
                        
                        new_cost = best_first_bid.price + best_second_ask.price
                        
                        # 检查新价格是否满足套利条件
                        if new_cost < THRESHOLD_PRICE and best_second_ask.size > THRESHOLD_SIZE:
                            print(f"✓ 新价格满足条件: 成本 ${new_cost:.4f} < ${THRESHOLD_PRICE:.2f}, 数量 {best_second_ask.size:.2f} > {THRESHOLD_SIZE}")
                            print(f"🔄 准备撤销旧订单并以新价格重新挂单...")
                            
                            with monitoring_lock:
                                if order_filled.is_set():
                                    print("   订单已成交，不执行操作")
                                    break
                                
                                try:
                                    # 1. 撤销旧订单
                                    print(f"📝 撤销旧订单 (ID: {first_order_id})...")
                                    if strategy['first_platform'] == 'opinion':
                                        cancel_result = self.opinion_client.cancel_order(first_order_id)
                                        if cancel_result.errno == 0:
                                            print("✅ Opinion 旧订单已撤销")
                                        else:
                                            print(f"⚠️ Opinion 撤单失败: {cancel_result.errmsg}")
                                            continue
                                    else:
                                        cancel_result = self.polymarket_client.cancel(first_order_id)
                                        print("✅ Polymarket 旧订单已撤销")
                                    
                                    time.sleep(1)  # 等待撤单确认
                                    
                                    # 2. 以新价格重新挂单
                                    new_price = best_first_bid.price
                                    print(f"📝 以新价格 ${new_price:.4f} 重新挂单...")
                                    
                                    # 重新计算下单数量（因为价格变了，手续费也会变）
                                    new_order_size, new_effective_size = self.get_order_size_for_platform(
                                        strategy['first_platform'],
                                        new_price,
                                        target_amount
                                    )
                                    
                                    if strategy['first_platform'] == 'opinion':
                                        new_order = PlaceOrderDataInput(
                                            marketId=match.opinion_market_id,
                                            tokenId=str(strategy['first_token']),
                                            side=strategy['first_side'],
                                            orderType=LIMIT_ORDER,
                                            price=str(new_price),
                                            makerAmountInBaseToken=str(new_order_size)
                                        )
                                        
                                        result = self.opinion_client.place_order(new_order)
                                        
                                        if result.errno != 0:
                                            print(f"❌ 重新挂单失败: {result.errmsg}")
                                            stop_monitoring.set()
                                            break
                                        
                                        first_order_id = result.result.order_data.order_id
                                        current_bid_price = new_price
                                        print(f"✅ 新订单已提交，订单 ID: {first_order_id}, 价格: ${new_price:.4f}")
                                        
                                    else:
                                        new_order = OrderArgs(
                                            token_id=strategy['first_token'],
                                            price=new_price,
                                            size=new_order_size,
                                            side=strategy['first_side']
                                        )
                                        
                                        signed_order = self.polymarket_client.create_order(new_order)
                                        result = self.polymarket_client.post_order(signed_order, OrderType.GTC)
                                        
                                        first_order_id = result.get('orderID') or result.get('order_id')
                                        current_bid_price = new_price
                                        print(f"✅ 新订单已提交，订单 ID: {first_order_id}, 价格: ${new_price:.4f}")
                                    
                                    # 刷新账户状态
                                    self._refresh_account_state_snapshot(strategy['first_platform'])
                                    
                                except Exception as e:
                                    print(f"❌ 撤单重挂异常: {e}")
                                    traceback.print_exc()
                                    stop_monitoring.set()
                                    break
                        else:
                            print(f"✗ 新价格不满足条件: 成本 ${new_cost:.4f} >= ${THRESHOLD_PRICE:.2f} 或数量不足")
                    
                    # 使用 _find_best_valid_bid_ask_pair 检查是否还有满足条件的配对
                    valid_pair = self._find_best_valid_bid_ask_pair(
                        first_book.bids,
                        second_book.asks,
                        THRESHOLD_PRICE,
                        THRESHOLD_SIZE
                    )
                    
                    condition_met = valid_pair is not None
                    
                    if not condition_met:
                        print("\n⚠️ 订单簿条件不再满足，准备撤单...")
                        
                        with monitoring_lock:
                            if order_filled.is_set():
                                print("   订单已成交，不执行撤单")
                                break
                            
                            # 撤单
                            try:
                                if strategy['first_platform'] == 'opinion':
                                    cancel_result = self.opinion_client.cancel_order(first_order_id)
                                    if cancel_result.errno == 0:
                                        print("✅ Opinion 订单已撤销")
                                    else:
                                        print(f"⚠️ Opinion 撤单失败: {cancel_result.errmsg}")
                                else:
                                    cancel_result = self.polymarket_client.cancel(first_order_id)
                                    print("✅ Polymarket 订单已撤销")
                                    
                            except Exception as e:
                                print(f"❌ 撤单异常: {e}")
                        
                        stop_monitoring.set()
                        break
                
                print("🔍 订单簿监控线程结束")
            
            #TODO 改为通过成交状态来检测订单情况
            def monitor_order_status():
                """监控订单状态，如果成交则立即对冲"""
                print("📊 启动订单状态监控线程...")
                
                while not stop_monitoring.is_set():
                    time.sleep(10)
                    
                    if stop_monitoring.is_set():
                        break
                    
                    # 刷新账户状态
                    self._refresh_account_state_snapshot(strategy['first_platform'],force_direct=True)
                    
                    # 检查订单状态
                    cached_state = self._check_cached_order_state(strategy['first_platform'], first_order_id)
                    print(cached_state)
                    
                    if cached_state:
                        status = cached_state.get('status')
                        filled = cached_state.get('filled', 0.0)
                        total = cached_state.get('total', 200.0)
                        
                        if filled and filled > 0:
                            with monitoring_lock:
                                if filled > filled_amount[0]:
                                    newly_filled = filled - filled_amount[0]
                                    filled_amount[0] = filled
                                    
                                    print(f"\n✅ 检测到成交: {filled:.2f} / {total:.2f} shares")
                                    print(f"   新增成交: {newly_filled:.2f} shares")
                                    
                                    # 计算对冲订单的数量
                                    # 如果首单在 Opinion,需要考虑手续费后的实际数量
                                    if strategy['first_platform'] == 'opinion':
                                        # Opinion 首单: 新增成交量已经是扣除手续费后的实际数量
                                        hedge_target_amount = newly_filled
                                    else:
                                        # Polymarket 首单: 新增成交量就是实际数量
                                        hedge_target_amount = newly_filled
                                    
                                    # 计算对冲单的下单数量
                                    hedge_order_size, hedge_effective_size = self.get_order_size_for_platform(
                                        strategy['second_platform'],
                                        strategy['second_price'],
                                        hedge_target_amount,
                                        is_hedge=True
                                    )
                                    
                                    # 立即在第二平台对冲
                                    print(f"\n🔄 在 {strategy['second_platform'].upper()} 执行对冲...")
                                    print(f"   对冲目标数量: {hedge_target_amount:.2f}")
                                    print(f"   对冲下单数量: {hedge_order_size:.2f}")
                                    
                                    try:
                                        if strategy['second_platform'] == 'opinion':
                                            hedge_order = PlaceOrderDataInput(
                                                marketId=match.opinion_market_id,
                                                tokenId=str(strategy['second_token']),
                                                side=strategy['second_side'],
                                                orderType=LIMIT_ORDER,
                                                price=str(strategy['second_price']),
                                                makerAmountInBaseToken=str(hedge_order_size)
                                            )
                                            
                                            hedge_result = self.opinion_client.place_order(hedge_order)
                                            
                                            if hedge_result.errno == 0:
                                                print(f"✅ Opinion 对冲订单已提交")
                                            else:
                                                print(f"❌ Opinion 对冲失败: {hedge_result.errmsg}")
                                        else:
                                            hedge_order = OrderArgs(
                                                token_id=strategy['second_token'],
                                                price=strategy['second_price'],
                                                size=hedge_order_size,
                                                side=strategy['second_side']
                                            )
                                            
                                            signed_hedge = self.polymarket_client.create_order(hedge_order)
                                            hedge_result = self.polymarket_client.post_order(signed_hedge, OrderType.GTC)
                                            
                                            print(f"✅ Polymarket 对冲订单已提交")
                                    
                                    except Exception as e:
                                        print(f"❌ 对冲异常: {e}")
                                        import traceback
                                        traceback.print_exc()
                        
                        # 检查是否完全成交
                        if self._status_is_filled(status, filled, total):
                            print(f"\n🎉 订单完全成交!")
                            order_filled.set()
                            stop_monitoring.set()
                            break
                
                print("📊 订单状态监控线程结束")
            
            # 启动监控线程
            orderbook_thread = threading.Thread(target=monitor_orderbook, daemon=True)
            status_thread = threading.Thread(target=monitor_order_status, daemon=True)
            
            orderbook_thread.start()
            status_thread.start()
            
            # 等待监控完成
            print("\n⏳ 监控中... (按 Ctrl+C 手动停止)")
            try:
                orderbook_thread.join()
                status_thread.join()
            except KeyboardInterrupt:
                print("\n\n⚠️ 用户中断监控")
                stop_monitoring.set()
                
                # 等待线程结束
                orderbook_thread.join(timeout=5)
                status_thread.join(timeout=5)
            
            print(f"\n{'='*100}")
            print("执行完成!")
            print(f"{'='*100}\n")
            
        except Exception as e:
            print(f"\n❌ 执行过程中发生错误: {e}")
            import traceback
            traceback.print_exc()

    # ==================== 即时执行线程支持 ====================
    def _spawn_execute_thread(self, opportunity: Dict[str, Any]) -> None:
        """启动一个后台线程来执行给定的套利机会（非交互）。"""
        t = threading.Thread(target=self._execute_opportunity, args=(opportunity,), daemon=True)
        t.start()
        self._active_exec_threads.append(t)
        print(f"🧵 已启动即时执行线程 (线程数={len(self._active_exec_threads)})")

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
                # 计算第一个平台的下单数量(考虑手续费)
                first_order_size, first_effective_size = self.get_order_size_for_platform(
                    opp['first_platform'],
                    opp['first_price'],
                    order_size
                )
                
                # 计算第二个平台的下单数量(需要匹配第一个平台的实际数量)
                second_order_size, second_effective_size = self.get_order_size_for_platform(
                    opp['second_platform'],
                    opp['second_price'],
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
                            price=str(opp['first_price']),
                            makerAmountInBaseToken=str(first_order_size)
                        )
                        res1 = self.opinion_client.place_order(order1)
                        if getattr(res1, 'errno', 0) != 0:
                            print(f"❌ Opinion 下单失败: {getattr(res1,'errmsg', res1)}")
                        else:
                            print(f"✅ Opinion 订单提交成功 (即时执行)")
                    except Exception as e:
                        print(f"❌ Opinion 下单异常: {e}")
                else:
                    try:
                        from py_clob_client.clob_types import OrderArgs, OrderType
                        order1 = OrderArgs(
                            token_id=opp['first_token'],
                            price=opp['first_price'],
                            size=first_order_size,
                            side=opp['first_side']
                        )
                        signed1 = self.polymarket_client.create_order(order1)
                        res1 = self.polymarket_client.post_order(signed1, OrderType.GTC)
                        print(f"✅ Polymarket 订单提交成功 (即时执行): {res1}")
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
                            price=str(opp['second_price']),
                            makerAmountInBaseToken=str(second_order_size)
                        )
                        res2 = self.opinion_client.place_order(order2)
                        if getattr(res2, 'errno', 0) != 0:
                            print(f"❌ Opinion 下单失败: {getattr(res2,'errmsg', res2)}")
                        else:
                            print(f"✅ Opinion 对冲订单提交成功 (即时执行)")
                    except Exception as e:
                        print(f"❌ Opinion 对冲下单异常: {e}")
                else:
                    try:
                        from py_clob_client.clob_types import OrderArgs, OrderType
                        order2 = OrderArgs(
                            token_id=opp['second_token'],
                            price=opp['second_price'],
                            size=second_order_size,
                            side=opp['second_side']
                        )
                        signed2 = self.polymarket_client.create_order(order2)
                        res2 = self.polymarket_client.post_order(signed2, OrderType.GTC)
                        print(f"✅ Polymarket 对冲订单提交成功 (即时执行): {res2}")
                    except Exception as e:
                        print(f"❌ Polymarket 对冲下单异常: {e}")

                print("🟢 即时套利执行线程完成 (immediate)")
                return

            # Pending execution: place first order and monitor
            if opp.get('type') == 'pending':
                # 计算第一笔挂单的下单数量(考虑手续费)
                first_order_size, first_effective_size = self.get_order_size_for_platform(
                    opp['first_platform'],
                    opp['first_price'],
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
                            price=str(opp['first_price']),
                            makerAmountInBaseToken=str(first_order_size)
                        )
                        result = self.opinion_client.place_order(order)
                        if getattr(result, 'errno', 0) != 0:
                            print(f"❌ Opinion 下单失败: {getattr(result,'errmsg', result)}")
                            return
                        first_order_id = getattr(getattr(result, 'result', {}), 'order_data', None)
                        if first_order_id and hasattr(first_order_id, 'order_id'):
                            first_order_id = first_order_id.order_id
                        print(f"✅ Opinion 挂单已提交 (order_id={first_order_id})")
                    else:
                        from py_clob_client.clob_types import OrderArgs, OrderType
                        order = OrderArgs(
                            token_id=opp['first_token'],
                            price=opp['first_price'],
                            size=first_order_size,
                            side=opp['first_side']
                        )
                        signed = self.polymarket_client.create_order(order)
                        res = self.polymarket_client.post_order(signed, OrderType.GTC)
                        first_order_id = res.get('orderID') or res.get('order_id')
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
                                opp['second_price'],
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
                                        price=str(opp['second_price']),
                                        makerAmountInBaseToken=str(hedge_order_size)
                                    )
                                    res2 = self.opinion_client.place_order(order2)
                                    print(f"✅ Opinion 对冲订单提交: {getattr(res2,'errno',res2)}")
                                else:
                                    from py_clob_client.clob_types import OrderArgs, OrderType
                                    order2 = OrderArgs(
                                        token_id=opp['second_token'],
                                        price=opp['second_price'],
                                        size=hedge_order_size,
                                        side=opp['second_side']
                                    )
                                    signed2 = self.polymarket_client.create_order(order2)
                                    res2 = self.polymarket_client.post_order(signed2, OrderType.GTC)
                                    print(f"✅ Polymarket 对冲订单提交: {res2}")
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
            scanner.execute_arbitrage_pro(non_interactive=args.no_interactive)
            return
        
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
