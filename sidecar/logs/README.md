# logs 说明

## 功能

该目录存放 Sidecar 运行日志。

典型内容：

- `sidecar.log`：由 `python -m sidecar.main` 运行时写入。

## 使用说明

- 日志用于排查同步、检索、网络请求问题。
- 可配合系统日志轮转策略定期清理。
