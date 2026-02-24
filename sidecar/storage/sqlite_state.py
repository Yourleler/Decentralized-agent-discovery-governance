"""
SQLite 状态存储。

后续职责：
1. 管理 agent_state / sync_state 表。
2. 提供 upsert_agent_state、get_agent_state、get_watermark 等方法。
3. 保证事务一致性与幂等更新。
"""

