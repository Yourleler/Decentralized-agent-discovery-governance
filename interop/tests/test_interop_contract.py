import sys
import unittest
from pathlib import Path

from flask import Flask

from interop.a2a_gateway import A2AGatewayService, create_a2a_blueprint
from interop.mcp_client_adapter import MCPServerRegistry
from interop.profile_adapter import build_agent_card, build_interop_profile
from interop.request_policy import (
    build_authorization_details,
    compute_authorization_details_hash,
    evaluate_tool_authorization,
    validate_request_envelope,
    with_request_envelope,
)


class FakeValidator:
    def verify_request_signature(self, text_payload, signature, claimed_did):
        return True, "ok"


class FakeRuntimeState:
    def __init__(self):
        self.records = []

    def append_interaction(self, **kwargs):
        self.records.append(kwargs)


class FakeMCPClient:
    def call_tool(self, name, arguments=None):
        return {"name": name, "arguments": arguments or {}, "content": "ok"}

    def close(self):
        return None


class FakeMCPRegistry:
    def create_client(self, server_id):
        return FakeMCPClient()


TOOLSET_VC = {
    "type": ["VerifiableCredential", "AgentToolsetCredential"],
    "credentialSubject": {
        "id": "did:ethr:sepolia:0xholder",
        "toolManifest": [
            {
                "name": "Web Search via MCP",
                "identifier": "tool.web.search",
                "providerProtocol": "mcp",
                "serverId": "demo-search",
                "serverEndpoint": "http://localhost:9300/mcp",
                "allowedActions": ["query"],
                "allowedResources": ["resource:web.search:*"],
                "permissions": "external-read",
                "rateLimit": "60/min",
                "riskLevel": "medium",
                "operationalStatus": "active",
            }
        ],
    },
}

FAKE_STDIO_SERVER = str(Path(__file__).with_name("fake_stdio_mcp_server.py"))


class RequestPolicyTests(unittest.TestCase):
    def test_validate_request_envelope_accepts_recent_request(self):
        payload = with_request_envelope(
            {"verifier_did": "did:ethr:sepolia:0xverifier"},
            resource="urn:dagg:holder:auth",
            action="authenticate",
            authorization_details=build_authorization_details(
                detail_type="vp_presentation",
                actions=["present"],
                locations=["http://localhost:5000"],
                datatypes=["AgentIdentityCredential"],
                identifier="holder-auth",
                privileges=["identity"],
            ),
        )
        ok, reason = validate_request_envelope(
            payload,
            expected_resource="urn:dagg:holder:auth",
            allowed_actions=["authenticate"],
        )
        self.assertTrue(ok, reason)

    def test_evaluate_tool_authorization_allows_matching_tool(self):
        decision = evaluate_tool_authorization(
            tool_identifier="tool.web.search",
            action="query",
            resource="resource:web.search:news",
            vcs=[TOOLSET_VC],
        )
        self.assertTrue(decision.allowed)

    def test_evaluate_tool_authorization_rejects_undeclared_action(self):
        decision = evaluate_tool_authorization(
            tool_identifier="tool.web.search",
            action="execute",
            resource="resource:web.search:news",
            vcs=[TOOLSET_VC],
        )
        self.assertFalse(decision.allowed)
        self.assertIn("动作未授权", decision.reason)

    def test_compute_authorization_details_hash_is_stable(self):
        details = build_authorization_details(
            detail_type="task-execution",
            actions=["execute"],
            locations=["http://localhost:5000"],
            datatypes=["text/plain"],
            identifier="task-1",
            privileges=["probe"],
        )
        hash_a = compute_authorization_details_hash(details)
        hash_b = compute_authorization_details_hash(dict(details))
        self.assertEqual(hash_a, hash_b)


class ProfileAdapterTests(unittest.TestCase):
    def test_build_agent_card_from_metadata(self):
        metadata = {
            "agentDid": "did:ethr:sepolia:0xholder",
            "service": {
                "name": "Demo Holder",
                "summary": "A2A compatible demo holder",
                "domain": "agent-runtime",
                "interactionModes": ["A2A_HTTP", "JSON_RPC"],
                "endpoints": [{"url": "http://localhost:5000"}],
            },
            "capabilities": [{"id": "cap.demo", "name": "Demo", "description": "demo capability", "inputs": ["query"]}],
            "vcManifest": {"types": ["AgentIdentityCredential", "AgentToolsetCredential"]},
            "interop": {
                "supportedProtocols": ["native", "a2a"],
                "a2aEndpoint": "http://localhost:5000/a2a",
                "supportedInteractionModes": ["A2A_HTTP", "JSON_RPC"],
                "authMode": "did-sig",
            },
        }
        profile = build_interop_profile(metadata)
        card = build_agent_card(profile)
        self.assertEqual(card["protocolVersion"], "0.2.5")
        self.assertEqual(card["extensions"]["agentDid"], "did:ethr:sepolia:0xholder")
        self.assertEqual(card["url"], "http://localhost:5000/a2a")


