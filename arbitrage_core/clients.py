"""
平台客户端管理模块
负责初始化和管理 Opinion 和 Polymarket 客户端
"""

from typing import Optional
from opinion_clob_sdk import Client as OpinionClient
from py_clob_client.client import ClobClient

from .config import ArbitrageConfig


class PlatformClients:
    """管理 Opinion 和 Polymarket 平台客户端"""

    def __init__(self, config: ArbitrageConfig):
        """
        初始化平台客户端

        Args:
            config: 套利配置对象
        """
        self.config = config
        self.opinion_client: Optional[OpinionClient] = None
        self.polymarket_client: Optional[ClobClient] = None

        self._init_opinion_client()
        self._init_polymarket_client()

    def _init_opinion_client(self) -> None:
        """初始化 Opinion 客户端"""
        print("🔧 初始化 Opinion 客户端...")
        self.opinion_client = OpinionClient(
            host=self.config.opinion_host,
            apikey=self.config.opinion_api_key,
            chain_id=self.config.opinion_chain_id,
            rpc_url=self.config.opinion_rpc_url,
            private_key=self.config.opinion_private_key,
            multi_sig_addr=self.config.opinion_multi_sig_addr,
        )
        print("✅ Opinion 客户端初始化完成")

    def _init_polymarket_client(self) -> None:
        """初始化 Polymarket 客户端"""
        print("🔧 初始化 Polymarket 客户端...")

        if self.config.polymarket_private_key:
            # 交易模式
            self.polymarket_client = ClobClient(
                self.config.polymarket_host,
                key=self.config.polymarket_private_key,
                chain_id=self.config.polymarket_chain_id,
                signature_type=2,
                funder=self.config.polymarket_funder
            )
            self.polymarket_client.set_api_creds(
                self.polymarket_client.create_or_derive_api_creds()
            )
            print("✅ Polymarket 客户端初始化完成 (交易模式)")
        else:
            # 只读模式
            self.polymarket_client = ClobClient(self.config.polymarket_host)
            print("✅ Polymarket 客户端初始化完成 (只读模式)")

    @property
    def trading_enabled(self) -> bool:
        """是否启用交易功能"""
        return self.config.polymarket_trading_enabled

    def get_opinion_client(self) -> OpinionClient:
        """获取 Opinion 客户端"""
        if self.opinion_client is None:
            raise RuntimeError("Opinion 客户端未初始化")
        return self.opinion_client

    def get_polymarket_client(self) -> ClobClient:
        """获取 Polymarket 客户端"""
        if self.polymarket_client is None:
            raise RuntimeError("Polymarket 客户端未初始化")
        return self.polymarket_client
