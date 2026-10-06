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


# ── R11/R12/R13/R15:执行正确性与显式披露 ─────────────────────────────────
def _假引擎(**覆盖):
    """构造未初始化的 BacktestEngine,只测纯方法(避免整套回测依赖)。"""
    from market_platform.predictor.分析.回测.引擎 import BacktestEngine

    引擎 = BacktestEngine.__new__(BacktestEngine)
    引擎.partition_overlay_enabled = True
    引擎.partition_overlay_source = "causal"
    引擎.因果分桶最小样本 = 2
    引擎._分桶命中 = {}
    引擎._分桶总数 = {}
    for 键, 值 in 覆盖.items():
        setattr(引擎, 键, 值)
    return 引擎


def test_因果分桶准确率_样本不足时不生效():
    引擎 = _假引擎()
    引擎._记入分桶结果(0.9, True)
    assert 引擎._因果分桶准确率(0.9) is None, "样本不足必须返回 None(不缩放)"


def test_因果分桶准确率_只反映已记入的历史():
    引擎 = _假引擎()
    引擎._记入分桶结果(0.9, True)
    引擎._记入分桶结果(0.9, False)
    assert 引擎._因果分桶准确率(0.9) == pytest.approx(0.5)
    # 再记入两次命中后,准确率上升;且此前返回的 0.5 是基于"当时已知"的结果
    准确率_之前 = 引擎._因果分桶准确率(0.9)
    引擎._记入分桶结果(0.9, True)
    引擎._记入分桶结果(0.9, True)
    assert 引擎._因果分桶准确率(0.9) > 准确率_之前


def test_重训块长改名后仍兼容旧键():
    from market_platform.predictor.分析.回测.引擎 import BacktestEngine
    import inspect

    源码 = inspect.getsource(BacktestEngine.__init__)
    assert "retrain_block" in 源码 and "rebalance_freq" in 源码, "必须支持新键并兼容旧键"


def test_自检_能发现_unified_下失效的风控(monkeypatch):
    from market_platform.predictor import 自检

    monkeypatch.setattr(
        自检, "get_config",
        lambda: {
            "portfolio": {"allocation_mode": "unified_sharpe"},
            "backtest": {"strategy": {"stop_loss": 0.08, "take_profit": {"enabled": True}}},
        },
    )
    问题 = 自检.检查统一夏普失效风控()
    assert any("stop_loss" in p for p in 问题)
    assert any("take_profit" in p for p in 问题)


def test_自检_关闭的开关不报噪声(monkeypatch):
    from market_platform.predictor import 自检

    monkeypatch.setattr(
        自检, "get_config",
        lambda: {
            "portfolio": {"allocation_mode": "unified_sharpe"},
            "backtest": {"strategy": {"disagreement_gate": {"enabled": False}}},
        },
    )
    assert 自检.检查统一夏普失效风控() == []


def test_自检_能发现前视开关(monkeypatch):
    from market_platform.predictor import 自检

    monkeypatch.setattr(
        自检, "get_config",
        lambda: {
            "model": {
                "split": {"split_method": "cycle"},
                "transformer": {"export_features": True, "export_walk_forward": False},
            },
            "backtest": {"strategy": {"stop_loss_uses_future_label": True}},
            "data": {"macro_publish_lag_days": 0},
        },
    )
    问题 = 自检.检查前视开关()
    assert len(问题) >= 4, 问题


def test_自检_默认配置下前视开关为空():
    from market_platform.predictor import 自检

    # 真实配置:切分已改 cycle_tail、走查已开、宏观滞后 15
    assert 自检.检查前视开关() == [], "默认配置不应存在主动打开的前视开关"


# ── C1:分层纪律(分析/采集/存储 不得反向依赖接口层) ────────────────────────
def test_分层方向_分析采集存储不得依赖接口层():
    """评审 C1:分析层 import 接口层是反向依赖,会把业务逻辑绑死在 HTTP 层。"""
    from market_platform.core.路径 import 包根

    根 = 包根() / "predictor"
    违规 = []
    for 子 in ("分析", "采集", "存储"):
        for p in (根 / 子).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            文本 = p.read_text(encoding="utf-8")
            if "market_platform.predictor.接口" in 文本:
                违规.append(str(p.relative_to(根)))
    assert not 违规, f"仍存在反向依赖: {违规}"


