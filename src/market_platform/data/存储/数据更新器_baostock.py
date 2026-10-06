"""Baostock 数据更新 — 完整字段覆盖重写（登录失败抛错；日行情支持 akshare 回退）"""
from datetime import date, timedelta
from .数据更新器 import (
    _to_n, _to_i, _pct_to_ratio, _insert, _existing_key_set,
    baostock_login_or_raise, retry_call, get_watermark,
)

# ── 1. 个股日行情文件 ──
def update_daily_bars(updater) -> str:
    conn = updater._get_conn()
    latest = None
    wm = get_watermark(conn, "公共", "个股日行情文件")
    if wm and wm.get("最后交易日"):
        latest = wm["最后交易日"]
    if latest is None:
        with conn.cursor() as cur:
            cur.execute('SELECT MAX("交易日期") FROM "公共"."个股日行情文件"')
            latest = cur.fetchone()[0]

    end = date.today()
    if latest is None:
        start = date(2020, 1, 1)
    else:
        start = latest + timedelta(days=1)

    # 水位表/MAX 已到 end 时仍可能有大量标的落后（腾讯回退曾只拉「已到 max 日」子集）
    lagging = 0
    最早落后日 = None
    with conn.cursor() as cur:
        cur.execute(
            '''
            SELECT COUNT(*) FROM "公共"."证券档案" a
            LEFT JOIN (
              SELECT trim("证券代码") AS code, MAX("交易日期") AS max_d
              FROM "公共"."个股日行情文件" GROUP BY 1
            ) b ON trim(a."证券代码") = b.code
            WHERE b.max_d IS NULL OR b.max_d < %s
            ''',
            (end,),
        )
        lagging = int(cur.fetchone()[0] or 0)

        # 覆盖塌陷修正:先看**全表**逐日家数,找出最近一次"家数骤降"的日期。
        # 旧实现只回补最近 10 天,遇到「某些天只落了部分标的」这类塌陷永远补不上,
        # 而全局 MAX 仍是最新,水位检查也发现不了(2026-07-27 的 5608→1994 即此类)。
        cur.execute(
            '''
            SELECT min(交易日期) FROM (
              SELECT "交易日期", count(*) AS n
              FROM "公共"."个股日行情文件"
              WHERE "交易日期" > %s
              GROUP BY 1
              HAVING count(*) < (SELECT 0.9 * max(n) FROM (
                  SELECT count(*) AS n FROM "公共"."个股日行情文件"
                  WHERE "交易日期" > %s GROUP BY "交易日期") x)
            ) y
            ''',
            (end - timedelta(days=400), end - timedelta(days=400)),
        )
        行 = cur.fetchone()
        最早落后日 = 行[0] if 行 else None

    # 「应补到哪天」= 最近一个有股票列表的交易日(set 到假期就会前移),
    # 不能直接用 end=今天:国庆/春节等长假里 今天 不是交易日,会导致 start > 目标日
    # 从而让 baostock 对每只股票都报「起始日期大于终止日期」。
    目标日 = end
    try:
        _bs = baostock_login_or_raise()
        for back in range(0, 10):
            d = end - timedelta(days=back)
            rs = _bs.query_all_stock(day=d.strftime("%Y-%m-%d"))
            有 = False
            while rs.next():
                有 = True
                break
            if 有:
                目标日 = d
                break
        _bs.logout()
    except Exception:
        目标日 = end

    if start > 目标日 and lagging == 0 and 最早落后日 is None:
        conn.close()
        return f"日行情已最新（最新: {latest}）"

    if start > 目标日 or lagging > 0 or 最早落后日 is not None:
        # 全局 MAX 已最新,但存在落后标的或覆盖塌陷:从**最早的塌陷/落后日**往前补
        候选 = [d for d in (最早落后日, 目标日 - timedelta(days=10)) if d is not None]
        start = min(候选 + [start]) if start <= 目标日 else min(候选)
        # 上限保护:单次最多回补 240 天,避免误触发全量重拉
        start = max(start, 目标日 - timedelta(days=240))
        print(
            f"[覆盖回补] 落后标的 {lagging} 只,最早塌陷日 {最早落后日},"
            f"目标日 {目标日},本次回补区间 {start} → {目标日}",
            flush=True,
        )

    # 优先 baostock；黑名单/登录失败则走腾讯行情回退，连续补到 end
    try:
        msg = _daily_bars_baostock(conn, start, end)
        conn.close()
        return msg
    except RuntimeError as e:
        if "黑名单" not in str(e) and "登录失败" not in str(e):
            conn.close()
            raise
        msg = _daily_bars_akshare_tx(conn, start, end)
        conn.close()
        return f"{msg}（baostock不可用已回退腾讯: {e}）"


