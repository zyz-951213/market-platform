"""观测与运维口径回归测试(TASK-003 第三批)。

覆盖 D3(状态不再假成功)、D5(水位扫描与控制台同口径)、D6(日历退化可见)。
"""

from __future__ import annotations

import datetime as dt

import pytest


# ── D3:更新明细状态必须由证据决定 ────────────────────────────────────────
def test_无水位列的数据集判为成功():
    from market_platform.data.存储.更新作业 import 判定明细状态

    assert 判定明细状态(有水位列=False, 水位前=None, 水位后=None, 是否推进=None) == "成功"


def test_水位推进判为成功():
    from market_platform.data.存储.更新作业 import 判定明细状态

    assert 判定明细状态(有水位列=True, 水位前="2026-09-01", 水位后="2026-09-30", 是否推进=True) == "成功"


def test_水位未推进不得伪装成功():
    from market_platform.data.存储.更新作业 import 判定明细状态

    状态 = 判定明细状态(有水位列=True, 水位前="2026-09-23", 水位后="2026-09-23", 是否推进=False)
    assert 状态 == "未推进", "此前无论结果都写『成功』(评审 D3)"


def test_取不到前后水位时判为未知():
    from market_platform.data.存储.更新作业 import 判定明细状态

    assert 判定明细状态(有水位列=True, 水位前=None, 水位后=None, 是否推进=False) == "未知"


# ── D5:水位扫描与控制台共用「期望最后日」口径 ─────────────────────────────
def _分类(**覆盖):
    from market_platform.data.脚本.刷新全库水位 import classify_status

    参数 = dict(strategy="增量", n=100, d1=dt.date(2026, 9, 23), d0=dt.date(2005, 1, 4))
    参数.update(覆盖)
    return classify_status(**参数)


def test_已达期望最后日判为已对齐():
    assert _分类(期望最后日=dt.date(2026, 9, 23), 已登记=True) == "已对齐"


def test_落后期望最后日给出落后天数():
    状态 = _分类(期望最后日=dt.date(2026, 9, 30), 已登记=True)
    assert 状态 == "可更新(落后7天)"


def test_已登记但无期望锚点判为未知():
    """与控制台视图 '未知' 逐字一致(评审 D5):不得与『正常』或『未登记』混为一谈。"""
    assert _分类(期望最后日=None, 已登记=True) == "未知"


def test_未登记数据集明确标注未登记():
    状态 = _分类(期望最后日=None, 已登记=False)
    assert 状态.startswith("未登记")


def test_判定优先级与视图一致():
    """已停用 → 冻结 → 不可调度 → 未知 → 已对齐/可更新(评审 D5)。"""
    assert _分类(已登记=True, 启用=False) == "已停用"
    assert _分类(strategy="冻结", 期望最后日=dt.date(2030, 1, 1), 已登记=True) == "冻结"
    assert _分类(可调度=False, 期望最后日=dt.date(2030, 1, 1), 已登记=True) == "不可调度"
    assert _分类(期望最后日=None, 已登记=True) == "未知"
    assert _分类(期望最后日=dt.date(2026, 9, 23), 已登记=True) == "已对齐"
    assert _分类(期望最后日=dt.date(2026, 9, 30), 已登记=True) == "可更新(落后7天)"


def test_空表与扫描失败优先于口径判定():
    assert _分类(n=0) == "空表"
    assert _分类(n=None) == "扫描失败"
    assert _分类(d1=None) == "无日期水位"


# ── D6:日历退化必须可见 ───────────────────────────────────────────────────
def test_日历状态可用时给出覆盖范围():
    from market_platform.agents.核心.任务.交易日历 import 日历状态

    状态 = 日历状态()
    assert set(状态) >= {"可用", "交易日数", "覆盖到", "回退原因", "回退方式"}
    if 状态["可用"]:
        assert 状态["交易日数"] > 0
        assert 状态["回退方式"] == "无"


def test_日历能识别法定节假日():
    """2026-10-05 在国庆假期内,不应被判为交易日。"""
    from market_platform.agents.核心.任务.交易日历 import 日历状态, is_trading_day

    if not 日历状态()["可用"]:
        pytest.skip("日历不可用,退化模式无法识别节假日")
    assert is_trading_day(dt.date(2026, 9, 30)) is True
    assert is_trading_day(dt.date(2026, 10, 5)) is False


