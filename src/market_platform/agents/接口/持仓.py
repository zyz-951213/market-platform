"""用户实盘持仓录入与调仓提醒。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from market_platform.agents.settings import AppConfig, NotifyConfig
from market_platform.agents.分析.信号.每日 import classify_signal_action
from market_platform.agents.存储 import data_access as store
from market_platform.agents.核心.路径.项目 import data_dir, profile_path
from . import 组合 as portfolio_api
from market_platform.agents.核心.通知.分发 import dispatch_notification

HOLDINGS_STATE_KEY = "user_holdings_v1"
WEIGHT_DELTA_THRESHOLD = 0.02
TargetSource = Literal["signals", "portfolio"]


def _holdings_file() -> Path:
    return data_dir() / "user_holdings.json"


def _default_doc() -> dict[str, Any]:
    return {
        "updated_at": None,
        "target_source": "signals",
        "target_portfolio_id": "",
        "cash_ratio": 0.0,
        "note": "",
        "positions": [],
    }


def load_holdings_doc() -> dict[str, Any]:
    payload = store.load_app_state(HOLDINGS_STATE_KEY)
    if isinstance(payload, dict) and payload.get("positions") is not None:
        return {**_default_doc(), **payload}
    path = _holdings_file()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {**_default_doc(), **raw}
        except json.JSONDecodeError:
            pass
    return _default_doc()


def save_holdings_doc(doc: dict[str, Any]) -> dict[str, Any]:
    out = {**_default_doc(), **doc}
    out["updated_at"] = datetime.now().isoformat(timespec="seconds")
    positions = out.get("positions") or []
    cleaned = []
    for row in positions:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol", "")).strip().zfill(6)
        if not sym.isdigit() or len(sym) != 6:
            continue
        weight = float(row.get("weight", 0) or 0)
        if weight < 0:
            weight = 0.0
        cleaned.append(
            {
                "symbol": sym,
                "name": str(row.get("name", "") or ""),
                "weight": weight,
                "quantity": row.get("quantity"),
                "cost": row.get("cost"),
            }
        )
    out["positions"] = cleaned
    store.save_app_state(HOLDINGS_STATE_KEY, out)
    path = _holdings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _symbol_meta(sym: str) -> dict[str, str]:
    from market_platform.agents.采集.名称 import build_symbol_name_map

    summary = store.load_symbol_summary()
    if not summary.empty:
        hit = summary[summary["symbol"].astype(str).str.zfill(6) == sym]
        if not hit.empty:
            row = hit.iloc[0]
            name = str(row.get("name", "") or "").strip()
            sector = str(row.get("sector", "") or "")
            if not name:
                name = build_symbol_name_map([sym], fetch_missing=True, persist=True).get(sym, "")
            return {"name": name, "sector": sector}
    name = build_symbol_name_map([sym], fetch_missing=True, persist=True).get(sym, "")
    return {"name": name, "sector": ""}


def _latest_signal(sym: str) -> dict[str, Any]:
    states = store.load_market_states(sym)
    if states.empty:
        return {
            "latest_state": "",
            "suggested_position": 0.0,
            "raw_suggested_position": 0.0,
            "prev_position": 0.0,
            "rebalance_action": "无数据",
        }
    states = states.sort_values("date")
    latest = states.iloc[-1]
    prev = states.iloc[-2] if len(states) >= 2 else latest
    state = str(latest.get("market_state", "") or "")
    pos = float(latest.get("suggested_position", 0) or 0)
    raw = float(latest.get("raw_suggested_position", pos) or pos)
    prev_pos = float(prev.get("suggested_position", pos) or pos)
    prev_state = str(prev.get("market_state", state) or state)
    return {
        "latest_state": state,
        "suggested_position": pos,
        "raw_suggested_position": raw,
        "prev_position": prev_pos,
        "rebalance_action": classify_signal_action(prev_state, state, prev_pos, pos),
        "signal_date": str(latest.get("date", "")),
    }


def _weight_action(delta: float) -> str:
    if delta >= WEIGHT_DELTA_THRESHOLD:
        return "增持"
    if delta <= -WEIGHT_DELTA_THRESHOLD:
        return "减持"
    return "持有"


def _target_weights(
    symbols: list[str],
    *,
    target_source: str,
    portfolio_id: str,
    output_root: Path | None,
) -> dict[str, float]:
    if target_source == "portfolio" and portfolio_id:
        try:
            meta = portfolio_api.load_portfolio(portfolio_id, output_root)
            return {
                str(h["symbol"]).zfill(6): float(h.get("weight", 0) or 0)
                for h in meta.get("holdings", [])
                if h.get("symbol")
            }
        except FileNotFoundError:
            pass

    raw: dict[str, float] = {}
    for sym in symbols:
        sig = _latest_signal(sym)
        raw[sym] = max(float(sig.get("suggested_position", 0) or 0), 0.0)
    total = sum(raw.values())
    if total <= 0:
        n = len(symbols) or 1
        return {sym: 1.0 / n for sym in symbols}
    return {sym: v / total for sym, v in raw.items()}


def analyze_rebalance(
    doc: dict[str, Any] | None = None,
    *,
    output_root: Path | None = None,
) -> dict[str, Any]:
    doc = doc or load_holdings_doc()
    positions = doc.get("positions") or []
    if not positions:
        return {
            "updated_at": doc.get("updated_at"),
            "target_source": doc.get("target_source", "signals"),
            "target_portfolio_id": doc.get("target_portfolio_id", ""),
            "cash_ratio": float(doc.get("cash_ratio", 0) or 0),
            "execution_note": "T 日收盘信号 → T+1 执行",
            "summary": "尚未录入实盘持仓。请在「持仓」页添加股票与权重后查看调仓建议。",
            "stats": {"positions_count": 0, "tracked_symbols": 0, "change_count": 0, "current_invested": 0, "target_invested": 0},
            "rows": [],
            "changes": [],
        }

    current = {str(p["symbol"]).zfill(6): float(p.get("weight", 0) or 0) for p in positions}
    target_source = str(doc.get("target_source", "signals") or "signals")
    portfolio_id = str(doc.get("target_portfolio_id", "") or "")
    targets: dict[str, float] = {}

    if target_source == "portfolio" and portfolio_id:
        try:
            meta = portfolio_api.load_portfolio(portfolio_id, output_root)
            targets = {
                str(h["symbol"]).zfill(6): float(h.get("weight", 0) or 0)
                for h in meta.get("holdings", [])
                if h.get("symbol")
            }
        except FileNotFoundError:
            targets = {}

    if not targets:
        symbols_for_signal = sorted(current)
        raw = {sym: max(float(_latest_signal(sym).get("suggested_position", 0) or 0), 0.0) for sym in symbols_for_signal}
        total = sum(raw.values()) or 1.0
        targets = {sym: v / total for sym, v in raw.items()}

    symbols = sorted(set(current) | set(targets))

    rows: list[dict[str, Any]] = []
    for sym in symbols:
        cw = float(current.get(sym, 0) or 0)
        tw = float(targets.get(sym, 0) or 0)
        delta = tw - cw
        meta = _symbol_meta(sym)
        sig = _latest_signal(sym)
        rows.append(
            {
                "symbol": sym,
                "name": meta.get("name") or next((p.get("name") for p in positions if str(p.get("symbol")).zfill(6) == sym), ""),
                "sector": meta.get("sector", ""),
                "current_weight": cw,
                "target_weight": tw,
                "weight_delta": delta,
                "weight_action": _weight_action(delta),
                "latest_state": sig.get("latest_state", ""),
                "suggested_position": sig.get("suggested_position"),
                "signal_action": sig.get("rebalance_action", ""),
                "signal_date": sig.get("signal_date"),
            }
        )

    rows.sort(key=lambda r: abs(float(r.get("weight_delta", 0) or 0)), reverse=True)
    changes = [r for r in rows if r["weight_action"] != "持有"]
    current_invested = sum(current.values())
    target_invested = sum(targets.get(s, 0) for s in symbols)
    cash_ratio = float(doc.get("cash_ratio", 0) or 0)

    summary_lines = [
        f"信号日参考：{rows[0].get('signal_date') if rows else '--'}",
        f"目标来源：{'推荐组合' if target_source == 'portfolio' and portfolio_id else '系统信号权重'}",
        f"当前股票仓位合计 {current_invested:.1%} · 目标合计 {target_invested:.1%} · 记录现金 {cash_ratio:.1%}",
        f"需调仓 {len(changes)} 只 · 执行语义 T 日收盘后信号 → T+1",
    ]
    if changes:
        summary_lines.append("调仓清单：")
        for r in changes[:12]:
            summary_lines.append(
                f"  {r['weight_action']} {r['symbol']} {r.get('name') or ''} "
                f"{r['current_weight']:.1%}→{r['target_weight']:.1%} "
                f"状态{r.get('latest_state') or '--'} 建议仓位{(r.get('suggested_position') or 0):.0%}"
            )
    else:
        summary_lines.append("暂无超过 2% 阈值的权重变动，维持持有。")

    return {
        "updated_at": doc.get("updated_at"),
        "target_source": target_source,
        "target_portfolio_id": portfolio_id,
        "cash_ratio": cash_ratio,
        "execution_note": "T 日收盘信号 → T+1 执行；以下为权重调配建议，非券商下单指令",
        "summary": "\n".join(summary_lines),
        "stats": {
            "positions_count": len(positions),
            "tracked_symbols": len(symbols),
            "change_count": len(changes),
            "current_invested": current_invested,
            "target_invested": target_invested,
        },
        "rows": rows,
        "changes": changes,
    }


def send_rebalance_notification(
    *,
    dry_run: bool = False,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    config = config or AppConfig.from_yaml(profile_path("默认.yaml"))
    report = analyze_rebalance()
    title = f"[Market-Agents] 调仓提醒 · {len(report.get('changes', []))} 项变动"
    body = report.get("summary", "")
    results = dispatch_notification(config.notify, title, body, dry_run=dry_run)
    return {
        "title": title,
        "body": body,
        "dry_run": dry_run,
        "notify_enabled": config.notify.enabled,
        "channels": [{"success": r.success, "channel": r.channel, "detail": r.detail} for r in results],
        "report": report,
    }
