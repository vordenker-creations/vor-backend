"""Ollama-based AI academic plan generation service.

Reads configuration from environment variables:
  OLLAMA_BASE_URL       (default: http://100.80.253.23:11434)
  OLLAMA_MODEL          (default: qwen3:14b)
  OLLAMA_TIMEOUT_SECONDS (default: 60)
"""

import json
import logging
import os
import time
from typing import Any

import httpx
from pydantic import ValidationError

from core.schemas import AIGeneratedPlan

logger = logging.getLogger("ai_engine")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://100.80.253.23:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:14b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "4096"))

GENERATE_URL = f"{OLLAMA_BASE_URL}/api/generate"


def _build_prompt(raw_input: dict[str, Any]) -> str:
    """Build a structured prompt from the complete stored raw_input context."""
    raw_input_json = json.dumps(raw_input, ensure_ascii=True, indent=2, sort_keys=True)

    return f"""/no_think
You are a JSON generator for an academic planning app.
Return ONLY one valid JSON object. No markdown. No comments. No explanation.

CONTEXT:
{raw_input_json}

Hard requirements:
- Top-level keys exactly: student_summary, skill_analysis, weekly_study_plan, academic_roadmap, metrics.
- Do not return an empty object.
- skill_analysis must be an object with arrays: strengths, weaknesses, recommended_focus.
- weekly_study_plan must be an array of day objects. Each day object has day and tasks. tasks is an array.
- academic_roadmap must be an array of objects with title, status, progress_pct, ai_insight.
- metrics must contain integer values: academic_progress, career_readiness, task_load.
- Allowed task type values: study, practice, review, project.
- Allowed priority values: high, medium, low.
- Allowed roadmap status values: completed, in_progress, not_started.
- metrics values: integers 0-100.
- duration_min: integers 1-480.
- progress_pct: integers 0-100.

Return this exact shape with filled values:
{{
  "student_summary": "",
  "skill_analysis": {{
    "strengths": [],
    "weaknesses": [],
    "recommended_focus": []
  }},
  "weekly_study_plan": [
    {{
      "day": "Monday",
      "tasks": [
        {{"title": "", "type": "study", "duration_min": 60, "priority": "medium"}}
      ]
    }}
  ],
  "academic_roadmap": [
    {{
      "title": "",
      "status": "not_started",
      "progress_pct": 0,
      "ai_insight": ""
    }}
  ],
  "metrics": {{
    "academic_progress": 0,
    "career_readiness": 0,
    "task_load": 0
  }}
}}"""


class OllamaError(Exception):
    """Raised when Ollama communication or parsing fails."""
    pass


async def generate_academic_plan(raw_input: dict[str, Any]) -> AIGeneratedPlan:
    """Call Ollama and return a validated AIGeneratedPlan.

    Raises OllamaError on any failure (timeout, connection, bad JSON, schema mismatch).
    """
    prompt = _build_prompt(raw_input)
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0,
            "top_p": 0.8,
            "repeat_penalty": 1.05,
            "num_predict": OLLAMA_NUM_PREDICT,
        },
    }

    start_time = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            response = await client.post(GENERATE_URL, json=payload)
    except httpx.TimeoutException:
        duration = time.perf_counter() - start_time
        raise OllamaError(f"Ollama timed out after {OLLAMA_TIMEOUT}s (actual duration: {duration:.2f}s).")
    except httpx.ConnectError:
        raise OllamaError(f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. Check Tailscale.")
    except httpx.HTTPError as e:
        raise OllamaError(f"HTTP error communicating with Ollama: {e}")

    duration = time.perf_counter() - start_time

    if response.status_code != 200:
        raise OllamaError(f"Ollama returned HTTP {response.status_code}: {response.text[:500]} (duration: {duration:.2f}s)")

    # Extract the generated text from Ollama's response envelope
    try:
        envelope = response.json()
    except Exception:
        raise OllamaError(f"Ollama returned non-JSON response envelope: {response.text[:500]} (duration: {duration:.2f}s)")

    raw_text = envelope.get("response", "")
    done_reason = envelope.get("done_reason")
    raw_len = len(raw_text)

    # Logging stats
    logger.info(
        f"Ollama stats - Model: {OLLAMA_MODEL}, duration: {duration:.2f}s, "
        f"raw_len: {raw_len}, done_reason: {done_reason}"
    )

    if not raw_text.strip():
        raise OllamaError(f"Ollama returned an empty response body. done_reason: {done_reason}, duration: {duration:.2f}s")

    # If done_reason is limit, the response was truncated
    if done_reason == "limit":
        raise OllamaError(
            f"Ollama generation was truncated (reached token limit of {OLLAMA_NUM_PREDICT}). "
            f"done_reason: {done_reason}, raw_len: {raw_len}, duration: {duration:.2f}s. "
            f"Raw text start: {raw_text[:300]}"
        )

    # Parse the generated text as JSON.
    try:
        plan_dict = json.loads(raw_text)
    except json.JSONDecodeError as e:
        parse_err_cat = type(e).__name__
        # Determine if it looks truncated even if done_reason wasn't "limit"
        looks_truncated = not raw_text.strip().endswith("}")
        truncation_flag = " (looks truncated - no closing brace)" if looks_truncated else ""
        raise OllamaError(
            f"Ollama output is not valid JSON. Error: {e} ({parse_err_cat}){truncation_flag}. "
            f"raw_len: {raw_len}, done_reason: {done_reason}, duration: {duration:.2f}s. "
            f"Raw snippet: {raw_text[:200]} ... {raw_text[-200:] if raw_len > 400 else ''}"
        )

    # Validate against the strict Pydantic schema
    try:
        plan = AIGeneratedPlan.model_validate(plan_dict)
    except ValidationError as e:
        validation_errors = e.errors()
        err_details = "; ".join([
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']} ({err['type']})"
            for err in validation_errors
        ])
        raise OllamaError(
            f"Ollama JSON does not match schema. Errors: {err_details}. "
            f"raw_len: {raw_len}, done_reason: {done_reason}, duration: {duration:.2f}s. "
            f"Raw snippet: {raw_text[:300]}"
        )

    return plan
