"""
ChromaDB 向量存储封装。

后续职责：
1. 初始化 collection（persist_directory/collection_name）。
2. 提供 upsert/query/delete 接口。
3. 统一返回语义分数格式，供 ranking.py 使用。
"""

