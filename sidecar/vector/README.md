# vector 说明

## 功能

该目录负责向量检索基础设施封装。

当前包含：

- `chroma_index.py`
  - Chroma 最小封装：`upsert/delete/query`
  - 统一输出命中结构 `SearchHit`
- `embedding_factory.py`
  - 统一创建 embedding 函数（默认 `BAAI/bge-m3`）

## 用法

通过 `wiring` 自动注入到：

- `SyncOrchestrator`（同步写入向量）
- `DiscoveryService`（语义召回）

一般不建议业务代码直接依赖 Chroma 原生 API。
