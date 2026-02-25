"""
Subgraph GraphQL 客户端适配层。

"""

import logging
import os
import sys
import json
from pathlib import Path
from typing import Any
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = current_dir
while not os.path.exists(os.path.join(project_root, "infrastructure")):
    parent = os.path.dirname(project_root)
    if parent == project_root: break 
    project_root = parent
if project_root not in sys.path:
    sys.path.insert(0, project_root)


try:
    from infrastructure.load_config import load_key_config
except ImportError:
    print("❌ 错误: 无法导入 infrastructure。")
    sys.exit(1)

import requests

LOGGER = logging.getLogger(__name__)

FALLBACK_SUBGRAPH_URL = "https://api.studio.thegraph.com/query/1740029/agent-registry-sepolia/version/latest"



KEY_CONFIG = load_key_config()
print(KEY_CONFIG)
DEFAULT_SUBGRAPH_URL = str(KEY_CONFIG.get("subgraph_url") or FALLBACK_SUBGRAPH_URL)
SUBGRAPH_URL = DEFAULT_SUBGRAPH_URL
SUBGRAPH_API_KEY = KEY_CONFIG.get("subgraph_api_key")
SUBGRAPH_AUTH_HEADER = os.getenv("SUBGRAPH_AUTH_HEADER", "Authorization")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("SUBGRAPH_TIMEOUT_SECONDS", "20"))


def _build_headers() -> dict[str, str]:
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
    print(headers)
    return headers


def _execute_graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
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
    构建增量查询语句。

    参数:
    - from_block: 增量同步起始区块高度（通常来自 last_synced_block）。
    - first: 单页拉取条数上限。
    - max_pages: 最多拉取页数，防止异常情况下无限循环，默认 50。

    返回:
    - tuple[list[dict[str, Any]], int, bool]。
      第一个值是本轮拼接后的全量增量数据；
      第二个值是本轮观察到的最大区块高度；
      第三个值表示是否触发了 max_pages 上限（True 表示可能还有下一页）。
    """
    # 基础参数校验，避免无意义或危险的分页请求。
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
    # 最大区块号用于推进下次增量同步水位线。
    max_block = from_block
    # True 表示本轮可能被 max_pages 截断，结果可能不完整。
    reached_page_limit = False

    # 对外表现为“一次返回全量增量”，内部通过分页循环拉取。
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
        # 空页表示没有更多增量数据。
        if not batch:
            break

        all_items.extend(batch)
        # 持续更新本轮最大区块号，供外部推进同步游标。
        for item in batch:
            try:
                bn = int(item.get("lastUpdatedBlock", from_block))
                if bn > max_block:
                    max_block = bn
            except (TypeError, ValueError):
                continue

        if len(batch) < first:  # 此页不满,即查询到最新了
            break
        if page_idx == max_pages - 1:
            # 命中最大分页上限且当前页仍为满页，说明“可能还有下一页未拉取”。
            reached_page_limit = True

    return all_items, max_block, reached_page_limit


def healthcheck_subgraph() -> bool:
    """
    对 Subgraph 服务进行轻量健康检查。

    使用 The Graph 标准的 _meta 内省查询，仅请求当前同步区块号。
    这是最轻量的探针：能成功返回说明 Subgraph 在线且索引正常。

    返回:
    - bool: 服务可用返回 True，否则返回 False。
    """
    query = "{ _meta { block { number } } }"

    try:
        payload = _execute_graphql(query)

        # 正常情况下 _meta.block.number 应为非负整数
        meta = (payload.get("data") or {}).get("_meta")
        if meta is None:
            return False

        block_number = (meta.get("block") or {}).get("number")
        try:
            print(f"block_number: {block_number}")
            return int(block_number) >= 0
        except (TypeError, ValueError):
            return False

    except Exception as exc:
        LOGGER.warning("Subgraph 健康检查失败: %s", exc)
        return False

print(healthcheck_subgraph())
