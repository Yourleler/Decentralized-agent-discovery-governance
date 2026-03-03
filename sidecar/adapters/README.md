# adapters 说明

## 功能

该目录负责“外部数据源 -> 项目内部结构”的适配层。

当前包含：

- `subgraph_client.py`：对 The Graph 提供增量拉取与健康检查。

## 主要接口

- `fetch_incremental_agents(from_block, first, max_pages)`
  - 作用：按区块水位线拉取增量 Agent 数据。
  - 返回：`(items, max_block, reached_page_limit)`。
- `healthcheck_subgraph()`
  - 作用：检查 Subgraph 基本可用性。

## 用法

通常由 `services/sync_orchestrator.py` 调用，不建议业务层直接操作 HTTP。
