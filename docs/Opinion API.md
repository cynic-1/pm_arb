# Opinion API

### Prerequisites

Before starting, ensure you have:

1. **Python 3.8+** installed
2. **Opinion CLOB SDK** installed (Installation Guide)
3. **API credentials** from Opinion Labs:
   - API Key
   - Private Key (for signing orders)
   - Multi-sig wallet address (create on https://app.opinion.trade)
   - RPC URL (BNB Chain mainnet)

> **Need credentials?** Fill out this [short application form](https://docs.google.com/forms/d/1h7gp8UffZeXzYQ-lv4jcou9PoRNOqMAQhyW4IwZDnII) to get your API key.

### 5-Minute Quickstart

#### Step 1: Set Up Environment

Create a `.env` file in your project directory:

Copy

```
# .env file
API_KEY=your_api_key_here
RPC_URL=https://bsc-dataseed.binance.org
PRIVATE_KEY=0x1234567890abcdef...
MULTI_SIG_ADDRESS=0xYourWalletAddress...
HOST=https://proxy.opinion.trade:8443
CHAIN_ID=56
CONDITIONAL_TOKEN_ADDR=0xAD1a38cEc043e70E83a3eC30443dB285ED10D774
MULTISEND_ADDR=0x998739BFdAAdde7C933B942a68053933098f9EDa
```

#### Step 2: Initialize the Client

Create a new Python file (`my_first_app.py`):

Copy

```
import os
from dotenv import load_dotenv
from opinion_clob_sdk import Client

# Load environment variables
load_dotenv()

# Initialize client
client = Client(
    host='https://proxy.opinion.trade:8443',
    apikey=os.getenv('API_KEY'),
    chain_id=56,  # BNB Chain mainnet
    rpc_url=os.getenv('RPC_URL'),
    private_key=os.getenv('PRIVATE_KEY'),
    multi_sig_addr=os.getenv('MULTI_SIG_ADDRESS'),
    conditional_tokens_addr=os.getenv('CONDITIONAL_TOKEN_ADDR'),
    multisend_addr=os.getenv('0x998739BFdAAdde7C933B942a68053933098f9EDa')
)

print("✓ Client initialized successfully!")
```

#### Step 3: Fetch Market Data

Add market data fetching:

Copy

```
from opinion_clob_sdk.model import TopicStatusFilter

# Get all active markets
markets_response = client.get_markets(
    status=TopicStatusFilter.ACTIVATED,
    page=1,
    limit=10
)

# Parse the response
if markets_response.errno == 0:
    markets = markets_response.result.list
    print(f"\n✓ Found {len(markets)} active markets:")

    for market in markets[:3]:  # Show first 3
        print(f"  - Market #{market.marketId}: {market.marketTitle}")
        print(f"    Status: {market.status}")
        print()
else:
    print(f"Error: {markets_response.errmsg}")
```

#### Step 4: Get Market Details

Copy

```
# Get details for a specific market
market_id = markets[0].topic_id  # Use first market from above

market_detail = client.get_market(market_id)
if market_detail.errno == 0:
    market = market_detail.result.data
    print(f"\n✓ Market Details for #{marketId}:")
    print(f"  Title: {market.marketTitle}")
    print(f"  Condition ID: {market.conditionId}")
    print(f"  Quote Token: {market.quoteToken}")
    print(f"  Chain ID: {market.chainId}")
```

#### Step 5: Check Orderbook

Copy

```
# Assuming the market has a token (get from market.options for binary markets)
# For this example, we'll use a placeholder token_id
token_id = "your_token_id_here"  # Replace with actual token ID

try:
    orderbook = client.get_orderbook(token_id)
    if orderbook.errno == 0:
        book = orderbook.result.data
        print(f"\n✓ Orderbook for token {token_id}:")
        print(f"  Best Bid: {book.bids[0] if book.bids else 'No bids'}")
        print(f"  Best Ask: {book.asks[0] if book.asks else 'No asks'}")
except Exception as e:
    print(f"  (Skip if token_id not set: {e})")
```

#### Complete Example

Here's the complete `my_first_app.py`:

Copy

```
import os
from dotenv import load_dotenv
from opinion_clob_sdk import Client
from opinion_clob_sdk.model import TopicStatusFilter

# Load environment variables
load_dotenv()

def main():
    # Initialize client
    client = Client(
        host='https://proxy.opinion.trade:8443',
        apikey=os.getenv('API_KEY'),
        chain_id=56,
        rpc_url=os.getenv('RPC_URL'),
        private_key=os.getenv('PRIVATE_KEY'),
        multi_sig_addr=os.getenv('MULTI_SIG_ADDRESS')
    )
    print("✓ Client initialized successfully!")

    # Get active markets
    markets_response = client.get_markets(
        status=TopicStatusFilter.ACTIVATED,
        limit=5
    )

    if markets_response.errno == 0:
        markets = markets_response.result.list
        print(f"\n✓ Found {len(markets)} active markets\n")

        # Display markets
        for i, market in enumerate(markets, 1):
            print(f"{i}. {market.marketTitle}")
            print(f"   Market ID: {market.marketId}")
            print()

        # Get details for first market
        if markets:
            first_market = markets[0]
            detail = client.get_market(first_market.marketId)

            if detail.errno == 0:
                m = detail.result.data
                print(f"✓ Details for '{m.markeTitle}':")
                print(f"  Status: {m.status}")
                print(f"  Condition ID: {m.conditionId}")
                print(f"  Quote Token: {m.quoteToken}")
    else:
        print(f"Error fetching markets: {markets_response.errmsg}")

if __name__ == '__main__':
    main()
```

#### Run Your App

Copy

```
# Install python-dotenv if not already installed
pip install python-dotenv

# Run the script
python my_first_app.py
```

**Expected Output:**

Copy

```
✓ Client initialized successfully!

✓ Found 5 active markets

1. Will Bitcoin reach $100k by end of 2025?
   Market ID: 1

2. Will AI surpass human intelligence by 2030?
   Market ID: 2

...

✓ Details for 'Will Bitcoin reach $100k by end of 2025?':
  Status: 2
  Condition ID: 0xabc123...
  Quote Token: 0xdef456...
```

### Next Steps

Now that you've fetched market data, explore more advanced features:

#### Trading

Learn how to place orders:

Copy

```
from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER

# Enable trading (required once before placing orders)
client.enable_trading()

# Place a buy order of "No" token
order_data = PlaceOrderDataInput(
    marketId=813,
    tokenId='33095770954068818933468604332582424490740136703838404213332258128147961949614',
    side=OrderSide.BUY,
    orderType=LIMIT_ORDER,
    price='0.55',
    makerAmountInQuoteToken=10  # 10 USDT
)

result = client.place_order(order_data)
print(f"Order placed: {result}")
```

See Placing Orders for detailed examples.

#### Position Management

Track your positions:

Copy

```
# Get balances
balances = client.get_my_balances()

# Get positions
positions = client.get_my_positions(limit=20)

# Get trade history
trades = client.get_my_trades(market_id=813)
```

See Managing Positions for more.

#### Smart Contract Operations

Interact with blockchain:

Copy

```
# Split USDT into outcome tokens
tx_hash, receipt, event = client.split(
    market_id=813,
    amount=1000000000000000000  # 1 USDT (18 decimals for USDT)
)

# Merge outcome tokens back to USDT
tx_hash, receipt, event = client.merge(
    market_id=813,
    amount=1000000000000000000
)

# Redeem winnings after market resolves
tx_hash, receipt, event = client.redeem(market_id=813)
```

See Contract Operations for details.

### Common Patterns

#### Error Handling

Always check response status:

Copy

```
response = client.get_markets()

if response.errno == 0:
    # Success
    markets = response.result.list
else:
    # Error
    print(f"Error {response.errno}: {response.errmsg}")
```

#### Using Try-Except

Copy

```
from opinion_clob_sdk import InvalidParamError, OpenApiError

try:
    market = client.get_market(market_id=123)
except InvalidParamError as e:
    print(f"Invalid parameter: {e}")
except OpenApiError as e:
    print(f"API error: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

#### Pagination

For large datasets:

Copy

```
page = 1
all_markets = []

while True:
    response = client.get_markets(page=page, limit=100)
    if response.errno != 0:
        break

    markets = response.result.list
    all_markets.extend(markets)

    # Check if more pages exist
    if len(markets) < 100:
        break

    page += 1

print(f"Total markets: {len(all_markets)}")
```

### Configuration Tips

#### Cache Settings

Optimize performance with caching:

Copy

```
client = Client(
    # ... other params ...
    market_cache_ttl=300,        # Cache markets for 5 minutes
    quote_tokens_cache_ttl=3600, # Cache quote tokens for 1 hour
    enable_trading_check_interval=3600  # Check trading status hourly
)
```

Set to `0` to disable caching:

Copy

```
client = Client(
    # ... other params ...
    market_cache_ttl=0  # Disable market caching
)
```

#### Chain Selection

For production deployment, ensure you're using the correct configuration:

Copy

```
client = Client(
    host='https://proxy.opinion.trade:8443',
    chain_id=56,  # BNB Chain mainnet
    rpc_url='https://bsc-dataseed.binance.org',  # BNB Chain RPC
    # ... other params ...
)
```

### Client Configuration

The `Client` class accepts multiple configuration parameters during initialization:

Copy

```
from opinion_clob_sdk import Client

client = Client(
    host='https://proxy.opinion.trade:8443',
    apikey='your_api_key',
    chain_id=56,
    rpc_url='your_rpc_url',
    private_key='0x...',
    multi_sig_addr='0x...',
    conditional_tokens_addr='0xAD1a38cEc043e70E83a3eC30443dB285ED10D774',
    multisend_addr='0x998739BFdAAdde7C933B942a68053933098f9EDa',
    enable_trading_check_interval=3600,
    quote_tokens_cache_ttl=3600,
    market_cache_ttl=300
)
```

### Required Parameters

#### host

**Type**: `str` **Description**: Opinion API host URL **Default**: No default (required)

Copy

```
# Production
host='https://proxy.opinion.trade:8443'
```

#### apikey

**Type**: `str` **Description**: API authentication key provided by Opinion Labs **Default**: No default (required)

**How to obtain**: fill out  this [short application form](https://docs.google.com/forms/d/1h7gp8UffZeXzYQ-lv4jcou9PoRNOqMAQhyW4IwZDnII)

Copy

```
apikey='________'
```

> ⚠️ **Security**: Store API keys in environment variables, never in source code.

#### chain_id

**Type**: `int` **Description**: Blockchain network chain ID **Supported values**:

- `56` - BNB Chain Mainnet (production)

Copy

```
# Mainnet
chain_id=56
```

#### rpc_url

**Type**: `str` **Description**: Blockchain RPC endpoint URL **Default**: No default (required)

**Common providers**:

- **BNB Chain Mainnet**: `https://bsc-dataseed.binance.org`
- **BNB Chain (Nodereal)**: [`https://bsc.nodereal.io`](https://bsc.nodereal.io/)

Copy

```
# Public RPC (rate limited)
rpc_url='https://bsc-dataseed.binance.org'

# Private RPC (recommended for production)
rpc_url='https://bsc.nodereal.io'
```

#### private_key

**Type**: `str` (HexStr) **Description**: Private key for signing orders and transactions **Format**: 64-character hex string (with or without `0x` prefix)

Copy

```
private_key='0x1234567890abcdef...'  # With 0x prefix
# or
private_key='1234567890abcdef...'    # Without 0x prefix
```

> ⚠️ **Critical Security**:
>
> - Never commit private keys to version control
> - Use environment variables or secure key management systems
> - Ensure the associated address has BNB for gas fees
> - This is the **signer** address, may differ from multi_sig_addr

#### multi_sig_addr

**Type**: `str` **Description**: Multi-signature wallet address (your assets/portfolio wallet) **Format**: Ethereum address (checksummed or lowercase)

Copy

```
multi_sig_addr='0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb'
```

**Relationship to private_key**:

- `private_key` → **Signer address** (signs orders/transactions)
- `multi_sig_addr` → **Assets address** (holds funds/positions)
- Can be the same address or different (e.g., hot wallet signs for cold wallet)

**Where to find**:

- Check your Opinion platform "My Profile" section
- Or use the wallet address where you hold USDT/positions

### Optional Parameters

#### conditional_tokens_addr

**Type**: `ChecksumAddress` (str) **Description**: ConditionalTokens contract address **Default**: `0xAD1a38cEc043e70E83a3eC30443dB285ED10D774` (BNB Chain mainnet)

Copy

```
# Default for BNB Chain - no need to specify
client = Client(chain_id=56, ...)

# Custom deployment
conditional_tokens_addr='0xYourConditionalTokensContract...'
```

**When to set**: Only if using a custom deployment

#### multisend_addr

**Type**: `ChecksumAddress` (str) **Description**: Gnosis Safe MultiSend contract address **Default**: `0x998739BFdAAdde7C933B942a68053933098f9EDa` (BNB Chain mainnet)

Copy

```
# Default for BNB Chain - no need to specify
client = Client(chain_id=56, ...)

# Custom deployment
multisend_addr='0xYourMultiSendContract...'
```

**When to set**: Only if using a custom Gnosis Safe deployment

#### enable_trading_check_interval

**Type**: `int` **Description**: Cache duration (in seconds) for trading approval checks **Default**: `3600` (1 hour) **Range**: `0` to `∞`

Copy

```
# Default: check approval status every hour
enable_trading_check_interval=3600

# Check every time (no caching)
enable_trading_check_interval=0

# Check daily
enable_trading_check_interval=86400
```

**Impact**:

- Higher values → Fewer RPC calls → Faster performance
- `0` → Always check → Slower but always current
- Recommended: `3600` (approvals rarely change)

#### quote_tokens_cache_ttl

**Type**: `int` **Description**: Cache duration (in seconds) for quote token data **Default**: `3600` (1 hour) **Range**: `0` to `∞`

Copy

```
# Default: cache for 1 hour
quote_tokens_cache_ttl=3600

# No caching (always fresh)
quote_tokens_cache_ttl=0

# Cache for 6 hours
quote_tokens_cache_ttl=21600
```

**Impact**:

- Quote tokens rarely change
- Higher values improve performance
- Recommended: `3600` or higher

#### market_cache_ttl

**Type**: `int` **Description**: Cache duration (in seconds) for market data **Default**: `300` (5 minutes) **Range**: `0` to `∞`

Copy

```
# Default: cache for 5 minutes
market_cache_ttl=300

# No caching (always fresh)
market_cache_ttl=0

# Cache for 1 hour
market_cache_ttl=3600
```

**Impact**:

- Markets change frequently (prices, status)
- Lower values → More current data
- Recommended: `300` for balance of performance and freshness

### Environment Variables

#### Using .env Files

Create a `.env` file in your project root:

Copy

```
# .env
API_KEY=opn_prod_abc123xyz789
RPC_URL=____
PRIVATE_KEY=0x1234567890abcdef...
MULTI_SIG_ADDRESS=0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb
CHAIN_ID=56
```

Load in your Python code:

Copy

```
import os
from dotenv import load_dotenv
from opinion_clob_sdk import Client

# Load .env file
load_dotenv()

# Use environment variables
client = Client(
    host='https://api.opinion.trade',
    apikey=os.getenv('API_KEY'),
    chain_id=int(os.getenv('CHAIN_ID', 56)),
    rpc_url=os.getenv('RPC_URL'),
    private_key=os.getenv('PRIVATE_KEY'),
    multi_sig_addr=os.getenv('MULTI_SIG_ADDRESS')
)
```

#### Using System Environment Variables

Set in shell:

Copy

```
# Linux/macOS
export API_KEY="opn_prod_abc123xyz789"
export RPC_URL=___
export PRIVATE_KEY="0x..."
export MULTI_SIG_ADDRESS="0x..."

# Windows (Command Prompt)
set API_KEY=opn_prod_abc123xyz789
set RPC_URL=___

# Windows (PowerShell)
$env:API_KEY="opn_prod_abc123xyz789"
$env:RPC_URL=___
```

Then access in Python:

Copy

```
import os
client = Client(
    host='https://proxy.opinion.trade:8443',
    apikey=os.environ['API_KEY'],  # Raises error if not set
    # ... or ...
    apikey=os.getenv('API_KEY', 'default_value'),  # Returns default if not set
    # ...
)
```

### Configuration Patterns

#### Multi-Environment Setup

Manage different environments (dev, staging, prod):

Copy

```
import os
from opinion_clob_sdk import Client

ENVIRONMENTS = {
    'production': {
        'host': 'https://proxy.opinion.trade:8443',
        'chain_id': 56,  # BNB Chain Mainnet
        'rpc_url': 'https://bsc-dataseed.binance.org'
    }
}

def create_client(env='production'):
    config = ENVIRONMENTS[env]

    return Client(
        host=config['host'],
        apikey=os.getenv(f'{env.upper()}_API_KEY'),
        chain_id=config['chain_id'],
        rpc_url=config['rpc_url'],
        private_key=os.getenv(f'{env.upper()}_PRIVATE_KEY'),
        multi_sig_addr=os.getenv(f'{env.upper()}_MULTI_SIG_ADDRESS')
    )

# Usage
dev_client = create_client('development')
prod_client = create_client('production')
```

#### Configuration Class

Organize configuration in a class:

Copy

```
from dataclasses import dataclass
import os
from opinion_clob_sdk import Client

@dataclass
class OpinionConfig:
    api_key: str
    rpc_url: str
    private_key: str
    multi_sig_address: str
    chain_id: int = 56
    host: str = 'https://proxy.opinion.trade:8443'
    market_cache_ttl: int = 300

    @classmethod
    def from_env(cls):
        """Load configuration from environment variables"""
        return cls(
            api_key=os.environ['API_KEY'],
            rpc_url=os.environ['RPC_URL'],
            private_key=os.environ['PRIVATE_KEY'],
            multi_sig_address=os.environ['MULTI_SIG_ADDRESS'],
            chain_id=int(os.getenv('CHAIN_ID', 56))
        )

    def create_client(self):
        """Create Opinion Client from this configuration"""
        return Client(
            host=self.host,
            apikey=self.api_key,
            chain_id=self.chain_id,
            rpc_url=self.rpc_url,
            private_key=self.private_key,
            multi_sig_addr=self.multi_sig_address,
            market_cache_ttl=self.market_cache_ttl
        )

# Usage
config = OpinionConfig.from_env()
client = config.create_client()
```

#### Read-Only Client

For applications that only read data (no trading):

Copy

```
# Minimal configuration for read-only access
client = Client(
    host='https://proxy.opinion.trade:8443',
    apikey=os.getenv('API_KEY'),
    chain_id=56,
    rpc_url='',           # Empty if not doing contract operations
    private_key='0x00',   # Dummy key if not placing orders
    multi_sig_addr='0x0000000000000000000000000000000000000000'
)

# Can use all GET methods
markets = client.get_markets()
market = client.get_market(123)
orderbook = client.get_orderbook('token_123')

# Cannot use trading or contract methods
# client.place_order(...)  # Would fail
# client.split(...)        # Would fail
```

### Performance Tuning

#### High-Frequency Trading

For trading bots with frequent API calls:

Copy

```
client = Client(
    # ... required params ...
    market_cache_ttl=60,           # 1-minute cache for faster updates
    quote_tokens_cache_ttl=3600,   # 1-hour cache (rarely changes)
    enable_trading_check_interval=7200  # 2-hour cache (already approved)
)
```

#### Analytics/Research

For data analysis with less frequent updates:

Copy

```
client = Client(
    # ... required params ...
    market_cache_ttl=1800,         # 30-minute cache
    quote_tokens_cache_ttl=86400,  # 24-hour cache
    enable_trading_check_interval=0  # Not trading
)
```

#### Real-Time Monitoring

For dashboards requiring fresh data:

Copy

```
client = Client(
    # ... required params ...
    market_cache_ttl=0,            # No caching
    quote_tokens_cache_ttl=0,      # No caching
    enable_trading_check_interval=0
)
```

### 

### Smart Contract Addresses

#### BNB Chain Mainnet (Chain ID: 56)

The following smart contract addresses are used by the Opinion CLOB SDK on BNB Chain mainnet:

Contract

Address

Description

**ConditionalTokens**

```
0xAD1a38cEc043e70E83a3eC30443dB285ED10D774
```

ERC1155 conditional tokens contract for outcome tokens

**MultiSend**

```
0x998739BFdAAdde7C933B942a68053933098f9EDa
```

Gnosis Safe MultiSend contract for batch transactions

These addresses are automatically used by the SDK when you specify `chain_id=56`. You only need to provide custom addresses if you're using a custom deployment.



## Data Models

Reference for all data models and enums used in the Opinion CLOB SDK.

### Enums

#### TopicType

Defines the type of prediction market. **Topic** is conceptional equivalent to **Market.**

**Module:** `opinion_clob_sdk.model`

Copy

```
from opinion_clob_sdk.model import TopicType

class TopicType(Enum):
    BINARY = 0        # Two-outcome markets (YES/NO)
    CATEGORICAL = 1   # Multi-outcome markets (Option A/B/C/...)
```

**Usage:**

Copy

```
# Filter for binary markets only
markets = client.get_markets(topic_type=TopicType.BINARY)

# Filter for categorical markets
markets = client.get_markets(topic_type=TopicType.CATEGORICAL)
```

------

#### TopicStatus

Market lifecycle status codes.

**Module:** `opinion_clob_sdk.model`

Copy

```
from opinion_clob_sdk.model import TopicStatus

class TopicStatus(Enum):
    CREATED = 1    # Market created but not yet active
    ACTIVATED = 2  # Market is live and accepting trades
    RESOLVING = 3  # Market ended, awaiting resolution
    RESOLVED = 4   # Market resolved with outcome
```

**Usage:**

Copy

```
market = client.get_market(123)
status = market.result.data.status

if status == TopicStatus.ACTIVATED.value:
    print("Market is live for trading")
elif status == TopicStatus.RESOLVED.value:
    print("Market resolved, can redeem winnings")
```

------

#### TopicStatusFilter

Filter values for querying markets by status.

**Module:** `opinion_clob_sdk.model`

Copy

```
from opinion_clob_sdk.model import TopicStatusFilter

class TopicStatusFilter(Enum):
    ALL = None           # All markets regardless of status
    ACTIVATED = "activated"  # Only active markets
    RESOLVED = "resolved"    # Only resolved markets
```

**Usage:**

Copy

```
# Get only active markets
markets = client.get_markets(status=TopicStatusFilter.ACTIVATED)

# Get only resolved markets
markets = client.get_markets(status=TopicStatusFilter.RESOLVED)

# Get all markets
markets = client.get_markets(status=TopicStatusFilter.ALL)
```

------

#### OrderSide

Trade direction for orders.

**Module:** `opinion_clob_sdk.chain.py_order_utils.model.sides`

Copy

```
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide

class OrderSide(IntEnum):
    BUY = 0   # Buy outcome tokens
    SELL = 1  # Sell outcome tokens
```

**Usage:**

Copy

```
from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput

# Place buy order
buy_order = PlaceOrderDataInput(
    marketId=123,
    tokenId="token_yes",
    side=OrderSide.BUY,  # Buy YES tokens
    # ...
)

# Place sell order
sell_order = PlaceOrderDataInput(
    marketId=123,
    tokenId="token_yes",
    side=OrderSide.SELL,  # Sell YES tokens
    # ...
)
```

------

#### Order Types

Constants for order type selection.

**Module:** `opinion_clob_sdk.chain.py_order_utils.model.order_type`

Copy

```
from opinion_clob_sdk.chain.py_order_utils.model.order_type import (
    MARKET_ORDER,
    LIMIT_ORDER
)

MARKET_ORDER = 1  # Execute immediately at best available price
LIMIT_ORDER = 2   # Execute at specified price or better
```

**Usage:**

Copy

```
from opinion_clob_sdk.chain.py_order_utils.model.order_type import MARKET_ORDER, LIMIT_ORDER

# Market order - executes immediately
market_order = PlaceOrderDataInput(
    orderType=MARKET_ORDER,
    price="0",  # Price ignored for market orders
    # ...
)

# Limit order - waits for specified price
limit_order = PlaceOrderDataInput(
    orderType=LIMIT_ORDER,
    price="0.55",  # Execute at $0.55 or better
    # ...
)
```

------

### Data Classes

#### PlaceOrderDataInput

Input data for placing an order.

**Module:** `opinion_clob_sdk.chain.py_order_utils.model.order`

Copy

```
@dataclass
class PlaceOrderDataInput:
    marketId: int
    tokenId: str
    side: int  # OrderSide.BUY or OrderSide.SELL
    orderType: int  # MARKET_ORDER or LIMIT_ORDER
    price: str
    makerAmountInQuoteToken: str = None  # Amount in USDT (optional)
    makerAmountInBaseToken: str = None   # Amount in YES/NO tokens (optional)
```

**Fields:**

Field

Type

Required

Description

```
marketId
int
```

Yes

Market ID to trade on

```
tokenId
str
```

Yes

Token ID (e.g., "token_yes")

```
side
int
```

Yes

`OrderSide.BUY` (0) or `OrderSide.SELL` (1)

```
orderType
int
```

Yes

`MARKET_ORDER` (1) or `LIMIT_ORDER` (2)

```
price
str
```

Yes

Price as string (e.g., "0.55"), "0" for market orders

```
makerAmountInQuoteToken
str
```

No*

Amount in quote token (e.g., "100" for 100 USDT)

```
makerAmountInBaseToken
str
```

No*

Amount in base token (e.g., "50" for 50 YES tokens)

\* Must provide exactly ONE of `makerAmountInQuoteToken` or `makerAmountInBaseToken`

**Amount Selection Rules:**

**For BUY orders:**

- ✅ `makerAmountInQuoteToken` - Common (specify how much USDT to spend)
- ✅ `makerAmountInBaseToken` - Specify how many tokens to buy
- ❌ Both - Invalid

**For SELL orders:**

- ✅ `makerAmountInBaseToken` - Common (specify how many tokens to sell)
- ✅ `makerAmountInQuoteToken` - Specify how much USDT to receive
- ❌ Both - Invalid

**Examples:**

**Buy 100 USDT worth at $0.55:**

Copy

```
order = PlaceOrderDataInput(
    marketId=123,
    tokenId="token_yes",
    side=OrderSide.BUY,
    orderType=LIMIT_ORDER,
    price="0.55",
    makerAmountInQuoteToken="100"  # Spend 100 USDT
)
```

**Sell 50 YES tokens at market price:**

Copy

```
order = PlaceOrderDataInput(
    marketId=123,
    tokenId="token_yes",
    side=OrderSide.SELL,
    orderType=MARKET_ORDER,
    price="0",
    makerAmountInBaseToken="50"  # Sell 50 tokens
)
```

------

#### OrderData

Internal order data structure (used by OrderBuilder).

**Module:** `opinion_clob_sdk.chain.py_order_utils.model.order`

Copy

```
@dataclass
class OrderData:
    maker: str              # Maker address (multi-sig wallet)
    taker: str              # Taker address (ZERO_ADDRESS for public orders)
    tokenId: str            # Token ID
    makerAmount: str        # Maker amount in wei
    takerAmount: str        # Taker amount in wei
    side: int               # OrderSide
    feeRateBps: str         # Fee rate in basis points
    nonce: str              # Nonce (default "0")
    signer: str             # Signer address
    expiration: str         # Expiration timestamp (default "0" = no expiration)
    signatureType: int      # Signature type (POLY_GNOSIS_SAFE)
```

**Note:** This is an internal structure. Users should use `PlaceOrderDataInput` instead.

------

#### OrderDataInput

Simplified order input (internal use).

**Module:** `opinion_clob_sdk.chain.py_order_utils.model.order`

Copy

```
@dataclass
class OrderDataInput:
    marketId: int
    tokenId: str
    makerAmount: str  # Already calculated amount
    price: str
    side: int
    orderType: int
```

**Note:** This is used internally by `_place_order()`. Users should use `PlaceOrderDataInput`.

------

### Response Models

#### API Response Structure

All API methods return responses with this standard structure:

Copy

```
class APIResponse:
    errno: int        # Error code (0 = success)
    errmsg: str       # Error message
    result: Result    # Result data
```

#### Result Types

**For single objects:**

Copy

```
class Result:
    data: Any  # Single object (market, order, etc.)
```

**For lists/arrays:**

Copy

```
class Result:
    list: List[Any]  # Array of objects
    total: int       # Total count (for pagination)
```

**Example Usage:**

Copy

```
# Single object response
market_response = client.get_market(123)
if market_response.errno == 0:
    market = market_response.result.data  # Access via .data

# List response
markets_response = client.get_markets()
if markets_response.errno == 0:
    markets = markets_response.result.list  # Access via .list
    total = markets_response.result.total
```

------

### Market Data Models

#### Market Object

Returned by `get_market()` and `get_markets()`.

**Key Fields:**

Field

Type

Description

```
marketId
int
```

Market ID

```
marketTitle
str
```

Market question/title

```
status
int
```

Market status (see TopicStatus)

```
marketType
int
```

Market type (0=binary, 1=categorical)

```
conditionId
str
```

Blockchain condition ID (hex string)

```
quoteToken
str
```

Quote token address (e.g., USDT)

```
chainId
str
```

Blockchain chain ID

```
volume
str
```

Trading volume

```
yesTokenId
str
```

Token ID of Yes side

```
noTokenId
str
```

Token ID of No side

```
resultTokenId
str
```

Token ID of Winning side

```
yesLabel
str
```

Token Label of Yes side

```
noLabel
str
```

Token Label of No side

```
rules
str
```

Market Resolution Criteria

```
cutoffAt
int
```

The latest date to resolve the market

```
resolvedAt
int
```

The date that market resolved

**Example:**

Copy

```
market = client.get_market(123).result.data

print(f"ID: {market.topic_id}")
print(f"Title: {market.topic_title}")
print(f"Status: {market.status}")  # 2 = ACTIVATED
print(f"Type: {market.topic_type}")  # 0 = BINARY
print(f"Condition: {market.condition_id}")
```

------

#### Quote Token Object

Returned by `get_quote_tokens()`.

**Key Fields:**

Field

Type

Description

```
quoteTokenAddress
str
```

Token contract address

```
decimal
int
```

Token decimals (e.g., 18 for USDT)

```
ctfExchangeAddress
str
```

CTF exchange contract address

```
chainId
int
```

Blockchain chain ID

```
quoteTokenName
str
```

Token name (e.g., "USDT")

```
symbol
str
```

Token symbol

**Example:**

Copy

```
tokens = client.get_quote_tokens().result.list

for token in tokens:
    print(f"{token.symbol}: {token.quote_token_address}")
    print(f"  Decimals: {token.decimal}")
    print(f"  Exchange: {token.ctf_exchange_address}")
```

------

#### Orderbook Object

Returned by `get_orderbook()`.

**Structure:**

Copy

```
{
    "bids": [  # Buy orders
        {"price": "0.55", "amount": "100", ...},
        {"price": "0.54", "amount": "200", ...},
    ],
    "asks": [  # Sell orders
        {"price": "0.56", "amount": "150", ...},
        {"price": "0.57", "amount": "250", ...},
    ]
}
```

**Example:**

Copy

```
book = client.get_orderbook("token_yes").result.data

# Best bid (highest buy price)
best_bid = book.bids[0] if book.bids else None
print(f"Best bid: ${best_bid['price']} x {best_bid['amount']}")

# Best ask (lowest sell price)
best_ask = book.asks[0] if book.asks else None
print(f"Best ask: ${best_ask['price']} x {best_ask['amount']}")

# Spread
if best_bid and best_ask:
    spread = float(best_ask['price']) - float(best_bid['price'])
    print(f"Spread: ${spread:.4f}")
```

------

### Constants

#### Signature Types

**Module:** `opinion_clob_sdk.chain.py_order_utils.model.signatures`

Copy

```
EOA = 0               # Externally Owned Account (regular wallet)
POLY_PROXY = 1        # Polymarket proxy
POLY_GNOSIS_SAFE = 2  # Gnosis Safe (used by Opinion SDK)
```

**Usage:** Orders are signed with `POLY_GNOSIS_SAFE` signature type by default.

------

#### Address Constants

**Module:** `opinion_clob_sdk.chain.py_order_utils.constants`

Copy

```
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ZX = "0x"  # Hex prefix
```

**Usage:**

- `ZERO_ADDRESS` is used for `taker` field in public orders (anyone can fill)

------

#### Chain IDs

**Module:** `opinion_clob_sdk.sdk`

Copy

```
CHAIN_ID_BNBCHAIN_MAINNET = 56
SUPPORTED_CHAIN_IDS = [56]  # BNB Chain mainnet
```

**Usage:**

Copy

```
# Mainnet
client = Client(chain_id=56, ...)
```

------

#### Decimals

**Module:** `opinion_clob_sdk.sdk`

Copy

```
MAX_DECIMALS = 18  # Maximum token decimals (ERC20 standard)
```

**Common Decimals:**

- USDT: 18 decimals
- BNB: 18 decimals
- Outcome tokens: Usually match quote token decimals

------

### Helper Functions

#### safe_amount_to_wei()

Convert human-readable amount to wei units.

**Module:** `opinion_clob_sdk.sdk`

**Signature:**

Copy

```
def safe_amount_to_wei(amount: float, decimals: int) -> int
```

**Parameters:**

- `amount` - Human-readable amount (e.g., `1.5`)
- `decimals` - Token decimals (e.g., `18` for USDT)

**Returns:** Integer amount in wei units

**Example:**

Copy

```
from opinion_clob_sdk.sdk import safe_amount_to_wei

# Convert 10.5 USDT to wei (18 decimals)
amount_wei = safe_amount_to_wei(10.5, 18)
print(amount_wei)  # 105000000000000000000

# Convert 1 BNB to wei (18 decimals)
amount_wei = safe_amount_to_wei(1.0, 18)
print(amount_wei)  # 100000000000000000000
```

------

#### calculate_order_amounts()

Calculate maker and taker amounts for limit orders.

**Module:** `opinion_clob_sdk.chain.py_order_utils.utils`

**Signature:**

Copy

```
def calculate_order_amounts(
    price: float,
    maker_amount: int,
    side: int,
    decimals: int
) -> Tuple[int, int]
```

**Parameters:**

- `price` - Order price (e.g., `0.55`)
- `maker_amount` - Maker amount in wei
- `side` - `OrderSide.BUY` or `OrderSide.SELL`
- `decimals` - Token decimals

**Returns:** Tuple of `(recalculated_maker_amount, taker_amount)`

**Example:**

Copy

```
from opinion_clob_sdk.chain.py_order_utils.utils import calculate_order_amounts
from opinion_clob_sdk.chain.py_order_utils.model.sides import BUY

maker_amount = 100000000000000000000  # 100 USDT (18 decimals)
price = 0.55
side = BUY
decimals = 18

maker, taker = calculate_order_amounts(price, maker_amount, side, decimals)
print(f"Maker: {maker}, Taker: {taker}")
```

------

# Methods

Complete reference for all methods available in the `Client` class.

### Overview

The `Client` class provides a unified interface for interacting with OPINION prediction markets. Methods are organized into these categories:

- **Market Data** - Query markets, prices, and orderbooks
- **Trading Operations** - Place and manage orders
- **User Data** - Access balances, positions, and trades
- **Smart Contract Operations** - Blockchain interactions (split, merge, redeem)

### Response Format

All API methods return responses with this structure:

Copy

```
response = client.get_markets()

# Check success
if response.errno == 0:
    # Success - access data
    data = response.result.data  # For single objects
    # or
    items = response.result.list  # For arrays
else:
    # Error - check error message
    print(f"Error {response.errno}: {response.errmsg}")
```

**Response fields:**

- `errno` - Error code (`0` = success, non-zero = error)
- `errmsg` - Error message string
- `result` - Contains `data` (single object) or `list` (array of objects)

### Market Data Methods

#### get_markets()

Get a paginated list of prediction markets.

**Signature:**

Copy

```
def get_markets(
    topic_type: Optional[TopicType] = None,
    page: int = 1,
    limit: int = 20,
    status: Optional[TopicStatusFilter] = None
) -> Any
```

**Parameters:**

Name

Type

Required

Default

Description

```
topic_type
TopicType
```

No

```
None
```

Filter by market type (`TopicType.BINARY` or `TopicType.CATEGORICAL`)

```
page
int
```

No

```
1
```

Page number (≥ 1)

```
limit
int
```

No

```
20
```

Items per page (1-20)

```
status
TopicStatusFilter
```

No

```
None
```

Filter by status (`ACTIVATED`, `RESOLVED`, or `ALL`)

**Returns:** API response with `result.list` containing market objects

**Example:**

Copy

```
from opinion_clob_sdk.model import TopicType, TopicStatusFilter

# Get all active binary markets
response = client.get_markets(
    topic_type=TopicType.BINARY,
    status=TopicStatusFilter.ACTIVATED,
    page=1,
    limit=10
)

if response.errno == 0:
    markets = response.result.list
    for market in markets:
        print(f"{market.market_id}: {market.market_title}")
```

**Raises:**

- `InvalidParamError` - If page < 1 or limit not in range [1, 20]

------

#### get_market()

Get detailed information about a specific market.

**Signature:**

Copy

```
def get_market(market_id: int, use_cache: bool = True) -> Any
```

**Parameters:**

Name

Type

Required

Default

Description

```
market_id
int
```

Yes

\-

Market ID to query

```
use_cache
bool
```

No

```
True
```

Whether to use cached data if available

**Returns:** API response with `result.data` containing market details

**Example:**

Copy

```
response = client.get_market(market_id=123, use_cache=True)

if response.errno == 0:
    market = response.result.data
    print(f"Title: {market.market_title}")
    print(f"Status: {market.status}")
    print(f"Condition ID: {market.condition_id}")
    print(f"Quote Token: {market.quote_token}")
```

**Caching:**

- Cache duration controlled by `market_cache_ttl` (default: 300 seconds)
- Set `use_cache=False` to force fresh data
- Set `market_cache_ttl=0` in Client constructor to disable caching

**Raises:**

- `InvalidParamError` - If market_id is missing or invalid
- `OpenApiError` - If API request fails

------

#### get_categorical_market()

Get detailed information about a categorical market (multi-outcome).

**Signature:**

Copy

```
def get_categorical_market(market_id: int) -> Any
```

**Parameters:**

Name

Type

Required

Description

```
market_id
int
```

Yes

Categorical market ID

**Returns:** API response with categorical market data

**Example:**

Copy

```
response = client.get_categorical_market(market_id=456)

if response.errno == 0:
    market = response.result.data
    print(f"Options: {market.options}")  # Multiple outcomes
```

------

#### get_quote_tokens()

Get list of supported quote tokens (collateral currencies).

**Signature:**

Copy

```
def get_quote_tokens(use_cache: bool = True) -> Any
```

**Parameters:**

Name

Type

Required

Default

Description

```
use_cache
bool
```

No

```
True
```

Whether to use cached data

**Returns:** API response with `result.list` containing quote token objects

**Example:**

Copy

```
response = client.get_quote_tokens()

if response.errno == 0:
    tokens = response.result.list
    for token in tokens:
        print(f"Token: {token.quote_token_address}")
        print(f"Decimals: {token.decimal}")
        print(f"Exchange: {token.ctf_exchange_address}")
```

**Caching:**

- Default TTL: 3600 seconds (1 hour)
- Controlled by `quote_tokens_cache_ttl` parameter

------

#### get_orderbook()

Get orderbook (bids and asks) for a specific token.

**Signature:**

Copy

```
def get_orderbook(token_id: str) -> Any
```

**Parameters:**

Name

Type

Required

Description

```
token_id
str
```

Yes

Token ID (e.g., "token_yes", "token_123")

**Returns:** API response with orderbook data

**Example:**

Copy

```
response = client.get_orderbook(token_id="token_yes")

if response.errno == 0:
    book = response.result.data
    print("Bids (buy orders):")
    for bid in book.bids[:5]:  # Top 5
        print(f"  Price: {bid.price}, Size: {bid.size}")

    print("Asks (sell orders):")
    for ask in book.asks[:5]:
        print(f"  Price: {ask.price}, Size: {ask.size}")
```

**Raises:**

- `InvalidParamError` - If token_id is missing
- `OpenApiError` - If API request fails

------

#### get_latest_price()

Get the current/latest price for a token.

**Signature:**

Copy

```
def get_latest_price(token_id: str) -> Any
```

**Parameters:**

Name

Type

Required

Description

```
token_id
str
```

Yes

Token ID

**Returns:** API response with latest price data

**Example:**

Copy

```
response = client.get_latest_price(token_id="token_yes")

if response.errno == 0:
    price_data = response.result.data
    print(f"Latest price: {price_data.price}")
    print(f"Timestamp: {price_data.timestamp}")
```

------

#### get_price_history()

Get historical price data (candlestick/OHLCV) for a token.

**Signature:**

Copy

```
def get_price_history(
    token_id: str,
    interval: str = "1h",
    start_at: Optional[int] = None,
    end_at: Optional[int] = None
) -> Any
```

**Parameters:**

Name

Type

Required

Default

Description

```
token_id
str
```

Yes

\-

Token ID

```
interval
str
```

No

```
"1h"
```

Time interval: `1m`, `1h`, `1d`, `1w`, `max`

```
start_at
int
```

No

```
None
```

Start timestamp (Unix seconds)

```
end_at
int
```

No

```
None
```

End timestamp (Unix seconds)

**Returns:** API response with price history data

**Example:**

Copy

```
import time

# Get last 24 hours of hourly data
end_time = int(time.time())
start_time = end_time - (24 * 3600)  # 24 hours ago

response = client.get_price_history(
    token_id="token_yes",
    interval="1h",
    start_at=start_time,
    end_at=end_time
)

if response.errno == 0:
    candles = response.result.data
    for candle in candles:
        print(f"Time: {candle.timestamp}, Price: {candle.close}")
```

------

#### get_fee_rates()

Get trading fee rates for a token.

**Signature:**

Copy

```
def get_fee_rates(token_id: str) -> Any
```

**Parameters:**

Name

Type

Required

Description

```
token_id
str
```

Yes

Token ID

**Returns:** API response with fee rate data

**Example:**

Copy

```
response = client.get_fee_rates(token_id="token_yes")

if response.errno == 0:
    fees = response.result.data
    print(f"Maker fee: {fees.maker_fee}")
    print(f"Taker fee: {fees.taker_fee}")
```

------

### Trading Operations

#### place_order()

Place a market or limit order.

**Signature:**

Copy

```
def place_order(
    data: PlaceOrderDataInput,
    check_approval: bool = False
) -> Any
```

**Parameters:**

Name

Type

Required

Description

```
data
PlaceOrderDataInput
```

Yes

Order parameters (see below)

```
check_approval
bool
```

No

Whether to check and enable trading approvals first

**PlaceOrderDataInput fields:**

Field

Type

Required

Description

```
marketId
int
```

Yes

Market ID

```
tokenId
str
```

Yes

Token ID to trade

```
side
OrderSide
```

Yes

```
OrderSide.BUY` or `OrderSide.SELL
orderType
int
```

Yes

`MARKET_ORDER` (1) or `LIMIT_ORDER` (2)

```
price
str
```

Yes*

Price string (required for limit orders, `"0"` for market)

```
makerAmountInQuoteToken
int` or `float
```

No**

Amount in quote token (e.g., 100 for 100 USDT)

```
makerAmountInBaseToken
int` or `float
```

No**

Amount in base token (e.g., 50 for 50 YES tokens)

\* Price is required for limit orders, set to `"0"` for market orders ** Must provide exactly ONE of `makerAmountInQuoteToken` or `makerAmountInBaseToken`

**Returns:** API response with order result

**Examples:**

**Limit Buy Order (using quote token):**

Copy

```
from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER

order = PlaceOrderDataInput(
    marketId=123,
    tokenId="token_yes",
    side=OrderSide.BUY,
    orderType=LIMIT_ORDER,
    price="0.55",  # Buy at $0.55 or better
    makerAmountInQuoteToken=100  # Spend 100 USDT (int or float)
)

result = client.place_order(order, check_approval=True)
if result.errno == 0:
    print(f"Order placed: {result.result.data.order_id}")
```

**Market Sell Order (using base token):**

Copy

```
from opinion_clob_sdk.chain.py_order_utils.model.order_type import MARKET_ORDER

order = PlaceOrderDataInput(
    marketId=123,
    tokenId="token_yes",
    side=OrderSide.SELL,
    orderType=MARKET_ORDER,
    price="0",  # Market orders don't need price
    makerAmountInBaseToken=50  # Sell 50 YES tokens (int or float)
)

result = client.place_order(order)
```

**Raises:**

- `InvalidParamError` - If parameters are invalid or missing
- `OpenApiError` - If API request fails or chain_id mismatch

------

#### place_orders_batch()

Place multiple orders in a single batch operation.

**Signature:**

Copy

```
def place_orders_batch(
    orders: List[PlaceOrderDataInput],
    check_approval: bool = False
) -> List[Any]
```

**Parameters:**

Name

Type

Required

Description

```
orders
List[PlaceOrderDataInput]
```

Yes

A list containing the order details.

```
check_approval
bool
```

No

Determines if approvals are verified for all orders.

**Returns:** List of results with `success`, `result`, and `error` fields for each order

**Example:**

Copy

```
orders = [
    PlaceOrderDataInput(marketId=123, tokenId="token_yes", side=OrderSide.BUY, ...),
    PlaceOrderDataInput(marketId=124, tokenId="token_no", side=OrderSide.SELL, ...),
]

results = client.place_orders_batch(orders, check_approval=True)

for i, result in enumerate(results):
    if result['success']:
        print(f"Order {i}: Success - {result['result']}")
    else:
        print(f"Order {i}: Failed - {result['error']}")
```

------

#### cancel_order()

Cancel a single order by order ID.

**Signature:**

Copy

```
def cancel_order(order_id: str) -> Any
```

**Parameters:**

Name

Type

Required

Description

```
order_id
str
```

Yes

Order ID to cancel

**Returns:** API response for cancellation

**Example:**

Copy

```
result = client.cancel_order(order_id="order_123")

if result.errno == 0:
    print("Order cancelled successfully")
```

------

#### cancel_orders_batch()

Cancel multiple orders in a batch.

**Signature:**

Copy

```
def cancel_orders_batch(order_ids: List[str]) -> List[Any]
```

**Parameters:**

Name

Type

Required

Description

```
order_ids
List[str]
```

Yes

List of order IDs to cancel

**Returns:** List of cancellation results for each order

**Example:**

Copy

```
order_ids = ["order_123", "order_456", "order_789"]
results = client.cancel_orders_batch(order_ids)

for i, result in enumerate(results):
    if result['success']:
        print(f"Cancelled: {order_ids[i]}")
    else:
        print(f"Failed: {order_ids[i]} - {result['error']}")
```

------

#### cancel_all_orders()

Cancel all open orders, optionally filtered by market and/or side.

**Signature:**

Copy

```
def cancel_all_orders(
    market_id: Optional[int] = None,
    side: Optional[OrderSide] = None
) -> Dict[str, Any]
```

**Parameters:**

Name

Type

Required

Description

```
market_id
int
```

No

Filter by market ID (all markets if None)

```
side
OrderSide
```

No

Filter by side (BUY/SELL, all sides if None)

**Returns:** Dictionary with cancellation summary:

Copy

```
{
    'total_orders': int,      # Total orders found matching filter
    'cancelled': int,         # Successfully cancelled count
    'failed': int,            # Failed cancellation count
    'results': List[dict]     # Detailed results for each order
}
```

**Example:**

Copy

```
# Cancel all open orders across all markets
result = client.cancel_all_orders()
print(f"Cancelled {result['cancelled']} out of {result['total_orders']} orders")

# Cancel all BUY orders in market 123
result = client.cancel_all_orders(market_id=123, side=OrderSide.BUY)
print(f"Success: {result['cancelled']}, Failed: {result['failed']}")

# Cancel all orders in market 456 (both sides)
result = client.cancel_all_orders(market_id=456)
```

------

#### get_my_orders()

Get user's orders with optional filters.

**Signature:**

Copy

```
def get_my_orders(
    market_id: int = 0,
    status: str = "",
    limit: int = 10,
    page: int = 1
) -> Any
```

**Parameters:**

Name

Type

Required

Default

Description

```
market_id
int
```

No

```
0
```

Filter by market (0 = all markets)

```
status
str
```

No

```
""
```

Filter by status (e.g., "open", "filled", "cancelled")

```
limit
int
```

No

```
10
```

Items per page

```
page
int
```

No

```
1
```

Page number

**Returns:** API response with `result.list` containing orders

**Example:**

Copy

```
# Get all open orders
response = client.get_my_orders(status="open", limit=50)

if response.errno == 0:
    orders = response.result.list
    for order in orders:
        print(f"Order {order.order_id}: {order.side} @ {order.price}")
```

------

#### get_order_by_id()

Get details for a specific order by ID.

**Signature:**

Copy

```
def get_order_by_id(order_id: str) -> Any
```

**Parameters:**

Name

Type

Required

Description

```
order_id
str
```

Yes

Order ID

**Returns:** API response with order details

**Example:**

Copy

```
response = client.get_order_by_id(order_id="order_123")

if response.errno == 0:
    order = response.result.data
    print(f"Status: {order.status}")
    print(f"Filled: {order.filled_amount}/{order.maker_amount}")
```

------

### User Data Methods

#### get_my_balances()

Get user's token balances.

**Signature:**

Copy

```
def get_my_balances() -> Any
```

**Returns:** API response with `result.data.balances` containing list of balance objects

**Example:**

Copy

```
response = client.get_my_balances()

if response.errno == 0:
    balance_data = response.result.data
    balances = balance_data.balances  # List of quote token balances
    for balance in balances:
        print(f"Token: {balance.quote_token}")
        print(f"  Available: {balance.available_balance}")
        print(f"  Frozen: {balance.frozen_balance}")
        print(f"  Total: {balance.total_balance}")
```

------

#### get_my_positions()

Get user's open positions across markets.

**Signature:**

Copy

```
def get_my_positions(
    market_id: int = 0,
    page: int = 1,
    limit: int = 10
) -> Any
```

**Parameters:**

Name

Type

Required

Default

Description

```
market_id
int
```

No

```
0
```

Filter by market (0 = all)

```
page
int
```

No

```
1
```

Page number

```
limit
int
```

No

```
10
```

Items per page

**Returns:** API response with `result.list` containing positions

**Example:**

Copy

```
response = client.get_my_positions(limit=50)

if response.errno == 0:
    positions = response.result.list
    for pos in positions:
        print(f"Market {pos.market_id}: {pos.market_title}")
        print(f"  Shares: {pos.shares_owned} ({pos.outcome_side_enum})")
        print(f"  Value: {pos.current_value_in_quote_token}")
        print(f"  P&L: {pos.unrealized_pnl} ({pos.unrealized_pnl_percent}%)")
```

------

#### get_my_trades()

Get user's trade history.

**Signature:**

Copy

```
def get_my_trades(
    market_id: Optional[int] = None,
    page: int = 1,
    limit: int = 10
) -> Any
```

**Parameters:**

Name

Type

Required

Default

Description

```
market_id
int
```

No

```
None
```

Filter by market

```
page
int
```

No

```
1
```

Page number

```
limit
int
```

No

```
10
```

Items per page

**Returns:** API response with `result.list` containing trade history

**Example:**

Copy

```
response = client.get_my_trades(market_id=123, limit=20)

if response.errno == 0:
    trades = response.result.list
    for trade in trades:
        print(f"{trade.created_at}: {trade.side} {trade.shares} shares @ {trade.price}")
        print(f"  Amount: {trade.amount}, Fee: {trade.fee}")
        print(f"  Status: {trade.status_enum}")
```

------



### Smart Contract Operations

These methods interact directly with the blockchain and **require gas (BNB)**.

#### enable_trading()

Enable trading by approving quote tokens for the exchange contract. Must be called once before placing orders or doing split/merge/redeem operations.

**Signature:**

Copy

```
def enable_trading() -> Tuple[Any, Any, Any]
```

**Returns:** Tuple of `(tx_hash, tx_receipt, contract_event)`

**Example:**

Copy

```
tx_hash, receipt, event = client.enable_trading()
print(f"Trading enabled! TX: {tx_hash.hex()}")
```

**Notes:**

- Only needs to be called once (result is cached for `enable_trading_check_interval` seconds)
- Automatically called by `split()`, `merge()`, `redeem()` if `check_approval=True`

------

#### split()

Convert collateral tokens (e.g., USDT) into outcome tokens (e.g., YES + NO).

**Signature:**

Copy

```
def split(
    market_id: int,
    amount: int,
    check_approval: bool = True
) -> Tuple[Any, Any, Any]
```

**Parameters:**

Name

Type

Required

Description

```
market_id
int
```

Yes

Market ID

```
amount
int
```

Yes

Amount in wei (e.g., 105000000000000000000 for 1 USDT with 18 decimals)

```
check_approval
bool
```

No

Auto-call `enable_trading()` if needed

**Returns:** Tuple of `(tx_hash, tx_receipt, contract_event)`

**Example:**

Copy

```
# Split 10 USDT (18 decimals) into YES + NO tokens
amount_wei = 10 * 10**18  # 10 USDT

tx_hash, receipt, event = client.split(
    market_id=123,
    amount=amount_wei,
    check_approval=True
)

print(f"Split complete! TX: {tx_hash.hex()}")
print(f"Gas used: {receipt.gasUsed}")
```

**Raises:**

- `InvalidParamError` - If market_id or amount is invalid
- `OpenApiError` - If market is not in valid state or chain mismatch
- Blockchain errors - If insufficient balance or gas

------

#### merge()

Convert outcome tokens back into collateral tokens.

**Signature:**

Copy

```
def merge(
    market_id: int,
    amount: int,
    check_approval: bool = True
) -> Tuple[Any, Any, Any]
```

**Parameters:**

Name

Type

Required

Description

```
market_id
int
```

Yes

Market ID

```
amount
int
```

Yes

Amount of outcome tokens in wei

```
check_approval
bool
```

No

Auto-call `enable_trading()` if needed

**Returns:** Tuple of `(tx_hash, tx_receipt, contract_event)`

**Example:**

Copy

```
# Merge 5 YES + 5 NO tokens back to 5 USDT
amount_wei = 5 * 10**18

tx_hash, receipt, event = client.merge(
    market_id=123,
    amount=amount_wei
)

print(f"Merge complete! TX: {tx_hash.hex()}")
```

------

#### redeem()

Claim winnings after a market is resolved. Redeems winning outcome tokens for collateral.

**Signature:**

Copy

```
def redeem(
    market_id: int,
    check_approval: bool = True
) -> Tuple[Any, Any, Any]
```

**Parameters:**

Name

Type

Required

Description

```
market_id
int
```

Yes

Resolved market ID

```
check_approval
bool
```

No

Auto-call `enable_trading()` if needed

**Returns:** Tuple of `(tx_hash, tx_receipt, contract_event)`

**Example:**

Copy

```
# Redeem winnings from resolved market
tx_hash, receipt, event = client.redeem(market_id=123)

print(f"Winnings redeemed! TX: {tx_hash.hex()}")
```

**Raises:**

- `InvalidParamError` - If market_id is invalid
- `OpenApiError` - If market is not resolved or chain mismatch
- `NoPositionsToRedeem` - If no winning positions to claim

------

### Error Handling

#### Exceptions

The SDK defines these custom exceptions:

Copy

```
from opinion_clob_sdk import InvalidParamError, OpenApiError
from opinion_clob_sdk.chain.exception import (
    BalanceNotEnough,
    NoPositionsToRedeem,
    InsufficientGasBalance
)
```

Exception

Description

```
InvalidParamError
```

Invalid method parameters

```
OpenApiError
```

API communication or response errors

```
BalanceNotEnough
```

Insufficient token balance for operation

```
NoPositionsToRedeem
```

No winning positions to redeem

```
InsufficientGasBalance
```

Not enough BNB for gas fees

#### Example Error Handling

Copy

```
try:
    result = client.place_order(order_data)
    if result.errno == 0:
        print("Success!")
    else:
        print(f"API Error: {result.errmsg}")

except InvalidParamError as e:
    print(f"Invalid parameter: {e}")
except OpenApiError as e:
    print(f"API error: {e}")
except BalanceNotEnough as e:
    print(f"Insufficient balance: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

# FAQ

Common questions and answers about the Opinion CLOB SDK.

### Installation & Setup

#### Q: What Python versions are supported?

**A:** Python 3.8 and higher. The SDK is tested on Python 3.8 through 3.13.

Copy

```
python --version  # Must be 3.8+
```

------

#### Q: How do I install the SDK?

**A:** Use pip:

Copy

```
pip install opinion_clob_sdk
```

See Installation Guide for details.

------

#### Q: Where do I get API credentials?

**A:** You need:

1. **API Key** - Fill out this [short application form ](https://docs.google.com/forms/d/1h7gp8UffZeXzYQ-lv4jcou9PoRNOqMAQhyW4IwZDnII)
2. **Private Key** - From your EVM wallet (e.g., MetaMask)
3. **Multi-sig Address** - Your wallet address (visible in "MyProfile")
4. **RPC URL** - Get from Nodereal, Alchemy, drpc etc..

Never share your private key or API key!

------

#### Q: What's the difference between `private_key` and `multi_sig_addr`?

**A:**

- `**private_key**`: The **signer** wallet that signs orders/transactions (hot wallet)
- `**multi_sig_addr**`: The **assets** wallet that holds funds/positions (can be cold wallet)

They can be the same address, or different for security (hot wallet signs for cold wallet).

**Example:**

Copy

```
client = Client(
    private_key='0x...',      # Hot wallet private key (signs orders)
    multi_sig_addr='0x...'    # Cold wallet address (holds assets)
)
```

------

### Configuration

#### Q: Which chain IDs are supported?

**A:** Only BNB blockchain:

- **BNB Mainnet**: `chain_id=56` (production)

Copy

```
# Mainnet
client = Client(chain_id=56, ...)
```

------

#### Q: How do I configure caching?

**A:** Use these parameters when creating the Client:

Copy

```
client = Client(
    # ... other params ...
    market_cache_ttl=300,        # Cache markets for 5 minutes (default)
    quote_tokens_cache_ttl=3600, # Cache tokens for 1 hour (default)
    enable_trading_check_interval=3600  # Cache approval checks for 1 hour
)
```

Set to `0` to disable caching:

Copy

```
client = Client(
    # ...
    market_cache_ttl=0  # Always fetch fresh data
)
```

------



### Trading

#### Q: What's the difference between market and limit orders?

**A:**

Feature

Market Order

Limit Order

**Execution**

Immediate

When price reached

**Price**

Best available

Your specified price or better

**Guarantee**

Fills immediately*

May not fill

**Price field**

Set to "0"

Set to desired price (e.g., "0.55")

\* If sufficient liquidity exists

**Examples:**

Copy

```
# Market order - executes now at best price
market = PlaceOrderDataInput(
    orderType=MARKET_ORDER,
    price="0",
    makerAmountInQuoteToken="100"
)

# Limit order - waits for price $0.55 or better
limit = PlaceOrderDataInput(
    orderType=LIMIT_ORDER,
    price="0.55",
    makerAmountInQuoteToken="100"
)
```

------

#### Q: Should I use `makerAmountInQuoteToken` or `makerAmountInBaseToken`?

**A:** Depends on order side:

**For BUY orders:**

- ✅ **Recommended**: `makerAmountInQuoteToken` (specify how much USDT to spend)
- Alternative: `makerAmountInBaseToken` (specify how many tokens to buy)

**For SELL orders:**

- ✅ **Recommended**: `makerAmountInBaseToken` (specify how many tokens to sell)
- Alternative: `makerAmountInQuoteToken` (specify how much USDT to receive)

**Rules:**

- ❌ Cannot specify both
- ❌ Market BUY cannot use `makerAmountInBaseToken`
- ❌ Market SELL cannot use `makerAmountInQuoteToken`

------

#### Q: Do I need to call `enable_trading()` before every order?

**A:** No, only once! The SDK caches the result for `enable_trading_check_interval` seconds (default 1 hour).

**Option 1: Manual (recommended for multiple orders)**

Copy

```
client.enable_trading()  # Call once

# Place many orders without checking again
client.place_order(order1)
client.place_order(order2)
client.place_order(order3)
```

**Option 2: Automatic (convenient for single orders)**

Copy

```
# Automatically checks and enables if needed
client.place_order(order, check_approval=True)
```

------

#### Q: How do I cancel all my open orders?

**A:** Use `cancel_all_orders()`:

Copy

```
# Cancel all orders across all markets
result = client.cancel_all_orders()
print(f"Cancelled {result['cancelled_count']} orders")

# Cancel only orders in a specific market
result = client.cancel_all_orders(market_id=123)

# Cancel only BUY orders in a market
result = client.cancel_all_orders(market_id=123, side=OrderSide.BUY)
```

------

### Smart Contracts

#### Q: What's the difference between split, merge, and redeem?

**A:**

Operation

Purpose

When to Use

Gas Required

**split**

USDT → YES + NO tokens

Before trading (create positions)

✅ Yes

**merge**

YES + NO → USDT

Exit position on unresolved market

✅ Yes

**redeem**

Winning tokens → USDT

Claim winnings after resolution

✅ Yes

**Examples:**

Copy

```
# 1. Split 10 USDT into 10 YES + 10 NO tokens
client.split(market_id=123, amount=10_000000)  # 6 decimals for USDT

# 2. Trade tokens (no gas, signed orders)
client.place_order(...)  # Sell some YES tokens

# 3a. Market still open: Merge remaining tokens back to USDT
client.merge(market_id=123, amount=5_000000)  # Merge 5 YES + 5 NO → 5 USDT

# 3b. Market resolved: Redeem winning tokens
client.redeem(market_id=123)  # Convert winning tokens → USDT
```

------

#### Q: Why do I need BNB if orders are gas-free?

**A:** BNB is needed for **blockchain operations**:

**Gas-free (signed orders):**

- ✅ `place_order()` - No BNB needed
- ✅ `cancel_order()` - No BNB needed
- ✅ All GET methods - No BNB needed

**Requires** BNB**:**

- ⛽ `enable_trading()` - On-chain approval
- ⛽ `split()` - On-chain transaction
- ⛽ `merge()` - On-chain transaction
- ⛽ `redeem()` - On-chain transaction

**How much** BNB**?** Usually $0.005-0.05 per transaction on BNB Chain.

------

#### Q: Can I split without calling `enable_trading()`?

**A:** Yes, but it will fail without approval. Use `check_approval=True`:

Copy

```
# Option 1: Enable first (manual)
client.enable_trading()
client.split(market_id=123, amount=1000000, check_approval=False)

# Option 2: Auto-enable (recommended)
client.split(market_id=123, amount=1000000, check_approval=True)
```

The same applies to `merge()` and `redeem()`.

------

### Errors

#### Q: What does `InvalidParamError` mean?

**A:** Your method parameters are invalid. Common causes:

Copy

```
# ✗ Price = 0 for limit order
order = PlaceOrderDataInput(orderType=LIMIT_ORDER, price="0", ...)
# Error: Price must be positive for limit orders

# ✗ Amount below minimum
order = PlaceOrderDataInput(makerAmountInQuoteToken="0.5", ...)
# Error: makerAmountInQuoteToken must be at least 1

# ✗ Wrong amount field for market buy
order = PlaceOrderDataInput(
    side=OrderSide.BUY,
    orderType=MARKET_ORDER,
    makerAmountInBaseToken="100"  # Should use makerAmountInQuoteToken
)
# Error: makerAmountInBaseToken is not allowed for market buy

# ✗ Page < 1
markets = client.get_markets(page=0)
# Error: page must be >= 1
```

------

#### Q: What does `OpenApiError` mean?

**A:** API communication or business logic error. Common causes:

Copy

```
# Chain ID mismatch
# Your client is on chain 8453 but market is on chain 56
client.place_order(order)  # Error: Cannot place order on different chain

# Market not active
client.split(market_id=999)  # Error: Cannot split on non-activated market

# Quote token not found
# Token not supported for your chain
```

Check:

1. `response.errno != 0` → API returned error
2. `response.errmsg` → Error message
3. Chain ID matches between client and market

------

#### Q: What does `errno != 0` mean in responses?

**A:** The API returned an error.

**Success:**

Copy

```
response = client.get_markets()
if response.errno == 0:
    # Success! Access data
    markets = response.result.list
```

**Error:**

Copy

```
response = client.get_market(99999)  # Non-existent market
if response.errno != 0:
    # Error occurred
    print(f"Error {response.errno}: {response.errmsg}")
    # Example: "Error 404: Market not found"
```

**Always check** `**errno**` **before accessing** `**result**`**.**

------

### Performance

#### Q: Why are my API calls slow?

**A:** Possible reasons:

1. **No caching** - Enable caching for better performance:

   Copy

   ```
   client = Client(
       market_cache_ttl=300,        # 5 minutes
       quote_tokens_cache_ttl=3600  # 1 hour
   )
   ```

2. **Slow RPC** - Use a faster provider:

   Copy

   ```
   # Slow: Public RPC
   rpc_url='https://some.slow.rpc.io'
   
   # Fast: Private RPC (Nodereal, dRPC)
   rpc_url='https://bsc.nodereal.io'
   ```

3. **Too many calls** - Use batch operations:

   Copy

   ```
   # Slow: One at a time
   for order in orders:
       client.place_order(order)
   
   # Fast: Batch
   client.place_orders_batch(orders)
   ```

------

#### Q: How do I reduce API calls?

**A:** Use caching and batch operations:

**Caching:**

Copy

```
# Enable caching
client = Client(market_cache_ttl=300, ...)

# First call: Fetches from API
market = client.get_market(123)

# Second call within 5 minutes: Returns cached data
market = client.get_market(123, use_cache=True)  # Fast!

# Force fresh data
market = client.get_market(123, use_cache=False)
```

**Batch operations:**

Copy

```
# Place multiple orders
results = client.place_orders_batch(orders)

# Cancel multiple orders
results = client.cancel_orders_batch(order_ids)
```

------

### Data & Precision

#### Q: How do I convert USDT amount to wei?

**A:** Use `safe_amount_to_wei()`:

Copy

```
from opinion_clob_sdk.sdk import safe_amount_to_wei

# USDT has 18 decimals
amount_wei = safe_amount_to_wei(10.5, 18)
print(amount_wei) # 105000000000000000000

# Use in split
client.split(market_id=123, amount=amount_wei)
```

**Common decimals:**

- USDT: 18 decimals
- BNB: 18 decimals
- Outcome tokens: Same as quote token

------

#### Q: How are prices formatted?

**A:** Prices are strings with up to 2 decimal places:

Copy

```
# Valid prices
"0.5"    # ✓ 50 cents
"0.55"   # ✓ 55 cents
"0.555"   # ✓ 55.5 cents 
"1"      # ✓ $1.00
"1.00"   # ✓ $1.00

# Invalid
"0.5555"  # ✗ Too many decimals
0.5      # ✗ Must be string
```

------

#### Q: What token amounts are in the API responses?

**A:** Amounts are in **wei units** (smallest unit).

**Example:**

Copy

```
balance = client.get_my_balances().result.list[0]
print(balance.amount)  # e.g., "105000000000000000000" (not "10.5")

# Convert to human-readable
decimals = 18  # USDT decimals
amount_usdt = int(balance.amount) / (10 ** decimals)
print(f"{amount_usdt} USDT")  # "10.5 USDT"
```

------



### Troubleshooting

#### Q: "ModuleNotFoundError: No module named 'opinion_clob_sdk'"

**A:** SDK not installed. Install it:

Copy

```
pip install opinion_clob_sdk

# Verify installation
python -c "import opinion_clob_sdk; print(opinion_clob_sdk.__version__)"
```

------

#### Q: "InvalidParamError: chain_id must be one of [56]"

**A:** You're using an unsupported chain ID. Use BNB mainnet:

Copy

```
# ✓ BNB Chain Mainnet
client = Client(chain_id=56, ...)

# ✗ Unsupported
client = Client(chain_id=1, ...)  # Ethereum mainnet not supported
```

------

#### Q: "OpenApiError: Cannot place order on different chain"

**A:** Your client and market are on different chains.

**Fix:** Ensure client chain_id matches market chain_id:

Copy

```
# Check market's chain
market = client.get_market(123).result.data
print(f"Market chain: {market.chain_id}")

# Ensure client matches
client = Client(chain_id=int(market.chain_id), ...)
```

------

#### Q: "BalanceNotEnough" error when calling split/merge

**A:** Insufficient token balance.

**For split:** Need enough USDT

Copy

```
# Check balance first
balances = client.get_my_balances().result.list
usdt_balance = next(b for b in balances if b.token.lower() == 'usdt')
print(f"USDT balance: {usdt_balance.amount}")

# Ensure you have enough
amount_to_split = 10_000000  # 10 USDT
if int(usdt_balance.amount) >= amount_to_split:
    client.split(market_id=123, amount=amount_to_split)
```

**For merge:** Need equal amounts of both outcome tokens

------

#### Q: "InsufficientGasBalance" error

**A:** Not enough BNB for gas fees.

**Fix:** Add BNB to your signer wallet:

Copy

```
# Check which wallet needs BNB
print(f"Signer address: {client.contract_caller.signer.address()}")

# Send BNB to this address
# Usually $1-5 worth is enough for many transactions
```