"""
Sidecar 进程主入口。

后续职责：
1. 加载配置（config/*.json + env）。
2. 初始化依赖（SQLite、Chroma、SubgraphClient、IPFSClient）。
3. 启动同步任务与可选 API 服务。
4. 处理优雅退出（flush 缓存、关闭连接）。
"""

