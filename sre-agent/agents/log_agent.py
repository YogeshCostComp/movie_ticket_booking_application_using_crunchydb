"""Log Agent — Ephemeral agent for log analysis."""

import asyncio
from .base_agent import BaseAgent


class LogAgent(BaseAgent):
    AGENT_TYPE = "log_agent"
    AGENT_ICON = "📋"
    AGENT_DESCRIPTION = "Log Analysis Agent"

    async def execute(self, action: str, params: dict) -> dict:
        self.emit("📊 Fetching logs from IBM Cloud Logs", "running",
                  f"Action: {action}, Params: {params}")

        loop = asyncio.get_event_loop()

        if action == "get_error_logs":
            hours = params.get("hours", 24)
            limit = params.get("limit", 100)
            self.emit(f"🔍 Scanning error logs — last {hours}h", "running", f"Limit: {limit}")
            result = await loop.run_in_executor(None, lambda: self.mcp.get_error_logs(hours, limit))

        elif action == "get_recent_logs":
            limit = params.get("limit", 50)
            self.emit(f"📜 Fetching recent logs", "running", f"Limit: {limit}")
            result = await loop.run_in_executor(None, lambda: self.mcp.get_recent_logs(limit))

        elif action == "get_app_logs":
            hours = params.get("hours", 1)
            limit = params.get("limit", 50)
            self.emit(f"📱 Fetching app logs — last {hours}h", "running")
            result = await loop.run_in_executor(None, lambda: self.mcp.get_app_logs(hours, limit))

        elif action == "get_platform_logs":
            hours = params.get("hours", 1)
            limit = params.get("limit", 50)
            self.emit(f"☁️ Fetching platform logs — last {hours}h", "running")
            result = await loop.run_in_executor(None, lambda: self.mcp.get_platform_logs(hours, limit))

        elif action == "query_logs":
            query = params.get("query", "source logs")
            hours = params.get("hours", 1)
            limit = params.get("limit", 50)
            self.emit(f"🔎 Custom log query", "running", f"Query: {query}")
            result = await loop.run_in_executor(None, lambda: self.mcp.query_logs(query, hours, limit))

        else:
            # Default: get error logs
            self.emit("📊 Default: fetching error logs — last 24h", "running")
            result = await loop.run_in_executor(None, lambda: self.mcp.get_error_logs(24, 100))

        self.emit("📋 Log data retrieved", "completed",
                  f"Found {len(result.get('logs', []))} log entries" if isinstance(result, dict) else "")
        return result
