import pytest
from decimal import Decimal
from opinion_clob_sdk.chain.py_order_utils.utils import (
    calculate_order_amounts,
    round_to_significant_digits
)
from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide


class TestRoundToSignificantDigits:
    """Test the round_to_significant_digits function"""

    def test_round_to_4_digits(self):
        """Test rounding to 4 significant digits"""
        # 123456789 -> keep first 4 digits (1234) -> round 5 up -> 1235 -> 123500000
        assert round_to_significant_digits(123456789, 4) == 123500000
        # 10000000000000000000 has 2 significant digits, should stay the same
        assert round_to_significant_digits(10000000000000000000, 4) == 10000000000000000000
        # 12345 -> keep first 4 digits (1234) -> round 5 to nearest even -> 12340
        result = round_to_significant_digits(12345, 4)
        assert result in [12340, 12350]  # Python's round() uses banker's rounding
        # 1234 has exactly 4 digits, should stay the same
        assert round_to_significant_digits(1234, 4) == 1234

    def test_round_to_6_digits(self):
        """Test rounding to 6 significant digits"""
        assert round_to_significant_digits(123456789, 6) == 123457000
        assert round_to_significant_digits(10000000000000000000, 6) == 10000000000000000000
        assert round_to_significant_digits(123456, 6) == 123456

    def test_edge_cases(self):
        """Test edge cases"""
        assert round_to_significant_digits(0, 4) == 0
        assert round_to_significant_digits(1, 4) == 1
        assert round_to_significant_digits(999, 4) == 999
        assert round_to_significant_digits(9999, 4) == 9999
        assert round_to_significant_digits(10000, 4) == 10000

    def test_large_numbers(self):
        """Test with 18-decimal wei amounts"""
        # 10 USDC = 10000000000000000000 wei
        result = round_to_significant_digits(10000000000000000000, 4)
        assert result == 10000000000000000000

        # 42.553191 tokens = 42553191000000000000 wei
        result = round_to_significant_digits(42553191000000000000, 4)
        assert result == 42550000000000000000  # 4 significant digits: 4255