def _daily_bars_baostock(conn, start: date, end: date) -> str:
    bs = baostock_login_or_raise()
    start_s = start.strftime("%Y-%m-%d")
    stocks = []
    stock_day = end
    for back in range(0, 7):
        d = end - timedelta(days=back)
        rs = bs.query_all_stock(day=d.strftime("%Y-%m-%d"))
        stocks = []
        while rs.next():
            stocks.append(rs.get_row_data()[0])
        if stocks:
            stock_day = d
            break
    if not stocks:
        bs.logout()
        raise RuntimeError("未获取到股票列表")

    updated = 0
    errors = 0
    total = len(stocks)
    for i, code in enumerate(stocks):
        parts = code.split(".")
        if len(parts) < 2:
            continue
        mkt, num = parts[0], parts[1]
        is_stock = (mkt == "sh" and num[:2] in ("60", "68", "90")) or \
                   (mkt == "sz" and num[:2] in ("00", "30", "20", "10", "28")) or \
                   (mkt == "bj" and num[:2] == "92")
        if not is_stock:
            continue
        try:
            krs = bs.query_history_k_data_plus(
                code,
                "date,open,high,low,close,preclose,volume,amount,"
                "adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,isST",
                start_date=start_s,
                end_date=stock_day.strftime("%Y-%m-%d"),
                frequency="d",
                adjustflag="3",
            )
            while krs.next():
                r = krs.get_row_data()
                if not (r[0] and r[4]):
                    continue
                code_short = code[3:] if len(code) > 3 else code
                ok = _insert(conn, "公共", "个股日行情文件", {
                    "证券代码": code_short, "交易日期": r[0],
                    "开盘价": _to_n(r[1]), "最高价": _to_n(r[2]),
                    "最低价": _to_n(r[3]), "收盘价": _to_n(r[4]),
                    "昨收盘": _to_n(r[5]) if len(r) > 5 else None,
                    "成交量(股)": _to_i(r[6]) if len(r) > 6 else None,
                    "成交金额(元)": _to_n(r[7]) if len(r) > 7 else None,
                    "涨跌幅": _pct_to_ratio(r[11]) if len(r) > 11 else None,   # baostock pctChg 是百分数
                }, source="baostock")
                if ok:
                    updated += 1
        except Exception:
            errors += 1
    bs.logout()
    return f"日行情: 处理 {total} 只, 新增 {updated} 条, 失败 {errors} 只 ({start}→{stock_day})"


def _code_to_tx_symbol(code: str) -> str | None:
    code = str(code).strip().zfill(6)
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "1", "2", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return None


