"""
同步与存储契约测试（最小落地版）。

覆盖点：
1. 迁移补列与索引创建。
2. upsert 幂等（按 last_event_block 防回滚）。
3. rescore_all 后评分结果正确性。

运行方法（在项目根目录执行）：
1. 运行本文件全部测试：
   python -m unittest sidecar.tests.test_sync_contract -v
2. 只运行单个测试用例（示例）：
   python -m unittest sidecar.tests.test_sync_contract.TestSyncContract.test_rescore_all_recomputes_scores -v
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from sidecar.services.sync_orchestrator import SyncOrchestrator
from sidecar.storage.sqlite_state import AgentState, SQLiteStateStore


class _FakeVectorIndex:
    def __init__(self) -> None:
        self.upserts: list[dict[str, object]] = []
        self.deletes: list[str] = []

    def upsert(self, agent_id: str, vector_text: str, metadata: dict | None = None) -> None:
        self.upserts.append(
            {
                "agent_id": agent_id,
                "vector_text": vector_text,
                "metadata": metadata or {},
            }
        )

    def delete(self, agent_id: str) -> None:
        self.deletes.append(agent_id)


class TestSyncContract(unittest.TestCase):
    """同步与 SQLite 契约测试。"""

    def test_init_db_migrates_columns_and_creates_indexes(self) -> None:
        """
        老库只有旧列时，init_db 能补齐新列并创建关键索引。
        """
        with tempfile.TemporaryDirectory() as tmpdir:#真实临时目录
            db_path = Path(tmpdir) / "legacy_state.db"
            conn = sqlite3.connect(str(db_path))
            conn.executescript(
                """
                CREATE TABLE agent_state (
                    agent_address TEXT PRIMARY KEY,
                    did TEXT NOT NULL DEFAULT '',
                    metadata_cid TEXT NOT NULL DEFAULT '',
                    init_score INTEGER NOT NULL DEFAULT 0,
                    accumulated_penalty INTEGER NOT NULL DEFAULT 0,
                    last_misconduct_timestamp INTEGER NOT NULL DEFAULT 0,
                    stake_amount TEXT NOT NULL DEFAULT '0',
                    is_slashed INTEGER NOT NULL DEFAULT 0,
                    is_registered INTEGER NOT NULL DEFAULT 1,
                    admin TEXT NOT NULL DEFAULT '',
                    last_event_block INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE sync_state (
                    state_key TEXT PRIMARY KEY,
                    state_value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            conn.commit()
            conn.close()

            store = SQLiteStateStore(db_path)
            try:
                store.init_db()
            finally:
                store.close()

            verify_conn = sqlite3.connect(str(db_path))
            verify_conn.row_factory = sqlite3.Row
            try:
                columns = {
                    str(row["name"])
                    for row in verify_conn.execute("PRAGMA table_info(agent_state)").fetchall()
                }
                for expected in (
                    "alpha",
                    "beta",
                    "last_score_update_ts",
                    "global_score",
                    "local_score",
                    "confidence_score",
                    "final_score",
                    "metadata_sha256",
                    "vector_text",
                ):
                    self.assertIn(expected, columns)

                index_names = {
                    str(row["name"])
                    for row in verify_conn.execute("PRAGMA index_list(agent_state)").fetchall()
                }
                self.assertIn("idx_agent_state_registered", index_names)
                self.assertIn("idx_agent_state_final_score", index_names)
                self.assertIn("idx_agent_state_last_event_block", index_names)
                self.assertIn("idx_agent_state_metadata_cid", index_names)
            finally:
                verify_conn.close()

    def test_upsert_idempotent_by_last_event_block(self) -> None:
        """
        幂等规则：
        - 低区块写入不覆盖高区块数据；
        - 高区块写入可覆盖旧数据。
        """
        store = SQLiteStateStore(":memory:")#db_path == ":memory:" 时，SQLite 引擎会自动创建内存数据库
        try:
            store.init_db()

            newest = AgentState(
                agent_address="0xabc",
                did="did:newest",
                metadata_cid="cid-newest",
                init_score=80,
                accumulated_penalty=10,
                final_score=70.0,
                last_event_block=100,
            )
            store.upsert_agent_state(newest)

            stale = AgentState(
                agent_address="0xabc",
                did="did:stale",
                metadata_cid="cid-stale",
                init_score=1,
                accumulated_penalty=1,
                final_score=1.0,
                last_event_block=90,
            )
            store.upsert_agent_state(stale)

            current = store.get_agent_state("0xabc")
            self.assertIsNotNone(current)
            assert current is not None
            self.assertEqual(current.did, "did:newest")
            self.assertEqual(current.metadata_cid, "cid-newest")
            self.assertEqual(current.last_event_block, 100)

            fresher = AgentState(
                agent_address="0xabc",
                did="did:fresher",
                metadata_cid="cid-fresher",
                init_score=90,
                accumulated_penalty=20,
                final_score=66.0,
                last_event_block=101,
            )
            store.upsert_agent_state(fresher)

            updated = store.get_agent_state("0xabc")
            self.assertIsNotNone(updated)
            assert updated is not None
            self.assertEqual(updated.did, "did:fresher")
            self.assertEqual(updated.metadata_cid, "cid-fresher")
            self.assertEqual(updated.last_event_block, 101)
        finally:
            store.close()

    def test_rescore_all_recomputes_scores(self) -> None:
        """
        rescore_all 会重算并落库评分字段。
        """
        store = SQLiteStateStore(":memory:")
        try:
            store.init_db()
            now_ts = int(time.time())

            state = AgentState(
                agent_address="0x01",
                init_score=80,
                accumulated_penalty=20,
                last_misconduct_timestamp=now_ts,
                is_registered=True,
                is_slashed=False,
                alpha=1.0,
                beta=1.0,
                last_score_update_ts=now_ts,
                last_event_block=1,
            )
            store.upsert_agent_state(state)

            orchestrator = SyncOrchestrator(state_store=store)
            rescored = orchestrator.rescore_all(batch_size=10)
            self.assertEqual(rescored, 1)

            got = store.get_agent_state("0x01")
            self.assertIsNotNone(got)
            assert got is not None

            # S_global = 80 - 20 + 2*0 = 60
            self.assertAlmostEqual(got.global_score, 60.0, places=6)
            # alpha=beta 且同秒结算，S_local 应接近 1
            self.assertAlmostEqual(got.local_score, 1.0, places=4)
            # 置信度权重应在 (0, 1] 区间
            self.assertGreater(got.confidence_score, 0.0)
            self.assertLessEqual(got.confidence_score, 1.0)
            # S_final = S_global * S_local * w
            self.assertAlmostEqual(
                got.final_score,
                got.global_score * got.local_score * got.confidence_score,
                places=6,
            )
        finally:
            store.close()

    def test_chroma_sync_only_updates_when_cid_changes(self) -> None:
        """
        Chroma 更新契约：
        1. 首次写入触发 upsert；
        2. CID 不变时不触发更新；
        3. CID 变化时再次触发 upsert。
        """
        store = SQLiteStateStore(":memory:")
        vector_index = _FakeVectorIndex()
        try:
            store.init_db()
            orchestrator = SyncOrchestrator(state_store=store, vector_index=vector_index)
            orchestrator._load_metadata = lambda cid, expected_did="": {  # type: ignore[method-assign]
                "sha256": f"sha-{cid}",
                "vector_text": f"text-{cid}",
            }

            state_v1 = AgentState(
                agent_address="0x11",
                did="did:demo",
                metadata_cid="cid-1",
                is_registered=True,
                is_slashed=False,
                last_event_block=1,
            )
            orchestrator._persist_states([state_v1])
            self.assertEqual(len(vector_index.upserts), 1)

            state_same_cid = AgentState(
                agent_address="0x11",
                did="did:demo",
                metadata_cid="cid-1",
                is_registered=True,
                is_slashed=False,
                last_event_block=2,
            )
            orchestrator._persist_states([state_same_cid])
            self.assertEqual(len(vector_index.upserts), 1)

            state_new_cid = AgentState(
                agent_address="0x11",
                did="did:demo",
                metadata_cid="cid-2",
                is_registered=True,
                is_slashed=False,
                last_event_block=3,
            )
            orchestrator._persist_states([state_new_cid])
            self.assertEqual(len(vector_index.upserts), 2)
            self.assertEqual(vector_index.upserts[-1]["metadata"]["metadata_cid"], "cid-2")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
