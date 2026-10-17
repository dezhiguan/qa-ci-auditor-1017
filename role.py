import re
import json
from typing import Dict, Any, List, Optional


async def execute(data: dict, ctx) -> dict:
    # Extract required identifiers from input text using strict patterns
    text = data.get("text", "")
    
    # Required fields extraction — exact string matches only
    repo_match = re.search(r"仓库\s+([\w\-]+)", text)
    runId_match = re.search(r"CI\s+runId\s+(\d+)", text)
    pr_match = re.search(r"PR\s+#(\d+)", text)
    jobId_match = re.search(r"任务\s+(DF-\d+)", text)
    
    repo = repo_match.group(1) if repo_match else None
    runId = runId_match.group(1) if runId_match else None
    number = pr_match.group(1) if pr_match else None
    jobId = jobId_match.group(1) if jobId_match else None
    
    # Block immediately on any missing identifier
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

    # Call all three tools — at least two required; failure of any → BLOCKED
    log_result = None
    diff_result = None
    job_result = None
    evidence_sources = []

    try:
        log_result = await ctx.tools.call("ci.log.fetch", repo=repo, runId=runId)
        if not isinstance(log_result, dict):
            raise ValueError("ci.log.fetch returned non-dict")
        if log_result.get("logsUnavailable") or not log_result.get("log"):
            raise ValueError("CI log unavailable or empty")
        evidence_sources.append({"tool": "ci.log.fetch", "repo": repo, "runId": runId})
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
        evidence_sources.append({"tool": "git.pr.diff", "repo": repo, "number": number})
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
        evidence_sources.append({"tool": "devflow.job.read", "jobId": jobId})
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "evidenceSources": [],
            "diffSummary": "",
            "blockReason": f"devflow.job.read failed: {str(e)}"
        }

    # Compute diff summary (first ≤200 chars)
    diff_text = diff_result.get("diff", "")
    diff_summary = diff_text[:200] + ("..." if len(diff_text) > 200 else "")

    # Assemble evidence blob for prompt — raw tool returns only
    evidence_blob = {
        "ci_log": log_result.get("log", ""),
        "pr_diff": diff_result.get("diff", ""),
        "job_records": job_result
    }

    # Compile prompt with {{input}} and {{evidence}} — no extra keys
    prompt = await ctx.prompt("role")
    compiled = prompt.compile(input=text, evidence=json.dumps(evidence_blob, ensure_ascii=False))

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

    # Parse *only* the decision field from LLM output's JSON object
    # Do NOT fall back to keyword scanning — explicit PASS/BLOCKED must be structurally present
    decision = "BLOCKED"
    block_reason = "No valid decision field found in LLM output"
    
    try:
        # Find first JSON object
        json_start = llm_output.find('{')
        json_end = llm_output.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            obj_str = llm_output[json_start:json_end]
            parsed = json.loads(obj_str)
            if isinstance(parsed, dict) and "decision" in parsed:
                decision_val = str(parsed["decision"]).strip()
                if decision_val == "PASS":
                    decision = "PASS"
                    block_reason = ""
                elif decision_val == "BLOCKED":
                    decision = "BLOCKED"
                    block_reason = parsed.get("blockReason", "LLM explicitly returned BLOCKED")
                else:
                    block_reason = f"Invalid decision value '{decision_val}' — must be exactly 'PASS' or 'BLOCKED'"
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass

    # Final guarantee: if decision is not PASS, it stays BLOCKED — no keyword fallback
    if decision != "PASS":
        decision = "BLOCKED"
        if not block_reason:
            block_reason = "LLM output did not contain a valid 'decision' field with value 'PASS'"

    return {
        "decision": decision,
        "evidenceSources": evidence_sources,
        "diffSummary": diff_summary,
        "blockReason": block_reason if decision == "BLOCKED" else ""
    }