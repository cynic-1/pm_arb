"""
WebSocket管理器模块
管理Polymarket和Opinion的WebSocket连接,提供实时订单簿更新
"""

import json
import time
import threading
import logging
import ssl
from typing import Dict, List, Callable, Optional, Set
from dataclasses import dataclass
from websocket import WebSocketApp

from .models import OrderBookSnapshot, OrderBookLevel
from .config import ArbitrageConfig

logger = logging.getLogger(__name__)


@dataclass
class OrderBookUpdate:
    """订单簿更新事件"""
    token_id: str
    market_id: Optional[int]
    source: str  # 'polymarket' or 'opinion'
    snapshot: Optional[OrderBookSnapshot]
    timestamp: float


class PolymarketWebSocket:
    """Polymarket WebSocket连接管理器"""

    def __init__(self, config: ArbitrageConfig):
        self.config = config
        self.ws: Optional[WebSocketApp] = None
        self.connected = threading.Event()
        self.message_count = 0
        self.orderbook_cache: Dict[str, OrderBookSnapshot] = {}
        self.lock = threading.Lock()
        self.callbacks: List[Callable[[OrderBookUpdate], None]] = []
        self.subscribed_assets: Set[str] = set()

        # Auto-reconnection settings
        self.auto_reconnect = True
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 1.0  # Start with 1 second
        self.max_reconnect_delay = 60.0  # Max 60 seconds
        self.is_closing = False
        self._reconnecting = False  # 防止多个重连线程
        self._reconnect_lock = threading.Lock()

    def on_message(self, ws, message):
        """处理接收到的消息"""
        recv_time = time.time()
        self.message_count += 1

        logger.debug(f"[Polymarket WS] 收到消息 #{self.message_count}, 长度={len(message)}")

        try:
            parse_start = time.time()
            data = json.loads(message)
            parse_time = (time.time() - parse_start) * 1000
            logger.debug(f"[Polymarket WS] JSON解析耗时: {parse_time:.2f}ms")

            # Handle initial book snapshot
            if isinstance(data, list):
                logger.debug(f"[Polymarket WS] 收到列表数据，包含 {len(data)} 项")
                for item in data:
                    self._process_book_data(item, recv_time)
            # Handle single book update
            elif isinstance(data, dict):
                event_type = data.get("event_type")
                asset_id = data.get("asset_id", "unknown")[:20]
                logger.debug(f"[Polymarket WS] 收到字典数据，event_type={event_type}, asset_id={asset_id}...")

                if event_type == "book":
                    self._process_book_data(data, recv_time)
                # Handle price changes or other events
                elif "asset_id" in data:
                    self._process_book_data(data, recv_time)

        except json.JSONDecodeError:
            logger.debug(f"Non-JSON message: {message[:100]}")
        except Exception as e:
            logger.error(f"Error processing Polymarket message: {e}")

    def _process_book_data(self, data: dict, recv_time: float):
        """处理订单簿数据"""
        process_start = time.time()

        asset_id = data.get("asset_id")
        if not asset_id:
            return

        logger.debug(f"[Polymarket] 处理订单簿: asset_id={asset_id[:20]}...")

        # Parse bids and asks
        parse_levels_start = time.time()
        bids_raw = data.get("bids", [])
        asks_raw = data.get("asks", [])

        bids = self._parse_levels(bids_raw, reverse=True)
        asks = self._parse_levels(asks_raw, reverse=False)
        parse_levels_time = (time.time() - parse_levels_start) * 1000

        logger.debug(f"[Polymarket] 解析档位耗时: {parse_levels_time:.2f}ms (bids={len(bids)}, asks={len(asks)})")

        snapshot = OrderBookSnapshot(
            bids=bids,
            asks=asks,
            source="polymarket",
            token_id=asset_id,
            timestamp=recv_time
        )

        # Validate orderbook: best ask should be > best bid
        if bids and asks:
            best_bid = bids[0].price
            best_ask = asks[0].price
            if best_ask <= best_bid:
                logger.error(
                    f"❌ [Polymarket] 交叉订单簿检测! asset_id={asset_id[:20]}..., "
                    f"best_bid={best_bid}, best_ask={best_ask}"
                )
                logger.error(f"   完整订单簿: bids={bids}, asks={asks}")
                logger.warning(
                    f"⚠️ [Polymarket] Polymarket发送完整快照，交叉订单簿可能是服务器端问题，"
                    f"无法通过REST API修复（Polymarket不提供REST订单簿API）"
                )

        # Cache the snapshot
        cache_start = time.time()
        with self.lock:
            self.orderbook_cache[asset_id] = snapshot
        cache_time = (time.time() - cache_start) * 1000

        logger.debug(f"[Polymarket] 缓存更新耗时: {cache_time:.2f}ms")

        # Notify callbacks
        update = OrderBookUpdate(
            token_id=asset_id,
            market_id=None,
            source="polymarket",
            snapshot=snapshot,
            timestamp=snapshot.timestamp
        )

        for callback in self.callbacks:
            try:
                callback_exec_start = time.time()
                callback(update)
                callback_exec_time = (time.time() - callback_exec_start) * 1000
                logger.debug(f"[Polymarket] 回调执行耗时: {callback_exec_time:.2f}ms")
            except Exception as e:
                logger.error(f"Callback error: {e}")

        total_time = (time.time() - process_start) * 1000
        logger.debug(f"[Polymarket] 总处理耗时: {total_time:.2f}ms (从开始处理到完成)")

    def _parse_levels(self, levels: List, reverse: bool) -> List[OrderBookLevel]:
        """解析订单簿档位"""
        result = []

        for level in levels:
            try:
                if isinstance(level, dict):
                    price = float(level.get("price", 0))
                    size = float(level.get("size", 0))
                elif isinstance(level, (list, tuple)) and len(level) >= 2:
                    price = float(level[0])
                    size = float(level[1])
                else:
                    continue

                if price > 0 and size > 0:
                    result.append(OrderBookLevel(price=price, size=size))
            except (ValueError, TypeError):
                continue

        # Sort by price
        result.sort(key=lambda x: x.price, reverse=reverse)
        return result[:5]  # Top 5 levels

    def on_error(self, ws, error):
        """处理错误"""
        logger.error(f"Polymarket WebSocket error: {error}")

    def on_close(self, ws, code, msg):
        """处理连接关闭"""
        logger.warning(f"⚠️ Polymarket WebSocket closed: {code} - {msg}")
        self.connected.clear()

        # Attempt reconnection if not intentionally closing
        if self.auto_reconnect and not self.is_closing:
            with self._reconnect_lock:
                if self._reconnecting:
                    logger.debug("🔄 Polymarket reconnection already in progress, skipping...")
                    return
                self._reconnecting = True
            logger.info(f"🔄 Polymarket WebSocket will attempt to reconnect...")
            threading.Thread(target=self._reconnect, daemon=True).start()

    def on_open(self, ws):
        """处理连接打开"""
        logger.info("✅ Polymarket WebSocket connected!")
        self.connected.set()

        # Reset reconnect counter on successful connection
        self.reconnect_attempts = 0
        self.reconnect_delay = 1.0

        # Subscribe to assets
        if self.subscribed_assets:
            msg = {
                "assets_ids": list(self.subscribed_assets),
                "type": "market"
            }
            ws.send(json.dumps(msg))
            logger.info(f"📡 Subscribed to {len(self.subscribed_assets)} Polymarket assets")

        # Start ping thread
        threading.Thread(target=self._ping_loop, daemon=True).start()

    def _ping_loop(self):
        """定期发送PING保持连接"""
        while self.ws and self.ws.sock and self.ws.sock.connected:
            try:
                self.ws.send("PING")
                logger.debug("💓 Sent Polymarket PING")
                time.sleep(10)
            except Exception as e:
                logger.debug(f"Ping error: {e}")
                break

    def _reconnect(self):
        """重连逻辑,使用指数退避"""
        try:
            while self.auto_reconnect and not self.is_closing and self.reconnect_attempts < self.max_reconnect_attempts:
                self.reconnect_attempts += 1

                logger.info(f"🔄 Polymarket reconnect attempt {self.reconnect_attempts}/{self.max_reconnect_attempts} in {self.reconnect_delay:.1f}s...")
                time.sleep(self.reconnect_delay)

                try:
                    # Close old connection if exists
                    if self.ws:
                        try:
                            self.ws.close()
                        except:
                            pass

                    # Create new connection
                    url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
                    self.ws = WebSocketApp(
                        url,
                        on_message=self.on_message,
                        on_error=self.on_error,
                        on_close=self.on_close,
                        on_open=self.on_open,
                    )

                    # Run in background thread
                    threading.Thread(target=self.ws.run_forever, daemon=True).start()

                    # Wait for connection
                    if self.connected.wait(timeout=10):
                        logger.info(f"✅ Polymarket reconnected successfully!")
                        return
                    else:
                        logger.warning(f"⚠️ Polymarket reconnection attempt {self.reconnect_attempts} timed out")

                except Exception as e:
                    logger.error(f"❌ Polymarket reconnection error: {e}")

                # Exponential backoff
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

            if self.reconnect_attempts >= self.max_reconnect_attempts:
                logger.error(f"❌ Polymarket WebSocket failed after {self.max_reconnect_attempts} reconnection attempts")
            elif not self.auto_reconnect:
                logger.info("🛑 Polymarket auto-reconnect disabled")
        finally:
            # Reset reconnecting flag
            with self._reconnect_lock:
                self._reconnecting = False

    def connect(self, asset_ids: List[str]) -> bool:
        """
        建立WebSocket连接并订阅资产

        Args:
            asset_ids: 要订阅的asset ID列表

        Returns:
            连接是否成功
        """
        self.subscribed_assets = set(asset_ids)

        url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        logger.info(f"🔗 Connecting to Polymarket WebSocket...")

        self.ws = WebSocketApp(
            url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
        )

        # Run in background thread
        threading.Thread(target=self.ws.run_forever, daemon=True).start()

        # Wait for connection
        if self.connected.wait(timeout=10):
            logger.info(f"✅ Polymarket WebSocket ready ({len(asset_ids)} assets)")
            return True
        else:
            logger.error("❌ Polymarket WebSocket connection timeout")
            return False

    def get_orderbook(self, asset_id: str) -> Optional[OrderBookSnapshot]:
        """获取缓存的订单簿"""
        with self.lock:
            return self.orderbook_cache.get(asset_id)

    def add_callback(self, callback: Callable[[OrderBookUpdate], None]):
        """添加订单簿更新回调"""
        self.callbacks.append(callback)

    def close(self):
        """关闭WebSocket连接"""
        self.is_closing = True
        self.auto_reconnect = False
        if self.ws:
            self.ws.close()


