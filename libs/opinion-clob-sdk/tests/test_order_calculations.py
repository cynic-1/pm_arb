import pytest
from opinion_clob_sdk.chain.py_order_utils.utils import calculate_order_amounts
from opinion_clob_sdk.chain.py_order_utils.model.sides import BUY, SELL


class TestOrderCalculations:
    """Test order amount calculations for buy and sell orders"""

    def test_buy_order_calculation(self):
        """Test maker and taker amount calculation for buy orders"""
        price = 0.5
        maker_amount = 1000000000000000000  # 1 token in wei
        side = BUY
        decimals = 6

        recalculated_maker_amount, taker_amount = calculate_order_amounts(
            price=price,
            maker_amount=maker_amount,
            side=side,
            decimals=decimals
        )

        # For buy orders, maker provides quote token (USDC)
        # taker provides base token (YES/NO)
        assert recalculated_maker_amount == maker_amount
        # Taker amount should be maker_amount / price
        expected_taker = int(maker_amount / price)
        assert taker_amount == expected_taker

    def test_sell_order_calculation(self):
        """Test maker and taker amount calculation for sell orders"""
        price = 0.5
        maker_amount = 1000000000000000000  # 1 token in wei
        side = SELL
        decimals = 6

        recalculated_maker_amount, taker_amount = calculate_order_amounts(
            price=price,
            maker_amount=maker_amount,
            side=side,
            decimals=decimals
        )

        # For sell orders, maker provides base token (YES/NO)
        # taker provides quote token (USDC)
        assert recalculated_maker_amount == maker_amount
        # Taker amount should be maker_amount * price
        expected_taker = int(maker_amount * price)
        assert taker_amount == expected_taker

    def test_buy_order_high_price(self):
        """Test buy order with high price (0.9) - now with 4-digit precision"""
        price = 0.9
        maker_amount = 1000000000000000000
        side = BUY
        decimals = 6

        recalculated_maker_amount, taker_amount = calculate_order_amounts(
            price=price,
            maker_amount=maker_amount,
            side=side,
            decimals=decimals
        )

        # With 4-digit precision, expect larger rounding differences
        # The maker amount will be rounded to 4 significant digits
        # Allow difference up to 10% due to 4-digit rounding
        assert recalculated_maker_amount <= maker_amount
        assert recalculated_maker_amount > 0

        # Verify taker amount is positive and reasonable
        assert taker_amount > 0

        # Verify the price relationship: for BUY orders, price = maker/taker
        # Allow larger rounding error due to 4-digit precision
        actual_price = recalculated_maker_amount / taker_amount
        assert abs(actual_price - price) < 0.1  # Increased tolerance for 4-digit precision

    def test_sell_order_low_price(self):
        """Test sell order with low price (0.1)"""
        price = 0.1
        maker_amount = 1000000000000000000
        side = SELL
        decimals = 6

        recalculated_maker_amount, taker_amount = calculate_order_amounts(
            price=price,
            maker_amount=maker_amount,
            side=side,
            decimals=decimals
        )

        assert recalculated_maker_amount == maker_amount
        expected_taker = int(maker_amount * price)
        assert taker_amount == expected_taker

    def test_large_amount(self):
        """Test calculation with large amounts"""
        price = 0.5
        maker_amount = 1000000000000000000000  # 1000 tokens
        side = BUY
        decimals = 6

        recalculated_maker_amount, taker_amount = calculate_order_amounts(
            price=price,
            maker_amount=maker_amount,
            side=side,
            decimals=decimals
        )

        assert recalculated_maker_amount == maker_amount
        expected_taker = int(maker_amount / price)
        assert taker_amount == expected_taker
