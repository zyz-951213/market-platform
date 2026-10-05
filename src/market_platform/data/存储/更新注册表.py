"""更新函数注册表：登记.更新函数 → callable。

字段映射/缺列计算在各 update_* 函数内完成；此处只做调度绑定。
"""
from __future__ import annotations

from typing import Callable

from . import 数据更新器_akshare as ak_u
from . import 数据更新器_baostock as bs_u
from . import 数据更新器_补充 as sup_u
from . import 数据更新器_项目 as proj_u

# key 必须与 行情增量.数据集登记.更新函数 一致
UPDATE_REGISTRY: dict[str, Callable] = {
    "baostock.update_daily_bars": bs_u.update_daily_bars,
    "baostock.update_income": bs_u.update_income,
    "baostock.update_balance": bs_u.update_balance,
    "akshare.update_index_daily": ak_u.update_index_daily,
    "akshare.update_exchange_rate": ak_u.update_exchange_rate,
    "akshare.update_futures_daily": ak_u.update_futures_daily,
    "akshare.update_risk_free": ak_u.update_risk_free,
    "akshare.update_money_supply": ak_u.update_money_supply,
    "akshare.update_cpi": ak_u.update_cpi,
    "akshare.update_pmi": ak_u.update_pmi,
    "akshare.update_gdp": ak_u.update_gdp,
    "akshare.update_fixed_invest": ak_u.update_fixed_invest,
    "akshare.update_ppi": ak_u.update_ppi,
    "akshare.update_reserve_rate": ak_u.update_reserve_rate,
    "akshare.update_retail_sales": ak_u.update_retail_sales,
    "akshare.update_industrial_output": ak_u.update_industrial_output,
    "akshare.update_open_market": ak_u.update_open_market,
    "akshare.update_report_dates": ak_u.update_report_dates,
    "补充.update_market_daily": sup_u.update_market_daily,
    "补充.update_money_flow": sup_u.update_money_flow,
    "补充.update_margin": sup_u.update_margin,
    "补充.update_dragon_tiger": sup_u.update_dragon_tiger,
    "补充.update_dragon_tiger_dept": sup_u.update_dragon_tiger_dept,
    "补充.update_etf_list": sup_u.update_etf_list,
    "补充.update_etf_margin": sup_u.update_etf_margin,
    "补充.update_company_info": sup_u.update_company_info,
    "补充.update_list_status": sup_u.update_list_status,
    "补充.update_dividend": sup_u.update_dividend,
    "补充.update_index_info": sup_u.update_index_info,
    "补充.update_cashflow": sup_u.update_cashflow,
    "项目.update_behavior_daily": proj_u.update_behavior_daily,
    "项目.update_predictor_series": proj_u.update_predictor_series,
    "项目.update_latent_bars": proj_u.update_latent_bars,
}


def resolve(fn_key: str | None) -> Callable | None:
    if not fn_key:
        return None
    return UPDATE_REGISTRY.get(fn_key)
