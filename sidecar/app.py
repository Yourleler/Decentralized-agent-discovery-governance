"""
Sidecar 应用装配层。

后续职责：
1. 统一创建应用级对象（服务、仓储、客户端）。
2. 管理生命周期（startup/shutdown）。
3. 对外提供 get_app()，供 main.py 或测试复用。
"""