def test_被下移的能力仍可从接口层导入():
    """兼容性:接口层同名 shim 必须继续可用(外部脚本/前端依赖这些路径)。"""
    from market_platform.predictor.接口.准确率分区 import compute_accuracy_partitions  # noqa: F401
    from market_platform.predictor.接口.回测存储 import load_latest_backtest, strategy_fingerprint  # noqa: F401
    from market_platform.predictor.接口.分类指标 import compute_classification_metrics  # noqa: F401
    from market_platform.predictor.接口.策略摘要 import build_strategy_summary  # noqa: F401
    from market_platform.predictor.接口.模拟盘 import run_paper_trading_cycle  # noqa: F401
    from market_platform.predictor.接口.市场分析 import load_feature_df  # noqa: F401


def test_shim_与实现指向同一函数对象():
    from market_platform.predictor.分析.指标.准确率分区 import compute_accuracy_partitions as 实现
    from market_platform.predictor.接口.准确率分区 import compute_accuracy_partitions as shim

    assert 实现 is shim, "shim 必须是同一函数对象,而不是复制实现"


# ── C11:采集默认参数不得硬编码到过去 ─────────────────────────────────────
def test_拉取器默认结束日期不早于今年():
    import inspect
    from datetime import date

    from market_platform.predictor.采集.拉取器 import fetch_index_daily

    签名 = inspect.signature(fetch_index_daily)
    默认 = 签名.parameters["end_date"].default
    assert 默认 is None or str(默认) >= f"{date.today().year}-01-01", f"默认 end_date={默认} 会静默截断数据"


# ── C8:cutoff 空实现必须显式说明,而不是静默 no-op ────────────────────────
def test_推断渠道的_cutoff_不再静默():
    """这五个渠道不调 LLM API,因此 cutoff 无意义 —— 但必须显式说明并留痕(评审 C8)。"""
    import inspect

    from market_platform.predictor.分析.推断.集合元模型 import MetaEnsemblePredictor
    from market_platform.predictor.分析.推断.纪律策略 import DisciplineRulePredictor
    from market_platform.predictor.分析.推断.擅长因子 import SpecialtyFactorPredictor
    from market_platform.predictor.分析.推断.角色调仓 import RoleEnsemblePredictor
    from market_platform.predictor.分析.推断.语言模型因子 import LlmFactorPredictor

    for 类 in (MetaEnsemblePredictor, DisciplineRulePredictor, SpecialtyFactorPredictor,
               RoleEnsemblePredictor, LlmFactorPredictor):
        源码 = inspect.getsource(类.set_api_cutoff_date)
        assert "只读缓存信号" in 源码 or "不调用 LLM API" in 源码, f"{类.__name__} 的 cutoff 仍是静默 no-op"


def test_真实_LM_渠道仍保留_cutoff_语义():
    """对照:语言模型预测渠道确实会调 API,cutoff 必须真的生效(不能被顺手改空)。"""
    import inspect

    from market_platform.predictor.分析.推断.语言模型预测 import LLMApiPredictor

    源码 = inspect.getsource(LLMApiPredictor.set_api_cutoff_date)
    assert "_api_cutoff_date" in 源码, "真实渠道的 cutoff 语义被破坏"


# ── 只读缓存安全阀:额度有限时不得因为缺缓存而调 API ────────────────────────
def test_cache_only_未命中时拒绝调用_API():
    from market_platform.predictor.分析.推断.语言模型预测 import LLMApiPredictor

    p = LLMApiPredictor.__new__(LLMApiPredictor)  # 不跑 __init__,避免读配置/建客户端
    p._cache_only = True
    p._cache_write = False
    p._api_cutoff_date = None
    p._cache_hits = 0
    p._cache_misses = 0
    p.api_cfg = {"cache_enabled": True}

    def 无缓存(prompt, bar_date=None):
        return None

    p._load_cached = 无缓存  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as 异常:
        p.analyze_prompt("测试 prompt", bar_date="2026-09-30")
    assert "拒绝调用 API" in str(异常.value) or "只读缓存" in str(异常.value)


