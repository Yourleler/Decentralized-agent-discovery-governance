"""
本文件应该做什么：
1. 提供最小语义检索服务接口（输入 query，返回 top-k agent）。
2. 组合 Chroma 召回与 SQLite 状态过滤（只保留可用 agent）。
3. 按“语义主导 + 信誉微调”做排序，确保相近功能优先。
4. 对上层返回稳定结构，避免直接暴露底层实现细节。
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Callable

import requests
from sidecar.storage.sqlite_state import AgentState, SQLiteStateStore
from sidecar.vector import ChromaIndex


@dataclass(slots=True)
class SearchResult:
    """
    检索结果结构。

    字段说明：
    - agent_address: Agent 地址。
    - did: Agent DID。
    - metadata_cid: 元数据 CID。
    - final_score: 评分融合后的最终分（S_final）。
    - semantic_distance: 语义距离分（越小越相近）。
    """

    agent_address: str
    did: str
    metadata_cid: str
    final_score: float
    semantic_distance: float


class DiscoveryService:
    """
    最小检索服务。

    排序策略（语义优先）：
    1. 先做语义召回，再做硬过滤：distance <= min(max_distance, d_min + distance_window)。
    2. 语义分：sem = exp(-semantic_decay * distance)。
    3. 信誉分压缩：trust = sigmoid((S_final - trust_center) / trust_scale)。
    4. 最终分：rank = sem * (trust_boost_base + trust_boost_gain * trust)。
    5. 对排序靠前候选做轻量可用性探测，不可用则过滤。

    关键参数分组：
    - 召回与语义过滤：`recall_multiplier/recall_floor/max_distance/distance_window/semantic_decay`
    - 信誉微调：`trust_center/trust_scale/trust_boost_base/trust_boost_gain`
    - 运行时探测：`runtime_probe_*` 系列参数
    """

    def __init__(
        self,
        state_store: SQLiteStateStore,
        vector_index: ChromaIndex,
        recall_multiplier: int = 8,
        recall_floor: int = 40,
        max_distance: float = 0.55,
        distance_window: float = 0.22,
        semantic_decay: float = 3.2,
        trust_center: float = 60.0,
        trust_scale: float = 14.0,
        trust_boost_base: float = 0.90,
        trust_boost_gain: float = 0.20,
        runtime_probe_top_n: int = 3,
        runtime_probe_ttl_seconds: int = 120,
        runtime_probe_timeout_seconds: float = 1.5,
        runtime_failure_threshold: int = 3,
        runtime_cooldown_seconds: int = 600,
        runtime_probe_enabled: bool = True,
        runtime_probe_func: Callable[[str, float], bool] | None = None,
    ) -> None:
        """
        初始化检索服务。

        参数：
        - state_store: 本地状态仓储（读取 Agent 评分、状态与探测信息）。
        - vector_index: 向量索引实例（负责语义召回）。
        - recall_multiplier: 召回放大倍数，`recall_k = max(top_k * recall_multiplier, recall_floor)`。
        - recall_floor: 最小召回条数下限。
        - max_distance: 语义硬阈值上限（超过即过滤）。
        - distance_window: 相对最优距离窗口，控制“与最优候选的可接受差距”。
        - semantic_decay: 语义距离衰减系数，越大越强调距离差异。
        - trust_center: 信誉压缩中心点（Sigmoid 中点）。
        - trust_scale: 信誉压缩尺度，越小对分差越敏感。
        - trust_boost_base: 信誉倍率基线。
        - trust_boost_gain: 信誉倍率增益幅度。
        - runtime_probe_top_n: 仅对排序前 N 候选执行在线探测。
        - runtime_probe_ttl_seconds: 探测结果缓存时长（TTL）。
        - runtime_probe_timeout_seconds: 单次在线探测超时。
        - runtime_failure_threshold: 连续失败达到该阈值后进入冷却。
        - runtime_cooldown_seconds: 冷却时长；冷却内默认过滤。
        - runtime_probe_enabled: 是否启用在线探测逻辑。
        - runtime_probe_func: 自定义探测函数；为空时使用默认 HTTP GET 探测。

        返回：
        - None: 完成参数校验与实例初始化。
        """
        if recall_multiplier <= 0:
            raise ValueError("recall_multiplier 必须为正整数")
        if recall_floor <= 0:
            raise ValueError("recall_floor 必须为正整数")
        if max_distance <= 0:
            raise ValueError("max_distance 必须为正数")
        if distance_window < 0:
            raise ValueError("distance_window 不能为负数")
        if semantic_decay <= 0:
            raise ValueError("semantic_decay 必须为正数")
        if trust_scale <= 0:
            raise ValueError("trust_scale 必须为正数")
        if trust_boost_base <= 0:
            raise ValueError("trust_boost_base 必须为正数")
        if trust_boost_gain < 0:
            raise ValueError("trust_boost_gain 不能为负数")
        if runtime_probe_top_n < 0:
            raise ValueError("runtime_probe_top_n 不能为负数")
        if runtime_probe_ttl_seconds < 0:
            raise ValueError("runtime_probe_ttl_seconds 不能为负数")
        if runtime_probe_timeout_seconds <= 0:
            raise ValueError("runtime_probe_timeout_seconds 必须为正数")
        if runtime_failure_threshold <= 0:
            raise ValueError("runtime_failure_threshold 必须为正整数")
        if runtime_cooldown_seconds < 0:
            raise ValueError("runtime_cooldown_seconds 不能为负数")

        self.state_store = state_store
        self.vector_index = vector_index
        self.recall_multiplier = int(recall_multiplier)
        self.recall_floor = int(recall_floor)
        self.max_distance = float(max_distance)
        self.distance_window = float(distance_window)
        self.semantic_decay = float(semantic_decay)
        self.trust_center = float(trust_center)
        self.trust_scale = float(trust_scale)
        self.trust_boost_base = float(trust_boost_base)
        self.trust_boost_gain = float(trust_boost_gain)
        self.runtime_probe_top_n = int(runtime_probe_top_n)
        self.runtime_probe_ttl_seconds = int(runtime_probe_ttl_seconds)
        self.runtime_probe_timeout_seconds = float(runtime_probe_timeout_seconds)
        self.runtime_failure_threshold = int(runtime_failure_threshold)
        self.runtime_cooldown_seconds = int(runtime_cooldown_seconds)
        self.runtime_probe_enabled = bool(runtime_probe_enabled)
        self.runtime_probe_func = runtime_probe_func or _default_runtime_probe

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """执行语义检索并按语义主导策略返回 top-k 结果。"""
        text = query.strip()
        if not text or top_k <= 0:
            return []

        recall_k = max(top_k * self.recall_multiplier, self.recall_floor)
        hits = self.vector_index.query(text=text, top_k=recall_k)

        candidates: list[SearchResult] = []
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
            candidates.append(
                SearchResult(
                    agent_address=state.agent_address,
                    did=state.did,
                    metadata_cid=state.metadata_cid,
                    final_score=float(state.final_score),
                    semantic_distance=float(hit.score),
                )
            )

        if not candidates:
            return []

        min_distance = min(item.semantic_distance for item in candidates)
        distance_limit = min(self.max_distance, min_distance + self.distance_window)
        filtered = [item for item in candidates if item.semantic_distance <= distance_limit]
        if not filtered:
            return []

        ranked = sorted(
            filtered,
            key=lambda item: (
                self._rank_score(
                    semantic_distance=item.semantic_distance,
                    final_score=item.final_score,
                ),
                -item.semantic_distance,
                item.final_score,
            ),
            reverse=True,
        )
        if not ranked:
            return []

        available: list[SearchResult] = []
        for idx, item in enumerate(ranked):
            state = self.state_store.get_agent_state(item.agent_address)
            if state is None:
                continue
            if (not state.is_registered) or state.is_slashed:
                continue

            need_probe = idx < self.runtime_probe_top_n
            if self._is_runtime_available(state, need_probe=need_probe):
                available.append(item)
            if len(available) >= top_k:
                break
        return available

    def search_as_dicts(self, query: str, top_k: int = 5) -> list[dict[str, object]]:
        """字典形态返回，便于 CLI / API 直接输出。"""
        return [asdict(item) for item in self.search(query=query, top_k=top_k)]

    def _rank_score(self, semantic_distance: float, final_score: float) -> float:
        """计算最终排序分数。"""
        semantic_part = math.exp(-self.semantic_decay * max(semantic_distance, 0.0))
        trust_part = _sigmoid((final_score - self.trust_center) / self.trust_scale)
        trust_multiplier = self.trust_boost_base + self.trust_boost_gain * trust_part
        return semantic_part * trust_multiplier

    def _is_runtime_available(self, state: AgentState, need_probe: bool) -> bool:
        """
        运行时可用性判定：冷却失败过滤 + 轻探测刷新状态。

        参数：
        - state: 当前候选 Agent 的本地状态快照。
        - need_probe: 是否需要对该候选执行在线探测（通常仅对前 N 名）。

        返回：
        - bool: True 表示可用，False 表示应在本次结果中过滤。

        说明：
        - 当启用探测且满足探测条件时，会调用探测函数并把结果写回 SQLite。
        """
        now_ts = int(time.time())
        blocked = self._is_in_failure_cooldown(
            consecutive_failures=int(state.consecutive_probe_failures),
            last_probe_ts=int(state.last_probe_ts),
            now_ts=now_ts,
        )
        if (not self.runtime_probe_enabled) or (not need_probe):
            return not blocked

        probe_url = str(state.runtime_probe_url or "").strip()
        if not probe_url:
            return not blocked

        should_probe = (
            int(state.last_probe_ts) <= 0
            or self.runtime_probe_ttl_seconds == 0
            or (now_ts - int(state.last_probe_ts)) >= self.runtime_probe_ttl_seconds
            or blocked
        )
        if not should_probe:
            return not blocked

        ok = bool(self.runtime_probe_func(probe_url, self.runtime_probe_timeout_seconds))
        self.state_store.update_runtime_probe(
            agent_address=state.agent_address,
            success=ok,
            probe_ts=now_ts,
        )
        refreshed = self.state_store.get_agent_state(state.agent_address) or state
        blocked_after = self._is_in_failure_cooldown(
            consecutive_failures=int(refreshed.consecutive_probe_failures),
            last_probe_ts=int(refreshed.last_probe_ts),
            now_ts=now_ts,
        )
        return not blocked_after

    def _is_in_failure_cooldown(
        self,
        consecutive_failures: int,
        last_probe_ts: int,
        now_ts: int,
    ) -> bool:
        """
        判断是否处于“连续失败冷却期”。

        参数：
        - consecutive_failures: 当前连续失败次数。
        - last_probe_ts: 最近一次探测时间戳（秒）。
        - now_ts: 当前时间戳（秒）。

        返回：
        - bool: True 表示命中冷却，应暂时屏蔽；False 表示不在冷却期。
        """
        if consecutive_failures < self.runtime_failure_threshold:
            return False
        if self.runtime_cooldown_seconds <= 0:
            return True
        if last_probe_ts <= 0:
            return True
        return (now_ts - last_probe_ts) < self.runtime_cooldown_seconds


def _sigmoid(value: float) -> float:
    """标准 Sigmoid 压缩函数。"""
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _default_runtime_probe(url: str, timeout_seconds: float) -> bool:
    """
    默认运行时探测实现。

    参数：
    - url: 目标探测地址。
    - timeout_seconds: 请求超时秒数。

    返回：
    - bool: 可达返回 True；网络异常或 5xx 返回 False。

    说明：
    - 采用 HTTP GET 探测；2xx/3xx/4xx 视为“服务可达”。
    """
    try:
        resp = requests.get(
            url,
            timeout=timeout_seconds,
            allow_redirects=True,
            headers={"User-Agent": "sidecar-runtime-probe/1.0"},
        )
    except requests.RequestException:
        return False
    return resp.status_code < 500
