"""Dashboard Agent — Ephemeral agent for SRE dashboard and golden signals."""

import asyncio
from .base_agent import BaseAgent


class DashboardAgent(BaseAgent):
    AGENT_TYPE = "dashboard_agent"
    AGENT_ICON = "📊"
    AGENT_DESCRIPTION = "SRE Dashboard Agent"

    async def execute(self, action: str, params: dict) -> dict:
        loop = asyncio.get_event_loop()

        if action == "get_dashboard":
            self.emit("📊 Building SRE Dashboard", "running", "Collecting Golden Signals")

            self.emit("1️⃣ Latency — Fetching response times", "running")
            response_times = await loop.run_in_executor(
                None, lambda: self.mcp.get_response_times(params.get("hours", 1))
            )

            self.emit("2️⃣ Traffic — Checking system status", "running")
            system_status = await loop.run_in_executor(None, self.mcp.get_system_status)

            self.emit("3️⃣ Errors — Analyzing failures", "running")
            failures = await loop.run_in_executor(
                None, lambda: self.mcp.get_failure_analysis(params.get("hours", 24))
            )

            self.emit("4️⃣ Saturation — Fetching SRE dashboard", "running")
            dashboard = await loop.run_in_executor(None, self.mcp.get_sre_dashboard)

            self.emit("📊 Assembling dashboard", "completed")
            result = {
                "dashboard": dashboard,
                "response_times": response_times,
                "system_status": system_status,
                "failure_analysis": failures,
            }

        elif action == "get_response_times":
            hours = params.get("hours", 1)
            self.emit(f"⏱️ Fetching response time metrics — last {hours}h", "running")
            result = await loop.run_in_executor(
                None, lambda: self.mcp.get_response_times(hours)
            )

        elif action == "get_failure_analysis":
            hours = params.get("hours", 24)
            self.emit(f"❌ Analyzing failures — last {hours}h", "running")
            result = await loop.run_in_executor(
                None, lambda: self.mcp.get_failure_analysis(hours)
            )

        else:
            self.emit("📊 Default: building full SRE dashboard", "running")
            result = await loop.run_in_executor(None, self.mcp.get_sre_dashboard)

        return result
