"""\
模块化跨平台套利检测器（做市版）- Opinion vs Polymarket

目标：用 arbitrage_core 的模块化单元，重构实现 arbitrage_market_maker.py 的核心功能。

特性覆盖（与 arbitrage_market_maker.py 对齐）：
- 读取 market_matches.json（支持逗号分隔多个文件）
- pro 扫描循环（复用 modular_arbitrage.py 的实现）
- 流动性提供模式：在 Opinion 挂单 + 在 Polymarket 对冲
- Opinion 成交轮询（get_my_trades）与订单状态轮询（get_order_by_id）

说明：
- 订单簿获取、盈利性指标与下单重试等通用能力，复用 modular_arbitrage.ModularArbitrage。
- 平台客户端/配置/手续费/日志/模型/辅助函数来自 arbitrage_core。
"""

from __future__ import annotations

import argparse
import os
import threading
import time
import traceback
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

import logging

from arbitrage_core import ArbitrageConfig, LiquidityOrderState, MarketMatch
from arbitrage_core.utils import setup_logger
from arbitrage_core.utils.helpers import extract_from_entry, to_float, to_int

from modular_arbitrage import ModularArbitrage

# Opinion SDK
from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER

# Polymarket SDK
from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY
from arbitrage_core.utils.helpers import infer_tick_size_from_price

logger = logging.getLogger(__name__)