def _daily_bars_akshare_tx(conn, start: date, end: date) -> str:
    """腾讯日K回退：用库内最近一日成分连续补缺口。"""
    import akshare as ak
    import time

    # 只拉水位落后的标的，避免「已到 max 日的子集」把深市等永久漏掉
    with conn.cursor() as cur:
        cur.execute(
            '''
            SELECT a."证券代码"
            FROM "公共"."证券档案" a
            LEFT JOIN (
              SELECT trim("证券代码") AS code, MAX("交易日期") AS max_d
              FROM "公共"."个股日行情文件"
              GROUP BY 1
            ) b ON trim(a."证券代码") = b.code
            WHERE b.max_d IS NULL OR b.max_d < %s
            ORDER BY a."证券代码"
            ''',
            (end,),
        )
        codes = [str(r[0]).strip() for r in cur.fetchall() if r[0]]
    if not codes:
        # 兜底：全市场档案
        with conn.cursor() as cur:
            cur.execute('SELECT "证券代码" FROM "公共"."证券档案"')
            codes = [str(r[0]).strip() for r in cur.fetchall() if r[0]]
    if not codes:
        raise RuntimeError("无股票列表可用于日行情回退")

    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    updated = 0
    errors = 0
    for i, code in enumerate(codes):
        sym = _code_to_tx_symbol(code)
        if not sym:
            continue
        try:
            df = retry_call(
                lambda s=sym: ak.stock_zh_a_hist_tx(
                    symbol=s, start_date=start_s, end_date=end_s, adjust=""
                ),
                retries=2,
                delay=0.8,
                what=f"腾讯日K:{sym}",
            )
            if df is None or len(df) == 0:
                continue
            for _, row in df.iterrows():
                day = str(row.get("date", ""))[:10]
                if not day:
                    continue
                ok = _insert(conn, "公共", "个股日行情文件", {
                    "证券代码": code.zfill(6),
                    "交易日期": day,
                    "开盘价": _to_n(row.get("open")),
                    "最高价": _to_n(row.get("high")),
                    "最低价": _to_n(row.get("low")),
                    "收盘价": _to_n(row.get("close")),
                    "成交金额(元)": _to_n(row.get("amount")),
                }, source="akshare_tx")
                if ok:
                    updated += 1
        except Exception:
            errors += 1
        if (i + 1) % 50 == 0:
            time.sleep(1)
            print(f"  腾讯日K进度 {i+1}/{len(codes)} 新增 {updated}", flush=True)
    return f"日行情(腾讯): 处理 {len(codes)} 只, 新增 {updated} 条, 失败 {errors} 只 ({start}→{end})"

# ── 2. 上市公司基本信息年度表 ──
def update_stock_list(updater) -> str:
    bs = baostock_login_or_raise()

    end = date.today().strftime("%Y-%m-%d")
    rs = bs.query_all_stock(day=end)

    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "上市公司基本信息年度表", "股票代码")
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
        if code in existing:
            continue
        ok = _insert(conn, "公共", "上市公司基本信息年度表", {
            "股票代码": code,
            "统计截止日期": end,
            "上市状态": {"1": "上市", "0": "退市"}.get(s[2], "未知"),
        })
        if ok:
            count += 1
    conn.close()
    bs.logout()
    return f"股票列表: 新增 {count} 条"

# ── 3. 日历文件 ──
def update_trade_cal(updater) -> str:
    bs = baostock_login_or_raise()

    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "日历文件", "日历日期")
    year = date.today().year
    rs = bs.query_trade_dates(start_date=f"{year-1}-01-01", end_date=f"{year+1}-12-31")
    count = 0
    while rs.next():
        d = rs.get_row_data()
        day = d[0]
        if day in existing:
            continue
        ok = _insert(conn, "公共", "日历文件", {
            "日历日期": day,
            "市场类型": "A股",
            "开市情况": "是" if d[1] == "1" else "否",
        })
        if ok:
            count += 1
    conn.close()
    bs.logout()
    return f"日历: 新增 {count} 个交易日"

# ── 4. 利润表（全量） ──
def _update_financial_table(updater, schema, table, query_fn, code, year, quarter, map_fn):
    """通用的财务表更新函数"""
    rs = query_fn(code=code, year=year, quarter=quarter)
    rows = []
    while rs.next():
        r = rs.get_row_data()
        if r[2]:  # statDate
            rows.append(r)
    return rows

def _get_stock_codes():
    import baostock as bs
    end = date.today().strftime("%Y-%m-%d")
    rs = bs.query_all_stock(day=end)
    stocks = []
    while rs.next():
        s = rs.get_row_data()
        stocks.append(s[0])
    return stocks

