import httpx
import json

raw_input = {
    "display_name": "Nguyen Van A",
    "university": "VKU",
    "major": "Computer Science",
    "student_year": 3,
    "gpa": 3.6,
    "current_courses": ["Algorithms", "Web Development", "Database Systems"],
    "skills": ["Python", "HTML/CSS", "SQL basics"],
    "weaknesses": ["public speaking", "advanced calculus"],
    "goals": ["become a full-stack engineer", "publish an academic paper"],
}

prompt = f"""You are an academic planning AI for Vietnamese university students.

CONTEXT:
{json.dumps(raw_input, indent=2)}

TASK:
Generate a personalized academic plan as a valid, minified JSON object matching this schema:
{{
  "student_summary": "A 1-2 sentence summary of academic profile/trajectory.",
  "skill_analysis": {{
    "strengths": ["list of strings"],
    "weaknesses": ["list of strings"],
    "recommended_focus": ["list of strings"]
  }},
  "weekly_study_plan": [
    {{
      "day": "Monday",
      "tasks": [
        {{"title": "task name", "type": "study|practice|review|project", "duration_min": 60, "priority": "high|medium|low"}}
      ]
    }}
  ],
  "academic_roadmap": [
    {{
      "title": "Course or milestone name",
      "status": "completed|in_progress|not_started",
      "progress_pct": 50,
      "ai_insight": "Why this matters."
    }}
  ],
  "metrics": {{
    "academic_progress": 50,
    "career_readiness": 50,
    "task_load": 50
  }}
}}

RULES:
- Return ONLY the JSON object. Do not include markdown codeblocks (e.g. ```json) or explanation.
- All five root keys must be present. Do not add any extra fields.
- metrics values: integers 0-100.
- duration_min: integers 1-480.
- type: study, practice, review, or project.
- priority: high, medium, or low.
- status: completed, in_progress, or not_started.
- weekly_study_plan should cover Monday through Sunday.
- Make all generated content specific and highly relevant to the student's profile."""

payload = {
    "model": "qwen3:14b",
    "prompt": prompt,
    "stream": False,
    "options": {
        "temperature": 0.1,
        "num_predict": 2048,
    },
}

resp = httpx.post('http://100.80.253.23:11434/api/generate', json=payload, timeout=300).json()
print("=== RESPONSE ===")
print(resp.get("response"))
print("=== THINKING ===")
print(resp.get("thinking"))
print("=== DONE REASON ===")
print(resp.get("done_reason"))
