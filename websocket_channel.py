from websocket import WebSocketApp
import json
import time
import threading
import os
import dotenv
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import hashlib

dotenv.load_dotenv()

# Configure module logger
logger = logging.getLogger("polymarket.websocket")
logger.setLevel(logging.DEBUG)

MARKET_CHANNEL = "market"
USER_CHANNEL = "user"


def setup_logger(asset_ids=None, condition_ids=None, verbose=False):
    """
    Setup logger with unique filename based on timestamp and asset/condition IDs.
    
    Args:
        asset_ids: List of asset IDs to include in filename
        condition_ids: List of condition IDs to include in filename
        verbose: If True, also add console handler
    
    Returns:
        The configured logger instance
    """
    # Generate unique log filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Create a short hash from asset_ids or condition_ids
    id_hash = ""
    if asset_ids and len(asset_ids) > 0:
        # Use first asset_id (or combine multiple if needed)
        id_str = str(asset_ids[0])[-8:]  # Last 8 chars
        id_hash = f"_asset_{id_str}"
    elif condition_ids and len(condition_ids) > 0:
        id_str = str(condition_ids[0])[-8:]
        id_hash = f"_cond_{id_str}"
    
    log_filename = f"websocket_{timestamp}{id_hash}.log"
    
    # Check if we already have a file handler, remove it to set up a new one
    logger.handlers = [h for h in logger.handlers if not isinstance(h, (RotatingFileHandler, logging.FileHandler))]
    
    # Add rotating file handler with unique filename
    file_handler = RotatingFileHandler(log_filename, maxBytes=5 * 1024 * 1024, backupCount=3)
    fmt = "%(asctime)s.%(msecs)03d %(levelname)s [%(threadName)s] %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"
    file_handler.setFormatter(logging.Formatter(fmt, datefmt))
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    
    # Add console handler if verbose and not already present
    if verbose and not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
        stream_handler.setLevel(logging.INFO)
        logger.addHandler(stream_handler)
    
    logger.info("=" * 60)
    logger.info("Log file: %s", log_filename)
    logger.info("Asset IDs: %s", asset_ids)
    logger.info("Condition IDs: %s", condition_ids)
    logger.info("=" * 60)
    
    return logger


