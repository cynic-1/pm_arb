#!/usr/bin/env python3
"""
WebSocket 桥接服务器
连接 Polymarket WebSocket 并转发数据到前端
"""

import asyncio
import websockets
import json
import os
import dotenv
from websocket import WebSocketApp
import threading
import time
from datetime import datetime

dotenv.load_dotenv()

# 存储连接的前端客户端
frontend_clients = set()
# 存储订单簿数据
orderbook_data = {}
# Polymarket WebSocket 连接
pm_ws = None
# 当前订阅的 asset_id
current_asset_id = None
# 事件循环
loop = None

def on_pm_message(ws, message):
    """处理来自 Polymarket 的消息"""
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 收到 Polymarket 消息")
    
    # 转发给所有前端客户端
    if frontend_clients and loop:
        # 在事件循环中调度异步任务
        asyncio.run_coroutine_threadsafe(broadcast_to_clients(message), loop)

def on_pm_error(ws, error):
    """处理 Polymarket 错误"""
    print(f"Polymarket WebSocket 错误: {error}")

def on_pm_close(ws, close_status_code, close_msg):
    """处理 Polymarket 连接关闭"""
    print(f"Polymarket WebSocket 关闭: code={close_status_code} msg={close_msg}")

def on_pm_open(ws):
    """Polymarket 连接打开"""
    print("Polymarket WebSocket 连接已打开")
    
    if current_asset_id:
        # 订阅市场数据
        msg = {"assets_ids": [current_asset_id], "type": "market"}
        ws.send(json.dumps(msg))
        print(f"已订阅 asset_id: {current_asset_id}")
        
        # 启动 ping 线程
        def ping_loop():
            while True:
                try:
                    ws.send("PING")
                    time.sleep(10)
                except:
                    break
        
        ping_thread = threading.Thread(target=ping_loop, daemon=True)
        ping_thread.start()

def connect_to_polymarket(asset_id):
    """连接到 Polymarket WebSocket"""
    global pm_ws, current_asset_id
    
    current_asset_id = asset_id
    url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    pm_ws = WebSocketApp(
        url,
        on_message=on_pm_message,
        on_error=on_pm_error,
        on_close=on_pm_close,
        on_open=on_pm_open,
    )
    
    # 在新线程中运行 WebSocket
    ws_thread = threading.Thread(target=pm_ws.run_forever, daemon=True)
    ws_thread.start()
    print(f"已启动 Polymarket WebSocket 连接线程")

async def broadcast_to_clients(message):
    """广播消息给所有前端客户端"""
    if frontend_clients:
        # 创建发送任务列表
        tasks = [client.send(message) for client in frontend_clients]
        # 并发发送，忽略错误
        await asyncio.gather(*tasks, return_exceptions=True)

async def handle_frontend_client(websocket):
    """处理前端客户端连接"""
    print(f"新的前端客户端连接: {websocket.remote_address}")
    frontend_clients.add(websocket)
    
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                
                if data.get('type') == 'subscribe':
                    asset_id = data.get('asset_id')
                    if asset_id:
                        print(f"前端请求订阅: {asset_id}")
                        # 连接到 Polymarket
                        connect_to_polymarket(asset_id)
                        # 发送确认
                        await websocket.send(json.dumps({
                            'type': 'subscribed',
                            'asset_id': asset_id
                        }))
                        
            except json.JSONDecodeError:
                print(f"无法解析消息: {message}")
            except Exception as e:
                print(f"处理消息错误: {e}")
                
    except websockets.exceptions.ConnectionClosed:
        print(f"前端客户端断开: {websocket.remote_address}")
    finally:
        frontend_clients.remove(websocket)

async def main():
    """启动 WebSocket 服务器"""
    global loop
    loop = asyncio.get_event_loop()
    
    print("=" * 60)
    print("WebSocket 桥接服务器启动中...")
    print("前端服务器地址: ws://localhost:8765")
    print("=" * 60)
    
    async with websockets.serve(handle_frontend_client, "localhost", 8765):
        print("服务器已启动，等待前端连接...")
        await asyncio.Future()  # 永久运行

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n服务器已停止")
