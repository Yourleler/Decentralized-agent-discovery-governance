# 示例字段说明（2026-04 对齐版）

本文档对应以下示例文件：
1. `config/agent_metadata_example.json`
2. `config/vc_example.json`
3. `config/vp_example.json`

目标：统一“发现字段、凭证字段、授权字段”的语义口径，避免样例和运行时实现不一致。

---

## 1. agent_metadata_example.json

### 1.1 顶层字段
| 字段 | 含义 | 说明 |
|---|---|---|
| `metadataVersion` | 元数据版本 | 用于升级兼容 |
| `agentDid` | Agent DID | 应与链上注册 DID 一致 |
| `adminAddress` | 管理地址 | 用于责任主体标识 |
| `service` | 服务描述 | 发现阶段主输入 |
| `capabilities` | 能力声明 | 语义检索和筛选依据 |
| `vcManifest` | 凭证清单摘要 | 告诉对方“可按需出示哪些 VC” |
| `indexHints` | 检索提示 | 向量文本与关键词提示 |
| `timestamps` | 时间字段 | 建议 UTC ISO 8601 |
| `interop` | 互操作稳定字段 | A2A/MCP 接入说明（低频变化） |

### 1.2 service 关键字段
| 字段 | 含义 |
|---|---|
| `service.name` | 服务名称 |
| `service.summary` | 服务摘要 |
| `service.domain` | 业务领域 |
| `service.tags` | 标签列表 |
| `service.interactionModes` | 交互模式（如 `A2A_HTTP`） |
| `service.endpoints[]` | 可用端点配置 |

### 1.3 interop 字段（新增重点）
| 字段 | 含义 | 是否高频变化 |
|---|---|---|
| `interop.supportedProtocols` | 支持协议，如 `native`、`a2a` | 否 |
| `interop.a2aEndpoint` | A2A 调用入口 | 否 |
| `interop.supportedInteractionModes` | 互操作方式摘要 | 否 |
| `interop.authMode` | 鉴权方式，如 `did-sig` | 否 |

说明：
- 动态工具权限不建议放 metadata，避免频繁改 CID。

---

## 2. vc_example.json

### 2.1 VC 通用字段
| 字段 | 含义 |
|---|---|
| `@context` | VC 语义上下文 |
| `type` | 凭证类型（需含 `VerifiableCredential`） |
| `issuer` | 发证方 DID |
| `validFrom` / `validUntil` | 生效/过期时间 |
| `credentialSubject` | 被证明主体及 claims |
| `proof` | VC 签名证明 |

### 2.2 Toolset VC（AgentToolsetCredential）建议字段
| 字段 | 含义 |
|---|---|
| `toolManifest[].identifier` | 工具唯一标识 |
| `toolManifest[].providerProtocol` | `mcp` / `native` |
| `toolManifest[].serverId` | MCP Server 标识 |
| `toolManifest[].serverEndpoint` | 工具服务端点 |
| `toolManifest[].allowedActions` | 允许动作 |
| `toolManifest[].allowedResources` | 允许资源范围 |
| `toolManifest[].riskLevel` | 风险等级 |
| `toolManifest[].rateLimit` | 频率约束 |
| `toolManifest[].operationalStatus` | 当前状态 |

---

## 3. vp_example.json

### 3.1 VP 通用字段
| 字段 | 含义 |
|---|---|
| `@context` | VP 语义上下文 |
| `type` | 演示类型（需含 `VerifiablePresentation`） |
| `holder` | 出示方 DID |
| `verifiableCredential` | 携带的 VC 列表 |
| `proof` | VP 自身签名 |

### 3.2 holderBinding（建议）
| 字段 | 含义 |
|---|---|
| `holderBinding.agentDid` | 绑定 Agent DID |
| `holderBinding.adminAddress` | Admin 责任主体 |
| `holderBinding.opAddress` | OP 执行地址 |
| `holderBinding.opKid` | OP key id |

### 3.3 session（新增重点）
| 字段 | 含义 |
|---|---|
| `session.requestId` | 绑定请求编号 |
| `session.timestamp` | 绑定请求时间 |
| `session.resource` | 绑定请求资源 |
| `session.action` | 绑定请求动作 |
| `session.authorizationDetailsHash` | 绑定授权细节哈希 |
| `session.verifierDid` | 绑定请求方 DID |

意义：
- 防止 VP 在不同请求间复用。

---

## 4. 请求封套字段（与运行时一致）
下列字段应在 auth/probe/context/A2A 请求中统一提供：

| 字段 | 含义 |
|---|---|
| `requestId` | 请求唯一编号 |
| `timestamp` | 请求时间 |
| `nonce` | 防重放随机量 |
| `resource` | 本次访问资源 |
| `action` | 本次执行动作 |
| `authorizationDetails` | 细粒度授权意图 |

`authorizationDetails` 常用子字段：
1. `type`
2. `actions`
3. `locations`
4. `datatypes`
5. `identifier`
6. `privileges`

---

## 5. 使用建议
1. 发现阶段：用 metadata（轻量）。
2. 握手阶段：按需出示 VC/VP（最小必要披露）。
3. 工具调用阶段：按 Toolset VC 的 `tool/action/resource` 做强校验。
4. 治理阶段：保留 request/VP/response/日志作为 PoM 证据链。

---

## 6. 一致性检查清单
提交样例前建议自检：
1. DID 是否与链上/本地配置一致。
2. 时间字段是否为 UTC ISO 8601。
3. Toolset VC 是否包含动作与资源边界。
4. VP session 是否与请求字段一一对应。
5. metadata 是否只放稳定字段，避免把高频权限塞进去。

---

## 7. 这些字段在运行时分别被谁使用
为了避免“样例写了但代码不用”这种情况，可以这样理解字段归属：
1. metadata
- 主要由 Discovery 和 Sidecar 使用。
- 用于检索、展示、过滤、构建向量索引。

2. VC
- 主要由 Issuer 签发、Holder 持有、Verifier 按需验证。
- 用于证明“你是谁、你有什么能力、你被授权用什么工具”。

3. VP
- 主要由 Holder 在握手阶段出示，Verifier 在现场验证。
- 用于证明“这些 VC 现在由我为这次请求出示”。

4. request 封套字段
- 主要由 Verifier/A2A 请求方构造，Holder/Validator 校验。
- 用于把权限判断从“验签”提升到“验签 + 验意图 + 验资源边界”。

---

## 8. 写样例时最容易出错的地方
1. 把动态工具权限写进 metadata。
- 这样会导致 metadata 频繁变化，不适合链上 CID 锚定。

2. VP 没有绑定 requestId/resource/action。
- 会让旧 VP 被错误复用。

3. Toolset VC 没写 `allowedActions/allowedResources`。
- 系统就没法做细粒度越权判断。

4. DID、adminAddress、holderBinding 三者不一致。
- 会导致追责路径混乱。
