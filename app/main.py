"""Chat Hub: a general assistant on the garrison SDK.

It can ask FoodDash's agent (agent to agent, with the key FoodDash issued
to the chat hub) and search Ironclad's public policies (with the public-only
key Ironclad issued to it). Both credentials are the chat hub's own; it never
holds another app's secret, an Ironclad merchant key, or a payment tool.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

load_dotenv()
_forge_env = os.path.join(os.path.dirname(__file__), "../../agent-forge/.env")  # local dev fallback
if os.path.exists(_forge_env):
    load_dotenv(_forge_env)

from garrison import Platform  # noqa: E402
from garrison.compat import getenv  # noqa: E402  (GARRISON_*, else the pre-rename names)

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("chathub")

HERE = Path(__file__).resolve().parent
CONFIG_PATH = os.environ.get("CHATHUB_CONFIG_PATH") or str(HERE.parent / "chathub.yaml")
APP = "chathub"
AGENT = "assistant"
AGENT_ID = f"{APP}.{AGENT}"
_URL_OVERRIDES = {"fooddash": "FOODDASH_AGENT_URL", "ironclad": "IRONCLAD_BASE_URL"}


def build_platform(**platform_kwargs: Any) -> Platform:
    """From the garrison control plane (GARRISON_URL and GARRISON_KEY) or the
    local chathub.yaml. In library mode, GARRISON_DASHBOARD_URL and
    GARRISON_DASHBOARD_KEY send traces to a garrison dashboard."""
    url, key = getenv("GARRISON_URL"), getenv("GARRISON_KEY")
    if url and key:
        platform = Platform.from_service(url, key, refresh_interval=float(getenv("GARRISON_REFRESH_SECONDS", "30")),
                                         **platform_kwargs)
        if platform.app != APP:
            raise RuntimeError(f"GARRISON_KEY belongs to app {platform.app!r}, not {APP!r}.")
        return platform
    with open(CONFIG_PATH) as f:
        raw = yaml.safe_load(f)
    for app, var in _URL_OVERRIDES.items():
        if os.getenv(var):
            raw["apps"][app]["base_url"] = os.environ[var].rstrip("/")
    if os.getenv("OPENAI_MODEL"):
        raw["agents"][AGENT_ID]["model"] = os.environ["OPENAI_MODEL"]
    if getenv("GARRISON_DASHBOARD_URL") and getenv("GARRISON_DASHBOARD_KEY"):
        raw["dashboard"] = {"url": getenv("GARRISON_DASHBOARD_URL"), "key": getenv("GARRISON_DASHBOARD_KEY")}
    return Platform.from_config(raw, app=APP, **platform_kwargs)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set: the assistant and Anvil's classifier need it.")
    platform = build_platform()
    await platform.start()
    app.state.platform = platform
    app.state.turns = {}
    logger.info("Chat Hub ready (agents=%s)", [a["name"] for a in platform.list_agents()])
    yield
    await platform.close()


app = FastAPI(title="Chat Hub", lifespan=lifespan)


def _platform() -> Platform:
    return app.state.platform


def _turns() -> dict[str, list[dict[str, Any]]]:
    if not hasattr(app.state, "turns"):
        app.state.turns = {}
    return app.state.turns


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=100)


class AppCall(BaseModel):
    app: str
    tool: str
    status: str | None = None
    violation: str | None = None
    trace_id: str | None = None  # the other app's trace, for agent-to-agent calls


class ChatResponse(BaseModel):
    response: str
    session_id: str
    trace_id: str
    calls: list[AppCall] = []


def _calls(trace: dict[str, Any]) -> list[AppCall]:
    out = []
    for c in trace.get("tool_calls") or []:
        child = c.get("child_trace") or {}
        out.append(AppCall(app=c.get("tool_app") or c["tool_id"].split(".", 1)[0], tool=c["tool"],
                           status=c.get("status"), violation=c.get("violation"), trace_id=child.get("trace_id")))
    return out


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def page():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/config")
def page_config():
    """What the chat page needs to link traces (no secrets)."""
    return {"dashboard_url": (getenv("GARRISON_DASHBOARD_UI") or getenv("GARRISON_DASHBOARD_URL") or "").rstrip("/")}


@app.post("/agent/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    session_id = req.session_id or f"hub_{uuid.uuid4().hex[:12]}"
    try:
        reply = await _platform().run_agent(AGENT, req.message, session_id=session_id)
    except Exception as exc:
        logger.exception("Agent error")
        raise HTTPException(status_code=502, detail=f"Agent error: {exc}") from exc
    calls = _calls(reply.trace)
    _turns().setdefault(session_id, []).append({"trace_id": reply.trace_id, "message": req.message,
                                                "calls": [c.model_dump() for c in calls]})
    return ChatResponse(response=reply.content, session_id=session_id, trace_id=reply.trace_id, calls=calls)


@app.get("/agent/traces/{session_id}")
def get_traces(session_id: str):
    """This session's turns: the chat hub's trace for each, and the apps it
    called (with their trace IDs for agent-to-agent calls)."""
    return {"session_id": session_id, "turns": _turns().get(session_id, [])}
