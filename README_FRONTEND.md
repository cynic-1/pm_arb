# Polymarket 订单簿实时监控前端

## 功能特点

✅ **超低延迟显示** - 使用高效的 DOM 更新策略和 Map 数据结构
✅ **实时订单簿** - 买单/卖单双面展示，带深度可视化
✅ **成交记录** - 实时显示最新成交，滑动动画
✅ **关键指标** - 最佳买卖价、价差、最新成交价
✅ **消息日志** - 实时消息追踪
✅ **响应式设计** - 适配不同屏幕尺寸

## 架构说明

```
Polymarket WebSocket → Python 桥接服务器 → 前端页面
                      (websocket_bridge.py)  (orderbook_frontend.html)
```

## 安装依赖

```bash
pip install websockets websocket-client python-dotenv
```

## 使用步骤

### 1. 启动桥接服务器

```bash
python websocket_bridge.py
```

你应该看到：
```
============================================================
WebSocket 桥接服务器启动中...
前端服务器地址: ws://localhost:8765
============================================================
服务器已启动，等待前端连接...
```

### 2. 打开前端页面

在浏览器中打开 `orderbook_frontend.html` 文件：

```bash
# 使用默认浏览器打开
xdg-open orderbook_frontend.html

# 或者使用 Python 的简单 HTTP 服务器
python -m http.server 8000
# 然后访问 http://localhost:8000/orderbook_frontend.html
```

### 3. 连接并订阅

1. 在页面顶部输入框中输入或确认 Asset ID
2. 点击"连接"按钮
3. 等待连接状态变为"已连接"（绿色指示灯）
4. 开始接收实时数据

## 性能优化说明

### 低延迟策略

1. **高效数据结构**
   - 使用 `Map` 而非数组存储订单簿，O(1) 查找/更新
   - 预先计算累计深度，避免重复计算

2. **DOM 优化**
   - 使用 `DocumentFragment` 批量更新
   - 限制显示数量（订单簿 20 档，成交记录 50 条）
   - 仅在数据变化时重新渲染

3. **WebSocket 优化**
   - Python 桥接服务器转发原始消息，无额外处理
   - 并发发送给多个客户端
   - 异步消息处理

4. **渲染优化**
   - CSS 动画使用 GPU 加速
   - 使用 `transform` 而非 `left/top`
   - 滚动容器独立优化

## 显示说明

### 订单簿面板

- **左侧（买单）**: 绿色，价格从高到低排列
- **右侧（卖单）**: 红色，价格从低到高排列
- **深度条**: 显示累计数量的可视化条形图
- **三列数据**: 价格 | 数量 | 累计

### 成交记录面板

- **时间**: 成交时间戳
- **价格**: 绿色（买入）/ 红色（卖出）
- **数量**: 成交数量
- **自动滚动**: 最新成交显示在顶部

### 统计卡片

- **最佳买价**: 当前最高买入价
- **最佳卖价**: 当前最低卖出价
- **价差**: 卖价 - 买价
- **最新成交价**: 最近一笔成交的价格

## 自定义配置

### 修改显示数量

在 `orderbook_frontend.html` 中修改：

```javascript
const maxTrades = 50;        // 最大成交记录数
const maxLogEntries = 100;   // 最大日志条目数

// 在 renderOrderBook() 函数中
.slice(0, 20);  // 改为想要的订单簿档位数
```

### 修改颜色主题

在 `<style>` 标签中修改 CSS 变量：

```css
/* 背景色 */
background: #0a0e27;

/* 买单颜色 */
.price.bid { color: #4ade80; }

/* 卖单颜色 */
.price.ask { color: #f87171; }
```

## 常见问题

### Q: 页面显示"连接错误"？
A: 确保 `websocket_bridge.py` 正在运行，并且端口 8765 未被占用。

### Q: 没有数据显示？
A: 检查 Asset ID 是否正确，查看控制台和消息日志获取详细信息。

### Q: 延迟很高怎么办？
A: 
- 确保网络连接稳定
- 减少显示的档位数量
- 关闭浏览器的其他标签页
- 使用本地运行而非远程服务器

### Q: 如何同时监控多个市场？
A: 在新的浏览器标签页中打开多个前端页面，每个订阅不同的 Asset ID。需要修改桥接服务器支持多订阅。

## 技术栈

- **前端**: HTML5 + CSS3 + Vanilla JavaScript
- **后端**: Python 3.7+ + asyncio + websockets
- **WebSocket**: Polymarket CLOB API

## 文件说明

- `orderbook_frontend.html` - 前端页面（单文件，包含所有 HTML/CSS/JS）
- `websocket_bridge.py` - WebSocket 桥接服务器
- `websocket_channel.py` - 原始 Polymarket WebSocket 客户端（参考）

## 下一步改进

- [ ] 添加图表可视化（价格走势、深度图）
- [ ] 支持多市场同时监控
- [ ] 添加数据导出功能
- [ ] 移动端优化
- [ ] 添加声音提醒
- [ ] K线图集成
