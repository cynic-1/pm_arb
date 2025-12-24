"""
PredictFun 客户端封装模块
负责初始化和管理 PredictFun SDK 客户端
"""

from typing import Optional, Any
from predict_sdk import OrderBuilder, ChainId, OrderBuilderOptions
from eth_account import Account


class PredictFunClient:
    """PredictFun 平台客户端封装"""

    def __init__(
        self,
        private_key: Optional[str] = None,
        predict_account: Optional[str] = None,
        chain_id: ChainId = ChainId.BNB_MAINNET,
        precision: int = 18,
        log_level: str = "INFO",
    ):
        """
        初始化 PredictFun 客户端

        Args:
            private_key: 私钥（可选，只读模式下不需要）
            predict_account: Predict smart account 地址（可选）
            chain_id: 链 ID
            precision: 精度（默认 18）
            log_level: 日志级别
        """
        self.private_key = private_key
        self.predict_account = predict_account
        self.chain_id = chain_id
        self._order_builder: Optional[OrderBuilder] = None
        self._trading_enabled = private_key is not None

        # 初始化 OrderBuilder
        self._init_order_builder(precision, log_level)

    def _init_order_builder(self, precision: int, log_level: str) -> None:
        """初始化 OrderBuilder"""
        if self.private_key:
            # 交易模式
            if self.predict_account:
                # 使用 Predict smart account
                options = OrderBuilderOptions(
                    predict_account=self.predict_account,
                    precision=precision,
                    log_level=log_level,
                )
                self._order_builder = OrderBuilder.make(
                    self.chain_id,
                    self.private_key,
                    options,
                )
            else:
                # 使用普通 EOA 钱包
                self._order_builder = OrderBuilder.make(
                    self.chain_id,
                    self.private_key,
                )
        else:
            # 只读模式
            self._order_builder = OrderBuilder.make(self.chain_id)

    @property
    def trading_enabled(self) -> bool:
        """是否启用交易功能"""
        return self._trading_enabled

    def get_order_builder(self) -> OrderBuilder:
        """获取 OrderBuilder 实例"""
        if self._order_builder is None:
            raise RuntimeError("OrderBuilder 未初始化")
        return self._order_builder

    def get_address(self) -> Optional[str]:
        """获取钱包地址"""
        if self.predict_account:
            return self.predict_account
        elif self.private_key:
            account = Account.from_key(self.private_key)
            return account.address
        return None

    def balance_of(self, token: str = "USDT", address: Optional[str] = None) -> int:
        """
        查询余额

        Args:
            token: 代币类型（默认 USDT）
            address: 查询地址（可选，默认为当前钱包）

        Returns:
            余额（wei 单位）
        """
        return self._order_builder.balance_of(token, address)

    async def balance_of_async(self, token: str = "USDT", address: Optional[str] = None) -> int:
        """
        异步查询余额

        Args:
            token: 代币类型（默认 USDT）
            address: 查询地址（可选，默认为当前钱包）

        Returns:
            余额（wei 单位）
        """
        return await self._order_builder.balance_of_async(token, address)

    def set_approvals(self, is_yield_bearing: bool = False) -> Any:
        """
        设置所有必要的授权

        Args:
            is_yield_bearing: 是否为收益型代币

        Returns:
            授权结果
        """
        if not self._trading_enabled:
            raise RuntimeError("只读模式下无法设置授权")
        return self._order_builder.set_approvals(is_yield_bearing=is_yield_bearing)

    def set_ctf_exchange_approval(
        self,
        is_neg_risk: bool = False,
        is_yield_bearing: bool = False,
        approved: bool = True,
    ) -> Any:
        """
        设置 CTF Exchange 授权（ERC-1155）

        Args:
            is_neg_risk: 是否为 NegRisk 市场
            is_yield_bearing: 是否为收益型代币
            approved: 是否授权

        Returns:
            交易结果
        """
        if not self._trading_enabled:
            raise RuntimeError("只读模式下无法设置授权")
        return self._order_builder.set_ctf_exchange_approval(
            is_neg_risk=is_neg_risk,
            is_yield_bearing=is_yield_bearing,
            approved=approved,
        )

    def set_ctf_exchange_allowance(
        self,
        is_neg_risk: bool = False,
        is_yield_bearing: bool = False,
        amount: Optional[int] = None,
    ) -> Any:
        """
        设置 CTF Exchange USDT 授权额度（ERC-20）

        Args:
            is_neg_risk: 是否为 NegRisk 市场
            is_yield_bearing: 是否为收益型代币
            amount: 授权额度（None 表示无限授权）

        Returns:
            交易结果
        """
        if not self._trading_enabled:
            raise RuntimeError("只读模式下无法设置授权")

        from predict_sdk import MAX_UINT256

        if amount is None:
            amount = MAX_UINT256

        return self._order_builder.set_ctf_exchange_allowance(
            is_neg_risk=is_neg_risk,
            is_yield_bearing=is_yield_bearing,
            amount=amount,
        )

    def validate_token_ids(
        self,
        token_ids: list,
        is_neg_risk: bool = False,
        is_yield_bearing: bool = False,
    ) -> bool:
        """
        验证 token ID 是否在交易所注册

        Args:
            token_ids: token ID 列表
            is_neg_risk: 是否为 NegRisk 市场
            is_yield_bearing: 是否为收益型代币

        Returns:
            是否有效
        """
        return self._order_builder.validate_token_ids(
            token_ids,
            is_neg_risk=is_neg_risk,
            is_yield_bearing=is_yield_bearing,
        )

    async def validate_token_ids_async(
        self,
        token_ids: list,
        is_neg_risk: bool = False,
        is_yield_bearing: bool = False,
    ) -> bool:
        """
        异步验证 token ID 是否在交易所注册

        Args:
            token_ids: token ID 列表
            is_neg_risk: 是否为 NegRisk 市场
            is_yield_bearing: 是否为收益型代币

        Returns:
            是否有效
        """
        return await self._order_builder.validate_token_ids_async(
            token_ids,
            is_neg_risk=is_neg_risk,
            is_yield_bearing=is_yield_bearing,
        )
