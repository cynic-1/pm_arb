from setuptools import setup, find_packages

NAME = "opinion_clob_sdk"
VERSION = "0.4.3"

setup(
    name=NAME,
    version=VERSION,
    description="Opinion CLOB SDK (Tech Preview) - Python SDK for Opinion Prediction Market Central Limit Order Book API",
    long_description="""
# Opinion CLOB SDK (Tech Preview)

**Technology Preview Release - BNB Chain Support**

Python SDK for interacting with Opinion prediction markets via the CLOB (Central Limit Order Book) API.

## Features

- Market data queries (markets, orderbooks, prices, candles)
- Order management (place, cancel, query orders)
- Position and balance tracking
- Smart contract interactions (split, merge, redeem)
- Support for BNB Chain mainnet (chain ID 56)

## Installation

```bash
pip install opinion_clob_sdk
```

## Quick Start

```python
from opinion_clob_sdk import Client

client = Client(
    host='https://proxy.opinion.trade:8443',
    apikey='your_api_key',
    chain_id=56,  # BNB Chain mainnet
    rpc_url='https://bsc-dataseed.binance.org',
    private_key='your_private_key',
    multi_sig_addr='your_multi_sig_address'
)

# Get markets
markets = client.get_markets(page=1, limit=10)

# Get orderbook
orderbook = client.get_orderbook(token_id='token_id')

# Place an order
from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER

order_data = PlaceOrderDataInput(
    marketId=123,
    tokenId='token_id',
    side=OrderSide.BUY,
    orderType=LIMIT_ORDER,
    price='0.5',
    makerAmountInQuoteToken=10
)

result = client.place_order(order_data)
```
    """,
    long_description_content_type="text/markdown",
    author="Opinion Labs",
    author_email="support@opinion.trade",
    url="https://opinion.trade",
    keywords=["PredictionMarket", "CLOB", "Trading", "Blockchain", "BNBChain", "BSC", "Opinion"],
    install_requires=[
        "urllib3 >= 2.3.0",
        "six >= 1.17.0",
        "certifi >= 2024.12.14",
        "python-dateutil >= 2.9.0.post0",
        "hexbytes >= 1.2.1",
        "web3 >= 7.6.1",
        "eth_account >= 0.13.0",
        "poly_eip712_structs >= 0.0.1",
        "opinion_api >= 0.2.1",
        "pytest >= 7.0.0",
    ],
    packages=[
        'opinion_clob_sdk',
        'opinion_clob_sdk.chain',
        'opinion_clob_sdk.chain.contracts',
        'opinion_clob_sdk.chain.py_order_utils',
        'opinion_clob_sdk.chain.py_order_utils.abi',
        'opinion_clob_sdk.chain.py_order_utils.builders',
        'opinion_clob_sdk.chain.py_order_utils.model',
        'opinion_clob_sdk.chain.safe',
        'opinion_clob_sdk.chain.safe.eip712',
        'opinion_clob_sdk.chain.safe.safe_contracts',
    ],
    package_dir={'opinion_clob_sdk': '.'},
    package_data={
        'opinion_clob_sdk': [
            'chain/py_order_utils/abi/*.json',
        ],
    },
    include_package_data=True,
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
