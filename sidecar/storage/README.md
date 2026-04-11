# storage 说明

## 功能

该目录负责 Sidecar 本地持久化（SQLite）。

当前包含：

- `sqlite_state.py`
  - `agent_state` 表：Agent 状态、评分、向量文本、运行时探测状态
  - `sync_state` 表：同步水位线等键值状态
  - `interaction_receipt` 表：本地交互请求/响应记录、上下文快照与申诉导出原始依据
  - 自动迁移：老库补列（增量迁移，不破坏历史数据）

## 关键能力

- 幂等写入：按 `last_event_block` 防止旧事件覆盖新状态。
- 水位线读写：`get_watermark()/set_watermark()`。
- 运行时探测状态更新：`update_runtime_probe()`。
- 本地交互记录：`append_interaction_receipt()`。
- 最近 7 天调用统计：写入交互记录时自动回写 `agent_state.recent_7d_calls`。
- 申诉导出接口：`build_appeal_payload()`。

## 用法

由 `wiring` 与 `services` 调用，通常不直接在 API 层使用。
