"""Ollama-based AI academic plan generation service.

Reads configuration from environment variables:
  OLLAMA_BASE_URL       (default: http://100.80.253.23:11434)
  OLLAMA_MODEL          (default: qwen3:14b)
  OLLAMA_TIMEOUT_SECONDS (default: 60)
"""

import json
import logging
import os
import re
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


ROADMAP_STATUS_ALIASES = {
    "done": "completed",
    "complete": "completed",
    "completed": "completed",
    "finished": "completed",
    "todo": "not_started",
    "planned": "not_started",
    "pending": "not_started",
    "not_started": "not_started",
    "in_progress": "in_progress",
    "ongoing": "in_progress",
    "started": "in_progress",
}

TASK_PRIORITY_ALIASES = {
    "high": "high",
    "medium": "medium",
    "low": "low",
    "medium_priority": "medium",
    "high_priority": "high",
    "low_priority": "low",
    "critical": "high",
    "urgent": "high",
    "normal": "medium",
    "standard": "medium",
    "minor": "low",
}

TASK_TYPE_ALIASES = {
    "study": "study",
    "practice": "practice",
    "review": "review",
    "project": "project",
    "homework": "practice",
    "assignment": "practice",
    "exercise": "practice",
    "exam": "review",
    "quiz": "review",
    "test": "review",
    "revision": "review",
    "reading": "study",
    "lecture": "study",
    "lab": "practice",
    "coding": "practice",
}


def _extract_json_object(raw_text: str) -> str:
    # 1. Remove closed <think>...</think> and <reasoning>...</reasoning> blocks if present.
    # To prevent removing valid JSON after unclosed think tags, do NOT strip unclosed blocks.
    # JSON boundary extraction (finding first "{" and last "}") acts as the primary recovery mechanism.
    text = re.sub(r"(?is)<think>.*?</think>", "", raw_text)
    text = re.sub(r"(?is)<reasoning>.*?</reasoning>", "", text)
    
    # 2. Extract boundaries using safest rule: find first "{" and last "}"
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    
    if start_idx == -1 or end_idx == -1 or end_idx < start_idx:
        raise OllamaError(
            f"No valid JSON object boundaries found in raw text. (length: {len(raw_text)})"
        )
        
    return text[start_idx:end_idx + 1]


def _normalize_choice(value: Any, allowed: list[str], default: str, aliases: dict[str, str] = None) -> str:
    if not isinstance(value, str):
        if value is None:
            return default
        val = str(value).strip().lower()
    else:
        val = value.strip().lower()
        
    val = val.replace(" ", "_").replace("-", "_")
    
    if aliases and val in aliases:
        val = aliases[val]
        
    if val in allowed:
        return val
        
    # Conservative suffix check: e.g. medium_priority -> medium
    if val.endswith("_priority") and val[:-9] in allowed:
        return val[:-9]
        
    return default


