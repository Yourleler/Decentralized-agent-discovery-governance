"""
SQLite 状态存储（毕设简版）。

职责：
1. 持久化 agent_state（本地代理状态）。
2. 持久化 sync_state（同步水位线等键值状态）。
3. 提供简洁的读写接口，避免业务层直接写 SQL。
"""

from __future__ import annotations

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
    - did/cid: 子图侧核心索引字段。
    - init_score/accumulated_penalty/last_misconduct_timestamp: 评分相关字段。
    - stake_amount: 质押金额（字符串，避免 SQLite INTEGER 溢出）。
    - is_slashed/is_registered: 状态位。
    - last_event_block: 最近事件区块号，用于幂等覆盖。
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
        data["admin"] = self.admin.lower().strip()
        data["stake_amount"] = str(self.stake_amount)
        data["is_slashed"] = 1 if self.is_slashed else 0
        data["is_registered"] = 1 if self.is_registered else 0
        data["updated_at"] = self.updated_at or _now_ts()
        return data


class SQLiteStateStore:
    """
    SQLite 状态仓储。
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
        # 允许跨线程访问，线程安全由 _lock 串行化保证。
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def init_db(self) -> None:
        """
        初始化表结构（不存在则创建）。
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
        ddl_index = """
        CREATE INDEX IF NOT EXISTS idx_agent_state_registered
        ON agent_state (is_registered);
        """
        with self._lock, self._conn:
            self._conn.execute(ddl_agent)
            self._conn.execute(ddl_sync)
            self._conn.execute(ddl_index)

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
            admin, last_event_block, updated_at
        ) VALUES (
            :agent_address, :did, :metadata_cid, :init_score, :accumulated_penalty,
            :last_misconduct_timestamp, :stake_amount, :is_slashed, :is_registered,
            :admin, :last_event_block, :updated_at
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
            updated_at = excluded.updated_at
        WHERE excluded.last_event_block >= agent_state.last_event_block;
        """
        with self._lock, self._conn:
            self._conn.execute(sql, params)

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

    def get_watermark(self, default: int = 10360984) -> int:
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
    

    

    @staticmethod  ##静态函数,不加self参数,可以直接类名调用
    def _row_to_agent_state(row: sqlite3.Row) -> AgentState:
        """
        将 sqlite3.Row 转换为 AgentState。

        参数：
        - row: 查询结果行。

        返回：
        - AgentState: 转换后的对象。
        """
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
            updated_at=int(row["updated_at"]),
        )
