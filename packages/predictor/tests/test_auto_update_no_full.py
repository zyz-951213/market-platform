"""自动更新：默认禁止全量回测。"""

from market_platform.predictor.分析.自动更新 import _needs_full_backtest


def test_needs_full_only_when_allowed_and_empty():
    assert _needs_full_backtest(["no_backtest"], allow_full=True) is True
    assert _needs_full_backtest(["no_backtest"], allow_full=False) is False
    assert _needs_full_backtest(["strategy_outdated"], allow_full=True) is False
    assert _needs_full_backtest(["model_mismatch", "data_outdated"], allow_full=True) is False
    assert _needs_full_backtest(["strategy_outdated"], allow_full=False) is False
