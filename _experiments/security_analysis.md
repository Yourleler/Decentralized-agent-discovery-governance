## 1. 针对“越权访问”与“权限滥用”的威胁模型 (Threat Model)

在“模型即服务”的去中心化多智能体网络中，传统中心化访问网关缺失，Agent 需要点对点(P2P)自主交互。我们针对任务书需求，建立了以下核心威胁模型：

1. **越权访问攻击**：恶意 Agent 未经授权，试图直接调用目标智能体的接口窃取模型数据或调用执行器工具。
2. **权限滥用攻击**：恶意 Agent 注册后，滥用其业务权限进行“挂羊头卖狗肉”（如模型能力造假）或在交互中恶意篡改上下文以逃脱问责。
3. **零成本女巫与洗白**：作恶被发现后，攻击者低成本丢弃身份，重新越权。

---

## 2. 细粒度权限控制与防越权访问设计

### 2.1 基于 DID+VP 的零信任访问控制
传统 API Key 的静态权限无法适应智能体。本系统采用去中心化身份(DID)与可验证凭证(VP)实现了机机交互的强制挑战-应答认证。

| 机制点 | 代码对应与防御逻辑 | 实测结果 |
|--------|----------------|----------|
| **防身份冒用访问** | `validator.py` 验证基于 ECDSA 的 DID 签名。没有私钥无法发起接口级调用。 | 负例 `fake_signature_auth` ✅ ：伪造签名的请求被 Holder 直截了当以 **HTTP 401 (Unauthorized)** 拒绝。 |
| **防旧凭证重放越权** | 认证强制携带单次 UUIDv4 作为 `nonce/challenge`，防止恶意 Agent 抓包过去合法的授权凭证来请求新资源。 | 负例 `tampered_vp_challenge` ✅ ：重放/被篡改 challenge 的请求阻击成功。 |
| **防凭证权限篡改** | JWS 签名保证了请求上下文不能在传输中被修改。 | 负例 `tampered_vp_signature` ✅ ：细微篡改即导致验签异常拦截。 |

> **论证结论**：通过拦截未经授权和伪造状态的直接请求，系统的 Auth（认证）阶段构筑了一道严密的屏障，确立了防范“恶意 Agent 越权操作”的第一道防线。

---

## 3. 防权限滥用与数据造假检测机制

智能体获得交互权限后，可能滥发假数据或破坏交互连贯性。针对这类在权限生命周期内的**滥用行为**，系统在发现（Discovery）和探针（Probe & Context）层实施强力拦截：

### 3.1 运行时上下文篡改与失忆（环境破坏滥用）
- **现象**：Agent 利用单方面销毁交互数据的形式以推卸业务责任（责任推诿机制失灵）。
- **防御**：系统实施**上下文哈希一致性检查**。双方对交互进行序列化并比对 `Context Hash`。
- **实测**：负例 `context_mismatch` ✅ 显示，当一方清空或伪造记忆时，立刻触发 `Mismatch (L:bad1ae R:4f53cd)` 错误机制并中断访问。

### 3.2 链下索引元数据投毒（能力声称滥用）
- **现象**：在 Sidecar 获取信息时，恶意 Agent 通过修改存储节点的数据使检索者获得污染数据。
- **防御**：寻址与数据内容的强验证（基于 CID 的 SHA-256 算法），不通过则立刻在同步节点侧抛弃，根本不赋予检索展现权限。

---

## 4. 全链路发现与智能体惩罚制裁措施 (Slashing Governance)

仅仅防范拦截不足以构建内生安全网络，违规必须付出代价。这是对抗无成本作恶的最终环节。当上述越权和滥用行为触发后，系统设置了完善的不可篡改惩罚措施。

### 4.1 惩罚裁决状态机 (Governance Slashing)
如果发生滥用（如验证时 Probe 超时未就绪或上下文无法对齐），发起交互的审计方将收集现场日志并固化为 IPFS 证据 CID。随后触发链上惩罚：
`reportMisbehavior` → `freeze` → `slash` (罚没)

