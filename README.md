# AgentDID Demo(暂未完工)

[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/Node.js-18.20-green)](https://nodejs.org/)
[![npm](https://img.shields.io/badge/npm-10.8-red)](https://www.npmjs.com/)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)

## 📖 项目概述 (Project Overview)

这是一个构建 **"Agent Native"** 去中心化身份认证系统的演示项目 (Proof of Concept)。

本项目旨在探索 AI Agent 在去中心化身份（DID）网络中的自主交互能力，重点实现了 Holder Agent 与 Verifier Agent 之间的端到端认证流程。

### 核心流程
1.  **Step 1 (注册)**: 注册 DID 并添加 Delegate 授权（由 Agent 的实际控制者操作）。
2.  **Step 2 (自主申领)**: Agent 启动时，自主向 Issuer 申请 VC (Verifiable Credential)。
3.  **Step 3 (身份认证)**: Agent 之间进行基于 DID 的身份验证。
4.  **Step 4 (探测与审计)**: Verifier 对 Holder 发起 Probe Task，进行状态探测及上下文一致性检查 (Context Consistency Check)。

---

## 🛠️ 环境准备 (Prerequisites)

本项目依赖 Python 和 Node.js 环境。为了确保系统稳定性，**强烈建议使用以下版本（或更高版本）**：

*   **Python**: `3.11.14` (需支持 Python 3.10+ 语法)
*   **Node.js**: `18.20.8` (用于 DID 解析服务)
*   **npm**: `10.8.2`

### 安装步骤

1.  **安装 Python 依赖**:
    ```bash
    pip install -r requirements.txt
    ```
2.  **安装 Node.js 依赖**:
    项目根目录下包含 `package.json` 和 `package-lock.json`。请确保已安装 Node.js，然后运行：
    ```bash
    npm install
    ```
3.  **配置密钥**:
    *   复制 `config/key_example.json` 为 `config/key.json`。
    *   **重要**: 填入你的 Sepolia 测试网 API URL、LLM API Key 以及拥有 Sepolia ETH 的账户私钥。
    *   ⚠️ **安全警告**: 请勿将包含真实资产的私钥提交到版本控制系统！确保 `config/key.json` 文件已添加到 `.gitignore`。

---

## 🚀 使用说明 (Usage)

本项目提供两种运行模式：**2v2 全流程演示** 和 **大规模并发实验**。

### 模式一：2v2 全流程演示
> **场景**: 演示 2 个 Holder 和 2 个 Verifier 之间的完整交互周期。

**启动步骤**:

1.  **初始化账户**: 生成 4 对密钥、注册 DID 并授权 Delegate。
    ```bash
    python _demo_2v2/setup_4_agents.py
    ```
2.  **启动发证机构 (Issuer)**:
    ```bash
    python _ops_services/issuer_server.py
    ```
3.  **启动 Agent 网络**:
    新开一个终端，运行网络编排脚本（启动 Holders 和 Verifiers）：
    ```bash
    python _demo_2v2/start_network.py
    ```
4.  **触发审计流程**:
    新开一个终端，向 Verifier 发送指令，开始对 Holder 进行探测：
    ```bash
    python _demo_2v2/trigger_audit.py
    ```

### 模式二：大规模并发实验 (Experiments)
> **场景**: 性能压测、延迟测量及 VC 存储成本分析。

**启动步骤**:

1.  **批量生成身份 (N个)**:
    修改脚本中的 N 值，生成大量测试账户：
    ```bash
    python _experiments/setup_agents_N.py
    ```
2.  **准备密钥**:
    确保生成的 `holders_key.json` 和 `verifiers_key.json` 已放置在 `data/` 目录下。
3.  **启动服务端 (Holders)**:
    启动 Issuer（如果尚未启动）和 P2P Holder 集群：
    ```bash
    python _ops_services/issuer_server.py
    python _experiments/start_p2p_holders.py
    ```
4.  **启动客户端并压测 (Verifiers)**:
    启动 Verifier 集群并发起攻击/探测，测试结果将输出为 CSV：
    ```bash
    python _experiments/stress_test_p2p.py
    ```

---

## 📊 实验工具 (Benchmarks)

*   **VC 大小测量**: 运行 `_experiments/measure_vc_size.py` 查看不同 Schema VC 的存储开销。
*   **上下文哈希性能**: 运行 `_experiments/context_test.py` 测试随着对话轮数增加，Hash 计算的时间成本曲线。

---

## ⚠️ 常见问题 (Troubleshooting)

*   **FileNotFoundError**: 通常是路径问题。请确保在项目根目录下运行脚本。
*   **DID 解析失败**: 检查 Node.js 是否安装且 `node` 命令在 PATH 中。
*   **Gas 不足**: 确保 `key.json` 中的 Master 账户有足够的 Sepolia ETH 用于分发和注册。

## License

[MIT License](LICENSE)