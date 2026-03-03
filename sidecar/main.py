"""
Sidecar 启动入口（毕设简化版）。
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

from sidecar.wiring import (
    SidecarSettings,
    build_sidecar_container,
    close_sidecar_container,
    load_sidecar_settings,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Sidecar 最小同步/检索入口")
    parser.add_argument("--db-path", dest="db_path", default=None, help="SQLite 文件路径")
    parser.add_argument(
        "--start-block",
        dest="start_block",
        type=int,
        default=None,
        help="初始同步区块（仅在没有 watermark 时生效）",
    )
    parser.add_argument("--first", dest="first", type=int, default=None, help="单页拉取条数")
    parser.add_argument(
        "--max-pages",
        dest="max_pages",
        type=int,
        default=None,
        help="单轮最大分页数",
    )
    parser.add_argument(
        "--max-rounds",
        dest="max_rounds",
        type=int,
        default=None,
        help="本次运行最大同步轮数",
    )
    parser.add_argument(
        "--once",
        dest="once",
        action="store_true",
        help="只执行一轮 sync_once",
    )
    parser.add_argument(
        "--rescore-only",
        dest="rescore_only",
        action="store_true",
        help="只执行全量评分重算，不拉取子图增量",
    )
    parser.add_argument(
        "--rescore-batch-size",
        dest="rescore_batch_size",
        type=int,
        default=500,
        help="全量评分重算分页批大小",
    )
    parser.add_argument(
        "--query",
        dest="query",
        default=None,
        help="语义检索输入文本",
    )
    parser.add_argument(
        "--top-k",
        dest="top_k",
        type=int,
        default=5,
        help="语义检索返回数量",
    )
    parser.add_argument(
        "--adjust-local-score",
        dest="adjust_local_score",
        action="store_true",
        help="手动增减本地评分证据（alpha/beta）",
    )
    parser.add_argument(
        "--agent-address",
        dest="agent_address",
        default=None,
        help="本地评分调整目标 agent 地址",
    )
    parser.add_argument(
        "--alpha-delta",
        dest="alpha_delta",
        type=float,
        default=0.0,
        help="本地正向证据增量（可为负）",
    )
    parser.add_argument(
        "--beta-delta",
        dest="beta_delta",
        type=float,
        default=0.0,
        help="本地负向证据增量（可为负）",
    )
    return parser.parse_args(argv)


def build_settings_from_args(args: argparse.Namespace) -> SidecarSettings:
    """根据命令行参数构建运行配置。"""
    return load_sidecar_settings(
        db_path=args.db_path,
        default_start_block=args.start_block,
        sync_first=args.first,
        sync_max_pages=args.max_pages,
        sync_max_rounds=args.max_rounds,
    )


def run_sync(
    settings: SidecarSettings,
    once: bool = False,
    rescore_only: bool = False,
    rescore_batch_size: int = 500,
) -> int:
    """执行同步任务。"""
    container = build_sidecar_container(settings)
    try:
        orchestrator = container.sync_orchestrator
        if rescore_only:
            rescored = orchestrator.rescore_all(batch_size=rescore_batch_size)
            logging.info(
                "rescore finished: total=%s batch_size=%s",
                rescored,
                rescore_batch_size,
            )
            return 0

        if once:
            result = orchestrator.sync_once(
                first=settings.sync_first,
                max_pages=settings.sync_max_pages,
            )
            logging.info(
                "sync once finished: from=%s to=%s fetched=%s written=%s rescored=%s page_limit=%s",
                result.from_block,
                result.to_block,
                result.fetched_count,
                result.written_count,
                result.rescored_count,
                result.reached_page_limit,
            )
            return 0

        results = orchestrator.sync_until_caught_up(
            first=settings.sync_first,
            max_pages=settings.sync_max_pages,
            max_rounds=settings.sync_max_rounds,
        )
        if not results:
            logging.info("sync finished: no rounds executed")
            return 0

        last = results[-1]
        logging.info(
            "sync finished: rounds=%s from=%s to=%s fetched=%s written=%s rescored=%s",
            len(results),
            results[0].from_block,
            last.to_block,
            sum(r.fetched_count for r in results),
            sum(r.written_count for r in results),
            sum(r.rescored_count for r in results),
        )
        return 0
    finally:
        close_sidecar_container(container)


def run_query(settings: SidecarSettings, query: str, top_k: int = 5) -> int:
    """执行最小语义检索接口（CLI 形态）。"""
    if top_k <= 0:
        raise ValueError("top_k 必须为正整数")

    container = build_sidecar_container(settings)
    try:
        results = container.discovery_service.search_as_dicts(query=query, top_k=top_k)
        payload = {
            "query": query,
            "top_k": top_k,
            "count": len(results),
            "items": results,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        close_sidecar_container(container)


def run_adjust_local_score(
    settings: SidecarSettings,
    agent_address: str,
    alpha_delta: float = 0.0,
    beta_delta: float = 0.0,
) -> int:
    """执行本地评分证据调整接口（CLI 形态）。"""
    container = build_sidecar_container(settings)
    try:
        updated = container.sync_orchestrator.adjust_local_score(
            agent_address=agent_address,
            alpha_delta=alpha_delta,
            beta_delta=beta_delta,
        )
        payload = {
            "agent_address": updated.agent_address,
            "alpha": updated.alpha,
            "beta": updated.beta,
            "global_score": updated.global_score,
            "local_score": updated.local_score,
            "confidence_score": updated.confidence_score,
            "final_score": updated.final_score,
            "last_score_update_ts": updated.last_score_update_ts,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        close_sidecar_container(container)


def main(argv: list[str] | None = None) -> int:
    """Sidecar 主函数。"""
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "sidecar.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

    args = parse_args(argv)
    settings = build_settings_from_args(args)
    if args.adjust_local_score:
        if not args.agent_address:
            raise ValueError("启用 --adjust-local-score 时必须提供 --agent-address")
        return run_adjust_local_score(
            settings=settings,
            agent_address=args.agent_address,
            alpha_delta=args.alpha_delta,
            beta_delta=args.beta_delta,
        )
    if args.query:
        return run_query(settings=settings, query=args.query, top_k=args.top_k)

    return run_sync(
        settings,
        once=args.once,
        rescore_only=args.rescore_only,
        rescore_batch_size=args.rescore_batch_size,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
