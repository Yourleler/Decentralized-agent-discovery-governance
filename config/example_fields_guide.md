# 三个示例文件字段说明

本文档说明以下三个示例文件中的字段含义：

- `config/agent_metadata_example.json`
- `config/vc_example.json`
- `config/vp_example.json`

## 1. agent_metadata_example.json

### 顶层字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `metadataVersion` | 元数据版本号 | 用于后续升级兼容 |
| `agentDid` | Agent 的 DID 标识 | 应与链上注册 DID 一致 |
| `adminAddress` | 管理员地址 | 用于治理追责或展示 |
| `service` | 服务基本信息 | 名称、摘要、领域、端点等 |
| `capabilities` | 能力列表 | 语义检索与能力匹配核心 |
| `vcManifest` | VC 清单摘要 | 告诉对方有哪些凭证可按需拉取 |
| `indexHints` | 检索提示信息 | 给向量化/关键词检索提供提示 |
| `timestamps` | 创建/更新时间 | 便于同步与审计 |

### service 字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `service.name` | 服务名称 | 人类可读展示名 |
| `service.summary` | 服务摘要 | 1~2 句描述核心能力 |
| `service.domain` | 业务领域 | 如 finance / medical |
| `service.tags` | 标签 | 用于筛选和检索增强 |
| `service.interactionModes` | 交互方式 | 如 `A2A_HTTP`、`JSON_RPC` |
| `service.endpoints` | 可用端点列表 | 可含多个入口 |
| `service.endpoints[].name` | 端点名称 | 如 primary-api |
| `service.endpoints[].url` | 端点地址 | 客户端实际调用地址 |
| `service.endpoints[].protocol` | 协议类型 | 如 https / ws |
| `service.endpoints[].auth` | 鉴权方式 | 如 did-sig / bearer |

### capabilities 字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `capabilities[].id` | 能力唯一标识 | 推荐稳定命名 |
| `capabilities[].name` | 能力名称 | 展示与检索使用 |
| `capabilities[].description` | 能力描述 | 检索排序的重要文本 |
| `capabilities[].inputs` | 输入参数列表 | 说明调用方要提供什么 |
| `capabilities[].outputs` | 输出字段列表 | 说明返回结果结构 |
| `capabilities[].examples` | 示例请求/任务 | 帮助快速理解能力 |

### vcManifest / indexHints / timestamps 字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `vcManifest.holderDid` | 持证者 DID | 通常与 `agentDid` 对应主体一致 |
| `vcManifest.types` | 支持的 VC 类型列表 | 如 `AgentIdentityCredential` |
| `vcManifest.lazyFetch` | 是否按需拉取完整 VC | `true` 表示延迟加载 |
| `vcManifest.fullVcRefs[]` | 完整 VC 引用列表 | 每项可带 `type/cid/uri/hash` |
| `indexHints.vectorText` | 向量化主文本 | 直接给检索模型使用 |
| `indexHints.searchableKeywords` | 关键词列表 | 做关键词过滤/补充召回 |
| `timestamps.createdAt` | 创建时间 | ISO 8601 |
| `timestamps.updatedAt` | 更新时间 | ISO 8601 |

## 2. vc_example.json

### 顶层字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `@context` | VC 语义上下文 | W3C VC 规范字段 |
| `type` | VC 类型数组 | 必含 `VerifiableCredential` |
| `issuer` | 发证方 DID | 签发该 VC 的主体 |
| `validFrom` | 生效时间 | 或使用 `issuanceDate` |
| `validUntil` | 失效时间 | 验证时可做过期检查 |
| `credentialSubject` | 被证明主体声明 | 具体 claim 放这里 |
| `proof` | 签名证明对象 | 含签名与验签元信息 |

### credentialSubject 字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `credentialSubject.id` | 该 VC 证明的主体 DID | 通常是 holder DID |
| `credentialSubject.agentDid` | Agent DID 冗余字段 | 便于跨模块关联 |
| `credentialSubject.claimsVersion` | claims 版本号 | 便于 claim 结构演进 |
| `credentialSubject.agent_name` | Agent 名称声明 | 业务自定义字段 |
| `credentialSubject.developer_did` | 开发者 DID | 业务自定义字段 |
| `credentialSubject.integrityCheck` | 完整性检查结果 | 业务自定义字段 |

### proof 字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `proof.type` | 签名套件类型 | 例如 `EcdsaSecp256k1Signature2019` |
| `proof.created` | 签名时间 | ISO 8601 |
| `proof.verificationMethod` | 验证方法标识 | 通常是 DID 文档里的 key id |
| `proof.proofPurpose` | 签名用途 | 如 `assertionMethod` |
| `proof.jws` | 签名值 | 验签核心数据 |

## 3. vp_example.json

### 顶层字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `@context` | VP 语义上下文 | W3C VP 规范字段 |
| `type` | VP 类型数组 | 必含 `VerifiablePresentation` |
| `holder` | 持有者 DID | 发起展示与签名的一方 |
| `holderBinding` | 持有者绑定信息 | 可用于 Admin/OP 分离追责 |
| `verifiableCredential` | 携带的 VC 列表 | 供对方现场验证 |
| `proof` | VP 自身签名证明 | 防篡改、防重放核心字段 |

### holderBinding 字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `holderBinding.agentDid` | 绑定的 Agent DID | 追责主标识 |
| `holderBinding.adminAddress` | 管理员地址 | 经济责任主体 |
| `holderBinding.opAddress` | 操作地址 | 实际执行交互的地址 |
| `holderBinding.opKid` | 操作密钥 ID | 对应 DID 文档中的 delegate key |

### proof 字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `proof.type` | VP 签名类型 | 例如 `EcdsaSecp256k1RecoverySignature2020` |
| `proof.created` | 签名时间 | ISO 8601 |
| `proof.verificationMethod` | 验证方法标识 | 通常是 holder 的 key id |
| `proof.proofPurpose` | 签名用途 | 常见为 `authentication` |
| `proof.challenge` | 挑战随机数（nonce） | 防重放，需与 verifier 下发一致 |
| `proof.jws` | VP 签名值 | verifier 用它验签 |

## 4. 使用建议

- 注册/发现阶段优先使用 metadata（轻量）。
- 握手时携带 VP + 必要 VC（按需，避免过重）。
- 任何时间相关字段统一使用 ISO 8601（UTC）。
