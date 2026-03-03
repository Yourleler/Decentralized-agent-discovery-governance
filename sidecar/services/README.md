# services 说明

## 功能

该目录承载 Sidecar 核心业务逻辑。

当前包含：

- `sync_orchestrator.py`
  - 增量同步编排
  - 评分计算与落库
  - CID 变更触发向量索引同步
  - 本地评分调整接口
- `discovery_service.py`
  - 语义检索
  - 评分微调排序
  - 运行时可用性探测与冷却过滤

## 用法

通过 `wiring.build_sidecar_container()` 获取：

- `container.sync_orchestrator`
- `container.discovery_service`

不建议手动 new，避免依赖参数不一致。
