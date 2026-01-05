"""
Test ERC20 ABI completeness

This test verifies that the ERC20 ABI includes all standard functions,
especially the optional but commonly used ones: decimals, name, symbol.
"""

import json
import pytest
from opinion_clob_sdk.chain.contracts import erc20


class TestERC20ABI:
    """Test ERC20 ABI definition completeness"""

    def test_abi_is_valid_json(self):
        """Test that ABI can be parsed as valid JSON"""
        abi_list = json.loads(erc20.abi)
        assert isinstance(abi_list, list)
        assert len(abi_list) > 0

    def test_abi_contains_required_functions(self):
        """Test that ABI contains all required ERC20 functions"""
        abi_list = json.loads(erc20.abi)
        functions = {item['name'] for item in abi_list if item['type'] == 'function'}

        # Required ERC20 functions
        required_functions = {
            'totalSupply',
            'balanceOf',
            'transfer',
            'transferFrom',
            'approve',
            'allowance'
        }

        assert required_functions.issubset(functions), \
            f"Missing required functions: {required_functions - functions}"

    def test_abi_contains_optional_metadata_functions(self):
        """Test that ABI contains optional but standard ERC20 metadata functions"""
        abi_list = json.loads(erc20.abi)
        functions = {item['name'] for item in abi_list if item['type'] == 'function'}

        # Optional but commonly used functions
        optional_functions = {
            'decimals',
            'name',
            'symbol'
        }

        assert optional_functions.issubset(functions), \
            f"Missing optional metadata functions: {optional_functions - functions}"

    def test_decimals_function_signature(self):
        """Test that decimals function has correct signature"""
        abi_list = json.loads(erc20.abi)
        decimals_func = next(
            item for item in abi_list
            if item['type'] == 'function' and item['name'] == 'decimals'
        )

        assert decimals_func['inputs'] == []
        assert len(decimals_func['outputs']) == 1
        assert decimals_func['outputs'][0]['type'] == 'uint8'
        assert decimals_func['stateMutability'] == 'view'

    def test_name_function_signature(self):
        """Test that name function has correct signature"""
        abi_list = json.loads(erc20.abi)
        name_func = next(
            item for item in abi_list
            if item['type'] == 'function' and item['name'] == 'name'
        )

        assert name_func['inputs'] == []
        assert len(name_func['outputs']) == 1
        assert name_func['outputs'][0]['type'] == 'string'
        assert name_func['stateMutability'] == 'view'

    def test_symbol_function_signature(self):
        """Test that symbol function has correct signature"""
        abi_list = json.loads(erc20.abi)
        symbol_func = next(
            item for item in abi_list
            if item['type'] == 'function' and item['name'] == 'symbol'
        )

        assert symbol_func['inputs'] == []
        assert len(symbol_func['outputs']) == 1
        assert symbol_func['outputs'][0]['type'] == 'string'
        assert symbol_func['stateMutability'] == 'view'

    def test_abi_contains_events(self):
        """Test that ABI contains standard ERC20 events"""
        abi_list = json.loads(erc20.abi)
        events = {item['name'] for item in abi_list if item['type'] == 'event'}

        required_events = {'Transfer', 'Approval'}
        assert required_events.issubset(events), \
            f"Missing required events: {required_events - events}"

    def test_web3_can_use_abi(self):
        """Test that Web3 can use the ABI to create a contract interface"""
        from web3 import Web3

        w3 = Web3()
        # Use a dummy address for testing
        dummy_address = '0x' + '0' * 40

        try:
            contract = w3.eth.contract(address=dummy_address, abi=json.loads(erc20.abi))

            # Verify that all expected functions are accessible
            assert hasattr(contract.functions, 'decimals')
            assert hasattr(contract.functions, 'name')
            assert hasattr(contract.functions, 'symbol')
            assert hasattr(contract.functions, 'balanceOf')
            assert hasattr(contract.functions, 'totalSupply')
            assert hasattr(contract.functions, 'allowance')
            assert hasattr(contract.functions, 'approve')
            assert hasattr(contract.functions, 'transfer')
            assert hasattr(contract.functions, 'transferFrom')

        except Exception as e:
            pytest.fail(f"Failed to create Web3 contract with ERC20 ABI: {e}")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
