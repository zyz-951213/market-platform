-- 指数预测英文兼容视图
CREATE OR REPLACE VIEW 兼容.index_features AS
SELECT 指数代码 AS index_code, 交易日期 AS date, 载荷 AS payload
FROM 指数预测.指数特征;

CREATE OR REPLACE VIEW 兼容.time_series AS
SELECT 数据集键 AS dataset_key, 交易日期 AS date, 载荷 AS payload
FROM 指数预测.时序数据;

CREATE OR REPLACE VIEW 兼容.dataset_snapshots AS
SELECT 数据集键 AS dataset_key, 记录 AS records, 更新时间 AS updated_at
FROM 指数预测.数据集快照;

CREATE OR REPLACE VIEW 兼容.llm_cache AS
SELECT
    模型 AS model,
    提示词版本 AS prompt_version,
    提示词摘要 AS prompt_digest,
    行情日期 AS bar_date,
    响应 AS response,
    创建时间 AS created_at
FROM 指数预测.大模型缓存;

CREATE OR REPLACE VIEW 兼容.backtest_runs AS
SELECT
    运行编号 AS run_id,
    是否最新 AS is_latest,
    模型名称 AS model_name,
    行情频率 AS bar_freq,
    策略指纹 AS strategy_fingerprint,
    数据截至 AS data_as_of,
    保存时间 AS saved_at,
    指标 AS metrics,
    分类 AS classification
FROM 指数预测.回测记录;

CREATE OR REPLACE VIEW 兼容.backtest_signals AS
SELECT 运行编号 AS run_id, 交易日期 AS date, 载荷 AS payload
FROM 指数预测.回测信号;

CREATE OR REPLACE VIEW 兼容.fetch_meta AS
SELECT 数据集键 AS dataset_key, 载荷 AS payload, 更新时间 AS updated_at
FROM 指数预测.拉取元数据;

-- 注意：行为分析与指数预测都有应用状态；预测侧用限定名
CREATE OR REPLACE VIEW 兼容.predictor_app_state AS
SELECT 键 AS key, 载荷 AS payload, 更新时间 AS updated_at
FROM 指数预测.应用状态;
