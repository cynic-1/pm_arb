import os
import time
import asyncio
from dotenv import load_dotenv
from opinion_clob_sdk import Client
from opinion_clob_sdk.model import TopicType, TopicStatusFilter
from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
from opinion_clob_sdk.chain.py_order_utils.model.order_type import MARKET_ORDER, LIMIT_ORDER
from typing import Optional, Dict, List, Callable
import threading
from dataclasses import dataclass

# 加载环境变量
load_dotenv()

def retry_on_failure(max_retries=3, delay=1.0):
    """
    装饰器：在失败时重试
    
    Args:
        max_retries: 最大重试次数
        delay: 重试间隔（秒）
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        print(f"⚠️ 请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                        time.sleep(delay)
                    else:
                        print(f"❌ 请求失败，已达最大重试次数: {e}")
            raise last_exception
        return wrapper
    return decorator

@dataclass
class OrderbookData:
    """订单簿数据"""
    token_id: str
    best_bid: Optional[Dict] = None
    best_ask: Optional[Dict] = None
    bids: List[Dict] = None
    asks: List[Dict] = None
    timestamp: float = 0

class OpinionTrader:
    """Opinion 预测市场交易类"""
    
    def __init__(self):
        """初始化交易客户端"""
        self.client = Client(
            host=os.getenv('OP_HOST', 'https://proxy.opinion.trade:8443'),
            apikey=os.getenv('OP_API_KEY'),
            chain_id=int(os.getenv('OP_CHAIN_ID', '56')),
            rpc_url=os.getenv('OP_RPC_URL'),
            private_key=os.getenv('OP_PRIVATE_KEY'),
            multi_sig_addr=os.getenv('OP_MULTI_SIG_ADDRESS'),
            conditional_tokens_addr=os.getenv('OP_CONDITIONAL_TOKEN_ADDR', '0xAD1a38cEc043e70E83a3eC30443dB285ED10D774'),
            multisend_addr=os.getenv('OP_MULTISEND_ADDR', '0x998739BFdAAdde7C933B942a68053933098f9EDa')
        )
        
        # 订单簿缓存
        self.orderbook_cache: Dict[str, OrderbookData] = {}
        
        # 监听线程控制
        self.monitoring_threads: Dict[str, threading.Thread] = {}
        self.stop_flags: Dict[str, threading.Event] = {}
        
        # 订单监听
        self.order_callbacks: List[Callable] = []
        self.my_orders_cache: Dict[str, Dict] = {}
        
        print("✓ OpinionTrader 初始化成功!")
    
    # ==================== 1. 获取市场 ====================
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def get_all_markets(
        self, 
        status: TopicStatusFilter = TopicStatusFilter.ACTIVATED,
        market_type: Optional[TopicType] = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        获取所有市场
        
        Args:
            status: 市场状态过滤 (ACTIVATED/RESOLVED/ALL)
            market_type: 市场类型 (BINARY/CATEGORICAL)
            limit: 每页数量
        
        Returns:
            市场列表
        """
        all_markets = []
        page = 1
        
        print(f"\n📊 获取市场列表 (状态: {status.value}, 类型: {market_type})...")
        
        while True:
            response = self.client.get_markets(
                status=status,
                topic_type=market_type,
                page=page,
                limit=limit
            )
            
            if response.errno != 0:
                raise Exception(f"获取市场失败: {response.errmsg}")
            
            markets = response.result.list
            if not markets:
                break
            
            all_markets.extend(markets)
            print(f"  - 第 {page} 页: {len(markets)} 个市场")
            
            if len(markets) < limit:
                break
            
            page += 1
        
        print(f"✓ 共获取 {len(all_markets)} 个市场\n")
        return all_markets
    
    def display_markets(self, markets: List[Dict], limit: int = 10):
        """
        显示市场信息
        
        Args:
            markets: 市场列表
            limit: 显示数量限制
        """
        print(f"\n{'='*80}")
        print(f"市场列表 (显示前 {min(limit, len(markets))} 个)")
        print(f"{'='*80}\n")
        
        for i, market in enumerate(markets[:limit], 1):
            print(f"{i}. 【Market #{market.market_id}】 {market.market_title}")
            print(f"   状态: {self._get_status_name(market.status)}")
            print(f"   交易量: {market.volume if hasattr(market, 'volume') else 'N/A'}")
            
            # 显示 Token IDs
            if hasattr(market, 'yes_token_id') and market.yes_token_id:
                print(f"   YES Token: {market.yes_token_id[:20]}...")
            if hasattr(market, 'no_token_id') and market.no_token_id:
                print(f"   NO Token:  {market.no_token_id[:20]}...")
            print()
    
    # ==================== 2. 监听订单簿 ====================
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def _fetch_orderbook(self, token_id: str) -> OrderbookData:
        """
        获取单次订单簿数据（内部方法，带重试）
        
        Args:
            token_id: Token ID
            
        Returns:
            订单簿数据
        """
        response = self.client.get_orderbook(token_id)
        
        if response.errno != 0:
            raise Exception(f"获取订单簿失败: {response.errmsg}")
        
        book = response.result
        
        # 对订单簿进行排序
        sorted_bids = sorted(book.bids, key=lambda x: float(x.price), reverse=True) if book.bids else []
        sorted_asks = sorted(book.asks, key=lambda x: float(x.price)) if book.asks else []
        
        # 构建数据
        return OrderbookData(
            token_id=token_id,
            best_bid=sorted_bids[0] if sorted_bids else None,
            best_ask=sorted_asks[0] if sorted_asks else None,
            bids=sorted_bids[:5],  # 前5档
            asks=sorted_asks[:5],
            timestamp=time.time()
        )
    
    def start_orderbook_monitor(
        self, 
        token_id: str, 
        interval: float = 2.0,
        callback: Optional[Callable] = None
    ):
        """
        开始监听订单簿变化
        
        Args:
            token_id: Token ID
            interval: 轮询间隔(秒)
            callback: 变化回调函数 callback(old_data, new_data)
        """
        if token_id in self.monitoring_threads:
            print(f"⚠️  Token {token_id[:20]}... 已在监听中")
            return
        
        stop_flag = threading.Event()
        self.stop_flags[token_id] = stop_flag
        
        def monitor_loop():
            print(f"🔍 开始监听订单簿: {token_id[:20]}...")
            
            while not stop_flag.is_set():
                try:
                    # 使用带重试的获取方法
                    new_data = self._fetch_orderbook(token_id)
                    
                    # 获取旧数据
                    old_data = self.orderbook_cache.get(token_id)
                    
                    # 检查是否有变化
                    if self._orderbook_changed(old_data, new_data):
                        self._display_orderbook_update(token_id, new_data)
                        
                        # 调用回调
                        if callback:
                            callback(old_data, new_data)
                    
                    # 更新缓存
                    self.orderbook_cache[token_id] = new_data
                    
                    time.sleep(interval)
                    
                except Exception as e:
                    print(f"❌ 订单簿监听错误: {e}")
                    time.sleep(interval)
            
            print(f"🛑 停止监听订单簿: {token_id[:20]}...")
        
        thread = threading.Thread(target=monitor_loop, daemon=True)
        thread.start()
        self.monitoring_threads[token_id] = thread
    
    def stop_orderbook_monitor(self, token_id: str):
        """停止监听订单簿"""
        if token_id in self.stop_flags:
            self.stop_flags[token_id].set()
            self.monitoring_threads.pop(token_id, None)
            self.stop_flags.pop(token_id, None)
            print(f"✓ 已停止监听: {token_id[:20]}...")
    
    def _orderbook_changed(self, old: Optional[OrderbookData], new: OrderbookData) -> bool:
        """检查订单簿是否变化"""
        if old is None:
            return True
        
        # 检查最优买卖价 - 使用属性访问
        old_bid_price = old.best_bid.price if old.best_bid else None
        new_bid_price = new.best_bid.price if new.best_bid else None
        old_ask_price = old.best_ask.price if old.best_ask else None
        new_ask_price = new.best_ask.price if new.best_ask else None
        
        return old_bid_price != new_bid_price or old_ask_price != new_ask_price

    def _display_orderbook_update(self, token_id: str, data: OrderbookData):
        """显示订单簿更新"""
        print(f"\n📖 订单簿更新 [{token_id[:20]}...] - {time.strftime('%H:%M:%S')}")
        
        if data.best_bid:
            size = getattr(data.best_bid, 'size', getattr(data.best_bid, 'amount', 'N/A'))
            print(f"  🟢 最优买价: ${data.best_bid.price} x {size}")
        else:
            print(f"  🟢 最优买价: 无")
        
        if data.best_ask:
            size = getattr(data.best_ask, 'size', getattr(data.best_ask, 'amount', 'N/A'))
            print(f"  🔴 最优卖价: ${data.best_ask.price} x {size}")
        else:
            print(f"  🔴 最优卖价: 无")
        
        if data.best_bid and data.best_ask:
            spread = float(data.best_ask.price) - float(data.best_bid.price)
            print(f"  📊 价差: ${spread:.4f}")
    
    # ==================== 3. 挂单买卖 ====================
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def place_limit_order(
        self,
        market_id: int,
        token_id: str,
        side: OrderSide,
        price: str,
        amount_in_usdt: float = None,
        amount_in_tokens: float = None
    ) -> Dict:
        """
        挂限价单
        
        Args:
            market_id: 市场 ID
            token_id: Token ID
            side: OrderSide.BUY 或 OrderSide.SELL
            price: 限价价格 (如 "0.55")
            amount_in_usdt: USDT 数量 (买单推荐)
            amount_in_tokens: Token 数量 (卖单推荐)
        
        Returns:
            订单结果
        """
        if amount_in_usdt is None and amount_in_tokens is None:
            raise ValueError("必须指定 amount_in_usdt 或 amount_in_tokens")
        
        if amount_in_usdt is not None and amount_in_tokens is not None:
            raise ValueError("只能指定一个数量参数")
        
        order_data = PlaceOrderDataInput(
            marketId=market_id,
            tokenId=token_id,
            side=side,
            orderType=LIMIT_ORDER,
            price=price,
            makerAmountInQuoteToken=str(amount_in_usdt) if amount_in_usdt else None,
            makerAmountInBaseToken=str(amount_in_tokens) if amount_in_tokens else None
        )
        
        side_name = "买入" if side == OrderSide.BUY else "卖出"
        print(f"\n📝 挂限价{side_name}单...")
        print(f"  市场: {market_id} | 价格: ${price}")
        print(f"  数量: {amount_in_usdt or amount_in_tokens} {'USDT' if amount_in_usdt else 'Tokens'}")
        
        result = self.client.place_order(order_data, check_approval=True)
        
        if result.errno != 0:
            raise Exception(f"下单失败: {result.errmsg}")
        
        print(result)
        order_id = result.result.order_id if hasattr(result.result, 'order_id') else 'N/A'
        print(f"✓ 订单已提交! Order ID: {order_id}")
        
        # 缓存订单
        self.my_orders_cache[order_id] = {
            'order_id': order_id,
            'market_id': market_id,
            'side': side,
            'price': price,
            'status': 'open',
            'timestamp': time.time()
        }
        
        return result
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def place_market_order(
        self,
        market_id: int,
        token_id: str,
        side: OrderSide,
        amount_in_usdt: float = None,
        amount_in_tokens: float = None
    ) -> Dict:
        """
        挂市价单
        
        Args:
            market_id: 市场 ID
            token_id: Token ID
            side: OrderSide.BUY 或 OrderSide.SELL
            amount_in_usdt: USDT 数量 (买单使用)
            amount_in_tokens: Token 数量 (卖单使用)
        
        Returns:
            订单结果
        """
        # 市价买单必须用 USDT，市价卖单必须用 Tokens
        if side == OrderSide.BUY and amount_in_usdt is None:
            raise ValueError("市价买单必须指定 amount_in_usdt")
        if side == OrderSide.SELL and amount_in_tokens is None:
            raise ValueError("市价卖单必须指定 amount_in_tokens")
        
        order_data = PlaceOrderDataInput(
            marketId=market_id,
            tokenId=token_id,
            side=side,
            orderType=MARKET_ORDER,
            price="0",  # 市价单价格为 0
            makerAmountInQuoteToken=str(amount_in_usdt) if amount_in_usdt else None,
            makerAmountInBaseToken=str(amount_in_tokens) if amount_in_tokens else None
        )
        
        side_name = "买入" if side == OrderSide.BUY else "卖出"
        print(f"\n⚡ 挂市价{side_name}单...")
        print(f"  市场: {market_id}")
        print(f"  数量: {amount_in_usdt or amount_in_tokens} {'USDT' if amount_in_usdt else 'Tokens'}")
        
        result = self.client.place_order(order_data, check_approval=True)
        
        if result.errno != 0:
            raise Exception(f"市价单失败: {result.errmsg}")
        
        print(f"✓ 市价单已执行!")
        return result
    
    # ==================== 4. 监听订单状态 ====================
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def _fetch_my_orders(self, status: str = "", limit: int = 50):
        """
        获取我的订单（内部方法，带重试）
        
        Args:
            status: 订单状态过滤
            limit: 数量限制
            
        Returns:
            订单列表
        """
        response = self.client.get_my_orders(status=status, limit=limit)
        
        if response.errno != 0:
            raise Exception(f"获取订单失败: {response.errmsg}")
        
        return response.result.list
    
    def start_order_monitor(self, interval: float = 3.0):
        """
        开始监听自己的订单状态
        
        Args:
            interval: 轮询间隔(秒)
        """
        if 'orders' in self.monitoring_threads:
            print("⚠️  订单监听已在运行中")
            return
        
        stop_flag = threading.Event()
        self.stop_flags['orders'] = stop_flag
        
        def monitor_loop():
            print("🔍 开始监听订单状态...")
            
            while not stop_flag.is_set():
                try:
                    # 使用带重试的获取方法
                    orders = self._fetch_my_orders(status="", limit=50)
                    
                    for order in orders:
                        order_id = order.order_id
                        
                        # 检查状态变化
                        if order_id in self.my_orders_cache:
                            old_status = self.my_orders_cache[order_id].get('status')
                            new_status = order.status
                            
                            if old_status != new_status:
                                self._notify_order_change(order, old_status, new_status)
                                self.my_orders_cache[order_id]['status'] = new_status
                        else:
                            # 新订单
                            self.my_orders_cache[order_id] = {
                                'order_id': order_id,
                                'status': order.status,
                                'timestamp': time.time()
                            }
                    
                    time.sleep(interval)
                    
                except Exception as e:
                    print(f"❌ 订单监听错误: {e}")
                    time.sleep(interval)
            
            print("🛑 停止订单监听")
        
        thread = threading.Thread(target=monitor_loop, daemon=True)
        thread.start()
        self.monitoring_threads['orders'] = thread
    
    def stop_order_monitor(self):
        """停止订单监听"""
        if 'orders' in self.stop_flags:
            self.stop_flags['orders'].set()
            self.monitoring_threads.pop('orders', None)
            self.stop_flags.pop('orders', None)
            print("✓ 已停止订单监听")
    
    def _notify_order_change(self, order, old_status: str, new_status: str):
        """订单状态变化通知"""
        print(f"\n🔔 订单状态变化!")
        print(f"  订单 ID: {order.order_id}")
        print(f"  方向: {'买入' if order.side == 0 else '卖出'}")
        print(f"  价格: ${order.price}")
        print(f"  状态: {old_status} → {new_status}")
        
        if new_status == 'filled':
            print(f"  ✅ 订单已完全成交!")
        elif new_status == 'partial_filled':
            print(f"  ⏳ 订单部分成交")
        elif new_status == 'cancelled':
            print(f"  ❌ 订单已取消")
    
    # ==================== 辅助方法 ====================
    
    def _get_status_name(self, status: int) -> str:
        """获取状态名称"""
        status_map = {1: "创建", 2: "激活", 3: "解决中", 4: "已解决"}
        return status_map.get(status, f"未知({status})")
    
    def _get_type_name(self, market_type: int) -> str:
        """获取市场类型名称"""
        type_map = {0: "二元", 1: "分类"}
        return type_map.get(market_type, f"未知({market_type})")
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def get_my_balances(self):
        """查看账户余额"""
        response = self.client.get_my_balances()
        
        if response.errno != 0:
            raise Exception(f"获取余额失败: {response.errmsg}")
        
        print(response)
        print(f"\n💰 账户余额:")
        balance_data = response.result
        balances = balance_data.balances if hasattr(balance_data, 'balances') else []
        
        for balance in balances:
            print(f"  Token: {balance.quote_token}")
            print(f"    可用: {balance.available_balance}")
            print(f"    冻结: {balance.frozen_balance}")
            print(f"    总计: {balance.total_balance}")
    
    def cleanup(self):
        """清理资源"""
        print("\n🧹 清理资源...")
        
        # 停止所有监听
        for token_id in list(self.stop_flags.keys()):
            if token_id == 'orders':
                self.stop_order_monitor()
            else:
                self.stop_orderbook_monitor(token_id)
        
        print("✓ 清理完成")


