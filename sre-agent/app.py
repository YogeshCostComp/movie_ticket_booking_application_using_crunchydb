"""
SRE Agent Orchestrator — Main FastAPI application.

Routes user queries through LLM brain → spawns ephemeral agents → streams
pipeline events over WebSocket → returns formatted results.
"""

import os
import json
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mcp_client import MCPClient
from llm_brain import LLMBrain
from agent_registry import registry
from agents.log_agent import LogAgent
from agents.health_agent import HealthAgent
from agents.monitoring_agent import MonitoringAgent
from agents.runbook_agent import RunbookAgent
from agents.trace_agent import TraceAgent
from agents.dashboard_agent import DashboardAgent
from agents.deployment_agent import DeploymentAgent

# ── Config ──────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("sre-orchestrator")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="SRE Agent Orchestrator", version="1.0.0")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ── Shared instances ────────────────────────────────────────────────
mcp = MCPClient()
brain = LLMBrain()
AGENT_COOLDOWN_SECONDS = int(os.environ.get("AGENT_COOLDOWN_SECONDS", 120))

# Agent registry
AGENT_CLASSES = {
    "log_agent": LogAgent,
    "health_agent": HealthAgent,
    "monitoring_agent": MonitoringAgent,
    "runbook_agent": RunbookAgent,
    "trace_agent": TraceAgent,
    "dashboard_agent": DashboardAgent,
    "deployment_agent": DeploymentAgent,
}

# ── Connected WebSocket clients ─────────────────────────────────────
connected_clients: Dict[str, WebSocket] = {}

# ── Agent run history ───────────────────────────────────────────────
agent_history: List[dict] = []
MAX_HISTORY = 100

# ── Agent cooldown pool (keeps Python refs alive for demo) ──────
_cooldown_agents: Dict[str, object] = {}


async def delayed_agent_destruction(agent, agent_name: str):
    """Keep agent alive in registry for cooldown period, then destroy it."""
    agent_id = agent.agent_id
    try:
        await asyncio.sleep(AGENT_COOLDOWN_SECONDS)
    except asyncio.CancelledError:
        pass
    # Destroy the agent
    result = agent.result or {"status": "unknown"}
    registry.deregister(agent, result)
    _cooldown_agents.pop(agent_id, None)
    await broadcast_pipeline_event({
        "step": f"🗑️ Destroying {agent.AGENT_DESCRIPTION}",
        "status": "completed",
        "detail": f"Agent {agent_id} terminated after {AGENT_COOLDOWN_SECONDS}s cooldown",
        "agent_id": agent_id,
        "agent_type": agent_name,
        "timestamp": datetime.utcnow().isoformat()
    })
    logger.info("Agent %s destroyed after %ds cooldown", agent_id, AGENT_COOLDOWN_SECONDS)


# ── WebSocket manager ──────────────────────────────────────────────
async def broadcast_pipeline_event(event: dict):
    """Send pipeline event to all connected WebSocket clients."""
    message = json.dumps({"type": "pipeline_event", "data": event})
    dead = []
    for cid, ws in connected_clients.items():
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(cid)
    for cid in dead:
        connected_clients.pop(cid, None)


async def send_chat_response(ws: WebSocket, message: str, agent_type: str = "", session_id: str = ""):
    """Send a chat response to a specific client."""
    try:
        await ws.send_text(json.dumps({
            "type": "chat_response",
            "data": {
                "message": message,
                "agent_type": agent_type,
                "session_id": session_id,
                "timestamp": datetime.utcnow().isoformat()
            }
        }))
    except Exception as e:
        logger.error("Failed to send chat response: %s", e)


# ── Routes ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    mcp_reachable = mcp.ping()
    return {
        "status": "healthy",
        "mcp_server": "reachable" if mcp_reachable else "unreachable",
        "timestamp": datetime.utcnow().isoformat(),
        "agents_available": list(AGENT_CLASSES.keys()),
    }


