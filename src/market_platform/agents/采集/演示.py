from __future__ import annotations

import numpy as np
import pandas as pd


def generate_demo_minute_bars(
    symbol: str = "000001",
    start_date: str = "20250601",
    n_days: int = 60,
    seed: int = 42,
) -> pd.DataFrame:
    """生成合成分钟 K 线，用于离线验证管线（非真实行情）。"""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp(start_date)
    rows: list[dict] = []
    price = 10.0
    trading_days = 0
    day_offset = 0

    while trading_days < n_days:
        day = start + pd.Timedelta(days=day_offset)
        day_offset += 1
        if day.weekday() >= 5:
            continue
        trading_days += 1

        day_open = price * (1 + rng.normal(0, 0.005))
        price = day_open
        minutes = pd.date_range(
            day.replace(hour=9, minute=30),
            day.replace(hour=15, minute=0),
            freq="1min",
        )
        minutes = minutes[(minutes.hour < 11) | ((minutes.hour == 11) & (minutes.minute <= 30))
                          | (minutes.hour >= 13)]

        for i, ts in enumerate(minutes):
            ret = rng.normal(0, 0.0008)
            if i < 10:
                ret += 0.0003
            if i > len(minutes) - 15:
                ret += rng.normal(0, 0.0005)

            close = max(price * (1 + ret), 0.01)
            high = max(close, price) * (1 + abs(rng.normal(0, 0.0005)))
            low = min(close, price) * (1 - abs(rng.normal(0, 0.0005)))
            volume = float(rng.integers(50_000, 500_000))
            if i < 10 or i > len(minutes) - 15:
                volume *= rng.uniform(1.5, 3.0)
            amount = volume * (high + low) / 2

            rows.append(
                {
                    "datetime": ts,
                    "date": ts.date(),
                    "open": price,
                    "close": close,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "amount": amount,
                    "vwap": amount / volume,
                    "symbol": symbol,
                }
            )
            price = close

    if not rows:
        raise ValueError(f"No trading days generated for n_days={n_days}, start_date={start_date}")

    return pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)


def generate_demo_fund_flow(minute_df: pd.DataFrame) -> pd.DataFrame:
    """根据分钟收益生成弱相关的合成资金流向。"""
    daily = minute_df.groupby("date").agg(
        close=("close", "last"),
        pct_change=("close", lambda s: s.iloc[-1] / s.iloc[0] - 1 if len(s) > 1 else 0),
    ).reset_index()

    rng = np.random.default_rng(7)
    daily["main_net_inflow"] = daily["pct_change"] * rng.uniform(5e7, 2e8) + rng.normal(0, 1e7, len(daily))
    daily["large_net_inflow"] = daily["main_net_inflow"] * rng.uniform(0.3, 0.6, len(daily))
    daily["small_net_inflow"] = -daily["main_net_inflow"] * rng.uniform(0.2, 0.5, len(daily))
    daily["main_net_ratio"] = daily["pct_change"] * 10 + rng.normal(0, 0.5, len(daily))
    daily["small_net_ratio"] = -daily["main_net_ratio"] * rng.uniform(0.3, 0.8, len(daily))
    daily["symbol"] = minute_df["symbol"].iloc[0]
    return daily[
        [
            "date",
            "close",
            "pct_change",
            "main_net_inflow",
            "large_net_inflow",
            "small_net_inflow",
            "main_net_ratio",
            "small_net_ratio",
            "symbol",
        ]
    ]


def generate_demo_northbound(dates: pd.Series) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    return pd.DataFrame(
        {
            "date": dates,
            "north_net_inflow": rng.normal(0, 3e9, len(dates)),
        }
    )


def generate_demo_margin(dates: pd.Series) -> pd.DataFrame:
    rng = np.random.default_rng(13)
    balance = 1.5e12 + np.cumsum(rng.normal(0, 5e9, len(dates)))
    df = pd.DataFrame({"date": dates, "margin_balance": balance})
    df["margin_balance_change"] = df["margin_balance"].diff().fillna(0)
    return df
