# Sidecar 最简同步说明

当前实现收敛为一条最小链路：

1. 从 Subgraph 拉取增量 agent 记录。
2. 在同步阶段计算并存储最终混合评分（`global_score/local_score/confidence_score/final_score`）。
3. 若 `metadata_cid` 变更，则下载 IPFS metadata 并提取向量化文本。
4. 将状态、评分、metadata 摘要统一写入 SQLite。
5. 全量评分重算由独立入口执行（适合定时任务），不和每轮增量同步强耦合。

## 核心文件

- `main.py`: 启动入口与命令行参数。
- `wiring.py`: 配置加载与依赖装配。
- `adapters/subgraph_client.py`: 子图增量拉取。
- `services/sync_orchestrator.py`: 同步编排、评分计算、CID 变更处理。
- `storage/sqlite_state.py`: SQLite 持久化（含评分与向量文本字段）。