class ModularArbitrageMM(ModularArbitrage):
    """在 ModularArbitrage 基础上增加流动性做市与对冲能力。"""

    def __init__(self, config: Optional[ArbitrageConfig] = None):
        super().__init__(config=config)

        # 交易开关（保持与 arbitrage_market_maker.py 语义一致）
        self.polymarket_trading_enabled = self.clients.trading_enabled

        # ==================== 流动性提供模式配置（来自 config）====================
        self.liquidity_min_annualized = self.config.liquidity_min_annualized
        self.liquidity_min_size = self.config.liquidity_min_size
        self.liquidity_target_size = self.config.liquidity_target_size
        self.max_liquidity_orders = self.config.max_liquidity_orders
        self.liquidity_price_tolerance = self.config.liquidity_price_tolerance
        self.liquidity_status_poll_interval = self.config.liquidity_status_poll_interval
        self.liquidity_loop_interval = self.config.liquidity_loop_interval
        self.liquidity_requote_increment = self.config.liquidity_requote_increment
        self.liquidity_wait_timeout = self.config.liquidity_wait_timeout
        self.liquidity_trade_poll_interval = self.config.liquidity_trade_poll_interval
        self.liquidity_trade_limit = self.config.liquidity_trade_limit
        self.liquidity_debug = self.config.liquidity_debug

        # 流动性订单跟踪
        self.liquidity_orders: Dict[str, LiquidityOrderState] = {}
        self.liquidity_orders_by_id: Dict[str, LiquidityOrderState] = {}
        self._liquidity_orders_lock = threading.Lock()
        self._liquidity_status_stop = threading.Event()
        self._liquidity_status_thread: Optional[threading.Thread] = None

        # trades 轮询去重
        self._last_trade_poll = 0.0
        self._recent_trade_ids: Deque[str] = deque(maxlen=500)

        # 统计
        self._total_fills_count = 0
        self._total_fills_volume = 0.0
        self._total_hedge_count = 0
        self._total_hedge_volume = 0.0
        self._hedge_failures = 0
        self._stats_start_time = time.time()

    # -------------------- helpers（兼容原脚本命名）--------------------

    def _extract_from_entry(self, entry: Any, candidate_keys: List[str]) -> Optional[Any]:
        return extract_from_entry(entry, candidate_keys)

    def _to_float(self, value: Any) -> Optional[float]:
        return to_float(value)

    def _to_int(self, value: Any) -> Optional[int]:
        return to_int(value)

    def _status_is_filled(
        self, status: Optional[str], filled: Optional[float] = None, total: Optional[float] = None
    ) -> bool:
        normalized = str(status or "").strip().lower()
        if normalized in {"filled", "finished", "completed", "done", "success", "closed", "executed", "matched"}:
            return True
        if filled is not None and total is not None:
            return filled >= max(total - 1e-6, 0.0)
        return False

    def _status_is_cancelled(self, status: Optional[str]) -> bool:
        normalized = str(status or "").strip().lower()
        # cancelinprogress 表示取消请求已被接受，可视为已取消，无需继续监控
        return normalized in {"cancelled", "canceled", "rejected", "expired", "failed", "cancel", "cancelinprogress"}

    def _parse_opinion_status(self, entry: Any) -> Optional[str]:
        text_value = self._extract_from_entry(entry, ["status_enum", "statusEnum", "status_text", "statusText"])
        if text_value:
            status_str = str(text_value).lower()
            if status_str in ("pending", "open"):
                return "pending"
            if status_str in ("finished", "filled", "completed"):
                return "filled"
            if status_str in ("canceled", "cancelled"):
                return "cancelled"
            if status_str == "partial":
                return "partial"
            return status_str

        raw = self._extract_from_entry(entry, ["status"])
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            mapping = {0: "unknown", 1: "pending", 2: "filled", 3: "cancelled", 4: "partial"}
            return mapping.get(int(raw), str(raw))

        status_str = str(raw).lower()
        if status_str in ("pending", "open"):
            return "pending"
        if status_str in ("finished", "filled", "completed"):
            return "filled"
        if status_str in ("canceled", "cancelled"):
            return "cancelled"
        if status_str == "partial":
            return "partial"
        return status_str

    def _sum_trade_shares(self, trades: Any) -> Optional[float]:
        if not trades or not isinstance(trades, (list, tuple)):
            return None
        total = 0.0
        for trade in trades:
            shares = self._to_float(
                self._extract_from_entry(
                    trade,
                    [
                        "shares",
                        "filled_shares",
                        "filledAmount",
                        "filled_amount",
                        "maker_amount",
                    ],
                )
            )
            if shares is None or shares <= 0:
                continue
            total += shares
        return total if total > 0 else None

    def _coalesce_order_amount(self, entry: Any, fallback: Optional[float]) -> Optional[float]:
        amount = self._to_float(
            self._extract_from_entry(
                entry,
                [
                    "maker_amount",
                    "makerAmount",
                    "maker_amount_in_base_token",
                    "makerAmountInBaseToken",
                    "amount",
                    "order_shares",
                ],
            )
        )
        if amount is not None and amount > 0:
            return amount
        if fallback is not None and fallback > 0:
            return float(fallback)
        return None

    def _ensure_book_skew_within_bounds(self, match: MarketMatch, opinion_book: Any, polymarket_book: Any):
        max_skew = self.config.max_orderbook_skew
        if max_skew <= 0 or not opinion_book or not polymarket_book:
            return opinion_book, polymarket_book
        skew = abs(opinion_book.timestamp - polymarket_book.timestamp)
        if skew <= max_skew:
            return opinion_book, polymarket_book
        logger.warning(
            f"⚠️ 订单簿时间差 {skew:.2f}s 超过阈值 {max_skew:.2f}s，跳过本次检测: {match.question[:60]}"
        )
        return None, None

    # -------------------- thread utils --------------------

    def wait_for_active_exec_threads(self) -> None:
        """兼容 arbitrage_market_maker.py：等待 pro 即时执行线程结束。"""
        threads = list(getattr(self, "_active_exec_threads", []) or [])
        for t in threads:
            try:
                if t.is_alive():
                    t.join(timeout=2.0)
            except Exception:
                pass

    # -------------------- Liquidity mode --------------------

    def _make_liquidity_key(self, match: MarketMatch, opinion_token: str, direction: str) -> str:
        slug = match.polymarket_slug or str(match.polymarket_condition_id)
        return f"{match.opinion_market_id}:{opinion_token}:{direction}:{slug}"

    def _collect_liquidity_candidates(self, match: MarketMatch, opinion_yes_book: Any, poly_yes_book: Any) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        if not opinion_yes_book or not poly_yes_book:
            return candidates

        opinion_no_book = self.derive_no_orderbook(opinion_yes_book, match.opinion_no_token) if match.opinion_no_token else None
        poly_no_book = self.derive_no_orderbook(poly_yes_book, match.polymarket_no_token) if match.polymarket_no_token else None

        cand_yes = self._evaluate_liquidity_pair(
            match,
            opinion_yes_book,
            poly_no_book,
            match.opinion_yes_token,
            match.polymarket_no_token,
            "opinion_yes_poly_no",
        )
        if cand_yes:
            candidates.append(cand_yes)

        cand_no = self._evaluate_liquidity_pair(
            match,
            opinion_no_book,
            poly_yes_book,
            match.opinion_no_token,
            match.polymarket_yes_token,
            "opinion_no_poly_yes",
        )
        if cand_no:
            candidates.append(cand_no)

        return candidates

    def _evaluate_liquidity_pair(
        self,
        match: MarketMatch,
        opinion_book: Any,
        poly_book: Any,
        opinion_token: Optional[str],
        polymarket_token: Optional[str],
        direction: str,
    ) -> Optional[Dict[str, Any]]:
        if not opinion_book or not poly_book or not opinion_token or not polymarket_token:
            return None
        bid_level = opinion_book.best_bid()
        hedge_level = poly_book.best_ask()
        if not bid_level or not hedge_level:
            return None

        available_hedge = hedge_level.size or 0.0
        if available_hedge < self.liquidity_min_size:
            return None

        metrics = self.compute_profitability_metrics(
            match,
            "opinion",
            bid_level.price,
            "polymarket",
            hedge_level.price,
            available_hedge,
        )
        if not metrics:
            return None

        annualized = metrics.get("annualized_rate")
        if annualized is None or annualized < self.liquidity_min_annualized:
            return None

        target_size = min(self.liquidity_target_size, available_hedge)
        if target_size < self.liquidity_min_size:
            return None

        key = self._make_liquidity_key(match, opinion_token, direction)
        return {
            "key": key,
            "match": match,
            "opinion_token": opinion_token,
            "opinion_price": bid_level.price,
            "opinion_side": OrderSide.BUY,
            "polymarket_token": polymarket_token,
            "polymarket_price": hedge_level.price,
            "polymarket_available": available_hedge,
            "hedge_side": BUY,
            "direction": direction,
            "min_size": target_size,
            "annualized_rate": annualized,
            "profit_rate": metrics.get("profit_rate"),
            "cost": metrics.get("cost"),
        }

    def _scan_liquidity_opportunities(self) -> List[Dict[str, Any]]:
        if not self.market_matches:
            logger.error("⚠️ 未加载市场匹配，无法扫描流动性机会")
            return []

        candidate_map: Dict[str, Dict[str, Any]] = {}
        total_matches = len(self.market_matches)
        batch_size = self.config.orderbook_batch_size
        logger.info(f"🔍 扫描 {total_matches} 个市场的流动性机会 (年化阈值 ≥ {self.liquidity_min_annualized:.2f}%)")

        for batch_start in range(0, total_matches, batch_size):
            batch_matches = self.market_matches[batch_start : batch_start + batch_size]
            if not batch_matches:
                continue

            poly_tokens = [m.polymarket_yes_token for m in batch_matches if m.polymarket_yes_token]
            opinion_tokens = [m.opinion_yes_token for m in batch_matches if m.opinion_yes_token]

            # 并行批量拉取订单簿
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=2) as batching_executor:
                future_poly = batching_executor.submit(self.get_polymarket_orderbooks_bulk, poly_tokens)
                future_opinion = batching_executor.submit(self.fetch_opinion_orderbooks_parallel, opinion_tokens)
                poly_books = future_poly.result()
                opinion_books = future_opinion.result()

            for match in batch_matches:
                opinion_yes_book = opinion_books.get(match.opinion_yes_token)
                poly_yes_book = poly_books.get(match.polymarket_yes_token)
                opinion_yes_book, poly_yes_book = self._ensure_book_skew_within_bounds(match, opinion_yes_book, poly_yes_book)
                if not opinion_yes_book or not poly_yes_book:
                    continue

                for candidate in self._collect_liquidity_candidates(match, opinion_yes_book, poly_yes_book):
                    prev = candidate_map.get(candidate["key"])
                    if not prev or (candidate.get("annualized_rate") or 0.0) > (prev.get("annualized_rate") or 0.0):
                        candidate_map[candidate["key"]] = candidate

        logger.info(f"🔎 找到 {len(candidate_map)} 个满足年化收益阈值的机会")
        return list(candidate_map.values())

    def _register_liquidity_order_state(self, state: LiquidityOrderState) -> None:
        with self._liquidity_orders_lock:
            old_state = self.liquidity_orders.get(state.key)
            if old_state and old_state.order_id != state.order_id:
                self.liquidity_orders_by_id.pop(old_state.order_id, None)
                if self.liquidity_debug:
                    logger.info(f"🗑️ 移除旧订单 {old_state.order_id[:10]}... 引用 (被新订单替代)")

            self.liquidity_orders[state.key] = state
            self.liquidity_orders_by_id[state.order_id] = state

        if self.liquidity_debug:
            logger.info(f"📥 追踪流动性挂单 {state.order_id} -> {state.key}")
        self._ensure_liquidity_status_thread()

    def _remove_liquidity_order_state(self, key: str, force: bool = False) -> None:
        """移除流动性订单状态。

        Args:
            key: 订单唯一标识
            force: 是否强制删除。默认 False 时仅标记为已完成，不从数组中删除，
                   以确保即使取消订单出现错误，依然能够在检测成交后顺利完成对冲。
        """
        with self._liquidity_orders_lock:
            state = self.liquidity_orders.get(key)
            if state:
                if force:
                    # 强制删除：从两个字典中完全移除
                    self.liquidity_orders.pop(key, None)
                    self.liquidity_orders_by_id.pop(state.order_id, None)
                    if self.liquidity_debug:
                        logger.info(f"📤 强制移除流动性挂单 {state.order_id} -> {key}")
                else:
                    # 非强制：仅标记为已移除，保留在 by_id 字典中继续监控
                    # 这样即使取消订单失败，仍能检测到成交并完成对冲
                    state.marked_for_removal = True
                    # 从 liquidity_orders 中移除（不再参与新的机会匹配）
                    self.liquidity_orders.pop(key, None)
                    if self.liquidity_debug:
                        logger.info(f"📤 标记流动性挂单为已移除（保留监控）{state.order_id} -> {key}")

    def _fetch_opinion_order_status(self, order_id: str) -> Optional[Any]:
        try:
            self._throttle_opinion_request()
            response = self.clients.get_opinion_client().get_order_by_id(order_id)
        except Exception as exc:
            logger.warning(f"⚠️ Opinion 订单状态查询失败 {order_id}: {exc}")
            return None

        if getattr(response, "errno", 0) != 0:
            logger.warning(f"⚠️ Opinion 返回错误码 {getattr(response, 'errno', 0)} 查询 {order_id}")
            return None

        result = getattr(response, "result", None)
        data = getattr(result, "data", None) if result is not None else None
        return data or result

    def _cancel_liquidity_order(self, state: LiquidityOrderState, reason: str = "") -> bool:
        if not state or not state.order_id:
            return False

        # 发起取消
        try:
            self._throttle_opinion_request()
            response = self.clients.get_opinion_client().cancel_order(state.order_id)
            logger.info(f"🚫 已发送取消请求 Opinion 流动性挂单 {state.order_id[:10]}... ({reason})")
            if hasattr(response, "errno") and response.errno != 0:
                logger.error(f"⚠️ 取消请求返回错误码 {response.errno}: {getattr(response, 'errmsg', 'N/A')}")
                return False
        except Exception as exc:
            logger.error(f"⚠️ 发送取消请求失败 {state.order_id[:10]}...: {exc}")
            return False

        time.sleep(0.5)
        try:
            verify_response = self.clients.get_opinion_client().get_order_by_id(state.order_id)
            if getattr(verify_response, "errno", 0) != 0:
                logger.warning(
                    f"⚠️ 验证取消状态失败，无法查询订单 {state.order_id[:10]}... errno={getattr(verify_response, 'errno', 'N/A')}"
                )
                return False

            result = getattr(verify_response, "result", None)
            data = getattr(result, "data", None) if result is not None else None
            if not data and result:
                data = result
            if data and hasattr(data, "order_data"):
                data = data.order_data

            if not data:
                logger.warning(f"⚠️ 验证取消状态失败，未返回订单数据 {state.order_id[:10]}...")
                return False

            current_status = self._parse_opinion_status(data)
            logger.info(f"🔍 取消后验证状态: {state.order_id[:10]}... status={current_status}")

            if self._status_is_cancelled(current_status):
                logger.info(f"✅ 确认订单已取消: {state.order_id[:10]}...，标记为已移除但继续监控")
                # 不强制删除，保留监控以防取消状态误判（如 cancelinprogress）
                self._remove_liquidity_order_state(state.key, force=False)
                return True

            filled_amount = self._to_float(
                self._extract_from_entry(
                    data, ["filled_amount", "filledAmount", "filled_base_amount", "filledBaseAmount"]
                )
            ) or 0.0
            total_amount = self._to_float(
                self._extract_from_entry(
                    data, ["maker_amount", "makerAmount", "maker_amount_in_base_token", "makerAmountInBaseToken"]
                )
            )

            logger.warning(
                f"❌ 取消失败！订单仍处于 {current_status} 状态，filled={filled_amount:.2f}/{total_amount}, order_id={state.order_id[:10]}..."
            )

            if self._status_is_filled(current_status, filled_amount, total_amount):
                logger.warning(f"⚠️ 订单在取消过程中已成交！需要立即对冲: {state.order_id[:10]}...")
                if filled_amount > state.filled_size + 1e-6:
                    delta = filled_amount - state.filled_size
                    state.filled_size = filled_amount
                    if self.polymarket_trading_enabled:
                        self._hedge_polymarket(state, delta)
                # 订单已完全成交，可以强制删除
                self._remove_liquidity_order_state(state.key, force=True)
                return True

            return False

        except Exception as exc:
            logger.error(f"⚠️ 验证订单取消状态时异常 {state.order_id[:10]}...: {exc}")
            traceback.print_exc()
            return False

    def _cancel_obsolete_liquidity_orders(self, desired_keys: set) -> None:
        with self._liquidity_orders_lock:
            items = list(self.liquidity_orders.items())

        cancelled_count = 0
        failed_count = 0

        for key, state in items:
            if key in desired_keys:
                continue

            success = self._cancel_liquidity_order(state, reason="opportunity gone")
            if success:
                cancelled_count += 1
            else:
                failed_count += 1

        if cancelled_count > 0 or failed_count > 0:
            logger.info(f"📊 订单取消结果: 成功={cancelled_count}, 失败={failed_count}")

    def _ensure_liquidity_status_thread(self) -> None:
        if self._liquidity_status_thread and self._liquidity_status_thread.is_alive():
            return
        self._liquidity_status_stop.clear()
        thread = threading.Thread(
            target=self._liquidity_status_loop,
            name="liquidity-status-monitor",
            daemon=True,
        )
        thread.start()
        self._liquidity_status_thread = thread
        if self.liquidity_debug:
            logger.info("🛰️ 已启动 Opinion 订单状态监控线程")

    def _stop_liquidity_status_thread(self) -> None:
        if not self._liquidity_status_thread:
            return
        self._liquidity_status_stop.set()
        try:
            self._liquidity_status_thread.join(timeout=2.0)
        except Exception:
            pass
        self._liquidity_status_thread = None

    def _liquidity_status_loop(self) -> None:
        while not self._liquidity_status_stop.is_set() and not self._monitor_stop_event.is_set():
            with self._liquidity_orders_lock:
                tracked = list(self.liquidity_orders_by_id.items())

            if not tracked:
                self._liquidity_status_stop.wait(timeout=max(2.0, self.liquidity_status_poll_interval))
                continue

            try:
                self._update_liquidity_order_statuses(tracked_states=tracked)
                self._poll_opinion_trades()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logger.error(f"⚠️ 流动性订单状态监控异常: {exc}")
                traceback.print_exc()

            self._liquidity_status_stop.wait(timeout=self.liquidity_status_poll_interval)

    def wait_for_liquidity_orders(self, timeout: Optional[float] = None) -> None:
        if timeout is None or timeout <= 0:
            timeout = self.liquidity_wait_timeout

        start = time.time()
        while True:
            with self._liquidity_orders_lock:
                active = len(self.liquidity_orders_by_id)
            if not active:
                break
            if timeout and (time.time() - start) >= timeout:
                logger.info("⚠️ 等待 Opinion 挂单完成超时，仍有挂单在执行")
                break
            time.sleep(min(self.liquidity_status_poll_interval, 2.0))

        self._stop_liquidity_status_thread()

    def _update_liquidity_order_statuses(self, tracked_states: Optional[List[Tuple[str, LiquidityOrderState]]] = None) -> None:
        # 清理超时的已标记移除订单（保留监控 5 分钟后强制清理）
        MARKED_REMOVAL_TIMEOUT = 12*60*60.0  # 5 分钟

        if tracked_states is None:
            with self._liquidity_orders_lock:
                if not self.liquidity_orders_by_id:
                    return
                tracked_states = list(self.liquidity_orders_by_id.items())
        elif not tracked_states:
            return

        orders_to_force_remove: List[str] = []

        for order_id, state in tracked_states:
            now = time.time()

            # 检查是否需要强制清理已标记为移除的订单
            if state.marked_for_removal:
                time_since_update = now - state.updated_at
                if time_since_update > MARKED_REMOVAL_TIMEOUT:
                    logger.info(
                        f"🧹 订单 {order_id[:10]}... 已标记移除超过 {MARKED_REMOVAL_TIMEOUT:.0f}s，强制清理"
                    )
                    orders_to_force_remove.append(order_id)
                    continue

            if now - state.last_status_check < self.liquidity_status_poll_interval:
                continue

            status_entry = self._fetch_opinion_order_status(order_id)
            state.last_status_check = now
            if not status_entry:
                continue

            previous_status = state.status
            parsed_status = self._parse_opinion_status(status_entry)
            if parsed_status is not None:
                state.status = parsed_status

            filled_amount = self._to_float(
                self._extract_from_entry(
                    status_entry,
                    ["filled_amount", "filledAmount", "filled_base_amount", "filledBaseAmount"],
                )
            ) or 0.0

            if filled_amount <= 0:
                filled_shares = self._to_float(self._extract_from_entry(status_entry, ["filled_shares", "filledShares"]))
                if filled_shares:
                    filled_amount = filled_shares

            total_amount = self._to_float(
                self._extract_from_entry(
                    status_entry,
                    ["maker_amount", "makerAmount", "maker_amount_in_base_token", "makerAmountInBaseToken"],
                )
            )

            trades_sum = self._sum_trade_shares(self._extract_from_entry(status_entry, ["trades"]))
            if trades_sum and trades_sum > filled_amount:
                filled_amount = trades_sum

            if total_amount is None or total_amount <= 0:
                total_amount = self._coalesce_order_amount(status_entry, state.opinion_order_size)

            target_total = total_amount or state.opinion_order_size or state.effective_size or 0.0

            if self._status_is_filled(state.status, filled_amount, total_amount) and filled_amount < target_total - 1e-6:
                filled_amount = target_total

            log_needed = False
            if state.status != state.last_reported_status:
                log_needed = True
            elif abs(filled_amount - state.filled_size) > 1e-6:
                log_needed = True
            elif now - state.last_status_log >= 30.0:
                log_needed = True

            if log_needed:
                logger.info(
                    f"🔍 Opinion 状态: {order_id[:10]} status={state.status or previous_status} "
                    f"filled={filled_amount:.2f}/{target_total:.2f}"
                )
                state.last_reported_status = state.status
                state.last_status_log = now

            if filled_amount > state.filled_size + 1e-6:
                delta = filled_amount - state.filled_size
                state.filled_size = filled_amount

                self._total_fills_count += 1
                self._total_fills_volume += delta

                logger.info("=" * 80)
                logger.info("💰💰💰 【订单状态检测到成交】")
                logger.info(f"    订单ID: {order_id}")
                logger.info(f"    本次成交: {delta:.2f}")
                logger.info(f"    累计成交: {state.filled_size:.2f} / {target_total:.2f}")
                logger.info(f"    成交进度: {(state.filled_size / target_total * 100) if target_total > 0 else 0:.1f}%")
                logger.info(f"    【统计】总成交次数: {self._total_fills_count}, 总成交量: {self._total_fills_volume:.2f}")
                logger.info("=" * 80)

                if self.polymarket_trading_enabled:
                    logger.info("🚀 开始执行对冲操作...")
                    self._hedge_polymarket(state, delta)
                else:
                    logger.error("⚠️⚠️⚠️ Polymarket 未启用交易，无法对冲！")

            if self._status_is_cancelled(state.status):
                logger.info(f"⚠️ Opinion 挂单 {order_id[:10]}... 状态 {state.status}，标记为已移除但继续监控")
                # 不从 by_id 中删除，保留监控以确保即使取消失败也能检测到成交并对冲
                self._remove_liquidity_order_state(state.key, force=False)
                continue

            if self._status_is_filled(state.status, filled_amount, total_amount):
                logger.info(f"🏁 Opinion 挂单 {order_id[:10]}... 已完成，强制移除")
                # 订单完全成交，可以安全地强制删除
                self._remove_liquidity_order_state(state.key, force=True)

        # 执行强制清理超时的已标记移除订单
        if orders_to_force_remove:
            with self._liquidity_orders_lock:
                for order_id in orders_to_force_remove:
                    state = self.liquidity_orders_by_id.pop(order_id, None)
                    if state and self.liquidity_debug:
                        logger.info(f"🧹 已强制清理订单 {order_id[:10]}... from by_id")

    def _poll_opinion_trades(self) -> None:
        now = time.time()
        if now - self._last_trade_poll < self.liquidity_trade_poll_interval:
            return
        self._last_trade_poll = now

        max_retries = 3
        trade_list = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.clients.get_opinion_client().get_my_trades(limit=self.liquidity_trade_limit)

                if getattr(response, "errno", 1) != 0:
                    if attempt < max_retries:
                        logger.warning(
                            f"⚠️ Opinion trades API errno={getattr(response, 'errno', None)}, 重试 {attempt}/{max_retries}"
                        )
                        time.sleep(1.0)
                        continue
                    logger.error(f"❌ Opinion trades API 调用失败达到最大重试次数！errno={getattr(response, 'errno', None)}")
                    return

                trade_list = getattr(getattr(response, "result", None), "list", None)
                if not trade_list:
                    return
                break

            except Exception as exc:
                if attempt < max_retries:
                    logger.warning(f"⚠️ Opinion trades API 调用异常: {exc}, 重试 {attempt}/{max_retries}")
                    time.sleep(1.0)
                    continue
                logger.error(f"❌ Opinion trades API 调用失败达到最大重试次数！异常: {exc}")
                traceback.print_exc()
                return

        if not trade_list:
            return

        new_trades_count = 0
        tracked_trades_count = 0
        untracked_trades_count = 0
        trades_by_order: Dict[str, List[Dict[str, Any]]] = {}

        for trade in trade_list:
            order_no = self._extract_from_entry(trade, ["order_no", "orderNo", "order_id", "orderId"])
            trade_no = self._extract_from_entry(trade, ["trade_no", "tradeNo", "id"])
            if not order_no or not trade_no:
                continue

            order_no = str(order_no)
            trade_no = str(trade_no)

            if trade_no in self._recent_trade_ids:
                continue

            status = self._parse_opinion_status(trade)
            if status != "filled":
                continue

            self._recent_trade_ids.append(trade_no)
            new_trades_count += 1

            price = self._to_float(self._extract_from_entry(trade, ["price"]))
            shares = self._to_float(
                self._extract_from_entry(trade, ["shares", "filled_shares", "filledAmount", "filled_amount"])
            )

            if shares is None or shares <= 1e-6:
                amount = self._to_float(self._extract_from_entry(trade, ["amount", "order_shares"]))
                if amount and amount > 1e-6:
                    shares = amount
                else:
                    usd_amount = self._to_float(self._extract_from_entry(trade, ["usd_amount", "usdAmount"]))
                    if usd_amount and usd_amount > 1e-6 and price and price > 1e-6:
                        usd_value = usd_amount / 1e18
                        shares = usd_value / price
                    else:
                        continue

            side = self._extract_from_entry(trade, ["side", "side_enum"])
            market_id = self._extract_from_entry(trade, ["market_id", "marketId"])
            created_at = self._extract_from_entry(trade, ["created_at", "createdAt", "timestamp"])

            trades_by_order.setdefault(order_no, []).append(
                {
                    "trade": trade,
                    "trade_no": trade_no,
                    "shares": shares,
                    "price": price,
                    "side": side,
                    "status": status,
                    "market_id": market_id,
                    "created_at": created_at,
                }
            )

        for order_no, trade_list_for_order in trades_by_order.items():
            with self._liquidity_orders_lock:
                state = self.liquidity_orders_by_id.get(order_no)

            if state:
                tracked_trades_count += len(trade_list_for_order)
                total_shares = sum(t["shares"] for t in trade_list_for_order)

                logger.info("=" * 80)
                logger.info("💰💰💰 【新成交】检测到流动性订单成交！")
                logger.info(f"    订单ID: {order_no[:10]}...")
                logger.info(f"    成交笔数: {len(trade_list_for_order)}")
                logger.info(f"    总成交量: {total_shares:.2f}")
                logger.info("    成交明细:")
                for idx, t in enumerate(trade_list_for_order, 1):
                    logger.info(
                        f"      {idx}. trade={t['trade_no'][:10]}..., shares={t['shares']:.2f}, price={t['price']}, time={t['created_at']}"
                    )
                logger.info("=" * 80)

                self._handle_opinion_trades_aggregated(trade_list_for_order, state)
            else:
                untracked_trades_count += len(trade_list_for_order)

        if new_trades_count > 0:
            logger.info(
                f"📊 交易轮询摘要: 新交易={new_trades_count}, 跟踪订单={tracked_trades_count}, 未跟踪订单={untracked_trades_count}"
            )

    def _handle_opinion_trades_aggregated(self, trade_list: list, state: LiquidityOrderState) -> None:
        total_shares = sum(t["shares"] for t in trade_list)
        if total_shares > 0:
            avg_price = sum(t["shares"] * (t["price"] or 0.0) for t in trade_list) / total_shares
        else:
            avg_price = (trade_list[0].get("price") or 0.0) if trade_list else 0.0

        delta = total_shares
        state.filled_size += delta

        self._total_fills_count += 1
        self._total_fills_volume += delta

        logger.info("┌" + "─" * 78 + "┐")
        logger.info(f"│ ✅ 成交处理: 订单 {state.order_id[:10]}...")
        logger.info(f"│    本次成交: {delta:.2f} (聚合 {len(trade_list)} 笔交易)")
        logger.info(f"│    累计成交: {state.filled_size:.2f}")
        logger.info(f"│    平均价格: {avg_price:.4f}")
        logger.info(f"│    【统计】总成交次数: {self._total_fills_count}, 总成交量: {self._total_fills_volume:.2f}")
        logger.info("└" + "─" * 78 + "┘")

        if self.polymarket_trading_enabled:
            logger.info("🚀 开始执行对冲操作...")
            self._hedge_polymarket(state, delta)
        else:
            logger.warning("⚠️⚠️⚠️ Polymarket 未启用交易，无法对冲！")

        if state.filled_size >= state.effective_size - 1e-6:
            logger.info(f"🏁 Opinion 挂单 {state.order_id[:10]}... 已完全成交，强制移除")
            # 订单完全成交，可以安全地强制删除
            self._remove_liquidity_order_state(state.key, force=True)

    def _hedge_polymarket(self, state: LiquidityOrderState, hedge_size: float) -> None:
        remaining = max(0.0, hedge_size)
        if remaining <= 0.0:
            return
        if not self.polymarket_trading_enabled:
            return

        logger.info("╔" + "═" * 78 + "╗")
        logger.info("║ 🛡️ 【对冲下单】开始执行 Polymarket 对冲")
        logger.info(f"║    需对冲数量: {hedge_size:.2f}")
        logger.info(f"║    对冲代币: {state.hedge_token}")
        logger.info(f"║    对冲方向: {state.hedge_side}")
        logger.info("╠" + "═" * 78 + "╣")

        hedge_attempts = 0
        total_hedged = 0.0

        while remaining > 1e-6:
            hedge_attempts += 1
            book = self.get_polymarket_orderbook(state.hedge_token, depth=1)
            if not book or not book.asks:
                logger.warning("║ ❌ 对冲失败：缺少 Polymarket 流动性")
                break

            best_ask = book.asks[0]
            tradable = min(remaining, best_ask.size or 0.0)
            if tradable <= 1e-6:
                logger.warning(f"║ ⚠️ 对冲数量 {remaining:.4f} 超出当前卖单数量，等待下一次机会")
                break

            order = OrderArgs(
                token_id=state.hedge_token,
                price=best_ask.price,
                size=tradable,
                side=state.hedge_side,
                fee_rate_bps=0,
            )
            # 创建选项以避免额外的网络请求
            options = PartialCreateOrderOptions(
                tick_size=infer_tick_size_from_price(best_ask.price),
                neg_risk=state.match.polymarket_neg_risk,
            )

            logger.info(f"║ 📤 正在下单：数量 {tradable:.2f}, 价格 {best_ask.price}, 尝试 {hedge_attempts}")

            success, _ = self.place_polymarket_order_with_retries(order, OrderType.GTC, context="流动性对冲", options=options)
            if not success:
                logger.warning(f"║ ❌ 对冲下单失败，剩余 {remaining:.2f}")
                self._hedge_failures += 1
                break

            remaining -= tradable
            state.hedged_size += tradable
            total_hedged += tradable

            self._total_hedge_count += 1
            self._total_hedge_volume += tradable

            logger.info(f"║ ✅ 对冲成功：本次 {tradable:.2f}, 累计已对冲 {state.hedged_size:.2f}")

            if remaining > 1e-6:
                time.sleep(0.2)

        logger.info("╠" + "═" * 78 + "╣")
        if remaining <= 1e-6:
            logger.info(f"║ 🎉🎉🎉 对冲完成！总计对冲 {total_hedged:.2f}")
        else:
            logger.warning(f"║ ⚠️⚠️⚠️ 对冲未完成！已对冲 {total_hedged:.2f}, 剩余 {remaining:.2f}")
        uptime = time.time() - self._stats_start_time
        hours = uptime / 3600
        logger.info(
            f"║ 【累计统计】成交: {self._total_fills_count}次/{self._total_fills_volume:.2f}量, "
            f"对冲: {self._total_hedge_count}次/{self._total_hedge_volume:.2f}量, "
            f"失败: {self._hedge_failures}次, "
            f"运行: {hours:.1f}小时"
        )
        logger.info("╚" + "═" * 78 + "╝")

    def _place_liquidity_order(self, opportunity: Dict[str, Any]) -> Optional[LiquidityOrderState]:
        target_size = min(
            opportunity.get("min_size", self.liquidity_target_size),
            opportunity.get("polymarket_available", self.liquidity_target_size),
            self.liquidity_target_size,
        )
        if target_size < self.liquidity_min_size:
            return None

        opinion_price = self.fee_calculator.round_price(opportunity["opinion_price"])
        if opinion_price is None:
            return None

        order_size, effective_size = self.fee_calculator.get_order_size_for_platform(
            "opinion", opinion_price, target_size, verbose=False
        )

        # Opinion 最小名义金额检查：order_size * price >= 1.3 USDT
        nominal_amount = order_size * opinion_price
        if nominal_amount < 1.3:
            if self.liquidity_debug:
                logger.error(f"⚠️ Opinion 订单名义金额 {nominal_amount:.4f} USDT < 1.3 USDT，跳过下单")
            return None

        try:
            order = PlaceOrderDataInput(
                marketId=opportunity["match"].opinion_market_id,
                tokenId=str(opportunity["opinion_token"]),
                side=opportunity["opinion_side"],
                orderType=LIMIT_ORDER,
                price=str(opinion_price),
                makerAmountInBaseToken=str(order_size),
            )
        except Exception as exc:
            logger.error(f"⚠️ 构造 Opinion 流动性订单失败: {exc}")
            return None

        success, result = self.place_opinion_order_with_retries(order, context="流动性挂单")
        if not success or not result:
            return None

        order_data = (
            getattr(getattr(result, "result", None), "order_data", None)
            or getattr(getattr(result, "result", None), "data", None)
        )
        order_id = self._extract_from_entry(order_data, ["order_id", "orderId"])
        if not order_id:
            logger.error("⚠️ 未返回 Opinion 订单编号，无法跟踪流动性挂单")
            return None

        order_id = str(order_id)
        logger.info(
            f"✅ 已在 Opinion 挂单 {order_id[:10]}... price={opinion_price:.3f}, size={order_size:.2f}, 目标净数量={effective_size:.2f}"
        )

        return LiquidityOrderState(
            key=opportunity["key"],
            order_id=order_id,
            match=opportunity["match"],
            opinion_token=opportunity["opinion_token"],
            opinion_price=opinion_price,
            opinion_side=opportunity["opinion_side"],
            opinion_order_size=order_size,
            effective_size=effective_size,
            hedge_token=opportunity["polymarket_token"],
            hedge_side=opportunity["hedge_side"],
            hedge_price=opportunity["polymarket_price"],
            last_roi=opportunity.get("profit_rate"),
            last_annualized=opportunity.get("annualized_rate"),
        )

    def _ensure_liquidity_order(self, opportunity: Dict[str, Any]) -> bool:
        key = opportunity["key"]
        with self._liquidity_orders_lock:
            existing = self.liquidity_orders.get(key)
            active_count = len(self.liquidity_orders)

        if existing:
            existing.last_roi = opportunity.get("profit_rate")
            existing.last_annualized = opportunity.get("annualized_rate")
            new_price = opportunity.get("opinion_price")
            need_requote = False

            if new_price is not None:
                if new_price > (existing.opinion_price + max(self.liquidity_requote_increment, 0.0) + 1e-6):
                    logger.info(
                        f"⬆️ Opinion 买一价 {new_price:.3f} 超过当前挂单 {existing.opinion_price:.3f}，撤单重新挂: {key}"
                    )
                    need_requote = True
                else:
                    price_diff = abs(existing.opinion_price - new_price)
                    if price_diff > self.liquidity_price_tolerance:
                        logger.info(f"🔁 流动性挂单价格偏移 {price_diff:.4f}，重新挂单: {key}")
                        need_requote = True

            if need_requote:
                cancel_success = self._cancel_liquidity_order(existing, reason="repricing")
                if not cancel_success:
                    logger.warning(f"⚠️ 取消订单失败，保持旧订单 {existing.order_id[:10]}... 继续监控")
                    existing.hedge_price = opportunity["polymarket_price"]
                    existing.updated_at = time.time()
                    return True
                existing = None
            else:
                existing.hedge_price = opportunity["polymarket_price"]
                existing.updated_at = time.time()
                return True

        if active_count >= self.max_liquidity_orders:
            logger.warning(f"⚠️ 已达到最大流动性挂单数量 {self.max_liquidity_orders}，跳过 {key}")
            return False

        state = self._place_liquidity_order(opportunity)
        if state:
            self._register_liquidity_order_state(state)
            return True

        return False

    def run_liquidity_provider_cycle(self) -> None:
        candidates = self._scan_liquidity_opportunities()
        if not candidates:
            self._cancel_obsolete_liquidity_orders(set())
            self._update_liquidity_order_statuses()
            return

        candidates.sort(key=lambda x: x.get("annualized_rate") or 0.0, reverse=True)
        desired_keys: List[str] = []
        for candidate in candidates:
            if len(desired_keys) >= self.max_liquidity_orders:
                break
            if self._ensure_liquidity_order(candidate):
                desired_keys.append(candidate["key"])

        self._cancel_obsolete_liquidity_orders(set(desired_keys))
        self._update_liquidity_order_statuses()

    def run_liquidity_provider_loop(self, interval_seconds: Optional[float] = None) -> None:
        interval = max(0.5, interval_seconds or self.liquidity_loop_interval)
        logger.info(f"♻️ 启动流动性提供循环，间隔 {interval:.1f}s")
        try:
            while not self._monitor_stop_event.is_set():
                start = time.time()
                try:
                    self.run_liquidity_provider_cycle()
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    logger.error(f"❌ 流动性提供循环异常: {exc}")
                    traceback.print_exc()
                elapsed = time.time() - start
                sleep_time = max(0.0, interval - elapsed)
                if sleep_time > 0:
                    self._monitor_stop_event.wait(timeout=sleep_time)
        finally:
            self._monitor_stop_event.set()
            self.wait_for_liquidity_orders()

    # -------------------- smoke test --------------------

    def test(self) -> None:
        """兼容原脚本 --test：做最小自检，不触发下单。"""
        logger.info("🧪 self-test: config + clients + fee calculator")
        self.config.display_summary()
        # 费率自检
        fee_rate = self.fee_calculator.calculate_opinion_fee_rate(0.55)
        logger.info(f"fee_rate(0.55)={fee_rate}")