class MCPClientAdapterTests(unittest.TestCase):
    def test_registry_accepts_stdio_server_config(self):
        registry = MCPServerRegistry.from_dict(
            {
                "official-time": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["-u", FAKE_STDIO_SERVER],
                    "timeout_seconds": 5,
                }
            }
        )
        config = registry.get("official-time")
        self.assertIsNotNone(config)
        self.assertEqual(config.transport, "stdio")
        self.assertEqual(config.command, sys.executable)

    def test_stdio_client_can_list_tools_and_call_tool(self):
        registry = MCPServerRegistry.from_dict(
            {
                "official-time": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["-u", FAKE_STDIO_SERVER],
                    "timeout_seconds": 5,
                }
            }
        )
        client = registry.create_client("official-time")
        try:
            tools = client.list_tools()
            self.assertEqual(tools[0]["name"], "tool.time.now")
            result = client.call_tool("tool.time.now", {"timezone": "Asia/Shanghai"})
            self.assertEqual(result["content"][0]["text"], "fake-time::Asia/Shanghai")
        finally:
            client.close()

    def test_stdio_client_supports_legacy_server_without_initialize(self):
        registry = MCPServerRegistry.from_dict(
            {
                "official-time-legacy": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["-u", FAKE_STDIO_SERVER, "--legacy-no-init"],
                    "timeout_seconds": 5,
                }
            }
        )
        client = registry.create_client("official-time-legacy")
        try:
            tools = client.list_tools()
            self.assertEqual(len(tools), 1)
            result = client.call_tool("tool.time.now", {"timezone": "UTC"})
            self.assertEqual(result["content"][0]["text"], "fake-time::UTC")
        finally:
            client.close()


class A2AGatewayTests(unittest.TestCase):
    def setUp(self):
        metadata = {
            "agentDid": "did:ethr:sepolia:0xholder",
            "service": {
                "name": "Demo Holder",
                "summary": "A2A compatible demo holder",
                "domain": "agent-runtime",
                "interactionModes": ["A2A_HTTP", "JSON_RPC"],
                "endpoints": [{"url": "http://localhost:5000"}],
            },
            "capabilities": [{"id": "cap.demo", "name": "Demo", "description": "demo capability", "inputs": ["query"]}],
            "vcManifest": {"types": ["AgentIdentityCredential", "AgentToolsetCredential"]},
            "interop": {
                "supportedProtocols": ["native", "a2a"],
                "a2aEndpoint": "http://localhost:5000/a2a",
                "supportedInteractionModes": ["A2A_HTTP", "JSON_RPC"],
                "authMode": "did-sig",
            },
        }
        profile = build_interop_profile(metadata)
        self.runtime_state = FakeRuntimeState()
        self.service = A2AGatewayService(
            validator=FakeValidator(),
            runtime_state=self.runtime_state,
            holder_did="did:ethr:sepolia:0xholder",
            profile=profile,
            vcs_getter=lambda: [TOOLSET_VC],
            task_executor=lambda payload: {"mode": "native", "echo": payload.get("message")},
            mcp_registry=FakeMCPRegistry(),
        )
        app = Flask(__name__)
        app.register_blueprint(create_a2a_blueprint(self.service))
        self.client = app.test_client()

    def test_agent_card_route(self):
        response = self.client.get("/.well-known/agent-card.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["protocolVersion"], "0.2.5")

    def test_message_send_calls_mcp_tool(self):
        payload = with_request_envelope(
            {
                "senderDid": "did:ethr:sepolia:0xverifier",
                "senderSignature": "0xsig",
                "message": "search latest news",
                "toolCall": {
                    "providerProtocol": "mcp",
                    "serverId": "demo-search",
                    "toolName": "tool.web.search",
                    "arguments": {"query": "latest news"},
                },
            },
            resource="resource:web.search:news",
            action="query",
            authorization_details=build_authorization_details(
                detail_type="tool-access",
                actions=["query"],
                locations=["http://localhost:9300/mcp"],
                datatypes=["text/plain"],
                identifier="tool.web.search",
                privileges=["external-read"],
            ),
        )
        response = self.client.post("/a2a/message/send", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["result"]["mode"], "mcp")
        self.assertTrue(any(item["stage"] == "mcp_tool" for item in self.runtime_state.records))

    def test_message_send_rejects_unauthorized_tool(self):
        payload = with_request_envelope(
            {
                "senderDid": "did:ethr:sepolia:0xverifier",
                "senderSignature": "0xsig",
                "message": "run forbidden tool",
                "toolCall": {
                    "providerProtocol": "mcp",
                    "serverId": "demo-search",
                    "toolName": "tool.web.search",
                    "arguments": {"query": "latest news"},
                },
            },
            resource="resource:web.search:news",
            action="execute",
            authorization_details=build_authorization_details(
                detail_type="tool-access",
                actions=["execute"],
                locations=["http://localhost:9300/mcp"],
                datatypes=["text/plain"],
                identifier="tool.web.search",
                privileges=["external-read"],
            ),
        )
        response = self.client.post("/a2a/message/send", json=payload)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["status"], "failed")


if __name__ == "__main__":
    unittest.main()
