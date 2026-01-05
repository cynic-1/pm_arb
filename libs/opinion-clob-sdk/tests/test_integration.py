"""
Integration tests for Opinion CLOB SDK

These tests require:
- Valid API key in environment variable API_KEY
- Valid RPC URL in environment variable RPC_URL
- Valid private key in environment variable PRIVATE_KEY
- Valid multi-sig address in environment variable MULTI_SIG_ADDRESS

Run with: pytest -v test_integration.py
Skip with: pytest -v -m "not integration"
"""

import os
import pytest
from opinion_clob_sdk import Client
from opinion_clob_sdk.model import TopicType, TopicStatusFilter

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    """Create a client instance for integration tests"""
    api_key = os.getenv('API_KEY')
    rpc_url = os.getenv('RPC_URL')
    private_key = os.getenv('PRIVATE_KEY')
    multi_sig_addr = os.getenv('MULTI_SIG_ADDRESS')

    if not all([api_key, rpc_url, private_key, multi_sig_addr]):
        pytest.skip('Missing required environment variables for integration tests')

    return Client(
        host='https://api.opinion.trade',
        apikey=api_key,
        chain_id=56,  # BNB Chain mainnet
        rpc_url=rpc_url,
        private_key=private_key,
        multi_sig_addr=multi_sig_addr
    )


class TestMarketIntegration:
    """Integration tests for market queries"""

    def test_get_currencies(self, client):
        """Test fetching supported currencies"""
        result = client.get_currencies()
        assert result is not None
        assert hasattr(result, 'result')

    def test_get_markets(self, client):
        """Test fetching markets list"""
        result = client.get_markets(page=1, limit=10)
        assert result is not None

    def test_get_markets_filtered(self, client):
        """Test fetching markets with filters"""
        result = client.get_markets(
            topic_type=TopicType.BINARY,
            status=TopicStatusFilter.ACTIVATED,
            page=1,
            limit=5
        )
        assert result is not None

    def test_get_market_detail(self, client):
        """Test fetching a specific market"""
        # First get a market ID from the list
        markets = client.get_markets(page=1, limit=1)
        if hasattr(markets, 'result') and hasattr(markets.result, 'list') and len(markets.result.list) > 0:
            market_id = markets.result.list[0].id
            result = client.get_market(market_id)
            assert result is not None


class TestTokenIntegration:
    """Integration tests for token queries"""

    def test_get_orderbook(self, client):
        """Test fetching orderbook for a token"""
        # Get a market first, then use its token
        markets = client.get_markets(page=1, limit=1)
        if hasattr(markets, 'result') and hasattr(markets.result, 'list') and len(markets.result.list) > 0:
            market = markets.result.list[0]
            if hasattr(market, 'token_id_yes'):
                result = client.get_orderbook(market.token_id_yes)
                assert result is not None

    def test_get_latest_price(self, client):
        """Test fetching latest price for a token"""
        markets = client.get_markets(page=1, limit=1)
        if hasattr(markets, 'result') and hasattr(markets.result, 'list') and len(markets.result.list) > 0:
            market = markets.result.list[0]
            if hasattr(market, 'token_id_yes'):
                result = client.get_latest_price(market.token_id_yes)
                assert result is not None

    def test_get_price_history(self, client):
        """Test fetching price history for a token"""
        markets = client.get_markets(page=1, limit=1)
        if hasattr(markets, 'result') and hasattr(markets.result, 'list') and len(markets.result.list) > 0:
            market = markets.result.list[0]
            if hasattr(market, 'token_id_yes'):
                result = client.get_price_history(market.token_id_yes, interval='1hour', bars=10)
                assert result is not None


class TestUserIntegration:
    """Integration tests for user queries"""

    def test_get_user_auth(self, client):
        """Test user authentication"""
        result = client.get_user_auth()
        assert result is not None

    def test_get_my_balances(self, client):
        """Test fetching user balances"""
        result = client.get_my_balances()
        assert result is not None

    def test_get_my_orders(self, client):
        """Test fetching user orders"""
        result = client.get_my_orders(limit=10)
        assert result is not None

    def test_get_my_positions(self, client):
        """Test fetching user positions"""
        result = client.get_my_positions(page=1, pageSize=10)
        assert result is not None

    def test_get_my_trades(self, client):
        """Test fetching user trades"""
        result = client.get_my_trades(limit=10)
        assert result is not None
