"""
同步编排器（简化版）。

流程：
1. 读取 watermark 作为起始区块。
2. 拉取子图增量数据。
3. 转换为 AgentState。
4. 计算评分字段（S_global / S_local / w / S_final）。
5. 仅在 CID 变化时拉取 IPFS metadata，并提取向量化文本。
6. 持久化到 SQLite。
7. 推进 watermark。

说明：
- `sync_once` 只处理本轮变更的 Agent；
- 全量重算由独立入口 `rescore_all()` 执行（适合定时任务）。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from infrastructure.ipfs import fetch_and_verify
from sidecar.adapters.subgraph_client import fetch_incremental_agents
from sidecar.storage.sqlite_state import AgentState, SQLiteStateStore
from sidecar.vector import ChromaIndex

LOGGER = logging.getLogger(__name__)

SECONDS_PER_DAY = 24 * 60 * 60

# 链上全局分恢复速率（与合约 SCORE_RECOVERY_RATE 对齐）
GLOBAL_RECOVERY_RATE_PER_DAY = 2.0

# 链下参数（对齐架构文档）
GAMMA_PER_SECOND = 0.9999997327
LOCAL_RECOVERY_R_PER_DAY = 0.2
LOCAL_K = 0.8
LOCAL_DELTA = 0.2
CONFIDENCE_C = 10.0
CONFIDENCE_LAMBDA = 0.15
MIN_EVIDENCE = 1e-9
MAX_VECTOR_TEXT_CHARS = 800


@dataclass
class SyncResult:
    """
    单轮同步结果。
    """

    from_block: int
    to_block: int
    fetched_count: int
    written_count: int
    rescored_count: int
    reached_page_limit: bool


class SyncOrchestrator:
    """
    增量同步编排器。
    """

    def __init__(
        self,
        state_store: SQLiteStateStore,
        default_start_block: int = 10360859,
        vector_index: ChromaIndex | None = None,
    ):
        """
        初始化同步器。

        参数：
        - state_store: SQLite 状态仓储实例。
        - default_start_block: 未找到水位线时的默认起始区块。
        """
        self.state_store = state_store
        self.default_start_block = default_start_block
        self.vector_index = vector_index

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
        written_count, rescored_count = self._persist_states(states)

        to_block = max(from_block, max_block)
        self._save_watermark(to_block)

        result = SyncResult(
            from_block=from_block,
            to_block=to_block,
            fetched_count=len(items),
            written_count=written_count,
            rescored_count=rescored_count,
            reached_page_limit=reached_page_limit,
        )
        LOGGER.info(
            "sync_once done: from=%s to=%s fetched=%s written=%s rescored=%s page_limit=%s",
            result.from_block,
            result.to_block,
            result.fetched_count,
            result.written_count,
            result.rescored_count,
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

    def _persist_states(self, states: list[AgentState]) -> tuple[int, int]:
        """
        持久化状态列表。

        参数：
        - states: 待持久化状态列表。

        返回：
        - tuple[int, int]:
          1) 实际写入条数；
          2) 本轮重算评分条数（与写入条数一致，仅覆盖本轮变更 Agent）。
        """
        written_count = 0
        rescored_count = 0
        now_ts = _now_ts()
        for state in states:
            previous = self.state_store.get_agent_state(state.agent_address)
            current = self._enrich_state(state, previous)
            self._compute_scores_inplace(current, now_ts)
            self.state_store.upsert_agent_state(current)
            self._sync_vector_index(current=current, previous=previous)
            written_count += 1
            rescored_count += 1
        return written_count, rescored_count

    def _sync_vector_index(self, current: AgentState, previous: AgentState | None) -> None:
        """
        仅在 metadata CID 变化时同步更新 Chroma 索引。
        """
        if self.vector_index is None:
            return

        current_cid = current.metadata_cid.strip()
        previous_cid = previous.metadata_cid.strip() if previous is not None else ""
        cid_changed = (previous is None) or (current_cid != previous_cid)
        if not cid_changed:
            return

        # metadata 软失败时会复用旧值，此时跳过向量更新，避免脏索引。
        if (
            previous is not None
            and current_cid != previous_cid
            and current.metadata_sha256 == previous.metadata_sha256
            and current.vector_text == previous.vector_text
        ):
            LOGGER.warning(
                "skip chroma sync because metadata refresh failed: agent=%s cid=%s",
                current.agent_address,
                current_cid,
            )
            return

        try:
            if (
                (not current_cid)
                or (not current.vector_text.strip())
                or (not current.is_registered)
                or current.is_slashed
            ):
                self.vector_index.delete(current.agent_address)
                return

            self.vector_index.upsert(
                agent_id=current.agent_address,
                vector_text=current.vector_text,
                metadata={
                    "did": current.did,
                    "metadata_cid": current.metadata_cid,
                    "metadata_sha256": current.metadata_sha256,
                },
            )
        except Exception as exc:
            LOGGER.warning(
                "chroma sync failed: agent=%s cid=%s err=%s",
                current.agent_address,
                current_cid,
                exc,
            )

    def rescore_all(self, batch_size: int = 500) -> int:
        """
        全量重算评分字段。

        说明：
        - 用于定时更新场景，即使没有新事件也会因时间因子更新评分；
        - 固定按地址顺序分页读取，避免分页漂移。
        """
        if batch_size <= 0:
            return 0

        now_ts = _now_ts()
        total = 0
        offset = 0
        while True:
            batch = self.state_store.list_agent_states_for_rescore(limit=batch_size, offset=offset)
            if not batch:
                break

            for state in batch:
                self._compute_scores_inplace(state, now_ts)
                self.state_store.upsert_agent_state(state)
                total += 1

            offset += len(batch)
        return total

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

    def _enrich_state(self, state: AgentState, previous: AgentState | None) -> AgentState:
        """
        写库前补充字段：
        1. 继承已有评分证据字段（alpha/beta/last_score_update_ts）；
        2. metadata_sha256/vector_text（仅在 CID 变动时更新）。
        """
        if previous is not None:
            state.alpha = previous.alpha
            state.beta = previous.beta
            state.last_score_update_ts = previous.last_score_update_ts
            state.global_score = previous.global_score
            state.local_score = previous.local_score
            state.confidence_score = previous.confidence_score
            state.final_score = previous.final_score

        cid = state.metadata_cid.strip()
        if not cid:
            state.metadata_sha256 = ""
            state.vector_text = ""
            return state

        if previous is not None and previous.metadata_cid.strip() == cid:
            state.metadata_sha256 = previous.metadata_sha256
            state.vector_text = previous.vector_text
            return state

        metadata_result = self._load_metadata(cid, expected_did=state.did)
        if metadata_result is None:
            # 软失败：保留旧值，不中断同步。
            if previous is not None:
                state.metadata_sha256 = previous.metadata_sha256
                state.vector_text = previous.vector_text
            else:
                state.metadata_sha256 = ""
                state.vector_text = ""
            return state

        state.metadata_sha256 = metadata_result["sha256"]
        state.vector_text = metadata_result["vector_text"]
        return state

    def _compute_scores_inplace(self, state: AgentState, now_ts: int) -> None:
        """
        按文档公式计算并回写：
        - S_global
        - S_local
        - w (confidence_score)
        - S_final
        """
        alpha = max(float(state.alpha), MIN_EVIDENCE)
        beta = max(float(state.beta), MIN_EVIDENCE)

        # 1) 时间遗忘：alpha/beta 指数衰减
        if state.last_score_update_ts > 0 and now_ts > state.last_score_update_ts:
            delta_seconds = now_ts - state.last_score_update_ts
            decay = math.pow(GAMMA_PER_SECOND, delta_seconds)
            alpha *= decay
            beta *= decay

        # 2) 无违规时间正向恢复：alpha += r * floor(delta_t_plus / day)
        baseline_ts = max(int(state.last_misconduct_timestamp), int(state.last_score_update_ts))
        if now_ts > baseline_ts:
            delta_plus_days = (now_ts - baseline_ts) // SECONDS_PER_DAY
            alpha += LOCAL_RECOVERY_R_PER_DAY * float(delta_plus_days)

        # 3) S_local = clip(1 + k*(p-0.5), 1-delta, 1+delta)
        evidence_sum = max(alpha + beta, MIN_EVIDENCE)
        p = alpha / evidence_sum
        s_local = 1.0 + LOCAL_K * (p - 0.5)
        s_local = _clip(s_local, 1.0 - LOCAL_DELTA, 1.0 + LOCAL_DELTA)

        # 4) w = 1 - lambda * (1 - min(1, ln(1+a+b) / ln(1+C)))
        confidence_ratio = math.log1p(evidence_sum) / math.log1p(CONFIDENCE_C)
        confidence_ratio = _clip(confidence_ratio, 0.0, 1.0)
        confidence_weight = 1.0 - CONFIDENCE_LAMBDA * (1.0 - confidence_ratio)

        # 5) S_global（与合约 T-CPRM 同语义）
        s_global = self._compute_global_score(state, now_ts)

        # 6) S_final = S_global * S_local * w
        s_final = s_global * s_local * confidence_weight

        state.alpha = alpha
        state.beta = beta
        state.last_score_update_ts = now_ts
        state.global_score = s_global
        state.local_score = s_local
        state.confidence_score = confidence_weight
        state.final_score = s_final

    @staticmethod
    def _compute_global_score(state: AgentState, now_ts: int) -> float:
        """
        全局分（链上语义）：
        S_global = min(S_init, S_init - P_total + rate * floor((now - T_last)/day))
        """
        if (not state.is_registered) or state.is_slashed:
            return 0.0

        base_score = float(state.init_score - state.accumulated_penalty)
        if base_score <= 0:
            return 0.0

        days_since_last = 0
        if state.last_misconduct_timestamp > 0 and now_ts > state.last_misconduct_timestamp:
            days_since_last = (now_ts - state.last_misconduct_timestamp) // SECONDS_PER_DAY

        score = base_score + GLOBAL_RECOVERY_RATE_PER_DAY * float(days_since_last)
        if score > float(state.init_score):
            return float(state.init_score)
        if score < 0:
            return 0.0
        return score

    def _load_metadata(self, cid: str, expected_did: str = "") -> dict[str, str] | None:
        """
        拉取并解析 metadata，失败时返回 None，不中断主同步。
        """
        try:
            payload = fetch_and_verify(cid)
        except Exception as exc:
            LOGGER.warning("ipfs fetch failed: cid=%s err=%s", cid, exc)
            return None

        metadata = payload.get("content")
        if not isinstance(metadata, dict):
            LOGGER.warning("ipfs content is not json object: cid=%s", cid)
            return None

        valid, errors = _validate_metadata_shape(metadata, expected_did=expected_did)
        if not valid:
            LOGGER.warning(
                "metadata template check failed: cid=%s errors=%s",
                cid,
                "; ".join(errors),
            )
            return None

        sha256 = str(payload.get("sha256", "") or "")
        vector_text = self._build_vector_text(metadata)
        return {
            "sha256": sha256,
            "vector_text": vector_text,
        }

    @staticmethod
    def _build_vector_text(metadata: dict[str, Any]) -> str:
        """
        从 metadata 提取向量化核心文本（毕设简化版）。

        策略：
        1. 若存在 indexHints.vectorText，优先使用；
        2. 否则仅使用 service.summary/domain/tags 与 capability.name/description；
        3. 对最终文本做长度裁剪，避免噪声过大影响召回质量。
        """
        index_hints = metadata.get("indexHints")
        if isinstance(index_hints, dict):
            hint_text = index_hints.get("vectorText")
            if isinstance(hint_text, str) and hint_text.strip():  # 非空或空格字符串
                return hint_text.strip()[:MAX_VECTOR_TEXT_CHARS]

        parts: list[str] = []

        service = metadata.get("service")
        if isinstance(service, dict):
            summary = str(service.get("summary", "") or "").strip()
            domain = str(service.get("domain", "") or "").strip()
            tags = _join_text_list(service.get("tags"))

            if summary:
                parts.append(summary)
            if domain:
                parts.append(f"Domain: {domain}")
            if tags:
                parts.append(f"Tags: {tags}")

        capabilities = metadata.get("capabilities")
        if isinstance(capabilities, list):
            for cap in capabilities:
                if not isinstance(cap, dict):
                    continue
                cap_name = str(cap.get("name", "") or "").strip()
                cap_desc = str(cap.get("description", "") or "").strip()

                if cap_name:
                    parts.append(f"Capability: {cap_name}")
                if cap_desc:
                    parts.append(cap_desc)

        compact = [p for p in parts if p]
        return "\n".join(compact)[:MAX_VECTOR_TEXT_CHARS]


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


def _join_text_list(value: Any) -> str:
    """把 list[str] 或混合列表安全拼接成文本。"""
    if not isinstance(value, list):
        return ""
    items = [str(v).strip() for v in value if str(v).strip()]
    return ", ".join(items)


def _clip(value: float, low: float, high: float) -> float:
    """把值裁剪到 [low, high] 区间。"""
    if value < low:
        return low
    if value > high:
        return high
    return value


def _now_ts() -> int:
    """返回当前 Unix 时间戳（秒）。"""
    import time

    return int(time.time())


def _validate_metadata_shape(
    metadata: dict[str, Any],
    expected_did: str = "",
) -> tuple[bool, list[str]]:
    """
    轻量模板校验（软校验）：
    - 仅校验关键字段存在与基本类型；
    - 失败仅记日志，不中断同步流程。
    """
    errors: list[str] = []

    required_top = [
        "metadataVersion",
        "agentDid",
        "service",
        "capabilities",
        "vcManifest",
        "timestamps",
    ]
    for key in required_top:
        if key not in metadata:
            errors.append(f"missing:{key}")

    agent_did = metadata.get("agentDid")
    if not isinstance(agent_did, str) or not agent_did.strip():
        errors.append("invalid:agentDid")

    if expected_did and isinstance(agent_did, str) and agent_did.strip() and agent_did.strip() != expected_did.strip():
        errors.append("mismatch:agentDid_vs_subgraph_did")

    service = metadata.get("service")
    if not isinstance(service, dict):
        errors.append("invalid:service")
    else:
        for key in ("name", "summary", "domain"):
            val = service.get(key)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"invalid:service.{key}")

    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, list) or len(capabilities) == 0:
        errors.append("invalid:capabilities")
    else:
        for idx, cap in enumerate(capabilities):
            if not isinstance(cap, dict):
                errors.append(f"invalid:capabilities[{idx}]")
                continue
            for key in ("name", "description"):
                val = cap.get(key)
                if not isinstance(val, str) or not val.strip():
                    errors.append(f"invalid:capabilities[{idx}].{key}")

    vc_manifest = metadata.get("vcManifest")
    if not isinstance(vc_manifest, dict):
        errors.append("invalid:vcManifest")
    else:
        holder_did = vc_manifest.get("holderDid")
        types = vc_manifest.get("types")
        lazy_fetch = vc_manifest.get("lazyFetch")
        if not isinstance(holder_did, str) or not holder_did.strip():
            errors.append("invalid:vcManifest.holderDid")
        if isinstance(agent_did, str) and isinstance(holder_did, str):
            if agent_did.strip() and holder_did.strip() and holder_did.strip() != agent_did.strip():
                errors.append("mismatch:vcManifest.holderDid_vs_agentDid")
        if not isinstance(types, list) or len(types) == 0:
            errors.append("invalid:vcManifest.types")
        if not isinstance(lazy_fetch, bool):
            errors.append("invalid:vcManifest.lazyFetch")

    timestamps = metadata.get("timestamps")
    if not isinstance(timestamps, dict):
        errors.append("invalid:timestamps")
    else:
        for key in ("createdAt", "updatedAt"):
            val = timestamps.get(key)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"invalid:timestamps.{key}")

    return len(errors) == 0, errors
