#!/usr/bin/env python3
"""对比 tmp_cmp/local.tsv 与 cloud.tsv，按结构映射归一后报告差异。

映射依据（本地 公共.*文件 ←→ 云端 微盘股研究.*，同结构同名数据）：
两侧表名不同但列与行数一致，已在人工核对中确认。
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

映射 = {
    "公共.上市状态文件": "微盘股研究.上市状态",
    "公共.交易日历文件": "微盘股研究.交易日历",
    "公共.公司档案文件": "微盘股研究.公司档案",
    "公共.市场收益文件": "微盘股研究.市场收益",
    "公共.指数日行情文件": "微盘股研究.指数日行情",
    "公共.新股发行文件": "微盘股研究.新股发行",
    "公共.无涨跌停文件": "微盘股研究.无涨跌停",
    "公共.日回报文件": "微盘股研究.日回报",
    "公共.日行情档案文件": "微盘股研究.日行情",
    "公共.月收益率文件": "微盘股研究.月收益率",
    "公共.行业归属文件": "微盘股研究.行业归属",
}


def load(path: Path) -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        tbl, metric, val = parts
        data.setdefault(tbl, {})[metric] = val
    return data


def 是备份(t: str) -> bool:
    # 备份表命名形如 公共.X_清洗备份_20261006 / 公共.X_去重备份_20261006
    return "备份_" in t


def main() -> None:
    local_all = load(HERE / "local.tsv")
    cloud = load(HERE / "cloud.tsv")
    local = {t: v for t, v in local_all.items() if not 是备份(t)}
    备份数 = len(local_all) - len(local)

    行 = []
    for lt in sorted(local):
        ct = 映射.get(lt, lt)
        lm = local[lt]
        cm = cloud.get(ct)
        if cm is None:
            行.append(("本地独有", lt, ct, lm.get("rows", "?"), "—", "云端无此表"))
            continue
        差异 = []
        if lm.get("rows") != cm.get("rows"):
            差异.append(f"行数 {lm.get('rows')} vs {cm.get('rows')}")
        for metric, lv in lm.items():
            if metric == "rows":
                continue
            cv = cm.get(metric)
            if cv is None:
                差异.append(f"{metric}: 云端无此列")
            elif cv != lv:
                差异.append(f"{metric}: {lv} vs {cv}")
        行.append(
            ("一致" if not 差异 else "差异", lt, ct, lm.get("rows", "?"),
             cm.get("rows", "?"), "；".join(差异) if 差异 else "—")
        )

    被映射 = set(映射.values()) | set(local)
    云端独有 = sorted(t for t in cloud if t not in 被映射)

    一致 = [r for r in 行 if r[0] == "一致"]
    差异 = [r for r in 行 if r[0] != "一致"]

    print("=" * 96)
    print(f"本地物理表 {len(local_all)}（其中历史备份表 {备份数}，不参与对比）")
    print(f"本地有效表 {len(local)} | 一致 {len(一致)} | 有差异 {len([r for r in 差异 if r[0] == '差异'])}"
          f" | 本地独有 {len([r for r in 差异 if r[0] == '本地独有'])}")
    print(f"云端物理表 {len(cloud)} | 云端独有（本地无对应） {len(云端独有)}")
    print("=" * 96)

    print("\n【A】有差异的表")
    for kind, lt, ct, lr, cr, st in 差异:
        if kind != "差异":
            continue
        别名 = f"  ↔  {ct}" if ct != lt else ""
        print(f"\n● {lt}{别名}\n    本地 {lr} 行 / 云端 {cr} 行\n    {st}")

    print("\n\n【B】本地独有（云端缺表）")
    for kind, lt, ct, lr, cr, st in 差异:
        if kind != "本地独有":
            continue
        print(f"  {lt:38s} 本地 {lr} 行")

    print("\n\n【C】云端独有（历史/研究库，本地无对应物）")
    for t in 云端独有:
        print(f"  {t:38s} {cloud[t].get('rows')} 行")

    print("\n\n【D】行数差距 TOP 20")
    cand = []
    for kind, lt, ct, lr, cr, st in 行:
        try:
            cand.append((abs(int(lr) - int(cr)), lt, lr, cr))
        except (TypeError, ValueError):
            continue
    for d, lt, lr, cr in sorted(cand, reverse=True)[:20]:
        if d:
            print(f"  Δ{d:>12,}  {lt:34s} 本地 {lr:>12} 云端 {cr:>12}")


if __name__ == "__main__":
    main()
