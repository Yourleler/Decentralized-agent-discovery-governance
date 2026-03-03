"""
Subgraph GraphQL 客户端适配层（毕设简版）。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import requests

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = current_dir
while not os.path.exists(os.path.join(project_root, "infrastructure")):
    parent = os.path.dirname(project_root)
    if parent == project_root:
        break
    project_root = parent
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from infrastructure.load_config import load_key_config
except ImportError:
    print("❌ 错误: 无法导入 infrastructure。")
    sys.exit(1)

LOGGER = logging.getLogger(__name__)

FALLBACK_SUBGRAPH_URL = "https://api.studio.thegraph.com/query/1740029/agent-registry-sepolia/version/latest"
KEY_CONFIG = load_key_config()
SUBGRAPH_URL = str(KEY_CONFIG.get("subgraph_url") or FALLBACK_SUBGRAPH_URL)
SUBGRAPH_API_KEY = KEY_CONFIG.get("subgraph_api_key")
SUBGRAPH_AUTH_HEADER = os.getenv("SUBGRAPH_AUTH_HEADER", "Authorization")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("SUBGRAPH_TIMEOUT_SECONDS", "20"))


def _build_headers() -> dict[str, str]:
    """
    组装请求头。

    返回：
    - dict[str, str]: GraphQL HTTP 请求头。
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "agent-sidecar/1.0",
    }
    if SUBGRAPH_API_KEY:
        token = SUBGRAPH_API_KEY
        if SUBGRAPH_AUTH_HEADER.lower() == "authorization" and not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        headers[SUBGRAPH_AUTH_HEADER] = token
    return headers


def _execute_graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    执行 GraphQL 请求。

    参数：
    - query: GraphQL 查询语句。
    - variables: 查询变量，默认 None。

    返回：
    - dict[str, Any]: GraphQL 响应 JSON。
    """
    body = {"query": query, "variables": variables or {}}
    try:
        resp = requests.post(
            SUBGRAPH_URL,
            json=body,
            headers=_build_headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Subgraph 网络请求失败: {exc}") from exc

    if resp.status_code >= 400:
        raise RuntimeError(f"Subgraph HTTP 错误: status={resp.status_code}, body={resp.text[:300]}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Subgraph 返回非 JSON: {resp.text[:300]}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Subgraph 返回格式非法：顶层不是 JSON 对象")
    if payload.get("errors"):
        raise RuntimeError(f"Subgraph 查询失败: {payload['errors']}")
    return payload


def fetch_incremental_agents(
    from_block: int,
    first: int = 200,
    max_pages: int = 50,
) -> tuple[list[dict[str, Any]], int, bool]:
    """
    拉取子图增量 Agent 数据。

    参数：
    - from_block: 增量同步起始区块（通常来自 watermark）。
    - first: 单页拉取条数。
    - max_pages: 最多拉取页数。

    返回：
    - tuple[list[dict[str, Any]], int, bool]
      1) 增量记录列表；
      2) 本轮最大区块号；
      3) 是否触发分页上限（True 表示可能还有数据未拉完）。
    """
    if from_block < 0:
        raise ValueError("from_block 不能为负数")
    if first <= 0:
        raise ValueError("first 必须为正整数")
    if max_pages <= 0:
        raise ValueError("max_pages 必须为正整数")

    query = """
query IncrementalAgents($fromBlock: BigInt!, $first: Int!, $skip: Int!) {
  agents(
    where: { lastUpdatedBlock_gte: $fromBlock }
    first: $first
    skip: $skip
    orderBy: lastUpdatedBlock
    orderDirection: asc
  ) {
    id
    did
    cid
    initScore
    stakeAmount
    accumulatedPenalty
    lastMisconductTimestamp
    slashed
    isRegistered
    lastUpdatedBlock
  }
}
""".strip()

    all_items: list[dict[str, Any]] = []
    max_block = from_block
    reached_page_limit = False

    for page_idx in range(max_pages):
        variables: dict[str, Any] = {
            "fromBlock": from_block,
            "first": first,
            "skip": page_idx * first,
        }
        payload = _execute_graphql(query, variables=variables)
        batch = (payload.get("data") or {}).get("agents") or []

        if not isinstance(batch, list):
            raise ValueError("Subgraph 返回格式非法：data.agents 不是数组")
        if not batch:
            break

        all_items.extend(batch)
        for item in batch:
            try:
                bn = int(item.get("lastUpdatedBlock", from_block))
                if bn > max_block:
                    max_block = bn
            except (TypeError, ValueError):
                continue

        if len(batch) < first:
            break
        if page_idx == max_pages - 1:
            reached_page_limit = True

    return all_items, max_block, reached_page_limit


def healthcheck_subgraph() -> bool:
    """
    对 Subgraph 服务做轻量健康检查。

    返回：
    - bool: 可用返回 True，否则 False。
    """
    query = "{ _meta { block { number } } }"
    try:
        payload = _execute_graphql(query)
        meta = (payload.get("data") or {}).get("_meta")
        if meta is None:
            return False
        block_number = (meta.get("block") or {}).get("number")
        return int(block_number) >= 0
    except Exception as exc:
        LOGGER.warning("Subgraph 健康检查失败: %s", exc)
        return False


#print(fetch_incremental_agents(10360859))