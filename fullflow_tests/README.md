# fullflow_tests 全流程闭环测试说明

## 1. 背景与目标
`fullflow_tests` 用于把项目现有能力串成一条可执行闭环，覆盖：
1. 账户准备与测试币分发（复用或新建）。
2. DID 隐式注册与 Delegate 授权。
3. 发现链路（metadata 注册、Subgraph 收录等待、Sidecar 命中断言）。
4. 认证链路（Auth / Probe / Context，标准 2v2，默认 3 轮）。
5. MCP 互操作链路（连接、工具发现、工具调用、资源接口兼容、权限控制）。
6. 治理链路（Sepolia 举报留痕 + 本地 Hardhat 完整治理）。
7. 性能与成本报表（CSV + JSON + Markdown + PNG 图表）。


## 2. 流程总览（文字版）
Provision -> Discovery -> Verification -> MCP Interop -> Governance -> Reporting

各阶段动作如下：
1. `Provision`: 检查 `config/agents_4_key.json` 是否可复用，不可复用则自动新建账户并打币、注册、授权。
2. `Discovery`: 为本次 Holder 生成 metadata，注册到 `AgentRegistry_v1`，如余额不足会尝试用 `master` 自动补币，再等待 Subgraph 收录并执行 Sidecar 命中断言。
3. `Verification`: 启动 Issuer + Holders，执行 2v2 多轮审计，采集 T1~T12 与 SLA 比例。
4. `MCP Interop`: 执行 MCP Server 连接、工具发现、工具调用、资源接口兼容、权限控制与延迟统计。
5. `Governance`: 基于失败证据触发治理；Sepolia 调 `reportMisbehavior`，本地跑 `report -> slash -> restore`。
6. `Reporting`: 输出固定命名报表文件，便于复现实验和论文整理。

## 3. 前置条件
1. Python 依赖已安装：`pip install -r requirements.txt`。
2. Node 依赖已安装：`npm install`。
3. `config/key.json` 可用，至少包含：
4. `api_url`、`subgraph_url`、`subgraph_api_key`。
5. `qwq_api_key` 与 `llm_config`（Probe/审计依赖模型调用）。
6. `accounts.master` 与 `accounts.issuer`（master 需要有 Sepolia ETH）。
7. 本机可访问 RPC、Subgraph、模型 API。

## 4. 快速开始
在项目根目录执行：

```bash
python fullflow_tests/run_fullflow.py
```

默认等价于：
1. `--profile paper`
2. `--rounds 3`
3. `--account-strategy mixed`
4. `--governance-mode both`
5. `--discovery-bind-current true`

## 5. 常用参数
```bash
python fullflow_tests/run_fullflow.py \
  --config fullflow_tests/config.default.json \
  --rounds 3 \
  --account-strategy mixed \
  --governance-mode both \
  --output-dir fullflow_tests/results
```

参数说明：
1. `--config`: 配置文件路径。
2. `--rounds`: 审计轮次。
3. `--account-strategy`: `mixed|reuse|fresh`。
4. `--governance-mode`: `sepolia|local|both|off`。
5. `--output-dir`: 结果目录根路径。
6. `--discovery-bind-current`: 是否强绑定本次账户。
7. `--random-seed`: 复测随机种子（论文建议固定）。

策略补充：
1. `mixed`：优先复用账户，仅检查角色完整性；余额不足由发现阶段按需自动补币。
2. `reuse`：严格复用，含余额阈值检查；若检测到 Admin 余额不足，会自动补款并复验。
3. `fresh`：总是新建账户并执行完整打币/注册/授权。

## 6. 运行期间会发生什么
1. 先准备账户，必要时执行链上资金与授权交易。
2. 再执行发现阶段注册与索引等待。
3. 启动 Issuer 和 Holder 进程，进行并发审计轮次。
4. 执行负例并沉淀证据，同时跑 MCP 互操作测试与并发矩阵。负例失败不中断流程，结果记录在 `case_assertions.csv` 中。
5. 调用治理动作并采集结果。
6. 最后统一写出报表，返回运行目录。
7. 控制台会持续输出 `[fullflow][时间][阶段]` 进度日志，包含轮询重试与轮次完成状态。
8. Discovery 会先做子图预检（`_meta`），自动选择可用节点后再进入轮询。

## 7. 结果文件说明
每次运行都会生成目录：

`fullflow_tests/results/<timestamp>/`

