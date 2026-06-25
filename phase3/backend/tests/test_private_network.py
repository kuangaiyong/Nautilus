"""内网私有化改造回归测试。

覆盖两条核心改造：
  1. 结算币精度：华币(HUA)=18 位，旧 USDC/USDT=6 位的换算正确性。
  2. LLM 统一网关：openai 兼容协议真实 HTTP 往返 + Anthropic 风格 shim 结构。

遵循"真实验证、禁止 mock"：网关测试启动一个真实本地 OpenAI 兼容 HTTP 服务
（即私有大模型的形态），让 openai SDK 真实发请求、真实解析响应。
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


# ---------------------------------------------------------------------------
# 1. 结算币精度换算（华币 18 位）
# ---------------------------------------------------------------------------

def test_token_units_18_decimals_roundtrip():
    from blockchain.blockchain_service import to_token_units, from_token_units

    # 1000 华币 → 1000 * 10^18 最小单位
    raw = to_token_units(1000.0, decimals=18)
    assert raw == 1000 * 10**18
    # 反向换算回 1000.0
    assert from_token_units(raw, decimals=18) == 1000.0


def test_token_units_legacy_6_decimals_preserved():
    """旧 USDC/USDT 6 位精度路径未被破坏（向后兼容）。"""
    from blockchain.blockchain_service import to_token_units, from_token_units

    raw = to_token_units(1000.0, decimals=6)
    assert raw == 1000 * 10**6
    assert from_token_units(raw, decimals=6) == 1000.0


# ---------------------------------------------------------------------------
# 2. LLM 网关：真实本地 OpenAI 兼容服务往返
# ---------------------------------------------------------------------------

class _OpenAICompatHandler(BaseHTTPRequestHandler):
    """最小 OpenAI 兼容 /v1/chat/completions 实现：回显最后一条 user 消息。"""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        last_user = ""
        for m in messages:
            if m.get("role") == "user":
                last_user = m.get("content", "")
        reply = f"PONG:{last_user}"
        payload = json.dumps({
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "test-model"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture
def private_llm_server(monkeypatch):
    """启动真实本地 OpenAI 兼容服务并把网关指向它。"""
    server = HTTPServer(("127.0.0.1", 0), _OpenAICompatHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "private-model")

    # 重置网关单例，确保读取新 env
    import services.llm_gateway as gw
    gw._client = None

    yield f"http://127.0.0.1:{port}/v1"

    server.shutdown()
    gw._client = None


def test_gateway_chat_real_roundtrip(private_llm_server):
    """chat() 真实打到本地私有模型端点并取回回复。"""
    from services.llm_gateway import chat, is_configured, get_model

    assert is_configured() is True
    assert get_model() == "private-model"
    out = chat("ping", system="you are a test")
    assert out == "PONG:ping"


def test_gateway_anthropic_shim_structure(private_llm_server):
    """Anthropic 风格 shim：msg.content[0].text / .type=='text'，内部走私有模型。"""
    from services.llm_gateway import get_anthropic_compatible_client

    client = get_anthropic_compatible_client()
    msg = client.messages.create(
        model="claude-sonnet-4-6",  # 应被忽略，统一用私有模型
        max_tokens=64,
        system="sys",
        messages=[{"role": "user", "content": "hello"}],
    )
    assert msg.content[0].text == "PONG:hello"
    assert msg.content[0].type == "text"


def test_gateway_anthropic_shim_block_list_content(private_llm_server):
    """anthropic 允许 content 为 block 列表，shim 应归一为纯文本。"""
    from services.llm_gateway import get_anthropic_compatible_client

    client = get_anthropic_compatible_client()
    msg = client.messages.create(
        max_tokens=64,
        messages=[{"role": "user", "content": [{"type": "text", "text": "blocky"}]}],
    )
    assert msg.content[0].text == "PONG:blocky"


def test_gateway_shim_usage_and_stop_reason(private_llm_server):
    """回归：agent_executor._call_llm 访问 response.usage / .stop_reason，
    shim 必须提供这两个属性，否则会 AttributeError 导致核心链路恒回退。
    """
    from services.llm_gateway import get_anthropic_compatible_client

    client = get_anthropic_compatible_client()
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=64,
        system="sys", tools=[{"name": "x"}],  # tools 参数应被静默忽略
        messages=[{"role": "user", "content": "hi"}],
    )
    # 模拟 _call_llm 的访问模式，确保不抛 AttributeError
    tokens = (msg.usage.input_tokens + msg.usage.output_tokens) if msg.usage else 0
    assert tokens == 0
    assert msg.stop_reason == "end_turn"
    for block in (msg.content or []):
        if block.type == "text":
            assert block.text == "PONG:hi"
