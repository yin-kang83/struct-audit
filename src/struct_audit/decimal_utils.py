# -*- coding: utf-8 -*-
"""Decimal 工具：本仓所有金额与比率只走 Decimal，绝不经过 float。

约定
  · 金额（元）：最多 2 位小数，超出即拒（不做「先四舍五入再放行」）；
  · 比率：有限 Decimal，区间由调用方断言；
  · 拒绝 bool（True 会被 Python 当成 1）、拒绝 float（二进制浮点表示不了 0.1）、
    拒绝 NaN / Infinity（非有限数无法参与确定性比较）。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")


def ensure_decimal(value: object, *, field: str = "数值") -> Decimal:
    """把 str / int / Decimal 转成 Decimal；其余类型一律拒（尤其是 float）。"""
    if isinstance(value, bool):
        raise TypeError(f"{field} 不接受 bool")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field} 为空字符串")
        try:
            result = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"{field} 不是合法的十进制数：{value!r}") from exc
    elif isinstance(value, float):
        raise TypeError(
            f"{field} 拒绝 float：{value!r}。二进制浮点不能精确表示 0.1 等十进制数，"
            "金额/比率请用字符串或 Decimal（例如 '0.10'、'1000.00'）。"
        )
    else:
        raise TypeError(f"{field} 类型不支持：{type(value).__name__}")
    if not result.is_finite():
        raise ValueError(f"{field} 必须是有限数，得到 {result}")
    return result


def parse_money(value: object, *, field: str = "金额") -> Decimal:
    """金额：必须能精确到分，且不超过 2 位小数。"""
    amount = ensure_decimal(value, field=field)
    quantized = amount.quantize(CENT)
    if quantized != amount:
        raise ValueError(f"{field} 最多 2 位小数（分），得到 {amount}")
    return quantized


def parse_rate(
    value: object,
    *,
    field: str = "比率",
    low: Decimal = Decimal("0"),
    high: Decimal = Decimal("1"),
    low_inclusive: bool = False,
    high_inclusive: bool = True,
) -> Decimal:
    """比率：默认要求 0 < r <= 1（小数形式，如 0.10）。"""
    rate = ensure_decimal(value, field=field)
    low_ok = rate >= low if low_inclusive else rate > low
    high_ok = rate <= high if high_inclusive else rate < high
    if not low_ok:
        operator = ">=" if low_inclusive else ">"
        raise ValueError(f"{field} 必须 {operator} {low}，得到 {rate}")
    if not high_ok:
        operator = "<=" if high_inclusive else "<"
        raise ValueError(f"{field} 必须 {operator} {high}，得到 {rate}")
    return rate


def q(value: Decimal) -> Decimal:
    """按分四舍五入（ROUND_HALF_UP），只用于算子输出。"""
    if not isinstance(value, Decimal):
        raise TypeError(f"q() 只接受 Decimal，得到 {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"q() 拒绝非有限数：{value}")
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def money_str(value: Decimal) -> str:
    """展示用：带千分位、两位小数。"""
    return f"{ensure_decimal(value, field='金额'):,.2f}"
