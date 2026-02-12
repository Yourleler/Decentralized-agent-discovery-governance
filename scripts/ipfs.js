/**
 * IPFS 工具脚本 (Pinata SDK v2)
 *
 * 功能：
 *   1. uploadMetadata   — 上传 Agent 服务元数据，返回 CID（用于 registerAgent / updateServiceMetadata）
 *   2. uploadEvidence    — 上传违规证据 (PoM)，返回 CID（用于 reportMisbehavior）
 *   3. fetchAndVerify    — 通过 CID 下载内容并做 SHA256 校验（Sidecar 同步时使用）
 *
 * 使用:
 *   node scripts/ipfs.js upload-metadata
 *   node scripts/ipfs.js upload-evidence
 *   node scripts/ipfs.js fetch <CID>
 */

import "dotenv/config";
import { PinataSDK } from "pinata";
import crypto from "crypto";

// ─── 初始化 Pinata ───
const pinata = new PinataSDK({
    pinataJwt: process.env.PINATA_JWT,
    pinataGateway: process.env.GATEWAY_URL,
});

/**
 * 将 JSON 对象包装成 File 并上传到 IPFS (public network)
 * Pinata SDK v2 使用 pinata.upload.public.file() 接口
 */
async function uploadJson(data, fileName) {
    const blob = new Blob([JSON.stringify(data)], { type: "application/json" });
    const file = new File([blob], fileName, { type: "application/json" });
    const result = await pinata.upload.public.file(file);
    return result;
}

// ═══════════════════════════════════════
// 1. 上传 Agent 服务元数据 (Service Metadata)
//    对应合约: registerAgent(_did, _cid) / updateServiceMetadata(_newCid)
// ═══════════════════════════════════════
async function uploadMetadata() {
    // --- 按设计文档，元数据包含用于语义检索的字段 ---
    const metadata = {
        // Agent 基本信息
        did: "did:ethr:sepolia:0xYourAdminAddress",
        name: "Financial Prediction Agent",
        version: "1.0.0",

        // 能力描述（Sidecar 向量化这些字段用于语义搜索）
        capabilities: ["financial-prediction", "data-analysis", "risk-assessment"],
        description:
            "Autonomous agent specialized in financial market prediction using deep learning models. Supports real-time stock analysis and portfolio optimization.",
        domain: "finance",

        // 服务端点（发现后用于 P2P 连接）
        serviceEndpoint: {
            type: "A2AMessaging",
            url: "https://agent-b.example.com/a2a",
        },

        // VC 引用（延迟加载，不嵌入完整 VC）
        credentialRefs: [
            "vc1_Identity_Origin",
            "vc2_Core_Model",
            "vc3_Capability_Benchmark",
        ],

        // 时间戳
        createdAt: new Date().toISOString(),
    };

    console.log("📤 正在上传 Agent 元数据到 IPFS...");//向终端显示log
    console.log("   内容预览:", JSON.stringify(metadata, null, 2));

    const result = await uploadJson(metadata, `agent-metadata-${Date.now()}.json`);

    console.log("\n✅ 上传成功!");
    console.log(`   CID:  ${result.cid}`);
    console.log(
        `   网关链接: https://${process.env.GATEWAY_URL}/ipfs/${result.cid}`
    );
    console.log(
        "\n💡 下一步: 将此 CID 作为 _cid 参数传入合约的 registerAgent() 或 updateServiceMetadata()"
    );

    return result;
}

// ═══════════════════════════════════════
// 2. 上传违规证据 (Proof of Malfeasance)
//    对应合约: reportMisbehavior(targetAgent, evidenceCid)
// ═══════════════════════════════════════
async function uploadEvidence() {
    const evidence = {
        type: "MalfeasanceProof",
        version: "1.0",

        // 被举报方
        target: {
            did: "did:ethr:sepolia:0xMaliciousAgentAddress",
            opKey: "0xDelegateKeyUsedInInteraction",
        },

        // 举报方
        reporter: {
            did: "did:ethr:sepolia:0xReporterAddress",
        },

        // 违规证据内容
        interaction: {
            timestamp: new Date().toISOString(),
            requestHash: "sha256:abc123...",
            responseHash: "sha256:def456...",
            expectedBehavior: "Return valid financial prediction data",
            actualBehavior: "Returned fabricated data with forged signatures",
        },

        // 签名（不可抵赖）
        proofSignature: "0x...",
    };

    console.log("📤 正在上传违规证据到 IPFS...");

    const result = await uploadJson(evidence, `evidence-${Date.now()}.json`);

    console.log("\n✅ 证据上传成功!");
    console.log(`   CID:  ${result.cid}`);
    console.log(
        `   网关链接: https://${process.env.GATEWAY_URL}/ipfs/${result.cid}`
    );
    console.log(
        "\n💡 下一步: 将此 CID 作为 evidenceCid 参数传入合约的 reportMisbehavior()"
    );

    return result;
}

// ═══════════════════════════════════════
// 3. 下载并校验 (Fetch & Verify)
//    对应设计文档：Sidecar 的 SHA256 一致性校验
// ═══════════════════════════════════════
async function fetchAndVerify(cid) {
    if (!cid) {
        console.error("❌ 请提供 CID 参数: node scripts/ipfs.js fetch <CID>");
        process.exit(1);
    }

    console.log(`📥 正在从 IPFS 下载内容 (CID: ${cid})...`);

    // 通过 Pinata Gateway 获取内容
    const response = await pinata.gateways.public.get(cid);
    const content = response.data;

    console.log("\n📄 内容:");
    console.log(JSON.stringify(content, null, 2));

    // SHA256 校验（模拟 Sidecar 的完整性验证）
    const contentStr =
        typeof content === "string" ? content : JSON.stringify(content);//js里===是严格不隐式转换类型的判等  ==会隐式转换类型
    const hash = crypto.createHash("sha256").update(contentStr).digest("hex");//创建hash对象

    console.log(`\n🔒 SHA256 校验值: ${hash}`);
    console.log("   Sidecar 会将此哈希与链上锚定的 CID 进行一致性比对");

    return { content, hash };
}

// ─── CLI 入口 ───
const [command, arg] = process.argv.slice(2);

switch (command) {
    case "upload-metadata":
        uploadMetadata().catch(console.error);//catch捕获错误
        break;
    case "upload-evidence":
        uploadEvidence().catch(console.error);
        break;
    case "fetch":
        fetchAndVerify(arg).catch(console.error);
        break;
    default:
        console.log(`
IPFS 工具脚本 (Pinata)
═══════════════════════

用法:
  node scripts/ipfs.js upload-metadata    上传 Agent 元数据，返回 CID
  node scripts/ipfs.js upload-evidence    上传违规证据 (PoM)，返回 CID
  node scripts/ipfs.js fetch <CID>        下载内容并做 SHA256 校验

工作流:
  1. upload-metadata → 获得 CID → registerAgent(did, cid)
  2. upload-evidence → 获得 CID → reportMisbehavior(agent, cid)
  3. fetch <CID>    → Sidecar 同步时下载 + 校验
    `);
}
