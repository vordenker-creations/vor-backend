import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

BASE_URL = "http://127.0.0.1:8000"


def request(method, path, payload=None, token=None, timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
        return exc.code, parsed
    except Exception as exc:
        return 999, str(exc)


# 5 representative student profiles
PROFILES = [
    {
        "display_name": "Nguyen Van A",
        "university": "VKU",
        "major": "Computer Science",
        "student_year": 3,
        "gpa": 3.6,
        "current_courses": ["Algorithms", "Web Development", "Database Systems"],
        "skills": ["Python", "HTML/CSS", "SQL basics"],
        "weaknesses": ["public speaking", "advanced calculus"],
        "goals": ["become a full-stack engineer", "publish an academic paper"],
    },
    {
        "display_name": "Tran Thi B",
        "university": "HUST",
        "major": "Data Science",
        "student_year": 2,
        "gpa": 3.2,
        "current_courses": ["Statistics", "Python for Data Science", "Linear Algebra"],
        "skills": ["Python", "Pandas", "SQL"],
        "weaknesses": ["communication skills", "time management"],
        "goals": ["secure a data analyst internship", "learn machine learning basics"],
    },
    {
        "display_name": "Le Van C",
        "university": "VKU",
        "major": "Software Engineering",
        "student_year": 4,
        "gpa": 3.8,
        "current_courses": ["Software Architecture", "Mobile Dev", "Project Management"],
        "skills": ["Java", "Kotlin", "Git", "Clean Architecture"],
        "weaknesses": ["system design", "no SQL databases"],
        "goals": ["get a junior mobile developer job", "pass English IELTS 6.5"],
    },
    {
        "display_name": "Pham Minh D",
        "university": "UET",
        "major": "Information Technology",
        "student_year": 1,
        "gpa": 2.9,
        "current_courses": ["Programming Foundations", "Discrete Math", "Physics 1"],
        "skills": ["C++ basics"],
        "weaknesses": ["problem solving logic", "physics lab"],
        "goals": ["build solid programming foundations", "improve GPA to 3.2 next semester"],
    },
    {
        "display_name": "Hoang Thi E",
        "university": "VKU",
        "major": "Cybersecurity",
        "student_year": 3,
        "gpa": 3.5,
        "current_courses": ["Cryptography", "Network Security", "Operating Systems"],
        "skills": ["Linux basics", "C", "Python for scripting"],
        "weaknesses": ["assembly language", "web application security"],
        "goals": ["learn penetration testing", "obtain a security certification"],
    }
]


def run_benchmark():
    print("====================================================")
    print("STARTING QWEN3:14B JSON GENERATION BENCHMARK (5 RUNS)")
    print("====================================================")
    
    results = []
    
    for idx, profile in enumerate(PROFILES, 1):
        print(f"\n--- RUN {idx}/5 for student: {profile['display_name']} ({profile['major']}) ---")
        
        unique = int(time.time()) + idx
        email = f"benchmark_qwen3_{unique}@example.com"
        password = "benchmark-password"
        
        # 1. Register
        print("Registering student...")
        status, registered = request(
            "POST",
            "/api/v1/auth/register",
            {
                "email": email,
                "password": password,
                "display_name": profile["display_name"],
                "major": profile["major"],
                "student_year": profile["student_year"],
            }
        )
        if status != 201:
            print(f"FAIL: Registration failed with status {status}: {registered}")
            results.append({"run": idx, "status": "REGISTRATION_FAILED", "error": str(registered)})
            continue
            
        token = registered["access_token"]
        student_id = registered["student_id"]
        
        # 2. Sync Profile Context
        print("Syncing academic context...")
        now = datetime.now(timezone.utc).isoformat()
        status, sync_result = request(
            "POST",
            "/api/v1/sync",
            {
                "students": [],
                "student_context": [
                    {
                        "id": str(uuid.uuid4()),
                        "student_id": student_id,
                        "raw_input": profile,
                        "ai_status": "EMPTY",
                        "updated_at": now,
                    }
                ],
            },
            token=token
        )
        if status != 200:
            print(f"FAIL: Profile sync failed with status {status}: {sync_result}")
            results.append({"run": idx, "status": "SYNC_FAILED", "error": str(sync_result)})
            continue
            
        # 3. Trigger AI generation
        print("Triggering AI academic plan generation...")
        generation_start_time = time.time()
        status, generated = request("POST", "/api/v1/ai/generate_academic_plan", {}, token=token)
        if status != 200:
            print(f"FAIL: Generation trigger failed with status {status}: {generated}")
            results.append({"run": idx, "status": "TRIGGER_FAILED", "error": str(generated)})
            continue
            
        # 4. Polling until terminal state
        print("Polling AI status...")
        poll_count = 0
        terminal_state = False
        duration = 0.0
        ai_last_error = None
        plan_data = None
        
        while not terminal_state:
            time.sleep(3)
            poll_count += 1
            status, poll_result = request("GET", "/api/v1/ai/academic_plan_status", token=token)
            
            if status != 200:
                print(f"Polling HTTP Error: {status} - {poll_result}")
                continue
                
            ai_status = poll_result.get("status")
            print(f"  Poll #{poll_count}: status = {ai_status}")
            
            if ai_status in ["COMPLETED", "FAILED"]:
                terminal_state = True
                duration = time.time() - generation_start_time
                ai_last_error = poll_result.get("ai_last_error")
                plan_data = poll_result.get("ai_plan")
                break
                
            if time.time() - generation_start_time > 300:
                print("FAIL: Timeout after 300 seconds.")
                duration = time.time() - generation_start_time
                ai_status = "TIMEOUT"
                terminal_state = True
                break
                
        # 5. Record result
        run_res = {
            "run": idx,
            "student": profile["display_name"],
            "major": profile["major"],
            "status": ai_status,
            "duration_sec": round(duration, 2),
            "ai_last_error": ai_last_error,
            "response_length": len(json.dumps(plan_data)) if plan_data else 0
        }
        results.append(run_res)
        print(f"Run {idx} finished. Status: {ai_status}, Duration: {duration:.2f}s")
        if ai_last_error:
            print(f"Error: {ai_last_error}")
            
    # Calculate statistics
    completed_runs = [r for r in results if r["status"] == "COMPLETED"]
    failed_runs = [r for r in results if r["status"] == "FAILED"]
    timeout_runs = [r for r in results if r["status"] == "TIMEOUT"]
    
    total_time = sum(r["duration_sec"] for r in completed_runs + failed_runs)
    num_ai_runs = len(completed_runs) + len(failed_runs)
    avg_duration = total_time / num_ai_runs if num_ai_runs > 0 else 0
    
    print("\n====================================================")
    print("BENCHMARK SUMMARY")
    print("====================================================")
    print(f"Total Runs: {len(results)}")
    print(f"COMPLETED: {len(completed_runs)}")
    print(f"FAILED: {len(failed_runs)}")
    print(f"TIMEOUT: {len(timeout_runs)}")
    print(f"Average Generation Time (excluding timeouts): {avg_duration:.2f}s")
    
    print("\nDetailed Results:")
    print(json.dumps(results, indent=2))
    
    # Save results to a file
    with open("benchmark_results.json", "w") as f:
        json.dump({"results": results, "summary": {
            "total_runs": len(results),
            "completed": len(completed_runs),
            "failed": len(failed_runs),
            "timeout": len(timeout_runs),
            "avg_duration": round(avg_duration, 2)
        }}, f, indent=2)
    print("\nResults saved to benchmark_results.json")


if __name__ == "__main__":
    run_benchmark()
