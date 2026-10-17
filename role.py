import re
import json
from typing import Dict, Any, List, Optional


async def execute(data: dict, ctx) -> dict:
    # Extract required identifiers from input text using robust patterns
    text = data.get("text", "")
    
    # Required fields extraction with strict matching
    repo_match = re.search(r"仓库\s+([\w\-]+)", text)
    runId_match = re.search(r"CI\s+runId\s+(\d+)", text)
    pr_match = re.search(r"PR\s+#(\d+)", text)
    jobId_match = re.search(r"任务\s+(DF-\d+)", text)
    
    repo = repo_match.group(1) if repo_match else None
    runId = runId_match.group(1) if runId_match else None
    number = pr_match.group(1) if pr_match else None
    jobId = jobId_match.group(1) if jobId_match else None
    
    # Block if any required identifier is missing
    missing = []
    if not repo:
        missing.append("repo")
    if not runId:
        missing.append("runId")
    if not number:
        missing.append("PR number")
    if not jobId:
        missing.append("jobId")
    
    if missing:
        return {
            "decision": "BLOCKED",
            "evidenceSources": [],
            "diffSummary": "",
            "blockReason": f"Missing required identifiers: {', '.join(missing)}"
        }

    # Fetch evidence via tools — must call at least two, all required
    log_result = None
    diff_result = None
    job_result = None
    
    try:
        log_result = await ctx.tools.call("ci.log.fetch", repo=repo, runId=runId)
        if not isinstance(log_result, dict):
            raise ValueError("ci.log.fetch returned non-dict")
        if log_result.get("logsUnavailable") or not log_result.get("log"):
            raise ValueError("CI log unavailable or empty")
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "evidenceSources": [],
            "diffSummary": "",
            "blockReason": f"ci.log.fetch failed: {str(e)}"
        }

    try:
        diff_result = await ctx.tools.call("git.pr.diff", repo=repo, number=number)
        if not isinstance(diff_result, dict) or not diff_result.get("diff"):
            raise ValueError("git.pr.diff returned invalid or empty diff")
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "evidenceSources": [],
            "diffSummary": "",
            "blockReason": f"git.pr.diff failed: {str(e)}"
        }

    try:
        job_result = await ctx.tools.call("devflow.job.read", jobId=jobId)
        if not isinstance(job_result, list):
            raise ValueError("devflow.job.read returned non-list")
        if not job_result:
            raise ValueError("devflow.job.read returned empty list")
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "evidenceSources": [],
            "diffSummary": "",
            "blockReason": f"devflow.job.read failed: {str(e)}"
        }

    # Assemble evidence sources
    evidence_sources = [
        {"tool": "ci.log.fetch", "repo": repo, "runId": runId},
        {"tool": "git.pr.diff", "repo": repo, "number": number},
        {"tool": "devflow.job.read", "jobId": jobId}
    ]

    # Compute minimal diff summary (first 200 chars + ellipsis if longer)
    diff_text = diff_result.get("diff", "")
    diff_summary = diff_text[:200] + ("..." if len(diff_text) > 200 else "")

    # Compile prompt and invoke LLM for structured audit conclusion
    prompt = await ctx.prompt("role")
    compiled = prompt.compile(
        input=text,
        repo=repo,
        runId=runId,
        number=number,
        jobId=jobId,
        log_sample=log_result.get("log", "")[0:300],
        diff_sample=diff_text[0:300],
        job_sample=json.dumps(job_result[0], ensure_ascii=False)[:300] if job_result else ""
    )

    try:
        llm_output = await ctx.llm.chat([
            {"role": "user", "content": compiled}
        ], prompt=prompt)
    except Exception:
        return {
            "decision": "BLOCKED",
            "evidenceSources": evidence_sources,
            "diffSummary": diff_summary,
            "blockReason": "LLM inference failed"
        }

    # Parse decision from LLM output — expect JSON-like substring or explicit PASS/BLOCKED
    # Fallback to BLOCKED on parse failure or unrecognized decision
    decision = "BLOCKED"
    block_reason = "LLM output did not contain valid decision; defaulting to BLOCKED"
    
    # Try to extract decision from JSON object in response
    try:
        # Look for first JSON object in output
        json_start = llm_output.find('{')
        json_end = llm_output.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            obj_str = llm_output[json_start:json_end]
            parsed = json.loads(obj_str)
            if isinstance(parsed, dict) and "decision" in parsed:
                decision = str(parsed["decision"]).strip().upper()
                if decision not in ["PASS", "BLOCKED"]:
                    decision = "BLOCKED"
                    block_reason = f"Invalid decision value '{parsed['decision']}' in LLM output"
                elif decision == "PASS":
                    block_reason = ""
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    # If no JSON, fall back to keyword scan (case-insensitive, whole-word)
    if decision == "BLOCKED":
        if re.search(r"\bPASS\b", llm_output, re.I):
            decision = "PASS"
            block_reason = ""
        elif re.search(r"\bBLOCKED\b", llm_output, re.I):
            decision = "BLOCKED"
            block_reason = "LLM explicitly stated BLOCKED"

    # Final guard: if decision is still BLOCKED and no reason set, use generic
    if decision == "BLOCKED" and not block_reason:
        block_reason = "No valid PASS signal found in LLM output"

    return {
        "decision": decision,
        "evidenceSources": evidence_sources,
        "diffSummary": diff_summary,
        "blockReason": block_reason if decision == "BLOCKED" else ""
    }