@app.get("/api/history")
async def get_history():
    return {"history": agent_history[-50:]}


@app.get("/api/agents")
async def get_agents():
    return {
        "agents": [
            {
                "name": name,
                "type": cls.AGENT_TYPE,
                "icon": cls.AGENT_ICON,
                "description": cls.AGENT_DESCRIPTION
            }
            for name, cls in AGENT_CLASSES.items()
        ]
    }


# ── Agent Registry Inspection Endpoints ─────────────────────────────
@app.get("/api/agents/active")
async def get_active_agents():
    """See which agents are currently alive and executing right now."""
    return {"active_agents": registry.get_active(), "count": len(registry.get_active())}


@app.get("/api/agents/completed")
async def get_completed_agents(limit: int = 50):
    """Full audit trail of completed (destroyed) agents with proof."""
    completed = registry.get_completed(limit)
    return {"completed_agents": completed, "count": len(completed)}


@app.get("/api/agents/stats")
async def get_agent_stats():
    """High-level stats: total created, destroyed, active count."""
    return registry.get_stats()


@app.get("/api/agents/{agent_id}")
async def get_agent_detail(agent_id: str):
    """Inspect a specific agent by ID — full lifecycle with all events."""
    entry = registry.get_agent(agent_id)
    if not entry:
        return JSONResponse({"error": f"Agent {agent_id} not found"}, status_code=404)
    return entry


# ── Agent Inspector Page ────────────────────────────────────────
@app.get("/inspect/{agent_id}", response_class=HTMLResponse)
async def inspect_agent(request: Request, agent_id: str):
    """Agent inspection dashboard — live proof of agent lifecycle."""
    return templates.TemplateResponse("inspect.html", {
        "request": request,
        "agent_id": agent_id,
        "cooldown_seconds": AGENT_COOLDOWN_SECONDS
    })


