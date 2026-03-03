"""
本文件应该做什么：
1. 提供最小语义检索服务接口（输入 query，返回 top-k agent）。
2. 组合 Chroma 召回与 SQLite 状态过滤（只保留可用 agent）。
3. 对上层返回稳定结构，避免直接暴露底层实现细节。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sidecar.storage.sqlite_state import SQLiteStateStore
from sidecar.vector import ChromaIndex


@dataclass(slots=True)
class SearchResult:
    """检索结果结构。"""

    agent_address: str
    did: str
    metadata_cid: str
    final_score: float
    semantic_distance: float


class DiscoveryService:
    """最小检索服务。"""

    def __init__(self, state_store: SQLiteStateStore, vector_index: ChromaIndex) -> None:
        self.state_store = state_store
        self.vector_index = vector_index

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """执行语义检索并返回 top-k 结果。"""
        text = query.strip()
        if not text or top_k <= 0:
            return []

        recall_k = max(top_k, top_k * 3)
        hits = self.vector_index.query(text=text, top_k=recall_k)

        results: list[SearchResult] = []
        seen: set[str] = set()

        for hit in hits:
            agent_id = str(hit.agent_id).lower().strip()
            if not agent_id or agent_id in seen:
                continue

            state = self.state_store.get_agent_state(agent_id)
            if state is None:
                continue
            if (not state.is_registered) or state.is_slashed:
                continue

            seen.add(agent_id)
            results.append(
                SearchResult(
                    agent_address=state.agent_address,
                    did=state.did,
                    metadata_cid=state.metadata_cid,
                    final_score=float(state.final_score),
                    semantic_distance=float(hit.score),
                )
            )
            if len(results) >= top_k:
                break

        return results

    def search_as_dicts(self, query: str, top_k: int = 5) -> list[dict[str, object]]:
        """字典形态返回，便于 CLI / API 直接输出。"""
        return [asdict(item) for item in self.search(query=query, top_k=top_k)]