class OpinionWebSocket:
    """Opinion WebSocket连接管理器"""

    # SSL选项 - 使用更宽松的SSL设置以避免连接错误
    SSL_OPTIONS = {"cert_reqs": ssl.CERT_NONE}

    def __init__(self, config: ArbitrageConfig, opinion_client=None):
        self.config = config
        self.opinion_client = opinion_client  # REST API客户端，用于获取初始订单簿
        self.ws: Optional[WebSocketApp] = None
        self.connected = threading.Event()
        self.message_count = 0
        self.orderbook_cache: Dict[str, OrderBookSnapshot] = {}
        self.token_to_market: Dict[str, int] = {}  # token_id -> market_id mapping
        self.lock = threading.Lock()
        self.callbacks: List[Callable[[OrderBookUpdate], None]] = []
        self.subscribed_markets: Set[int] = set()

        # Orderbook freshness tracking
        self.orderbook_last_update: Dict[str, float] = {}  # token_id -> last update timestamp
        self.orderbook_stale_threshold = 10.0  # 订单簿30秒未更新视为过期
        self.last_message_time = time.time()  # WebSocket最后接收消息时间
        self.ws_stale_threshold = 20.0  # WebSocket 60秒无消息视为可能断连

        # Auto-reconnection settings
        self.auto_reconnect = True
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 1.0  # Start with 1 second
        self.max_reconnect_delay = 60.0  # Max 60 seconds
        self.is_closing = False
        self._reconnecting = False  # 防止多个重连线程
        self._reconnect_lock = threading.Lock()
        self.subscription_confirmed = threading.Event()  # 订阅确认事件
        self.pending_subscriptions = 0  # 待确认的订阅数

    def on_message(self, ws, message):
        """处理接收到的消息"""
        recv_time = time.time()
        self.message_count += 1
        self.last_message_time = recv_time  # 更新最后消息时间

        logger.debug(f"[Opinion WS] 收到消息 #{self.message_count}, 长度={len(message)}")

        # 记录原始消息用于调试
        if self.message_count <= 10:  # 只记录前10条消息避免日志过多
            logger.debug(f"[Opinion WS] 原始消息: {message[:500]}")

        try:
            parse_start = time.time()
            data = json.loads(message)
            parse_time = (time.time() - parse_start) * 1000
            logger.debug(f"[Opinion WS] JSON解析耗时: {parse_time:.2f}ms")

            msg_type = data.get("msgType")
            logger.debug(f"[Opinion WS] 消息类型: {msg_type}")

            if msg_type == "market.depth.diff":
                self._process_book_update(data, recv_time)
            elif data.get("code") == 200:
                # Subscription confirmation
                message = data.get('message', '')
                logger.debug(f"✓ [Opinion] 订阅确认: {message}")
                self.pending_subscriptions -= 1
                logger.debug(f"🔢 剩余待确认: {self.pending_subscriptions}")
                if self.pending_subscriptions <= 0:
                    self.subscription_confirmed.set()
                    logger.info(f"✅ 所有订阅已确认")

        except json.JSONDecodeError:
            logger.debug(f"Non-JSON message: {message[:100]}")
        except Exception as e:
            logger.error(f"Error processing Opinion message: {e}")

    def _process_book_update(self, data: dict, recv_time: float):
        """处理订单簿更新"""
        process_start = time.time()

        market_id = data.get("marketId")
        token_id = data.get("tokenId")
        side = data.get("side")  # 'bids' or 'asks'
        outcome_side = data.get("outcomeSide")  # 1=YES, 2=NO
        price = float(data.get("price", 0))
        size = float(data.get("size", 0))

        logger.debug(f"[Opinion] 处理订单簿更新: market={market_id}, token={token_id[:20]}..., outcomeSide={outcome_side}, side={side}, price={price}, size={size}")

        if not (market_id and token_id and side and price > 0):
            return

        # Get or create orderbook snapshot
        with self.lock:
            snapshot = self.orderbook_cache.get(token_id)

            if not snapshot:
                logger.debug(f"[Opinion] 创建新订单簿快照: token={token_id[:20]}...")
                # Create new snapshot
                snapshot = OrderBookSnapshot(
                    bids=[],
                    asks=[],
                    source="opinion",
                    token_id=token_id,
                    timestamp=recv_time
                )

            # Update the appropriate side
            update_start = time.time()
            level = OrderBookLevel(price=price, size=size)

            if side == "bids":
                # Update bids
                bids = [l for l in snapshot.bids if abs(l.price - price) > 0.001]
                if size > 0:  # Only add if size > 0
                    bids.append(level)
                bids.sort(key=lambda x: x.price, reverse=True)
                snapshot = OrderBookSnapshot(
                    bids=bids[:5],
                    asks=snapshot.asks,
                    source="opinion",
                    token_id=token_id,
                    timestamp=recv_time
                )
            else:  # asks
                asks = [l for l in snapshot.asks if abs(l.price - price) > 0.001]
                if size > 0:
                    asks.append(level)
                asks.sort(key=lambda x: x.price)
                snapshot = OrderBookSnapshot(
                    bids=snapshot.bids,
                    asks=asks[:5],
                    source="opinion",
                    token_id=token_id,
                    timestamp=recv_time
                )

            update_time = (time.time() - update_start) * 1000
            logger.debug(f"[Opinion] 订单簿更新耗时: {update_time:.2f}ms")

            # Validate orderbook: best ask should be > best bid
            if snapshot.bids and snapshot.asks:
                best_bid = snapshot.bids[0].price
                best_ask = snapshot.asks[0].price
                if best_ask <= best_bid:
                    logger.error(
                        f"❌ [Opinion] 交叉订单簿检测! market={market_id}, token={token_id[:20]}..., "
                        f"outcomeSide={outcome_side}, best_bid={best_bid}, best_ask={best_ask}, "
                        f"刚更新的: side={side}, price={price}, size={size}"
                    )
                    logger.error(f"   完整订单簿: bids={snapshot.bids}, asks={snapshot.asks}")

                    # 尝试通过REST API重新获取完整订单簿修复错误
                    logger.warning(f"🔄 [Opinion] 尝试通过REST API重新获取订单簿修复错误...")
                    if self._initialize_orderbook_from_rest(token_id):
                        logger.info(f"✅ [Opinion] 订单簿已通过REST API刷新并修复")
                        # 重新读取刷新后的订单簿用于后续处理
                        snapshot = self.orderbook_cache.get(token_id)
                    else:
                        logger.error(f"❌ [Opinion] REST API刷新失败，保留原订单簿")

            # Cache updated snapshot and record update time
            self.orderbook_cache[token_id] = snapshot
            self.orderbook_last_update[token_id] = recv_time  # 记录订单簿更新时间
            self.token_to_market[token_id] = market_id

        # Notify callbacks
        update = OrderBookUpdate(
            token_id=token_id,
            market_id=market_id,
            source="opinion",
            snapshot=snapshot,
            timestamp=snapshot.timestamp
        )

        for callback in self.callbacks:
            try:
                callback_exec_start = time.time()
                callback(update)
                callback_exec_time = (time.time() - callback_exec_start) * 1000
                logger.debug(f"[Opinion] 回调执行耗时: {callback_exec_time:.2f}ms")
            except Exception as e:
                logger.error(f"Callback error: {e}")

        total_time = (time.time() - process_start) * 1000
        logger.debug(f"[Opinion] 总处理耗时: {total_time:.2f}ms (从开始处理到完成)")

    def on_error(self, ws, error):
        """处理错误"""
        logger.error(f"Opinion WebSocket error: {error}")

    def on_close(self, ws, code, msg):
        """处理连接关闭"""
        logger.warning(f"⚠️ Opinion WebSocket closed: {code} - {msg}")
        self.connected.clear()

        # Attempt reconnection if not intentionally closing
        if self.auto_reconnect and not self.is_closing:
            with self._reconnect_lock:
                if self._reconnecting:
                    logger.debug("🔄 Opinion reconnection already in progress, skipping...")
                    return
                self._reconnecting = True
            logger.info(f"🔄 Opinion WebSocket will attempt to reconnect...")
            threading.Thread(target=self._reconnect, daemon=True).start()

    def on_open(self, ws):
        """处理连接打开"""
        logger.info("✅ Opinion WebSocket connected!")
        self.connected.set()

        # Reset last message time to prevent immediate disconnect detection
        self.last_message_time = time.time()

        # Reset reconnect counter on successful connection
        self.reconnect_attempts = 0
        self.reconnect_delay = 1.0

        # Subscribe to markets (send all at once for faster subscription)
        self._subscribe_to_markets(ws)

        # Start heartbeat thread
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def _subscribe_to_markets(self, ws):
        """订阅所有市场 - 分批发送以避免服务器拒绝"""
        if not self.subscribed_markets:
            return

        market_list = list(self.subscribed_markets)
        total = len(market_list)
        logger.info(f"📡 Subscribing to {total} Opinion markets...")

        # 重置订阅确认状态
        self.subscription_confirmed.clear()
        self.pending_subscriptions = total
        logger.debug(f"🔢 设置待确认订阅数: {self.pending_subscriptions}")

        # 分批发送，每批50个市场，避免服务器过载
        batch_size = 50
        batch_delay = 0.1  # 每批之间延迟100ms

        for i in range(0, total, batch_size):
            batch = market_list[i:i+batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total + batch_size - 1) // batch_size

            logger.info(f"📤 Sending batch {batch_num}/{total_batches} ({len(batch)} markets)...")

            for market_id in batch:
                msg = {
                    "action": "SUBSCRIBE",
                    "channel": "market.depth.diff",
                    "marketId": market_id
                }
                try:
                    ws.send(json.dumps(msg))
                    logger.debug(f"✓ 订阅请求已发送: market_id={market_id}")
                except Exception as e:
                    logger.error(f"Failed to subscribe to market {market_id}: {e}")
                    return  # Stop if connection is lost

            # 批次之间短暂延迟
            if i + batch_size < total:
                time.sleep(batch_delay)

        logger.info(f"✅ Sent {total} subscription requests in {total_batches} batches")
        logger.debug(f"⏳ 等待订阅确认... (pending={self.pending_subscriptions})")

        # 等待一小段时间让服务器处理订阅
        time.sleep(0.2)

        # 检查是否有任何消息到达
        if self.message_count > 0:
            logger.info(f"✓ 已收到 {self.message_count} 条消息，订阅可能已激活")
        else:
            logger.warning(f"⚠️ 尚未收到任何消息，订阅可能未激活")

    def _heartbeat_loop(self):
        """定期发送HEARTBEAT保持连接，并检查WebSocket健康状态"""
        while self.ws and self.ws.sock and self.ws.sock.connected:
            try:
                # 发送心跳
                msg = {"action": "HEARTBEAT"}
                self.ws.send(json.dumps(msg))
                logger.debug("💓 Sent Opinion HEARTBEAT")

                # 检查WebSocket是否长时间无消息
                time_since_last_msg = time.time() - self.last_message_time
                if time_since_last_msg > self.ws_stale_threshold:
                    logger.error(
                        f"❌ [Opinion] WebSocket可能已断连! "
                        f"已 {time_since_last_msg:.1f}秒 无消息（阈值={self.ws_stale_threshold}s）"
                    )
                    logger.warning("🔄 [Opinion] 触发主动重连...")

                    # 检查是否已经有重连在进行
                    with self._reconnect_lock:
                        if self._reconnecting:
                            logger.debug("🔄 Opinion reconnection already in progress, skipping...")
                            break
                        self._reconnecting = True

                    # 清除连接状态
                    self.connected.clear()

                    # 主动关闭旧连接
                    try:
                        if self.ws:
                            self.ws.close()
                    except:
                        pass

                    # 直接启动重连线程
                    logger.info("🔄 Opinion 启动重连线程...")
                    threading.Thread(target=self._reconnect, daemon=True).start()
                    break

                time.sleep(30)
            except Exception as e:
                logger.debug(f"Heartbeat error: {e}")
                break

    def _reconnect(self):
        """重连逻辑,使用指数退避"""
        try:
            while self.auto_reconnect and not self.is_closing and self.reconnect_attempts < self.max_reconnect_attempts:
                self.reconnect_attempts += 1

                logger.info(f"🔄 Opinion reconnect attempt {self.reconnect_attempts}/{self.max_reconnect_attempts} in {self.reconnect_delay:.1f}s...")
                time.sleep(self.reconnect_delay)

                try:
                    # Close old connection if exists
                    if self.ws:
                        try:
                            self.ws.close()
                        except:
                            pass

                    # Create new connection
                    if not self.config.opinion_api_key:
                        logger.error("❌ Opinion API key not configured")
                        return

                    url = f"wss://ws.opinion.trade?apikey={self.config.opinion_api_key}"
                    self.ws = WebSocketApp(
                        url,
                        on_message=self.on_message,
                        on_error=self.on_error,
                        on_close=self.on_close,
                        on_open=self.on_open,
                    )

                    # Run in background thread with SSL options
                    threading.Thread(target=lambda: self.ws.run_forever(sslopt=self.SSL_OPTIONS), daemon=True).start()

                    # Wait for connection
                    if self.connected.wait(timeout=10):
                        logger.info(f"✅ Opinion WebSocket connected, waiting for subscription confirmation...")

                        # Wait for subscription confirmation
                        if self.subscription_confirmed.wait(timeout=5):
                            logger.info(f"✅ Opinion reconnected successfully (subscriptions confirmed)!")
                            return
                        else:
                            logger.warning(
                                f"⚠️ Opinion subscription confirmation timeout "
                                f"(pending={self.pending_subscriptions}, total={len(self.subscribed_markets)})"
                            )
                            logger.warning(
                                f"⚠️ Opinion服务器可能不发送订阅确认消息，"
                                f"但连接已建立。将尝试继续使用..."
                            )
                            # 即使订阅确认超时也返回，因为可能服务器不会为每个订阅发送确认
                            return
                    else:
                        logger.warning(f"⚠️ Opinion reconnection attempt {self.reconnect_attempts} timed out")

                except Exception as e:
                    logger.error(f"❌ Opinion reconnection error: {e}")

                # Exponential backoff
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

            if self.reconnect_attempts >= self.max_reconnect_attempts:
                logger.error(f"❌ Opinion WebSocket failed after {self.max_reconnect_attempts} reconnection attempts")
            elif not self.auto_reconnect:
                logger.info("🛑 Opinion auto-reconnect disabled")
        finally:
            # Reset reconnecting flag
            with self._reconnect_lock:
                self._reconnecting = False

    def _initialize_orderbook_from_rest(self, token_id: str) -> bool:
        """通过REST API获取单个token的初始订单簿"""
        if not self.opinion_client:
            return False

        try:
            response = self.opinion_client.get_orderbook(token_id)

            if response.errno != 0:
                logger.warning(f"⚠️ Opinion REST API返回错误码 {response.errno} for token {token_id[:20]}...")
                return False

            book = response.result

            # Parse bids and asks
            bids_raw = getattr(book, 'bids', [])
            asks_raw = getattr(book, 'asks', [])

            # Convert to OrderBookLevel format
            bids = []
            for bid in bids_raw[:5]:  # Top 5 levels
                try:
                    price = float(getattr(bid, 'price', 0))
                    size = float(getattr(bid, 'size', 0) or getattr(bid, 'quantity', 0) or getattr(bid, 'makerAmountInBaseToken', 0))
                    if price > 0 and size > 0:
                        bids.append(OrderBookLevel(price=price, size=size))
                except (ValueError, TypeError):
                    continue

            # Sort bids descending
            bids.sort(key=lambda x: x.price, reverse=True)

            # Similar for asks
            asks = []
            for ask in asks_raw[:5]:  # Top 5 levels
                try:
                    price = float(getattr(ask, 'price', 0))
                    size = float(getattr(ask, 'size', 0) or getattr(ask, 'quantity', 0) or getattr(ask, 'makerAmountInBaseToken', 0))
                    if price > 0 and size > 0:
                        asks.append(OrderBookLevel(price=price, size=size))
                except (ValueError, TypeError):
                    continue

            # Sort asks ascending
            asks.sort(key=lambda x: x.price)

            # Create and cache snapshot
            snapshot = OrderBookSnapshot(
                bids=bids,
                asks=asks,
                source="opinion",
                token_id=token_id,
                timestamp=time.time()
            )

            with self.lock:
                self.orderbook_cache[token_id] = snapshot
                self.orderbook_last_update[token_id] = time.time()  # 记录初始化时间

            logger.debug(f"✅ [Opinion REST] 初始化订单簿: token={token_id[:20]}..., bids={len(bids)}, asks={len(asks)}")
            return True

        except Exception as e:
            logger.error(f"❌ [Opinion REST] 获取初始订单簿失败 (token={token_id[:20]}...): {e}")
            return False

    def connect(self, market_ids: List[int], token_ids: Optional[List[str]] = None) -> bool:
        """
        建立WebSocket连接并订阅市场

        Args:
            market_ids: 要订阅的market ID列表
            token_ids: 可选的token ID列表，用于预先通过REST API获取初始订单簿

        Returns:
            连接是否成功
        """
        self.subscribed_markets = set(market_ids)

        # 如果提供了token_ids，先通过REST API获取初始订单簿
        if token_ids and self.opinion_client:
            logger.info(f"📥 [Opinion REST] 通过REST API获取 {len(token_ids)} 个token的初始订单簿...")
            success_count = 0
            for token_id in token_ids:
                if self._initialize_orderbook_from_rest(token_id):
                    success_count += 1
            logger.info(f"✅ [Opinion REST] 初始订单簿获取完成: {success_count}/{len(token_ids)} 成功，缓存了 {len(self.orderbook_cache)} 个订单簿")

        if not self.config.opinion_api_key:
            logger.error("❌ Opinion API key not configured")
            return False

        url = f"wss://ws.opinion.trade?apikey={self.config.opinion_api_key}"
        logger.info(f"🔗 Connecting to Opinion WebSocket...")

        self.ws = WebSocketApp(
            url,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
        )

        # Run in background thread with SSL options
        threading.Thread(target=lambda: self.ws.run_forever(sslopt=self.SSL_OPTIONS), daemon=True).start()

        # Wait for connection
        if self.connected.wait(timeout=10):
            logger.info(f"✅ Opinion WebSocket ready ({len(market_ids)} markets)")
            return True
        else:
            logger.error("❌ Opinion WebSocket connection timeout")
            return False

    def get_orderbook(self, token_id: str) -> Optional[OrderBookSnapshot]:
        """
        获取缓存的订单簿，如果订单簿过期则自动刷新

        Args:
            token_id: Token ID

        Returns:
            订单簿快照，如果不存在或刷新失败则返回None
        """
        with self.lock:
            snapshot = self.orderbook_cache.get(token_id)
            last_update = self.orderbook_last_update.get(token_id, 0)

        # 检查订单簿是否过期
        if snapshot and last_update > 0:
            age = time.time() - last_update
            if age > self.orderbook_stale_threshold:
                logger.warning(
                    f"⚠️ [Opinion] 订单簿过期! token={token_id[:20]}..., "
                    f"已 {age:.1f}秒 未更新（阈值={self.orderbook_stale_threshold}s）"
                )
                # 尝试通过REST API刷新
                if self._initialize_orderbook_from_rest(token_id):
                    logger.info(f"✅ [Opinion] 过期订单簿已通过REST API刷新")
                    with self.lock:
                        snapshot = self.orderbook_cache.get(token_id)
                else:
                    logger.error(f"❌ [Opinion] 过期订单簿刷新失败，返回旧数据")

        return snapshot

    def add_callback(self, callback: Callable[[OrderBookUpdate], None]):
        """添加订单簿更新回调"""
        self.callbacks.append(callback)

    def get_staleness_report(self) -> Dict[str, any]:
        """
        获取订单簿时效性报告

        Returns:
            包含过期订单簿统计的字典
        """
        current_time = time.time()
        stale_books = []
        fresh_books = 0

        with self.lock:
            for token_id, last_update in self.orderbook_last_update.items():
                age = current_time - last_update
                if age > self.orderbook_stale_threshold:
                    stale_books.append({
                        'token': token_id[:20] + '...',
                        'age': age,
                        'market_id': self.token_to_market.get(token_id)
                    })
                else:
                    fresh_books += 1

        ws_age = current_time - self.last_message_time

        return {
            'total_books': len(self.orderbook_cache),
            'fresh_books': fresh_books,
            'stale_books': len(stale_books),
            'stale_details': stale_books,
            'ws_last_message_age': ws_age,
            'ws_healthy': ws_age < self.ws_stale_threshold
        }

    def close(self):
        """关闭WebSocket连接"""
        self.is_closing = True
        self.auto_reconnect = False
        if self.ws:
            self.ws.close()


