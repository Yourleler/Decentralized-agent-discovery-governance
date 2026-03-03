# api 说明

## 功能

该目录提供 Sidecar 的最小 HTTP 接口。

当前包含：

- `search_api.py`：FastAPI 应用，提供检索与本地评分调整接口。

## 接口

- `GET /search`
  - 参数：`query`、`top_k`
  - 返回：候选 Agent 列表（已做注册/罚没/可用性过滤）
- `POST /local-score/adjust`
  - Body：`agent_address`、`alpha_delta`、`beta_delta`
  - 返回：更新后的评分字段
- `GET /health`
  - 返回：服务状态

## 启动

```bash
uvicorn sidecar.api.search_api:app --host 0.0.0.0 --port 8000
```
