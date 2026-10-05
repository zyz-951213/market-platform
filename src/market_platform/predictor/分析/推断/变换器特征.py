"""
Transformer 衍生特征 — 将短周期动量 Transformer 概率输出并入特征表

**前视修正(评审 R1)**
历史实现:训练一次后对**整个 df** 推理回写 `tf_proba_*` —— 训练集那部分概率是
"样本内预测"(模型见过答案),而回测与 LLM 校准器都消费这些列 ⇒ 量价通道被污染。

现改为两种**严格因果**的生成方式:

1. `走查(walk-forward)`(默认):按块前进,每块都用**该块之前**的数据重新训练,
   再预测本块 ⇒ 每一行的 `tf_proba_*` 都来自"只见过它之前数据"的模型。
2. `单次样本外`(走查关闭时):加载 checkpoint,只填**训练结束日之后**的行
   (区间来自 checkpoint 元数据;元数据缺失时以训练/验证切分点为界并告警),
   其余行一律留空(NaN)—— 宁可缺,不给样本内数字。

配置(`config/指数预测/配置档/默认.yaml → model.transformer`):

    export_features: true          # 是否导出 tf_* 特征
    export_walk_forward: true      # 走查生成(推荐);false = 仅填 checkpoint 之后的样本外行
    export_retrain_every: 120      # 走查块长(根数)
    export_min_train_rows: 750     # 走查最小训练行数(约 3 年日线),之前一律留空
    seed: 42                       # 随机种子(逐块派生 seed+块序号,保证可复现)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config


def is_transformer_features_enabled() -> bool:
    cfg = get_config().get("model", {}).get("transformer", {})
    return bool(cfg.get("export_features", cfg.get("as_lgb_features", False)))


def _空概率(n: int) -> tuple[np.ndarray, np.ndarray]:
    return np.full((n, 3), np.nan, dtype=np.float32), np.zeros(n, dtype=np.int64)


def _装载预测器(cfg: dict, *, seed: int | None = None):
    """构造 TransformerPredictor:把调用方的 cfg **整体**透传为覆盖项。

    注意(踩过的坑):只透传 seed 会让调用方配置的 seq_len/epochs/d_model 失效,
    当真实配置的 seq_len 大于切片长度时,`predict_proba_aligned` 会**静默**返回全 NaN。
    """
    from market_platform.predictor.分析.推断.变换器 import TransformerPredictor

    覆盖 = dict(cfg or {})
    if seed is not None:
        覆盖["seed"] = int(seed)
    return TransformerPredictor(覆盖 or None)


def _日期(df: pd.DataFrame, idx: int) -> str:
    if "date" not in df.columns or not (0 <= idx < len(df)):
        return f"#{idx}"
    return str(pd.Timestamp(df["date"].iloc[idx]).date())


def 走查概率(df: pd.DataFrame, cfg: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    """逐块重训 + 只预测本块 ⇒ 每一行都是样本外概率。"""
    seq_len = int(cfg.get("seq_len", 20))
    块长 = max(1, int(cfg.get("export_retrain_every", 120)))
    最小训练行 = max(seq_len + 1, int(cfg.get("export_min_train_rows", 750)))
    种子 = int(cfg.get("seed", 42))
    n = len(df)
    proba, pred = _空概率(n)
    统计 = {"块数": 0, "已填行": 0, "留空行": 0, "失败块": 0}

    if n <= 最小训练行 + 1:
        logger.warning(
            f"⚠️ 走查概率:数据仅 {n} 行,不足最小训练行 {最小训练行},全部留空(不给样本内数字)"
        )
        统计["留空行"] = n
        return proba, pred, 统计

    logger.info(
        f"🚶 走查生成 tf_proba:块长={块长} 最小训练行={最小训练行} 序列长={seq_len} 总行数={n}"
        "(每块重训一次,耗时随数据量增长)"
    )
    start = 最小训练行
    块序号 = 0
    while start < n:
        end = min(start + 块长, n)
        训练切片 = df.iloc[:start]
        try:
            预测器 = _装载预测器(cfg, seed=种子 + 块序号)
            预测器.train(训练切片)
            预测切片 = df.iloc[start - seq_len : end]
            块概率, 块方向 = 预测器.predict_proba_aligned(预测切片)
            本块 = 块概率[seq_len:]
            有效 = int(np.isfinite(本块[:, 1]).sum())
            if 有效 == 0:
                # 静默失效防护:切片过短/特征列不匹配时 predict 会返回全 NaN 而不报错
                统计["失败块"] += 1
                统计["留空行"] += end - start
                logger.warning(
                    f"  ⚠️ 块{块序号 + 1}(第 {start + 1}..{end} 行)推理结果为全缺失,"
                    f"该块留空(检查 seq_len={seq_len} 与切片长度 {len(预测切片)} 是否匹配)"
                )
            else:
                proba[start:end] = 本块
                pred[start:end] = 块方向[seq_len:]
                统计["块数"] += 1
                统计["已填行"] += 有效
                if 有效 < end - start:
                    统计["留空行"] += (end - start) - 有效
                logger.info(
                    f"  块{块序号 + 1}: 训练 {start} 行 → 预测第 {start + 1}..{end} 行 "
                    f"({_日期(df, start)} ~ {_日期(df, end - 1)}),有效 {有效}/{end - start}"
                )
        except Exception as exc:  # noqa: BLE001 — 单块失败不阻断其它块,但必须记账
            统计["失败块"] += 1
            统计["留空行"] += end - start
            logger.warning(
                f"  ⚠️ 块{块序号 + 1}(第 {start + 1}..{end} 行)训练/预测失败,该块留空: "
                f"{type(exc).__name__}: {exc}"
            )
        start = end
        块序号 += 1

    统计["留空行"] += 最小训练行
    return proba, pred, 统计


def 样本外概率(df: pd.DataFrame, cfg: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    """加载已有 checkpoint,只填训练结束日之后的行(样本外)。"""
    n = len(df)
    proba, pred = _空概率(n)
    统计 = {"已填行": 0, "留空行": n, "模式": "checkpoint样本外"}

    try:
        预测器 = _装载预测器(cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"⚠️ TransformerPredictor 初始化失败: {exc}")
        return proba, pred, 统计

    if not 预测器.try_load():
        logger.warning("⚠️ Transformer 特征跳过：无 checkpoint，请先跑 pipeline -- --step train")
        return proba, pred, 统计

    训练结束 = str(getattr(预测器, "train_end", "") or "")
    起点 = 0
    if 训练结束 and "date" in df.columns:
        日期 = pd.to_datetime(df["date"])
        起点 = int((日期 <= pd.Timestamp(训练结束)).sum())  # 训练结束日(含)之前全部留空
        logger.info(f"📌 checkpoint 训练结束于 {训练结束},其之前 {起点} 行留空(不写样本内概率)")
    else:
        切分 = get_config().get("model", {}).get("split", {})
        val_ratio = float(切分.get("val_ratio", 0.2))
        起点 = max(1, min(int(n * (1 - val_ratio)), n - 1))
        logger.warning(
            f"⚠️ checkpoint 无训练区间元数据(旧文件),退化为按 val_ratio={val_ratio} "
            f"从第 {起点 + 1} 行起填;为彻底避免样本内概率,建议开启 export_walk_forward"
        )

    统计["留空行"] = 起点
    if 起点 >= n - 1:
        logger.warning("⚠️ 无样本外行可填,全部留空")
        return proba, pred, 统计

    try:
        seq_len = int(cfg.get("seq_len", 20))
        切片 = df.iloc[max(0, 起点 - seq_len) :]
        全概率, 全方向 = 预测器.predict_proba_aligned(切片)
        偏移 = len(切片) - (n - 起点)
        proba[起点:] = 全概率[偏移:]
        pred[起点:] = 全方向[偏移:]
        统计["已填行"] = n - 起点
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"⚠️ Transformer 推理失败,全部留空: {exc}")
    return proba, pred, 统计


def add_transformer_derived_features(df: pd.DataFrame, *, 走查: bool | None = None) -> pd.DataFrame:
    """生成 `tf_proba_*` / `tf_pred_direction` 列(严格样本外,见模块 docstring)。"""
    cfg = get_config().get("model", {}).get("transformer", {})
    if not is_transformer_features_enabled():
        return df

    try:
        import market_platform.predictor.分析.推断.变换器  # noqa: F401 — 探测依赖可用性
    except ImportError as e:
        logger.warning(f"⚠️ Transformer 模型模块不可用: {e}")
        return df

    用走查 = bool(cfg.get("export_walk_forward", True)) if 走查 is None else bool(走查)
    if 用走查:
        proba, pred, 统计 = 走查概率(df, cfg)
    else:
        proba, pred, 统计 = 样本外概率(df, cfg)

    out = df.copy()
    out["tf_proba_down"] = proba[:, 0]
    out["tf_proba_flat"] = proba[:, 1]
    out["tf_proba_up"] = proba[:, 2]
    out["tf_pred_direction"] = pred
    有效 = int(np.isfinite(proba[:, 1]).sum())
    logger.info(
        f"🔗 Transformer 衍生特征({'走查' if 用走查 else '样本外'}): "
        f"{有效}/{len(out)} 行有效 | {统计}"
    )
    if 有效 == 0:
        logger.warning("⚠️ 没有任何样本外概率可用(tf_* 全缺失),下游应按缺失处理")
    return out