| 实测操作 | 后置状态 (链上合约变更) | 有效性 |
|----------|----------------|--------|
| **举报并冻结** | `isfrozen=true`，立即封存所有权限操作 | ✅ 通过 |
| **执行罚没(Slash)** | `isSlashed=true`，扣除质押的真金白银(ETH)，`accumulatedPenalty` 激增 | ✅ 通过 |

### 4.2 惩罚传导与权限剥夺 (业务网封杀)
智能合约的惩罚将即刻触发链下**权限肃清**：
- P2P 网络层面的 Sidecar 在监听到 `slash` 事件后，会在向量库 ChromaDB 中 **强制抹除该 Agent 的索引记录**。
- 这意味着一旦违规并接受惩罚，该节点从整个分布式 Agent 网络中彻底“隐身”，物理层面上被剥夺了接单和继续滥用系统的权限。

## 5. 治理闭环实测验证

### 5.1 完整治理状态转移测试

本地 Hardhat 环境已验证完整的治理状态机转移：

```
register → reportMisbehavior → freeze → slash → unfreeze → appeal → restore
```

| 动作 | 链上函数 | 前置状态 | 后置状态 | 测试结果 |
|------|----------|----------|----------|----------|
| 举报 | `reportMisbehavior()` | isRegistered=true | MisbehaviorReported 事件触发 | ✅ `reportSubmitted=true` |
| 冻结 | `freeze()` | isfrozen=false | isfrozen=true | ✅ `freezeApplied=true` |
| 罚没 | `slash()` | isSlashed=false | accumulatedPenalty=20 | ✅ `afterSlash.accumulatedPenalty=20` |
| 解冻 | `unfreeze()` | isfrozen=true | isfrozen=false | ✅ `unfreezeApplied=true` |
| 申诉 | `appeal()` | isRegistered=true | AgentAppealed 事件触发 | ✅ `appealSubmitted=true` |
| 恢复 | `restore()` | isSlashed/penalty=20 | penalty=5, isSlashed=false | ✅ `afterRestore.accumulatedPenalty=5` |

### 5.2 Sepolia 链上举报验证

真实 Sepolia 测试网: 区块 10402187 已确认 `reportMisbehavior` 交易：
- **交易哈希**: `0x177acdfd5ed6d67648d846f716c1b9a138306e049f8878c9157cba71fe0e568c`
- **Gas 消耗**: 28,977
- **确认延迟**: 7.67 秒
- **状态**: 成功

---

## 6. 基础权限控制机制分析

### 6.1 Admin-OP 权限分离

| 角色 | 权限范围 | 链上操作 | 泄露影响 |
|------|----------|----------|----------|
| **Admin** | 质押/注销/治理追责主体 | registerAgent, depositStake, withdrawStake, unregisterAgent | 直接经济损失 |
| **OP** | 日常交互签名 | 无链上操作权限 | 仅影响对外交互，不影响质押安全 |
| **Governance** | 治理裁决执行 | freeze, slash, restore, unfreeze | 需 GOVERNANCE_ROLE |

**防越权机制**：
- OP 无法执行链上质押操作（合约函数均检查 `msg.sender == admin`）
- 注销操作在冻结状态下不可执行（`require(!agents[msg.sender].isfrozen)`）
- 减持操作受分数下限约束（`require(getGlobalScore(msg.sender) > SCORE_MIN)`）
- 治理操作限制 GOVERNANCE_ROLE（`onlyRole(GOVERNANCE_ROLE)`）

### 6.2 AccessControl 角色管理

合约基于 OpenZeppelin `AccessControl`，仅 `DEFAULT_ADMIN_ROLE` 可分配/撤销治理角色，防止普通 Agent 自行提权。

---

## 7. 外部工具(MCP)接入的细粒度权限控制

### 7.1 威胁场景

在多智能体协作中，Agent 通过 MCP（Model Context Protocol）协议调用外部工具（如时间查询、网页抓取）。若缺乏权限控制，恶意 Agent 可能：
- **越权调用**：调用未被授权的敏感工具（如数据库写入、文件删除）
- **动作升级**：对只有 `query` 权限的工具执行 `execute` 动作
- **无凭证访问**：绕过凭证体系直接访问工具接口

### 7.2 Toolset VC 权限控制机制

系统通过 `AgentToolsetCredential`（Toolset VC）实现 MCP 工具的细粒度权限控制：