def _update_income_baostock(updater) -> str:
    bs = baostock_login_or_raise()

    conn = updater._get_conn()
    stocks = _get_stock_codes()
    existing = _existing_key_set(conn, "公共", "利润表", "证券代码", "统计截止日期")

    year = date.today().year
    count = 0
    for i, code_full in enumerate(stocks):
        code = code_full[3:] if len(code_full) > 3 else code_full
        try:
            for yr in [year - 1, year]:
                for q in [1, 2, 3, 4]:
                    rs = bs.query_profit_data(code=code_full, year=yr, quarter=q)
                    while rs.next():
                        r = rs.get_row_data()
                        if not r[2]:
                            continue
                        stat_date = r[2]
                        key = f"{code}|{stat_date}"
                        if key in existing:
                            continue
                        ok = _insert(conn, "公共", "利润表", {
                            "证券代码": code,
                            "统计截止日期": stat_date,
                            "报表类型": str(q),
                            "营业总收入": _to_n(r[6]) if len(r) > 6 else None,
                            "营业收入": _to_n(r[6]) if len(r) > 6 else None,
                            "营业总成本": _to_n(r[7]) if len(r) > 7 else None,
                            "营业利润": _to_n(r[8]) if len(r) > 8 else None,
                            "利润总额": _to_n(r[9]) if len(r) > 9 else None,
                            "净利润": _to_n(r[10]) if len(r) > 10 else None,
                            "归属于母公司所有者的净利润": _to_n(r[11]) if len(r) > 11 else None,
                            "基本每股收益": _to_n(r[3]) if len(r) > 3 else None,
                            "稀释每股收益": _to_n(r[4]) if len(r) > 4 else None,
                            "研发费用": _to_n(r[17]) if len(r) > 17 else None,
                            "其他收益": _to_n(r[20]) if len(r) > 20 else None,
                            "投资收益": _to_n(r[14]) if len(r) > 14 else None,
                            "公允价值变动收益": _to_n(r[15]) if len(r) > 15 else None,
                            "资产减值损失": _to_n(r[16]) if len(r) > 16 else None,
                        })
                        if ok:
                            count += 1
                            existing.add(key)
        except Exception:
            pass
    conn.close()
    bs.logout()
    return f"利润表: 新增 {count} 条（全量）"

# ── 5. 资产负债表（全量） ──
def _update_balance_baostock(updater) -> str:
    bs = baostock_login_or_raise()

    conn = updater._get_conn()
    stocks = _get_stock_codes()
    existing = _existing_key_set(conn, "公共", "资产负债表", "证券代码", "统计截止日期")

    year = date.today().year
    count = 0
    for i, code_full in enumerate(stocks):
        code = code_full[3:] if len(code_full) > 3 else code_full
        try:
            for yr in [year - 1, year]:
                for q in [1, 2, 3, 4]:
                    rs = bs.query_balance_data(code=code_full, year=yr, quarter=q)
                    while rs.next():
                        r = rs.get_row_data()
                        if not r[2]:
                            continue
                        stat_date = r[2]
                        key = f"{code}|{stat_date}"
                        if key in existing:
                            continue
                        ok = _insert(conn, "公共", "资产负债表", {
                            "证券代码": code,
                            "统计截止日期": stat_date,
                            "报表类型": str(q),
                            "货币资金": _to_n(r[3]) if len(r) > 3 else None,
                            "交易性金融资产": _to_n(r[4]) if len(r) > 4 else None,
                            "应收票据净额": _to_n(r[5]) if len(r) > 5 else None,
                            "应收账款净额": _to_n(r[6]) if len(r) > 6 else None,
                            "预付款项净额": _to_n(r[7]) if len(r) > 7 else None,
                            "其他应收款净额": _to_n(r[8]) if len(r) > 8 else None,
                            "存货净额": _to_n(r[9]) if len(r) > 9 else None,
                            "流动资产合计": _to_n(r[10]) if len(r) > 10 else None,
                            "固定资产净额": _to_n(r[11]) if len(r) > 11 else None,
                            "无形资产净额": _to_n(r[12]) if len(r) > 12 else None,
                            "资产总计": _to_n(r[13]) if len(r) > 13 else None,
                            "短期借款": _to_n(r[14]) if len(r) > 14 else None,
                            "应付票据": _to_n(r[15]) if len(r) > 15 else None,
                            "应付账款": _to_n(r[16]) if len(r) > 16 else None,
                            "预收款项": _to_n(r[17]) if len(r) > 17 else None,
                            "应付职工薪酬": _to_n(r[18]) if len(r) > 18 else None,
                            "应交税费": _to_n(r[19]) if len(r) > 19 else None,
                            "流动负债合计": _to_n(r[20]) if len(r) > 20 else None,
                            "长期借款": _to_n(r[21]) if len(r) > 21 else None,
                            "负债合计": _to_n(r[22]) if len(r) > 22 else None,
                            "实收资本(或股本)": _to_n(r[23]) if len(r) > 23 else None,
                            "资本公积": _to_n(r[24]) if len(r) > 24 else None,
                            "盈余公积": _to_n(r[25]) if len(r) > 25 else None,
                            "未分配利润": _to_n(r[26]) if len(r) > 26 else None,
                            "归属于母公司所有者权益合计": _to_n(r[27]) if len(r) > 27 else None,
                            "所有者权益合计": _to_n(r[28]) if len(r) > 28 else None,
                            "负债与所有者权益总计": _to_n(r[29]) if len(r) > 29 else None,
                        })
                        if ok:
                            count += 1
                            existing.add(key)
        except Exception:
            pass
    conn.close()
    bs.logout()
    return f"资产负债表: 新增 {count} 条（全量）"