# ── WebSocket endpoint ─────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    client_id = str(uuid.uuid4())[:8]
    connected_clients[client_id] = ws
    logger.info("Client %s connected. Total: %d", client_id, len(connected_clients))

    try:
        # Send welcome
        await ws.send_text(json.dumps({
            "type": "system",
            "data": {
                "message": "🤖 SRE Agent Orchestrator connected. Ask me anything about your application!",
                "client_id": client_id,
                "agents": list(AGENT_CLASSES.keys())
            }
        }))

        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            user_message = data.get("message", "").strip()

            if not user_message:
                continue

            session_id = str(uuid.uuid4())[:8]
            logger.info("[%s] User: %s", session_id, user_message)

            # ── Pipeline: Step 1 — Classify intent ──────────────
            await broadcast_pipeline_event({
                "step": "🧠 Analyzing user query",
                "status": "running",
                "detail": user_message[:80],
                "agent_id": session_id,
                "agent_type": "orchestrator",
                "timestamp": datetime.utcnow().isoformat()
            })

            intent = await asyncio.get_event_loop().run_in_executor(
                None, lambda: brain.classify_intent(user_message)
            )

            agent_name = intent.get("agent", "health_agent")
            action = intent.get("action", "check_all")
            params = intent.get("params", {})
            reasoning = intent.get("reasoning", "")

            await broadcast_pipeline_event({
                "step": "🧠 Intent classified",
                "status": "completed",
                "detail": f"Agent: {agent_name} | Action: {action} | {reasoning}",
                "agent_id": session_id,
                "agent_type": "orchestrator",
                "timestamp": datetime.utcnow().isoformat()
            })

            # ── Pipeline: Step 2 — Spawn ephemeral agent ────────
            agent_cls = AGENT_CLASSES.get(agent_name)
            if not agent_cls:
                await send_chat_response(ws, f"❌ Unknown agent: {agent_name}", "", session_id)
                continue

            # Create event callback that broadcasts pipeline events
            def make_callback():
                loop = asyncio.get_event_loop()
                def cb(event):
                    asyncio.run_coroutine_threadsafe(broadcast_pipeline_event(event), loop)
                return cb

            agent = agent_cls(mcp_client=mcp, event_callback=make_callback())

            # ── Pipeline: Step 3 — Execute ──────────────────────
            result = await agent.run(action, params)

            # ── Pipeline: Step 3.5 — Keep agent alive for inspection ─
            _cooldown_agents[agent.agent_id] = agent
            await broadcast_pipeline_event({
                "step": "🔗 Agent available for inspection",
                "status": "completed",
                "detail": f"Inspect agent {agent.agent_id} before auto-destruction",
                "inspect_url": f"/inspect/{agent.agent_id}",
                "agent_id": agent.agent_id,
                "agent_type": agent_name,
                "cooldown_remaining": AGENT_COOLDOWN_SECONDS,
                "timestamp": datetime.utcnow().isoformat()
            })
            asyncio.create_task(delayed_agent_destruction(agent, agent_name))

            # ── Pipeline: Step 4 — Format response with LLM ────
            await broadcast_pipeline_event({
                "step": "🧠 Formatting response with AI",
                "status": "running",
                "detail": "Claude is summarizing the results",
                "agent_id": agent.agent_id,
                "agent_type": agent_name,
                "timestamp": datetime.utcnow().isoformat()
            })

            formatted = await asyncio.get_event_loop().run_in_executor(
                None, lambda: brain.format_response(agent_name, action, result.get("data", result))
            )

            await broadcast_pipeline_event({
                "step": "🧠 Response formatted",
                "status": "completed",
                "detail": f"{len(formatted)} chars",
                "agent_id": agent.agent_id,
                "agent_type": agent_name,
                "timestamp": datetime.utcnow().isoformat()
            })

            # ── Send response to chat ───────────────────────────
            await send_chat_response(ws, formatted, agent_name, session_id)

            # ── Record history ──────────────────────────────────
            run_record = {
                "session_id": session_id,
                "user_query": user_message,
                "agent": agent_name,
                "action": action,
                "agent_id": agent.agent_id,
                "status": result.get("status", "unknown"),
                "duration": result.get("duration_seconds"),
                "timestamp": datetime.utcnow().isoformat()
            }
            agent_history.append(run_record)
            if len(agent_history) > MAX_HISTORY:
                agent_history.pop(0)

            # ── Final pipeline event ────────────────────────────
            await broadcast_pipeline_event({
                "step": "✨ Request complete",
                "status": "completed",
                "detail": f"Session {session_id} | {result.get('duration_seconds', 0):.1f}s",
                "agent_id": session_id,
                "agent_type": "orchestrator",
                "timestamp": datetime.utcnow().isoformat()
            })

    except WebSocketDisconnect:
        logger.info("Client %s disconnected", client_id)
    except Exception as e:
        logger.error("WebSocket error for %s: %s", client_id, e)
    finally:
        connected_clients.pop(client_id, None)


# ── Quick POST endpoint (for non-WebSocket clients) ────────────────
@app.post("/api/query")
async def query(request: Request):
    """REST alternative to WebSocket for simple queries."""
    body = await request.json()
    user_message = body.get("message", "")
    if not user_message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    intent = brain.classify_intent(user_message)
    agent_name = intent.get("agent", "health_agent")
    action = intent.get("action", "check_all")
    params = intent.get("params", {})

    agent_cls = AGENT_CLASSES.get(agent_name)
    if not agent_cls:
        return JSONResponse({"error": f"Unknown agent: {agent_name}"}, status_code=400)

    agent = agent_cls(mcp_client=mcp)
    result = await agent.run(action, params)
    formatted = brain.format_response(agent_name, action, result.get("data", result))

    return {
        "agent": agent_name,
        "action": action,
        "reasoning": intent.get("reasoning", ""),
        "result": formatted,
        "raw": result,
        "timestamp": datetime.utcnow().isoformat()
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
