#!/usr/bin/env python
"""
市场预测 — 快速探索入口
用 Jupyter Notebook 做交互式数据分析
"""

# 这是给 jupyter notebook 用的 setup 脚本
# 在 notebook 里运行:
#   %run scripts/setup_notebook.py

import sys
from pathlib import Path

# 添加项目根目录
PROJECT_ROOT = Path.cwd()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from market_platform.predictor.配置 import get_config
from market_platform.predictor.核心.工具 import setup_logging
from market_platform.predictor.采集 import fetch_index_daily, load_local_data, clean_daily_data, build_feature_pipeline
from market_platform.predictor.分析.推断 import TransformerPredictor, LLMApiPredictor
from market_platform.predictor.分析.回测 import BacktestEngine, summary_report

import pandas as pd
import numpy as np

def main():
    setup_logging()
    print("✅ 环境已就绪！可用模块: pd, np, get_config, fetch_index_daily, ...")


if __name__ == "__main__":
    main()
