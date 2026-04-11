"""
SQLite 状态存储。

职责：
1. 持久化 agent_state（本地代理状态）。
2. 持久化 sync_state（同步水位线等键值状态）。
3. 保存同步阶段计算出的评分字段（S_global / S_local / w / S_final）。
4. 保存向量化文本字段。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def _now_ts() -> int:
    """
    获取当前 Unix 时间戳（秒）。
    """
    return int(time.time())


@dataclass
class AgentState:
    """
    Agent 本地状态结构。

    字段说明：
    - agent_address: Agent 地址（主键）。
    - did/metadata_cid: 子图侧核心索引字段。
    - init_score/accumulated_penalty/last_misconduct_timestamp: 评分输入字段。
    - stake_amount: 质押金额（字符串，避免 SQLite INTEGER 溢出）。
    - is_slashed/is_registered: 状态位。
    - last_event_block: 最近事件区块号，用于幂等覆盖。
    - alpha/beta/last_score_update_ts: 链下信誉证据与上次结算时间。
    - global_score/local_score/confidence_score/final_score: 评分结果字段。
    - metadata_sha256/vector_text: CID 对应元数据摘要与向量化文本。
    - runtime_probe_url: 运行时探测地址（从 metadata 提取）。
    - last_probe_ts/last_probe_success_ts: 最近探测时间与最近成功时间。
    - consecutive_probe_failures: 连续探测失败次数（用于冷却过滤）。
    - recent_7d_calls: 最近 7 天本地记录到的访问次数。
    - updated_at: 本地记录更新时间戳（秒）。
    """

    agent_address: str
    did: str = ""
    metadata_cid: str = ""
    init_score: int = 0
    accumulated_penalty: int = 0
    last_misconduct_timestamp: int = 0
    stake_amount: str = "0"
    is_slashed: bool = False
    is_registered: bool = True
    admin: str = ""
    last_event_block: int = 0
    alpha: float = 1.0
    beta: float = 1.0
    last_score_update_ts: int = 0
    global_score: float = 0.0
    local_score: float = 1.0
    confidence_score: float = 1.0
    final_score: float = 0.0
    metadata_sha256: str = ""
    vector_text: str = ""
    runtime_probe_url: str = ""
    last_probe_ts: int = 0
    last_probe_success_ts: int = 0
    consecutive_probe_failures: int = 0
    recent_7d_calls: int = 0
    updated_at: int = 0

    def to_db_params(self) -> dict[str, Any]:
        """
        转换为 SQLite 参数字典。

        返回：
        - dict[str, Any]: 可直接用于 execute(sql, params) 的参数映射。
        """
        data = asdict(self)
        data["agent_address"] = self.agent_address.lower().strip()
        data["did"] = self.did.strip()
        data["metadata_cid"] = self.metadata_cid.strip()
        data["stake_amount"] = str(self.stake_amount)
        data["is_slashed"] = 1 if self.is_slashed else 0
        data["is_registered"] = 1 if self.is_registered else 0
        data["admin"] = self.admin.lower().strip()
        data["alpha"] = float(self.alpha)
        data["beta"] = float(self.beta)
        data["last_score_update_ts"] = int(self.last_score_update_ts)
        data["global_score"] = float(self.global_score)
        data["local_score"] = float(self.local_score)
        data["confidence_score"] = float(self.confidence_score)
        data["final_score"] = float(self.final_score)
        data["metadata_sha256"] = self.metadata_sha256.strip().lower()
        data["vector_text"] = self.vector_text.strip()
        data["runtime_probe_url"] = self.runtime_probe_url.strip()
        data["last_probe_ts"] = int(self.last_probe_ts)
        data["last_probe_success_ts"] = int(self.last_probe_success_ts)
        data["consecutive_probe_failures"] = int(self.consecutive_probe_failures)
        data["recent_7d_calls"] = int(self.recent_7d_calls)
        data["updated_at"] = self.updated_at or _now_ts()
        return data


@dataclass
class InteractionReceipt:
    """
    本地交互记录结构。

    字段说明：
    - owner_did: 当前这份本地记录归属的 DID（本地视角）。
    - peer_did: 对端 DID，用于上下文哈希与申诉提取。
    - caller_did/target_did: 调用关系双方 DID。
    - session_id/task_id: 会话/任务标识，便于串联一次交互。
    - stage: 交互阶段，例如 auth/probe/context。
    - status: 结果状态，例如 success/fail。
    - request_json/response_json: 原始请求/响应 JSON 串。
    - request_hash/response_hash: 结构化内容哈希。
    - latency_ms: 本次交互耗时（毫秒）。
    - source: 数据来源标签，例如 holder/verifier/fullflow。
    """

    receipt_id: int = 0
    owner_did: str = ""
    peer_did: str = ""
    caller_did: str = ""
    target_did: str = ""
    session_id: str = ""
    task_id: str = ""
    stage: str = ""
    status: str = "unknown"
    request_json: str = ""
    response_json: str = ""
    request_hash: str = ""
    response_hash: str = ""
    latency_ms: int = 0
    source: str = ""
    created_at: int = 0
    updated_at: int = 0


class SQLiteStateStore:
    """
    SQLite 状态仓储。

    构造参数：
    - db_path: SQLite 文件路径（支持 `:memory:`）。
    """

    WATERMARK_KEY = "last_synced_block"

    def __init__(self, db_path: str | Path):
        """
        初始化仓储实例。

        参数：
        - db_path: SQLite 数据库文件路径。
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 允许跨线程访问；线程安全由 _lock 串行化保证。
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def init_db(self) -> None:
        """
        初始化或迁移表结构。

        兼容策略：
        - 新库：按最新 DDL 建表；
        - 老库：通过 _ensure_column_exists 按列增量迁移，不破坏已有数据。
        """
        ddl_agent = """
        CREATE TABLE IF NOT EXISTS agent_state (
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
            alpha REAL NOT NULL DEFAULT 1,
            beta REAL NOT NULL DEFAULT 1,
            last_score_update_ts INTEGER NOT NULL DEFAULT 0,
            global_score REAL NOT NULL DEFAULT 0,
            local_score REAL NOT NULL DEFAULT 1,
            confidence_score REAL NOT NULL DEFAULT 1,
            final_score REAL NOT NULL DEFAULT 0,
            metadata_sha256 TEXT NOT NULL DEFAULT '',
            vector_text TEXT NOT NULL DEFAULT '',
            runtime_probe_url TEXT NOT NULL DEFAULT '',
            last_probe_ts INTEGER NOT NULL DEFAULT 0,
            last_probe_success_ts INTEGER NOT NULL DEFAULT 0,
            consecutive_probe_failures INTEGER NOT NULL DEFAULT 0,
            recent_7d_calls INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL
        );
        """
        ddl_sync = """
        CREATE TABLE IF NOT EXISTS sync_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        );
        """
        ddl_index_registered = """
        CREATE INDEX IF NOT EXISTS idx_agent_state_registered
        ON agent_state (is_registered);
        """
        ddl_index_final_score = """
        CREATE INDEX IF NOT EXISTS idx_agent_state_final_score
        ON agent_state (final_score DESC);
        """
        ddl_index_last_event_block = """
        CREATE INDEX IF NOT EXISTS idx_agent_state_last_event_block
        ON agent_state (last_event_block);
        """
        ddl_index_metadata_cid = """
        CREATE INDEX IF NOT EXISTS idx_agent_state_metadata_cid
        ON agent_state (metadata_cid);
        """
        ddl_interaction = """
        CREATE TABLE IF NOT EXISTS interaction_receipt (
            receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_did TEXT NOT NULL DEFAULT '',
            peer_did TEXT NOT NULL DEFAULT '',
            caller_did TEXT NOT NULL DEFAULT '',
            target_did TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            task_id TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'unknown',
            request_json TEXT NOT NULL DEFAULT '',
            response_json TEXT NOT NULL DEFAULT '',
            request_hash TEXT NOT NULL DEFAULT '',
            response_hash TEXT NOT NULL DEFAULT '',
            latency_ms INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        """
        ddl_index_receipt_owner_peer = """
        CREATE INDEX IF NOT EXISTS idx_interaction_receipt_owner_peer
        ON interaction_receipt (owner_did, peer_did, receipt_id);
        """
        ddl_index_receipt_target_created = """
        CREATE INDEX IF NOT EXISTS idx_interaction_receipt_target_created
        ON interaction_receipt (target_did, created_at DESC);
        """
        ddl_index_receipt_caller_created = """
        CREATE INDEX IF NOT EXISTS idx_interaction_receipt_caller_created
        ON interaction_receipt (caller_did, created_at DESC);
        """
        with self._lock, self._conn:
            self._conn.execute(ddl_agent)
            self._conn.execute(ddl_sync)
            self._conn.execute(ddl_interaction)

            # 老库升级：补齐简化方案新增字段。
            self._ensure_column_exists("agent_state", "alpha", "REAL NOT NULL DEFAULT 1")
            self._ensure_column_exists("agent_state", "beta", "REAL NOT NULL DEFAULT 1")
            self._ensure_column_exists("agent_state", "last_score_update_ts", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column_exists("agent_state", "global_score", "REAL NOT NULL DEFAULT 0")
            self._ensure_column_exists("agent_state", "local_score", "REAL NOT NULL DEFAULT 1")
            self._ensure_column_exists("agent_state", "confidence_score", "REAL NOT NULL DEFAULT 1")
            self._ensure_column_exists("agent_state", "final_score", "REAL NOT NULL DEFAULT 0")
            self._ensure_column_exists("agent_state", "metadata_sha256", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column_exists("agent_state", "vector_text", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column_exists("agent_state", "runtime_probe_url", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column_exists("agent_state", "last_probe_ts", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column_exists("agent_state", "last_probe_success_ts", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column_exists("agent_state", "consecutive_probe_failures", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column_exists("agent_state", "recent_7d_calls", "INTEGER NOT NULL DEFAULT 0")

            # 索引放在补列之后，兼容老库迁移场景。
            self._conn.execute(ddl_index_registered)
            self._conn.execute(ddl_index_final_score)
            self._conn.execute(ddl_index_last_event_block)
            self._conn.execute(ddl_index_metadata_cid)
            self._conn.execute(ddl_index_receipt_owner_peer)
            self._conn.execute(ddl_index_receipt_target_created)
            self._conn.execute(ddl_index_receipt_caller_created)

    def close(self) -> None:
        """
        关闭数据库连接。
        """
        with self._lock:
            self._conn.close()

    def upsert_agent_state(self, state: AgentState) -> None:
        """
        写入或更新 Agent 状态。

        参数：
        - state: 需要写入的 AgentState 对象。

        规则：
        - 仅当新区块号 >= 库内区块号时允许覆盖，防止乱序事件回滚。
        """
        params = state.to_db_params()
        sql = """
        INSERT INTO agent_state (
            agent_address, did, metadata_cid, init_score, accumulated_penalty,
            last_misconduct_timestamp, stake_amount, is_slashed, is_registered,
            admin, last_event_block, alpha, beta, last_score_update_ts,
            global_score, local_score, confidence_score, final_score,
            metadata_sha256, vector_text, runtime_probe_url, last_probe_ts,
            last_probe_success_ts, consecutive_probe_failures, recent_7d_calls,
            updated_at
        ) VALUES (
            :agent_address, :did, :metadata_cid, :init_score, :accumulated_penalty,
            :last_misconduct_timestamp, :stake_amount, :is_slashed, :is_registered,
            :admin, :last_event_block, :alpha, :beta, :last_score_update_ts,
            :global_score, :local_score, :confidence_score, :final_score,
            :metadata_sha256, :vector_text, :runtime_probe_url, :last_probe_ts,
            :last_probe_success_ts, :consecutive_probe_failures, :recent_7d_calls,
            :updated_at
        )
        ON CONFLICT(agent_address) DO UPDATE SET
            did = excluded.did,
            metadata_cid = excluded.metadata_cid,
            init_score = excluded.init_score,
            accumulated_penalty = excluded.accumulated_penalty,
            last_misconduct_timestamp = excluded.last_misconduct_timestamp,
            stake_amount = excluded.stake_amount,
            is_slashed = excluded.is_slashed,
            is_registered = excluded.is_registered,
            admin = excluded.admin,
            last_event_block = excluded.last_event_block,
            alpha = excluded.alpha,
            beta = excluded.beta,
            last_score_update_ts = excluded.last_score_update_ts,
            global_score = excluded.global_score,
            local_score = excluded.local_score,
            confidence_score = excluded.confidence_score,
            final_score = excluded.final_score,
            metadata_sha256 = excluded.metadata_sha256,
            vector_text = excluded.vector_text,
            runtime_probe_url = excluded.runtime_probe_url,
            last_probe_ts = excluded.last_probe_ts,
            last_probe_success_ts = excluded.last_probe_success_ts,
            consecutive_probe_failures = excluded.consecutive_probe_failures,
            recent_7d_calls = excluded.recent_7d_calls,
            updated_at = excluded.updated_at
        WHERE excluded.last_event_block >= agent_state.last_event_block;
        """
        with self._lock, self._conn:
            self._conn.execute(sql, params)

    def append_interaction_receipt(
        self,
        owner_did: str,
        peer_did: str,
        caller_did: str,
        target_did: str,
        request_data: Any,
        response_data: Any,
        stage: str = "",
        status: str = "unknown",
        latency_ms: int = 0,
        session_id: str = "",
        task_id: str = "",
        source: str = "",
        created_at: int | None = None,
    ) -> int:
        """
        追加一条本地交互记录，并同步刷新目标 Agent 的最近 7 天访问次数。

        返回：
        - int: 新插入记录的 receipt_id。
        """
        ts = int(created_at or _now_ts())
        owner = str(owner_did or "").strip()
        peer = str(peer_did or "").strip()
        caller = str(caller_did or "").strip()
        target = str(target_did or "").strip()
        request_json = self._stable_json_dumps(request_data)
        response_json = self._stable_json_dumps(response_data)
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        response_hash = hashlib.sha256(response_json.encode("utf-8")).hexdigest()
        sql = """
        INSERT INTO interaction_receipt (
            owner_did, peer_did, caller_did, target_did, session_id, task_id,
            stage, status, request_json, response_json, request_hash, response_hash,
            latency_ms, source, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                sql,
                (
                    owner,
                    peer,
                    caller,
                    target,
                    str(session_id or "").strip(),
                    str(task_id or "").strip(),
                    str(stage or "").strip(),
                    str(status or "unknown").strip() or "unknown",
                    request_json,
                    response_json,
                    request_hash,
                    response_hash,
                    max(0, int(latency_ms or 0)),
                    str(source or "").strip(),
                    ts,
                    ts,
                ),
            )
            receipt_id = int(cur.lastrowid or 0)
            if target:
                self._refresh_recent_7d_calls_for_target(target_did=target, now_ts=ts)
        return receipt_id

    def count_recent_calls_by_target_did(
        self,
        target_did: str,
        now_ts: int | None = None,
        window_days: int = 7,
    ) -> int:
        """
        统计目标 DID 在最近若干天内被记录到的访问次数。
        """
        target = str(target_did or "").strip()
        if not target:
            return 0
        cutoff_ts = int(now_ts or _now_ts()) - max(1, int(window_days)) * 86400
        sql = """
        SELECT COUNT(*) AS total
        FROM interaction_receipt
        WHERE target_did = ? AND created_at >= ?
        """
        with self._lock:
            row = self._conn.execute(sql, (target, cutoff_ts)).fetchone()
        if row is None:
            return 0
        return int(row["total"] or 0)

    def list_interaction_receipts(
        self,
        owner_did: str | None = None,
        peer_did: str | None = None,
        caller_did: str | None = None,
        target_did: str | None = None,
        since_ts: int | None = None,
        limit: int = 200,
    ) -> list[InteractionReceipt]:
        """
        按条件读取本地交互记录。
        """
        if limit <= 0:
            return []
        clauses: list[str] = []
        params: list[Any] = []
        if owner_did:
            clauses.append("owner_did = ?")
            params.append(str(owner_did).strip())
        if peer_did:
            clauses.append("peer_did = ?")
            params.append(str(peer_did).strip())
        if caller_did:
            clauses.append("caller_did = ?")
            params.append(str(caller_did).strip())
        if target_did:
            clauses.append("target_did = ?")
            params.append(str(target_did).strip())
        if since_ts is not None:
            clauses.append("created_at >= ?")
            params.append(int(since_ts))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
        SELECT * FROM interaction_receipt
        {where_sql}
        ORDER BY receipt_id ASC
        LIMIT ?
        """
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_interaction_receipt(row) for row in rows]

    def build_interaction_snapshot_hash(self, owner_did: str, peer_did: str) -> tuple[str, int]:
        """
        基于 owner/peer 维度构造与旧 memory JSON 兼容的上下文快照哈希。

        返回：
        - tuple[str, int]: (快照哈希, 参与哈希的请求/响应对象数量)
        """
        owner = str(owner_did or "").strip()
        peer = str(peer_did or "").strip()
        if not owner or not peer:
            empty = self._stable_json_dumps([])
            return hashlib.sha256(empty.encode("utf-8")).hexdigest(), 0

        sql = """
        SELECT request_json, response_json
        FROM interaction_receipt
        WHERE owner_did = ? AND peer_did = ?
        ORDER BY receipt_id ASC
        """
        items: list[Any] = []
        with self._lock:
            rows = self._conn.execute(sql, (owner, peer)).fetchall()
        for row in rows:
            items.append(self._safe_json_loads(str(row["request_json"])))
            items.append(self._safe_json_loads(str(row["response_json"])))
        serialized = self._stable_json_dumps(items)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest(), len(items)

    def clear_interaction_history(self, owner_did: str, peer_did: str) -> int:
        """
        清空指定 owner/peer 对应的本地交互记录。
        """
        owner = str(owner_did or "").strip()
        peer = str(peer_did or "").strip()
        if not owner or not peer:
            return 0
        target_sql = """
        SELECT DISTINCT target_did
        FROM interaction_receipt
        WHERE owner_did = ? AND peer_did = ?
        """
        delete_sql = """
        DELETE FROM interaction_receipt
        WHERE owner_did = ? AND peer_did = ?
        """
        with self._lock, self._conn:
            rows = self._conn.execute(target_sql, (owner, peer)).fetchall()
            targets = [str(row["target_did"]) for row in rows if row["target_did"]]
            cur = self._conn.execute(delete_sql, (owner, peer))
            for target in targets:
                self._refresh_recent_7d_calls_for_target(target, now_ts=_now_ts())
        return int(cur.rowcount or 0)

    def build_appeal_payload(
        self,
        owner_did: str,
        peer_did: str,
        limit: int = 200,
    ) -> dict[str, Any]:
        """
        将本地交互记录整理成申诉证据载荷。
        """
        receipts = self.list_interaction_receipts(
            owner_did=owner_did,
            peer_did=peer_did,
            limit=limit,
        )
        return {
            "owner_did": str(owner_did or "").strip(),
            "peer_did": str(peer_did or "").strip(),
            "exported_at": _now_ts(),
            "receipt_count": len(receipts),
            "items": [
                {
                    "receipt_id": item.receipt_id,
                    "caller_did": item.caller_did,
                    "target_did": item.target_did,
                    "session_id": item.session_id,
                    "task_id": item.task_id,
                    "stage": item.stage,
                    "status": item.status,
                    "request_hash": item.request_hash,
                    "response_hash": item.response_hash,
                    "latency_ms": item.latency_ms,
                    "source": item.source,
                    "created_at": item.created_at,
                    "request": self._safe_json_loads(item.request_json),
                    "response": self._safe_json_loads(item.response_json),
                }
                for item in receipts
            ],
        }

    def update_runtime_probe(self, agent_address: str, success: bool, probe_ts: int | None = None) -> None:
        """
        更新运行时探测结果（用于发现阶段可用性过滤）。

        参数：
        - agent_address: 目标 Agent 地址（会被规范化为小写后写库）。
        - success: 本次探测是否成功。
          - True: 记录最近成功时间并将连续失败次数清零。
          - False: 仅记录探测时间并将连续失败次数 +1。
        - probe_ts: 探测时间戳（秒）；为空时使用当前时间。

        返回：
        - None: 仅执行数据库更新，不返回业务数据。
        """
        key = agent_address.lower().strip()
        if not key:
            return
        ts = int(probe_ts or _now_ts())
        with self._lock, self._conn:
            if success:
                self._conn.execute(
                    """
                    UPDATE agent_state
                    SET
                        last_probe_ts = ?,
                        last_probe_success_ts = ?,
                        consecutive_probe_failures = 0,
                        updated_at = ?
                    WHERE agent_address = ?
                    """,
                    (ts, ts, ts, key),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE agent_state
                    SET
                        last_probe_ts = ?,
                        consecutive_probe_failures = consecutive_probe_failures + 1,
                        updated_at = ?
                    WHERE agent_address = ?
                    """,
                    (ts, ts, key),
                )

    def get_agent_state(self, agent_address: str) -> AgentState | None:
        """
        读取单个 Agent 状态。

        参数：
        - agent_address: Agent 地址。

        返回：
        - AgentState | None: 命中返回对象，未命中返回 None。
        """
        sql = "SELECT * FROM agent_state WHERE agent_address = ?"
        key = agent_address.lower().strip()
        with self._lock:
            row = self._conn.execute(sql, (key,)).fetchone()
        if row is None:
            return None
        return self._row_to_agent_state(row)

    def list_agent_states(
        self,
        only_registered: bool = True,
        limit: int = 200,
        offset: int = 0,
    ) -> list[AgentState]:
        """
        分页读取 Agent 状态列表。

        参数：
        - only_registered: True 时只返回已注册 Agent。
        - limit: 单次返回上限。
        - offset: 分页偏移量。

        返回：
        - list[AgentState]: 状态对象列表。
        """
        if limit <= 0:
            return []

        if only_registered:
            sql = """
            SELECT * FROM agent_state
            WHERE is_registered = 1
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """
        else:
            sql = """
            SELECT * FROM agent_state
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """

        with self._lock:
            rows = self._conn.execute(sql, (limit, offset)).fetchall()
        return [self._row_to_agent_state(row) for row in rows]

    def list_agent_states_for_rescore(
        self,
        limit: int = 200,
        offset: int = 0,
    ) -> list[AgentState]:
        """
        读取全量 Agent 状态（用于全量重算评分）。

        说明：
        - 固定按 agent_address 排序，避免重算过程中 updated_at 变化导致分页漂移。
        """
        if limit <= 0:
            return []
        sql = """
        SELECT * FROM agent_state
        ORDER BY agent_address ASC
        LIMIT ? OFFSET ?
        """
        with self._lock:
            rows = self._conn.execute(sql, (limit, offset)).fetchall()
        return [self._row_to_agent_state(row) for row in rows]

    def delete_agent_state(self, agent_address: str) -> int:
        """
        删除指定 Agent 状态。

        参数：
        - agent_address: 目标 Agent 地址。

        返回：
        - int: 删除行数（0 或 1）。
        """
        sql = "DELETE FROM agent_state WHERE agent_address = ?"
        key = agent_address.lower().strip()
        with self._lock, self._conn:
            cur = self._conn.execute(sql, (key,))
        return int(cur.rowcount or 0)

    def set_sync_state(self, state_key: str, state_value: str) -> None:
        """
        写入同步状态键值。

        参数：
        - state_key: 状态键。
        - state_value: 状态值（字符串）。
        """
        sql = """
        INSERT INTO sync_state (state_key, state_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(state_key) DO UPDATE SET
            state_value = excluded.state_value,
            updated_at = excluded.updated_at
        """
        with self._lock, self._conn:
            self._conn.execute(sql, (state_key, state_value, _now_ts()))

    def get_sync_state(self, state_key: str, default: str | None = None) -> str | None:
        """
        读取同步状态键值。

        参数：
        - state_key: 状态键。
        - default: 未命中时返回的默认值。

        返回：
        - str | None: 命中返回字符串；未命中返回 default。
        """
        sql = "SELECT state_value FROM sync_state WHERE state_key = ?"
        with self._lock:
            row = self._conn.execute(sql, (state_key,)).fetchone()
        if row is None:
            return default
        return str(row["state_value"])

    def set_watermark(self, block_number: int) -> None:
        """
        设置同步水位线。

        参数：
        - block_number: 已成功处理完成的最高区块号。
        """
        if block_number < 0:
            raise ValueError("block_number 不能为负数")
        self.set_sync_state(self.WATERMARK_KEY, str(block_number))

    def get_watermark(self, default: int = 10360859) -> int:
        """
        获取同步水位线。

        参数：
        - default: 水位线缺失/非法时的回退值。

        返回：
        - int: 当前有效水位线。
        """
        raw = self.get_sync_state(self.WATERMARK_KEY, None)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if value >= 0 else default

    def _ensure_column_exists(self, table: str, column: str, definition: str) -> None:
        """
        若列不存在则执行 ALTER TABLE 增加列。

        参数：
        - table: 表名。
        - column: 列名。
        - definition: 列定义（含类型与默认值）。
        """
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        exists = any(str(row["name"]) == column for row in rows)
        if not exists:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _row_to_agent_state(row: sqlite3.Row) -> AgentState:
        """
        将 sqlite3.Row 转换为 AgentState。

        兼容说明：
        - 对老库可能不存在的新列（评分字段/metadata 字段）做兜底。
        """
        keys = set(row.keys())
        alpha = 1.0
        if "alpha" in keys and row["alpha"] is not None:
            alpha = float(row["alpha"])

        beta = 1.0
        if "beta" in keys and row["beta"] is not None:
            beta = float(row["beta"])

        last_score_update_ts = 0
        if "last_score_update_ts" in keys and row["last_score_update_ts"] is not None:
            last_score_update_ts = int(row["last_score_update_ts"])

        global_score = 0.0
        if "global_score" in keys and row["global_score"] is not None:
            global_score = float(row["global_score"])

        local_score = 1.0
        if "local_score" in keys and row["local_score"] is not None:
            local_score = float(row["local_score"])

        confidence_score = 1.0
        if "confidence_score" in keys and row["confidence_score"] is not None:
            confidence_score = float(row["confidence_score"])

        final_score = 0.0
        if "final_score" in keys and row["final_score"] is not None:
            final_score = float(row["final_score"])

        metadata_sha256 = ""
        if "metadata_sha256" in keys and row["metadata_sha256"] is not None:
            metadata_sha256 = str(row["metadata_sha256"])

        vector_text = ""
        if "vector_text" in keys and row["vector_text"] is not None:
            vector_text = str(row["vector_text"])

        runtime_probe_url = ""
        if "runtime_probe_url" in keys and row["runtime_probe_url"] is not None:
            runtime_probe_url = str(row["runtime_probe_url"])

        last_probe_ts = 0
        if "last_probe_ts" in keys and row["last_probe_ts"] is not None:
            last_probe_ts = int(row["last_probe_ts"])

        last_probe_success_ts = 0
        if "last_probe_success_ts" in keys and row["last_probe_success_ts"] is not None:
            last_probe_success_ts = int(row["last_probe_success_ts"])

        consecutive_probe_failures = 0
        if "consecutive_probe_failures" in keys and row["consecutive_probe_failures"] is not None:
            consecutive_probe_failures = int(row["consecutive_probe_failures"])

        recent_7d_calls = 0
        if "recent_7d_calls" in keys and row["recent_7d_calls"] is not None:
            recent_7d_calls = int(row["recent_7d_calls"])

        return AgentState(
            agent_address=str(row["agent_address"]),
            did=str(row["did"]),
            metadata_cid=str(row["metadata_cid"]),
            init_score=int(row["init_score"]),
            accumulated_penalty=int(row["accumulated_penalty"]),
            last_misconduct_timestamp=int(row["last_misconduct_timestamp"]),
            stake_amount=str(row["stake_amount"]),
            is_slashed=bool(row["is_slashed"]),
            is_registered=bool(row["is_registered"]),
            admin=str(row["admin"]),
            last_event_block=int(row["last_event_block"]),
            alpha=alpha,
            beta=beta,
            last_score_update_ts=last_score_update_ts,
            global_score=global_score,
            local_score=local_score,
            confidence_score=confidence_score,
            final_score=final_score,
            metadata_sha256=metadata_sha256,
            vector_text=vector_text,
            runtime_probe_url=runtime_probe_url,
            last_probe_ts=last_probe_ts,
            last_probe_success_ts=last_probe_success_ts,
            consecutive_probe_failures=consecutive_probe_failures,
            recent_7d_calls=recent_7d_calls,
            updated_at=int(row["updated_at"]),
        )

    def _row_to_interaction_receipt(self, row: sqlite3.Row) -> InteractionReceipt:
        """
        将 sqlite3.Row 转为 InteractionReceipt。
        """
        return InteractionReceipt(
            receipt_id=int(row["receipt_id"]),
            owner_did=str(row["owner_did"]),
            peer_did=str(row["peer_did"]),
            caller_did=str(row["caller_did"]),
            target_did=str(row["target_did"]),
            session_id=str(row["session_id"]),
            task_id=str(row["task_id"]),
            stage=str(row["stage"]),
            status=str(row["status"]),
            request_json=str(row["request_json"]),
            response_json=str(row["response_json"]),
            request_hash=str(row["request_hash"]),
            response_hash=str(row["response_hash"]),
            latency_ms=int(row["latency_ms"]),
            source=str(row["source"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    def _refresh_recent_7d_calls_for_target(self, target_did: str, now_ts: int) -> None:
        """
        将指定 target DID 最近 7 天调用次数回写到 agent_state。
        """
        cutoff_ts = int(now_ts) - 7 * 86400
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM interaction_receipt
            WHERE target_did = ? AND created_at >= ?
            """,
            (str(target_did or "").strip(), cutoff_ts),
        ).fetchone()
        total = int(row["total"] or 0) if row is not None else 0
        sql = """
        UPDATE agent_state
        SET recent_7d_calls = ?, updated_at = ?
        WHERE did = ?
        """
        self._conn.execute(sql, (total, int(now_ts), str(target_did or "").strip()))

    @staticmethod
    def _stable_json_dumps(payload: Any) -> str:
        """
        统一 JSON 序列化，保证哈希稳定。
        """
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _safe_json_loads(raw: str) -> Any:
        """
        安全反序列化 JSON；失败时退回原始字符串。
        """
        try:
            return json.loads(raw)
        except Exception:
            return raw
