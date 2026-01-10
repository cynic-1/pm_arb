"""
手续费计算模块
提供 Opinion 平台的手续费计算逻辑
"""

from typing import Optional, Tuple
from .config import ArbitrageConfig


class FeeCalculator:
    """手续费计算器"""

    def __init__(self, config: ArbitrageConfig):
        """
        初始化手续费计算器

        Args:
            config: 套利配置对象
        """
        self.config = config

    def round_price(self, value: Optional[float]) -> Optional[float]:
        """
        将价格四舍五入到配置的小数位数

        Args:
            value: 价格值

        Returns:
            四舍五入后的价格，如果输入为 None 则返回 None
        """
        if value is None:
            return None
        try:
            return round(float(value), self.config.price_decimals)
        except (TypeError, ValueError):
            return None

    def calculate_opinion_fee_rate(self, price: float) -> float:
        """
        计算 Opinion 平台的手续费率

        根据推导公式: fee_rate = 0.06 * price * (1 - price) + 0.0025

        Args:
            price: 订单价格

        Returns:
            手续费率 (小数形式)
        """
        return 0.06 * price * (1 - price) + 0.0025

    def calculate_opinion_adjusted_amount(
        self,
        price: float,
        target_amount: float,
        verbose: bool = True
    ) -> float:
        """
        计算 Opinion 平台考虑手续费后应下单的数量

        目标: 使得扣除手续费后,实际得到的数量等于 target_amount

        逻辑流程:
        1. 计算 fee_rate = 0.06 * price * (1 - price) + 0.0025
        2. 预计算: A_provisional = target_amount / (1 - fee_rate)
        3. 计算预估手续费: Fee_provisional = price * A_provisional * fee_rate
        4. 判断适用场景:
           - 如果 Fee_provisional > 0.5: 适用百分比手续费
             A_order = target_amount / (1 - fee_rate)
           - 如果 Fee_provisional <= 0.5: 适用最低手续费 $0.5
             A_order = target_amount + 0.5 / price

        Args:
            price: 订单价格
            target_amount: 期望最终得到的数量
            verbose: 是否打印详细信息

        Returns:
            应下单的数量 (考虑手续费后)
        """
        # 步骤1: 计算手续费率
        fee_rate = self.calculate_opinion_fee_rate(price)

        # 步骤2: 预计算 (假设适用百分比手续费)
        A_provisional = target_amount / (1 - fee_rate)

        # 步骤3: 计算预估手续费
        Fee_provisional = price * A_provisional * fee_rate

        # 步骤4: 判断适用场景并返回最终数量
        if Fee_provisional > self.config.opinion_min_fee:
            # 适用百分比手续费
            A_order = target_amount / (1 - fee_rate)
            if verbose:
                print(
                    f"💰 Opinion 手续费计算: price={price:.3f}, fee_rate={fee_rate:.6f}, "
                    f"预估手续费=${Fee_provisional:.4f} (百分比手续费)"
                )
        else:
            # 适用最低手续费
            A_order = target_amount + self.config.opinion_min_fee / price
            if verbose:
                print(
                    f"💰 Opinion 手续费计算: price={price:.3f}, fee_rate={fee_rate:.6f}, "
                    f"预估手续费=${Fee_provisional:.4f} -> 最低手续费 ${self.config.opinion_min_fee}"
                )

        if verbose:
            print(f"   目标数量: {target_amount:.2f} -> 修正后下单数量: {A_order:.2f}")

        return A_order

    def calculate_opinion_effective_amount(
        self,
        price: float,
        order_amount: float,
        verbose: bool = True
    ) -> float:
        """
        计算 Opinion 订单成交后实际得到的数量 (扣除手续费)

        关系: effective_amount = order_amount - fee / price

        Args:
            price: 订单价格
            order_amount: 下单数量
            verbose: 是否打印详细信息

        Returns:
            实际得到的数量 (扣除手续费后)
        """
        # 计算手续费率
        fee_rate = self.calculate_opinion_fee_rate(price)

        # 计算订单价值
        value = price * order_amount

        # 计算手续费 (至少 $0.5)
        fee = max(value * fee_rate, self.config.opinion_min_fee)

        # 计算实际得到的数量
        effective_amount = order_amount - fee / price

        if verbose:
            print(
                f"💰 Opinion 实际数量计算: 订单数量={order_amount:.2f}, "
                f"手续费=${fee:.4f}, 实际数量={effective_amount:.2f}"
            )

        return effective_amount

    def get_order_size_for_platform(
        self,
        platform: str,
        price: float,
        target_amount: float,
        is_hedge: bool = False,
        is_maker_order: bool = False,
        verbose: bool = True
    ) -> Tuple[float, float]:
        """
        获取指定平台的下单数量

        对于 Opinion 平台,需要考虑手续费进行修正
        对于 Polymarket 平台,直接使用目标数量

        Args:
            platform: 平台名称 ('opinion' 或 'polymarket')
            price: 订单价格
            target_amount: 目标数量（希望实际得到的数量）
            is_hedge: 是否是对冲单（对冲单需要精确匹配首单的实际数量）
            is_maker_order: 是否为流动性做市订单（maker order 不收手续费）
            verbose: 是否打印详细信息

        Returns:
            (order_size, effective_size): 下单数量和实际得到的数量
        """
        if platform == 'opinion':
            if is_maker_order:
                # Maker order 不收手续费，直接使用目标数量
                return target_amount, target_amount
            else:
                # Taker order 需要考虑手续费修正
                order_size = self.calculate_opinion_adjusted_amount(price, target_amount, verbose=verbose)
                effective_size = target_amount  # 修正后应该能得到目标数量
                return order_size, effective_size
        else:
            # Polymarket 直接使用目标数量
            return target_amount, target_amount

    def calculate_opinion_cost_per_token(
        self,
        price: Optional[float],
        size_tokens: float
    ) -> Optional[float]:
        """
        计算在 Opinion 上获取给定净数量时的单位成本（包含手续费）

        Args:
            price: 订单价格
            size_tokens: Token 数量

        Returns:
            单位成本，如果计算失败则返回 None
        """
        rounded_price = self.round_price(price)
        if rounded_price is None or rounded_price <= 0:
            return None

        size_tokens = max(size_tokens, 1e-6)
        fee_rate = self.calculate_opinion_fee_rate(rounded_price)

        if fee_rate >= 0.999:
            return None

        order_amount = size_tokens / (1.0 - fee_rate)
        trade_value = rounded_price * order_amount
        percentage_fee = trade_value * fee_rate

        if percentage_fee >= self.config.opinion_min_fee:
            effective_price = rounded_price / (1.0 - fee_rate)
        else:
            effective_price = rounded_price + (self.config.opinion_min_fee / size_tokens)

        return self.round_price(effective_price)
