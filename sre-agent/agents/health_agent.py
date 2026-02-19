"""Health Agent — Ephemeral agent for health checks."""

import asyncio
from .base_agent import BaseAgent


class HealthAgent(BaseAgent):
    AGENT_TYPE = "health_agent"
    AGENT_ICON = "🏥"
    AGENT_DESCRIPTION = "Health Check Agent"

    async def execute(self, action: str, params: dict) -> dict:
        loop = asyncio.get_event_loop()

        if action == "check_app_health":
            self.emit("🌐 Checking application health", "running", "HTTP GET to app endpoint")
            result = await loop.run_in_executor(None, self.mcp.check_app_health)

        elif action == "check_database_health":
            self.emit("🗄️ Checking database health", "running", "PostgreSQL connection test")
            result = await loop.run_in_executor(None, self.mcp.check_database_health)

        elif action == "get_system_status":
            self.emit("📊 Fetching full system status", "running", "App + DB + Error scan")
            result = await loop.run_in_executor(None, self.mcp.get_system_status)

        elif action == "check_all":
            # Run all health checks
            self.emit("🌐 Checking application health", "running")
            app_health = await loop.run_in_executor(None, self.mcp.check_app_health)

            self.emit("🗄️ Checking database health", "running")
            db_health = await loop.run_in_executor(None, self.mcp.check_database_health)

            self.emit("📊 Fetching system status", "running")
            sys_status = await loop.run_in_executor(None, self.mcp.get_system_status)

            result = {
                "app_health": app_health,
                "database_health": db_health,
                "system_status": sys_status
            }
        else:
            self.emit("📊 Default: full system status", "running")
            result = await loop.run_in_executor(None, self.mcp.get_system_status)

        overall = "HEALTHY" if not result.get("error") else "ERROR"
        self.emit(f"{'✅' if overall == 'HEALTHY' else '❌'} Health check result: {overall}", "completed")
        return result
