"""
跨平台市场匹配工具 - Opinion vs Polymarket
该模块仅保留市场匹配与结果保存逻辑。
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
# Opinion SDK
from opinion_clob_sdk import Client as OpinionClient
from opinion_clob_sdk.model import TopicStatusFilter, TopicType
# Polymarket SDK
from py_clob_client.client import ClobClient
import requests
from py_clob_client.clob_types import ApiCreds

import requests
import os
from dotenv import load_dotenv
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

    # 截止时间（秒级时间戳）
    cutoff_at: Optional[int] = None

    op_rules: Optional[str] = None  # Opinion 市场规则
    poly_rules: Optional[str] = None  # Polymarket 市场规则

    polymarket_neg_risk: Optional[bool] = False  # Polymarket neg_risk 标志


class CrossPlatformArbitrage:
    """负责市场匹配和结果保存的精简类"""

    def __init__(
        self,
        gamma_api: str = "https://gamma-api.polymarket.com",
        unmatched_output_file: str = "unmatched_markets.json",
    ):
        self.gamma_api = gamma_api
        self.unmatched_output_file = unmatched_output_file
        self.opinion_markets: List[Dict] = []
        self.market_matches: List[MarketMatch] = []
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

            # 打印第一个市场的数据结构以供调试
            if page == 1 and len(markets) > 0:
                print("\n=== Opinion Market 数据结构示例 ===")
                first_market = markets[0]
                print(f"Market ID: {first_market.market_id}")
                print(f"Title: {first_market.market_title}")
                print(f"Available attributes: {dir(first_market)}")
                # 尝试获取 rules 字段
                rules = getattr(first_market, 'rules', None)
                print(f"Rules field: {rules}")
                print("=" * 50 + "\n")

            # 转换为字典格式
            for market in markets:
                # 检查是否为 CATEGORICAL 类型且有子市场
                child_markets = getattr(market, 'child_markets', None)
                cutoff_raw = getattr(market, "cutoff_at", None)
                cutoff_ts = int(cutoff_raw) if cutoff_raw is not None else None

                if child_markets and len(child_markets) > 0:
                    # CATEGORICAL 类型: 展平子市场
                    parent_title = market.market_title
                    parent_rules = getattr(market, 'rules', None) or ""
                    for child in child_markets:
                        if child.status_enum != 'Activated':
                            continue
                        # 拼接标题: "父标题 - 子标题"
                        combined_title = f"{parent_title} - {child.market_title}"
                        # 子市场可能有自己的 rules，如果没有则使用父市场的
                        child_rules = getattr(child, 'rules', None) or parent_rules

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
                            'cutoff_at': cutoff_ts,
                            'rules': child_rules
                        })
                else:
                    # BINARY 类型或无子市场: 直接添加
                    market_rules = getattr(market, 'rules', None) or ""
                    all_markets.append({
                        'market_id': market.market_id,
                        'title': market.market_title,
                        'yes_token_id': getattr(market, 'yes_token_id', None),
                        'no_token_id': getattr(market, 'no_token_id', None),
                        'volume': float(getattr(market, 'volume', 0)),
                        'status': market.status,
                        'cutoff_at': cutoff_ts,
                        'rules': market_rules
                    })

            page += 1
        
        self.opinion_markets = all_markets
        print(f"✅ 获取到 {len(all_markets)} 个 Opinion 市场\n")
        return all_markets

    def __fetch_opinion_markets(self, max_markets: int = 100, topic_type: TopicType = TopicType.BINARY) -> List[Dict]:
        """获取 Opinion 的所有活跃市场"""
        print("📊 获取 Opinion 市场...")

        all_markets: List[Dict] = []
        page = 1
        limit = 20  # Opinion API 限制每页最多 20 条

        while len(all_markets) < max_markets:
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
            print(f"  获取到 {len(markets)} 个市场 (第 {page} 页)")
            print(f"  累计市场数: {len(all_markets) + len(markets)}")

            for market in markets:
                cutoff_raw = getattr(market, "cutoff_at", None)
                cutoff_ts = int(cutoff_raw) if cutoff_raw is not None else None

                child_markets = getattr(market, "child_markets", None)
                if child_markets:
                    parent_title = market.market_title
                    for child in child_markets:
                        # 继承父市场的截止时间
                        entry = {
                            "market_id": child.market_id,
                            "title": f"{parent_title} - {child.market_title}",
                            "yes_token_id": getattr(child, "yes_token_id", None),
                            "no_token_id": getattr(child, "no_token_id", None),
                            "volume": getattr(child, "volume", 0),
                            "status": child.status,
                            "parent_market_id": market.market_id,
                            "parent_title": parent_title,
                            "child_title": child.market_title,
                            "cutoff_at": cutoff_ts,
                        }
                        all_markets.append(entry)
                        if len(all_markets) >= max_markets:
                            break
                else:
                    all_markets.append({
                        "market_id": market.market_id,
                        "title": market.market_title,
                        "yes_token_id": getattr(market, "yes_token_id", None),
                        "no_token_id": getattr(market, "no_token_id", None),
                        "volume": getattr(market, "volume", 0),
                        "status": market.status,
                        "cutoff_at": cutoff_ts,
                    })

            if len(all_markets) >= max_markets:
                break

            page += 1

        self.opinion_markets = all_markets
        print(f"✅ 获取到 {len(all_markets)} 个 Opinion 市场\n")
        return all_markets

    # ==================== 搜索辅助 ====================

    def search_polymarket_market(self, query: str, debug: bool = False) -> Optional[Dict]:
        """根据问题标题在 Polymarket 搜索匹配市场"""
        try:
            response = requests.get(
                f"{self.gamma_api}/public-search",
                params={"q": query},
                timeout=10,
            )
            response.raise_for_status()
            results = response.json()

            events = results.get("events", [])
            if not events:
                return None

            # 获取第一个事件的描述信息
            first_event = events[0]
            event_description = first_event.get("description", "")

            # 调试: 打印事件数据结构
            if debug:
                print("\n=== Polymarket Event 数据结构示例 ===")
                print(f"Event ID: {first_event.get('id')}")
                print(f"Event Title: {first_event.get('title')}")
                print(f"Description: {event_description[:200] if event_description else 'None'}...")
                print(f"Available fields: {list(first_event.keys())}")
                print("=" * 50 + "\n")

            markets = first_event.get("markets", [])
            if not markets:
                return None

            if len(markets) == 1:
                return self._extract_market_entry(markets[0], event_description)

            best_match = None
            best_similarity = 0.0
            query_lower = query.lower().strip()

            for market in markets:
                market_question = market.get("question", "").lower().strip()
                similarity = self._calculate_similarity(query_lower, market_question)

                if similarity >= 0.95 or query_lower == market_question:
                    return self._extract_market_entry(market, event_description)

                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = market

            if best_match and best_similarity >= 0.6:
                return self._extract_market_entry(best_match, event_description)

            return None
        except Exception as exc:  # pragma: no cover - 仅记录日志
            print(f"  搜索失败: {exc}")
            return None

    def _extract_market_entry(self, market: Dict, description: str = "") -> Optional[Dict]:
        token_ids_raw = market.get("clobTokenIds", "[]")
        token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
        if len(token_ids) < 2:
            return None
        return {
            "condition_id": market.get("conditionId"),
            "question": market.get("question"),
            "slug": market.get("slug"),
            "yes_token_id": token_ids[0],
            "no_token_id": token_ids[1],
            "volume": float(market.get("volume", 0)),
            "active": market.get("active", True),
            "description": description,  # 添加事件描述
            "neg_risk": market.get("negRisk", False),  # 添加 neg_risk 标志
        }

    # ==================== 市场匹配 ====================

    def match_markets_by_search(self, topic_types: Optional[List[str]] = None) -> List[MarketMatch]:
        """使用搜索 API 匹配两个平台的市场"""
        normalized_topics = {
            (topic or "BINARY").upper()
            for topic in (topic_types if topic_types else ["BINARY"])
        }
        invalid = normalized_topics.difference({"BINARY", "CATEGORICAL"})
        if invalid:
            raise ValueError(
                f"topic_type 仅支持 BINARY/CATEGORICAL，收到: {', '.join(sorted(invalid))}"
            )

        print(f"🔍 使用搜索 API 匹配市场 (类型: {', '.join(sorted(normalized_topics))})...")

        matches: List[MarketMatch] = []
        unmatched_groups: List[Dict] = []

        binary_markets: List[Dict] = []
        categorical_groups: Dict[int, Dict] = {}

        for op_market in self.opinion_markets:
            if "parent_market_id" in op_market and "parent_title" in op_market:
                parent_id = op_market["parent_market_id"]
                if parent_id not in categorical_groups:
                    categorical_groups[parent_id] = {
                        "parent_title": op_market["parent_title"],
                        "children": [],
                    }
                categorical_groups[parent_id]["children"].append(op_market)
            else:
                binary_markets.append(op_market)

        process_binary = "BINARY" in normalized_topics
        process_categorical = "CATEGORICAL" in normalized_topics

        if process_binary:
            print(f"\n📊 处理 {len(binary_markets)} 个 BINARY 市场...")
            for i, op_market in enumerate(binary_markets, 1):
                op_title = op_market["title"]
                if "?" in op_title and not op_title.endswith("?"):
                    op_title = op_title.split("?", 1)[0] + "?"
                print(f"[{i}/{len(binary_markets)}] 搜索: {op_title[:60]}...")

                # 只在第一次调用时启用调试
                pm_market = self.search_polymarket_market(query=op_title, debug=(i == 1))

                if pm_market:
                    matches.append(
                        MarketMatch(
                            question=op_title,
                            opinion_market_id=op_market["market_id"],
                            opinion_yes_token=op_market.get("yes_token_id", "") or "",
                            opinion_no_token=op_market.get("no_token_id", "") or "",
                            cutoff_at=op_market.get("cutoff_at"),
                            polymarket_condition_id=pm_market["condition_id"],
                            polymarket_yes_token=pm_market["yes_token_id"],
                            polymarket_no_token=pm_market["no_token_id"],
                            polymarket_slug=pm_market["slug"],
                            similarity_score=1.0,
                            op_rules=op_market.get("rules", ""),
                            poly_rules=pm_market.get("description", ""),
                            polymarket_neg_risk=pm_market.get("neg_risk", False),  # 添加 neg_risk
                        )
                    )
                    print("  ✓ 匹配成功")
                else:
                    print("  ✗ 未找到匹配")

                time.sleep(1)

        if process_categorical:
            print(f"\n📊 处理 {len(categorical_groups)} 个 CATEGORICAL 市场组...")
            for group_idx, (parent_id, group_info) in enumerate(categorical_groups.items(), 1):
                parent_title = group_info["parent_title"]
                op_children = group_info["children"]
                if "?" in parent_title and not parent_title.endswith("?"):
                    parent_title = parent_title.split("?", 1)[0] + "?"

                print(f"\n[{group_idx}/{len(categorical_groups)}] 父市场: {parent_title}")
                print(f"  Opinion 子市场数: {len(op_children)}")

                pm_children = self._fetch_polymarket_event_markets(parent_title)

                if not pm_children:
                    print("  ✗ 未找到 Polymarket 对应事件")
                    unmatched_groups.append(
                        {
                            "parent_title": parent_title,
                            "opinion_children": [
                                {
                                    "market_id": child["market_id"],
                                    "child_title": child["child_title"],
                                    "yes_token_id": child.get("yes_token_id"),
                                    "no_token_id": child.get("no_token_id"),
                                }
                                for child in op_children
                            ],
                            "polymarket_children": [],
                        }
                    )
                    continue

                print(f"  Polymarket 子市场数: {len(pm_children)}")
                matched, unmatched_op, unmatched_pm = self._match_child_markets(op_children, pm_children, parent_title)
                matches.extend(matched)

                if unmatched_op or unmatched_pm:
                    unmatched_groups.append(
                        {
                            "parent_title": parent_title,
                            "opinion_children": [
                                {
                                    "market_id": child["market_id"],
                                    "child_title": child["child_title"],
                                    "yes_token_id": child.get("yes_token_id"),
                                    "no_token_id": child.get("no_token_id"),
                                }
                                for child in unmatched_op
                            ],
                            "polymarket_children": [
                                {
                                    "condition_id": child["condition_id"],
                                    "question": child["question"],
                                    "slug": child["slug"],
                                    "yes_token_id": child["yes_token_id"],
                                    "no_token_id": child["no_token_id"],
                                }
                                for child in unmatched_pm
                            ],
                        }
                    )

                time.sleep(1)

        if unmatched_groups:
            self._save_unmatched_groups(unmatched_groups)

        self.market_matches = self.market_matches+matches
        print(f"\n✅ 共匹配到 {len(matches)} 个市场对")
        if unmatched_groups:
            print("⚠️  部分市场组存在未匹配项，已保存到 unmatched_markets.json")
        print()
        return matches

    def _fetch_polymarket_event_markets(self, parent_title: str) -> List[Dict]:
        """获取指定事件下的 Polymarket 子市场"""
        try:
            response = requests.get(
                f"{self.gamma_api}/public-search",
                params={"q": parent_title},
                timeout=10,
            )
            response.raise_for_status()
            results = response.json()

            events = results.get("events", [])
            if not events:
                return []

            first_event = events[0]
            event_description = first_event.get("description", "")
            markets = first_event.get("markets", [])
            if not markets:
                return []

            pm_children = []
            for market in markets:
                entry = self._extract_market_entry(market, event_description)
                if entry:
                    pm_children.append(entry)

            return pm_children
        except Exception as exc:  # pragma: no cover - 仅记录日志
            print(f"  ✗ 获取 Polymarket 事件失败: {exc}")
            return []

    def _match_child_markets(
        self,
        op_children: List[Dict],
        pm_children: List[Dict],
        parent_title: str,
    ) -> Tuple[List[MarketMatch], List[Dict], List[Dict]]:
        """根据子市场标题做包含匹配"""
        matches: List[MarketMatch] = []
        unmatched_op: List[Dict] = []
        unmatched_pm: List[Dict] = list(pm_children)

        for op_child in op_children:
            child_title = op_child["child_title"]
            child_title_lower = child_title.lower().strip()

            found_match = None
            for pm_child in unmatched_pm:
                pm_question = pm_child["question"].lower().strip()
                if child_title_lower in pm_question or pm_question in child_title_lower:
                    found_match = pm_child
                    break

            if found_match:
                combined_title = f"{parent_title} - {child_title}"
                matches.append(
                    MarketMatch(
                        question=combined_title,
                        opinion_market_id=op_child["market_id"],
                        opinion_yes_token=op_child.get("yes_token_id", "") or "",
                        opinion_no_token=op_child.get("no_token_id", "") or "",
                        cutoff_at=op_child.get("cutoff_at"),
                        polymarket_condition_id=found_match["condition_id"],
                        polymarket_yes_token=found_match["yes_token_id"],
                        polymarket_no_token=found_match["no_token_id"],
                        polymarket_slug=found_match["slug"],
                        similarity_score=1.0,
                        op_rules=op_child.get("rules", ""),
                        poly_rules=found_match.get("description", ""),
                        polymarket_neg_risk=found_match.get("neg_risk", False),  # 添加 neg_risk
                    )
                )
                unmatched_pm.remove(found_match)
                print(f"    ✓ {child_title[:40]} → {found_match['question'][:40]}")
            else:
                unmatched_op.append(op_child)
                print(f"    ✗ {child_title[:40]} (未匹配)")

        return matches, unmatched_op, unmatched_pm

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """简单的 Jaccard 相似度"""
        words1 = set(text1.split())
        words2 = set(text2.split())

        if not words1 and not words2:
            return 0.0

        intersection = words1.intersection(words2)
        union = words1.union(words2)

        if not union:
            return 0.0

        return len(intersection) / len(union)

    # ==================== 保存结果 ====================

    def _save_unmatched_groups(self, unmatched_groups: List[Dict]):
        """保存未匹配市场组到 unmatched_markets.json"""
        filename = self.unmatched_output_file
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(unmatched_groups, file, indent=2, ensure_ascii=False)
        print(f"\n💾 未匹配市场组已保存到: {filename}")

    def save_market_matches(self, filename: str = "market_matches.json"):
        """保存匹配结果"""
        data = [asdict(match) for match in self.market_matches]
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
        print(f"✅ 市场匹配结果已保存到: {filename}")


def load_markets_from_file(path: str) -> List[Dict]:
    """从 JSON 文件加载市场列表"""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"找不到文件: {file_path}")

    with file_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError(f"文件 {file_path} 的内容不是列表")

    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="跨平台市场匹配工具")
    parser.add_argument(
        "--topic-type",
        default="BINARY,CATEGORICAL",
        help="指定要处理的 Opinion 市场类型，可用逗号分隔 (例如: BINARY,CATEGORICAL)",
    )
    parser.add_argument(
        "--output",
        default="market_matches_1205.json",
        help="保存匹配结果的文件路径",
    )
    parser.add_argument(
        "--unmatched-file",
        default="unmatched_markets.json",
        help="保存未匹配市场组的文件路径",
    )
    parser.add_argument(
        "--gamma-api",
        default="https://gamma-api.polymarket.com",
        help="自定义 Gamma API 根路径",
    )
    args = parser.parse_args()

    scanner = CrossPlatformArbitrage(
        gamma_api=args.gamma_api,
        unmatched_output_file=args.unmatched_file,
    )

    topic_values = [value.strip() for value in args.topic_type.split(",") if value.strip()]

    for topic_value in topic_values:
        if topic_value.upper() not in {"BINARY", "CATEGORICAL"}:
            print(f"❌ 无效的 topic_type: {topic_value}。仅支持 BINARY/CATEGORICAL。")
            sys.exit(1)
        try:
            opinion_markets = scanner.fetch_opinion_markets(600, topic_type=TopicType[topic_value.upper()])
        except Exception as exc:
            print(f"❌ 无法加载 Opinion 市场文件: {exc}")
            sys.exit(1)

        scanner.opinion_markets = opinion_markets

        scanner.match_markets_by_search(topic_types=topic_values)

    scanner.save_market_matches(args.output)


if __name__ == "__main__":
    main()