```json
{
  "type": ["VerifiableCredential", "AgentToolsetCredential"],
  "credentialSubject": {
    "toolManifest": [
      {
        "identifier": "get_current_time",
        "providerProtocol": "mcp",
        "allowedActions": ["query"],
        "allowedResources": ["resource:time:*"],
        "riskLevel": "low"
      }
    ]
  }
}
```

权限判定逻辑（`request_policy.py` → `evaluate_tool_authorization()`）执行三重校验：
1. **工具标识匹配**：请求的 `tool_identifier` 必须出现在 VC 的 `toolManifest` 中
2. **动作授权检查**：请求的 `action` 必须在 `allowedActions` 列表中
3. **资源范围限定**：请求的 `resource` 必须匹配 `allowedResources` 的通配符模式

### 7.3 实测验证结果

基于 MCP 互操作专项测试（`mcp_interop.py`）的实测数据：

| 测试场景 | 请求参数 | 预期结果 | 实测结果 | 拦截 |
|----------|----------|----------|----------|------|
| **权限正例** | tool=get_current_time, action=query, 携带合法 VC | allowed=True | allowed=True, reason=ok | ✅ 通过 |
| **非法动作** | tool=get_current_time, action=**execute**, 携带合法 VC | allowed=False | allowed=False, reason=工具动作未授权 | ✅ 拦截 |
| **无凭证** | tool=get_current_time, action=query, **无 VC** | allowed=False | allowed=False, reason=未找到已授权工具 | ✅ 拦截 |

**MCP 权限控制拦截率：3/3 = 100%**

### 7.4 MCP Server 接入安全边界

| 安全边界 | 控制机制 | 说明 |
|----------|----------|------|
| Server 启动权限 | `mcp_servers.json` 白名单 | 仅配置中声明的 Server 可被启动 |
| 工具调用权限 | Toolset VC + `evaluate_tool_authorization` | 每次调用均需 VC 声明的工具+动作匹配 |
| 传输层隔离 | stdio 管道（无网络暴露） | 本地 Server 通过管道通信，不暴露端口 |
| 兼容性安全 | `resources/list` 优雅降级 | 不支持的协议端点不会导致流程崩溃 |

---

## 8. 安全性与权限控制总结

| 攻击类型 | 针对目标 | 防御与惩罚机制 | 测试覆盖 | 拦截判定 |
|----------|----------|----------------|----------|----------|
| 越权访问调用 | 防非法接口调用 | DID 签名检查与 HTTP 401 | ✅ | 100% 拦截 |
| 通信凭证伪造/重放 | 防旧授权滥用 | Nonce 防重放与 JWS 验签 | ✅ | 100% 拦截 |
| 业务上下文失联 | 防违规"失忆" | 双方一致性哈希核对 | ✅ | 100% 发现 |
| 索引元数据投毒 | 防假能力欺诈 | SHA-256 强制数据一致校验 | ✅ | 100% 隔离 |
| **MCP 工具越权调用** | 防非法工具/动作 | Toolset VC 三重校验 | ✅ | 100% 拦截 |
| **MCP 无凭证访问** | 防绕过 VC 体系 | 空 VC 列表强制拒绝 | ✅ | 100% 拦截 |
| 无成本越权(女巫) | 防零伤重试 | ETH质押成本+Slash没收+声誉抹除 | 设计分析 | 商业不可行 |
| 系统内角色越权 | 防链上资产挪用 | 合约Admin-OP分离控制 | 设计分析 | 强制限制 |

> **核心结论**：本系统彻底贯彻了权限控制要求，在不信任的基础网络上构建了"事前拒绝越权访问（Auth 401）"、"事中拦截权限滥用（Probe/Context 校验 + MCP Toolset VC 细粒度控制）"、"事后严厉全网惩罚（Slash + 肃清向量索引）"三位一体的权限管理与惩罚体系。系统以零容忍的态势确保了恶意 Agent 的每一次越权和欺骗行为均有对应的硬拦截与惩戒，完整切题多智能体协作下的细粒度权限管控要求。MCP 外部工具接入层通过 Toolset VC 实现了**工具级、动作级、资源级**的三维权限控制，实测拦截率 100%。
