"""日频交易信号分类（基于状态序列相邻两日对比）。"""

from __future__ import annotations

POSITION_DELTA_THRESHOLD = 0.05


def classify_signal_action(
    prev_state: str,
    curr_state: str,
    prev_position: float,
    curr_position: float,
) -> str:
    """对比相邻两日状态/仓位，返回操作语义。"""
    delta = float(curr_position) - float(prev_position)
    if abs(delta) >= POSITION_DELTA_THRESHOLD:
        return "加仓" if delta > 0 else "减仓"
    if str(prev_state) != str(curr_state):
        return "状态切换"
    return "持有"
