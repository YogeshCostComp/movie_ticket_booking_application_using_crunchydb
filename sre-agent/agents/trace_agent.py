"""Trace Agent — Ephemeral agent for distributed trace analysis."""

import asyncio
from .base_agent import BaseAgent


class TraceAgent(BaseAgent):
    AGENT_TYPE = "trace_agent"
    AGENT_ICON = "🔗"
    AGENT_DESCRIPTION = "Trace Analysis Agent"

    async def execute(self, action: str, params: dict) -> dict:
        loop = asyncio.get_event_loop()

        if action == "get_recent_traces":
            limit = params.get("limit", 20)
            self.emit(f"🔗 Fetching recent traces", "running", f"Limit: {limit}")
            result = await loop.run_in_executor(None, lambda: self.mcp.get_recent_traces(limit))

        elif action == "get_trace_details":
            trace_id = params.get("trace_id", "")
            if not trace_id:
                return {"error": "trace_id is required"}
            self.emit(f"🔍 Fetching trace details", "running", f"Trace: {trace_id[:16]}...")
            result = await loop.run_in_executor(None, lambda: self.mcp.get_trace_details(trace_id))

        elif action == "get_trace_summary":
            hours = params.get("hours", 1)
            self.emit(f"📊 Generating trace summary — last {hours}h", "running")
            result = await loop.run_in_executor(None, lambda: self.mcp.get_trace_summary(hours))

        else:
            self.emit("🔗 Default: fetching recent traces", "running")
            result = await loop.run_in_executor(None, lambda: self.mcp.get_recent_traces(20))

        trace_count = len(result.get("traces", [])) if isinstance(result, dict) else 0
        self.emit(f"🔗 Retrieved {trace_count} traces", "completed")
        return result