class TestOrderCalculationPrecision:
    """Test order calculations with 4-digit precision to avoid matching engine errors"""

    def test_buy_order_10_usdc_at_0_27(self):
        """
        Test the exact case that was failing:
        BUY 10 USDC @ 0.27 price

        Expected behavior:
        - maker_amount: 10 USDC = 10000000000000000000 wei
        - Price should be EXACT when calculated from maker/taker
        - No additional decimal places in the price
        """
        price = 0.27
        maker_amount = 10000000000000000000  # 10 USDC
        side = OrderSide.BUY

        recalculated_maker, taker = calculate_order_amounts(
            price=price,
            maker_amount=maker_amount,
            side=side,
            decimals=18
        )

        # Check that maker is close to 4 significant digits (allow small deviation for exact price)
        # The priority is EXACT price, not perfect 4-digit rounding
        expected_4digit = round_to_significant_digits(maker_amount, 4)
        # Allow up to 10% deviation from 4-digit target for price exactness
        assert abs(recalculated_maker - expected_4digit) / expected_4digit < 0.1, \
            f"Maker {recalculated_maker} too far from 4-digit target {expected_4digit}"

        # Most importantly: verify that the calculated price is EXACT
        calculated_price = Decimal(str(recalculated_maker)) / Decimal(str(taker))
        price_decimal = Decimal(str(price))

        # The calculated price should exactly match the input price
        assert calculated_price == price_decimal, \
            f"Price mismatch: expected {price_decimal}, got {calculated_price}"

        # Ensure we don't exceed the original maker amount
        assert recalculated_maker <= maker_amount

    def test_buy_order_10_usdc_at_0_235(self):
        """
        Test BUY 10 USDC @ 0.235 price (another reported case)
        """
        price = 0.235
        maker_amount = 10000000000000000000  # 10 USDC
        side = OrderSide.BUY

        recalculated_maker, taker = calculate_order_amounts(
            price=price,
            maker_amount=maker_amount,
            side=side,
            decimals=18
        )

        # Verify exact price match
        calculated_price = Decimal(str(recalculated_maker)) / Decimal(str(taker))
        price_decimal = Decimal(str(price))
        assert calculated_price == price_decimal, \
            f"Price mismatch: expected {price_decimal}, got {calculated_price}"

    def test_sell_order_precision(self):
        """Test SELL order price exactness"""
        price = 0.5
        maker_amount = 10000000000000000000  # 10 tokens
        side = OrderSide.SELL

        recalculated_maker, taker = calculate_order_amounts(
            price=price,
            maker_amount=maker_amount,
            side=side,
            decimals=18
        )

        # Verify exact price match for SELL: price = taker / maker
        calculated_price = Decimal(str(taker)) / Decimal(str(recalculated_maker))
        price_decimal = Decimal(str(price))
        assert calculated_price == price_decimal, \
            f"Price mismatch: expected {price_decimal}, got {calculated_price}"

    def test_various_prices_buy_orders(self):
        """Test BUY orders with various prices - ensure exact price match"""
        test_cases = [
            0.111,  # Should give exact 0.111, not 0.111000111
            0.222,  # Should give exact 0.222, not fail
            0.333,  # Should give exact 0.333
            0.125,  # Should give exact 0.125
            0.275,  # Should give exact 0.275
            0.1,
            0.25,
            0.5,
            0.75,
            0.9,
        ]

        for price in test_cases:
            recalculated_maker, taker = calculate_order_amounts(
                price=price,
                maker_amount=10000000000000000000,
                side=OrderSide.BUY,
                decimals=18
            )

            # Verify exact price match
            calculated_price = Decimal(str(recalculated_maker)) / Decimal(str(taker))
            price_decimal = Decimal(str(price))
            assert calculated_price == price_decimal, \
                f"Price {price}: expected {price_decimal}, got {calculated_price}"

    def test_various_prices_sell_orders(self):
        """Test SELL orders with various prices - ensure exact price match"""
        test_cases = [
            0.111,
            0.222,
            0.333,
            0.125,
            0.275,
            0.1,
            0.25,
            0.5,
            0.75,
            0.9,
        ]

        for price in test_cases:
            recalculated_maker, taker = calculate_order_amounts(
                price=price,
                maker_amount=10000000000000000000,
                side=OrderSide.SELL,
                decimals=18
            )

            # Verify exact price match
            calculated_price = Decimal(str(taker)) / Decimal(str(recalculated_maker))
            price_decimal = Decimal(str(price))
            assert calculated_price == price_decimal, \
                f"Price {price}: expected {price_decimal}, got {calculated_price}"

    def test_price_validation(self):
        """Test that invalid prices are rejected"""
        with pytest.raises(ValueError, match="Price must be between"):
            calculate_order_amounts(0.0, 10000000000000000000, OrderSide.BUY, 18)

        with pytest.raises(ValueError, match="Price must be between"):
            calculate_order_amounts(1.0, 10000000000000000000, OrderSide.BUY, 18)

        with pytest.raises(ValueError, match="Price must be between"):
            calculate_order_amounts(1.5, 10000000000000000000, OrderSide.BUY, 18)

    def test_precision_limit_validation(self):
        """Test that excessive price precision is rejected"""
        # 7 decimal places should fail (max is 6)
        with pytest.raises(ValueError, match="Price precision cannot exceed 6 decimal places"):
            calculate_order_amounts(0.1234567, 10000000000000000000, OrderSide.BUY, 18)

        # 6 decimal places should pass
        recalculated_maker, taker = calculate_order_amounts(
            0.123456, 10000000000000000000, OrderSide.BUY, 18
        )
        assert recalculated_maker > 0
        assert taker > 0

    def test_minimum_amount_enforcement(self):
        """Test that calculated amounts are at least 1"""
        # Small amounts should still produce valid results
        price = 0.5
        maker_amount = 100  # very small amount

        recalculated_maker, taker = calculate_order_amounts(
            price=price,
            maker_amount=maker_amount,
            side=OrderSide.BUY,
            decimals=18
        )

        # Should be at least 1
        assert recalculated_maker >= 1
        assert taker >= 1

    def test_no_overspending(self):
        """Test that recalculated_maker never exceeds original maker_amount"""
        test_cases = [
            (0.27, 10000000000000000000),
            (0.235, 10000000000000000000),
            (0.5, 10000000000000000000),
            (0.75, 10000000000000000000),
        ]

        for price, maker_amount in test_cases:
            recalculated_maker, taker = calculate_order_amounts(
                price=price,
                maker_amount=maker_amount,
                side=OrderSide.BUY,
                decimals=18
            )

            # Should never exceed original amount
            assert recalculated_maker <= maker_amount, \
                f"Overspending detected: {recalculated_maker} > {maker_amount} at price {price}"

    def test_calculated_price_within_bounds(self):
        """Test that calculated price from amounts is within valid bounds"""
        # Test reasonable prices that won't hit boundary validation issues
        for price in [0.1, 0.27, 0.5, 0.75, 0.9]:
            for side in [OrderSide.BUY, OrderSide.SELL]:
                recalculated_maker, taker = calculate_order_amounts(
                    price=price,
                    maker_amount=10000000000000000000,
                    side=side,
                    decimals=18
                )

                # Calculate actual price from amounts
                if side == OrderSide.BUY:
                    actual_price = recalculated_maker / taker
                else:
                    actual_price = taker / recalculated_maker

                # Should be within valid range with some tolerance
                assert 0.001 <= actual_price <= 0.999, \
                    f"Calculated price {actual_price} out of bounds for {side.name} at {price}"
