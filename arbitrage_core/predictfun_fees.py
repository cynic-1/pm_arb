"""
PredictFun 手续费计算模块
提供 PredictFun 平台的手续费计算逻辑

手续费规则:
- Makers 不收费
- Takers 根据价格和是否有折扣，费率在 0.018% 到 2% 之间
- 计算公式: Raw Fee = Base Fee % × min(Price, 1 - Price) × Shares
- 如果有 10% 折扣，则乘以 0.9
"""

from typing import Optional, Tuple


class PredictFunFeeCalculator:
    """PredictFun 手续费计算器"""

    def __init__(
        self,
        price_decimals: int = 6,
        has_discount: bool = False,
    ):
        """
        初始化手续费计算器

        Args:
            price_decimals: 价格小数位数
            has_discount: 是否有 10% 手续费折扣
        """
        self.price_decimals = price_decimals
        self.has_discount = has_discount

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
            return round(float(value), self.price_decimals)
        except (TypeError, ValueError):
            return None

    def calculate_taker_fee(
        self,
        price: float,
        shares: float,
        base_fee_bps: int,
    ) -> float:
        """
        计算 Taker 手续费（绝对值）

        公式: Raw Fee = Base Fee % × min(Price, 1 - Price) × Shares
        如果有折扣: Raw Fee × 0.9

        Args:
            price: 订单价格 (0-1 之间)
            shares: 数量
            base_fee_bps: 基础费率（基点，例如 200 表示 2%）

        Returns:
            手续费（USDT）
        """
        # 基础费率转换为小数
        base_fee_percent = base_fee_bps / 10000.0

        # 计算原始手续费
        raw_fee = base_fee_percent * min(price, 1 - price) * shares

        # 应用折扣
        if self.has_discount:
            raw_fee *= 0.9

        return raw_fee

    def calculate_taker_fee_rate(
        self,
        price: float,
        base_fee_bps: int,
    ) -> float:
        """
        计算 Taker 手续费率（百分比）

        公式: Percentage Fee = Base Fee % × min(Price, 1 - Price)
        如果有折扣: Percentage Fee × 0.9

        Args:
            price: 订单价格 (0-1 之间)
            base_fee_bps: 基础费率（基点，例如 200 表示 2%）

        Returns:
            手续费率（小数形式，例如 0.02 表示 2%）
        """
        # 基础费率转换为小数
        base_fee_percent = base_fee_bps / 10000.0

        # 计算费率
        fee_rate = base_fee_percent * min(price, 1 - price)

        # 应用折扣
        if self.has_discount:
            fee_rate *= 0.9

        return fee_rate

    def calculate_buy_cost(
        self,
        price: float,
        shares: float,
        base_fee_bps: int,
        is_maker: bool = False,
    ) -> Tuple[float, float, float]:
        """
        计算买入成本

        Args:
            price: 订单价格
            shares: 数量
            base_fee_bps: 基础费率（基点）
            is_maker: 是否为 Maker 订单

        Returns:
            (total_cost, base_cost, fee): 总成本、基础成本、手续费
        """
        base_cost = price * shares

        if is_maker:
            # Maker 不收费
            fee = 0.0
        else:
            # Taker 收费
            fee = self.calculate_taker_fee(price, shares, base_fee_bps)

        total_cost = base_cost + fee
        return total_cost, base_cost, fee

    def calculate_sell_revenue(
        self,
        price: float,
        shares: float,
        base_fee_bps: int,
        is_maker: bool = False,
    ) -> Tuple[float, float, float]:
        """
        计算卖出收入

        Args:
            price: 订单价格
            shares: 数量
            base_fee_bps: 基础费率（基点）
            is_maker: 是否为 Maker 订单

        Returns:
            (net_revenue, base_revenue, fee): 净收入、基础收入、手续费
        """
        base_revenue = price * shares

        if is_maker:
            # Maker 不收费
            fee = 0.0
        else:
            # Taker 收费
            fee = self.calculate_taker_fee(price, shares, base_fee_bps)

        net_revenue = base_revenue - fee
        return net_revenue, base_revenue, fee

    def calculate_effective_buy_price(
        self,
        price: float,
        base_fee_bps: int,
        is_maker: bool = False,
    ) -> float:
        """
        计算有效买入价格（包含手续费）

        对于买入 1 个 token:
        - 基础成本 = price
        - 每单位手续费 = Base Fee % × min(Price, 1 - Price)
        - 总成本 = price + 每单位手续费

        注意：手续费公式中的 min(Price, 1-Price) 是一个绝对值调整因子，
        不是相对费率！

        Args:
            price: 订单价格
            base_fee_bps: 基础费率（基点）
            is_maker: 是否为 Maker 订单

        Returns:
            有效价格（每个 token 的实际成本）
        """
        if is_maker:
            return price

        # 每单位手续费 = Base Fee % × min(Price, 1 - Price)
        base_fee_percent = base_fee_bps / 10000.0
        fee_per_token = base_fee_percent * min(price, 1 - price)

        # 应用折扣
        if self.has_discount:
            fee_per_token *= 0.9

        # 有效价格 = 原价格 + 每单位手续费
        effective_price = price + fee_per_token
        return effective_price

    def calculate_effective_sell_price(
        self,
        price: float,
        base_fee_bps: int,
        is_maker: bool = False,
    ) -> float:
        """
        计算有效卖出价格（扣除手续费）

        对于卖出 1 个 token:
        - 基础收入 = price
        - 每单位手续费 = Base Fee % × min(Price, 1 - Price)
        - 净收入 = price - 每单位手续费

        Args:
            price: 订单价格
            base_fee_bps: 基础费率（基点）
            is_maker: 是否为 Maker 订单

        Returns:
            有效价格（每个 token 的实际净收入）
        """
        if is_maker:
            return price

        # 每单位手续费 = Base Fee % × min(Price, 1 - Price)
        base_fee_percent = base_fee_bps / 10000.0
        fee_per_token = base_fee_percent * min(price, 1 - price)

        # 应用折扣
        if self.has_discount:
            fee_per_token *= 0.9

        # 有效价格 = 原价格 - 每单位手续费
        effective_price = price - fee_per_token
        return effective_price

    def get_order_size_for_predictfun(
        self,
        price: float,
        target_amount: float,
        base_fee_bps: int,
        is_maker: bool = True,
    ) -> Tuple[float, float]:
        """
        获取 PredictFun 平台的下单数量

        对于 Maker 订单，不收手续费，直接使用目标数量
        对于 Taker 订单，需要考虑手续费

        Args:
            price: 订单价格
            target_amount: 目标数量
            base_fee_bps: 基础费率（基点）
            is_maker: 是否为 Maker 订单

        Returns:
            (order_size, effective_size): 下单数量和实际得到的数量
        """
        if is_maker:
            # Maker 订单不收费，直接使用目标数量
            return target_amount, target_amount
        else:
            # Taker 订单收费，但由于是买入，数量不变
            # 只是成本会增加
            return target_amount, target_amount

    def calculate_cost_per_token(
        self,
        price: Optional[float],
        base_fee_bps: int,
        is_maker: bool = False,
    ) -> Optional[float]:
        """
        计算单位 token 成本（包含手续费）

        Args:
            price: 订单价格
            base_fee_bps: 基础费率（基点）
            is_maker: 是否为 Maker 订单

        Returns:
            单位成本，如果计算失败则返回 None
        """
        rounded_price = self.round_price(price)
        if rounded_price is None or rounded_price <= 0:
            return None

        if is_maker:
            # Maker 不收费
            return rounded_price

        # Taker 收费
        fee_rate = self.calculate_taker_fee_rate(rounded_price, base_fee_bps)
        # 单位成本 = 价格 + 手续费/单位
        # 由于手续费是 base_cost * fee_rate
        # 单位手续费 = price * fee_rate
        effective_price = rounded_price * (1 + fee_rate / rounded_price)

        return self.round_price(effective_price)