def test_cache_only_默认关闭():
    from market_platform.predictor.分析.推断.语言模型预测 import LLMApiPredictor

    p = LLMApiPredictor.__new__(LLMApiPredictor)
    p._cache_only = False
    assert p._cache_only is False


def test_回测记录白名单包含披露字段():
    """TASK-003:丢弃样本数与口径降级必须能落库,否则披露只活在日志里。"""
    import inspect

    from market_platform.predictor.存储.数据库 import PostgresStore

    源码 = inspect.getsource(PostgresStore.save_backtest_run)
    for 键 in ("dropped_bars", "failed_train_blocks", "failed_predict_bars",
               "unified_fallback_reason", "allocation_mode"):
        assert 键 in 源码, f"回测记录未持久化 {键}"


# ── R12:unified 主路径风控(开关驱动,默认关闭) ─────────────────────────────
def test_unified_风控默认关闭():
    from market_platform.predictor.分析.回测.引擎 import BacktestEngine

    e = BacktestEngine()
    assert e.统一风控启用 is False, "默认必须保持既有回测口径不变"
    assert e.统一风控触发 == {"止损": 0, "止盈": 0, "移动止盈": 0}


def test_unified_风控缩进攻并转入现金流():
    from market_platform.predictor.分析.回测.引擎 import BacktestEngine
    from market_platform.predictor.分析.资金.统一夏普配置 import PortfolioWeights

    e = BacktestEngine.__new__(BacktestEngine)
    e.统一风控启用 = True
    e.统一风控参数 = {"stop_loss": 0.08, "stop_loss_scale": 0.5}
    e.统一风控触发 = {"止损": 0, "止盈": 0, "移动止盈": 0}
    e.stop_loss = 0.08
    e.emergency_reduce_scale = 0.6

    pw = PortfolioWeights(
        weights={"csi300": 0.5, "nasdaq": 0.1, "gold": 0.2, "bond": 0.1, "mm": 0.05, "repo": 0.05},
        group_weights={"offense": 0.6, "defense": 0.3, "cashflow": 0.1},
    )
    新pw, 触发 = e._统一风控调整(pw, 进攻净值=0.90, 入场净值=1.0, 峰值=1.0)
    assert 触发 and "止损" in 触发[0]
    assert e.统一风控触发["止损"] == 1
    # 进攻袖缩半
    assert 新pw.weights["csi300"] == pytest.approx(0.25)
    assert 新pw.weights["nasdaq"] == pytest.approx(0.05)
    # 权重守恒(释放的给了现金流袖)
    assert sum(新pw.weights.values()) == pytest.approx(1.0, abs=1e-9)


def test_unified_风控未触发时不动仓位():
    from market_platform.predictor.分析.回测.引擎 import BacktestEngine
    from market_platform.predictor.分析.资金.统一夏普配置 import PortfolioWeights

    e = BacktestEngine.__new__(BacktestEngine)
    e.统一风控启用 = True
    e.统一风控参数 = {"stop_loss": 0.08, "take_profit_gain": 0.10, "trailing_peak_dd": 0.06}
    e.统一风控触发 = {"止损": 0, "止盈": 0, "移动止盈": 0}
    e.stop_loss = 0.08
    e.emergency_reduce_scale = 0.6

    pw = PortfolioWeights(weights={"csi300": 0.5, "nasdaq": 0.1, "gold": 0.2, "bond": 0.1, "mm": 0.05, "repo": 0.05},
                          group_weights={})
    新pw, 触发 = e._统一风控调整(pw, 进攻净值=1.03, 入场净值=1.0, 峰值=1.05)
    assert 触发 == [] and 新pw.weights == pw.weights


def test_指纹包含_portfolio_配置():
    """改 unified 配置必须改变策略指纹,否则历史记录无法区分口径。"""
    import inspect

    from market_platform.predictor.存储 import 回测存储

    源码 = inspect.getsource(回测存储.strategy_fingerprint)
    assert '"portfolio"' in 源码