class WebSocketManager:
    """统一的WebSocket管理器,同时管理Polymarket和Opinion连接"""

    def __init__(self, config: ArbitrageConfig, opinion_client=None):
        self.config = config
        self.polymarket_ws = PolymarketWebSocket(config)
        self.opinion_ws = OpinionWebSocket(config, opinion_client=opinion_client)
        self.update_callbacks: List[Callable[[OrderBookUpdate], None]] = []

    def connect_all(self, polymarket_assets: List[str], opinion_markets: List[int],
                    opinion_tokens: Optional[List[str]] = None) -> bool:
        """
        连接到两个平台的WebSocket

        Args:
            polymarket_assets: Polymarket asset IDs
            opinion_markets: Opinion market IDs
            opinion_tokens: 可选的Opinion token IDs，用于预先通过REST API获取初始订单簿

        Returns:
            是否都连接成功
        """
        logger.info("🚀 Connecting to WebSocket streams...")

        # Connect Polymarket
        poly_success = self.polymarket_ws.connect(polymarket_assets)

        # Connect Opinion (with initial orderbook fetch if token_ids provided)
        opinion_success = self.opinion_ws.connect(opinion_markets, token_ids=opinion_tokens)

        if poly_success and opinion_success:
            logger.info("✅ All WebSocket connections established!")
            return True
        else:
            if not poly_success:
                logger.error("❌ Polymarket WebSocket connection failed")
            if not opinion_success:
                logger.error("❌ Opinion WebSocket connection failed")
            return False

    def get_orderbook(self, token_id: str, source: str) -> Optional[OrderBookSnapshot]:
        """
        获取订单簿

        Args:
            token_id: Token ID
            source: 'polymarket' or 'opinion'

        Returns:
            订单簿快照
        """
        if source == "polymarket":
            return self.polymarket_ws.get_orderbook(token_id)
        elif source == "opinion":
            return self.opinion_ws.get_orderbook(token_id)
        return None

    def add_update_callback(self, callback: Callable[[OrderBookUpdate], None]):
        """添加全局订单簿更新回调"""
        self.polymarket_ws.add_callback(callback)
        self.opinion_ws.add_callback(callback)

    def get_stats(self) -> dict:
        """获取统计信息，包括订单簿时效性"""
        opinion_staleness = self.opinion_ws.get_staleness_report()

        return {
            "polymarket": {
                "messages": self.polymarket_ws.message_count,
                "cached_books": len(self.polymarket_ws.orderbook_cache),
                "connected": self.polymarket_ws.connected.is_set()
            },
            "opinion": {
                "messages": self.opinion_ws.message_count,
                "cached_books": len(self.opinion_ws.orderbook_cache),
                "connected": self.opinion_ws.connected.is_set(),
                "fresh_books": opinion_staleness['fresh_books'],
                "stale_books": opinion_staleness['stale_books'],
                "ws_age": opinion_staleness['ws_last_message_age'],
                "ws_healthy": opinion_staleness['ws_healthy']
            }
        }

    def close_all(self):
        """关闭所有WebSocket连接"""
        logger.info("🔌 Closing WebSocket connections...")
        self.polymarket_ws.close()
        self.opinion_ws.close()
