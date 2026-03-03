"""
本文件应该做什么：
1. 约束最小检索接口输入 query、输出 top-k agent 的行为。
2. 验证检索结果会过滤未注册或已被惩罚的 agent。
3. 保证接口返回结构稳定，便于上层 API/CLI 直接使用。
"""

from __future__ import annotations

import unittest

from sidecar.services.discovery_service import DiscoveryService
from sidecar.storage.sqlite_state import AgentState, SQLiteStateStore
from sidecar.vector import SearchHit


class _FakeVectorIndex:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits

    def query(self, text: str, top_k: int = 5, where: dict | None = None) -> list[SearchHit]:
        _ = text
        _ = where
        return self._hits[:top_k]


class TestDiscoveryContract(unittest.TestCase):
    """最小检索契约测试。"""

    def test_search_returns_top_k_registered_agents(self) -> None:
        store = SQLiteStateStore(":memory:")
        try:
            store.init_db()
            store.upsert_agent_state(
                AgentState(
                    agent_address="0xa",
                    did="did:a",
                    metadata_cid="cid-a",
                    is_registered=True,
                    is_slashed=False,
                    final_score=90.0,
                    vector_text="中文金融分析",
                    last_event_block=1,
                )
            )
            store.upsert_agent_state(
                AgentState(
                    agent_address="0xb",
                    did="did:b",
                    metadata_cid="cid-b",
                    is_registered=False,
                    is_slashed=False,
                    final_score=80.0,
                    vector_text="英文摘要",
                    last_event_block=1,
                )
            )
            store.upsert_agent_state(
                AgentState(
                    agent_address="0xc",
                    did="did:c",
                    metadata_cid="cid-c",
                    is_registered=True,
                    is_slashed=True,
                    final_score=70.0,
                    vector_text="risk control",
                    last_event_block=1,
                )
            )

            service = DiscoveryService(
                state_store=store,
                vector_index=_FakeVectorIndex(
                    hits=[
                        SearchHit(agent_id="0xa", score=0.11, metadata={}, document="doc-a"),
                        SearchHit(agent_id="0xb", score=0.12, metadata={}, document="doc-b"),
                        SearchHit(agent_id="0xc", score=0.13, metadata={}, document="doc-c"),
                    ]
                ),
            )

            results = service.search(query="我要找做金融分析的Agent", top_k=2)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].agent_address, "0xa")
        finally:
            store.close()

    def test_search_empty_query_returns_empty(self) -> None:
        store = SQLiteStateStore(":memory:")
        try:
            store.init_db()
            service = DiscoveryService(
                state_store=store,
                vector_index=_FakeVectorIndex(hits=[]),
            )
            self.assertEqual(service.search(query="   ", top_k=5), [])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
