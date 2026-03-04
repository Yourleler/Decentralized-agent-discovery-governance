# `fullflow_tests` 全流程闭环测试方案（标准2v2，3轮，双治理模式）

## 摘要
本方案在根目录新增 `fullflow_tests/`，实现“一键从头到尾”的测试闭环：账户准备与打币、DID注册与Delegate授权、发现（Sidecar+Subgraph）、认证审计（Auth/Probe/Context）、治理（Sepolia留痕 + 本地完整治理）、性能与成本报表。  
已按你确认的默认参数固化：`标准2v2`、`3轮`、`混合账户策略`、`CSV+JSON+摘要`、`双治理模式`、`发现强绑定本次账户`。

## 公共接口与新增文件
1. 新增目录 `fullflow_tests/`。
2. 新增主入口 `fullflow_tests/run_fullflow.py`。  
3. 新增配置文件 `fullflow_tests/config.default.json`。  
4. 新增模块文件：`provision.py`、`discovery.py`、`verification.py`、`governance.py`、`reporting.py`、`orchestrator.py`。  
5. 新增本地治理脚本 `fullflow_tests/contracts/local_governance.js`（Hardhat 本地链专用）。  
6. 新增说明文档 `fullflow_tests/README.md`。  
7. 新增结果目录 `fullflow_tests/results/<timestamp>/`（运行时生成）。  

主入口 CLI 约定：
1. `--profile standard`（默认）。  
2. `--rounds 3`（默认）。  
3. `--account-strategy mixed`（默认）。  
4. `--governance-mode both`（默认，`sepolia|local|both`）。  
5. `--discovery-bind-current true`（默认）。  
6. `--output-dir fullflow_tests/results`。  

## 实施步骤（决策已固化）
1. 账户准备（混合策略）。
2. 先校验 `config/agents_4_key.json` 是否可复用。  
3. 校验项：角色完整性（a/b/c/d admin+op+issuer）、余额阈值、基础链上可用性。  
4. 复用失败时自动回退“新建账户+master打币+DID隐式注册+Delegate授权”，复用 `_demo_2v2/setup_4_agents.py` 的核心流程逻辑。  
5. 记录所有链上交易的 `tx_hash/gasUsed/effectiveGasPrice/latency/cost_eth/cost_usd`。  

6. 启动服务编排。  
7. 拉起 Issuer 与 2 个 Holder 进程，健康检查通过后进入测试。  
8. 全流程结束后统一回收子进程，支持异常中断清理。  

9. 发现闭环（强绑定本次账户）。  
10. 使用 Sepolia 上 `AgentRegistry_v1` 地址 `0x28249C2F09eF3196c1B42a0110dDD02D3B2b59B7`。  
11. 为本次 Holder Admin 注册 `registerAgent(did,cid)`；若已注册则 `updateServiceMetadata`。  
12. 元数据采用“本地缓存 CID”策略：写入 `.ipfs_cache/<cid>`，避免依赖 `pinata_jwt` 缺失。  
13. 轮询 Subgraph，直到本次账户可见或超时（默认 10 分钟，15 秒间隔）。  
14. 执行 Sidecar 同步与检索断言，要求检索结果命中本次注册账户。  

15. 审计闭环（标准2v2，3轮）。  
16. 复用学姐验证核心：`agents/verifier/runtime.py` 的 `execute_auth / execute_probe / execute_context_check`。  
17. 每轮并发两对（C→A，D→B），共 3 轮。  
18. 每轮记录 `T1~T12`、`SLA_Load_Ratio`、三阶段延迟与总时长。  
19. 计算吞吐：`TPS = 成功审计数 / 批次完成时间(最慢总时长)`。  

20. 负例与治理闭环。  
21. 负例A：伪造签名调用 `/auth`，期望 401。  
22. 负例B：中途重置 Holder memory 触发 Context mismatch。  
23. 任一失败会生成证据 JSON（请求、响应、哈希、时间戳、DID、阶段）。  
24. Sepolia治理模式：调用 `reportMisbehavior(target,evidenceCid)`，校验事件 `MisbehaviorReported`。  
25. 本地治理模式：Hardhat 本地链部署合约后执行 `registerAgent -> reportMisbehavior -> slash -> restore`，校验状态转移。  

26. 报表输出（CSV+JSON+摘要）。  
27. `phase_metrics.csv`：轮次/对组/阶段/状态/延迟/SLA。  
28. `chain_tx_metrics.csv`：注册、授权、发现注册、治理上链等交易成本。  
29. `discovery_metrics.csv`：Subgraph等待时间、同步耗时、检索耗时、命中情况。  
30. `governance_metrics.csv`：Sepolia与本地治理动作结果。  
31. `raw_metrics.json`：全量原始数据。  
32. `summary.md`：可直接用于论文的摘要表（成本、延迟、并发吞吐、成功率）。  

33. 工程同步项。  
34. 更新 `.gitignore`：忽略 `fullflow_tests/results/`、临时日志与本地治理产物。  
35. `requirements.txt` 与 `package.json` 默认不新增依赖（仅复用现有）。  

## 测试用例与场景
1. `TC-01` 账户复用路径成功。  
2. `TC-02` 账户回退新建路径成功（含 master 打币）。  
3. `TC-03` DID 注册与 Delegate 授权交易成功并有成本数据。  
4. `TC-04` 发现链路可见本次注册账户并可检索命中。  
5. `TC-05` 2v2 审计成功（3轮）并产出 T1~T12/SLA/TPS。  
6. `TC-06` Auth 伪造签名被拒绝（401）。  
7. `TC-07` Context mismatch 可被检测。  
8. `TC-08` 失败触发证据生成与 Sepolia 举报上链。  
9. `TC-09` 本地治理 `slash/restore` 状态变化正确。  
10. `TC-10` 报表文件完整且字段齐全。  

## 验收标准
1. 一条命令可跑完整流程并自动清理进程。  
2. 默认配置下输出完整 CSV/JSON/Markdown 报表。  
3. 发现阶段命中“本次账户”，不是仅命中历史链上数据。  
4. 治理双模式都可执行且有可验证结果。  
5. 出错时有明确失败阶段、原因和证据文件定位。  

## 明确假设与默认值
1. 使用 `config/key.json` 中的 `master/issuer/api_url/subgraph_url/subgraph_api_key/qwq_api_key`。  
2. `pinata_jwt` 缺失时，自动走本地 IPFS 缓存 CID 方案（默认）。  
3. 默认 profile 为 `standard`，默认轮次 `3`。  
4. 默认治理模式 `both`，默认账户策略 `mixed`。  
5. 发现阶段默认“强绑定本次账户”，允许等待 Subgraph 索引。  
