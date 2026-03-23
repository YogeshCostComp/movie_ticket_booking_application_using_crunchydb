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


AUTONOMOUS_SYSTEM_PROMPT = """You are an Autonomous SRE Agent operating in a ReAct (Reason + Act) loop.
Your mission: fully resolve the user's SRE goal by chaining tool calls autonomously — one step at a time.

Available tools (agents):
  log_agent        — get_error_logs, get_recent_logs, search_logs
  health_agent     — check_all, check_app, check_database
  monitoring_agent — start, stop, status
  runbook_agent    — start, stop, status
  trace_agent      — get_recent_traces, get_slow_endpoints, get_trace_details
  dashboard_agent  — get_dashboard
  deployment_agent — check_status, get_deployment_history, restart_app, stop_app, start_app

At EVERY step respond ONLY with valid JSON — no markdown, no extra text.

FORMAT A — take a tool action:
{
  "type": "action",
  "thought": "Explain why you need this information and what you expect to learn",
  "agent": "<agent_name>",
  "action": "<action_name>",
  "params": { ... }
}

FORMAT B — final answer (when you have gathered enough data):
{
  "type": "final_answer",
  "thought": "Explain why no further tool calls are needed",
  "summary": "Comprehensive markdown report with all findings and recommendations"
}

Autonomous reasoning rules:
- ALWAYS include a "thought" field that shows your internal reasoning before acting.
- Chain agents intelligently: e.g. health check → if issues found → check logs → if errors → check traces.
- Do NOT repeat the same agent+action combination if you already have that result.
- Prefer targeted follow-up queries over re-running broad checks.
- Decide "final_answer" when: (a) the goal is clearly resolved, (b) you have enough evidence, OR (c) you have run 4+ steps.
- In your final_answer summary, always include: findings, root cause (if any), recommendations."""


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

    def autonomous_think(self, goal: str, observations: list) -> dict:
        """
        Autonomous ReAct step.

        Given the original user goal and a list of past observations
        (each with 'action_taken' and 'result'), returns either:
          {"type": "action",       "thought": ..., "agent": ..., "action": ..., "params": ...}
          {"type": "final_answer", "thought": ..., "summary": ...}
        """
        text = ""
        try:
            if not observations:
                messages = [{
                    "role": "user",
                    "content": (
                        f"User Goal: {goal}\n\n"
                        "This is your first step. Think about what information you need "
                        "to fully resolve this goal and choose the best first tool to call."
                    )
                }]
            else:
                messages = [{"role": "user", "content": f"User Goal: {goal}"}]
                for i, obs in enumerate(observations):
                    messages.append({
                        "role": "assistant",
                        "content": json.dumps(obs["action_taken"])
                    })
                    is_last = (i == len(observations) - 1)
                    suffix = (
                        "\n\nYou now have the above observations. "
                        "Reason carefully: is the goal fully resolved? "
                        "If yes, provide a final_answer. If not, what is the next best tool call?"
                        if is_last else ""
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Observation {i + 1}: "
                            f"{json.dumps(obs['result'], default=str)[:1500]}{suffix}"
                        )
                    })

            response = self.client.messages.create(
                model=self.model,
                max_tokens=800,
                system=AUTONOMOUS_SYSTEM_PROMPT,
                messages=messages
            )
            text = response.content[0].text.strip()
            # Strip potential markdown code fences
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse autonomous thought: %s | raw=%s", e, text[:300])
            return {
                "type": "final_answer",
                "thought": f"JSON parse error — {e}",
                "summary": f"Autonomous reasoning hit a parse error. Raw output:\n```\n{text[:400]}\n```"
            }
        except Exception as e:
            logger.error("autonomous_think failed: %s", e)
            return {
                "type": "final_answer",
                "thought": f"Unexpected error: {e}",
                "summary": f"Autonomous agent error: {str(e)}"
            }
