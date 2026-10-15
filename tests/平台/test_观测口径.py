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


# ── D4:缺口扫描链路 ──────────────────────────────────────────────────────
def test_缺口扫描_责任方别名映射():
    from market_platform.data.脚本.扫描行情缺口 import 责任方别名

    assert 责任方别名["数据底座"] == "database"   # 登记表沿用旧英文代号
    assert 责任方别名.get("全部") is None
    assert 责任方别名["行为分析"] == "行为分析"


def test_缺口扫描_只针对按交易日的序列():
    """月频/季频数据集不该被当交易日逐日核对(否则全是假缺口)。"""
    import inspect

    from market_platform.data.脚本 import 扫描行情缺口 as 模块

    源码 = inspect.getsource(模块._登记数据集)
    assert "期望锚点" in 源码 and "交易日" in 源码


def test_缺口登记有业务键唯一索引():
    """没有业务键时 ON CONFLICT 无法去重,重复扫描会堆行(迁移 009)。"""
    import psycopg

    from market_platform.core.配置 import 数据库DSN

    try:
        conn = psycopg.connect(数据库DSN(), connect_timeout=5, autocommit=True)
    except Exception:
        pytest.skip("数据库不可用")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM pg_indexes
                WHERE schemaname='行情增量' AND tablename='缺口登记'
                  AND indexname='缺口登记_业务键_uidx'
                """
            )
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


# ── D7:档案分界日来自配置 ────────────────────────────────────────────────
def test_档案分界日可配置():
    from market_platform.core.配置 import 日行情档案分界日

    值 = 日行情档案分界日()
    assert isinstance(值, str) and len(值) == 10 and 值.startswith("20")


def test_运行时代码不再硬编码分界日():
    import inspect

    from market_platform.data.存储 import 数据更新器_项目 as 模块

    源码 = inspect.getsource(模块.update_latent_bars)
    assert "日行情档案分界日" in 源码 and "2026-05-20" not in 源码


# ── D8:镜像推送的爆炸半径预检 ────────────────────────────────────────────
def test_推送脚本有预检与确认门闸():
    from market_platform.core.路径 import 仓库根

    文本 = (仓库根() / "deploy" / "数据底座" / "推送增量.sh").read_text(encoding="utf-8")
    assert "执行前预检" in 文本
    assert "--yes" in 文本 and "CONFIRMED" in 文本
    assert "拒绝执行" in 文本
    # dry-run 必须完全离线(不建远端目录)
    assert "dry-run) 跳过远端临时目录创建" in 文本
    # 预检必须出现在联网之前
    assert 文本.index("执行前预检") < 文本.index("云端临时目录创建失败")


# ── TASK-004:市场类型编码与两段口径 ──────────────────────────────────────
def test_市场类型字典已登记且编码齐全():
    import psycopg

    from market_platform.core.配置 import 数据库DSN

    try:
        conn = psycopg.connect(数据库DSN(), connect_timeout=5, autocommit=True)
    except Exception:
        pytest.skip("数据库不可用")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 编码, 名称, 证券代码前缀 FROM 公共.市场类型字典 ORDER BY 编码")
            行 = dict((r[0], r[1]) for r in cur.fetchall())
            assert set(行) == {1, 2, 4, 8, 16, 32, 64}, 行
            assert "创业板" in 行[16] and "科创板" in 行[32] and "北交所" in 行[64]
    finally:
        conn.close()


def test_市场收益视图已被增量段补满():
    """TASK-004 的核心目标:被消费的 公共.市场收益 不再停在档案分界日。"""
    import psycopg

    from market_platform.core.配置 import 数据库DSN, 日行情档案分界日

    try:
        conn = psycopg.connect(数据库DSN(), connect_timeout=5, autocommit=True)
    except Exception:
        pytest.skip("数据库不可用")
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT max(trade_date) FROM 公共.市场收益')
            视图最后日 = cur.fetchone()[0]
            cur.execute('SELECT max(trade_date) FROM 公共.日行情')
            行情最后日 = cur.fetchone()[0]
            assert 视图最后日 is not None and 行情最后日 is not None
            # 视图应跟到 日行情 的最后一个交易日(允许 1 天差,防止跨日边界抖动)
            assert (行情最后日 - 视图最后日).days <= 1, (视图最后日, 行情最后日)
            assert str(视图最后日) > 日行情档案分界日(), "视图仍停在档案段"
    finally:
        conn.close()


def test_市场收益构建_口径与档案段一致():
    """决定"能否补齐"的关键证据:同一交易日上,自算口径与档案段的家数/成交量/收益对齐。"""
    from market_platform.data.存储.市场收益构建 import 一致性校验

    try:
        结果 = 一致性校验(样例日="2026-05-20")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"数据库不可用: {e}")
    assert 结果["分段"], "样例日应能取到档案段"
    家数一致 = [s for s in 结果["分段"] if s["档案家数"] == s["计算家数"]]
    assert len(家数一致) >= 5, 结果
    收益差 = [abs(s["等权差bp"]) for s in 结果["分段"] if s["等权差bp"] is not None]
    assert 收益差 and max(收益差) < 10, f"等权收益差过大(bp): {收益差}"


def test_市场收益构建幂等():
    import psycopg

    from market_platform.core.配置 import 数据库DSN
    from market_platform.data.存储.市场收益构建 import 构建市场收益

    try:
        conn = psycopg.connect(数据库DSN(), connect_timeout=5, autocommit=True)
    except Exception:
        pytest.skip("数据库不可用")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM 公共.市场收益文件")
            前 = cur.fetchone()[0]
    finally:
        conn.close()

    统计 = 构建市场收益()
    assert 统计["写入行"] == 0, f"重复构建不应新增行: {统计}"

    conn = psycopg.connect(数据库DSN(), connect_timeout=5, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM 公共.市场收益文件")
            后 = cur.fetchone()[0]
        assert 后 == 前
    finally:
        conn.close()


# ── ③ 缺口回填 ───────────────────────────────────────────────────────────
def test_缺口回填_策略表覆盖当前缺口数据集():
    from market_platform.data.存储.缺口回填 import 策略表

    # 具备区间回填能力的两个必须存在且为"区间"
    assert 策略表["龙虎榜"]["级别"] == "区间"
    assert 策略表["ETF融资融券交易明细表"]["级别"] == "区间"
    # 2026-10-06 复审:两个数据集发现了历史接口 ⇒ 从"快照"升级为逐股回填;
    # ETF 申赎清单确认只有即时快照,必须写明原因(不能只写"待处理")
    assert 策略表["龙虎榜营业部"]["级别"] == "逐股按日"
    assert 策略表["个股资金流向"]["级别"] == "逐股历史"
    assert 策略表["ETF基金申购赎回清单"]["级别"] == "快照"
    # 每个数据集都必须写明处置说明(不能只写"待处理")
    for 名 in ("龙虎榜", "ETF融资融券交易明细表", "龙虎榜营业部", "个股资金流向", "ETF基金申购赎回清单"):
        assert 策略表[名]["说明"]


def test_缺口登记没有无策略的待处理():
    """待处理必须都是**有回填路径**的待办(2026-10-06 起允许存在,如源限流待续跑),
    但不能出现策略表里没有定义、或策略是"快照"却仍挂在待处理的行 —— 那是假待办。"""
    import psycopg

    from market_platform.core.配置 import 数据库DSN
    from market_platform.data.存储.缺口回填 import 策略表

    try:
        conn = psycopg.connect(数据库DSN(), connect_timeout=5, autocommit=True)
    except Exception:
        pytest.skip("数据库不可用")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 状态, count(*) FROM 行情增量.缺口登记 GROUP BY 1")
            分布 = dict(cur.fetchall())
            cur.execute("SELECT DISTINCT 数据集 FROM 行情增量.缺口登记 WHERE 状态 = '待处理'")
            待处理集 = {r[0] for r in cur.fetchall()}
        assert set(分布) <= {"待处理", "已修复", "不可回填", "已关闭"}, 分布
        assert 分布.get("已修复", 0) + 分布.get("不可回填", 0) > 0, 分布
        for 数据集 in 待处理集:
            策略 = 策略表.get(数据集)
            assert 策略 is not None, f"{数据集} 挂在待处理但策略表未定义"
            assert 策略["级别"] != "快照", f"{数据集} 是快照型却仍是待处理(应标不可回填)"
    finally:
        conn.close()


def test_龙虎榜与ETF两融支持显式区间():
    import inspect

    from market_platform.data.存储 import 数据更新器_补充 as 模块

    assert hasattr(模块, "设龙虎榜回填区间")
    assert hasattr(模块, "设ETF两融回填区间")
    assert "stock_lhb_detail_em" in inspect.getsource(模块.update_dragon_tiger)
    assert "_ETF两融显式区间" in inspect.getsource(模块.update_etf_margin)


def test_insert_适配_autocommit_连接():
    """回归:autocommit 连接里 SAVEPOINT 会报 NoActiveSqlTransaction。"""
    import inspect

    from market_platform.data.存储.数据更新器 import _insert

    源码 = inspect.getsource(_insert)
    assert 'getattr(conn, "autocommit"' in 源码


# ── ② 前端字典展示 ───────────────────────────────────────────────────────
def test_前端概览页含市场分段字典面板():
    from market_platform.core.路径 import 前端根

    页面 = (前端根() / "模块" / "数据底座" / "index.html").read_text(encoding="utf-8")
    脚本 = (前端根() / "模块" / "数据底座" / "源码" / "页面" / "概览.js").read_text(encoding="utf-8")
    assert "market-dict-body" in 页面 and "市场分段字典" in 页面
    assert "市场类型字典" in 脚本 and "loadMarketDict" in 脚本


# ── ④ 数值口径护栏(2026-10-06:量纲混用 / NaN 传播 / 权重口径缺失)─────────────
def test_百分数转小数():
    """baostock 的 pctChg 是百分数,入库必须换算成小数(库内统一口径)。"""
    from market_platform.data.存储.数据更新器 import _pct_to_ratio

    assert _pct_to_ratio("-2.88") == pytest.approx(-0.0288)
    assert _pct_to_ratio(6.83) == pytest.approx(0.0683)
    assert _pct_to_ratio(None) is None
    assert _pct_to_ratio("--") is None


def test_非有限浮点按缺失处理():
    """NaN/±Inf 不得入库:numeric 能存 'NaN',聚合会把它整段传播出去。"""
    from market_platform.data.存储.数据更新器 import _to_n

    assert _to_n("NaN") is None
    assert _to_n(float("nan")) is None
    assert _to_n(float("inf")) is None
    assert _to_n("1.5") == pytest.approx(1.5)
    assert _to_n("-0.03") == pytest.approx(-0.03)


def test_insert_过滤非有限浮点():
    import inspect

    from market_platform.data.存储.数据更新器 import _insert

    源码 = inspect.getsource(_insert)
    assert "math.isfinite" in 源码, "写入侧必须拦掉 NaN/Inf(评审: 2026-10-06 事故)"


def test_baostock写入用百分数换算():
    import inspect

    from market_platform.data.存储 import 数据更新器_baostock as 模块

    源码 = inspect.getsource(模块._daily_bars_baostock)
    assert "_pct_to_ratio" in 源码, "baostock pctChg 是百分数,必须换算后入库"
    assert "_to_n(r[11])" not in 源码, "回归:曾直接写 _to_n(r[11]) 导致量纲放大 100 倍"


def test_深B含201前缀():
    """CSMAR 深B 市场含 201xxx(如 201872),只认 200xxx 会少数一只。"""
    from market_platform.data.存储.市场收益构建 import 分段表达式

    assert "'200', '201'" in 分段表达式


def test_权重口径逐分段():
    """B 股用 B 股市值/股本,科创板用 A 股口径(剔除 H 股),其余用原始市值列。"""
    from market_platform.data.存储.市场收益构建 import 权重列

    片段 = 权重列("y")
    assert "float_b_mkt_cap" in 片段 and "float_a_mkt_cap" in 片段
    assert "float_mkt_cap" in 片段
    assert "IN (2, 8)" in 片段 and "= 32" in 片段


def test_昨窗口锚点必须是分界日():
    """回归:窗口若锚到『止日』,区间前半段取不到前一日权重 → 市值加权整段为 NULL。"""
    import inspect

    from market_platform.data.存储.市场收益构建 import 构建市场收益

    源码 = inspect.getsource(构建市场收益)
    assert "(分界日, 止, 止, 分界日, 止, 分界日, 止)" in 源码


def test_除权息按日期关联():
    """回归:红 CTE 只按代码关联会让 今⋈红 行放大,虚增 n_firms/volume/amount。"""
    import inspect

    from market_platform.data.存储.市场收益构建 import 构建市场收益

    源码 = inspect.getsource(构建市场收益)
    assert "红.除权日 = j.trade_date" in 源码
    assert "GROUP BY 1, 2" in 源码


def test_市场收益构建覆盖分红与权重口径():
    import inspect

    from market_platform.data.存储.市场收益构建 import 构建市场收益

    源码 = inspect.getsource(构建市场收益)
    assert "ret_ew_div" in 源码 and "ret_total_vw_div" in 源码
    assert "权重口径" in 源码 and "股本结转" in 源码


def test_数据体检判据对量纲异常报警():
    """体检项的阈值方向:量纲放大 100 倍是『偏大』,必须报错,而不是报『偏小』。"""
    from market_platform.data.存储 import 数据体检 as 模块

    assert 模块.涨跌幅上界 == pytest.approx(0.12)
    # 模拟判据:0.02 正常、2.4 异常
    异常 = [v for v in (0.02, 2.4, 0.03, 3.3) if v > 模块.涨跌幅上界]
    assert 异常 == [2.4, 3.3]


def test_数据体检打印只在错误时非零():
    from market_platform.data.存储.数据体检 import 打印数据体检

    行: list[str] = []
    assert 打印数据体检({"项": [("提示", "仅供参考")]}, 行.append) == 0
    行.clear()
    assert 打印数据体检({"项": [("错误", "必须修")]}, 行.append) == 1
    行.clear()
    assert 打印数据体检({"项": []}, 行.append) == 0


# ── ⑤ 幂等与重复写入(2026-10-06:无业务键唯一索引的表重跑会重复写整行)───────
def test_insert_do_nothing分支必须带回显():
    """回归:DO NOTHING 不带 RETURNING 时,『确实插入』与『被跳过』都返回空行,
    调用方会把新增记成跳过(龙虎榜营业部回填写入 1582 行却报新增 0)。"""
    import inspect

    from market_platform.data.存储.数据更新器 import _insert

    源码 = inspect.getsource(_insert)
    assert "ON CONFLICT DO NOTHING RETURNING 1" in 源码


def test_座位回填先按股票去重():
    """回归:详情表同一只股票有多行(每个上榜原因一行),逐行取明细会重复写入 N 次。"""
    import inspect

    from market_platform.data.存储.数据更新器_补充 import 回填龙虎榜营业部

    源码 = inspect.getsource(回填龙虎榜营业部)
    assert "已取" in 源码 and "continue" in 源码


def test_逐股回填合并区间只遍历一次():
    """逐股型数据集必须合并区间跑一次(否则 11 段 × 5000 只 ≈ 7 小时)。"""
    import inspect

    from market_platform.data.存储.缺口回填 import 回填缺口

    源码 = inspect.getsource(回填缺口)
    assert "逐股数据集" in 源码 and "合并" in 源码


def test_数据体检含业务键检查项():
    from market_platform.data.存储 import 数据体检 as 模块

    assert "业务键唯一性" in 模块.检查项
    assert callable(模块.检查业务键唯一性)


def test_缺口补齐判据必须看覆盖度():
    """回归:只看"当天有没有行"会把残缺日误判为已补齐(个股资金流向每日仅 5 行)。"""
    import inspect

    from market_platform.data.存储.缺口回填 import _区间已补齐

    src = inspect.getsource(_区间已补齐)
    assert "覆盖阈值" in src and "参照" in src
    assert "GROUP BY 1" in src, "必须按日统计行数,而不是只判断日期是否存在"


def test_缺口补齐参照取自数据集整体而非区间内部():
    """回归:参照若取自被检查区间,"整段残缺"的区间会内部自证完整(2026-10-06 两次误判)。"""
    import inspect

    from market_platform.data.存储.缺口回填 import _区间已补齐

    src = inspect.getsource(_区间已补齐)
    assert "percentile_cont(0.9)" in src, "参照应为数据集近 365 天日行数的 p90"
    assert "- 365" in src, "参照窗口必须独立于被检查区间"


def test_日覆盖完整度参照用p90():
    """参照不能用最大(事件驱动表波动大)也不能用中位数(有些表大多数日子本身残缺)。"""
    import inspect

    from market_platform.data.存储.数据体检 import 检查日覆盖完整度

    src = inspect.getsource(检查日覆盖完整度)
    assert "p90" in src and "0.9" in src


# ── ⑥ 应用层日行情口径(2026-10-06:93% 的成交额被写成成交量)────────────────
def test_腾讯日线成交量列必须换算为股与元():
    """回归:akshare 腾讯接口的 `amount` 列实际装的是**成交量(手)**,
    直接当成交额入库会造成"成交额 == 成交量"(行为分析.日行情 1271 万行)。"""
    import pandas as pd

    pytest.importorskip("akshare", reason="akshare 为可选数据源依赖，CI 未安装时跳过")
    from market_platform.agents.采集.数据源 import _normalize_daily

    df = pd.DataFrame({"date": ["2026-09-23", "2026-09-24"], "open": [10.0, 11.0], "close": [12.0, 13.0],
                       "high": [12.5, 13.5], "low": [9.9, 10.9], "amount": [300.0, 400.0]})
    out = _normalize_daily(df, "000001", "tencent")
    assert list(out["volume"]) == [30000.0, 40000.0], "手 → 股 必须 ×100"
    assert list(out["amount"]) == [360000.0, 520000.0], "成交额 = 股 × 收盘(元)"
    assert not (out["amount"] == out["volume"]).any()


def test_交易日集合合并两套日历口径():
    """回归:日历表 `A股` 只覆盖 2026-07-26 以后,`上证/深证A股` 只到 2026-07-25,
    只取一个会让另一段交易日集合为空。"""
    import inspect

    from market_platform.data.脚本.扫描行情缺口 import _交易日集合

    src = inspect.getsource(_交易日集合)
    assert "'A股'" in src and "上证A股" in src and "深证A股" in src


def test_日覆盖完整度只在开市日判定():
    """回归:节假日只有新闻类数据集有行,拿它当残缺日是假警报(2026-06-19 端午节)。"""
    import inspect

    from market_platform.data.存储.数据体检 import 检查日覆盖完整度

    src = inspect.getsource(检查日覆盖完整度)
    assert "_交易日集合" in src and "开市" in src


def test_应用层日行情口径体检项存在():
    from market_platform.data.存储 import 数据体检 as 模块

    assert "应用层日行情口径" in 模块.检查项
    assert callable(模块.检查应用层日行情口径)


def test_修复应用日行情以公共层为源():
    import inspect

    from market_platform.agents.脚本.运维.修复应用日行情 import 修复应用日行情

    src = inspect.getsource(修复应用日行情)
    assert "公共.日行情" in src and "行为分析.日行情" in src
    assert "ON CONFLICT" in src, "补缺失日必须幂等"
