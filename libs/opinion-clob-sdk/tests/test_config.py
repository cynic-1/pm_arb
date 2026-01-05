import pytest
from web3 import Web3
from opinion_clob_sdk.config import (
    BNB_CHAIN_CONDITIONAL_TOKENS_ADDR,
    BNB_CHAIN_MULTISEND_ADDR,
    BNB_CHAIN_FEE_MANAGER_ADDR,
    DEFAULT_CONTRACT_ADDRESSES,
)


class TestContractAddresses:
    """Test that all contract addresses are in valid checksum format"""

    def test_conditional_tokens_address_is_checksum(self):
        """Test that conditional tokens address is in checksum format"""
        assert Web3.is_checksum_address(BNB_CHAIN_CONDITIONAL_TOKENS_ADDR)

    def test_multisend_address_is_checksum(self):
        """Test that multisend address is in checksum format"""
        assert Web3.is_checksum_address(BNB_CHAIN_MULTISEND_ADDR)

    def test_fee_manager_address_is_checksum(self):
        """Test that fee manager address is in checksum format"""
        assert Web3.is_checksum_address(BNB_CHAIN_FEE_MANAGER_ADDR)

    def test_all_default_addresses_are_checksum(self):
        """Test that all addresses in DEFAULT_CONTRACT_ADDRESSES are checksum"""
        for chain_id, addresses in DEFAULT_CONTRACT_ADDRESSES.items():
            for contract_name, address in addresses.items():
                assert Web3.is_checksum_address(address), \
                    f"Address for {contract_name} on chain {chain_id} is not checksum: {address}"

    def test_fee_manager_address_correct_value(self):
        """Test that fee manager address matches expected checksum value"""
        expected = "0xC9063Dc52dEEfb518E5b6634A6b8D624bc5d7c36"
        assert BNB_CHAIN_FEE_MANAGER_ADDR == expected
        assert Web3.is_checksum_address(BNB_CHAIN_FEE_MANAGER_ADDR)
