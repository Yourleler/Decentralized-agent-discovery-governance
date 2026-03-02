/**
 * IPFS 工具脚本（Pinata SDK v2）
 *
 * 用法:
 *   node scripts/ipfs.js upload-metadata
 *   node scripts/ipfs.js upload-evidence
 *   node scripts/ipfs.js fetch <CID>
 */

import "dotenv/config";
import { PinataSDK } from "pinata";
import crypto from "crypto";

const pinata = new PinataSDK({
    pinataJwt: process.env.PINATA_JWT,
    pinataGateway: process.env.GATEWAY_URL,
});

/**
 * 上传 JSON 到 IPFS（public network）
 */
async function uploadJson(data, fileName) {
    const blob = new Blob([JSON.stringify(data)], { type: "application/json" });
    const file = new File([blob], fileName, { type: "application/json" });
    return await pinata.upload.public.file(file);
}

/**
 * 上传 Agent Metadata
 * 对齐: config/agent_metadata_format.schema.json
 */
async function uploadMetadata() {
    const nowIso = new Date().toISOString();
    const metadata = {
        metadataVersion: "2.0.0",
        agentDid: "did:ethr:sepolia:0xYourAdminAddress",
        adminAddress: "0xYourAdminAddress",
        service: {
            name: "Financial Prediction Agent",
            summary: "Autonomous agent for financial time-series forecasting and risk assessment.",
            domain: "finance",
            tags: ["forecast", "timeseries", "risk"],
            interactionModes: ["A2A_HTTP", "JSON_RPC"],
            endpoints: [
                {
                    name: "primary-api",
                    url: "https://agent-b.example.com/api/v1",
                    protocol: "https",
                    auth: "did-sig",
                },
            ],
        },
        capabilities: [
            {
                id: "cap.finance.forecast.v1",
                name: "Financial Forecasting",
                description: "Predicts short-term price range using historical OHLCV features.",
                inputs: ["symbol", "timeframe", "historyWindow"],
                outputs: ["predictedRange", "confidence"],
                examples: ["Predict BTC range for next 24h."],
            },
        ],
        vcManifest: {
            holderDid: "did:ethr:sepolia:0xYourAdminAddress",
            types: [
                "AgentIdentityCredential",
                "AgentModelCredential",
                "AgentCapabilityCredential",
                "AgentToolsetCredential",
                "AgentComplianceCredential",
            ],
            lazyFetch: true,
            fullVcRefs: [
                {
                    type: "AgentIdentityCredential",
                    cid: "bafybeihxxxx",
                },
            ],
        },
        indexHints: {
            vectorText: "Finance forecasting agent for BTC/ETH time-series prediction and risk analysis.",
            searchableKeywords: ["finance forecasting", "time-series", "risk control"],
        },
        timestamps: {
            createdAt: nowIso,
            updatedAt: nowIso,
        },
    };

    console.log("正在上传 Agent 元数据到 IPFS...");
    console.log("内容预览:", JSON.stringify(metadata, null, 2));

    const result = await uploadJson(metadata, `agent-metadata-${Date.now()}.json`);

    console.log("\n上传成功");
    console.log(`CID: ${result.cid}`);
    console.log(`网关链接: https://${process.env.GATEWAY_URL}/ipfs/${result.cid}`);
    console.log("\n下一步：将此 CID 作为 _cid 传入合约 registerAgent() 或 updateServiceMetadata()");

    return result;
}

/**
 * 上传违规证据（PoM）
 */
async function uploadEvidence() {
    const evidence = {
        type: "MalfeasanceProof",
        version: "1.0",
        target: {
            did: "did:ethr:sepolia:0xMaliciousAgentAddress",
            opKey: "0xDelegateKeyUsedInInteraction",
        },
        reporter: {
            did: "did:ethr:sepolia:0xReporterAddress",
        },
        interaction: {
            timestamp: new Date().toISOString(),
            requestHash: "sha256:abc123...",
            responseHash: "sha256:def456...",
            expectedBehavior: "Return valid financial prediction data",
            actualBehavior: "Returned fabricated data with forged signatures",
        },
        proofSignature: "0x...",
    };

    console.log("正在上传违规证据到 IPFS...");
    const result = await uploadJson(evidence, `evidence-${Date.now()}.json`);

    console.log("\n上传成功");
    console.log(`CID: ${result.cid}`);
    console.log(`网关链接: https://${process.env.GATEWAY_URL}/ipfs/${result.cid}`);
    console.log("\n下一步：将此 CID 作为 evidenceCid 传入合约 reportMisbehavior()");

    return result;
}

/**
 * 通过 CID 拉取并做 SHA256 校验
 */
async function fetchAndVerify(cid) {
    if (!cid) {
        console.error("请提供 CID 参数: node scripts/ipfs.js fetch <CID>");
        process.exit(1);
    }

    console.log(`正在从 IPFS 拉取内容 (CID: ${cid})...`);
    const response = await pinata.gateways.public.get(cid);
    const content = response.data;

    console.log("\n内容:");
    console.log(JSON.stringify(content, null, 2));

    const contentStr = typeof content === "string" ? content : JSON.stringify(content);
    const hash = crypto.createHash("sha256").update(contentStr).digest("hex");

    console.log(`\nSHA256: ${hash}`);
    return { content, hash };
}

const [command, arg] = process.argv.slice(2);

switch (command) {
    case "upload-metadata":
        uploadMetadata().catch(console.error);
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

用法:
  node scripts/ipfs.js upload-metadata
  node scripts/ipfs.js upload-evidence
  node scripts/ipfs.js fetch <CID>
        `);
}
