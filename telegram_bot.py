#!/usr/bin/env python3
"""
Telegram bot that runs the arbitrage scanner periodically and pushes immediate arbitrage opportunities.

Config is loaded from a .env file. Required environment variables:
    TELEGRAM_BOT_TOKEN   - Bot token (eg. 123456:ABC-...)
    TELEGRAM_CHAT_ID     - Chat id to send messages to (user or group)
    ARBITRAGE_LOG_PATH   - Absolute path to arbitrage log file to follow (or directory pointer file)

Optional environment variables:
    LOG_POINTER_FILE     - File that stores the path of the latest log (default: logs/CURRENT_LOG)
    FOLLOW_INTERVAL      - Seconds between log polling (default: 5)
    MAX_CHUNK_BYTES      - Max bytes per Telegram message chunk (default: 3500)

The bot no longer launches the scanner itself; instead it tails the arbitrage
log file and forwards newly appended "immediate arbitrage" segments.
"""
import os
import time
import traceback
import logging
from typing import Optional
from pathlib import Path

from dotenv import load_dotenv
import requests

load_dotenv()

LOG = logging.getLogger("telegram_bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FOLLOW_INTERVAL = float(os.getenv("FOLLOW_INTERVAL", "5"))
MAX_CHUNK_BYTES = int(os.getenv("MAX_CHUNK_BYTES", "3500"))
LOG_POINTER_FILE = os.getenv("LOG_POINTER_FILE", "logs/CURRENT_LOG")
ARBITRAGE_LOG_PATH = os.getenv("ARBITRAGE_LOG_PATH")


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


def resolve_log_path() -> Optional[Path]:
    """Resolve the actual log file to tail.

    Priority:
      1. ARBITRAGE_LOG_PATH env (file or pointer).
      2. LOG_POINTER_FILE env (defaults to logs/CURRENT_LOG).
    """
    if ARBITRAGE_LOG_PATH:
        candidate = Path(ARBITRAGE_LOG_PATH)
        if candidate.is_dir():
            LOG.warning("ARBITRAGE_LOG_PATH points to a directory; expecting file")
            return None
        return candidate

    pointer = Path(LOG_POINTER_FILE)
    if not pointer.exists():
        LOG.error("Pointer file %s does not exist", pointer)
        return None
    try:
        target = pointer.read_text(encoding="utf-8").strip()
    except Exception:
        LOG.exception("Failed to read pointer file %s", pointer)
        return None
    if not target:
        LOG.error("Pointer file %s is empty", pointer)
        return None
    log_path = Path(target)
    if not log_path.exists():
        LOG.error("Log file %s from pointer does not exist", log_path)
        return None
    return log_path


def tail_new_content(log_path: Path, last_offset: int) -> tuple[int, Optional[str]]:
    """Read new bytes from log file starting at last_offset."""
    try:
        size = log_path.stat().st_size
    except FileNotFoundError:
        LOG.error("Log file %s disappeared", log_path)
        return last_offset, None
    except Exception:
        LOG.exception("Failed to stat log file %s", log_path)
        return last_offset, None

    if size <= last_offset:
        return size, None

    try:
        with log_path.open("r", encoding="utf-8", errors="ignore") as fh:
            fh.seek(last_offset)
            data = fh.read()
            return fh.tell(), data
    except Exception:
        LOG.exception("Failed to read log file %s", log_path)
        return last_offset, None


def main_loop():
    LOG.info("Starting telegram bot loop; polling interval=%ss", FOLLOW_INTERVAL)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        LOG.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID. Exiting.")
        return

    log_path = resolve_log_path()
    if not log_path:
        send_message("❌ 无法定位套利日志文件，Bot 已停止。")
        return

    LOG.info("Following arbitrage log: %s", log_path)
    last_offset = 0

    while True:
        try:
            log_path = resolve_log_path() or log_path
            offset, chunk = tail_new_content(log_path, last_offset)
            last_offset = offset

            if chunk:
                block = extract_immediate_arbitrage_block(chunk)
                if block:
                    if len(block) <= MAX_CHUNK_BYTES:
                        send_message(block)
                    else:
                        parts = []
                        cur = []
                        cur_len = 0
                        for line in block.splitlines(True):
                            if cur_len + len(line) > MAX_CHUNK_BYTES and cur:
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
            time.sleep(FOLLOW_INTERVAL)

        except KeyboardInterrupt:
            LOG.info("Interrupted by user, exiting")
            break
        except Exception:
            LOG.exception("Unexpected error in main loop")
            try:
                send_message("❌ Bot 发生异常，请检查日志。\n" + traceback.format_exc()[:1500])
            except Exception:
                LOG.exception("Failed to report exception to Telegram")


if __name__ == '__main__':
    main_loop()
