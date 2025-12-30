"""
通用辅助函数模块
提供数据转换、验证等工具函数
"""

from typing import Any, List, Optional


def to_float(value: Any) -> Optional[float]:
    """
    安全地将值转换为 float

    Args:
        value: 待转换的值

    Returns:
        转换后的 float 值，失败返回 None
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None


def to_int(value: Any) -> Optional[int]:
    """
    安全地将值转换为 int

    Args:
        value: 待转换的值

    Returns:
        转换后的 int 值，失败返回 None
    """
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def extract_from_entry(entry: Any, candidate_keys: List[str]) -> Optional[Any]:
    """
    从对象或字典中提取字段

    Args:
        entry: 数据对象或字典
        candidate_keys: 候选键名列表

    Returns:
        找到的第一个匹配值，未找到返回 None
    """
    if entry is None:
        return None

    if isinstance(entry, dict):
        for key in candidate_keys:
            if key in entry:
                return entry[key]
    else:
        for key in candidate_keys:
            if hasattr(entry, key):
                return getattr(entry, key)

    return None


def dedupe_tokens(token_ids: List[str]) -> List[str]:
    """
    去除 token ID 列表中的重复项，保持顺序

    Args:
        token_ids: Token ID 列表

    Returns:
        去重后的列表
    """
    deduped: List[str] = []
    seen: set = set()

    for token in token_ids or []:
        token_str = str(token or "").strip()
        if not token_str or token_str in seen:
            continue
        seen.add(token_str)
        deduped.append(token_str)

    return deduped


def infer_tick_size_from_price(price: float) -> str:
    """
    根据价格推断 tick_size

    Polymarket 规则:
    - 如果价格有两位小数（例如 0.45, 0.99），tick_size 为 "0.01"
    - 如果价格有三位小数（例如 0.455, 0.991），tick_size 为 "0.001"

    Args:
        price: 订单价格

    Returns:
        推断的 tick_size 字符串 ("0.01" 或 "0.001")
    """
    # 将价格转换为字符串以检查小数位数
    price_str = f"{price:.6f}".rstrip('0').rstrip('.')

    # 计算小数位数
    if '.' in price_str:
        decimal_places = len(price_str.split('.')[1])
        # 如果有3位或更多小数，使用 "0.001"
        if decimal_places >= 3:
            return "0.001"

    # 默认使用 "0.01"
    return "0.01"
