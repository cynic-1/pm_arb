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
import threading
from typing import Any, Dict, List, Optional, Tuple
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
        fallback_env = os.getenv("ORDER_STATUS_FALLBACK_AFTER")
        self.order_status_fallback_after: Optional[float] = None
        if fallback_env:
            try:
                self.order_status_fallback_after = float(fallback_env)
            except ValueError:
                print("⚠️ ORDER_STATUS_FALLBACK_AFTER 环境变量不是有效数字，将忽略。")
        
        print("✅ 初始化完成!\n")
    
    # ==================== 1. 获取市场数据 ====================
    
    def fetch_opinion_markets(self, max_markets: int = 200, topic_type: TopicType = TopicType.BINARY) -> List[Dict]:
        """
        获取 Opinion 的所有活跃市场
        
        Args:
            max_markets: 最大市场数量
            topic_type: 市场类型 (TopicType.BINARY 或 TopicType.CATEGORICAL)
        """
        print(f"📊 获取 Opinion 市场 (类型: {topic_type})...")
        
        all_markets = []
        page = 1
        limit = 20  # Opinion API 限制每页最多 20 条
        father_count = 0
        
        while father_count < max_markets:
            response = self.opinion_client.get_markets(
                page=page,
                limit=limit,
                status=TopicStatusFilter.ACTIVATED,
                topic_type=topic_type
            )

            if response.errno != 0:
                print(f"❌ 获取失败: {response.errmsg}")
                break
            
            markets = response.result.list
            if not markets:
                print("❌ 无更多市场可获取")
                break

            print(f"  获取{len(markets)} 个市场")
            father_count += len(markets)

            # 转换为字典格式
            for market in markets:
                # 检查是否为 CATEGORICAL 类型且有子市场
                child_markets = getattr(market, 'child_markets', None)
                
                if child_markets and len(child_markets) > 0:
                    # CATEGORICAL 类型: 展平子市场
                    parent_title = market.market_title
                    for child in child_markets:
                        if child.status_enum != 'Activated':
                            continue
                        # 拼接标题: "父标题 - 子标题"
                        combined_title = f"{parent_title} - {child.market_title}"
                        
                        all_markets.append({
                            'market_id': child.market_id,
                            'title': combined_title,
                            'yes_token_id': getattr(child, 'yes_token_id', None),
                            'no_token_id': getattr(child, 'no_token_id', None),
                            'volume': float(getattr(child, 'volume', 0)),
                            'status': child.status,
                            'parent_market_id': market.market_id,
                            'parent_title': parent_title,
                            'child_title': child.market_title,
                        })
                else:
                    # BINARY 类型或无子市场: 直接添加
                    all_markets.append({
                        'market_id': market.market_id,
                        'title': market.market_title,
                        'yes_token_id': getattr(market, 'yes_token_id', None),
                        'no_token_id': getattr(market, 'no_token_id', None),
                        'volume': float(getattr(market, 'volume', 0)),
                        'status': market.status,
                    })

            page += 1
        
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
        在 Polymarket 搜索特定市场（用于 BINARY 类型）
        
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
            
            events = results.get('events', [])
            if not events or len(events) == 0:
                return None
            
            # 获取第一个事件
            event = events[0]
            
            # 从事件中获取所有市场
            markets = event.get('markets', [])
            if not markets or len(markets) == 0:
                return None
            
            # 如果只有一个市场，直接返回
            if len(markets) == 1:
                market = markets[0]
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
            
            # 多个市场时，找出匹配度最高的
            best_match = None
            best_similarity = 0.0
            query_lower = query.lower().strip()
            
            for market in markets:
                market_question = market.get('question', '').lower().strip()
                
                # 计算相似度
                similarity = self._calculate_similarity(query_lower, market_question)
                
                # 如果完全匹配，直接返回
                if similarity >= 0.95 or query_lower == market_question:
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
                
                # 记录最佳匹配
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = market
            
            # 返回最佳匹配（如果相似度 >= 0.6）
            if best_match and best_similarity >= 0.6:
                token_ids_raw = best_match.get('clobTokenIds', '[]')
                if isinstance(token_ids_raw, str):
                    token_ids = json.loads(token_ids_raw)
                else:
                    token_ids = token_ids_raw
                
                if len(token_ids) >= 2:
                    return {
                        'condition_id': best_match.get('conditionId'),
                        'question': best_match.get('question'),
                        'slug': best_match.get('slug'),
                        'yes_token_id': token_ids[0],
                        'no_token_id': token_ids[1],
                        'volume': float(best_match.get('volume', 0)),
                        'active': best_match.get('active', True),
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
        unmatched_groups = []  # 保存未匹配的市场组供手工筛选
        
        # 将 Opinion 市场按类型分组
        binary_markets = []
        categorical_groups = {}  # parent_market_id -> list of child markets
        
        for op_market in self.opinion_markets:
            if 'parent_market_id' in op_market and 'parent_title' in op_market:
                # CATEGORICAL 子市场
                parent_id = op_market['parent_market_id']
                if parent_id not in categorical_groups:
                    categorical_groups[parent_id] = {
                        'parent_title': op_market['parent_title'],
                        'children': []
                    }
                categorical_groups[parent_id]['children'].append(op_market)
            else:
                # BINARY 市场
                binary_markets.append(op_market)
        
        # 处理 BINARY 市场
        print(f"\n📊 处理 {len(binary_markets)} 个 BINARY 市场...")
        for i, op_market in enumerate(binary_markets, 1):
            op_title = op_market['title']
            print(f"[{i}/{len(binary_markets)}] 搜索: {op_title[:60]}...")
            
            pm_market = self.search_polymarket_market(query=op_title)
            
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
                    similarity_score=1.0
                )
                matches.append(match)
                print(f"  ✓ 匹配成功")
            else:
                print(f"  ✗ 未找到匹配")
            
            time.sleep(0.3)
        
        # 处理 CATEGORICAL 市场组
        print(f"\n📊 处理 {len(categorical_groups)} 个 CATEGORICAL 市场组...")
        for group_idx, (parent_id, group_info) in enumerate(categorical_groups.items(), 1):
            parent_title = group_info['parent_title']
            op_children = group_info['children']
            
            print(f"\n[{group_idx}/{len(categorical_groups)}] 父市场: {parent_title}")
            print(f"  Opinion 子市场数: {len(op_children)}")
            
            # 一次性获取 Polymarket 的事件及其子市场
            pm_children = self._fetch_polymarket_event_markets(parent_title)
            
            if not pm_children:
                print(f"  ✗ 未找到 Polymarket 对应事件")
                # 记录未匹配组
                unmatched_groups.append({
                    'parent_title': parent_title,
                    'opinion_children': [
                        {
                            'market_id': child['market_id'],
                            'child_title': child['child_title'],
                            'yes_token_id': child['yes_token_id'],
                            'no_token_id': child['no_token_id'],
                        }
                        for child in op_children
                    ],
                    'polymarket_children': []
                })
                continue
            
            print(f"  Polymarket 子市场数: {len(pm_children)}")
            
            # 批量匹配子市场
            matched, unmatched_op, unmatched_pm = self._match_child_markets(
                op_children, pm_children, parent_title
            )
            
            matches.extend(matched)
            
            # 如果有未匹配的，记录下来
            if unmatched_op or unmatched_pm:
                unmatched_groups.append({
                    'parent_title': parent_title,
                    'opinion_children': [
                        {
                            'market_id': child['market_id'],
                            'child_title': child['child_title'],
                            'yes_token_id': child['yes_token_id'],
                            'no_token_id': child['no_token_id'],
                        }
                        for child in unmatched_op
                    ],
                    'polymarket_children': [
                        {
                            'condition_id': child['condition_id'],
                            'question': child['question'],
                            'slug': child['slug'],
                            'yes_token_id': child['yes_token_id'],
                            'no_token_id': child['no_token_id'],
                        }
                        for child in unmatched_pm
                    ]
                })
            
            time.sleep(0.3)
        
        # 保存未匹配的组
        if unmatched_groups:
            self._save_unmatched_groups(unmatched_groups)
        
        self.market_matches = matches
        print(f"\n✅ 共匹配到 {len(matches)} 个市场对")
        if unmatched_groups:
            print(f"⚠️  有 {len(unmatched_groups)} 个市场组存在未匹配项，已保存到 unmatched_markets.json")
        print()
        return matches
    
    def _fetch_polymarket_event_markets(self, parent_title: str) -> List[Dict]:
        """
        获取 Polymarket 事件下的所有子市场（一次性调用）
        
        Args:
            parent_title: Opinion 父市场标题
            
        Returns:
            Polymarket 子市场列表
        """
        try:
            response = requests.get(
                f"{self.gamma_api}/public-search",
                params={'q': parent_title}
            )
            response.raise_for_status()
            results = response.json()
            
            events = results.get('events', [])
            if not events or len(events) == 0:
                return []
            
            # 获取第一个事件（通常是最匹配的）
            event = events[0]
            event_title = event.get('title', '')
            print(f"  → Polymarket 事件: {event_title[:60]}...")
            
            # 提取所有子市场
            markets = event.get('markets', [])
            if not markets:
                return []
            
            pm_children = []
            for market in markets:
                token_ids_raw = market.get('clobTokenIds', '[]')
                if isinstance(token_ids_raw, str):
                    token_ids = json.loads(token_ids_raw)
                else:
                    token_ids = token_ids_raw
                
                if len(token_ids) >= 2:
                    pm_children.append({
                        'condition_id': market.get('conditionId'),
                        'question': market.get('question'),
                        'slug': market.get('slug'),
                        'yes_token_id': token_ids[0],
                        'no_token_id': token_ids[1],
                        'volume': float(market.get('volume', 0)),
                        'active': market.get('active', True),
                    })
            
            return pm_children
            
        except Exception as e:
            print(f"  ✗ 获取 Polymarket 事件失败: {e}")
            return []
    
    def _match_child_markets(
        self, 
        op_children: List[Dict], 
        pm_children: List[Dict],
        parent_title: str
    ) -> Tuple[List[MarketMatch], List[Dict], List[Dict]]:
        """
        批量匹配子市场（使用子串包含而非相似度）
        
        Args:
            op_children: Opinion 子市场列表
            pm_children: Polymarket 子市场列表
            parent_title: 父市场标题（用于拼接完整问题）
            
        Returns:
            (匹配的市场对, 未匹配的 Opinion 子市场, 未匹配的 Polymarket 子市场)
        """
        matches = []
        unmatched_op = []
        unmatched_pm = list(pm_children)  # 复制一份，用于移除已匹配项
        
        for op_child in op_children:
            child_title = op_child['child_title']
            child_title_lower = child_title.lower().strip()
            
            # 在 Polymarket 子市场中查找包含此子标题的市场
            found_match = None
            
            for pm_child in unmatched_pm:
                pm_question = pm_child['question'].lower().strip()
                
                # 双向检查：子标题在问题中，或问题在子标题中
                if child_title_lower in pm_question or pm_question in child_title_lower:
                    found_match = pm_child
                    break
            
            if found_match:
                # 找到匹配
                combined_title = f"{parent_title} - {child_title}"
                match = MarketMatch(
                    question=combined_title,
                    opinion_market_id=op_child['market_id'],
                    opinion_yes_token=op_child['yes_token_id'] or "",
                    opinion_no_token=op_child['no_token_id'] or "",
                    polymarket_condition_id=found_match['condition_id'],
                    polymarket_yes_token=found_match['yes_token_id'],
                    polymarket_no_token=found_match['no_token_id'],
                    polymarket_slug=found_match['slug'],
                    similarity_score=1.0
                )
                matches.append(match)
                unmatched_pm.remove(found_match)
                print(f"    ✓ {child_title[:40]} → {found_match['question'][:40]} {op_child['status']}")
            else:
                # 未找到匹配
                unmatched_op.append(op_child)
                print(f"    ✗ {child_title[:40]} (未匹配)")
        
        return matches, unmatched_op, unmatched_pm
    
    def _save_unmatched_groups(self, unmatched_groups: List[Dict]):
        """保存未匹配的市场组到文件供手工筛选"""
        filename = "unmatched_markets.json"
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(unmatched_groups, f, indent=2, ensure_ascii=False)
        
        print(f"\n💾 未匹配市场组已保存到: {filename}")
        print(f"   包含 {len(unmatched_groups)} 个市场组，可手工筛选后添加到匹配列表")
    
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
        try:
            balances_resp = self.opinion_client.get_my_balances()
            if getattr(balances_resp, "errno", None) == 0:
                state["balances"] = getattr(balances_resp, "result", None)
            else:
                state["balance_error"] = getattr(balances_resp, "errmsg", "unknown error")
        except Exception as exc:
            state["balance_error"] = str(exc)
        orders: List[Dict[str, Any]] = []
        if hasattr(self.opinion_client, "get_my_orders"):
            try:
                orders_resp = self.opinion_client.get_my_orders()
                if getattr(orders_resp, "errno", None) == 0:
                    raw_orders = self._extract_iterable(getattr(orders_resp, "result", None))
                    for entry in raw_orders:
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
            "filled_amount",
            "filledAmount",
            "size_matched",
            "sizeMatched",
            "quantity_filled"
        ]))
        total = self._to_float(self._extract_from_entry(entry, [
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
            )
            if price is None or size is None:
                continue
            levels.append(OrderBookLevel(price=price, size=size))
        return levels

    def get_polymarket_orderbook(self, token_id: str, depth: int = 5) -> Optional[OrderBookSnapshot]:
        """获取 Polymarket 订单簿前 N 档含价格和数量"""
        try:
            book = self.polymarket_client.get_order_book(token_id)
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
            levels.append(OrderBookLevel(price=price, size=size))
        return levels
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

        monitors_active = self._account_monitors_started and (
            platform != "polymarket" or self.polymarket_trading_enabled
        )
        state_event = self._polymarket_state_updated if platform == "polymarket" else self._opinion_state_updated
        state_event.clear()
        start_time = time.time()
        monitor_wait_logged = False
        fallback_logged = False

        self._refresh_account_state_snapshot(platform, force_direct=not monitors_active)

        while True:
            elapsed = time.time() - start_time
            if elapsed >= max_wait_seconds:
                break

            cached_state = self._check_cached_order_state(platform, order_id)
            if cached_state:
                result = self._interpret_cached_order_state(platform, cached_state)
                if result is not None:
                    return result

            use_direct = (not monitors_active) or (
                self.order_status_fallback_after is not None and elapsed >= self.order_status_fallback_after
            )

            if use_direct:
                if monitors_active and self.order_status_fallback_after is not None and not fallback_logged:
                    print(
                        f"   超过 {self.order_status_fallback_after:.0f}s，启用直接查询接口以确认成交状态..."
                    )
                    fallback_logged = True
                self._refresh_account_state_snapshot(platform, force_direct=True)
                cached_state = self._check_cached_order_state(platform, order_id)
                if cached_state:
                    result = self._interpret_cached_order_state(platform, cached_state, source="直接查询")
                    if result is not None:
                        return result
            else:
                if monitors_active and not monitor_wait_logged:
                    print("   使用账户监控数据等待成交...")
                    monitor_wait_logged = True

            remaining = max_wait_seconds - (time.time() - start_time)
            if remaining <= 0:
                break
            wait_time = min(check_interval, remaining)

            if monitors_active:
                state_event.clear()
                self._refresh_account_state_snapshot(platform)
                state_event.wait(wait_time)
            else:
                time.sleep(wait_time)

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
            self._ensure_account_monitors()
            
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
                
                opinion_order_id = opinion_result.result.order_data.order_id 
                print(f"✅ Opinion 订单已提交")
                if opinion_order_id:
                    print(f"   订单 ID: {opinion_order_id}")
                    self._refresh_account_state_snapshot("opinion")
                
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
                    self._refresh_account_state_snapshot("polymarket")
                
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
                    self._refresh_account_state_snapshot("polymarket")
                
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

                opinion_order_id = opinion_result.result.order_data.order_id if hasattr(opinion_result.result, 'order_data') and hasattr(opinion_result.result.order_data, 'order_id') else None
                print(f"✅ Opinion 订单已提交")
                if opinion_order_id:
                    print(f"   订单 ID: {opinion_order_id}")
                    self._refresh_account_state_snapshot("opinion")
                
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
            opinion_yes_book = self.get_opinion_orderbook(match.opinion_yes_token)
            opinion_no_book = self.get_opinion_orderbook(match.opinion_no_token)
            
            # 获取 Polymarket 订单簿
            poly_yes_book = self.get_polymarket_orderbook(match.polymarket_yes_token)
            poly_no_book = self.get_polymarket_orderbook(match.polymarket_no_token)

            op_yes_bid = opinion_yes_book.best_bid().price if opinion_yes_book and opinion_yes_book.best_bid() else None
            op_yes_ask = opinion_yes_book.best_ask().price if opinion_yes_book and opinion_yes_book.best_ask() else None
            op_no_bid = opinion_no_book.best_bid().price if opinion_no_book and opinion_no_book.best_bid() else None
            op_no_ask = opinion_no_book.best_ask().price if opinion_no_book and opinion_no_book.best_ask() else None
            pm_yes_bid = poly_yes_book.best_bid().price if poly_yes_book and poly_yes_book.best_bid() else None
            pm_yes_ask = poly_yes_book.best_ask().price if poly_yes_book and poly_yes_book.best_ask() else None
            pm_no_bid = poly_no_book.best_bid().price if poly_no_book and poly_no_book.best_bid() else None
            pm_no_ask = poly_no_book.best_ask().price if poly_no_book and poly_no_book.best_ask() else None
            
            print(f"  Opinion YES: bid={op_yes_bid}, ask={op_yes_ask} volumes={self._format_levels(opinion_yes_book)}")
            print(f"  Opinion NO:  bid={op_no_bid}, ask={op_no_ask} volumes={self._format_levels(opinion_no_book)}")
            print(f"  Poly YES:    bid={pm_yes_bid}, ask={pm_yes_ask} volumes={self._format_levels(poly_yes_book)}")
            print(f"  Poly NO:     bid={pm_no_bid}, ask={pm_no_ask} volumes={self._format_levels(poly_no_book)}")
            
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
                        opinion_yes_book=opinion_yes_book,
                        poly_no_bid=pm_no_bid,
                        poly_no_ask=pm_no_ask,
                        poly_no_book=poly_no_book,
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
                        poly_yes_book=poly_yes_book,
                        poly_yes_bid=pm_yes_bid,
                        poly_yes_ask=pm_yes_ask,
                        opinion_no_bid=op_no_bid,
                        opinion_no_ask=op_no_ask,
                        opinion_no_book=opinion_no_book,
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
    
    # ==================== 7. 主流程 ====================
    
    def run_full_scan(
        self, 
        use_search: bool = True, 
        interactive: bool = True,
        use_cached_matches: bool = False,
        matches_file: str = "market_matches.json",
        topic_type: TopicType = TopicType.BINARY
    ):
        """
        运行完整的套利扫描流程
        
        Args:
            use_search: 是否使用搜索 API 匹配市场
            interactive: 是否进入交互式执行模式
            use_cached_matches: 是否使用缓存的市场匹配结果
            matches_file: 市场匹配结果文件路径
            topic_type: Opinion 市场类型 (TopicType.BINARY 或 TopicType.CATEGORICAL)
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
            self.fetch_opinion_markets(max_markets=300, topic_type=topic_type)
            
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
        print(opinion_result)
        print(opinion_result.result.order_data.order_id)
        result = self.opinion_client.get_my_orders(status="1")
        print(result)
        result = self.opinion_client.get_my_orders(status="2")
        print(result)
        result = self.opinion_client.get_my_orders(status="3")
        print(result)
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
        help='使用缓存的市场匹配结果 (默认: market_matches_multi.json)'
    )
    
    parser.add_argument(
        '--matches-file',
        type=str,
        default='market_matches_multi_1124.json',
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
    
    parser.add_argument(
        '--topic-type',
        type=str,
        choices=['BINARY', 'CATEGORICAL'],
        default='BINARY',
        help='Opinion 市场类型 (BINARY 或 CATEGORICAL, 默认: BINARY)'
    )
    
    args = parser.parse_args()
    
    try:
        scanner = CrossPlatformArbitrage()
        if args.test:
            scanner.test()
            return
        
        # 转换 topic_type 字符串为枚举
        topic_type = TopicType.CATEGORICAL if args.topic_type == 'CATEGORICAL' else TopicType.BINARY
        
        scanner.run_full_scan(
            use_search=not args.no_search,
            interactive=not args.no_interactive,
            use_cached_matches=args.use_cached,
            matches_file=args.matches_file,
            topic_type=topic_type
        )
        
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
