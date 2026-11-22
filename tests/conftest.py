"""Stubbed model, FoodDash agent and Ironclad policy search. bastion, Anvil
and Agent Forge are real."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import httpx
import pytest
from bastion.classifier import is_classifier_request
from openai.types.chat import ChatCompletion

HUB_FOODDASH_KEY = "ak_test_issued_by_fooddash_to_chathub"
HUB_POLICY_KEY = "pk_test_issued_by_ironclad_to_chathub"
BANK_AGENT_KEY = "pk_test_ironclad_banking_agent"  # Ironclad's own: must never be sent
INTERNAL_KEY = "pk_test_internal"

os.environ.setdefault("OPENAI_API_KEY", "dummy-key-for-tests")
os.environ["CHATHUB_FOODDASH_KEY"] = HUB_FOODDASH_KEY
os.environ["CHATHUB_IRONCLAD_POLICY_KEY"] = HUB_POLICY_KEY
os.environ["IRONCLAD_POLICY_KEY"] = BANK_AGENT_KEY
for var in ("OPENAI_MODEL", "BASTION_URL", "BASTION_KEY", "FOODDASH_AGENT_URL", "IRONCLAD_BASE_URL",
            "BASTION_DASHBOARD_URL", "BASTION_DASHBOARD_KEY"):
    os.environ.pop(var, None)


def _completion(message: dict, finish: str) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "c", "object": "chat.completion", "created": 0, "model": "stub",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


def _role(m):
    return m.get("role") if isinstance(m, dict) else getattr(m, "role", None)


def _content(m):
    return m.get("content") if isinstance(m, dict) else getattr(m, "content", None)


class StubLLM:
    """plans: user message -> steps ({"tool", "args"} or {"text"})."""

    def __init__(self):
        self.plans: dict[str, list[dict]] = {}
        self.requests: list[list] = []
        self.flag_words: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        msgs = kwargs["messages"]
        if is_classifier_request(msgs):
            text = (_content(msgs[-1]) or "").lower()
            hit = next((w for w in self.flag_words if w in text), None)
            verdict = {"injection": True, "pattern": f"says {hit}"} if hit else {"injection": False}
            return _completion({"role": "assistant", "content": json.dumps(verdict)}, "stop")
        self.requests.append(msgs)
        last_user = max(i for i, m in enumerate(msgs) if _role(m) == "user")
        done = sum(1 for m in msgs[last_user + 1:] if _role(m) == "assistant")
        step = self.plans[_content(msgs[last_user])][done]
        if "tool" in step:
            return _completion({"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{done}", "type": "function",
                "function": {"name": step["tool"], "arguments": json.dumps(step.get("args", {}))},
            }]}, "tool_calls")
        return _completion({"role": "assistant", "content": step["text"]}, "stop")

    def tool_results_seen(self, user_text: str) -> list[dict]:
        out = []
        for msgs in self.requests:
            idx = [i for i, m in enumerate(msgs) if _role(m) == "user" and _content(m) == user_text]
            if idx:
                out += [json.loads(m["content"]) for m in msgs[idx[-1] + 1:] if _role(m) == "tool"]
        return out


PUBLIC = {"doc_slug": "transfer-limits", "doc_title": "Transfer Limits", "section": "Transfers to other people",
          "effective_date": "2026-10-01", "content": "Transfers to another person are limited to $10,000.00 per day."}
INTERNAL = {"doc_slug": "fraud-monitoring-thresholds", "doc_title": "Fraud Monitoring Thresholds",
            "section": "Review triggers", "effective_date": "2026-11-01",
            "content": "Transfers over $2,000 to a new recipient are held for fraud review."}


class StubApps:
    """FoodDash's /agent/chat on :8100 and Ironclad's /policies/search on :8001."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.food_reply = "Golden Dragon, Pasta Palace and Taco Fiesta are open."

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        port, path = request.url.port, request.url.path
        if port == 8100 and path == "/agent/chat":
            body = json.loads(request.content)
            return httpx.Response(200, json={"response": self.food_reply, "session_id": body["session_id"],
                                             "trace_id": "fooddash-child-trace"})
        if port == 8001 and path == "/policies/search":
            key = request.headers.get("authorization", "").removeprefix("Bearer ")
            levels = {HUB_POLICY_KEY: ["public"], BANK_AGENT_KEY: ["public"], INTERNAL_KEY: ["public", "internal"]}
            if key not in levels:
                return httpx.Response(401, json={"error": "POLICY_KEY_INVALID", "message": "Unknown policy key."})
            results = [dict(PUBLIC, score=0.8)] + ([dict(INTERNAL, score=0.7)] if "internal" in levels[key] else [])
            return httpx.Response(200, json={"query": request.url.params["q"], "min_score": 0.43, "results": results})
        return httpx.Response(404, json={"detail": f"stub has no {port}{path}"})

    def to(self, port):
        return [r for r in self.requests if r.url.port == port]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_FORGE_DB_PATH", str(tmp_path / "forge.db"))
    monkeypatch.chdir(tmp_path)
    yield
    from agent_forge.tools import _REGISTRY, sources

    for name in [n for n, fn in _REGISTRY.items() if getattr(fn, "_tool_source", None) is not None]:
        _REGISTRY.pop(name)
    sources._SOURCE_TOOLS.clear()


@pytest.fixture
async def service():
    import app.main as main

    llm, apps = StubLLM(), StubApps()
    platform = main.build_platform(openai_client=llm, http_transport=httpx.MockTransport(apps), sinks=[])
    await platform.start()
    main.app.state.platform = platform
    main.app.state.turns = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://hub") as client:
        yield client, llm, apps
    await platform.close()
