# fullflow_tests 全流程闭环测试说明

## 1. 背景与目标
`fullflow_tests` 用于把项目现有能力串成一条可执行闭环，覆盖：
1. 账户准备与测试币分发（复用或新建）。
2. DID 隐式注册与 Delegate 授权。
3. 发现链路（metadata 注册、Subgraph 收录等待、Sidecar 命中断言）。
4. 认证链路（Auth / Probe / Context，标准 2v2，默认 3 轮）。
5. 治理链路（Sepolia 举报留痕 + 本地 Hardhat 完整治理）。
6. 性能与成本报表（CSV + JSON + Markdown 摘要）。

## 2. 流程总览（文字版）
Provision -> Discovery -> Verification -> Governance -> Reporting

各阶段动作如下：
1. `Provision`: 检查 `config/agents_4_key.json` 是否可复用，不可复用则自动新建账户并打币、注册、授权。
2. `Discovery`: 为本次 Holder 生成 metadata，注册到 `AgentRegistry_v1`，如余额不足会尝试用 `master` 自动补币，再等待 Subgraph 收录并执行 Sidecar 命中断言。
3. `Verification`: 启动 Issuer + Holders，执行 2v2 多轮审计，采集 T1~T12 与 SLA 比例。
4. `Governance`: 基于失败证据触发治理；Sepolia 调 `reportMisbehavior`，本地跑 `report -> slash -> restore`。
5. `Reporting`: 输出固定命名报表文件，便于复现实验和论文整理。

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
1. `--profile standard`
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

策略补充：
1. `mixed`：优先复用账户，仅检查角色完整性；余额不足由发现阶段按需自动补币。
2. `reuse`：严格复用，含余额阈值检查，失败即报错。
3. `fresh`：总是新建账户并执行完整打币/注册/授权。

## 6. 运行期间会发生什么
1. 先准备账户，必要时执行链上资金与授权交易。
2. 再执行发现阶段注册与索引等待。
3. 启动 Issuer 和 Holder 进程，进行并发审计轮次。
4. 执行负例并沉淀证据。
5. 调用治理动作并采集结果。
6. 最后统一写出报表，返回运行目录。
7. 控制台会持续输出 `[fullflow][时间][阶段]` 进度日志，包含轮询重试与轮次完成状态。
8. Discovery 会先做子图预检（`_meta`），自动选择可用节点后再进入轮询。

## 7. 结果文件说明
每次运行都会生成目录：

`fullflow_tests/results/<timestamp>/`

固定产物：
1. `phase_metrics.csv`：验证阶段和负例阶段指标（含 T1~T12、SLA、状态）。
2. `chain_tx_metrics.csv`：链上交易成本（gas、耗时、ETH 成本）。
3. `discovery_metrics.csv`：Subgraph 等待、同步统计、检索命中断言。
4. `governance_metrics.csv`：Sepolia 与本地治理执行结果。
5. `raw_metrics.json`：全量原始对象（便于二次分析）。
6. `summary.md`：论文可直接引用的汇总视图。

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
