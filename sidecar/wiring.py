"""
依赖注入与模块装配（毕设简化版）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sidecar.services.discovery_service import DiscoveryService
from sidecar.services.sync_orchestrator import SyncOrchestrator
from sidecar.storage.sqlite_state import SQLiteStateStore
from sidecar.vector import ChromaIndex, ChromaIndexSettings, build_sentence_transformer_embedding


@dataclass
class SidecarSettings:
    """Sidecar 运行配置。"""

    db_path: str
    default_start_block: int
    sync_first: int
    sync_max_pages: int
    sync_max_rounds: int
    chroma_persist_path: str
    chroma_collection_name: str
    embed_model_name: str


@dataclass
class SidecarContainer:
    """Sidecar 依赖容器。"""

    settings: SidecarSettings
    state_store: SQLiteStateStore
    chroma_index: ChromaIndex
    sync_orchestrator: SyncOrchestrator
    discovery_service: DiscoveryService


def load_sidecar_settings(
    db_path: str | None = None,
    default_start_block: int | None = None,
    sync_first: int | None = None,
    sync_max_pages: int | None = None,
    sync_max_rounds: int | None = None,
    chroma_persist_path: str | None = None,
    chroma_collection_name: str | None = None,
    embed_model_name: str | None = None,
) -> SidecarSettings:
    """
    加载 Sidecar 运行配置（参数优先，其次环境变量，最后默认值）。
    """
    base_dir = Path(__file__).resolve().parent
    default_db_path = str(base_dir / "data" / "sidecar_state.db")
    default_chroma_path = str(base_dir / "data" / "chroma")

    resolved_db_path = db_path or os.getenv("SIDECAR_DB_PATH") or default_db_path
    resolved_start_block = _resolve_int(
        primary=default_start_block,
        env_key="SIDECAR_START_BLOCK",
        fallback=10360859,
    )
    resolved_sync_first = _resolve_int(
        primary=sync_first,
        env_key="SIDECAR_SYNC_FIRST",
        fallback=200,
    )
    resolved_sync_max_pages = _resolve_int(
        primary=sync_max_pages,
        env_key="SIDECAR_SYNC_MAX_PAGES",
        fallback=50,
    )
    resolved_sync_max_rounds = _resolve_int(
        primary=sync_max_rounds,
        env_key="SIDECAR_SYNC_MAX_ROUNDS",
        fallback=10,
    )
    resolved_chroma_path = (
        chroma_persist_path
        or os.getenv("SIDECAR_CHROMA_PATH")
        or default_chroma_path
    )
    resolved_collection_name = (
        chroma_collection_name
        or os.getenv("SIDECAR_CHROMA_COLLECTION")
        or "agent_index"
    ).strip()
    resolved_embed_model = (
        embed_model_name
        or os.getenv("SIDECAR_EMBED_MODEL")
        or "BAAI/bge-m3"
    ).strip()

    if resolved_sync_first <= 0:
        raise ValueError("sync_first 必须为正整数")
    if resolved_sync_max_pages <= 0:
        raise ValueError("sync_max_pages 必须为正整数")
    if resolved_sync_max_rounds <= 0:
        raise ValueError("sync_max_rounds 必须为正整数")
    if not resolved_collection_name:
        raise ValueError("chroma_collection_name 不能为空")
    if not resolved_embed_model:
        raise ValueError("embed_model_name 不能为空")

    return SidecarSettings(
        db_path=resolved_db_path,
        default_start_block=resolved_start_block,
        sync_first=resolved_sync_first,
        sync_max_pages=resolved_sync_max_pages,
        sync_max_rounds=resolved_sync_max_rounds,
        chroma_persist_path=resolved_chroma_path,
        chroma_collection_name=resolved_collection_name,
        embed_model_name=resolved_embed_model,
    )


def build_sidecar_container(settings: SidecarSettings | None = None) -> SidecarContainer:
    """构建 Sidecar 容器并完成依赖接线。"""
    cfg = settings or load_sidecar_settings()
    state_store = SQLiteStateStore(cfg.db_path)
    state_store.init_db()

    embedding_function = build_sentence_transformer_embedding(
        model_name=cfg.embed_model_name
    )
    chroma_index = ChromaIndex(
        settings=ChromaIndexSettings(
            persist_path=cfg.chroma_persist_path,
            collection_name=cfg.chroma_collection_name,
        ),
        embedding_function=embedding_function,
    )

    sync_orchestrator = SyncOrchestrator(
        state_store=state_store,
        default_start_block=cfg.default_start_block,
        vector_index=chroma_index,
    )
    discovery_service = DiscoveryService(
        state_store=state_store,
        vector_index=chroma_index,
    )

    return SidecarContainer(
        settings=cfg,
        state_store=state_store,
        chroma_index=chroma_index,
        sync_orchestrator=sync_orchestrator,
        discovery_service=discovery_service,
    )


def close_sidecar_container(container: SidecarContainer) -> None:
    """关闭 Sidecar 容器持有的资源。"""
    container.state_store.close()


def _resolve_int(primary: int | None, env_key: str, fallback: int) -> int:
    """解析整数配置值。"""
    if primary is not None:
        return int(primary)
    env_value = os.getenv(env_key)
    if env_value is not None and env_value != "":
        return int(env_value)
    return int(fallback)
