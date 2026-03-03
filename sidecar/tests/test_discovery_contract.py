"""
本文件应该做什么：
1. 约束最小检索接口输入 query、输出 top-k agent 的行为。
2. 验证检索结果会过滤未注册或已被惩罚的 agent。
3. 保证接口返回结构稳定，便于上层 API/CLI 直接使用。
"""

from __future__ import annotations

import time
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

    def test_search_uses_hybrid_weighted_ranking(self) -> None:
        """
        在“语义差距明显”场景，排序应保持语义优先，不被高 S_final 喧宾夺主。
        """
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
                    final_score=10.0,
                    vector_text="中文金融分析",
                    last_event_block=1,
                )
            )
            store.upsert_agent_state(
                AgentState(
                    agent_address="0xb",
                    did="did:b",
                    metadata_cid="cid-b",
                    is_registered=True,
                    is_slashed=False,
                    final_score=100.0,
                    vector_text="中文金融分析",
                    last_event_block=1,
                )
            )

            service = DiscoveryService(
                state_store=store,
                vector_index=_FakeVectorIndex(
                    hits=[
                        # A 语义更近（距离更小）
                        SearchHit(agent_id="0xa", score=0.10, metadata={}, document="doc-a"),
                        # B 语义稍远，但 final_score 显著更高
                        SearchHit(agent_id="0xb", score=0.30, metadata={}, document="doc-b"),
                    ]
                ),
            )

            results = service.search(query="我要找做金融分析的Agent", top_k=2)
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].agent_address, "0xa")
            self.assertEqual(results[1].agent_address, "0xb")
        finally:
            store.close()

    def test_search_trust_can_adjust_when_semantics_are_close(self) -> None:
        """
        在“语义接近”场景，高 S_final 应能作为温和修正项提升排序。
        """
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
                    final_score=20.0,
                    vector_text="中文金融分析",
                    last_event_block=1,
                )
            )
            store.upsert_agent_state(
                AgentState(
                    agent_address="0xb",
                    did="did:b",
                    metadata_cid="cid-b",
                    is_registered=True,
                    is_slashed=False,
                    final_score=100.0,
                    vector_text="中文金融分析",
                    last_event_block=1,
                )
            )

            service = DiscoveryService(
                state_store=store,
                vector_index=_FakeVectorIndex(
                    hits=[
                        # A 稍微更近
                        SearchHit(agent_id="0xa", score=0.20, metadata={}, document="doc-a"),
                        # B 稍微更远但信誉明显更高
                        SearchHit(agent_id="0xb", score=0.24, metadata={}, document="doc-b"),
                    ]
                ),
            )

            results = service.search(query="我要找做金融分析的Agent", top_k=2)
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].agent_address, "0xb")
            self.assertEqual(results[1].agent_address, "0xa")
        finally:
            store.close()

    def test_search_filters_runtime_unavailable_agents(self) -> None:
        """运行时连续失败超阈值时，应在冷却期内被过滤。"""
        store = SQLiteStateStore(":memory:")
        try:
            store.init_db()
            now_ts = int(time.time())
            store.upsert_agent_state(
                AgentState(
                    agent_address="0xa",
                    did="did:a",
                    metadata_cid="cid-a",
                    is_registered=True,
                    is_slashed=False,
                    final_score=95.0,
                    vector_text="中文金融分析",
                    consecutive_probe_failures=3,
                    last_probe_ts=now_ts,
                    last_event_block=1,
                )
            )
            store.upsert_agent_state(
                AgentState(
                    agent_address="0xb",
                    did="did:b",
                    metadata_cid="cid-b",
                    is_registered=True,
                    is_slashed=False,
                    final_score=70.0,
                    vector_text="中文金融分析",
                    last_event_block=1,
                )
            )

            service = DiscoveryService(
                state_store=store,
                vector_index=_FakeVectorIndex(
                    hits=[
                        SearchHit(agent_id="0xa", score=0.10, metadata={}, document="doc-a"),
                        SearchHit(agent_id="0xb", score=0.11, metadata={}, document="doc-b"),
                    ]
                ),
            )
            results = service.search(query="我要找做金融分析的Agent", top_k=2)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].agent_address, "0xb")
        finally:
            store.close()

    def test_search_runtime_probe_can_recover_agent(self) -> None:
        """冷却中的 Agent 探测成功后，应恢复可见。"""
        store = SQLiteStateStore(":memory:")
        try:
            store.init_db()
            now_ts = int(time.time())
            store.upsert_agent_state(
                AgentState(
                    agent_address="0xa",
                    did="did:a",
                    metadata_cid="cid-a",
                    is_registered=True,
                    is_slashed=False,
                    final_score=90.0,
                    vector_text="中文金融分析",
                    runtime_probe_url="http://agent-a.local/health",
                    consecutive_probe_failures=3,
                    last_probe_ts=now_ts,
                    last_event_block=1,
                )
            )
            store.upsert_agent_state(
                AgentState(
                    agent_address="0xb",
                    did="did:b",
                    metadata_cid="cid-b",
                    is_registered=True,
                    is_slashed=False,
                    final_score=90.0,
                    vector_text="中文金融分析",
                    last_event_block=1,
                )
            )

            def _probe_ok(url: str, timeout_seconds: float) -> bool:
                _ = timeout_seconds
                return "agent-a.local" in url

            service = DiscoveryService(
                state_store=store,
                vector_index=_FakeVectorIndex(
                    hits=[
                        SearchHit(agent_id="0xa", score=0.10, metadata={}, document="doc-a"),
                        SearchHit(agent_id="0xb", score=0.11, metadata={}, document="doc-b"),
                    ]
                ),
                runtime_probe_ttl_seconds=0,
                runtime_probe_func=_probe_ok,
            )
            results = service.search(query="我要找做金融分析的Agent", top_k=2)
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].agent_address, "0xa")

            refreshed = store.get_agent_state("0xa")
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertEqual(refreshed.consecutive_probe_failures, 0)
            self.assertGreater(refreshed.last_probe_success_ts, 0)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
