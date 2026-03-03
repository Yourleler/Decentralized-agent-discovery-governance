# data 说明

## 功能

该目录存放 Sidecar 运行时数据（本地状态与向量索引）。

典型内容：

- `sidecar_state.db`：SQLite 主库
- `*.db-wal / *.db-shm`：SQLite 日志文件
- `chroma/`：Chroma 持久化目录

## 使用说明

- 目录内容可重建（通过重新同步恢复）。
- 生产环境建议做定期备份。
