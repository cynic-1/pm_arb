import asyncio
import json
from typing import Optional, Dict, List, Any
from datetime import datetime
import websocket
from threading import Thread
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs, 
    MarketOrderArgs, 
    OrderType,
    BookParams
)
from py_clob_client.order_builder.constants import BUY, SELL
import requests


class PMService:
    """
    Polymarket Service - 封装 Polymarket API 和 SDK 功能
    """
    
    def __init__(
        self,
        private_key: Optional[str] = None,
        funder_address: Optional[str] = None,
        signature_type: int = 0,
        chain_id: int = 137
    ):
        """
        初始化 Polymarket 服务
        
        Args:
            private_key: 私钥（如需要交易功能）
            funder_address: 资金地址（如使用代理钱包）
            signature_type: 签名类型 (0=EOA, 1=Email/Magic, 2=Browser)
            chain_id: 链ID（默认137为Polygon）
        """
        self.clob_host = "https://clob.polymarket.com"
        self.gamma_api_host = "https://gamma-api.polymarket.com"
        self.wss_host = "wss://ws-subscriptions-clob.polymarket.com/ws"
        
        # 初始化只读客户端
        self.read_client = ClobClient(self.clob_host)
        
        # 如果提供了私钥，初始化交易客户端
        self.trade_client = None
        if private_key:
            self.trade_client = ClobClient(
                self.clob_host,
                key=private_key,
                chain_id=chain_id,
                signature_type=signature_type,
                funder=funder_address
            )
            # 设置 API 凭证
            self.trade_client.set_api_creds(
                self.trade_client.create_or_derive_api_creds()
            )
            
        # WebSocket 连接管理
        self.ws_connections: Dict[str, websocket.WebSocketApp] = {}
        self.ws_threads: Dict[str, Thread] = {}
        
    # ==================== 事件相关 ====================
    
    def get_events(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        **filters
    ) -> List[Dict[str, Any]]:
        """
        获取事件列表
        
        Args:
            limit: 返回数量限制
            offset: 偏移量
            **filters: 其他过滤参数（active, closed, archived等）
            
        Returns:
            事件列表
        """
        url = f"{self.gamma_api_host}/events"
        params = {}
        
        if limit:
            params['limit'] = limit
        if offset:
            params['offset'] = offset
        params.update(filters)
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def get_event_by_id(self, event_id: str) -> Dict[str, Any]:
        """
        根据ID获取事件详情
        
        Args:
            event_id: 事件ID
            
        Returns:
            事件详情
        """
        url = f"{self.gamma_api_host}/events/{event_id}"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    
    def get_event_by_slug(self, slug: str) -> Dict[str, Any]:
        """
        根据 slug 获取事件
        
        Args:
            slug: 事件的 slug（URL中的标识）
            
        Returns:
            事件详情
        """
        events = self.get_events(slug=slug)
        if events and len(events) > 0:
            return events[0]
        raise ValueError(f"Event with slug '{slug}' not found")
    
    # ==================== 市场相关 ====================
    
    def get_markets(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        **filters
    ) -> List[Dict[str, Any]]:
        """
        获取市场列表
        
        Args:
            limit: 返回数量限制
            offset: 偏移量
            **filters: 其他过滤参数
            
        Returns:
            市场列表
        """
        url = f"{self.gamma_api_host}/markets"
        params = {}
        
        if limit:
            params['limit'] = limit
        if offset:
            params['offset'] = offset
        params.update(filters)
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    def get_market_by_id(self, market_id: str) -> Dict[str, Any]:
        """
        根据ID获取市场详情
        
        Args:
            market_id: 市场ID
            
        Returns:
            市场详情
        """
        url = f"{self.gamma_api_host}/markets/{market_id}"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    
    def get_market_by_slug(self, slug: str) -> Dict[str, Any]:
        """
        根据 slug 获取市场
        
        Args:
            slug: 市场的 slug
            
        Returns:
            市场详情
        """
        markets = self.get_markets(slug=slug)
        if markets and len(markets) > 0:
            return markets[0]
        raise ValueError(f"Market with slug '{slug}' not found")
    
    # ==================== Token ID 相关 ====================
    
    def get_token_ids_from_market(self, market_data: Dict[str, Any]) -> List[str]:
        """
        从市场数据中提取 token IDs
        
        Args:
            market_data: 市场数据字典
            
        Returns:
            Token ID 列表
        """
        clob_token_ids = market_data.get('clobTokenIds', '')
        if isinstance(clob_token_ids, str):
            # 通常是逗号分隔的字符串
            return [tid.strip() for tid in clob_token_ids.split(',') if tid.strip()]
        return []
    
    def get_token_ids_by_condition_id(self, condition_id: str) -> List[str]:
        """
        根据 condition ID 获取 token IDs
        
        Args:
            condition_id: 条件ID（市场ID）
            
        Returns:
            Token ID 列表
        """
        # 先通过 condition_id 找到市场
        markets = self.get_markets(conditionId=condition_id)
        if markets and len(markets) > 0:
            return self.get_token_ids_from_market(markets[0])
        return []
    
    # ==================== 订单簿相关 ====================
    
    def get_order_book(self, token_id: str) -> Dict[str, Any]:
        """
        获取单个 token 的订单簿
        
        Args:
            token_id: Token ID
            
        Returns:
            订单簿数据
        """
        return self.read_client.get_order_book(token_id)
    
    def get_order_books(self, token_ids: List[str]) -> List[Dict[str, Any]]:
        """
        批量获取订单簿
        
        Args:
            token_ids: Token ID 列表
            
        Returns:
            订单簿列表
        """
        book_params = [BookParams(token_id=tid) for tid in token_ids]
        return self.read_client.get_order_books(book_params)
    
    def get_midpoint(self, token_id: str) -> float:
        """
        获取中间价
        
        Args:
            token_id: Token ID
            
        Returns:
            中间价
        """
        return self.read_client.get_midpoint(token_id)
    
    def get_price(self, token_id: str, side: str) -> float:
        """
        获取买入或卖出价格
        
        Args:
            token_id: Token ID
            side: "BUY" 或 "SELL"
            
        Returns:
            价格
        """
        return self.read_client.get_price(token_id, side=side)
    
    # ==================== WebSocket 监控 ====================
    
    def subscribe_to_orderbook(
        self,
        token_ids: List[str],
        on_message_callback,
        connection_name: str = "default"
    ):
        """
        订阅订单簿 WebSocket
        
        Args:
            token_ids: 要监控的 token ID 列表
            on_message_callback: 收到消息时的回调函数
            connection_name: 连接名称（用于管理多个连接）
        """
        def on_message(ws, message):
            try:
                data = json.loads(message) if message != "PING" else None
                if data:
                    on_message_callback(data)
            except Exception as e:
                print(f"Error processing message: {e}")
        
        def on_error(ws, error):
            print(f"WebSocket error: {error}")
        
        def on_close(ws, close_status_code, close_msg):
            print(f"WebSocket closed: {close_status_code} - {close_msg}")
        
        def on_open(ws):
            print(f"WebSocket opened for connection: {connection_name}")
            # 订阅市场数据
            ws.send(json.dumps({
                "assets_ids": token_ids,
                "type": "market"
            }))
            
            # 启动心跳
            def send_ping():
                while True:
                    try:
                        ws.send("PING")
                        asyncio.sleep(10)
                    except:
                        break
            
            ping_thread = Thread(target=send_ping, daemon=True)
            ping_thread.start()
        
        # 创建 WebSocket 连接
        ws_url = f"{self.wss_host}/market"
        ws = websocket.WebSocketApp(
            ws_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        
        # 保存连接
        self.ws_connections[connection_name] = ws
        
        # 在新线程中运行
        ws_thread = Thread(target=ws.run_forever, daemon=True)
        ws_thread.start()
        self.ws_threads[connection_name] = ws_thread
        
        print(f"Started WebSocket subscription for {len(token_ids)} tokens")
    
    def close_websocket(self, connection_name: str = "default"):
        """
        关闭 WebSocket 连接
        
        Args:
            connection_name: 连接名称
        """
        if connection_name in self.ws_connections:
            self.ws_connections[connection_name].close()
            del self.ws_connections[connection_name]
            if connection_name in self.ws_threads:
                del self.ws_threads[connection_name]
            print(f"Closed WebSocket connection: {connection_name}")
    
    # ==================== 下单功能 ====================
    
    def place_market_order(
        self,
        token_id: str,
        amount: float,
        side: str,
        order_type: OrderType = OrderType.FOK
    ) -> Dict[str, Any]:
        """
        下市价单
        
        Args:
            token_id: Token ID
            amount: 交易金额（美元）
            side: "BUY" 或 "SELL"
            order_type: 订单类型（默认 FOK - Fill or Kill）
            
        Returns:
            订单响应
        """
        if not self.trade_client:
            raise ValueError("Trade client not initialized. Please provide private_key.")
        
        side_const = BUY if side.upper() == "BUY" else SELL
        
        market_order = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=side_const,
            order_type=order_type
        )
        
        signed_order = self.trade_client.create_market_order(market_order)
        response = self.trade_client.post_order(signed_order, order_type)
        
        return response
    
    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: OrderType = OrderType.GTC
    ) -> Dict[str, Any]:
        """
        下限价单
        
        Args:
            token_id: Token ID
            price: 价格（0-1之间）
            size: 数量（份额）
            side: "BUY" 或 "SELL"
            order_type: 订单类型（默认 GTC - Good Till Cancel）
            
        Returns:
            订单响应
        """
        if not self.trade_client:
            raise ValueError("Trade client not initialized. Please provide private_key.")
        
        side_const = BUY if side.upper() == "BUY" else SELL
        
        limit_order = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side_const
        )
        
        signed_order = self.trade_client.create_order(limit_order)
        response = self.trade_client.post_order(signed_order, order_type)
        
        return response
    
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        取消订单
        
        Args:
            order_id: 订单ID
            
        Returns:
            取消结果
        """
        if not self.trade_client:
            raise ValueError("Trade client not initialized. Please provide private_key.")
        
        return self.trade_client.cancel(order_id)
    
    def cancel_all_orders(self) -> Dict[str, Any]:
        """
        取消所有订单
        
        Returns:
            取消结果
        """
        if not self.trade_client:
            raise ValueError("Trade client not initialized. Please provide private_key.")
        
        return self.trade_client.cancel_all()
    
    def get_open_orders(self) -> List[Dict[str, Any]]:
        """
        获取未完成订单
        
        Returns:
            订单列表
        """
        if not self.trade_client:
            raise ValueError("Trade client not initialized. Please provide private_key.")
        
        from py_clob_client.clob_types import OpenOrderParams
        return self.trade_client.get_orders(OpenOrderParams())