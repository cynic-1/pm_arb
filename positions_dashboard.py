"""Simple dashboard server for Polymarket & Opinion positions."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
try:
    from opinion_clob_sdk import Client as OpinionClient
    OPINION_SDK_AVAILABLE = True
except ImportError:
    OpinionClient = None
    OPINION_SDK_AVAILABLE = False
    print("[warn] opinion_clob_sdk not available, Opinion balance via SDK will be disabled")
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import TradeParams, BalanceAllowanceParams, AssetType
from decimal import Decimal

load_dotenv()

POLYMARKET_ADDRESS = os.getenv(
    "PM_FUNDER",
    "0xbd6c2a16c00ab38338b241783d454981a750a568",
)
POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
POLYMARKET_POSITIONS_URL = "https://data-api.polymarket.com/positions"
POLYMARKET_CLOSED_POSITIONS_URL = "https://data-api.polymarket.com/closed-positions"
POLYMARKET_TRADES_URL = "https://data-api.polymarket.com/trades"
POLYMARKET_VALUE_URL = "https://data-api.polymarket.com/value"
OPINION_OPENAPI_URL = "https://openapi.opinion.trade/openapi"
OPINION_WALLET_ADDRESS = os.getenv("OP_WALLET_ADDRESS", "") or os.getenv("PM_FUNDER", "")
VALUE_THRESHOLD = float(os.getenv("POSITION_VALUE_THRESHOLD", "1"))
CACHE_SECONDS = int(os.getenv("DASHBOARD_CACHE_SECONDS", "600"))
DEFAULT_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
INVESTOR_HTML_BASENAME = "investor_dashboard.html"
MARKET_MATCHES_FILE = Path(__file__).parent / "market_matches.json"


@dataclass
class FetchResult:
    """Normalized payload returned to the frontend."""

    polymarket: Dict[str, Any]
    opinion: Dict[str, Any]
    matched_pairs: Dict[str, Any]
    last_updated: str


@dataclass
class TradesResult:
    """Trades data for both platforms."""

    polymarket_trades: List[Dict[str, Any]]
    opinion_trades: List[Dict[str, Any]]
    last_updated: str


@dataclass
class ClosedPositionsResult:
    """Closed positions data for realized PnL tracking."""

    polymarket: List[Dict[str, Any]]
    opinion: List[Dict[str, Any]]
    last_updated: str


class PortfolioFetcher:
    """Fetches and caches portfolio info for Polymarket & Opinion."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._cache: Optional[FetchResult] = None
        self._cache_ts = 0.0
        self._opinion_client = self._init_opinion_client()
        self._polymarket_client = self._init_polymarket_client()
        self._market_matches = self._load_market_matches()
        self._stop_event = threading.Event()
        self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        try:
            self.refresh_now()
        except Exception as exc:  # pragma: no cover - defensive guard
            print(f"[warn] Initial refresh failed: {exc}")
        self._refresh_thread.start()

    def _init_opinion_client(self):
        """Initialize Opinion client if credentials are present."""
        if not OPINION_SDK_AVAILABLE:
            print("[info] Opinion SDK not available, skipping client init")
            return None

        try:
            return OpinionClient(
                host=os.getenv("OP_HOST", "https://proxy.opinion.trade:8443"),
                apikey=os.getenv("OP_API_KEY"),
                chain_id=int(os.getenv("OP_CHAIN_ID", "56")),
                rpc_url=os.getenv("OP_RPC_URL"),
                private_key=os.getenv("OP_PRIVATE_KEY"),
                multi_sig_addr=os.getenv("OP_MULTI_SIG_ADDRESS"),
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            print(f"[warn] Failed to init Opinion client: {exc}")
            return None

    def _init_polymarket_client(self) -> Optional[ClobClient]:
        """Initialize Polymarket CLOB client if credentials are present."""
        try:
            private_key = os.getenv("PM_KEY")
            if not private_key:
                print("[warn] PM_KEY not set, Polymarket trades will not be available")
                return None
            chain_id = int(os.getenv("CHAIN_ID", "137"))
            signature_type = int(os.getenv("SIGNATURE_TYPE", "2"))  # 2=Safe/Proxy by default
            funder = POLYMARKET_ADDRESS
            print(f"[debug] Polymarket init: chain_id={chain_id}, signature_type={signature_type}, funder={funder}")
            client = ClobClient(
                POLYMARKET_HOST,
                key=private_key,
                chain_id=chain_id,
                signature_type=signature_type,
                funder=funder,
            )
            api_creds = client.create_or_derive_api_creds()
            print(f"[debug] Polymarket API creds derived successfully")
            client.set_api_creds(api_creds)
            return client
        except Exception as exc:
            print(f"[warn] Failed to init Polymarket CLOB client: {exc}")
            import traceback
            traceback.print_exc()
            return None

    def _load_market_matches(self) -> List[Dict[str, Any]]:
        """Load market matches from JSON file."""
        try:
            if MARKET_MATCHES_FILE.exists():
                with open(MARKET_MATCHES_FILE, 'r') as f:
                    return json.load(f)
            print(f"[warn] Market matches file not found: {MARKET_MATCHES_FILE}")
            return []
        except Exception as exc:
            print(f"[warn] Failed to load market matches: {exc}")
            return []

    def refresh_now(self) -> None:
        """Force an immediate refresh from upstream APIs."""

        poly = self._fetch_polymarket()
        op = self._fetch_opinion()
        matched = self._calculate_matched_pairs(poly, op)
        snapshot = FetchResult(
            polymarket=poly,
            opinion=op,
            matched_pairs=matched,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._cache = snapshot
            self._cache_ts = time.time()

    def get_snapshot(self) -> FetchResult:
        """Return latest cached snapshot (no upstream calls)."""

        with self._lock:
            if self._cache:
                return self._cache

        return self._empty_snapshot()

    def get_market_matches(self) -> List[Dict[str, Any]]:
        """Return loaded market matches."""
        return self._market_matches

    def get_trades(self, limit: int = 10) -> TradesResult:
        """Fetch recent trades from both platforms."""
        poly_trades = self._fetch_polymarket_trades(limit)
        opinion_trades = self._fetch_opinion_trades(limit)
        return TradesResult(
            polymarket_trades=poly_trades,
            opinion_trades=opinion_trades,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )

    def get_balances(self) -> Dict[str, Any]:
        """Fetch cash balances from both platforms."""
        poly_balance = self._fetch_polymarket_balance()
        opinion_balance = self._fetch_opinion_balance()
        return {
            "polymarket": poly_balance,
            "opinion": opinion_balance,
            "total": poly_balance + opinion_balance,
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
        }

    def _fetch_polymarket_balance(self) -> float:
        """Fetch USDC balance from Polymarket."""
        if not self._polymarket_client:
            print("[debug] Polymarket client not initialized")
            return 0.0
        try:
            resp = self._polymarket_client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            print(f"[debug] Polymarket balance response: {resp}")
            # Balance is in 1e6 (USDC decimals)
            balance = Decimal(resp.get("balance", "0")) / Decimal(10**6)
            print(f"[debug] Polymarket balance: {balance}")
            return float(balance)
        except Exception as exc:
            print(f"[warn] Failed to fetch Polymarket balance: {exc}")
            return 0.0

    def _fetch_opinion_balance(self) -> float:
        """Fetch USDT balance from Opinion via SDK."""
        if not self._opinion_client:
            print("[debug] Opinion client not initialized")
            return 0.0
        try:
            resp = self._opinion_client.get_my_balances()
            print(f"[debug] Opinion balance response: {resp}")

            # Parse balance items - the SDK returns pydantic models
            # resp.result is OpenapiBalanceRespOpenAPI with .balances list
            items = []
            if hasattr(resp, "result"):
                result = resp.result
                if hasattr(result, "balances"):
                    items = result.balances
                elif hasattr(result, "list"):
                    items = result.list
                elif hasattr(result, "data"):
                    items = result.data

            total = Decimal(0)
            for b in items:
                print(f"[debug] Opinion balance item: {b}")
                # OpenapiQuoteTokenBalance has: available_balance, total_balance, token_decimals, quote_token
                amount_str = "0"
                decimals = 18
                if hasattr(b, "available_balance"):
                    amount_str = str(b.available_balance or "0")
                if hasattr(b, "token_decimals"):
                    decimals = int(b.token_decimals or 18)
                # Note: available_balance is already in human-readable format (not wei)
                # Check if it's a large number (wei) or small number (already converted)
                amount = Decimal(amount_str)
                if amount > 1e10:  # Likely in wei
                    amount = amount / (Decimal(10) ** decimals)
                total += amount
                print(f"[debug] Amount: {amount_str}, Decimals: {decimals}, Parsed: {amount}")

            print(f"[debug] Opinion total balance: {total}")
            return float(total)
        except Exception as exc:
            print(f"[warn] Failed to fetch Opinion balance: {exc}")
            import traceback
            traceback.print_exc()
            return 0.0

    def get_closed_positions(self, limit: int = 50) -> ClosedPositionsResult:
        """Fetch closed positions for realized PnL tracking.

        Only includes:
        - Merged positions (YES+NO combined to $1)
        - Resolved/settled positions (market ended, claimed)

        Does NOT include regular buy/sell trades.
        """
        poly_closed = self._fetch_polymarket_closed_positions(limit)
        # Only fetch claimed/resolved positions, not regular trades
        opinion_closed = self._fetch_opinion_claimed_positions()
        return ClosedPositionsResult(
            polymarket=poly_closed,
            opinion=opinion_closed,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )

    def _fetch_polymarket_closed_positions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch closed positions from Polymarket API."""
        try:
            resp = self._session.get(
                POLYMARKET_CLOSED_POSITIONS_URL,
                params={
                    "user": POLYMARKET_ADDRESS,
                    "limit": min(limit, 50),
                    "sortBy": "REALIZEDPNL",
                    "sortDirection": "DESC",
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            positions = data if isinstance(data, list) else data.get("data", [])
            result = []
            for pos in positions:
                result.append({
                    "title": pos.get("title"),
                    "outcome": pos.get("outcome"),
                    "avgPrice": float(pos.get("avgPrice", 0) or 0),
                    "totalBought": float(pos.get("totalBought", 0) or 0),
                    "realizedPnl": float(pos.get("realizedPnl", 0) or 0),
                    "curPrice": float(pos.get("curPrice", 0) or 0),
                    "timestamp": pos.get("timestamp"),
                    "slug": pos.get("slug"),
                    "conditionId": pos.get("conditionId"),
                })
            return result
        except Exception as exc:
            print(f"[warn] Failed to fetch Polymarket closed positions: {exc}")
            return []

    def _fetch_opinion_trades_with_profit(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch Opinion SELL trades for realized PnL calculation."""
        api_key = os.getenv("OP_API_KEY")
        wallet_address = OPINION_WALLET_ADDRESS or os.getenv("OP_WALLET_ADDRESS", "")
        if not api_key or not wallet_address:
            return []
        try:
            # Fetch multiple pages to get more complete trade history
            all_trades = []
            pages_to_fetch = (limit + 19) // 20  # ceil division
            for page in range(1, min(pages_to_fetch + 1, 6)):  # max 5 pages
                url = f"{OPINION_OPENAPI_URL}/trade/user/{wallet_address}"
                resp = self._session.get(
                    url,
                    params={"page": page, "limit": 20},
                    headers={"apikey": api_key},
                    timeout=20,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != 0 and data.get("errno") != 0:
                    print(f"[warn] Opinion trades API error: {data.get('msg')}")
                    break
                batch = data.get("result", {}).get("list", [])
                if not batch:
                    break
                all_trades.extend(batch)
                if len(batch) < 20:
                    break

            trades = []
            for trade in all_trades:
                # Include SELL trades (realized gains/losses from selling)
                # profit field can be positive (gain) or negative (loss)
                profit = trade.get("profit")
                side = trade.get("side", "").upper()
                if side == "SELL" or profit is not None:
                    # For SELL trades, calculate profit if not provided
                    if profit is None and side == "SELL":
                        # Approximate: we don't have avg entry price here
                        # but the trade amount represents the sale proceeds
                        profit = 0  # Will be calculated from positions
                    trades.append({
                        "marketTitle": trade.get("marketTitle") or trade.get("rootMarketTitle"),
                        "outcomeSideEnum": trade.get("outcomeSideEnum"),
                        "side": side,
                        "price": float(trade.get("price", 0) or 0),
                        "shares": float(trade.get("shares", 0) or 0),
                        "profit": float(profit) if profit else 0,
                        "createdAt": trade.get("createdAt"),
                    })
            return trades[:limit]
        except Exception as exc:
            print(f"[warn] Failed to fetch Opinion trades with profit: {exc}")
            return []

    def _fetch_opinion_claimed_positions(self) -> List[Dict[str, Any]]:
        """Fetch Opinion positions that have been claimed/resolved via OpenAPI.

        Only includes positions from resolved markets (merge or final settlement).
        """
        api_key = os.getenv("OP_API_KEY")
        wallet_address = OPINION_WALLET_ADDRESS
        if not api_key or not wallet_address:
            return []
        try:
            all_positions: List[Dict[str, Any]] = []
            page = 1
            while page <= 10:  # Safety limit
                url = f"{OPINION_OPENAPI_URL}/positions/user/{wallet_address}"
                resp = self._session.get(
                    url,
                    params={"page": page, "limit": 20},
                    headers={"apikey": api_key},
                    timeout=20,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("code", -1) != 0:
                    print(f"[warn] Opinion positions API error: {data.get('msg')}")
                    break
                batch = data.get("result", {}).get("list", [])
                if not batch:
                    break
                all_positions.extend(batch)
                if len(batch) < 20:
                    break
                page += 1

            claimed = []
            for pos in all_positions:
                claim_status = pos.get("claimStatusEnum", "")
                market_status = pos.get("marketStatusEnum", "")
                # Only include claimed positions and resolved markets
                # This covers: merge (redeemed) and final settlement (resolved + claimed)
                if claim_status == "Claimed" or market_status == "Resolved":
                    shares = float(pos.get("sharesOwned", 0) or 0)
                    avg_price = float(pos.get("avgEntryPrice", 0) or 0)
                    # unrealizedPnl at resolution = realized PnL
                    # Positive = won (resolved in your favor)
                    # Negative = lost (resolved against you, shares worth $0)
                    unrealized_pnl = float(pos.get("unrealizedPnl", 0) or 0)

                    claimed.append({
                        "marketTitle": pos.get("marketTitle") or pos.get("rootMarketTitle"),
                        "outcomeSideEnum": pos.get("outcomeSideEnum"),
                        "shares": shares,
                        "avgPrice": avg_price,
                        "profit": unrealized_pnl,
                        "claimStatus": claim_status,
                        "marketStatus": market_status,
                    })
            return claimed
        except Exception as exc:
            print(f"[warn] Failed to fetch Opinion claimed positions: {exc}")
            return []

    def _fetch_polymarket_trades(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch recent trades from Polymarket data API."""
        try:
            resp = self._session.get(
                POLYMARKET_TRADES_URL,
                params={
                    "user": POLYMARKET_ADDRESS,
                    "limit": min(limit, 100),
                },
                timeout=20,
            )
            resp.raise_for_status()
            raw_trades = resp.json()
            if isinstance(raw_trades, dict):
                raw_trades = raw_trades.get("data", [])
            trades = []
            for trade in raw_trades[:limit]:
                trades.append({
                    "id": trade.get("id"),
                    "market": trade.get("conditionId"),
                    "title": trade.get("title"),
                    "asset_id": trade.get("asset"),
                    "side": trade.get("side"),
                    "size": float(trade.get("size", 0) or 0),
                    "price": float(trade.get("price", 0) or 0),
                    "outcome": trade.get("outcome"),
                    "status": trade.get("status"),
                    "match_time": trade.get("timestamp"),
                    "type": trade.get("type"),
                    "fee_rate_bps": trade.get("fee_rate_bps"),
                    "transaction_hash": trade.get("transactionHash"),
                })
            return trades
        except Exception as exc:
            print(f"[warn] Failed to fetch Polymarket trades: {exc}")
            return []

    def _fetch_opinion_trades(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch recent trades from Opinion via OpenAPI."""
        api_key = os.getenv("OP_API_KEY")
        wallet_address = OPINION_WALLET_ADDRESS or os.getenv("OP_WALLET_ADDRESS", "")
        if not api_key or not wallet_address:
            return []
        try:
            url = f"{OPINION_OPENAPI_URL}/trade/user/{wallet_address}"
            resp = self._session.get(
                url,
                params={"page": 1, "limit": limit},
                headers={"apikey": api_key},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errno") != 0:
                print(f"[warn] Opinion trades API error: {data.get('msg')}")
                return []
            trades = []
            for trade in data.get("result", {}).get("list", []):
                trades.append({
                    "txHash": trade.get("txHash"),
                    "marketId": trade.get("marketId"),
                    "marketTitle": trade.get("marketTitle") or trade.get("rootMarketTitle"),
                    "side": trade.get("side"),
                    "outcome": trade.get("outcome"),
                    "outcomeSide": trade.get("outcomeSideEnum"),
                    "price": trade.get("price"),
                    "shares": trade.get("shares"),
                    "amount": trade.get("amount"),
                    "usdAmount": trade.get("usdAmount"),
                    "fee": trade.get("fee"),
                    "status": trade.get("statusEnum"),
                    "chainId": trade.get("chainId"),
                    "createdAt": trade.get("createdAt"),
                })
            return trades
        except Exception as exc:
            print(f"[warn] Failed to fetch Opinion trades: {exc}")
            return []

    def _refresh_loop(self) -> None:
        """Background thread keeping the cache warm."""

        while not self._stop_event.wait(CACHE_SECONDS):
            try:
                self.refresh_now()
            except Exception as exc:  # pragma: no cover - defensive logging
                print(f"[warn] Background refresh failed: {exc}")

    def _empty_snapshot(self) -> FetchResult:
        """Return placeholder snapshot when data is unavailable."""

        placeholder = {
            "totalValue": 0.0,
            "positions": [],
            "error": "Snapshot not ready. Please retry shortly.",
        }
        return FetchResult(
            polymarket={"address": POLYMARKET_ADDRESS, **placeholder},
            opinion=dict(placeholder),
            matched_pairs={"pairs": [], "totalMatchedValue": 0.0, "error": None},
            last_updated="pending",
        )

    def _fetch_polymarket(self) -> Dict[str, Any]:
        """Fetch Polymarket positions and total value."""

        payload: Dict[str, Any] = {
            "address": POLYMARKET_ADDRESS,
            "totalValue": 0.0,
            "positions": [],
            "error": None,
        }

        try:
            positions_resp = self._session.get(
                POLYMARKET_POSITIONS_URL,
                params={
                    "user": POLYMARKET_ADDRESS,
                    "sizeThreshold": 0,
                    "limit": 500,
                    "sortBy": "CURRENT",
                    "sortDirection": "DESC",
                },
                timeout=20,
            )
            positions_resp.raise_for_status()
            raw_positions = positions_resp.json()
            if isinstance(raw_positions, dict):
                positions_data = raw_positions.get("positions") or raw_positions.get("data", [])
            else:
                positions_data = raw_positions

            filtered_positions: List[Dict[str, Any]] = []
            for pos in positions_data:
                current_value = float(pos.get("currentValue", 0) or 0)
                if current_value < VALUE_THRESHOLD:
                    continue
                filtered_positions.append(
                    {
                        "market": pos.get("title"),
                        "outcome": pos.get("outcome"),
                        "size": float(pos.get("size", 0) or 0),
                        "avgPrice": float(pos.get("avgPrice", 0) or 0),
                        "currentValue": current_value,
                        "cashPnl": float(pos.get("cashPnl", 0) or 0),
                        "percentPnl": float(pos.get("percentPnl", 0) or 0),
                        "slug": pos.get("slug"),
                        "icon": pos.get("icon"),
                        "asset": pos.get("asset"),
                        "conditionId": pos.get("conditionId"),
                    }
                )

            filtered_positions.sort(key=lambda x: x["size"], reverse=True)

            payload["positions"] = filtered_positions

            value_resp = self._session.get(
                POLYMARKET_VALUE_URL,
                params={"user": POLYMARKET_ADDRESS},
                timeout=20,
            )
            value_resp.raise_for_status()
            value_data = value_resp.json()[0] if isinstance(value_resp.json(), list) else value_resp.json()
            payload["totalValue"] = float(value_data.get("value", 0) or 0)
        except Exception as exc:  # pragma: no cover - network guard
            payload["error"] = str(exc)

        return payload

    def _fetch_opinion(self) -> Dict[str, Any]:
        """Fetch Opinion positions and total value."""

        payload: Dict[str, Any] = {
            "totalValue": 0.0,
            "positions": [],
            "error": None,
        }

        if not self._opinion_client:
            payload["error"] = "Opinion client not configured."
            return payload

        try:
            positions: List[Any] = []
            page = 1
            limit = 20
            while True:
                response = self._opinion_client.get_my_positions(page=page, limit=limit)
                if response.errno != 0:
                    raise RuntimeError(f"Opinion API error: {response.errmsg}")
                batch = response.result.list or []
                positions.extend(batch)
                if len(batch) < limit:
                    break
                page += 1

            filtered_positions: List[Dict[str, Any]] = []
            total_value = 0.0
            for pos in positions:
                current_value = float(_safe_get(pos, "current_value_in_quote_token", 0))
                if current_value < VALUE_THRESHOLD:
                    continue
                total_value += current_value
                # Construct full title with parent title if available
                parent_title = _safe_get(pos, "root_market_title")
                market_title = _safe_get(pos, "market_title")
                full_title = f"{parent_title} - {market_title}" if parent_title else market_title

                # Get token ID based on side
                side = _safe_get(pos, "outcome_side_enum", "")
                token_id = _safe_get(pos, "token_id")

                filtered_positions.append(
                    {
                        "marketId": _safe_get(pos, "market_id"),
                        "marketTitle": full_title,
                        "parentTitle": parent_title,
                        "subtitle": market_title,
                        "side": side,
                        "shares": float(_safe_get(pos, "shares_owned", 0)),
                        "avgPrice": float(_safe_get(pos, "avg_entry_price", 0)),
                        "currentValue": current_value,
                        "unrealizedPnl": float(_safe_get(pos, "unrealized_pnl", 0)),
                        "unrealizedPnlPercent": float(
                            _safe_get(pos, "unrealized_pnl_percent", 0)
                        ),
                        "tokenId": str(token_id) if token_id else None,
                    }
                )

            # Sort positions by shares (descending)
            filtered_positions.sort(key=lambda x: x["shares"], reverse=True)

            payload["positions"] = filtered_positions
            payload["totalValue"] = total_value
        except Exception as exc:  # pragma: no cover - network guard
            payload["error"] = str(exc)

        return payload

    def _calculate_matched_pairs(
        self, polymarket_data: Dict[str, Any], opinion_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Calculate matched position pairs across platforms."""
        result: Dict[str, Any] = {
            "pairs": [],
            "totalMatchedValue": 0.0,
            "error": None,
        }

        try:
            if not self._market_matches:
                result["error"] = "No market matches loaded"
                return result

            poly_positions = polymarket_data.get("positions", [])
            opinion_positions = opinion_data.get("positions", [])

            # Build index of positions by token ID
            poly_by_token: Dict[str, Dict[str, Any]] = {}
            for pos in poly_positions:
                asset = pos.get("asset")
                if asset:
                    poly_by_token[str(asset)] = pos

            opinion_by_token: Dict[str, Dict[str, Any]] = {}
            for pos in opinion_positions:
                token_id = pos.get("tokenId")
                if token_id:
                    opinion_by_token[str(token_id)] = pos

            # Match positions using market_matches.json
            matched_pairs = []
            for match in self._market_matches:
                # Check for Polymarket YES + Opinion YES pair
                poly_yes = poly_by_token.get(str(match.get("polymarket_yes_token")))
                opinion_yes = opinion_by_token.get(str(match.get("opinion_yes_token")))
                if poly_yes and opinion_yes:
                    pair = self._create_pair(match, poly_yes, opinion_yes, "Yes")
                    matched_pairs.append(pair)

                # Check for Polymarket NO + Opinion NO pair
                poly_no = poly_by_token.get(str(match.get("polymarket_no_token")))
                opinion_no = opinion_by_token.get(str(match.get("opinion_no_token")))
                if poly_no and opinion_no:
                    pair = self._create_pair(match, poly_no, opinion_no, "No")
                    matched_pairs.append(pair)

                # Check for cross-platform hedges: Polymarket YES + Opinion NO
                if poly_yes and opinion_no:
                    pair = self._create_pair(match, poly_yes, opinion_no, "Cross-Yes/No")
                    matched_pairs.append(pair)

                # Check for cross-platform hedges: Polymarket NO + Opinion YES
                if poly_no and opinion_yes:
                    pair = self._create_pair(match, poly_no, opinion_yes, "Cross-No/Yes")
                    matched_pairs.append(pair)

            # Sort by matched value (descending)
            matched_pairs.sort(key=lambda x: x["matchedValue"], reverse=True)

            total_matched_value = sum(pair["matchedValue"] for pair in matched_pairs)

            result["pairs"] = matched_pairs
            result["totalMatchedValue"] = total_matched_value

        except Exception as exc:
            result["error"] = str(exc)
            print(f"[warn] Failed to calculate matched pairs: {exc}")

        return result

    def _create_pair(
        self,
        match: Dict[str, Any],
        poly_pos: Dict[str, Any],
        opinion_pos: Dict[str, Any],
        pair_type: str,
    ) -> Dict[str, Any]:
        """Create a matched pair from two positions."""
        poly_size = poly_pos.get("size", 0)
        opinion_size = opinion_pos.get("shares", 0)

        # Matched shares is the minimum of the two positions
        matched_shares = min(poly_size, opinion_size)

        # Settlement value is $1 per matched share
        settlement_value = matched_shares * 1.0

        return {
            "question": match.get("question"),
            "pairType": pair_type,
            "polymarketPosition": {
                "market": poly_pos.get("market"),
                "outcome": poly_pos.get("outcome"),
                "size": poly_size,
                "avgPrice": poly_pos.get("avgPrice", 0),
                "currentValue": poly_pos.get("currentValue", 0),
            },
            "opinionPosition": {
                "market": opinion_pos.get("marketTitle"),
                "side": opinion_pos.get("side"),
                "size": opinion_size,
                "avgPrice": opinion_pos.get("avgPrice", 0),
                "currentValue": opinion_pos.get("currentValue", 0),
            },
            "matchedShares": matched_shares,
            "matchedValue": settlement_value,
            "polymarketExcess": poly_size - matched_shares,
            "opinionExcess": opinion_size - matched_shares,
        }


def _safe_get(obj: Any, attr: str, default: Any = None) -> Any:
    """Fetch attribute or dict key safely."""

    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the HTML dashboard and JSON API."""

    fetcher: PortfolioFetcher = PortfolioFetcher()
    investor_html_path = Path(__file__).with_name(INVESTOR_HTML_BASENAME)

    def do_GET(self) -> None:  # noqa: N802 - required name
        parsed = urlparse(self.path)
        if parsed.path == "/api/positions":
            self._serve_json()
        elif parsed.path == "/api/trades":
            self._serve_trades()
        elif parsed.path == "/api/balances":
            self._serve_balances()
        elif parsed.path == "/api/market_matches":
            self._serve_market_matches()
        elif parsed.path in {"/", "", "/index.html", "/investor", "/investor.html", f"/{INVESTOR_HTML_BASENAME}"}:
            self._serve_investor_html()
        else:
            self._respond_not_found()

    def _serve_json(self) -> None:
        snapshot = self.fetcher.get_snapshot()
        payload = {
            "polymarket": snapshot.polymarket,
            "opinion": snapshot.opinion,
            "matchedPairs": snapshot.matched_pairs,
            "lastUpdated": snapshot.last_updated,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._set_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_trades(self) -> None:
        from urllib.parse import parse_qs
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        limit = int(query.get("limit", ["10"])[0])
        limit = min(max(limit, 1), 50)  # Clamp between 1 and 50
        trades = self.fetcher.get_trades(limit)
        payload = {
            "polymarketTrades": trades.polymarket_trades,
            "opinionTrades": trades.opinion_trades,
            "lastUpdated": trades.last_updated,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._set_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_market_matches(self) -> None:
        matches = self.fetcher.get_market_matches()
        body = json.dumps(matches, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._set_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_balances(self) -> None:
        balances = self.fetcher.get_balances()
        body = json.dumps(balances, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._set_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_closed_positions(self) -> None:
        from urllib.parse import parse_qs
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        limit = int(query.get("limit", ["50"])[0])
        limit = min(max(limit, 1), 100)
        closed = self.fetcher.get_closed_positions(limit)
        payload = {
            "polymarket": closed.polymarket,
            "opinion": closed.opinion,
            "lastUpdated": closed.last_updated,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._set_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_investor_html(self) -> None:
        if not self.investor_html_path.exists():
            self._respond_not_found()
            return

        body = self.investor_html_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._set_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_not_found(self) -> None:
        body = b"Not Found"
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self._set_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _set_security_headers(self) -> None:
        """Apply strict security headers to every response."""

        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        """Redirect to stdout with a consistent prefix."""

        print(f"[http] {self.address_string()} - {fmt % args}")


def run_server(port: int) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"📊 Positions dashboard running on http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve Polymarket & Opinion portfolio dashboard",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind (default: {DEFAULT_PORT})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_server(args.port)
