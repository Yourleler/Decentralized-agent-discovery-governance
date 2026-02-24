"""
Subgraph GraphQL 客户端适配层。

"""

from typing import Any

from graphqlclient import GraphQLClient
client=GraphQLClient('https://api.studio.thegraph.com/query/1740029/agent-registry-sepolia/version/latest')




def build_incremental_query(
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

    import json

    query = """
query IncrementalAgents($fromBlock: Int!, $first: Int!, $skip: Int!) {
  agents(
    where: { blockNumber_gte: $fromBlock }
    first: $first
    skip: $skip
    orderBy: blockNumber
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
        raw_response = client.execute(query, variables=variables)
        # graphqlclient 可能返回字符串或对象，这里统一转成 dict。
        payload = json.loads(raw_response) if isinstance(raw_response, str) else raw_response

        # 先做协议层校验，再进入业务字段解析。
        if not isinstance(payload, dict):
            raise ValueError("Subgraph 返回格式非法：顶层不是 JSON 对象")
        if payload.get("errors"):
            raise RuntimeError(f"Subgraph 查询失败: {payload['errors']}")

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
                bn = int(item.get("blockNumber", from_block))
                if bn > max_block:
                    max_block = bn
            except (TypeError, ValueError):
                continue

        if len(batch) < first:
            break
        if page_idx == max_pages - 1:
            # 命中最大分页上限且当前页仍为满页，说明“可能还有下一页未拉取”。
            reached_page_limit = True

    return all_items, max_block, reached_page_limit




def healthcheck_subgraph(client: GraphQLClient) -> bool:
    """
    对 Subgraph 服务进行轻量健康检查。

    参数:
    - client: GraphQL 客户端。

    返回:
    - bool: 服务可用返回 True，否则返回 False。
    """
    pass
