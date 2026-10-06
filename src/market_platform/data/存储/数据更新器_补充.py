"""补充数据更新 — 完整字段覆盖重写"""
from datetime import date
from .数据更新器 import _to_n, _insert, _existing_set, _existing_key_set, baostock_login_or_raise, retry_call
from .数据更新器 import _existing_composite_set


def _parse_cn_money(v):
    """解析『1.87亿』『-5813.14万』等为元；纯数字原样转 float。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "")
    if not s or s in ("-", "--", "None", "nan"):
        return None
    mult = 1.0
    if s.endswith("亿"):
        mult = 1e8
        s = s[:-1]
    elif s.endswith("万"):
        mult = 1e4
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return _to_n(s)


def _parse_pct(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("%", "").replace(",", "")
    if not s or s in ("-", "--"):
        return None
    return _to_n(s)


def _quarter_ends(n: int = 6) -> list[str]:
    """最近 n 个季报期末 YYYYMMDD。"""
    today = date.today()
    candidates: list[date] = []
    yy = today.year
    while len(candidates) < n + 2:
        for qm, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            d = date(yy, qm, day)
            if d <= today:
                candidates.append(d)
        yy -= 1
    return [d.strftime("%Y%m%d") for d in candidates[:n]]


# ── 1. 个股资金流向 — 优先同花顺即时全市场，东财排行作回退 ──
def update_money_flow(updater) -> str:
    """个股资金流向: stock_fund_flow_individual(即时) → 东财排行回退。"""
    import akshare as ak
    from .数据更新器 import get_watermark

    conn = updater._get_conn()
    wm = get_watermark(conn, "公共", "个股资金流向")
    latest = wm.get("最后交易日") if wm else None
    day = date.today().strftime("%Y-%m-%d")
    with conn.cursor() as cur:
        cur.execute(
            'SELECT "股票代码" FROM "公共"."个股资金流向" WHERE "交易日期" = %s',
            (day,),
        )
        existing = {str(r[0]).strip().zfill(6) for r in cur.fetchall()}

    count = 0
    source_used = None
    last_err = None

    # 1) 同花顺即时（字段为流入/流出/净额字符串）
    try:
        rank = retry_call(
            lambda: ak.stock_fund_flow_individual(symbol="即时"),
            retries=3,
            delay=2.0,
            what="资金流向即时",
        )
        source_used = "同花顺即时"
        for _, row in rank.iterrows():
            code = str(row.get("股票代码", "") or row.get("代码", "")).strip().zfill(6)
            if not code or code in existing:
                continue
            net = _parse_cn_money(row.get("净额"))
            ok = _insert(conn, "公共", "个股资金流向", {
                "交易日期": day, "股票代码": code,
                "交易所": "SH" if code.startswith(("6", "9")) else "SZ",
                "收盘价": _to_n(row.get("最新价")),
                "涨跌幅": _parse_pct(row.get("涨跌幅")),
                "主力净流入": net,
                "主力净占比": None,
                "超大单净流入": None, "超大单净占比": None,
                "大单净流入": None, "大单净占比": None,
                "中单净流入": None, "中单净占比": None,
                "小单净流入": None, "小单净占比": None,
            }, source="akshare-ths")
            if ok:
                count += 1
                existing.add(code)
    except Exception as e:
        last_err = e

    # 2) 东财排行回退
    if count == 0:
        try:
            rank = retry_call(
                lambda: ak.stock_individual_fund_flow_rank(indicator="今日"),
                retries=3,
                delay=2.0,
                what="资金流向排行",
            )
            source_used = "东财排行"
            for _, row in rank.iterrows():
                code = str(row.get("代码", "") or row.get("股票代码", "")).strip().zfill(6)
                if not code or code in existing:
                    continue
                ok = _insert(conn, "公共", "个股资金流向", {
                    "交易日期": day, "股票代码": code,
                    "交易所": "SH" if code.startswith(("6", "9")) else "SZ",
                    "收盘价": _to_n(row.get("最新价") or row.get("收盘价")),
                    "涨跌幅": _parse_pct(row.get("涨跌幅")),
                    "主力净流入": _to_n(row.get("主力净流入-净额") or row.get("主力净流入")),
                    "主力净占比": _to_n(row.get("主力净流入-净占比") or row.get("主力净占比")),
                    "超大单净流入": _to_n(row.get("超大单净流入-净额") or row.get("超大单净流入")),
                    "超大单净占比": _to_n(row.get("超大单净流入-净占比") or row.get("超大单净占比")),
                    "大单净流入": _to_n(row.get("大单净流入-净额") or row.get("大单净流入")),
                    "大单净占比": _to_n(row.get("大单净流入-净占比") or row.get("大单净占比")),
                    "中单净流入": _to_n(row.get("中单净流入-净额") or row.get("中单净流入")),
                    "中单净占比": _to_n(row.get("中单净流入-净占比") or row.get("中单净占比")),
                    "小单净流入": _to_n(row.get("小单净流入-净额") or row.get("小单净流入")),
                    "小单净占比": _to_n(row.get("小单净流入-净占比") or row.get("小单净占比")),
                }, source="akshare")
                if ok:
                    count += 1
                    existing.add(code)
        except Exception as e:
            conn.close()
            raise RuntimeError(
                f"个股资金流向: 即时与排行均失败，水位原={latest}。"
                f"即时错误={last_err}; 排行错误={e}"
            ) from e

    conn.close()
    if count == 0 and last_err and source_used is None:
        raise RuntimeError(f"个股资金流向: 无新增且源失败，水位原={latest}: {last_err}")
    return f"个股资金流向: 新增 {count} 条（{source_used or '无源'}, 水位原={latest}）"

# ── 2. 现金流量表(合并) — 全量 ──
def _get_stock_codes():
    import baostock as bs
    end = date.today().strftime("%Y-%m-%d")
    rs = bs.query_all_stock(day=end)
    stocks = []
    while rs.next():
        s = rs.get_row_data()
        code_full = s[0]
        parts = code_full.split('.')
        if len(parts) >= 2:
            mkt, num = parts[0], parts[1]
            is_stock = (mkt == 'sh' and num[:2] in ('60', '68', '90')) or \
                       (mkt == 'sz' and num[:2] in ('00', '30', '20', '10', '28')) or \
                       (mkt == 'bj' and num[:2] == '92')
            if is_stock:
                stocks.append(code_full)
    return stocks

def _update_cashflow_baostock(updater) -> str:
    bs = baostock_login_or_raise()

    conn = updater._get_conn()
    stocks = _get_stock_codes()
    existing = _existing_key_set(conn, "公共", "现金流量表(合并)", "证券代码", "统计截止日期")

    year = date.today().year
    count = 0
    for i, code_full in enumerate(stocks):
        code = code_full[3:] if len(code_full) > 3 else code_full
        try:
            for yr in [year - 1, year]:
                for q in [1, 2, 3, 4]:
                    rs = bs.query_cash_flow_data(code=code_full, year=yr, quarter=q)
                    while rs.next():
                        r = rs.get_row_data()
                        if not r[2]:
                            continue
                        stat_date = r[2]
                        key = f"{code}|{stat_date}"
                        if key in existing:
                            continue
                        # r 列: code, pubDate, statDate, ... 各现金流项
                        # 根据 baostock 文档，第3列开始是现金流项（不同版本可能不一样）
                        ok = _insert(conn, "公共", "现金流量表(合并)", {
                            "证券代码": code,
                            "统计截止日期": stat_date,
                            "报表类型": str(q),
                            "经营活动产生的现金流量净额": _to_n(r[6]) if len(r) > 6 else None,
                            "投资活动产生的现金流量净额": _to_n(r[9]) if len(r) > 9 else None,
                            "筹资活动产生的现金流量净额": _to_n(r[12]) if len(r) > 12 else None,
                            "现金及现金等价物净增加额": _to_n(r[14]) if len(r) > 14 else None,
                            "销售商品、提供劳务收到的现金": _to_n(r[3]) if len(r) > 3 else None,
                            "收到的其他与经营活动有关的现金": _to_n(r[4]) if len(r) > 4 else None,
                            "购买商品、接受劳务支付的现金": _to_n(r[5]) if len(r) > 5 else None,
                            "支付给职工以及为职工支付的现金": _to_n(r[7]) if len(r) > 7 else None,
                            "支付的各项税费": _to_n(r[8]) if len(r) > 8 else None,
                        })
                        if ok:
                            count += 1
                            existing.add(key)
        except Exception:
            pass
    conn.close()
    bs.logout()
    return f"现金流量表: 新增 {count} 条（全量）"

# ── 3. ETF基金申购赎回清单（按 代码+日期 增量；补默认份额类别避免唯一键/事务问题）──
def update_etf_list(updater) -> str:
    import akshare as ak
    from .数据更新器 import get_watermark

    conn = updater._get_conn()
    with conn.cursor() as cur:
        cur.execute(
            'SELECT DISTINCT "基金代码" || \'|\' || "交易日期"::text FROM "公共"."ETF基金申购赎回清单"'
        )
        existing = {str(r[0]) for r in cur.fetchall()}
    wm = get_watermark(conn, "公共", "ETF基金申购赎回清单")
    latest = wm.get("最后交易日") if wm else None

    try:
        df = retry_call(lambda: ak.fund_etf_spot_em(), retries=3, what="ETF清单")
    except Exception as e:
        conn.close()
        raise RuntimeError(f"ETF清单: {e}") from e

    count = 0
    today = date.today().strftime("%Y-%m-%d")
    for _, row in df.iterrows():
        code = str(row.get("代码", "")).strip()
        if not code:
            continue
        data_date = str(row.get("数据日期", "") or today)[:10]
        if not data_date or data_date.lower() == "nan":
            data_date = today
        key = f"{code}|{data_date}"
        if key in existing:
            continue
        name = str(row.get("名称", "") or "")
        ok = _insert(conn, "公共", "ETF基金申购赎回清单", {
            "基金代码": code,
            "基金简称": name,
            "交易日期": data_date,
            "基金份额类别ID": "",
            "上市交易所": "上交所" if code.startswith(("51", "56", "58")) else "深交所",
            "基金份额净值": _to_n(row.get("最新价") or row.get("IOPV实时估值")),
        })
        if ok:
            count += 1
            existing.add(key)
    conn.close()
    return f"ETF清单: 新增 {count} 条（水位原={latest}）"


# ── 4. 龙虎榜 ──
_龙虎榜显式区间: tuple | None = None


def 设龙虎榜回填区间(起, 止) -> None:
    """供缺口回填使用:让 update_dragon_tiger 按指定区间取数(见 缺口回填.py)。"""
    global _龙虎榜显式区间
    _龙虎榜显式区间 = (起, 止) if 起 and 止 else None


def update_dragon_tiger(updater) -> str:
    import akshare as ak
    from datetime import date, timedelta
    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "龙虎榜", "交易日期", "股票代码")
    count = 0

    if _龙虎榜显式区间 is not None:
        # 缺口回填:一次拉整个区间(东财接口支持 start/end)
        起, 止 = _龙虎榜显式区间
        try:
            df = ak.stock_lhb_detail_em(
                start_date=str(起).replace("-", ""),
                end_date=str(止).replace("-", ""),
            )
        except Exception as e:
            conn.close()
            return f"龙虎榜(回填 {起}~{止}): 接口失败({e})"
        帧列表 = [df]
    else:
        帧列表 = []
        for back in range(10):
            day = date.today() - timedelta(days=back)
            try:
                帧列表.append(ak.stock_lhb_detail_em(
                    start_date=day.strftime("%Y%m%d"),
                    end_date=day.strftime("%Y%m%d"),
                ))
            except Exception:
                帧列表.append(None)

    for df in 帧列表:
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            d = str(row.get("上榜日", ""))[:10]
            code = str(row.get("代码", "")).strip().zfill(6)
            key = f"{d}|{code}"
            if key in existing:
                continue
            ok = _insert(conn, "公共", "龙虎榜", {
                "交易日期": d, "股票代码": code,
                "上榜原因": row.get("解读") or row.get("上榜原因"),
                "收盘价": _to_n(row.get("收盘价")),
                "涨跌幅": _to_n(row.get("涨跌幅")),
                "龙虎榜买入额": _to_n(row.get("龙虎榜买入额")),
                "龙虎榜卖出额": _to_n(row.get("龙虎榜卖出额")),
                "龙虎榜净买额": _to_n(row.get("龙虎榜净买额")),
            })
            if ok:
                count += 1
    conn.close()
    return f"龙虎榜: 新增 {count} 条"

def update_dragon_tiger_dept(updater) -> str:
    """龙虎榜营业部: 新浪机构席位明细(机构专用席位买卖额)。
    东财营业部逐股接口(stock_lhb_stock_detail_em)近期限流, 先用机构席位填充;
    后续东财恢复后可扩展逐股营业部明细。
    """
    import akshare as ak
    conn = updater._get_conn()
    with conn.cursor() as cur:
        cur.execute('SELECT DISTINCT "交易日期"::text || \'|\' || "股票代码" || \'|\' || "营业部名称" FROM "公共"."龙虎榜营业部"')
        existing = {str(r[0]) for r in cur.fetchall()}
    count = 0
    try:
        df = ak.stock_lhb_jgmx_sina()
    except Exception as e:
        conn.close()
        return f"龙虎榜营业部: 接口失败({e})"
    if df is None or len(df) == 0:
        conn.close()
        return "龙虎榜营业部: 无数据"
    for _, row in df.iterrows():
        day = str(row.get("交易日期", ""))[:10]
        code = str(row.get("股票代码", "")).strip().zfill(6)
        buy = _to_n(row.get("机构席位买入额"))
        sell = _to_n(row.get("机构席位卖出额"))
        name = "机构专用"
        key = f"{day}|{code}|{name}"
        if key in existing:
            continue
        direction = "买入" if (buy or 0) >= (sell or 0) else "卖出"
        ok = _insert(conn, "公共", "龙虎榜营业部", {
            "交易日期": day, "股票代码": code,
            "营业部名称": name,
            "买卖方向": direction,
            "买入金额": buy,
            "卖出金额": sell,
            "净额": round((buy or 0) - (sell or 0), 4) if (buy or sell) else None,
        })
        if ok:
            count += 1
    conn.close()
    return f"龙虎榜营业部: 新增 {count} 条（机构席位）"

_ETF两融显式区间: tuple | None = None


def 设ETF两融回填区间(起, 止) -> None:
    """供缺口回填使用:让 update_etf_margin 按指定区间补(见 缺口回填.py)。"""
    global _ETF两融显式区间
    _ETF两融显式区间 = (起, 止) if 起 and 止 else None


def update_etf_margin(updater) -> str:
    """ETF融资融券: 上交所两融明细中过滤 ETF，按最近交易日窗口连续补。"""
    import akshare as ak
    from datetime import timedelta as _td
    from .数据更新器 import get_watermark

    conn = updater._get_conn()
    with conn.cursor() as cur:
        cur.execute(
            'SELECT DISTINCT "交易日期"::text || \'|\' || "基金代码" FROM "公共"."ETF融资融券交易明细表"'
        )
        existing = {str(r[0]) for r in cur.fetchall()}
    wm = get_watermark(conn, "公共", "ETF融资融券交易明细表")
    latest = wm.get("最后交易日") if wm else None

    count = 0
    end_d = date.today()
    if _ETF两融显式区间 is not None:
        # 缺口回填:按登记缺口给出的区间取数
        起, 止 = _ETF两融显式区间
        start_d = date.fromisoformat(str(起)[:10])
        end_d = date.fromisoformat(str(止)[:10])
    # 从水位次日或最近 15 天窗口
    elif latest:
        start_d = latest + _td(days=1)
        if isinstance(start_d, str):
            start_d = date.fromisoformat(start_d[:10])
    else:
        start_d = end_d - _td(days=15)
    days = []
    d = start_d
    while d <= end_d:
        days.append(d)
        d += _td(days=1)
    if not days:
        days = [end_d - _td(days=i) for i in range(5)]

    hard_errors = 0
    for day in days:
        ds = day.strftime("%Y%m%d")
        try:
            df = retry_call(
                lambda d=ds: ak.stock_margin_detail_sse(date=d),
                retries=2,
                delay=1.0,
                what=f"ETF两融:{ds}",
            )
        except Exception:
            hard_errors += 1
            continue
        if df is None or len(df) == 0:
            continue
        for _, row in df.iterrows():
            code = str(row.get("标的证券代码", "") or row.get("证券代码", "")).strip().zfill(6)
            if not code.startswith(("51", "56", "58", "15", "16", "18")):
                continue
            day_s = str(row.get("信用交易日期", "") or row.get("日期", "") or day)[:10]
            if len(day_s) == 8 and day_s.isdigit():
                day_s = f"{day_s[:4]}-{day_s[4:6]}-{day_s[6:]}"
            key = f"{day_s}|{code}"
            if key in existing:
                continue
            ok = _insert(conn, "公共", "ETF融资融券交易明细表", {
                "交易日期": day_s,
                "基金代码": code,
                "基金份额类别ID": "",
                "融资买入额": _to_n(row.get("融资买入额")),
                "融资偿还额": _to_n(row.get("融资偿还额")),
                "融资余额": _to_n(row.get("融资余额")),
                "融券卖出量": _to_n(row.get("融券卖出量")),
                "融券偿还量": _to_n(row.get("融券偿还量")),
                "融券余量": _to_n(row.get("融券余量")),
                "融券余额": _to_n(row.get("融券余额")),
                "融资融券余额": _to_n(row.get("融资融券余额")),
            })
            if ok:
                count += 1
                existing.add(key)
    conn.close()
    if count == 0 and hard_errors and not days:
        raise RuntimeError("ETF融资融券: 无可用交易日数据")
    return f"ETF融资融券: 新增 {count} 条（上交所ETF, 水位原={latest}）"

# ── 5. 原有功能增强 ──
def update_list_status(updater) -> str:
    """上市状态: 优先沪深退市清单 + A股代码表增量；baostock 作可选回退。"""
    try:
        return _update_list_status_akshare(updater)
    except Exception as primary_err:
        try:
            return _update_list_status_baostock(updater)
        except Exception as e:
            raise RuntimeError(
                f"上市状态变更表: akshare与baostock均失败。"
                f"ak={primary_err}; bs={e}"
            ) from e


def _update_list_status_akshare(updater) -> str:
    import akshare as ak

    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "上市状态变更表", "证券代码", "变动公布日期")
    count = 0
    notes = []

    def _add(code: str, pub: str, status: str, reason: str = "") -> None:
        nonlocal count
        code = str(code).strip().zfill(6)
        if not code or not pub:
            return
        # 归一日期
        pub_s = str(pub)[:10].replace("/", "-")
        if len(pub_s) == 8 and pub_s.isdigit():
            pub_s = f"{pub_s[:4]}-{pub_s[4:6]}-{pub_s[6:8]}"
        key = f"{code}|{pub_s}"
        if key in existing:
            return
        ok = _insert(conn, "公共", "上市状态变更表", {
            "证券代码": code,
            "变动公布日期": pub_s,
            "执行日期": pub_s,
            "变动前的上市状态": "",
            "变动后的上市状态": status,
            "变动原因": reason or None,
        }, source="akshare")
        if ok:
            count += 1
            existing.add(key)

    # 沪/深退市
    try:
        sh = retry_call(ak.stock_info_sh_delist, retries=3, delay=1.5, what="沪退市")
        for _, row in sh.iterrows():
            _add(
                row.get("公司代码") or row.get("证券代码"),
                row.get("暂停上市日期") or row.get("终止上市日期") or date.today().isoformat(),
                "退市",
                "上交所退市清单",
            )
        notes.append(f"沪退市源{len(sh)}行")
    except Exception as e:
        notes.append(f"沪退市失败:{e}")

    try:
        sz = retry_call(ak.stock_info_sz_delist, retries=3, delay=1.5, what="深退市")
        for _, row in sz.iterrows():
            _add(
                row.get("证券代码") or row.get("公司代码"),
                row.get("终止上市日期") or row.get("暂停上市日期") or date.today().isoformat(),
                "退市",
                "深交所退市清单",
            )
        notes.append(f"深退市源{len(sz)}行")
    except Exception as e:
        notes.append(f"深退市失败:{e}")

    # 新上市：代码表有、公司文件无
    try:
        codes_df = retry_call(ak.stock_info_a_code_name, retries=3, delay=1.5, what="A股代码表")
        with conn.cursor() as cur:
            cur.execute('SELECT "证券代码" FROM "公共"."公司文件"')
            known = {str(r[0]).strip().zfill(6) for r in cur.fetchall()}
        today_str = date.today().isoformat()
        new_n = 0
        for _, row in codes_df.iterrows():
            code = str(row.get("code") or row.get("证券代码") or "").strip().zfill(6)
            if not code or code in known:
                continue
            _add(code, today_str, "上市", "代码表新增")
            new_n += 1
        notes.append(f"疑似新上市{new_n}")
    except Exception as e:
        notes.append(f"代码表失败:{e}")

    conn.close()
    if count == 0 and all("失败" in n for n in notes if n):
        raise RuntimeError("上市状态变更表: " + "; ".join(notes))
    return f"上市状态变更表: 新增 {count} 条（{'; '.join(notes)}）"


def _update_list_status_baostock(updater) -> str:
    bs = baostock_login_or_raise()
    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "上市状态变更表", "证券代码", "变动公布日期")
    rs = bs.query_all_stock(day=date.today().strftime("%Y-%m-%d"))
    count = 0
    while rs.next():
        s = rs.get_row_data()
        code_full = s[0]
        # 过滤：只保留实际股票
        parts = code_full.split('.')
        if len(parts) >= 2:
            mkt, num = parts[0], parts[1]
            is_stock = (mkt == 'sh' and num[:2] in ('60', '68', '90')) or \
                       (mkt == 'sz' and num[:2] in ('00', '30', '20', '10', '28')) or \
                       (mkt == 'bj' and num[:2] == '92')
        else:
            is_stock = False
        if not is_stock:
            continue
        code = code_full[3:] if len(code_full) > 3 else code_full
        status = {"1": "上市", "0": "退市"}.get(s[2], "未知")
        today_str = date.today().strftime("%Y-%m-%d")
        key = f"{code}|{today_str}"
        if key in existing:
            continue
        ok = _insert(conn, "公共", "上市状态变更表", {
            "证券代码": code, "变动公布日期": today_str,
            "执行日期": today_str,
            "变动前的上市状态": "", "变动后的上市状态": status,
        })
        if ok:
            count += 1
    conn.close()
    bs.logout()
    return f"上市状态变更表: 新增 {count} 条"

def _update_company_info_baostock(updater) -> str:
    bs = baostock_login_or_raise()
    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "公司文件", "证券代码")
    rs = bs.query_all_stock(day=date.today().strftime("%Y-%m-%d"))
    stocks = []
    while rs.next():
        s = rs.get_row_data()
        stocks.append(s[0])
    count = 0
    for code_full in stocks:
        code = code_full[3:] if len(code_full) > 3 else code_full
        if code in existing:
            continue
        try:
            rs2 = bs.query_stock_basic(code=code_full)
            while rs2.next():
                r = rs2.get_row_data()
                ok = _insert(conn, "公共", "公司文件", {
                    "证券代码": code, "证券简称": r[1] if r[1] else None,
                    "上市日期": r[2] if r[2] and r[2] != "" else None,
                    "公司全称": r[3] if len(r) > 3 and r[3] else None,
                    "所属省份": r[4] if len(r) > 4 and r[4] else None,
                    "所属城市": r[5] if len(r) > 5 and r[5] else None,
                    "公司成立日期": r[6] if len(r) > 6 and r[6] else None,
                })
                if ok: count += 1
        except Exception:
            pass
    conn.close()
    bs.logout()
    return f"公司文件: 新增 {count} 条"

def update_dividend(updater) -> str:
    """分配文件: 优先东财分红配送(stock_fhps_em)；baostock 回退。"""
    try:
        return _update_dividend_fhps(updater)
    except Exception as primary_err:
        try:
            return _update_dividend_baostock(updater)
        except Exception as e:
            raise RuntimeError(
                f"分配文件: fhps与baostock均失败。fhps={primary_err}; bs={e}"
            ) from e


def _update_dividend_fhps(updater) -> str:
    import akshare as ak

    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "分配文件", "证券代码", "除权(息)日期")
    count = 0
    periods_ok = 0
    errors = []
    for period in _quarter_ends(6):
        try:
            df = retry_call(
                lambda p=period: ak.stock_fhps_em(date=p),
                retries=2,
                delay=1.5,
                what=f"分红配送{period}",
            )
        except Exception as e:
            errors.append(f"{period}:{e}")
            continue
        periods_ok += 1
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).strip().zfill(6)
            ex = row.get("除权除息日")
            if not code or ex is None or (hasattr(ex, "__float__") and str(ex) == "nan"):
                continue
            if hasattr(ex, "isoformat"):
                ex_s = ex.isoformat()
            else:
                ex_s = str(ex)[:10]
            key = f"{code}|{ex_s}"
            if key in existing:
                continue
            plan = row.get("预案公告日")
            plan_s = plan.isoformat() if hasattr(plan, "isoformat") else (str(plan)[:10] if plan else None)
            reg = row.get("股权登记日")
            reg_s = reg.isoformat() if hasattr(reg, "isoformat") else (str(reg)[:10] if reg else None)
            cash = _to_n(row.get("现金分红-现金分红比例"))
            song = _to_n(row.get("送转股份-送转比例") or row.get("送转股份-送转总比例"))
            zhuan = _to_n(row.get("送转股份-转股比例"))
            year = int(ex_s[:4]) if ex_s and len(ex_s) >= 4 else None
            ok = _insert(conn, "公共", "分配文件", {
                "证券代码": code,
                "除权(息)日期": ex_s,
                "公告日期": plan_s,
                "支付日期": ex_s,
                "分配类型": str(row.get("方案进度") or "")[:32] or None,
                "分配比率": cash,
                "配股价格": None,
                "财政年度": year,
                "预案公告日期": plan_s,
                "送股比(每10股)": song,
                "转增比(每10股)": zhuan,
                "派息比(每10股税前)": cash,
                "股权登记日": reg_s,
                "实施方案内容": str(row.get("方案进度") or "") or None,
            }, source="akshare-fhps")
            if ok:
                count += 1
                existing.add(key)
    conn.close()
    if periods_ok == 0:
        raise RuntimeError("分配文件: fhps 全部报告期失败: " + "; ".join(errors[:3]))
    return f"分配文件: 新增 {count} 条（fhps {periods_ok}期）"


def _update_dividend_baostock(updater) -> str:
    bs = baostock_login_or_raise()
    conn = updater._get_conn()
    stocks = _get_stock_codes()
    existing = _existing_key_set(conn, "公共", "分配文件", "证券代码", "除权(息)日期")
    count = 0
    year = date.today().year
    for code_full in stocks:
        # 只保留实际股票代码（跳过指数/ETF）
        parts = code_full.split('.')
        if len(parts) < 2:
            continue
        mkt, num = parts[0], parts[1]
        # sh.60xxxx, sh.68xxxx, sh.90xxxx | sz.00xxxx, sz.30xxxx, sz.20xxxx | bj.92xxxx
        is_stock = (mkt == 'sh' and num[:2] in ('60', '68', '90')) or \
                   (mkt == 'sz' and num[:2] in ('00', '30', '20', '10', '28')) or \
                   (mkt == 'bj' and num[:2] == '92')
        if not is_stock:
            continue
        code = code_full[3:] if len(code_full) > 3 else code_full
        try:
            for yr in [year - 1, year, year + 1]:
                rs2 = bs.query_dividend_data(code=code_full, year=yr, yearType="report")
                while rs2.next():
                    r = rs2.get_row_data()
                    ex_date = r[7] if len(r) > 7 and r[7] else None
                    if not ex_date:
                        continue
                    key = f"{code}|{ex_date}"
                    if key in existing:
                        continue
                    ok = _insert(conn, "公共", "分配文件", {
                        "证券代码": code,
                        "除权(息)日期": ex_date,
                        "公告日期": r[1] if len(r) > 1 and r[1] else None,
                        "支付日期": r[3] if len(r) > 3 and r[3] else None,
                        "分配类型": r[2] if len(r) > 2 else None,
                        "分配比率": _to_n(r[9]) if len(r) > 9 else None,
                        "配股价格": _to_n(r[11]) if len(r) > 11 else None,
                        "财政年度": yr,
                        "预案公告日期": r[1] if len(r) > 1 and r[1] else None,
                    })
                    if ok:
                        count += 1
                        existing.add(key)
        except Exception:
            pass
    conn.close()
    bs.logout()
    return f"分配文件: 新增 {count} 条"

def update_index_info(updater) -> str:
    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "指数基本信息文件", "交易所指数代码")
    indices = [
        ("000001", "上证综合指数", "1990-12-19"), ("000016", "上证50指数", "2004-01-02"),
        ("000300", "沪深300指数", "2005-04-08"), ("000905", "中证500指数", "2007-01-15"),
        ("000688", "科创50指数", "2020-07-23"), ("399001", "深证成指", "1995-01-23"),
        ("399006", "创业板指", "2010-06-01"), ("399852", "中证1000指数", "2014-10-17"),
    ]
    count = 0
    for code, name, start_date in indices:
        if code in existing:
            continue
        ok = _insert(conn, "公共", "指数基本信息文件", {
            "交易所指数代码": code, "指数名称": name,
            "开始日期": start_date, "基准日期": start_date, "基准点数": 1000,
        })
        if ok:
            count += 1
    conn.close()
    return f"指数基本信息文件: 新增 {count} 条"

def update_market_daily(updater) -> str:
    """按指数写入日市场回报率增量。

    **键修正(TASK-003 D2b)**:历史实现有三个缺陷:
      1. 三个指数(上证综指/深证成指/创业板指)的行**全部标成 市场类型='A股'**,
         导致同一天出现多行同名记录;
      2. 幂等检查按 `交易日期` **单键**,同一天第二个指数必然重复插入;
      3. 取得 `existing` 后**不更新集合**,循环内进一步放大了重复。
    现改为:市场类型 = 指数中文简称;幂等键 = (交易日期, 市场类型) 且随插入同步更新。
    """
    import akshare as ak
    conn = updater._get_conn()
    existing = _existing_composite_set(conn, "公共", "日市场回报率文件", ["交易日期", "市场类型"])
    # 注意:该 helper 返回的是 "日期|市场类型" 形式的字符串集合
    indices = [("sh000001", "上证综指"), ("sz399001", "深证成指"), ("sz399006", "创业板指")]
    count = 0
    for symbol, name in indices:
        try:
            df = ak.stock_zh_index_daily(symbol=symbol)
            for _, row in df.iterrows():
                day = str(row["date"])[:10]
                if f"{day}|{name}" in existing:
                    continue
                close = _to_n(row.get("close"))
                open_p = _to_n(row.get("open"))
                volume = _to_n(row.get("volume"))
                ret = round((close - open_p) / open_p * 100, 4) if (close and open_p and open_p > 0) else None
                ok = _insert(conn, "公共", "日市场回报率文件", {
                    "交易日期": day, "市场类型": name,      # 按指数区分,不再一律 'A股'
                    "日市场交易总股数": volume,
                    "考虑现金红利再投资的日市场回报率(等权平均": ret,
                })
                existing.add(f"{day}|{name}")  # 同步更新,避免同日重复插入
                if ok:
                    count += 1
        except Exception:
            pass
    conn.close()
    return f"日市场回报率: 新增 {count} 条"

def update_report_basic(updater) -> str:
    bs = baostock_login_or_raise()
    conn = updater._get_conn()
    stocks = _get_stock_codes()
    existing = _existing_key_set(conn, "公共", "年、中、季报基本情况文件", "证券代码", "统计截止日期")
    year = date.today().year
    count = 0
    for code_full in stocks:
        code = code_full[3:] if len(code_full) > 3 else code_full
        for yr in [year - 1, year]:
            for quarter in [1, 2, 3, 4]:
                try:
                    rs2 = bs.query_profit_data(code=code_full, year=yr, quarter=quarter)
                    while rs2.next():
                        r = rs2.get_row_data()
                        stat_date = r[2] if r[2] else None
                        if not stat_date:
                            continue
                        key = f"{code}|{stat_date}"
                        if key in existing:
                            continue
                        ok = _insert(conn, "公共", "年、中、季报基本情况文件", {
                            "证券代码": code, "证券简称": r[1] if len(r) > 1 else None,
                            "报告类型": str(quarter),
                            "统计截止日期": stat_date,
                            "报告公布日期": r[1] if len(r) > 1 and r[1] else None,
                        })
                        if ok:
                            count += 1
                            existing.add(key)
                except Exception:
                    pass
    conn.close()
    bs.logout()
    return f"报告基本情况: 新增 {count} 条（全量）"

def update_margin(updater) -> str:
    import akshare as ak
    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "融资融券", "交易日期", "股票代码")
    count = 0
    # 上交所
    try:
        df_sse = ak.stock_margin_sse()
        for _, row in df_sse.iterrows():
            day = str(row.get("信用交易日期", ""))[:10]
            code = "999999"
            key = f"{day}|{code}"
            if key in existing:
                continue
            ok = _insert(conn, "公共", "融资融券", {
                "交易日期": day, "股票代码": code, "交易所": "上交所",
                "融资余额": _to_n(row.get("融资余额")),
                "融资买入额": _to_n(row.get("融资买入额")),
                "融券余量": _to_n(row.get("融券余量")),
                "融券余额": _to_n(row.get("融券余量金额")),
                "融券卖出量": _to_n(row.get("融券卖出量")),
                "融资融券余额": _to_n(row.get("融资融券余额")),
            })
            if ok:
                count += 1
    except Exception:
        pass
    # 深交所
    try:
        df_szse = ak.stock_margin_szse()
        for _, row in df_szse.iterrows():
            day = str(row.get("信用交易日期", ""))[:10]
            code = "999999"
            key = f"{day}|{code}"
            if key not in existing:
                ok = _insert(conn, "公共", "融资融券", {
                    "交易日期": day, "股票代码": code, "交易所": "深交所",
                    "融资余额": _to_n(row.get("融资余额")),
                    "融资买入额": _to_n(row.get("融资买入额")),
                    "融券余量": _to_n(row.get("融券余量")),
                    "融券余额": _to_n(row.get("融券余量金额")),
                    "融券卖出量": _to_n(row.get("融券卖出量")),
                    "融资融券余额": _to_n(row.get("融资融券余额")),
                })
                if ok:
                    count += 1
    except Exception:
        pass
    conn.close()
    return f"融资融券: 新增 {count} 条"

def update_futures_info(updater) -> str:
    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "国内商品期货基本信息表", "交易代码")
    contracts = [
        ("上海期货交易所", "CU", "铜"), ("上海期货交易所", "AL", "铝"),
        ("上海期货交易所", "ZN", "锌"), ("上海期货交易所", "PB", "铅"),
        ("上海期货交易所", "NI", "镍"), ("上海期货交易所", "SN", "锡"),
        ("上海期货交易所", "AU", "黄金"), ("上海期货交易所", "AG", "白银"),
        ("上海期货交易所", "RB", "螺纹钢"), ("上海期货交易所", "HC", "热轧卷板"),
        ("上海期货交易所", "RU", "天然橡胶"), ("上海期货交易所", "BU", "石油沥青"),
        ("上海期货交易所", "SP", "纸浆"), ("上海期货交易所", "SS", "不锈钢"),
        ("大连商品交易所", "A", "豆一"), ("大连商品交易所", "M", "豆粕"),
        ("大连商品交易所", "Y", "豆油"), ("大连商品交易所", "P", "棕榈油"),
        ("大连商品交易所", "C", "玉米"), ("大连商品交易所", "J", "焦炭"),
        ("大连商品交易所", "JM", "焦煤"), ("大连商品交易所", "I", "铁矿石"),
        ("郑州商品交易所", "SR", "白砂糖"), ("郑州商品交易所", "TA", "PTA"),
        ("郑州商品交易所", "MA", "甲醇"), ("郑州商品交易所", "FG", "玻璃"),
        ("郑州商品交易所", "RM", "菜籽粕"),
        ("上海国际能源交易中心", "SC", "原油"),
    ]
    count = 0
    for exchange, code, name in contracts:
        if code in existing:
            continue
        ok = _insert(conn, "公共", "国内商品期货基本信息表", {
            "交易所代码": exchange, "交易代码": code,
            "标的品种": name, "报价单位": "元/吨",
        })
        if ok:
            count += 1
    conn.close()
    return f"期货基本信息: 新增 {count} 条"


def _update_cashflow_em(updater) -> str:
    import akshare as ak
    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "现金流量表(合并)", "证券代码", "统计截止日期")
    count = 0
    today = date.today()
    dates = []
    for y in range(today.year, today.year - 2, -1):
        for md in ("1231", "0930", "0630", "0331"):
            d = date(y, int(md[:2]), int(md[2:]))
            if d <= today:
                dates.append(d.strftime("%Y%m%d"))
    for rd in dates[:4]:
        try:
            df = retry_call(lambda d=rd: ak.stock_xjll_em(date=d), retries=2, what=f"现金流EM:{rd}")
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            code = str(row.get("股票代码", "")).strip().zfill(6)
            stat = f"{rd[:4]}-{rd[4:6]}-{rd[6:]}"
            key = f"{code}|{stat}"
            if not code or key in existing:
                continue
            ok = _insert(conn, "公共", "现金流量表(合并)", {
                "证券代码": code,
                "统计截止日期": stat,
                "经营活动产生的现金流量净额": _to_n(row.get("经营性现金流-现金流量净额")),
                "投资活动产生的现金流量净额": _to_n(row.get("投资性现金流-现金流量净额")),
                "筹资活动产生的现金流量净额": _to_n(row.get("融资性现金流-现金流量净额")),
            }, source="akshare_em")
            if ok:
                count += 1
                existing.add(key)
    conn.close()
    return f"现金流量表(EM): 新增 {count} 条"


def update_cashflow(updater) -> str:
    try:
        return _update_cashflow_baostock(updater)
    except RuntimeError as e:
        if "黑名单" in str(e) or "登录失败" in str(e):
            return _update_cashflow_em(updater) + f"（baostock回退: {e}）"
        raise


def update_company_info(updater) -> str:
    try:
        return _update_company_info_baostock(updater)
    except RuntimeError as e:
        if "黑名单" not in str(e) and "登录失败" not in str(e):
            raise
        import akshare as ak
        conn = updater._get_conn()
        existing = _existing_set(conn, "公共", "公司文件", "证券代码")
        df = retry_call(lambda: ak.stock_info_a_code_name(), retries=3, what="公司代码表")
        count = 0
        for _, row in df.iterrows():
            code = str(row.get("code", "")).strip().zfill(6)
            name = str(row.get("name", "") or "")
            if not code or code in existing:
                continue
            ok = _insert(conn, "公共", "公司文件", {
                "证券代码": code,
                "证券简称": name,
            }, source="akshare")
            if ok:
                count += 1
                existing.add(code)
        conn.close()
        return f"公司文件(代码表回退): 新增 {count} 条（{e}）"