# ==================== 使用示例 ====================

def main():
    """主函数示例"""
    
    # 初始化交易器
    trader = OpinionTrader()
    
    try:
        # 1. 获取所有激活的市场
        markets = trader.get_all_markets(
            status=TopicStatusFilter.ACTIVATED,
            market_type=TopicType.BINARY,
            limit=20
        )
        trader.display_markets(markets, limit=5)
        
        if not markets:
            print("没有可用市场")
            return
        
        # 选择第一个市场进行演示
        demo_market = markets[0]
        market_id = demo_market.market_id
        yes_token_id = demo_market.yes_token_id if hasattr(demo_market, 'yes_token_id') else None

        if not yes_token_id:
            print("⚠️  市场没有 YES token ID，无法演示")
            return
        
        print(f"\n使用市场 #{market_id} 进行演示")
        
        # 2. 监听订单簿变化
        trader.start_orderbook_monitor(
            token_id=yes_token_id,
            interval=2.0,
            callback=lambda old, new: print(f"  → 订单簿已更新")
        )
        
        # 3. 查看账户余额
        trader.get_my_balances()
        
        # 4. 开始监听订单状态
        trader.start_order_monitor(interval=3.0)
        
        # 5. 下单示例 (注释掉，避免实际交易)
        """
        # 限价买单
        trader.place_limit_order(
            market_id=market_id,
            token_id=yes_token_id,
            side=OrderSide.BUY,
            price="0.55",
            amount_in_usdt=10
        )
        
        # 限价卖单
        trader.place_limit_order(
            market_id=market_id,
            token_id=yes_token_id,
            side=OrderSide.SELL,
            price="0.65",
            amount_in_tokens=10
        )
        
        # 市价买单
        trader.place_market_order(
            market_id=market_id,
            token_id=yes_token_id,
            side=OrderSide.BUY,
            amount_in_usdt=5
        )
        """
        
        # 保持运行，监听订单簿和订单状态
        print(f"\n{'='*80}")
        print("系统正在运行，按 Ctrl+C 退出...")
        print(f"{'='*80}\n")
        
        while True:
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n\n收到退出信号...")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        trader.cleanup()


if __name__ == "__main__":
    main()