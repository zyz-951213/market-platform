from __future__ import annotations

import argparse

from loguru import logger


def run_web(host: str = "0.0.0.0", port: int | None = None) -> None:
    import os
    import uvicorn

    from market_platform.predictor.核心.工具 import setup_logging

    if port is None:
        port = int(os.getenv("API_PORT", "18765"))

    setup_logging()
    logger.info(
        "Starting API server at http://{}:{} "
        "(前端: cd 前端 && npm run dev，或 npm run build 后 SERVE_FRONTEND=1)",
        host,
        port,
    )
    uvicorn.run(
        "market_platform.predictor.接口.应用:app",
        host=host,
        port=port,
        reload=False,
    )


def run_pipeline(argv: list[str] | None = None) -> None:
    from market_platform.predictor.脚本.运行管道 import main as pipeline_main

    args = list(argv or [])
    if args and args[0] == "--":
        args = args[1:]
    pipeline_main(args or None)


def run_pg_init() -> None:
    from market_platform.predictor.存储.数据访问 import init_schema, pg_enabled

    if not pg_enabled():
        logger.error("PostgreSQL 未启用，请配置 后端/配置/数据库.yaml 或 DATABASE_URL")
        raise SystemExit(1)
    init_schema()
    logger.info("PostgreSQL schema 已初始化")


def run_pg_status() -> None:
    from market_platform.predictor.存储.存储配置 import load_database_settings
    from market_platform.predictor.存储.数据访问 import pg_enabled, ping

    settings = load_database_settings()
    ok = ping() if pg_enabled() else False
    print(f"enabled: {settings.enabled}")
    print(f"dsn: {settings.dsn}")
    print(f"read_from_pg: {settings.read_from_pg}")
    print(f"write_files: {settings.write_files}")
    print(f"file_fallback: {settings.file_fallback}")
    print(f"connected: {ok}")
    raise SystemExit(0 if ok else 1)


def run_pg_migrate() -> None:
    from market_platform.predictor.存储.数据访问 import migrate_files_to_pg, pg_enabled

    if not pg_enabled():
        logger.error("PostgreSQL 未启用，请配置 后端/配置/数据库.yaml 或 DATABASE_URL")
        raise SystemExit(1)
    stats = migrate_files_to_pg()
    logger.info("迁移完成: {}", stats)


def run_paper_trading(argv: list[str] | None = None) -> None:
    from market_platform.predictor.脚本.运行模拟盘 import main as paper_main

    paper_main(argv)


def run_auto_update(argv: list[str] | None = None) -> None:
    from market_platform.predictor.脚本.运行自动更新 import main as update_main

    update_main(argv)


def main() -> None:
    try:
        from dotenv import load_dotenv
        from market_platform.predictor.核心.路径.项目 import repo_root

        load_dotenv(repo_root() / ".env")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(prog="market-predictor")
    sub = parser.add_subparsers(dest="command", required=True)

    web_p = sub.add_parser("web", help="启动 FastAPI API 服务")
    web_p.add_argument("--host", default="0.0.0.0")
    web_p.add_argument("--port", type=int, default=None)

    pipe_p = sub.add_parser("pipeline", help="运行数据/回测管道")
    pipe_p.add_argument("pipeline_args", nargs=argparse.REMAINDER, help="传递给运行管道的参数")

    pg_p = sub.add_parser("pg", help="PostgreSQL 数据库管理")
    pg_sub = pg_p.add_subparsers(dest="pg_command", required=True)
    pg_sub.add_parser("init", help="初始化数据库 schema")
    pg_sub.add_parser("status", help="检查数据库连接与配置")
    pg_sub.add_parser("migrate", help="将本地文件数据导入 PostgreSQL")

    paper_p = sub.add_parser("paper", help="模拟实盘调度")
    paper_p.add_argument("--once", action="store_true", help="立即执行一轮")
    paper_p.add_argument("--force", action="store_true", help="强制处理最新 K 线")
    paper_p.add_argument("--daemon", action="store_true", help="守护进程模式")

    update_p = sub.add_parser("update", help="收盘后自动更新（数据+增量回测）")
    update_p.add_argument("--once", action="store_true", help="立即执行")
    update_p.add_argument("--force", action="store_true", help="忽略交易日检查")
    update_p.add_argument("--daemon", action="store_true", help="守护进程模式")

    args = parser.parse_args()
    if args.command == "web":
        run_web(host=args.host, port=args.port)
    elif args.command == "pipeline":
        run_pipeline(args.pipeline_args or None)
    elif args.command == "pg":
        if args.pg_command == "init":
            run_pg_init()
        elif args.pg_command == "status":
            run_pg_status()
        elif args.pg_command == "migrate":
            run_pg_migrate()
    elif args.command == "paper":
        paper_argv = []
        if args.once:
            paper_argv.append("--once")
        if args.force:
            paper_argv.append("--force")
        if args.daemon:
            paper_argv.append("--daemon")
        run_paper_trading(paper_argv or None)
    elif args.command == "update":
        update_argv = []
        if args.once:
            update_argv.append("--once")
        if args.force:
            update_argv.append("--force")
        if args.daemon:
            update_argv.append("--daemon")
        run_auto_update(update_argv or None)


if __name__ == "__main__":
    main()
