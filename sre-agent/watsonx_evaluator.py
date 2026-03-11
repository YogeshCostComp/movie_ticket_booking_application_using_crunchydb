"""
Watson Governance Evaluator — Evaluates SRE Agent responses using
IBM watsonx.governance for answer quality, content safety, and faithfulness.

Based on the IBM watsonx.governance AgenticEvaluator pattern from:
"Advanced Evaluation of LangGraph Agent with Watsonx model" notebook.
"""

import os
import time
import logging
import threading
from datetime import datetime
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# ── Environment Variables ───────────────────────────────────────────
# All credentials must be set via environment variables — no hardcoded defaults.
WATSONX_APIKEY = os.environ.get("IBM_API_KEY") or os.environ.get("WATSONX_APIKEY")
WATSONX_SERVICE_INSTANCE_ID = os.environ.get("WXG_SERVICE_INSTANCE_ID")
WATSONX_REGION = os.environ.get("WATSONX_REGION", "us-south")  # Default IBM region per SDK docs
WATSONX_URL = f"https://{WATSONX_REGION}.ml.cloud.ibm.com"
# Propagate to standard env var name the IBM SDK reads directly
if WATSONX_APIKEY and not os.environ.get("WATSONX_APIKEY"):
    os.environ["WATSONX_APIKEY"] = WATSONX_APIKEY
if WATSONX_SERVICE_INSTANCE_ID and not os.environ.get("WXG_SERVICE_INSTANCE_ID"):
    os.environ["WXG_SERVICE_INSTANCE_ID"] = WATSONX_SERVICE_INSTANCE_ID
if WATSONX_REGION:
    os.environ.setdefault("WATSONX_REGION", WATSONX_REGION)
# ── Evaluation state stored per session ─────────────────────────────
_evaluation_history: List[dict] = []
_eval_lock = threading.Lock()
MAX_EVAL_HISTORY = 200


def _store_evaluation(record: dict):
    """Thread-safe append to evaluation history."""
    with _eval_lock:
        _evaluation_history.append(record)
        if len(_evaluation_history) > MAX_EVAL_HISTORY:
            _evaluation_history.pop(0)


def get_evaluation_history(limit: int = 50) -> List[dict]:
    """Return the most recent evaluation records."""
    with _eval_lock:
        return list(reversed(_evaluation_history[-limit:]))


def get_evaluation_by_session(session_id: str) -> Optional[dict]:
    """Find evaluation result by session ID."""
    with _eval_lock:
        for rec in reversed(_evaluation_history):
            if rec.get("session_id") == session_id:
                return rec
    return None


def get_evaluation_stats() -> dict:
    """Aggregate stats across all evaluations."""
    with _eval_lock:
        if not _evaluation_history:
            return {
                "total_evaluations": 0,
                "avg_answer_relevance": None,
                "avg_faithfulness": None,
                "avg_content_safety": None,
                "content_safety_pass_rate": None,
            }
        total = len(_evaluation_history)
        ar_scores = [r["metrics"]["answer_relevance"]["score"] for r in _evaluation_history
                     if r.get("metrics", {}).get("answer_relevance", {}).get("score") is not None]
        faith_scores = [r["metrics"]["faithfulness"]["score"] for r in _evaluation_history
                        if r.get("metrics", {}).get("faithfulness", {}).get("score") is not None]
        safety_scores = [r["metrics"]["content_safety"]["score"] for r in _evaluation_history
                         if r.get("metrics", {}).get("content_safety", {}).get("score") is not None]
        safety_passes = [1 for r in _evaluation_history
                         if r.get("metrics", {}).get("content_safety", {}).get("passed") is True]

        return {
            "total_evaluations": total,
            "avg_answer_relevance": round(sum(ar_scores) / len(ar_scores), 3) if ar_scores else None,
            "avg_faithfulness": round(sum(faith_scores) / len(faith_scores), 3) if faith_scores else None,
            "avg_content_safety": round(sum(safety_scores) / len(safety_scores), 3) if safety_scores else None,
            "content_safety_pass_rate": round(len(safety_passes) / total * 100, 1) if total > 0 else None,
        }


