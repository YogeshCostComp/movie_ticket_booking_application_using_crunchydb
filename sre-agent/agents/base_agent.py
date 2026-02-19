"""
Base Agent — Abstract class for all ephemeral SRE agents.
Each agent gets created, executes its task, emits pipeline events, and dies.
"""

import uuid
import logging
from datetime import datetime
from abc import ABC, abstractmethod
from typing import Callable, Optional
from agent_registry import registry

logger = logging.getLogger(__name__)


class PipelineEvent:
    """Represents a step in the agent's execution pipeline."""

    def __init__(self, step: str, status: str = "running", detail: str = "", agent_id: str = "", agent_type: str = ""):
        self.id = str(uuid.uuid4())[:8]
        self.step = step
        self.status = status          # pending | running | completed | error
        self.detail = detail
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self):
        return {
            "id": self.id,
            "step": self.step,
            "status": self.status,
            "detail": self.detail,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "timestamp": self.timestamp
        }


class BaseAgent(ABC):
    """Base class for all ephemeral SRE agents."""

    AGENT_TYPE = "base"
    AGENT_ICON = "🤖"
    AGENT_DESCRIPTION = "Base Agent"

    def __init__(self, mcp_client, event_callback: Optional[Callable] = None):
        self.agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        self.mcp = mcp_client
        self._emit = event_callback or (lambda e: None)
        self.created_at = datetime.utcnow()
        self.completed_at = None
        self.result = None
        registry.register(self)

    def emit(self, step: str, status: str = "running", detail: str = ""):
        event = PipelineEvent(
            step=step, status=status, detail=detail,
            agent_id=self.agent_id, agent_type=self.AGENT_TYPE
        )
        registry.record_event(self.agent_id, event.to_dict())
        self._emit(event.to_dict())
        return event

    async def run(self, action: str, params: dict) -> dict:
        """Execute the agent lifecycle: create → execute → (orchestrator handles destruction)."""
        registry.update_action(self.agent_id, action, params)
        self.emit("🔍 Analyzing request", "completed", f"Action: {action}")
        self.emit(f"{self.AGENT_ICON} Creating {self.AGENT_DESCRIPTION}", "running", f"ID: {self.agent_id}")

        try:
            self.emit(f"{self.AGENT_ICON} {self.AGENT_DESCRIPTION} active", "completed")
            self.emit("📡 Connecting to MCP Server", "running", "IBM Cloud Code Engine")

            # Execute the actual work
            result = await self.execute(action, params)
            self.result = result

            self.emit("📦 Processing results", "completed", f"Got {len(str(result))} bytes")
            self.completed_at = datetime.utcnow()
            duration = (self.completed_at - self.created_at).total_seconds()
            self.emit(f"✅ {self.AGENT_DESCRIPTION} completed", "completed", f"Duration: {duration:.1f}s")

            return {
                "status": "success",
                "agent_id": self.agent_id,
                "agent_type": self.AGENT_TYPE,
                "action": action,
                "duration_seconds": duration,
                "data": result
            }
        except Exception as e:
            logger.error("Agent %s failed: %s", self.agent_id, e)
            self.emit(f"❌ {self.AGENT_DESCRIPTION} failed", "error", str(e))
            return {
                "status": "error",
                "agent_id": self.agent_id,
                "agent_type": self.AGENT_TYPE,
                "action": action,
                "error": str(e)
            }

    @abstractmethod
    async def execute(self, action: str, params: dict) -> dict:
        """Override in subclasses to do actual work."""
        ...
