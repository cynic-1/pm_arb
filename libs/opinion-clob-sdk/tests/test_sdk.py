import pytest
from unittest.mock import Mock, MagicMock, patch
from opinion_clob_sdk.sdk import Client, InvalidParamError, OpenApiError
from opinion_clob_sdk.model import TopicStatus, TopicType, TopicStatusFilter, TopicSortType


class TestClientInitialization:
    """Test Client initialization and configuration"""

    def test_client_initialization_with_valid_chain_id(self):
        """Test client initialization with valid chain ID"""
        # Test BNB Chain mainnet
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )
        assert client.chain_id == 56

    def test_client_initialization_with_invalid_chain_id(self):
        """Test client initialization with invalid chain ID"""
        with pytest.raises(InvalidParamError, match='chain_id must be one of'):
            Client(
                host='https://api.opinion.trade',
                apikey='test_key',
                chain_id=1,  # Invalid chain ID
                rpc_url='https://bsc-dataseed.binance.org',
                private_key='0x' + '0' * 64,
                multi_sig_addr='0x' + '0' * 40
            )


class TestMarketQueries:
    """Test market query methods"""

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_get_markets_with_pagination(self, mock_contract, mock_user_api, mock_market_api):
        """Test get_markets with pagination parameters"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        # Mock the API response
        mock_response = Mock()
        client.market_api.openapi_market_get = Mock(return_value=mock_response)

        # Test valid pagination
        result = client.get_markets(page=1, limit=10)
        client.market_api.openapi_market_get.assert_called_once()

        # Verify default sort_by is BY_TIME_DESC (value=1)
        call_args = client.market_api.openapi_market_get.call_args
        assert call_args.kwargs['sort_by'] == TopicSortType.BY_TIME_DESC.value
        assert result == mock_response

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_get_markets_invalid_page(self, mock_contract, mock_user_api, mock_market_api):
        """Test get_markets with invalid page number"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        with pytest.raises(InvalidParamError, match='page must be >= 1'):
            client.get_markets(page=0, limit=10)

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_get_markets_invalid_limit(self, mock_contract, mock_user_api, mock_market_api):
        """Test get_markets with invalid limit"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        with pytest.raises(InvalidParamError, match='limit must be between 1 and 20'):
            client.get_markets(page=1, limit=25)

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_get_market_missing_id(self, mock_contract, mock_user_api, mock_market_api):
        """Test get_market with missing market_id"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        with pytest.raises(InvalidParamError, match='market_id is required'):
            client.get_market(None)

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_get_markets_with_sort_by(self, mock_contract, mock_user_api, mock_market_api):
        """Test get_markets with sort_by parameter"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        # Mock the API response
        mock_response = Mock()
        client.market_api.openapi_market_get = Mock(return_value=mock_response)

        # Test with sort_by parameter
        result = client.get_markets(page=1, limit=10, sort_by=TopicSortType.BY_VOLUME_DESC)

        # Verify the method was called with correct parameters
        client.market_api.openapi_market_get.assert_called_once()
        call_args = client.market_api.openapi_market_get.call_args
        assert call_args.kwargs['sort_by'] == TopicSortType.BY_VOLUME_DESC.value
        assert result == mock_response


class TestTokenQueries:
    """Test token-related query methods"""

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_get_orderbook_missing_token_id(self, mock_contract, mock_user_api, mock_market_api):
        """Test get_orderbook with missing token_id"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        with pytest.raises(InvalidParamError, match='token_id is required'):
            client.get_orderbook(None)

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_get_latest_price(self, mock_contract, mock_user_api, mock_market_api):
        """Test get_latest_price for a token"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        mock_response = Mock()
        client.market_api.openapi_token_latest_price_get = Mock(return_value=mock_response)

        result = client.get_latest_price('token123')
        client.market_api.openapi_token_latest_price_get.assert_called_once_with(
            apikey='test_key',
            token_id='token123'
        )
        assert result == mock_response

    @patch('opinion_clob_sdk.sdk.ContractCaller')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    def test_get_fee_rates(self, mock_market_api, mock_user_api, mock_contract):
        """Test get_fee_rates for a token from on-chain FeeManager contract"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        # Mock the on-chain contract call response with converted fee rates
        # Example: 800 bps * 0.25 / 10000 = 0.02 (2%)
        #          1600 bps * 0.25 / 10000 = 0.04 (4%)
        mock_fee_data = {
            'maker_max_fee_rate': 0.02,  # 2%
            'taker_max_fee_rate': 0.04,  # 4%
            'enabled': True
        }

        # Set up the mock on the client's contract_caller instance
        client.contract_caller.get_fee_rate_settings = Mock(return_value=mock_fee_data)

        result = client.get_fee_rates('token123')
        client.contract_caller.get_fee_rate_settings.assert_called_once_with('token123')
        assert result == mock_fee_data
        assert result['maker_max_fee_rate'] == 0.02
        assert result['taker_max_fee_rate'] == 0.04
        assert result['enabled'] is True

    def test_fee_rate_conversion_formula(self):
        """Test the fee rate conversion formula: bps * 0.25 / 10000"""
        # Test case 1: 800 bps should convert to 0.02 (2%)
        maker_bps = 800
        expected_maker = 800 * 0.25 / 10000
        assert expected_maker == 0.02

        # Test case 2: 1600 bps should convert to 0.04 (4%)
        taker_bps = 1600
        expected_taker = 1600 * 0.25 / 10000
        assert expected_taker == 0.04

        # Test case 3: 10000 bps should convert to 0.25 (25%)
        max_bps = 10000
        expected_max = 10000 * 0.25 / 10000
        assert expected_max == 0.25

        # Test case 4: 0 bps should convert to 0
        zero_bps = 0
        expected_zero = 0 * 0.25 / 10000
        assert expected_zero == 0.0


