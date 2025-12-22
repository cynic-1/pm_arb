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
