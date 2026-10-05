"""破坏性运维脚本的安全护栏(TASK-003 D9)。

背景(评审):`修复前复权.py`、`修复脏标的.py`、`迁移净值合并状态.py` 会执行
`DELETE FROM 行为分析.…` 与 `save_daily_bars(..., replace=True)`(整表写回),
`迁移净值合并状态.py --vacuum` 还会 `VACUUM FULL`;这些脚本此前**没有 dry-run**,
误跑即不可逆(且 `DELETE` 可能级联影响子表)。

本模块提供统一护栏:
    guard = 破坏性操作护栏(脚本名="修复前复权", dry_run=args.dry_run, confirmed=args.yes)
    if guard.拦截(动作="删除 3 张表的孤立行", 影响="行为分析.策略净值/参与者活跃度/分钟日特征"):
        ...真实执行...
"""

from __future__ import annotations

import logging

logger = logging.getLogger("market_platform.agents.脚本.运维.安全护栏")


class 破坏性操作护栏:
    """统一处理 dry-run 打印与"未确认即拒绝"。

    - `dry_run=True`:只打印将要执行的动作与影响范围,不执行;
    - `confirmed=False`(且非 dry-run):第一次调用会打印所有动作并**拒绝执行**,
      要求显式加 `--yes` 确认;
    - 两者都有时以 dry-run 优先(只打印)。
    """

    def __init__(self, *, 脚本名: str, dry_run: bool = False, confirmed: bool = False) -> None:
        self.脚本名 = 脚本名
        self.dry_run = bool(dry_run)
        self.confirmed = bool(confirmed)
        self._已打印 = False
        self._动作: list[str] = []

    def 登记(self, 动作: str) -> None:
        self._动作.append(动作)

    def 拦截(self, *, 动作: str, 影响: str = "") -> bool:
        """返回 True 表示应当**跳过**真实执行(仅打印)。"""
        self.登记(动作)
        影响说明 = f"｜影响: {影响}" if 影响 else ""
        if self.dry_run:
            logger.warning("【dry-run】%s:%s%s", self.脚本名, 动作, 影响说明)
            print(f"[dry-run] {动作}{影响说明}", flush=True)
            return True
        if not self.confirmed:
            if not self._已打印:
                self._已打印 = True
                print(
                    f"\n⚠️ {self.脚本名} 包含破坏性操作(DELETE / 整表写回 / VACUUM FULL)。\n"
                    f"   动作:{动作}{影响说明}\n"
                    "   请先加 --dry-run 预览,确认后用 --yes 执行。\n",
                    flush=True,
                )
            logger.error("拒绝执行未确认的破坏性操作: %s", 动作)
            return True
        logger.warning("执行破坏性操作: %s%s", 动作, 影响说明)
        return False


def 加入安全参数(parser) -> None:
    """给 argparse 解析器加上统一的 --dry-run / --yes。"""
    parser.add_argument("--dry-run", action="store_true", help="只打印将要执行的动作与影响,不做任何写入")
    parser.add_argument("--yes", action="store_true", help="确认执行破坏性操作(不加则拒绝执行)")


__all__ = ["破坏性操作护栏", "加入安全参数"]