def _recent_report_dates(n=4):
    today = date.today()
    out = []
    for y in range(today.year, today.year - 2, -1):
        for md in ("1231", "0930", "0630", "0331"):
            d = date(y, int(md[:2]), int(md[2:]))
            if d <= today:
                out.append(d.strftime("%Y%m%d"))
    return out[:n]


def _update_income_em(updater) -> str:
    import akshare as ak
    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "利润表", "证券代码", "统计截止日期")
    count = 0
    for rd in _recent_report_dates():
        try:
            df = retry_call(lambda d=rd: ak.stock_lrb_em(date=d), retries=2, what=f"利润表EM:{rd}")
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            code = str(row.get("股票代码", "")).strip().zfill(6)
            # 用报告期而不是公告日期作为统计截止
            stat = f"{rd[:4]}-{rd[4:6]}-{rd[6:]}"
            key = f"{code}|{stat}"
            if not code or key in existing:
                continue
            ok = _insert(conn, "公共", "利润表", {
                "证券代码": code,
                "统计截止日期": stat,
                "净利润": _to_n(row.get("净利润")),
                "营业总收入": _to_n(row.get("营业总收入")),
                "营业利润": _to_n(row.get("营业利润")),
                "利润总额": _to_n(row.get("利润总额")),
            }, source="akshare_em")
            if ok:
                count += 1
                existing.add(key)
    conn.close()
    return f"利润表(EM): 新增 {count} 条"


def _update_balance_em(updater) -> str:
    import akshare as ak
    conn = updater._get_conn()
    existing = _existing_key_set(conn, "公共", "资产负债表", "证券代码", "统计截止日期")
    count = 0
    for rd in _recent_report_dates():
        try:
            df = retry_call(lambda d=rd: ak.stock_zcfz_em(date=d), retries=2, what=f"资产负债EM:{rd}")
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
            ok = _insert(conn, "公共", "资产负债表", {
                "证券代码": code,
                "统计截止日期": stat,
                "货币资金": _to_n(row.get("资产-货币资金")),
                "应收账款净额": _to_n(row.get("资产-应收账款")),
                "存货净额": _to_n(row.get("资产-存货")),
                "资产总计": _to_n(row.get("资产-总资产")),
                "应付账款": _to_n(row.get("负债-应付账款")),
                "预收款项": _to_n(row.get("负债-预收账款")),
                "负债合计": _to_n(row.get("负债-总负债")),
                "所有者权益合计": _to_n(row.get("股东权益合计")),
            }, source="akshare_em")
            if ok:
                count += 1
                existing.add(key)
    conn.close()
    return f"资产负债表(EM): 新增 {count} 条"


def update_income(updater) -> str:
    try:
        return _update_income_baostock(updater)
    except RuntimeError as e:
        if "黑名单" in str(e) or "登录失败" in str(e):
            return _update_income_em(updater) + f"（baostock回退: {e}）"
        raise


def update_balance(updater) -> str:
    try:
        return _update_balance_baostock(updater)
    except RuntimeError as e:
        if "黑名单" in str(e) or "登录失败" in str(e):
            return _update_balance_em(updater) + f"（baostock回退: {e}）"
        raise

