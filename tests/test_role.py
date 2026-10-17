import pytest
from unittest.mock import AsyncMock, MagicMock
import json
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

    # Simulate prompt compilation with {{input}} and {{evidence}}
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="Compiled prompt")
    ctx.prompt.return_value = mock_prompt

    # Simulate tool returns
    ctx.tools.call.side_effect = [
        {"log": "build success\nall tests passed", "logsUnavailable": False},  # ci.log.fetch
        {"diff": "diff --git a/file.py b/file.py\n+ print('hello')", "number": "1"},  # git.pr.diff
        [{"id": "DF-0006", "status": "DONE", "title": "Fix audit logic"}]  # devflow.job.read
    ]

    # Simulate LLM returning valid PASS JSON
    ctx.llm.chat.return_value = '''{
        "decision": "PASS",
        "notes": "All evidence present and consistent."
    }'''

    # Input with all required identifiers
    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对主分支构建与差异，给出可追溯结论。"
    }

    result = await execute(data, ctx)

    assert isinstance(result, dict)
    assert result["decision"] == "PASS"
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

    assert result["decision"] == "BLOCKED"
    assert "PR number" in result["blockReason"]
    assert result["evidenceSources"] == []
    assert result["diffSummary"] == ""


@pytest.mark.asyncio
async def test_execute_model_blocked_remains_blocked_when_reason_mentions_pass():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.tools.call = AsyncMock()
    ctx.llm.chat = AsyncMock()

    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt.return_value = mock_prompt

    # Simulate all tools succeeding
    ctx.tools.call.side_effect = [
        {"log": "OK", "logsUnavailable": False},
        {"diff": "+x", "number": "1"},
        [{"id": "DF-0006", "status": "DONE"}]
    ]

    # LLM returns BLOCKED decision — even if explanation says "PASS"
    ctx.llm.chat.return_value = '''{
        "decision": "BLOCKED",
        "blockReason": "No independent review completed — PASS requires human sign-off"
    }'''

    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对。"
    }

    result = await execute(data, ctx)

    assert result["decision"] == "BLOCKED"
    assert "independent review" in result["blockReason"]


@pytest.mark.asyncio
async def test_execute_model_returns_invalid_decision_fallbacks_to_blocked():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.tools.call = AsyncMock()
    ctx.llm.chat = AsyncMock()

    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt.return_value = mock_prompt

    ctx.tools.call.side_effect = [
        {"log": "OK", "logsUnavailable": False},
        {"diff": "+x", "number": "1"},
        [{"id": "DF-0006", "status": "DONE"}]
    ]

    # LLM returns malformed JSON without decision field
    ctx.llm.chat.return_value = "The evidence looks fine. I think it's safe."

    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对。"
    }

    result = await execute(data, ctx)

    assert result["decision"] == "BLOCKED"
    assert "no valid decision field" in result["blockReason"].lower()
