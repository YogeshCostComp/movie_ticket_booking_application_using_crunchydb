"""
MCP Client — Talks to the SRE MCP Server on IBM Cloud Code Engine.
Sends JSON-RPC 2.0 requests and also supports direct REST tool endpoints.
"""

import os
import json
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.environ.get(
    'MCP_SERVER_URL',
    'https://sre-mcp-server.260m2gai7zqb.us-south.codeengine.appdomain.cloud'
)
MCP_API_KEY = os.environ.get('MCP_API_KEY', 'sre-mcp-secret-key-2026')


class MCPClient:
    """Client for the SRE MCP Server on IBM Cloud Code Engine."""

    def __init__(self, base_url: str = None, api_key: str = None):
        self.base_url = (base_url or MCP_SERVER_URL).rstrip('/')
        self.api_key = api_key or MCP_API_KEY
        self._request_id = 0

    def _next_id(self):
        self._request_id += 1
        return self._request_id

    def _jsonrpc(self, method: str, params: dict = None, timeout: int = 120):
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or {}
        }
        headers = {"Content-Type": "application/json", "X-API-Key": self.api_key}
        try:
            resp = requests.post(f"{self.base_url}/mcp", json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                return {"error": data["error"]}
            return data.get("result", data)
        except requests.Timeout:
            return {"error": "MCP request timed out"}
        except Exception as e:
            return {"error": str(e)}

    def _rest(self, endpoint: str, payload: dict = None, timeout: int = 90):
        headers = {"Content-Type": "application/json", "X-API-Key": self.api_key}
        try:
            resp = requests.post(f"{self.base_url}{endpoint}", json=payload or {}, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def call_tool(self, tool_name: str, arguments: dict = None, timeout: int = 120):
        return self._jsonrpc("tools/call", {"name": tool_name, "arguments": arguments or {}}, timeout=timeout)

    def list_tools(self):
        return self._jsonrpc("tools/list")

    # ── Convenience methods ─────────────────────────────────────────
    def get_error_logs(self, hours=24, limit=100):
        return self._rest("/tools/get_error_logs", {"hours": hours, "limit": limit})

    def get_recent_logs(self, limit=50):
        return self._rest("/tools/get_recent_logs", {"limit": limit})

    def get_app_logs(self, hours=1, limit=50):
        return self.call_tool("get_app_logs", {"hours": hours, "limit": limit})

    def get_platform_logs(self, hours=1, limit=50):
        return self.call_tool("get_platform_logs", {"hours": hours, "limit": limit})

    def check_app_health(self):
        return self._rest("/tools/check_app_health")

    def check_database_health(self):
        return self._rest("/tools/check_database_health")

    def get_system_status(self):
        return self._rest("/tools/get_system_status")

    def get_recent_traces(self, limit=20):
        return self._rest("/tools/get_recent_traces", {"limit": limit})

    def get_trace_details(self, trace_id: str):
        return self._rest("/tools/get_trace_details", {"trace_id": trace_id})

    def get_trace_summary(self, hours=1):
        return self._rest("/tools/get_trace_summary", {"hours": hours})

    def get_seat_status(self):
        return self._rest("/tools/get_seat_status")

    def get_bookings(self):
        return self._rest("/tools/get_bookings")

    def query_logs(self, query: str, hours=1, limit=50):
        return self._rest("/tools/query_logs", {"query": query, "hours": hours, "limit": limit})

    def get_response_times(self, hours=1):
        return self.call_tool("get_response_times", {"hours": hours})

    def get_sre_dashboard(self):
        return self.call_tool("get_sre_dashboard")

    def get_failure_analysis(self, hours=24):
        return self.call_tool("get_failure_analysis", {"hours": hours})

    def get_deployment_history(self):
        return self.call_tool("get_deployment_history")

    def get_app_status(self):
        return self.call_tool("get_app_status")

    def start_monitoring(self, interval_minutes=2, webhook_url=""):
        return self._rest("/tools/start_monitoring", {"interval_minutes": interval_minutes, "teams_webhook_url": webhook_url})

    def stop_monitoring(self):
        return self._rest("/tools/stop_monitoring")

    def get_monitoring_status(self):
        return self._rest("/tools/get_monitoring_status")

    def start_runbook_monitoring(self, interval_minutes=5, webhook_url=""):
        return self._rest("/tools/start_runbook_monitoring", {"interval_minutes": interval_minutes, "teams_webhook_url": webhook_url})

    def stop_runbook_monitoring(self):
        return self._rest("/tools/stop_runbook_monitoring")

    def get_runbook_monitoring_status(self):
        return self._rest("/tools/get_runbook_monitoring_status")

    def stop_app(self):
        return self.call_tool("stop_app")

    def start_app(self):
        return self.call_tool("start_app")

    def restart_app(self):
        return self.call_tool("restart_app")

    def simulate_error(self, error_type="500"):
        return self._rest("/tools/simulate_error", {"error_type": error_type})

    def reset_bookings(self):
        return self._rest("/tools/reset_bookings")

    def ping(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/", timeout=10)
            return resp.status_code == 200
        except Exception:
            return False