def main() -> None:
    parser = argparse.ArgumentParser(
        description="模块化跨平台套利检测器（做市版）- Opinion vs Polymarket",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python modular_arbitrage_mm.py --pro --matches-file market_matches.json
  python modular_arbitrage_mm.py --liquidity --matches-file market_matches.json
""",
    )

    # 保持与 arbitrage_market_maker.py CLI 兼容
    parser.add_argument("--use-cached", action="store_true", help="兼容参数：当前实现始终从 matches-file 加载")
    parser.add_argument(
        "--matches-file",
        type=str,
        default="market_matches.json",
        help="市场匹配结果文件路径，支持多个文件用逗号分隔 (默认: market_matches.json)",
    )
    parser.add_argument("--no-search", action="store_true", help="兼容参数：当前实现不包含搜索匹配逻辑")
    parser.add_argument("--no-interactive", action="store_true", help="兼容参数：pro 模式仅扫描输出")

    parser.add_argument("--test", action="store_true", help="运行测试函数")

    parser.add_argument("--pro", action="store_true", help="运行专业套利执行模式")
    parser.add_argument("--pro-once", action="store_true", help="仅运行一次专业套利扫描，不进入循环")
    parser.add_argument(
        "--loop-interval",
        type=float,
        default=None,
        help="专业模式循环间隔时间（秒），默认读取 PRO_LOOP_INTERVAL 环境变量 (默认 90s)",
    )

    parser.add_argument("--liquidity", action="store_true", help="运行流动性提供模式")
    parser.add_argument("--liquidity-once", action="store_true", help="仅运行一次流动性扫描，不进入循环")
    parser.add_argument(
        "--liquidity-interval",
        type=float,
        default=None,
        help="流动性模式循环间隔（秒），默认读取 LIQUIDITY_LOOP_INTERVAL 环境变量",
    )

    args = parser.parse_args()

    try:
        config = ArbitrageConfig()
        setup_logger(config.log_dir, config.arbitrage_log_pointer)

        scanner = ModularArbitrageMM(config)

        if args.test:
            scanner.test()
            return

        if args.pro:
            if not scanner.load_market_matches(args.matches_file):
                logger.error("⚠️ 无法加载市场匹配")
                return

            if args.loop_interval is not None:
                loop_interval = max(0.0, args.loop_interval)
            else:
                loop_interval = config.pro_loop_interval

            if args.pro_once or loop_interval <= 0:
                try:
                    scanner.execute_arbitrage_pro()
                finally:
                    scanner.wait_for_active_exec_threads()
            else:
                scanner.run_pro_loop(loop_interval)
            return

        if args.liquidity:
            if not scanner.polymarket_trading_enabled:
                logger.error("⚠️ 未配置 Polymarket 交易密钥，无法执行对冲。")
                return

            if not scanner.load_market_matches(args.matches_file):
                logger.error("⚠️ 无法加载市场匹配")
                return

            liquidity_interval = (
                max(0.0, args.liquidity_interval)
                if args.liquidity_interval is not None
                else config.liquidity_loop_interval
            )

            if args.liquidity_once or liquidity_interval <= 0:
                scanner.run_liquidity_provider_cycle()
                scanner.wait_for_liquidity_orders()
            else:
                scanner.run_liquidity_provider_loop(liquidity_interval)
            return

        # 与原脚本类似：未指定 mode 时不做任何事
        print("ℹ️ 未指定模式参数：请使用 --pro 或 --liquidity")

    except KeyboardInterrupt:
        logger.warning("\n\n⚠️ 用户中断")
    except Exception as exc:
        logger.error(f"\n❌ 发生错误: {exc}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
