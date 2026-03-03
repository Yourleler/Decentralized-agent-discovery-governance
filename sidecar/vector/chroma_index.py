"""
本文件应该做什么：
1. 提供 ChromaDB 的最小封装：upsert / delete / query。
2. 只负责向量索引，不掺杂评分与同步编排逻辑。
3. 对调用方隐藏 Chroma 结果结构，返回稳定的数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import chromadb
except Exception as exc:  # pragma: no cover
    chromadb = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


@dataclass(slots=True)
class ChromaIndexSettings:
    """
    Chroma 向量索引配置。

    字段说明：
    - persist_path: Chroma 持久化目录。
    - collection_name: 集合名称（默认 `agent_index`）。
    - distance_space: 距离空间（默认 `cosine`）。
    """

    persist_path: str
    collection_name: str = "agent_index"
    distance_space: str = "cosine"


@dataclass(slots=True)
class SearchHit:
    """
    统一的检索命中结构。

    字段说明：
    - agent_id: 命中文档 ID（通常是 agent 地址）。
    - score: 距离分数（越小越相近）。
    - metadata: Chroma 存储的元数据字典。
    - document: 命中文本原文。
    """

    agent_id: str
    score: float
    metadata: dict[str, Any]
    document: str


class ChromaIndex:
    """Chroma 最小可用封装。"""

    def __init__(self, settings: ChromaIndexSettings, embedding_function: Any | None = None) -> None:
        """
        参数：
        - settings (ChromaIndexSettings): 向量索引配置，包含持久化路径、集合名与距离空间类型。
        - embedding_function (Any | None): Chroma 使用的 embedding 函数；为空时由 Chroma 走默认行为。

        返回值：
        - None: 完成 Chroma 客户端与集合初始化。
        """
        if chromadb is None:  # pragma: no cover
            raise RuntimeError(
                "未安装 chromadb，请先在当前 Python 环境安装：pip install chromadb"
            ) from _IMPORT_ERROR

        self._settings = settings
        self._client = chromadb.PersistentClient(path=settings.persist_path)
        self._collection = self._client.get_or_create_collection(
            name=settings.collection_name,
            embedding_function=embedding_function,
            metadata={"hnsw:space": settings.distance_space},
        )

    def upsert(self, agent_id: str, vector_text: str, metadata: dict[str, Any] | None = None) -> None:
        """写入或更新单条向量文档。"""
        # 参数：
        # - agent_id (str): 文档唯一标识（通常为 agent 地址或 ID）。
        # - vector_text (str): 用于向量化的文本内容；空白文本会被直接忽略。
        # - metadata (dict[str, Any] | None): 附加元数据字典；为空时写入空字典。
        #
        # 返回值：
        # - None: 写入成功或在空文本场景下直接返回。
        text = vector_text.strip()
        if not text:
            return
        self._collection.upsert(
            ids=[agent_id],
            documents=[text],
            metadatas=[metadata or {}],
        )

    def delete(self, agent_id: str) -> None:
        """按 agent_id 删除。"""
        # 参数：
        # - agent_id (str): 需要删除的文档 ID。
        #
        # 返回值：
        # - None: 删除请求提交到 Chroma 集合。
        self._collection.delete(ids=[agent_id])

    def query(self, text: str, top_k: int = 5, where: dict[str, Any] | None = None) -> list[SearchHit]:
        """语义检索，返回统一命中结构。"""
        # 参数：
        # - text (str): 检索文本；空白文本会直接返回空列表。
        # - top_k (int): 期望返回数量；内部会兜底为至少 1。
        # - where (dict[str, Any] | None): Chroma 元数据过滤条件。
        #
        # 返回值：
        # - list[SearchHit]: 统一命中列表，包含 agent_id、距离分数、元数据和原文。
        query_text = text.strip()
        if not query_text:
            return []

        raw = self._collection.query(
            query_texts=[query_text],
            n_results=max(1, top_k),
            where=where,
        )

        ids = (raw.get("ids") or [[]])[0]
        docs = (raw.get("documents") or [[]])[0]
        metas = (raw.get("metadatas") or [[]])[0]
        dists = (raw.get("distances") or [[]])[0]
        

        hits: list[SearchHit] = []
        for idx, item_id in enumerate(ids):
            hits.append(
                SearchHit(
                    agent_id=str(item_id),
                    score=float(dists[idx]) if idx < len(dists) and dists[idx] is not None else 0.0,
                    metadata=metas[idx] if idx < len(metas) and metas[idx] is not None else {},
                    document=docs[idx] if idx < len(docs) and docs[idx] is not None else "",
                )
            )
        return hits
