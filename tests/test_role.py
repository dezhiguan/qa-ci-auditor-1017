import pytest
from unittest.mock import AsyncMock, MagicMock
import re

# Import the execute function
from role import execute


@pytest.mark.asyncio
async def test_execute_normal_evidence_flow():
    # Mock context
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.tools.call = AsyncMock()
    ctx.llm.chat = AsyncMock()

    # Simulate prompt compilation
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="Compiled prompt with evidence")
    ctx.prompt.return_value = mock_prompt

    # Simulate tool returns
    ctx.tools.call.side_effect = [
        {"log": "build success\nall tests passed", "logsUnavailable": False},  # ci.log.fetch
        {"diff": "diff --git a/file.py b/file.py\n+ print('hello')", "number": "1"},  # git.pr.diff
        [{"id": "DF-0006", "status": "DONE", "title": "Fix audit logic"}]  # devflow.job.read
    ]

    # Simulate LLM returning PASS in JSON
    ctx.llm.chat.return_value = '''{
        "decision": "PASS",
        "notes": "All evidence present and consistent."
    }'''

    # Input with all required identifiers
    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对主分支构建与差异，给出可追溯结论。"
    }

    result = await execute(data, ctx)

    # Assert structure and decision
    assert isinstance(result, dict)
    assert result["decision"] == "PASS"
    assert "evidenceSources" in result
    assert len(result["evidenceSources"]) == 3
    assert "diffSummary" in result
    assert isinstance(result["diffSummary"], str)
    assert result["blockReason"] == ""


@pytest.mark.asyncio
async def test_execute_missing_pr_number_blocks():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.tools.call = AsyncMock()
    ctx.llm.chat = AsyncMock()

    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt.return_value = mock_prompt

    # Input missing PR #
    data = {
        "text": "仓库 qa-ci-evidence-1017 的 CI runId 39009850970、任务 DF-0006 请核验。"
    }

    result = await execute(data, ctx)

    # Must block and report missing PR number
    assert result["decision"] == "BLOCKED"
    assert "PR number" in result["blockReason"]
    assert "evidenceSources" in result
    assert len(result["evidenceSources"]) == 0
    assert result["diffSummary"] == ""


@pytest.mark.asyncio
async def test_execute_tool_failure_blocks():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.tools.call = AsyncMock()
    ctx.llm.chat = AsyncMock()

    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt.return_value = mock_prompt

    # First tool fails
    ctx.tools.call.side_effect = Exception("network timeout")

    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对。"
    }

    result = await execute(data, ctx)

    assert result["decision"] == "BLOCKED"
    assert "ci.log.fetch failed" in result["blockReason"]
    assert result["evidenceSources"] == []
