"""
Agent Registry — Tracks every ephemeral agent's full lifecycle.

Provides proof that agents are created, executed, and destroyed with:
- Unique IDs, Python object IDs (memory address), thread info
- Precise creation/completion timestamps
- Full execution audit trail with every pipeline event recorded
"""

import os
import threading
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Central registry that tracks all ephemeral agent lifecycles."""

    def __init__(self, max_completed: int = 200):
        self._active: Dict[str, dict] = {}       # agent_id → metadata
        self._completed: List[dict] = []          # finished agents (FIFO)
        self._max_completed = max_completed
        self._lock = threading.Lock()
        self._total_created = 0
        self._total_destroyed = 0

    def register(self, agent) -> dict:
        """Register a newly created agent. Returns the registry entry."""
        with self._lock:
            self._total_created += 1
            entry = {
                "agent_id": agent.agent_id,
                "agent_type": agent.AGENT_TYPE,
                "agent_icon": agent.AGENT_ICON,
                "description": agent.AGENT_DESCRIPTION,
                "python_object_id": id(agent),           # memory address proof
                "python_class": agent.__class__.__name__,
                "thread_id": threading.current_thread().ident,
                "thread_name": threading.current_thread().name,
                "process_id": os.getpid(),
                "created_at": agent.created_at.isoformat(),
                "status": "active",
                "action": None,
                "params": None,
                "completed_at": None,
                "duration_seconds": None,
                "result_status": None,
                "result_size_bytes": None,
                "events": [],                            # audit trail
                "seq": self._total_created,
            }
            self._active[agent.agent_id] = entry
            logger.info(
                "AGENT CREATED: %s [%s] obj@%s pid=%s",
                agent.agent_id, agent.AGENT_TYPE, hex(id(agent)), os.getpid()
            )
            return entry

    def record_event(self, agent_id: str, event: dict):
        """Add a pipeline event to an agent's audit trail."""
        with self._lock:
            entry = self._active.get(agent_id)
            if entry:
                entry["events"].append(event)

    def update_action(self, agent_id: str, action: str, params: dict):
        """Record what action/params the agent is executing."""
        with self._lock:
            entry = self._active.get(agent_id)
            if entry:
                entry["action"] = action
                entry["params"] = params
                entry["status"] = "executing"

    def deregister(self, agent, result: dict) -> Optional[dict]:
        """Deregister an agent after it completes. Moves to completed list."""
        with self._lock:
            entry = self._active.pop(agent.agent_id, None)
            if not entry:
                return None
            self._total_destroyed += 1
            completed_at = datetime.utcnow()
            entry["completed_at"] = completed_at.isoformat()
            entry["status"] = "destroyed"
            entry["duration_seconds"] = (
                completed_at - agent.created_at
            ).total_seconds()
            entry["result_status"] = result.get("status", "unknown")
            entry["result_size_bytes"] = len(str(result))
            self._completed.append(entry)
            if len(self._completed) > self._max_completed:
                self._completed.pop(0)
            logger.info(
                "AGENT DESTROYED: %s [%s] duration=%.1fs status=%s",
                agent.agent_id, agent.AGENT_TYPE,
                entry["duration_seconds"], entry["result_status"]
            )
            return entry

    # ── Query methods ───────────────────────────────────────────────
    def get_active(self) -> List[dict]:
        with self._lock:
            return list(self._active.values())

    def get_completed(self, limit: int = 50) -> List[dict]:
        with self._lock:
            return list(reversed(self._completed[-limit:]))

    def get_agent(self, agent_id: str) -> Optional[dict]:
        with self._lock:
            if agent_id in self._active:
                return self._active[agent_id]
            for entry in reversed(self._completed):
                if entry["agent_id"] == agent_id:
                    return entry
            return None

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "total_created": self._total_created,
                "total_destroyed": self._total_destroyed,
                "currently_active": len(self._active),
                "completed_in_history": len(self._completed),
                "active_agents": [
                    {
                        "agent_id": e["agent_id"],
                        "type": e["agent_type"],
                        "icon": e["agent_icon"],
                        "status": e["status"],
                        "created_at": e["created_at"],
                        "action": e["action"],
                    }
                    for e in self._active.values()
                ]
            }


# ── Singleton ───────────────────────────────────────────────────────
registry = AgentRegistry()
