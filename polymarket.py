import os
import json
import time
import threading
from decimal import Decimal
from typing import List, Dict, Optional, Callable
from dotenv import load_dotenv
from websocket import WebSocketApp
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs, 
    MarketOrderArgs, 
    OrderType,
    OpenOrderParams,
    BookParams
)
from py_clob_client.order_builder.constants import BUY, SELL

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

class PolymarketTrader:
    """Polymarket 交易和监控系统"""
    
    def __init__(self):
        """初始化交易客户端"""
        # 从环境变量获取配置
        self.host = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
        self.wss_url = os.getenv("POLYMARKET_WSS", "wss://ws-subscriptions-clob.polymarket.com")
        self.gamma_api = os.getenv("GAMMA_API", "https://gamma-api.polymarket.com")
        self.chain_id = int(os.getenv("CHAIN_ID", "137"))
        
        # 获取认证信息
        private_key = os.getenv("PM_KEY")
        funder_address = os.getenv("PM_FUNDER")
        signature_type = int(os.getenv("SIGNATURE_TYPE", "1"))
        
        if not private_key:
            raise ValueError("PRIVATE_KEY 未在 .env 文件中设置")
        
        # 初始化 CLOB 客户端
        self.client = ClobClient(
            self.host,
            key=private_key,
            chain_id=self.chain_id,
            signature_type=signature_type,
            funder=funder_address
        )
        
        # 设置 API 凭证
        self.client.set_api_creds(self.client.create_or_derive_api_creds())
        
        # 获取 API 密钥用于 WebSocket 认证
        api_creds = self.client.create_or_derive_api_creds()
        self.api_key = api_creds.api_key
        self.api_secret = api_creds.api_secret
        self.api_passphrase = api_creds.api_passphrase
        
        # WebSocket 连接
        self.market_ws: Optional[WebSocketApp] = None
        self.user_ws: Optional[WebSocketApp] = None
        
        # 回调函数
        self.orderbook_callbacks: Dict[str, List[Callable]] = {}
        self.order_callbacks: List[Callable] = []
        self.trade_callbacks: List[Callable] = []
        
        print(f"✅ Polymarket 交易客户端初始化成功")
        print(f"📡 连接到: {self.host}")
    
    # ==================== 市场数据 ====================
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def get_all_markets(self, limit: int = 100, active_only: bool = True) -> List[Dict]:
        """
        获取所有市场
        
        Args:
            limit: 返回的市场数量限制
            active_only: 是否只返回活跃市场
            
        Returns:
            市场列表
        """
        import requests
        
        params = {}
        if limit:
            params['limit'] = limit
        if active_only:
            params['active'] = 'true'
            params['closed'] = 'false'
        
        params['order'] = ['volume']
        params['ascending'] = 'false'

        response = requests.get(f"{self.gamma_api}/markets", params=params)
        response.raise_for_status()
        
        markets = response.json()
        print(f"📊 获取到 {len(markets)} 个市场")
        return markets
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def get_market_by_slug(self, slug: str) -> Optional[Dict]:
        """
        通过 slug 获取市场信息
        
        Args:
            slug: 市场的 slug 标识符
            
        Returns:
            市场详情
        """
        import requests
        
        response = requests.get(f"{self.gamma_api}/markets")
        response.raise_for_status()
        
        markets = response.json()
        for market in markets:
            if market.get('slug') == slug:
                return market
        
        print(f"⚠️ 未找到 slug 为 {slug} 的市场")
        return None
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def get_orderbook(self, token_id: str) -> Optional[Dict]:
        """
        获取指定代币的订单簿
        
        Args:
            token_id: 代币 ID
            
        Returns:
            订单簿数据
        """
        book = self.client.get_order_book(token_id)
        return book
    
    # ==================== WebSocket 监听 ====================
    
    def subscribe_orderbook(self, token_ids: List[str], callback: Callable[[Dict], None]):
        """
        订阅订单簿变化
        
        Args:
            token_ids: 要监听的代币 ID 列表
            callback: 订单簿更新时的回调函数
        """
        for token_id in token_ids:
            if token_id not in self.orderbook_callbacks:
                self.orderbook_callbacks[token_id] = []
            self.orderbook_callbacks[token_id].append(callback)
        
        # 启动 market WebSocket
        if not self.market_ws:
            self._start_market_websocket(token_ids)
        else:
            print("⚠️ Market WebSocket 已在运行")
    
    def _start_market_websocket(self, asset_ids: List[str]):
        """启动市场数据 WebSocket"""
        
        def on_message(_ws, message):
            try:
                data = json.loads(message)
                print(data)
                
                # Handle case where message is an array containing a single object
                if isinstance(data, list):
                    if len(data) > 0:
                        data = data[0]
                    else:
                        return
                
                if data.get('event_type') == 'book':
                    asset_id = data.get('asset_id')
                    
                    # 调用所有注册的回调
                    if asset_id in self.orderbook_callbacks:
                        for callback in self.orderbook_callbacks[asset_id]:
                            callback(data)
                
                elif data.get('event_type') == 'price_change':
                    # 修改：遍历 price_changes 列表
                    price_changes = data.get('price_changes', [])
                    # 确保 price_changes 是列表
                    if isinstance(price_changes, list):
                        for price_change in price_changes:
                            # 确保 price_change 是字典
                            if isinstance(price_change, dict):
                                asset_id = price_change.get('asset_id')
                                
                                if asset_id in self.orderbook_callbacks:
                                    # 修改：将完整的数据对象传递，包含 price_change 和时间戳
                                    callback_data = {
                                        'event_type': 'price_change',
                                        'timestamp': data.get('timestamp'),
                                        'market': data.get('market'),
                                        **price_change  # 展开 price_change 字段
                                    }
                                    for callback in self.orderbook_callbacks[asset_id]:
                                        callback(callback_data)
                
            except json.JSONDecodeError:
                pass  # 忽略非 JSON 消息（如 PONG）
            except Exception as e:
                print(f"⚠️ 处理消息时出错: {e}")
                print(f"   消息内容: {message}")
        
        def on_error(ws, error):
            print(f"❌ Market WebSocket 错误: {error}")
        
        def on_close(ws, close_status_code, close_msg):
            print("🔌 Market WebSocket 连接关闭")
        
        def on_open(ws):
            print("✅ Market WebSocket 连接成功")
            
            # 订阅市场数据
            subscribe_msg = {
                "assets_ids": asset_ids,
                "type": "market"
            }
            ws.send(json.dumps(subscribe_msg))
            
            # 启动心跳
            def ping():
                while True:
                    try:
                        ws.send("PING")
                        time.sleep(10)
                    except:
                        break
            
            ping_thread = threading.Thread(target=ping, daemon=True)
            ping_thread.start()
        
        self.market_ws = WebSocketApp(
            f"{self.wss_url}/ws/market",
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        
        # 在新线程中运行
        ws_thread = threading.Thread(target=self.market_ws.run_forever, daemon=True)
        ws_thread.start()
        
        print(f"🚀 已启动 Market WebSocket，监听 {len(asset_ids)} 个代币")
    
    def subscribe_user_orders(
        self, 
        condition_ids: List[str],
        order_callback: Optional[Callable[[Dict], None]] = None,
        trade_callback: Optional[Callable[[Dict], None]] = None
    ):
        """
        订阅用户订单和交易事件
        
        Args:
            condition_ids: 要监听的市场条件 ID 列表
            order_callback: 订单更新时的回调函数
            trade_callback: 交易完成时的回调函数
        """
        if order_callback:
            self.order_callbacks.append(order_callback)
        if trade_callback:
            self.trade_callbacks.append(trade_callback)
        
        # 启动 user WebSocket
        if not self.user_ws:
            self._start_user_websocket(condition_ids)
        else:
            print("⚠️ User WebSocket 已在运行")
    
    def _start_user_websocket(self, market_ids: List[str]):
        """启动用户数据 WebSocket"""
        
        def on_message(ws, message):
            try:
                data = json.loads(message)
                
                if data.get('event_type') == 'order':
                    print(f"📝 订单更新: {data.get('type')} - {data.get('id')}")
                    
                    for callback in self.order_callbacks:
                        callback(data)
                
                elif data.get('event_type') == 'trade':
                    print(f"💰 交易完成: {data.get('side')} {data.get('size')} @ {data.get('price')}")
                    
                    for callback in self.trade_callbacks:
                        callback(data)
                
            except json.JSONDecodeError:
                pass
        
        def on_error(ws, error):
            print(f"❌ User WebSocket 错误: {error}")
        
        def on_close(ws, close_status_code, close_msg):
            print("🔌 User WebSocket 连接关闭")
        
        def on_open(ws):
            print("✅ User WebSocket 连接成功")
            
            # 订阅用户数据
            subscribe_msg = {
                "markets": market_ids,
                "type": "user",
                "auth": {
                    "apiKey": self.api_key,
                    "secret": self.api_secret,
                    "passphrase": self.api_passphrase
                }
            }
            ws.send(json.dumps(subscribe_msg))
            
            # 启动心跳
            def ping():
                while True:
                    try:
                        ws.send("PING")
                        time.sleep(10)
                    except:
                        break
            
            ping_thread = threading.Thread(target=ping, daemon=True)
            ping_thread.start()
        
        self.user_ws = WebSocketApp(
            f"{self.wss_url}/ws/user",
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        
        # 在新线程中运行
        ws_thread = threading.Thread(target=self.user_ws.run_forever, daemon=True)
        ws_thread.start()
        
        print(f"🚀 已启动 User WebSocket，监听 {len(market_ids)} 个市场")
    
    # ==================== 交易功能 ====================
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def place_limit_order(
        self,
        token_id: str,
        side: str,  # "BUY" or "SELL"
        price: float,
        size: float
    ) -> Optional[Dict]:
        """
        下限价单
        
        Args:
            token_id: 代币 ID
            side: "BUY" 或 "SELL"
            price: 价格 (0.00-1.00)
            size: 数量
            
        Returns:
            订单响应
        """
        order = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY if side.upper() == "BUY" else SELL
        )
        
        signed_order = self.client.create_order(order)
        response = self.client.post_order(signed_order, OrderType.GTC)
        
        print(f"✅ 限价单已提交: {side} {size} @ ${price}")
        print(f"   订单 ID: {response.get('orderID', 'N/A')}")
        
        return response
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def place_market_order(
        self,
        token_id: str,
        side: str,  # "BUY" or "SELL"
        amount: float  # 金额（美元）
    ) -> Optional[Dict]:
        """
        下市价单
        
        Args:
            token_id: 代币 ID
            side: "BUY" 或 "SELL"
            amount: 交易金额（美元）
            
        Returns:
            订单响应
        """
        market_order = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=BUY if side.upper() == "BUY" else SELL,
            order_type=OrderType.FOK  # Fill or Kill
        )
        
        signed_order = self.client.create_market_order(market_order)
        response = self.client.post_order(signed_order, OrderType.FOK)
        
        print(f"✅ 市价单已提交: {side} ${amount}")
        print(f"   订单 ID: {response.get('orderID', 'N/A')}")
        
        return response
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def cancel_order(self, order_id: str) -> bool:
        """
        取消订单
        
        Args:
            order_id: 订单 ID
            
        Returns:
            是否成功
        """
        self.client.cancel(order_id)
        print(f"✅ 订单已取消: {order_id}")
        return True
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def cancel_all_orders(self) -> bool:
        """
        取消所有订单
        
        Returns:
            是否成功
        """
        self.client.cancel_all()
        print("✅ 所有订单已取消")
        return True
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def get_open_orders(self) -> List[Dict]:
        """
        获取所有未完成订单
        
        Returns:
            订单列表
        """
        orders = self.client.get_orders(OpenOrderParams())
        print(f"📋 当前有 {len(orders)} 个未完成订单")
        return orders
    
    @retry_on_failure(max_retries=3, delay=1.0)
    def get_trades(self) -> List[Dict]:
        """
        获取交易历史
        
        Returns:
            交易列表
        """
        trades = self.client.get_trades()
        print(f"📊 获取到 {len(trades)} 条交易记录")
        return trades


