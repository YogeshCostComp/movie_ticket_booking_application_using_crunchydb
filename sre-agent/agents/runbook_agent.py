"""Runbook Agent — Ephemeral agent for automated runbook monitoring (with auto-restart)."""

import asyncio
from .base_agent import BaseAgent


class RunbookAgent(BaseAgent):
    AGENT_TYPE = "runbook_agent"
    AGENT_ICON = "📕"
    AGENT_DESCRIPTION = "Runbook Automation Agent"

    async def execute(self, action: str, params: dict) -> dict:
        loop = asyncio.get_event_loop()

        if action == "start":
            interval = params.get("interval_minutes", 5)
            webhook = params.get("webhook_url", "")
            self.emit("📕 Activating Runbook RB-SRE-001", "running",
                      "Auto-restart on errors enabled")
            self.emit(f"⏱️ Setting check interval: {interval} min", "running")
            result = await loop.run_in_executor(
                None, lambda: self.mcp.start_runbook_monitoring(interval, webhook)
            )
            self.emit("📕 Runbook monitoring active", "completed", "Will auto-restart on detected errors")

        elif action == "stop":
            self.emit("⏹️ Stopping runbook monitoring", "running")
            result = await loop.run_in_executor(None, self.mcp.stop_runbook_monitoring)
            self.emit("📕 Runbook monitoring deactivated", "completed")

        elif action == "status":
            self.emit("📊 Checking runbook monitoring status", "running")
            result = await loop.run_in_executor(None, self.mcp.get_runbook_monitoring_status)
            active = result.get("active", result.get("monitoring_active", False))
            self.emit(f"📕 Runbook is {'ACTIVE' if active else 'INACTIVE'}", "completed")

        else:
            self.emit("📊 Fetching runbook status", "running")
            result = await loop.run_in_executor(None, self.mcp.get_runbook_monitoring_status)

        return result
