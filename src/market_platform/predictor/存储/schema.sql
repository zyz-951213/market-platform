-- market-predictor PostgreSQL schema（指数预测）
-- 表/列均为中文标识符，与库内实际结构一致

CREATE TABLE IF NOT EXISTS "指数特征" (
    "指数代码" TEXT NOT NULL,
    "交易日期" DATE NOT NULL,
    "载荷" JSONB NOT NULL,
    PRIMARY KEY ("指数代码", "交易日期")
);
CREATE INDEX IF NOT EXISTS "指数特征_交易日期_索引" ON "指数特征"("交易日期");

CREATE TABLE IF NOT EXISTS "时序数据" (
    "数据集键" TEXT NOT NULL,
    "交易日期" DATE NOT NULL,
    "载荷" JSONB NOT NULL,
    PRIMARY KEY ("数据集键", "交易日期")
);
CREATE INDEX IF NOT EXISTS "时序数据_数据集键_索引" ON "时序数据"("数据集键");

CREATE TABLE IF NOT EXISTS "数据集快照" (
    "数据集键" TEXT PRIMARY KEY,
    "记录" JSONB NOT NULL,
    "更新时间" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS "大模型缓存" (
    "模型" TEXT NOT NULL,
    "提示词版本" TEXT NOT NULL,
    "提示词摘要" TEXT NOT NULL,
    "行情日期" DATE NOT NULL,
    "响应" JSONB NOT NULL,
    "创建时间" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY ("模型", "提示词版本", "提示词摘要", "行情日期")
);
CREATE INDEX IF NOT EXISTS "大模型缓存_行情日期_索引" ON "大模型缓存"("行情日期");

CREATE TABLE IF NOT EXISTS "回测记录" (
    "运行编号" TEXT PRIMARY KEY,
    "是否最新" BOOLEAN NOT NULL DEFAULT FALSE,
    "模型名称" TEXT,
    "行情频率" TEXT,
    "策略指纹" TEXT,
    "数据截至" DATE,
    "保存时间" TIMESTAMPTZ,
    "指标" JSONB NOT NULL DEFAULT '{}'::jsonb,
    "分类" JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS "回测记录_是否最新_索引" ON "回测记录"("是否最新") WHERE "是否最新";

CREATE TABLE IF NOT EXISTS "回测信号" (
    "运行编号" TEXT NOT NULL REFERENCES "回测记录"("运行编号") ON DELETE CASCADE,
    "交易日期" DATE NOT NULL,
    "载荷" JSONB NOT NULL,
    PRIMARY KEY ("运行编号", "交易日期")
);
CREATE INDEX IF NOT EXISTS "回测信号_运行编号_索引" ON "回测信号"("运行编号");

CREATE TABLE IF NOT EXISTS "拉取元数据" (
    "数据集键" TEXT PRIMARY KEY,
    "载荷" JSONB NOT NULL,
    "更新时间" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS "应用状态" (
    "键" TEXT PRIMARY KEY,
    "载荷" JSONB NOT NULL,
    "更新时间" TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
