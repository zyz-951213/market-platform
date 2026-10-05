from __future__ import annotations


def apply_price_impact(
    price: float,
    net_order_flow: float,
    avg_volume: float,
    impact_lambda: float,
) -> float:
    """简化 Kyle 价格冲击：ΔP/P ≈ λ × 净订单 / 典型成交量。"""
    if price <= 0 or avg_volume <= 0:
        return price
    normalized_flow = net_order_flow / avg_volume
    return max(price * (1.0 + impact_lambda * normalized_flow), 0.01)