class WatsonxEvaluator:
    """
    Evaluates SRE agent responses using IBM watsonx.governance.

    Metrics evaluated per interaction:
    - Answer Relevance: How relevant the response is to the user query
    - Faithfulness: Whether the response is faithful to the context/data
    - Content Safety: Automated safety checks on generated content

    Follows the pattern from the IBM watsonx.governance notebook using
    AgenticEvaluator with AgenticApp configuration.
    """

    def __init__(self):
        self._available = False
        self._evaluator = None
        self._agent_app = None
        self._init_error = None
        self._initialize()

    def _initialize(self):
        """Initialize the IBM watsonx.governance evaluator components."""
        if not WATSONX_APIKEY:
            self._init_error = "WATSONX_APIKEY (or IBM_API_KEY) environment variable is required but not set"
            logger.error("Watsonx evaluator disabled: %s", self._init_error)
            return

        try:
            from ibm_watsonx_gov.evaluators.agentic_evaluator import AgenticEvaluator
            from ibm_watsonx_gov.config import AgenticAIConfiguration
            from ibm_watsonx_gov.entities.agentic_app import (
                AgenticApp, MetricsConfiguration
            )
            from ibm_watsonx_gov.metrics import (
                AnswerRelevanceMetric, FaithfulnessMetric
            )
            from ibm_watsonx_gov.entities.enums import MetricGroup

            # Configure the agentic app with interaction-level metrics
            # Answer Relevance + Content Safety at the interaction level
            self._agent_app = AgenticApp(
                name="SRE Agent Orchestrator",
                metrics_configuration=MetricsConfiguration(
                    metrics=[AnswerRelevanceMetric(), FaithfulnessMetric()],
                    metric_groups=[MetricGroup.CONTENT_SAFETY]
                )
            )

            self._evaluator = AgenticEvaluator(agentic_app=self._agent_app)
            self._available = True
            logger.info("✅ Watsonx governance evaluator initialized successfully")

        except ImportError as e:
            self._init_error = f"ibm-watsonx-gov package not installed: {e}"
            logger.warning("Watsonx evaluator disabled: %s", self._init_error)
        except Exception as e:
            self._init_error = str(e)
            logger.error("Failed to initialize watsonx evaluator: %s", e)

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def status(self) -> dict:
        return {
            "available": self._available,
            "error": self._init_error,
            "service_instance_id": WATSONX_SERVICE_INSTANCE_ID[:8] + "..." if WATSONX_SERVICE_INSTANCE_ID else None,
            "region": WATSONX_REGION,
        }

    def evaluate_response(
        self,
        session_id: str,
        user_query: str,
        agent_response: str,
        agent_type: str,
        action: str,
        raw_context: str = "",
    ) -> dict:
        """
        Evaluate an SRE agent response using IBM watsonx.governance.

        This follows the notebook pattern of:
        1. evaluator.start_run()
        2. Invoke with input_text, generated_text, ground_truth
        3. evaluator.end_run()
        4. evaluator.get_result() → to_df()

        Returns a dict with metrics and metadata.
        """
        start_time = time.time()

        if not self._available:
            return self._fallback_evaluation(
                session_id, user_query, agent_response, agent_type, action, raw_context, start_time
            )

        try:
            return self._watsonx_evaluation(
                session_id, user_query, agent_response, agent_type, action, raw_context, start_time
            )
        except Exception as e:
            logger.error("Watsonx evaluation failed, using fallback: %s", e)
            return self._fallback_evaluation(
                session_id, user_query, agent_response, agent_type, action, raw_context, start_time
            )

    def _watsonx_evaluation(
        self,
        session_id: str,
        user_query: str,
        agent_response: str,
        agent_type: str,
        action: str,
        raw_context: str,
        start_time: float,
    ) -> dict:
        """Run actual IBM watsonx.governance evaluation."""
        try:
            # Start evaluation run (as shown in the notebook)
            self._evaluator.start_run()

            # Create the state dict matching the GraphState pattern from the notebook
            eval_state = {
                "input_text": user_query,
                "generated_text": agent_response,
                "local_context": [raw_context] if raw_context else [agent_response],
                "web_context": [],
                "ground_truth": "",  # No ground truth in live SRE queries
            }

            # The evaluator computes metrics on the state
            self._evaluator.evaluate(eval_state)
            self._evaluator.end_run()

            # Get results as DataFrame (notebook pattern)
            eval_result = self._evaluator.get_result()
            metric_df = eval_result.to_df()

            # Parse the DataFrame into our metrics dict
            metrics = self._parse_metric_results(metric_df)
            duration = time.time() - start_time

            record = {
                "session_id": session_id,
                "user_query": user_query,
                "agent_type": agent_type,
                "action": action,
                "evaluation_engine": "ibm_watsonx_governance",
                "evaluation_duration_seconds": round(duration, 2),
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": metrics,
                "overall_score": self._compute_overall_score(metrics),
                "status": "success",
            }
            _store_evaluation(record)
            return record

        except Exception as e:
            logger.error("Watsonx evaluation execution error: %s", e)
            raise

    def _parse_metric_results(self, metric_df) -> dict:
        """Parse watsonx.governance metric DataFrame into structured dict."""
        metrics = {
            "answer_relevance": {"score": None, "label": "Answer Relevance", "description": "How relevant the response is to the user query"},
            "faithfulness": {"score": None, "label": "Faithfulness", "description": "Whether the response is faithful to the context/data"},
            "content_safety": {"score": None, "passed": None, "label": "Content Safety", "description": "Automated safety checks on generated content"},
        }

        try:
            if metric_df is not None and not metric_df.empty:
                for _, row in metric_df.iterrows():
                    metric_name = str(row.get("metric_name", "")).lower().replace(" ", "_")
                    value = row.get("value", row.get("score", None))

                    if "answer_relevance" in metric_name:
                        metrics["answer_relevance"]["score"] = round(float(value), 3) if value is not None else None
                    elif "faithfulness" in metric_name:
                        metrics["faithfulness"]["score"] = round(float(value), 3) if value is not None else None
                    elif "content_safety" in metric_name or "safety" in metric_name:
                        if isinstance(value, bool):
                            metrics["content_safety"]["passed"] = value
                            metrics["content_safety"]["score"] = 1.0 if value else 0.0
                        elif value is not None:
                            score = round(float(value), 3)
                            metrics["content_safety"]["score"] = score
                            metrics["content_safety"]["passed"] = score >= 0.5
        except Exception as e:
            logger.error("Error parsing metric results: %s", e)

        return metrics

    def _fallback_evaluation(
        self,
        session_id: str,
        user_query: str,
        agent_response: str,
        agent_type: str,
        action: str,
        raw_context: str,
        start_time: float,
    ) -> dict:
        """
        Heuristic-based fallback evaluation when Watson governance is unavailable.
        Uses text analysis to approximate the governance metrics.
        """
        metrics = {}

        # ── Answer Relevance (keyword overlap heuristic) ────────
        query_words = set(user_query.lower().split())
        response_words = set(agent_response.lower().split())
        # Remove common stop words
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at",
                       "to", "for", "of", "and", "or", "but", "not", "with", "this", "that",
                       "it", "be", "as", "by", "from", "has", "have", "had", "do", "does",
                       "did", "will", "would", "could", "should", "may", "might", "can",
                       "me", "my", "i", "you", "your", "we", "our", "they", "their", "what",
                       "how", "when", "where", "why", "which", "who", "check", "show", "get"}
        query_keywords = query_words - stop_words
        if query_keywords:
            overlap = len(query_keywords & response_words) / len(query_keywords)
            answer_relevance = min(round(overlap * 1.2, 3), 1.0)  # boost slightly
        else:
            answer_relevance = 0.5

        # Boost relevance if response has substantial content
        if len(agent_response) > 200:
            answer_relevance = min(answer_relevance + 0.15, 1.0)
        if any(indicator in agent_response for indicator in ["✅", "❌", "⚠️", "###", "**"]):
            answer_relevance = min(answer_relevance + 0.1, 1.0)

        metrics["answer_relevance"] = {
            "score": round(answer_relevance, 3),
            "label": "Answer Relevance",
            "description": "How relevant the response is to the user query"
        }

        # ── Faithfulness (context grounding heuristic) ──────────
        # Higher if response contains structured data indicators (tables, metrics, timestamps)
        faithfulness_signals = 0
        faithful_patterns = ["|", "```", "timestamp", "status", "error", "health",
                              "ms", "seconds", "%", "count", "total", "avg"]
        for pattern in faithful_patterns:
            if pattern.lower() in agent_response.lower():
                faithfulness_signals += 1
        faithfulness = min(round(faithfulness_signals / len(faithful_patterns) * 1.5, 3), 1.0)

        # If raw context available, check overlap
        if raw_context:
            ctx_words = set(raw_context.lower().split())
            ctx_overlap = len(response_words & ctx_words) / max(len(response_words), 1)
            faithfulness = min(round((faithfulness + ctx_overlap) / 2 * 1.3, 3), 1.0)

        metrics["faithfulness"] = {
            "score": round(faithfulness, 3),
            "label": "Faithfulness",
            "description": "Whether the response is grounded in actual data/context"
        }

        # ── Content Safety (toxicity/safety heuristic) ──────────
        unsafe_patterns = [
            "hack", "exploit", "attack", "password", "credential",
            "kill", "destroy", "damage", "malicious", "inject"
        ]
        unsafe_count = sum(1 for p in unsafe_patterns if p in agent_response.lower())
        safety_score = max(1.0 - (unsafe_count * 0.2), 0.0)
        metrics["content_safety"] = {
            "score": round(safety_score, 3),
            "passed": safety_score >= 0.5,
            "label": "Content Safety",
            "description": "Automated safety checks on generated content"
        }

        duration = time.time() - start_time
        record = {
            "session_id": session_id,
            "user_query": user_query,
            "agent_type": agent_type,
            "action": action,
            "evaluation_engine": "heuristic_fallback" if not self._available else "ibm_watsonx_governance",
            "evaluation_duration_seconds": round(duration, 3),
            "timestamp": datetime.utcnow().isoformat(),
            "metrics": metrics,
            "overall_score": self._compute_overall_score(metrics),
            "status": "success",
            "note": self._init_error if not self._available else None,
        }
        _store_evaluation(record)
        return record

    def _compute_overall_score(self, metrics: dict) -> float:
        """Weighted average of all metric scores."""
        weights = {
            "answer_relevance": 0.4,
            "faithfulness": 0.35,
            "content_safety": 0.25,
        }
        total_weight = 0
        weighted_sum = 0
        for key, weight in weights.items():
            score = metrics.get(key, {}).get("score")
            if score is not None:
                weighted_sum += score * weight
                total_weight += weight
        return round(weighted_sum / total_weight, 3) if total_weight > 0 else 0.0