def test_日历加载失败时记录回退原因(monkeypatch):
    """取数失败时必须留下回退原因,而不是静默按周一~五运行(评审 D6)。"""
    from market_platform.agents.存储 import 数据访问 as 访问
    from market_platform.agents.核心.任务 import 交易日历 as 日历

    def 取数必失败(*_a, **_kw):
        raise RuntimeError("模拟数据库不可用")

    monkeypatch.setattr(访问, "_store", 取数必失败)
    monkeypatch.setattr(日历, "_store", 取数必失败, raising=False)
    日历._load_a_share_trading_days.cache_clear()

    try:
        状态 = 日历.日历状态()
        assert 状态["可用"] is False
        assert 状态["回退原因"] and "模拟数据库不可用" in 状态["回退原因"]
        assert "周一~五" in 状态["回退方式"]
        # 退化模式下依然给出结果,但状态字段暴露了风险
        assert 日历.is_trading_day(dt.date(2026, 10, 5)) is True  # 周一,退化模式会误判为交易日
    finally:
        日历._load_a_share_trading_days.cache_clear()


# ── D9:破坏性运维脚本的安全护栏 ──────────────────────────────────────────
def test_护栏_dry_run_只打印不执行(capsys):
    from market_platform.agents.脚本.运维.安全护栏 import 破坏性操作护栏

    护栏 = 破坏性操作护栏(脚本名="测试脚本", dry_run=True)
    assert 护栏.拦截(动作="DELETE FROM 行为分析.策略净值", 影响="孤立行") is True
    输出 = capsys.readouterr().out
    assert "[dry-run]" in 输出 and "DELETE" in 输出


def test_护栏_未确认时拒绝执行(capsys):
    from market_platform.agents.脚本.运维.安全护栏 import 破坏性操作护栏

    护栏 = 破坏性操作护栏(脚本名="测试脚本", dry_run=False, confirmed=False)
    assert 护栏.拦截(动作="整表写回 行为分析.日行情") is True, "未确认必须拒绝"
    输出 = capsys.readouterr().out
    assert "--dry-run" in 输出 and "--yes" in 输出


def test_护栏_确认后放行():
    from market_platform.agents.脚本.运维.安全护栏 import 破坏性操作护栏

    护栏 = 破坏性操作护栏(脚本名="测试脚本", dry_run=False, confirmed=True)
    assert 护栏.拦截(动作="DELETE FROM 行为分析.策略净值") is False


def test_护栏_dry_run_优先于确认():
    from market_platform.agents.脚本.运维.安全护栏 import 破坏性操作护栏

    护栏 = 破坏性操作护栏(脚本名="测试脚本", dry_run=True, confirmed=True)
    assert 护栏.拦截(动作="VACUUM FULL") is True, "dry-run 优先,即使加了 --yes 也不执行"


def test_安全参数_提供_dry_run_与_yes():
    import argparse

    from market_platform.agents.脚本.运维.安全护栏 import 加入安全参数

    parser = argparse.ArgumentParser()
    加入安全参数(parser)
    args = parser.parse_args(["--dry-run"])
    assert args.dry_run is True and args.yes is False
    args = parser.parse_args(["--yes"])
    assert args.yes is True and args.dry_run is False


def test_破坏性脚本都声明了安全参数():
    """回归:三个破坏性脚本必须可 dry-run(评审 D9)。"""

    from market_platform.core.路径 import 包根

    运维 = 包根() / "agents" / "脚本" / "运维"
    for 名 in ("修复前复权.py", "修复脏标的.py"):
        文本 = (运维 / 名).read_text(encoding="utf-8")
        assert "加入安全参数" in 文本, f"{名} 未提供 --dry-run/--yes"
        assert "破坏性操作护栏" in 文本, f"{名} 未接入护栏"


