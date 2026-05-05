# 系统测试方法与流程说明

## 1. 测试架构总览

本系统的测试分为三个层次，从底层单元测试到端到端全流程闭环测试，形成完整的质量验证体系：

```
┌─────────────────────────────────────────────────────────────┐
│                   全流程闭环测试 (fullflow)                    │
│  Provision → Discovery → Verification → MCP → Governance    │
│─────────────────────────────────────────────────────────────│
│       MCP 互操作专项测试 (mcp_interop)                        │
│  连接性 → 工具发现 → 工具调用 → 权限控制 → 延迟统计             │
│─────────────────────────────────────────────────────────────│
│       互操作单元测试 (interop/tests)                          │
│  请求策略 → Agent Card → MCP 适配器 → A2A 网关               │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 测试阶段详解

### 2.1 全流程闭环测试（fullflow）

全流程测试在真实区块链网络（Sepolia 测试网）+ 真实 LLM 推理 + 真实 MCP Server 环境下，验证系统从账户准备到治理惩罚的完整生命周期。

#### 执行流程

```
PROVISION → DISCOVERY → VERIFICATION → MCP_INTEROP → GOVERNANCE → REPORTING
```

| 阶段 | 描述 | 依赖 | 核心验证点 |
|------|------|------|-----------|
| **PROVISION** | 账户准备与资金分配 | Sepolia 测试网 | 多账户自动创建、ETH 自动分配 |
| **DISCOVERY** | 链上注册+Subgraph索引+Sidecar向量检索 | 智能合约+TheGraph+ChromaDB | 可信发现闭环、CID 寻址一致 |
| **VERIFICATION** | 2v2 认证审计（Auth→Probe→Context） | Holder+Verifier+Issuer进程 | 正例通过+6类负例拦截 |
| **MCP_INTEROP** | MCP 互操作能力全面验证 | stdio MCP Server | 连接/发现/调用/权限/延迟 |
| **GOVERNANCE** | 链上治理状态机（举报→冻结→罚没→恢复） | Hardhat/Sepolia | 完整治理生命周期 |
| **REPORTING** | 自动化报表+图表生成 | matplotlib | CSV+图表+summary.md |

#### 执行方式

```bash
# 标准执行（论文复测档位）
python fullflow_tests/run_fullflow.py --config fullflow_tests/config.default.json --profile paper

# 自定义参数
python fullflow_tests/run_fullflow.py \
  --config fullflow_tests/config.default.json \
  --rounds 3 \
  --governance-mode both \
  --random-seed 20260307
