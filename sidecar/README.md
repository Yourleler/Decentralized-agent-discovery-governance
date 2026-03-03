# Sidecar 说明

## 1. 功能概览

`sidecar` 是本项目的本地发现与同步组件，核心能力：

1. 从 Subgraph 增量同步 Agent 状态（带水位线断点续传）。
2. 计算并存储评分字段（`S_global/S_local/w/S_final`）。
3. 同步 CID 对应 metadata，写入 Chroma 向量索引。
4. 提供最小检索接口（CLI + HTTP），按“语义优先 + 评分微调”排序。
5. 对前排候选做运行时可用性探测，过滤连续失败节点。

## 2. 目录结构

- `main.py`：Sidecar CLI 入口（同步/检索/调分）。
- `wiring.py`：依赖装配、配置加载、容器构建。
- `adapters/`：外部数据源适配（当前为 Subgraph）。
- `services/`：同步编排与检索业务逻辑。
- `storage/`：SQLite 状态存储与迁移。
- `vector/`：Chroma 封装与 embedding 工厂。
- `api/`：FastAPI 最小检索接口。
- `tests/`：同步与检索契约测试。
- `data/`：运行时数据库与向量索引目录。
- `logs/`：运行日志输出目录。

## 3. 快速用法

在项目根目录执行：

```bash
# 单轮同步
python -m sidecar.main --once

# 语义检索
python -m sidecar.main --query "我要找做金融分析的 agent" --top-k 5

# 手动调整本地评分证据
python -m sidecar.main --adjust-local-score --agent-address 0xabc --alpha-delta 1 --beta-delta 0
```

启动 HTTP 接口：

```bash
uvicorn sidecar.api.search_api:app --host 0.0.0.0 --port 8000
```

常用接口：

- `GET /search?query=...&top_k=5`
- `POST /local-score/adjust`
- `GET /health`

## 4. 测试

```bash
python -m unittest sidecar.tests.test_sync_contract sidecar.tests.test_discovery_contract -v
```

## 5. 配置要点

- 默认 embedding 模型：`BAAI/bge-m3`（首次使用会自动下载到本地缓存）。
- 主要环境变量：
  - `SIDECAR_DB_PATH`
  - `SIDECAR_CHROMA_PATH`
  - `SIDECAR_CHROMA_COLLECTION`
  - `SIDECAR_EMBED_MODEL`
