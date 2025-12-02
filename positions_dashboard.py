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
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from opinion_clob_sdk import Client as OpinionClient

load_dotenv()

POLYMARKET_ADDRESS = os.getenv(
    "POLYMARKET_ADDRESS",
    "0xbd6c2a16c00ab38338b241783d454981a750a568",
)
POLYMARKET_POSITIONS_URL = "https://data-api.polymarket.com/positions"
POLYMARKET_VALUE_URL = "https://data-api.polymarket.com/value"
VALUE_THRESHOLD = float(os.getenv("POSITION_VALUE_THRESHOLD", "1"))
CACHE_SECONDS = int(os.getenv("DASHBOARD_CACHE_SECONDS", "600"))
DEFAULT_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
HTML_BASENAME = "positions_dashboard.html"


@dataclass
class FetchResult:
    """Normalized payload returned to the frontend."""

    polymarket: Dict[str, Any]
    opinion: Dict[str, Any]
    last_updated: str


class PortfolioFetcher:
    """Fetches and caches portfolio info for Polymarket & Opinion."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._cache: Optional[FetchResult] = None
        self._cache_ts = 0.0
        self._opinion_client = self._init_opinion_client()
        self._stop_event = threading.Event()
        self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        try:
            self.refresh_now()
        except Exception as exc:  # pragma: no cover - defensive guard
            print(f"[warn] Initial refresh failed: {exc}")
        self._refresh_thread.start()

    def _init_opinion_client(self) -> Optional[OpinionClient]:
        """Initialize Opinion client if credentials are present."""

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

    def refresh_now(self) -> None:
        """Force an immediate refresh from upstream APIs."""

        poly = self._fetch_polymarket()
        op = self._fetch_opinion()
        snapshot = FetchResult(
            polymarket=poly,
            opinion=op,
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
                    }
                )

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
            limit = 50
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
                filtered_positions.append(
                    {
                        "marketId": _safe_get(pos, "market_id"),
                        "marketTitle": _safe_get(pos, "market_title"),
                        "side": _safe_get(pos, "outcome_side_enum"),
                        "shares": float(_safe_get(pos, "shares_owned", 0)),
                        "avgPrice": float(_safe_get(pos, "avg_entry_price", 0)),
                        "currentValue": current_value,
                        "unrealizedPnl": float(_safe_get(pos, "unrealized_pnl", 0)),
                        "unrealizedPnlPercent": float(
                            _safe_get(pos, "unrealized_pnl_percent", 0)
                        ),
                    }
                )

            payload["positions"] = filtered_positions
            payload["totalValue"] = total_value
        except Exception as exc:  # pragma: no cover - network guard
            payload["error"] = str(exc)

        return payload


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
    html_path = Path(__file__).with_name(HTML_BASENAME)

    def do_GET(self) -> None:  # noqa: N802 - required name
        parsed = urlparse(self.path)
        if parsed.path == "/api/positions":
            self._serve_json()
        elif parsed.path in {"/", "", "/index.html", f"/{HTML_BASENAME}"}:
            self._serve_html()
        else:
            self._respond_not_found()

    def _serve_json(self) -> None:
        snapshot = self.fetcher.get_snapshot()
        payload = {
            "polymarket": snapshot.polymarket,
            "opinion": snapshot.opinion,
            "lastUpdated": snapshot.last_updated,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._set_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self) -> None:
        if not self.html_path.exists():
            self._respond_not_found()
            return

        body = self.html_path.read_bytes()
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