```

#### 配置说明 (`config.default.json`)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `profile` | `paper` | 论文档位，自动补齐严格参数 |
| `rounds` | `3` | 正向审计轮次数 |
| `account_strategy` | `mixed` | 账户复用策略 |
| `governance_mode` | `both` | Hardhat 本地 + Sepolia 双模式 |
| `random_seed` | `20260307` | 固定种子确保可复现 |
| `mcp_interop.enabled` | `true` | 是否启用 MCP 互操作测试 |
| `mcp_interop.batch_call_count` | `20` | 延迟批量统计调用次数 |
| `mcp_interop.test_servers` | `["official-time", "official-fetch"]` | 参与测试的 MCP Server |

---

### 2.2 MCP 互操作专项测试

MCP 互操作测试可**独立于全流程运行**，无需 Sepolia 链上交易，无需启动 Holder/Verifier/Issuer 进程，在数秒内完成。

#### 测试用例矩阵

| # | 用例 ID | 测试维度 | 测试内容 | 验证目标 |
|---|---------|----------|----------|----------|
| 1 | `mcp_stdio_connect_*` | 连接性 | 启动 stdio Server + initialize 握手 | MCP 协议兼容性 |
| 2 | `mcp_tools_discovery_*` | 工具发现 | `tools/list` 返回工具名称与 schema | 工具注册正确性 |
| 3 | `mcp_tool_call_*_get_current_time` | 工具调用 | 调用 `get_current_time(timezone=Asia/Shanghai)` | 本地工具功能正确 |
| 4 | `mcp_tool_call_*_fetch` | 工具调用 | 调用 `fetch(url=https://example.com)` | 网络工具功能正确 |
| 5 | `mcp_resources_graceful_*` | 兼容性 | 对不支持 `resources/list` 的 Server 调用 | 优雅降级，不崩溃 |
| 6 | `mcp_vc_auth_positive` | 权限正例 | 合法 Toolset VC + 合法 action | 允许通过 |
| 7 | `mcp_vc_auth_negative_action` | 权限负例 | 合法 VC + 非法 action (`execute`) | 拒绝，"动作未授权" |
| 8 | `mcp_vc_auth_negative_no_vc` | 权限负例 | 无 VC 直接请求 | 拒绝，"未找到已授权工具" |
| 9 | `mcp_latency_batch_*` | 延迟统计 | 连续 20 次调用，统计 avg/P50/P95/max | 延迟分布量化 |

#### 执行方式

```bash
# 独立运行（推荐调试用）
python fullflow_tests/run_mcp_tests.py
```

#### 接入的 MCP Server

| Server ID | 包名 | 传输层 | 暴露工具 | 用途 |
|-----------|------|--------|----------|------|
| `official-time` | `mcp-server-time` | stdio | `get_current_time`, `convert_time` | 本地计算类工具代表 |
| `official-fetch` | `mcp-server-fetch` | stdio | `fetch` | 网络 I/O 类工具代表 |
| `demo-search-http` | 自建 | HTTP | 可配置 | HTTP 传输类工具代表 |

> 所有 stdio MCP Server 均通过 `uvx`（uv 工具链）自动安装和启动，无需手动预装。

---

### 2.3 互操作单元测试

底层单元测试覆盖互操作模块的核心逻辑，使用 Python `unittest` 框架：

| 测试类 | 覆盖模块 | 测试内容 |
|--------|----------|----------|
| `RequestPolicyTests` | `request_policy.py` | 请求信封校验、工具授权判定、哈希稳定性 |
| `ProfileAdapterTests` | `profile_adapter.py` | Agent Card 构建、互操作配置生成 |
| `MCPClientAdapterTests` | `mcp_client_adapter.py` | stdio 配置解析、工具列表/调用、旧版兼容 |
| `A2AGatewayTests` | `a2a_gateway.py` | Agent Card 路由、MCP 工具调用、未授权拒绝 |

#### 执行方式

```bash
python -m pytest interop/tests/test_interop_contract.py -v
```

### 2.4 多规模 + MCP 并发越权联动压测

该压测在每个规模档位（默认 `1/3/5/10`）下执行两类测试并汇总：

1. `Verifier -> Holder` 的 P2P 认证闭环性能（T4/T8/T12/Total/TPS）。
2. `A2A + MCP` 并发越权请求拦截能力（非法 action、非法 resource，期望 HTTP 403）。

执行方式：

```bash
python _experiments/run_scale_tests.py
```

产出文件（`_experiments/result/`）：

1. `scale_comparison.csv`：多规模性能 + MCP 越权拦截汇总。
2. `mcp_abuse_<scale>v<scale>.csv`：每个规模的逐请求明细。
3. `chart_scale_mcp_abuse_passrate.png`：越权拦截通过率随规模变化。
4. `chart_scale_mcp_abuse_latency.png`：越权请求平均延迟随规模变化。

---

## 3. 验证阶段（VERIFICATION）负例测试设计

负例测试是验证安全机制有效性的核心手段。每一类负例对应一种特定的攻击向量：

| 负例 ID | 攻击向量 | 注入方式 | 预期拦截点 | 预期结果 |
|---------|----------|----------|------------|----------|
| `fake_signature_auth` | 伪造 DID 签名 | 替换签名为随机字节 | Auth 阶段 | HTTP 401 |
| `tampered_vp_challenge` | VP Challenge 篡改 | 修改 VP 中的 challenge 值 | Context 阶段 | 不匹配拒绝 |
| `tampered_vp_signature` | VP 签名篡改 | 微调 VP JWS 签名字段 | Auth 阶段 | 验签失败 |
| `context_mismatch` | 上下文篡改/失忆 | 清空 Holder 一侧的记忆 | Context 阶段 | Hash Mismatch |
| `unregistered_agent` | 未注册 Agent | 生成随机 DID 并查询注册表 `getAgentByDID` | Discovery/注册表阶段 | `isRegistered=False` |
| `expired_vc` | 过期凭证 | 将 VP 中 VC 的 `validUntil` 篡改为过去时间 | Auth/验签阶段 | `verify_vp=False` |
| `mcp_abuse_concurrency` | 并发 MCP 越权调用 | 并发发送非法 action / 非法 resource 的 A2A+MCP 请求 | A2A 网关权限层 | 全部 HTTP 403 |

---

## 4. MCP 权限控制测试设计

MCP 工具调用通过 `AgentToolsetCredential`（Toolset VC）实现细粒度权限控制。测试设计覆盖三种场景：

### 4.1 正例：合法 VC + 合法动作

```
Toolset VC 声明: allowedActions=["query"], allowedResources=["resource:time:*"]
请求: tool=get_current_time, action=query, resource=resource:time:current
结果: allowed=True
```

### 4.2 负例：合法 VC + 非法动作

```
Toolset VC 声明: allowedActions=["query"]
请求: tool=get_current_time, action=execute  ← 非法
结果: allowed=False, reason="工具动作未授权"
```

### 4.3 负例：无 VC

```
请求: tool=get_current_time, action=query, vcs=[]  ← 无凭证
结果: allowed=False, reason="未找到已授权工具"
```

> 权限判定由 `request_policy.py` 中的 `evaluate_tool_authorization()` 函数执行，遍历所有持有的 VC 中的 `toolManifest`，逐项核对 `identifier`、`allowedActions`、`allowedResources`。

---

## 5. 延迟测试方法

### 5.1 测量口径

所有延迟采用**墙钟时间（wall clock time）**测量，使用 `time.monotonic()` 消除系统时间跳变影响。

| 指标类型 | 测量区间 | 单位 |
|----------|----------|------|
| Auth 延迟 (T4) | 发出认证请求 → 收到 VP 响应 | 秒 |
| Probe 延迟 (T8) | 发出探测任务 → 收到执行结果 | 秒 |
| Context 延迟 (T12) | 发出上下文校验 → 收到哈希对比结果 | 秒 |
| MCP 工具调用延迟 | `call_tool()` 发出 → 收到 JSON-RPC 响应 | 秒 |
| 全流程延迟 (Total) | Auth 开始 → Context 完成 | 秒 |

### 5.2 批量统计方法

对 MCP 工具调用，采用连续 N 次（默认 20 次）调用同一工具，收集所有延迟样本后计算：

- **平均值 (Avg)**：所有样本的算术平均
- **P50 (中位数)**：排序后第 50% 位置的值
- **P95**：排序后第 95% 位置的值，代表尾部延迟
- **最大值 (Max)**：最慢的单次调用
- **最小值 (Min)**：最快的单次调用

---

## 6. 测试产出物

每次测试运行生成独立的时间戳目录，全部产出物如下：

### 6.1 全流程测试产出 (`fullflow_tests/results/<timestamp>/`)

| 文件 | 内容描述 |
|------|----------|
| `run_manifest.json` | 运行参数快照（含 git commit、随机种子） |
| `phase_metrics.csv` | 每轮每对的 Auth/Probe/Context 延迟 |
| `chain_tx_metrics.csv` | 链上交易哈希、Gas、延迟、成本 |
| `discovery_metrics.csv` | 注册/检索/向量匹配指标 |
| `governance_metrics.csv` | 治理状态转移记录 |
| `case_assertions.csv` | 所有用例的通过/失败断言 |
| `latency_stats.csv` | 各阶段延迟统计（avg/P50/P95/max） |
| `mcp_metrics.csv` | MCP 工具调用延迟和权限验证指标 |
| `l2_cost_estimates.csv` | L2 网络成本估算 |
| `l2_operation_estimates.csv` | 按操作类型的 L2 成本细分 |
| `scale_projection.csv` | 大规模（100-1000 Agent）线性外推 |
| `summary.md` | 自动生成的测试摘要（含图表嵌入） |
| `raw_metrics.json` | 完整原始指标（脱敏后） |
| `chart_latency_stage.png` | 阶段延迟对比图 |
| `chart_case_passrate.png` | 用例通过率图 |
| `chart_mcp_latency_distribution.png` | MCP 工具调用延迟分布图 |
| `chart_mcp_test_matrix.png` | MCP 测试结果矩阵图 |
| `chart_mcp_tool_comparison.png` | MCP 工具延迟对比图 |

### 6.2 MCP 独立测试产出 (`fullflow_tests/results/mcp_<timestamp>/`)

| 文件 | 内容描述 |
|------|----------|
| `mcp_case_assertions.csv` | MCP 用例断言 |
| `mcp_metrics.csv` | 延迟指标 |
| `mcp_summary.md` | 文本摘要 |
| `chart_mcp_*.png` | 3 张论文可用图表 |

---

## 7. 环境要求与快速开始

### 7.1 前置依赖

```
Python 3.10+
uv/uvx（用于自动管理 MCP Server）
Node.js 18+（Hardhat 合约测试）
matplotlib（图表生成）
```

### 7.2 测试执行速查

```bash
# 1. 互操作单元测试（最快，~5秒）
python -m pytest interop/tests/test_interop_contract.py -v

# 2. MCP 互操作专项测试（无需链上，~30秒）
python fullflow_tests/run_mcp_tests.py

# 3. 全流程闭环测试（需Sepolia账户+LLM API，~20分钟）
python fullflow_tests/run_fullflow.py --profile paper

# 4. 多规模 + MCP 越权联动压测（需预生成账户并启动本地 Holder）
python _experiments/run_scale_tests.py
```

### 7.3 可复测性保障

- **固定随机种子**：`random_seed=20260307` 确保负例编排顺序一致
- **运行清单**：每次运行自动记录参数快照 + git commit
- **脱敏存储**：私钥等敏感字段在 `raw_metrics.json` 落盘前自动替换为 `***REDACTED***`

---

## 8. 测试指标与论文对齐

| 任务书指标 | 测试覆盖 | 关键文件 |
|-----------|----------|----------|
| 可信发现功能 | Discovery 阶段：链上注册→Subgraph→Sidecar 向量检索 | `discovery_metrics.csv` |
| 权限管理原型 | Auth(401拦截) + Toolset VC(动作级控制) | `case_assertions.csv` |
| 惩罚措施 | Governance 阶段：举报→冻结→罚没→恢复 | `governance_metrics.csv` |
| 多Agent并发交互 | 2v2 正例审计 × 多轮 + 批量延迟统计 | `phase_metrics.csv`, `latency_stats.csv` |
| 权限系统性能分析 | MCP 延迟分布 + 全流程延迟分解 | `mcp_metrics.csv`, `latency_stats.csv` |
| MCP 协议互操作 | MCP 互操作阶段：连接/发现/调用/权限/延迟 | `mcp_metrics.csv` |