def _clamp_int(value: Any, min_value: int, max_value: int, default: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default

    return max(min_value, min(max_value, value))


def _repair_plan_dict(plan_dict: dict[str, Any], json_extracted: bool) -> dict[str, Any]:
    if not isinstance(plan_dict, dict):
        return {}

    dropped_weekly_plan_items = 0
    dropped_weekly_tasks = 0
    dropped_roadmap_items = 0
    defaults_inserted = []

    # 1. Top-level defaults
    if "student_summary" not in plan_dict or not isinstance(plan_dict["student_summary"], str):
        plan_dict["student_summary"] = ""
        defaults_inserted.append("student_summary")

    # skill_analysis
    if "skill_analysis" not in plan_dict or not isinstance(plan_dict["skill_analysis"], dict):
        plan_dict["skill_analysis"] = {}
        defaults_inserted.append("skill_analysis")

    sa = plan_dict["skill_analysis"]
    for field in ["strengths", "weaknesses", "recommended_focus"]:
        if field not in sa or not isinstance(sa[field], list):
            sa[field] = []
            defaults_inserted.append(f"skill_analysis.{field}")

    # weekly_study_plan
    if "weekly_study_plan" not in plan_dict or not isinstance(plan_dict["weekly_study_plan"], list):
        plan_dict["weekly_study_plan"] = []
        defaults_inserted.append("weekly_study_plan")

    # academic_roadmap
    if "academic_roadmap" not in plan_dict or not isinstance(plan_dict["academic_roadmap"], list):
        plan_dict["academic_roadmap"] = []
        defaults_inserted.append("academic_roadmap")

    # metrics
    if "metrics" not in plan_dict or not isinstance(plan_dict["metrics"], dict):
        plan_dict["metrics"] = {}
        defaults_inserted.append("metrics")

    # 2. Repair weekly study plan
    weekly_plan = plan_dict["weekly_study_plan"]
    repaired_weekly_plan = []
    
    for day_item in weekly_plan:
        if not isinstance(day_item, dict):
            dropped_weekly_plan_items += 1
            continue
            
        if "day" not in day_item or not isinstance(day_item["day"], str):
            day_item["day"] = "Unscheduled"
            defaults_inserted.append("weekly_study_plan.day")
            
        if "tasks" not in day_item or not isinstance(day_item["tasks"], list):
            day_item["tasks"] = []
            defaults_inserted.append("weekly_study_plan.tasks")
            
        repaired_tasks = []
        for task_item in day_item["tasks"]:
            if not isinstance(task_item, dict):
                dropped_weekly_tasks += 1
                continue
                
            if "title" not in task_item or not isinstance(task_item["title"], str):
                task_item["title"] = "Study session"
                defaults_inserted.append("weekly_study_plan.tasks.title")
                
            task_item["type"] = _normalize_choice(
                task_item.get("type"),
                ["study", "practice", "review", "project"],
                "study",
                TASK_TYPE_ALIASES
            )
            
            task_item["duration_min"] = _clamp_int(
                task_item.get("duration_min"), 1, 480, 60
            )
            
            task_item["priority"] = _normalize_choice(
                task_item.get("priority"),
                ["high", "medium", "low"],
                "medium",
                TASK_PRIORITY_ALIASES
            )
            
            repaired_tasks.append(task_item)
            
        day_item["tasks"] = repaired_tasks
        repaired_weekly_plan.append(day_item)
        
    plan_dict["weekly_study_plan"] = repaired_weekly_plan

    # 3. Repair academic roadmap
    roadmap = plan_dict["academic_roadmap"]
    repaired_roadmap = []
    
    for item in roadmap:
        if not isinstance(item, dict):
            dropped_roadmap_items += 1
            continue
            
        if "title" not in item or not isinstance(item["title"], str):
            item["title"] = "Academic milestone"
            defaults_inserted.append("academic_roadmap.title")
            
        item["status"] = _normalize_choice(
            item.get("status"),
            ["completed", "in_progress", "not_started"],
            "not_started",
            ROADMAP_STATUS_ALIASES
        )
        
        item["progress_pct"] = _clamp_int(
            item.get("progress_pct"), 0, 100, 0
        )
        
        if "ai_insight" not in item or not isinstance(item["ai_insight"], str):
            item["ai_insight"] = ""
            defaults_inserted.append("academic_roadmap.ai_insight")
            
        repaired_roadmap.append(item)
        
    plan_dict["academic_roadmap"] = repaired_roadmap

    # 4. Repair metrics
    metrics = plan_dict["metrics"]
    metrics["academic_progress"] = _clamp_int(
        metrics.get("academic_progress"), 0, 100, 0
    )
    metrics["career_readiness"] = _clamp_int(
        metrics.get("career_readiness"), 0, 100, 0
    )
    metrics["task_load"] = _clamp_int(
        metrics.get("task_load"), 0, 100, 50
    )

    # 5. Log concise summary of repair actions
    if (json_extracted or dropped_weekly_plan_items > 0 or 
            dropped_weekly_tasks > 0 or dropped_roadmap_items > 0 or 
            defaults_inserted):
        logger.info(
            f"Ollama plan repaired - "
            f"json_extracted: {json_extracted}, "
            f"dropped_days: {dropped_weekly_plan_items}, "
            f"dropped_tasks: {dropped_weekly_tasks}, "
            f"dropped_roadmap_items: {dropped_roadmap_items}, "
            f"defaults_inserted: {', '.join(sorted(list(set(defaults_inserted)))) if defaults_inserted else 'none'}"
        )

    return plan_dict


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
            "repeat_penalty": 1.08,
            "num_predict": OLLAMA_NUM_PREDICT,
        },
    }

    start_time = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            response = await client.post(GENERATE_URL, json=payload)
    except httpx.TimeoutException:
        duration = time.perf_counter() - start_time
        raise OllamaError(
            f"Ollama timed out after {OLLAMA_TIMEOUT}s (actual duration: {duration:.2f}s)."
        )
    except httpx.ConnectError:
        raise OllamaError(
            f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. Check Tailscale."
        )
    except httpx.HTTPError as e:
        raise OllamaError(f"HTTP error communicating with Ollama: {e}")

    duration = time.perf_counter() - start_time

    if response.status_code != 200:
        raise OllamaError(
            f"Ollama returned HTTP {response.status_code}: {response.text[:500]} (duration: {duration:.2f}s)"
        )

    # Extract the generated text from Ollama response envelope
    try:
        envelope = response.json()
    except Exception:
        raise OllamaError(
            f"Ollama returned non-JSON response envelope: {response.text[:500]} (duration: {duration:.2f}s)"
        )

    raw_text = envelope.get("response", "")
    done_reason = envelope.get("done_reason")
    raw_len = len(raw_text)

    # Logging stats
    logger.info(
        f"Ollama stats - Model: {OLLAMA_MODEL}, duration: {duration:.2f}s, "
        f"raw_len: {raw_len}, done_reason: {done_reason}"
    )

    if not raw_text.strip():
        raise OllamaError(
            f"Ollama returned an empty response body. done_reason: {done_reason}, duration: {duration:.2f}s"
        )

    # If done_reason is limit, the response was truncated
    if done_reason == "limit":
        raise OllamaError(
            f"Ollama generation was truncated (reached token limit of {OLLAMA_NUM_PREDICT}). "
            f"done_reason: {done_reason}, raw_len: {raw_len}, duration: {duration:.2f}s. "
            f"Raw text start: {raw_text[:300]}"
        )

    # Extract the JSON object boundaries from response
    try:
        json_text = _extract_json_object(raw_text)
        json_extracted = (json_text.strip() != raw_text.strip())
    except OllamaError as e:
        raise OllamaError(
            f"Ollama output does not contain a JSON object. "
            f"raw_len: {raw_len}, done_reason: {done_reason}, duration: {duration:.2f}s. "
            f"Error: {str(e)}"
        )

    extracted_len = len(json_text)

    # Parse the extracted text as JSON.
    try:
        plan_dict = json.loads(json_text)
    except json.JSONDecodeError as e:
        parse_err_cat = type(e).__name__
        looks_truncated = not json_text.strip().endswith("}")
        truncation_flag = (
            " (looks truncated - no closing brace)" if looks_truncated else ""
        )
        raise OllamaError(
            f"Ollama output is not valid JSON. Error: {e} ({parse_err_cat}){truncation_flag}. "
            f"raw_len: {raw_len}, extracted_len: {extracted_len}, done_reason: {done_reason}, duration: {duration:.2f}s. "
            f"Raw snippet: {json_text[:200]} ... {json_text[-200:] if extracted_len > 400 else ''}"
        )

    # Repair common model mistakes before strict validation
    if not isinstance(plan_dict, dict):
        raise OllamaError(
            f"Ollama JSON root must be an object, got {type(plan_dict).__name__}. "
            f"raw_len: {raw_len}, extracted_len: {extracted_len}, done_reason: {done_reason}, duration: {duration:.2f}s."
        )

    plan_dict = _repair_plan_dict(plan_dict, json_extracted=json_extracted)

    # Validate against the strict Pydantic schema
    try:
        plan = AIGeneratedPlan.model_validate(plan_dict)
    except ValidationError as e:
        validation_errors = e.errors()
        err_details = "; ".join(
            [
                f"{'.'.join(str(loc) for loc in err.get('loc', ())) if err.get('loc') else ''}: {err.get('msg', '')} ({err.get('type', '')})"
                for err in validation_errors
            ]
        )
        raise OllamaError(
            f"Ollama JSON does not match schema. Errors: {err_details}. "
            f"raw_len: {raw_len}, extracted_len: {extracted_len}, done_reason: {done_reason}, duration: {duration:.2f}s. "
            f"Raw snippet: {json_text[:300]}"
        )

    return plan


