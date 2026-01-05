# Opinion CLOB SDK (Tech Preview)

**Technology Preview Release - BNB Chain Support**

Python SDK for interacting with Opinion prediction markets via the CLOB (Central Limit Order Book) API.

**Latest Version: 0.2.5** - Fixed POA chain compatibility for BNB Chain operations

## Overview

The Opinion CLOB SDK provides a Python interface for:

- Querying prediction market data
- Placing and managing orders
- Tracking positions and balances
- Interacting with smart contracts (split, merge, redeem)

Supports BNB Chain mainnet (chain ID 56).

## Installation

```bash
pip install opinion_clob_sdk
```

## Quick Start

```python
from opinion_clob_sdk import Client

# Initialize client
client = Client(
    host='https://proxy.opinion.trade:8443',
    apikey='your_api_key',
    chain_id=56,  # BNB Chain mainnet
    rpc_url='your_rpc_url',
    private_key='your_private_key',
    multi_sig_addr='your_multi_sig_address'
)

# Get markets
markets = client.get_markets(page=1, limit=10)

# Get market detail
market = client.get_market(market_id=123)

# Get orderbook
orderbook = client.get_orderbook(token_id='token_123')

# Get latest price
price = client.get_latest_price(token_id='token_123')
```

## Core Features

### Market Data

```python
# Get all markets with filters
from opinion_clob_sdk.model import TopicType, TopicStatusFilter

markets = client.get_markets(
    topic_type=TopicType.BINARY,
    status=TopicStatusFilter.ACTIVATED,
    page=1,
    limit=20
)

# Get specific market
market = client.get_market(market_id=123)

# Get categorical market
categorical = client.get_categorical_market(market_id=456)

# Get supported currencies
currencies = client.get_currencies()
```

### Token Data

```python
# Get orderbook
orderbook = client.get_orderbook(token_id='token_123')

# Get latest price
price = client.get_latest_price(token_id='token_123')

# Get price history
history = client.get_price_history(
    token_id='token_123',
    interval='1hour',
    bars=24
)

# Get fee rates
fees = client.get_fee_rates(token_id='token_123')
```

### Trading

```python
from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER, MARKET_ORDER

# Place a limit order
order_data = PlaceOrderDataInput(
    marketId=123,
    tokenId='token_yes',
    side=OrderSide.BUY,
    orderType=LIMIT_ORDER,
    price='0.5',
    makerAmountInQuoteToken=10  # 10 USDC
)
result = client.place_order(order_data)

# Place a market order
market_order = PlaceOrderDataInput(
    marketId=123,
    tokenId='token_yes',
    side=OrderSide.SELL,
    orderType=MARKET_ORDER,
    price='0',  # Market orders don't need price
    makerAmountInBaseToken=5  # 5 YES tokens
)
result = client.place_order(market_order)

# Cancel an order
client.cancel_order(trans_no='order_trans_no')

# Get my orders
my_orders = client.get_my_orders(market_id=123, limit=10)

# Get order by ID
order = client.get_order_by_id(order_id='order_123')
```

### User Data

```python
# Get balances
balances = client.get_my_balances()

# Get positions
positions = client.get_my_positions(page=1, pageSize=10)

# Get trade history
trades = client.get_my_trades(market_id=123, limit=20)

# Get user auth info
auth = client.get_user_auth()
```

### Smart Contract Operations

```python
# Enable trading (approve tokens)
client.enable_trading()

# Split collateral into outcome tokens
tx_hash = client.split(market_id=123, amount=1000000)  # amount in wei

# Merge outcome tokens back to collateral
tx_hash = client.merge(market_id=123, amount=1000000)

# Redeem winning positions
tx_hash = client.redeem(market_id=123)
```

## Configuration

### Environment Variables

Create a `.env` file:

```
API_KEY=your_api_key
RPC_URL=your_rpc_url
PRIVATE_KEY=your_private_key
MULTI_SIG_ADDRESS=your_multi_sig_address
```

### Chain IDs

- **BNB Chain Mainnet**: 56

## Development

### Running Tests

```bash
# Install development dependencies
pip install -r requirements.txt

# Run unit tests
pytest -v -m "not integration"

# Run all tests (including integration)
pytest -v

# Run with coverage
pytest --cov=opinion_clob_sdk --cov-report=html
```

### Project Structure

```
opinion_clob_sdk/
├── __init__.py           # Package exports
├── sdk.py                # Main Client class
├── model.py              # Enums and types
├── config.py             # Configuration constants
├── chain/                # Blockchain interactions
│   ├── contract_caller.py
│   ├── py_order_utils/   # Order building and signing
│   └── safe/             # Gnosis Safe integration
└── tests/                # Test suite
    ├── test_sdk.py
    ├── test_model.py
    ├── test_order_calculations.py
    └── test_integration.py
```

## API Reference

See the [full API documentation](https://docs.opinion.trade) for detailed information.

## Support

- Documentation: https://docs.opinion.trade
- Email: support@opinion.trade
- GitHub Issues: https://github.com/opinionlabs/openapi/issues

## License

MIT License - see LICENSE file for details
