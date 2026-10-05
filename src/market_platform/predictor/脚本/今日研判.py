"""
今日预测 — 快速查看最新一期 LLM 研判结果

用法: python 后端/脚本/今日研判.py
"""

from __future__ import annotations


import pandas as pd
from loguru import logger

try:
    from dotenv import load_dotenv
    from market_platform.predictor.核心.路径.项目 import repo_root

    load_dotenv(repo_root() / ".env")
except ImportError:
    pass

from market_platform.predictor.接口.市场分析 import load_feature_df, run_deepseek_analysis


def main():
    print("=" * 50)
    print("📊 Market Predictor — 今日预测")
    print("=" * 50)

    df = load_feature_df()
    latest = df.iloc[-1]
    print(f"\n最新数据: {pd.Timestamp(latest['date']).date()}")
    print(f"收盘价: {latest['close']:.2f}")

    print("\n正在调用 DeepSeek API 研判...")
    result = run_deepseek_analysis()
    print("\n" + "=" * 50)
    print("研判结果:")
    print("=" * 50)
    print(result.get("summary", result))


if __name__ == "__main__":
    main()
