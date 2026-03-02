"""
同步编排器（毕设流程版）。

流程：
1. 读取 watermark 作为起始区块。
2. 拉取子图增量数据。
3. 转换为 AgentState。
4. 可选执行预处理钩子（metadata/评分）。
5. 持久化到 SQLite。
6. 可选执行后处理钩子（向量索引）。
7. 推进 watermark。

前后钩子待完成
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from sidecar.adapters.subgraph_client import fetch_incremental_agents
from sidecar.storage.sqlite_state import AgentState, SQLiteStateStore

LOGGER = logging.getLogger(__name__)

PrePersistHook = Callable[[AgentState], AgentState | None]#提示做库前处理函数应遵守
PostPersistHook = Callable[[AgentState], None]#库后处理应遵守(向量化)


@dataclass
class SyncResult:
    """
    单轮同步结果。
    """

    from_block: int
    to_block: int
    fetched_count: int
    written_count: int
    reached_page_limit: bool


class SyncOrchestrator:
    """
    增量同步编排器。
    """

    def __init__(
        self,
        state_store: SQLiteStateStore,
        default_start_block: int = 10360984,
        pre_persist_hook: PrePersistHook | None = None,
        post_persist_hook: PostPersistHook | None = None,
    ):
        """
        初始化同步器。

        参数：
        - state_store: SQLite 状态仓储实例。
        - default_start_block: 未找到水位线时的默认起始区块。
        - pre_persist_hook: 写库前钩子（可用于 metadata/评分）。
        - post_persist_hook: 写库后钩子（可用于向量索引）。
        """
        self.state_store = state_store
        self.default_start_block = default_start_block
        self.pre_persist_hook = pre_persist_hook
        self.post_persist_hook = post_persist_hook

    def sync_once(self, first: int = 200, max_pages: int = 50) -> SyncResult:
        """
        执行单轮同步流程。

        参数：
        - first: 子图单页拉取条数。
        - max_pages: 单轮最大分页数。

        返回：
        - SyncResult: 本轮同步统计。
        """
        from_block = self._load_from_block()
        items, max_block, reached_page_limit = self._fetch_increment(from_block, first, max_pages)

        states = self._parse_items(items)
        written_count = self._persist_states(states)

        to_block = max(from_block, max_block)
        self._save_watermark(to_block)

        result = SyncResult(
            from_block=from_block,
            to_block=to_block,
            fetched_count=len(items),
            written_count=written_count,
            reached_page_limit=reached_page_limit,
        )
        LOGGER.info(
            "sync_once done: from=%s to=%s fetched=%s written=%s page_limit=%s",
            result.from_block,
            result.to_block,
            result.fetched_count,
            result.written_count,
            result.reached_page_limit,
        )
        return result

    def sync_until_caught_up(
        self,
        first: int = 200,
        max_pages: int = 50,
        max_rounds: int = 10,
    ) -> list[SyncResult]:
        """
        连续同步直到追平或达到轮次上限。

        参数：
        - first: 子图单页拉取条数。
        - max_pages: 单轮最大分页数。
        - max_rounds: 最大轮次数，避免无限循环。

        返回：
        - list[SyncResult]: 每轮同步结果。
        """
        if max_rounds <= 0:
            raise ValueError("max_rounds 必须为正整数")

        results: list[SyncResult] = []
        for _ in range(max_rounds):
            result = self.sync_once(first=first, max_pages=max_pages)
            results.append(result)
            if not result.reached_page_limit:
                break
            if result.written_count == 0:
                break
        return results

    def _load_from_block(self) -> int:
        """
        读取本轮同步起始区块。

        返回：
        - int: 起始区块号。
        """
        return self.state_store.get_watermark(default=self.default_start_block)

    @staticmethod
    def _fetch_increment(
        from_block: int,
        first: int,
        max_pages: int,
    ) -> tuple[list[dict[str, Any]], int, bool]:
        """
        拉取子图增量记录。

        参数：
        - from_block: 起始区块。
        - first: 单页条数。
        - max_pages: 最大分页数。

        返回：
        - tuple[list[dict[str, Any]], int, bool]: 记录列表、最大区块、分页上限标记。
        """
        return fetch_incremental_agents(
            from_block=from_block,
            first=first,
            max_pages=max_pages,
        )

    def _parse_items(self, items: list[dict[str, Any]]) -> list[AgentState]:
        """
        将子图记录列表转换为 AgentState 列表。

        参数：
        - items: 子图返回记录列表。

        返回：
        - list[AgentState]: 可入库状态对象列表。
        """
        states: list[AgentState] = []
        for item in items:
            state = self._to_agent_state(item)
            if state is not None:
                states.append(state)
        return states

    def _persist_states(self, states: list[AgentState]) -> int:
        """
        持久化状态列表，并执行可选钩子。

        参数：
        - states: 待持久化状态列表。

        返回：
        - int: 实际写入条数。
        """
        written_count = 0
        for state in states:
            current = state
            if self.pre_persist_hook is not None:
                maybe_state = self.pre_persist_hook(current)
                if maybe_state is None:
                    continue
                current = maybe_state

            self.state_store.upsert_agent_state(current)
            written_count += 1

            if self.post_persist_hook is not None:
                self.post_persist_hook(current)
        return written_count

    def _save_watermark(self, block_number: int) -> None:
        """
        保存同步水位线。

        参数：
        - block_number: 新水位线区块号。
        """
        self.state_store.set_watermark(block_number)

    @staticmethod
    def _to_agent_state(item: dict[str, Any]) -> AgentState | None:
        """
        单条子图记录转 AgentState。

        参数：
        - item: 子图返回的单条 agent 字典。

        返回：
        - AgentState | None: 成功返回对象，缺少 id 返回 None。
        """
        agent_address = str(item.get("id", "")).strip().lower()
        if not agent_address:
            LOGGER.warning("skip item: missing id, item=%s", item)
            return None

        return AgentState(
            agent_address=agent_address,
            did=str(item.get("did", "") or ""),
            metadata_cid=str(item.get("cid", "") or ""),
            init_score=_to_int(item.get("initScore"), 0),
            accumulated_penalty=_to_int(item.get("accumulatedPenalty"), 0),
            last_misconduct_timestamp=_to_int(item.get("lastMisconductTimestamp"), 0),
            stake_amount=str(item.get("stakeAmount", "0") or "0"),
            is_slashed=_to_bool(item.get("slashed"), False),
            is_registered=_to_bool(item.get("isRegistered"), True),
            admin="",
            last_event_block=_to_int(item.get("lastUpdatedBlock"), 0),
        )


def _to_int(value: Any, default: int = 0) -> int:
    """
    安全整数转换。

    参数：
    - value: 待转换值。
    - default: 转换失败时返回值。

    返回：
    - int: 转换结果或默认值。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    """
    安全布尔转换。

    参数：
    - value: 待转换值。
    - default: 无法识别时返回值。

    返回：
    - bool: 转换结果或默认值。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return default
