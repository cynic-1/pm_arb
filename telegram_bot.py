#!/usr/bin/env python3
"""
Telegram bot that runs the arbitrage scanner periodically and pushes immediate arbitrage opportunities.

Config is loaded from a .env file. Required environment variables:
  TELEGRAM_BOT_TOKEN  - Bot token (eg. 123456:ABC-...)
  TELEGRAM_CHAT_ID    - Chat id to send messages to (user or group)
  PYTHON_EXEC         - Path to Python executable to run (default: venv/bin/python)
  MATCHES_FILE        - Comma-separated matches files (default: market_matches_multi.json,market_matches_unmatched.json)
  INTERVAL_SECONDS    - Interval between runs in seconds (default: 1800 = 30 minutes)

The bot runs continuously and will post the "immediate arbitrage" section from the script's output.
"""
import os
import time
import shlex
import subprocess
import traceback
import logging
from typing import Optional

from dotenv import load_dotenv
import requests

load_dotenv()

LOG = logging.getLogger("telegram_bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PYTHON_EXEC = os.getenv("PYTHON_EXEC", "venv/bin/python")
MATCHES_FILE = os.getenv("MATCHES_FILE", "market_matches_merged.json")
INTERVAL_SECONDS = int(os.getenv("INTERVAL_SECONDS", "600"))
CMD = f"{shlex.quote(PYTHON_EXEC)} arbitrage_parallel_retry_instant_lite.py --pro --no-interactive --matches-file {shlex.quote(MATCHES_FILE)}"


def send_message(text: str) -> bool:
    """Send a text message to the configured Telegram chat using Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        LOG.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=20)
        resp.raise_for_status()
        LOG.info("Message sent to Telegram chat %s", TELEGRAM_CHAT_ID)
        return True
    except Exception:
        LOG.exception("Failed to send Telegram message")
        return False


def run_scanner(timeout: int = 300) -> Optional[str]:
    """Run the arbitrage scanner command and return combined stdout+stderr as text."""
    LOG.info("Running command: %s", CMD)
    try:
        # Use shell=False for safety; split the command
        args = shlex.split(CMD)
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            cwd=os.path.dirname(__file__) or None,
            text=True,
        )
        output = completed.stdout or ""
        LOG.info("Scanner finished (returncode=%s), %d bytes captured", completed.returncode, len(output))
        return output
    except subprocess.TimeoutExpired:
        LOG.exception("Scanner timed out after %s seconds", timeout)
        return None
    except Exception:
        LOG.exception("Failed to run scanner command")
        return None


def extract_immediate_arbitrage_block(output: str) -> Optional[str]:
    """Extract immediate-arbitrage related log lines from the scanner output.

    The latest scanner version no longer prints the old summary blocks. Instead,
    immediate opportunities are logged line-by-line, e.g.:

        ✓ 发现立即套利: ...
        ⚡ 利润率 ... 启动即时执行线程
        🟢 即时执行机会: ...

    This helper collects those lines, normalizes them, and returns a concise
    message that can be delivered to Telegram. If no relevant lines are found it
    returns a short "no opportunities" notice instead.
    """
    if not output:
        return None

    text = output.replace("\r\n", "\n")
    lines = [line for line in text.splitlines() if line.strip()]

    import re

    def _strip_prefix(line: str) -> str:
        """Remove leading timestamp/module prefix to keep Telegram message short."""
        match = re.match(r"^\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+[^ ]+\s+[^ ]+\s+(.*)$", line)
        if match:
            return match.group(1).strip()
        return line.strip()

    detection_lines = []
    execution_lines = []
    exec_include = ["即时执行", "即时套利", "immediate"]
    exec_skip = ["即时执行已启用", "即时执行已禁用"]

    for raw_line in lines:
        cleaned = _strip_prefix(raw_line)
        if "发现立即套利" in cleaned:
            detection_lines.append(cleaned)
            continue
        if any(keyword in cleaned for keyword in exec_include):
            if not any(skip in cleaned for skip in exec_skip):
                execution_lines.append(cleaned)

    if not detection_lines and not execution_lines:
        return None

    blocks = []
    if detection_lines:
        header = f"🎯 立即套利检测（{len(detection_lines)} 条）"
        blocks.append("\n".join([header] + detection_lines))

    if execution_lines:
        blocks.append("⚡ 执行进度" + "\n" + "\n".join(execution_lines))

    return "\n\n".join(blocks)


def main_loop():
    LOG.info("Starting telegram bot loop: will run every %s seconds", INTERVAL_SECONDS)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        LOG.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID. Exiting.")
        return

    while True:
        try:
            output = run_scanner(timeout=180)
            if output is None:
                send_message("⚠️ 扫描器运行失败或超时。请检查运行环境和日志。")
            else:
                block = extract_immediate_arbitrage_block(output)
                if block:
                    # Telegram has message size limits; split if necessary
                    max_len = 3900
                    if len(block) <= max_len:
                        send_message(block)
                    else:
                        # Split into chunks at newline boundaries
                        parts = []
                        cur = []
                        cur_len = 0
                        for line in block.splitlines(True):
                            if cur_len + len(line) > max_len and cur:
                                parts.append(''.join(cur))
                                cur = [line]
                                cur_len = len(line)
                            else:
                                cur.append(line)
                                cur_len += len(line)
                        if cur:
                            parts.append(''.join(cur))
                        for p in parts:
                            send_message(p)
                            time.sleep(0.5)
                else:
                    send_message("ℹ️ 本轮未检测到立即套利机会（或无法解析输出）。")

        except KeyboardInterrupt:
            LOG.info("Interrupted by user, exiting")
            break
        except Exception:
            LOG.exception("Unexpected error in main loop")
            try:
                send_message("❌ Bot 发生异常，请检查日志。\n" + traceback.format_exc()[:1500])
            except Exception:
                LOG.exception("Failed to report exception to Telegram")

        LOG.info("Sleeping for %s seconds...", INTERVAL_SECONDS)
        time.sleep(INTERVAL_SECONDS)


if __name__ == '__main__':
    main_loop()
