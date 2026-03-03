# storage 说明

## 功能

该目录负责 Sidecar 本地持久化（SQLite）。

当前包含：

- `sqlite_state.py`
  - `agent_state` 表：Agent 状态、评分、向量文本、运行时探测状态
  - `sync_state` 表：同步水位线等键值状态
  - 自动迁移：老库补列（增量迁移，不破坏历史数据）

## 关键能力

- 幂等写入：按 `last_event_block` 防止旧事件覆盖新状态。
- 水位线读写：`get_watermark()/set_watermark()`。
- 运行时探测状态更新：`update_runtime_probe()`。

## 用法

由 `wiring` 与 `services` 调用，通常不直接在 API 层使用。
