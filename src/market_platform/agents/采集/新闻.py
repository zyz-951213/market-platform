from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from urllib.parse import urlencode

import numpy as np
import pandas as pd
from market_platform.agents.settings import DataConfig

logger = logging.getLogger(__name__)

_EASTMONEY_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
_EASTMONEY_HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9",
    "referer": "https://so.eastmoney.com/news/s",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _normalize_symbol(symbol: str) -> str:
    return symbol.replace(".SH", "").replace(".SZ", "").replace("sh", "").replace("sz", "")


def _parse_jsonp(text: str) -> dict:
    match = re.search(r"\((\{.*\})\)\s*;?\s*$", text.strip(), flags=re.DOTALL)
    if match:
        return json.loads(match.group(1))
    start = text.find("(")
    end = text.rfind(")")
    if start == -1 or end <= start:
        raise ValueError("invalid jsonp response")
    return json.loads(text[start + 1 : end])


def _strip_html_emphasis(series: pd.Series) -> pd.Series:
    out = series.astype(str)
    for pattern, repl, use_regex in (
        (r"\(<em>", "", True),
        (r"</em>\)", "", True),
        (r"<em>", "", True),
        (r"</em>", "", True),
    ):
        out = out.str.replace(pattern, repl, regex=use_regex)
    out = out.str.replace("\u3000", "", regex=False)
    out = out.str.replace("\r\n", " ", regex=False)
    return out


def fetch_eastmoney_stock_news(symbol: str, page_size: int = 50) -> pd.DataFrame:
    """直接调用东方财富新闻 API（修复 akshare 中无效正则）。"""
    inner_param = {
        "uid": "",
        "keyword": symbol,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": page_size,
                "preTag": "<em>",
                "postTag": "</em>",
            }
        },
    }
    params = {
        "cb": "jQuery_callback",
        "param": json.dumps(inner_param, ensure_ascii=False),
        "_": str(int(time.time() * 1000)),
    }
    query = urlencode(params)
    url = f"{_EASTMONEY_NEWS_URL}?{query}"
    req = urllib.request.Request(url, headers=_EASTMONEY_HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data_json = _parse_jsonp(resp.read().decode("utf-8"))

    articles = data_json.get("result", {}).get("cmsArticleWebOld") or []
    if not articles:
        return pd.DataFrame()

    df = pd.DataFrame(articles)
    df["url"] = "http://finance.eastmoney.com/a/" + df["code"].astype(str) + ".html"
    rename = {
        "date": "published_at",
        "mediaName": "source",
        "title": "title",
        "content": "content",
        "url": "url",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "title" in df.columns:
        df["title"] = _strip_html_emphasis(df["title"])
    if "content" in df.columns:
        df["content"] = _strip_html_emphasis(df["content"])
    return df


def generate_demo_news(dates: pd.Series, symbol: str = "000001", seed: int = 99) -> pd.DataFrame:
    """生成与交易日对齐的合成新闻标题。"""
    rng = np.random.default_rng(seed)
    templates = [
        "{sym} 获机构调研，市场关注度升温",
        "银行板块午后拉升，{sym} 涨幅居前",
        "宏观流动性预期改善，金融股受益",
        "市场震荡整理，{sym} 成交量萎缩",
        "北向资金净流入，权重股受青睐",
        "政策面释放积极信号，风险偏好回升",
        "短线资金快进快出，题材股分化加剧",
    ]
    rows: list[dict] = []
    for d in sorted(dates.unique()):
        n = int(rng.integers(2, 5))
        for _ in range(n):
            title = rng.choice(templates).format(sym=symbol)
            rows.append(
                {
                    "date": pd.to_datetime(d).date(),
                    "title": title,
                    "source": "demo",
                    "symbol": symbol,
                }
            )
    return pd.DataFrame(rows)


class NewsFetcher:
    """个股新闻拉取（东方财富）。"""

    def __init__(self, config: DataConfig, sleep_seconds: float = 0.5):
        self.config = config
        self.sleep_seconds = sleep_seconds

    def fetch_stock_news(self) -> pd.DataFrame:
        symbol = _normalize_symbol(self.config.symbol)
        logger.info("Fetching stock news for %s", symbol)
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)

        df = pd.DataFrame()
        try:
            df = fetch_eastmoney_stock_news(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("eastmoney news API failed: %s", exc)
            try:
                import akshare as ak

                df = ak.stock_news_em(symbol=symbol)
                rename = {
                    "新闻标题": "title",
                    "新闻内容": "content",
                    "发布时间": "published_at",
                    "文章来源": "source",
                    "新闻链接": "url",
                }
                df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
            except Exception as ak_exc:  # noqa: BLE001
                logger.warning("akshare stock_news_em fallback failed: %s", ak_exc)
                return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        if "published_at" in df.columns:
            df["date"] = pd.to_datetime(df["published_at"]).dt.date
        elif "发布时间" in df.columns:
            df["date"] = pd.to_datetime(df["发布时间"]).dt.date
        else:
            df["date"] = pd.Timestamp.today().date()

        df["symbol"] = symbol
        start = pd.to_datetime(self.config.start_date).date()
        df = df[df["date"] >= start]
        keep = [c for c in ("date", "title", "content", "source", "url", "symbol") if c in df.columns]
        return df[keep].sort_values("date").reset_index(drop=True)


def news_for_date(news_df: pd.DataFrame, date_val, max_items: int = 5) -> list[str]:
    """取某日新闻标题列表。"""
    if news_df is None or news_df.empty:
        return []
    day = news_df.copy()
    day["date"] = pd.to_datetime(day["date"]).dt.date
    target = pd.to_datetime(date_val).date()
    titles = day.loc[day["date"] == target, "title"].head(max_items).tolist()
    return [str(t) for t in titles if pd.notna(t)]