固定产物：
1. `phase_metrics.csv`：验证阶段和负例阶段指标（含 T1~T12、SLA、状态）。
2. `chain_tx_metrics.csv`：链上交易成本（含 phase/case_id/ETH/USD/CNY）。
3. `discovery_metrics.csv`：Subgraph 等待、同步统计、检索命中断言。
4. `governance_metrics.csv`：Sepolia 与本地治理执行结果。
5. `case_assertions.csv`：能力-用例矩阵断言（`capability_id/expected/actual/passed/error`）。
6. `latency_stats.csv`：阶段与用例延迟统计（mean/p50/p95/max）。
7. `l2_cost_estimates.csv`：Base/Optimism/Arbitrum 成本估算（ETH/USD/CNY）。
8. `run_manifest.json`：复测清单（profile、seed、配置摘要、git commit）。
9. `raw_metrics.json`：全量原始对象（便于二次分析）。
10. `summary.md`：论文可直接引用的汇总视图（含图表引用）。
11. `chart_latency_stage.png`：阶段延迟图（均值+P95，含样本数标注）。
12. `chart_tx_cost_eth.png`：交易类别成本图（ETH）。
13. `chart_l2_cost_cny.png`：L2 人民币成本对比图。
14. `chart_concurrency_stress.png`：Verification 正向并发矩阵图（通过率/P95/TPS）。
15. `chart_security_negative_matrix.png`：安全负例通过情况图。
16. `chart_mcp_abuse_matrix.png`：MCP 并发越权拦截矩阵图。
17. `chart_mcp_latency_distribution.png`：MCP 批量调用延迟分布图。
18. `chart_mcp_test_matrix.png`：MCP 能力项测试矩阵图。
19. `chart_mcp_tool_comparison.png`：MCP 单次工具调用延迟对比图。
20. `chart_mcp_latency_matrix.png`：MCP 并发延迟矩阵图。
21. `mcp_metrics.csv`：MCP 互操作与并发矩阵结果。
22. `fullflow_report.md`：适合直接汇报的完整文本版实验报告。

## 8. 常见失败与排查
1. RPC 连接失败：检查 `config/key.json` 的 `api_url`。
2. Subgraph 超时：确认 `subgraph_url`、`subgraph_api_key` 与索引延迟。
3. Subgraph 504：脚本会自动重试并继续轮询，若持续不可用会在总超时后失败。
4. LLM 调用超时：确认 `qwq_api_key` 和网络连通性。
5. Gas 不足：检查 `master` 余额，或降低 `register_stake_eth`。
6. 端口冲突：确认 `8000/5000/5001` 未被占用。
7. 自动补币失败：确认 `config/key.json` 中存在 `accounts.master` 且余额足够。

建议的网络排查顺序：
1. 在命令行直接请求子图 `_meta`，确认不是本地代理/防火墙拦截。
2. 检查 `subgraph_api_key` 是否仍有效。
3. 在 `discovery.subgraph_url_pool` 中加入备用节点地址，避免单点 504。

## 9. 安全注意
1. 不要将真实私钥提交到仓库。
2. 结果目录可能包含运行细节，不建议公开分享完整 `raw_metrics.json`。
3. `raw_metrics.json` 已对私钥与 API Key 脱敏，但仍建议按最小范围共享。
4. 建议只在测试网运行该脚本，避免主网误操作。

## 10. 代码注释与函数说明规范
本目录新增 Python 函数统一使用中文 docstring，固定包含：
1. `功能`
2. `参数`
3. `返回值`

这样新用户可以直接从代码定位“做什么、怎么传参、返回什么”。



## 11. 新增的 MCP / A2A 兼容能力现在测了什么
这次相对旧版测试说明，新增的重点是 MCP 互操作与权限控制，不再只是 Discovery + Verification + Governance 三段式。

新增覆盖包括：
1. MCP Server 连接是否成功（`stdio_connect`）。
2. MCP `tools/list` 能否发现工具（`tools_discovery`）。
3. MCP `tools/call` 能否稳定执行（`tool_call`）。
4. `resources/list` 不支持时是否能优雅降级（`resources_graceful`）。
5. Toolset VC 权限判断是否生效（`vc_auth_positive / vc_auth_negative_action / vc_auth_negative_no_vc`）。
6. MCP 串行批量延迟统计（`latency_batch`）。
7. MCP 并发延迟矩阵（S/M/L）。
8. Verification 阶段的 MCP 并发越权联动测试（S/M/L）。

## 12. 我们现在具体怎么做并发测试
为了对齐“模拟多智能体并发交互”的任务要求，目前全流程里实际做了三类并发：

1. Verification 正向并发矩阵。
- S 档：4 任务
- M 档：16 任务
- L 档：32 任务
- 目标是观察认证主链路在不同规模下的通过率、P95 和吞吐变化。

2. MCP 并发延迟矩阵。
- S 档：2 并发 / 20 调用
- M 档：10 并发 / 100 调用
- L 档：24 并发 / 300 调用
- 目标是观察互操作层在不同规模下的响应延迟与稳定性。

3. MCP 并发越权矩阵。
- S 档：20 个非法请求
- M 档：80 个非法请求
- L 档：200 个非法请求
- 目标是观察高并发下权限控制是否仍稳定生效。

## 13. 最新样本结果怎么引用
可直接引用目录：`fullflow_tests/results/20260423_162534`

核心结果如下：
1. 分阶段结果。
- Provision：1/1
- Discovery：7/7
- Verification：19/22（并发 M/L 档因 API 限速未达阈值，负例全部通过）
- MCP Interop：11/12（official-time stdio 连接失败）
- Governance：3/3

2. 主链路时延（3轮 2v2）。
- 正向用例全部通过（6/6）

3. Verification 并发矩阵。
- S：通过
- M：未达阈值（API 限速导致，pass_rate=0.5625）
- L：未达阈值（API 限速导致，pass_rate=0.5625）

4. MCP 并发越权拦截。
- S/M/L：全部 100% 拦截

5. 图表说明。
- `chart_scale_projection.png` 已移除（线性外推估算值不适合可视化）
- 所有图表标注改为相对偏移，避免数值重叠


