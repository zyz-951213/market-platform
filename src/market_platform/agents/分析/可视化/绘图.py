from __future__ import annotations

import logging
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from market_platform.agents.分析.可视化.字体 import setup_matplotlib_cjk
from market_platform.agents.分析.推断.智能体角色 import get_agent_columns

logger = logging.getLogger(__name__)

_CJK_FONT = setup_matplotlib_cjk()


def _cjk_kwargs() -> dict:
    if _CJK_FONT is None:
        return {}
    return {"fontproperties": _CJK_FONT}


def plot_backtest(equity_df: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(equity_df["date"], equity_df["strategy_equity"], label="Strategy", linewidth=1.5)
    ax.plot(equity_df["date"], equity_df["benchmark_equity"], label="Buy & Hold", linewidth=1.5, alpha=0.85)
    ax.set_title("Equity Curve: State-Based vs Buy & Hold")
    ax.set_xlabel("Date")
    ax.set_ylabel("Equity")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    logger.info("Saved plot -> %s", path)


def plot_states(states_df: pd.DataFrame, path: Path) -> None:
    state_to_y = {
        "恐慌": 0,
        "震荡偏弱": 1,
        "横盘": 2,
        "震荡偏强": 3,
        "狂热": 4,
    }
    plot_labels = ["Panic", "Weak", "Range", "Strong", "Euphoria"]
    y = states_df["market_state"].map(state_to_y)

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(states_df["date"], y, marker="o", markersize=3, linewidth=1)
    ax.set_yticks(list(state_to_y.values()))
    ax.set_yticklabels(plot_labels)
    ax.set_title("Market State Timeline")
    ax.set_xlabel("Date")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    logger.info("Saved plot -> %s", path)


def plot_agent_activity(activity_df: pd.DataFrame, path: Path) -> None:
    agent_cols = get_agent_columns(activity_df)
    if not agent_cols:
        return

    fig, ax = plt.subplots(figsize=(12, 5))
    dates = activity_df["date"]
    for col in agent_cols:
        ax.plot(dates, activity_df[col], label=col, alpha=0.85)
    ax.set_title("每日市场参与者活跃度", **_cjk_kwargs())
    ax.set_xlabel("Date")
    ax.set_ylabel("活跃度", **_cjk_kwargs())
    ax.legend(loc="upper right", ncol=2, fontsize=8, prop=_CJK_FONT)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    logger.info("Saved plot -> %s", path)
