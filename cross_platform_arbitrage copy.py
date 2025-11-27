"""
跨平台套利检测器 - Opinion vs Polymarket
检测在两个平台之间同一市场的套利机会
套利条件: Opinion_YES_Price + Polymarket_NO_Price < 1
         或 Polymarket_YES_Price + Opinion_NO_Price < 1
"""

import os
import json
import time
import argparse
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from dotenv import load_dotenv

# Opinion SDK
from opinion_clob_sdk import Client as OpinionClient
from opinion_clob_sdk.model import TopicStatusFilter, TopicType

# Polymarket SDK
from py_clob_client.client import ClobClient
import requests
from py_clob_client.clob_types import ApiCreds

# 加载环境变量
load_dotenv()


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
        
        # 缓存
        self.opinion_markets: List[Dict] = []
        self.polymarket_markets: List[Dict] = []
        self.market_matches: List[MarketMatch] = []
        
        print("✅ 初始化完成!\n")
    
    # ==================== 1. 获取市场数据 ====================
    
    def fetch_opinion_markets(self, max_markets: int = 100) -> List[Dict]:
        """获取 Opinion 的所有活跃市场"""
        print("📊 获取 Opinion 市场...")
        
        all_markets = []
        page = 1
        limit = 20  # Opinion API 限制每页最多 20 条
        
        while len(all_markets) < max_markets:
            response = self.opinion_client.get_markets(
                page=page,
                limit=limit,
                status=TopicStatusFilter.ACTIVATED
            )
            
            if response.errno != 0:
                print(f"❌ 获取失败: {response.errmsg}")
                break
            
            markets = response.result.list
            if not markets:
                print("❌ 无更多市场可获取")
                break
            
            # 转换为字典格式
            for market in markets:
                all_markets.append({
                    'market_id': market.market_id,
                    'title': market.market_title,
                    'yes_token_id': getattr(market, 'yes_token_id', None),
                    'no_token_id': getattr(market, 'no_token_id', None),
                    'volume': getattr(market, 'volume', 0),
                    'status': market.status,
                })
            
            if len(markets) < limit:
                break
            
            page += 1
            
            # 避免请求过多
            if len(all_markets) >= max_markets:
                print(len(all_markets))
                break
        
        self.opinion_markets = all_markets
        print(f"✅ 获取到 {len(all_markets)} 个 Opinion 市场\n")
        return all_markets
    
    def fetch_polymarket_markets(self, max_markets: int = 100) -> List[Dict]:
        """获取 Polymarket 的所有活跃市场"""
        print("📊 获取 Polymarket 市场...")
        
        try:
            all_processed_markets = []
            offset = 0
            limit_per_request = 100
            
            # 使用 while 循环分页获取，直到达到 max_markets
            while len(all_processed_markets) < max_markets:
                params = {
                    'limit': min(limit_per_request, max_markets - len(all_processed_markets)),
                    'offset': offset,
                    'active': 'true',
                    'closed': 'false',
                    'order': 'volume',
                    'ascending': 'false'
                }
                
                response = requests.get(f"{self.gamma_api}/markets", params=params)
                response.raise_for_status()
                
                markets = response.json()
                
                if not markets or len(markets) == 0:
                    print(f"  已获取所有可用市场")
                    break
                
                print(f"  获取第 {offset + 1}-{offset + len(markets)} 个市场")
                
                # 提取需要的信息
                for market in markets:
                    # 解析 token IDs
                    token_ids_raw = market.get('clobTokenIds', '[]')
                    if isinstance(token_ids_raw, str):
                        token_ids = json.loads(token_ids_raw)
                    else:
                        token_ids = token_ids_raw
                    
                    if len(token_ids) >= 2:
                        all_processed_markets.append({
                            'condition_id': market.get('conditionId'),
                            'question': market.get('question'),
                            'slug': market.get('slug'),
                            'yes_token_id': token_ids[0],
                            'no_token_id': token_ids[1],
                            'volume': float(market.get('volume', 0)),
                            'active': market.get('active', True),
                        })
                        
                        # 达到目标数量，停止
                        if len(all_processed_markets) >= max_markets:
                            break
                
                # 如果返回的市场数少于请求的数量，说明没有更多数据了
                if len(markets) < params['limit']:
                    break
                
                offset += len(markets)
                time.sleep(0.2)  # 避免请求过快
            
            self.polymarket_markets = all_processed_markets
            print(f"✅ 获取到 {len(all_processed_markets)} 个 Polymarket 市场\n")
            return all_processed_markets
            
        except Exception as e:
            print(f"❌ 获取 Polymarket 市场失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def search_polymarket_market(self, query: str) -> Optional[Dict]:
        """
        在 Polymarket 搜索特定市场
        
        Args:
            query: 搜索关键词
            
        Returns:
            匹配的市场信息
        """
        try:
            # 使用 Gamma API 的搜索端点
            response = requests.get(
                f"{self.gamma_api}/public-search",
                params={'q': query}
            )
            response.raise_for_status()
            results = response.json()
            
            # 解析搜索结果结构: {'events': [...], 'pagination': {...}}
            events = results.get('events', [])
            
            if not events or len(events) == 0:
                return None
            
            # 获取第一个事件
            event = events[0]
            
            # 从事件中获取第一个市场
            markets = event.get('markets', [])
            if not markets or len(markets) == 0:
                return None
            
            market = markets[0]
            
            # 解析 token IDs
            token_ids_raw = market.get('clobTokenIds', '[]')
            if isinstance(token_ids_raw, str):
                token_ids = json.loads(token_ids_raw)
            else:
                token_ids = token_ids_raw
            
            if len(token_ids) >= 2:
                return {
                    'condition_id': market.get('conditionId'),
                    'question': market.get('question'),
                    'slug': market.get('slug'),
                    'yes_token_id': token_ids[0],
                    'no_token_id': token_ids[1],
                    'volume': float(market.get('volume', 0)),
                    'active': market.get('active', True),
                }
            
            return None
            
        except Exception as e:
            print(f"  搜索失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    # ==================== 2. 市场匹配 ====================
    
    def match_markets_by_search(self) -> List[MarketMatch]:
        """
        使用搜索 API 匹配两个平台的相同市场（更准确）
        
        Returns:
            匹配的市场对列表
        """
        print("🔍 使用搜索 API 匹配市场...")
        
        matches = []
        
        for i, op_market in enumerate(self.opinion_markets, 1):
            op_title = op_market['title']
            
            print(f"[{i}/{len(self.opinion_markets)}] 搜索: {op_title[:60]}...")
            
            # 在 Polymarket 搜索这个市场
            pm_market = self.search_polymarket_market(op_title)
            
            if pm_market:
                match = MarketMatch(
                    question=op_title,
                    opinion_market_id=op_market['market_id'],
                    opinion_yes_token=op_market['yes_token_id'] or "",
                    opinion_no_token=op_market['no_token_id'] or "",
                    polymarket_condition_id=pm_market['condition_id'],
                    polymarket_yes_token=pm_market['yes_token_id'],
                    polymarket_no_token=pm_market['no_token_id'],
                    polymarket_slug=pm_market['slug'],
                    similarity_score=1.0  # 搜索结果认为是高度匹配
                )
                matches.append(match)
                print(f"  ✓ 找到匹配: {pm_market['question'][:60]}...")
            else:
                print(f"  ✗ 未找到匹配")
            
            time.sleep(0.3)  # 避免请求过快
        
        self.market_matches = matches
        print(f"\n✅ 共匹配到 {len(matches)} 个市场对\n")
        return matches
    
    def match_markets(self, similarity_threshold: float = 0.8) -> List[MarketMatch]:
        """
        匹配两个平台的相同市场（使用本地相似度计算）
        
        Args:
            similarity_threshold: 相似度阈值 (0-1)
        
        Returns:
            匹配的市场对列表
        """
        print("🔍 开始匹配市场...")
        
        matches = []
        
        for op_market in self.opinion_markets:
            op_title = op_market['title'].lower().strip()
            
            for pm_market in self.polymarket_markets:
                pm_question = pm_market['question'].lower().strip()
                
                # 简单的相似度计算（可以使用更复杂的算法）
                similarity = self._calculate_similarity(op_title, pm_question)
                
                if similarity >= similarity_threshold:
                    match = MarketMatch(
                        question=op_market['title'],
                        opinion_market_id=op_market['market_id'],
                        opinion_yes_token=op_market['yes_token_id'] or "",
                        opinion_no_token=op_market['no_token_id'] or "",
                        polymarket_condition_id=pm_market['condition_id'],
                        polymarket_yes_token=pm_market['yes_token_id'],
                        polymarket_no_token=pm_market['no_token_id'],
                        polymarket_slug=pm_market['slug'],
                        similarity_score=similarity
                    )
                    matches.append(match)
                    print(f"  ✓ 匹配: {op_market['title'][:60]}... (相似度: {similarity:.2f})")
        
        self.market_matches = matches
        print(f"\n✅ 共匹配到 {len(matches)} 个市场对\n")
        return matches
    
    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """
        计算两个文本的相似度
        这里使用简单的词汇重叠度，可以改进为更复杂的算法
        """
        # 分词（简单按空格分割）
        words1 = set(text1.split())
        words2 = set(text2.split())
        
        # Jaccard 相似度
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        if len(union) == 0:
            return 0.0
        
        return len(intersection) / len(union)
    
    # ==================== 3. 获取订单簿 ====================
    
    def get_opinion_orderbook(self, token_id: str) -> Tuple[Optional[float], Optional[float]]:
        """
        获取 Opinion 订单簿的最优买价和卖价
        
        Returns:
            (best_bid, best_ask)
        """
        try:
            response = self.opinion_client.get_orderbook(token_id)
            
            if response.errno != 0:
                return None, None
            
            book = response.result
            
            # 排序
            bids = sorted(book.bids, key=lambda x: float(x.price), reverse=True) if book.bids else []
            asks = sorted(book.asks, key=lambda x: float(x.price)) if book.asks else []
            
            best_bid = float(bids[0].price) if bids else None
            best_ask = float(asks[0].price) if asks else None
            
            return best_bid, best_ask
            
        except Exception as e:
            print(f"❌ 获取 Opinion 订单簿失败 ({token_id[:20]}...): {e}")
            return None, None
    
    def get_polymarket_orderbook(self, token_id: str) -> Tuple[Optional[float], Optional[float]]:
        """
        获取 Polymarket 订单簿的最优买价和卖价
        
        Returns:
            (best_bid, best_ask)
        """
        try:
            book = self.polymarket_client.get_order_book(token_id)
            
            if not book:
                return None, None
            
            # 获取订单列表
            bids = book.bids if hasattr(book, 'bids') else []
            asks = book.asks if hasattr(book, 'asks') else []
            
            # 排序：bids 按价格降序，asks 按价格升序
            bids = sorted(bids, key=lambda x: float(x.price), reverse=True) if bids else []
            asks = sorted(asks, key=lambda x: float(x.price)) if asks else []
            
            best_bid = float(bids[0].price) if bids and hasattr(bids[0], 'price') else None
            best_ask = float(asks[0].price) if asks and hasattr(asks[0], 'price') else None
            
            return best_bid, best_ask
            
        except Exception as e:
            print(f"❌ 获取 Polymarket 订单簿失败 ({token_id[:20]}...): {e}")
            return None, None
    # ==================== 4. 套利执行 ====================
    
    def calculate_arbitrage_size(
        self, 
        opp: ArbitrageOpportunity,
        max_investment: float = 100.0
    ) -> Tuple[float, float]:
        """
        计算套利的最优下单数量
        
        Args:
            opp: 套利机会
            max_investment: 最大投资金额（美元）
            
        Returns:
            (投资金额, 预期利润)
        """
        # 套利成本
        cost_per_unit = opp.cost
        
        # 可以买入的最大份数
        max_units = max_investment / cost_per_unit
        
        # 实际投资金额
        investment = max_units * cost_per_unit
        
        # 预期利润
        expected_profit = max_units * opp.profit
        
        return investment, expected_profit
    
    def wait_for_order_fill(
        self,
        platform: str,
        order_id: str,
        max_wait_seconds: int = 300,
        check_interval: int = 5
    ) -> bool:
        """
        等待订单完全成交
        
        Args:
            platform: 'opinion' 或 'polymarket'
            order_id: 订单 ID
            max_wait_seconds: 最大等待时间（秒）
            check_interval: 检查间隔（秒）
            
        Returns:
            是否完全成交
        """
        import time
        
        print(f"\n⏳ 等待订单成交 (最多等待 {max_wait_seconds} 秒)...")
        print(f"   平台: {platform}, 订单 ID: {order_id}")
        
        elapsed = 0
        while elapsed < max_wait_seconds:
            try:
                if platform == 'opinion':
                    # 查询 Opinion 订单状态
                    print(f"\n[DEBUG] 查询 Opinion 订单状态...")
                    order_response = self.opinion_client.get_order(order_id)
                    print(f"[DEBUG] Response errno: {order_response.errno}")
                    
                    if order_response.errno == 0:
                        order = order_response.result
                        print(f"[DEBUG] 订单对象: {order}")
                        print(f"[DEBUG] 订单属性: {dir(order)}")
                        
                        status = order.status if hasattr(order, 'status') else None
                        filled_amount = float(order.filled_amount) if hasattr(order, 'filled_amount') else 0
                        total_amount = float(order.original_amount) if hasattr(order, 'original_amount') else 0
                        
                        print(f"[DEBUG] status={status}, filled={filled_amount}, total={total_amount}")
                        
                        if status == 2:  # 完全成交
                            print(f"✅ Opinion 订单已完全成交!")
                            return True
                        elif status == 3:  # 已取消
                            print(f"❌ Opinion 订单已被取消")
                            return False
                        else:
                            fill_rate = (filled_amount / total_amount * 100) if total_amount > 0 else 0
                            print(f"   进度: {fill_rate:.1f}% ({filled_amount:.4f}/{total_amount:.4f} shares)")
                    else:
                        print(f"[DEBUG] 查询失败: {order_response.errmsg}")
                
                elif platform == 'polymarket':
                    # 查询 Polymarket 订单状态
                    print(f"\n[DEBUG] 查询 Polymarket 订单状态...")
                    from py_clob_client.clob_types import OpenOrderParams
                    
                    orders = self.polymarket_client.get_orders(OpenOrderParams())
                    print(f"[DEBUG] 获取到 {len(orders)} 个未完成订单")
                    
                    order = None
                    for o in orders:
                        print(f"[DEBUG] 订单: {o.get('id')} vs 目标: {order_id}")
                        if o.get('id') == order_id:
                            order = o
                            break
                    
                    if not order:
                        # 订单不在未完成列表中，可能已完全成交或取消
                        print(f"[DEBUG] 订单不在未完成列表，检查交易历史...")
                        self.polymarket_client.set_api_creds(
                            self.polymarket_client.create_or_derive_api_creds()
                        )
                        trades = self.polymarket_client.get_trades()
                        print(f"[DEBUG] 获取到 {len(trades)} 条交易记录")
                        
                        for trade in trades:
                            trade_order_id = trade.get('order_id') or trade.get('orderId')
                            print(f"[DEBUG] 交易订单 ID: {trade_order_id}")
                            if trade_order_id == order_id:
                                print(f"✅ Polymarket 订单已完全成交!")
                                return True
                        
                        print(f"❌ Polymarket 订单未找到或已取消")
                        return False
                    else:
                        # 订单仍在进行中
                        print(f"[DEBUG] 订单详情: {order}")
                        size = float(order.get('size', 0))
                        size_matched = float(order.get('size_matched', 0))
                        fill_rate = (size_matched / size * 100) if size > 0 else 0
                        print(f"   进度: {fill_rate:.1f}% ({size_matched:.4f}/{size:.4f} shares)")
                
                time.sleep(check_interval)
                elapsed += check_interval
                
            except Exception as e:
                print(f"   查询订单状态出错: {e}")
                import traceback
                print(f"[DEBUG] 详细错误信息:")
                traceback.print_exc()
                time.sleep(check_interval)
                elapsed += check_interval
        
        print(f"⚠️ 超时: 订单未在 {max_wait_seconds} 秒内完全成交")
        return False
    
    def execute_arbitrage(
        self, 
        opp: ArbitrageOpportunity,
        shares: float
    ) -> bool:
        """
        执行套利交易
        
        Args:
            opp: 套利机会
            shares: 购买的份数（以 share 为单位）
            
        Returns:
            是否成功
        """
        from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
        from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
        from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
        
        try:
            print(f"\n{'='*80}")
            print(f"开始执行套利交易")
            print(f"{'='*80}\n")
            
            match = opp.market_match
            
            # 计算投资金额
            total_cost = shares * opp.cost
            opinion_cost = shares * (opp.opinion_yes_ask if opp.strategy == "opinion_yes_poly_no" else opp.opinion_no_ask)
            poly_cost = shares * (opp.poly_no_ask if opp.strategy == "opinion_yes_poly_no" else opp.poly_yes_ask)
            expected_return = shares * 1.0  # 每份在市场解决后返回 $1
            expected_profit = expected_return - total_cost
            
            print(f"交易详情:")
            print(f"  份数: {shares:.4f} shares")
            print(f"  总成本: ${total_cost:.2f}")
            print(f"  预期回报: ${expected_return:.2f}")
            print(f"  预期利润: ${expected_profit:.2f} ({(expected_profit/total_cost*100):.2f}%)")
            print()
            
            # 执行策略
            if opp.strategy == "opinion_yes_poly_no":
                # 策略1: Opinion YES + Polymarket NO
                print(f"策略: Opinion YES + Polymarket NO")
                print(f"  - Opinion YES: {shares:.4f} shares @ ${opp.opinion_yes_ask:.4f} = ${opinion_cost:.2f}")
                print(f"  - Polymarket NO: {shares:.4f} shares @ ${opp.poly_no_ask:.4f} = ${poly_cost:.2f}")
                
                # 1. Opinion 下单
                print(f"\n1️⃣ 在 Opinion 下限价买单 (YES)...")
                opinion_order = PlaceOrderDataInput(
                    marketId=match.opinion_market_id,
                    tokenId=str(match.opinion_yes_token),
                    side=OrderSide.BUY,
                    orderType=LIMIT_ORDER,
                    price=str(opp.opinion_yes_ask),
                    makerAmountInBaseToken=str(shares)  # 使用 shares 而不是金额
                )
                
                opinion_result = self.opinion_client.place_order(opinion_order)
                
                if opinion_result.errno != 0:
                    print(f"❌ Opinion 下单失败: {opinion_result.errmsg}")
                    return False
                
                opinion_order_id = opinion_result.result.order_id if hasattr(opinion_result.result, 'order_id') else None
                print(f"✅ Opinion 订单已提交")
                if opinion_order_id:
                    print(f"   订单 ID: {opinion_order_id}")
                
                # 等待 Opinion 订单成交
                if opinion_order_id:
                    if not self.wait_for_order_fill('opinion', opinion_order_id):
                        print(f"⚠️ Opinion 订单未完全成交，取消后续操作")
                        return False
                else:
                    print(f"⚠️ 无法获取 Opinion 订单 ID，继续执行...")
                    import time
                    time.sleep(10)  # 等待 10 秒
                
                # 2. Polymarket 下单
                print(f"\n2️⃣ 在 Polymarket 下限价买单 (NO)...")
                poly_order = OrderArgs(
                    token_id=match.polymarket_no_token,
                    price=opp.poly_no_ask,
                    size=shares,  # 直接使用 shares
                    side=BUY
                )
                
                signed_order = self.polymarket_client.create_order(poly_order)
                poly_result = self.polymarket_client.post_order(signed_order, OrderType.GTC)
                
                poly_order_id = poly_result.get('orderID') or poly_result.get('order_id')
                print(f"✅ Polymarket 订单已提交")
                if poly_order_id:
                    print(f"   订单 ID: {poly_order_id}")
                
                # 等待 Polymarket 订单成交
                if poly_order_id:
                    if not self.wait_for_order_fill('polymarket', poly_order_id):
                        print(f"⚠️ Polymarket 订单未完全成交")
                
            else:
                # 策略2: Polymarket YES + Opinion NO
                print(f"策略: Polymarket YES + Opinion NO")
                print(f"  - Polymarket YES: {shares:.4f} shares @ ${opp.poly_yes_ask:.4f} = ${poly_cost:.2f}")
                print(f"  - Opinion NO: {shares:.4f} shares @ ${opp.opinion_no_ask:.4f} = ${opinion_cost:.2f}")
                
                # 1. Polymarket 下单
                print(f"\n1️⃣ 在 Polymarket 下限价买单 (YES)...")
                poly_order = OrderArgs(
                    token_id=match.polymarket_yes_token,
                    price=opp.poly_yes_ask,
                    size=shares,  # 直接使用 shares
                    side=BUY
                )
                
                signed_order = self.polymarket_client.create_order(poly_order)
                poly_result = self.polymarket_client.post_order(signed_order, OrderType.GTC)
                
                poly_order_id = poly_result.get('orderID') or poly_result.get('order_id')
                print(f"✅ Polymarket 订单已提交")
                if poly_order_id:
                    print(f"   订单 ID: {poly_order_id}")
                
                # 等待 Polymarket 订单成交
                if poly_order_id:
                    if not self.wait_for_order_fill('polymarket', poly_order_id):
                        print(f"⚠️ Polymarket 订单未完全成交，取消后续操作")
                        return False
                else:
                    print(f"⚠️ 无法获取 Polymarket 订单 ID，继续执行...")
                    import time
                    time.sleep(10)  # 等待 10 秒
                
                # 2. Opinion 下单
                print(f"\n2️⃣ 在 Opinion 下限价买单 (NO)...")
                opinion_order = PlaceOrderDataInput(
                    marketId=match.opinion_market_id,
                    tokenId=str(match.opinion_no_token),
                    side=OrderSide.BUY,
                    orderType=LIMIT_ORDER,
                    price=str(opp.opinion_no_ask),
                    makerAmountInBaseToken=str(shares)  # 使用 shares 而不是金额
                )
                
                opinion_result = self.opinion_client.place_order(opinion_order)
                
                if opinion_result.errno != 0:
                    print(f"❌ Opinion 下单失败: {opinion_result.errmsg}")
                    return False
                
                opinion_order_id = opinion_result.result.order_id if hasattr(opinion_result.result, 'order_id') else None
                print(f"✅ Opinion 订单已提交")
                if opinion_order_id:
                    print(f"   订单 ID: {opinion_order_id}")
                
                # 等待 Opinion 订单成交
                if opinion_order_id:
                    if not self.wait_for_order_fill('opinion', opinion_order_id):
                        print(f"⚠️ Opinion 订单未完全成交")
            
            print(f"\n{'='*80}")
            print(f"✅ 套利交易执行完成!")
            print(f"{'='*80}\n")
            
            return True
            
        except Exception as e:
            print(f"\n❌ 执行套利交易时出错: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def interactive_arbitrage_execution(
        self, 
        opportunities: List[ArbitrageOpportunity],
        default_shares: float = 100.0
    ):
        """
        交互式套利执行
        
        Args:
            opportunities: 套利机会列表
            default_shares: 默认购买份数
        """
        if not opportunities:
            print("没有套利机会可执行")
            return
        
        # 按利润率排序
        sorted_opps = sorted(opportunities, key=lambda x: x.profit_rate, reverse=True)
        
        print(f"\n{'='*100}")
        print(f"套利机会列表 (共 {len(opportunities)} 个)")
        print(f"{'='*100}\n")
        
        for i, opp in enumerate(sorted_opps, 1):
            match = opp.market_match
            # 计算默认份数对应的成本和利润
            total_cost = default_shares * opp.cost
            expected_return = default_shares * 1.0
            expected_profit = expected_return - total_cost
            
            print(f"{i}. {match.question[:70]}")
            print(f"   策略: {self._get_strategy_name(opp.strategy)}")
            print(f"   成本: ${opp.cost:.4f}/share | 利润率: {opp.profit_rate:.2f}%")
            print(f"   {default_shares:.0f} shares → 成本 ${total_cost:.2f} → 利润 ${expected_profit:.2f}")
            print()
        
        # 用户选择
        while True:
            try:
                print("\n" + "="*100)
                choice = input(f"请选择要执行的套利机会 (1-{len(sorted_opps)})，或输入 'q' 退出: ").strip()
                
                if choice.lower() == 'q':
                    print("退出套利执行")
                    break
                
                idx = int(choice) - 1
                if idx < 0 or idx >= len(sorted_opps):
                    print(f"❌ 无效选择，请输入 1-{len(sorted_opps)}")
                    continue
                
                selected_opp = sorted_opps[idx]
                
                # 显示详情
                print(f"\n{'='*100}")
                print(f"选择的套利机会:")
                print(f"{'='*100}")
                print(f"市场: {selected_opp.market_match.question}")
                print(f"策略: {self._get_strategy_name(selected_opp.strategy)}")
                print(f"单份成本: ${selected_opp.cost:.4f}")
                print(f"利润率: {selected_opp.profit_rate:.2f}%")
                
                # 输入购买份数
                shares_input = input(f"\n请输入购买份数 (默认 {default_shares:.0f} shares): ").strip()
                shares = float(shares_input) if shares_input else default_shares
                
                # 计算详情
                total_cost = shares * selected_opp.cost
                expected_return = shares * 1.0
                expected_profit = expected_return - total_cost
                
                print(f"\n交易详情:")
                print(f"  购买份数: {shares:.4f} shares")
                print(f"  总成本: ${total_cost:.2f}")
                print(f"  预期回报: ${expected_return:.2f}")
                print(f"  预期利润: ${expected_profit:.2f} ({selected_opp.profit_rate:.2f}%)")
                
                # 确认执行
                confirm = input(f"\n确认执行此套利交易? (y/n): ").strip().lower()
                
                if confirm == 'y':
                    success = self.execute_arbitrage(selected_opp, shares)
                    
                    if success:
                        print(f"\n✅ 套利交易已成功执行!")
                        
                        # 询问是否继续
                        continue_choice = input(f"\n是否继续执行其他套利? (y/n): ").strip().lower()
                        if continue_choice != 'y':
                            break
                    else:
                        print(f"\n❌ 套利交易执行失败")
                else:
                    print("已取消执行")
                
            except ValueError:
                print("❌ 输入无效，请重试")
            except KeyboardInterrupt:
                print("\n\n用户中断")
                break
            except Exception as e:
                print(f"❌ 发生错误: {e}")
    
    # ==================== 5. 套利检测 ====================
    
    def detect_arbitrage(self) -> List[ArbitrageOpportunity]:
        """
        检测所有匹配市场的套利机会
        
        套利逻辑:
        - 策略1: 买 Opinion YES + 买 Polymarket NO，总成本 < 1，必赢 $1
        - 策略2: 买 Polymarket YES + 买 Opinion NO，总成本 < 1，必赢 $1
        """
        print("🔍 开始检测套利机会...\n")
        
        opportunities = []
        
        for i, match in enumerate(self.market_matches, 1):
            print(f"[{i}/{len(self.market_matches)}] 检查: {match.question}...")
            
            # 获取 Opinion 订单簿
            op_yes_bid, op_yes_ask = self.get_opinion_orderbook(match.opinion_yes_token)
            op_no_bid, op_no_ask = self.get_opinion_orderbook(match.opinion_no_token)
            
            # 获取 Polymarket 订单簿
            pm_yes_bid, pm_yes_ask = self.get_polymarket_orderbook(match.polymarket_yes_token)
            pm_no_bid, pm_no_ask = self.get_polymarket_orderbook(match.polymarket_no_token)
            
            print(f"  Opinion YES: bid={op_yes_bid}, ask={op_yes_ask}")
            print(f"  Opinion NO:  bid={op_no_bid}, ask={op_no_ask}")
            print(f"  Poly YES:    bid={pm_yes_bid}, ask={pm_yes_ask}")
            print(f"  Poly NO:     bid={pm_no_bid}, ask={pm_no_ask}")
            
            # 策略1: 买 Opinion YES + 买 Polymarket NO
            if op_yes_ask is not None and pm_no_ask is not None:
                cost1 = op_yes_ask + pm_no_ask
                if cost1 < 1.0:
                    profit1 = 1.0 - cost1
                    profit_rate1 = (profit1 / cost1) * 100
                    
                    opp = ArbitrageOpportunity(
                        market_match=match,
                        strategy="opinion_yes_poly_no",
                        opinion_yes_bid=op_yes_bid,
                        opinion_yes_ask=op_yes_ask,
                        poly_no_bid=pm_no_bid,
                        poly_no_ask=pm_no_ask,
                        cost=cost1,
                        profit=profit1,
                        profit_rate=profit_rate1,
                        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    opportunities.append(opp)
                    print(f"  🎯 套利机会! 策略1: 成本=${cost1:.4f}, 利润=${profit1:.4f} ({profit_rate1:.2f}%)")
            
            # 策略2: 买 Polymarket YES + 买 Opinion NO
            if pm_yes_ask is not None and op_no_ask is not None:
                cost2 = pm_yes_ask + op_no_ask
                if cost2 < 1.0:
                    profit2 = 1.0 - cost2
                    profit_rate2 = (profit2 / cost2) * 100
                    
                    opp = ArbitrageOpportunity(
                        market_match=match,
                        strategy="poly_yes_opinion_no",
                        poly_yes_bid=pm_yes_bid,
                        poly_yes_ask=pm_yes_ask,
                        opinion_no_bid=op_no_bid,
                        opinion_no_ask=op_no_ask,
                        cost=cost2,
                        profit=profit2,
                        profit_rate=profit_rate2,
                        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    )
                    opportunities.append(opp)
                    print(f"  🎯 套利机会! 策略2: 成本=${cost2:.4f}, 利润=${profit2:.4f} ({profit_rate2:.2f}%)")
            
            print()
            time.sleep(0.5)  # 避免请求过快
        
        print(f"\n✅ 检测完成，共发现 {len(opportunities)} 个套利机会\n")
        return opportunities
    
    # ==================== 5. 保存结果 ====================
    
    def load_market_matches(self, filename: str = "market_matches.json") -> bool:
        """
        从本地加载市场匹配结果
        
        Args:
            filename: JSON 文件路径
            
        Returns:
            是否成功加载
        """
        try:
            if not os.path.exists(filename):
                print(f"❌ 文件不存在: {filename}")
                return False
            
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 将字典转换为 MarketMatch 对象
            self.market_matches = [MarketMatch(**item) for item in data]
            
            print(f"✅ 已从 {filename} 加载 {len(self.market_matches)} 个市场匹配")
            return True
            
        except Exception as e:
            print(f"❌ 加载市场匹配失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def save_market_matches(self, filename: str = "market_matches.json"):
        """保存市场匹配结果到本地"""
        data = [asdict(match) for match in self.market_matches]
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"✅ 市场匹配结果已保存到: {filename}")
    
    def save_arbitrage_opportunities(self, opportunities: List[ArbitrageOpportunity], 
                                     filename: str = "arbitrage_opportunities.json"):
        """保存套利机会到本地"""
        data = []
        for opp in opportunities:
            opp_dict = asdict(opp)
            opp_dict['market_match'] = asdict(opp.market_match)
            data.append(opp_dict)
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"✅ 套利机会已保存到: {filename}")
    
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
                print(f"   执行: 买入 Opinion YES @ ${opp.opinion_yes_ask:.4f} + 买入 Polymarket NO @ ${opp.poly_no_ask:.4f}")
            else:
                print(f"   执行: 买入 Polymarket YES @ ${opp.poly_yes_ask:.4f} + 买入 Opinion NO @ ${opp.opinion_no_ask:.4f}")
            
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
    
    # ==================== 7. 主流程 ====================
    
    def run_full_scan(
        self, 
        use_search: bool = True, 
        interactive: bool = True,
        use_cached_matches: bool = False,
        matches_file: str = "market_matches.json"
    ):
        """
        运行完整的套利扫描流程
        
        Args:
            use_search: 是否使用搜索 API 匹配市场
            interactive: 是否进入交互式执行模式
            use_cached_matches: 是否使用缓存的市场匹配结果
            matches_file: 市场匹配结果文件路径
        """
        print(f"\n{'='*100}")
        print("开始跨平台套利扫描")
        print(f"{'='*100}\n")
        
        # Step 1 & 2: 获取或加载市场匹配
        if use_cached_matches:
            # 使用缓存的匹配结果
            print(f"📁 使用缓存的市场匹配结果...")
            if not self.load_market_matches(matches_file):
                print("⚠️ 加载失败，将重新获取并匹配市场...")
                use_cached_matches = False
        
        if not use_cached_matches:
            # 重新获取并匹配市场
            # Step 1: 获取 Opinion 市场
            self.fetch_opinion_markets(max_markets=100)
            
            # Step 2: 匹配市场
            if use_search:
                # 使用搜索 API 匹配
                print("使用搜索 API 进行精确匹配...")
                self.match_markets_by_search()
            else:
                # 使用本地相似度匹配（需要先获取 Polymarket 市场）
                print("使用本地相似度算法匹配...")
                # 获取足够多的 Polymarket 市场以提高匹配率
                self.fetch_polymarket_markets(max_markets=2000)
                self.match_markets(similarity_threshold=0.9)  # 降低阈值以匹配更多
            
            if not self.market_matches:
                print("❌ 未找到匹配的市场，退出")
                return None
            
            # 保存匹配结果
            self.save_market_matches(matches_file)
        
        if not self.market_matches:
            print("❌ 没有可用的市场匹配，退出")
            return None
        
        # Step 3: 检测套利
        opportunities = self.detect_arbitrage()
        
        # Step 4: 保存和显示结果
        if opportunities:
            self.save_arbitrage_opportunities(opportunities)
            self.display_arbitrage_summary(opportunities)
            
            # Step 5: 交互式执行（可选）
            if interactive:
                execute_choice = input("\n是否进入交互式套利执行模式? (y/n): ").strip().lower()
                if execute_choice == 'y':
                    self.interactive_arbitrage_execution(opportunities, default_shares=100.0)
        else:
            print("❌ 未发现套利机会")
        
        print(f"\n{'='*100}")
        print("扫描完成!")
        print(f"{'='*100}\n")
        
        return opportunities
    
    def test(self):
        """测试函数"""
        from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
        from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
        from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY
    
        print(f"\n{'='*80}")
        print(f"开始执行套利交易")
        print(f"{'='*80}\n")

        # 1. Opinion 下单
        print(f"\n1️⃣ 在 Opinion 下限价买单 (YES)...")
        opinion_order = PlaceOrderDataInput(
            marketId=1384,
            tokenId="15667508119522618704974492339108806331160935332314347072444716606165452203109",
            side=OrderSide.BUY,
            orderType=LIMIT_ORDER,
            price=str(0.1),
            makerAmountInBaseToken=str(60.0)
        )
            
        opinion_result = self.opinion_client.place_order(opinion_order)
            
        if opinion_result.errno != 0:
            print(f"❌ Opinion 下单失败: {opinion_result.errmsg}")
            return False
        
        print(f"✅ Opinion 订单已提交")
        return    
        # 2. Polymarket 下单
        print(f"\n2️⃣ 在 Polymarket 下限价买单 (NO)...")
        poly_size = poly_investment / opp.poly_no_ask  # 计算份数
        
        poly_order = OrderArgs(
            token_id=match.polymarket_no_token,
            price=opp.poly_no_ask,
            size=poly_size,
            side=BUY
        )
        
        signed_order = self.polymarket_client.create_order(poly_order)
        poly_result = self.polymarket_client.post_order(signed_order, OrderType.GTC)
        
        print(f"✅ Polymarket 订单已提交")
        print(f"   订单 ID: {poly_result.get('orderID', 'N/A')}")


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
        help='市场匹配结果文件路径 (默认: market_matches.json)'
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
    
    args = parser.parse_args()
    
    try:
        scanner = CrossPlatformArbitrage()
        if args.test:
            scanner.test()
            return
        scanner.run_full_scan(
            use_search=not args.no_search,
            interactive=not args.no_interactive,
            use_cached_matches=args.use_cached,
            matches_file=args.matches_file
        )
        
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
