"""研究可信度回归测试 —— 守护 TASK-003 第一批的因果性修正。

这些用例只针对"是否使用未来信息"这一性质,不依赖网络、数据库与 GPU。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ── R5:扩展窗口归一化 / 标准化不得使用未来统计量 ──────────────────────────
def test_扩展窗口归一化_截断未来不改变历史行():
    from market_platform.agents.分析.特征.日频 import normalize_feature_matrix

    n = 60
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "volume_total": np.linspace(1.0, 10.0, n),
            "amount_total": np.linspace(10.0, 1.0, n),
        }
    )
    全长, _ = normalize_feature_matrix(df)
    前缀, _ = normalize_feature_matrix(df.iloc[:30])

    # 因果性:只用前 30 行算出的归一化值,必须与全长结果的前 30 行完全一致
    pd.testing.assert_frame_equal(
        全长.iloc[:30].reset_index(drop=True),
        前缀.reset_index(drop=True),
        check_dtype=False,
    )


def test_全样本归一化模式确实有前视_用于对照():
    from market_platform.agents.分析.特征.日频 import normalize_feature_matrix

    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=20, freq="D"),
            "volume_total": np.r_[np.ones(19), 100.0],
        }
    )
    因果, _ = normalize_feature_matrix(df)
    全量, _ = normalize_feature_matrix(df, 归一方式="full")
    # 旧行为:第 1 行就被未来的 100 压成 0.01;因果模式第 1 行应为 1.0
    assert 因果["volume_total"].iloc[0] == pytest.approx(1.0)
    # 旧行为下首行被未来的 100 稀释(log1p(1)/log1p(100)≈0.15)
    assert 全量["volume_total"].iloc[0] < 0.3


def test_扩展窗口标准化_逐行只用历史行():
    from market_platform.agents.分析.推断.隐马尔可夫 import _扩展窗口标准化

    x = np.array([[1.0], [2.0], [3.0], [100.0]])
    结果 = _扩展窗口标准化(x)
    # 第 1 行:均值=1,std=0→按 1 处理 → 0
    assert 结果[0, 0] == pytest.approx(0.0)
    # 第 2 行:均值=1.5,总体 std=0.5 → (2-1.5)/0.5 = 1
    assert 结果[1, 0] == pytest.approx(1.0)
    # 第 4 行(100)不得反过来影响前三行
    x_前缀 = _扩展窗口标准化(x[:3])
    np.testing.assert_allclose(结果[:3], x_前缀)


# ── R1:tf_proba 必须样本外生成 ────────────────────────────────────────────
class 假预测器:
    """记录每次"训练用了哪些行"的假预测器,用于验证走查不越界。"""

    记录: list[tuple[int, int]] = []

    def __init__(self, config_override=None):
        self.cfg = dict(config_override or {})
        self.训练区间: tuple[int, int] | None = None

    def train(self, df=None, val_df=None):
        self.训练区间 = (0, len(df))
        假预测器.记录.append((len(df), int(self.cfg.get("seed", 0))))
        return {"val_acc": 0.5}

    def predict_proba_aligned(self, df):
        n = len(df)
        seq_len = int(self.cfg.get("seq_len", 20))
        p = np.full((n, 3), np.nan, dtype=np.float32)
        p[seq_len:] = np.array([0.2, 0.3, 0.5], dtype=np.float32)
        d = np.zeros(n, dtype=np.int64)
        return p, d


def test_走查概率_每块只用该块之前的数据(monkeypatch):
    import market_platform.predictor.分析.推断.变换器特征 as 特征模块

    假预测器.记录 = []
    monkeypatch.setattr(特征模块, "_装载预测器", lambda cfg, seed=None: 假预测器({"seq_len": 5, "seed": seed}))

    n = 40
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=n, freq="D"), "close": np.arange(n)})
    cfg = {"seq_len": 5, "export_retrain_every": 10, "export_min_train_rows": 15, "seed": 7}

    proba, pred, 统计 = 特征模块.走查概率(df, cfg)

    # 训练行数必须严格递增且都等于"块起点",即只用了本块之前的数据
    训练行数 = [r[0] for r in 假预测器.记录]
    assert 训练行数 == sorted(训练行数)
    assert 训练行数[0] == 15
    assert all(行 < n for 行 in 训练行数)
    # 最小训练行之前一律留空(不给样本内数字)
    assert np.isnan(proba[:15]).all()
    assert np.isfinite(proba[15:]).all()
    # 每块种子不同 → 逐块可复现
    种子 = [r[1] for r in 假预测器.记录]
    assert len(set(种子)) == len(种子)
    assert 统计["失败块"] == 0 and 统计["已填行"] == n - 15


def test_走查概率_数据不足时全部留空_绝不给样本内数字():
    import market_platform.predictor.分析.推断.变换器特征 as 特征模块

    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=20, freq="D")})
    proba, pred, 统计 = 特征模块.走查概率(df, {"seq_len": 5, "export_min_train_rows": 50})
    assert np.isnan(proba).all()
    assert 统计["已填行"] == 0 and 统计["留空行"] == 20


def test_样本外概率_只填训练结束日之后(monkeypatch):
    import market_platform.predictor.分析.推断.变换器特征 as 特征模块

    class 带元数据预测器(假预测器):
        train_end = "2024-02-09"

        def try_load(self, name="transformer_model"):
            return True

    monkeypatch.setattr(特征模块, "_装载预测器", lambda cfg, seed=None: 带元数据预测器({"seq_len": 5}))

    n = 40
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=n, freq="D"), "close": np.arange(n)})
    proba, pred, 统计 = 特征模块.样本外概率(df, {"seq_len": 5})
    训练内 = (pd.to_datetime(df["date"]) <= pd.Timestamp("2024-02-09")).sum()
    assert np.isnan(proba[:训练内]).all(), "训练区间内的行必须留空"
    assert np.isfinite(proba[训练内:]).all()


# ── R2:切分不得让训练集跨越验证集 ─────────────────────────────────────────
def test_周期末尾切分_训练集严格早于验证集():
    from market_platform.predictor.采集.拆分 import cycle_tail_train_val_split

    n = 600
    rng = np.random.default_rng(0)
    close = 3000 + np.cumsum(rng.normal(0, 20, n))
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n, freq="B"), "close": close})

    train, val, 段 = cycle_tail_train_val_split(df, val_ratio=0.2)
    assert not train.empty and not val.empty
    assert train["date"].max() < val["date"].min(), "训练集不得跨越验证集起点"


def test_旧cycle切分回退到时间切分且不跨越():
    from market_platform.predictor.采集 import 拆分

    n = 600
    rng = np.random.default_rng(1)
    close = 3000 + np.cumsum(rng.normal(0, 20, n))
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n, freq="B"), "close": close})
    train, val, _ = 拆分.train_val_split(df, val_ratio=0.2, split_method="temporal")
    assert train["date"].max() < val["date"].min()


# ── R3:月频宏观指标按发布滞后可见 ─────────────────────────────────────────
def test_月频宏观_发布滞后生效且不回填():
    from market_platform.predictor.采集.基本面 import _expand_monthly_to_daily

    monthly = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01")],
            "m2_yoy": [8.7, 8.3],
        }
    )
    start, end = pd.Timestamp("2024-01-01"), pd.Timestamp("2024-03-31")
    无滞后 = _expand_monthly_to_daily(monthly, start, end, 列滞后={"m2_yoy": 0})
    有滞后 = _expand_monthly_to_daily(monthly, start, end, 列滞后={"m2_yoy": 15})
    无滞后 = 无滞后.set_index("date")["m2_yoy"]
    有滞后 = 有滞后.set_index("date")["m2_yoy"]

    # 语义锚点 = 所属期末(**不是**月初):1 月值在 1 月内不可知
    assert pd.isna(无滞后.loc["2024-01-30"])
    assert 无滞后.loc["2024-01-31"] == pytest.approx(8.7)
    # 有滞后 15 天:1 月值要到 01-31+15=02-15 才可见,之前必须为空(不得回填)
    assert pd.isna(有滞后.loc["2024-01-31"])
    assert pd.isna(有滞后.loc["2024-02-14"])
    assert 有滞后.loc["2024-02-15"] == pytest.approx(8.7)
    # 2 月值:02-29+15=03-15 才可见;在此之前只能看到 1 月的旧值(不是 NaN,也不得提前用 2 月值)
    assert 有滞后.loc["2024-03-14"] == pytest.approx(8.7)
    assert 有滞后.loc["2024-03-15"] == pytest.approx(8.3)


def test_宏观发布滞后配置可读():
    from market_platform.predictor.采集.基本面 import _宏观发布滞后

    assert _宏观发布滞后("m2_yoy") >= 1
    assert _宏观发布滞后("pmi_mfg") >= 0


# ── R4:追加式分解不得改写历史 ─────────────────────────────────────────────
def _特征矩阵(n: int, seed: int = 0) -> pd.DataFrame:
    from market_platform.agents.分析.特征.日频 import NMF_FEATURE_COLUMNS

    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"date": pd.date_range("2022-01-03", periods=n, freq="B")})
    for i, col in enumerate(NMF_FEATURE_COLUMNS):
        df[col] = np.abs(rng.normal(1.0 + i * 0.1, 0.3, n))
    return df


def test_追加式分解_历史活跃度保持原值():
    from market_platform.agents.分析.推断.矩阵分解 import 追加式分解
    from market_platform.agents.settings import NMFConfig

    cfg = NMFConfig()
    前段 = _特征矩阵(120, seed=1)
    后段 = _特征矩阵(150, seed=1)

    首次 = 追加式分解(前段, cfg)
    assert 首次.activity.shape[0] == 120

    二次 = 追加式分解(后段, cfg, 已有活跃度=首次.activity, 已有模式=首次.patterns)
    assert 二次.activity.shape[0] == 150
    # 历史 120 行必须逐值相等(不被重拟合改写)
    pd.testing.assert_frame_equal(
        首次.activity.reset_index(drop=True),
        二次.activity.head(120).reset_index(drop=True),
        check_dtype=False,
    )
    assert 二次.说明 is not None and 二次.说明["历史保留行"] == 120
    # 新增行非负(非负最小二乘)
    agent_cols = [c for c in 二次.activity.columns if c.startswith("Agent_")]
    assert (二次.activity[agent_cols].iloc[120:] >= 0).all().all()


def test_旧全窗口重拟合会改写历史_用于对照():
    from market_platform.agents.分析.推断.矩阵分解 import decompose_agent_activity
    from market_platform.agents.settings import NMFConfig

    cfg = NMFConfig()
    前段 = _特征矩阵(120, seed=2)
    后段 = _特征矩阵(150, seed=2)
    旧结果 = decompose_agent_activity(前段, cfg)
    新结果 = decompose_agent_activity(后段, cfg)
    # 旧实现下,同样日期的历史活跃度确实会变(证明 R4 是真实存在的偏差)
    assert not np.allclose(
        旧结果.activity[[c for c in 旧结果.activity.columns if c.startswith("Agent_")]].to_numpy(),
        新结果.activity[[c for c in 新结果.activity.columns if c.startswith("Agent_")]].head(120).to_numpy(),
    )


# ── R7:随机种子 ───────────────────────────────────────────────────────────
def test_种子可设定且可复现():
    from market_platform.core.随机 import 置随机种子

    a = 置随机种子(123)
    import random

    第一次 = [random.random() for _ in range(3)]
    b = 置随机种子(123)
    第二次 = [random.random() for _ in range(3)]
    assert a == b == 123
    assert 第一次 == 第二次


def test_取生成器_同种子同序列():
    from market_platform.core.随机 import 取生成器

    g1, g2 = 取生成器(5), 取生成器(5)
    if g1 is None:  # torch 不可用
        pytest.skip("torch 不可用")
    import torch

    a = torch.randperm(10, generator=g1)
    b = torch.randperm(10, generator=g2)
    assert torch.equal(a, b)


def test_装载预测器_必须透传调用方配置_而非只传种子(monkeypatch):
    """回归:只透传 seed 会让 seq_len 等配置失效,导致推理静默返回全 NaN。"""
    import market_platform.predictor.分析.推断.变换器 as 变换器
    import market_platform.predictor.分析.推断.变换器特征 as 特征模块

    捕获: dict = {}

    class 记录型(假预测器):
        def __init__(self, config_override=None):
            super().__init__(config_override)
            捕获.update(config_override or {})

    monkeypatch.setattr(变换器, "TransformerPredictor", 记录型)
    cfg = {"seq_len": 5, "export_retrain_every": 5, "export_min_train_rows": 25, "seed": 11}
    特征模块._装载预测器(cfg, seed=99)
    assert 捕获.get("seq_len") == 5, "调用方的 seq_len 必须透传"
    assert 捕获.get("export_min_train_rows") == 25
    assert 捕获.get("seed") == 99, "seed 覆盖应生效"


def test_走查概率_全缺失块必须记为失败而不是静默填零(monkeypatch):
    import market_platform.predictor.分析.推断.变换器特征 as 特征模块

    class 全NaN预测器(假预测器):
        def predict_proba_aligned(self, df):
            n = len(df)
            return np.full((n, 3), np.nan, dtype=np.float32), np.zeros(n, dtype=np.int64)

    monkeypatch.setattr(特征模块, "_装载预测器", lambda cfg, seed=None: 全NaN预测器({"seq_len": 5}))

    n = 30
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=n, freq="D")})
    proba, pred, 统计 = 特征模块.走查概率(
        df, {"seq_len": 5, "export_retrain_every": 5, "export_min_train_rows": 15}
    )
    assert 统计["已填行"] == 0
    assert 统计["失败块"] >= 1, "全缺失块必须记账为失败,不得算作已填"
    assert np.isnan(proba).all()