class WebSocketOrderBook:
    def __init__(self, channel_type, url, data, auth, message_callback, verbose, 
                 max_reconnect_attempts=10, reconnect_delay=5):
        self.channel_type = channel_type
        self.url = url
        self.data = data
        self.auth = auth
        self.message_callback = message_callback
        self.verbose = verbose
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = reconnect_delay
        self.reconnect_count = 0
        self.should_reconnect = True
        self.ping_thread = None
        self.ping_stop_event = threading.Event()
        self.connection_success_count = 0
        self.last_connection_time = None
        
        furl = url + "/ws/" + channel_type
        self.furl = furl
        logger.info("Initializing WebSocket connection to: %s", furl)
        logger.info("Channel type: %s", channel_type)
        logger.info("Max reconnect attempts: %d, Reconnect delay: %ds", 
                   max_reconnect_attempts, reconnect_delay)
        
        self._create_websocket()
        self.orderbooks = {}
    
    def _create_websocket(self):
        """Create a new WebSocket connection"""
        self.ws = WebSocketApp(
            self.furl,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
        )

    def on_message(self, ws, message):
        logger.info("Received message: %s", message)
        # pass through to optional callback
        if self.message_callback:
            try:
                self.message_callback(message)
            except Exception:
                logger.exception("Exception in message_callback")

    def on_error(self, ws, error):
        logger.error("WebSocket error: %s", error)

    def on_close(self, ws, close_status_code, close_msg):
        logger.warning("WebSocket closed: code=%s msg=%s", close_status_code, close_msg)
        
        if self.should_reconnect and self.reconnect_count < self.max_reconnect_attempts:
            self.reconnect_count += 1
            # Exponential backoff: delay * (2 ^ (attempt - 1))
            wait_time = self.reconnect_delay * (2 ** (self.reconnect_count - 1))
            wait_time = min(wait_time, 300)  # Cap at 5 minutes
            
            logger.info("Attempting reconnection %d/%d in %d seconds...", 
                       self.reconnect_count, self.max_reconnect_attempts, wait_time)
            time.sleep(wait_time)
            
            try:
                self._create_websocket()
                logger.info("Reconnection attempt %d: starting new connection", self.reconnect_count)
                self.ws.run_forever()
            except Exception as e:
                logger.exception("Reconnection attempt %d failed: %s", self.reconnect_count, e)
        else:
            if self.reconnect_count >= self.max_reconnect_attempts:
                logger.error("Max reconnection attempts (%d) reached. Giving up.", 
                           self.max_reconnect_attempts)
            else:
                logger.info("Reconnection disabled or not needed. Exiting.")
            exit(1)

    def on_open(self, ws):
        # Reset reconnect count on successful connection
        if self.reconnect_count > 0:
            logger.info("WebSocket reconnection successful after %d attempts", self.reconnect_count)
            self.reconnect_count = 0
        
        self.connection_success_count += 1
        self.last_connection_time = datetime.now()
        logger.info("WebSocket connection opened (total connections: %d)", self.connection_success_count)
        
        if self.channel_type == MARKET_CHANNEL:
            msg = {"assets_ids": self.data, "type": MARKET_CHANNEL}
            logger.info("Sending market subscription: %s", msg)
            ws.send(json.dumps(msg))
        elif self.channel_type == USER_CHANNEL and self.auth:
            msg = {"markets": self.data, "type": USER_CHANNEL, "auth": self.auth}
            logger.info("Sending user channel subscription (auth omitted from log)")
            ws.send(json.dumps(msg))
        else:
            logger.error("Invalid channel configuration")
            exit(1)

        # Stop old ping thread if exists
        if self.ping_thread and self.ping_thread.is_alive():
            logger.debug("Stopping old ping thread")
            self.ping_stop_event.set()
            self.ping_thread.join(timeout=2)
        
        # Start new ping thread
        self.ping_stop_event.clear()
        logger.info("Starting new ping thread")
        self.ping_thread = threading.Thread(target=self.ping, args=(ws,), name="PingThread")
        self.ping_thread.daemon = True
        self.ping_thread.start()

    def ping(self, ws):
        ping_count = 0
        consecutive_failures = 0
        max_failures = 3
        
        while not self.ping_stop_event.is_set():
            try:
                if ws.sock and ws.sock.connected:
                    ws.send("PING")
                    ping_count += 1
                    consecutive_failures = 0
                    logger.debug("Sent PING #%d", ping_count)
                else:
                    logger.debug("WebSocket not connected, stopping ping thread")
                    break
            except Exception as e:
                consecutive_failures += 1
                logger.warning("Failed to send PING (attempt %d/%d): %s", 
                              consecutive_failures, max_failures, e)
                if consecutive_failures >= max_failures:
                    logger.error("Max ping failures reached, stopping ping thread")
                    break
            
            # Sleep in small intervals to allow quick exit
            for _ in range(10):
                if self.ping_stop_event.is_set():
                    break
                time.sleep(1)
        
        logger.debug("Ping thread stopped (sent %d pings)", ping_count)

    def run(self):
        self.ws.run_forever()


if __name__ == "__main__":
    url = "wss://ws-subscriptions-clob.polymarket.com"
    #Complete these by exporting them from your initialized client. 
    api_key = os.getenv("PM_API_KEY")
    api_secret = os.getenv("PM_API_SECRET")
    api_passphrase = os.getenv("PM_PASSPHRASE")

    asset_ids = [
        "30936936609724496138468607982445763622155194043662725455541377449855721703688",
    ]
    condition_ids = [] # no really need to filter by this one

    auth = {"apiKey": api_key, "secret": api_secret, "passphrase": api_passphrase}

    # Setup logger with unique filename based on asset_ids
    setup_logger(asset_ids=asset_ids, condition_ids=condition_ids, verbose=True)

    market_connection = WebSocketOrderBook(
        MARKET_CHANNEL, url, asset_ids, auth, None, True
    )
#     user_connection = WebSocketOrderBook(
        # USER_CHANNEL, url, condition_ids, auth, None, True
    # )

    market_connection.run()
    # user_connection.run()