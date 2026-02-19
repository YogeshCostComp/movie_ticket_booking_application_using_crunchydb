"""
LLM Brain — Uses Anthropic Claude to understand user intent,
pick the right agent, and format human-readable responses.
"""

import os
import json
import logging
from anthropic import Anthropic

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

SYSTEM_PROMPT = """You are an SRE Agent Orchestrator for a Movie Ticket Booking application.
Your job is to understand user queries and decide which specialized agent to spin up.

Available ephemeral agents:
1. **log_agent** — Analyze application logs, error logs, search for patterns
2. **health_agent** — Check app health, database health, system status
3. **monitoring_agent** — Start/stop continuous monitoring, check monitoring status
4. **runbook_agent** — Start/stop automated runbook monitoring with auto-restart
5. **trace_agent** — Analyze request traces, find slow endpoints, trace details
6. **dashboard_agent** — Generate SRE dashboard with golden signals, response times, failure analysis
7. **deployment_agent** — Check deployment history, app status, manage app lifecycle (start/stop/restart)

Given a user query, respond with a JSON object:
{
  "agent": "<agent_name>",
  "action": "<specific_action>",
  "params": { ... },
  "reasoning": "Brief explanation of why this agent was chosen"
}

Examples:
- "check logs for errors" → {"agent": "log_agent", "action": "get_error_logs", "params": {"hours": 24, "limit": 100}, "reasoning": "User wants to check error logs"}
- "is the app healthy?" → {"agent": "health_agent", "action": "check_all", "params": {}, "reasoning": "User wants a full health check"}
- "show me the SRE dashboard" → {"agent": "dashboard_agent", "action": "get_dashboard", "params": {}, "reasoning": "User wants the golden signals dashboard"}
- "start monitoring every 5 mins" → {"agent": "monitoring_agent", "action": "start", "params": {"interval_minutes": 5}, "reasoning": "User wants to start continuous monitoring"}
- "show recent traces" → {"agent": "trace_agent", "action": "get_recent_traces", "params": {"limit": 20}, "reasoning": "User wants to see recent request traces"}
- "restart the app" → {"agent": "deployment_agent", "action": "restart_app", "params": {}, "reasoning": "User wants to restart the application"}
- "enable runbook monitoring" → {"agent": "runbook_agent", "action": "start", "params": {"interval_minutes": 5}, "reasoning": "User wants automated runbook monitoring with auto-restart"}

Always respond with valid JSON only. No markdown, no extra text."""

RESPONSE_PROMPT = """You are an SRE Agent presenting results to a human operator.
Format the data into a clear, concise, human-readable response using markdown.
Include relevant metrics, timestamps, and status indicators (✅ ❌ ⚠️).
For logs, highlight errors and warnings.
For dashboards, present as organized tables.
For health checks, give a clear healthy/unhealthy verdict.
Keep it professional but easy to scan quickly.
If there are errors, suggest potential actions."""


class LLMBrain:
    """Anthropic Claude-based reasoning engine for the SRE orchestrator."""

    def __init__(self, api_key: str = None):
        self.client = Anthropic(api_key=api_key or ANTHROPIC_API_KEY)
        self.model = "claude-sonnet-4-20250514"

    def classify_intent(self, user_message: str) -> dict:
        """Determine which agent and action to invoke from a user message."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}]
            )
            text = response.content[0].text.strip()
            # Extract JSON from potential markdown wrapping
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM response: %s", e)
            return {
                "agent": "health_agent",
                "action": "check_all",
                "params": {},
                "reasoning": f"Could not parse intent, defaulting to health check. Raw: {text[:200]}"
            }
        except Exception as e:
            logger.error("LLM classification failed: %s", e)
            return {
                "agent": "health_agent",
                "action": "check_all",
                "params": {},
                "reasoning": f"LLM error: {str(e)}, defaulting to health check"
            }

    def format_response(self, agent_name: str, action: str, raw_data: dict) -> str:
        """Turn raw MCP tool output into a human-friendly markdown response."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=RESPONSE_PROMPT,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Agent: {agent_name}\n"
                        f"Action: {action}\n"
                        f"Raw Data:\n```json\n{json.dumps(raw_data, indent=2, default=str)[:4000]}\n```\n\n"
                        "Format this into a clear SRE report."
                    )
                }]
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error("LLM formatting failed: %s", e)
            return f"**Raw Result:**\n```json\n{json.dumps(raw_data, indent=2, default=str)[:2000]}\n```"