class TestUserQueries:
    """Test user-related query methods"""

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_get_my_orders_invalid_market_id(self, mock_contract, mock_user_api, mock_market_api):
        """Test get_my_orders with invalid market_id type"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        with pytest.raises(InvalidParamError, match='market_id must be an integer'):
            client.get_my_orders(market_id='invalid')

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_get_order_by_id_invalid_id(self, mock_contract, mock_user_api, mock_market_api):
        """Test get_order_by_id with invalid order_id"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        with pytest.raises(InvalidParamError, match='order_id must be a non-empty string'):
            client.get_order_by_id(None)

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_get_my_positions_invalid_params(self, mock_contract, mock_user_api, mock_market_api):
        """Test get_my_positions with invalid parameters"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        with pytest.raises(InvalidParamError, match='page must be an integer'):
            client.get_my_positions(page='invalid')

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_get_user_auth(self, mock_contract, mock_user_api, mock_market_api):
        """Test get_user_auth method"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        mock_response = Mock()
        client.user_api.openapi_user_auth_get = Mock(return_value=mock_response)

        result = client.get_user_auth()
        client.user_api.openapi_user_auth_get.assert_called_once()
        assert result == mock_response


class TestOrderOperations:
    """Test order placement and cancellation"""

    @patch('opinion_clob_sdk.sdk.PredictionMarketApi')
    @patch('opinion_clob_sdk.sdk.UserApi')
    @patch('opinion_clob_sdk.sdk.ContractCaller')
    def test_cancel_order_invalid_order_id(self, mock_contract, mock_user_api, mock_market_api):
        """Test cancel_order with invalid order_id"""
        client = Client(
            host='https://api.opinion.trade',
            apikey='test_key',
            chain_id=56,
            rpc_url='https://bsc-dataseed.binance.org',
            private_key='0x' + '0' * 64,
            multi_sig_addr='0x' + '0' * 40
        )

        with pytest.raises(InvalidParamError, match='order_id must be a non-empty string'):
            client.cancel_order(None)

        with pytest.raises(InvalidParamError, match='order_id must be a non-empty string'):
            client.cancel_order(123)
