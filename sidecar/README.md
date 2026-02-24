# Sidecar 设计骨架

本目录用于实现“子图增量同步 + IPFS 元数据拉取 + 本地评分缓存 + Chroma 语义检索”的完整链路。

当前阶段只创建文件框架，不放业务实现代码。

## 目标链路

1. 从子图拉取增量事件（did/cid/score/penalty/lastTime/isSlashed）。
2. 按 CID 拉取并校验 IPFS metadata。
3. 融合计算本地评分（s_global/s_local/w_confidence/s_final）。
4. 写入本地缓存（SQLite）与向量库（ChromaDB）。
5. 对外提供发现查询与健康检查接口（可选 FastAPI）。

## 模块分层

- `adapters/`: 外部系统适配层（Subgraph/IPFS）
- `domain/`: 领域模型与算法（评分、过滤、排序、向量文本）
- `storage/`: 持久化层（SQLite/Chroma/快照）
- `services/`: 编排层（同步、向量化、查询）
- `api/`: HTTP 接口层（后期开启）
- `jobs/`: 调度与周期任务
- `tests/`: 契约测试/回归测试骨架

