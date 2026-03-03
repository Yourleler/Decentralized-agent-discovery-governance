# tests 说明

## 功能

该目录存放 Sidecar 契约测试与行为回归测试。

当前包含：

- `test_sync_contract.py`
  - 同步/迁移/幂等/向量同步/本地调分测试
- `test_discovery_contract.py`
  - 检索排序、可用性过滤、运行时探测测试
- `test_scoring_contract.py`
  - 评分契约骨架（待扩展）
- `test_vector_contract.py`
  - 向量配置基础契约（pytest 风格）

## 运行

```bash
# 当前主回归
python -m unittest sidecar.tests.test_sync_contract sidecar.tests.test_discovery_contract -v
```

说明：

- `test_vector_contract.py` 使用 pytest 风格，需安装 `pytest` 才能单独运行。
