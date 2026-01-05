"""
Test POA middleware integration for BNB Chain compatibility

This test verifies that the POA middleware is properly injected to handle
BNB Chain's extra block header data (280 bytes instead of 32).
"""

import pytest
from unittest.mock import Mock, patch
from web3 import Web3

# Handle both old and new web3.py versions
try:
    from web3.middleware import ExtraDataToPOAMiddleware
    geth_poa_middleware = ExtraDataToPOAMiddleware
except ImportError:
    from web3.middleware import geth_poa_middleware

from eth_typing import HexStr, ChecksumAddress

from opinion_clob_sdk.chain.contract_caller import ContractCaller


class TestPOAMiddleware:
    """Test POA middleware is properly configured for BNB Chain"""

    def test_poa_middleware_injected_on_init(self):
        """Test that POA middleware is injected during ContractCaller initialization"""

        # Mock Web3 instance
        mock_w3 = Mock(spec=Web3)
        mock_middleware_onion = Mock()
        mock_w3.middleware_onion = mock_middleware_onion

        with patch('opinion_clob_sdk.chain.contract_caller.Web3') as MockWeb3, \
             patch('opinion_clob_sdk.chain.contract_caller.Safe'):

            MockWeb3.return_value = mock_w3

            # Initialize ContractCaller
            caller = ContractCaller(
                rpc_url='https://bsc-dataseed.binance.org',
                private_key=HexStr('0x' + '1' * 64),
                multi_sig_addr=ChecksumAddress('0x' + '1' * 40),
                conditional_tokens_addr=ChecksumAddress('0x' + '2' * 40),
                multisend_addr=ChecksumAddress('0x' + '3' * 40)
            )

            # Verify POA middleware was injected at layer 0
            mock_middleware_onion.inject.assert_called()
            call_args = mock_middleware_onion.inject.call_args_list

            # Check that geth_poa_middleware was injected
            poa_injected = False
            for call in call_args:
                args, kwargs = call
                if len(args) > 0 and (args[0] == geth_poa_middleware or
                                      args[0].__name__ == geth_poa_middleware.__name__):
                    poa_injected = True
                    assert kwargs.get('layer') == 0, "POA middleware should be injected at layer 0"
                    break

            assert poa_injected, "POA middleware should be injected"

    def test_poa_middleware_import(self):
        """Test that POA middleware can be imported correctly"""
        # This verifies the import fallback works
        from opinion_clob_sdk.chain import contract_caller

        # Should have geth_poa_middleware defined
        assert hasattr(contract_caller, 'geth_poa_middleware')
        assert contract_caller.geth_poa_middleware is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

