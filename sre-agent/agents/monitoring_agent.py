"""Monitoring Agent — Ephemeral agent for continuous monitoring control."""

import asyncio
from .base_agent import BaseAgent


class MonitoringAgent(BaseAgent):
    AGENT_TYPE = "monitoring_agent"
    AGENT_ICON = "📡"
    AGENT_DESCRIPTION = "Monitoring Control Agent"

    async def execute(self, action: str, params: dict) -> dict:
        loop = asyncio.get_event_loop()

        if action == "start":
            interval = params.get("interval_minutes", 2)
            webhook = params.get("webhook_url", "")
            self.emit(f"▶️ Starting continuous monitoring", "running", f"Interval: {interval} min")
            result = await loop.run_in_executor(
                None, lambda: self.mcp.start_monitoring(interval, webhook)
            )
            self.emit("📡 Monitoring activated", "completed")

        elif action == "stop":
            self.emit("⏹️ Stopping continuous monitoring", "running")
            result = await loop.run_in_executor(None, self.mcp.stop_monitoring)
            self.emit("📡 Monitoring deactivated", "completed")

        elif action == "status":
            self.emit("📊 Checking monitoring status", "running")
            result = await loop.run_in_executor(None, self.mcp.get_monitoring_status)
            active = result.get("active", result.get("monitoring_active", False))
            self.emit(f"📡 Monitoring is {'ACTIVE' if active else 'INACTIVE'}", "completed")

        else:
            self.emit("📊 Fetching monitoring status", "running")
            result = await loop.run_in_executor(None, self.mcp.get_monitoring_status)

        return result