# ── C5:回测渠道回退必须标注,不能让别的渠道冒充 ────────────────────────────
def test_渠道回退会标注_provider_mismatch(monkeypatch):
    from market_platform.predictor.存储 import 回测存储

    monkeypatch.setattr(回测存储, "expected_model_label", lambda provider=None: "m@x")
    monkeypatch.setattr(回测存储, "dal_load_backtest_by_model_name", lambda name: None if "@" in name else {"model_name": "m"})
    monkeypatch.setattr(回测存储, "load_latest_backtest", lambda: {"model_name": "latest"})

    结果 = 回测存储.load_backtest_for_provider("x")
    assert 结果 is not None
    assert 结果.get("provider_mismatch") is True
    assert 结果.get("requested_provider") == "x"
    assert 结果.get("provider_mismatch_reason")


def test_渠道命中时不得标注_mismatch(monkeypatch):
    from market_platform.predictor.存储 import 回测存储

    monkeypatch.setattr(回测存储, "expected_model_label", lambda provider=None: "m@x")
    monkeypatch.setattr(回测存储, "dal_load_backtest_by_model_name", lambda name: {"model_name": name})

    结果 = 回测存储.load_backtest_for_provider("x")
    assert 结果 is not None and "provider_mismatch" not in 结果


# ── C4:降级必须随响应返回 ────────────────────────────────────────────────
def test_回测图接口在无降级时不带_degraded():
    """正常路径不应平白多出字段(避免前端/缓存误判)。"""
    import inspect

    from market_platform.predictor.接口 import 市场分析

    源码 = inspect.getsource(市场分析.get_backtest_charts)
    assert "if 降级说明" in 源码, "降级字段必须是条件添加"
    assert 'degraded' in 源码


def test_静默降级点已加日志():
    """C4 清单:统计失败/checkpoint 检测失败必须留痕,而不是静默置默认值。"""
    import inspect

    from market_platform.predictor.分析.指标 import 策略摘要
    from market_platform.predictor.存储 import 数据库

    assert "检测失败" in inspect.getsource(策略摘要.build_strategy_summary)
    assert "统计失败" in inspect.getsource(数据库.PostgresStore)


# ── D2:幂等改由数据库业务键约束保证(而非 Python 预读) ────────────────────
def test_冲突策略可配置且默认更新():
    from market_platform.data.存储.数据更新器 import _冲突策略

    assert _冲突策略() in {"更新", "忽略"}


def test_可更新列排除代理键与时间戳():
    from market_platform.data.存储.数据更新器 import _可更新列

    列 = _可更新列({"序号": 1, "证券代码": "000001", "交易日期": "2026-10-01", "updated_at": "NOW()", "source": "akshare"})
    assert "序号" not in 列 and "updated_at" not in 列
    assert {"证券代码", "交易日期", "source"} <= set(列)


def test_业务键唯一列_能发现业务索引并忽略代理主键():
    """真实库上验证:index 日行情 的业务键应为三列(含简称),而代理主键不算。"""
    import psycopg

    from market_platform.core.配置 import 数据库DSN
    from market_platform.data.存储.数据更新器 import _业务键唯一列

    try:
        conn = psycopg.connect(数据库DSN(), connect_timeout=5, autocommit=True)
    except Exception:
        pytest.skip("数据库不可用")
    try:
        列 = _业务键唯一列(conn, "公共", "国内指数日行情文件")
        assert 列 is not None, "迁移 005 应已建立业务键唯一索引"
        assert set(列) >= {"交易所指数代码", "交易日期"}
        assert "指数中文简称" in 列, "缺简称维度会把不同指数混为一谈(980001 的坑)"
    finally:
        conn.close()


def test_无业务键索引的表退回忽略策略():
    """只有代理主键的表不应被 DO UPDATE(否则没有任何冲突目标可用)。"""
    import psycopg

    from market_platform.core.配置 import 数据库DSN
    from market_platform.data.存储.数据更新器 import _业务键唯一列

    try:
        conn = psycopg.connect(数据库DSN(), connect_timeout=5, autocommit=True)
    except Exception:
        pytest.skip("数据库不可用")
    try:
        列 = _业务键唯一列(conn, "公共", "不存在这样的表_xyz")
        assert 列 is None
    finally:
        conn.close()


def test_写入统计可区分插入与更新():
    from market_platform.data.存储.数据更新器 import 取写入统计, 重置写入统计

    重置写入统计()
    assert 取写入统计() == {"插入": 0, "更新": 0, "跳过": 0}