# ==================== 使用示例 ====================

def example_usage():
    """使用示例"""
    
    # 初始化交易器
    trader = PolymarketTrader()
    
    # 1. 获取所有市场
    print("\n" + "="*50)
    print("1️⃣ 获取所有活跃市场")
    print("="*50)
    markets = trader.get_all_markets(limit=5)
    
    for i, market in enumerate(markets[:3], 1):
        print(f"\n市场 {i}:")
        print(f"  标题: {market.get('question', 'N/A')}")
        print(f"  Slug: {market.get('slug', 'N/A')}")
        print(f"  条件 ID: {market.get('conditionId', 'N/A')}")
        print(f"  代币 IDs: {market.get('clobTokenIds', 'N/A')}")
    
    if not markets:
        print("⚠️ 未获取到市场数据，程序退出")
        return
    
    # 2. 选择一个市场进行操作
    test_market = markets[0]
    condition_id = test_market.get('conditionId')
    token_ids_raw = test_market.get('clobTokenIds', '[]')
    
    if not token_ids_raw or token_ids_raw == '[]':
        print("⚠️ 市场没有代币 ID，程序退出")
        return
    
    # 解析 JSON 字符串为列表
    if isinstance(token_ids_raw, str):
        token_ids = json.loads(token_ids_raw)
    else:
        token_ids = token_ids_raw
    
    if not token_ids:
        print("⚠️ 代币列表为空，程序退出")
        return
    
    token_id = token_ids[0]  # 使用第一个代币（通常是 YES）

    
    print(f"\n选择测试市场: {test_market.get('question')}")
    print(f"使用代币 ID: {token_id}")
    
    # 3. 获取订单簿
    print("\n" + "="*50)
    print("2️⃣ 获取订单簿")
    print("="*50)
    orderbook = trader.get_orderbook(token_id)
    if orderbook:
        print(f"市场: {orderbook.market if hasattr(orderbook, 'market') else 'N/A'}")
        bids = orderbook.bids if hasattr(orderbook, 'bids') else []
        asks = orderbook.asks if hasattr(orderbook, 'asks') else []
        
        print(f"最佳买价数量: {len(bids)}")
        print(f"最佳卖价数量: {len(asks)}")
        
        if bids:
            print(f"最佳买价: ${bids[0].price if hasattr(bids[0], 'price') else 'N/A'}")
        if asks:
            print(f"最佳卖价: ${asks[0].price if hasattr(asks[0], 'price') else 'N/A'}")
    # 4. 订阅订单簿变化
    print("\n" + "="*50)
    print("3️⃣ 订阅订单簿实时数据")
    print("="*50)
    
    def on_orderbook_update(data):
        event_type = data.get('event_type', data.get('side'))
        if event_type == 'book':
            print(f"\n📖 完整订单簿更新")
            print(f"   买单数: {len(data.get('bids', []))}")
            print(f"   卖单数: {len(data.get('asks', []))}")
        else:
            print(f"\n📊 价格变动: {data.get('side')} @ ${data.get('price')} - 数量: {data.get('size')}")
    
    trader.subscribe_orderbook([token_id], on_orderbook_update)
    
    # 5. 订阅用户订单
    print("\n" + "="*50)
    print("4️⃣ 订阅用户订单和交易")
    print("="*50)
    
    def on_order_update(data):
        order_type = data.get('type')
        order_id = data.get('id')
        
        if order_type == 'PLACEMENT':
            print(f"\n📝 新订单: {data.get('side')} {data.get('original_size')} @ ${data.get('price')}")
        elif order_type == 'UPDATE':
            print(f"\n🔄 订单更新: {order_id} - 已成交: {data.get('size_matched')}")
        elif order_type == 'CANCELLATION':
            print(f"\n❌ 订单取消: {order_id}")
    
    def on_trade_update(data):
        print(f"\n💰 交易完成!")
        print(f"   方向: {data.get('side')}")
        print(f"   价格: ${data.get('price')}")
        print(f"   数量: {data.get('size')}")
        print(f"   状态: {data.get('status')}")
    
    trader.subscribe_user_orders(
        [condition_id],
        order_callback=on_order_update,
        trade_callback=on_trade_update
    )
    
    # 6. 示例：下限价单（注释掉，避免真实交易）
    print("\n" + "="*50)
    print("5️⃣ 交易示例（已注释）")
    print("="*50)
    print("⚠️ 取消注释以下代码以执行真实交易:")
    print("""
    # 下限价买单
    # trader.place_limit_order(
    #     token_id=token_id,
    #     side="BUY",
    #     price=0.50,
    #     size=10.0
    # )
    
    # 下市价买单
    # trader.place_market_order(
    #     token_id=token_id,
    #     side="BUY",
    #     amount=25.0
    # )
    
    # 查看未完成订单
    # orders = trader.get_open_orders()
    
    # 取消订单
    # if orders:
    #     trader.cancel_order(orders[0]['id'])
    """)
    
    # 保持程序运行以接收 WebSocket 消息
    print("\n" + "="*50)
    print("✅ 系统正在运行，监听市场数据...")
    print("按 Ctrl+C 退出")
    print("="*50)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n👋 程序退出")


if __name__ == "__main__":
    example_usage()